"""process_inbox accounting — every file is a terminal disposition, every applied
file counts once, one file never stops the drain (CONTRACT §5.4 / §10 / §33).

Pins the survivors of the P3b mutation round on the drain loop: the returned
count (special forms +1 each, unknown card +0, bad files +0), ``continue`` vs
``break`` after every special form (a second file in the same pass must still
be applied), the missing-inbox short-circuit, the ``images`` boundary clean
(dedupe + cap 4), the capture title cap, ingress → source ``who``, the §5.4
ack ledger's stat-key cache and raw-UTF-8 lines, and the weekly-digest event
gate.
"""
import json
import os
import time
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import analytics, config, detached, registry
from act.lib.registry import Requirement, State

_APPLIED = config.STATE_DIR / "sync" / "applied.jsonl"


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()
    if _APPLIED.exists():
        _APPLIED.unlink()
    (config.STATE_DIR / "sync.json").write_text(
        json.dumps({"mode": "cloud", "device_id": "dev-test"}), encoding="utf-8")
    actd._SYNC_ACTIVE_CACHE = None


def _drop(body: dict, stem=None, mtime=None) -> str:
    aid = stem or str(uuid.uuid4())
    path = config.INBOX_DIR / f"{aid}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return aid


def _acks() -> dict:
    out = {}
    if _APPLIED.exists():
        for ln in _APPLIED.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rec = json.loads(ln)
                out[rec["action_id"]] = rec["result_status"]
    return out


class DrainCountTest(unittest.TestCase):
    def setUp(self):
        _clean()
        mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def test_missing_inbox_dir_returns_zero(self):
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        config.INBOX_DIR.rmdir()
        try:
            self.assertEqual(actd.process_inbox(), 0)
        finally:
            config.INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def test_each_special_form_counts_once_and_the_drain_continues(self):
        # oldest first: capture, feedback (dropped: empty), split_note (noop),
        # merge_review (noop: unknown ids), merge_force (noop), merge_dismiss
        # (unknown suggestion), import (module patched out → noop), detached
        # digest (patched) — then an unknown card (acked unknown, NOT counted)
        # and finally a real card verb. Every one of them must be applied in a
        # single pass: a `break` after any special form would leave the rest
        # on disk for the next pass.
        base = time.time() - 100
        registry.save(Requirement(id="R-1", title="t", status=State.CARD_SENT.value))
        stems = [
            _drop({"action": "capture", "text": "计数一"}, stem="a-capture", mtime=base + 1),
            _drop({"action": "feedback", "text": "", "ids": []}, stem="b-feedback", mtime=base + 2),
            _drop({"action": "split_note", "id": "R-1", "note_ts": "nope"}, stem="c-split", mtime=base + 3),
            _drop({"action": "merge_review", "ids": ["R-9", "R-8"]}, stem="d-review", mtime=base + 4),
            _drop({"action": "merge_force", "ids": ["R-9"], "primary": "R-9"}, stem="e-force", mtime=base + 5),
            _drop({"action": "merge_dismiss", "id": "MS-none"}, stem="f-dismiss", mtime=base + 6),
            _drop({"action": "import_claude_sessions"}, stem="g-import", mtime=base + 7),
            _drop({"action": "weekly_digest_now"}, stem="h-digest", mtime=base + 8),
            _drop({"action": "approve", "id": "R-404"}, stem="i-unknown", mtime=base + 9),
            _drop({"action": "defer", "id": "R-1"}, stem="j-defer", mtime=base + 10),
        ]
        with mock.patch.object(actd, "radar_claude_sessions", None), \
                mock.patch.object(actd, "_spawn_weekly_digest", return_value="running"):
            n = actd.process_inbox()
        self.assertEqual(n, 9)   # 10 files, the unknown-card one is not counted
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        acks = _acks()
        self.assertEqual(set(acks), set(stems))
        self.assertEqual(acks["a-capture"], "running")
        self.assertEqual(acks["b-feedback"], "noop")
        self.assertEqual(acks["c-split"], "noop")
        self.assertEqual(acks["d-review"], "noop")
        self.assertEqual(acks["e-force"], "noop")
        self.assertEqual(acks["f-dismiss"], "unknown")
        self.assertEqual(acks["g-import"], "noop")
        self.assertEqual(acks["h-digest"], "running")
        self.assertEqual(acks["i-unknown"], "unknown")
        self.assertEqual(acks["j-defer"], "running")
        self.assertEqual(registry.load("R-1").status, State.DETECTED.value)

    def test_bad_files_are_disposed_of_without_counting_and_the_rest_apply(self):
        base = time.time() - 100
        (config.INBOX_DIR / "x-garbage.json").write_text("{not json", encoding="utf-8")
        os.utime(config.INBOX_DIR / "x-garbage.json", (base + 1, base + 1))
        _drop([1, 2, 3], stem="y-list", mtime=base + 2)
        _drop({"action": "capture", "text": "还在"}, stem="z-capture", mtime=base + 3)
        n = actd.process_inbox()
        self.assertEqual(n, 1)
        acks = _acks()
        self.assertEqual(acks["x-garbage"], "bad_json")
        self.assertEqual(acks["y-list"], "bad_json")
        self.assertEqual(acks["z-capture"], "running")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_a_crashing_apply_is_acked_bad_json_and_the_next_file_still_applies(self):
        base = time.time() - 100
        _drop({"action": "capture", "text": "boom"}, stem="a-boom", mtime=base + 1)
        _drop({"action": "capture", "text": "after"}, stem="b-after", mtime=base + 2)
        real = actd._apply_capture

        def flaky(text, *a, **kw):
            if text == "boom":
                raise RuntimeError("simulated apply crash")
            return real(text, *a, **kw)

        with mock.patch.object(actd, "_apply_capture", side_effect=flaky):
            n = actd.process_inbox()
        self.assertEqual(n, 1)
        self.assertEqual(_acks(), {"a-boom": "bad_json", "b-after": "running"})


class CaptureBoundaryTest(unittest.TestCase):
    def setUp(self):
        _clean()
        mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def test_clean_image_paths_dedupes_and_caps_at_four(self):
        raw = [" a.png", "a.png", "", 3, None, "b.png", "c.png", "d.png", "e.png"]
        self.assertEqual(actd._clean_image_paths(raw), ["a.png", "b.png", "c.png", "d.png"])
        self.assertEqual(actd._clean_image_paths("a.png"), [])
        self.assertEqual(actd._clean_image_paths(None), [])

    def test_capture_title_is_capped_at_eighty_chars(self):
        text = "字" * 200
        self.assertEqual(actd._apply_capture(text), "running")
        req = next(r for r in registry.load_all() if r.status == State.RAISING.value)
        self.assertEqual(len(req.title), 80)
        self.assertEqual(req.sources[0]["quote"], text)   # 原话不截

    def test_ingress_decides_the_source_who(self):
        actd._apply_capture("owner 打的", via=None)
        actd._apply_capture("agent 投的", via="agent")
        actd._apply_capture("remote 投的", via="remote")
        actd._apply_capture("web 打的", via="web")
        who = {r.title: r.sources[0]["who"] for r in registry.load_all()}
        self.assertEqual(who["owner 打的"], "zelin")
        self.assertEqual(who["web 打的"], "zelin")
        self.assertEqual(who["agent 投的"], "agent")
        self.assertEqual(who["remote 投的"], "remote")
        chan = {r.title: r.sources[0]["channel"] for r in registry.load_all()}
        self.assertEqual(chan["agent 投的"], "agent_capture")
        self.assertEqual(chan["remote 投的"], "remote_capture")

    def test_direct_run_is_an_owner_privilege(self):
        # a non-owner mode:"run" degrades to the proposal path (raising), the
        # owner's lands approved with the direct-run bookkeeping
        self.assertEqual(actd._apply_capture("agent run", mode="run", via="agent"), "running")
        self.assertEqual(actd._apply_capture("owner run", mode="run", via=None), "running")
        by_title = {r.title: r for r in registry.load_all()}
        self.assertEqual(by_title["agent run"].status, State.RAISING.value)
        self.assertEqual(by_title["owner run"].status, State.APPROVED.value)
        self.assertEqual(by_title["owner run"].delivery_mode, "chat")
        self.assertIn("approved_at", by_title["owner run"].execution)


class AckLedgerTest(unittest.TestCase):
    def setUp(self):
        _clean()

    def test_ack_lines_keep_raw_utf8_and_the_cache_tracks_sync_json(self):
        actd._write_applied_ack("动作-１", "running")
        raw = _APPLIED.read_text(encoding="utf-8")
        self.assertIn('"action_id": "动作-１"', raw)       # ensure_ascii=False
        self.assertNotIn("\\u", raw)
        # cache: same stat key → cached answer; opt-out rewrite → re-read
        self.assertTrue(actd._sync_active())
        cached = actd._SYNC_ACTIVE_CACHE
        self.assertTrue(actd._sync_active())
        self.assertIs(actd._SYNC_ACTIVE_CACHE, cached)
        time.sleep(0.01)
        (config.STATE_DIR / "sync.json").write_text(
            json.dumps({"mode": "local", "pad": "x" * 10}), encoding="utf-8")
        self.assertFalse(actd._sync_active())
        actd._write_applied_ack("silent", "noop")
        self.assertNotIn("silent", _APPLIED.read_text(encoding="utf-8"))

    def test_missing_or_broken_sync_json_is_inactive(self):
        (config.STATE_DIR / "sync.json").unlink()
        actd._SYNC_ACTIVE_CACHE = None
        self.assertFalse(actd._sync_active())
        (config.STATE_DIR / "sync.json").write_text("{broken", encoding="utf-8")
        actd._SYNC_ACTIVE_CACHE = None
        self.assertFalse(actd._sync_active())
        self.assertEqual(actd._SYNC_ACTIVE_CACHE[1], False)


class PreconditionAndDigestGateTest(unittest.TestCase):
    def test_precondition_answers_are_literal_bools(self):
        req = Requirement(id="R-7", title="t", status=State.EXECUTING.value)
        self.assertIs(actd._precondition_ok(req, None), True)
        self.assertIs(actd._precondition_ok(req, "executing"), True)
        self.assertIs(actd._precondition_ok(req, "review", "accept"), True)
        self.assertIs(actd._precondition_ok(req, "review", "rework"), True)
        self.assertIs(actd._precondition_ok(req, "review", "comment"), False)
        self.assertIs(actd._precondition_ok(req, "card_sent"), False)

    def test_weekly_digest_event_only_when_the_launch_ran(self):
        with mock.patch.object(detached, "launch", return_value=detached.RUNNING), \
                mock.patch.object(analytics, "log_event") as ev:
            self.assertEqual(actd._spawn_weekly_digest(), detached.RUNNING)
        ev.assert_called_once_with("weekly_digest_requested")
        with mock.patch.object(detached, "launch", return_value=detached.NOOP), \
                mock.patch.object(analytics, "log_event") as ev:
            self.assertEqual(actd._spawn_weekly_digest(), detached.NOOP)
        ev.assert_not_called()


if __name__ == "__main__":
    unittest.main()
