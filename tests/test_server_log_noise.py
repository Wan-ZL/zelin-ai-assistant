"""server.launchd.log 的噪音闸（CONTRACT §54.2 追记 2026-09-14，issue #314）。

三条法条各自的判例：(1) `_Server.handle_error` 只吞三个连接类异常、其余照打
全栈；(2) 访问日志行首带本地 ISO 时间戳；(3) `/api/board` / `/api/health` 按
`(path, 状态码)` 分桶采样——状态一变立刻写，同码重复才被掐且条数随下一行报
出来，`ZAI_LOG_POLLS=1` 关采样。

注意：tests/test_server_common.py 在 import 期把 `Handler.log_message` 换成
no-op（进程内全局），所以本文件一律直接练模块级纯函数与 handler 方法本身，
不经真请求——发不发那一行是这里唯一要证的事。
"""
import http
import io
import os
import re
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from server import app

# 2026-09-14T14:34:05-0400 / …+0000（`%z` 在 UTC runner 上也一定有偏移）
ISO_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4} ")


def _handler(path, requestline=None):
    """不跑 __init__ 的 Handler + 收行的假 log_message（实例属性盖过类属性）。"""
    handler = app.Handler.__new__(app.Handler)
    handler.path = path
    handler.requestline = requestline or "GET %s HTTP/1.1" % path
    lines = []
    handler.log_message = lambda fmt, *args: lines.append(fmt % args)
    return handler, lines


def _raise_into_handle_error(exc):
    """在 except 块里调 handle_error（它读 sys.exc_info()），回 (stdout, stderr)。"""
    server = app._Server.__new__(app._Server)  # 不 bind 端口
    out, err = io.StringIO(), io.StringIO()
    try:
        raise exc
    except BaseException:
        with redirect_stdout(out), redirect_stderr(err):
            server.handle_error(None, ("127.0.0.1", 53728))
    return out.getvalue(), err.getvalue()


class ServerClassTestCase(unittest.TestCase):
    """make_server 起的就是带 handle_error 的那个子类。"""

    def test_make_server_returns_quiet_server(self):
        home = tempfile.mkdtemp(prefix="zai-log-noise-home-")
        httpd = app.make_server(port=0, home=home, start_watcher=False)
        self.addCleanup(httpd.server_close)
        self.assertIsInstance(httpd, app._Server)
        self.assertIsInstance(httpd, ThreadingHTTPServer)


class HandleErrorTestCase(unittest.TestCase):
    """连接类异常静默；其余异常的全栈一个字不少。"""

    def test_connection_errors_are_silent(self):
        for exc in (ConnectionResetError(54, "Connection reset by peer"),
                    BrokenPipeError(32, "Broken pipe"),
                    ConnectionAbortedError(53, "Software caused abort")):
            with self.subTest(exc=type(exc).__name__):
                out, err = _raise_into_handle_error(exc)
                self.assertEqual(out, "")
                self.assertEqual(err, "")

    def test_real_exception_still_prints_traceback(self):
        out, err = _raise_into_handle_error(ValueError("boom"))
        both = out + err
        self.assertIn("ValueError", both)
        self.assertIn("boom", both)
        self.assertIn("Traceback", both)

    def test_quiet_classes_are_exactly_three(self):
        # 只吞连接类——别的 OSError（磁盘满、EMFILE）仍要炸出来
        self.assertEqual(app._QUIET_CONN_ERRORS,
                         (BrokenPipeError, ConnectionResetError,
                          ConnectionAbortedError))
        out, err = _raise_into_handle_error(OSError(24, "Too many open files"))
        self.assertIn("OSError", out + err)


@unittest.skipUnless(hasattr(time, "tzset"), "fixed local timezone needs time.tzset (POSIX)")
class AccessLineTestCase(unittest.TestCase):
    """访问日志行首的本地 ISO 时间戳（原来只有 `127.0.0.1 - …`）。

    时区钉死（照 `tests/test_report_golden.py` 的 setUp / tearDown idiom）：本节量的是
    **本地**时间戳，而 `_access_line` 走 `time.localtime`（server/app.py:199）——不钉时区，
    注入时钟那条判例就随跑测试的机器漂。注入的 `1757865245.0` = 2025-09-14T15:54:05Z，
    本地偏移一旦 ≥ +08:06（东京 +09 / 悉尼 +10 / 奥克兰 +12）本地日期就翻成 09-15，
    判例必红——与 `registry.restore` 那颗日历炸弹（ad4f0b71 修的那枚 `restored_at` 戳）
    同一形状：判例把时钟冻住，被量的那一端却走另一把尺。只是这颗的自变量是时区不是
    日期，所以 CI（UTC）与 PT 本机上都看不见。钉 `America/New_York`
    是因为它把这一瞬放在 11:54（离两头午夜都 ≈12 h），偏移又正好是 server/app.py:198
    文档里那个 `-0400` 形状。
    """

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_line_starts_with_iso_stamp(self):
        line = app._access_line("127.0.0.1", '"GET /api/board HTTP/1.1" 200 12')
        self.assertRegex(line, ISO_STAMP)
        self.assertTrue(line.endswith(
            '127.0.0.1 - "GET /api/board HTTP/1.1" 200 12\n'))

    def test_stamp_follows_the_injected_clock(self):
        line = app._access_line("127.0.0.1", "x", now=1757865245.0)
        # 整枚戳，不只日期前缀：注入的时钟要逐字生效（日期 + 时分秒 + 偏移）。
        # 只钉日期前缀会放行「日期对、时分秒错」——实测把 server/app.py:199 的
        # `time.localtime(now)` 换成 `time.gmtime(now)`（UTC 时刻配本地偏移）时，
        # 前缀版整个 class 仍 OK，本版红。偏移丢了那种由上面那条正则判例管。
        self.assertTrue(line.startswith("2025-09-14T11:54:05-0400 "), line)


class PollSamplerTestCase(unittest.TestCase):
    """窗口 / 计数 / 每 (path, code) 桶独立（注入时钟，绝不 sleep）。"""

    def test_window_and_suppressed_count(self):
        sampler = app._PollSampler(window=300.0)
        self.assertEqual(sampler.decide("/api/board", 200, 1000.0), (True, 0))
        self.assertEqual(sampler.decide("/api/board", 200, 1100.0), (False, 0))
        self.assertEqual(sampler.decide("/api/board", 200, 1200.0), (False, 0))
        # 窗口一到，下一条写出去并把吃掉的两条报出来
        self.assertEqual(sampler.decide("/api/board", 200, 1301.0), (True, 2))
        self.assertEqual(sampler.decide("/api/board", 200, 1302.0), (False, 0))

    def test_paths_are_independent(self):
        sampler = app._PollSampler(window=300.0)
        self.assertEqual(sampler.decide("/api/board", 200, 0.0), (True, 0))
        self.assertEqual(sampler.decide("/api/health", 200, 0.0), (True, 0))
        self.assertEqual(sampler.decide("/api/board", 200, 1.0), (False, 0))

    def test_codes_are_independent_buckets(self):
        # 状态一变立刻写（新桶），同码的重复才计数——「同一路径反复同一个错误」
        # 也是洪水（owner 日志里 1549 行 /api/board 404）
        sampler = app._PollSampler(window=300.0)
        self.assertEqual(sampler.decide("/api/board", 200, 0.0), (True, 0))
        self.assertEqual(sampler.decide("/api/board", 404, 1.0), (True, 0))
        self.assertEqual(sampler.decide("/api/board", 404, 2.0), (False, 0))
        self.assertEqual(sampler.decide("/api/board", 404, 3.0), (False, 0))
        self.assertEqual(sampler.decide("/api/board", 500, 4.0), (True, 0))
        # 窗口后的第一条 404 把吃掉的两条报出来（错误不会被藏起来）
        self.assertEqual(sampler.decide("/api/board", 404, 400.0), (True, 2))

    def test_default_window_is_five_minutes(self):
        self.assertEqual(app._POLL_WINDOW_SECONDS, 300.0)
        self.assertEqual(app._POLL_SAMPLER.window, 300.0)
        self.assertEqual(app._POLL_PATHS, ("/api/board", "/api/health"))


class QuietCandidateTestCase(unittest.TestCase):
    """哪条访问日志进采样闸：轮询路径 + 认得出的状态码（任何码）。"""

    def test_truth_table(self):
        cases = [
            ("/api/board", 200, True),
            ("/api/health", 304, True),
            ("/api/board", http.HTTPStatus.OK, True),
            # 非 2xx 也进闸：分桶键含状态码，掐的是同码重复而不是错误本身
            ("/api/board", 500, True),
            ("/api/board", 404, True),
            ("/api/health", http.HTTPStatus.NOT_FOUND, True),
            ("/api/board", "-", False),      # 形状不明 → 逐条写
            ("/api/board", None, False),
            ("/api/cards/R-1", 200, False),  # 非轮询路径永远写
            ("/api/cards/R-1", 404, False),
            ("/", 200, False),
        ]
        for path, code, expected in cases:
            with self.subTest(path=path, code=code):
                self.assertIs(app._quiet_candidate(path, code), expected)

    def test_status_int_normalizes_shapes(self):
        self.assertEqual(app._status_int(200), 200)
        self.assertEqual(app._status_int(http.HTTPStatus.NOT_FOUND), 404)
        self.assertEqual(app._status_int("404"), 404)
        self.assertIsNone(app._status_int("-"))
        self.assertIsNone(app._status_int(None))


class LogRequestTestCase(unittest.TestCase):
    """采样闸装在 log_request 上：行的形状、后缀、逐条写的三种情形。"""

    def setUp(self):
        patcher = mock.patch.object(app, "_POLL_SAMPLER",
                                    app._PollSampler(window=300.0))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(app._LOG_POLLS_ENV, None)

    def test_first_poll_logs_then_repeats_are_dropped(self):
        handler, lines = _handler("/api/board")
        with mock.patch.object(app.time, "time", side_effect=[10.0, 11.0, 12.0]):
            for _ in range(3):
                handler.log_request(200, 4096)
        self.assertEqual(lines, ['"GET /api/board HTTP/1.1" 200 4096'])

    def test_suppressed_count_rides_the_next_line(self):
        handler, lines = _handler("/api/board")
        with mock.patch.object(app.time, "time",
                               side_effect=[10.0, 11.0, 12.0, 400.0]):
            for _ in range(4):
                handler.log_request(200, 1)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1],
                         '"GET /api/board HTTP/1.1" 200 1'
                         ' (+2 suppressed in the last 300s)')

    def test_query_string_does_not_dodge_the_sampler(self):
        handler, lines = _handler("/api/board?since=7",
                                  requestline="GET /api/board?since=7 HTTP/1.1")
        with mock.patch.object(app.time, "time", side_effect=[10.0, 11.0]):
            handler.log_request(200, 1)
            handler.log_request(200, 1)
        self.assertEqual(len(lines), 1)

    def test_status_change_logs_immediately(self):
        # 200 → 404 → 500：每次状态变化都是新桶，立刻写（信号不进闸）
        handler, lines = _handler("/api/board")
        with mock.patch.object(app.time, "time",
                               side_effect=[10.0, 11.0, 12.0]):
            handler.log_request(200, 4096)
            handler.log_request(404, "-")
            handler.log_request(500, "-")
        self.assertEqual(lines, ['"GET /api/board HTTP/1.1" 200 4096',
                                 '"GET /api/board HTTP/1.1" 404 -',
                                 '"GET /api/board HTTP/1.1" 500 -'])

    def test_repeated_same_error_is_sampled_with_a_count(self):
        # owner 日志里 1549 行一模一样的 /api/board 404 = 洪水，同样要掐，
        # 但每窗口仍有一行、且带被吃掉的条数（错误不会被藏起来）
        handler, lines = _handler("/api/board")
        with mock.patch.object(app.time, "time",
                               side_effect=[10.0, 11.0, 12.0, 400.0]):
            for _ in range(4):
                handler.log_request(404, "-")
        self.assertEqual(lines, [
            '"GET /api/board HTTP/1.1" 404 -',
            '"GET /api/board HTTP/1.1" 404 -'
            ' (+2 suppressed in the last 300s)'])

    def test_non_poll_path_always_logs(self):
        handler, lines = _handler("/api/actions",
                                  requestline="POST /api/actions HTTP/1.1")
        with mock.patch.object(app.time, "time", return_value=10.0):
            handler.log_request(200, 1)
            handler.log_request(200, 1)
        self.assertEqual(lines, ['"POST /api/actions HTTP/1.1" 200 1'] * 2)

    def test_env_knob_disables_sampling(self):
        handler, lines = _handler("/api/health")
        with mock.patch.dict(os.environ, {app._LOG_POLLS_ENV: "1"}):
            for _ in range(3):
                handler.log_request(200, 1)
        self.assertEqual(len(lines), 3)

    def test_http_status_enum_is_rendered_as_a_number(self):
        handler, lines = _handler("/api/cards/R-1",
                                  requestline="GET /api/cards/R-1 HTTP/1.1")
        handler.log_request(http.HTTPStatus.NOT_FOUND, "-")
        self.assertEqual(lines, ['"GET /api/cards/R-1 HTTP/1.1" 404 -'])


class LogPollsEnvTestCase(unittest.TestCase):
    """ZAI_LOG_POLLS 的取值口径（缺省 = 采样开着）。"""

    def test_truthy_and_falsy(self):
        for raw, expected in (("1", True), ("true", True), ("YES", True),
                              ("on", True), (" 1 ", True),
                              ("0", False), ("", False), ("no", False)):
            with self.subTest(raw=raw):
                self.assertIs(app._log_polls_verbatim({app._LOG_POLLS_ENV: raw}),
                              expected)

    def test_absent_means_sampling_stays_on(self):
        self.assertFalse(app._log_polls_verbatim({}))


if __name__ == "__main__":
    unittest.main()
