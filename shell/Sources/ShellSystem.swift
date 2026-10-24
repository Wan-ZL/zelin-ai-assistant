// ShellSystem.swift — 壳内其余原生残留（R2.2.3；CONTRACT §68.13 / §61.6）：
//   ShellWindow        窗口显示钩子（通知点击 → 前置主窗口）；
//   PermissionsProbe   TCC 四项探针（屏幕录制 / 麦克风 / 通知 / 笔记库 Documents）+ 请求 + 系统设置深链
//                      ——原生 Permissions.swift 的 model 半边（视图半边在 web 权限体检页）；
//   LaunchAtLogin      SMAppService.mainApp（原生 Settings 通用区「登录时启动」）；
//   FolderDialog / FileDialog  NSOpenPanel（§68.1 目录字段「选择…」/ 页面 <input type=file> 的 WKUIDelegate 落点）。
// 全部只做「原生 API 调用 + 状态」，无业务逻辑（§54 薄壳）；经 ShellBridge 暴露给页面。
// v2.0.0 退役（§61.1 / §68.13 tombstone，全文见 tag full）：QuickCaptureHotkey（⌃⌥Space）、
// DockBadge（setBadge）、CaptionKeyCheck（probeCaptionKey）。

import AppKit
import AVFoundation
import Combine
import ServiceManagement
import UniformTypeIdentifiers
import UserNotifications

// MARK: - window hook

@MainActor
enum ShellWindow {
    /// AppDelegate 在启动时装上：前置主窗口 + activate（点通知）。
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
    /// 原生 Permissions.swift 同名 UserDefaults 键：屏幕录制的系统提示已弹过一次（之后只能深链面板）；
    /// 经快照 `permissions.screen_requested` 推给页面（§61.1 追记）
    static let screenRequestedKey = "screenPermissionRequested"

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
    /// `d` 只为测试注入（缺省 = 真 UserDefaults.standard）：harness 拿一次性 suite
    /// 钉住 vaultAccessGranted 这把键的读边（§66.2 setting:prefs:vaultAccessGranted）。
    nonisolated static func probeVaultPassive(_ d: UserDefaults = .standard) -> String {
        let modeFile = AppPaths.stateRoot + "/state/vault_sync_mode"
        if let mode = try? String(contentsOfFile: modeFile, encoding: .utf8),
           mode.trimmingCharacters(in: .whitespacesAndNewlines) == "mirror" {
            return "granted"
        }
        return vaultGranted(in: d) ? "granted" : "unknown"
    }

    /// app 内成功授权过一次（读边；写边在 requestVaultAccess 的 UserDefaults.standard）。
    nonisolated static func vaultGranted(in d: UserDefaults) -> Bool {
        d.object(forKey: vaultGrantedKey) == nil ? false : d.bool(forKey: vaultGrantedKey)
    }

    /// 这次点「屏幕录制」该弹系统提示（= 还没弹过）还是只能深链面板（= 弹过了）。
    /// `d` 同样只为测试注入；写边留在 request(_:) 里的 UserDefaults.standard。
    nonisolated static func screenPromptPending(in d: UserDefaults = .standard) -> Bool {
        !(d.object(forKey: screenRequestedKey) == nil ? false : d.bool(forKey: screenRequestedKey))
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
            if Self.screenPromptPending() {
                UserDefaults.standard.set(true, forKey: Self.screenRequestedKey)
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

    /// 向导终章「登录时自动启动」行**能不能提供 / 动手**（D39，快照键 `launch_at_login_available`）：原生
    /// `Onboarding.registerLaunchAtLoginDefault` 的安装位置守卫——有 bundle id（bare binary 不算）∧ bundle 装在
    /// `/Applications/` 或 `~/Applications/` 下。从 build 目录跑的开发版不许默认注册：登录项会钉住一个临时路径。
    /// 关于区的手动开关（`set`）**不**走这道守卫（原生也不走）；这只管向导默认勾选那一下。
    static var isAvailable: Bool {
        guard Bundle.main.bundleIdentifier != nil else { return false }
        return isInstalledBundle(path: Bundle.main.bundlePath, home: NSHomeDirectory())
    }

    /// 纯判定（判例 shell/tests/LaunchHarness.swift）：`path` 以 `/Applications/` 或 `<home>/Applications/` 开头。
    /// `home` 尾部的 `/` 容忍；空 home 不会把 `/Applications/` 之外的路径放进来。
    nonisolated static func isInstalledBundle(path: String, home: String) -> Bool {
        if path.hasPrefix("/Applications/") { return true }
        var base = home
        while base.hasSuffix("/") { base.removeLast() }
        guard !base.isEmpty else { return false }
        return path.hasPrefix(base + "/Applications/")
    }

    /// 本次进程是不是 loginwindow 作为登录项拉起的（D38，LaunchPolicy 的第二个输入）：
    /// 启动 Apple Event 是 `oapp`（kAEOpenApplication）且其 `keyAEPropData` 属性是枚举
    /// `keyAELaunchedAsLogInItem`（'lgit'）——Apple 文档给登录项的判法，SMAppService.mainApp
    /// 注册的「登录时打开」项同样由 loginwindow 发这枚事件（sindresorhus/LaunchAtLogin-Modern
    /// `wasLaunchedAtLogin` 同一句）。**只能在 applicationDidFinishLaunching 里读**：那一刻
    /// `currentAppleEvent` 才是启动事件，早了是 nil、晚了是别的事件。Dock / Finder / `open`
    /// 启动的 `oapp` 不带这个属性 → false；读不到事件一律 false（宁可前置，不许误藏窗口）。
    /// 读与判分开：这里只读 `currentAppleEvent`，判定在纯函数 `isLoginItemLaunch(_:)`——
    /// 判例 shell/tests/LaunchHarness.swift 用合成的 NSAppleEventDescriptor 钉住它。
    static func launchedAsLoginItem() -> Bool {
        return isLoginItemLaunch(NSAppleEventManager.shared().currentAppleEvent)
    }

    /// 纯判定：`event` 是 `oapp` 且 `keyAEPropData` 是枚举 `keyAELaunchedAsLogInItem` → true；
    /// nil / 其他事件（`odoc` 等）/ 没有该属性 / 属性是别的枚举（`keyAELaunchedAsServiceItem`）→ false。
    nonisolated static func isLoginItemLaunch(_ event: NSAppleEventDescriptor?) -> Bool {
        guard let event = event,
              event.eventID == AEEventID(kAEOpenApplication),
              let prop = event.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))
        else { return false }
        return prop.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
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

// MARK: - file dialog（页面 `<input type="file">` 的壳侧落点；CONTRACT §54 追记 2026-09-06 / §10bis 贴图 📎，D41）
//
// macOS 的 WKWebView 不实现 WKUIDelegate `runOpenPanelWith` 就**静默禁用**文件上传（Apple 文档原话：
// "By default on macOS, file uploads are disabled if you don't implement this method."）——页面里的 📎 点了
// 什么都不发生、没有错误、没有提示。今日壳里唯一的文件输入是列顶输入框的 📎（accept="image/*" multiple，
// web/src/components/board/LaneComposer.tsx），WKOpenPanelParameters 又不公开 accept 列表，所以面板只选
// 图片文件（`imageTypes`）、多选随页面、目录永不许选；取消 → nil（WebKit 当没选）。壳零业务逻辑：选中的 URL
// 直接交回 WebKit，文件内容由页面自己读、降采样、经 POST /api/attachments 落盘（§49 追记）。
// 日后页面若添非图片的文件输入，这里的类型限制要跟着放宽——判例钉住今日的 `[.image]`。

@MainActor
enum FileDialog {
    /// 面板放行的类型：今日页面唯一的文件输入是贴图 📎，只认图片
    static let imageTypes: [UTType] = [.image]

    /// 对话框的执行体（注入缝：桥 harness 换成假实现，绝不弹真面板）。参数 = 页面是否允许多选 →
    /// 选中的 URL（取消 = nil）。
    static var runner: (Bool) -> [URL]? = { multiple in
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = multiple
        panel.allowedContentTypes = imageTypes
        guard panel.runModal() == .OK else { return nil }
        return panel.urls
    }

    static func chooseImages(multiple: Bool) -> [URL]? {
        runner(multiple)
    }
}
