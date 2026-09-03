"""§62.2 UI 对齐门（scripts/ui/parity_check.py）的判例。

迷你仓库根（web/src 源、index.html、server/lanes.py、server/settings.py）+ 迷你清单 +
注入的假 vitest runner（永不真起 node）钉住：每类静态探针的在/不在、control 走 vitest
报告（普通 it 与 `[pending]` it 的反向语义）、三态判决（NEW / STALE / PENDING / WAIVED）、
报告内容、--write-pending、vitest 报告缺席 = FAIL 不软化。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import parity_check as pc  # noqa: E402
import ui_common as uc  # noqa: E402

INVENTORY = {
    "source": {"dir": "mac/Sources", "files": 1, "sha256": "abc123def456"},
    "controls": [
        {"id": "control:board:button:approve", "zh": "批准", "en": "Approve", "role": "button",
         "screen": "board", "owner": "web", "gated": True},
        {"id": "control:board:button:later", "zh": "暂缓", "en": "Later", "role": "button",
         "screen": "board", "owner": "web", "gated": True},
        {"id": "control:board:label:gone", "zh": "没了", "en": "Gone", "role": "label",
         "screen": "board", "owner": "web", "gated": True},
        {"id": "control:board:copy:long", "zh": "长句", "en": "Long sentence.", "role": "copy",
         "screen": "board", "owner": "web", "gated": False},
        {"id": "control:menu.main:menu-item:quit", "zh": "退出", "en": "Quit", "role": "menu-item",
         "screen": "menu.main", "owner": "shell", "gated": False},
    ],
    "rail": {"side": "left", "items": [
        {"id": "rail:dashboard", "slug": "dashboard", "zh": "任务台", "en": "Workbench", "gated": True, "owner": "web"},
        {"id": "rail:trash", "slug": "trash", "zh": "回收站", "en": "Trash", "gated": True, "owner": "web"},
    ]},
    "lanes": {"order": ["debt", "needs_approval", "archived"], "items": [
        {"id": "lane:debt", "slug": "debt", "zh": "潜在任务", "en": "Backlog", "gated": True, "owner": "web"},
        {"id": "lane:needs_approval", "slug": "needs_approval", "zh": "提案", "en": "Proposals", "gated": True, "owner": "web"},
        {"id": "lane:archived", "slug": "archived", "zh": "永久性完成", "en": "Done for good", "gated": True, "owner": "web"},
    ], "card_affordances": {}},
    "screens": [
        {"id": "screen:trash", "zh": "回收站", "en": "Trash", "gated": True, "owner": "web"},
        {"id": "screen:about", "zh": "关于", "en": "About", "gated": True, "owner": "web"},
    ],
    "settings_keys": [
        {"id": "setting:overrides:language", "key": "language", "store": "overrides", "gated": True, "owner": "web"},
        {"id": "setting:overrides:voice_enabled", "key": "voice_enabled", "store": "overrides", "gated": True, "owner": "web"},
        {"id": "setting:prefs:cardSortOrder", "key": "cardSortOrder", "store": "prefs", "gated": True, "owner": "web"},
        {"id": "setting:prefs:captionsEngine", "key": "captionsEngine", "store": "prefs", "gated": False, "owner": "shell"},
    ],
    "shortcuts": [
        {"id": "shortcut:board:cmd-f", "key": "⌘F", "gated": True, "owner": "web"},
        {"id": "shortcut:board:cmd-l", "key": "⌘L", "gated": True, "owner": "web"},
    ],
    "notifications": [{"id": "notification:general", "gated": False, "owner": "shell"}],
    "theme_layout": [
        {"id": "theme:default", "value": "light", "gated": True, "owner": "web"},
        {"id": "layout:lane-width", "token": "layout.lane.width", "gated": True, "owner": "web"},
        {"id": "layout:strip-width", "token": "layout.strip.width", "gated": True, "owner": "web"},
    ],
}

WEB_TSX = '''
// comment mentioning 关于 About should not count
export function Rail() {
  return <nav data-rail="left">
    <a data-rail-item="dashboard">{text("任务台", "Workbench")}</a>
    <a data-rail-item="trash">{text("回收站", "Trash")}</a>
  </nav>;
}
export const SORT_KEY = "cardSortOrder";
export const HINT = "搜索（⌘F）";
'''

BOARD_LANES_TSX = '''
export function BoardLanes() {
  return <div>
    <BacklogStrip />
    <Lane title={text("提案", "Proposals")} />
    <ArchiveStrip />
  </div>;
}
'''

BOARD_CSS = ".board-column { width: var(--native-layout-lane-width); }\n"
INDEX_HTML = '<script>if (!theme) document.documentElement.dataset.theme = "light";</script>'
LANES_PY = 'LANES = ({"slug": "debt", "help": {"zh": "潜在任务", "en": "Backlog"}},\n' \
           ' {"slug": "needs_approval", "help": {}}, {"slug": "archived", "help": {"zh": "永久性完成", "en": "Done for good"}})\n'
SETTINGS_PY = 'OVERRIDE_KEYS = ("language",)\n'


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_repo(root):
    _write(root, "web/src/components/shell/Rail.tsx", WEB_TSX)
    _write(root, "web/src/components/board/BoardLanes.tsx", BOARD_LANES_TSX)
    _write(root, "web/src/components/board/BoardLanes.test.tsx", 'text("关于", "About") // tests never count')
    _write(root, "web/src/styles/board.css", BOARD_CSS)
    _write(root, "web/src/styles/tokens.css", "--native-layout-strip-width: 44px;\n")
    _write(root, "web/index.html", INDEX_HTML)
    _write(root, "server/lanes.py", LANES_PY)
    _write(root, "server/settings.py", SETTINGS_PY)
    _write(root, "ui/parity/native-inventory.json", uc.dump_json(INVENTORY))


def _vitest_report(results):
    return json.dumps({"testResults": [{"assertionResults": [
        {"title": title, "status": status} for title, status in results.items()]}]})


def _fake_runner(results):
    def runner(web_dir, out_path):
        uc.write_text(out_path, _vitest_report(results))
        return 0
    return runner


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _make_repo(self.root)
        self.snap = pc.WebSnapshot(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _args(self, *extra):
        base = ["--check", "--root", self.root, "--web-dir", os.path.join(self.root, "web"),
                "--inventory", os.path.join(self.root, "ui/parity/native-inventory.json"),
                "--waivers", os.path.join(self.root, "ui/parity/waivers.txt"),
                "--pending", os.path.join(self.root, "ui/parity/pending.txt"),
                "--report-json", os.path.join(self.root, "ui/parity/report.json"),
                "--report-md", os.path.join(self.root, "ui/parity/report.md")]
        return base + list(extra)


class StaticProbesTestCase(_RepoCase):
    def test_static_presence_per_probe(self):
        presence = pc.static_presence(self.snap, INVENTORY)
        self.assertEqual(presence, {
            "rail:dashboard": True, "rail:trash": True,
            "lane:debt": True, "lane:needs_approval": True, "lane:archived": True,
            "screen:trash": True, "screen:about": False,          # 只在注释/测试里出现 ≠ 在
            "setting:overrides:language": True, "setting:overrides:voice_enabled": False,
            "setting:prefs:cardSortOrder": True,
            "shortcut:board:cmd-f": True, "shortcut:board:cmd-l": False,
            "theme:default": True, "layout:lane-width": True, "layout:strip-width": False,  # tokens.css 自身不算消费
            "rail:order": True, "lanes:order": True, "lanes:rail-left": True, "lanes:rail-right": True,
        })

    def test_rail_order_and_strips_detect_misplacement(self):
        _write(self.root, "web/src/components/shell/Rail.tsx", WEB_TSX.replace('data-rail="left"', 'data-rail="top"'))
        _write(self.root, "web/src/components/board/BoardLanes.tsx",
               BOARD_LANES_TSX.replace("<BacklogStrip />\n    <Lane", "<Lane").replace("<ArchiveStrip />", "<ArchiveStrip /><Lane x />"))
        snap = pc.WebSnapshot(self.root)
        structural = pc.structural_items(snap, INVENTORY)
        self.assertEqual(structural, {"rail:order": False, "lanes:order": True,
                                      "lanes:rail-left": False, "lanes:rail-right": False})
        bad_lanes = dict(INVENTORY, lanes=dict(INVENTORY["lanes"], order=["needs_approval", "debt", "archived"]))
        self.assertFalse(pc.structural_items(snap, bad_lanes)["lanes:order"])


class VitestProbeTestCase(_RepoCase):
    def test_control_presence_reads_normal_and_pending_titles(self):
        results = {"control:board:button:approve": "passed", "control:board:button:later [pending]": "passed",
                   "control:board:label:gone [pending]": "failed", "清单与账本都读到了": "passed"}
        self.assertEqual(pc.control_presence(results, {}), {
            "control:board:button:approve": True, "control:board:button:later": False,
            "control:board:label:gone": True})

    def test_missing_report_is_an_error_not_a_pass(self):
        def runner(web_dir, out_path):
            return 127
        presence, error = pc.vitest_presence(os.path.join(self.root, "web"), {}, runner=runner)
        self.assertEqual(presence, {})
        self.assertIn("no report", error)
        self.assertIn("127", error)

    def test_unreadable_report_is_an_error(self):
        bad = os.path.join(self.root, "bad.json")
        uc.write_text(bad, "{not json")
        presence, error = pc.vitest_presence(os.path.join(self.root, "web"), {}, vitest_json=bad)
        self.assertEqual(presence, {})
        self.assertIn("unreadable", error)

    def test_default_runner_returns_127_when_npx_is_absent(self):
        old = os.environ.get("PATH")
        os.environ["PATH"] = self.root   # 空目录：找不到 npx
        try:
            self.assertEqual(pc.default_vitest_runner(self.root, os.path.join(self.root, "x.json")), 127)
        finally:
            os.environ["PATH"] = old


class JudgementTestCase(_RepoCase):
    RESULTS = {"control:board:button:approve": "passed", "control:board:button:later": "failed",
               "control:board:label:gone": "failed"}

    def test_new_missing_fails_and_write_pending_enrolls_them(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pc.main(self._args(), runner=_fake_runner(self.RESULTS))
        self.assertEqual(rc, 1)
        self.assertIn("NEW: control:board:button:later", out.getvalue())
        self.assertIn("NEW: screen:about", out.getvalue())
        with redirect_stdout(io.StringIO()):
            rc = pc.main(self._args("--write-pending"), runner=_fake_runner(
                {"control:board:button:approve": "passed", "control:board:button:later [pending]": "passed",
                 "control:board:label:gone [pending]": "passed"}))
        self.assertEqual(rc, 0)
        pending = uc.load_ledger(os.path.join(self.root, "ui/parity/pending.txt"))
        self.assertEqual(sorted(pending), ["control:board:button:later", "control:board:label:gone",
                                           "layout:strip-width", "screen:about",
                                           "setting:overrides:voice_enabled", "shortcut:board:cmd-l"])

    def test_stale_pending_fails_and_waivers_are_excluded(self):
        _write(self.root, "ui/parity/pending.txt", "screen:trash\nscreen:about\n")
        _write(self.root, "ui/parity/waivers.txt",
               "control:board:label:gone  retired with #119  #119\nlayout:strip-width  x  D3\n"
               "setting:overrides:voice_enabled x\nshortcut:board:cmd-l x\ncontrol:board:button:later x\n")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pc.main(self._args(), runner=_fake_runner({"control:board:button:approve": "passed"}))
        self.assertEqual(rc, 1)
        self.assertIn("STALE: screen:trash", out.getvalue())
        self.assertNotIn("NEW", out.getvalue())
        report = uc.load_json(os.path.join(self.root, "ui/parity/report.json"))
        self.assertEqual(report["items"]["screen:trash"], "STALE")
        self.assertEqual(report["items"]["screen:about"], "PENDING")
        self.assertEqual(report["items"]["control:board:label:gone"], "WAIVED")
        self.assertEqual(report["counts"]["WAIVED"], 5)
        self.assertEqual(report["not_gated"], {"informational": 1, "shell": 3})
        md = uc.read_text(os.path.join(self.root, "ui/parity/report.md"))
        self.assertIn("| STALE | 1 |", md)
        self.assertIn("- `screen:trash`", md)
        self.assertIn("Verdict: **FAIL**", md)

    def test_all_green_when_ledgers_match_reality_and_report_dir_gets_a_copy(self):
        _write(self.root, "ui/parity/pending.txt",
               "control:board:button:later\ncontrol:board:label:gone\nscreen:about\n"
               "setting:overrides:voice_enabled\nshortcut:board:cmd-l\nlayout:strip-width\n")
        report_dir = os.path.join(self.root, "qa-report")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pc.main(self._args("--report", report_dir), runner=_fake_runner(
                {"control:board:button:approve": "passed", "control:board:button:later [pending]": "passed",
                 "control:board:label:gone [pending]": "passed"}))
        self.assertEqual(rc, 0)
        self.assertIn("[ui-parity] OK", out.getvalue())
        self.assertTrue(os.path.exists(os.path.join(report_dir, "ui_parity_verdict.txt")))
        report = uc.load_json(os.path.join(self.root, "ui/parity/report.json"))
        self.assertEqual(report["counts"], {"PRESENT": 16, "PENDING": 6})
        self.assertTrue(report["ok"])

    def test_vitest_failure_to_run_makes_the_gate_red_even_with_clean_ledgers(self):
        _write(self.root, "ui/parity/pending.txt",
               "control:board:button:approve\ncontrol:board:button:later\ncontrol:board:label:gone\nscreen:about\n"
               "setting:overrides:voice_enabled\nshortcut:board:cmd-l\nlayout:strip-width\n")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pc.main(self._args(), runner=lambda web_dir, out_path: 1)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR:", out.getvalue())

    def test_render_markdown_lists_kind_table(self):
        report = {"inventory_sha256": "abcdef123456789", "counts": {"PRESENT": 1, "MISSING": 1},
                  "not_gated": {"shell": 2}, "new": ["lane:x"], "stale": [],
                  "items": {"lane:x": "MISSING", "rail:y": "PRESENT"}, "ok": False}
        md = pc.render_markdown(report)
        self.assertIn("| lane | 0 | 0 | 1 | 0 | 0 |", md)
        self.assertIn("| rail | 1 | 0 | 0 | 0 | 0 |", md)
        self.assertIn("## NEW", md)
        self.assertNotIn("## STALE", md)


if __name__ == "__main__":
    unittest.main()
