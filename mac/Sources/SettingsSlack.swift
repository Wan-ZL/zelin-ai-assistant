// SettingsSlack.swift — 设置 · Slack 接入（3 步全在 App 内，CONTRACT §15 Slack/Gmail 设置区）
//
// Kills the docs-crawl + config.yaml editing from the Slack happy path
// (audit 6.2 — the report's biggest new work item):
//   1. [复制 App Manifest] + [打开 api.slack.com/apps] — create the app by
//      pasting (manifest source of truth = config/slack-app-manifest.json,
//      drift-guarded against act/lib/slack_setup.manifest_json)
//   2. Install to Workspace → copy the User OAuth Token (xoxp-)
//   3. paste the token here — auth.test verifies ON SAVE (v0.14 credential
//      semantics) and AUTO-FILLS the owner identity (`owner_slack_user_id`
//      override) so nobody ever types a U01234ABCDE by hand.
// After that the channel / watch-people pickers load the workspace directory
// via `python3 -m act.lib.slack_setup --directory` (paginated, cached 1h,
// bilingual scope-missing messages) and write the `slack_channels` /
// `watch_people` overrides. The enable toggle renders + loads the slackradar
// launchd agent through the shared Doctor.swift LaunchAgents helper.
//
// Save semantics: like the iMessage section, everything here persists
// immediately (no form-level Save). The pickers write their override keys
// only when the user actually changes a selection — untouched selections
// leave config.yaml live (the app can't reliably parse the nested YAML list,
// so diff-write degrades to write-on-user-change; stated in the UI).

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

struct SlackDirEntry: Identifiable, Equatable {
    let id: String
    let name: String
    var realName: String = ""
}

@MainActor
final class SlackSettingsModel: ObservableObject {
    nonisolated static let agentLabel = "com.zelin.aiassistant.slackradar"

    // enable toggle (features.slack_radar) + agent + health
    @Published var enabled = true
    @Published var busy = false
    @Published var statusNote = ""
    @Published var agentLoaded: Bool? = nil
    @Published var healthHasData = false
    @Published var lastOK: String? = nil
    @Published var lastAttempt: String? = nil
    @Published var skipReason: String? = nil
    @Published var pollRunning = false
    // step 3: token (0 unset, 1 legacy path, 2 saved, 3 verified, 4 failed)
    @Published var tokenInput = ""
    @Published var tokenState = 0
    @Published var tokenNote = ""
    @Published var tokenNoteColor = Color.secondary
    @Published var verifying = false
    @Published var identity = ""            // "Team · @user" after auth.test
    // manifest copy feedback
    @Published var copiedManifest = false
    @Published var manifestError = ""
    // directory pickers
    @Published var channels: [SlackDirEntry] = []
    @Published var users: [SlackDirEntry] = []
    @Published var directoryLoading = false
    @Published var directoryError = ""
    @Published var directoryLoadedAt = ""
    @Published var selectedChannels: [String: String] = [:]   // id -> name
    @Published var selectedPeople: Set<String> = []           // @handles
    @Published var channelFilter = ""
    @Published var peopleFilter = ""
    @Published var hasChannelOverride = false
    @Published var hasPeopleOverride = false

    private var loaded = false
    private var copyFadeGen = 0

    func loadIfNeeded() {
        guard !loaded else { refreshStatus(); return }
        loaded = true
        let ov = SettingsIO.readOverrides()
        // effective flag: overrides features dict → config.yaml → default on
        let feats = ov["features"] as? [String: Any] ?? [:]
        enabled = (feats["slack_radar"] as? Bool) ?? Self.configFlagLayer()
        // token presence
        refreshTokenState()
        // saved pickers (override layer only — config.yaml stays live when unset)
        if let list = ov["slack_channels"] as? [Any] {
            hasChannelOverride = true
            for item in list {
                if let d = item as? [String: Any], let id = d["id"] as? String {
                    selectedChannels[id] = (d["name"] as? String) ?? id
                } else if let id = item as? String {
                    selectedChannels[id] = id
                }
            }
        }
        if let people = ov["watch_people"] as? [Any] {
            hasPeopleOverride = true
            selectedPeople = Set(people.compactMap { $0 as? String })
        }
        refreshStatus()
    }

    private func refreshTokenState() {
        if SecretsIO.hasSecret(SecretsIO.slackFile) {
            if tokenState < 2 { tokenState = 2 }
        } else if SecretsIO.nonEmptyFile(
            ("~/Desktop/Keys/slack-user-token.txt" as NSString).expandingTildeInPath) {
            tokenState = 1
        } else {
            tokenState = 0
        }
    }

    var tokenPresent: Bool { tokenState >= 1 }

    nonisolated private static func configFlagLayer() -> Bool {
        (SettingsIO.configNestedScalar(block: "features", key: "slack_radar")
            ?? "true").lowercased() != "false"
    }

    // MARK: manifest

    func copyManifest() {
        let path = AppPaths.stateRoot + "/config/slack-app-manifest.json"
        guard let text = try? String(contentsOfFile: path, encoding: .utf8),
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            manifestError = L("找不到 \(path)——repo 不完整？重装一次即可。",
                              "Missing \(path) — incomplete repo? Reinstall to fix.")
            return
        }
        manifestError = ""
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        Analytics.log("mw_slack_manifest_copy")
        copyFadeGen += 1
        let gen = copyFadeGen
        copiedManifest = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            MainActor.assumeIsolated {
                if self.copyFadeGen == gen { self.copiedManifest = false }
            }
        }
    }

    // MARK: token save + verify (auth.test) + identity autofill

    func saveToken() {
        var token = tokenInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        if token.hasPrefix("xoxb-") {
            // A bot token passes auth.test but can't read your DMs — refuse
            // up front instead of leaving a green-but-broken credential.
            setTokenNote(L("这是 Bot token（xoxb-）——雷达读你的 DM 需要 User OAuth Token（xoxp- 开头，在 OAuth & Permissions 页的 User 区）。",
                           "That's a Bot token (xoxb-) — reading your DMs needs the User OAuth Token (starts with xoxp-, in the User section of OAuth & Permissions)."),
                         .orange)
            return
        }
        if !token.hasPrefix("xoxp-") {
            setTokenNote(L("提示：User OAuth Token 通常以 xoxp- 开头——检查是否复制对了。仍会尝试验证…",
                           "Heads-up: User OAuth Tokens usually start with xoxp- — double-check the copy. Verifying anyway…"),
                         .orange)
        }
        token = token.filter { !$0.isWhitespace }
        do {
            try SecretsIO.save(SecretsIO.slackFile, token: token)
            tokenInput = ""
            tokenState = 2
            Analytics.log("mw_secret_save", fields: ["name": SecretsIO.slackFile])
            verifyToken(token, savedFirst: true)
        } catch {
            setTokenNote(L("保存失败: ", "Save failed: ") + error.localizedDescription, .red)
        }
    }

    /// 验证 button — probe the stored token.
    func verifyStored() {
        guard let stored = try? String(contentsOfFile: SecretsIO.path(SecretsIO.slackFile),
                                       encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines), !stored.isEmpty else {
            setTokenNote(L("先粘贴并保存 token 再验证", "Paste and save a token first"), .orange)
            return
        }
        verifyToken(stored, savedFirst: false)
    }

    private func verifyToken(_ token: String, savedFirst: Bool) {
        verifying = true
        setTokenNote(savedFirst ? L("已保存，验证中…", "Saved — verifying…")
                                : L("验证中…", "Verifying…"), .secondary)
        Self.authTest(token: token) { result in
            self.verifying = false
            switch result {
            case .ok(let user, let userId, let team):
                self.tokenState = 3
                self.identity = "\(team) · @\(user)"
                self.autofillOwnerId(userId, user: user)
                self.setTokenNote(
                    L("已验证 ✓ 已连接 \(team)，身份 @\(user) 自动填好——不用再改任何文件。",
                      "Verified ✓ Connected to \(team); identity @\(user) filled in automatically — no files to edit."),
                    .green)
                Analytics.log("mw_key_validate",
                              fields: ["name": SecretsIO.slackFile, "result": "ok"])
                // v0.19.0 funnel (C's milestone, folded into Swift): a working
                // Slack token means an ingest source is live. firstReach dedups.
                Analytics.firstReach("ingest_configured")
                // token freshly working → offer the pickers with fresh data
                self.loadDirectory(refresh: true)
                self.refreshStatus(afterDelay: 2)
            case .unauthorized(let code):
                self.tokenState = 4
                self.identity = ""
                self.setTokenNote(
                    L("验证失败：token 无效——到 api.slack.com/apps → OAuth & Permissions 重新复制 User OAuth Token 再粘贴（\(code)）",
                      "Verification failed: the token is invalid — copy the User OAuth Token again at api.slack.com/apps → OAuth & Permissions and paste it (\(code))"),
                    .red)
                Analytics.log("mw_key_validate",
                              fields: ["name": SecretsIO.slackFile, "result": "unauthorized"])
            case .failed(let why):
                if self.tokenState == 3 || self.tokenState == 4 { self.tokenState = 2 }
                self.setTokenNote(
                    L("无法验证（网络/服务问题），稍后点「验证」重试：", "Couldn't verify (network/service) — click Verify again later: ") + why,
                    .orange)
                Analytics.log("mw_key_validate",
                              fields: ["name": SecretsIO.slackFile, "result": "error"])
            }
        }
    }

    private func setTokenNote(_ text: String, _ color: Color) {
        tokenNote = text
        tokenNoteColor = color
    }

    /// auth.test's user_id → `owner_slack_user_id` override (diff-write vs
    /// config.yaml owner.slack_user_id). Zero questions asked (audit 6.2).
    private func autofillOwnerId(_ userId: String, user: String) {
        guard !userId.isEmpty else { return }
        var merged = SettingsIO.readOverrides()
        let cfgLayer = SettingsIO.configNestedScalar(block: "owner", key: "slack_user_id") ?? ""
        if userId == cfgLayer {
            merged.removeValue(forKey: "owner_slack_user_id")
        } else {
            merged["owner_slack_user_id"] = userId
        }
        try? SettingsIO.writeOverrides(merged)
        Analytics.log("mw_slack_identity_autofill")
    }

    enum AuthResult {
        case ok(user: String, userId: String, team: String)
        case unauthorized(String)
        case failed(String)
    }

    /// POST auth.test — like KeyProbe.slack but keeps the identity fields the
    /// autofill needs (user_id / team), not just "@user".
    nonisolated static func authTest(token: String,
                                     done: @escaping @MainActor (AuthResult) -> Void) {
        var req = URLRequest(url: URL(string: "https://slack.com/api/auth.test")!)
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        URLSession.shared.dataTask(with: req) { data, _, err in
            let result: AuthResult
            if let err {
                result = .failed(err.localizedDescription)
            } else if let data,
                      let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                      let ok = obj["ok"] as? Bool {
                if ok {
                    result = .ok(user: obj["user"] as? String ?? "?",
                                 userId: obj["user_id"] as? String ?? "",
                                 team: obj["team"] as? String ?? "?")
                } else {
                    let code = obj["error"] as? String ?? "unknown_error"
                    let tokenErrors = ["invalid_auth", "not_authed", "account_inactive",
                                       "token_revoked", "token_expired"]
                    result = tokenErrors.contains(code) ? .unauthorized(code) : .failed(code)
                }
            } else {
                result = .failed("no response")
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { done(result) }
            }
        }.resume()
    }

    // MARK: directory (channel + people pickers)

    func loadDirectory(refresh: Bool) {
        guard !directoryLoading else { return }
        directoryLoading = true
        directoryError = ""
        Analytics.log("mw_slack_directory_load", fields: ["refresh": refresh])
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, chans, members, message, fetchedAt) = Self.fetchDirectory(refresh: refresh)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.directoryLoading = false
                    if ok {
                        self.channels = chans
                        self.users = members
                        self.directoryLoadedAt = fetchedAt
                    } else {
                        self.directoryError = message
                    }
                }
            }
        }
    }

    /// Blocking — background queue only. Runs the runtime python CLI
    /// (paginated + cached + bilingual errors live on the python side, where
    /// they are unit-tested — tests/test_slack_setup.py).
    nonisolated private static func fetchDirectory(refresh: Bool)
        -> (Bool, [SlackDirEntry], [SlackDirEntry], String, String) {
        let py = RuntimePython.resolve()
        let root = AppPaths.stateRoot
        var args = ["-m", "act.lib.slack_setup", "--directory"]
        if refresh { args.append("--refresh") }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        env["AIASSISTANT_HOME"] = root
        p.environment = env
        let outPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = Pipe()
        do { try p.run() } catch {
            return (false, [], [],
                    L("找不到可用的 python（", "No usable python (")
                        + error.localizedDescription + ")", "")
        }
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return (false, [], [],
                    L("读取 Slack 目录失败——稍后重试", "Couldn't read the Slack directory — try again later"),
                    "")
        }
        if (obj["ok"] as? Bool) != true {
            let message = obj["message"] as? String
                ?? (obj["error"] as? String ?? "unknown error")
            return (false, [], [], message, "")
        }
        let chans = (obj["channels"] as? [[String: Any]] ?? []).compactMap { d -> SlackDirEntry? in
            guard let id = d["id"] as? String else { return nil }
            return SlackDirEntry(id: id, name: d["name"] as? String ?? id)
        }
        let members = (obj["users"] as? [[String: Any]] ?? []).compactMap { d -> SlackDirEntry? in
            guard let id = d["id"] as? String else { return nil }
            return SlackDirEntry(id: id, name: d["name"] as? String ?? id,
                                 realName: d["real_name"] as? String ?? "")
        }
        return (true, chans, members, "", obj["fetched_at"] as? String ?? "")
    }

    // MARK: picker persistence (write-on-user-change, §15.3)

    func toggleChannel(_ entry: SlackDirEntry) {
        if selectedChannels[entry.id] != nil {
            selectedChannels.removeValue(forKey: entry.id)
        } else {
            selectedChannels[entry.id] = entry.name
        }
        hasChannelOverride = true
        var merged = SettingsIO.readOverrides()
        merged["slack_channels"] = selectedChannels
            .sorted { $0.value.lowercased() < $1.value.lowercased() }
            .map { ["id": $0.key, "name": $0.value] }
        try? SettingsIO.writeOverrides(merged)
        Analytics.log("mw_slack_channels_save",
                      fields: ["n": selectedChannels.count])
    }

    func togglePerson(_ handle: String) {
        if selectedPeople.contains(handle) {
            selectedPeople.remove(handle)
        } else {
            selectedPeople.insert(handle)
        }
        hasPeopleOverride = true
        var merged = SettingsIO.readOverrides()
        merged["watch_people"] = selectedPeople.sorted()
        try? SettingsIO.writeOverrides(merged)
        Analytics.log("mw_slack_watch_people_save",
                      fields: ["n": selectedPeople.count])
    }

    // MARK: enable toggle + launchd agent

    func setEnabled(_ on: Bool) {
        guard !busy else { return }
        persistFlag(on)
        enabled = on
        busy = true
        Analytics.log("mw_slack_toggle", fields: ["on": on])
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
                            ? L("已开启 ✓ 后台雷达每 3 分钟看一次 DM / 群 / @提及。没保存 token 时走 MCP 只读兜底或静默待机。",
                                "Enabled ✓ The background radar checks DMs / groups / @mentions every 3 minutes. Without a token it falls back to read-only MCP scanning or idles silently.")
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

    private func persistFlag(_ on: Bool) {
        var merged = SettingsIO.readOverrides()
        var feats = merged["features"] as? [String: Any] ?? [:]
        if on == Self.configFlagLayer() {
            feats.removeValue(forKey: "slack_radar")
        } else {
            feats["slack_radar"] = on
        }
        if feats.isEmpty {
            merged.removeValue(forKey: "features")
        } else {
            merged["features"] = feats
        }
        try? SettingsIO.writeOverrides(merged)
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
                    self.refreshTokenState()
                }
            }
        }
    }

    func pollNow() {
        guard !pollRunning else { return }
        pollRunning = true
        Analytics.log("mw_slack_kickstart")
        DispatchQueue.global(qos: .userInitiated).async {
            _ = Shell.run("/bin/launchctl",
                          ["kickstart", "gui/\(getuid())/\(Self.agentLabel)"])
            Thread.sleep(forTimeInterval: 6)
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

    /// state/radar_health.json "slack" entry; nil = no data yet.
    nonisolated static func readHealth() -> [String: Any]? {
        let path = AppPaths.stateRoot + "/state/radar_health.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return nil }
        return obj["slack"] as? [String: Any]
    }
}

// MARK: - View

struct SlackSettingsSection: View {
    @StateObject private var model = SlackSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    // Content-only (v0.21): the card / title / collapse chrome is supplied by
    // the shared CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("把「别人在 Slack 上找你的事」（DM / 群 / @提及）自动变成提案卡。3 步全在这里完成，不用改任何文件；对外只出草稿，永远你自己发。此区改动即时生效。",
                   "Turns \"people needing you on Slack\" (DMs / groups / @mentions) into proposal cards automatically. All 3 setup steps happen right here — no files to edit; outbound replies are drafts only, you always send them yourself. Changes apply immediately."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Toggle(L("启用 Slack 雷达", "Enable the Slack radar"), isOn: Binding(
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

            if model.tokenPresent {
                Divider()
                pickers
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

    // MARK: 3-step card

    private var stepCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            // step 1
            VStack(alignment: .leading, spacing: 4) {
                Text(L("① 建 Slack app（一次粘贴，权限已配好）", "① Create the Slack app (one paste; scopes preconfigured)"))
                    .font(.system(size: 12, weight: .medium))
                Text(L("打开 api.slack.com/apps → Create New App → From a manifest → 选你的 workspace → 对话框切到 JSON 标签页 → 粘贴刚复制的内容 → Create。",
                       "Open api.slack.com/apps → Create New App → From a manifest → pick your workspace → switch the dialog to the JSON tab → paste what you copied → Create."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button(model.copiedManifest ? L("已复制 ✓", "Copied ✓")
                                                : L("复制 App Manifest", "Copy App Manifest")) {
                        model.copyManifest()
                    }
                    .controlSize(.small)
                    Button(L("打开 api.slack.com/apps", "Open api.slack.com/apps")) {
                        NSWorkspace.shared.open(URL(string: "https://api.slack.com/apps")!)
                    }
                    .controlSize(.small)
                }
                if !model.manifestError.isEmpty {
                    Text(model.manifestError)
                        .font(.system(size: 10))
                        .foregroundColor(.red)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Divider()
            // step 2
            VStack(alignment: .leading, spacing: 4) {
                Text(L("② 安装授权", "② Install & authorize"))
                    .font(.system(size: 12, weight: .medium))
                Text(L("页面顶部 Install to Workspace → 授权。装好后到左侧 OAuth & Permissions，复制 User OAuth Token（xoxp- 开头；不是 xoxb- 的 Bot token）。公司要求管理员审批的话，等批下来再做第 ③ 步——期间雷达会用只读 MCP 兜底扫描，不会干等。",
                       "Click Install to Workspace at the top → authorize. Then open OAuth & Permissions (left sidebar) and copy the User OAuth Token (starts with xoxp-, NOT the xoxb- bot token). If your company requires admin approval, do step ③ once it's granted — meanwhile the radar falls back to read-only MCP scanning instead of waiting idle."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            // step 3
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Circle().fill(tokenDotColor).frame(width: 8, height: 8)
                    Text(L("③ 粘贴 token（保存即验证，身份自动填好）", "③ Paste the token (verified on save; identity auto-filled)"))
                        .font(.system(size: 12, weight: .medium))
                    if !model.identity.isEmpty {
                        Text(model.identity)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
                HStack(spacing: 8) {
                    SecureField(L("xoxp-…（只存本机 config/secrets/）", "xoxp-… (stored locally in config/secrets/)"),
                                text: $model.tokenInput)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12, design: .monospaced))
                        .onSubmit { model.saveToken() }
                    Button(L("保存", "Save")) { model.saveToken() }
                        .controlSize(.small)
                        .disabled(model.verifying
                            || model.tokenInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button(model.verifying ? L("验证中…", "Verifying…") : L("验证", "Verify")) {
                        model.verifyStored()
                    }
                    .controlSize(.small)
                    .disabled(model.verifying || !model.tokenPresent)
                }
                if !model.tokenNote.isEmpty {
                    Text(model.tokenNote)
                        .font(.system(size: 10))
                        .foregroundColor(model.tokenNoteColor)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var tokenDotColor: Color {
        switch model.tokenState {
        case 3: return .green
        case 4: return .red
        case 1, 2: return .yellow
        default: return Color.secondary.opacity(0.4)
        }
    }

    // MARK: pickers

    private var pickers: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(L("监控范围", "What to watch"))
                    .font(.system(size: 12, weight: .medium))
                Spacer()
                Button(model.directoryLoading
                       ? L("加载中…", "Loading…")
                       : (model.channels.isEmpty && model.users.isEmpty
                          ? L("加载频道和成员", "Load channels & members")
                          : L("刷新", "Refresh"))) {
                    model.loadDirectory(refresh: !model.channels.isEmpty || !model.users.isEmpty)
                }
                .controlSize(.small)
                .disabled(model.directoryLoading)
            }
            Text(L("DM 和群消息总是全看（有人私你 = 大概率要处理）。频道只看你勾选的这些、且 @你 才建卡；「关注的人」的第一位按你的 manager 处理（会议纪要识别用）。这里没改过时沿用 config.yaml 的配置。",
                   "DMs and group DMs are always watched (a DM usually needs you). Channels: only the ones you check here, and only when you're @mentioned; the first \"watched person\" is treated as your manager (for meeting-note detection). Until you change something here, config.yaml stays in charge."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.directoryError.isEmpty {
                Text(model.directoryError)
                    .font(.system(size: 11))
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            if !model.channels.isEmpty {
                pickerList(
                    title: L("频道（@你 才建卡）", "Channels (card only when @mentioned)"),
                    entries: model.channels,
                    filter: $model.channelFilter,
                    isOn: { model.selectedChannels[$0.id] != nil },
                    label: { "#" + $0.name },
                    toggle: { model.toggleChannel($0) })
            }
            if !model.users.isEmpty {
                pickerList(
                    title: L("关注的人（第一位 = 你的 manager）", "Watched people (first = your manager)"),
                    entries: model.users,
                    filter: $model.peopleFilter,
                    isOn: { model.selectedPeople.contains($0.name) },
                    label: { $0.realName.isEmpty ? "@" + $0.name : "@\($0.name)（\($0.realName)）" },
                    toggle: { model.togglePerson($0.name) })
            }
        }
    }

    @ViewBuilder
    private func pickerList(title: String, entries: [SlackDirEntry],
                            filter: Binding<String>,
                            isOn: @escaping (SlackDirEntry) -> Bool,
                            label: @escaping (SlackDirEntry) -> String,
                            toggle: @escaping (SlackDirEntry) -> Void) -> some View {
        let q = filter.wrappedValue.trimmingCharacters(in: .whitespaces).lowercased()
        let filtered = entries.filter {
            q.isEmpty || $0.name.lowercased().contains(q)
                || $0.realName.lowercased().contains(q)
        }
        // selected entries float to the top so current choices are visible
        let shown = Array((filtered.filter(isOn) + filtered.filter { !isOn($0) }).prefix(200))
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.system(size: 11, weight: .medium))
                Spacer()
                TextField(L("筛选…", "Filter…"), text: filter)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11))
                    .frame(width: 160)
            }
            ScrollView(.vertical) {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(shown) { entry in
                        Toggle(label(entry), isOn: Binding(
                            get: { isOn(entry) },
                            set: { _ in toggle(entry) }))
                            .toggleStyle(.checkbox)
                            .font(.system(size: 11))
                    }
                    if filtered.count > shown.count {
                        Text(L("还有 \(filtered.count - shown.count) 项——用上面的筛选框缩小范围",
                               "\(filtered.count - shown.count) more — narrow it down with the filter above"))
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 140)
        }
        .padding(6)
        .background(Color.primary.opacity(0.03))
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
            return (.orange, s)
        }
        if let ok = model.lastOK, !ok.isEmpty {
            return (.green, L("运行正常 ✓ 最近成功 ", "Working ✓ last success ")
                    + (RelativeTime.since(ok) ?? ok))
        }
        return (.orange, attempt ?? L("状态未知", "unknown"))
    }

    /// Machine skip_reason → plain-language fix (unknown codes pass through).
    private static func humanSkip(_ r: String) -> String {
        if r.hasPrefix("mcp_failed") {
            return L("MCP 兜底扫描失败（token 批下来后自动改走正式通道）：\(r)",
                     "The MCP fallback scan failed (the native path takes over once a token is saved): \(r)")
        }
        switch r {
        case "disabled":
            return L("上一轮运行时开关还没打开——点「立即测试一轮」再看",
                     "The toggle was still off during the last round — click \"Test one round now\"")
        case "no_credentials":
            return L("还没保存 token——完成上面第 ③ 步（等管理员审批时会走 MCP 兜底）",
                     "No token saved yet — finish step ③ above (while awaiting admin approval the MCP fallback covers you)")
        case "connect_failed":
            return L("token 无效或连不上 Slack——点上面「验证」看具体原因",
                     "Token invalid or Slack unreachable — click Verify above for the exact reason")
        default:
            return r
        }
    }
}
