"""weekly_digest.run — ingest window -> digest review card + proposal cards
(CONTRACT §24). The injectable ``runner`` replaces headless ``claude -p``.

Pinned here:
- cost guards: zero notes in the window / nothing new since the last run ->
  skip WITHOUT calling claude (runner must not fire);
- strict-JSON output -> a review-lane digest card (status=review, final_draft
  = full text, delivery_mode=chat) + <=3 card_sent proposal cards, all with
  source channel "weekly-digest";
- same-week re-run dedupes through merge_or_new (no duplicate cards) and
  refreshes the digest content;
- schedule gate: wrong weekday / before the hour / already ran -> not due;
- malformed output -> ok=False, no cards, marker untouched (retry next fire);
- the actd inbox action ``weekly_digest_now`` consumes the file and spawns
  the detached generator.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import weekly_digest
from act.lib import config, registry


def _payload(digest="这周主要在弄 telemetry 和审计修复。",
             suggestions=None):
    if suggestions is None:
        suggestions = [
            {"title": "自动生成周报草稿", "summary": "每周五自动起草周报",
             "plan": ["收集本周 notes", "起草", "落入待验收"]},
            {"title": "自动整理下载目录", "summary": "每天归档 Downloads",
             "plan": ["按类型分目录"]},
        ]
    return json.dumps({"digest": digest, "suggestions": suggestions},
                      ensure_ascii=False)


class WeeklyDigestBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="wd-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n', encoding="utf-8")
        # keep tests hermetic: no osascript / phone mirror
        patcher = mock.patch.object(weekly_digest.notify, "notify",
                                    return_value=True)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)
        self.now = dt.datetime(2026, 7, 6, 10, 0)  # a Monday, 10:00

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        marker = config.STATE_DIR / weekly_digest.MARKER_PATH_NAME
        if marker.exists():
            marker.unlink()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _note(self, name, text, age_days=1.0):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        ts = self.now.timestamp() - age_days * 86400
        os.utime(p, (ts, ts))
        return p


class CostGuardTestCase(WeeklyDigestBase):
    def test_no_notes_skips_without_calling_claude(self):
        called = []
        summary = weekly_digest.run(force=True, now=self.now,
                                    runner=lambda p: called.append(p) or "{}")
        self.assertEqual(summary["skipped"], "no_data")
        self.assertEqual(called, [])          # cost guard: claude never ran
        self.assertEqual(registry.load_all(), [])
        self.notify.assert_called()           # forced run still gives feedback

    def test_old_notes_outside_window_skip(self):
        self._note("2026-06-01-old.md", "ancient", age_days=30)
        summary = weekly_digest.run(force=True, now=self.now,
                                    runner=lambda p: self.fail("must not run"))
        self.assertEqual(summary["skipped"], "no_data")

    def test_no_new_data_since_last_run_skips_scheduled_pass(self):
        # Tuesday: the user presses "Generate now" (forced run consumes the
        # only note); next Monday the scheduled run sees the same note still
        # inside the window but nothing newer -> skip without calling claude.
        tuesday = dt.datetime(2026, 7, 7, 10, 0)
        p = self._note("2026-07-07-a.md", "same old note", age_days=0)
        ts = dt.datetime(2026, 7, 7, 6, 0).timestamp()
        os.utime(p, (ts, ts))
        summary = weekly_digest.run(force=True, now=tuesday,
                                    runner=lambda p: _payload())
        self.assertIsNone(summary["skipped"])
        next_monday = dt.datetime(2026, 7, 13, 10, 0)
        summary2 = weekly_digest.run(now=next_monday,
                                     runner=lambda p: self.fail("must not run"))
        self.assertEqual(summary2["skipped"], "no_new_data")

    def test_disabled_flag_no_ops(self):
        config.CONFIG_PATH.write_text(
            'sources:\n'
            f'  obsidian_raw: "{self.raw.as_posix()}"\n'
            '  weekly_digest:\n    enabled: false\n', encoding="utf-8")
        self._note("2026-07-05-a.md", "note", age_days=1)
        summary = weekly_digest.run(force=True, now=self.now,
                                    runner=lambda p: self.fail("must not run"))
        self.assertEqual(summary["skipped"], "disabled")


class CardsTestCase(WeeklyDigestBase):
    def test_digest_and_suggestions_become_cards(self):
        self._note("2026-07-05-a.md", "worked on telemetry consent", age_days=1)
        self._note("2026-07-03-b.md", "wrote weekly report by hand", age_days=3)
        prompts = []
        summary = weekly_digest.run(force=True, now=self.now,
                                    runner=lambda p: prompts.append(p) or _payload())
        self.assertIsNone(summary["skipped"])
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["notes"], 2)
        self.assertEqual(summary["suggestions"], 2)

        # outbound prompt carries the fenced note material
        self.assertIn("UNTRUSTED SOURCE MATERIAL", prompts[0])
        self.assertIn("telemetry consent", prompts[0])

        reqs = {r.id: r for r in registry.load_all()}
        digest = reqs[summary["digest_id"]]
        self.assertEqual(digest.status, "review")
        self.assertEqual(digest.type, "digest")
        self.assertEqual(digest.delivery_mode, "chat")
        self.assertIn("telemetry", digest.execution["final_draft"])
        self.assertTrue(digest.execution["review_at"])
        self.assertEqual(digest.sources[0]["channel"], "weekly-digest")

        for sid in summary["suggestion_ids"]:
            s = reqs[sid]
            self.assertEqual(s.status, "card_sent")
            self.assertEqual(s.type, "automation")
            self.assertEqual(s.sources[0]["channel"], "weekly-digest")
            self.assertTrue(s.plan)

    def test_same_week_rerun_merges_and_refreshes_digest(self):
        self._note("2026-07-05-a.md", "first pass", age_days=1)
        s1 = weekly_digest.run(force=True, now=self.now,
                               runner=lambda p: _payload(digest="第一版"))
        n_after_first = len(registry.load_all())
        self._note("2026-07-06-b.md", "new material", age_days=0.1)
        s2 = weekly_digest.run(force=True, now=self.now,
                               runner=lambda p: _payload(digest="第二版"))
        # dedupe: identical titles merged through merge_or_new, no new cards
        self.assertEqual(len(registry.load_all()), n_after_first)
        self.assertEqual(s1["digest_id"], s2["digest_id"])
        digest = registry.load(s2["digest_id"])
        self.assertEqual(digest.execution["final_draft"], "第二版")
        self.assertEqual(digest.status, "review")

    def test_suggestions_capped_at_three(self):
        self._note("2026-07-05-a.md", "busy week", age_days=1)
        five = [{"title": f"自动化建议 {i} 号", "summary": "s", "plan": ["p"]}
                for i in range(5)]
        summary = weekly_digest.run(
            force=True, now=self.now,
            runner=lambda p: _payload(suggestions=five))
        self.assertEqual(summary["suggestions"], 3)

    def test_malformed_output_files_nothing_and_keeps_marker(self):
        self._note("2026-07-05-a.md", "note", age_days=1)
        summary = weekly_digest.run(force=True, now=self.now,
                                    runner=lambda p: "sorry, no json here")
        self.assertFalse(summary["ok"])
        self.assertEqual(registry.load_all(), [])
        # marker untouched -> next scheduled fire retries
        self.assertEqual(weekly_digest._read_marker(), {})


class ScheduleGateTestCase(WeeklyDigestBase):
    def test_wrong_day_or_early_hour_not_due(self):
        cfg = config.load_config()
        tuesday = dt.datetime(2026, 7, 7, 10, 0)
        self.assertFalse(weekly_digest.due(cfg, {}, tuesday))
        monday_early = dt.datetime(2026, 7, 6, 8, 59)
        self.assertFalse(weekly_digest.due(cfg, {}, monday_early))
        self.assertTrue(weekly_digest.due(cfg, {}, self.now))

    def test_recent_last_run_not_due_again(self):
        cfg = config.load_config()
        marker = {"last_run": "2026-07-06"}
        same_day_later = dt.datetime(2026, 7, 6, 15, 0)
        self.assertFalse(weekly_digest.due(cfg, marker, same_day_later))
        next_monday = dt.datetime(2026, 7, 13, 10, 0)
        self.assertTrue(weekly_digest.due(cfg, marker, next_monday))

    def test_scheduled_run_files_cards_when_due(self):
        self._note("2026-07-05-a.md", "note", age_days=1)
        summary = weekly_digest.run(now=self.now, runner=lambda p: _payload())
        self.assertIsNone(summary["skipped"])
        self.assertTrue(summary["ok"])

    def test_not_due_is_quiet(self):
        self._note("2026-07-05-a.md", "note", age_days=1)
        tuesday = dt.datetime(2026, 7, 7, 10, 0)
        summary = weekly_digest.run(now=tuesday,
                                    runner=lambda p: self.fail("must not run"))
        self.assertEqual(summary["skipped"], "not_due")


class InboxActionTestCase(WeeklyDigestBase):
    def test_weekly_digest_now_inbox_action_spawns_and_consumes(self):
        from act import actd
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = config.INBOX_DIR / "test-weekly-digest.json"
        path.write_text(json.dumps({"action": "weekly_digest_now",
                                    "ts": "2026-07-06T10:00:00Z"}),
                        encoding="utf-8")
        with mock.patch.object(actd, "_spawn_weekly_digest") as spawn:
            n = actd.process_inbox()
        self.assertEqual(n, 1)
        spawn.assert_called_once()
        self.assertFalse(path.exists())  # actd reads then deletes


if __name__ == "__main__":
    unittest.main()
