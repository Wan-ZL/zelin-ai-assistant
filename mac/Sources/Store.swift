// Store.swift — DashboardStore（含本地占位状态）/ CaptureDraft / SlashCommands / CaptureHistory

import AppKit
import SwiftUI
import Foundation

// MARK: - Local instant-feedback types (契约2)

/// Which dashboard list an item (or its echo) belongs to.
enum ListKind: String { case approval, running, review, debt, trash, completed }

/// Optimistic "the action is in flight" placeholder rendered in the TARGET
/// list right after a button press, before actd rewrites dashboard.json.
struct PendingEcho: Identifiable, Hashable {
    let id: String        // "echo-" + sourceID
    let sourceID: String  // original item id
    let title: String     // original item title (self-looked-up from dashboard)
    let target: ListKind  // which list renders it
    let label: String     // greyed status label (契约4)
    let created: Date
}

/// A raise-placeholder ("研究并提议") with its creation time for the timeout.
struct RaisingEntry {
    let summary: String
    let created: Date
}

/// v0.10.2: a "card returns to another lane" action in flight. restore /
/// abort_execution / revert_review share the one pending+timeout mechanism
/// that restore introduced in v0.10 (契约: 信息条 instead of an echo);
/// `kind` picks the per-action timeout wording.
struct PendingReturn {
    enum Kind { case restore, abort, revert }
    let kind: Kind
    let created: Date
}

/// Timed-out placeholder notice (capture → yellow, raise → orange).
struct LocalNotice: Identifiable, Equatable {
    enum Kind { case captureTimeout, raiseTimeout }
    let id: String
    let kind: Kind
    let text: String
    let created: Date
}

// MARK: - Store (@MainActor => Sendable, safe to capture in Timer block)

@MainActor
final class DashboardStore: ObservableObject {
    @Published var dashboard: Dashboard?
    @Published var lastRefresh: Date?
    @Published var loadError: String?
    @Published var missing: Bool = true
    // quick-capture spinner cards (popover input → state/inbox/capture-*.json)
    @Published var capturePending: [CapturePending] = []
    // Optimistic removal after a button press. Two policies:
    //  - sticky: id → SOURCE list; kept hidden until the item actually LEAVES
    //    that list (actd moved it). Recording the source list fixes the old
    //    "moved between lists → hidden forever" bug: only the source matters.
    //  - once: hidden until dashboard.generated_at changes (legacy comment path).
    @Published var hiddenSticky: [String: ListKind] = [:]
    @Published var hiddenOnce: Set<String> = []
    // just-raised debts: id -> (summary, created), shown as a greyed spinner in
    // 待审批 until the backend surfaces the card — or the 180 s timeout fires.
    @Published var raisingLocal: [String: RaisingEntry] = [:]
    // instant-feedback echoes rendered in their TARGET list (契约2)
    @Published var pendingEchoes: [PendingEcho] = []
    // pin pressed → show the 永久 badge immediately (backend still catching up)
    @Published var pinnedLocal: Set<String> = []
    // comment sent → card stays in place with a blue "修改意见合并中…" line
    // until generated_at changes (or the 180 s fallback)
    @Published var pendingComment: [String: Date] = [:]
    // "returns to another lane" actions in flight (restore / abort_execution /
    // revert_review) → id + kind + when. These plant NO echo (契约: info strip
    // instead; restore's target lane is unknown anyway), so without this the
    // sticky hide has no timeout: actd down → the card stays in its source
    // list AND stays hidden forever. sweepTimeouts releases the hide after
    // 180 s, mirroring the echo branch. (v0.10 restoringLocal, generalized.)
    @Published var returningLocal: [String: PendingReturn] = [:]
    // timed-out placeholder notices (capture = yellow, raise = orange)
    @Published var notices: [LocalNotice] = []

    // raw bytes of the last successfully-read dashboard.json — reload
    // short-circuits (no publish) when the file hasn't changed.
    private var lastRawData: Data?
    private var lastGeneratedAt: String?

    func reload() {
        // local placeholder timeouts tick on every refresh, even when the
        // dashboard file itself is unchanged (actd down = file frozen).
        sweepTimeouts()

        let path = AppPaths.dashboardPath
        guard FileManager.default.fileExists(atPath: path) else {
            if dashboard != nil || !missing || loadError != nil {
                dashboard = nil
                missing = true
                loadError = nil
                lastRawData = nil
                lastRefresh = Date()
            }
            return
        }
        let data: Data
        do {
            data = try Data(contentsOf: URL(fileURLWithPath: path))
        } catch {
            loadError = L("读取 dashboard.json 失败: ", "Failed to read dashboard.json: ")
                + error.localizedDescription
            lastRefresh = Date()
            return
        }
        // unchanged bytes (vs the last SUCCESSFUL decode) → nothing new to
        // decode or publish; clear a stale decode error from a bad interim file
        if data == lastRawData {
            if loadError != nil { loadError = nil }
            return
        }
        do {
            let db = try JSONDecoder().decode(Dashboard.self, from: data)
            lastRawData = data
            withAnimation(.easeOut(duration: 0.2)) {
                dashboard = db
                missing = false
                loadError = nil
                // one-shot hides + pending comments clear when the backend has
                // actually regenerated (generated_at changed); missing field →
                // legacy behavior (clear on any reload) + 180 s fallback.
                if let gen = db.generated_at, !gen.isEmpty {
                    if gen != lastGeneratedAt {
                        lastGeneratedAt = gen
                        hiddenOnce.removeAll()
                        pendingComment.removeAll()
                    }
                } else {
                    hiddenOnce.removeAll()
                }
                // sticky hides release once the id has LEFT its source list —
                // moving to ANOTHER list no longer keeps it hidden forever.
                hiddenSticky = hiddenSticky.filter { id, kind in
                    ids(in: kind, of: db).contains(id)
                }
                // return bookkeeping (restore/abort/revert) clears once its
                // sticky hide released (the id left its source list — actd
                // actually moved the card).
                returningLocal = returningLocal.filter { hiddenSticky[$0.key] != nil }
                // echoes clear once the item shows up in its target list.
                // v0.10: running[] now mixes in state=="queued" items — they
                // decode into db.running like any other row, so an approve echo
                // is replaced the moment its queued twin appears (verified: the
                // "sourceID in target list" match below needs no special case).
                pendingEchoes.removeAll { ids(in: $0.target, of: db).contains($0.sourceID) }
                // drop local raise-placeholders once the backend shows the item
                // anywhere in needs_approval (raising card or finished card).
                let backendApproval = Set(db.needs_approval.map { $0.id })
                for id in Array(raisingLocal.keys) where backendApproval.contains(id) {
                    raisingLocal.removeValue(forKey: id)
                }
                // drop capture placeholders once a needs_approval card matches
                // (normalized, bidirectional contains on the first 10 chars)
                capturePending.removeAll { pending in
                    Self.captureMatches(pending.text, in: db)
                }
                // backend confirmed permanent → local pin marker is redundant
                pinnedLocal.subtract(db.trash.filter { $0.permanent }.map { $0.id })
            }
        } catch {
            // Keep the previously good dashboard rather than blanking the UI.
            loadError = L("读取 dashboard.json 失败: ", "Failed to read dashboard.json: ")
                + error.localizedDescription
        }
        lastRefresh = Date()
    }

    // MARK: timeouts (run every refresh tick, independent of file changes)

    private func sweepTimeouts() {
        let now = Date()
        // capture placeholders: 300 s → yellow notice (analysis can be slow)
        let expiredCaptures = capturePending.filter { now.timeIntervalSince($0.created) > 300 }
        // raise placeholders: 180 s → orange notice + release the sticky hide
        let expiredRaises = raisingLocal.filter { now.timeIntervalSince($0.value.created) > 180 }
        // echoes: 180 s → give up; release the sticky hide so the card returns
        let expiredEchoes = pendingEchoes.filter { now.timeIntervalSince($0.created) > 180 }
        // comment fallback (no generated_at movement): 180 s
        let expiredComments = pendingComment.filter { now.timeIntervalSince($0.value) > 180 }
        // returns (restore/abort/revert): 180 s without leaving the source
        // list → give up, release the hide
        let expiredReturns = returningLocal.filter { now.timeIntervalSince($0.value.created) > 180 }
        // notices themselves fade after 120 s
        let expiredNotices = notices.filter { now.timeIntervalSince($0.created) > 120 }
        guard !expiredCaptures.isEmpty || !expiredRaises.isEmpty || !expiredEchoes.isEmpty
            || !expiredComments.isEmpty || !expiredReturns.isEmpty
            || !expiredNotices.isEmpty else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            for c in expiredCaptures {
                capturePending.removeAll { $0.id == c.id }
                notices.append(LocalNotice(
                    id: "notice-" + c.id, kind: .captureTimeout,
                    text: L("分析比平时慢，卡片稍后会自动出现（也可能失败，留意 actd）",
                            "Analysis is slower than usual — the card should still appear (check actd if not)"),
                    created: now))
            }
            for (id, entry) in expiredRaises {
                raisingLocal.removeValue(forKey: id)
                hiddenSticky.removeValue(forKey: id)
                notices.append(LocalNotice(
                    id: "notice-raise-" + id, kind: .raiseTimeout,
                    text: L("「\(String(entry.summary.prefix(20)))」研究提案超时，请重试",
                            "Research proposal for \"\(String(entry.summary.prefix(20)))\" timed out — try again"),
                    created: now))
            }
            for e in expiredEchoes {
                pendingEchoes.removeAll { $0.id == e.id }
                hiddenSticky.removeValue(forKey: e.sourceID)
                // no longer silent: tell the user whether the card survived.
                // gone from every list → the write likely never landed (orange);
                // still present → it just un-hides and is operable again (yellow).
                let stillExists = currentList(of: e.sourceID) != nil
                let noticeID = "notice-echo-" + e.sourceID
                notices.removeAll { $0.id == noticeID }
                let label = String(e.title.prefix(20))
                notices.append(LocalNotice(
                    id: noticeID,
                    kind: stillExists ? .captureTimeout : .raiseTimeout,
                    text: stillExists
                        ? L("后台响应超时，卡片已恢复可操作",
                            "Backend timed out — the card is interactive again")
                        : L("「\(label)」已提交但后台超时未确认，请检查 actd 是否在运行",
                            "\"\(label)\" was submitted but the backend never confirmed — check that actd is running"),
                    created: now))
            }
            for (id, _) in expiredComments { pendingComment.removeValue(forKey: id) }
            for (id, entry) in expiredReturns {
                returningLocal.removeValue(forKey: id)
                hiddenSticky.removeValue(forKey: id)   // source card returns, operable again
                let noticeID = "notice-return-" + id
                notices.removeAll { $0.id == noticeID }   // replace the info notice
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout,
                    text: Self.returnTimeoutText(entry.kind),
                    created: now))
            }
            for n in expiredNotices { notices.removeAll { $0.id == n.id } }
        }
    }

    /// Per-kind timeout wording for the shared pending-return mechanism.
    private static func returnTimeoutText(_ kind: PendingReturn.Kind) -> String {
        switch kind {
        case .restore:
            return L("恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）",
                     "Restore timed out — the card is back in the trash, try again (check that actd is running)")
        case .abort:
            return L("停止退回超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）",
                     "Stop & return timed out — the card is still in Running, try again (check that actd is running)")
        case .revert:
            return L("退回待验收超时，卡片仍在已验收列，可重试（检查 actd 是否在运行）",
                     "Back-to-review timed out — the card is still in Delivered, try again (check that actd is running)")
        }
    }

    // MARK: applyAction — the ONE entry point for card-button actions (契约2)

    /// Wave-2 wiring target: AppDelegate.submit() calls this after the inbox
    /// write succeeds. Policy is frozen — see the implementation plan.
    func applyAction(_ action: String, id: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            switch action {
            case "approve":
                hideSticky(id, from: .approval)
                addEcho(id: id, target: .running, label: L("启动中…", "Starting…"))
            case "rework":
                hideSticky(id, from: .review)
                addEcho(id: id, target: .running, label: L("打回处理中…", "Sending back…"))
            case "accept":
                hideSticky(id, from: .review)
                addEcho(id: id, target: .completed, label: L("验收确认中…", "Accepting…"))
            case "reject", "trash":
                hideSticky(id, from: currentList(of: id))
                // trash echo counts (visibleTrashCount) but renders no card
                addEcho(id: id, target: .trash, label: "")
            case "restore":
                // no echo: the card may return to ANY lane (its previous
                // state), so a fixed-target placeholder would often be wrong.
                // sticky-hide from trash + an info notice instead; returningLocal
                // gives the hide a 180 s timeout (sweepTimeouts) — without it an
                // unresponsive actd would keep the card hidden forever.
                beginReturn(id, from: .trash, kind: .restore,
                            info: L("恢复中，卡片将回到原状态列",
                                    "Restoring — the card returns to its previous lane"))
            case "done_external":
                // v0.10.2: Zelin finished it outside the system (card_sent |
                // review → DELIVERED) — echo straight into 已验收.
                hideSticky(id, from: currentList(of: id))
                addEcho(id: id, target: .completed, label: L("已办完", "done outside"))
            case "abort_execution":
                // v0.10.2: stop the run, card returns to 待审批 (CARD_SENT) —
                // same pending+timeout mechanism as restore (契约: 信息条).
                beginReturn(id, from: .running, kind: .abort,
                            info: L("停止中，卡片将回到待审批",
                                    "Stopping — card returns to approval"))
            case "revert_review":
                // v0.10.2: delivered → back to REVIEW for re-acceptance.
                beginReturn(id, from: .completed, kind: .revert,
                            info: L("退回中，卡片将回到待验收",
                                    "Reverting to review"))
            case "pin":
                pinnedLocal.insert(id)   // no hide — badge flips in place
            case "comment":
                pendingComment[id] = Date()   // no hide — blue in-place line
            default:
                // e.g. "raise": optimistic sticky hide from wherever it lives
                // (the raisingLocal placeholder is planted by beginRaising)
                hideSticky(id, from: currentList(of: id))
            }
        }
    }

    /// Echoes to prepend before the backend rows of one list.
    func echoes(for target: ListKind) -> [PendingEcho] {
        pendingEchoes.filter { $0.target == target }
    }

    /// Trash count including in-flight reject/trash echoes (rendered nowhere).
    var visibleTrashCount: Int {
        visibleTrash.count + echoes(for: .trash).count
    }

    private func hideSticky(_ id: String, from kind: ListKind?) {
        hiddenSticky[id] = kind ?? .approval
    }

    /// Shared "the card will come back in another lane" bookkeeping (restore /
    /// abort_execution / revert_review): sticky-hide from the source list, arm
    /// the 180 s timeout, and show an info strip (these actions plant no echo).
    private func beginReturn(_ id: String, from source: ListKind,
                             kind: PendingReturn.Kind, info: String) {
        hideSticky(id, from: source)
        returningLocal[id] = PendingReturn(kind: kind, created: Date())
        let noticeID = "notice-return-" + id
        notices.removeAll { $0.id == noticeID }
        notices.append(LocalNotice(
            id: noticeID, kind: .captureTimeout, text: info, created: Date()))
    }

    private func addEcho(id: String, target: ListKind, label: String) {
        pendingEchoes.removeAll { $0.sourceID == id }
        pendingEchoes.append(PendingEcho(
            id: "echo-" + id, sourceID: id, title: title(of: id),
            target: target, label: label, created: Date()))
    }

    /// Which list currently holds this id (self-lookup for source recording).
    private func currentList(of id: String) -> ListKind? {
        guard let db = dashboard else { return nil }
        for kind in [ListKind.approval, .review, .debt, .trash, .running, .completed]
        where ids(in: kind, of: db).contains(id) { return kind }
        return nil
    }

    private func ids(in kind: ListKind, of db: Dashboard) -> Set<String> {
        switch kind {
        case .approval: return Set(db.needs_approval.map { $0.id })
        // .running spans running (incl. v0.10 queued rows) + needs_input
        case .running: return Set(db.running.map { $0.id }).union(db.needs_input.map { $0.id })
        case .review: return Set(db.review.map { $0.id })
        case .debt: return Set(db.debt.map { $0.id })
        case .trash: return Set(db.trash.map { $0.id })
        case .completed: return Set(db.completed.map { $0.id })
        }
    }

    private func title(of id: String) -> String {
        guard let db = dashboard else { return id }
        if let c = db.needs_approval.first(where: { $0.id == id }) { return c.displaySummary }
        if let r = db.review.first(where: { $0.id == id }) { return r.name }
        if let d = db.debt.first(where: { $0.id == id }) { return d.displaySummary }
        if let t = db.trash.first(where: { $0.id == id }) { return t.displaySummary }
        if let t = (db.running + db.needs_input + db.completed).first(where: { $0.id == id }) {
            return t.name
        }
        return id
    }

    // MARK: legacy shim (wave 1: AppDelegate.submit still calls this)

    /// Compatibility shim — sticky hides self-look-up their source list, so
    /// the "moved between lists → hidden forever" fix applies without touching
    /// AppDelegate. Wave 2 replaces the call site with applyAction().
    func hide(_ id: String, sticky: Bool) {
        withAnimation(.easeOut(duration: 0.2)) {
            if sticky {
                hideSticky(id, from: currentList(of: id))
            } else {
                hiddenOnce.insert(id)
            }
        }
    }

    func beginRaising(_ id: String, summary: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            raisingLocal[id] = RaisingEntry(summary: summary, created: Date())
        }
    }

    func beginCapture(_ text: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            capturePending.append(
                CapturePending(id: "capture-" + UUID().uuidString, text: text, created: Date()))
        }
    }

    private func isHidden(_ id: String) -> Bool {
        hiddenSticky[id] != nil || hiddenOnce.contains(id)
    }

    // MARK: capture ↔ backend matching (relaxed: normalize + bidirectional)

    /// Lowercase and strip whitespace/punctuation/symbols so cosmetic rewrites
    /// by the backend (quotes, dashes, spacing) don't break the match.
    private static func normalized(_ s: String) -> String {
        s.lowercased().filter { !($0.isWhitespace || $0.isPunctuation || $0.isSymbol) }
    }

    private static func captureMatches(_ text: String, in db: Dashboard) -> Bool {
        let p = normalized(text)
        guard !p.isEmpty else { return false }
        let pKey = String(p.prefix(10))
        for card in db.needs_approval {
            for field in [card.title, card.summary ?? ""] {
                let t = normalized(field)
                guard !t.isEmpty else { continue }
                let tKey = String(t.prefix(10))
                if t.contains(pKey) || p.contains(tKey) { return true }
            }
        }
        return false
    }

    // MARK: card sorting (v0.10.3 契约一 — Prefs.cardSortOrder projection)

    /// Trailing digit run of a task id ("R-013" → 13); nil when absent (or on
    /// Int overflow) → those rows sort last, keeping their original order.
    private static func idSuffix(_ id: String) -> Int? {
        let tail = id.reversed().prefix(while: { $0.isNumber })
        guard !tail.isEmpty else { return nil }
        return Int(String(tail.reversed()))
    }

    /// Stable sort per the cardSortOrder pref (纯 UI 偏好，UserDefaults):
    ///  - "newest" (default): id numeric suffix DESCENDING; unparsable ids
    ///    last, original order kept.
    ///  - "oldest": id suffix ASCENDING (今日现状), same unparsable-tail rule.
    ///  - "deadline": dated items first by YYYY-MM-DD string ascending; the
    ///    undated rest follows as "newest". Lists whose model has no deadline
    ///    field pass nil → the whole column degrades to "newest" (契约).
    /// Sorting lives HERE in the visible* projections so the popover and the
    /// kanban stay consistent for free.
    private static func sortCards<T>(
        _ items: [T], id: (T) -> String, deadline: ((T) -> String?)? = nil
    ) -> [T] {
        // decorate with the original index — explicit stability (Swift's sort
        // stability is an implementation detail, not a documented guarantee)
        typealias Row = (offset: Int, element: T)
        let rows = Array(items.enumerated())
        func newestFirst(_ a: Row, _ b: Row) -> Bool {
            switch (idSuffix(id(a.element)), idSuffix(id(b.element))) {
            case let (x?, y?): return x == y ? a.offset < b.offset : x > y
            case (.some, .none): return true
            case (.none, .some): return false
            case (.none, .none): return a.offset < b.offset
            }
        }
        switch Prefs.cardSortOrder {
        case "oldest":
            return rows.sorted { a, b in
                switch (idSuffix(id(a.element)), idSuffix(id(b.element))) {
                case let (x?, y?): return x == y ? a.offset < b.offset : x < y
                case (.some, .none): return true
                case (.none, .some): return false
                case (.none, .none): return a.offset < b.offset
                }
            }.map { $0.element }
        case "deadline":
            guard let deadline else { return rows.sorted(by: newestFirst).map { $0.element } }
            return rows.sorted { a, b in
                let da = deadline(a.element).flatMap { $0.isEmpty ? nil : $0 }
                let db = deadline(b.element).flatMap { $0.isEmpty ? nil : $0 }
                switch (da, db) {
                case let (x?, y?): return x == y ? a.offset < b.offset : x < y
                case (.some, .none): return true   // dated before undated
                case (.none, .some): return false
                case (.none, .none): return newestFirst(a, b)
                }
            }.map { $0.element }
        default:   // "newest" — and any unknown pref value
            return rows.sorted(by: newestFirst).map { $0.element }
        }
    }

    /// 设置页 Picker changed the sort pref — the pref lives in UserDefaults
    /// (no @Published change happens by itself), so republish explicitly and
    /// every visible* consumer re-sorts immediately.
    func sortOrderChanged() {
        withAnimation(.easeOut(duration: 0.2)) { objectWillChange.send() }
    }

    // MARK: visible lists (sorted per 契约一)

    var visibleApprovals: [ApprovalCard] {
        let backend = Self.sortCards(
            (dashboard?.needs_approval ?? []).filter { !isHidden($0.id) },
            id: { $0.id }, deadline: { $0.deadline })
        let backendIDs = Set(backend.map { $0.id })
        // prepend synthetic processing placeholders for just-raised debts the
        // backend hasn't surfaced yet (the ≤10s gap before actd marks 'raising').
        // 契约一: captures + placeholders stay pinned at the very top and never
        // participate in sorting.
        let placeholders = raisingLocal
            .filter { !backendIDs.contains($0.key) }
            .sorted { $0.value.created < $1.value.created }
            .map { ApprovalCard.processingPlaceholder(id: $0.key, summary: $0.value.summary) }
        // quick-capture spinner cards (cleared on relaxed match or 300 s timeout)
        let captures = capturePending
            .map { ApprovalCard.processingPlaceholder(id: $0.id, summary: $0.text) }
        return captures + placeholders + backend
    }

    var visibleDebt: [DebtItem] {
        Self.sortCards((dashboard?.debt ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    var visibleTrash: [TrashItem] {
        Self.sortCards((dashboard?.trash ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    var visibleReview: [ReviewItem] {
        Self.sortCards((dashboard?.review ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    // v0.10.3 契约一: running / needs_input / completed projections — sorted
    // here so the popover and the kanban agree. RunningTask (which the running
    // column shares with queued / review-active rows) has NO deadline field →
    // "deadline" mode degrades to "newest" for these columns.
    var visibleRunning: [RunningTask] {
        Self.sortCards((dashboard?.running ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    var visibleNeedsInput: [RunningTask] {
        Self.sortCards((dashboard?.needs_input ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    var visibleCompleted: [RunningTask] {
        Self.sortCards((dashboard?.completed ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }
}

// MARK: - Popover capture draft (item 6)
//
// The popover capture text lives here (not in DashboardView @State) so
// non-SwiftUI code — the Esc key monitor, future hotkey logic — can observe
// and clear it. Only the binding moved; the popover layout is untouched.

@MainActor
final class CaptureDraft: ObservableObject {
    static let popover = CaptureDraft()
    @Published var text = ""
}

// MARK: - Slash commands (item 3) + capture history (item 5)

/// item 3: only /rec, /open, /lang count as commands — anything else that
/// starts with "/" (e.g. an absolute path "/Users/… 整理一下") is a normal
/// capture and still becomes a card. Capture/inbox JSON contract untouched:
/// commands never write inbox files.
@MainActor
enum SlashCommands {
    static func isCommand(_ text: String) -> Bool {
        text.range(of: #"^/(rec|open|lang)\b"#, options: .regularExpression) != nil
    }

    /// One-line hint shown under the input while a "/…" draft is being typed.
    static var hintLine: String {
        L("命令：/rec off|screen|audio · /open board|deps|ingest|settings|about · /lang zh|en",
          "Commands: /rec off|screen|audio · /open board|deps|ingest|settings|about · /lang zh|en")
    }

    /// Set when run() fails on an INTERNAL IO error (e.g. writing the language
    /// override) — nil on success or plain syntax errors, so the caller can
    /// tell "you typed it wrong" from "the command broke".
    static var lastErrorLine: String?

    /// Execute a recognized command. Returns false on a bad/missing argument
    /// — the caller keeps the input and shows 未识别.
    static func run(_ text: String, app: AppDelegate) -> Bool {
        lastErrorLine = nil
        let parts = text.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard let verb = parts.first else { return false }
        let arg = parts.count > 1 ? parts[1].lowercased() : ""
        switch verb {
        case "/rec":
            let modes = ["off": "off", "screen": "screen", "audio": "screen_audio"]
            guard let mode = modes[arg] else { return false }
            RecordingController.shared.setMode(mode)
            Analytics.log("slash_command", fields: ["cmd": "rec", "arg": arg])
            return true
        case "/open":
            let sections: [String: MainSection] = [
                "board": .dashboard, "deps": .deps, "ingest": .ingest,
                "settings": .settings, "about": .about]
            guard let s = sections[arg] else { return false }
            MainNav.shared.section = s
            app.openMainWindow(nil)
            Analytics.log("slash_command", fields: ["cmd": "open", "arg": arg])
            return true
        case "/lang":
            guard arg == "zh" || arg == "en" else { return false }
            // read-merge-write: SettingsIO.writeOverrides REPLACES the whole
            // file — merge the single key so every other setting survives.
            var ov = SettingsIO.readOverrides()
            ov["language"] = arg
            do { try SettingsIO.writeOverrides(ov) } catch {
                lastErrorLine = L("语言设置写入失败：", "Failed to write language setting: ")
                    + error.localizedDescription
                return false
            }
            // same apply path as 设置页保存: store + main-menu rebuild
            LanguageStore.shared.lang = arg
            app.installMainMenu()
            Analytics.log("slash_command", fields: ["cmd": "lang", "arg": arg])
            return true
        default:
            return false
        }
    }
}

/// item 5: submitted capture history — UserDefaults "captureHistory",
/// deduped, newest first, capped at 20. Shared by both capture fields;
/// slash commands are recorded too (re-typing /rec is common).
enum CaptureHistory {
    static var items: [String] {
        UserDefaults.standard.stringArray(forKey: "captureHistory") ?? []
    }

    static func push(_ text: String) {
        var h = items
        h.removeAll { $0 == text }
        h.insert(text, at: 0)
        if h.count > 20 { h = Array(h.prefix(20)) }
        UserDefaults.standard.set(h, forKey: "captureHistory")
    }
}
