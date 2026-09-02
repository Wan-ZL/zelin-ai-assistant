"""素材库台账 + 状态机（CONTRACT §62.2 / §62.3；act/lib/materials.py）。

- add：归一（trim / http(s) / 长度上限 / url 与 note 至少一个）、记录形状、
  最新在前的列表；开放条目上限 → full。
- fold：同 id 后行覆盖前行；坏行 / 非法状态行被读侧忽略，不崩。
- 状态机：TRANSITIONS 表逐格穷举（合法 → 落新行；非法 → bad_transition 且
  台账不动）；links 只增不删、只收 LINK_KEYS；dismissed → new 回程票。
- 压缩：超过 LEDGER_MAX_BYTES 时折叠 + 从最老的终态条目开始丢；开放条目
  一条不丢；压缩后仍是合法 jsonl 且读侧结果不变。
零网络、零子进程。
"""
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import materials


def _clock(start="2026-09-02T10:00:00Z"):
    """确定性时钟：每次调用 +1 秒（ts 单调，排序可预测）。"""
    counter = itertools.count()
    base = int(start[11:13]) * 3600 + int(start[14:16]) * 60 + int(start[17:19])

    def tick():
        s = base + next(counter)
        return "2026-09-02T%02d:%02d:%02dZ" % (s // 3600, (s // 60) % 60, s % 60)
    return tick


def _ident(prefix="m-"):
    counter = itertools.count(1)
    return lambda: "%s%012x" % (prefix, next(counter))


class _LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-materials-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = materials.ledger_path(self.home)
        self.clock = _clock()
        self.ident = _ident()

    def add(self, **kw):
        kw.setdefault("clock", self.clock)
        kw.setdefault("ident", self.ident)
        return materials.add(self.path, **kw)

    def lines(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]


class AddTestCase(_LedgerCase):
    def test_ledger_lives_under_state_materials(self):
        self.assertEqual(self.path, self.home / "state" / "materials" / "materials.jsonl")

    def test_add_writes_one_full_record_line(self):
        rec = self.add(url="  https://example.com/x?a=1 ", note="  看看这个  ")
        self.assertEqual(rec, {
            "id": "m-000000000001", "ts": "2026-09-02T10:00:00Z",
            "created_at": "2026-09-02T10:00:00Z", "url": "https://example.com/x?a=1",
            "note": "看看这个", "status": "new", "links": {},
        })
        self.assertTrue(materials.ID_RE.match(rec["id"]))
        self.assertEqual(self.lines(), [rec])
        self.assertEqual(materials.list_items(self.path), [rec])
        self.assertEqual(materials.get(self.path, rec["id"]), rec)

    def test_real_id_and_clock_shapes(self):
        rec = materials.add(self.path, note="x")
        self.assertRegex(rec["id"], materials.ID_RE.pattern)
        self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_note_only_and_url_only_both_allowed(self):
        self.assertEqual(self.add(note="just a thought")["url"], "")
        self.assertEqual(self.add(url="http://a.b/c")["note"], "")

    def test_empty_both_rejected(self):
        with self.assertRaises(materials.MaterialsError) as cm:
            self.add(url="  ", note="")
        self.assertEqual(cm.exception.code, "invalid")
        self.assertFalse(self.path.exists())

    def test_url_must_be_absolute_http(self):
        for bad in ("ftp://x/y", "file:///etc/passwd", "javascript:alert(1)", "example.com/x",
                    "https://", "https://" + "a" * materials.MAX_URL_CHARS):
            with self.assertRaises(materials.MaterialsError, msg=bad) as cm:
                self.add(url=bad, note="n")
            self.assertEqual(cm.exception.code, "invalid")

    def test_note_caps_and_nul(self):
        with self.assertRaises(materials.MaterialsError):
            self.add(note="a" * (materials.MAX_NOTE_CHARS + 1))
        with self.assertRaises(materials.MaterialsError):
            self.add(note="bad\x00byte")
        self.assertEqual(len(self.add(note="a" * materials.MAX_NOTE_CHARS)["note"]),
                         materials.MAX_NOTE_CHARS)

    def test_non_string_inputs_are_coerced_not_crashing(self):
        self.assertEqual(self.add(url=None, note=123)["note"], "123")

    def test_list_is_newest_first_and_filters_by_status(self):
        a = self.add(note="a")
        b = self.add(note="b")
        c = self.add(note="c")
        self.assertEqual([r["id"] for r in materials.list_items(self.path)],
                         [c["id"], b["id"], a["id"]])
        materials.transition(self.path, b["id"], "dismissed", clock=self.clock)
        self.assertEqual([r["id"] for r in materials.list_items(self.path, "open")],
                         [c["id"], a["id"]])
        self.assertEqual([r["id"] for r in materials.list_items(self.path, "dismissed")], [b["id"]])
        self.assertEqual(len(materials.list_items(self.path, "all")), 3)
        with self.assertRaises(materials.MaterialsError):
            materials.list_items(self.path, "bogus")

    def test_same_second_ties_order_by_ledger_position_not_random_id(self):
        frozen = lambda: "2026-09-02T10:00:00Z"  # noqa: E731 - three adds in the same second
        ids = iter(["m-ffffffffffff", "m-000000000000", "m-777777777777"])  # deliberately unsorted
        first = materials.add(self.path, note="1", clock=frozen, ident=lambda: next(ids))
        second = materials.add(self.path, note="2", clock=frozen, ident=lambda: next(ids))
        third = materials.add(self.path, note="3", clock=frozen, ident=lambda: next(ids))
        self.assertEqual([r["id"] for r in materials.list_items(self.path)],
                         [third["id"], second["id"], first["id"]])
        # a compaction rewrite keeps ledger order, so the tie-break survives it
        materials.compact(self.path)
        self.assertEqual([r["id"] for r in materials.list_items(self.path)],
                         [third["id"], second["id"], first["id"]])

    def test_missing_ledger_reads_as_empty(self):
        self.assertEqual(materials.list_items(self.path), [])
        self.assertEqual(materials.open_count(self.path), 0)
        self.assertIsNone(materials.get(self.path, "m-000000000001"))

    def test_open_cap_refuses_with_full(self):
        with mock.patch.object(materials, "MAX_OPEN_ITEMS", 2):
            self.add(note="1")
            self.add(note="2")
            with self.assertRaises(materials.MaterialsError) as cm:
                self.add(note="3")
            self.assertEqual(cm.exception.code, "full")
            self.assertEqual(materials.open_count(self.path), 2)
            # terminal items do not count against the cap
            first = materials.list_items(self.path)[-1]
            materials.transition(self.path, first["id"], "dismissed", clock=self.clock)
            self.assertEqual(self.add(note="3")["status"], "new")


class FoldTestCase(_LedgerCase):
    def test_later_line_for_same_id_wins(self):
        rec = self.add(note="a")
        materials.transition(self.path, rec["id"], "picked_up", clock=self.clock)
        self.assertEqual(len(self.lines()), 2)
        items = materials.list_items(self.path, "all")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "picked_up")
        self.assertEqual(items[0]["created_at"], rec["created_at"])
        self.assertGreater(items[0]["ts"], rec["ts"])

    def test_garbage_lines_are_skipped_not_fatal(self):
        rec = self.add(note="a")
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write(json.dumps(["list", "not", "dict"]) + "\n")
            fh.write(json.dumps({"id": 5, "status": "new"}) + "\n")
            fh.write(json.dumps({"id": "m-deadbeefcafe", "status": "bogus"}) + "\n")
            fh.write("\n")
        self.assertEqual(materials.list_items(self.path, "all"), [rec])


class StateMachineTestCase(_LedgerCase):
    def _item_in(self, status):
        """造一条处于 status 的条目（沿合法路径走过去）。"""
        rec = self.add(note="s")
        path = {"new": [], "picked_up": ["picked_up"],
                "proposal_created": ["picked_up", "proposal_created"],
                "pr_opened": ["picked_up", "proposal_created", "pr_opened"],
                "done": ["picked_up", "proposal_created", "pr_opened", "done"],
                "dismissed": ["dismissed"]}[status]
        for step in path:
            rec = materials.transition(self.path, rec["id"], step, clock=self.clock)
        self.assertEqual(rec["status"], status)
        return rec

    def test_vocabulary_is_closed_and_partitioned(self):
        self.assertEqual(set(materials.TRANSITIONS), set(materials.STATUSES))
        self.assertEqual(materials.OPEN_STATUSES | materials.TERMINAL_STATUSES | {"pr_opened"},
                         set(materials.STATUSES))
        self.assertFalse(materials.OPEN_STATUSES & materials.TERMINAL_STATUSES)
        for targets in materials.TRANSITIONS.values():
            self.assertTrue(targets <= set(materials.STATUSES))

    def test_every_cell_of_the_table(self):
        for src in materials.STATUSES:
            for dst in materials.STATUSES:
                rec = self._item_in(src)
                before = self.lines()
                if dst in materials.TRANSITIONS[src]:
                    out = materials.transition(self.path, rec["id"], dst, clock=self.clock)
                    self.assertEqual(out["status"], dst, "%s→%s" % (src, dst))
                    self.assertEqual(materials.get(self.path, rec["id"])["status"], dst)
                    self.assertEqual(len(self.lines()), len(before) + 1)
                else:
                    with self.assertRaises(materials.MaterialsError, msg="%s→%s" % (src, dst)) as cm:
                        materials.transition(self.path, rec["id"], dst, clock=self.clock)
                    self.assertEqual(cm.exception.code, "bad_transition")
                    self.assertEqual(self.lines(), before)

    def test_done_is_terminal_dismissed_has_return_ticket(self):
        self.assertEqual(materials.TRANSITIONS["done"], frozenset())
        self.assertEqual(materials.TRANSITIONS["dismissed"], frozenset({"new"}))
        rec = self._item_in("dismissed")
        back = materials.transition(self.path, rec["id"], "new", clock=self.clock)
        self.assertEqual(back["status"], "new")
        self.assertEqual(back["note"], "s")

    def test_open_means_not_yet_pr_opened_done_or_dismissed(self):
        self.assertEqual(materials.OPEN_STATUSES, {"new", "picked_up", "proposal_created"})

    def test_unknown_status_and_unknown_id(self):
        rec = self.add(note="x")
        with self.assertRaises(materials.MaterialsError) as cm:
            materials.transition(self.path, rec["id"], "shipped")
        self.assertEqual(cm.exception.code, "invalid")
        with self.assertRaises(materials.MaterialsError) as cm:
            materials.transition(self.path, "m-000000000fff", "picked_up")
        self.assertEqual(cm.exception.code, "not_found")

    def test_links_accumulate_and_only_known_keys(self):
        rec = self.add(note="x")
        rec = materials.transition(self.path, rec["id"], "picked_up", clock=self.clock)
        rec = materials.transition(self.path, rec["id"], "proposal_created",
                                   links={"proposal_id": "P-201", "junk": "no"}, clock=self.clock)
        self.assertEqual(rec["links"], {"proposal_id": "P-201"})
        rec = materials.transition(self.path, rec["id"], "pr_opened",
                                   links={"pr_url": "https://github.com/o/r/pull/9"}, clock=self.clock)
        self.assertEqual(rec["links"], {"proposal_id": "P-201",
                                        "pr_url": "https://github.com/o/r/pull/9"})
        rec = materials.transition(self.path, rec["id"], "done", clock=self.clock)
        self.assertEqual(rec["links"]["proposal_id"], "P-201")  # never dropped


class CompactionTestCase(_LedgerCase):
    def _fill(self, n_open, n_done):
        for i in range(n_open):
            self.add(note="open-%d %s" % (i, "x" * 200))
        for i in range(n_done):
            rec = self.add(note="done-%d %s" % (i, "y" * 200))
            for step in ("picked_up", "proposal_created", "done"):
                materials.transition(self.path, rec["id"], step, clock=self.clock)

    def test_append_over_cap_folds_and_drops_oldest_terminal_first(self):
        with mock.patch.object(materials, "LEDGER_MAX_BYTES", 6000):
            self._fill(n_open=6, n_done=12)
        items = materials.list_items(self.path, "all")
        opens = [r for r in items if r["status"] == "new"]
        dones = [r for r in items if r["status"] == "done"]
        self.assertEqual(len(opens), 6, "open items are never dropped by compaction")
        self.assertLess(len(dones), 12, "some terminal items were trimmed")
        # what survived is the newest terminal (oldest dropped first)
        self.assertTrue(all(r["note"].startswith("done-") for r in dones))
        kept_idx = sorted(int(r["note"].split()[0].split("-")[1]) for r in dones)
        self.assertEqual(kept_idx, list(range(12 - len(dones), 12)))
        # one line per id after a rewrite; still valid jsonl
        ids = [line["id"] for line in self.lines()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(self.path.stat().st_size, 6000 + 600)  # the last append lands post-compaction
        self.assertFalse(self.path.with_suffix(".jsonl.tmp").exists())

    def test_compact_without_pressure_only_folds(self):
        rec = self.add(note="a")
        for step in ("picked_up", "new", "picked_up"):
            materials.transition(self.path, rec["id"], step, clock=self.clock)
        self.assertEqual(len(self.lines()), 4)
        before = materials.list_items(self.path, "all")
        materials.compact(self.path)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(materials.list_items(self.path, "all"), before)

    def test_compact_keeps_creation_order_in_file(self):
        with mock.patch.object(materials, "LEDGER_MAX_BYTES", 10 ** 9):
            self._fill(n_open=3, n_done=2)
        materials.compact(self.path)
        created = [line["created_at"] for line in self.lines()]
        self.assertEqual(created, sorted(created))
        self.assertEqual(len(created), 5)

    def test_open_items_alone_over_cap_are_all_kept(self):
        with mock.patch.object(materials, "LEDGER_MAX_BYTES", 1000):
            self._fill(n_open=8, n_done=0)
        self.assertEqual(materials.open_count(self.path), 8)


class LockTestCase(_LedgerCase):
    def test_lock_file_sits_next_to_ledger_and_is_reentrant_free(self):
        self.add(note="a")
        if materials.fcntl is not None:
            self.assertTrue(self.path.with_suffix(".lock").exists())

    def test_no_fcntl_path_still_works(self):
        with mock.patch.object(materials, "fcntl", None):
            rec = self.add(note="windows")
            materials.transition(self.path, rec["id"], "dismissed", clock=self.clock)
            materials.compact(self.path)
        self.assertEqual(materials.list_items(self.path, "dismissed")[0]["note"], "windows")


if __name__ == "__main__":
    unittest.main()
