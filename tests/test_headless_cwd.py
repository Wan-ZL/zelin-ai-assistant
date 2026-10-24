"""headless ``claude -p`` 的中性 cwd（PR80 审查 P1-4；v2.0.0 起是 §78 隔离的结构半边）。

v1 的中性目录是 STATE_DIR——它在 repo 树里，祖先链上有 repo 的 CLAUDE.md 与 .claude/skills，
state/vault-mirror/.claude/settings.json 也在树里（实测含对用户级 ~/.claude 的
permissions.allow）。§78 把 cwd 移出 repo 树：``config.headless_cwd()`` =
``<CLAUDE_CONFIG_DIR>/cwd``，一个空目录（无 CLAUDE.md、无 .claude/），即使 ``--bare``
的语义将来变化，repo 与 vault-mirror 的配置也不在 claude 的祖先链上。

钉住：helper 的落点与「空」；env 覆盖（测试沙箱用它）；repo 树之外；唯一的 headless
call site（act/recap.py）与 doctor 的两个活探针显式带着它。fake 的是 runner / probes，
绝不 spawn 真 claude。Runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import doctor, recap
from act.lib import config, recap_text

REPO = Path(__file__).resolve().parent.parent


class _Recorder:
    def __init__(self, stdout="ok"):
        self.calls = []
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


class HeadlessCwdHelperTestCase(unittest.TestCase):
    def test_helper_is_the_cwd_under_the_claude_config_dir_and_exists(self):
        cwd = Path(config.headless_cwd())
        self.assertEqual(cwd, config.claude_config_dir() / "cwd")
        self.assertTrue(cwd.is_dir())
        # 中性 = 这里绝不能长出会被 claude 自动加载的 CLAUDE.md / 项目设置
        self.assertFalse((cwd / "CLAUDE.md").exists())
        self.assertFalse((cwd / ".claude").exists())

    def test_it_is_outside_the_repo_tree(self):
        cwd = Path(config.headless_cwd()).resolve()
        self.assertNotIn(REPO.resolve(), (cwd, *cwd.parents))
        self.assertNotIn(config.STATE_DIR.resolve(), (cwd, *cwd.parents))

    def test_the_default_location_is_application_support(self):
        self.assertEqual(config.CLAUDE_CONFIG_DIR,
                         Path.home() / "Library" / "Application Support" / "ZelinAIAssistant"
                         / "claude-config")
        self.assertEqual(config.HEADLESS_CWD, config.CLAUDE_CONFIG_DIR / "cwd")

    def test_the_env_override_relocates_both(self):
        other = Path(tempfile.mkdtemp(prefix="zai-cc-")) / "cc"
        with mock.patch.dict(os.environ, {"AIASSISTANT_CLAUDE_CONFIG_DIR": str(other)}):
            self.assertEqual(config.claude_config_dir(), other)
            self.assertEqual(Path(config.headless_cwd()), other / "cwd")
        self.assertTrue((other / "cwd").is_dir())


class HeadlessCwdWiringTestCase(unittest.TestCase):
    """每个 headless call site 的 subprocess kwargs 里都钉着中性 cwd。"""

    def test_the_recap_model_call(self):
        rec = _Recorder()
        with mock.patch("act.lib.config.resolve_claude_bin", return_value="claude"):
            recap._call_model("prompt", rec, config.Config(), recap_text.NO_EGRESS_ARGV, 5)
        self.assertEqual(rec.calls[0][1]["cwd"], config.headless_cwd())

    def test_the_doctor_live_probes(self):
        seen = []

        def run(cmd, env=None, timeout=None, cwd=None):
            seen.append(cwd)
            return 0, "ok"

        probes = doctor.Probes(which=lambda name: "/fake/claude", run=run)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            doctor._check_claude_auth(probes)
        doctor._check_model_liveness(probes)
        self.assertEqual(seen, [config.headless_cwd(), config.headless_cwd()])


if __name__ == "__main__":
    unittest.main()
