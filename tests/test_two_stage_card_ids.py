"""两段式卡片编号判例（CONTRACT §60，owner 决策 D21，issue #127）。

D21 原话：「如果这个卡片没有执行,就不算是真正的卡片,不需要给它 R 编号;只有我
approve 跑了的,才给编号。」

钉住的行为（两后端逐条跑）：
  * 出生 = ``P-<n>`` 主键（next_id），detected/card_sent/raising/merge/trash 一律
    **不**分配工作编号；
  * 进入 approved 的每条路径都分配 ``R-<m>``：owner approve、§51 免批、capture[run]
    出生即 approved、restore 精确复位回 approved；
  * 工作序列稠密、单调、永不复用（含 sqlite tombstone / yaml 硬删 + 高水位）；
  * set-once：退回提案再批准、trash→restore 都不换号；
  * resolve() 主键与工作编号双向可达；inbox/merge 入口按两种 ref 都能找到卡且
    lineage 只落主键；
  * legacy ``R-<n>`` 主键：从未批准 → id_kind=legacy、display_id=主键；批准 →
    work_id 采纳自己的主键（不另发号）；
  * 投影行 display_id / id_kind / work_id；executor 的 prompt 头 / 会话名 / 日志名
    用显示编号，analytics 仍记主键；
  * store2 schema v1 → v2 升级梯子：加列 + 唯一索引 + set-once 触发器，crash window
    幂等重跑，全新库与升级库形状收敛；踏出一级前先留 ``<db>.pre-v<from>`` 整库快照
    （写锁下复核版本后每次刷新、单文件；拍不下来 = SCHEMA_SNAPSHOT_FAILED 拒绝
    升级）——升级单向、旧代码打不开新库，快照是 D17 代码回滚的退路（§53.1 单向门
    条款）：旧代码的门对升级后的库关、对快照开，恢复快照后再升级快照随之刷新，
    并发首开只留一份 v1 快照；
  * 陈旧内存副本 / 被旧代码剥掉 payload 号的卡落盘时采纳真源里的号，不重铸不清空；
  * 导出 ↔ 迁移 round-trip 带 work_id；
  * 跨命名空间序键（auto_merge / quick_capture / actd FIFO）。
跨进程的序列判例住 tests/integration/test_work_seq_cross_process.py。
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import
from tests import store2_testkit

from act import actd, executor
from act.lib import auto_merge, config, dashboard, quick_capture, registry
from act.lib.registry import Requirement, State
from act.lib.store2 import export_yaml, hot, migrate_yaml
from act.lib.store2.store import (SCHEMA_VERSION, IntegrityViolation, Store,
                                  StoreError, pre_upgrade_snapshot_path)

BACKENDS = ("yaml", "sqlite")
SCHEMA_PATH = Path(registry.__file__).parent / "store2" / "schema.sql"


def _card(rid, title, status=State.CARD_SENT.value, **kw):
    base = dict(id=rid, title=title, type="dev", tier="T1", status=status,
                hardness="soft", repeated_mentions=1,
                sources=[{"channel": "meeting", "date": "2026-08-30",
                          "ref": f"ref-{rid}", "quote": f"quote {title}",
                          "who": "manager"}],
                plan=["step 1"], summary=f"summary of {title}")
    base.update(kw)
    return Requirement.from_dict(base)


def _approve(rid):
    """owner approve 的 actd 路径（inbox → _apply_decision）。"""
    with registry.acting_as("user"):
        return actd._apply_decision(registry.load(rid), "approve", None)


class _Both(unittest.TestCase):
    """每条判例在两后端各跑一遍（subTest）。"""

    def for_each_backend(self, body):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                store2_testkit.use_backend(self, backend)
                body(backend)


# --------------------------------------------------------------------------- #
# 出生 = P- 主键，检测/合并/回收站不发工作号
# --------------------------------------------------------------------------- #
class BirthNeverConsumesWorkNumberTestCase(_Both):
    def test_next_id_is_provisional_and_dense(self):
        def body(_b):
            self.assertEqual(registry.next_id(), "P-001")
            registry.upsert(_card("P-001", "a", status=State.DETECTED.value))
            self.assertEqual(registry.next_id(), "P-002")
            # 存量 legacy 主键不影响 P 序列
            registry.upsert(_card("R-040", "legacy", status=State.DETECTED.value))
            self.assertEqual(registry.next_id(), "P-002")
        self.for_each_backend(body)

    def test_detection_merge_trash_allocate_nothing(self):
        def body(_b):
            kind, new = registry.merge_or_new_with_kind(
                {"title": "全新的事", "sources": [
                    {"channel": "gmail", "date": "2026-08-31", "ref": "m-1",
                     "quote": "ask", "who": "hr"}]})
            self.assertEqual(kind, "proposed")
            self.assertTrue(new.id.startswith("P-"))
            self.assertIsNone(new.work_id)
            # 纯重述折叠：仍无号
            _kind, folded = registry.merge_or_new_with_kind(
                {"title": "全新的事", "sources": [
                    {"channel": "slack", "date": "2026-08-31", "ref": "s-1",
                     "quote": "again", "who": "hr"}]})
            self.assertIsNone(folded.work_id)
            # raising / card_sent / trash 都不发
            r = registry.load(new.id)
            r.set_status(State.RAISING)
            registry.save(r)
            r.set_status(State.CARD_SENT)
            registry.save(r)
            with registry.acting_as("user"):
                registry.trash(registry.load(new.id), "rejected")
            self.assertIsNone(registry.load(new.id).work_id)
            self.assertEqual(registry.next_work_id(), "R-001")
        self.for_each_backend(body)

    def test_merge_secondary_gets_no_number_and_lineage_uses_keys(self):
        def body(_b):
            registry.upsert(_card("P-001", "主卡"))
            registry.upsert(_card("P-002", "副卡"))
            with registry.acting_as("user"):
                sec = registry.load("P-002")
                sec.set_status(State.MERGED)
                sec.merged_into = "P-001"
                registry.save(sec)
            self.assertIsNone(registry.load("P-002").work_id)
            self.assertEqual(registry.load("P-002").merged_into, "P-001")
        self.for_each_backend(body)


# --------------------------------------------------------------------------- #
# 进入 approved 的每条路径都分配；set-once；稠密单调
# --------------------------------------------------------------------------- #
class ApprovalAllocatesTestCase(_Both):
    def setUp(self):
        self.cfg = config.Config()
        self.cfg.memory_inject = False

    def test_owner_approve_allocates(self):
        def body(_b):
            registry.upsert(_card("P-001", "写周报"))
            self.assertEqual(_approve("P-001"), "running")
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertEqual(saved.work_id, "R-001")
            self.assertEqual(registry.display_id(saved), "R-001")
            self.assertEqual(registry.id_kind(saved), registry.ID_KIND_WORK)
        self.for_each_backend(body)

    def test_policy_auto_dispatch_allocates(self):
        def body(_b):
            hand = _card("P-001", "手打卡", type="other", cost_estimate_usd=1.0,
                         sources=[{"who": "zelin", "channel": "quick",
                                   "date": "2026-08-31", "quote": "原话"}])
            registry.upsert(hand)
            with mock.patch.object(actd.notify, "notify"):
                self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertEqual(saved.work_id, "R-001")
        self.for_each_backend(body)

    def test_capture_run_born_approved_allocates(self):
        def body(_b):
            for p in config.INBOX_DIR.glob("*.json"):
                p.unlink()
            payload = {"action": "capture", "text": "直接跑：整理周报",
                       "mode": "run", "ts": "2026-09-01T00:00:00Z"}
            (config.INBOX_DIR / f"capture-{uuid.uuid4()}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            actd._SYNC_ACTIVE_CACHE = None
            self.assertEqual(actd.process_inbox(), 1)
            req = [r for r in registry.load_all() if r.title == "直接跑：整理周报"][0]
            self.assertTrue(req.id.startswith("P-"))
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertEqual(req.work_id, "R-001")
        self.for_each_backend(body)

    def test_restore_into_approved_allocates_and_keeps_existing(self):
        def body(_b):
            # ① 从未批准过的卡：trash（prev=card_sent）→ restore 回 card_sent = 无号
            registry.upsert(_card("P-001", "提案"))
            with registry.acting_as("user"):
                registry.trash(registry.load("P-001"), "rejected")
                registry.restore(registry.load("P-001"))
            self.assertIsNone(registry.load("P-001").work_id)
            # ② 批准过的卡：trash → restore 精确复位回 approved，号不变（set-once）
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-001")
            with registry.acting_as("user"):
                registry.trash(registry.load("P-001"), "deleted")
                self.assertEqual(registry.load("P-001").work_id, "R-001")  # 回收站保号
                registry.restore(registry.load("P-001"))
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertEqual(saved.work_id, "R-001")
            # ③ 手编回程票：trashed + prev_status=approved 的卡 restore 进 approved 也分号
            registry.upsert(_card("P-002", "带票", status=State.TRASHED.value,
                                  prev_status=State.APPROVED.value,
                                  trashed_at="2026-09-01T00:00:00Z"))
            with registry.acting_as("user"):
                registry.restore(registry.load("P-002"))
            self.assertEqual(registry.load("P-002").work_id, "R-002")
        self.for_each_backend(body)

    def test_abort_and_reapprove_keeps_the_same_number(self):
        def body(_b):
            registry.upsert(_card("P-001", "反复"))
            _approve("P-001")
            with registry.acting_as("user"):
                actd._apply_decision(registry.load("P-001"), "abort_execution", None)
            back = registry.load("P-001")
            self.assertEqual(back.status, State.CARD_SENT.value)
            self.assertEqual(back.work_id, "R-001")      # 退回提案不收回号
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-001")
            self.assertEqual(registry.next_work_id(), "R-002")
        self.for_each_backend(body)

    def test_sequence_is_dense_monotonic_and_never_reused(self):
        def body(backend):
            for i in range(1, 5):
                registry.upsert(_card(f"P-{i:03d}", f"卡{i}"))
            for i in (3, 1, 4, 2):
                _approve(f"P-{i:03d}")
            got = [registry.load(f"P-{i:03d}").work_id for i in (3, 1, 4, 2)]
            self.assertEqual(got, ["R-001", "R-002", "R-003", "R-004"])
            # 最大号的卡进回收站并硬删：号不复用（sqlite tombstone 保热列；yaml 靠
            # state/work_seq.json 高水位）
            with registry.acting_as("user"):
                registry.trash(registry.load("P-002"), "deleted")
            self.assertTrue(registry.delete(registry.load("P-002")))
            self.assertEqual(registry.next_work_id(), "R-005")
            seq = json.loads((config.STATE_DIR / registry.WORK_SEQ_NAME)
                             .read_text(encoding="utf-8"))
            self.assertEqual(seq, {"work_seq": 4})
        self.for_each_backend(body)

    def test_sequence_starts_above_legacy_keys(self):
        def body(_b):
            registry.upsert(_card("R-278", "存量最大号", status=State.DETECTED.value))
            registry.upsert(_card("P-001", "新卡"))
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-279")
            # resolve 无歧义：R-278 是 legacy 主键，R-279 是工作编号
            self.assertEqual(registry.resolve("R-278").id, "R-278")
            self.assertEqual(registry.resolve("R-279").id, "P-001")
        self.for_each_backend(body)

    def test_allocation_failure_does_not_break_save(self):
        def body(_b):
            registry.upsert(_card("P-001", "健壮"))
            with mock.patch.object(registry, "next_work_id",
                                   side_effect=RuntimeError("boom")):
                _approve("P-001")
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertIsNone(saved.work_id)          # 卡照常落盘，号下次补
            registry.save(saved)                      # 下一次 approved 落盘补号
            self.assertEqual(registry.load("P-001").work_id, "R-001")
        self.for_each_backend(body)

    def test_failed_write_releases_the_number(self):
        # sqlite：put_card 抛 → 内存里刚分的号清掉，重试不会带着同一个号撞 UNIQUE
        store2_testkit.use_backend(self, "sqlite")
        registry.upsert(_card("P-001", "写失败"))
        r = registry.load("P-001")
        r.set_status(State.APPROVED)
        with mock.patch.object(Store, "put_card",
                               side_effect=StoreError("X", "disk full")):
            with self.assertRaises(StoreError):
                registry.save(r)
        self.assertIsNone(r.work_id)
        registry.save(r)
        self.assertEqual(registry.load("P-001").work_id, "R-001")


# --------------------------------------------------------------------------- #
# 陈旧内存副本防御（§60.2）：盘上已发的号只会被采纳，绝不重铸/清空
# --------------------------------------------------------------------------- #
class StaleCopyAdoptsStoredNumberTestCase(_Both):
    """跨进程 fold 撞上 approve 的 read-modify-write 窗口（§53.5）：一份取自
    拿号之前的内存副本再落盘，必须采纳盘上的号——否则 sqlite 撞
    ``WORK_ID_SET_ONCE`` 硬失败（inbox 决策文件被当 poison 丢弃），yaml
    静默换号（重铸）或丢号（覆写成 None）。"""

    def test_stale_approved_copy_adopts_not_remints(self):
        def body(_b):
            registry.upsert(_card("P-001", "陈旧副本"))
            stale = registry.load("P-001")            # 拿号之前读出的副本
            self.assertIsNone(stale.work_id)
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-001")
            nw = registry.next_work_id()
            stale.set_status(State.APPROVED)
            registry.save(stale)                      # 不撞 set-once、不换号
            self.assertEqual(registry.load("P-001").work_id, "R-001")
            self.assertEqual(registry.next_work_id(), nw)     # 序列零消耗
        self.for_each_backend(body)

    def test_stale_executing_copy_does_not_clear_the_number(self):
        def body(_b):
            registry.upsert(_card("P-001", "丢号防御"))
            stale = registry.load("P-001")
            _approve("P-001")
            stale.set_status(State.EXECUTING)         # dispatch 形状的陈旧副本
            stale.work_id = None
            registry.save(stale)
            self.assertEqual(registry.load("P-001").work_id, "R-001")
        self.for_each_backend(body)

    def test_d21_literal_card_without_a_stored_number_stays_unnumbered(self):
        # 绕过 approved 直达 review/delivered 的 P 卡（§60.1 D21 字面）：真源里
        # 没号 → 采纳分支不发号、也不铸号，display 回落主键、id_kind=proposal。
        def body(_b):
            registry.upsert(_card("P-001", "外部完成"))
            req = registry.load("P-001")
            req.set_status(State.DELIVERED)          # done_external：card_sent→delivered
            with registry.acting_as("user"):
                registry.save(req)
            again = registry.load("P-001")
            self.assertIsNone(again.work_id)
            self.assertEqual(registry.id_kind(again), registry.ID_KIND_PROPOSAL)
            self.assertEqual(registry.display_id(again), "P-001")
        self.for_each_backend(body)

    def test_payload_stripped_by_old_code_readopts_from_hot_column(self):
        # §53.1「绝不手改 user_version」点名的腐蚀形状：< v0.48.13 的 save 整卡
        # 覆写 payload 时不认识 work_id 键 → payload 丢号、热列仍钉着号。
        # 新代码下一次落盘必须从热列采纳，而不是撞 WORK_ID_SET_ONCE 永远存不进去。
        store2_testkit.use_backend(self, "sqlite")
        registry.upsert(_card("P-001", "旧代码剥号"))
        _approve("P-001")
        store = registry._store()
        row = store.get_card("P-001")
        self.assertEqual(row["work_id"], "R-001")
        stripped = dict(row["payload"])
        stripped.pop("work_id", None)
        con = store._conn()
        con.execute("UPDATE cards SET payload = ? WHERE id = ?",
                    (json.dumps(stripped, ensure_ascii=False), "P-001"))
        registry.reset_store_cache()
        req = registry.load("P-001")
        self.assertIsNone(req.work_id)               # 内存副本：payload 里已无号
        registry.save(req)                           # 不抛 IntegrityViolation
        fresh = registry.load("P-001")
        self.assertEqual(fresh.work_id, "R-001")
        self.assertEqual(registry._store().get_card("P-001")["work_id"], "R-001")
        self.assertEqual(registry.next_work_id(), "R-002")   # 没有重铸


# --------------------------------------------------------------------------- #
# legacy 主键
# --------------------------------------------------------------------------- #
class LegacyKeysTestCase(_Both):
    def test_unapproved_legacy_card_is_greyed_not_renumbered(self):
        def body(_b):
            registry.upsert(_card("R-050", "存量提案"))
            r = registry.load("R-050")
            self.assertIsNone(r.work_id)
            self.assertEqual(registry.display_id(r), "R-050")
            self.assertEqual(registry.id_kind(r), registry.ID_KIND_LEGACY)
        self.for_each_backend(body)

    def test_approved_legacy_card_adopts_its_own_key(self):
        def body(_b):
            registry.upsert(_card("R-050", "存量提案"))
            _approve("R-050")
            r = registry.load("R-050")
            self.assertEqual(r.work_id, "R-050")
            self.assertEqual(registry.id_kind(r), registry.ID_KIND_WORK)
            # 采纳不消耗序列：下一张新卡拿 R-051
            registry.upsert(_card("P-001", "新卡"))
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-051")
        self.for_each_backend(body)

    def test_legacy_cards_past_approval_count_as_work_and_adopt_key_on_save(self):
        def body(_b):
            # 已交付的存量卡：升级后首次落盘采纳主键作 work_id；落盘前 id_kind 也已按
            # 状态判 work（不灰显——它的 R 号是批准后跑出来的）
            registry.upsert(_card("R-030", "已交付", status=State.DELIVERED.value))
            r = registry.load("R-030")
            self.assertEqual(r.work_id, "R-030")
            self.assertEqual(registry.id_kind(r), registry.ID_KIND_WORK)
            # 带 approved 回程票进回收站的存量卡同样算 work
            registry.upsert(_card("R-031", "回收站里的已批卡", status=State.TRASHED.value,
                                  prev_status=State.EXECUTING.value,
                                  trashed_at="2026-09-01T00:00:00Z"))
            self.assertEqual(registry.load("R-031").work_id, "R-031")
            # 从未批准的存量卡：raising / card_sent / 带 card_sent 票的回收站 = legacy
            for rid, st, prev in (("R-032", State.RAISING.value, None),
                                  ("R-033", State.TRASHED.value, State.CARD_SENT.value)):
                registry.upsert(_card(rid, "噪音", status=st, prev_status=prev,
                                      trashed_at="2026-09-01T00:00:00Z" if prev else None))
                got = registry.load(rid)
                self.assertIsNone(got.work_id, rid)
                self.assertEqual(registry.id_kind(got), registry.ID_KIND_LEGACY, rid)
            # 序列下界仍是 legacy 最大号（采纳不消耗）
            self.assertEqual(registry.next_work_id(), "R-034")
        self.for_each_backend(body)

    def test_legacy_approved_card_keeps_log_name(self):
        # 存量 approved 卡的派发日志仍叫 R-<n>.log（display_id 回落主键）
        store2_testkit.use_backend(self, "yaml")
        req = _card("R-060", "存量排队卡", status=State.APPROVED.value)
        self.assertEqual(registry.display_id(req), "R-060")
        self.assertEqual(executor.session_name(req), "R-060 · 存量排队卡")


# --------------------------------------------------------------------------- #
# resolve：inbox / merge 入口
# --------------------------------------------------------------------------- #
class ResolveTestCase(_Both):
    def test_resolve_by_key_or_work_id(self):
        def body(_b):
            registry.upsert(_card("P-001", "a"))
            _approve("P-001")
            self.assertEqual(registry.resolve("P-001").id, "P-001")
            self.assertEqual(registry.resolve("R-001").id, "P-001")
            self.assertIsNone(registry.resolve("R-999"))
            self.assertIsNone(registry.resolve(""))
            self.assertIsNone(registry.load_by_work_id("P-001"))   # 不是工作号形
        self.for_each_backend(body)

    def test_inbox_decision_by_work_id(self):
        def body(_b):
            for p in config.INBOX_DIR.glob("*.json"):
                p.unlink()
            registry.upsert(_card("P-001", "a"))
            _approve("P-001")
            payload = {"action": "abort_execution", "id": "R-001", "comment": None,
                       "ts": "2026-09-01T00:00:00Z"}
            (config.INBOX_DIR / f"{uuid.uuid4()}.json").write_text(
                json.dumps(payload), encoding="utf-8")
            actd._SYNC_ACTIVE_CACHE = None
            self.assertEqual(actd.process_inbox(), 1)
            self.assertEqual(registry.load("P-001").status, State.CARD_SENT.value)
        self.for_each_backend(body)

    def test_merge_force_by_work_ids_writes_key_lineage(self):
        def body(_b):
            registry.upsert(_card("P-001", "主"))
            registry.upsert(_card("P-002", "副"))
            _approve("P-001")
            _approve("P-002")
            with registry.acting_as("user"), \
                    mock.patch.object(actd, "_stop_live_session"):
                result = actd._apply_merge_force(["R-001", "R-002"], "R-001")
            self.assertEqual(result, "running")
            sec = registry.load("P-002")
            self.assertEqual(sec.status, State.MERGED.value)
            self.assertEqual(sec.merged_into, "P-001")   # lineage 只认主键
        self.for_each_backend(body)

    def test_canonical_ids_dedupes_key_and_work_id_of_same_card(self):
        store2_testkit.use_backend(self, "yaml")
        registry.upsert(_card("P-001", "a"))
        _approve("P-001")
        ids, missing = actd._canonical_ids(["P-001", "R-001", "R-777"])
        self.assertEqual(ids, ["P-001"])
        self.assertEqual(missing, ["R-777"])


# --------------------------------------------------------------------------- #
# 投影 / executor / 排序
# --------------------------------------------------------------------------- #
class ProjectionAndNamingTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_dashboard_rows_carry_display_id_and_kind(self):
        registry.upsert(_card("P-001", "提案"))
        registry.upsert(_card("P-002", "备选", status=State.DETECTED.value))
        registry.upsert(_card("R-050", "存量提案"))
        registry.upsert(_card("P-003", "已批"))
        _approve("P-003")
        with registry.acting_as("user"):
            registry.trash(registry.load("P-001"), "rejected")
        dash = dashboard.build_dashboard(agents=[], cfg=config.Config())
        rows = {r["id"]: r for sec in ("needs_approval", "running", "debt", "trash")
                for r in dash[sec]}
        self.assertEqual(rows["R-050"]["display_id"], "R-050")
        self.assertEqual(rows["R-050"]["id_kind"], "legacy")
        self.assertNotIn("work_id", rows["R-050"])
        self.assertEqual(rows["P-002"]["display_id"], "P-002")
        self.assertEqual(rows["P-002"]["id_kind"], "proposal")
        # 工作序列从 legacy 上界 R-050 之上起 → 第一张批准卡拿 R-051
        self.assertEqual(rows["P-003"]["display_id"], "R-051")
        self.assertEqual(rows["P-003"]["work_id"], "R-051")
        self.assertEqual(rows["P-003"]["id_kind"], "work")
        self.assertEqual(rows["P-003"]["id"], "P-003")       # 动作回传键不动
        self.assertEqual(rows["P-001"]["display_id"], "P-001")  # 回收站行同样带

    def test_executor_uses_display_id_for_human_faces(self):
        req = _card("P-007", "写周报", status=State.APPROVED.value, work_id="R-280")
        cfg = config.Config()
        cfg.memory_inject = False
        with mock.patch.object(executor, "has_remote", return_value=False):
            prompt = executor.build_prompt(req, cfg, target=Path(tempfile.mkdtemp()))
        self.assertIn("# Requirement R-280: 写周报", prompt)
        self.assertNotIn("# Requirement P-007", prompt)
        self.assertEqual(executor.session_name(req), "R-280 · 写周报")

    def test_dispatch_log_named_by_work_id_and_analytics_by_key(self):
        target = Path(tempfile.mkdtemp(prefix="ids-target-"))
        (target / "keep.txt").write_text("x", encoding="utf-8")
        req = _card("P-007", "派发", status=State.APPROVED.value,
                    target_repo=str(target))
        registry.save(req)                       # approved 落盘 → work_id R-001
        cfg = config.Config()
        cfg.memory_inject = False
        proc = mock.Mock(returncode=0, stdout="backgrounded · e88561e5\n", stderr="")
        with mock.patch.object(executor, "has_remote", return_value=False), \
                mock.patch.object(executor.notify, "notify"):
            out = executor.dispatch(registry.load("P-007"), cfg,
                                    runner=mock.Mock(return_value=proc))
        self.assertEqual(Path(out.execution["log"]).name, "R-001.log")
        self.assertIn("# dispatch R-001 (P-007)",
                      Path(out.execution["log"]).read_text(encoding="utf-8"))
        events = [json.loads(ln) for ln in
                  (config.STATE_DIR / "analytics" / "events.jsonl")
                  .read_text(encoding="utf-8").splitlines() if ln.strip()]
        disp = [e for e in events if e.get("event") == "dispatch" and e.get("req") == "P-007"]
        self.assertTrue(disp, "analytics 仍按主键记账")

    def test_cross_namespace_sort_key(self):
        ids = ["P-002", "R-300", "P-001", "R-002", "weird", "R-010"]
        self.assertEqual(sorted(ids, key=registry.id_sort_key),
                         ["R-002", "R-010", "R-300", "P-001", "P-002", "weird"])
        # auto_merge / quick_capture 的「更老」判断同源
        self.assertLess(auto_merge._idnum("R-300"), auto_merge._idnum("P-001"))

    def test_raising_fifo_prefers_legacy_backlog(self):
        registry.upsert(_card("P-001", "新的", status=State.RAISING.value))
        registry.upsert(_card("R-200", "老的", status=State.RAISING.value))
        picked = []
        with mock.patch.object(actd, "analyze") as an:
            an.expand_debt = lambda r: picked.append(r.id)
            actd.process_raising(config.Config())
        self.assertEqual(picked, ["R-200"])

    def test_inventory_orders_legacy_before_provisional(self):
        registry.upsert(_card("P-001", "新提案"))
        registry.upsert(_card("R-200", "老提案"))
        text = quick_capture.registry_inventory_text()
        self.assertLess(text.index("R-200"), text.index("P-001"))


# --------------------------------------------------------------------------- #
# store2 schema v1 → v2
# --------------------------------------------------------------------------- #
def _v1_schema_sql() -> str:
    """从 v2 schema.sql 反推 v1 形状（去掉 §60 的三件 + 版本钉回 1）——升级判例
    的起点必须是「真正没有 work_id 列」的库。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    sql = re.sub(r"\n  work_id\s+TEXT,\n", "\n", sql)
    sql = re.sub(r"CREATE UNIQUE INDEX IF NOT EXISTS cards_work_id\n.*?;\n", "", sql,
                 flags=re.S)
    sql = re.sub(r"CREATE TRIGGER IF NOT EXISTS cards_work_id_set_once\n.*?END;\n", "",
                 sql, flags=re.S)
    sql = sql.replace(f"PRAGMA user_version = {SCHEMA_VERSION};",
                      "PRAGMA user_version = 1;")
    assert "work_id" not in re.sub(r"--[^\n]*", "", sql), "v1 fixture still mentions work_id"
    return sql


def _objects(db: Path) -> dict:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"
                           " WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                           " ORDER BY type, name").fetchall()
        cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
    finally:
        con.close()
    return {"objects": {(t, n): tbl for t, n, tbl, _sql in rows}, "cards_cols": cols}


class SchemaUpgradeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-v2-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _v1_db(self, name="v1.db") -> Path:
        db = self.tmp / name
        con = sqlite3.connect(db)
        con.executescript(_v1_schema_sql())
        con.execute("INSERT INTO cards (id, status, title, created, updated, payload)"
                    " VALUES ('R-001', 'approved', 't', 'x', 'x', '{\"id\":\"R-001\"}')")
        con.commit()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        con.close()
        return db

    def test_v1_db_upgrades_in_place_and_keeps_rows(self):
        db = self._v1_db()
        store = Store(db)
        try:
            con = store._conn()
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
            self.assertIn("work_id", [r[1] for r in con.execute("PRAGMA table_info(cards)")])
            card = store.get_card("R-001")
            self.assertIsNone(card["work_id"])          # legacy 行不回填
            self.assertEqual(card["status"], "approved")
        finally:
            store.close()

    def test_upgraded_db_matches_fresh_db_shape(self):
        old = self._v1_db("old.db")
        Store(old).close()
        fresh = self.tmp / "fresh.db"
        Store(fresh).close()
        self.assertEqual(_objects(old)["objects"], _objects(fresh)["objects"])
        self.assertEqual(_objects(old)["cards_cols"], _objects(fresh)["cards_cols"])
        self.assertEqual(_objects(fresh)["cards_cols"][-1], "work_id")

    def test_crash_window_column_added_but_version_not_bumped(self):
        # ALTER 落了、版本没钉（升级途中崩）：重开必须幂等补全，不能 duplicate column
        db = self._v1_db()
        con = sqlite3.connect(db)
        con.execute("ALTER TABLE cards ADD COLUMN work_id TEXT")
        con.commit()
        con.close()
        store = Store(db)
        try:
            self.assertEqual(store._conn().execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
        finally:
            store.close()

    def test_future_version_still_refused(self):
        db = self._v1_db()
        con = sqlite3.connect(db)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.close()
        with self.assertRaises(StoreError) as cm:
            Store(db)
        self.assertEqual(cm.exception.code, "SCHEMA_VERSION_MISMATCH")

    def test_set_once_trigger_and_unique_index(self):
        store = Store(self.tmp / "t.db")
        try:
            base = {"id": "P-001", "title": "t", "type": "", "tier": "T1",
                    "status": "approved", "sources": [], "plan": None}
            hot_cols, _w, _e = hot.derive(export_yaml.normalize_card(
                dict(base, work_id="R-001")))
            store.put_card("P-001", dict(base, work_id="R-001"), hot_cols, [])
            # 改号 → WORK_ID_SET_ONCE
            hot2, _w, _e = hot.derive(export_yaml.normalize_card(dict(base, work_id="R-002")))
            with self.assertRaises(IntegrityViolation) as cm:
                store.put_card("P-001", dict(base, work_id="R-002"), hot2, [])
            self.assertEqual(cm.exception.code, "WORK_ID_SET_ONCE")
            # 清空 → 同样拒绝（payload 丢号不许静默抹掉）
            hot3, _w, _e = hot.derive(export_yaml.normalize_card(dict(base)))
            with self.assertRaises(IntegrityViolation):
                store.put_card("P-001", dict(base), hot3, [])
            # 另一张卡拿同号 → WORK_ID_DUPLICATE
            with self.assertRaises(IntegrityViolation) as cm:
                store.put_card("P-002", dict(base, id="P-002", work_id="R-001"),
                               dict(hot_cols), [])
            self.assertEqual(cm.exception.code, "WORK_ID_DUPLICATE")
            # tombstone 后热列仍在 → 序列不复用
            with registry.acting_as("user"):
                pass
            store.put_card("P-001", dict(base, work_id="R-001", status="trashed",
                                         prev_status="approved"),
                           dict(hot_cols, status="trashed", prev_status="approved"),
                           [], actor_type="user")
            store.purge_trashed("P-001")
            row = store.get_card("P-001")
            self.assertEqual((row["tombstone"], row["work_id"]), (1, "R-001"))
        finally:
            store.close()

    def test_upgrade_snapshots_the_v1_db_for_the_rollback_target(self):
        # §53.1 单向门条款：踏出 v1→v2 前留 <db>.pre-v1——旧代码（SCHEMA_VERSION=1，
        # user_version != 1 即 raise）唯一打得开的账本副本，D17 代码回滚落到
        # < 0.48.13 时的退路（TROUBLESHOOTING「store2 回滚」schema 降级段）。
        db = self._v1_db()
        pristine = self._v1_db("pristine.db")        # 从未被新代码碰过的 v1 对照
        Store(db).close()
        snap = pre_upgrade_snapshot_path(db, 1)
        self.assertEqual(snap, Path(str(db) + ".pre-v1"))
        self.assertTrue(snap.exists())
        # 单文件：backup 抄来的 WAL 头标已切回 DELETE，旁边不长 -wal/-shm、无 tmp 残留
        self.assertEqual([p.name for p in self.tmp.glob("v1.db.pre-v1*")], ["v1.db.pre-v1"])
        con = sqlite3.connect(snap)
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
            self.assertNotIn("work_id", cols)      # 升级前的形状，逐字保留
            rows = con.execute("SELECT id, status, payload FROM cards").fetchall()
            self.assertEqual(rows, [("R-001", "approved", '{"id":"R-001"}')])
        finally:
            con.close()
        # 形状 == 一个从未升级过的 v1 库（sqlite_master + 列序）——旧代码
        # （SCHEMA_VERSION=1 且只认 user_version==1）按构造打得开它
        self.assertEqual(_objects(snap), _objects(pristine))

    def test_old_reader_cannot_open_the_upgraded_db_but_can_open_the_snapshot(self):
        # 两份审查同一个 blocker 的可执行形态：D17 回滚目标（< v0.48.13）的门 =
        # `user_version != 1 → raise`。升级后的库对它必然关门（这是设计），
        # 快照对它必然开门——否则 TROUBLESHOOTING 的「恢复快照」出路是空话。
        def old_code_gate(path: Path) -> list:
            con = sqlite3.connect(path)
            try:
                v = con.execute("PRAGMA user_version").fetchone()[0]
                if v != 1:
                    raise StoreError("SCHEMA_VERSION_MISMATCH",
                                     f"db user_version={v}, store2 supports 1")
                return con.execute("SELECT id, status FROM cards ORDER BY id").fetchall()
            finally:
                con.close()
        db = self._v1_db()
        Store(db).close()
        with self.assertRaises(StoreError) as cm:
            old_code_gate(db)
        self.assertEqual(cm.exception.code, "SCHEMA_VERSION_MISMATCH")
        self.assertEqual(old_code_gate(pre_upgrade_snapshot_path(db, 1)),
                         [("R-001", "approved")])

    def test_snapshot_is_refreshed_on_each_run_of_the_step(self):
        # 真实降级剧本：升级 → 回滚代码 → 恢复快照 → 旧代码继续写卡 → 再部署新
        # 代码。第二次踏出 v1→v2 时快照必须刷新成「这一次升级前」的状态——
        # 只认第一份会把旧代码那阵子写的卡从退路里漏掉。写锁下已复核版本，
        # 刷新不可能把 v2 形状写进 pre-v1。
        db = self._v1_db()
        Store(db).close()
        snap = pre_upgrade_snapshot_path(db, 1)
        first = snap.read_bytes()
        # 恢复快照（TROUBLESHOOTING 步骤 2 的形状：主库连旁文件一起挪走）
        for side in ("", "-wal", "-shm"):
            p = Path(str(db) + side)
            if p.exists():
                p.replace(Path(str(db) + ".v2-stranded" + side))
        shutil.copyfile(snap, db)
        con = sqlite3.connect(db)                    # 「旧代码」在 v1 库上继续写
        con.execute("INSERT INTO cards (id, status, title, created, updated, payload)"
                    " VALUES ('R-002', 'detected', 't2', 'x', 'x', '{\"id\":\"R-002\"}')")
        con.commit()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        con.close()
        Store(db).close()                            # 新代码再部署：第二次踏出
        self.assertNotEqual(snap.read_bytes(), first)
        con = sqlite3.connect(snap)
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn("work_id", [r[1] for r in con.execute("PRAGMA table_info(cards)")])
            self.assertEqual([r[0] for r in con.execute("SELECT id FROM cards ORDER BY id")],
                             ["R-001", "R-002"])
        finally:
            con.close()
        # 被挪走的 v2 库原样还在（步骤 2 只挪不删）
        self.assertTrue(Path(str(db) + ".v2-stranded").exists())

    def test_concurrent_openers_converge_and_leave_one_v1_snapshot(self):
        # 多个进程（cron 雷达 + actd + server）同时首开一个 v1 库：都成功、
        # 只升一次、快照仍是 v1 形状（等锁者拿到锁后复核版本，不重拍、不重升）。
        db = self._v1_db()
        errors: list = []
        barrier = threading.Barrier(6)

        def opener():
            try:
                barrier.wait(timeout=10)
                Store(db).close()
            except BaseException as e:  # noqa: BLE001 - 收集后统一断言
                errors.append(e)
        threads = [threading.Thread(target=opener) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        con = sqlite3.connect(db)
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual([r[1] for r in con.execute("PRAGMA table_info(cards)")].count("work_id"), 1)
        finally:
            con.close()
        con = sqlite3.connect(pre_upgrade_snapshot_path(db, 1))
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn("work_id", [r[1] for r in con.execute("PRAGMA table_info(cards)")])
        finally:
            con.close()
        self.assertEqual([p.name for p in self.tmp.glob("v1.db.pre-v1*")], ["v1.db.pre-v1"])

    def test_snapshot_failure_refuses_the_upgrade(self):
        # 拍不下来 = SCHEMA_SNAPSHOT_FAILED 拒绝踏出单向门：DB 留在 v1（旧代码
        # 照样打得开）、无半截快照；障碍解除后重开即照常升级。
        db = self._v1_db()
        blocker = Path(str(db) + f".pre-v1.tmp-{os.getpid()}")
        blocker.mkdir()                    # sqlite 打不开目录 → 快照必失败
        with self.assertRaises(StoreError) as cm:
            Store(db)
        self.assertEqual(cm.exception.code, "SCHEMA_SNAPSHOT_FAILED")
        con = sqlite3.connect(db)
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            con.close()
        self.assertFalse(Path(str(db) + ".pre-v1").exists())
        blocker.rmdir()
        Store(db).close()
        self.assertTrue(Path(str(db) + ".pre-v1").exists())

    def test_fresh_db_takes_no_snapshot(self):
        # 全新库（version 0 → executescript）不走梯子，也就没有快照。
        fresh = self.tmp / "fresh2.db"
        Store(fresh).close()
        self.assertEqual(list(self.tmp.glob("fresh2.db.pre-v*")), [])

    def test_migrate_check_target_accepts_current_version_only(self):
        fresh = self.tmp / "f.db"
        Store(fresh).close()
        self.assertEqual(migrate_yaml.check_target(fresh), "schema-only")
        old = self._v1_db()
        with self.assertRaises(migrate_yaml.MigrateError):
            migrate_yaml.check_target(old)


# --------------------------------------------------------------------------- #
# 导出 ↔ 迁移 round-trip 带 work_id
# --------------------------------------------------------------------------- #
class ExportImportParityTestCase(unittest.TestCase):
    def test_work_id_survives_yaml_migration_and_export(self):
        tmp = Path(tempfile.mkdtemp(prefix="ids-rt-"))
        reg = tmp / "registry"
        reg.mkdir()
        (reg / "P-001.yaml").write_text(
            "id: P-001\ntitle: 带号的卡\ntype: dev\ntier: T1\nstatus: executing\n"
            "hardness: soft\ndeadline: null\nrepeated_mentions: 1\n"
            "green_sign_required: false\ndisagreement: null\ncost_estimate_usd: null\n"
            "sources: []\nplan: null\nwork_id: R-280\n", encoding="utf-8")
        (reg / "P-002.yaml").write_text(
            "id: P-002\ntitle: 无号\ntype: dev\ntier: T1\nstatus: detected\n"
            "hardness: soft\ndeadline: null\nrepeated_mentions: 1\n"
            "green_sign_required: false\ndisagreement: null\ncost_estimate_usd: null\n"
            "sources: []\nplan: null\n", encoding="utf-8")
        db = tmp / "s.db"
        self.assertEqual(migrate_yaml.main(["--registry", str(reg), "--db", str(db)]), 0)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT work_id FROM cards WHERE id='P-001'")
                         .fetchone()[0], "R-280")
        self.assertIsNone(con.execute("SELECT work_id FROM cards WHERE id='P-002'")
                          .fetchone()[0])
        con.close()
        out = tmp / "export"
        self.assertEqual(export_yaml.export_db(db, out), 0)
        self.assertEqual((out / "P-001.yaml").read_text(encoding="utf-8"),
                         (reg / "P-001.yaml").read_text(encoding="utf-8"))
        self.assertEqual((out / "P-002.yaml").read_text(encoding="utf-8"),
                         (reg / "P-002.yaml").read_text(encoding="utf-8"))

    def test_normalize_card_mirrors_from_dict_for_work_id(self):
        raw = {"id": "P-001", "title": "t", "work_id": 280}     # 手编成 int
        want = Requirement.from_dict(raw).to_dict()
        got = export_yaml.normalize_card(raw)
        self.assertEqual(list(got.items()), list(want.items()))
        self.assertEqual(got["work_id"], "280")


if __name__ == "__main__":
    unittest.main()
