"""`python -m act.lib.taskscheduler` 这条 CLI 的合同（CONTRACT §25 / §49 / §55）。

tests/test_taskscheduler_render.py 把**渲染**钉得很死（token 全替换、XML 合法、
trigger/repetition/PATH 守卫），但它只从「四个参数都给齐、out 目录已存在、只跑一
次」这一条 happy path 进 `main()`——夜间变异（§57）因此在 `main()` 的 argparse
与 `mkdir` 上留了一串存活体：把四个 `required=True` 全改成 `False`、把
`parents=True` / `exist_ok=True` 改成 `False`，判例照样全绿。

这里钉的是那条 CLI 对 install.ps1 的三句承诺：

* **四个路径全是必填**：少一个是**用法错误**（argparse 的 exit 2），不是拿着
  `None` 往模板里替换、在 staging 里留下半份 XML。渲染是 install.ps1 注册任务
  之前的最后一道纯函数，半份输出会被 `Register-ScheduledTask` 当真。
* **staging 目录连父目录一起建**：install.ps1 给的 `-Out` 是一条还不存在的临时
  路径（`$env:TEMP\\zelin-tasks\\...`），渲染器负责把它创出来。
* **重跑是幂等的**：install.ps1 每次装机/升级都往同一个 staging 目录再渲染一遍，
  第二次不许因为目录已存在就炸。
"""
from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import
from tests.scratch_testkit import scratch_dir

from act.lib import taskscheduler as ts

PY = r"C:\Program Files\Python312\python.exe"
REPO = r"C:\Users\Friend\Projects\zelin-ai-assistant"
CLAUDE_DIR = r"C:\Users\Friend\.local\bin"


def _tmpdir(case, name: str) -> Path:
    return Path(scratch_dir(case, prefix="tasksched-train-")) / name


def _main(argv: list) -> int:
    """`main` 把写出的文件名打到 stdout（install.ps1 读它）——套件里吞掉。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return ts.main(argv)


class RequiredFlagsTestCase(unittest.TestCase):
    """四个路径缺任何一个 = 用法错误，绝不半渲染。"""

    def _argv(self, out: Path, drop: str = "") -> list:
        pairs = [("--python", PY), ("--repo-root", REPO),
                 ("--claude-bin-dir", CLAUDE_DIR), ("--out", str(out))]
        argv = []
        for flag, value in pairs:
            if flag == drop:
                continue
            argv += [flag, value]
        return argv

    def test_every_path_flag_is_required(self):
        # argparse 的缺参 = SystemExit(2)。少一个就渲染（把 None 替进模板 /
        # Path(None)）是另一种失败：那会在 staging 里留下 install.ps1 会照单
        # 注册的半份 XML。
        for flag in ("--python", "--repo-root", "--claude-bin-dir", "--out"):
            out = _tmpdir(self, "staging")
            with self.subTest(missing=flag):
                with contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as caught:
                    _main(self._argv(out, drop=flag))
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse(out.exists(), flag)

    def test_all_four_together_render_the_full_task_set(self):
        out = _tmpdir(self, "staging")
        self.assertEqual(_main(self._argv(out)), 0)
        self.assertEqual(sorted(p.name for p in out.glob("*.xml")),
                         sorted(ts.render_all(PY, REPO, CLAUDE_DIR)))


class StagingDirTestCase(unittest.TestCase):
    """install.ps1 给的是一条还不存在的临时路径，且它会重跑。"""

    def _run(self, out: Path) -> int:
        return _main(["--python", PY, "--repo-root", REPO,
                      "--claude-bin-dir", CLAUDE_DIR, "--out", str(out)])

    def test_missing_parent_dirs_are_created(self):
        # `$env:TEMP\zelin-tasks\<run-id>` 整条路径都可能不存在——渲染器建全套，
        # 不是只建最后一级（那会 FileNotFoundError，装机当场断在渲染这一步）。
        out = _tmpdir(self, "nested") / "deeper" / "staging"
        self.assertFalse(out.parent.exists())
        self.assertEqual(self._run(out), 0)
        self.assertTrue(out.is_dir())
        self.assertTrue(list(out.glob("*.xml")))

    def test_rendering_twice_into_the_same_dir_is_idempotent(self):
        # 每次装机/升级 install.ps1 都往同一个 staging 目录再渲染一遍：第二次
        # 必须照样 0 并覆盖出同一批文件，不许「目录已存在」就炸。
        out = _tmpdir(self, "staging")
        self.assertEqual(self._run(out), 0)
        first = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.xml")}
        self.assertEqual(self._run(out), 0)
        second = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.xml")}
        self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
