// I18n.swift — L() 双语文案 + LanguageMirror（界面语言的 nonisolated 镜像）
// SHARED between the Mac app and the iOS app. Foundation-only by contract.
// Was the L()/LanguageMirror half of mac/Sources/L10n.swift — moved verbatim.
// The Mac-only LanguageStore (ObservableObject, reads SettingsIO) stays in
// mac/Sources/L10n.swift and keeps writing LanguageMirror.current; the iOS app
// owns its own language store (ios/Sources) and writes the same mirror.

import Foundation

/// Nonisolated mirror of the current UI language so L() stays callable off the
/// main actor. Written only from the main actor; a stale read during a switch
/// is benign. Both clients (Mac LanguageStore, iOS LanguageStore) set it.
enum LanguageMirror {
    nonisolated(unsafe) static var current = "zh"
}

/// L("中文", "English") — inline bilingual literal, picked per current UI language.
func L(_ zh: String, _ en: String) -> String {
    LanguageMirror.current == "en" ? en : zh
}
