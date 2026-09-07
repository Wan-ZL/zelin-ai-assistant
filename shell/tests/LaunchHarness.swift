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
}

run()
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
