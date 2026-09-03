"""§70 每日维护——过时卡进回收站的确定性规则与保护罩（D10；Q4：45 天 + guards）。

规则：deadline_passed / diagnostic_expired / superseded / idle；guards：不在两列、
preset、user_titled、未来 deadline、同簇有 approved/executing/review 兄弟、提及
≥3（只挡 idle）、活动时间解析不了 = 不动。回收站 reason `stale:<rule>`，
prev_status 完整；循环卡的保留期 = daily_loop.trash_retention_days（默认 90），
purge_at 投影与 purge_due 同一判决；trash.retention_days = 0 总开关。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, dashboard, maintenance, registry
from act.lib.registry import Requirement, State

TODAY = _dt.date(2026, 9, 2)


def _days_ago(n: int) -> str:
    return (TODAY - _dt.timedelta(days=n)).isoformat()


def _card(rid, *, age=60, status=State.DETECTED.value, channel="meeting", **kw):
    fields = dict(id=rid, title=f"topic {rid} with enough length", status=status,
                  sources=[{"channel": channel, "date": _days_ago(age), "quote": "q"}])
    fields.update(kw)
    return Requirement(**fields)


class StaleVerdictTestCase(unittest.TestCase):
    def verdict(self, req, others=()):
        return maintenance.stale_verdict(req, [req, *others], TODAY, 45)

    def test_idle_beyond_stale_days_is_stale_and_within_is_not(self):
        self.assertEqual(self.verdict(_card("P-1", age=46)), "idle")
        self.assertIsNone(self.verdict(_card("P-2", age=45)))
        self.assertIsNone(self.verdict(_card("P-3", age=10)))

    def test_latest_activity_wins_over_the_oldest_source(self):
        req = _card("P-1", age=200)
        registry.append_fold_note(req, "刚刚有人又提了一次", "radar")   # today's handle
        self.assertIsNone(self.verdict(req))

    def test_unparseable_activity_is_left_alone(self):
        req = _card("P-1", sources=[{"channel": "meeting", "date": "someday", "quote": "q"}])
        self.assertIsNone(self.verdict(req))
        self.assertIsNone(self.verdict(_card("P-2", sources=[])))

    def test_gmail_rfc2822_dates_count_as_activity(self):
        req = _card("P-1", sources=[{"channel": "gmail", "date": "Mon, 03 Mar 2025 10:00:00 +0000"}])
        self.assertEqual(self.verdict(req), "idle")

    def test_guards_protect_invested_cards(self):
        self.assertIsNone(self.verdict(_card("P-1", age=90, user_titled=True, display_title="x")))
        self.assertIsNone(self.verdict(_card("P-2", age=90, preset="proposals_triage")))
        self.assertIsNone(self.verdict(_card("P-3", age=90, deadline=(TODAY + _dt.timedelta(days=3)).isoformat())))
        self.assertIsNone(self.verdict(_card("P-4", age=90, repeated_mentions=3)))
        for st in (State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value, State.DELIVERED.value):
            self.assertIsNone(self.verdict(_card("P-5", age=90, status=st)))

    def test_live_sibling_in_cluster_protects(self):
        req = _card("P-1", age=90, thread_id="P-1")
        running = _card("P-2", age=1, status=State.EXECUTING.value, thread_id="P-1")
        self.assertIsNone(self.verdict(req, [running]))
        child = _card("P-3", age=1, status=State.APPROVED.value, improvement_of="P-1")
        self.assertIsNone(self.verdict(req, [child]))

    def test_deadline_passed_beats_idle_and_needs_a_quiet_week(self):
        past = (TODAY - _dt.timedelta(days=10)).isoformat()
        self.assertEqual(self.verdict(_card("P-1", age=8, deadline=past)), "deadline_passed")
        # mentioned again 2 days ago → the matter moved, the deadline alone is not enough
        self.assertIsNone(self.verdict(_card("P-2", age=2, deadline=past)))
        # deadline passed only yesterday → grace period
        self.assertIsNone(self.verdict(_card("P-3", age=8, deadline=_days_ago(1))))
        # many mentions do not protect a moot deadline (only idle is guarded by mentions)
        self.assertEqual(self.verdict(_card("P-4", age=8, deadline=past, repeated_mentions=12)), "deadline_passed")

    def test_diagnostic_cards_expire_after_two_weeks(self):
        self.assertEqual(self.verdict(_card("P-1", age=15, channel="radar-diagnostic")), "diagnostic_expired")
        self.assertIsNone(self.verdict(_card("P-2", age=13, channel="radar-parse-degraded")))
        mixed = _card("P-3", age=15, sources=[{"channel": "radar-diagnostic", "date": _days_ago(15)},
                                             {"channel": "meeting", "date": _days_ago(15)}])
        self.assertIsNone(self.verdict(mixed))

    def test_superseded_by_a_delivered_twin(self):
        req = _card("P-1", age=5, title="ship the weekly export job")
        done = _card("P-2", age=1, title="ship the weekly export job", status=State.DELIVERED.value)
        self.assertEqual(self.verdict(req, [done]), "superseded")
        archived = _card("P-3", age=1, title="ship the weekly export job", status=State.ARCHIVED.value)
        self.assertEqual(self.verdict(req, [archived]), "superseded")
        # an increment child of a delivered parent shares the title on purpose
        child = _card("P-4", age=5, title="ship the weekly export job", improvement_of="P-2")
        self.assertIsNone(self.verdict(child, [done]))

    def test_stale_days_zero_disables_idle_only(self):
        self.assertIsNone(maintenance.stale_verdict(_card("P-1", age=400), [], TODAY, 0))
        past = (TODAY - _dt.timedelta(days=10)).isoformat()
        self.assertEqual(maintenance.stale_verdict(_card("P-2", age=8, deadline=past), [], TODAY, 0),
                         "deadline_passed")


class SweepStaleTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()

    def test_sweep_trashes_with_rule_token_and_keeps_prev_status(self):
        registry.save(_card("P-1", age=60))
        registry.save(_card("P-2", age=60, status=State.CARD_SENT.value))
        registry.save(_card("P-3", age=1))
        out = maintenance.sweep_stale(self.cfg, today=TODAY)
        self.assertEqual(sorted(x["id"] for x in out), ["P-1", "P-2"])
        self.assertEqual({x["rule"] for x in out}, {"idle"})
        p2 = registry.load("P-2")
        self.assertEqual(p2.status, State.TRASHED.value)
        self.assertEqual(p2.trash_reason, "stale:idle")
        self.assertEqual(p2.prev_status, State.CARD_SENT.value)
        self.assertEqual(registry.load("P-3").status, State.DETECTED.value)
        restored = registry.restore(registry.load("P-2"))
        self.assertEqual(restored.status, State.CARD_SENT.value)

    def test_sweep_never_touches_running_or_review(self):
        registry.save(_card("P-1", age=90, status=State.EXECUTING.value))
        registry.save(_card("P-2", age=90, status=State.REVIEW.value))
        self.assertEqual(maintenance.sweep_stale(self.cfg, today=TODAY), [])


class LoopTrashRetentionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()   # trash 60 d, loop 90 d

    def _trashed(self, rid, reason, days_ago):
        ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        req = Requirement(id=rid, title=rid, status=State.TRASHED.value, trashed_at=ts,
                          trash_reason=reason, prev_status="detected")
        registry.save(req)
        return req

    def test_loop_trash_gets_the_longer_window(self):
        self.assertEqual(maintenance.retention_days(self._trashed("P-1", "deleted", 1), self.cfg), 60)
        self.assertEqual(maintenance.retention_days(self._trashed("P-2", "stale:idle", 1), self.cfg), 90)
        self.assertEqual(maintenance.retention_days(self._trashed("P-3", "daily-merge: 并入 P-9", 1), self.cfg), 90)

    def test_purge_respects_per_row_retention_and_countdown_agrees(self):
        owner = self._trashed("P-1", "deleted", 70)
        loop_young = self._trashed("P-2", "stale:idle", 70)
        self._trashed("P-3", "stale:deadline_passed", 91)
        self.assertEqual(actd.purge_trash(self.cfg), 2)
        self.assertIsNone(registry.load("P-1"))
        self.assertIsNotNone(registry.load("P-2"))
        self.assertIsNone(registry.load("P-3"))
        # §40.5: the projected countdown is derived from the same judge
        at = dashboard._purge_at(loop_young, self.cfg)
        self.assertIsNotNone(at)
        self.assertTrue(at > _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        owner_at = dashboard._purge_at(owner, self.cfg)
        self.assertIsNotNone(owner_at)
        self.assertTrue(owner_at < _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_master_switch_off_keeps_loop_trash_too(self):
        self._trashed("P-1", "stale:idle", 400)
        self.cfg.trash_retention_days = 0
        self.assertEqual(actd.purge_trash(self.cfg), 0)
        self.assertIsNone(dashboard._purge_at(registry.load("P-1"), self.cfg))

    def test_pinned_and_unparseable_never_purge(self):
        pinned = self._trashed("P-1", "stale:idle", 400)
        pinned.permanent = True
        registry.save(pinned)
        bad = self._trashed("P-2", "stale:idle", 400)
        bad.trashed_at = "not a date"
        registry.save(bad)
        self.assertEqual(actd.purge_trash(self.cfg), 0)
        self.assertIsNone(dashboard._purge_at(pinned, self.cfg))
        self.assertIsNone(dashboard._purge_at(bad, self.cfg))


if __name__ == "__main__":
    unittest.main()
