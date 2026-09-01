"""scripts/auto-deploy.sh 行为判例（CONTRACT §56 合并即上岗）——真 bash + 真 git。

住在 tests/integration/（防腐 #7：真 IO 只许住这里，单文件时间预算见
BUDGET_SECONDS）。整套夹具是一个临时 bare origin + 一个 live clone，clone 里的
install.sh / act/doctor.py / act/lib/notify.py 全是**记录调用、按剧本出退出码**
的假货，只有 scripts/auto-deploy.sh 是真的（逐字拷进夹具并提交）。不出网、不起
launchd、不碰真 $HOME（HOME 指到临时目录）。

钉住的行为：
  - HEAD == origin/main → up_to_date，install 不跑；
  - origin/main 前进 + 树干净 → ff、install --non-interactive、doctor 基线/复查、
    deployed（state 文件 + 一条通知）；
  - install 失败 / doctor 出现**新增** FAIL → git reset --hard 回旧 sha + 再装 +
    rolled_back + failed_sha 记账，下一轮对同一 sha 不再重试，--force 才重试；
  - 部署前已红的 doctor 项不归咎新版本（deployed，detail 点名 pre-existing）；
  - 脏树拒绝（不动 HEAD、同一 sha 只通知一次）；不在 main 拒绝；
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
BUDGET_SECONDS = 120
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
mkdir -p "$here/state"
printf '{"steps": [{"name": "app", "status": "%s"}]}\n' "${FAKE_APP_STATUS:-ok}" > "$here/state/install_report.json"
[ -n "${FAKE_INSTALL_SLEEP:-}" ] && sleep "$FAKE_INSTALL_SLEEP"
exit "$rc"
"""

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

    def run_script(self, *args, doctor_plan=None, install_rc=None, env=None):
        if doctor_plan is not None:
            self.doctor_plan.write_text("\n".join(doctor_plan) + "\n", encoding="utf-8")
        elif self.doctor_plan.exists():
            self.doctor_plan.unlink()
        if install_rc is not None:
            self.install_rc_plan.write_text("\n".join(str(r) for r in install_rc) + "\n", encoding="utf-8")
        elif self.install_rc_plan.exists():
            self.install_rc_plan.unlink()
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("AIASSISTANT_", "AUTODEPLOY_", "FAKE_", "GIT_"))}
        full = {
            **base,
            "HOME": str(self.home),
            "AIASSISTANT_PYTHON": sys.executable,
            "AUTODEPLOY_LOG_DIR": str(self.logs),
            "AUTODEPLOY_DOCTOR_SETTLE": "0",
            "AUTODEPLOY_INSTALL_TIMEOUT": "30",
            "FAKE_NOTIFY_LOG": str(self.notify_log),
            "FAKE_INSTALL_LOG": str(self.install_log),
            "FAKE_DOCTOR_LOG": str(self.doctor_log),
            "FAKE_DOCTOR_PLAN": str(self.doctor_plan),
            "FAKE_INSTALL_RC_PLAN": str(self.install_rc_plan),
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
        doc = self.doctor_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(doc), 2, doc)
        self.assertTrue(all("version=0.48.4" in ln and "--fast --json" in ln for ln in doc), doc)

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

    # -- 3. rollback --------------------------------------------------------- #

    def test_new_doctor_failure_rolls_back_and_poisons_that_sha(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard"])  # green baseline, red after install
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
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("rolled back to %s" % self.base_sha[:7], notes[0])
        # next interval: same origin/main sha → no retry storm
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 2, "no third install for the poisoned sha")
        self.assertEqual(self.head(), self.base_sha)
        self.assertIn("already failed deploy", self.log_text())
        self.assertEqual(len(self.notifications()), 1, "silent while waiting")
        # a new commit on main is tried normally
        target2 = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target2)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("failed_sha", self.state())

    def test_force_retries_the_poisoned_sha(self):
        target = self.push("0.48.4")
        self.run_script(doctor_plan=["-", "dashboard"])
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

    def test_mac_app_build_failure_is_reported_not_rolled_back(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_APP_STATUS": "fail"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertIn("mac app build failed", st["detail"])

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
