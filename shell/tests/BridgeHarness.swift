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

    // ---- 6. §68.7 terminal takeover: launcher quoting / setting resolution, queue relay, heartbeat ----
    print("[6] TerminalLauncher + TerminalRelay (issue #216):")
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
    writeEntry("b.json", ["id": "b", "kind": "takeover", "command": "claude", "shell_line": "exec claude", "created_at": now - 2])
    writeEntry("a.json", ["id": "a", "kind": "maintainer", "command": "cd /r && claude", "shell_line": "cd /r; exec claude", "created_at": now - 30])
    writeEntry("old.json", ["id": "old", "kind": "takeover", "command": "claude", "shell_line": "exec claude", "created_at": now - TerminalRelay.staleAfter - 1])
    writeEntry("bad.json", ["id": "bad", "kind": "takeover"])          // no shell_line → malformed
    fm.createFile(atPath: qdir + "/half.json.tmp", contents: Data("{".utf8))   // in-flight server write: never touched
    check(TerminalRelay.parse(path: "/p", ["id": "x", "kind": "takeover", "command": "c", "shell_line": "exec c", "created_at": 1.0]) != nil,
          "parse accepts the server entry shape")
    check(TerminalRelay.parse(path: "/p", ["id": "x", "kind": "takeover", "command": "c", "shell_line": "", "created_at": 1.0]) == nil,
          "parse rejects an empty shell_line")
    var launched: [String] = []
    let drained = TerminalRelay.drain(now: now) { launched.append($0.id + ":" + $0.shellLine) }
    check(launched == ["a:cd /r; exec claude", "b:exec claude"], "fresh entries launched oldest first, stale/malformed never launched", "\(launched)")
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
    ShellHeartbeat.stop()
    check(!fm.fileExists(atPath: ShellHeartbeat.path), "stop removes the heartbeat (server flips to 503 at once)")
}

MainActor.assumeIsolated { run() }
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
