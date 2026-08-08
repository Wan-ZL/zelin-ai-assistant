// Store.swift — DashboardStore（含本地占位状态）/ CaptureDraft / SlashCommands / CaptureHistory

import AppKit
import SwiftUI
import Foundation

// MARK: - Local instant-feedback types (契约2)

// enum ListKind moved to shared/Sources/Lanes.swift (shared with iOS).
// PendingEcho / RaisingEntry / PendingReturn / PendingTitle / PendingComment /
// PendingMergeAction / PendingForceMerge 与全部清除谓词 moved to
// PendingSweep.swift（Foundation-only，LogicTests 第四道门可测）。

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
    // §38 拆成新卡 in flight: "<cardID>|<noteTs>" → submitted-at. Cleared by
    // the REAL signal (that fold-note line shows 已拆出 in the card's
    // notes_text) or the 180 s sweep (honest timeout notice — the split
    // demonstrably never landed).
    @Published var pendingSplits: [String: Date] = [:]
    // timed-out placeholder notices (capture = yellow, raise = orange)
    @Published var notices: [LocalNotice] = []
    // §39 回答需输入: answer sent → the needs-input card keeps an orange
    // 「回答发送中…」line until it LEAVES needs_input (actd delivered the
    // answer and the session resumed — or the delivery failed and the card
    // rerouted to running with last_error). 180 s sweep = honest timeout.
    @Published var answerPending: [String: Date] = [:]
    // §37 rename echoes: id → the in-flight display title (row shows it at
    // once); cleared when the backend row carries the new name, 180 s sweep.
    @Published var pendingTitles: [String: PendingTitle] = [:]

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
    // v0.46.x 布局风暴修复：剥掉 generated_at 后的内容指纹（ReloadGate.swift）
    // —— actd 每 ~10s 重写 dashboard.json，心跳字段必变而内容常常不变；指纹
    // 相同的「假更新」跳过 decode + publish（一次 publish = 全板重排）。
    private var lastContentFingerprint: Data?
    /// dashboard.json 最近一次成功读取的 generated_at（心跳假更新也推进）。
    /// 刻意不 @Published：publish 会把整个看板拖去重排，这正是要修的风暴；
    /// 新鲜度标签（FreshnessLabel / popover footer）在 TimelineView 的 15s
    /// tick 里主动来读，健康裁决（computeHealth）同样从这里取真值。
    private(set) var liveGeneratedAt: Date?
    // §44.6 静默并入回执：已经变成 notice 的回执 id（回执在 dashboard 里带
    // TTL 存活 ~10 分钟，每次 reload 都会再出现——只在首次见到时发一行提示）。
    // 首次成功加载先 prime（不发）：app 刚启动时躺在文件里的旧回执不是"刚刚
    // 发生"的事，回放会撒谎（§28 notify_queue 的 app-closed=no-notification
    // 同款语义）。
    private var seenFoldReceipts: Set<String> = []
    private var foldReceiptsPrimed = false

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
                // 指纹/新鲜度一并清：文件重新出现时必须走完整 decode+publish
                //（dashboard 已被置 nil，指纹残留会让相同内容被误判假更新）。
                lastContentFingerprint = nil
                liveGeneratedAt = nil
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
        // v0.46.x 布局风暴修复（放大器 #2）：actd 每 ~10s 重写 dashboard.json，
        // generated_at 心跳必变、卡片内容常常一字不变 —— 纯心跳的「假更新」
        // 不重新 decode、不 withAnimation publish（一次 publish = 全板 ~128 行
        // 重排，2026-07-28 主线程 hang 17 分钟的节拍器）。pendingXXX 的清除
        // 信号全部盯内容变化（plan 指纹 / 卡片离列 / notes 行 / 后端标题），
        // 内容没变即无事可清，跳过安全；hiddenOnce 是唯一盯 generated_at 的，
        // 下面按原语义照常释放。
        let reading = DashboardReloadGate.read(data)
        if dashboard != nil, let fp = reading.fingerprint,
           fp == lastContentFingerprint {
            lastRawData = data
            liveGeneratedAt = FreshnessLabel.parseISO(reading.generatedAt)
            // one-shot hides 语义照旧：generated_at 变了 = 后端已重新生成 →
            // 释放；字段缺失 → legacy 行为（任何 reload 都释放）。空集合不发
            // publish（@Published 的无效突变也会触发 objectWillChange）。
            if let gen = reading.generatedAt, !gen.isEmpty {
                if gen != lastGeneratedAt {
                    lastGeneratedAt = gen
                    releaseOnceHidesIfAny()
                }
            } else {
                releaseOnceHidesIfAny()
            }
            // 同 unchanged-bytes 分支：内容既已解码过且相同，陈旧 decode
            // 错误可以清掉（坏行提示除外）。
            if loadError != nil && (dashboard?.decodeDrops.isEmpty ?? true) { loadError = nil }
            // review P1：内容没变 ≠ 无事可清 —— 清除谓词是 (db × pending) 的
            // 联合函数，闸门跳过期间新建的 pending 可能一出生就满足清除条件
            //（同值改名：set_title 落盘写同值，dashboard 内容永远不再变化，
            // 只在这里清才躲得过 180s 假「改名超时」）。对缓存的上次 decode
            // 结果照跑 sweep；真清掉东西才动画 publish（无变化零 publish，
            // 布局风暴的约束不破）。
            if let cached = dashboard {
                let swept = pendingSweepState.cleared(by: cached)
                if swept.differs(from: pendingSweepState) {
                    withAnimation(.easeOut(duration: 0.2)) {
                        adoptPendingClears(swept)
                        updateBoardMotion()
                    }
                }
            }
            return
        }
        do {
            let db = try JSONDecoder().decode(Dashboard.self, from: data)
            lastRawData = data
            lastContentFingerprint = reading.fingerprint
            liveGeneratedAt = FreshnessLabel.parseISO(db.generated_at)
            // §37 review fix (perf): a new dashboard invalidates the memoized
            // normalized field blobs + per-query hit results.
            invalidateSearchCaches()
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
                // pending 清除 sweep：全部谓词在 PendingSweep.swift（review P1
                // 抽取——决定「什么信号算 REAL signal」的逐段注释随谓词走；
                // 指纹闸门跳过路径共用同一份，判例在 LogicTests）。
                adoptPendingClears(pendingSweepState.cleared(by: db))
                // §44.6 静默并入回执 → 一行可消失的 info 提示（NoticeRow 绿色
                // 机制复用，120 s 自动淡出）。seen-set 保证每条回执只提示一次；
                // 首启 prime：文件里躺着的旧回执不回放。回执不是 pending（无
                // 清除谓词/超时），刻意不进 PendingSweep；指纹闸门跳过路径也
                // 不需要它——回执内容变化必然改变指纹，走全量 decode。
                let receiptIDs = Set(db.fold_receipts.map { $0.id })
                if !foldReceiptsPrimed {
                    foldReceiptsPrimed = true
                    seenFoldReceipts = receiptIDs
                } else {
                    for r in db.fold_receipts where !seenFoldReceipts.contains(r.id) {
                        seenFoldReceipts.insert(r.id)
                        // 隐私红线（§44.6）：回执不携带被并入内容原文——文案
                        // 只由目标卡 id + 显示名拼成；目标卡已消失则只报 R-xxx。
                        let name = r.title.isEmpty
                            ? "" : L("「\(String(r.title.prefix(20)))」",
                                     " \"\(String(r.title.prefix(20)))\"")
                        notices.append(LocalNotice(
                            id: "notice-fold-" + r.id, kind: .info,
                            lane: .approval,
                            text: L("刚才的输入已并入 \(r.req)\(name)（没有建新卡）",
                                    "Your input was merged into \(r.req)\(name) (no new card filed)"),
                            created: Date()))
                    }
                    // 过期出投影的回执从 seen-set 剪掉（同一 id 不会复活，
                    // 集合不随长会话无界增长）。
                    seenFoldReceipts.formIntersection(receiptIDs)
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

    /// 假更新路径的 one-shot hide 释放：非空才 publish（un-hide 改变泳道
    /// 内容，需要动画 + 飞行层事件；空集合连 objectWillChange 都不能发 ——
    /// @Published 的无效突变也会触发全板重排，正是本修复要挡的）。
    private func releaseOnceHidesIfAny() {
        guard !hiddenOnce.isEmpty else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            hiddenOnce.removeAll()
            updateBoardMotion()
        }
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
        // §39: answer echoes give up after 180 s if the card never left
        // needs_input (actd down / inbox file lost) — honest orange notice.
        let expiredAnswers = answerPending.filter { now.timeIntervalSince($0.value) > 180 }
        // §38 拆成新卡: 180 s without the origin line flipping to 已拆出 →
        // the split never landed; button reverts, honest notice.
        let expiredSplits = pendingSplits.filter { now.timeIntervalSince($0.value) > 180 }
        // §37 rename echoes: 180 s without the backend adopting the new name
        let expiredTitles = pendingTitles.filter { now.timeIntervalSince($0.value.created) > 180 }
        // notices themselves fade after 120 s
        let expiredNotices = notices.filter { now.timeIntervalSince($0.created) > 120 }
        guard !expiredCaptures.isEmpty || !expiredRaises.isEmpty || !expiredEchoes.isEmpty
            || !expiredComments.isEmpty || !expiredReturns.isEmpty
            || !expiredMergeBadges.isEmpty || !expiredMergeActions.isEmpty
            || !expiredForceBadges.isEmpty || !expiredAnswers.isEmpty
            || !expiredSplits.isEmpty || !expiredTitles.isEmpty
            || !expiredNotices.isEmpty else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            for c in expiredCaptures {
                capturePending.removeAll { $0.id == c.id }
                // direct-run: after 180 s with no queued row the task really
                // did NOT start — orange, and say so (audit honesty standard);
                // a proposal capture is usually just slow analysis — yellow.
                // §34 修订后 [run] 一律新建卡（不再判重并入），排除了"命中已有
                // 卡所以没开跑"的分支——超时只剩一个诚实解释：后台没在跑。
                notices.append(LocalNotice(
                    id: "notice-" + c.id,
                    kind: c.run ? .raiseTimeout : .captureTimeout,
                    lane: c.run ? .running : .approval,
                    text: c.run
                        ? L("「\(String(c.text.prefix(20)))」任务没有开始——后台可能没在跑（检查 actd）",
                            "\"\(String(c.text.prefix(20)))\" did not start — the backend may not be running (check actd)")
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
            for (key, _) in expiredSplits {
                pendingSplits.removeValue(forKey: key)
                let noticeID = "notice-split-" + key
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: .approval,
                    text: L("「拆成新卡」超时未生效，原备注未变化，请重试（检查 actd 是否在运行）",
                            "Split-into-card timed out — the note is unchanged, try again (check that actd is running)"),
                    created: now))
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
            // §37: a rename that never landed must not vanish silently — the
            // row falls back to its old name, say so (audit honesty standard).
            for (id, _) in expiredTitles {
                pendingTitles.removeValue(forKey: id)
                let noticeID = "notice-title-" + id
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout,
                    lane: currentList(of: id) ?? .approval,
                    text: L("改名超时未确认，卡片名字未变化，请重试（检查 actd 是否在运行）",
                            "Rename timed out — the card name is unchanged, try again (check that actd is running)"),
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
            // §39: the answer echo vanishing must not be silent — the card
            // never left needs_input, so the answer demonstrably never landed.
            for (id, _) in expiredAnswers {
                answerPending.removeValue(forKey: id)
                let noticeID = "notice-answer-" + id
                notices.removeAll { $0.id == noticeID }
                notices.append(LocalNotice(
                    id: noticeID, kind: .raiseTimeout, lane: .running,
                    text: L("回答超时未确认，卡片仍在「需输入」，请重试（检查 actd 是否在运行）",
                            "Answer timed out unconfirmed — the card still needs input, try again (check that actd is running)"),
                    created: now))
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
        guard dashboard != nil else { return missing ? .missing : .ok }
        // legacy dashboards without generated_at: no verdict (footer degrades
        // to the refresh stamp the same way)
        // v0.46.x: 真值取 liveGeneratedAt —— 假更新跳过 publish 后，
        // dashboard.generated_at 会停在上次内容变化，按它裁决会误报 dead。
        guard let gen = liveGeneratedAt else { return .ok }
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
                    fingerprint: dashboard.flatMap { PendingSweep.commentFingerprint(of: id, in: $0) },
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
        dashboard.flatMap { PendingSweep.currentList(of: id, in: $0) }
    }

    private func ids(in kind: ListKind, of db: Dashboard) -> Set<String> {
        PendingSweep.ids(in: kind, of: db)
    }

    // MARK: pending 清除 sweep（谓词在 PendingSweep.swift，两条 reload 路径共用）

    /// 当前全部本地 pending 的快照（喂给 PendingSweepState.cleared(by:)）。
    private var pendingSweepState: PendingSweepState {
        PendingSweepState(
            capturePending: capturePending, hiddenSticky: hiddenSticky,
            raisingLocal: raisingLocal, pendingEchoes: pendingEchoes,
            pinnedLocal: pinnedLocal, pendingComment: pendingComment,
            returningLocal: returningLocal, mergeAnalyzingLocal: mergeAnalyzingLocal,
            mergeForcingLocal: mergeForcingLocal, pendingMergeActions: pendingMergeActions,
            pendingSplits: pendingSplits, answerPending: answerPending,
            pendingTitles: pendingTitles)
    }

    /// sweep 结果写回 @Published —— 只在集合真变小时赋值（sweep 只删不改，
    /// count 比较即等价比较；无效突变也触发 objectWillChange = 全板重排，
    /// 正是布局风暴修复要挡的）。
    private func adoptPendingClears(_ new: PendingSweepState) {
        if new.capturePending.count != capturePending.count { capturePending = new.capturePending }
        if new.hiddenSticky.count != hiddenSticky.count { hiddenSticky = new.hiddenSticky }
        if new.raisingLocal.count != raisingLocal.count { raisingLocal = new.raisingLocal }
        if new.pendingEchoes.count != pendingEchoes.count { pendingEchoes = new.pendingEchoes }
        if new.pinnedLocal.count != pinnedLocal.count { pinnedLocal = new.pinnedLocal }
        if new.pendingComment.count != pendingComment.count { pendingComment = new.pendingComment }
        if new.returningLocal.count != returningLocal.count { returningLocal = new.returningLocal }
        if new.mergeAnalyzingLocal.count != mergeAnalyzingLocal.count {
            mergeAnalyzingLocal = new.mergeAnalyzingLocal
        }
        if new.mergeForcingLocal.count != mergeForcingLocal.count {
            mergeForcingLocal = new.mergeForcingLocal
        }
        if new.pendingMergeActions.count != pendingMergeActions.count {
            pendingMergeActions = new.pendingMergeActions
        }
        if new.pendingSplits.count != pendingSplits.count { pendingSplits = new.pendingSplits }
        if new.answerPending.count != answerPending.count { answerPending = new.answerPending }
        if new.pendingTitles.count != pendingTitles.count { pendingTitles = new.pendingTitles }
    }

    private func title(of id: String) -> String {
        guard let db = dashboard else { return id }
        if let c = db.needs_approval.first(where: { $0.id == id }) { return c.displaySummary }
        if let r = db.review.first(where: { $0.id == id }) { return r.rowTitle }
        if let d = db.debt.first(where: { $0.id == id }) { return d.displaySummary }
        if let t = db.trash.first(where: { $0.id == id }) { return t.displaySummary }
        if let t = (db.running + db.needs_input + db.completed).first(where: { $0.id == id }) {
            return t.rowTitle
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

    /// §39: answer sent — the needs-input card shows 「回答发送中…」in place
    /// (no hide/echo: the card must stay visible while the answer travels).
    func beginAnswer(_ id: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            answerPending[id] = Date()
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

    /// §37: the set_title inbox write succeeded — echo the new display name on
    /// the row immediately. ONLY call site: AppDelegate.submitSetTitle.
    func beginTitleEdit(_ id: String, title: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            pendingTitles[id] = PendingTitle(title: title, created: Date())
        }
    }

    /// The in-flight rename for a row, if any (views overlay it on the title).
    func pendingTitle(_ id: String) -> String? { pendingTitles[id]?.title }

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

    /// §38 拆成新卡: the split_note inbox write succeeded — that fold-note
    /// line shows 拆分中… until the origin line flips to 已拆出 (reload) or
    /// the 180 s sweep gives up honestly. ONLY call site:
    /// AppDelegate.submitSplitNote, after the IO succeeded.
    func beginSplitNote(cardID: String, ts: String) {
        withAnimation(.easeOut(duration: 0.2)) {
            pendingSplits["\(cardID)|\(ts)"] = Date()
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

    // capture ↔ backend matching / commentFingerprint 等清除信号 helper
    // moved to PendingSweep.swift（LogicTests 可测）。

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
    /// below filter every lane; "" (or whitespace) = passthrough. Lives in
    /// the store per the visible* projection pattern; the POPOVER
    /// deliberately keeps reading visible* — search is a board-only
    /// affordance, and KanbanView clears the query onDisappear so a stale
    /// filter can never silently hide cards elsewhere.
    /// Review fix (perf): the TextField binds HERE (instant caret/echo), but
    /// the projections filter on `boardFilterQuery`, which trails by ~200 ms
    /// (debounce) — six lanes re-filtering synchronously on every keystroke
    /// was hundreds of ms on a large indexed board. Clearing is instant.
    @Published var boardQuery: String = "" { didSet { queryEdited() } }

    /// The query the board* projections actually filter on (≤200 ms behind
    /// boardQuery; "" applied immediately so Esc/清空 never lags).
    @Published private(set) var boardFilterQuery: String = ""
    private var searchDebounce: DispatchWorkItem?

    private func queryEdited() {
        searchDebounce?.cancel()
        let q = boardQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty {
            applyFilterQuery("")
            return
        }
        let item = DispatchWorkItem { [weak self] in self?.applyFilterQuery(q) }
        searchDebounce = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2, execute: item)
    }

    private func applyFilterQuery(_ q: String) {
        guard q != boardFilterQuery else { return }
        boardFilterQuery = q
        hitCache.removeAll()   // per-query memo (see hitInfo)
    }

    // §37 词表 (v0.37, per lane where the row carries the field): id + frozen
    // title/name + display_title + former_titles + summary + notes fold +
    // plan/dod + delivered_summary/final_draft + source quotes + agent name.
    // Matching itself is SearchMatch (shared/Sources): separator-free
    // latin/digit runs ("eb1" finds "EB-1A"), CJK substring, AND terms.
    private static func searchFields(of c: ApprovalCard) -> [String] {
        var f = [c.id, c.title, c.summary ?? "", c.display_title ?? "",
                 c.notes_text ?? ""]
        f += c.former_titles ?? []
        f += c.plan
        f += c.dod
        f += c.sources.map { $0.quote }
        return f
    }

    private static func searchFields(of t: RunningTask) -> [String] {
        var f = [t.id, t.name, t.summary ?? "", t.display_title ?? "",
                 t.notes_text ?? "", t.delivered_summary ?? "",
                 t.final_draft ?? "", t.agent_name ?? ""]
        f += t.former_titles ?? []
        f += t.plan ?? []
        f += t.dod ?? []
        return f
    }

    private static func searchFields(of r: ReviewItem) -> [String] {
        var f = [r.id, r.name, r.summary ?? "", r.display_title ?? "",
                 r.notes_text ?? "", r.delivered_summary ?? "",
                 r.final_draft ?? "", r.agent_name ?? ""]
        f += r.former_titles ?? []
        f += r.plan ?? []
        f += r.dod
        f += (r.sources ?? []).map { $0.quote }
        return f
    }

    private static func searchFields(of d: DebtItem) -> [String] {
        var f = [d.id, d.title, d.summary ?? "", d.display_title ?? "",
                 d.notes_text ?? ""]
        f += d.former_titles ?? []
        f += (d.sources ?? []).map { $0.quote }
        return f
    }

    // §37 hit computation, memoized (review fix, perf):
    //  - normFieldsCache: normalized field haystack per card id, built once
    //    per dashboard decode (cleared in reload());
    //  - hitCache: (hit, sessionOnly) per card id for the CURRENT filter
    //    query — the lane filter computes it once and the 命中会话 badge
    //    reuses it instead of re-matching per rendered row. Cleared when the
    //    query, the dashboard, or the session index changes.
    private var normFieldsCache: [String: [String]] = [:]
    private var hitCache: [String: (hit: Bool, sessionOnly: Bool)] = [:]

    /// Drop the per-dashboard caches (called on every successful decode).
    fileprivate func invalidateSearchCaches() {
        normFieldsCache.removeAll()
        hitCache.removeAll()
    }

    /// §37 two-layer hit for one card under the current filter query.
    /// Cross-layer AND (review fix): a term may be satisfied by a projected
    /// field OR the session transcript — "推荐信 chen" matches a card whose
    /// display title carries 推荐信 while only the transcript mentions chen.
    /// `sessionOnly` (the 命中会话 badge) stays truthful: the card matched,
    /// but NOT on its visible fields alone.
    private func hitInfo(id: String, fields: () -> [String]) -> (hit: Bool, sessionOnly: Bool) {
        let q = boardFilterQuery
        guard !q.isEmpty else { return (true, false) }
        if let cached = hitCache[id] { return cached }
        let norm: [String]
        if let cached = normFieldsCache[id] {
            norm = cached
        } else {
            norm = SearchMatch.normalizedHaystack(fields())
            normFieldsCache[id] = norm
        }
        let fieldHit = SearchMatch.matchesNormalized(q, in: norm)
        var result = (hit: fieldHit, sessionOnly: false)
        if !fieldHit, let session = sessionNormText(id),
           SearchMatch.matchesNormalized(q, in: norm + [session]) {
            result = (hit: true, sessionOnly: true)
        }
        hitCache[id] = result
        return result
    }

    func sessionOnlyHit(_ c: ApprovalCard) -> Bool {
        hitInfo(id: c.id, fields: { Self.searchFields(of: c) }).sessionOnly
    }
    func sessionOnlyHit(_ t: RunningTask) -> Bool {
        hitInfo(id: t.id, fields: { Self.searchFields(of: t) }).sessionOnly
    }
    func sessionOnlyHit(_ r: ReviewItem) -> Bool {
        hitInfo(id: r.id, fields: { Self.searchFields(of: r) }).sessionOnly
    }
    func sessionOnlyHit(_ d: DebtItem) -> Bool {
        hitInfo(id: d.id, fields: { Self.searchFields(of: d) }).sessionOnly
    }

    // §37 session-content layer — state/search_index.json (actd-maintained,
    // Mac-local, NEVER part of dashboard.json). Lazy-loaded and revalidated by
    // (mtime, size) so the ~10s tick never re-parses an unchanged file;
    // missing/corrupt = the layer is silently absent (field search still
    // works). Texts are stored NORMALIZED once per (re)load — never
    // re-normalized per keystroke (review fix, perf).
    private var searchIndexNorm: [String: String] = [:]
    private var searchIndexStamp: (mtime: Date, size: Int)?

    private func sessionNormText(_ id: String) -> String? {
        reloadSearchIndexIfNeeded()
        return searchIndexNorm[id]
    }

    private func reloadSearchIndexIfNeeded() {
        let path = AppPaths.stateRoot + "/state/search_index.json"
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let mtime = attrs[.modificationDate] as? Date else {
            if !searchIndexNorm.isEmpty { hitCache.removeAll() }
            searchIndexNorm = [:]
            searchIndexStamp = nil
            return
        }
        let size = (attrs[.size] as? Int) ?? 0
        if let stamp = searchIndexStamp, stamp.mtime == mtime, stamp.size == size {
            return
        }
        searchIndexStamp = (mtime, size)
        hitCache.removeAll()   // the session layer changed → hits may change
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else {
            searchIndexNorm = [:]   // corrupt → layer absent, never a crash
            return
        }
        var out: [String: String] = [:]
        for (k, v) in obj {
            if let entry = v as? [String: Any], let text = entry["text"] as? String {
                out[k] = SearchMatch.normalize(text)
            }
        }
        searchIndexNorm = out
    }

    /// visibleApprovals + search. 占位卡不参与过滤隐藏: the grey processing
    /// prefix (captures + raise placeholders, `processing == true`) always
    /// rides through — hiding an in-flight submit behind a filter would read
    /// as a lost capture. (建议卡 likewise stay unfiltered — they never pass
    /// through this projection at all; KanbanView keeps visibleMergeSuggestions.)
    var boardApprovals: [ApprovalCard] {
        guard !boardFilterQuery.isEmpty else { return visibleApprovals }
        return visibleApprovals.filter {
            card in card.processing
                || hitInfo(id: card.id, fields: { Self.searchFields(of: card) }).hit
        }
    }

    var boardRunning: [RunningTask] { searchTasks(visibleRunning) }
    var boardNeedsInput: [RunningTask] { searchTasks(visibleNeedsInput) }
    var boardCompleted: [RunningTask] { searchTasks(visibleCompleted) }

    /// Shared RunningTask filter (running / needs_input / completed reuse the
    /// struct).
    private func searchTasks(_ tasks: [RunningTask]) -> [RunningTask] {
        guard !boardFilterQuery.isEmpty else { return tasks }
        return tasks.filter { t in
            hitInfo(id: t.id, fields: { Self.searchFields(of: t) }).hit
        }
    }

    var boardReview: [ReviewItem] {
        guard !boardFilterQuery.isEmpty else { return visibleReview }
        return visibleReview.filter { r in
            hitInfo(id: r.id, fields: { Self.searchFields(of: r) }).hit
        }
    }

    /// 潜在任务 (backlog, dashboard key `debt`) — DebtItem has no dod/plan fields.
    var boardDebt: [DebtItem] {
        guard !boardFilterQuery.isEmpty else { return visibleDebt }
        return visibleDebt.filter { d in
            hitInfo(id: d.id, fields: { Self.searchFields(of: d) }).hit
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
