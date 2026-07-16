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

// ---- 7. device_label (§35 v0.35): optional top-level rename-without-rescan ----
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

// ---- 8. §37 living display titles: add-only decode + headline preference ----
print("[8] display_title / user_titled / former_titles decode:")
let titled = """
{"needs_approval": [
   {"id": "P-1", "title": "https://example.com/a/b", "summary": "人话摘要",
    "display_title": "整理推荐信", "former_titles": ["旧名一", "旧名二"],
    "notes_text": "评论折叠"},
   {"id": "P-2", "title": "raw", "summary": "摘要",
    "display_title": "用户钉的名", "user_titled": true}
 ],
 "review": [{"id": "V-1", "name": "raw internal name",
             "display_title": "起草绿卡推荐信", "user_titled": true}],
 "running": [{"id": "R-1", "name": "raw", "display_title": "跑着的显示名",
              "final_draft": "草稿正文"}],
 "debt": [{"id": "D-1", "title": "raw", "display_title": "潜在任务显示名"}]}
"""
if let d = decodeDashboard(titled) {
    let p1 = d.needs_approval[0]
    check(p1.display_title == "整理推荐信", "display_title decodes")
    check(p1.former_titles == ["旧名一", "旧名二"], "former_titles decode")
    check(p1.notes_text == "评论折叠", "notes_text decodes")
    check(p1.displaySummary == "人话摘要", "summary still wins when not user-titled")
    let p2 = d.needs_approval[1]
    check(p2.user_titled && p2.displaySummary == "用户钉的名",
          "user-pinned name wins over summary", "got \(p2.displaySummary)")
    let v1 = d.review[0]
    check(v1.rowTitle == "起草绿卡推荐信", "review rowTitle prefers display_title")
    check(v1.displayHeadline == "起草绿卡推荐信", "user-pinned review headline")
    let r1 = d.running[0]
    check(r1.rowTitle == "跑着的显示名", "running rowTitle prefers display_title")
    check(r1.final_draft == "草稿正文", "running final_draft decodes (§37 search)")
    let m = BoardModel(d)
    check(m.title(of: "V-1") == "起草绿卡推荐信", "BoardModel.title uses displayHeadline")
    check(m.title(of: "D-1") == "潜在任务显示名",
          "debt displaySummary backstops with display_title")
} else { check(false, "decode", "titled payload must decode") }
if let d = decodeDashboard(#"{"needs_approval": [{"id": "P-1", "title": "t"}]}"#) {
    let c = d.needs_approval[0]
    check(c.display_title == nil && !c.user_titled && c.former_titles == nil,
          "absent §37 fields decode to nil/false (old actd payloads)")
    check(c.displaySummary == "t", "fallback chain bottoms out at title")
} else { check(false, "decode", "legacy payload must decode") }
if let d = decodeDashboard(
    #"{"trash": [{"id":"T-1","title":"raw","display_title":"回收站显示名","user_titled":true}]}"#) {
    check(d.trash[0].displaySummary == "回收站显示名", "trash row honors user pin")
} else { check(false, "decode", "trash titled payload must decode") }

// ---- 9. §37 SearchMatch: separator-free latin runs + CJK + AND terms ----
print("[9] SearchMatch normalized matching:")
check(SearchMatch.matches("eb1", in: ["准备 EB-1A 的推荐信"]), "eb1 → EB-1A")
check(SearchMatch.matches("h1b", in: ["H-1B transfer timeline"]), "h1b → H-1B")
check(!SearchMatch.matches("eb2", in: ["准备 EB-1A 的推荐信"]),
      "no false positive: eb2 must NOT match EB-1A")
check(SearchMatch.matches("绿卡", in: ["下一步是绿卡材料清单"]), "CJK substring")
check(SearchMatch.matches("绿卡 推荐信", in: ["整理绿卡材料", "三封推荐信"]),
      "multi-term AND across fields")
check(!SearchMatch.matches("绿卡 报税", in: ["整理绿卡材料", "三封推荐信"]),
      "AND semantics: one missing term fails the card")
check(SearchMatch.matches("EB1A", in: ["eb-1a petition"]), "case-insensitive both ways")
check(SearchMatch.matches("v0.33", in: ["v0_33 release notes"]),
      "underscore/dot separators strip the same way")
check(SearchMatch.matches("", in: ["anything"]), "empty query = passthrough")
check(!SearchMatch.matches("x", in: []), "no fields = no match")
// review fix — cross-layer AND: the Store appends the session text to the
// FIELD haystack (one combined AND pool), so "推荐信 chen" matches a card
// whose display title has 推荐信 while only the transcript mentions chen.
// Badge truth: fields alone must NOT match in that case.
let cardFields = ["整理绿卡推荐信材料", "R-1"]
let sessionText = "和 chen 教授通了电话，聊了下一步"
check(!SearchMatch.matches("推荐信 chen", in: cardFields),
      "cross-layer: fields alone miss (badge condition)")
check(SearchMatch.matches("推荐信 chen", in: cardFields + [sessionText]),
      "cross-layer: fields + session combined hit (filter condition)")
// review fix — pre-normalized hot path must agree with the convenience API
let hay = SearchMatch.normalizedHaystack(cardFields + [sessionText])
check(SearchMatch.matchesNormalized("推荐信 chen", in: hay)
        == SearchMatch.matches("推荐信 chen", in: cardFields + [sessionText]),
      "matchesNormalized == matches over normalizedHaystack")
check(SearchMatch.matchesNormalized("eb1", in: SearchMatch.normalizedHaystack(["EB-1A"])),
      "normalizedHaystack strips separators once, matches still hit")

// ---- 10. FoldNote.parse (§38): notes_text fold-line parsing ----
// Lockstep twin of act/lib/registry.py's fold-line regexes. The projection is
// line-aligned TAIL-clipped (python side), so the parser only ever sees whole
// lines — but a straddle-shaped partial line must still degrade safely (no
// crash, no phantom split marker), and the 已拆出 real-signal line must parse
// exactly (the Mac Store clears its optimistic 拆分中… off it).
print("[10] FoldNote.parse:")
let foldNotes = """
…（更早的备注已省略）
plain non-fold note line
[radar] 邮件又催了一遍 [@2026-07-16T08:00:00Z]
[quick] 老格式没有句柄
[radar] 已拆出去的那条 [@2026-07-16T08:00:01Z] [已拆出 R-045]
"""
let parsed = FoldNote.parse(foldNotes)
check(parsed.count == 3, "non-fold lines (marker, prose) skipped", "got \(parsed.count)")
check(parsed[0].kind == "radar" && parsed[0].text == "邮件又催了一遍"
        && parsed[0].ts == "2026-07-16T08:00:00Z" && parsed[0].splitInto == nil,
      "timestamped line → text + ts handle")
check(parsed[1].ts == nil && parsed[1].splitInto == nil,
      "legacy un-timestamped line → display-only (no handle)")
check(parsed[2].splitInto == "R-045" && parsed[2].ts == "2026-07-16T08:00:01Z",
      "已拆出 line → splitInto (the Store's real-signal read)",
      "got \(String(describing: parsed[2].splitInto))")
// straddle shape: a HEAD-clipped partial tag (the pre-fix projection bug)
// must not parse as a split marker — and must not crash.
let straddle = FoldNote.parse("[radar] 拆过的 [@t1] [已拆出 R")
check(straddle.count == 1 && straddle[0].splitInto == nil,
      "truncated 已拆出 tag → no phantom split marker",
      "got \(String(describing: straddle.first?.splitInto))")
check(FoldNote.parse(nil).isEmpty && FoldNote.parse("").isEmpty,
      "nil/empty notes → empty")

// ---- 11. question (§39 v0.39): needs_input rows carry the pending question ----
// Old actd payloads lack the key (nil → UI falls back to waiting_for); the
// InboxAction side of §39 (answer_input encoding + the scalar clip) is locked
// in section 12 below, decode compat is locked here.
print("[11] needs_input question decode:")
let questionBoard = """
{"needs_input": [
   {"id": "N-1", "name": "asker", "state": "blocked",
    "question": "A 方案还是 B 方案？", "waiting_for": null},
   {"id": "N-2", "name": "legacy", "state": "blocked", "waiting_for": "input"}
 ]}
"""
if let d = decodeDashboard(questionBoard) {
    check(d.needs_input.first?.question == "A 方案还是 B 方案？",
          "present → decoded", "got \(String(describing: d.needs_input.first?.question))")
    check(d.needs_input.first?.waiting_for == nil,
          "null waiting_for beside a question → nil")
    check(d.needs_input.last?.question == nil, "absent → nil (old actd payloads)")
    check(d.needs_input.last?.waiting_for == "input", "legacy fallback row intact")
    check(d.decodeDrops.isEmpty, "question is not a drop", "got \(d.decodeDrops)")
} else { check(false, "decode", "question payload must decode") }

// ---- 12. InboxAction.answerInput (§39.2): pinned wire bytes + scalar clip ----
// The encoder must stay byte-deterministic (sortedKeys) and clipAnswer must
// count UNICODE SCALARS — actd validates len(text) in Python code points, so
// a Character-based prefix could smuggle >4000 code points past the client.
print("[12] answerInput encoding + clipAnswer:")
let ansTS = "2026-07-16T00:00:00Z"
let pinned = InboxAction.answerInput(id: "R-001", text: "用 A 方案",
                                     expectedStatus: "executing", ts: ansTS)
check(String(data: pinned, encoding: .utf8) ==
      #"{"action":"answer_input","expected_status":"executing","id":"R-001","text":"用 A 方案","ts":"2026-07-16T00:00:00Z"}"#,
      "pinned encoding is stable (sortedKeys)",
      "got \(String(data: pinned, encoding: .utf8) ?? "nil")")
let noPin = InboxAction.answerInput(id: "R-001", text: "t", ts: ansTS)
check(String(data: noPin, encoding: .utf8)?.contains("expected_status") == false,
      "nil expectedStatus omits the key (local Mac convention)")
// "a🇨🇳b" = 4 scalars (flag is 2) but only 3 Characters — a Character-based
// prefix(3) would keep all 4 code points; the scalar clip must not.
check(InboxAction.clipAnswer("a🇨🇳b", max: 3) == "a🇨🇳",
      "clipAnswer counts unicode scalars",
      "got \(InboxAction.clipAnswer("a🇨🇳b", max: 3))")
check(InboxAction.clipAnswer("abc", max: 3) == "abc", "under the bound → untouched")
check(InboxAction.clipAnswer("", max: 3) == "", "empty stays empty")

// ---- 13. §40 add-only fields: cost_state (needs_approval) + purge_at (trash) ----
// Old actd payloads lack both keys — they must decode to nil (the render sites
// then derive: cost from cost_usd presence, no countdown). Junk values must
// never fail the row.
print("[13] §40 cost_state + purge_at decode:")
let hon = """
{"needs_approval": [
   {"id": "P-1", "title": "known", "cost_usd": 3.5, "show_cost": false, "cost_state": "estimated"},
   {"id": "P-2", "title": "unknown", "cost_usd": null, "show_cost": false, "cost_state": "unknown"}
 ],
 "trash": [
   {"id": "T-1", "title": "counting down", "kind": "debt", "purge_at": "2026-08-30T00:00:00Z"},
   {"id": "T-2", "title": "pinned", "kind": "debt", "permanent": true, "purge_at": null}
 ]}
"""
if let d = decodeDashboard(hon) {
    check(d.needs_approval.map(\.cost_state) == ["estimated", "unknown"],
          "cost_state decodes per row", "got \(d.needs_approval.map(\.cost_state))")
    check(d.needs_approval[0].cost_usd == 3.5, "cost_usd rides alongside cost_state")
    check(d.trash[0].purge_at == "2026-08-30T00:00:00Z", "purge_at decodes",
          "got \(String(describing: d.trash[0].purge_at))")
    check(d.trash[1].purge_at == nil && d.trash[1].permanent, "pinned row → null purge_at")
    check(d.decodeDrops.isEmpty, "§40 fields are not drops", "got \(d.decodeDrops)")
} else { check(false, "decode", "§40 payload must decode") }
let honOld = #"{"needs_approval": [{"id": "P-1", "title": "old"}], "trash": [{"id": "T-1", "title": "old", "kind": "debt"}]}"#
if let d = decodeDashboard(honOld) {
    check(d.needs_approval[0].cost_state == nil, "absent cost_state → nil (old actd)")
    check(d.trash[0].purge_at == nil, "absent purge_at → nil (old actd)")
} else { check(false, "decode", "old payload must decode") }
if let d = decodeDashboard(#"{"needs_approval": [{"id": "P-1", "title": "junk", "cost_state": 42}], "trash": [{"id": "T-1", "title": "junk", "kind": "debt", "purge_at": 42}]}"#) {
    check(d.needs_approval.first?.cost_state == nil && d.trash.first?.purge_at == nil,
          "junk §40 values → nil, rows survive",
          "drops \(d.decodeDrops)")
} else { check(false, "decode", "junk §40 values must not fail the payload") }
if !allOK {
    FileHandle.standardError.write(Data("CONTRACT TESTS: FAILURES\n".utf8))
    exit(1)
}
print("CONTRACT TESTS: ALL PASS")
