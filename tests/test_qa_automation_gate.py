"""自动行为总账那道硬门的判例（CONTRACT §81 / §58.4；issue #451）。

每条规则各给一个「红」和一个「绿」的最小合成行（不扫真仓，`scan(rows=…)` 是
注入缝），外加一条**自维护钉**：真仓今天跑出来的违例必须全部在
`qa/automation_baseline.txt` 上——这条等价于门本体，但让
`python3 -m unittest` 也抓得到，不必等 CI（对比 `coverage_inventory.py --check`
那个「没人跑所以可以静静过期」的反面教材）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "qa"))
import automation_check  # noqa: E402
import qa_common  # noqa: E402

from act.lib import automation  # noqa: E402

REPO_ROOT = qa_common.REPO_ROOT


def _row(**kw):
    base = dict(slug="probe", zh="探针", en="probe", runner=automation.RUNNER_ACTD,
                cadence="每 pass", effect=[automation.EFFECT_DELETE],
                code="act/lib/automation.py:enabled", law=["§81"],
                verdict=automation.VERDICT_KEEP, why="判例用",
                switch=["trash_retention_days"], kind=automation.KIND_THRESHOLD,
                audit="actd.log")
    base.update(kw)
    return automation._b(**base)


def _keys(rows):
    return set(automation_check.scan(root=REPO_ROOT, rows=rows))


class SwitchRuleTestCase(unittest.TestCase):
    def test_a_costly_row_without_a_switch_is_red(self):
        self.assertIn("switch:probe", _keys([_row(switch=[], kind=automation.KIND_NONE)]))

    def test_a_costly_row_with_a_switch_is_green(self):
        self.assertNotIn("switch:probe", _keys([_row()]))

    def test_a_read_only_row_needs_no_switch(self):
        """只读 / 只投影的行不在 ask 4 的射程里——给健康扫描配一把开关是违宪
        （§0 第 3 条：健康报告不许可关）。"""
        rows = [_row(effect=[automation.EFFECT_STATE], switch=[],
                     kind=automation.KIND_NONE, audit=automation.AUDIT_NONE)]
        self.assertEqual(_keys(rows) & {"switch:probe", "audit:probe"}, set())


class HotSwitchRuleTestCase(unittest.TestCase):
    def test_an_actd_switch_outside_live_fields_is_red(self):
        rows = [_row(switch=["some_field_nobody_refreshes"], kind=automation.KIND_BOOL)]
        self.assertIn("cold-switch:probe:some_field_nobody_refreshes", _keys(rows))

    def test_a_non_actd_row_is_not_judged_hot(self):
        rows = [_row(runner=automation.RUNNER_CRON, switch=["some_field_nobody_refreshes"],
                     kind=automation.KIND_BOOL)]
        self.assertNotIn("cold-switch:probe:some_field_nobody_refreshes", _keys(rows))


class AuditRuleTestCase(unittest.TestCase):
    def test_a_costly_row_without_a_trace_is_red(self):
        self.assertIn("audit:probe", _keys([_row(audit=automation.AUDIT_NONE)]))

    def test_claiming_the_audit_log_without_a_callsite_is_red(self):
        self.assertIn("no-callsite:probe", _keys([_row(audit=automation.AUDIT_LOG)]))

    def test_a_real_row_that_does_call_audit_is_green(self):
        """`purge_trash` 在 act/actd.py 里真有 automation.audit(...) 的调用点。"""
        self.assertNotIn("no-callsite:purge_trash", _keys(automation.LEDGER))

    def test_an_enabled_call_is_not_accepted_as_proof_of_a_receipt(self):
        """写了闸门、忘了回执 —— 门必须照样红。

        第一版把 `enabled()` 与 `audit()` 的调用点记在同一个集合里，于是「这行说
        它往 automation.jsonl 留痕」只要有人 `enabled()` 过就算证明完毕。
        """
        row = _row(slug="probe", audit=automation.AUDIT_LOG)
        scores = {}
        automation_check._scan_callsites(
            (row,), {"probe": {"act/actd.py"}}, set(), scores)   # 只 enabled 过
        self.assertIn("no-callsite:probe", scores)
        scores = {}
        automation_check._scan_callsites(
            (row,), {"probe": {"act/actd.py"}}, {"probe"}, scores)   # 真 audit 过
        self.assertNotIn("no-callsite:probe", scores)


class DefaultRuleTestCase(unittest.TestCase):
    def test_a_costly_row_that_is_factory_on_is_red(self):
        rows = [_row(switch=["archive_after_days"], kind=automation.KIND_THRESHOLD)]
        self.assertIn("default-on:probe", _keys(rows))

    def test_a_costly_row_that_is_factory_off_is_green(self):
        """出厂 `trash.retention_days: 0` —— §81 / D83 真翻的那一把默认。"""
        self.assertNotIn("default-on:probe", _keys([_row()]))

    def test_the_verdict_does_not_depend_on_this_machine(self):
        """用 config.Config() 求值 = 纯出厂值，不读跑它那台机器的 config.yaml。"""
        self.assertEqual(_keys([_row()]), _keys([_row()]))


class PinnedRuleTestCase(unittest.TestCase):
    """ask 4 的第二半：模板钉死一把代价大的开关 = 每台新装机带着一个用户从没
    做过的显式选择（D57 原话：2026-09-02 到 09-14 之间装的机器就是这么带上
    `self_improve.enabled: true` 的）。"""

    def test_a_live_template_line_for_a_costly_switch_is_red(self):
        tpl = "autodispatch:\n  enabled: true\n  notify: true\n\nother: 1\n"
        self.assertTrue(automation_check._pinned_in_block(tpl, "autodispatch", "enabled"))

    def test_a_commented_out_template_line_is_green(self):
        tpl = "autodispatch:\n  # enabled: true\n  notify: true\n\nother: 1\n"
        self.assertFalse(automation_check._pinned_in_block(tpl, "autodispatch", "enabled"))

    def test_a_same_named_key_in_another_block_is_not_a_match(self):
        """`daily_loop.trash_retention_days` 不许被当成 `trash.retention_days`。"""
        tpl = "trash:\n  retention_days: 0\n\ndaily_loop:\n  trash_retention_days: 90\n"
        self.assertFalse(automation_check._pinned_in_block(tpl, "trash", "trash_retention_days"))
        self.assertTrue(automation_check._pinned_in_block(tpl, "trash", "retention_days"))

    def test_the_shipped_template_pins_nothing_costly(self):
        found = _keys(automation.LEDGER)
        self.assertEqual({k for k in found if k.startswith("pinned:")}, set())


class PointerRuleTestCase(unittest.TestCase):
    def test_a_code_pointer_at_a_missing_file_is_red(self):
        self.assertIn("code:probe", _keys([_row(code="act/lib/nope.py:whatever")]))

    def test_a_code_pointer_at_a_missing_symbol_is_red(self):
        self.assertIn("code:probe", _keys([_row(code="act/lib/automation.py:no_such_fn")]))

    def test_a_law_pointer_at_a_missing_section_is_red(self):
        self.assertIn("law:probe:§999", _keys([_row(law=["§999"])]))

    def test_every_real_row_resolves(self):
        found = _keys(automation.LEDGER)
        self.assertEqual({k for k in found if k.startswith(("code:", "law:"))}, set())


class OrphanCallTestCase(unittest.TestCase):
    def test_a_slug_asked_for_in_code_but_absent_from_the_ledger_is_red(self):
        found = _keys([_row(slug="only_in_the_ledger")])
        orphans = {k for k in found if k.startswith("orphan-call:")}
        self.assertTrue(orphans, "真仓里的 automation.enabled(...) 调用点应该被数到")
        self.assertTrue(all("purge_trash" in k or ":" in k for k in orphans))

    def test_the_real_ledger_has_no_orphans(self):
        found = _keys(automation.LEDGER)
        self.assertEqual({k for k in found if k.startswith("orphan-call:")}, set())


class CompletenessTestCase(unittest.TestCase):
    def test_every_scheduler_unit_is_claimed_by_a_row(self):
        found = _keys(automation.LEDGER)
        self.assertEqual({k for k in found if k.startswith("unlisted:")}, set())

    def test_an_empty_ledger_reports_every_scheduler_unit(self):
        found = _keys([])
        unlisted = {k for k in found if k.startswith("unlisted:")}
        self.assertTrue(unlisted)
        self.assertIn("unlisted:launchd:com.zelin.aiassistant.actd", unlisted)
        self.assertIn("unlisted:cron:install.sh:INGEST_CHAIN", unlisted)

    def test_the_three_enumerable_sources_are_non_trivial(self):
        """提取器退化成空表时，门会「全绿」——像 native-inventory 那条
        非平凡性判例一样，钉住每个源至少数到它该数到的量级。"""
        units = automation_check.scheduler_units(REPO_ROOT)
        self.assertGreaterEqual(len(units["launchd"]), 7)
        self.assertGreaterEqual(len(units["cron"]), 3)
        self.assertGreaterEqual(len(units["gha"]), 5)

    def test_contract_sections_are_parsed(self):
        sections = automation_check.contract_sections(REPO_ROOT)
        self.assertIn("81", sections)
        self.assertIn("48", sections)
        self.assertNotIn("999", sections)


class SelfMaintainingTestCase(unittest.TestCase):
    def test_todays_violations_are_all_on_the_baseline(self):
        """门本体的等价物。红了先看 `python3 scripts/qa/automation_check.py --list`。"""
        ledger = qa_common.load_ledger(automation_check.BASELINE)
        extra = sorted(set(automation_check.scan()) - set(ledger))
        self.assertEqual(extra, [], "账外违例（修代码，不要记账）：%s" % extra)

    def test_nothing_on_the_baseline_is_already_fixed(self):
        """shrink-only 的另一半：修好了就要划掉那一行，不许挂着。"""
        ledger = qa_common.load_ledger(automation_check.BASELINE)
        stale = sorted(set(ledger) - set(automation_check.scan()))
        self.assertEqual(stale, [], "已修好仍挂账（划掉这几行）：%s" % stale)

    def test_the_baseline_rows_carry_a_reason(self):
        """账本的每一行都要说明「为什么还欠着」——裸备注会崩解析器，所以
        理由必须写在 ` #` 之后。"""
        with open(automation_check.BASELINE, "r", encoding="utf-8") as fh:
            rows = [ln for ln in fh.read().splitlines()
                    if ln.strip() and not ln.lstrip().startswith("#")]
        self.assertTrue(rows)
        for line in rows:
            self.assertIn(" #", line, line)


if __name__ == "__main__":
    unittest.main()
