"""v0.40.0 钱看得见、事有回执 (CONTRACT §40) — honesty/feedback debt.

Pinned here:
- cost_state projection matrix (§40.1): "estimated" for any parseable number
  (the threshold keeps gating only show_cost / the collapsed badge),
  "unknown" for missing/corrupt estimates — unknown-cost cards must never
  read as free;
- purge_at math (§40.5): trashed_at + retention, null for pinned rows /
  disabled retention / unparsable trashed_at — exactly the rows
  actd.purge_trash skips, so the countdown never promises a purge that
  isn't coming;
- capture receipt chooser (§40.2): capture decision (res["action"]) ->
  emoji reaction on the captured self-DM message; the receipt fires only
  after apply_result returned; off-switch; failure is analytics-only and
  never raises into the capture;
- radar give-up diagnostic card (§40.3): filed in 备选 on give-up, deduped
  by note path forever (incl. trashed duplicates), end-to-end through
  radar.scan;
- weekly-digest failure exits notify on MANUAL runs (§40.4) — the detached
  「现在生成一份」 press must never fail silently; scheduled runs stay
  quiet (a failed Monday re-fires hourly — an unconditional notify would
  ping all day);
- notification batching (§40.6): >2 fresh proposals in one pass collapse
  to one 「雷达新增 N 张待审批卡」; 回锅 and ≤2 fresh stay per-card.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no
LLM subprocess is ever spawned (runners/extractors injected).
"""
import datetime as _dt
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, radar, radar_slack, weekly_digest
from act.lib import config, dashboard, notify, registry
from act.lib.registry import Requirement, State


# --------------------------------------------------------------------------- #
# §40.1 cost_state projection matrix
# --------------------------------------------------------------------------- #
class CostStateTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()  # show_cost_above_usd = 5.0
        home = tempfile.mkdtemp(prefix="cost-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _card(self, **fields):
        req = Requirement.from_dict(
            {"id": "R-1", "title": "t", "status": "card_sent", **fields})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        return dash["needs_approval"][0]

    def test_estimate_below_threshold_is_estimated_but_badge_hidden(self):
        item = self._card(cost_estimate_usd=3)
        self.assertEqual(item["cost_usd"], 3.0)
        self.assertFalse(item["show_cost"])         # threshold gates the badge
        self.assertEqual(item["cost_state"], "estimated")  # detail says money

    def test_estimate_at_threshold_shows_badge_and_estimated(self):
        item = self._card(cost_estimate_usd=12)
        self.assertTrue(item["show_cost"])
        self.assertEqual(item["cost_state"], "estimated")

    def test_missing_estimate_is_unknown_not_free(self):
        item = self._card()
        self.assertIsNone(item["cost_usd"])
        self.assertFalse(item["show_cost"])
        self.assertEqual(item["cost_state"], "unknown")

    def test_corrupt_estimate_is_unknown(self):
        item = self._card(cost_estimate_usd="cheap")
        self.assertIsNone(item["cost_usd"])
        self.assertEqual(item["cost_state"], "unknown")


# --------------------------------------------------------------------------- #
# §40.5 purge_at math + pinned
# --------------------------------------------------------------------------- #
class PurgeAtTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()  # trash_retention_days = 60
        home = tempfile.mkdtemp(prefix="purge-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _row(self, **fields):
        req = Requirement.from_dict(
            {"id": "R-9", "title": "t", "status": "trashed",
             "trash_reason": "deleted", **fields})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        return dash["trash"][0]

    def test_purge_at_is_trashed_at_plus_retention(self):
        row = self._row(trashed_at="2026-07-01T00:00:00Z")
        self.assertEqual(row["purge_at"], "2026-08-30T00:00:00Z")  # +60 days

    def test_pinned_row_never_gets_a_deadline(self):
        row = self._row(trashed_at="2026-07-01T00:00:00Z", permanent=True)
        self.assertIsNone(row["purge_at"])

    def test_retention_disabled_means_no_deadline(self):
        self.cfg.trash_retention_days = 0
        row = self._row(trashed_at="2026-07-01T00:00:00Z")
        self.assertIsNone(row["purge_at"])

    def test_unparsable_trashed_at_means_no_deadline(self):
        # purge_trash skips these rows too — the countdown must not promise
        # a purge that isn't coming.
        row = self._row(trashed_at="not-a-date")
        self.assertIsNone(row["purge_at"])


# --------------------------------------------------------------------------- #
# §40.2 capture receipt chooser (emoji reaction ack)
# --------------------------------------------------------------------------- #
def _clean_registry():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        shutil.rmtree(config.REGISTRY_DIR)
    config.ensure_state_dirs()


class CaptureReceiptTestCase(unittest.TestCase):
    def setUp(self):
        _clean_registry()
        self.addCleanup(_clean_registry)
        self.cfg = config.Config()
        self.msg = {"channel": "D123", "channel_type": "self",
                    "ts": "1700.42", "text": "记一下"}

    def _ack(self, kind, resp=None):
        calls = []

        def fake_api(method, token, params=None):
            calls.append((method, params))
            return resp if resp is not None else {"ok": True}

        with mock.patch.object(radar_slack, "slack_api", fake_api):
            radar_slack._ack_capture("xoxp-t", self.msg, kind, self.cfg)
        return calls

    def test_receipt_kind_mirrors_the_normalized_decision(self):
        # capture() normalizes action to exactly new_proposal/relates_to/
        # ignore; anything else (defensive) must read as ignored, never filed.
        self.assertEqual(radar_slack._receipt_kind({"action": "new_proposal"}),
                         "new_proposal")
        self.assertEqual(radar_slack._receipt_kind({"action": "relates_to"}),
                         "relates_to")
        self.assertEqual(radar_slack._receipt_kind({"action": "ignore"}),
                         "ignored")
        self.assertEqual(radar_slack._receipt_kind({}), "ignored")
        self.assertEqual(radar_slack._receipt_kind(None), "ignored")

    def test_filed_decisions_get_inbox_tray(self):
        for kind in ("new_proposal", "relates_to"):
            (call,) = self._ack(kind)
            self.assertEqual(call[0], "reactions.add")
            self.assertEqual(call[1]["name"], "inbox_tray")
            self.assertEqual(call[1]["channel"], "D123")
            self.assertEqual(call[1]["timestamp"], "1700.42")

    def test_ignored_gets_no_entry_sign(self):
        (call,) = self._ack("ignored")
        self.assertEqual(call[1]["name"], "no_entry_sign")

    def test_off_switch_posts_nothing(self):
        self.cfg.slack_capture_receipts = False
        self.assertEqual(self._ack("new_proposal"), [])

    def test_unknown_kind_posts_nothing(self):
        self.assertEqual(self._ack("banana"), [])

    def test_failure_logs_only_and_never_raises(self):
        events = []
        with mock.patch.object(radar_slack.analytics, "log_event",
                               lambda name, **kw: events.append((name, kw))):
            self._ack("new_proposal",
                      resp={"ok": False, "error": "missing_scope"})
        self.assertEqual(events[0][0], "capture_receipt_failed")
        self.assertEqual(events[0][1]["error"], "missing_scope")

    def test_already_reacted_is_a_success_echo_not_a_failure(self):
        events = []
        with mock.patch.object(radar_slack.analytics, "log_event",
                               lambda name, **kw: events.append(name)):
            self._ack("new_proposal",
                      resp={"ok": False, "error": "already_reacted"})
        self.assertEqual(events, [])

    def _handle(self, decision: dict) -> list:
        raw = json.dumps(decision)
        extractor = lambda prompt: subprocess.CompletedProcess(  # noqa: E731
            args=["fake"], returncode=0, stdout=raw)
        calls = []
        with mock.patch.object(radar_slack, "slack_api",
                               lambda m, t, p=None: calls.append((m, p)) or {"ok": True}):
            radar_slack._handle_self_message(self.msg, "xoxp-t", self.cfg,
                                             extractor=extractor)
        return calls

    def test_self_message_end_to_end_ignore_acks_no_entry(self):
        # ignore decision -> registry untouched, 🚫 receipt on the message
        calls = self._handle({"action": "ignore", "reason": "闲聊"})
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(calls, [("reactions.add",
                                  {"channel": "D123", "timestamp": "1700.42",
                                   "name": "no_entry_sign"})])

    def test_self_message_end_to_end_files_and_acks_inbox(self):
        calls = self._handle({"action": "new_proposal", "title": "记一下",
                              "summary": "记一下", "type": "other",
                              "tier": "T0", "confidence": "high"})
        self.assertEqual(len(registry.load_all()), 1)
        self.assertEqual(calls[0][1]["name"], "inbox_tray")

    def test_apply_result_raising_means_no_receipt(self):
        # unknown outcome must not be acked as filed — the receipt only fires
        # after apply_result returned.
        extractor = lambda prompt: subprocess.CompletedProcess(  # noqa: E731
            args=["fake"], returncode=0,
            stdout=json.dumps({"action": "new_proposal", "title": "t",
                               "summary": "t"}))
        calls = []
        from act.lib import quick_capture
        with mock.patch.object(quick_capture, "apply_result",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(radar_slack, "slack_api",
                               lambda m, t, p=None: calls.append(m) or {"ok": True}):
            radar_slack._handle_self_message(self.msg, "xoxp-t", self.cfg,
                                             extractor=extractor)
        self.assertEqual(calls, [])


# --------------------------------------------------------------------------- #
# §40.3 radar give-up diagnostic card + dedup
# --------------------------------------------------------------------------- #
class GiveUpCardTestCase(unittest.TestCase):
    def setUp(self):
        _clean_registry()
        self.addCleanup(_clean_registry)
        self.addCleanup(self._clean_radar_state)
        self._clean_radar_state()
        self.entry = {"mtime": 1.0, "attempts": 5, "gave_up": True,
                      "last_error": "claude exit 1: boom"}

    @staticmethod
    def _clean_radar_state():
        for name in (radar.MARKER_PATH_NAME, radar.FAILED_QUEUE_NAME):
            p = config.STATE_DIR / name
            if p.exists():
                p.unlink()
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()

    def test_give_up_files_visible_diagnostic_card(self):
        saved = radar.file_give_up_card(Path("/vault/2026-07-10 sync.md"),
                                        self.entry)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, State.DETECTED.value)  # 备选, visible
        self.assertEqual(saved.type, "diagnostic")
        self.assertEqual(saved.title, "有一篇笔记我处理不了：2026-07-10 sync.md")
        self.assertIn("/vault/2026-07-10 sync.md", saved.summary)
        self.assertIn("手动处理", saved.summary)
        self.assertIn("[radar-give-up]", saved.notes)
        self.assertIn("claude exit 1: boom", saved.notes)
        self.assertIn("/vault/2026-07-10 sync.md", saved.notes)
        self.assertEqual(saved.sources[0]["channel"], radar.GIVE_UP_CHANNEL)
        self.assertEqual(saved.sources[0]["ref"], "/vault/2026-07-10 sync.md")

    def test_dedup_by_note_path_never_refiles(self):
        note = Path("/vault/poison.md")
        first = radar.file_give_up_card(note, self.entry)
        self.assertIsNotNone(first)
        # a later give-up round (e.g. after an mtime reset) must not re-file
        again = radar.file_give_up_card(note, dict(self.entry, attempts=5))
        self.assertIsNone(again)
        self.assertEqual(len(registry.load_all()), 1)

    def test_dedup_survives_the_user_trashing_the_card(self):
        note = Path("/vault/poison.md")
        card = radar.file_give_up_card(note, self.entry)
        registry.trash(card, "deleted")
        self.assertIsNone(radar.file_give_up_card(note, self.entry))

    def test_scan_files_the_card_after_the_retry_budget_burns_out(self):
        tmp = tempfile.TemporaryDirectory(prefix="giveup-vault-")
        self.addCleanup(tmp.cleanup)
        raw = Path(tmp.name) / "2 - raw"
        raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{raw.as_posix()}"\n', encoding="utf-8")

        base = 1_700_000_000.0
        bad = raw / "poison.md"
        bad.write_text("BAD note", encoding="utf-8")
        os.utime(bad, (base, base))

        def runner(text):
            if "BAD" in text:
                raise RuntimeError("extract boom")
            return "[]"

        # each pass needs a succeeding sibling, or the all-failed pass is
        # (correctly) judged systemic and charges no retry budget.
        for i in range(radar.FAILED_MAX_ATTEMPTS):
            ok = raw / f"ok-{i}.md"
            ok.write_text("fine", encoding="utf-8")
            os.utime(ok, (base + 10 * (i + 1), base + 10 * (i + 1)))
            radar.scan(runner=runner)

        cards = [r for r in registry.load_all() if r.type == "diagnostic"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].title, "有一篇笔记我处理不了：poison.md")
        # one more pass with a fresh sibling — still exactly one card
        extra = raw / "ok-extra.md"
        extra.write_text("fine", encoding="utf-8")
        os.utime(extra, (base + 100, base + 100))
        radar.scan(runner=runner)
        self.assertEqual(
            len([r for r in registry.load_all() if r.type == "diagnostic"]), 1)


# --------------------------------------------------------------------------- #
# §40.4 weekly-digest failure notifies (manual runs)
# --------------------------------------------------------------------------- #
class DigestFailureNotifyTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.addCleanup(self._cleanup)
        self.tmp = tempfile.TemporaryDirectory(prefix="wd40-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n',
            encoding="utf-8")
        note = self.raw / "2026-07-15 work.md"
        note.write_text("did things", encoding="utf-8")
        patcher = mock.patch.object(weekly_digest.notify, "notify",
                                    return_value=True)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        marker = config.STATE_DIR / weekly_digest.MARKER_PATH_NAME
        if marker.exists():
            marker.unlink()
        if config.REGISTRY_DIR.exists():
            shutil.rmtree(config.REGISTRY_DIR)

    def test_claude_failure_notifies_with_retry_pointer(self):
        def boom(prompt):
            raise RuntimeError("claude down")
        summary = weekly_digest.run(force=True, runner=boom)
        self.assertFalse(summary["ok"])
        self.notify.assert_called_once()
        title, body = self.notify.call_args[0][:2]
        self.assertIn("本周摘要生成失败", title)
        self.assertIn("重试", body)

    def test_unparseable_output_notifies_too(self):
        summary = weekly_digest.run(force=True,
                                    runner=lambda p: "not json at all")
        self.assertFalse(summary["ok"])
        self.notify.assert_called_once()
        title, body = self.notify.call_args[0][:2]
        self.assertIn("本周摘要生成失败", title)
        self.assertIn("无法解析", body)

    def test_scheduled_failure_stays_quiet(self):
        # mirror of the no-data gate: due() keeps returning True after a
        # failed Monday (the marker never advanced), so the hourly launchd
        # re-run would otherwise notify all day long.
        def boom(prompt):
            raise RuntimeError("claude down")
        cfg = config.load_config()
        due_now = _dt.datetime(2026, 7, 13, cfg.weekly_digest_hour + 1)  # Mon
        summary = weekly_digest.run(force=False, runner=boom, now=due_now)
        self.assertFalse(summary["ok"])
        self.notify.assert_not_called()


# --------------------------------------------------------------------------- #
# §40.6 notification batching (fresh proposals only)
# --------------------------------------------------------------------------- #
def _dash(needs_approval=()):
    return {"needs_approval": [dict(i) for i in needs_approval],
            "running": [], "needs_input": [], "review": []}


class BatchNotifyTestCase(unittest.TestCase):
    def test_three_fresh_cards_collapse_to_one(self):
        prev = _dash()
        curr = _dash(needs_approval=[{"id": f"R-{i}", "title": f"卡{i}"}
                                     for i in range(3)])
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(len(msgs), 1)
        title, _body, rid = msgs[0]
        self.assertIn("3", title)
        self.assertEqual(title, notify.msg_new_cards_batch(3)[0])
        self.assertIsNone(rid)   # a batch names no single card

    def test_two_fresh_cards_stay_per_card(self):
        prev = _dash()
        curr = _dash(needs_approval=[{"id": "R-1", "title": "卡一"},
                                     {"id": "R-2", "title": "卡二"}])
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(len(msgs), 2)
        self.assertEqual({m[2] for m in msgs}, {"R-1", "R-2"})

    def test_reraised_stays_per_card_next_to_a_batch(self):
        prev = _dash()
        curr = _dash(needs_approval=(
            [{"id": f"R-{i}", "title": f"卡{i}"} for i in range(3)]
            + [{"id": "R-9", "title": "回锅卡", "reraised": True,
                "reraised_note": "新信息"}]))
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(len(msgs), 2)
        by_rid = {m[2] for m in msgs}
        self.assertIn("R-9", by_rid)   # 回锅 keeps its own notification
        self.assertIn(None, by_rid)    # the 3 fresh ones collapsed


if __name__ == "__main__":
    unittest.main()
