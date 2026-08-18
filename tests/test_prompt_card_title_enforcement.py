"""§37.1 CARD TITLE 条件强制 — dispatch prompt 分三档（v0.46 两档 + v0.47 第三档）。

判例（钉死的行为）：
- titles.is_unreadable_title：URL / 文件系统路径 / 超长截断文本为 True；
  正常中英文短标题、非 str、空白为 False（fail 向第三档，绝不硬性首轮命名）。
- 冻结 title 是 URL 且无 display_title 的卡 → dispatch prompt 携带强制命名
  文案（required this round / 必须），无「原样重复」豁免。
- 含空格的文件系统路径（"/Users/z/My Files/a.pdf"）也算不可读——判定侧
  放宽，不动 sanitize_title 的 _PATH_RE（显示 fallback 行为不变）。
- direct-run 卡（notes **首行**以 [direct-run] 创建标签开头）→ 首轮命名
  指示（「请在第一轮交付就给出 CARD TITLE」）。提升追加的 tag 行、fold
  嵌入的用户原文里出现字面 [direct-run] 都**不**触发——notes 面包屑是
  prose，只有 actd 铸卡写的首行标签算信号。
- v0.47 第三档：其余非 user_titled 卡（title 可读 / 已有 display_title）
  → 每轮**必须重新审视**显示名：prompt 注入当前显示名现值（display_title
  → sanitize_title(title) → title 链），名字过时必须给 CARD TITLE 行，
  仍准确原样重复亦可（旧自愿制「名字仍然贴切就省略」文案退役）。
- user_titled=true 的卡 → 收尾指令**完全不提** CARD TITLE（用户钦定 LLM
  永不覆盖，连请求都不发）。
- rework gate line 同一分档（三档齐全，判定收敛在 executor._card_title_tier）：
  user_titled 无 CARD TITLE 请求；无 display_title 且冻结 title 不可读 /
  direct-run → 强制给行、无「原样重复」豁免（首轮没给 CARD TITLE 被打回的卡
  落此档）；其余注入现值 + 必须重新审视、原样重复亦可。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor
from act.lib import config, registry, titles
from act.lib.registry import Requirement, State

_MANDATORY = "required this round"
_RECHECK = "re-check required"
_REPEAT = "原样重复该行亦可"
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

    # ---- v0.46 两档：无 display_title 且不可读 / direct-run（回归不破） ----

    def test_url_title_forces_card_title(self):
        prompt = self._prompt(Requirement(
            id="R-100", title="https://www.youtube.com/watch?v=abc123"))
        self.assertIn(_MANDATORY, prompt)
        self.assertIn("必须", prompt)
        self.assertNotIn(_RECHECK, prompt)
        self.assertNotIn(_REPEAT, prompt)   # 强制档没有「原样重复」豁免

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

    def test_direct_run_first_round_instruction(self):
        # §34 direct-run 卡：title 即便短到可读，首轮也强制命名（起点没过 LLM）
        prompt = self._prompt(Requirement(
            id="R-104", title="帮我看下这个报错",
            notes="[direct-run] 用户直接开跑"))
        self.assertIn(_MANDATORY, prompt)
        self.assertIn(_FIRST_ROUND, prompt)
        self.assertNotIn(_RECHECK, prompt)

    # ---- v0.47 第三档：其余非 user_titled 卡每轮必须重新审视 ----

    def test_readable_title_gets_recheck_tier(self):
        prompt = self._prompt(Requirement(id="R-103", title="修复登录 bug"))
        self.assertIn(_RECHECK, prompt)
        self.assertIn(_REPEAT, prompt)
        # 现值注入：无 display_title 时走 sanitize_title(title) → title 链
        self.assertIn("「修复登录 bug」", prompt)
        self.assertNotIn(_MANDATORY, prompt)
        self.assertNotIn(_FIRST_ROUND, prompt)

    def test_promotion_appended_tag_is_not_direct_run(self):
        # radar 卡被 direct-run 命中提升：actd 只**追加** tag 行（actd.py 的
        # saved.notes + "\n" + tag），首行还是原 prose → 不触发首轮强制
        prompt = self._prompt(Requirement(
            id="R-108", title="整理周报要点",
            notes="from radar: slack #ai-team\n"
                  "[direct-run] 交付改为 chat（跳过预览，不动 repo）"))
        self.assertIn(_RECHECK, prompt)
        self.assertNotIn(_MANDATORY, prompt)
        self.assertNotIn(_FIRST_ROUND, prompt)

    def test_fold_embedded_literal_tag_is_not_direct_run(self):
        # fold note 逐字嵌入用户原文——原文含字面 [direct-run] 不算信号
        prompt = self._prompt(Requirement(
            id="R-109", title="排查提示词问题",
            notes="fold: 用户说「帮我查下 [direct-run] 标签为什么没生效」"))
        self.assertIn(_RECHECK, prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_non_str_notes_does_not_crash(self):
        # 手写卡 notes: 123（YAML 解析成 int）——str() 防御，不许崩 dispatch
        prompt = self._prompt(Requirement(
            id="R-110", title="修复登录 bug", notes=123))
        self.assertIn(_RECHECK, prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_existing_display_title_gets_recheck_with_current_value(self):
        # 卡已有可读显示名（LLM/上一轮 harvest）→ 第三档，注入的现值是存量名
        prompt = self._prompt(Requirement(
            id="R-105", title="https://example.com/a/b",
            display_title="整理供应商对比表"))
        self.assertIn(_RECHECK, prompt)
        self.assertIn("「整理供应商对比表」", prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_direct_run_with_display_title_gets_recheck(self):
        # direct-run 命中提升的旧卡可能带上一轮的 display_title → 第三档
        prompt = self._prompt(Requirement(
            id="R-106", title="再跑一遍那个脚本",
            display_title="重跑数据清洗脚本",
            notes="[direct-run] 用户直接开跑"))
        self.assertIn(_RECHECK, prompt)
        self.assertNotIn(_MANDATORY, prompt)

    def test_unreadable_title_without_display_falls_back_sanitized(self):
        # 第三档现值链：URL title 无存量名时注入 sanitize_title 的可读投影
        prompt = self._prompt(Requirement(
            id="R-113", title="整理 EB-1A 推荐信材料清单"))
        self.assertIn("「整理 EB-1A 推荐信材料清单」", prompt)

    # ---- v0.47 第一档：user_titled 钦定卡收尾指令完全不提 CARD TITLE ----

    def test_user_titled_card_gets_no_card_title_request(self):
        prompt = self._prompt(Requirement(
            id="R-111", title="修复登录 bug",
            display_title="用户钉的名字", user_titled=True))
        self.assertNotIn("CARD TITLE", prompt)

    def test_user_titled_beats_unreadable_forced_tier(self):
        # 钦定优先于一切档位：即便冻结 title 不可读也不发请求
        prompt = self._prompt(Requirement(
            id="R-112", title="https://example.com/a/b/c",
            display_title="用户钉的名字", user_titled=True))
        self.assertNotIn("CARD TITLE", prompt)


class ReworkGateTitleTierTestCase(unittest.TestCase):
    """rework 打回 prompt 的 CARD TITLE 分档 — 与 dispatch 同一逻辑（v0.47）。"""

    FULL_SID = "feedc0de-0000-4000-8000-000000000001"

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.wt = Path(tempfile.mkdtemp(prefix="rework-title-")) / "worktree"
        patcher = mock.patch.object(executor, "_agent_info", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rework_prompt(self, **fields) -> str:
        fields.setdefault("title", "打回测试")
        req = Requirement(id="R-970",
                          status=State.REVIEW.value,
                          execution={"session_id": "feedc0de", "done": True},
                          **fields)
        registry.save(req)
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["claude"], 0, stdout="backgrounded · feedc0de", stderr=""))
        with mock.patch.object(
                executor, "_transcript_info",
                side_effect=lambda sid: (self.FULL_SID, self.wt)
                if str(sid).startswith("feedc0de") else None):
            self.assertTrue(executor.rework(req, "再补一个测试", self.cfg,
                                            runner=runner))
        return runner.call_args[0][0]

    def test_rework_prompt_requires_title_recheck_with_current_value(self):
        prompt = self._rework_prompt(display_title="重跑数据清洗脚本")
        self.assertIn("必须重新审视卡片显示名", prompt)
        self.assertIn("「重跑数据清洗脚本」", prompt)
        self.assertIn(_REPEAT, prompt)

    def test_rework_prompt_user_titled_never_asks(self):
        prompt = self._rework_prompt(display_title="用户钉的名字",
                                     user_titled=True)
        self.assertNotIn("CARD TITLE", prompt)

    # ---- 强制档：首轮交付没给 CARD TITLE 行、harvest 落空后被打回的卡 ----

    def test_rework_prompt_unreadable_title_forces_no_repeat_exemption(self):
        prompt = self._rework_prompt(
            title="https://www.youtube.com/watch?v=abc123")
        self.assertIn("必须", prompt)
        self.assertIn("CARD TITLE", prompt)
        self.assertNotIn(_REPEAT, prompt)          # 强制档无「原样重复」豁免
        self.assertNotIn("重新审视卡片显示名", prompt)

    def test_rework_prompt_direct_run_forces_no_repeat_exemption(self):
        prompt = self._rework_prompt(
            title="帮我看下这个报错", notes="[direct-run] 用户直接开跑")
        self.assertIn("CARD TITLE", prompt)
        self.assertNotIn(_REPEAT, prompt)

    def test_rework_prompt_forced_tier_yields_to_existing_display_title(self):
        # 上一轮已 harvest 到显示名的不可读卡 → 回到第三档（与 dispatch 同判例）
        prompt = self._rework_prompt(
            title="https://example.com/a/b", display_title="整理供应商对比表")
        self.assertIn("必须重新审视卡片显示名", prompt)
        self.assertIn("「整理供应商对比表」", prompt)
        self.assertIn(_REPEAT, prompt)


class SameValueNoOpTestCase(unittest.TestCase):
    """v0.47 幂等保护 — set_display_title 对 same-value 是 no-op（唯一落笔点）。

    session 每轮原样重复同名会经 harvest→set_display_title 走一遍：不得污染
    former_titles、不得报 changed（actd 只在 True 时打「refreshed」日志）。"""

    def test_same_value_noop_keeps_former_titles(self):
        req = Requirement(id="R-980", title="t")
        registry.set_display_title(req, "名字A")
        registry.set_display_title(req, "名字B")
        self.assertEqual(req.former_titles, ["名字A"])
        # 原样重复：no-op，曾用名不追加、显示名不变
        self.assertFalse(registry.set_display_title(req, "名字B"))
        self.assertEqual(req.former_titles, ["名字A"])
        self.assertEqual(req.display_title, "名字B")

    def test_same_value_after_whitespace_collapse_is_noop(self):
        req = Requirement(id="R-981", title="t")
        registry.set_display_title(req, "重跑 数据 脚本")
        # clip_title 折叠空白后同值 → 仍是 no-op
        self.assertFalse(registry.set_display_title(req, "重跑  数据\n脚本"))
        self.assertIsNone(req.former_titles)

    def test_different_value_still_appends_with_cap(self):
        req = Requirement(id="R-982", title="t")
        for name in ["名一", "名二", "名三", "名四", "名五"]:
            self.assertTrue(registry.set_display_title(req, name))
        # 异名更新照常追加曾用名，cap 3、最新在后
        self.assertEqual(req.former_titles, ["名二", "名三", "名四"])
        self.assertEqual(req.display_title, "名五")


if __name__ == "__main__":
    unittest.main()
