// LaunchHarness.swift — behavior tests for the launch-source policy in
// shell/Sources/ShellSupport.swift (D38; CONTRACT §56.5 / §61 追记: a background
// launch — the install.sh auto-deploy relaunch `open -g … --args --background`, or a
// loginwindow login-item launch — builds the board window but never orders it front
// nor activates the app; every other launch shows the window as before). Compiled by
// run.sh together with every shell source except main.swift into a plain macOS CLI
// tool — no Xcode, no XCTest, no NSWindow. Exits non-zero on any failure. Same
// harness style as PolicyHarness.swift.
//
// Why a pure policy: applicationDidFinishLaunching cannot be driven without a running
// app, so main.swift collects the two inputs (CommandLine.arguments and the launch
// Apple Event's login-item flag) and asks LaunchPolicy; the side effect (showWindow or
// a log line) is the only thing left in the delegate. Every cell is pinned here.
//
// The login-item half is split the same way: LaunchAtLogin.launchedAsLoginItem() only
// reads NSAppleEventManager.currentAppleEvent; the decode is the pure
// LaunchAtLogin.isLoginItemLaunch(_:), pinned in [5] with synthesized
// NSAppleEventDescriptors (oapp + keyAELaunchedAsLogInItem is the only true cell).
// The failure-alert timing (defer the connect-failure NSAlert on a background launch
// until the window is first shown) is LaunchPolicy.failureAlertTiming, pinned in [6].
// [7] pins LaunchAtLogin.isInstalledBundle — the D39 predicate behind the snapshot key
// `launch_at_login_available` (the wizard finale offers 「登录时自动启动」 only for a bundle
// installed under /Applications or ~/Applications; CONTRACT §28 追记).
//
// [8]/[9] pin EngineOwnership (CONTRACT §61.8, lite 2.0 adopt semantics): at launch an engine
// whose argv carries the recipe markers (`screenpipe@0.3.349 record` + `--ignored-windows`) is
// ADOPTED — pid recorded, never killed; any other engine is logged and left alone; nothing is
// ever signalled at launch. At exit only an engine this shell spawned AND that is still alive is
// stopped, and not even that one while the `engineHandoff` preference (written by the §79
// upgrade script before relaunch) is present. [8] is the pure truth table (verdicts, the pgrep
// listing parser, the exit verdict, the pidfile body), [9] the call sequences through recorded
// seams — the harness never runs a real pgrep/pkill and never writes the real pidfile or
// UserDefaults (same rule as the §61.7 schedule gate). Cell (h), against the default seam, pins
// that the exit credential is our engine's LIVENESS, never "this shell once spawned one":
// `Recording.swift` never puts `engineProcess` back to nil.

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

func run() {
    typealias P = LaunchPolicy.Presentation
    let exe = "/Applications/Zelin's AI Assistant.app/Contents/MacOS/ZelinAIBoard"
    func decide(_ args: [String], loginItem: Bool = false) -> P {
        LaunchPolicy.presentation(arguments: args, launchedAsLoginItem: loginItem)
    }

    // ---- 1. the flag install.sh passes is one exact word ----
    print("[1] LaunchPolicy.backgroundFlag:")
    check(LaunchPolicy.backgroundFlag == "--background",
          "flag is `--background` verbatim (install.sh relaunch_shell_app passes exactly this)")

    // ---- 2. argv variants ----
    print("[2] argv:")
    check(decide([exe]) == .foreground, "bare launch (Dock / Finder / `open`) → foreground")
    check(decide([]) == .foreground, "empty argv → foreground (never hide by accident)")
    check(decide([exe, "--background"]) == .background(reason: "argv --background"),
          "`open -g <bundle> --args --background` (argv[1]) → background, reason names the flag")
    check(decide([exe, "-NSDocumentRevisionsDebugMode", "YES", "--background"])
          == .background(reason: "argv --background"),
          "flag anywhere in argv → background (position does not matter)")
    check(decide([exe, "--background=1"]) == .foreground,
          "`--background=1` is not the flag → foreground (exact word only)")
    check(decide([exe, "-background"]) == .foreground, "single-dash `-background` → foreground")
    check(decide([exe, "--Background"]) == .foreground, "case matters → foreground")
    check(decide([exe, "background"]) == .foreground, "bare word `background` → foreground")
    check(decide([exe, "-NSDocumentRevisionsDebugMode", "YES"]) == .foreground,
          "unrelated argv (Xcode debug flags) → foreground")

    // ---- 3. the login-item flag from the launch Apple Event ----
    print("[3] login item:")
    check(decide([exe], loginItem: true) == .background(reason: "login item"),
          "loginwindow launch (keyAELaunchedAsLogInItem) → background, reason `login item`")
    check(decide([exe], loginItem: false) == .foreground, "no flag, no argv → foreground")
    check(decide([exe, "--background"], loginItem: true) == .background(reason: "argv --background"),
          "both signals → background; argv wins the reason (deterministic input first)")

    // ---- 4. the verdict type is exhaustive: foreground carries no reason ----
    print("[4] shape:")
    switch decide([exe]) {
    case .foreground: check(true, "foreground is a bare case (nothing to log)")
    case .background: check(false, "foreground must not come back as background")
    }
    if case .background(let reason) = decide([exe], loginItem: true) {
        check(!reason.isEmpty, "background always carries a non-empty reason for board-shell.log")
    } else {
        check(false, "login-item launch must be background")
    }

    // ---- 5. the login-item decoder, fed synthesized launch Apple Events ----
    // loginwindow sends `oapp` with keyAEPropData == keyAELaunchedAsLogInItem ('lgit');
    // Dock / Finder / `open` send a bare `oapp`; a service item carries 'lsvc' instead.
    print("[5] login-item event:")
    func launchEvent(_ eventID: AEEventID, propData: OSType? = nil) -> NSAppleEventDescriptor {
        let target = NSAppleEventDescriptor(processIdentifier: ProcessInfo.processInfo.processIdentifier)
        let event = NSAppleEventDescriptor(eventClass: AEEventClass(kCoreEventClass),
                                           eventID: eventID,
                                           targetDescriptor: target,
                                           returnID: AEReturnID(kAutoGenerateReturnID),
                                           transactionID: AETransactionID(kAnyTransactionID))
        if let code = propData {
            event.setParam(NSAppleEventDescriptor(enumCode: code),
                           forKeyword: AEKeyword(keyAEPropData))
        }
        return event
    }
    let oapp = AEEventID(kAEOpenApplication), odoc = AEEventID(kAEOpenDocuments), rapp = AEEventID(kAEReopenApplication)
    let lgit = OSType(keyAELaunchedAsLogInItem), lsvc = OSType(keyAELaunchedAsServiceItem)
    check(LaunchAtLogin.isLoginItemLaunch(launchEvent(oapp, propData: lgit)),
          "`oapp` + keyAEPropData == keyAELaunchedAsLogInItem → true (the loginwindow launch)")
    check(!LaunchAtLogin.isLoginItemLaunch(launchEvent(oapp)),
          "bare `oapp` (Dock / Finder / `open`) → false")
    check(!LaunchAtLogin.isLoginItemLaunch(launchEvent(odoc, propData: lgit)),
          "`odoc` carrying the login-item enum → false (only the open-application event counts)")
    check(!LaunchAtLogin.isLoginItemLaunch(launchEvent(oapp, propData: lsvc)),
          "`oapp` + keyAELaunchedAsServiceItem → false (a service item is not a login item)")
    check(!LaunchAtLogin.isLoginItemLaunch(nil), "no launch event at all → false (never hide by accident)")
    check(!LaunchAtLogin.isLoginItemLaunch(launchEvent(rapp, propData: lgit)),
          "`rapp` (Dock reopen) with the enum → false")
    check(!LaunchAtLogin.isLoginItemLaunch(NSAppleEventManager.shared().currentAppleEvent),
          "this harness process (no launch Apple Event) decodes false via the same reader input")

    // ---- 6. failure-alert timing: a background launch must not runModal over the owner's work ----
    print("[6] failure alert timing:")
    typealias T = LaunchPolicy.AlertTiming
    let bg = P.background(reason: "login item")
    check(LaunchPolicy.failureAlertTiming(presentation: .foreground, boardVisible: true) == T.now,
          "foreground launch, window shown → alert now (unchanged pre-D38 behaviour)")
    check(LaunchPolicy.failureAlertTiming(presentation: .foreground, boardVisible: false) == T.now,
          "foreground launch, owner already closed the window → still now (they launched it by hand)")
    check(LaunchPolicy.failureAlertTiming(presentation: bg, boardVisible: false) == T.deferUntilShown,
          "background launch, window never shown → defer until the first showWindow()")
    check(LaunchPolicy.failureAlertTiming(presentation: P.background(reason: "argv --background"),
                                          boardVisible: false) == T.deferUntilShown,
          "auto-deploy relaunch (argv flag) → defer as well; reason does not matter")
    check(LaunchPolicy.failureAlertTiming(presentation: bg, boardVisible: true) == T.now,
          "background launch but the owner has since brought the window up → now")

    // ---- 7. D39: the wizard finale's 「登录时自动启动」 row is offered only for an installed bundle ----
    // Native Onboarding.registerLaunchAtLoginDefault guarded on /Applications or ~/Applications so a dev
    // build never pins its temporary path as a login item; the shell reports the same predicate as the
    // add-only snapshot key `launch_at_login_available` (CONTRACT §28 追记).
    print("[7] installed bundle (launch_at_login_available):")
    let home = "/Users/zelin"
    func installed(_ path: String, home: String = home) -> Bool {
        LaunchAtLogin.isInstalledBundle(path: path, home: home)
    }
    check(installed("/Applications/Zelin's AI Assistant.app"), "/Applications/<bundle> → available")
    check(installed("/Users/zelin/Applications/Zelin's AI Assistant.app"),
          "~/Applications/<bundle> → available (AIASSISTANT_UI_APPS_DIR installs, fresh-install CI)")
    check(installed("/Users/zelin/Applications/Zelin's AI Assistant.app", home: home + "/"),
          "trailing slash on home tolerated")
    check(!installed("/Volumes/Storage/repo/shell/build/Zelin's AI Assistant.app"),
          "build directory (dev build) → not available")
    check(!installed("/Users/other/Applications/Zelin's AI Assistant.app"),
          "another user's ~/Applications → not available (only this home counts)")
    check(!installed("/ApplicationsX/Zelin's AI Assistant.app"),
          "prefix must be the directory `/Applications/`, not a sibling name")
    check(installed("/Applications/Zelin's AI Assistant.app", home: ""),
          "empty home still accepts /Applications/ itself")
    check(!installed("/tmp/Zelin's AI Assistant.app", home: ""),
          "empty home does not widen the rule to a bare `/Applications/` suffix match")
    check(!installed(""), "empty path → not available")

    checkEngineOwnershipVerdicts()
    checkEngineOwnershipEffects()
}

// ---- 8. §61.8 engine ownership — the pure verdicts (every cell) ---- //
func checkEngineOwnershipVerdicts() {
    print("[8] EngineOwnership verdicts:")
    typealias V = EngineOwnership.LaunchVerdict
    typealias E = EngineOwnership.EngineProcess
    let recipe = "npm exec screenpipe@0.3.349 record --disable-audio --use-all-monitors"
        + " --ignored-windows 1Password --ignored-windows Bitwarden -l chinese -l english --retention-days 1"
    let node = "node /Users/x/.npm/_npx/abc/node_modules/.bin/screenpipe record --disable-audio --ignored-windows 1Password"
    let bare = "/opt/homebrew/bin/screenpipe record --disable-audio"
    check(EngineOwnership.recipeMarkers == ["screenpipe@0.3.349 record", "--ignored-windows"],
          "recipe markers are the two literals Recording.swift startCommand emits")
    check(EngineOwnership.isRecipeEngine(recipe), "the npx wrapper of our recipe carries both markers")
    check(!EngineOwnership.isRecipeEngine(node), "the node child lacks the pinned version → not a marker line by itself")
    check(!EngineOwnership.isRecipeEngine(bare), "a hand-started engine without the markers is not ours")
    check(!EngineOwnership.isRecipeEngine("npx screenpipe@0.3.349 record --disable-audio"),
          "pinned version but no --ignored-windows (ignored_apps: []) → not adopted (both markers required)")
    check(!EngineOwnership.isRecipeEngine("npx screenpipe@0.3.350 record --ignored-windows X"),
          "another engine version → not adopted (the recipe pins 0.3.349)")
    check(EngineOwnership.decideAtLaunch(engines: []) == V.idle, "no engine at launch → idle (autostart unchanged)")
    check(EngineOwnership.decideAtLaunch(engines: [E(pid: 10, command: recipe), E(pid: 11, command: node)])
          == V.adopt(pid: 10),
          "recipe engine alive → adopt the marker-carrying pid (never reclaim, never kill)")
    check(EngineOwnership.decideAtLaunch(engines: [E(pid: 11, command: node), E(pid: 10, command: recipe)])
          == V.adopt(pid: 10),
          "adopt picks the marker-carrying line wherever pgrep lists it")
    check(EngineOwnership.decideAtLaunch(engines: [E(pid: 12, command: bare)]) == V.foreign,
          "engine without the markers → foreign (logged only: not adopted, not stopped)")
    // the pgrep -lf listing parser
    let listing = "96282 \(recipe)\n96343 \(node)\n\nnot-a-pid line\n96347   \(bare)  \n"
    let parsed = EngineOwnership.parseListing(listing)
    check(parsed.map(\.pid) == [96282, 96343, 96347], "parseListing keeps every `<pid> <command>` line — got \(parsed.map(\.pid))")
    check(parsed.first?.command == recipe, "the full command line survives (no 400-char tail truncation)")
    check(parsed.last?.command == bare, "surrounding whitespace is trimmed")
    check(EngineOwnership.parseListing("").isEmpty, "empty listing → no engines")
    check(EngineOwnership.parseListing("123\n").isEmpty, "a pid with no command is skipped")
    // exit verdict: our live engine, and only without the upgrade handoff
    check(EngineOwnership.stopAtExit(spawnedAlive: true, handoffPending: false),
          "we spawned it, it is alive, no handoff → quitting stops it (§15 追记: quitting the app stops recording)")
    check(!EngineOwnership.stopAtExit(spawnedAlive: true, handoffPending: true),
          "engineHandoff set → never stopped (§79: upgrades never interrupt recording)")
    check(!EngineOwnership.stopAtExit(spawnedAlive: false, handoffPending: false),
          "adopted / foreign / dead engine → never stopped by the quit path (lifecycle honesty)")
    check(!EngineOwnership.stopAtExit(spawnedAlive: false, handoffPending: true), "nothing spawned + handoff → nothing")
    // pidfile body
    check(EngineOwnership.pidfileBody(spawnedPid: 7, adoptedPid: 9, engineAlive: true) == "7 spawned\n",
          "a live engine this shell spawned wins the pidfile")
    check(EngineOwnership.pidfileBody(spawnedPid: nil, adoptedPid: 9, engineAlive: true) == "9 adopted\n",
          "otherwise the adopted pid, while an engine is alive")
    check(EngineOwnership.pidfileBody(spawnedPid: nil, adoptedPid: 9, engineAlive: false) == nil,
          "no engine alive → the pidfile is removed")
    check(EngineOwnership.pidfileBody(spawnedPid: nil, adoptedPid: nil, engineAlive: true) == nil,
          "a foreign engine never lands in the pidfile")
    check(EngineOwnership.handoffKey == "engineHandoff", "handoff preference key literal frozen (§61.8 / §79)")
    check(EngineOwnership.pidfilePath.hasSuffix("/Library/Application Support/ZelinAIAssistant/engine.pid"),
          "pidfile lives in Application Support, outside the repo tree")
    check(EngineOwnership.exitBudget <= 2.0,
          "the willTerminate budget stays well inside macOS's ~5 s (\(EngineOwnership.exitBudget)s)")
    check(EngineOwnership.pollStep > 0, "a non-positive poll step would spin forever")
}

// ---- 9. §61.8 engine ownership — the effects, through recorded seams ---- //
func checkEngineOwnershipEffects() {
    print("[9] EngineOwnership effects (fake pgrep/pkill/defaults/pidfile — nothing is ever signalled):")
    typealias E = EngineOwnership.EngineProcess
    let recipe = "npm exec screenpipe@0.3.349 record --disable-audio --ignored-windows 1Password"

    final class Rec {
        var calls: [String] = []
        var engines: [E]
        var engineAlive: Bool
        var spawned: Bool
        var spawnedPid: Int32?
        var handoff: Bool
        /// how many `killEngine` calls it takes before the fake engine dies (huge = never)
        var diesAfterKills: Int
        private var kills = 0
        init(engines: [E] = [], engineAlive: Bool = false, spawned: Bool = false, spawnedPid: Int32? = nil,
             handoff: Bool = false, diesAfterKills: Int = 1) {
            self.engines = engines
            self.engineAlive = engineAlive
            self.spawned = spawned
            self.spawnedPid = spawnedPid
            self.handoff = handoff
            self.diesAfterKills = diesAfterKills
        }
        func install() {
            EngineOwnership.resetState()
            EngineOwnership.listEngines = { [self] in calls.append("pgrep -lf"); return engines }
            EngineOwnership.engineRunning = { [self] in calls.append("pgrep engine"); return engineAlive }
            EngineOwnership.engineSpawnedByUs = { [self] in spawned }
            EngineOwnership.spawnedPid = { [self] in spawnedPid }
            EngineOwnership.handoffPending = { [self] in calls.append("read handoff"); return handoff }
            EngineOwnership.clearHandoff = { [self] in calls.append("clear handoff"); handoff = false }
            EngineOwnership.killEngine = { [self] signal in
                calls.append("pkill \(signal)")
                kills += 1
                if kills >= diesAfterKills { engineAlive = false }
            }
            EngineOwnership.writePidfile = { [self] body in calls.append("pidfile \(body ?? "<rm>")") }
            EngineOwnership.logLine = { [self] line in calls.append("log \(line)") }
            EngineOwnership.pause = { [self] _ in calls.append("sleep") }
        }
    }
    /// Between scenarios the seams are left INERT, never restored to the real
    /// pgrep/pkill/defaults/file writes: a stray call from anywhere in this harness must not be
    /// able to signal a real process or touch the developer's preferences (§61.7 harness rule).
    func disarmSeams() {
        EngineOwnership.listEngines = { [] }
        EngineOwnership.engineRunning = { false }
        EngineOwnership.engineSpawnedByUs = { false }
        EngineOwnership.spawnedPid = { nil }
        EngineOwnership.handoffPending = { false }
        EngineOwnership.clearHandoff = {}
        EngineOwnership.killEngine = { _ in }
        EngineOwnership.writePidfile = { _ in }
        EngineOwnership.logLine = { _ in }
        EngineOwnership.pause = { _ in }
        EngineOwnership.resetState()
    }
    func scenario(_ rec: Rec, _ body: () -> Void) -> [String] {
        rec.install()
        body()
        disarmSeams()
        return rec.calls
    }
    func signals(_ calls: [String]) -> Bool { calls.contains(where: { $0.hasPrefix("pkill") }) }

    // (a) nothing running at launch: one listing, no signal, no log, no pidfile
    var calls = scenario(Rec()) { EngineOwnership.adoptAtLaunch() }
    check(calls == ["pgrep -lf", "read handoff"],
          "idle launch: one listing + the handoff read, nothing else — got \(calls)")

    // (b) our recipe engine survived the previous shell: adopt, record, never signal
    var verdict = EngineOwnership.LaunchVerdict.idle
    calls = scenario(Rec(engines: [E(pid: 4242, command: recipe)], engineAlive: true)) {
        verdict = EngineOwnership.adoptAtLaunch()
    }
    check(verdict == .adopt(pid: 4242), "recipe engine → adopt(pid) — got \(verdict)")
    check(!signals(calls), "adopt sends not one signal — got \(calls)")
    check(calls.contains("log adopt engine pid=4242: recipe markers present — this shell never stops it on quit"),
          "the adoption is written down in engine.log — got \(calls)")
    check(calls.contains("pidfile 4242 adopted\n"), "the adopted pid lands in the pidfile — got \(calls)")

    // (c) a foreign engine: logged, not adopted, not stopped, no pidfile
    calls = scenario(Rec(engines: [E(pid: 5, command: "/opt/homebrew/bin/screenpipe record")], engineAlive: true)) {
        verdict = EngineOwnership.adoptAtLaunch()
    }
    check(verdict == .foreign && EngineOwnership.adoptedPid == nil, "foreign engine → not adopted")
    check(!signals(calls) && !calls.contains(where: { $0.hasPrefix("pidfile") }),
          "foreign engine: no signal, no pidfile — got \(calls)")
    check(calls.contains("log engine running without the recipe markers — left alone (not adopted, not stopped)"),
          "foreign engine: one honest log line — got \(calls)")

    // (d) relaunch after an upgrade: adopt, then clear the one-shot handoff
    let upgraded = Rec(engines: [E(pid: 77, command: recipe)], engineAlive: true, handoff: true)
    calls = scenario(upgraded) { EngineOwnership.adoptAtLaunch() }
    check(calls.last == "clear handoff" && !signals(calls),
          "upgrade relaunch: adopt, then clear engineHandoff (one relaunch only) — got \(calls)")
    check(!upgraded.handoff, "the handoff preference is gone after the launch decision")
    calls = scenario(Rec(handoff: true)) { EngineOwnership.adoptAtLaunch() }
    check(calls.contains("log engine handoff: no engine running to adopt") && calls.last == "clear handoff",
          "a stale handoff with no engine is logged and cleared — got \(calls)")

    // (e) quit, engine ours and well behaved, no handoff: TERM only, then the pidfile goes
    calls = scenario(Rec(engineAlive: true, spawned: true)) { EngineOwnership.stopAtExit() }
    check(calls.contains("pkill -TERM") && !calls.contains("pkill -KILL"),
          "a well-behaved engine dies on TERM — got \(calls)")
    check(calls.first == "read handoff" && calls[1] == "log stop engine on quit: this shell spawned it and it is still alive",
          "the handoff is consulted BEFORE anything is signalled, and the quit leaves a breadcrumb — got \(calls)")
    check(calls.last == "pidfile <rm>", "a stopped engine leaves no pidfile behind — got \(calls)")

    // (f) quit, engine ignores TERM: exactly one escalation to KILL
    calls = scenario(Rec(engineAlive: true, spawned: true, diesAfterKills: 2)) { EngineOwnership.stopAtExit() }
    check(calls.filter { $0 == "pkill -TERM" }.count == 1 && calls.filter { $0 == "pkill -KILL" }.count == 1,
          "TERM then exactly one KILL — got \(calls)")
    check(calls.firstIndex(of: "pkill -TERM")! < calls.firstIndex(of: "pkill -KILL")!, "TERM before KILL")
    let sleeps = calls.filter { $0 == "sleep" }.count
    check(sleeps <= Int(EngineOwnership.exitBudget / EngineOwnership.pollStep) + 1,
          "the poll is bounded by exitBudget (\(sleeps) sleeps)")

    // (g) quit with the upgrade handoff set: our own engine keeps recording
    calls = scenario(Rec(engineAlive: true, spawned: true, handoff: true)) { EngineOwnership.stopAtExit() }
    check(!signals(calls), "engineHandoff set at quit → not one signal, even though the engine is ours — got \(calls)")
    check(calls.last == "log stop engine on quit skipped: engineHandoff is set — the next shell adopts it",
          "and the skipped stop is written down — got \(String(describing: calls.last))")

    // (g2) quit, engine adopted / foreign (not spawned by us): the quit path does nothing at all
    calls = scenario(Rec(engines: [E(pid: 4242, command: recipe)], engineAlive: true, spawned: false)) {
        EngineOwnership.adoptAtLaunch()
        EngineOwnership.stopAtExit()
    }
    check(!signals(calls) && !calls.contains(where: { $0.hasPrefix("log stop") }),
          "an adopted engine is never stopped by our quit path — got \(calls)")

    // (g3) the 5 s tick keeps the pidfile honest and writes only on change
    calls = scenario(Rec(spawnedPid: 31)) {
        EngineOwnership.syncPidfile(engineAlive: true)
        EngineOwnership.syncPidfile(engineAlive: true)
        EngineOwnership.syncPidfile(engineAlive: false)
    }
    check(calls == ["pidfile 31 spawned\n"],
          "tick: our spawned pid is written once; a live spawned handle wins even when pgrep lags — got \(calls)")
    calls = scenario(Rec(engines: [E(pid: 8, command: recipe)], engineAlive: true)) {
        EngineOwnership.adoptAtLaunch()
        EngineOwnership.syncPidfile(engineAlive: true)
        EngineOwnership.syncPidfile(engineAlive: false)
    }
    check(calls.filter { $0.hasPrefix("pidfile") } == ["pidfile 8 adopted\n", "pidfile <rm>"],
          "tick: the adopted pid stays while the engine lives and is removed once it is gone — got \(calls)")

    // (h) THE exit credential itself (default seam, no recorder installed): ownership is
    //     LIVENESS, not history. `Recording.swift` assigns `engineProcess` once in
    //     startEngineBlocking and never puts it back to nil, so a `!= nil` credential
    //     would survive our engine's death (§61.7 schedule stop / mode=off / a crash)
    //     and make the exit path pkill whatever engine is alive at quit time — including
    //     an adopted one. A Process that was never launched has the same
    //     `isRunning == false` as one that exited, and costs no subprocess here.
    disarmSeams()
    RecordingController.engineProcess = nil
    check(EngineOwnership.defaultEngineSpawnedByUs() == false,
          "no engine handle at all → we own nothing")
    RecordingController.engineProcess = Process()   // 非 nil 但没在跑（= 起过又死了）
    check(EngineOwnership.defaultEngineSpawnedByUs() == false,
          "a dead engine handle must NOT be a lifetime licence to pkill by pattern")
    RecordingController.engineProcess = nil
}

run()
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
