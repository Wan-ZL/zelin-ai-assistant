// I18n.swift — L() 双语文案 + LanguageMirror（界面语言的 nonisolated 镜像）
// The shell's own copy (lite 2.0, CONTRACT §54.3): compiled with shell/Sources/*.swift.
// Foundation-only. Was shared/Sources/I18n.swift (Mac app + iOS app); the shell's
// LanguageStore (ShellSupport.swift) is the only writer of LanguageMirror.current.

import Foundation

/// Nonisolated mirror of the current UI language so L() stays callable off the
/// main actor. Written only from the main actor; a stale read during a switch
/// is benign. The shell's LanguageStore sets it.
enum LanguageMirror {
    nonisolated(unsafe) static var current = "zh"
}

/// L("中文", "English") — inline bilingual literal, picked per current UI language.
func L(_ zh: String, _ en: String) -> String {
    LanguageMirror.current == "en" ? en : zh
}
