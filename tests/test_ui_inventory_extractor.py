"""§66.1 原生 UI 清单提取器（scripts/ui/extract_native_inventory.py）的判例。

用一座迷你 mac/Sources（覆盖 MainSection 侧栏、Settings 注册表与 group、Slack 子
section、Kanban 列、卡片按钮 / 对话框、AppDelegate 菜单与 ⌘n、NotifyRelay kind）钉住：
归类 role、screen 归属、id 铸造与 #n 去重、settings 键、列序与书立条、快捷键、
owner（web / shell / os / retired）、确定性（重跑同 JSON）与 CLI 三态。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import extract_native_inventory as inv  # noqa: E402

MAIN_WINDOW = '''
enum MainSection: String, CaseIterable, Identifiable {
    case dashboard, trash, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .dashboard: return L("任务台", "Workbench")
        case .trash: return L("回收站", "Trash")
        case .settings: return L("设置", "Settings")
        }
    }
    var icon: String {
        switch self {
        case .dashboard: return "tray.full"
        case .trash: return "trash"
        case .settings: return "gearshape"
        }
    }
}

struct MainWindowView: View {
    private let collapsedWidth: Double = 48
    var body: some View {
        Button { } label: { Image(systemName: "sidebar.leading") }
            .help(L("折叠/展开侧栏", "Collapse/expand sidebar"))
    }
}
'''

APP_DELEGATE = '''
final class AppDelegate: NSObject {
    func installMainMenu() {
        let settings = NSMenuItem(title: L("设置…", "Settings…"),
                                  action: #selector(openSettingsPage(_:)),
                                  keyEquivalent: ",")
        appMenu.addItem(withTitle: L("退出", "Quit"),
                        action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        edit.addItem(withTitle: L("重做", "Redo"),
                     action: Selector(("redo:")), keyEquivalent: "Z")
        for (i, s) in MainSection.allCases.enumerated() {
            let mi = NSMenuItem(title: s.title,
                                action: #selector(showMainSection(_:)),
                                keyEquivalent: "\\(i + 1)")
        }
        let sidebar = NSMenuItem(title: L("折叠/展开侧栏", "Collapse/Expand Sidebar"),
                                 action: #selector(toggleSidebar(_:)),
                                 keyEquivalent: "s")
        sidebar.keyEquivalentModifierMask = [.command, .option]
        let plain = NSMenuItem(title: L("关于", "About"),
                               action: #selector(openAboutPage(_:)), keyEquivalent: "")
    }

    private func showStatusMenu() {
        menu.addItem(NSMenuItem(title: L("打开主窗口", "Open Main Window"),
                                action: #selector(openMainWindow(_:)), keyEquivalent: ""))
    }

    func confirmT2(id: String, summary: String) -> Bool {
        let alert = NSAlert()
        alert.messageText = L("确认", "Confirm")
        alert.addButton(withTitle: L("批准", "Approve"))
        alert.addButton(withTitle: L("取消", "Cancel"))
        return true
    }
}
'''

SETTINGS = '''
struct SettingsFormView: View {
    @State private var on = true
    var body: some View {
        VStack {
            Text(L("设置", "Settings"))
            TextField(L("搜索设置（⌘F）", "Search settings (⌘F)"), text: $q)
            Button("") { focus = true }
                .keyboardShortcut("f", modifiers: .command)
            Button(L("清除", "Clear")) { q = "" }
        }
    }

    private var sections: [SettingsSectionDescriptor] {
        [
            SettingsSectionDescriptor(
                id: "general", titleZh: "通用", titleEn: "General",
                keywords: "通用 general",
                anchor: nil, content: AnyView(generalGroup)),
            SettingsSectionDescriptor(
                id: "menuBar", titleZh: "菜单栏", titleEn: "Menu Bar",
                keywords: "菜单栏",
                anchor: nil, content: AnyView(menuBarGroup)),
            SettingsSectionDescriptor(
                id: "slack", titleZh: "Slack 接入", titleEn: "Slack",
                keywords: "slack",
                anchor: "slack", content: AnyView(SlackSettingsSection())),
        ]
    }

    private var generalGroup: some View {
        group {
            Toggle(L("登录时启动", "Launch at login"), isOn: $on)
            Text(L("走 macOS 登录项，系统设置里可见可改，这是一句很长的说明文字，超过标签长度上限。",
                   "Uses macOS login items; a long explanatory sentence that exceeds the label limit."))
            HStack {
                Text(L("界面语言", "Interface language"))
                Picker("", selection: $language) {
                    Text("中文 (zh)").tag("zh")
                    Text(L("英文", "English")).tag("en")
                }
            }
            Toggle(L("自动检查新版本", "Check for updates"), isOn: Binding(
                get: { updateCheckEnabled },
                set: { v in
                    persistOverride("updates_check_enabled", v, dropWhen: true)
                }))
            Button(L("打开", "Open")) { }
            Button(L("打开", "Open")) { }
        }
    }

    private var menuBarGroup: some View {
        Toggle(L("显示菜单栏图标", "Show menu-bar icon"), isOn: Binding(
            get: { showMenuBarIcon },
            set: { v in UserDefaults.standard.set(v, forKey: "showMenuBarIcon") }))
    }

    private func load() {
        let ov = SettingsIO.readOverrides()
        let feats = ov["features"] as? [String: Any] ?? [:]
        func flag(_ key: String) -> Bool { (feats[key] as? Bool) ?? true }
        featSlackRadar = flag("slack_radar")
        let tele = ov["telemetry"] as? [String: Any] ?? [:]
        if let v = tele["enabled"] as? Bool { telemetryEnabled = v }
        cardSortOrder = Prefs.string("cardSortOrder", default: "newest")
        let w = d.double(forKey: "sidebarWidth")
    }

    private func save() {
        var merged = SettingsIO.readOverrides()
        merged["language"] = "en"
        merged.removeValue(forKey: "review_notify")
    }
}
'''

SETTINGS_SLACK = '''
struct SlackSettingsSection: View {
    var body: some View {
        SecureField(L("粘贴 token", "Paste token"), text: $token)
        Button(L("保存", "Save")) { save() }
    }
}

private func noteSaved() -> String { L("已保存", "Saved") }
'''

KANBAN = '''
struct KanbanView: View {
    var body: some View {
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 12) {
                collapsibleColumn(title: L("潜在任务 · backlog", "Backlog"), count: 1,
                                  emptyText: L("空", "Empty"), isEmpty: false,
                                  expanded: $a, motionKey: "debt") { }
                column(title: L("提案 · proposals", "Proposals"), count: 1,
                       emptyText: L("空", "Empty"), isEmpty: false, motionKey: "approval") { }
                column(title: L("运行中 · running", "Running"), count: 1,
                       emptyText: L("空", "Empty"), isEmpty: false, motionKey: "running") { }
                collapsibleColumn(title: L("🗄 永久性完成", "🗄 Done for good"), count: 1,
                                  emptyText: L("空", "Empty"), isEmpty: false,
                                  expanded: $b, motionKey: "archived") { }
            }
            .padding(16)
        }
    }
}
'''

CARDS = '''
struct ApprovalCardView: View {
    var body: some View {
        HStack(spacing: 8) {
            Button {
                app.submit(id: card.id, action: "approve", comment: nil)
            } label: { Label(L("批准", "Approve"), systemImage: "checkmark.circle.fill") }
            Button {
                withAnimation { expanded.toggle() }
            } label: {
                Text(expanded ? L("收起 ▾", "Collapse ▾") : L("展开详情 ▸", "Details ▸"))
            }
            Text(L("请求合并建议 (\\(activeCount))", "Request merge suggestions (\\(activeCount))"))
        }
        .confirmationDialog(L("停止这个任务？", "Stop this task?"), isPresented: $showStop) {
            Button(L("退回提案", "Discard & re-propose"), role: .destructive) { }
        }
    }
}

struct TaskRow: View {
    var body: some View {
        Menu {
            Button(L("停止", "Stop")) { }
        } label: { Text(L("更多", "More")) }
        Button(L("让 AI 修", "Fix with AI")) { }
    }
}
'''

NOTIFY = '''
final class NotifyRelay {
    func drain() {
        if e.kind == "review_ready" && reviewMode == "off" { return }
    }
}
'''

FILES = {
    "MainWindow.swift": MAIN_WINDOW,
    "AppDelegate.swift": APP_DELEGATE,
    "Settings.swift": SETTINGS,
    "SettingsSlack.swift": SETTINGS_SLACK,
    "Kanban.swift": KANBAN,
    "Cards.swift": CARDS,
    "NotifyRelay.swift": NOTIFY,
}


def _write_fixture(root):
    for name, text in FILES.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(text)


class _FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        _write_fixture(cls.tmp.name)
        cls.inventory = inv.build_inventory(cls.tmp.name)
        cls.controls = {c["id"]: c for c in cls.inventory["controls"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class RailAndScreensTestCase(_FixtureCase):
    def test_rail_items_follow_case_order_with_titles_icons_and_cmd_numbers(self):
        rail = self.inventory["rail"]
        self.assertEqual(rail["side"], "left")
        self.assertEqual([(r["slug"], r["zh"], r["en"], r["icon"], r["shortcut"]) for r in rail["items"]], [
            ("dashboard", "任务台", "Workbench", "tray.full", "⌘1"),
            ("trash", "回收站", "Trash", "trash", "⌘2"),
            ("settings", "设置", "Settings", "gearshape", "⌘3"),
        ])
        self.assertNotIn("control:rail:label:workbench", self.controls)  # 栏目标题不重复进 controls

    def test_settings_registry_becomes_section_screens_with_owner(self):
        screens = {s["id"]: s for s in self.inventory["screens"]}
        self.assertEqual(screens["screen:settings.general"]["en"], "General")
        self.assertEqual(screens["screen:settings.slack"]["anchor"], "slack")
        self.assertEqual(screens["screen:settings.menuBar"]["owner"], "retired")
        self.assertFalse(screens["screen:settings.menuBar"]["gated"])
        self.assertEqual(screens["screen:trash"]["kind"], "rail-page")


class ControlClassificationTestCase(_FixtureCase):
    def test_roles_from_call_chain(self):
        roles = {cid: c["role"] for cid, c in self.controls.items()}
        self.assertEqual(roles["control:board.needs_approval:button:approve"], "button")
        self.assertEqual(roles["control:board.needs_approval:button:details"], "button")
        self.assertEqual(roles["control:board.needs_approval:button:collapse"], "button")
        self.assertEqual(roles["control:board.needs_approval:dialog:stop-this-task"], "dialog")
        self.assertEqual(roles["control:board.needs_approval:button:discard-re-propose"], "button")
        self.assertEqual(roles["control:board.running:menu:more"], "menu")
        self.assertEqual(roles["control:board.running:button:stop"], "button")
        self.assertEqual(roles["control:settings.general:toggle:launch-at-login"], "toggle")
        self.assertEqual(roles["control:settings.general:option:english"], "option")
        self.assertEqual(roles["control:settings.general:option:中文-zh"], "option")
        self.assertEqual(roles["control:settings.general:label:interface-language"], "label")
        self.assertEqual(roles["control:settings:textfield:search-settings-f"], "textfield")
        self.assertEqual(roles["control:settings.slack:textfield:paste-token"], "textfield")
        self.assertEqual(roles["control:board.dialogs:alert-button:approve"], "alert-button")
        self.assertEqual(roles["control:board.dialogs:label:confirm"], "label")
        self.assertEqual(roles["control:window:help:collapse-expand-sidebar"], "help")

    def test_long_sentences_are_informational_copy(self):
        copy = [c for c in self.controls.values() if c["role"] == "copy"]
        self.assertEqual(len(copy), 1)
        self.assertFalse(copy[0]["gated"])
        self.assertTrue(copy[0]["en"].startswith("Uses macOS login items"))

    def test_interpolation_becomes_placeholder(self):
        c = self.controls["control:board.needs_approval:label:request-merge-suggestions-activecount"]
        self.assertEqual(c["en"], "Request merge suggestions ({activeCount})")

    def test_duplicate_ids_get_dense_suffixes_in_source_order(self):
        first = self.controls["control:settings.general:button:open"]
        second = self.controls["control:settings.general:button:open#2"]
        self.assertLess(int(first["source"].split(":")[1]), int(second["source"].split(":")[1]))

    def test_screen_attribution_registry_member_type_and_file_default(self):
        self.assertEqual(self.controls["control:settings.general:toggle:launch-at-login"]["screen"],
                         "settings.general")
        self.assertEqual(self.controls["control:settings.slack:button:save"]["screen"], "settings.slack")
        # 同文件的 helper 归到该 section（file default 来自注册表类型所在文件）
        self.assertEqual(self.controls["control:settings.slack:label:saved"]["screen"], "settings.slack")
        self.assertEqual(self.controls["control:settings:button:clear"]["screen"], "settings")

    def test_owner_and_gating(self):
        menu_bar = self.controls["control:settings.menuBar:toggle:show-menu-bar-icon"]
        self.assertEqual((menu_bar["owner"], menu_bar["gated"]), ("retired", False))
        status = self.controls["control:menu.status:menu-item:open-main-window"]
        self.assertEqual((status["owner"], status["gated"]), ("retired", False))
        main_menu = self.controls["control:menu.main:menu-item:settings"]
        self.assertEqual((main_menu["owner"], main_menu["gated"]), ("shell", False))
        self.assertTrue(self.controls["control:board.running:button:fix-with-ai"]["gated"])

    def test_card_affordances_group_verbs_by_lane(self):
        aff = self.inventory["lanes"]["card_affordances"]
        self.assertEqual([r["en"] for r in aff["needs_approval"]],
                         ["Approve", "Collapse ▾", "Details ▸", "Discard & re-propose"])
        self.assertEqual([r["en"] for r in aff["running"]], ["Fix with AI", "Stop", "More"])  # 按 id 排


class LanesKeysShortcutsTestCase(_FixtureCase):
    def test_lanes_order_rails_and_slugs(self):
        lanes = self.inventory["lanes"]
        self.assertEqual(lanes["order"], ["debt", "needs_approval", "running", "archived"])
        self.assertEqual([lane["rail"] for lane in lanes["items"]], ["left", None, None, "right"])
        self.assertEqual(lanes["items"][1]["en"], "Proposals")
        self.assertNotIn("control:board:label:proposals", self.controls)   # 列名只在 lanes 节

    def test_settings_keys_cover_overrides_nested_and_prefs(self):
        keys = {(k["store"], k["key"]): k for k in self.inventory["settings_keys"]}
        for expected in (("overrides", "updates_check_enabled"), ("overrides", "features.slack_radar"),
                         ("overrides", "telemetry.enabled"), ("overrides", "language"),
                         ("overrides", "review_notify"), ("prefs", "cardSortOrder"),
                         ("prefs", "sidebarWidth"), ("prefs", "showMenuBarIcon")):
            self.assertIn(expected, keys)
        self.assertNotIn(("overrides", "features"), keys)     # 容器键不算
        self.assertNotIn(("overrides", "telemetry"), keys)
        self.assertTrue(keys[("prefs", "cardSortOrder")]["sources"][0].startswith("Settings.swift:"))

    def test_shortcuts_menu_items_keyboard_shortcuts_owner(self):
        shortcuts = {s["id"]: s for s in self.inventory["shortcuts"]}
        self.assertEqual(shortcuts["shortcut:menu.main:cmd-,-settings"]["key"], "⌘,")
        self.assertEqual(shortcuts["shortcut:menu.main:cmd-q-quit"]["owner"], "os")
        self.assertEqual(shortcuts["shortcut:menu.main:shift-cmd-z-redo"]["key"], "⇧⌘Z")
        self.assertEqual(shortcuts["shortcut:menu.main:shift-cmd-z-redo"]["owner"], "os")
        self.assertEqual(shortcuts["shortcut:menu.main:opt-cmd-s-collapse-expand-sidebar"]["key"], "⌥⌘S")
        self.assertEqual(shortcuts["shortcut:settings:cmd-f"]["gated"], True)
        self.assertNotIn("shortcut:menu.main:-about", shortcuts)  # 空 keyEquivalent 不是快捷键

    def test_notification_kinds(self):
        self.assertEqual([n["id"] for n in self.inventory["notifications"]],
                         ["notification:review_ready", "notification:general"])
        self.assertFalse(any(n["gated"] for n in self.inventory["notifications"]))

    def test_theme_layout_pointers_and_stats(self):
        ids = [t["id"] for t in self.inventory["theme_layout"]]
        self.assertIn("theme:default", ids)
        self.assertIn("layout:lane-width", ids)
        counts = inv.stats(self.inventory)
        self.assertEqual(counts["rail"], {"total": 3, "gated": 3})
        self.assertGreater(counts["control"]["total"], counts["control"]["gated"])


class DeterminismAndCliTestCase(unittest.TestCase):
    def test_rebuild_is_byte_identical_and_cli_check_write_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "Sources")
            os.makedirs(src)
            _write_fixture(src)
            out = os.path.join(tmp, "inventory.json")
            a = json.dumps(inv.build_inventory(src), sort_keys=True)
            b = json.dumps(inv.build_inventory(src), sort_keys=True)
            self.assertEqual(a, b)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(inv.main(["--check", "--root", src, "--out", out]), 1)
            self.assertIn("stale", err.getvalue())
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(inv.main(["--write", "--stats", "--root", src, "--out", out]), 0)
                self.assertEqual(inv.main(["--check", "--root", src, "--out", out]), 0)
            self.assertIn("rail", buf.getvalue())
            self.assertIn("fresh", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["source"]["files"], len(FILES))

    def test_empty_source_dir_yields_empty_but_valid_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            inventory = inv.build_inventory(tmp)
            self.assertEqual(inventory["controls"], [])
            self.assertEqual(inventory["rail"]["items"], [])
            self.assertEqual(inventory["lanes"]["order"], [])
            self.assertEqual(inventory["settings_keys"], [])
            self.assertEqual([n["id"] for n in inventory["notifications"]], ["notification:general"])
            self.assertEqual(list(inv.iter_items(inventory))[-1]["id"], "layout:rail-collapsed-width")


if __name__ == "__main__":
    unittest.main()
