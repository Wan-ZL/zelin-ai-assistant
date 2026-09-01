"""act/digest.py cadence gate — ``digest.frequency`` (CONTRACT §17, D19).

Owner 2026-09-01: 「像这种每日摘要，好像在设置里面没法关，几天没看就攒起来了……
能不能在设置里面让我能够改成一周或者两天摘要，或者完全关掉」. Pinned here:

- the knob: ``off | daily | every2days | weekly``, default **off**; config.yaml
  ``digest.frequency`` and the settings_overrides flat key ``digest_frequency``
  both feed ``Config.digest_frequency``; typos fail-quiet to off;
- ``due()`` with a fake clock: rolling interval from the ``state/digest.json``
  marker (absent/corrupt marker = due), never due when off;
- ``run()``: off/not-due scheduled passes publish nothing and are QUIET (no
  print, no analytics) — the cron fires daily now, so a default-off knob
  must not leave a line a day in state/digest.log; ``force`` (``--now``)
  bypasses the cadence but not ``features.digest``; a successful publish
  advances the marker; a marker that cannot be written is one readable log
  line + ``marker_error`` in the summary, never a traceback (review M3);
- the install.sh crontab line fires daily WITHOUT ``--now`` (the pre-D19
  Monday-only ``--now`` line would have forced a weekly card past ``off``).

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py);
publish_digest is mocked — the card content has its own pins in
test_audit_digest.py.
"""
import datetime as _dt
import io
import json
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import digest
from act.lib import analytics, config
from act.lib.registry import Requirement

_REPO = Path(__file__).resolve().parents[1]


class _Base(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.addCleanup(self._cleanup)

    @staticmethod
    def _cleanup():
        for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH,
                  config.STATE_DIR / digest.MARKER_PATH_NAME):
            if p.exists():
                p.unlink()

    @staticmethod
    def _yaml(freq) -> None:
        config.CONFIG_PATH.write_text(f"digest:\n  frequency: {freq}\n",
                                      encoding="utf-8")


class KnobTestCase(_Base):
    def test_default_is_off(self):
        self.assertEqual(config.DEFAULT_DIGEST_FREQUENCY, "off")
        self.assertEqual(config.Config().digest_frequency, "off")
        self.assertEqual(config.load_config().digest_frequency, "off")

    def test_value_set_is_closed(self):
        self.assertEqual(config.DIGEST_FREQUENCIES,
                         ("off", "daily", "every2days", "weekly"))
        # every schedulable value has an interval; off has none
        self.assertEqual(set(digest.FREQUENCY_DAYS),
                         set(config.DIGEST_FREQUENCIES) - {"off"})

    def test_yaml_block_each_value(self):
        for v in config.DIGEST_FREQUENCIES:
            with self.subTest(v=v):
                self._yaml(v)
                self.assertEqual(config.load_config().digest_frequency, v)

    def test_yaml_typo_or_unknown_fails_quiet_to_off(self):
        for bad in ("weekl", "hourly", "true", "", "[1, 2]"):
            with self.subTest(bad=bad):
                self._yaml(bad)
                self.assertEqual(config.load_config().digest_frequency, "off")

    def test_yaml_spelling_variants_normalise(self):
        for spelled in ("Weekly", " every_2_days ", "every-2-days", "DAILY"):
            with self.subTest(spelled=spelled):
                self._yaml(f'"{spelled}"')
                self.assertIn(config.load_config().digest_frequency,
                              ("weekly", "every2days", "daily"))

    def test_legacy_digest_weekly_key_is_ignored(self):
        # config.example.yaml shipped `digest: {weekly: monday}` for years and
        # nothing ever read it; it must stay an ignored unknown key
        config.CONFIG_PATH.write_text("digest:\n  weekly: monday\n",
                                      encoding="utf-8")
        self.assertEqual(config.load_config().digest_frequency, "off")

    def test_overrides_flat_key_wins(self):
        # the Settings UI writes this key (§15.3 diff-write allowlist)
        self.assertIn("digest_frequency", config._OVERRIDE_FIELDS)
        self._yaml("weekly")
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"digest_frequency": "every2days"}), encoding="utf-8")
        self.assertEqual(config.load_config().digest_frequency, "every2days")

    def test_overrides_bad_value_fails_quiet_to_off(self):
        self._yaml("weekly")
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"digest_frequency": "sometimes"}), encoding="utf-8")
        self.assertEqual(config.load_config().digest_frequency, "off")

    def test_example_yaml_ships_the_knob_off(self):
        text = (_REPO / "config.example.yaml").read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r"^digest:", text, re.M), "digest block missing")
        self.assertIsNotNone(re.search(r"^\s+frequency:\s*off\b", text, re.M))
        self.assertIsNone(re.search(r"^\s+weekly:\s*monday", text, re.M),
                          "legacy key must go")


class DueTestCase(_Base):
    """Fake-clock pins for the rolling-interval gate."""

    TODAY = _dt.date(2026, 9, 1)  # a Tuesday — the gate must not care

    def _cfg(self, freq) -> config.Config:
        return config.Config(digest_frequency=freq)

    def test_off_is_never_due(self):
        cfg = self._cfg("off")
        self.assertFalse(digest.due(cfg, {}, self.TODAY))
        self.assertFalse(digest.due(cfg, {"last_run": "2020-01-01"}, self.TODAY))

    def test_unknown_value_is_never_due(self):
        # belt and braces: the config layer already coerces, but a Config
        # built by hand must not schedule anything either
        self.assertFalse(digest.due(self._cfg("hourly"), {}, self.TODAY))

    def test_no_marker_is_due_for_every_schedulable_value(self):
        for v in ("daily", "every2days", "weekly"):
            with self.subTest(v=v):
                self.assertTrue(digest.due(self._cfg(v), {}, self.TODAY))

    def test_corrupt_marker_counts_as_absent(self):
        self.assertTrue(digest.due(self._cfg("weekly"),
                                   {"last_run": "yesterday-ish"}, self.TODAY))
        self.assertTrue(digest.due(self._cfg("weekly"),
                                   {"last_run": 42}, self.TODAY))

    def test_daily(self):
        cfg = self._cfg("daily")
        self.assertFalse(digest.due(cfg, {"last_run": "2026-09-01"}, self.TODAY))
        self.assertTrue(digest.due(cfg, {"last_run": "2026-08-31"}, self.TODAY))

    def test_every2days(self):
        cfg = self._cfg("every2days")
        self.assertFalse(digest.due(cfg, {"last_run": "2026-09-01"}, self.TODAY))
        self.assertFalse(digest.due(cfg, {"last_run": "2026-08-31"}, self.TODAY))
        self.assertTrue(digest.due(cfg, {"last_run": "2026-08-30"}, self.TODAY))
        self.assertTrue(digest.due(cfg, {"last_run": "2026-08-01"}, self.TODAY))

    def test_weekly_is_rolling_not_pinned_to_monday(self):
        cfg = self._cfg("weekly")
        for age in range(0, 7):
            day = (self.TODAY - _dt.timedelta(days=age)).isoformat()
            self.assertFalse(digest.due(cfg, {"last_run": day}, self.TODAY), age)
        self.assertTrue(digest.due(cfg, {"last_run": "2026-08-25"}, self.TODAY))
        # a Monday with a 3-day-old marker is NOT due — cadence, not weekday
        monday = _dt.date(2026, 9, 7)
        self.assertFalse(digest.due(cfg, {"last_run": "2026-09-04"}, monday))

    def test_sequence_over_a_fortnight(self):
        """Walk a daily cron fire for 14 days per value and count runs."""
        expected = {"off": 0, "daily": 14, "every2days": 7, "weekly": 2}
        start = _dt.date(2026, 9, 1)
        for v, n in expected.items():
            with self.subTest(v=v):
                cfg, marker, runs = self._cfg(v), {}, 0
                for i in range(14):
                    today = start + _dt.timedelta(days=i)
                    if digest.due(cfg, marker, today):
                        runs += 1
                        marker = {"last_run": today.isoformat()}
                self.assertEqual(runs, n)


class RunTestCase(_Base):
    """``run()`` — the scheduled entry the daily cron line hits."""

    TODAY = _dt.date(2026, 9, 1)

    def setUp(self):
        super().setUp()
        self.card = Requirement(id="R-777", title="digest", status="review")
        p = mock.patch.object(digest, "publish_digest", return_value=self.card)
        self.publish = p.start()
        self.addCleanup(p.stop)

    def _quiet_run(self, **kw):
        buf = io.StringIO()
        with mock.patch.object(analytics, "log_event") as ev, redirect_stdout(buf):
            summary = digest.run(today=self.TODAY, **kw)
        return summary, buf.getvalue(), ev

    def test_default_off_publishes_nothing_and_is_quiet(self):
        summary, out, ev = self._quiet_run()
        self.assertEqual(summary["skipped"], "off")
        self.publish.assert_not_called()
        self.assertEqual(out, "")
        ev.assert_not_called()
        self.assertFalse((config.STATE_DIR / digest.MARKER_PATH_NAME).exists())

    def test_not_due_is_quiet(self):
        self._yaml("weekly")
        digest._write_marker({"last_run": "2026-08-30"})
        summary, out, ev = self._quiet_run()
        self.assertEqual(summary["skipped"], "not_due")
        self.publish.assert_not_called()
        self.assertEqual(out, "")
        ev.assert_not_called()
        # marker untouched
        self.assertEqual(digest._read_marker(), {"last_run": "2026-08-30"})

    def test_due_publishes_and_advances_marker(self):
        self._yaml("daily")
        summary, _out, _ev = self._quiet_run()
        self.assertIsNone(summary["skipped"])
        self.assertEqual(summary["id"], "R-777")
        self.publish.assert_called_once_with(self.TODAY)
        self.assertEqual(digest._read_marker(), {"last_run": "2026-09-01"})
        # same day again: no second card
        self.publish.reset_mock()
        summary2, _o, _e = self._quiet_run()
        self.assertEqual(summary2["skipped"], "not_due")
        self.publish.assert_not_called()

    def test_force_bypasses_off_and_advances_marker(self):
        # `python -m act.digest --now` is a human asking for one now
        summary, _out, _ev = self._quiet_run(force=True)
        self.assertIsNone(summary["skipped"])
        self.publish.assert_called_once()
        self.assertEqual(digest._read_marker(), {"last_run": "2026-09-01"})

    def test_features_digest_off_wins_even_over_force(self):
        # §16 master kill switch keeps its pre-D19 precedence
        self._yaml("daily")
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"features": {"digest": False}}), encoding="utf-8")
        for force in (False, True):
            with self.subTest(force=force):
                summary, _out, _ev = self._quiet_run(force=force)
                self.assertEqual(summary["skipped"], "disabled")
        self.publish.assert_not_called()

    def test_marker_write_failure_is_one_line_and_card_still_published(self):
        # review M3: state/digest.json unwritable (perm/disk). An absent
        # marker reads as due, so `weekly` would degrade to a card a day —
        # that cannot be fixed here, but it must be VISIBLE as one readable
        # line (+ summary["marker_error"]) rather than a traceback that hides
        # the fact the card WAS published, and `--now` must still print the id.
        self._yaml("weekly")
        with mock.patch.object(digest, "_write_marker",
                               side_effect=PermissionError(13, "denied")):
            summary, out, _ev = self._quiet_run()
            self.assertIsNone(summary["skipped"])
            self.assertEqual(summary["id"], "R-777")
            self.assertIn("PermissionError", summary["marker_error"])
            lines = [ln for ln in out.splitlines() if "marker write failed" in ln]
            self.assertEqual(len(lines), 1)
            self.assertIn(digest.MARKER_PATH_NAME, lines[0])
            self.assertEqual(digest._read_marker(), {})
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(digest.main(["--now"]), 0)
            self.assertIn("R-777", buf.getvalue())
        # a healthy write carries no marker_error key at all
        summary, _out, _ev = self._quiet_run(force=True)
        self.assertNotIn("marker_error", summary)

    def test_main_cli_exit_codes_and_output(self):
        # scheduled fire on default config: exit 0, nothing printed
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = digest.main([])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")
        # --now prints the card id
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = digest.main(["--now"])
        self.assertEqual(rc, 0)
        self.assertIn("R-777", buf.getvalue())


class CronLineTestCase(unittest.TestCase):
    """install.sh must fire act.digest DAILY and WITHOUT --now (§17 D19)."""

    def test_install_sh_digest_line(self):
        text = (_REPO / "install.sh").read_text(encoding="utf-8")
        m = re.search(r'^DIGEST_LINE="([^"]+)"', text, re.M)
        self.assertIsNotNone(m, "DIGEST_LINE assignment missing")
        line = m.group(1)
        self.assertTrue(line.startswith("7 9 * * * "), line)   # daily, not `* * 1`
        self.assertIn("-m act.digest", line)
        self.assertNotIn("--now", line)
        # the legacy Monday-only line is replaced, not kept alongside
        self.assertIn("grep -v 'act\\.digest'", text)


if __name__ == "__main__":
    unittest.main()
