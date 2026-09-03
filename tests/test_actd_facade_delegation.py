"""act.actd compatibility facade — every delegate forwards and returns (CONTRACT §58.4 P3b).

The facade is the surface 80 test files patch; each wrapper must (1) build the
``seam.Daemon`` snapshot from the CURRENT module names (so a patched
``actd.executor`` / ``actd.save`` / … is what the lib code sees), (2) forward
its arguments unchanged, and (3) return the lib function's result. One table
drives all of it — a wrapper that swallowed a return value or dropped an
argument would fail here before any behaviour judgment noticed.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib.actd import (alerts, decisions, dispatch, housekeeping, inbox, merge,
                          reconcile, seam, session, triage_guard)

_S = object()   # sentinel return value

# (facade name, lib module, lib function, positional args, kwargs, ctx first?)
_TABLE = [
    ("process_inbox", inbox, "process_inbox", (), {}, True),
    ("_apply_capture", inbox, "apply_capture", ("t", "run", ["a.png"]),
     {"plan": ["p"], "preset": "proposals_triage", "inbox_stem": "s", "via": "web"}, True),
    ("_attach_capture_images", inbox, "attach_capture_images", ("req", ["a.png"]), {}, True),
    ("_apply_split_note", inbox, "apply_split_note", ("R-1", "ts"), {}, True),
    ("_apply_set_title", inbox, "apply_set_title", ("req", "title"), {}, True),
    ("_apply_feedback", inbox, "apply_feedback", ({"action": "feedback"},), {}, True),
    ("_apply_claude_import", inbox, "apply_claude_import", ({"action": "import"},), {}, True),
    ("_apply_decision", decisions, "apply_decision",
     ("req", "approve", "c", "card_sent", 7), {"ts": "t", "via": "web", "stem": "s"}, True),
    ("_record_nonowner_comment", decisions, "record_nonowner_comment", ("req", "c", "agent"), {}, True),
    ("_stop_session_tracked", session, "stop_session_tracked", ("req", {}, "sid", "why"),
     {"log_prefix": "merge"}, True),
    ("_stop_live_session", session, "stop_live_session", ("req", "why"), {}, True),
    ("_apply_harvest_title", session, "apply_harvest_title", ("req", {"card_title": "x"}), {}, True),
    ("_update_search_index", session, "update_search_index", ("R-1", "sid"), {}, True),
    ("_stamp_triage_snapshot", triage_guard, "stamp_triage_snapshot", ("R-1",), {}, True),
    ("_check_triage_registry_guard", triage_guard, "check_triage_registry_guard", ("req", {}), {}, True),
    ("_sweep_triage_snapshots", triage_guard, "sweep_triage_snapshots", (), {}, True),
    ("_apply_merge_review", merge, "apply_merge_review", (["R-1", "R-2"],), {}, True),
    ("_apply_merge_force", merge, "apply_merge_force", (["R-1", "R-2"], "R-1"), {}, True),
    ("_apply_merge_decision", merge, "apply_merge_decision", ("merge_apply", "MS-1"), {}, True),
    ("_apply_merge_verdict", merge, "apply_merge_verdict", ({"verdict": "merge"},), {}, True),
    ("_apply_merge_partition", merge, "apply_merge_partition", ({"verdict": "partition"},), {}, True),
    ("_merge_into_primary", merge, "merge_into_primary", ("R-1", ["R-2"]), {}, True),
    ("cleanup_merge_jobs", merge, "cleanup_merge_jobs", (), {}, True),
    ("_rearm_dispatch", dispatch, "rearm_dispatch", ({"x": 1},), {}, True),
    ("auto_dispatch_pass", dispatch, "auto_dispatch_pass", ("cfg",), {}, True),
    ("dispatch_approved", dispatch, "dispatch_approved", ("cfg",), {}, True),
    ("process_raising", dispatch, "process_raising", ("cfg",), {}, True),
    ("_reconcile_review_attach", reconcile, "reconcile_review_attach", ("req", {}), {}, True),
    ("_promote_if_delivered", reconcile, "promote_if_delivered", ("req", {}, "sid"), {}, True),
    ("_harvest_to_review", reconcile, "harvest_to_review", ("req", {}, "sid", "tag", "why"),
     {"interrupted_reason": "blocked", "agent": {"pid": 1}}, True),
    ("_drop_steers", reconcile, "drop_steers", ("req", ["p"], "reason", "why"), {}, True),
    ("_flush_steers", reconcile, "flush_steers", ("req", "cfg"), {}, True),
    ("reconcile_executing", reconcile, "reconcile_executing", ("cfg", set()), {}, True),
    ("purge_trash", housekeeping, "purge_trash", ("cfg",), {}, True),
    ("_purge_one", housekeeping, "purge_one", ("req", "cfg", "now"), {}, True),
    ("archive_stale", housekeeping, "archive_stale", ("cfg",), {}, True),
    ("_sweep_attachment_dirs", housekeeping, "sweep_attachment_dirs", (123.0,), {}, True),
    ("gc_attachments", housekeeping, "gc_attachments", (), {}, True),
    ("detect_transitions", alerts, "detect_transitions", ({"a": 1}, {"b": 2}), {}, False),
    ("_check_auth_failures", alerts, "check_auth_failures", (set(),), {}, False),
    ("_wake_grace", alerts, "wake_grace", ("cfg", 1.0, 5, 2.0), {}, False),
    ("_check_radar_liveness", alerts, "check_radar_liveness", (set(),),
     {"now": "n", "interval": 5, "mono": 1.0, "missing_since": {}}, True),
]


class FacadeDelegationTest(unittest.TestCase):
    def test_every_wrapper_forwards_arguments_and_returns_the_result(self):
        for name, module, fn, args, kwargs, ctx_first in _TABLE:
            with self.subTest(name), mock.patch.object(module, fn, return_value=_S) as target:
                result = getattr(actd, name)(*args, **kwargs)
                self.assertIs(result, _S, name)
                target.assert_called_once()
                call_args = list(target.call_args.args)
                if ctx_first:
                    self.assertIsInstance(call_args[0], seam.Daemon, name)
                    call_args = call_args[1:]
                self.assertEqual(tuple(call_args), args, name)
                self.assertEqual(target.call_args.kwargs, kwargs, name)

    def test_facade_wrappers_with_positional_kwargs(self):
        # the few wrappers that re-spell keyword arguments positionally
        with mock.patch.object(inbox, "apply_with_actor", return_value=_S) as target:
            self.assertIs(actd._apply_with_actor({"via": "agent"}, len, "x", key=1), _S)
        self.assertIsInstance(target.call_args.args[0], seam.Daemon)
        self.assertEqual(target.call_args.args[1:], ({"via": "agent"}, len, "x"))
        self.assertEqual(target.call_args.kwargs, {"key": 1})

    def test_ctx_reads_the_patched_names_at_call_time(self):
        fake_exec, fake_save, fake_log = object(), object(), object()
        with mock.patch.object(actd, "executor", fake_exec), \
                mock.patch.object(actd, "save", fake_save), \
                mock.patch.object(actd, "_log", fake_log), \
                mock.patch.object(actd, "analyze", None), \
                mock.patch.object(actd, "_merge_into_primary", fake_save):
            ctx = actd._ctx()
            self.assertIs(ctx.executor, fake_exec)
            self.assertIs(ctx.save, fake_save)
            self.assertIs(ctx.log, fake_log)
            self.assertIsNone(ctx.analyze)
            self.assertIs(ctx.merge_into_primary, fake_save)
        fresh = actd._ctx()
        self.assertIs(fresh.executor, actd.executor)
        self.assertIs(fresh.log, actd._log)
        self.assertIs(fresh.detached_actions, actd._DETACHED_ACTIONS)
        self.assertIs(fresh.run_claude_agents, actd._run_claude_agents)

    def test_shared_state_objects_are_the_same_objects(self):
        # tests mutate these in place through the facade — they must be the lib's dicts
        self.assertIs(actd._wake_state, alerts.WAKE_STATE)
        self.assertIs(actd._no_baseline_since, alerts.NO_BASELINE_SINCE)
        self.assertIs(actd._HARVEST_PROBE_AT, reconcile.HARVEST_PROBE_AT)
        self.assertIs(actd._ATTACH_GC_MARKER, housekeeping.ATTACH_GC_MARKER)
        self.assertEqual(actd.PROPOSALS_TRIAGE_PRESET, triage_guard.PROPOSALS_TRIAGE_PRESET)
        self.assertIs(actd._parse_iso, actd.maintenance.parse_iso)


if __name__ == "__main__":
    unittest.main()
