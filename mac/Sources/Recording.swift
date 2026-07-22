// Recording.swift — ScreenpipeRecipe / RecordingController（录制引擎）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation
import UserNotifications // self-heal / TCC-loss one-shot notices

// MARK: - Screenpipe launch recipe (tuning verbatim from the Automator applet —
// SHARED contract; + sensitive-app exclusion, P1-9)

enum ScreenpipeRecipe {
    static let tuning =
        " --use-all-monitors --disable-meeting-detector --disable-clipboard-capture"
        + " --prioritize-input-latency --pause-extraction-on-input-ms 1500"
        + " --capture-scroll true --visual-check-interval-ms 0"
        + " --idle-capture-interval-ms 86400000 --min-capture-interval-ms 1000"

    /// Sensitive-app capture exclusion (P1-9). The engine's --ignored-windows
    /// (screenpipe 0.3.349, source-verified) does case-insensitive SUBSTRING
    /// matching against both the app name and the window title, and skips
    /// matching windows before any frame/OCR is stored. Entries pass through
    /// verbatim, so the engine's `App::Title` scoping syntax also works.
    /// Configurable via config.yaml `recording.ignored_apps` (absent → these
    /// defaults; explicit [] = off). Keep in sync with DEFAULT_IGNORED_APPS
    /// in act/lib/config.py — the export script filters already-stored frames
    /// with the same list (drift-guarded by tests/test_capture_exclusion.py).
    static let defaultIgnoredApps = [
        "1Password", "Bitwarden", "LastPass", "KeePassXC", "Keychain Access",
        "Private Browsing", "Incognito",
    ]

    /// ` --ignored-windows 'X' --ignored-windows 'Y' …`, single-quoted for zsh.
    static func exclusionArgs() -> String {
        let apps = SettingsIO.configList("ignored_apps") ?? defaultIgnoredApps
        return apps.map {
            " --ignored-windows '" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'"
        }.joined()
    }

    /// Full zsh command that starts the engine detached (nohup … & → survives
    /// the app). PATH needs /opt/homebrew/bin (npx lives there). nil for "off".
    static func startCommand(mode: String) -> String? {
        let record: String
        var prep = ""
        switch mode {
        case "screen":
            record = "npx screenpipe@0.3.349 record --disable-audio\(tuning)\(exclusionArgs())"
                + " -l chinese -l english --retention-days 1"
        case "screen_audio":
            // flip disableAudio in store.bin first (recipe verbatim)
            prep = "[ -f \"$HOME/.screenpipe/store.bin\" ] && /usr/bin/sed -i.bak"
                + " 's/\"disableAudio\": true/\"disableAudio\": false/'"
                + " \"$HOME/.screenpipe/store.bin\"; "
            record = "npx screenpipe@0.3.349 record -a parakeet\(tuning)\(exclusionArgs())"
                + " -l chinese -l english --retention-days 1"
        default:
            return nil
        }
        // exec (not `nohup … &`): a GUI app's orphaned background jobs get
        // reaped by macOS (RunningBoard) before npx even writes a byte — the
        // engine must stay a direct child of the app, referenced by a Process.
        // PATH must cover BOTH where npx lives (/opt/homebrew/bin) AND where
        // ffmpeg may live — screenpipe shells out to ffmpeg to encode frames
        // and, if it can't find one, tries (and often fails) to auto-download
        // it, leaving recording silently dead. The login shell only sources
        // .zprofile (never .zshrc, 例4b), so an ffmpeg outside
        // /opt/homebrew/bin (e.g. ~/.local/bin, Intel-brew /usr/local/bin,
        // MacPorts /opt/local/bin) was invisible. Cover the common install
        // dirs so a present ffmpeg is always found (from PR #42).
        return "export PATH=\"/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/opt/local/bin:$PATH\"; "
            + "mkdir -p \"$HOME/.screenpipe\"; "
            + prep
            + "exec \(record) >> \"$HOME/.screenpipe/engine.log\" 2>&1"
    }
}

// MARK: - engine-death diagnosis (audit 2.3 — mirror of
// act/lib/failures.classify_engine_log; keep the two in sync)

/// Why the engine is down (or busy downloading). failureId ∈ the §25 catalog:
/// node_missing | engine_npm_download | engine_crashed | engine_dead |
/// engine_ffmpeg_missing. logTail is only populated for engine_crashed /
/// engine_ffmpeg_missing (the last real log lines — surfacing them verbatim
/// is the whole point).
struct EngineDiagnosis: Equatable {
    let failureId: String
    let logTail: String
}

// MARK: - Recording controller (Screenpipe engine)
//
// mode ∈ "off" | "screen" | "screen_audio", persisted in UserDefaults
// "recordingMode" (fresh install: "off" until the one-time consent prompt —
// P0-11; RecordingConsent in Onboarding.swift). The engine outlives the app
// (nohup …&); liveness = `pgrep -f "screenpipe.*record"` has results (contract).

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
    /// Classified reason the engine is down / still downloading (audit 2.3);
    /// nil = healthy or mode == off. Refreshed with engineRunning.
    @Published private(set) var diagnosis: EngineDiagnosis?
    /// Audit 9.2: Screen Recording was granted once and macOS revoked it
    /// (OS update / re-sign / user action) — drives the calm re-grant banner.
    @Published private(set) var tccLost = false
    /// Transient success line after a consent-race self-heal ("权限已生效…").
    @Published private(set) var selfHealNote = ""
    /// Transient warning line for a refused / rolled-back mode switch — the
    /// DURABLE in-app explanation (15 s): the injected diagnosis would be
    /// wiped by the next 5 s refresh tick and postSystemNotice is dropped
    /// silently when notifications are not granted.
    @Published private(set) var recordingNote = ""

    private var applying = false
    private var checking = false
    /// Last CGPreflight value seen THIS session (nil until the first poll) —
    /// the false→true flip is what triggers the self-heal restart.
    private var lastGrantSeen: Bool?
    private var selfHealToken = 0
    private var noteToken = 0
    /// Bumped by EVERY setMode call — the async ffmpeg precheck's commit is
    /// dropped unless the generation still matches. Comparing mode VALUES
    /// (mode == prev) was not enough: 屏幕+音频 → 关 → 仅屏幕 while the probe
    /// ran ends on the same value the probe captured, and the stale commit
    /// re-enabled the microphone against the user's newest choice.
    private var modeGen = 0

    private init() {
        let stored = UserDefaults.standard.string(forKey: "recordingMode") ?? ""
        // P0-11: no stored key = fresh install → OFF; recording must not start
        // before consent. Existing installs keep whatever they chose.
        mode = ["off", "screen", "screen_audio"].contains(stored) ? stored : "off"
        // app-focus is a natural "just came back from System Settings" moment
        NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification,
            object: nil, queue: .main) { _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    RecordingController.shared.pollScreenPermission()
                }
            }
        }
    }

    nonisolated static func label(forMode m: String) -> String {
        switch m {
        case "off": return L("关", "Off")
        case "screen_audio": return L("屏幕 + 音频", "Screen + Audio")
        default: return L("仅屏幕", "Screen Only")
        }
    }

    var modeLabel: String { Self.label(forMode: mode) }

    /// Show `text` in recordingNote for 15 s (token: an older timer must not
    /// clear a newer note — same pattern as selfHealNote).
    private func flashNote(_ text: String) {
        recordingNote = text
        noteToken += 1
        let token = noteToken
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) {
            MainActor.assumeIsolated {
                if self.noteToken == token { self.recordingNote = "" }
            }
        }
    }

    /// The mode the re-enable button should restore: the last non-off mode
    /// the user actually ran (default "screen" for fresh installs). The
    /// screen_audio ffmpeg precheck in setMode still guards the restore.
    var resumeMode: String {
        let last = UserDefaults.standard.string(forKey: "lastActiveRecordingMode") ?? ""
        return ["screen", "screen_audio"].contains(last) ? last : "screen"
    }

    func setMode(_ newMode: String) {
        guard ["off", "screen", "screen_audio"].contains(newMode) else { return }
        modeGen += 1
        // screen_audio hard-requires ffmpeg at engine startup — precheck
        // BEFORE committing, so a doomed switch never pkills a healthy
        // engine (2026-07-13: the stop→start path killed a live screen
        // engine, every screen_audio spawn then died on ffmpeg, and
        // recording silently stopped). Probe is blocking → off-main.
        if newMode == "screen_audio" {
            let prev = mode
            let gen = modeGen
            DispatchQueue.global(qos: .userInitiated).async {
                let ok = Self.ffmpegPresent()
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        // stale click: the user picked another mode while the
                        // probe ran — their newer choice wins, this one drops
                        // (a late commit here once re-enabled the microphone
                        // AFTER an explicit 关; see modeGen for why the guard
                        // is a generation, not a mode-value compare).
                        guard self.modeGen == gen else { return }
                        if ok {
                            self.commitMode(newMode, rollbackTo: prev)
                        } else {
                            Analytics.log("recording_ffmpeg_blocked",
                                          fields: ["kept_mode": prev])
                            let note = FailureCatalog.message("engine_ffmpeg_missing") ?? ""
                            self.flashNote(note)
                            Self.postSystemNotice(
                                title: L("还开不了「屏幕+音频」", "Screen + Audio is not ready"),
                                body: note)
                        }
                    }
                }
            }
            return
        }
        commitMode(newMode, rollbackTo: mode)
    }

    /// The actual mode write + engine cycle (post-precheck). `previous` feeds
    /// applyMode's rollback so a switch that cannot start falls back instead
    /// of leaving recording dead.
    private func commitMode(_ newMode: String, rollbackTo previous: String) {
        if newMode == "off", mode != "off" {
            // remember what was on: the re-enable button restores THIS, so
            // an off→on round-trip no longer silently drops the audio tier
            // (2026-07-21: a manual restart landed on "screen" and audio
            // capture stayed dead for a day without anyone choosing that).
            UserDefaults.standard.set(mode, forKey: "lastActiveRecordingMode")
        }
        UserDefaults.standard.set(newMode, forKey: "recordingMode")
        let changed = newMode != mode
        mode = newMode
        // switching mode = stop → start; re-picking a live mode with a dead
        // engine also (re)starts it.
        if changed || (newMode != "off" && !engineRunning) {
            applyMode(rollbackTo: changed ? previous : nil)
        }
        Analytics.log("recording_set_mode", fields: ["mode": newMode])
        // v0.19.0 funnel (C's milestone, folded into Swift): turning recording
        // on is configuring the screenpipe ingest source. firstReach dedups.
        if newMode != "off" { Analytics.firstReach("ingest_configured") }
    }

    /// Contract D: restart the current mode's engine — same stop→start path
    /// as re-picking a live mode. mode == "off" has no engine → no-op.
    func restartEngine() {
        Analytics.log("recording_restart", fields: ["mode": mode])
        guard mode != "off" else { return }
        applyMode()
    }

    /// stop → (start per mode). Blocking work runs off-main; engineRunning is
    /// refreshed when done. `rollbackTo` (mode-switch path only): when the NEW
    /// mode fails to come up, restart the previous live mode instead of
    /// leaving recording silently dead — one attempt, no recursion
    /// (2026-07-13: a switch to screen_audio killed a healthy screen engine
    /// and its replacement died on missing ffmpeg; capture just stopped).
    func applyMode(rollbackTo previous: String? = nil) {
        guard !applying else { return }
        applying = true
        let m = mode
        DispatchQueue.global(qos: .userInitiated).async {
            Self.stopEngineBlocking()
            if m != "off" { Self.startEngineBlocking(mode: m) }
            Thread.sleep(forTimeInterval: 0.5)  // let the process surface in pgrep
            var running = Self.isEngineRunning()
            var diag = m == "off" ? nil : Self.diagnoseEngine(running: running)
            // Mode-switch slow-death watch: a doomed engine can outlive the
            // +0.5 s check by seconds (2026-07-13: the screen_audio spawn
            // spent ~4 s downloading ffmpeg before dying, and pgrep matches
            // the zsh/npx wrapper argv from t=0) — publish optimistically,
            // keep `applying` held, and re-verify before declaring the
            // switch good.
            if running, m != "off", previous != nil {
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        self.engineRunning = true
                        self.diagnosis = diag
                    }
                }
                Thread.sleep(forTimeInterval: 7.5)
                running = Self.isEngineRunning()
                diag = Self.diagnoseEngine(running: running)
            }
            var rolledBack = false
            if !running, m != "off", let prev = previous,
               prev != m, prev != "off" {
                _ = Shell.ok("echo \"[app $(date '+%F %T')] rollback mode=\(prev) after failed \(m)\" >> \"$HOME/.screenpipe/engine.log\"")
                Self.startEngineBlocking(mode: prev)
                Thread.sleep(forTimeInterval: 0.5)
                running = Self.isEngineRunning()
                rolledBack = running
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.applying = false
                    // did the user pick something else while this cycle ran?
                    // (their commitMode's applyMode was dropped by the
                    // `applying` guard — honor it below, and never clobber
                    // it with the rollback write.)
                    let userChanged = self.mode != m
                    if rolledBack, let prev = previous, !userChanged {
                        // the mode the user asked for never started — be
                        // honest in the persisted state and say why (note +
                        // notice; `diagnosis` alone would be wiped by the
                        // next 5 s refresh once the rolled-back engine
                        // reports healthy).
                        UserDefaults.standard.set(prev, forKey: "recordingMode")
                        self.mode = prev
                        Analytics.log("recording_mode_rollback", fields: [
                            "from": m, "to": prev,
                            "reason": diag?.failureId ?? "unknown"])
                        let note = Self.rollbackNote(failed: m, kept: prev,
                                                     diag: diag)
                        self.flashNote(note)
                        Self.postSystemNotice(
                            title: L("已退回原来的录制模式",
                                     "Reverted to the previous recording mode"),
                            body: note)
                    }
                    self.engineRunning = running
                    self.diagnosis = diag
                    if userChanged { self.applyMode() }
                }
            }
        }
    }

    /// Self-contained rollback explanation (notification + recordingNote).
    /// Deliberately NOT the FailureCatalog sentence: those are written for
    /// the inline doctor row ("看下面的引擎日志") and would contradict a
    /// successful rollback ("录制引擎没有在运行" while the old engine records).
    nonisolated static func rollbackNote(failed: String, kept: String,
                                         diag: EngineDiagnosis?) -> String {
        let cause: String
        switch diag?.failureId {
        case "engine_ffmpeg_missing":
            cause = L("缺 ffmpeg（brew install ffmpeg 装好后再切一次）",
                      "ffmpeg is missing (brew install ffmpeg, then switch again)")
        case "node_missing":
            cause = L("缺 Node.js", "Node.js is missing")
        default:
            cause = L("引擎没能启动", "its engine failed to start")
        }
        return L("「\(label(forMode: failed))」没能开启——\(cause)；已退回「\(label(forMode: kept))」继续录制",
                 "\(label(forMode: failed)) could not start — \(cause); reverted to \(label(forMode: kept)) and recording continues")
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
            let diag = m == "off" ? nil : Self.diagnoseEngine(running: running)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.engineRunning = running
                    self.diagnosis = diag
                }
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

    // MARK: consent-race self-heal + TCC-loss detection (audit 2.2 / 9.2)
    //
    // Called from the 2 s permissions-window poll (PermissionsModel.refresh),
    // the 5 s AppDelegate tick, and app-focus. CGPreflight is a cheap TCC read
    // — safe at this frequency.

    /// UserDefaults key: "Screen Recording was granted at least once". The
    /// PERSISTED flag detects revocation across launches (macOS update /
    /// re-sign); the in-session `lastGrantSeen` flip drives the auto-restart.
    private static let wasGrantedKey = "screenTCCWasGranted"

    func pollScreenPermission() {
        let granted = Self.hasScreenPermission()
        let flipped = lastGrantSeen == false && granted
        lastGrantSeen = granted

        if granted {
            if !Prefs.bool(Self.wasGrantedKey, default: false) {
                UserDefaults.standard.set(true, forKey: Self.wasGrantedKey)
            }
            tccLost = false
        } else if Prefs.bool(Self.wasGrantedKey, default: false) && !tccLost {
            // was granted before, gone now — the silent post-update killer
            tccLost = true
            Analytics.log("screen_tcc_lost", fields: ["mode": mode])
            if mode != "off" {
                Self.postSystemNotice(
                    title: L("屏幕录制授权失效了", "Screen Recording permission lost"),
                    body: FailureCatalog.message("screen_tcc_lost") ?? "")
            }
        }

        // the consent race: user granted while a recording mode is on — the
        // engine either exited instantly at start or records black frames.
        // Either way a restart with the fresh grant fixes it; do it for them.
        if flipped && mode != "off" {
            Analytics.log("recording_self_heal", fields: ["trigger": "tcc_granted"])
            let note = L("屏幕权限已生效，录制引擎已自动重启",
                         "Screen Recording is now granted — the engine restarted automatically")
            selfHealNote = note
            Self.postSystemNotice(
                title: L("录制已就绪", "Recording is live"), body: note)
            applyMode()  // stop → start under the fresh grant
            selfHealToken += 1
            let token = selfHealToken
            DispatchQueue.main.asyncAfter(deadline: .now() + 15) {
                MainActor.assumeIsolated {
                    if self.selfHealToken == token { self.selfHealNote = "" }
                }
            }
        }
    }

    /// Best-effort system notification (silently dropped when notifications
    /// are not granted — the same text also shows inline in the recording UI).
    nonisolated static func postSystemNotice(title: String, body: String) {
        // UNUserNotificationCenter traps outside a real .app bundle (bare dev
        // binary) — same guard as PermissionsModel.refreshNotifications.
        guard Bundle.main.bundleIdentifier != nil else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(
            identifier: UUID().uuidString, content: content, trigger: nil))
    }

    /// Cheap pgrep poll (+ death diagnosis when a mode is on) — safe to call
    /// every refresh tick.
    func refreshEngineState() {
        guard !checking && !applying else { return }
        checking = true
        let m = mode
        DispatchQueue.global(qos: .utility).async {
            let running = Self.isEngineRunning()
            let diag = m == "off" ? nil : Self.diagnoseEngine(running: running)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.checking = false
                    if !self.applying {
                        self.engineRunning = running
                        self.diagnosis = diag
                    }
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

    // MARK: engine-death diagnosis (audit 2.3) — Swift mirror of
    // act/lib/failures.classify_engine_log; keep the two in sync.

    nonisolated static var engineLogPath: String {
        NSHomeDirectory() + "/.screenpipe/engine.log"
    }

    /// Last real engine lines (our own "[app …]" breadcrumbs filtered out) —
    /// reads at most the final 16 KB; cheap enough for the 5 s tick.
    nonisolated static func engineLogTail(maxLines: Int = 12) -> String {
        guard let handle = FileHandle(forReadingAtPath: engineLogPath) else { return "" }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        let want: UInt64 = 16_384
        let offset = size > want ? size - want : 0
        try? handle.seek(toOffset: offset)
        guard let data = try? handle.readToEnd(),
              let text = String(data: data, encoding: .utf8) else { return "" }
        let lines = text.split(separator: "\n", omittingEmptySubsequences: true)
            .filter { !$0.hasPrefix("[app") }
        return lines.suffix(maxLines).joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// npx presence, cached for 60 s (a login-shell `command -v` per probe is
    /// too heavy for every tick). Only touched from the serialized refresh /
    /// apply background paths.
    nonisolated(unsafe) private static var npxCache: (ok: Bool, at: Date)?

    nonisolated static func npxPresent() -> Bool {
        if let c = npxCache, Date().timeIntervalSince(c.at) < 60 { return c.ok }
        let ok = Shell.ok("command -v npx >/dev/null || test -x /opt/homebrew/bin/npx")
        npxCache = (ok, Date())
        return ok
    }

    /// ffmpeg presence — the screen_audio hard dependency (the engine exits
    /// seconds after spawn without it, and its built-in auto-installer is
    /// unreliable — 2026-07-13). EXECUTES `-version` rather than testing for
    /// a file: the installer's own artifact proves nothing (that day it
    /// wrote ~/.local/bin/ffmpeg and still reported "os error 2").
    /// ~/.local/bin is probed explicitly because the engine's install
    /// location may be missing from a NON-interactive login-shell PATH
    /// (例4b). Deliberately uncached: mode switches are rare, concurrent
    /// probes would race a shared cache, and a stale "missing" would refuse
    /// a user who JUST installed it. Blocking — background queue only.
    nonisolated static func ffmpegPresent() -> Bool {
        // arms mirror ScreenpipeRecipe.startCommand's engine PATH — the
        // precheck must never refuse a switch the engine could serve
        Shell.ok("ffmpeg -version >/dev/null 2>&1"
            + " || \"$HOME/.local/bin/ffmpeg\" -version >/dev/null 2>&1"
            + " || /opt/homebrew/bin/ffmpeg -version >/dev/null 2>&1"
            + " || /usr/local/bin/ffmpeg -version >/dev/null 2>&1"
            + " || /opt/local/bin/ffmpeg -version >/dev/null 2>&1")
    }

    /// The capture db got a write in the last 5 min — recording is really on.
    nonisolated static func dbFresh() -> Bool {
        let db = NSHomeDirectory() + "/.screenpipe/db.sqlite"
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: db),
              let mtime = attrs[.modificationDate] as? Date else { return false }
        return Date().timeIntervalSince(mtime) < 300
    }

    /// Mirror of failures.classify_engine_log(tail, npx_present, engine_alive).
    /// nil = healthy (alive with nothing suspicious in the log — silence alone
    /// is never a failure; a locked screen legitimately goes quiet). Blocking
    /// (login-shell probe on cache miss) — background queue only.
    nonisolated static func diagnoseEngine(running: Bool) -> EngineDiagnosis? {
        if !npxPresent() { return EngineDiagnosis(failureId: "node_missing", logTail: "") }
        let tail = engineLogTail()
        let lower = tail.lowercased()
        if lower.contains("command not found: npx") || lower.contains("command not found: node")
            || lower.contains("env: node: no such file") {
            return EngineDiagnosis(failureId: "node_missing", logTail: "")
        }
        let downloading = lower.contains("package was not found and will be installed")
            || lower.contains("need to install the following package")
        // network trouble in the tail means a download DIED, not "in progress"
        // (python rule-order contract: network_error outranks the npm banner)
        let networky = ["etimedout", "econnre", "enotfound", "network is unreachable",
                        "connection refused", "connection reset",
                        "connection timed out"].contains { lower.contains($0) }
        if running {
            // a quiet-but-recording engine can keep the old npm banner in its
            // last lines forever — a fresh db write proves recording is live,
            // so never show "downloading" over it
            if downloading && !networky && !Self.dbFresh() {
                return EngineDiagnosis(failureId: "engine_npm_download", logTail: "")
            }
            return nil
        }
        // dead on screenpipe's ffmpeg install failure -> the specific fix
        // beats the generic "crashed" (an ALIVE engine with stale ffmpeg
        // lines in its tail already returned healthy above). The colon keeps
        // the signature screenpipe-exclusive ("failed to install
        // ffmpeg-python" and prose must not match) — python mirror:
        // failures._FFMPEG_INSTALL_FAILED.
        if lower.contains("failed to install ffmpeg:")
            || lower.contains("ffmpeg not found and installation failed") {
            return EngineDiagnosis(failureId: "engine_ffmpeg_missing", logTail: tail)
        }
        return tail.isEmpty
            ? EngineDiagnosis(failureId: "engine_dead", logTail: "")
            : EngineDiagnosis(failureId: "engine_crashed", logTail: tail)
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

// v0.15 (owner decision): the Carbon global hotkey (HotKeyCenter, ⌥Space) is
// gone — with its settings UI removed there was no way to see registration
// failures or turn it off, and an invisible always-on global shortcut is
// worse than none. Quick capture stays: menu-bar icon click, ⌘L (View menu),
// the kanban composer, and text dropped onto the icon.
