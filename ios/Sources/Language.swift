// Language.swift — iOS UI-language store. Mirrors the Mac LanguageStore posture
// (mac/Sources/L10n.swift) but for iOS: it writes the SHARED LanguageMirror so
// the shared L(_:_:) picks the right string. First run follows the system
// locale; an explicit choice persists to UserDefaults.

import Foundation
import SwiftUI

@MainActor
final class LanguageStore: ObservableObject {
    static let shared = LanguageStore()
    private static let key = "language"

    @Published var lang: String {
        didSet {
            LanguageMirror.current = lang
            UserDefaults.standard.set(lang, forKey: Self.key)
        }
    }

    private init() {
        let v: String
        if let stored = UserDefaults.standard.string(forKey: Self.key) {
            v = stored == "en" ? "en" : "zh"
        } else {
            v = (Locale.preferredLanguages.first ?? "en").hasPrefix("zh") ? "zh" : "en"
        }
        lang = v
        LanguageMirror.current = v
    }

    func toggle() { lang = (lang == "en" ? "zh" : "en") }
}
