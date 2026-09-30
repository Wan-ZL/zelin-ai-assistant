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


#: 进了这些节点就不再往下看——只有**函数体**里的懒 import 发生在 env 立起来之后。
#: 类体不在此列：`class C: from act.lib import config` 照样在 import 期执行。
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _collect(node, out):
    """递归收 import，但**不进函数体**。

    只看 `tree.body` 的直接子节点是不够的：`if _LANDED:` / `try: … except
    ImportError:` / `with …:` 里的 import 照样在 import 期执行（`tests/test_store2_cas.py`
    就是这个形状），而扫描器看不见它 = 那个文件整份从本条法里消失——不是判它违例，
    是压根不查。§82.2 写的是「零豁免」，一个隐形豁免比一条明账更糟。

    **类体同理**：跳过 `ClassDef` 的理由本来写的是「懒 import 发生在 env 之后」，
    可类体是 import 期就执行完的，那个理由对它根本不成立——等于给自己留了一个
    隐形豁免。树里今天零命中（586 个模块，递归进类体后漏网清单一条不变），趁空的
    时候收掉比等它被用上再收便宜。
    """
    for child in ast.iter_child_nodes(node):
        _visit(child, out)


def _never_runs_at_import(test) -> bool:
    """这个 `if` 的条件在 import 期是否**恒假**——只认静态认得出的三种形状。

    递归进 `if` 体是为了堵住隐形豁免，但它同时开始数那些 import 期**跑不到**的
    分支。三种得减回去，否则一份**合规**文件会被判红，而 §82.2 零豁免、零账本，
    被误判的人连个出口都没有：

    - `if TYPE_CHECKING:` —— 运行期恒假，体只给类型检查器看；
    - `if False:` —— 同理，且是 `# if 0:` 那种临时屏蔽的写法；
    - `if __name__ == "__main__":` —— 只在「直接把这个文件当脚本跑」时执行，
      而那时模块级 import 早已跑完，顺序在它之前就定死了。

    `except ImportError:` 的处理体**不**在此列：try 失败时它就在 import 期执行，
    算进去是对的（保守一侧）。
    """
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Constant) and test.value is False:
        return True
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__")


def _visit(child, out):
    """一条语句：要么记下它的 import，要么继续往里走。"""
    if isinstance(child, _SCOPE_NODES):
        return
    if isinstance(child, ast.Import):
        out.extend((child.lineno, alias.name.split(".")[0])
                   for alias in child.names)
    elif isinstance(child, ast.ImportFrom):
        out.append((child.lineno, (child.module or "").split(".")[0]))
    elif isinstance(child, ast.If) and _never_runs_at_import(child.test):
        for alt in child.orelse:          # `else:` 那半边照样在 import 期跑
            _visit(alt, out)
    else:
        _collect(child, out)


def _top_level_imports(tree):
    """[(lineno, 顶层包名)]，按源码顺序——import 期会执行到的那些。"""
    out = []
    _collect(tree, out)
    return sorted(out)


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

    def test_an_import_nested_in_try_or_if_still_counts(self):
        """import 期会跑到的 import 都算——藏进 `try:` 不是豁免（§82.2 零豁免）。"""
        source = ("import unittest\n"
                  "try:\n"
                  "    from act.lib import config\n"
                  "except ImportError:\n"
                  "    config = None\n")
        imports = _top_level_imports(ast.parse(source))
        self.assertEqual(_first(imports, PRODUCT_PACKAGES), 3)
        self.assertIsNone(_first(imports, ("tests",)),
                          "这份源码没立沙箱 —— 扫描器必须把它判成漏网，不是跳过")

    def test_a_lazy_import_inside_a_function_is_still_exempt(self):
        """函数体里的 import 发生在 env 立起来之后，与本条法无关。"""
        source = ("import unittest\n"
                  "def helper():\n"
                  "    from act.lib import config\n"
                  "    return config\n")
        self.assertIsNone(
            _first(_top_level_imports(ast.parse(source)), PRODUCT_PACKAGES))

    def test_branches_that_never_run_at_import_are_not_counted(self):
        """递归进 `if` 体不许开始数 import 期跑不到的分支——那是给合规文件判红。

        三种形状各一份，都只在那个死分支里 import act：扫描器必须**看不见**它，
        于是这份文件与本条法无关，而不是「没立沙箱」的漏网。
        """
        shapes = {
            "TYPE_CHECKING": ("from typing import TYPE_CHECKING\n"
                              "if TYPE_CHECKING:\n"
                              "    from act.lib import config\n"),
            "if False": ("import unittest\n"
                         "if False:\n"
                         "    from act.lib import config\n"),
            "__main__": ("import unittest\n"
                         "if __name__ == \"__main__\":\n"
                         "    from act.lib import config\n"),
        }
        for label, source in shapes.items():
            with self.subTest(shape=label):
                imports = _top_level_imports(ast.parse(source))
                self.assertIsNone(
                    _first(imports, PRODUCT_PACKAGES),
                    "%s 的体在 import 期跑不到，不该算进顺序" % label)

    def test_the_else_half_of_a_dead_branch_still_counts(self):
        """`if TYPE_CHECKING:` 的 `else:` 是运行期真跑的那一半——不许跟着一起跳。"""
        source = ("from typing import TYPE_CHECKING\n"
                  "if TYPE_CHECKING:\n"
                  "    pass\n"
                  "else:\n"
                  "    from act.lib import config\n")
        imports = _top_level_imports(ast.parse(source))
        self.assertEqual(_first(imports, PRODUCT_PACKAGES), 5)

    def test_an_import_only_in_an_except_handler_still_counts(self):
        """`except ImportError:` 的体在 try 失败时就是 import 期执行——保守一侧。"""
        source = ("import unittest\n"
                  "try:\n"
                  "    import nonexistent_pkg\n"
                  "except ImportError:\n"
                  "    from act.lib import config\n")
        imports = _top_level_imports(ast.parse(source))
        self.assertEqual(_first(imports, PRODUCT_PACKAGES), 5)

    def test_an_import_in_a_class_body_still_counts(self):
        """类体在 import 期就执行完——它不是函数体，不配拿到那份豁免。

        跳过 `ClassDef` 的旧理由写的是「懒 import 发生在 env 立起来之后」，
        而这话只对函数体成立。留着就是 §82.2 明令禁止的隐形豁免。
        """
        source = ("import unittest\n"
                  "class Fixture:\n"
                  "    from act.lib import config\n")
        imports = _top_level_imports(ast.parse(source))
        self.assertEqual(_first(imports, PRODUCT_PACKAGES), 3)
        self.assertIsNone(_first(imports, ("tests",)),
                          "这份源码没立沙箱 —— 扫描器必须把它判成漏网，不是跳过")


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
