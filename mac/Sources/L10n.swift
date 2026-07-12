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
        }
        lang = v
        LanguageMirror.current = v
    }

    /// First-run default: zh-* system locales → "zh", everything else → "en".
    /// Never persisted — only an explicit choice saved in 设置 writes the key.
    nonisolated static var systemDefault: String {
        (Locale.preferredLanguages.first ?? "en").hasPrefix("zh") ? "zh" : "en"
    }
}

// L(_:_:) and LanguageMirror moved to shared/Sources/I18n.swift (shared with iOS).
