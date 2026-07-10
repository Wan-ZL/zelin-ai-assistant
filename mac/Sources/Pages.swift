// Pages.swift — 主窗口页面：依赖检查（DepAction/DepRowState/DepsModel/DepsView）/ 录制与 ingest（IngestModel/IngestView）/ 关于（AboutView）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation
import AVFoundation  // AVCaptureDevice.authorizationStatus (microphone TCC row)

// MARK: §15.1 依赖检查 — "车跑之前轮子都得在"

enum DepAction {
    case url(String)
    case reveal(String)
    case grant(String)  // TCC rows: x-apple.systempreferences deep link to the pane
    case settings   // credential rows: jump to the 设置 page (App 内管理)
    case ingest     // engine row: jump to the 录制与 ingest page (start/stop there)
    case cronFDA    // §25 guided grant: copy /usr/sbin/cron + open the FDA pane

    @MainActor func perform() {
        switch self {
        case .url(let u), .grant(let u):
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
        case .ingest:
            MainNav.shared.section = .ingest
        case .cronFDA:
            CronFDA.beginGrant()
        }
    }

    var label: String {
        switch self {
        case .url: return L("下载页", "Download")
        case .reveal: return L("显示", "Reveal")
        case .grant: return L("去授权", "Grant…")
        case .settings: return L("去设置", "Open Settings")
        case .ingest: return L("去录制页", "Open Recording")
        case .cronFDA: return L("去授权", "Grant…")
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

/// One §25 doctor check (python -m act.doctor --json): raw fields only —
/// display text is built in the view so it follows language switches.
struct DoctorRow: Identifiable {
    let id: String
    let name: String
    let status: String     // "ok" | "warn" | "fail"
    let detail: String
    let fix: String
    let failureID: String  // act/lib/failures.py id ("" = unclassified)
    let actionID: String
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
    // P1-3 app-side hook: `bash install.sh --check` (= python -m act.doctor)
    @Published var doctorOutput = ""
    @Published var doctorRunning = false
    // exit code = number of FAILs (doctor contract); nil = never run
    @Published var doctorFails: Int? = nil
    // §25 classified findings (empty when the JSON path was unavailable)
    @Published var doctorRows: [DoctorRow] = []
    // "让 AI 修" status line under the diagnostics block
    @Published var aiFixStatus = ""

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
        // P1-5: recording mode decides whether a missing mic grant is a blocker
        let recMode = RecordingController.shared.mode

        DispatchQueue.global(qos: .userInitiated).async {
            let fm = FileManager.default
            var out: [DepRowState] = []
            // screenpipe: canonical launch path is `npx screenpipe@<pin>`
            // (Recording.swift) — check npx presence + engine liveness (pgrep
            // contract), NOT /Applications/Screenpipe.app.
            out.append(DepRowState(
                id: "npx", name: "Node / npx",
                ok: Shell.ok("command -v npx >/dev/null"
                    + " || test -x /opt/homebrew/bin/npx"),
                detail: L("npx（screenpipe 引擎经 npx 自动运行，无需单独安装；缺失则 brew install node）",
                          "npx (screenpipe engine runs via npx — no separate install; missing? brew install node)"),
                action: .url("https://nodejs.org")))
            out.append(DepRowState(
                id: "engine", name: L("录制引擎", "Recording engine"),
                ok: RecordingController.isEngineRunning(),
                detail: "pgrep -f \"\(RecordingController.enginePattern)\""
                    + L("（引擎进程存活）", " (engine process alive)"),
                action: .ingest))
            // P1-5 TCC rows — the two most common first-launch blockers, both
            // probeable without prompting (CGPreflight / authorizationStatus).
            out.append(DepRowState(
                id: "screen_tcc", name: L("屏幕录制权限", "Screen Recording permission"),
                ok: RecordingController.hasScreenPermission(),
                detail: L("CGPreflightScreenCaptureAccess()（未授权时引擎启动即退出、录不到任何内容）",
                          "CGPreflightScreenCaptureAccess() (without it the engine exits instantly, capturing nothing)"),
                action: .grant(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")))
            let micGranted = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
            out.append(DepRowState(
                id: "mic_tcc", name: L("麦克风权限", "Microphone permission"),
                // only a blocker when the screen_audio mode actually needs it
                ok: micGranted || recMode != "screen_audio",
                detail: micGranted
                    ? L("已授权（「屏幕+音频」转写可用）",
                        "granted (Screen + Audio transcription available)")
                    : recMode == "screen_audio"
                    ? L("未授权——「屏幕+音频」模式录不到语音",
                        "not granted — Screen + Audio mode can't transcribe")
                    : L("未授权（当前模式不需要；切「屏幕+音频」前先授权）",
                        "not granted (not needed in the current mode; grant before switching to Screen + Audio)"),
                action: .grant(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")))
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
            // §25 cron Full Disk Access — the audit's #1 documented silent
            // failure. Ground truth = state/cron_probe.json, written by REAL
            // cron runs (an in-app probe would use the app's own grant and lie).
            out.append(Self.cronFDARow())
            // 雷达健康 (contract E) — is the gmail/slack radar actually landing?
            let radar = Self.readRadarHealth(path: radarHealthPath)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.rows = out
                    self.radar = radar
                    self.checking = false
                    self.autoRunDoctorIfBroken(rows: out)
                }
            }
        }
    }

    /// §25 row from the cron FDA probe. Three honest states: no data yet /
    /// probe fresh + read ok / probe says blocked (or the chain stopped).
    nonisolated private static func cronFDARow() -> DepRowState {
        guard let probe = CronProbe.read() else {
            return DepRowState(
                id: "cron_fda", name: L("定时任务磁盘权限", "Cron disk access"),
                ok: false,
                detail: L("还没有探测数据——等下一次定时运行（约 30 分钟）；一直没有就重跑一遍安装（会更新定时任务）",
                          "No probe data yet — wait for the next scheduled run (~30 min); if it never appears, rerun the installer (updates the cron line)"),
                action: .cronFDA)
        }
        let age = probe.ts.map { Date().timeIntervalSince($0) }
        if let age, age > 2 * 3600 {
            return DepRowState(
                id: "cron_fda", name: L("定时任务磁盘权限", "Cron disk access"),
                ok: false,
                detail: L("最近一次探测在 \(Int(age / 3600)) 小时前——定时任务可能停跑了（先查「诊断」）",
                          "Last probe \(Int(age / 3600))h ago — the scheduled jobs may have stopped (run Diagnostics)"),
                action: .cronFDA)
        }
        if probe.readOK {
            return DepRowState(
                id: "cron_fda", name: L("定时任务磁盘权限", "Cron disk access"),
                ok: true,
                detail: L("定时任务能读取 \(probe.path)", "cron can read \(probe.path)"),
                action: .cronFDA)
        }
        return DepRowState(
            id: "cron_fda", name: L("定时任务磁盘权限", "Cron disk access"),
            ok: false,
            detail: L("macOS 挡住了定时任务读取 \(probe.path)——屏幕记录不会变成笔记。点「去授权」按提示给 cron 开「完全磁盘访问」",
                      "macOS blocks the scheduled jobs from reading \(probe.path) — captures never become notes. Click Grant and follow the steps"),
            action: .cronFDA)
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

    // MARK: P1-3/§25 diagnostics — the post-install doctor, classified

    /// §25: auto-run the cheap doctor (--fast, no live claude call) ONCE per
    /// app session as soon as a failure state is visible in the quick rows —
    /// the tired user must not have to know a "Run diagnostics" button exists.
    nonisolated(unsafe) private static var autoDoctorRan = false

    func autoRunDoctorIfBroken(rows: [DepRowState]) {
        guard !Self.autoDoctorRan, !doctorRunning else { return }
        let recOff = RecordingController.shared.mode == "off"
        let critical = rows.contains { row in
            guard row.ok == false else { return false }
            switch row.id {
            case "npx", "claude", "pyyaml", "cron_fda": return true
            case "engine": return !recOff  // a stopped engine is fine when recording is off
            default: return false
            }
        }
        guard critical else { return }
        Self.autoDoctorRan = true
        runDoctor(fast: true)
    }

    /// `python -m act.doctor --json` (§25): classified rows with one-click
    /// fixes. `fast` skips the live claude probe (free — used for auto-runs);
    /// the manual button does the full run. JSON unavailable (broken runtime
    /// python) → fall back to the legacy `bash install.sh --check` text dump.
    func runDoctor(fast: Bool = false) {
        guard !doctorRunning else { return }
        doctorRunning = true
        doctorOutput = ""
        doctorRows = []
        doctorFails = nil
        Analytics.log(fast ? "mw_doctor_auto" : "mw_doctor_run")
        let root = AppPaths.stateRoot
        let py = IMessageSettingsModel.runtimePython()
        DispatchQueue.global(qos: .userInitiated).async {
            var args = ["-m", "act.doctor", "--json"]
            if fast { args.append("--fast") }
            let (code, out) = Self.runFullOutput(py, args, cwd: root)
            if let rows = Self.parseDoctorJSON(out) {
                let text = rows.map { r in
                    var line = "[\(r.status)] \(r.name): \(r.detail)"
                    if !r.fix.isEmpty && r.status != "ok" { line += "\n    fix: \(r.fix)" }
                    return line
                }.joined(separator: "\n")
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        self.doctorRunning = false
                        self.doctorRows = rows
                        self.doctorOutput = text
                        self.doctorFails = rows.filter { $0.status == "fail" }.count
                        Analytics.log("mw_doctor_result", fields: ["fails": Int(code)])
                    }
                }
                return
            }
            // legacy fallback — e.g. runtime python broken: install.sh --check
            // has its own interpreter detection.
            let cmd = "cd " + Self.shellQuote(root) + " && bash install.sh --check 2>&1"
            let (code2, out2) = Self.runFullOutput("/bin/zsh", ["-lc", cmd])
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.doctorRunning = false
                    let text = out2.trimmingCharacters(in: .whitespacesAndNewlines)
                    self.doctorOutput = text.isEmpty
                        ? L("诊断没有产生输出（exit \(code2)）——检查 install.sh 是否存在于 repo 根目录",
                            "Diagnostics produced no output (exit \(code2)) — check that install.sh exists at the repo root")
                        : text
                    self.doctorFails = text.isEmpty ? nil : Int(code2)
                    Analytics.log("mw_doctor_result", fields: ["fails": Int(code2)])
                }
            }
        }
    }

    /// {"home":…, "checks":[{name,status,detail,fix,failure_id,action_id}]} →
    /// rows; nil when the output is not the §25 JSON shape.
    nonisolated private static func parseDoctorJSON(_ out: String) -> [DoctorRow]? {
        guard let start = out.firstIndex(of: "{"),
              let data = String(out[start...]).data(using: .utf8),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let checks = obj["checks"] as? [[String: Any]]
        else { return nil }
        return checks.enumerated().map { i, c in
            DoctorRow(
                id: "\(i)-\((c["name"] as? String) ?? "?")",
                name: (c["name"] as? String) ?? "?",
                status: (c["status"] as? String) ?? "warn",
                detail: (c["detail"] as? String) ?? "",
                fix: (c["fix"] as? String) ?? "",
                failureID: (c["failure_id"] as? String) ?? "",
                actionID: (c["action_id"] as? String) ?? "")
        }
    }

    /// Like Shell.run but returns the FULL combined output — the doctor
    /// report must not be truncated to Shell.run's 400-char tail.
    nonisolated private static func runFullOutput(
        _ launchPath: String, _ args: [String], cwd: String? = nil) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launchPath)
        p.arguments = args
        if let cwd {
            p.currentDirectoryURL = URL(fileURLWithPath: cwd, isDirectory: true)
            var env = ProcessInfo.processInfo.environment
            env["AIASSISTANT_HOME"] = cwd
            p.environment = env
        }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (127, error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }

    /// Single-quote for zsh — the repo path may contain spaces (or worse).
    nonisolated private static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
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
                VStack(alignment: .leading, spacing: 6) {
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
                                .help(row.detail)
                        }
                        Spacer()
                        Button(row.action.label) { row.action.perform() }
                            .controlSize(.small)
                    }
                    // §25 cron FDA guided grant — inline click-by-click steps
                    // (clone of the iMessage FDA block's approach)
                    if row.id == "cron_fda", row.ok == false {
                        Text(CronFDA.grantSteps)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
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

            // P1-3: post-install doctor — deep, on-demand (spends one cheap
            // live claude call), complements the quick rows above.
            Text(L("诊断", "Diagnostics"))
                .font(.system(size: 13, weight: .semibold))
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Button(model.doctorRunning ? L("诊断中…", "Running…")
                                               : L("运行诊断", "Run diagnostics")) {
                        model.runDoctor()
                    }
                    .disabled(model.doctorRunning)
                    if model.doctorRunning {
                        ProgressView().controlSize(.small)
                        Text(L("最长约 1-2 分钟（含一次真实 claude 调用）",
                               "up to ~1-2 min (includes one live claude call)"))
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    } else if let fails = model.doctorFails {
                        Text(fails == 0
                             ? L("全部通过 ✓", "All checks passed ✓")
                             : L("\(fails) 项未通过——每条都有对应按钮",
                                 "\(fails) check(s) failed — each has its own button"))
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(fails == 0 ? .green : .red)
                    }
                    // §25 escape hatch: anything unclassified / repair failed
                    if !model.doctorRunning, (model.doctorFails ?? 0) > 0, AIFix.enabled {
                        Button(L("让 AI 修", "Fix with AI")) {
                            model.aiFixStatus = L("正在准备诊断包…", "Preparing the diagnostic bundle…")
                            AIFix.launch(context: model.doctorOutput) { _, msg in
                                model.aiFixStatus = msg
                            }
                        }
                    }
                    Spacer()
                }
                Text(L("发现异常时会自动运行一次快速诊断；这个按钮跑完整版（含一次真实 claude 调用）。",
                       "A quick diagnostic auto-runs when something looks broken; this button runs the full version (one live claude call)."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                if !model.aiFixStatus.isEmpty {
                    Text(model.aiFixStatus)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                // §25 classified findings — plain sentence + one right button;
                // the raw probe text drops to the tooltip / full report.
                ForEach(model.doctorRows.filter { $0.status != "ok" }) { row in
                    HStack(alignment: .center, spacing: 8) {
                        Text(row.status == "fail" ? "❌" : "⚠️")
                            .font(.system(size: 12))
                        VStack(alignment: .leading, spacing: 1) {
                            Text(FailureCatalog.message(row.failureID) ?? row.detail)
                                .font(.system(size: 11))
                                .fixedSize(horizontal: false, vertical: true)
                            Text(row.name)
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        if let label = FailureCatalog.actionLabel(row.failureID) {
                            Button(label) { FailureCatalog.perform(row.failureID) }
                                .controlSize(.small)
                        }
                    }
                    .help(row.detail + (row.fix.isEmpty ? "" : "\nfix: " + row.fix))
                    .padding(6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background((row.status == "fail" ? Color.red : Color.orange).opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                }
                if !model.doctorOutput.isEmpty {
                    DisclosureGroup(L("完整报告", "Full report")) {
                        ScrollView {
                            Text(model.doctorOutput)
                                .font(.system(size: 10, design: .monospaced))
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(8)
                        }
                        .frame(maxHeight: 260)
                        .background(Color.primary.opacity(0.04))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                    .font(.system(size: 11))
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
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
    // P2-4: full output tail of the last FAILED run (the status line truncates
    // to 120 chars) — surfaced via .help tooltip; cleared on run start.
    @Published var exportErrorTail = ""
    @Published var ingestErrorTail = ""
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
        exportErrorTail = ""
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
                self.exportErrorTail = tail
                self.setExportStatus(
                    L("失败 (exit \(code)) ", "Failed (exit \(code)) ") + tail.suffix(120))
            }
            self.refreshLabels()
        }
    }

    func runIngest() {
        guard !ingestRunning else { return }
        ingestRunning = true
        ingestErrorTail = ""
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
                self.ingestErrorTail = tail
                self.setIngestStatus(
                    L("失败 (exit \(code)) ", "Failed (exit \(code)) ") + tail.suffix(120))
            }
            self.refreshLabels()
        }
    }

    /// Reveal the ingest script's own log — the claude output that a manual
    /// run truncates (Shell.run keeps 400 chars) all lands there. Path must
    /// match LOGFILE in ingest/process-screenpipe.sh.
    static func revealIngestLog() {
        let p = "/tmp/screenpipe-auto.log"
        let target = FileManager.default.fileExists(atPath: p)
            ? p : (p as NSString).deletingLastPathComponent
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: target)])
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
                        // P2-4: hover shows the untruncated failure tail (the
                        // export script has no log file — the tail is all there is)
                        Text(model.exportStatus)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .help(model.exportErrorTail.isEmpty
                                  ? model.exportStatus : model.exportErrorTail)
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
                            .help(model.ingestErrorTail.isEmpty
                                  ? model.ingestStatus : model.ingestErrorTail)
                    }
                    if !model.ingestErrorTail.isEmpty {
                        // full claude output lives in the script's log file
                        Button(L("查看日志", "View log")) { IngestModel.revealIngestLog() }
                            .controlSize(.small)
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
                Divider()
                HStack(alignment: .top) {
                    Text(L("卸载", "Uninstall")).foregroundColor(.secondary).frame(width: 80, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Button(L("卸载…", "Uninstall…")) { confirmUninstall() }
                            .controlSize(.small)
                        Text(L("停止全部后台服务并移除本产品；任务历史与密钥默认保留。",
                               "Stops every background service and removes the product; task history and keys are kept by default."))
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

    // MARK: 卸载 — confirmation dialog, then uninstall.sh in Terminal

    /// The app cannot safely delete itself and its own daemons from inside
    /// the process — uninstall.sh (repo root) owns that. This entry makes the
    /// script reachable without reading docs: an explicit dialog listing
    /// exactly what will happen, then Terminal runs the script, which echoes
    /// every action and asks its own final Y/n.
    private func confirmUninstall() {
        let script = AppPaths.stateRoot + "/uninstall.sh"
        guard FileManager.default.fileExists(atPath: script) else {
            let alert = NSAlert()
            alert.messageText = L("找不到卸载脚本", "Uninstall script not found")
            alert.informativeText = L(
                "预期位置：\(script)\n更新一次产品（重装 .pkg 或 git pull）即可补上，或按 docs/INSTALL.md 的「卸载」一节手动移除。",
                "Expected at: \(script)\nUpdate the product once (reinstall the .pkg or git pull) to restore it, or follow the Uninstall section of docs/INSTALL.md to remove things manually.")
            alert.addButton(withTitle: L("好", "OK"))
            alert.runModal()
            return
        }
        let alert = NSAlert()
        alert.messageText = L("卸载 Zelin's AI Assistant？", "Uninstall Zelin's AI Assistant?")
        alert.informativeText = L(
            """
            将执行以下操作（在 Terminal 中逐条显示，动手前再确认一次）：
            • 停止并移除全部后台服务（AI 派发、屏幕录制、雷达、定时任务）
            • 从 crontab 移除本产品的行（你的其他行原样保留）
            • 退出本 App，删除 /Applications 里的 App 与系统级管线副本

            默认保留：任务历史（state/）、API 密钥、Obsidian vault、屏幕录像——每一项都会附上删除命令。
            """,
            """
            What will happen (each step shown in Terminal, with one final confirmation there):
            • Stop and remove every background service (AI dispatch, screen recording, radars, scheduled jobs)
            • Remove this product's lines from your crontab (all your other lines kept)
            • Quit this app, delete the app in /Applications and the system-level pipeline copy

            Kept by default: task history (state/), API keys, your Obsidian vault, screen recordings — each listed with its removal command.
            """)
        alert.addButton(withTitle: L("在 Terminal 中卸载…", "Uninstall in Terminal…"))
        alert.addButton(withTitle: L("取消", "Cancel"))
        alert.alertStyle = .warning
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        Analytics.log("uninstall_started")
        // POSIX-quote the path for the shell, then escape for the AppleScript
        // string literal (repo paths can contain spaces/quotes).
        let shellCmd = "bash " + Self.shellQuote(script)
        let osa = "tell application \"Terminal\"\nactivate\ndo script \""
            + Self.appleScriptEscape(shellCmd) + "\"\nend tell"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", osa]
        do { try p.run() } catch {
            let fail = NSAlert()
            fail.messageText = L("无法打开 Terminal", "Could not open Terminal")
            fail.informativeText = L(
                "请手动在 Terminal 里运行：\(shellCmd)",
                "Run this in Terminal yourself: \(shellCmd)")
            fail.addButton(withTitle: L("好", "OK"))
            fail.runModal()
        }
    }

    private static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func appleScriptEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }
}
