"""act/radar_claude_sessions.py — one-shot Claude Code session import (§22).

All transcripts are SYNTHETIC fixtures written into a per-test tempdir that
CLAUDE_CONFIG_DIR points at — the real ~/.claude is NEVER read. Registry and
state live under the sandbox AIASSISTANT_HOME (tests/__init__.py).

Contract under test:
(a) scan finds recent sessions: project from cwd, gist = first-user head +
    last-assistant head, waiting-on-you sessions sorted first;
(b) waiting detection: assistant question at the end -> True; assistant
    statement or trailing user message -> False;
(c) exclusions: outside the window, subagent files, sidechain/meta entries,
    bookkeeping-only files, sessions this product itself dispatched;
(d) import: waiting -> card_sent, merely-recent -> detected; marker file
    written; re-import and re-scan are no-ops (dedupe both belts);
(e) the import_claude_sessions inbox action end-to-end through
    actd.process_inbox (explicit ids and the no-ids waiting-only default).
"""
import json
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import actd, radar_claude_sessions as rcs
from act.lib import config, registry


# a real directory, so import sets it as target_repo (existence-checked)
_DEMO_CWD = tempfile.mkdtemp(prefix="demo-app-")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(etype: str, text, ts: datetime, cwd: str = None,
           **extra) -> dict:
    cwd = cwd or _DEMO_CWD
    e = {
        "type": etype,
        "uuid": str(uuid.uuid4()),
        "timestamp": _iso(ts),
        "cwd": cwd,
        "isSidechain": False,
        "sessionId": extra.pop("sessionId", "s"),
    }
    if etype == "user":
        e["message"] = {"role": "user", "content": text}
    elif etype == "assistant":
        e["message"] = {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}
    e.update(extra)
    return e


class ClaudeSessionsRadarTest(unittest.TestCase):
    def setUp(self):
        self.claude_dir = Path(tempfile.mkdtemp(prefix="claude-cfg-"))
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_dir)
        self.proj = self.claude_dir / "projects" / "-tmp-demo-app"
        self.proj.mkdir(parents=True)
        self.now = datetime.now(timezone.utc)
        # isolate registry + marker state between tests
        if config.REGISTRY_DIR.exists():
            shutil.rmtree(config.REGISTRY_DIR)
        config.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        marker = config.STATE_DIR / rcs.STATE_FILE
        if marker.exists():
            marker.unlink()

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        shutil.rmtree(self.claude_dir, ignore_errors=True)

    # -- fixture helpers ---------------------------------------------------- #
    def _write_session(self, sid: str, entries: list, project: Path = None,
                       mtime: datetime = None) -> Path:
        p = (project or self.proj) / f"{sid}.jsonl"
        p.write_text("\n".join(json.dumps(e, ensure_ascii=False)
                               for e in entries) + "\n", encoding="utf-8")
        if mtime is not None:
            ts = mtime.timestamp()
            os.utime(p, (ts, ts))
        return p

    def _waiting_session(self, sid: str = "sess-waiting") -> Path:
        t = self.now - timedelta(hours=2)
        return self._write_session(sid, [
            _entry("user", "Fix the flaky login test in demo-app", t),
            _entry("assistant", "I found two suspects. Should I patch the "
                                "retry helper or rewrite the fixture?",
                   t + timedelta(minutes=3)),
        ])

    def _finished_session(self, sid: str = "sess-done") -> Path:
        t = self.now - timedelta(hours=5)
        return self._write_session(sid, [
            _entry("user", "Rename the config module in demo-app", t),
            _entry("assistant", "Done. All 42 tests pass and the module is "
                                "renamed everywhere.", t + timedelta(minutes=4)),
        ])

    # -- (a) scan ------------------------------------------------------------ #
    def test_scan_builds_candidates_with_gist_and_project(self):
        self._waiting_session()
        cands = rcs.scan(7)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c["session_id"], "sess-waiting")
        self.assertEqual(c["project"], Path(_DEMO_CWD).name)
        self.assertEqual(c["project_dir"], _DEMO_CWD)
        self.assertIn("Fix the flaky login test", c["gist"])
        self.assertIn("→", c["gist"])
        self.assertIn("patch the", c["gist"])
        self.assertTrue(c["ended_waiting_on_user"])
        self.assertTrue(c["last_activity"].endswith("Z"))

    def test_scan_sorts_waiting_first(self):
        self._finished_session("sess-newer-done")   # newer but not waiting
        self._waiting_session("sess-older-waiting")
        cands = rcs.scan(7)
        self.assertEqual([c["session_id"] for c in cands],
                         ["sess-older-waiting", "sess-newer-done"])

    def test_scan_missing_dir_returns_empty(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_dir / "nope")
        self.assertEqual(rcs.scan(7), [])

    def test_ai_title_preferred_for_title(self):
        t = self.now - timedelta(hours=1)
        self._write_session("sess-titled", [
            {"type": "ai-title", "aiTitle": "Login test flakiness fix",
             "sessionId": "sess-titled"},
            _entry("user", "Fix the flaky login test in demo-app", t),
            _entry("assistant", "Working on it.", t + timedelta(minutes=1)),
        ])
        c = rcs.scan(7)[0]
        self.assertEqual(c["title"], "Login test flakiness fix")

    # -- (b) waiting detection ------------------------------------------------ #
    def test_finished_session_not_waiting(self):
        self._finished_session()
        c = rcs.scan(7)[0]
        self.assertFalse(c["ended_waiting_on_user"])

    def test_trailing_user_message_not_waiting(self):
        t = self.now - timedelta(hours=1)
        self._write_session("sess-user-last", [
            _entry("user", "Refactor the payment webhook parser", t),
            _entry("assistant", "Which format should the output use?",
                   t + timedelta(minutes=2)),
            _entry("user", "Use JSON please", t + timedelta(minutes=9)),
        ])
        c = rcs.scan(7)[0]
        self.assertFalse(c["ended_waiting_on_user"])

    def test_chinese_question_is_waiting(self):
        t = self.now - timedelta(hours=1)
        self._write_session("sess-zh", [
            _entry("user", "把周报模板改成双语版本", t),
            _entry("assistant", "有两种排版方案，请确认要哪一种。",
                   t + timedelta(minutes=2)),
        ])
        c = rcs.scan(7)[0]
        self.assertTrue(c["ended_waiting_on_user"])

    # -- (c) exclusions -------------------------------------------------------- #
    def test_old_session_outside_window_excluded(self):
        t = self.now - timedelta(days=30)
        self._write_session("sess-old", [
            _entry("user", "Archive the old migration scripts", t),
            _entry("assistant", "Archived.", t + timedelta(minutes=2)),
        ], mtime=t)
        self.assertEqual(rcs.scan(7), [])

    def test_recent_mtime_but_old_activity_excluded(self):
        # touched recently (e.g. by a backup tool) but conversation is old
        t = self.now - timedelta(days=30)
        self._write_session("sess-touched", [
            _entry("user", "Archive the old migration scripts", t),
            _entry("assistant", "Archived.", t + timedelta(minutes=2)),
        ])  # mtime = now
        self.assertEqual(rcs.scan(7), [])

    def test_subagent_files_excluded(self):
        self._waiting_session()
        sub = self.proj / "sess-waiting" / "subagents"
        sub.mkdir(parents=True)
        t = self.now - timedelta(hours=1)
        self._write_session("agent-deadbeef", [
            _entry("user", "subagent task text", t),
            _entry("assistant", "subagent answer?", t),
        ], project=sub)
        cands = rcs.scan(7)
        self.assertEqual([c["session_id"] for c in cands], ["sess-waiting"])

    def test_sidechain_and_meta_entries_ignored(self):
        t = self.now - timedelta(hours=1)
        self._write_session("sess-side", [
            _entry("user", "Trim the docker image size", t),
            _entry("assistant", "Trimmed to 120MB.", t + timedelta(minutes=2)),
            _entry("assistant", "Sidechain: which base image?",
                   t + timedelta(minutes=3), isSidechain=True),
            _entry("user", "meta note", t + timedelta(minutes=4), isMeta=True),
        ])
        c = rcs.scan(7)[0]
        self.assertFalse(c["ended_waiting_on_user"])
        self.assertNotIn("Sidechain", c["gist"])

    def test_bookkeeping_only_file_skipped(self):
        self._write_session("sess-empty", [
            {"type": "queue-operation", "operation": "enqueue",
             "sessionId": "sess-empty", "timestamp": _iso(self.now)},
        ])
        self.assertEqual(rcs.scan(7), [])

    def test_own_dispatched_sessions_excluded(self):
        self._waiting_session("sess-own")
        registry.upsert(registry.Requirement(
            id="R-900", title="own dispatched work", status="executing",
            execution={"session_id": "sess-own"}))
        self.assertEqual(rcs.scan(7), [])

    # -- (d) import ------------------------------------------------------------ #
    def test_import_by_ids_statuses_and_marker(self):
        self._waiting_session()
        self._finished_session()
        n = rcs.import_by_ids(["sess-waiting", "sess-done"])
        self.assertEqual(n, 2)
        by_title = {r.title: r for r in registry.load_all()}
        waiting = next(r for r in by_title.values()
                       if "flaky login" in r.title)
        done = next(r for r in by_title.values()
                    if "Rename the config" in r.title)
        self.assertEqual(waiting.status, "card_sent")
        self.assertEqual(done.status, "detected")
        self.assertEqual(waiting.sources[0]["channel"], "claude_code")
        self.assertEqual(waiting.sources[0]["ref"], "sess-waiting")
        self.assertIn("claude-code 导入", waiting.notes)
        self.assertEqual(waiting.target_repo, _DEMO_CWD)
        marker = json.loads((config.STATE_DIR / rcs.STATE_FILE)
                            .read_text(encoding="utf-8"))
        self.assertIn("sess-waiting", marker["imported"])
        self.assertIn("sess-done", marker["imported"])

    def test_reimport_and_rescan_are_noops(self):
        self._waiting_session()
        self.assertEqual(rcs.import_by_ids(["sess-waiting"]), 1)
        before = len(registry.load_all())
        self.assertEqual(rcs.import_by_ids(["sess-waiting"]), 0)
        self.assertEqual(len(registry.load_all()), before)
        self.assertEqual(rcs.scan(7), [])   # imported ids leave the scan too

    def test_import_ids_with_slash_dropped(self):
        self._waiting_session()
        self.assertEqual(rcs.import_by_ids(["../../etc/passwd", ""]), 0)

    def test_run_once_default_imports_waiting_only(self):
        self._waiting_session()
        self._finished_session()
        self.assertEqual(rcs.run_once(7), 1)
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].status, "card_sent")

    def test_run_once_all_imports_everything(self):
        self._waiting_session()
        self._finished_session()
        self.assertEqual(rcs.run_once(7, include_all=True), 2)

    def test_missing_project_dir_not_set_as_target_repo(self):
        t = self.now - timedelta(hours=1)
        self._write_session("sess-gone", [
            _entry("user", "Summarize the sprint retro notes", t,
                   cwd="/nonexistent/path/xyz"),
            _entry("assistant", "Which sprint do you mean?",
                   t + timedelta(minutes=1), cwd="/nonexistent/path/xyz"),
        ])
        rcs.import_by_ids(["sess-gone"])
        req = registry.load_all()[0]
        self.assertIsNone(req.target_repo)

    # -- (e) inbox action end-to-end -------------------------------------------- #
    def _inbox_write(self, payload: dict) -> None:
        config.ensure_state_dirs()
        p = config.INBOX_DIR / f"{uuid.uuid4()}.json"
        p.write_text(json.dumps(payload), encoding="utf-8")

    def test_inbox_action_with_ids(self):
        self._waiting_session()
        self._inbox_write({"action": "import_claude_sessions",
                           "session_ids": ["sess-waiting"],
                           "ts": _iso(self.now)})
        processed = actd.process_inbox()
        self.assertEqual(processed, 1)
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].status, "card_sent")
        self.assertFalse(list(config.INBOX_DIR.glob("*.json")))

    def test_inbox_action_without_ids_imports_waiting_in_window(self):
        self._waiting_session()
        self._finished_session()
        self._inbox_write({"action": "import_claude_sessions",
                           "window_days": 7, "ts": _iso(self.now)})
        actd.process_inbox()
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].status, "card_sent")

    def test_inbox_action_bad_payload_never_raises(self):
        self._inbox_write({"action": "import_claude_sessions",
                           "session_ids": "not-a-list",
                           "window_days": "soon"})
        # falls back to the no-ids window default over an empty fixture set
        self.assertEqual(actd.process_inbox(), 1)
        self.assertEqual(registry.load_all(), [])


if __name__ == "__main__":
    unittest.main()
