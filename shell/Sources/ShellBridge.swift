// ShellBridge.swift — 页面 ⇄ 壳 的唯一通道（WKScriptMessageHandlerWithReply，
// handler 名 `zaiShell`）。看板 header 的「录制」「实时字幕」两个开关经它驱动
// 壳内的 RecordingController / LiveCaptionsController；壳在任何状态变化时把
// 同一份快照以 `zai-shell-state` window 事件推回页面。
//
// 这是一份 wire contract（CONTRACT §61.1，add-only）：
//   request  = window.webkit.messageHandlers.zaiShell.postMessage({method, ...args})
//              → Promise<state>；未知 method / 坏参数 → reject(String)
//              对话框类方法（chooseFolder）的回执 = 快照 + add-only `dialog: {path: string|null}`
//   event    = window.dispatchEvent(new CustomEvent("zai-shell-state", {detail: state}))
//   state    = ShellBridge.stateSnapshot()（键名 snake_case，前端逐字镜像——防腐 #10）
// 页面只在 `window.webkit?.messageHandlers?.zaiShell` 存在时渲染这两个开关
// （普通浏览器会话里没有壳，开关隐藏）。
//
// 桥本身零业务逻辑：所有语义都在被搬进来的引擎文件里（§61.3 逻辑零改动）；这里
// 只做 参数校验 → 调用 → 快照。管我的法条：CONTRACT §54 / §61。

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
        LiveCaptionsController.shared.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
        LanguageStore.shared.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
        PermissionsProbe.shared.objectWillChange
            .sink { [weak self] _ in self?.schedulePush() }
            .store(in: &cancellables)
    }

    /// 壳 → 页面 的命令事件（§61.6）：全局快捷键等原生入口向页面发一个动作。
    func pushCommand(_ command: String) {
        guard let webView,
              let data = try? JSONSerialization.data(withJSONObject: ["command": command]),
              let json = String(data: data, encoding: .utf8) else { return }
        let js = "window.dispatchEvent(new CustomEvent('\(Self.commandEventName)', {detail: \(json)}));"
        webView.evaluateJavaScript(js) { _, _ in }
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
        let cap = LiveCaptionsController.shared
        var recording: [String: Any] = [
            "available": true,
            "on": rec.mode != "off",
            "mode": rec.mode,
            "engine_running": rec.engineRunning,
            "note": rec.recordingNote,
            "tcc_lost": rec.tccLost,
            "screen_permission": RecordingController.hasScreenPermission(),
            "resume_mode": rec.resumeMode,
        ]
        // JSON null when healthy / off（前端按 string | null 镜像）
        recording["diagnosis"] = rec.diagnosis?.failureId ?? NSNull()
        let captions: [String: Any] = [
            "available": true,
            "on": cap.enabled,
            "engine": cap.engineChoice,
            "paused": cap.paused,
            "engine_dead": cap.engineDead,
            "status_text": cap.statusText,
            "status_is_error": cap.statusIsError,
            // §68.2 字幕偏好八键（web 设置页「实时字幕」镜像；写侧 = setCaptionPrefs）
            "source": cap.source,
            "translate": cap.translateEnabled,
            "translate_direction": cap.translateDirection,
            "apple_locale": cap.appleLocale,
            "ark_model": cap.arkModel,
            "font_size": cap.fontSize,
            "opacity": cap.opacity,
        ]
        let perm = PermissionsProbe.shared
        let permissions: [String: Any] = [
            "screen": perm.screen,
            "microphone": perm.microphone,
            "notifications": perm.notifications,
            // §68.13 add-only：笔记库（Documents）授权——原生 PermissionsModel.vault 的被动探针
            "vault": perm.vault,
        ]
        return [
            "recording": recording,
            "captions": captions,
            "permissions": permissions,
            "launch_at_login": LaunchAtLogin.isEnabled,
            "hotkey": QuickCaptureHotkey.label,
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
    static let captionEngines = ["auto", "doubao", "apple"]
    static let captionSources = ["both", "mic", "system"]
    static let captionDirections = ["auto", "zh2en", "en2zh"]
    static let captionLocales = ["zh", "en"]
    static let fontSizeRange = 14.0...40.0
    static let opacityRange = 0.2...1.0

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
                RecordingController.shared.setMode(requested)
            } else {
                RecordingController.shared.setMode("off")
            }
        case "restartRecording":
            RecordingController.shared.restartEngine()
        case "openScreenRecordingSettings":
            RecordingController.openScreenRecordingSettings()
        case "setCaptions":
            guard let on = dict["on"] as? Bool else {
                throw BridgeError.invalidArgs("setCaptions needs on: bool")
            }
            LiveCaptionsController.shared.setEnabled(on)
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
        case "setCaptionPrefs":
            try Self.applyCaptionPrefs(dict)
        case "setBadge":
            guard let count = dict["count"] as? Int, count >= 0 else {
                throw BridgeError.invalidArgs("setBadge needs count: non-negative int")
            }
            DockBadge.set(count)
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

    /// §68.2 字幕偏好：全部可选键；每键先校验再写（任一坏值整个请求拒绝、零写入）。
    static func applyCaptionPrefs(_ dict: [String: Any]) throws {
        let cap = LiveCaptionsController.shared
        var writes: [() -> Void] = []
        func pick(_ key: String, _ allowed: [String], _ write: @escaping (String) -> Void) throws {
            guard let raw = dict[key] else { return }
            guard let v = raw as? String, allowed.contains(v) else {
                throw BridgeError.invalidArgs("\(key) must be one of \(allowed.joined(separator: "|"))")
            }
            writes.append { write(v) }
        }
        try pick("engine", captionEngines) { cap.engineChoice = $0 }
        try pick("source", captionSources) { cap.source = $0 }
        try pick("translate_direction", captionDirections) { cap.translateDirection = $0 }
        try pick("apple_locale", captionLocales) { cap.appleLocale = $0 }
        if let raw = dict["translate"] {
            guard let v = raw as? Bool else { throw BridgeError.invalidArgs("translate must be bool") }
            writes.append { cap.translateEnabled = v }
        }
        if let raw = dict["ark_model"] {
            guard let v = (raw as? String)?.trimmingCharacters(in: .whitespaces), !v.isEmpty, v.count <= 64 else {
                throw BridgeError.invalidArgs("ark_model must be a non-empty string (<=64)")
            }
            writes.append { cap.arkModel = v }
        }
        if let raw = dict["font_size"] {
            guard let v = Self.number(raw), fontSizeRange.contains(v) else {
                throw BridgeError.invalidArgs("font_size must be 14...40")
            }
            writes.append { cap.fontSize = v }
        }
        if let raw = dict["opacity"] {
            guard let v = Self.number(raw), opacityRange.contains(v) else {
                throw BridgeError.invalidArgs("opacity must be 0.2...1")
            }
            writes.append { cap.opacity = v }
        }
        guard !writes.isEmpty else { throw BridgeError.invalidArgs("setCaptionPrefs needs at least one pref") }
        writes.forEach { $0() }
    }

    private static func number(_ raw: Any) -> Double? {
        if let d = raw as? Double { return d }
        if let i = raw as? Int { return Double(i) }
        return nil
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

// MARK: - one-time preference seed from the retiring native app
//
// UserDefaults are per bundle id: the shell (com.zelin.ai-board) starts with
// no `recordingMode`, which P0-11 rightly treats as "no consent yet → off".
// An owner who already chose a mode / captions prefs in the native app
// (com.zelin.ai-engineer) has given that consent — carry the choices over
// ONCE so the first shell launch keeps recording the way the old app did.
// `screenTCCWasGranted` is deliberately NOT copied: the new bundle id needs
// its own Screen Recording grant (TROUBLESHOOTING「换壳后的 TCC 重授权」), and
// the shell should learn its own grant history rather than inherit a stale one.

enum LegacyPrefs {
    static let nativeSuite = "com.zelin.ai-engineer"
    static let marker = "legacyPrefsSeeded"
    static let keys = [
        "recordingMode", "lastActiveRecordingMode", "liveCaptionsEnabled",
        "captionsEngine", "captionsSource", "captionsTranslate",
        "captionsTranslateDirection", "captionsAppleLocale", "captionsArkModel",
        "captionsFontSize", "captionsOpacity",
    ]

    /// Copies `keys` that are set in the native domain and unset here; runs
    /// once per shell install (marker). Never overwrites a shell-side value.
    static func seedFromNativeAppIfNeeded(
        target: UserDefaults = .standard,
        source: UserDefaults? = UserDefaults(suiteName: nativeSuite)
    ) -> [String] {
        guard !target.bool(forKey: marker), let source else { return [] }
        var copied: [String] = []
        for key in keys where target.object(forKey: key) == nil {
            if let value = source.object(forKey: key) {
                target.set(value, forKey: key)
                copied.append(key)
            }
        }
        target.set(true, forKey: marker)
        return copied
    }
}
