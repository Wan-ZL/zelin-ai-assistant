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
from unittest import mock

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
        self.assertEqual(
            (target / "state" / "slack_mcp.marker").read_text(encoding="utf-8"),
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

    def test_two_offenders_sharing_a_basename_both_survive(self):
        """`state/` 是一棵树，两个子目录下可以同名。拍平成 basename 会让
        `shutil.move` 退化成 `os.rename` 静静盖掉前一份——那是不可恢复的自动删除
        （宪法第 2 条），而 manifest 还照记两行两个 sha256，等于回执在撒谎。"""
        self._write("inbox/R-237.json", '{"who": "A", "at": "%s"}' % FUTURE)
        self._write("fold_receipts/R-237.json", '{"who": "B", "at": "%s"}' % FUTURE)

        result = state_audit.apply(self.home, NOW)
        target = Path(result["quarantine_dir"])

        self.assertEqual(len(result["quarantined"]), 2)
        self.assertIn(
            "A", (target / "state" / "inbox" / "R-237.json").read_text(encoding="utf-8"))
        self.assertIn(
            "B", (target / "state" / "fold_receipts" / "R-237.json"
                  ).read_text(encoding="utf-8"))
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        digests = {row["sha256"] for row in manifest["moved"]}
        self.assertEqual(len(digests), 2, "两行两个 sha256，就得有两份字节还在")

    def test_a_file_named_manifest_json_does_not_eat_the_receipt(self):
        """源文件叫 `manifest.json` 时，拍平会让回执把被隔离的那一份覆盖掉——
        用来让它可恢复的东西反过来销毁了它。"""
        self._write("sub/manifest.json", '{"mine": true, "at": "%s"}' % FUTURE)

        result = state_audit.apply(self.home, NOW)
        target = Path(result["quarantine_dir"])

        self.assertIn(
            "mine", (target / "state" / "sub" / "manifest.json"
                     ).read_text(encoding="utf-8"))
        receipt = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["moved"][0]["rel"], "state/sub/manifest.json")

    def test_the_quarantine_copy_is_not_rescanned_on_the_next_run(self):
        """铺开之后隔离区里多了一层 `state/`——它仍然在 `backups/` 底下，
        下一轮必须照样跳过，否则每跑一次就再搬一次。"""
        self._write("slack_mcp.marker", FUTURE)
        state_audit.apply(self.home, NOW)
        self.assertEqual(state_audit.report(self.home, NOW)["findings"], [])

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


class FailSafeTestCase(_HomeMixin, unittest.TestCase):
    """一把诊断扫帚不许因为环境不配合就把整轮体检掀翻（宪法第 11 条）。

    下面每一条都是「这一份出事只属于它自己」：列不动目录、读不动文件、算不出
    摘要、搬不动、回执写不下去——上层拿到的都是一份能读的报告，不是 traceback。
    """

    def test_a_state_dir_that_cannot_be_listed_scans_to_empty(self):
        class Unlistable:
            def rglob(self, _pattern):
                raise OSError("scandir: permission denied")

        self.assertEqual(list(state_audit._candidates(Unlistable())), [])

    def test_report_survives_a_listing_failure_end_to_end(self):
        """上一条是对 `_candidates` 的单元探针，够不到 `report()` 这一层。真正要
        保证的是**上层拿到一份能读的报告**：所以同一个故障从 CLI 的入口再跑一遍。
        """
        self._write("slack_mcp.marker", FUTURE)
        with mock.patch.object(Path, "rglob",
                               side_effect=OSError("scandir: permission denied")):
            result = state_audit.report(self.home, NOW)
        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["home"], str(self.home))

    def test_a_directory_named_like_a_ledger_is_not_read(self):
        (self.state / "looks_like.json").mkdir()
        self.assertFalse(
            state_audit._scannable(self.state / "looks_like.json", self.state))
        self.assertEqual(state_audit.report(self.home, NOW)["scanned"], 0)

    def test_a_file_that_cannot_be_stat_ed_does_not_escape_as_a_traceback(self):
        """`is_file()` 会抛 `PermissionError`：pathlib 只咽 ENOENT/ENOTDIR/EBADF/
        ELOOP，EACCES 往外抛。真实形状是一个 `chmod 0444` 的子目录（列得出、
        stat 不了）；`0000` / `0111` 反而没事，因为那两种 rglob 自己就列不出来。
        `report()` 得到的必须还是一份能读的报告，不是 traceback（宪法第 11 条）。
        """
        self._write("slack_mcp.marker", FUTURE)
        real = Path.is_file

        def boom(self_):
            if self_.name == "slack_mcp.marker":
                raise PermissionError(13, "Permission denied")
            return real(self_)

        with mock.patch.object(Path, "is_file", boom):
            result = state_audit.report(self.home, NOW)

        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["findings"], [])

    def test_a_home_living_under_a_dir_named_backups_is_still_scanned(self):
        """`backups` 只该在 `state/` 内部数层。拿整条绝对路径去比的话，home 住在
        任何一个叫 `backups` 的目录底下（一个完全正常的路径）都会让每份文件被当成
        隔离区跳过——一棵脏树报成 `scanned=0` 的干净，正是这把扫帚要治的病
        （宪法第 3 条）。
        """
        home = Path(self.tmp.name) / "backups" / "aiassistant"
        state = home / "state"
        state.mkdir(parents=True)
        (state / "slack_mcp.marker").write_text(FUTURE, encoding="utf-8")

        result = state_audit.report(home, NOW)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual([f["rel"] for f in result["findings"]],
                         ["state/slack_mcp.marker"])

    def test_the_quarantine_copy_is_still_skipped_on_the_next_run(self):
        """上一条把判据收窄到 `state/` 内部之后，这条老保证不许跟着丢。"""
        self._write("slack_mcp.marker", FUTURE)
        state_audit.apply(self.home, NOW)
        self.assertEqual(state_audit.report(self.home, NOW)["findings"], [])

    def test_ninety_nine_taken_names_report_instead_of_reusing_one(self):
        """`for n in range(2, 100)` 原来是**掉出去**的：带着一个仍然存在的 target
        回去，`mkdir(exist_ok=True)` 照样成功，这一轮就搬进上一轮的隔离区、连
        manifest 一起盖掉。够到那儿要同一秒 `--apply` 99 次，实际到不了——但
        §82.5 的「永不覆盖」是无条件的，宁可报「建不出隔离区」。
        """
        victim = self._write("slack_mcp.marker", FUTURE)
        slug = "quarantine-%s" % state_audit._stamp_slug(NOW)
        base = self.state / "backups"
        for n in range(1, 100):
            (base / (slug if n == 1 else "%s-%d" % (slug, n))).mkdir(parents=True)

        result = state_audit.apply(self.home, NOW)

        self.assertIn("quarantine dir", result["error"])
        self.assertEqual(result["quarantined"], [])
        self.assertEqual(victim.read_text(encoding="utf-8"), FUTURE,
                         "一个字节都不许搬——原文件还在原地")

    def test_a_file_that_vanished_reads_as_none(self):
        self.assertIsNone(state_audit._read(self.state / "gone.json"))

    def test_a_digest_that_cannot_be_taken_is_none_not_a_raise(self):
        self.assertIsNone(state_audit._sha256(self.state / "gone.json"))

    def test_a_file_that_cannot_be_moved_is_recorded_on_itself(self):
        target = self.state / "backups" / "q"
        target.mkdir(parents=True)
        finding = {"path": str(self.state / "gone.json"),
                   "rel": "state/gone.json", "stamps": [FUTURE]}

        manifest, moved = state_audit._move_all([finding], target)

        self.assertEqual((manifest, moved), ([], []), "搬不动的不许进 manifest")
        self.assertIn("error", finding, "失败要写在它自己头上，不是抛出去")

    def test_a_receipt_that_cannot_be_written_reports_why(self):
        target = self.state / "backups" / "q"
        (target / "manifest.json").mkdir(parents=True)
        self.assertIsNotNone(
            state_audit._write_manifest(target, self.home, NOW, []))

    def test_an_unbuildable_quarantine_dir_moves_nothing(self):
        src = self._write("slack_mcp.marker", FUTURE)
        (self.state / "backups").write_text("我不是目录", encoding="utf-8")

        result = state_audit.apply(self.home, NOW)

        self.assertIn("quarantine dir", result["error"])
        self.assertTrue(src.exists(), "建不出隔离区就一个字节都不许搬")
        self.assertEqual(result["quarantined"], [])
        self.assertIsNone(result["quarantine_dir"])

    def test_a_failed_receipt_does_not_un_move_the_files(self):
        self._write("slack_mcp.marker", FUTURE)
        with mock.patch.object(state_audit, "_write_manifest",
                               return_value="No space left on device"):
            result = state_audit.apply(self.home, NOW)

        self.assertIn("manifest: No space left on device", result["error"])
        self.assertEqual(result["quarantined"], ["state/slack_mcp.marker"])
        self.assertTrue(
            (Path(result["quarantine_dir"]) / "state" / "slack_mcp.marker").exists(),
            "回执写不下去是回执的事——文件已经搬走了，报告必须照实说它在哪")


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

    def test_a_failed_apply_says_so_instead_of_advising_apply(self):
        """机器面（--json）有 error、人看的这面一个字不说，还反过来劝他
        「re-run with --apply」——那是在虚报干净（宪法第 3 条）。"""
        self._write("slack_mcp.marker", FUTURE)
        (self.state / "backups").write_text("我不是目录", encoding="utf-8")

        rc, out = self._run("--apply")

        self.assertEqual(rc, 0, "扫帚不是门，退出码恒 0（§82.5）")
        self.assertIn("quarantine dir", out, "失败的原因必须出现在人看的这一面")
        self.assertNotIn("re-run with --apply", out,
                         "他刚跑过 --apply —— 不许再劝他跑一次")

    def test_a_file_that_could_not_be_moved_is_named_in_the_output(self):
        self._write("slack_mcp.marker", FUTURE)
        with mock.patch.object(state_audit.shutil, "move",
                               side_effect=OSError("Read-only file system")):
            _rc, out = self._run("--apply")
        self.assertIn("Read-only file system", out)

    def test_apply_with_only_report_only_hits_does_not_advise_apply(self):
        """owner 的 live 装机就是这个稳态：`work_seq.json` 被夹具卡抬高过的工号
        按法条永远只上报（工号只许往上走）。搬完之后每一次 `--apply` 都劝他再跑
        一次 `--apply`，等于这条命令永远显得没干完。"""
        self._write("work_seq.json", json.dumps({"next": 8154, "at": FUTURE}))

        _rc, out = self._run("--apply")

        self.assertIn("report-only", out)
        self.assertNotIn("re-run with --apply", out)

    def test_report_mode_with_only_report_only_hits_does_not_advise_apply(self):
        """同一个稳态、**默认**那一跑（文档里写的就是这条命令）：劝 `--apply` 的
        前提是 `--apply` 真会搬走点什么，而这里它可证明地什么都不搬。判据是有没有
        可搬项，不是带没带 `--apply`——只修 `--apply` 半边等于默认跑法还在虚报。
        """
        self._write("work_seq.json", json.dumps({"next": 8154, "at": FUTURE}))

        _rc, out = self._run()

        self.assertIn("report-only", out)
        self.assertNotIn("re-run with --apply", out,
                         "默认这一跑劝的是一个可证明的空操作（宪法第 3 条）")

    def test_report_mode_still_advises_apply_when_something_is_movable(self):
        """反面钉住：真有可搬项时那句劝告必须还在——上一条不许把它劝没了。"""
        self._write("work_seq.json", json.dumps({"next": 8154, "at": FUTURE}))
        self._write("slack_mcp.marker", FUTURE)

        _rc, out = self._run()

        self.assertIn("re-run with --apply", out)


if __name__ == "__main__":
    unittest.main()
