"""§34bis 提案积压清理按钮（proposals backlog triage preset）判例。

判例②：Python 端固定 prompt（plan）构造/常量存在且含关键指令 —— 只读
registry、不写 inbox、三组建议清单、交互确认。
判例③：清理决定落地走「建议报告」档（advisory report）—— preset 卡强制
chat 交付（§34 direct-run 铁律），plan 进 build_prompt 的可信 ## Plan 区
（sources 围栏是 untrusted DATA），会话产出只有 FINAL DRAFT 清单，一切
丢弃/合并由用户在看板上执行。
另钉：preset 词表 fail-safe（缺 mode:"run" / 垃圾 preset 值 = 完全忽略），
以及 Swift 侧字面量与 Python 侧逐字一致（§10bis 两侧常量先例）。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import unittest
import uuid
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, registry
from act.lib.registry import State

# Swift 侧真源文件（symlink 进 LogicTests 的同一份） —— 两侧逐字常量的
# Python 端执法点；Swift 端判例在 mac/LogicTests 的 ProposalsTriageTests。
_SWIFT_FILE = Path(__file__).resolve().parents[1] / "mac" / "Sources" / "ProposalsTriage.swift"

# Mac 按钮实际发出的载荷（形状 = ProposalsTriage.payload，Swift 判例①钉过）
_BUTTON_TEXT = "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议"


def _drop(body: dict) -> str:
    config.ensure_state_dirs()
    aid = str(uuid.uuid4())
    (config.INBOX_DIR / f"{aid}.json").write_text(
        json.dumps(body), encoding="utf-8")
    return aid


def _button_payload(**overrides) -> dict:
    body = {"action": "capture", "text": _BUTTON_TEXT, "mode": "run",
            "preset": "proposals_triage", "ts": "2026-08-07T00:00:00Z"}
    body.update(overrides)
    return body


class TriageBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _only_card(self):
        cards = registry.load_all()
        self.assertEqual(len(cards), 1, [c.id for c in cards])
        return cards[0]


class PlanConstantTests(TriageBase):
    """判例②：固定 plan 存在且含关键指令。"""

    def test_plan_contains_key_instructions(self):
        plan = actd._proposals_triage_plan()
        self.assertIsInstance(plan, list)
        self.assertTrue(plan)
        blob = "\n".join(plan)
        # registry 真实路径（会话在 workbench cwd 跑，必须拿到绝对路径）
        self.assertIn(str(config.REGISTRY_DIR), blob)
        # 只读红线：不改 registry、不写 inbox（用户指令通道）
        self.assertIn("只读", blob)
        self.assertIn("state/inbox", blob)
        # 审阅口径：提案态三状态 + 三选一判断
        for kw in ("detected", "card_sent", "raising",
                   "仍值得做", "已过时", "重复"):
            self.assertIn(kw, blob)
        # 交付物：三组建议清单 + FINAL DRAFT（chat 收割钩子）
        for kw in ("保留", "建议丢弃", "建议合并", "FINAL DRAFT"):
            self.assertIn(kw, blob)
        # 交互：拿不准的卡要与用户确认
        self.assertIn("问用户", blob)

    def test_preset_key_matches_swift_verbatim(self):
        # §10bis 先例：跨端逐字常量各自钉同一字面量 + 读对面真源交叉执法
        self.assertEqual(actd.PROPOSALS_TRIAGE_PRESET, "proposals_triage")
        swift = _SWIFT_FILE.read_text(encoding="utf-8")
        self.assertIn('presetKey = "proposals_triage"', swift)
        self.assertIn(f'captureText = "{_BUTTON_TEXT}"', swift)


class PresetCaptureFlowTests(TriageBase):
    """按钮载荷 → direct-run 卡 + 固定 plan + chat 交付（判例③落地档）。"""

    def test_button_payload_files_approved_card_with_plan(self):
        _drop(_button_payload())
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)
        self.assertEqual(card.delivery_mode, "chat")   # §34 direct-run 铁律
        self.assertIsNone(card.target_repo)
        self.assertEqual(card.plan, actd._proposals_triage_plan())
        self.assertEqual(card.title, _BUTTON_TEXT[:80])

    def test_plan_lands_in_trusted_prompt_block(self):
        # 判例③：指令必须在 ## Plan（可信区）——写进 sources 围栏会被
        # agent 按律当 DATA 忽略，按钮就成了空按钮。
        _drop(_button_payload())
        actd.process_inbox()
        card = self._only_card()
        prompt = executor.build_prompt(card)
        plan_at = prompt.index("## Plan")
        fence_at = prompt.index("## Sources")
        redline_at = prompt.index("绝不修改/移动/删除 registry")
        self.assertLess(plan_at, redline_at)
        self.assertLess(redline_at, fence_at)

    def test_double_click_never_files_a_twin(self):
        # 连点兜底（Swift 冷却之外的后端保证）：第二发命中自己已 approved
        # 的清理卡 → 只并 sources，不出第二张卡（§34 处置表，plan 非增量）。
        _drop(_button_payload())
        actd.process_inbox()
        _drop(_button_payload(ts="2026-08-07T00:00:05Z"))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)


class PresetFailSafeTests(TriageBase):
    """垃圾 preset / 缺 run = 完全忽略 preset，绝不静默替换任务内容。"""

    def test_preset_without_run_mode_stays_on_proposal_path(self):
        _drop(_button_payload(mode=None))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.RAISING.value)
        self.assertFalse(card.plan)   # 固定 plan 不注入

    def test_unknown_preset_is_plain_direct_run(self):
        _drop(_button_payload(preset="garbage_preset"))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)
        self.assertFalse(card.plan)

    def test_non_string_preset_is_plain_direct_run(self):
        _drop(_button_payload(preset=42))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)
        self.assertFalse(card.plan)


if __name__ == "__main__":
    unittest.main()
