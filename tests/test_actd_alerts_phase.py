"""(d) alerts phase — credential scan of executing logs + fan-out to notify (CONTRACT §11 / §40 / §48).

``_check_auth_failures`` reads each executing card's ``execution.log`` once:
a credential failure notifies once per card (anti-nag set), cards without a
log / with an unreadable log / not executing / already notified are skipped.
``_alerts_phase`` fans every message of the three detectors out to
``notify.notify`` with the (title, body, req, kind) shape each one carries.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_AUTH_LOG = ("# dispatch R-7 @ 2026-07-15T09:00:00\n=== STDERR ===\n"
             "authentication_error: invalid api key\n")


class AuthFailureScanTest(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.logs = config.STATE_DIR / "auth-scan-logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def _card(self, rid, status=State.EXECUTING.value, log=None, title="任务"):
        ex = {"session_id": f"sid-{rid}"}
        if log is not None:
            ex["log"] = str(log)
        registry.save(Requirement(id=rid, title=title, status=status, execution=ex))

    def test_notifies_once_per_failing_card_and_skips_the_rest(self):
        bad = self.logs / "bad.log"
        bad.write_text(_AUTH_LOG, encoding="utf-8")
        good = self.logs / "good.log"
        good.write_text("all fine\n", encoding="utf-8")
        self._card("R-1", log=bad, title="登录坏了")
        self._card("R-2", log=good)
        self._card("R-3")                                        # no log
        self._card("R-4", log=self.logs / "missing.log")         # unreadable
        self._card("R-5", status=State.REVIEW.value, log=bad)    # not executing
        self._card("R-6", log=bad, title="已通知")
        notified = {"R-6"}
        msgs = actd._check_auth_failures(notified)
        self.assertEqual(len(msgs), 1)
        self.assertIn("登录坏了", msgs[0][1])
        self.assertEqual(notified, {"R-6", "R-1"})
        # second pass: R-1 is now in the anti-nag set → silence
        self.assertEqual(actd._check_auth_failures(notified), [])

    def test_untitled_card_names_claude(self):
        bad = self.logs / "bad2.log"
        bad.write_text(_AUTH_LOG, encoding="utf-8")
        self._card("R-9", log=bad, title="")
        msgs = actd._check_auth_failures(set())
        self.assertEqual(len(msgs), 1)
        self.assertIn("claude", msgs[0][1])


class AlertsPhaseFanOutTest(unittest.TestCase):
    def test_every_detector_message_reaches_notify_with_its_shape(self):
        with mock.patch.object(actd, "detect_transitions",
                               return_value=[("新卡", "body", "R-1", None),
                                             ("待验收", "body2", "R-2", "review_ready")]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[("登录", "again")]), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[("雷达", "dead")]) as live, \
                mock.patch.object(actd.notify, "notify") as notify:
            actd._alerts_phase({"a": 1}, {"b": 2}, set(), None, interval=30)
        self.assertEqual(notify.call_args_list, [
            mock.call("新卡", "body", req="R-1", kind=None),
            mock.call("待验收", "body2", req="R-2", kind="review_ready"),
            mock.call("登录", "again"),
            mock.call("雷达", "dead"),
        ])
        # a missing anti-nag set is replaced by a fresh one, interval passed through
        self.assertEqual(live.call_args.args[0], set())
        self.assertEqual(live.call_args.kwargs, {"interval": 30})


if __name__ == "__main__":
    unittest.main()
