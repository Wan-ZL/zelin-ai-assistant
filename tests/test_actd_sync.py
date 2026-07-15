"""actd §5.4 sync-safety changes (plan of record §5.4):

  * comment/raise/accept/rework gain a SYNC-ONLY stale-guard — a synced action
    carries the expected_status the phone SAW; if the card has since moved, the
    action is an idempotent no-op (never rips a running/moved card back). LOCAL
    Mac-app / web callers send NO expected_status, so these actions apply
    exactly as they do on main — no hard status precondition is imposed on the
    local path (regression B1/M1: a local accept/rework on a 待验收 card whose
    on-disk status is still EXECUTING, or a local comment on a RAISING card,
    must APPLY, not silently no-op);
  * process_inbox writes a state/sync/applied.jsonl ack line on EVERY terminal
    disposition — success (running), guarded no-op (noop), unknown/gone req
    (unknown) and bad-JSON (bad_json) — so the phone's "did it land?" is a
    durable truth, not inferred from the always-deleted inbox file. M2: acks are
    only written when cloud sync is ACTIVE, so a local-only install never grows
    applied.jsonl (see AppliedAckGatingTestCase).

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py) and on
macOS/Linux unchanged.
"""
import json
import shutil
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_APPLIED = config.STATE_DIR / "sync" / "applied.jsonl"
_SYNC_JSON = config.STATE_DIR / "sync.json"


def _activate_sync():
    """M2: acks are only written when cloud sync is opted in. This suite is the
    SYNC-safety suite, so it runs with sync active (state/sync.json mode=cloud)
    and also resets actd's cheap _sync_active stat-cache."""
    config.ensure_state_dirs()
    _SYNC_JSON.write_text(json.dumps({"mode": "cloud", "device_id": "dev-test"}),
                          encoding="utf-8")
    actd._SYNC_ACTIVE_CACHE = None


def _mk_req(req_id="R-700", status=State.CARD_SENT.value, execution=None, notes=""):
    req = Requirement(id=req_id, title="sync guard test", status=status,
                      execution=execution, notes=notes)
    registry.save(req)
    return req


def _drop(action, req_id=None, comment=None, expected_status=None,
          board_seq=None, raw=None):
    """Write one inbox file; return its action_id (= filename stem)."""
    config.ensure_state_dirs()
    aid = str(uuid.uuid4())
    path = config.INBOX_DIR / f"{aid}.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return aid
    body = {"id": req_id, "action": action, "comment": comment,
            "ts": "2026-07-08T00:00:00Z"}
    if expected_status is not None:
        body["expected_status"] = expected_status
    if board_seq is not None:
        body["board_seq"] = board_seq
    path.write_text(json.dumps(body), encoding="utf-8")
    return aid


def _acks() -> list:
    if not _APPLIED.exists():
        return []
    return [json.loads(ln) for ln in _APPLIED.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _result_for(aid: str):
    for rec in _acks():
        if rec.get("action_id") == aid:
            return rec.get("result_status")
    return None


class SyncGuardBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        try:
            _APPLIED.unlink()
        except OSError:
            pass
        _activate_sync()


# --------------------------------------------------------------------------- #
# status guards — stale / wrong-precondition actions are no-ops
# --------------------------------------------------------------------------- #
class StatusGuardTestCase(SyncGuardBase):
    # ---- SYNC stale-guard: expected_status present + mismatched => no-op ---- #
    def test_comment_stale_expected_status_is_noop(self):
        _mk_req(status=State.EXECUTING.value)
        aid = _drop("comment", "R-700", comment="改方向", expected_status="card_sent")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.EXECUTING.value)
        self.assertEqual(_result_for(aid), "noop")

    def test_accept_expected_status_mismatch_is_noop(self):
        # even though the card IS in review, a phone that saw it 'delivered'
        # must not re-accept it — the synced expected_status guards that.
        _mk_req(status=State.REVIEW.value)
        aid = _drop("accept", "R-700", expected_status="delivered")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.REVIEW.value)
        self.assertEqual(_result_for(aid), "noop")

    # (d) regression: a SYNCED accept whose pinned expected_status no longer
    # matches the current status is a no-op (the intended stale-guard).
    def test_synced_accept_expected_review_but_now_card_sent_is_noop(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("accept", "R-700", expected_status="review")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.CARD_SENT.value)
        self.assertEqual(_result_for(aid), "noop")

    # ---- LOCAL callers (no expected_status) apply exactly as on main -------- #
    def test_comment_on_card_sent_applies(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("comment", "R-700", comment="加个测试")
        actd.process_inbox()
        req = registry.load("R-700")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertIn("加个测试", req.notes or "")
        self.assertEqual(_result_for(aid), "running")

    # (c) regression B1: a LOCAL comment on a RAISING/processing card (the web
    # renders 修改 on it) must APPLY — fold + return to card_sent — not no-op.
    def test_comment_on_raising_card_applies_without_expected(self):
        _mk_req(status=State.RAISING.value)
        aid = _drop("comment", "R-700", comment="换个方向")
        actd.process_inbox()
        req = registry.load("R-700")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertIn("换个方向", req.notes or "")
        self.assertEqual(_result_for(aid), "running")

    def test_raise_from_detected_applies(self):
        _mk_req(status=State.DETECTED.value)
        aid = _drop("raise", "R-700")
        with mock.patch.object(actd, "analyze", mock.Mock()):
            actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.RAISING.value)
        self.assertEqual(_result_for(aid), "running")

    # restore main: a LOCAL raise carries no expected_status, so it applies
    # regardless of the current status (no hard detected-only precondition).
    def test_raise_without_expected_applies_from_card_sent(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("raise", "R-700")
        with mock.patch.object(actd, "analyze", mock.Mock()):
            actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.RAISING.value)
        self.assertEqual(_result_for(aid), "running")

    def test_accept_from_review_applies(self):
        _mk_req(status=State.REVIEW.value)
        aid = _drop("accept", "R-700", expected_status="review")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.DELIVERED.value)
        self.assertEqual(_result_for(aid), "running")

    # (a) regression B1: the 待验收 lane legitimately holds a card whose on-disk
    # status is still EXECUTING (agent done, not yet promoted — process_inbox
    # runs BEFORE reconcile_executing). A LOCAL 验收 (no expected_status) must
    # APPLY → delivered; a hard REVIEW-only precondition silently broke this.
    def test_accept_on_executing_done_review_card_applies(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-1", "done": True})
        aid = _drop("accept", "R-700")
        actd.process_inbox()
        req = registry.load("R-700")
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertTrue((req.execution or {}).get("accepted_at"))
        self.assertEqual(_result_for(aid), "running")

    def test_rework_from_review_applies(self):
        _mk_req(status=State.REVIEW.value)
        aid = _drop("rework", "R-700", comment="补一段")
        fake_exec = mock.Mock()
        fake_exec.rework.return_value = True
        with mock.patch.object(actd, "executor", fake_exec):
            actd.process_inbox()
        fake_exec.rework.assert_called_once()
        self.assertEqual(_result_for(aid), "running")

    # (b) regression B1: 打回 on an EXECUTING-done 待验收 card, LOCAL (no
    # expected_status) — executor.rework runs (stop-idle-then-resume) and the
    # card goes back to executing; a hard REVIEW-only precondition no-oped it.
    def test_rework_on_executing_done_review_card_applies(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-1", "done": True})
        aid = _drop("rework", "R-700", comment="再改改")
        fake_exec = mock.Mock()
        fake_exec.rework.return_value = True
        with mock.patch.object(actd, "executor", fake_exec):
            actd.process_inbox()
        fake_exec.rework.assert_called_once()
        self.assertEqual(_result_for(aid), "running")


# --------------------------------------------------------------------------- #
# applied.jsonl ack — one line for EVERY terminal disposition
# --------------------------------------------------------------------------- #
class AppliedAckTestCase(SyncGuardBase):
    def test_success_writes_running_ack(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("approve", "R-700")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "running")

    def test_guarded_noop_writes_noop_ack(self):
        # a SYNCED accept whose pinned expected_status no longer matches the
        # current status is the guarded (stale) no-op.
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("accept", "R-700", expected_status="review")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")

    def test_unknown_req_writes_unknown_ack(self):
        aid = _drop("approve", "R-DOES-NOT-EXIST")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "unknown")

    def test_bad_json_writes_bad_json_ack(self):
        aid = _drop("approve", raw="{ this is not valid json")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "bad_json")

    def test_unknown_action_writes_unknown_ack(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("frobnicate", "R-700")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "unknown")


# --------------------------------------------------------------------------- #
# M2 — a local-only install (no cloud sync) never grows applied.jsonl
# --------------------------------------------------------------------------- #
class AppliedAckGatingTestCase(unittest.TestCase):
    """Deliberately does NOT activate sync — the local Mac/web scenario."""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        # the shared sandbox home is reused across test classes — start from a
        # truly local state so we can assert the sync dir is never (re)created.
        shutil.rmtree(config.STATE_DIR / "sync", ignore_errors=True)
        try:
            _SYNC_JSON.unlink()
        except OSError:
            pass
        actd._SYNC_ACTIVE_CACHE = None

    def test_local_action_writes_no_ack_and_no_sync_dir(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("approve", "R-700")
        n = actd.process_inbox()
        self.assertEqual(n, 1)                       # action still applied
        self.assertEqual(registry.load("R-700").status, State.APPROVED.value)
        self.assertFalse(_APPLIED.exists())          # M2: no applied.jsonl
        self.assertFalse((config.STATE_DIR / "sync").exists())  # no state/sync/
        self.assertIsNone(_result_for(aid))

    def test_mode_off_is_also_treated_as_local(self):
        _SYNC_JSON.write_text(json.dumps({"mode": "off"}), encoding="utf-8")
        actd._SYNC_ACTIVE_CACHE = None
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("approve", "R-700")
        actd.process_inbox()
        self.assertFalse(_APPLIED.exists())
        self.assertIsNone(_result_for(aid))

    def test_enabling_sync_starts_writing_acks_without_restart(self):
        # M2 cache is stat-keyed: flipping sync.json to cloud is picked up in
        # the same process (actd is long-lived and never reloads its config) —
        # WITHOUT manually clearing the cache here, proving the stat check
        # invalidates it on its own.
        _mk_req(status=State.CARD_SENT.value)
        aid1 = _drop("approve", "R-700")
        actd.process_inbox()
        self.assertIsNone(_result_for(aid1))         # local: no ack yet
        _SYNC_JSON.write_text(json.dumps({"mode": "cloud", "device_id": "d"}),
                              encoding="utf-8")       # enable mid-run
        _mk_req(req_id="R-701", status=State.CARD_SENT.value)
        aid2 = _drop("approve", "R-701")
        actd.process_inbox()
        self.assertEqual(_result_for(aid2), "running")   # now acking


class StartupConfigGuardTestCase(unittest.TestCase):
    """main() 启动处的 config 纵深防御（夜间审计批次）：load_config 意外抛
    异常时用内置默认起动，坏 config.yaml/overrides 绝不拒启 daemon
    （load_config 自身已防崩，这里守「万一」）。"""

    def test_main_once_survives_load_config_crash(self):
        with mock.patch.object(actd.config, "load_config",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(actd, "run_once", return_value=None) as ro:
            code = actd.main(["--once"])
        self.assertEqual(code, 0)                      # 没有崩、正常跑完一轮
        cfg = ro.call_args[0][0]
        self.assertIsInstance(cfg, config.Config)      # 用的是内置默认


if __name__ == "__main__":
    unittest.main()
