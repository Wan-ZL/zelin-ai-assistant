"""Dispatch-storm brake (CONTRACT §4 / §25 fd_limit / §51; v0.48.4).

2026-08-31 live: launchd handed actd ``ulimit -n 256``, ``claude --bg`` died
on "low max file descriptors", and one approved card was re-dispatched 66
times in 13h — 954 tracebacks, 98% of all registry writes, doctor green, the
only notification saying「会自动重试」. Four judgments pinned here:

1. ``failures.classify`` knows the fd-limit message (``fd_limit``).
2. After ``dispatch_max_failures`` consecutive failures of the same class the
   card is HALTED: stays approved, ``execution.dispatch_halted``, a
   ``[dispatch-halted]`` notes line, one ``dispatch_halted`` event + one
   notification, and neither actd nor executor ever launch it again. A class
   change restarts the streak; ``0`` disables the brake.
3. The backoff window is a pure no-op for actd: no registry write, no
   traceback (the fixpoint re-record was the write storm).
4. Re-approval (退回提案 → 批准) clears the streak; the dashboard projects a
   halted card into ``needs_input`` as a blocked row (never「排队中」), and the
   transition detector does not double-ping it.

Same injectable-runner pattern as tests/test_dispatch.py; nothing real is
launched, notified or queried.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import analytics, config, dashboard, failures, registry
from act.lib.registry import Requirement, State
from server import board_source

FD_ERR = ("error: An unknown error occurred, possibly due to low max file "
          "descriptors (Unexpected)")


def _proc(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


def _events(req_id: str, name: str) -> list:
    return [e for e in analytics.read_events()
            if e.get("req") == req_id and e.get("event") == name]


class FdLimitClassificationTestCase(unittest.TestCase):
    def test_claude_fd_message_classifies(self):
        self.assertEqual(failures.classify(FD_ERR), "fd_limit")
        self.assertEqual(failures.classify("EMFILE: too many open files, open"),
                         "fd_limit")
        self.assertEqual(failures.action_id("fd_limit"), "restart_actd")

    def test_bypass_disclaimer_refusal_classifies_narrowly(self):
        # issue #89's exact text; prose about permissions/disclaimers must not
        err = ("--bg with bypassPermissions requires accepting the disclaimer first. "
               "Run `claude --dangerously-skip-permissions` once interactively.")
        self.assertEqual(failures.classify(err), "claude_bypass_disclaimer")
        self.assertIsNone(failures.classify(
            "add a disclaimer to the permissions page of the docs"))
        self.assertIn("--dangerously-skip-permissions",
                      failures.user_message("claude_bypass_disclaimer", "en"))

    def test_dispatch_error_class_pools_unknown_text(self):
        self.assertEqual(executor.dispatch_error_class(FD_ERR), "fd_limit")
        self.assertEqual(executor.dispatch_error_class("boom pid 4242"), "unclassified")
        self.assertEqual(executor.dispatch_error_class("boom pid 9999"), "unclassified")


class BrakeBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.target = Path(tempfile.mkdtemp(prefix="storm-target-"))
        (self.target / "keep.txt").write_text("x", encoding="utf-8")
        self.cfg = config.Config()
        self.cfg.memory_inject = False
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=False),
            mock.patch.object(executor.notify, "notify",
                              new=mock.Mock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.notified = executor.notify.notify

    def _approved(self, req_id: str, notes: str = "") -> Requirement:
        req = Requirement(id=req_id, title="风暴刹车测试", notes=notes,
                          status=State.APPROVED.value,
                          target_repo=str(self.target))
        registry.save(req)
        return req

    def _fail_once(self, req_id: str, err: str = FD_ERR):
        """One real launch attempt that fails, with the backoff window forced
        open so the next attempt really launches again."""
        req = registry.load(req_id)
        ex = dict(req.execution or {})
        ex["last_dispatch_attempt_at"] = "2026-01-01T00:00:00Z"
        req.execution = ex
        registry.save(req)
        runner = mock.Mock(return_value=_proc(1, stderr=err))
        with self.assertRaises(executor.DispatchError) as cm:
            executor.dispatch(registry.load(req_id), self.cfg, runner=runner)
        runner.assert_called_once()
        return cm.exception


class StormBrakeTripsTestCase(BrakeBase):
    def test_fifth_same_class_failure_halts_the_card(self):
        self._approved("R-980")
        for i in range(4):
            exc = self._fail_once("R-980")
            self.assertNotIsInstance(exc, executor.DispatchHalted, i)
            ex = registry.load("R-980").execution
            self.assertEqual(ex["dispatch_error_class"], "fd_limit")
            self.assertEqual(ex["dispatch_class_streak"], i + 1)
            self.assertNotIn("dispatch_halted", ex)
        exc = self._fail_once("R-980")
        self.assertIsInstance(exc, executor.DispatchHalted)

        saved = registry.load("R-980")
        ex = saved.execution
        self.assertEqual(saved.status, State.APPROVED.value)   # parked, not trashed
        self.assertTrue(ex["dispatch_halted"])
        self.assertTrue(ex["dispatch_halted_at"])
        self.assertEqual(ex["dispatch_attempts"], 5)
        self.assertEqual(ex["dispatch_class_streak"], 5)
        self.assertIn(FD_ERR[:60], ex["last_error"])
        # notes breadcrumb names the count, the class and the catalog hint
        self.assertIn("[dispatch-halted]", saved.notes)
        self.assertIn("5", saved.notes)
        self.assertIn("fd_limit", saved.notes)
        self.assertIn("8192", saved.notes)
        # exactly one halted event + one halted notification (plus the single
        # first-failure ping from the streak start)
        halted = _events("R-980", "dispatch_halted")
        self.assertEqual(len(halted), 1)
        self.assertEqual(halted[0].get("failure_id"), "fd_limit")
        self.assertEqual(halted[0].get("streak"), 5)
        titles = [c.args[0] for c in self.notified.call_args_list]
        self.assertEqual(len(titles), 2, titles)
        self.assertTrue(any("停止重试" in t or "stopped retrying" in t for t in titles),
                        titles)

    def test_class_change_restarts_the_streak(self):
        self._approved("R-981")
        for _ in range(3):
            self._fail_once("R-981")
        self._fail_once("R-981", err="Invalid API key")   # claude_auth_failed
        ex = registry.load("R-981").execution
        self.assertEqual(ex["dispatch_error_class"], "claude_auth_failed")
        self.assertEqual(ex["dispatch_class_streak"], 1)
        self.assertEqual(ex["dispatch_attempts"], 4)
        self.assertNotIn("dispatch_halted", ex)

    def test_unclassified_errors_with_drifting_text_still_pool(self):
        self._approved("R-982")
        for i in range(5):
            exc = self._fail_once("R-982", err="boom pid %d" % (1000 + i))
        self.assertIsInstance(exc, executor.DispatchHalted)
        self.assertEqual(registry.load("R-982").execution["dispatch_error_class"],
                         "unclassified")

    def test_zero_limit_never_brakes(self):
        self.cfg.dispatch_max_failures = 0
        self._approved("R-983")
        for _ in range(7):
            exc = self._fail_once("R-983")
            self.assertNotIsInstance(exc, executor.DispatchHalted)
        self.assertNotIn("dispatch_halted", registry.load("R-983").execution)

    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="storm-cfg-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_config_yaml_knob_is_parsed_and_clamped(self):
        self.assertEqual(config.Config().dispatch_max_failures, 5)   # default pinned
        self.assertEqual(self._load_with_yaml("execution: {}\n").dispatch_max_failures, 5)
        self.assertEqual(self._load_with_yaml(
            "execution:\n  dispatch_max_failures: 3\n").dispatch_max_failures, 3)
        # negatives clamp to 0 (= brake off); junk keeps the default
        self.assertEqual(self._load_with_yaml(
            "execution:\n  dispatch_max_failures: -4\n").dispatch_max_failures, 0)
        self.assertEqual(self._load_with_yaml(
            "execution:\n  dispatch_max_failures: lots\n").dispatch_max_failures, 5)


class HaltedCardIsNeverRetriedTestCase(BrakeBase):
    def _halted(self, req_id: str) -> Requirement:
        req = self._approved(req_id)
        req.execution = {"approved_at": "2026-08-31T11:15:00Z",
                         "last_error": FD_ERR, "last_error_at": "2026-08-31T12:00:00Z",
                         "dispatch_attempts": 5, "dispatch_class_streak": 5,
                         "dispatch_error_class": "fd_limit",
                         "last_dispatch_attempt_at": "2026-01-01T00:00:00Z",
                         "dispatch_halted": True,
                         "dispatch_halted_at": "2026-08-31T12:00:00Z"}
        registry.save(req)
        return req

    def test_executor_refuses_without_launching(self):
        self._halted("R-984")
        runner = mock.Mock()
        with self.assertRaises(executor.DispatchHalted):
            executor.dispatch(registry.load("R-984"), self.cfg, runner=runner)
        runner.assert_not_called()

    def test_actd_skips_halted_cards_without_touching_them(self):
        self._halted("R-985")
        path = config.REGISTRY_DIR / "R-985.yaml"
        before = path.read_bytes()
        launch = mock.Mock(side_effect=AssertionError("must not be called"))
        with mock.patch.object(actd.executor, "dispatch", launch), \
                mock.patch.object(actd, "save", wraps=registry.save) as saved, \
                mock.patch.object(actd, "_log") as log:
            n = actd.dispatch_approved(self.cfg)
        self.assertEqual(n, 0)
        launch.assert_not_called()
        saved.assert_not_called()
        self.assertFalse(any("R-985" in str(c) for c in log.call_args_list))
        self.assertEqual(path.read_bytes(), before)


class BackoffWindowIsANoOpTestCase(BrakeBase):
    def test_actd_neither_writes_nor_tracebacks_while_backing_off(self):
        self._approved("R-986")
        # one real failure -> attempts=1, window open for 60s from now
        runner = mock.Mock(return_value=_proc(1, stderr=FD_ERR))
        with self.assertRaises(executor.DispatchError):
            executor.dispatch(registry.load("R-986"), self.cfg, runner=runner)
        path = config.REGISTRY_DIR / "R-986.yaml"
        before = path.read_bytes()
        real_dispatch = executor.dispatch
        runner2 = mock.Mock()

        def wrap(req, cfg):
            return real_dispatch(req, cfg, runner=runner2)

        with mock.patch.object(actd.executor, "dispatch", wrap), \
                mock.patch.object(actd, "save", wraps=registry.save) as saved, \
                mock.patch.object(actd, "_log") as log:
            for _ in range(3):   # three idle passes inside the window
                actd.dispatch_approved(self.cfg)
        runner2.assert_not_called()
        saved.assert_not_called()
        self.assertFalse(any("Traceback" in str(c) or "FAILED" in str(c)
                             for c in log.call_args_list), log.call_args_list)
        self.assertEqual(path.read_bytes(), before)

    def test_fresh_failure_is_written_once_and_logged_on_one_line(self):
        # the executor records the failure; actd must not rewrite the same
        # text (second registry write) nor dump a 28-line traceback for a
        # DispatchError it already understands.
        self._approved("R-987")
        real_dispatch = executor.dispatch
        runner = mock.Mock(return_value=_proc(1, stderr=FD_ERR))

        def wrap(req, cfg):
            return real_dispatch(req, cfg, runner=runner)

        with mock.patch.object(actd.executor, "dispatch", wrap), \
                mock.patch.object(actd, "save", wraps=registry.save) as saved, \
                mock.patch.object(actd, "_log") as log:
            actd.dispatch_approved(self.cfg)
        runner.assert_called_once()
        saved.assert_not_called()   # executor's own save is the one write
        lines = [str(c.args[0]) for c in log.call_args_list if "R-987" in str(c)]
        self.assertEqual(len(lines), 1, lines)
        self.assertNotIn("Traceback", lines[0])
        self.assertIn("low max file descriptors", lines[0])


class ReapprovalRearmsTestCase(BrakeBase):
    def test_approve_clears_the_streak_and_halt(self):
        req = Requirement(id="R-988", title="重批测试", status=State.CARD_SENT.value,
                          target_repo=str(self.target))
        req.execution = {"aborted_at": "2026-08-31T13:00:00Z",
                         "last_error": FD_ERR, "last_error_at": "x",
                         "dispatch_attempts": 5, "dispatch_class_streak": 5,
                         "dispatch_error_class": "fd_limit",
                         "last_dispatch_attempt_at": "x",
                         "dispatch_halted": True, "dispatch_halted_at": "x"}
        registry.save(req)
        result = actd._apply_decision(req, "approve", None)
        self.assertEqual(result, "running")
        ex = registry.load("R-988").execution
        for key in executor.DISPATCH_STREAK_KEYS + ("last_error", "last_error_at"):
            self.assertNotIn(key, ex, key)
        self.assertTrue(ex.get("approved_at"))
        self.assertEqual(ex.get("aborted_at"), "2026-08-31T13:00:00Z")  # unrelated keys stay


class HaltedProjectionTestCase(unittest.TestCase):
    def _row(self):
        req = Requirement.from_dict({
            "id": "R-989", "title": "投影测试", "status": "approved",
            "execution": {"last_error": FD_ERR, "dispatch_attempts": 5,
                          "dispatch_class_streak": 5,
                          "dispatch_error_class": "fd_limit",
                          "dispatch_halted": True},
        })
        return dashboard.build_dashboard(reqs=[req], agents=[], cfg=config.Config())

    def test_halted_card_is_a_blocked_row_not_a_queued_one(self):
        dash = self._row()
        self.assertEqual([r["id"] for r in dash["running"]], [])
        rows = dash["needs_input"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["state"], "blocked")
        self.assertTrue(row["dispatch_halted"])
        self.assertEqual(row["dispatch_attempts"], 5)
        self.assertEqual(row["last_error_id"], "fd_limit")
        self.assertEqual(row["last_error"], FD_ERR)
        self.assertIsNone(row["session_id"])
        self.assertIn("5", row["question"])
        self.assertIn("8192", row["question"])   # catalog hint, not raw text
        self.assertEqual(dash["counts"]["needs_input"], 1)
        self.assertEqual(dash["counts"]["running"], 0)

    def test_transition_detector_does_not_double_ping(self):
        prev = {"running": [{"id": "R-989", "name": "投影测试", "state": "queued"}],
                "needs_input": [], "needs_approval": [], "review": []}
        curr = self._row()
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_server_does_not_treat_the_halted_row_as_executing(self):
        # comments on the parked card fold into notes; a steer receipt would lie
        dash = self._row()
        with mock.patch.object(board_source, "_board_dict", return_value=dash):
            self.assertFalse(board_source.is_executing(Path("/nonexistent"), "R-989"))


if __name__ == "__main__":
    unittest.main()
