"""测试跑者永远不许把一棵 git 工作树当 AIASSISTANT_HOME（CONTRACT §82.2，issue #452）。

2026-09-18 的看板被抹（registry 清空、`state/vault-mirror` 删除、27 个 tracked 文件
消失、夹具卡 `R-8150` / `R-960` 被工号分配收养）的根因只有一句：一次测试跑把
`config.HOME` 绑在了 owner 的 live 安装上。本文件钉住那道守卫的全部判决面：

- 判据是「解析掉符号链接之后，它自己或某个祖先有没有 `.git`」——与 `.pkg` 目的地守卫
  `mac/scripts/pkg_dest_guard.sh` 同源（法条侧的交叉引用写在 §82.2 正文里，本文件只钉
  python 侧那一份）；`~/Projects` 在本机就是一条指向外置卷的符号链接；
- **只对测试跑者执法**——生产入口（一律显式携带该 env，§19）一个字节不受影响，
  这是「`act.doctor` 的 home 行 / `--print-path` CLI 永不 traceback」得以成立的前提；
- 逃生门 `AIASSISTANT_ALLOW_LIVE_HOME` 认得出真假值；
- `config._home()` 仍是纯解析、永不抛（`tests/test_server_paths_mirror.py` 的
  漂移判例建在它上面）。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, home

REPO_ROOT = Path(__file__).resolve().parents[1]


class CheckoutRootTestCase(unittest.TestCase):
    """`checkout_root`：证明不了在工作树之外就报出那棵树的根。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="home-guard-")
        # resolve()：macOS 的 $TMPDIR 自己就是 /var -> /private/var 的符号链接，
        # 而 checkout_root 按法条先解析再判——比较面必须用解析后的形状。
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_temp_dir_is_outside_any_worktree(self):
        self.assertIsNone(home.checkout_root(self.root))

    def test_dot_git_directory_makes_it_a_checkout(self):
        (self.root / ".git").mkdir()
        self.assertEqual(home.checkout_root(self.root), self.root)

    def test_dot_git_FILE_counts_too(self):
        """worktree / submodule 的 `.git` 是一个指向 gitdir 的文件，不是目录。"""
        (self.root / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        self.assertEqual(home.checkout_root(self.root), self.root)

    def test_an_ancestor_counts(self):
        (self.root / ".git").mkdir()
        deep = self.root / "state" / "a" / "b"
        deep.mkdir(parents=True)
        self.assertEqual(home.checkout_root(deep), self.root)

    def test_a_nonexistent_path_under_a_checkout_still_counts(self):
        """还没建出来的 home 也要判得动（.pkg 目的地守卫同款：不存在不是借口）。"""
        (self.root / ".git").mkdir()
        self.assertEqual(home.checkout_root(self.root / "nope" / "state"), self.root)

    def test_symlink_is_resolved_before_judging(self):
        """本机的真实形状：`~/Projects` 是符号链接，只看字面路径等于没装守卫。"""
        (self.root / ".git").mkdir()
        link = Path(tempfile.gettempdir()) / (self.root.name + "-link")
        link.symlink_to(self.root)
        self.addCleanup(link.unlink)
        self.assertEqual(home.checkout_root(link), self.root)

    def test_this_repo_is_recognised(self):
        self.assertIsNotNone(home.checkout_root(REPO_ROOT))

    def test_the_suite_sandbox_is_not(self):
        self.assertIsNone(home.checkout_root(TMP_HOME))


class UnderTestTestCase(unittest.TestCase):
    """跑者判据与 import 顺序无关：runpy 先装跑者模块，才 import 测试模块。"""

    def test_unittest_in_sys_modules_is_the_signal(self):
        self.assertTrue(home.under_test())

    def test_pytest_counts_too(self):
        self.assertTrue(home.under_test(modules={"pytest": object()}))

    def test_a_production_process_has_neither(self):
        self.assertFalse(home.under_test(modules={"json": object()}))

    def test_no_production_module_imports_a_test_runner(self):
        """守卫的零误伤前提：act/ 与 server/ 全树没有一行 import unittest/pytest。"""
        hits = []
        for base in ("act", "server"):
            for path in sorted((REPO_ROOT / base).rglob("*.py")):
                for lineno, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), 1):
                    head = line.strip()
                    if head.startswith(("import unittest", "from unittest",
                                        "import pytest", "from pytest",
                                        "import doctest", "from doctest")):
                        hits.append("%s:%d" % (path.relative_to(REPO_ROOT), lineno))
        self.assertEqual(hits, [], "生产代码 import 了测试跑者 → §82.2 守卫会误伤")


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="home-guard-g-")
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def _runner(self):
        return ("unittest",)

    def test_checkout_home_under_a_test_runner_raises(self):
        (self.root / ".git").mkdir()
        with self.assertRaises(home.HomeNotIsolated) as ctx:
            home.guard(self.root, modules=self._runner(), env={})
        message = str(ctx.exception)
        self.assertIn(str(self.root), message)
        self.assertIn("§82", message)
        self.assertIn("mktemp -d", message)       # 修法要给得出来

    def test_sandbox_home_under_a_test_runner_is_fine(self):
        home.guard(self.root, modules=self._runner(), env={})

    def test_production_is_never_touched_even_on_a_live_checkout(self):
        """不是测试跑者 = 生产（launchd / cron / ingest / install.sh 全显式携带 env）。"""
        (self.root / ".git").mkdir()
        home.guard(self.root, modules={"json": object()}, env={})

    def test_escape_hatch_allows_a_live_home(self):
        (self.root / ".git").mkdir()
        home.guard(self.root, modules=self._runner(),
                   env={home.ALLOW_LIVE_ENV: "1"})

    def test_falsey_escape_hatch_values_do_not_open_the_gate(self):
        (self.root / ".git").mkdir()
        for value in ("", "0", "false", "no", "off", " FALSE "):
            with self.subTest(value=value):
                with self.assertRaises(home.HomeNotIsolated):
                    home.guard(self.root, modules=self._runner(),
                               env={home.ALLOW_LIVE_ENV: value})


class ConfigWiringTestCase(unittest.TestCase):
    """config 侧的接线：guard 在 import 期跑，`_home()` 自己永不抛。"""

    def test_default_home_is_single_sourced_from_home_module(self):
        self.assertEqual(home.DEFAULT_HOME, "~/Projects/zelin-ai-assistant")

    def test_home_resolution_stays_pure_and_never_raises(self):
        """`act.doctor` 的 home 行与 `--print-path` CLI 的承诺建在这上面。"""
        env = dict(os.environ)
        env.pop("AIASSISTANT_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config._home(),
                             Path(home.DEFAULT_HOME).expanduser())

    def test_the_suite_itself_is_bound_to_the_sandbox(self):
        """绊线：这三个常量任何一个落在工作树里 = 2026-09-18 正在重演。"""
        for name in ("HOME", "STATE_DIR", "REGISTRY_DIR"):
            with self.subTest(constant=name):
                self.assertIsNone(home.checkout_root(getattr(config, name)))


class _SubprocessMixin:
    """真起一个子进程跑一小段源码（import 期的事只有真 import 证得了）。"""

    DEFAULT_CODE = "import unittest\nfrom act.lib import config\nprint(config.HOME)\n"

    def _run(self, home_value, extra_env=None, code=None):
        env = dict(os.environ)
        env.pop("AIASSISTANT_ALLOW_LIVE_HOME", None)
        env["AIASSISTANT_HOME"] = str(home_value)
        # 本仓库的 act/ 必须赢：开发者 shell 里的 PYTHONPATH 常指向另一棵 checkout
        # （worktree 里跑的时候子进程会 import 错那一棵）。
        env["PYTHONPATH"] = str(REPO_ROOT)
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-c", code or self.DEFAULT_CODE],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        )


class ImportTimeRefusalTestCase(_SubprocessMixin, unittest.TestCase):
    """端到端：真起一个子进程，证明 `import act.lib.config` 在 #452 的形状下就炸。"""

    def test_a_test_runner_pointed_at_this_checkout_dies_on_import(self):
        proc = self._run(REPO_ROOT)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("HomeNotIsolated", proc.stderr)

    def test_the_same_process_with_a_sandbox_home_imports_clean(self):
        with tempfile.TemporaryDirectory(prefix="home-guard-sub-") as tmp:
            proc = self._run(tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(tmp, proc.stdout)

    def test_the_escape_hatch_works_across_the_process_boundary(self):
        proc = self._run(REPO_ROOT, {"AIASSISTANT_ALLOW_LIVE_HOME": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)


class SwallowedGuardTestCase(_SubprocessMixin, unittest.TestCase):
    """守卫不许被生产代码的 best-effort 兜底吞掉（`HomeNotIsolated(BaseException)`）。

    生产代码遍地是 `except Exception`（宪法第 11 条——一条坏记录不许崩 pass）。
    这条守卫要是个普通 Exception，第一个兜底就把它咽了，进程带着 live 路径接着跑，
    只是少了一块能力——看起来像产品 bug，不像 home 出了事。仓库里已经为同一个问题
    立过一次法：`tests/__init__.RealSubprocessBanned` 就继承 BaseException，
    docstring 写着同样的理由。
    """

    def test_server_settings_cannot_degrade_the_refusal_into_a_missing_feature(self):
        # server/settings.py: `except Exception: skill_store = None`。
        # HomeNotIsolated 曾经是 RuntimeError，这一句实测 rc=0 + skill_store=None。
        proc = self._run(REPO_ROOT, code="import unittest\nimport server.settings\n")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("HomeNotIsolated", proc.stderr)

    def test_a_bare_except_exception_around_the_import_does_not_catch_it(self):
        code = ("import unittest\n"
                "try:\n"
                "    from act.lib import config\n"
                "except Exception:\n"
                "    print('SWALLOWED')\n")
        proc = self._run(REPO_ROOT, code=code)
        self.assertNotIn("SWALLOWED", proc.stdout)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
