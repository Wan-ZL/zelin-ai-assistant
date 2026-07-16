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
- capture receipt chooser (§40.2): apply_result_with_kind outcome ->
  emoji reaction on the captured self-DM message (the additive seam:
  apply_result's public string surface is frozen and delegates); the
  receipt fires only after the registry write returned; off-switch;
  failure is analytics-only and never raises into the capture;
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

    def test_numeric_trashed_at_means_no_deadline(self):
        # docs-review finding (PR #61): actd._parse_iso REJECTS bare numerics
        # (fromisoformat/strptime both fail) so purge_trash never purges such
        # a row — the old _epoch-based parse showed a red countdown for a
        # purge that would never happen.
        row = self._row(trashed_at=1752600000)
        self.assertIsNone(row["purge_at"])

    def test_purge_at_null_exactly_when_purge_trash_would_skip(self):
        # parity pin: _purge_at's null conditions must mirror the parser
        # purge_trash actually uses (actd._parse_iso) — value by value.
        from act import actd
        for value in ("2026-07-01T00:00:00Z", "2026-07-01T08:00:00+08:00",
                      1752600000, "not-a-date", None):
            row = self._row(trashed_at=value)
            purger_parses = actd._parse_iso(value) is not None
            self.assertEqual(
                row["purge_at"] is not None, purger_parses,
                f"drift for trashed_at={value!r}: purge_at="
                f"{row['purge_at']!r} vs purge_trash parses={purger_parses}")

    def test_purge_at_normalizes_offset_timestamps_to_utc(self):
        # +08:00 wall time = 00:00 UTC — purge_trash purges it, so the
        # countdown must show, expressed in the payload's UTC convention.
        row = self._row(trashed_at="2026-07-01T08:00:00+08:00")
        self.assertEqual(row["purge_at"], "2026-08-30T00:00:00Z")


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

    def test_every_seam_kind_has_an_emoji(self):
        # apply_result_with_kind's full vocabulary (= apply_triage's) — a new
        # kind without a mapping would silently drop its receipt.
        for kind in ("proposed", "folded", "follow_up", "reraised", "ignored"):
            self.assertIn(kind, radar_slack._RECEIPT_EMOJI)

    def test_filed_kinds_get_inbox_tray(self):
        for kind in ("proposed", "folded", "follow_up"):
            (call,) = self._ack(kind)
            self.assertEqual(call[0], "reactions.add")
            self.assertEqual(call[1]["name"], "inbox_tray")
            self.assertEqual(call[1]["channel"], "D123")
            self.assertEqual(call[1]["timestamp"], "1700.42")

    def test_ignored_gets_no_entry_and_reraised_gets_hook(self):
        (call,) = self._ack("ignored")
        self.assertEqual(call[1]["name"], "no_entry_sign")
        (call,) = self._ack("reraised")
        self.assertEqual(call[1]["name"], "leftwards_arrow_with_hook")

    def test_off_switch_posts_nothing(self):
        self.cfg.slack_capture_receipts = False
        self.assertEqual(self._ack("proposed"), [])

    def test_unknown_kind_posts_nothing(self):
        self.assertEqual(self._ack("banana"), [])

    def test_failure_logs_only_and_never_raises(self):
        events = []
        with mock.patch.object(radar_slack.analytics, "log_event",
                               lambda name, **kw: events.append((name, kw))):
            self._ack("proposed",
                      resp={"ok": False, "error": "missing_scope"})
        self.assertEqual(events[0][0], "capture_receipt_failed")
        self.assertEqual(events[0][1]["error"], "missing_scope")

    def test_already_reacted_is_a_success_echo_not_a_failure(self):
        events = []
        with mock.patch.object(radar_slack.analytics, "log_event",
                               lambda name, **kw: events.append(name)):
            self._ack("proposed",
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

    def test_apply_raising_means_no_receipt(self):
        # unknown outcome must not be acked as filed — the receipt only fires
        # after the registry write returned.
        extractor = lambda prompt: subprocess.CompletedProcess(  # noqa: E731
            args=["fake"], returncode=0,
            stdout=json.dumps({"action": "new_proposal", "title": "t",
                               "summary": "t"}))
        calls = []
        from act.lib import quick_capture
        with mock.patch.object(quick_capture, "apply_result_with_kind",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(radar_slack, "slack_api",
                               lambda m, t, p=None: calls.append(m) or {"ok": True}):
            radar_slack._handle_self_message(self.msg, "xoxp-t", self.cfg,
                                             extractor=extractor)
        self.assertEqual(calls, [])

    def test_new_proposal_that_reraises_reads_hook_not_inbox(self):
        # review finding (PR #61): a new_proposal decision whose card matches
        # a resolved parent is internally RE-RAISED by merge_or_new — the
        # receipt must read ↩️, not 📥. The outcome now rides
        # registry.merge_or_new_with_kind (additive seam, same pattern).
        from act.lib import quick_capture
        parent = Requirement(id="R-050", title="给 Quinton 开通 PRD 文档编辑权限",
                             status="delivered")
        registry.save(parent)
        kind, saved, _reply = quick_capture.apply_result_with_kind(
            {"action": "new_proposal",
             "title": "给 Quinton 开通 PRD 文档编辑权限",
             "summary": "权限又要开一次", "type": "other", "tier": "T1",
             "cost_estimate_usd": 3}, self.cfg)   # cost = the increment
        self.assertEqual(kind, "reraised")
        self.assertEqual(saved.id, "R-050")       # the ORIGINAL card flipped
        self.assertEqual(registry.load("R-050").status,
                         State.CARD_SENT.value)
        self.assertEqual(radar_slack._RECEIPT_EMOJI[kind],
                         "leftwards_arrow_with_hook")

    def test_seam_reports_follow_up_then_folded_on_resolved_parent(self):
        # the outcome the receipt hinges on is decided INSIDE
        # apply_result_with_kind (reraise_or_followup) — it is not derivable
        # from the decision dict, which is why the seam exists.
        from act.lib import quick_capture
        parent = Requirement(id="R-019", title="给 Quinton 开通 PRD 文档编辑权限",
                             status="delivered")
        registry.save(parent)
        kind, saved, reply = quick_capture.apply_result_with_kind(
            {"action": "relates_to", "req": "R-019",
             "note": "Quinton 已设我为 editor，继续把文档补完"}, self.cfg)
        self.assertEqual(kind, "follow_up")
        self.assertEqual(saved.improvement_of, "R-019")
        self.assertIn("后续卡", reply)          # legacy reply string intact
        kind2, saved2, _reply2 = quick_capture.apply_result_with_kind(
            {"action": "relates_to", "req": "R-019", "note": "再补一句"},
            self.cfg)
        self.assertEqual(kind2, "folded")       # second lands in the open follow-up
        self.assertEqual(saved2.id, saved.id)

    def test_legacy_apply_result_is_a_pure_delegate(self):
        # apply_result's public surface is frozen: same reply string the seam
        # returns, no second write (ignore files nothing, so calling both is
        # safe here).
        from act.lib import quick_capture
        res = {"action": "ignore", "reason": "闲聊"}
        reply = quick_capture.apply_result(res, self.cfg)
        kind, saved, seam_reply = quick_capture.apply_result_with_kind(
            res, self.cfg)
        self.assertEqual(reply, seam_reply)
        self.assertEqual((kind, saved), ("ignored", None))
        self.assertTrue(reply.startswith("先不建卡：闲聊"))


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
        if config.SETTINGS_OVERRIDES_PATH.exists():
            config.SETTINGS_OVERRIDES_PATH.unlink()

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

    def test_en_locale_gets_an_english_card(self):
        # docs-review finding (PR #61): the card copy follows the single UI
        # language switch (§15) like every other v0.40 string.
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"language": "en"}), encoding="utf-8")
        saved = radar.file_give_up_card(Path("/vault/en note.md"), self.entry)
        self.assertEqual(saved.title, "A note I couldn't process: en note.md")
        self.assertIn("still at /vault/en note.md", saved.summary)
        self.assertIn("[radar-give-up] gave up after 5", saved.notes)

    def test_dedup_by_note_path_never_refiles(self):
        note = Path("/vault/poison.md")
        first = radar.file_give_up_card(note, self.entry)
        self.assertIsNotNone(first)
        # a later give-up round (e.g. after an mtime reset) must not re-file
        again = radar.file_give_up_card(note, dict(self.entry, attempts=5))
        self.assertIsNone(again)
        self.assertEqual(len(registry.load_all()), 1)
        # …not even after a UI language switch: dedup identity is the source
        # ref, not the (language-dependent) title.
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"language": "en"}), encoding="utf-8")
        self.assertIsNone(radar.file_give_up_card(note, self.entry))
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

    def test_digest_filed_cards_are_not_reannounced(self):
        # review finding (PR #61): the weekly digest already announced its
        # suggestions by count in its own notification — actd re-pinging them
        # (per-card or batched) was a duplicate every suggestion-bearing
        # Monday. Seam = the row's source channel.
        prev = _dash()
        wd = [{"id": f"R-{i}", "title": f"建议{i}",
               "sources": [{"who": "assistant", "channel": "weekly-digest",
                            "date": "2026-07-13", "quote": "q"}]}
              for i in range(3)]
        curr = _dash(needs_approval=wd + [{"id": "R-9", "title": "雷达卡"}])
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0][2], "R-9")   # only the non-digest card pings

    def test_batch_copy_is_source_neutral(self):
        # actd only sees the board diff — fresh cards may come from any
        # filer, so the batch copy must not claim 雷达.
        title, _body = notify.msg_new_cards_batch(4)
        self.assertNotIn("雷达", title)
        self.assertIn("4", title)


if __name__ == "__main__":
    unittest.main()
