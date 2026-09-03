"""§65 每日维护——两列去重：同题簇合成一张新卡、旧卡进回收站可恢复（D10）。

钉住的行为：
- 只碰 detected / card_sent；approved / executing / review / delivered 永不入簇；
- 新卡 merged_from[] 列全部旧卡主键、sources 并集、mentions 累加、former_titles
  记旧名、每张旧卡一行带 [@ts] 句柄的 fold note、状态 = 有 card_sent 则 card_sent；
- 旧卡 reason `daily-merge: 并入 <new>`、prev_status 完整、restore 回原列；
- 恢复出的旧卡与新卡是 linked（auto_merge 永不建议并回）、卡对进终局台账；
- 血缘（improvement_of / thread）相连的卡不同簇；一簇失败不影响另一簇。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import auto_merge, config, maintenance, registry
from act.lib.registry import Requirement, State


def _mk(rid, title, status=State.DETECTED.value, **kw):
    fields = dict(id=rid, title=title, status=status, type="engineering",
                  sources=[{"channel": "meeting", "date": "2026-08-01", "quote": title}])
    fields.update(kw)
    req = Requirement(**fields)
    registry.save(req)
    return req


class _Sandbox(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        auto_merge.STATE_PATH.unlink(missing_ok=True)
        self.cfg = config.Config()


class FindClustersTestCase(_Sandbox):
    def test_same_normalized_title_forms_a_cluster_within_the_two_lanes(self):
        _mk("P-1", "邮件截止日期雷达 for job pipeline")
        _mk("P-2", "邮件截止日期雷达 for job pipeline", status=State.CARD_SENT.value)
        _mk("P-3", "完全不同的另一件事 about taxes")
        clusters = maintenance.find_clusters(registry.load_all(), self.cfg)
        self.assertEqual([[r.id for r in c] for c in clusters], [["P-1", "P-2"]])

    def test_invested_lanes_never_join_a_cluster(self):
        _mk("P-1", "邮件截止日期雷达 for job pipeline")
        for i, st in enumerate((State.APPROVED.value, State.EXECUTING.value,
                                State.REVIEW.value, State.DELIVERED.value), start=2):
            _mk(f"P-{i}", "邮件截止日期雷达 for job pipeline", status=st)
        self.assertEqual(maintenance.find_clusters(registry.load_all(), self.cfg), [])

    def test_lineage_linked_cards_are_not_duplicates(self):
        _mk("P-1", "邮件截止日期雷达 for job pipeline")
        _mk("P-2", "邮件截止日期雷达 for job pipeline", improvement_of="P-1")
        _mk("P-3", "邮件截止日期雷达 for job pipeline", thread_id="P-1")
        _mk("P-4", "邮件截止日期雷达 for job pipeline", split_from="P-1")
        clusters = maintenance.find_clusters(registry.load_all(), self.cfg)
        # P-2/P-3/P-4 are each linked to P-1 but not to one another by
        # lineage; the union-find still keeps them apart from P-1.
        for c in clusters:
            self.assertNotIn("P-1", [r.id for r in c])

    def test_preset_cards_and_short_titles_stay_out(self):
        _mk("P-1", "跟进", status=State.CARD_SENT.value)
        _mk("P-2", "跟进", status=State.CARD_SENT.value)
        _mk("P-3", "清理提案积压 proposals triage", preset="proposals_triage")
        _mk("P-4", "清理提案积压 proposals triage", preset="proposals_triage")
        self.assertEqual(maintenance.find_clusters(registry.load_all(), self.cfg), [])

    def test_near_dupe_high_signal_clusters_but_contact_rule_does_not(self):
        a = _mk("P-1", "prepare quarterly roadmap deck for leadership review meeting")
        b = _mk("P-2", "prepare the quarterly roadmap deck for the leadership review")
        with mock.patch.object(auto_merge, "is_near_dupe", return_value=(True, ["x"], "contact")):
            self.assertEqual(maintenance.find_clusters([a, b], self.cfg), [])
        with mock.patch.object(auto_merge, "is_near_dupe", return_value=(True, ["x"], "high")):
            self.assertEqual(len(maintenance.find_clusters([a, b], self.cfg)), 1)


class ApplyMergeTestCase(_Sandbox):
    def _cluster(self):
        a = _mk("P-1", "邮件截止日期雷达 for job pipeline", repeated_mentions=2,
                summary="第一张", plan=["a"], deadline="2026-12-01",
                sources=[{"channel": "meeting", "date": "2026-08-01", "quote": "q1"}])
        b = _mk("P-2", "邮件截止日期雷达 for job pipeline", status=State.CARD_SENT.value,
                repeated_mentions=3, summary="第二张", hardness="hard",
                display_title="截止日期雷达", deadline="2026-11-01",
                sources=[{"channel": "gmail", "date": "2026-08-02", "quote": "q2"},
                         {"channel": "meeting", "date": "2026-08-01", "quote": "q1"}])
        return [a, b]

    def test_one_new_card_with_full_lineage_and_olds_in_trash(self):
        cluster = self._cluster()
        result = maintenance.apply_merge(cluster)
        new = registry.load(result["new"])
        self.assertIsNotNone(new)
        self.assertEqual(new.merged_from, ["P-1", "P-2"])
        self.assertEqual(new.status, State.CARD_SENT.value)        # any card_sent → card_sent
        self.assertEqual(new.repeated_mentions, 5)                 # 2 + 3
        self.assertEqual(new.hardness, "hard")
        self.assertEqual(new.deadline, "2026-11-01")               # earliest
        self.assertEqual(len(new.sources), 2)                      # deduped union
        self.assertEqual(new.origin_trust, "external")             # gmail source → min trust (§50)
        self.assertEqual(new.display_title, "截止日期雷达")           # primary = most mentions
        self.assertEqual(new.thread_id, "P-1")                     # oldest card's thread root
        self.assertEqual(new.plan, ["a"])
        notes = registry.parse_fold_notes(new.notes)
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(n["ts"] for n in notes))               # every line has a split handle
        self.assertIn("每日整理并入 P-1「邮件截止日期雷达 for job pipeline」：第一张", notes[0]["text"])
        for old_id in ("P-1", "P-2"):
            old = registry.load(old_id)
            self.assertEqual(old.status, State.TRASHED.value)
            self.assertEqual(old.trash_reason, f"daily-merge: 并入 {new.id}")
        self.assertEqual(registry.load("P-1").prev_status, State.DETECTED.value)
        self.assertEqual(registry.load("P-2").prev_status, State.CARD_SENT.value)
        self.assertTrue(maintenance.is_loop_trash(registry.load("P-1")))

    def test_former_titles_keep_the_olds_searchable(self):
        cluster = self._cluster()
        new = registry.load(maintenance.apply_merge(cluster)["new"])
        self.assertIn("邮件截止日期雷达 for job pipeline", new.former_titles)

    def test_restore_puts_an_old_back_and_it_stays_linked_to_the_new_card(self):
        new_id = maintenance.apply_merge(self._cluster())["new"]
        old = registry.restore(registry.load("P-2"))
        self.assertEqual(old.status, State.CARD_SENT.value)
        self.assertIsNone(old.trash_reason)
        new = registry.load(new_id)
        self.assertTrue(auto_merge.linked(new, old))
        self.assertTrue(auto_merge.linked(old, new))
        # the pair is final in the §38.3 ledger: no silent-merge suggestion ever
        seen = auto_merge._load_state()
        self.assertIn(auto_merge.pair_key(new_id, "P-2"), seen.get("suggested") or [])
        # and the daily dedup itself does not re-merge the restored card
        self.assertEqual(maintenance.find_clusters(registry.load_all(), self.cfg), [])

    def test_dedup_lanes_isolates_a_failing_cluster(self):
        self._cluster()
        _mk("P-7", "another duplicated topic about invoices", status=State.CARD_SENT.value)
        _mk("P-8", "another duplicated topic about invoices", status=State.CARD_SENT.value)
        real = maintenance.apply_merge

        def flaky(cluster):
            if cluster[0].id == "P-1":
                raise RuntimeError("boom")
            return real(cluster)

        with mock.patch.object(maintenance, "apply_merge", side_effect=flaky):
            out = maintenance.dedup_lanes(self.cfg)
        self.assertEqual([m["from"] for m in out], [["P-7", "P-8"]])
        self.assertEqual(registry.load("P-1").status, State.DETECTED.value)   # untouched

    def test_user_titled_card_wins_the_display_title(self):
        _mk("P-1", "邮件截止日期雷达 for job pipeline", repeated_mentions=9)
        _mk("P-2", "邮件截止日期雷达 for job pipeline", display_title="我的名字", user_titled=True)
        new = registry.load(maintenance.apply_merge(registry.load_all())["new"])
        self.assertEqual(new.display_title, "我的名字")
        self.assertTrue(new.user_titled)


class Store2BackendTestCase(unittest.TestCase):
    """§53 真源下同样成立：合成 + 旧卡 →trashed（system 白名单行）+ 过时 + 铸提案，
    双后端逐字一致（tests/test_registry_backend_parity 的纪律）。"""

    def _round_trip(self):
        _mk("P-1", "邮件截止日期雷达 for job pipeline")
        _mk("P-2", "邮件截止日期雷达 for job pipeline", status=State.CARD_SENT.value)
        _mk("P-3", "an old idle card nobody touched for a while",
            sources=[{"channel": "meeting", "date": "2026-01-01", "quote": "q"}])
        merges = maintenance.dedup_lanes(config.Config())
        stale = maintenance.sweep_stale(config.Config())
        from act.lib import daily_loop
        from act.lib.loop_inputs import Signal
        filed = daily_loop.file_proposals([Signal(kind="doctor_fail", fingerprint="doctor_fail:x",
                                                  title="doctor 红灯：x", summary="s", plan=["p"], dod=["d"])],
                                          "2026-09-02", "/repo")
        new = registry.load(merges[0]["new"])
        return merges, stale, filed, new

    def test_sqlite_and_yaml_agree(self):
        from tests import store2_testkit
        outcomes = {}
        for backend in ("yaml", "sqlite"):
            with self.subTest(backend=backend):
                store2_testkit.use_backend(self, backend)
                auto_merge.STATE_PATH.unlink(missing_ok=True)
                merges, stale, filed, new = self._round_trip()
                self.assertEqual(merges[0]["from"], ["P-1", "P-2"])
                self.assertEqual(new.merged_from, ["P-1", "P-2"])
                self.assertEqual(registry.load("P-1").status, State.TRASHED.value)
                self.assertEqual(registry.load("P-1").trash_reason, f"daily-merge: 并入 {new.id}")
                self.assertEqual([x["rule"] for x in stale], ["idle"])
                self.assertEqual(registry.load("P-3").trash_reason, "stale:idle")
                self.assertEqual(filed[0]["outcome"], "proposed")
                card = registry.load(filed[0]["id"])
                self.assertEqual(card.sources[0]["channel"], "self_improve")
                with registry.acting_as("user"):        # restore 是 owner 的 inbox 动作（§9 白名单 user 行）
                    restored = registry.restore(registry.load("P-2"))
                self.assertEqual(restored.status, State.CARD_SENT.value)
                outcomes[backend] = (new.to_dict() | {"id": "X"}, card.to_dict() | {"id": "Y"})
        # same shapes on both backends (ids/timestamps aside)
        for a, b in zip(outcomes["yaml"], outcomes["sqlite"]):
            a_d, b_d = dict(a), dict(b)
            for d in (a_d, b_d):
                d.pop("notes", None)
                d.pop("sources", None)
            self.assertEqual(a_d, b_d)


if __name__ == "__main__":
    unittest.main()
