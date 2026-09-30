"""自动行为总账本身的判例（CONTRACT §81；issue #451 / owner 决策 D83）。

这里钉的是**表的自洽**与三个公开 API 的语义：词表闭集、指针不空头、
:func:`enabled` 的 fail-closed、:func:`audit` 的形状与自压缩、
:func:`live_fields` 的派生。门（scripts/qa/automation_check.py）的判例住在
tests/test_qa_automation_gate.py——一个 behavior 一个文件。
"""
import json
import os
import unittest
from pathlib import Path

from act.lib import automation, config
from tests.scratch_testkit import scratch_dir


def _poison(cfg, name):
    """把冻结 cfg 上的一把开关写成一个绝不会是出厂值的东西（三种拼法各一种写法）。"""
    if "." not in name:
        setattr(cfg, name, "POISONED")
        return
    head, tail = name.split(".", 1)
    if head == "features":
        cfg.features[tail] = "POISONED"
        return
    block = cfg.raw.setdefault(head, {}) if isinstance(cfg.raw, dict) else None
    if isinstance(block, dict):
        block[tail] = "POISONED"


class VocabularyTestCase(unittest.TestCase):
    """词表是闭集，slug 唯一，lineage 指得到活行。"""

    def test_every_row_uses_the_closed_vocabulary(self):
        for row in automation.LEDGER:
            self.assertIn(row.runner, automation.RUNNERS, row.slug)
            self.assertIn(row.kind, automation.KINDS, row.slug)
            self.assertIn(row.verdict, automation.VERDICTS, row.slug)
            self.assertTrue(row.effect, row.slug)
            self.assertLessEqual(set(row.effect), set(automation.EFFECTS), row.slug)

    def test_slugs_are_unique(self):
        slugs = automation.slugs()
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_lineage_points_at_real_rows(self):
        known = set(automation.slugs())
        for row in automation.LEDGER:
            for other in row.overlaps:
                self.assertIn(other, known, "%s overlaps unknown %s" % (row.slug, other))
            if row.verdict == automation.VERDICT_MERGED:
                self.assertIn(row.merged_into, known, row.slug)
                self.assertNotEqual(row.merged_into, row.slug)
            else:
                self.assertEqual(row.merged_into, "", row.slug)

    def test_every_row_says_why_and_where(self):
        """`why` 是 issue #451 第 1 问的答案，`code` / `law` 是它的证据。"""
        for row in automation.LEDGER:
            self.assertTrue(row.why.strip(), row.slug)
            self.assertTrue(row.code.strip(), row.slug)
            self.assertTrue(row.law, row.slug)

    def test_costly_is_exactly_the_three_ask4_classes(self):
        self.assertEqual(set(automation.COSTLY),
                         {automation.EFFECT_CARDS, automation.EFFECT_DELETE,
                          automation.EFFECT_SPEND})


class EnabledTestCase(unittest.TestCase):
    """`enabled()` 的判据：fail-closed、合取、非 keep 行永假。"""

    def test_unknown_slug_is_off(self):
        self.assertFalse(automation.enabled("no-such-behaviour", config.Config()))

    def test_a_merged_row_never_runs_again(self):
        merged = [r for r in automation.LEDGER if r.verdict == automation.VERDICT_MERGED]
        self.assertTrue(merged, "总账里至少该有一条并入行（mac_notify_relay）")
        for row in merged:
            self.assertFalse(automation.enabled(row.slug, config.Config()), row.slug)

    def test_a_row_without_a_switch_is_on(self):
        """没有开关 ≠ 偷偷关掉：它今天本来就一直在跑，总账只是把这个事实写明。"""
        rows = [r for r in automation.kept()
                if r.kind == automation.KIND_NONE and not r.switch]
        for row in rows:
            self.assertTrue(automation.enabled(row.slug, config.Config()), row.slug)

    def test_the_switch_is_a_conjunction(self):
        cfg = config.Config()
        self.assertTrue(automation.enabled("silent_merge", cfg))
        cfg.features["merge_silent"] = False
        self.assertFalse(automation.enabled("silent_merge", cfg))

    def test_threshold_zero_is_off(self):
        cfg = config.Config(trash_retention_days=30)
        self.assertTrue(automation.enabled("purge_trash", cfg))
        cfg.trash_retention_days = 0
        self.assertFalse(automation.enabled("purge_trash", cfg))

    def test_hard_purge_is_off_out_of_the_box(self):
        """§81 / D83 的那一把真翻的默认：出厂不做不可恢复的自动删除。"""
        self.assertFalse(automation.enabled("purge_trash", config.Config()))

    def test_an_enum_knob_spelled_off_is_off(self):
        """`digest.frequency: off` 是字符串——bool("off") 为真会把出厂关着的
        旋钮读成开着（§17 / D19 的默认就会被误报）。"""
        self.assertFalse(automation.enabled("digest_card", config.Config()))

    def test_a_raw_block_switch_is_read_from_cfg_raw(self):
        """`autodispatch.enabled` 住在 cfg.raw 里（§51 的闸只从那里读）。"""
        cfg = config.Config()
        cfg.raw = {"autodispatch": {"enabled": True}}
        self.assertTrue(automation.enabled("auto_dispatch", cfg))
        cfg.raw = {"autodispatch": {"enabled": False}}
        self.assertFalse(automation.enabled("auto_dispatch", cfg))

    def test_an_unwritten_raw_block_follows_that_block_s_factory_default(self):
        """盘上没写过 `autodispatch:` 块 ≠ 关。

        第一版把「键不在」读成 None 进而判关，于是出厂 Config 下
        `auto_dispatch` 报「已经关着」——整条管线里最贵的「没人点过、卡却自己
        批准并开了 LLM 会话」就这样从 ask 4 的账单底下溜过去了。真源是
        `policy.AUTODISPATCH_DEFAULTS["enabled"] = True`。
        """
        self.assertTrue(automation.enabled("auto_dispatch", config.Config()))
        empty = config.Config()
        empty.raw = {"autodispatch": {}}          # 块在、键不在
        self.assertTrue(automation.enabled("auto_dispatch", empty))
        explicit_off = config.Config()
        explicit_off.raw = {"autodispatch": {"enabled": False}}
        self.assertFalse(automation.enabled("auto_dispatch", explicit_off))

    def test_the_autodispatch_default_matches_policys_own(self):
        """两处不许分叉：总账判出来的出厂值 = §51 自己那张默认表。"""
        from act.lib import policy
        self.assertEqual(automation.enabled("auto_dispatch", config.Config()),
                         bool(policy.autodispatch_config(config.Config())["enabled"]))

    def test_truthy_covers_every_arm(self):
        """`_truthy` 的四条臂各钉一次——缺席、阈值、枚举字符串、裸布尔。"""
        t = automation._truthy
        self.assertTrue(t(automation._ABSENT, automation.KIND_BOOL))
        self.assertFalse(t(automation._ABSENT, automation.KIND_THRESHOLD))
        self.assertTrue(t("30", automation.KIND_THRESHOLD))
        self.assertFalse(t("forever", automation.KIND_THRESHOLD))
        self.assertFalse(t(None, automation.KIND_THRESHOLD))
        self.assertFalse(t("off", automation.KIND_BOOL))
        self.assertTrue(t("weekly", automation.KIND_BOOL))
        self.assertTrue(t(True, automation.KIND_BOOL))
        self.assertFalse(t(None, automation.KIND_BOOL))

    def test_a_broken_config_never_raises(self):
        """宪法第 11 条：开关判定自身绝不反杀调用方。"""

        class _Hostile:
            raw = "not a dict"

            def feature(self, _name):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            _Hostile().feature("x")          # 前提：这个对象真的会炸
        self.assertFalse(automation.enabled("purge_trash", _Hostile()))


class LiveFieldsTestCase(unittest.TestCase):
    """热开关名单是**派生**的——总账加一行就自动变热，没有第二处要抄。"""

    def test_only_actd_rows_with_a_switch_contribute(self):
        live = set(automation.live_fields())
        for row in automation.LEDGER:
            if row.runner == automation.RUNNER_ACTD and row.switch:
                for name in row.switch:
                    self.assertIn(name, live, "%s:%s" % (row.slug, name))
            elif row.runner != automation.RUNNER_ACTD:
                self.assertFalse(row.live, row.slug)

    def test_the_five_dead_switches_from_451_are_now_live(self):
        """issue #451「开关只在 actd 启动时读一次」点名的那几把。"""
        live = set(automation.live_fields())
        for name in ("trash_retention_days", "card_summary_enabled",
                     "updates_check_enabled", "features.feedback_sync",
                     "autodispatch.enabled", "archive_after_days"):
            self.assertIn(name, live, name)

    def test_the_order_is_stable_and_deduped(self):
        fields = automation.live_fields()
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual(fields, automation.live_fields())

    def test_a_yaml_null_switch_stays_off_across_a_refresh(self):
        """`autodispatch:\\n  enabled:`（YAML null）= 关，刷新不许把它刷成开。

        `policy.autodispatch_config` 分得出「写了但空值」（`bool(None)` = 关）与
        「压根没写」（= 出厂开）；刷新点最初照 `_refresh_owner_logins` 那样按
        None 删键，于是这份 config 启动时免批是关的、第一个 pass 之后自己变成
        开的——整条管线里最贵的那条自动行为，被一个「为了让开关更可信」才加的
        刷新点朝着 ask 4 明令禁止的方向掰了过去。
        """
        from unittest import mock

        from act import actd

        frozen = config.Config()
        frozen.raw = {"autodispatch": {"enabled": None, "max_concurrent": 3}}
        fresh = config.Config()
        fresh.raw = {"autodispatch": {"enabled": None, "max_concurrent": 3}}
        self.assertFalse(automation.enabled("auto_dispatch", frozen))
        with mock.patch.object(config, "load_config", return_value=fresh):
            actd._refresh_automation_switches(frozen, fresh)
        self.assertFalse(automation.enabled("auto_dispatch", frozen))
        self.assertIn("enabled", frozen.raw["autodispatch"])

    def test_a_key_that_left_the_disk_falls_back_to_the_factory_default(self):
        """反过来的那一半：盘上删掉了这一键 = 回到出厂默认，不是留着旧值。"""
        from unittest import mock

        from act import actd

        frozen = config.Config()
        frozen.raw = {"autodispatch": {"enabled": False}}
        fresh = config.Config()
        fresh.raw = {"autodispatch": {}}
        with mock.patch.object(config, "load_config", return_value=fresh):
            actd._refresh_automation_switches(frozen, fresh)
        self.assertNotIn("enabled", frozen.raw["autodispatch"])
        self.assertTrue(automation.enabled("auto_dispatch", frozen))

    def test_actd_really_refreshes_every_live_field(self):
        """不变量 2 的**行为**判例，不是名单比对：把冻结 cfg 上的每一把都写坏，
        跑一次 actd 的刷新点，盘上的出厂值必须全都被读回来。

        这条比「两份名单互为子集」硬——`daily_loop.LIVE_KNOBS` 是本名单的前身，
        里面还混着 `daily_loop_time` 这类调参（不是 on/off，本来就不该进 switch），
        真正要钉的是「总账说热的，actd 就真的每 pass 现读」。
        """
        from unittest import mock

        from act import actd

        home = scratch_dir(self, prefix="live-switch-")
        frozen = config.Config()
        for name in automation.live_fields():
            _poison(frozen, name)
        fresh = config.Config()
        with mock.patch.dict(os.environ, {"AIASSISTANT_HOME": home}), \
                mock.patch.object(config, "load_config", return_value=fresh):
            actd._refresh_automation_switches(frozen, fresh)
        for name in automation.live_fields():
            self.assertEqual(automation._switch_value(frozen, name),
                             automation._switch_value(fresh, name), name)


class AuditTestCase(unittest.TestCase):
    """一行「<slug> 做了 <action>」：形状、消毒、带帽、永不抛。"""

    def setUp(self):
        self.dir = scratch_dir(self, prefix="automation-audit-")
        self.path = Path(self.dir) / "automation.jsonl"

    def test_it_writes_one_json_line_per_call(self):
        self.assertTrue(automation.audit("purge_trash", "acted", path=self.path, purged=3))
        self.assertTrue(automation.audit("purge_trash", "acted", path=self.path, purged=1))
        rows = [json.loads(line) for line
                in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["purged"] for r in rows], [3, 1])
        self.assertEqual({r["slug"] for r in rows}, {"purge_trash"})
        self.assertEqual({r["action"] for r in rows}, {"acted"})
        self.assertTrue(all(r["ts"].endswith("Z") for r in rows))

    def test_long_fields_are_clipped_not_stored_whole(self):
        automation.audit("purge_trash", "acted", path=self.path, note="x" * 5000)
        row = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(len(row["note"]), 200)

    def test_an_unwritable_path_returns_false_instead_of_raising(self):
        """父目录的位置上是一个**文件** → mkdir 抛 OSError，audit 只能回 False。"""
        blocker = Path(self.dir) / "blocker"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")
        self.assertFalse(automation.audit("purge_trash", "acted",
                                          path=blocker / "automation.jsonl"))

    def test_a_non_scalar_field_is_stringified_not_rejected(self):
        """消毒在写之前：标量原样，其余 str() 后截断——所以没有任何一种附加
        字段能把这一行变成不可序列化的 JSON。"""
        self.assertTrue(automation.audit("purge_trash", "acted", path=self.path,
                                         blob=object()))
        row = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertIsInstance(row["blob"], str)
        self.assertLessEqual(len(row["blob"]), 200)

    def test_the_log_is_born_with_a_cap(self):
        """防腐 #4：新的 append-only 文件出生即带帽。"""
        self.path.write_text("x" * (automation.AUDIT_MAX_BYTES + 10) + "\n",
                             encoding="utf-8")
        automation.audit("purge_trash", "acted", path=self.path)
        self.assertLessEqual(self.path.stat().st_size, automation.AUDIT_MAX_BYTES)

    def test_recent_reads_newest_first_and_skips_bad_lines(self):
        automation.audit("purge_trash", "acted", path=self.path, n=1)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        automation.audit("archive_stale", "acted", path=self.path, n=2)
        rows = automation.recent(10, path=self.path)
        self.assertEqual([r["slug"] for r in rows], ["archive_stale", "purge_trash"])

    def test_recent_honours_the_limit_and_a_missing_file(self):
        for i in range(5):
            automation.audit("purge_trash", "acted", path=self.path, n=i)
        self.assertEqual(len(automation.recent(2, path=self.path)), 2)
        self.assertEqual(automation.recent(0, path=self.path), [])
        self.assertEqual(automation.recent(5, path=Path(self.dir) / "absent.jsonl"), [])


class ProjectionTestCase(unittest.TestCase):
    def test_inventory_is_json_serialisable_and_complete(self):
        wire = automation.inventory()
        self.assertEqual(len(wire["behaviours"]), len(automation.LEDGER))
        json.dumps(wire, ensure_ascii=False)       # 不抛 = 全是 JSON 原生类型
        first = wire["behaviours"][0]
        for key in ("slug", "runner", "effect", "switch", "verdict", "why",
                    "costly", "live", "unit"):
            self.assertIn(key, first)

    def test_by_slug_round_trips_and_fails_closed(self):
        for row in automation.LEDGER:
            self.assertIs(automation.by_slug(row.slug), row)
        self.assertIsNone(automation.by_slug("nope"))

    def test_kept_excludes_retired_and_merged(self):
        self.assertLessEqual(set(automation.kept()), set(automation.LEDGER))
        for row in automation.kept():
            self.assertEqual(row.verdict, automation.VERDICT_KEEP)


class CliTestCase(unittest.TestCase):
    """出口码纪律与 :mod:`act.lib.sources` 逐字同款：0 = on / 3 = off / 2 = 不认识。
    1 号刻意空着——那是 python 自己崩掉的码，shell 调用方靠它 fail-open。"""

    def test_enabled_exit_codes(self):
        self.assertEqual(automation.main(["--enabled", "no-such"]), 2)
        self.assertEqual(automation.main(["--enabled", "silent_merge"]), 0)
        self.assertEqual(automation.main(["--enabled", "mac_notify_relay"]), 3)

    def test_list_and_json_and_bare(self):
        self.assertEqual(automation.main(["--list"]), 0)
        self.assertEqual(automation.main(["--json"]), 0)
        self.assertEqual(automation.main([]), 2)


if __name__ == "__main__":
    unittest.main()
