// MenuHarness.swift — behavior tests for the shell main-menu table in
// shell/Sources/ShellSupport.swift (CONTRACT §54 追记「菜单 l10n」 / §68.13: MenuSpec —
// the native AppDelegate.installMainMenu rebuilt as a pure `menus(lang:appName:)`
// table: L()-style bilingual titles, 关于 → ?page=about, 设置… ⌘, → settings,
// 权限体检… → ?page=permissions, 隐藏 ⌘H / 隐藏其他 ⌥⌘H / 全部显示 / 退出 ⌘Q, 文件 →
// 关闭窗口 ⌘W, 编辑 chain, 显示 → 重新载入 ⌘R + 聚焦捕获框 ⌘L, 窗口 → 最小化 ⌘M + 缩放;
// ⌥⌘S sidebar toggle deliberately retired). Compiled by run.sh together with every
// shell source except main.swift into a plain macOS CLI tool — no Xcode, no XCTest,
// no NSApplication, no NSMenu. Exits non-zero on any failure. Same harness style as
// BridgeHarness.swift / PolicyHarness.swift (one behaviour family per file, 防腐 #7).
//
// Why the table and not NSApp.mainMenu: the menu cannot be enumerated without a
// running NSApplication, so main.swift installs MenuSpec.menus(lang:) verbatim
// (title / keyEquivalent / modifier / target) and the table is what gets pinned.
// The rebuild-on-language-switch half is a `LanguageStore.$lang` sink in
// main.swift; the property it depends on — the table is a pure function of `lang`,
// never of LanguageMirror (which is still the OLD language when a @Published sink
// runs) — is pinned here by flipping the mirror and asserting the table ignores it.

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

let appName = "Zelin's AI Assistant"

func flat(_ menus: [MenuSpec.Menu]) -> [MenuSpec.Item] { menus.flatMap { $0.items } }
func titles(_ menu: MenuSpec.Menu) -> [String] {
    menu.items.filter { $0.action != .separator }.map { $0.title }
}
func item(_ menus: [MenuSpec.Menu], _ action: MenuSpec.Action) -> MenuSpec.Item? {
    flat(menus).first { $0.action == action }
}
func hasCJK(_ s: String) -> Bool {
    s.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }
}

func run() {
    let zh = MenuSpec.menus(lang: "zh", appName: appName)
    let en = MenuSpec.menus(lang: "en", appName: appName)

    // ---- 1. shape: five top-level menus in the native order, app menu untitled ----
    print("[1] MenuSpec shape:")
    check(zh.map { $0.title } == ["", "文件", "编辑", "显示", "窗口"],
          "zh top-level = App / 文件 / 编辑 / 显示 / 窗口 (AppDelegate.installMainMenu order)",
          String(describing: zh.map { $0.title }))
    check(en.map { $0.title } == ["", "File", "Edit", "View", "Window"],
          "en top-level = App / File / Edit / View / Window",
          String(describing: en.map { $0.title }))
    check(zh.map { $0.isWindowsMenu } == [false, false, false, false, true],
          "only 窗口 is registered as NSApp.windowsMenu")
    check(zh.count == en.count && zip(zh, en).allSatisfy { $0.items.count == $1.items.count },
          "zh and en tables have the same shape (only titles differ)")
    // structure minus titles must be identical across languages: keys / modifiers / actions
    let zhSkeleton = flat(zh).map { "\($0.key)|\($0.option)|\($0.action)" }
    let enSkeleton = flat(en).map { "\($0.key)|\($0.option)|\($0.action)" }
    check(zhSkeleton == enSkeleton, "key equivalents, modifiers and actions are language-independent")
    check(flat(zh).allSatisfy { ($0.action == .separator) == $0.title.isEmpty },
          "every non-separator item has a title; separators have none")
    check(!flat(en).contains { hasCJK($0.title) }, "en table has no CJK title",
          String(describing: flat(en).filter { hasCJK($0.title) }.map { $0.title }))
    check(flat(zh).filter { $0.action != .separator }.allSatisfy { hasCJK($0.title) },
          "every zh title is actually Chinese",
          String(describing: flat(zh).filter { $0.action != .separator && !hasCJK($0.title) }.map { $0.title }))
    // L() semantics: anything that is not "en" is Chinese (LanguageStore normalises to zh|en,
    // but the table must not invent a third language on a stray value)
    check(MenuSpec.menus(lang: "fr", appName: appName) == zh, "unknown lang → zh (same rule as L())")
    check(MenuSpec.menus(lang: "", appName: appName) == zh, "empty lang → zh")

    // ---- 2. app menu (AppDelegate.swift:407-443) ----
    print("[2] App menu:")
    check(titles(zh[0]) == ["关于 \(appName)", "设置…", "权限体检…", "隐藏 \(appName)", "隐藏其他", "全部显示", "退出"],
          "zh app menu titles verbatim from the native menu", String(describing: titles(zh[0])))
    check(titles(en[0]) == ["About \(appName)", "Settings…", "Permissions Checkup…",
                            "Hide \(appName)", "Hide Others", "Show All", "Quit"],
          "en app menu titles verbatim from the native menu", String(describing: titles(en[0])))
    check(zh[0].items.map { $0.action == .separator } == [false, true, false, false, true, false, false, false, true, false],
          "separators after About, after Permissions Checkup, before Quit (native grouping)")
    check(MenuSpec.menus(lang: "zh", appName: "X").flatMap { $0.items }.map { $0.title }.contains("关于 X"),
          "app name is interpolated (ShellConfig.displayName), not hard-wired")
    let about = item(zh, .shell(.about))
    check(about?.title == "关于 \(appName)" && about?.key == "",
          "关于 → ShellAction.about (board ?page=about — NOT the stock About panel), no shortcut")
    let settings = item(zh, .shell(.settings))
    check(settings?.title == "设置…" && settings?.key == "," && settings?.option == false,
          "设置… → ShellAction.settings with ⌘, (shortcut:menu.main:cmd-,-settings)")
    let perms = item(zh, .shell(.permissions))
    check(perms?.title == "权限体检…" && perms?.key == "",
          "权限体检… → ShellAction.permissions (board ?page=permissions), no shortcut")
    let hide = item(zh, .responder("hide:"))
    check(hide?.key == "h" && hide?.option == false, "隐藏 <app> = hide: ⌘H (responder chain → NSApplication)")
    let hideOthers = item(zh, .responder("hideOtherApplications:"))
    check(hideOthers?.title == "隐藏其他" && hideOthers?.key == "h" && hideOthers?.option == true,
          "隐藏其他 = hideOtherApplications: ⌥⌘H (the one item with an extra modifier)")
    let showAll = item(zh, .responder("unhideAllApplications:"))
    check(showAll?.title == "全部显示" && showAll?.key == "", "全部显示 = unhideAllApplications:, no shortcut")
    let quit = item(zh, .responder("terminate:"))
    check(quit?.title == "退出" && quit?.key == "q" && quit?.option == false,
          "退出 = terminate: ⌘Q (Dock app quits normally, no ⌘Q guard — §54 v0.48.19)")

    // ---- 3. File / Edit (AppDelegate.swift:445-473) ----
    print("[3] File / Edit menus:")
    check(titles(zh[1]) == ["关闭窗口"] && titles(en[1]) == ["Close Window"], "文件 → 关闭窗口 only")
    let close = item(zh, .responder("performClose:"))
    check(close?.key == "w" && close?.option == false, "关闭窗口 = performClose: ⌘W via the responder chain (key window)")
    check(titles(zh[2]) == ["撤销", "重做", "剪切", "拷贝", "粘贴", "全选"],
          "编辑 chain titles (撤销 / 重做 / 剪切 / 拷贝 / 粘贴 / 全选)", String(describing: titles(zh[2])))
    check(titles(en[2]) == ["Undo", "Redo", "Cut", "Copy", "Paste", "Select All"],
          "Edit chain titles", String(describing: titles(en[2])))
    let editKeys = zh[2].items.filter { $0.action != .separator }.map { "\($0.key)" }
    check(editKeys == ["z", "Z", "x", "c", "v", "a"],
          "edit keys ⌘Z / ⇧⌘Z (uppercase = shift, AppKit convention) / ⌘X / ⌘C / ⌘V / ⌘A",
          String(describing: editKeys))
    let editSelectors = zh[2].items.compactMap { i -> String? in
        if case .responder(let s) = i.action { return s } else { return nil }
    }
    check(editSelectors == ["undo:", "redo:", "cut:", "copy:", "paste:", "selectAll:"],
          "edit chain is nil-target responder selectors (webview text fields must eat ⌘C/⌘V/⌘Z)",
          String(describing: editSelectors))
    check(zh[2].items[2].action == .separator, "separator between Redo and Cut (native grouping)")

    // ---- 4. View (AppDelegate.swift:475-503 minus ⌘1..5 and ⌥⌘S) ----
    print("[4] View menu:")
    check(titles(zh[3]) == ["重新载入", "聚焦捕获框"] && titles(en[3]) == ["Reload", "Focus Capture Field"],
          "显示 → 重新载入 + 聚焦捕获框 (⌘1..7 page switching belongs to the web NavRail)",
          String(describing: titles(zh[3]) + titles(en[3])))
    let reload = item(zh, .shell(.reload))
    check(reload?.key == "r" && reload?.option == false, "重新载入 → ShellAction.reload ⌘R (shell-only item, kept)")
    let focus = item(zh, .shell(.focusCapture))
    check(focus?.title == "聚焦捕获框" && focus?.key == "l" && focus?.option == false,
          "聚焦捕获框 → ShellAction.focusCapture ⌘L (shortcut:menu.main:cmd-l-focus-capture-field; same path as ⌃⌥Space)")
    check(item(en, .shell(.focusCapture))?.title == "Focus Capture Field", "en: Focus Capture Field")
    // owner decision (s4 acceptance table DELETE list): no ⌥⌘S sidebar toggle — the web board has no
    // collapsible sidebar. Pin the absence so nobody re-adds it by mirroring the native file blindly.
    check(!flat(zh).contains { $0.key.lowercased() == "s" },
          "no ⌥⌘S 折叠/展开侧栏 (retired, §68.13 tombstone)")
    check(!flat(zh).contains { $0.title.contains("侧栏") } && !flat(en).contains { $0.title.contains("Sidebar") },
          "no sidebar item in either language")

    // ---- 5. Window (AppDelegate.swift:505-517) ----
    print("[5] Window menu:")
    check(titles(zh[4]) == ["最小化", "缩放"] && titles(en[4]) == ["Minimize", "Zoom"],
          "窗口 → 最小化 + 缩放 (Zoom was missing from the shell)", String(describing: titles(zh[4]) + titles(en[4])))
    let mini = item(zh, .responder("performMiniaturize:"))
    check(mini?.key == "m" && mini?.option == false, "最小化 = performMiniaturize: ⌘M")
    let zoom = item(zh, .responder("performZoom:"))
    check(zoom?.title == "缩放" && zoom?.key == "", "缩放 = performZoom:, no shortcut (native)")

    // ---- 6. shortcut hygiene: nothing the page owns, no duplicates, every shell action wired once ----
    print("[6] Shortcut hygiene:")
    let keyed = flat(zh).filter { !$0.key.isEmpty }
    let combos = keyed.map { "\($0.option ? "⌥" : "")⌘\($0.key)" }
    check(Set(combos).count == combos.count, "no two items share a key equivalent (⌘H vs ⌥⌘H differ by modifier)",
          String(describing: combos))
    check(!keyed.contains { $0.key.lowercased() == "f" }, "no ⌘F item — the board binds Cmd+F itself, the menu must not intercept it")
    check(!keyed.contains { $0.key.first?.isNumber == true }, "no ⌘1..⌘9 items — page shortcuts live in web NavRail")
    for action in MenuSpec.ShellAction.allCases {
        let n = flat(zh).filter { $0.action == .shell(action) }.count
        check(n == 1, "ShellAction.\(action) appears exactly once", "count=\(n)")
    }

    // ---- 7. the table is a pure function of `lang`, never of LanguageMirror ----
    // main.swift rebuilds the menu from a `LanguageStore.$lang` sink; @Published publishes in
    // willSet, so when the sink runs LanguageMirror (hence L()) still holds the OLD language.
    // If MenuSpec read L() the menu would always lag one switch behind.
    print("[7] Pure in lang (ignores LanguageMirror):")
    let saved = LanguageMirror.current
    LanguageMirror.current = "en"
    check(MenuSpec.menus(lang: "zh", appName: appName) == zh, "mirror=en, lang=zh → zh table")
    LanguageMirror.current = "zh"
    check(MenuSpec.menus(lang: "en", appName: appName) == en, "mirror=zh, lang=en → en table")
    LanguageMirror.current = saved
    check(zh != en, "zh and en tables differ (titles are actually localised)")
}

run()
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
