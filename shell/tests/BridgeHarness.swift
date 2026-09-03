// BridgeHarness.swift — behavior tests for shell/Sources/ShellBridge.swift
// (the `zaiShell` wire contract, CONTRACT §61.1) + LegacyPrefs (§61.4).
// Compiled by run.sh together with every shell source except main.swift into a
// plain macOS CLI tool — no Xcode, no XCTest, no WKWebView. Exits non-zero on
// any failure. Same harness style as ios/tests/captions.
//
// Boundaries the harness deliberately does NOT cross: no `setRecording` /
// `setCaptions` with VALID args (those spawn pgrep/pkill, write UserDefaults
// and analytics — run.sh sandboxes AIASSISTANT_HOME, but the engines are the
// mac app's frozen logic and are covered by their own drift guards); no
// UserDefaults.standard writes (LegacyPrefs gets injected suites).

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

@MainActor
func run() {
    let bridge = ShellBridge()

    // ---- 1. snapshot shape: every wire key present (add-only vocabulary) ----
    print("[1] stateSnapshot wire keys:")
    let snap = ShellBridge.stateSnapshot()
    let rec = snap["recording"] as? [String: Any] ?? [:]
    let cap = snap["captions"] as? [String: Any] ?? [:]
    for key in ["available", "on", "mode", "engine_running", "diagnosis", "note",
                "tcc_lost", "screen_permission", "resume_mode"] {
        check(rec[key] != nil, "recording.\(key) present")
    }
    for key in ["available", "on", "engine", "paused", "engine_dead",
                "status_text", "status_is_error"] {
        check(cap[key] != nil, "captions.\(key) present")
    }
    check(["zh", "en"].contains(snap["language"] as? String ?? ""),
          "language ∈ zh|en", "got \(String(describing: snap["language"]))")
    check(rec["available"] as? Bool == true && cap["available"] as? Bool == true,
          "both engines available in this shell build")
    // `on` is derived from mode, never stored separately
    let mode = rec["mode"] as? String ?? "?"
    check(["off", "screen", "screen_audio"].contains(mode), "mode vocabulary frozen (§15)",
          "got \(mode)")
    check((rec["on"] as? Bool) == (mode != "off"), "recording.on ⇔ mode != off")
    check(rec["diagnosis"] is NSNull || rec["diagnosis"] is String,
          "diagnosis is null or failure id")
    // §68.2 / §68.13 add-only keys: caption prefs, permissions block, launch_at_login, hotkey
    for key in ["source", "translate", "translate_direction", "apple_locale", "ark_model",
                "font_size", "opacity"] {
        check(cap[key] != nil, "captions.\(key) present (caption prefs)")
    }
    let perm = snap["permissions"] as? [String: Any] ?? [:]
    for key in ["screen", "microphone", "notifications", "vault"] {
        let value = perm[key] as? String ?? "?"
        check(["granted", "denied", "unknown"].contains(value),
              "permissions.\(key) ∈ granted|denied|unknown", "got \(value)")
    }
    check(snap["launch_at_login"] is Bool, "launch_at_login is bool")
    check((snap["hotkey"] as? String)?.isEmpty == false, "hotkey label present")

    // ---- 2. JSON is a valid JS expression for the event push ----
    print("[2] stateJSON round-trips:")
    if let json = ShellBridge.stateJSON(),
       let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
        check(obj["recording"] != nil && obj["captions"] != nil, "parses back with both blocks")
    } else {
        check(false, "stateJSON produced parseable JSON")
    }

    // ---- 3. request vocabulary: getState / rejections ----
    print("[3] request dispatch:")
    if let state = try? bridge.handle(["method": "getState"]) {
        check(state["recording"] != nil, "getState returns the snapshot")
    } else {
        check(false, "getState must not throw")
    }
    func rejection(_ body: Any?) -> String {
        do { _ = try bridge.handle(body); return "" }
        catch let e as BridgeError { return e.code }
        catch { return "OTHER" }
    }
    check(rejection(["method": "nope"]).hasPrefix("UNKNOWN_METHOD"), "unknown method rejected",
          rejection(["method": "nope"]))
    check(rejection("just a string").hasPrefix("INVALID_ARGS"), "non-dict body rejected")
    check(rejection(["on": true]).hasPrefix("INVALID_ARGS"), "missing method rejected")
    check(rejection(["method": "setRecording"]).hasPrefix("INVALID_ARGS"),
          "setRecording without on: bool rejected")
    check(rejection(["method": "setRecording", "on": "yes"]).hasPrefix("INVALID_ARGS"),
          "setRecording with string on rejected (type-strict)")
    check(rejection(["method": "setRecording", "on": true, "mode": "off"]).hasPrefix("INVALID_ARGS"),
          "setRecording on:true mode:off rejected (off is on:false)")
    check(rejection(["method": "setRecording", "on": true, "mode": "video"]).hasPrefix("INVALID_ARGS"),
          "setRecording with unknown mode rejected")
    check(rejection(["method": "setCaptions"]).hasPrefix("INVALID_ARGS"),
          "setCaptions without on rejected")
    check(rejection(["method": "setLanguage", "lang": "fr"]).hasPrefix("INVALID_ARGS"),
          "setLanguage outside zh|en rejected")
    // §68.13 new methods: type-strict rejections (valid calls touch TCC / SMAppService /
    // Dock / UserDefaults — out of the harness's bounds, see header)
    check(rejection(["method": "requestPermission", "kind": "camera"]).hasPrefix("INVALID_ARGS"),
          "requestPermission outside screen|microphone|notifications rejected")
    check(rejection(["method": "requestPermission"]).hasPrefix("INVALID_ARGS"),
          "requestPermission without kind rejected")
    check(rejection(["method": "openPane", "pane": "bluetooth"]).hasPrefix("INVALID_ARGS"),
          "openPane outside the pane vocabulary rejected")
    check(rejection(["method": "setLaunchAtLogin", "on": "yes"]).hasPrefix("INVALID_ARGS"),
          "setLaunchAtLogin with string on rejected")
    check(rejection(["method": "setBadge", "count": -1]).hasPrefix("INVALID_ARGS"),
          "setBadge negative rejected")
    check(rejection(["method": "setBadge", "count": "3"]).hasPrefix("INVALID_ARGS"),
          "setBadge string rejected (type-strict)")
    check(rejection(["method": "setCaptionPrefs"]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs with no prefs rejected")
    check(rejection(["method": "setCaptionPrefs", "engine": "whisper"]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs engine outside auto|doubao|apple rejected")
    check(rejection(["method": "setCaptionPrefs", "font_size": 99]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs font_size outside 14...40 rejected")
    check(rejection(["method": "setCaptionPrefs", "opacity": 0.05]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs opacity outside 0.2...1 rejected")
    check(rejection(["method": "setCaptionPrefs", "translate": "on"]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs translate must be bool")
    check(rejection(["method": "setCaptionPrefs", "ark_model": ""]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs empty ark_model rejected")
    // a bad key alongside a good one rejects the WHOLE request (zero writes)
    check(rejection(["method": "setCaptionPrefs", "source": "mic", "engine": "bogus"]).hasPrefix("INVALID_ARGS"),
          "setCaptionPrefs is all-or-nothing")
    if let state = try? bridge.handle(["method": "getPermissions"]) {
        check((state["permissions"] as? [String: Any])?["screen"] != nil,
              "getPermissions refreshes and returns the permissions block")
    } else {
        check(false, "getPermissions must not throw")
    }
    check(PermissionsProbe.kinds == ["screen", "microphone", "notifications", "vault"], "permission kinds vocabulary frozen")
    check(Set(PermissionsProbe.panes.keys) == Set(["full_disk", "screen", "microphone", "notifications", "files_folders"]),
          "pane vocabulary frozen")
    // 笔记库探针是被动的：没有 vault_sync_mode=mirror 也没有 vaultAccessGranted 时答 unknown，绝不读 ~/Documents
    check(["granted", "unknown"].contains(PermissionsProbe.probeVaultPassive()), "vault probe is passive (granted|unknown)")
    check(PermissionsProbe.vaultRootPath().hasSuffix("Obsidian Vault") || !PermissionsProbe.vaultRootPath().isEmpty,
          "vault root = obsidian_raw's parent (default ~/Documents/Obsidian Vault)")

    // ---- 4. setLanguage flips the L() mirror (no persistence) ----
    print("[4] setLanguage:")
    let before = LanguageStore.shared.lang
    let other = before == "zh" ? "en" : "zh"
    if let state = try? bridge.handle(["method": "setLanguage", "lang": other]) {
        check(state["language"] as? String == other, "snapshot reports the new language")
        check(LanguageMirror.current == other, "LanguageMirror follows")
        check(L("中", "en") == (other == "en" ? "en" : "中"), "L() picks the new language")
    } else {
        check(false, "setLanguage must not throw for zh|en")
    }
    _ = try? bridge.handle(["method": "setLanguage", "lang": before])
    check(LanguageStore.shared.lang == before, "language restored")

    // ---- 5. LegacyPrefs seed: copy only unset keys, once ----
    print("[5] LegacyPrefs.seedFromNativeAppIfNeeded:")
    let stamp = String(Int(Date().timeIntervalSince1970 * 1000))
    let sourceName = "zai.harness.source.\(stamp)"
    let targetName = "zai.harness.target.\(stamp)"
    let source = UserDefaults(suiteName: sourceName)!
    let target = UserDefaults(suiteName: targetName)!
    defer {
        source.removePersistentDomain(forName: sourceName)
        target.removePersistentDomain(forName: targetName)
    }
    source.set("screen_audio", forKey: "recordingMode")
    source.set(true, forKey: "liveCaptionsEnabled")
    source.set(30.0, forKey: "captionsFontSize")
    source.set(true, forKey: "screenTCCWasGranted")      // must NOT travel
    target.set("off", forKey: "recordingMode")           // shell-side choice wins
    let copied = LegacyPrefs.seedFromNativeAppIfNeeded(target: target, source: source)
    check(copied.sorted() == ["captionsFontSize", "liveCaptionsEnabled"],
          "copies only unset, whitelisted keys", "got \(copied)")
    check(target.string(forKey: "recordingMode") == "off", "never overwrites a shell value")
    check(target.object(forKey: "screenTCCWasGranted") == nil,
          "TCC history is not inherited (new bundle id needs its own grant)")
    check(target.bool(forKey: LegacyPrefs.marker), "marker written")
    source.set(40.0, forKey: "captionsOpacity")
    let second = LegacyPrefs.seedFromNativeAppIfNeeded(target: target, source: source)
    check(second.isEmpty && target.object(forKey: "captionsOpacity") == nil,
          "runs once (marker) — later native changes do not leak in")
    let noSource = LegacyPrefs.seedFromNativeAppIfNeeded(
        target: UserDefaults(suiteName: targetName + ".b")!, source: nil)
    check(noSource.isEmpty, "no native domain → nothing copied, no crash")
    UserDefaults(suiteName: targetName + ".b")?.removePersistentDomain(forName: targetName + ".b")
}

MainActor.assumeIsolated { run() }
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
