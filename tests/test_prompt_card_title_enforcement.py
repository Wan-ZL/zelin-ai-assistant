"""§37.1 CARD TITLE 条件强制 — dispatch prompt 分两档（v0.46+）。

判例（钉死的行为）：
- titles.is_unreadable_title：URL / 文件系统路径 / 超长截断文本为 True；
  正常中英文短标题、非 str、空白为 False（fail 向自愿制）。
- 冻结 title 是 URL 且无 display_title 的卡 → dispatch prompt 携带强制命名
  文案（required this round / 必须），不再出现「名字仍然贴切就省略」。
- title 可读的卡 → 维持自愿制原文案（byte-level 关键句不变，零回归）。
- 含空格的文件系统路径（"/Users/z/My Files/a.pdf"）也算不可读——判定侧
  放宽，不动 sanitize_title 的 _PATH_RE（显示 fallback 行为不变）。
- direct-run 卡（notes **首行**以 [direct-run] 创建标签开头）→ 首轮命名
  指示（「请在第一轮交付就给出 CARD TITLE」）。提升追加的 tag 行、fold
  嵌入的用户原文里出现字面 [direct-run] 都**不**触发——notes 面包屑是
  prose，只有 actd 铸卡写的首行标签算信号。
- 已有 display_title 的卡（含 direct-run 命中提升的旧卡）→ 回到自愿制：
  卡已经有人类可读的名字，不硬性打扰 agent。
"""
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor
from act.lib import config, titles
from act.lib.registry import Requirement

_MANDATORY = "required this round"
_VOLUNTARY = "名字仍然贴切就省略"
_FIRST_ROUND = "请在第一轮交付就给出 CARD TITLE"


class IsUnreadableTitleTestCase(unittest.TestCase):
    """判定函数本身的正例/反例 — 与 sanitize_title 同一口径。"""

    def test_positive_shapes(self):
        for raw in [
            "https://www.youtube.com/watch?v=abc123",
            "http://example.com/a/b/c",
            "/Users/zelin/Projects/zelin-ai-assistant/act/executor.py",
            "~/Documents/notes/2026-08-07-meeting.md",
            # 含空格路径：_PATH_RE 不认，但判定侧放宽（首字符 / 或 ~ 且 ≥2 个 /）
            "/Users/z/My Files/annual report v3.pdf",
            "~/My Docs/2026 planning/notes.md",
            # direct-run 原话截 80：超过 _LONG_TEXT(60) 即算截断文本
            "把" * 80,
            "please go through the entire ingest pipeline and figure out why "
            "the leftover dumps keep reappearing after every push",
        ]:
            self.assertTrue(titles.is_unreadable_title(raw), msg=repr(raw))

    def test_negative_shapes(self):
        for raw in [
            "修复登录 bug",                      # 正常中文短标题
            "Draft the weekly update",           # 正常英文短标题
            "整理 EB-1A 推荐信材料清单",
            "config.json 解析报错",              # 含点号但不是路径/URL
            "/tmp",                              # 单段：不足两个分隔符
            "对比 A/B 方案 word/excel 两版",      # 含 / 但不以路径开头
            # ~ 是约数不是 home：首段 "~3" 自身无路径结构 → 不算路径
            "~3 天完成 A/B 测试 x/y 对比",
            "",                                  # 空
            "   ",                               # 全空白
            None,                                # 非 str
            42,                                  # 非 str
        ]:
            self.assertFalse(titles.is_unreadable_title(raw), msg=repr(raw))

    def test_whitespace_collapsed_before_judging(self):
        # 换行/多空格折叠后再判长度 — 与 sanitize_title 的折叠口径一致
        self.assertFalse(titles.is_unreadable_title("修 复\n登 录"))


class PromptEnforcementTestCase(unittest.TestCase):
    def _prompt(self, req: Requirement) -> str:
        cfg = config.Config()
        cfg.memory_inject = False   # stay off the real ~/.claude memory
        cfg.voice_enabled = False   # keep the prompt minimal/deterministic
        with tempfile.TemporaryDirectory(prefix="cardtitle-") as td:
            return executor.build_prompt(req, cfg, target=Path(td))

    def test_url_title_forces_card_title(self):
        prompt = self._prompt(Requirement(
            id="R-100", title="https://www.youtube.com/watch?v=abc123"))
        self.assertIn(_MANDATORY, prompt)
        self.assertIn("必须", prompt)
        self.assertNotIn(_VOLUNTARY, prompt)

    def test_path_title_forces_card_title(self):
        prompt = self._prompt(Requirement(
            id="R-101", title="/Users/zelin/Downloads/report-final-v3.pdf"))
        self.assertIn(_MANDATORY, prompt)

    def test_spaced_path_title_forces_card_title(self):
        prompt = self._prompt(Requirement(
            id="R-107", title="/Users/z/My Files/annual report v3.pdf"))
        self.assertIn(_MANDATORY, prompt)

    def test_overlong_title_forces_card_title(self):
        prompt = self._prompt(Requirement(id="R-102", title="查" * 80))
        self.assertIn(_MANDATORY, prompt)

    def test_readable_title_keeps_voluntary_wording(self):
        prompt = self._prompt(Requirement(id="R-103", title="修复登录 bug"))
        self.assertIn(_VOLUNTARY, prompt)
        self.assertNotIn(_MANDATORY, prompt)
        self.assertNotIn(_FIRST_ROUND, prompt)

    def test_direct_run_first_round_instruction(self):
        # §34 direct-run 卡：title 即便短到可读，首轮也强制命名（起点没过 LLM）
        prompt = self._prompt(Requirement(
            id="R-104", title="帮我看下这个报错",
            notes="[direct-run] 用户直接开跑"))
        self.assertIn(_MANDATORY, prompt)
        self.assertIn(_FIRST_ROUND, prompt)
        self.assertNotIn(_VOLUNTARY, prompt)

    def test_promotion_appended_tag_is_not_direct_run(self):
        # radar 卡被 direct-run 命中提升：actd 只**追加** tag 行（actd.py 的
        # saved.notes + "\n" + tag），首行还是原 prose → 不触发首轮强制
        prompt = self._prompt(Requirement(
            id="R-108", title="整理周报要点",
            notes="from radar: slack #ai-team\n"
                  "[direct-run] 交付改为 chat（跳过预览，不动 repo）"))
        self.assertIn(_VOLUNTARY, prompt)
        self.assertNotIn(_MANDATORY, prompt)
        self.assertNotIn(_FIRST_ROUND, prompt)

    def test_fold_embedded_literal_tag_is_not_direct_run(self):
        # fold note 逐字嵌入用户原文——原文含字面 [direct-run] 不算信号
        prompt = self._prompt(Requirement(
            id="R-109", title="排查提示词问题",
            notes="fold: 用户说「帮我查下 [direct-run] 标签为什么没生效」"))
        self.assertIn(_VOLUNTARY, prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_non_str_notes_does_not_crash(self):
        # 手写卡 notes: 123（YAML 解析成 int）——str() 防御，不许崩 dispatch
        prompt = self._prompt(Requirement(
            id="R-110", title="修复登录 bug", notes=123))
        self.assertIn(_VOLUNTARY, prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_existing_display_title_restores_voluntary(self):
        # 卡已有可读显示名（LLM/用户/上一轮 harvest）→ 不再硬性要求
        prompt = self._prompt(Requirement(
            id="R-105", title="https://example.com/a/b",
            display_title="整理供应商对比表"))
        self.assertIn(_VOLUNTARY, prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_direct_run_with_display_title_restores_voluntary(self):
        # direct-run 命中提升的旧卡可能带上一轮的 display_title → 自愿制
        prompt = self._prompt(Requirement(
            id="R-106", title="再跑一遍那个脚本",
            display_title="重跑数据清洗脚本",
            notes="[direct-run] 用户直接开跑"))
        self.assertIn(_VOLUNTARY, prompt)
        self.assertNotIn(_MANDATORY, prompt)


if __name__ == "__main__":
    unittest.main()
