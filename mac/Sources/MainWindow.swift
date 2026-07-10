// MainWindow.swift — MainWindowController（单例主窗口）/ MainSection / MainNav / MainWindowView / TrashPageView
// Split from main.swift; bucket B adds: sidebar collapse (48pt icon rail) +
// drag-to-resize (160–320pt), tab memory, window title per page, pendingAnchor.

import AppKit
import SwiftUI
import Combine
import Foundation

// MARK: - Main window (§15) — singleton NSWindow; closing hides, app stays .accessory

@MainActor
final class MainWindowController: NSObject, NSWindowDelegate {
    static let shared = MainWindowController()
    private var window: NSWindow?
    private var pendingShow = false
    private var titleSubs: Set<AnyCancellable> = []

    func show() {
        if window == nil {
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 900, height: 640),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false)
            win.title = "Zelin's AI Assistant"
            // Closing must NOT deallocate (we re-show the same window) and must
            // NOT quit (no applicationShouldTerminateAfterLastWindowClosed).
            win.isReleasedWhenClosed = false
            // belt & suspenders with MainWindowView's .frame(minWidth/minHeight)
            win.contentMinSize = NSSize(width: 720, height: 480)
            win.contentViewController = NSHostingController(rootView: MainWindowView())
            // Behave as a normal window: get its own Space / Mission Control slot
            // instead of floating over whatever fullscreen app is up front.
            win.collectionBehavior = [.fullScreenPrimary, .managed]
            win.delegate = self
            // remember position/size across opens; first launch: centered
            win.setFrameAutosaveName("ZelinAIEngineerMainWindow")
            if win.frameAutosaveName.isEmpty || !win.setFrameUsingName(win.frameAutosaveName) {
                win.center()
            }
            window = win
            installTitleSink()
        }
        // Become a REGULAR app while the window is open (Dock icon, normal
        // Space handling). The policy switch needs a runloop turn — ordering
        // front before the app is actually active glues the window onto the
        // CURRENT (possibly fullscreen) Space like an accessory panel. So:
        // activate first, show the window only once activation lands.
        NSApp.setActivationPolicy(.regular)
        if NSApp.isActive {
            window?.makeKeyAndOrderFront(nil)
        } else {
            pendingShow = true
            NotificationCenter.default.addObserver(
                self, selector: #selector(appDidActivate),
                name: NSApplication.didBecomeActiveNotification, object: nil)
            NSApp.activate(ignoringOtherApps: true)
            // fallback if the activation notification races/never fires
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
                MainActor.assumeIsolated { self?.flushPendingShow() }
            }
        }
        Analytics.log("mw_open")
    }

    /// Window title follows the current page and UI language:
    /// "Zelin's AI Assistant — 任务台". receive(on: main) re-dispatches so
    /// LanguageStore's didSet (LanguageMirror) has landed before L() reads it.
    private func installTitleSink() {
        guard titleSubs.isEmpty else { return }
        MainNav.shared.$section
            .combineLatest(LanguageStore.shared.$lang)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] section, _ in
                MainActor.assumeIsolated {
                    self?.window?.title = "Zelin's AI Assistant — " + section.title
                }
            }
            .store(in: &titleSubs)
    }

    @objc private func appDidActivate() {
        MainActor.assumeIsolated { flushPendingShow() }
    }

    private func flushPendingShow() {
        guard pendingShow else { return }
        pendingShow = false
        NotificationCenter.default.removeObserver(
            self, name: NSApplication.didBecomeActiveNotification, object: nil)
        window?.makeKeyAndOrderFront(nil)
    }

    // Window closed -> back to menu-bar-only (.accessory): no Dock icon, app
    // keeps running in the background behind the status item.
    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}

enum MainSection: String, CaseIterable, Identifiable {
    // dashboard first: main window = full workbench, popover = quick preview
    // v0.10.2: trash sits right before settings (契约); the kanban keeps
    // excluding trash — this page is where deleted cards live in the window.
    case dashboard, deps, ingest, trash, settings, about
    var id: String { rawValue }
    var title: String {
        switch self {
        case .dashboard: return L("任务台", "Workbench")
        case .deps: return L("依赖检查", "Dependencies")
        case .ingest: return L("录制与 ingest", "Recording & Ingest")
        case .trash: return L("回收站", "Trash")
        case .settings: return L("设置", "Settings")
        case .about: return L("关于", "About")
        }
    }
    var icon: String {
        switch self {
        case .dashboard: return "tray.full"
        case .deps: return "checklist"
        case .ingest: return "record.circle"
        case .trash: return "trash"
        case .settings: return "gearshape"
        case .about: return "info.circle"
        }
    }
}

// Shared section selector so non-view code (e.g. deps「去设置」button) can
// switch the main-window page. Contract 3 (frozen): buckets C/D read/write
// these members but never redefine them.
@MainActor
final class MainNav: ObservableObject {
    static let shared = MainNav()

    /// current page — remembered across launches (UserDefaults "mainSection")
    @Published var section: MainSection {
        didSet { UserDefaults.standard.set(section.rawValue, forKey: "mainSection") }
    }
    /// sidebar collapsed to a 48pt icon rail (UserDefaults "sidebarCollapsed")
    @Published var sidebarCollapsed: Bool {
        didSet { UserDefaults.standard.set(sidebarCollapsed, forKey: "sidebarCollapsed") }
    }
    /// sidebar width in pt, clamped 160...320 (UserDefaults "sidebarWidth")
    @Published var sidebarWidth: Double {
        didSet { UserDefaults.standard.set(sidebarWidth, forKey: "sidebarWidth") }
    }
    /// cross-page scroll target (frozen anchor: "credentials"). Bucket C sets
    /// it (then switches section); MainWindowView scrollTo()s and resets nil.
    @Published var pendingAnchor: String?

    private init() {
        // didSet does not fire during init — no spurious UserDefaults writes.
        let d = UserDefaults.standard
        section = MainSection(rawValue: d.string(forKey: "mainSection") ?? "") ?? .dashboard
        sidebarCollapsed = Prefs.bool("sidebarCollapsed", default: false)
        let w = d.double(forKey: "sidebarWidth")
        sidebarWidth = w == 0 ? 200 : min(max(w, 160), 320)
    }

    /// Collapse/expand the sidebar. Bucket D's ⌥⌘S menu item calls this;
    /// the view animates via .animation(value: sidebarCollapsed).
    func toggleSidebar() {
        sidebarCollapsed.toggle()
        Analytics.log("mw_sidebar_toggle", fields: ["collapsed": sidebarCollapsed])
    }
}

struct MainWindowView: View {
    @ObservedObject private var nav = MainNav.shared
    // observe the UI language so the whole main window re-renders on switch
    @ObservedObject private var i18n = LanguageStore.shared
    // width while a divider drag is in flight; nil = idle (use nav.sidebarWidth).
    // Persisted to UserDefaults only in onEnded, per contract.
    @State private var dragWidth: Double? = nil
    @State private var hoveredSection: MainSection? = nil
    @State private var resizeCursorPushed = false

    private let collapsedWidth: Double = 48

    private var sidebarWidthNow: Double {
        nav.sidebarCollapsed ? collapsedWidth : (dragWidth ?? nav.sidebarWidth)
    }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: sidebarWidthNow)
                .background(Color.primary.opacity(0.03))
                .animation(.easeInOut(duration: 0.15), value: nav.sidebarCollapsed)

            Divider()
                .overlay {
                    // collapsed state ignores drag entirely (no handle, no cursor)
                    if !nav.sidebarCollapsed { dragHandle }
                }
                // keep the 8pt grab strip hit-testable above the detail pane
                .zIndex(1)

            detail
        }
        // relaxed from 900×640: split-screen / small displays need to shrink;
        // 720 wide still fits sidebar (≤320) + settings labels, and Kanban
        // lanes scroll horizontally anyway.
        .frame(minWidth: 720, minHeight: 480)
    }

    // MARK: sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 4) {
            // title row; collapsed → toggle button alone on its own row
            if nav.sidebarCollapsed {
                toggleButton
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 10)
            } else {
                HStack(spacing: 4) {
                    Text("Zelin's AI Assistant")
                        .font(.system(size: 14, weight: .semibold))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 4)
                    toggleButton
                }
                .padding(.bottom, 10)
            }
            ForEach(MainSection.allCases) { s in
                sectionRow(s)
            }
            Spacer()
        }
        .padding(.vertical, 12)
        .padding(.horizontal, nav.sidebarCollapsed ? 6 : 12)
    }

    private var toggleButton: some View {
        Button {
            nav.toggleSidebar()
        } label: {
            Image(systemName: "sidebar.leading")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
                .padding(.vertical, 4)
                .padding(.horizontal, 6)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(L("折叠/展开侧栏", "Collapse/expand sidebar"))
    }

    @ViewBuilder
    private func sectionRow(_ s: MainSection) -> some View {
        let row = Button {
            nav.section = s
        } label: {
            HStack(spacing: 8) {
                Image(systemName: s.icon)
                    .frame(width: 16)
                if !nav.sidebarCollapsed {
                    Text(s.title)
                        .font(.system(size: 13))
                    Spacer()
                }
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 8)
            .frame(maxWidth: .infinity,
                   alignment: nav.sidebarCollapsed ? .center : .leading)
            .background(
                nav.section == s
                    ? Color.accentColor.opacity(0.18)
                    : (hoveredSection == s ? Color.primary.opacity(0.06) : Color.clear))
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { inside in
            if inside { hoveredSection = s }
            else if hoveredSection == s { hoveredSection = nil }
        }
        if nav.sidebarCollapsed {
            row.help(s.title)  // icon-only rail → bilingual tooltip
        } else {
            row
        }
    }

    // MARK: divider drag handle (8pt invisible strip)

    private var dragHandle: some View {
        Color.clear
            .frame(width: 8)
            .contentShape(Rectangle())
            .onHover { inside in
                if inside {
                    if !resizeCursorPushed {
                        NSCursor.resizeLeftRight.push()
                        resizeCursorPushed = true
                    }
                } else if resizeCursorPushed {
                    NSCursor.pop()
                    resizeCursorPushed = false
                }
            }
            .gesture(
                DragGesture(minimumDistance: 1)
                    .onChanged { v in
                        guard !nav.sidebarCollapsed else { return }
                        // live width is local state only; nav.sidebarWidth
                        // (= drag start width) is untouched until onEnded
                        dragWidth = clampSidebar(nav.sidebarWidth + v.translation.width)
                    }
                    .onEnded { v in
                        if !nav.sidebarCollapsed {
                            // didSet persists to UserDefaults "sidebarWidth"
                            nav.sidebarWidth = clampSidebar(nav.sidebarWidth + v.translation.width)
                        }
                        dragWidth = nil
                        // 兜底 pop：hover-exit can be swallowed mid-drag
                        if resizeCursorPushed {
                            NSCursor.pop()
                            resizeCursorPushed = false
                        }
                    }
            )
    }

    private func clampSidebar(_ w: Double) -> Double { min(max(w, 160), 320) }

    // MARK: detail

    @ViewBuilder
    private var detail: some View {
        if nav.section == .dashboard {
            // full workbench = Jira-style kanban board (KanbanView manages
            // its own scrolling: horizontal lanes, each scrolls vertically).
            // The popover keeps the vertical DashboardView untouched.
            if let app = NSApp.delegate as? AppDelegate {
                KanbanView(store: app.store, app: app)
            }
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    Group {
                        switch nav.section {
                        case .dashboard: EmptyView()   // handled above
                        case .deps: DepsView()
                        case .ingest: IngestView()
                        case .trash:
                            // v0.10.2 回收站 page — popover 同款 TrashSectionView
                            // (search / restore / pin), same store.
                            if let app = NSApp.delegate as? AppDelegate {
                                TrashPageView(store: app.store, app: app)
                            }
                        case .settings: SettingsFormView()
                        case .about:
                            // §26: the update row observes the shared store.
                            if let app = NSApp.delegate as? AppDelegate {
                                AboutView(store: app.store)
                            }
                        }
                    }
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .onAppear { consumePendingAnchor(proxy) }
                .onChange(of: nav.pendingAnchor) { _, _ in consumePendingAnchor(proxy) }
                .onChange(of: nav.section) { _, _ in consumePendingAnchor(proxy) }
            }
        }
    }

    /// Contract 3: bucket C sets pendingAnchor (e.g. "credentials") and switches
    /// section; once the target page has rendered its .id()s we scroll there and
    /// reset the anchor to nil. The async hop lets the section switch commit first.
    private func consumePendingAnchor(_ proxy: ScrollViewProxy) {
        guard nav.pendingAnchor != nil else { return }
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                guard let anchor = nav.pendingAnchor else { return }
                withAnimation(.easeInOut(duration: 0.25)) {
                    proxy.scrollTo(anchor, anchor: .top)
                }
                nav.pendingAnchor = nil
            }
        }
    }
}

// MARK: - Trash page (v0.10.2) — main-window sidebar 回收站
//
// Thin wrapper so the page OBSERVES the store (MainWindowView itself doesn't;
// TrashSectionView takes its rows by value, so restores/pins wouldn't re-render
// without it). Reuses the popover component with startExpanded — search /
// restore / pin behave identically, one store for both surfaces.
struct TrashPageView: View {
    @ObservedObject var store: DashboardStore
    // re-render on language switch (same pattern as KanbanView)
    @ObservedObject private var i18n = LanguageStore.shared
    unowned let app: AppDelegate

    var body: some View {
        TrashSectionView(items: store.visibleTrash, count: store.visibleTrashCount,
                         pinnedLocal: store.pinnedLocal, app: app, startExpanded: true)
            // keep cards at their popover-designed width (kanban lanes are
            // 400pt too) instead of stretching across the whole window
            .frame(maxWidth: 420, alignment: .leading)
    }
}
