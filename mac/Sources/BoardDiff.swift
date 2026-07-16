// BoardDiff.swift — pure snapshot differ + flight planner behind the kanban
// flight layer (v0.43 手感). Foundation + CoreGraphics (geometry types) ONLY,
// by contract: ios/tests/boarddiff/run.sh compiles this file standalone with
// plain swiftc (CaptionCore.swift's Foundation+Compression 先例), so no
// AppKit/SwiftUI/Combine may ever be imported here.
//
// Input: the per-lane ORDERED id lists the board renders, captured before and
// after a change (a dashboard.json snapshot landing, or a local optimistic
// mutation — echoes ride under their sourceID so a button press diffs as the
// move it represents). Output: which ids moved lanes, appeared, or left the
// board entirely — the ONLY vocabulary the flight layer animates. Intra-lane
// reorders and lane-array reorders are deliberately invisible to this differ:
// sorting is not causality.

import Foundation
import CoreGraphics

/// One board lane's rendered ids, in render order. `lane` is a stable key
/// ("debt"/"approval"/"running"/"review"/"completed"/"archived") — plain
/// String rather than ListKind so this file stays compilable standalone.
struct BoardLaneList: Equatable {
    let lane: String
    let ids: [String]

    init(lane: String, ids: [String]) {
        self.lane = lane
        self.ids = ids
    }
}

/// What changed between two board snapshots. Ordering is deterministic:
/// moves/inserts follow CURRENT lane order (destination reading order, which
/// the flight layer uses for its 40 ms stagger), removals follow PREVIOUS
/// lane order. An id listed in several lanes of one snapshot counts once —
/// first lane in the given order wins.
struct BoardDiffResult: Equatable {
    struct Move: Equatable {
        let id: String
        let fromLane: String
        let toLane: String
    }

    struct Insert: Equatable {
        let id: String
        let lane: String
    }

    struct Removal: Equatable {
        let id: String
        let lane: String   // the lane the id was last seen in
    }

    let moves: [Move]
    let inserts: [Insert]
    let removals: [Removal]

    var isEmpty: Bool { moves.isEmpty && inserts.isEmpty && removals.isEmpty }
    var changeCount: Int { moves.count + inserts.count + removals.count }

    static let none = BoardDiffResult(moves: [], inserts: [], removals: [])
}

enum BoardDiff {
    /// Above this many changes in ONE snapshot the flight layer degrades to a
    /// plain crossfade — 20 simultaneous flights read as noise, not causality.
    static let flightCap = 6

    /// nil `previous` = first snapshot (launch / file appearing) — nothing to
    /// diff against, so nothing animates. 契约 with the caller: it keeps the
    /// returned-from snapshot as the next call's `previous`.
    static func compute(previous: [BoardLaneList]?,
                        current: [BoardLaneList]) -> BoardDiffResult {
        guard let previous else { return .none }
        let prevLane = laneByID(previous)
        let curLane = laneByID(current)
        var moves: [BoardDiffResult.Move] = []
        var inserts: [BoardDiffResult.Insert] = []
        for lane in current {
            // `curLane[id] == lane.lane` skips an id's duplicate occurrences
            // (first lane in snapshot order won during map building).
            for id in lane.ids where curLane[id] == lane.lane {
                if let from = prevLane[id] {
                    if from != lane.lane {
                        moves.append(.init(id: id, fromLane: from, toLane: lane.lane))
                    }
                } else {
                    inserts.append(.init(id: id, lane: lane.lane))
                }
            }
        }
        var removals: [BoardDiffResult.Removal] = []
        for lane in previous {
            for id in lane.ids where prevLane[id] == lane.lane && curLane[id] == nil {
                removals.append(.init(id: id, lane: lane.lane))
            }
        }
        return BoardDiffResult(moves: moves, inserts: inserts, removals: removals)
    }

    /// id → lane, first occurrence (in the given lane order) winning.
    private static func laneByID(_ lanes: [BoardLaneList]) -> [String: String] {
        var map: [String: String] = [:]
        for lane in lanes {
            for id in lane.ids where map[id] == nil { map[id] = lane.lane }
        }
        return map
    }
}

/// One consumable motion event the store publishes alongside the state change
/// that caused it (same transaction, so row transitions see it in the same
/// render pass). `seq` makes each event one-shot for the view; `crossfade`
/// bakes the >flightCap policy in here where the harness can test it.
struct BoardMotionEvent: Equatable {
    let seq: Int
    let diff: BoardDiffResult
    let crossfade: Bool

    init(seq: Int, diff: BoardDiffResult) {
        self.seq = seq
        self.diff = diff
        self.crossfade = diff.changeCount > BoardDiff.flightCap
    }
}

// MARK: - flight planning (pure — geometry policy the harness can pin)

/// One planned proxy animation, fully resolved: both endpoints validated
/// against the frames the board actually reported. Anything the planner
/// drops simply appears at its destination — never a flight to a frame we
/// don't have (a nil / zero-size / off-viewport endpoint reads as a glitch,
/// not as juice).
struct BoardFlightPlan: Equatable {
    enum Kind: Equatable {
        case move          // lane → lane, curved path + settle
        case sink          // off-board removal: shrink+fade toward the lane edge
    }
    let id: String
    let kind: Kind
    let toLane: String         // accent derivation (sinks keep their last lane)
    let from: CGRect
    let to: CGRect
    let pulseStrip: String?    // strip key to pop on landing (collapsed target)
}

/// Turns a diff + the board's reported frames into the flights that may
/// actually run. Pure and deterministic (plan order = diff order: moves
/// first, then removals) so ios/tests/boarddiff pins the drop rules.
enum BoardFlightPlanner {
    /// Every card id an event touches — the controller cancels any still-
    /// flying proxy of these on event arrival (an A→B→A within one window
    /// must replace the first proxy, and a removal chased by a re-insert
    /// must cancel the sink; the superseded proxy's completion is a no-op).
    static func touchedIDs(_ diff: BoardDiffResult) -> Set<String> {
        var ids = Set(diff.moves.map { $0.id })
        ids.formUnion(diff.inserts.map { $0.id })
        ids.formUnion(diff.removals.map { $0.id })
        return ids
    }

    /// - sources: pre-change frames per card id (captured before layout ran)
    /// - frames:  post-layout lookup — "row:<lane>:<id>", "strip:<lane>",
    ///            "lane:<lane>"
    /// - visible: the board's on-screen rect in the same space; endpoints
    ///            whose midpoint lies outside are dropped — rows scrolled out
    ///            of their lane's viewport report far-off frames (LazyVStack
    ///            keeps them alive), and a flight from off-screen crosses
    ///            headers. An off-screen card just appears at its destination.
    static func plans(diff: BoardDiffResult,
                      sources: [String: CGRect],
                      frames: [String: CGRect],
                      visible: CGRect) -> [BoardFlightPlan] {
        var plans: [BoardFlightPlan] = []
        for move in diff.moves {
            guard let from = sources[move.id], usable(from, in: visible) else { continue }
            var pulse: String?
            // destination priority: the real row → the collapsed strip's top
            // (badge area; pop it on landing) → the lane column's top region.
            var to = frames["row:\(move.toLane):\(move.id)"]
            if !isUsable(to, in: visible), let strip = frames["strip:\(move.toLane)"] {
                to = CGRect(x: strip.minX, y: strip.minY,
                            width: strip.width, height: 72)
                pulse = move.toLane
            }
            if !isUsable(to, in: visible), let lane = frames["lane:\(move.toLane)"] {
                to = CGRect(x: lane.minX, y: lane.minY,
                            width: lane.width, height: 120)
                pulse = nil
            }
            guard let dest = to, usable(dest, in: visible) else { continue }
            plans.append(BoardFlightPlan(id: move.id, kind: .move,
                                         toLane: move.toLane,
                                         from: from, to: dest, pulseStrip: pulse))
        }
        for removal in diff.removals {
            guard let from = sources[removal.id], usable(from, in: visible) else { continue }
            plans.append(BoardFlightPlan(id: removal.id, kind: .sink,
                                         toLane: removal.lane,
                                         from: from,
                                         to: from.offsetBy(dx: 0, dy: 26),
                                         pulseStrip: nil))
        }
        return plans
    }

    /// Non-degenerate (a zero-size frame is a view that never really laid
    /// out) and on screen (midpoint inside the visible rect, small tolerance).
    private static func usable(_ rect: CGRect, in visible: CGRect) -> Bool {
        guard rect.width >= 2, rect.height >= 2 else { return false }
        return visible.insetBy(dx: -4, dy: -4)
            .contains(CGPoint(x: rect.midX, y: rect.midY))
    }

    private static func isUsable(_ rect: CGRect?, in visible: CGRect) -> Bool {
        rect.map { usable($0, in: visible) } ?? false
    }
}
