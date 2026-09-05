// TerminalLauncher.swift — open an app-generated command in a NEW WINDOW of
// the user's chosen terminal app via plain Apple Events (CONTRACT §68.7,
// 2026-09-05 / issue #216). Ported from the retired mac/Sources/
// TerminalLauncher.swift (live-verified on Ghostty 1.3.1); the ONLY deliberate
// differences are this header and `preferred`: the terminal choice is the
// server-owned setting 「通用 · 终端应用」(`terminal_app` in
// state/settings_overrides.json, §68.1) instead of the native app's own
// UserDefaults key `terminalApp` — the shell READS that knob (SettingsIO, read
// side only, §61.3) and never writes it.
//
// Why the shell and not the server (issue #216): the server's `.command` +
// `open -a` channel wrote a fresh, timestamp-named "script document" per
// launch, and macOS 26 asks "Allow Ghostty to execute …?" for every one of
// them — a unique filename has nothing to remember. Apple Events are
// remembered per (this app, target terminal) pair: one Automation consent,
// then silence. Info.plist carries NSAppleEventsUsageDescription for it.
//
// Mechanisms (plain Apple Events per app — no Accessibility hacks):
// - Ghostty (≥1.2 scripting dictionary, verified live on 1.3.1): a new TAB in
//   the frontmost window when one exists, else a new window (owner preference,
//   2026-07-10 — a window per card gets noisy):
//     new tab in window 1 with configuration {command:"/bin/zsh -lc '<cmd>'"}
//   `in window 1` is REQUIRED: the bare application-level `new tab …` and
//   `tell front window to new tab …` forms both fail with -1708 (event not
//   handled) on 1.3.1; only the `in <window>` parameter form works. The
//   command string is shell-word parsed (single quotes respected). The CLI
//   route (`open -na Ghostty --args -e …`) was tried first and REJECTED: it
//   spawns a second app instance and never started the command on this
//   machine.
// - Terminal.app: classic `do script "<cmd>"` — new window, login shell.
// - iTerm2: `create window with default profile command "/bin/zsh -lc '<cmd>'"`
//   per its documented scripting API. Offered only when installed; NOT
//   live-verified (iTerm2 absent on the dev machine).
// Terminal.app/iTerm2 stay new-window: it's what they support scriptably
// without knowing whether a front window exists.
//
// /bin/zsh -lc wrapping (Ghostty/iTerm2): their `command` execs without a
// login environment and `claude` lives in ~/.local/bin, so PATH must come
// from a login shell. Terminal.app's do script already runs in one.
// 例4b: a login shell alone is NOT enough — a non-interactive `zsh -lc`
// sources .zprofile/.zlogin but never .zshrc, and on this machine (and any
// standard `claude` install) ~/.local/bin is added to PATH in .zshrc, so a
// fresh Ghostty window died with `command not found: claude`. Fix: every
// EXECUTED command gets a PATH bootstrap prepended (bootstrapped(_:) below);
// the card's displayed copy text stays the readable raw command.
//
// SECURITY: only SERVER-ASSEMBLED command lines may reach launch() — the
// queue entries in state/terminal_queue/ are written by server/
// terminal_launch.py from the card projection (copy_cmd / session_id past
// SAFE_ID) or from validated settings (maintainer repo path / session id).
// The string becomes a shell command line; free-form user-typed or remote
// content must never be wired in. The quoting below is defense in depth, not
// an injection gate.
//
// This file is deliberately self-contained (Foundation/AppKit only, own
// osascript runner) so the CLI harness can compile it standalone; `runner` is
// the injection seam (the harness never spawns osascript).

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
    /// Pickable apps (installed only, declaration order).
    static var installed: [TerminalApp] { TerminalApp.allCases.filter(\.isInstalled) }

    /// `terminal_app` vocabulary (server/settings_catalog.py, §68.1) →
    /// TerminalApp; `auto` / unknown → nil (caller falls back).
    static func app(forSetting raw: String?) -> TerminalApp? {
        switch raw {
        case "ghostty": return .ghostty
        case "terminal": return .terminal
        case "iterm2": return .iterm2
        default: return nil
        }
    }

    /// Resolve the setting against what is installed — pure, so the harness
    /// can pin it: an explicit choice wins when installed; `auto`, unknown or
    /// an uninstalled choice → Ghostty when installed, else Terminal.app
    /// (server/terminal_launch resolve_terminal 同款 — one rule, two mirrors).
    static func resolve(setting raw: String?, installed: (TerminalApp) -> Bool) -> TerminalApp {
        if let chosen = app(forSetting: raw), installed(chosen) { return chosen }
        return installed(.ghostty) ? .ghostty : .terminal
    }

    /// The effective terminal: settings_overrides.json `terminal_app`
    /// (server-owned knob, §68.1; the shell only reads it) resolved against
    /// the installed apps.
    static var preferred: TerminalApp {
        resolve(setting: SettingsIO.readOverrides()["terminal_app"] as? String,
                installed: { $0.isInstalled })
    }

    /// 例4b PATH 兜底: prefix prepended to every EXECUTED command (never to
    /// the displayed copy text). ~/.local/bin = the standard `claude` install
    /// location, missing from non-interactive login-shell PATH (it's added in
    /// .zshrc, which `zsh -lc` never sources); /opt/homebrew/bin +
    /// /usr/local/bin cover brew installs on Apple silicon / Intel. Appending
    /// :$PATH keeps everything the login shell did resolve. Double quotes
    /// survive both quoting layers below: shellSingleQuoted wraps the whole
    /// line in single quotes (double quotes pass through untouched) and
    /// appleScriptQuoted escapes them for the AppleScript literal — $HOME
    /// still expands at execution time inside the target shell.
    static let pathBootstrap =
        #"export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; "#

    /// The command line that actually runs: PATH bootstrap + raw command.
    static func bootstrapped(_ command: String) -> String {
        pathBootstrap + command
    }

    /// osascript executor — injection seam. Blocking; called off-main by
    /// launch(). The harness swaps it for a recorder.
    nonisolated(unsafe) static var runner: (String) -> (Bool, String) = runOsascript

    /// Run `command` in a new window of `app` (nil → preferred). osascript
    /// blocks (TCC consent can hold the Apple Event for up to ~2 min), so it
    /// runs off-main; completion comes back on the main queue.
    /// 例4b: the PATH bootstrap is injected HERE — the one execution entry —
    /// so every caller (TerminalRelay today) gets it without touching the
    /// user-visible copy string.
    static func launch(_ command: String, in app: TerminalApp? = nil,
                       completion: @escaping (Bool) -> Void = { _ in }) {
        let target = app ?? preferred
        let script = script(for: target, command: bootstrapped(command))
        let run = runner
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, tail) = run(script)
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
                if (count of windows) > 0 then
                    new tab in window 1 with configuration {command:\(cmd)}
                else
                    new window with configuration {command:\(cmd)}
                end if
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
