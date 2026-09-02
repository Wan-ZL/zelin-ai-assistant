// ShellSupport.swift — 壳内最小原生残留（R2.2.3）的公共底座：AppPaths / Analytics /
// SettingsIO（只读）/ Shell / Prefs / SecretsIO（只读）/ FailureCatalog（引擎子集）/
// LanguageStore。
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
