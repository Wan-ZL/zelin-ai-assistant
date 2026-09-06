"""store2.migrate_yaml / export_yaml — the helpers split out in P3b (§53.3).

Pins: the three timestamp parsers (date-only incl. invalid dates, ISO with
and without tz, RFC 2822, garbage), created-column derivation precedence
(epoch / string / bool sent_at, sources[0].date, mtime fallback), the scan
helpers (unreadable / empty / non-dict / id-less members, archive residue
override, duplicate ignore), plan helpers (unknown keys both ways, cost
coercion, unserialisable payload), topo readiness, check_target refusals,
readback checks, the CLI stages, and export_yaml's row/prune helpers.
"""
import datetime as _dt
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib.store2 import export_yaml as ey
from act.lib.store2 import migrate_yaml as my

UTC = _dt.timezone.utc


class TimestampTestCase(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(my._parse_ts(" 2026-09-02 "), _dt.datetime(2026, 9, 2, tzinfo=UTC))
        self.assertIsNone(my._parse_ts("2026-02-30"))
        self.assertEqual(my._parse_ts("2026-09-02T10:00:00Z"),
                         _dt.datetime(2026, 9, 2, 10, tzinfo=UTC))
        self.assertEqual(my._parse_ts("2026-09-02T10:00:00").tzinfo, UTC)
        self.assertEqual(my._parse_ts("2026-09-02T10:00:00+02:00").utcoffset(),
                         _dt.timedelta(hours=2))
        rfc = my._parse_ts("Tue, 02 Sep 2026 10:00:00 +0000")
        self.assertEqual(rfc, _dt.datetime(2026, 9, 2, 10, tzinfo=UTC))
        self.assertEqual(my._parse_ts("Tue, 02 Sep 2026 10:00:00").tzinfo, UTC)
        self.assertIsNone(my._parse_ts("not a date"))
        self.assertIsNone(my._rfc2822(""))
        self.assertIsNone(my._iso8601("x"))

    def test_derive_created_precedence(self):
        base = {"sources": [{"date": "2026-01-01"}]}
        self.assertEqual(my._derive_created({"card": {"sent_at": 0}, **base}, 5.0),
                         ("1970-01-01T00:00:00Z", "card.sent_at"))
        self.assertEqual(my._derive_created({"card": {"sent_at": "2026-03-04"}, **base}, 5.0),
                         ("2026-03-04T00:00:00Z", "card.sent_at"))
        self.assertEqual(my._derive_created({"card": {"sent_at": True}, **base}, 5.0),
                         ("2026-01-01T00:00:00Z", "sources[0].date"))
        self.assertEqual(my._derive_created({"card": {"sent_at": "junk"}, **base}, 5.0)[1],
                         "sources[0].date")
        self.assertEqual(my._derive_created({"card": {"sent_at": None}, **base}, 5.0)[1],
                         "sources[0].date")
        self.assertEqual(my._derive_created({"sources": [{"date": 20260101}]}, 5.0),
                         (my._iso(_dt.datetime.fromtimestamp(5.0, tz=UTC)), "file-mtime"))
        self.assertEqual(my._derive_created({"sources": "junk"}, 5.0)[1], "file-mtime")
        self.assertEqual(my._derive_created({"sources": ["junk"]}, 5.0)[1], "file-mtime")
        self.assertEqual(my._derive_created({}, 5.0)[1], "file-mtime")
        self.assertIsNone(my._epoch_iso(10 ** 20))
        self.assertIsNone(my._sent_at_iso([1]))
        self.assertIsNone(my._first_source({"sources": []}))


class ScanHelpersTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="migrate-scan-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _f(self, name, text):
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_read_members(self):
        notes = []
        self.assertIsNone(my._read_members(self._f("bad.yaml", "a: [b"), notes))
        self.assertIsNone(my._read_members(self._f("empty.yaml", ""), notes))
        self.assertEqual(my._read_members(self._f("one.yaml", "id: X\n"), notes), [{"id": "X"}])
        self.assertEqual(my._read_members(self._f("list.yaml", "- id: A\n- 3\n"), notes),
                         [{"id": "A"}, 3])
        self.assertEqual([n.split(" ")[1] for n in notes], ["unreadable", "empty"])

    def test_member_helpers(self):
        self.assertEqual(my._member_id({"id": 7}), "7")
        self.assertIsNone(my._member_id({"id": None}))
        self.assertEqual(my._member_id({"id": "R-1"}), "R-1")
        notes = []
        path = self.tmp / "x.yaml"
        self.assertIsNone(my._member_entry("junk", path, 1.0, False, notes))
        self.assertIsNone(my._member_entry({"title": "no id"}, path, 1.0, False, notes))
        rid, entry = my._member_entry({"id": 5}, path, 1.0, True, notes)
        self.assertEqual((rid, entry["in_archive"], entry["mtime"]), ("5", True, 1.0))
        self.assertEqual(notes, ["skip non-dict member in x.yaml", "skip card without id in x.yaml"])

    def test_register_rules(self):
        by_id, notes = {}, {}
        notes = []
        a = {"in_archive": False, "file": Path("a.yaml")}
        b = {"in_archive": True, "file": Path("archive/a.yaml")}
        c = {"in_archive": False, "file": Path("c.yaml")}
        my._register(by_id, "R-1", a, notes)
        my._register(by_id, "R-1", c, notes)           # duplicate active → ignored
        self.assertIs(by_id["R-1"], a)
        my._register(by_id, "R-1", b, notes)           # archive overrides
        self.assertIs(by_id["R-1"], b)
        my._register(by_id, "R-1", a, notes)           # active after archive → ignored
        self.assertIs(by_id["R-1"], b)
        self.assertTrue(notes[0].startswith("duplicate id R-1 in c.yaml"))
        self.assertTrue(notes[1].startswith("residue: R-1 双份"))
        self.assertTrue(notes[2].startswith("duplicate id R-1 in a.yaml"))

    def test_scan_registry_end_to_end(self):
        self._f("R-000-example.yaml", "id: R-000\n")
        self._f("R-001.yaml", "id: R-001\ntitle: active\n")
        self._f("archive/R-001.yaml", "id: R-001\ntitle: sealed\n")
        by_id, notes = my.scan_registry(self.tmp)
        self.assertEqual(list(by_id), ["R-001"])
        self.assertEqual(by_id["R-001"]["raw"]["title"], "sealed")
        self.assertEqual(len(notes), 1)
        with self.assertRaises(my.MigrateError):
            my.scan_registry(self.tmp / "missing")


class PlanHelpersTestCase(unittest.TestCase):
    def test_unknown_keys_both_ways(self):
        w, e = [], []
        my._note_unknown_keys({"id": "a", "bogus": 1, "repo": "/r"}, False, w, e)
        self.assertEqual(w, [])
        self.assertEqual(len(e), 1)
        self.assertIn("'bogus'", e[0])
        w, e = [], []
        my._note_unknown_keys({"id": "a", "bogus": 1}, True, w, e)
        self.assertEqual((len(w), e), (1, []))

    def test_cost_coercion(self):
        w = []
        norm = {"cost_estimate_usd": None}
        my._coerce_cost_field(norm, w)
        self.assertEqual((norm, w), ({"cost_estimate_usd": None}, []))
        norm = {"cost_estimate_usd": 5}
        my._coerce_cost_field(norm, w)
        self.assertEqual((norm["cost_estimate_usd"], w), (5, []))
        norm = {"cost_estimate_usd": "cheap"}
        my._coerce_cost_field(norm, w)
        self.assertIsNone(norm["cost_estimate_usd"])
        self.assertIn("非数字", w[0])

    def test_payload_json(self):
        e = []
        self.assertEqual(my._payload_json({"a": 1}, e), '{"a": 1}')
        self.assertIsNone(my._payload_json({"a": _dt.date(2026, 1, 1)}, e))
        self.assertTrue(e and "无法 JSON 序列化" in e[0])

    def test_ready_plans_and_topo(self):
        def plan(rid, parent=None):
            return {"hot": {"id": rid, "merged_into_id": parent}}

        plans = [plan("b", "a"), plan("a"), plan("c", "b")]
        self.assertEqual([p["hot"]["id"] for p in my._ready_plans(plans, set())], ["a"])
        self.assertEqual([p["hot"]["id"] for p in my._topo_order(plans)], ["a", "b", "c"])
        with self.assertRaises(my.MigrateError) as cm:
            my._topo_order([plan("x", "ghost")])
        self.assertIn("x→ghost", str(cm.exception))

    def test_plan_card_with_coerce_off_keeps_payload(self):
        entry = {"raw": {"id": "P-1", "title": "t", "status": "detected",
                         "cost_estimate_usd": "cheap"}, "mtime": 0.0}
        p = my.plan_card("P-1", entry, coerce_cost=False)
        self.assertEqual(p["norm"]["cost_estimate_usd"], "cheap")
        self.assertEqual(p["errors"], [])
        p = my.plan_card("P-1", entry)
        self.assertIsNone(p["norm"]["cost_estimate_usd"])


class TargetAndReadbackTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="migrate-target-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _schema_db(self):
        db = self.tmp / "s.db"
        con = sqlite3.connect(str(db))
        my._apply_schema(con)
        con.close()
        return db

    def test_check_target_states(self):
        self.assertEqual(my.check_target(self.tmp / "none.db"), "fresh")
        empty = self.tmp / "empty.db"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(my.check_target(empty), "empty-file")
        self.assertEqual(my.check_target(self._schema_db()), "schema-only")
        garbage = self.tmp / "g.db"
        garbage.write_text("not sqlite at all, long enough to matter" * 4, encoding="utf-8")
        with self.assertRaises(my.MigrateError):
            my.check_target(garbage)

    def test_refusals(self):
        db = self._schema_db()
        con = sqlite3.connect(str(db))
        with self.assertRaises(my.MigrateError) as cm:
            my._refuse_if_foreign_schema(con, {"cards"}, db)
        self.assertIn("已有非 store2", str(cm.exception))
        my._refuse_if_rows(con)
        my._refuse_if_written(con)
        con.execute("UPDATE board_revision SET value = 3 WHERE id = 1")
        with self.assertRaises(my.MigrateError):
            my._refuse_if_written(con)
        con.execute("INSERT INTO cards (id, status, title, created, updated, version, board_rev,"
                    " tombstone, last_actor_type, payload) VALUES ('P-1','detected','t','x','x',1,1,0,"
                    "'system','{}')")
        with self.assertRaises(my.MigrateError) as cm:
            my._refuse_if_rows(con)
        self.assertIn("cards 有 1 行", str(cm.exception))
        con.close()

    def test_readback_checks(self):
        with self.assertRaises(my.MigrateError):
            my._check_payload("P-1", {"a": 1}, {"a": 2})
        my._check_payload("P-1", {"a": 1}, {"a": 1})
        with self.assertRaises(my.MigrateError):
            my._check_hot("P-1", {"tier": "T1"}, {"tier": "T2"})
        my._check_hot("P-1", {"tier": "T1"}, {"tier": "T1", "extra": 0})
        con = sqlite3.connect(":memory:")
        my._apply_schema(con)
        my._check_source_count(con, "P-1", 0)
        with self.assertRaises(my.MigrateError):
            my._check_source_count(con, "P-1", 1)
        with self.assertRaises(my.MigrateError):
            my._readback_plan(con, {"hot": {"id": "ghost"}})
        con.close()

    def test_cli_stages(self):
        reg = self.tmp / "registry"
        reg.mkdir()
        (reg / "P-1.yaml").write_text("id: P-1\ntitle: t\nstatus: detected\n", encoding="utf-8")
        db = self.tmp / "out.db"
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(my.main(["--registry", str(reg), "--db", str(db), "--dry-run"]), 0)
        self.assertIn("DRY-RUN", out.getvalue())
        self.assertFalse(db.exists())
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(my.main(["--registry", str(reg), "--db", str(db)]), 0)
        self.assertIn("DONE", out.getvalue())
        self.assertTrue(db.exists())
        (reg / "P-2.yaml").write_text("id: P-2\ntitle: t\nstatus: flying\n", encoding="utf-8")
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(my.main(["--registry", str(reg), "--db", str(self.tmp / "x.db")]), 2)
        self.assertIn("REFUSED/FAILED", err.getvalue())
        self.assertIn("P-2: status 'flying'", err.getvalue())

    def test_plan_or_errors_and_open_target(self):
        p, errs = my._plan_or_errors("P-1", {"raw": {"id": "P-1", "title": "t", "status": "detected"},
                                             "mtime": 0.0}, False)
        self.assertEqual((p["hot"]["id"], errs), ("P-1", []))
        with mock.patch.object(my, "plan_card", side_effect=ValueError("bad")):
            p, errs = my._plan_or_errors("P-1", {"raw": {}, "mtime": 0.0}, False)
        self.assertEqual((p, errs), (None, ["P-1: plan_card failed (ValueError: bad)"]))
        con = my._open_target(str(self.tmp / "deep" / "dir" / "t.db"), False)
        con.close()
        self.assertTrue((self.tmp / "deep" / "dir" / "t.db").exists())
        my._open_target("ignored", True).close()


class ExportHelpersTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="export-helpers-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_card_object_and_rel(self):
        self.assertEqual(ey._card_object("../x", "{}"), (None, "skip '../x': id 不符合文件名白名单"))
        self.assertEqual(ey._card_object(".hidden", "{}")[0], None)
        self.assertEqual(ey._card_object("P-1", "{broken")[0], None)
        self.assertEqual(ey._card_object("P-1", '{"title": "no id"}'),
                         (None, "skip P-1: payload 缺 id（疑似半截行）"))
        self.assertEqual(ey._card_object("P-1", "[1]")[0], None)
        self.assertEqual(ey._card_object("P-1", '{"id": "P-1"}'), ({"id": "P-1"}, None))
        self.assertEqual(ey._snapshot_rel("P-1", "archived"), Path("archive") / "P-1.yaml")
        self.assertEqual(ey._snapshot_rel("P-1", "detected"), Path("P-1.yaml"))

    def test_write_if_changed_and_prune(self):
        p = self.tmp / "sub" / "a.yaml"
        self.assertTrue(ey._write_if_changed(p, "x\n"))
        self.assertFalse(ey._write_if_changed(p, "x\n"))
        self.assertTrue(ey._write_if_changed(p, "y\n"))
        (self.tmp / "R-000-example.yaml").write_text("", encoding="utf-8")
        (self.tmp / "old.yaml").write_text("", encoding="utf-8")
        (self.tmp / "archive").mkdir()
        (self.tmp / "archive" / "gone.yaml").write_text("", encoding="utf-8")
        keep = self.tmp / "keep.yaml"
        keep.write_text("", encoding="utf-8")
        self.assertEqual(ey._prune_snapshots(self.tmp, {keep}), 2)
        self.assertTrue((self.tmp / "R-000-example.yaml").exists())
        self.assertTrue(keep.exists())
        self.assertEqual(ey._prune_snapshots(self.tmp / "nodir", set()), 0)

    def test_export_row_tally(self):
        tally = {"written": 0, "unchanged": 0, "tombstones": 0}
        problems, expected = [], set()
        ey._export_row(("P-1", "detected", 1, "{}"), self.tmp, tally, problems, expected)
        ey._export_row(("P-2", "detected", 0, "{bad"), self.tmp, tally, problems, expected)
        ey._export_row(("P-3", "archived", 0, '{"id": "P-3"}'), self.tmp, tally, problems, expected)
        ey._export_row(("P-3", "archived", 0, '{"id": "P-3"}'), self.tmp, tally, problems, expected)
        self.assertEqual(tally, {"written": 1, "unchanged": 1, "tombstones": 1})
        self.assertEqual(len(problems), 1)
        self.assertEqual(expected, {self.tmp / "archive" / "P-3.yaml"})

    def test_normalize_card_helpers(self):
        vals = ey._field_values({"repo": "/r", "sources": None})
        self.assertEqual((vals["target_repo"], vals["sources"]), ("/r", None))
        self.assertEqual(ey._field_values({})["sources"], [])
        self.assertEqual(ey._coerce_delivery_mode(" Chat "), "chat")
        self.assertEqual(ey._coerce_delivery_mode(None), "repo")
        vals = {"id": 4, "title": None, "tier": 2, "work_id": "R-1"}
        ey._coerce_scalars(vals)
        self.assertEqual(vals, {"id": "4", "title": None, "tier": "2", "work_id": "R-1"})
        self.assertTrue(ey._skip_optional("delivery_mode", "repo"))
        self.assertFalse(ey._skip_optional("delivery_mode", "chat"))
        self.assertTrue(ey._skip_optional("x", 0))
        card = ey.normalize_card({"id": 1, "title": "t", "delivery_mode": "chat", "bogus": 1})
        self.assertEqual((card["id"], card["delivery_mode"]), ("1", "chat"))
        self.assertNotIn("bogus", card)
        self.assertEqual(json.dumps(card)[:1], "{")

    def test_export_main_missing_db(self):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(ey.main(["--db", str(self.tmp / "none.db"), "--out", str(self.tmp)]), 1)
        self.assertIn("DB 不存在", err.getvalue())


class ExportWarnTestCase(unittest.TestCase):
    def test_problem_rows_are_reported_and_exit_2(self):
        import sqlite3 as _sq
        tmp = Path(tempfile.mkdtemp(prefix="export-warn-"))
        db = tmp / "s.db"
        con = _sq.connect(str(db))
        my._apply_schema(con)
        con.execute("INSERT INTO cards (id, status, title, created, updated, version, board_rev,"
                    " tombstone, last_actor_type, payload) VALUES ('P-1','detected','t','x','x',1,1,0,"
                    "'system','{}')")           # payload without id → skipped with a WARN
        con.commit()
        con.close()
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(ey.export_db(db, tmp / "out"), 2)
        self.assertIn("export: WARN skip P-1: payload 缺 id", err.getvalue())
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
