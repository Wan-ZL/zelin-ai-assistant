"""每个测试模块都必须先把沙箱 env 立起来，再 import act/server（CONTRACT §82.2）。

`unittest discover -s tests` 把用例命名成**裸模块名**（`test_policy`，不是
`tests.test_policy`），所以 `tests/__init__.py`——沙箱 `AIASSISTANT_HOME` 的唯一
出生地——只因为某个用例写了 `from tests import …` 才会被牵进来。谁先谁后由字母序
决定，于是「单独跑一个模块」这件最平常的事就能让 `config.HOME` 绑在 owner 的
live 安装上：issue #452 的 33 个漏网文件正是这么来的（2026-09-18 看板被抹）。

act/lib/home.py 的 import 期守卫是**拒绝**那种进程；本文件管的是另一半——让它
压根不发生，并且**不许再退回去**。两条判例：
1. 全树扫描：任何 `tests/**/test_*.py` 里，`tests` 的 import 必须先于
   `act`/`server` 的第一个 import。零豁免、零账本——同一个 PR 里全部修得完的事
   不配拥有一份 shrink-only baseline。
2. 绊线：`config.HOME` / `STATE_DIR` / `REGISTRY_DIR` 必须都落在 `tests.TMP_HOME`
   底下。2026-09-18 之前没有任何一处把这两者对比过。
"""
import ast
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
PRODUCT_PACKAGES = ("act", "server")


def _top_level_imports(tree):
    """[(lineno, 顶层包名)]，按源码顺序——只看模块级 import（函数里的懒 import
    发生在 env 立起来之后，与本条法无关）。"""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append((node.lineno, (node.module or "").split(".")[0]))
    return out


def _first(imports, wanted):
    for lineno, name in imports:
        if name in wanted:
            return lineno
    return None


def _test_modules():
    return sorted(p for p in TESTS_DIR.rglob("test_*.py")
                  if "__pycache__" not in p.parts)


class SandboxImportOrderTestCase(unittest.TestCase):
    def test_every_test_module_sandboxes_before_importing_the_product(self):
        offenders = []
        for path in _test_modules():
            imports = _top_level_imports(ast.parse(path.read_text(encoding="utf-8")))
            product = _first(imports, PRODUCT_PACKAGES)
            if product is None:
                continue                      # 不碰产品代码 → 与本条法无关
            sandbox = _first(imports, ("tests",))
            if sandbox is None or sandbox > product:
                offenders.append("%s:%d" % (path.relative_to(REPO_ROOT), product))
        self.assertEqual(
            offenders, [],
            "这些模块先 import 了 act/server：单独跑它们会把 config.HOME 绑在 live "
            "安装上（§82.2 / issue #452）。修法是在第一个 act/server import 之前加一行\n"
            "    from tests import TMP_HOME  # noqa: F401 - sandbox env first\n"
            "漏网清单：\n  " + "\n  ".join(offenders))

    def test_the_scan_actually_sees_the_corpus(self):
        """扫描器自己坏了（比如 rglob 改错）不许悄悄变成全绿。"""
        self.assertGreater(len(_test_modules()), 400)


class SandboxTripwireTestCase(unittest.TestCase):
    def test_config_paths_live_under_the_suite_sandbox(self):
        sandbox = Path(TMP_HOME).resolve()
        for name in ("HOME", "STATE_DIR", "REGISTRY_DIR", "INBOX_DIR", "LOG_DIR"):
            with self.subTest(constant=name):
                resolved = Path(getattr(config, name)).resolve()
                self.assertTrue(
                    resolved == sandbox or sandbox in resolved.parents,
                    "config.%s = %s 不在沙箱 %s 底下 —— 这一轮正在写真账本"
                    % (name, resolved, sandbox))


if __name__ == "__main__":
    unittest.main()
