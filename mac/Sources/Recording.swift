// Recording.swift — ScreenpipeRecipe / RecordingController（录制引擎）/ HotKeyCenter（全局热键）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation
import Carbon.HIToolbox  // RegisterEventHotKey (global hotkey, no TCC)

// MARK: - Screenpipe launch recipe (verbatim from the Automator applet — SHARED contract)

enum ScreenpipeRecipe {
    static let tuning =
        " --use-all-monitors --disable-meeting-detector --disable-clipboard-capture"
        + " --prioritize-input-latency --pause-extraction-on-input-ms 1500"
        + " --capture-scroll true --visual-check-interval-ms 0"
        + " --idle-capture-interval-ms 86400000 --min-capture-interval-ms 1000"

    /// Full zsh command that starts the engine detached (nohup … & → survives
    /// the app). PATH needs /opt/homebrew/bin (npx lives there). nil for "off".
    static func startCommand(mode: String) -> String? {
        let record: String
        var prep = ""
        switch mode {
        case "screen":
            record = "npx screenpipe@0.3.349 record --disable-audio\(tuning)"
                + " -l chinese -l english --retention-days 1"
        case "screen_audio":
            // flip disableAudio in store.bin first (recipe verbatim)
            prep = "[ -f \"$HOME/.screenpipe/store.bin\" ] && /usr/bin/sed -i.bak"
                + " 's/\"disableAudio\": true/\"disableAudio\": false/'"
                + " \"$HOME/.screenpipe/store.bin\"; "
            record = "npx screenpipe@0.3.349 record -a parakeet\(tuning)"
                + " -l chinese -l english --retention-days 1"
        default:
            return nil
        }
        // exec (not `nohup … &`): a GUI app's orphaned background jobs get
        // reaped by macOS (RunningBoard) before npx even writes a byte — the
        // engine must stay a direct child of the app, referenced by a Process.
        return "export PATH=\"/opt/homebrew/bin:$PATH\"; "
            + "mkdir -p \"$HOME/.screenpipe\"; "
            + prep
            + "exec \(record) >> \"$HOME/.screenpipe/engine.log\" 2>&1"
    }
}

// MARK: - Recording controller (Screenpipe engine)
//
// mode ∈ "off" | "screen" | "screen_audio", persisted in UserDefaults
// "recordingMode" (default "screen"). The engine outlives the app (nohup …&);
// liveness = `pgrep -f "screenpipe.*record"` has results (contract).

@MainActor
final class RecordingController: ObservableObject {
    static let shared = RecordingController()

    /// AppDelegate hook — refresh the menu-bar recording icon on any change.
    var onChange: (@MainActor () -> Void)?

    @Published private(set) var mode: String {
        didSet { onChange?() }
    }
    @Published private(set) var engineRunning = false {
        didSet { onChange?() }
    }

    private var applying = false
    private var checking = false

    private init() {
        let stored = UserDefaults.standard.string(forKey: "recordingMode") ?? ""
        mode = ["off", "screen", "screen_audio"].contains(stored) ? stored : "screen"
    }

    var modeLabel: String {
        switch mode {
        case "off": return L("关", "Off")
        case "screen_audio": return L("屏幕 + 音频", "Screen + Audio")
        default: return L("仅屏幕", "Screen Only")
        }
    }

    func setMode(_ newMode: String) {
        guard ["off", "screen", "screen_audio"].contains(newMode) else { return }
        UserDefaults.standard.set(newMode, forKey: "recordingMode")
        let changed = newMode != mode
        mode = newMode
        // switching mode = stop → start; re-picking a live mode with a dead
        // engine also (re)starts it.
        if changed || (newMode != "off" && !engineRunning) {
            applyMode()
        }
        Analytics.log("recording_set_mode", fields: ["mode": newMode])
    }

    /// Contract D: restart the current mode's engine — same stop→start path
    /// as re-picking a live mode. mode == "off" has no engine → no-op.
    func restartEngine() {
        Analytics.log("recording_restart", fields: ["mode": mode])
        guard mode != "off" else { return }
        applyMode()
    }

    /// stop → (start per mode). Blocking work runs off-main; engineRunning is
    /// refreshed when done.
    func applyMode() {
        guard !applying else { return }
        applying = true
        let m = mode
        DispatchQueue.global(qos: .userInitiated).async {
            Self.stopEngineBlocking()
            if m != "off" { Self.startEngineBlocking(mode: m) }
            Thread.sleep(forTimeInterval: 0.5)  // let the process surface in pgrep
            let running = Self.isEngineRunning()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.applying = false
                    self.engineRunning = running
                }
            }
        }
    }

    /// App launch: mode != off and engine not already running → start.
    /// (Zelin default: opening the app keeps screen-only recording alive.
    /// macOS may prompt for screen-recording permission — system handles it.)
    func autostartIfNeeded() {
        let m = mode
        // without the TCC grant the engine exits instantly — surface the
        // system prompt (first time only; afterwards macOS stays silent)
        if m != "off" && !Self.hasScreenPermission() { Self.requestScreenPermission() }
        DispatchQueue.global(qos: .userInitiated).async {
            var running = Self.isEngineRunning()
            _ = Shell.ok("echo \"[app $(date '+%F %T')] autostart mode=\(m) running=\(running)\" >> \"$HOME/.screenpipe/engine.log\"")
            if m != "off" && !running {
                Self.startEngineBlocking(mode: m)
                Thread.sleep(forTimeInterval: 0.5)
                running = Self.isEngineRunning()
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.engineRunning = running }
            }
        }
    }

    // MARK: screen-recording permission (TCC) — the #1 reason the engine
    // "starts then instantly exits with 0 monitors" when launched by this app.

    /// True when macOS has granted this app Screen Recording.
    nonisolated static func hasScreenPermission() -> Bool {
        CGPreflightScreenCaptureAccess()
    }

    /// One-time system prompt; also adds the app to the Screen Recording list.
    nonisolated static func requestScreenPermission() {
        _ = CGRequestScreenCaptureAccess()
    }

    static func openScreenRecordingSettings() {
        if let url = URL(string:
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Cheap pgrep poll — safe to call every refresh tick.
    func refreshEngineState() {
        guard !checking && !applying else { return }
        checking = true
        DispatchQueue.global(qos: .utility).async {
            let running = Self.isEngineRunning()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.checking = false
                    if !self.applying { self.engineRunning = running }
                }
            }
        }
    }

    // MARK: engine plumbing (blocking — background queue only)

    /// Contract: engine alive ⇔ pgrep -f "screenpipe.*record" finds something.
    /// The [r] character class keeps the pattern from matching ITSELF in a
    /// concurrent pgrep/pkill's argv — two pgreps racing at app launch used to
    /// see each other and report "engine running", silently skipping autostart.
    nonisolated static let enginePattern = "screenpipe.*[r]ecord"

    nonisolated static func isEngineRunning() -> Bool {
        Shell.run("/usr/bin/pgrep", ["-f", enginePattern]).0 == 0
    }

    /// Contract stop recipe: pkill -f '<engine>' ; sleep 1
    nonisolated static func stopEngineBlocking() {
        _ = Shell.run("/usr/bin/pkill", ["-f", enginePattern])
        Thread.sleep(forTimeInterval: 1.0)
    }

    /// The engine runs as a direct child of the app (kept referenced here) —
    /// see ScreenpipeRecipe.startCommand for why nohup-orphaning fails.
    nonisolated(unsafe) static var engineProcess: Process?

    nonisolated static func startEngineBlocking(mode: String) {
        guard let cmd = ScreenpipeRecipe.startCommand(mode: mode) else { return }
        _ = Shell.ok("echo \"[app $(date '+%F %T')] spawn mode=\(mode)\" >> \"$HOME/.screenpipe/engine.log\"")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", cmd]
        do { try p.run() } catch {
            _ = Shell.ok("echo \"[app] spawn failed: \(error.localizedDescription)\" >> \"$HOME/.screenpipe/engine.log\"")
            return
        }
        engineProcess = p  // hold the reference; engine lives with the app
    }
}

// MARK: - Global hotkey (item 2) — Carbon RegisterEventHotKey
//
// No TCC permission needed; works for an LSUIElement app. Preference lives in
// UserDefaults ("hotkeyEnabled"/"hotkeyPreset") — a pure local UI pref, kept
// OUT of settings_overrides.json (that file is a pipeline contract).
// Default ⌥Space: ⌃⌥Space is the system "select next input source" default on
// multi-IME setups (Zelin types Chinese) and would silently shadow us.

@MainActor
final class HotKeyCenter: ObservableObject {
    static let shared = HotKeyCenter()

    struct Preset {
        let id: String
        let keyCode: UInt32
        let carbonMods: UInt32
        let label: String   // pure symbols — no L() needed
    }

    static let presets: [Preset] = [
        Preset(id: "opt-space", keyCode: UInt32(kVK_Space),
               carbonMods: UInt32(optionKey), label: "⌥ Space"),
        Preset(id: "ctrl-opt-space", keyCode: UInt32(kVK_Space),
               carbonMods: UInt32(controlKey | optionKey), label: "⌃⌥ Space"),
        Preset(id: "ctrl-shift-space", keyCode: UInt32(kVK_Space),
               carbonMods: UInt32(controlKey | shiftKey), label: "⌃⇧ Space"),
        Preset(id: "cmd-shift-space", keyCode: UInt32(kVK_Space),
               carbonMods: UInt32(cmdKey | shiftKey), label: "⌘⇧ Space"),
    ]

    static func preset(for id: String?) -> Preset {
        presets.first { $0.id == id } ?? presets[0]
    }

    /// False when registration failed (e.g. another app owns the combo) —
    /// the settings page shows a yellow hint to pick a different preset.
    @Published private(set) var registered = false

    private var hotKeyRef: EventHotKeyRef?
    private var handlerInstalled = false

    /// (Re-)register per current prefs. Failure is silent here (no dialogs);
    /// state surfaces in 设置 → 快捷键.
    func apply() {
        if let ref = hotKeyRef {
            UnregisterEventHotKey(ref)
            hotKeyRef = nil
        }
        registered = false
        guard Prefs.bool("hotkeyEnabled", default: true) else { return }
        installHandlerIfNeeded()
        let p = Self.preset(for: UserDefaults.standard.string(forKey: "hotkeyPreset"))
        // four-char code 'ZAI1' = 0x5A414931
        let hotKeyID = EventHotKeyID(signature: OSType(0x5A41_4931), id: 1)
        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(p.keyCode, p.carbonMods, hotKeyID,
                                         GetApplicationEventTarget(), 0, &ref)
        if status == noErr, let ref {
            hotKeyRef = ref
            registered = true
        }
    }

    private func installHandlerIfNeeded() {
        guard !handlerInstalled else { return }
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                      eventKind: UInt32(kEventHotKeyPressed))
        // C callback — no captures; hop to the main actor per house style.
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ -> OSStatus in
            Analytics.log("hotkey_activated")  // static call — no capture
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    (NSApp.delegate as? AppDelegate)?.hotKeyActivated()
                }
            }
            return noErr
        }, 1, &eventType, nil, nil)
        handlerInstalled = true
    }
}
