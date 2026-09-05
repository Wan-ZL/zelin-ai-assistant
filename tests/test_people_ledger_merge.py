"""§17（issue #23）重点人物账本 — 滚动合并：去重、完成标记、消毒、留存帽、渲染。

「按人滚动账本而不是每篇笔记一个文件」的机制半边：模型输出逐字段消毒
（坏 direction / 空 text / assistant 与 system 发言一律丢）；新条目按归一化 text
或 quote 对既有 open 条目去重（同批内也去重）；``done`` 里的 id 只关 open 条目；
已完成条目只留最近 DONE_RETENTION 条；渲染稿三段 + 落点守卫。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config
from act.lib import people_ledger_store as store


def person(handle="arash.k"):
    return store.Person(handle, store.tokens_for(handle))


class CleanItemTestCase(unittest.TestCase):
    def test_good_item_is_trimmed_and_normalized(self):
        it = store.clean_item({"direction": " Owner_Owes ", "text": "send  the\nreport", "quote": "I'll send it",
                               "speaker": "owner"})
        self.assertEqual(it, {"direction": "owner_owes", "text": "send the report", "quote": "I'll send it"})

    def test_bad_shapes_are_dropped(self):
        for bad in (None, "x", 3, {"direction": "nope", "text": "t"}, {"direction": "owner_owes", "text": ""},
                    {"direction": "person_owes", "text": "t", "speaker": "assistant"},
                    {"direction": "person_owes", "text": "t", "speaker": "System"}):
            with self.subTest(item=bad):
                self.assertIsNone(store.clean_item(bad))

    def test_long_fields_are_capped(self):
        it = store.clean_item({"direction": "owner_owes", "text": "x" * 1000, "quote": "q" * 1000})
        self.assertEqual((len(it["text"]), len(it["quote"])), (store.TEXT_MAX, store.QUOTE_MAX))

    def test_done_ids_filtered_to_item_id_shape(self):
        self.assertEqual(store.clean_done_ids(["L-1", "x", 3, "L-22", None, "L-"]), ["L-1", "L-22"])
        self.assertEqual(store.clean_done_ids("L-1"), [])


class MergeTestCase(unittest.TestCase):
    def setUp(self):
        self.doc = store.empty_ledger(person())

    def _new(self, text, direction="owner_owes", quote=""):
        return {"direction": direction, "text": text, "quote": quote}

    def test_add_then_dedupe_by_text_and_quote(self):
        added, closed = store.merge(self.doc, [self._new("Send the report", quote="I'll send the report")],
                                    [], "n1.md", "2026-09-01")
        self.assertEqual((added, closed), (1, 0))
        self.assertEqual(self.doc["items"][0]["id"], "L-1")
        # 同 text（大小写 / 标点不同）→ 重复；同 quote 不同 text → 重复；同批内重复只进一条
        added, _ = store.merge(self.doc, [self._new("send the report!"),
                                          self._new("ship report", quote="I'll send the report."),
                                          self._new("Review PR"), self._new("review pr")],
                               [], "n2.md", "2026-09-02")
        self.assertEqual(added, 1)
        self.assertEqual([it["text"] for it in store.open_items(self.doc)], ["Send the report", "Review PR"])

    def test_same_text_other_direction_is_a_different_item(self):
        store.merge(self.doc, [self._new("share slides")], [], "n1.md", "2026-09-01")
        added, _ = store.merge(self.doc, [self._new("share slides", direction="person_owes")], [], "n2.md", "d")
        self.assertEqual(added, 1)

    def test_done_closes_only_open_items_and_records_source(self):
        store.merge(self.doc, [self._new("a"), self._new("b")], [], "n1.md", "2026-09-01")
        added, closed = store.merge(self.doc, [], ["L-1", "L-9", "junk"], "n2.md", "2026-09-03")
        self.assertEqual((added, closed), (0, 1))
        done = [it for it in self.doc["items"] if it["status"] == "done"]
        self.assertEqual(done[0]["id"], "L-1")
        self.assertEqual((done[0]["done_at"], done[0]["done_note"]), ("2026-09-03", "n2.md"))
        # 再关一次 = 0（已经 done）；done 的 text 可以再次以 open 出现（新一轮承诺）
        added, closed = store.merge(self.doc, [self._new("a")], ["L-1"], "n3.md", "d")
        self.assertEqual((added, closed), (1, 0))

    def test_done_retention_cap(self):
        for i in range(store.DONE_RETENTION + 5):
            store.merge(self.doc, [self._new("task %d" % i)], ["L-%d" % i], "n.md", "2026-01-%02d" % (i % 28 + 1))
        # 最后一条 open，其余 done → 只留 DONE_RETENTION 条 done
        done = [it for it in self.doc["items"] if it["status"] == "done"]
        self.assertEqual(len(done), store.DONE_RETENTION)
        self.assertEqual(len(store.open_items(self.doc)), 1)
        self.assertNotIn("L-1", {it["id"] for it in self.doc["items"]})

    def test_wrong_shaped_new_or_done_and_a_hand_edited_id_never_raise(self):
        """宪法第 11 条：``new`` / ``done`` 不是 list = 空表；手改坏掉的 id 只影响排序。"""
        for new, done in ((5, "L-1"), ({"text": "x"}, None), ("L-1", 3), (None, {"a": 1})):
            with self.subTest(new=new, done=done):
                self.assertEqual(store.merge(self.doc, new, done, "n.md", "d"), (0, 0))
        self.assertEqual(self.doc["items"], [])
        for i in range(store.DONE_RETENTION + 2):
            store.merge(self.doc, [self._new("task %d" % i)], ["L-%d" % i], "n.md", "2026-01-01")
        self.doc["items"][0]["id"] = "hand-edited"
        store.merge(self.doc, [], ["L-%d" % (store.DONE_RETENTION + 2)], "n.md", "2026-01-02")
        self.assertEqual(len([it for it in self.doc["items"] if it["status"] == "done"]), store.DONE_RETENTION)


class RenderAndPathsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ledger-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        mock.patch.object(config, "STATE_DIR", self.root / "state").start()
        self.addCleanup(mock.patch.stopall)

    def test_render_three_sections(self):
        doc = store.empty_ledger(person())
        store.merge(doc, [{"direction": "owner_owes", "text": "send report", "quote": "I will send it"},
                          {"direction": "person_owes", "text": "review PR"}], [], "n1.md", "2026-09-01")
        store.merge(doc, [], ["L-2"], "n2.md", "2026-09-02")
        with mock.patch.object(store.failures, "ui_lang", return_value="zh"):
            text = store.render(doc, "Zelin")
        self.assertIn("# 重点人物账本 · Arash", text)
        self.assertIn("## Zelin 答应 Arash 的", text)
        self.assertIn("- [ ] L-1 · send report（2026-09-01 · n1.md） 「I will send it」", text)
        self.assertIn("## Arash 答应 Zelin 的\n- （无）", text)
        self.assertIn("- [x] L-2 · review PR（完成于 2026-09-02 · n2.md）", text)
        with mock.patch.object(store.failures, "ui_lang", return_value="en"):
            self.assertIn("## Zelin owes Arash", store.render(doc, "Zelin"))

    def test_rendered_path_guard_and_json_roundtrip(self):
        cfg = config.Config()   # default_target_repo 未显式配置 → state/
        self.assertEqual(store.rendered_path(cfg, "arash-k"), self.root / "state" / "people_ledger" / "arash-k.md")
        cfg2 = config.Config(default_target_repo=str(self.root / "wb"), default_target_repo_configured=True)
        self.assertEqual(store.rendered_path(cfg2, "arash-k"), self.root / "wb" / "people_ledger" / "arash-k.md")
        p = person()
        doc = store.load_ledger(p)
        self.assertEqual(doc["items"], [])
        store.merge(doc, [{"direction": "owner_owes", "text": "x"}], [], "n.md", "d")
        store.save_ledger(doc)
        again = store.load_ledger(p)
        self.assertEqual(again["items"][0]["text"], "x")
        self.assertEqual(again["next_id"], 2)
        self.assertTrue(again["updated_at"])
        out = store.write_rendered(cfg, doc)
        self.assertTrue(out.exists())
        self.assertIn("L-1 · x", out.read_text(encoding="utf-8"))

    def test_cursor_roundtrip(self):
        self.assertIsNone(store.load_cursor())
        store.save_cursor(123.5, first_run_at="2026-09-04T00:00:00Z")
        cur = store.load_cursor()
        self.assertEqual((cur["marker"], cur["first_run_at"]), (123.5, "2026-09-04T00:00:00Z"))
        store.save_cursor(200.0)
        self.assertEqual(store.load_cursor()["first_run_at"], "2026-09-04T00:00:00Z")
        store.cursor_path().write_text("{bad json", encoding="utf-8")
        self.assertIsNone(store.load_cursor())


if __name__ == "__main__":
    unittest.main()
