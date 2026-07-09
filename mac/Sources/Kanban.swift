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
                    column(title: L("待审批 · needs approval", "Needs Approval"),
                           count: approvals.count,
                           emptyText: L("无待审批", "Nothing awaiting approval"),
                           isEmpty: false) {
                        // resident quick-capture composer (Composer.swift)
                        KanbanComposer(app: app)
                        if approvals.isEmpty && approvalNotices.isEmpty {
                            lanePlaceholder(L("无待审批", "Nothing awaiting approval"))
                        }
                        ForEach(approvalNotices) { NoticeRow(notice: $0) }
                        ForEach(approvals, id: \.id) { card in
                            ApprovalCardView(card: card, app: app,
                                             commentPending: store.pendingComment[card.id] != nil)
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
                            TaskRow(task: t, app: app, lane: .needsInput)
                        }
                        if !needsInput.isEmpty && !running.isEmpty {
                            Divider().opacity(0.5)
                        }
                        ForEach(running, id: \.id) { t in
                            TaskRow(task: t, app: app, lane: .running)
                        }
                    }
                    column(title: L("待验收 · review", "Review"),
                           count: reviews.count,
                           emptyText: L("无待验收草稿", "No drafts to review"),
                           isEmpty: reviews.isEmpty && reviewNotices.isEmpty) {
                        ForEach(reviewNotices) { NoticeRow(notice: $0) }
                        ForEach(reviews, id: \.id) { r in
                            ReviewRow(item: r, app: app)
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
