"""test-code skill · checks.py 变异残差台账：杀掉最后一个可杀的存活体，并把剩下两个
**可证等价**的存活体连证明一起记在案，免得每晚的 mutation 报告反复把它们重新报一遍。

来源：卡片 R-001（self-improve lane）。夜跑 scripts/qa/mutate.py 在
skills/test-code/scripts/checks.py 上是 run=839 / killed=435 / survived=404。
本分支先原样采纳 PR #401 的四份判例（那一批把 404 打到 3），本文件补它漏掉的那一个。
三个残差各自的判决（全部经 scripts/qa/mutate.py 的 site_id 逐个复测，不是推断）：

  return_none@438:8#0   `_unpinned` 豁免分支 `return False` → `return None`
                        —— 本文件杀。理由见下面的「选定不变量」一段。
  int_plus1@556:30#0    `_ignored_literals` 的 `raw.split("#", 1)[0]` → `split("#", 2)[0]`
                        —— **可证等价，永不可杀**。`str.split(sep, maxsplit)[0]` 对任何
                        maxsplit >= 1 都是「首个分隔符之前的那一段」，加大 maxsplit 只会
                        多切后面的尾巴，而 `[0]` 只读第一段。211,111 条输入（含空串、
                        无分隔符、前导/连续分隔符、非 ASCII）穷举 + 随机复核，零差异。
                        mutate.py 的 `_const_sites` 对整数常量一律铸 ±1 两个 site，不认识
                        「这个整数是 maxsplit」，所以这个 site 本就不该存在——属于工具的
                        已知噪声，不是测试网的洞。
  return_none@572:12#0  `_drift_scanner` 里嵌套闭包 `dangling` 的 `return False` → `return None`
                        —— **判等价，故意不杀**。它与 438 同形（False/None 都是假值，
                        唯一消费点是 `if path and dangling(path)`，经任何公共入口都不可观测），
                        但 438 的宿主是模块级私名、可以直接调；`dangling` 只活在 `fn` 的
                        closure cell 里，要断言它得去翻 `fn.__closure__`。那样的判例钉住的是
                        「dangling 是个嵌套函数」这一实现结构——把它提成模块级函数、或者内联掉，
                        行为一字不变而判例会红。这种判例比没有判例更坏（防腐 #7 的反面），
                        所以这里只记账不补。PR #391（R-217）独立地作了同一判断。

**「选定不变量」这一档要诚实说清楚**：`_unpinned` 走豁免分支时返回 False 还是 None，经
`check_actions_sha_pin` 这个公共入口**观测不到**——两个值都是假值，`_pin_violations` 只在
`match and _unpinned(...)` 的真假位置消费它。行为面（`./local` 与 `docker://` 豁免 SHA pin）
早已由 tests/test_skill_test_code_checks.py 钉住，本文件不重复。这里钉的是另一件事：
`_unpinned` 是个**谓词**，它的另一条出口 `not _SHA_RE.match(ref)` 返回的是真布尔，豁免分支
返回 None 就是同一函数两条出口类型不一致——今天无害，等哪天这个值进了 details / 账本 / JSON
报告就立刻可观测。钉住成本一行，先钉便宜。把它算作「行为锚定」是不诚实的，它是类型契约。

法典指针：docs/CONTRACT.md §57（存活变异体 = 补测试提案；等价体记理由）、
§58（internal 检查 fail closed、阈值 truth = qa/gates.toml 只读）。
设计 = docs/design/vnext2-plan.md R2.8。零子进程。
"""
import unittest

from tests import skill_test_code_testkit as kit  # noqa: F401  (挂 sys.path)

import checks  # noqa: E402

# 40 位十六进制 = 合法的 action SHA pin（运行时拼，文件里不留 key 形状字面量）
SHA40 = "0123456789abcdef" * 2 + "01234567"


class UnpinnedIsAPredicateTestCase(unittest.TestCase):
    """checks._unpinned 的两条出口都必须是真 bool（checks.py:436-440）。"""

    def test_the_exemption_branch_returns_false_not_none(self):
        """checks.py:438 `return False` → `return None`。

        豁免分支（`./` 本地 action、`docker://` 镜像）返回的必须是 False 本身。
        assertIs 而不是 assertFalse —— None 也是假的，assertFalse 杀不死这个变异体。
        """
        self.assertIs(checks._unpinned("./.github/actions/setup"), False)
        self.assertIs(checks._unpinned("docker://alpine:3.20"), False)

    def test_the_matching_branch_also_returns_a_real_bool(self):
        """同一函数的另一条出口 `not _SHA_RE.match(ref)` —— 这条本来就是真 bool，
        钉住它是为了让「两条出口同类型」成为一条写下来的契约，而不是巧合。"""
        self.assertIs(checks._unpinned("actions/checkout@" + SHA40), False)
        self.assertIs(checks._unpinned("actions/checkout@v4"), True)
        self.assertIs(checks._unpinned("actions/checkout"), True)


if __name__ == "__main__":
    unittest.main()
