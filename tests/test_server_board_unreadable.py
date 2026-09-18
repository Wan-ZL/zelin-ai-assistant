"""读不了 ≠ 不在：``GET /api/board`` 的 errno 分流（CONTRACT §49 追记
2026-09-18，issue #423；§0 宪法第 3 条）。

法条：``server/board_source.board_bytes`` 原先一个光秃秃的 ``except OSError``
把**每一种**读失败都说成 404「dashboard.json not found」。live 机器上
``state/dashboard.json`` 明明在（425 KB、模式正常、同一个解释器在终端读得
出来），2026-09-17 16:44:32 起 server 连着约 1 小时 40 分答 404——而真 errno
被吞在那个 except 里，于是这个错误映射把它藏了好几周，没有任何一个面看得见。
自本判例起两路分开：

- **文件不在**（ENOENT / ENOTDIR / EISDIR——HOME 指错、``state/`` 被写成普通
  文件、``dashboard.json`` 是个目录）→ 404 ``NOT_FOUND``，原文案一字不改
  （§49 2026-09-05 追记的客户端语义与既有判例都靠它）；
- **读不了、而且不是因为它不在**（EACCES / EPERM / EIO…）→ 503
  ``BOARD_UNREADABLE``，``details`` 带 ``path`` + 真 ``errno`` + ``strerror``。
  措辞上不写「文件在」——errno 证不出那个（§49 追记的措辞纪律）。

ENOENT 必须**永远**留在 404 那一侧：``tests/test_server_auth.py`` 用空 HOME
的 404 证明读路径不吃 token 闸，它一旦能答 503，那条判例就不再有区分力。
"""
from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env，防任何 act.* 触真库

from server import app as app_mod
from server import board_source, state_read
from server.errors import BoardUnreadableError, NotFoundError
from tests.test_server_common import (assert_envelope, get_json, seed_scene,
                                      start_server, write_text)

# chmod 000 在 Windows 与 root 下都拦不住——真 EACCES 判例只在别处跑
_NO_REAL_EACCES = (sys.platform == "win32"
                   or getattr(os, "geteuid", lambda: 1)() == 0)


def _home(slug: str) -> Path:
    return Path(tempfile.mkdtemp(prefix="zai-r961-%s-" % slug))


def _deny_reading(name: str, exc: OSError):
    """只对某个文件名拒读的 ``Path.read_bytes`` / ``read_text`` 补丁。

    整类替换（``mock.patch.object(Path, "read_bytes")``）会连 token 文件、静态
    资源一起拒掉；这里按 basename 放行其余，于是判例练的是真的那一条路径。
    """
    real_bytes, real_text = Path.read_bytes, Path.read_text

    def fake_bytes(self, *a, **k):
        if self.name == name:
            raise exc
        return real_bytes(self, *a, **k)

    def fake_text(self, *a, **k):
        if self.name == name:
            raise exc
        return real_text(self, *a, **k)

    return mock.patch.multiple(Path, read_bytes=fake_bytes, read_text=fake_text)


class AbsentErrnoVocabularyTestCase(unittest.TestCase):
    """``state_read.is_absent`` 的词表——这条线决定 404 还是 503。"""

    def test_absent_family(self):
        for code in (errno.ENOENT, errno.ENOTDIR, errno.EISDIR):
            self.assertTrue(state_read.is_absent(OSError(code, "x")),
                            "errno %d 该算「文件不在」" % code)

    def test_denied_family(self):
        for code in (errno.EACCES, errno.EPERM, errno.EIO, errno.ELOOP):
            self.assertFalse(state_read.is_absent(OSError(code, "x")),
                             "errno %d 该算「读不了」" % code)

    def test_errno_less_oserror_is_not_absent(self):
        # 手工合成的 OSError("x") 没有 errno——「不知道」绝不能读成「不在」
        self.assertFalse(state_read.is_absent(OSError("x")))

    def test_the_three_builtin_subclasses_land_where_expected(self):
        # NotADirectoryError / IsADirectoryError 直接继承 OSError 而不是
        # FileNotFoundError——按异常类分流会把它们错判成「读不了」
        self.assertTrue(state_read.is_absent(FileNotFoundError(errno.ENOENT, "x")))
        self.assertTrue(state_read.is_absent(NotADirectoryError(errno.ENOTDIR, "x")))
        self.assertTrue(state_read.is_absent(IsADirectoryError(errno.EISDIR, "x")))
        self.assertFalse(state_read.is_absent(PermissionError(errno.EACCES, "x")))


class FailureDetailTestCase(unittest.TestCase):
    """envelope ``details`` 的两项结构化字段（不是 ``str(exc)``）。"""

    def test_two_fields_from_a_real_oserror(self):
        detail = state_read.failure_detail(PermissionError(1, "Operation not permitted"))
        self.assertEqual(detail, {"errno": 1, "strerror": "Operation not permitted"})

    def test_missing_values_stay_null_not_zero(self):
        # 0 会被读成「没出错」；不知道就答 null
        self.assertEqual(state_read.failure_detail(OSError("x")),
                         {"errno": None, "strerror": None})


class ReadJsonTestCase(unittest.TestCase):
    """``state_read.read_json`` = 旧 ``health._read_json`` 的语义 + 一个 failure。"""

    def setUp(self):
        self.home = _home("readjson")
        self.p = self.home / "state" / "x.json"

    def test_good_json_object(self):
        write_text(self.p, json.dumps({"a": 1}))
        self.assertEqual(state_read.read_json(self.p), ({"a": 1}, None))

    def test_absent_is_not_a_failure(self):
        # 首次安装的正常状态，不是故障源（§47.4 原文不动）
        self.assertEqual(state_read.read_json(self.p), (None, None))

    def test_torn_json_is_not_a_failure(self):
        write_text(self.p, "{not json")
        self.assertEqual(state_read.read_json(self.p), (None, None))

    def test_non_object_top_level_reads_as_none(self):
        write_text(self.p, "[1, 2]")
        self.assertEqual(state_read.read_json(self.p), (None, None))

    def test_non_utf8_bytes_are_torn_not_a_raise(self):
        # UnicodeDecodeError 继承 ValueError 而**不是** OSError：旧代码那句
        # `except (OSError, ValueError)` 顺手接住了它，只写 `except OSError`
        # 就会让一个非 UTF-8 的 state 文件把 health.snapshot() 炸穿
        # （它的 docstring 说「Never raises」、§49 说 /api/health 永不 500）
        self.p.parent.mkdir(parents=True, exist_ok=True)
        self.p.write_bytes(b'{"a": "\xff\xfe"}')
        self.assertEqual(state_read.read_json(self.p), (None, None))

    def test_denied_read_reports_the_errno(self):
        write_text(self.p, "{}")
        with _deny_reading("x.json", PermissionError(1, "Operation not permitted")):
            doc, failure = state_read.read_json(self.p)
        self.assertIsNone(doc)
        self.assertEqual(failure, {"errno": 1, "strerror": "Operation not permitted"})


class BoardBytesSplitTestCase(unittest.TestCase):
    """``board_bytes`` 本体：谁 404、谁 503。"""

    def setUp(self):
        self.home = _home("split")

    def test_absent_file_is_still_not_found(self):
        with self.assertRaises(NotFoundError) as cm:
            board_source.board_bytes(self.home)
        self.assertIn("dashboard.json not found", cm.exception.message)
        self.assertEqual(cm.exception.status, 404)

    def test_state_dir_is_a_regular_file_is_not_found(self):
        # HOME 指错的经典形状：ENOTDIR。旧文案「is actd … pointed at this
        # AIASSISTANT_HOME?」就是为它写的，它必须留在 404 那一侧
        write_text(self.home / "state", "oops")
        with self.assertRaises(NotFoundError):
            board_source.board_bytes(self.home)

    def test_dashboard_is_a_directory_is_not_found(self):
        (self.home / "state" / "dashboard.json").mkdir(parents=True)
        with self.assertRaises(NotFoundError):
            board_source.board_bytes(self.home)

    def test_denied_read_is_503_with_the_errno(self):
        write_text(self.home / "state" / "dashboard.json", "{}")
        with _deny_reading("dashboard.json",
                           PermissionError(1, "Operation not permitted")):
            with self.assertRaises(BoardUnreadableError) as cm:
                board_source.board_bytes(self.home)
        err = cm.exception
        self.assertEqual((err.status, err.code), (503, "BOARD_UNREADABLE"))
        self.assertEqual(err.details["errno"], 1)
        self.assertEqual(err.details["strerror"], "Operation not permitted")
        self.assertEqual(err.details["path"],
                         str(self.home / "state" / "dashboard.json"))
        # 「不在」那句话绝不能出现在「读不了」上——这正是 issue #423 的谎
        self.assertNotIn("not found", err.message)

    def test_errno_less_oserror_is_503_with_nulls(self):
        write_text(self.home / "state" / "dashboard.json", "{}")
        with _deny_reading("dashboard.json", OSError("weird")):
            with self.assertRaises(BoardUnreadableError) as cm:
                board_source.board_bytes(self.home)
        self.assertIsNone(cm.exception.details["errno"])
        self.assertIsNone(cm.exception.details["strerror"])

    def test_a_readable_board_still_passes_through_byte_for_byte(self):
        raw = '{"generated_at": "2026-09-18T00:00:00Z"}'
        write_text(self.home / "state" / "dashboard.json", raw)
        self.assertEqual(board_source.board_bytes(self.home),
                         raw.encode("utf-8"))


class BoardRouteTestCase(unittest.TestCase):
    """路由层：envelope 形状 + 真 EACCES 端到端。"""

    def setUp(self):
        self.home = _home("route")
        seed_scene(self.home, "initial")

    def test_denied_read_answers_503_envelope(self):
        _, port = start_server(self, self.home)
        with _deny_reading("dashboard.json",
                           PermissionError(13, "Permission denied")):
            status, obj = get_json(port, "/api/board")
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "BOARD_UNREADABLE")
        self.assertEqual(obj["error"]["details"]["errno"], 13)

    def test_a_good_board_is_unaffected(self):
        _, port = start_server(self, self.home)
        status, obj = get_json(port, "/api/board")
        self.assertEqual(status, 200)
        self.assertIn("generated_at", obj)

    @unittest.skipIf(_NO_REAL_EACCES, "chmod 000 does not block on Windows / root")
    def test_real_chmod_000_is_503_not_404(self):
        dash = self.home / "state" / "dashboard.json"
        dash.chmod(0)
        self.addCleanup(dash.chmod, 0o600)
        with self.assertRaises(PermissionError):   # 前提：这台机器上确实是 EACCES
            dash.read_bytes()
        _, port = start_server(self, self.home)
        status, obj = get_json(port, "/api/board")
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "BOARD_UNREADABLE")
        self.assertEqual(obj["error"]["details"]["errno"], errno.EACCES)


class AccessLineErrnoTestCase(unittest.TestCase):
    """errno 真的走到了访问行上——**经一次真请求**（§54.2 追记 2026-09-18）。

    ``tests/test_server_common`` 在 import 期把 ``Handler.log_message`` 换成
    no-op（进程内全局），所以本仓库没有任何判例看得见真请求产生的那一行。
    后果实测过：把 ``_send_api_error`` 里挂注记与发响应的两行**对调**，973 条
    server 判例**全绿**——一次一行的重构就能无声地把这个功能退回去，日志重新
    变回 §54.2 要它别再说的那句谎。这条判例就是那根接线本身：真 server、真
    socket、真 503，抓那一行。
    """

    def setUp(self):
        self.home = _home("accesslog")
        write_text(self.home / "state" / "dashboard.json", "{}")
        # 采样器是**进程级**的（app._POLL_SAMPLER）：同一个 (path, 状态码) 桶被
        # 本进程里跑在前面的判例占过，这里那一行就会被掐掉、captured 空手而归。
        # 每条判例换一只新的——与 tests/test_server_log_noise.py 的 _fresh_sampler 同法。
        fresh = mock.patch.object(app_mod, "_POLL_SAMPLER",
                                  app_mod._PollSampler(window=300.0))
        fresh.start()
        self.addCleanup(fresh.stop)

    def test_the_503_writes_its_errno_on_the_access_line(self):
        captured = []
        _, port = start_server(self, self.home)
        # test_server_common 在 import 期把 log_message 换成了进程级 no-op；
        # 这条判例要看的正是那一行，所以在本测试期间把它换回一个采集器
        with mock.patch.object(app_mod.Handler, "log_message",
                               lambda self_, fmt, *a: captured.append(fmt % a)):
            with _deny_reading("dashboard.json",
                               PermissionError(1, "Operation not permitted")):
                status, _obj = get_json(port, "/api/board")
        self.assertEqual(status, 503)
        board_lines = [ln for ln in captured if "/api/board" in ln]
        self.assertTrue(board_lines, captured)
        self.assertIn("503", board_lines[-1])
        self.assertIn("errno=1 Operation not permitted", board_lines[-1])

    def test_a_200_writes_no_note(self):
        captured = []
        _, port = start_server(self, self.home)
        with mock.patch.object(app_mod.Handler, "log_message",
                               lambda self_, fmt, *a: captured.append(fmt % a)):
            status, _obj = get_json(port, "/api/board")
        self.assertEqual(status, 200)
        board_lines = [ln for ln in captured if "/api/board" in ln]
        self.assertTrue(board_lines, captured)
        self.assertNotIn("errno", board_lines[-1])


class DownstreamReadersTestCase(unittest.TestCase):
    """``_board_dict`` 下游：谁跟着说 503、谁照旧兜底。"""

    def setUp(self):
        self.home = _home("downstream")
        seed_scene(self.home, "initial")

    def test_card_detail_says_unreadable_instead_of_card_not_found(self):
        # 这是**刻意**让 503 穿过 _board_dict（§49 追记 2026-09-18）：读不到
        # 投影就说读不到，不借「card not found」/「dashboard.json not found」
        # 掩过去——今天它答的那个 404 同样是假话，只是换了一句。
        _, port = start_server(self, self.home)
        with _deny_reading("dashboard.json", PermissionError(1, "nope")):
            status, obj = get_json(port, "/api/cards/R-1")
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "BOARD_UNREADABLE")

    def test_is_executing_stays_fail_safe(self):
        # steer 标注是个标注不是真相声明：读不到就不标，503 不顶到 POST 回执上
        with _deny_reading("dashboard.json", PermissionError(1, "nope")):
            self.assertFalse(board_source.is_executing(self.home, "R-1"))


if __name__ == "__main__":
    unittest.main()
