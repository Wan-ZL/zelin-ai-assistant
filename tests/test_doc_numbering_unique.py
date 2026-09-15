"""编号台账不许重号（防腐 #6「§ 号永不复用、永不静默消失」；CONTRACT §58.3 追记 / §76）。

两本台账各有一套号：`docs/CONTRACT.md` 的顶层 `## N.` 法条号与
`docs/design/vnext2-plan.md` 的 `| DN |` 决策行。两个**同轮并行**的 PR 都按
「`origin/dev` 上的 max + 1」算号时会算出同一个号，而合并没有任何一步会察觉
——第二个落地的那个 PR 就在法典里留下第二个 §N（issue #313 / PR #349 评审实
例：#347 与 #349 都写了 `## 75.`）。这条判例就是那道察觉：重号 = 红，改号的
代价（改一遍引用）远小于两条同号法条的代价。

**不查号是否连续**：跳号是合法的（§59 原文「若它们最终未立法，两个号作废、
永不复用」；§11 席位今天就是空的）——作废的号必须留着空着，所以这里只查
「同一个号出现两次」。
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "CONTRACT.md")
PLAN_PATH = os.path.join(REPO_ROOT, "docs", "design", "vnext2-plan.md")

_SECTION_RE = re.compile(r"^## (\d+)\.")
_D_ROW_RE = re.compile(r"^\| D(\d+) \|")


def _numbers(path: str, pattern: "re.Pattern") -> list:
    with open(path, "r", encoding="utf-8") as fh:
        return [int(m.group(1)) for m in
                (pattern.match(line) for line in fh) if m]


def _duplicates(numbers: list) -> list:
    seen, dups = set(), []
    for n in numbers:
        if n in seen and n not in dups:
            dups.append(n)
        seen.add(n)
    return dups


class DocNumberingUniqueTestCase(unittest.TestCase):
    def test_contract_top_level_section_numbers_are_unique(self):
        numbers = _numbers(CONTRACT_PATH, _SECTION_RE)
        self.assertGreater(len(numbers), 40)      # 解析活着（正则没有被标题改动打瞎）
        self.assertEqual(_duplicates(numbers), [],
                         "docs/CONTRACT.md 有重号的顶层 §——同轮并行 PR 各取一个"
                         "空号（§59 先例），第二个落地的必须改号")

    def test_vnext2_decision_row_ids_are_unique(self):
        numbers = _numbers(PLAN_PATH, _D_ROW_RE)
        self.assertGreater(len(numbers), 40)
        self.assertEqual(_duplicates(numbers), [],
                         "docs/design/vnext2-plan.md 有重号的 D 行——新 D 行 = "
                         "origin/dev 上 max D + 1，rebase 时重新核号")


if __name__ == "__main__":
    unittest.main()
