"""§75 安全边界的输入消毒：算不出来的路径一律**当作边界之外**（fail-closed）。

`act/lib/worktrees.py` 的两道闸都建在 `_real`（realpath + expanduser）之上：
`under(root, path)` 决定「这条路径归本模块管吗」，`live_paths` 决定「哪些 worktree
还有人能 resume 回去」。两处的输入都来自盘上与卡上的字符串——空值、`~` 形、以及
realpath 自己算不出来的那种（Windows 的 `_getfullpathname` 会抛，POSIX 的 realpath
把 OSError / ValueError 咽在 `islink` 里），判不出来的时候**只有一个安全答案**：
不在托管根之内（于是一条都不删）。

`live_paths` 这一半钉的是另一种缺失：一张在飞的卡可能根本没有 `session_id`
（派发失败、或 execution 是手改的）——那时不去问 transcript，只认卡上写着的 `cwd`。
"""
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import worktrees
from act.lib.registry import Requirement, State


class UnresolvablePathTestCase(unittest.TestCase):
    def test_a_path_realpath_cannot_resolve_is_never_inside_the_managed_root(self):
        root = worktrees.managed_root("/repo")
        self.assertTrue(worktrees.under(root, root + "/wf-1"))
        for boom in (OSError("ELOOP"), ValueError("embedded null byte")):
            with self.subTest(boom=type(boom).__name__), \
                    mock.patch.object(os.path, "realpath", side_effect=boom):
                self.assertFalse(worktrees.under(root, root + "/wf-1"))

    def test_an_empty_path_is_outside_too(self):
        root = worktrees.managed_root("/repo")
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertFalse(worktrees.under(root, value))


class LivePathsTestCase(unittest.TestCase):
    def _card(self, **execution) -> Requirement:
        return Requirement(id="R-8500", title="在飞的卡", status=State.EXECUTING.value,
                           execution=dict(execution))

    def test_a_card_without_a_session_id_still_protects_its_recorded_cwd(self):
        asked = []
        got = worktrees.live_paths([self._card(cwd="/tmp")], resolve=asked.append)
        self.assertEqual(got, {os.path.realpath("/tmp")})
        self.assertEqual(asked, [])          # 没有 sid = 不去问 transcript

    def test_a_session_id_adds_the_transcript_cwd(self):
        got = worktrees.live_paths([self._card(session_id="sid-1", cwd="/tmp")],
                                   resolve=lambda _sid: Path("/var"))
        self.assertEqual(got, {os.path.realpath("/tmp"), os.path.realpath("/var")})

    def test_a_settled_card_protects_nothing(self):
        req = self._card(cwd="/tmp")
        req.status = State.DELIVERED.value
        self.assertEqual(worktrees.live_paths([req], resolve=lambda _sid: None), set())


if __name__ == "__main__":
    unittest.main()
