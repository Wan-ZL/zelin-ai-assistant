// BridgeHarness.swift — behavior tests for shell/Sources/ShellBridge.swift
// (the `zaiShell` wire contract, CONTRACT §61.1) + LegacyPrefs (§61.4).
// Compiled by run.sh together with every shell source except main.swift into a
// plain macOS CLI tool — no Xcode, no XCTest, no WKWebView. Exits non-zero on
// any failure. Same harness style as ios/tests/captions.
//
// Boundaries the harness deliberately does NOT cross: no `setCaptions` with
// VALID args and no `setRecording` / `refreshRecording` against the REAL
// RecordingController (those spawn pgrep/pkill, write UserDefaults and
// analytics — run.sh sandboxes AIASSISTANT_HOME, but the engines are the mac
// app's frozen logic and are covered by their own drift guards) — the valid
// setRecording / refreshRecording cases below run against the injected
// `RecordingActions` seams (never CGRequestScreenCaptureAccess, never setMode);
// the §61.7 schedule cases swap `RecordingSchedule.active` for one backed by
// a throwaway UserDefaults suite and inject its now / mode / engine seams
// (never pkill, never applyMode);
// no UserDefaults.standard writes (LegacyPrefs gets injected suites).

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
    // §28 追记 add-only (D39): the wizard finale's 「登录时自动启动」 row is offered only when the shell
    // reports an installed bundle; this harness is a bare CLI tool, so the key is present and false.
    check(snap["launch_at_login_available"] is Bool, "launch_at_login_available is bool")
    check(snap["launch_at_login_available"] as? Bool == false,
          "launch_at_login_available is false for a bare binary (no bundle id / not under /Applications)")
    check((snap["hotkey"] as? String)?.isEmpty == false, "hotkey label present")
    // §61.1 追记 add-only keys (parity batch shell-recording-bridge): typed, never absent
    check(rec["self_heal_note"] is String, "recording.self_heal_note is a string")
    check(rec["log_tail"] is String, "recording.log_tail is a string")
    check((rec["log_tail"] as? String) == "" || rec["diagnosis"] is String,
          "log_tail is empty unless a diagnosis is present")
    check(cap["translation_note"] is String, "captions.translation_note is a string")
    check(cap["translation_active"] is Bool, "captions.translation_active is bool")
    check(cap["source_note"] is String, "captions.source_note is a string")
    check(cap["apple_engine_available"] as? Bool == appleCaptionEngineAvailable(),
          "captions.apple_engine_available mirrors appleCaptionEngineAvailable()")
    check(perm["screen_requested"] is Bool, "permissions.screen_requested is bool")
    check(perm["screen_requested"] as? Bool == Prefs.bool(PermissionsProbe.screenRequestedKey, default: false),
          "permissions.screen_requested mirrors the screenPermissionRequested pref")

    // ---- 2. JSON is a valid JS expression for the event push ----
    print("[2] stateJSON round-trips:")
    if let json = ShellBridge.stateJSON(),
       let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
        check(obj["recording"] != nil && obj["captions"] != nil, "parses back with both blocks")
    } else {
        check(false, "stateJSON produced parseable JSON")
    }

    // ---- 2b. shell → page command event detail (§61.6; D40 open_page) ----
    print("[2b] commandJSON (zai-shell-command detail):")
    if let json = ShellBridge.commandJSON("quick_capture"),
       let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
        check(obj["command"] as? String == "quick_capture" && obj.count == 1, "quick_capture → {command} only (old vocabulary unchanged)")
    } else {
        check(false, "commandJSON(quick_capture) produced parseable JSON")
    }
    if let json = ShellBridge.commandJSON("open_page", args: ["page": "settings", "anchor": "live_captions"]),
       let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
        check(obj["command"] as? String == "open_page", "open_page → command key")
        check(obj["page"] as? String == "settings" && obj["anchor"] as? String == "live_captions",
              "open_page carries page + anchor verbatim (web app.tsx shellPageUrl reads these keys)")
    } else {
        check(false, "commandJSON(open_page) produced parseable JSON")
    }
    if let json = ShellBridge.commandJSON("open_page", args: ["page": "about", "command": "quick_capture"]),
       let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any] {
        check(obj["command"] as? String == "open_page", "args cannot overwrite the command key")
    } else {
        check(false, "commandJSON with a rogue command arg still parses")
    }
    // the dispatched event is cancelable and the script evaluates to "was it handled" (page preventDefault → dispatchEvent false)
    if let script = ShellBridge.commandScript("open_page", args: ["page": "about"]) {
        check(script.hasPrefix("!window.dispatchEvent(new CustomEvent('zai-shell-command'"),
              "commandScript dispatches zai-shell-command and evaluates to the handled flag")
        check(script.contains("cancelable: true") && script.contains("\"page\":\"about\""),
              "commandScript event is cancelable and carries the detail")
    } else {
        check(false, "commandScript produced a script")
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
    checkRecordingActions(bridge)
    checkRecordingSchedule(bridge)
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
    // §68.1 chooseFolder：注入假面板（绝不弹真 NSOpenPanel）；回执 = 快照 + dialog.path；取消 = null；类型严格
    var dialogCalls: [(String, String?)] = []
    let realRunner = FolderDialog.runner
    FolderDialog.runner = { current, prompt in dialogCalls.append((current, prompt)); return current.isEmpty ? nil : "~/Picked" }
    defer { FolderDialog.runner = realRunner }
    if let reply = try? bridge.handle(["method": "chooseFolder", "current": "~/Notes", "prompt": "选择"]) {
        let dialog = reply["dialog"] as? [String: Any]
        check(dialog?["path"] as? String == "~/Picked", "chooseFolder returns dialog.path alongside the snapshot")
        check(reply["recording"] != nil, "chooseFolder reply still carries the snapshot")
        check(dialogCalls.count == 1 && dialogCalls[0].0 == "~/Notes" && dialogCalls[0].1 == "选择",
              "chooseFolder forwards current + prompt to the panel")
    } else {
        check(false, "chooseFolder must not throw for string args")
    }
    if let reply = try? bridge.handle(["method": "chooseFolder"]) {
        check((reply["dialog"] as? [String: Any])?["path"] is NSNull, "cancelled dialog → dialog.path is null")
    } else {
        check(false, "chooseFolder without args is valid (current defaults to empty)")
    }
    check(rejection(["method": "chooseFolder", "current": 3]).hasPrefix("INVALID_ARGS"),
          "chooseFolder current must be a string")
    check(rejection(["method": "chooseFolder", "prompt": true]).hasPrefix("INVALID_ARGS"),
          "chooseFolder prompt must be a string")
    check((try? bridge.handle(["method": "getState"]))?["dialog"] == nil, "non-dialog methods carry no dialog block")
    // §68.2 追记 probeCaptionKey：词表 / 类型严格 / 沙箱里没保存 key → INVALID_ARGS；注入假探针（绝不连网）
    check(cap["key_probe"] is NSNull, "captions.key_probe is null before any test")
    check(rejection(["method": "probeCaptionKey"]).hasPrefix("INVALID_ARGS"), "probeCaptionKey without name rejected")
    check(rejection(["method": "probeCaptionKey", "name": "anthropic-api-key.txt"]).hasPrefix("INVALID_ARGS"),
          "probeCaptionKey outside the two volcano files rejected")
    check(rejection(["method": "probeCaptionKey", "name": "volcano-ark-key.txt", "value": 3]).hasPrefix("INVALID_ARGS"),
          "probeCaptionKey value must be a string")
    check(rejection(["method": "probeCaptionKey", "name": "volcano-ark-key.txt"]).hasPrefix("INVALID_ARGS: nothing to test"),
          "probeCaptionKey with nothing saved and no value rejected", rejection(["method": "probeCaptionKey", "name": "volcano-ark-key.txt"]))
    var probed: (String, String)? = nil
    CaptionKeyCheck.shared.arkProbe = { key, model, done in
        probed = (key, model)
        done(.modelNotFound(detail: "harness"))
    }
    CaptionKeyCheck.shared.speechProbe = { credential, done in
        probed = (credential.fileRepresentation, "speech")
        done(.ok)
    }
    if let reply = try? bridge.handle(["method": "probeCaptionKey", "name": "volcano-ark-key.txt", "value": " ark-KEY "]) {
        let probe = (reply["captions"] as? [String: Any])?["key_probe"] as? [String: Any]
        check(probed?.0 == "ark-KEY", "probeCaptionKey trims the pasted value and probes it (not the file)")
        check(probed?.1 == LiveCaptionsController.shared.arkModel, "ark probe uses the configured Ark model")
        check(probe?["name"] as? String == "volcano-ark-key.txt", "key_probe names the credential")
        check(probe?["state"] as? String == "done" && probe?["verdict"] as? String == "model_not_found"
              && probe?["detail"] as? String == "harness", "synchronous fake verdict lands in the snapshot",
              String(describing: probe))
        check(probe?["code"] as? String == "" && probe?["message"] as? String == "", "unused verdict fields are empty strings")
    } else {
        check(false, "probeCaptionKey with a pasted value must not throw")
    }
    if let reply = try? bridge.handle(["method": "probeCaptionKey", "name": "volcano-speech-key.txt", "value": "1234567890:token-abc"]) {
        let probe = (reply["captions"] as? [String: Any])?["key_probe"] as? [String: Any]
        check(probed?.1 == "speech" && probed?.0.contains("token-abc") == true, "speech probe gets the parsed credential")
        check(probe?["verdict"] as? String == "ok", "ok verdict lands", String(describing: probe))
    } else {
        check(false, "probeCaptionKey speech with a pasted value must not throw")
    }
    let describe = CaptionKeyCheck.describe(name: "volcano-speech-key.txt", verdict: .resourceNotEnabled(code: "45000030", message: "not activated"))
    check(describe["verdict"] as? String == "resource_not_enabled" && describe["code"] as? String == "45000030"
          && describe["message"] as? String == "not activated", "describe carries code + message for resource verdicts")
    check(CaptionKeyCheck.result(.badKey(detail: "x")) == "unauthorized" && CaptionKeyCheck.result(.network(detail: "x")) == "error",
          "analytics result vocabulary mirrors the native applyCaptionVerdict")
    check(FolderDialog.abbreviateHome(NSHomeDirectory() + "/Notes") == "~/Notes", "abbreviateHome folds $HOME to ~")
    check(FolderDialog.abbreviateHome("/Volumes/X") == "/Volumes/X", "abbreviateHome leaves other paths alone")
    // §54 追记 2026-09-06 / D41：页面 <input type=file>（📎 贴图）的 WKUIDelegate runOpenPanelWith 落点 = FileDialog。
    // 注入假面板（绝不弹真 NSOpenPanel）：多选标志原样透传；取消 = nil；今日只认图片（页面唯一的文件输入是 📎）
    var fileDialogCalls: [Bool] = []
    let realFileRunner = FileDialog.runner
    FileDialog.runner = { multiple in
        fileDialogCalls.append(multiple)
        return multiple ? [URL(fileURLWithPath: "/tmp/a.png"), URL(fileURLWithPath: "/tmp/b.png")] : nil
    }
    defer { FileDialog.runner = realFileRunner }
    check(FileDialog.chooseImages(multiple: true)?.map(\.lastPathComponent) == ["a.png", "b.png"],
          "chooseImages hands the picked URLs back (multiple)")
    check(FileDialog.chooseImages(multiple: false) == nil, "chooseImages returns nil on cancel")
    check(fileDialogCalls == [true, false], "chooseImages passes the page's allowsMultipleSelection through")
    check(FileDialog.imageTypes == [.image], "file panel only admits images (the page's only file input is the 📎 picker)")
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
    check(UserDefaults(suiteName: targetName + ".b")!.object(forKey: LegacyPrefs.marker) == nil,
          "no native domain → no marker either (a later install of the native domain still seeds)")
    UserDefaults(suiteName: targetName + ".b")?.removePersistentDomain(forName: targetName + ".b")

    checkOverlayFrameSeed(target: target, source: source, targetName: targetName)
    checkTerminalTakeover()
}

/// §61.1 追记 setRecording on:true = TCC 提示（缺才弹）→ setMode；on:false = setMode("off") 不碰 TCC；
/// refreshRecording 执行体恰跑一次、getState 纯读。注入 RecordingActions 四缝（绝不调真 CGRequest /
/// setMode / pgrep）；`trace` 钉顺序。
@MainActor
func checkRecordingActions(_ bridge: ShellBridge) {
    print("[3b] setRecording / refreshRecording through the RecordingActions seams:")
    var trace: [String] = []
    var permissionGranted = false
    let realHas = RecordingActions.hasScreenPermission
    let realRequest = RecordingActions.requestScreenPermission
    let realSetMode = RecordingActions.setMode
    let realRefresh = RecordingActions.refresh
    RecordingActions.hasScreenPermission = { permissionGranted }
    RecordingActions.requestScreenPermission = { trace.append("request") }
    RecordingActions.setMode = { trace.append("setMode:\($0)") }
    RecordingActions.refresh = { trace.append("refresh") }
    defer {
        RecordingActions.hasScreenPermission = realHas
        RecordingActions.requestScreenPermission = realRequest
        RecordingActions.setMode = realSetMode
        RecordingActions.refresh = realRefresh
    }
    if let reply = try? bridge.handle(["method": "setRecording", "on": true, "mode": "screen"]) {
        check(trace == ["request", "setMode:screen"],
              "on:true without the grant requests Screen Recording ONCE, then setMode", "got \(trace)")
        check(reply["recording"] != nil, "setRecording reply is the snapshot")
    } else {
        check(false, "setRecording on:true mode:screen must not throw")
    }
    trace = []
    _ = try? bridge.handle(["method": "setRecording", "on": true, "mode": "screen_audio"])
    check(trace == ["request", "setMode:screen_audio"],
          "every turn-on re-asks while the grant is missing (macOS itself dedups the prompt)", "got \(trace)")
    trace = []
    permissionGranted = true
    _ = try? bridge.handle(["method": "setRecording", "on": true])
    check(trace == ["setMode:\(RecordingController.shared.resumeMode)"],
          "on:true with the grant never requests; mode defaults to resume_mode", "got \(trace)")
    trace = []
    permissionGranted = false
    _ = try? bridge.handle(["method": "setRecording", "on": false])
    check(trace == ["setMode:off"], "on:false = setMode(off), no TCC request even without the grant", "got \(trace)")
    trace = []
    _ = try? bridge.handle(["method": "setRecording", "on": true, "mode": "video"])
    check(trace.isEmpty, "a rejected setRecording touches neither TCC nor setMode")
    // §61.1 追记 refreshRecording：执行体跑一次（pollScreenPermission + refreshEngineState），回执 = 快照
    if let reply = try? bridge.handle(["method": "refreshRecording"]) {
        check(trace == ["refresh"], "refreshRecording runs the refresh seam exactly once", "got \(trace)")
        check(reply["recording"] != nil && reply["dialog"] == nil, "refreshRecording reply is the plain snapshot")
    } else {
        check(false, "refreshRecording must not throw")
    }
    trace = []
    _ = try? bridge.handle(["method": "getState"])
    check(trace.isEmpty, "getState stays pure (no refresh, no TCC)")
}

/// §61.4 追记：字幕悬浮窗的拖动位置（NSWindow autosave "liveCaptionsPanel"）在自己的一次性标记下
/// 补种——已经播过种的壳（传入的 target：marker 已 true）也收到一次；壳自己拖过的永不覆盖。
@MainActor
func checkOverlayFrameSeed(target: UserDefaults, source: UserDefaults, targetName: String) {
    print("[6] LegacyPrefs overlay frame (own one-shot marker):")
    let frame = "120 80 760 110 0 0 1440 877 "
    check(LegacyPrefs.overlayFrameKey == "NSWindow Frame liveCaptionsPanel",
          "overlay frame key = NSWindow's autosave record for liveCaptionsPanel")
    check(!LegacyPrefs.keys.contains(LegacyPrefs.overlayFrameKey), "frame is NOT in the first-seed key list")
    // the seeded target above already has both markers (the second call armed the frame marker while the
    // native domain had no frame yet) → a frame written natively afterwards must not leak in
    check(target.bool(forKey: LegacyPrefs.overlayFrameMarker), "frame marker armed by the previous run")
    source.set(frame, forKey: LegacyPrefs.overlayFrameKey)
    check(LegacyPrefs.seedFromNativeAppIfNeeded(target: target, source: source).isEmpty
          && target.object(forKey: LegacyPrefs.overlayFrameKey) == nil,
          "frame marker set → later native frames do not leak in")
    // an install seeded BEFORE this key existed: prefs marker true, frame marker absent → frame copied once
    let legacyName = targetName + ".legacy"
    let legacy = UserDefaults(suiteName: legacyName)!
    defer { legacy.removePersistentDomain(forName: legacyName) }
    legacy.set(true, forKey: LegacyPrefs.marker)
    let frameOnly = LegacyPrefs.seedFromNativeAppIfNeeded(target: legacy, source: source)
    check(frameOnly == [LegacyPrefs.overlayFrameKey],
          "already-seeded install receives the frame once (and nothing else)", "got \(frameOnly)")
    check(legacy.string(forKey: LegacyPrefs.overlayFrameKey) == frame, "frame value copied verbatim")
    check(legacy.object(forKey: "recordingMode") == nil, "prefs marker still honoured — recordingMode not re-seeded")
    check(legacy.bool(forKey: LegacyPrefs.overlayFrameMarker), "frame marker written")
    source.set("1 1 320 72 0 0 1440 877 ", forKey: LegacyPrefs.overlayFrameKey)
    check(LegacyPrefs.seedFromNativeAppIfNeeded(target: legacy, source: source).isEmpty
          && legacy.string(forKey: LegacyPrefs.overlayFrameKey) == frame,
          "frame copies once — later native drags stay native")
    // a shell that already dragged its own overlay keeps its frame
    let draggedName = targetName + ".dragged"
    let dragged = UserDefaults(suiteName: draggedName)!
    defer { dragged.removePersistentDomain(forName: draggedName) }
    dragged.set("9 9 500 100 0 0 1440 877 ", forKey: LegacyPrefs.overlayFrameKey)
    let draggedCopied = LegacyPrefs.seedFromNativeAppIfNeeded(target: dragged, source: source)
    check(!draggedCopied.contains(LegacyPrefs.overlayFrameKey)
          && dragged.string(forKey: LegacyPrefs.overlayFrameKey) == "9 9 500 100 0 0 1440 877 ",
          "shell-side frame never overwritten", "got \(draggedCopied)")
    check(dragged.bool(forKey: LegacyPrefs.overlayFrameMarker) && dragged.bool(forKey: LegacyPrefs.marker),
          "fresh install arms both markers in one run")
}

/// §68.7（issue #216）终端接管：TerminalLauncher 三形 AppleScript + 两层引号、terminal_app 解析六例、
/// TerminalRelay 队列消费（新鲜 / 过期 / 坏形 / .tmp / 顺序 / 消费）、ShellHeartbeat beat / stop——全部在沙盒
/// AIASSISTANT_HOME 下，绝不 spawn osascript。
@MainActor
func checkTerminalTakeover() {
    // ---- 7. §68.7 terminal takeover: launcher quoting / setting resolution, queue relay, heartbeat ----
    print("[7] TerminalLauncher + TerminalRelay (issue #216):")
    // quoting layers: single-quote the whole shell line, then AppleScript-escape it
    check(TerminalLauncher.shellSingleQuoted("a 'b' c") == "'a '\\''b'\\'' c'", "POSIX single-quoting closes–escapes–reopens")
    check(TerminalLauncher.appleScriptQuoted("say \"hi\" \\ there") == "\"say \\\"hi\\\" \\\\ there\"", "AppleScript literal escapes \\ and \"")
    let ghostty = TerminalLauncher.script(for: .ghostty, command: "claude --resume x")
    check(ghostty.contains("new tab in window 1 with configuration {command:\"/bin/zsh -lc 'claude --resume x'\"}")
          && ghostty.contains("new window with configuration"), "Ghostty script: new tab in window 1, else new window",
          ghostty)
    check(TerminalLauncher.script(for: .terminal, command: "claude --resume x").contains("do script \"claude --resume x\""),
          "Terminal.app script: do script <line>")
    check(TerminalLauncher.script(for: .iterm2, command: "claude").contains("create window with default profile command \"/bin/zsh -lc 'claude'\""),
          "iTerm2 script: create window with default profile command")
    check(TerminalLauncher.bootstrapped("claude").hasPrefix(TerminalLauncher.pathBootstrap)
          && TerminalLauncher.bootstrapped("claude").hasSuffix("claude"), "executed line = PATH bootstrap + raw command")
    // D36 / issue #216 复合接管命令：server 的 shell_line 是 `cd '<cwd>' || { echo 'folder not found:' '<cwd>'; exit 1; }; export AIASSISTANT_HOME=…; cd '<wt>' && claude --resume <id>`
    // （不 exec——`exec cd` 会让 shell 静默退出，退役 .command 通道就是这样坏的；echo 里的 cwd 也 shlex.quote 过——路径可能是 LLM 原文）。
    // 壳必须把整行**作为一个 shell 字串**交给 /bin/zsh -lc：单引号层 closes–escapes–reopens 每个 '，双引号只在 AppleScript 层
    // 转义（PATH 兜底那句里有），&& / ; / {} 原样进 zsh。
    let compound = "cd '/tmp/h' || { echo 'folder not found:' '/tmp/h'; exit 1; }; export AIASSISTANT_HOME=/tmp/h; cd '/tmp/wt' && claude --resume 6f9619ff"
    let executed = TerminalLauncher.bootstrapped(compound)
    check(executed == TerminalLauncher.pathBootstrap + compound && !executed.contains("exec "),
          "compound shell_line rides verbatim behind the PATH bootstrap — no exec anywhere", executed)
    let ghosttyCompound = TerminalLauncher.script(for: .ghostty, command: executed)
    let expectedZsh = "/bin/zsh -lc '" + executed.replacingOccurrences(of: "'", with: "'\\''") + "'"
    check(ghosttyCompound.contains("{command:" + TerminalLauncher.appleScriptQuoted(expectedZsh) + "}"),
          "Ghostty: the whole compound line is ONE zsh -lc argument (cd && claude survive both quoting layers)", ghosttyCompound)
    check(ghosttyCompound.contains("cd '\\\\''/tmp/wt'\\\\'' && claude --resume 6f9619ff")
          && ghosttyCompound.contains("echo '\\\\''folder not found:'\\\\'' '\\\\''/tmp/h'\\\\''; exit 1;")
          && ghosttyCompound.contains("export PATH=\\\"$HOME/.local/bin"),
          "Ghostty: single quotes re-opened, double quotes AppleScript-escaped, && and ; untouched", ghosttyCompound)
    check(TerminalLauncher.script(for: .iterm2, command: executed).contains("command " + TerminalLauncher.appleScriptQuoted(expectedZsh)),
          "iTerm2: same zsh -lc wrapping for the compound line")
    check(TerminalLauncher.script(for: .terminal, command: executed).contains("do script " + TerminalLauncher.appleScriptQuoted(executed)),
          "Terminal.app: compound line goes to do script as one AppleScript string (login shell parses it)")
    // terminal_app setting (server-owned, §68.1) resolved against installed apps — mirrors server resolve_terminal
    let onlyTerminal: (TerminalApp) -> Bool = { $0 == .terminal }
    let all: (TerminalApp) -> Bool = { _ in true }
    check(TerminalLauncher.resolve(setting: "auto", installed: all) == .ghostty, "auto → Ghostty when installed")
    check(TerminalLauncher.resolve(setting: "auto", installed: onlyTerminal) == .terminal, "auto → Terminal when Ghostty absent")
    check(TerminalLauncher.resolve(setting: "iterm2", installed: all) == .iterm2, "explicit iterm2 wins when installed")
    check(TerminalLauncher.resolve(setting: "iterm2", installed: onlyTerminal) == .terminal, "uninstalled choice falls back like auto")
    check(TerminalLauncher.resolve(setting: "bogus", installed: all) == .ghostty, "unknown value = auto")
    check(TerminalLauncher.resolve(setting: nil, installed: onlyTerminal) == .terminal, "missing override = auto")
    // queue relay in the sandboxed AIASSISTANT_HOME: parse shape, stale + malformed dropped, fresh launched oldest-first, consumed
    let fm = FileManager.default
    let qdir = TerminalRelay.queueDir
    check(qdir.hasPrefix(AppPaths.stateRoot) && qdir.hasSuffix("/state/terminal_queue"), "queue dir = <home>/state/terminal_queue")
    try? fm.createDirectory(atPath: qdir, withIntermediateDirectories: true)
    let now: TimeInterval = 1_700_000_000
    func writeEntry(_ name: String, _ obj: [String: Any]) {
        let data = try! JSONSerialization.data(withJSONObject: obj)
        fm.createFile(atPath: qdir + "/" + name, contents: data)
    }
    writeEntry("b.json", ["id": "b", "kind": "takeover", "command": "claude", "shell_line": "claude", "created_at": now - 2])
    writeEntry("a.json", ["id": "a", "kind": "maintainer", "command": "cd /r && claude", "shell_line": "cd /r; claude", "created_at": now - 30])
    writeEntry("old.json", ["id": "old", "kind": "takeover", "command": "claude", "shell_line": "claude", "created_at": now - TerminalRelay.staleAfter - 1])
    writeEntry("bad.json", ["id": "bad", "kind": "takeover"])          // no shell_line → malformed
    fm.createFile(atPath: qdir + "/half.json.tmp", contents: Data("{".utf8))   // in-flight server write: never touched
    check(TerminalRelay.parse(path: "/p", ["id": "x", "kind": "takeover", "command": "c", "shell_line": "c", "created_at": 1.0]) != nil,
          "parse accepts the server entry shape")
    check(TerminalRelay.parse(path: "/p", ["id": "x", "kind": "takeover", "command": "c", "shell_line": "", "created_at": 1.0]) == nil,
          "parse rejects an empty shell_line")
    var launched: [String] = []
    let drained = TerminalRelay.drain(now: now) { launched.append($0.id + ":" + $0.shellLine) }
    check(launched == ["a:cd /r; claude", "b:claude"], "fresh entries launched oldest first, stale/malformed never launched", "\(launched)")
    check(drained.map(\.kind) == ["maintainer", "takeover"], "drain returns what it handed to launch")
    let left = (try? fm.contentsOfDirectory(atPath: qdir))?.sorted() ?? []
    check(left == ["half.json.tmp"], "consumed + stale + malformed entries deleted; the .tmp in-flight write is left alone", "\(left)")
    check(TerminalRelay.drain(now: now) { _ in check(false, "nothing to launch on an empty queue") }.isEmpty, "empty queue → no launches")
    check(TerminalRelay.staleAfter == 60 && TerminalRelay.tickInterval == 1.0, "stale threshold 60 s (server STALE_AFTER_S), 1 s tick")
    // heartbeat: beat creates/touches state/shell.heartbeat, stop removes it
    ShellHeartbeat.stop()
    check(!fm.fileExists(atPath: ShellHeartbeat.path), "no heartbeat before the first beat")
    ShellHeartbeat.beat(now: Date(timeIntervalSince1970: now - 100))
    let beat1 = (try? fm.attributesOfItem(atPath: ShellHeartbeat.path))?[.modificationDate] as? Date
    ShellHeartbeat.beat(now: Date(timeIntervalSince1970: now))
    let beat2 = (try? fm.attributesOfItem(atPath: ShellHeartbeat.path))?[.modificationDate] as? Date
    check(ShellHeartbeat.path.hasSuffix("/state/shell.heartbeat"), "heartbeat path = <home>/state/shell.heartbeat")
    check(beat1 != nil && beat2 != nil && beat2! > beat1!, "beat touches the mtime forward", "\(String(describing: beat1)) → \(String(describing: beat2))")
    check((try? String(contentsOfFile: ShellHeartbeat.path, encoding: .utf8))?.hasPrefix("pid=") == true, "heartbeat body carries the pid")
    let beatMode = ((try? fm.attributesOfItem(atPath: ShellHeartbeat.path))?[.posixPermissions] as? NSNumber)?.intValue ?? -1
    check(beatMode == 0o600, "heartbeat file is 0600 (private-file lens inside state/)", "\(beatMode)")
    ShellHeartbeat.stop()
    check(!fm.fileExists(atPath: ShellHeartbeat.path), "stop removes the heartbeat (server flips to 503 at once)")
}

MainActor.assumeIsolated { run() }
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)

/// §61.7 录制日程：纯窗口数学（固定 UTC 公历，日期自证星期）+ 控制器边界执法（缝注入：时钟 / 模式 /
/// 引擎活性 / 停 / 起——绝不 pkill、绝不 applyMode）+ 桥 `setRecordingSchedule` 词表与 all-or-nothing +
/// 快照 `recording.schedule` 与「paused ⇒ diagnosis null」。
@MainActor
func checkRecordingSchedule(_ bridge: ShellBridge) {
    print("[7] RecordingSchedule (§61.7):")
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    // 2026-09-06 是周日（weekday 1）；at(weekday, h, m) = 那一周的某天某刻
    func at(_ weekday: Int, _ h: Int, _ m: Int) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 9, day: 6 + (weekday - 1), hour: h, minute: m))!
    }
    check(cal.component(.weekday, from: at(1, 0, 0)) == 1 && cal.component(.weekday, from: at(7, 0, 0)) == 7,
          "harness calendar anchor: 2026-09-06 is a Sunday")

    // ---- spec math ----
    let d = RecordingScheduleSpec.defaults
    check(!d.enabled && d.start == "09:00" && d.end == "19:00" && d.days == [2, 3, 4, 5, 6] && d.isValid,
          "defaults = off, 09:00–19:00, Mon–Fri (issue #27's example; off = today's always-on)")
    check(RecordingScheduleSpec.minutes("09:00") == 540 && RecordingScheduleSpec.minutes("23:59") == 1439
          && RecordingScheduleSpec.minutes("00:00") == 0, "HH:MM parses to minutes")
    // 带正负号的两位段：Int("+9") / Int("-0") 都解析得出来，长度检查拦不住——必须是 ASCII 数字（全角数字也不是）
    for bad in ["9:00", "24:00", "09:60", "0900", "09:00 ", "", "ab:cd", "+9:00", "-0:30", "09:-0", "09:+5", "０９:00"] {
        check(RecordingScheduleSpec.minutes(bad) == nil, "\"\(bad)\" is not a clock value")
    }
    check(RecordingScheduleSpec.normalizedDays([5, 2, 2]) == [2, 5], "days sort + dedupe")
    check(RecordingScheduleSpec.normalizedDays([]) == nil && RecordingScheduleSpec.normalizedDays([0]) == nil
          && RecordingScheduleSpec.normalizedDays([8]) == nil, "days must be non-empty and within 1…7")
    check(d.contains(at(2, 9, 0), calendar: cal) && d.contains(at(2, 18, 59), calendar: cal),
          "Mon 09:00 and 18:59 are inside [start, end)")
    check(!d.contains(at(2, 8, 59), calendar: cal) && !d.contains(at(2, 19, 0), calendar: cal),
          "Mon 08:59 and 19:00 are outside (end is exclusive)")
    check(!d.contains(at(7, 12, 0), calendar: cal) && !d.contains(at(1, 12, 0), calendar: cal),
          "weekend noon is outside a Mon–Fri schedule")
    let night = RecordingScheduleSpec(enabled: true, start: "22:00", end: "02:00", days: [6])
    check(night.isValid, "overnight window is a valid spec")
    check(night.contains(at(6, 23, 0), calendar: cal) && night.contains(at(7, 1, 0), calendar: cal),
          "Fri 22:00–02:00: Fri 23:00 and Sat 01:00 are inside (window belongs to its start day)")
    check(!night.contains(at(6, 21, 59), calendar: cal) && !night.contains(at(7, 3, 0), calendar: cal)
          && !night.contains(at(5, 23, 0), calendar: cal) && !night.contains(at(7, 23, 0), calendar: cal),
          "Fri 21:59 / Sat 03:00 / Thu 23:00 / Sat 23:00 are outside")
    let sunWrap = RecordingScheduleSpec(enabled: true, start: "22:00", end: "02:00", days: [7])
    check(sunWrap.contains(at(1, 1, 0), calendar: cal), "Sat night wraps into Sunday 01:00 (weekday 7 → 1)")
    let broken = RecordingScheduleSpec(enabled: true, start: "09:00", end: "09:00", days: [2])
    check(!broken.isValid && broken.contains(at(1, 3, 0), calendar: cal),
          "start == end is invalid and never pauses (a bad value must not kill recording)")

    // ---- controller: persistence + edge enforcement through the seams ----
    let stamp = String(Int(Date().timeIntervalSince1970 * 1000))
    let suiteName = "zai.harness.schedule.\(stamp)"
    let suite = UserDefaults(suiteName: suiteName)!
    defer { suite.removePersistentDomain(forName: suiteName) }
    let sched = RecordingSchedule(defaults: suite)
    check(sched.spec == .defaults && !sched.paused, "fresh suite loads the defaults, not paused")

    var clock = at(2, 12, 0)     // Monday noon
    var mode = "screen"
    var running = true
    var trace: [String] = []
    let realNow = RecordingSchedule.now
    let realCalendar = RecordingSchedule.calendar
    let realMode = RecordingSchedule.currentMode
    let realRunning = RecordingSchedule.engineRunning
    let realStop = RecordingSchedule.stopEngine
    let realStart = RecordingSchedule.startEngine
    let realActive = RecordingSchedule.active
    RecordingSchedule.now = { clock }
    RecordingSchedule.calendar = { cal }     // 窗口数学按 harness 的 UTC 公历算，不看本机时区
    RecordingSchedule.currentMode = { mode }
    RecordingSchedule.engineRunning = { running }
    RecordingSchedule.stopEngine = { trace.append("stop:\($0)") }
    RecordingSchedule.startEngine = { trace.append("start:\($0)") }
    RecordingSchedule.active = sched
    defer {
        RecordingSchedule.now = realNow
        RecordingSchedule.calendar = realCalendar
        RecordingSchedule.currentMode = realMode
        RecordingSchedule.engineRunning = realRunning
        RecordingSchedule.stopEngine = realStop
        RecordingSchedule.startEngine = realStart
        RecordingSchedule.active = realActive
    }

    sched.enforce(reason: "launch")
    check(!sched.paused && trace.isEmpty && sched.allowsCaptureNow(), "disabled schedule never touches the engine")
    clock = at(1, 3, 0)
    sched.enforce(reason: "tick")
    check(!sched.paused && trace.isEmpty, "disabled schedule ignores the clock entirely (always-on = today's behavior)")
    clock = at(2, 12, 0)

    var spec = RecordingScheduleSpec.defaults
    spec.enabled = true
    sched.apply(spec)
    check(!sched.paused && trace.isEmpty, "enabling inside the window changes nothing")
    check(suite.bool(forKey: RecordingSchedule.enabledKey) && suite.string(forKey: RecordingSchedule.startKey) == "09:00"
          && suite.string(forKey: RecordingSchedule.endKey) == "19:00"
          && (suite.array(forKey: RecordingSchedule.daysKey) as? [Int]) == [2, 3, 4, 5, 6],
          "apply persists all four keys")
    check(RecordingSchedule.load(from: suite) == spec, "load reads back exactly what apply wrote")

    clock = at(2, 19, 0)
    sched.enforce(reason: "tick")
    check(sched.paused && trace == ["stop:tick"] && !sched.allowsCaptureNow(),
          "crossing into the pause window stops the engine on that very tick", "got \(trace)")
    // overdue timer 先处理了边界、didWake 紧跟着来：缓存的 engineRunning 还是 true（pgrep 跑在 pkill 前面），
    // 但 stop 已经在路上——醒来那一拍不能再 stop 一次（那就是第二条 recording_schedule_pause 虚报）
    sched.enforce(reason: "wake")
    check(trace == ["stop:tick"], "tick-then-wake across one boundary = exactly one stop (the wake sees a stop already in flight)", "got \(trace)")
    trace = []
    running = true
    // stop 之后的第一拍读到的是 stop 前那次 pgrep（refreshEngineState 异步、enforce 永远读上一拍）——不算一次「看见」
    sched.enforce(reason: "tick")
    sched.enforce(reason: "tick")
    sched.enforce(reason: "tick")
    check(trace.isEmpty, "the tick right after a stop reads the pre-stop pgrep and does not count; then \(RecordingSchedule.killAfterTicks - 1) real sightings are tolerated (frozen applyMode's slow-death watch)")
    sched.enforce(reason: "tick")
    check(trace == ["stop:tick"], "the \(RecordingSchedule.killAfterTicks)rd real sighting stops it", "got \(trace)")
    trace = []
    running = false
    sched.enforce(reason: "tick")
    running = true
    sched.enforce(reason: "tick")
    sched.enforce(reason: "tick")
    check(trace.isEmpty, "the running counter resets whenever the engine is seen down")
    // prefs / launch / wake 都不是 5 s 节拍上的一拍：暂停中改一次 days 不该把宽限缩短
    sched.apply(RecordingScheduleSpec(enabled: true, start: spec.start, end: spec.end, days: [2, 3, 4, 5, 6, 7]))
    sched.apply(spec)
    check(trace.isEmpty, "prefs writes while paused (no edge) are not grace sightings", "got \(trace)")
    sched.enforce(reason: "tick")
    check(trace == ["stop:tick"], "…the 3rd tick sighting still stops it", "got \(trace)")
    trace = []
    running = false
    sched.enforce(reason: "tick")
    running = true
    sched.enforce(reason: "tick")
    sched.enforce(reason: "tick")
    check(trace.isEmpty, "counter reset again (engine seen down, then two sightings)", "got \(trace)")
    trace = []
    sched.enforce(reason: "wake")
    check(trace == ["stop:wake"], "waking inside the pause window with a revived engine (no stop in flight) stops immediately (no 3-tick grace)", "got \(trace)")
    trace = []
    running = false
    sched.enforce(reason: "wake")
    sched.enforce(reason: "wake")
    check(trace.isEmpty && sched.paused, "waking while already paused with the engine down is a no-op (no stop, no phantom pause event)", "got \(trace)")
    running = true
    // 睡前在窗内（引擎在跑），醒来已在窗外 = 边界跨越，醒来那一拍就停
    clock = at(2, 12, 0)
    sched.enforce(reason: "tick")
    check(!sched.paused && trace == ["start:tick"], "sanity: back inside the window resumes once", "got \(trace)")
    trace = []
    clock = at(2, 19, 0)
    sched.enforce(reason: "wake")
    check(sched.paused && trace == ["stop:wake"], "sleeping across the end boundary: the wake tick itself stops the engine", "got \(trace)")
    trace = []
    mode = "off"
    sched.enforce(reason: "tick")
    check(!sched.paused && trace.isEmpty, "mode off is never 'paused by schedule' and never starts anything")
    mode = "screen"
    sched.enforce(reason: "tick")
    check(sched.paused && trace == ["stop:tick"], "picking a mode outside the window pauses again", "got \(trace)")

    trace = []
    clock = at(3, 9, 0)
    sched.enforce(reason: "tick")
    check(!sched.paused && trace == ["start:tick"], "entering the window restarts the engine once", "got \(trace)")
    running = false
    sched.enforce(reason: "tick")
    sched.enforce(reason: "tick")
    check(trace == ["start:tick"], "no restart storm on later ticks while the engine is down for its own reasons")

    clock = at(3, 20, 0)
    sched.enforce(reason: "tick")
    trace = []
    spec.enabled = false
    sched.apply(spec)
    check(!sched.paused && trace == ["start:prefs"], "turning the schedule off while paused brings the engine back", "got \(trace)")
    let wire = sched.wireValue()
    check(wire["enabled"] as? Bool == false && wire["start"] as? String == "09:00" && wire["end"] as? String == "19:00"
          && wire["days"] as? [Int] == [2, 3, 4, 5, 6] && wire["paused"] as? Bool == false,
          "wireValue carries the four keys + paused")

    // ---- live → live 切换的观察期：边界不立刻 pkill，改走宽限（冻结 applyMode 的 ~9 s 慢死亡观察会把它读成假回滚） ----
    spec.enabled = true
    mode = "screen"
    running = true
    clock = at(4, 18, 59)                                   // Wed 18:59:00，窗内
    sched.apply(spec)
    trace = []
    sched.enforce(reason: "tick")
    mode = "screen_audio"                                   // owner 在 18:59:50 切到 屏幕+音频
    clock = at(4, 18, 59).addingTimeInterval(50)
    sched.enforce(reason: "tick")
    clock = at(4, 19, 0)                                    // 边界 tick：切换 10 s 前才被看见
    sched.enforce(reason: "tick")
    check(sched.paused && trace.isEmpty,
          "a live→live switch seen within \(Int(RecordingSchedule.liveSwitchHold)) s of the boundary defers the edge kill (it would land inside the frozen slow-death watch → false rollback + notice)", "got \(trace)")
    clock = clock.addingTimeInterval(5); sched.enforce(reason: "tick")
    clock = clock.addingTimeInterval(5); sched.enforce(reason: "tick")
    check(trace.isEmpty, "…the deferred edge rides the grace: the switch tick's sighting is stale, two real ones are tolerated", "got \(trace)")
    clock = clock.addingTimeInterval(5); sched.enforce(reason: "tick")
    check(trace == ["stop:tick"], "…and the 3rd real sighting stops it (≥ 15 s after the switch was seen)", "got \(trace)")
    // 对照：切换早于观察期的边界照旧立刻停
    trace = []
    clock = at(5, 12, 0); mode = "screen"; sched.enforce(reason: "tick")
    check(trace == ["start:tick"], "sanity: Thu noon resumes once", "got \(trace)")
    trace = []
    mode = "screen_audio"; clock = at(5, 18, 59); sched.enforce(reason: "tick")   // 边界前 60 s 看见切换
    clock = at(5, 19, 0); sched.enforce(reason: "tick")
    check(trace == ["stop:tick"], "a switch seen ≥ \(Int(RecordingSchedule.liveSwitchHold)) s before the boundary does not defer the edge kill", "got \(trace)")

    // ---- paused 读时算：setRecording 改 mode 不经 enforce，快照 / 回执不能等下一拍 ----
    trace = []
    mode = "off"
    sched.enforce(reason: "tick")
    check(!sched.paused && sched.wireValue()["paused"] as? Bool == false, "mode off: not paused (stored and live agree)")
    mode = "screen"                                         // header 单选改了 mode，下一拍还没到
    check(!sched.paused && sched.pausedNow && sched.wireValue()["paused"] as? Bool == true,
          "wireValue derives paused at read time: a mode change outside the window is 按日程暂停 before the next tick")
    sched.enforce(reason: "tick")
    check(sched.paused && trace == ["stop:tick"], "…and the next tick catches up with the edge stop", "got \(trace)")

    // load: bad plist values fall back PER KEY; start == end falls back as a pair
    suite.set("9am", forKey: RecordingSchedule.startKey)
    suite.set([0, 9], forKey: RecordingSchedule.daysKey)
    suite.set("17:30", forKey: RecordingSchedule.endKey)
    let loaded = RecordingSchedule.load(from: suite)
    check(loaded.start == "09:00" && loaded.days == [2, 3, 4, 5, 6] && loaded.end == "17:30",
          "bad start / days fall back to defaults, good end survives", "got \(loaded)")
    suite.set("17:30", forKey: RecordingSchedule.startKey)
    let collapsed = RecordingSchedule.load(from: suite)
    check(collapsed.start == "09:00" && collapsed.end == "19:00", "start == end on disk falls back to the default window")

    // ---- bridge: setRecordingSchedule vocabulary (all-or-nothing) + snapshot block ----
    print("[7b] setRecordingSchedule through the bridge:")
    sched.apply(RecordingScheduleSpec.defaults)
    trace = []
    func rejection(_ body: Any?) -> String {
        do { _ = try bridge.handle(body); return "" }
        catch let e as BridgeError { return e.code }
        catch { return "OTHER" }
    }
    check(rejection(["method": "setRecordingSchedule"]).hasPrefix("INVALID_ARGS"), "no keys rejected")
    check(rejection(["method": "setRecordingSchedule", "enabled": "yes"]).hasPrefix("INVALID_ARGS"), "enabled must be bool")
    check(rejection(["method": "setRecordingSchedule", "start": "9:00"]).hasPrefix("INVALID_ARGS"), "start must be HH:MM")
    check(rejection(["method": "setRecordingSchedule", "end": 1900]).hasPrefix("INVALID_ARGS"), "end must be a string")
    check(rejection(["method": "setRecordingSchedule", "days": []]).hasPrefix("INVALID_ARGS"), "days must be non-empty")
    check(rejection(["method": "setRecordingSchedule", "days": [0, 2]]).hasPrefix("INVALID_ARGS"), "days outside 1…7 rejected")
    check(rejection(["method": "setRecordingSchedule", "days": ["2"]]).hasPrefix("INVALID_ARGS"), "days must be ints (type-strict)")
    // 真 JSON 路径：true 到 Swift 是 NSNumber(CFBoolean)，`as? Int` 会桥成 1——类型严格必须挡住它
    let boolDays = try? JSONSerialization.jsonObject(with: Data(#"{"method":"setRecordingSchedule","days":[true,3]}"#.utf8))
    check(rejection(boolDays).hasPrefix("INVALID_ARGS") && sched.spec == .defaults,
          "JSON booleans in days are rejected (NSNumber true must not bridge to Sunday)")
    check(rejection(["method": "setRecordingSchedule", "start": "19:00"]) == "INVALID_ARGS: start and end must differ",
          "start colliding with the stored end is rejected after the merge")
    check(rejection(["method": "setRecordingSchedule", "enabled": true, "start": "bad"]).hasPrefix("INVALID_ARGS")
          && sched.spec == .defaults && trace.isEmpty,
          "a bad key alongside a good one rejects the WHOLE request (zero writes, no enforcement)")
    clock = at(2, 20, 0)
    if let reply = try? bridge.handle(["method": "setRecordingSchedule", "enabled": true, "start": "08:30", "end": "17:45", "days": [4, 2, 2]]) {
        let rec = reply["recording"] as? [String: Any] ?? [:]
        let block = rec["schedule"] as? [String: Any] ?? [:]
        check(block["enabled"] as? Bool == true && block["start"] as? String == "08:30" && block["end"] as? String == "17:45"
              && block["days"] as? [Int] == [2, 4], "valid request lands in recording.schedule (days sorted + deduped)", String(describing: block))
        check(block["paused"] as? Bool == true && trace == ["stop:prefs"],
              "enabling outside the window pauses at once and stops the engine", "got \(trace)")
        check(rec["diagnosis"] is NSNull && rec["log_tail"] as? String == "",
              "paused ⇒ diagnosis null + log_tail empty (a deliberate stop is not an engine failure)")
    } else {
        check(false, "setRecordingSchedule with valid keys must not throw")
    }
    trace = []
    _ = try? bridge.handle(["method": "setRecordingSchedule", "enabled": false])
    check(sched.spec.enabled == false && sched.spec.start == "08:30" && trace == ["start:prefs"],
          "partial update keeps the other keys and resumes the engine", "got \(trace)")
    let intDays = try? JSONSerialization.jsonObject(with: Data(#"{"method":"setRecordingSchedule","days":[3,1]}"#.utf8))
    check(rejection(intDays) == "" && sched.spec.days == [1, 3], "JSON integers in days still land (sorted)", "got \(sched.spec.days)")
    let snap = ShellBridge.stateSnapshot()
    let block = (snap["recording"] as? [String: Any])?["schedule"] as? [String: Any] ?? [:]
    for key in ["enabled", "start", "end", "days", "paused"] {
        check(block[key] != nil, "recording.schedule.\(key) present in every snapshot")
    }
    if let json = ShellBridge.stateJSON(),
       let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any],
       let rec = obj["recording"] as? [String: Any], let sch = rec["schedule"] as? [String: Any] {
        check(sch["days"] is [Any] && sch["paused"] is Bool, "schedule block serializes (days array, paused bool)")
    } else {
        check(false, "stateJSON with the schedule block must serialize")
    }
}
