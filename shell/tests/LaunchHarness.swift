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
// [8]/[9] pin EngineOwnership (CONTRACT §61.8, issue #318): the launch/exit verdicts about
// who owns the running screenpipe engine, and the two effectful entry points driven through
// recorded seams — the harness never runs a real pgrep/pkill (same rule as the §61.7 schedule
// gate). [8] is the pure truth table, [9] the call sequences (log breadcrumb → pkill → poll,
// TERM→KILL escalation on exit, the "do nothing" paths, and — cell (i), against the default
// seam — that the exit credential is our engine's LIVENESS, never "this shell once spawned one":
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
    func decide(_ alive: Bool, _ legacy: Bool) -> V {
        EngineOwnership.decideAtLaunch(engineAlive: alive, legacyAppRunning: legacy)
    }
    check(decide(false, false) == V.idle, "no engine at launch → idle (autostart unchanged)")
    check(decide(false, true) == V.idle, "no engine, legacy app running → still idle (nothing to decide)")
    check(decide(true, false) == V.reclaim,
          "engine alive, no legacy app → reclaim (a fresh shell spawned nothing, so it is not ours)")
    check(decide(true, true) == V.legacyOwns,
          "engine alive while ZelinAIEngineer runs → legacyOwns (§54 frozen app owns its own engine)")
    check(EngineOwnership.legacyExecName == "ZelinAIEngineer",
          "the legacy guard uses the frozen app's CFBundleExecutable verbatim (§54)")
    check(EngineOwnership.stopAtExit(spawned: true),
          "we spawned the engine → quitting stops it (§15 追记: quitting the app stops recording)")
    check(!EngineOwnership.stopAtExit(spawned: false),
          "we spawned nothing → never kill an engine we merely saw (lifecycle honesty)")
    check(EngineOwnership.exitBudget <= 2.0,
          "the willTerminate budget stays well inside macOS's ~5 s (\(EngineOwnership.exitBudget)s)")
    check(EngineOwnership.pollStep > 0, "a non-positive poll step would spin forever")
}

// ---- 9. §61.8 engine ownership — the effects, through recorded seams ---- //
func checkEngineOwnershipEffects() {
    print("[9] EngineOwnership effects (fake pgrep/pkill — nothing is ever signalled):")

    // one recorder per scenario: `alive` is the fake pgrep's answer, mutated by the fake pkill
    final class Rec {
        var calls: [String] = []
        var engineAlive: Bool
        var legacyAlive: Bool
        var spawned: Bool
        /// how many `killEngine` calls it takes before the fake engine dies (huge = never)
        var diesAfterKills: Int
        private var kills = 0
        init(engineAlive: Bool, legacyAlive: Bool = false, spawned: Bool = false, diesAfterKills: Int = 1) {
            self.engineAlive = engineAlive
            self.legacyAlive = legacyAlive
            self.spawned = spawned
            self.diesAfterKills = diesAfterKills
        }
        func install() {
            EngineOwnership.engineRunning = { [self] in calls.append("pgrep engine"); return engineAlive }
            EngineOwnership.legacyAppRunning = { [self] in calls.append("pgrep legacy"); return legacyAlive }
            EngineOwnership.engineSpawnedByUs = { [self] in spawned }
            EngineOwnership.killEngine = { [self] signal in
                calls.append("pkill \(signal)")
                kills += 1
                if kills >= diesAfterKills { engineAlive = false }
            }
            EngineOwnership.logLine = { [self] line in calls.append("log \(line)") }
            EngineOwnership.pause = { [self] _ in calls.append("sleep") }
        }
    }
    /// Between scenarios the seams are left INERT, never restored to the real
    /// pgrep/pkill: a stray call from anywhere in this harness must not be able to
    /// signal a real process on the developer's machine (§61.7 harness rule).
    func disarmSeams() {
        EngineOwnership.engineRunning = { false }
        EngineOwnership.legacyAppRunning = { false }
        EngineOwnership.engineSpawnedByUs = { false }
        EngineOwnership.killEngine = { _ in }
        EngineOwnership.logLine = { _ in }
        EngineOwnership.pause = { _ in }
    }
    /// run `body` against a fresh recorder, then disarm
    func scenario(_ rec: Rec, _ body: () -> Void) -> [String] {
        rec.install()
        body()
        disarmSeams()
        return rec.calls
    }

    // (a) nothing running at launch: one probe, zero kills, zero noise in engine.log
    var calls = scenario(Rec(engineAlive: false)) { EngineOwnership.reclaimAtLaunch() }
    check(calls == ["pgrep engine"],
          "idle launch: one pgrep, no pkill, no log line — got \(calls)")

    // (b) the orphan: breadcrumb → pkill -TERM → poll → "done"
    calls = scenario(Rec(engineAlive: true)) { EngineOwnership.reclaimAtLaunch() }
    check(calls.contains("pkill -TERM"), "orphan launch: the engine is signalled — got \(calls)")
    check(!calls.contains("pkill -KILL"),
          "the launch reclaim never escalates to SIGKILL (§15 contract stop recipe is TERM)")
    check(calls.first == "pgrep engine" && calls[1] == "pgrep legacy",
          "the legacy guard is consulted before anything is killed — got \(calls)")
    check(calls.firstIndex(of: "log reclaim orphan engine: this shell spawned nothing yet")
            .map { $0 < calls.firstIndex(of: "pkill -TERM")! } ?? false,
          "the breadcrumb lands in engine.log BEFORE the kill (a crash mid-reclaim still explains itself)")
    check(calls.last == "log reclaim orphan engine: done",
          "a verified reclaim says so — got \(String(describing: calls.last))")

    // (c) the orphan survives: bounded polling, honest log, never an infinite loop
    let stubborn = Rec(engineAlive: true, diesAfterKills: 99)
    calls = scenario(stubborn) { EngineOwnership.reclaimAtLaunch() }
    check(calls.last == "log reclaim orphan engine: still alive after 2s",
          "an unkillable orphan is reported, not pretended away — got \(String(describing: calls.last))")
    let sleeps = calls.filter { $0 == "sleep" }.count
    check(sleeps <= Int(EngineOwnership.reclaimBudget / EngineOwnership.pollStep) + 1,
          "the poll is bounded by reclaimBudget (\(sleeps) sleeps)")

    // (d) the frozen legacy app is in charge: hands off (§54)
    calls = scenario(Rec(engineAlive: true, legacyAlive: true)) { EngineOwnership.reclaimAtLaunch() }
    check(!calls.contains(where: { $0.hasPrefix("pkill") }),
          "legacy app running → not one signal is sent — got \(calls)")
    check(calls.last == "log reclaim skipped: ZelinAIEngineer is running and owns its own engine",
          "and the skip is written down — got \(String(describing: calls.last))")

    // (e) quit, engine ours and well behaved: TERM only
    calls = scenario(Rec(engineAlive: true, spawned: true)) { EngineOwnership.stopAtExit() }
    check(calls.contains("pkill -TERM") && !calls.contains("pkill -KILL"),
          "a well-behaved engine dies on TERM — got \(calls)")
    check(calls.first == "pgrep legacy",
          "the §54 guard is consulted BEFORE the exit path fires: `pkill -f` cannot tell "
            + "whose engine it is, so the frozen app being in charge means hands off — got \(calls)")
    check(calls[1] == "log stop engine on quit: this shell spawned it and it is still alive",
          "the quit path leaves a breadcrumb too — got \(calls)")

    // (f) quit, engine ignores TERM: exactly one escalation to KILL
    calls = scenario(Rec(engineAlive: true, spawned: true, diesAfterKills: 2)) {
        EngineOwnership.stopAtExit()
    }
    check(calls.filter { $0 == "pkill -TERM" }.count == 1
            && calls.filter { $0 == "pkill -KILL" }.count == 1,
          "TERM then exactly one KILL — got \(calls)")
    check(calls.firstIndex(of: "pkill -TERM")! < calls.firstIndex(of: "pkill -KILL")!,
          "TERM before KILL")

    // (g) quit, engine is somebody else's: not a single probe of it
    calls = scenario(Rec(engineAlive: true, spawned: false)) { EngineOwnership.stopAtExit() }
    check(calls.isEmpty, "we spawned nothing → the quit path does nothing at all — got \(calls)")

    // (h) quit while the §54 frozen app is in charge: our own engine is left running
    //     rather than risking its engine (`pkill -f` hits both) — the leak is §61.8's
    //     second honest boundary, the next launch without it in charge reclaims ours.
    calls = scenario(Rec(engineAlive: true, legacyAlive: true, spawned: true)) {
        EngineOwnership.stopAtExit()
    }
    check(!calls.contains(where: { $0.hasPrefix("pkill") }),
          "legacy app in charge at quit → not one signal, even though the engine is ours — got \(calls)")
    check(calls.last == "log stop engine on quit skipped: ZelinAIEngineer is running — "
            + "pkill -f would take its engine down too",
          "and the skipped stop is written down, not silently dropped — got \(String(describing: calls.last))")

    // (i) THE credential itself (default seam, no recorder installed): ownership is
    //     LIVENESS, not history. `Recording.swift` assigns `engineProcess` once in
    //     startEngineBlocking and never puts it back to nil, so a `!= nil` credential
    //     would survive our engine's death (§61.7 schedule stop / mode=off / a crash)
    //     and make the exit path pkill whatever foreign engine is alive at quit time —
    //     including the frozen app's. A Process that was never launched has the same
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
