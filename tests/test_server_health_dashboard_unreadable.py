"""``/api/health`` 说出「我读不了那个文件」（CONTRACT §47.4 追记 2026-09-18，
issue #423；§0 宪法第 3 条）。

法条：``server/health.py`` 的三个读者都把 ``OSError`` 折成 ``None``，于是
「文件不在」「内容撕裂」「这个进程读不了它」在 wire 上是同一个值。issue #423
的现场里 actd 心跳好好地跳着，``/api/health`` 于是照答 ``verdict: "ok"`` 而
``dashboard: null``——body 里没有一个字说明为什么。

自本判例起加一个 add-only 顶层键 ``unreadable``：

- **恒在**（干净时 ``{}``）——于是「键不在」= 老 server，而不是「没问题」；
- 只装**真·读失败**（非 ENOENT/ENOTDIR/EISDIR 的 OSError），缺席与撕裂照旧
  静默：§49 路由表原文「文件缺失/撕裂 → 如实报 null/``stale``」一字不动；
- **verdict 阶梯刻意不动**：``verdict`` 讲的是**管线**活性（actd 可能跳得好
  好的，只是这个进程读不了某个文件），新加一级会压在 ``stalled`` 之上、而且
  web 的横幅 switch 里没有它的分支，等于加了一句没人听得见的话；
- **``dashboard`` 仍是 null**：让它变成真值会让 ``dashboard.age_s`` 的客户端
  镜像当场说谎（§47.4 的 wire 形是 ``{generated_at, age_s, stale}|null``）;
- **路由恒 200**（§49 路由表「永不 500」一字不动）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env，防任何 act.* 触真库

from server import health
from tests.test_server_common import get_json, start_server, write_text

NOW = 1_700_000_000.0


def _deny(name: str, exc: OSError, *, stat_too: bool = False):
    """只对某个 basename 拒读（可选连 ``stat`` 一起拒）的补丁。

    整类替换会连 token 文件一起拒掉；按 basename 放行其余，判例练的才是那一条
    真路径。``stat_too`` 用来分开练两种拒绝——``stat()`` 只要目录可穿越，
    ``read`` 还要文件可读，同一次拒绝可能只炸其中一个。
    """
    real_text, real_stat = Path.read_text, Path.stat
    patches = {}

    def fake_text(self, *a, **k):
        if self.name == name:
            raise exc
        return real_text(self, *a, **k)

    patches["read_text"] = fake_text
    if stat_too:
        def fake_stat(self, *a, **k):
            if self.name == name:
                raise exc
            return real_stat(self, *a, **k)
        patches["stat"] = fake_stat
    return mock.patch.multiple(Path, **patches)


class UnreadableBlockTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-r961-health-"))
        state = self.home / "state"
        # 心跳新鲜 + 看板新鲜：issue #423 的现场形状（actd 活着，文件读不了）
        write_text(state / "actd.heartbeat",
                   json.dumps({"phase": "idle", "pid": 1, "interval": 10,
                               "stale_after_s": 90}))
        write_text(state / "dashboard.json",
                   json.dumps({"generated_at": "2023-11-14T22:13:20Z"}))
        write_text(state / "loop_health.json",
                   json.dumps({"consecutive_failures": 0}))
        # 心跳 mtime 决定 age：写完就是现在，注入的 NOW 对齐真实 mtime
        self.now = (state / "actd.heartbeat").stat().st_mtime

    # ---------------- 干净 / 缺席 / 撕裂：都不算 unreadable ---------------- #

    def test_clean_home_reports_an_empty_block(self):
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"], {})
        self.assertEqual(snap["verdict"], "ok")

    def test_the_key_is_always_present(self):
        # 键**恒在**：它的缺席只表示「老 server」，不表示「没问题」
        empty = Path(tempfile.mkdtemp(prefix="zai-r961-empty-"))
        self.assertIn("unreadable", health.snapshot(empty, now=NOW))

    def test_absent_files_are_not_a_read_failure(self):
        empty = Path(tempfile.mkdtemp(prefix="zai-r961-absent-"))
        snap = health.snapshot(empty, now=NOW)
        self.assertEqual(snap["unreadable"], {})
        # §47.4 原文不动：缺席仍是 stale + null，不是新故障源
        self.assertEqual(snap["verdict"], "stale")
        self.assertIsNone(snap["dashboard"])

    def test_torn_json_is_not_a_read_failure(self):
        write_text(self.home / "state" / "dashboard.json", "{half-writ")
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"], {})
        self.assertIsNone(snap["dashboard"])

    def test_non_utf8_bytes_never_raise(self):
        # 撕裂的第二种形状：字节坏了（非 UTF-8）。UnicodeDecodeError 不是
        # OSError，漏接一次就是 /api/health 的 500——§49 路由表的「永不 500」与
        # snapshot() 的「Never raises」两句话同时作废
        (self.home / "state" / "dashboard.json").write_bytes(b'{"a": "\xff\xfe"}')
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"], {})
        self.assertIsNone(snap["dashboard"])

    # ---------------- 读不了：报出来，但别的都不动 ---------------- #

    def test_denied_dashboard_read_is_named_with_its_errno(self):
        with _deny("dashboard.json", PermissionError(1, "Operation not permitted")):
            snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"],
                         {"dashboard": {"errno": 1,
                                        "strerror": "Operation not permitted"}})

    def test_the_verdict_ladder_is_untouched(self):
        # issue #423 的现场：actd 心跳好好地跳着 ⇒ verdict 仍是诚实的 "ok"
        # （它讲的是管线活性）；不诚实的地方是「没有一个字说读不了」——那由
        # unreadable 补上，而不是往阶梯上加一级看不见的 rung。
        with _deny("dashboard.json", PermissionError(1, "nope")):
            snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "ok")
        self.assertIsNotNone(snap["heartbeat"])

    def test_dashboard_block_stays_null(self):
        # 变成真值会让客户端镜像的 age_s / stale 当场说谎
        with _deny("dashboard.json", PermissionError(1, "nope")):
            snap = health.snapshot(self.home, now=self.now)
        self.assertIsNone(snap["dashboard"])

    def test_denied_loop_health_read_is_named(self):
        with _deny("loop_health.json", PermissionError(13, "Permission denied")):
            snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"]["loop_health"],
                         {"errno": 13, "strerror": "Permission denied"})
        self.assertEqual(snap["loop_health"]["consecutive_failures"], 0)

    def test_denied_heartbeat_read_keeps_the_block_and_names_the_failure(self):
        # read 被拒但 stat 没被拒：块还在（age 来自 mtime），body 字段全 null
        # ——这正是 tests/test_server_health.py 当作「撕裂」认的那个形状，
        # 所以必须有一行说清它其实是读不了
        with _deny("actd.heartbeat", PermissionError(1, "nope")):
            snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"]["heartbeat"]["errno"], 1)
        self.assertIsNotNone(snap["heartbeat"])
        self.assertIsNone(snap["heartbeat"]["phase"])

    def test_denied_heartbeat_stat_is_also_named(self):
        # stat 与 read 要的权限不同；只炸 stat 时块整个消失、verdict 掉到无心跳
        # 那条阶梯——同一个根因给出不同 verdict，所以两处都要留痕
        with _deny("actd.heartbeat", PermissionError(1, "nope"), stat_too=True):
            snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["unreadable"]["heartbeat"]["errno"], 1)
        self.assertIsNone(snap["heartbeat"])

    def test_several_failures_are_all_named(self):
        with _deny("dashboard.json", PermissionError(1, "a")), \
                _deny("loop_health.json", PermissionError(1, "b")):
            snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(sorted(snap["unreadable"]), ["dashboard", "loop_health"])


class HealthRouteTestCase(unittest.TestCase):
    """路由层：带着读失败也**恒 200**（§49:3763「永不 500」）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-r961-health-route-"))
        write_text(self.home / "state" / "dashboard.json",
                   json.dumps({"generated_at": "2023-11-14T22:13:20Z"}))

    def test_route_ships_the_block(self):
        _, port = start_server(self, self.home)
        status, snap = get_json(port, "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(snap["unreadable"], {})

    def test_route_stays_200_when_the_board_cannot_be_read(self):
        _, port = start_server(self, self.home)
        with _deny("dashboard.json", PermissionError(1, "Operation not permitted")):
            status, snap = get_json(port, "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(snap["unreadable"]["dashboard"]["errno"], 1)


if __name__ == "__main__":
    unittest.main()
