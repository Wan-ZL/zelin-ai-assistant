// AppDelegate.swift — AppDelegate（状态栏/popover/主菜单/inbox 写入/prompt 弹窗）/ StatusDropView / PromptSendDelegate
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// MARK: - App delegate

extension Notification.Name {
    // item 7b: ⌘L (View menu) → focus the quick-capture input field
    static let focusCaptureField = Notification.Name("focusCaptureField")
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    let store = DashboardStore()
    private var statusItem: NSStatusItem?
    private let popover = NSPopover()
    /// Read-only popover visibility for views outside this file (e.g. the
    /// kanban composer ignores .focusCaptureField while the popover is open).
    var popoverIsShown: Bool { popover.isShown }
    private var refreshTimer: Timer?
    private var popoverClickMonitor: Any?
    private var popoverKeyMonitor: Any?
    // item 1: app-lifetime local monitor — Shift+Return inserts a newline in
    // field-editor-backed (SwiftUI) text fields; plain Return keeps submitting.
    private var shiftReturnMonitor: Any?
    // click-outside defocus: app-lifetime local monitor — see install below.
    private var clickDefocusMonitor: Any?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // P0-12: force the language store to resolve (override → system locale)
        // BEFORE the first L() call — the main menu below reads LanguageMirror,
        // which only leaves its "zh" fallback once LanguageStore.shared exists.
        _ = LanguageStore.shared
        // ⌘C/⌘V/⌘A/⌘Z dispatch through the main menu even for a menu-bar app —
        // without an Edit menu every text field is copy/paste-dead.
        installMainMenu()
        // main icon + recording-control icon, per UserDefaults visibility prefs
        updateStatusItemsVisibility()

        // §28: relayed-notification clicks open the main window; banners keep
        // showing while the app is frontmost.
        NotifyRelayDelegate.install()

        popover.behavior = .transient
        popover.contentSize = NSSize(width: 400, height: 560)
        popover.contentViewController = NSHostingController(
            rootView: DashboardView(store: store, app: self)
        )
        // Single choke point for popover-close cleanup: no matter HOW the
        // popover closes (toggle, outside click, Esc, ⌘W), popoverDidClose
        // removes the global click + local key monitors.
        popover.delegate = self

        // recording engine: keep the menu-bar icon in sync + autostart per mode.
        // P0-11: a fresh install must not capture anything before the one-time
        // consent prompt — recording defaults to off, and this autostart only
        // runs once a mode exists (prior consent or pre-existing prefs).
        //
        // v0.14: the setup wizard (SetupWizard.swift) replaces the single
        // first-run permissions page. It opens whenever its completion marker
        // (UserDefaults "setupWizardCompleted") is missing or corrupt — fresh
        // installs, upgrades that never saw the wizard, and interrupted runs
        // alike. Idempotent: every step is prefilled and an answered recording
        // consent (recordingConsentShown / recordingMode) is never re-asked.
        let wizardPending = !SetupWizardMarker.completed
        if RecordingConsent.needsPrompt {
            // fresh install: consent is answered inside the wizard; no engine
            // autostart before consent (P0-11). Mark first-run consumed so the
            // P1-5 deps pop can never additionally fire on a later launch.
            UserDefaults.standard.set(true, forKey: "hasCompletedFirstRun")
            // deferred a turn so launch setup finishes before the window shows
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    Analytics.log("first_launch_permissions")
                    RecordingConsent.present()
                }
            }
        } else {
            RecordingController.shared.autostartIfNeeded()
            if wizardPending {
                // upgrade or interrupted wizard: reopen (once — 完成 writes the
                // marker). Everything is prefilled; nothing is wiped/re-asked.
                UserDefaults.standard.set(true, forKey: "hasCompletedFirstRun")
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        SetupWizardController.shared.show()
                    }
                }
            } else {
                DispatchQueue.main.async {
                    MainActor.assumeIsolated { self.openDepsOnFirstLaunchIfNeeded() }
                }
            }
        }

        // item 1: Shift+Return = newline in the SwiftUI capture fields (their
        // backing NSTextView is the window's field editor; isFieldEditor also
        // rules out the NSAlert comment editor, which has its own delegate).
        // IME red line: during pinyin composition (hasMarkedText) Return
        // belongs to the input method — pass the event through untouched.
        shiftReturnMonitor = NSEvent.addLocalMonitorForEvents(
            matching: .keyDown) { event in
            let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            guard event.keyCode == 36,  // 36 = Return
                  mods.contains(.shift),
                  mods.isDisjoint(with: [.command, .option, .control])
            else { return event }
            var handled = false
            MainActor.assumeIsolated {
                guard let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
                      tv.isFieldEditor, !tv.hasMarkedText()
                else { return }
                tv.insertNewlineIgnoringFieldEditor(nil)
                handled = true
            }
            return handled ? nil : event
        }

        // Click-outside defocus: AppKit keeps the first responder when a
        // click lands on dead space, so a focused field's caret survives
        // clicks anywhere outside it. Expected macOS feel: click outside =
        // defocus. Watch mouseDown app-wide (main window, popover, panels):
        // when a field editor owns the caret and the click doesn't land on a
        // text input, end editing — @FocusState bindings sync to false and
        // drafts stay in their bindings. The event is ALWAYS returned
        // unmodified, so the click itself (button action, card tap, drag,
        // scroll) proceeds exactly as before; only the caret moves out.
        clickDefocusMonitor = NSEvent.addLocalMonitorForEvents(
            matching: .leftMouseDown) { event in
            MainActor.assumeIsolated {
                guard let window = event.window,
                      let editor = window.firstResponder as? NSTextView,
                      editor.isFieldEditor,
                      let content = window.contentView,
                      // nil hit = title bar / window edge — native behavior
                      // there (drag/resize) never moved the caret; keep that.
                      let hit = content.hitTest(event.locationInWindow)
                else { return }
                var v: NSView? = hit
                while let view = v {
                    // clicks INTO any text input keep the normal focus path;
                    // scroller clicks keep native behavior (scrolling never
                    // moves the caret).
                    if view is NSTextView || view is NSTextField
                        || view is NSScroller { return }
                    v = view.superview
                }
                window.makeFirstResponder(nil)
            }
            return event
        }

        refresh()
        // Schedule in .common run-loop mode so the timer keeps firing while the
        // popover is open. A .default-mode timer (Timer.scheduledTimer) is
        // suspended during status-item/popover event tracking — that was the bug
        // where the open popover only updated after close+reopen.
        let timer = Timer(timeInterval: 5.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.refresh()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        refreshTimer = timer

        // Do NOT show the main window on launch: background restarts (login
        // items, install scripts) would pop it over whatever Zelin is doing —
        // including fullscreen video. A deliberate double-click on the already
        // running app lands in applicationShouldHandleReopen below, which is
        // the one place that opens the window — with ONE exception: the P1-5
        // first-launch-without-dashboard walk-through (openDepsOnFirstLaunch-
        // IfNeeded above), which fires at most once per user, ever.
    }

    // P1-5 first-launch UX: with no dashboard.json the popover is a dead end
    // ("waiting for pipeline") — open the main window ON the Dependencies page
    // exactly once, so the first thing a new user sees is the checklist that
    // names what's missing. hasCompletedFirstRun is set on the first launch
    // no matter what, so this can never turn into a nag.
    private func openDepsOnFirstLaunchIfNeeded() {
        guard !Prefs.bool("hasCompletedFirstRun", default: false) else { return }
        UserDefaults.standard.set(true, forKey: "hasCompletedFirstRun")
        guard !FileManager.default.fileExists(atPath: AppPaths.dashboardPath) else { return }
        Analytics.log("first_launch_deps")
        MainNav.shared.section = .deps
        openMainWindow(nil)
    }

    // Double-clicking the app in Finder/Dock while it is already running
    // (no windows visible) re-opens the main window instead of doing nothing.
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        if !flag { MainWindowController.shared.show() }
        return true
    }

    // Modern-AppKit standard: opt in to secure state restoration (silences the
    // "Secure coding is not enabled for restorable state" launch warning).
    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    func refresh() {
        store.reload()
        updateStatusTitle()
        // §28: post + delete any python-relayed notifications (app identity)
        NotifyRelay.drain()
        // consent-race self-heal / TCC-loss watch first (cheap TCC read) so a
        // fresh grant restarts the engine before the liveness poll reports it
        RecordingController.shared.pollScreenPermission()
        RecordingController.shared.refreshEngineState()
    }

    // MARK: status items (visibility per UserDefaults, changeable from 设置)

    func updateStatusItemsVisibility() {
        let showMain = Prefs.bool("showMenuBarIcon", default: true)
        if showMain, statusItem == nil { makeMainStatusItem() }
        if !showMain, let item = statusItem {
            if popover.isShown { popover.performClose(nil) }
            removePopoverClickMonitor()
            NSStatusBar.system.removeStatusItem(item)
            statusItem = nil
        }
        updateStatusTitle()
    }

    // last symbol pushed to the status button (P1-4 health swap) — avoids
    // recreating the NSImage on every 5 s refresh tick.
    private var statusSymbolShown = "checklist"

    private func makeMainStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            statusSymbolShown = "checklist"   // fresh item starts at the default
            button.image = NSImage(systemSymbolName: "checklist",
                                   accessibilityDescription: "Zelin's AI Assistant")
            button.imagePosition = .imageLeading
            button.target = self
            button.action = #selector(togglePopover(_:))
            // §15: right-click on the status item opens a small menu (main window / quit).
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
            // item 7c: transparent drop overlay — drag selected text from any
            // app onto the icon to quick-capture it (clicks pass through).
            let drop = StatusDropView(frame: button.bounds)
            drop.autoresizingMask = [.width, .height]
            drop.app = self
            button.addSubview(drop)
        }
        statusItem = item
    }

    private func updateStatusTitle() {
        guard let button = statusItem?.button else { return }
        // render-array count (visible cards + local placeholders), NOT the raw
        // backend counter — keeps the badge in sync with what the popover shows
        let n = store.visibleApprovals.count
        if n > 0 {
            button.title = " \(n)"
        } else {
            button.title = ""
        }
        // P1-4: unhealthy pipeline → warning triangle in the menu bar itself;
        // the 10pt footer note alone was invisible until the popover opened.
        let symbol = store.pipelineHealth == .ok ? "checklist" : "exclamationmark.triangle"
        if symbol != statusSymbolShown {
            statusSymbolShown = symbol
            button.image = NSImage(systemSymbolName: symbol,
                                   accessibilityDescription: "Zelin's AI Assistant")
        }
    }

    @objc private func togglePopover(_ sender: Any?) {
        guard statusItem?.button != nil else { return }
        // §15: right-click = context menu (main window / quit); left-click = popover.
        if let event = NSApp.currentEvent, event.type == .rightMouseUp {
            showStatusMenu()
            return
        }
        // item 7a: ⌥+click on the icon = straight to the main window
        if let event = NSApp.currentEvent, event.modifierFlags.contains(.option) {
            openMainWindow(sender)
            return
        }
        if popover.isShown {
            popover.performClose(sender)
        } else {
            showPopover(source: "click")
        }
    }

    /// Open the popover on the status item and install the outside-click +
    /// Esc monitors.
    /// 契约F: every successful show logs popover_open{source}; the source
    /// vocabulary is click|hotkey|menu|reopen. hotkey retired in v0.15 (the
    /// Carbon global hotkey was removed with its settings UI); menu/reopen
    /// are reserved — no menu item opens the popover today, and a Dock/Finder
    /// reopen goes to the main window (applicationShouldHandleReopen).
    private func showPopover(source: String) {
        guard let button = statusItem?.button else { return }
        Analytics.log("popover_open", fields: ["source": source])
        refresh()
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        // .transient alone can't detect outside clicks for a menu-bar app
        // (we never become the active app) — watch globally and close.
        popoverClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if self.popover.isShown { self.popover.performClose(nil) }
                self.removePopoverClickMonitor()
            }
        }
        // Esc in the popover (item 6): non-empty capture draft → clear it
        // first; empty → close. The app never activates, so the standard
        // transient cancelOperation path is unreliable (and a focused
        // SwiftUI TextField can swallow Esc) — a local monitor is robust.
        popoverKeyMonitor = NSEvent.addLocalMonitorForEvents(
            matching: .keyDown) { [weak self] event in
            guard event.keyCode == 53 else { return event }  // 53 = Esc
            var handled = false
            MainActor.assumeIsolated {
                guard let self, self.popover.isShown else { return }
                // IME red line: Esc cancels a live pinyin composition —
                // the input method owns it, pass through.
                if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
                   tv.hasMarkedText() { return }
                if !CaptureDraft.popover.text.isEmpty {
                    CaptureDraft.popover.text = ""   // 1st Esc: clear draft
                } else {
                    self.popover.performClose(nil)   // 2nd Esc: close
                }
                handled = true
            }
            return handled ? nil : event
        }
    }

    // NSPopoverDelegate — the ONE cleanup path for every close route
    // (toggle click, outside click, Esc, ⌘W): drop both event monitors.
    func popoverDidClose(_ notification: Notification) {
        removePopoverClickMonitor()
        removePopoverKeyMonitor()
    }

    private func removePopoverClickMonitor() {
        if let m = popoverClickMonitor {
            NSEvent.removeMonitor(m)
            popoverClickMonitor = nil
        }
    }

    private func removePopoverKeyMonitor() {
        if let m = popoverKeyMonitor {
            NSEvent.removeMonitor(m)
            popoverKeyMonitor = nil
        }
    }

    // File > Close Window (⌘W). Route by context: an open popover closes
    // first; otherwise close the key window. Never send performClose to the
    // borderless popover window directly — it has no close button and beeps.
    @objc func closeKeyWindow(_ sender: Any?) {
        if popover.isShown {
            popover.performClose(sender)
            return
        }
        NSApp.keyWindow?.performClose(sender)
    }

    // App menu / status menu: open the main window on a specific page.
    @objc func openSettingsPage(_ sender: Any?) {
        MainNav.shared.section = .settings
        openMainWindow(sender)
    }

    // App menu / status menu / 设置 → 通用: reopen the first-run permissions
    // page anytime (权限体检 — Screen Recording / Notifications / Full Disk
    // Access, live statuses).
    @objc func openPermissionsWindow(_ sender: Any?) {
        if popover.isShown { popover.performClose(sender) }
        PermissionsWindowController.shared.show(firstRun: false)
    }

    @objc func openAboutPage(_ sender: Any?) {
        MainNav.shared.section = .about
        openMainWindow(sender)
    }

    // View menu (item 4): ⌘1..5 — open the main window on the tagged page
    // (tag = index into MainSection.allCases).
    @objc func showMainSection(_ sender: Any?) {
        guard let item = sender as? NSMenuItem,
              MainSection.allCases.indices.contains(item.tag) else { return }
        let section = MainSection.allCases[item.tag]
        // 契约F: ⌘1..5 menu nav — dest = section rawValue (dashboard/deps/…)
        Analytics.log("mw_nav", fields: ["dest": section.rawValue])
        MainNav.shared.section = section
        openMainWindow(sender)
    }

    // View menu (契约3/bucket D): ⌥⌘S — collapse/expand the main-window
    // sidebar. Works even when the window is closed (state persists via
    // UserDefaults and applies on next open).
    @objc func toggleSidebar(_ sender: Any?) {
        MainNav.shared.toggleSidebar()
    }

    // View menu (item 7b): ⌘L — put the caret in the quick-capture field
    // (popover when open, else the main-window board header).
    @objc func focusCaptureField(_ sender: Any?) {
        // 契约F: ⌘L counts as a nav gesture too — dest "capture" whether the
        // caret lands in the popover field or the main-window board header.
        Analytics.log("mw_nav", fields: ["dest": "capture"])
        if popover.isShown {
            popover.contentViewController?.view.window?.makeKey()
            NotificationCenter.default.post(name: .focusCaptureField, object: nil)
            return
        }
        openMainWindow(sender)
        // let the window / hosting view land first on a cold open
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            MainActor.assumeIsolated {
                NotificationCenter.default.post(name: .focusCaptureField, object: nil)
            }
        }
    }

    /// Standard main menu (App / File / Edit / Window). Keyboard shortcuts
    /// (⌘W/⌘M/⌘C/⌘V/⌘A/⌘Z…) dispatch through the main menu even for a
    /// menu-bar app; without this, text fields silently ignore copy/paste and
    /// windows ignore close/minimize. Not private: the 设置 page re-installs
    /// the menu after a UI-language switch (NSMenu doesn't observe SwiftUI).
    func installMainMenu() {
        let main = NSMenu()

        // App menu — About / Settings… / Hide / Quit, macOS convention.
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        let about = NSMenuItem(
            title: L("关于 Zelin's AI Assistant", "About Zelin's AI Assistant"),
            action: #selector(openAboutPage(_:)), keyEquivalent: "")
        about.target = self
        appMenu.addItem(about)
        appMenu.addItem(.separator())
        let settings = NSMenuItem(title: L("设置…", "Settings…"),
                                  action: #selector(openSettingsPage(_:)),
                                  keyEquivalent: ",")
        settings.target = self
        appMenu.addItem(settings)
        let permsItem = NSMenuItem(title: L("权限体检…", "Permissions Checkup…"),
                                   action: #selector(openPermissionsWindow(_:)),
                                   keyEquivalent: "")
        permsItem.target = self
        appMenu.addItem(permsItem)
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: L("隐藏 Zelin's AI Assistant", "Hide Zelin's AI Assistant"),
                        action: #selector(NSApplication.hide(_:)),
                        keyEquivalent: "h")
        let hideOthers = appMenu.addItem(
            withTitle: L("隐藏其他", "Hide Others"),
            action: #selector(NSApplication.hideOtherApplications(_:)),
            keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: L("全部显示", "Show All"),
                        action: #selector(NSApplication.unhideAllApplications(_:)),
                        keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: L("退出", "Quit"),
                        action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        appItem.submenu = appMenu

        // File menu — Close Window ⌘W (popover-aware, see closeKeyWindow).
        let fileItem = NSMenuItem()
        main.addItem(fileItem)
        let file = NSMenu(title: L("文件", "File"))
        let close = NSMenuItem(title: L("关闭窗口", "Close Window"),
                               action: #selector(closeKeyWindow(_:)),
                               keyEquivalent: "w")
        close.target = self
        file.addItem(close)
        fileItem.submenu = file

        // Edit menu — text fields are copy/paste-dead without it.
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: L("编辑", "Edit"))
        edit.addItem(withTitle: L("撤销", "Undo"),
                     action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: L("重做", "Redo"),
                     action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: L("剪切", "Cut"),
                     action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: L("拷贝", "Copy"),
                     action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: L("粘贴", "Paste"),
                     action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: L("全选", "Select All"),
                     action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        // View menu (items 4/7b) — ⌘1..5 switch the five main-window pages,
        // ⌘L focuses the quick-capture field. The whole menu is rebuilt on a
        // language switch, so titles follow the UI language automatically.
        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let view = NSMenu(title: L("显示", "View"))
        for (i, s) in MainSection.allCases.enumerated() {
            let mi = NSMenuItem(title: s.title,
                                action: #selector(showMainSection(_:)),
                                keyEquivalent: "\(i + 1)")
            mi.target = self
            mi.tag = i
            view.addItem(mi)
        }
        view.addItem(.separator())
        let focus = NSMenuItem(title: L("聚焦捕获框", "Focus Capture Field"),
                               action: #selector(focusCaptureField(_:)),
                               keyEquivalent: "l")
        focus.target = self
        view.addItem(focus)
        // 契约3: ⌥⌘S toggles the main-window sidebar (no clash with any
        // existing shortcut; the 设置 page's LOCAL ⌘S has no ⌥ and wins there).
        let sidebar = NSMenuItem(title: L("折叠/展开侧栏", "Collapse/Expand Sidebar"),
                                 action: #selector(toggleSidebar(_:)),
                                 keyEquivalent: "s")
        sidebar.keyEquivalentModifierMask = [.command, .option]
        sidebar.target = self
        view.addItem(sidebar)
        viewItem.submenu = view

        // Window menu — Minimize/Zoom; registering as windowsMenu makes AppKit
        // list open windows in it automatically.
        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let window = NSMenu(title: L("窗口", "Window"))
        window.addItem(withTitle: L("最小化", "Minimize"),
                       action: #selector(NSWindow.performMiniaturize(_:)),
                       keyEquivalent: "m")
        window.addItem(withTitle: L("缩放", "Zoom"),
                       action: #selector(NSWindow.performZoom(_:)),
                       keyEquivalent: "")
        windowItem.submenu = window
        NSApp.windowsMenu = window

        NSApp.mainMenu = main
    }

    // Transient-menu trick: assign the menu, synthesize a click so AppKit shows
    // it, then detach so normal left-clicks keep toggling the popover.
    private func showStatusMenu() {
        guard let item = statusItem else { return }
        let menu = NSMenu()
        let mainWin = NSMenuItem(title: L("打开主窗口", "Open Main Window"),
                                 action: #selector(openMainWindow(_:)),
                                 keyEquivalent: "")
        mainWin.target = self
        menu.addItem(mainWin)
        let settings = NSMenuItem(title: L("设置…", "Settings…"),
                                  action: #selector(openSettingsPage(_:)),
                                  keyEquivalent: "")
        settings.target = self
        menu.addItem(settings)
        let perms = NSMenuItem(title: L("权限体检…", "Permissions Checkup…"),
                               action: #selector(openPermissionsWindow(_:)),
                               keyEquivalent: "")
        perms.target = self
        menu.addItem(perms)
        let about = NSMenuItem(title: L("关于", "About"),
                               action: #selector(openAboutPage(_:)),
                               keyEquivalent: "")
        about.target = self
        menu.addItem(about)
        // §26: low-key update line — present only while dashboard.json carries
        // update_available (a strictly newer release). Opens the release page;
        // nothing auto-downloads (unsigned .pkg + trust honesty).
        if let upd = store.dashboard?.update_available {
            menu.addItem(.separator())
            let updateItem = NSMenuItem(
                title: L("新版本 v\(upd.latest) 可用 — 下载安装包",
                         "Update v\(upd.latest) available — download installer"),
                action: #selector(openReleasePage(_:)), keyEquivalent: "")
            updateItem.target = self
            menu.addItem(updateItem)
        }
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: L("退出", "Quit"),
                                action: #selector(NSApplication.terminate(_:)),
                                keyEquivalent: "q"))
        item.menu = menu
        item.button?.performClick(nil)
        item.menu = nil
    }

    @objc func openMainWindow(_ sender: Any?) {
        if popover.isShown { popover.performClose(sender) }
        removePopoverClickMonitor()
        MainWindowController.shared.show()
    }

    // §26: status-menu update line → open the GitHub release page in the
    // browser. Download/install stays a deliberate user action.
    @objc func openReleasePage(_ sender: Any?) {
        guard let upd = store.dashboard?.update_available,
              let url = upd.releaseURL else { return }
        Analytics.log("update_open_release",
                      fields: ["source": "menu", "latest": upd.latest])
        NSWorkspace.shared.open(url)
    }

    // MARK: hello bubble (setup wizard finale — audit 2.5)

    // v0.14: after the wizard's 完成, point at the status item so users of
    // this menu-bar-only app know where it lives (otherwise "nothing
    // launched"). Separate NSPopover — the main dashboard popover, its click
    // monitors and toggle logic stay untouched.
    private var helloPopover: NSPopover?

    func showHelloBubble() {
        guard let button = statusItem?.button else { return }  // icon hidden → skip
        helloPopover?.performClose(nil)
        let pop = NSPopover()
        pop.behavior = .transient
        pop.contentViewController = NSHostingController(
            rootView: HelloBubbleView { [weak self] in
                self?.helloPopover?.performClose(nil)
            })
        pop.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        helloPopover = pop
        Analytics.log("wizard_hello_bubble")
        // auto-dismiss so a click-elsewhere user is never stuck with it
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
            MainActor.assumeIsolated {
                guard let self, let p = self.helloPopover, p === pop, p.isShown else { return }
                p.performClose(nil)
            }
        }
    }

    // T2 (§ v0 contract + task): typed confirmation gate. Returns true only if
    // the user typed 确认 or go (case-insensitive). A mismatch beeps and
    // re-prompts (with a "didn't match" note) instead of silently failing;
    // Cancel is the only way out with false.
    func confirmT2(id: String, summary: String) -> Bool {
        var mismatched = false
        while true {
            let alert = NSAlert()
            alert.messageText = L("T2 · 高影响操作确认", "T2 · High-Impact Action Confirmation")
            var info = L(
                "批准 \(id)：\(summary)\n\n请输入 确认 或 go 后再点「批准」。",
                "Approve \(id): \(summary)\n\nType 确认 or go, then click \"Approve\".")
            if mismatched {
                info += "\n" + L("上次输入不匹配。", "Previous input didn't match.")
            }
            alert.informativeText = info
            alert.addButton(withTitle: L("批准", "Approve"))
            alert.addButton(withTitle: L("取消", "Cancel"))
            let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
            field.placeholderString = L("输入 确认 或 go", "Type 确认 or go")
            alert.accessoryView = field
            alert.window.initialFirstResponder = field
            let resp = alert.runModal()
            guard resp == .alertFirstButtonReturn else { return false }
            let text = field.stringValue
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
            let ok = (text == "确认" || text == "go")
            Analytics.log(ok ? "t2_confirm_pass" : "t2_confirm_fail", fields: ["req": id])
            if ok { return true }
            NSSound.beep()
            mismatched = true
        }
    }

    // MARK: inbox write

    // merge-review 契约一: merge_apply / merge_dismiss flow through here
    // unchanged — writeInbox has no action whitelist, and applyAction carries
    // their optimistic echo (契约七). card_action analytics below covers them
    // automatically (契约八).
    func submit(id: String, action: String, comment: String?) {
        // wave 2 (契约2): the IO write must succeed BEFORE any optimistic UI —
        // on failure the card stays put and an alert explains why.
        guard writeInbox(id: id, action: action, comment: comment) else { return }
        // 契约F: the action really reached the inbox — count it (failed writes
        // above already return and must not be counted).
        Analytics.log("card_action", fields: ["action": action, "req": id])
        // instant local feedback — hide/echo/pin/comment policy is frozen in
        // DashboardStore.applyAction; this is its ONLY call site.
        store.applyAction(action, id: id)
    }

    /// Returns false when the inbox file could not be written; the caller must
    /// NOT apply optimistic UI in that case (the card must not disappear).
    private func writeInbox(id: String, action: String, comment: String?) -> Bool {
        let ts = ISO8601DateFormatter().string(from: Date())
        var dict: [String: Any] = ["id": id, "action": action, "ts": ts]
        dict["comment"] = comment ?? NSNull()
        return writeInboxFile(dict)
    }

    /// merge-review 契约一/七: 多选「请求合并建议」→
    /// {"action":"merge_review","ids":["R-xxx","R-yyy",…]} inbox file. Same
    /// atomic-write + failure-alert path as card actions (writeInboxFile); on
    /// success the involved cards get the 合并分析中… badge (store). The
    /// request itself is counted python-side (merge_review_requested, 契约八)
    /// — no Swift analytics event here.
    func submitMergeReview(ids: [String]) -> Bool {
        guard ids.count >= 2 else { return false }   // 契约一: ≥2 张卡
        let ts = ISO8601DateFormatter().string(from: Date())
        let dict: [String: Any] = ["action": "merge_review", "ids": ids, "ts": ts]
        guard writeInboxFile(dict) else { return false }
        store.beginMergeReview(ids: ids)   // 契约七: 涉及卡片盖角标
        return true
    }

    /// 建议上报入口（看板 header 直点 = ids 空 → 对整体；多选操作条 = 针对
    /// 所选卡）: 弹多行文本框（promptText 复用——↩ 发送 · ⇧↩ 换行 同款），
    /// 提交走 submitFeedback。返回 true = 已写入 inbox（调用方据此退出多选）；
    /// 取消 / 空文本 / 写失败 = false，选择保持原样。
    func promptFeedback(ids: [String]) -> Bool {
        let title = ids.isEmpty
            ? L("💡 提建议（对整体）", "💡 Send feedback (overall)")
            : L("💡 提建议（\(ids.count) 张卡）", "💡 Send feedback (\(ids.count) cards)")
        // CONTRACT §29 明示条款：内容（建议全文 + 所选卡片标题快照）会上传给
        // 维护者，且不受「产品改进计划」开关/首启 consent 限制——入口文案必须
        // 把这一点讲清楚，不得暗示是本地闭环。
        guard let text = promptText(
            title: title,
            info: L("说说哪里不对 / 可以更好。发送后，建议全文与所选卡片的标题快照会上传给维护者用于改进产品（即使你关闭了匿名统计）——请勿包含敏感信息。",
                    "What's off / could be better. On send, your feedback text and the selected cards' title snapshots are uploaded to the maintainer to improve the product (even with anonymous stats off) — avoid sensitive details."),
            placeholder: L("建议内容…", "Your feedback…")),
            !text.isEmpty
        else { return false }
        return submitFeedback(ids: ids, text: text)
    }

    /// 建议上报（照 submitMergeReview 模式）:
    /// {"action":"feedback","ids":[…],"text":…} inbox 文件 — ids sorted 保持
    /// payload 确定性，允许为空（对整体提建议）。同一 atomic-write +
    /// failure-alert 路径（writeInboxFile）；成功后乐观回显一条绿色
    /// 「已记录建议，感谢」信息条（store.noteFeedbackRecorded）。
    func submitFeedback(ids: [String], text: String) -> Bool {
        let ts = ISO8601DateFormatter().string(from: Date())
        let dict: [String: Any] = ["action": "feedback", "ids": ids.sorted(),
                                   "text": text, "ts": ts]
        guard writeInboxFile(dict) else { return false }
        Analytics.log("feedback_submit", fields: ["ids": ids.count])
        store.noteFeedbackRecorded()
        return true
    }

    /// The ONE atomic inbox write + failure alert (card actions + merge_review
    /// share it). Contract: on false the caller must NOT apply optimistic UI.
    private func writeInboxFile(_ dict: [String: Any]) -> Bool {
        let fm = FileManager.default
        do {
            try fm.createDirectory(atPath: AppPaths.inboxDir,
                                   withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: dict,
                                                  options: [.prettyPrinted, .sortedKeys])
            let path = AppPaths.inboxDir + "/" + UUID().uuidString + ".json"
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
            return true
        } catch {
            NSLog("inbox write failed: \(error.localizedDescription)")
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = L("操作未能写入", "Action Could Not Be Written")
            alert.informativeText = L(
                "写入 inbox 指令文件失败：\(error.localizedDescription)\n卡片保持原样，请稍后重试。",
                "Failed to write the inbox action file: \(error.localizedDescription)\nThe card is unchanged — please try again.")
            alert.addButton(withTitle: L("好", "OK"))
            alert.runModal()
            return false
        }
    }

    /// Quick capture (popover input): state/inbox/capture-<uuid>.json with
    /// {"action":"capture","text":…,"ts":ISO8601} — contract #4. A local grey
    /// spinner card covers the gap until actd surfaces the proposal.
    /// Item 3: a leading /rec | /open | /lang runs as a command instead.
    /// Returns true when the text was consumed (capture written or command
    /// executed); false = unrecognized/malformed slash command OR the capture
    /// inbox write failed — the caller keeps the input (correction / retry;
    /// SlashCommands.lastErrorLine distinguishes IO errors for commands).
    /// 契约F `source` 词表冻结为 popover|kanban —— 无默认值，每个调用点必须
    /// 显式传词表内的值（状态栏图标拖放归入 popover：同属菜单栏入口）。
    @discardableResult
    func submitCapture(_ text: String, source: String) -> Bool {
        if SlashCommands.isCommand(text) {
            let ok = SlashCommands.run(text, app: self)
            if ok { CaptureHistory.push(text) }   // item 5: commands count too
            return ok
        }
        let fm = FileManager.default
        do {
            try fm.createDirectory(atPath: AppPaths.inboxDir,
                                   withIntermediateDirectories: true)
            let ts = ISO8601DateFormatter().string(from: Date())
            let dict: [String: Any] = ["action": "capture", "text": text, "ts": ts]
            let data = try JSONSerialization.data(withJSONObject: dict,
                                                  options: [.prettyPrinted, .sortedKeys])
            let path = AppPaths.inboxDir + "/capture-" + UUID().uuidString + ".json"
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
            store.beginCapture(text)
            CaptureHistory.push(text)   // item 5
            // 契约F: renamed from "quick_capture"; source 词表 = popover|kanban
            Analytics.log("capture_submit", fields: ["source": source])
        } catch {
            // wave 2: surface the IO failure — the caller keeps the input and
            // shows 提交失败 (bucket A's non-command false branch).
            NSLog("capture write failed: \(error.localizedDescription)")
            return false
        }
        return true
    }

    func promptComment() -> String? {
        promptText(title: L("💬 修改方向", "💬 Comment / Change Direction"),
                   info: L("会并入需求的 plan/notes，卡片留在提案列等你批准。",
                           "Merged into the request's plan/notes; the card stays in Proposals awaiting your approval."),
                   placeholder: L("改哪里…", "What to change…"))
    }

    func promptRework() -> String? {
        let text = promptText(
            title: L("↩︎ 打回 · 追加要求", "↩︎ Send Back · Add Requirements"),
            info: L("反馈会送回原 session（上下文保留），任务回到运行中继续改。\n留空直接发送 = 让 AI 对照「怎样算办完」自查并改进。",
                    "Feedback returns to the original session (context kept); the task goes back to running.\nSend empty = the AI re-checks itself against the definition of done."),
            placeholder: L("还差什么 / 要改什么…（可留空）", "What's missing / what to change… (may be empty)"),
            allowEmpty: true)
        guard let t = text else { return nil }  // cancelled
        if t.isEmpty {
            // Zelin sent the rework without a reason — standing self-review order
            return "Zelin 打回了这次交付但没有写具体理由。请对照本需求的 definition_of_done 逐条自检："
                + "每一条是否真正达成、产出物是否在承诺的位置、质量是否达到可直接使用的程度。"
                + "找出差距，自行改进后重新交付，并用两三句话说明这次改了什么。"
        }
        return t
    }

    private func promptText(title: String, info: String, placeholder: String,
                            allowEmpty: Bool = false) -> String? {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = info + "\n" + placeholder + "\n"
            + L("↩ 发送 · ⇧↩ 换行", "↩ send · ⇧↩ newline")
        alert.addButton(withTitle: L("发送", "Send"))
        alert.addButton(withTitle: L("取消", "Cancel"))
        // multi-line editor (scrolls past ~5 lines); ⌘C/⌘V/⌘A work via the
        // Edit main menu installed at launch
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 360, height: 96))
        let tv = NSTextView(frame: scroll.bounds)
        tv.isRichText = false
        tv.font = .systemFont(ofSize: 13)
        tv.autoresizingMask = [.width]
        tv.textContainerInset = NSSize(width: 4, height: 6)
        tv.allowsUndo = true
        // item 1: Return sends (clicks 发送), Shift+Return keeps the default
        // newline — see PromptSendDelegate. Esc = cancel is untouched.
        let sendDelegate = PromptSendDelegate()
        sendDelegate.sendButton = alert.buttons.first
        tv.delegate = sendDelegate
        scroll.documentView = tv
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        alert.accessoryView = scroll
        alert.window.initialFirstResponder = tv
        // keep the delegate alive for the whole modal session
        let resp = withExtendedLifetime(sendDelegate) { alert.runModal() }
        if resp == .alertFirstButtonReturn {
            let text = tv.string.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { return allowEmpty ? "" : nil }
            return text
        }
        return nil
    }

    func copyCommand(_ task: RunningTask) {
        // state-correct command from the pipeline (attach for live, --resume for
        // done); fall back to --resume with whatever id we have.
        let cmd: String
        if let c = task.copy_cmd, !c.isEmpty {
            cmd = c
        } else if let sid = task.session_id, !sid.isEmpty {
            cmd = "claude --resume \(sid)"
        } else {
            return
        }
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(cmd, forType: .string)
    }
}

// item 7c: drop text onto the menu-bar icon = quick capture (mouse-side
// complement of ⌘L). A transparent overlay on the status
// button accepts string drags; every mouse event is forwarded to the button
// underneath so left/right-click behavior stays exactly as before. Feedback
// comes for free: submitCapture plants the grey spinner card (beginCapture).
@MainActor
private final class StatusDropView: NSView {
    weak var app: AppDelegate?

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.string])
    }

    required init?(coder: NSCoder) { fatalError("StatusDropView is code-only") }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        sender.draggingPasteboard.availableType(from: [.string]) != nil ? .copy : []
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        guard let raw = sender.draggingPasteboard.string(forType: .string) else { return false }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        // 契约F 词表只有 popover|kanban：状态栏图标拖放归入 popover（同一个
        // 菜单栏入口），避免打出词表外的 source 污染下游聚合。
        app?.submitCapture(text, source: "popover")
        return true
    }

    // clicks belong to the status button underneath — forward everything
    override func mouseDown(with event: NSEvent) { superview?.mouseDown(with: event) }
    override func mouseUp(with event: NSEvent) { superview?.mouseUp(with: event) }
    override func rightMouseDown(with event: NSEvent) { superview?.rightMouseDown(with: event) }
    override func rightMouseUp(with event: NSEvent) { superview?.rightMouseUp(with: event) }
    override func otherMouseDown(with event: NSEvent) { superview?.otherMouseDown(with: event) }
    override func otherMouseUp(with event: NSEvent) { superview?.otherMouseUp(with: event) }
}

// item 1: comment/rework dialogs — Return sends (clicks the alert's default
// button), Shift+Return inserts a newline. IME-safe by construction: while a
// pinyin composition is active, Return commits the marked text inside the
// input method and never reaches textView(_:doCommandBy:).
@MainActor
private final class PromptSendDelegate: NSObject, NSTextViewDelegate {
    weak var sendButton: NSButton?

    func textView(_ textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        guard commandSelector == #selector(NSResponder.insertNewline(_:)) else { return false }
        if NSApp.currentEvent?.modifierFlags.contains(.shift) == true {
            return false  // Shift+Return → default newline
        }
        sendButton?.performClick(nil)  // Return → send
        return true
    }
}
