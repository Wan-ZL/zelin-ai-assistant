"""§38 可逆折叠 — timestamped fold notes + the split_note inbox action.

End-to-end: a radar fold lands as a timestamped note line; split_note re-files
that line as a NEW card via the normal capture path (raising) and tags the
origin line 已拆出; replays / poison payloads / unknown handles are honest
no-ops (never a second card). Plus the registry fold-note helpers and the
webui boundary for the new action.
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, quick_capture, registry
from act.lib.registry import Requirement, State


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    # load() prefers the archive copy of an id — a leftover archived twin
    # would shadow every later _seed of the same id (test isolation).
    if registry.ARCHIVE_DIR.exists():
        for p in registry.ARCHIVE_DIR.glob("*.yaml"):
            p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()


def _seed(rid="R-100", title="整理 EB-1A 推荐信清单", status=State.CARD_SENT.value, **kw):
    r = Requirement(id=rid, title=title, status=status, **kw)
    registry.save(r)
    return r


def _drop(action_dict):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(json.dumps(action_dict, ensure_ascii=False), encoding="utf-8")
    return path


class FoldNoteHelpersTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)

    def test_append_is_timestamped_and_prefix_frozen(self):
        r = _seed()
        ts = registry.append_fold_note(r, "wegreened 回邮件了", "radar")
        self.assertIsNotNone(ts)
        # the pre-§38 anchor: "[radar] <text>" stays a literal prefix
        self.assertIn("[radar] wegreened 回邮件了", r.notes)
        self.assertIn(f"[@{ts}]", r.notes)

    def test_dedupe_on_text_survives_timestamp(self):
        r = _seed()
        registry.append_fold_note(r, "同一条进展", "radar")
        again = registry.append_fold_note(r, "同一条进展", "radar")
        self.assertIsNone(again)
        self.assertEqual(r.notes.count("同一条进展"), 1)

    def test_legacy_untimestamped_line_counts_as_existing(self):
        r = _seed(notes="[radar] 旧格式进展")
        self.assertIsNone(registry.append_fold_note(r, "旧格式进展", "radar"))

    def test_same_second_collision_gets_suffix(self):
        r = _seed()
        with mock.patch.object(registry, "_iso_now",
                               return_value="2026-07-16T08:00:00Z"):
            t1 = registry.append_fold_note(r, "第一条", "radar")
            t2 = registry.append_fold_note(r, "第二条", "radar")
        self.assertEqual(t1, "2026-07-16T08:00:00Z")
        self.assertEqual(t2, "2026-07-16T08:00:00Z#2")

    def test_parse_roundtrip_and_split_tag(self):
        r = _seed(notes="随手写的非折叠行")
        ts = registry.append_fold_note(r, "拆我", "quick")
        self.assertTrue(registry.mark_note_split(r, ts, "R-999"))
        parsed = registry.parse_fold_notes(r.notes)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["kind"], "quick")
        self.assertEqual(parsed[0]["text"], "拆我")
        self.assertEqual(parsed[0]["ts"], ts)
        self.assertEqual(parsed[0]["split_into"], "R-999")
        # already split → False (idempotent replay)
        self.assertFalse(registry.mark_note_split(r, ts, "R-998"))
        # non-fold lines survive untouched
        self.assertIn("随手写的非折叠行", r.notes)

    def test_fold_paths_produce_timestamped_lines(self):
        target = _seed(rid="R-101")
        child = Requirement(id="", title="x", sources=[{
            "who": "quinton", "channel": "slack", "date": "2026-07-16",
            "quote": "进展一句"}])
        quick_capture._fold_into(target, child, "进展一句")
        entries = registry.parse_fold_notes(registry.load("R-101").notes)
        self.assertEqual(len(entries), 1)
        self.assertIsNotNone(entries[0]["ts"])


class SplitNoteEndToEndTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)

    def _fold_then_ts(self, rid="R-100"):
        r = _seed(rid=rid, type="paperwork", tier="T1")
        ts = registry.append_fold_note(r, "顺手记的另一件事 pytest", "radar")
        registry.save(r)
        return r, ts

    def test_fold_split_new_card_origin_tagged(self):
        r, ts = self._fold_then_ts()
        _drop({"action": "split_note", "id": r.id, "note_ts": ts,
               "ts": "2026-07-16T00:00:00Z"})
        actd.process_inbox()
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        reqs = {x.id: x for x in registry.load_all()}
        new = [x for x in reqs.values() if x.id != r.id]
        self.assertEqual(len(new), 1)
        new = new[0]
        # normal capture path: raising → AI expansion → proposal
        self.assertEqual(new.status, State.RAISING.value)
        self.assertEqual(new.title, "顺手记的另一件事 pytest")
        self.assertIn(f"[拆自 {r.id}]", new.notes)
        self.assertEqual(new.split_from, r.id)   # machine-readable lineage (§38.3)
        self.assertEqual(new.type, "paperwork")
        self.assertEqual(new.sources[0]["channel"], "split")
        # origin line kept + tagged
        origin = reqs[r.id]
        parsed = registry.parse_fold_notes(origin.notes)
        self.assertEqual(parsed[0]["split_into"], new.id)
        self.assertEqual(parsed[0]["text"], "顺手记的另一件事 pytest")

    def test_replay_never_mints_a_second_card(self):
        r, ts = self._fold_then_ts()
        for _ in range(2):
            _drop({"action": "split_note", "id": r.id, "note_ts": ts})
            actd.process_inbox()
        others = [x for x in registry.load_all() if x.id != r.id]
        self.assertEqual(len(others), 1)

    def test_unknown_ts_and_unknown_card_are_noops(self):
        r, _ts = self._fold_then_ts()
        self.assertEqual(actd._apply_split_note(r.id, "2020-01-01T00:00:00Z"),
                         "noop")
        self.assertEqual(actd._apply_split_note("R-777", "x"), "unknown")
        self.assertEqual(len([x for x in registry.load_all() if x.id != r.id]), 0)

    def test_poison_types_are_noops(self):
        r, ts = self._fold_then_ts()
        self.assertEqual(actd._apply_split_note(None, ts), "noop")
        self.assertEqual(actd._apply_split_note(r.id, ["x"]), "noop")
        self.assertEqual(actd._apply_split_note(123, ts), "noop")

    def test_archived_card_is_guarded(self):
        r, ts = self._fold_then_ts()
        registry.archive(r, reason="user")
        self.assertEqual(actd._apply_split_note(r.id, ts), "noop")

    def test_all_terminal_states_are_guarded(self):
        # review blocker 8: trashed/merged/rejected must no-op exactly like
        # archived (§32.2 terminal-state doctrine) — a stale detail panel must
        # not mint a live card (+1 expand run) out of a dead one.
        for status in (State.TRASHED.value, State.MERGED.value,
                       State.REJECTED.value):
            with self.subTest(status=status):
                _clean()
                r, ts = self._fold_then_ts()
                r.set_status(status)
                registry.save(r)
                self.assertEqual(actd._apply_split_note(r.id, ts), "noop")
                self.assertEqual(
                    [x.id for x in registry.load_all() if x.id != r.id], [])
                # origin notes untouched — no phantom 已拆出 tag
                entry = registry.parse_fold_notes(registry.load(r.id).notes)[0]
                self.assertIsNone(entry["split_into"])

    def test_legacy_untimestamped_note_cannot_split(self):
        r = _seed(notes="[radar] 老格式没有句柄")
        self.assertEqual(actd._apply_split_note(r.id, ""), "noop")


class WebuiSplitNoteBoundaryTestCase(unittest.TestCase):
    """webui 400-gates split_note like every other action (§38 triple check)."""

    def test_action_allowed_and_note_ts_type_checked(self):
        from act import webui
        self.assertIn("split_note", webui.ALLOWED_ACTIONS)
        self.assertIn("note_ts", webui._INBOX_KEYS)

    def test_syncd_shape_gate_covers_note_ts(self):
        from act import syncd
        self.assertIsNone(syncd._inbox_shape_error(
            {"action": "split_note", "id": "R-1", "note_ts": "t"}))
        self.assertIsNotNone(syncd._inbox_shape_error(
            {"action": "split_note", "id": "R-1", "note_ts": 42}))
