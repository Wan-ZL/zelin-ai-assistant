// SettingsGmail.swift — 设置 · Gmail 接入（全程 App 内，CONTRACT §15 Slack/Gmail 设置区）
//
// Kills the config.yaml step from the Gmail happy path (audit 6.13: the doc
// used to send people editing sources.gmail.address by hand):
//   ① [打开 Google 应用专用密码页] — direct link to myaccount.google.com/apppasswords
//     (+ the 2-Step-Verification prerequisite and the Workspace-admin caveat,
//     stated up front instead of buried in docs)
//   ② Gmail address — plain field, persists to the `gmail_address` override
//   ③ app password — reuses CredentialRowView(kind:.gmail): whitespace-strip
//     on save + a REAL IMAP LOGIN probe through the runtime python, with the
//     Workspace-admin telltale spelled out on auth failure.
// The enable toggle writes the `gmail_enabled` override and renders + loads
// the gmailradar launchd agent via the shared Doctor.swift LaunchAgents
// helper; the status rows read state/radar_health.json's "gmail" entry
// (ground truth — same honesty rule as the iMessage section).
//
// Like the iMessage section, everything here persists immediately.

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

@MainActor
final class GmailSettingsModel: ObservableObject {
    nonisolated static let agentLabel = "com.zelin.aiassistant.gmailradar"

    @Published var enabled = true
    @Published var busy = false
    @Published var statusNote = ""
    @Published var address = ""
    @Published var addressNote = ""
    @Published var addressNoteIsError = false
    // §14bis 抓取方式：false = A 应用专用密码(IMAP)，true = B 自定义抓取命令。
    // 生效判据与管线一致：gmail_fetch_command override 非空 ⇔ B 在跑。
    @Published var useCommand = false
    @Published var fetchCommand = ""
    @Published var fetchCommandNote = ""
    @Published var fetchCommandNoteIsError = false
    // launchd agent + radar health (state/radar_health.json "gmail")
    @Published var agentLoaded: Bool? = nil
    @Published var healthHasData = false
    @Published var lastOK: String? = nil
    @Published var lastAttempt: String? = nil
    @Published var skipReason: String? = nil
    @Published var pollRunning = false

    private var loaded = false

    func loadIfNeeded() {
        guard !loaded else { refreshStatus(); return }
        loaded = true
        let ov = SettingsIO.readOverrides()
        // effective：§48.1 真源投影（radar_sources.gmail.enabled = flag 与
        // sources.gmail.enabled 的合取）——只读 override 的话，yaml 里
        // enabled:false 或 features.gmail_radar:false 时面板会显示「开启」
        // 而雷达永远静默。投影缺失（actd 还没跑）回退 override → 默认开。
        enabled = DiagnosticsRules.effectiveSourceEnabled(
            projected: DiagnosticsModel.readRadarSources()["gmail"],
            fallback: (ov["gmail_enabled"] as? Bool) ?? true)
        address = (ov["gmail_address"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? SettingsIO.configScalar("address") ?? ""
        // §14bis: override-only read（和 enabled 一样，naive scanner 读不到
        // config.yaml 的两级嵌套；App 写的永远是 override，UI==生效值成立）。
        fetchCommand = (ov["gmail_fetch_command"] as? String) ?? ""
        useCommand = !fetchCommand.trimmingCharacters(in: .whitespaces).isEmpty
        refreshStatus()
    }

    // MARK: address

    func saveAddress() {
        let v = address.trimmingCharacters(in: .whitespaces)
        address = v
        if !v.isEmpty, let err = Self.validateAddress(v) {
            addressNote = err
            addressNoteIsError = true
            return
        }
        var merged = SettingsIO.readOverrides()
        let configLayer = SettingsIO.configScalar("address") ?? ""
        if v.isEmpty || v == configLayer {
            merged.removeValue(forKey: "gmail_address")
        } else {
            merged["gmail_address"] = v
        }
        do {
            try SettingsIO.writeOverrides(merged)
        } catch {
            addressNote = L("保存设置失败: ", "Failed to save settings: ")
                + error.localizedDescription
            addressNoteIsError = true
            return
        }
        addressNote = v.isEmpty
            ? L("已清空（改用 config.yaml 里的地址，如果有）。",
                "Cleared (falls back to the config.yaml address, if any).")
            : L("已保存 ✓ 在下面粘贴应用专用密码即可自动验证整条链路。",
                "Saved ✓ Paste the app password below and the whole path gets verified automatically.")
        addressNoteIsError = false
        Analytics.log("mw_gmail_address_save")
    }

    // MARK: §14bis fetch path (A = app password / B = fetch command)

    /// Picker handler. A: deactivate the command override (IMAP takes over).
    /// B: activate the saved command if there is one, else prompt for it.
    func setUseCommand(_ on: Bool) {
        useCommand = on
        if !on {
            var merged = SettingsIO.readOverrides()
            merged.removeValue(forKey: "gmail_fetch_command")
            do { try SettingsIO.writeOverrides(merged) } catch {
                fetchCommandNote = L("保存设置失败: ", "Failed to save settings: ")
                    + error.localizedDescription
                fetchCommandNoteIsError = true
                return
            }
            fetchCommandNote = L("已切回 A：走应用专用密码通道（抓取命令已停用，命令文本保留着，切回 B 随时恢复）。",
                                 "Back on path A: the app-password channel (the fetch command is deactivated; its text is kept — switch back to B anytime).")
            fetchCommandNoteIsError = false
            Analytics.log("mw_gmail_fetch_path", fields: ["command": false])
            return
        }
        Analytics.log("mw_gmail_fetch_path", fields: ["command": true])
        if fetchCommand.trimmingCharacters(in: .whitespaces).isEmpty {
            fetchCommandNote = L("填好下面的抓取命令并点「保存」即生效。",
                                 "Fill in the fetch command below and click Save to activate.")
            fetchCommandNoteIsError = false
        } else {
            saveFetchCommand()
        }
    }

    /// Diff-write the `gmail_fetch_command` override（非空即赢过 IMAP——见
    /// CONTRACT §14bis；空 = 删键回落 A）。
    func saveFetchCommand() {
        let v = fetchCommand.trimmingCharacters(in: .whitespaces)
        fetchCommand = v
        var merged = SettingsIO.readOverrides()
        if v.isEmpty {
            merged.removeValue(forKey: "gmail_fetch_command")
        } else {
            merged["gmail_fetch_command"] = v
        }
        do {
            try SettingsIO.writeOverrides(merged)
        } catch {
            fetchCommandNote = L("保存设置失败: ", "Failed to save settings: ")
                + error.localizedDescription
            fetchCommandNoteIsError = true
            return
        }
        useCommand = !v.isEmpty
        fetchCommandNote = v.isEmpty
            ? L("已清空——回到 A：应用专用密码通道。", "Cleared — back on path A (app password).")
            : L("已保存 ✓ 下一轮（≤5 分钟）起雷达改走这条命令抓邮件；跑没跑成看下面「运行状态」。",
                "Saved ✓ From the next round (≤5 min) the radar fetches mail via this command; see \"Run status\" below for the truth.")
        fetchCommandNoteIsError = false
        Analytics.log("mw_gmail_fetch_command_save", fields: ["set": !v.isEmpty])
    }

    /// nil = ok; otherwise a plain-language fix message.
    nonisolated static func validateAddress(_ raw: String) -> String? {
        let s = raw.trimmingCharacters(in: .whitespaces)
        let parts = s.split(separator: "@")
        if parts.count == 2, !parts[0].isEmpty, parts[1].contains("."),
           !parts[1].hasPrefix("."), !parts[1].hasSuffix(".") { return nil }
        return L("邮箱格式不对——例：you@gmail.com（公司 Google Workspace 邮箱也可以）",
                 "That email doesn't look right — e.g. you@gmail.com (a Google Workspace address works too)")
    }

    // MARK: enable toggle + launchd agent

    func setEnabled(_ on: Bool) {
        guard !busy else { return }
        // explicit write both ways: the app can't read the two-level-nested
        // config layer, and the toggle IS a user change — the override must
        // guarantee UI == effective (dropping "true" could silently leave a
        // config.yaml `enabled: false` in charge while the switch shows on).
        // 打开时把合取的**两个键**都写 true（§48.1）：只写 gmail_enabled 的
        // 话，yaml 里 features.gmail_radar:false 仍压着雷达——用户显式动作
        // 允许覆盖 yaml。关闭只写 gmail_enabled=false（合取，单键足以关）。
        var merged = SettingsIO.readOverrides()
        merged["gmail_enabled"] = on
        if on {
            var feats = merged["features"] as? [String: Any] ?? [:]
            feats["gmail_radar"] = true
            merged["features"] = feats
        }
        do {
            try SettingsIO.writeOverrides(merged)
        } catch {
            statusNote = L("保存设置失败: ", "Failed to save settings: ")
                + error.localizedDescription
            return
        }
        enabled = on
        busy = true
        Analytics.log("mw_gmail_toggle", fields: ["on": on])
        statusNote = on ? L("正在开启并安装后台雷达…", "Enabling — installing the background radar…")
                        : L("正在关闭并卸载后台雷达…", "Disabling — removing the background radar…")
        DispatchQueue.global(qos: .userInitiated).async {
            var failMsg = ""
            if on {
                let (ok, msg) = LaunchAgents.install(label: Self.agentLabel)
                if !ok { failMsg = msg }
            } else {
                Self.removeAgent()
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.busy = false
                    if on {
                        self.statusNote = failMsg.isEmpty
                            ? L("已开启 ✓ 后台雷达每 5 分钟扫一次收件箱未读（只读，不会标已读）。没存密码时静默待机。",
                                "Enabled ✓ The background radar scans unread inbox mail every 5 minutes (read-only — nothing gets marked read). Without a saved password it idles silently.")
                            : failMsg
                    } else {
                        self.statusNote = L("已关闭。后台雷达已卸载；随时可以再打开。",
                                            "Disabled. The background radar was removed; re-enable anytime.")
                    }
                    self.refreshStatus(afterDelay: on ? 4 : 0)
                }
            }
        }
    }

    nonisolated static func removeAgent() {
        let dest = LaunchAgents.plistDest(agentLabel)
        _ = Shell.run("/bin/launchctl", ["unload", dest])
        try? FileManager.default.removeItem(atPath: dest)
    }

    func reinstallAgent() {
        guard !busy else { return }
        busy = true
        statusNote = L("正在重新安装后台雷达…", "Reinstalling the background radar…")
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, msg) = LaunchAgents.install(label: Self.agentLabel)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.busy = false
                    self.statusNote = ok ? L("已重新安装 ✓", "Reinstalled ✓") : msg
                    self.refreshStatus(afterDelay: 3)
                }
            }
        }
    }

    // MARK: status (agent + radar health)

    func refreshStatus(afterDelay delay: TimeInterval = 0) {
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + delay) {
            let loadedNow = LaunchAgents.isLoaded(label: Self.agentLabel)
            let health = Self.readHealth()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.agentLoaded = loadedNow
                    self.healthHasData = health != nil
                    self.lastOK = health?["last_ok"] as? String
                    self.lastAttempt = health?["last_attempt"] as? String
                    self.skipReason = health?["skip_reason"] as? String
                }
            }
        }
    }

    func pollNow() {
        guard !pollRunning else { return }
        pollRunning = true
        Analytics.log("mw_gmail_kickstart")
        DispatchQueue.global(qos: .userInitiated).async {
            _ = Shell.run("/bin/launchctl",
                          ["kickstart", "gui/\(getuid())/\(Self.agentLabel)"])
            Thread.sleep(forTimeInterval: 8)   // network pass — a touch slower
            let health = Self.readHealth()
            let loadedNow = LaunchAgents.isLoaded(label: Self.agentLabel)
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

    /// state/radar_health.json "gmail" entry; nil = no data yet.
    nonisolated static func readHealth() -> [String: Any]? {
        let path = AppPaths.stateRoot + "/state/radar_health.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return nil }
        return obj["gmail"] as? [String: Any]
    }
}

// MARK: - View

struct GmailSettingsSection: View {
    @StateObject private var model = GmailSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    // Content-only (v0.21): the card / title / collapse chrome is supplied by
    // the shared CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("轮询收件箱里的未读邮件，需要你处理的自动变成提案卡（纯通知/营销直接过滤）。只读——邮件绝不会被标成已读。全部在这里配好，不用改任何文件；此区改动即时生效。",
                   "Polls unread inbox mail and turns the ones needing you into proposal cards (notifications/marketing filtered out). Read-only — mail is never marked read. Everything is set up right here, no files to edit; changes apply immediately."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Toggle(L("启用 Gmail 雷达", "Enable the Gmail radar"), isOn: Binding(
                get: { model.enabled },
                set: { model.setEnabled($0) }))
                .toggleStyle(.switch)
                .disabled(model.busy)

            if !model.statusNote.isEmpty {
                HStack(spacing: 6) {
                    if model.busy { ProgressView().controlSize(.small) }
                    Text(model.statusNote)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            pathPicker
            if model.useCommand {
                commandCard
            } else {
                stepCard
            }

            if model.enabled {
                Divider()
                agentRow
                healthRow
            }
        }
        .font(.system(size: 12))
        .onAppear { model.loadIfNeeded() }
        .onChange(of: i18n.lang) { _, _ in model.refreshStatus() }
    }

    // MARK: §14bis path picker (A app password / B fetch command)

    private var pathPicker: some View {
        VStack(alignment: .leading, spacing: 4) {
            Picker(L("抓取方式", "Fetch path"), selection: Binding(
                get: { model.useCommand },
                set: { model.setUseCommand($0) })) {
                Text(L("A · 应用专用密码（推荐）", "A · App password (recommended)")).tag(false)
                Text(L("B · 自定义抓取命令", "B · Custom fetch command")).tag(true)
            }
            .pickerStyle(.segmented)
            Text(L("公司 Workspace 禁用了应用专用密码时走 B——雷达定时调你自己的命令去抓邮件，抓回来的分诊完全相同。",
                   "Use B when a Workspace admin has disabled app passwords — the radar periodically runs your own command to fetch mail; triage downstream is identical."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            // switching to A hides the command card — its confirmation note
            // must survive the switch, so it renders here on the A side too
            if !model.useCommand, !model.fetchCommandNote.isEmpty {
                Text(model.fetchCommandNote)
                    .font(.system(size: 10))
                    .foregroundColor(model.fetchCommandNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var commandCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("抓取命令（§14bis 契约）", "Fetch command (the §14bis contract)"))
                .font(.system(size: 12, weight: .medium))
            Text(L("雷达每轮直接执行它（不走 shell），环境变量 GMAIL_RADAR_LAST_UID 带上次进度，命令在 stdout 打印一个 JSON 数组：{uid（单调递增）, from, subject, date, message_id, body}。Gmail API 脚本、MCP 客户端都可以。",
                   "The radar executes it directly each round (no shell). $GMAIL_RADAR_LAST_UID carries the progress marker; the command prints a JSON array to stdout: {uid (monotonic), from, subject, date, message_id, body}. A Gmail-API script or an MCP client both qualify."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                TextField(L("例：/Users/you/bin/gmail-fetch.sh", "e.g. /Users/you/bin/gmail-fetch.sh"),
                          text: $model.fetchCommand)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .onSubmit { model.saveFetchCommand() }
                Button(L("保存", "Save")) { model.saveFetchCommand() }
                    .controlSize(.small)
            }
            if !model.fetchCommandNote.isEmpty {
                Text(model.fetchCommandNote)
                    .font(.system(size: 10))
                    .foregroundColor(model.fetchCommandNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    // MARK: guided card

    private var stepCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            // step 1 — generate the app password
            VStack(alignment: .leading, spacing: 4) {
                Text(L("① 生成应用专用密码（一次性，~1 分钟）", "① Generate an app password (one-time, ~1 min)"))
                    .font(.system(size: 12, weight: .medium))
                Text(L("要求账号已开两步验证。页面里 App name 随便填（如 Zelin AI Assistant）→ 创建 → Google 显示 16 位密码（只显示这一次），复制它。",
                       "Requires 2-Step Verification on the account. On the page, any app name works (e.g. Zelin AI Assistant) → Create → Google shows a 16-letter password (only once) — copy it."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button(L("打开 Google 应用专用密码页", "Open Google app passwords")) {
                        NSWorkspace.shared.open(
                            URL(string: "https://myaccount.google.com/apppasswords")!)
                    }
                    .controlSize(.small)
                    Button(L("打不开？先开两步验证", "Page unavailable? Enable 2-Step first")) {
                        NSWorkspace.shared.open(
                            URL(string: "https://myaccount.google.com/signinoptions/two-step-verification")!)
                    }
                    .controlSize(.small)
                }
                Text(L("公司 Google Workspace：页面若显示 “The setting you are looking for is not available for your account”，是管理员禁用了应用专用密码——此路不通，不用再试；你读邮件的画面仍会经屏幕录制链进入系统。",
                       "Company Google Workspace: if the page says \"The setting you are looking for is not available for your account\", the admin has disabled app passwords — this path is closed, don't keep trying; mail you read on screen still reaches the system via the recording pipeline."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            // step 2 — address
            VStack(alignment: .leading, spacing: 4) {
                Text(L("② 填 Gmail 地址", "② Enter your Gmail address"))
                    .font(.system(size: 12, weight: .medium))
                HStack(spacing: 8) {
                    TextField(L("例：you@gmail.com", "e.g. you@gmail.com"), text: $model.address)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12, design: .monospaced))
                        .onSubmit { model.saveAddress() }
                    Button(L("保存", "Save")) { model.saveAddress() }
                        .controlSize(.small)
                }
                if !model.addressNote.isEmpty {
                    Text(model.addressNote)
                        .font(.system(size: 10))
                        .foregroundColor(model.addressNoteIsError ? .orange : .green)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Divider()
            // step 3 — password (verify-on-save credential row; strips spaces,
            // real IMAP LOGIN probe, Workspace telltale spelled out on failure)
            VStack(alignment: .leading, spacing: 4) {
                Text(L("③ 粘贴密码（自动去空格，保存即真实验证）", "③ Paste the password (spaces auto-stripped; a real login verifies it on save)"))
                    .font(.system(size: 12, weight: .medium))
                CredentialRowView(
                    title: L("Gmail 应用密码", "Gmail app password"),
                    secretName: SecretsIO.gmailFile,
                    legacyPath: "~/Desktop/Keys/gmail-app-password.txt",
                    links: [],
                    kind: .gmail)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    // MARK: status rows

    private var agentRow: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.agentLoaded == true ? Color.green
                      : model.agentLoaded == false ? Color.orange : Color.secondary.opacity(0.4))
                .frame(width: 8, height: 8)
            Text(L("后台雷达", "Background radar"))
                .font(.system(size: 12, weight: .medium))
            Text(model.agentLoaded == true
                 ? L("已安装，每 5 分钟自动运行", "installed — runs every 5 minutes")
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
                    L("还没有运行记录。等一轮（≤5 分钟）或点「立即测试一轮」。",
                      "No runs recorded yet. Wait one round (≤5 min) or click \"Test one round now\"."))
        }
        let attempt = RelativeTime.since(model.lastAttempt).map {
            L("最近一轮 \($0)", "last round \($0)")
        }
        if let reason = model.skipReason, !reason.isEmpty {
            var s = Self.humanSkip(reason)
            if let a = attempt { s += L("（\(a)）", " (\(a))") }
            return (reason == "auth_failed" ? .red : .orange, s)
        }
        if let ok = model.lastOK, !ok.isEmpty {
            return (.green, L("运行正常 ✓ 最近成功 ", "Working ✓ last success ")
                    + (RelativeTime.since(ok) ?? ok))
        }
        return (.orange, attempt ?? L("状态未知", "unknown"))
    }

    /// Machine skip_reason → plain-language fix (unknown codes pass through).
    /// Vocabulary: act/radar_gmail.py `_note_skip` + `connect_ex`.
    private static func humanSkip(_ r: String) -> String {
        switch r {
        case "disabled":
            return L("上一轮运行时开关还没打开——点「立即测试一轮」再看",
                     "The toggle was still off during the last round — click \"Test one round now\"")
        case "no_credentials":
            return L("还没保存应用专用密码——完成上面第 ①/③ 步",
                     "No app password saved yet — finish steps ①/③ above")
        case "no_address":
            return L("还没填 Gmail 地址——在上面第 ② 步填好并保存",
                     "No Gmail address yet — fill in step ② above and Save")
        case "auth_failed":
            return L("应用密码或地址不对——重新生成一个应用专用密码再粘贴（公司 Workspace 禁用应用密码时也会这样，见上方说明）",
                     "Wrong app password or address — generate a fresh app password and paste it (a Workspace admin having disabled app passwords looks the same; see the note above)")
        case "connect_failed":
            return L("连不上 Gmail（网络问题）——稍后点「立即测试一轮」重试",
                     "Can't reach Gmail (network trouble) — click \"Test one round now\" again later")
        case "command_failed":
            return L("抓取命令没跑成（fetch_command 报错/超时）——在终端手动跑一次它看报错",
                     "The fetch command failed (error/timeout) — run it by hand in a terminal to see why")
        case "command_bad_output":
            return L("抓取命令的输出不是 JSON 数组——检查 fetch_command 的输出格式",
                     "The fetch command didn't print a JSON array — check its output format")
        default:
            return r
        }
    }
}
