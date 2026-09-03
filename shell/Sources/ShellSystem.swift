// ShellSystem.swift — 壳内其余原生残留（R2.2.3；CONTRACT §68.13 / §61.6）：
//   ShellWindow        窗口显示钩子（通知点击 / 全局快捷键 → 前置看板窗口）；
//   PermissionsProbe   TCC 三项探针（屏幕录制 / 麦克风 / 通知）+ 请求 + 系统设置深链
//                      ——原生 Permissions.swift 的 model 半边（视图半边在 web 权限体检页）；
//   LaunchAtLogin      SMAppService.mainApp（原生 Settings 通用区「登录时启动」）；
//   QuickCaptureHotkey Carbon RegisterEventHotKey 全局快捷键 ⌃⌥Space（不需要辅助功能授权），
//                      触发 = 前置窗口 + 向页面推 `zai-shell-command {command: "quick_capture"}`；
//   DockBadge          Dock 图标徽章（原生菜单栏徽章的 Dock 版，D3：等你动作的卡数由页面推来）。
// 全部只做「原生 API 调用 + 状态」，无业务逻辑（§54 薄壳）；经 ShellBridge 暴露给页面。

import AppKit
import AVFoundation
import Carbon.HIToolbox
import Combine
import ServiceManagement
import UserNotifications

// MARK: - window hook

@MainActor
enum ShellWindow {
    /// AppDelegate 在启动时装上：前置看板窗口 + activate（点通知 / 快捷键 共用）。
    static var show: (() -> Void)?
}

// MARK: - TCC probes（原生 PermissionsModel 的探针半边）

@MainActor
final class PermissionsProbe: ObservableObject {
    static let shared = PermissionsProbe()

    /// "granted" | "denied" | "unknown"（wire 词表；页面镜像 PermissionStatus）
    @Published private(set) var screen = "unknown"
    @Published private(set) var microphone = "unknown"
    @Published private(set) var notifications = "unknown"

    static let kinds = ["screen", "microphone", "notifications"]
    static let panes: [String: String] = [
        "full_disk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "notifications": "x-apple.systempreferences:com.apple.preference.notifications",
    ]

    private init() {}

    /// 同步刷新屏幕 / 麦克风；通知是异步 API，回来后单独发布（桥合并到下一次推送）。
    func refresh() {
        screen = RecordingController.hasScreenPermission() ? "granted" : "denied"
        microphone = Self.microphoneStatus()
        refreshNotifications()
    }

    nonisolated static func microphoneStatus() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "granted"
        case .denied, .restricted: return "denied"
        default: return "unknown"
        }
    }

    private func refreshNotifications() {
        // UNUserNotificationCenter traps outside a real .app bundle (bare dev binary)
        guard Bundle.main.bundleIdentifier != nil else {
            notifications = "unknown"
            return
        }
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let s: String
            switch settings.authorizationStatus {
            case .authorized, .provisional: s = "granted"
            case .denied: s = "denied"
            default: s = "unknown"
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    if self.notifications != s { self.notifications = s }
                }
            }
        }
    }

    /// 请求一项授权。屏幕：首次 CGRequestScreenCaptureAccess（系统只弹一次），之后深链面板；
    /// 麦克风：AVCaptureDevice.requestAccess（notDetermined 才会弹），否则深链；
    /// 通知：notDetermined → requestAuthorization，否则深链。
    func request(_ kind: String) {
        switch kind {
        case "screen":
            if !Prefs.bool("screenPermissionRequested", default: false) {
                UserDefaults.standard.set(true, forKey: "screenPermissionRequested")
                RecordingController.requestScreenPermission()
            } else {
                RecordingController.openScreenRecordingSettings()
            }
        case "microphone":
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                AVCaptureDevice.requestAccess(for: .audio) { _ in
                    DispatchQueue.main.async { MainActor.assumeIsolated { self.refresh() } }
                }
            } else {
                Self.openPane("microphone")
            }
        default:
            requestNotifications()
        }
        Analytics.log("permissions_action", fields: ["cap": kind])
    }

    private func requestNotifications() {
        guard Bundle.main.bundleIdentifier != nil else { return }
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            if settings.authorizationStatus == .notDetermined {
                UNUserNotificationCenter.current().requestAuthorization(
                    options: [.alert, .sound, .badge]) { _, _ in
                    DispatchQueue.main.async { MainActor.assumeIsolated { self.refresh() } }
                }
            } else {
                DispatchQueue.main.async { MainActor.assumeIsolated { Self.openPane("notifications") } }
            }
        }
    }

    /// 系统设置深链（pane ∈ panes 的键；桥已校验）。
    static func openPane(_ pane: String) {
        guard let raw = panes[pane], let url = URL(string: raw) else { return }
        NSWorkspace.shared.open(url)
    }
}

// MARK: - launch at login（SMAppService；macOS 13+）

@MainActor
enum LaunchAtLogin {
    static var isEnabled: Bool {
        guard Bundle.main.bundleIdentifier != nil else { return false }
        return SMAppService.mainApp.status == .enabled
    }

    /// 返回错误句（nil = 成功）。bare binary（无 bundle）直接说不支持。
    @discardableResult
    static func set(_ on: Bool) -> String? {
        guard Bundle.main.bundleIdentifier != nil else { return "not an app bundle" }
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            return nil
        } catch {
            return error.localizedDescription
        }
    }
}

// MARK: - global quick-capture hotkey（⌃⌥Space，Carbon；无需辅助功能授权）

@MainActor
final class QuickCaptureHotkey {
    static let shared = QuickCaptureHotkey()
    /// 人话（页面「设置 → 关于」显示）。改键 = 改这里 + register() 的 keyCode/modifiers。
    static let label = "⌃⌥Space"

    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    /// 触发回调（AppDelegate 装：前置窗口 + 推命令）
    var onFire: (() -> Void)?

    private init() {}

    func register() {
        guard ref == nil else { return }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated { QuickCaptureHotkey.shared.onFire?() }
            }
            return noErr
        }, 1, &spec, nil, &handler)
        guard status == noErr else { return }
        // signature 'zaiQ' + id 1；Space = kVK_Space (49)；⌃⌥ = controlKey | optionKey
        let hotKeyID = EventHotKeyID(signature: 0x7A61_6951, id: 1)
        RegisterEventHotKey(UInt32(kVK_Space), UInt32(controlKey | optionKey), hotKeyID,
                            GetApplicationEventTarget(), 0, &ref)
    }

    func unregister() {
        if let ref { UnregisterEventHotKey(ref) }
        ref = nil
        if let handler { RemoveEventHandler(handler) }
        handler = nil
    }
}

// MARK: - Dock badge

@MainActor
enum DockBadge {
    /// 等你动作的卡数（页面经桥 setBadge 推来；0 = 清空）。
    static func set(_ count: Int) {
        NSApp.dockTile.badgeLabel = count > 0 ? String(count) : nil
    }
}
