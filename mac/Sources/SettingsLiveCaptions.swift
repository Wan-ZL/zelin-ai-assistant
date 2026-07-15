// SettingsLiveCaptions.swift — 设置 → 实时字幕: enable toggle, engine/source
// pickers, the two BYO keys (Doubao speech + Ark translation, stored via the
// existing SecretsIO contract), translation options, and overlay appearance.
// Registered as its own SettingsSectionDescriptor (anchor "live_captions" —
// the overlay's gear button deep-links here). Same section style as
// SettingsSlack/SettingsGmail.

import AppKit
import SwiftUI

struct LiveCaptionsSettingsSection: View {
    @ObservedObject private var cap = LiveCaptionsController.shared
    @ObservedObject private var i18n = LanguageStore.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(L("开启实时字幕悬浮窗", "Show the live-captions overlay"),
                   isOn: Binding(get: { cap.enabled }, set: { cap.setEnabled($0) }))
            Text(L("歌词式置顶字幕：实时转写麦克风和/或系统声音。也可以从菜单栏「录制」菜单开关。",
                   "Lyrics-style always-on-top captions: live transcription of the microphone and/or system audio. Also toggleable from the menu-bar Recording menu."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            if !cap.statusText.isEmpty {
                statusRow(cap.statusText, isError: cap.statusIsError)
            }
            if !cap.sourceNote.isEmpty {
                statusRow(cap.sourceNote, isError: true)
            }
            Divider()

            pickerRow(L("识别引擎", "Recognition engine"), selection: $cap.engineChoice) {
                Text(L("自动", "Auto")).tag("auto")
                Text(L("豆包在线", "Doubao (online)")).tag("doubao")
                Text(L("Apple 本地", "Apple on-device")).tag("apple")
            }
            Text(engineFootnote)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            pickerRow(L("声音来源", "Audio source"), selection: $cap.source) {
                Text(L("麦克风 + 系统声音", "Mic + system audio")).tag("both")
                Text(L("仅麦克风", "Microphone only")).tag("mic")
                Text(L("仅系统声音", "System audio only")).tag("system")
            }
            Text(L("系统声音走「屏幕录制」权限（录制引擎已经用它）；麦克风首次开启时会弹系统授权。",
                   "System audio rides the Screen Recording grant (the recording engine already uses it); the mic prompts for permission on first enable."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            pickerRow(L("本地识别语言（仅 Apple 引擎）", "On-device language (Apple engine only)"),
                      selection: $cap.appleLocale) {
                Text(L("中文", "Chinese")).tag("zh")
                Text("English").tag("en")
            }
            Text(L("豆包引擎自动中英混识，无需选择。",
                   "The Doubao engine code-switches zh/en automatically — no choice needed."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Divider()

            CredentialRowView(
                title: L("豆包语音 API Key（识别用）", "Doubao speech API key (recognition)"),
                secretName: SecretsIO.volcanoSpeechFile,
                legacyPath: "",
                links: [(L("语音控制台", "Speech console"),
                         "https://console.volcengine.com/speech/app")],
                kind: .plain)
            CredentialRowView(
                title: L("Ark API Key（翻译用，另一个 Key）", "Ark API key (translation — a different key)"),
                secretName: SecretsIO.volcanoArkFile,
                legacyPath: "",
                links: [(L("Ark 控制台", "Ark console"),
                         "https://console.volcengine.com/ark")],
                kind: .plain)
            Text(L("两个 Key 来自火山引擎的两个不同控制台：语音 Key 管识别，Ark Key 管翻译。都只存本机 config/secrets/，只有 App 自己读（Python/cron 永不读取），保存后在开启字幕时生效。",
                   "The two keys come from two different Volcano consoles: the speech key does recognition, the Ark key does translation. Both live only in local config/secrets/, read only by the app itself (never by Python/cron), and take effect when captions start."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()

            Toggle(L("翻译字幕（需要 Ark Key + 豆包引擎）",
                     "Translate captions (needs the Ark key + Doubao engine)"),
                   isOn: $cap.translateEnabled)
            if !cap.translationNote.isEmpty {
                statusRow(cap.translationNote, isError: false)
            }
            pickerRow(L("翻译方向", "Translation direction"), selection: $cap.translateDirection) {
                Text(L("自动（按句判断）", "Auto (per sentence)")).tag("auto")
                Text(L("中 → 英", "zh → en")).tag("zh2en")
                Text(L("英 → 中", "en → zh")).tag("en2zh")
            }
            HStack {
                Text(L("翻译模型（Ark model ID）", "Translation model (Ark model ID)"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                TextField("doubao-seed-1-6-flash", text: $cap.arkModel)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(width: 280)
                Spacer()
            }
            Divider()

            HStack {
                Text(L("字号", "Font size"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Slider(value: $cap.fontSize, in: 14...40, step: 1)
                    .frame(width: 220)
                Text("\(Int(cap.fontSize)) pt")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                Spacer()
            }
            HStack {
                Text(L("背景不透明度", "Background opacity"))
                    .font(.system(size: 12))
                    .frame(width: 220, alignment: .leading)
                Slider(value: $cap.opacity, in: 0.3...1.0)
                    .frame(width: 220)
                Text("\(Int(cap.opacity * 100))%")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                Spacer()
            }
            Text(L("费用（都是你自己的账号）：豆包流式识别约 ¥1/小时，个人实名开通即送 20 小时；翻译走 doubao-seed flash，一小时字幕的翻译费通常不到 ¥0.1，Ark 每个模型另送 50 万 token。Apple 本地引擎完全免费离线（需 macOS 26+）。字幕文本永不离开这台 Mac，只发往你自己开通的识别/翻译服务；本产品的匿名统计里也永远没有字幕内容。",
                   "Costs (all on your own account): Doubao streaming ASR ≈ ¥1/hour with 20 free hours after personal sign-up; translation via doubao-seed flash usually costs under ¥0.1 per captioned hour, and Ark grants 500k free tokens per model. The Apple on-device engine is fully free and offline (macOS 26+). Caption text never leaves this Mac except to your own recognition/translation endpoints, and never appears in this product's anonymous telemetry."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .toggleStyle(.switch)
        .font(.system(size: 12))
    }

    private var engineFootnote: String {
        if appleCaptionEngineAvailable() {
            return L("自动 = 有豆包 Key 就用豆包（中英混识、标点更好），否则用 Apple 本地（免费离线）。",
                     "Auto = Doubao when a key is saved (better zh/en mixing and punctuation), otherwise Apple on-device (free, offline).")
        }
        return L("自动 = 有豆包 Key 就用豆包。这台 Mac 低于 macOS 26，没有 Apple 本地识别可用。",
                 "Auto = Doubao when a key is saved. This Mac is below macOS 26, so Apple on-device recognition is unavailable.")
    }

    private func pickerRow<Content: View>(_ label: String, selection: Binding<String>,
                                          @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 12))
                .frame(width: 220, alignment: .leading)
            Picker("", selection: selection) { content() }
                .pickerStyle(.segmented)
                .frame(width: 280)
            Spacer()
        }
    }

    private func statusRow(_ text: String, isError: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: isError ? "exclamationmark.circle.fill" : "info.circle.fill")
                .foregroundColor(isError ? .orange : .secondary)
            Text(text)
                .font(.system(size: 11))
                .foregroundColor(isError ? .orange : .secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
