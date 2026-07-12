// CardViews.swift — the five lane row types (mirroring the Mac card styling),
// the full-field detail sheet, the comment/rework composer, and the shared
// action bar. Action verbs and their lane gating match actd.process_inbox
// (plan §6.2 table). Mutating actions on a STALE/DEAD board hard-gate a confirm
// (plan §5.6).

import SwiftUI

// MARK: - shared chrome -------------------------------------------------------
private struct CardChrome<Content: View>: View {
    var dimmed: Bool = false
    @ViewBuilder var content: Content
    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
            .opacity(dimmed ? 0.55 : 1)
    }
}

private struct TierChip: View {
    let tier: String
    var body: some View {
        Text(tier).font(.system(size: 10, weight: .bold))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Color.accentColor.opacity(0.15), in: Capsule())
    }
}

private struct MetaChip: View {
    let text: String; var color: Color = .secondary
    var body: some View {
        Text(text).font(.system(size: 10, weight: .semibold)).foregroundStyle(color)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
    }
}

// MARK: - action model + bar --------------------------------------------------
enum TextNeed { case none, optional, required }

struct LaneAction: Identifiable {
    let title: String
    let verb: InboxVerb
    var destructive = false
    var tint: Color? = nil
    var textNeed: TextNeed = .none
    var placeholder: String = ""
    var id: String { title }
}

/// The row action bar: renders buttons, gates mutating actions behind a
/// freshness confirm, and drives the composer for text actions.
struct ActionBar: View {
    @EnvironmentObject var state: AppState
    let cardId: String
    let actions: [LaneAction]

    @State private var composer: LaneAction?
    @State private var confirm: LaneAction?

    var body: some View {
        HStack(spacing: 8) {
            ForEach(actions) { a in
                Button(role: a.destructive ? .destructive : nil) { tap(a) } label: { Text(a.title) }
                    .buttonStyle(.bordered).controlSize(.small)
                    .tint(a.tint)
            }
        }
        .sheet(item: $composer) { a in
            ComposerSheet(title: a.title, placeholder: a.placeholder, required: a.textNeed == .required) { text in
                fire(a, comment: text)
            }
        }
        .confirmationDialog(confirmMessage, isPresented: confirmBinding, titleVisibility: .visible) {
            if let a = confirm {
                Button(a.title, role: a.destructive ? .destructive : nil) { fire(a, comment: nil) }
                Button(L("取消", "Cancel"), role: .cancel) {}
            }
        }
    }

    private func tap(_ a: LaneAction) {
        if a.textNeed != .none { composer = a; return }
        if state.selectedDeviceId.map({ state.freshness(for: $0).requiresConfirm }) ?? false {
            confirm = a
        } else {
            fire(a, comment: nil)
        }
    }

    private func fire(_ a: LaneAction, comment: String?) {
        Task {
            if await state.submit(cardId: cardId, verb: a.verb, comment: comment) {
                try? await Task.sleep(nanoseconds: 3_500_000_000)
                await state.refreshBoard()
            }
        }
    }

    private var confirmBinding: Binding<Bool> {
        Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } })
    }
    private var confirmMessage: String {
        let fresh = state.selectedDeviceId.map { state.freshness(for: $0).label } ?? ""
        return L("这台设备的看板可能已过时（\(fresh)）。仍要继续吗？",
                 "This device's board may be out of date (\(fresh)). Continue anyway?")
    }
}

// MARK: - composer ------------------------------------------------------------
struct ComposerSheet: View {
    let title: String; let placeholder: String; let required: Bool
    let onSend: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            VStack {
                TextEditor(text: $text).frame(minHeight: 140)
                    .overlay(alignment: .topLeading) {
                        if text.isEmpty {
                            Text(placeholder).foregroundStyle(.secondary).padding(8).allowsHitTesting(false)
                        }
                    }
                    .padding(.horizontal, 8)
                Spacer()
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L("取消", "Cancel")) { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(L("发送", "Send")) { onSend(text); dismiss() }
                        .disabled(required && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}

// MARK: - Proposals (needs_approval) ------------------------------------------
struct ProposalCardRow: View {
    let card: ApprovalCard
    @State private var showDetail = false

    var body: some View {
        CardChrome(dimmed: card.processing) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    TierChip(tier: card.tier)
                    if card.green_sign == true { MetaChip(text: L("低风险", "Low-risk"), color: .green) }
                    if let d = card.days_left { MetaChip(text: L("剩 \(d) 天", "\(d)d left"),
                                                         color: d <= 1 ? .red : .secondary) }
                    if card.show_cost, let c = card.cost_usd { MetaChip(text: String(format: "$%.2f", c)) }
                    Spacer()
                    if card.processing { ProgressView().controlSize(.mini) }
                }
                Text(card.displaySummary).font(.subheadline).fontWeight(.medium)
                if let repo = card.target_repo { Text(repo).font(.caption).foregroundStyle(.secondary) }

                if !card.processing {
                    ActionBar(cardId: card.id, actions: [
                        LaneAction(title: L("批准", "Approve"), verb: .approve, tint: .green),
                        LaneAction(title: L("修改", "Comment"), verb: .comment, textNeed: .optional,
                                   placeholder: L("补充方向 / 修改意见…", "Add direction / changes…")),
                        LaneAction(title: L("存备选", "Backlog"), verb: .defer),
                        LaneAction(title: L("拒绝", "Reject"), verb: .reject, destructive: true, tint: .red),
                    ])
                }
                Button(L("展开详情", "Details")) { showDetail = true }
                    .font(.caption).buttonStyle(.plain).foregroundStyle(.tint)
            }
        }
        .sheet(isPresented: $showDetail) { CardDetailSheet(card: card) }
    }
}

// MARK: - Backlog (debt) ------------------------------------------------------
struct DebtRow: View {
    let item: DebtItem
    var body: some View {
        CardChrome {
            VStack(alignment: .leading, spacing: 8) {
                Text(item.displaySummary).font(.subheadline).fontWeight(.medium)
                if let h = item.hardness { MetaChip(text: h) }
                ActionBar(cardId: item.id, actions: [
                    LaneAction(title: L("研究并提议", "Research & propose"), verb: .raise, tint: .accentColor),
                    LaneAction(title: L("删除", "Delete"), verb: .trash, destructive: true, tint: .red),
                ])
            }
        }
    }
}

// MARK: - Running (+needs_input, read-only) -----------------------------------
struct RunningRow: View {
    let task: RunningTask
    let needsInput: Bool
    var body: some View {
        CardChrome(dimmed: task.state == "queued") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    if needsInput { MetaChip(text: L("需输入", "Needs input"), color: .orange) }
                    if task.state == "queued" { MetaChip(text: L("排队中", "Queued")) }
                    Spacer()
                }
                Text(task.summary ?? task.name).font(.subheadline).fontWeight(.medium)
                if needsInput, let w = task.waiting_for {
                    Text(w).font(.caption).foregroundStyle(.secondary)
                }
                // needs_input has no phone reply path (plan §6.2) — read-only.
                if !needsInput {
                    ActionBar(cardId: task.id, actions: [
                        LaneAction(title: L("停止", "Stop"), verb: .abort_execution, destructive: true, tint: .red),
                        LaneAction(title: L("已在别处完成", "Done elsewhere"), verb: .done_external),
                    ])
                }
            }
        }
    }
}

// MARK: - Review --------------------------------------------------------------
struct ReviewRow: View {
    let item: ReviewItem
    var body: some View {
        CardChrome {
            VStack(alignment: .leading, spacing: 8) {
                Text(item.summary ?? item.name).font(.subheadline).fontWeight(.medium)
                if let ds = item.delivered_summary { Text(ds).font(.caption).foregroundStyle(.secondary) }
                if !item.dod.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(item.dod, id: \.self) { Text("• \($0)").font(.caption2).foregroundStyle(.secondary) }
                    }
                }
                ActionBar(cardId: item.id, actions: [
                    LaneAction(title: L("验收", "Accept"), verb: .accept, tint: .green),
                    LaneAction(title: L("打回", "Send back"), verb: .rework, tint: .orange,
                               textNeed: .required, placeholder: L("说明要改什么（必填）…", "What to change (required)…")),
                ])
            }
        }
    }
}

// MARK: - Done (completed) ----------------------------------------------------
struct DoneRow: View {
    let task: RunningTask
    var body: some View {
        CardChrome {
            VStack(alignment: .leading, spacing: 8) {
                Text(task.delivered_summary ?? task.summary ?? task.name)
                    .font(.subheadline).foregroundStyle(.secondary)
                ActionBar(cardId: task.id, actions: [
                    LaneAction(title: L("退回待验收", "Reopen review"), verb: .revert_review),
                ])
            }
        }
    }
}

// MARK: - detail sheet --------------------------------------------------------
struct CardDetailSheet: View {
    let card: ApprovalCard
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section(L("摘要", "Summary")) { Text(card.displaySummary) }
                if let repo = card.target_repo {
                    Section(L("目标", "Target")) {
                        Text(repo)
                        if let name = card.target_name { Text(name).foregroundStyle(.secondary) }
                        if let kind = card.target_kind { Text(kind).foregroundStyle(.secondary) }
                    }
                }
                Section(L("规格", "Spec")) {
                    LabeledContent(L("等级", "Tier"), value: card.tier)
                    if let h = card.hardness { LabeledContent(L("难度", "Hardness"), value: h) }
                    if let d = card.days_left { LabeledContent(L("剩余天数", "Days left"), value: "\(d)") }
                    if card.show_cost, let c = card.cost_usd { LabeledContent(L("成本", "Cost"), value: String(format: "$%.2f", c)) }
                }
                if !card.plan.isEmpty {
                    Section(L("计划", "Plan")) { ForEach(card.plan, id: \.self) { Text("• \($0)") } }
                }
                if !card.dod.isEmpty {
                    Section(L("验收标准", "Acceptance criteria")) { ForEach(card.dod, id: \.self) { Text("• \($0)") } }
                }
                if !card.sources.isEmpty {
                    Section(L("来源", "Sources")) {
                        ForEach(card.sources, id: \.self) { s in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(s.who) · \(s.channel) · \(s.date)").font(.caption).foregroundStyle(.secondary)
                                Text(s.quote).font(.caption)
                            }
                        }
                    }
                }
                Section {
                    ActionBar(cardId: card.id, actions: [
                        LaneAction(title: L("批准", "Approve"), verb: .approve, tint: .green),
                        LaneAction(title: L("修改", "Comment"), verb: .comment, textNeed: .optional,
                                   placeholder: L("补充方向 / 修改意见…", "Add direction / changes…")),
                        LaneAction(title: L("拒绝", "Reject"), verb: .reject, destructive: true, tint: .red),
                    ])
                }
            }
            .navigationTitle(card.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L("完成", "Done")) { dismiss() } } }
        }
    }
}
