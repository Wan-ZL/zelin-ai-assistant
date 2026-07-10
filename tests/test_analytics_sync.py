"""act/lib/analytics_sync.py — default-on Supabase telemetry uploader.

sync_once must (a) upload only complete new lines and advance the byte cursor,
(b) split uploads into BATCH_SIZE batches with the cursor saved per batch,
(c) be a silent no-op when telemetry is disabled (opt-out) or the URL is
empty, (d) skip (and count) malformed lines without crashing, (e) leave a
half-written trailing line for the next run, (f) write the cursor atomically,
(g) resolve the upload key as key file -> built-in publishable key, and
(h) never upload before a consent surface existed: the app's shown-marker
(state/telemetry_consent_shown), a config.yaml ``telemetry:`` block, or a
telemetry key in settings_overrides.json (ConsentGateTestCase).

The local JSONL file is never modified. Transport is injected — no network.
Everything lives under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import contextlib
import io
import json
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import analytics, config, secrets
from act.lib import analytics_sync as sync


def _cfg(enabled: bool = True) -> config.Config:
    c = config.Config()
    c.telemetry_enabled = enabled
    c.telemetry_supabase_url = "https://example.supabase.co"
    return c


def _write_events(*lines: str, append: bool = False) -> None:
    analytics.ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(analytics.EVENTS_PATH, mode, encoding="utf-8") as f:
        f.write("".join(lines))


def _event_line(event: str, **fields) -> str:
    rec = {"ts": "2026-07-09T01:02:03Z", "event": event, **fields}
    return json.dumps(rec, ensure_ascii=False) + "\n"


def _cursor_offset() -> int:
    data = json.loads(sync.CURSOR_PATH.read_text(encoding="utf-8"))
    return data["files"][analytics.EVENTS_PATH.name]


class AnalyticsSyncTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # a key file pins the resolution away from the built-in publishable
        # key (KeyResolutionTestCase covers the resolution order itself)
        secrets.write_secret(sync.SUPABASE_SERVICE_KEY_FILE, "test-service-key")

    def setUp(self):
        # consent surface shown — the gate itself is ConsentGateTestCase's job
        sync.CONSENT_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        sync.CONSENT_MARKER_PATH.write_text("2026-07-09T00:00:00Z\n",
                                            encoding="utf-8")
        for p in (analytics.EVENTS_PATH, sync.CURSOR_PATH):
            if p.exists():
                p.unlink()
        self.batches: list = []

    def _transport(self, rows):
        self.batches.append(list(rows))

    def _sync(self, transport=None):
        return sync.sync_once(cfg=_cfg(), transport=transport or self._transport)

    # -- cursor advance -------------------------------------------------- #
    def test_uploads_new_events_and_advances_cursor(self):
        _write_events(_event_line("inbox_approve", req="R-001", sid="ab12cd34",
                                  v="0.10.3", source="slack"))
        size = analytics.EVENTS_PATH.stat().st_size

        stats = self._sync()
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["uploaded"], 1)
        self.assertEqual(_cursor_offset(), size)

        row = self.batches[0][0]
        self.assertEqual(row["event"], "inbox_approve")
        self.assertEqual(row["sid"], "ab12cd34")
        self.assertEqual(row["app_version"], "0.10.3")
        self.assertEqual(row["source"], "slack")
        self.assertEqual(row["client_ts"], "2026-07-09T01:02:03Z")
        self.assertEqual(row["props"]["req"], "R-001")  # full original record
        self.assertTrue(row["device_id"])  # stable per-install id
        self.assertEqual(row["device_id"],
                         sync.DEVICE_ID_PATH.read_text().strip())

        # second run re-uploads nothing — only run 1's own telemetry_sync
        # event (appended after the cursor was saved) is new
        self.batches.clear()
        self._sync()
        self.assertEqual([r["event"] for b in self.batches for r in b],
                         ["telemetry_sync"])

    # -- batch split ------------------------------------------------------ #
    def test_splits_uploads_into_batches_and_saves_cursor_per_batch(self):
        n = sync.BATCH_SIZE + 7
        _write_events(*[_event_line("card_sent", i=i) for i in range(n)])

        stats = self._sync()
        self.assertEqual(stats["uploaded"], n)
        self.assertEqual(stats["batches"], 2)
        self.assertEqual([len(b) for b in self.batches], [sync.BATCH_SIZE, 7])

        # a failure AFTER the first batch must not lose that batch's cursor
        for p in (analytics.EVENTS_PATH, sync.CURSOR_PATH):
            p.unlink()
        _write_events(*[_event_line("card_sent", i=i) for i in range(n)])

        calls = []

        def flaky(rows):
            if calls:
                raise OSError("supabase unreachable")
            calls.append(list(rows))

        stats = self._sync(transport=flaky)
        self.assertFalse(stats["ok"])
        self.assertEqual(stats["uploaded"], sync.BATCH_SIZE)
        self.assertIn("unreachable", stats["error"])
        saved = _cursor_offset()  # exactly the end of batch 1
        self.assertGreater(saved, 0)
        self.assertLess(saved, analytics.EVENTS_PATH.stat().st_size)

    # -- disabled no-op ---------------------------------------------------- #
    def test_disabled_is_a_silent_noop(self):
        _write_events(_event_line("inbox_approve"))
        before = analytics.EVENTS_PATH.read_text(encoding="utf-8")

        stats = sync.sync_once(cfg=_cfg(enabled=False),
                               transport=self._transport)
        self.assertEqual(stats["skipped"], "disabled")
        self.assertEqual(self.batches, [])
        self.assertFalse(sync.CURSOR_PATH.exists())
        # silent: not even a telemetry_sync event is logged
        self.assertEqual(analytics.EVENTS_PATH.read_text(encoding="utf-8"),
                         before)

    def test_default_config_uploads_by_default(self):
        # default-on telemetry (docs/TELEMETRY.md): a plain Config() has
        # enabled=True + the maintainer URL, so events upload out of the box
        _write_events(_event_line("inbox_approve"))
        cfg = config.Config()
        self.assertTrue(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_supabase_url,
                         config.DEFAULT_TELEMETRY_SUPABASE_URL)
        stats = sync.sync_once(cfg=cfg, transport=self._transport)
        self.assertNotIn("skipped", stats)
        self.assertEqual(stats["uploaded"], 1)

    def test_empty_supabase_url_disables_uploads_entirely(self):
        # forks' hard off switch (docs/TELEMETRY.md): supabase_url "" -> no-op
        _write_events(_event_line("inbox_approve"))
        cfg = config.Config()
        cfg.telemetry_supabase_url = ""
        stats = sync.sync_once(cfg=cfg, transport=self._transport)
        self.assertEqual(stats["skipped"], "disabled")
        self.assertEqual(self.batches, [])

    # -- malformed line skip ------------------------------------------------ #
    def test_malformed_lines_are_counted_skipped_and_cursor_passes_them(self):
        _write_events(
            _event_line("good_one"),
            "{{{ not json at all\n",
            json.dumps({"ts": "x", "no_event": True}) + "\n",  # no "event"
            _event_line("good_two"),
        )
        size = analytics.EVENTS_PATH.stat().st_size

        stats = self._sync()
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["uploaded"], 2)
        self.assertEqual(stats["malformed"], 2)
        self.assertEqual([r["event"] for r in self.batches[0]],
                         ["good_one", "good_two"])
        self.assertEqual(_cursor_offset(), size)  # bad lines consumed once

    # -- half-written trailing line ----------------------------------------- #
    def test_half_written_trailing_line_is_left_for_the_next_run(self):
        complete = _event_line("good_one") + _event_line("good_two")
        _write_events(complete, '{"ts": "2026-07-09T01:02:03Z", "event": "in_fl')

        stats = self._sync()
        self.assertEqual(stats["uploaded"], 2)
        self.assertEqual(_cursor_offset(), len(complete.encode("utf-8")))

        # writer finishes the line (rewrite the file: run 1's own
        # telemetry_sync event appended after the half line would otherwise
        # pollute the simulation) -> next run uploads exactly that event
        _write_events(complete, _event_line("in_flight"))
        self.batches.clear()
        self._sync()
        uploaded = [r["event"] for b in self.batches for r in b]
        self.assertEqual(uploaded, ["in_flight"])

    # -- key resolution: key file wins, publishable key is the fallback ------ #
    def test_key_file_wins_over_publishable_default(self):
        # setUpClass wrote the secrets file — it must beat the built-in key
        self.assertEqual(sync._resolve_key(config.Config()), "test-service-key")

    # -- atomic cursor write -------------------------------------------------- #
    def test_cursor_write_is_atomic_and_never_torn(self):
        _write_events(_event_line("good_one"))
        self._sync()
        self.assertFalse(sync.CURSOR_PATH.with_suffix(".json.tmp").exists())
        json.loads(sync.CURSOR_PATH.read_text(encoding="utf-8"))  # valid JSON

        # corrupt cursor -> next run starts fresh instead of crashing
        sync.CURSOR_PATH.write_text("{{{ torn", encoding="utf-8")
        stats = self._sync()
        self.assertTrue(stats["ok"])
        json.loads(sync.CURSOR_PATH.read_text(encoding="utf-8"))


class ConsentGateTestCase(unittest.TestCase):
    """Pre-consent upload window (docs/TELEMETRY.md "上传何时发生"): install.sh
    installs the hourly cron unconditionally, so sync_once must no-op until a
    consent surface existed — the app's shown-marker, an explicit config.yaml
    ``telemetry:`` block, or a telemetry key in settings_overrides.json."""

    def setUp(self):
        for p in (sync.CONSENT_MARKER_PATH, config.CONFIG_PATH,
                  config.SETTINGS_OVERRIDES_PATH, analytics.EVENTS_PATH,
                  sync.CURSOR_PATH):
            if p.exists():
                p.unlink()
        _write_events(_event_line("inbox_approve"))
        self.batches: list = []

    def tearDown(self):
        # leave no consent state behind for later test modules
        for p in (sync.CONSENT_MARKER_PATH, config.CONFIG_PATH,
                  config.SETTINGS_OVERRIDES_PATH):
            if p.exists():
                p.unlink()

    def _transport(self, rows):
        self.batches.append(list(rows))

    def test_no_marker_and_no_explicit_config_blocks_upload(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            stats = sync.sync_once(cfg=_cfg(), transport=self._transport)
        self.assertEqual(stats["skipped"], "consent_pending")
        self.assertEqual(self.batches, [])
        self.assertFalse(sync.CURSOR_PATH.exists())
        # the cron log gets a clear line instead of a silent mystery no-op
        self.assertIn("waiting for first-run consent surface", buf.getvalue())

    def test_marker_file_unblocks_upload(self):
        sync.CONSENT_MARKER_PATH.write_text("2026-07-09T00:00:00Z\n",
                                            encoding="utf-8")
        stats = sync.sync_once(cfg=_cfg(), transport=self._transport)
        self.assertNotIn("skipped", stats)
        self.assertEqual(stats["uploaded"], 1)

    def test_explicit_config_yaml_telemetry_section_unblocks_upload(self):
        config.CONFIG_PATH.write_text(
            "owner:\n  name: Test\ntelemetry:\n  enabled: true\n",
            encoding="utf-8")
        stats = sync.sync_once(cfg=_cfg(), transport=self._transport)
        self.assertNotIn("skipped", stats)
        self.assertEqual(stats["uploaded"], 1)

    def test_settings_overrides_telemetry_key_unblocks_upload(self):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"telemetry": {"enabled": True}}), encoding="utf-8")
        stats = sync.sync_once(cfg=_cfg(), transport=self._transport)
        self.assertNotIn("skipped", stats)
        self.assertEqual(stats["uploaded"], 1)


class KeyResolutionTestCase(unittest.TestCase):
    """_resolve_key order: secrets file -> telemetry.key_path -> built-in
    publishable key (default-on telemetry, docs/TELEMETRY.md)."""

    def setUp(self):
        # start from "no key file configured"
        path = secrets.SECRETS_DIR / sync.SUPABASE_SERVICE_KEY_FILE
        if path.exists():
            path.unlink()

    def test_publishable_key_is_the_default(self):
        self.assertEqual(sync._resolve_key(config.Config()),
                         config.DEFAULT_TELEMETRY_PUBLISHABLE_KEY)
        self.assertTrue(
            config.DEFAULT_TELEMETRY_PUBLISHABLE_KEY.startswith(
                "sb_publishable_"))  # never ship a secret key as the default

    def test_secrets_file_wins(self):
        secrets.write_secret(sync.SUPABASE_SERVICE_KEY_FILE, "svc-key-123")
        try:
            self.assertEqual(sync._resolve_key(config.Config()), "svc-key-123")
        finally:
            (secrets.SECRETS_DIR / sync.SUPABASE_SERVICE_KEY_FILE).unlink()

    def test_explicit_key_path_wins_over_publishable_default(self):
        path = Path(TMP_HOME) / "alt-supabase-key.txt"
        path.write_text("alt-key-456\n", encoding="utf-8")
        cfg = config.Config()
        cfg.telemetry_key_path = str(path)
        self.assertEqual(sync._resolve_key(cfg), "alt-key-456")


if __name__ == "__main__":
    unittest.main()
