"""§76.2 / §76.3 结算信号：投影真值表 + 三条一次性升级通知。

issue #313 的第二、三条诉求。**§78（issue #447 / owner 决策 D80）之后这三个
信号住在潜在任务列**（``debt[]``）：提案车道退役，``needs_approval[]`` 恒空，
所以原来钉在提案行上的每一条判例都逐条重锚到 ``debt[]``——口径一个字没改，
换的只是它长在哪一行上。钉住的契约：

- ``decision_due``：``days_left <= 0`` 为真；未来 / 无 / 坏 deadline 为假
  （拿不准不催人）。它是投影，不是状态——§70.2 的静默清扫一字不动；
- ``mention_escalated``：``repeated >= approval.mention_escalation``（默认
  ``config.DEFAULT_MENTION_ESCALATION``）；阈值 0 / 负 = 关；坏配置回落出厂值；
- ``completion_hint``：卡上有提示才出键，``at`` 转 epoch int；空壳整键省略；
- **两个派生 bool 现在必须下到潜在任务列**（§78 / D80.8 作废了 §76.2 原文
  「备选卡面没有 deadline 决策行」那一句）：那是 owner 唯一看得见它们的卡面，
  留在恒空的提案列上等于把 issue #313 的三条信号整体报废；
- 退役状态 ``card_sent`` 的落单卡投影进同一列、同一张卡面（§78：没有一张卡
  因为状态退役而隐形）；
- 阈值这把旋钮有**唯一一处**可见面：设置页「审批 / 成本」区那一行（落点
  ``approval.mention_escalation``、override 扁平键同名），actd 每 pass 现读
  （`_refresh_model_knobs`）——config.yaml 手改 + 重启不是唯一出路；
- ``detect_transitions``：三个信号的 false→true 翻面各响**一次**（同一张卡
  在两个快照里都在）；恒为真的下一个 pass 静默；新卡不在此列（§40 的新卡
  通知已经点名过它）；``prev is None``（actd 刚起）整轮不发。**差分源自 §78
  起是 ``debt[]``**——这是整次退役里最容易静默失效的一处（§40.6 修法）。
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, dashboard, notify
from act.lib.actd import alerts
from act.lib.registry import Requirement
from server import settings_catalog


def _row(status="detected", **kw) -> dict:
    """一张机器卡的投影行（§78：潜在任务列 ``debt[0]``，不再是 needs_approval）。"""
    cfg = kw.pop("cfg", None) or config.Config()
    req = Requirement.from_dict({"id": "P-023", "title": "改名 Compass",
                                 "status": status, **kw})
    dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg, archived=[])
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

    def test_the_retired_lane_straggler_projects_the_same_face(self):
        """退役状态 ``card_sent`` 的落单卡照样投影进潜在任务列、长同一张卡面
        （§78：归并扫描没跑完 / 手改盘面留下的卡不许因为状态退役而隐形）。

        这一条继承的是旧判例「另一条车道也必须发 ``completion_hint``，否则那是
        只写不读的死字段」（PR #349 评审）——提案列退役后「另一条车道」指的就是
        落单的 ``card_sent``。"""
        row = _row(status="card_sent",
                   completion_hint={"at": "2026-09-09T12:00:00Z",
                                    "note": "repo 已建", "channel": "meeting"})
        self.assertEqual(row["completion_hint"],
                         {"at": 1788955200, "note": "repo 已建", "channel": "meeting"})
        self.assertNotIn("completion_hint", _row(status="card_sent"))
        # 落单卡面与 detected 卡面同形：三个结算信号一个不少
        self.assertIn("decision_due", row)
        self.assertIn("mention_escalated", row)

    def test_the_two_derived_bools_ride_the_backlog_row(self):
        """§78 / D80.8 **作废**了 §76.2 原文「备选卡面没有 deadline 决策行、
        也没有『被提×N』章」那一句——它是一句关于提案列与备选列分工的论断，而
        提案列已经不存在了。``debt[]`` 是 owner 唯一看得见这两个派生 bool 的卡面：
        留在恒空的 ``needs_approval[]`` 上 = issue #313 的三条结算信号整体报废
        （P-008「被提 ×23 一次升级动作都没有」原样复发）。"""
        row = _row(deadline="2000-01-01", repeated_mentions=23)
        self.assertIs(row["decision_due"], True)
        self.assertIs(row["mention_escalated"], True)

    def test_the_retired_proposal_lane_stays_present_and_empty(self):
        """§78 墓碑：``needs_approval`` 这个 wire 键与 ``counts.needs_approval``
        都留着（宪法第 6 条 add-only；冻结的原生 app 少一个键就整份 payload 解不开），
        但恒为 ``[]`` / 恒为 0——它的空是法条，不是遗漏。"""
        req = Requirement.from_dict({"id": "P-023", "title": "改名 Compass",
                                     "status": "detected", "deadline": "2000-01-01"})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=config.Config(),
                                         archived=[])
        self.assertEqual(dash["needs_approval"], [])
        self.assertEqual(dash["counts"]["needs_approval"], 0)
        self.assertEqual([r["id"] for r in dash["debt"]], ["P-023"])


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
    """一份看板快照。§78：结算信号的差分源是潜在任务列（``debt[]``）——
    ``needs_approval[]`` 恒空，继续从它差分 = 三条升级通知全部无声死掉
    （§40.6 修法）。这里刻意把空的提案列也摆上，钉死「别再看那一列」。"""
    row = {"id": "P-023", "title": "改名 Compass", "repeated": 23}
    row.update(flags)
    return {"needs_approval": [], "debt": [row], "running": [], "review": []}


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
        empty = {"needs_approval": [], "debt": [], "running": [], "review": []}
        titles = self._titles(empty,
                              _snap(decision_due=True, mention_escalated=True))
        self.assertEqual(len(titles), 1)
        self.assertIn("awaiting approval", titles[0])

    def test_restart_replays_nothing(self):
        self.assertEqual(alerts.detect_transitions(None, _snap(decision_due=True)), [])

    def test_a_signal_going_away_is_silent(self):
        self.assertEqual(self._titles(_snap(decision_due=True), _snap()), [])


if __name__ == "__main__":
    unittest.main()
