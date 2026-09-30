"""`state/` 的未来戳体检：默认只看，--apply 只搬不删（CONTRACT §82.5，issue #452）。

2026-09-18 那次泄漏留在 live `state/` 里的假时钟戳（`slack_mcp.marker` =
`2027-10-23T11:32:23Z`、`ask_history.json` 里的 `2027-` 行）要有一把扫帚。它必须：

- **默认零副作用**——`report()` 纯读，CLI 不带 `--apply` 一个字节都不动；
- **只搬不删（宪法第 2 条）**——整份文件进 `state/backups/quarantine-<ts>/`，
  带 sha256 manifest，搬错了 `mv` 回去；同名目录永不覆盖；
- **卡片一个字节不碰**——`work_seq.json` 与 store2 只上报（registry 单写者，且被夹具卡
  抬高过的工号按法条不许调低：那一笔是永久欠账，报出来给人看，不许悄悄「修」）；
- **永不抛（宪法第 11 条）**——坏文件、读不动的文件只属于它自己。
"""
import contextlib
import datetime as _dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import state_audit

NOW = _dt.datetime(2026, 9, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)
FUTURE = "2027-10-23T11:32:23Z"
PAST = "2026-09-18T06:03:00Z"


class FutureStampScanTestCase(unittest.TestCase):
    def test_finds_a_future_stamp(self):
        self.assertEqual(state_audit.future_stamps(FUTURE, NOW), [FUTURE])

    def test_ignores_a_past_stamp(self):
        self.assertEqual(state_audit.future_stamps(PAST, NOW), [])

    def test_finds_stamps_embedded_in_json(self):
        text = json.dumps({"asked_at": FUTURE, "answered_at": PAST})
        self.assertEqual(state_audit.future_stamps(text, NOW), [FUTURE])

    def test_deduplicates_and_keeps_source_order(self):
        text = "%s %s %s" % (FUTURE, "2028-01-01T00:00:00Z", FUTURE)
        self.assertEqual(state_audit.future_stamps(text, NOW),
                         [FUTURE, "2028-01-01T00:00:00Z"])

    def test_caps_the_evidence_list(self):
        text = " ".join("202%d-01-01T00:00:00Z" % d for d in range(7, 10))
        text += " " + " ".join("203%d-01-01T00:00:00Z" % d for d in range(0, 6))
        self.assertLessEqual(len(state_audit.future_stamps(text, NOW)),
                             state_audit.MAX_STAMPS_PER_FILE)

    def test_prose_that_is_not_a_timestamp_is_ignored(self):
        self.assertEqual(state_audit.future_stamps("version 2027.1 released", NOW), [])


class _HomeMixin:
    """每个用例一个一次性 home（扫的是 `<home>/state`，绝不碰沙箱之外的东西）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="state-audit-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.state = self.home / "state"
        self.state.mkdir(parents=True)

    def _write(self, name, text):
        path = self.state / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class ReportTestCase(_HomeMixin, unittest.TestCase):
    def test_a_clean_state_dir_reports_nothing(self):
        self._write("slack_mcp.marker", PAST)
        result = state_audit.report(self.home, NOW)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["scanned"], 1)

    def test_the_poisoned_marker_is_reported_with_its_stamp(self):
        self._write("slack_mcp.marker", FUTURE)
        result = state_audit.report(self.home, NOW)
        self.assertEqual([f["rel"] for f in result["findings"]],
                         ["state/slack_mcp.marker"])
        self.assertEqual(result["findings"][0]["stamps"], [FUTURE])
        self.assertFalse(result["findings"][0]["report_only"])

    def test_work_seq_is_report_only(self):
        """工号只许往上走（法条禁止调低），这把扫帚不许碰它。"""
        self._write("work_seq.json", json.dumps({"next": 8154, "at": FUTURE}))
        result = state_audit.report(self.home, NOW)
        self.assertTrue(result["findings"][0]["report_only"])

    def test_report_writes_nothing(self):
        self._write("slack_mcp.marker", FUTURE)
        before = sorted(p.name for p in self.state.iterdir())
        state_audit.report(self.home, NOW)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), before)

    def test_binary_and_oversized_files_are_skipped_without_raising(self):
        big = self._write("huge.json", "x")
        big.write_bytes(b"\x00" * (state_audit.MAX_READ_BYTES + 1))
        self.assertEqual(state_audit.report(self.home, NOW)["findings"], [])

    def test_the_quarantine_dir_itself_is_never_rescanned(self):
        self._write("backups/quarantine-old/slack_mcp.marker", FUTURE)
        self.assertEqual(state_audit.report(self.home, NOW)["findings"], [])

    def test_a_missing_state_dir_is_not_an_error(self):
        empty = self.home / "nowhere"
        result = state_audit.report(empty, NOW)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["scanned"], 0)


class ApplyTestCase(_HomeMixin, unittest.TestCase):
    def test_apply_moves_the_file_and_leaves_a_manifest(self):
        src = self._write("slack_mcp.marker", FUTURE)
        result = state_audit.apply(self.home, NOW)

        self.assertFalse(src.exists(), "原文件必须离开 state/ 根")
        target = Path(result["quarantine_dir"])
        self.assertEqual((target / "slack_mcp.marker").read_text(encoding="utf-8"),
                         FUTURE, "内容一个字节都不许改——搬走不是删掉")
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["moved"][0]["rel"], "state/slack_mcp.marker")
        self.assertEqual(len(manifest["moved"][0]["sha256"]), 64)
        self.assertEqual(manifest["moved"][0]["stamps"], [FUTURE])

    def test_the_quarantine_dir_lives_under_state_backups(self):
        self._write("slack_mcp.marker", FUTURE)
        result = state_audit.apply(self.home, NOW)
        self.assertEqual(Path(result["quarantine_dir"]).parent,
                         self.home / "state" / "backups")

    def test_an_existing_quarantine_dir_is_never_overwritten(self):
        (self.state / "backups" / ("quarantine-%s" % state_audit._stamp_slug(NOW))
         ).mkdir(parents=True)
        self._write("slack_mcp.marker", FUTURE)
        result = state_audit.apply(self.home, NOW)
        self.assertTrue(Path(result["quarantine_dir"]).name.endswith("-2"))

    def test_work_seq_is_left_in_place_by_apply_too(self):
        src = self._write("work_seq.json", json.dumps({"next": 8154, "at": FUTURE}))
        result = state_audit.apply(self.home, NOW)
        self.assertTrue(src.exists())
        self.assertEqual(result["quarantined"], [])
        self.assertIsNone(result["quarantine_dir"])

    def test_a_clean_tree_moves_nothing(self):
        self._write("slack_mcp.marker", PAST)
        result = state_audit.apply(self.home, NOW)
        self.assertEqual(result["quarantined"], [])
        self.assertFalse((self.state / "backups").exists())


class CliTestCase(_HomeMixin, unittest.TestCase):
    def _run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = state_audit.main(["--home", str(self.home), *args])
        return rc, buf.getvalue()

    def test_report_mode_is_the_default_and_moves_nothing(self):
        src = self._write("slack_mcp.marker", FUTURE)
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("future-stamped=1", out)
        self.assertIn("--apply", out)
        self.assertTrue(src.exists())

    def test_apply_mode_reports_where_it_put_things(self):
        self._write("slack_mcp.marker", FUTURE)
        rc, out = self._run("--apply")
        self.assertEqual(rc, 0)
        self.assertIn("quarantined 1 file(s)", out)

    def test_json_mode_is_one_parseable_object(self):
        self._write("slack_mcp.marker", FUTURE)
        rc, out = self._run("--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["findings"][0]["stamps"], [FUTURE])

    def test_report_only_rows_are_labelled_for_a_human(self):
        self._write("work_seq.json", json.dumps({"at": FUTURE}))
        _rc, out = self._run()
        self.assertIn("report-only", out)


if __name__ == "__main__":
    unittest.main()
