"""store2 读路径的规模预算判例（R2.1 性能面：~200 卡不得拖垮 10s pass）。

actd 的一个 pass 会调 load_all 多次（inbox/reconcile/dashboard/清扫），yaml
后端是 200 次文件读 + YAML parse；sqlite 后端必须至少同量级——这里给 200 卡
的 load_all 钉一个宽松但真实的墙钟预算（CI 波动余量 ×10 仍远小于 pass 间隔）。
"""
import time
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act.lib import registry
from act.lib.registry import Requirement


class LoadScaleTestCase(unittest.TestCase):
    def test_load_all_on_200_cards_stays_inside_the_pass_budget(self):
        store2_testkit.use_backend(self, "sqlite")
        for i in range(1, 201):
            registry.upsert(Requirement(
                id=f"R-{i:03d}", title=f"规模卡 {i}", status="detected",
                sources=[{"channel": "meeting", "date": "2026-08-30",
                          "ref": f"r{i}", "quote": "q" * 200, "who": "m"}],
                plan=[f"step {i}"], summary="s" * 120))
        t0 = time.monotonic()
        reqs = registry.load_all()
        elapsed = time.monotonic() - t0
        self.assertEqual(len(reqs), 200)
        self.assertLess(elapsed, 2.0,
                        f"load_all(200 cards) took {elapsed:.2f}s — the 10s "
                        "pass budget would be eaten by reads")
        # 二读（连接已热）应显著更快，顺带证明无每次重建 schema 的隐藏成本
        t1 = time.monotonic()
        registry.load_all()
        self.assertLess(time.monotonic() - t1, 1.0)


if __name__ == "__main__":
    unittest.main()
