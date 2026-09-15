"""`python3 -m <module>` 的退出码就是 `main()` 的返回值（CONTRACT §72.2 / §75.4 的
两条 CLI 入口；§18 的 cron 链逐字调的是这一形）。

两个模块的最后两行是 ``if __name__ == "__main__": raise SystemExit(main())``——cron
与 owner 手敲的都是这一形（``python3 -m act.lib.screenpipe_retention``、
``python3 -m act.lib.worktrees --json``），所以「一行 JSON 进 stdout + 退出码 0」这
条约定必须有判例，而不是只测到 `main()` 那一层就停。

零子进程、零网络：retention 那一形指向一个不存在的 db（``skipped: no_db``），
worktrees 那一形把 `subprocess.run` 换成一个不回答的替身（`git` / `du` 的默认
runner 于是「没有答案」，判决表因此为空）。`runpy` 在本进程里跑，不 spawn。
"""
import contextlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import screenpipe_retention, worktrees   # noqa: F401 - pinned module paths


class _Proc:
    """`subprocess.run` 的替身结果（rc 0、空输出 = 「问不出东西」）。"""

    returncode = 0
    stdout = ""
    stderr = ""


@contextlib.contextmanager
def _exit_code():
    """收下脚本形那一下 ``SystemExit``，把退出码放进盒子里。"""
    box = {"code": None}
    try:
        yield box
    except SystemExit as exc:
        box["code"] = exc.code


def _run_as_script(module: str, argv: list) -> "tuple[int, str]":
    """把模块当脚本跑一遍，返回 (退出码, stdout)。"""
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["prog"] + argv), warnings.catch_warnings():
        # runpy 对「已经 import 过的模块再当脚本跑」会念一句 RuntimeWarning——
        # 这里正是故意再跑一遍那两行，不是意外。
        warnings.simplefilter("ignore", RuntimeWarning)
        with _exit_code() as box, contextlib.redirect_stdout(buf):
            runpy.run_module(module, run_name="__main__")
    return box["code"], buf.getvalue()


class ModuleScriptEntryTestCase(unittest.TestCase):
    def test_the_retention_module_prints_one_json_line_and_exits_zero(self):
        missing = Path(tempfile.mkdtemp(prefix="zai-script-")) / "nope.sqlite"
        code, out = _run_as_script("act.lib.screenpipe_retention",
                                   ["--dry-run", "--db", str(missing)])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(json.loads(out)["skipped"], "no_db")

    def test_the_worktrees_module_prints_the_inventory_and_exits_zero(self):
        with mock.patch.object(subprocess, "run", return_value=_Proc()):
            code, out = _run_as_script("act.lib.worktrees", ["--json"])
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["worktrees"], 0)
        self.assertIn("stale_days", doc)


if __name__ == "__main__":
    unittest.main()
