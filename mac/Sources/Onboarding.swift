// Onboarding.swift — first-launch recording consent (P0-11)
//
// A fresh install must NOT capture anything before the user has seen, in
// plain words, what is recorded, where it goes and how long it is kept.
// The prompt shows exactly once (Prefs "recordingConsentShown"); existing
// installs that already carry a recordingMode value are never asked.

import AppKit
import Foundation

@MainActor
enum RecordingConsent {
    /// True on a fresh install only: no recording mode ever chosen AND no
    /// consent recorded. Either key present ⇒ never prompt again.
    static var needsPrompt: Bool {
        UserDefaults.standard.string(forKey: "recordingMode") == nil
            && !Prefs.bool("recordingConsentShown", default: false)
    }

    /// One-time bilingual consent alert. Whatever the choice, it is persisted
    /// and the prompt never returns; recording starts only for the two
    /// opt-in buttons. "隐私说明…" opens PRIVACY.md and re-presents.
    static func present() {
        // LSUIElement app: without explicit activation the modal alert can
        // open BEHIND the frontmost app and look like a silent hang.
        NSApp.activate(ignoringOtherApps: true)
        let privacyButton = NSApplication.ModalResponse(
            rawValue: NSApplication.ModalResponse.alertThirdButtonReturn.rawValue + 1)
        while true {
            let alert = NSAlert()
            alert.messageText = L("屏幕录制与隐私", "Screen Recording & Privacy")
            alert.informativeText = L(
                """
                Zelin AI Assistant 的核心功能依赖持续屏幕录制（OCR 文字识别）。

                • 采集什么：屏幕上的可见文字（OCR）；「屏幕 + 音频」模式另加麦克风语音转写
                • 去哪里：先写入本地数据库和 Obsidian vault；摘要会经 claude CLI 发送到 Anthropic API 做分析
                • 保留多久：原始录屏媒体本地保留约 1 天后自动清理；提炼后的笔记留在本地 vault

                现在开始录制吗？之后可随时在「设置 → 录制」中更改。
                """,
                """
                Zelin AI Assistant's core features rely on continuous screen recording (OCR text capture).

                • What is captured: visible on-screen text (OCR); "Screen + Audio" adds microphone transcription
                • Where it goes: stored locally first (database + Obsidian vault); summaries are sent to the Anthropic API via the claude CLI for analysis
                • How long it is kept: raw recordings are cleaned up locally after ~1 day; distilled notes stay in your local vault

                Start recording now? You can change this anytime in Settings → Recording.
                """)
            alert.addButton(withTitle: L("仅屏幕", "Screen Only"))
            alert.addButton(withTitle: L("屏幕 + 音频", "Screen + Audio"))
            alert.addButton(withTitle: L("暂不开启", "Not Now"))
            alert.addButton(withTitle: L("隐私说明…", "Privacy Details…"))
            let resp = alert.runModal()
            if resp == privacyButton {
                openPrivacyDoc()
                continue  // re-present after reading
            }
            let mode: String
            switch resp {
            case .alertFirstButtonReturn: mode = "screen"
            case .alertSecondButtonReturn: mode = "screen_audio"
            default: mode = "off"
            }
            UserDefaults.standard.set(true, forKey: "recordingConsentShown")
            Analytics.log("recording_consent", fields: ["choice": mode])
            if mode != "off", !RecordingController.hasScreenPermission() {
                RecordingController.requestScreenPermission()
            }
            // persists recordingMode and (for the opt-in modes) starts the
            // engine via the normal stop→start path
            RecordingController.shared.setMode(mode)
            return
        }
    }

    private static func openPrivacyDoc() {
        let local = AppPaths.stateRoot + "/docs/PRIVACY.md"
        if FileManager.default.fileExists(atPath: local) {
            NSWorkspace.shared.open(URL(fileURLWithPath: local))
        } else if let url = URL(
            string: "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/PRIVACY.md") {
            NSWorkspace.shared.open(url)
        }
    }
}
