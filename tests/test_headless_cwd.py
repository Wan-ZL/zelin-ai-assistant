"""headless ``claude -p`` 的中性 cwd（PR80 审查 P1-4）.

actd/launchd 的 WorkingDirectory 是 repo 根；判官/提取类 `claude -p` 子进程若
继承它，claude 会把根下的 CLAUDE.md（指挥性入职文档）自动注进每一次管线调用
——token/延迟膨胀（radar 的 v0.43.2 慢性超时前科），还把无关指令喂给提取任务。
钉住：这些调用统一显式 ``cwd=config.headless_cwd()``（= STATE_DIR，无 CLAUDE.md、
无项目 memory）。executor 派发的真工作会话不在此列（它们就该在目标 repo 里跑）。

fake 的是 ``subprocess.run`` 本身（记录 kwargs 后返回罐头 CompletedProcess），
绝不 spawn 真 claude。Runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import ask, golden_eval, merge_review, radar, radar_gmail, radar_slack, \
    weekly_digest
from act.lib import config, quick_capture


class _Recorder:
    def __init__(self, stdout="[]"):
        self.calls: list[tuple[list, dict]] = []
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


class HeadlessCwdHelperTestCase(unittest.TestCase):
    def test_helper_is_state_dir_and_exists(self):
        cwd = config.headless_cwd()
        self.assertEqual(cwd, str(config.STATE_DIR))
        self.assertTrue(config.STATE_DIR.is_dir())
        # 中性 = 这里绝不能长出会被 claude 自动加载的 CLAUDE.md
        self.assertFalse((config.STATE_DIR / "CLAUDE.md").exists())


class HeadlessCwdWiringTestCase(unittest.TestCase):
    """每个判官/提取 call site 的 subprocess kwargs 里都钉着中性 cwd."""

    def _assert_neutral_cwd(self, rec: _Recorder):
        self.assertEqual(len(rec.calls), 1)
        argv, kwargs = rec.calls[0]
        self.assertEqual(kwargs.get("cwd"), str(config.STATE_DIR))

    def test_radar_extract(self):
        rec = _Recorder()
        with mock.patch("subprocess.run", rec):
            radar._run_extract("note body")
        self._assert_neutral_cwd(rec)

    def test_triage_extractor(self):
        rec = _Recorder()
        with mock.patch("subprocess.run", rec):
            quick_capture._default_extractor("prompt")
        self._assert_neutral_cwd(rec)

    def test_remaining_judges_and_extractors(self):
        for runner in (radar_gmail._default_extractor,
                       radar_slack._default_extractor,
                       golden_eval._default_extractor,
                       merge_review._default_runner,
                       ask._default_runner,
                       weekly_digest._run_claude):
            with self.subTest(runner=runner.__module__):
                rec = _Recorder()
                with mock.patch("subprocess.run", rec):
                    runner("prompt")
                self._assert_neutral_cwd(rec)


if __name__ == "__main__":
    unittest.main()
