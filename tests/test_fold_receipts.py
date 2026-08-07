"""§44.6 静默并入看板回执（fold_receipts）— CONTRACT §44.6 / §34.1 判例.

2026-08-07 拍板：radar/普通 capture 通道的静默并入保留，但 fold 发生时看板
必须给「已并入 R-xxx」回执；[run] 通道整体退出并入体系（判例在 test_capture）。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM.
"""
import json
import time
import unittest
import uuid

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import actd
from act.lib import config, dashboard, fold_receipts, quick_capture, registry
from act.lib.registry import Requirement, State


def _clear_dirs():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()
    if config.FOLD_RECEIPTS_DIR.exists():
        for p in config.FOLD_RECEIPTS_DIR.glob("*"):
            p.unlink()


class FoldReceiptsLedgerTestCase(unittest.TestCase):
    """record/load_recent 台账语义：原子落条、TTL、cap、坏文件跳过。"""

    def setUp(self):
        _clear_dirs()

    def test_record_then_load_roundtrip(self):
        now = time.time()
        path = fold_receipts.record("R-001", "  修 bug   的卡  ", "quick_capture",
                                    "同一句话又来了一遍", now=now)
        self.assertIsNotNone(path)
        got = fold_receipts.load_recent(now=now)
        self.assertEqual(len(got), 1)
        e = got[0]
        self.assertEqual(e["req"], "R-001")
        self.assertEqual(e["title"], "修 bug 的卡")     # 空白折叠
        self.assertEqual(e["channel"], "quick_capture")
        self.assertEqual(e["text"], "同一句话又来了一遍")
        self.assertEqual(e["at"], int(now))

    def test_expired_receipts_leave_projection_and_get_swept(self):
        old = time.time() - fold_receipts.TTL_S - 5
        fold_receipts.record("R-001", "旧回执", "radar", "x", now=old)
        # 过期条目不进投影
        self.assertEqual(fold_receipts.load_recent(), [])
        # 下一次 record 顺手清扫过期兄弟（mtime 是真实写入时刻，先补旧）
        for p in config.FOLD_RECEIPTS_DIR.glob("*.json"):
            import os
            os.utime(p, (old, old))
        fold_receipts.record("R-002", "新回执", "radar", "y")
        stems = {json.loads(p.read_text(encoding="utf-8"))["req"]
                 for p in config.FOLD_RECEIPTS_DIR.glob("*.json")}
        self.assertEqual(stems, {"R-002"})

    def test_projection_caps_and_sorts_newest_first(self):
        now = time.time()
        for i in range(fold_receipts.PROJECTION_CAP + 3):
            fold_receipts.record(f"R-{i:03d}", "t", "radar", "x", now=now + i)
        got = fold_receipts.load_recent(now=now + 20)
        self.assertEqual(len(got), fold_receipts.PROJECTION_CAP)
        ats = [e["at"] for e in got]
        self.assertEqual(ats, sorted(ats, reverse=True))

    def test_corrupt_and_reqless_files_are_skipped(self):
        config.FOLD_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.FOLD_RECEIPTS_DIR / "bad.json").write_text("{not json",
                                                           encoding="utf-8")
        (config.FOLD_RECEIPTS_DIR / "list.json").write_text("[1,2]",
                                                            encoding="utf-8")
        (config.FOLD_RECEIPTS_DIR / "noreq.json").write_text(
            json.dumps({"id": "x", "at": int(time.time())}), encoding="utf-8")
        self.assertEqual(fold_receipts.load_recent(), [])   # never raises

    def test_dashboard_carries_fold_receipts_key_even_when_empty(self):
        # add-only 顶层键恒在（空 list，不是缺键）——Swift decodeIfPresent 兼容
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(),
                                         archived=[])
        self.assertEqual(dash["fold_receipts"], [])


class CaptureFoldReceiptTestCase(unittest.TestCase):
    """§44.6 判例：普通 capture 被静默并入已有卡 → 看板回执投影出现。"""

    def setUp(self):
        _clear_dirs()

    def _write_capture(self, text):
        payload = {"action": "capture", "text": text,
                   "ts": "2026-08-07T00:00:00Z"}
        (config.INBOX_DIR / f"capture-{uuid.uuid4()}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_plain_capture_fold_emits_board_receipt(self):
        text = "把周报数据整理成一页摘要发出去"
        existing = Requirement(id=registry.next_id(), title=text,
                               status=State.CARD_SENT.value,
                               sources=[{"who": "zelin", "channel": "quick_capture",
                                         "date": "2026-08-06", "quote": text}])
        registry.save(existing)

        self._write_capture(text)
        actd.process_inbox()
        # 没建新卡（静默并入保留）……
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, existing.id)
        # ……但看板回执必须出现（8-07 事故判例的另一半：并入不许无声）
        dash = dashboard.build_dashboard(agents=[], cfg=config.Config(),
                                         archived=[])
        receipts = dash["fold_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["req"], existing.id)
        self.assertEqual(receipts[0]["channel"], "quick_capture")
        self.assertEqual(receipts[0]["text"], text)

    def test_plain_capture_new_card_emits_no_receipt(self):
        self._write_capture("一句全新的话不产生回执")
        actd.process_inbox()
        self.assertEqual(fold_receipts.load_recent(), [])

    def test_run_capture_never_emits_fold_receipt(self):
        # §34.1: [run] 不判重并入 → 也永远没有并入回执（新卡本身就是回执）
        text = "运行框输入撞标题也不并入"
        registry.save(Requirement(
            id=registry.next_id(), title=text, status=State.EXECUTING.value,
            execution={"session_id": "live0001"},
            sources=[{"who": "zelin", "channel": "quick_capture",
                      "date": "2026-08-06", "quote": text}]))
        payload = {"action": "capture", "text": text, "mode": "run",
                   "ts": "2026-08-07T00:00:00Z"}
        (config.INBOX_DIR / f"capture-{uuid.uuid4()}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        actd.process_inbox()
        self.assertEqual(fold_receipts.load_recent(), [])
        self.assertEqual(
            len([r for r in registry.load_all() if r.title == text]), 2)


class RadarFoldReceiptTestCase(unittest.TestCase):
    """radar 通道的 fold choke point（_fold_into）也留回执。"""

    def setUp(self):
        _clear_dirs()

    def test_fold_into_emits_receipt(self):
        target = Requirement(id=registry.next_id(), title="修好周报管线",
                             status=State.CARD_SENT.value,
                             sources=[{"who": "zelin", "channel": "meeting",
                                       "date": "2026-08-06", "quote": "修好周报管线"}])
        registry.save(target)
        child = Requirement(id="R-999", title="周报管线又挂了",
                            sources=[{"who": "zelin", "channel": "slack",
                                      "date": "2026-08-07", "quote": "又挂了"}])
        quick_capture._fold_into(target, child, "周报管线又挂了")
        got = fold_receipts.load_recent()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["req"], target.id)
        self.assertEqual(got[0]["channel"], "radar")
        self.assertEqual(got[0]["text"], "周报管线又挂了")


if __name__ == "__main__":
    unittest.main()
