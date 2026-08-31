"""store2 migration 测试（BUILD-CONTRACT §3；dashi 最高价值模式）：
手工构造 OLD-shape YAML registry fixture（状态混杂、archive 卡、LLM 污染值、
list 文件、坏文件）→ migrate → 断言行数/字段/状态等价 + 回读。

走 B3 的真实表面：`migrate_yaml.main(["--registry", ..., "--db", ...])`
（CLI 是唯一入口；rc==0 = 成功，非零 = 整体拒绝——B3 的 fail-atomic 纪律，
绝不留半库）。fixture 依据 docs/design/store2-mapping.md（173 卡 census）
逐条取材：省略语义 §0.2、archive 双份规则 §6、LLM 污染容忍 §7、plan 双形 §9.7。
"""
import importlib.util
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

import yaml

_MIGRATE_LANDED = importlib.util.find_spec("act.lib.store2.migrate_yaml") is not None
_SKIP_REASON = ("act.lib.store2.migrate_yaml (B3) not importable — "
                "these tests activate automatically once it exists")


def _run_migrate(registry_dir: Path, db_path: Path, *, dry_run: bool = False):
    """成功路径 helper：rc 非零直接 fail（错误详情在 stderr 里）。"""
    import act.lib.store2.migrate_yaml as m
    argv = ["--registry", str(registry_dir), "--db", str(db_path)]
    if dry_run:
        argv.append("--dry-run")
    rc = m.main(argv)
    if rc != 0:
        raise AssertionError(f"migrate_yaml.main({argv}) returned rc={rc}")
    return rc


# --------------------------------------------------------------------------- #
# fixture：OLD-shape YAML registry（真源 registry 的等价物，全部手写钉死）
# --------------------------------------------------------------------------- #
FOLD_TS = "2026-08-01T10:00:00Z"
NOTES_BLOB = (f"[radar] 补充导出按钮需求 [@{FOLD_TS}]\n"
              "[re-raised] manager 又提了一次\n"
              "from app quick capture")
PLAN_STR = "1. 先梳理现有 export 入口\n2. 加按钮 + 单测"
GMAIL_THRID = "1823324031954270241"  # 超 int53 的引号字符串，绝不转 int

# 期望状态表（migration 后的 store2 视角；legacy merged_into: 归一为 merged）
EXPECTED_STATUS = {
    "R-101": "detected",
    "R-102": "card_sent",
    "R-103": "executing",
    "R-104": "trashed",
    "R-105": "trashed",
    "R-106": "merged",     # legacy 'merged_into:R-102' 归一（schema CHECK 强制）
    "R-107": "merged",
    "R-110": "detected",
    "R-111": "card_sent",
    "R-112": "detected",
    "R-115": "review",
    "R-116": "delivered",
    "R-117": "delivered",
}
EXPECTED_ARCHIVE_STATUS = {"R-108": "archived", "R-109": "archived"}


def _card(rid, status, **extra):
    """live to_dict 形状的最小复刻：13 个 core 键永远在场（哪怕 null），
    optional 键只在取值时出现（省略语义 §0.2）。"""
    d = {"id": rid, "title": extra.pop("title", f"任务 {rid}"),
         "type": extra.pop("type", "chore"), "tier": extra.pop("tier", "T1"),
         "status": status, "hardness": extra.pop("hardness", "soft"),
         "deadline": extra.pop("deadline", None),
         "repeated_mentions": extra.pop("repeated_mentions", 1),
         "green_sign_required": False, "disagreement": None,
         "cost_estimate_usd": extra.pop("cost_estimate_usd", None),
         "sources": extra.pop("sources", [
             {"channel": "meeting", "who": "manager", "date": "2026-08-20",
              "quote": f"quote for {rid}"}]),
         "plan": extra.pop("plan", None)}
    d.update(extra)
    return d


def build_fixture_registry(root: Path, *, with_archive: bool = True,
                           with_example: bool = True) -> None:
    """在 root 下手搭 OLD-shape registry。with_archive=False 给 parity 测试
    用（worktree 的 8fd3b33 版 registry.load_all 不识 archive/ 子目录，
    也不排除 R-000-example）。坏文件/空文件两边加载器都会跳过，恒定在场。"""
    root.mkdir(parents=True, exist_ok=True)

    def dump(name, obj):
        (root / name).write_text(
            yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")

    # 干净基线卡
    dump("R-101.yaml", _card("R-101", "detected"))
    # 满配卡：list plan、deadline、双外部 source（slack + gmail RFC2822 日期）、
    # chat 交付、live-only optional 字段（thread_key 等，老 registry 不识 → 冷列必须保住）
    dump("R-102.yaml", _card(
        "R-102", "card_sent", tier="T2", type="feature",
        deadline="2026-09-15", plan=["调研", "实现", "验收"],
        sources=[
            {"channel": "slack", "who": "manager", "date": "2026-08-10",
             "ref": "https://x.slack.com/archives/C1/p1725000000000100",
             "quote": "能不能加个一键导出"},
            {"channel": "gmail", "who": "hr@example.com",
             "date": "Thu, 06 Feb 2025 15:54:59 +0000",
             "ref": "<msg-1@mail.example.com>", "quote": "please export",
             "gmail_thread_id": GMAIL_THRID}],
        summary="给 my-bench 加一键导出",
        definition_of_done=["按钮可点", "生成 CSV", "有单测"],
        target_repo="~/Projects/my-bench", delivery_mode="chat",
        thread_id="R-102", thread_key=f"gmail:{GMAIL_THRID}"))
    # 执行中：execution 杂物抽屉 + notes 混合 blob（fold/re-raised/prose 三形）
    dump("R-103.yaml", _card(
        "R-103", "executing",
        execution={"session_id": "sess-abc123", "dispatched_at": FOLD_TS,
                   "log": "state/logs/R-103.log",
                   "delivered_summary": "上一轮交付摘要"},
        notes=NOTES_BLOB))
    # 回收站：带回程票 / 缺回程票（B3 按 live restore fallback 回填 detected）
    dump("R-104.yaml", _card(
        "R-104", "trashed", prev_status="card_sent",
        trashed_at="2026-08-25T09:00:00Z", trash_reason="rejected"))
    dump("R-105.yaml", _card(
        "R-105", "trashed", trashed_at="2026-08-25T09:00:00Z",
        trash_reason="silent-merge: 已并入 R-102",
        sources=[{"channel": "quick_capture", "who": "zelin",
                  "date": "2026-08-24", "quote": "重复念叨"}]))
    # merged 两形：legacy verbatim 状态串 vs 现代 merged + merged_into 字段
    dump("R-106.yaml", _card("R-106", "merged_into:R-102"))
    dump("R-107.yaml", _card("R-107", "merged", merged_into="R-102"))
    # LLM 污染卡（CLAUDE.md 血泪实录）：int title/tier、bool deadline、非数字 cost
    dump("R-110.yaml", _card(
        "R-110", "detected", title=456, tier=7, deadline=True,
        cost_estimate_usd="cheap"))
    # plan 的 str 合法形（mapping §9.7：round-trip 优先，原样保留）
    dump("R-111.yaml", _card("R-111", "card_sent", plan=PLAN_STR))
    # sources 列表混入 non-dict 项（load_all/_dedupe_sources 都静默跳过）
    dump("R-112.yaml", _card(
        "R-112", "detected",
        sources=["stray-non-dict-entry",
                 {"channel": "meeting", "who": "manager",
                  "date": "2026-08-21", "quote": "only real source"}]))
    # 单文件多卡的 list 形态（历史欠账批，migrate 必须处理）
    (root / "legacy-batch.yaml").write_text(
        yaml.safe_dump([
            _card("R-115", "review",
                  execution={"rework_count": 1,
                             "last_rework_at": "2026-08-22T08:00:00Z"}),
            _card("R-116", "delivered"),
        ], allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    # 已验收 + 交付 artifact；delivery_mode 缺失 == repo（§0.3）
    dump("R-117.yaml", _card(
        "R-117", "delivered",
        execution={"delivered_summary": "已完成", "final_draft": "成稿全文…",
                   "accepted_at": "2026-08-23T10:00:00Z", "done": True}))
    # 坏文件三连：损坏 YAML / 空文件 →跳过不致命（宪法第 11 条）
    (root / "R-113.yaml").write_text("{\n  broken: [unclosed", encoding="utf-8")
    (root / "R-114.yaml").write_text("", encoding="utf-8")
    if with_example:
        # 文档示例卡按文件名排除（live _iter_files 规则），永不入库
        dump("R-000-example.yaml", _card("R-000", "detected"))

    if with_archive:
        arch = root / "archive"
        arch.mkdir(exist_ok=True)
        dump_a = lambda name, obj: (arch / name).write_text(  # noqa: E731
            yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")
        dump_a("R-108.yaml", _card(
            "R-108", "archived", prev_status="delivered",
            archived_at="2026-08-26T10:00:00Z", archive_reason="user"))
        # crash-mid-move 双份：active 残件 vs archive 权威副本（load() 规则）
        dump("R-108.yaml", _card("R-108", "delivered"))
        # 缺 prev_status 的 legacy archive 卡 → 回填 delivered（unarchive fallback）
        dump_a("R-109.yaml", _card(
            "R-109", "archived", archived_at="2026-08-26T10:00:00Z",
            archive_reason="user"))


@unittest.skipUnless(_MIGRATE_LANDED, _SKIP_REASON)
class MigrationTestCase(unittest.TestCase):
    """(a) OLD-shape fixture → migrate → 行数/字段/状态等价 + 回读。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="store2-migrate-"))
        cls.registry_dir = cls.tmp / "registry"
        cls.db_path = cls.tmp / "store2.db"
        build_fixture_registry(cls.registry_dir)
        _run_migrate(cls.registry_dir, cls.db_path)
        cls.conn = sqlite3.connect(cls.db_path)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _row(self, rid):
        row = self.conn.execute(
            "SELECT * FROM cards WHERE id = ?", (rid,)).fetchone()
        self.assertIsNotNone(row, f"card {rid} missing after migration")
        return row

    def _payload(self, rid):
        return json.loads(self._row(rid)["payload"])

    def test_counts_and_skips(self):
        ids = {r["id"] for r in self.conn.execute("SELECT id FROM cards")}
        expected = set(EXPECTED_STATUS) | set(EXPECTED_ARCHIVE_STATUS)
        self.assertEqual(ids, expected)          # 15 张，一张不多一张不少
        # 坏文件/空文件跳过、示例卡按文件名排除、R-108 active 残件不重复成行
        self.assertNotIn("R-000", ids)
        self.assertEqual(len(ids), 15)

    def test_status_equivalence(self):
        for rid, want in {**EXPECTED_STATUS, **EXPECTED_ARCHIVE_STATUS}.items():
            self.assertEqual(self._row(rid)["status"], want, rid)

    def test_legacy_merged_status_normalized_with_parent_pointer(self):
        # legacy 'merged_into:R-102' → 热列归一 merged + merged_into_id（schema
        # CHECK 强制）；payload 真源保 verbatim 串——matching 语义与终态 merged
        # 相反（mapping §6），export/round-trip 靠它不失真
        row = self._row("R-106")
        self.assertEqual(row["merged_into_id"], "R-102")
        self.assertEqual(self._payload("R-106").get("status"), "merged_into:R-102")
        self.assertEqual(self._row("R-107")["merged_into_id"], "R-102")

    def test_prev_status_backfilled_for_legacy_cards(self):
        # 缺回程票的 legacy 卡按 live restore/unarchive fallback 回填
        self.assertEqual(self._row("R-104")["prev_status"], "card_sent")
        self.assertEqual(self._row("R-105")["prev_status"], "detected")
        self.assertEqual(self._row("R-108")["prev_status"], "delivered")
        self.assertEqual(self._row("R-109")["prev_status"], "delivered")

    def test_archive_copy_wins_over_crash_residue(self):
        # crash-mid-move 双份：archive 副本权威（live load() 规则）
        rows = self.conn.execute(
            "SELECT status FROM cards WHERE id = 'R-108'").fetchall()
        self.assertEqual([r["status"] for r in rows], ["archived"])

    def test_llm_polluted_values_survive_without_crash(self):
        row = self._row("R-110")
        self.assertEqual(row["title"], "456")            # int → str 归一（from_dict 语义）
        self.assertIsInstance(row["title"], str)
        self.assertEqual(row["tier"], "T1")              # 越界 tier 热列回落 T1
        self.assertIsNone(row["deadline"])               # bool deadline 进不了热列 GLOB
        payload = self._payload("R-110")
        self.assertEqual(payload.get("tier"), "7")       # payload 保留原值（str 归一后）
        self.assertIs(payload.get("deadline"), True)     # payload 真源 verbatim
        # cost 非数字：B3 裁决 payload 也按 _coerce_cost 归 None（偏离逐字节
        # round-trip——export 会吐 null 而非 'cheap'；TODO(contract) 见报告）
        self.assertIsNone(payload.get("cost_estimate_usd"))

    def test_plan_str_form_preserved_verbatim(self):
        # mapping §9.7 拍板：round-trip 优先，str 形不归一成 list
        self.assertEqual(self._payload("R-111").get("plan"), PLAN_STR)
        self.assertEqual(self._payload("R-102").get("plan"), ["调研", "实现", "验收"])

    def test_sources_table_rows_in_list_order(self):
        rows = self.conn.execute(
            "SELECT channel, quote FROM sources WHERE card_id = 'R-102'"
            " ORDER BY id").fetchall()
        # 保序：sources[0] 是 thread_key 推导的首选源
        self.assertEqual([r["channel"] for r in rows], ["slack", "gmail"])
        self.assertEqual(rows[1]["quote"], "please export")

    def test_non_dict_source_items_skipped(self):
        rows = self.conn.execute(
            "SELECT channel FROM sources WHERE card_id = 'R-112'").fetchall()
        self.assertEqual([r["channel"] for r in rows], ["meeting"])

    def test_payload_keeps_live_only_optional_fields(self):
        # 老 dataclass 不识的 live 字段（thread_key 等）migrate 后仍在冷列
        payload = self._payload("R-102")
        self.assertEqual(payload.get("thread_key"), f"gmail:{GMAIL_THRID}")
        # gmail_thread_id 保持字符串（超 int53 的标识符，转 int = 数据损坏）
        srcs = [s for s in payload.get("sources", []) if isinstance(s, dict)]
        gmail = [s for s in srcs if s.get("channel") == "gmail"]
        if gmail:  # payload 是否冗余存 sources 由 B3 定；存了就必须没走样
            self.assertEqual(gmail[0].get("gmail_thread_id"), GMAIL_THRID)

    def test_execution_and_notes_blob_verbatim(self):
        p103 = self._payload("R-103")
        self.assertEqual(p103.get("execution", {}).get("session_id"), "sess-abc123")
        self.assertEqual(p103.get("notes"), NOTES_BLOB)  # blob 真源逐字保留（§5）
        p117 = self._payload("R-117")
        self.assertEqual(p117.get("execution", {}).get("final_draft"), "成稿全文…")

    def test_delivery_mode_semantics(self):
        # 磁盘只会出现 chat；缺失 == repo（mapping §0.3）
        self.assertEqual(self._payload("R-102").get("delivery_mode"), "chat")
        self.assertEqual(
            self._payload("R-117").get("delivery_mode", "repo"), "repo")

    def test_bookkeeping_columns_initialized(self):
        for row in self.conn.execute(
                "SELECT id, version, created, updated, origin_trust,"
                " tombstone FROM cards"):
            self.assertEqual(row["version"], 1, row["id"])      # CAS 起点
            self.assertTrue(row["created"] and row["updated"], row["id"])
            self.assertEqual(row["tombstone"], 0, row["id"])
        # origin_trust fail-closed：外部渠道（meeting）绝不能被判成 hand
        self.assertEqual(self._row("R-101")["origin_trust"], "external")

    def test_trash_bookkeeping_survives(self):
        p = self._payload("R-105")
        self.assertEqual(p.get("trash_reason"), "silent-merge: 已并入 R-102")
        self.assertEqual(p.get("trashed_at"), "2026-08-25T09:00:00Z")


@unittest.skipUnless(_MIGRATE_LANDED, _SKIP_REASON)
class MigrationRefusalTestCase(unittest.TestCase):
    """fail-atomic：无法忠实入库的卡（词表外 status，schema 无法表达）→
    整体拒绝、零写入——绝不留半库（宁可拒绝也不静默改数据；人工修复源
    文件后重跑）。区别于 tier/deadline 这类「热列可回落、payload 保真」的
    WARN 级污染。"""

    def test_out_of_vocab_status_refuses_whole_run(self):
        import act.lib.store2.migrate_yaml as m
        tmp = Path(tempfile.mkdtemp(prefix="store2-refuse-"))
        try:
            reg = tmp / "registry"
            reg.mkdir(parents=True)
            good = _card("R-201", "detected")
            bad = _card("R-202", "banana")   # 词表外 status：热列无处安放
            for name, obj in (("R-201.yaml", good), ("R-202.yaml", bad)):
                (reg / name).write_text(
                    yaml.safe_dump(obj, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
            db = tmp / "s.db"
            rc = m.main(["--registry", str(reg), "--db", str(db)])
            self.assertNotEqual(rc, 0)
            # 整体拒绝 = 连好卡也不落库，绝不留半库（错误先于建库发生）
            if db.exists():
                conn = sqlite3.connect(db)
                try:
                    n = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] \
                        if conn.execute("SELECT name FROM sqlite_master WHERE"
                                        " type='table' AND name='cards'").fetchone() \
                        else 0
                    self.assertEqual(n, 0)
                finally:
                    conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(_MIGRATE_LANDED, _SKIP_REASON)
class MigrationDryRunTestCase(unittest.TestCase):
    def test_dry_run_writes_no_rows(self):
        tmp = Path(tempfile.mkdtemp(prefix="store2-dryrun-"))
        try:
            registry_dir = tmp / "registry"
            db_path = tmp / "store2.db"
            build_fixture_registry(registry_dir)
            _run_migrate(registry_dir, db_path, dry_run=True)
            if db_path.exists():
                conn = sqlite3.connect(db_path)
                try:
                    tables = {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if "cards" in tables:
                        n = conn.execute(
                            "SELECT COUNT(*) FROM cards").fetchone()[0]
                        self.assertEqual(n, 0, "--dry-run must not insert cards")
                finally:
                    conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
