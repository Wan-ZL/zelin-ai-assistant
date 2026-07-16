// SetupWizard.swift — 初始设置向导 / first-run Setup Wizard (novice audit theme 2, wave-3 W1)
//
// Six one-screen steps, all prefilled with detected/current values so that
// Next-Next-Next yields a working system (design law: automate > guided_ui >
// copy — the happy path never needs YAML, Terminal or docs):
//   1 欢迎 + 语言  — welcome + UI language (writes the existing "language" override)
//   2 AI 引擎      — auto-detect claude CLI + its auth; fallback: paste an API
//                    key with VERIFY-ON-PASTE (KeyProbe /v1/models — free GET)
//                    and 0600 save to config/secrets/anthropic-api-key.txt (§19)
//   3 系统权限     — CapabilityRowsView + TelemetryBlockView (shared components
//                    with the permissions checkup, Permissions.swift)
//   4 屏幕记录     — RecordingConsentSection (same one-time consent keys:
//                    recordingConsentShown / recordingMode — v0.11/v0.13 rules)
//   5 笔记库       — Obsidian vault picker (parsed from Obsidian's own
//                    obsidian.json registry) or a plain markdown folder;
//                    writes the existing "obsidian_raw" override (§15.3)
//   6 完成         — live health check (权限 / 引擎 / 后台服务 / 首次数据),
//                    every failing row with ONE fix button; then the menu-bar
//                    hello bubble so users know where the app lives.
//
// Completion marker = UserDefaults "setupWizardCompleted" (CONTRACT §15 v0.14
// note): missing or corrupt → the wizard auto-reopens on next launch; ONLY the
// finale's 完成 button writes it. The wizard is idempotent: never wipes data,
// never re-imports, never re-asks an answered consent. 设置 → 通用 has a
// "重新运行初始设置" button that reopens it anytime.

import AppKit
import SwiftUI
import Foundation

// MARK: - completion marker (CONTRACT §15 v0.14 note)

enum SetupWizardMarker {
    static let key = "setupWizardCompleted"

    /// True only when the marker is present AND a real Bool true. A missing
    /// or corrupt (non-Bool) value counts as "not completed" → the wizard
    /// reopens on next launch.
    nonisolated static var completed: Bool {
        (UserDefaults.standard.object(forKey: key) as? Bool) ?? false
    }

    static func markCompleted() {
        UserDefaults.standard.set(true, forKey: key)
    }
}

// MARK: - window controller (singleton; closing hides, app stays .accessory)

@MainActor
final class SetupWizardController: NSObject, NSWindowDelegate {
    static let shared = SetupWizardController()
    private var window: NSWindow?
    // models live here (not @StateObject) so their timers can be stopped
    // deterministically in windowWillClose — same pattern as
    // PermissionsWindowController.
    private let perms = PermissionsModel()
    private let engine = EngineDetector()
    private let probe = PipelineProbeModel()

    func show() {
        if window == nil {
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 660, height: 720),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false)
            win.isReleasedWhenClosed = false
            win.contentMinSize = NSSize(width: 620, height: 600)
            win.delegate = self
            win.center()
            window = win
        }
        window?.title = L("初始设置", "Setup")
        // rebuild the root view each show so every step re-reads current values
        // (idempotent re-runs: everything prefilled, nothing wiped)
        window?.contentViewController = NSHostingController(
            rootView: SetupWizardView(perms: perms, engine: engine, probe: probe) { [weak self] in
                self?.window?.performClose(nil)
            })
        perms.startPolling()
        engine.detect()
        Analytics.log("wizard_open", fields: ["rerun": SetupWizardMarker.completed])
        // LSUIElement app: activate explicitly or the window opens behind the
        // frontmost app (same trap as the permissions window).
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        perms.stopPolling()
        probe.stopPolling()
        // P0-11 guarantee unchanged: dismissing the wizard without answering
        // the recording question counts as 暂不 — nothing was ever captured
        // and the consent never nags again (recording stays changeable in
        // 设置 → 录制). The wizard itself still reopens on next launch while
        // the completion marker is missing.
        if RecordingConsent.needsPrompt {
            RecordingConsent.record(granted: false)
        }
    }
}

// MARK: - AI engine detection (claude CLI + auth)

enum EngineAuth {
    case oauth        // Claude Code keychain login (or ~/.claude/.credentials.json)
    case envKey       // ANTHROPIC_API_KEY in the login-shell environment
    case secretsFile  // config/secrets/anthropic-api-key.txt (§19, app-managed)
    case legacyFile   // ~/.config/anthropic-key.txt (§19 legacy tier)

    var label: String {
        switch self {
        case .oauth: return L("Claude Code 登录", "Claude Code login")
        case .envKey: return L("ANTHROPIC_API_KEY 环境变量", "ANTHROPIC_API_KEY env var")
        case .secretsFile: return L("API key(App 内保存)", "API key (saved in-app)")
        case .legacyFile: return L("API key(旧路径)", "API key (legacy path)")
        }
    }
}

@MainActor
final class EngineDetector: ObservableObject {
    struct Detection {
        var cliPath: String?
        var version: String?
        var auth: EngineAuth?
        var ready: Bool { cliPath != nil && auth != nil }
    }

    @Published var checking = false
    @Published var detection = Detection()

    func detect() {
        guard !checking else { return }
        checking = true
        DispatchQueue.global(qos: .userInitiated).async {
            let d = Self.probe()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.detection = d
                    self.checking = false
                    Analytics.log("wizard_engine_detect", fields: [
                        "cli": d.cliPath != nil, "auth": d.auth != nil])
                }
            }
        }
    }

    nonisolated private static func probe() -> Detection {
        var d = Detection()
        // CLI path: login shell first (PATH as in a user terminal), then the
        // fixed locations GUI shells commonly miss (same list as DepsModel).
        let (code, out) = Shell.run("/bin/zsh", ["-lc", "command -v claude"])
        if code == 0 {
            let line = out.trimmingCharacters(in: .whitespacesAndNewlines)
                .components(separatedBy: "\n")
                .last { $0.hasPrefix("/") }
            if let line, !line.isEmpty { d.cliPath = line }
        }
        if d.cliPath == nil {
            for p in [NSHomeDirectory() + "/.local/bin/claude", "/opt/homebrew/bin/claude"]
            where FileManager.default.isExecutableFile(atPath: p) {
                d.cliPath = p
                break
            }
        }
        if let cli = d.cliPath {
            let (vc, vout) = Shell.run(cli, ["--version"])
            if vc == 0 {
                let first = vout.trimmingCharacters(in: .whitespacesAndNewlines)
                    .components(separatedBy: "\n").first ?? ""
                if !first.isEmpty { d.version = String(first.prefix(40)) }
            }
        }
        d.auth = detectAuth()
        return d
    }

    /// Same resolution ladder headless claude effectively uses. Keychain
    /// probe checks EXISTENCE only (`security find-generic-password` without
    /// -w never prints the secret).
    nonisolated private static func detectAuth() -> EngineAuth? {
        for service in ["Claude Code-credentials", "Claude Code"] {
            if Shell.run("/usr/bin/security",
                         ["find-generic-password", "-s", service]).0 == 0 {
                return .oauth
            }
        }
        if SecretsIO.nonEmptyFile(NSHomeDirectory() + "/.claude/.credentials.json") {
            return .oauth
        }
        if Shell.ok("[ -n \"$ANTHROPIC_API_KEY\" ]") { return .envKey }
        if SecretsIO.hasSecret(SecretsIO.anthropicFile) { return .secretsFile }
        if SecretsIO.nonEmptyFile(
            ("~/.config/anthropic-key.txt" as NSString).expandingTildeInPath) {
            return .legacyFile
        }
        return nil
    }
}

// MARK: - Obsidian vault registry (Obsidian's own obsidian.json)

enum ObsidianVaults {
    /// Vault root paths Obsidian has registered on this Mac, existing dirs
    /// only, stable order. Missing/corrupt registry (Obsidian not installed)
    /// degrades to [] — the plain-folder option always remains.
    nonisolated static func registered() -> [String] {
        let p = NSHomeDirectory() + "/Library/Application Support/obsidian/obsidian.json"
        guard let data = FileManager.default.contents(atPath: p),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let vaults = obj["vaults"] as? [String: Any]
        else { return [] }
        var out: [String] = []
        for value in vaults.values {
            guard let d = value as? [String: Any],
                  let path = d["path"] as? String, !path.isEmpty else { continue }
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: path, isDirectory: &isDir),
               isDir.boolValue {
                out.append(path)
            }
        }
        return out.sorted()
    }
}

// MARK: - Vault apply (shared: wizard step 5 + 设置 → Obsidian Vault 位置)

enum ObsidianVaultSetup {
    /// The four standard pipeline dirs inside the vault root, pipeline order —
    /// same derivation as config.py `_derive_obsidian_dirs`.
    static let pipelineDirNames = ["1 - unprocessed", "2 - raw",
                                   "3 - change-summary", "4 - wiki"]

    /// Point the pipeline at a vault root: create the four standard dirs
    /// (idempotent — creating an existing dir is a no-op; a real failure
    /// throws, because a vault the pipeline cannot create its dirs in means
    /// radar/ingest would silently find nothing), then diff-write the
    /// "obsidian_raw" override — written ONLY when the choice differs from
    /// the current effective value, and DROPPED when it equals the config
    /// layer (config.yaml → built-in default) so config.yaml stays live
    /// (§15.3). Returns true when the effective raw dir actually changed
    /// (analytics hook).
    @discardableResult
    static func apply(root: String) throws -> Bool {
        let r = root.trimmingCharacters(in: .whitespaces)
        guard !r.isEmpty else { return false }
        let rootExpanded = (r as NSString).expandingTildeInPath
        for name in pipelineDirNames {
            try FileManager.default.createDirectory(
                atPath: rootExpanded + "/" + name, withIntermediateDirectories: true)
        }
        let raw = r + "/2 - raw"
        let current = SettingsIO.resolvedPath(
            overrideKey: "obsidian_raw", configKey: "obsidian_raw",
            fallback: "~/Documents/Obsidian Vault/2 - raw")
        guard (raw as NSString).expandingTildeInPath != current else { return false }
        let configLayer = SettingsIO.configScalar("obsidian_raw")
            .flatMap { $0.isEmpty ? nil : $0 } ?? "~/Documents/Obsidian Vault/2 - raw"
        var merged = SettingsIO.readOverrides()
        if (raw as NSString).expandingTildeInPath
            == (configLayer as NSString).expandingTildeInPath {
            merged.removeValue(forKey: "obsidian_raw")
        } else {
            merged["obsidian_raw"] = raw
        }
        try SettingsIO.writeOverrides(merged)
        return true
    }
}

// MARK: - actd launchd agent (render + load, mirrors install.sh step 5)

enum ActdAgent {
    static let label = "com.zelin.aiassistant.actd"

    nonisolated static var plistDest: String {
        NSHomeDirectory() + "/Library/LaunchAgents/\(label).plist"
    }

    nonisolated static func isLoaded() -> Bool {
        Shell.run("/bin/launchctl", ["print", "gui/\(getuid())/\(label)"]).0 == 0
    }

    /// Render the repo plist template (same 4 placeholder substitutions as
    /// install.sh render_launchd_plist, same order) and launchctl load it.
    /// Blocking — background queue only.
    nonisolated static func renderAndLoad() -> (Bool, String) {
        let root = AppPaths.stateRoot
        let template = root + "/act/launchd/\(label).plist"
        guard var text = try? String(contentsOfFile: template, encoding: .utf8) else {
            return (false, L("后台服务模板缺失(\(template))——repo 不完整?在 repo 目录运行 bash install.sh 可修复",
                             "Background-service template missing (\(template)) — incomplete repo? Running bash install.sh in the repo directory fixes it"))
        }
        let py = RuntimePython.resolve()
        let pyDir = (py as NSString).deletingLastPathComponent
        let home = NSHomeDirectory()
        text = text
            .replacingOccurrences(of: "/Users/YOURUSERNAME/miniconda3/bin/python3", with: py)
            .replacingOccurrences(of: "/Users/YOURUSERNAME/Projects/zelin-ai-assistant", with: root)
            .replacingOccurrences(of: "/Users/YOURUSERNAME/miniconda3/bin", with: pyDir)
            .replacingOccurrences(of: "/Users/YOURUSERNAME", with: home)
        let dest = plistDest
        do {
            try FileManager.default.createDirectory(
                atPath: (dest as NSString).deletingLastPathComponent,
                withIntermediateDirectories: true)
            try text.write(toFile: dest, atomically: true, encoding: .utf8)
        } catch {
            return (false, L("写入 \(dest) 失败: ", "Failed to write \(dest): ")
                    + error.localizedDescription)
        }
        _ = Shell.run("/bin/launchctl", ["unload", dest])  // ignore "not loaded"
        let (code, out) = Shell.run("/bin/launchctl", ["load", dest])
        if code != 0 {
            return (false, L("启动失败: ", "Start failed: ") + out)
        }
        return (true, "")
    }
}

// MARK: - pipeline probes (finale health check: 后台服务 + 首次数据)

@MainActor
final class PipelineProbeModel: ObservableObject {
    @Published var actdLoaded: Bool? = nil        // nil = still checking
    @Published var dashboardExists: Bool? = nil   // nil = still checking
    @Published var dashboardAgo = ""              // relative "generated_at" age
    @Published var fixingDaemon = false
    @Published var seeding = false
    @Published var fixNote = ""
    // §25 cron FDA probe (state/cron_probe.json) — nil = no data yet; the
    // wizard finale renders it as its own health row (audit 3.1: an all-green
    // finale without this probe would be a lie).
    @Published var cronProbe: CronProbe?
    @Published var cronProbeChecked = false       // false = still checking

    private var timer: Timer?

    func startPolling() {
        refresh()
        guard timer == nil else { return }
        // .common mode so ticks keep landing during event tracking — same
        // rationale as the PermissionsModel timer.
        let t = Timer(timeInterval: 2.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func refresh() {
        let dash = AppPaths.dashboardPath
        DispatchQueue.global(qos: .utility).async {
            let loaded = ActdAgent.isLoaded()
            var exists = false
            var ago = ""
            if let data = FileManager.default.contents(atPath: dash),
               let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
                exists = true
                if let gen = obj["generated_at"] as? String,
                   let rel = RelativeTime.since(gen) {
                    ago = rel
                }
            }
            let cron = CronProbe.read()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.actdLoaded = loaded
                    self.dashboardExists = exists
                    self.dashboardAgo = ago
                    self.cronProbe = cron
                    self.cronProbeChecked = true
                }
            }
        }
    }

    /// One-click daemon start: render the plist (if needed) + launchctl load,
    /// in-process — the wizard never sends users to Terminal (P0-3 lite).
    func startBackgroundService() {
        guard !fixingDaemon else { return }
        fixingDaemon = true
        fixNote = ""
        Analytics.log("wizard_fix", fields: ["what": "actd"])
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, why) = ActdAgent.renderAndLoad()
            Thread.sleep(forTimeInterval: 2.0)   // let RunAtLoad surface
            let loaded = ActdAgent.isLoaded()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.fixingDaemon = false
                    self.actdLoaded = loaded
                    self.fixNote = ok ? "" : why
                    self.refresh()
                }
            }
        }
    }

    /// Seed dashboard.json once (doctor's documented remedy) via the pinned
    /// runtime python — covers the "daemon just started, no data yet" gap.
    func seedDashboard() {
        guard !seeding else { return }
        seeding = true
        fixNote = ""
        Analytics.log("wizard_fix", fields: ["what": "seed_dashboard"])
        let root = AppPaths.stateRoot
        DispatchQueue.global(qos: .userInitiated).async {
            let py = RuntimePython.resolve()
            let p = Process()
            p.executableURL = URL(fileURLWithPath: py)
            p.arguments = ["-m", "act.lib.dashboard"]
            p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
            var env = ProcessInfo.processInfo.environment
            env["AIASSISTANT_HOME"] = root
            env["AIASSISTANT_UI_LANG"] = LanguageMirror.current   // §15: python copy matches the app language
            p.environment = env
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            var code: Int32 = 127
            var tail = ""
            do {
                try p.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                code = p.terminationStatus
                tail = String((String(data: data, encoding: .utf8) ?? "").suffix(200))
            } catch {
                tail = error.localizedDescription
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.seeding = false
                    if code != 0 {
                        self.fixNote = L("生成失败: ", "Seeding failed: ") + tail
                    }
                    self.refresh()
                }
            }
        }
    }
}

// MARK: - wizard view

struct SetupWizardView: View {
    enum Step: Int, CaseIterable {
        case welcome, engine, permissions, recording, vault, finale

        var shortName: String {
            switch self {
            case .welcome: return "welcome"
            case .engine: return "engine"
            case .permissions: return "permissions"
            case .recording: return "recording"
            case .vault: return "vault"
            case .finale: return "finale"
            }
        }
    }

    @ObservedObject var perms: PermissionsModel
    @ObservedObject var engine: EngineDetector
    @ObservedObject var probe: PipelineProbeModel
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var i18n = LanguageStore.shared
    let close: () -> Void

    @State private var step: Step = .welcome

    // engine step: verify-on-paste state
    @State private var keyInput = ""
    @State private var keyNote = ""
    @State private var keyNoteColor = Color.secondary
    @State private var keyProbing = false
    @State private var keyProbeGen = 0
    @State private var lastProbedKey = ""

    // vault step
    @State private var registeredVaults: [String] = []
    @State private var vaultRoot = ""
    @State private var customRoot = ""
    @State private var vaultLoaded = false
    @State private var vaultError = ""

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                Group {
                    switch step {
                    case .welcome: welcomeStep
                    case .engine: engineStep
                    case .permissions: permissionsStep
                    case .recording: recordingStep
                    case .vault: vaultStep
                    case .finale: finaleStep
                    }
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            footer
                .padding(.horizontal, 24)
                .padding(.vertical, 14)
        }
        .frame(minWidth: 620, minHeight: 600)
        .onAppear {
            if !vaultLoaded {
                vaultLoaded = true
                loadVaultChoices()
            }
        }
    }

    // MARK: shared pieces

    private func stepTitle(_ title: String, _ subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(size: 22, weight: .semibold))
            Text(subtitle)
                .font(.system(size: 13))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 6)
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) { content() }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var footer: some View {
        HStack(spacing: 10) {
            // progress dots — filled up to the current step
            HStack(spacing: 5) {
                ForEach(Step.allCases, id: \.rawValue) { s in
                    Circle()
                        .fill(s.rawValue <= step.rawValue
                              ? Color.accentColor : Color.secondary.opacity(0.25))
                        .frame(width: 7, height: 7)
                }
            }
            Text(L("第 \(step.rawValue + 1) / \(Step.allCases.count) 步",
                   "Step \(step.rawValue + 1) of \(Step.allCases.count)"))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            Spacer()
            if step != .welcome {
                Button(L("上一步", "Back")) { setStep(Step(rawValue: step.rawValue - 1) ?? .welcome) }
            }
            if step != .finale {
                Button(L("下一步", "Next")) { advance() }
                    .keyboardShortcut(.defaultAction)
            } else {
                Button(L("完成", "Done")) { finish() }
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func advance() {
        // a failed vault apply keeps the user on this step with the error
        // shown — advancing would let the finale declare 🎉 while notes
        // would actually land somewhere else (or nowhere)
        if step == .vault, !applyVaultChoice() { return }
        guard let next = Step(rawValue: step.rawValue + 1) else { return }
        setStep(next)
    }

    private func setStep(_ s: Step) {
        step = s
        Analytics.log("wizard_step", fields: ["step": s.shortName])
        if s == .engine { engine.detect() }
        if s == .finale {
            engine.detect()
            probe.startPolling()
            rec.refreshEngineState()
            perms.refresh()
        } else {
            probe.stopPolling()
        }
    }

    private func finish() {
        SetupWizardMarker.markCompleted()
        Analytics.log("wizard_complete")
        close()
        // point at the menu bar once the wizard window is gone — menu-bar-only
        // apps otherwise look like "nothing launched" (audit 2.5)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            MainActor.assumeIsolated {
                (NSApp.delegate as? AppDelegate)?.showHelloBubble()
            }
        }
    }

    // MARK: step 1 — welcome + language

    private var welcomeStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepTitle(L("欢迎使用 Zelin's AI Assistant", "Welcome to Zelin's AI Assistant"),
                      L("你的个人 AI 秘书:它记录你的屏幕、从邮件和消息里发现别人拜托你的事,整理成提案卡片;你只需要批准和验收,其余交给 AI。",
                        "Your personal AI secretary: it captures your screen, finds what people ask of you in mail and messages, and turns it into proposal cards; you approve and accept — the AI does the rest."))
            card {
                Text(L("接下来几步(约 2 分钟)把系统配好。所有选项都已按检测结果预填——一路点「下一步」就能得到一套能用的系统,之后随时可在设置里修改。",
                       "The next few steps (about 2 minutes) set everything up. Every option is prefilled from what was detected — clicking Next all the way through yields a working system, and everything stays changeable in Settings."))
                    .font(.system(size: 13))
                    .fixedSize(horizontal: false, vertical: true)
            }
            card {
                Text(L("界面语言", "Interface language"))
                    .font(.system(size: 14, weight: .semibold))
                Picker("", selection: Binding(
                    get: { i18n.lang },
                    set: { setLanguage($0) })) {
                    Text("中文").tag("zh")
                    Text("English").tag("en")
                }
                .pickerStyle(.segmented)
                .frame(width: 260)
                Text(L("已按系统语言预选;随时可在 设置 里更改。",
                       "Preselected from your system language; changeable anytime in Settings."))
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }
        }
    }

    /// Persist an explicit language choice immediately (same "language"
    /// override the settings form writes) and rebuild the AppKit main menu.
    private func setLanguage(_ v: String) {
        let lang = v == "en" ? "en" : "zh"
        LanguageStore.shared.lang = lang
        var merged = SettingsIO.readOverrides()
        merged["language"] = lang
        try? SettingsIO.writeOverrides(merged)
        (NSApp.delegate as? AppDelegate)?.installMainMenu()
        Analytics.log("wizard_language", fields: ["lang": lang])
    }

    // MARK: step 2 — AI engine

    private var engineStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepTitle(L("接入 AI 引擎", "Connect the AI engine"),
                      L("批准的卡片由 claude CLI 在后台执行。这里检测你已有的配置——多数人无需任何操作。",
                        "Approved cards are executed by the claude CLI in the background. This step detects your existing setup — most people need to do nothing."))
            if engine.checking {
                card {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(L("正在检测 claude CLI 与登录状态…", "Detecting the claude CLI and its login…"))
                            .font(.system(size: 13))
                    }
                }
            } else if engine.detection.ready {
                card {
                    HStack(spacing: 8) {
                        Circle().fill(Color.green).frame(width: 10, height: 10)
                        Text(L("已连接,无需配置", "Connected — nothing to configure"))
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundColor(.green)
                        Spacer()
                        Button(L("重新检测", "Re-detect")) { engine.detect() }
                            .controlSize(.small)
                    }
                    if let v = engine.detection.version {
                        Text(v)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(.secondary)
                    }
                    if let auth = engine.detection.auth {
                        Text(L("认证方式:", "Auth: ") + auth.label)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                }
            } else {
                // the honest sentence (audit 2.1): no engine = nothing ever runs
                Text(L("没有 AI 引擎,提案永远不会被执行。选择下面任一方式接入(推荐 A):",
                       "Without an AI engine, proposals will never be executed. Connect with either option below (A recommended):"))
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                card {
                    Text(L("A. 使用 Claude Code 现有登录(推荐)", "A. Use your Claude Code login (recommended)"))
                        .font(.system(size: 14, weight: .semibold))
                    if engine.detection.cliPath == nil {
                        Text(L("还没找到 claude 命令。安装 Claude Code,登录一次,然后点「重新检测」。",
                               "The claude command wasn't found. Install Claude Code, log in once, then click Re-detect."))
                            .font(.system(size: 12))
                            .fixedSize(horizontal: false, vertical: true)
                        CopyPathLine(label: L("安装命令:", "Install: "),
                                     path: "npm install -g @anthropic-ai/claude-code")
                        HStack(spacing: 8) {
                            Button(L("打开安装页", "Open install page")) {
                                if let url = URL(string: "https://claude.com/claude-code") {
                                    NSWorkspace.shared.open(url)
                                }
                            }
                            .controlSize(.small)
                            Button(L("重新检测", "Re-detect")) { engine.detect() }
                                .controlSize(.small)
                        }
                    } else {
                        Text(L("已找到 claude CLI,但还没有登录。在终端运行 claude 按提示登录,回来点「重新检测」。",
                               "The claude CLI is installed but not logged in. Run claude in Terminal, follow the login prompt, then click Re-detect."))
                            .font(.system(size: 12))
                            .fixedSize(horizontal: false, vertical: true)
                        CopyPathLine(label: L("在终端运行:", "Run in Terminal: "), path: "claude")
                        Button(L("重新检测", "Re-detect")) { engine.detect() }
                            .controlSize(.small)
                    }
                }
                card {
                    Text(L("B. 粘贴 Anthropic API key", "B. Paste an Anthropic API key"))
                        .font(.system(size: 14, weight: .semibold))
                    Text(L("从控制台生成一个 key 粘贴到这里——粘贴后自动验证(一次免费的连通性检查,不消耗 tokens),有效才保存(仅存本机,权限 0600)。",
                           "Generate a key in the console and paste it here — it verifies on paste (one free connectivity check, no tokens billed) and is saved only when valid (local only, mode 0600)."))
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        SecureField(L("sk-ant-…(粘贴后自动验证)", "sk-ant-… (verifies on paste)"),
                                    text: $keyInput)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(size: 12, design: .monospaced))
                            .onChange(of: keyInput) { _, v in keyInputChanged(v) }
                        Button(keyProbing ? L("验证中…", "Verifying…") : L("保存", "Save")) {
                            saveKeyManually()
                        }
                        .controlSize(.small)
                        .disabled(keyProbing
                                  || keyInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button(L("打开控制台", "Open console")) {
                            if let url = URL(string: "https://console.anthropic.com/settings/keys") {
                                NSWorkspace.shared.open(url)
                            }
                        }
                        .controlSize(.small)
                    }
                    if !keyNote.isEmpty {
                        Text(keyNote)
                            .font(.system(size: 11))
                            .foregroundColor(keyNoteColor)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Text(L("现在跳过也可以——之后在 设置 → 凭证 里随时补上。",
                       "Skipping now is fine too — add it anytime later in Settings → Credentials."))
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }
        }
    }

    /// Verify-on-paste: a paste lands as one big onChange — debounce briefly,
    /// then probe. Inner whitespace/newlines from the clipboard are stripped.
    private func keyInputChanged(_ v: String) {
        let key = v.components(separatedBy: .whitespacesAndNewlines).joined()
        if key.isEmpty {
            keyNote = ""
            return
        }
        guard key.count >= 20, key != lastProbedKey else { return }
        keyProbeGen += 1
        let gen = keyProbeGen
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            MainActor.assumeIsolated {
                guard gen == keyProbeGen, !keyProbing else { return }
                verifyAndSaveKey(key)
            }
        }
    }

    /// Probe the key against /v1/models (free GET); save 0600 ONLY when valid.
    /// An invalid key is never stored silently (audit checklist #5).
    private func verifyAndSaveKey(_ key: String) {
        keyProbing = true
        lastProbedKey = key
        setKeyNote(L("验证中…", "Verifying…"), .secondary)
        KeyProbe.anthropic(key: key) { outcome in
            keyProbing = false
            switch outcome {
            case .ok:
                do {
                    try SecretsIO.save(SecretsIO.anthropicFile, token: key)
                    keyInput = ""
                    setKeyNote(L("✅ key 有效,已保存", "✅ Key valid — saved"), .green)
                    engine.detect()   // flips this step to 已连接
                } catch {
                    setKeyNote(L("key 有效,但保存失败: ", "Key valid, but saving failed: ")
                               + error.localizedDescription, .red)
                }
                Analytics.log("wizard_key_validate", fields: ["result": "ok"])
            case .unauthorized:
                setKeyNote(L("❌ key 无效——请到控制台重新生成一个,回来再粘贴",
                             "❌ Invalid key — regenerate one in the console and paste again"), .red)
                Analytics.log("wizard_key_validate", fields: ["result": "unauthorized"])
            case .failed(let why):
                setKeyNote(L("暂时无法验证(网络问题): \(why)——可点「保存」先存下,稍后在 设置 → 凭证 里再验证",
                             "Couldn't verify right now (network): \(why) — click Save to store it and re-verify later in Settings → Credentials"),
                           .orange)
                Analytics.log("wizard_key_validate", fields: ["result": "error"])
            }
        }
    }

    /// Manual Save — for the network-down case where the auto probe couldn't
    /// give a verdict. Stores the key (0600) and re-detects.
    private func saveKeyManually() {
        let key = keyInput.components(separatedBy: .whitespacesAndNewlines).joined()
        guard !key.isEmpty else { return }
        do {
            try SecretsIO.save(SecretsIO.anthropicFile, token: key)
            keyInput = ""
            setKeyNote(L("已保存(未验证)——之后可在 设置 → 凭证 里点「验证」",
                         "Saved (unverified) — you can Verify later in Settings → Credentials"), .secondary)
            engine.detect()
        } catch {
            setKeyNote(L("保存失败: ", "Save failed: ") + error.localizedDescription, .red)
        }
    }

    private func setKeyNote(_ text: String, _ color: Color) {
        keyNote = text
        keyNoteColor = color
    }

    // MARK: step 3 — system permissions

    private var permissionsStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepTitle(L("系统权限", "System permissions"),
                      L("需要用户亲手点的只有这几个系统开关。状态实时刷新——授权完成会自动变绿。",
                        "These system switches are the only things macOS requires you to click yourself. Statuses refresh live — rows turn green as you grant them."))
            CapabilityRowsView(model: perms)
            TelemetryBlockView()
        }
    }

    // MARK: step 4 — recording consent

    private var recordingStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepTitle(L("屏幕记录", "Screen recording"),
                      L("这是助手的核心数据来源。先看清楚采集什么、去哪里、留多久,再决定。",
                        "This is the assistant's core data source. See what is captured, where it goes and how long it stays — then decide."))
            RecordingConsentSection(model: perms)
        }
    }

    // MARK: step 5 — vault / notes folder

    private var vaultStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepTitle(L("笔记放在哪里?", "Where should notes live?"),
                      L("屏幕记录提炼出的笔记存在这里,雷达也从这里发现待办。检测到的 Obsidian vault 已列出——不用 Obsidian 也完全没问题。",
                        "Distilled notes live here, and the radar scans it for asks. Obsidian vaults found on this Mac are listed — not using Obsidian is perfectly fine."))
            ForEach(registeredVaults, id: \.self) { v in
                vaultRow(root: v,
                         title: (v as NSString).lastPathComponent,
                         subtitle: v,
                         badge: "Obsidian vault",
                         chooser: false)
            }
            vaultRow(root: customRoot,
                     title: L("不用 Obsidian — 存成普通 Markdown 文件夹", "No Obsidian — plain markdown folder"),
                     subtitle: customRoot,
                     badge: nil,
                     chooser: true)
            Text(L("会在所选位置自动创建 4 个标准子目录(1 - unprocessed / 2 - raw / 3 - change-summary / 4 - wiki);之后可在 设置 → Obsidian Vault 位置 修改。",
                   "Four standard subfolders are created inside (1 - unprocessed / 2 - raw / 3 - change-summary / 4 - wiki); changeable later in Settings → Obsidian Vault location."))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !vaultError.isEmpty {
                Text(vaultError)
                    .font(.system(size: 11))
                    .foregroundColor(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func vaultRow(root: String, title: String, subtitle: String,
                          badge: String?, chooser: Bool) -> some View {
        Button {
            vaultRoot = root
        } label: {
            HStack(spacing: 10) {
                Image(systemName: vaultRoot == root ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 16))
                    .foregroundColor(vaultRoot == root ? .accentColor : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(title)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(.primary)
                        if let badge {
                            Text(badge)
                                .font(.system(size: 9, weight: .semibold))
                                .padding(.horizontal, 5)
                                .padding(.vertical, 1)
                                .background(Color.purple.opacity(0.15))
                                .clipShape(Capsule())
                        }
                    }
                    Text(subtitle)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                if chooser {
                    Button(L("选择…", "Choose…")) { chooseCustomFolder() }
                        .controlSize(.small)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(vaultRoot == root
                        ? Color.accentColor.opacity(0.10) : Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// Prefill from the current effective config (override → config.yaml →
    /// built-in default) so re-runs land on the value already in use.
    private func loadVaultChoices() {
        registeredVaults = ObsidianVaults.registered()
        let raw = SettingsIO.resolvedPath(
            overrideKey: "obsidian_raw", configKey: "obsidian_raw",
            fallback: "~/Documents/Obsidian Vault/2 - raw")
            ?? ("~/Documents/Obsidian Vault/2 - raw" as NSString).expandingTildeInPath
        let root = (raw as NSString).deletingLastPathComponent
        vaultRoot = root
        customRoot = registeredVaults.contains(root)
            ? ("~/Documents/AI Assistant Notes" as NSString).expandingTildeInPath
            : root
    }

    private func chooseCustomFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.prompt = L("选择", "Choose")
        if panel.runModal() == .OK, let url = panel.url {
            customRoot = url.path
            vaultRoot = url.path
        }
    }

    /// Create the pipeline dirs and diff-write the "obsidian_raw" override
    /// (an unchanged wizard run never clobbers a config.yaml-level setting) —
    /// ObsidianVaultSetup is shared with 设置 → Obsidian Vault 位置.
    /// Returns false (with the error rendered on the step) when the apply
    /// failed — the wizard must never carry an unapplied choice forward.
    private func applyVaultChoice() -> Bool {
        let root = vaultRoot.trimmingCharacters(in: .whitespaces)
        guard !root.isEmpty else { return true }
        do {
            let changed = try ObsidianVaultSetup.apply(root: root)
            vaultError = ""
            if changed {
                Analytics.log("wizard_vault_set", fields: [
                    "obsidian": registeredVaults.contains(root)])
            }
            return true
        } catch {
            vaultError = L("保存失败（磁盘或权限问题），这个位置没有生效——换一个位置或修复后再点「下一步」：",
                           "Save failed (disk or permissions); this location was not applied — pick another or fix it, then click Next again: ")
                + error.localizedDescription
            Analytics.log("wizard_vault_set_failed")
            return false
        }
    }

    // MARK: step 6 — finale (live health check)

    private var finaleStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepTitle(L("最后检查", "Final check"),
                      L("逐项确认系统真的能跑起来。红色的行都带一个修复按钮——绿完为止。",
                        "Confirming the system actually runs. Every red row has one fix button — go until it's all green."))
            VStack(alignment: .leading, spacing: 8) {
                permissionHealthRow
                engineHealthRow
                daemonHealthRow
                dataHealthRow
                cronFDAHealthRow
                if rec.mode != "off" {
                    recordingHealthRow
                }
            }
            if !probe.fixNote.isEmpty {
                Text(probe.fixNote)
                    .font(.system(size: 11))
                    .foregroundColor(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if allGreen {
                card {
                    Text(L("🎉 一切就绪!", "🎉 All set!"))
                        .font(.system(size: 15, weight: .semibold))
                    Text(L("点「完成」后我会指给你看菜单栏里的图标——助手就住在那里。",
                           "Click Done and I'll point at the menu-bar icon — that's where the assistant lives."))
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                }
            }
            // sibling feature (Settings → 导入 Claude Code 工作): link only —
            // the wizard itself never imports anything.
            HStack(spacing: 8) {
                Button(L("导入 Claude Code 已有工作…", "Import existing Claude Code work…")) {
                    Analytics.log("wizard_import_link")
                    MainNav.shared.pendingAnchor = "claude_import"
                    MainNav.shared.section = .settings
                    (NSApp.delegate as? AppDelegate)?.openMainWindow(nil)
                }
                .controlSize(.small)
                Text(L("(可选:把你在终端里跑过的 Claude 会话变成卡片)",
                       "(optional: turn Claude sessions you ran in Terminal into cards)"))
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }
        }
    }

    private var allGreen: Bool {
        let permOK = rec.mode == "off" || perms.screen == .granted
        let recOK = rec.mode == "off" || rec.engineRunning
        // no probe data yet is normal minutes after install (row shows "—");
        // a KNOWN-bad probe must block the 🎉 — an all-green lie is worse
        // than an honest wait (audit 3.1).
        return permOK && engine.detection.ready
            && probe.actdLoaded == true && probe.dashboardExists == true && recOK
            && !cronFDABad
    }

    /// Probe says cron is blocked right now, or the chain visibly stopped
    /// (probe exists but is stale). Missing probe = not bad, just unknown.
    private var cronFDABad: Bool {
        guard let cp = probe.cronProbe else { return false }
        if cp.isBlocked { return true }
        if let ts = cp.ts, Date().timeIntervalSince(ts) > 2 * 3600 { return true }
        return false
    }

    private enum HealthState { case checking, ok, fail, neutral }

    private var permissionHealthRow: some View {
        let state: HealthState
        let detail: String
        if rec.mode == "off" {
            state = .neutral
            detail = L("已选择暂不录制——之后可回上一步或在 设置 → 录制 打开",
                       "You chose not to record — enable later in the previous step or Settings → Recording")
        } else if perms.screen == .granted {
            state = .ok
            detail = L("已授权", "Granted")
        } else {
            state = .fail
            detail = L("未授权——没有它录不到任何内容", "Not granted — nothing can be captured without it")
        }
        return healthRow(state, name: L("屏幕录制权限", "Screen Recording permission"),
                         detail: detail,
                         fixLabel: state == .fail ? L("去授权", "Grant…") : nil) {
            perms.requestScreen()
        }
    }

    private var engineHealthRow: some View {
        let state: HealthState = engine.checking ? .checking
            : (engine.detection.ready ? .ok : .fail)
        let detail = engine.checking ? L("检测中…", "Detecting…")
            : engine.detection.ready
                ? (engine.detection.auth.map { $0.label } ?? "")
                : L("没有 AI 引擎,提案永远不会被执行", "Without an AI engine, proposals will never be executed")
        return healthRow(state, name: L("AI 引擎", "AI engine"), detail: detail,
                         fixLabel: state == .fail ? L("去配置", "Configure…") : nil) {
            setStep(.engine)
        }
    }

    private var daemonHealthRow: some View {
        let state: HealthState = probe.actdLoaded == nil ? .checking
            : (probe.actdLoaded == true ? .ok : .fail)
        let detail = probe.actdLoaded == nil ? L("检测中…", "Checking…")
            : probe.actdLoaded == true
                ? L("在后台运行,几秒内处理你的每个操作", "Running in the background, handling your actions within seconds")
                : L("没有运行——批准的卡片不会被执行", "Not running — approved cards won't execute")
        return healthRow(state, name: L("后台服务", "Background service"), detail: detail,
                         fixLabel: state == .fail
                             ? (probe.fixingDaemon ? L("启动中…", "Starting…") : L("启动后台服务", "Start it"))
                             : nil) {
            if !probe.fixingDaemon { probe.startBackgroundService() }
        }
    }

    private var dataHealthRow: some View {
        let state: HealthState = probe.dashboardExists == nil ? .checking
            : (probe.dashboardExists == true ? .ok : .fail)
        let detail = probe.dashboardExists == nil ? L("检测中…", "Checking…")
            : probe.dashboardExists == true
                ? (probe.dashboardAgo.isEmpty
                   ? L("已生成", "Generated")
                   : L("已生成(", "Generated (") + probe.dashboardAgo + ")")
                : L("还没有——后台服务启动后约 10 秒自动生成", "Not yet — appears ~10 s after the background service starts")
        return healthRow(state, name: L("首次数据", "First data"), detail: detail,
                         fixLabel: state == .fail
                             ? (probe.seeding ? L("生成中…", "Seeding…") : L("立即生成一次", "Generate now"))
                             : nil) {
            if !probe.seeding { probe.seedDashboard() }
        }
    }

    /// §25 cron Full-Disk-Access row (audit 3.1) — ground truth is
    /// state/cron_probe.json, written by REAL cron runs (an in-app probe
    /// would use the app's own grant and lie). Right after a fresh install
    /// there is no data yet — that's a calm "—", not a failure.
    private var cronFDAHealthRow: some View {
        let state: HealthState
        let detail: String
        var fixLabel: String?
        var fix: () -> Void = { CronFDA.beginGrant() }
        if !probe.cronProbeChecked {
            state = .checking
            detail = L("检测中…", "Checking…")
        } else if let cp = probe.cronProbe {
            if cp.isBlocked {
                state = .fail
                detail = FailureCatalog.message("cron_fda_blocked") ?? ""
                fixLabel = L("去授权", "Grant…")
            } else if let ts = cp.ts, Date().timeIntervalSince(ts) > 2 * 3600 {
                state = .fail
                detail = L("最近一次定时任务在 \(Int(Date().timeIntervalSince(ts) / 3600)) 小时前——它可能停跑了",
                           "Last scheduled run was \(Int(Date().timeIntervalSince(ts) / 3600))h ago — the jobs may have stopped")
                fixLabel = L("查看诊断", "Diagnostics")
                fix = {
                    MainNav.shared.section = .deps
                    (NSApp.delegate as? AppDelegate)?.openMainWindow(nil)
                }
            } else {
                state = .ok
                detail = L("定时任务能读取你的数据", "The scheduled jobs can read your data")
            }
        } else {
            state = .neutral
            detail = L("还没有数据——定时任务首次运行（约 30 分钟内）后自动出现，现在可以先点「完成」",
                       "No data yet — appears after the first scheduled run (within ~30 min); you can finish now")
        }
        return healthRow(state, name: L("定时任务磁盘权限", "Cron disk access"),
                         detail: detail, fixLabel: fixLabel, fix: fix)
    }

    private var recordingHealthRow: some View {
        // first-run npm download reads as calm progress, never as a failure
        let downloading = rec.diagnosis?.failureId == "engine_npm_download"
        let state: HealthState = downloading ? .checking
            : (rec.engineRunning ? .ok : .fail)
        let detail = downloading
            ? (FailureCatalog.message("engine_npm_download") ?? "")
            : rec.engineRunning
            ? (rec.mode == "screen_audio" ? L("录制中(屏幕+音频)", "Recording (screen + audio)")
                                          : L("录制中(仅屏幕)", "Recording (screen only)"))
            : (rec.diagnosis.flatMap { FailureCatalog.message($0.failureId) }
               ?? L("未在录制——首次启动要下载引擎,可能需要几分钟", "Not recording — the first start downloads the engine and can take a few minutes"))
        return healthRow(state, name: L("录制引擎", "Capture engine"), detail: detail,
                         fixLabel: state == .fail ? L("启动引擎", "Start engine") : nil) {
            if !RecordingController.hasScreenPermission() {
                RecordingController.requestScreenPermission()
            }
            rec.restartEngine()
        }
    }

    private func healthRow(_ state: HealthState, name: String, detail: String,
                           fixLabel: String?, fix: @escaping () -> Void) -> some View {
        HStack(alignment: .center, spacing: 10) {
            switch state {
            case .checking:
                ProgressView().controlSize(.small).frame(width: 16)
            case .ok:
                Text("✅").font(.system(size: 14))
            case .fail:
                Text("❌").font(.system(size: 14))
            case .neutral:
                Text("—").font(.system(size: 14)).foregroundColor(.secondary)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.system(size: 13, weight: .medium))
                Text(detail)
                    .font(.system(size: 11))
                    .foregroundColor(state == .fail ? .orange : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if let fixLabel {
                Button(fixLabel, action: fix)
                    .controlSize(.small)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - hello bubble ("我在菜单栏" — the wizard's parting arrow)

struct HelloBubbleView: View {
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("我在这里 👆", "I live up here 👆"))
                .font(.system(size: 15, weight: .semibold))
            Text(L("助手住在菜单栏。点图标看提案卡片,顶部输入框随时捕获一句话任务。",
                   "The assistant lives in the menu bar. Click the icon for proposal cards; the capture field on top takes a one-line task anytime."))
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(L("知道了", "Got it")) { dismiss() }
                    .controlSize(.small)
            }
        }
        .padding(14)
        .frame(width: 300)
    }
}
