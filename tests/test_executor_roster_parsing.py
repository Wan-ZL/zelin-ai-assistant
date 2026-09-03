"""executor roster plumbing — ``claude agents --json --all`` parsing (§4 P0-6).

tests/test_dispatch.py pins the AFTER-gate; this file pins the shapes the
roster reader must survive without ever binding the wrong session: every
envelope claude has printed ({"agents": […]} / {"sessions": […]} / bare list /
garbage), every field alias (cwd / working_directory / workingDirectory,
session_id / sessionId / id, started_at / startedAt / created_at), worktree
sub-paths under the target, ties resolved by ROSTER ORDER (stable sort, last
wins), and the three failure answers (spawn error / bad JSON / empty stdout →
None). ``_parse_when`` gets its full three-shape table (ISO / epoch seconds /
epoch millis, naive → UTC, junk → None). ``_agent_info_strict`` keeps its
None-vs-{} distinction (§46.1: a failed probe is not "not running").
subprocess.run is patched everywhere — no real claude.
"""
import datetime as _dt
import json
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor

UTC = _dt.timezone.utc


def _proc(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


class ParseWhenTestCase(unittest.TestCase):
    def test_none_and_blank_are_none(self):
        self.assertIsNone(executor._parse_when(None))
        self.assertIsNone(executor._parse_when(""))
        self.assertIsNone(executor._parse_when("   "))

    def test_iso_with_z_and_offset(self):
        self.assertEqual(executor._parse_when("2026-07-08T12:00:00Z"),
                         _dt.datetime(2026, 7, 8, 12, 0, tzinfo=UTC))
        self.assertEqual(executor._parse_when("2026-07-08T14:00:00+02:00"),
                         _dt.datetime(2026, 7, 8, 12, 0, tzinfo=UTC))

    def test_naive_iso_is_assumed_utc(self):
        got = executor._parse_when("2026-07-08T12:00:00")
        self.assertEqual(got, _dt.datetime(2026, 7, 8, 12, 0, tzinfo=UTC))
        self.assertIs(got.tzinfo, UTC)

    def test_epoch_seconds_and_millis(self):
        secs = 1_800_000_000
        self.assertEqual(executor._parse_when(secs),
                         _dt.datetime.fromtimestamp(secs, tz=UTC))
        self.assertEqual(executor._parse_when(secs * 1000),
                         _dt.datetime.fromtimestamp(secs, tz=UTC))
        self.assertEqual(executor._parse_when(float(secs) * 1000 + 500),
                         _dt.datetime.fromtimestamp(secs + 0.5, tz=UTC))

    def test_numeric_strings_parse_as_epoch(self):
        secs = 1_800_000_000
        self.assertEqual(executor._parse_when(str(secs)),
                         _dt.datetime.fromtimestamp(secs, tz=UTC))
        self.assertEqual(executor._parse_when(" 1800000000000 "),
                         _dt.datetime.fromtimestamp(secs, tz=UTC))

    def test_non_positive_and_absurd_epochs_are_none(self):
        self.assertIsNone(executor._parse_when(0))
        self.assertIsNone(executor._parse_when(-5))
        self.assertIsNone(executor._parse_when(1e300))   # OverflowError / ValueError path
        # the boundary: one second past the epoch is a real (ancient) time
        self.assertEqual(executor._parse_when(1), _dt.datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC))

    def test_junk_text_is_none(self):
        self.assertIsNone(executor._parse_when("yesterday"))
        self.assertIsNone(executor._parse_when("2026-13-45T99:00:00Z"))


class NewestSessionShapesTestCase(unittest.TestCase):
    CWD = "/tmp/roster-target/"   # trailing slash on purpose: normalised away

    def _lookup(self, payload, after=None):
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        with mock.patch.object(executor.subprocess, "run", return_value=_proc(0, stdout)):
            return executor._newest_session_for_cwd(self.CWD, after=after)

    def test_envelopes_are_unwrapped(self):
        entry = {"cwd": "/tmp/roster-target", "session_id": "abc12345"}
        for key in ("agents", "sessions", "items", "data"):
            with self.subTest(envelope=key):
                self.assertEqual(self._lookup({key: [entry]}), "abc12345")
        self.assertEqual(self._lookup([entry]), "abc12345")

    def test_unknown_envelope_or_scalar_payload_is_none(self):
        self.assertIsNone(self._lookup({"foo": [{"cwd": "/tmp/roster-target", "id": "x"}]}))
        self.assertIsNone(self._lookup({"agents": "not-a-list"}))
        self.assertIsNone(self._lookup(42))
        self.assertIsNone(self._lookup("null"))

    def test_non_dict_entries_and_missing_ids_are_skipped(self):
        payload = ["junk", 7, {"cwd": "/tmp/roster-target"},
                   {"cwd": "/tmp/roster-target", "sessionId": "keep0001"}]
        self.assertEqual(self._lookup(payload), "keep0001")

    def test_cwd_aliases_and_worktree_subpaths_match(self):
        for field in ("cwd", "working_directory", "workingDirectory"):
            with self.subTest(field=field):
                self.assertEqual(self._lookup([{field: "/tmp/roster-target/", "id": "a1b2c3d4"}]),
                                 "a1b2c3d4")
        self.assertEqual(
            self._lookup([{"cwd": "/tmp/roster-target/.claude/worktrees/x", "id": "wt000001"}]),
            "wt000001")

    def test_sibling_and_prefix_dirs_do_not_match(self):
        self.assertIsNone(self._lookup([{"cwd": "/tmp/roster-target-2", "id": "no000001"}]))
        self.assertIsNone(self._lookup([{"cwd": "/tmp", "id": "no000002"}]))
        self.assertIsNone(self._lookup([{"cwd": "", "id": "no000003"}]))

    def test_id_aliases_in_priority_order(self):
        e = {"cwd": "/tmp/roster-target", "session_id": "first111",
             "sessionId": "second22", "id": "third333"}
        self.assertEqual(self._lookup([e]), "first111")
        del e["session_id"]
        self.assertEqual(self._lookup([e]), "second22")
        del e["sessionId"]
        self.assertEqual(self._lookup([e]), "third333")

    def test_started_aliases_all_feed_the_gate(self):
        after = executor._parse_when("2026-07-08T12:00:00Z")
        for field in ("started_at", "startedAt", "created_at"):
            with self.subTest(field=field):
                e = {"cwd": "/tmp/roster-target", "id": "gate0001",
                     field: "2026-07-08T12:00:03Z"}
                self.assertEqual(self._lookup([e], after=after), "gate0001")

    def test_gate_tolerates_two_seconds_of_roster_truncation(self):
        after = executor._parse_when("2026-07-08T12:00:00Z")
        ok = {"cwd": "/tmp/roster-target", "id": "slack001",
              "started_at": "2026-07-08T11:59:58Z"}
        too_old = {"cwd": "/tmp/roster-target", "id": "old00001",
                   "started_at": "2026-07-08T11:59:57Z"}
        self.assertEqual(self._lookup([ok], after=after), "slack001")
        self.assertIsNone(self._lookup([too_old], after=after))

    def test_ties_resolve_to_the_later_roster_entry(self):
        same = "2026-07-08T12:00:05Z"
        entries = [{"cwd": "/tmp/roster-target", "id": "tie00001", "started_at": same},
                   {"cwd": "/tmp/roster-target", "id": "tie00002", "started_at": same}]
        self.assertEqual(self._lookup(entries), "tie00002")
        # unparseable ages sort oldest, so a dated entry beats them regardless of order
        entries = [{"cwd": "/tmp/roster-target", "id": "dated001", "started_at": same},
                   {"cwd": "/tmp/roster-target", "id": "noage001"}]
        self.assertEqual(self._lookup(entries), "dated001")

    def test_failures_answer_none(self):
        with mock.patch.object(executor.subprocess, "run", side_effect=OSError("no claude")):
            self.assertIsNone(executor._newest_session_for_cwd(self.CWD))
        with mock.patch.object(executor.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("claude", 30)):
            self.assertIsNone(executor._newest_session_for_cwd(self.CWD))
        self.assertIsNone(self._lookup("{not json"))
        self.assertIsNone(self._lookup("   "))

    def test_query_ignores_exit_code(self):
        # the legacy reader trusts stdout even on a non-zero exit (claude
        # prints warnings to stderr and exits 1 on some versions)
        entry = [{"cwd": "/tmp/roster-target", "id": "rc100001"}]
        with mock.patch.object(executor.subprocess, "run",
                               return_value=_proc(1, json.dumps(entry), "warn")):
            self.assertEqual(executor._newest_session_for_cwd(self.CWD), "rc100001")


class AgentInfoStrictTestCase(unittest.TestCase):
    SID = "deadbeef-0000-4000-8000-000000000001"

    def _probe(self, proc):
        with mock.patch.object(executor.subprocess, "run", return_value=proc):
            return executor._agent_info_strict(self.SID)

    def test_query_failures_are_none_not_empty(self):
        with mock.patch.object(executor.subprocess, "run", side_effect=OSError("boom")):
            self.assertIsNone(executor._agent_info_strict(self.SID))
        self.assertIsNone(self._probe(_proc(1, "[]")))
        self.assertIsNone(self._probe(_proc(0, "{bad json")))

    def test_absent_session_is_empty_dict(self):
        self.assertEqual(self._probe(_proc(0, "")), {})
        self.assertEqual(self._probe(_proc(0, "[]")), {})
        self.assertEqual(self._probe(_proc(0, json.dumps({"agents": []}))), {})   # envelopes NOT unwrapped here
        self.assertEqual(self._probe(_proc(0, json.dumps(["junk", {"id": "other001"}]))), {})

    def test_match_by_short_id_or_full_session_id_prefix(self):
        by_id = [{"id": "deadbeef", "pid": 41, "cwd": "/w"}]
        self.assertEqual(self._probe(_proc(0, json.dumps(by_id))), {"pid": 41, "cwd": "/w"})
        by_sid = [{"sessionId": self.SID, "pid": None}]
        self.assertEqual(self._probe(_proc(0, json.dumps(by_sid))), {"pid": None, "cwd": None})

    def test_lenient_wrapper_folds_failure_into_empty(self):
        with mock.patch.object(executor, "_agent_info_strict", return_value=None):
            self.assertEqual(executor._agent_info(self.SID), {})
        with mock.patch.object(executor, "_agent_info_strict", return_value={"pid": 1}):
            self.assertEqual(executor._agent_info(self.SID), {"pid": 1})

    def test_roster_query_uses_the_resolved_claude_binary(self):
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw))
            return _proc(0, "[]")
        with mock.patch.object(executor.subprocess, "run", run), \
                mock.patch.object(executor.llm, "claude_bin", return_value="/opt/claude"):
            executor._agent_info_strict(self.SID)
            executor._newest_session_for_cwd("/tmp/x")
        self.assertEqual([a for a, _k in seen], [["/opt/claude", "agents", "--json", "--all"]] * 2)
        for _a, kw in seen:
            self.assertEqual(kw, {"capture_output": True, "text": True, "timeout": 30})

    def test_field_readers_on_odd_entries(self):
        self.assertEqual(executor._agent_cwd("junk"), "")
        self.assertEqual(executor._agent_cwd({"cwd": None, "workingDirectory": "/w"}), "/w")
        self.assertEqual(executor._agent_started({}), 0)
        self.assertEqual(executor._agent_started({"createdAt": "x", "created_at": "c"}), "c")
        self.assertIsNone(executor._agent_sid({}))


if __name__ == "__main__":
    unittest.main()
