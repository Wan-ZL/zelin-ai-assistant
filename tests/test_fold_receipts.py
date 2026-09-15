"""§44.6 静默并入看板回执（fold_receipts）— CONTRACT §44.6 / §34.1 判例.

2026-08-07 拍板：radar/普通 capture 通道的静默并入保留，但 fold 发生时看板
必须给「已并入 R-xxx」回执；[run] 通道整体退出并入体系（判例在 test_capture）。

隐私红线（P0 review）：回执文件与投影永不携带被并入内容原文——dashboard.json
被 syncd 整包上云同步，capture 原话（可能含密钥/本机路径）不得出机。投影所需
的主卡显示名由 dashboard 投影时从 registry 现查补齐。

§44.6 追记（issue #308）：回执只属于**用户自己投进来的**通道（policy 的 HAND 类
= quick / quick_capture）——雷达 / 每日整理的自动并入一条不写、旧盘面上的也不投影；
同一张卡在 TTL 窗口内的多次并入合成一行带 count；设置里可整体关掉。

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
    """record/load_recent 台账语义：原子落条、TTL、cap、去重、坏文件跳过。"""

    def setUp(self):
        _clear_dirs()

    def test_record_then_load_roundtrip(self):
        now = time.time()
        path = fold_receipts.record("R-001", "quick_capture",
                                    "同一句话又来了一遍", now=now)
        self.assertIsNotNone(path)
        got = fold_receipts.load_recent(now=now)
        self.assertEqual(len(got), 1)
        e = got[0]
        self.assertEqual(e["req"], "R-001")
        self.assertEqual(e["channel"], "quick_capture")
        self.assertEqual(e["at"], int(now))

    def test_receipt_never_stores_folded_text(self):
        # 隐私红线（P0）：note 只进内容键散列——落盘文件与投影里都不得出现原文
        secret = "sk-ant-SECRET-KEY 和 /Users/zelin/private/path"
        path = fold_receipts.record("R-001", "quick_capture", secret)
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("/Users/zelin", raw)
        self.assertNotIn("text", json.loads(raw))
        for e in fold_receipts.load_recent():
            self.assertNotIn("text", e)

    def test_same_content_within_ttl_dedupes_to_one_receipt(self):
        # 内容键去重：radar failed-note 重试队列对同一条目反复 re-fold ——
        # TTL 窗口内同键不重发、id 不变（Swift seen-set 不会重复弹提示）。
        now = time.time()
        p1 = fold_receipts.record("R-001", "quick", "同一条目", now=now)
        p2 = fold_receipts.record("R-001", "quick", "同一条目", now=now + 5)
        self.assertEqual(p1, p2)
        got = fold_receipts.load_recent(now=now + 5)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["at"], int(now))   # 不刷新 at（不续命）

    def test_different_content_or_target_gets_own_receipt(self):
        now = time.time()
        fold_receipts.record("R-001", "quick", "条目甲", now=now)
        fold_receipts.record("R-001", "quick", "条目乙", now=now)
        fold_receipts.record("R-002", "quick", "条目甲", now=now)
        # 台账层三条（内容键各不相同）……
        self.assertEqual(
            len(list(config.FOLD_RECEIPTS_DIR.glob("*.json"))), 3)
        # ……投影层两行（R-001 的两条合成一行，§44.6 追记 issue #308）
        rows = fold_receipts.load_recent(now=now)
        self.assertEqual({(r["req"], r["count"]) for r in rows},
                         {("R-001", 2), ("R-002", 1)})

    def test_same_content_after_ttl_expiry_records_again(self):
        old = time.time() - fold_receipts.TTL_S - 5
        p1 = fold_receipts.record("R-001", "quick", "同一条目", now=old)
        import os
        os.utime(p1, (old, old))    # 让清扫看见真实的过期 mtime
        p2 = fold_receipts.record("R-001", "quick", "同一条目")
        self.assertIsNotNone(p2)
        got = fold_receipts.load_recent()
        self.assertEqual(len(got), 1)   # 旧的被扫、新的可见

    def test_expired_receipts_leave_projection_and_get_swept(self):
        old = time.time() - fold_receipts.TTL_S - 5
        fold_receipts.record("R-001", "quick", "x", now=old)
        # 过期条目不进投影
        self.assertEqual(fold_receipts.load_recent(), [])
        # 下一次 record 顺手清扫过期兄弟（mtime 是真实写入时刻，先补旧）
        for p in config.FOLD_RECEIPTS_DIR.glob("*.json"):
            import os
            os.utime(p, (old, old))
        fold_receipts.record("R-002", "quick", "y")
        stems = {json.loads(p.read_text(encoding="utf-8"))["req"]
                 for p in config.FOLD_RECEIPTS_DIR.glob("*.json")}
        self.assertEqual(stems, {"R-002"})

    def test_projection_caps_and_sorts_newest_first(self):
        now = time.time()
        for i in range(fold_receipts.PROJECTION_CAP + 3):
            fold_receipts.record(f"R-{i:03d}", "quick", "x", now=now + i)
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

    def test_legacy_receipt_with_text_field_projects_without_it(self):
        # 已落盘的旧格式回执（曾含 title/text）向后兼容：条目照常投影，
        # 但多余字段被忽略——原文即便躺在旧盘面上也不再出机。
        config.FOLD_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        legacy = {"id": "legacy1", "req": "R-009", "title": "旧标题",
                  "channel": "quick_capture", "text": "旧原文摘要",
                  "at": int(time.time())}
        (config.FOLD_RECEIPTS_DIR / "legacy1.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        got = fold_receipts.load_recent()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["req"], "R-009")
        self.assertNotIn("text", got[0])
        self.assertNotIn("title", got[0])

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
        # 投影文案素材 = 目标卡显示名（registry 现查），不携带 capture 原文
        self.assertEqual(receipts[0]["title"],
                         dashboard._display_title(entries[0]))
        self.assertNotIn("text", receipts[0])

    def test_receipt_title_empty_when_target_card_gone(self):
        # 目标卡投影前消失（归档/回收）→ title 空串，App 端只报 R-xxx
        fold_receipts.record("R-404", "quick", "目标卡不在了")
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(),
                                         archived=[])
        self.assertEqual(len(dash["fold_receipts"]), 1)
        self.assertEqual(dash["fold_receipts"][0]["title"], "")

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
    """radar 通道的 fold choke point（_fold_into）照常调 record，但不出回执。

    §44.6 追记（issue #308）：「刚才的输入」指用户刚敲进去的 capture——雷达并入
    的是它自己扫到的内容，用户什么都没做，这条提示对使用者没有可执行的意义。
    可见面仍在：目标卡的 §38 折叠记录 + §44.5「已并入×N」章。
    """

    def setUp(self):
        _clear_dirs()

    def _target(self):
        target = Requirement(id=registry.next_id(), title="修好周报管线",
                             status=State.CARD_SENT.value,
                             sources=[{"who": "zelin", "channel": "meeting",
                                       "date": "2026-08-06", "quote": "修好周报管线"}])
        registry.save(target)
        return target

    def test_fold_into_emits_no_receipt_but_keeps_the_fold_note(self):
        target = self._target()
        child = Requirement(id="R-999", title="周报管线又挂了",
                            sources=[{"who": "zelin", "channel": "slack",
                                      "date": "2026-08-07", "quote": "又挂了"}])
        quick_capture._fold_into(target, child, "周报管线又挂了")
        self.assertEqual(fold_receipts.load_recent(), [])
        self.assertEqual(list(config.FOLD_RECEIPTS_DIR.glob("*.json")), [])
        # 并入本身照常发生（回执静默不等于并入静默）
        folded = registry.load(target.id)
        self.assertIn("[radar] 周报管线又挂了", folded.notes or "")

    def test_autonomous_channels_write_nothing_at_all(self):
        # 写入端闸：radar / 每日整理 / 会议 / slack 一律 None，目录里不留文件
        for channel in ("radar", "daily_loop", "meeting", "slack", "", None):
            with self.subTest(channel=channel):
                self.assertIsNone(fold_receipts.record("R-001", channel, "x"))
        self.assertEqual(list(config.FOLD_RECEIPTS_DIR.glob("*.json")), [])

    def test_legacy_radar_file_on_disk_is_not_projected(self):
        # 升级前的 actd 已经写下的自动通道回执：读时再滤一次，不该升级后还弹
        config.FOLD_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.FOLD_RECEIPTS_DIR / "old-radar.json").write_text(
            json.dumps({"id": "old-radar", "req": "R-008", "channel": "radar",
                        "at": int(time.time())}), encoding="utf-8")
        self.assertEqual(fold_receipts.load_recent(), [])


class FoldReceiptGroupingTestCase(unittest.TestCase):
    """§44.6 追记（issue #308）：同一张卡的多次并入合成一行 + count。"""

    def setUp(self):
        _clear_dirs()

    def test_two_folds_into_one_card_make_one_row_with_count_two(self):
        now = time.time()
        fold_receipts.record("R-008", "quick", "第一条", now=now)
        fold_receipts.record("R-008", "quick", "第二条", now=now + 7)
        got = fold_receipts.load_recent(now=now + 7)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["req"], got[0]["count"]), ("R-008", 2))
        # 簇代表 = 最新的那条（id 仍是一条真实存在的回执文件名）
        self.assertEqual(got[0]["at"], int(now + 7))
        self.assertTrue(
            (config.FOLD_RECEIPTS_DIR / (got[0]["id"] + ".json")).exists())

    def test_single_fold_counts_one(self):
        now = time.time()
        fold_receipts.record("R-008", "quick", "只有一条", now=now)
        self.assertEqual(fold_receipts.load_recent(now=now)[0]["count"], 1)

    def test_projection_cap_counts_groups_not_entries(self):
        now = time.time()
        for i in range(fold_receipts.PROJECTION_CAP + 3):
            fold_receipts.record(f"R-{i:03d}", "quick", "甲", now=now + i)
            fold_receipts.record(f"R-{i:03d}", "quick", "乙", now=now + i)
        got = fold_receipts.load_recent(now=now + 30)
        self.assertEqual(len(got), fold_receipts.PROJECTION_CAP)
        self.assertEqual({e["count"] for e in got}, {2})


class FoldReceiptSwitchTestCase(unittest.TestCase):
    """§44.6 追记（issue #308）：设置 · 通知「静默并入回执」整体关掉。"""

    def setUp(self):
        _clear_dirs()

    def test_toggle_off_empties_the_projection_but_keeps_the_key(self):
        fold_receipts.record("R-007", "quick", "并进来的一句话")
        cfg = config.Config()
        self.assertEqual(len(dashboard._fold_receipts(cfg)), 1)
        cfg.fold_receipt_notices = False
        self.assertEqual(dashboard._fold_receipts(cfg), [])
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=cfg,
                                         archived=[])
        self.assertEqual(dash["fold_receipts"], [])       # 键恒在、列为空
        # 台账本身不受影响（开关是投影面的，不是写入端的）
        self.assertEqual(len(fold_receipts.load_recent()), 1)

    def test_default_is_on_and_no_cfg_means_on(self):
        self.assertTrue(config.Config().fold_receipt_notices)
        fold_receipts.record("R-007", "quick", "并进来的一句话")
        self.assertEqual(len(dashboard._fold_receipts()), 1)


class SelfDMFoldReceiptTestCase(unittest.TestCase):
    """self-DM 的 relates_to 备注折叠也发回执（P1 review：与其他 fold 点同口径）。"""

    def setUp(self):
        _clear_dirs()

    def test_relates_to_note_fold_emits_receipt(self):
        target = Requirement(id=registry.next_id(), title="修好周报管线",
                             status=State.CARD_SENT.value,
                             sources=[{"who": "zelin", "channel": "quick",
                                       "date": "2026-08-06", "quote": "修好周报管线"}])
        registry.save(target)
        res = {"action": "relates_to", "req": target.id,
               "note": "补充：管线卡在导出那一步", "_text": "补充：管线卡在导出那一步"}
        kind, saved, _reply = quick_capture.apply_result_with_kind(
            res, config.Config())
        self.assertEqual(kind, "folded")
        got = fold_receipts.load_recent()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["req"], target.id)
        self.assertEqual(got[0]["channel"], "quick")


if __name__ == "__main__":
    unittest.main()
