"""dispatch_prompt — dev 列车改动行上的变异幸存体判例（CONTRACT §60.4 / §37.1）。

一条契约：**bg 会话名 = `<工作编号> · <卡片此刻的显示名截 48>`**，48 是硬帽，
空名回落纯编号。`claude` 只在启动期收 `-n/--name`，这个名字会成为 worktree 目录名
的一部分——长度帽不是装饰，差一个字符就是一个不同的目录/分支名，而 `claude agents`
与看板对不上时 owner 无法把会话和卡对起来。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); pure function.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import dispatch_prompt
from act.lib.registry import Requirement

# 位置编码的 60 字名字：截 47 / 48 / 49 三种结果逐字节可分。
MARKER = "".join(str(i % 10) for i in range(60))


class SessionNameClipTestCase(unittest.TestCase):
    def test_the_display_name_is_clipped_at_exactly_48_characters(self):
        req = Requirement(id="R-500", title="冻结标题", display_title=MARKER)
        name = dispatch_prompt.session_name(req)
        self.assertEqual(name, "R-500 · " + MARKER[:48])
        self.assertEqual(len(name.split(" · ", 1)[1]), 48)

    def test_a_name_at_the_cap_is_carried_whole(self):
        req = Requirement(id="R-500", title="冻结标题", display_title=MARKER[:48])
        self.assertEqual(dispatch_prompt.session_name(req), "R-500 · " + MARKER[:48])

    def test_an_empty_name_falls_back_to_the_bare_work_id(self):
        self.assertEqual(dispatch_prompt.session_name(Requirement(id="R-500", title="")),
                         "R-500")


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
