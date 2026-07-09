// Utils.swift — AppPaths / Analytics / SettingsIO / Shell / Prefs / SecretsIO / linkified / RelativeTime 通用工具
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation
import Darwin  // Analytics v2: open/write/close raw fd (O_APPEND atomic lines)

// MARK: - Paths

enum AppPaths {
    static var stateRoot: String {
        let raw = ProcessInfo.processInfo.environment["AIASSISTANT_HOME"] ?? "~/Projects/zelin-ai-assistant"
        return (raw as NSString).expandingTildeInPath
    }
    static var dashboardPath: String { stateRoot + "/state/dashboard.json" }
    static var inboxDir: String { stateRoot + "/state/inbox" }
    // §15: the ONLY config file the app writes; config.load_config() merges it last.
    static var settingsOverridesPath: String { stateRoot + "/state/settings_overrides.json" }
    static var actdLogPath: String { stateRoot + "/state/actd.log" }
    static var analyticsDir: String { stateRoot + "/state/analytics" }
}

// MARK: - Analytics (append-only JSONL, mirrors act/lib/analytics.py; never throws)

enum Analytics {
    // v2: every event carries a session id (8 random chars fixed at app
    // launch — groups one run's events) and the app version.
    private static let sid: String = {
        let alphabet = Array("abcdefghijklmnopqrstuvwxyz0123456789")
        return String((0..<8).map { _ in alphabet.randomElement()! })
    }()
    private static let version: String =
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
            as? String ?? "dev"

    // v2: dedicated SERIAL queue (was: concurrent global) — events land in
    // call order and two log() calls can never interleave their writes.
    private static let queue = DispatchQueue(label: "zelin.assistant.analytics",
                                             qos: .utility)

    /// Append one event line to state/analytics/events.jsonl. Failures are
    /// swallowed — analytics must never break the app.
    static func log(_ event: String, fields: [String: Any] = [:]) {
        let dir = AppPaths.analyticsDir
        queue.async {
            var rec: [String: Any] = ["ts": Self.utcNow(), "event": event,
                                      "sid": Self.sid, "v": Self.version]
            for (k, v) in fields { rec[k] = v }
            guard JSONSerialization.isValidJSONObject(rec),
                  let data = try? JSONSerialization.data(withJSONObject: rec,
                                                         options: [.sortedKeys])
            else { return }
            var line = data
            line.append(0x0A)  // "\n"
            try? FileManager.default.createDirectory(
                atPath: dir, withIntermediateDirectories: true)
            // O_APPEND + a single write(2) per line: appends < PIPE_BUF are
            // atomic, so lines can't shear even against the Python writer.
            let fd = Darwin.open(dir + "/events.jsonl",
                                 O_WRONLY | O_APPEND | O_CREAT, 0o644)
            guard fd >= 0 else { return }
            defer { _ = Darwin.close(fd) }
            line.withUnsafeBytes { buf in
                guard let base = buf.baseAddress else { return }
                _ = Darwin.write(fd, base, buf.count)
            }
        }
    }

    private static func utcNow() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return f.string(from: Date())
    }
}

// MARK: - Settings overrides + config.yaml fallback (read side, §15)

enum SettingsIO {
    /// Read state/settings_overrides.json as a dictionary ([:] if absent/bad).
    static func readOverrides() -> [String: Any] {
        guard let data = FileManager.default.contents(atPath: AppPaths.settingsOverridesPath),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any]
        else { return [:] }
        return dict
    }

    /// Atomic write (Data.write .atomic = temp file + rename).
    static func writeOverrides(_ dict: [String: Any]) throws {
        let data = try JSONSerialization.data(
            withJSONObject: dict, options: [.prettyPrinted, .sortedKeys])
        try FileManager.default.createDirectory(
            atPath: AppPaths.stateRoot + "/state", withIntermediateDirectories: true)
        try data.write(to: URL(fileURLWithPath: AppPaths.settingsOverridesPath),
                       options: .atomic)
    }

    /// Naive line-scan of config.yaml (then config.example.yaml) for a scalar
    /// key, e.g. "obsidian_raw". Good enough for flat "key: value" lines; the
    /// app must not depend on a YAML library (single-file swiftc build).
    static func configScalar(_ key: String) -> String? {
        for file in [AppPaths.stateRoot + "/config.yaml",
                     AppPaths.stateRoot + "/config.example.yaml"] {
            guard let text = try? String(contentsOfFile: file, encoding: .utf8) else { continue }
            for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
                let line = rawLine.trimmingCharacters(in: .whitespaces)
                guard line.hasPrefix(key + ":") else { continue }
                var v = String(line.dropFirst(key.count + 1)).trimmingCharacters(in: .whitespaces)
                if v.hasPrefix("\"") {
                    // quoted value: take up to closing quote
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

    /// override key → config.yaml key → hard default. Tilde-expanded.
    static func resolvedPath(overrideKey: String, configKey: String?, fallback: String?) -> String? {
        let ov = readOverrides()
        var value: String? = ov[overrideKey] as? String
        if value == nil || value!.isEmpty, let ck = configKey {
            value = configScalar(ck)
        }
        if value == nil || value!.isEmpty { value = fallback }
        guard let v = value, !v.isEmpty else { return nil }
        return (v as NSString).expandingTildeInPath
    }
}

// MARK: - Shell helpers (used by the main window; run OFF the main actor)

enum Shell {
    /// True if `cmd` exits 0 under a login zsh (PATH as in a user terminal).
    /// Blocking — call from a background queue only.
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
    /// Blocking — call from a background queue only.
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
    /// Bool with a real default (UserDefaults.bool returns false when unset).
    static func bool(_ key: String, default def: Bool) -> Bool {
        let d = UserDefaults.standard
        return d.object(forKey: key) == nil ? def : d.bool(forKey: key)
    }

    /// String with a real default (UserDefaults.string returns nil when unset).
    static func string(_ key: String, default def: String) -> String {
        UserDefaults.standard.string(forKey: key) ?? def
    }

    /// 卡片排序 (v0.10.3 契约一) — pure UI preference, deliberately NOT in
    /// settings_overrides.json. "newest" (default) | "oldest" | "deadline".
    static var cardSortOrder: String { string("cardSortOrder", default: "newest") }
}

// MARK: - Secrets (contract: <AIASSISTANT_HOME>/config/secrets/, dir 0700 file 0600)

enum SecretsIO {
    static var dir: String { AppPaths.stateRoot + "/config/secrets" }
    // fixed file names per cross-component contract — do not rename
    static let slackFile = "slack-user-token.txt"
    static let gmailFile = "gmail-app-password.txt"
    static let anthropicFile = "anthropic-api-key.txt"

    static func path(_ name: String) -> String { dir + "/" + name }

    /// True if the file exists and holds non-whitespace content.
    static func nonEmptyFile(_ path: String) -> Bool {
        guard let data = FileManager.default.contents(atPath: path),
              let text = String(data: data, encoding: .utf8)
        else { return false }
        return !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    static func hasSecret(_ name: String) -> Bool { nonEmptyFile(path(name)) }

    /// Write one token (single line + trailing \n). Directory 0700, file 0600.
    static func save(_ name: String, token: String) throws {
        let fm = FileManager.default
        try fm.createDirectory(atPath: dir, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        // enforce perms even if the dir pre-existed with looser mode
        try? fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir)
        let line = token.trimmingCharacters(in: .whitespacesAndNewlines) + "\n"
        let p = path(name)
        try Data(line.utf8).write(to: URL(fileURLWithPath: p), options: .atomic)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: p)
    }
}

/// Detect URLs in plain text (NSDataDetector) and mark them as .link so
/// SwiftUI Text renders them clickable — Slack-style, no gesture code needed.
/// Non-URL text is returned unchanged; detector failure degrades gracefully.
func linkified(_ s: String) -> AttributedString {
    var attr = AttributedString(s)
    guard let detector = try? NSDataDetector(
        types: NSTextCheckingResult.CheckingType.link.rawValue) else { return attr }
    let full = NSRange(location: 0, length: (s as NSString).length)
    for match in detector.matches(in: s, options: [], range: full) {
        guard let url = match.url,
              let range = Range(match.range, in: attr) else { continue }
        attr[range].link = url
        attr[range].underlineStyle = .single
    }
    return attr
}

// Relative age from an ISO8601 timestamp, e.g. "3天前" / "5小时前" / "刚刚".
enum RelativeTime {
    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func since(_ isoString: String?) -> String? {
        guard let s = isoString, !s.isEmpty else { return nil }
        let date = iso.date(from: s) ?? isoFrac.date(from: s)
        guard let d = date else { return nil }
        let secs = Date().timeIntervalSince(d)
        if secs < 60 { return L("刚刚", "just now") }
        let mins = Int(secs / 60)
        if mins < 60 { return L("\(mins)分钟前", "\(mins)m ago") }
        let hours = mins / 60
        if hours < 24 { return L("\(hours)小时前", "\(hours)h ago") }
        let days = hours / 24
        return L("\(days)天前", "\(days)d ago")
    }
}
