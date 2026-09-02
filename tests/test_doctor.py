"""act/doctor.py — post-install diagnostics, with injected probes.

The doctor must (a) never raise, (b) exit with the number of FAILs,
(c) label every check ok/warn/fail with a one-line fix for non-ok states.
All machine access goes through doctor.Probes; these tests inject fakes for
every probe and build the on-disk fixtures inside the sandbox
AIASSISTANT_HOME (tests/__init__.py) — nothing outside it is ever touched.
"""
import contextlib
import datetime as _dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME

from act import doctor
from act.lib import config, secrets

NOW = 1_700_000_000.0

# The launchd/cron/screenpipe checks model a macOS install and lean on POSIX
# file modes + executable shell shims; skip them on Windows (the systemd branch
# has its own Windows-safe suite below). Not darwin-only in spirit — Linux runs
# them fine — so key on "not Windows".
_WIN = sys.platform.startswith("win")

LABELS = ["com.zelin.aiassistant.actd", "com.zelin.aiassistant.radar"]

HEALTHY_LAUNCHCTL = (
    "4242\t0\tcom.zelin.aiassistant.actd\n"
    "-\t0\tcom.zelin.aiassistant.radar\n"
    "77\t0\tcom.apple.unrelated\n"
)

HEALTHY_CRON = (
    "*/30 * * * * cd /repo && ./ingest/screenpipe-export.sh && "
    "python3 -m act.radar --once\n"
    # §17 D19: daily fire, NO --now — the module gates itself on
    # digest.frequency. The pre-D19 Monday `--now` form is LEGACY_DIGEST_CRON.
    "7 9 * * * cd /repo && python3 -m act.digest >> /repo/state/digest.log 2>&1\n"
)
# What every install before D19 left in the crontab: --now bypasses the
# cadence gate, so this line forces a card every Monday past frequency=off.
LEGACY_DIGEST_CRON = (
    "*/30 * * * * cd /repo && ./ingest/screenpipe-export.sh && "
    "python3 -m act.radar --once\n"
    "7 9 * * 1 cd /repo && python3 -m act.digest --now\n"
)


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class FakeRun:
    """Injectable probes.run — records calls, answers from a canned table."""

    def __init__(self, table=None):
        self.calls = []
        self.table = table or {}

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append({"cmd": list(cmd), "env": env})
        prog = os.path.basename(cmd[0])
        if cmd[0] == sys.executable:
            return self.table.get("python", (0, "3.11"))
        if prog == "claude" and "--version" in cmd:
            return self.table.get("claude --version", (0, "2.0.14 (Claude Code)"))
        if prog == "claude":
            return self.table.get("claude -p", (0, "ok"))
        if prog == "gh":
            return self.table.get("gh auth", (0, "Logged in to github.com"))
        return (0, "")

    def commands(self):
        return [" ".join(c["cmd"][:2]) for c in self.calls]


def by_name(results, name):
    matches = [r for r in results if r.name == name]
    assert matches, "no check named %r in %s" % (name, [r.name for r in results])
    return matches[0]


@unittest.skipIf(_WIN, "macOS/POSIX install checks (launchd/cron/modes)")
class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        # This suite exercises the macOS launchd/cron/screenpipe checks; pin
        # darwin so it validates that branch identically on the macOS and
        # ubuntu/windows CI runners. The systemd branch has its own suite below.
        p = mock.patch("sys.platform", "darwin")
        p.start()
        self.addCleanup(p.stop)

        self.home = Path(TMP_HOME)
        self._created = []

        # another test's leftover overrides would leak into load_config()
        self._stashed_overrides = None
        if config.SETTINGS_OVERRIDES_PATH.exists():
            self._stashed_overrides = config.SETTINGS_OVERRIDES_PATH.read_text(
                encoding="utf-8")
            config.SETTINGS_OVERRIDES_PATH.unlink()

        config.ensure_state_dirs()
        self._touch(self.home / "install.sh", "#!/bin/bash\n")

        vault = self.home / "vault"
        self.raw_dir = vault / "2 - raw"
        self.unprocessed_dir = vault / "1 - unprocessed"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.unprocessed_dir.mkdir(parents=True, exist_ok=True)
        self._touch(
            config.CONFIG_PATH,
            "sources:\n  obsidian_raw: %s\n" % json.dumps(str(self.raw_dir)))

        self._touch(self.home / "config" / "runtime.json",
                    json.dumps({"python": sys.executable}))

        self.dashboard = config.DASHBOARD_PATH
        self._write_dashboard(NOW - 5)

        self.key_file = secrets.write_secret(
            secrets.ANTHROPIC_API_KEY_FILE, "sk-ant-test-123")
        self._created.append(self.key_file)

        self.db_file = self.home / "fake-screenpipe.sqlite"
        self._touch(self.db_file, "not-a-real-db")
        os.utime(self.db_file, (NOW - 300, NOW - 300))

        self.missing_legacy = self.home / "no-such-legacy-key.txt"

        # §55 第三幕 probe cwd: the default target repo must exist for the row
        self.target_repo = self.home / "fake-workbench"
        self.target_repo.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for p in self._created:
            if p.exists():
                p.unlink()
        if self._stashed_overrides is not None:
            config.SETTINGS_OVERRIDES_PATH.write_text(
                self._stashed_overrides, encoding="utf-8")

    def _touch(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._created.append(path)

    def _write_dashboard(self, generated_ts: float):
        self._touch(self.dashboard,
                    json.dumps({"generated_at": _iso(generated_ts)}))

    def make_probes(self, run=None, launchctl=None, cron=None, which_map=None,
                    now=None, db=None, legacy=None, installed_plists=None,
                    launchd_logs=None, agent_files=None, heartbeat=None,
                    pid_alive=None, launchd_claude=None, claude_code=None):
        if which_map is None:
            which_map = {"claude": "/fake/bin/claude",
                         "npx": "/fake/bin/npx",
                         "gh": "/fake/bin/gh"}
        return doctor.Probes(
            which=which_map.get,
            run=run if run is not None else FakeRun(),
            launchctl_list=lambda: (
                HEALTHY_LAUNCHCTL if launchctl is None else launchctl),
            crontab=lambda: HEALTHY_CRON if cron is None else cron,
            now=now or (lambda: NOW),
            launchd_labels=LABELS,
            screenpipe_db=db or self.db_file,
            legacy_key_path=legacy or self.missing_legacy,
            # hermetic: never read the REAL ~/Library/LaunchAgents plists, the
            # REAL ~/Library/Logs agent logs, or the real login shell from the
            # sandboxed suite. The log seam matters as much as the others: a
            # developer whose own machine still carries a crashed agent's
            # "No module named 'act'" log would otherwise flip the §55 log
            # attribution branch under every unrelated test.
            daemon_path_env=lambda: None,
            login_shell_claude=lambda: None,
            installed_plist_text=lambda label: (installed_plists or {}).get(label),
            launchd_log_tail=lambda short: (launchd_logs or {}).get(short, ""),
            # §55 orphan scan reads the REAL ~/Library/LaunchAgents by default;
            # §47.4 heartbeat/pid probes likewise — all injected here.
            installed_agent_labels=lambda: list(agent_files or []),
            heartbeat_read=lambda: heartbeat,
            pid_alive=pid_alive or (lambda pid: True),
            # §55 第三幕：the real probe bootstraps a launchd job — never from
            # the suite. Default = "claude reads the folder" so the healthy
            # baseline stays healthy.
            launchd_claude_probe=(lambda claude_bin, cwd: dict(launchd_claude))
            if launchd_claude is not None
            else (lambda claude_bin, cwd: {"state": "ok", "rc": 0, "text": "2.1.252"}),
            # §59: never read the developer's real ~/.claude/settings.json;
            # default = "unset" so the healthy baseline stays healthy.
            claude_code_settings=lambda: dict(
                claude_code if claude_code is not None
                else {"model": None, "exists": False, "parseable": False}),
            # §56.4: never read the developer's real HOME mirror or the real
            # autodeploy.launchd.log mtime (tests/test_doctor_launchd_volume_access.py
            # injects both); default = no mirror, no log.
            deploy_mirror_read=lambda: None,
            launchd_log_mtime=lambda short: None,
        )

    def _main(self, probes, argv=None):
        """Run doctor.main with stdout captured; returns (exit_code, output)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = doctor.main(argv or [], probes=probes)
        return code, buf.getvalue()

    # -- healthy baseline ---------------------------------------------------- #
    def test_healthy_setup_has_no_fails_and_exits_zero(self):
        probes = self.make_probes()
        results = doctor.run_checks(probes)
        fails = [r for r in results if r.status == doctor.FAIL]
        self.assertEqual(fails, [], "unexpected FAILs: %s" % fails)
        for name in ("AIASSISTANT_HOME", "claude CLI", "daemon python",
                     "config.yaml", "anthropic key", "state dirs", "actd",
                     "radar", "cron ingest chain", "cron digest", "dashboard",
                     "obsidian vault", "screenpipe db", "node/npx", "gh CLI",
                     "claude auth"):
            self.assertEqual(by_name(results, name).status, doctor.OK, name)
        code, out = self._main(self.make_probes())
        self.assertEqual(code, 0)
        self.assertIn("[ ok ] actd: running (pid 4242)", out)

    # -- key resolution (CONTRACT §19) --------------------------------------- #
    def test_missing_key_is_subscription_mode_warn(self):
        self.key_file.unlink()
        results = doctor.run_checks(self.make_probes(), fast=True)
        r = by_name(results, "anthropic key")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("subscription-auth", r.detail)
        self.assertIn("Settings", r.fix)

    def test_legacy_key_path_still_resolves(self):
        self.key_file.unlink()
        legacy = self.home / "legacy-key.txt"
        self._touch(legacy, "sk-ant-legacy\n")
        results = doctor.run_checks(self.make_probes(legacy=legacy), fast=True)
        r = by_name(results, "anthropic key")
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("legacy", r.detail)

    def test_world_readable_key_file_warns_chmod(self):
        os.chmod(self.key_file, 0o644)
        results = doctor.run_checks(self.make_probes(), fast=True)
        r = by_name(results, "anthropic key")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("chmod 600", r.fix)

    # -- live auth probe ------------------------------------------------------ #
    def test_live_probe_passes_resolved_key_in_env(self):
        run = FakeRun()
        doctor.run_checks(self.make_probes(run=run))
        probe_calls = [c for c in run.calls if "-p" in c["cmd"]]
        self.assertEqual(len(probe_calls), 1)
        self.assertEqual(probe_calls[0]["env"]["ANTHROPIC_API_KEY"],
                         "sk-ant-test-123")

    def test_live_probe_failure_is_fail_with_fix(self):
        run = FakeRun(table={"claude -p": (1, "Invalid API key")})
        results = doctor.run_checks(self.make_probes(run=run))
        r = by_name(results, "claude auth")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("Invalid API key", r.detail)
        self.assertTrue(r.fix)

    def test_fast_skips_live_probe(self):
        run = FakeRun()
        results = doctor.run_checks(self.make_probes(run=run), fast=True)
        self.assertNotIn("claude auth", [r.name for r in results])
        self.assertFalse([c for c in run.calls if "-p" in c["cmd"]])

    # -- launchd agents -------------------------------------------------------- #
    def test_crashing_actd_is_fail_with_log_pointer(self):
        out = "-\t78\tcom.zelin.aiassistant.actd\n-\t0\tcom.zelin.aiassistant.radar\n"
        results = doctor.run_checks(self.make_probes(launchctl=out), fast=True)
        r = by_name(results, "actd")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("78", r.detail)
        self.assertIn("actd.launchd.log", r.fix)

    def test_unregistered_actd_fails_but_radar_only_warns(self):
        results = doctor.run_checks(self.make_probes(launchctl=""), fast=True)
        self.assertEqual(by_name(results, "actd").status, doctor.FAIL)
        self.assertEqual(by_name(results, "radar").status, doctor.WARN)

    # -- §55 常驻（KeepAlive）agent 的 crash-loop 是 FAIL，不只 actd ---------- #
    # PR #124 审查 B3：syncd（live mode=cloud 时 = 手机/web 看板）import 即死
    # 只得 WARN，而 §56 的回滚只数 FAIL 行——新版本把 syncd 弄坏就永远
    # crash-loop 下去、deploy_state 却写 deployed。

    RESIDENT_LABELS = ["com.zelin.aiassistant.actd", "com.zelin.aiassistant.syncd",
                       "com.zelin.aiassistant.gmailradar",
                       "com.zelin.aiassistant.weeklydigest"]

    def _resident_probes(self, launchctl):
        probes = self.make_probes(launchctl=launchctl)
        probes.launchd_labels = list(self.RESIDENT_LABELS)
        return probes

    def test_crash_looping_syncd_is_fail_like_actd(self):
        out = ("4242\t0\tcom.zelin.aiassistant.actd\n"
               "-\t1\tcom.zelin.aiassistant.syncd\n"
               "-\t0\tcom.zelin.aiassistant.gmailradar\n"
               "-\t0\tcom.zelin.aiassistant.weeklydigest\n")
        results = doctor.run_checks(self._resident_probes(out), fast=True)
        r = by_name(results, "syncd")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("crash loop", r.detail)
        self.assertIn("syncd.launchd.log", r.fix)
        self.assertEqual(r.failure_id, "agent_unloaded")
        self.assertEqual(by_name(results, "actd").status, doctor.OK)

    def test_periodic_agent_exiting_non_zero_once_only_warns(self):
        # RunAtLoad radar / weeklydigest：一次网络抖动就是一个非 0 退出码，
        # 不是 crash loop —— WARN，否则 §56 会为此回滚一次部署。
        out = ("4242\t0\tcom.zelin.aiassistant.actd\n"
               "-\t0\tcom.zelin.aiassistant.syncd\n"
               "-\t1\tcom.zelin.aiassistant.gmailradar\n"
               "-\t1\tcom.zelin.aiassistant.weeklydigest\n")
        results = doctor.run_checks(self._resident_probes(out), fast=True)
        for short in ("gmailradar", "weeklydigest"):
            r = by_name(results, short)
            self.assertEqual(r.status, doctor.WARN, short)
            self.assertNotIn("crash loop", r.detail)
        # syncd with sync OFF exits 0 every throttle cycle: that is healthy
        self.assertEqual(by_name(results, "syncd").status, doctor.OK)

    def test_unregistered_syncd_still_only_warns(self):
        # 「没注册」不是 crash loop：只有 actd 缺席是 FAIL（卡不会动）
        out = "4242\t0\tcom.zelin.aiassistant.actd\n"
        results = doctor.run_checks(self._resident_probes(out), fast=True)
        self.assertEqual(by_name(results, "syncd").status, doctor.WARN)

    def test_resident_labels_mirror_the_keepalive_templates(self):
        # 单源纪律（防腐 #9）：doctor 的常驻集合必须和 act/launchd/*.plist 里
        # KeepAlive=true 的模板逐字一致——加/删一个 KeepAlive agent 就得改两处。
        import re
        repo = Path(__file__).resolve().parents[1]
        keepalive = set()
        for plist in (repo / "act" / "launchd").glob("*.plist"):
            text = plist.read_text(encoding="utf-8")
            if re.search(r"<key>KeepAlive</key>\s*<true\s*/>", text):
                keepalive.add(plist.stem)
        self.assertTrue(keepalive, "no KeepAlive templates found — layout changed?")
        self.assertEqual(set(doctor.RESIDENT_LABELS), keepalive)

    # -- §55 迁移探测: pre-v0.48 plists still pointing at the repo ------------- #
    # 2026-08-31 双次宕机根因: 已安装 plist 的 spawn 前路径键指着 repo，repo
    # 在外置卷上时 launchd 以 EX_CONFIG(78) 拒绝 spawn；「一键修复」只重渲染
    # actd，所以 doctor 必须点名每一个 stale agent 并指向 install.sh。

    def _stale_plist(self):
        repo = str(self.home)
        return ("<plist><dict>\n"
                "<key>StandardOutPath</key>\n"
                "<string>%s/state/actd.launchd.log</string>\n"
                "<key>WorkingDirectory</key>\n<string>%s</string>\n"
                "</dict></plist>" % (repo, repo))

    def _fresh_plist(self):
        return ("<plist><dict>\n"
                "<key>StandardOutPath</key>\n"
                "<string>/Users/u/Library/Logs/zelin-ai-assistant/actd.launchd.log</string>\n"
                "<key>WorkingDirectory</key>\n<string>/Users/u</string>\n"
                "</dict></plist>")

    def test_stale_plist_names_agent_and_points_at_install_sh(self):
        probes = self.make_probes(installed_plists={
            LABELS[0]: self._stale_plist(),
            LABELS[1]: self._fresh_plist(),
        })
        # repo (TMP_HOME) NOT under home → the external-volume case → FAIL
        with mock.patch.object(doctor.Path, "home",
                               return_value=Path("/nonexistent-home")):
            results = doctor.run_checks(probes, fast=True)
        r = by_name(results, "launchd paths")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("actd", r.detail)
        self.assertNotIn("radar", r.detail)  # fresh agent is NOT named
        self.assertIn("external volume", r.detail)
        self.assertIn("install.sh", r.fix)
        self.assertIn("only re-renders actd", r.fix)

    def test_stale_plist_with_repo_under_home_only_warns(self):
        probes = self.make_probes(
            installed_plists={LABELS[0]: self._stale_plist()})
        # repo under home → agents still spawn → degraded-but-working WARN
        with mock.patch.object(doctor.Path, "home",
                               return_value=self.home.parent):
            results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd paths").status, doctor.WARN)

    def test_fresh_plists_ok_and_nothing_installed_is_silent(self):
        probes = self.make_probes(
            installed_plists={LABELS[0]: self._fresh_plist()})
        results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd paths").status, doctor.OK)
        self.assertNotIn("launchd python", [r.name for r in results])
        # nothing installed at all → no "launchd paths" row (unregistered is
        # already _check_launchd's finding; an OK here would be a lie)
        names = [r.name for r in doctor.run_checks(self.make_probes(), fast=True)]
        self.assertNotIn("launchd paths", names)

    # -- §55 symlink 形状 + 解释器验证（2026-08-31 live 部署的两个症状）------- #
    # owner 的 repo 实体在 /Volumes/… 上，另有 ~/Projects -> /Volumes/… 的便利
    # symlink。渲染进 plist 的是 symlink 形状 → launchd 进程被 TCC 拒绝 →
    # `No module named 'act'`；同一轮又挑中一个没有 PyYAML 的 python3。

    def _symlinked_repo_path(self):
        """沙箱里真造一条 symlink（不能靠 /var -> /private/var：Linux CI 上
        tmp 不是 symlink，测试会静默失效）。"""
        real = self.home / "physical" / "repo"
        real.mkdir(parents=True, exist_ok=True)
        link = self.home / "shortcut"
        if not link.exists():
            link.symlink_to(self.home / "physical")
        linked = str(link / "repo")
        self.assertNotEqual(os.path.realpath(linked), linked)  # fixture sanity
        return linked

    def _shim(self, name, exit_code):
        """沙箱里的假解释器（真 python 一个都不起）——只为过 os.access。"""
        p = self.home / name
        self._touch(p, "#!/bin/sh\nexit %d\n" % exit_code)
        p.chmod(0o755)
        self._created.append(p)
        return str(p)

    def _plist_with(self, repo_path=None, interpreter=None):
        """spawn 前的键一律指 /Users/u（干净），只让待测的值变脏。"""
        interpreter = interpreter or self._shim("good-python3", 0)
        return (
            "<plist><dict>\n"
            "<key>ProgramArguments</key>\n<array>\n<string>%s</string>\n"
            "<string>-m</string>\n<string>act.actd</string>\n</array>\n"
            "<key>WorkingDirectory</key>\n<string>/Users/u</string>\n"
            "<key>StandardOutPath</key>\n"
            "<string>/Users/u/Library/Logs/zelin-ai-assistant/actd.launchd.log</string>\n"
            "<key>EnvironmentVariables</key>\n<dict>\n"
            "<key>AIASSISTANT_HOME</key>\n<string>%s</string>\n"
            "<key>PYTHONPATH</key>\n<string>%s</string>\n"
            "</dict>\n</dict></plist>"
            % (interpreter,
               repo_path or "/Users/u/Projects/zelin-ai-assistant",
               repo_path or "/Users/u/Projects/zelin-ai-assistant"))

    def test_symlinked_repo_path_in_plist_is_flagged(self):
        linked = self._symlinked_repo_path()
        probes = self.make_probes(installed_plists={
            LABELS[0]: self._plist_with(repo_path=linked),
            LABELS[1]: self._plist_with(),
        })
        with mock.patch.object(doctor.Path, "home",
                               return_value=Path("/nonexistent-home")):
            results = doctor.run_checks(probes, fast=True)
        r = by_name(results, "launchd paths")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("symlinked", r.detail)
        self.assertIn("actd", r.detail)
        self.assertNotIn("radar", r.detail)  # physical-path agent is not named
        self.assertIn("No module named 'act'", r.detail)
        self.assertIn("install.sh", r.fix)

    def test_symlinked_repo_path_only_warns_when_the_repo_is_under_home(self):
        probes = self.make_probes(installed_plists={
            LABELS[0]: self._plist_with(repo_path=self._symlinked_repo_path())})
        with mock.patch.object(doctor.Path, "home",
                               return_value=self.home.parent):
            results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd paths").status, doctor.WARN)

    def _no_yaml_interpreter(self):
        """可执行的假解释器 + 一个让 `import yaml` 探针失败的 run。"""
        py = self._shim("no-yaml-python3", 1)
        base = FakeRun()

        def run(cmd, env=None, timeout=None):
            if cmd[0] == py:
                return (1, "ModuleNotFoundError: No module named 'yaml'")
            return base(cmd, env=env, timeout=timeout)

        return py, run

    def test_plist_interpreter_without_pyyaml_is_flagged(self):
        py, run = self._no_yaml_interpreter()
        probes = self.make_probes(run=run, installed_plists={
            LABELS[0]: self._plist_with(interpreter=py),
            LABELS[1]: self._plist_with(),
        })
        results = doctor.run_checks(probes, fast=True)
        # paths themselves are clean — only the interpreter is broken
        self.assertEqual(by_name(results, "launchd paths").status, doctor.OK)
        r = by_name(results, "launchd python")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("import yaml", r.detail)
        self.assertIn("actd", r.detail)
        self.assertNotIn("radar", r.detail)
        self.assertIn(py, r.detail)
        self.assertIn("install.sh", r.fix)

    def test_missing_plist_interpreter_is_flagged_without_probing(self):
        probes = self.make_probes(installed_plists={
            LABELS[0]: self._plist_with(interpreter="/nonexistent/python3")})
        results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd python").status, doctor.FAIL)

    # -- §55 症状 4: 路径全对、yaml 也过，解释器却读不到 repo ------------------ #
    # 2026-08-31 live 部署的最后一幕（v0.48.2 修好路径之后才露出来）：
    # /opt/homebrew/bin/python3 装着 PyYAML、PYTHONPATH 也渲染正确，但 macOS
    # 按 binary 授文件权限，它在 launchd 下读不了外置卷上的 repo，于是 agent
    # 一直 `No module named 'act'` 退出 1。install.sh 当时把它误报成
    # 「PyYAML missing」——两条 ModuleNotFoundError 必须从日志里分开。

    CRASHED = "-\t1\tcom.zelin.aiassistant.actd\n-\t0\tcom.zelin.aiassistant.radar\n"

    def test_missing_act_in_the_log_blames_the_interpreter_not_pyyaml(self):
        probes = self.make_probes(
            launchctl=self.CRASHED,
            launchd_logs={"actd": "Traceback...\nModuleNotFoundError: "
                                  "No module named 'act'\n"})
        r = by_name(doctor.run_checks(probes, fast=True), "actd")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("cannot see the repo", r.detail)
        self.assertIn("PyYAML is NOT the problem", r.detail)
        self.assertNotIn("pip install", r.fix)
        self.assertIn("install.sh", r.fix)
        self.assertEqual(r.failure_id, "interpreter_blind")

    def test_missing_yaml_in_the_log_still_blames_pyyaml(self):
        probes = self.make_probes(
            launchctl=self.CRASHED,
            launchd_logs={"actd": "ModuleNotFoundError: No module named 'yaml'\n"})
        r = by_name(doctor.run_checks(probes, fast=True), "actd")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("PyYAML is missing", r.detail)
        self.assertIn("pip install", r.fix)
        self.assertIn("pyyaml", r.fix)
        self.assertEqual(r.failure_id, "agent_unloaded")

    def test_an_unreadable_log_keeps_the_old_both_causes_pointer(self):
        r = by_name(doctor.run_checks(
            self.make_probes(launchctl=self.CRASHED), fast=True), "actd")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("actd.launchd.log", r.fix)
        # 读不到日志时不许偏袒任何一边——两个原因都摆出来
        self.assertIn("No module named 'act'", r.fix)
        self.assertIn("No module named 'yaml'", r.fix)

    def test_the_latest_log_line_wins(self):
        # KeepAlive 把历次失败都留在同一个文件里：最新那条才是当前状态
        probes = self.make_probes(
            launchctl=self.CRASHED,
            launchd_logs={"actd": "No module named 'yaml'\n"
                                  "No module named 'act'\n"})
        r = by_name(doctor.run_checks(probes, fast=True), "actd")
        self.assertIn("cannot see the repo", r.detail)

    def test_yaml_capable_interpreter_blind_to_the_repo_gets_its_own_row(self):
        # 路径干净 + import yaml 通过 + 日志说没有 act = 症状 4，独立一行、
        # 独立修法（换解释器 / 授 FDA），不是「重装 agent」。
        probes = self.make_probes(
            launchctl=self.CRASHED,
            installed_plists={LABELS[0]: self._plist_with(),
                              LABELS[1]: self._plist_with()},
            launchd_logs={"actd": "ModuleNotFoundError: No module named 'act'\n"})
        results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd paths").status, doctor.OK)
        r = by_name(results, "launchd python")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("imports yaml", r.detail)
        self.assertIn("cannot READ", r.detail)
        self.assertIn("per binary", r.detail)
        self.assertIn("actd", r.detail)
        self.assertNotIn("radar", r.detail)  # 只点名真在崩的那个
        self.assertIn("install.sh", r.fix)
        self.assertIn("Full Disk Access", r.fix)
        self.assertEqual(r.failure_id, "interpreter_blind")

    def test_a_healthy_agent_never_gets_the_blind_interpreter_row(self):
        # 日志里有旧的 'act' 残留，但 agent 现在跑得好好的 → 不报
        probes = self.make_probes(
            installed_plists={LABELS[0]: self._plist_with()},
            launchd_logs={"actd": "No module named 'act'\n"})
        results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd paths").status, doctor.OK)
        self.assertNotIn("launchd python", [r.name for r in results])

    def test_broken_paths_suppress_the_blind_interpreter_row(self):
        # 路径本身坏时重渲染就一起修了；多报一行只会把人支去授一个其实
        # 不需要的 FDA。
        probes = self.make_probes(
            launchctl=self.CRASHED,
            installed_plists={LABELS[0]: self._stale_plist()},
            launchd_logs={"actd": "No module named 'act'\n"})
        with mock.patch.object(doctor.Path, "home",
                               return_value=Path("/nonexistent-home")):
            results = doctor.run_checks(probes, fast=True)
        self.assertEqual(by_name(results, "launchd paths").status, doctor.FAIL)
        self.assertNotIn("launchd python", [r.name for r in results])

    # -- dashboard freshness ----------------------------------------------------- #
    def test_stale_dashboard_is_fail(self):
        self._write_dashboard(NOW - 1200)  # 20 min old
        results = doctor.run_checks(self.make_probes(), fast=True)
        r = by_name(results, "dashboard")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("stale", r.detail)
        self.assertIn("20 min", r.detail)

    def test_missing_dashboard_is_fail(self):
        self.dashboard.unlink()
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertEqual(by_name(results, "dashboard").status, doctor.FAIL)

    # -- §47.4 heartbeat stall watchdog ------------------------------------------ #
    def _hb(self, age_s, phase="idle", pid=4242, interval=10):
        return {"age_s": age_s, "phase": phase, "pid": pid, "interval": interval,
                "stale_after_s": 90, "ts": _iso(NOW - age_s)}

    def test_fresh_heartbeat_is_ok_and_names_the_phase(self):
        results = doctor.run_checks(self.make_probes(heartbeat=self._hb(4)), fast=True)
        r = by_name(results, "actd heartbeat")
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("phase=idle", r.detail)
        self.assertIn("pid 4242", r.detail)

    def test_alive_but_stale_heartbeat_is_the_stall_fail_with_kickstart(self):
        # 2026-08-31 22:31 shape: launchctl shows a pid, the loop stopped 150 min ago
        hb = self._hb(150 * 60, phase="reconcile")
        results = doctor.run_checks(self.make_probes(heartbeat=hb), fast=True)
        r = by_name(results, "actd heartbeat")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertEqual(r.failure_id, "actd_stalled")
        self.assertEqual(r.action_id, "restart_actd")
        self.assertIn("alive (pid 4242)", r.detail)
        self.assertIn("150 min", r.detail)
        self.assertIn("phase 'reconcile'", r.detail)
        self.assertIn("launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd",
                      r.fix)

    def test_stale_heartbeat_with_dead_actd_does_not_double_fail(self):
        dead = HEALTHY_LAUNCHCTL.replace("4242\t0", "-\t1")
        results = doctor.run_checks(
            self.make_probes(launchctl=dead, heartbeat=self._hb(600)), fast=True)
        r = by_name(results, "actd heartbeat")
        self.assertEqual(r.status, doctor.WARN)   # the actd row carries the FAIL
        self.assertEqual(r.failure_id, "")
        self.assertEqual(by_name(results, "actd").status, doctor.FAIL)

    def test_alive_without_any_heartbeat_file_warns_about_old_daemon(self):
        results = doctor.run_checks(self.make_probes(heartbeat=None), fast=True)
        r = by_name(results, "actd heartbeat")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("never written", r.detail)
        self.assertIn("kickstart", r.fix)

    def test_no_heartbeat_and_no_actd_says_nothing_extra(self):
        dead = HEALTHY_LAUNCHCTL.replace("4242\t0", "-\t1")
        results = doctor.run_checks(self.make_probes(launchctl=dead, heartbeat=None),
                                    fast=True)
        self.assertNotIn("actd heartbeat", [r.name for r in results])

    def test_stale_threshold_is_the_writers_not_the_readers(self):
        # a 60 s interval daemon declares stale_after_s=180: 120 s old is fine
        hb = self._hb(120, interval=60)
        hb["stale_after_s"] = 180
        results = doctor.run_checks(self.make_probes(heartbeat=hb), fast=True)
        self.assertEqual(by_name(results, "actd heartbeat").status, doctor.OK)

    # -- §55 fd soft limit on the INSTALLED actd plist --------------------------- #
    _SOFT = ("<key>SoftResourceLimits</key><dict><key>NumberOfFiles</key>"
             "<integer>8192</integer></dict>")
    _HARD = ("<key>HardResourceLimits</key><dict><key>NumberOfFiles</key>"
             "<integer>8192</integer></dict>")

    def test_installed_plist_with_soft_limit_only_is_ok(self):
        plists = {"com.zelin.aiassistant.actd": "<plist>" + self._SOFT + "</plist>"}
        results = doctor.run_checks(self.make_probes(installed_plists=plists), fast=True)
        r = by_name(results, "launchd fd limit")
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("8192", r.detail)
        self.assertIn("unlimited", r.detail)

    def test_installed_plist_without_soft_limit_warns_fd_limit(self):
        plists = {"com.zelin.aiassistant.actd": "<plist><key>Label</key></plist>"}
        results = doctor.run_checks(self.make_probes(installed_plists=plists), fast=True)
        r = by_name(results, "launchd fd limit")
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "fd_limit")
        self.assertIn("256", r.detail)
        self.assertIn("install.sh", r.fix)
        # the row no longer blames dispatch failures on the fd limit
        self.assertNotIn("claude", r.detail)

    def test_hard_limit_present_warns_because_it_lowers_the_ceiling(self):
        # the 2026-08-31 hotfix shape (Soft+Hard 8192): verified 2026-09-01 to
        # turn launchd's [256, unlimited] into [8192, 8192]
        plists = {"com.zelin.aiassistant.actd": "<plist>" + self._SOFT + self._HARD + "</plist>"}
        results = doctor.run_checks(self.make_probes(installed_plists=plists), fast=True)
        r = by_name(results, "launchd fd limit")
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "fd_limit")
        self.assertIn("LOWERS", r.detail)
        self.assertIn("install.sh", r.fix)

    def test_too_low_soft_limit_warns(self):
        low = self._SOFT.replace("8192", "512")
        plists = {"com.zelin.aiassistant.actd": "<plist>" + low + "</plist>"}
        results = doctor.run_checks(self.make_probes(installed_plists=plists), fast=True)
        self.assertEqual(by_name(results, "launchd fd limit").status, doctor.WARN)

    def test_number_of_files_need_not_be_the_first_key_in_the_dict(self):
        text = ("<plist><key>SoftResourceLimits</key><dict><key>NumberOfProcesses</key>"
                "<integer>512</integer><key>NumberOfFiles</key><integer>8192</integer>"
                "</dict></plist>")
        self.assertEqual(doctor._plist_number_of_files(text, "SoftResourceLimits"), 8192)
        self.assertIsNone(doctor._plist_number_of_files(text, "HardResourceLimits"))

    def test_no_installed_actd_plist_skips_the_fd_row(self):
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertNotIn("launchd fd limit", [r.name for r in results])

    # -- §55 第三幕: launchd-spawned claude vs the task folder (TCC) ------------- #
    _ACTD_PLIST = {"com.zelin.aiassistant.actd": "<plist>" + _SOFT + "</plist>"}
    _BUN_GUESS = ("error: An unknown error occurred, possibly due to low max file "
                  "descriptors (Unexpected)\n\nCurrent limit: 8192\n")

    def _claude_row(self, probe):
        with mock.patch.object(doctor.config, "load_config") as lc:
            cfg = doctor.config.Config()
            cfg.default_target_repo = str(self.target_repo)
            lc.return_value = cfg
            results = doctor.run_checks(
                self.make_probes(installed_plists=self._ACTD_PLIST, launchd_claude=probe),
                fast=True)
        return by_name(results, "launchd claude")

    def test_launchd_claude_reading_the_folder_is_ok(self):
        r = self._claude_row({"state": "ok", "rc": 0, "text": "2.1.252 (Claude Code)"})
        self.assertEqual(r.status, doctor.OK)
        self.assertIn(str(self.target_repo.resolve()), r.detail)

    def test_bun_guess_under_launchd_is_fail_claude_blind_with_fda_fix(self):
        # the live 2026-08-31 failure, reproduced 2026-09-01 by exactly this probe
        r = self._claude_row({"state": "failed", "rc": 1, "text": self._BUN_GUESS})
        self.assertEqual(r.status, doctor.FAIL)
        self.assertEqual(r.failure_id, "claude_blind")
        self.assertEqual(r.action_id, "open_deps")
        self.assertIn("Full Disk Access", r.detail)
        self.assertIn("Full Disk Access", r.fix)
        self.assertIn("claude update", r.fix)
        self.assertNotIn("8192", r.fix)          # no more fd-limit advice here

    def test_hang_under_launchd_warns_claude_blind(self):
        # ~/Downloads: a promptable TCC folder — the job has no UI, claude hangs
        r = self._claude_row({"state": "hang", "rc": None,
                              "text": "claude started under launchd but produced no exit"})
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "claude_blind")
        self.assertIn("never exited", r.detail)

    def test_unavailable_probe_is_a_plain_warn_without_failure_id(self):
        r = self._claude_row({"state": "unavailable", "rc": None,
                              "text": "launchd refused the probe job"})
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "")
        self.assertIn("could not ask launchd", r.detail)

    def test_other_launchd_failure_is_warn_not_claude_blind(self):
        r = self._claude_row({"state": "failed", "rc": 2, "text": "segmentation fault"})
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "")

    def test_no_installed_actd_plist_skips_the_claude_row(self):
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertNotIn("launchd claude", [r.name for r in results])

    def test_real_probe_is_inert_when_switched_off(self):
        # safety belt for the suite itself: with AIASSISTANT_LAUNCHD_PROBE=0 the
        # real probe must return without touching launchctl
        with mock.patch.dict(os.environ, {"AIASSISTANT_LAUNCHD_PROBE": "0"}), \
                mock.patch.object(doctor.subprocess, "run",
                                  side_effect=AssertionError("must not spawn")):
            res = doctor._launchd_claude_probe("/fake/claude", "/tmp")
        self.assertEqual(res["state"], "unavailable")

    # -- §55 orphan agents ------------------------------------------------------- #
    def test_no_orphans_is_ok(self):
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertEqual(by_name(results, "launchd orphans").status, doctor.OK)

    def test_loaded_retired_label_is_fail_with_bootout_hint(self):
        # the 51-day imessageradar case: loaded, crash-looping, no template
        listing = HEALTHY_LAUNCHCTL + "-\t1\tcom.zelin.aiassistant.imessageradar\n"
        results = doctor.run_checks(self.make_probes(launchctl=listing), fast=True)
        r = by_name(results, "launchd orphans")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertEqual(r.failure_id, "launchd_orphan")
        self.assertIn("imessageradar", r.detail)
        self.assertIn("launchctl bootout gui/$(id -u)/com.zelin.aiassistant.imessageradar",
                      r.fix)
        self.assertIn("install.sh", r.fix)

    def test_plist_file_without_template_warns_even_when_not_loaded(self):
        results = doctor.run_checks(
            self.make_probes(agent_files=["com.zelin.aiassistant.radar",
                                          "com.zelin.aiassistant.imessageradar"]),
            fast=True)
        r = by_name(results, "launchd orphans")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("imessageradar", r.detail)
        self.assertNotIn("aiassistant.radar", r.detail)   # has a template -> not an orphan

    def test_other_vendors_labels_are_never_orphans(self):
        listing = HEALTHY_LAUNCHCTL + "-\t0\tcom.zelin.storageguard\n"
        results = doctor.run_checks(
            self.make_probes(launchctl=listing, agent_files=["com.zelin.storageguard"]),
            fast=True)
        self.assertEqual(by_name(results, "launchd orphans").status, doctor.OK)

    # -- cron ----------------------------------------------------------------- #
    def test_empty_crontab_fails_ingest_and_warns_digest(self):
        results = doctor.run_checks(self.make_probes(cron=""), fast=True)
        self.assertEqual(by_name(results, "cron ingest chain").status, doctor.FAIL)
        self.assertEqual(by_name(results, "cron digest").status, doctor.WARN)

    def test_daily_digest_line_is_ok_and_names_the_cadence(self):
        r = by_name(doctor.run_checks(self.make_probes(), fast=True), "cron digest")
        self.assertEqual(r.status, doctor.OK)
        # installed != cards appear: the row must point at the knob (§17 D19)
        self.assertIn("digest.frequency", r.detail)

    def test_legacy_monday_now_digest_line_warns(self):
        # §17 D19 (review H1): the pre-D19 line still passes --now, which
        # bypasses the cadence gate — reporting it as "installed (cadence =
        # digest.frequency)" would be a lie while it mints a card every
        # Monday against frequency=off. Only `bash install.sh` replaces it.
        results = doctor.run_checks(self.make_probes(cron=LEGACY_DIGEST_CRON),
                                    fast=True)
        r = by_name(results, "cron digest")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("--now", r.detail)
        self.assertIn("digest.frequency", r.detail)
        self.assertIn("install.sh", r.fix)
        self.assertEqual(r.failure_id, "cron_missing")
        # the ingest chain half of the crontab is untouched by this verdict
        self.assertEqual(by_name(results, "cron ingest chain").status, doctor.OK)

    def test_commented_out_legacy_digest_line_is_missing_not_legacy(self):
        # a `#`-disabled line is neither installed nor forcing anything
        cron = ("*/30 * * * * cd /repo && ./ingest/screenpipe-export.sh\n"
                "# 7 9 * * 1 cd /repo && python3 -m act.digest --now\n")
        r = by_name(doctor.run_checks(self.make_probes(cron=cron), fast=True),
                    "cron digest")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("missing", r.detail)

    # -- daemon python / PyYAML ------------------------------------------------ #
    def test_daemon_python_without_pyyaml_is_fail(self):
        run = FakeRun(table={"python": (1, "ModuleNotFoundError: yaml")})
        results = doctor.run_checks(self.make_probes(run=run), fast=True)
        r = by_name(results, "daemon python")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("pip install", r.fix)
        self.assertIn("--break-system-packages", r.fix)

    def test_missing_runtime_json_is_warn(self):
        (self.home / "config" / "runtime.json").unlink()
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertEqual(by_name(results, "daemon python").status, doctor.WARN)

    # -- robustness ------------------------------------------------------------ #
    def test_probe_exception_becomes_fail_never_raises(self):
        def boom():
            raise RuntimeError("launchctl exploded")

        probes = self.make_probes()
        probes.launchctl_list = boom
        results = doctor.run_checks(probes, fast=True)  # must not raise
        crashed = [r for r in results if "diagnostic crashed" in r.detail]
        self.assertEqual(len(crashed), 1)
        self.assertEqual(crashed[0].status, doctor.FAIL)

    def test_exit_code_equals_number_of_fails(self):
        self.dashboard.unlink()                       # 1 fail
        probes = self.make_probes(cron="")            # +1 fail (ingest chain)
        code, out = self._main(probes, argv=["--fast"])
        self.assertEqual(code, 2)
        self.assertIn("2 fail", out)

    def test_missing_claude_is_fail(self):
        results = doctor.run_checks(
            self.make_probes(which_map={"npx": "/fake/npx", "gh": "/fake/gh"}),
            fast=True)
        self.assertEqual(by_name(results, "claude CLI").status, doctor.FAIL)


@unittest.skipIf(_WIN, "uses #!/bin/sh executable shims resolved via PATH")
class DaemonClaudeCheckTestCase(unittest.TestCase):
    """_check_daemon_claude — the 2026-07-08 two-installs incident: launchd's
    PATH resolved an outdated claude (no --bg) while the login shell used the
    new one; every dispatch failed with "unknown option '--bg'" forever.

    Real executable shims in tempdirs (echoing different --version / --help)
    exercise the real resolution path; only the plist/login-shell probes are
    injected."""

    NEW_HELP = "Usage: claude [options]\n  --bg, --background  background\n"
    OLD_HELP = "Usage: claude [options]\n  -p, --print  print mode\n"

    def setUp(self):
        # pinned darwin: this is the launchd two-installs incident; the fix
        # lines reference the OS installer (install.sh on darwin).
        p = mock.patch("sys.platform", "darwin")
        p.start()
        self.addCleanup(p.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="daemon-claude-"))

    def _shim(self, sub: str, version: str, help_text: str,
              bg_supported: bool) -> Path:
        d = self.tmp / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / "claude"
        bg_case = ("  --bg) echo backgrounded ;;\n" if bg_supported else
                   "  --bg) echo \"error: unknown option '--bg'\" >&2; exit 1 ;;\n")
        p.write_text("#!/bin/sh\ncase \"$1\" in\n"
                     "  --version) echo \"%s\" ;;\n"
                     "  --help) printf '%%b' \"%s\" ;;\n%s"
                     "esac\n" % (version, help_text.replace("\n", "\\n"), bg_case),
                     encoding="utf-8")
        p.chmod(0o755)
        return p

    def _probes(self, daemon_dir, shell_claude):
        return doctor.Probes(
            daemon_path_env=lambda: str(daemon_dir) if daemon_dir else None,
            login_shell_claude=lambda: str(shell_claude) if shell_claude else None,
        )

    def test_version_mismatch_is_fail_with_outdated_classification(self):
        old = self._shim("old", "2.1.16 (Claude Code)", self.OLD_HELP, False)
        new = self._shim("new", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        res = doctor._check_daemon_claude(self._probes(old.parent, new))
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "claude_cli_outdated")
        self.assertEqual(res.action_id, "open_deps")
        self.assertIn("2.1.16", res.detail)
        self.assertIn("2.1.206", res.detail)
        self.assertIn("install.sh", res.fix)

    def test_same_binary_everywhere_is_ok(self):
        new = self._shim("new", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        res = doctor._check_daemon_claude(self._probes(new.parent, new))
        self.assertEqual(res.status, doctor.OK)
        self.assertIn("same as your login shell", res.detail)

    def test_bg_unsupported_fails_even_without_a_shell_comparison(self):
        old = self._shim("old", "2.1.16 (Claude Code)", self.OLD_HELP, False)
        res = doctor._check_daemon_claude(self._probes(old.parent, None))
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "claude_cli_outdated")
        self.assertIn("--bg", res.detail)

    def test_two_copies_same_version_is_ok(self):
        a = self._shim("a", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        b = self._shim("b", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        res = doctor._check_daemon_claude(self._probes(a.parent, b))
        self.assertEqual(res.status, doctor.OK)

    def test_missing_plist_is_honest_warn(self):
        res = doctor._check_daemon_claude(self._probes(None, None))
        self.assertEqual(res.status, doctor.WARN)
        self.assertIn("install.sh", res.fix)

    def test_no_claude_on_daemon_path_is_fail(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        res = doctor._check_daemon_claude(self._probes(empty, None))
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "claude_cli_missing")


SYSTEMD_UNITS = [
    "zelin-actd.service", "zelin-webui.service",
    "zelin-gmail-radar.timer", "zelin-slack-radar.timer",
    "zelin-obsidian-radar.timer", "zelin-weekly-digest.timer",
]


def _systemctl(rows, bullets=()):
    """Build `systemctl --user list-units` output from {unit: (active, sub)}.

    ``bullets`` names units that get the failed-unit ● prefix systemd emits.
    """
    out = []
    for unit, (active, sub) in rows.items():
        prefix = "● " if unit in bullets else "  "
        out.append("%s%-28s loaded %-9s %-8s Zelin AI Assistant\n"
                   % (prefix, unit, active, sub))
    return "".join(out)


class SystemdDoctorTestCase(unittest.TestCase):
    """_check_systemd — the Linux systemd --user mirror of the launchd check.

    Feeds `systemctl --user list-units` fixture text (what the OS seam returns
    off-macOS) and asserts the parse: resident services must be active, timers
    must be active, actd is the only FAIL-if-down unit, a ● failed line parses.
    """

    def setUp(self):
        p = mock.patch("sys.platform", "linux")
        p.start()
        self.addCleanup(p.stop)

    def _probes(self, listing):
        return doctor.Probes(launchctl_list=lambda: listing,
                             systemd_units=list(SYSTEMD_UNITS))

    def _healthy_rows(self):
        rows = {"zelin-actd.service": ("active", "running"),
                "zelin-webui.service": ("active", "running"),
                # a timer-driven oneshot .service is correctly inactive between
                # fires — present in --all output but NOT in our expected list
                "zelin-gmail-radar.service": ("inactive", "dead")}
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            rows["zelin-%s.timer" % t] = ("active", "waiting")
        return rows

    def test_healthy_units_all_ok(self):
        results = doctor._check_systemd(self._probes(
            _systemctl(self._healthy_rows())))
        by = {r.name: r for r in results}
        self.assertEqual(by["actd"].status, doctor.OK)
        self.assertIn("active (running)", by["actd"].detail)
        self.assertEqual(by["webui"].status, doctor.OK)
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            self.assertEqual(by[t].status, doctor.OK)
            self.assertIn("waiting", by[t].detail)
        # the inactive oneshot .service is NOT reported (only residents+timers)
        self.assertNotIn("gmail-radar.service", by)

    def test_actd_down_fails_but_timer_only_warns(self):
        rows = self._healthy_rows()
        rows["zelin-actd.service"] = ("inactive", "dead")
        rows["zelin-gmail-radar.timer"] = ("inactive", "dead")
        by = {r.name: r for r in doctor._check_systemd(
            self._probes(_systemctl(rows)))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not running", by["actd"].detail)
        self.assertIn("systemctl --user enable --now", by["actd"].fix)
        self.assertEqual(by["actd"].failure_id, "agent_unloaded")
        self.assertEqual(by["gmail-radar"].status, doctor.WARN)

    def test_failed_unit_bullet_is_parsed(self):
        rows = self._healthy_rows()
        rows["zelin-actd.service"] = ("failed", "failed")
        by = {r.name: r for r in doctor._check_systemd(
            self._probes(_systemctl(rows, bullets=("zelin-actd.service",))))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("failed to start", by["actd"].detail)
        self.assertIn("journalctl", by["actd"].fix)

    def test_not_registered_when_manager_absent(self):
        # empty listing (e.g. `systemctl --user` could not reach the bus)
        by = {r.name: r for r in doctor._check_systemd(self._probes(""))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not registered", by["actd"].detail)
        self.assertIn("install-linux.sh", by["actd"].fix)
        self.assertEqual(by["webui"].status, doctor.WARN)

    def test_platform_composition_drops_macos_only_checks(self):
        names = {f.__name__ for f in doctor._checks_for_platform()}
        self.assertIn("_check_systemd", names)
        for macos_only in ("_check_launchd", "_check_cron",
                           "_check_screenpipe", "_check_npx"):
            self.assertNotIn(macos_only, names)
        with mock.patch("sys.platform", "darwin"):
            dnames = {f.__name__ for f in doctor._checks_for_platform()}
        self.assertIn("_check_launchd", dnames)
        self.assertIn("_check_cron", dnames)
        self.assertNotIn("_check_systemd", dnames)


# Full \ZelinAIAssistant\ task names doctor expects, mirroring SYSTEMD_UNITS.
TASKS = [
    "\\ZelinAIAssistant\\actd", "\\ZelinAIAssistant\\webui",
    "\\ZelinAIAssistant\\gmail-radar", "\\ZelinAIAssistant\\slack-radar",
    "\\ZelinAIAssistant\\obsidian-radar", "\\ZelinAIAssistant\\weekly-digest",
]


def _schtasks(rows):
    """Build `schtasks /query /fo LIST /v` output from {task: (status, state)}.

    LIST is one "Field: Value" block per task, blocks separated by a blank line.
    """
    out = []
    for task, (status, state) in rows.items():
        out.append(
            "Folder: \\ZelinAIAssistant\n"
            "HostName: FRIEND-PC\n"
            "TaskName: %s\n"
            "Next Run Time: 7/11/2026 9:00:00 AM\n"
            "Status: %s\n"
            "Logon Mode: Interactive only\n"
            "Scheduled Task State: %s\n"
            "\n" % (task, status, state))
    return "".join(out)


class WindowsScheduledTasksDoctorTestCase(unittest.TestCase):
    """_check_scheduled_tasks — the Windows Task Scheduler mirror of the launchd
    / systemd checks.

    Feeds `schtasks /query /fo LIST /v` fixture text (what the OS seam returns on
    Windows) and asserts the parse: resident tasks Running/Ready are OK, a
    Disabled task is down, actd is the only FAIL-if-down task, and unrelated
    OS tasks are ignored.
    """

    def setUp(self):
        p = mock.patch("sys.platform", "win32")
        p.start()
        self.addCleanup(p.stop)

    def _probes(self, listing):
        return doctor.Probes(launchctl_list=lambda: listing,
                             scheduled_tasks=list(TASKS))

    def _healthy_rows(self):
        rows = {"\\ZelinAIAssistant\\actd": ("Running", "Enabled"),
                "\\ZelinAIAssistant\\webui": ("Running", "Enabled")}
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            rows["\\ZelinAIAssistant\\" + t] = ("Ready", "Enabled")
        return rows

    def test_healthy_tasks_all_ok(self):
        # add an unrelated Windows task to prove it is filtered out
        rows = self._healthy_rows()
        rows["\\Microsoft\\Windows\\UpdateOrchestrator\\Scan"] = ("Ready", "Enabled")
        by = {r.name: r for r in doctor._check_scheduled_tasks(
            self._probes(_schtasks(rows)))}
        self.assertEqual(by["actd"].status, doctor.OK)
        self.assertIn("running", by["actd"].detail)
        self.assertEqual(by["webui"].status, doctor.OK)
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            self.assertEqual(by[t].status, doctor.OK)
            self.assertIn("ready", by[t].detail)
        self.assertNotIn("Scan", by)

    def test_actd_missing_fails_but_radar_only_warns(self):
        rows = self._healthy_rows()
        del rows["\\ZelinAIAssistant\\actd"]
        del rows["\\ZelinAIAssistant\\gmail-radar"]
        by = {r.name: r for r in doctor._check_scheduled_tasks(
            self._probes(_schtasks(rows)))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not registered", by["actd"].detail)
        self.assertIn("install.ps1", by["actd"].fix)
        self.assertEqual(by["actd"].failure_id, "agent_unloaded")
        self.assertEqual(by["gmail-radar"].status, doctor.WARN)

    def test_disabled_task_is_down(self):
        rows = self._healthy_rows()
        rows["\\ZelinAIAssistant\\actd"] = ("Ready", "Disabled")
        by = {r.name: r for r in doctor._check_scheduled_tasks(
            self._probes(_schtasks(rows)))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("disabled", by["actd"].detail)
        self.assertIn("/ENABLE", by["actd"].fix)

    def test_not_registered_when_schtasks_empty(self):
        by = {r.name: r for r in doctor._check_scheduled_tasks(self._probes(""))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not registered", by["actd"].detail)
        self.assertEqual(by["webui"].status, doctor.WARN)

    def test_platform_composition_uses_tasks_not_launchd_or_systemd(self):
        names = {f.__name__ for f in doctor._checks_for_platform()}
        self.assertIn("_check_scheduled_tasks", names)
        for other in ("_check_launchd", "_check_cron", "_check_systemd",
                      "_check_screenpipe", "_check_npx"):
            self.assertNotIn(other, names)

    def test_installer_is_ps1_on_windows(self):
        self.assertEqual(doctor._installer(), "install.ps1")


class DoctorLanguageRoutingTestCase(unittest.TestCase):
    """v0.42 (audit #16): the unclassified checks' detail/fix prose follows the
    §15 language resolution (act/lib/failures.ui_lang): AIASSISTANT_UI_LANG
    env var (app-spawned) > persisted setting (overrides/config.yaml) >
    system locale (zh* → zh, else en). Commands stay English in every case.
    _check_gh's missing-binary WARN is the probe — it only touches
    probes.which, so the test needs no filesystem fixtures."""

    def setUp(self):
        config.ensure_state_dirs()
        # hermetic: neither source may carry a persisted language going in
        self._stashed_overrides = self._stash(config.SETTINGS_OVERRIDES_PATH)
        self._stashed_config = self._stash(config.CONFIG_PATH)
        self.addCleanup(self._restore)

    @staticmethod
    def _stash(path):
        if path.exists():
            content = path.read_text(encoding="utf-8")
            path.unlink()
            return content
        return None

    def _restore(self):
        for path, content in ((config.SETTINGS_OVERRIDES_PATH, self._stashed_overrides),
                              (config.CONFIG_PATH, self._stashed_config)):
            if content is not None:
                path.write_text(content, encoding="utf-8")
            elif path.exists():
                path.unlink()

    def _gh(self, env=None, persisted=None):
        """Run the check with a controlled environment: the language-relevant
        vars are removed, then `env` applied; `persisted` writes the §15
        overrides file first."""
        if persisted is not None:
            config.SETTINGS_OVERRIDES_PATH.write_text(
                json.dumps({"language": persisted}), encoding="utf-8")
        base = {k: v for k, v in os.environ.items()
                if k not in ("AIASSISTANT_UI_LANG", "LANG", "LC_ALL")}
        base.update(env or {})
        with mock.patch.dict(os.environ, base, clear=True):
            return doctor._check_gh(doctor.Probes(which=lambda _n: None))

    def test_persisted_zh_detail_with_english_command_fix(self):
        r = self._gh(persisted="zh")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("缺失", r.detail)
        self.assertIn("brew install gh", r.fix)   # the command stays a command

    def test_persisted_en_detail_with_english_command_fix(self):
        r = self._gh(persisted="en")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("missing", r.detail)
        self.assertNotIn("缺失", r.detail)
        self.assertIn("brew install gh", r.fix)

    def test_env_var_wins_over_persisted_setting(self):
        # app-spawned: the Mac app passes its EFFECTIVE language — it must
        # beat a stale persisted value so app output always matches the app.
        r = self._gh(env={"AIASSISTANT_UI_LANG": "en"}, persisted="zh")
        self.assertIn("missing", r.detail)
        r = self._gh(env={"AIASSISTANT_UI_LANG": "zh"}, persisted="en")
        self.assertIn("缺失", r.detail)

    def test_system_locale_fallback_when_nothing_persisted(self):
        # cron/CLI with no persisted setting: system locale decides —
        # matching the Swift first-run default instead of hardcoded zh.
        self.assertIn("缺失", self._gh(env={"LANG": "zh_CN.UTF-8"}).detail)
        self.assertIn("缺失", self._gh(env={"LC_ALL": "zh_TW.UTF-8"}).detail)
        self.assertIn("missing", self._gh(env={"LANG": "en_US.UTF-8"}).detail)
        self.assertIn("missing", self._gh().detail)   # no locale at all → en


class CronProbeSchemaTestCase(unittest.TestCase):
    """cron_probe.json 半截损坏（read_ok 缺键 / 非 bool）的容错要与其它损坏
    probe 文件一致：WARN unreadable，绝不据半截数据给出「FDA 被禁」的红色
    确定性诊断 + 授权指引（shell writer 只写字面量 true/false）。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.addCleanup(lambda: doctor.CRON_PROBE_PATH.unlink(missing_ok=True))
        self.probes = doctor.Probes(crontab=lambda: HEALTHY_CRON)

    def _write(self, payload: dict) -> None:
        doctor.CRON_PROBE_PATH.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _fresh_ts() -> str:
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _check(self):
        return doctor._check_cron_probe(self.probes, cron_installed=True)

    def test_missing_read_ok_is_warn_not_fda_fail(self):
        self._write({"ts": self._fresh_ts(), "protected_path": "/v"})
        r = self._check()
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "")
        # v0.42: detail prose is language-routed — anchor the language-stable
        # file name, not the English word.
        self.assertIn("cron_probe.json", r.detail)

    def test_non_bool_read_ok_is_warn(self):
        for bad in (0, 1, None, "false", "true", []):
            self._write({"ts": self._fresh_ts(), "read_ok": bad,
                         "protected_path": "/v"})
            r = self._check()
            self.assertEqual(r.status, doctor.WARN, f"read_ok={bad!r}")
            self.assertEqual(r.failure_id, "", f"read_ok={bad!r}")

    def test_real_false_still_fails_as_fda_blocked(self):
        self._write({"ts": self._fresh_ts(), "read_ok": False,
                     "protected_path": "/v"})
        r = self._check()
        self.assertEqual(r.status, doctor.FAIL)
        self.assertEqual(r.failure_id, "cron_fda_blocked")

    def test_real_true_is_ok(self):
        self._write({"ts": self._fresh_ts(), "read_ok": True,
                     "protected_path": "/v"})
        self.assertEqual(self._check().status, doctor.OK)


if __name__ == "__main__":
    unittest.main()
