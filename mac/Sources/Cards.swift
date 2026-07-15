// Cards.swift — 卡片与行组件：SectionHeader / EmptyRow / Badge / ApprovalCardView / TaskRow / ReviewRow / DebtRow / MergeSuggestionCard / TrashSectionView / TrashRow
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// enum LaneHelp moved to shared/Sources/Lanes.swift (shared with iOS).

struct SectionHeader: View {
    let title: String
    let count: Int
    // v0.18: optional lane definition. macOS tooltips have a ~1s delay and no
    // visual signifier, so hover-only is invisible to a novice — the ? icon is
    // always shown and CLICK is the primary path (instant popover); .help()
    // stays as the secondary hover path.
    var help: String? = nil
    @State private var showHelp = false
    var body: some View {
        HStack(spacing: 6) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.secondary)
            if let help {
                Button {
                    showHelp.toggle()
                } label: {
                    Image(systemName: "questionmark.circle")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help(help)
                .popover(isPresented: $showHelp, arrowEdge: .bottom) {
                    Text(help)
                        .font(.system(size: 12))
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(width: 280, alignment: .leading)
                        .padding(12)
                }
            }
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
// (content greyed, no card tap, no stroke — the actions row keeps full
// opacity: a queued card's live buttons must not look disabled, P2-2).
// The actions slot gets the unified button styling (font 11 / .bordered /
// .small); callers only supply Button + .tint.
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
    let doubleClickRuns: Bool       // + double-click runs copyText in the user's terminal
                                    //   (TerminalLauncher). SECURITY: opt-in ONLY for rows
                                    //   whose copyText is an app-generated claude command —
                                    //   never enable it for paths/drafts/error text.
    let trailingIcon: (name: String, color: Color)?  // ignored when copyText != nil
    let pending: Bool               // content at 0.75 + no tap + no stroke (actions stay full)
    let expandedBinding: Binding<Bool>?   // nil → internal @State drives the detail slot
    private let detail: (() -> Detail)?  // nil → no expandable detail
    private let actions: () -> Actions
    private let content: () -> Content
    private let hasActions: Bool
    private var hasDetail: Bool { detail != nil }

    // click-to-copy feedback, internal to the surface (1.5 s reset)
    @State private var copied = false
    // double-click feedback (已在终端打开 2.5 s / 打开失败 3 s). Optimistic:
    // launched flips before osascript returns — dead air until the terminal
    // window appears would read as a broken double-click.
    @State private var launched = false
    @State private var launchFailed = false
    @State private var hovering = false
    // detail-slot disclosure when the caller doesn't pass a binding
    @State private var expandedInternal = false

    // designated init — every public init funnels here (the detailOrNil label
    // keeps it out of overload resolution against the @ViewBuilder variants)
    fileprivate init(accent: Color?, bgOpacity: Double, padding: CGFloat,
                     cornerRadius: CGFloat, stroked: Bool, copyText: String?,
                     doubleClickRuns: Bool,
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
        self.doubleClickRuns = doubleClickRuns
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
         doubleClickRuns: Bool = false,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         expanded: Binding<Bool>? = nil,
         @ViewBuilder actions: @escaping () -> Actions,
         @ViewBuilder detail: @escaping () -> Detail,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  doubleClickRuns: doubleClickRuns,
                  trailingIcon: trailingIcon, pending: pending,
                  expandedBinding: expanded, detailOrNil: detail,
                  actions: actions, content: content)
    }

    private var isExpanded: Bool { expandedBinding?.wrappedValue ?? expandedInternal }

    var body: some View {
        let base = card
        if let text = copyText, !pending {
            // Single click copies IMMEDIATELY even when a double-click may
            // follow (no exclusively-before timer): copying is side-effect-
            // free, so the first click of a double-click just copies too.
            let tappable = base
                .contentShape(Rectangle())
                .onTapGesture { copy(text) }
                .onHover { h in
                    hovering = h
                    if h { NSCursor.pointingHand.push() } else { NSCursor.pop() }
                }
            if doubleClickRuns {
                tappable
                    .simultaneousGesture(TapGesture(count: 2)
                        .onEnded { runInTerminal(text) })
                    .help(L("单击复制 · 双击在终端运行",
                            "Click to copy · double-click to run in terminal"))
            } else {
                tappable
            }
        } else {
            base
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 8) {
            // pending dims the content only — the actions row below stays at
            // full opacity so a queued card's escape hatch reads as tappable.
            Group {
                if copyText != nil || trailingIcon != nil {
                    HStack(alignment: .top, spacing: 8) {
                        VStack(alignment: .leading, spacing: 8) { content() }
                        Spacer(minLength: 4)
                        if copyText != nil {
                            if launchFailed {
                                HStack(spacing: 3) {
                                    Image(systemName: "exclamationmark.triangle")
                                        .font(.system(size: 10))
                                    Text(L("打开终端失败", "Terminal launch failed"))
                                        .font(.system(size: 9))
                                }
                                .foregroundColor(.red)
                            } else if launched {
                                HStack(spacing: 3) {
                                    Image(systemName: "terminal.fill")
                                        .font(.system(size: 10))
                                    Text(L("已在终端打开", "Opened in terminal"))
                                        .font(.system(size: 9))
                                }
                                .foregroundColor(.green)
                            } else {
                                Image(systemName: copied ? "checkmark" : "doc.on.clipboard")
                                    .font(.system(size: 10))
                                    .foregroundColor(copied ? .green : .secondary)
                            }
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
            }
            .opacity(pending ? 0.75 : 1)
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

    // SECURITY: `text` is this card's copyText, which doubleClickRuns callers
    // guarantee to be an app-generated claude command (TaskRow.cmd /
    // ReviewItem.copy_cmd) — the only strings allowed into TerminalLauncher.
    private func runInTerminal(_ text: String) {
        launched = true
        launchFailed = false
        Analytics.firstReach("terminal")
        Analytics.log("card_run_in_terminal",
                      fields: ["app": TerminalLauncher.preferred.rawValue])
        TerminalLauncher.launch(text) { ok in
            if !ok {
                launched = false
                launchFailed = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { launchFailed = false }
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { launched = false }
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
         doubleClickRuns: Bool = false,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         @ViewBuilder actions: @escaping () -> Actions,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  doubleClickRuns: doubleClickRuns,
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
         doubleClickRuns: Bool = false,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  doubleClickRuns: doubleClickRuns,
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
         doubleClickRuns: Bool = false,
         trailingIcon: (name: String, color: Color)? = nil,
         pending: Bool = false,
         expanded: Binding<Bool>? = nil,
         @ViewBuilder detail: @escaping () -> Detail,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(accent: accent, bgOpacity: bgOpacity, padding: padding,
                  cornerRadius: cornerRadius, stroked: stroked, copyText: copyText,
                  doubleClickRuns: doubleClickRuns,
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

/// Full error text in a red block + a copy button (P2-4). A Button so its tap
/// wins over the whole-card copy gesture (CopyPathLine 先例); .textSelection
/// stays off in these cards — the button IS the copy path.
fileprivate struct ErrorTextBlock: View {
    let text: String
    @State private var copied = false
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(L("错误全文", "Full error"))
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(.red)
                Button {
                    let pb = NSPasteboard.general
                    pb.clearContents()
                    pb.setString(text, forType: .string)
                    copied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
                } label: {
                    HStack(spacing: 3) {
                        Image(systemName: copied ? "checkmark" : "doc.on.clipboard")
                            .font(.system(size: 9))
                        Text(copied ? L("已复制", "Copied") : L("复制", "Copy"))
                            .font(.system(size: 9))
                    }
                    .foregroundColor(copied ? .green : .secondary)
                }
                .buttonStyle(.plain)
                Spacer(minLength: 0)
            }
            Text(text)
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.red)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 6))
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

// MARK: - NoticeRow — placeholder-timeout strip (capture = yellow, raise =
// orange) or positive info strip (info = green ✓, e.g. 建议上报回执)

struct NoticeRow: View {
    let notice: LocalNotice
    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: symbol)
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
        switch notice.kind {
        case .captureTimeout: return .yellow
        case .raiseTimeout: return .orange
        case .info: return .green
        }
    }

    private var symbol: String {
        notice.kind == .info ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
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
        CardSurface(bgOpacity: 0.04, padding: 10, cornerRadius: 8, stroked: true) {
            // v0.21 拍板：四个 2 字决策按钮回到一排（批准·拒绝·修改·暂缓），每个
            // .lineLimit(1)+.fixedSize，2 字标签四颗在 ~400pt 卡宽绰绰有余、绝不
            // 截断。「展开详情」移出决策行 —— 右对齐的 plain 灰链接（disclosure，
            // 不与决策按钮抢戏），正是它腾出的空间让四颗按钮回到一排。保留 T2 gate
            //（批准在展开前禁用）+ ▸/▾ 提示与 expanded 状态。(font 11 / .bordered /
            // .small 由 CardSurface 统一施加；detail 槽的共享 toggle 不使用。)
            HStack(spacing: 8) {
                Button {
                    if card.tier == "T2" {
                        // typed confirmation (确认 / go) — anything else = no-op.
                        guard app.confirmT2(id: card.id, summary: card.displaySummary) else { return }
                    }
                    app.submit(id: card.id, action: "approve", comment: nil)
                } label: { Label(L("批准", "Approve"), systemImage: "checkmark.circle.fill") }
                    .tint(.green)
                    .disabled(card.tier == "T2" && !expanded)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)

                Button {
                    // v0.10.3: reject asks which kind (Zelin 拍板)。区分是功能性的：
                    // 回收站条目不参与 merge_or_new 匹配，同一需求会重新出卡；
                    // "已办完"(done_external→delivered) 才能把后续重述压成合并。
                    // 拒绝是低频操作，多一次点击可接受。「先不做」刻意不塞进这个
                    // 弹窗（标题问的是"不需要执行？"，语义相反），弹窗保持两选。
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
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)

                Button {
                    if let c = app.promptComment() {
                        app.submit(id: card.id, action: "comment", comment: c)
                    }
                } label: { Label(L("修改", "Comment"), systemImage: "bubble.left.fill") }
                    .tint(.blue)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)

                Button {
                    // v0.18 defer (暂缓/Later since v0.33): demote is NOT
                    // reject — the card goes back to the backlog
                    // (card_sent→detected) with summary/plan/sources intact
                    // and KEEPS matching in merge_or_new (restatements merge;
                    // radar act-now re-promotes), while trash is excluded from
                    // matching. One click, no confirmation: cheap + reversible
                    // — undo is the backlog lane's 研究并提议.
                    app.submit(id: card.id, action: "defer", comment: nil)
                } label: { Label(L("暂缓", "Later"), systemImage: "tray.and.arrow.down") }
                    .tint(.gray)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)

                Spacer(minLength: 8)

                // 展开详情 是 disclosure，不是决策：做成 plain 灰链接（覆盖继承的
                // .bordered），读作「展开」而非第五个竞争按钮。保留 ▸/▾ 提示与
                // T2 gate 所用的 expanded 状态。
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
                } label: {
                    Text(expanded ? L("收起 ▾", "Collapse ▾")
                                  : L("展开详情 ▸", "Details ▸"))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                .buttonStyle(.plain)
            }
        } content: {
            // v0.20 card-lifecycle 「回锅」marker: this proposal is a
            // previously-accepted thread re-raised because new info arrived.
            // Amber, at the very top so a tired expert reads "this came back"
            // before anything else; the new ask (reraisedNote) shows inline.
            if card.reraised { reraisedBadge }

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

            // T2 gate hint: approve unlocks only after expanding the details.
            if card.tier == "T2" && !expanded {
                Text(L("T2 需先展开看明细", "T2: expand details first"))
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(.orange)
            }

            // expanded detail blocks (sources + plan + long title) — rendered
            // at the tail of the content, right above the action rows. Driven by
            // this view's own `expanded` (the disclosure link below toggles it),
            // so no shared detail-slot toggle is needed.
            if expanded { expandedDetail }
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

    // MARK: 回锅 marker (v0.20 card-lifecycle re-raise)

    // amber pill 「↩︎ 回锅·Returned」+ plain-language subtext + the new ask.
    private static let reraisedTint = Color.orange
    @ViewBuilder private var reraisedBadge: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(L("↩︎ 回锅 · Returned", "↩︎ Returned"))
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(Self.reraisedTint)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(Self.reraisedTint.opacity(0.15))
                    .clipShape(Capsule())
                Text(L("你之前验收过这件事，来了新信息",
                       "You accepted this before — new info arrived"))
                    .font(.system(size: 10))
                    .foregroundColor(Self.reraisedTint)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let note = card.reraisedNote, !note.isEmpty {
                Text(L("新增：", "New: ") + note)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(Self.reraisedTint)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(7)
        .background(Self.reraisedTint.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 6))
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

/// Which lane a TaskRow renders in — passed explicitly by both call sites
/// (popover sections + kanban columns). Behavior (delivered buttons, input
/// badge) derives from this, never from the accent color (P2-2: the old
/// `accent == .green` discriminator was a correctness trap dressed as style).
enum TaskLane {
    case running, needsInput, completed
}

// NOTE: no .textSelection here on purpose — the whole card copies on click
// (CardSurface.copyText); textSelection would fight the tap gesture.
// Keyboard accessibility of the former Button wrapper is traded away —
// accepted and recorded in the implementation plan.
struct TaskRow: View {
    let task: RunningTask
    unowned let app: AppDelegate
    let lane: TaskLane

    // v0.21: 运行中卡上「停止」→ 2 选 confirmationDialog（退回提案/去待验收）。
    @State private var showStopDialog = false

    // accent is purely visual now, derived from the lane — no call site can
    // drift a color away from its semantics again.
    private var accent: Color {
        switch lane {
        case .running: return .blue
        case .needsInput: return .orange
        case .completed: return .green
        }
    }

    // kanban merges needs_input into the 运行中 column, where this badge is
    // the only lane signal; the popover's 需输入 section shows it too
    // (redundant under its header, but consistent across surfaces).
    private var showsInputBadge: Bool { lane == .needsInput }

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

    /// dispatch_error for queued rows, last_error otherwise — the row shows a
    /// one-line truncation; the detail block carries the full text (P2-4).
    private var errorText: String? {
        let e = isQueued ? task.dispatch_error : task.last_error
        guard let e, !e.isEmpty else { return nil }
        return e
    }

    private var hasDetailContent: Bool {
        (task.summary?.isEmpty == false)
            || !(task.plan ?? []).isEmpty
            || !(task.dod ?? []).isEmpty
            || (task.log?.isEmpty == false)
            || errorText != nil
    }

    private var isDelivered: Bool { lane == .completed }

    // v0.21 契约: 「停止」on EVERY non-delivered running-lane row (queued/
    // working/blocked/needs_input AND legacy review-active). One button →
    // 2-choice confirmationDialog: 退回提案 (abort_execution) or 去待验收
    // (stop_to_review). Replaces the old 停止并退回 + 已办完 pair; done_external
    // no longer lives on the running card (it stays on the proposal reject
    // dialog). Never on the completed lane.
    private var showsStop: Bool { !isDelivered }

    var body: some View {
        let hasButtons = showsStop || isDelivered
        // doubleClickRuns: cmd is app-generated (pipeline copy_cmd / Swift-built
        // "claude --resume <id>") — the TerminalLauncher security precondition.
        if hasDetailContent && hasButtons {
            CardSurface(copyText: cmd, doubleClickRuns: true, pending: isQueued,
                        actions: { actionButtons },
                        detail: { detailBlock }, content: { rowContent })
        } else if hasDetailContent {
            CardSurface(copyText: cmd, doubleClickRuns: true, pending: isQueued,
                        detail: { detailBlock }, content: { rowContent })
        } else if hasButtons {
            CardSurface(copyText: cmd, doubleClickRuns: true, pending: isQueued,
                        actions: { actionButtons }, content: { rowContent })
        } else {
            CardSurface(copyText: cmd, doubleClickRuns: true, pending: isQueued) { rowContent }
        }
    }

    // v0.10.2 action row — Buttons win over the whole-card copy tap (ReviewRow
    // 先例); CardSurface applies the unified font/bordered/small styling.
    @ViewBuilder private var actionButtons: some View {
        if showsStop {
            // v0.21: one 停止 opens a 2-choice dialog so the fork is explicit —
            // 退回提案 discards this run (abort_execution → back to 提案), 去待验收
            // keeps what the agent produced (stop_to_review → 待验收). Both just
            // stop the agent; the dialog subtitle spells out the difference.
            Button {
                showStopDialog = true
            } label: { Label(L("停止", "Stop"), systemImage: "stop.circle") }
                .tint(.orange)
                .confirmationDialog(
                    L("停止这个任务？", "Stop this task?"),
                    isPresented: $showStopDialog, titleVisibility: .visible
                ) {
                    Button(L("退回提案", "Discard & re-propose"), role: .destructive) {
                        app.submit(id: task.id, action: "abort_execution", comment: nil)
                    }
                    Button(L("去待验收", "Keep for review")) {
                        app.submit(id: task.id, action: "stop_to_review", comment: nil)
                    }
                    Button(L("取消", "Cancel"), role: .cancel) {}
                } message: {
                    Text(L("退回提案＝丢弃这次结果重来；去待验收＝留下它做的，我来检查",
                           "Discard & re-propose = throw away this run and start over; Keep for review = keep what it made and I'll check it"))
                }
        }
        if isDelivered {
            // delivered → back to REVIEW for re-acceptance
            Button {
                app.submit(id: task.id, action: "revert_review", comment: nil)
            } label: { Label(L("退回待验收", "Back to review"), systemImage: "arrow.uturn.backward") }
                .tint(.teal)
            // v0.20 card-lifecycle archive (永久完成/Done for good since
            // v0.33): seal this accepted thread. One tap, no confirm
            // (reversible via the 永久性完成 section's 放回看板). Sealed =
            // excluded from matching, so later mentions open a fresh card
            // instead of re-raising this one.
            Button {
                app.submit(id: task.id, action: "archive", comment: nil)
            } label: { Label(L("永久完成", "Done for good"), systemImage: "archivebox") }
                .tint(.gray)
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
                        // legacy review-active rows (older actd ≤0.17.1 only;
                        // §30 actd keeps attach-active cards in review[]) —
                        // same honest wording as the review-lane badge: no 打回
                        // verdict happened, so "reworking" was a misstatement.
                        if task.state == "review-active" {
                            Badge(text: L("会话有新活动", "Session active"), color: .teal)
                        } else if let st = task.state { Badge(text: st, color: accent) }
                        // §30 v0.28.1: a 待验收 card projected here because its
                        // session was reactivated (attach). Label it so a card
                        // Zelin remembers delivering doesn't read as brand-new
                        // work; it drops back to 待验收 when the session settles.
                        if task.from_review == true {
                            Badge(text: L("已交付过·再运行", "Delivered · re-running"), color: .teal)
                        }
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
                    Text(L("单击复制 · 双击在终端运行：", "Click to copy · double-click runs: ") + cmd)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                // §25: classified errors show the plain-language sentence +
                // one right button; the raw text drops to the tooltip/detail.
                if isQueued, let de = task.dispatch_error, !de.isEmpty {
                    errorLine(prefix: L("派发失败：", "Dispatch failed: "),
                              raw: de, failureID: task.dispatch_error_id)
                }
                if !isQueued, let le = task.last_error, !le.isEmpty {
                    errorLine(prefix: L("错误：", "Error: "),
                              raw: le, failureID: task.last_error_id)
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

    /// §25 error line: classified id → plain sentence + the one right button
    /// (+ 让 AI 修 fallback); unclassified → raw text + 让 AI 修. The raw text
    /// always survives in the tooltip and the expanded detail block.
    @ViewBuilder private func errorLine(prefix: String, raw: String,
                                        failureID: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(FailureCatalog.message(failureID).map { prefix + $0 } ?? (prefix + raw))
                .font(.system(size: 10))
                .foregroundColor(.red)
                .lineLimit(2)
                .truncationMode(.tail)
                .help(raw)
            HStack(spacing: 6) {
                if let label = FailureCatalog.actionLabel(failureID) {
                    Button(label) { FailureCatalog.perform(failureID) }
                }
                if AIFix.enabled {
                    Button(L("让 AI 修", "Fix with AI")) {
                        AIFix.launch(context: prefix + raw) { _, _ in }
                    }
                }
                Spacer()
            }
            .font(.system(size: 10))
            .buttonStyle(.bordered)
            .controlSize(.mini)
        }
    }

    @ViewBuilder private var detailBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let err = errorText { ErrorTextBlock(text: err) }
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
        // doubleClickRuns: copy_cmd is app-generated by the pipeline — the
        // TerminalLauncher security precondition.
        if hasDetailContent {
            CardSurface(accent: .teal, copyText: item.copy_cmd, doubleClickRuns: true,
                        actions: { actionButtons },
                        detail: { detailBlock },
                        content: { rowContent })
        } else {
            CardSurface(accent: .teal, copyText: item.copy_cmd, doubleClickRuns: true,
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
            // §30: the session is live-working — user attach / organic
            // activity, calmly noted; a real 打回 leaves this lane entirely
            // (review->executing), so this is never a rework round.
            if item.session_active {
                Badge(text: L("会话有新活动", "Session active"), color: .teal)
            }
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
            Text(L("单击复制 · 双击在终端运行：", "Click to copy · double-click runs: ") + cmd)
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
        surface.contextMenu { contextItems }
    }

    // detail slot only when there are source quotes to show — otherwise the
    // toggle would open an empty drawer.
    @ViewBuilder private var surface: some View {
        if let srcs = item.sources, !srcs.isEmpty {
            CardSurface(actions: { actionButtons },
                        detail: { SourceListView(sources: srcs)
                                      .frame(maxWidth: .infinity, alignment: .leading) },
                        content: { rowContent })
        } else {
            CardSurface(actions: { actionButtons }, content: { rowContent })
        }
    }

    // v0.20 card-lifecycle: 永久完成 (archive) lives in the context menu,
    // deliberately distinct from the primary 删除 button — it SEALS the item
    // (keeps it as a record, excluded from matching so it never re-suggests),
    // whereas delete drops it into trash. Kept off the main button row so the
    // two one-click actions (研究并提议 / 删除) stay uncluttered.
    @ViewBuilder private var contextItems: some View {
        Button {
            app.submit(id: item.id, action: "archive", comment: nil)
        } label: { Label(L("永久完成（封存，不再提示）", "Done for good (seal, stop suggesting)"),
                         systemImage: "archivebox") }
    }

    @ViewBuilder private var actionButtons: some View {
        Button {
            // the inbox write must succeed BEFORE any optimistic UI (wave-2
            // contract): a failed submit already alerts "卡片保持原样" — it
            // must not also leave a ghost 研究中 placeholder in the proposal
            // lane, so beginRaising only runs after submit returns true.
            if app.submit(id: item.id, action: "raise", comment: nil) {
                app.store.beginRaising(item.id, summary: item.displaySummary)
            }
        // "propose" 小写：与 iOS 按钮及 shared/Lanes.swift backlog 说明逐字一致
        } label: { Label(L("研究并提议", "Research & propose"), systemImage: "magnifyingglass") }
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

// MARK: - MergeSuggestionCard — 契约 merge-review §七（analyzing / done / failed 三态）
//
// 紫色 accent 建议卡，宿主（kanban 待审批列顶 / popover 镜像）负责摆放；本视图
// 只渲染 + 把 接受/取消 写进 inbox（merge_apply / merge_dismiss，经 app.submit
// → card_action analytics 自动覆盖，契约 §八）。
// actionPending: 宿主传 store 的乐观态（接受/取消已提交、等 actd 下一版
// dashboard 把建议卡拿掉；180 s 兜底在 Store）→ 内容灰显、按钮换成 spinner 行。

struct MergeSuggestionCard: View {
    let suggestion: MergeSuggestion
    unowned let app: AppDelegate
    var actionPending: Bool = false
    // 契约 §21bis: 「仍然合并」覆盖弹窗（当 AI 判 verdict≠merge 或分析失败时，
    // 用户仍要合）。走同一个 ForceMergeSheet（选主卡 + 不可撤销告知）。
    @State private var showForceMerge = false

    var body: some View {
        content
            // 强制合并确认弹窗（选主卡）——成功后顺手把这条建议 dismiss 掉，
            // 因为它已被用户的直断取代，不该继续挂在看板上。
            .sheet(isPresented: $showForceMerge) {
                ForceMergeSheet(ids: suggestion.ids, app: app,
                                defaultPrimary: suggestion.primary) { primary in
                    if app.submitMergeForce(ids: suggestion.ids, primary: primary) {
                        app.submit(id: suggestion.id, action: "merge_dismiss", comment: nil)
                    }
                }
            }
    }

    @ViewBuilder private var content: some View {
        switch suggestion.status {
        case "done": doneBody
        case "failed": failedBody
        default: analyzingBody   // "analyzing"（未知状态也按分析中兜底渲染）
        }
    }

    // MARK: analyzing — 灰卡 spinner（契约 §七）

    private var analyzingBody: some View {
        CardSurface(bgOpacity: 0.04, padding: 10, cornerRadius: 8, pending: true) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L("合并分析中…", "Analyzing merge…"))
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.secondary)
                    Text(involvedLine)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let age = RelativeTime.sinceEpoch(suggestion.requested_at) {
                        Text(L("发起于 ", "requested ") + age)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
                Spacer()
            }
        }
    }

    // MARK: done — 结论 + 主/副卡 + rationale + 动作清单全文 + confidence + 按钮

    private var doneBody: some View {
        CardSurface(accent: .purple, padding: 10, cornerRadius: 8,
                    pending: actionPending, actions: { doneButtons }) {
            headline

            // 主卡/副卡名（keep_separate 等无 primary 时列出全部涉及卡）
            VStack(alignment: .leading, spacing: 1) {
                if let p = suggestion.primary, !p.isEmpty {
                    Text(L("主卡：", "Primary: ") + nameLine(p))
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                    ForEach(suggestion.ids.filter { $0 != p }, id: \.self) { sid in
                        Text(L("副卡：", "Secondary: ") + nameLine(sid))
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    ForEach(suggestion.ids, id: \.self) { sid in
                        Text("• " + nameLine(sid))
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            if let r = suggestion.rationale, !r.isEmpty {
                Text(r)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // 「接受后将执行」— 契约 §七 要求全文展示（执行是确定性的，
            // 这份清单是 AI 对确定性语义的解释，Zelin 拍板前必须能读全）。
            if !suggestion.action_plan.isEmpty {
                VStack(alignment: .leading, spacing: 1) {
                    Text(L("接受后将执行：", "On accept, this will:"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.secondary)
                    ForEach(Array(suggestion.action_plan.enumerated()), id: \.offset) { i, step in
                        Text("\(i + 1). \(step)")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    @ViewBuilder private var doneButtons: some View {
        if actionPending {
            submittedLine
        } else {
            Button {
                app.submit(id: suggestion.id, action: "merge_apply", comment: nil)
            } label: { Label(L("接受", "Accept"), systemImage: "checkmark.circle.fill") }
                .tint(.green)

            // 契约 §21bis: AI 没判「合并」（保持独立 / 挂改进卡 / 关副卡）时，
            // 给不认同的用户一个直断入口——钦定主卡强制合并（走确认弹窗）。
            if suggestion.verdict != "merge" {
                Button {
                    showForceMerge = true
                } label: { Label(L("仍然合并", "Merge anyway"), systemImage: "arrow.triangle.merge") }
                    .tint(.purple)
            }

            Button {
                app.submit(id: suggestion.id, action: "merge_dismiss", comment: nil)
            } label: { Label(L("取消", "Dismiss"), systemImage: "xmark.circle") }
                .tint(.gray)
        }
        Spacer()
    }

    // MARK: failed — 橙色 + error 全文 + 仅「取消」

    private var failedBody: some View {
        CardSurface(accent: .orange, padding: 10, cornerRadius: 8,
                    pending: actionPending, actions: { failedButtons }) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 12))
                    .foregroundColor(.orange)
                Text(L("合并分析失败", "Merge analysis failed"))
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.primary)
                Spacer(minLength: 4)
            }
            Text(involvedLine)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let err = suggestion.error, !err.isEmpty {
                ErrorTextBlock(text: err)
            }
        }
    }

    @ViewBuilder private var failedButtons: some View {
        if actionPending {
            submittedLine
        } else {
            // 契约 §21bis: 分析失败也别把用户卡死——仍可钦定主卡直接合并。
            Button {
                showForceMerge = true
            } label: { Label(L("仍然合并", "Merge anyway"), systemImage: "arrow.triangle.merge") }
                .tint(.purple)

            Button {
                app.submit(id: suggestion.id, action: "merge_dismiss", comment: nil)
            } label: { Label(L("取消", "Dismiss"), systemImage: "xmark.circle") }
                .tint(.gray)
        }
        Spacer()
    }

    // MARK: shared bits

    /// 🔀 + verdict 一句话结论 + confidence 徽章（done 态首行）。
    private var headline: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "arrow.triangle.merge")
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.purple)
                .padding(.top, 1)
            Text(verdictHeadline)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(.primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            if let conf = suggestion.confidence, !conf.isEmpty {
                confidenceBadge(conf)
            }
        }
    }

    /// verdict 本地化 —— 契约 §三 的四枚举；未知值原样透出（不吞信息）。
    private var verdictHeadline: String {
        switch suggestion.verdict {
        case "merge":
            return L("建议合并：副卡并入主卡", "Suggest merging the secondary into the primary")
        case "link_improvement":
            return L("建议挂为主卡的改进卡", "Suggest linking as an improvement of the primary")
        case "keep_separate":
            return L("建议保持独立，不合并", "Suggest keeping them separate")
        case "close_secondary":
            return L("建议关闭副卡（进回收站）", "Suggest closing the secondary (to trash)")
        default:
            return suggestion.verdict ?? L("分析完成", "Analysis complete")
        }
    }

    @ViewBuilder private func confidenceBadge(_ conf: String) -> some View {
        switch conf {
        case "high":   Badge(text: L("置信度：高", "Confidence: high"), color: .green)
        case "medium": Badge(text: L("置信度：中", "Confidence: medium"), color: .orange)
        case "low":    Badge(text: L("置信度：低", "Confidence: low"), color: .gray)
        default:       Badge(text: conf, color: .gray)
        }
    }

    /// 提交后的乐观态行（占按钮位；内容灰显由 CardSurface.pending 负责）。
    private var submittedLine: some View {
        HStack(spacing: 6) {
            ProgressView().controlSize(.small).scaleEffect(0.7)
            Text(L("已提交…", "Submitted…"))
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(.secondary)
        }
    }

    /// "R-xxx · 标题"；卡已不在 dashboard（如已并走）时只剩 id。标题解析走
    /// store.cardTitle —— 全 lane 覆盖（含 潜在任务/debt：v0.21 起多选合并
    /// 覆盖全部看板列），与 ForceMergeSheet 同源，两个 surface 永远一致。
    private func nameLine(_ id: String) -> String {
        let t = app.store.cardTitle(id)
        return t == id ? id : "\(id) · \(t)"
    }

    private var involvedLine: String {
        suggestion.ids.map { nameLine($0) }.joined(separator: "  +  ")
    }
}

// MARK: - ForceMergeSheet — 契约 §21bis 强制合并确认弹窗
//
// 两个入口共用（kanban 多选操作条「强制合并」/ AI 建议卡「仍然合并」覆盖）：
// 选一张卡当主卡（默认第一张 / AI 选的 primary），读不可撤销告知，确认 →
// onConfirm(primary)。宿主把 onConfirm 接到 AppDelegate.submitMergeForce（外加
// 各自的收尾：多选退出 / 建议卡顺手 dismiss）。因 merged 是终态、UI 不可撤销，
// 这个弹窗是唯一的确认关口——不做「一点就合」。
struct ForceMergeSheet: View {
    let ids: [String]
    unowned let app: AppDelegate
    let onConfirm: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var primary: String

    init(ids: [String], app: AppDelegate, defaultPrimary: String? = nil,
         onConfirm: @escaping (String) -> Void) {
        self.ids = ids
        self.app = app
        self.onConfirm = onConfirm
        let d = defaultPrimary.flatMap { ids.contains($0) ? $0 : nil } ?? ids.first ?? ""
        _primary = State(initialValue: d)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Image(systemName: "arrow.triangle.merge").foregroundColor(.purple)
                Text(L("强制合并 \(ids.count) 张卡片", "Force-merge \(ids.count) cards"))
                    .font(.headline)
            }
            Text(L("选一张作为主卡保留，其余全部并入它。",
                   "Pick one card to keep as the primary; the rest fold into it."))
                .font(.subheadline).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 4) {
                ForEach(ids, id: \.self) { id in
                    Button { primary = id } label: {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: primary == id
                                  ? "largecircle.fill.circle" : "circle")
                                .foregroundColor(primary == id ? .purple : .secondary)
                                .padding(.top, 1)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(app.store.cardTitle(id))
                                    .font(.system(size: 12,
                                                  weight: primary == id ? .semibold : .regular))
                                    .foregroundColor(.primary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text(primary == id
                                     ? L("主卡 · 保留", "Primary · kept")
                                     : L("副卡 · 并入主卡", "Secondary · folds in"))
                                    .font(.system(size: 10))
                                    .foregroundColor(primary == id ? .purple : .secondary)
                            }
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(10)
            .background(Color.secondary.opacity(0.06),
                        in: RoundedRectangle(cornerRadius: 8))

            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 11)).foregroundColor(.orange).padding(.top, 1)
                Text(L("副卡会停止运行、进入「已合并」——这一步不可撤销。它们的来源与交付物会保留在主卡上。",
                       "Secondaries stop running and become \u{201C}merged\u{201D} — this cannot be undone. Their sources and deliverables are kept on the primary."))
                    .font(.system(size: 11)).foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack {
                Spacer()
                Button(L("取消", "Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button {
                    onConfirm(primary)
                    dismiss()
                } label: {
                    Text(L("强制合并", "Force-merge"))
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent).tint(.purple)
                .disabled(primary.isEmpty || ids.count < 2)
            }
        }
        .padding(18)
        .frame(width: 400)
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

// v0.20 card-lifecycle §5: 永久性完成 (archive) browse view — mirrors
// TrashSectionView (collapsible, collapsed by default in the popover; search
// box; per-row 「放回看板」→ unarchive). Archived cards are sealed & off-board
// (like trash), so this is a calm browse+restore surface, never a work queue.
struct ArchiveSectionView: View {
    let items: [ArchivedItem]
    let count: Int
    unowned let app: AppDelegate
    // main-window page opens expanded; popover keeps collapsed-by-default.
    var startExpanded: Bool = false
    @State private var expanded = false
    @State private var query = ""

    /// Shared section help — also the kanban 永久性完成 strip's header help
    /// (v0.33), so both surfaces describe the archive with one voice.
    static var helpCopy: String {
        L("彻底结束、封存的线程（你点的永久完成 + 自动封存的冷交付）。封存=不再参与匹配，后续相关信息会开新卡而不是回锅这张。可随时「放回看板」回到原状态列。",
          "Threads that are truly over — ones you marked done for good, plus auto-sealed cold deliveries. Sealed = excluded from matching, so later mentions open a fresh card instead of re-raising this one. Press \"Put back\" any time to return one to its previous lane.")
    }

    private var filtered: [ArchivedItem] {
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
                        .padding(.top, 4)
                    SectionHeader(title: L("🗄 永久性完成 · done for good", "🗄 Done for good"),
                                  count: count, help: Self.helpCopy)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                TextField(L("搜索标题 / summary…", "Search title / summary…"), text: $query)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11))

                if filtered.isEmpty {
                    EmptyRow(text: items.isEmpty ? L("还没有永久完成的卡", "Nothing here yet")
                                                 : L("无匹配项", "No matches"))
                } else {
                    ForEach(filtered, id: \.id) { it in
                        ArchiveRow(item: it, app: app)
                    }
                }
            }
        }
        .onAppear { if startExpanded { expanded = true } }
    }
}

struct ArchiveRow: View {
    let item: ArchivedItem
    unowned let app: AppDelegate

    // "user" → 你封存 (green); "auto" → 自动封存 (gray). Anything else omits.
    private var reasonBadge: (text: String, color: Color)? {
        switch item.archive_reason {
        case "user": return (L("你封存", "You sealed"), .green)
        case "auto": return (L("自动封存", "Auto-sealed"), .gray)
        default: return nil
        }
    }

    var body: some View {
        CardSurface {
            Button {
                app.submit(id: item.id, action: "unarchive", comment: nil)
            } label: { Label(L("放回看板", "Put back"), systemImage: "arrow.uturn.left") }
                .tint(.green)

            Spacer()
        } content: {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "archivebox")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .padding(.top, 2)
                Text(item.displaySummary)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                Spacer(minLength: 4)
                if let rb = reasonBadge {
                    Badge(text: rb.text, color: rb.color)
                }
            }

            // tag line: kind · previous status · relative age
            HStack(spacing: 6) {
                if let k = item.kind, !k.isEmpty { Badge(text: k, color: .gray) }
                if let ps = item.prev_status, !ps.isEmpty {
                    Text(L("原状态: ", "was: ") + ps)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                if let age = RelativeTime.since(item.archived_at) {
                    Text(age)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
    }
}
