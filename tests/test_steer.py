"""act/lib/steer.py 行为测试——steer relay 纯函数记账（vnext WIRE M2）。

覆盖：入队 fail-closed / 带时间戳 dedup / 队列溢出留痕 / prompt 组装 /
送达记账（环形上限）/ 尝试计数与放弃 / drop_trace 诚实留痕 / 状态投影。
全部纯内存，绝不 spawn 真 claude、绝不落盘（模块本身无 I/O）。
"""
import unittest

from act.lib import steer
from act.lib.registry import Requirement


def _card(**kw):
    base = dict(id="R-900", title="steer test card", status="executing")
    base.update(kw)
    return Requirement.from_dict(base)


class TestEnqueue(unittest.TestCase):
    def test_note_shape_and_bookkeeping(self):
        req = _card()
        note = steer.enqueue_steer(req, "改用 B 方案", ts="2026-08-30T01:00:00Z")
        self.assertIsNotNone(note)
        self.assertEqual(note["class"], "steer")
        self.assertEqual(note["text"], "改用 B 方案")
        self.assertEqual(note["ts"], "2026-08-30T01:00:00Z")
        self.assertTrue(note["key"].startswith("2026-08-30T01:00:00Z|"))
        ex = req.execution
        self.assertEqual(ex["pending_steers"], [note])
        self.assertEqual(ex["steer_queued"], ["2026-08-30T01:00:00Z"])

    def test_ts_defaults_to_now(self):
        req = _card()
        note = steer.enqueue_steer(req, "hello")
        self.assertRegex(note["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_garbage_text_fail_closed(self):
        req = _card()
        for bad in (None, 42, "", "   ", ["list"]):
            self.assertIsNone(steer.enqueue_steer(req, bad, ts="t"))
        self.assertIsNone(req.execution)  # 卡片一个字段都没被碰

    def test_oversize_clipped_keeps_head(self):
        req = _card()
        note = steer.enqueue_steer(req, "x" * 5000, ts="t1")
        self.assertEqual(len(note["text"]), steer.MAX_STEER_CHARS)

    def test_dedup_same_ts_same_text(self):
        req = _card()
        self.assertIsNotNone(steer.enqueue_steer(req, "同一句", ts="t1"))
        self.assertIsNone(steer.enqueue_steer(req, "同一句", ts="t1"))
        self.assertEqual(len(req.execution["pending_steers"]), 1)

    def test_same_text_new_ts_is_new_steer(self):
        # owner 隔十分钟重申同一句话 = 新指令，不是重放——键带时间戳
        req = _card()
        steer.enqueue_steer(req, "快一点", ts="t1")
        self.assertIsNotNone(steer.enqueue_steer(req, "快一点", ts="t2"))
        self.assertEqual(len(req.execution["pending_steers"]), 2)

    def test_dedup_against_delivered_ledger(self):
        # crash-replay：第一跑已 flush 清队，仅查 pending 会二次入队
        req = _card()
        note = steer.enqueue_steer(req, "改标题", ts="t1")
        steer.mark_delivered(req, [note])
        self.assertIsNone(steer.enqueue_steer(req, "改标题", ts="t1"))
        self.assertNotIn("pending_steers", req.execution)

    def test_overflow_evicts_oldest_with_trace(self):
        req = _card()
        for i in range(steer.PENDING_CAP + 1):
            steer.enqueue_steer(req, f"指令 {i}", ts=f"t{i:02d}")
        pend = req.execution["pending_steers"]
        self.assertEqual(len(pend), steer.PENDING_CAP)
        self.assertEqual(pend[0]["text"], "指令 1")     # 最老的被挤出
        self.assertIn("追加指令未送达", req.notes)       # §39 红线：留痕
        self.assertIn("指令 0", req.notes)

    def test_queued_ring_capped(self):
        req = _card()
        for i in range(15):
            steer.enqueue_steer(req, f"n{i}", ts=f"ts{i:02d}")
        self.assertEqual(len(req.execution["steer_queued"]), steer.TS_RING_CAP)
        self.assertEqual(req.execution["steer_queued"][-1], "ts14")


class TestPendingRead(unittest.TestCase):
    def test_tolerates_garbage_entries(self):
        req = _card(execution={"pending_steers": [
            "裸字符串", 42, {"class": "steer"},           # 垃圾条目
            {"text": "  "},                               # 空白文本
            {"class": "steer", "text": "合法", "ts": "t1", "key": "t1|abc"},
            {"text": "缺键条目", "ts": "t2"},              # 缺 key → 现算
        ]})
        pend = steer.pending_steers(req)
        self.assertEqual([n["text"] for n in pend], ["合法", "缺键条目"])
        self.assertEqual(pend[1]["key"], steer.steer_key("缺键条目", "t2"))

    def test_non_list_queue(self):
        req = _card(execution={"pending_steers": "oops"})
        self.assertEqual(steer.pending_steers(req), [])


class TestPrompt(unittest.TestCase):
    def test_owner_update_prefix_and_bullets(self):
        req = _card()
        a = steer.enqueue_steer(req, "先修 bug", ts="t1")
        b = steer.enqueue_steer(req, "再补测试", ts="t2")
        prompt = steer.build_steer_prompt([a, b])
        self.assertTrue(prompt.startswith(steer.STEER_PREFIX))
        self.assertIn("- 先修 bug\n- 再补测试", prompt)
        self.assertIn("not a new task", prompt)   # 明示是转向不是新任务


class TestDelivery(unittest.TestCase):
    def test_mark_delivered_bookkeeping(self):
        req = _card()
        a = steer.enqueue_steer(req, "one", ts="t1")
        b = steer.enqueue_steer(req, "two", ts="t2")
        steer.mark_delivered(req, [a, b], delivered_at="2026-08-30T02:00:00Z")
        ex = req.execution
        self.assertNotIn("pending_steers", ex)
        self.assertNotIn("steer_attempts", ex)
        self.assertEqual(ex["steer_count"], 2)
        self.assertEqual(ex["last_steer_at"], "2026-08-30T02:00:00Z")
        self.assertEqual(ex["steer_delivered"], ["2026-08-30T02:00:00Z"])
        # C-3（M8.3 终裁）：台账环形元素带全文——board 投影 delivered 行的数据源
        self.assertEqual(ex["delivered_steers"], [
            {"key": a["key"], "text": "one", "ts": "t1",
             "delivered_at": "2026-08-30T02:00:00Z"},
            {"key": b["key"], "text": "two", "ts": "t2",
             "delivered_at": "2026-08-30T02:00:00Z"},
        ])

    def test_delivered_ledger_dedups_replay_and_tolerates_bare_keys(self):
        # 旧裸 key 条目容忍（crash 窗口混合形）；同 key 重放不再入队
        req = _card()
        a = steer.enqueue_steer(req, "one", ts="t1")
        steer.mark_delivered(req, [a])
        ex = dict(req.execution)
        ex["delivered_steers"] = ["legacy|key"] + ex["delivered_steers"]
        req.execution = ex
        self.assertIsNone(steer.enqueue_steer(req, "one", ts="t1"))  # replay
        entries = steer.delivered_entries(req)
        self.assertEqual(len(entries), 1)          # 裸 key 条目不进投影
        self.assertEqual(entries[0]["text"], "one")
        self.assertEqual(entries[0]["ts"], "t1")

    def test_mid_flight_enqueue_survives_flush(self):
        # flush 期间另一进程排入的新 steer 必须留队（§44.3 sent-set 判例）
        req = _card()
        a = steer.enqueue_steer(req, "old", ts="t1")
        late = steer.enqueue_steer(req, "late", ts="t2")
        steer.mark_delivered(req, [a])
        self.assertEqual(req.execution["pending_steers"], [late])

    def test_delivered_ledger_ring_cap(self):
        req = _card()
        for i in range(steer.DELIVERED_LEDGER_CAP + 5):
            n = steer.enqueue_steer(req, f"m{i}", ts=f"t{i:03d}")
            steer.mark_delivered(req, [n])
        self.assertEqual(len(req.execution["delivered_steers"]),
                         steer.DELIVERED_LEDGER_CAP)

    def test_attempts_and_give_up(self):
        req = _card()
        steer.enqueue_steer(req, "stuck", ts="t1")
        self.assertFalse(steer.give_up_due(req))
        for want in (1, 2, 3):
            self.assertEqual(steer.record_attempt(req), want)
        self.assertTrue(steer.give_up_due(req))

    def test_attempts_cleared_on_delivery(self):
        req = _card()
        n = steer.enqueue_steer(req, "ok now", ts="t1")
        steer.record_attempt(req)
        steer.mark_delivered(req, [n])
        self.assertFalse(steer.give_up_due(req))
        self.assertNotIn("steer_attempts", req.execution)


class TestDropTrace(unittest.TestCase):
    def test_drop_leaves_trace_and_clears(self):
        req = _card()
        a = steer.enqueue_steer(req, "长指令 " + "x" * 300, ts="t1")
        steer.record_attempt(req)
        tags = steer.drop_trace(req, [a], "3 次注入尝试失败")
        self.assertEqual(len(tags), 1)
        self.assertIn("追加指令未送达", tags[0])
        self.assertIn("3 次注入尝试失败", tags[0])
        self.assertIn("…", tags[0])                     # 超 200 截断标记
        self.assertIn(tags[0], req.notes)               # notes 留痕 = 同一行
        self.assertNotIn("pending_steers", req.execution)
        self.assertNotIn("steer_attempts", req.execution)

    def test_drop_only_named_notes(self):
        req = _card()
        a = steer.enqueue_steer(req, "drop me", ts="t1")
        keep = steer.enqueue_steer(req, "keep me", ts="t2")
        steer.drop_trace(req, [a], "队列已满，被更新的指令挤出")
        self.assertEqual(req.execution["pending_steers"], [keep])


class TestStatus(unittest.TestCase):
    def test_projection_defaults(self):
        st = steer.steer_status(_card())
        self.assertEqual(st, {"steer_pending": 0, "steer_queued": [],
                              "steer_delivered": [], "steer_count": 0,
                              "last_steer_at": None})

    def test_projection_populated_and_dirty_tolerant(self):
        req = _card(execution={
            "pending_steers": [{"class": "steer", "text": "x",
                                "ts": "t1", "key": "t1|k"}],
            "steer_queued": ["t1", None],
            "steer_delivered": "oops",       # 脏数据 → 空列表
            "steer_count": "bad",            # 脏数据 → 0
            "last_steer_at": "t0",
        })
        st = steer.steer_status(req)
        self.assertEqual(st["steer_pending"], 1)
        self.assertEqual(st["steer_queued"], ["t1"])
        self.assertEqual(st["steer_delivered"], [])
        self.assertEqual(st["steer_count"], 0)
        self.assertEqual(st["last_steer_at"], "t0")


if __name__ == "__main__":
    unittest.main()
