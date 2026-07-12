"""卡片生命周期 v0.20.0 — thread-level 语义匹配 + archived state + re-raise-to-提案.

Pins the §7 test list from the implementation spec (worktree A · py-core):

 1. matching exclusion: archived cards never match merge_or_new + are absent
    from the triage/capture LLM inventory;
 2. id-collision regression (critique #1, HIGHEST risk): archiving R-050 must
    NOT let next_id() reissue R-050 and overwrite it; load() still finds it;
 3. re-raise same_task: an unarchived delivered card + a new actionable ask +
    an aligned title flips the ORIGINAL card back to card_sent (reraised_at +
    summary「新增」); an archived hit opens a fresh detected card instead;
 4. re-raise different task (critique #2): a thread_key hit on a delivered card
    with a DIFFERENT title opens a card_sent follow-up inheriting thread_id —
    the old card's status + title are untouched;
 5. pure restatement (critique #3): resolved parent + not-actionable
    (needs_action=false / no increment) bumps mentions but does NOT flip —
    consistent across the deterministic and LLM paths;
 6. live-work protection: a canonical primary that is executing/review/approved
    only folds (never flips to card_sent); a canonical dead-end on a
    trashed/rejected primary re-cards from scratch;
 7. open follow-up coexistence (critique medium): a delivered card with an
    already-open follow-up folds a later hit into it — never a second proposal;
 8. archive/unarchive inbox actions: delivered/detected -> archived; illegal
    states are idempotent no-ops; unarchive restores prev_status + relocates;
 9. archive_stale: default off (archive_after_days=0); when enabled it archives
    only cold delivered cards (skips future-deadline / live-sibling / recent),
    relocates, and self-gates to once per 24h;
10. dashboard: archived cards enter ONLY archived[] (not completed/debt/
    needs_approval), counts.archived is the true total, the build-loop skip
    guard holds, and the「回锅」reraised flag is projected;
11. backward compat: legacy YAML with no thread/archive fields round-trips
    byte-stable and loads without the new keys leaking in;
12. loop safety: two candidates on the same archived thread in one batch yield
    one fresh card + a fold, never two;
13. inventory pin: a delivered card stays pinned into the LLM window even when
    the registry outgrows the cap.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, dashboard, quick_capture, registry
from act.lib.registry import Requirement, State


def _iso_days_ago(n: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _src(channel="slack", date="2026-07-02", quote="q", **extra):
    s = {"who": "quinton", "channel": channel, "date": date, "quote": quote}
    s.update(extra)
    return s


class LifecycleBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._wipe()
        self.addCleanup(self._wipe)
        self.cfg = config.Config()

    def _wipe(self):
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        if registry.ARCHIVE_DIR.exists():
            for p in registry.ARCHIVE_DIR.glob("*.yaml"):
                p.unlink()
        marker = config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER
        if marker.exists():
            marker.unlink()

    def _seed(self, rid, title, status, **kw):
        kw.setdefault("sources", [_src()])
        r = Requirement(id=rid, title=title, status=status, **kw)
        registry.save(r)
        return registry.load(rid)

    def _active_ids(self):
        return sorted(r.id for r in registry.load_all())


# --------------------------------------------------------------------------- #
# 1 + 2: archive exclusion + id-collision regression
# --------------------------------------------------------------------------- #
class ArchiveExclusionAndCollisionTestCase(LifecycleBase):
    def test_archived_never_matches_and_is_absent_from_inventory(self):
        r = self._seed("R-050", "Ship the quarterly report", State.DELIVERED.value)
        registry.archive(r, "user")
        # excluded from matching -> a restatement opens a fresh card
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        sources=[_src(channel="meeting")]))
        self.assertNotEqual(got.id, "R-050")
        self.assertEqual(got.status, State.DETECTED.value)
        self.assertEqual(registry.load("R-050").status, State.ARCHIVED.value)
        # invisible to the triage LLM inventory
        self.assertNotIn("R-050", quick_capture.registry_inventory_text())

    def test_next_id_and_load_scan_archive_dir(self):
        r = self._seed("R-050", "sealed", State.DELIVERED.value)
        registry.archive(r, "user")
        # CRITICAL: id must not be reissued (would overwrite the archived card)
        self.assertEqual(registry.next_id(), "R-051")
        # load still resolves the archived card (unarchive path depends on it)
        loaded = registry.load("R-050")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, State.ARCHIVED.value)

    def test_archive_relocates_file_to_subdir(self):
        r = self._seed("R-050", "sealed", State.DELIVERED.value)
        registry.archive(r, "user")
        self.assertFalse((config.REGISTRY_DIR / "R-050.yaml").exists())
        self.assertTrue((registry.ARCHIVE_DIR / "R-050.yaml").exists())
        # default load_all (hot scan) skips the archive subdir
        self.assertEqual(registry.load_all(), [])
        self.assertEqual([x.id for x in registry.load_archived()], ["R-050"])


# --------------------------------------------------------------------------- #
# derive_thread_key contract (B + C depend on this exact signature)
# --------------------------------------------------------------------------- #
class DeriveThreadKeyTestCase(LifecycleBase):
    def test_gmail_slack_and_none(self):
        self.assertEqual(
            registry.derive_thread_key({"gmail_thread_id": "ABC"}), "gmail:ABC")
        self.assertEqual(
            registry.derive_thread_key({"slack_thread_ts": "17.42"}), "slack:17.42")
        # gmail wins when both present
        self.assertEqual(
            registry.derive_thread_key(
                {"gmail_thread_id": "G", "slack_thread_ts": "S"}), "gmail:G")
        self.assertIsNone(registry.derive_thread_key({"who": "x"}))
        self.assertIsNone(registry.derive_thread_key(None))


# --------------------------------------------------------------------------- #
# 3: re-raise same_task (deterministic + LLM) + archived hit re-cards
# --------------------------------------------------------------------------- #
class ReraiseSameTaskTestCase(LifecycleBase):
    def test_deterministic_same_task_increment_flips_original(self):
        self._seed("R-100", "Ship the quarterly report", State.DELIVERED.value,
                   deadline="2026-08-01", execution={"accepted_at": _iso_days_ago(3)})
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        summary="need it a week earlier", deadline="2026-07-15",
                        sources=[_src(channel="meeting")]))
        self.assertEqual(got.id, "R-100")                       # in-place
        self.assertEqual(got.status, State.CARD_SENT.value)     # 翻回提案
        self.assertTrue((got.execution or {}).get("reraised_at"))
        self.assertIn("新增", got.summary)
        self.assertEqual(self._active_ids(), ["R-100"])         # no second card

    def test_llm_same_task_flips_and_returns_reraised(self):
        self._seed("R-019", "Ship the quarterly report", State.DELIVERED.value)
        cand = Requirement(id=registry.next_id(), title="Ship the quarterly report",
                           summary="manager wants it now", status=State.CARD_SENT.value,
                           sources=[_src()])
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "manager escalated", "needs_action": True},
            cand, self.cfg)
        self.assertEqual(kind, "reraised")
        self.assertEqual(saved.id, "R-019")
        self.assertEqual(saved.status, State.CARD_SENT.value)
        self.assertTrue((saved.execution or {}).get("reraised_at"))
        self.assertEqual(saved.execution.get("reraised_note"), "manager escalated")

    def test_archived_hit_recards_not_reraise(self):
        r = self._seed("R-019", "Ship the quarterly report", State.DELIVERED.value)
        registry.archive(r, "user")
        cand = Requirement(id=registry.next_id(), title="Ship the quarterly report",
                           summary="again", status=State.CARD_SENT.value,
                           sources=[_src()])
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019", "needs_action": True},
            cand, self.cfg)
        self.assertEqual(kind, "proposed")                      # fresh card
        self.assertNotEqual(saved.id, "R-019")
        self.assertEqual(registry.load("R-019").status, State.ARCHIVED.value)


# --------------------------------------------------------------------------- #
# 4: re-raise different task -> thread-lineage follow-up child, old card intact
# --------------------------------------------------------------------------- #
class ReraiseDifferentTaskTestCase(LifecycleBase):
    def test_thread_key_hit_different_title_makes_child(self):
        self._seed("R-020", "Submit recommendation letters", State.DELIVERED.value,
                   thread_key="gmail:THREAD1",
                   sources=[_src(channel="gmail", gmail_thread_id="THREAD1")])
        # new task in the SAME gmail thread (thread_key derived from the source)
        got = registry.merge_or_new(
            Requirement(id="", title="Reply to the USCIS RFE",
                        summary="draft the RFE response",
                        sources=[_src(channel="gmail", gmail_thread_id="THREAD1")]))
        self.assertNotEqual(got.id, "R-020")
        self.assertEqual(got.status, State.CARD_SENT.value)
        self.assertEqual(got.improvement_of, "R-020")
        self.assertEqual(got.thread_id, "R-020")                # inherited lineage
        # the old card's title + status are NOT polluted
        parent = registry.load("R-020")
        self.assertEqual(parent.status, State.DELIVERED.value)
        self.assertEqual(parent.title, "Submit recommendation letters")


# --------------------------------------------------------------------------- #
# 5: pure restatement gate — bump, never flip (both paths)
# --------------------------------------------------------------------------- #
class PureRestatementTestCase(LifecycleBase):
    def test_deterministic_restatement_bumps_not_flips(self):
        self._seed("R-100", "Ship the quarterly report", State.DELIVERED.value,
                   sources=[_src(channel="meeting", date="2026-07-01")])
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        sources=[_src(channel="slack", date="2026-07-02")]))
        self.assertEqual(got.id, "R-100")
        self.assertEqual(got.status, State.DELIVERED.value)     # NOT flipped
        self.assertIsNone((got.execution or {}).get("reraised_at"))
        self.assertEqual(got.repeated_mentions, 2)              # bumped
        self.assertEqual(self._active_ids(), ["R-100"])

    def test_llm_needs_action_false_folds_not_flips(self):
        self._seed("R-019", "Ship the quarterly report", State.DELIVERED.value)
        cand = Requirement(id=registry.next_id(), title="unrelated wording",
                           status=State.CARD_SENT.value, sources=[_src()])
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "just an FYI update", "needs_action": False},
            cand, self.cfg)
        self.assertEqual(kind, "folded")
        self.assertEqual(saved.id, "R-019")
        self.assertEqual(saved.status, State.DELIVERED.value)   # NOT flipped
        self.assertEqual(self._active_ids(), ["R-019"])         # no new card


# --------------------------------------------------------------------------- #
# 6: live-work protection + canonical dead-end
# --------------------------------------------------------------------------- #
class LiveWorkProtectionTestCase(LifecycleBase):
    def test_canonical_live_primary_only_folds(self):
        # merged duplicate R-010 whose canonical primary R-011 is EXECUTING
        self._seed("R-011", "Ship the quarterly report", State.EXECUTING.value)
        self._seed("R-010", "Ship the quarterly report", State.MERGED.value,
                   merged_into="R-011")
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        summary="poke", deadline="2020-01-01",  # would be an increment
                        sources=[_src(channel="meeting")]))
        # folded into the live primary; NEVER pulled back to card_sent
        self.assertEqual(got.id, "R-011")
        self.assertEqual(got.status, State.EXECUTING.value)
        self.assertEqual(registry.load("R-010").status, State.MERGED.value)
        self.assertEqual(self._active_ids(), ["R-010", "R-011"])

    def test_canonical_dead_end_recards(self):
        # merged duplicate R-010 whose canonical primary R-011 is TRASHED
        self._seed("R-011", "Ship the quarterly report", State.TRASHED.value)
        self._seed("R-010", "Ship the quarterly report", State.MERGED.value,
                   merged_into="R-011")
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        sources=[_src(channel="meeting")]))
        self.assertNotIn(got.id, ("R-010", "R-011"))            # fresh card
        self.assertEqual(got.status, State.DETECTED.value)


# --------------------------------------------------------------------------- #
# 7: open follow-up coexistence — fold, never a second proposal
# --------------------------------------------------------------------------- #
class OpenFollowUpCoexistTestCase(LifecycleBase):
    def test_hit_folds_into_open_follow_up(self):
        self._seed("R-010", "Ship the quarterly report", State.DELIVERED.value)
        self._seed("R-045", "follow-up already open", State.CARD_SENT.value,
                   improvement_of="R-010")
        cand = Requirement(id=registry.next_id(), title="Ship the quarterly report",
                           summary="another mention", status=State.CARD_SENT.value,
                           sources=[_src(channel="DM", date="2026-07-09")])
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-010",
             "note": "same event again", "needs_action": True},
            cand, self.cfg)
        self.assertEqual(kind, "folded")
        self.assertEqual(saved.id, "R-045")                     # folded into it
        # exactly one follow-up; parent untouched
        fus = [r for r in registry.load_all() if r.improvement_of == "R-010"]
        self.assertEqual(len(fus), 1)
        self.assertEqual(registry.load("R-010").status, State.DELIVERED.value)


# --------------------------------------------------------------------------- #
# 8: archive / unarchive inbox actions
# --------------------------------------------------------------------------- #
class ArchiveUnarchiveActionTestCase(LifecycleBase):
    def test_delivered_and_detected_archive(self):
        for rid, status in (("R-100", State.DELIVERED.value),
                            ("R-101", State.DETECTED.value)):
            r = self._seed(rid, "t", status)
            actd._apply_decision(r, "archive", None)
            self.assertEqual(registry.load(rid).status, State.ARCHIVED.value)
            self.assertEqual(registry.load(rid).prev_status, status)

    def test_illegal_state_archive_is_noop(self):
        r = self._seed("R-102", "t", State.CARD_SENT.value)
        actd._apply_decision(r, "archive", None)
        self.assertEqual(registry.load("R-102").status, State.CARD_SENT.value)

    def test_unarchive_restores_prev_status_and_relocates(self):
        r = self._seed("R-100", "t", State.DELIVERED.value)
        actd._apply_decision(r, "archive", None)
        arch = registry.load("R-100")
        actd._apply_decision(arch, "unarchive", None)
        back = registry.load("R-100")
        self.assertEqual(back.status, State.DELIVERED.value)
        self.assertIsNone(back.archived_at)
        self.assertIsNone(back.archive_reason)
        self.assertIsNone(back.prev_status)
        self.assertTrue((config.REGISTRY_DIR / "R-100.yaml").exists())
        self.assertFalse((registry.ARCHIVE_DIR / "R-100.yaml").exists())

    def test_unarchive_on_non_archived_is_noop(self):
        r = self._seed("R-100", "t", State.DELIVERED.value)
        actd._apply_decision(r, "unarchive", None)
        self.assertEqual(registry.load("R-100").status, State.DELIVERED.value)


# --------------------------------------------------------------------------- #
# 9: archive_stale — default off + skip guards + daily gate
# --------------------------------------------------------------------------- #
class ArchiveStaleTestCase(LifecycleBase):
    def test_disabled_by_default(self):
        self._seed("R-100", "t", State.DELIVERED.value,
                   execution={"accepted_at": _iso_days_ago(400)})
        self.assertEqual(actd.archive_stale(self.cfg), 0)       # no attr -> off
        self.assertEqual(registry.load("R-100").status, State.DELIVERED.value)

    def _cfg_days(self, days):
        cfg = config.Config()
        setattr(cfg, "archive_after_days", days)
        return cfg

    def test_enabled_archives_only_cold_delivered(self):
        cfg = self._cfg_days(30)
        self._seed("R-100", "cold", State.DELIVERED.value,
                   execution={"accepted_at": _iso_days_ago(90)})
        self._seed("R-101", "recent", State.DELIVERED.value,
                   execution={"accepted_at": _iso_days_ago(2)})
        self._seed("R-102", "future deadline", State.DELIVERED.value,
                   deadline="2099-01-01",
                   execution={"accepted_at": _iso_days_ago(90)})
        n = actd.archive_stale(cfg)
        self.assertEqual(n, 1)
        self.assertEqual(registry.load("R-100").status, State.ARCHIVED.value)
        self.assertEqual(registry.load("R-100").archive_reason, "auto")
        self.assertEqual(registry.load("R-101").status, State.DELIVERED.value)
        self.assertEqual(registry.load("R-102").status, State.DELIVERED.value)
        self.assertTrue((registry.ARCHIVE_DIR / "R-100.yaml").exists())

    def test_live_sibling_blocks_archive(self):
        cfg = self._cfg_days(30)
        self._seed("R-100", "cold", State.DELIVERED.value, thread_id="R-100",
                   execution={"accepted_at": _iso_days_ago(90)})
        self._seed("R-101", "live sibling", State.CARD_SENT.value, thread_id="R-100")
        self.assertEqual(actd.archive_stale(cfg), 0)
        self.assertEqual(registry.load("R-100").status, State.DELIVERED.value)

    def test_daily_gate_prevents_second_sweep(self):
        cfg = self._cfg_days(30)
        self._seed("R-100", "cold", State.DELIVERED.value,
                   execution={"accepted_at": _iso_days_ago(90)})
        self.assertEqual(actd.archive_stale(cfg), 1)
        # a newly cold card in the same 24h window is NOT swept again
        self._seed("R-101", "cold2", State.DELIVERED.value,
                   execution={"accepted_at": _iso_days_ago(90)})
        self.assertEqual(actd.archive_stale(cfg), 0)
        self.assertEqual(registry.load("R-101").status, State.DELIVERED.value)


# --------------------------------------------------------------------------- #
# 10: dashboard projection
# --------------------------------------------------------------------------- #
class DashboardArchivedTestCase(LifecycleBase):
    def _build(self, reqs, archived):
        return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg,
                                         archived=archived)

    def test_archived_partition_and_counts(self):
        delivered = Requirement(id="R-1", title="done", status=State.DELIVERED.value,
                                execution={"accepted_at": _iso_days_ago(1)})
        debt = Requirement(id="R-2", title="backlog", status=State.DETECTED.value)
        arch = Requirement(id="R-3", title="sealed", status=State.ARCHIVED.value,
                           prev_status=State.DELIVERED.value, archived_at=_iso_days_ago(1),
                           archive_reason="user")
        dash = self._build([delivered, debt], [arch])
        self.assertEqual([a["id"] for a in dash["archived"]], ["R-3"])
        self.assertEqual(dash["archived"][0]["archive_reason"], "user")
        self.assertEqual(dash["counts"]["archived"], 1)
        # not in any board lane
        for lane in ("needs_approval", "running", "review", "completed", "debt", "trash"):
            self.assertNotIn("R-3", [x["id"] for x in dash[lane]])

    def test_build_loop_skip_guard_for_leaked_archived(self):
        leaked = Requirement(id="R-9", title="leaked", status=State.ARCHIVED.value)
        dash = self._build([leaked], [])
        for lane in ("needs_approval", "running", "review", "completed", "debt", "trash"):
            self.assertNotIn("R-9", [x["id"] for x in dash[lane]])

    def test_reraised_flag_projected(self):
        r = Requirement(id="R-5", title="回锅", status=State.CARD_SENT.value,
                        execution={"reraised_at": _iso_days_ago(0),
                                   "reraised_note": "new ask"})
        dash = self._build([r], [])
        row = dash["needs_approval"][0]
        self.assertTrue(row["reraised"])
        self.assertEqual(row["reraised_note"], "new ask")


# --------------------------------------------------------------------------- #
# 11: backward compatibility
# --------------------------------------------------------------------------- #
class BackwardCompatTestCase(LifecycleBase):
    def test_legacy_yaml_loads_without_new_keys(self):
        legacy = {"id": "R-200", "title": "legacy card", "status": "delivered",
                  "sources": [_src()]}
        r = Requirement.from_dict(legacy)
        self.assertIsNone(r.thread_id)
        self.assertIsNone(r.thread_key)
        self.assertIsNone(r.archived_at)
        # to_dict must NOT introduce the new optional keys when unset
        d = r.to_dict()
        for k in ("thread_id", "thread_key", "archived_at", "archive_reason"):
            self.assertNotIn(k, d)

    def test_round_trip_is_byte_stable(self):
        self._seed("R-200", "legacy card", State.DELIVERED.value)
        path = config.REGISTRY_DIR / "R-200.yaml"
        first = path.read_bytes()
        registry.save(registry.load("R-200"))                   # re-save, no mutation
        self.assertEqual(path.read_bytes(), first)


# --------------------------------------------------------------------------- #
# 12: loop safety — two candidates, same archived thread, one batch
# --------------------------------------------------------------------------- #
class LoopSafetyTestCase(LifecycleBase):
    def test_two_candidates_same_archived_thread(self):
        r = self._seed("R-100", "Ship the quarterly report", State.DELIVERED.value,
                       thread_key="gmail:T1")
        registry.archive(r, "user")
        def mk():
            return Requirement(id="", title="Ship the quarterly report",
                               sources=[_src(channel="gmail", gmail_thread_id="T1")])
        first = registry.merge_or_new(mk())
        second = registry.merge_or_new(mk())
        self.assertNotEqual(first.id, "R-100")                  # archived not revived
        self.assertEqual(second.id, first.id)                   # 2nd folds into 1st
        self.assertEqual(registry.load("R-100").status, State.ARCHIVED.value)
        self.assertEqual(self._active_ids(), [first.id])        # one fresh card only


# --------------------------------------------------------------------------- #
# 13: inventory pin — delivered survives the cap
# --------------------------------------------------------------------------- #
class InventoryPinTestCase(LifecycleBase):
    def test_delivered_pinned_even_past_cap(self):
        for i in range(1, 66):                                  # 65 detected cards
            self._seed(f"R-{i:03d}", f"debt {i}", State.DETECTED.value)
        self._seed("R-500", "Ship the report", State.DELIVERED.value)
        text = quick_capture.registry_inventory_text()
        self.assertIn("R-500 | delivered", text)                # pinned
        self.assertLessEqual(len(text.splitlines()), quick_capture._INVENTORY_CAP)
        self.assertNotIn("R-001 |", text)                       # oldest debt dropped


if __name__ == "__main__":
    unittest.main()
