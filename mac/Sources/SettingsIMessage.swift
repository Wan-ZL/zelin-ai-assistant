// SettingsIMessage.swift — 设置 · iPhone 联动（iMessage 手机通道，CONTRACT §13/§15）
//
// Novice-first in-app setup for the iMessage phone channel: one toggle + one
// handle field replaces the whole manual docs/IMESSAGE_SETUP.md path.
//
// What this section owns (immediate writes, independent of the form's Save):
// - settings_overrides.json keys `phone_channel` ("imessage"|"none") and
//   `imessage_self_handle` — both already in config.py _OVERRIDE_FIELDS.
// - Rendering + loading/unloading the launchd radar agent, mirroring
//   install.sh step 5 (same placeholder substitutions, same unload-first).
//
// Honesty note on the Full Disk Access probe: macOS TCC attributes file
// access to the RESPONSIBLE process. A python subprocess spawned by this app
// would be checked against the APP's FDA grant, not against the grant of the
// python binary that launchd runs — so an in-app "test-read chat.db" probe
// would lie in both directions. The truthful signal is the radar's own
// runtime result: state/radar_health.json's "imessage" entry (written every
// pass by act/radar_imessage.py), refreshed on demand via `launchctl
// kickstart`. That is what the status rows below display.

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

@MainActor
final class IMessageSettingsModel: ObservableObject {
    nonisolated static let agentLabel = "com.zelin.aiassistant.imessageradar"

    @Published var enabled = false
    @Published var handle = ""
    @Published var handleNote = ""
    @Published var handleNoteIsError = false
    @Published var busy = false               // toggle / launchctl in flight
    @Published var statusNote = ""
    // launchd agent
    @Published var agentLoaded: Bool? = nil   // nil = not checked yet
    // radar health (state/radar_health.json "imessage" entry — ground truth)
    @Published var healthHasData = false
    @Published var lastOK: String? = nil
    @Published var lastAttempt: String? = nil
    @Published var skipReason: String? = nil
    @Published var pollRunning = false
    // test message
    @Published var testStatus = ""
    @Published var testFailed = false
    @Published var testRunning = false
    // FDA guidance
    @Published var pythonPath = ""            // resolved REAL interpreter binary
    @Published var copiedPath = false

    private var loaded = false
    private var copyFadeGen = 0

    func loadIfNeeded() {
        guard !loaded else { refreshStatus(); return }
        loaded = true
        let ov = SettingsIO.readOverrides()
        let channel = (ov["phone_channel"] as? String)
            ?? SettingsIO.configScalar("phone_channel") ?? "none"
        enabled = (channel == "imessage")
        handle = (ov["imessage_self_handle"] as? String)
            ?? SettingsIO.configScalar("self_handle") ?? ""
        refreshStatus()
    }

    // MARK: toggle

    func setEnabled(_ on: Bool) {
        guard !busy else { return }
        if on {
            let t = handle.trimmingCharacters(in: .whitespaces)
            if let err = Self.validateHandle(t) {
                // toggle snaps back (source of truth `enabled` unchanged)
                handleNote = err
                handleNoteIsError = true
                return
            }
            handle = t
            guard writeOverrides(channel: "imessage", handle: t) else { return }
            enabled = true
            busy = true
            statusNote = L("正在开启并安装后台雷达…", "Enabling — installing the background radar…")
            Analytics.log("mw_imessage_toggle", fields: ["on": true])
            DispatchQueue.global(qos: .userInitiated).async {
                let (ok, msg) = Self.installAgent()
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        self.busy = false
                        self.statusNote = ok
                            ? L("已开启 ✓ 后台雷达每 3 分钟看一次你的「给自己发消息」对话。下面的状态几秒后会更新。",
                                "Enabled ✓ The background radar checks your \"message yourself\" thread every 3 minutes. Status below updates in a few seconds.")
                            : msg
                        // RunAtLoad runs one pass right away — give it a moment
                        self.refreshStatus(afterDelay: 4)
                    }
                }
            }
        } else {
            guard writeOverrides(channel: "none", handle: nil) else { return }
            enabled = false
            busy = true
            statusNote = L("正在关闭并卸载后台雷达…", "Disabling — removing the background radar…")
            Analytics.log("mw_imessage_toggle", fields: ["on": false])
            DispatchQueue.global(qos: .userInitiated).async {
                Self.removeAgent()
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        self.busy = false
                        self.statusNote = L("已关闭。后台雷达已卸载；随时可以再打开。",
                                            "Disabled. The background radar was removed; re-enable anytime.")
                        self.refreshStatus()
                    }
                }
            }
        }
    }

    // MARK: handle

    func saveHandle() {
        let t = handle.trimmingCharacters(in: .whitespaces)
        if let err = Self.validateHandle(t) {
            handleNote = err
            handleNoteIsError = true
            return
        }
        handle = t
        guard writeOverrides(channel: nil, handle: t) else { return }
        // status language aligned with the credentials rows (v0.14): a format
        // check is not delivery verification — say so, and point at the real
        // verification actions.
        handleNote = enabled
            ? L("已保存 ✓（格式有效）下一轮（≤3 分钟）生效；点「立即测试一轮」或「发送测试消息」可真实验证。",
                "Saved ✓ (format valid) Takes effect next round (≤3 min) — verify for real with \"Test one round now\" or \"Send test message\".")
            : L("已保存 ✓（格式有效，尚未真实验证——开启开关后可发送测试消息）",
                "Saved ✓ (format valid; not verified yet — enable the toggle to send a test message)")
        handleNoteIsError = false
        Analytics.log("mw_imessage_handle_save")
    }

    /// nil = ok; otherwise a plain-language fix message.
    nonisolated static func validateHandle(_ raw: String) -> String? {
        let s = raw.trimmingCharacters(in: .whitespaces)
        if s.isEmpty {
            return L("请先填写你自己的手机号（如 +14155551234）或 Apple ID 邮箱。",
                     "Enter your own phone number (e.g. +14155551234) or Apple ID email first.")
        }
        if s.contains("@") {
            let parts = s.split(separator: "@")
            if parts.count == 2, !parts[0].isEmpty, parts[1].contains("."),
               !parts[1].hasPrefix("."), !parts[1].hasSuffix(".") { return nil }
            return L("邮箱格式不对——例：you@icloud.com", "That email doesn't look right — e.g. you@icloud.com")
        }
        if s.hasPrefix("+") {
            let digits = s.dropFirst()
            if (7...15).contains(digits.count), digits.allSatisfy({ $0.isNumber }) { return nil }
            return L("手机号要用国际格式：+国家码+号码，例：+14155551234",
                     "Use the international format: +countrycode number, e.g. +14155551234")
        }
        if s.allSatisfy({ $0.isNumber || $0 == " " || $0 == "-" || $0 == "(" || $0 == ")" }) {
            return L("手机号前面要加国家码：美国 +1、中国 +86，例：+14155551234",
                     "Add the country code first — +1 for US, +86 for China, e.g. +14155551234")
        }
        return L("填手机号（+ 开头）或 Apple ID 邮箱。", "Enter a phone number (starting with +) or an Apple ID email.")
    }

    /// Read-merge-write into settings_overrides.json (same landmine as the
    /// main form: writeOverrides REPLACES the file). Returns false on error.
    @discardableResult
    private func writeOverrides(channel: String?, handle: String?) -> Bool {
        var merged = SettingsIO.readOverrides()
        if let c = channel { merged["phone_channel"] = c }
        if let h = handle { merged["imessage_self_handle"] = h }
        do {
            try SettingsIO.writeOverrides(merged)
            return true
        } catch {
            statusNote = L("保存设置失败: ", "Failed to save settings: ") + error.localizedDescription
            return false
        }
    }

    // MARK: status

    func refreshStatus(afterDelay delay: TimeInterval = 0) {
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + delay) {
            let loadedNow = Self.isAgentLoaded()
            let health = Self.readHealth()
            let py = Self.realBinary(of: Self.runtimePython())
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.agentLoaded = loadedNow
                    self.healthHasData = health != nil
                    self.lastOK = health?["last_ok"] as? String
                    self.lastAttempt = health?["last_attempt"] as? String
                    self.skipReason = health?["skip_reason"] as? String
                    self.pythonPath = py
                }
            }
        }
    }

    /// 「立即测试一轮」— kickstart the launchd agent (runs a real radar pass
    /// in the radar's own TCC context), then re-read the health file.
    func pollNow() {
        guard !pollRunning else { return }
        pollRunning = true
        Analytics.log("mw_imessage_kickstart")
        DispatchQueue.global(qos: .userInitiated).async {
            _ = Shell.run("/bin/launchctl",
                          ["kickstart", "gui/\(getuid())/\(Self.agentLabel)"])
            // one pass = open db + scan; comfortably done within a few seconds
            Thread.sleep(forTimeInterval: 6)
            let health = Self.readHealth()
            let loadedNow = Self.isAgentLoaded()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.pollRunning = false
                    self.agentLoaded = loadedNow
                    self.healthHasData = health != nil
                    self.lastOK = health?["last_ok"] as? String
                    self.lastAttempt = health?["last_attempt"] as? String
                    self.skipReason = health?["skip_reason"] as? String
                }
            }
        }
    }

    /// Reinstall + reload the agent without touching the toggle (repair path).
    func reinstallAgent() {
        guard !busy else { return }
        busy = true
        statusNote = L("正在重新安装后台雷达…", "Reinstalling the background radar…")
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, msg) = Self.installAgent()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.busy = false
                    self.statusNote = ok ? L("已重新安装 ✓", "Reinstalled ✓") : msg
                    self.refreshStatus(afterDelay: 3)
                }
            }
        }
    }

    // MARK: test message

    func sendTest() {
        guard !testRunning else { return }
        let t = handle.trimmingCharacters(in: .whitespaces)
        if let err = Self.validateHandle(t) {
            testStatus = err
            testFailed = true
            return
        }
        testRunning = true
        testFailed = false
        testStatus = L("发送中…（第一次会弹「控制 Messages」授权，请点允许）",
                       "Sending… (first time macOS asks to control Messages — click Allow)")
        let text = L("🤖 测试消息：iPhone 联动已打通！之后审批通知也会出现在这里。",
                     "🤖 Test message: iPhone link works! Approval notifications will appear here too.")
        DispatchQueue.global(qos: .userInitiated).async {
            let (code, tail) = Self.runSendTest(handle: t, text: text)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.testRunning = false
                    self.testFailed = code != 0
                    Analytics.log("mw_imessage_test_send", fields: ["ok": code == 0])
                    if code == 0 {
                        self.testStatus = L("已发送 ✓ 打开 Messages（这台 Mac 或手机）就能看到。",
                                            "Sent ✓ Check Messages on this Mac or your phone.")
                    } else {
                        let detail = tail.trimmingCharacters(in: .whitespacesAndNewlines)
                        self.testStatus = L("发送失败。常见原因：Messages 没登录 iMessage；「控制 Messages」授权被拒（系统设置→隐私与安全性→自动化 里重新允许）；号码/邮箱和 Messages 里的不一致。",
                                            "Send failed. Common causes: Messages isn't signed in to iMessage; the \"control Messages\" consent was denied (re-allow under System Settings → Privacy & Security → Automation); or the handle doesn't match Messages.")
                            + (detail.isEmpty ? "" : "\n" + detail.suffix(200))
                    }
                }
            }
        }
    }

    /// Same osascript path the radar uses: run act.radar_imessage's send
    /// runner via the pinned runtime python, so import/config problems and
    /// the real osascript error both surface in the output tail.
    nonisolated private static func runSendTest(handle: String, text: String) -> (Int32, String) {
        let py = runtimePython()
        let root = AppPaths.stateRoot
        let code = """
        import subprocess, sys
        from act import radar_imessage as r
        p = r._default_send_runner(sys.argv[1], sys.argv[2])
        sys.stderr.write((p.stderr or "") + (p.stdout or ""))
        sys.exit(p.returncode)
        """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = ["-c", code, handle, text]
        p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        env["AIASSISTANT_HOME"] = root
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (127, error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String((String(data: data, encoding: .utf8) ?? "").suffix(400)))
    }

    // MARK: launchd plumbing (mirrors install.sh step 5)

    nonisolated private static var plistDest: String {
        NSHomeDirectory() + "/Library/LaunchAgents/\(agentLabel).plist"
    }

    /// Render + load via the shared launchd helper (Doctor.swift LaunchAgents —
    /// same 4 placeholder substitutions as install.sh render_launchd_plist).
    nonisolated static func installAgent() -> (Bool, String) {
        LaunchAgents.install(label: agentLabel)
    }

    /// Unload + remove — same as install.sh's feature-off branch.
    nonisolated static func removeAgent() {
        let dest = plistDest
        _ = Shell.run("/bin/launchctl", ["unload", dest])
        try? FileManager.default.removeItem(atPath: dest)
    }

    nonisolated static func isAgentLoaded() -> Bool {
        Shell.run("/bin/launchctl", ["print", "gui/\(getuid())/\(agentLabel)"]).0 == 0
    }

    /// state/radar_health.json "imessage" entry; nil = no data yet.
    nonisolated static func readHealth() -> [String: Any]? {
        let path = AppPaths.stateRoot + "/state/radar_health.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return nil }
        return obj["imessage"] as? [String: Any]
    }

    /// The interpreter launchd runs: config/runtime.json pointer (CONTRACT
    /// §19) → login-shell `command -v python3` → /usr/bin/python3.
    nonisolated static func runtimePython() -> String {
        let p = AppPaths.stateRoot + "/config/runtime.json"
        if let data = FileManager.default.contents(atPath: p),
           let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let py = obj["python"] as? String, !py.isEmpty {
            return py
        }
        let (code, out) = Shell.run("/bin/zsh", ["-lc", "command -v python3"])
        let lines = out.trimmingCharacters(in: .whitespacesAndNewlines)
            .components(separatedBy: "\n")
        if code == 0, let found = lines.last, found.hasPrefix("/") { return found }
        return "/usr/bin/python3"
    }

    /// FDA must be granted to the REAL binary — resolve symlinks (miniconda's
    /// python3 usually symlinks python3.x).
    nonisolated static func realBinary(of path: String) -> String {
        URL(fileURLWithPath: path).resolvingSymlinksInPath().path
    }

    // MARK: copy helper

    func copyPythonPath() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(pythonPath, forType: .string)
        copyFadeGen += 1
        let gen = copyFadeGen
        copiedPath = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            MainActor.assumeIsolated {
                if self.copyFadeGen == gen { self.copiedPath = false }
            }
        }
    }
}

// MARK: - View

struct IMessageSettingsSection: View {
    @StateObject private var model = IMessageSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("iPhone 联动（iMessage）", "iPhone via iMessage"))
                .font(.system(size: 13, weight: .semibold))
            Text(L("把 Messages 里「给自己发消息」的对话变成手机遥控器：在手机上回「批准 R-xxx」等指令、随手记一句想法，通知也会镜像成 iMessage（点 👍/❤️ 即批准）。只会给你自己发消息，绝不会发给别人。此区改动即时生效，无需点下方「保存」。",
                   "Turns your \"message yourself\" thread in Messages into a phone remote: reply \"approve R-xxx\", jot quick thoughts, and notifications mirror as iMessages (tap 👍/❤️ to approve). Messages only ever go to yourself — never to anyone else. Changes here apply immediately; no need to press Save below."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Toggle(L("启用 iPhone 联动", "Enable iPhone via iMessage"), isOn: Binding(
                get: { model.enabled },
                set: { model.setEnabled($0) }))
                .toggleStyle(.switch)
                .disabled(model.busy)

            HStack(spacing: 8) {
                Text(L("自己的手机号或 Apple ID", "Your phone number or Apple ID"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                TextField(L("例：+14155551234 或 you@icloud.com", "e.g. +14155551234 or you@icloud.com"),
                          text: $model.handle)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .onSubmit { model.saveHandle() }
                Button(L("保存", "Save")) { model.saveHandle() }
                    .controlSize(.small)
            }
            if !model.handleNote.isEmpty {
                Text(model.handleNote)
                    .font(.system(size: 10))
                    .foregroundColor(model.handleNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(L("要和 Messages 里「给自己发消息」对话的收件人完全一致。还没有这个对话？在 Messages 新建对话、收件人填你自己，随便发一条。",
                   "Must exactly match the recipient of your \"message yourself\" thread. Don't have one? In Messages, start a new conversation addressed to yourself and send anything."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if !model.statusNote.isEmpty {
                HStack(spacing: 6) {
                    if model.busy { ProgressView().controlSize(.small) }
                    Text(model.statusNote)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if model.enabled {
                Divider()
                agentRow
                healthRow
                fdaBlock
                Divider()
                testRow
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .font(.system(size: 12))
        .onAppear { model.loadIfNeeded() }
        // status texts are rebuilt per render, so a language switch is enough
        .onChange(of: i18n.lang) { _, _ in model.refreshStatus() }
    }

    // MARK: rows

    private var agentRow: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.agentLoaded == true ? Color.green
                      : model.agentLoaded == false ? Color.orange : Color.secondary.opacity(0.4))
                .frame(width: 8, height: 8)
            Text(L("后台雷达", "Background radar"))
                .font(.system(size: 12, weight: .medium))
            Text(model.agentLoaded == true
                 ? L("已安装，每 3 分钟自动运行", "installed — runs every 3 minutes")
                 : model.agentLoaded == false
                 ? L("未安装", "not installed")
                 : L("检查中…", "checking…"))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            Spacer()
            if model.agentLoaded == false {
                Button(L("重新安装", "Reinstall")) { model.reinstallAgent() }
                    .controlSize(.small)
                    .disabled(model.busy)
            }
        }
    }

    private var healthRow: some View {
        let (color, text) = healthSummary()
        return HStack(spacing: 8) {
            Circle().fill(color).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(L("运行状态（真实轮询结果）", "Run status (real poll results)"))
                    .font(.system(size: 12, weight: .medium))
                Text(text)
                    .font(.system(size: 11))
                    .foregroundColor(color == .green ? .secondary : color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Button(model.pollRunning ? L("测试中…", "Testing…") : L("立即测试一轮", "Test one round now")) {
                model.pollNow()
            }
            .controlSize(.small)
            .disabled(model.pollRunning || model.agentLoaded != true)
            Button(L("刷新", "Refresh")) { model.refreshStatus() }
                .controlSize(.small)
        }
    }

    private func healthSummary() -> (Color, String) {
        guard model.healthHasData else {
            return (.secondary,
                    L("还没有运行记录。等一轮（≤3 分钟）或点「立即测试一轮」。",
                      "No runs recorded yet. Wait one round (≤3 min) or click \"Test one round now\"."))
        }
        let attempt = RelativeTime.since(model.lastAttempt).map {
            L("最近一轮 \($0)", "last round \($0)")
        }
        if let reason = model.skipReason, !reason.isEmpty {
            var s = Self.humanSkip(reason)
            if let a = attempt { s += L("（\(a)）", " (\(a))") }
            let isFDA = reason.hasPrefix("db_open_failed") || reason.hasPrefix("db_read_failed")
            return (isFDA ? .red : .orange, s)
        }
        if let ok = model.lastOK, !ok.isEmpty {
            return (.green, L("运行正常 ✓ 最近成功 ", "Working ✓ last success ")
                    + (RelativeTime.since(ok) ?? ok))
        }
        return (.orange, attempt ?? L("状态未知", "unknown"))
    }

    /// Machine skip_reason → plain-language fix (unknown codes pass through).
    private static func humanSkip(_ r: String) -> String {
        if r.hasPrefix("db_open_failed") || r.hasPrefix("db_read_failed") {
            return L("读不了 Messages 数据库——十有八九是「完全磁盘访问」还没授给下面这个 python，按下方步骤操作",
                     "Can't read the Messages database — almost always Full Disk Access hasn't been granted to the python below; follow the steps beneath")
        }
        if r.hasPrefix("scan_error") { return r }
        switch r {
        case "disabled":
            return L("上一轮运行时开关还没打开——点「立即测试一轮」再看",
                     "The toggle was still off during the last round — click \"Test one round now\"")
        case "no_self_handle":
            return L("还没填手机号/邮箱——在上面填好并点保存",
                     "No phone number / email yet — fill it in above and Save")
        case "db_missing":
            return L("这台 Mac 从没用过 Messages——先打开 Messages 并登录 iMessage",
                     "Messages has never been used on this Mac — open Messages and sign in to iMessage first")
        case "self_chat_not_found":
            return L("找不到「给自己发消息」的对话——在 Messages 给自己（上面填的号码/邮箱）发一条，再测一轮",
                     "Couldn't find your \"message yourself\" thread — send yourself a message (to the handle above) in Messages, then test again")
        default:
            return r
        }
    }

    private var fdaBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("完全磁盘访问（必须，一次性设置）", "Full Disk Access (required, one-time)"))
                .font(.system(size: 12, weight: .medium))
            Text(L("后台雷达由系统直接运行下面这个 python 程序来读 Messages 数据库。macOS 按「哪个程序在读」判权限，所以必须把这个 python 文件本身加进「完全磁盘访问权限」——授权给终端或本 App 都不管用。",
                   "The background radar reads the Messages database via the python program below, run directly by the system. macOS grants access per program — so this exact python file must be added to Full Disk Access; granting Terminal or this app does nothing."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Text(model.pythonPath.isEmpty ? "…" : model.pythonPath)
                    .font(.system(size: 10, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                Button(model.copiedPath ? L("已复制 ✓", "Copied ✓") : L("复制路径", "Copy path")) {
                    model.copyPythonPath()
                }
                .controlSize(.small)
                .disabled(model.pythonPath.isEmpty)
                Spacer()
            }
            Text(L("1. 点「打开系统设置」（直达「完全磁盘访问权限」）\n2. 点列表下方的 “＋”\n3. 按 ⌘⇧G，粘贴刚复制的路径，回车\n4. 选中 python3 → 打开，并确认它的开关是开着的\n5. 回到这里点「立即测试一轮」——上面的状态变绿就成了",
                   "1. Click \"Open System Settings\" (goes straight to Full Disk Access)\n2. Click the \"+\" under the list\n3. Press ⌘⇧G, paste the copied path, press Return\n4. Select python3 → Open, and make sure its switch is ON\n5. Come back and click \"Test one round now\" — green above means done"))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button(L("打开系统设置", "Open System Settings")) {
                if let url = URL(string:
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") {
                    NSWorkspace.shared.open(url)
                }
            }
            .controlSize(.small)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var testRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Button(model.testRunning ? L("发送中…", "Sending…") : L("发送测试消息", "Send test message")) {
                    model.sendTest()
                }
                .disabled(model.testRunning)
                Text(L("给你自己发一条 iMessage，验证发送链路。", "Sends yourself one iMessage to verify the send path."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                Spacer()
            }
            if !model.testStatus.isEmpty {
                Text(model.testStatus)
                    .font(.system(size: 11))
                    .foregroundColor(model.testFailed ? .orange : (model.testRunning ? .secondary : .green))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
    }
}
