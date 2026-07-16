// L10n.swift — LanguageStore（界面语言，Mac-only ObservableObject）
// L() 与 LanguageMirror 已抽到 shared/Sources/I18n.swift（iOS 共用）；本文件
// 只保留依赖 AppKit/SwiftUI/SettingsIO 的 Mac-only LanguageStore，它照旧写
// LanguageMirror.current。Mechanically split — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// MARK: - Language (界面语言 "zh" | "en" — settings_overrides.json "language")

/// Single source of truth for the UI language. SwiftUI views observe this to
/// re-render on switch; non-SwiftUI code (NSMenu, NSAlert) reads it via L()
/// at build time, so freshly built menus/alerts pick up the new language.
@MainActor
final class LanguageStore: ObservableObject {
    static let shared = LanguageStore()
    @Published var lang: String {
        didSet { LanguageMirror.current = lang }
    }
    private init() {
        // P0-12: an explicit override always wins; with no "language" key at
        // all (first run) follow the system locale instead of hardcoding zh.
        let v: String
        if let stored = SettingsIO.readOverrides()["language"] as? String {
            v = stored == "en" ? "en" : "zh"
        } else {
            v = Self.systemDefault
            // v0.42 §15: persist the effective first-run language so the
            // python half (failures.ui_lang → cron/daemon notify copy) speaks
            // the same language as the app — launchd carries no LANG, so an
            // unpersisted zh user's notifications would otherwise flip to en.
            // NOT a silent preference change: this mirrors the de-facto UI
            // language the user is already seeing, the Settings picker shows
            // exactly this value, and changing it there works as always.
            // Idempotent (only when NEITHER overrides nor config.yaml carries
            // a language) and never overwrites an explicit choice. Best-effort:
            // a failed write (unparseable overrides file — writeOverrides is
            // fail-closed) just leaves the pre-persist behavior.
            if SettingsIO.configScalar("language") == nil {
                var merged = SettingsIO.readOverrides()
                merged["language"] = v
                try? SettingsIO.writeOverrides(merged)
            }
        }
        lang = v
        LanguageMirror.current = v
    }

    /// First-run default: zh-* system locales → "zh", everything else → "en".
    /// Persisted on first launch since v0.42 (see init) so python-side copy
    /// matches; an explicit choice saved in 设置 still always wins.
    nonisolated static var systemDefault: String {
        (Locale.preferredLanguages.first ?? "en").hasPrefix("zh") ? "zh" : "en"
    }
}

// L(_:_:) and LanguageMirror moved to shared/Sources/I18n.swift (shared with iOS).
