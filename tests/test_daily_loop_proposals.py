"""§70 每日提案器：≤ max_proposals_per_day 张 🤖 卡、指纹去重、每 class 一条、
GitHub 同题不重提、卡片形状（channel self_improve 写死 → proposed，plan/DoD/成本齐）；
D33：自检类信号（ADVISORY_KINDS / doctor owner_action）只成 advisory 行，永不铸卡。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py)。
"""
import datetime as _dt
import inspect
import re
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, daily_loop, failures, loop_inputs, policy, registry
from act.lib.loop_inputs import ADVISORY_KINDS, CARD_KINDS, Signal, Summary
from act.lib.registry import State

TODAY = "2026-09-02"
NOW = _dt.datetime(2026, 9, 2, 10, 30, tzinfo=_dt.timezone.utc)


def _sig(kind, detail, priority=50, title=None, ref=""):
    return Signal(kind=kind, fingerprint=f"{kind}:{detail}", title=title or f"{kind} {detail} long enough title",
                  summary="why", plan=["p1"], dod=["d1"], cost_usd=2.0, evidence="ev", priority=priority, ref=ref)


class SelectSignalsTestCase(unittest.TestCase):
    def test_cap_priority_and_one_per_kind(self):
        signals = [_sig("issue", "1", 45), _sig("issue", "2", 45), _sig("pr_red", "7", 5),
                   _sig("mutation", "m", 40), _sig("pr_comment", "a", 8), _sig("pr_comment", "b", 8),
                   _sig("material", "m-1", 42)]
        chosen, skipped = daily_loop.select_signals(signals, taken=set(), gh_titles=[], budget=3)
        self.assertEqual([s.kind for s in chosen], ["pr_red", "pr_comment", "mutation"])
        self.assertEqual(skipped["kind_taken"], 1)      # pr_comment:b (a was chosen)
        self.assertEqual(skipped["cap"], 3)             # material, issue:1, issue:2
        self.assertEqual(skipped["dedup"], 0)
        self.assertEqual(skipped["advisory"], 0)

    def test_taken_fingerprints_and_github_titles_dedup(self):
        signals = [_sig("material", "m-1", title="消化素材：账本写风暴：R-1.yaml 24 h 内被重写 150 次"),
                   _sig("mutation", "x", title="补测试：act/lib/registry.py 变异存活 198 体"),
                   _sig("issue", "18", title="issue #18：账本写风暴：R-1.yaml 24 h 内被重写 150 次")]
        chosen, skipped = daily_loop.select_signals(
            signals, taken={"mutation:x"},
            gh_titles=["fix: 消化素材：账本写风暴：R-1.yaml 24 h 内被重写 150 次 (write storm)"], budget=5)
        # material is already an open issue/PR title → skipped; the issue-derived
        # signal is exempt from the GitHub-title check (it IS the GitHub item)
        self.assertEqual([s.kind for s in chosen], ["issue"])
        self.assertEqual(skipped, {"advisory": 0, "dedup": 1, "kind_taken": 0, "gh_title": 1, "cap": 0})

    def test_zero_budget_selects_nothing(self):
        chosen, skipped = daily_loop.select_signals([_sig("pr_red", "1")], taken=set(), gh_titles=[], budget=0)
        self.assertEqual(chosen, [])
        self.assertEqual(skipped["cap"], 1)


class AdvisoryKindsTestCase(unittest.TestCase):
    """D33：自检类信号永不铸卡——selector 与 collector 两道闸都按 ADVISORY_KINDS 元组
    参数化，日后加一种 kind 归错类会在这里红。"""

    def test_every_signal_kind_in_loop_inputs_is_classified(self):
        # 每个 Signal 构造点写的是 kind="<x>", fingerprint=f"<x>:…"——新 kind 必须归到一类
        kinds = set(re.findall(r'kind="(\w+)", fingerprint=f"', inspect.getsource(loop_inputs)))
        self.assertTrue(kinds >= {"issue", "doctor_fail", "material", "pr_red"})   # regex 还活着
        self.assertEqual(kinds, set(CARD_KINDS) | set(ADVISORY_KINDS))
        self.assertEqual(set(CARD_KINDS) & set(ADVISORY_KINDS), set())

    def test_selector_never_chooses_an_advisory_kind(self):
        for kind in ADVISORY_KINDS:
            with self.subTest(kind=kind):
                chosen, skipped = daily_loop.select_signals([_sig(kind, "x", 1)], taken=set(),
                                                            gh_titles=[], budget=5)
                self.assertEqual(chosen, [])
                self.assertEqual(skipped["advisory"], 1)
        for kind in CARD_KINDS:
            with self.subTest(kind=kind):
                chosen, _skipped = daily_loop.select_signals([_sig(kind, "x", 1)], taken=set(),
                                                             gh_titles=[], budget=5)
                self.assertEqual([s.kind for s in chosen], [kind])

    def test_collector_turns_advisory_kinds_into_summaries(self):
        for kind in ADVISORY_KINDS:
            with self.subTest(kind=kind):
                sig = _sig(kind, "x", 1, title="派发卡死：3 张已批卡发不出去")
                with mock.patch.object(loop_inputs, "registry_signals", return_value=[sig]), \
                        mock.patch.object(loop_inputs, "materials_signals", return_value=[]):
                    out = daily_loop.collect_signals([], now=NOW, gh=lambda a: None, doctor=lambda: "[]",
                                                     repo="o/r")
                self.assertEqual(out["signals"], [])
                self.assertEqual(out["inputs"]["registry"], 1)        # the reader is still counted
                mine = [a for a in out["advisories"] if a.fingerprint == f"{kind}:x"]
                self.assertEqual(len(mine), 1)
                adv = mine[0]
                self.assertIsInstance(adv, Summary)
                self.assertEqual(adv.kind, kind)
                self.assertTrue(adv.text.startswith("派发卡死：3 张已批卡发不出去 — why"))

    def test_doctor_owner_action_row_is_an_advisory_whatever_its_kind(self):
        for fid in sorted(failures.OWNER_ACTION_IDS):
            self.assertTrue(loop_inputs.is_advisory(_sig("issue", "1", ref=fid)), fid)   # belt and braces
        self.assertTrue(loop_inputs.is_advisory(_sig("doctor_fail", "launchd claude", ref="claude_blind")))
        self.assertTrue(loop_inputs.is_advisory(_sig("doctor_fail", "python", ref="")))   # kind alone suffices
        self.assertFalse(loop_inputs.is_advisory(_sig("issue", "1", ref="https://github.com/x/1")))
        self.assertFalse(loop_inputs.is_advisory(_sig("pr_red", "7", ref="")))

    def test_split_orders_by_priority_and_keeps_cards(self):
        cards, adv = daily_loop.split_advisories([_sig("doctor_fail", "a", 14), _sig("issue", "1", 45),
                                                  _sig("stuck_dispatch", "claude_blind", 10)])
        self.assertEqual([s.kind for s in cards], ["issue"])
        self.assertEqual([a.fingerprint for a in adv], ["stuck_dispatch:claude_blind", "doctor_fail:a"])

    def test_advisory_rows_inherit_first_seen_and_are_capped(self):
        adv = [Summary(kind="doctor_fail", text=f"t{i}", fingerprint=f"doctor_fail:{i}") for i in range(25)]
        state = {"last_result": {"advisories": [{"fingerprint": "doctor_fail:3", "first_seen": "2026-08-30"},
                                                {"fingerprint": "doctor_fail:4"},            # no first_seen → today
                                                "garbage", {"fingerprint": "", "first_seen": "x"}]}}
        rows = daily_loop.advisory_rows(adv, state, TODAY)
        self.assertEqual(len(rows), daily_loop.ADVISORIES_CAP)
        self.assertEqual(set(rows[0]), {"kind", "text", "ref", "fingerprint", "first_seen"})
        by_fp = {r["fingerprint"]: r["first_seen"] for r in rows}
        self.assertEqual(by_fp["doctor_fail:3"], "2026-08-30")
        self.assertEqual(by_fp["doctor_fail:4"], TODAY)
        self.assertEqual(by_fp["doctor_fail:0"], TODAY)
        self.assertEqual(daily_loop.advisory_rows(adv, {"last_result": "bad"}, TODAY)[0]["first_seen"], TODAY)


class FileProposalsTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_card_shape_is_proposal_lane_with_locked_channel(self):
        sig = _sig("pr_red", "7", title="修红 CI：PR #7 feat: quieter loop")
        filed = daily_loop.file_proposals([sig], TODAY, "/repo/path")
        self.assertEqual(len(filed), 1)
        card = registry.load(filed[0]["id"])
        self.assertTrue(card.title.startswith("🤖 "))
        self.assertEqual(card.status, State.CARD_SENT.value)
        self.assertEqual(card.type, "self-improvement")
        self.assertEqual(card.target_repo, "/repo/path")
        self.assertEqual(card.plan, ["p1"])
        self.assertEqual(card.definition_of_done, ["d1"])
        self.assertEqual(card.cost_estimate_usd, 2.0)
        src = card.sources[0]
        self.assertEqual(src["channel"], daily_loop.SOURCE_CHANNEL)
        self.assertEqual(src["ref"], "self_improve:pr_red:7")
        self.assertEqual(src["date"], TODAY)
        self.assertEqual(card.origin_trust, policy.PROPOSED)          # §50: AI 自提 → 人批
        self.assertEqual(policy.classify_origin(card.sources), policy.PROPOSED)
        self.assertFalse(policy.may_auto_dispatch(card, config.Config())[0])   # §51 不免批
        self.assertTrue(card.id.startswith("P-"))                        # §60 主键

    def test_fingerprints_are_read_back_from_the_registry_including_trash(self):
        sig = _sig("material", "m-1")
        filed = daily_loop.file_proposals([sig], TODAY, "/repo")
        card = registry.load(filed[0]["id"])
        registry.trash(card, "rejected")                                # owner said no
        reqs = registry.load_all()
        self.assertIn("material:m-1", daily_loop.existing_fingerprints(reqs))
        self.assertEqual(daily_loop.proposals_today(reqs, TODAY), 1)     # trashed still spends the day's cap
        self.assertEqual(daily_loop.proposals_today(reqs, "2026-09-03"), 0)

    def test_same_title_twice_folds_instead_of_duplicating(self):
        sig = _sig("mutation", "x", title="补测试：act/lib/registry.py 变异存活 198 体")
        first = daily_loop.file_proposals([sig], TODAY, "/repo")
        again = daily_loop.file_proposals([Signal(**{**sig.__dict__, "fingerprint": "mutation:y"})], TODAY, "/repo")
        self.assertEqual(first[0]["outcome"], "proposed")
        self.assertEqual(again[0]["outcome"], "folded")
        self.assertEqual(again[0]["id"], first[0]["id"])
        self.assertEqual(len(registry.load_all()), 1)

    def test_a_bad_card_is_isolated(self):
        bad = _sig("issue", "1")
        bad.plan = object()          # unserializable → save blows up
        good = _sig("pr_red", "7")
        filed = daily_loop.file_proposals([bad, good], TODAY, "/repo")
        self.assertEqual(len(filed), 2)
        self.assertIn("error", filed[0])
        self.assertIn("id", filed[1])


class TitleOnGithubTestCase(unittest.TestCase):
    def test_containment_needs_length(self):
        self.assertTrue(daily_loop.title_on_github("修红 CI：PR #7 feat: x", ["fix: 修红 CI：PR #7 feat: x now"]))
        self.assertFalse(daily_loop.title_on_github("short", ["short"]))
        self.assertFalse(daily_loop.title_on_github("something long enough here", ["unrelated title here"]))


class ConfigKnobsTestCase(unittest.TestCase):
    def test_defaults_and_yaml_block(self):
        cfg = config.Config()
        self.assertTrue(cfg.daily_loop_enabled)
        self.assertEqual((cfg.daily_loop_time, cfg.daily_loop_max_proposals_per_day,
                          cfg.daily_loop_stale_days, cfg.daily_loop_trash_retention_days),
                         ("03:30", 2, 45, 90))                         # D33: 5 → 2
        self.assertEqual(config.DEFAULT_DAILY_LOOP_MAX_PROPOSALS, 2)
        config._apply_daily_loop_block(cfg, {"daily_loop": {
            "enabled": "no", "time": "4:05", "max_proposals_per_day": 4, "stale_days": -3,
            "trash_retention_days": "bogus"}})
        self.assertFalse(cfg.daily_loop_enabled)
        self.assertEqual(cfg.daily_loop_time, "04:05")
        self.assertEqual(cfg.daily_loop_max_proposals_per_day, 4)
        self.assertEqual(cfg.daily_loop_stale_days, 0)          # negative → 0 (= off)
        self.assertEqual(cfg.daily_loop_trash_retention_days, 90)   # bad value keeps default

    def test_bad_time_keeps_default_and_overrides_are_wired(self):
        cfg = config.Config()
        config._apply_daily_loop_block(cfg, {"daily_loop": {"time": "25:99"}})
        self.assertEqual(cfg.daily_loop_time, "03:30")
        for key in ("daily_loop_enabled", "daily_loop_time", "daily_loop_max_proposals_per_day",
                    "daily_loop_stale_days", "daily_loop_trash_retention_days"):
            self.assertIn(key, config._OVERRIDE_FIELDS)
        self.assertEqual(config._OVERRIDE_FIELDS["daily_loop_time"]("3:30"), "03:30")
        with self.assertRaises(ValueError):
            config._OVERRIDE_FIELDS["daily_loop_max_proposals_per_day"](-1)
        with self.assertRaises(ValueError):
            config._OVERRIDE_FIELDS["daily_loop_max_proposals_per_day"](True)
        with self.assertRaises(ValueError):
            config._OVERRIDE_FIELDS["daily_loop_time"]("noon")

    def test_overrides_file_wins_over_yaml_defaults(self):
        config.ensure_state_dirs()
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"daily_loop_max_proposals_per_day": 5, "daily_loop_time": "5:00", "daily_loop_enabled": false}',
            encoding="utf-8")
        try:
            cfg = config.load_config()
        finally:
            config.SETTINGS_OVERRIDES_PATH.unlink()
        self.assertEqual(cfg.daily_loop_max_proposals_per_day, 5)
        self.assertEqual(cfg.daily_loop_time, "05:00")
        self.assertFalse(cfg.daily_loop_enabled)


class ScheduleTestCase(unittest.TestCase):
    def _now(self, h, m, day=2):
        return _dt.datetime(2026, 9, day, h, m, tzinfo=_dt.timezone(_dt.timedelta(hours=-7)))

    def test_due_needs_unlock_time_and_not_yet_today(self):
        cfg = config.Config()
        self.assertFalse(daily_loop.due(cfg, {}, self._now(3, 29)))
        self.assertTrue(daily_loop.due(cfg, {}, self._now(3, 30)))
        self.assertTrue(daily_loop.due(cfg, {"last_run_day": "2026-09-01"}, self._now(23, 59)))
        self.assertFalse(daily_loop.due(cfg, {"last_run_day": "2026-09-02"}, self._now(23, 59)))
        cfg.daily_loop_enabled = False
        self.assertFalse(daily_loop.due(cfg, {}, self._now(12, 0)))

    def test_next_run_at(self):
        cfg = config.Config()
        nxt = daily_loop.next_run_at(cfg, {}, self._now(1, 0))
        self.assertEqual((nxt.day, nxt.hour, nxt.minute), (2, 3, 30))
        nxt = daily_loop.next_run_at(cfg, {"last_run_day": "2026-09-02"}, self._now(1, 0))
        self.assertEqual((nxt.day, nxt.hour, nxt.minute), (3, 3, 30))
        nxt = daily_loop.next_run_at(cfg, {}, self._now(4, 0))
        self.assertEqual(nxt.day, 3)
        cfg.daily_loop_enabled = False
        self.assertIsNone(daily_loop.next_run_at(cfg, {}, self._now(4, 0)))


if __name__ == "__main__":
    unittest.main()
