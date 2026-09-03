"""§63 recap store: settings from config, the inbox special-form argv, the
add-only ``recaps[]`` board projection with server-owned marks, and the actd
detached spawn table (act/lib/recap_store.py, act/lib/detached.py, act/actd.py).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import actd
from act.lib import config, detached
from act.lib import recap_sessions as rs
from act.lib import recap_store as store

KEY = "meeting:2026-08-31T1256-zoom"


class SettingsTestCase(unittest.TestCase):
    def test_defaults_from_a_bare_config(self):
        st = store.settings(config.Config())
        self.assertTrue(st["enabled"])
        self.assertEqual(st["default_language"], "auto")
        self.assertFalse(st["slack_draft_enabled"])          # default OFF (owner)
        self.assertEqual(st["slack_targets"], {})
        self.assertEqual((st["max_per_run"], st["max_per_day"], st["retention_days"]), (2, 8, 90))
        self.assertIsNone(st["db_path"])
        self.assertIsInstance(st["options"], rs.Options)

    def test_yaml_block_and_overrides_reach_the_knobs(self):
        cfg = config.Config(raw={"recap": {
            "enabled": "false", "default_language": "EN", "slack_draft": {"enabled": "yes",
            "targets": {"Zoom": "C0123456789", "teams": "not-an-id", "meet": 12}},
            "max_per_run": "3", "max_per_day": 0, "retention_days": "x", "db_path": " /tmp/x.sqlite "}})
        config._apply_recap_block(cfg, cfg.raw)
        st = store.settings(cfg)
        self.assertFalse(st["enabled"])
        self.assertEqual(st["default_language"], "en")
        self.assertTrue(st["slack_draft_enabled"])
        self.assertEqual(st["slack_targets"], {"zoom": "C0123456789"})
        self.assertEqual((st["max_per_run"], st["max_per_day"], st["retention_days"]), (3, 1, 90))
        self.assertEqual(st["db_path"], "/tmp/x.sqlite")

    def test_override_fields_are_registered_with_coercions(self):
        for key in ("recap_enabled", "recap_default_language", "recap_slack_draft_enabled"):
            self.assertIn(key, config._OVERRIDE_FIELDS)
        self.assertEqual(config._OVERRIDE_FIELDS["recap_default_language"]("ZH"), "zh")
        self.assertEqual(config._OVERRIDE_FIELDS["recap_default_language"]("klingon"), "auto")
        self.assertFalse(config._OVERRIDE_FIELDS["recap_slack_draft_enabled"]("false"))
        self.assertEqual(config.RECAP_LANGUAGES, store.LANGUAGES)

    def test_bad_yaml_shapes_keep_defaults(self):
        cfg = config.Config(raw={"recap": "nonsense"})
        config._apply_recap_block(cfg, cfg.raw)
        self.assertTrue(cfg.recap_enabled)
        self.assertEqual(store.settings(cfg)["options"].gap_s, 300)


class InboxArgvTestCase(unittest.TestCase):
    def test_generate_forms(self):
        self.assertEqual(store.inbox_argv({"action": "recap_generate", "meeting_key": KEY}),
                         ["--generate", KEY])
        self.assertEqual(store.inbox_argv({"action": "recap_generate", "meeting_key": KEY,
                                           "note": "fix", "partial": True}),
                         ["--generate", KEY, "--note", "fix", "--partial"])
        self.assertEqual(store.inbox_argv({"action": "recap_generate", "meeting_key": KEY,
                                           "partial": "yes"}), ["--generate", KEY])   # only literal true

    def test_slack_draft_form(self):
        self.assertEqual(store.inbox_argv({"action": "recap_slack_draft", "meeting_key": KEY,
                                           "channel_id": "D0ABCDEF12"}),
                         ["--slack-draft", KEY, "--channel-id", "D0ABCDEF12"])

    def test_malformed_is_none(self):
        bad = [
            None, "x", {},
            {"action": "recap_generate"},
            {"action": "recap_generate", "meeting_key": "meeting:../../etc"},
            {"action": "recap_generate", "meeting_key": KEY, "note": 5},
            {"action": "recap_generate", "meeting_key": KEY, "note": "n" * 501},
            {"action": "recap_slack_draft", "meeting_key": KEY},
            {"action": "recap_slack_draft", "meeting_key": KEY, "channel_id": "lowercase"},
            {"action": "recap_slack_draft", "meeting_key": KEY, "channel_id": "C123 --bg"},
            {"action": "approve", "meeting_key": KEY},
        ]
        for decision in bad:
            with self.subTest(decision=decision):
                self.assertIsNone(store.inbox_argv(decision))

    def test_key_shape(self):
        self.assertTrue(store.valid_key(KEY))
        self.assertTrue(store.valid_key("meeting:2026-08-31T1256-slack-huddle"))
        for bad in ("meeting:2026-08-31T12:56-zoom", "MEETING:2026-08-31T1256-zoom", "R-101",
                    "meeting:2026-08-31T1256-", "meeting:2026-08-31T1256-Zoom", 5, None):
            self.assertFalse(store.valid_key(bad), bad)
        with self.assertRaises(ValueError):
            store.recap_path("R-101")


class ProjectionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-store-")
        self.addCleanup(self.tmp.cleanup)
        mock.patch.object(config, "STATE_DIR", Path(self.tmp.name)).start()
        self.addCleanup(mock.patch.stopall)

    def _session(self, start=1756669000.0, app="zoom"):
        return rs.Session(start=start, end=start + 1200, frames=40, audio_rows=30, app=app, events=[])

    def test_attach_is_add_only_and_never_raises(self):
        dash = {"counts": {}}
        self.assertEqual(store.attach(dash)["recaps"], [])          # no state/recap at all
        store.ensure_dirs()
        store.recap_path(KEY).write_text("{not json", encoding="utf-8")
        self.assertEqual(store.attach(dash)["recaps"], [])          # corrupt file skipped
        with mock.patch.object(store, "projection", side_effect=RuntimeError("x")):
            out = store.attach({"a": 1})
        self.assertEqual(out, {"a": 1})                              # failure = key absent

    def test_projection_merges_marks_strips_history_and_dedupes(self):
        rec = store.new_record(self._session(), KEY, rs.CLOSED)
        rec.update({"en": ["Decided: x"], "zh": ["定了：x"], "version": 2,
                    "history": [{"version": 1}]})
        store.save_recap(rec)
        store._write_json(store.marks_path(), {KEY: {"copied_at": "2026-09-01T00:00:00Z", "sent_at": None}})
        state = store.new_state({"frames": 1, "audio": 1}, "now")
        state["open"] = [store.new_record(self._session(start=1756680000.0), "meeting:2026-08-31T1600-zoom", rs.OPEN),
                         dict(store.new_record(self._session(), KEY, rs.OPEN), status="open")]   # stale duplicate
        store.save_state(state)
        rows = store.projection()
        self.assertEqual([r["key"] for r in rows], ["meeting:2026-08-31T1600-zoom", KEY])
        closed = rows[1]
        self.assertEqual(closed["status"], "closed")                 # the file wins over the OPEN row
        self.assertEqual(closed["copied_at"], "2026-09-01T00:00:00Z")
        self.assertIsNone(closed["sent_at"])
        self.assertNotIn("history", closed)
        self.assertEqual(closed["history_count"], 1)
        self.assertEqual(rows[0]["status"], "open")
        self.assertIsNone(rows[0]["copied_at"])

    def test_projection_cap_and_order(self):
        for i in range(store.PROJECTION_CAP + 5):
            key = "meeting:2026-08-%02dT%02d00-zoom" % (1 + i // 24, i % 24)
            store.save_recap(store.new_record(self._session(start=1756600000.0 + i * 3600), key, rs.CLOSED))
        rows = store.projection()
        self.assertEqual(len(rows), store.PROJECTION_CAP)
        starts = [r["start"] for r in rows]
        self.assertEqual(starts, sorted(starts, reverse=True))

    def test_priors_and_intervals_and_prune(self):
        base = 1756669000.0
        for i, days in enumerate((1, 3, 10, 20)):
            rec = store.new_record(self._session(start=base - days * 86400), "meeting:2026-08-%02dT1200-zoom" % (30 - i), rs.CLOSED)
            rec["en"] = ["Decided: prior %d" % days]
            store.save_recap(rec)
        priors = store.priors_for(base, "America/Los_Angeles")
        self.assertEqual([p["en"][0] for p in priors], ["Decided: prior 1", "Decided: prior 3", "Decided: prior 10"])
        self.assertEqual(len(store.closed_intervals(base, within_s=2 * 86400)), 1)
        self.assertEqual(store.prune(base, retention_days=15), 1)
        self.assertEqual(len(store.list_recaps()), 3)

    def test_dashboard_carries_recaps(self):
        from act.lib import dashboard
        rec = store.new_record(self._session(), KEY, rs.CLOSED)
        store.save_recap(rec)
        dash = dashboard.build_dashboard(reqs=[], archived=[])
        self.assertEqual([r["key"] for r in dash["recaps"]], [KEY])


class DetachedSpawnTestCase(unittest.TestCase):
    def setUp(self):
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def _drop(self, name, decision):
        path = config.INBOX_DIR / name
        path.write_text(json.dumps(decision), encoding="utf-8")
        return path

    def test_recap_generate_spawns_the_recap_module_detached(self):
        path = self._drop("recap-gen.json", {"action": "recap_generate", "meeting_key": KEY,
                                             "note": "fix the deadline", "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn") as spawn:
            n = actd.process_inbox()
        self.assertEqual(n, 1)
        spawn.assert_called_once_with(["act.recap", "--generate", KEY, "--note", "fix the deadline"], "recap.log")
        self.assertFalse(path.exists())

    def test_recap_slack_draft_spawns_with_channel(self):
        path = self._drop("recap-draft.json", {"action": "recap_slack_draft", "meeting_key": KEY,
                                               "channel_id": "C0123456789", "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn") as spawn:
            actd.process_inbox()
        spawn.assert_called_once_with(["act.recap", "--slack-draft", KEY, "--channel-id", "C0123456789"],
                                      "recap.log")
        self.assertFalse(path.exists())

    def test_malformed_recap_decision_is_a_noop_without_spawn(self):
        path = self._drop("recap-bad.json", {"action": "recap_generate", "meeting_key": "R-101",
                                             "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn") as spawn, \
                mock.patch.object(actd, "_write_applied_ack") as ack:
            actd.process_inbox()
        spawn.assert_not_called()
        ack.assert_called_once_with("recap-bad", "noop")
        self.assertFalse(path.exists())

    def test_weekly_digest_still_routes_through_the_table(self):
        self._drop("wd.json", {"action": "weekly_digest_now", "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn") as spawn:
            actd.process_inbox()
        spawn.assert_called_once_with(["act.weekly_digest", "--now"], "weekly_digest.log")

    def test_launch_translates_failures_to_noop(self):
        said = []
        with mock.patch.object(detached, "spawn", side_effect=OSError("no python")):
            self.assertEqual(detached.launch(["act.recap"], "recap.log", "recap_generate", said.append), "noop")
        self.assertIn("launch FAILED", said[0])
        with mock.patch.object(detached, "spawn"):
            self.assertEqual(detached.launch(["act.recap"], "recap.log", "recap_generate"), "running")

    def test_spawn_shape(self):
        with mock.patch.object(detached.subprocess, "Popen") as popen:
            detached.spawn(["act.recap", "--once"], "recap.log")
        argv = popen.call_args[0][0]
        self.assertEqual(argv[1:], ["-m", "act.recap", "--once"])
        self.assertTrue(popen.call_args[1]["start_new_session"])
        self.assertEqual(popen.call_args[1]["cwd"], str(config.HOME))


if __name__ == "__main__":
    unittest.main()
