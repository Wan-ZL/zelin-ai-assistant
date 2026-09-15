"""§76.2 / §76.3 提案结算信号：投影真值表 + 三条一次性升级通知。

issue #313 的第二、三条诉求。钉住的契约：

- ``decision_due``：``days_left <= 0`` 为真；未来 / 无 / 坏 deadline 为假
  （拿不准不催人）。它是投影，不是状态——§70.2 的静默清扫一字不动；
- ``mention_escalated``：``repeated >= approval.mention_escalation``（默认
  ``config.DEFAULT_MENTION_ESCALATION``）；阈值 0 / 负 = 关；坏配置回落出厂值；
- ``completion_hint``：卡上有提示才出键，``at`` 转 epoch int；空壳整键省略；
  **债务列的备选卡同样投影它**（盖章状态含 detected，卡面才有得看，PR #349 评审）；
- 阈值这把旋钮有**唯一一处**可见面：设置页「审批 / 成本」区那一行（落点
  ``approval.mention_escalation``、override 扁平键同名），actd 每 pass 现读
  （`_refresh_model_knobs`）——config.yaml 手改 + 重启不是唯一出路；
- ``detect_transitions``：三个信号的 false→true 翻面各响**一次**（同一张卡
  在两个快照里都在）；恒为真的下一个 pass 静默；新卡不在此列（§40 的新卡
  通知已经点名过它）；``prev is None``（actd 刚起）整轮不发。
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, dashboard, notify
from act.lib.actd import alerts
from act.lib.registry import Requirement
from server import settings_catalog


def _row(**kw) -> dict:
    """一张 card_sent 卡的投影行（needs_approval[0]）。"""
    cfg = kw.pop("cfg", None) or config.Config()
    req = Requirement.from_dict({"id": "P-023", "title": "改名 Compass",
                                 "status": "card_sent", **kw})
    dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg, archived=[])
    return dash["needs_approval"][0]


def _debt_row(**kw) -> dict:
    """一张 detected 卡的投影行（debt[0]）——备选列的同一条提示。"""
    req = Requirement.from_dict({"id": "P-204", "title": "潜在任务",
                                 "status": "detected", **kw})
    dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=config.Config(),
                                     archived=[])
    return dash["debt"][0]


class DecisionDueTestCase(unittest.TestCase):
    def test_truth_table(self):
        today = dashboard._today().isoformat()
        cases = {None: False, "": False, "not-a-date": False,
                 "2099-01-01": False, today: True, "2000-01-01": True}
        for deadline, want in cases.items():
            with self.subTest(deadline=deadline):
                self.assertIs(_row(deadline=deadline)["decision_due"], want)

    def test_key_is_always_present(self):
        """派生值没有「不知道」这一档——键恒在，客户端不必猜旧 server。"""
        self.assertIn("decision_due", _row())


class MentionEscalationTestCase(unittest.TestCase):
    def _escalated(self, repeated, threshold=None) -> bool:
        cfg = config.Config()
        if threshold is not None:
            cfg.approval_mention_escalation = threshold
        return _row(repeated_mentions=repeated, cfg=cfg)["mention_escalated"]

    def test_default_threshold_is_five(self):
        self.assertEqual(config.Config().approval_mention_escalation,
                         config.DEFAULT_MENTION_ESCALATION)
        self.assertIs(self._escalated(4), False)
        self.assertIs(self._escalated(5), True)
        self.assertIs(self._escalated(23), True)      # issue #313 的 P-008

    def test_zero_or_negative_threshold_turns_it_off(self):
        for threshold in (0, -1):
            with self.subTest(threshold=threshold):
                self.assertIs(self._escalated(99, threshold), False)

    def test_bad_counts_never_raise(self):
        self.assertIs(self._escalated("abc"), False)   # _repeated -> 1

    def test_config_reads_the_approval_knob(self):
        cfg = config.Config()
        config._apply_approval(cfg, {"approval": {"mention_escalation": 3}})
        self.assertEqual(cfg.approval_mention_escalation, 3)
        # 坏形状回落出厂值——配错一个字不许把唯一的升级路悄悄关掉
        cfg2 = config.Config()
        config._apply_approval(cfg2, {"approval": {"mention_escalation": "lots"}})
        self.assertEqual(cfg2.approval_mention_escalation,
                         config.DEFAULT_MENTION_ESCALATION)


class CompletionHintProjectionTestCase(unittest.TestCase):
    def test_hint_projects_with_epoch_at(self):
        row = _row(completion_hint={"at": "2026-09-09T12:00:00Z",
                                    "note": "repo 已建", "channel": "meeting"})
        self.assertEqual(row["completion_hint"],
                         {"at": 1788955200, "note": "repo 已建", "channel": "meeting"})

    def test_absent_or_hollow_hints_omit_the_key(self):
        for hint in (None, {}, "done", [], {"at": None, "note": "", "channel": "x"}):
            with self.subTest(hint=hint):
                self.assertNotIn("completion_hint", _row(completion_hint=hint))

    def test_unparsable_at_still_projects_when_there_is_a_note(self):
        row = _row(completion_hint={"at": "whenever", "note": "已经做完了"})
        self.assertEqual(row["completion_hint"],
                         {"at": None, "note": "已经做完了", "channel": ""})

    def test_the_debt_lane_projects_it_too(self):
        """备选卡也会被盖章（§76.1 的 `_HINT_STATES` 含 detected），所以债务列
        也必须发这一键——否则那是只写不读的死字段（PR #349 评审）。"""
        row = _debt_row(completion_hint={"at": "2026-09-09T12:00:00Z",
                                         "note": "repo 已建", "channel": "meeting"})
        self.assertEqual(row["completion_hint"],
                         {"at": 1788955200, "note": "repo 已建", "channel": "meeting"})
        self.assertNotIn("completion_hint", _debt_row())
        # 提案列那两个派生 bool 不下到这一列（备选卡没有 deadline / 被提章面）
        self.assertNotIn("decision_due", row)
        self.assertNotIn("mention_escalated", row)


class MentionEscalationKnobSurfaceTestCase(unittest.TestCase):
    """这把旋钮唯一的可见面 = 设置页「审批 / 成本」区那一行（D3 之后原生 app
    退役，config.yaml 手改 + 重启不是唯一出路）；actd 每 pass 现读。"""

    def setUp(self):
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)

    def _write(self, doc):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")

    def _catalog_row(self) -> dict:
        section = next(s for s in settings_catalog.SECTIONS if s["id"] == "approval")
        return next(f for f in section["fields"]
                    if f["key"] == "approval_mention_escalation")

    def test_catalog_row_mirrors_the_config_knob(self):
        row = self._catalog_row()
        self.assertEqual(row["kind"], "int")
        self.assertEqual(row["default"], config.DEFAULT_MENTION_ESCALATION)
        self.assertEqual(row["config"], ("approval", "mention_escalation"))
        self.assertEqual(row["override"], "approval_mention_escalation")
        self.assertIn("approval_mention_escalation", config._OVERRIDE_FIELDS)
        self.assertTrue(row["label"]["zh"] and row["label"]["en"])
        self.assertTrue(row["help"]["zh"] and row["help"]["en"])

    def test_the_override_key_reaches_the_pipeline(self):
        self._write({"approval_mention_escalation": 3})
        cfg = config.Config()
        config._apply_settings_overrides(cfg)
        self.assertEqual(cfg.approval_mention_escalation, 3)
        self._write({"approval_mention_escalation": -2})   # 负数 / 垃圾 = 该条跳过
        cfg2 = config.Config()
        config._apply_settings_overrides(cfg2)
        self.assertEqual(cfg2.approval_mention_escalation,
                         config.DEFAULT_MENTION_ESCALATION)

    def test_actd_rereads_it_every_pass(self):
        frozen = config.Config()
        self._write({"approval_mention_escalation": 0})
        actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.approval_mention_escalation, 0)   # 关掉即生效
        self._write({})                       # diff-write 删键 = 回到出厂默认
        actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.approval_mention_escalation,
                         config.DEFAULT_MENTION_ESCALATION)


def _snap(**flags) -> dict:
    row = {"id": "P-023", "title": "改名 Compass", "repeated": 23}
    row.update(flags)
    return {"needs_approval": [row], "running": [], "review": []}


class SettlementTransitionsTestCase(unittest.TestCase):
    def _titles(self, prev, curr) -> list:
        return [m[0] for m in alerts.detect_transitions(prev, curr)]

    def test_each_signal_fires_once_on_the_flip(self):
        # 文案双语单源 = notify._pick（测试环境答英文）——这里只认那一份
        for key, phrase in (("completion_hint", "already done"),
                            ("decision_due", "deadline"),
                            ("mention_escalated", "came up")):
            with self.subTest(key=key):
                flip = {key: True} if key != "completion_hint" else {
                    key: {"at": 1, "note": "n", "channel": "c"}}
                titles = self._titles(_snap(), _snap(**flip))
                self.assertEqual(len(titles), 1, titles)
                self.assertIn(phrase, titles[0])
                # 恒为真的下一个 pass 静默（投影幂等，通知不幂等）
                self.assertEqual(self._titles(_snap(**flip), _snap(**flip)), [])

    def test_all_three_can_fire_in_one_pass(self):
        curr = _snap(completion_hint={"note": "n"}, decision_due=True,
                     mention_escalated=True)
        self.assertEqual(len(self._titles(_snap(), curr)), 3)

    def test_the_mention_message_names_the_count(self):
        msgs = alerts.detect_transitions(_snap(), _snap(mention_escalated=True))
        self.assertIn("23", msgs[0][0])
        self.assertEqual(msgs[0][2], "P-023")
        self.assertEqual(msgs[0][3], notify.KIND_PROPOSAL)

    def test_a_hand_mangled_repeated_count_reads_as_zero_instead_of_crashing(self):
        """`repeated` 是投影上的一个数字位，而盘面是文件：手改过的卡、升级前写下的
        行里什么都可能有。文案里诚实写 0，绝不让一次通知扫描把整个 pass 带下水
        （宪法第 11 条；同一条纪律钉在 LLM 输出逐字段消毒上）。"""
        for bad in ("many", {"n": 3}, [3], None):
            with self.subTest(repeated=bad):
                msgs = alerts.detect_transitions(
                    _snap(repeated=bad), _snap(repeated=bad, mention_escalated=True))
                self.assertEqual(len(msgs), 1)
                self.assertIn("0", msgs[0][0])

    def test_a_brand_new_card_does_not_double_up(self):
        """出生即带信号的新卡只响 §40 的新卡通知，不再多响三声。"""
        titles = self._titles({"needs_approval": [], "running": [], "review": []},
                              _snap(decision_due=True, mention_escalated=True))
        self.assertEqual(len(titles), 1)
        self.assertIn("awaiting approval", titles[0])

    def test_restart_replays_nothing(self):
        self.assertEqual(alerts.detect_transitions(None, _snap(decision_due=True)), [])

    def test_a_signal_going_away_is_silent(self):
        self.assertEqual(self._titles(_snap(decision_due=True), _snap()), [])


if __name__ == "__main__":
    unittest.main()
