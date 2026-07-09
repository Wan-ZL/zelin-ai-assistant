// Pages.swift — 主窗口页面：依赖检查（DepAction/DepRowState/DepsModel/DepsView）/ 录制与 ingest（IngestModel/IngestView）/ 关于（AboutView）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// MARK: §15.1 依赖检查 — "车跑之前轮子都得在"

enum DepAction {
    case url(String)
    case reveal(String)
    case settings   // credential rows: jump to the 设置 page (App 内管理)

    @MainActor func perform() {
        switch self {
        case .url(let u):
            if let url = URL(string: u) { NSWorkspace.shared.open(url) }
        case .reveal(let p):
            let target = FileManager.default.fileExists(atPath: p)
                ? p
                : (p as NSString).deletingLastPathComponent
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: target)])
        case .settings:
            // Contract 3: set the anchor first, then switch pages; the
            // settings page scrolls to the credentials group (id "credentials")
            // and flashes it (Settings.swift side).
            MainNav.shared.pendingAnchor = "credentials"
            MainNav.shared.section = .settings
        }
    }

    var label: String {
        switch self {
        case .url: return L("下载页", "Download")
        case .reveal: return L("显示", "Reveal")
        case .settings: return L("去设置", "Open Settings")
        }
    }
}

struct DepRowState: Identifiable {
    let id: String
    let name: String
    var ok: Bool?          // nil = still checking
    var detail: String
    let action: DepAction
}

/// One radar source's health (contract E: state/radar_health.json, written by
/// the radar's health hook). Raw strings only — display text is built in the
/// view so it follows language switches without a re-check.
struct RadarHealthRow: Identifiable {
    let id: String          // "gmail" | "slack"
    let hasData: Bool       // false = source absent from the file
    let lastOK: String?     // iso timestamp of the last successful poll
    let skipReason: String? // machine reason when it never succeeded
}

@MainActor
final class DepsModel: ObservableObject {
    @Published var rows: [DepRowState] = []
    // nil = health file missing / unreadable / bad JSON (shown as 暂无数据)
    @Published var radar: [RadarHealthRow]? = nil
    @Published var checking = false

    func check() {
        guard !checking else { return }
        checking = true
        Analytics.log("mw_deps_check")

        // Resolve configured paths on the main actor (cheap file reads),
        // then do the blocking checks (login-shell `which`, python import)
        // off the main queue.
        let vaultPath = SettingsIO.resolvedPath(
            overrideKey: "obsidian_raw", configKey: "obsidian_raw",
            fallback: "~/Documents/Obsidian Vault/2 - raw") ?? ""
        // legacy credential paths (contract #2 fallback chain: secrets file →
        // config.yaml explicit path → old default)
        let legacySlack = SettingsIO.resolvedPath(
            overrideKey: "slack_token_path", configKey: "slack_token_path",
            fallback: "~/Desktop/Keys/slack-user-token.txt") ?? ""
        let legacyGmail = SettingsIO.resolvedPath(
            overrideKey: "gmail_app_password_path", configKey: "app_password_path",
            fallback: "~/Desktop/Keys/gmail-app-password.txt") ?? ""
        let legacyAnthropic = ("~/.config/anthropic-key.txt" as NSString).expandingTildeInPath
        let secretsSlack = SecretsIO.path(SecretsIO.slackFile)
        let secretsGmail = SecretsIO.path(SecretsIO.gmailFile)
        let secretsAnthropic = SecretsIO.path(SecretsIO.anthropicFile)
        let runtimeJSON = AppPaths.stateRoot + "/config/runtime.json"
        let radarHealthPath = AppPaths.stateRoot + "/state/radar_health.json"

        DispatchQueue.global(qos: .userInitiated).async {
            let fm = FileManager.default
            var out: [DepRowState] = []
            out.append(DepRowState(
                id: "screenpipe", name: "Screenpipe",
                ok: fm.fileExists(atPath: "/Applications/Screenpipe.app"),
                detail: "/Applications/Screenpipe.app",
                action: .url("https://screenpi.pe")))
            // API-key workflow: only probe that the executable exists (GUI
            // shells often miss ~/.local/bin in PATH). Never suggests login.
            out.append(DepRowState(
                id: "claude", name: "claude CLI",
                ok: Shell.ok("command -v claude >/dev/null"
                    + " || test -x \"$HOME/.local/bin/claude\""
                    + " || test -x /opt/homebrew/bin/claude"),
                detail: L("claude 可执行文件（headless 认证走 Anthropic API key，无需登录）",
                          "claude executable (headless auth via Anthropic API key; no login needed)"),
                action: .url("https://claude.com/claude-code")))
            out.append(DepRowState(
                id: "gh", name: "gh CLI",
                ok: Shell.ok("which gh"),
                detail: L("which gh（登录 shell）", "which gh (login shell)"),
                action: .url("https://cli.github.com")))
            // PyYAML — use the interpreter install.sh pinned in config/runtime.json
            // {"python": "<abs path>"} (contract #3); fallback: plain python3.
            var python = "python3"
            if let data = fm.contents(atPath: runtimeJSON),
               let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
               let p = obj["python"] as? String, !p.isEmpty {
                python = p
            }
            let pyOK: Bool
            if python.hasPrefix("/") {
                pyOK = Shell.run(python, ["-c", "import yaml"]).0 == 0
            } else {
                pyOK = Shell.ok("\(python) -c 'import yaml'")
            }
            out.append(DepRowState(
                id: "pyyaml", name: "PyYAML",
                ok: pyOK,
                detail: "\(python) -c \"import yaml\"",
                action: .url("https://pyyaml.org")))
            out.append(DepRowState(
                id: "vault", name: "Obsidian vault",
                ok: !vaultPath.isEmpty && fm.fileExists(atPath: vaultPath),
                detail: vaultPath,
                action: .reveal(vaultPath)))
            // credentials — secrets file first, legacy path as fallback (contract #2)
            func credRow(id: String, name: String, secretsPath: String,
                         legacyPath: String) -> DepRowState {
                let hasSecret = SecretsIO.nonEmptyFile(secretsPath)
                let hasLegacy = !legacyPath.isEmpty && SecretsIO.nonEmptyFile(legacyPath)
                let suffix = hasSecret ? L("（App 内管理）", " (managed in-app)")
                    : hasLegacy ? L("（App 内管理；当前用旧路径）", " (managed in-app; using legacy path)")
                    : L("（App 内管理；未设置）", " (managed in-app; not set)")
                return DepRowState(
                    id: id, name: name,
                    ok: hasSecret || hasLegacy,
                    detail: secretsPath + suffix,
                    action: .settings)
            }
            out.append(credRow(id: "slack", name: "Slack token",
                               secretsPath: secretsSlack, legacyPath: legacySlack))
            out.append(credRow(id: "gmail", name: L("Gmail 应用密码", "Gmail app password"),
                               secretsPath: secretsGmail, legacyPath: legacyGmail))
            out.append(credRow(id: "anthropic", name: "Anthropic API key",
                               secretsPath: secretsAnthropic, legacyPath: legacyAnthropic))
            // 雷达健康 (contract E) — is the gmail/slack radar actually landing?
            let radar = Self.readRadarHealth(path: radarHealthPath)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.rows = out
                    self.radar = radar
                    self.checking = false
                }
            }
        }
    }

    /// nil = file missing / unreadable / bad JSON. Tolerant by contract E —
    /// this must never crash a dependencies check.
    nonisolated private static func readRadarHealth(path: String) -> [RadarHealthRow]? {
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return nil }
        return ["gmail", "slack"].map { key in
            let d = obj[key] as? [String: Any]
            return RadarHealthRow(
                id: key,
                hasData: d != nil,
                lastOK: d?["last_ok"] as? String,
                skipReason: d?["skip_reason"] as? String)
        }
    }
}

struct DepsView: View {
    @StateObject private var model = DepsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L("依赖检查", "Dependencies"))
                    .font(.system(size: 18, weight: .semibold))
                Spacer()
                Button(model.checking ? L("检查中…", "Checking…") : L("重新检查", "Re-check")) { model.check() }
                    .disabled(model.checking)
            }
            Text(L("车跑之前轮子都得在。", "All wheels must be on before the car runs."))
                .font(.system(size: 12))
                .foregroundColor(.secondary)

            if model.rows.isEmpty {
                Text(model.checking ? L("检查中…", "Checking…")
                                    : L("点「重新检查」开始", "Click \"Re-check\" to start"))
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            }

            ForEach(model.rows) { row in
                HStack(alignment: .center, spacing: 10) {
                    Text(row.ok == true ? "✅" : "⚠️")
                        .font(.system(size: 14))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.name)
                            .font(.system(size: 13, weight: .medium))
                        Text(row.detail)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    Spacer()
                    Button(row.action.label) { row.action.perform() }
                        .controlSize(.small)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.03))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            // 雷达健康 (contract E) — freshness of the gmail/slack radar polls
            Text(L("雷达健康", "Radar Health"))
                .font(.system(size: 13, weight: .semibold))
                .padding(.top, 6)
            if let radar = model.radar {
                ForEach(radar) { row in
                    HStack(alignment: .center, spacing: 10) {
                        Circle()
                            .fill(radarColor(row))
                            .frame(width: 8, height: 8)
                        Text(row.id == "gmail" ? "Gmail" : "Slack")
                            .font(.system(size: 13, weight: .medium))
                        Text(radarDetail(row))
                            .font(.system(size: 12))
                            .foregroundColor(radarColor(row))
                        Spacer()
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.primary.opacity(0.03))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            } else {
                Text(L("暂无数据", "No data yet"))
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            }
        }
        .onAppear { model.check() }
        // item 7: model-produced strings (detail suffixes) are baked at check
        // time — re-run on language switch so rows don't linger in the old one.
        .onChange(of: i18n.lang) { _, _ in model.check() }
    }

    // green = has succeeded; orange = never, with a known reason; red = never,
    // cause unknown; gray = source not in the file yet.
    private func radarColor(_ row: RadarHealthRow) -> Color {
        if !row.hasData { return .secondary }
        if row.lastOK?.isEmpty == false { return .green }
        return row.skipReason?.isEmpty == false ? .orange : .red
    }

    private func radarDetail(_ row: RadarHealthRow) -> String {
        guard row.hasData else { return L("暂无数据", "No data yet") }
        if let ok = row.lastOK, !ok.isEmpty {
            // unparseable timestamp degrades to the raw string — still useful
            return L("最近成功 ", "last ok ") + (RelativeTime.since(ok) ?? ok)
        }
        var s = L("从未成功", "never succeeded")
        if let reason = row.skipReason, !reason.isEmpty {
            s += L("：", ": ") + Self.humanSkipReason(reason)
        }
        return s
    }

    /// Machine skip_reason → 人话 (unknown codes pass through verbatim).
    private static func humanSkipReason(_ r: String) -> String {
        switch r {
        case "no_credentials": return L("未配置凭证", "no credentials")
        case "auth_failed", "auth_error", "invalid_credentials":
            return L("凭证无效", "invalid credentials")
        case "network_error", "timeout": return L("网络错误", "network error")
        // 契约E 词表第三项：radar 连不上 / auth.test 校验失败也归这个 code。
        case "connect_failed": return L("连接失败", "connection failed")
        case "disabled": return L("已禁用", "disabled")
        default: return r
        }
    }
}

// MARK: §15.2 录制与 ingest

@MainActor
final class IngestModel: ObservableObject {
    @Published var exportStatus = ""
    @Published var ingestStatus = ""
    @Published var unprocessedLabel = "—"
    @Published var actdLogLabel = "—"
    @Published var dbLabel = L("无数据", "No data")   // ~/.screenpipe/db.sqlite mtime
    @Published var exportRunning = false
    @Published var ingestRunning = false
    // item 6 (Pages side): success feedback auto-fades after 2.5s (asyncAfter
    // + generation guard, per the `copied` precedent); failure text stays so
    // the error tail remains readable. Every status write bumps the gen so a
    // pending fade can never wipe a newer message.
    private var exportFadeGen = 0
    private var ingestFadeGen = 0

    private func setExportStatus(_ s: String, fade: Bool = false) {
        exportFadeGen += 1
        exportStatus = s
        guard fade else { return }
        let gen = exportFadeGen
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            MainActor.assumeIsolated {
                if self.exportFadeGen == gen { self.exportStatus = "" }
            }
        }
    }

    private func setIngestStatus(_ s: String, fade: Bool = false) {
        ingestFadeGen += 1
        ingestStatus = s
        guard fade else { return }
        let gen = ingestFadeGen
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            MainActor.assumeIsolated {
                if self.ingestFadeGen == gen { self.ingestStatus = "" }
            }
        }
    }

    func refreshLabels() {
        let vaultRaw = SettingsIO.resolvedPath(
            overrideKey: "obsidian_raw", configKey: "obsidian_raw",
            fallback: "~/Documents/Obsidian Vault/2 - raw")
        let unprocessedDir: String
        if let raw = vaultRaw {
            unprocessedDir = (raw as NSString).deletingLastPathComponent + "/1 - unprocessed"
        } else {
            unprocessedDir = ("~/Documents/Obsidian Vault/1 - unprocessed" as NSString).expandingTildeInPath
        }
        let logPath = AppPaths.actdLogPath
        let dbPath = ("~/.screenpipe/db.sqlite" as NSString).expandingTildeInPath
        DispatchQueue.global(qos: .userInitiated).async {
            let newest = Self.newestMTime(inDir: unprocessedDir)
            let logM = Self.mtime(of: logPath)
            let dbM = Self.mtime(of: dbPath)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.unprocessedLabel = newest.map(Self.fmt) ?? L("无文件", "No files")
                    self.actdLogLabel = logM.map(Self.fmt) ?? L("无日志", "No log")
                    self.dbLabel = dbM.map { L("最近写入 ", "Last write ") + Self.hm($0) }
                        ?? L("无数据", "No data")
                }
            }
        }
    }

    func runExport() {
        guard !exportRunning else { return }
        exportRunning = true
        setExportStatus(L("运行中…", "Running…"))
        Analytics.log("mw_export_now")
        let t0 = Date()  // contract F: secs = click → callback
        let script = AppPaths.stateRoot + "/ingest/screenpipe-export.sh"
        runAsync("/bin/bash", [script]) { code, tail in
            self.exportRunning = false
            Analytics.log("mw_export_result", fields: [
                "code": Int(code),
                "secs": (Date().timeIntervalSince(t0) * 10).rounded() / 10])
            if code == 0 {
                self.setExportStatus(L("完成 ✓", "Done ✓"), fade: true)
            } else {
                self.setExportStatus(
                    L("失败 (exit \(code)) ", "Failed (exit \(code)) ") + tail.suffix(120))
            }
            self.refreshLabels()
        }
    }

    func runIngest() {
        guard !ingestRunning else { return }
        ingestRunning = true
        setIngestStatus(L("运行中…", "Running…"))
        Analytics.log("mw_ingest_now")
        let t0 = Date()  // contract F: secs = click → callback
        let script = AppPaths.stateRoot + "/ingest/process-screenpipe.sh"
        // Manual click: no export is racing us, so skip the script's 90s
        // partial-write guard (SCREENPIPE_NO_WAIT=1).
        runAsync("/bin/bash", ["-c", "SCREENPIPE_NO_WAIT=1 exec \"$0\"", script]) { code, tail in
            self.ingestRunning = false
            // contract F: result event fires for every exit code, incl. 3 (skip)
            Analytics.log("mw_ingest_result", fields: [
                "code": Int(code),
                "secs": (Date().timeIntervalSince(t0) * 10).rounded() / 10])
            if code == 0 {
                self.setIngestStatus(L("完成 ✓", "Done ✓"), fade: true)
            } else if code == 3 {
                // exit 3 = another ingest holds the lock (usually the cron run)
                Analytics.log("mw_ingest_skipped")
                self.setIngestStatus(
                    L("已有 ingest 在运行，本次跳过", "Already running — skipped"), fade: true)
            } else {
                self.setIngestStatus(
                    L("失败 (exit \(code)) ", "Failed (exit \(code)) ") + tail.suffix(120))
            }
            self.refreshLabels()
        }
    }

    /// Launch off-main, hop back with (exit code, output tail).
    private func runAsync(_ launchPath: String, _ args: [String],
                          done: @escaping @MainActor (Int32, String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let (code, tail) = Shell.run(launchPath, args)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    done(code, tail)
                }
            }
        }
    }

    // nonisolated: pure filesystem reads, safe to call off the main actor
    // (invoked from a background Process-completion context).
    nonisolated private static func mtime(of path: String) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: path))?[.modificationDate] as? Date
    }

    nonisolated private static func newestMTime(inDir dir: String) -> Date? {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: dir) else { return nil }
        var best: Date?
        for n in names where !n.hasPrefix(".") {
            guard let d = mtime(of: dir + "/" + n) else { continue }
            if best == nil || d > best! { best = d }
        }
        return best
    }

    private static func fmt(_ d: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f.string(from: d)
    }

    private static func hm(_ d: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f.string(from: d)
    }
}

struct IngestView: View {
    @StateObject private var model = IngestModel()
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var i18n = LanguageStore.shared

    // 契约4 recording terms — in-page status line uses the bare words
    // (no 录制：/Rec: prefix; that prefix is popover-button-only).
    private var engineStatusText: String {
        if rec.mode == "off" { return L("关", "Off") }
        if !rec.engineRunning { return L("未在录制", "Not recording") }
        return rec.mode == "screen_audio" ? L("屏幕+音频", "Screen + audio")
                                          : L("仅屏幕", "Screen only")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("录制与 ingest", "Recording & Ingest"))
                .font(.system(size: 18, weight: .semibold))

            // Screenpipe control — mode picker + engine status (RecordingController)
            VStack(alignment: .leading, spacing: 8) {
                Text(L("Screenpipe 录制", "Screenpipe Recording"))
                    .font(.system(size: 13, weight: .semibold))
                HStack(spacing: 8) {
                    Text(L("模式", "Mode"))
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                    Picker("", selection: Binding(
                        get: { rec.mode },
                        set: { rec.setMode($0) })) {
                        Text(L("关", "Off")).tag("off")
                        Text(L("仅屏幕", "Screen only")).tag("screen")
                        Text(L("屏幕+音频", "Screen + audio")).tag("screen_audio")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 280)
                    Spacer()
                }
                HStack(spacing: 8) {
                    Circle()
                        .fill(rec.engineRunning ? Color.green
                              : (rec.mode != "off" ? Color.orange : Color.secondary.opacity(0.5)))
                        .frame(width: 8, height: 8)
                    Text(engineStatusText)
                        .font(.system(size: 12))
                    Text(model.dbLabel)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.secondary)
                    Button(L("刷新", "Refresh")) {
                        rec.refreshEngineState()
                        model.refreshLabels()
                    }
                    .controlSize(.small)
                    Spacer()
                }
                if !rec.engineRunning && rec.mode != "off"
                    && !RecordingController.hasScreenPermission() {
                    HStack(spacing: 8) {
                        Text(L("原因：macOS 还没把「屏幕录制」权限授给本 App（授权一次即可，之后开 App 自动录制）。",
                               "Cause: macOS hasn't granted this app Screen Recording yet (grant once; recording then auto-starts with the app)."))
                            .font(.system(size: 11))
                            .foregroundColor(.orange)
                        Button(L("去授权", "Grant…")) {
                            RecordingController.openScreenRecordingSettings()
                        }
                        .controlSize(.small)
                    }
                }
                Text(L("菜单栏面板右上角的录制按钮可随时切换。",
                       "The recording button at the top-right of the menu-bar panel can switch modes anytime."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            // export / ingest now
            VStack(alignment: .leading, spacing: 8) {
                Text(L("手动触发", "Manual Triggers"))
                    .font(.system(size: 13, weight: .semibold))
                HStack(spacing: 8) {
                    Button(L("立即导出", "Export Now")) { model.runExport() }
                        .disabled(model.exportRunning)
                    if !model.exportStatus.isEmpty {
                        Text(model.exportStatus)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                }
                HStack(spacing: 8) {
                    Button(L("立即 ingest", "Ingest Now")) { model.runIngest() }
                        .disabled(model.ingestRunning)
                    if !model.ingestStatus.isEmpty {
                        Text(model.ingestStatus)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            // freshness labels
            VStack(alignment: .leading, spacing: 6) {
                Text(L("最近活动", "Recent Activity"))
                    .font(.system(size: 13, weight: .semibold))
                HStack {
                    Text(L("vault「1 - unprocessed」最新文件：",
                           "Newest file in vault \"1 - unprocessed\": "))
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                    Text(model.unprocessedLabel)
                        .font(.system(size: 12, design: .monospaced))
                }
                HStack {
                    Text(L("state/actd.log 更新于：", "state/actd.log updated: "))
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                    Text(model.actdLogLabel)
                        .font(.system(size: 12, design: .monospaced))
                }
                Button(L("刷新", "Refresh")) { model.refreshLabels() }
                    .controlSize(.small)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .onAppear {
            model.refreshLabels()
            rec.refreshEngineState()
        }
        // item 7: freshness labels are baked at refresh time — re-run on
        // language switch so 无文件/No files etc. follow the new language.
        .onChange(of: i18n.lang) { _, _ in model.refreshLabels() }
    }
}

// MARK: §15.4 关于

struct AboutView: View {
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("关于", "About"))
                .font(.system(size: 18, weight: .semibold))
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(L("应用", "App")).foregroundColor(.secondary).frame(width: 80, alignment: .leading)
                    Text("Zelin's AI Assistant")
                }
                HStack {
                    Text(L("版本", "Version")).foregroundColor(.secondary).frame(width: 80, alignment: .leading)
                    // single source of truth = Info.plist; "dev" when running
                    // the bare binary outside a bundle (no info dictionary)
                    Text(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
                         as? String ?? "dev")
                }
                HStack(alignment: .top) {
                    Text(L("仓库", "Repo")).foregroundColor(.secondary).frame(width: 80, alignment: .leading)
                    Text(AppPaths.stateRoot)
                        .font(.system(size: 12, design: .monospaced))
                        .textSelection(.enabled)
                }
                HStack(alignment: .top) {
                    Text(L("用量报告", "Usage report")).foregroundColor(.secondary).frame(width: 80, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("python -m act.report")
                            .font(.system(size: 12, design: .monospaced))
                            .textSelection(.enabled)
                        Text(L("在 repo 目录下运行，查看功能使用频率与健康信号。",
                               "Run in the repo directory to see feature usage and health signals."))
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                }
            }
            .font(.system(size: 13))
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}
