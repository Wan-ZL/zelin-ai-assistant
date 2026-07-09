// Cards.swift — 卡片与行组件：SectionHeader / EmptyRow / Badge / ApprovalCardView / TaskRow / ReviewRow / DebtRow / TrashSectionView / TrashRow
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

struct SectionHeader: View {
    let title: String
    let count: Int
    var body: some View {
        HStack(spacing: 6) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.secondary)
            Text("\(count)")
                .font(.system(size: 11, weight: .bold))
                .padding(.horizontal, 6)
                .padding(.vertical, 1)
                .background(Color.secondary.opacity(0.18))
                .clipShape(Capsule())
            Spacer()
        }
        .padding(.top, 4)
    }
}

struct EmptyRow: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 11))
            .foregroundColor(.secondary.opacity(0.7))
            .padding(.leading, 2)
    }
}

struct Badge: View {
    let text: String
    let color: Color
    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.18))
            .foregroundColor(color)
            .clipShape(Capsule())
    }
}

// MARK: - CardSurface (契约1) — the ONE card chrome all five rows share
//
// Background/stroke/padding/corner + optional whole-card click-to-copy
// (clipboard→✓ trailing icon, hover tint, pointing-hand cursor) + pending
// (greyed, no interaction, no stroke). The actions slot gets the unified
// button styling (font 11 / .bordered / .small); callers only supply
// Button + .tint.
//
// v0.10: optional detail slot — pass `detail:` and the surface renders the
// unified 展开详情/收起 toggle at the end of the actions row; the expanded
// state lives in an internal @State unless the caller passes `expanded:`
// (ApprovalCardView needs it for the T2 gate).

struct CardSurface<Content: View, Actions: View, Detail: View>: View {
    let accent: Color?              // nil → primary.opacity(bgOpacity); else accent.opacity(0.06)
    let bgOpacity: Double
    let padding: CGFloat
    let cornerRadius: CGFloat
    let stroked: Bool               // primary.opacity(0.12) stroke; suppressed when pending
    let copyText: String?           // non-nil: whole-card tap copies + trailing clipboard→✓
    let trailingIcon: (name: String, color: Color)?  // ignored when copyText != nil
    let pending: Bool               // opacity 0.75 + no tap + no stroke
    let expandedBinding: Binding<Bool>?   // nil → internal @State drives the detail slot
    private let detail: (() -> Detail)?  // nil → no expandable detail
    private let actions: () -> Actions
    private let content: () -> Content
    private let hasActions: Bool
    private var hasDetail: Bool { detail != nil }

    // click-to-copy feedback, internal to the surface (1.5 s reset)
    @State private var copied = false
    @State private var hovering = false
    // detail-slot disclosure when the caller doesn't pass a binding
    @State private var expandedInternal = false

    // designated init — every public init funnels here (the detailOrNil label
    // keeps it out of overload resolution against the @ViewBuilder variants)
    fileprivate init(accent: Color?, bgOpacity: Double, padding: CGFloat,
                     cornerRadius: CGFloat, stroked: Bool, copyText: String?,
                     trailingIcon: (name: String, color: Color)?, pending: Bool,
                     expandedBinding: Binding<Bool>?,
                     detailOrNil: (() -> Detail)?,
                     actions: @escaping () -> Actions,
                     content: @escaping () -> Content) {
        self.accent = accent
        self.bgOpacity = bgOpacity
        self.padding = padding
        self.cornerRadius = cornerRadius
        self.stroked = stroked
        self.copyText = copyText
        self.trailingIcon = trailingIcon
        self.pending = pending
        self.expandedBinding = expandedBinding
        self.detail = detailOrNil
        self.actions = actions
        self.content = content
        self.hasActions = Actions.self != EmptyView.self
    }

    /// Full init incl. the optional detail slot; expanded == nil → self-managed.
    init(accent: Color? = nil,
         bgOpacity: Double = 0.03,
         padding: CGFloat = 8,
         cornerRadius: CGFloat = 6,
         stroked: Bool = false,
         copyText: String? = nil,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         expanded: Binding<Bool>? = nil,
         @ViewBuilder actions: @escaping () -> Actions,
         @ViewBuilder detail: @escaping () -> Detail,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  trailingIcon: trailingIcon, pending: pending,
                  expandedBinding: expanded, detailOrNil: detail,
                  actions: actions, content: content)
    }

    private var isExpanded: Bool { expandedBinding?.wrappedValue ?? expandedInternal }

    var body: some View {
        let base = card
        if let text = copyText, !pending {
            base
                .contentShape(Rectangle())
                .onTapGesture { copy(text) }
                .onHover { h in
                    hovering = h
                    if h { NSCursor.pointingHand.push() } else { NSCursor.pop() }
                }
        } else {
            base
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 8) {
            if copyText != nil || trailingIcon != nil {
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 8) { content() }
                    Spacer(minLength: 4)
                    if copyText != nil {
                        Image(systemName: copied ? "checkmark" : "doc.on.clipboard")
                            .font(.system(size: 10))
                            .foregroundColor(copied ? .green : .secondary)
                    } else if let icon = trailingIcon {
                        Image(systemName: icon.name)
                            .font(.system(size: 10))
                            .foregroundColor(icon.color)
                    }
                }
            } else {
                content()
            }
            if let detail, isExpanded {
                detail()
            }
            if hasActions || hasDetail {
                HStack(spacing: 8) {
                    actions()
                    if hasDetail {
                        Spacer(minLength: 4)
                        Button {
                            toggleExpanded()
                        } label: {
                            Text(isExpanded ? L("收起 ▾", "Collapse ▾")
                                            : L("展开详情 ▸", "Details ▸"))
                        }
                        .tint(.secondary)
                    }
                }
                .font(.system(size: 11))
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(padding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(backgroundColor)
        .overlay {
            if stroked && !pending {
                RoundedRectangle(cornerRadius: cornerRadius)
                    .stroke(Color.primary.opacity(0.12))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        .opacity(pending ? 0.75 : 1)
    }

    private var backgroundColor: Color {
        if let accent { return accent.opacity(0.06) }
        let op = (copyText != nil && hovering && !pending) ? 0.06 : bgOpacity
        return Color.primary.opacity(op)
    }

    private func copy(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
    }

    private func toggleExpanded() {
        withAnimation(.easeInOut(duration: 0.15)) {
            if let b = expandedBinding { b.wrappedValue.toggle() }
            else { expandedInternal.toggle() }
        }
    }
}

extension CardSurface where Detail == EmptyView {
    /// Pre-v0.10 init — actions + content, no detail slot (existing call sites).
    init(accent: Color? = nil,
         bgOpacity: Double = 0.03,
         padding: CGFloat = 8,
         cornerRadius: CGFloat = 6,
         stroked: Bool = false,
         copyText: String? = nil,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         @ViewBuilder actions: @escaping () -> Actions,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  trailingIcon: trailingIcon, pending: pending,
                  expandedBinding: nil, detailOrNil: nil,
                  actions: actions, content: content)
    }
}

extension CardSurface where Actions == EmptyView, Detail == EmptyView {
    /// Convenience init for cards without a button row.
    init(accent: Color? = nil,
         bgOpacity: Double = 0.03,
         padding: CGFloat = 8,
         cornerRadius: CGFloat = 6,
         stroked: Bool = false,
         copyText: String? = nil,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  trailingIcon: trailingIcon, pending: pending,
                  expandedBinding: nil, detailOrNil: nil,
                  actions: { EmptyView() }, content: content)
    }
}

extension CardSurface where Actions == EmptyView {
    /// Detail slot without a button row — the toggle renders alone.
    init(accent: Color? = nil,
         bgOpacity: Double = 0.03,
         padding: CGFloat = 8,
         cornerRadius: CGFloat = 6,
         stroked: Bool = false,
         copyText: String? = nil,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         expanded: Binding<Bool>? = nil,
         @ViewBuilder detail: @escaping () -> Detail,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  trailingIcon: trailingIcon, pending: pending,
                  expandedBinding: expanded, detailOrNil: detail,
                  actions: { EmptyView() }, content: content)
    }
}

// MARK: - Detail-slot building blocks (shared by TaskRow / ReviewRow / DebtRow)
//
// fileprivate on purpose: other agents are adding views concurrently this
// batch — keeping these file-scoped avoids cross-file name collisions.
// No .textSelection inside: these live in click-to-copy cards (TaskRow NOTE).

/// 「需求来自」 quotes — same rendering as the approval card's expanded area.
fileprivate struct SourceListView: View {
    let sources: [Source]
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("💬 需求来自", "💬 Requested by"))
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(.secondary)
            ForEach(Array(sources.enumerated()), id: \.offset) { _, s in
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(s.who) · \(s.channel) · \(s.date)")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.secondary)
                    Text(linkified(s.quote))
                        .font(.system(size: 10))
                        .foregroundColor(Color.secondary.opacity(0.85))
                        .italic()
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

/// 「要做什么」 numbered plan; "[修改方向]" rework-direction lines pop in orange.
fileprivate struct PlanListView: View {
    let plan: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(L("📋 要做什么", "📋 Plan"))
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(.secondary)
            ForEach(Array(plan.enumerated()), id: \.offset) { i, step in
                let rework = step.hasPrefix("[修改方向]")
                Text(linkified("\(i + 1). \(step)"))
                    .font(.system(size: 11, weight: rework ? .semibold : .regular))
                    .foregroundColor(rework ? .orange : .primary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// 「怎样算办完」 numbered DoD list (approval-card styling).
fileprivate struct DodListView: View {
    let dod: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(L("怎样算办完：", "Definition of done:"))
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)
            ForEach(Array(dod.enumerated()), id: \.offset) { i, d in
                Text("\(i + 1). \(d)")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// One-line mono path that copies itself on click (clipboard→✓, 1.5 s reset).
/// A Button so its tap wins over the whole-card copy gesture underneath.
/// Internal (not fileprivate like its siblings above): P1-4/P1-5 reuse it for
/// the pipeline-health banner and the shared empty state (Freshness.swift).
struct CopyPathLine: View {
    let label: String
    let path: String
    @State private var copied = false
    var body: some View {
        Button {
            let pb = NSPasteboard.general
            pb.clearContents()
            pb.setString(path, forType: .string)
            copied = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: copied ? "checkmark" : "doc.on.clipboard")
                    .font(.system(size: 9))
                    .foregroundColor(copied ? .green : .secondary)
                Text(label + path)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .buttonStyle(.plain)
    }
}

// Epoch-seconds variants — RelativeTime itself lives in Utils.swift (another
// owner's file this batch), so the additions stay in a local extension.
fileprivate extension RelativeTime {
    /// Relative age from epoch seconds, same wording as since(_ iso:).
    static func sinceEpoch(_ epoch: Int?) -> String? {
        guard let e = epoch, e > 0 else { return nil }
        let secs = Date().timeIntervalSince(Date(timeIntervalSince1970: TimeInterval(e)))
        if secs < 60 { return L("刚刚", "just now") }
        let mins = Int(secs / 60)
        if mins < 60 { return L("\(mins)分钟前", "\(mins)m ago") }
        let hours = mins / 60
        if hours < 24 { return L("\(hours)小时前", "\(hours)h ago") }
        return L("\(hours / 24)天前", "\(hours / 24)d ago")
    }

    /// Compact duration between two epoch seconds ("5分钟" / "2小时10分" / "3天2小时").
    static func duration(from: Int?, to: Int?) -> String? {
        guard let a = from, let b = to, a > 0, b >= a else { return nil }
        let secs = b - a
        if secs < 60 { return L("\(secs)秒", "\(secs)s") }
        let mins = secs / 60
        if mins < 60 { return L("\(mins)分钟", "\(mins)m") }
        let hours = mins / 60
        if hours < 24 {
            let m = mins % 60
            return m == 0 ? L("\(hours)小时", "\(hours)h") : L("\(hours)小时\(m)分", "\(hours)h \(m)m")
        }
        let days = hours / 24
        let h = hours % 24
        return h == 0 ? L("\(days)天", "\(days)d") : L("\(days)天\(h)小时", "\(days)d \(h)h")
    }
}

// MARK: - PendingEchoRow — greyed spinner echo in the TARGET list (契约2)

struct PendingEchoRow: View {
    let echo: PendingEcho
    var body: some View {
        CardSurface(pending: true) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                VStack(alignment: .leading, spacing: 2) {
                    Text(echo.title)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if !echo.label.isEmpty {
                        Text(echo.label)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
                Spacer()
            }
        }
    }
}

// MARK: - NoticeRow — placeholder-timeout strip (capture = yellow, raise = orange)

struct NoticeRow: View {
    let notice: LocalNotice
    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 10))
                .foregroundColor(color)
            Text(notice.text)
                .font(.system(size: 10))
                .foregroundColor(.primary.opacity(0.8))
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.14))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var color: Color {
        notice.kind == .captureTimeout ? .yellow : .orange
    }
}

struct ApprovalCardView: View {
    let card: ApprovalCard
    unowned let app: AppDelegate
    // instant comment feedback: card stays in place with a blue merging line
    // (parent passes store.pendingComment[card.id] != nil)
    var commentPending: Bool = false
    // v0.1 §7: collapsed by default. v0.10: the toggle itself renders in the
    // CardSurface base (detail slot); the binding stays here for the T2 gate.
    @State private var expanded = false

    var body: some View {
        if card.processing { processingBody } else { normalBody }
    }

    // greyed spinner placeholder while AI expands a just-raised debt (§ raise UX)
    private var processingBody: some View {
        // P1-4 honest feedback: with the pipeline not ok, "analyzing (2-3 min)"
        // would be a promise nothing can keep — say where the capture actually
        // is (queued on disk) and drop the spinner until the pipeline is back.
        let stalled = card.id.hasPrefix("capture-") && app.store.pipelineHealth != .ok
        return CardSurface(bgOpacity: 0.04, padding: 10, cornerRadius: 8, pending: true) {
            HStack(spacing: 10) {
                if stalled {
                    Image(systemName: "tray.and.arrow.down")
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                } else {
                    ProgressView().controlSize(.small)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.displaySummary)
                        .font(.system(size: 13))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    // quick-capture placeholders (id "capture-…") get honest
                    // wording + expectation; raised debts keep the research copy.
                    Text(stalled
                         ? L("已保存到队列，pipeline 启动后开始处理",
                             "Saved to the queue — processed once the pipeline is running")
                         : card.id.hasPrefix("capture-")
                         ? L("已提交，AI 分析中（通常 2-3 分钟）",
                             "Submitted — analyzing (usually 2-3 min)")
                         : L("AI 研究中…（补全上下文、生成提案）",
                             "AI researching… (gathering context, drafting proposal)"))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
    }

    private var normalBody: some View {
        CardSurface(bgOpacity: 0.04, padding: 10, cornerRadius: 8, stroked: true,
                    expanded: $expanded) {
            // buttons row (base applies font/bordered/small + the detail toggle)
            Button {
                if card.tier == "T2" {
                    // typed confirmation (确认 / go) — anything else = no-op.
                    guard app.confirmT2(id: card.id, summary: card.displaySummary) else { return }
                }
                app.submit(id: card.id, action: "approve", comment: nil)
            } label: { Label(L("批准", "Approve"), systemImage: "checkmark.circle.fill") }
                .tint(.green)
                .disabled(card.tier == "T2" && !expanded)

            Button {
                // v0.10.3: reject asks which kind (Zelin 拍板)。区分是功能性的：
                // 回收站条目不参与 merge_or_new 匹配，同一需求会重新出卡；
                // "已办完"(done_external→delivered) 才能把后续重述压成合并。
                // 拒绝是低频操作，多一次点击可接受，按钮行保持三个。
                let alert = NSAlert()
                alert.messageText = L("这张卡不需要执行？", "No need to run this card?")
                alert.informativeText = card.displaySummary
                alert.addButton(withTitle: L("不想做（进回收站）", "Won't do (to trash)"))
                alert.addButton(withTitle: L("已办完（记为已交付）", "Already done (mark delivered)"))
                let cancel = alert.addButton(withTitle: L("取消", "Cancel"))
                cancel.keyEquivalent = "\u{1b}"
                switch alert.runModal() {
                case .alertFirstButtonReturn:
                    app.submit(id: card.id, action: "reject", comment: nil)
                case .alertSecondButtonReturn:
                    app.submit(id: card.id, action: "done_external", comment: nil)
                default:
                    break
                }
            } label: { Label(L("拒绝", "Reject"), systemImage: "xmark.circle.fill") }
                .tint(.red)

            Button {
                if let c = app.promptComment() {
                    app.submit(id: card.id, action: "comment", comment: c)
                }
            } label: { Label(L("修改", "Comment"), systemImage: "bubble.left.fill") }
                .tint(.blue)
        } detail: {
            // expanded detail blocks (sources + plan + long title) — rendered
            // by the base between content and the buttons row, as before.
            expandedDetail
        } content: {
            // §16 self-improvement lineage: first line when this improves another req.
            if let imp = card.improvement_of, !imp.isEmpty {
                Text(L("↳ 改进 #\(imp)", "↳ Improves #\(imp)"))
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(.teal)
            }

            // 1) plain-language summary — prominent, black, ~15pt.
            Text(linkified(card.displaySummary))
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(.primary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            // 2) target line (repo destination).
            targetLine

            // 3) badge row.
            badgeRow

            // §11 验收标准 — visible by default: approving the card approves this.
            if !card.dod.isEmpty {
                VStack(alignment: .leading, spacing: 1) {
                    Text(L("怎样算办完：", "Definition of done:"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.secondary)
                    ForEach(Array(card.dod.enumerated()), id: \.offset) { i, d in
                        Text("\(i + 1). \(d)")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                    }
                }
            }

            // disagreement (red box) — a warning; kept visible in both states.
            if let dis = card.disagreement, !dis.isEmpty {
                Text(L("⚠︎ 分歧: ", "⚠︎ Disagreement: ") + dis)
                    .font(.system(size: 11))
                    .foregroundColor(.red)
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.red.opacity(0.10))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.red.opacity(0.5)))
            }

            // comment sent → in-place blue feedback until actd regenerates
            if commentPending {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small).scaleEffect(0.7)
                    Text(L("修改意见合并中…", "Merging your feedback…"))
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.blue)
                }
            }

            // (expanded detail blocks moved to the CardSurface detail slot.)

            // T2 gate hint: approve unlocks only after expanding the details.
            if card.tier == "T2" && !expanded {
                Text(L("T2 需先展开看明细", "T2: expand details first"))
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(.orange)
            }
        }
    }

    // MARK: target line

    @ViewBuilder private var targetLine: some View {
        if let kind = card.target_kind, let name = targetName {
            if kind == "new" {
                Text(L("🟢 新建 repo: \(name)", "🟢 New repo: \(name)"))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.green)
                    .fixedSize(horizontal: false, vertical: true)
            } else if kind == "existing" {
                // your-workbench = the paperwork drafts home, not a code
                // change — say so instead of the misleading "modify existing"
                if name.hasSuffix("your-workbench") {
                    Text(L("📄 草稿落点: your-workbench（只出文档，不动任何代码）",
                           "📄 Drafts land in: your-workbench (documents only, no code touched)"))
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(L("🟠 修改现有: \(name)（只提 draft PR，不动主分支）",
                           "🟠 Modify existing: \(name) (draft PR only, main branch untouched)"))
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// basename preference: target_name, else last path component of target_repo.
    private var targetName: String? {
        if let n = card.target_name, !n.isEmpty { return n }
        if let repo = card.target_repo, !repo.isEmpty {
            return (repo as NSString).lastPathComponent
        }
        return nil
    }

    // MARK: badge row

    @ViewBuilder private var badgeRow: some View {
        HStack(spacing: 6) {
            Badge(text: tierText, color: .purple)
            // v0.10 chat delivery: draft lands in the reply, no repo/PR touched
            if card.delivery_mode == "chat" {
                Badge(text: L("交付：聊天成稿", "Deliver: chat draft"), color: .blue)
            }
            if let dl = card.deadline, !dl.isEmpty {
                let urgent = (card.days_left ?? 99) <= 3
                let daysStr = card.days_left.map { " (\($0)d)" } ?? ""
                Text(L("截止 \(dl)\(daysStr)", "Due \(dl)\(daysStr)"))
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(urgent ? .red : .secondary)
            }
            if card.show_cost, let cost = card.cost_usd {
                Badge(text: Self.money(cost), color: .secondary)
            }
            if let hard = card.hardness, !hard.isEmpty {
                Badge(text: hard, color: hard == "hard" ? .red : .gray)
            }
            if let r = card.repeated, r >= 2 {
                Badge(text: L("重复×\(r)", "Repeated ×\(r)"), color: .orange)
            }
            if card.green_sign == true {
                Badge(text: L("需 manager green-sign（只出草稿）",
                              "Needs manager green-sign (draft only)"), color: .orange)
            }
            Spacer()
        }
    }

    // MARK: expanded detail

    @ViewBuilder private var expandedDetail: some View {
        VStack(alignment: .leading, spacing: 8) {
            // long technical title lives here now.
            Text(card.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            // 「需求来自」— sources
            if !card.sources.isEmpty {
                Text(L("💬 需求来自", "💬 Requested by"))
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.secondary)
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(card.sources.enumerated()), id: \.offset) { _, s in
                        VStack(alignment: .leading, spacing: 1) {
                            Text("\(s.who) · \(s.channel) · \(s.date)")
                                .font(.system(size: 10, weight: .medium))
                                .foregroundColor(.secondary)
                                .textSelection(.enabled)
                            // Slack quotes often carry links — make them clickable
                            Text(linkified(s.quote))
                                .font(.system(size: 10))
                                .foregroundColor(Color.secondary.opacity(0.85))
                                .italic()
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                    }
                }
            }

            // 「要做什么」— plan (numbered)
            if !card.plan.isEmpty {
                Text(L("📋 要做什么", "📋 Plan"))
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(card.plan.enumerated()), id: \.offset) { i, step in
                        Text(linkified("\(i + 1). \(step)"))
                            .font(.system(size: 11))
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var tierText: String {
        if let hint = card.tier_hint, !hint.isEmpty {
            return "\(card.tier) · \(hint)"
        }
        return card.tier
    }

    private static func money(_ v: Double) -> String {
        if v == v.rounded() { return "$\(Int(v))" }
        return String(format: "$%.2f", v)
    }
}

// NOTE: no .textSelection here on purpose — the whole card copies on click
// (CardSurface.copyText); textSelection would fight the tap gesture.
// Keyboard accessibility of the former Button wrapper is traded away —
// accepted and recorded in the implementation plan.
struct TaskRow: View {
    let task: RunningTask
    unowned let app: AppDelegate
    let accent: Color
    // kanban 运行中 lane: needs_input rows always show the orange badge
    var showsInputBadge: Bool = false

    // v0.10: status=approved tasks ride in running[] as state=="queued" —
    // greyed like pending (no spinner), no session, nothing to copy yet.
    private var isQueued: Bool { task.state == "queued" }

    /// State-correct command (attach for live, --resume for done); nil → the
    /// row has nothing to copy and the whole-card tap is disabled.
    private var cmd: String? {
        guard !isQueued else { return nil }
        if let c = task.copy_cmd, !c.isEmpty { return c }
        if let sid = task.session_id, !sid.isEmpty { return "claude --resume \(sid)" }
        return nil
    }

    private var hasDetailContent: Bool {
        (task.summary?.isEmpty == false)
            || !(task.plan ?? []).isEmpty
            || !(task.dod ?? []).isEmpty
            || (task.log?.isEmpty == false)
    }

    // v0.10.2: which lane this row sits in. The lane can't be passed in —
    // Kanban.swift/DashboardView.swift belong to other buckets this batch —
    // so the accent doubles as the discriminator: every call site uses .green
    // for the delivered/completed lane and .blue/.orange for running/needs_input.
    private var isDelivered: Bool { accent == .green }

    // 契约: 停止并退回 on regular running rows (queued/working/blocked …),
    // NOT on review-active rework rows and NOT in the completed lane.
    private var showsAbort: Bool { !isDelivered && task.state != "review-active" }

    var body: some View {
        let hasButtons = showsAbort || isDelivered
        if hasDetailContent && hasButtons {
            CardSurface(copyText: cmd, pending: isQueued,
                        actions: { actionButtons },
                        detail: { detailBlock }, content: { rowContent })
        } else if hasDetailContent {
            CardSurface(copyText: cmd, pending: isQueued,
                        detail: { detailBlock }, content: { rowContent })
        } else if hasButtons {
            CardSurface(copyText: cmd, pending: isQueued,
                        actions: { actionButtons }, content: { rowContent })
        } else {
            CardSurface(copyText: cmd, pending: isQueued) { rowContent }
        }
    }

    // v0.10.2 action row — Buttons win over the whole-card copy tap (ReviewRow
    // 先例); CardSurface applies the unified font/bordered/small styling.
    @ViewBuilder private var actionButtons: some View {
        if showsAbort {
            // approved|executing → stop the run, card returns to 待审批
            Button {
                app.submit(id: task.id, action: "abort_execution", comment: nil)
            } label: { Label(L("停止并退回", "Stop & return"), systemImage: "stop.circle") }
                .tint(.orange)
        }
        if isDelivered {
            // delivered → back to REVIEW for re-acceptance
            Button {
                app.submit(id: task.id, action: "revert_review", comment: nil)
            } label: { Label(L("退回待验收", "Back to review"), systemImage: "arrow.uturn.backward") }
                .tint(.teal)
        }
        Spacer()
    }

    @ViewBuilder private var rowContent: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(isQueued ? Color.secondary.opacity(0.5) : accent)
                .frame(width: 7, height: 7).padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                Text(task.name)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(isQueued ? .secondary : .primary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    if isQueued {
                        Badge(text: L("排队中", "Queued"), color: .gray)
                    } else {
                        if showsInputBadge { Badge(text: L("需输入", "Input"), color: .orange) }
                        // v0.10 attach 回流: review req whose agent is working
                        // again — teal to tell it apart from working/queued.
                        if task.state == "review-active" {
                            Badge(text: L("验收后返工中", "reworking"), color: .teal)
                        } else if let st = task.state { Badge(text: st, color: accent) }
                        if let sid = task.short_id ?? task.session_id {
                            Text(sid.prefix(8))
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundColor(.secondary)
                        }
                        // how long it's been running (dispatch time as fallback)
                        if let age = RelativeTime.sinceEpoch(task.started_at ?? task.dispatched_at) {
                            Text(age)
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                        }
                        // completed lane: when Zelin accepted the draft
                        if let acc = RelativeTime.sinceEpoch(task.accepted_at) {
                            Text(L("验收于 ", "accepted ") + acc)
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                        }
                        if let wf = task.waiting_for, !wf.isEmpty {
                            Text(L("等待: ", "Waiting: ") + wf)
                                .font(.system(size: 10))
                                .foregroundColor(.orange)
                        }
                    }
                    if let cwd = task.cwd, !cwd.isEmpty {
                        Badge(text: (cwd as NSString).lastPathComponent, color: .secondary)
                    }
                }
                // completed lane: what actually got delivered (one line)
                if let ds = task.delivered_summary, !ds.isEmpty {
                    Text(ds)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                if let cmd {
                    Text(L("点按复制：", "Click to copy: ") + cmd)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if isQueued, let de = task.dispatch_error, !de.isEmpty {
                    Text(L("派发失败：", "Dispatch failed: ") + de)
                        .font(.system(size: 10))
                        .foregroundColor(.red)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                if !isQueued, let le = task.last_error, !le.isEmpty {
                    Text(L("错误：", "Error: ") + le)
                        .font(.system(size: 10))
                        .foregroundColor(.red)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                if let an = task.agent_name, !an.isEmpty {
                    Text(L("claude agents 列表名：", "claude agents list name: ") + an)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            // red corner mark when the executor hit an error (detail line above)
            if !isQueued, task.last_error?.isEmpty == false {
                Spacer(minLength: 4)
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 10))
                    .foregroundColor(.red)
                    .padding(.top, 2)
            }
        }
    }

    @ViewBuilder private var detailBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let s = task.summary, !s.isEmpty {
                Text(s)
                    .font(.system(size: 11))
                    .foregroundColor(.primary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let plan = task.plan, !plan.isEmpty { PlanListView(plan: plan) }
            if let dod = task.dod, !dod.isEmpty { DodListView(dod: dod) }
            if let log = task.log, !log.isEmpty {
                CopyPathLine(label: L("日志：", "Log: "), path: log)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// §11 待验收 row — draft ready; Zelin accepts or sends it back with feedback.
// Whole card = click-to-copy (CardSurface.copyText); body textSelection is
// dropped on purpose — it would fight the tap gesture (TaskRow NOTE 先例).
struct ReviewRow: View {
    let item: ReviewItem
    unowned let app: AppDelegate
    // "复制成稿" clipboard→✓ feedback (1.5 s reset, same pattern as CardSurface)
    @State private var draftCopied = false

    private var hasDetailContent: Bool {
        !(item.plan ?? []).isEmpty
            || !(item.sources ?? []).isEmpty
            || (item.log?.isEmpty == false)
    }

    var body: some View {
        if hasDetailContent {
            CardSurface(accent: .teal, copyText: item.copy_cmd,
                        actions: { actionButtons },
                        detail: { detailBlock },
                        content: { rowContent })
        } else {
            CardSurface(accent: .teal, copyText: item.copy_cmd,
                        actions: { actionButtons },
                        content: { rowContent })
        }
    }

    @ViewBuilder private var actionButtons: some View {
        Button {
            app.submit(id: item.id, action: "accept", comment: nil)
        } label: { Label(L("验收", "Accept"), systemImage: "checkmark.seal.fill") }
            .tint(.green)

        Button {
            if let fb = app.promptRework() {
                app.submit(id: item.id, action: "rework", comment: fb)
            }
        } label: { Label(L("打回", "Send Back"), systemImage: "arrowshape.turn.up.backward.fill") }
            .tint(.orange)

        // chat-delivery tasks: the finished draft is right here — copy & paste
        if let draft = item.final_draft, !draft.isEmpty {
            Button {
                let pb = NSPasteboard.general
                pb.clearContents()
                pb.setString(draft, forType: .string)
                draftCopied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { draftCopied = false }
            } label: {
                Label(draftCopied ? L("已复制 ✓", "Copied ✓") : L("复制成稿", "Copy final draft"),
                      systemImage: draftCopied ? "checkmark" : "doc.on.doc")
            }
                .tint(.teal)
        }

        Spacer()
    }

    @ViewBuilder private var rowContent: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle().fill(Color.teal).frame(width: 7, height: 7).padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(.system(size: 12, weight: .medium))
                    .fixedSize(horizontal: false, vertical: true)
                // no linkified in these body texts: link taps vs the whole-card
                // copy gesture couldn't be verified conflict-free — 放弃,
                // recorded in the completion report.
                if let ds = item.delivered_summary, !ds.isEmpty {
                    // v0.10: what the executor actually delivered = the body;
                    // the approval-time summary demotes to grey context below.
                    Text(L("交付了什么：", "Delivered:"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.secondary)
                        .padding(.top, 2)
                    Text(ds)
                        .font(.system(size: 11))
                        .foregroundColor(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let s = item.summary, !s.isEmpty {
                        Text(s)
                            .font(.system(size: 10))
                            .foregroundColor(Color.secondary.opacity(0.85))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else if let s = item.summary, !s.isEmpty {
                    Text(s)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        // §11 acceptance checklist — always rendered (fallback text when empty)
        VStack(alignment: .leading, spacing: 1) {
            Text(L("验收清单——逐条对照：", "Acceptance checklist:"))
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)
            if item.dod.isEmpty {
                Text(L("该任务未定义验收标准，请自行判断",
                       "No acceptance criteria defined — judge manually"))
                    .font(.system(size: 10))
                    .foregroundColor(Color.secondary.opacity(0.7))
            } else {
                ForEach(Array(item.dod.enumerated()), id: \.offset) { _, d in
                    Text("☐ " + d)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        // meta line: where it ran + how long it took + how long it's waited
        HStack(spacing: 6) {
            if let cwd = item.cwd, !cwd.isEmpty {
                Badge(text: (cwd as NSString).lastPathComponent, color: .secondary)
            }
            if let dur = RelativeTime.duration(from: item.dispatched_at, to: item.review_at) {
                Text(L("耗时 ", "took ") + dur)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            if let waited = RelativeTime.duration(
                from: item.review_at, to: Int(Date().timeIntervalSince1970)) {
                Text(L("已等待验收 ", "in review ") + waited)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        if let cmd = item.copy_cmd {
            // echo line only — the copy action is the whole-card tap
            Text(L("点按复制：", "Click to copy: ") + cmd)
                .font(.system(size: 9, design: .monospaced))
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        if let an = item.agent_name, !an.isEmpty {
            Text(L("claude agents 列表名：", "claude agents list name: ") + an)
                .font(.system(size: 9))
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
        }
    }

    @ViewBuilder private var detailBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let plan = item.plan, !plan.isEmpty { PlanListView(plan: plan) }
            if let srcs = item.sources, !srcs.isEmpty { SourceListView(sources: srcs) }
            if let log = item.log, !log.isEmpty {
                CopyPathLine(label: L("日志：", "Log: "), path: log)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct DebtRow: View {
    let item: DebtItem
    unowned let app: AppDelegate

    var body: some View {
        // detail slot only when there are source quotes to show — otherwise
        // the toggle would open an empty drawer.
        if let srcs = item.sources, !srcs.isEmpty {
            CardSurface(actions: { actionButtons },
                        detail: { SourceListView(sources: srcs)
                                      .frame(maxWidth: .infinity, alignment: .leading) },
                        content: { rowContent })
        } else {
            CardSurface(actions: { actionButtons }, content: { rowContent })
        }
    }

    @ViewBuilder private var actionButtons: some View {
        Button {
            app.store.beginRaising(item.id, summary: item.displaySummary)
            app.submit(id: item.id, action: "raise", comment: nil)
        } label: { Label(L("研究并提议", "Research & Propose"), systemImage: "magnifyingglass") }
            .tint(.blue)

        Button {
            app.submit(id: item.id, action: "trash", comment: nil)
        } label: { Label(L("删除", "Delete"), systemImage: "trash") }
            .tint(.red)

        Spacer()
    }

    @ViewBuilder private var rowContent: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "tray.full")
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .padding(.top, 2)
            Text(linkified(item.displaySummary))
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.primary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 4)
            if let t = item.type, !t.isEmpty { Badge(text: t, color: .gray) }
            if let h = item.hardness, !h.isEmpty {
                Badge(text: h, color: h == "hard" ? .red : .gray)
            }
        }
    }
}

// v0.1 §9: recycle bin — collapsible (collapsed by default), search box, restore/pin.
struct TrashSectionView: View {
    let items: [TrashItem]
    let count: Int
    // ids pinned locally (pin pressed, backend not confirmed yet) — passed by
    // value so the section re-renders the 永久 badge immediately
    var pinnedLocal: Set<String> = []
    unowned let app: AppDelegate
    // v0.10.2 minimal parameterization: the main-window 回收站 page opens
    // expanded (a dedicated page shouldn't hide behind a disclosure); the
    // popover keeps collapsed-by-default (v0.1 §9). No other layout deps.
    var startExpanded: Bool = false
    @State private var expanded = false
    @State private var query = ""

    private var filtered: [TrashItem] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return items }
        return items.filter {
            $0.title.lowercased().contains(q)
                || ($0.summary?.lowercased().contains(q) ?? false)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Text(expanded ? "▾" : "▸")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.secondary)
                        .padding(.top, 4)   // align with SectionHeader's top padding
                    // header body reuses the shared SectionHeader (title + count pill)
                    SectionHeader(title: L("🗑 回收站 · trash", "🗑 Trash"), count: count)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                TextField(L("搜索标题 / summary…", "Search title / summary…"), text: $query)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11))

                if filtered.isEmpty {
                    EmptyRow(text: items.isEmpty ? L("回收站为空", "Trash is empty")
                                                 : L("无匹配项", "No matches"))
                } else {
                    ForEach(filtered, id: \.id) { it in
                        TrashRow(item: it, pinnedLocally: pinnedLocal.contains(it.id), app: app)
                    }
                }
            }
        }
        // section recreated per page visit → @State is fresh, so this fires
        // reliably; a no-op in the popover (startExpanded defaults false)
        .onAppear { if startExpanded { expanded = true } }
    }
}

struct TrashRow: View {
    let item: TrashItem
    // pin pressed locally — badge flips immediately, button disappears
    var pinnedLocally: Bool = false
    unowned let app: AppDelegate

    private var isPinned: Bool { item.permanent || pinnedLocally }

    var body: some View {
        CardSurface {
            Button {
                app.submit(id: item.id, action: "restore", comment: nil)
            } label: { Label(L("恢复", "Restore"), systemImage: "arrow.uturn.left") }
                .tint(.green)

            if !isPinned {
                Button {
                    app.submit(id: item.id, action: "pin", comment: nil)
                } label: { Label(L("永久保存", "Pin"), systemImage: "pin.fill") }
                    .tint(.teal)
            }

            Spacer()
        } content: {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "trash")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .padding(.top, 2)
                Text(item.displaySummary)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                Spacer(minLength: 4)
                if isPinned {
                    Badge(text: L("永久", "Pinned"), color: .teal)
                }
            }

            // tag line: kind · reason · relative age
            HStack(spacing: 6) {
                if let k = item.kind, !k.isEmpty { Badge(text: k, color: .gray) }
                if let r = item.trash_reason, !r.isEmpty {
                    Text(r)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                if let age = RelativeTime.since(item.trashed_at) {
                    Text(age)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
    }
}
