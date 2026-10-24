// ShellBridge.swift — 页面 ⇄ 壳 的唯一通道（WKScriptMessageHandlerWithReply，
// handler 名 `zaiShell`）。页面的录制开关、权限页与设置页经它驱动壳内的
// RecordingController / PermissionsProbe / LaunchAtLogin；壳在任何状态变化时把
// 同一份快照以 `zai-shell-state` window 事件推回页面。
//
// 这是一份 wire contract（CONTRACT §61.1，add-only）：
//   request  = window.webkit.messageHandlers.zaiShell.postMessage({method, ...args})
//              → Promise<state>；未知 method / 坏参数 → reject(String)
//              对话框类方法（chooseFolder）的回执 = 快照 + add-only `dialog: {path: string|null}`
//              异步类方法（refreshRecording）的回执 = 起跑后的快照（引擎活性仍是旧值），
//              结果由随后的 `zai-shell-state` 事件带回
//   event    = window.dispatchEvent(new CustomEvent("zai-shell-state", {detail: state}))
//   state    = ShellBridge.stateSnapshot()（键名 snake_case，前端逐字镜像——防腐 #10）
// 页面只在 `window.webkit?.messageHandlers?.zaiShell` 存在时渲染壳专属控件
// （普通浏览器会话里没有壳，控件隐藏）。
// v2.0.0 退役（art.6 v2.0 条款，§61.1 tombstone，全文见 tag full）：方法 setCaptions /
// setCaptionPrefs / setBadge / probeCaptionKey，快照块 `captions` 与键 `hotkey`，命令
// `quick_capture`，以及 §61.4 的 LegacyPrefs 一次性偏好迁移。
//
// 桥本身零业务逻辑：所有语义都在被搬进来的引擎文件里（§61.3 逻辑零改动）；这里
// 只做 参数校验 → 调用 → 快照。唯一的例外是 `setRecording on:true` 前的 TCC 提示
// （RecordingActions.turnOn——原生两处 setMode「开启」按钮各自带的那一句，桥替页面统一补上）。
// 管我的法条：CONTRACT §54 / §61（§61.1 wire）。

import AppKit
import Combine
import WebKit

@MainActor
final class ShellBridge: NSObject, WKScriptMessageHandlerWithReply {
    static let handlerName = "zaiShell"
    static let eventName = "zai-shell-state"
    static let commandEventName = "zai-shell-command"

    private weak var webView: WKWebView?
    private var cancellables = Set<AnyCancellable>()
    private var pushScheduled = false

    /// Register on the WKWebViewConfiguration BEFORE the WKWebView is created
    /// (WebKit captures the handler list at creation).
    func install(into config: WKWebViewConfiguration) {
        config.userContentController.addScriptMessageHandler(
            self, contentWorld: .page, name: Self.handlerName)
    }

    /// Start pushing state changes into `webView`. `objectWillChange` fires
    /// BEFORE the property write, so the push is deferred one main-queue turn
    /// (also coalesces a burst of @Published writes into one event).
    func attach(to webView: WKWebView) {
        self.webView = webView
        RecordingController.shared.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
        LanguageStore.shared.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
        PermissionsProbe.shared.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
        RecordingSchedule.active.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
    }

    /// 壳 → 页面 的命令事件（§61.6）：菜单等原生入口向页面发一个动作。
    /// 词表：`open_page {page, anchor?}`（菜单 关于 / 设置… / 权限体检… 在页面已载入时让 SPA 自己换页，
    /// 不重载——D40，PageOpenPolicy）。`quick_capture` 在 v2.0.0 退役（§61.1 tombstone）。
    /// `completion(handled)`：页面的处理器接到命令会 preventDefault（web shellBridge.onShellCommand）——false =
    /// 没人接（文档还在加载、React 树没起来、老 web 构建没有这个词），调用方据此退回整页加载等旧路。
    func pushCommand(_ command: String, args: [String: String] = [:], completion: ((Bool) -> Void)? = nil) {
        guard let webView, let js = Self.commandScript(command, args: args) else { completion?(false); return }
        webView.evaluateJavaScript(js) { result, _ in completion?((result as? Bool) ?? false) }
    }

    /// 命令事件的 detail（纯函数，判例钉 wire 形）：`{"command": …}` + args 各键；args 不许覆盖 `command`。
    nonisolated static func commandJSON(_ command: String, args: [String: String] = [:]) -> String? {
        var body: [String: String] = args
        body["command"] = command
        guard let data = try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]),
              let json = String(data: data, encoding: .utf8) else { return nil }
        return json
    }

    /// 命令事件的 JS（纯函数）：cancelable 的 CustomEvent，表达式的值 = 有没有处理器 preventDefault（dispatchEvent 回 false
    /// = 被取消 = 页面接到了）。
    nonisolated static func commandScript(_ command: String, args: [String: String] = [:]) -> String? {
        guard let json = commandJSON(command, args: args) else { return nil }
        return "!window.dispatchEvent(new CustomEvent('\(commandEventName)', {detail: \(json), cancelable: true}))"
    }

    private func schedulePush() {
        guard !pushScheduled else { return }
        pushScheduled = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.pushScheduled = false
            self.pushState()
        }
    }

    /// Dispatch the current snapshot as a window event. Harmless while the
    /// splash/failure page is showing (no listener) or before any page loaded.
    func pushState() {
        guard let webView, let json = Self.stateJSON() else { return }
        let js = "window.dispatchEvent(new CustomEvent('\(Self.eventName)', {detail: \(json)}));"
        webView.evaluateJavaScript(js) { _, _ in }
    }

    // MARK: - snapshot（wire keys；add-only）

    static func stateSnapshot() -> [String: Any] {
        let rec = RecordingController.shared
        var recording: [String: Any] = [
            "available": true,
            "on": rec.mode != "off",
            "mode": rec.mode,
            "engine_running": rec.engineRunning,
            "note": rec.recordingNote,
            "tcc_lost": rec.tccLost,
            "screen_permission": RecordingController.hasScreenPermission(),
            "resume_mode": rec.resumeMode,
            // §61.1 追记 add-only：consent-race 自愈后的 15 s 成功句（原生 selfHealNote）；
            // 引擎死因的日志尾（原生 diagnosis.logTail——只在 engine_crashed / engine_ffmpeg_missing 非空）
            "self_heal_note": rec.selfHealNote,
            "log_tail": rec.diagnosis?.logTail ?? "",
        ]
        // §61.7 add-only：录制日程四键 + 派生 `paused`（日程开 ∧ mode != off ∧ 现在在窗外——读时算，
        // setRecording 改完 mode 的回执就已经是「按日程暂停」，不等下一拍）
        let schedule = RecordingSchedule.active
        recording["schedule"] = schedule.wireValue()
        // JSON null when healthy / off（前端按 string | null 镜像）。按日程暂停时引擎是被**故意**停的：
        // 冻结引擎的 diagnoseEngine 会把它读成 engine_crashed / engine_dead——那是误判，不投给页面
        // （§61.7：paused ⇒ diagnosis null、log_tail ""）。
        if schedule.pausedNow {
            recording["diagnosis"] = NSNull()
            recording["log_tail"] = ""
        } else {
            recording["diagnosis"] = rec.diagnosis?.failureId ?? NSNull()
        }
        let perm = PermissionsProbe.shared
        let permissions: [String: Any] = [
            "screen": perm.screen,
            "microphone": perm.microphone,
            "notifications": perm.notifications,
            // §68.13 add-only：笔记库（Documents）授权——原生 PermissionsModel.vault 的被动探针
            "vault": perm.vault,
            // §61.1 追记 add-only：屏幕录制的系统提示已经弹过一次（原生 screenRow 按钮
            // 「去授权」→「打开系统设置」的判据；PermissionsProbe.request("screen") 写它）
            "screen_requested": Prefs.bool(PermissionsProbe.screenRequestedKey, default: false),
        ]
        return [
            "recording": recording,
            "permissions": permissions,
            "launch_at_login": LaunchAtLogin.isEnabled,
            // §28 追记 add-only（D39）：向导终章「登录时自动启动」行能不能提供——壳是 /Applications（或
            // ~/Applications）里的正式安装 ∧ 有 bundle id；开发版 / bare binary 为 false，页面把行禁用并说明原因
            "launch_at_login_available": LaunchAtLogin.isAvailable,
            "language": LanguageStore.shared.lang,
        ]
    }

    static func stateJSON() -> String? {
        guard let data = try? JSONSerialization.data(
                withJSONObject: stateSnapshot(), options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { return nil }
        return text
    }

    // MARK: - requests

    static let recordingModes = ["screen", "screen_audio"]
    static let languages = ["zh", "en"]

    /// Pure request dispatcher (no WebKit types) so a swiftc harness can pin
    /// the vocabulary: returns the snapshot, or throws a `BridgeError` whose
    /// code is what the page sees as the rejection string.
    func handle(_ body: Any?) throws -> [String: Any] {
        guard let dict = body as? [String: Any],
              let method = dict["method"] as? String else {
            throw BridgeError.invalidArgs("body must be {method: string, ...}")
        }
        // 对话框类方法：回执 = 快照 + `dialog` 块（§61.1 add-only；页面 chooseFolder() 读它）
        var dialog: [String: Any]? = nil
        switch method {
        case "getState":
            break
        case "setRecording":
            guard let on = dict["on"] as? Bool else {
                throw BridgeError.invalidArgs("setRecording needs on: bool")
            }
            if on {
                let requested = dict["mode"] as? String
                    ?? RecordingController.shared.resumeMode
                guard Self.recordingModes.contains(requested) else {
                    throw BridgeError.invalidArgs(
                        "mode must be one of \(Self.recordingModes.joined(separator: "|"))")
                }
                RecordingActions.turnOn(requested)
            } else {
                RecordingActions.setMode("off")
            }
        case "refreshRecording":
            // §61.1 追记：页面要一份新鲜的录制真相（权限页 / 录制页进场）——同 5 s tick 的两步
            // （CGPreflight 自愈判定 + pgrep 活性）；回执 = 起跑后的快照，活性随后以事件推回。
            // getState 保持纯读。
            RecordingActions.refresh()
        case "restartRecording":
            // 刻意不带 TCC 守卫：原生的 restartEngine 按钮（DashboardView / Pages / Diagnostics /
            // Doctor）都不带；唯一带守卫的 restart 站点是 SetupWizard 终章「启动引擎」，它的 web
            // 半边 FinaleStep.tsx 自己先发 requestPermission(screen) 再发这条。
            RecordingController.shared.restartEngine()
        case "openScreenRecordingSettings":
            RecordingController.openScreenRecordingSettings()
        case "setLanguage":
            guard let lang = dict["lang"] as? String, Self.languages.contains(lang) else {
                throw BridgeError.invalidArgs("lang must be zh|en")
            }
            if LanguageStore.shared.lang != lang { LanguageStore.shared.lang = lang }
        case "getPermissions":
            PermissionsProbe.shared.refresh()
        case "requestPermission":
            guard let kind = dict["kind"] as? String, PermissionsProbe.kinds.contains(kind) else {
                throw BridgeError.invalidArgs("kind must be one of \(PermissionsProbe.kinds.joined(separator: "|"))")
            }
            PermissionsProbe.shared.request(kind)
        case "openPane":
            guard let pane = dict["pane"] as? String, PermissionsProbe.panes[pane] != nil else {
                throw BridgeError.invalidArgs("pane must be one of \(PermissionsProbe.panes.keys.sorted().joined(separator: "|"))")
            }
            PermissionsProbe.openPane(pane)
        case "setLaunchAtLogin":
            guard let on = dict["on"] as? Bool else {
                throw BridgeError.invalidArgs("setLaunchAtLogin needs on: bool")
            }
            if let why = LaunchAtLogin.set(on) { throw BridgeError.invalidArgs("launch at login: \(why)") }
        case "setRecordingSchedule":
            // §61.7 录制日程：全部可选键、整份先校验再落盘（任一坏值整个请求拒绝、零写入）
            try Self.applyRecordingSchedule(dict)
        case "chooseFolder":
            // §68.1 目录字段「选择…」：NSOpenPanel 只选目录、可新建；current / prompt 都可选但类型严格
            if let raw = dict["current"], !(raw is String) {
                throw BridgeError.invalidArgs("chooseFolder current must be a string")
            }
            if let raw = dict["prompt"], !(raw is String) {
                throw BridgeError.invalidArgs("chooseFolder prompt must be a string")
            }
            let picked = FolderDialog.chooseFolder(current: dict["current"] as? String ?? "",
                                                   prompt: dict["prompt"] as? String)
            dialog = ["path": picked ?? NSNull()]
        default:
            throw BridgeError.unknownMethod(method)
        }
        var snapshot = Self.stateSnapshot()
        if let dialog { snapshot["dialog"] = dialog }
        return snapshot
    }

    /// §61.7 录制日程：`enabled: bool` / `start` / `end`（严格 "HH:MM"）/ `days: [int]`（1 = 周日 … 7 = 周六，
    /// 非空）全部可选、至少一键；合并进现有日程后 start == end 也拒绝。校验全过才 `RecordingSchedule.apply`。
    static func applyRecordingSchedule(_ dict: [String: Any]) throws {
        var spec = RecordingSchedule.active.spec
        var touched = false
        if let raw = dict["enabled"] {
            guard let v = raw as? Bool else { throw BridgeError.invalidArgs("enabled must be bool") }
            spec.enabled = v
            touched = true
        }
        func clock(_ key: String) throws -> String? {
            guard let raw = dict[key] else { return nil }
            guard let v = raw as? String, RecordingScheduleSpec.minutes(v) != nil else {
                throw BridgeError.invalidArgs("\(key) must be \"HH:MM\" (00:00…23:59)")
            }
            return v
        }
        if let v = try clock("start") { spec.start = v; touched = true }
        if let v = try clock("end") { spec.end = v; touched = true }
        if let raw = dict["days"] {
            let list = raw as? [Any] ?? []
            // JSON 的 true / false 到这里是 NSNumber，`as? Int` 会把它桥成 1 / 0——类型严格：CFBoolean 不是整数
            // （反过来 `is Bool` 会把 NSNumber(1) 也判成 Bool，所以看 CF 类型而不是 Swift 类型）
            let ints = list.compactMap { item -> Int? in
                CFGetTypeID(item as CFTypeRef) == CFBooleanGetTypeID() ? nil : item as? Int
            }
            guard raw is [Any], ints.count == list.count,
                  let days = RecordingScheduleSpec.normalizedDays(ints) else {
                throw BridgeError.invalidArgs("days must be a non-empty list of weekdays 1…7 (1 = Sunday)")
            }
            spec.days = days
            touched = true
        }
        guard touched else { throw BridgeError.invalidArgs("setRecordingSchedule needs at least one of enabled|start|end|days") }
        guard RecordingScheduleSpec.minutes(spec.start) != RecordingScheduleSpec.minutes(spec.end) else {
            throw BridgeError.invalidArgs("start and end must differ")
        }
        RecordingSchedule.active.apply(spec)
    }

    // WKScriptMessageHandlerWithReply（WebKit 在主线程投递；SDK 已标 @MainActor）
    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage,
        replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void
    ) {
        do {
            replyHandler(try handle(message.body), nil)
        } catch let error as BridgeError {
            replyHandler(nil, error.code)
        } catch {
            replyHandler(nil, "INTERNAL: \(error.localizedDescription)")
        }
    }
}

/// Rejection vocabulary the page receives (the string before the colon is the
/// stable code; the remainder is a human hint, free to change).
enum BridgeError: Error {
    case unknownMethod(String)
    case invalidArgs(String)

    var code: String {
        switch self {
        case .unknownMethod(let m): return "UNKNOWN_METHOD: \(m)"
        case .invalidArgs(let why): return "INVALID_ARGS: \(why)"
        }
    }
}

// MARK: - recording actions（桥 → RecordingController 的动作缝；CONTRACT §61.1 追记）
//
// 原生两处 setMode「开启」——Onboarding.record（consent「开启」）、Permissions 状态行「开启(仅屏幕
// / 屏幕+音频)」——都是同一句：
//   `if !RecordingController.hasScreenPermission() { RecordingController.requestScreenPermission() }`
// 再 setMode。没有 TCC 授权引擎起来就立刻退出（Recording.swift autostartIfNeeded 的注释），
// 而 CGRequestScreenCaptureAccess 每个 bundle 身份只弹一次、之后 macOS 静默，所以每次
// 开都调一遍是安全的。web 移植曾把这句丢了（consent「开启」只发 setRecording）——桥在
// 这里替页面补回，页面不用知道 TCC。
// SetupWizard 终章「启动引擎」带同一句守卫但接的是 restartEngine 而非 setMode——它对应桥的
// `restartRecording`，守卫留在 web 半边（FinaleStep.tsx 先发 requestPermission(screen)），
// 桥的 restartRecording 与其余原生 restart 按钮一样不带守卫；不归这里。
// Recording.swift 是逐字节冻结的引擎副本（§61.3），缝只能长在它外面：四个执行体默认直连
// RecordingController；harness 换成假实现——真 setMode 会 pkill 机器上正跑的 screenpipe、
// 真 CGRequest 会弹系统框，两者都不许进测试。

@MainActor
enum RecordingActions {
    static var hasScreenPermission: () -> Bool = { RecordingController.hasScreenPermission() }
    static var requestScreenPermission: () -> Void = { RecordingController.requestScreenPermission() }
    static var setMode: (String) -> Void = { RecordingController.shared.setMode($0) }
    /// `refreshRecording` 的执行体：5 s tick 的两步（§61.3 启动序列），CGPreflight + pgrep 都便宜。
    static var refresh: () -> Void = {
        RecordingController.shared.pollScreenPermission()
        RecordingController.shared.refreshEngineState()
    }

    /// `setRecording {on:true}`：先补系统授权提示（缺才弹），再走引擎自己的 setMode（ffmpeg 预检 /
    /// 回滚 / analytics 都在那边，零复制）。
    static func turnOn(_ mode: String) {
        if !hasScreenPermission() { requestScreenPermission() }
        setMode(mode)
    }
}
