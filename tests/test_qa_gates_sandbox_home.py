"""QA 门永远在自己的沙箱 home 里跑（CONTRACT §82.3，issue #452）。

`scripts/qa/run_gates.sh` 是无人值守 self-improve 会话跑的那条命令，而那些会话从
守护进程继承 `AIASSISTANT_HOME=<live checkout>`（install.sh 把它烙进 launchd
plist）。于是 `run_coverage.sh` 原来那句 `${AIASSISTANT_HOME:-$(mktemp -d)}` 的回落
**永远命不中**——全套 unittest 就跑在真账本上。两条脚本改成**无条件**开沙箱。

判例钉的是字面量，因为这一条只能在 shell 层成立（python 侧的 §82.2 守卫是第二道
墙，不是第一道）：
- 两条脚本都必须**无条件**赋值，不许再出现 `:-` 回落；
- 赋值必须是两条语句（`X="$(mktemp -d)"` 再 `export`）——`export X="$(mktemp -d)"`
  触发 shellcheck SC2155，而 ci.yml 的 lint job 对每个 tracked `*.sh` 跑 shellcheck；
- 沙箱要自己收（trap）。
"""
import re
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "scripts" / "qa" / "run_gates.sh"
COVERAGE = REPO_ROOT / "scripts" / "qa" / "run_coverage.sh"
VAULT_SYNC = REPO_ROOT / "ingest" / "vault-sync.sh"

#: `VAR="$(mktemp -d)"` —— 一条语句一件事（SC2155 的修法）
_ASSIGN_RE = re.compile(r'^[A-Z_]+="\$\(mktemp -d\)"$', re.M)


class SandboxHomeTestCase(unittest.TestCase):
    def _text(self, path):
        return path.read_text(encoding="utf-8")

    def _code(self, path):
        """只看可执行的行——注释里解释病根时会原样写出那个已经删掉的形状。"""
        return "\n".join(line for line in self._text(path).splitlines()
                         if not line.lstrip().startswith("#"))

    def test_run_gates_exports_a_fresh_sandbox_home(self):
        text = self._text(GATES)
        self.assertRegex(text, _ASSIGN_RE)
        self.assertIn('export AIASSISTANT_HOME="$ZAI_GATE_HOME"', text)

    def test_run_coverage_exports_a_fresh_sandbox_home(self):
        text = self._text(COVERAGE)
        self.assertRegex(text, _ASSIGN_RE)
        self.assertIn('export AIASSISTANT_HOME="$ZAI_COV_HOME"', text)

    def test_neither_script_falls_back_to_an_inherited_home(self):
        """`${AIASSISTANT_HOME:-…}` 就是 #452 的一半病根——继承值永远赢。"""
        for path in (GATES, COVERAGE):
            with self.subTest(script=path.name):
                self.assertNotIn("${AIASSISTANT_HOME:-", self._code(path))

    def test_the_export_is_two_statements_not_one(self):
        """`export X="$(mktemp -d)"` = shellcheck SC2155 = CI lint job 红。"""
        for path in (GATES, COVERAGE):
            with self.subTest(script=path.name):
                self.assertNotIn('export AIASSISTANT_HOME="$(mktemp -d)"',
                                 self._code(path))

    def test_both_scripts_clean_up_after_themselves(self):
        for path in (GATES, COVERAGE):
            with self.subTest(script=path.name):
                self.assertRegex(self._text(path), r"trap '.*rm -rf .*' EXIT")

    def test_run_gates_sandboxes_before_it_calls_anything(self):
        """顺序要对：沙箱必须在第一个子进程之前立起来。"""
        code = self._code(GATES)
        self.assertLess(code.index("export AIASSISTANT_HOME="),
                        code.index("bash scripts/qa/run_coverage.sh"))

    def test_vault_sync_requires_a_home_instead_of_collapsing_to_slash(self):
        """`$AIASSISTANT_HOME/state/vault-mirror` 为空时会塌成 `/state/vault-mirror`，
        而这份脚本的 pull 是 `rsync --delete`（§82.3 的 shell 侧同款 fail-closed）。"""
        self.assertIn(': "${AIASSISTANT_HOME:?', self._text(VAULT_SYNC))


if __name__ == "__main__":
    unittest.main()
