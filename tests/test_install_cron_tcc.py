"""install.sh apply_crontab — crontab 被 TCC 拒写不是部署失败步（§23 / §56.5）。

首次 timer 实战（2026-09-02，v0.48.12）：自动部署的 launchd 会话里 crontab 报
`tmp/tmp.<pid>: Operation not permitted`（TCC——此前两次成功部署都发生在 owner
交互会话拉起的环境里，没暴露），install.sh 记 cron=fail → 退出 1 → 回滚 →
回滚重装撞同一堵墙 → rollback_failed + failed_sha 中毒，所有后续部署停摆——
而代码回滚治不了环境问题。钉住的行为：

  - crontab 以 "Operation not permitted" 失败 → `cron=skipped_tcc`（§23 新值，
    add-only），不进 failed_deploy_steps（--non-interactive 退出码不计它）；
    warn 行带 Full Disk Access 指引，手动 crontab -e 的两行照常打印；
  - 其余 crontab 失败（语法错等）仍是 `cron=fail`，照旧算部署失败步；
  - 成功照旧 `cron=ok`；行没变化时 crontab 根本不被调用（`already installed`）；
  - doctor `cron write access` 行（§25 `cron_tcc_blocked`）：install_report 的
    cron=skipped_tcc → WARN，fix 点名给守护 python 开 FDA 且「终端跑通不算数」；
    cron=ok / 无报告 → 无此行（crontab 行内容 pattern 匹配旧行照样绿，这行是
    唯一窗口）。

真跑 install.sh 的函数原文（同 test_auto_deploy_agent 的 _install_sh_fn 手法），
stub crontab 前置 PATH；不碰真 crontab、不出网。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from act import doctor
from act.lib import config

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")

FAKE_CRONTAB = r"""#!/bin/bash
# stub crontab: log argv, drain stdin, act per FAKE_CRONTAB_MODE
printf 'crontab %s\n' "$*" >> "$FAKE_CRONTAB_LOG"
cat >/dev/null
case "${FAKE_CRONTAB_MODE:-ok}" in
    eperm)  echo "crontab: tmp/tmp.$$: Operation not permitted" >&2; exit 1 ;;
    syntax) echo "crontab: syntax error in crontab file" >&2; exit 1 ;;
    *)      exit 0 ;;
esac
"""

HEALTHY_CRON = (
    "*/30 * * * * cd /r && ./ingest/screenpipe-export.sh\n"
    "7 9 * * * cd /r && python3 -m act.digest >> /r/state/digest.log 2>&1\n")


def _install_sh_fn(name):
    """install.sh 里 `name() {` … 行首 `}` 的原文（同 test_auto_deploy_agent）。"""
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class ApplyCrontabTestCase(unittest.TestCase):
    """真跑 install.sh 的 apply_crontab + failed_deploy_steps，对着 stub crontab。"""

    INGEST = "*/30 * * * * fixture ingest chain"
    DIGEST = "7 9 * * * fixture digest line"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cron-tcc-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stub = self.bin / "crontab"
        stub.write_text(FAKE_CRONTAB, encoding="utf-8")
        stub.chmod(0o755)
        self.cron_log = self.tmp / "crontab.log"

    def run_apply(self, mode="ok", new="NEW LINE", current=""):
        script = ("set -uo pipefail\n"
                  + _install_sh_fn("report_step")
                  + _install_sh_fn("failed_deploy_steps")
                  + _install_sh_fn("apply_crontab")
                  + 'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                    'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                    'info() { printf "  [info] %s\\n" "$1"; }\n'
                    'REPORT_STEPS=""\n'
                    'INGEST_CHAIN="$1"; DIGEST_LINE="$2"; CRON_PY="$3"\n'
                    'NEW_CRON="$4"; CURRENT_CRON="$5"\n'
                    'apply_crontab\n'
                    'printf "===REPORT===\\n%s" "$REPORT_STEPS"\n'
                    'printf "===FAILED===\\n"\n'
                    'failed_deploy_steps\n')
        env = {**os.environ,
               "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
               "FAKE_CRONTAB_MODE": mode,
               "FAKE_CRONTAB_LOG": str(self.cron_log)}
        proc = subprocess.run(
            ["bash", "-c", script, "bash",
             self.INGEST, self.DIGEST, "/pinned/python3", new, current],
            capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out, _, rest = proc.stdout.partition("===REPORT===\n")
        report, _, failed = rest.partition("===FAILED===\n")
        return (out, report.splitlines(),
                [ln for ln in failed.splitlines() if ln], proc.stderr)

    def crontab_calls(self):
        return self.cron_log.read_text(encoding="utf-8").splitlines() \
            if self.cron_log.exists() else []

    def test_eperm_records_skipped_tcc_and_is_not_a_deploy_failure(self):
        out, report, failed, stderr = self.run_apply(mode="eperm")
        self.assertTrue(any(ln.startswith("cron=skipped_tcc:") for ln in report), report)
        self.assertIn("Operation not permitted", "".join(report))
        self.assertEqual(failed, [], "TCC 拒写不是部署失败步——回滚治不了环境问题")
        self.assertIn("Full Disk Access", out)
        self.assertIn("/pinned/python3", out, "the fix names the daemon python")
        # the manual fallback still prints both lines for crontab -e
        self.assertIn(self.INGEST, out)
        self.assertIn(self.DIGEST, out)
        # the raw crontab error is re-surfaced (it lands in the auto-deploy log)
        self.assertIn("Operation not permitted", stderr)

    def test_other_crontab_failure_stays_fatal(self):
        out, report, failed, _ = self.run_apply(mode="syntax")
        self.assertIn("cron=fail:crontab rewrite failed", report)
        self.assertEqual(failed, ["cron=fail:crontab rewrite failed"],
                         "a real crontab failure still fails the deploy")
        self.assertNotIn("Full Disk Access", out)

    def test_success_stays_ok(self):
        _, report, failed, _ = self.run_apply(mode="ok")
        self.assertTrue(any(ln.startswith("cron=ok:") for ln in report), report)
        self.assertEqual(failed, [])

    def test_unchanged_crontab_is_never_invoked(self):
        _, report, failed, _ = self.run_apply(new="SAME", current="SAME")
        self.assertIn("cron=ok:already installed", report)
        self.assertEqual(failed, [])
        self.assertEqual(self.crontab_calls(), [], "no rewrite needed, no crontab call")

    def test_rewrite_is_a_single_plain_crontab_call(self):
        # stderr 直接抓进变量：不落 /tmp 临时文件（固定名 + 共享目录 = symlink
        # 覆写面），也不摆弄 TMPDIR（crontab 的 tmp/tmp.<pid> 是 spool 相对路径）。
        _, report, _, _ = self.run_apply(mode="eperm")
        self.assertEqual(self.crontab_calls(), ["crontab -"])
        self.assertIn("cron=skipped_tcc:crontab rewrite refused", "\n".join(report))


class DoctorCronWriteAccessTestCase(unittest.TestCase):
    """§25 `cron_tcc_blocked`：install_report 的 cron=skipped_tcc → WARN 行。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.report = config.STATE_DIR / "install_report.json"
        self.runtime = config.HOME / "config" / "runtime.json"
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: self.report.unlink(missing_ok=True))
        self.addCleanup(lambda: self.runtime.unlink(missing_ok=True))
        self.runtime.write_text('{"python": "/pinned/python3"}', encoding="utf-8")
        self.probes = doctor.Probes(crontab=lambda: HEALTHY_CRON)

    def _write_report(self, cron_status):
        self.report.write_text(json.dumps({
            "version": "0.48.12", "mode": "non-interactive",
            "steps": [{"name": "cron", "status": cron_status,
                       "detail": "crontab rewrite refused"}],
        }), encoding="utf-8")

    def _row(self):
        rows = doctor._check_cron(self.probes)
        return next((r for r in rows if r.name == "cron write access"), None)

    def test_skipped_tcc_surfaces_as_warn_with_the_fda_remediation(self):
        self._write_report("skipped_tcc")
        row = self._row()
        self.assertIsNotNone(row, "cron=skipped_tcc must produce the row — the "
                             "crontab content checks match stale lines and stay green")
        self.assertEqual(row.status, doctor.WARN)
        self.assertEqual(row.failure_id, "cron_tcc_blocked")
        self.assertIn("/pinned/python3", row.fix, "the fix names the daemon python")
        self.assertIn("prove", row.fix, "terminal-kickstarted runs prove nothing")

    def test_ok_report_produces_no_row(self):
        self._write_report("ok")
        self.assertIsNone(self._row())

    def test_missing_or_torn_report_produces_no_row(self):
        self.report.unlink(missing_ok=True)
        self.assertIsNone(self._row())
        self.report.write_text("not json", encoding="utf-8")
        self.assertIsNone(self._row(), "unreadable report never crashes the pass (宪法 11)")


if __name__ == "__main__":
    unittest.main()
