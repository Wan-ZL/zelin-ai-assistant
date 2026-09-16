"""§75.4 / §49：``GET /api/worktrees`` 与 ``POST /api/worktrees/cleanup`` 的两个端点。

判决与执行全在 `act/lib/worktrees.py`——server 不 import act（§49），两个端点都经
`server/subproc.run_module` 起 ``python -m act.lib.worktrees``，本判例钉的是那层薄壳：
GET 首次回 `state: "computing"` 的空壳（扫目录 + du 在后台线程，GET 路径零子进程）、
算完转 ready、`?refresh=1` 才重算；POST 走 `--sweep`（`{"dry_run": true}` → `--dry-run`）、
认不出的字段 400、跑完让缓存作废；子进程没给 JSON = `ok:false` + `state: "error"` 且
**补满整份形状**（`worktrees: null` 而不是键根本不在）而不是 500。

`subproc` 的 runner 经注入替换——测试绝不真起子进程、绝不真删任何 worktree。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server

from server import worktree_inventory as inv

INVENTORY = {"ok": True, "scanned_at": "2026-09-15T04:00:00Z", "worktrees": 3, "removable": 2,
             "bytes": 12_000_000, "bytes_partial": False, "truncated": False, "stale_days": 14,
             "repos": [{"repo": "/r", "root": "/r/.claude/worktrees", "registered": 4,
                        "managed": 3, "removable": 2, "truncated": False, "rows": [],
                        "error": None}]}
SWEEP = {"ok": True, "dry_run": False, "removed": [{"path": "/r/.claude/worktrees/a",
                                                    "branch": "feat/a", "reason": "merged",
                                                    "removed": True, "branch_deleted": True,
                                                    "error": None}],
         "failed": [], "skipped": {"dirty": 1}, "worktrees": 3, "removable": 2}


class WorktreeEndpointTestCase(unittest.TestCase):
    def setUp(self):
        inv.reset_cache_for_tests()
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-worktrees-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.tmp.name)})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []
        self.out = json.dumps(INVENTORY)

        def runner(argv, _env, _cwd, _timeout_s):
            self.calls.append(list(argv))
            return 0, self.out, ""

        self.runner = runner
        patched = mock.patch.object(inv.subproc, "default_runner", runner)
        patched.start()
        self.addCleanup(patched.stop)
        # cleanup 是 LIFO：这一下登记在临时目录 / patcher **之后**，所以它先跑——GET 起的后台
        # 清点线程要先 join 掉，否则 patcher 一 stop 它就拿回真 runner 去起真子进程，而它的
        # 临时 home 可能已经被 rmtree 了（同 §72.1，CI 2026-09-15 的 Errno 39）。
        self.addCleanup(inv.reset_cache_for_tests)
        _httpd, self.port = start_server(self, self.home)

    # -- GET ----------------------------------------------------------------- #
    def test_the_first_get_is_a_computing_shell_and_starts_no_subprocess_on_the_request_path(self):
        status, body = get_json(self.port, "/api/worktrees")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "computing")
        self.assertIsNone(body["worktrees"])
        self.assertTrue(body["refreshing"])

    def test_the_background_job_fills_the_cache_and_the_next_get_is_ready(self):
        got = inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertEqual(got["state"], "computing")     # 这一次仍回空壳（算在它之后）
        again = inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertEqual(again["state"], "ready")
        self.assertEqual(again["worktrees"], 3)
        self.assertEqual(self.calls[0][-2:], ["act.lib.worktrees", "--json"])

    def test_a_fresh_cache_is_reused_until_refresh(self):
        inv.snapshot(self.home, spawn=lambda fn: fn())
        inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertEqual(len(self.calls), 1)
        inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertEqual(len(self.calls), 1)
        inv.snapshot(self.home, refresh=True, spawn=lambda fn: fn())
        self.assertEqual(len(self.calls), 2)

    def test_a_subprocess_that_prints_no_json_is_not_a_500(self):
        self.out = "boom"
        inv.snapshot(self.home, spawn=lambda fn: fn())
        got = inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "worktrees_failed")

    def test_a_failed_inventory_still_carries_the_whole_shape_and_says_error(self):
        # 少一个键 = 前端在数字位上渲染 undefined（防腐 #10：client 逐字镜像 wire 键）
        self.out = "boom"
        inv.snapshot(self.home, spawn=lambda fn: fn())
        got = inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertEqual(got["state"], "error")
        for key in inv.placeholder():
            self.assertIn(key, got)
        self.assertIsNone(got["worktrees"])
        self.assertIsNone(got["removable"])
        self.assertIsNone(got["bytes"])
        self.assertEqual(got["repos"], [])

    def test_a_runner_that_raises_lands_as_state_error_with_the_whole_shape(self):
        """后台线程里的**异常**（子进程起不起来这一层：OSError / 超时的裸抛）与
        「跑完了但没给 JSON」走同一个出口：整份形状 + `state: "error"` + 一句原因。

        线程里漏出去的异常没有任何人接得住——它会让缓存永远停在 `inflight`，页面
        从此一直转圈（§0 第 11 条在后台线程上的形状）。"""
        def boom(_argv, _env, _cwd, _timeout_s):
            raise OSError("python3 not found")

        inv.snapshot(self.home, spawn=lambda fn: fn(), runner=boom)
        got = inv.snapshot(self.home, spawn=lambda fn: fn(), runner=boom)
        self.assertEqual(got["state"], "error")
        self.assertFalse(got["ok"])
        self.assertIn("OSError: python3 not found", got["error"])
        for key in inv.placeholder():
            self.assertIn(key, got)
        self.assertIsNone(got["worktrees"])
        self.assertFalse(got["refreshing"])            # 在飞标记放开了，不会永远转圈

    # -- POST ---------------------------------------------------------------- #
    def test_cleanup_runs_the_sweep_and_returns_the_receipt(self):
        self.out = json.dumps(SWEEP)
        status, body = post_json(self.port, "/api/worktrees/cleanup", {})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["removed"]), 1)
        self.assertEqual(self.calls[-1][-2:], ["act.lib.worktrees", "--sweep"])

    def test_dry_run_asks_the_cli_for_a_dry_run(self):
        self.out = json.dumps(dict(SWEEP, dry_run=True))
        status, body = post_json(self.port, "/api/worktrees/cleanup", {"dry_run": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["dry_run"])
        self.assertEqual(self.calls[-1][-2:], ["act.lib.worktrees", "--dry-run"])

    def test_an_unknown_field_is_refused(self):
        status, body = post_json(self.port, "/api/worktrees/cleanup", {"force": True})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "UNKNOWN_FIELD")

    def test_a_real_sweep_invalidates_the_cached_inventory(self):
        inv.snapshot(self.home, spawn=lambda fn: fn())
        inv.snapshot(self.home, spawn=lambda fn: fn())
        self.out = json.dumps(SWEEP)
        inv.cleanup(self.home, {})
        self.out = json.dumps(INVENTORY)
        got = inv.snapshot(self.home, spawn=lambda fn: fn())
        self.assertEqual(got["state"], "computing")     # 缓存没了 → 重新算


if __name__ == "__main__":
    unittest.main()
