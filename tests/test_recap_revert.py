"""§63.9 回退到存着的某一版（issue #300）：``python -m act.recap --revert KEY
--to-version N`` 把那一版的正文写成新的一版。

钉死的行为：当前正文先进 history（**非破坏**——回退本身也能被回退）、version + 1、
新的 ``generated_at``、``reverted_from`` 说出搬自第几版、``quality`` 跟着那一版回来
（§63.9 之前入库的条目没有这个键 → 需复核，永不伪造 ok）、``note`` / ``problems`` /
``repairs`` 清空；这一版不存在 / key 不认识 = 诚实 None 且文件一字不动；inbox
``recap_revert`` 经 actd 的 detached 表分离起子进程（server 永不写纪要文件，§63.6）。
帽的代价也钉在这里：``history`` 已经满 ``HISTORY_CAP`` 条时，回退把当前正文压进历史
**会挤掉最早那一版**（一个回退目标就此老化），面板的文案照这条判例说话。
时钟是注入的，绝不起真 claude（本路径根本不调模型）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import actd, recap
from act.lib import analytics, config, detached
from act.lib import recap_sessions as rs
from act.lib import recap_store as store

KEY = "meeting:2026-08-31T1256-zoom"
T1 = 1756670000.0

V1_EN = ["Decided: Ann owns the data mix", "Split: Ann, Bo", "Deadline: Monday",
         "Changed since last plan: none recorded", "Open: none"]
V1_ZH = ["定了：数据配比归 Ann", "分工：Ann、Bo", "截止：周一",
         "较上次变化：无记录", "待定：无"]
V2_EN = ["Decided: the data mix moves Monday", "Split: not assigned", "Deadline: none set",
         "Changed since last plan: none recorded", "Open: none"]
V2_ZH = ["定了：数据配比周一改", "分工：未分配", "截止：未定",
         "较上次变化：无记录", "待定：无"]


def _session(start: float = 1756669000.0) -> rs.Session:
    return rs.Session(start=start, end=start + 1200, frames=40, audio_rows=30, app="zoom", events=[])


class RevertCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-revert-")
        self.addCleanup(self.tmp.cleanup)
        mock.patch.object(config, "STATE_DIR", Path(self.tmp.name) / "state").start()
        self.addCleanup(mock.patch.stopall)
        self.events = []
        mock.patch.object(analytics, "log_event",
                          side_effect=lambda name, **kw: self.events.append((name, kw))).start()
        store.ensure_dirs()

    def _record(self, *, quality_v1="ok", with_quality=True) -> dict:
        """v1 在 history、v2 是当前版（v2 是那次「所有人都变成未分配」的回归）。"""
        rec = store.new_record(_session(), KEY, rs.CLOSED)
        entry = {"version": 1, "generated_at": "2026-08-31T20:20:00Z", "en": list(V1_EN),
                 "zh": list(V1_ZH), "partial": False}
        if with_quality:
            entry["quality"] = quality_v1
        rec.update({"version": 2, "generated_at": "2026-08-31T20:40:00Z", "en": list(V2_EN),
                    "zh": list(V2_ZH), "quality": store.QUALITY_OK, "note": "tighten the owners",
                    "problems": [{"code": "line_too_long", "lang": "en", "line": 1}],
                    "repairs": [{"lang": "en", "line": 1, "over": 3, "removed": 8}],
                    "history": [entry]})
        store.save_recap(rec)
        return rec

    def _full_record(self) -> dict:
        """history 恰好满 ``HISTORY_CAP`` 条（第 1..N 版），当前是第 N+1 版。"""
        rec = self._record()
        cap = recap.HISTORY_CAP
        rec["history"] = [{"version": n, "generated_at": "2026-08-31T20:%02d:00Z" % n,
                           "en": ["%s (v%d)" % (line, n) for line in V1_EN], "zh": list(V1_ZH),
                           "partial": False, "quality": store.QUALITY_OK}
                          for n in range(1, cap + 1)]
        rec.update({"version": cap + 1, "en": list(V2_EN), "zh": list(V2_ZH)})
        store.save_recap(rec)
        return rec


class RevertBehaviourTestCase(RevertCase):
    def test_revert_restores_the_stored_text_as_a_new_version(self):
        self._record()
        out = recap.revert(KEY, 1, now=T1)
        self.assertIsNotNone(out)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["en"], V1_EN)
        self.assertEqual(rec["zh"], V1_ZH)
        self.assertEqual(rec["version"], 3)                     # 新的一版，不是「变回第 1 版」
        self.assertEqual(rec["generated_at"], rs.iso_utc(T1))   # 注入的钟
        self.assertEqual(rec["reverted_from"], 1)
        self.assertEqual(rec["quality"], store.QUALITY_OK)      # 那一版自己的判决跟着回来
        self.assertIsNone(rec["note"])                          # 备注属于那次生成，不属于这次回退
        self.assertEqual((rec["problems"], rec["repairs"]), ([], []))
        # 非破坏：被换掉的 v2 正文进了 history，v1 还在（回退可以再回退）
        versions = [e["version"] for e in rec["history"]]
        self.assertEqual(versions, [1, 2])
        pushed = rec["history"][1]
        self.assertEqual((pushed["en"], pushed["quality"]), (V2_EN, store.QUALITY_OK))

    def test_a_revert_on_a_full_history_ages_out_the_oldest_stored_version(self):
        """帽满时回退**也**会老化掉一版：当前正文压进 history 就挤掉最早那一条，
        那一版之后再也回不去——面板必须说这件事（`RecapDetail` 的「历史已经满 N 版了」）。"""
        cap = recap.HISTORY_CAP
        self._full_record()
        self.assertIsNotNone(recap.revert(KEY, 3, now=T1))
        rec = store.load_recap(KEY)
        # 第 1 版被挤掉，刚被换下来的第 N+1 版排在最后；条数仍是帽
        self.assertEqual([e["version"] for e in rec["history"]], list(range(2, cap + 2)))
        self.assertEqual(len(rec["history"]), cap)
        # 老化掉的那一版就是一个消失了的回退目标（投影句柄也不再列它）
        self.assertIsNone(recap.revert(KEY, 1, now=T1 + 60))
        self.assertNotIn(1, [h["version"] for h in store.history_versions(store.load_recap(KEY))])

    def test_a_revert_is_itself_revertible(self):
        self._record()
        recap.revert(KEY, 1, now=T1)
        recap.revert(KEY, 2, now=T1 + 60)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["en"], V2_EN)                      # 回到那份「未分配」的正文
        self.assertEqual((rec["version"], rec["reverted_from"]), (4, 2))
        self.assertEqual([e["version"] for e in rec["history"]], [1, 2, 3])

    def test_history_entry_without_quality_falls_back_to_needs_review(self):
        # §63.9 之前写进 history 的条目没有 quality：兜成需复核（「粘贴前请通读一遍」），永不伪造 ok
        self._record(with_quality=False)
        recap.revert(KEY, 1, now=T1)
        self.assertEqual(store.load_recap(KEY)["quality"], store.QUALITY_NEEDS_REVIEW)

    def test_needs_review_comes_back_as_needs_review(self):
        self._record(quality_v1=store.QUALITY_NEEDS_REVIEW)
        recap.revert(KEY, 1, now=T1)
        self.assertEqual(store.load_recap(KEY)["quality"], store.QUALITY_NEEDS_REVIEW)

    def test_metadata_only_analytics(self):
        self._record()
        recap.revert(KEY, 1, now=T1)
        name, payload = self.events[-1]
        self.assertEqual(name, "recap_reverted")
        self.assertEqual((payload["version"], payload["reverted_from"]), (3, 1))
        self.assertNotIn("en", payload)                         # 宪法第 9 条：正文永不进 analytics
        self.assertNotIn("zh", payload)


class RevertMissesTestCase(RevertCase):
    def test_unknown_key_is_none(self):
        self.assertIsNone(recap.revert("meeting:2026-08-31T0000-teams", 1, now=T1))

    def test_unknown_version_leaves_the_record_untouched(self):
        before = self._record()
        self.assertIsNone(recap.revert(KEY, 7, now=T1))
        self.assertEqual(store.load_recap(KEY), before)

    def test_a_history_entry_without_text_is_not_a_version(self):
        rec = self._record()
        rec["history"] = [{"version": 1, "generated_at": None, "en": None, "zh": None}]
        store.save_recap(rec)
        self.assertIsNone(recap.revert(KEY, 1, now=T1))
        self.assertEqual(store.load_recap(KEY)["en"], V2_EN)

    def test_a_bad_target_version_is_none(self):
        self._record()
        self.assertIsNone(recap.revert(KEY, "two", now=T1))
        self.assertEqual(store.load_recap(KEY)["version"], 2)

    def test_cli_exit_codes(self):
        self._record()
        self.assertEqual(recap.main(["--revert", KEY, "--to-version", "1"]), 0)
        self.assertEqual(store.load_recap(KEY)["reverted_from"], 1)
        self.assertEqual(recap.main(["--revert", KEY, "--to-version", "99"]), 1)


class RevertProjectionTestCase(RevertCase):
    def test_the_row_carries_the_version_handles_but_never_the_text(self):
        self._record()
        row = store.projection()[0]
        self.assertEqual(row["history_versions"],
                         [{"version": 1, "generated_at": "2026-08-31T20:20:00Z", "partial": False}])
        self.assertEqual(row["history_count"], 1)
        self.assertNotIn("history", row)
        # 正文不在句柄里（看板每 10 s 一轮，60 行 × 5 版 × 两语言不许上车）
        self.assertNotIn("en", row["history_versions"][0])
        self.assertIsNone(row["reverted_from"])
        recap.revert(KEY, 1, now=T1)
        self.assertEqual(store.projection()[0]["reverted_from"], 1)

    def test_junk_history_entries_are_skipped_by_the_handles(self):
        rec = self._record()
        rec["history"] = [{"version": 1, "en": list(V1_EN), "generated_at": 7, "partial": "yes"},
                          "not a dict", {"version": "two", "en": list(V1_EN)},
                          {"version": 3, "en": []}]
        store.save_recap(rec)
        handles = store.projection()[0]["history_versions"]
        self.assertEqual(handles, [{"version": 1, "generated_at": None, "partial": True}])
        self.assertEqual(store.projection()[0]["history_count"], 4)   # 原始条数不撒谎


class RevertInboxFormTestCase(unittest.TestCase):
    def test_argv_tail(self):
        self.assertEqual(store.inbox_argv({"action": "recap_revert", "meeting_key": KEY, "version": 3}),
                         ["--revert", KEY, "--to-version", "3"])

    def test_malformed_versions_are_none(self):
        for version in (None, 0, -1, True, "2", 2.0, [2]):
            with self.subTest(version=version):
                self.assertIsNone(store.inbox_argv(
                    {"action": "recap_revert", "meeting_key": KEY, "version": version}))
        self.assertIsNone(store.inbox_argv({"action": "recap_revert", "meeting_key": "R-1", "version": 1}))


class RevertDetachedSpawnTestCase(unittest.TestCase):
    def setUp(self):
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def _drop(self, name, decision):
        path = config.INBOX_DIR / name
        path.write_text(json.dumps(decision), encoding="utf-8")
        return path

    def test_recap_revert_spawns_the_recap_module_detached(self):
        path = self._drop("recap-revert.json", {"action": "recap_revert", "meeting_key": KEY,
                                                "version": 2, "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn") as spawn:
            self.assertEqual(actd.process_inbox(), 1)
        spawn.assert_called_once_with(["act.recap", "--revert", KEY, "--to-version", "2"], "recap.log")
        self.assertFalse(path.exists())

    def test_a_malformed_revert_is_a_noop_without_spawn(self):
        self._drop("recap-revert-bad.json", {"action": "recap_revert", "meeting_key": KEY,
                                             "version": 0, "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn") as spawn, \
                mock.patch.object(actd, "_write_applied_ack") as ack:
            actd.process_inbox()
        spawn.assert_not_called()
        ack.assert_called_once_with("recap-revert-bad", "noop")

    def test_revert_stays_out_of_the_generate_request_ledger(self):
        # §63.8 的台账只记「重新生成」：回退不是一次生成，行上不该出现「生成中」
        self._drop("recap-revert-2.json", {"action": "recap_revert", "meeting_key": KEY,
                                           "version": 1, "ts": "2026-09-01T00:00:00Z"})
        with mock.patch.object(detached, "spawn"), \
                mock.patch("act.lib.recap_requests.record") as record:
            actd.process_inbox()
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
