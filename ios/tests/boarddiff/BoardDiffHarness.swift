// BoardDiffHarness.swift — behavior tests for mac/Sources/BoardDiff.swift:
// the pure snapshot differ behind the v0.43 kanban flight layer. Compiled by
// run.sh into a plain macOS CLI tool — no Xcode, no XCTest. Exits non-zero on
// any failure. (Same harness style as ios/tests/contract and
// ios/tests/captions.)

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

func lanes(_ pairs: [(String, [String])]) -> [BoardLaneList] {
    pairs.map { BoardLaneList(lane: $0.0, ids: $0.1) }
}

// ---- 1. first load: nil previous → nothing animates ----
print("[1] first load (previous == nil):")
let firstBoard = lanes([("approval", ["R-1", "R-2"]), ("running", ["R-3"])])
let first = BoardDiff.compute(previous: nil, current: firstBoard)
check(first.isEmpty, "no motion on first snapshot")
check(first == .none, "returns the .none constant shape")

// ---- 2. unchanged snapshot ----
print("[2] unchanged snapshot:")
let same = BoardDiff.compute(previous: firstBoard, current: firstBoard)
check(same.isEmpty, "identical lanes → empty diff")

// ---- 3. single lane move (approve: 提案 → 运行中) ----
print("[3] single move:")
let afterApprove = lanes([("approval", ["R-2"]), ("running", ["R-1", "R-3"])])
let move = BoardDiff.compute(previous: firstBoard, current: afterApprove)
check(move.moves == [.init(id: "R-1", fromLane: "approval", toLane: "running")],
      "R-1 moved approval→running", "got \(move.moves)")
check(move.inserts.isEmpty && move.removals.isEmpty, "nothing else changed")
check(move.changeCount == 1, "changeCount 1")

// ---- 4. multi-move in one snapshot, deterministic destination order ----
print("[4] multi-move in one snapshot:")
let prev4 = lanes([("approval", ["A", "B"]), ("running", ["C"]), ("review", [])])
let cur4 = lanes([("approval", []), ("running", ["A"]), ("review", ["C", "B"])])
let multi = BoardDiff.compute(previous: prev4, current: cur4)
check(multi.moves == [
    .init(id: "A", fromLane: "approval", toLane: "running"),
    .init(id: "C", fromLane: "running", toLane: "review"),
    .init(id: "B", fromLane: "approval", toLane: "review"),
], "3 moves in current-lane reading order", "got \(multi.moves)")

// ---- 5. a card that moved WHILE lanes/ids reordered ----
print("[5] move + lane reorder + intra-lane reorder:")
let prev5 = lanes([("approval", ["A", "B", "C"]), ("running", ["D"])])
// lanes array order flipped, approval internally re-sorted, B moved out
let cur5 = lanes([("running", ["B", "D"]), ("approval", ["C", "A"])])
let re = BoardDiff.compute(previous: prev5, current: cur5)
check(re.moves == [.init(id: "B", fromLane: "approval", toLane: "running")],
      "only the real move survives reordering noise", "got \(re.moves)")
check(re.inserts.isEmpty && re.removals.isEmpty, "reorders alone are invisible")

// ---- 6. inserts keep current-lane order (stagger order) ----
print("[6] inserts:")
let cur6 = lanes([("approval", ["N-1", "R-1", "N-2", "R-2"]), ("running", ["N-3", "R-3"])])
let ins = BoardDiff.compute(previous: firstBoard, current: cur6)
check(ins.inserts == [
    .init(id: "N-1", lane: "approval"),
    .init(id: "N-2", lane: "approval"),
    .init(id: "N-3", lane: "running"),
], "3 inserts in reading order", "got \(ins.inserts)")
check(ins.moves.isEmpty && ins.removals.isEmpty, "existing ids untouched")

// ---- 7. removals (trash / merged: id leaves every lane) ----
print("[7] removals:")
let cur7 = lanes([("approval", ["R-2"]), ("running", [])])
let rm = BoardDiff.compute(previous: firstBoard, current: cur7)
check(rm.removals == [
    .init(id: "R-1", lane: "approval"),
    .init(id: "R-3", lane: "running"),
], "2 removals with their last-seen lane", "got \(rm.removals)")

// ---- 8. duplicate id across lanes: first lane in snapshot order wins ----
print("[8] duplicate id in one snapshot:")
let dupPrev = lanes([("approval", ["X"]), ("running", [])])
let dupCur = lanes([("approval", ["X"]), ("running", ["X"])])
let dup = BoardDiff.compute(previous: dupPrev, current: dupCur)
check(dup.isEmpty, "dupe occurrence doesn't fabricate a move/insert",
      "got \(dup)")

// ---- 9. mixed move+insert+removal in one snapshot ----
print("[9] mixed diff:")
let prev9 = lanes([("approval", ["A", "B"]), ("running", ["C"])])
let cur9 = lanes([("approval", ["NEW"]), ("running", ["A"])])
let mix = BoardDiff.compute(previous: prev9, current: cur9)
check(mix.moves == [.init(id: "A", fromLane: "approval", toLane: "running")], "move")
check(mix.inserts == [.init(id: "NEW", lane: "approval")], "insert")
check(mix.removals == [
    .init(id: "B", lane: "approval"),
    .init(id: "C", lane: "running"),
], "removals")
check(mix.changeCount == 4, "changeCount sums all three kinds")

// ---- 10. crossfade policy: >flightCap changes in one event ----
print("[10] BoardMotionEvent crossfade cap:")
check(BoardDiff.flightCap == 6, "cap frozen at 6 (PR checklist value)")
let smallEvent = BoardMotionEvent(seq: 1, diff: mix)
check(!smallEvent.crossfade, "4 changes → flights")
let bigCur = lanes([("approval", (1...7).map { "N-\($0)" })])
let bigDiff = BoardDiff.compute(previous: lanes([("approval", [])]), current: bigCur)
check(bigDiff.changeCount == 7, "7 inserts")
let bigEvent = BoardMotionEvent(seq: 2, diff: bigDiff)
check(bigEvent.crossfade, "7 changes → crossfade, no flights")
let edgeEvent = BoardMotionEvent(
    seq: 3,
    diff: BoardDiff.compute(previous: lanes([("approval", [])]),
                            current: lanes([("approval", (1...6).map { "N-\($0)" })])))
check(!edgeEvent.crossfade, "exactly 6 changes still flies (cap is exclusive)")

// ---- 11. empty board edge cases ----
print("[11] empty boards:")
check(BoardDiff.compute(previous: lanes([]), current: lanes([])).isEmpty,
      "empty→empty")
check(BoardDiff.compute(previous: firstBoard, current: lanes([])).removals.count == 3,
      "board emptied → every id a removal")

print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)
