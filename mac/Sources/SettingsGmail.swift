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
        // effective: override → config.yaml sources.gmail.enabled (the naive
        // one-level scanner can't reach the two-level nest, so config-layer
        // reads fall back to the product default: on)
        enabled = (ov["gmail_enabled"] as? Bool) ?? true
        address = (ov["gmail_address"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? SettingsIO.configScalar("address") ?? ""
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
        var merged = SettingsIO.readOverrides()
        merged["gmail_enabled"] = on
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

            stepCard

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
        default:
            return r
        }
    }
}
