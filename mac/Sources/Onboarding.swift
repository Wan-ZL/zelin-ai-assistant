// Onboarding.swift — first-launch recording consent (P0-11; presentation v0.13)
//
// A fresh install must NOT capture anything before the user has seen, in
// plain words, what is recorded, where it goes and how long it is kept.
// The consent shows once (Prefs "recordingConsentShown"); existing installs
// that already carry a recordingMode value are never asked.
//
// v0.13: the question is presented inside the first-run permissions window
// (Permissions.swift) as a SINGLE choice — 开启 → screen-ONLY recording
// ("screen"); the old screen vs screen+audio picker is gone from onboarding.
// Audio remains available, but only as an explicit opt-in wherever the
// recording mode is switchable (设置 → 录制, the popover recording menu, the
// 录制与 ingest page, /rec audio). Keys and semantics are unchanged.

import AppKit
import Foundation
import ServiceManagement  // SMAppService (first-run launch-at-login default)

@MainActor
enum RecordingConsent {
    /// True on a fresh install only: no recording mode ever chosen AND no
    /// consent recorded. Either key present ⇒ never prompt again.
    /// (nonisolated: UserDefaults is thread-safe; SwiftUI state initializers
    /// read this outside the main actor.)
    nonisolated static var needsPrompt: Bool {
        UserDefaults.standard.string(forKey: "recordingMode") == nil
            && !Prefs.bool("recordingConsentShown", default: false)
    }

    /// Fresh install: open the first-run permissions & setup window. The
    /// window records the choice via record(granted:); closing it without
    /// choosing counts as 暂不 (PermissionsWindowController.windowWillClose).
    static func present() {
        PermissionsWindowController.shared.show(firstRun: true)
    }

    /// Persist the one-time consent. granted → screen-ONLY recording starts
    /// (mode "screen", never audio here); either way both keys are set and
    /// the prompt never returns. Recording stays changeable in 设置 → 录制.
    static func record(granted: Bool) {
        let mode = granted ? "screen" : "off"
        UserDefaults.standard.set(true, forKey: "recordingConsentShown")
        Analytics.log("recording_consent", fields: ["choice": mode])
        registerLaunchAtLoginDefault()
        if granted, !RecordingController.hasScreenPermission() {
            RecordingController.requestScreenPermission()
        }
        // persists recordingMode and (when granted) starts the engine via the
        // normal stop→start path
        RecordingController.shared.setMode(mode)
    }

    /// First run only: default launch-at-login ON. A menu-bar assistant that
    /// dies at the first reboot is an inert product for pkg users (audit 1.2)
    /// — the tired expert never finds the Settings toggle. One-shot marker so
    /// a user who later turns the toggle OFF is never re-registered; dev
    /// builds are skipped (the login item would pin the temporary build
    /// path); failure is silent — the Settings toggle reads the real
    /// SMAppService status either way, and this stays user-visible and
    /// changeable in System Settings → General → Login Items.
    private static func registerLaunchAtLoginDefault() {
        let appliedKey = "launchAtLoginDefaultApplied"
        guard !UserDefaults.standard.bool(forKey: appliedKey) else { return }
        UserDefaults.standard.set(true, forKey: appliedKey)
        let path = Bundle.main.bundlePath
        let installed = path.hasPrefix("/Applications/")
            || path.hasPrefix(NSHomeDirectory() + "/Applications/")
        guard installed, SMAppService.mainApp.status != .enabled else { return }
        do {
            try SMAppService.mainApp.register()
            Analytics.log("launch_at_login_default", fields: ["ok": true])
        } catch {
            Analytics.log("launch_at_login_default", fields: ["ok": false])
        }
    }

    static func openPrivacyDoc() {
        let local = AppPaths.stateRoot + "/docs/PRIVACY.md"
        if FileManager.default.fileExists(atPath: local) {
            NSWorkspace.shared.open(URL(fileURLWithPath: local))
        } else if let url = URL(
            string: "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/PRIVACY.md") {
            NSWorkspace.shared.open(url)
        }
    }
}
