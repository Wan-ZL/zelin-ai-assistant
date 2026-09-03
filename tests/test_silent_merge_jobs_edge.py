"""silent_merge job-file plumbing edges (CONTRACT §44): sweep's corrupt /
undated / fresh branches, consume_judged's skip / vanished / execute-raised
branches, the detached judge CLI's early exits, request() when the job write
or the spawn fails, and the crash-retry event probe's failure posture.

Characterization net for the P3a CRAP refactor: recorded against the
pre-refactor module. Zero registry writes leak: every card lives in the
sandboxed REGISTRY_DIR; no subprocess is spawned (Popen is stubbed).
"""
import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import analytics, config, registry, silent_merge
from act.lib.registry import Requirement


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class _JobDirMixin:
    def _isolate_jobs(self):
        d = Path(tempfile.mkdtemp(prefix="sm-jobs-"))
        p = mock.patch.object(silent_merge, "SILENT_DIR", d)
        p.start()
        self.addCleanup(p.stop)
        logs = Path(tempfile.mkdtemp(prefix="sm-logs-"))
        p2 = mock.patch.object(config, "LOG_DIR", logs)
        p2.start()
        self.addCleanup(p2.stop)
        return d

    def _isolate_analytics(self):
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        d = Path(tempfile.mkdtemp(dir=str(config.STATE_DIR)))
        for attr, val in (("ANALYTICS_DIR", d), ("EVENTS_PATH", d / "events.jsonl")):
            p = mock.patch.object(analytics, attr, val)
            p.start()
            self.addCleanup(p.stop)


class SweepEdgeTestCase(_JobDirMixin, unittest.TestCase):
    def setUp(self):
        self.dir = self._isolate_jobs()
        self.now = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.timezone.utc)

    def _job(self, name, **fields):
        (self.dir / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")

    def test_unreadable_dir_returns_zero(self):
        with mock.patch.object(silent_merge.Path, "glob", side_effect=OSError("io")):
            self.assertEqual(silent_merge.sweep(self.now), 0)

    def test_corrupt_file_is_removed(self):
        (self.dir / "SM-bad.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(silent_merge.sweep(self.now), 1)
        self.assertFalse((self.dir / "SM-bad.json").exists())

    def test_corrupt_file_unlink_failure_is_swallowed(self):
        (self.dir / "SM-bad.json").write_text("{nope", encoding="utf-8")
        with mock.patch.object(silent_merge.Path, "unlink", side_effect=OSError("ro")):
            self.assertEqual(silent_merge.sweep(self.now), 0)

    def test_undated_and_fresh_jobs_are_kept(self):
        self._job("SM-nodate", id="SM-nodate", status="pending")
        self._job("SM-fresh", id="SM-fresh", status="pending",
                  requested_at=_iso(self.now - _dt.timedelta(minutes=1)))
        self._job("SM-done", id="SM-done", status="done",
                  finished_at=_iso(self.now - _dt.timedelta(hours=1)))
        self.assertEqual(silent_merge.sweep(self.now), 0)
        for name in ("SM-nodate", "SM-fresh", "SM-done"):
            self.assertTrue((self.dir / f"{name}.json").exists())
        self.assertEqual(json.loads((self.dir / "SM-fresh.json").read_text())["status"],
                         "pending")

    def test_expired_purge_also_drops_twin_log_and_tolerates_unlink_errors(self):
        self._job("SM-old", id="SM-old", status="failed",
                  finished_at=_iso(self.now - _dt.timedelta(hours=30)))
        (config.LOG_DIR / "SM-old.log").write_text("log", encoding="utf-8")
        self.assertEqual(silent_merge.sweep(self.now), 1)
        self.assertFalse((self.dir / "SM-old.json").exists())
        self.assertFalse((config.LOG_DIR / "SM-old.log").exists())
        # second pass: unlink raising on the job file is swallowed, count 0
        self._job("SM-old2", id="SM-old2", status="done",
                  finished_at=_iso(self.now - _dt.timedelta(hours=30)))
        with mock.patch.object(silent_merge.Path, "unlink", side_effect=OSError("ro")):
            self.assertEqual(silent_merge.sweep(self.now), 0)

    def test_default_now_is_utc_now(self):
        self._job("SM-stuck", id="SM-stuck", status="pending",
                  requested_at="2020-01-01T00:00:00Z")
        silent_merge.sweep()
        job = json.loads((self.dir / "SM-stuck.json").read_text())
        self.assertEqual((job["status"], job["error"]), ("failed", "judge timed out"))

    def test_parse_iso_shapes(self):
        self.assertIsNone(silent_merge._parse_iso(None))
        self.assertIsNone(silent_merge._parse_iso("garbage"))
        naive = silent_merge._parse_iso("2026-09-02T00:00:00")
        self.assertEqual(naive.tzinfo, _dt.timezone.utc)


class ConsumeJudgedEdgeTestCase(_JobDirMixin, unittest.TestCase):
    def setUp(self):
        self.dir = self._isolate_jobs()
        self._isolate_analytics()

    def _job(self, name, **fields):
        (self.dir / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")

    def _card(self, id_, status="card_sent"):
        req = Requirement.from_dict({"id": id_, "title": id_, "status": status})
        registry.save(req)
        return req

    def test_unreadable_dir_returns_zero(self):
        with mock.patch.object(silent_merge.Path, "glob", side_effect=OSError("io")):
            self.assertEqual(silent_merge.consume_judged(), 0)

    def test_corrupt_non_dict_and_non_judged_are_skipped(self):
        (self.dir / "SM-bad.json").write_text("{", encoding="utf-8")
        (self.dir / "SM-list.json").write_text("[]", encoding="utf-8")
        self._job("SM-pending", id="SM-pending", status="pending")
        self.assertEqual(silent_merge.consume_judged(), 0)
        self.assertEqual(json.loads((self.dir / "SM-pending.json").read_text())["status"],
                         "pending")

    def test_vanished_card_fails_job(self):
        self._job("SM-gone", id="SM-gone", status="judged", primary="P-nope",
                  secondary="P-nada")
        self.assertEqual(silent_merge.consume_judged(), 0)
        job = json.loads((self.dir / "SM-gone.json").read_text())
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "card vanished before execute")

    def test_execute_raising_marks_failed_and_logs(self):
        self._card("P-901")
        self._card("P-902")
        self._job("SM-x", id="SM-x", status="judged", primary="P-901",
                  secondary="P-902", brief="b")
        with mock.patch.object(silent_merge, "execute", side_effect=RuntimeError("half")):
            self.assertEqual(silent_merge.consume_judged(), 0)
        job = json.loads((self.dir / "SM-x.json").read_text())
        self.assertEqual((job["status"], job["error"]), ("failed", "execute failed: half"))
        events = [json.loads(ln) for ln in analytics.EVENTS_PATH.read_text().splitlines()]
        self.assertEqual([e["outcome"] for e in events if e["event"] == "silent_merge"],
                         ["execute_failed"])

    def test_execute_false_is_skipped_not_merged(self):
        self._card("P-903")
        self._card("P-904")
        self._job("SM-y", id="SM-y", status="judged", primary="P-903", secondary="P-904")
        with mock.patch.object(silent_merge, "execute", return_value=False):
            self.assertEqual(silent_merge.consume_judged(), 0)
        self.assertEqual(json.loads((self.dir / "SM-y.json").read_text())["verdict"], "skipped")


class RequestAndFinishTestCase(_JobDirMixin, unittest.TestCase):
    def setUp(self):
        self.dir = self._isolate_jobs()

    def test_write_failure_returns_none(self):
        with mock.patch.object(silent_merge, "_write_job", side_effect=OSError("ro")):
            self.assertIsNone(silent_merge.request("P-1", "P-2"))

    def test_spawn_failure_fails_the_job(self):
        with mock.patch.object(silent_merge.subprocess, "Popen",
                               side_effect=OSError("no python")):
            sid = silent_merge.request("P-1", "P-2")
        job = json.loads((self.dir / f"{sid}.json").read_text())
        self.assertEqual(job["status"], "failed")
        self.assertIn("judge launch failed: no python", job["error"])

    def test_finish_tolerates_missing_job_and_write_errors(self):
        silent_merge._finish("SM-none", "failed", error="e", verdict=None)
        job = json.loads((self.dir / "SM-none.json").read_text())
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("verdict", job)
        with mock.patch.object(silent_merge, "_write_job", side_effect=OSError("ro")):
            silent_merge._finish("SM-none", "done")   # swallowed

    def test_load_job_shapes(self):
        (self.dir / "SM-l.json").write_text("[1]", encoding="utf-8")
        self.assertIsNone(silent_merge._load_job("SM-l"))
        self.assertIsNone(silent_merge._load_job("SM-missing"))

    def test_pending_count_tolerates_bad_files(self):
        (self.dir / "SM-a.json").write_text('{"status": "pending"}', encoding="utf-8")
        (self.dir / "SM-b.json").write_text("{", encoding="utf-8")
        self.assertEqual(silent_merge.pending_count(), 1)
        with mock.patch.object(silent_merge.Path, "glob", side_effect=OSError("io")):
            self.assertEqual(silent_merge.pending_count(), 0)


class CliEarlyExitsTestCase(_JobDirMixin, unittest.TestCase):
    def setUp(self):
        self.dir = self._isolate_jobs()

    def test_missing_or_non_pending_job_is_noop(self):
        self.assertEqual(silent_merge._main("SM-none"), 0)
        (self.dir / "SM-d.json").write_text('{"id": "SM-d", "status": "done"}', encoding="utf-8")
        self.assertEqual(silent_merge._main("SM-d"), 0)

    def test_vanished_card_fails(self):
        (self.dir / "SM-v.json").write_text(
            '{"id": "SM-v", "status": "pending", "primary": "P-x", "secondary": "P-y"}',
            encoding="utf-8")
        self.assertEqual(silent_merge._main("SM-v"), 0)
        job = json.loads((self.dir / "SM-v.json").read_text())
        self.assertEqual((job["status"], job["error"]), ("failed", "card vanished"))

    def test_judge_without_any_runner_is_none(self):
        a = Requirement.from_dict({"id": "P-a", "title": "a", "status": "detected"})
        b = Requirement.from_dict({"id": "P-b", "title": "b", "status": "detected"})
        with mock.patch.object(silent_merge, "JUDGE_RUNNER", None), \
                mock.patch.object(silent_merge, "_mr", None):
            self.assertIsNone(silent_merge.judge(a, b))

    def test_judge_string_output_and_nonzero_rc(self):
        a = Requirement.from_dict({"id": "P-a", "title": "a", "status": "detected"})
        b = Requirement.from_dict({"id": "P-b", "title": "b", "status": "detected"})
        out = silent_merge.judge(a, b, runner=lambda p: '{"same_thing": true, "brief": " x "}')
        self.assertEqual(out, {"same_thing": True, "brief": "x"})

        class Proc:
            returncode = 1
            stdout = '{"same_thing": true, "brief": "x"}'
        self.assertIsNone(silent_merge.judge(a, b, runner=lambda p: Proc()))

    def test_parse_verdict_partial_shapes(self):
        self.assertIsNone(silent_merge._parse_verdict('{"same_thing": true} {bad'))
        self.assertIsNone(silent_merge._parse_verdict('{not json} {"brief": "x"}'))
        self.assertEqual(silent_merge._parse_verdict(
            '{oops} {"same_thing": true, "brief": "k"}')["brief"], "k")
        self.assertIsNone(silent_merge._parse_verdict('{"brief": "only"}'))
        self.assertEqual(silent_merge._parse_verdict(
            'pre {"a": {"b": 1}} {"same_thing": false, "brief": "k"} }')["brief"], "k")


class MergeEventLoggedTestCase(_JobDirMixin, unittest.TestCase):
    def setUp(self):
        self._isolate_analytics()

    def test_read_failure_counts_as_not_logged(self):
        with mock.patch.object(analytics, "read_events", side_effect=OSError("io")):
            self.assertFalse(silent_merge._merge_event_logged("P-1", "P-2"))

    def test_only_matching_ok_events_count(self):
        analytics.EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = _iso(_dt.datetime.now(_dt.timezone.utc))
        rows = [
            {"ts": now, "event": "silent_merge", "primary": "P-1", "secondary": "P-2",
             "outcome": "separate"},
            {"ts": now, "event": "silent_merge", "primary": "P-1", "secondary": "P-9",
             "outcome": "ok"},
            {"ts": now, "event": "other", "primary": "P-1", "secondary": "P-2", "outcome": "ok"},
        ]
        analytics.EVENTS_PATH.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertFalse(silent_merge._merge_event_logged("P-1", "P-2"))
        rows.append({"ts": now, "event": "silent_merge", "primary": "P-1",
                     "secondary": "P-2", "outcome": "ok_retry"})
        analytics.EVENTS_PATH.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertTrue(silent_merge._merge_event_logged("P-1", "P-2"))


class CardMaterialTestCase(unittest.TestCase):
    def test_material_lines(self):
        req = Requirement.from_dict({
            "id": "P-7", "title": "标题", "status": "detected", "display_title": "显示",
            "summary": "摘要", "notes": "n" * 1300,
            "sources": [{"who": "a", "channel": "slack", "date": "d", "quote": "q" * 400},
                        {"who": "", "quote": ""}, "str", {"who": "b"}],
        })
        text = silent_merge._card_material(req)
        lines = text.split("\n")
        self.assertEqual(lines[:5], ["id: P-7", "status: detected", "title: 标题",
                                     "display_title: 显示", "summary: 摘要"])
        self.assertEqual(lines[5], "notes: " + "n" * 1200)
        self.assertEqual(lines[6], "source: a · slack · d — " + "q" * 300)
        self.assertEqual(lines[7], "source: ")
        self.assertEqual(lines[8], "source: b")
        self.assertEqual(len(lines), 9)

    def test_find_fold_target_swallows_errors(self):
        req = Requirement.from_dict({"id": "P-8", "title": "x", "status": "detected"})
        with mock.patch.object(registry, "load_all", side_effect=RuntimeError("db")):
            self.assertIsNone(silent_merge.find_fold_target(req))


if __name__ == "__main__":
    unittest.main()
