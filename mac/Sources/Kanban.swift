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
// Columns: 储备 | 提案 | 运行中(+需输入) | 待验收 | 已验收 — trash stays out.
// (v0.18: backlog moved leftmost so the board reads as a spatial flow —
// detected sits upstream of card_sent, and every action moves a card exactly
// one column to the right. Display order ONLY; the menu-bar popover keeps its
// own attention-ordered list. 储备/Backlog is the DISPLAY name of the former
// 欠账/debt lane — registry status names and the dashboard.json `debt` key
// are unchanged, 纯展示层.)

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
    // 搜索过滤: focus for the header search box (⌘F focuses, Esc clears).
    // The query itself lives in the STORE (boardQuery) so the board*
    // projections can filter — visible* 现有模式.
    @FocusState private var searchFocused: Bool
    // 搜索埋点: last non-empty query of the current search session — flushed
    // as ONE board_search event when the caret leaves the box / page switches
    // (never per keystroke). Query text itself is capture_input-gated.
    @State private var searchSessionQuery = ""

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
        // 多选态 → 底部浮出操作条（请求合并建议 ≥2 / 提建议 ≥0 / 取消）
        .overlay(alignment: .bottom) { selectionBar }
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
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 12) {
                    // 储备/Backlog leftmost (v0.18 flow order): display rename
                    // of the debt lane — the store projection (visibleDebt)
                    // and dashboard key stay. quiet: a pre-execution parking
                    // lot must not compete with proposals for attention.
                    column(title: L("储备 · backlog", "Backlog"),
                           count: debt.count + debtEchoes.count,
                           help: LaneHelp.backlog,
                           emptyText: laneEmptyText(
                               L("不着急的事会先停在这里——不会自动执行，也永不过期",
                                 "Not-urgent items park here — nothing runs on its own, nothing expires")),
                           isEmpty: debt.isEmpty && debtEchoes.isEmpty
                               && debtNotices.isEmpty,
                           quiet: true) {
                        ForEach(debtNotices) { NoticeRow(notice: $0) }
                        ForEach(debtEchoes) { PendingEchoRow(echo: $0) }
                        // v0.21 契约七: 储备卡也可多选参与合并（selectableIDs 已含 debt）。
                        ForEach(debt, id: \.id) { d in
                            selectableCard(d.id) {
                                DebtRow(item: d, app: app)
                            }
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
                           isEmpty: false) {
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
                        }
                    }
                    // needs_input merges into 运行中 — listed first with a
                    // permanent orange 需输入 badge, then a thin divider.
                    column(title: L("运行中 · running", "Running"),
                           count: running.count + needsInput.count + runningEchoes.count,
                           help: LaneHelp.running,
                           emptyText: laneEmptyText(
                               L("没有正在执行的任务。批准一个提案，AI 就开始干活",
                                 "Nothing running — approve a proposal to start")),
                           isEmpty: running.isEmpty && needsInput.isEmpty
                               && runningEchoes.isEmpty && runningNotices.isEmpty) {
                        ForEach(runningNotices) { NoticeRow(notice: $0) }
                        ForEach(runningEchoes) { PendingEchoRow(echo: $0) }
                        ForEach(needsInput, id: \.id) { t in
                            selectableCard(t.id) {
                                TaskRow(task: t, app: app, lane: .needsInput)
                            }
                        }
                        if !needsInput.isEmpty && !running.isEmpty {
                            Divider().opacity(0.5)
                        }
                        ForEach(running, id: \.id) { t in
                            selectableCard(t.id) {
                                TaskRow(task: t, app: app, lane: .running)
                            }
                        }
                    }
                    column(title: L("待验收 · review", "Review"),
                           count: reviews.count,
                           help: LaneHelp.review,
                           emptyText: laneEmptyText(
                               L("没有等你验收的交付", "No drafts waiting for your review")),
                           isEmpty: reviews.isEmpty && reviewNotices.isEmpty) {
                        ForEach(reviewNotices) { NoticeRow(notice: $0) }
                        ForEach(reviews, id: \.id) { r in
                            selectableCard(r.id) {
                                ReviewRow(item: r, app: app)
                            }
                        }
                    }
                    // English twin Delivered→Done (v0.18, display-only):
                    // delivery happens at the review stage; this lane means
                    // "you accepted it". Registry status `delivered` frozen.
                    column(title: L("已验收 · done", "Done"),
                           count: completed.count + completedEchoes.count,
                           help: LaneHelp.done,
                           emptyText: laneEmptyText(
                               L("还没有验收过的交付", "Nothing accepted yet")),
                           isEmpty: completed.isEmpty && completedEchoes.isEmpty
                               && completedNotices.isEmpty) {
                        ForEach(completedNotices) { NoticeRow(notice: $0) }
                        ForEach(completedEchoes) { PendingEchoRow(echo: $0) }
                        // v0.21 契约七: 已验收卡也可多选参与合并（selectableIDs 已含 completed）。
                        ForEach(completed, id: \.id) { t in
                            selectableCard(t.id) {
                                TaskRow(task: t, app: app, lane: .completed)
                            }
                        }
                    }
                }
                .padding(16)
            }
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
    /// lane — 储备/待审批/运行中(含需输入)/待验收/已验收 — minus placeholders
    /// (processing), echoes, and suggestion cards. v0.21: 全 lane 可选（含
    /// 储备/debt + 已验收/completed）；跨状态合并的合法性交由后端 merge_review
    /// 判定 —— Swift 侧只保持选择 UI 宽松，不预先拦截。归档区不是看板列，不在此
    /// 多选面里。Selection is re-validated against this at submit time (a card
    /// may have moved lanes since it was ticked).
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
            HStack(spacing: 10) {
                if selectedIDs.count >= 2 {
                    Button {
                        submitSelection()
                    } label: {
                        Text(L("请求合并建议 (\(selectedIDs.count))",
                               "Suggest merge (\(selectedIDs.count))"))
                            .font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.purple)   // 建议卡同款紫色 accent
                }
                // 建议上报: ≥0 张 — 零选中即对整体提建议
                Button {
                    submitFeedbackSelection()
                } label: {
                    Text(selectedIDs.isEmpty
                         ? L("提建议", "Send feedback")
                         : L("提建议 (\(selectedIDs.count))",
                             "Send feedback (\(selectedIDs.count))"))
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
                if store.isMergeAnalyzing(id) {
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

    // one lane: fixed 400pt so cards keep their popover size; header on top,
    // then an independent vertical scroll for the lane's cards.
    // help → SectionHeader's ? popover/tooltip; quiet → one notch of visual
    // quieting on the header (v0.18: backlog only, so proposals keep the eye).
    private func column<Content: View>(
        title: String, count: Int, help: String? = nil,
        emptyText: String, isEmpty: Bool, quiet: Bool = false,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionHeader(title: title, count: count, help: help)
                .opacity(quiet ? 0.65 : 1)
                .padding(.horizontal, 10)
                .padding(.top, 6)
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
