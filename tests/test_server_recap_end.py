"""§63.16（issue #440 第 4 件，源自 #299 / #332）：会议头部的结束时间可以手改。

头部显示的是录制到的最后一段（2026-09-21 那场显示 12:36，会其实 12:30 结束）。自此
``POST /api/recaps/end {key, end_override: ISO-Z | null}`` 把 owner 定的结束时间写进
server 独写的 ``state/recap/marks.json``（add-only 键 ``end_override``，与「复制 / 已发送 /
忽略」同一个文件、同一套写者分工），投影行 add-only 地带出它（`recap_store._row`；
解析不出 = None，键恒在），页面的表头、行标签与剪贴板表头按它显示。**纯展示层**：
recap 文件里录制到的 ``end`` 一字不动，生成与晚到切片都不读它；``null`` = 回到录制时间。
日历来源今天没有（本 repo 没有任何日历事件源），登记在 §63.7。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, post_json, start_server

from act.lib import config
from act.lib import recap_sessions as rs
from act.lib import recap_store as store

KEY = "meeting:2026-08-31T1256-zoom"
# 与 ProjectionTestCase 的记录同一天：录制到 20:16Z，owner 说会 20:10Z 就散了
START_TS = 1756669000.0                         # 2026-08-31T19:36:40Z（fixture 的会议区间起点）
END = "2026-08-31T20:10:00Z"


class EndOverrideEndpointTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-end-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def marks(self) -> dict:
        p = self.home / "state" / "recap" / "marks.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def test_set_then_clear_next_to_the_other_marks(self):
        post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "copied"})
        status, body = post_json(self.port, "/api/recaps/end", {"key": KEY, "end_override": END})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "key": KEY, "end_override": END})
        entry = self.marks()[KEY]
        self.assertEqual(entry["end_override"], END)
        self.assertTrue(entry["copied_at"])                  # 别的标记原样在（同一个 dict，add-only 键）
        status, body = post_json(self.port, "/api/recaps/end", {"key": KEY, "end_override": None})
        self.assertEqual(status, 200)
        self.assertIsNone(body["end_override"])
        self.assertIsNone(self.marks()[KEY]["end_override"])
        self.assertTrue(self.marks()[KEY]["copied_at"])

    def test_validation_fails_closed(self):
        for payload, code in (
            ({"key": "R-101", "end_override": END}, "INVALID_FIELD"),
            ({"end_override": END}, "INVALID_FIELD"),
            ({"key": KEY}, "INVALID_FIELD"),                                   # 缺键不是 null
            ({"key": KEY, "end_override": "2026-08-31 20:10"}, "INVALID_FIELD"),
            ({"key": KEY, "end_override": "2026-08-31T20:10:00+00:00"}, "INVALID_FIELD"),
            ({"key": KEY, "end_override": "2026-13-45T20:10:00Z"}, "INVALID_FIELD"),  # 形对、不是真时刻
            ({"key": KEY, "end_override": 1756671000}, "INVALID_FIELD"),
            ({"key": KEY, "end_override": END, "start_override": END}, "UNKNOWN_FIELD"),
        ):
            with self.subTest(payload=payload):
                status, body = post_json(self.port, "/api/recaps/end", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, body, code)
        self.assertEqual(self.marks(), {})

    def test_a_corrupt_marks_file_is_replaced_not_crashed(self):
        p = self.home / "state" / "recap" / "marks.json"
        p.parent.mkdir(parents=True)
        p.write_text("{oops", encoding="utf-8")
        status, _body = post_json(self.port, "/api/recaps/end", {"key": KEY, "end_override": END})
        self.assertEqual(status, 200)
        self.assertEqual(self.marks()[KEY]["end_override"], END)

    def test_the_recap_file_is_never_written(self):
        recaps = self.home / "state" / "recap" / "recaps"
        recaps.mkdir(parents=True)
        path = recaps / (KEY.replace(":", "_") + ".json")
        path.write_text(json.dumps({"key": KEY, "end": "2026-08-31T20:16:00Z"}), encoding="utf-8")
        post_json(self.port, "/api/recaps/end", {"key": KEY, "end_override": END})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["end"], "2026-08-31T20:16:00Z")


class ProjectionTestCase(unittest.TestCase):
    """投影行带出手改的结束时间（键恒在），记录上的 end 一字不动。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-end-row-")
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        mock.patch.object(config, "STATE_DIR", state).start()
        self.addCleanup(mock.patch.stopall)
        rec = store.new_record(rs.Session(start=START_TS, end=START_TS + 1200, frames=40,
                                          audio_rows=30, app="zoom", events=[]), KEY, rs.CLOSED)
        store.save_recap(rec)

    def _marks(self, entry) -> None:
        store.marks_path().parent.mkdir(parents=True, exist_ok=True)
        store.marks_path().write_text(json.dumps({KEY: entry}), encoding="utf-8")

    def _row(self) -> dict:
        return {r["key"]: r for r in store.projection()}[KEY]

    def test_the_row_carries_the_override_and_keeps_the_captured_end(self):
        self.assertIsNone(self._row()["end_override"])       # 没改过 = None，键恒在
        self._marks({"end_override": END, "copied_at": "2026-08-31T20:20:00Z"})
        row = self._row()
        self.assertEqual(row["end_override"], END)
        self.assertEqual(row["end"], rs.iso_utc(START_TS + 1200))   # 录制到的 end 原样
        self.assertEqual(store.load_recap(KEY)["end"], rs.iso_utc(START_TS + 1200))

    def test_a_hand_mangled_override_is_none_not_a_crash(self):
        for junk in (7, True, "yesterday", "", [END], {"at": END}):
            with self.subTest(junk=junk):
                self._marks({"end_override": junk})
                self.assertIsNone(self._row()["end_override"])
        self._marks({"end_override": None})
        self.assertIsNone(self._row()["end_override"])

    def test_the_override_does_not_move_the_row_between_lanes(self):
        # 手改的结束时间不是一个「理由」：不归档、不忽略、filed 时刻也不看它
        self._marks({"end_override": END})
        row = self._row()
        self.assertEqual(store.lane(row), "active")
        self.assertFalse(store.filed(row))
        self._marks({"end_override": END, "sent_at": "2026-08-31T21:00:00Z"})
        row = self._row()
        self.assertEqual(store.lane(row), "archived")
        self.assertEqual(store._filed_ts(row), rs.parse_ts("2026-08-31T21:00:00Z"))


if __name__ == "__main__":
    unittest.main()
