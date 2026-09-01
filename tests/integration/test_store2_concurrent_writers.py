"""跨进程并发写判例（CONTRACT §53.5 / R2.1.5）——真子进程，住 integration/。

现实形态：actd 常驻 + 两个雷达 + digest 都是**独立进程**，yaml 时代靠
「无锁写文件 + 祈祷」；store2 后端由数据库事务（BEGIN IMMEDIATE +
busy_timeout=5000）保证**每笔写原子**。剧本：4 个真 python 子进程同时经
registry 门面往同一个库里铸卡 + 反复改写同一张共享卡——结束后账本必须满足
事务给出的保证：
  - 每个进程铸的每张卡都在（插入零丢失，无 torn write）；
  - 每行 payload 都是完整合法 JSON（yaml 时代的半写/交错文件不可能出现）；
  - 共享卡至少带有「最后提交者」的完整 note 序列（跨进程 read-modify-write
    的语义与 yaml 后端一致 = 后写者胜，§53.5 明文；状态转移单写者纪律 §44
    未变，这个窗口只存在于 fold 类 payload 并写，与 yaml 时代等价）；
  - version / board_revision 单调且不低于可证下界；
  - 子进程零 traceback、零超时（busy_timeout 生效，没人饿死）。

时间预算：BUDGET_SECONDS 兜底（4 进程 × 每进程 ~20 次写，秒级完成）。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act.lib import registry

BUDGET_SECONDS = 60
N_PROCS = 4
CARDS_PER_PROC = 10
SHARED_TOUCHES = 5

_WORKER = r"""
import os, sys
sys.path.insert(0, {repo!r})
os.environ["AIASSISTANT_HOME"] = {home!r}
os.environ["ZAI_REGISTRY_BACKEND"] = "sqlite"
from act.lib import registry
from act.lib.registry import Requirement
proc = int(sys.argv[1])
for i in range({cards}):
    rid = f"R-{{proc}}{{i:02d}}"
    registry.upsert(Requirement(id=rid, title=f"proc {{proc}} card {{i}}",
                                status="detected",
                                summary=f"from writer {{proc}}"))
for i in range({touches}):
    shared = registry.load("R-900")
    note = registry.append_fold_note(shared, f"proc {{proc}} touch {{i}}", "radar")
    registry.save(shared)
print("worker-ok", proc)
"""


class ConcurrentWritersTestCase(unittest.TestCase):
    def test_parallel_processes_lose_no_writes(self):
        store2_testkit.use_backend(self, "sqlite")
        registry.upsert(registry.Requirement(id="R-900", title="共享卡",
                                             status="detected"))
        code = _WORKER.format(
            repo=str(Path.cwd()), home=os.environ["AIASSISTANT_HOME"],
            cards=CARDS_PER_PROC, touches=SHARED_TOUCHES)
        t0 = time.monotonic()
        procs = [subprocess.Popen(
            [sys.executable, "-c", code, str(n)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=str(Path.cwd())) for n in range(N_PROCS)]
        outs = [p.communicate(timeout=BUDGET_SECONDS) for p in procs]
        self.assertLess(time.monotonic() - t0, BUDGET_SECONDS)
        for p, (out, err) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, err)
            self.assertNotIn("Traceback", err, err)
            self.assertIn("worker-ok", out)

        registry.reset_store_cache()
        reqs = registry.load_all()
        ids = {r.id for r in reqs}
        for n in range(N_PROCS):
            for i in range(CARDS_PER_PROC):
                self.assertIn(f"R-{n}{i:02d}", ids)
        # 共享卡：至少「最后提交者」的完整序列存活（它每轮重读，自己的前几
        # 笔一定在自己的最后一笔里）；上限 = 全部写者的总和
        shared = registry.load("R-900")
        notes = registry.parse_fold_notes(shared.notes)
        self.assertGreaterEqual(len(notes), SHARED_TOUCHES)
        self.assertLessEqual(len(notes), N_PROCS * SHARED_TOUCHES)
        # DB 侧不变量：每行 payload 完整合法；version/board_revision 单调且
        # 不低于可证下界（每笔真实变更 +1；铸卡数是硬下界）
        con = sqlite3.connect(f"file:{registry.store2_db_path()}?mode=ro",
                              uri=True)
        try:
            for (payload,) in con.execute("SELECT payload FROM cards"):
                self.assertIsInstance(json.loads(payload), dict)
            ver = con.execute(
                "SELECT version FROM cards WHERE id = 'R-900'").fetchone()[0]
            rev = con.execute(
                "SELECT value FROM board_revision WHERE id = 1").fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(ver, 1 + SHARED_TOUCHES)
        self.assertGreaterEqual(rev, N_PROCS * CARDS_PER_PROC + SHARED_TOUCHES)


if __name__ == "__main__":
    unittest.main()
