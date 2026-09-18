"""test-code skill · checks.py 变异残差台账：杀掉最后一个可杀的存活体，并把剩下两个
**可证等价**的存活体连证明一起记在案，免得每晚的 mutation 报告反复把它们重新报一遍。

来源：卡片 R-001（self-improve lane）。夜跑 scripts/qa/mutate.py 在
skills/test-code/scripts/checks.py 上是 run=839 / killed=435 / survived=404。
本分支先原样采纳 PR #401 的四份判例（那一批把 404 打到 3），本文件补它漏掉的那一个。
三个残差各自的判决（全部经 scripts/qa/mutate.py 的 site_id 逐个复测，不是推断）：

  return_none@438:8#0   `_unpinned` 豁免分支 `return False` → `return None`
                        —— 本文件杀。理由见下面的「选定不变量」一段。
  int_plus1@556:30#0    `_ignored_literals` 的 `raw.split("#", 1)[0]` → `split("#", 2)[0]`
                        —— **可证等价，永不可杀**。定理：`str.split(sep, k)[i]` 对任何
                        `i < k` 都与 k 无关——左到右切，第 i 段就是真的第 i 段，只有第 k 段
                        （余段）随 k 变；这里 `i = 0`、`k` 从 1 变 2，两边都满足 `i < k`。
                        实测佐证 353,982 条输入（长度 ≤6 的分隔符全排列穷举 + 含全码位的随机
                        串 + 真 `.gitignore` 72 行）零差异；阳性对照是同一行的
                        `int_minus1@556:30#0`（1→0，真的会让注释不再被剥），它在 200,533 条
                        输入上报差异、且被本文件之外的判例杀掉——说明这一行本身是被钉住的，
                        存活的不是覆盖漏洞。mutate.py 的 `_const_sites` 对整数常量一律铸 ±1
                        两个 site，不认识「这个整数是 maxsplit」，属工具已知噪声。
                        ⚠️ 方向性：只有「增大」等价，「减小」是真变异体，抑制规则不能一刀切。
  return_none@572:12#0  `_drift_scanner` 里嵌套闭包 `dangling` 的 `return False` → `return None`
                        —— **判等价，故意不杀**。它与 438 同形：False/None 都是假值，唯一
                        消费点是 `if path and dangling(path)`，经任何公共入口都不可观测。
                        差别**不在**「一个是行为一个是结构」，也不在「改了会不会红」——下面
                        那条钉 `_unpinned` 的判例同样是耦合实现的，把 `_unpinned` 内联掉它
                        一样会红。真正的差别是**测试面有没有先例**：直呼模块级私名是本仓库
                        几十处判例的既有做法（`_` 开头的 helper 本来就当测试面用），而去翻
                        `fn.__closure__` 把嵌套函数掏出来没有任何先例，且钉住的恰恰是「它是
                        个嵌套函数」——提成模块级函数就红。后者不值那一行。
                        PR #391（R-217）独立地作了同一判断。

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


class UnpinnedExemptionBranchTestCase(unittest.TestCase):
    """checks._unpinned 的**豁免**出口必须返真 bool（checks.py:436-438）。

    另一条出口（`not _SHA_RE.match(ref)`）已经由
    tests/test_skill_test_code_internal_math.py 的 `ActionRefPinTestCase` 用同一个
    assertIs 写法钉住了，这里不重复——本文件只补它没覆盖到的豁免分支。
    """

    def test_the_exemption_branch_returns_false_not_none(self):
        """checks.py:438 `return False` → `return None`。

        豁免分支（`./` 本地 action、`docker://` 镜像）返回的必须是 False 本身。
        assertIs 而不是 assertFalse —— None 也是假的，assertFalse 杀不死这个变异体。
        """
        self.assertIs(checks._unpinned("./.github/actions/setup"), False)
        self.assertIs(checks._unpinned("docker://alpine:3.20"), False)


if __name__ == "__main__":
    unittest.main()
