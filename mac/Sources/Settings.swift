// Settings.swift — 设置页 SettingsFormView（settings_overrides.json 读写）/ CredentialRowView（凭证行）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation
import ServiceManagement  // SMAppService (launch at login)

// MARK: §15.3 设置 — reads/writes state/settings_overrides.json (atomic)

struct SettingsFormView: View {
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var i18n = LanguageStore.shared
    // 契约3: deps「去设置」sets nav.pendingAnchor = "credentials" then switches
    // here — observe it so the credentials group can flash on arrival.
    @ObservedObject private var nav = MainNav.shared
    // item 2: global hotkey — UserDefaults only (NOT settings_overrides.json:
    // that file is a pipeline contract; this is a pure local UI pref).
    @ObservedObject private var hotkey = HotKeyCenter.shared
    @State private var hotkeyEnabled = true
    @State private var hotkeyPreset = "opt-space"
    // v0.10.3 契约一: 卡片排序 — UserDefaults only (pure UI pref, NOT
    // settings_overrides.json). "newest" | "oldest" | "deadline".
    @State private var cardSortOrder = "newest"
    @State private var obsidianRaw = ""
    // v0.10.3 契约二: the other three Obsidian pipeline dirs — default derived
    // from the vault root (= raw's parent), editable, saved to
    // settings_overrides (obsidian_unprocessed / _change_summary / _wiki).
    @State private var obsidianUnprocessed = ""
    @State private var obsidianChangeSummary = ""
    @State private var obsidianWiki = ""
    @State private var gmailAddress = ""
    @State private var showMenuBarIcon = true
    // 通用 · launch at login (SMAppService; state read from the system, not stored)
    @State private var launchAtLogin = false
    @State private var showCostAbove = "5"
    @State private var confirmAbove = "50"
    @State private var trashDays = "60"
    @State private var language = "zh"
    // §16 feature flags — default all on.
    @State private var featSlackRadar = true
    @State private var featGmailRadar = true
    @State private var featObsidianRadar = true
    @State private var featDigest = true
    @State private var featAutoResume = true
    @State private var featAnalytics = true
    @State private var featManagerPack = true
    // local pre-send redaction
    @State private var redactionEnabled = false
    @State private var redactionTermsFile = ""
    @State private var redactionMaskSecrets = true
    @State private var status = ""
    @State private var loaded = false
    // 1.5 s highlight on the credentials group after a deps「去设置」jump
    @State private var credFlash = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("设置", "Settings"))
                .font(.system(size: 18, weight: .semibold))
            Text(L("保存到 state/settings_overrides.json，优先级最高（覆盖 config.yaml）。",
                   "Saved to state/settings_overrides.json; highest priority (overrides config.yaml)."))
                .font(.system(size: 11))
                .foregroundColor(.secondary)

            group(L("通用", "General")) {
                Toggle(L("登录时启动（推荐：菜单栏助手常驻）",
                         "Launch at login (recommended: keep the menu-bar assistant resident)"),
                       isOn: Binding(
                    get: { launchAtLogin },
                    set: { v in setLaunchAtLogin(v) }))
                Text(L("走 macOS 登录项（系统设置 → 通用 → 登录项与扩展 可见/可改）。",
                       "Uses macOS login items (visible in System Settings → General → Login Items & Extensions)."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                Divider()
                // v0.10.3 契约一: 卡片排序 — takes effect immediately (Prefs
                // write + store republish; visible* re-sort on next render).
                HStack {
                    Text(L("卡片排序", "Card sorting"))
                        .font(.system(size: 12))
                        .frame(width: 220, alignment: .leading)
                    Picker("", selection: Binding(
                        get: { cardSortOrder },
                        set: { v in
                            cardSortOrder = v
                            UserDefaults.standard.set(v, forKey: "cardSortOrder")
                            (NSApp.delegate as? AppDelegate)?.store.sortOrderChanged()
                            Analytics.log("mw_sort_order", fields: ["order": v])
                        })) {
                        Text(L("新的在上（默认）", "Newest first")).tag("newest")
                        Text(L("旧的在上（先清积压）", "Oldest first")).tag("oldest")
                        Text(L("Deadline 近的在上", "Deadline first")).tag("deadline")
                    }
                    .pickerStyle(.menu)
                    .frame(width: 220)
                    Spacer()
                }
                Text(L("纯界面偏好（存本机），弹窗与看板同时生效；待审批列顶的处理中占位卡不参与排序。",
                       "UI-only preference (stored locally); applies to the popover and the board alike — processing placeholders stay pinned atop the approval column."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            .toggleStyle(.switch)
            .font(.system(size: 12))

            group(L("菜单栏", "Menu Bar")) {
                Toggle(L("显示菜单栏图标（主图标：卡片列表）",
                         "Show menu-bar icon (main icon: checklist)"), isOn: Binding(
                    get: { showMenuBarIcon },
                    set: { v in
                        showMenuBarIcon = v
                        UserDefaults.standard.set(v, forKey: "showMenuBarIcon")
                        (NSApp.delegate as? AppDelegate)?.updateStatusItemsVisibility()
                    }))
                Text(L("隐藏主图标后，主窗口仍可从 Dock / 再次打开 App 唤起。",
                       "With the main icon hidden, the main window still opens from the Dock / by reopening the app."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            .toggleStyle(.switch)
            .font(.system(size: 12))

            group(L("快捷键", "Hotkey")) {
                Toggle(L("全局热键唤出快速捕获（图标隐藏时打开主窗口）",
                         "Global hotkey opens quick capture (main window when the icon is hidden)"),
                       isOn: Binding(
                    get: { hotkeyEnabled },
                    set: { v in
                        hotkeyEnabled = v
                        UserDefaults.standard.set(v, forKey: "hotkeyEnabled")
                        HotKeyCenter.shared.apply()
                    }))
                HStack {
                    Text(L("按键", "Shortcut"))
                        .font(.system(size: 12))
                        .frame(width: 220, alignment: .leading)
                    Picker("", selection: Binding(
                        get: { hotkeyPreset },
                        set: { v in
                            hotkeyPreset = v
                            UserDefaults.standard.set(v, forKey: "hotkeyPreset")
                            HotKeyCenter.shared.apply()
                        })) {
                        ForEach(HotKeyCenter.presets, id: \.id) { p in
                            Text(p.label).tag(p.id)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(width: 160)
                    .disabled(!hotkeyEnabled)
                    Spacer()
                }
                if hotkeyEnabled && !hotkey.registered {
                    Text(L("⚠︎ 注册失败——该快捷键可能被其他 App 占用，换一个预设试试。",
                           "⚠︎ Registration failed — this shortcut may be taken by another app; try a different preset."))
                        .font(.system(size: 11))
                        .foregroundColor(.orange)
                }
                Text(L("默认 ⌥Space。⌃⌥Space 常被系统「选择上一个输入源」占用（多输入法环境慎选）。",
                       "Default ⌥Space. ⌃⌥Space is often taken by the system input-source switcher (avoid with multiple input methods)."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            .toggleStyle(.switch)
            .font(.system(size: 12))

            group(L("录制", "Recording")) {
                HStack {
                    Text(L("默认录制模式", "Default recording mode"))
                        .font(.system(size: 12))
                        .frame(width: 220, alignment: .leading)
                    Picker("", selection: Binding(
                        get: { rec.mode },
                        set: { rec.setMode($0) })) {
                        Text(L("关", "Off")).tag("off")
                        Text(L("仅屏幕", "Screen Only")).tag("screen")
                        Text(L("屏幕 + 音频", "Screen + Audio")).tag("screen_audio")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 280)
                    Spacer()
                }
                Text(L("打开 App 时自动按此模式启动 Screenpipe 持续录制。",
                       "On app launch, Screenpipe recording starts automatically in this mode."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }

            // v0.10.3 契约二: the four Obsidian pipeline dirs, in pipeline
            // order. 2 - raw keeps its existing editable override
            // (obsidian_raw); the other three default to vault root (raw's
            // parent) + standard name — same derivation as config.py — and
            // save into settings_overrides when edited.
            group(L("Obsidian 目录（按管线顺序）", "Obsidian directories (pipeline order)")) {
                obsidianRow("1 - unprocessed",
                            desc: L("截图/录音导出落点", "Screenshots / recording exports land here"),
                            path: $obsidianUnprocessed)
                Divider()
                obsidianRow("2 - raw",
                            desc: L("雷达扫描源", "Radar scan source"),
                            path: $obsidianRaw)
                Divider()
                obsidianRow("3 - change-summary",
                            desc: L("ingest 变更日志", "Ingest change logs"),
                            path: $obsidianChangeSummary)
                Divider()
                obsidianRow("4 - wiki",
                            desc: L("加工后的知识库", "Processed knowledge base"),
                            path: $obsidianWiki)
                Text(L("1/3/4 默认由 vault 根（= 2 - raw 的上级目录）+ 标准名派生；编辑后点保存写入 settings_overrides。",
                       "1/3/4 default to the vault root (2 - raw's parent) + the standard name; edit and Save to write settings_overrides."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }

            group(L("凭证（存本机 config/secrets/，目录 0700 / 文件 0600）",
                    "Credentials (stored locally in config/secrets/, dir 0700 / file 0600)")) {
                labeledField(L("Gmail 地址", "Gmail address"), $gmailAddress)
                Divider()
                CredentialRowView(
                    title: "Slack token",
                    secretName: SecretsIO.slackFile,
                    legacyPath: "~/Desktop/Keys/slack-user-token.txt",
                    links: [(L("申请页", "Apply"), "https://api.slack.com/apps"),
                            (L("指南", "Guide"), "docs/SLACK_SETUP.md")])
                Divider()
                CredentialRowView(
                    title: L("Gmail 应用密码", "Gmail app password"),
                    secretName: SecretsIO.gmailFile,
                    legacyPath: "~/Desktop/Keys/gmail-app-password.txt",
                    links: [(L("生成密码", "Generate"), "https://myaccount.google.com/apppasswords"),
                            (L("指南", "Guide"), "docs/GMAIL_SETUP.md")])
                Divider()
                CredentialRowView(
                    title: "Anthropic API key",
                    secretName: SecretsIO.anthropicFile,
                    legacyPath: "~/.config/anthropic-key.txt",
                    links: [(L("控制台", "Console"), "https://console.anthropic.com/settings/keys")],
                    validatesAnthropicKey: true)
            }
            // 契约3 frozen anchor — MainWindowView scrollTo()s here from deps
            .id("credentials")
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.accentColor.opacity(credFlash ? 0.16 : 0))
                    .allowsHitTesting(false)
            }

            group(L("审批 / 成本", "Approval / Cost")) {
                labeledField(L("显示成本阈值（USD ≥）", "Show cost above (USD ≥)"), $showCostAbove)
                labeledField(L("文字确认阈值（USD ≥，升 T2）",
                               "Text-confirm above (USD ≥, escalates to T2)"), $confirmAbove)
                labeledField(L("回收站保留天数", "Trash retention days"), $trashDays)
                HStack {
                    Text(L("界面语言", "Interface language"))
                        .font(.system(size: 12))
                        .frame(width: 220, alignment: .leading)
                    Picker("", selection: $language) {
                        Text("中文 (zh)").tag("zh")
                        Text("English (en)").tag("en")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 220)
                    Spacer()
                }
            }

            group(L("Feature flags（§16，默认全开）", "Feature flags (§16, all on by default)")) {
                Toggle(L("slack_radar — Slack 需求雷达", "slack_radar — Slack demand radar"),
                       isOn: $featSlackRadar)
                Toggle(L("gmail_radar — Gmail 捕获", "gmail_radar — Gmail capture"),
                       isOn: $featGmailRadar)
                Toggle(L("obsidian_radar — Obsidian 雷达", "obsidian_radar — Obsidian radar"),
                       isOn: $featObsidianRadar)
                Toggle(L("digest — 周一 digest", "digest — Monday digest"), isOn: $featDigest)
                Toggle(L("auto_resume — 后台任务自动拉起", "auto_resume — auto-resume background tasks"),
                       isOn: $featAutoResume)
                Toggle(L("analytics — 用量统计", "analytics — usage stats"), isOn: $featAnalytics)
                Toggle(L("manager_pack — 会后清单 + 1:1 准备页",
                         "manager_pack — post-meeting checklist + 1:1 prep page"), isOn: $featManagerPack)
            }
            .toggleStyle(.switch)
            .font(.system(size: 12))

            group(L("脱敏（发给 AI 前本地打码）", "Redaction (local masking before sending to AI)")) {
                Toggle(L("启用词表脱敏 — 发出 prompt 前把词表词条替换成 [脱敏]",
                         "Enable term-list redaction — replace term-list matches with [REDACTED] before sending prompts"),
                       isOn: $redactionEnabled)
                    .toggleStyle(.switch)
                Toggle(L("密钥掩码 — 内置正则 (sk-ant-/xox*/AKIA/gh*_/PEM)，始终生效，不依赖词表开关",
                         "Secrets masking — built-in regexes (sk-ant-/xox*/AKIA/gh*_/PEM), always on regardless of the toggle above"),
                       isOn: $redactionMaskSecrets)
                    .toggleStyle(.switch)
                labeledField(L("词表文件（一行一条，re: 前缀=正则）",
                               "Terms file (one per line, re: prefix = regex)"), $redactionTermsFile)
                Text(L("密钥掩码默认开启；词表脱敏默认关闭（打开会改变 AI 看到的内容）。本地存的原文不受影响。",
                       "Secrets masking is on by default; term-list redaction is off by default (enabling changes what the AI sees). Local originals are unaffected."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            .font(.system(size: 12))

            HStack(spacing: 10) {
                Button(L("保存", "Save")) { save() }
                    .keyboardShortcut("s", modifiers: .command)
                if !status.isEmpty {
                    Text(status)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
        .onAppear {
            if !loaded { load(); loaded = true }
            flashCredentialsIfPending()
        }
        .onChange(of: nav.pendingAnchor) { _, _ in flashCredentialsIfPending() }
    }

    /// 契约3: on arrival from deps「去设置」(pendingAnchor still set — the
    /// MainWindowView consumer clears it on an async hop AFTER this appears),
    /// flash the credentials group for 1.5 s so the eye lands on it.
    private func flashCredentialsIfPending() {
        guard nav.pendingAnchor == "credentials" else { return }
        withAnimation(.easeIn(duration: 0.2)) { credFlash = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            withAnimation(.easeOut(duration: 0.4)) { credFlash = false }
        }
    }

    @ViewBuilder
    private func group<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 13, weight: .semibold))
            content()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func labeledField(_ label: String, _ binding: Binding<String>) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 12))
                .frame(width: 220, alignment: .leading)
            TextField("", text: binding)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
        }
    }

    /// v0.10.3 契约二: one Obsidian pipeline-directory row — folder name +
    /// purpose blurb, editable path, and an Open-in-Finder button.
    private func obsidianRow(_ name: String, desc: String, path: Binding<String>) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(name)
                    .font(.system(size: 12, weight: .medium))
                Text(desc)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            .frame(width: 220, alignment: .leading)
            TextField("", text: path)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
            Button(L("打开", "Open")) { openInFinder(path.wrappedValue) }
                .controlSize(.small)
        }
    }

    /// Open a (possibly tilde-prefixed) directory in Finder.
    private func openInFinder(_ path: String) {
        let p = (path.trimmingCharacters(in: .whitespaces) as NSString).expandingTildeInPath
        guard !p.isEmpty else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: p, isDirectory: true))
    }

    /// v0.10.3 契约二: default pipeline dir = vault root (2 - raw's parent;
    /// built-in vault when raw is unset) + standard folder name. MUST match
    /// config.py `_derive_obsidian_dirs` (act/lib/config.py).
    private static func derivedObsidianDir(raw: String, name: String) -> String {
        let r = raw.trimmingCharacters(in: .whitespaces)
        let vault = r.isEmpty ? "~/Documents/Obsidian Vault"
                              : (r as NSString).deletingLastPathComponent
        return vault + "/" + name
    }

    /// Launch-at-login via SMAppService.mainApp. On failure the toggle snaps
    /// back and an NSAlert explains why. Registering a dev build running from
    /// mac/build/ would point the login item at the build directory, so we
    /// require the installed copy (/Applications or ~/Applications) first.
    private func setLaunchAtLogin(_ on: Bool) {
        if on {
            let bundlePath = Bundle.main.bundlePath
            let installed = bundlePath.hasPrefix("/Applications/")
                || bundlePath.hasPrefix(NSHomeDirectory() + "/Applications/")
            guard installed else {
                launchAtLogin = false
                loginItemAlert(
                    L("无法开启登录时启动", "Can't enable launch at login"),
                    L("当前是从 \(bundlePath) 运行的开发版；登录项会指向这个临时路径。请先 ./build.sh --install 装到 /Applications 再开启。",
                      "Currently running from \(bundlePath) (a dev build); the login item would point at that temporary path. Run ./build.sh --install first, then enable."))
                return
            }
            do {
                try SMAppService.mainApp.register()
                launchAtLogin = true
            } catch {
                launchAtLogin = false
                loginItemAlert(
                    L("开启登录时启动失败", "Failed to enable launch at login"),
                    error.localizedDescription)
            }
        } else {
            do {
                try SMAppService.mainApp.unregister()
                launchAtLogin = false
            } catch {
                launchAtLogin = true
                loginItemAlert(
                    L("关闭登录时启动失败", "Failed to disable launch at login"),
                    error.localizedDescription)
            }
        }
    }

    private func loginItemAlert(_ title: String, _ info: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = info
        alert.addButton(withTitle: L("好", "OK"))
        alert.runModal()
    }

    private func load() {
        let ov = SettingsIO.readOverrides()
        func str(_ key: String, configKey: String?, fallback: String) -> String {
            if let v = ov[key] as? String, !v.isEmpty { return v }
            if let ck = configKey, let v = SettingsIO.configScalar(ck), !v.isEmpty { return v }
            return fallback
        }
        obsidianRaw = str("obsidian_raw", configKey: "obsidian_raw",
                          fallback: "~/Documents/Obsidian Vault/2 - raw")
        // v0.10.3 契约二: override → config.yaml → derived from the raw dir
        // just loaded (vault root + standard name, same rule as config.py).
        obsidianUnprocessed = str(
            "obsidian_unprocessed", configKey: "obsidian_unprocessed",
            fallback: Self.derivedObsidianDir(raw: obsidianRaw, name: "1 - unprocessed"))
        obsidianChangeSummary = str(
            "obsidian_change_summary", configKey: "obsidian_change_summary",
            fallback: Self.derivedObsidianDir(raw: obsidianRaw, name: "3 - change-summary"))
        obsidianWiki = str(
            "obsidian_wiki", configKey: "obsidian_wiki",
            fallback: Self.derivedObsidianDir(raw: obsidianRaw, name: "4 - wiki"))
        gmailAddress = str("gmail_address", configKey: "address", fallback: "")
        showMenuBarIcon = Prefs.bool("showMenuBarIcon", default: true)
        hotkeyEnabled = Prefs.bool("hotkeyEnabled", default: true)
        hotkeyPreset = UserDefaults.standard.string(forKey: "hotkeyPreset") ?? "opt-space"
        cardSortOrder = Prefs.cardSortOrder
        launchAtLogin = SMAppService.mainApp.status == .enabled
        func num(_ key: String, configKey: String?, fallback: String) -> String {
            if let v = ov[key] as? Double {
                return v == v.rounded() ? String(Int(v)) : String(v)
            }
            if let v = ov[key] as? Int { return String(v) }
            if let ck = configKey, let v = SettingsIO.configScalar(ck), !v.isEmpty { return v }
            return fallback
        }
        showCostAbove = num("show_cost_above_usd", configKey: "show_cost_above_usd", fallback: "5")
        confirmAbove = num("require_text_confirm_above_usd",
                           configKey: "require_text_confirm_above_usd", fallback: "50")
        trashDays = num("trash_retention_days", configKey: "retention_days", fallback: "60")
        // P0-12: no override key → picker mirrors the same locale fallback
        // LanguageStore resolved at launch (an explicit save still wins).
        language = (ov["language"] as? String).map { $0 == "en" ? "en" : "zh" }
            ?? LanguageStore.systemDefault
        let feats = ov["features"] as? [String: Any] ?? [:]
        func flag(_ key: String) -> Bool { (feats[key] as? Bool) ?? true }
        featSlackRadar = flag("slack_radar")
        featGmailRadar = flag("gmail_radar")
        featObsidianRadar = flag("obsidian_radar")
        featDigest = flag("digest")
        featAutoResume = flag("auto_resume")
        featAnalytics = flag("analytics")
        featManagerPack = flag("manager_pack")
        redactionEnabled = (ov["redaction_enabled"] as? Bool) ?? false
        redactionMaskSecrets = (ov["redaction_mask_secrets"] as? Bool) ?? true
        redactionTermsFile = str("redaction_terms_file", configKey: nil,
                                 fallback: "config/redaction_terms.txt")
    }

    private func save() {
        // read-merge-write: SettingsIO.writeOverrides REPLACES the whole file
        // (same landmine /lang works around) — merge over the existing keys so
        // out-of-form overrides (e.g. legacy slack_token_path) survive a save.
        var merged = SettingsIO.readOverrides()
        let dict: [String: Any] = [
            "obsidian_raw": obsidianRaw.trimmingCharacters(in: .whitespaces),
            "gmail_address": gmailAddress.trimmingCharacters(in: .whitespaces),
            "show_cost_above_usd": Double(showCostAbove.trimmingCharacters(in: .whitespaces)) ?? 5.0,
            "require_text_confirm_above_usd": Double(confirmAbove.trimmingCharacters(in: .whitespaces)) ?? 50.0,
            "trash_retention_days": Int(trashDays.trimmingCharacters(in: .whitespaces)) ?? 60,
            "language": language == "en" ? "en" : "zh",
            "redaction_enabled": redactionEnabled,
            "redaction_mask_secrets": redactionMaskSecrets,
            "redaction_terms_file": redactionTermsFile.trimmingCharacters(in: .whitespaces),
            "features": [
                "slack_radar": featSlackRadar,
                "gmail_radar": featGmailRadar,
                "obsidian_radar": featObsidianRadar,
                "digest": featDigest,
                "auto_resume": featAutoResume,
                "analytics": featAnalytics,
                "manager_pack": featManagerPack,
            ],
        ]
        for (k, v) in dict { merged[k] = v }
        // v0.10.3 契约二: the three derived Obsidian dirs. An emptied field
        // snaps back to its derived default; a value equal to that default
        // DROPS the override key (config.py derivation stays live, so moving
        // raw later re-points them); anything else is written explicitly.
        let rawSaved = obsidianRaw.trimmingCharacters(in: .whitespaces)
        let unprocessedDefault = Self.derivedObsidianDir(raw: rawSaved, name: "1 - unprocessed")
        let changeSummaryDefault = Self.derivedObsidianDir(raw: rawSaved, name: "3 - change-summary")
        let wikiDefault = Self.derivedObsidianDir(raw: rawSaved, name: "4 - wiki")
        if obsidianUnprocessed.trimmingCharacters(in: .whitespaces).isEmpty {
            obsidianUnprocessed = unprocessedDefault
        }
        if obsidianChangeSummary.trimmingCharacters(in: .whitespaces).isEmpty {
            obsidianChangeSummary = changeSummaryDefault
        }
        if obsidianWiki.trimmingCharacters(in: .whitespaces).isEmpty {
            obsidianWiki = wikiDefault
        }
        for (key, def, value) in [
            ("obsidian_unprocessed", unprocessedDefault, obsidianUnprocessed),
            ("obsidian_change_summary", changeSummaryDefault, obsidianChangeSummary),
            ("obsidian_wiki", wikiDefault, obsidianWiki),
        ] {
            let v = value.trimmingCharacters(in: .whitespaces)
            if v == def {
                merged.removeValue(forKey: key)
            } else {
                merged[key] = v
            }
        }
        do {
            try SettingsIO.writeOverrides(merged)
            // apply the UI language immediately (observed views re-render)
            LanguageStore.shared.lang = language == "en" ? "en" : "zh"
            // AppKit main menu doesn't observe SwiftUI state — rebuild it so
            // menu titles follow the new language too.
            (NSApp.delegate as? AppDelegate)?.installMainMenu()
            let f = DateFormatter()
            f.dateFormat = "HH:mm:ss"
            status = L("已保存 ", "Saved ") + f.string(from: Date())
            Analytics.log("mw_settings_save")
        } catch {
            status = L("保存失败: ", "Save failed: ") + error.localizedDescription
        }
    }
}

// One credential row in 设置·凭证 — status dot (green = secrets file saved,
// yellow = legacy path in use, grey = unset) + SecureField paste + save +
// helper link buttons (http URL or repo-relative doc path).
// P1-2: rows with validatesAnthropicKey get a 验证 button (cheap live probe
// against api.anthropic.com/v1/models) and every save auto-verifies — an
// invalid key is never stored silently.
struct CredentialRowView: View {
    let title: String
    let secretName: String                       // file name under config/secrets/
    let legacyPath: String                       // tilde form ok
    let links: [(label: String, target: String)] // http(s) URL or repo-relative path
    var validatesAnthropicKey: Bool = false

    @State private var input = ""
    @State private var state = 0   // 0 = unset, 1 = legacy path, 2 = saved
    @State private var note = ""
    @State private var noteColor = Color.secondary
    @State private var validating = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle().fill(dotColor).frame(width: 8, height: 8)
                Text(title)
                    .font(.system(size: 12, weight: .medium))
                Text(stateText)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                Spacer()
                ForEach(links.indices, id: \.self) { i in
                    Button(links[i].label) { openLink(links[i].target) }
                        .controlSize(.small)
                        .font(.system(size: 10))
                }
            }
            HStack(spacing: 8) {
                SecureField(L("粘贴后点保存（只存本机）", "Paste, then Save (stored locally only)"),
                            text: $input)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                Button(L("保存", "Save")) { save() }
                    .controlSize(.small)
                    .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if validatesAnthropicKey {
                    // P1-2: probe the pasted key (or the stored one when the
                    // field is empty) — inline ok/fail with the reason.
                    Button(validating ? L("验证中…", "Verifying…") : L("验证", "Verify")) {
                        verify()
                    }
                    .controlSize(.small)
                    .disabled(validating)
                }
                if !note.isEmpty {
                    Text(note)
                        .font(.system(size: 10))
                        .foregroundColor(noteColor)
                        .lineLimit(2)
                        .help(note)
                }
            }
            Text(SecretsIO.path(secretName))
                .font(.system(size: 9, design: .monospaced))
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .padding(.vertical, 2)
        .onAppear { refreshState() }
    }

    private var dotColor: Color {
        switch state {
        case 2: return .green
        case 1: return .yellow
        default: return Color.secondary.opacity(0.4)
        }
    }

    private var stateText: String {
        switch state {
        case 2: return L("已保存（App 内管理）", "Saved (managed in-app)")
        case 1: return L("使用旧路径", "Using legacy path")
        default: return L("未设置", "Not set")
        }
    }

    private func refreshState() {
        if SecretsIO.hasSecret(secretName) {
            state = 2
        } else if SecretsIO.nonEmptyFile((legacyPath as NSString).expandingTildeInPath) {
            state = 1
        } else {
            state = 0
        }
    }

    private func save() {
        let token = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        do {
            try SecretsIO.save(secretName, token: token)
            input = ""
            refreshState()
            Analytics.log("mw_secret_save", fields: ["name": secretName])
            if validatesAnthropicKey {
                // P1-2: never store an invalid key silently — probe right away
                runProbe(token, savedFirst: true)
            } else {
                setNote(L("已保存 ✓", "Saved ✓"), .secondary)
            }
        } catch {
            setNote(L("保存失败: ", "Save failed: ") + error.localizedDescription, .red)
        }
    }

    // MARK: P1-2 key validation

    private func setNote(_ text: String, _ color: Color) {
        note = text
        noteColor = color
    }

    /// 验证 button: probe the field content; empty field → the stored secret.
    private func verify() {
        let candidate = input.trimmingCharacters(in: .whitespacesAndNewlines)
        let key: String
        if !candidate.isEmpty {
            key = candidate
        } else if let stored = try? String(contentsOfFile: SecretsIO.path(secretName),
                                           encoding: .utf8)
                    .trimmingCharacters(in: .whitespacesAndNewlines), !stored.isEmpty {
            key = stored
        } else {
            setNote(L("先粘贴（或保存）一个 key 再验证", "Paste (or save) a key first"), .orange)
            return
        }
        runProbe(key, savedFirst: false)
    }

    private func runProbe(_ key: String, savedFirst: Bool) {
        validating = true
        setNote(savedFirst ? L("已保存，验证中…", "Saved — verifying…")
                           : L("验证中…", "Verifying…"), .secondary)
        KeyProbe.anthropic(key: key) { outcome in
            validating = false
            switch outcome {
            case .ok:
                setNote(savedFirst ? L("已保存 ✓ key 有效", "Saved ✓ key valid")
                                   : L("key 有效 ✓", "Key valid ✓"), .green)
                Analytics.log("mw_key_validate", fields: ["result": "ok"])
            case .unauthorized(let why):
                setNote((savedFirst ? L("已保存，但 key 无效：", "Saved, but the key is INVALID: ")
                                    : L("key 无效：", "Invalid key: ")) + why, .red)
                Analytics.log("mw_key_validate", fields: ["result": "unauthorized"])
            case .failed(let why):
                setNote(L("无法验证（网络/服务问题）：", "Couldn't verify (network/service): ") + why,
                        .orange)
                Analytics.log("mw_key_validate", fields: ["result": "error"])
            }
        }
    }

    private func openLink(_ target: String) {
        if target.hasPrefix("http") {
            if let url = URL(string: target) { NSWorkspace.shared.open(url) }
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: AppPaths.stateRoot + "/" + target))
        }
    }
}

// MARK: - P1-2 Anthropic key probe
//
// GET /v1/models — free (no tokens billed), fast, and it fails with 401 on a
// bad key, which is exactly the signal we need. URLSession instead of a curl
// subprocess so the key never appears in a process argv (`ps` would show it).

enum KeyProbe {
    enum Outcome {
        case ok
        case unauthorized(String)  // the key itself is bad (401/403)
        case failed(String)        // network / service — key verdict unknown
    }

    static func anthropic(key: String, done: @escaping @MainActor (Outcome) -> Void) {
        var req = URLRequest(url: URL(string: "https://api.anthropic.com/v1/models?limit=1")!)
        req.timeoutInterval = 10
        req.setValue(key, forHTTPHeaderField: "x-api-key")
        req.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        URLSession.shared.dataTask(with: req) { data, resp, err in
            let outcome: Outcome
            if let err {
                outcome = .failed(err.localizedDescription)
            } else if let http = resp as? HTTPURLResponse {
                if (200..<300).contains(http.statusCode) {
                    outcome = .ok
                } else {
                    let detail = Self.apiErrorMessage(data) ?? "HTTP \(http.statusCode)"
                    outcome = (http.statusCode == 401 || http.statusCode == 403)
                        ? .unauthorized(detail) : .failed(detail)
                }
            } else {
                outcome = .failed("no response")
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { done(outcome) }
            }
        }.resume()
    }

    /// {"error": {"type": "...", "message": "..."}} → "type: message"
    private static func apiErrorMessage(_ data: Data?) -> String? {
        guard let data,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let err = obj["error"] as? [String: Any] else { return nil }
        let parts = [err["type"] as? String, err["message"] as? String].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ": ")
    }
}
