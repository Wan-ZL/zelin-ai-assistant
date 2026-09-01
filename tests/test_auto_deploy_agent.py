"""§56 自动部署 agent 的安装形状判例：plist 模板 + python 启动器 + install.sh 闸门。

- com.zelin.aiassistant.autodeploy.plist：StartInterval 600、RunAtLoad false、
  SoftResourceLimits.NumberOfFiles 与其余模板同款 8192（§55 资源上限）、
  ProgramArguments = 渲染注入的解释器 -m act.auto_deploy（§55：argv0 必须是那个
  launchd 可行的 python，doctor 的 `launchd python` 探针靳它 import yaml）；通用
  路径纪律由 tests/test_launchd_render.py 对全部模板统一钉。
- act/auto_deploy.py：把自己的解释器经 AIASSISTANT_PYTHON 交给脚本；脚本缺失退 1。
  注入缝 run=（绝不真起脚本）。
- install.sh autodeploy_wanted / failed_deploy_steps：抠出原文真跑（同
  test_launchd_render 的 install_sh_prelude 手法）——非 git 目录不装；
  features.auto_deploy: false 不装；探针崩了 fail-open；app 步骤失败不计入退出码。
- install.sh install_mac_app（§56.5）：--non-interactive **永不**跑
  `mac/build.sh --install`——build.sh 会 quit + relaunch 正在跑的 app，screenpipe
  是它的直接子进程、实时字幕住在它里面，无人值守的重建等于在任意时刻掐断录制；
  交互模式照常构建。假 mac/build.sh 记录调用。
"""
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from act import auto_deploy

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "act" / "launchd" / "com.zelin.aiassistant.autodeploy.plist"
_WIN = sys.platform.startswith("win")


class AutodeployPlistShapeTestCase(unittest.TestCase):
    def setUp(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        bare = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        self.obj = plistlib.loads(bare.encode("utf-8"))

    def test_label_and_launcher(self):
        self.assertEqual(self.obj["Label"], "com.zelin.aiassistant.autodeploy")
        argv = self.obj["ProgramArguments"]
        self.assertEqual(argv[1:], ["-m", "act.auto_deploy"])
        self.assertIn("python", argv[0].rsplit("/", 1)[-1],
                      "argv0 must be the rendered daemon interpreter (§55), never bash")

    def test_periodic_not_resident(self):
        self.assertEqual(self.obj["StartInterval"], 600)
        self.assertIs(self.obj["RunAtLoad"], False,
                      "a manual install.sh must not trigger a deploy pass on load")
        self.assertNotIn("KeepAlive", self.obj)

    def test_fd_soft_limit_matches_the_other_templates(self):
        # §55 资源上限：每个模板 Soft 8192、不带 Hard（tests/test_launchd_render.py
        # 钉全部模板 >= 4096；这里钉「与兄弟模板同款」，别让一个模板另立标准）
        self.assertEqual(self.obj["SoftResourceLimits"]["NumberOfFiles"], 8192)
        self.assertNotIn("HardResourceLimits", self.obj)


class LauncherTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autodeploy-launcher-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = []
        self._orig = auto_deploy.SCRIPT

    def tearDown(self):
        auto_deploy.SCRIPT = self._orig

    def _run(self, cmd, env=None):
        self.calls.append((cmd, env))
        return 0

    def test_runs_the_script_under_its_own_interpreter(self):
        script = self.tmp / "auto-deploy.sh"
        script.write_text("#!/bin/bash\n", encoding="utf-8")
        auto_deploy.SCRIPT = script
        rc = auto_deploy.main(["--force"], run=self._run)
        self.assertEqual(rc, 0)
        (cmd, env), = self.calls
        self.assertEqual(cmd, ["/bin/bash", str(script), "--force"])
        self.assertEqual(env["AIASSISTANT_PYTHON"], sys.executable)
        self.assertIn("AIASSISTANT_HOME", env)

    def test_missing_script_exits_1_without_running_anything(self):
        auto_deploy.SCRIPT = self.tmp / "nope.sh"
        self.assertEqual(auto_deploy.main([], run=self._run), 1)
        self.assertEqual(self.calls, [])

    def test_script_exit_code_is_passed_through(self):
        script = self.tmp / "auto-deploy.sh"
        script.write_text("#!/bin/bash\n", encoding="utf-8")
        auto_deploy.SCRIPT = script
        self.assertEqual(auto_deploy.main([], run=lambda cmd, env=None: 3), 3)


def _install_sh_fn(name):
    """install.sh 里 `name() {` … 行首 `}` 的原文（同 test_launchd_render）。"""
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class InstallGateTestCase(unittest.TestCase):
    """真跑 install.sh 的 autodeploy_wanted（闸门）与 failed_deploy_steps（退出码）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autodeploy-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _git_repo(self, config_yaml=None):
        root = self.tmp / "repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30)
        if config_yaml is not None:
            (root / "config.yaml").write_text(config_yaml, encoding="utf-8")
        return root

    def _wanted(self, root, runtime_py=sys.executable, pythonpath=str(REPO)):
        script = ("set -u\n" + _install_sh_fn("autodeploy_wanted")
                  + 'REPO_ROOT="$1"; RUNTIME_PY="$2"\n'
                  'if autodeploy_wanted; then printf wanted; else printf unwanted; fi\n')
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(root), runtime_py],
            capture_output=True, text=True, timeout=60,
            # the probe imports the REAL act.lib.config via PYTHONPATH while
            # AIASSISTANT_HOME (set inside the function) is the fixture repo
            env={**os.environ, "PYTHONPATH": pythonpath})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_plain_directory_without_git_is_not_wanted(self):
        plain = self.tmp / "pkgcopy"
        plain.mkdir()
        self.assertEqual(self._wanted(plain), "unwanted",
                         "a .pkg copy has no .git — nothing to fast-forward")

    def test_git_checkout_is_wanted_by_default(self):
        self.assertEqual(self._wanted(self._git_repo()), "wanted")

    def test_feature_flag_off_is_not_wanted(self):
        root = self._git_repo("features:\n  auto_deploy: false\n")
        self.assertEqual(self._wanted(root), "unwanted")

    def test_probe_crash_fails_open(self):
        # an interpreter that cannot import act at all (empty PYTHONPATH) → exit 1
        # python crash ≠ the dedicated exit 3 "off" → installs as before
        root = self._git_repo("features:\n  auto_deploy: false\n")
        self.assertEqual(self._wanted(root, pythonpath=str(self.tmp)), "wanted")

    def _failed(self, steps):
        script = ("set -u\n" + _install_sh_fn("failed_deploy_steps")
                  + 'REPORT_STEPS="$1"\nfailed_deploy_steps\n')
        proc = subprocess.run(["bash", "-c", script, "bash", steps],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [ln for ln in proc.stdout.splitlines() if ln]

    def test_exit_code_counts_failed_steps_except_the_legacy_app(self):
        steps = ("config=ok:kept\nruntime_python=fail:no yaml\napp=fail:mac/build.sh --install failed\n"
                 "launchd=fail:2 agent(s) failed to load\ncron=ok\n")
        self.assertEqual(self._failed(steps),
                         ["runtime_python=fail:no yaml", "launchd=fail:2 agent(s) failed to load"])

    def test_all_ok_or_only_app_failed_is_clean(self):
        self.assertEqual(self._failed("config=ok\napp=fail:x\nlaunchd=ok:4 agents loaded\n"), [])
        self.assertEqual(self._failed(""), [])


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class InstallMacAppStepTestCase(unittest.TestCase):
    """§56.5：自动部署（--non-interactive）永不重建 Mac app。

    真跑 install.sh 的 install_mac_app，REPO_ROOT 指向一个只有假 mac/build.sh 的
    目录；假脚本把 argv 追加到 calls.log。断言的是**行为**（build.sh 有没有被以
    --install 调起、§23 报告行写了什么），不是 install.sh 的字面。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autodeploy-app-step-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "mac").mkdir()
        self.calls = self.tmp / "calls.log"
        (self.tmp / "mac" / "build.sh").write_text(
            "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\nexit \"${FAKE_BUILD_RC:-0}\"\n",
            encoding="utf-8")

    def _run(self, *, non_interactive, pkg=0, build_rc=0):
        script = ("set -u\n"
                  "ok() { :; }; warn() { :; }; info() { :; }\n"
                  "REPORT_STEPS=''\n"
                  + _install_sh_fn("report_step")
                  + _install_sh_fn("install_mac_app")
                  + 'REPO_ROOT="$1"; NON_INTERACTIVE="$2"; PKG_POSTINSTALL="$3"\n'
                  "install_mac_app >/dev/null\n"
                  "printf '%s' \"$REPORT_STEPS\"\n")
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(self.tmp), str(non_interactive), str(pkg)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "CALLS": str(self.calls), "FAKE_BUILD_RC": str(build_rc)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []
        return calls, proc.stdout.strip()

    def test_non_interactive_never_invokes_build_sh(self):
        calls, report = self._run(non_interactive=1)
        self.assertEqual(calls, [], "auto-deploy must not quit/relaunch the app (kills screenpipe + captions)")
        self.assertTrue(report.startswith("app=skipped:"), report)
        self.assertIn("non-interactive", report)
        self.assertIn("bash install.sh", report, "the report must point at the manual rebuild path")

    def test_non_interactive_skips_even_when_a_build_would_fail(self):
        # 不是「构建失败不回滚」——是压根不构建；报告不得出现 app=fail
        calls, report = self._run(non_interactive=1, build_rc=1)
        self.assertEqual(calls, [])
        self.assertNotIn("app=fail", report)

    def test_interactive_builds_and_installs(self):
        calls, report = self._run(non_interactive=0)
        self.assertEqual(calls, ["--install"])
        self.assertEqual(report, "app=ok:built and installed")

    def test_interactive_build_failure_is_reported_honestly(self):
        calls, report = self._run(non_interactive=0, build_rc=1)
        self.assertEqual(calls, ["--install"])
        self.assertEqual(report, "app=fail:mac/build.sh --install failed")

    def test_pkg_postinstall_still_skips(self):
        calls, report = self._run(non_interactive=0, pkg=1)
        self.assertEqual(calls, [])
        self.assertEqual(report, "app=skipped:installed by the .pkg")


if __name__ == "__main__":
    unittest.main()
