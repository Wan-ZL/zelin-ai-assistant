"""registry 公开 API 的双后端一致性判例（CONTRACT §53 / R2.1.1）。

同一串真实操作剧本（铸卡/折叠/re-raise/trash/restore/pin/archive/unarchive/
改显示名/拆备注/删除/next_id）分别跑在 yaml 与 store2(sqlite) 后端上，
逐字段比较最终账本与每步回报——调用方（actd/雷达/digest/dashboard/server）
换真源后行为必须零漂移。时间戳经 registry._iso_now 注入缝钉死成确定序列，
两轮剧本产生完全相同的账。

已知且有意的分歧（单独判例钉住，不进剧本比较）：
- next_id 对「已硬删的最大 id」：yaml 会复用文件号（历史危险行为），
  sqlite 的 tombstone 行钉住 id 永不复用（§53.2）。

§60（D21）起 next_id 发 ``P-<n>`` 主键、approve 落盘分配 ``R-<m>`` work_id
——两后端必须分出**同一个**号（剧本里 P-001 批准 → work_id R-004，即存量
legacy 主键上界 + 1；legacy R-001 批准 → 采纳自己的主键 R-001），分配后的
work_id 进快照比较，trash→restore 回 approved 时 set-once 不重分。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act.lib import registry
from act.lib.registry import Requirement
from act.lib.store2.export_yaml import normalize_card


def _ts_gen():
    n = [0]

    def _next():
        n[0] += 1
        return f"2026-09-01T10:{n[0] // 60:02d}:{n[0] % 60:02d}Z"
    return _next


def _card(rid, title, status="card_sent", **kw):
    base = dict(id=rid, title=title, type="dev", tier="T1", status=status,
                hardness="soft", repeated_mentions=1,
                sources=[{"channel": "meeting", "date": "2026-08-30",
                          "ref": f"ref-{rid}", "quote": f"quote {title}",
                          "who": "manager"}],
                plan=["step 1"], summary=f"summary of {title}")
    base.update(kw)
    return Requirement.from_dict(base)


def _snapshot():
    reqs = registry.load_all(include_archived=True)
    return {r.id: normalize_card(r.to_dict()) for r in reqs}


class BackendParityTestCase(unittest.TestCase):
    maxDiff = None

    def _run_scenario(self, backend):
        store2_testkit.use_backend(self, backend)
        log = []
        with mock.patch.object(registry, "_iso_now", side_effect=_ts_gen()):
            # 铸卡（各态出生 + unicode + plan str 形 + delivery_mode chat）
            registry.upsert(_card("R-001", "写周报", status="card_sent"))
            registry.upsert(_card("R-002", "备选事项", status="detected",
                                  plan="line1\nline2"))
            registry.upsert(_card("R-003", "已交付的事", status="delivered",
                                  delivery_mode="chat",
                                  execution={"session_id": "s-3", "done": True}))
            log.append(("next_id", registry.next_id()))          # P-001（§60）
            log.append(("next_work_id", registry.next_work_id()))  # R-004

            # merge_or_new：纯重述折叠（同标题）
            kind, saved = registry.merge_or_new_with_kind(
                {"title": "写周报", "sources": [
                    {"channel": "slack", "date": "2026-08-31",
                     "ref": "ts-1", "quote": "再说一遍", "who": "manager"}]})
            log.append(("fold", kind, saved.id))
            # merge_or_new：全新卡
            kind, saved = registry.merge_or_new_with_kind(
                {"title": "全新的事", "sources": [
                    {"channel": "gmail", "date": "2026-08-31",
                     "ref": "m-1", "quote": "new ask", "who": "hr"}]})
            log.append(("new", kind, saved.id))
            # merge_or_new：resolved parent 的 re-raise（同标题 + 增量）
            kind, saved = registry.merge_or_new_with_kind(
                {"title": "已交付的事", "deadline": "2026-09-10",
                 "hardness": "hard", "sources": [
                     {"channel": "meeting", "date": "2026-09-01",
                      "ref": "m-2", "quote": "again", "who": "manager"}]})
            log.append(("reraise", kind, saved.id, str(saved.status)))

            # 用户动作族
            with registry.acting_as("user"):
                r = registry.load("R-001")
                r.set_status("approved")
                registry.save(r)              # §60：legacy 主键 → work_id 采纳 R-001
                log.append(("work_id_legacy", registry.load("R-001").work_id))
                # merge_or_new 铸的新卡是 P-001（不再消耗 R 号，issue #127）；
                # 批准 → 工作编号 R-004（legacy 上界 3 + 1），resolve 双向可达
                p1 = registry.load("P-001")
                p1.set_status("approved")
                registry.save(p1)
                log.append(("work_id_new", registry.load("P-001").work_id))
                log.append(("resolve", registry.resolve("R-004").id,
                            registry.resolve("P-001").work_id))
                registry.set_display_title(registry.load("R-002"), "备选的新名字",
                                           by_user=True)
                r2 = registry.load("R-002")
                registry.set_display_title(r2, "备选的新名字", by_user=True)
                registry.save(r2)
                registry.trash(registry.load("P-001"), "deleted")
                registry.restore(registry.load("P-001"))   # 回 approved：set-once
                log.append(("work_id_after_restore", registry.load("P-001").work_id))
                registry.trash(registry.load("P-001"), "deleted")
                registry.pin(registry.load("P-001"))
                registry.archive(registry.load("R-002"), reason="user")
                registry.unarchive(registry.load("R-002"))
                registry.archive(registry.load("R-002"), reason="user")

            # 管线动作（system）：fold note + 拆出标记
            r1 = registry.load("R-001")
            ts = registry.append_fold_note(r1, "radar 补充信息", "radar")
            registry.save(r1)
            r1 = registry.load("R-001")
            self.assertTrue(registry.mark_note_split(r1, ts, "R-999"))
            registry.save(r1)

            # 回收站硬删（删非最大 id——最大 id 的复用分歧见下一条判例）
            registry.upsert(_card("R-005", "要删的卡", status="detected"))
            registry.upsert(_card("R-006", "钉住最大号", status="detected"))
            with registry.acting_as("user"):
                registry.trash(registry.load("R-005"), "rejected")
            deleted = registry.delete(registry.load("R-005"))
            log.append(("delete", deleted))
            log.append(("next_id_final", registry.next_id()))

            log.append(("archived", sorted(r.id for r in registry.load_archived())))
            log.append(("live", sorted(r.id for r in registry.load_all())))
            log.append(("load_missing", registry.load("R-404") is None))
            log.append(("load_deleted", registry.load("R-005") is None))
        return log, _snapshot()

    def test_every_public_function_is_backend_identical(self):
        yaml_log, yaml_snap = self._run_scenario("yaml")
        sq_log, sq_snap = self._run_scenario("sqlite")
        self.assertEqual(yaml_log, sq_log)
        self.assertEqual(yaml_snap, sq_snap)

    def test_next_id_never_reuses_a_purged_id_on_sqlite(self):
        # 有意分歧：yaml 硬删最大 id 后会复用号码；sqlite tombstone 钉死 id。
        # （§60 起 next_id 发 P- 主键，判例改用 P 命名空间，语义不变）
        store2_testkit.use_backend(self, "sqlite")
        registry.upsert(_card("P-007", "最大号", status="card_sent"))
        with registry.acting_as("user"):
            registry.trash(registry.load("P-007"), "deleted")
        self.assertTrue(registry.delete(registry.load("P-007")))
        self.assertEqual(registry.next_id(), "P-008")

    def test_agent_wall_is_identical_on_both_backends(self):
        from act.lib.store2.store import TransitionDenied
        for backend in ("yaml", "sqlite"):
            with self.subTest(backend=backend):
                store2_testkit.use_backend(self, backend)
                registry.upsert(_card("R-010", "墙测试", status="card_sent"))
                r = registry.load("R-010")
                r.set_status("approved")
                with registry.acting_as("agent"):
                    with self.assertRaises(TransitionDenied):
                        registry.save(r)
                self.assertEqual(registry.load("R-010").status, "card_sent")


if __name__ == "__main__":
    unittest.main()
