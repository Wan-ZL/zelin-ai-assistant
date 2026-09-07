"""executor.live_session_count — the roster count the §56.3 session gate defers on.

scripts/auto-deploy.sh asks it before anything that would restart actd. Pinned:
it is built on the SAME reader as every other roster site (``_roster_query`` →
``_parse_roster`` → ``_unwrap_roster``, the resolved claude binary, 30 s timeout —
no second parser); "live" = a background entry carrying a ``pid`` (claude prints
one only while the worker process exists), whatever its ``state`` — a `done`
worker the owner is attached to dies just the same (live 2026-09-07 P-029);
``kind == interactive`` (the owner's own terminal claude) never counts; and the
three failure answers (spawn error / non-zero / bad JSON) are ``None`` — strict
like ``_agent_info_strict``, so the gate can fail CLOSED on "cannot tell"
instead of reading it as "no sessions". subprocess.run is patched: no real claude.
"""
import json
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import executor


def _proc(rc=0, stdout=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr="")


ROSTER = [
    # a working background worker — live
    {"id": "2262980e", "sessionId": "2262980e-cf27", "kind": "background", "pid": 24910,
     "state": "working", "status": "busy", "cwd": "/w"},
    # finished, process still alive (the owner may be attached) — live
    {"id": "f40f2001", "sessionId": "f40f2001-82b7", "kind": "background", "pid": 53729,
     "state": "done", "status": "idle", "cwd": "/w"},
    # blocked with a live process — live (the harvest, not the deployer, ends it)
    {"id": "0f8999d2", "kind": "background", "pid": 59731, "state": "blocked"},
    # finished, process gone — not live
    {"id": "5b79d6ba", "kind": "background", "state": "done"},
    {"id": "9cc91864", "kind": "background", "pid": None, "state": "stopped"},
    # the owner's terminal claude — never counts, pid or not
    {"sessionId": "45ebfc6c", "kind": "interactive", "pid": 12077, "status": "busy"},
    # junk entries the parser tolerates
    "not-a-dict", 42,
]


class LiveSessionCountTestCase(unittest.TestCase):
    def _count(self, rc=0, stdout=None, side_effect=None):
        with mock.patch.object(executor.subprocess, "run",
                               side_effect=side_effect,
                               return_value=None if side_effect else _proc(rc, stdout)) as run, \
                mock.patch.object(executor.llm, "claude_bin", return_value="/stable/claude"):
            got = executor.live_session_count()
        run.assert_called_once()
        return got, run.call_args

    def test_counts_background_entries_with_a_live_pid_whatever_their_state(self):
        got, _ = self._count(stdout=json.dumps(ROSTER))
        self.assertEqual(got, 3)

    def test_interactive_sessions_never_count(self):
        got, _ = self._count(stdout=json.dumps(
            [{"sessionId": "a", "kind": "interactive", "pid": 1},
             {"sessionId": "b", "kind": "interactive", "pid": 2, "state": "working"}]))
        self.assertEqual(got, 0)

    def test_entries_without_kind_count_by_pid(self):
        # a roster shape from before `kind` existed listed background sessions only
        got, _ = self._count(stdout=json.dumps([{"id": "x", "pid": 7}, {"id": "y"}]))
        self.assertEqual(got, 1)

    def test_envelopes_are_unwrapped_like_everywhere_else(self):
        for envelope in ("agents", "sessions", "items", "data"):
            got, _ = self._count(stdout=json.dumps({envelope: [{"id": "x", "pid": 7}]}))
            self.assertEqual(got, 1, envelope)

    def test_empty_roster_is_zero_not_none(self):
        self.assertEqual(self._count(stdout="[]")[0], 0)
        self.assertEqual(self._count(stdout="")[0], 0, "empty stdout = an empty roster (existing reader contract)")
        self.assertEqual(self._count(stdout=json.dumps({"other": 1}))[0], 0)

    def test_failures_are_none_so_the_gate_can_fail_closed(self):
        self.assertIsNone(self._count(side_effect=OSError("no claude"))[0])
        self.assertIsNone(self._count(side_effect=subprocess.TimeoutExpired("claude", 30))[0])
        self.assertIsNone(self._count(rc=1, stdout="[]")[0], "non-zero exit = cannot tell")
        self.assertIsNone(self._count(stdout="not json")[0])

    def test_uses_the_resolved_claude_and_the_shared_roster_argv(self):
        _, call = self._count(stdout="[]")
        self.assertEqual(call.args[0], ["/stable/claude", "agents", "--json", "--all"])
        self.assertEqual(call.kwargs, {"capture_output": True, "text": True, "timeout": 30})


if __name__ == "__main__":
    unittest.main()
