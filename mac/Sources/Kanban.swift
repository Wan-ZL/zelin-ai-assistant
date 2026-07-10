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
// Columns: 待审批 | 运行中(+需输入) | 待验收 | 欠账 | 完成 — trash stays out.

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
            Divider()
            if store.dashboard == nil {
                emptyState
            } else {
                board
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        // 契约七: 选中 ≥2 → 底部浮出操作条（请求合并建议 / 取消）
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
            // 契约七: 「选择」enters multi-select; the same button (or Esc /
            // the bar's 取消) exits. Board-only — no dashboard, no button.
            if store.dashboard != nil {
                Button(selectMode ? L("退出选择", "Done") : L("选择", "Select")) {
                    setSelectMode(!selectMode)
                }
                .font(.system(size: 12))
            }
            RecordingMenuButton()
        }
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
            // visibleApprovals prepends the quick-capture / raisingLocal grey
            // processing placeholders — identical behavior to the popover.
            let approvals = store.visibleApprovals
            let reviews = store.visibleReview
            let debt = store.visibleDebt
            // v0.10.3 契约一: sorted+hidden-filtered projections, shared with
            // the popover so both surfaces always agree.
            let running = store.visibleRunning
            let needsInput = store.visibleNeedsInput
            let completed = store.visibleCompleted
            // merge-review 契约七: suggestion cards (dismiss-echo filtered)
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
                    // isEmpty: false — the resident composer means this lane
                    // always has content; the ghost placeholder renders below
                    // it manually so the empty look stays the same.
                    // W8: lane display name 提案/Proposals — internal keys
                    // (needs_approval, card_sent, …) unchanged.
                    column(title: L("提案 · proposals", "Proposals"),
                           count: approvals.count + suggestions.count,
                           emptyText: L("暂无提案", "No proposals yet"),
                           isEmpty: false) {
                        // resident quick-capture composer (Composer.swift)
                        KanbanComposer(app: app)
                        if approvals.isEmpty && approvalNotices.isEmpty
                            && suggestions.isEmpty {
                            lanePlaceholder(L("暂无提案", "No proposals yet"))
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
                           emptyText: L("无运行中任务", "No running tasks"),
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
                           emptyText: L("无待验收草稿", "No drafts to review"),
                           isEmpty: reviews.isEmpty && reviewNotices.isEmpty) {
                        ForEach(reviewNotices) { NoticeRow(notice: $0) }
                        ForEach(reviews, id: \.id) { r in
                            selectableCard(r.id) {
                                ReviewRow(item: r, app: app)
                            }
                        }
                    }
                    column(title: L("欠账 · debt", "Debt"),
                           count: debt.count + debtEchoes.count,
                           emptyText: L("无欠账", "No debt items"),
                           isEmpty: debt.isEmpty && debtEchoes.isEmpty
                               && debtNotices.isEmpty) {
                        ForEach(debtNotices) { NoticeRow(notice: $0) }
                        ForEach(debtEchoes) { PendingEchoRow(echo: $0) }
                        ForEach(debt, id: \.id) { d in
                            DebtRow(item: d, app: app)
                        }
                    }
                    column(title: L("已验收 · delivered", "Delivered"),
                           count: completed.count + completedEchoes.count,
                           emptyText: L("无已验收任务", "No delivered tasks"),
                           isEmpty: completed.isEmpty && completedEchoes.isEmpty
                               && completedNotices.isEmpty) {
                        ForEach(completedNotices) { NoticeRow(notice: $0) }
                        ForEach(completedEchoes) { PendingEchoRow(echo: $0) }
                        ForEach(completed, id: \.id) { t in
                            TaskRow(task: t, app: app, lane: .completed)
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

    /// Ids that may join a merge review right now: real cards of the
    /// 待审批/运行中(含需输入)/待验收 lanes — no placeholders (processing),
    /// no echoes, no suggestion cards. Selection is re-validated against this
    /// at submit time (a card may have moved lanes since it was ticked).
    private var selectableIDs: Set<String> {
        var s = Set(store.visibleApprovals.filter { !$0.processing }.map { $0.id })
        s.formUnion(store.visibleRunning.map { $0.id })
        s.formUnion(store.visibleNeedsInput.map { $0.id })
        s.formUnion(store.visibleReview.map { $0.id })
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

    /// 契约七: 选中 ≥2 → bottom floating action bar.
    @ViewBuilder private var selectionBar: some View {
        if selectMode && selectedIDs.count >= 2 {
            HStack(spacing: 10) {
                Button {
                    submitSelection()
                } label: {
                    Text(L("请求合并建议 (\(selectedIDs.count))",
                           "Suggest merge (\(selectedIDs.count))"))
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.borderedProminent)
                .tint(.purple)   // 建议卡同款紫色 accent
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
    private func column<Content: View>(
        title: String, count: Int, emptyText: String, isEmpty: Bool,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionHeader(title: title, count: count)
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
