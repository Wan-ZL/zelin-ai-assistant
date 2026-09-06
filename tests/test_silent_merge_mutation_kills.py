"""silent_merge — judgments that pin the survivors of the 2026-09 mutation round (CONTRACT §44).

``scripts/qa/mutate.py`` on act/lib/silent_merge.py (258 sites, mapped subset =
test_silent_merge.py + test_silent_merge_jobs_edge.py) left 53 survivors. Two are
equivalent: ``_converge_abort``'s ``ts and not split_into`` guard — with ``or``
the extra ``mark_note_split`` call lands on an already-split (or ts-less) line,
which ``mark_note_split`` refuses anyway, so ``changed`` is untouched; and the
``getattr(primary, "silent_merge_count", 0)`` default in ``_bump_counters`` —
the field always exists on a Requirement, so the default is never read. The
rest were real holes (one a real crash: ``_finish`` on a job record without
``id`` KeyError'd out of the sweep); each is a behavior the module promises,
pinned here:

  * sweep boundaries are STRICT (``>``): a check pending exactly 20 min is not
    stuck, a job done exactly 24 h ago is not expired — and the two constants
    are the numbers §44 states (20 min / 24 h), not their neighbours;
  * job files are human-readable JSON (UTF-8 verbatim, 2-space indent), the
    job dir may pre-exist, ids are ``SM-`` + 8 hex, the detached judge is
    spawned as its own session (never waited on) with stdin closed;
  * a job record without ``id`` is addressed by its file stem;
  * judge material: ``display_title`` only when it differs from ``title``,
    at most 6 sources; the verdict object may carry ``same_thing`` alone when
    it IS the whole output; a balanced object followed by prose (``…}.``) still
    parses; the merge_review default runner is used when no seam is set;
  * fold-note text carries the brief only when there is one; counters treat a
    missing ``repeated_mentions`` as 1 and a missing ``silent_merge_count`` as 0;
  * crash-retry: only a fold note about THIS secondary means "already applied";
    a secondary the OWNER trashed is an abort, not a converge; a replayed abort
    does not rewrite the primary; the 48 h event window is exactly 48 h;
  * fold-target search skips closed cards and keeps scanning past a linked hit;
  * the CLI / consume paths return the literal ``0`` / ``False`` and fail a
    half-vanished pair as "card vanished" instead of crashing into the judge;
    a judged job's brief reaches the fold note.
"""
import datetime as _dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import analytics, config, registry, silent_merge
from act.lib.registry import Requirement, State

DUP_A = "整理 EB-1A 推荐信 recommendation letters 清单 wegreened"
DUP_B = "EB-1A 推荐信 recommendation letters wegreened 跟进"


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _runner(payload: str):
    def run(prompt):
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")
    return run


def _never_called(prompt):
    raise AssertionError("judge must not run here")


class _Sandbox(unittest.TestCase):
    """Fresh registry + job dir + events file per test."""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.jobs = Path(tempfile.mkdtemp(prefix="sm-kill-jobs-"))
        logs = Path(tempfile.mkdtemp(prefix="sm-kill-logs-"))
        events = Path(tempfile.mkdtemp(dir=str(config.STATE_DIR)))
        for target, attr, val in ((silent_merge, "SILENT_DIR", self.jobs),
                                  (config, "LOG_DIR", logs),
                                  (analytics, "ANALYTICS_DIR", events),
                                  (analytics, "EVENTS_PATH", events / "events.jsonl")):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def _job(self, name, **fields):
        (self.jobs / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")

    def _read_job(self, name):
        return json.loads((self.jobs / f"{name}.json").read_text(encoding="utf-8"))

    def _seed(self, rid, summary, status=State.CARD_SENT.value, **kw):
        r = Requirement(id=rid, title=rid, status=status, summary=summary, **kw)
        registry.save(r)
        return r

    def _outcomes(self):
        if not analytics.EVENTS_PATH.exists():
            return []
        return [json.loads(ln)["outcome"]
                for ln in analytics.EVENTS_PATH.read_text().splitlines()
                if json.loads(ln).get("event") == "silent_merge"]

    def _crash_before_trash(self, primary, secondary, brief="补充了预算数字"):
        """First run dies after save(primary), before trash(secondary)."""
        with mock.patch.object(registry, "trash",
                               side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                silent_merge.execute(primary, secondary, brief)


class SweepBoundaryTest(_Sandbox):
    NOW = _dt.datetime(2026, 9, 6, 12, 0, tzinfo=_dt.timezone.utc)

    def _pending_aged(self, name, minutes):
        self._job(name, id=name, status="pending",
                  requested_at=_iso(self.NOW - _dt.timedelta(minutes=minutes)))

    def _done_aged(self, name, minutes):
        self._job(name, id=name, status="done",
                  finished_at=_iso(self.NOW - _dt.timedelta(minutes=minutes)))

    def test_pending_timeout_is_strictly_twenty_minutes(self):
        self.assertEqual(silent_merge.PENDING_TIMEOUT_MIN, 20)
        self._pending_aged("SM-at", 20)        # exactly on the line: kept
        self._pending_aged("SM-under", 19.5)   # kept
        self._pending_aged("SM-over", 20.5)    # failed
        self.assertEqual(silent_merge.sweep(self.NOW), 0)
        self.assertEqual(self._read_job("SM-at")["status"], "pending")
        self.assertEqual(self._read_job("SM-under")["status"], "pending")
        self.assertEqual(self._read_job("SM-over")["status"], "failed")

    def test_ttl_is_strictly_twenty_four_hours(self):
        self.assertEqual(silent_merge.TTL_HOURS, 24)
        self._done_aged("SM-at", 24 * 60)          # exactly on the line: kept
        self._done_aged("SM-under", 24 * 60 - 10)  # kept
        self._done_aged("SM-over", 24 * 60 + 10)   # purged
        self.assertEqual(silent_merge.sweep(self.NOW), 1)
        self.assertTrue((self.jobs / "SM-at.json").exists())
        self.assertTrue((self.jobs / "SM-under.json").exists())
        self.assertFalse((self.jobs / "SM-over.json").exists())

    def test_job_without_id_is_addressed_by_file_stem(self):
        # a record missing "id" must still be failed IN PLACE (not under a
        # bogus "None" id) — the file stem is the fallback identity
        (self.jobs / "SM-noid.json").write_text(json.dumps({
            "status": "pending",
            "requested_at": _iso(self.NOW - _dt.timedelta(minutes=30))}),
            encoding="utf-8")
        silent_merge.sweep(self.NOW)
        self.assertEqual(self._read_job("SM-noid")["status"], "failed")
        self.assertEqual(sorted(p.name for p in self.jobs.glob("*.json")),
                         ["SM-noid.json"])


class JobFilePlumbingTest(_Sandbox):
    def test_job_dir_may_already_exist(self):
        with mock.patch.object(silent_merge.subprocess, "Popen",
                               lambda *a, **k: None):
            first = silent_merge.request("R-001", "R-002")
            second = silent_merge.request("R-001", "R-003")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)

    def test_job_dir_is_created_with_missing_parents(self):
        # fresh install: state/ may not exist yet when the first check is filed
        deep = Path(tempfile.mkdtemp(prefix="sm-kill-deep-")) / "state" / "silent_merge"
        with mock.patch.object(silent_merge, "SILENT_DIR", deep), \
                mock.patch.object(silent_merge.subprocess, "Popen", lambda *a, **k: None):
            sid = silent_merge.request("R-001", "R-002")
        self.assertIsNotNone(sid)
        self.assertTrue((deep / f"{sid}.json").exists())

    def test_job_file_is_readable_utf8_two_space_json(self):
        silent_merge._finish("SM-utf8", "judged", brief="副卡补充了 deadline")
        text = (self.jobs / "SM-utf8.json").read_text(encoding="utf-8")
        self.assertIn("副卡补充了 deadline", text)      # not \\uXXXX-escaped
        self.assertEqual(text, json.dumps(json.loads(text), ensure_ascii=False,
                                          indent=2))

    def test_job_id_shape(self):
        for _ in range(5):
            sid = silent_merge.new_job_id()
            self.assertRegex(sid, r"^SM-[0-9a-f]{8}$")
            self.assertEqual(len(sid), 11)

    def test_judge_is_spawned_detached_with_stdin_closed(self):
        calls = []

        def fake_popen(argv, **kw):
            calls.append((argv, kw))

        with mock.patch.object(silent_merge.subprocess, "Popen", fake_popen):
            sid = silent_merge.request("R-001", "R-002")
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertEqual(argv, [sys.executable, "-m", "act.lib.silent_merge", sid])
        self.assertIs(kw["start_new_session"], True)
        self.assertIs(kw["stdin"], silent_merge.subprocess.DEVNULL)


class JudgeMaterialTest(_Sandbox):
    def test_display_title_line_only_when_it_differs(self):
        same = Requirement.from_dict({"id": "P-1", "title": "标题", "status": "detected",
                                      "display_title": "标题"})
        self.assertNotIn("display_title:", silent_merge._card_material(same))
        diff = Requirement.from_dict({"id": "P-1", "title": "标题", "status": "detected",
                                      "display_title": "显示"})
        self.assertIn("display_title: 显示", silent_merge._card_material(diff))

    def test_at_most_six_sources(self):
        srcs = [{"who": f"p{i}", "channel": "slack", "date": "2026-09-01",
                 "quote": f"q{i}"} for i in range(7)]
        req = Requirement.from_dict({"id": "P-1", "title": "t", "status": "detected",
                                     "sources": srcs})
        lines = [ln for ln in silent_merge._card_material(req).split("\n")
                 if ln.startswith("source:")]
        self.assertEqual(len(lines), 6)
        self.assertIn("q5", lines[-1])
        self.assertNotIn("q6", "\n".join(lines))

    def test_whole_output_needs_only_same_thing(self):
        # the stated contract: the whole output IS the verdict object — a
        # brief-less answer still counts (the fallback scan would demand both)
        self.assertEqual(silent_merge._parse_verdict('{"same_thing": true}'),
                         {"same_thing": True})

    def test_balanced_object_followed_by_prose_parses(self):
        out = 'Verdict: {"same_thing": false, "brief": "两件事"}.'
        self.assertEqual(silent_merge._parse_verdict(out)["brief"], "两件事")

    def test_default_runner_is_merge_review_pipeline(self):
        sentinel = object()
        fake_mr = SimpleNamespace(_default_runner=sentinel)
        with mock.patch.object(silent_merge, "JUDGE_RUNNER", None), \
                mock.patch.object(silent_merge, "_mr", fake_mr):
            self.assertIs(silent_merge._resolve_runner(None), sentinel)


class FoldBookkeepingTest(_Sandbox):
    def test_merge_note_carries_brief_only_when_present(self):
        sec = Requirement.from_dict({"id": "R-002", "title": "R-002", "status": "card_sent"})
        self.assertEqual(silent_merge._merge_note(sec, ""), "静默并入 R-002「R-002」")
        self.assertEqual(silent_merge._merge_note(sec, "无新增信息"),
                         "静默并入 R-002「R-002」")
        self.assertEqual(silent_merge._merge_note(sec, "补充链接"),
                         "静默并入 R-002「R-002」：补充链接")

    def test_counters_default_missing_values(self):
        p = Requirement.from_dict({"id": "R-001", "title": "a", "status": "card_sent"})
        s = Requirement.from_dict({"id": "R-002", "title": "b", "status": "card_sent"})
        p.repeated_mentions = None
        p.silent_merge_count = None
        s.repeated_mentions = None
        silent_merge._bump_counters(p, s)
        self.assertEqual(p.repeated_mentions, 2)   # 1 (self) + 1 (secondary)
        self.assertEqual(p.silent_merge_count, 1)

    def test_state_moved_returns_literal_false(self):
        primary = self._seed("R-001", DUP_A)
        secondary = self._seed("R-002", DUP_B, status=State.APPROVED.value)
        self.assertIs(silent_merge.execute(primary, secondary, ""), False)
        self.assertEqual(self._outcomes(), ["state_moved"])


class CrashRetryTest(_Sandbox):
    def test_unrelated_radar_note_is_not_an_applied_marker(self):
        primary = self._seed("R-001", DUP_A)
        registry.append_fold_note(primary, "别的雷达命中", "radar")
        registry.save(primary)
        secondary = self._seed("R-002", DUP_B)
        self.assertIs(silent_merge.execute(primary, secondary, ""), True)
        p = registry.load("R-001")
        self.assertEqual(p.silent_merge_count, 1)   # fresh path, not converge
        self.assertEqual(self._outcomes(), ["ok"])

    def test_owner_trashed_secondary_aborts_instead_of_converging(self):
        primary = self._seed("R-001", DUP_A)
        secondary = self._seed("R-002", DUP_B)
        self._crash_before_trash(primary, secondary)
        # in the crash window the OWNER trashed the secondary (reason != ours)
        registry.trash(registry.load("R-002"), "deleted")
        p2, s2 = registry.load("R-001"), registry.load("R-002")
        self.assertIs(silent_merge.execute(p2, s2, "补充了预算数字"), False)
        self.assertEqual(self._outcomes(), ["retry_aborted"])
        self.assertEqual(registry.load("R-002").trash_reason, "deleted")
        self.assertIn("并入中止", registry.load("R-001").notes)

    def test_replayed_abort_does_not_rewrite_primary(self):
        primary = self._seed("R-001", DUP_A)
        secondary = self._seed("R-002", DUP_B)
        self._crash_before_trash(primary, secondary)
        registry.trash(registry.load("R-002"), "deleted")
        self.assertIs(silent_merge.execute(registry.load("R-001"),
                                           registry.load("R-002"), ""), False)
        # second replay: note already split, audit note already present
        with mock.patch.object(registry, "save", wraps=registry.save) as spy:
            self.assertIs(silent_merge.execute(registry.load("R-001"),
                                               registry.load("R-002"), ""), False)
            self.assertEqual(spy.call_count, 0)

    def test_retry_completion_defaults_missing_mentions_to_one(self):
        primary = self._seed("R-001", DUP_A)
        secondary = self._seed("R-002", DUP_B)
        self._crash_before_trash(primary, secondary)
        p2, s2 = registry.load("R-001"), registry.load("R-002")
        p2.repeated_mentions = None    # a legacy card without the counter
        s2.sources = [{"who": "c", "channel": "gmail", "date": "2026-09-05",
                       "quote": "window gain"}]
        self.assertIs(silent_merge.execute(p2, s2, "补充了预算数字"), True)
        self.assertEqual(registry.load("R-001").repeated_mentions, 2)  # 1 + 1 added

    def test_event_window_is_exactly_48_hours(self):
        seen = {}

        def fake_read(since=None):
            seen["since"] = since
            return iter(())

        before = _dt.datetime.now(_dt.timezone.utc)
        with mock.patch.object(analytics, "read_events", fake_read):
            self.assertIs(silent_merge._merge_event_logged("R-001", "R-002"), False)
        after = _dt.datetime.now(_dt.timezone.utc)
        window_lo = before - seen["since"]
        window_hi = after - seen["since"]
        self.assertLessEqual(window_lo, _dt.timedelta(hours=48))
        self.assertGreaterEqual(window_hi, _dt.timedelta(hours=48))
        self.assertLess(window_hi - window_lo, _dt.timedelta(minutes=1))

    def test_event_probe_failure_is_literal_false(self):
        with mock.patch.object(analytics, "read_events", side_effect=OSError("io")):
            self.assertIs(silent_merge._merge_event_logged("R-001", "R-002"), False)


class FoldTargetSearchTest(_Sandbox):
    def test_closed_cards_are_never_fold_targets(self):
        # a trashed near-dupe is not a candidate — even a judge that would say
        # "same" never gets to fold into it (the runner is real here so the
        # never-raise belt cannot mask a wrong candidate pool)
        self._seed("R-001", DUP_A, status=State.TRASHED.value)
        req = Requirement(id="R-999", title=DUP_B, status="card_sent", summary=DUP_B)
        self.assertIsNone(silent_merge.find_fold_target(
            req, runner=_runner('{"same_thing": true, "brief": "x"}')))
        self.assertFalse(hasattr(req, "_silent_separate_from"))

    def test_scan_continues_past_a_linked_hit(self):
        self._seed("R-001", DUP_A)                 # linked (split lineage)
        self._seed("R-002", DUP_A)                 # the real target
        req = Requirement(id="R-999", title=DUP_B, status="card_sent",
                          summary=DUP_B, split_from="R-001")
        target = silent_merge.find_fold_target(
            req, runner=_runner('{"same_thing": true, "brief": "x"}'))
        self.assertIsNotNone(target)
        self.assertEqual(target.id, "R-002")


class CliAndConsumeTest(_Sandbox):
    def test_main_returns_zero_after_judging(self):
        self._seed("R-001", DUP_A)
        self._seed("R-002", DUP_B)
        self._job("SM-j", id="SM-j", status="pending", primary="R-001",
                  secondary="R-002")
        with mock.patch.object(silent_merge, "JUDGE_RUNNER",
                               _runner('{"same_thing": true, "brief": "x"}')):
            self.assertEqual(silent_merge._main("SM-j"), 0)
        self.assertEqual(self._read_job("SM-j")["status"], "judged")

    def test_main_half_vanished_pair_fails_without_judging(self):
        self._seed("R-001", DUP_A)
        self._job("SM-h", id="SM-h", status="pending", primary="R-001",
                  secondary="R-gone")
        with mock.patch.object(silent_merge, "JUDGE_RUNNER", _never_called):
            self.assertEqual(silent_merge._main("SM-h"), 0)
        job = self._read_job("SM-h")
        self.assertEqual((job["status"], job["error"]), ("failed", "card vanished"))

    def test_consume_half_vanished_pair_fails_without_executing(self):
        self._seed("R-001", DUP_A)
        job = {"id": "SM-c", "status": "judged", "primary": "R-001",
               "secondary": "R-gone"}
        self._job("SM-c", **job)
        with mock.patch.object(silent_merge, "execute", side_effect=AssertionError):
            self.assertIs(silent_merge._consume_one("SM-c", job), False)
        got = self._read_job("SM-c")
        self.assertEqual((got["status"], got["error"]),
                         ("failed", "card vanished before execute"))

    def test_consume_execute_raising_returns_literal_false(self):
        self._seed("R-001", DUP_A)
        self._seed("R-002", DUP_B)
        job = {"id": "SM-e", "status": "judged", "primary": "R-001",
               "secondary": "R-002"}
        self._job("SM-e", **job)
        with mock.patch.object(silent_merge, "execute", side_effect=RuntimeError("x")):
            self.assertIs(silent_merge._consume_one("SM-e", job), False)

    def test_judged_brief_reaches_the_fold_note(self):
        self._seed("R-001", DUP_A)
        self._seed("R-002", DUP_B)
        self._job("SM-b", id="SM-b", status="judged", primary="R-001",
                  secondary="R-002", brief="补充链接")
        self.assertEqual(silent_merge.consume_judged(), 1)
        self.assertIn("静默并入 R-002「R-002」：补充链接", registry.load("R-001").notes)


if __name__ == "__main__":
    unittest.main()
