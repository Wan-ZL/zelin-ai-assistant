"""test-ui skill · CATALOG ⟷ 文档同源判例（防腐 #5 文档指针纪律 + 变异靶）：references/tiers.md 每档表格里的 id 与
checks_ui.CATALOG 的 tier 逐条相等（tier 数字改一位 = 判例红，不再只查「在 1–5 之间」）；references/triggers.md 的加挂层列表与
TRIGGER_CHECKS 逐条相等；SKILL.md 的档表列出的核心 id 全在 CATALOG 且 tier 一致；phase 纪律：读 runtime bundle 的层全在 phase 3，
起 app 的 structure_runtime 是唯一的 phase 2 核心层；每个 tier 的默认勾选集合逐字钉死。零子进程。

法典：CLAUDE.md 防腐 #5；设计 vnext2-plan R2.8。
"""
import inspect
import os
import re
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import sensors  # noqa: E402

REFS = os.path.join(os.path.dirname(kit.SKILL_SCRIPTS), "references")
SKILL_MD = os.path.join(os.path.dirname(kit.SKILL_SCRIPTS), "SKILL.md")
_ROW_ID = re.compile(r"^\| `([a-z_0-9]+)` \|")
_TIER_HEAD = re.compile(r"^## 档 (\d)")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _tiers_from_docs():
    """tiers.md → {id: tier} from the per-tier tables (档 4 / 5 list their ids in prose backticks after the heading)."""
    out, tier = {}, None
    for line in _read(os.path.join(REFS, "tiers.md")).splitlines():
        head = _TIER_HEAD.match(line)
        if head:
            tier = int(head.group(1))
            continue
        row = _ROW_ID.match(line)
        if row and tier:
            out[row.group(1)] = tier
        elif tier in (4, 5) and line.startswith("`"):
            for cid in re.findall(r"`([a-z]+_[a-z_0-9]+)`", line.split(".")[0]):  # check ids carry an underscore; `flaky` is prose
                out.setdefault(cid, tier)
    return out


class TierTableTestCase(unittest.TestCase):
    def test_every_documented_core_check_has_the_documented_tier(self):
        documented = _tiers_from_docs()
        core = {e["id"]: e["tier"] for e in checks_ui.CATALOG if e["circle"] == "core"}
        self.assertEqual(sorted(documented), sorted(core), "tiers.md rows and CATALOG core ids must be the same set")
        for cid, tier in sorted(documented.items()):
            self.assertEqual(checks_ui.BY_ID[cid]["tier"], tier, cid)

    def test_skill_md_tier_table_matches_catalog(self):
        rows = [ln for ln in _read(SKILL_MD).splitlines() if ln.startswith("| **") and "档" not in ln]
        self.assertEqual(len(rows), 5)
        for line in rows:
            tier = int(line.split("**")[1].split()[0])
            for cid in re.findall(r"`([a-z_0-9]+)`", line.split("|")[3]):
                self.assertIn(cid, checks_ui.BY_ID, cid)
                self.assertEqual(checks_ui.BY_ID[cid]["tier"], tier, cid)

    def test_default_checks_per_tier_are_pinned(self):
        det = {"triggers": []}
        expected = {
            1: ["surface_detect", "seed_probe", "structure_source", "tokens_source", "ledger_lint", "golden_manifest", "thresholds_unmoved",
                "pair_structure", "pair_tokens", "theme_default_declared", "off_token_literals", "contrast_pairs", "a11y_static", "seed_guard"],
            2: ["structure_runtime", "app_launch", "pair_runtime", "topology_runtime", "tokens_runtime", "geometry_runtime",
                "theme_default_observed", "a11y_rules", "screens_capture"],
            3: ["visual_diff", "matrix_themes_viewports", "keyboard_reach", "focus_order", "reflow", "i18n_parity"],
            4: ["inventory_stability", "visual_stability", "states_matrix", "cross_engine", "reference_runtime"],
            5: ["matrix_all_routes", "all_references", "clean_machine_ui", "golden_review_sheet"],
        }
        previous = []
        for tier in range(1, 6):
            chosen = checks_ui.default_checks(det, tier)
            added = [c for c in chosen if c not in previous]
            self.assertEqual(sorted(added), sorted(expected[tier]), "tier %d" % tier)
            previous = chosen
        self.assertEqual(len(previous), sum(len(v) for v in expected.values()))


class TriggerTableTestCase(unittest.TestCase):
    def test_triggers_md_add_ons_equal_trigger_checks(self):
        documented = {}
        for line in _read(os.path.join(REFS, "triggers.md")).splitlines():
            row = _ROW_ID.match(line)
            if row:
                documented[row.group(1)] = re.findall(r"`([a-z_0-9]+)`", line.split(" | ")[2])  # `width|height` sits inside a cell
        expected = {k: v for k, v in checks_ui.TRIGGER_CHECKS.items() if k != "always"}
        self.assertEqual(documented, expected)
        self.assertEqual(checks_ui.TRIGGER_CHECKS["always"], ["seed_guard", "ledger_lint"])


class PhaseDisciplineTestCase(unittest.TestCase):
    def test_bundle_readers_run_in_phase_3_after_the_single_phase_2_capture(self):
        readers = []
        for entry in checks_ui.CATALOG:
            fn = getattr(sensors, "check_" + entry["id"], None)
            if fn is None or fn.__name__.startswith("check_") and fn.__module__ != sensors.__name__:
                continue
            try:
                source = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            body = source.split("\n", 1)[1]  # the def line of check_structure_runtime itself is not a read
            if "_need_runtime(ctx)" in body or "_runtime(ctx)" in body:
                readers.append(entry["id"])
        self.assertIn("pair_runtime", readers)
        for cid in readers:
            self.assertEqual(checks_ui.BY_ID[cid]["phase"], 3, cid)
        phase2_core = [e["id"] for e in checks_ui.CATALOG if e["phase"] == 2 and e["circle"] == "core"]
        self.assertEqual(phase2_core, ["structure_runtime"])
        self.assertEqual([e["phase"] for e in checks_ui.CATALOG if e["tier"] == 1 and e["circle"] == "core"],
                         [1, 1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 3, 3])  # extraction / ledgers parallel, pairing after


if __name__ == "__main__":
    unittest.main()
