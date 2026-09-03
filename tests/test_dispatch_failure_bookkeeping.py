"""executor.dispatch — the exact numbers of the failure ledger (§4 / P0-6).

tests/test_dispatch.py and tests/test_dispatch_storm_brake.py pin the flow;
this file pins the constants and edges mutation testing showed were unpinned:
the retry-backoff curve (30s·2^attempts, capped at 600s, attempts capped at
5, the window is [0, backoff) — a future timestamp never backs off), the
streak counter's first two values, a limit of 1 halting on the first failure,
the notification naming the card by title (id only when untitled), the
[dispatch-halted] notes tag appended under existing notes with the raw first
line (200 chars) when the error is unclassified, the 500-char cap on
last_error and on the raised message, stdout-only error text surviving, and
wait_s (0 for a future approved_at, whole seconds otherwise).
"""
import datetime as _dt
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State

UTC = _dt.timezone.utc
NOW = _dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _proc(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


class BackoffCurveTestCase(unittest.TestCase):
    def _open(self, attempts, seconds_ago):
        stamp = (NOW - _dt.timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with mock.patch.object(executor, "_utcnow", return_value=NOW):
            return executor._backing_off(attempts, stamp)

    def test_curve_doubles_from_60s_and_caps_at_600s(self):
        # attempts=1 → 60s, 2 → 120s, 3 → 240s, 4 → 480s, 5+ → 600s
        for attempts, window in ((1, 60), (2, 120), (3, 240), (4, 480), (5, 600), (6, 600), (50, 600)):
            with self.subTest(attempts=attempts):
                self.assertIs(self._open(attempts, window - 1), True)
                self.assertIs(self._open(attempts, window), False)   # window is half-open

    def test_no_attempts_or_no_stamp_or_future_stamp_never_back_off(self):
        self.assertIs(executor._backing_off(0, "2026-09-02T11:59:00Z"), False)
        self.assertIs(executor._backing_off(3, None), False)
        self.assertIs(executor._backing_off(3, "not a time"), False)
        self.assertIs(self._open(3, -5), False)      # stamp in the future
        self.assertIs(self._open(3, 0), True)        # stamped right now


class _Base(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.target = Path(tempfile.mkdtemp(prefix="fail-ledger-"))
        (self.target / "keep.txt").write_text("x", encoding="utf-8")
        self.cfg = config.Config()
        self.cfg.memory_inject = False
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=False),
            mock.patch.object(executor.notify, "notify", mock.Mock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.notified = executor.notify.notify

    def _req(self, **kw):
        base = dict(id="R-600", title="失败台账", status=State.APPROVED.value,
                    target_repo=str(self.target))
        base.update(kw)
        req = Requirement(**base)
        registry.save(req)
        return req

    def _fail(self, req, err, **proc):
        runner = mock.Mock(return_value=_proc(**proc) if proc else _proc(1, stderr=err))
        with self.assertRaises(executor.DispatchError) as cm:
            executor.dispatch(req, self.cfg, runner=runner)
        return cm.exception, registry.load(req.id).execution

    def _reopen(self, req_id):
        req = registry.load(req_id)
        ex = dict(req.execution or {})
        ex["last_dispatch_attempt_at"] = "2026-01-01T00:00:00Z"
        req.execution = ex
        registry.save(req)
        return req


class FailureLedgerTestCase(_Base):
    def test_streak_counts_one_then_two(self):
        req = self._req()
        _e, ex = self._fail(req, "boom one")
        self.assertEqual((ex["dispatch_attempts"], ex["dispatch_class_streak"]), (1, 1))
        self.assertEqual(ex["dispatch_error_class"], "unclassified")
        _e, ex = self._fail(self._reopen("R-600"), "boom two")
        self.assertEqual((ex["dispatch_attempts"], ex["dispatch_class_streak"]), (2, 2))

    def test_limit_of_one_halts_on_the_first_failure(self):
        self.cfg.dispatch_max_failures = 1
        req = self._req()
        exc, ex = self._fail(req, "boom")
        self.assertIsInstance(exc, executor.DispatchHalted)
        self.assertIs(ex["dispatch_halted"], True)

    def test_notification_names_the_card_by_title_else_id(self):
        req = self._req()
        self._fail(req, "boom")
        title, body = self.notified.call_args.args[:2]
        self.assertIn("失败台账", body)
        self.notified.reset_mock()
        untitled = self._req(id="R-601", title="")
        self._fail(untitled, "boom")
        self.assertIn("R-601", self.notified.call_args.args[1])

    def test_halt_tag_is_appended_under_existing_notes_with_the_raw_first_line(self):
        self.cfg.dispatch_max_failures = 1
        first_line = "x" * 250
        req = self._req(notes="[direct-run] 用户直接开跑")
        self._fail(req, first_line + "\nsecond line")
        notes = registry.load("R-600").notes
        self.assertTrue(notes.startswith("[direct-run] 用户直接开跑\n[dispatch-halted] "))
        self.assertIn("x" * 200, notes)
        self.assertNotIn("x" * 201, notes)          # first line clipped to 200
        self.assertNotIn("second line", notes)
        self.assertIn("（unclassified）", notes)

    def test_classified_halt_hint_is_the_catalog_sentence(self):
        self.cfg.dispatch_max_failures = 1
        req = self._req()
        self._fail(req, "possibly due to low max file descriptors")
        notes = registry.load("R-600").notes
        self.assertIn("（claude_blind）", notes)
        # the catalog sentence (in the UI language), not the raw Bun text
        self.assertIn("Full Disk Access", notes.replace("完全磁盘访问", "Full Disk Access"))
        self.assertNotIn("possibly due to low max file descriptors [@", notes)

    def test_error_text_is_capped_at_500_chars_everywhere(self):
        req = self._req()
        exc, ex = self._fail(req, "e" * 700)
        self.assertEqual(len(ex["last_error"]), 500)
        self.assertEqual(len(str(exc)), 500)
        self.cfg.dispatch_max_failures = 1
        exc, ex = self._fail(self._reopen("R-600"), "h" * 700)
        self.assertEqual(len(str(exc)), 500)

    def test_stdout_only_error_text_is_kept(self):
        req = self._req()
        _e, ex = self._fail(req, None, rc=2, stdout="only on stdout", stderr="")
        self.assertEqual(ex["last_error"], "only on stdout")
        _e, ex = self._fail(self._reopen("R-600"), None, rc=3, stdout="", stderr="")
        self.assertEqual(ex["last_error"], "claude --bg exited 3 (no output)")


class WaitSecondsTestCase(_Base):
    def _wait_s(self, approved_at):
        req = self._req(execution={"approved_at": approved_at})
        runner = mock.Mock(return_value=_proc(0, stdout="backgrounded · e88561e5"))
        executor.dispatch(req, self.cfg, runner=runner)
        events = [e for e in analytics.read_events()
                  if e.get("event") == "dispatch" and e.get("req") == "R-600"]
        return events[-1].get("wait_s")

    def test_wait_is_whole_seconds_since_approval_never_negative(self):
        past = (_dt.datetime.now(UTC) - _dt.timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertIn(self._wait_s(past), (90, 91))
        future = (_dt.datetime.now(UTC) + _dt.timedelta(seconds=3600)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self._wait_s(future), 0)
        self.assertIsNone(self._wait_s(None))


if __name__ == "__main__":
    unittest.main()
