// TerminalLauncher.swift — 双击「在终端运行」: open an app-generated command
// in a NEW WINDOW of the user's chosen terminal app.
//
// Mechanisms (plain Apple Events per app — no Accessibility hacks):
// - Ghostty (≥1.2 scripting dictionary, verified live on 1.3.1):
//     new window with configuration {command:"/bin/zsh -lc '<cmd>'"}
//   opens on the RUNNING instance; the command string is shell-word parsed
//   (single quotes respected). The CLI route (`open -na Ghostty --args -e …`)
//   was tried first and REJECTED: it spawns a second app instance and never
//   started the command on this machine.
// - Terminal.app: classic `do script "<cmd>"` — new window, login shell.
// - iTerm2: `create window with default profile command "/bin/zsh -lc '<cmd>'"`
//   per its documented scripting API. Offered only when installed; NOT
//   live-verified (iTerm2 absent on the dev machine).
// New window (not new tab) everywhere: it's what all three support scriptably
// without knowing whether a front window exists.
//
// /bin/zsh -lc wrapping (Ghostty/iTerm2): their `command` execs without a
// login environment and `claude` lives in ~/.local/bin, so PATH must come
// from a login shell. Terminal.app's do script already runs in one.
//
// First use per terminal app shows the one-time macOS Automation consent
// ("…wants access to control Terminal.app"); Info.plist carries
// NSAppleEventsUsageDescription for it.
//
// SECURITY: only APP-GENERATED command strings may reach launch() — the
// pipeline's copy_cmd or the Swift-built "claude --resume <id>". Never wire
// user-typed or remote content into it: the string becomes a shell command
// line. The quoting below is defense in depth, not an injection gate.
//
// This file is deliberately self-contained (Foundation/AppKit only, own
// osascript runner, UserDefaults read instead of Prefs) so the CLI
// verification harness can compile it standalone.

import AppKit
import Foundation

enum TerminalApp: String, CaseIterable {
    case ghostty
    case terminal
    case iterm2

    var bundleID: String {
        switch self {
        case .ghostty: return "com.mitchellh.ghostty"
        case .terminal: return "com.apple.Terminal"
        case .iterm2: return "com.googlecode.iterm2"
        }
    }

    var displayName: String {
        switch self {
        case .ghostty: return "Ghostty"
        case .terminal: return "Terminal"
        case .iterm2: return "iTerm2"
        }
    }

    var isInstalled: Bool {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) != nil
    }
}

enum TerminalLauncher {
    /// Pickable apps for 设置 (installed only, declaration order).
    static var installed: [TerminalApp] { TerminalApp.allCases.filter(\.isInstalled) }

    /// UserDefaults "terminalApp" — pure UI preference, deliberately NOT in
    /// settings_overrides.json (cardSortOrder 先例). Default: Ghostty when
    /// installed, else Terminal.app; a stored choice that got uninstalled
    /// falls back the same way.
    static var preferred: TerminalApp {
        if let raw = UserDefaults.standard.string(forKey: "terminalApp"),
           let app = TerminalApp(rawValue: raw), app.isInstalled { return app }
        return TerminalApp.ghostty.isInstalled ? .ghostty : .terminal
    }

    /// Run `command` in a new window of `app` (nil → preferred). osascript
    /// blocks (TCC consent can hold the Apple Event for up to ~2 min), so it
    /// runs off-main; completion comes back on the main queue.
    static func launch(_ command: String, in app: TerminalApp? = nil,
                       completion: @escaping (Bool) -> Void = { _ in }) {
        let target = app ?? preferred
        let script = script(for: target, command: command)
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, tail) = runOsascript(script)
            if !ok { NSLog("TerminalLauncher: osascript failed for %@: %@",
                           target.rawValue, tail) }
            DispatchQueue.main.async { completion(ok) }
        }
    }

    /// AppleScript per app. `command` is a full shell command LINE: for
    /// Terminal.app it goes to do script as-is (the shell parses it); for
    /// Ghostty/iTerm2 it rides single-quoted inside /bin/zsh -lc.
    static func script(for app: TerminalApp, command: String) -> String {
        switch app {
        case .ghostty:
            let cmd = appleScriptQuoted("/bin/zsh -lc " + shellSingleQuoted(command))
            return """
            tell application "Ghostty"
                new window with configuration {command:\(cmd)}
                activate
            end tell
            """
        case .terminal:
            return """
            tell application "Terminal"
                do script \(appleScriptQuoted(command))
                activate
            end tell
            """
        case .iterm2:
            let cmd = appleScriptQuoted("/bin/zsh -lc " + shellSingleQuoted(command))
            return """
            tell application "iTerm2"
                create window with default profile command \(cmd)
                activate
            end tell
            """
        }
    }

    /// POSIX single-quoting: the whole string becomes one shell word; every
    /// embedded ' is closed–escaped–reopened.
    static func shellSingleQuoted(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// AppleScript string literal (backslash and double-quote escapes).
    static func appleScriptQuoted(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    /// Own runner (not Shell.run) to keep the file standalone-compilable.
    /// Blocking — background queue only.
    private static func runOsascript(_ script: String) -> (Bool, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (false, error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let out = String(data: data, encoding: .utf8) ?? ""
        return (p.terminationStatus == 0, String(out.suffix(400)))
    }
}
