// ContractHarness.swift — behavior tests for shared/Sources: Contract.swift's
// per-row lossy dashboard.json decode (decodeLossyRows + stableFallbackID +
// decodeDrops + update_available suppression) and BoardModel's running-lane
// merge / badge counts. Compiled by run.sh (with shared/Sources/*.swift) into
// a plain macOS CLI tool — no Xcode, no XCTest. Exits non-zero on any failure.
//
// These are the most intricate hand-written pieces both apps compile but
// nothing else executes: their regression mode is exactly the silent lane-wipe
// (or lying lane order/badge) they were written to prevent.

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

func decodeDashboard(_ json: String) -> Dashboard? {
    try? JSONDecoder().decode(Dashboard.self, from: Data(json.utf8))
}

// ---- 1. lossy row decode: one corrupt row must not wipe the lane ----
// The pre-fix bug (documented in Contract.swift): `(try? decode) ?? []` turned
// ONE malformed running row into an empty 运行中 lane on Mac AND iPhone while
// the badge kept its count and loadError stayed nil.
print("[1] one corrupt row → other rows survive, drop is recorded:")
let corruptRow = """
{"counts": {"running": 2},
 "running": [
   {"id": "R-1", "name": "good one"},
   {"id": 42, "name": 7},
   {"id": "R-3", "name": "good two"}
 ]}
"""
if let d = decodeDashboard(corruptRow) {
    check(d.running.map(\.id) == ["R-1", "R-3"], "good rows survive", "got \(d.running.map(\.id))")
    check(d.decodeDrops == ["running[1]"], "decodeDrops observability", "got \(d.decodeDrops)")
    check(d.counts.running == 2, "counts untouched by row drop")
} else { check(false, "decode", "corrupt row must not fail the whole payload") }

// ---- 2. non-object junk row: skipped WITHOUT losing the rows after it ----
// Exercises the skip-consume path (AnySkippedRow) on a scalar element; the
// stuck-index give-up branch stays as defensive depth. Either way the decode
// must terminate, keep every good row it can, and record what it dropped.
print("[2] scalar junk row → skipped, later rows survive, no hang:")
let scalarJunk = """
{"running": [{"id": "R-1", "name": "good one"}, 42, {"id": "R-3", "name": "late row"}]}
"""
if let d = decodeDashboard(scalarJunk) {
    check(d.running.map(\.id) == ["R-1", "R-3"], "rows around the junk survive", "got \(d.running.map(\.id))")
    check(d.decodeDrops.first == "running[1]" && !d.decodeDrops.isEmpty,
          "junk row recorded in decodeDrops", "got \(d.decodeDrops)")
} else { check(false, "decode", "junk row must not fail the whole payload") }

// ---- 3. missing id → deterministic noid- fallback (stable across decodes) ----
// A random UUID here makes identity drift every ~10s reload (SwiftUI churn,
// hiddenSticky/pendingMergeActions bookkeeping all mismatch).
print("[3] missing id → stable content-derived noid- id:")
let noID = #"{"trash": [{"title": "no id row", "summary": "s", "kind": "debt"}]}"#
let noIDOther = #"{"trash": [{"title": "different row", "summary": "s", "kind": "debt"}]}"#
if let a = decodeDashboard(noID), let b = decodeDashboard(noID), let c = decodeDashboard(noIDOther),
   let ta = a.trash.first, let tb = b.trash.first, let tc = c.trash.first {
    check(ta.id.hasPrefix("noid-"), "fallback id is flagged noid-", "got \(ta.id)")
    check(ta.id == tb.id, "same row → same id on every decode", "\(ta.id) vs \(tb.id)")
    check(ta.id != tc.id, "different content → different id")
} else { check(false, "decode", "noid fixtures must decode") }

// ---- 4. update_available: empty latest is "no known update", not a banner ----
print("[4] update_available suppression:")
if let d = decodeDashboard(#"{"update_available": {"latest": ""}}"#) {
    check(d.update_available == nil, "empty latest → nil")
} else { check(false, "decode", "empty-latest payload must decode") }
if let d = decodeDashboard(#"{"update_available": {"latest": "9.9.9"}}"#) {
    check(d.update_available?.latest == "9.9.9", "real latest → surfaced")
} else { check(false, "decode", "real-latest payload must decode") }
if let d = decodeDashboard("{}") {
    check(d.update_available == nil, "absent → nil (old actd payloads)")
} else { check(false, "decode", "empty payload must decode") }

// ---- 5. unknown keys / absent sections → [] with no drops (compat both ways) ----
print("[5] forward/backward compatibility:")
if let d = decodeDashboard(#"{"some_future_section": {"x": 1}, "counts": {"debt": 3}}"#) {
    check(d.debt.isEmpty && d.running.isEmpty && d.trash.isEmpty && d.archived.isEmpty,
          "absent sections decode to []")
    check(d.decodeDrops.isEmpty, "absent sections are NOT drops", "got \(d.decodeDrops)")
    check(d.counts.debt == 3, "counts still decode")
} else { check(false, "decode", "future-key payload must decode") }
if let d = decodeDashboard(#"{"trash": 123}"#) {
    check(d.trash.isEmpty, "whole-column corruption → empty column")
    check(d.decodeDrops.count == 1 && d.decodeDrops[0].hasPrefix("trash ("),
          "whole-column corruption IS an observable drop", "got \(d.decodeDrops)")
} else { check(false, "decode", "corrupt-column payload must decode") }

// ---- 6. BoardModel: running-lane merge order + badge counts + title(of:) ----
// Lanes.swift's shipped help copy promises: "Orange 'Needs input' … those sort
// first" and the lane badge = running + needs_input. Lock both.
print("[6] BoardModel lane logic:")
let board = """
{"counts": {"running": 2, "needs_input": 1, "needs_approval": 1, "debt": 1, "review": 1},
 "running": [{"id": "R-1", "name": "run one", "summary": "Running One"},
             {"id": "R-2", "name": "run two"}],
 "needs_input": [{"id": "N-1", "name": "blocked", "waiting_for": "answer"}],
 "needs_approval": [{"id": "P-1", "title": "prop", "summary": "Proposal One"}],
 "review": [{"id": "V-1", "name": "review one"}],
 "debt": [{"id": "D-1", "title": "Debt One"}]}
"""
if let d = decodeDashboard(board) {
    let m = BoardModel(d)
    check(m.runningLane.map(\.id) == ["N-1", "R-1", "R-2"],
          "needs_input sorts first in the running lane", "got \(m.runningLane.map(\.id))")
    check(m.isNeedsInput(m.runningLane[0]), "isNeedsInput flags the blocked row")
    check(!m.isNeedsInput(m.runningLane[1]), "isNeedsInput does NOT flag a running row")
    check(BoardLane.running.count(d.counts) == 3, "running badge = running + needs_input")
    check(m.count(.running) == 3 && m.count(.backlog) == 1 && m.count(.review) == 1,
          "BoardModel.count matches Counts")
    check(BoardLane.allCases.map(\.id) == ["backlog", "proposals", "running", "review", "done"],
          "lane/page order is frozen")
    check(m.title(of: "P-1") == "Proposal One", "title(of:) proposal → displaySummary")
    check(m.title(of: "D-1") == "Debt One", "title(of:) debt")
    check(m.title(of: "R-1") == "Running One", "title(of:) running → summary")
    check(m.title(of: "R-2") == "run two", "title(of:) running → name fallback")
    check(m.title(of: "V-1") == "review one", "title(of:) review")
    check(m.title(of: "ZZZ") == "ZZZ", "title(of:) off-board id → bare id")
} else { check(false, "decode", "board fixture must decode") }

// ---- 7. device_label (§34 v0.35): optional top-level rename-without-rescan ----
// The Mac's user-set device name rides the board payload; old actd payloads
// lack the key and must keep decoding (nil), and junk must not fail the decode.
print("[7] device_label decode:")
if let d = decodeDashboard(#"{"device_label": "书房的 Mac mini", "counts": {"running": 1}}"#) {
    check(d.device_label == "书房的 Mac mini", "present → decoded",
          "got \(String(describing: d.device_label))")
    check(d.counts.running == 1, "sibling fields unaffected")
    check(d.decodeDrops.isEmpty, "device_label is not a drop", "got \(d.decodeDrops)")
} else { check(false, "decode", "device_label payload must decode") }
if let d = decodeDashboard("{}") {
    check(d.device_label == nil, "absent → nil (old actd payloads)")
} else { check(false, "decode", "empty payload must decode") }
if let d = decodeDashboard(#"{"device_label": 42}"#) {
    check(d.device_label == nil, "non-string → nil, payload still decodes")
} else { check(false, "decode", "junk device_label must not fail the payload") }

if !allOK {
    FileHandle.standardError.write(Data("CONTRACT TESTS: FAILURES\n".utf8))
    exit(1)
}
print("CONTRACT TESTS: ALL PASS")
