"""§63.15（issue #440 第 3 件，源自 #332）：模型超时随转写长度伸缩，§63.8 的「丢了」判线跟着重定。

`act/recap.LLM_TIMEOUT_S` 曾是定值 240 s：4,493 词的那场首次生成超时、靠下一轮重试
才落稿，6,160 词的那场用了 213 s、离定值只差 27 s。自此一次模型调用的超时是
`recap_timing.llm_timeout_s(words)`（地板 = 原来的定值、每千词加一段、封在天花板下），
`fill_record` 按这份转写的词数把它交给 `generate_lines`（重试用同一个数）；而 §63.8 的
`LOST_AFTER_S` 当时恰等于「锁等待 + 模型 × 重试」的上界，所以 `recap_requests` 的判线
也按同一行的 `transcript_words` 算（`lost_after_s(words)`），并把算出来的秒数 add-only
地写进回执 `generate_request.lost_after_s`——页面据它说「超过 N 分钟」而不是写死 10。
词数未知（OPEN 行的阶段稿 / 老记录）= 地板 = 原来的 10 分钟，一字不变。
"""
import datetime as _dt
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import recap
from act.lib import config, notify
from act.lib import recap_requests as requests
from act.lib import recap_sessions as rs
from act.lib import recap_store as store
from act.lib import recap_timing as timing

KEY = fx.KEY
MIN = 60.0


class FormulaTestCase(unittest.TestCase):
    """纯函数：地板 / 斜率 / 天花板 / 坏输入，以及两条线之间的恒等式。"""

    def test_the_floor_is_the_old_fixed_value_and_short_transcripts_sit_on_it(self):
        self.assertEqual(timing.LLM_TIMEOUT_BASE_S, 240.0)
        self.assertEqual(timing.llm_timeout_s(0), 240.0)
        self.assertEqual(timing.llm_timeout_s(None), 240.0)
        # 地板之上按千词线性加：300 词（MIN_TRANSCRIPT_WORDS）多 18 s，6,160 词多 369.6 s
        self.assertAlmostEqual(timing.llm_timeout_s(300), 240.0 + 60.0 * 0.3)
        self.assertAlmostEqual(timing.llm_timeout_s(6160), 240.0 + 60.0 * 6.16)
        # #332 的那场（4,493 词）自此有 240 + 269.58 s，而不是被 240 s 掐断
        self.assertGreater(timing.llm_timeout_s(4493), 480.0)

    def test_the_ceiling_holds_and_junk_word_counts_fall_to_the_floor(self):
        self.assertEqual(timing.llm_timeout_s(10 ** 6), timing.LLM_TIMEOUT_MAX_S)
        self.assertEqual(timing.LLM_TIMEOUT_MAX_S, 900.0)
        for junk in (True, False, "6160", [], {}, -5, -0.5):
            self.assertEqual(timing.llm_timeout_s(junk), timing.LLM_TIMEOUT_BASE_S, junk)
        # 浮点词数照算（记录上手改成 1200.0 也不崩）
        self.assertAlmostEqual(timing.llm_timeout_s(1200.0), 240.0 + 72.0)

    def test_lost_after_is_lock_wait_plus_the_model_calls_of_one_run(self):
        # §63.8 原文：「10 分钟 = 一次成功生成的上界：锁等待 120 s + 模型 240 s × 重试」——
        # 词数未知时恒等式与那 10 分钟逐字相同
        self.assertEqual(timing.MODEL_CALLS_PER_RUN, 2)
        self.assertEqual(timing.lost_after_s(), 120.0 + 2 * 240.0)
        self.assertEqual(timing.lost_after_s(), 600.0)
        self.assertEqual(requests.LOST_AFTER_S, timing.lost_after_s())
        for words in (0, 300, 4493, 6160, 10 ** 6):
            self.assertEqual(timing.lost_after_s(words),
                             timing.LOCK_WAIT_S + timing.MODEL_CALLS_PER_RUN * timing.llm_timeout_s(words))

    def test_recap_takes_its_lock_wait_and_floor_from_the_one_truth(self):
        self.assertEqual(recap.LOCK_WAIT_S, timing.LOCK_WAIT_S)
        self.assertEqual(recap.LLM_TIMEOUT_S, timing.LLM_TIMEOUT_BASE_S)


class RunnerTimeoutTestCase(unittest.TestCase):
    """真 `fill_record`：runner 拿到的 timeout 是这份转写的词数算出来的，重试也是同一个数。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-timing-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.calls = []
        self.replies = [fx.good_output()]
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self._runner, cfg=self.cfg)

    def _runner(self, argv, **kwargs):
        self.calls.append(dict(kwargs))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return subprocess.CompletedProcess(argv, 0, stdout=reply, stderr="")

    def _closed_round(self):
        return recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)

    def test_the_call_timeout_matches_the_transcripts_word_count(self):
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)
        self._closed_round()
        rec = store.load_recap(KEY)
        self.assertGreaterEqual(rec["transcript_words"], 300)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["timeout"], timing.llm_timeout_s(rec["transcript_words"]))
        # 词数在地板之上 → 真的比定值多（否则这条判例钉的只是地板）
        self.assertGreater(self.calls[0]["timeout"], timing.LLM_TIMEOUT_BASE_S)

    def test_a_long_transcript_gets_a_longer_budget_and_the_retry_uses_the_same_one(self):
        # 20 分钟里每分钟 8 行 × 12 词 ≈ 1,900 词——超过一千词，斜率看得见
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20, rows_per_minute=8)
        # 第一次回一份带转述的稿（校验不过 → 重试一次），第二次干净
        self.replies = [fx.good_output(en_tail=" as Ann said"), fx.good_output()]
        self._closed_round()
        rec = store.load_recap(KEY)
        self.assertGreater(rec["transcript_words"], 1000)
        self.assertEqual(len(self.calls), 2)
        expected = timing.llm_timeout_s(rec["transcript_words"])
        self.assertEqual([c["timeout"] for c in self.calls], [expected, expected])
        self.assertGreater(expected, timing.llm_timeout_s(300))


class LostLineTestCase(unittest.TestCase):
    """§63.8 的回执：判线按这一行的词数伸缩，秒数 add-only 地随回执发出。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-lost-")
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        mock.patch.object(config, "STATE_DIR", state).start()
        mock.patch.object(requests, "REQUESTS_PATH", state / "recap_requests.json").start()
        self.addCleanup(mock.patch.stopall)
        self.t0 = _dt.datetime(2026, 9, 21, 19, 0, 0, tzinfo=_dt.timezone.utc)
        requests.record(KEY, "running", now=self.t0)

    def _at(self, seconds: float):
        return self.t0 + _dt.timedelta(seconds=seconds)

    def test_unknown_word_count_keeps_the_old_ten_minute_line(self):
        floor = timing.lost_after_s()
        inside = requests.projection(KEY, None, now=self._at(floor))
        outside = requests.projection(KEY, None, now=self._at(floor + 1))
        self.assertEqual((inside["state"], outside["state"]), ("running", "lost"))
        self.assertEqual(inside["lost_after_s"], 600)
        # 老 daemon 的形状照旧在（add-only：只多一个键）
        self.assertEqual(set(inside), {"requested_at", "state", "note", "lost_after_s"})

    def test_a_long_transcript_moves_the_line_out_and_the_receipt_says_so(self):
        words = 6160
        line = timing.lost_after_s(words)
        self.assertGreater(line, 600.0)
        still = requests.projection(KEY, None, now=self._at(601), words=words)
        self.assertEqual(still["state"], "running")     # 10 分钟过了，但这份转写的上界没过
        self.assertEqual(still["lost_after_s"], int(line))
        gone = requests.projection(KEY, None, now=self._at(line + 1), words=words)
        self.assertEqual(gone["state"], "lost")
        # 落笔了就是 done，与判线无关
        self.assertEqual(requests.projection(KEY, "2026-09-21T19:00:30Z", now=self._at(5),
                                             words=words)["state"], "done")

    def test_the_projection_row_feeds_its_own_word_count_into_the_line(self):
        rec = store.new_record(rs.Session(start=1756669000.0, end=1756670200.0, frames=40,
                                          audio_rows=30, app="zoom", events=[]), KEY, rs.CLOSED)
        rec["transcript_words"] = 6160
        store.save_recap(rec)
        row = {r["key"]: r for r in store.projection()}[KEY]
        self.assertEqual(row["generate_request"]["lost_after_s"], int(timing.lost_after_s(6160)))
        # OPEN 行（还没有文件、没有词数）= 地板
        state = store.new_state({"frames": 0, "audio": 0}, "2026-09-21T19:00:00Z")
        open_key = "meeting:2026-09-21T1200-teams"
        state["open"] = [dict(store.new_record(rs.Session(start=1758456000.0, end=1758457200.0,
                                                          frames=3, audio_rows=0, app="teams",
                                                          events=[]), open_key, rs.OPEN))]
        store.save_state(state)
        requests.record(open_key, "running", now=self.t0)
        open_row = {r["key"]: r for r in store.projection()}[open_key]
        self.assertEqual(open_row["generate_request"]["lost_after_s"], 600)


if __name__ == "__main__":
    unittest.main()
