// Settings.swift — 设置页 SettingsFormView（settings_overrides.json 读写）/ CredentialRowView（凭证行）
//
// v0.14 save semantics (audit 5.1/5.2, CONTRACT §15.3 v0.14 note):
// - NO deferred save. Every control persists on change (toggles/pickers) or on
//   Enter / focus-out (text fields). There is no global Save button.
// - Diff-write: a key is written ONLY when the user's value differs from the
//   config-layer effective value (config.yaml → built-in default, i.e. what
//   the pipeline would resolve WITHOUT the override); equal values REMOVE the
//   key so choices made in config.yaml stay live. The app never mirrors whole
//   sections it didn't change — that used to silently clobber redaction /
//   telemetry choices made in config.yaml (a real privacy regression).
// - Numbers are validated: parse failure shows an inline error and writes
//   nothing (no silent coercion to defaults).

import AppKit
import SwiftUI
import Foundation
import ServiceManagement  // SMAppService (launch at login)

// MARK: - Settings redesign infrastructure (v0.21: collapsible + searchable)

/// One entry per settings section. Section-grain (not row-grain): the content
/// is the existing group / embedded-section view; `keywords` is a hand-authored
/// bilingual (zh+en) blob so the finder hits regardless of the shown language.
struct SettingsSectionDescriptor: Identifiable {
    let id: String          // stable key; == frozen anchor where one exists
    let titleZh: String
    let titleEn: String
    let keywords: String    // zh+en blob: title + labels + help lines
    let anchor: String?     // "credentials" | "telemetry" | "claude_import" | nil
    let content: AnyView    // existing group content OR embedded section view
}

/// Persisted collapse state (UserDefaults, comma-joined id list). Default =
/// remember-last, seeded to all-collapsed on first run — a single-screen
/// overview of every section title; expand on demand. Deep-link anchors
/// force-expand their target regardless of stored state.
@MainActor
final class SettingsCollapseStore: ObservableObject {
    private static let key = "settings.expandedSections"
    @Published private var expanded: Set<String>

    init() {
        let raw = UserDefaults.standard.string(forKey: Self.key) ?? ""
        expanded = Set(raw.split(separator: ",").map(String.init).filter { !$0.isEmpty })
    }

    func isExpanded(_ id: String) -> Bool { expanded.contains(id) }

    func expand(_ id: String) {
        guard !expanded.contains(id) else { return }
        expanded.insert(id)
        persist()
    }

    /// Two-way binding for a section id — the collapse toggle drives this.
    func binding(_ id: String) -> Binding<Bool> {
        Binding(
            get: { self.expanded.contains(id) },
            set: { on in
                if on { self.expand(id) }
                else if self.expanded.remove(id) != nil { self.persist() }
            })
    }

    private func persist() {
        UserDefaults.standard.set(expanded.sorted().joined(separator: ","), forKey: Self.key)
    }
}

/// Shared card wrapper — one consistent card / collapse / search chrome for
/// every section (replaces both the old `group()` card and the embedded
/// sections' hand-rolled cards). Body renders only when expanded (or when a
/// search is active, which force-expands every match).
struct CollapsibleSection<Content: View>: View {
    let id: String
    let title: String
    var anchor: String? = nil
    @Binding var isExpanded: Bool
    var searchActive: Bool = false
    var matched: Bool = false
    var flash: Bool = false
    @ViewBuilder let content: () -> Content

    private var effectiveExpanded: Bool { searchActive ? true : isExpanded }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) { isExpanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.secondary)
                        .rotationEffect(.degrees(effectiveExpanded ? 90 : 0))
                    Text(title)
                        .font(.system(size: 13, weight: .semibold))
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // during an active search every match is force-expanded; disabling
            // the toggle keeps the stored accordion state untouched (§1.9).
            .disabled(searchActive)

            if effectiveExpanded { content() }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.accentColor.opacity((flash || (searchActive && matched)) ? 0.16 : 0))
                .allowsHitTesting(false)
        }
        // frozen anchor stays on the (always-present) card so scrollTo lands
        // even while collapsed; the deep-link handler expands to reveal content.
        .id(anchor ?? id)
    }
}

/// The settings finder box. Magnifier + trailing clear (×); ⌘F focuses it
/// (hidden button in SettingsFormView), Esc clears then defocuses. NOT
/// auto-focused on page open, so deep-link scroll-to-anchor is preserved.
struct SettingsSearchField: View {
    @Binding var text: String
    var focused: FocusState<Bool>.Binding
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            TextField(L("搜索设置（⌘F）", "Search settings (⌘F)"), text: $text)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .focused(focused)
                .onKeyPress(.escape) { esc() }
            if !text.isEmpty {
                Button {
                    text = ""
                    focused.wrappedValue = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("清空搜索", "Clear search"))
            }
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 8)
        .frame(maxWidth: 460, alignment: .leading)
        .background(Color.primary.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func esc() -> KeyPress.Result {
        // IME red line: Esc cancels a live pinyin composition — the input
        // method owns it, pass through untouched (Composer.escKey 先例).
        if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
           tv.hasMarkedText() { return .ignored }
        if !text.isEmpty { text = "" }        // 1st Esc: clear the query
        else { focused.wrappedValue = false } // 2nd Esc: release the caret
        return .handled
    }
}

// MARK: §15.3 设置 — reads/writes state/settings_overrides.json (atomic)

struct SettingsFormView: View {
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var i18n = LanguageStore.shared
    // 契约3: deps「去设置」sets nav.pendingAnchor = "credentials" then switches
    // here — observe it so the credentials group can flash on arrival.
    @ObservedObject private var nav = MainNav.shared
    // v0.10.3 契约一: 卡片排序 — UserDefaults only (pure UI pref, NOT
    // settings_overrides.json). "newest" | "oldest" | "deadline".
    @State private var cardSortOrder = "newest"
    // 终端应用 for double-click run-in-terminal — UserDefaults only, same
    // rationale (TerminalLauncher owns default + fallback).
    @State private var terminalApp = TerminalApp.terminal.rawValue
    // v0.15 owner decision: ONE vault-root field. The four pipeline-dir keys
    // (obsidian_raw/_unprocessed/_change_summary/_wiki) remain valid in
    // config.yaml/overrides for experts; the UI edits the vault root and the
    // dirs derive automatically (config.py rule, via ObsidianVaultSetup).
    @State private var vaultRoot = ""
    @State private var vaultMissing = false
    // a pipeline-dir key hand-customized away from the derivation — surfaced
    // as a note so the single field never misrepresents the effective config.
    @State private var obsidianCustomized = false
    @State private var showMenuBarIcon = true
    // 通用 · launch at login (SMAppService; state read from the system, not stored)
    @State private var launchAtLogin = false
    @State private var showCostAbove = "5"
    @State private var confirmAbove = "50"
    @State private var trashDays = "60"
    @State private var showCostError = ""
    @State private var confirmError = ""
    @State private var trashDaysError = ""
    @State private var language = "zh"
    // §15: default output format for drafted deliverables ("markdown" | "html").
    @State private var outputFormat = "markdown"
    // v0.14 (audit 7.1/7.3): execution keys promoted from config.yaml-only.
    @State private var targetRepo = ""
    @State private var targetRepoExists = true
    @State private var skipPermissions = true
    @State private var createGithubRepo = false
    // §16 feature flags — default all on.
    @State private var featSlackRadar = true
    @State private var featGmailRadar = true
    @State private var featObsidianRadar = true
    @State private var featDigest = true
    @State private var featAutoResume = true
    @State private var featAnalytics = true
    // local pre-send redaction
    @State private var redactionEnabled = false
    @State private var redactionTermsFile = ""
    @State private var redactionMaskSecrets = true
    // 语气档案 (docs/VOICE.md): the executor injects the effective voice
    // profile into every dispatched prompt so drafts in the owner's name
    // sound like the owner. Toggle = config voice.enabled (diff-write
    // override key `voice_enabled`); status mirrors executor
    // resolve_voice_profile()'s two-level fallback: state/voice-profile.md
    // (private, gitignored) → config/voice-profile.default.md (shipped).
    @State private var voiceEnabled = true
    @State private var voicePrivateExists = false
    @State private var voiceDefaultExists = false
    @State private var voiceGenRunning = false
    @State private var voiceGenStatus = ""
    @State private var voiceGenFailed = false
    // product improvement program (docs/TELEMETRY.md) — anonymous usage
    // stats, default ON; saved as the nested {"telemetry": {enabled, level,
    // capture_input}} override (CONTRACT §15). capture_input (default ON
    // since v0.18, like level=detailed) is the typed-text switch — only
    // effective together with 详细 level; the copy above must keep saying
    // typed text is included while these defaults hold.
    @State private var telemetryEnabled = true
    @State private var telemetryLevel = "detailed"
    @State private var telemetryCaptureInput = true
    // §26: in-app update check (GitHub releases API, at most once a day).
    @State private var updateCheckEnabled = true
    @State private var status = ""
    @State private var statusIsError = false
    @State private var loaded = false
    // v0.21 collapsible + searchable settings
    @StateObject private var collapse = SettingsCollapseStore()
    @State private var query = ""
    @FocusState private var searchFocused: Bool
    // 1.5 s highlight on the section a deps/wizard「去设置」jump lands on
    // (credentials or claude_import); nil = nothing flashing.
    @State private var flashedAnchor: String? = nil
    // text-field commit plumbing: field key currently focused; leaving a field
    // (or pressing Enter) commits it.
    @FocusState private var focusedField: String?

    var body: some View {
        let searchActive = !query.isEmpty
        let visible = searchActive ? sections.filter { matches($0, query) } : sections
        VStack(alignment: .leading, spacing: 14) {
            Text(L("设置", "Settings"))
                .font(.system(size: 18, weight: .semibold))
            Text(L("设置只存在这台 Mac 上，修改即时生效。",
                   "Settings live only on this Mac; changes take effect immediately."))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .help(L("写入 state/settings_overrides.json——只写与 config.yaml 不同的键（优先级最高）。",
                        "Written to state/settings_overrides.json — only keys that differ from config.yaml (highest priority)."))

            SettingsSearchField(text: $query, focused: $searchFocused)

            if searchActive && visible.isEmpty {
                emptyStateView
            } else {
                ForEach(visible) { sectionView($0) }
            }

            // Advanced/config.yaml footer — hidden during an active search so
            // the result list reads clean. The save status/error still surfaces
            // on its own, so a rare「保存失败…」is never silently hidden.
            if !searchActive {
                footerRow
            } else {
                statusText
            }
        }
        .background {
            // ⌘F focuses the finder — hidden window-scoped shortcut (same
            // pattern as the board search; only fires while 设置 is the page).
            Button("") { searchFocused = true }
                .keyboardShortcut("f", modifiers: .command)
                .opacity(0)
                .frame(width: 0, height: 0)
                .accessibilityHidden(true)
        }
        .onAppear {
            if !loaded { load(); loaded = true }
            expandAnchorIfPending()
            flashAnchorIfPending()
        }
        .onChange(of: nav.pendingAnchor) { _, _ in
            expandAnchorIfPending()
            flashAnchorIfPending()
        }
        // leaving a text field commits it; window close commits the focused one
        .onChange(of: focusedField) { old, _ in commitField(old) }
        .onDisappear { commitField(focusedField) }
    }

    // MARK: - section registry + search

    /// One descriptor per section, in display order. Built inside the view so
    /// each `content` closure still captures self / $focusedField / the private
    /// persist helpers — zero change to save/focus plumbing.
    private var sections: [SettingsSectionDescriptor] {
        [
            SettingsSectionDescriptor(
                id: "general", titleZh: "通用", titleEn: "General",
                keywords: "通用 general 登录时启动 launch at login 登录项 login items 界面语言 interface language 中文 english 语言 交付物格式 输出格式 deliverable format output format markdown html 标记语言 卡片排序 card sorting 排序 newest oldest deadline 终端应用 terminal app 权限体检 permissions checkup 屏幕录制 通知 笔记库访问 初始设置向导 setup wizard 重新运行 re-run 自动检查新版本 检查更新 check for updates github",
                anchor: nil, content: AnyView(generalGroup)),
            SettingsSectionDescriptor(
                id: "menuBar", titleZh: "菜单栏", titleEn: "Menu Bar",
                keywords: "菜单栏 menu bar 显示菜单栏图标 show menu-bar icon 主图标 卡片列表 checklist dock",
                anchor: nil, content: AnyView(menuBarGroup)),
            SettingsSectionDescriptor(
                id: "recording", titleZh: "录制", titleEn: "Recording",
                keywords: "录制 recording 默认录制模式 default recording mode 关 off 仅屏幕 screen only 屏幕音频 screen audio screenpipe 持续录制",
                anchor: nil, content: AnyView(recordingGroup)),
            SettingsSectionDescriptor(
                id: "liveCaptions", titleZh: "实时字幕", titleEn: "Live captions",
                keywords: "实时字幕 live captions 字幕 subtitles 悬浮窗 overlay 歌词 语音识别 speech asr 豆包 doubao 火山 volcano ark 翻译 translation 同传 中英 麦克风 microphone 系统声音 system audio apple 本地 on-device speechanalyzer 字号 font 不透明度 opacity api key",
                anchor: "live_captions", content: AnyView(LiveCaptionsSettingsSection())),
            SettingsSectionDescriptor(
                id: "obsidian", titleZh: "笔记库", titleEn: "Notes vault",
                keywords: "笔记库 notes vault obsidian vault 位置 location 雷达 radar 待办 unprocessed raw change-summary wiki 管线目录 pipeline",
                anchor: nil, content: AnyView(obsidianGroup)),
            SettingsSectionDescriptor(
                id: "credentials",
                titleZh: "凭证（存本机 config/secrets/，保存后自动验证）",
                titleEn: "Credentials (stored locally in config/secrets/; verified automatically on save)",
                keywords: "凭证 credentials secrets anthropic api key 密钥 控制台 console slack token gmail 密码 password 验证 verify",
                anchor: "credentials", content: AnyView(credentialsGroup)),
            SettingsSectionDescriptor(
                id: "slack", titleZh: "Slack 接入", titleEn: "Slack",
                keywords: "slack 接入 雷达 radar dm 群 @提及 mention 提案卡 proposal card token 草稿 draft ingest 需求",
                anchor: nil, content: AnyView(SlackSettingsSection())),
            SettingsSectionDescriptor(
                id: "gmail", titleZh: "Gmail 接入", titleEn: "Gmail",
                keywords: "gmail 接入 email 邮件 雷达 radar 收件箱 inbox 未读 unread 提案卡 proposal card 只读 read-only app password 应用专用密码",
                anchor: nil, content: AnyView(GmailSettingsSection())),
            SettingsSectionDescriptor(
                id: "claudeImport", titleZh: "导入 Claude Code 工作",
                titleEn: "Import Claude Code work",
                keywords: "导入 import claude code 工作 会话 session 看板卡片 board card 扫描 scan 最近 7 天 last 7 days 等你回复 waiting",
                anchor: "claude_import", content: AnyView(ClaudeImportSettingsSection())),
            SettingsSectionDescriptor(
                id: "sync", titleZh: "同步 / 配对", titleEn: "Sync / Pairing",
                keywords: "同步 配对 二维码 手机 iphone sync pairing QR qr code device 看板 board 远程 remote 扫码 scan 端到端加密 e2e",
                anchor: nil, content: AnyView(SyncSettingsSection())),
            SettingsSectionDescriptor(
                id: "approval", titleZh: "审批 / 成本", titleEn: "Approval / Cost",
                keywords: "审批 approval 成本 cost 任务工作目录 task working folder target repo 显示成本阈值 show cost 文字确认 confirm 回收站保留天数 trash retention 免确认 skip permissions github 私有仓库 private repo",
                anchor: nil, content: AnyView(approvalGroup)),
            SettingsSectionDescriptor(
                id: "flags", titleZh: "Feature flags（§16，默认全开）",
                titleEn: "Feature flags (§16, all on by default)",
                keywords: "feature flags 开关 slack_radar gmail_radar obsidian_radar digest auto_resume analytics 雷达 用量统计 usage stats",
                anchor: nil, content: AnyView(flagsGroup)),
            SettingsSectionDescriptor(
                id: "weeklyDigest", titleZh: "每周摘要", titleEn: "Weekly digest",
                keywords: "每周摘要 weekly digest 摘要 recap 自动化建议 automation ideas 现在生成 generate now 待验收 review 待审批 approvals ingest",
                anchor: nil, content: AnyView(WeeklyDigestSettingsSection())),
            SettingsSectionDescriptor(
                id: "voice", titleZh: "语气档案（以你的口吻起草）",
                titleEn: "Voice profile (drafts in your voice)",
                keywords: "语气档案 voice profile 口吻 起草 draft slack 回复 邮件 email 当前生效 in effect 打开档案 open profile 语气注入 voice injection 生成 generate",
                anchor: nil, content: AnyView(voiceGroup)),
            SettingsSectionDescriptor(
                id: "redaction", titleZh: "脱敏（发给 AI 前本地打码）",
                titleEn: "Redaction (local masking before sending to AI)",
                keywords: "脱敏 redaction 打码 mask 词表 term list 密钥掩码 secrets masking regex 正则 sk-ant xox akia pem 词表文件 terms file",
                anchor: nil, content: AnyView(redactionGroup)),
            SettingsSectionDescriptor(
                id: "telemetry", titleZh: "产品改进计划",
                titleEn: "Product improvement program",
                keywords: "产品改进计划 product improvement telemetry 遥测 匿名 anonymous 行为事件级别 behavior event level 基础 basic 详细 detailed 上传文本 upload text capture input 隐私 privacy",
                anchor: "telemetry", content: AnyView(telemetryGroup)),
        ]
    }

    /// §1.7: lowercase + diacritic-fold both sides, whitespace-split the query
    /// into tokens, require ALL tokens to be substrings of the bilingual
    /// keyword blob (AND semantics). Empty query ⇒ every section visible.
    private func matches(_ d: SettingsSectionDescriptor, _ query: String) -> Bool {
        let fold: (String) -> String = {
            $0.folding(options: [.diacriticInsensitive, .caseInsensitive],
                       locale: .current)
        }
        let hay = fold(d.titleZh + " " + d.titleEn + " " + d.keywords)
        let tokens = fold(query).split(whereSeparator: { $0.isWhitespace })
        guard !tokens.isEmpty else { return true }
        return tokens.allSatisfy { hay.contains($0) }
    }

    @ViewBuilder
    private func sectionView(_ d: SettingsSectionDescriptor) -> some View {
        let searchActive = !query.isEmpty
        CollapsibleSection(
            id: d.id,
            title: L(d.titleZh, d.titleEn),
            anchor: d.anchor,
            isExpanded: collapse.binding(d.id),
            searchActive: searchActive,
            matched: searchActive && matches(d, query),
            flash: d.anchor != nil && flashedAnchor == d.anchor
        ) { d.content }
    }

    private var emptyStateView: some View {
        VStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 22))
                .foregroundColor(.secondary)
            Text(L("无匹配设置", "No matching settings"))
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Button(L("清除", "Clear")) { query = "" }
                .controlSize(.small)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    // MARK: - groups

    private var generalGroup: some View {
        group {
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
            // audit 5.5: language lives in General (was buried in Approval/Cost)
            HStack {
                Text(L("界面语言", "Interface language"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Picker("", selection: Binding(
                    get: { language },
                    set: { v in
                        language = v == "en" ? "en" : "zh"
                        persistLanguage()
                    })) {
                    Text("中文 (zh)").tag("zh")
                    Text("English (en)").tag("en")
                }
                .pickerStyle(.segmented)
                .frame(width: 220)
                Spacer()
            }
            Divider()
            // §15: default output format for drafted deliverables — diff-write
            // vs config.yaml `default_output_format` (markdown = status quo).
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(L("交付物默认格式", "Deliverable format"))
                        .font(.system(size: 12))
                    Text(L("助手起草文档/报告时用哪种标记语言",
                           "Markup the assistant drafts documents/reports in"))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                .frame(width: 220, alignment: .leading)
                Picker("", selection: Binding(
                    get: { outputFormat },
                    set: { v in
                        outputFormat = v == "html" ? "html" : "markdown"
                        persistOutputFormat()
                    })) {
                    Text("Markdown").tag("markdown")
                    Text("HTML").tag("html")
                }
                .pickerStyle(.segmented)
                .frame(width: 220)
                Spacer()
            }
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
            Text(L("纯界面偏好（存本机），弹窗与看板同时生效；提案列顶的处理中占位卡不参与排序。",
                   "UI-only preference (stored locally); applies to the popover and the board alike — processing placeholders stay pinned atop the Proposals column."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Divider()
            // 终端应用 — double-click run-in-terminal target (TerminalLauncher).
            // Installed apps only; UserDefaults like cardSortOrder (app-only
            // pref, the pipeline never reads it — no settings_overrides key).
            HStack {
                Text(L("终端应用", "Terminal app"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Picker("", selection: Binding(
                    get: { terminalApp },
                    set: { v in
                        terminalApp = v
                        UserDefaults.standard.set(v, forKey: "terminalApp")
                        Analytics.log("terminal_app_changed", fields: ["app": v])
                    })) {
                    ForEach(TerminalLauncher.installed, id: \.rawValue) { app in
                        Text(app.displayName).tag(app.rawValue)
                    }
                }
                .pickerStyle(.menu)
                .frame(width: 220)
                Spacer()
            }
            Text(L("双击卡片上的 claude 命令时在这个终端里新开窗口运行（单击仍是复制）。首次使用 macOS 会弹一次「控制该终端」的自动化授权。纯界面偏好（存本机）。",
                   "Double-clicking a card's claude command opens and runs it in a new window of this terminal (single click still copies). First use shows the one-time macOS Automation consent for controlling that app. UI-only preference (stored locally)."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Divider()
            // v0.13: reopen the first-run permissions page anytime.
            HStack {
                Text(L("权限体检", "Permissions checkup"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Button(L("打开", "Open")) {
                    PermissionsWindowController.shared.show(firstRun: false)
                }
                .controlSize(.small)
                Spacer()
            }
            Text(L("屏幕录制 / 笔记库访问 / 通知的授权状态一页看全，缺哪个当场补。",
                   "See Screen Recording / notes-vault / Notifications grants on one page and fix any gap on the spot."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Divider()
            // v0.14: reopen the first-run setup wizard anytime — idempotent
            // (all steps prefilled with current values, never wipes data,
            // never re-imports, never re-asks an answered consent).
            HStack {
                Text(L("初始设置向导", "Setup wizard"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Button(L("重新运行初始设置", "Re-run setup")) {
                    Analytics.log("wizard_rerun_from_settings")
                    SetupWizardController.shared.show()
                }
                .controlSize(.small)
                Spacer()
            }
            Text(L("重跑一遍首次设置（语言 / AI 引擎 / 权限 / 录制 / 笔记库 / 健康检查）：全部预填当前值，不会清除任何数据。",
                   "Walk through first-run setup again (language / AI engine / permissions / recording / notes / health check): everything prefilled with current values; nothing gets wiped."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Divider()
            // §26: in-app update check — default on, diff-write override.
            Toggle(L("自动检查新版本（每天最多一次）",
                     "Check for updates automatically (at most once a day)"),
                   isOn: Binding(
                get: { updateCheckEnabled },
                set: { v in
                    updateCheckEnabled = v
                    persistOverride("updates_check_enabled", v,
                                    dropWhen: v == configLayerBool(block: "updates",
                                                                   key: "check_enabled",
                                                                   default: true))
                }))
            Text(L("向 GitHub 查询最新版本号（api.github.com）——请求只暴露你的 IP 和当前版本号，别无其他。发现新版只在菜单栏菜单与「关于」页低调提示，绝不自动下载安装。详见 docs/TELEMETRY.md。",
                   "Asks GitHub for the latest version number (api.github.com) — the request exposes only your IP and the current version string, nothing else. A new version shows a low-key note in the menu-bar menu and the About page; nothing is ever downloaded or installed automatically. See docs/TELEMETRY.md."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
        .toggleStyle(.switch)
        .font(.system(size: 12))
    }

    private var menuBarGroup: some View {
        group {
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
    }

    private var recordingGroup: some View {
        group {
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
            if !rec.selfHealNote.isEmpty {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text(rec.selfHealNote)
                        .font(.system(size: 11))
                        .foregroundColor(.green)
                }
            }
            if !rec.recordingNote.isEmpty {
                // refused / rolled-back mode switch (15 s transient): without
                // this the segmented picker above just snaps back with no
                // explanation on THIS page (the note otherwise renders only
                // in the popover and the ingest page) — mirror of IngestView.
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundColor(.orange)
                    Text(rec.recordingNote)
                        .font(.system(size: 11))
                        .foregroundColor(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // v0.15 (owner decision): ONE vault-root field replaces the four
    // per-directory rows. Picking a vault creates the standard pipeline dirs
    // and diff-writes obsidian_raw via ObsidianVaultSetup (shared with the
    // setup wizard's step 5 — same derivation as config.py).
    private var obsidianGroup: some View {
        group {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(L("Obsidian Vault 位置", "Obsidian Vault location"))
                        .font(.system(size: 12, weight: .medium))
                    Text(L("笔记存这里，雷达也从这里发现待办",
                           "Notes live here; the radar scans it for asks"))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                .frame(width: 220, alignment: .leading)
                TextField("", text: $vaultRoot)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .focused($focusedField, equals: "obsidian_vault")
                    .onSubmit { commitField("obsidian_vault") }
                Button(L("选择…", "Choose…")) {
                    pickFolder(current: vaultRoot) { p in
                        vaultRoot = p
                        commitVaultRoot()
                    }
                }
                .controlSize(.small)
                Button(L("打开", "Open")) { openInFinder(vaultRoot) }
                    .controlSize(.small)
            }
            if vaultMissing {
                HStack(spacing: 8) {
                    Text(L("⚠︎ 笔记库目录还不存在——点「选择…」挑一个，或一键创建。",
                           "⚠︎ The vault folder doesn't exist yet — pick one with Choose…, or create it now."))
                        .font(.system(size: 10))
                        .foregroundColor(.red)
                    Button(L("创建", "Create")) { commitVaultRoot() }
                        .controlSize(.small)
                }
            }
            if obsidianCustomized {
                Text(L("⚙︎ 部分管线目录已在 config.yaml 自定义，不跟随这里的 vault 根目录——以 config.yaml 为准。",
                       "⚙︎ Some pipeline folders are customized in config.yaml and don't follow this vault root — config.yaml wins."))
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
            }
            Text(L("vault 内自动使用并创建 4 个标准子目录：1 - unprocessed（截图/录音落点）· 2 - raw（雷达扫描源）· 3 - change-summary（变更日志）· 4 - wiki（知识库）。默认 ~/Documents/Obsidian Vault。",
                   "Four standard subfolders inside the vault are used (and created) automatically: 1 - unprocessed (capture exports) · 2 - raw (radar scan source) · 3 - change-summary (change logs) · 4 - wiki (knowledge base). Default: ~/Documents/Obsidian Vault."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
    }

    // v0.14 Slack/Gmail 设置区: the Slack token and Gmail address/password
    // moved into their own guided sections (SettingsSlack.swift /
    // SettingsGmail.swift) right below — this group keeps the AI engine key
    // (and the frozen "credentials" anchor deps/Doctor deep-link to).
    // Frozen anchor "credentials" + flash-on-arrival now live in the shared
    // CollapsibleSection wrapper (registered with anchor: "credentials").
    private var credentialsGroup: some View {
        group {
            CredentialRowView(
                title: "Anthropic API key",
                secretName: SecretsIO.anthropicFile,
                legacyPath: "~/.config/anthropic-key.txt",
                links: [(L("控制台", "Console"), "https://console.anthropic.com/settings/keys")],
                kind: .anthropic)
            Text(L("Slack token 与 Gmail 密码在下面各自的接入区里粘贴（同样存本机、保存即验证）。",
                   "The Slack token and Gmail password live in their own sections below (same local storage, same verify-on-save)."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
    }

    private var approvalGroup: some View {
        group {
            // v0.14 (audit 7.1): execution.default_target_repo — until now the
            // first approved card dispatched into a nonexistent placeholder.
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(L("任务工作目录", "Task working folder"))
                        .font(.system(size: 12, weight: .medium))
                    Text(L("批准的卡片默认在这个文件夹里执行", "Approved cards run inside this folder by default"))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                .frame(width: 220, alignment: .leading)
                TextField("", text: $targetRepo)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .focused($focusedField, equals: "target_repo")
                    .onSubmit { commitField("target_repo") }
                Button(L("选择…", "Choose…")) {
                    pickFolder(current: targetRepo) { p in
                        targetRepo = p
                        commitTargetRepo()
                    }
                }
                .controlSize(.small)
            }
            if !targetRepoExists {
                HStack(spacing: 8) {
                    Text(L("⚠︎ 目录不存在——第一张批准的卡会派发失败。",
                           "⚠︎ Folder doesn't exist — the first approved card will fail to dispatch."))
                        .font(.system(size: 10))
                        .foregroundColor(.red)
                    Button(L("创建文件夹", "Create folder")) { createTargetRepoDir() }
                        .controlSize(.small)
                }
            }
            Divider()
            numberField(L("显示成本阈值（USD ≥）", "Show cost above (USD ≥)"),
                        $showCostAbove, key: "show_cost", error: showCostError)
            numberField(L("超过此金额需文字确认（USD ≥）", "Require text confirmation above (USD ≥)"),
                        $confirmAbove, key: "confirm_cost", error: confirmError)
            numberField(L("回收站保留天数", "Trash retention days"),
                        $trashDays, key: "trash_days", error: trashDaysError)
            Divider()
            // v0.14 (audit 7.3): security-relevant execution switches, plain
            // language, effective-load + diff-write.
            Toggle(L("后台任务免确认执行（更快，默认开）",
                     "Run background tasks without per-action confirmations (faster, default on)"),
                   isOn: Binding(
                get: { skipPermissions },
                set: { v in
                    skipPermissions = v
                    persistOverride("skip_permissions", v,
                                    dropWhen: v == configLayerBool(block: "execution",
                                                                   key: "skip_permissions",
                                                                   default: true))
                }))
            Text(L("关掉后走 claude 正常权限模型：敏感操作会把任务挂到「需输入」等你确认。",
                   "When off, claude's normal permission model applies: sensitive actions park the task in Needs Input until you confirm."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Toggle(L("允许自动创建 GitHub 私有仓库（默认关）",
                     "Allow auto-creating private GitHub repos (default off)"),
                   isOn: Binding(
                get: { createGithubRepo },
                set: { v in
                    createGithubRepo = v
                    persistOverride("create_github_repo", v,
                                    dropWhen: v == configLayerBool(block: "execution",
                                                                   key: "create_github_repo",
                                                                   default: false))
                }))
            Text(L("开启后，新建目标的卡片会自动建私有 repo 以交付 draft PR；内容可能源自屏幕/邮件，默认不外推。",
                   "When on, cards targeting a new repo auto-create a private repo so draft PRs can be delivered; content may originate from your screen/mail, so nothing is pushed by default."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
        .toggleStyle(.switch)
        .font(.system(size: 12))
    }

    private var flagsGroup: some View {
        group {
            Toggle(L("slack_radar — Slack 需求雷达", "slack_radar — Slack demand radar"),
                   isOn: featureBinding("slack_radar", $featSlackRadar))
            Toggle(L("gmail_radar — Gmail 捕获", "gmail_radar — Gmail capture"),
                   isOn: featureBinding("gmail_radar", $featGmailRadar))
            Toggle(L("obsidian_radar — Obsidian 雷达", "obsidian_radar — Obsidian radar"),
                   isOn: featureBinding("obsidian_radar", $featObsidianRadar))
            Toggle(L("digest — 周一 digest", "digest — Monday digest"),
                   isOn: featureBinding("digest", $featDigest))
            Toggle(L("auto_resume — 后台任务自动拉起", "auto_resume — auto-resume background tasks"),
                   isOn: featureBinding("auto_resume", $featAutoResume))
            Toggle(L("analytics — 用量统计", "analytics — usage stats"),
                   isOn: featureBinding("analytics", $featAnalytics))
        }
        .toggleStyle(.switch)
        .font(.system(size: 12))
    }

    // 语气档案 (docs/VOICE.md): status row (which profile the executor would
    // inject right now) + open + voice.enabled toggle + one-click generation
    // from the owner's real messages (`python -m act.voice_gen`, same
    // subprocess/progress/result pattern as the iMessage test button).
    private var voiceGroup: some View {
        group {
            Text(L("以你的名义起草的文字（Slack 回复、邮件正文等）会先读这份档案来模仿你的说话风格。私有档案只存本机 state/，永不进 git。",
                   "Text drafted in your name (Slack replies, email bodies, …) first reads this profile to match how you write. Your private profile stays local in state/ and is never committed."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            // 1) status row: what resolve_voice_profile() would pick right now
            HStack(spacing: 8) {
                Circle().fill(voiceDotColor).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text(L("当前生效", "In effect"))
                            .font(.system(size: 12, weight: .medium))
                        Text(voiceStatusText)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                    if let p = voiceEffectivePath {
                        Text(abbreviateHome(p))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                    }
                }
                Spacer()
                // 2) open the effective file (when disabled: the one that
                //    WOULD take effect after re-enabling)
                Button(L("打开档案", "Open profile")) {
                    if let p = voiceEffectivePath {
                        NSWorkspace.shared.open(URL(fileURLWithPath: p))
                    }
                }
                .controlSize(.small)
                .disabled(voiceEffectivePath == nil)
            }
            // 3) master switch — config voice.enabled, diff-write override
            Toggle(L("启用语气注入（默认开）", "Voice injection (default on)"),
                   isOn: Binding(
                get: { voiceEnabled },
                set: { v in
                    voiceEnabled = v
                    persistOverride("voice_enabled", v,
                                    dropWhen: v == configLayerBool(block: "voice",
                                                                   key: "enabled",
                                                                   default: true))
                    Analytics.log("mw_voice_toggle", fields: ["on": v])
                }))
            Text(L("关掉后后台任务照常运行，只是起草的文字不再模仿你的口吻。",
                   "When off, background tasks run as usual — drafted text just stops imitating your voice."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Divider()
            // 4) generate/update the private profile from real messages
            HStack(spacing: 8) {
                Button(voiceGenRunning ? L("生成中…", "Generating…")
                                       : L("从我的消息生成/更新档案", "Generate from my messages")) {
                    runVoiceGen()
                }
                .controlSize(.small)
                .disabled(voiceGenRunning)
                if voiceGenRunning { ProgressView().controlSize(.small) }
                Text(L("需要 Slack 连接；生成前会自动备份现有档案。",
                       "Requires the Slack connection; the existing profile is backed up automatically before generating."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
            }
            if !voiceGenStatus.isEmpty {
                Text(voiceGenStatus)
                    .font(.system(size: 11))
                    .foregroundColor(voiceGenRunning ? .secondary
                                     : (voiceGenFailed ? .orange : .green))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .toggleStyle(.switch)
        .font(.system(size: 12))
    }

    private var redactionGroup: some View {
        group {
            Toggle(L("启用词表脱敏 — 发出 prompt 前把词表词条替换成 [脱敏]",
                     "Enable term-list redaction — replace term-list matches with [REDACTED] before sending prompts"),
                   isOn: Binding(
                get: { redactionEnabled },
                set: { v in
                    redactionEnabled = v
                    persistOverride("redaction_enabled", v,
                                    dropWhen: v == configLayerBool(block: "redaction",
                                                                   key: "enabled",
                                                                   default: false))
                }))
                .toggleStyle(.switch)
            Toggle(L("密钥掩码 — 内置正则 (sk-ant-/xox*/AKIA/gh*_/PEM)，始终生效，不依赖词表开关",
                     "Secrets masking — built-in regexes (sk-ant-/xox*/AKIA/gh*_/PEM), always on regardless of the toggle above"),
                   isOn: Binding(
                get: { redactionMaskSecrets },
                set: { v in
                    redactionMaskSecrets = v
                    persistOverride("redaction_mask_secrets", v,
                                    dropWhen: v == configLayerBool(block: "redaction",
                                                                   key: "mask_secrets",
                                                                   default: true))
                }))
                .toggleStyle(.switch)
            labeledField(L("词表文件（一行一条，re: 前缀=正则）",
                           "Terms file (one per line, re: prefix = regex)"),
                         $redactionTermsFile, key: "redaction_terms_file")
            Text(L("密钥掩码默认开启；词表脱敏默认关闭（打开会改变 AI 看到的内容）。本地存的原文不受影响。",
                   "Secrets masking is on by default; term-list redaction is off by default (enabling changes what the AI sees). Local originals are unaffected."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
        .font(.system(size: 12))
    }

    private var telemetryGroup: some View {
        group {
            Toggle(L("参与产品改进（默认开，默认含我输入的文本——可在下方单独关闭）",
                     "Product improvement (on by default; includes text I type by default — separately switchable below)"),
                   isOn: Binding(
                get: { telemetryEnabled },
                set: { v in
                    telemetryEnabled = v
                    persistTelemetry()
                }))
                .toggleStyle(.switch)
            HStack {
                Text(L("行为事件级别", "Behavior-event level"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Picker("", selection: Binding(
                    get: { telemetryLevel },
                    set: { v in
                        telemetryLevel = v == "detailed" ? "detailed" : "basic"
                        persistTelemetry()
                    })) {
                    Text(L("基础", "Basic")).tag("basic")
                    Text(L("详细（默认）", "Detailed (default)")).tag("detailed")
                }
                .pickerStyle(.segmented)
                .frame(width: 220)
                .disabled(!telemetryEnabled)
                Spacer()
            }
            Text(L("基础与详细都发送匿名事件元数据——事件名、时间、页面/动作、耗时计数、随机设备号、版本号。切到基础还会同时停掉下方的输入文本上传（文本需要详细级）。",
                   "Both Basic and Detailed send anonymous event metadata — event name, time, page/action, timing counts, random device id, app version. Switching to Basic also stops the typed-text upload below (text requires Detailed)."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Toggle(L("上传我输入的文本以更懂我（默认开：快速捕获、提问、打回反馈、搜索词；每条 ≤500 字符）",
                     "Upload the text I type, to know me better (on by default: captures, questions, rework feedback, search terms; ≤500 chars each)"),
                   isOn: Binding(
                get: { telemetryCaptureInput },
                set: { v in
                    telemetryCaptureInput = v
                    // captureTouched: flipping THIS toggle is the informed
                    // interaction with the disclosing control — the key is
                    // written explicitly (even when it equals the default)
                    // so content_gate accepts it without the v2 marker.
                    persistTelemetry(captureTouched: true)
                }))
                .toggleStyle(.switch)
                .disabled(!telemetryEnabled || telemetryLevel != "detailed")
            Text(L("只收集你亲手输入进本 App 的文字（截断 500 字符，内置密钥掩码）——绝不含 AI 的回答、屏幕录制内容、邮件或 Slack 消息。关掉此开关即停止记录与上传新的文本（关前已记录、尚未上传的少量行仍会随行为统计发出），行为统计不受影响。",
                   "Collects only what you personally type into this app (truncated to 500 chars, built-in key masking) — never the AI's answers, screen-recording content, emails or Slack messages. Turning this off stops recording and uploading new text (a few lines recorded before the switch-off may still upload with behavior stats); behavior stats are unaffected."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Text(L("关掉最上方开关即完全停止全部上传；本地统计文件不受影响。详见 docs/TELEMETRY.md。",
                   "Turning the top toggle off stops all uploads entirely; the local stats file is unaffected. See docs/TELEMETRY.md."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
        .font(.system(size: 12))
        // 首启披露页「详情与关闭在设置」跳转锚点 (pendingAnchor = "telemetry")
        // now lives on the CollapsibleSection wrapper (anchor: "telemetry").
        // NO passive v2-marker writer here — and never should be. Historically
        // the risk was the non-lazy VStack firing .onAppear on INSERTION; with
        // v0.21 collapse, this section's content mounts only on EXPAND, which
        // is even safer (still not "the user read & consented"). Legitimate
        // arming stays: the first-run disclosure/wizard writes the v2 marker
        // when its copy renders, and flipping the toggle above writes the
        // explicit capture_input key (captureTouched).
    }

    // v0.14 (audit 7.6): expert-only keys stay in config.yaml — say so, and
    // open the file on request (copied from the example first when missing).
    private var footerRow: some View {
        HStack(spacing: 10) {
            Text(L("高级选项（轮询间隔、digest 时间、不录制的 App、单独指定 4 个 Obsidian 管线目录等）在 config.yaml 中",
                   "Advanced options (poll intervals, digest schedule, ignored apps, per-directory Obsidian pipeline paths, …) live in config.yaml"))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Button(L("打开 config.yaml", "Open config.yaml")) { openConfigYaml() }
                .controlSize(.small)
            Spacer()
            statusText
        }
    }

    /// Save status / error line — sits at the right of the footer normally, and
    /// is surfaced on its own during an active search so a failed save is never
    /// silently hidden.
    @ViewBuilder
    private var statusText: some View {
        if !status.isEmpty {
            Text(status)
                .font(.system(size: 11))
                .foregroundColor(statusIsError ? .red : .secondary)
                .lineLimit(2)
                .help(status)
        }
    }

    /// Deep-link expand (§1.9): a deps/wizard「去设置」jump sets
    /// nav.pendingAnchor; force-expand the target section so the async
    /// scrollTo (MainWindow.consumePendingAnchor) lands on rendered content.
    private func expandAnchorIfPending() {
        guard let a = nav.pendingAnchor,
              let d = sections.first(where: { $0.anchor == a }) else { return }
        // An active search filters the target out of `visible`, so its .id
        // never renders and the async scrollTo can't land — clear it first.
        query = ""
        collapse.expand(d.id)
    }

    /// 契约3: on arrival from a「去设置」jump (pendingAnchor still set — the
    /// MainWindowView consumer clears it on an async hop AFTER this appears),
    /// flash the target section for 1.5 s so the eye lands on it. Covers the
    /// credentials + claude_import anchors (telemetry doesn't flash today).
    private func flashAnchorIfPending() {
        guard let a = nav.pendingAnchor,
              a == "credentials" || a == "claude_import" else { return }
        withAnimation(.easeIn(duration: 0.2)) { flashedAnchor = a }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            withAnimation(.easeOut(duration: 0.4)) {
                if flashedAnchor == a { flashedAnchor = nil }
            }
        }
    }

    /// Inner content container for the inline sections — the card / title /
    /// collapse / anchor chrome now lives in the shared CollapsibleSection
    /// wrapper (§1.5), so this only groups the rows.
    @ViewBuilder
    private func group<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            content()
        }
    }

    private func labeledField(_ label: String, _ binding: Binding<String>, key: String) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 12))
                .frame(width: 220, alignment: .leading)
            TextField("", text: binding)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
                .focused($focusedField, equals: key)
                .onSubmit { commitField(key) }
        }
    }

    /// audit 5.3: numeric field with inline validation — a parse failure shows
    /// a red hint and writes NOTHING (the displayed value always equals the
    /// effective one after a successful commit).
    @ViewBuilder
    private func numberField(_ label: String, _ binding: Binding<String>,
                             key: String, error: String) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 12))
                .frame(width: 220, alignment: .leading)
            TextField("", text: binding)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
                .frame(width: 120)
                .focused($focusedField, equals: key)
                .onSubmit { commitField(key) }
            if !error.isEmpty {
                Text(error)
                    .font(.system(size: 10))
                    .foregroundColor(.red)
            }
            Spacer()
        }
    }

    /// Open a (possibly tilde-prefixed) directory in Finder.
    private func openInFinder(_ path: String) {
        let p = (path.trimmingCharacters(in: .whitespaces) as NSString).expandingTildeInPath
        guard !p.isEmpty else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: p, isDirectory: true))
    }

    /// NSOpenPanel folder picker (directories only, may create) — returns the
    /// picked path with $HOME abbreviated back to "~".
    private func pickFolder(current: String, done: (String) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.prompt = L("选择", "Choose")
        let cur = (current.trimmingCharacters(in: .whitespaces) as NSString).expandingTildeInPath
        if !cur.isEmpty, FileManager.default.fileExists(atPath: cur) {
            panel.directoryURL = URL(fileURLWithPath: cur, isDirectory: true)
        }
        if panel.runModal() == .OK, let url = panel.url {
            done(abbreviateHome(url.path))
        }
    }

    private func abbreviateHome(_ path: String) -> String {
        let home = NSHomeDirectory()
        return path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
    }

    private func dirExists(_ path: String) -> Bool {
        let p = (path.trimmingCharacters(in: .whitespaces) as NSString).expandingTildeInPath
        guard !p.isEmpty else { return true }
        var isDir: ObjCBool = false
        return FileManager.default.fileExists(atPath: p, isDirectory: &isDir) && isDir.boolValue
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

    // MARK: - load (effective values: overrides → config.yaml → defaults)

    private func load() {
        let ov = SettingsIO.readOverrides()
        func str(_ key: String, configKey: String?, fallback: String) -> String {
            if let v = ov[key] as? String, !v.isEmpty { return v }
            if let ck = configKey, let v = SettingsIO.configScalar(ck), !v.isEmpty { return v }
            return fallback
        }
        // v0.15: vault root = the effective obsidian_raw's parent (override →
        // config.yaml → built-in default); customized per-dir keys flagged.
        loadVault(effectiveRaw: str("obsidian_raw", configKey: "obsidian_raw",
                                    fallback: "~/Documents/Obsidian Vault/2 - raw"),
                  overrides: ov)
        showMenuBarIcon = Prefs.bool("showMenuBarIcon", default: true)
        cardSortOrder = Prefs.cardSortOrder
        // preferred validates the stored choice against installed apps
        terminalApp = TerminalLauncher.preferred.rawValue
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
        // v0.14 (audit 7.1/7.3): execution keys — effective load.
        targetRepo = (ov["default_target_repo"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? SettingsIO.configNestedScalar(block: "execution", key: "default_target_repo")
            ?? "~/Projects/your-workbench"
        targetRepoExists = dirExists(targetRepo)
        skipPermissions = (ov["skip_permissions"] as? Bool)
            ?? configLayerBool(block: "execution", key: "skip_permissions", default: true)
        createGithubRepo = (ov["create_github_repo"] as? Bool)
            ?? configLayerBool(block: "execution", key: "create_github_repo", default: false)
        // §26: update check — effective load (override → config.yaml → on)
        updateCheckEnabled = (ov["updates_check_enabled"] as? Bool)
            ?? configLayerBool(block: "updates", key: "check_enabled", default: true)
        // P0-12: no override key → picker mirrors the same locale fallback
        // LanguageStore resolved at launch (an explicit save still wins).
        language = (ov["language"] as? String).map { $0 == "en" ? "en" : "zh" }
            ?? LanguageStore.systemDefault
        // §15: deliverable format — effective (override → config.yaml → markdown)
        outputFormat = {
            let v = str("default_output_format", configKey: "default_output_format",
                        fallback: "markdown").lowercased()
            return v == "html" ? "html" : "markdown"
        }()
        // §16 flags — effective: overrides → config.yaml features: → default on
        // (audit 5.2: reading overrides only made config.yaml choices look wrong
        // and get clobbered on the next save).
        let feats = ov["features"] as? [String: Any] ?? [:]
        func flag(_ key: String) -> Bool {
            (feats[key] as? Bool) ?? configLayerBool(block: "features", key: key, default: true)
        }
        featSlackRadar = flag("slack_radar")
        featGmailRadar = flag("gmail_radar")
        featObsidianRadar = flag("obsidian_radar")
        featDigest = flag("digest")
        featAutoResume = flag("auto_resume")
        featAnalytics = flag("analytics")
        // redaction — effective (audit 5.2: a config.yaml `redaction.enabled:
        // true` used to show as off AND get overwritten by unrelated saves).
        redactionEnabled = (ov["redaction_enabled"] as? Bool)
            ?? configLayerBool(block: "redaction", key: "enabled", default: false)
        redactionMaskSecrets = (ov["redaction_mask_secrets"] as? Bool)
            ?? configLayerBool(block: "redaction", key: "mask_secrets", default: true)
        redactionTermsFile = (ov["redaction_terms_file"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? SettingsIO.configNestedScalar(block: "redaction", key: "terms_file")
            ?? "config/redaction_terms.txt"
        // 语气档案 — effective (override → config.yaml voice.enabled → on),
        // plus which profile file resolve_voice_profile() would pick.
        voiceEnabled = (ov["voice_enabled"] as? Bool)
            ?? configLayerBool(block: "voice", key: "enabled", default: true)
        refreshVoiceProfileStatus()
        // telemetry — mirror the effective config: overrides (nested form
        // shared with the first-run permissions page, flat keys accepted
        // too) → config.yaml telemetry block → built-in defaults (on /
        // detailed / capture on, v0.18), same precedence as act/lib/config.py.
        let tele = ov["telemetry"] as? [String: Any] ?? [:]
        if let v = tele["enabled"] as? Bool {
            telemetryEnabled = v
        } else if let v = ov["telemetry.enabled"] as? Bool {
            telemetryEnabled = v
        } else if let v = SettingsIO.configNestedScalar(block: "telemetry", key: "enabled") {
            telemetryEnabled = (v.lowercased() != "false")
        } else {
            telemetryEnabled = true
        }
        let level = ((tele["level"] as? String)
            ?? (ov["telemetry.level"] as? String)
            ?? SettingsIO.configNestedScalar(block: "telemetry", key: "level")
            ?? "detailed").lowercased()
        telemetryLevel = level == "detailed" ? "detailed" : "basic"
        // capture_input（输入文本上传，v0.18 起默认开）— same precedence
        // chain; effective truth mirrored by Telemetry.captureInput().
        if let v = tele["capture_input"] as? Bool {
            telemetryCaptureInput = v
        } else if let v = ov["telemetry.capture_input"] as? Bool {
            telemetryCaptureInput = v
        } else if let v = SettingsIO.configNestedScalar(block: "telemetry",
                                                        key: "capture_input") {
            telemetryCaptureInput = (v.lowercased() != "false")
        } else {
            telemetryCaptureInput = true
        }
    }

    // MARK: - persist (on change, diff-write; CONTRACT §15.3 v0.14)

    /// Config-layer effective bool (config.yaml → built-in default) — the
    /// value the pipeline would resolve WITHOUT any override. Used to decide
    /// whether an override key is needed at all.
    private func configLayerBool(block: String, key: String, default def: Bool) -> Bool {
        guard let v = SettingsIO.configNestedScalar(block: block, key: key) else { return def }
        return v.lowercased() != "false"
    }

    /// Write (or drop) ONE override key. `dropWhen` = the new value equals the
    /// config-layer effective value, so the key is removed and config.yaml
    /// stays live. All other keys in the file are left untouched.
    private func persistOverride(_ key: String, _ value: Any, dropWhen equalsConfigLayer: Bool) {
        var merged = SettingsIO.readOverrides()
        if equalsConfigLayer {
            merged.removeValue(forKey: key)
        } else {
            merged[key] = value
        }
        // behavior telemetry (docs/TELEMETRY.md): WHICH key changed — never
        // the value (paths/addresses/thresholds stay on this machine).
        Analytics.log("mw_setting_change", fields: ["key": key])
        writeMerged(merged)
    }

    private func writeMerged(_ merged: [String: Any]) {
        do {
            try SettingsIO.writeOverrides(merged)
            noteSaved()
            Analytics.log("mw_settings_save")
        } catch {
            noteSaveFailure(error)
        }
    }

    /// The fail-closed corrupt-overrides refusal carries its own honest,
    /// self-contained message — the generic disk/permissions prefix ("change
    /// it again to retry") would be wrong advice for it.
    private func noteSaveFailure(_ error: Error) {
        if (error as NSError).domain == SettingsIO.errorDomain {
            noteError(error.localizedDescription)
        } else {
            noteError(L("保存失败（磁盘或权限问题），这次改动没写入——再改一次即可重试：",
                        "Save failed (disk or permissions); this change was not written — change it again to retry: ")
                + error.localizedDescription)
        }
    }

    private func noteSaved() {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        status = L("已保存 ", "Saved ") + f.string(from: Date())
        statusIsError = false
    }

    private func noteError(_ text: String) {
        status = text
        statusIsError = true
    }

    /// One toggle = one flag key inside the "features" dict; equal to the
    /// config-layer value → key dropped (empty dict → whole key dropped).
    private func featureBinding(_ key: String, _ state: Binding<Bool>) -> Binding<Bool> {
        Binding(
            get: { state.wrappedValue },
            set: { v in
                state.wrappedValue = v
                var merged = SettingsIO.readOverrides()
                var feats = merged["features"] as? [String: Any] ?? [:]
                if v == configLayerBool(block: "features", key: key, default: true) {
                    feats.removeValue(forKey: key)
                } else {
                    feats[key] = v
                }
                if feats.isEmpty {
                    merged.removeValue(forKey: "features")
                } else {
                    merged["features"] = feats
                }
                Analytics.log("mw_setting_change",
                              fields: ["key": "features." + key])
                writeMerged(merged)
            })
    }

    /// Language is an explicit user choice that must stick (the fallback is
    /// locale-dependent, not a config layer) — always written on change.
    private func persistLanguage() {
        var merged = SettingsIO.readOverrides()
        merged["language"] = language == "en" ? "en" : "zh"
        Analytics.log("mw_setting_change", fields: ["key": "language"])
        writeMerged(merged)
        // apply the UI language immediately (observed views re-render)
        LanguageStore.shared.lang = language == "en" ? "en" : "zh"
        // AppKit main menu doesn't observe SwiftUI state — rebuild it so
        // menu titles follow the new language too.
        (NSApp.delegate as? AppDelegate)?.installMainMenu()
    }

    /// §15 deliverable format: diff-write vs the config.yaml effective value —
    /// key present only when it differs from `default_output_format` (→ default
    /// markdown), removed when it matches, so the app never clobbers a
    /// config.yaml choice. Read side = executor build_prompt (HTML instruction).
    private func persistOutputFormat() {
        var merged = SettingsIO.readOverrides()
        let cfgRaw = (SettingsIO.configScalar("default_output_format") ?? "markdown").lowercased()
        let cfgVal = cfgRaw == "html" ? "html" : "markdown"
        let val = outputFormat == "html" ? "html" : "markdown"
        if val == cfgVal {
            merged.removeValue(forKey: "default_output_format")
        } else {
            merged["default_output_format"] = val
        }
        Analytics.log("mw_setting_change", fields: ["key": "default_output_format"])
        writeMerged(merged)
    }

    /// §15 telemetry overrides (docs/TELEMETRY.md): nested form; enabled +
    /// level sub-keys diff-written, legacy flat keys dropped so the two
    /// spellings can never disagree. capture_input is DIFFERENT: it doubles
    /// as the consent record (CONTRACT §15 v0.18) — once the user has
    /// flipped its toggle (captureTouched) the key is written explicitly
    /// even at the default value, and unrelated saves never diff-drop an
    /// existing key (dropping it would silently revoke a recorded choice).
    private func persistTelemetry(captureTouched: Bool = false) {
        var merged = SettingsIO.readOverrides()
        var tele = merged["telemetry"] as? [String: Any] ?? [:]
        let cfgEnabled = configLayerBool(block: "telemetry", key: "enabled", default: true)
        let cfgLevelRaw = (SettingsIO.configNestedScalar(block: "telemetry", key: "level")
            ?? "detailed").lowercased()
        let cfgLevel = cfgLevelRaw == "detailed" ? "detailed" : "basic"
        let level = telemetryLevel == "detailed" ? "detailed" : "basic"
        if telemetryEnabled == cfgEnabled {
            tele.removeValue(forKey: "enabled")
        } else {
            tele["enabled"] = telemetryEnabled
        }
        if level == cfgLevel {
            tele.removeValue(forKey: "level")
        } else {
            tele["level"] = level
        }
        if captureTouched {
            tele["capture_input"] = telemetryCaptureInput
        } else if tele["capture_input"] == nil,
                  let legacy = merged["telemetry.capture_input"] as? Bool {
            // migrate a hand-written LEGACY FLAT capture_input into the
            // nested form BEFORE the flat-key cleanup below deletes it —
            // dropping it would silently revoke a recorded opt-out
            // (the nested key, when present, already wins and is kept).
            tele["capture_input"] = legacy
        }
        // not touched this save: leave any existing capture_input key
        // exactly as it is — it records an explicit (consent) choice.
        if tele.isEmpty {
            merged.removeValue(forKey: "telemetry")
        } else {
            merged["telemetry"] = tele
        }
        merged.removeValue(forKey: "telemetry.enabled")
        merged.removeValue(forKey: "telemetry.level")
        merged.removeValue(forKey: "telemetry.capture_input")
        Analytics.log("mw_setting_change", fields: ["key": "telemetry"])
        writeMerged(merged)
    }

    /// Route a text-field commit (Enter / focus-out / window close).
    private func commitField(_ key: String?) {
        guard loaded, let key else { return }
        switch key {
        case "obsidian_vault":
            commitVaultRoot()
        case "show_cost":
            commitShowCost()
        case "confirm_cost":
            commitConfirmAbove()
        case "trash_days":
            commitTrashDays()
        case "redaction_terms_file":
            commitTermsFile()
        case "target_repo":
            commitTargetRepo()
        default:
            break
        }
    }

    /// v0.15: derive the display state from the effective config. Vault root
    /// = effective obsidian_raw's parent; any per-dir key (still honored from
    /// config.yaml/overrides) that doesn't match the derivation — or a raw
    /// dir not named "2 - raw" — marks the config as hand-customized.
    private func loadVault(effectiveRaw: String, overrides ov: [String: Any]) {
        func expand(_ s: String) -> String { (s as NSString).expandingTildeInPath }
        vaultRoot = (effectiveRaw as NSString).deletingLastPathComponent
        var customized = (effectiveRaw as NSString).lastPathComponent != "2 - raw"
        for (key, name) in [("obsidian_unprocessed", "1 - unprocessed"),
                            ("obsidian_change_summary", "3 - change-summary"),
                            ("obsidian_wiki", "4 - wiki")] {
            let v = (ov[key] as? String).flatMap { $0.isEmpty ? nil : $0 }
                ?? SettingsIO.configScalar(key)
            if let v, expand(v) != expand(Self.derivedObsidianDir(raw: effectiveRaw, name: name)) {
                customized = true
            }
        }
        obsidianCustomized = customized
        refreshVaultExists()
    }

    private func refreshVaultExists() {
        vaultMissing = !dirExists(vaultRoot)
            || (!obsidianCustomized && !ObsidianVaultSetup.pipelineDirNames
                .allSatisfy { dirExists(vaultRoot + "/" + $0) })
    }

    /// Commit the vault root (Enter / focus-out / Choose… / Create): create
    /// the standard pipeline dirs and diff-write obsidian_raw through
    /// ObsidianVaultSetup — the same helper wizard step 5 uses (no fork).
    private func commitVaultRoot() {
        var v = vaultRoot.trimmingCharacters(in: .whitespaces)
        if v.isEmpty {
            // emptied field snaps back to the config layer's vault root
            let rawLayer = SettingsIO.configScalar("obsidian_raw")
                .flatMap { $0.isEmpty ? nil : $0 } ?? "~/Documents/Obsidian Vault/2 - raw"
            v = (rawLayer as NSString).deletingLastPathComponent
        }
        vaultRoot = v
        do {
            try ObsidianVaultSetup.apply(root: v)
            noteSaved()
            Analytics.log("mw_settings_save")
        } catch {
            noteSaveFailure(error)
        }
        loadVault(effectiveRaw: effectiveRawDir(), overrides: SettingsIO.readOverrides())
    }

    /// Effective obsidian_raw for display (override → config.yaml → default).
    private func effectiveRawDir() -> String {
        let ov = SettingsIO.readOverrides()
        if let v = ov["obsidian_raw"] as? String, !v.isEmpty { return v }
        if let v = SettingsIO.configScalar("obsidian_raw"), !v.isEmpty { return v }
        return "~/Documents/Obsidian Vault/2 - raw"
    }

    private func commitTermsFile() {
        let v = redactionTermsFile.trimmingCharacters(in: .whitespaces)
        let configLayer = SettingsIO.configNestedScalar(block: "redaction", key: "terms_file")
            ?? "config/redaction_terms.txt"
        if v.isEmpty {
            redactionTermsFile = configLayer
            persistOverride("redaction_terms_file", configLayer, dropWhen: true)
            return
        }
        persistOverride("redaction_terms_file", v, dropWhen: v == configLayer)
    }

    private func numFormat(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(v)
    }

    private func commitShowCost() {
        let t = showCostAbove.trimmingCharacters(in: .whitespaces)
        guard let v = Double(t), v >= 0 else {
            showCostError = L("请输入不小于 0 的数字，如 5", "Enter a number ≥ 0, e.g. 5")
            return
        }
        showCostError = ""
        showCostAbove = numFormat(v)
        let cfgLayer = Double(SettingsIO.configScalar("show_cost_above_usd") ?? "") ?? 5.0
        persistOverride("show_cost_above_usd", v, dropWhen: v == cfgLayer)
    }

    private func commitConfirmAbove() {
        let t = confirmAbove.trimmingCharacters(in: .whitespaces)
        guard let v = Double(t), v >= 0 else {
            confirmError = L("请输入不小于 0 的数字，如 50", "Enter a number ≥ 0, e.g. 50")
            return
        }
        confirmError = ""
        confirmAbove = numFormat(v)
        let cfgLayer = Double(SettingsIO.configScalar("require_text_confirm_above_usd") ?? "") ?? 50.0
        persistOverride("require_text_confirm_above_usd", v, dropWhen: v == cfgLayer)
    }

    private func commitTrashDays() {
        let t = trashDays.trimmingCharacters(in: .whitespaces)
        guard let v = Int(t), v >= 0 else {
            trashDaysError = L("请输入整数天数，如 60（0 = 永不自动清）",
                               "Enter a whole number of days, e.g. 60 (0 = never auto-purge)")
            return
        }
        trashDaysError = ""
        trashDays = String(v)
        let cfgLayer = Int(SettingsIO.configScalar("retention_days") ?? "") ?? 60
        persistOverride("trash_retention_days", v, dropWhen: v == cfgLayer)
    }

    private func commitTargetRepo() {
        let cfgLayer = SettingsIO.configNestedScalar(block: "execution", key: "default_target_repo")
            ?? "~/Projects/your-workbench"
        var v = targetRepo.trimmingCharacters(in: .whitespaces)
        if v.isEmpty {
            v = cfgLayer
            targetRepo = v
        }
        persistOverride("default_target_repo", v, dropWhen: v == cfgLayer)
        targetRepoExists = dirExists(v)
    }

    /// Create the task working folder (with a best-effort background
    /// `git init` so branch-based delivery works out of the box).
    private func createTargetRepoDir() {
        let p = (targetRepo.trimmingCharacters(in: .whitespaces) as NSString).expandingTildeInPath
        guard !p.isEmpty else { return }
        do {
            try FileManager.default.createDirectory(atPath: p, withIntermediateDirectories: true)
            targetRepoExists = true
            noteSaved()
            DispatchQueue.global(qos: .utility).async {
                if !FileManager.default.fileExists(atPath: p + "/.git") {
                    _ = Shell.run("/usr/bin/git", ["-C", p, "init", "-q"])
                }
            }
        } catch {
            noteError(L("创建目录失败：", "Couldn't create the folder: ") + error.localizedDescription)
        }
    }

    // MARK: - 语气档案 helpers (docs/VOICE.md)

    // The two candidate files, exactly as act/executor.py
    // resolve_voice_profile() derives them from AIASSISTANT_HOME.
    private var voicePrivatePath: String { AppPaths.stateRoot + "/state/voice-profile.md" }
    private var voiceDefaultPath: String { AppPaths.stateRoot + "/config/voice-profile.default.md" }

    /// The file the executor injects right now — or, when injection is
    /// disabled, the one that WOULD take effect after re-enabling (the Open
    /// button intentionally opens that one). nil = neither file exists.
    private var voiceEffectivePath: String? {
        if voicePrivateExists { return voicePrivatePath }
        if voiceDefaultExists { return voiceDefaultPath }
        return nil
    }

    private var voiceStatusText: String {
        if !voiceEnabled { return L("已停用", "Disabled") }
        if voicePrivateExists { return L("你的私有档案", "Your private profile") }
        if voiceDefaultExists { return L("出厂默认（作者风格）", "Shipped default (author's style)") }
        return L("无档案（不注入）", "No profile (nothing injected)")
    }

    private var voiceDotColor: Color {
        if !voiceEnabled { return Color.secondary.opacity(0.4) }
        if voicePrivateExists { return .green }
        if voiceDefaultExists { return .blue }
        return .orange
    }

    private func refreshVoiceProfileStatus() {
        voicePrivateExists = FileManager.default.fileExists(atPath: voicePrivatePath)
        voiceDefaultExists = FileManager.default.fileExists(atPath: voiceDefaultPath)
    }

    /// 「从我的消息生成/更新档案」— run `<runtime python> -m act.voice_gen`
    /// asynchronously (same subprocess + progress + result-feedback pattern as
    /// the iMessage test button). Success → green one-liner + status refresh;
    /// failure → the error verbatim in orange.
    private func runVoiceGen() {
        guard !voiceGenRunning else { return }
        voiceGenRunning = true
        voiceGenFailed = false
        voiceGenStatus = L("生成中……会读取你最近发出的 Slack 消息，可能需要几分钟。",
                           "Generating… reads Slack messages you sent recently; this can take a few minutes.")
        Analytics.log("mw_voice_gen")
        DispatchQueue.global(qos: .userInitiated).async {
            let (code, tail) = Self.runVoiceGenProcess()
            DispatchQueue.main.async {
                voiceGenRunning = false
                voiceGenFailed = code != 0
                let trimmed = tail.trimmingCharacters(in: .whitespacesAndNewlines)
                if code == 0 {
                    // stdout's last non-empty line is the tool's one-line result
                    let line = trimmed.components(separatedBy: "\n")
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                        .last { !$0.isEmpty }
                    voiceGenStatus = line ?? L("已生成 ✓", "Generated ✓")
                } else {
                    voiceGenStatus = trimmed.isEmpty
                        ? L("生成失败（退出码 \(code)），没有更多输出。",
                            "Generation failed (exit code \(code)) with no further output.")
                        : trimmed
                }
                // refresh even on failure — a backup/restore may have
                // changed which file exists
                refreshVoiceProfileStatus()
                Analytics.log("mw_voice_gen_done", fields: ["ok": code == 0])
            }
        }
    }

    /// Blocking — call from a background queue only. Mirrors the iMessage
    /// send-test runner: pinned runtime python, cwd + AIASSISTANT_HOME =
    /// repo root, stdout+stderr merged, tail returned for display.
    private static func runVoiceGenProcess() -> (Int32, String) {
        let py = RuntimePython.resolve()
        let root = AppPaths.stateRoot
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = ["-m", "act.voice_gen"]
        p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        env["AIASSISTANT_HOME"] = root
        env["AIASSISTANT_UI_LANG"] = LanguageMirror.current   // §15: python copy matches the app language
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (127, error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String((String(data: data, encoding: .utf8) ?? "").suffix(600)))
    }

    private func openConfigYaml() {
        let root = AppPaths.stateRoot
        let cfg = root + "/config.yaml"
        let fm = FileManager.default
        if !fm.fileExists(atPath: cfg) {
            try? fm.copyItem(atPath: root + "/config.example.yaml", toPath: cfg)
        }
        if fm.fileExists(atPath: cfg) {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: cfg)])
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: root, isDirectory: true))
        }
    }
}

// One credential row in 设置·凭证 — status dot + SecureField paste + save +
// helper link buttons (http URL or repo-relative doc path).
//
// v0.14 (audit 6.1/6.4): EVERY row verifies on save with a real probe —
// Anthropic → GET /v1/models, Slack → auth.test, Gmail app password → IMAP
// LOGIN via the runtime python. The dot is green ONLY after a successful
// verification; a failed probe shows the classified plain-language reason
// inline. Gmail passwords are stripped of ALL whitespace before storing
// (Google renders them as "abcd efgh ijkl mnop"; pasting that used to fail).
struct CredentialRowView: View {
    enum Kind { case plain, anthropic, slack, gmail }

    let title: String
    let secretName: String                       // file name under config/secrets/
    let legacyPath: String                       // tilde form ok
    let links: [(label: String, target: String)] // http(s) URL or repo-relative path
    var kind: Kind = .plain

    // 0 = unset, 1 = legacy path, 2 = saved (not verified),
    // 3 = verified ok, 4 = verification failed (bad credential)
    @State private var input = ""
    @State private var state = 0
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
                SecureField(L("粘贴后点保存（只存本机，保存即验证）",
                              "Paste, then Save (stored locally; verified on save)"),
                            text: $input)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                Button(L("保存", "Save")) { save() }
                    .controlSize(.small)
                    .disabled(validating
                        || input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if kind != .plain {
                    // probe the pasted credential (or the stored one when the
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
        }
        .padding(.vertical, 2)
        .onAppear { refreshState() }
    }

    private var dotColor: Color {
        switch state {
        case 3: return .green
        case 4: return .red
        case 2, 1: return .yellow
        default: return Color.secondary.opacity(0.4)
        }
    }

    private var stateText: String {
        switch state {
        case 4: return L("验证失败", "verification failed")
        case 3: return L("已验证 ✓", "verified ✓")
        case 2: return kind == .plain ? L("已保存（App 内管理）", "Saved (managed in-app)")
                                      : L("已保存（未验证）", "saved (not verified)")
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
        var token = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if kind == .gmail {
            // audit 6.4: Google displays app passwords as "abcd efgh ijkl
            // mnop" — inner whitespace is never part of the password.
            token = token.filter { !$0.isWhitespace }
        }
        guard !token.isEmpty else { return }
        do {
            try SecretsIO.save(secretName, token: token)
            input = ""
            refreshState()
            Analytics.log("mw_secret_save", fields: ["name": secretName])
            // v0.19.0 funnel (C's milestone, folded into Swift): a saved Gmail
            // app password configures the Gmail ingest source. The Anthropic key
            // is the LLM credential (not an ingest source), so it is excluded.
            // firstReach dedups — the first configured source wins.
            if kind == .gmail { Analytics.firstReach("ingest_configured") }
            if kind == .gmail && !Self.looksLikeAppPassword(token) {
                setNote(L("提示：应用密码通常是 16 位字母——检查是否粘贴了别的东西。仍会尝试验证…",
                          "Heads-up: app passwords are usually 16 letters — check you pasted the right thing. Verifying anyway…"),
                        .orange)
            }
            // never leave an invalid credential looking green — probe right away
            runVerify(token, savedFirst: true)
        } catch {
            setNote(L("保存失败: ", "Save failed: ") + error.localizedDescription, .red)
        }
    }

    /// 16 alphanumerics (Google app-password shape) — advisory only.
    private static func looksLikeAppPassword(_ s: String) -> Bool {
        s.count == 16 && s.allSatisfy { $0.isLetter || $0.isNumber }
    }

    // MARK: verification

    private func setNote(_ text: String, _ color: Color) {
        note = text
        noteColor = color
    }

    /// 验证 button: probe the field content; empty field → the stored secret.
    private func verify() {
        var candidate = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if kind == .gmail { candidate = candidate.filter { !$0.isWhitespace } }
        let secret: String
        if !candidate.isEmpty {
            secret = candidate
        } else if let stored = try? String(contentsOfFile: SecretsIO.path(secretName),
                                           encoding: .utf8)
                    .trimmingCharacters(in: .whitespacesAndNewlines), !stored.isEmpty {
            secret = stored
        } else {
            setNote(L("先粘贴（或保存）一个凭证再验证", "Paste (or save) a credential first"), .orange)
            return
        }
        runVerify(secret, savedFirst: false)
    }

    private func runVerify(_ secret: String, savedFirst: Bool) {
        let done: @MainActor (KeyProbe.Outcome) -> Void = { outcome in
            validating = false
            handleOutcome(outcome, savedFirst: savedFirst)
        }
        switch kind {
        case .anthropic:
            beginValidating(savedFirst)
            KeyProbe.anthropic(key: secret, done: done)
        case .slack:
            beginValidating(savedFirst)
            KeyProbe.slack(token: secret, done: done)
        case .gmail:
            let address = Self.effectiveGmailAddress()
            guard !address.isEmpty else {
                setNote((savedFirst ? L("已保存，但还没填 Gmail 地址——", "Saved, but no Gmail address yet — ")
                                    : L("还没填 Gmail 地址——", "No Gmail address yet — "))
                    + L("在上面「Gmail 地址」填好后点「验证」。",
                        "fill in \"Gmail address\" above, then click Verify."),
                        .orange)
                return
            }
            beginValidating(savedFirst)
            // legacy stored values may still carry inner spaces — normalize
            KeyProbe.gmailIMAP(address: address,
                               password: secret.filter { !$0.isWhitespace }, done: done)
        case .plain:
            setNote(L("已保存 ✓", "Saved ✓"), .secondary)
        }
    }

    private func beginValidating(_ savedFirst: Bool) {
        validating = true
        setNote(savedFirst ? L("已保存，验证中…", "Saved — verifying…")
                           : L("验证中…", "Verifying…"), .secondary)
    }

    private func handleOutcome(_ outcome: KeyProbe.Outcome, savedFirst: Bool) {
        switch outcome {
        case .ok(let detail):
            state = 3
            let suffix = detail.map { " " + $0 } ?? ""
            setNote((savedFirst ? L("已保存 ✓ 验证通过", "Saved ✓ verified")
                                : L("验证通过 ✓", "Verified ✓")) + suffix, .green)
            Analytics.log("mw_key_validate",
                          fields: ["name": secretName, "result": "ok"])
        case .unauthorized(let why):
            state = 4
            setNote((savedFirst ? L("已保存，但验证失败：", "Saved, but verification FAILED: ")
                                : L("验证失败：", "Verification failed: ")) + humanAuthReason(why),
                    .red)
            Analytics.log("mw_key_validate",
                          fields: ["name": secretName, "result": "unauthorized"])
        case .failed(let why):
            // network/service — the credential's verdict is unknown, keep it
            // saved-but-unverified rather than pretending either way.
            if state == 3 || state == 4 { state = 2 }
            setNote(L("无法验证（网络/服务问题），稍后点「验证」重试：",
                      "Couldn't verify (network/service) — click Verify again later: ") + why,
                    .orange)
            Analytics.log("mw_key_validate",
                          fields: ["name": secretName, "result": "error"])
        }
    }

    /// Classified plain-language reason (audit 6.1): what went wrong + the
    /// one action that fixes it; the raw code rides along in parentheses.
    private func humanAuthReason(_ raw: String) -> String {
        switch kind {
        case .slack:
            return L("token 无效——到 api.slack.com/apps → OAuth & Permissions 重新生成 User OAuth Token 再粘贴（\(raw)）",
                     "The token is invalid — regenerate the User OAuth Token at api.slack.com/apps → OAuth & Permissions and paste it again (\(raw))")
        case .gmail:
            // Workspace-admin telltales (docs/GMAIL_SETUP.md caveat, surfaced
            // as a plain sentence right where the failure happens): Google
            // rejects the LOGIN with these strings when the domain admin has
            // disabled IMAP / forces web login (app passwords included).
            let lower = raw.lowercased()
            if lower.contains("disabled for your domain")
                || lower.contains("web login required")
                || lower.contains("imap access is disabled") {
                return L("你的公司 Google Workspace 禁用了这条登录路（\(raw)）——此路不通，不用再试；你读邮件的画面仍会经屏幕录制链进入系统。",
                         "Your company's Google Workspace has disabled this login path (\(raw)) — it's a dead end, don't keep trying; mail you read on screen still reaches the system via the recording pipeline.")
            }
            if lower.contains("application-specific password required") {
                return L("粘贴的是账号普通密码——这里需要的是应用专用密码：点「打开 Google 应用专用密码页」生成一个再粘贴（\(raw)）",
                         "That's your normal account password — this needs an app password: click \"Open Google app passwords\" to generate one and paste it (\(raw))")
            }
            return L("应用密码或地址不对——重新生成一个应用专用密码再粘贴（\(raw)）",
                     "Wrong app password or address — generate a fresh app password and paste it (\(raw))")
        case .anthropic:
            return L("key 无效——到 console.anthropic.com 重新生成，回来粘贴保存（\(raw)）",
                     "The key is invalid — regenerate it at console.anthropic.com, then paste and save (\(raw))")
        case .plain:
            return raw
        }
    }

    /// Effective Gmail address for the IMAP probe: override → config.yaml.
    /// (The address field persists on change, so disk is current.)
    private static func effectiveGmailAddress() -> String {
        if let v = SettingsIO.readOverrides()["gmail_address"] as? String, !v.isEmpty {
            return v
        }
        return SettingsIO.configScalar("address") ?? ""
    }

    private func openLink(_ target: String) {
        if target.hasPrefix("http") {
            if let url = URL(string: target) { NSWorkspace.shared.open(url) }
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: AppPaths.stateRoot + "/" + target))
        }
    }
}

// MARK: - Credential probes (P1-2 Anthropic; v0.14 Slack + Gmail)
//
// Anthropic: GET /v1/models — free (no tokens billed), fast, 401 on a bad key.
// Slack: POST auth.test — the canonical token check; ok:false carries an error
// code we classify. Gmail: a real IMAP LOGIN via the runtime python (imaplib),
// password passed over stdin so it NEVER appears in a process argv (`ps`).

enum KeyProbe {
    enum Outcome {
        case ok(String?)           // verified; optional detail ("@user", address)
        case unauthorized(String)  // the credential itself is bad
        case failed(String)        // network / service — verdict unknown
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
                    outcome = .ok(nil)
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

    /// Slack auth.test (audit 6.1) — token-shaped errors → .unauthorized.
    static func slack(token: String, done: @escaping @MainActor (Outcome) -> Void) {
        var req = URLRequest(url: URL(string: "https://slack.com/api/auth.test")!)
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        URLSession.shared.dataTask(with: req) { data, _, err in
            let outcome: Outcome
            if let err {
                outcome = .failed(err.localizedDescription)
            } else if let data,
                      let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                      let ok = obj["ok"] as? Bool {
                if ok {
                    let user = obj["user"] as? String
                    outcome = .ok(user.map { "@" + $0 })
                } else {
                    let code = obj["error"] as? String ?? "unknown_error"
                    let tokenErrors = ["invalid_auth", "not_authed", "account_inactive",
                                       "token_revoked", "token_expired"]
                    outcome = tokenErrors.contains(code) ? .unauthorized(code) : .failed(code)
                }
            } else {
                outcome = .failed("no response")
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { done(outcome) }
            }
        }.resume()
    }

    /// Gmail app password (audit 6.1/6.4): one real IMAP LOGIN through the
    /// pinned runtime python — the same interpreter the radar runs under.
    static func gmailIMAP(address: String, password: String,
                          done: @escaping @MainActor (Outcome) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = gmailProbeSync(address: address, password: password)
            DispatchQueue.main.async {
                MainActor.assumeIsolated { done(outcome) }
            }
        }
    }

    /// Blocking — call from a background queue only.
    private static func gmailProbeSync(address: String, password: String) -> Outcome {
        let py = RuntimePython.resolve()
        let code = """
        import imaplib, sys
        pw = sys.stdin.read().strip()
        try:
            c = imaplib.IMAP4_SSL("imap.gmail.com", timeout=15)
            c.login(sys.argv[1], pw)
            try:
                c.logout()
            except Exception:
                pass
            print("PROBE_OK")
        except imaplib.IMAP4.error as e:
            print("PROBE_AUTH " + str(e)[:200])
            sys.exit(1)
        except Exception as e:
            print("PROBE_NET " + str(e)[:200])
            sys.exit(2)
        """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = ["-c", code, address]
        let inPipe = Pipe()
        let outPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = outPipe
        do { try p.run() } catch {
            return .failed(L("找不到可用的 python（", "No usable python (")
                + error.localizedDescription + ")")
        }
        try? inPipe.fileHandleForWriting.write(contentsOf: Data(password.utf8))
        try? inPipe.fileHandleForWriting.close()
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let out = String(data: data, encoding: .utf8) ?? ""
        let last = out.split(separator: "\n").last.map(String.init)?
            .trimmingCharacters(in: .whitespaces) ?? ""
        if last == "PROBE_OK" { return .ok(address) }
        if last.hasPrefix("PROBE_AUTH") {
            return .unauthorized(String(last.dropFirst("PROBE_AUTH".count))
                .trimmingCharacters(in: .whitespaces))
        }
        if last.hasPrefix("PROBE_NET") {
            return .failed(String(last.dropFirst("PROBE_NET".count))
                .trimmingCharacters(in: .whitespaces))
        }
        return .failed(last.isEmpty ? "probe produced no output" : String(last.suffix(200)))
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
