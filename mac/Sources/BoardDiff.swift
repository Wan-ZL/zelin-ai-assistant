// BoardDiff.swift — pure snapshot differ behind the kanban flight layer
// (v0.43 手感). Foundation-only BY CONTRACT: ios/tests/boarddiff/run.sh
// compiles this file standalone with plain swiftc (CaptionCore.swift 先例),
// so no AppKit/SwiftUI/Combine may ever be imported here.
//
// Input: the per-lane ORDERED id lists the board renders, captured before and
// after a change (a dashboard.json snapshot landing, or a local optimistic
// mutation — echoes ride under their sourceID so a button press diffs as the
// move it represents). Output: which ids moved lanes, appeared, or left the
// board entirely — the ONLY vocabulary the flight layer animates. Intra-lane
// reorders and lane-array reorders are deliberately invisible to this differ:
// sorting is not causality.

import Foundation

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
