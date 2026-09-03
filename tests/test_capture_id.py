"""§10 ``capture_id`` — a quick capture maps exactly to the card it minted (issue #7).

Chain pinned end to end (inbox filename → registry source → dashboard card):
  - actd processes ``state/inbox/capture-<uuid>.json`` → the birth ``sources[0]``
    row carries ``capture_id == "capture-<uuid>"`` (the stem, = what the server
    returns to the web as ``file`` minus ``.json``); Slack self-DM captures have no
    inbox file → no key;
  - the raising placeholder AND the expanded card_sent row on the dashboard carry
    the card-level ``capture_id``; projected ``sources[]`` rows carry it too;
  - ``mode:"run"`` cards carry it as well (and it equals ``execution.inbox_stem``);
  - two near-identical captures that do NOT fold each keep their own id; a fold
    (restatement) keeps the BIRTH id — folds never rewrite it;
  - the raw registry YAML round-trips the key (add-only, free-form source dict);
  - cards without the key project without it (old cards decode unchanged).
Runs inside the sandbox AIASSISTANT_HOME; no LLM is ever invoked.
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, dashboard, quick_capture, registry
from act.lib.registry import Requirement, State
from tests import store2_testkit


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.rglob("*.yaml"):
        p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()


def _drop_capture(text, **extra):
    stem = f"capture-{uuid.uuid4()}"
    rec = {"action": "capture", "text": text, "ts": "2026-09-02T00:00:00Z", **extra}
    (config.INBOX_DIR / f"{stem}.json").write_text(json.dumps(rec), encoding="utf-8")
    return stem


def _dash(reqs):
    return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=config.Config(), archived=[])


def _row(dash, lane, rid):
    return next(r for r in dash[lane] if r["id"] == rid)


class CaptureIdTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def test_inbox_capture_stamps_birth_source_and_dashboard_rows(self):
        stem = _drop_capture("给下周评审准备材料 capture-id 判例")
        actd.process_inbox()
        (req,) = registry.load_all()
        self.assertEqual(req.sources[0]["capture_id"], stem)
        self.assertEqual(req.sources[0]["channel"], "quick_capture")
        # raising placeholder (what the web sees first) already carries it
        self.assertEqual(req.status, State.RAISING.value)
        dash = _dash([req])
        self.assertEqual(_row(dash, "needs_approval", req.id)["capture_id"], stem)
        # after AI expansion → card_sent: the full proposal row + its sources carry it
        req.set_status(State.CARD_SENT)
        registry.save(req)
        dash = _dash([registry.load(req.id)])
        row = _row(dash, "needs_approval", req.id)
        self.assertEqual(row["capture_id"], stem)
        self.assertEqual(row["sources"][0]["capture_id"], stem)
        self.assertEqual(row["sources"][0]["quote"], "给下周评审准备材料 capture-id 判例")

    def test_registry_yaml_round_trips_the_key(self):
        stem = _drop_capture("YAML 往返判例")
        actd.process_inbox()
        (req,) = registry.load_all()
        registry.save(req)
        self.assertEqual(registry.load(req.id).sources[0]["capture_id"], stem)

    def test_store2_sqlite_round_trips_the_key(self):
        # production truth is store2 (§53): the payload keeps the whole source
        # dict, the sources table only projects channel/who/date/ref/quote
        store2_testkit.use_backend(self, "sqlite")
        stem = _drop_capture("SQLite 往返判例")
        actd.process_inbox()
        (req,) = registry.load_all()
        self.assertEqual(req.sources[0]["capture_id"], stem)
        registry.reset_store_cache()
        self.assertEqual(registry.load(req.id).sources[0]["capture_id"], stem)

    def test_run_mode_card_carries_it_and_matches_inbox_stem(self):
        stem = _drop_capture("直接开跑 capture-id 判例", mode="run", via="web")
        actd.process_inbox()
        (req,) = registry.load_all()
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertEqual(req.sources[0]["capture_id"], stem)
        self.assertEqual(req.execution["inbox_stem"], stem)

    def test_two_distinct_captures_keep_their_own_ids(self):
        a = _drop_capture("A 任务：整理评审材料")
        b = _drop_capture("B 任务：写季度汇报草稿")
        actd.process_inbox()
        by_stem = {r.sources[0]["capture_id"]: r for r in registry.load_all()}
        self.assertEqual(set(by_stem), {a, b})
        self.assertNotEqual(by_stem[a].id, by_stem[b].id)

    def test_fold_keeps_the_birth_capture_id(self):
        first = _drop_capture("同一件事 fold 判例")
        actd.process_inbox()
        (req,) = registry.load_all()
        req.set_status(State.CARD_SENT)
        registry.save(req)
        second = _drop_capture("同一件事 fold 判例")     # exact restatement → folds
        actd.process_inbox()
        (req,) = registry.load_all()
        self.assertEqual(req.sources[0]["capture_id"], first)
        self.assertNotEqual(first, second)
        self.assertEqual(_row(_dash([req]), "needs_approval", req.id)["capture_id"], first)

    def test_slack_self_dm_capture_has_no_key(self):
        quick_capture.apply_result({"action": "new_proposal", "title": "Slack 来的想法",
                                    "summary": "Slack 来的想法", "_text": "Slack 来的想法"})
        (req,) = registry.load_all()
        self.assertNotIn("capture_id", req.sources[0])
        row = _row(_dash([req]), "needs_approval", req.id)
        self.assertNotIn("capture_id", row)
        self.assertNotIn("capture_id", row["sources"][0])

    def test_legacy_card_without_key_projects_unchanged(self):
        req = Requirement(id="R-1", title="老卡", status=State.CARD_SENT.value,
                          sources=[{"who": "zelin", "channel": "quick_capture",
                                    "date": "2026-01-01", "quote": "老卡"}])
        row = _row(_dash([req]), "needs_approval", "R-1")
        self.assertNotIn("capture_id", row)
        self.assertEqual(set(row["sources"][0]), {"who", "channel", "date", "quote"})


if __name__ == "__main__":
    unittest.main()
