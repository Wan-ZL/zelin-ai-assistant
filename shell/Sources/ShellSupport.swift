// ShellSupport.swift — 壳内最小原生残留（R2.2.3）的公共底座：AppPaths / Analytics /
// SettingsIO（只读）/ Shell / Prefs / SecretsIO（只读）/ FailureCatalog（引擎子集）/
// LanguageStore + 窗口三条纯策略（ExternalLinkPolicy / ReopenPolicy / WindowTitlePolicy，
// §54 追记；判例 shell/tests/PolicyHarness.swift）+ 主菜单纯表（MenuSpec，§54 追记「菜单 l10n」；
// 判例 shell/tests/MenuHarness.swift）。
//
// 为什么这些名字与 mac/Sources/Utils.swift、Doctor.swift、L10n.swift 完全同名：
// 录制引擎（Recording.swift）与实时字幕引擎（CaptionCore / LiveCaptions /
// CaptionOverlay）从 mac/ 搬进 shell/ 时要求**逻辑零改动**（CONTRACT §61.3），
// 它们调用的正是这组 helper。这里逐字复制被调用的那一部分（读侧），写侧
// （SettingsIO.writeOverrides / SecretsIO.save）刻意不带——壳只读配置与凭证，
// 设置页的写者是 server（§59.5 / R2.10.5）。判例：tests/test_shell_engine_mirror.py
// 钉住 FailureCatalog 句子与 act/lib/failures.py 逐字一致。
//
// 管我的法条：CONTRACT §54（薄壳）、§61（桥 + 引擎落户 shell/）、§16（analytics
// 隐私门）、§19（secrets 目录契约）、§25（失败分类句子）。

import AppKit
import SwiftUI
import Foundation
import Darwin  // Analytics: open/write/close raw fd (O_APPEND atomic lines)

// MARK: - Paths（与 mac AppPaths 同一解析顺序；CONTRACT §19）

enum AppPaths {
    /// env AIASSISTANT_HOME → home.txt 指针 → canonical 默认值。默认值与
    /// server/paths.py DEFAULT_HOME / act/lib/config._home() 逐字同一——这不是
    /// 「猜路径」（§54 只禁止为 spawn server 猜路径），而是三方共享的同一常量：
    /// 引擎只在这里读 config.yaml / secrets，读不到就按缺席处理。
    static let stateRoot: String = {
        if let env = ProcessInfo.processInfo.environment["AIASSISTANT_HOME"],
           !env.isEmpty {
            return (env as NSString).expandingTildeInPath
        }
        let pointer = ("~/Library/Application Support/ZelinAIAssistant/home.txt"
                       as NSString).expandingTildeInPath
        if let text = try? String(contentsOfFile: pointer, encoding: .utf8) {
            let home = (text.trimmingCharacters(in: .whitespacesAndNewlines)
                        as NSString).expandingTildeInPath
            var isDir: ObjCBool = false
            if !home.isEmpty,
               FileManager.default.fileExists(atPath: home, isDirectory: &isDir),
               isDir.boolValue {
                return home
            }
        }
        return ("~/Projects/zelin-ai-assistant" as NSString).expandingTildeInPath
    }()

    static var settingsOverridesPath: String { stateRoot + "/state/settings_overrides.json" }
    static var analyticsDir: String { stateRoot + "/state/analytics" }
}

// MARK: - Analytics (append-only JSONL, mirrors act/lib/analytics.py; never throws)
//
// 逐字复制自 mac/Sources/Utils.swift：录制/字幕引擎发的事件词表
// （recording_set_mode / recording_restart / recording_mode_rollback /
// recording_self_heal / recording_ffmpeg_blocked / screen_tcc_lost /
// captions_toggle / captions_autostart / feature_first_reach）要在 Mac app
// 退役后继续落 state/analytics/events.jsonl，每日循环与 insights 才不断档。

enum Analytics {
    private static let sid: String = {
        let alphabet = Array("abcdefghijklmnopqrstuvwxyz0123456789")
        return String((0..<8).map { _ in alphabet.randomElement()! })
    }()
    private static let version: String =
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
            as? String ?? "dev"

    private static let queue = DispatchQueue(label: "zelin.assistant.analytics",
                                             qos: .utility)

    /// §16 privacy gate — features.analytics。读取优先级：overrides（嵌套
    /// features 块 → 平铺 features.analytics）→ config.yaml `features:` 块 →
    /// 默认 on。fail-closed 特例镜像 act/lib/analytics.feature_gate：文件存在
    /// 但读不出/认不动 = off；键/文件不存在才落默认 on。
    static func featureEnabled() -> Bool {
        guard !SettingsIO.overridesUnparseable() else { return false }
        let ov = SettingsIO.readOverrides()
        if let f = ov["features"] as? [String: Any], let raw = f["analytics"] {
            return Self.coerceBool(raw) ?? false
        }
        if let raw = ov["features.analytics"] {
            return Self.coerceBool(raw) ?? false
        }
        switch Self.configFeaturesAnalyticsScan(
            file: AppPaths.stateRoot + "/config.yaml") {
        case .value(let raw): return Self.parseBool(raw) ?? false
        case .unreadable: return false
        case .absent: break
        }
        if case .value(let raw) = Self.configFeaturesAnalyticsScan(
            file: AppPaths.stateRoot + "/config.example.yaml") {
            return Self.parseBool(raw) ?? false
        }
        return true
    }

    private static func parseBool(_ raw: String) -> Bool? {
        let v = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if ["true", "yes", "on", "1"].contains(v) { return true }
        if ["false", "no", "off", "0"].contains(v) { return false }
        return nil
    }

    private static func coerceBool(_ raw: Any) -> Bool? {
        if let b = raw as? Bool { return b }
        if let n = raw as? Int, n == 0 || n == 1 { return n == 1 }
        if let s = raw as? String { return parseBool(s) }
        return nil
    }

    private static func valueAfterKey(_ line: String, key: String) -> String? {
        guard line.hasPrefix(key) else { return nil }
        let rest = String(line.dropFirst(key.count))
        let ws = rest.prefix(while: { $0 == " " || $0 == "\t" })
        let afterWS = String(rest.dropFirst(ws.count))
        guard afterWS.hasPrefix(":") else { return nil }
        return String(afterWS.dropFirst())
    }

    private enum ConfigScan {
        case value(String)
        case absent
        case unreadable
    }

    private static func configFeaturesAnalyticsScan(file: String) -> ConfigScan {
        guard FileManager.default.fileExists(atPath: file) else { return .absent }
        guard let text = try? String(contentsOfFile: file, encoding: .utf8)
        else { return .unreadable }
        var inBlock = false
        for rawLine in text.components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if !inBlock {
                guard let after = Self.valueAfterKey(rawLine, key: "features")
                else { continue }
                let rest = after.trimmingCharacters(in: .whitespacesAndNewlines)
                if rest.hasPrefix("{") {
                    let body = String(rest.dropFirst())
                    guard let close = body.firstIndex(of: "}") else {
                        return .unreadable
                    }
                    for pair in body[..<close].split(separator: ",") {
                        let kv = pair.split(separator: ":", maxSplits: 1)
                        guard kv.count == 2 else { continue }
                        let k = kv[0].trimmingCharacters(in: .whitespaces)
                            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                        guard k == "analytics" else { continue }
                        let v = kv[1].trimmingCharacters(in: .whitespaces)
                            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                        return .value(v)
                    }
                    return .absent
                }
                inBlock = true
                continue
            }
            if !rawLine.hasPrefix(" ") && !rawLine.hasPrefix("\t") {
                if line.isEmpty || line.hasPrefix("#") { continue }
                break
            }
            guard let after = Self.valueAfterKey(line, key: "analytics")
            else { continue }
            var v = after.trimmingCharacters(in: .whitespacesAndNewlines)
            if v.hasPrefix("\"") || v.hasPrefix("'") {
                let quote = v.first!
                let inner = String(v.dropFirst())
                v = inner.firstIndex(of: quote).map { String(inner[..<$0]) } ?? inner
            } else if v.hasPrefix("#") {
                v = ""
            } else if let hash = v.range(of: " #") {
                v = String(v[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces)
            }
            return .value(v)
        }
        return .absent
    }

    /// Append one event line to state/analytics/events.jsonl. Failures are
    /// swallowed — analytics must never break the app. Gated on
    /// features.analytics (§16).
    static func log(_ event: String, fields: [String: Any] = [:]) {
        queue.async {
            guard Self.featureEnabled() else { return }
            _ = Self.appendLine(event: event, fields: fields)
        }
    }

    static func flush() {
        queue.sync {}
    }

    private static func appendLine(event: String, fields: [String: Any]) -> Bool {
        let dir = AppPaths.analyticsDir
        var rec: [String: Any] = ["ts": Self.utcNow(), "event": event,
                                  "sid": Self.sid, "v": Self.version]
        for (k, v) in fields { rec[k] = v }
        guard JSONSerialization.isValidJSONObject(rec),
              let data = try? JSONSerialization.data(withJSONObject: rec,
                                                     options: [.sortedKeys])
        else { return false }
        var line = data
        line.append(0x0A)
        try? FileManager.default.createDirectory(
            atPath: dir, withIntermediateDirectories: true)
        let fd = Darwin.open(dir + "/events.jsonl",
                             O_WRONLY | O_APPEND | O_CREAT, 0o644)
        guard fd >= 0 else { return false }
        defer { _ = Darwin.close(fd) }
        return line.withUnsafeBytes { buf in
            guard let base = buf.baseAddress else { return false }
            return Darwin.write(fd, base, buf.count) == buf.count
        }
    }

    private static func utcNow() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return f.string(from: Date())
    }

    /// Once-per-install feature-reach marker (docs/TELEMETRY.md)。UserDefaults
    /// 键与 Mac app 同名——同一台机器换壳后里程碑不重发（两个 bundle id 的
    /// defaults 域不同，首次经壳触达仍会发一次，属可接受的迁移代价）。
    static func firstReach(_ feature: String) {
        let key = "analytics.firstReach." + feature
        queue.async {
            guard Self.featureEnabled() else { return }
            guard !UserDefaults.standard.bool(forKey: key) else { return }
            guard Self.appendLine(event: "feature_first_reach",
                                  fields: ["feature": feature]) else { return }
            UserDefaults.standard.set(true, forKey: key)
        }
    }
}

// MARK: - SettingsIO（只读子集；写者是 server，§59.5）

enum SettingsIO {
    /// Read state/settings_overrides.json as a dictionary ([:] if absent/bad).
    static func readOverrides() -> [String: Any] {
        guard let data = FileManager.default.contents(atPath: AppPaths.settingsOverridesPath),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any]
        else { return [:] }
        return dict
    }

    /// The file EXISTS but does not parse as a JSON object.
    static func overridesUnparseable() -> Bool {
        guard let data = FileManager.default.contents(atPath: AppPaths.settingsOverridesPath)
        else { return false }
        guard let obj = try? JSONSerialization.jsonObject(with: data),
              obj is [String: Any]
        else { return true }
        return false
    }

    /// Naive line-scan of config.yaml (then config.example.yaml) for a scalar key.
    static func configScalar(_ key: String) -> String? {
        for file in [AppPaths.stateRoot + "/config.yaml",
                     AppPaths.stateRoot + "/config.example.yaml"] {
            guard let text = try? String(contentsOfFile: file, encoding: .utf8) else { continue }
            for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
                let line = rawLine.trimmingCharacters(in: .whitespaces)
                guard line.hasPrefix(key + ":") else { continue }
                var v = String(line.dropFirst(key.count + 1)).trimmingCharacters(in: .whitespaces)
                if v.hasPrefix("\"") {
                    let inner = String(v.dropFirst())
                    if let end = inner.firstIndex(of: "\"") {
                        v = String(inner[..<end])
                    } else {
                        v = inner
                    }
                } else if let hash = v.range(of: " #") {
                    v = String(v[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces)
                }
                if !v.isEmpty { return v }
            }
        }
        return nil
    }

    /// Naive line-scan for a YAML block sequence (config.yaml → example fallback);
    /// nil = key absent from both files, [] = explicit `key: []`.
    static func configList(_ key: String) -> [String]? {
        for file in [AppPaths.stateRoot + "/config.yaml",
                     AppPaths.stateRoot + "/config.example.yaml"] {
            guard let text = try? String(contentsOfFile: file, encoding: .utf8) else { continue }
            var inBlock = false
            var items: [String] = []
            for rawLine in text.components(separatedBy: "\n") {
                let line = rawLine.trimmingCharacters(in: .whitespaces)
                if !inBlock {
                    guard line.hasPrefix(key + ":") else { continue }
                    var rest = String(line.dropFirst(key.count + 1)).trimmingCharacters(in: .whitespaces)
                    if let hash = rest.range(of: "#") { rest = String(rest[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces) }
                    if rest == "[]" { return [] }
                    inBlock = true
                    continue
                }
                if line.isEmpty || line.hasPrefix("#") { continue }
                guard line.hasPrefix("- ") else { break }
                var v = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                if v.hasPrefix("\"") || v.hasPrefix("'") {
                    let quote = v.first!
                    let inner = String(v.dropFirst())
                    v = inner.firstIndex(of: quote).map { String(inner[..<$0]) } ?? inner
                } else if let hash = v.range(of: " #") {
                    v = String(v[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces)
                }
                if !v.isEmpty { items.append(v) }
            }
            if inBlock { return items }
        }
        return nil
    }
}

// MARK: - Shell helpers (blocking — run OFF the main actor)

enum Shell {
    /// True if `cmd` exits 0 under a login zsh (PATH as in a user terminal).
    static func ok(_ cmd: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", cmd]
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        do { try p.run() } catch { return false }
        p.waitUntilExit()
        return p.terminationStatus == 0
    }

    /// Run an executable with args; returns (exit code, combined output tail).
    static func run(_ launchPath: String, _ args: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launchPath)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch {
            return (127, error.localizedDescription)
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let out = String(data: data, encoding: .utf8) ?? ""
        return (p.terminationStatus, String(out.suffix(400)))
    }
}

// MARK: - UserDefaults helpers

enum Prefs {
    static func bool(_ key: String, default def: Bool) -> Bool {
        let d = UserDefaults.standard
        return d.object(forKey: key) == nil ? def : d.bool(forKey: key)
    }

    static func string(_ key: String, default def: String) -> String {
        UserDefaults.standard.string(forKey: key) ?? def
    }
}

// MARK: - Secrets（只读；契约 §19：<AIASSISTANT_HOME>/config/secrets/，dir 0700 file 0600）

enum SecretsIO {
    static var dir: String { AppPaths.stateRoot + "/config/secrets" }
    // v0.36 实时字幕 BYO keys (CONTRACT §36) — 只有原生层读这两个文件
    static let volcanoSpeechFile = "volcano-speech-key.txt"
    static let volcanoArkFile = "volcano-ark-key.txt"

    static func path(_ name: String) -> String { dir + "/" + name }

    static func nonEmptyFile(_ path: String) -> Bool {
        guard let data = FileManager.default.contents(atPath: path),
              let text = String(data: data, encoding: .utf8)
        else { return false }
        return !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    static func hasSecret(_ name: String) -> Bool { nonEmptyFile(path(name)) }

    /// Trimmed secret content, nil when missing/empty.
    static func read(_ name: String) -> String? {
        guard let raw = try? String(contentsOfFile: path(name), encoding: .utf8)
        else { return nil }
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : token
    }
}

// MARK: - Failure catalog（§25 引擎子集；句子与 act/lib/failures.py 逐字一致）
//
// 只带录制引擎会引用的 id（Recording.swift 的 flashNote / postSystemNotice
// 与桥的 diagnosis 文案）。全表镜像仍在 mac/Sources/Doctor.swift；web 面的
// 其余失败句由 server 投影（§49），壳不再是第二份全表。判例：
// tests/test_shell_engine_mirror.py 逐 id 比对 plain_zh / plain_en。

enum FailureCatalog {
    static func message(_ id: String?) -> String? {
        switch id ?? "" {
        case "node_missing":
            return L("缺少 Node.js——录制引擎无法启动",
                     "Node.js is missing — the recording engine cannot start")
        case "engine_dead":
            return L("录制引擎没有在运行——屏幕内容不会被记录",
                     "The recording engine is not running — nothing on screen is being captured")
        case "engine_npm_download":
            return L("录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制",
                     "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes")
        case "engine_crashed":
            return L("录制引擎意外停了——点「重启引擎」再试；反复失败就看下面的引擎日志",
                     "The recording engine stopped unexpectedly — click Restart engine; if it keeps happening, check the engine log lines below")
        case "engine_ffmpeg_missing":
            return L("「屏幕+音频」需要 ffmpeg，这台电脑上还没有——装一个（brew install ffmpeg）或切回「仅屏幕」",
                     "Screen + Audio needs ffmpeg, which this Mac does not have — install it (brew install ffmpeg) or switch back to Screen Only")
        case "screen_tcc_lost":
            return L("「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复",
                     "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume")
        default:
            return nil
        }
    }
}

// MARK: - Language（界面语言 "zh" | "en"）
//
// 与 mac LanguageStore 同一读侧：overrides "language" 显式值优先，缺席跟随
// 系统 locale。刻意**不**做 mac 版的首启持久化——壳不写 overrides（server 是
// web 侧写者）。CaptionOverlayView 观察它以便字幕悬浮窗文案随语言重绘；桥的
// `setLanguage` 让页面把 zai.lang 同步过来（§61.1），两边看到同一种语言。

@MainActor
final class LanguageStore: ObservableObject {
    static let shared = LanguageStore()
    @Published var lang: String {
        didSet { LanguageMirror.current = lang }
    }
    private init() {
        let v: String
        if let stored = SettingsIO.readOverrides()["language"] as? String {
            v = stored == "en" ? "en" : "zh"
        } else {
            v = Self.systemDefault
        }
        lang = v
        LanguageMirror.current = v
    }

    nonisolated static var systemDefault: String {
        (Locale.preferredLanguages.first ?? "en").hasPrefix("zh") ? "zh" : "en"
    }
}

// MARK: - 窗口策略（纯函数，无 AppKit 状态；§54 追记「外链 / Dock 重开 / 标题」）
//
// main.swift 的 AppDelegate 只做「问策略 → 执行副作用」两步，判断本身住在这里，
// 好让 shell/tests/PolicyHarness.swift 不用 WKWebView / NSWindow 就能钉住每一格。

/// 外链一律交系统浏览器（原生 Pages.swift DepAction.url / Doctor.swift
/// FailureCatalog.perform 都是 `NSWorkspace.shared.open`）。WKWebView 不实现
/// WKUIDelegate 时 target=_blank / window.open 会被静默取消——壳自 §54 追记起
/// 按本策略分流：看板 SPA 留在同一个 webView，其余 http(s) / mailto 交系统处理者，
/// 别的 scheme 一律不理（allow-list，不是 deny-list）。
enum ExternalLinkPolicy {
    enum Verdict: Equatable {
        /// http://127.0.0.1|localhost|::1:<port>/ 且路径就是 `/`（看板每一页都是
        /// `?page=` query，见 web/src/route.ts）— 看板 SPA 自己，留在壳内加载。
        case board
        /// 其余 http(s)（含看板 origin 上路径不是 `/` 的 server 文件 / API 面：
        /// `/files/…` 交付物、`/api/…`、markdown 相对链接解析出的路径）与 `mailto:`
        /// — `NSWorkspace.shared.open`，系统浏览器 / 邮件客户端接手。壳只有一个
        /// webView、没有后退，永不把它导航到看板之外的页面。
        case external
        /// about:blank / javascript: / data: / blob: / file: / 自定义 scheme / 空 URL
        /// — 什么都不做。页面能发出的只有 http(s) / mailto（markdown sanitizeUrl 白名单）；
        /// 其余 scheme 交 `NSWorkspace.open` 会直接启动处理者（file:///…app、
        /// shortcuts://…），浏览器都不会不问就放行，壳更不。
        case ignore
    }

    private static let loopbackHosts: Set<String> = ["127.0.0.1", "localhost", "::1", "[::1]"]

    static func classify(_ url: URL?, port: Int) -> Verdict {
        guard let url = url, let scheme = url.scheme?.lowercased(), !scheme.isEmpty else {
            return .ignore
        }
        if scheme == "mailto" { return .external }
        guard scheme == "http" || scheme == "https" else { return .ignore }
        // origin = scheme + host + port，三者全对（ShellConfig.boardURL 是明文 http；
        // https://127.0.0.1:<port> 不是同一个 origin）**且**路径是 `/`（或空）才是
        // 看板 SPA；同 origin 的其他路径是 server 的文件 / API 面，按 external。
        let host = (url.host ?? "").lowercased()
        if scheme == "http", loopbackHosts.contains(host), (url.port ?? 80) == port,
           url.path.isEmpty || url.path == "/" {
            return .board
        }
        return .external
    }
}

/// Dock 重开只看看板窗口，不看 AppKit 的 hasVisibleWindows（原生 AppDelegate.swift
/// applicationShouldHandleReopen + MainWindowController.isWindowOpen 同义）：字幕悬浮
/// NSPanel（CaptionOverlay，orderFrontRegardless）会把 hasVisibleWindows 顶成 true，
/// 按它判 Dock 点击就成了空操作。三分：看板不在 → show；最小化 → 壳自己
/// deminiaturize（AppKit 的默认重开只在 hasVisibleWindows == false 时才还原最小化
/// 窗口——悬浮窗在场时它不会，所以不能交给它）；可见 → 什么都不做。
enum ReopenPolicy {
    enum Action: Equatable {
        case show
        case deminiaturize
        case none
    }

    static func action(boardVisible: Bool, boardMiniaturized: Bool) -> Action {
        if boardMiniaturized { return .deminiaturize }
        return boardVisible ? .none : .show
    }
}

/// 窗口标题跟随页面（原生 MainWindow.installTitleSink：标题随 section / 语言重算）：
/// 壳这半 KVO 观察 `webView.title`，页面没给标题（内嵌 splash / 空串 / 全空白）时
/// 回落产品名。
enum WindowTitlePolicy {
    static func resolve(pageTitle: String?, fallback: String) -> String {
        let trimmed = (pageTitle ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? fallback : trimmed
    }
}

// MARK: - 主菜单纯表（§54 追记「菜单 l10n」；原生 AppDelegate.installMainMenu 的壳版）
//
// 原生主菜单每个标题都走 L()，并在语言切换时整个重建（Store.swift `/lang` 与设置页保存后
// `app.installMainMenu()`——NSMenu 不观察任何东西）。壳把「菜单长什么样」抽成这张纯表，
// `MenuSpec.build` 把表装成 NSMenu（不碰 NSApp，判例不起 NSApplication 就能检查装好的项），
// main.swift 的 `installMainMenu(lang:)` 只负责挂到 NSApp、并订阅 `LanguageStore.$lang`
// 在每次切换时重装。表**显式吃 lang、不读 LanguageMirror**：@Published 在 willSet 发值，订阅
// sink 跑到时镜像（也就是 L()）还是旧语言。双语字面量与 L() 同一形式（zh, en 一对），不是
// 第二套 i18n——菜单在 server 连上之前就得在，server-owned 文案目录此刻拿不到。
// 判例 shell/tests/MenuHarness.swift 钉每个标题、键位与动作（含「没有 ⌘F / ⌘1..7 / ⌥⌘S」）
// 与装配结果（target / selector / 修饰键逐项落地）。

enum MenuSpec {
    /// 壳自己处理的动作——每个都是原生 AppDelegate 同名 @objc 方法的壳版，main.swift 逐一映射到
    /// 显式 target = AppDelegate 的 selector。
    enum ShellAction: Equatable, CaseIterable {
        /// 关于 → 看板 `?page=about`（原生 openAboutPage；**不是**系统 About 面板）
        case about
        /// 设置… ⌘, → `?page=settings`（不带 anchor：原生 openSettingsPage 落在设置页顶部；web 目录序是
        /// 显示 / 模型 / 通用，带 `anchor=general` 会滚到第三区）
        case settings
        /// 权限体检… → `?page=permissions`（原生 openPermissionsWindow；权限页文案「之后随时可从菜单
        /// 「权限体检」再打开」自此在壳里为真）
        case permissions
        /// 重新载入 ⌘R（壳独有：已在看板上 reload；还停在 splash / 失败页则重走连接序）
        case reload
        /// 聚焦捕获框 ⌘L → 推 `quick_capture`（原生 focusCaptureField；与 ⌃⌥Space 同一条路，§68.13）
        case focusCapture
    }

    /// 菜单项动作三分：壳动作（显式 target）、AppKit first-responder 链 selector（nil target，随焦点
    /// 走——webview 里的输入框吃 ⌘C/⌘V/⌘Z，窗口吃 ⌘W/⌘M/缩放，NSApp 吃 隐藏/退出）、分隔线。
    enum Action: Equatable {
        case shell(ShellAction)
        case responder(String)
        case separator
    }

    struct Item: Equatable {
        let title: String
        /// NSMenuItem.keyEquivalent：`""` = 无快捷键；大写字母 = 带 ⇧（AppKit 约定，重做 = "Z"）。
        let key: String
        /// 除 ⌘ 之外再带 ⌥——只有「隐藏其他」（⌥⌘H）。
        let option: Bool
        let action: Action

        init(_ title: String, key: String = "", option: Bool = false, action: Action) {
            self.title = title
            self.key = key
            self.option = option
            self.action = action
        }

        static let separator = Item("", action: .separator)
    }

    struct Menu: Equatable {
        /// 顶层标题；app 菜单为 `""`（AppKit 用进程名显示）。
        let title: String
        let items: [Item]
        /// 装成 `NSApp.windowsMenu`（AppKit 自动在里面列出打开的窗口）——只有「窗口」。
        let isWindowsMenu: Bool

        init(_ title: String, items: [Item], isWindowsMenu: Bool = false) {
            self.title = title
            self.items = items
            self.isWindowsMenu = isWindowsMenu
        }
    }

    /// 整张主菜单（App / 文件 / 编辑 / 显示 / 窗口），顺序与原生 installMainMenu 一致。
    /// `lang`：`"en"` → 英文，其余一律中文（L() 同一判定）。`appName` = ShellConfig.displayName。
    ///
    /// 刻意**没有**的：Find（⌘F 不被菜单截胡，落到 WKWebView 再进页面——board 自己绑了 ⌘F 搜索）；
    /// ⌘1..7 切页（归 web NavRail，同理）；⌥⌘S 折叠/展开侧栏（s4 清单 DELETE 项，owner 决策——
    /// 折叠只走 web 导航栏栏顶的折叠钮，tombstone 在 §54.4 2026-09-05 追记 (c)）。
    static func menus(lang: String, appName: String) -> [Menu] {
        func t(_ zh: String, _ en: String) -> String { lang == "en" ? en : zh }
        return [
            Menu("", items: [
                Item(t("关于 \(appName)", "About \(appName)"), action: .shell(.about)),
                .separator,
                Item(t("设置…", "Settings…"), key: ",", action: .shell(.settings)),
                Item(t("权限体检…", "Permissions Checkup…"), action: .shell(.permissions)),
                .separator,
                Item(t("隐藏 \(appName)", "Hide \(appName)"), key: "h", action: .responder("hide:")),
                Item(t("隐藏其他", "Hide Others"), key: "h", option: true,
                     action: .responder("hideOtherApplications:")),
                Item(t("全部显示", "Show All"), action: .responder("unhideAllApplications:")),
                .separator,
                Item(t("退出", "Quit"), key: "q", action: .responder("terminate:")),
            ]),
            Menu(t("文件", "File"), items: [
                Item(t("关闭窗口", "Close Window"), key: "w", action: .responder("performClose:")),
            ]),
            Menu(t("编辑", "Edit"), items: [
                Item(t("撤销", "Undo"), key: "z", action: .responder("undo:")),
                Item(t("重做", "Redo"), key: "Z", action: .responder("redo:")),
                .separator,
                Item(t("剪切", "Cut"), key: "x", action: .responder("cut:")),
                Item(t("拷贝", "Copy"), key: "c", action: .responder("copy:")),
                Item(t("粘贴", "Paste"), key: "v", action: .responder("paste:")),
                Item(t("全选", "Select All"), key: "a", action: .responder("selectAll:")),
            ]),
            Menu(t("显示", "View"), items: [
                Item(t("重新载入", "Reload"), key: "r", action: .shell(.reload)),
                .separator,
                Item(t("聚焦捕获框", "Focus Capture Field"), key: "l", action: .shell(.focusCapture)),
            ]),
            Menu(t("窗口", "Window"), items: [
                Item(t("最小化", "Minimize"), key: "m", action: .responder("performMiniaturize:")),
                Item(t("缩放", "Zoom"), action: .responder("performZoom:")),
            ], isWindowsMenu: true),
        ]
    }

    /// 把表逐项装成 NSMenu（原生 installMainMenu 的装配半边；不碰 NSApp——挂到 `NSApp.mainMenu` /
    /// `NSApp.windowsMenu` 是 main.swift 的事，所以判例不起 NSApplication 就能逐项检查）。
    /// 壳动作 `selector(action)` + 显式 `target`；AppKit 标准动作 `Selector(name)`、nil target 走
    /// first-responder 链；`option` 对两种动作一视同仁（⌥⌘ 不因为是壳动作就悄悄掉成 ⌘）。
    /// 返回主菜单与 `isWindowsMenu` 那一份（没有则 nil）。
    static func build(_ menus: [Menu], target: AnyObject,
                      selector: (ShellAction) -> Selector) -> (main: NSMenu, windows: NSMenu?) {
        let main = NSMenu()
        var windows: NSMenu?
        for spec in menus {
            let top = NSMenuItem()
            main.addItem(top)
            let menu = NSMenu(title: spec.title)
            top.submenu = menu
            for item in spec.items {
                let mi: NSMenuItem
                switch item.action {
                case .separator:
                    mi = .separator()
                case .responder(let name):
                    mi = NSMenuItem(title: item.title, action: Selector(name), keyEquivalent: item.key)
                case .shell(let action):
                    mi = NSMenuItem(title: item.title, action: selector(action), keyEquivalent: item.key)
                    mi.target = target
                }
                if item.option { mi.keyEquivalentModifierMask = [.command, .option] }
                menu.addItem(mi)
            }
            if spec.isWindowsMenu { windows = menu }
        }
        return (main, windows)
    }
}
