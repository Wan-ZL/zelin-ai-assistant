"""store2.activate — the helpers split out of first_run / status / tick / main
in P3b (CONTRACT §53.3 / §53.4 / §53.6).

Pins: field-level parity diffs (missing either side, per-key rows, clipped
values), the plan stage (lossy notes → refusal, plan_card crash → refusal),
the migrate stage refusing on a MigrateError and disposing the db, the
verify stage (diff / concurrent writer), the state table (yaml_forced,
db_missing, active + late writes + bad activated_at, cooldown vs refused,
pending), tick's attempt lines (activated / refused with >10 diffs), and the
CLI in-process (--report, --export-now on both backends, the tick exit code).
"""
import datetime as _dt
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act.lib import config, registry
from act.lib.store2 import activate, migrate_yaml

NOW = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.timezone.utc)


def _card_yaml(rid, title="t", extra=""):
    return (f"id: {rid}\ntitle: {title}\ntype: dev\ntier: T1\nstatus: card_sent\n"
            f"hardness: soft\ndeadline: null\nrepeated_mentions: 1\n"
            f"green_sign_required: false\ndisagreement: null\ncost_estimate_usd: null\n"
            f"sources: []\nplan:\n- do it\n{extra}")


class ParityDiffTestCase(unittest.TestCase):
    def test_field_diffs(self):
        self.assertEqual(activate._field_diffs("P-1", {"a": 1}, {"a": 1}), [])
        rows = activate._field_diffs("P-1", {"a": 1, "b": "x" * 80}, {"a": 2, "c": 3})
        self.assertEqual(rows[0], "P-1.a: backup=1 export=2")
        self.assertTrue(rows[1].startswith('P-1.b: backup="' + "x" * 58 + "…"))
        self.assertEqual(rows[2], "P-1.c: backup=null export=3")

    def test_parity_diff_on_directories(self):
        store2_testkit.use_backend(self, "yaml")
        a = config.STATE_DIR / "parity-a"
        b = config.STATE_DIR / "parity-b"
        a.mkdir(parents=True, exist_ok=True)
        b.mkdir(parents=True, exist_ok=True)
        (a / "P-1.yaml").write_text(_card_yaml("P-1"), encoding="utf-8")
        (a / "P-2.yaml").write_text(_card_yaml("P-2"), encoding="utf-8")
        (b / "P-1.yaml").write_text(_card_yaml("P-1", title="changed"), encoding="utf-8")
        (b / "P-3.yaml").write_text(_card_yaml("P-3"), encoding="utf-8")
        diffs = activate.parity_diff(a, b)
        self.assertEqual(diffs, ["P-2: present in backup, missing from export",
                                 "P-3: present in export, missing from backup",
                                 'P-1.title: backup="t" export="changed"'])
        self.assertEqual(activate.parity_diff(a / "missing", b / "missing"), [])


class FirstRunStagesTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "auto")

    def test_plan_stage_refuses_lossy_and_crashing_cards(self):
        backup = config.STATE_DIR / "bk"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "dup.yaml").write_text("- id: P-1\n  title: a\n- id: P-1\n  title: b\n",
                                          encoding="utf-8")
        plans, _notes, refusal = activate._plan_from_backup(NOW, backup)
        self.assertIsNone(plans)
        self.assertEqual(refusal["result"], "refused")
        self.assertIn("would drop", refusal["reason"])
        (backup / "dup.yaml").unlink()
        (backup / "P-2.yaml").write_text(_card_yaml("P-2"), encoding="utf-8")
        with mock.patch.object(migrate_yaml, "plan_card", side_effect=TypeError("boom")):
            plans, _notes, refusal = activate._plan_from_backup(NOW, backup)
        self.assertIsNone(plans)
        self.assertIn("cannot be represented", refusal["reason"])
        self.assertIn("P-2: plan_card failed (TypeError: boom)", refusal["diff"])
        plans, notes, refusal = activate._plan_from_backup(NOW, backup)
        self.assertEqual((len(plans), refusal), (1, None))
        plans, notes, refusal = activate._plan_from_backup(NOW, backup / "nope")
        self.assertEqual((plans, notes, refusal), ([], [], None))

    def test_plan_one_shapes(self):
        entry = {"raw": {"id": "P-9", "title": "t", "status": "flying"}, "mtime": 0.0}
        p, errs = activate._plan_one("P-9", entry)
        self.assertIsNone(p)
        self.assertTrue(errs and errs[0].startswith("P-9: status 'flying'"))

    def test_migrate_stage_disposes_db_on_error(self):
        db = registry.store2_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_text("garbage", encoding="utf-8")
        refusal = activate._migrate_plans(NOW, db, [], config.STATE_DIR / "bk")
        self.assertEqual(refusal["result"], "refused")
        self.assertIn("migration failed", refusal["reason"])
        self.assertFalse(db.exists())
        self.assertIsNone(activate._migrate_plans(NOW, db, [], config.STATE_DIR / "bk"))
        self.assertTrue(db.exists())

    def test_verify_stage_refuses_concurrent_writer(self):
        db = registry.store2_db_path()
        activate._migrate_plans(NOW, db, [], config.STATE_DIR / "bk")
        backup = config.STATE_DIR / "bk2"
        backup.mkdir(parents=True, exist_ok=True)
        refusal = activate._verify_and_mark(NOW, db, backup, {"ghost.yaml": "sha"})
        self.assertIn("another writer", refusal["reason"])
        self.assertEqual(refusal["retry_after"],
                         activate._iso(NOW + _dt.timedelta(seconds=activate.RETRY_AFTER_RACE_S)))
        self.assertFalse(db.exists())
        activate._migrate_plans(NOW, db, [], backup)
        self.assertIsNone(activate._verify_and_mark(NOW, db, backup,
                                                    activate.manifest(config.REGISTRY_DIR)))


class StatusTableTestCase(unittest.TestCase):
    def test_state_fields(self):
        db = config.STATE_DIR / "nope.db"
        self.assertEqual(activate._state_fields({}, {}, "yaml", db, NOW),
                         {"state": "yaml_forced", "marker_present": False})
        self.assertEqual(activate._state_fields({"activated_at": "x"}, {}, None, db, NOW),
                         {"state": "db_missing"})
        self.assertEqual(activate._state_fields({}, {}, None, db, NOW), {"state": "pending"})
        self.assertEqual(activate._state_fields({}, {"result": "refused", "retry_after": "junk"},
                                                None, db, NOW), {"state": "refused"})
        later = activate._iso(NOW + _dt.timedelta(hours=1))
        self.assertEqual(activate._inactive_state({"result": "refused", "retry_after": later}, NOW),
                         "cooldown")
        earlier = activate._iso(NOW - _dt.timedelta(hours=1))
        self.assertEqual(activate._inactive_state({"result": "refused", "retry_after": earlier}, NOW),
                         "refused")
        self.assertEqual(activate._inactive_state({"result": "activated"}, NOW), "pending")

    def test_active_fields_and_late_writes(self):
        store2_testkit.use_backend(self, "yaml")
        registry.save(registry.Requirement(id="P-late", title="late"))
        marker = {"activated_at": activate._iso(NOW - _dt.timedelta(days=1))}
        fields = activate._active_fields(marker)
        self.assertEqual(fields["state"], "active")
        self.assertEqual(fields["late_yaml_writes"], ["P-late.yaml"])
        self.assertIsNone(fields["export_last_run"])
        self.assertEqual(activate._active_fields({"activated_at": "garbage"})["late_yaml_writes"], [])
        self.assertIsNone(activate._activated_ts({}))
        far_future = _dt.datetime.now(_dt.timezone.utc).timestamp() + 10 ** 6
        self.assertEqual(activate._late_yaml_writes(far_future), [])
        files = registry.registry_yaml_files(include_archived=True)
        with mock.patch.object(registry, "registry_yaml_files", return_value=files), \
                mock.patch.object(Path, "stat", side_effect=OSError("gone")):
            self.assertEqual(activate._late_yaml_writes(0.0), [])


class TickAndCliTestCase(unittest.TestCase):
    def test_attempt_lines(self):
        lines = activate._attempt_lines({"result": "activated", "cards": 3, "backup_dir": "/b"})
        self.assertEqual(len(lines), 1)
        self.assertIn("3 cards", lines[0])
        refused = {"result": "refused", "reason": "why", "diff": [f"d{i}" for i in range(12)],
                   "diff_total": 12, "backup_dir": "/b", "retry_after": "T"}
        lines = activate._attempt_lines(refused)
        self.assertEqual(lines[0], "ACTIVATION REFUSED — YAML stays the truth: why")
        self.assertEqual(sum(1 for ln in lines if ln.startswith("  diff:")), 10)
        self.assertTrue(any("2 more" in ln for ln in lines))
        self.assertIn("retry after T", lines[-1])
        short = activate._attempt_lines({"result": "refused", "reason": "r"})
        self.assertEqual(len(short), 2)

    def test_daily_export_lines_gate(self):
        store2_testkit.use_backend(self, "yaml")
        self.assertEqual(activate._daily_export_lines({"state": "active"}, NOW), [])
        with mock.patch.object(registry, "backend", return_value="sqlite"), \
                mock.patch.object(activate, "daily_export", return_value=None):
            self.assertEqual(activate._daily_export_lines({"state": "active"}, NOW), [])
        with mock.patch.object(registry, "backend", return_value="sqlite"), \
                mock.patch.object(activate, "daily_export", return_value="refreshed"):
            self.assertEqual(activate._daily_export_lines({"state": "active"}, NOW), ["refreshed"])
            self.assertEqual(activate._daily_export_lines({"state": "pending"}, NOW), [])

    def test_cli_report_and_export_now(self):
        store2_testkit.use_backend(self, "yaml")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(activate.main(["--report"]), 0)
        self.assertEqual(json.loads(out.getvalue())["state"], "yaml_forced")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(activate.main(["--export-now"]), 2)
        self.assertIn("not the active backend", err.getvalue())
        store2_testkit.use_backend(self, "sqlite")
        with mock.patch("sys.stdout", io.StringIO()) as out2:
            self.assertEqual(activate.main(["--export-now"]), 0)
        self.assertIn("export refreshed", out2.getvalue())

    def test_cli_tick_exit_codes(self):
        store2_testkit.use_backend(self, "yaml")
        seen = []
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(activate.main([], log=seen.append), 0)   # yaml_forced → 0
        self.assertIn("state=yaml_forced", out.getvalue())
        with mock.patch.object(activate, "tick", return_value=["line-1"]), \
                mock.patch.object(activate, "status", return_value={"state": "pending",
                                                                    "backend": "yaml"}), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(activate.main([], log=seen.append), 2)
        self.assertEqual(seen, ["line-1"])


if __name__ == "__main__":
    unittest.main()
