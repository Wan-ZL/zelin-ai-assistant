"""§76.1 疑似已完成：雷达 fold 时盖 add-only ``completion_hint``，状态永不变。

issue #313 的第一条诉求。钉住的契约：

(a) triage prompt 带第三个判据 ``completed``（relates_to 分支）；
(b) ``completed=true`` 命中 detected/card_sent 卡 -> 卡上出现
    ``completion_hint {at, note, channel}``，**status 一个字不改**，fold 本身
    （备注 + sources 去重 + 被提数）照旧发生——证据永不因为提示被丢掉；
(c) 缺键 / 垃圾值 / ``false`` -> 没有提示（fail-closed：LLM 说不出「做完了」
    就不许在卡上贴「疑似已完成」）；字符串 "true"/"yes" 宽容放行；
(d) owner 已投入的卡（approved/executing/review/delivered）永不被盖提示；
(e) §45 屏幕闸门（CORROBORATE）照旧只佐证：fold 进开着的卡时**可以**盖提示
    （盖提示既不铸卡也不改状态——那正是 P-023 的形态），但一张卡都不许新铸、
    一个状态都不许变；
(f) note 截到 ``HINT_NOTE_CAP``，channel = 证据的真实来路（子候选 sources）。
"""
import shutil
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, provenance, quick_capture, registry, silent_merge


def _proc(stdout: str = "", returncode: int = 0):
    import subprocess
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode,
                                       stdout=stdout, stderr="")


def _clean_state():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        shutil.rmtree(config.REGISTRY_DIR)
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()


class CompletionHintTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)
        self.cfg = config.Config()
        # §44.2 的 fold 判官坐在 apply_triage 底下——恒判「不同」，本文件钉的
        # 不是它（同 tests/test_radar_triage.py 的做法，绝不 spawn 真 claude）。
        judge = mock.patch.object(
            silent_merge, "JUDGE_RUNNER",
            lambda prompt: _proc('{"same_thing": false, "brief": "不同的事"}'))
        judge.start()
        self.addCleanup(judge.stop)

    # -- helpers ----------------------------------------------------------- #
    def _target(self, status="card_sent", **kw) -> registry.Requirement:
        r = registry.Requirement(
            id="P-023", title="把 Strawberry 改名 Compass：同步 repo、slides 与文档",
            status=status, deadline="2026-09-09", repeated_mentions=3, **kw)
        registry.save(r)
        return r

    def _evidence(self, summary="Compass repo 已建、slides 已改", channel="meeting"):
        return registry.Requirement(
            id=registry.next_id(), title=summary[:80], summary=summary,
            status="card_sent",
            sources=[{"who": "zelin", "channel": channel, "date": "2026-09-09",
                      "quote": summary}])

    def _decision(self, **kw) -> dict:
        d = {"action": "relates_to", "req": "P-023", "note": "rename 已经做完了"}
        d.update(kw)
        return d

    # -- (a) prompt --------------------------------------------------------- #
    def test_prompt_asks_for_the_completed_judgement(self):
        prompt = quick_capture.build_triage_prompt("候选需求：Compass repo 已建", self.cfg)
        self.assertIn("completed", prompt)
        self.assertIn("已经发生", prompt)
        self.assertIn("拿不准填 false", prompt)

    # -- (b) the happy path ------------------------------------------------- #
    def test_completed_stamps_the_hint_without_touching_status(self):
        self._target()
        kind, target = quick_capture.apply_triage(
            self._decision(completed=True), self._evidence(), self.cfg)
        self.assertEqual(kind, "folded")
        saved = registry.load("P-023")
        self.assertEqual(saved.status, "card_sent")          # 状态一个字没改
        hint = saved.completion_hint
        self.assertEqual(set(hint), {"at", "note", "channel"})
        self.assertEqual(hint["note"], "rename 已经做完了")
        self.assertEqual(hint["channel"], "meeting")
        self.assertTrue(hint["at"].endswith("Z"))
        self.assertEqual(target.completion_hint, hint)

    def test_the_fold_itself_still_happens(self):
        """提示不是丢弃证据的借口：备注 / sources / 被提数照旧（issue #313 的
        「不要继续并入」在这里被明确偏离——见 §76.4 与 D70）。"""
        self._target()
        quick_capture.apply_triage(self._decision(completed=True),
                                   self._evidence(), self.cfg)
        saved = registry.load("P-023")
        self.assertIn("[radar]", saved.notes)
        self.assertIn("rename 已经做完了", saved.notes)
        self.assertEqual(len(saved.sources), 1)
        self.assertEqual(saved.repeated_mentions, 4)          # 3 + 1 条新来源

    def test_detected_card_also_takes_the_hint(self):
        self._target(status="detected")
        evidence = self._evidence()
        evidence.set_status(registry.State.DETECTED)   # 不是 act-now 候选，不谈提升
        quick_capture.apply_triage(self._decision(completed=True, needs_action=False),
                                   evidence, self.cfg)
        saved = registry.load("P-023")
        self.assertEqual(saved.status, "detected")            # 备选卡不被提升
        self.assertIsNotNone(saved.completion_hint)

    def test_repeat_hits_overwrite_with_the_newest_evidence(self):
        self._target()
        quick_capture.apply_triage(self._decision(completed=True),
                                   self._evidence(), self.cfg)
        quick_capture.apply_triage(
            self._decision(completed=True, note="Slack 里也宣布了"),
            self._evidence(summary="Slack 公告：Compass 已上线", channel="slack"),
            self.cfg)
        hint = registry.load("P-023").completion_hint
        self.assertEqual(hint["note"], "Slack 里也宣布了")
        self.assertEqual(hint["channel"], "slack")

    # -- (c) fail-closed coercion ------------------------------------------ #
    def test_missing_or_garbage_completed_leaves_no_hint(self):
        for value in ({}, {"completed": None}, {"completed": False},
                      {"completed": "false"}, {"completed": "maybe"},
                      {"completed": 1}, {"completed": []}):
            with self.subTest(value=value):
                _clean_state()
                self._target()
                quick_capture.apply_triage(self._decision(**value),
                                           self._evidence(), self.cfg)
                self.assertIsNone(registry.load("P-023").completion_hint)

    def test_string_true_is_tolerated(self):
        for value in ("true", "TRUE", " yes ", "1"):
            with self.subTest(value=value):
                _clean_state()
                self._target()
                quick_capture.apply_triage(self._decision(completed=value),
                                           self._evidence(), self.cfg)
                self.assertIsNotNone(registry.load("P-023").completion_hint)

    # -- (d) invested cards are off limits --------------------------------- #
    def test_invested_states_never_take_a_hint(self):
        for status in ("approved", "executing", "review", "delivered"):
            with self.subTest(status=status):
                _clean_state()
                self._target(status=status)
                quick_capture.apply_triage(
                    self._decision(completed=True, needs_action=False),
                    self._evidence(), self.cfg)
                saved = registry.load("P-023")
                self.assertEqual(saved.status, status)
                self.assertIsNone(saved.completion_hint)

    # -- (e) §45 screen gate ------------------------------------------------ #
    def test_screen_evidence_may_corroborate_but_never_promotes_or_mints(self):
        """P-023 的真实形态：证据来自屏幕录制笔记（CORROBORATE）。提示可以盖，
        状态不许动，卡不许新铸（§45 一字不动）。"""
        self._target(status="detected")
        kind, _ = quick_capture.apply_triage(
            self._decision(completed=True, needs_action=True),
            self._evidence(), self.cfg, gate=provenance.CORROBORATE)
        self.assertEqual(kind, "folded")
        saved = registry.load("P-023")
        self.assertEqual(saved.status, "detected")            # act-now 提升被压平
        self.assertIsNotNone(saved.completion_hint)
        self.assertEqual([r.id for r in registry.load_all()], ["P-023"])

    def test_screen_evidence_on_a_closed_card_is_still_blocked(self):
        """完结卡命中照旧整条拦下——闸门语义没有因为新信号松一寸。"""
        self._target(status="delivered")
        kind, saved = quick_capture.apply_triage(
            self._decision(completed=True), self._evidence(), self.cfg,
            gate=provenance.CORROBORATE)
        self.assertEqual(kind, "ignored")
        self.assertIsNone(saved)
        self.assertIsNone(registry.load("P-023").completion_hint)

    # -- (f) shape discipline ---------------------------------------------- #
    def test_note_is_capped_and_channel_falls_back(self):
        self._target()
        evidence = self._evidence()
        evidence.sources = [{"who": "x", "date": "2026-09-09"}]   # 没有 channel
        quick_capture.apply_triage(
            self._decision(completed=True, note="做完了 " * 300), evidence, self.cfg)
        hint = registry.load("P-023").completion_hint
        self.assertEqual(len(hint["note"]), quick_capture.HINT_NOTE_CAP)
        self.assertEqual(hint["channel"], "radar")

    def test_a_source_row_that_is_not_a_table_falls_back_to_radar(self):
        """`sources` 是盘上的 YAML：手改过的文件里那一条可能是个裸字符串。读不出
        channel 的条目跳过（不是崩、也不是把 `str(...)` 出来的垃圾写成 channel），
        一条都读不出就回落 `radar`（D64 的口径：通道是内容的出身）。"""
        self._target()
        evidence = self._evidence()
        evidence.sources = ["屏幕上看到 Compass 已经上线", 7]
        quick_capture.apply_triage(self._decision(completed=True), evidence, self.cfg)
        self.assertEqual(registry.load("P-023").completion_hint["channel"], "radar")

    def test_a_stamping_failure_never_takes_the_fold_down_with_it(self):
        """盖章这一步自己炸了 = 吞掉，fold 照常落盘（一个提示不许连坐数据）。

        §76.1 的提示是**观测面**，而 fold 是数据：备注 / sources / 被提数都必须
        照常落下去（宪法第 11 条）。这里让盖章时刻那一下抛（时钟 / 时区库出问题的
        形状），因为它是盖章路上最早的一步——提示整块因此写不出来。"""
        self._target()
        with mock.patch.object(quick_capture, "_iso_now",
                               side_effect=OSError("时钟读不出来")):
            kind, _target = quick_capture.apply_triage(
                self._decision(completed=True), self._evidence(), self.cfg)
        self.assertEqual(kind, "folded")
        saved = registry.load("P-023")
        self.assertEqual(saved.status, "card_sent")
        self.assertIsNone(saved.completion_hint)               # 提示没写出来
        self.assertIn("rename 已经做完了", saved.notes or "")    # 证据一条没丢
        self.assertEqual(len(saved.sources), 1)
        self.assertEqual(saved.repeated_mentions, 4)

    def test_hint_survives_a_yaml_round_trip(self):
        self._target()
        quick_capture.apply_triage(self._decision(completed=True),
                                   self._evidence(), self.cfg)
        reloaded = registry.Requirement.from_dict(
            registry.load("P-023").to_dict())
        self.assertEqual(reloaded.completion_hint["channel"], "meeting")


if __name__ == "__main__":
    unittest.main()
