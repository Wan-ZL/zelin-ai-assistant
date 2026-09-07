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
}

run()
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
