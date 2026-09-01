"""§56 自动部署 agent 的安装形状判例：plist 模板 + python 启动器 + install.sh 闸门。

- com.zelin.aiassistant.autodeploy.plist：StartInterval 600、RunAtLoad false、
  SoftResourceLimits.NumberOfFiles（swift build 在 gui domain 默认 256 fd 下会炸）、
  ProgramArguments = 渲染注入的解释器 -m act.auto_deploy（§55：argv0 必须是那个
  launchd 可行的 python，doctor 的 `launchd python` 探针靳它 import yaml）；通用
  路径纪律由 tests/test_launchd_render.py 对全部模板统一钉。
- act/auto_deploy.py：把自己的解释器经 AIASSISTANT_PYTHON 交给脚本；脚本缺失退 1。
  注入缝 run=（绝不真起脚本）。
- install.sh autodeploy_wanted / failed_deploy_steps：抠出原文真跑（同
  test_launchd_render 的 install_sh_prelude 手法）——非 git 目录不装；
  features.auto_deploy: false 不装；探针崩了 fail-open；app 步骤失败不计入退出码。
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

    def test_fd_limit_raised_for_the_swift_build(self):
        self.assertGreaterEqual(self.obj["SoftResourceLimits"]["NumberOfFiles"], 1024)


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


if __name__ == "__main__":
    unittest.main()
