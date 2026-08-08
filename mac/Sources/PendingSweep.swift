// PendingSweep.swift — 本地乐观占位（pending）的清除谓词，Foundation-only 纯函数。
//
// 从 Store.swift 的 reload() decode 块整体搬家（v0.46.x 布局风暴修复的
// review P1）：清除谓词是 (backend db × 本地 pending) 的联合函数 —— db
// 一字不变时，指纹闸门跳过期间新建的 pending 也可能一出生就满足清除条件
// （复现：同值改名的 set_title 落盘后 dashboard 内容永远不再变化，pending
// 只能等 180s 假「改名超时」）。抽成纯函数后 decode 路径与闸门跳过路径
// 共用一份谓词，并让 mac/LogicTests 第四道门能钉判例（symlink 进
// MacLogic，依赖 = shared 的 Contract/Lanes/FoldNote，禁 AppKit/SwiftUI）。

import Foundation

// MARK: - Local instant-feedback types (契约2；原 Store.swift)

/// Optimistic "the action is in flight" placeholder rendered in the TARGET
/// list right after a button press, before actd rewrites dashboard.json.
struct PendingEcho: Identifiable, Hashable {
    let id: String        // "echo-" + sourceID
    let sourceID: String  // original item id
    let title: String     // original item title (self-looked-up from dashboard)
    let target: ListKind  // which list renders it
    let source: ListKind  // where the action happened (P2-4 notice routing)
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
    enum Kind { case restore, abort, revert, unarchive, stopToReview }
    let kind: Kind
    let source: ListKind  // lane the action was taken in (P2-4 notice routing)
    let created: Date
}

/// §37: a set_title rename in flight — the new name echoes on the row at
/// once; cleared once the backend row carries it (reload), or the 180 s sweep
/// gives up with an honest notice.
struct PendingTitle {
    let title: String
    let created: Date
}

/// A 修改方向 comment in flight (blue "修改意见合并中…" line). `fingerprint`
/// snapshots the card's plan at submit time — the entry clears once the plan
/// actually CHANGED (actd folded the comment in, _fold_comment appends the
/// tag to plan), NOT on a generated_at bump: actd rewrites the dashboard
/// every pass regardless, which would drop the line before the comment file
/// was even consumed (§21bis force-merge batch-clear precedent).
struct PendingComment {
    let fingerprint: String?   // nil = card wasn't in the proposal lane
    let created: Date
}

/// merge-review 契约七: a merge_apply / merge_dismiss pressed on a suggestion
/// card. apply → the card greys out in place; dismiss → it disappears at once
/// (visibleMergeSuggestions filter). Cleared on reload once the suggestion has
/// left dashboard.merge_suggestions (actd consumed the action) — plus the
/// standard 180 s fallback in sweepTimeouts.
struct PendingMergeAction {
    enum Kind { case apply, dismiss }
    let kind: Kind
    let created: Date
}

/// 契约 §21bis: one in-flight force-merge (合并中… badge). Tracked as a BATCH so
/// the badge clears on the REAL signal — every secondary has left its lane
/// (become terminal `merged`, invisible everywhere) — NOT on a generated_at
/// bump (actd rewrites the dashboard every pass regardless of merges, which
/// would clear the badge before the merge actually lands).
struct PendingForceMerge: Identifiable {
    let id = UUID()
    let primary: String
    let secondaries: [String]
    let created: Date
    var involved: [String] { [primary] + secondaries }
}

// MARK: - pure helpers（原 Store.swift 的 static 函数，逐字搬家）

enum PendingSweep {

    /// §37: collapse internal whitespace runs exactly like actd's
    /// `" ".join(title.split())` (Character.isWhitespace covers U+3000, the
    /// full-width space Chinese IMEs produce). Review fix: the Mac used to
    /// trim ENDS only while actd collapsed internal whitespace — a double
    /// space / 全角空格 in the typed title meant the rename LANDED on disk
    /// but the echo-clear's exact-equality compare never matched, leaving a
    /// permanent FALSE 「改名超时未确认，卡片名字未变化」 notice (and a retry
    /// no-oped into the same loop). Submit path and clear compare both go
    /// through this.
    static func normalizedTitle(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    /// §37 review P1: renaming a card to the name it already shows is a no-op
    /// — actd would write the SAME display_title, the dashboard content never
    /// changes, and the pending echo has no clear signal to wait for (only
    /// the 180 s false-timeout notice). The rename UI closes the editor
    /// without writing an inbox action or planting a pending.
    static func renameIsNoOp(draft: String, current: String) -> Bool {
        normalizedTitle(draft) == normalizedTitle(current)
    }

    static func ids(in kind: ListKind, of db: Dashboard) -> Set<String> {
        switch kind {
        case .approval: return Set(db.needs_approval.map { $0.id })
        // .running spans running (incl. v0.10 queued rows) + needs_input
        case .running: return Set(db.running.map { $0.id }).union(db.needs_input.map { $0.id })
        case .review: return Set(db.review.map { $0.id })
        case .debt: return Set(db.debt.map { $0.id })
        case .trash: return Set(db.trash.map { $0.id })
        case .completed: return Set(db.completed.map { $0.id })
        case .archived: return Set(db.archived.map { $0.id })
        }
    }

    /// Which list currently holds this id (self-lookup for source recording).
    static func currentList(of id: String, in db: Dashboard) -> ListKind? {
        for kind in [ListKind.approval, .review, .debt, .trash, .running, .completed, .archived]
        where ids(in: kind, of: db).contains(id) { return kind }
        return nil
    }

    /// Backend display_title of a row (§37 rename-echo clear signal).
    static func backendDisplayTitle(of id: String, in db: Dashboard) -> String? {
        if let c = db.needs_approval.first(where: { $0.id == id }) { return c.display_title }
        if let r = db.review.first(where: { $0.id == id }) { return r.display_title }
        if let d = db.debt.first(where: { $0.id == id }) { return d.display_title }
        if let t = (db.running + db.needs_input + db.completed)
            .first(where: { $0.id == id }) { return t.display_title }
        return nil
    }

    /// §38: the projected notes_text of a card, wherever its lane carries one
    /// (needs_approval / debt / review — the fold-note surfaces). nil = the
    /// card isn't visible on a notes-carrying row right now.
    static func notesText(of id: String, in db: Dashboard) -> String? {
        if let c = db.needs_approval.first(where: { $0.id == id }) { return c.notes_text }
        if let d = db.debt.first(where: { $0.id == id }) { return d.notes_text }
        if let r = db.review.first(where: { $0.id == id }) { return r.notes_text }
        return nil
    }

    /// Plan snapshot for the pendingComment clear signal: actd's _fold_comment
    /// appends the 修改方向 tag to the card's plan, so a changed plan is the
    /// proof the comment landed. nil = the card is not in the proposal lane
    /// (comment buttons only exist there).
    static func commentFingerprint(of id: String, in db: Dashboard) -> String? {
        db.needs_approval.first { $0.id == id }.map { $0.plan.joined(separator: "\n") }
    }

    // MARK: capture ↔ backend matching (relaxed: normalize + bidirectional)

    /// Lowercase and strip whitespace/punctuation/symbols so cosmetic rewrites
    /// by the backend (quotes, dashes, spacing) don't break the match.
    private static func normalized(_ s: String) -> String {
        s.lowercased().filter { !($0.isWhitespace || $0.isPunctuation || $0.isSymbol) }
    }

    static func captureMatches(_ text: String, in db: Dashboard,
                               run: Bool = false) -> Bool {
        let p = normalized(text)
        guard !p.isEmpty else { return false }
        let pKey = String(p.prefix(10))
        // v0.34 direct-run: a filed run lands as a queued/running row (title =
        // the typed text, truncated) — clear ONLY against rows that can
        // represent THIS submit. Deliberately NOT review: a week-old 待验收
        // card with the same words would clear the placeholder into a fake
        // "launched" look while actd acked noop (nothing started); letting
        // the 180 s timeout fire with its honest notice is the correct outcome.
        let fields: [[String]] = run
            ? (db.running + db.needs_input).map { [$0.name, $0.summary ?? ""] }
            : db.needs_approval.map { [$0.title, $0.summary ?? ""] }
        for row in fields {
            for field in row {
                let t = normalized(field)
                guard !t.isEmpty else { continue }
                let tKey = String(t.prefix(10))
                if t.contains(pKey) || p.contains(tKey) { return true }
            }
        }
        return false
    }
}

// MARK: - the sweep itself

/// Store 本地 pending 状态的一次快照。`cleared(by:)` 对 backend db 跑全部
/// 清除谓词并返回新值 —— 所有清除都是纯删除（filter/remove/subtract），
/// 调用方按「集合真变小才赋值」写回 @Published（无效突变也会触发
/// objectWillChange = 全板重排，正是布局风暴修复要挡的）。
struct PendingSweepState {
    var capturePending: [CapturePending] = []
    var hiddenSticky: [String: ListKind] = [:]
    var raisingLocal: [String: RaisingEntry] = [:]
    var pendingEchoes: [PendingEcho] = []
    var pinnedLocal: Set<String> = []
    var pendingComment: [String: PendingComment] = [:]
    var returningLocal: [String: PendingReturn] = [:]
    var mergeAnalyzingLocal: [String: Date] = [:]
    var mergeForcingLocal: [PendingForceMerge] = []
    var pendingMergeActions: [String: PendingMergeAction] = [:]
    var pendingSplits: [String: Date] = [:]
    var answerPending: [String: Date] = [:]
    var pendingTitles: [String: PendingTitle] = [:]

    /// 原 reload() decode 块的逐段搬家（注释随行）。谓词只看 (db, self)：
    /// generated_at / 指纹与此无关，两条 reload 路径共用。
    func cleared(by db: Dashboard) -> PendingSweepState {
        var out = self
        // pending comments clear on the REAL signal — the card's plan
        // changed (actd folded the comment in) or the card left the
        // proposal lane. A generated_at bump alone must NOT clear: a
        // comment sent mid-pass lands in the inbox AFTER that pass's
        // drain, yet the pass still rewrites the dashboard (§21bis).
        // A dropped/failed comment never clears here → the 180 s
        // sweep fires an honest timeout notice.
        out.pendingComment = pendingComment.filter { id, entry in
            PendingSweep.commentFingerprint(of: id, in: db) == entry.fingerprint
        }
        // 契约 §21bis: a force-merge batch is done once EVERY secondary has
        // left its lane (terminal `merged` → invisible). This is the real
        // "it landed" signal; clearing on generated_at alone would drop the
        // badge on any pass's dashboard rewrite, before the merge_force
        // inbox file was even consumed. A dropped/failed request never
        // clears here → the 180 s sweep fallback fires the honest notice.
        out.mergeForcingLocal = mergeForcingLocal.filter { batch in
            !batch.secondaries.allSatisfy { PendingSweep.currentList(of: $0, in: db) == nil }
        }
        // §37 rename echoes clear on the REAL signal — the backend row
        // now carries the new display title (not on a generated_at
        // bump: actd rewrites the dashboard every pass regardless).
        // Belt+braces: compare whitespace-NORMALIZED forms — actd
        // stores the collapsed title, and an exact-equality compare
        // against a differently-spaced pending value was the false
        // 「改名超时」 loop (review finding; submit normalizes too).
        out.pendingTitles = pendingTitles.filter { id, p in
            PendingSweep.backendDisplayTitle(of: id, in: db).map(PendingSweep.normalizedTitle)
                != PendingSweep.normalizedTitle(p.title)
        }
        // sticky hides release once the id has LEFT its source list —
        // moving to ANOTHER list no longer keeps it hidden forever.
        out.hiddenSticky = hiddenSticky.filter { id, kind in
            PendingSweep.ids(in: kind, of: db).contains(id)
        }
        // return bookkeeping (restore/abort/revert) clears once its
        // sticky hide released (the id left its source list — actd
        // actually moved the card).
        out.returningLocal = returningLocal.filter { out.hiddenSticky[$0.key] != nil }
        // echoes clear once the item shows up in its target list.
        // v0.10: running[] now mixes in state=="queued" items — they
        // decode into db.running like any other row, so an approve echo
        // is replaced the moment its queued twin appears (verified: the
        // "sourceID in target list" match below needs no special case).
        out.pendingEchoes = pendingEchoes.filter {
            !PendingSweep.ids(in: $0.target, of: db).contains($0.sourceID)
        }
        // drop local raise-placeholders once the backend shows the item
        // anywhere in needs_approval (raising card or finished card).
        let backendApproval = Set(db.needs_approval.map { $0.id })
        out.raisingLocal = raisingLocal.filter { !backendApproval.contains($0.key) }
        // drop capture placeholders once a needs_approval card matches
        // (normalized, bidirectional contains on the first 10 chars);
        // direct-run placeholders match the running lane instead.
        out.capturePending = capturePending.filter { pending in
            !PendingSweep.captureMatches(pending.text, in: db, run: pending.run)
        }
        // backend confirmed permanent → local pin marker is redundant
        out.pinnedLocal = pinnedLocal
            .subtracting(db.trash.filter { $0.permanent }.map { $0.id })
        // merge-review 契约六/七: local analyzing badges drop once the
        // backend shows a suggestion covering the id (the suggestion
        // card takes over as the visible signal); apply/dismiss echoes
        // drop once their suggestion has left merge_suggestions (actd
        // consumed the action / TTL-cleaned the job file).
        let suggestions = db.merge_suggestions
        out.mergeAnalyzingLocal = mergeAnalyzingLocal.filter { id, _ in
            !suggestions.contains { $0.ids.contains(id) }
        }
        let suggestionIDs = Set(suggestions.map { $0.id })
        out.pendingMergeActions = pendingMergeActions.filter {
            suggestionIDs.contains($0.key)
        }
        // §39: an answer echo clears on the REAL signal — the card
        // left needs_input (resumed to running, or the failed delivery
        // rerouted it there with last_error + a notification). A
        // generated_at bump alone must NOT clear it (§21bis precedent).
        let blockedIDs = Set(db.needs_input.map { $0.id })
        out.answerPending = answerPending.filter { blockedIDs.contains($0.key) }
        // §38: a split clears on the REAL signal — the origin fold
        // line now carries 已拆出 in the card's projected notes_text.
        // Card not found / lane without notes → keep until the sweep.
        out.pendingSplits = pendingSplits.filter { key, _ in
            guard let sep = key.firstIndex(of: "|") else { return false }
            let cid = String(key[..<sep])
            let ts = String(key[key.index(after: sep)...])
            guard let notes = PendingSweep.notesText(of: cid, in: db) else { return true }
            return !FoldNote.parse(notes).contains {
                $0.ts == ts && $0.splitInto != nil
            }
        }
        return out
    }

    /// 「清掉了任何东西吗」—— 所有谓词都只删不改，count 比较即等价比较。
    /// Store 用它决定要不要 publish（decode 路径按集合逐个比、这里给闸门
    /// 跳过路径整体裁决），LogicTests 用它钉「无变化 = 不 publish」判例。
    func differs(from other: PendingSweepState) -> Bool {
        capturePending.count != other.capturePending.count
            || hiddenSticky.count != other.hiddenSticky.count
            || raisingLocal.count != other.raisingLocal.count
            || pendingEchoes.count != other.pendingEchoes.count
            || pinnedLocal.count != other.pinnedLocal.count
            || pendingComment.count != other.pendingComment.count
            || returningLocal.count != other.returningLocal.count
            || mergeAnalyzingLocal.count != other.mergeAnalyzingLocal.count
            || mergeForcingLocal.count != other.mergeForcingLocal.count
            || pendingMergeActions.count != other.pendingMergeActions.count
            || pendingSplits.count != other.pendingSplits.count
            || answerPending.count != other.answerPending.count
            || pendingTitles.count != other.pendingTitles.count
    }
}
