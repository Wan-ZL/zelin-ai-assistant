// L10n.swift — L() 双语文案 / LanguageStore / LanguageMirror（界面语言）
// Mechanically split from main.swift — zero logic changes.

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
        let v = (SettingsIO.readOverrides()["language"] as? String) == "en" ? "en" : "zh"
        lang = v
        LanguageMirror.current = v
    }
}

/// Nonisolated mirror of LanguageStore.lang so L() stays callable off the main
/// actor (e.g. dependency checks build row strings on a background queue).
/// Written only from the main actor; a stale read during a switch is benign.
enum LanguageMirror {
    nonisolated(unsafe) static var current = "zh"
}

/// L("中文", "English") — inline bilingual literal, picked per current UI language.
func L(_ zh: String, _ en: String) -> String {
    LanguageMirror.current == "en" ? en : zh
}
