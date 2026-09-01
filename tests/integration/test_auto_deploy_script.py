"""scripts/auto-deploy.sh 行为判例（CONTRACT §56 合并即上岗）——真 bash + 真 git。

住在 tests/integration/（防腐 #7：真 IO 只许住这里，单文件时间预算见
BUDGET_SECONDS）。整套夹具是一个临时 bare origin + 一个 live clone，clone 里的
install.sh / act/doctor.py / act/lib/notify.py / act/auto_deploy.py 全是**记录
调用、按剧本出退出码**的假货，PATH 前置一个假 `curl`（按剧本回 GitHub
check-runs JSON），只有 scripts/auto-deploy.sh 是真的（逐字拷进夹具并提交）。
不出网、不起 launchd、不碰真 $HOME（HOME 指到临时目录）。

钉住的行为：
  - HEAD == origin/main → up_to_date，install 不跑；
  - **CI 闸门**（PR #124 审查 B1）：ff 之前查 origin/main **那个 sha** 的 `ci`
    check-run；success 才部署；in_progress / 尚无 run / API 不可达 → ci_pending
    不动 HEAD 下轮再试；红 → ci_failed + failed_sha 记账 + 一条通知；只有
    CI_CHECKS 里的名字算数；--force 跳过闸门；origin 不是 github.com 且没设
    AUTODEPLOY_CI_REPO → failed + 通知一次，不猜；
  - origin/main 前进 + 树干净 → ff、自检、install --non-interactive、等新 actd
    心跳、doctor 基线/复查、deployed（state 文件 + 一条通知）；
  - **自检**（B3）：合进来的 scripts/auto-deploy.sh 不能 `bash -n` 或
    act.auto_deploy import 不了 → 不装、回滚；
  - **就绪等待**（B2）：install 后必须等到 state/actd.heartbeat 由**新进程**
    （pid 变了）写下**新版本** + phase=idle；旧 daemon 的心跳不算；超时 =
    actd:no_heartbeat_from_new_version 回滚；新 daemon pass 抛 `failed` 只在旧
    daemon 也 `failed` 时算 pre-existing；
  - install 失败 / doctor 出现**新增** FAIL → git reset --hard 回旧 sha + 再装 +
    rolled_back + failed_sha 记账，下一轮对同一 sha 不再重试，--force 才重试；
  - **settle-before-verdict**（首次实战 2026-09-01 的假阳性回滚）：装后 doctor
    判决是重试环（默认 3 次、间隔 AUTODEPLOY_DOCTOR_SETTLE），只有撑到**最后
    一次**的新增 FAIL 才回滚；瞬态 FAIL（重启后 daemon 还在 settle / EPERM 窗口）
    自愈 → 照常 deployed；判决只看最后一次（早轮的名字不进 detail）；
  - 部署前已红的 doctor 项不归咎新版本（deployed，detail 点名 pre-existing）；
  - doctor 自身跑不出 JSON（import 崩 / 打印垃圾）= 致命而非 pre-existing：基线
    阶段就回滚且不装新版本；装完后**持续**崩同样回滚，瞬态崩走 settle 重试；
  - 脏树拒绝（不动 HEAD、同一 sha 只通知一次）；不在 main 拒绝；
  - 回滚前重验：部署期间 owner 改了 tracked 文件 → 回滚**被拒**（rollback_failed、
    通知、改动保留、HEAD 留在新版本）；install.sh 自己的 +x 位翻转不算改动，照常回滚；
  - **git 答不上来 ≠ detached**（首次实战：EPERM 窗口里 symbolic-ref/rev-parse
    全空，误报 detached + 空 sha）：入口与回滚重验都区分 rc>1（git 读不了 checkout
    → 诚实报错，不 reset）与 rc=1（真 detached）；"checkout left at" 永不插空值；
  - **store2 迁移感知回滚**：state/store2_truth.json 在本次部署期间出现 → 回滚
    被拒 + 指向 docs/TROUBLESHOOTING.md「store2 回滚」；部署前就在 → 照常回滚；
  - write_state / notify 失败时日志行携带子进程异常（首次实战两行 non-fatal
    全裸，PermissionError 只在 launchd stderr 里）；
  - 每轮都写 last_run（回滚路径、poisoned-sha 跳过也写）；
  - 锁：活 PID 持锁则跳过，死 PID 的锁视为陈旧；
  - 日志 1 MB 自压；ff-merge 途中脚本自身被替换也照常跑完（main 包裹）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "auto-deploy.sh"
_WIN = sys.platform.startswith("win")
BUDGET_SECONDS = 180  # ~45 runs of real bash+git; ~95 s on a 2024 Mac
_T0 = time.monotonic()

FAKE_INSTALL = r"""#!/bin/bash
# fake install.sh: record the call, exit per FAKE_INSTALL_RC_PLAN (one rc per line, consumed)
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ver="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$here/act/__init__.py")"
printf 'install %s head=%s version=%s active=%s\n' "$*" "$(git -C "$here" rev-parse HEAD)" "$ver" \
    "${AIASSISTANT_AUTODEPLOY_ACTIVE:-}" >> "$FAKE_INSTALL_LOG"
rc=0
if [ -n "${FAKE_INSTALL_RC_PLAN:-}" ] && [ -s "$FAKE_INSTALL_RC_PLAN" ]; then
    rc="$(head -n 1 "$FAKE_INSTALL_RC_PLAN")"
    tail -n +2 "$FAKE_INSTALL_RC_PLAN" > "$FAKE_INSTALL_RC_PLAN.tmp" && mv "$FAKE_INSTALL_RC_PLAN.tmp" "$FAKE_INSTALL_RC_PLAN"
fi
[ -n "${FAKE_INSTALL_SLEEP:-}" ] && sleep "$FAKE_INSTALL_SLEEP"
# simulate the owner editing a tracked file while the deploy runs / install.sh's own chmod
[ -n "${FAKE_INSTALL_EDIT:-}" ] && printf 'owner edit mid-deploy\n' >> "$here/README.md"
[ -n "${FAKE_INSTALL_CHMOD:-}" ] && chmod +x "$here/README.md"
# simulate the new actd's first pass migrating the registry truth to SQLite
# (§53 activation happens between the script's "before" sample and any rollback)
if [ -n "${FAKE_INSTALL_STORE2:-}" ]; then
    mkdir -p "$here/state"
    printf '{"activated_at": "2026-09-01T21:07:19Z"}\n' > "$here/state/store2_truth.json"
fi
# generic trigger file (the fake git uses it to start failing mid-run)
[ -n "${FAKE_INSTALL_TOUCH:-}" ] && touch "$FAKE_INSTALL_TOUCH"
# the restarted actd's heartbeat (§47.4): a NEW pid ($$ of this install run),
# the checkout's version, phase per FAKE_INSTALL_HEARTBEAT (default idle = one
# full pass done; "none" = the new daemon never writes one, e.g. dies on import)
hb="${FAKE_INSTALL_HEARTBEAT:-idle}"
if [ "$hb" != "none" ]; then
    mkdir -p "$here/state"
    printf '{"ts": "2026-09-01T00:00:00Z", "phase": "%s", "pid": %s, "interval": 10, "stale_after_s": 90, "version": "%s"}\n' \
        "$hb" "$$" "$ver" > "$here/state/actd.heartbeat"
fi
exit "$rc"
"""

FAKE_CURL = r"""#!/bin/bash
# fake curl: record the URL, answer the check-runs API per FAKE_CURL_PLAN (one
# word per line, consumed): success | failure | pending | missing | rerun |
# UNREACHABLE | GARBAGE. Default success. The success body also carries a RED
# non-required check (Lint) to pin that only CI_CHECKS names gate the deploy.
set -u
for a in "$@"; do case "$a" in http*) printf '%s\n' "$a" >> "$FAKE_CURL_LOG" ;; esac; done
verdict=success
if [ -n "${FAKE_CURL_PLAN:-}" ] && [ -s "$FAKE_CURL_PLAN" ]; then
    verdict="$(head -n 1 "$FAKE_CURL_PLAN")"
    tail -n +2 "$FAKE_CURL_PLAN" > "$FAKE_CURL_PLAN.tmp" && mv "$FAKE_CURL_PLAN.tmp" "$FAKE_CURL_PLAN"
fi
run() { printf '{"id": %s, "name": "%s", "status": "%s", "conclusion": %s}' "$1" "$2" "$3" "$4"; }
case "$verdict" in
    UNREACHABLE) exit 22 ;;
    GARBAGE)  printf 'not json at all' ;;
    missing)  printf '{"total_count": 0, "check_runs": []}' ;;
    pending)  printf '{"check_runs": [%s]}' "$(run 1 ci in_progress null)" ;;
    failure)  printf '{"check_runs": [%s]}' "$(run 1 ci completed '"failure"')" ;;
    rerun)    printf '{"check_runs": [%s, %s]}' "$(run 1 ci completed '"failure"')" "$(run 2 ci completed '"success"')" ;;
    *)        printf '{"check_runs": [%s, %s]}' "$(run 1 ci completed '"success"')" \
                     "$(run 2 'Lint (shellcheck + ruff)' completed '"failure"')" ;;
esac
exit 0
"""

FAKE_GIT = r"""#!/bin/bash
# fake git: once FAKE_GIT_BREAK_FILE exists, symbolic-ref and every HEAD query
# fail like the live EPERM window (2026-09-01: git went dark mid-rollback and
# the refusal blamed a phantom 'detached'); everything else passes through.
set -u
if [ -n "${FAKE_GIT_BREAK_FILE:-}" ] && [ -f "$FAKE_GIT_BREAK_FILE" ]; then
    for a in "$@"; do
        case "$a" in
            symbolic-ref|HEAD)
                echo "fatal: Operation not permitted (fixture EPERM)" >&2
                exit 128 ;;
        esac
    done
fi
exec @REAL_GIT@ "$@"
"""

FAKE_SHIM = '"""fake act.auto_deploy: only has to import (the script self-checks it after the merge)."""\n'

FAKE_DOCTOR = r'''"""fake act.doctor: FAIL names per call from FAKE_DOCTOR_PLAN (one line per call, consumed)."""
import json, os, sys
from act import __version__
plan = os.environ.get("FAKE_DOCTOR_PLAN")
names = []
if plan and os.path.exists(plan):
    with open(plan, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if lines:
        first, rest = lines[0], lines[1:]
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write("\n".join(rest) + ("\n" if rest else ""))
        names = [n for n in first.split(",") if n and n != "-"]
log = os.environ.get("FAKE_DOCTOR_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("doctor %s version=%s fails=%s\n" % (" ".join(sys.argv[1:]), __version__, ",".join(names) or "-"))
if "GARBAGE" in names:
    # the doctor itself is broken on this commit: no JSON at all
    print("Traceback (most recent call last): ModuleNotFoundError: No module named 'yaml'")
    sys.exit(1)
checks = [{"name": n, "status": "fail", "detail": "", "fix": ""} for n in names]
checks.append({"name": "home", "status": "ok", "detail": "", "fix": ""})
print(json.dumps({"home": os.getcwd(), "checks": checks}))
sys.exit(len(names))
'''

FAKE_NOTIFY = '''"""fake act.lib.notify: append title|body to FAKE_NOTIFY_LOG."""
import os
def notify(title, body, subtitle=None, req=None, kind=None):
    path = os.environ.get("FAKE_NOTIFY_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s|%s\\n" % (title, body))
    return True
'''


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def tearDownModule():
    elapsed = time.monotonic() - _T0
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_auto_deploy_script.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(_WIN, "bash + install.sh are POSIX-only; the Windows installer is install.ps1")
class AutoDeployScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autodeploy-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.logs = self.tmp / "logs"
        self.notify_log = self.tmp / "notify.log"
        self.install_log = self.tmp / "install.log"
        self.doctor_log = self.tmp / "doctor.log"
        self.doctor_plan = self.tmp / "doctor.plan"
        self.install_rc_plan = self.tmp / "install.rc"
        self.curl_log = self.tmp / "curl.log"
        self.curl_plan = self.tmp / "curl.plan"
        # fake curl shadows the real one on PATH (the script only calls curl
        # for the check-runs API)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake_curl = self.bin / "curl"
        fake_curl.write_text(FAKE_CURL, encoding="utf-8")
        fake_curl.chmod(0o755)

        # bare origin whose HEAD is main, seeded from a scratch clone
        self.origin = self.tmp / "origin.git"
        _git(self.tmp, "init", "-q", "--bare", str(self.origin))
        _git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        seed = self.tmp / "seed"
        seed.mkdir()
        _git(seed, "init", "-q")
        _git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
        self._write_tree(seed, "0.48.3")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed v0.48.3")
        _git(seed, "remote", "add", "origin", str(self.origin))
        _git(seed, "push", "-q", "origin", "main")
        self.dev = seed  # later commits are made here and pushed

        # the live checkout under test
        self.live = self.tmp / "live"
        _git(self.tmp, "clone", "-q", str(self.origin), str(self.live))
        self.script = self.live / "scripts" / "auto-deploy.sh"
        self.base_sha = _git(self.live, "rev-parse", "HEAD")

    # -- fixture helpers ---------------------------------------------------- #

    def _write_tree(self, root, version):
        (root / "act" / "lib").mkdir(parents=True, exist_ok=True)
        (root / "act" / "__init__.py").write_text('__version__ = "%s"\n' % version, encoding="utf-8")
        (root / "act" / "lib" / "__init__.py").write_text("", encoding="utf-8")
        (root / "act" / "lib" / "notify.py").write_text(FAKE_NOTIFY, encoding="utf-8")
        (root / "act" / "doctor.py").write_text(FAKE_DOCTOR, encoding="utf-8")
        (root / "act" / "auto_deploy.py").write_text(FAKE_SHIM, encoding="utf-8")
        inst = root / "install.sh"
        inst.write_text(FAKE_INSTALL, encoding="utf-8")
        inst.chmod(0o755)
        (root / "scripts").mkdir(exist_ok=True)
        real = root / "scripts" / "auto-deploy.sh"
        shutil.copyfile(str(SCRIPT), str(real))
        real.chmod(0o755)
        (root / "README.md").write_text("fixture\n", encoding="utf-8")

    def push(self, version, extra=None):
        """Advance origin/main: bump the version (+ optional extra edits)."""
        (self.dev / "act" / "__init__.py").write_text('__version__ = "%s"\n' % version, encoding="utf-8")
        if extra:
            extra(self.dev)
        _git(self.dev, "add", "-A")
        _git(self.dev, "commit", "-q", "-m", "v%s" % version)
        _git(self.dev, "push", "-q", "origin", "main")
        return _git(self.dev, "rev-parse", "HEAD")

    def run_script(self, *args, doctor_plan=None, install_rc=None, ci=None, env=None):
        if doctor_plan is not None:
            self.doctor_plan.write_text("\n".join(doctor_plan) + "\n", encoding="utf-8")
        elif self.doctor_plan.exists():
            self.doctor_plan.unlink()
        if install_rc is not None:
            self.install_rc_plan.write_text("\n".join(str(r) for r in install_rc) + "\n", encoding="utf-8")
        elif self.install_rc_plan.exists():
            self.install_rc_plan.unlink()
        if ci is not None:
            self.curl_plan.write_text("\n".join(ci) + "\n", encoding="utf-8")
        elif self.curl_plan.exists():
            self.curl_plan.unlink()
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("AIASSISTANT_", "AUTODEPLOY_", "FAKE_", "GIT_"))}
        full = {
            **base,
            "PATH": str(self.bin) + os.pathsep + base.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "AIASSISTANT_PYTHON": sys.executable,
            "AUTODEPLOY_LOG_DIR": str(self.logs),
            "AUTODEPLOY_HEARTBEAT_DEADLINE": "1",
            "AUTODEPLOY_INSTALL_TIMEOUT": "30",
            "AUTODEPLOY_DOCTOR_SETTLE": "0",
            # the fixture origin is a local bare repo, not github.com: name the
            # repo the fake curl "serves" so the CI gate runs like on the owner Mac
            "AUTODEPLOY_CI_REPO": "fixture/repo",
            "FAKE_NOTIFY_LOG": str(self.notify_log),
            "FAKE_INSTALL_LOG": str(self.install_log),
            "FAKE_DOCTOR_LOG": str(self.doctor_log),
            "FAKE_DOCTOR_PLAN": str(self.doctor_plan),
            "FAKE_INSTALL_RC_PLAN": str(self.install_rc_plan),
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CURL_PLAN": str(self.curl_plan),
            **(env or {}),
        }
        proc = subprocess.run(["bash", str(self.script), *args], cwd=str(self.tmp),
                              capture_output=True, text=True, timeout=110, env=full)
        return proc

    def state(self):
        path = self.live / "state" / "deploy_state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def head(self):
        return _git(self.live, "rev-parse", "HEAD")

    def log_text(self):
        p = self.logs / "auto-deploy.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def installs(self):
        return self.install_log.read_text(encoding="utf-8").splitlines() if self.install_log.exists() else []

    def notifications(self):
        return self.notify_log.read_text(encoding="utf-8").splitlines() if self.notify_log.exists() else []

    def ci_queries(self):
        return self.curl_log.read_text(encoding="utf-8").splitlines() if self.curl_log.exists() else []

    def doctor_runs(self):
        return self.doctor_log.read_text(encoding="utf-8").splitlines() if self.doctor_log.exists() else []

    def seed_heartbeat(self, version, phase, pid=1):
        """A heartbeat left by the daemon running BEFORE the deploy."""
        hb = self.live / "state" / "actd.heartbeat"
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(json.dumps({"ts": "2026-09-01T00:00:00Z", "phase": phase, "pid": pid,
                                  "interval": 10, "stale_after_s": 90, "version": version}),
                      encoding="utf-8")

    def install_fake_git(self):
        """Shadow git on the script's PATH with the EPERM-window wrapper."""
        real = shutil.which("git")
        fake = self.bin / "git"
        fake.write_text(FAKE_GIT.replace("@REAL_GIT@", real), encoding="utf-8")
        fake.chmod(0o755)
        return self.tmp / "git.break"  # tests hand this path to FAKE_GIT_BREAK_FILE

    # -- 1. nothing to do ---------------------------------------------------- #

    def test_up_to_date_runs_nothing_and_records_it(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "install.sh must not run when HEAD == origin/main")
        st = self.state()
        self.assertEqual(st["status"], "up_to_date")
        self.assertEqual(st["head"], self.base_sha)
        self.assertEqual(st["version"], "0.48.3")
        self.assertNotIn("last_deployed", st, "never deployed by this job → no last_deployed")
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.ci_queries(), [], "nothing to deploy → the CI API is not asked")
        self.assertFalse((self.live / "state" / "auto-deploy.lock").exists(), "lock released")

    # -- 2. the happy path --------------------------------------------------- #

    def test_fast_forward_deploy_installs_once_and_notifies(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(_git(self.live, "symbolic-ref", "--short", "HEAD"), "main", "stays on main")
        inst = self.installs()
        self.assertEqual(len(inst), 1, inst)
        self.assertIn("install --non-interactive", inst[0])
        self.assertIn("head=%s" % target, inst[0], "install.sh runs on the NEW checkout")
        self.assertIn("active=1", inst[0], "install.sh is told it runs inside the agent")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertEqual(st["version"], "0.48.4")
        self.assertEqual(st["head"], target)
        self.assertEqual(st["prev"], self.base_sha)
        self.assertIn("last_deployed", st)
        self.assertNotIn("failed_sha", st)
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("v0.48.4", notes[0])
        # doctor ran twice, both times with the NEW code (baseline is pre-install, same doctor)
        doc = self.doctor_runs()
        self.assertEqual(len(doc), 2, doc)
        self.assertTrue(all("version=0.48.4" in ln and "--fast --json" in ln for ln in doc), doc)
        # the CI gate asked about THE sha being deployed, once, at the configured repo
        queries = self.ci_queries()
        self.assertEqual(len(queries), 1, queries)
        self.assertIn("/repos/fixture/repo/commits/%s/check-runs" % target, queries[0])
        self.assertIn("CI green on %s" % target[:7], self.log_text())

    def test_second_run_after_deploy_is_up_to_date_and_keeps_last_deployed(self):
        self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"])
        deployed_at = self.state()["last_deployed"]
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "up_to_date")
        self.assertEqual(st["last_deployed"], deployed_at, "carried over, not rewritten")
        self.assertEqual(st["prev"], self.base_sha)
        self.assertEqual(len(self.installs()), 1)

    def test_script_replaced_by_the_merge_still_completes(self):
        # the ff-merge rewrites scripts/auto-deploy.sh on disk mid-run; bash must
        # already hold the whole file (main "$@" wrapper) and finish normally
        def touch_script(dev):
            p = dev / "scripts" / "auto-deploy.sh"
            p.write_text(p.read_text(encoding="utf-8") + "\n# fixture edit\n", encoding="utf-8")
        target = self.push("0.48.4", extra=touch_script)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    # -- 2b. the CI gate (B1) ------------------------------------------------- #
    # ruleset protect-main 是 non-strict：PR head 绿了就能合，合出来的 merge
    # commit 的树没人测过；main 上的 `ci` 跑 ~8 min，这个 job 在 +10 min 开火。
    # 所以部署前必须问 GitHub：**这个 sha** 的 ci 结束了且绿了吗。

    def test_ci_still_running_defers_without_touching_the_checkout(self):
        target = self.push("0.48.4")
        for plan, why in (("pending", "in_progress"), ("missing", "no ci check-run yet"),
                          ("UNREACHABLE", "unreachable"), ("GARBAGE", "no JSON")):
            proc = self.run_script(ci=[plan])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.head(), self.base_sha, plan)
            self.assertEqual(self.installs(), [], plan)
            st = self.state()
            self.assertEqual(st["status"], "ci_pending", plan)
            self.assertIn(target[:7], st["detail"], plan)
            self.assertIn(why, st["detail"], plan)
            self.assertEqual(st["head"], self.base_sha)
            self.assertEqual(st["version"], "0.48.3")
            self.assertNotIn("failed_sha", st, "pending is not a verdict — never poisons")
        self.assertEqual(self.notifications(), [], "waiting for CI is not news")
        self.assertEqual(len(self.ci_queries()), 4, "asked once per run")
        # CI finishes green → the very next run deploys
        proc = self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_red_ci_on_main_poisons_the_sha_notifies_once_and_never_merges(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "a red sha is never fast-forwarded to")
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.doctor_runs(), [], "no doctor either — nothing changed on disk")
        st = self.state()
        self.assertEqual(st["status"], "ci_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("ci failure", st["detail"])
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("CI red", notes[0])
        # next interval: poisoned → quiet, and the API is not even asked again
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(len(self.ci_queries()), 1)
        self.assertEqual(len(self.notifications()), 1)
        self.assertIn("already failed", self.log_text())
        # a new commit on main is asked about normally
        target2 = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(self.head(), target2)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("failed_sha", self.state())

    def test_force_skips_the_ci_gate(self):
        # --force = the owner asked for THIS sha: no API call, straight to the deploy
        target = self.push("0.48.4")
        self.run_script(ci=["failure"])
        self.assertEqual(self.state()["status"], "ci_failed")
        proc = self.run_script("--force", doctor_plan=["-", "-"], ci=["failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(len(self.ci_queries()), 1, "the forced run did not ask")
        self.assertIn("CI gate skipped", self.log_text())

    def test_rerun_of_a_red_check_the_newest_run_wins(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["rerun"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_only_the_configured_checks_gate(self):
        # the default fake body carries a red Lint run next to the green ci run
        # (the happy path deploys on it: Lint is not in CI_CHECKS); naming Lint
        # as required turns that same body into a refusal
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"],
                               env={"AUTODEPLOY_CI_CHECKS": "ci, Lint (shellcheck + ruff)"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "ci_failed")
        self.assertIn("Lint (shellcheck + ruff) failure", st["detail"])
        self.assertEqual(st["failed_sha"], target)

    def test_non_github_origin_without_ci_repo_refuses_and_notifies_once(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"AUTODEPLOY_CI_REPO": ""})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "never deploys what it cannot verify")
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "failed")
        self.assertIn("AUTODEPLOY_CI_REPO", st["detail"])
        self.assertEqual(self.ci_queries(), [])
        self.assertEqual(len(self.notifications()), 1)
        self.run_script(doctor_plan=["-", "-"], env={"AUTODEPLOY_CI_REPO": ""})
        self.assertEqual(len(self.notifications()), 1, "same pending sha: quiet")
        # naming the repo unblocks it
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target)

    # -- 2c. self-check of the deploy agent (B3) ----------------------------- #

    def test_merge_that_breaks_the_deploy_script_rolls_back_before_installing(self):
        def break_script(dev):
            p = dev / "scripts" / "auto-deploy.sh"
            p.write_text(p.read_text(encoding="utf-8") + "\nif [ 1 -eq 1 ]; then\n", encoding="utf-8")
        target = self.push("0.48.4", extra=break_script)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back to PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("self-check", st["detail"])
        self.assertIn("bash -n", st["detail"])
        inst = self.installs()
        self.assertEqual(len(inst), 1, inst)
        self.assertIn("head=%s" % self.base_sha, inst[0], "the only install is the rollback at PREV")
        self.assertEqual(self.doctor_runs(), [], "self-check comes before the doctor baseline")
        self.assertEqual(len(self.notifications()), 1)

    def test_merge_that_breaks_the_launchd_shim_rolls_back_before_installing(self):
        def break_shim(dev):
            (dev / "act" / "auto_deploy.py").write_text("def (:\n", encoding="utf-8")
        target = self.push("0.48.4", extra=break_shim)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("act.auto_deploy does not import", st["detail"])
        self.assertEqual(len(self.installs()), 1)

    # -- 2d. readiness: the NEW actd must complete a pass (B2) ---------------- #
    # 30 s 静置 + 一次 launchctl 采样是抛硬币：import 即死的 KeepAlive actd 每个
    # ~10 s 节流周期亮 ~0.5 s 的 pid，旧 daemon 的 heartbeat/dashboard 文件又
    # 90 s 内都算新鲜。心跳文件带 version + pid + phase（§47.4）——等它。

    def test_new_actd_that_never_completes_a_pass_rolls_back(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "starting"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("actd:no_heartbeat_from_new_version", st["detail"])
        self.assertIn("0.48.4", st["detail"])
        self.assertEqual(len(self.installs()), 2, "install at NEW, rollback install at PREV")
        self.assertEqual(len(self.doctor_runs()), 1, "no post-install doctor: not ready is already the verdict")

    def test_the_old_daemons_heartbeat_does_not_count_even_with_the_same_version(self):
        # no version bump (§56.1 says every PR bumps, but a merge that does not
        # must still not be waved through by the OLD daemon's idle beat) and
        # the new actd dies on import (writes nothing)
        target = self.push("0.48.4")
        self.seed_heartbeat("0.48.4", "idle", pid=1)
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("no_heartbeat_from_new_version", st["detail"])

    def test_no_heartbeat_file_at_all_before_and_after_rolls_back(self):
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertIn("no_heartbeat_from_new_version", self.state()["detail"])

    def test_new_actd_whose_pass_throws_rolls_back_when_the_old_one_was_fine(self):
        self.push("0.48.4")
        self.seed_heartbeat("0.48.3", "idle")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "failed"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.state()["status"], "rolled_back")

    def test_pre_existing_failing_pass_is_not_blamed_on_the_new_version(self):
        # the old daemon was already throwing every pass: the new one doing the
        # same is pre-existing (else the machine could never take the fix)
        target = self.push("0.48.4")
        self.seed_heartbeat("0.48.3", "failed")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "failed"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_ready_heartbeat_is_logged_and_the_doctor_runs_after_it(self):
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("completed a pass (heartbeat: 0.48.4 ", self.log_text())
        self.assertEqual(len(self.doctor_runs()), 2)

    # -- 3. rollback --------------------------------------------------------- #

    def test_new_doctor_failure_rolls_back_and_poisons_that_sha(self):
        target = self.push("0.48.4")
        # green baseline, red after install — and red on every settle retry
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "back on the previous commit")
        self.assertEqual(_git(self.live, "symbolic-ref", "--short", "HEAD"), "main")
        inst = self.installs()
        self.assertEqual(len(inst), 2, inst)
        self.assertIn("head=%s" % target, inst[0])
        self.assertIn("head=%s" % self.base_sha, inst[1], "re-installed at PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertEqual(st["head"], self.base_sha)
        self.assertEqual(st["version"], "0.48.3")
        self.assertIn("dashboard", st["detail"])
        self.assertIn("last_run", st, "§56.4: last_run describes THIS run, rollback included")
        rolled_back_at = st["last_run"]
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("rolled back to %s" % self.base_sha[:7], notes[0])
        # next interval: same origin/main sha → no retry storm
        time.sleep(1.1)  # last_run has 1 s resolution
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 2, "no third install for the poisoned sha")
        self.assertEqual(self.head(), self.base_sha)
        self.assertIn("already failed", self.log_text())
        self.assertEqual(len(self.notifications()), 1, "silent while waiting")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back", "verdict carried: still poisoned")
        self.assertNotEqual(st["last_run"], rolled_back_at, "but the skip round still stamps last_run")
        # a new commit on main is tried normally
        target2 = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target2)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("failed_sha", self.state())

    def test_force_retries_the_poisoned_sha(self):
        target = self.push("0.48.4")
        self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"])
        self.assertEqual(self.state()["status"], "rolled_back")
        proc = self.run_script("--force", doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_install_failure_rolls_back(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_rc=[2, 0])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("install.sh exited 2", st["detail"])
        self.assertEqual(len(self.installs()), 2)

    def test_pre_existing_red_is_not_blamed_on_the_new_version(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["cron", "cron"])  # same FAIL before and after
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertIn("pre-existing", st["detail"])
        self.assertIn("cron", st["detail"])
        self.assertEqual(len(self.installs()), 1)

    def test_unparseable_doctor_on_the_new_code_rolls_back_before_installing(self):
        # H1：baseline 与复查都用新代码——doctor 在两次都 unparseable 时「新增 FAIL」
        # 为空，旧逻辑会判 deployed 并把 doctor:unparseable 写成 pre-existing。
        # 恰恰是 doctor 自己 import 崩的那种提交，必须回滚，且新版本压根不该被装。
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["GARBAGE", "GARBAGE"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back to PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("unparseable", st["detail"])
        inst = self.installs()
        self.assertEqual(len(inst), 1, inst)
        self.assertIn("head=%s" % self.base_sha, inst[0], "the only install is the rollback at PREV")
        self.assertNotIn("pre-existing", st["detail"])
        self.assertEqual(len(self.notifications()), 1)

    def test_unparseable_doctor_after_install_rolls_back(self):
        # 持续 unparseable（每次 settle 重试都崩）才回滚——瞬态崩见 settle 测试
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "GARBAGE", "GARBAGE", "GARBAGE"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("unparseable", st["detail"])
        self.assertEqual(len(self.installs()), 2, "install at NEW, then the rollback install at PREV")

    def test_rollback_refuses_to_destroy_edits_made_during_the_deploy(self):
        # P1（review）：step 4 的脏树检查到 reset --hard 之间隔着 install + settle +
        # doctor；owner 在这几分钟里改了 tracked 文件，reset 会无声毁掉它（§0.2）。
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_EDIT": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "not reset: the owner's edit would be lost")
        self.assertIn("owner edit mid-deploy", (self.live / "README.md").read_text(encoding="utf-8"))
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("refused", st["detail"])
        self.assertIn("README.md", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install either")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("REFUSED", notes[0])
        self.assertIn("rollback REFUSED", self.log_text())

    def test_rollback_ignores_install_sh_own_mode_flips(self):
        # install.sh 的 chmod +x 是它自己的脚印，不是 owner 的工作：不得阻止回滚
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_CHMOD": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back despite the mode-only change")
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertEqual(self.state()["failed_sha"], target)
        self.assertEqual(len(self.installs()), 2)

    # -- 3b. 首次实战修的四类 bug（2026-09-01 v0.48.8，§56.3 step 10 修订） ---- #
    # 实录：install.sh 重启全部 daemon 后 12 s 就取 doctor 判决，撞上 store2 首跑
    # 迁移 + 外置卷瞬态 EPERM 窗口 → 6 个假「new FAIL」→ 假阳性回滚；回滚重验里
    # symbolic-ref / rev-parse 全空 → 误报 "HEAD is on 'detached'" + 空 sha（歪打
    # 正着拦下了会 strand SQLite 账本的回退——本组测试把这份运气变成结构）；
    # write_state / notify 的失败行不带原因（PermissionError 只在 launchd stderr）。

    def test_transient_doctor_fail_after_install_settles_and_deploys(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "store2,config.yaml", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "a FAIL that clears on retry is settling, not a verdict")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("failed_sha", st)
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        self.assertEqual(len(self.doctor_runs()), 3, "baseline + first sample + the clean retry")
        self.assertIn("daemons may still be settling", self.log_text())
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("v0.48.4", notes[0])

    def test_transient_unparseable_doctor_after_install_settles(self):
        # 装后第一采样 doctor 整个崩（EPERM 窗口里连 import 都可能失败）——基线
        # 已证明新代码的 doctor 能跑，装后的瞬态崩走同一条 settle 重试路
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "GARBAGE", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(len(self.installs()), 1)

    def test_persistent_new_fail_verdict_names_only_the_final_run(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "store2", "dashboard", "dashboard"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "still red on the final attempt → rollback")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("dashboard", st["detail"])
        self.assertNotIn("store2", st["detail"], "a transient first-attempt name is not the verdict")
        self.assertEqual(len(self.doctor_runs()), 4, "baseline + 3 attempts")

    def test_git_failure_during_rollback_is_reported_as_git_not_detached(self):
        target = self.push("0.48.4")
        trigger = self.install_fake_git()
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_GIT_BREAK_FILE": str(trigger),
                                    "FAKE_INSTALL_TOUCH": str(trigger)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "never resets through a git it cannot trust")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertIn("git cannot read the checkout", st["detail"])
        self.assertNotIn("detached", st["detail"])
        self.assertNotIn("detached", self.log_text())
        self.assertIn("checkout left at unknown", self.log_text(),
                      "the refusal line never interpolates empty git output")
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        self.assertEqual(len(self.notifications()), 1)

    def test_git_failure_at_entry_is_an_environment_error_not_refused_branch(self):
        self.push("0.48.4")
        trigger = self.install_fake_git()
        trigger.write_text("", encoding="utf-8")  # git is dark from the very first call
        proc = self.run_script(env={"FAKE_GIT_BREAK_FILE": str(trigger)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "failed")
        self.assertIn("cannot read HEAD", st["detail"])
        self.assertNotIn("detached", st["detail"])
        self.assertEqual(self.notifications(), [], "an environment hiccup retries silently")

    def test_store2_truth_appearing_during_the_deploy_refuses_code_rollback(self):
        # D2 安全网：本次部署里 actd 把卡片真源迁到 SQLite，回退代码会让账本落在
        # 没有 store2 运行时的版本上——拒绝，并指向 TROUBLESHOOTING 的手动流程
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_STORE2": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "code stays on the new version")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("TROUBLESHOOTING", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("store2", notes[0])
        self.assertIn("TROUBLESHOOTING", notes[0])

    def test_store2_truth_present_before_the_deploy_rolls_back_normally(self):
        self.push("0.48.4")
        marker = self.live / "state" / "store2_truth.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"activated_at": "2026-08-01T00:00:00Z"}\n', encoding="utf-8")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "a pre-existing marker never blocks a rollback")
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertTrue(marker.exists(), "reset --hard does not touch untracked state/")

    def test_write_state_failure_logs_the_cause(self):
        (self.live / "state").mkdir(exist_ok=True)
        (self.live / "state" / "deploy_state.json.tmp").mkdir()  # open(tmp, "w") → IsADirectoryError
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("write_state failed (non-fatal): IsADirectoryError", self.log_text())

    def test_notify_failure_logs_the_cause(self):
        self.push("0.48.4")
        (self.live / "README.md").write_text("dirty\n", encoding="utf-8")  # → one notify
        proc = self.run_script(env={"FAKE_NOTIFY_LOG": str(self.tmp)})  # open(dir, "a") → IsADirectoryError
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("notify failed (non-fatal)", self.log_text())
        self.assertIn("IsADirectoryError", self.log_text())

    def test_install_timeout_counts_as_failure(self):
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_rc=[0, 0],
                               env={"AUTODEPLOY_INSTALL_TIMEOUT": "1", "FAKE_INSTALL_SLEEP": "5"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.state()["status"], "rollback_failed",
                         "the rollback install also hits the 1s timeout → honest rollback_failed")
        self.assertIn("timeout", self.log_text())

    # -- 4. refusals --------------------------------------------------------- #

    def test_dirty_tree_refuses_and_notifies_once_per_sha(self):
        target = self.push("0.48.4")
        (self.live / "README.md").write_text("local edit\n", encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "HEAD untouched")
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "refused_dirty")
        self.assertIn("README.md", st["detail"])
        self.assertEqual(len(self.notifications()), 1)
        self.assertIn("dirty", self.notifications()[0])
        # same pending sha, still dirty: quiet
        self.run_script()
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(self.state()["status"], "refused_dirty")
        # the owner cleans up → the very next run deploys
        _git(self.live, "checkout", "--", "README.md")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_untracked_files_do_not_count_as_dirty(self):
        target = self.push("0.48.4")
        (self.live / "scratch.txt").write_text("untracked\n", encoding="utf-8")
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_not_on_main_refuses(self):
        self.push("0.48.4")
        _git(self.live, "checkout", "-q", "-b", "experiment")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "refused_branch")
        self.assertIn("experiment", st["detail"])

    def test_diverged_local_main_refuses_without_force_push(self):
        self.push("0.48.4")
        (self.live / "README.md").write_text("local commit\n", encoding="utf-8")
        _git(self.live, "commit", "-q", "-am", "local divergence")
        local = self.head()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), local, "never resets a diverged local main")
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("ff-only", self.state()["detail"])
        self.assertEqual(len(self.notifications()), 1)

    # -- 5. lock + log cap --------------------------------------------------- #

    def test_live_lock_skips_and_stale_lock_is_reclaimed(self):
        self.push("0.48.4")
        lock = self.live / "state" / "auto-deploy.lock"
        lock.mkdir(parents=True)
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.kill)
        (lock / "pid").write_text("%d\n" % sleeper.pid, encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "another live run holds the lock")
        self.assertIn("another auto-deploy run is active", self.log_text())
        self.assertTrue(lock.exists(), "a live lock is never removed")
        # stale: the holder is gone
        sleeper.kill()
        sleeper.wait()
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removing stale lock", self.log_text())
        self.assertEqual(self.state()["status"], "deployed")
        self.assertFalse(lock.exists())

    def test_fresh_lock_without_pid_is_live_and_old_one_is_stale(self):
        # P2（review）：mkdir 与写 pid 之间的另一实例看到「无 pid」不得当陈旧锁回收
        self.push("0.48.4")
        lock = self.live / "state" / "auto-deploy.lock"
        lock.mkdir(parents=True)
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "a fresh pid-less lock is a holder mid-mkdir")
        self.assertIn("has not written its pid yet", self.log_text())
        self.assertTrue(lock.exists())
        # the same pid-less dir, but old: a crash between mkdir and printf → stale
        old = time.time() - 600
        os.utime(str(lock), (old, old))
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removing stale lock", self.log_text())
        self.assertEqual(self.state()["status"], "deployed")
        self.assertFalse(lock.exists())

    def test_log_is_capped(self):
        self.logs.mkdir()
        big = self.logs / "auto-deploy.log"
        big.write_text("x" * (1048576 + 4096), encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(big.stat().st_size, 1048576)
        self.assertIn("log capped", big.read_text(encoding="utf-8"))

    def test_not_a_git_checkout_exits_1(self):
        plain = self.tmp / "plain"
        (plain / "scripts").mkdir(parents=True)
        shutil.copyfile(str(SCRIPT), str(plain / "scripts" / "auto-deploy.sh"))
        proc = subprocess.run(["bash", str(plain / "scripts" / "auto-deploy.sh")],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "HOME": str(self.home),
                                   "AIASSISTANT_PYTHON": sys.executable,
                                   "AUTODEPLOY_LOG_DIR": str(self.logs)})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not a git checkout", self.log_text())


if __name__ == "__main__":
    unittest.main()
