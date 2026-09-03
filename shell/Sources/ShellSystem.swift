// ShellSystem.swift — 壳内其余原生残留（R2.2.3；CONTRACT §68.13 / §61.6）：
//   ShellWindow        窗口显示钩子（通知点击 / 全局快捷键 → 前置看板窗口）；
//   PermissionsProbe   TCC 四项探针（屏幕录制 / 麦克风 / 通知 / 笔记库 Documents）+ 请求 + 系统设置深链
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
    /// 笔记库（Documents）访问——原生 PermissionsModel.vault：被动探针，永不主动读 ~/Documents
    @Published private(set) var vault = "unknown"

    static let kinds = ["screen", "microphone", "notifications", "vault"]
    static let panes: [String: String] = [
        "full_disk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "notifications": "x-apple.systempreferences:com.apple.preference.notifications",
        "files_folders": "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders",
    ]
    /// 原生 Permissions.swift 同名 UserDefaults 键：一次成功的 app 内授权点击（§66.2 setting:prefs:vaultAccessGranted）
    static let vaultGrantedKey = "vaultAccessGranted"

    private init() {}

    /// 同步刷新屏幕 / 麦克风 / 笔记库；通知是异步 API，回来后单独发布（桥合并到下一次推送）。
    func refresh() {
        screen = RecordingController.hasScreenPermission() ? "granted" : "denied"
        microphone = Self.microphoneStatus()
        vault = Self.probeVaultPassive()
        refreshNotifications()
    }

    /// 被动探 Documents 授权（原生 probeVaultPassive 逐字）：GUI 里读一下 ~/Documents 本身就会触发
    /// 一次性 TCC 弹窗，弹窗必须留在按钮后面。证据两条：ingest 链只在 courier 成功拉过之后才写
    /// state/vault_sync_mode="mirror"（证明授权在 cron 里也生效），UserDefaults 记一次 app 内成功授权。
    nonisolated static func probeVaultPassive() -> String {
        let modeFile = AppPaths.stateRoot + "/state/vault_sync_mode"
        if let mode = try? String(contentsOfFile: modeFile, encoding: .utf8),
           mode.trimmingCharacters(in: .whitespacesAndNewlines) == "mirror" {
            return "granted"
        }
        return Prefs.bool(vaultGrantedKey, default: false) ? "granted" : "unknown"
    }

    /// 生效的笔记库根（= obsidian_raw 的上级），override → config.yaml → 默认，与设置页同一解析。
    nonisolated static func vaultRootPath() -> String {
        var raw = "~/Documents/Obsidian Vault/2 - raw"
        if let v = SettingsIO.readOverrides()["obsidian_raw"] as? String, !v.isEmpty {
            raw = v
        } else if let v = SettingsIO.configScalar("obsidian_raw"), !v.isEmpty {
            raw = v
        }
        return ((raw as NSString).expandingTildeInPath as NSString).deletingLastPathComponent
    }

    /// 笔记库授权：GUI 里读一次 vault 目录——macOS 弹标准的「想访问“文稿”文件夹」，授权落在壳的稳定
    /// bundle 身份上，vault-sync-helper（同一 bundle）从 cron 里永远复用（§68.13）。ENOENT 不是拒绝：
    /// 新机器上目录还没建，照 ObsidianVaultSetup 建出来——同一次 Documents 授权、同一个一次性弹窗。
    /// 已经拒绝过再点 = 深链「文件与文件夹」面板（弹窗每个身份只弹一次）。
    func requestVaultAccess() {
        let alreadyDenied = vault == "denied"
        DispatchQueue.global(qos: .userInitiated).async {
            let fm = FileManager.default
            let dir = Self.vaultRootPath()
            var ok = (try? fm.contentsOfDirectory(atPath: dir)) != nil
            if !ok, !fm.fileExists(atPath: dir) {
                ok = (try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)) != nil
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    if ok {
                        UserDefaults.standard.set(true, forKey: Self.vaultGrantedKey)
                        self.vault = "granted"
                    } else {
                        self.vault = "denied"
                        if alreadyDenied { Self.openPane("files_folders") }
                    }
                }
            }
        }
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
    /// 通知：notDetermined → requestAuthorization，否则深链；笔记库：requestVaultAccess（读一次 vault 目录）。
    func request(_ kind: String) {
        switch kind {
        case "vault":
            requestVaultAccess()
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

// MARK: - folder dialog（原生 Settings.pickFolder 的 NSOpenPanel；CONTRACT §61.1 `chooseFolder` / §68.1 目录字段）

@MainActor
enum FolderDialog {
    /// 对话框的执行体（注入缝：桥 harness 换成假实现，绝不弹真面板）。参数 (current, prompt) →
    /// 选中的路径（`$HOME` 缩回 `~`，同原生 abbreviateHome）或 nil（取消）。
    static var runner: (String, String?) -> String? = { current, prompt in
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        if let prompt, !prompt.isEmpty { panel.prompt = prompt }
        let cur = (current.trimmingCharacters(in: .whitespaces) as NSString).expandingTildeInPath
        if !cur.isEmpty, FileManager.default.fileExists(atPath: cur) {
            panel.directoryURL = URL(fileURLWithPath: cur, isDirectory: true)
        }
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return abbreviateHome(url.path)
    }

    static func chooseFolder(current: String, prompt: String?) -> String? {
        runner(current, prompt)
    }

    /// `/Users/x/Notes` → `~/Notes`（原生 Settings.abbreviateHome 同款；其它路径原样）。
    static func abbreviateHome(_ path: String) -> String {
        let home = NSHomeDirectory()
        return path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
    }
}
