"""看板文件读不动 ≠ 看板文件不在（CONTRACT §49 追记 2026-09-18 / §54.2 同日追记，issue #423）。

覆盖：
- ``board_bytes`` 的 errno 分类真值表：ENOENT 三形（叶子缺席 / 父目录缺席 /
  悬空软链）→ 404 ``NOT_FOUND`` 且**消息与 details 逐字不变**；EACCES / EISDIR /
  ENOTDIR / ELOOP → 503 ``BOARD_UNREADABLE`` 且 details 带 path + errno + strerror；
- live server 上同一次拒绝的 envelope（状态码 / code / details 三样都在 wire 上）；
- errno 诊断行（§54.2 追记）：首条写 / 同 errno 窗内压制并把条数挂到下一条 /
  **换个 errno 立刻写**（采样器的「变化是信号」）/ ``ZAI_LOG_POLLS=1`` 关采样 /
  没有 errno 的受控错误一行都不写 / 日志出 bug 绝不吃掉 envelope。

判据刻意分两层：分类真值表用 monkeypatch 注入真 ``OSError``（确定性，且 chmod 000
对 root 不生效——CI 与 owner 机器上都不许靠运气），另有一条真 chmod 的判例做端到端
佐证，root 下跳过。
"""
from __future__ import annotations

import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (assert_envelope, get_json, start_server,
                                      write_text)

from server import app, board_source
from server.errors import ApiError, BoardUnreadableError, NotFoundError

# board_bytes 对缺席的看板抛的那一句——四份 web fixture 与 shell_ui_probe 逐字引它，
# 分类改造**不许**动它一个字节（§49 追记 2026-09-18）
MISSING_MESSAGE = ("dashboard.json not found — is actd (or the demo "
                   "seeder) pointed at this AIASSISTANT_HOME?")

GOOD_BOARD = json.dumps({"generated_at": "2026-09-18T00:00:00Z"})


def _home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="zai-board-denied-"))
    (home / "state").mkdir(parents=True, exist_ok=True)
    return home


class ErrnoClassificationTestCase(unittest.TestCase):
    """缺席 → 404，读不动 → 503。分界线是 ``FileNotFoundError``，不是 errno 白名单。"""

    def setUp(self):
        self.home = _home()

    # ---- 真的不在：404，且那句话一字不改 ---------------------------------- #
    def test_absent_leaf_is_404_with_the_verbatim_message(self):
        with self.assertRaises(NotFoundError) as ctx:
            board_source.board_bytes(self.home)
        err = ctx.exception
        self.assertEqual(err.status, 404)
        self.assertEqual(err.code, "NOT_FOUND")
        self.assertEqual(err.message, MISSING_MESSAGE)
        self.assertEqual(err.details,
                         {"path": str(self.home / "state" / "dashboard.json")})

    def test_absent_parent_directory_is_also_404(self):
        home = Path(tempfile.mkdtemp(prefix="zai-board-noparent-"))  # 没有 state/
        with self.assertRaises(NotFoundError):
            board_source.board_bytes(home)

    def test_dangling_symlink_is_404(self):
        link = self.home / "state" / "dashboard.json"
        link.symlink_to(self.home / "state" / "nowhere.json")
        with self.assertRaises(NotFoundError):
            board_source.board_bytes(self.home)

    # ---- 读不动：503 + errno --------------------------------------------- #
    def _assert_denied(self, exc: OSError, expected_errno):
        write_text(self.home / "state" / "dashboard.json", GOOD_BOARD)
        with mock.patch.object(Path, "read_bytes", side_effect=exc):
            with self.assertRaises(BoardUnreadableError) as ctx:
                board_source.board_bytes(self.home)
        err = ctx.exception
        self.assertEqual(err.status, 503)
        self.assertEqual(err.code, "BOARD_UNREADABLE")
        self.assertEqual(err.details["errno"], expected_errno)
        self.assertEqual(err.details["path"],
                         str(self.home / "state" / "dashboard.json"))
        # errno / strerror 一律取自异常自己（`os.strerror(None)` 会抛 TypeError，
        # 而那一抛就发生在 500 兜不住它的地方——见诊断行那组判例）
        self.assertEqual(err.details["strerror"], exc.strerror)
        return err

    def test_eacces_is_503_with_errno(self):
        self._assert_denied(
            PermissionError(errno.EACCES, os.strerror(errno.EACCES)),
            errno.EACCES)

    def test_eperm_is_503_with_errno(self):
        # owner 机器上最可能的那一个（TCC / provenance 类的按进程拒绝）
        self._assert_denied(
            PermissionError(errno.EPERM, os.strerror(errno.EPERM)),
            errno.EPERM)

    def test_eio_is_503_although_it_is_not_a_permissionerror(self):
        # 分界线必须是 FileNotFoundError vs OSError——按 PermissionError 分会漏掉 EIO
        self._assert_denied(OSError(errno.EIO, os.strerror(errno.EIO)),
                            errno.EIO)

    def test_eisdir_is_503(self):
        self._assert_denied(
            IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR)),
            errno.EISDIR)

    def test_enotdir_is_503(self):
        self._assert_denied(
            NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR)),
            errno.ENOTDIR)

    def test_eloop_is_503(self):
        self._assert_denied(OSError(errno.ELOOP, os.strerror(errno.ELOOP)),
                            errno.ELOOP)

    def test_the_503_message_never_claims_the_file_exists(self):
        """ELOOP / ENOTDIR / ENAMETOOLONG 那几路谁也没查过文件在不在。

        「在、但读不动」是一句没查就下的断言（宪法第 3 条），真相只有 errno。"""
        err = self._assert_denied(
            OSError(errno.ELOOP, os.strerror(errno.ELOOP)), errno.ELOOP)
        # 消息是英文的，所以只用英文的断言词——中文 needle 在这里恒不命中，
        # 那种「永远通不了也永远不红」的断言等于没写（本轮 review 抓到的原状）
        for claim in ("exists", "is there", "is present", "still there"):
            self.assertNotIn(claim, err.message)
        self.assertIn("errno", err.message)

    def test_errno_none_still_classifies_as_unreadable(self):
        # 裸 OSError()（errno 为 None）也是「不是缺席」——不许因此崩，也不许退回 404。
        # 这一路 details 里两个字段都是 None（诚实：这次读连 errno 都没拿到），
        # 诊断行也就无话可说（见 test_controlled_errors_without_an_errno_write_nothing）
        err = self._assert_denied(OSError(), None)
        self.assertIsNone(err.details["errno"])
        self.assertIsNone(err.details["strerror"])


@unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0,
                 "chmod 000 does not bind root")
class RealChmodEndToEndTestCase(unittest.TestCase):
    """真 chmod 的端到端佐证：同一个 HOME 上「读不动」与「不在」答两句不同的话。"""

    def setUp(self):
        self.home = _home()
        self.dash = self.home / "state" / "dashboard.json"
        write_text(self.dash, GOOD_BOARD)
        _, self.port = start_server(self, self.home)

    def tearDown(self):
        try:                       # 让 tmpdir 之后能被清掉
            os.chmod(self.dash, 0o644)
        except OSError:
            pass

    def test_unreadable_board_is_503_envelope_with_errno(self):
        os.chmod(self.dash, 0o000)
        status, obj = get_json(self.port, "/api/board")
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "BOARD_UNREADABLE")
        details = obj["error"]["details"]
        self.assertEqual(details["errno"], errno.EACCES)
        self.assertEqual(details["path"], str(self.dash))
        self.assertIsInstance(details["strerror"], str)

    def test_health_reports_the_same_errno_and_still_answers_200(self):
        os.chmod(self.dash, 0o000)
        status, obj = get_json(self.port, "/api/health")
        self.assertEqual(status, 200)               # 这一面永不 500
        self.assertIsNone(obj["dashboard"])         # 既有键含义一字不变
        self.assertEqual(obj["dashboard_error"]["errno"], errno.EACCES)

    def test_absent_board_is_still_404_on_the_same_home(self):
        os.chmod(self.dash, 0o644)
        self.dash.unlink()
        status, obj = get_json(self.port, "/api/board")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        self.assertEqual(obj["error"]["message"], MISSING_MESSAGE)


class _FakeHandler:
    """``_send_api_error`` 的最小宿主：只借 log_message / _send_json 两个出口。

    不起真 server——本组判例考的是诊断行的采样与容错，不是 HTTP。"""

    def __init__(self):
        self.lines: "list[str]" = []
        self.sent: "list[tuple[int, dict]]" = []

    def log_message(self, fmt, *args):
        self.lines.append(fmt % args)

    def _send_json(self, status, obj):
        self.sent.append((status, obj))

    _send_api_error = app.Handler._send_api_error


class ErrnoDiagnosticLineTestCase(unittest.TestCase):
    """§54.2 追记 2026-09-18：errno 落一份到日志，同 300 s 窗，桶键含 errno。"""

    def setUp(self):
        # 进程级全局，必须换成本例私有的一份（test_server_log_noise 同款）——
        # 否则同进程里任何一次 live 503 都会把桶先占掉，用例顺序决定结果
        patcher = mock.patch.object(app, "_ERRNO_SAMPLER",
                                    app._PollSampler(window=300.0))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(app._LOG_POLLS_ENV, None)

    @staticmethod
    def _denial(number=errno.EACCES):
        return BoardUnreadableError(
            "cannot read dashboard.json — see details.errno",
            {"path": "/x/state/dashboard.json", "errno": number,
             "strerror": os.strerror(number)})

    def test_first_denial_writes_one_line_with_the_errno(self):
        handler = _FakeHandler()
        with mock.patch.object(app.time, "time", return_value=10.0):
            handler._send_api_error(self._denial())
        self.assertEqual(len(handler.lines), 1)
        self.assertIn("BOARD_UNREADABLE", handler.lines[0])
        self.assertIn("errno=%d" % errno.EACCES, handler.lines[0])
        self.assertIn("path=/x/state/dashboard.json", handler.lines[0])
        # envelope 照发
        self.assertEqual(handler.sent[0][0], 503)

    def test_same_errno_inside_the_window_is_suppressed_then_counted(self):
        handler = _FakeHandler()
        with mock.patch.object(app.time, "time",
                               side_effect=[10.0, 11.0, 12.0, 400.0]):
            for _ in range(4):
                handler._send_api_error(self._denial())
        self.assertEqual(len(handler.lines), 2)
        self.assertIn("(+2 suppressed in the last 300s)", handler.lines[1])
        self.assertEqual(len(handler.sent), 4)      # 每一次都答了

    def test_a_different_errno_writes_immediately(self):
        """EACCES 之后来一条 EIO 必须自己写一行。

        被并进 suppressed 会让读日志的人把 I-O 错误诊断成权限问题——采样器的
        立论本来就是「变化是信号」（§54.2 2026-09-14 追记，按状态码分桶）。"""
        handler = _FakeHandler()
        with mock.patch.object(app.time, "time", side_effect=[10.0, 11.0]):
            handler._send_api_error(self._denial(errno.EACCES))
            handler._send_api_error(self._denial(errno.EIO))
        self.assertEqual(len(handler.lines), 2)
        self.assertIn("errno=%d" % errno.EACCES, handler.lines[0])
        self.assertIn("errno=%d" % errno.EIO, handler.lines[1])

    def test_log_polls_verbatim_turns_the_sampling_off(self):
        os.environ[app._LOG_POLLS_ENV] = "1"
        handler = _FakeHandler()
        with mock.patch.object(app.time, "time", side_effect=[10.0, 11.0, 12.0]):
            for _ in range(3):
                handler._send_api_error(self._denial())
        self.assertEqual(len(handler.lines), 3)

    def test_controlled_errors_without_an_errno_write_nothing(self):
        handler = _FakeHandler()
        with mock.patch.object(app.time, "time", return_value=10.0):
            handler._send_api_error(NotFoundError("not found", {"path": "/x"}))
            handler._send_api_error(ApiError("internal error"))
        self.assertEqual(handler.lines, [])
        self.assertEqual([s for s, _ in handler.sent], [404, 500])

    def test_a_broken_diagnostic_never_costs_the_caller_its_envelope(self):
        """``_send_api_error`` 是从 ``_dispatch`` 的 ``except ApiError`` 子句里调的。

        从那里抛出去的异常同级 ``except Exception`` 兜不住，会逃到
        ``handle_one_request``——客户端连 envelope 都收不到。日志永不赔上回答。"""
        handler = _FakeHandler()
        with mock.patch.object(app, "_errno_diagnostic",
                               side_effect=RuntimeError("logging blew up")):
            handler._send_api_error(self._denial())
        self.assertEqual(handler.lines, [])
        self.assertEqual(handler.sent[0][0], 503)
        self.assertEqual(handler.sent[0][1]["error"]["code"], "BOARD_UNREADABLE")

    def test_errno_none_is_written_as_a_question_mark_never_as_none(self):
        """裸 ``OSError``（拿不到 errno）也要留一行——静默正是本 issue 的病。

        而 ``strerror`` 为 None 时不许把字面 ``None`` 写进日志：读日志的人会在
        「人类可读的原因」那一格看见一个 ``None``。"""
        handler = _FakeHandler()
        bare = BoardUnreadableError(
            "cannot read dashboard.json — see details.errno",
            {"path": "/x/state/dashboard.json", "errno": None, "strerror": None})
        with mock.patch.object(app.time, "time", return_value=10.0):
            handler._send_api_error(bare)
        self.assertEqual(len(handler.lines), 1)
        self.assertIn("errno=?", handler.lines[0])
        self.assertNotIn("None", handler.lines[0])
        self.assertEqual(handler.sent[0][0], 503)

    def test_verbatim_mode_reports_no_suppressed_count_but_loses_none(self):
        """``ZAI_LOG_POLLS=1`` = 采样整个关掉：那时**不碰桶**。

        没有东西在被压制，所以 verbatim 的行不带 suppressed 后缀；而旋钮打开之前
        真被压住的那些条一条不丢——关回去之后的第一条把它们全报出来。日志从此
        既不高报也不静默丢弃。"""
        handler = _FakeHandler()
        with mock.patch.object(app.time, "time",
                               side_effect=[10.0, 11.0, 12.0, 13.0, 400.0]):
            handler._send_api_error(self._denial())           # 首条，写
            handler._send_api_error(self._denial())           # 窗内，真被压制 1
            handler._send_api_error(self._denial())           # 窗内，真被压制 2
            os.environ[app._LOG_POLLS_ENV] = "1"
            handler._send_api_error(self._denial())           # verbatim，照写、不带后缀
            os.environ.pop(app._LOG_POLLS_ENV, None)
            handler._send_api_error(self._denial())           # 出窗，把那 2 条报出来
        self.assertEqual(len(handler.lines), 3)
        self.assertNotIn("suppressed", handler.lines[1])       # verbatim 那一行
        self.assertIn("(+2 suppressed in the last 300s)", handler.lines[2])


class SamplerIsolationTestCase(unittest.TestCase):
    """诊断行的桶不许与访问日志的桶相撞（§54.2 的「桶数有界」仍对后者成立）。

    刻意**不**在 setUp 里替换 ``_ERRNO_SAMPLER``——上面那组替换掉之后，
    「两者不是同一个对象」就恒真了，连 ``_ERRNO_SAMPLER = _POLL_SAMPLER``
    这种真事故也照样绿（本轮 review 实测）。"""

    def test_the_two_samplers_are_distinct_module_globals(self):
        self.assertIsNot(app._ERRNO_SAMPLER, app._POLL_SAMPLER)

    def test_a_diagnostic_never_writes_into_the_access_log_buckets(self):
        before = dict(app._POLL_SAMPLER._seen)
        handler = _FakeHandler()
        err = BoardUnreadableError("x", {"path": "/x", "errno": errno.EACCES,
                                         "strerror": "denied"})
        with mock.patch.object(app.time, "time", return_value=10.0):
            handler._send_api_error(err)
        self.assertEqual(dict(app._POLL_SAMPLER._seen), before)


if __name__ == "__main__":
    unittest.main()
