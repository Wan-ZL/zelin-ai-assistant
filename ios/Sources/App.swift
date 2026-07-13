// App.swift — the iOS companion app entry point + root routing (QR-only v2).
//
// Routing (no account — the QR is the credential):
//   sync off               → OnboardingView (consent/disclosure only)
//   sync on, no channel     → PairingView    (scan a Mac's QR)
//   sync on, ≥1 channel     → BoardView       (the 5-lane board)
//
// Free-tier reality (plan §6.4): while foregrounded we poll + subscribe and
// raise LOCAL notifications; when closed there is no push. The board is a
// convenience mirror — the Mac remains the authoritative alert channel.

import SwiftUI

@main
struct ZelinCompanionApp: App {
    @StateObject private var state = AppState()
    @StateObject private var lang = LanguageStore.shared

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .environmentObject(lang)
                // re-render the whole tree when the UI language flips
                .id(lang.lang)
        }
    }
}

struct RootView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if !state.syncEnabled {
                OnboardingView()
            } else if state.channels.isEmpty {
                NavigationStack { PairingView() }
            } else {
                BoardView()
            }
        }
        .task { await state.refreshEverything() }
        .onChange(of: scenePhase) { phase in
            // Return-to-foreground catch-up (plan §5.2): backgrounded websockets
            // are suspended, so reconnect + GET on activate.
            if phase == .active { Task { await state.refreshEverything() } }
        }
    }
}
