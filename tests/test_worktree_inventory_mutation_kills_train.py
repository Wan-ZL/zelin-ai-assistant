"""§75.4 / §49：worktree 清点那层薄壳里还没有判例把守的边界（变异网 train 补测）。

`tests/test_server_worktrees.py` 钉住了两个端点的主干（computing → ready、`?refresh=1`、
`--sweep` / `--dry-run`、未知字段 400、失败也补满整份形状）。夜报剩下的存活体全落在它
没说出口的几处：子进程**说了什么**怎么进 `message`（stderr 与 stdout 各自的那一支、
两边都没说时的兜底句）、`rc=127` 与别的非零 rc 是两件事（装不上 python ≠ 清点崩了）、
`placeholder()` 里三个值的**语义**（还在算不是失败、没量过不是量了一半）、后台线程炸掉
落的是 `ok:false`、缓存 TTL「到点」就过期、以及**同一时刻最多一个后台算**（在飞时第二次
GET 不许再起一个，且它自己要说 `refreshing: true`）。

照旧零子进程：`snapshot` / `cleanup` 的 `runner` 参数就是 §49 那道注入缝，后台线程用
`spawn=lambda fn: fn()` 同步跑掉（没有真线程要 join）。

刻意不追的等价体：`INVENTORY_TIMEOUT_S` / `CLEANUP_TIMEOUT_S` 的 ±1——它们是子进程的
时间帽（防腐 #4），法条要的是「GET 那把要容得下 `du` 几十秒、POST 那把要容得下一整轮
回收」，而不是 180 与 600 这两个数本身；差一秒没有任何可观察后果。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import worktree_inventory as inv

READY = {"ok": True, "scanned_at": "2026-09-15T04:00:00Z", "worktrees": 3, "removable": 2,
         "bytes": 12_000_000, "bytes_partial": False, "truncated": False, "stale_days": 14,
         "repos": []}


def _runner(rc=0, out="", err=""):
    def run(_argv, _env, _cwd, _timeout_s):
        return rc, out, err

    return run


class WorktreeInventoryEdgeTestCase(unittest.TestCase):
    def setUp(self):
        inv.reset_cache_for_tests()
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-wt-inv-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        # cleanup 是 LIFO：这一下排在临时目录之后登记，先 join 再拆目录（§75.4 追记）
        self.addCleanup(inv.reset_cache_for_tests)

    def _computed(self, runner, **kw):
        """起一次同步的后台算，回落地后的那份快照。"""
        inv.snapshot(self.home, spawn=lambda fn: fn(), runner=runner, **kw)
        return inv.snapshot(self.home, spawn=lambda fn: fn(), runner=runner, **kw)

    # -- 子进程说了什么 ------------------------------------------------------- #
    def test_what_the_subprocess_said_on_stderr_is_quoted_back(self):
        got = self._computed(_runner(rc=1, err="ModuleNotFoundError: No module named 'yaml'"))
        self.assertIn("ModuleNotFoundError", got["message"])

    def test_what_the_subprocess_said_on_stdout_is_quoted_back_too(self):
        # 人话印在 stdout 而 stderr 空着的形态（CLI 自己的错误行）
        got = self._computed(_runner(rc=1, out="worktrees: 通道 repo 不存在"))
        self.assertIn("通道 repo 不存在", got["message"])

    def test_a_subprocess_that_said_nothing_at_all_still_names_the_exit_code(self):
        got = self._computed(_runner(rc=3))
        self.assertIn("worktrees exited 3", got["message"])

    def test_a_missing_interpreter_is_told_apart_from_a_crash(self):
        # rc=127 = 起不来（`no_python`），别的非零 = 起来了但崩了——设置页给的话不一样
        self.assertEqual(self._computed(_runner(rc=127))["error"], "no_python")
        inv.reset_cache_for_tests()
        self.assertEqual(self._computed(_runner(rc=1))["error"], "worktrees_failed")

    def test_a_background_job_that_blows_up_lands_as_not_ok(self):
        def boom(_argv, _env, _cwd, _timeout_s):
            raise RuntimeError("thread went bang")

        got = self._computed(boom)
        self.assertIs(got["ok"], False)
        self.assertEqual(got["state"], "error")
        self.assertIn("RuntimeError", got["error"])

    # -- 空壳的语义 ----------------------------------------------------------- #
    def test_the_computing_shell_is_honest_about_what_it_does_not_know(self):
        shell = inv.placeholder()
        self.assertEqual(shell["state"], "computing")
        self.assertIs(shell["ok"], True)             # 还在算不是失败
        self.assertIs(shell["bytes_partial"], False)  # 没量过 ≠ 量了一半
        self.assertIs(shell["truncated"], False)
        self.assertIsNone(shell["worktrees"])

    # -- 缓存 ----------------------------------------------------------------- #
    def test_the_cache_expires_the_moment_the_ttl_is_reached(self):
        calls = []

        def counted(_argv, _env, _cwd, _timeout_s):
            calls.append(1)
            return 0, json.dumps(READY), ""

        run = {"spawn": lambda fn: fn(), "runner": counted}
        inv.snapshot(self.home, now=1000.0, **run)
        inv.snapshot(self.home, now=1000.0, **run)
        self.assertEqual(len(calls), 1)
        inv.snapshot(self.home, now=1000.0 + inv.CACHE_TTL_S, **run)
        self.assertEqual(len(calls), 2)

    def test_a_second_get_while_one_is_in_flight_does_not_start_another(self):
        queued = []
        run = {"spawn": queued.append, "runner": _runner(0, json.dumps(READY))}
        inv.snapshot(self.home, **run)
        self.assertEqual(len(queued), 1)
        second = inv.snapshot(self.home, **run)
        self.assertEqual(len(queued), 1)             # 在飞时不许再起一个
        self.assertIs(second["refreshing"], True)    # 而且要说「还在算」
        for job in queued:
            job()

    # -- POST ------------------------------------------------------------------ #
    def test_a_cli_receipt_without_an_ok_key_is_taken_as_ok(self):
        # 回执是 CLI 的原话透传；它没说失败就不是失败（键缺席 ≠ ok:false）
        doc = {"dry_run": False, "removed": [{"path": "/r/.claude/worktrees/a"}]}
        got = inv.cleanup(self.home, {}, runner=_runner(0, json.dumps(doc)))
        self.assertIs(got["ok"], True)
        self.assertEqual(len(got["removed"]), 1)


if __name__ == "__main__":
    unittest.main()
