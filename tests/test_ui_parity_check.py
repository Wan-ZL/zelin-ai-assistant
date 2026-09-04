"""§66.2 UI 对齐门（scripts/ui/parity_check.py）的判例。

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
        # 壳直发的系统通知句（§66.2 追记）：目录 + 壳源码都有 → 在；只有目录没有壳句 → 不在；都没有 → 不在
        {"id": "control:notifications:label:recording-is-live", "zh": "录制已就绪", "en": "Recording is live",
         "role": "label", "screen": "notifications", "owner": "shell", "gated": True, "probe": "notify_catalog"},
        {"id": "control:notifications:label:overflow-count-more-notifications", "zh": "还有 {overflow.count} 条通知",
         "en": "+{overflow.count} more notifications", "role": "label", "screen": "notifications", "owner": "shell",
         "gated": True, "probe": "notify_catalog"},
        {"id": "control:notifications:label:only-in-catalog", "zh": "只在目录", "en": "Catalog only",
         "role": "label", "screen": "notifications", "owner": "shell", "gated": True, "probe": "notify_catalog"},
        {"id": "control:notifications:label:nowhere", "zh": "哪都没有", "en": "Nowhere",
         "role": "label", "screen": "notifications", "owner": "shell", "gated": True, "probe": "notify_catalog"},
    ],
    "rail": {"side": "left", "items": [
        {"id": "rail:dashboard", "slug": "dashboard", "zh": "任务台", "en": "Workbench", "gated": True, "owner": "web"},
        # 归属表 RAIL_OWNER 标 retired 的侧栏项（D29 / D30）：只列不判，rail:order 的期望顺序里没有它
        {"id": "rail:ask", "slug": "ask", "zh": "问问助手", "en": "Ask", "gated": False, "owner": "retired", "reason": "D29"},
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
        {"id": "setting:prefs:captionsEngine", "key": "captionsEngine", "store": "prefs", "gated": True, "owner": "shell",
         "probe": "shell_source"},
        {"id": "setting:prefs:captionsGhost", "key": "captionsGhost", "store": "prefs", "gated": True, "owner": "shell",
         "probe": "shell_source"},
        {"id": "setting:prefs:terminalApp", "key": "terminalApp", "store": "prefs", "gated": True, "owner": "server",
         "probe": "server_source", "landing": '"terminal_app"'},
        {"id": "setting:prefs:hasCompletedFirstRun", "key": "hasCompletedFirstRun", "store": "prefs", "gated": True,
         "owner": "server", "probe": "server_source", "landing": "setup_done.json"},
        {"id": "setting:prefs:showMenuBarIcon", "key": "showMenuBarIcon", "store": "prefs", "gated": False,
         "owner": "retired", "reason": "D3"},
    ],
    "shortcuts": [
        {"id": "shortcut:board:cmd-f", "key": "⌘F", "gated": True, "owner": "web"},
        {"id": "shortcut:board:cmd-l", "key": "⌘L", "gated": True, "owner": "web"},
    ],
    "notifications": [
        {"id": "notification:review_ready", "kind": "review_ready", "gated": True, "owner": "shell", "probe": "notify_catalog"},
        {"id": "notification:general", "kind": None, "gated": True, "owner": "shell", "probe": "notify_catalog"},
        {"id": "notification:unlisted", "kind": "unlisted", "gated": True, "owner": "shell", "probe": "notify_catalog"},
    ],
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
SETTINGS_PY = 'OVERRIDE_KEYS = ("language", "terminal_app")\n'
SHELL_SWIFT = '''
enum PermissionsProbe {
    static let key = "captionsEngine"
    static func post() {
        Self.postSystemNotice(title: L("录制已就绪", "Recording is live"), body: note)
        content.title = L("还有 \\(overflow.count) 条通知", "+\\(overflow.count) more notifications")
    }
}
'''
# 迷你 server/notify_catalog.py：与真目录同一接口（kind_names / has_sentence / same_template），不 import act
NOTIFY_CATALOG_PY = '''
import re
_PH = re.compile(r"\\{[^{}]*\\}")
SENTENCES = [("录制已就绪", "Recording is live"), ("还有 {n} 条通知", "+{n} more notifications"), ("只在目录", "Catalog only")]
def kind_names():
    return ["review_ready", "general"]
def fragments(t):
    return [p for p in _PH.split(t) if p]
def same_template(a, b):
    return fragments(a) == fragments(b)
def has_sentence(zh, en):
    return any(same_template(zh, a) and same_template(en, b) for a, b in SENTENCES)
'''


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
    _write(root, "server/notify_catalog.py", NOTIFY_CATALOG_PY)
    _write(root, "shell/Sources/ShellSystem.swift", SHELL_SWIFT)
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
            # §66.2 追记：壳持有的键探 shell/Sources；搬到 server 的键探 landing 字面量（setup_done.json 不在迷你 server 里）
            "setting:prefs:captionsEngine": True, "setting:prefs:captionsGhost": False,
            "setting:prefs:terminalApp": True, "setting:prefs:hasCompletedFirstRun": False,
            "notification:review_ready": True, "notification:general": True, "notification:unlisted": False,
            "control:notifications:label:recording-is-live": True,
            "control:notifications:label:overflow-count-more-notifications": True,   # 占位名不同也算同一句
            "control:notifications:label:only-in-catalog": False,                    # 壳没在发
            "control:notifications:label:nowhere": False,
            "shortcut:board:cmd-f": True, "shortcut:board:cmd-l": False,
            "theme:default": True, "layout:lane-width": True, "layout:strip-width": False,  # tokens.css 自身不算消费
            "rail:order": True, "lanes:order": True, "lanes:rail-left": True, "lanes:rail-right": True,
        })

    def test_notify_probes_are_absent_without_the_server_catalog(self):
        os.remove(os.path.join(self.root, "server", "notify_catalog.py"))
        snap = pc.WebSnapshot(self.root)
        self.assertIsNone(snap.notify)
        items = {i["id"]: i for i in INVENTORY["controls"] + INVENTORY["notifications"]}
        self.assertFalse(pc.probe_notification(snap, items["notification:review_ready"]))
        self.assertFalse(pc.probe_notice_control(snap, items["control:notifications:label:recording-is-live"]))
        # 壳源码本身照常读到（没有目录时退到逐字相等）
        self.assertTrue(snap.shell_posts("录制已就绪", "Recording is live"))
        self.assertFalse(snap.shell_posts("还有 {n} 条通知", "+{n} more notifications"))

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

    def test_rail_order_skips_retired_rail_items_but_flags_them_when_rendered(self):
        # 清单里 ask 在 dashboard 与 trash 之间、owner=retired（D29）：web 栏上没有它 = 顺序正确
        self.assertTrue(pc.structural_items(self.snap, INVENTORY)["rail:order"])
        # 要是 web 把退役项又画回栏上，顺序探针照样红——退役不是「可选」
        _write(self.root, "web/src/components/shell/Rail.tsx",
               WEB_TSX.replace('<a data-rail-item="trash">', '<a data-rail-item="ask">Ask</a>\n    <a data-rail-item="trash">'))
        self.assertFalse(pc.structural_items(pc.WebSnapshot(self.root), INVENTORY)["rail:order"])
        # 退役项不进 gated 清单，报告里不计
        self.assertNotIn("rail:ask", pc.gated_ids(INVENTORY))


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


# §66.2 追记的 shell / server 探针在迷你仓库里判「不在」的五条（账本测试把它们当存量挂账）
EXTRA_MISSING = ["control:notifications:label:nowhere", "control:notifications:label:only-in-catalog",
                 "notification:unlisted", "setting:prefs:captionsGhost", "setting:prefs:hasCompletedFirstRun"]
EXTRA_PENDING_TEXT = "".join(i + "\n" for i in EXTRA_MISSING)


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
        self.assertEqual(sorted(pending), sorted(["control:board:button:later", "control:board:label:gone",
                                                  "layout:strip-width", "screen:about",
                                                  "setting:overrides:voice_enabled", "shortcut:board:cmd-l"]
                                                 + EXTRA_MISSING))

    def test_stale_pending_fails_and_waivers_are_excluded(self):
        _write(self.root, "ui/parity/pending.txt", "screen:trash\nscreen:about\n" + EXTRA_PENDING_TEXT)
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
        # 只列不判的：copy 文案（web）、菜单项（shell）、退役的偏好键 + 退役的侧栏项 ask（retired）；通知 / 壳偏好键现在都判
        self.assertEqual(report["not_gated"], {"informational": 1, "shell": 1, "retired": 2})
        self.assertEqual(report["items"]["notification:review_ready"], "PRESENT")
        self.assertEqual(report["items"]["setting:prefs:terminalApp"], "PRESENT")
        self.assertEqual(report["items"]["control:notifications:label:only-in-catalog"], "PENDING")
        md = uc.read_text(os.path.join(self.root, "ui/parity/report.md"))
        self.assertIn("| STALE | 1 |", md)
        self.assertIn("- `screen:trash`", md)
        self.assertIn("Verdict: **FAIL**", md)

    def test_all_green_when_ledgers_match_reality_and_report_dir_gets_a_copy(self):
        _write(self.root, "ui/parity/pending.txt",
               "control:board:button:later\ncontrol:board:label:gone\nscreen:about\n"
               "setting:overrides:voice_enabled\nshortcut:board:cmd-l\nlayout:strip-width\n" + EXTRA_PENDING_TEXT)
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
        self.assertEqual(report["counts"], {"PRESENT": 22, "PENDING": 11})
        self.assertTrue(report["ok"])

    def test_vitest_failure_to_run_makes_the_gate_red_even_with_clean_ledgers(self):
        _write(self.root, "ui/parity/pending.txt",
               "control:board:button:approve\ncontrol:board:button:later\ncontrol:board:label:gone\nscreen:about\n"
               "setting:overrides:voice_enabled\nshortcut:board:cmd-l\nlayout:strip-width\n" + EXTRA_PENDING_TEXT)
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
