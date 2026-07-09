// Zelin's AI Assistant — menu-bar app (no SPM; sources split across mac/Sources/*.swift)
//
// Compile (build.sh compiles ALL of mac/Sources/*.swift in one module):
//   swiftc -O Sources/*.swift -o ZelinAIEngineer \
//     -framework AppKit -framework SwiftUI -framework Foundation -framework Carbon
//
// Runtime: reads  AIASSISTANT_HOME/state/dashboard.json  (read-only, every 5s)
//          writes AIASSISTANT_HOME/state/inbox/<uuid>.json (approve/reject/comment)
//          writes AIASSISTANT_HOME/state/settings_overrides.json (main window §15)
// AIASSISTANT_HOME defaults to ~/Projects/zelin-ai-assistant (env var name kept for compat, §12).
//
// The app NEVER calls claude, never touches the registry, never holds secrets.
//
// main.swift holds ONLY the bootstrap: swiftc allows top-level statements in
// main.swift alone; everything else lives in the sibling module files.

import AppKit

// MARK: - Bootstrap (top-level code, main-actor isolated)

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
