// Kanban.swift — KanbanView（主窗口任务台看板，400pt 固定列宽）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// MARK: - Kanban board (main-window 任务台) — Jira-style lanes
//
// Main window only; the popover keeps the vertical DashboardView untouched.
// Cards/rows are the popover components reused verbatim at their popover
// width (fixed 400pt lanes); each lane scrolls vertically on its own.
// Columns: 潜在任务 | 提案 | 运行中(+需输入) | 待验收 | 阶段性完成 | 永久性完成
// — trash stays out. (v0.18: backlog moved leftmost so the board reads as a
// spatial flow — detected sits upstream of card_sent, and every action moves
// a card exactly one column to the right. Display order ONLY; the menu-bar
// popover keeps its own attention-ordered list. 潜在任务/Backlog is the
// DISPLAY name of the former 欠账/debt lane and 阶段性完成/Done for now of the
// completed lane — registry status names and the dashboard.json keys are
// unchanged, 纯展示层. v0.33: the backlog lane and the off-board archive
// [永久性完成/Done for good] render as default-collapsed bookend strips —
// see collapsibleColumn below.)

/// 契约 §21bis: Identifiable payload carrying the multi-selected ids into the
/// force-merge confirmation sheet (`.sheet(item:)` requires Identifiable).
private struct ForceMergePayload: Identifiable {
    let id = UUID()
    let ids: [String]
}

struct KanbanView: View {
    @ObservedObject var store: DashboardStore
    // observe the UI language so the whole board re-renders on switch
    @ObservedObject private var i18n = LanguageStore.shared
    unowned let app: AppDelegate
    // merge-review 契约七: multi-select state — header 「选择」button toggles,
    // Esc exits (hidden cancel-action button below). @State is discarded when
    // the page switches away, so select mode never leaks across pages.
    @State private var selectMode = false
    @State private var selectedIDs: Set<String> = []
    // 契约 §21bis 强制合并: 操作条「强制合并」点开确认弹窗（选主卡）。非 nil =
    // 弹窗展示中；Identifiable 载荷带住此刻的选中集，弹窗内选主卡后 submit。
    @State private var forceMergePayload: ForceMergePayload?
    // 搜索过滤: focus for the header search box (⌘F focuses, Esc clears).
    // The query itself lives in the STORE (boardQuery) so the board*
    // projections can filter — visible* 现有模式.
    @FocusState private var searchFocused: Bool
    // 搜索埋点: last non-empty query of the current search session — flushed
    // as ONE board_search event when the caret leaves the box / page switches
    // (never per keystroke). Query text itself is capture_input-gated.
    @State private var searchSessionQuery = ""
    // v0.43 手感: consumes the store's BoardMotionEvents — owns the flight
    // proxies, row frames, landing gates, strip pulses (BoardMotion.swift).
    @StateObject private var flights = BoardFlightController()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            if let err = store.loadError {
                Text(err)
                    .font(.system(size: 11))
                    .foregroundColor(.orange)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 6)
            }
            // P1-4: slow-vs-broken pipeline banner (shared with the popover)
            PipelineHealthBanner(store: store, app: app,
                                 horizontalPadding: 16, bottomPadding: 8)
            // v0.19.0: board-level ingest diagnostic cards — silent ingest
            // failures become visible, actionable cards. Renders nothing for a
            // healthy / fresh (recording off + no creds) setup.
            DiagnosticsStrip(app: app, horizontalPadding: 16, bottomPadding: 8)
            Divider()
            if store.dashboard == nil {
                emptyState
            } else {
                board
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        // 多选态 → 底部浮出操作条（请求合并建议 ≥2 / 强制合并 ≥2 / 提建议 ≥0 / 取消）
        .overlay(alignment: .bottom) { selectionBar }
        // 契约 §21bis: 强制合并确认弹窗（选主卡）。提交成功后退出多选。
        .sheet(item: $forceMergePayload) { payload in
            ForceMergeSheet(ids: payload.ids, app: app) { primary in
                if app.submitMergeForce(ids: payload.ids, primary: primary) {
                    setSelectMode(false)
                }
            }
        }
        .background {
            // 契约七: Esc 退出多选 — window-scoped hidden cancel action (no
            // event monitor; keyboard shortcuts only fire while THIS window is
            // key, so the popover's own Esc logic is untouched).
            if selectMode {
                Button("") { setSelectMode(false) }
                    .keyboardShortcut(.cancelAction)
                    .opacity(0)
                    .frame(width: 0, height: 0)
                    .accessibilityHidden(true)
            }
            // 搜索过滤: ⌘F puts the caret in the board search box — same
            // hidden-button pattern; window-scoped, so the popover (no board,
            // no box) and the 设置 page's local shortcuts are untouched.
            Button("") { searchFocused = true }
                .keyboardShortcut("f", modifiers: .command)
                .opacity(0)
                .frame(width: 0, height: 0)
                .accessibilityHidden(true)
        }
        .onChange(of: store.boardQuery) { _, v in
            if !v.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                searchSessionQuery = v
            }
        }
        .onChange(of: searchFocused) { _, focused in
            if !focused { flushSearchEvent() }
        }
        // page switched away / window closed → drop the filter, so cards can
        // never come back silently hidden (same policy as the multi-select
        // @State, which SwiftUI discards for us).
        .onDisappear {
            flushSearchEvent()
            store.boardQuery = ""
        }
        // 快速捕获输入框已从这里的工具栏移入待审批列顶（KanbanComposer，
        // Composer.swift）；.focusCaptureField 通知改由 composer 自己接收。
    }

    // full-width header: freshness left, recording control right-aligned
    // (the app-name title lives in the window title bar — no duplicate here)
    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            // dashboard.json freshness — same semantics as the popover footer
            FreshnessLabel(generatedAt: FreshnessLabel.parseISO(store.dashboard?.generated_at))
            Spacer()
            if store.dashboard != nil {
                // 搜索过滤: non-empty → every lane filters in real time
                // (board* store projections); ⌘F focuses, Esc clears.
                searchField
                // 建议上报: header 直点 = 对整体提建议（ids 空）；多选后
                // 操作条上的同名按钮才针对所选卡。
                Button(L("提建议", "Send feedback")) {
                    _ = app.promptFeedback(ids: [])
                }
                .font(.system(size: 12))
                .help(L("对整体提建议；先「选择」卡片可针对所选卡",
                        "Overall feedback; use Select first to target cards"))
                // 契约七: 「选择」enters multi-select; the same button (or Esc
                // / the bar's 取消) exits. Board-only — no dashboard, no button.
                Button(selectMode ? L("退出选择", "Done") : L("选择", "Select")) {
                    setSelectMode(!selectMode)
                }
                .font(.system(size: 12))
            }
            RecordingMenuButton()
        }
    }

    // MARK: - board search (搜索过滤)

    /// Header search box. Matching is case-insensitive over
    /// title/summary/dod/plan/id (DashboardStore.board* projections);
    /// 占位卡/建议卡 never hide. Esc is staged (IME-safe): non-empty clears
    /// the query (native search-field behavior — a filter, not a draft);
    /// already empty defocuses, and a further Esc (field no longer focused,
    /// onKeyPress can't fire) reaches select-mode's cancel action as before.
    /// Clicking outside the box defocuses too (AppDelegate's app-wide
    /// clickDefocusMonitor) — the query stays, visible in the box.
    private var searchField: some View {
        HStack(spacing: 4) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            TextField(L("搜索卡片（⌘F）", "Search cards (⌘F)"),
                      text: $store.boardQuery)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .frame(width: 170)
                .focused($searchFocused)
                .onKeyPress(.escape) { escClearSearch() }
            if !store.boardQuery.isEmpty {
                Button {
                    store.boardQuery = ""
                    // the clear button sits INSIDE the visual search box, but
                    // the defocus monitor can't tell (SwiftUI buttons have no
                    // NSView) — refocus so clear-and-retype keeps the caret,
                    // matching native NSSearchField.
                    searchFocused = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("清空搜索", "Clear search"))
            }
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 8)
        .background(Color.primary.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    /// One board_search event per search session (docs/TELEMETRY.md): chars
    /// is metadata; the query TEXT rides along only behind the capture_input
    /// gate. No-op when nothing was typed since the last flush.
    private func flushSearchEvent() {
        let q = searchSessionQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        searchSessionQuery = ""
        Analytics.firstReach("board_search")
        var fields: [String: Any] = ["chars": q.count]
        if Telemetry.contentCaptureActive() {
            fields["query"] = Analytics.clip(q)
        }
        Analytics.log("board_search", fields: fields)
    }

    private func escClearSearch() -> KeyPress.Result {
        // IME red line: Esc cancels a live pinyin composition — the input
        // method owns it, pass through untouched (Composer.escKey 先例).
        if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
           tv.hasMarkedText() { return .ignored }
        if !store.boardQuery.isEmpty {
            store.boardQuery = ""    // 1st Esc: clear the filter
        } else {
            searchFocused = false    // 2nd Esc: release the caret
        }
        return .handled
    }

    /// True while a search filter is active (mirrors the store's normalization).
    private var searching: Bool {
        !store.boardQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Lane empty copy: while filtering, an empty lane means "no matches
    /// here", not "nothing exists" — say so instead of the normal empty text.
    private func laneEmptyText(_ normal: String) -> String {
        searching ? L("无匹配卡片", "No matching cards") : normal
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 14) {
            // capture keeps working before the first dashboard.json exists —
            // the inbox write path doesn't depend on the pipeline having run.
            KanbanComposer(app: app)
                .frame(width: 400)
            // P1-5: shared first-launch empty state (Freshness.swift) — same
            // copy as the popover, start command + dependency-check button.
            PipelineEmptyStateView(app: app)
                .frame(maxWidth: 400, alignment: .leading)
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    // Column counts follow the RENDERED arrays (visible* + echoes) — same
    // policy as the popover, so a badge can never disagree with its lane.
    @ViewBuilder private var board: some View {
        if store.dashboard != nil {
            // 搜索过滤: board* = visible* + the search filter (empty query →
            // passthrough), so lanes and their counts follow the filter for
            // free. visibleApprovals' quick-capture / raisingLocal grey
            // processing placeholders ride through unfiltered (占位卡不参与
            // 过滤隐藏) — identical behavior to the popover otherwise.
            let approvals = store.boardApprovals
            let reviews = store.boardReview
            let debt = store.boardDebt
            // v0.10.3 契约一: sorted+hidden-filtered projections, shared with
            // the popover so both surfaces always agree.
            let running = store.boardRunning
            let needsInput = store.boardNeedsInput
            let completed = store.boardCompleted
            // v0.34 direct-run placeholders (占位卡不参与过滤隐藏, same policy
            // as the proposal lane's processing prefix).
            let runCaptures = store.visibleRunCaptures
            // merge-review 契约七: suggestion cards (dismiss-echo filtered);
            // 建议卡不参与过滤隐藏 — deliberately NOT search-filtered.
            let suggestions = store.visibleMergeSuggestions
            let runningEchoes = store.echoes(for: .running)
            let completedEchoes = store.echoes(for: .completed)
            let debtEchoes = store.echoes(for: .debt)
            // P2-4: notices render in the lane where the action happened —
            // an abort timeout belongs next to the running column, not two
            // columns away. Trash isn't a board column → its notices (restore
            // timeouts) surface in the approval lane. Popover keeps one list.
            let approvalNotices = laneNotices(.approval, .trash)
            let runningNotices = laneNotices(.running)
            let reviewNotices = laneNotices(.review)
            let debtNotices = laneNotices(.debt)
            let completedNotices = laneNotices(.completed)
            let archivedNotices = laneNotices(.archived)
            // v0.43 手感: the flight layer sits ABOVE the board scroll view in
            // a shared named coordinate space; rows/lanes report their frames
            // into it, proxies fly across it, and it never intercepts clicks.
            ZStack(alignment: .topLeading) {
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 12) {
                    // 潜在任务/Backlog leftmost (v0.18 flow order): display
                    // rename of the debt lane — the store projection
                    // (visibleDebt) and dashboard key stay. quiet: a
                    // pre-execution parking lot must not compete with
                    // proposals for attention. v0.33: default-collapsed strip;
                    // the store force-opens it when a debt echo/notice lands.
                    // While a search has debt matches the strip renders
                    // expanded regardless (a stale filter / collapsed strip
                    // can never silently hide cards — Store.boardQuery
                    // invariant); clearing the query restores the strip state.
                    collapsibleColumn(
                           title: L("潜在任务 · backlog", "Backlog"),
                           count: debt.count + debtEchoes.count,
                           help: LaneHelp.backlog,
                           emptyText: laneEmptyText(
                               L("不着急的事会先停在这里——不会自动执行，也永不过期",
                                 "Not-urgent items park here — nothing runs on its own, nothing expires")),
                           isEmpty: debt.isEmpty && debtEchoes.isEmpty
                               && debtNotices.isEmpty,
                           quiet: true,
                           expanded: searching && !debt.isEmpty
                               ? .constant(true) : $store.backlogStripExpanded,
                           motionKey: "debt") {
                        ForEach(debtNotices) { NoticeRow(notice: $0) }
                        // v0.43: echo rows report under their sourceID — the
                        // differ tracks the CARD id, so a 暂缓 flight can land
                        // on the echo that stands in for it.
                        ForEach(debtEchoes) {
                            PendingEchoRow(echo: $0)
                                .boardCardMotion($0.sourceID, lane: "debt", store: store, flights: flights)
                        }
                        // v0.21 契约七: 潜在任务卡也可多选参与合并（selectableIDs 已含 debt）。
                        ForEach(debt, id: \.id) { d in
                            selectableCard(d.id) {
                                DebtRow(item: d, app: app)
                            }
                            .boardCardMotion(d.id, lane: "debt", store: store, flights: flights)
                        }
                    }
                    // isEmpty: false — the resident composer means this lane
                    // always has content; the ghost placeholder renders below
                    // it manually so the empty look stays the same.
                    // W8: lane display name 提案/Proposals — internal keys
                    // (needs_approval, card_sent, …) unchanged.
                    column(title: L("提案 · proposals", "Proposals"),
                           count: approvals.count + suggestions.count,
                           help: LaneHelp.proposals,
                           emptyText: laneEmptyText(
                               L("没有等你拍板的事。想到什么，直接在上面输入框里说一句",
                                 "Nothing needs your decision. Capture a thought in the box above")),
                           isEmpty: false, motionKey: "approval") {
                        // resident quick-capture composer (Composer.swift)
                        KanbanComposer(app: app)
                        if approvals.isEmpty && approvalNotices.isEmpty
                            && suggestions.isEmpty {
                            lanePlaceholder(laneEmptyText(
                                L("没有等你拍板的事。想到什么，直接在上面输入框里说一句",
                                  "Nothing needs your decision. Capture a thought in the box above")))
                        }
                        ForEach(approvalNotices) { NoticeRow(notice: $0) }
                        // 契约七: 建议卡插在 composer 与占位卡之后、真实卡之前。
                        // 占位卡 = visibleApprovals 的灰色 processing 前缀
                        // (captures + raise placeholders 恒在数组头部)；
                        // prefix(while:) 不动其余排序。
                        let placeholderPrefix = approvals.prefix(while: { $0.processing })
                        ForEach(Array(placeholderPrefix), id: \.id) { card in
                            ApprovalCardView(card: card, app: app,
                                             commentPending: store.pendingComment[card.id] != nil)
                                .boardCardMotion(card.id, lane: "approval", store: store, flights: flights)
                        }
                        ForEach(suggestions, id: \.id) { s in
                            // dismiss-pending 的建议卡已被投影过滤（即时消失），
                            // 这里只剩 apply-pending 需要灰显（契约七）。
                            MergeSuggestionCard(suggestion: s, app: app,
                                                actionPending: store.mergeApplyPending(s.id))
                        }
                        ForEach(Array(approvals.dropFirst(placeholderPrefix.count)),
                                id: \.id) { card in
                            // checkbox 只上真实卡：后端 raising 卡（processing）
                            // 不参与多选（契约七: 不含占位/建议卡）
                            selectableCard(card.id, selectable: !card.processing) {
                                ApprovalCardView(card: card, app: app,
                                                 commentPending: store.pendingComment[card.id] != nil)
                            }
                            .boardCardMotion(card.id, lane: "approval", store: store, flights: flights)
                        }
                    }
                    // needs_input merges into 运行中 — listed first with a
                    // permanent orange 需输入 badge, then a thin divider.
                    // isEmpty: false — the resident run composer (v0.34) means
                    // this lane always has content; the ghost placeholder
                    // renders below it manually (proposals-column pattern).
                    column(title: L("运行中 · running", "Running"),
                           count: running.count + needsInput.count
                               + runningEchoes.count + runCaptures.count,
                           help: LaneHelp.running,
                           emptyText: laneEmptyText(
                               L("没有正在执行的任务。批准一个提案，AI 就开始干活",
                                 "Nothing running — approve a proposal to start")),
                           isEmpty: false, motionKey: "running") {
                        // resident direct-run composer (Composer.swift, v0.34)
                        KanbanComposer(app: app, mode: .run)
                        if running.isEmpty && needsInput.isEmpty
                            && runningEchoes.isEmpty && runningNotices.isEmpty
                            && runCaptures.isEmpty {
                            lanePlaceholder(laneEmptyText(
                                L("没有正在执行的任务。批准一个提案，或在上面输入框里直接开跑",
                                  "Nothing running — approve a proposal, or type above to run one now")))
                        }
                        ForEach(runningNotices) { NoticeRow(notice: $0) }
                        ForEach(runCaptures, id: \.id) { c in
                            RunCapturePendingRow(pending: c, app: app)
                                .boardCardMotion(c.id, lane: "running", store: store, flights: flights)
                        }
                        ForEach(runningEchoes) {
                            PendingEchoRow(echo: $0)
                                .boardCardMotion($0.sourceID, lane: "running", store: store, flights: flights)
                        }
                        ForEach(needsInput, id: \.id) { t in
                            selectableCard(t.id) {
                                TaskRow(task: t, app: app, lane: .needsInput)
                            }
                            .boardCardMotion(t.id, lane: "running", store: store, flights: flights)
                        }
                        if !needsInput.isEmpty && !running.isEmpty {
                            Divider().opacity(0.5)
                        }
                        ForEach(running, id: \.id) { t in
                            selectableCard(t.id) {
                                TaskRow(task: t, app: app, lane: .running)
                            }
                            .boardCardMotion(t.id, lane: "running", store: store, flights: flights)
                        }
                    }
                    column(title: L("待验收 · review", "Review"),
                           count: reviews.count,
                           help: LaneHelp.review,
                           emptyText: laneEmptyText(
                               L("没有等你验收的交付", "No drafts waiting for your review")),
                           isEmpty: reviews.isEmpty && reviewNotices.isEmpty,
                           motionKey: "review") {
                        ForEach(reviewNotices) { NoticeRow(notice: $0) }
                        ForEach(reviews, id: \.id) { r in
                            selectableCard(r.id) {
                                ReviewRow(item: r, app: app)
                            }
                            .boardCardMotion(r.id, lane: "review", store: store, flights: flights)
                        }
                    }
                    // 阶段性完成/Done for now (display-only): delivery happens
                    // at the review stage; this lane means "you accepted this
                    // round" — it may still be waiting on the other side, and
                    // 永久完成 (archive) is one lane further right. Registry
                    // status `delivered` frozen.
                    column(title: L("阶段性完成 · done for now", "Done for now"),
                           count: completed.count + completedEchoes.count,
                           help: LaneHelp.done,
                           emptyText: laneEmptyText(
                               L("还没有验收过的交付", "Nothing accepted yet")),
                           isEmpty: completed.isEmpty && completedEchoes.isEmpty
                               && completedNotices.isEmpty,
                           motionKey: "completed") {
                        ForEach(completedNotices) { NoticeRow(notice: $0) }
                        ForEach(completedEchoes) {
                            PendingEchoRow(echo: $0)
                                .boardCardMotion($0.sourceID, lane: "completed", store: store, flights: flights)
                        }
                        // v0.21 契约七: 阶段性完成卡也可多选参与合并（selectableIDs 已含 completed）。
                        ForEach(completed, id: \.id) { t in
                            selectableCard(t.id) {
                                TaskRow(task: t, app: app, lane: .completed)
                            }
                            .boardCardMotion(t.id, lane: "completed", store: store, flights: flights)
                        }
                    }
                    // v0.33 far-right bookend: 永久性完成/Done for good — the
                    // off-board archive surfaced as a second default-collapsed
                    // strip, symmetric with the backlog strip. STILL NOT a
                    // board lane: it joins no selectableIDs/multi-select.
                    // Unarchive feedback (info strip / timeout notice, lane
                    // .archived) renders at the top of the expanded content —
                    // the store force-opens the strip when one lands, so 放回
                    // 看板 can never fail invisibly. Expanded content = the
                    // popover archive section's search + rows.
                    collapsibleColumn(
                           title: L("🗄 永久性完成 · done for good", "🗄 Done for good"),
                           count: store.visibleArchivedCount,
                           help: ArchiveSectionView.helpCopy,
                           emptyText: L("还没有永久完成的卡", "Nothing here yet"),
                           isEmpty: false,
                           quiet: true,
                           expanded: $store.archiveStripExpanded,
                           motionKey: "archived") {
                        ForEach(archivedNotices) { NoticeRow(notice: $0) }
                        ArchiveLaneContent(store: store, app: app)
                    }
                }
                .padding(16)
            }
            BoardFlightOverlay(controller: flights)
            }
            .coordinateSpace(name: BoardMotionPolicy.space)
            .onPreferenceChange(BoardFramesKey.self) { flights.frames = $0 }
            .onChange(of: store.boardMotion) { _, event in
                if let event { flights.handle(event, store: store) }
            }
            // first render after the window (re)opens: mark the current event
            // seen — no animation on the first snapshot a fresh board shows.
            .onAppear { flights.baseline(store.boardMotion?.seq) }
        }
    }

    /// Notices whose action happened in one of these lanes (P2-4 routing).
    private func laneNotices(_ lanes: ListKind...) -> [LocalNotice] {
        store.notices.filter { lanes.contains($0.lane) }
    }

    // MARK: - multi-select (merge-review 契约七)

    private func setSelectMode(_ on: Bool) {
        guard on != selectMode else { return }
        withAnimation(.easeOut(duration: 0.15)) {
            selectMode = on
            if !on { selectedIDs.removeAll() }
        }
    }

    private func toggleSelected(_ id: String) {
        withAnimation(.easeOut(duration: 0.15)) {
            if selectedIDs.contains(id) {
                selectedIDs.remove(id)
            } else {
                selectedIDs.insert(id)
            }
        }
    }

    /// Ids that may join a merge review right now: real cards of EVERY board
    /// lane — 潜在任务/待审批/运行中(含需输入)/待验收/阶段性完成 — minus
    /// placeholders (processing), echoes, and suggestion cards. v0.21: 全 lane
    /// 可选（含 debt + completed）；跨状态合并的合法性交由后端 merge_review
    /// 判定 —— Swift 侧只保持选择 UI 宽松，不预先拦截。永久性完成（归档区）不是
    /// 看板列——它的 v0.33 书立条不在此多选面里。Selection is re-validated
    /// against this at submit time (a card may have moved lanes since it was
    /// ticked).
    private var selectableIDs: Set<String> {
        var s = Set(store.visibleApprovals.filter { !$0.processing }.map { $0.id })
        s.formUnion(store.visibleRunning.map { $0.id })
        s.formUnion(store.visibleNeedsInput.map { $0.id })
        s.formUnion(store.visibleReview.map { $0.id })
        s.formUnion(store.visibleDebt.map { $0.id })
        s.formUnion(store.visibleCompleted.map { $0.id })
        return s
    }

    private func submitSelection() {
        // sorted for a deterministic inbox payload; stale ids (card left its
        // lane since ticking) are dropped rather than sent for actd to reject.
        let ids = selectedIDs.intersection(selectableIDs).sorted()
        guard ids.count >= 2 else { return }   // 契约一: ≥2
        if app.submitMergeReview(ids: ids) {
            setSelectMode(false)   // 契约七: 提交后退出多选（角标由 store 盖）
        }
    }

    /// 建议上报: 操作条「提建议」— stale ids dropped like submitSelection;
    /// 零选中 = ids 空（对整体提建议）。成功提交后退出多选。
    private func submitFeedbackSelection() {
        let ids = selectedIDs.intersection(selectableIDs).sorted()
        if app.promptFeedback(ids: ids) {
            setSelectMode(false)
        }
    }

    /// Multi-select bottom floating action bar — shows for the whole select
    /// session (提建议 works at ≥0 selected; 契约一 keeps 请求合并建议 at ≥2).
    @ViewBuilder private var selectionBar: some View {
        if selectMode {
            // count + visibility follow what submit will ACTUALLY send (stale
            // ids — cards that left the board since ticking — are pruned at
            // submit time): a label promising (2) over a silent no-op guard
            // would be a dead button.
            let activeCount = selectedIDs.intersection(selectableIDs).count
            HStack(spacing: 10) {
                if activeCount >= 2 {
                    Button {
                        submitSelection()
                    } label: {
                        Text(L("请求合并建议 (\(activeCount))",
                               "Suggest merge (\(activeCount))"))
                            .font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.purple)   // 建议卡同款紫色 accent
                    // 契约 §21bis: 用户直断——跳过 AI、钦定主卡直接合并（走确认弹窗）
                    Button {
                        let ids = selectedIDs.intersection(selectableIDs).sorted()
                        guard ids.count >= 2 else { return }
                        forceMergePayload = ForceMergePayload(ids: ids)
                    } label: {
                        Text(L("强制合并 (\(activeCount))",
                               "Force-merge (\(activeCount))"))
                            .font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.bordered)
                    .help(L("跳过 AI 分析，钦定主卡直接合并（不可撤销）",
                            "Skip AI analysis — pick a primary and merge now (not reversible)"))
                }
                // 建议上报: ≥0 张 — 零选中即对整体提建议
                Button {
                    submitFeedbackSelection()
                } label: {
                    Text(activeCount == 0
                         ? L("提建议", "Send feedback")
                         : L("提建议 (\(activeCount))",
                             "Send feedback (\(activeCount))"))
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.bordered)
                Button(L("取消", "Cancel")) { setSelectMode(false) }
                    .font(.system(size: 12))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .stroke(Color.primary.opacity(0.12)))
            .shadow(color: .black.opacity(0.15), radius: 8, y: 2)
            .padding(.bottom, 18)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    /// Wraps a REAL card (待审批/运行中/待验收) with the 契约七 chrome:
    ///  - 多选态: top-left checkbox + a full-card click-catcher (点卡=切换选中;
    ///    the catcher deliberately blocks the card's own buttons while
    ///    selecting — a mis-click must not approve/trash anything)
    ///  - 合并分析中… corner badge while a requested analysis covers the id
    ///    (local optimistic entry or a live backend analyzing suggestion)
    private func selectableCard<V: View>(
        _ id: String, selectable: Bool = true, @ViewBuilder content: () -> V
    ) -> some View {
        content()
            .overlay {
                if selectMode && selectable {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.accentColor.opacity(
                            selectedIDs.contains(id) ? 0.10 : 0.001))
                        .overlay(alignment: .topLeading) {
                            Image(systemName: selectedIDs.contains(id)
                                  ? "checkmark.circle.fill" : "circle")
                                .font(.system(size: 15))
                                .foregroundColor(selectedIDs.contains(id)
                                                 ? .accentColor : .secondary)
                                .padding(6)
                        }
                        .contentShape(Rectangle())
                        .onTapGesture { toggleSelected(id) }
                }
            }
            .overlay(alignment: .topTrailing) {
                if store.isMergeForcing(id) {
                    mergeForcingBadge
                } else if store.isMergeAnalyzing(id) {
                    mergeAnalyzingBadge
                }
            }
    }

    /// 契约七: 合并分析中… 角标 (local optimistic → backend analyzing handoff).
    private var mergeAnalyzingBadge: some View {
        Text(L("合并分析中…", "Analyzing…"))
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(.purple)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.purple.opacity(0.12))
            .clipShape(Capsule())
            .padding(6)
            .allowsHitTesting(false)
    }

    /// 契约 §21bis: 合并中… 角标 (强制合并已提交，等下一版 dashboard 落地).
    private var mergeForcingBadge: some View {
        Text(L("合并中…", "Merging…"))
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(.purple)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.purple.opacity(0.12))
            .clipShape(Capsule())
            .padding(6)
            .allowsHitTesting(false)
    }

    // one lane: fixed 400pt so cards keep their popover size; header on top,
    // then an independent vertical scroll for the lane's cards.
    // help → SectionHeader's ? popover/tooltip; quiet → one notch of visual
    // quieting on the header (backlog + archive, so proposals keep the eye).
    // collapse ≠ nil → this is an expanded strip: clicking the header (or its
    // ⟨⟨ hint) collapses it back to collapsedStrip (v0.33).
    // motionKey (v0.43): lane key under which the column reports its frame —
    // the flight layer's fallback landing zone when the target row isn't
    // laid out (scrolled away / archive content).
    private func column<Content: View>(
        title: String, count: Int, help: String? = nil,
        emptyText: String, isEmpty: Bool, quiet: Bool = false,
        collapse: (() -> Void)? = nil, motionKey: String? = nil,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                SectionHeader(title: title, count: count, help: help)
                if collapse != nil {
                    Image(systemName: "chevron.left.2")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.secondary.opacity(0.7))
                        .padding(.top, 4)
                        .help(L("点列头收起", "Click the header to collapse"))
                }
            }
                .opacity(quiet ? 0.65 : 1)
                .padding(.horizontal, 10)
                .padding(.top, 6)
                .contentShape(Rectangle())
                .onTapGesture { collapse?() }
            ScrollView(.vertical) {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if isEmpty {
                        lanePlaceholder(emptyText)
                    } else {
                        content()
                    }
                }
                .padding(.horizontal, 10)
                // top inset so the first item's focus ring (e.g. the
                // composer's .roundedBorder blue ring) clears the lane's
                // rounded-rect clipShape instead of being cut off.
                .padding(.top, 6)
                .padding(.bottom, 10)
            }
        }
        .frame(width: 400)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color.primary.opacity(0.018))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .boardMotionFrame("lane:\(motionKey ?? title)")
    }

    // v0.33 bookend strips: a lane that renders as a narrow 44pt strip until
    // expanded. Expansion is session-sticky STORE state (survives page
    // switches, never persisted — every launch starts collapsed); the debt
    // strip is additionally force-opened by the store when an echo/notice
    // lands in it.
    private func collapsibleColumn<Content: View>(
        title: String, count: Int, help: String? = nil,
        emptyText: String, isEmpty: Bool, quiet: Bool = false,
        expanded: Binding<Bool>, motionKey: String? = nil,
        @ViewBuilder content: () -> Content
    ) -> some View {
        Group {
            if expanded.wrappedValue {
                column(title: title, count: count, help: help,
                       emptyText: emptyText, isEmpty: isEmpty, quiet: quiet,
                       collapse: {
                           withAnimation(.easeInOut(duration: 0.15)) {
                               expanded.wrappedValue = false
                           }
                       }, motionKey: motionKey, content: content)
            } else {
                collapsedStrip(title: title, count: count, motionKey: motionKey) {
                    withAnimation(.easeInOut(duration: 0.15)) {
                        expanded.wrappedValue = true
                    }
                }
            }
        }
    }

    /// The collapsed form: count badge on top, lane title rotated 90°.
    /// Click anywhere to expand back into the normal 400pt column.
    /// v0.43: reports its frame as "strip:<key>" (flights land ON the strip
    /// when their target lane is folded away) and the count badge does its
    /// single 1.0→1.25→1.0 pop when one does.
    private func collapsedStrip(title: String, count: Int,
                                motionKey: String? = nil,
                                expand: @escaping () -> Void) -> some View {
        Button(action: expand) {
            VStack(spacing: 8) {
                Image(systemName: "chevron.right.2")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.secondary.opacity(0.7))
                    .padding(.top, 10)
                Text("\(count)")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Color.secondary.opacity(0.18))
                    .clipShape(Capsule())
                    .scaleEffect(motionKey.map { flights.pulsing.contains($0) } == true
                                 ? 1.25 : 1.0)
                // rotated title: the unrotated layout box stays text-sized, so
                // give it an explicit tall frame the rotated glyphs fit into.
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .fixedSize()
                    .rotationEffect(.degrees(90))
                    .frame(width: 16, height: 240)
                Spacer(minLength: 0)
            }
            .frame(width: 44)
            .frame(maxHeight: .infinity, alignment: .top)
            .background(Color.primary.opacity(0.018))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .help(L("点击展开", "Click to expand"))
        .boardMotionFrame("strip:\(motionKey ?? title)")
    }

    // centered ghost placeholder (the popover keeps EmptyRow) — shared by the
    // generic empty branch above and the composer-resident 待审批 lane.
    private func lanePlaceholder(_ text: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: "tray")
                .font(.system(size: 20))
                .foregroundColor(.secondary.opacity(0.35))
            Text(text)
                .font(.system(size: 11))
                .foregroundColor(.secondary.opacity(0.55))
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 28)
    }
}

// v0.33: expanded content of the 永久性完成 strip — the same search box +
// ArchiveRow list the popover's ArchiveSectionView shows, minus its own
// disclosure header (the lane header handles collapse). Deliberately outside
// multi-select: the archive is still not a board lane.
private struct ArchiveLaneContent: View {
    @ObservedObject var store: DashboardStore
    unowned let app: AppDelegate
    @State private var query = ""

    private var filtered: [ArchivedItem] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return store.visibleArchived }
        return store.visibleArchived.filter {
            $0.title.lowercased().contains(q)
                || ($0.summary?.lowercased().contains(q) ?? false)
        }
    }

    var body: some View {
        TextField(L("搜索标题 / summary…", "Search title / summary…"), text: $query)
            .textFieldStyle(.roundedBorder)
            .font(.system(size: 11))
        if filtered.isEmpty {
            EmptyRow(text: store.visibleArchived.isEmpty
                     ? L("还没有永久完成的卡", "Nothing here yet")
                     : L("无匹配项", "No matches"))
        } else {
            ForEach(filtered, id: \.id) { it in
                ArchiveRow(item: it, app: app)
            }
        }
    }
}
