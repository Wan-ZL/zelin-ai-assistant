"""「重新生成 / 现在生成」回执的 Python 半边（CONTRACT §63.8，issue #297）：inbox ``recap_generate``
→ actd ``_spawn_recap`` 先取 requested_at 再分离起 ``act.recap --generate``、记台账
``state/recap_requests.json``（actd 单写者、TTL + cap 有界）→ ``recaps[]`` 行的 add-only
``generate_request``（running / done / noop / lost，纯磁盘真值函数：done ⇔ 文件的
generated_at ≥ requested_at）。末尾一条走真 ``recap.generate``（fixture DB + 假 runner）
钉住「子进程落笔即 done」的时序契约。绝不真 spawn、绝不真调模型。
"""
import datetime as _dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import actd, recap
from act.lib import config, dashboard, detached, notify
from act.lib import recap_requests as requests
from act.lib import recap_sessions as rs
from act.lib import recap_store as store

KEY = "meeting:2026-08-31T1256-zoom"
NOW = _dt.datetime(2026, 9, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(**kw):
    return _iso(NOW - _dt.timedelta(**kw))


class SandboxCase(unittest.TestCase):
    """STATE_DIR 与台账路径都指向一个临时目录（模块常量在 import 时已定型，逐个 patch）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-req-")
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        mock.patch.object(config, "STATE_DIR", state).start()
        mock.patch.object(requests, "REQUESTS_PATH", state / "recap_requests.json").start()
        self.addCleanup(mock.patch.stopall)

    def _session(self, start=1756669000.0, app="zoom"):
        return rs.Session(start=start, end=start + 1200, frames=40, audio_rows=30, app=app, events=[])


class LedgerTestCase(SandboxCase):
    def test_record_running_writes_one_entry_per_key(self):
        requests.record(KEY, "running", now=NOW)
        data = json.loads(requests.REQUESTS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data, {KEY: {"requested_at": _iso(NOW), "launch": "running", "note": None}})
        # 同键再按 = 覆盖，不堆积
        requests.record(KEY, "running", requested_at=_ago(seconds=-5), now=NOW)
        self.assertEqual(list(requests.load()), [KEY])
        self.assertEqual(requests.load()[KEY]["requested_at"], _ago(seconds=-5))

    def test_record_noop_carries_launch_failed(self):
        requests.record(KEY, "noop", now=NOW)
        self.assertEqual(requests.load()[KEY], {"requested_at": _iso(NOW), "launch": "noop", "note": "launch_failed"})

    def test_ledger_is_bounded_by_ttl_and_cap(self):
        requests.REQUESTS_PATH.write_text(json.dumps({
            "meeting:2026-08-01T0900-zoom": {"requested_at": _ago(hours=25), "launch": "running", "note": None},
            "meeting:2026-08-02T0900-zoom": {"requested_at": _ago(hours=23), "launch": "running", "note": None},
            "junk": "not-a-dict",
            "meeting:2026-08-03T0900-zoom": {"launch": "running", "note": None},   # no requested_at
        }), encoding="utf-8")
        requests.record(KEY, "running", now=NOW)
        self.assertEqual(sorted(requests.load()), sorted(["meeting:2026-08-02T0900-zoom", KEY]))
        for i in range(requests.CAP + 10):
            requests.record("meeting:2026-07-%02dT%02d00-zoom" % (1 + i // 24, i % 24), "running",
                            requested_at=_iso(NOW - _dt.timedelta(minutes=i + 1)), now=NOW)
        data = requests.load()
        self.assertEqual(len(data), requests.CAP)
        self.assertIn(KEY, data)                                   # newest survive the cap
        self.assertNotIn("meeting:2026-07-03T2100-zoom", data)     # the oldest fell off

    def test_corrupt_or_missing_ledger_reads_as_empty_and_write_failures_are_swallowed(self):
        self.assertEqual(requests.load(), {})
        requests.REQUESTS_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(requests.load(), {})
        requests.REQUESTS_PATH.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(requests.load(), {})
        with mock.patch.object(requests.os, "replace", side_effect=OSError("disk full")):
            requests.record(KEY, "running", now=NOW)                # no raise
        self.assertEqual(requests.load(), {})


class ProjectionTestCase(SandboxCase):
    def _ledger(self, launch="running", minutes_ago=1, note=None):
        return {KEY: {"requested_at": _ago(minutes=minutes_ago), "launch": launch, "note": note}}

    def test_no_request_or_junk_is_null(self):
        self.assertIsNone(requests.projection(KEY, None, {}, NOW))
        self.assertIsNone(requests.projection(KEY, None, {KEY: "junk"}, NOW))
        self.assertIsNone(requests.projection(KEY, None, {KEY: {"launch": "running"}}, NOW))
        self.assertIsNone(requests.projection(KEY, None, {KEY: {"requested_at": "yesterday", "launch": "running"}}, NOW))
        self.assertIsNone(requests.projection(None, None, self._ledger(), NOW))

    def test_running_until_the_file_is_stamped_after_the_request(self):
        ledger = self._ledger()
        self.assertEqual(requests.projection(KEY, None, ledger, NOW)["state"], "running")
        self.assertEqual(requests.projection(KEY, _ago(minutes=5), ledger, NOW)["state"], "running")
        out = requests.projection(KEY, _ago(minutes=1), ledger, NOW)   # same second as the request = done
        self.assertEqual(out, {"requested_at": _ago(minutes=1), "state": "done", "note": None})
        self.assertEqual(requests.projection(KEY, _iso(NOW), ledger, NOW)["state"], "done")

    def test_lost_after_the_budget_without_a_new_version(self):
        ledger = self._ledger(minutes_ago=11)
        self.assertEqual(requests.projection(KEY, None, ledger, NOW)["state"], "lost")
        self.assertEqual(requests.projection(KEY, _ago(hours=2), ledger, NOW)["state"], "lost")
        # 但只要文件在请求之后落过笔，就是 done——哪怕很久以后才看
        self.assertEqual(requests.projection(KEY, _ago(minutes=10), ledger, NOW)["state"], "done")
        self.assertEqual(requests.projection(KEY, None, self._ledger(minutes_ago=10), NOW)["state"], "running")

    def test_noop_carries_the_note_and_ignores_the_file(self):
        out = requests.projection(KEY, _iso(NOW), self._ledger(launch="noop", note="launch_failed"), NOW)
        self.assertEqual((out["state"], out["note"]), ("noop", "launch_failed"))
        self.assertIsNone(requests.projection(KEY, None, self._ledger(launch="noop", note=""), NOW)["note"])

    def test_past_the_ttl_the_receipt_is_null_again(self):
        self.assertIsNone(requests.projection(KEY, None, self._ledger(minutes_ago=25 * 60), NOW))
        self.assertEqual(requests.projection(KEY, None, self._ledger(minutes_ago=23 * 60), NOW)["state"], "lost")

    def test_reads_the_ledger_from_disk_when_not_handed_one(self):
        requests.record(KEY, "running", requested_at=_ago(minutes=1), now=NOW)
        self.assertEqual(requests.projection(KEY, None, now=NOW)["state"], "running")


class StoreProjectionTestCase(SandboxCase):
    def test_rows_carry_generate_request_add_only(self):
        # store.projection() 读真钟（不可注入 now），所以这里全程用真钟算相对时间
        wall = _dt.datetime.now(_dt.timezone.utc)
        rec = store.new_record(self._session(), KEY, rs.CLOSED)
        rec.update({"en": ["Decided: x"], "zh": ["定了：x"], "version": 1,
                    "generated_at": _iso(wall - _dt.timedelta(hours=1))})
        store.save_recap(rec)
        other = "meeting:2026-08-31T1600-zoom"
        state = store.new_state({"frames": 1, "audio": 1}, "now")
        state["open"] = [store.new_record(self._session(start=1756680000.0), other, rs.OPEN)]
        store.save_state(state)
        rows = {r["key"]: r for r in store.projection()}
        self.assertIsNone(rows[KEY]["generate_request"])          # 没请求过 = null，键恒在
        self.assertIsNone(rows[other]["generate_request"])
        requests.record(KEY, "running", requested_at=_iso(wall), now=wall)
        requests.record(other, "running", requested_at=_iso(wall), now=wall)
        rows = {r["key"]: r for r in store.projection()}
        self.assertEqual(rows[KEY]["generate_request"]["state"], "running")     # 文件的 generated_at 更早
        self.assertEqual(rows[other]["generate_request"]["state"], "running")   # OPEN 行还没有文件
        rec["generated_at"] = _iso(wall + _dt.timedelta(seconds=1))
        rec["version"] = 2
        store.save_recap(rec)
        self.assertEqual({r["key"]: r for r in store.projection()}[KEY]["generate_request"]["state"], "done")
        # lost / noop 也走同一条投影路
        requests.record(other, "running", requested_at=_iso(wall - _dt.timedelta(minutes=11)), now=wall)
        requests.record(KEY, "noop", requested_at=_iso(wall), now=wall)
        rows = {r["key"]: r for r in store.projection()}
        self.assertEqual(rows[other]["generate_request"]["state"], "lost")
        self.assertEqual((rows[KEY]["generate_request"]["state"], rows[KEY]["generate_request"]["note"]),
                         ("noop", "launch_failed"))

    def test_corrupt_ledger_never_breaks_the_projection_or_the_dashboard(self):
        store.save_recap(store.new_record(self._session(), KEY, rs.CLOSED))
        requests.REQUESTS_PATH.write_text("{not json", encoding="utf-8")
        rows = store.projection()
        self.assertEqual([r["key"] for r in rows], [KEY])
        self.assertIsNone(rows[0]["generate_request"])
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(), archived=[])
        self.assertIsNone(dash["recaps"][0]["generate_request"])

    def test_recap_json_on_disk_stays_free_of_the_receipt(self):
        # 回执住台账、只在投影里合并——recap 文件（act/recap.py 独写）一个字都不多
        store.save_recap(store.new_record(self._session(), KEY, rs.CLOSED))
        requests.record(KEY, "running", now=NOW)
        store.projection()
        self.assertNotIn("generate_request", store.load_recap(KEY))


class ActdTestCase(SandboxCase):
    def setUp(self):
        super().setUp()
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        self.spawned = []
        mock.patch.object(detached, "spawn", lambda argv, log_name: self.spawned.append((argv, log_name))).start()

    def _drop(self, name, decision):
        path = config.INBOX_DIR / name
        path.write_text(json.dumps(dict(decision, ts="2026-09-14T00:00:00Z")), encoding="utf-8")
        return path

    def test_recap_generate_stamps_requested_at_before_launch_and_records_after(self):
        # 戳必须贴着**真实**此刻取：`record()` 不带 now → 按真实时钟剪 TTL_S 条，
        # 写死的 2026-09-14T12:00:00Z 只在那天之后 24 小时内活着（这条判例
        # 2026-09-15 就因此变红——测试不许有保质期）。
        STAMP = _iso(_dt.datetime.now(_dt.timezone.utc))
        events = []
        real_launch = detached.launch

        def iso_now(now=None):
            events.append("stamp")
            return STAMP

        def launch(argv, log_name, label, log=None):
            events.append(("launch", requests.load()))   # 台账在起子进程那一刻还是空的（起完才记 running / noop）
            return real_launch(argv, log_name, label, log)

        with mock.patch.object(requests, "iso_now", iso_now), mock.patch.object(detached, "launch", launch):
            rc = actd._DETACHED_ACTIONS["recap_generate"]({"action": "recap_generate", "meeting_key": KEY})
        self.assertEqual(rc, "running")
        self.assertEqual(self.spawned, [(["act.recap", "--generate", KEY], "recap.log")])
        # §63.8 done 判据的根：requested_at 在起子进程之前取——子进程自己的 generated_at 只会 ≥ 它
        self.assertEqual([e if isinstance(e, str) else e[0] for e in events], ["stamp", "launch"])
        self.assertEqual(events[1][1], {})
        rec = requests.load()[KEY]
        self.assertEqual((rec["launch"], rec["note"], rec["requested_at"]), ("running", None, STAMP))

    def test_launch_failure_is_recorded_as_noop(self):
        with mock.patch.object(detached, "spawn", side_effect=OSError("no fork")):
            rc = actd._DETACHED_ACTIONS["recap_generate"]({"action": "recap_generate", "meeting_key": KEY, "partial": True})
        self.assertEqual(rc, "noop")
        self.assertEqual((requests.load()[KEY]["launch"], requests.load()[KEY]["note"]), ("noop", "launch_failed"))

    def test_malformed_and_slack_draft_leave_the_ledger_alone(self):
        self.assertEqual(actd._DETACHED_ACTIONS["recap_generate"]({"action": "recap_generate", "meeting_key": "R-1"}), "noop")
        rc = actd._DETACHED_ACTIONS["recap_slack_draft"]({"action": "recap_slack_draft", "meeting_key": KEY,
                                                          "channel_id": "C0123456789"})
        self.assertEqual(rc, "running")
        self.assertEqual(requests.load(), {})
        self.assertEqual(self.spawned[0][0][:2], ["act.recap", "--slack-draft"])

    def test_inbox_pass_writes_the_ledger_and_the_early_dashboard_shows_running(self):
        store.save_recap(store.new_record(self._session(), KEY, rs.CLOSED))
        self._drop("recap-gen.json", {"action": "recap_generate", "meeting_key": KEY, "note": "deadline is Friday"})
        self.assertEqual(actd.process_inbox(), 1)
        self.assertEqual(self.spawned[0][0], ["act.recap", "--generate", KEY, "--note", "deadline is Friday"])
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(), archived=[])
        self.assertEqual(dash["recaps"][0]["generate_request"]["state"], "running")


class _Runner:
    """llm.run's runner seam: a fixed good reply, never a real model; ``fail_after`` = 从第 N 次
    调用起 claude 非零退出（模拟登录过期 / 限流）。"""

    def __init__(self, fail_after=None):
        self.calls = []
        self.fail_after = fail_after

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Not logged in")
        return subprocess.CompletedProcess(argv, 0, stdout=fx.good_output(), stderr="")


class EndToEndTestCase(SandboxCase):
    """真 ``recap.generate`` 落笔 → 投影翻 done（时序契约：子进程的 generated_at ≥ requested_at）。"""

    def setUp(self):
        super().setUp()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.conn = fx.make_db(Path(self.tmp.name) / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.runner = _Runner()

    def _closed_recap(self):
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self.runner, cfg=self.cfg)   # marker
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)
        recap.run_once(now=fx.T0 + 34 * 60, conn=self.conn, runner=self.runner, cfg=self.cfg)
        return store.load_recap(fx.KEY)

    def test_generate_landing_turns_the_receipt_done(self):
        rec = self._closed_recap()
        self.assertEqual(rec["version"], 1)
        request_ts = fx.T0 + 3600
        requested_at = rs.iso_utc(request_ts)
        requests.record(fx.KEY, "running", requested_at=requested_at,
                        now=_dt.datetime.fromtimestamp(request_ts, _dt.timezone.utc))
        at_request = _dt.datetime.fromtimestamp(request_ts + 5, _dt.timezone.utc)
        self.assertEqual(requests.projection(fx.KEY, rec["generated_at"], now=at_request)["state"], "running")
        # 子进程在请求 59 s 后落笔（issue #297 的真实时序）
        out = recap.generate(fx.KEY, note="the deadline is Friday", now=request_ts + 59,
                             conn=self.conn, runner=self.runner, cfg=self.cfg)
        self.assertEqual(out["version"], 2)
        later = _dt.datetime.fromtimestamp(request_ts + 60, _dt.timezone.utc)
        row = {r["key"]: r for r in store.projection()}[fx.KEY]
        self.assertEqual(requests.projection(fx.KEY, row["generated_at"], now=later)["state"], "done")
        self.assertEqual(row["version"], 2)
        # 同一秒落笔也算 done（秒级 ISO-Z，>=）
        self.assertEqual(requests.projection(fx.KEY, requested_at, now=later)["state"], "done")

    def test_model_call_error_crashes_without_landing_so_the_receipt_goes_lost_not_done(self):
        # §63.8 诚实条款：claude 非零退出（登录过期 / 限流）时 generate() 不捕获、不落笔——好的 v1 正文
        # 留在面板上、原因只进 recap.log；回执 running 到 10 分钟后转 lost，绝不伪装 done / generation_failed
        rec = self._closed_recap()
        self.runner.fail_after = len(self.runner.calls)
        request_ts = fx.T0 + 3600
        requested_at = rs.iso_utc(request_ts)
        requests.record(fx.KEY, "running", requested_at=requested_at,
                        now=_dt.datetime.fromtimestamp(request_ts, _dt.timezone.utc))
        with self.assertRaises(Exception):
            recap.generate(fx.KEY, note="fix it", now=request_ts + 5, conn=self.conn, runner=self.runner, cfg=self.cfg)
        after = store.load_recap(fx.KEY)
        self.assertEqual((after["version"], after["quality"], after["en"], after["generated_at"]),
                         (1, rec["quality"], rec["en"], rec["generated_at"]))
        soon = _dt.datetime.fromtimestamp(request_ts + 6, _dt.timezone.utc)
        late = _dt.datetime.fromtimestamp(request_ts + requests.LOST_AFTER_S + 1, _dt.timezone.utc)
        self.assertEqual(requests.projection(fx.KEY, after["generated_at"], now=soon)["state"], "running")
        self.assertEqual(requests.projection(fx.KEY, after["generated_at"], now=late)["state"], "lost")


if __name__ == "__main__":
    unittest.main()
