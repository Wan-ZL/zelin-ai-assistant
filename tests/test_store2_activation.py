"""store2 激活协议判例（CONTRACT §53.3/§53.4/§53.6；owner 决策 D2 / R2.1.2-3）。

首跑切换 = 备份整个 YAML registry（含 archive/，带 sha256 manifest，永不覆盖
既有备份）→ 从**备份**迁移 → 导出 → 与备份逐字段比对 → 零差异且 live 目录
未被并发写 → 才写真源标记。任何差异/不忠实 = 拒绝：删库、留响亮台账
（state/store2_activation.json）、YAML 照旧是真源、doctor FAIL——绝无半态。
激活后每本地日导出一份 YAML 镜像到 state/registry-export/（增量幂等 + prune，
目录大小 = 活卡数，不增长）。
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act.lib import config, registry
from act.lib.store2 import activate

CARD = """id: {rid}
title: {title}
type: dev
tier: T1
status: card_sent
hardness: soft
deadline: null
repeated_mentions: 1
green_sign_required: false
disagreement: null
cost_estimate_usd: null
sources:
- channel: meeting
  date: '2026-08-30'
  ref: r-{rid}
  quote: q
  who: manager
plan:
- do it
"""


def _seed(rid="R-001", title="激活样卡", extra=""):
    (config.REGISTRY_DIR / f"{rid}.yaml").write_text(
        CARD.format(rid=rid, title=title) + extra, encoding="utf-8")


class ActivationTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "auto")

    def test_first_run_backs_up_migrates_and_flips_the_truth(self):
        _seed("R-001")
        _seed("R-002", title="第二张")
        lines = activate.tick()
        self.assertTrue(any("ACTIVATED" in ln for ln in lines), lines)
        st = activate.status()
        self.assertEqual(st["state"], "active")
        self.assertEqual(registry.backend(), registry.BACKEND_SQLITE)
        # 备份完整 + manifest（sha256 逐文件）
        backups = sorted(registry.registry_backups_dir().glob("registry-*"))
        dirs = [b for b in backups if b.is_dir()]
        self.assertEqual(len(dirs), 1)
        self.assertTrue((dirs[0] / "R-001.yaml").exists())
        man = json.loads(dirs[0].with_suffix(".manifest.json")
                         .read_text(encoding="utf-8"))
        self.assertIn("R-001.yaml", man["files"])
        # 导出镜像 = 备份逐字段零差异（激活协议的核心不变量）
        self.assertEqual(
            activate.parity_diff(dirs[0], registry.registry_export_dir()), [])
        # 真源切换后 API 直接可用
        self.assertEqual(sorted(r.id for r in registry.load_all()),
                         ["R-001", "R-002"])
        # 幂等：再 tick 不再激活、不再导出（当天已导）
        self.assertEqual(activate.tick(), [])

    def test_fresh_install_with_no_yaml_activates_empty(self):
        lines = activate.tick()
        self.assertTrue(any("ACTIVATED" in ln for ln in lines), lines)
        self.assertEqual(registry.backend(), registry.BACKEND_SQLITE)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(registry.next_id(), "R-001")

    def test_unknown_key_refuses_and_yaml_stays_truth(self):
        _seed("R-001")
        _seed("R-002", extra="wild_key: 悄悄丢字段\n")
        lines = activate.tick()
        self.assertTrue(any("REFUSED" in ln for ln in lines), lines)
        st = activate.status()
        self.assertEqual(st["state"], "cooldown")   # 拒绝后进退避窗口
        self.assertEqual(registry.backend(), registry.BACKEND_YAML)
        self.assertFalse(registry.store2_db_path().exists())   # 无标记的库已丢弃
        self.assertFalse(registry.store2_truth_path().exists())
        act_info = json.loads(registry.store2_activation_path()
                              .read_text(encoding="utf-8"))
        self.assertEqual(act_info["result"], "refused")
        self.assertIn("wild_key", json.dumps(act_info, ensure_ascii=False))
        # 备份仍在（安全网不回收）
        self.assertTrue(any(registry.registry_backups_dir().glob("registry-*")))
        # 管线零感知：YAML 照常读写
        self.assertEqual(len(registry.load_all()), 2)
        # 退避窗口内不再尝试（ensure 短路）
        self.assertEqual(activate.tick(), [])

    def test_duplicate_id_refuses_as_lossy(self):
        _seed("R-001")
        (config.REGISTRY_DIR / "R-001-copy.yaml").write_text(
            CARD.format(rid="R-001", title="重复 id"), encoding="utf-8")
        activate.tick()
        act_info = json.loads(registry.store2_activation_path()
                              .read_text(encoding="utf-8"))
        self.assertEqual(act_info["result"], "refused")
        self.assertIn("duplicate id", act_info["reason"])
        self.assertEqual(registry.backend(), registry.BACKEND_YAML)

    def test_concurrent_writer_during_migration_refuses_with_short_retry(self):
        _seed("R-001")
        real_parity = activate.parity_diff

        def parity_then_race(backup_dir, export_dir):
            # 迁移窗口内另一个进程落了新卡——manifest 复检必须拒绝本次激活
            _seed("R-099", title="迁移中落的卡")
            return real_parity(backup_dir, export_dir)

        with mock.patch.object(activate, "parity_diff", parity_then_race):
            res = activate.first_run()
        self.assertEqual(res["result"], "refused")
        self.assertIn("changed while migrating", res["reason"])
        self.assertFalse(registry.store2_db_path().exists())
        self.assertEqual(registry.backend(), registry.BACKEND_YAML)
        # 短退避（60s 级）而非 6h：race 很快就能重试
        retry = _dt.datetime.strptime(res["retry_after"], activate.TS_FMT)
        now = _dt.datetime.utcnow()
        self.assertLess((retry - now).total_seconds(), 600)

    def test_backup_dir_is_never_overwritten(self):
        fixed = _dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
        d1 = activate.fresh_backup_dir(fixed)
        d1.mkdir(parents=True)
        d2 = activate.fresh_backup_dir(fixed)
        self.assertNotEqual(d1, d2)
        self.assertTrue(str(d2).endswith("-2"))

    def test_forced_backend_never_auto_migrates(self):
        _seed("R-001")
        import os
        os.environ["ZAI_REGISTRY_BACKEND"] = "yaml"
        registry.reset_store_cache()
        try:
            self.assertEqual(activate.tick(), [])
            self.assertFalse(registry.store2_truth_path().exists())
            self.assertEqual(activate.status()["state"], "yaml_forced")
        finally:
            os.environ.pop("ZAI_REGISTRY_BACKEND", None)
            registry.reset_store_cache()

    def test_daily_export_runs_once_per_local_day(self):
        _seed("R-001")
        activate.tick()
        # 把导出标记拨回昨天 → 下一个 tick 重新导出一次
        marker = activate.export_marker_path()
        body = json.loads(marker.read_text(encoding="utf-8"))
        body["last_run"] = "2020-01-01"
        marker.write_text(json.dumps(body), encoding="utf-8")
        lines = activate.tick()
        self.assertTrue(any("daily YAML export" in ln for ln in lines), lines)
        self.assertEqual(activate.tick(), [])   # 当天第二次：静默

    def test_export_mirrors_and_prunes_with_the_ledger(self):
        _seed("R-001")
        activate.tick()
        with registry.acting_as("user"):
            registry.trash(registry.load("R-001"), "deleted")
        registry.delete(registry.load("R-001"))     # tombstone 硬删
        registry.upsert(registry.Requirement(id="R-010", title="新卡",
                                             status="detected"))
        activate.refresh_export()
        names = sorted(p.name for p in
                       registry.registry_export_dir().glob("*.yaml"))
        self.assertEqual(names, ["R-010.yaml"])     # 死卡随 prune 消失，不增长

    def test_db_missing_half_state_fails_loud(self):
        _seed("R-001")
        activate.tick()
        registry.reset_store_cache()     # 先关缓存连接（Windows 锁着删不掉）
        registry.store2_db_path().unlink()
        self.assertEqual(activate.status()["state"], "db_missing")
        self.assertEqual(registry.backend(), registry.BACKEND_SQLITE)
        with self.assertRaises(RuntimeError):
            registry.load_all()
        lines = activate.tick()
        self.assertTrue(any("FAIL" in ln for ln in lines), lines)


class DoctorRowTestCase(unittest.TestCase):
    """doctor `store2` 行：数据层状态的诚实报告（§53.6）。"""

    def setUp(self):
        store2_testkit.use_backend(self, "auto")

    def _row(self):
        from act import doctor
        res = doctor._check_store2(doctor.Probes())
        return res if not isinstance(res, list) else res[0]

    def test_pending_reports_ok_with_migration_notice(self):
        _seed("R-001")
        row = self._row()
        self.assertEqual(row.status, "ok")

    def test_active_reports_ok(self):
        _seed("R-001")
        activate.tick()
        row = self._row()
        self.assertEqual(row.status, "ok")
        self.assertIn("SQLite is the registry truth", row.detail)

    def test_refused_reports_fail_with_reason(self):
        _seed("R-001", extra="wild_key: x\n")
        activate.tick()
        row = self._row()
        self.assertEqual(row.status, "fail")
        self.assertEqual(row.failure_id, "store2_refused")

    def test_db_missing_reports_fail(self):
        _seed("R-001")
        activate.tick()
        registry.reset_store_cache()     # 先关缓存连接（Windows 锁着删不掉）
        registry.store2_db_path().unlink()
        row = self._row()
        self.assertEqual(row.status, "fail")
        self.assertEqual(row.failure_id, "store2_db_missing")

    def test_late_yaml_writes_warn(self):
        _seed("R-001")
        activate.tick()
        import time
        time.sleep(1.1)   # mtime 必须晚于 activated_at（秒级戳）
        _seed("R-050", title="迟到的旁路写")
        row = self._row()
        self.assertEqual(row.status, "warn")
        self.assertIn("R-050", row.detail)


if __name__ == "__main__":
    unittest.main()
