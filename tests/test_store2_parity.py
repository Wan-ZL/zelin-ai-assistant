"""store2 parity 测试（BUILD-CONTRACT §3）：同一批 fixture 卡片经
act.lib.registry.load_all（YAML 真源读法）与 store2（migrate 后 SQL 读法）
双读，投影必须一致。

范围注记：worktree 基于 8fd3b33，act/lib/registry.py 是老版本——不识
archive/ 子目录、merged/archived 终态枚举、thread_*/display_title 等新
optional 字段（from_dict 静默丢弃），也没有 id/title/tier 的 str() 归一。
因此 parity 面 = 老 dataclass 认识的字段交集；archive 卡与 live-only 字段
的等价性由 test_store2_migration.py 按 mapping 文档直接对 DB 断言。
比对时的归一（str()/merged 前缀映射/词表外 tier 跳过）逐条对应 live
registry.from_dict 与 schema CHECK 的既定语义，不是测试放水。
"""
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from act.lib import config, registry
from tests.test_store2_migration import (
    _MIGRATE_LANDED, _SKIP_REASON, EXPECTED_STATUS, build_fixture_registry,
    _run_migrate)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _expected_store_view(req):
    """registry 卡 → store2 热列期望值（legacy merged 前缀 → merged + 父指针）。"""
    if isinstance(req.status, str) and req.status.startswith(registry.MERGED_PREFIX):
        return "merged", req.status[len(registry.MERGED_PREFIX):]
    return req.status, req.merged_into


@unittest.skipUnless(_MIGRATE_LANDED, _SKIP_REASON)
class ParityTestCase(unittest.TestCase):
    """(b) YAML registry 读法 vs store2 读法，投影一致。"""

    @classmethod
    def setUpClass(cls):
        # fixture 落在 sandbox HOME 的真 registry 目录，让 registry.load_all
        # 走它平日的路径解析；archive/example 排除（老 loader 语义，见模块注记）
        config.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        build_fixture_registry(config.REGISTRY_DIR, with_archive=False,
                               with_example=False)
        cls.tmp = Path(tempfile.mkdtemp(prefix="store2-parity-"))
        cls.db_path = cls.tmp / "store2.db"
        _run_migrate(config.REGISTRY_DIR, cls.db_path)
        cls.conn = sqlite3.connect(cls.db_path)
        cls.conn.row_factory = sqlite3.Row
        cls.reqs = {str(r.id): r for r in registry.load_all()}
        cls.rows = {r["id"]: r for r in cls.conn.execute("SELECT * FROM cards")}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_same_card_population(self):
        # 双方看到同一批卡：坏文件/空文件两边同样跳过，list 文件两边同样展开
        self.assertEqual(set(self.reqs), set(EXPECTED_STATUS))
        self.assertEqual(set(self.rows), set(self.reqs))

    def test_hot_column_projection_agrees(self):
        for rid, req in self.reqs.items():
            row = self.rows[rid]
            want_status, want_parent = _expected_store_view(req)
            self.assertEqual(row["status"], want_status, rid)
            self.assertEqual(row["merged_into_id"], want_parent, rid)
            self.assertEqual(row["title"], str(req.title), rid)
            self.assertEqual(row["type"], req.type, rid)
            self.assertEqual(row["target_repo"], req.target_repo, rid)
            if str(req.tier) in ("T0", "T1", "T2"):
                self.assertEqual(row["tier"], str(req.tier), rid)
            if isinstance(req.deadline, str) and _DATE_RE.match(req.deadline):
                self.assertEqual(row["deadline"], req.deadline, rid)
            if req.prev_status:
                self.assertEqual(row["prev_status"], req.prev_status, rid)
            elif want_status == "trashed":
                # 缺回程票的 legacy 卡：store2 按 live restore fallback 回填
                self.assertEqual(row["prev_status"], "detected", rid)

    def test_payload_projection_agrees(self):
        # 省略语义 §0.2：键缺失 == 默认值（None/''/0/False），比对两边同规
        for rid, req in self.reqs.items():
            payload = json.loads(self.rows[rid]["payload"])
            self.assertEqual(payload.get("hardness", "soft"), req.hardness, rid)
            self.assertEqual(payload.get("repeated_mentions", 1),
                             req.repeated_mentions, rid)
            self.assertEqual(payload.get("summary", "") or "",
                             req.summary or "", rid)
            self.assertEqual(payload.get("notes", "") or "",
                             req.notes or "", rid)
            self.assertEqual(payload.get("trash_reason"), req.trash_reason, rid)
            self.assertEqual(payload.get("trashed_at"), req.trashed_at, rid)
            if rid != "R-110":
                # cost 污染卡除外：B3 裁决 payload 按 _coerce_cost 归 None，
                # 老 registry from_dict 存原样 'cheap'——已知偏差，flag 在报告
                self.assertEqual(payload.get("cost_estimate_usd"),
                                 req.cost_estimate_usd, rid)
            # plan 双形（str|list|None）原样保留，不归一（mapping §9.7）
            self.assertEqual(payload.get("plan"), req.plan, rid)

    def test_sources_projection_agrees(self):
        for rid, req in self.reqs.items():
            want = [s["channel"] for s in req.sources if isinstance(s, dict)]
            got = [r["channel"] for r in self.conn.execute(
                "SELECT channel FROM sources WHERE card_id = ?"
                " ORDER BY id", (rid,))]
            self.assertEqual(got, want, rid)  # 逐卡保序（sources[0] 驱动 thread_key）
            quotes = [s.get("quote") for s in req.sources if isinstance(s, dict)]
            got_q = [r["quote"] for r in self.conn.execute(
                "SELECT quote FROM sources WHERE card_id = ?"
                " ORDER BY id", (rid,))]
            self.assertEqual(got_q, quotes, rid)


if __name__ == "__main__":
    unittest.main()
