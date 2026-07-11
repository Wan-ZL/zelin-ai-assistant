// Permissions.swift — 首启权限与设置窗口 / 权限体检 (first-run permissions & setup)
//
// One window, two jobs (layout inspired by KeyboardHolder's permissions page):
//  1. First run (RecordingConsent.needsPrompt): a single screen-recording
//     consent question — 开启 → screen-ONLY mode ("screen"); audio stays an
//     explicit opt-in in 设置/menus (P0-11 semantics unchanged, presentation
//     upgraded from the old 3-button NSAlert) — plus the capability checklist
//     and the anonymous-usage-stats opt-out checkbox.
//  2. Anytime after: "权限体检 / Permissions checkup" (App menu, status-item
//     right-click menu, 设置 → 通用) reopens the same page with live statuses.
//
// Statuses poll every 2 s while the window is open and refresh when the
// window regains key (windowDidBecomeKey → model.refresh()). Each capability
// row = status dot + what it is + why (one plain sentence) + ONE button.

import AppKit
import SwiftUI
import Foundation
import UserNotifications
import Darwin  // open(2) probe for Full Disk Access (TCC denies with EPERM)

// MARK: - capability status

enum PermissionStatus {
    case granted
    case denied
    case unknown  // not determined / not probeable on this Mac
}

@MainActor
final class PermissionsModel: ObservableObject {
    @Published var screen: PermissionStatus = .unknown
    @Published var notifications: PermissionStatus = .unknown
    @Published var fullDisk: PermissionStatus = .unknown

    private var timer: Timer?

    func startPolling() {
        refresh()
        guard timer == nil else { return }
        // .common mode so the timer keeps firing during event tracking —
        // same rationale as the AppDelegate refresh timer.
        let t = Timer(timeInterval: 2.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func refresh() {
        screen = RecordingController.hasScreenPermission() ? .granted : .denied
        // consent-race self-heal (audit 2.2): this 2 s poll is exactly the
        // "user is in System Settings flipping the switch" window — a fresh
        // grant auto-restarts the engine (RecordingController decides).
        RecordingController.shared.pollScreenPermission()
        refreshNotifications()
        DispatchQueue.global(qos: .utility).async {
            let s = Self.probeFullDisk()
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.fullDisk = s }
            }
        }
    }

    private func refreshNotifications() {
        // UNUserNotificationCenter traps when the process runs outside a real
        // .app bundle (bare dev binary) — degrade to unknown instead.
        guard Bundle.main.bundleIdentifier != nil else {
            notifications = .unknown
            return
        }
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let s: PermissionStatus
            switch settings.authorizationStatus {
            case .authorized, .provisional: s = .granted
            case .denied: s = .denied
            default: s = .unknown  // .notDetermined
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.notifications = s }
            }
        }
    }

    /// FDA probe: actually open the Messages DB — without Full Disk Access
    /// TCC denies the open(2) with EPERM (FileManager.isReadableFile can
    /// misreport). ENOENT (no iMessage history on this Mac) = inconclusive,
    /// NOT denied.
    nonisolated private static func probeFullDisk() -> PermissionStatus {
        let path = NSHomeDirectory() + "/Library/Messages/chat.db"
        let fd = Darwin.open(path, O_RDONLY)
        if fd >= 0 {
            Darwin.close(fd)
            return .granted
        }
        return errno == ENOENT ? .unknown : .denied
    }

    // MARK: actions — one button per capability row

    /// First click: CGRequestScreenCaptureAccess (macOS only ever shows the
    /// system prompt once, and it also adds the app to the pane's list);
    /// afterwards macOS stays silent, so later clicks deep-link the pane.
    func requestScreen() {
        Analytics.log("permissions_action", fields: ["cap": "screen"])
        if !Prefs.bool("screenPermissionRequested", default: false) {
            UserDefaults.standard.set(true, forKey: "screenPermissionRequested")
            RecordingController.requestScreenPermission()
        } else {
            RecordingController.openScreenRecordingSettings()
        }
    }

    /// notDetermined → in-app system prompt; already denied → the prompt can
    /// never re-appear, deep-link the Notifications pane instead.
    func requestNotifications() {
        Analytics.log("permissions_action", fields: ["cap": "notifications"])
        guard Bundle.main.bundleIdentifier != nil else { return }
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            if settings.authorizationStatus == .notDetermined {
                UNUserNotificationCenter.current().requestAuthorization(
                    options: [.alert, .sound, .badge]) { _, _ in
                    DispatchQueue.main.async {
                        MainActor.assumeIsolated { self.refresh() }
                    }
                }
            } else {
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        Self.openPane("com.apple.preference.notifications")
                    }
                }
            }
        }
    }

    func openFullDiskPane() {
        Analytics.log("permissions_action", fields: ["cap": "full_disk"])
        Self.openPane("com.apple.preference.security?Privacy_AllFiles")
    }

    static func openPane(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:" + pane) {
            NSWorkspace.shared.open(url)
        }
    }
}

// MARK: - telemetry consent surface (marker only since v0.18)

/// First-run disclosure IO. Since v0.18 the first-run surface is a one-line
/// disclosure (TelemetryBlockView) — the on/off toggle, level picker and
/// capture_input switch all live in Settings「产品改进计划」, which writes
/// the SAME override key shape ({"telemetry": {…}}, CONTRACT §15). What
/// remains here is the consent-surface marker.
enum TelemetryConsent {
    /// Marker the Python uploader gates on (act/lib/analytics_sync, CONTRACT
    /// §15): its existence means "the consent surface was DISPLAYED at least
    /// once" — independent of any choice (telemetry.enabled in Settings
    /// controls on/off). Without it, and without an explicit telemetry
    /// config, the hourly cron sync no-ops, so a fresh install can never
    /// upload before this disclosure appeared. Content = first-shown UTC
    /// timestamp, written once.
    static func markSurfaceShown() {
        let path = AppPaths.stateRoot + "/state/telemetry_consent_shown"
        guard !FileManager.default.fileExists(atPath: path) else { return }
        try? FileManager.default.createDirectory(
            atPath: AppPaths.stateRoot + "/state",
            withIntermediateDirectories: true)
        let ts = ISO8601DateFormatter().string(from: Date()) + "\n"
        try? ts.write(toFile: path, atomically: true, encoding: .utf8)
    }
}

// MARK: - window (singleton; closing hides, app stays .accessory)

@MainActor
final class PermissionsWindowController: NSObject, NSWindowDelegate {
    static let shared = PermissionsWindowController()
    private var window: NSWindow?
    private let model = PermissionsModel()

    func show(firstRun: Bool = false) {
        if window == nil {
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 600, height: 700),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false)
            win.isReleasedWhenClosed = false
            win.contentMinSize = NSSize(width: 540, height: 480)
            win.delegate = self
            win.center()
            window = win
        }
        window?.title = L("权限体检", "Permissions Checkup")
        // rebuild the root view each show — first-run vs checkup differ, and
        // the consent block re-evaluates RecordingConsent.needsPrompt.
        window?.contentViewController = NSHostingController(
            rootView: PermissionsView(model: model, firstRun: firstRun) { [weak self] in
                self?.window?.performClose(nil)
            })
        model.startPolling()
        Analytics.log("permissions_open", fields: ["first_run": firstRun])
        // LSUIElement app: without explicit activation the window can open
        // BEHIND the frontmost app (same trap as the old consent alert).
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    /// Spec: statuses refresh when the window regains focus (e.g. the user
    /// comes back from System Settings after flipping a switch).
    func windowDidBecomeKey(_ notification: Notification) {
        model.refresh()
    }

    func windowWillClose(_ notification: Notification) {
        model.stopPolling()
        // First-run dismissal without an explicit choice counts as 暂不:
        // recording stays OFF (nothing was ever captured) and the one-time
        // consent is marked answered — same P0-11 guarantee, no nagging.
        // Recording can be enabled anytime in 设置 → 录制 or right here.
        if RecordingConsent.needsPrompt {
            RecordingConsent.record(granted: false)
        }
    }
}

// MARK: - view

struct PermissionsView: View {
    @ObservedObject var model: PermissionsModel
    @ObservedObject private var i18n = LanguageStore.shared
    let firstRun: Bool
    let close: () -> Void

    init(model: PermissionsModel, firstRun: Bool, close: @escaping () -> Void) {
        self.model = model
        self.firstRun = firstRun
        self.close = close
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                RecordingConsentSection(model: model)
                Text(L("系统权限", "System permissions"))
                    .font(.system(size: 13, weight: .semibold))
                CapabilityRowsView(model: model)
                TelemetryBlockView()
                footer
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minWidth: 540, minHeight: 480)
    }

    // MARK: header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(firstRun ? L("欢迎使用 Zelin's AI Assistant", "Welcome to Zelin's AI Assistant")
                          : L("权限体检", "Permissions Checkup"))
                .font(.system(size: 18, weight: .semibold))
            Text(L("这一页帮你把需要的系统授权一次配齐;状态实时刷新,之后随时可从菜单「权限体检」再打开。",
                   "This page sets up the system permissions in one place; statuses refresh live, and you can reopen it anytime from the menu (\"Permissions Checkup\")."))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: footer

    private var footer: some View {
        HStack(spacing: 10) {
            // the permissions window replaces the P1-5 first-launch deps pop —
            // link to the full dependency checklist instead of stacking windows
            Button(L("打开依赖检查", "Open dependency check")) {
                MainNav.shared.section = .deps
                (NSApp.delegate as? AppDelegate)?.openMainWindow(nil)
            }
            .controlSize(.small)
            Spacer()
            Button(firstRun ? L("完成", "Done") : L("关闭", "Close")) {
                close()
            }
        }
    }
}

// MARK: - shared step components (permissions checkup + setup wizard)
//
// The three blocks below were extracted verbatim from PermissionsView so the
// setup wizard (SetupWizard.swift) can embed the SAME consent semantics,
// capability rows and telemetry checkbox as standalone steps. No behavior
// change: keys (recordingConsentShown / recordingMode), probes and copy are
// untouched.

/// Recording consent (one-time question) or the live recording status row.
struct RecordingConsentSection: View {
    @ObservedObject var model: PermissionsModel
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var i18n = LanguageStore.shared

    @State private var consentPending = RecordingConsent.needsPrompt

    var body: some View {
        if consentPending {
            consentBlock
        } else {
            recordingStatusRow
        }
    }

    // MARK: first-run consent (single choice — screen-ONLY; audio stays a
    // separate opt-in in 设置 → 录制 / the recording menus)

    private var consentBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("现在开启屏幕记录吗?", "Turn on screen recording now?"))
                .font(.system(size: 14, weight: .semibold))
            Text(L(
                """
                Zelin AI Assistant 的核心功能依赖持续屏幕记录(OCR 文字识别)。

                • 采集什么:屏幕上的可见文字(OCR);密码管理器与无痕窗口默认排除
                • 去哪里:先写入本地数据库和 Obsidian vault;摘要经 claude CLI 发送到 Anthropic API 做分析
                • 保留多久:原始录屏本地保留约 1 天后自动清理;提炼后的笔记留在本地 vault
                """,
                """
                Zelin AI Assistant's core features rely on continuous screen recording (OCR text capture).

                • What is captured: visible on-screen text (OCR); password managers and private-browsing windows are excluded by default
                • Where it goes: stored locally first (database + Obsidian vault); summaries are sent to the Anthropic API via the claude CLI for analysis
                • How long it is kept: raw recordings are cleaned up locally after ~1 day; distilled notes stay in your local vault
                """))
                .font(.system(size: 12))
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Button(L("开启", "Turn On")) {
                    RecordingConsent.record(granted: true)
                    consentPending = false
                    model.refresh()
                }
                .keyboardShortcut(.defaultAction)
                Button(L("暂不", "Not Now")) {
                    RecordingConsent.record(granted: false)
                    consentPending = false
                }
                Button(L("隐私说明…", "Privacy Details…")) {
                    RecordingConsent.openPrivacyDoc()
                }
                .buttonStyle(.link)
                .font(.system(size: 11))
                Spacer()
            }
            Text(L("不录音频。语音转写(屏幕+音频)之后可在「设置 → 录制」里单独打开;这里的选择也随时可改。",
                   "No audio is recorded. Voice transcription (Screen + Audio) can be enabled separately later in Settings → Recording; this choice can be changed anytime."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: recording status (consent already answered)

    private var recordingStatusRow: some View {
        HStack(alignment: .center, spacing: 10) {
            Circle()
                .fill(rec.engineRunning ? Color.green
                      : (rec.mode != "off" ? Color.orange : Color.secondary.opacity(0.5)))
                .frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(L("屏幕记录", "Screen recording"))
                        .font(.system(size: 13, weight: .medium))
                    Text(recordingStateWord)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                Text(L("屏幕上的可见文字会被识别并整理进你的本地知识库;音频只能在「设置 → 录制」里显式打开。",
                       "Visible on-screen text is captured into your local knowledge base; audio can only be enabled explicitly in Settings → Recording."))
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if !rec.selfHealNote.isEmpty {
                    // consent-race self-heal just fired (audit 2.2)
                    Text(rec.selfHealNote)
                        .font(.system(size: 11))
                        .foregroundColor(.green)
                } else if rec.tccLost && rec.mode != "off" {
                    // audit 9.2 — the honest post-update story; the screen
                    // capability row below carries the Grant button
                    Text(FailureCatalog.message("screen_tcc_lost") ?? "")
                        .font(.system(size: 11))
                        .foregroundColor(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
            if rec.mode == "off" {
                Button(L("开启(仅屏幕)", "Turn On (screen only)")) {
                    if !RecordingController.hasScreenPermission() {
                        RecordingController.requestScreenPermission()
                    }
                    rec.setMode("screen")
                }
                .controlSize(.small)
            } else {
                Button(L("关闭", "Turn Off")) {
                    rec.setMode("off")
                }
                .controlSize(.small)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var recordingStateWord: String {
        if rec.mode == "off" { return L("已关闭", "Off") }
        if !rec.engineRunning { return L("已开启,引擎未在录制", "On — engine not recording") }
        return rec.mode == "screen_audio" ? L("录制中(屏幕+音频)", "Recording (screen + audio)")
                                          : L("录制中(仅屏幕)", "Recording (screen only)")
    }
}

/// The three capability rows (screen / notifications / full disk), live.
struct CapabilityRowsView: View {
    @ObservedObject var model: PermissionsModel
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            screenRow
            notificationsRow
            fullDiskRow
        }
    }

    // MARK: capability rows

    private var screenRow: some View {
        capabilityRow(
            status: model.screen,
            name: L("屏幕录制", "Screen Recording"),
            why: L("这是本产品的核心数据来源——没有这项授权,录制引擎启动后会立刻退出,记不到任何内容。",
                   "This is the product's core data source — without it the capture engine exits instantly and nothing gets recorded."),
            statusText: model.screen == .granted ? L("已授权", "Granted") : L("未授权", "Not granted"),
            buttonLabel: Prefs.bool("screenPermissionRequested", default: false)
                ? L("打开系统设置", "Open System Settings") : L("去授权", "Grant…"),
            showButton: model.screen != .granted) {
            model.requestScreen()
        }
    }

    private var notificationsRow: some View {
        capabilityRow(
            status: model.notifications,
            name: L("通知", "Notifications"),
            why: L("有新提案卡、任务完成或需要你输入时,用系统通知第一时间提醒你。",
                   "System notifications alert you the moment a new proposal card arrives or a task finishes / needs your input."),
            statusText: model.notifications == .granted ? L("已授权", "Granted")
                : model.notifications == .denied ? L("未授权", "Not granted")
                : L("尚未请求", "Not requested yet"),
            buttonLabel: model.notifications == .unknown
                ? L("请求权限", "Request…") : L("打开系统设置", "Open System Settings"),
            showButton: model.notifications != .granted) {
            model.requestNotifications()
        }
    }

    private var fullDiskRow: some View {
        capabilityRow(
            status: model.fullDisk,
            name: L("完全磁盘访问", "Full Disk Access"),
            why: L("仅在开启 iPhone(iMessage)联动时需要——读取「给自己发消息」线程来接收手机指令;不用该功能可跳过。",
                   "Only needed for the iPhone (iMessage) channel — it reads your \"message yourself\" thread for phone commands; skip if you don't use it."),
            statusText: model.fullDisk == .granted ? L("已授权", "Granted")
                : model.fullDisk == .denied ? L("未授权(可选)", "Not granted (optional)")
                : L("无法检测(本机暂无 iMessage 数据)", "Can't probe (no iMessage data on this Mac)"),
            buttonLabel: L("去授权", "Grant…"),
            showButton: model.fullDisk != .granted) {
            model.openFullDiskPane()
        }
    }

    private func capabilityRow(status: PermissionStatus, name: String, why: String,
                               statusText: String, buttonLabel: String,
                               showButton: Bool,
                               action: @escaping () -> Void) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Circle()
                .fill(dotColor(status))
                .frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(name)
                        .font(.system(size: 13, weight: .medium))
                    Text(statusText)
                        .font(.system(size: 11))
                        .foregroundColor(dotColor(status))
                }
                Text(why)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if showButton {
                Button(buttonLabel, action: action)
                    .controlSize(.small)
            } else {
                Text("✓")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.green)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func dotColor(_ s: PermissionStatus) -> Color {
        switch s {
        case .granted: return .green
        case .denied: return .orange
        case .unknown: return Color.secondary.opacity(0.6)
        }
    }
}

/// First-run telemetry disclosure (v0.18): one low-key honest line — stats
/// are ON by default and, per the shipped default, INCLUDE the text the
/// user types into the app (truth in labeling: the copy below must never
/// claim "no personal text" while capture_input defaults on; the honesty
/// drift-guard in tests/test_telemetry_level.py checks this file). Plus a
/// link to the Settings section that holds the full detail and the off
/// switches. Low-key but NOT hidden: the link is right here, one click
/// away. Rendering this block still writes the consent-surface marker the
/// Python uploader gates on (unchanged semantics — nothing uploads before
/// this line has been shown).
struct TelemetryBlockView: View {
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        (Text(L("匿名使用统计（含你输入的文本，每条截断 500 字）默认开启以改进产品。",
                "Anonymous usage stats (including the text you type, clipped to 500 chars each) are on by default to improve the product."))
            + Text(" ")
            + Text(L("详情与关闭在设置。", "Details & opt-out in Settings."))
                .foregroundColor(.accentColor)
                .underline())
            .font(.system(size: 11))
            .foregroundColor(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .onTapGesture {
                MainNav.shared.pendingAnchor = "telemetry"
                MainNav.shared.section = .settings
                (NSApp.delegate as? AppDelegate)?.openMainWindow(nil)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onAppear { TelemetryConsent.markSurfaceShown() }
    }
}
