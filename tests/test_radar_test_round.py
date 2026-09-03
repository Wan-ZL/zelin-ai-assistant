"""「立即测试一轮」端到端的 Python 半边（CONTRACT §48.7）：inbox 特形 ``radar_test_round``
→ actd ``_DETACHED_ACTIONS`` → act/lib/radar_rounds（源开着才分离起 ``act.radar_<src> --once``，
台账 ``state/radar_test_rounds.json`` actd 单写者）→ dashboard ``radar_sources.<src>``
add-only 投影 ``last_attempt`` / ``test_round``（running / done / noop / lost，纯磁盘真值函数）。

绝不真 spawn：detached.spawn 注入记录器；config 现读用沙箱 config.yaml。
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import actd
from act.lib import config, dashboard, detached, radar_health, radar_rounds
from server import inbox_writer
from server.errors import InvalidFieldError, UnknownFieldError

NOW = _dt.datetime(2026, 9, 3, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean():
    for p in (radar_rounds.ROUNDS_PATH, radar_health.HEALTH_PATH, config.CONFIG_PATH,
              config.STATE_DIR / radar_rounds.LOG_NAME):
        if p.exists():
            p.unlink()


class RequestTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean()
        self.addCleanup(_clean)
        self.spawned = []
        patcher = mock.patch.object(detached, "spawn", lambda argv, log_name: self.spawned.append((argv, log_name)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = []

    def test_enabled_source_spawns_the_radar_once_and_records_running(self):
        rc = radar_rounds.request({"action": "radar_test_round", "source": "gmail"}, self.log.append)
        self.assertEqual(rc, "running")
        self.assertEqual(self.spawned, [(["act.radar_gmail", "--once"], radar_rounds.LOG_NAME)])
        rec = json.loads(radar_rounds.ROUNDS_PATH.read_text(encoding="utf-8"))["gmail"]
        self.assertEqual(rec["launch"], "running")
        self.assertIsNone(rec["note"])
        self.assertRegex(rec["requested_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_slack_maps_to_its_module(self):
        radar_rounds.request({"source": "slack"}, self.log.append)
        self.assertEqual(self.spawned[0][0], ["act.radar_slack", "--once"])

    def test_switched_off_source_is_noop_and_never_spawns(self):
        # §48.2 真静默：关着的雷达入口直接 return、不写 health——起了也永远不会 done
        config.CONFIG_PATH.write_text("sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        rc = radar_rounds.request({"source": "gmail"}, self.log.append)
        self.assertEqual(rc, "noop")
        self.assertEqual(self.spawned, [])
        rec = radar_rounds.load_rounds()["gmail"]
        self.assertEqual((rec["launch"], rec["note"]), ("noop", "disabled"))
        self.assertTrue(any("switched off" in m for m in self.log))

    def test_malformed_source_is_noop_without_a_record(self):
        for bad in ({"source": "obsidian"}, {"source": 3}, {}, "not-a-dict"):
            with self.subTest(bad=bad):
                self.assertEqual(radar_rounds.request(bad, self.log.append), "noop")
        self.assertEqual(self.spawned, [])
        self.assertEqual(radar_rounds.load_rounds(), {})

    def test_launch_failure_is_recorded_honestly(self):
        with mock.patch.object(detached, "spawn", side_effect=OSError("no fork")):
            rc = radar_rounds.request({"source": "gmail"}, self.log.append)
        self.assertEqual(rc, "noop")
        rec = radar_rounds.load_rounds()["gmail"]
        self.assertEqual((rec["launch"], rec["note"]), ("noop", "launch_failed"))

    def test_actd_routes_the_inbox_action_through_the_detached_table(self):
        self.assertIn("radar_test_round", actd._DETACHED_ACTIONS)
        rc = actd._DETACHED_ACTIONS["radar_test_round"]({"action": "radar_test_round", "source": "slack"})
        self.assertEqual(rc, "running")
        self.assertEqual(self.spawned[0][0], ["act.radar_slack", "--once"])


class ProjectionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean()
        self.addCleanup(_clean)

    def _rounds(self, launch="running", note=None, minutes_ago=1):
        return {"gmail": {"requested_at": _iso(NOW - _dt.timedelta(minutes=minutes_ago)),
                          "launch": launch, "note": note}}

    def test_no_request_is_null(self):
        self.assertIsNone(radar_rounds.projection("gmail", {}, NOW, {}))
        self.assertIsNone(radar_rounds.projection("gmail", {}, NOW, {"gmail": "junk"}))
        self.assertIsNone(radar_rounds.projection("gmail", {}, NOW, {"gmail": {"launch": "running"}}))

    def test_running_until_the_radar_writes_health_after_the_request(self):
        rounds = self._rounds()
        before = {"last_attempt": _iso(NOW - _dt.timedelta(minutes=5))}
        self.assertEqual(radar_rounds.projection("gmail", before, NOW, rounds)["state"], "running")
        after = {"last_attempt": _iso(NOW)}
        out = radar_rounds.projection("gmail", after, NOW, rounds)
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["requested_at"], rounds["gmail"]["requested_at"])
        self.assertIsNone(out["note"])

    def test_lost_after_the_budget_without_a_health_write(self):
        rounds = self._rounds(minutes_ago=11)
        self.assertEqual(radar_rounds.projection("gmail", {}, NOW, rounds)["state"], "lost")
        self.assertEqual(radar_rounds.projection("gmail", None, NOW, rounds)["state"], "lost")
        # 但只要雷达在请求之后落过笔，就是 done——哪怕很久以后才看
        late = {"last_attempt": _iso(NOW - _dt.timedelta(minutes=10))}
        self.assertEqual(radar_rounds.projection("gmail", late, NOW, rounds)["state"], "done")

    def test_noop_carries_the_note(self):
        out = radar_rounds.projection("gmail", {"last_attempt": _iso(NOW)}, NOW,
                                      self._rounds(launch="noop", note="disabled"))
        self.assertEqual((out["state"], out["note"]), ("noop", "disabled"))

    def test_dashboard_carries_last_attempt_and_test_round_add_only(self):
        radar_health.update_radar_health("gmail", ok=True)
        radar_rounds.ROUNDS_PATH.write_text(json.dumps(
            {"gmail": {"requested_at": "2020-01-01T00:00:00Z", "launch": "running", "note": None}}),
            encoding="utf-8")
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(), archived=[])
        gm = dash["radar_sources"]["gmail"]
        self.assertEqual(gm["last_attempt"], gm["last_ok"])
        self.assertEqual(gm["test_round"]["state"], "done")
        # 没请求过的源：null；关着的源：两键都屏蔽（§48.4「关着 = null」）
        self.assertIsNone(dash["radar_sources"]["slack"]["test_round"])
        config.CONFIG_PATH.write_text("sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        gm = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(), archived=[])["radar_sources"]["gmail"]
        self.assertIsNone(gm["test_round"])
        self.assertIsNone(gm["last_attempt"])

    def test_corrupt_rounds_file_never_breaks_the_dashboard(self):
        radar_rounds.ROUNDS_PATH.write_text("{not json", encoding="utf-8")
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(), archived=[])
        self.assertIsNone(dash["radar_sources"]["gmail"]["test_round"])


class InboxWriterTestCase(unittest.TestCase):
    """server 入站面：``{action, source}``，source ∈ gmail|slack，其余零容忍。"""

    def test_builds_the_record_for_both_sources(self):
        for src in ("gmail", "slack"):
            rec = inbox_writer._build_record("radar_test_round", {"action": "radar_test_round", "source": src}, None)
            self.assertEqual(rec, {"action": "radar_test_round", "source": src})

    def test_gates(self):
        with self.assertRaises(InvalidFieldError):
            inbox_writer._build_record("radar_test_round", {"action": "radar_test_round", "source": "obsidian"}, None)
        with self.assertRaises(InvalidFieldError):
            inbox_writer._build_record("radar_test_round", {"action": "radar_test_round"}, None)
        with self.assertRaises(UnknownFieldError):
            inbox_writer._reject_unknown_fields("radar_test_round", {"action": "radar_test_round", "source": "gmail", "id": "R-1"})
        self.assertIn("radar_test_round", inbox_writer.ALLOWED_ACTIONS)
        self.assertEqual(inbox_writer._RADAR_ROUND_SOURCES, frozenset(radar_rounds.SOURCES))


if __name__ == "__main__":
    unittest.main()
