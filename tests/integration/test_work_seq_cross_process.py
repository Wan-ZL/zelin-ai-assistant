"""工作编号序列的跨进程判例（CONTRACT §60.2 / D21）——真子进程，住 integration/。

现实形态：只有 actd 把卡送进 approved（单写者纪律），但 actd 会重启、也可能
被人从 CLI 手动批准——序列必须**跨进程**稠密单调：每个新进程都从账本
（sqlite 热列 / yaml 文件 + state/work_seq.json 高水位）重新算 max，接着上一个
进程分到哪就从哪 +1，硬删过的最大号不复用。剧本：N 个顺序启动的真 python
子进程各批准若干张 P 卡；中间一次把当前最大号的卡硬删（yaml 后端只剩高水位
撑着）；结束后全部 work_id 去重后 = 连续区间 R-001..R-K 减去被删的那个，
且每个进程分到的号严格大于前一个进程的。两后端各跑一遍。

时间预算：BUDGET_SECONDS 兜底（3 进程 × 每进程 3 次批准，秒级完成）。
"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act.lib import registry
from act.lib.registry import Requirement, State

BUDGET_SECONDS = 60
N_PROCS = 3
APPROVALS_PER_PROC = 3

_WORKER = r"""
import os, sys, json
sys.path.insert(0, {repo!r})
os.environ["AIASSISTANT_HOME"] = {home!r}
os.environ["ZAI_REGISTRY_BACKEND"] = {backend!r}
from act.lib import registry
from act.lib.registry import Requirement, State
proc = int(sys.argv[1])
got = []
for i in range({n}):
    rid = f"P-{{proc}}{{i:02d}}"
    registry.upsert(Requirement(id=rid, title=f"proc {{proc}} card {{i}}",
                                status=State.CARD_SENT.value))
    with registry.acting_as("user"):
        r = registry.load(rid)
        r.set_status(State.APPROVED)
        registry.save(r)
    got.append(registry.load(rid).work_id)
if proc == 1:
    # 中途硬删当前最大号的卡：号不得复用（sqlite 靠 tombstone 热列，yaml 靠高水位）
    victim = registry.load(f"P-1{{{n}-1:02d}}")
    with registry.acting_as("user"):
        registry.trash(victim, "deleted")
    assert registry.delete(registry.load(victim.id))
print(json.dumps(got))
"""


class WorkSeqCrossProcessTestCase(unittest.TestCase):
    def _run(self, backend: str):
        store2_testkit.use_backend(self, backend)
        # 存量 legacy 主键抬高序列下界：一切工作号 > R-010
        registry.upsert(Requirement(id="R-010", title="legacy",
                                    status=State.DETECTED.value))
        code = _WORKER.format(repo=str(Path.cwd()), home=os.environ["AIASSISTANT_HOME"],
                              backend=backend, n=APPROVALS_PER_PROC)
        t0 = time.monotonic()
        per_proc = []
        for n in range(N_PROCS):
            proc = subprocess.run([sys.executable, "-c", code, str(n)],
                                  capture_output=True, text=True,
                                  cwd=str(Path.cwd()), timeout=BUDGET_SECONDS)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr, proc.stderr)
            per_proc.append(json.loads(proc.stdout.strip().splitlines()[-1]))
        self.assertLess(time.monotonic() - t0, BUDGET_SECONDS)

        nums = [[registry.id_number(w) for w in got] for got in per_proc]
        # 每个进程内部单调；进程之间接力（后一个进程的最小号 > 前一个进程的最大号）
        for got in nums:
            self.assertEqual(got, sorted(got))
        for prev, cur in zip(nums, nums[1:]):
            self.assertGreater(min(cur), max(prev))
        flat = [n for got in nums for n in got]
        total = N_PROCS * APPROVALS_PER_PROC
        # 稠密：从 legacy 上界 10 之上连续 R-011..R-(10+total)，无空洞（删掉的号
        # 已经发出、不复用，所以仍在区间里）
        self.assertEqual(flat, list(range(11, 11 + total)))
        self.assertEqual(len(set(flat)), total)

        registry.reset_store_cache()
        # 被硬删的卡不在账本里，但它的号被后来的进程跳过了（上面已证）；再来一次
        # 分配仍是 max+1
        self.assertEqual(registry.next_work_id(), f"R-{11 + total:03d}")

    def test_sequence_relays_across_processes_sqlite(self):
        self._run("sqlite")

    def test_sequence_relays_across_processes_yaml(self):
        self._run("yaml")


if __name__ == "__main__":
    unittest.main()
