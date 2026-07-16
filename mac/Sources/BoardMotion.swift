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

/// One reported frame plus the motion-event generation the reporting view
/// was born under. During an A→B→A the outgoing row at A (still fading for
/// ~0.2 s) and the fresh row at A coexist under the SAME "row:A:<id>" key —
/// the generation lets the merge deterministically keep the newer view
/// instead of whichever lands later in tree order.
struct BoardFrameEntry: Equatable {
    let rect: CGRect
    let gen: Int
}

/// Merged dictionary of "who is where" on the board, keyed
/// "row:<lane>:<id>" for rows, "lane:<key>" for expanded columns,
/// "strip:<key>" for collapsed bookend strips, "board" for the whole
/// visible board rect. Rows carry their LANE in the key on purpose: during
/// a move the outgoing row and the incoming row coexist — a bare-id key
/// would let whichever lane renders later in tree order win the merge,
/// sending the flight right back to its source. Only laid-out views report
/// (LazyVStack ⇒ offscreen rows may be missing → the flight falls back to
/// the lane/strip frame; rows scrolled out of a lane's viewport DO still
/// report, which is why the planner clamps to the visible rect).
struct BoardFramesKey: PreferenceKey {
    static let defaultValue: [String: BoardFrameEntry] = [:]
    static func reduce(value: inout [String: BoardFrameEntry],
                       nextValue: () -> [String: BoardFrameEntry]) {
        value.merge(nextValue()) { old, new in new.gen >= old.gen ? new : old }
    }
}

extension View {
    /// Report this view's frame (in the board coordinate space) under `key`.
    /// `generation` disambiguates same-key duplicates (see BoardFrameEntry);
    /// singleton keys (lanes/strips/board) keep the default 0.
    /// Toggle-off really costs nothing: the GeometryReader + preference
    /// publish only exist while 看板动画 is on (gated on the PREF alone —
    /// UserDefaults is cheap enough for this per-row hot path, the
    /// NSWorkspace reduce-motion check is not; reduce-motion is enforced at
    /// the cold paths instead: event publish + consumption).
    func boardMotionFrame(_ key: String, generation: Int = 0) -> some View {
        background {
            if Prefs.boardAnimations {
                GeometryReader { geo in
                    Color.clear.preference(
                        key: BoardFramesKey.self,
                        value: [key: BoardFrameEntry(
                            rect: geo.frame(in: .named(BoardMotionPolicy.space)),
                            gen: generation)])
                }
            }
        }
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
    var frames: [String: BoardFrameEntry] = [:]
    @Published var flights: [BoardFlight] = []
    /// Move-target ids whose proxy is queued or airborne — the row renders at
    /// opacity 0 until its OWN proxy completes (per-proxy, so a second event
    /// arriving mid-flight can neither orphan nor prematurely reveal it).
    @Published var pendingLanding: Set<String> = []
    /// Move-target ids of the CURRENT event whose proxy already landed or
    /// never launched (planner dropped it / crossfade) — reset per event;
    /// bridges the gap between the event render and the +0.05 s launch.
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
        guard BoardMotionPolicy.animationsEnabled else { return false }
        if pendingLanding.contains(id) { return true }
        guard let event, event.seq == lastSeq, !event.crossfade,
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
        // Every proxy of a card this event touches is superseded — an A→B→A
        // replaces the first flight, a removal chased by a re-insert cancels
        // the sink. Their completions become no-ops (finish() guard).
        let touched = BoardFlightPlanner.touchedIDs(event.diff)
        flights.removeAll { touched.contains($0.cardID) }
        pendingLanding.subtract(touched)
        landed = []
        guard BoardMotionPolicy.animationsEnabled, !event.crossfade else {
            // crossfade / motion off: the store's own withAnimation fade IS
            // the degradation — nothing to launch, nothing stays hidden.
            landed = Set(event.diff.moves.map { $0.id })
            return
        }
        // Phase A snapshots: last-known frames of everything that leaves.
        // A removal only gets a source frame (⇒ a sink) when a REAL card
        // still stands behind the id: a trashed card still sits in
        // dashboard.trash (title resolves), a force-merged secondary is
        // covered by the 合并中 badge machinery. The completed/archived
        // lists cap at their newest ~50, so a tail id evicted by a new
        // arrival diffs as a removal too — that is bookkeeping, not a user
        // action, and animating it would be a lie. (Suggestion-merge
        // consolidation clears its pending marker in the same snapshot, so
        // those absorptions stay silent as well — noted in the CHANGELOG.)
        var sourceFrames: [String: CGRect] = [:]
        for m in event.diff.moves {
            sourceFrames[m.id] = frames["row:\(m.fromLane):\(m.id)"]?.rect
        }
        for r in event.diff.removals
        where store.cardTitle(r.id) != r.id || store.isMergeForcing(r.id) {
            sourceFrames[r.id] = frames["row:\(r.lane):\(r.id)"]?.rect
        }
        let titles = titlesFor(event: event, store: store)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            guard let self, self.lastSeq == event.seq else { return }
            self.launch(event: event, sourceFrames: sourceFrames, titles: titles)
        }
    }

    /// Resolve display titles while the store still knows the card (removals'
    /// ids may already be pruned from every lane by the time flights land).
    /// An id the store can't resolve renders an honest generic label — never
    /// the raw R-/capture- id.
    private func titlesFor(event: BoardMotionEvent,
                           store: DashboardStore) -> [String: String] {
        var t: [String: String] = [:]
        for id in event.diff.moves.map(\.id) + event.diff.removals.map(\.id) {
            let title = store.cardTitle(id)   // falls back to the id itself
            t[id] = title == id ? L("卡片", "Card") : title
        }
        return t
    }

    private func launch(event: BoardMotionEvent,
                        sourceFrames: [String: CGRect],
                        titles: [String: String]) {
        // Endpoint validation + strip/lane fallback + viewport clamp live in
        // the PURE planner (harness-pinned): nil / zero-size / off-screen
        // endpoints are dropped — those cards just appear at the destination.
        let plans = BoardFlightPlanner.plans(
            diff: event.diff,
            sources: sourceFrames,
            frames: frames.mapValues { $0.rect },
            visible: frames["board"]?.rect ?? .null)
        let flying = Set(plans.filter { $0.kind == .move }.map { $0.id })
        for move in event.diff.moves where !flying.contains(move.id) {
            landed.insert(move.id)   // dropped by the planner → show the row
        }
        guard !plans.isEmpty else { return }
        let newFlights = plans.enumerated().map { index, plan in
            BoardFlight(cardID: plan.id, kind: plan.kind == .move ? .move : .sink,
                        title: titles[plan.id] ?? plan.id,
                        accent: Self.laneAccent(plan.toLane),
                        from: plan.from, to: plan.to,
                        delay: Double(index) * 0.04,
                        pulseStrip: plan.pulseStrip)
        }
        pendingLanding.formUnion(flying)
        flights.append(contentsOf: newFlights)
        // no shared timer: each proxy reports its OWN animation completion
        // (BoardFlightProxyView) and reconciles independently via finish().
    }

    /// Per-proxy completion (from the proxy view's animation-done callback,
    /// with a safety-net timer behind it). Idempotent, and a no-op for
    /// superseded proxies — only a flight still on the board may un-hide its
    /// row or pop a strip badge.
    func finish(_ flight: BoardFlight) {
        guard flights.contains(where: { $0.id == flight.id }) else { return }
        flights.removeAll { $0.id == flight.id }
        if flight.kind == .move {
            withAnimation(.easeOut(duration: 0.15)) {
                pendingLanding.remove(flight.cardID)
                landed.insert(flight.cardID)
            }
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
                BoardFlightProxyView(flight: flight) {
                    controller.finish(flight)
                }
            }
        }
        .clipped()
        .allowsHitTesting(false)
    }
}

/// The animated silhouette: rounded rect + the card's title line, tinted with
/// the destination lane's accent. One spring drives progress 0→1; the path
/// modifier below turns progress into position/scale/shadow. Completion is
/// per-proxy — each animation reports its OWN done (no shared timer), with a
/// safety-net timer behind it; finish() is idempotent either way.
private struct BoardFlightProxyView: View {
    let flight: BoardFlight
    let onDone: () -> Void
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
            let animation: Animation = switch flight.kind {
            case .move:
                // lift → curved flight → settle; the spring's light
                // underdamping past t=1 IS the landing bounce.
                .spring(response: 0.34, dampingFraction: 0.72).delay(flight.delay)
            case .sink:
                .easeIn(duration: 0.25).delay(flight.delay)
            }
            withAnimation(animation, completionCriteria: .logicallyComplete) {
                progress = 1
            } completion: { onDone() }
            // safety net: a proxy whose animation never completes (view torn
            // down mid-transaction) must not sit orphaned on the overlay.
            DispatchQueue.main.asyncAfter(deadline: .now() + flight.delay + 1.0) {
                onDone()
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
    // the motion-event generation this row was born under (0 = pre-motion):
    // lets the frame merge prefer the FRESH row over a same-key outgoing one
    // still fading through its removal transition (A→B→A within a window).
    @State private var bornGen = 0

    func body(content: Content) -> some View {
        content
            .boardMotionFrame("row:\(lane):\(id)", generation: bornGen)
            .onAppear { bornGen = store.boardMotion?.seq ?? 0 }
            .opacity(flights.isAwaitingLanding(id, event: store.boardMotion) ? 0 : 1)
            .transition(.asymmetric(insertion: insertion, removal: .opacity))
            // micro-juice: hover lift (none existed — CardSurface only tints).
            // 1 pt raise + soft shadow, 120 ms; skipped under Reduce Motion /
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
