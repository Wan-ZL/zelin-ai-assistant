// BoardModel.swift — the 5 fixed board lanes + the running/needs_input merge.
// SHARED between the Mac app and the iOS app. Foundation-only by contract.
//
// The Mac renders these lanes as horizontal columns; the iOS app renders them
// as a vertical paged TabView. Both consume the SAME lane order, titles, help
// copy (LaneHelp) and counts so the two surfaces tell one story (plan §6.2).

import Foundation

/// One of the five board lanes, in left-to-right / first-to-last page order:
/// 潜在任务 · 提案 · 运行中 · 待验收 · 阶段性完成 (trash + archived are off-board).
enum BoardLane: String, CaseIterable, Identifiable {
    case backlog     // 潜在任务 — dashboard.debt
    case proposals   // 提案   — dashboard.needs_approval
    case running     // 运行中 — dashboard.needs_input + dashboard.running (merged)
    case review      // 待验收 — dashboard.review
    case done        // 阶段性完成 — dashboard.completed

    var id: String { rawValue }

    /// The underlying dashboard list kind (for pending-echo routing parity with
    /// the Mac store). `.running` maps to `.running` even though it also shows
    /// needs_input rows — needs_input is a sub-state of the running lane.
    var kind: ListKind {
        switch self {
        case .backlog: return .debt
        case .proposals: return .approval
        case .running: return .running
        case .review: return .review
        case .done: return .completed
        }
    }

    /// Bilingual lane title (picked via the shared L()).
    var title: String {
        switch self {
        case .backlog: return L("潜在任务", "Backlog")
        case .proposals: return L("提案", "Proposals")
        case .running: return L("运行中", "Running")
        case .review: return L("待验收", "Review")
        case .done: return L("阶段性完成", "Done for now")
        }
    }

    /// The lane help/definition copy (shared LaneHelp).
    var help: String {
        switch self {
        case .backlog: return LaneHelp.backlog
        case .proposals: return LaneHelp.proposals
        case .running: return LaneHelp.running
        case .review: return LaneHelp.review
        case .done: return LaneHelp.done
        }
    }

    /// Badge count for the lane, read from the authoritative `Counts`. The
    /// running lane sums running + needs_input (they share one lane); done shows
    /// the TRUE total (the list itself is capped to the latest 50 upstream).
    func count(_ c: Counts) -> Int {
        switch self {
        case .backlog: return c.debt
        case .proposals: return c.needs_approval
        case .running: return c.running + c.needs_input
        case .review: return c.review
        case .done: return c.completed
        }
    }
}

/// Read-only projection of a `Dashboard` into the five lanes. Pure value logic
/// (no UI), so both clients and tests can use it.
struct BoardModel {
    let dashboard: Dashboard

    init(_ dashboard: Dashboard) { self.dashboard = dashboard }

    /// The running lane's merged rows: needs_input first (blocked → sort to the
    /// top, matching the Mac board), then the actively running tasks.
    var runningLane: [RunningTask] { dashboard.needs_input + dashboard.running }

    var backlog: [DebtItem] { dashboard.debt }
    var proposals: [ApprovalCard] { dashboard.needs_approval }
    var review: [ReviewItem] { dashboard.review }
    var done: [RunningTask] { dashboard.completed }

    func count(_ lane: BoardLane) -> Int { lane.count(dashboard.counts) }

    /// True when a running-lane row is a blocked "needs input" task (renders the
    /// orange marker, read-only on the phone — no reply path per plan §6.2).
    func isNeedsInput(_ task: RunningTask) -> Bool {
        dashboard.needs_input.contains(task)
    }

    /// Human title for a card id across the on-board lanes (contract §21 merge
    /// suggestions / §21bis force-merge picker resolve their ids through this).
    /// Falls back to the bare id when the card isn't on the board.
    func title(of id: String) -> String {
        if let c = dashboard.needs_approval.first(where: { $0.id == id }) { return c.displaySummary }
        if let r = dashboard.review.first(where: { $0.id == id }) { return r.summary ?? r.name }
        if let d = dashboard.debt.first(where: { $0.id == id }) { return d.displaySummary }
        if let t = (dashboard.running + dashboard.needs_input + dashboard.completed)
            .first(where: { $0.id == id }) { return t.summary ?? t.name }
        return id
    }
}
