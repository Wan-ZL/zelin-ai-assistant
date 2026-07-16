// Store.swift — DashboardStore（含本地占位状态）/ CaptureDraft / SlashCommands / CaptureHistory

import AppKit
import SwiftUI
import Foundation

// MARK: - Local instant-feedback types (契约2)

// enum ListKind moved to shared/Sources/Lanes.swift (shared with iOS).

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

/// Timed-out placeholder notice (capture → yellow, raise → orange) or a
/// positive info strip (info → green, e.g. 建议上报的「已记录建议」回执).
/// `lane` = where the triggering action happened; the kanban renders each
/// notice in that column (P2-4 — an abort timeout two columns away from the
/// running lane was invisible), the popover keeps its single list.
struct LocalNotice: Identifiable, Equatable {
    enum Kind { case captureTimeout, raiseTimeout, info }
    let id: String
    let kind: Kind
    let lane: ListKind
    let text: String
    let created: Date
}

// MARK: - Pipeline health (P1-4) — slow vs broken, told apart honestly

/// Why a dead pipeline is dead — the banner turns this into actionable copy.
/// Verdict data only (no baked strings): the view renders per current language.
enum PipelineDeadReason: Equatable {
    case radarsAlive   // radar_health.json still moving → actd alone is down
    case allQuiet      // nothing in the pipeline writes anything anymore
}

/// Age tiers of state/dashboard.json (actd rewrites it every ~10 s pass):
///  - ok:    generated_at ≤ 90 s — fresh, pipeline alive
///  - stale: 90 s < age ≤ 10 min — slow or just stopped ("可能只是慢")
///  - dead:  age > 10 min — actd is not coming back on its own
///  - missing: no dashboard.json at all (fresh install / wrong home)
enum PipelineHealth: Equatable {
    case ok
    case stale(minutes: Int)
    case dead(minutes: Int, reason: PipelineDeadReason)
    case missing
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
    // until the card's plan actually changes (or the 180 s fallback)
    @Published var pendingComment: [String: PendingComment] = [:]
    // "returns to another lane" actions in flight (restore / abort_execution /
    // revert_review) → id + kind + when. These plant NO echo (契约: info strip
    // instead; restore's target lane is unknown anyway), so without this the
    // sticky hide has no timeout: actd down → the card stays in its source
    // list AND stays hidden forever. sweepTimeouts releases the hide after
    // 180 s, mirroring the echo branch. (v0.10 restoringLocal, generalized.)
    @Published var returningLocal: [String: PendingReturn] = [:]
    // merge-review 契约七: merge_review submitted → every involved card
    // carries a 「合并分析中…」corner badge. Local optimistic entry (180 s
    // fallback); reload drops it once ANY backend suggestion covers the id —
    // from then on the suggestion card itself is the visible analyzing signal
    // (isMergeAnalyzing unions both, so the badge survives the handoff).
    @Published var mergeAnalyzingLocal: [String: Date] = [:]
    // 契约 §21bis (强制合并): in-flight force merges → every involved card carries
    // a 「合并中…」badge. Unlike a merge_review request there is NO backend
    // suggestion to hand off to (force skips the AI); we clear a batch once all
    // its secondaries have left their lanes (become terminal `merged`) — the
    // real "it landed" signal — with a 180 s fallback. Kept separate from
    // mergeAnalyzingLocal so the false "分析请求超时" notice never fires here.
    @Published var mergeForcingLocal: [PendingForceMerge] = []
    // 契约七: suggestion-card accept/dismiss echoes (apply = grey in place,
    // dismiss = instant removal), keyed by suggestion id ("MS-…").
    @Published var pendingMergeActions: [String: PendingMergeAction] = [:]
    // timed-out placeholder notices (capture = yellow, raise = orange)
    @Published var notices: [LocalNotice] = []

    // v0.33 collapsed bookend strips (Mac kanban): 潜在任务 (far left) and
    // 永久性完成 (far right) render as narrow strips until expanded. Expansion
    // lives HERE — not view @State — so it survives page switches within a
    // session, and is deliberately NOT persisted: every launch starts
    // collapsed. Each strip force-opens whenever feedback lands in it (debt:
    // 暂缓 echo / raise-timeout notice; archive: unarchive info strip /
    // timeout notice) so a response to the user's own click can never appear
    // inside an invisible column.
    @Published var backlogStripExpanded = false
    @Published var archiveStripExpanded = false
    // P1-4: dashboard freshness verdict, recomputed on every refresh tick —
    // the file being frozen (reload short-circuit) is exactly the signal.
    @Published var pipelineHealth: PipelineHealth = .ok

    // raw bytes of the last successfully-read dashboard.json — reload
    // short-circuits (no publish) when the file hasn't changed.
    private var lastRawData: Data?
    private var lastGeneratedAt: String?

    // MARK: board motion (v0.43 手感 — display-only, BoardDiff/BoardMotion.swift)

    /// One-shot motion event for the kanban flight layer, published in the
    /// SAME transaction as the lane change that caused it (row transitions
    /// must see both in one render pass). Auto-clears ~0.8 s later so a row
    /// inserted by anything else (strip expand, search) never re-triggers a
    /// stale deal-in. nil ⇒ nothing is animating.
    @Published private(set) var boardMotion: BoardMotionEvent?
    /// Previous per-lane snapshot (BoardDiff baseline). Maintained even while
    /// the 看板动画 pref is off / Reduce Motion is on (only the diff+publish
    /// is skipped then — the view gates consumption too, belt and suspenders),
    /// so re-enabling never animates a stale mega-diff.
    private var lastBoardLanes: [BoardLaneList]?
    private var boardMotionSeq = 0

    func reload() {
        // local placeholder timeouts tick on every refresh, even when the
        // dashboard file itself is unchanged (actd down = file frozen).
        sweepTimeouts()
        // P1-4: re-verdict on EVERY exit path, including the unchanged-bytes
        // short-circuit below — a frozen file is what "stale" looks like.
        defer { updateHealth() }

        let path = AppPaths.dashboardPath
        guard FileManager.default.fileExists(atPath: path) else {
            if dashboard != nil || !missing || loadError != nil {
                dashboard = nil
                missing = true
                loadError = nil
                lastRawData = nil
                lastRefresh = Date()
                // v0.43: board gone → drop the motion baseline, so the next
                // dashboard.json appearing counts as a first load (no animation).
                lastBoardLanes = nil
                boardMotion = nil
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
            // 例外：丢行提示不清 —— 字节没变说明坏行还躺在文件里
            if loadError != nil && (dashboard?.decodeDrops.isEmpty ?? true) { loadError = nil }
            return
        }
        do {
            let db = try JSONDecoder().decode(Dashboard.self, from: data)
            lastRawData = data
            withAnimation(.easeOut(duration: 0.2)) {
                dashboard = db
                missing = false
                // 行级 lenient 解码（Contract.swift）跳过的坏行必须可观测：
                // 好行照常展示，banner 说清丢了哪些行 —— 绝不静默丢数据。
                loadError = db.decodeDrops.isEmpty ? nil
                    : L("dashboard.json 有 \(db.decodeDrops.count) 行损坏已跳过（其余照常显示）: ",
                        "dashboard.json: skipped \(db.decodeDrops.count) corrupt row(s), the rest render normally: ")
                        + db.decodeDrops.joined(separator: ", ")
                // one-shot hides clear when the backend has actually
                // regenerated (generated_at changed); missing field →
                // legacy behavior (clear on any reload).
                if let gen = db.generated_at, !gen.isEmpty {
                    if gen != lastGeneratedAt {
                        lastGeneratedAt = gen
                        hiddenOnce.removeAll()
                    }
                } else {
                    hiddenOnce.removeAll()
                }
                // pending comments clear on the REAL signal — the card's plan
                // changed (actd folded the comment in) or the card left the
                // proposal lane. A generated_at bump alone must NOT clear: a
                // comment sent mid-pass lands in the inbox AFTER that pass's
                // drain, yet the pass still rewrites the dashboard (§21bis).
                // A dropped/failed comment never clears here → the 180 s
                // sweep fires an honest timeout notice.
                pendingComment = pendingComment.filter { id, entry in
                    Self.commentFingerprint(of: id, in: db) == entry.fingerprint
                }
                // 契约 §21bis: a force-merge batch is done once EVERY secondary has
                // left its lane (terminal `merged` → invisible). This is the real
                // "it landed" signal; clearing on generated_at alone would drop the
                // badge on any pass's dashboard rewrite, before the merge_force
                // inbox file was even consumed. A dropped/failed request never
                // clears here → the 180 s sweep fallback fires the honest notice.
                mergeForcingLocal.removeAll { batch in
                    batch.secondaries.allSatisfy { currentList(of: $0) == nil }
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
                // (normalized, bidirectional contains on the first 10 chars);
                // direct-run placeholders match the running lane instead.
                capturePending.removeAll { pending in
                    Self.captureMatches(pending.text, in: db, run: pending.run)
                }
                // backend confirmed permanent → local pin marker is redundant
                pinnedLocal.subtract(db.trash.filter { $0.permanent }.map { $0.id })
                // merge-review 契约六/七: local analyzing badges drop once the
                // backend shows a suggestion covering the id (the suggestion
                // card takes over as the visible signal); apply/dismiss echoes
                // drop once their suggestion has left merge_suggestions (actd
                // consumed the action / TTL-cleaned the job file).
                let suggestions = db.merge_suggestions
                mergeAnalyzingLocal = mergeAnalyzingLocal.filter { id, _ in
                    !suggestions.contains { $0.ids.contains(id) }
                }
                let suggestionIDs = Set(suggestions.map { $0.id })
                pendingMergeActions = pendingMergeActions.filter {
                    suggestionIDs.contains($0.key)
                }
                // v0.43: diff the freshly-applied snapshot against the previous
                // one — must be the LAST line of this block so the lane lists
                // it reads are final for this pass.
                updateBoardMotion()
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
        // capture placeholders: 300 s → yellow notice (analysis can be slow).
        // Direct-run captures (v0.34) involve no LLM — actd queues them on the
        // next ~10 s pass — so they give up at the echo-class 180 s instead.
        // P1-4: pipeline not ok → skip; the placeholder honestly says "queued
        // until the pipeline runs" (Cards.processingBody) and a timeout notice
        // would be a false alarm. updateHealth re-arms `created` on recovery.
        let expiredCaptures = pipelineHealth == .ok
            ? capturePending.filter {
                now.timeIntervalSince($0.created) > ($0.run ? 180 : 300)
            }
            : []
        // raise placeholders: 180 s → orange notice + release the sticky hide
        let expiredRaises = raisingLocal.filter { now.timeIntervalSince($0.value.created) > 180 }
        // echoes: 180 s → give up; release the sticky hide so the card returns
        let expiredEchoes = pendingEchoes.filter { now.timeIntervalSince($0.created) > 180 }
        // comment fallback (plan never changed): 180 s
        let expiredComments = pendingComment.filter { now.timeIntervalSince($0.value.created) > 180 }
        // returns (restore/abort/revert): 180 s without leaving the source
        // list → give up, release the hide
        let expiredReturns = returningLocal.filter { now.timeIntervalSince($0.value.created) > 180 }
        // merge-review 契约七: analyzing badges / apply-dismiss echoes give up
        // after 180 s without backend movement (suggestion never appeared /
        // never left merge_suggestions)
        let expiredMergeBadges = mergeAnalyzingLocal.filter {
            now.timeIntervalSince($0.value) > 180
        }
        let expiredMergeActions = pendingMergeActions.filter {
            now.timeIntervalSince($0.value.created) > 180
        }
        // 契约 §21bis: force-merge batches give up after 180 s without their
        // secondaries leaving their lanes (the merge never landed — actd down /
        // request dropped as invalid).
        let expiredForceBadges = mergeForcingLocal.filter {
            now.timeIntervalSince($0.created) > 180
        }
        // notices themselves fade after 120 s
        let expiredNotices = notices.filter { now.timeIntervalSince($0.created) > 120 }
        guard !expiredCaptures.isEmpty || !expiredRaises.isEmpty || !expiredEchoes.isEmpty
            || !expiredComments.isEmpty || !expiredReturns.isEmpty
            || !expiredMergeBadges.isEmpty || !expiredMergeActions.isEmpty
            || !expiredForceBadges.isEmpty
            || !expiredNotices.isEmpty else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            for c in expiredCaptures {
                capturePending.removeAll { $0.id == c.id }
                // direct-run: after 180 s with no queued row the task really
                // did NOT start — orange, and say so (audit honesty standard);
                // a proposal capture is usually just slow analysis — yellow.
                // The run copy names BOTH causes: actd acks noop when the line
                // matched an existing 待验收/提案 card (fold, nothing runs) —
                // indistinguishable from a dead backend at this distance.
                notices.append(LocalNotice(
                    id: "notice-" + c.id,
                    kind: c.run ? .raiseTimeout : .captureTimeout,
                    lane: c.run ? .running : .approval,
                    text: c.run
                        ? L("「\(String(c.text.prefix(20)))」任务没有开始——可能这句话命中了已有的卡（看看待验收/提案），或后台没在跑（检查 actd）",
                            "\"\(String(c.text.prefix(20)))\" did not start — the line may have matched an existing card (check Review/Proposals), or the backend isn't running (check actd)")
                        : L("分析比平时慢，卡片稍后会自动出现；一直没有就打开「依赖检查」页并查看 state/actd.log",
                            "Analysis is slower than usual — the card should still appear; if it never does, open the Dependencies page and check state/actd.log"),
                    created: now))
            }
            for (id, entry) in expiredRaises {
                raisingLocal.removeValue(forKey: id)
                hiddenSticky.removeValue(forKey: id)
                // lane .debt: 研究并提议 lives on the debt card, and that's
                // where the card resurfaces for the suggested retry.
                notices.append(LocalNotice(
                    id: "notice-raise-" + id, kind: .raiseTimeout, lane: .debt,
                    text: L("「\(String(entry.summary.prefix(20)))」研究提案超时，请重试",
                            "Research proposal for \"\(String(entry.summary.prefix(20)))\" timed out — try again"),
                    created: now))
                // v0.33: the notice lands in the (possibly collapsed) backlog
                // strip — force-open so the retry hint is visible.
                backlogStripExpanded = true
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
                    lane: e.source,   // the card un-hides back in its source lane
                    text: stillExists
                        ? L("后台响应超时，卡片已恢复可操作",
                            "Backend timed out — the card is interactive again")
                        : L("「\(label)」已提交但后台超时未确认，请检查 actd 是否在运行",
                            "\"\(label)\" was submitted but the backend never confirmed — check that actd is running"),
                    created: now))
                // v0.33: a debt-lane timeout notice (trash/archive echo from a
                // DebtRow) — and the card silently un-hiding there — must not
                // land inside the collapsed backlog strip; force-open it.
                if e.source == .debt { backlogStripExpanded = true }
            }
            for (id, _) in expiredComments {
                pendingComment.removeValue(forKey: id)
                // the blue line vanishing must not be silent — the comment
                // demonstrably never landed (plan unchanged), say so honestly
                // like every other expiry path does.
                let noticeID = "notice-comment-" + id
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: .approval,
                    text: L("修改意见超时未合并，卡片未变化，请重试（检查 actd 是否在运行）",
                            "Comment timed out before merging — the card is unchanged, try again (check that actd is running)"),
                    created: now))
            }
            // merge-review 契约七: badges of one request expire together →
            // one grouped notice in the approval lane (where the suggestion
            // card would have appeared).
            if !expiredMergeBadges.isEmpty {
                for (id, _) in expiredMergeBadges {
                    mergeAnalyzingLocal.removeValue(forKey: id)
                }
                let noticeID = "notice-merge-review"
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: .approval,
                    text: L("合并分析请求超时，后台未生成建议卡，请重试（检查 actd 是否在运行）",
                            "Merge analysis request timed out — no suggestion card appeared, try again (check that actd is running)"),
                    created: now))
            }
            for (id, _) in expiredMergeActions {
                pendingMergeActions.removeValue(forKey: id)
                // suggestion card un-greys / reappears, operable again
                let noticeID = "notice-merge-" + id
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: .approval,
                    text: L("合并建议操作超时，卡片已恢复可操作（检查 actd 是否在运行）",
                            "Merge-suggestion action timed out — the card is interactive again (check that actd is running)"),
                    created: now))
            }
            // 契约 §21bis: force-merge batches that timed out expire together →
            // one grouped notice (the merge never landed — actd likely down).
            if !expiredForceBadges.isEmpty {
                let expiredIDs = Set(expiredForceBadges.map { $0.id })
                mergeForcingLocal.removeAll { expiredIDs.contains($0.id) }
                let noticeID = "notice-merge-force"
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: .approval,
                    text: L("强制合并未确认，卡片未变化，请重试（检查 actd 是否在运行）",
                            "Force-merge never confirmed — nothing changed, try again (check that actd is running)"),
                    created: now))
            }
            for (id, entry) in expiredReturns {
                returningLocal.removeValue(forKey: id)
                hiddenSticky.removeValue(forKey: id)   // source card returns, operable again
                let noticeID = "notice-return-" + id
                notices.removeAll { $0.id == noticeID }   // replace the info notice
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: entry.source,
                    text: Self.returnTimeoutText(entry.kind),
                    created: now))
                // v0.33: an unarchive timeout notice — and the card silently
                // reappearing there — must not hide inside the collapsed
                // archive strip; force-open it (backlog strip precedent).
                if entry.source == .archived { archiveStripExpanded = true }
            }
            for n in expiredNotices { notices.removeAll { $0.id == n.id } }
            // v0.43: expiries un-hide cards / drop placeholders — lane lists
            // changed, so the flight layer gets its event in this transaction.
            updateBoardMotion()
        }
    }

    /// Per-kind timeout wording for the shared pending-return mechanism.
    private static func returnTimeoutText(_ kind: PendingReturn.Kind) -> String {
        switch kind {
        case .restore:
            return L("恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）",
                     "Restore timed out — the card is back in the trash, try again (check that actd is running)")
        case .abort:
            // v0.21 起按钮叫「停止」→「退回提案/Discard & re-propose」——文案跟按钮走
            return L("退回提案超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）",
                     "Discard & re-propose timed out — the card is still in Running, try again (check that actd is running)")
        case .revert:
            return L("退回待验收超时，卡片仍在「阶段性完成」列，可重试（检查 actd 是否在运行）",
                     "Back-to-review timed out — the card is still in Done for now, try again (check that actd is running)")
        case .unarchive:
            return L("放回看板超时，卡片仍在「永久性完成」区，可重试（检查 actd 是否在运行）",
                     "Put back timed out — the card is still in Done for good, try again (check that actd is running)")
        case .stopToReview:
            return L("去待验收超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）",
                     "Stop-to-review timed out — the card is still in Running, try again (check that actd is running)")
        }
    }

    // MARK: board motion diffing (v0.43 手感)

    /// Diff the CURRENT per-lane lists against the previous snapshot and, when
    /// something moved/appeared/left, publish a one-shot BoardMotionEvent for
    /// the kanban flight layer. Called as the last line of every mutating
    /// withAnimation block (reload / sweepTimeouts / applyAction / hide /
    /// beginRaising / beginCapture) so the event lands in the SAME transaction
    /// as the lane change — row transitions read both in one render pass.
    /// First snapshot (nil baseline) records the baseline and animates nothing.
    private func updateBoardMotion() {
        let lanes = currentBoardLanes()
        defer { lastBoardLanes = lanes }
        guard lastBoardLanes != nil else { return }
        // Toggle-off / Reduce Motion pays nothing past this line: no diff, no
        // publish (an extra objectWillChange per mutation), no delayed
        // nil-clear. The baseline above DOES keep updating, so re-enabling
        // mid-session never animates a stale mega-diff.
        guard BoardMotionPolicy.animationsEnabled else { return }
        let diff = BoardDiff.compute(previous: lastBoardLanes, current: lanes)
        guard !diff.isEmpty else { return }
        boardMotionSeq += 1
        let seq = boardMotionSeq
        boardMotion = BoardMotionEvent(seq: seq, diff: diff)
        // one-shot: clear after the flights are done (≤ 0.05 launch + 6×0.04
        // stagger + ~0.42 flight) so later unrelated row insertions — strip
        // expand, search-filter edits — can never match a stale deal-in.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
            guard let self, self.boardMotion?.seq == seq else { return }
            self.boardMotion = nil
        }
    }

    /// The per-lane id lists as the BOARD renders them — the UNFILTERED
    /// visible* projections plus placeholder/echo rows, echoes riding under
    /// their sourceID so a button press diffs as the move it represents (and
    /// the later snapshot that swaps echo→real row diffs as no change).
    /// Search (board*) is deliberately NOT applied: query edits change what
    /// renders, but they are not causality and must never fire motion.
    /// Trash is off-board — ids leaving for it surface as removals.
    private func currentBoardLanes() -> [BoardLaneList] {
        [
            BoardLaneList(lane: "debt",
                          ids: echoes(for: .debt).map { $0.sourceID }
                              + visibleDebt.map { $0.id }),
            BoardLaneList(lane: "approval", ids: visibleApprovals.map { $0.id }),
            BoardLaneList(lane: "running",
                          ids: visibleRunCaptures.map { $0.id }
                              + echoes(for: .running).map { $0.sourceID }
                              + visibleNeedsInput.map { $0.id }
                              + visibleRunning.map { $0.id }),
            BoardLaneList(lane: "review", ids: visibleReview.map { $0.id }),
            BoardLaneList(lane: "completed",
                          ids: echoes(for: .completed).map { $0.sourceID }
                              + visibleCompleted.map { $0.id }),
            BoardLaneList(lane: "archived",
                          ids: echoes(for: .archived).map { $0.sourceID }
                              + visibleArchived.map { $0.id }),
        ]
    }

    // MARK: pipeline health (P1-4)

    private static let staleAfter: TimeInterval = 90    // popover footer 同阈值
    private static let deadAfter: TimeInterval = 600    // actd 每 ~10s 一写；10 分钟没写不会自己好

    private func updateHealth() {
        let verdict = computeHealth()
        guard verdict != pipelineHealth else { return }
        let recovered = verdict == .ok && pipelineHealth != .ok
        pipelineHealth = verdict
        if recovered {
            // pipeline is back: pending captures kept waiting through the
            // outage — restart their 300 s window so sweepTimeouts doesn't
            // fire a timeout notice the instant health returns.
            capturePending = capturePending.map {
                CapturePending(id: $0.id, text: $0.text, created: Date(), run: $0.run)
            }
        }
    }

    private func computeHealth() -> PipelineHealth {
        guard let db = dashboard else { return missing ? .missing : .ok }
        // legacy dashboards without generated_at: no verdict (footer degrades
        // to the refresh stamp the same way)
        guard let gen = FreshnessLabel.parseISO(db.generated_at) else { return .ok }
        let age = Date().timeIntervalSince(gen)
        if age <= Self.staleAfter { return .ok }
        let mins = max(1, Int(age / 60))
        if age <= Self.deadAfter { return .stale(minutes: mins) }
        return .dead(minutes: mins,
                     reason: Self.radarsRecentlyAlive() ? .radarsAlive : .allQuiet)
    }

    /// radar_health.json is rewritten on every gmail/slack radar attempt
    /// (contract E) — a fresh mtime while the dashboard is old means the
    /// scheduled half of the pipeline still runs and actd alone is down.
    private static func radarsRecentlyAlive() -> Bool {
        let path = AppPaths.stateRoot + "/state/radar_health.json"
        guard let mtime = (try? FileManager.default.attributesOfItem(atPath: path))?[
            .modificationDate] as? Date else { return false }
        return Date().timeIntervalSince(mtime) < 40 * 60   // radars poll every ≤30 min
    }

    // MARK: applyAction — the ONE entry point for card-button actions (契约2)

    /// Wave-2 wiring target: AppDelegate.submit() calls this after the inbox
    /// write succeeds. Policy is frozen — see the implementation plan.
    func applyAction(_ action: String, id: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            switch action {
            case "approve":
                hideSticky(id, from: .approval)
                addEcho(id: id, target: .running, source: .approval,
                        label: L("启动中…", "Starting…"))
            case "rework":
                hideSticky(id, from: .review)
                addEcho(id: id, target: .running, source: .review,
                        label: L("打回处理中…", "Sending back…"))
            case "accept":
                hideSticky(id, from: .review)
                addEcho(id: id, target: .completed, source: .review,
                        label: L("验收确认中…", "Accepting…"))
            case "reject", "trash":
                let src = currentList(of: id)
                hideSticky(id, from: src)
                // trash echo counts (visibleTrashCount) but renders no card
                addEcho(id: id, target: .trash, source: src ?? .approval, label: "")
            case "defer":
                // v0.18 defer (displayed as 暂缓/Later since v0.33): proposal
                // returns to the backlog (detected) with its plan intact.
                // Fixed, known target — a real echo in the debt lane (both
                // kanban and popover already render debtEchoes), unlike
                // restore's any-lane info strip.
                hideSticky(id, from: .approval)
                addEcho(id: id, target: .debt, source: .approval,
                        label: L("暂缓中…", "Moving to backlog…"))
            case "restore":
                // no echo: the card may return to ANY lane (its previous
                // state), so a fixed-target placeholder would often be wrong.
                // sticky-hide from trash + an info notice instead; returningLocal
                // gives the hide a 180 s timeout (sweepTimeouts) — without it an
                // unresponsive actd would keep the card hidden forever.
                beginReturn(id, from: .trash, kind: .restore,
                            info: L("恢复中，卡片将回到原状态列",
                                    "Restoring — the card returns to its previous lane"))
            case "archive":
                // v0.20 card-lifecycle: seal a delivered (阶段性完成) or backlog
                // (潜在任务) card into the archive — reversible, no confirm. Fixed,
                // known target: sticky-hide from whichever lane holds it and
                // plant an echo in the archive section (renders no card, but
                // keeps visibleArchivedCount honest, mirroring trash).
                let src = dashboard.flatMap { db in
                    [ListKind.completed, .debt].first { ids(in: $0, of: db).contains(id) }
                }
                hideSticky(id, from: src)
                addEcho(id: id, target: .archived, source: src ?? .completed, label: "")
            case "unarchive":
                // v0.20 unarchive (displayed as 放回看板/Put back since v0.33):
                // like restore, the card returns to its prev_status (any
                // lane), so no fixed-target echo. sticky-hide from the archive
                // + info strip; returningLocal arms the 180 s timeout so an
                // unresponsive actd can't hide it forever.
                beginReturn(id, from: .archived, kind: .unarchive,
                            info: L("放回看板中，卡片将回到原状态列",
                                    "Putting back — the card returns to its previous lane"))
            case "done_external":
                // Zelin finished it outside the system → DELIVERED; the button
                // now also lives on running-lane rows (queued/working/blocked/
                // needs_input/review-active), so the sticky-hide source is
                // whichever ACTIONABLE lane shows the card right now: approval
                // (reject dialog), review, or running (incl. needs_input —
                // ids(in: .running) unions both). Deliberately NOT currentList:
                // that also scans completed, and hiding from there would bury
                // the real delivered card under its own echo.
                let src = dashboard.flatMap { db in
                    [ListKind.approval, .review, .running]
                        .first { ids(in: $0, of: db).contains(id) }
                }
                hideSticky(id, from: src)
                addEcho(id: id, target: .completed, source: src ?? .approval,
                        label: L("已办完", "done outside"))
            case "abort_execution":
                // v0.10.2: stop the run, card returns to 待审批 (CARD_SENT) —
                // same pending+timeout mechanism as restore (契约: 信息条).
                beginReturn(id, from: .running, kind: .abort,
                            info: L("停止中，卡片将回到提案列",
                                    "Stopping — card returns to Proposals"))
            case "stop_to_review":
                // v0.21: stop the agent but KEEP what it produced → 待验收.
                // Same pending+timeout mechanism as abort (契约: 信息条); only
                // the target lane (and thus the wording) differs.
                beginReturn(id, from: .running, kind: .stopToReview,
                            info: L("停止中，卡片将去待验收",
                                    "Stopping — card moves to Review"))
            case "revert_review":
                // v0.10.2: delivered → back to REVIEW for re-acceptance.
                beginReturn(id, from: .completed, kind: .revert,
                            info: L("退回中，卡片将回到待验收",
                                    "Reverting to review"))
            case "pin":
                pinnedLocal.insert(id)   // no hide — badge flips in place
            case "comment":
                // no hide — blue in-place line; cleared once the card's plan
                // actually changes (the comment landed), 180 s sweep fallback
                pendingComment[id] = PendingComment(
                    fingerprint: dashboard.flatMap { Self.commentFingerprint(of: id, in: $0) },
                    created: Date())
            case "merge_apply":
                // merge-review 契约七: 接受 — the suggestion card greys out in
                // place until actd consumes the job. MS- ids live in
                // merge_suggestions, not in any card list → no hide/echo.
                pendingMergeActions[id] = PendingMergeAction(kind: .apply, created: Date())
            case "merge_dismiss":
                // 契约七: 取消 — the suggestion card disappears at once
                // (visibleMergeSuggestions filters it out).
                pendingMergeActions[id] = PendingMergeAction(kind: .dismiss, created: Date())
            default:
                // e.g. "raise": optimistic sticky hide from wherever it lives
                // (the raisingLocal placeholder is planted by beginRaising)
                hideSticky(id, from: currentList(of: id))
            }
            // v0.43: the optimistic hide/echo IS the causal moment — diff now
            // so the flight launches on the click, not on the next snapshot.
            updateBoardMotion()
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
        returningLocal[id] = PendingReturn(kind: kind, source: source, created: Date())
        let noticeID = "notice-return-" + id
        notices.removeAll { $0.id == noticeID }
        notices.append(LocalNotice(
            id: noticeID, kind: .captureTimeout, lane: source, text: info,
            created: Date()))
        // v0.33: unarchive's info strip lands in the (possibly collapsed)
        // archive strip — a response to the user's own click can never appear
        // inside an invisible column; force-open it (backlog strip precedent).
        if source == .archived { archiveStripExpanded = true }
    }

    private func addEcho(id: String, target: ListKind, source: ListKind, label: String) {
        pendingEchoes.removeAll { $0.sourceID == id }
        pendingEchoes.append(PendingEcho(
            id: "echo-" + id, sourceID: id, title: title(of: id),
            target: target, source: source, label: label, created: Date()))
        // v0.33: an echo landing in the collapsed backlog strip (暂缓中…)
        // must be visible — force-open the strip.
        if target == .debt { backlogStripExpanded = true }
    }

    /// Which list currently holds this id (self-lookup for source recording).
    private func currentList(of id: String) -> ListKind? {
        guard let db = dashboard else { return nil }
        for kind in [ListKind.approval, .review, .debt, .trash, .running, .completed, .archived]
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
        case .archived: return Set(db.archived.map { $0.id })
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

    /// Public id → human title resolver (all lanes incl. 潜在任务/debt), used by
    /// ForceMergeSheet's primary picker so the user never has to choose between
    /// bare R-ids. Falls back to the id itself when the card isn't on the board.
    func cardTitle(_ id: String) -> String { title(of: id) }

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
            updateBoardMotion()
        }
    }

    func beginRaising(_ id: String, summary: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            raisingLocal[id] = RaisingEntry(summary: summary, created: Date())
            updateBoardMotion()
        }
    }

    /// `run` = direct-run capture (v0.34, mode:"run"): the placeholder lands in
    /// the 运行中 lane and clears against running rows instead of proposals.
    func beginCapture(_ text: String, run: Bool = false) {
        withAnimation(.easeOut(duration: 0.2)) {
            capturePending.append(
                CapturePending(id: "capture-" + UUID().uuidString, text: text,
                               created: Date(), run: run))
            updateBoardMotion()
        }
    }

    /// merge-review 契约七: the merge_review inbox write succeeded — badge
    /// every involved card with 合并分析中… (local optimistic; cleared on
    /// reload once a backend suggestion covers the id, or after 180 s).
    /// ONLY call site: AppDelegate.submitMergeReview, after the IO succeeded.
    func beginMergeReview(ids: [String]) {
        withAnimation(.easeOut(duration: 0.2)) {
            let now = Date()
            for id in ids { mergeAnalyzingLocal[id] = now }
        }
    }

    /// 契约 §21bis: the merge_force inbox write succeeded — badge every involved
    /// card with 合并中… (optimistic; cleared once the secondaries land terminal
    /// `merged`, or after 180 s). ONLY call site: AppDelegate.submitMergeForce,
    /// after the IO succeeded. `secondaries` must be non-empty (the caller
    /// guarantees ≥2 distinct ids with primary ∈ ids).
    func beginMergeForce(primary: String, secondaries: [String]) {
        guard !secondaries.isEmpty else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            mergeForcingLocal.append(PendingForceMerge(
                primary: primary, secondaries: secondaries, created: Date()))
        }
    }

    /// 建议上报: the feedback inbox write succeeded → optimistic green
    /// 「已记录建议，感谢」info strip in the proposal lane (fixed id — a
    /// second submit replaces, not stacks). Fades with the standard 120 s
    /// notice sweep. ONLY call site: AppDelegate.submitFeedback.
    func noteFeedbackRecorded() {
        withAnimation(.easeOut(duration: 0.2)) {
            let noticeID = "notice-feedback"
            notices.removeAll { $0.id == noticeID }
            notices.append(LocalNotice(
                id: noticeID, kind: .info, lane: .approval,
                text: L("已记录建议，感谢", "Feedback recorded"),
                created: Date()))
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

    /// Plan snapshot for the pendingComment clear signal: actd's _fold_comment
    /// appends the 修改方向 tag to the card's plan, so a changed plan is the
    /// proof the comment landed. nil = the card is not in the proposal lane
    /// (comment buttons only exist there).
    private static func commentFingerprint(of id: String, in db: Dashboard) -> String? {
        db.needs_approval.first { $0.id == id }.map { $0.plan.joined(separator: "\n") }
    }

    private static func captureMatches(_ text: String, in db: Dashboard,
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
        // quick-capture spinner cards (cleared on relaxed match or 300 s
        // timeout); direct-run captures echo in the running lane instead.
        let captures = capturePending
            .filter { !$0.run }
            .map { ApprovalCard.processingPlaceholder(id: $0.id, summary: $0.text) }
        return captures + placeholders + backend
    }

    /// v0.34 direct-run placeholders — grey queued rows pinned at the top of
    /// the 运行中 lane until the backend surfaces the matching queued/running
    /// card (or the 180 s sweep gives up, honestly). Like the proposal-lane
    /// processing prefix, these never participate in search filtering.
    var visibleRunCaptures: [CapturePending] {
        capturePending.filter { $0.run }
    }

    var visibleDebt: [DebtItem] {
        Self.sortCards((dashboard?.debt ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    var visibleTrash: [TrashItem] {
        Self.sortCards((dashboard?.trash ?? []).filter { !isHidden($0.id) }, id: { $0.id })
    }

    // v0.20 card-lifecycle: archived items (sealed, off-board). dashboard.py
    // already ships them newest-first by archived_at, so keep backend order
    // rather than re-sorting by id (unlike trash, archive is a chronological
    // browse view); still honor the sticky-hide of an in-flight unarchive.
    var visibleArchived: [ArchivedItem] {
        (dashboard?.archived ?? []).filter { !isHidden($0.id) }
    }

    /// Archive count including in-flight archive echoes (rendered nowhere).
    var visibleArchivedCount: Int {
        visibleArchived.count + echoes(for: .archived).count
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

    // MARK: merge suggestions (merge-review 契约六/七)

    /// Suggestion cards for the kanban 待审批列顶 and the popover mirror.
    /// analyzing/done/failed all render (契约六 — dismissed never reaches the
    /// dashboard); a dismiss-in-flight one vanishes at once, an apply-in-flight
    /// one stays and greys out (mergeApplyPending). Backend order is kept.
    var visibleMergeSuggestions: [MergeSuggestion] {
        (dashboard?.merge_suggestions ?? []).filter {
            pendingMergeActions[$0.id]?.kind != .dismiss
        }
    }

    /// True while an accept (merge_apply) is in flight on this suggestion —
    /// MergeSuggestionCard renders its greyed 乐观回显 off this.
    func mergeApplyPending(_ suggestionID: String) -> Bool {
        pendingMergeActions[suggestionID]?.kind == .apply
    }

    /// 契约七 角标: this card is part of a requested merge analysis — either
    /// the local optimistic entry (just submitted, backend not yet visible)
    /// or a live backend suggestion still "analyzing" that covers the id.
    func isMergeAnalyzing(_ id: String) -> Bool {
        if mergeAnalyzingLocal[id] != nil { return true }
        return (dashboard?.merge_suggestions ?? []).contains {
            $0.status == "analyzing" && $0.ids.contains(id)
        }
    }

    /// 契约 §21bis 角标: this card is part of an in-flight force merge (primary
    /// or a not-yet-merged secondary). Optimistic — cleared once the batch's
    /// secondaries land terminal `merged`, or after the 180 s sweep.
    func isMergeForcing(_ id: String) -> Bool {
        mergeForcingLocal.contains { $0.primary == id || $0.secondaries.contains(id) }
    }

    // MARK: board search (看板搜索过滤 — board* projections over visible*)

    /// Kanban header search box text. Non-empty → the board* projections
    /// below filter every lane in real time; "" (or whitespace) = passthrough.
    /// Lives in the store per the visible* projection pattern; the POPOVER
    /// deliberately keeps reading visible* — search is a board-only
    /// affordance, and KanbanView clears the query onDisappear so a stale
    /// filter can never silently hide cards elsewhere.
    @Published var boardQuery: String = ""

    /// Normalized needle ("" = filtering off).
    private var boardNeedle: String {
        boardQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// Case-insensitive substring match over one card's searchable text —
    /// 词表冻结: id + title/summary (scalar) + dod/plan (lists). Kept private
    /// so every lane filters by exactly the same rule.
    private static func searchMatches(_ needle: String, id: String,
                                      texts: [String?], lists: [[String]?]) -> Bool {
        if id.lowercased().contains(needle) { return true }
        for t in texts where t?.lowercased().contains(needle) == true { return true }
        for list in lists where list?.contains(
            where: { $0.lowercased().contains(needle) }) == true { return true }
        return false
    }

    /// visibleApprovals + search. 占位卡不参与过滤隐藏: the grey processing
    /// prefix (captures + raise placeholders, `processing == true`) always
    /// rides through — hiding an in-flight submit behind a filter would read
    /// as a lost capture. (建议卡 likewise stay unfiltered — they never pass
    /// through this projection at all; KanbanView keeps visibleMergeSuggestions.)
    var boardApprovals: [ApprovalCard] {
        let q = boardNeedle
        guard !q.isEmpty else { return visibleApprovals }
        return visibleApprovals.filter {
            $0.processing || Self.searchMatches(q, id: $0.id,
                                                texts: [$0.title, $0.summary],
                                                lists: [$0.dod, $0.plan])
        }
    }

    var boardRunning: [RunningTask] { searchTasks(visibleRunning) }
    var boardNeedsInput: [RunningTask] { searchTasks(visibleNeedsInput) }
    var boardCompleted: [RunningTask] { searchTasks(visibleCompleted) }

    /// Shared RunningTask filter (running / needs_input / completed reuse the
    /// struct); `name` is the row's title-equivalent field.
    private func searchTasks(_ tasks: [RunningTask]) -> [RunningTask] {
        let q = boardNeedle
        guard !q.isEmpty else { return tasks }
        return tasks.filter {
            Self.searchMatches(q, id: $0.id, texts: [$0.name, $0.summary],
                               lists: [$0.dod, $0.plan])
        }
    }

    var boardReview: [ReviewItem] {
        let q = boardNeedle
        guard !q.isEmpty else { return visibleReview }
        return visibleReview.filter {
            Self.searchMatches(q, id: $0.id, texts: [$0.name, $0.summary],
                               lists: [$0.dod, $0.plan])
        }
    }

    /// 潜在任务 (backlog, dashboard key `debt`) — DebtItem has no dod/plan fields.
    var boardDebt: [DebtItem] {
        let q = boardNeedle
        guard !q.isEmpty else { return visibleDebt }
        return visibleDebt.filter {
            Self.searchMatches(q, id: $0.id, texts: [$0.title, $0.summary],
                               lists: [])
        }
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
