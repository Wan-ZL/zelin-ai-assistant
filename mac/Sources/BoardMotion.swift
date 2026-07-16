// BoardMotion.swift — the kanban flight layer (v0.43 手感): consumes the
// BoardMotionEvents the store publishes (BoardDiff.swift) and turns them into
// visible cause-and-effect — a card that changes lanes FLIES there instead of
// teleporting on the next snapshot repaint.
//
// Board window only (KanbanView); the menu-bar popover and iOS deliberately
// stay still. Pure display layer: nothing here writes state, files, or wire
// payloads. All motion is short (≤ ~350 ms springs), never blocks input (the
// overlay is hit-test transparent), and switches itself off with the system
// Reduce Motion setting or the 设置 → 通用 「看板动画」 toggle.

import AppKit
import SwiftUI
import Foundation

// MARK: - policy

enum BoardMotionPolicy {
    /// Master gate, checked at consumption time (not at diff time — the store
    /// keeps its baseline warm regardless, so flipping the toggle mid-session
    /// never animates a stale mega-diff).
    @MainActor static var animationsEnabled: Bool {
        Prefs.boardAnimations
            && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    }

    /// Named coordinate space of the board ZStack — every frame in
    /// BoardFlightController.frames is measured in it.
    static let space = "boardMotionSpace"
}

// MARK: - frame capture (rows + lanes + collapsed strips)

/// Merged dictionary of "who is where" on the board, keyed
/// "row:<lane>:<id>" for rows, "lane:<key>" for expanded columns,
/// "strip:<key>" for collapsed bookend strips. Rows carry their LANE in the
/// key on purpose: during a move the outgoing row (still fading for ~0.2 s)
/// and the incoming row coexist — a bare-id key would let whichever lane
/// renders later in tree order win the merge, sending the flight right back
/// to its source. Only laid-out views report (LazyVStack ⇒ offscreen rows
/// may be missing → the flight falls back to the lane/strip frame).
struct BoardFramesKey: PreferenceKey {
    static let defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect],
                       nextValue: () -> [String: CGRect]) {
        value.merge(nextValue()) { _, new in new }
    }
}

extension View {
    /// Report this view's frame (in the board coordinate space) under `key`.
    func boardMotionFrame(_ key: String) -> some View {
        background(GeometryReader { geo in
            Color.clear.preference(key: BoardFramesKey.self,
                                   value: [key: geo.frame(in: .named(BoardMotionPolicy.space))])
        })
    }
}

// MARK: - flight controller

/// One in-flight proxy: a lightweight card silhouette animated in the overlay.
/// Identity is a fresh UUID (not the card id) so a card that flies twice in
/// quick succession gets a NEW proxy view — same-id reuse would skip the
/// second onAppear and freeze the proxy at its destination.
struct BoardFlight: Identifiable, Equatable {
    enum Kind: Equatable {
        case move          // lane → lane, curved path + settle
        case sink          // off-board removal: shrink+fade toward the lane edge
    }
    let id = UUID()
    let cardID: String
    let kind: Kind
    let title: String
    let accent: Color
    let from: CGRect
    let to: CGRect
    let delay: Double              // 40 ms stagger when multiple
    let pulseStrip: String?        // strip key to pop on landing (collapsed target)
}

/// View-side consumer of the store's BoardMotionEvents. Owns the live frame
/// map (plain var — scroll-driven preference updates must not re-render the
/// overlay), the active proxies, the "row hidden until its proxy lands"
/// bookkeeping, and the collapsed-strip badge pulses.
@MainActor
final class BoardFlightController: ObservableObject {
    /// Continuously refreshed by onPreferenceChange; read only at flight start.
    var frames: [String: CGRect] = [:]
    @Published var flights: [BoardFlight] = []
    /// Move-target ids whose proxy has landed (or never launched) — the row
    /// un-hides the moment its id enters this set. Reset per event.
    @Published var landed: Set<String> = []
    /// Strip keys currently doing their one count-badge pop.
    @Published var pulsing: Set<String> = []
    private var lastSeq = 0

    /// Mark everything up to `seq` as already seen — first render after the
    /// window (re)opens must not animate the backlog of events.
    func baseline(_ seq: Int?) {
        lastSeq = max(lastSeq, seq ?? 0)
    }

    /// True while `id` is the destination of a not-yet-landed flight — its
    /// real row renders at opacity 0 so the proxy is the only "card" visible.
    func isAwaitingLanding(_ id: String, event: BoardMotionEvent?) -> Bool {
        guard BoardMotionPolicy.animationsEnabled,
              let event, event.seq == lastSeq, !event.crossfade,
              !landed.contains(id) else { return false }
        return event.diff.moves.contains { $0.id == id }
    }

    /// Consume one event (idempotent by seq). Phase A runs in the same render
    /// pass as the data change: source frames still hold PRE-change layout.
    /// Phase B (next runloop tick, post-layout) reads destination frames and
    /// launches the proxies.
    func handle(_ event: BoardMotionEvent, store: DashboardStore) {
        guard event.seq > lastSeq else { return }
        lastSeq = event.seq
        landed = []
        guard BoardMotionPolicy.animationsEnabled, !event.crossfade else {
            // crossfade / motion off: the store's own withAnimation fade IS
            // the degradation — nothing to launch, nothing stays hidden.
            landed = Set(event.diff.moves.map { $0.id })
            return
        }
        // Phase A snapshots: last-known frames of everything that leaves.
        var sourceFrames: [String: CGRect] = [:]
        for m in event.diff.moves {
            sourceFrames[m.id] = frames["row:\(m.fromLane):\(m.id)"]
        }
        for r in event.diff.removals {
            sourceFrames[r.id] = frames["row:\(r.lane):\(r.id)"]
        }
        let titles = titlesFor(event: event, store: store)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            guard let self, self.lastSeq == event.seq else { return }
            self.launch(event: event, sourceFrames: sourceFrames, titles: titles)
        }
    }

    /// Resolve display titles while the store still knows the card (removals'
    /// ids may already be pruned from every lane by the time flights land).
    private func titlesFor(event: BoardMotionEvent,
                           store: DashboardStore) -> [String: String] {
        var t: [String: String] = [:]
        for m in event.diff.moves { t[m.id] = store.cardTitle(m.id) }
        for r in event.diff.removals { t[r.id] = store.cardTitle(r.id) }
        return t
    }

    private func launch(event: BoardMotionEvent,
                        sourceFrames: [String: CGRect],
                        titles: [String: String]) {
        var newFlights: [BoardFlight] = []
        var stagger = 0
        for move in event.diff.moves {
            guard let from = sourceFrames[move.id] else {
                landed.insert(move.id)   // source never laid out → no flight
                continue
            }
            // Destination priority: the real row (post-layout frame) → the
            // collapsed strip (pop its badge on landing) → the lane column.
            var pulse: String?
            var to = frames["row:\(move.toLane):\(move.id)"]
            if to == nil, let strip = frames["strip:\(move.toLane)"] {
                // land on the strip's TOP (badge area), not its mid-height
                to = CGRect(x: strip.minX, y: strip.minY,
                            width: strip.width, height: 72)
                pulse = move.toLane
            }
            if to == nil { to = frames["lane:\(move.toLane)"] }
            guard let dest = to else {
                landed.insert(move.id)   // lane not visible at all → skip
                continue
            }
            newFlights.append(BoardFlight(
                cardID: move.id, kind: .move,
                title: titles[move.id] ?? move.id,
                accent: Self.laneAccent(move.toLane),
                from: from, to: dest,
                delay: Double(stagger) * 0.04, pulseStrip: pulse))
            stagger += 1
        }
        for removal in event.diff.removals {
            guard let from = sourceFrames[removal.id] else { continue }
            // shrink+fade drifting toward the lane's bottom edge — subtle,
            // ~250 ms; the row's own store-driven fade runs underneath.
            let to = from.offsetBy(dx: 0, dy: 26)
            newFlights.append(BoardFlight(
                cardID: removal.id, kind: .sink,
                title: titles[removal.id] ?? removal.id,
                accent: Self.laneAccent(removal.lane),
                from: from, to: to,
                delay: Double(stagger) * 0.04, pulseStrip: nil))
            stagger += 1
        }
        guard !newFlights.isEmpty else { return }
        // a newer event supersedes any still-flying proxy of the same card
        let cardIDs = Set(newFlights.map { $0.cardID })
        flights.removeAll { cardIDs.contains($0.cardID) }
        flights.append(contentsOf: newFlights)
        for flight in newFlights {
            let total = flight.delay + (flight.kind == .move ? 0.42 : 0.30)
            DispatchQueue.main.asyncAfter(deadline: .now() + total) { [weak self] in
                self?.finish(flight)
            }
        }
    }

    private func finish(_ flight: BoardFlight) {
        flights.removeAll { $0.id == flight.id }
        if flight.kind == .move {
            withAnimation(.easeOut(duration: 0.15)) { _ = landed.insert(flight.cardID) }
        }
        if let strip = flight.pulseStrip { pulse(strip) }
    }

    /// One count-badge pop: 1.0 → 1.25 → 1.0 (single, never repeats).
    func pulse(_ stripKey: String) {
        withAnimation(.spring(response: 0.18, dampingFraction: 0.5)) {
            _ = pulsing.insert(stripKey)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) { [weak self] in
            withAnimation(.spring(response: 0.22, dampingFraction: 0.7)) {
                _ = self?.pulsing.remove(stripKey)
            }
        }
    }

    /// Proxy tint per DESTINATION lane — mirrors the accents the real rows
    /// use (TaskRow lane accents / ReviewRow teal); neutral lanes stay grey.
    static func laneAccent(_ lane: String) -> Color {
        switch lane {
        case "running": return .blue
        case "review": return .teal
        case "completed": return .green
        default: return .secondary
        }
    }
}

// MARK: - overlay + proxy views

/// Hit-test-transparent layer above the board HStack where proxies fly.
struct BoardFlightOverlay: View {
    @ObservedObject var controller: BoardFlightController

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
            ForEach(controller.flights) { flight in
                BoardFlightProxyView(flight: flight)
            }
        }
        .clipped()
        .allowsHitTesting(false)
    }
}

/// The animated silhouette: rounded rect + the card's title line, tinted with
/// the destination lane's accent. One spring drives progress 0→1; the path
/// modifier below turns progress into position/scale/shadow.
private struct BoardFlightProxyView: View {
    let flight: BoardFlight
    @State private var progress: CGFloat = 0

    var body: some View {
        let width = min(max(flight.from.width - 20, 120), 340)
        HStack(spacing: 6) {
            Circle()
                .fill(flight.accent.opacity(0.8))
                .frame(width: 6, height: 6)
            Text(flight.title)
                .font(.system(size: 12, weight: .medium))
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .frame(width: width, height: 34, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8)
            .stroke(flight.accent.opacity(0.45), lineWidth: 1))
        .modifier(BoardFlightPath(progress: progress, flight: flight))
        .onAppear {
            switch flight.kind {
            case .move:
                // lift → curved flight → settle; the spring's light
                // underdamping past t=1 IS the landing bounce.
                withAnimation(.spring(response: 0.34, dampingFraction: 0.72)
                    .delay(flight.delay)) { progress = 1 }
            case .sink:
                withAnimation(.easeIn(duration: 0.25).delay(flight.delay)) {
                    progress = 1
                }
            }
        }
    }
}

/// Animatable progress → position along a quadratic Bézier (arched upward),
/// mid-flight lift (scale + shadow), and the fade-out near landing. For
/// .sink: shrink+fade along the straight drift instead.
private struct BoardFlightPath: ViewModifier, Animatable {
    var progress: CGFloat
    let flight: BoardFlight

    var animatableData: CGFloat {
        get { progress }
        set { progress = newValue }
    }

    func body(content: Content) -> some View {
        let t = progress
        let clamped = min(max(t, 0), 1)
        switch flight.kind {
        case .move:
            // arc height grows gently with distance; the spring may push t a
            // touch past 1 — the unclamped Bézier extends smoothly, so the
            // proxy overshoots its landing spot a few points and settles.
            let lift = CGFloat(1.0 + 0.03 * sin(.pi * Double(clamped)))
            content
                .scaleEffect(lift)
                .shadow(color: .black.opacity(0.22 * Double(sin(.pi * Double(clamped)))),
                        radius: 8, y: 4)
                .opacity(t < 0.82 ? 1 : max(0, 1 - Double((t - 0.82) / 0.18)))
                .position(bezier(t))
        case .sink:
            content
                .scaleEffect(1 - 0.25 * clamped)
                .opacity(Double(1 - clamped))
                .position(linear(t))
        }
    }

    private func bezier(_ t: CGFloat) -> CGPoint {
        let p0 = CGPoint(x: flight.from.midX, y: flight.from.midY)
        let p2 = CGPoint(x: flight.to.midX, y: flight.to.midY)
        let dist = hypot(p2.x - p0.x, p2.y - p0.y)
        let control = CGPoint(x: (p0.x + p2.x) / 2,
                              y: min(p0.y, p2.y) - 24 - dist * 0.08)
        let u = 1 - t
        return CGPoint(
            x: u * u * p0.x + 2 * u * t * control.x + t * t * p2.x,
            y: u * u * p0.y + 2 * u * t * control.y + t * t * p2.y)
    }

    private func linear(_ t: CGFloat) -> CGPoint {
        CGPoint(x: flight.from.midX + (flight.to.midX - flight.from.midX) * t,
                y: flight.from.midY + (flight.to.midY - flight.from.midY) * t)
    }
}

// MARK: - per-row modifier (frame report + landing gate + deal-in + hover)

extension View {
    /// The one modifier KanbanView hangs on every board row: reports the
    /// row's frame (keyed by lane — see BoardFramesKey), hides it while its
    /// move-proxy is still flying, deals it in when it's a fresh insert, and
    /// adds the hover lift. `id` must be the card id the differ sees (echo
    /// rows pass their sourceID); `lane` the row's board lane key.
    func boardCardMotion(_ id: String, lane: String, store: DashboardStore,
                         flights: BoardFlightController) -> some View {
        modifier(BoardCardMotionModifier(id: id, lane: lane, store: store,
                                         flights: flights))
    }
}

private struct BoardCardMotionModifier: ViewModifier {
    let id: String
    let lane: String
    @ObservedObject var store: DashboardStore
    @ObservedObject var flights: BoardFlightController
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .boardMotionFrame("row:\(lane):\(id)")
            .opacity(flights.isAwaitingLanding(id, event: store.boardMotion) ? 0 : 1)
            .transition(.asymmetric(insertion: insertion, removal: .opacity))
            // micro-juice: hover lift (none existed — CardSurface only tints).
            // 2 pt raise + soft shadow, 120 ms; skipped under Reduce Motion /
            // toggle-off like every other motion here.
            .offset(y: hovering ? -1 : 0)
            .shadow(color: .black.opacity(hovering ? 0.14 : 0),
                    radius: hovering ? 4 : 0, y: hovering ? 2 : 0)
            .onHover { h in
                // un-hover must always land, even if the toggle flipped off
                // mid-hover — otherwise the lift freezes on.
                guard !h || BoardMotionPolicy.animationsEnabled else { return }
                withAnimation(.easeOut(duration: 0.12)) { hovering = h }
            }
    }

    /// Deal-in for rows the CURRENT motion event marked as inserts: slide
    /// from the lane top with a slight rotation settle, 40 ms staggered. The
    /// store clears the event ~0.8 s after publishing, so a row (re)inserted
    /// by anything else — strip expand/collapse, search filter, scrolling —
    /// falls through to the default opacity fade.
    private var insertion: AnyTransition {
        guard BoardMotionPolicy.animationsEnabled,
              let event = store.boardMotion, !event.crossfade,
              let index = event.diff.inserts.firstIndex(where: { $0.id == id })
        else { return .opacity }
        return .modifier(
            active: DealInModifier(active: true),
            identity: DealInModifier(active: false)
        ).animation(.spring(response: 0.32, dampingFraction: 0.78)
            .delay(Double(index) * 0.04))
    }
}

/// The deal-in shape: starts 14 pt above its slot, faintly rotated, invisible.
private struct DealInModifier: ViewModifier {
    let active: Bool

    func body(content: Content) -> some View {
        content
            .offset(y: active ? -14 : 0)
            .rotationEffect(.degrees(active ? -1.2 : 0), anchor: .top)
            .opacity(active ? 0 : 1)
    }
}
