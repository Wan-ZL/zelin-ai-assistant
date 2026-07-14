"""radar.scan — Obsidian extraction loop against a tmpdir vault (P1-12).

The injectable ``runner`` replaces the headless ``claude -p`` call, so no
subprocess can fire. Pinned here:

- strict-JSON output -> requirement reconciled through merge_or_new, hard +
  deadline routes straight to card_sent, marker advances to the note's mtime;
- 水位语义 v2: a claude failure or unparseable output sends the note to the
  state/radar_failed.json retry queue (marker advances past it — a failed
  note neither pins later notes into re-extraction nor gets lost behind a
  same-mtime success); retries stop after radar.FAILED_MAX_ATTEMPTS with a
  visible trace, and the re-scan after recovery is idempotent (no duplicate
  cards, no mention inflation);
- a valid ``[]`` is NOT a failure — the marker advances normally;
- notes at-or-before the marker are never re-read (unless queued for retry);
- file-level hazards (non-UTF-8 bytes, dirs named *.md, dangling symlinks)
  never crash a pass.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar
from act.lib import config, registry

BASE = 1_760_000_000.0  # fixed epoch — deterministic mtimes


def _item(title, hardness="soft", deadline=None, quote="do the thing"):
    return {"title": title, "type": "report", "tier": "T1",
            "hardness": hardness, "deadline": deadline,
            "cost_estimate_usd": None, "quote": quote}


class RadarScanBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n', encoding="utf-8")

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        for p in (config.STATE_DIR / radar.MARKER_PATH_NAME,
                  config.STATE_DIR / radar.FAILED_QUEUE_NAME):
            if p.exists():
                p.unlink()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _note(self, name, text, mtime):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p


# --------------------------------------------------------------------------- #
# happy path — extraction -> merge_or_new -> marker
# --------------------------------------------------------------------------- #
class ExtractionTestCase(RadarScanBase):
    def test_hard_deadline_item_becomes_card_and_marker_advances(self):
        note = self._note("2026-07-08 weekly sync.md",
                          "Boss: ship the Q3 report by July 20", BASE)
        runner = lambda text: json.dumps(  # noqa: E731
            [_item("Ship the Q3 quarterly report", hardness="hard",
                   deadline="2026-07-20", quote="ship the Q3 report by July 20")])
        summary = radar.scan(runner=runner)
        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(summary["reconciled"], 1)
        self.assertEqual(summary["cards"], 1)  # hard + deadline = high confidence

        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        req = reqs[0]
        self.assertEqual(req.status, "card_sent")
        self.assertEqual(req.deadline, "2026-07-20")
        src = req.sources[0]
        self.assertEqual(src["channel"], "meeting")
        self.assertEqual(src["who"], "manager")
        self.assertEqual(src["date"], "2026-07-08")  # from the filename
        self.assertEqual(src["ref"], str(note))

        self.assertEqual(radar._read_marker(), BASE)
        # nothing new -> second scan touches no file
        second = radar.scan(runner=lambda t: self.fail("re-read a scanned note"))
        self.assertEqual(second["files_scanned"], 0)

    def test_soft_item_lands_as_detected_debt(self):
        self._note("2026-07-08 note.md", "maybe tidy the wiki", BASE)
        summary = radar.scan(runner=lambda t: json.dumps([_item("Tidy the wiki")]))
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all()[0].status, "detected")

    def test_null_deadline_string_is_not_high_confidence(self):
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: json.dumps(
            [_item("Do the hard thing", hardness="hard", deadline="null")]))
        self.assertEqual(summary["cards"], 0)
        req = registry.load_all()[0]
        self.assertIsNone(req.deadline)
        self.assertEqual(req.status, "detected")

    def test_item_without_title_is_ignored(self):
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: json.dumps([{"quote": "no title"}]))
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(summary["reconciled"], 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(radar._read_marker(), BASE)  # processed, just empty

    def test_fenced_json_is_parsed(self):
        self._note("n.md", "x", BASE)
        runner = lambda t: "```json\n" + json.dumps([_item("Fenced item")]) + "\n```"  # noqa: E731
        summary = radar.scan(runner=runner)
        self.assertEqual(summary["reconciled"], 1)


# --------------------------------------------------------------------------- #
# watermark semantics v2 — failures go to the retry queue, never lose a note
# --------------------------------------------------------------------------- #
class WatermarkTestCase(RadarScanBase):
    def _queue(self):
        return radar._load_failed_queue()

    def test_malformed_output_queues_note_for_retry(self):
        note_a = self._note("a AAA.md", "AAA content", BASE)
        self._note("b BBB.md", "BBB content", BASE + 10)

        def flaky(text):
            if "AAA" in text:
                return "Sorry, I can't produce JSON for that."
            return json.dumps([_item("Requirement from note B")])

        summary = radar.scan(runner=flaky)
        self.assertEqual(summary["files_scanned"], 2)  # scan survives the bad note
        self.assertEqual(summary["reconciled"], 1)     # B still processed
        self.assertTrue(any("unparseable extraction on a AAA.md" in s
                            for s in summary["skipped"]))
        # v2: marker advances past BOTH notes; A is owned by the retry queue
        self.assertEqual(radar._read_marker(), BASE + 10)
        self.assertIn(str(note_a), self._queue())

        # recovery scan: ONLY A retried (B is not re-extracted — no re-burn)
        def fixed(text):
            if "BBB" in text:
                self.fail("re-extracted a successfully processed note")
            return "[]"

        second = radar.scan(runner=fixed)
        self.assertEqual(second["files_scanned"], 1)
        self.assertEqual(radar._read_marker(), BASE + 10)
        self.assertEqual(self._queue(), {})            # case closed
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)                 # no duplicate card
        self.assertEqual(reqs[0].repeated_mentions, 1)

    def test_runner_exception_queues_note_and_scan_survives(self):
        note_a = self._note("a AAA.md", "AAA content", BASE)
        self._note("b BBB.md", "BBB content", BASE + 10)

        def flaky(text):
            if "AAA" in text:
                raise RuntimeError("claude exit 1: transient")
            return json.dumps([_item("Requirement from note B")])

        summary = radar.scan(runner=flaky)
        self.assertEqual(summary["reconciled"], 1)
        self.assertTrue(any("claude -p failed on a AAA.md" in s
                            for s in summary["skipped"]))
        entry = self._queue()[str(note_a)]
        self.assertEqual(entry["attempts"], 1)
        self.assertFalse(entry["gave_up"])

    def test_failure_does_not_reburn_later_notes(self):
        # 旧语义的矛盾②：失败 note 钉死 marker，其后的 C 每轮被重新提取。
        self._note("a AAA.md", "AAA content", BASE)
        self._note("b BBB.md", "BBB content", BASE + 10)
        self._note("c CCC.md", "CCC content", BASE + 20)
        calls = []

        def flaky(text):
            calls.append(text)
            if "BBB" in text:
                raise RuntimeError("boom")
            return "[]"

        radar.scan(runner=flaky)
        self.assertEqual(radar._read_marker(), BASE + 20)  # advances past B too
        calls.clear()
        radar.scan(runner=flaky)                           # retry pass
        self.assertEqual(len(calls), 1)                    # only B re-extracted
        self.assertIn("BBB", calls[0])

    def test_same_mtime_failure_is_retried_not_lost(self):
        # 旧语义的矛盾①：成功的 A 把 marker 推到共享 mtime，失败的 B 从此
        # ``mtime <= marker`` 永久跳过 —— 内容静默丢失。
        self._note("a AAA.md", "AAA content", BASE)
        note_b = self._note("b BBB.md", "BBB content", BASE)  # same mtime

        def flaky(text):
            if "BBB" in text:
                raise RuntimeError("claude 500")
            return "[]"

        first = radar.scan(runner=flaky)
        self.assertEqual(first["files_scanned"], 2)
        self.assertIn(str(note_b), self._queue())

        second = radar.scan(runner=lambda t: json.dumps(
            [_item("Requirement from note B", hardness="hard",
                   deadline="2026-07-20")]))
        self.assertEqual(second["files_scanned"], 1)       # B retried
        self.assertEqual(second["cards"], 1)               # ...and not lost
        self.assertEqual(self._queue(), {})

    def test_retry_gives_up_after_max_attempts_with_trace(self):
        # 陪跑成功 note：部分失败（≠systemic 全军覆没）才 charge 重试额度
        note = self._note("poison.md", "poison content", BASE)
        calls = []

        def runner(text):
            calls.append(text)
            if "poison" in text:
                raise RuntimeError("permanent failure")
            return "[]"

        for attempt in range(1, radar.FAILED_MAX_ATTEMPTS + 1):
            self._note(f"ok-{attempt}.md", f"ok content {attempt}",
                       BASE + 10 * attempt)
            summary = radar.scan(runner=runner)
            self.assertEqual(summary["files_scanned"], 2, f"attempt {attempt}")
        self.assertTrue(any("giving up on poison.md" in s
                            for s in summary["skipped"]))
        entry = self._queue()[str(note)]
        self.assertTrue(entry["gave_up"])
        self.assertEqual(entry["attempts"], radar.FAILED_MAX_ATTEMPTS)

        # 放弃后不再烧 claude —— 但案底留在台账里（不是静默消失）
        calls.clear()
        self._note("ok-final.md", "ok final content", BASE + 1000)
        after = radar.scan(runner=runner)
        self.assertEqual(after["files_scanned"], 1)
        self.assertFalse(any("poison" in c for c in calls))
        self.assertIn(str(note), self._queue())

    def test_editing_a_given_up_note_resets_its_attempts(self):
        note = self._note("poison.md", "poison content", BASE)

        def runner(text):
            if "poison" in text:
                raise RuntimeError("x")
            return "[]"

        for i in range(radar.FAILED_MAX_ATTEMPTS):
            # 陪跑成功 note：部分失败才 charge 额度（systemic 不扣）
            self._note(f"ok-{i}.md", f"ok {i}", BASE + 10 * (i + 1))
            radar.scan(runner=runner)
        self.assertTrue(self._queue()[str(note)]["gave_up"])

        # 用户改了 note（新 mtime）→ 重新扫描 + 重试额度从头计
        self._note("poison.md", "fixed content", BASE + 1000)
        summary = radar.scan(runner=lambda t: "[]")
        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(self._queue(), {})

    def test_deleted_note_case_is_closed(self):
        note = self._note("gone.md", "gone content", BASE)
        self._note("ok.md", "ok content", BASE + 10)  # 陪跑：≠systemic

        def runner(text):
            if "gone" in text:
                raise RuntimeError("x")
            return "[]"

        radar.scan(runner=runner)
        self.assertIn(str(note), self._queue())
        note.unlink()
        radar.scan(runner=lambda t: self.fail("nothing left to scan"))
        self.assertEqual(self._queue(), {})

    def test_systemic_outage_never_burns_retry_budget(self):
        # audit review 2026-07-14 blocker 回归：claude/key/网络整体故障
        # （本轮全军覆没）不 charge 重试额度、不推 marker——故障修复后整个
        # 积压自动重扫，绝不出现 gave_up 的"死积压"。
        self._note("a.md", "AAA content", BASE)
        self._note("b.md", "BBB content", BASE + 10)
        marker_before = radar._read_marker()
        for _ in range(radar.FAILED_MAX_ATTEMPTS + 2):  # 远超单 note 额度
            s = radar.scan(runner=lambda t: (_ for _ in ()).throw(
                RuntimeError("claude exit 1: EPERM")))
            self.assertTrue(any("systemic extraction failure" in x
                                for x in s["skipped"]))
        self.assertEqual(self._queue(), {})              # 没有人被扣额度
        self.assertEqual(radar._read_marker(), marker_before)  # marker 钉住

        # 通道恢复 → 整个积压一轮扫完
        ok = radar.scan(runner=lambda t: "[]")
        self.assertEqual(ok["files_scanned"], 2)
        self.assertEqual(radar._read_marker(), BASE + 10)

    def test_valid_empty_array_advances_marker(self):
        # "[]" is the prompt's own no-requirements answer — NOT a failure
        self._note("quiet note.md", "nothing new here", BASE)
        summary = radar.scan(runner=lambda t: "[]")
        self.assertEqual(summary["extracted"], 0)
        self.assertEqual(summary["skipped"], [])
        self.assertEqual(radar._read_marker(), BASE)
        self.assertEqual(self._queue(), {})

    def test_notes_at_or_before_marker_are_skipped(self):
        self._note("old.md", "already seen", BASE)
        radar._write_marker(BASE)
        summary = radar.scan(
            runner=lambda t: self.fail("re-read a note behind the marker"))
        self.assertEqual(summary["files_scanned"], 0)


# --------------------------------------------------------------------------- #
# file-level hazards — a bad path must never crash a pass
# --------------------------------------------------------------------------- #
class FileHazardTestCase(RadarScanBase):
    def test_non_utf8_note_is_queued_not_fatal(self):
        bad = self.raw / "binary.md"
        bad.write_bytes(b"\xff\xfe not utf8")
        os.utime(bad, (BASE, BASE))
        self._note("good.md", "fine", BASE + 10)
        summary = radar.scan(runner=lambda t: "[]")   # 旧代码在 read_text 崩掉
        self.assertEqual(summary["files_scanned"], 2)
        self.assertTrue(any("unreadable note binary.md" in s
                            for s in summary["skipped"]))
        self.assertEqual(radar._read_marker(), BASE + 10)  # good.md 未被拖死
        self.assertIn(str(bad), radar._load_failed_queue())

    def test_dangling_symlink_is_filtered_not_fatal(self):
        (self.raw / "dead.md").symlink_to(self.raw / "no-such-target.md")
        self._note("good.md", "fine", BASE)
        summary = radar.scan(runner=lambda t: "[]")   # 旧代码在 sorted 的 stat 崩掉
        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(radar._read_marker(), BASE)

    def test_directory_named_md_is_filtered_not_fatal(self):
        folder = self.raw / "folder.md"
        folder.mkdir()
        os.utime(folder, (BASE, BASE))
        self._note("later.md", "fine", BASE + 10)
        first = radar.scan(runner=lambda t: "[]")
        self.assertEqual(first["files_scanned"], 1)
        self.assertEqual(first["skipped"], [])        # 目录不是 note，不算失败
        self.assertEqual(radar._read_marker(), BASE + 10)
        # 第二轮不再重扫 later.md（旧代码每轮 halted + 重烧）
        second = radar.scan(runner=lambda t: self.fail("re-burned later.md"))
        self.assertEqual(second["files_scanned"], 0)


# --------------------------------------------------------------------------- #
# preflight guards
# --------------------------------------------------------------------------- #
class PreflightTestCase(RadarScanBase):
    def test_feature_flag_off_skips_scan(self):
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n'
            "features:\n  obsidian_radar: false\n", encoding="utf-8")
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: self.fail("scanned while off"))
        self.assertIn("features.obsidian_radar is off", summary["skipped"])
        self.assertEqual(summary["files_scanned"], 0)

    def test_missing_vault_dir_is_reported_not_fatal(self):
        gone = Path(self.tmp.name) / "no-such-dir"
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{gone.as_posix()}"\n', encoding="utf-8")
        summary = radar.scan(runner=lambda t: self.fail("scanned a missing vault"))
        self.assertTrue(any(s.startswith("obsidian_raw not found")
                            for s in summary["skipped"]))


# --------------------------------------------------------------------------- #
# item hygiene — LLM 输出的类型不可信，脏字段不许崩 pass / 骗过发卡门
# --------------------------------------------------------------------------- #
class ItemHygieneTestCase(RadarScanBase):
    def test_non_string_title_item_is_skipped_not_fatal(self):
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: '[{"title": 123}]')  # 旧代码 .strip() 崩
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(summary["reconciled"], 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(radar._read_marker(), BASE)  # processed, just empty

    def test_string_false_urgent_parks_in_backlog(self):
        # `is not False` 恒等比较曾把字符串 "false" 当 urgent 发进提案列
        self._note("n.md", "x", BASE)
        item = _item("Do Y", hardness="hard", deadline="2026-07-20")
        item["urgent"] = "false"
        summary = radar.scan(runner=lambda t: json.dumps([item]))
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all()[0].status, "detected")

    def test_junk_deadline_does_not_pass_the_card_gate(self):
        self._note("n.md", "x", BASE)
        raw = json.dumps([{"title": "X", "hardness": "hard",
                           "deadline": True, "urgent": True}])
        summary = radar.scan(runner=lambda t: raw)
        req = registry.load_all()[0]
        self.assertIsNone(req.deadline)               # bool 不是日期
        self.assertEqual(req.status, "detected")      # 没资格进提案列
        self.assertEqual(summary["cards"], 0)

    def test_unparseable_date_string_is_dropped(self):
        self._note("n.md", "x", BASE)
        radar.scan(runner=lambda t: json.dumps(
            [_item("X", hardness="hard", deadline="2026-13-99")]))
        self.assertIsNone(registry.load_all()[0].deadline)

    def test_title_is_truncated_to_80(self):
        self._note("n.md", "x", BASE)
        radar.scan(runner=lambda t: json.dumps([_item("X" * 8000)]))
        self.assertLessEqual(len(registry.load_all()[0].title), 80)

    def test_low_confidence_downgrade_is_not_counted_as_card(self):
        # apply_triage 内部降级进备选后，summary["cards"] 不得再拿 stale hc 虚报
        self._note("n.md", "x", BASE)
        triager = lambda p: subprocess.CompletedProcess(  # noqa: E731
            [], 0, stdout='{"action": "new_proposal", "confidence": "low"}')
        summary = radar.scan(
            runner=lambda t: json.dumps(
                [_item("do X", hardness="hard", deadline="2026-07-20")]),
            triager=triager)
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all()[0].status, "detected")

    def test_act_now_fold_promotes_detected_card(self):
        # hc 候选 relates_to 一张备选卡时，卡必须提进提案列（不许把硬 deadline
        # 的紧急诉求折进 backlog 里不可见）
        parked = registry.upsert(registry.Requirement(
            id=registry.next_id(), title="build eval harness", type="code",
            tier="T1", status="detected", hardness="soft"))
        self._note("n.md", "x", BASE)
        triager = lambda p, i=parked.id: subprocess.CompletedProcess(  # noqa: E731
            [], 0, stdout='{"action": "relates_to", "req": "%s", "note": "hard ddl"}' % i)
        radar.scan(
            runner=lambda t: json.dumps(
                [_item("do X", hardness="hard", deadline="2026-07-20")]),
            triager=triager)
        self.assertEqual(registry.load(parked.id).status, "card_sent")

    def test_filing_exception_queues_note_not_fatal(self):
        from act.lib import quick_capture
        orig = quick_capture.apply_triage
        self.addCleanup(lambda: setattr(quick_capture, "apply_triage", orig))

        def boom(*a, **k):
            raise RuntimeError("registry down")

        quick_capture.apply_triage = boom
        note = self._note("n.md", "x", BASE)
        # 陪跑 note 提取出 0 项（不触发 filing）→ 计成功：部分失败 ≠ systemic
        self._note("ok.md", "quiet", BASE + 10)
        summary = radar.scan(
            runner=lambda t: json.dumps([_item("T")]) if t and "x" in t else "[]")
        self.assertTrue(any("filing failed on n.md" in s
                            for s in summary["skipped"]))
        self.assertIn(str(note), radar._load_failed_queue())


# --------------------------------------------------------------------------- #
# _parse_extraction — [] (valid empty) vs None (malformed, retry the note)
# --------------------------------------------------------------------------- #
class ParseExtractionTestCase(unittest.TestCase):
    def test_strict_array(self):
        self.assertEqual(radar._parse_extraction('[{"title": "x"}]'),
                         [{"title": "x"}])

    def test_valid_empty_array(self):
        self.assertEqual(radar._parse_extraction("[]"), [])

    def test_fenced_array(self):
        self.assertEqual(radar._parse_extraction('```json\n[{"title": "x"}]\n```'),
                         [{"title": "x"}])

    def test_array_embedded_in_prose(self):
        raw = 'Here you go:\n[{"title": "x"}]\nHope that helps!'
        self.assertEqual(radar._parse_extraction(raw), [{"title": "x"}])

    def test_non_dict_items_are_filtered(self):
        self.assertEqual(radar._parse_extraction('["junk", {"title": "x"}]'),
                         [{"title": "x"}])

    def test_all_non_dict_array_is_malformed_not_valid_empty(self):
        # ["do X by friday"] 按空处理会静默丢需求 —— 必须走重试
        self.assertIsNone(radar._parse_extraction('["do X by friday", "review Y"]'))

    def test_bracketed_prose_before_array_is_parsed(self):
        raw = 'Requirements [from the meeting note]:\n[{"title": "do X"}]'
        self.assertEqual(radar._parse_extraction(raw), [{"title": "do X"}])

    def test_footnote_after_array_is_parsed(self):
        raw = '[{"title": "do X"}]\nNote: [1] deadline unclear'
        self.assertEqual(radar._parse_extraction(raw), [{"title": "do X"}])

    def test_empty_output_is_malformed(self):
        self.assertIsNone(radar._parse_extraction(""))
        self.assertIsNone(radar._parse_extraction("   \n"))

    def test_prose_without_array_is_malformed(self):
        self.assertIsNone(radar._parse_extraction("I could not find any."))

    def test_non_array_json_is_malformed(self):
        self.assertIsNone(radar._parse_extraction('{"title": "x"}'))


if __name__ == "__main__":
    unittest.main()
