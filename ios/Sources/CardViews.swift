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
        if state.selectedChannelId.map({ state.freshness(for: $0).requiresConfirm }) ?? false {
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
        let fresh = state.selectedChannelId.map { state.freshness(for: $0).label } ?? ""
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
                        LaneAction(title: L("暂缓", "Later"), verb: .defer),
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
                // §37: displayHeadline = user-pinned name → summary → display title → name
                Text(task.displayHeadline).font(.subheadline).fontWeight(.medium)
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
                // §37: displayHeadline = user-pinned name → summary → display title → name
                Text(item.displayHeadline).font(.subheadline).fontWeight(.medium)
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
                Text(task.delivered_summary ?? task.displayHeadline)
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
            .navigationTitle(card.display_title ?? card.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L("完成", "Done")) { dismiss() } } }
        }
    }
}

// MARK: - Merge suggestions (契约 §21 / §21bis) --------------------------------
// The AI merge-suggestion card mirrored on the phone: analyzing / done / failed,
// with 接受 (merge_apply) / 取消 (merge_dismiss) and — when the AI did NOT land
// on 「合并」(verdict≠merge, or a failed analysis) — a 「仍然合并」override that
// force-merges with a user-chosen primary (§21bis). Rendered at the top of the
// 提案 lane. After any action we refresh the board (the card updates/vanishes).
struct MergeSuggestionCard: View {
    let suggestion: MergeSuggestion
    let model: BoardModel?
    @EnvironmentObject var state: AppState
    @State private var busy = false
    @State private var showForceMerge = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch suggestion.status {
            case "done": doneBody
            case "failed": failedBody
            default: analyzingBody
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color.purple.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.purple.opacity(0.25)))
        .opacity(busy ? 0.55 : 1)
        .sheet(isPresented: $showForceMerge) {
            ForceMergeSheet(ids: suggestion.ids, model: model,
                            defaultPrimary: suggestion.primary) { primary in
                act {
                    let ok = await state.submitMergeForce(ids: suggestion.ids, primary: primary)
                    // 顺手 dismiss 被取代的建议（同 Mac）——只在 force 成功后。
                    if ok { _ = await state.submitMergeDismiss(suggestionId: suggestion.id) }
                    return ok
                }
            }
        }
    }

    @ViewBuilder private var analyzingBody: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(L("合并分析中…", "Analyzing merge…")).font(.subheadline).foregroundStyle(.secondary)
            Spacer()
        }
        Text(involvedLine).font(.caption).foregroundStyle(.secondary)
    }

    @ViewBuilder private var doneBody: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "arrow.triangle.merge").foregroundStyle(.purple)
            Text(verdictHeadline).font(.subheadline).fontWeight(.semibold)
            Spacer(minLength: 4)
            if let c = suggestion.confidence, !c.isEmpty { confidenceChip(c) }
        }
        if let p = suggestion.primary, !p.isEmpty {
            Text(L("主卡：", "Primary: ") + title(p)).font(.caption).fontWeight(.medium)
            ForEach(suggestion.ids.filter { $0 != p }, id: \.self) { sid in
                Text(L("副卡：", "Secondary: ") + title(sid)).font(.caption).foregroundStyle(.secondary)
            }
        } else {
            ForEach(suggestion.ids, id: \.self) { sid in
                Text("• " + title(sid)).font(.caption).foregroundStyle(.secondary)
            }
        }
        if let r = suggestion.rationale, !r.isEmpty {
            Text(r).font(.caption).foregroundStyle(.secondary)
        }
        if !suggestion.action_plan.isEmpty {
            VStack(alignment: .leading, spacing: 1) {
                Text(L("接受后将执行：", "On accept, this will:"))
                    .font(.caption2).fontWeight(.semibold).foregroundStyle(.secondary)
                ForEach(Array(suggestion.action_plan.enumerated()), id: \.offset) { i, step in
                    Text("\(i + 1). \(step)").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        buttonsRow(showAccept: true)
    }

    @ViewBuilder private var failedBody: some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Text(L("合并分析失败", "Merge analysis failed")).font(.subheadline).fontWeight(.semibold)
            Spacer()
        }
        Text(involvedLine).font(.caption).foregroundStyle(.secondary)
        if let e = suggestion.error, !e.isEmpty {
            Text(e).font(.caption2).foregroundStyle(.secondary)
        }
        buttonsRow(showAccept: false)
    }

    @ViewBuilder private func buttonsRow(showAccept: Bool) -> some View {
        if busy {
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text(L("已提交…", "Submitted…")).font(.caption).foregroundStyle(.secondary)
            }
        } else {
            HStack(spacing: 8) {
                if showAccept {
                    Button(L("接受", "Accept")) {
                        act { await state.submitMergeApply(suggestionId: suggestion.id) }
                    }
                    .buttonStyle(.bordered).controlSize(.small).tint(.green)
                }
                // 仍然合并: shown when the AI did NOT land on 「合并」(or it failed).
                if suggestion.status == "failed" || suggestion.verdict != "merge" {
                    Button(L("仍然合并", "Merge anyway")) { showForceMerge = true }
                        .buttonStyle(.bordered).controlSize(.small).tint(.purple)
                }
                Button(L("取消", "Dismiss")) {
                    act { await state.submitMergeDismiss(suggestionId: suggestion.id) }
                }
                .buttonStyle(.bordered).controlSize(.small).tint(.gray)
                Spacer()
            }
        }
    }

    /// Run a write, then (on success) refresh so the card updates/vanishes.
    private func act(_ op: @escaping () async -> Bool) {
        busy = true
        Task {
            let ok = await op()
            if ok {
                try? await Task.sleep(nanoseconds: 3_500_000_000)
                await state.refreshBoard()
            }
            busy = false
        }
    }

    private func title(_ id: String) -> String { model?.title(of: id) ?? id }
    private var involvedLine: String { suggestion.ids.map { title($0) }.joined(separator: "  +  ") }
    private var verdictHeadline: String {
        switch suggestion.verdict {
        case "merge": return L("建议合并：副卡并入主卡", "Suggest merging the secondary into the primary")
        case "link_improvement": return L("建议挂为主卡的改进卡", "Suggest linking as an improvement of the primary")
        case "keep_separate": return L("建议保持独立，不合并", "Suggest keeping them separate")
        case "close_secondary": return L("建议关闭副卡（进回收站）", "Suggest closing the secondary (to trash)")
        default: return suggestion.verdict ?? L("分析完成", "Analysis complete")
        }
    }
    @ViewBuilder private func confidenceChip(_ c: String) -> some View {
        switch c {
        case "high":   MetaChip(text: L("置信 高", "Conf: high"), color: .green)
        case "medium": MetaChip(text: L("置信 中", "Conf: med"), color: .orange)
        case "low":    MetaChip(text: L("置信 低", "Conf: low"), color: .gray)
        // §38 auto suggestions: deterministic rule, not an AI analysis
        case "deterministic": MetaChip(text: L("规则判定", "Rule-based"), color: .purple)
        default:       MetaChip(text: c, color: .gray)
        }
    }
}

// 契约 §21bis 强制合并确认弹窗（iOS）：选主卡 + 不可撤销告知 → onConfirm(primary)。
struct ForceMergeSheet: View {
    let ids: [String]
    let model: BoardModel?
    let onConfirm: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var primary: String

    init(ids: [String], model: BoardModel?, defaultPrimary: String? = nil,
         onConfirm: @escaping (String) -> Void) {
        self.ids = ids
        self.model = model
        self.onConfirm = onConfirm
        let d = defaultPrimary.flatMap { ids.contains($0) ? $0 : nil } ?? ids.first ?? ""
        _primary = State(initialValue: d)
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(ids, id: \.self) { id in
                        Button { primary = id } label: {
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: primary == id ? "largecircle.fill.circle" : "circle")
                                    .foregroundStyle(primary == id ? .purple : .secondary)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(title(id)).foregroundStyle(.primary)
                                    Text(primary == id ? L("主卡 · 保留", "Primary · kept")
                                                       : L("副卡 · 并入主卡", "Secondary · folds in"))
                                        .font(.caption)
                                        .foregroundStyle(primary == id ? .purple : .secondary)
                                }
                                Spacer()
                            }
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text(L("选一张作为主卡保留，其余全部并入它",
                           "Pick one card to keep as the primary; the rest fold in"))
                }
                Section {
                    Label(L("副卡会停止运行、进入「已合并」——不可撤销。来源与交付物保留在主卡上。",
                            "Secondaries stop and become \u{201C}merged\u{201D} — not reversible. Their sources & deliverables are kept on the primary."),
                          systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            .navigationTitle(L("强制合并 \(ids.count) 张卡片", "Force-merge \(ids.count) cards"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(L("取消", "Cancel")) { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(L("强制合并", "Force-merge")) { onConfirm(primary); dismiss() }
                        .disabled(primary.isEmpty || ids.count < 2)
                }
            }
        }
    }

    private func title(_ id: String) -> String { model?.title(of: id) ?? id }
}
