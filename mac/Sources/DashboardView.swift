// DashboardView.swift — popover 根视图 DashboardView + RecordingMenuButton（录制控制按钮）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// MARK: - SwiftUI views

/// Recording control in the popover header (replaces the former separate
/// status-bar item). Dot red while the engine records; menu = mode picker
/// plus a permission escape hatch when TCC blocks the engine.
struct RecordingMenuButton: View {
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var i18n = LanguageStore.shared
    // 契约D feedback: transient 重启中… next to the button text after a mode
    // switch / engine restart; the 5-s refresh loop (refreshEngineState) then
    // takes over via statusLabel/statusColor. Token: an older timer must not
    // clear a newer flash (same pattern as Cards.swift copied-feedback).
    @State private var restarting = false
    @State private var restartToken = 0

    var body: some View {
        Menu {
            if rec.mode != "off" && !rec.engineRunning {
                Text(L("未在录制 — 多半缺「屏幕录制」权限",
                       "Not recording — likely missing Screen Recording permission"))
            } else {
                Text(L("录制：", "Recording: ") + stateWord)
            }
            Divider()
            ForEach(modes, id: \.0) { m, label in
                Button {
                    rec.setMode(m)
                    // "off" just stops — no engine spin-up to wait on
                    if m != "off" { flashRestarting() }
                } label: {
                    if rec.mode == m {
                        Label(label, systemImage: "checkmark")
                    } else {
                        Text(label)
                    }
                }
            }
            Divider()
            // 契约D: explicit engine restart — same semantics as re-picking
            // the current mode (restartEngine logs "recording_restart" itself).
            Button(L("重启录制引擎", "Restart recording engine")) {
                rec.restartEngine()
                flashRestarting()
            }
            .disabled(rec.mode == "off")
            if !RecordingController.hasScreenPermission() {
                Divider()
                Button(L("打开系统设置 → 屏幕录制",
                         "Open System Settings → Screen Recording")) {
                    RecordingController.openScreenRecordingSettings()
                }
            }
        } label: {
            // icon + text (Zelin: icon alone is not readable at a glance)
            HStack(spacing: 4) {
                Image(systemName: symbol)
                Text(statusLabel)
                if restarting {
                    Text(L("重启中…", "restarting…"))
                        .foregroundColor(.orange)
                }
            }
            .font(.system(size: 12))
            .foregroundColor(statusColor)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(L("录制控制", "Recording controls"))
    }

    // Show 重启中… for a few seconds, then let the normal state refresh speak.
    private func flashRestarting() {
        restartToken += 1
        let token = restartToken
        restarting = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            if restartToken == token { restarting = false }
        }
    }

    // 契约4 recording terms — popover button carries the 录制：/Rec: prefix;
    // in-page status lines (Pages side, 桶C) use the bare words.
    private var statusLabel: String {
        L("录制：", "Rec: ") + stateWord
    }

    private var stateWord: String {
        if rec.mode == "off" { return L("关", "Off") }
        if !rec.engineRunning { return L("未在录制", "Not recording") }
        return rec.mode == "screen_audio" ? L("屏幕+音频", "Screen + audio")
                                          : L("仅屏幕", "Screen only")
    }

    private var statusColor: Color {
        if rec.mode == "off" { return .secondary }
        return rec.engineRunning ? .red : .orange
    }

    private var modes: [(String, String)] {
        [("off", L("关", "Off")),
         ("screen", L("仅屏幕", "Screen only")),
         ("screen_audio", L("屏幕+音频", "Screen + audio"))]
    }

    private var symbol: String {
        switch rec.mode {
        case "off": return "record.circle"
        case "screen_audio": return "waveform.circle.fill"
        default: return "record.circle.fill"
        }
    }
}

struct DashboardView: View {
    @ObservedObject var store: DashboardStore
    // observe the UI language so the whole popover re-renders on switch
    @ObservedObject private var i18n = LanguageStore.shared
    unowned let app: AppDelegate
    // item 6: the draft lives in a shared model (not @State) so the Esc key
    // monitor can observe / clear it; binding change only, layout untouched.
    @ObservedObject private var draft = CaptureDraft.popover
    // Spotlight/Raycast convention: popover opens → caret already in the
    // capture field, type immediately. (Popover only — KanbanView untouched.)
    @FocusState private var captureFocused: Bool
    // item 3: 未识别 slash-command error (kept until the text is edited)
    @State private var slashError: String?
    // item 5: index into CaptureHistory.items while browsing with ↑/↓
    @State private var historyIndex: Int?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Zelin's AI Assistant")
                        .font(.system(size: 15, weight: .semibold))
                    Spacer()
                    // §15: recording control lives here (no separate status item)
                    RecordingMenuButton()
                }

                // quick capture → state/inbox/capture-*.json (contract #4)
                HStack(alignment: .bottom, spacing: 6) {
                    // grows with long input, capped at 6 lines (Enter submits,
                    // Shift+Enter inserts a newline — item 1 monitor)
                    TextField(L("一句话，AI 来研究并提案…", "One sentence — AI researches and proposes…"),
                              text: $draft.text, axis: .vertical)
                        .lineLimit(1...6)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                        .focused($captureFocused)
                        .onSubmit { submitCapture() }
                        // item 5: ↑/↓ recall submitted history
                        .onKeyPress(.upArrow) { historyKey(up: true) }
                        .onKeyPress(.downArrow) { historyKey(up: false) }
                        .onChange(of: draft.text) { _, _ in slashError = nil }
                    Button {
                        submitCapture()
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(
                                draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                    ? .secondary : .accentColor)
                    }
                    .buttonStyle(.plain)
                    .disabled(draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                // item 3: slash-command hint / error — appended line only,
                // existing layout structure untouched.
                if let err = slashError {
                    Text(err)
                        .font(.system(size: 10))
                        .foregroundColor(.orange)
                } else if draft.text.hasPrefix("/") {
                    Text(SlashCommands.hintLine)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }

                if let err = store.loadError {
                    Text(err)
                        .font(.system(size: 11))
                        .foregroundColor(.orange)
                }

                // P1-4: slow-vs-broken pipeline banner (shared with the kanban)
                PipelineHealthBanner(store: store, app: app)

                // placeholder-timeout notices (capture = yellow, raise = orange)
                ForEach(store.notices) { NoticeRow(notice: $0) }

                if store.dashboard == nil {
                    // quick-capture spinner cards render even before the first
                    // dashboard.json exists — the submit must never feel lost
                    ForEach(store.visibleApprovals, id: \.id) { card in
                        ApprovalCardView(card: card, app: app)
                    }
                    emptyState
                } else {
                    content
                }

                Divider()
                footer
            }
            .padding(14)
            .frame(width: 400, alignment: .leading)
        }
        .frame(width: 400)
        // each popover show re-adds the hosted view → onAppear refocuses;
        // defaultFocus covers the no-first-responder case on macOS 14+
        .defaultFocus($captureFocused, true)
        .onAppear { captureFocused = true }
        // item 7b: ⌘L (View menu) re-focuses the capture field
        .onReceive(NotificationCenter.default.publisher(for: .focusCaptureField)) { _ in
            captureFocused = true
        }
    }

    private func submitCapture() {
        let text = draft.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if app.submitCapture(text, source: "popover") {   // 契约F capture_submit
            draft.text = ""
            slashError = nil
            historyIndex = nil
        } else if SlashCommands.isCommand(text) {
            // slash command failed: IO error (lastErrorLine) vs typed wrong
            slashError = SlashCommands.lastErrorLine
                ?? (L("未识别或参数错误：", "Unrecognized or bad argument: ") + text)
        } else {
            // capture inbox write failed (wave 2: submitCapture returns false)
            slashError = L("提交失败，已保留输入", "Submit failed — input kept")
        }
    }

    // item 5: ↑/↓ history recall — only when the field is empty or shows an
    // untouched history item (otherwise arrows keep moving the caret). IME
    // red line: while the pinyin candidate window is up (hasMarkedText),
    // ↑/↓ pick candidates — never intercept.
    private func historyKey(up: Bool) -> KeyPress.Result {
        if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
           tv.hasMarkedText() { return .ignored }
        let hist = CaptureHistory.items
        guard !hist.isEmpty else { return .ignored }
        let browsing = historyIndex.map {
            hist.indices.contains($0) && draft.text == hist[$0]
        } ?? false
        guard draft.text.isEmpty || browsing else { return .ignored }
        var idx = (browsing ? historyIndex : nil) ?? -1
        idx += up ? 1 : -1
        if idx < 0 {                       // stepped past the newest → empty
            historyIndex = nil
            draft.text = ""
            return .handled
        }
        guard hist.indices.contains(idx) else { return .handled }  // oldest: stay
        historyIndex = idx
        draft.text = hist[idx]
        return .handled
    }

    // P1-5: shared first-launch empty state (Freshness.swift) — same copy as
    // the kanban, with a start command + a path into the dependency check.
    private var emptyState: some View {
        PipelineEmptyStateView(app: app)
            .padding(.vertical, 24)
    }

    // All section counts follow the RENDERED arrays (visible* + echoes) — the
    // old counts.* badges could disagree with an emptied list ("徽章>0 但空列").
    @ViewBuilder private var content: some View {
        let approvals = store.visibleApprovals
        let runningEchoes = store.echoes(for: .running)
        let completedEchoes = store.echoes(for: .completed)
        let debtEchoes = store.echoes(for: .debt)
        let reviews = store.visibleReview
        let debt = store.visibleDebt
        // v0.10.3 契约一: running/needs-input/completed now flow through the
        // sorted+hidden-filtered store projections too (same as the kanban).
        let running = store.visibleRunning
        let needsInput = store.visibleNeedsInput
        let completed = store.visibleCompleted

        // merge-review 契约七: the popover MIRRORS suggestion cards (accept /
        // dismiss work; no multi-select here). Same slot as the kanban: after
        // the grey processing prefix (captures + raise placeholders), before
        // the real approval cards.
        let suggestions = store.visibleMergeSuggestions

        // W8: lane display name 提案/Proposals — internal keys unchanged.
        if approvals.isEmpty && suggestions.isEmpty {
            CompactEmptySection(title: L("提案 · proposals", "Proposals"),
                                emptyText: L("暂无提案", "No proposals yet"))
        } else {
            SectionHeader(title: L("提案 · proposals", "Proposals"),
                          count: approvals.count + suggestions.count)
            let placeholderPrefix = approvals.prefix(while: { $0.processing })
            ForEach(Array(placeholderPrefix), id: \.id) { card in
                ApprovalCardView(card: card, app: app,
                                 commentPending: store.pendingComment[card.id] != nil)
            }
            ForEach(suggestions, id: \.id) { s in
                // dismiss-pending 已被 visibleMergeSuggestions 过滤（即时消失）；
                // apply-pending 灰显（契约七）。
                MergeSuggestionCard(suggestion: s, app: app,
                                    actionPending: store.mergeApplyPending(s.id))
            }
            ForEach(Array(approvals.dropFirst(placeholderPrefix.count)),
                    id: \.id) { card in
                ApprovalCardView(card: card, app: app,
                                 commentPending: store.pendingComment[card.id] != nil)
            }
        }

        if runningEchoes.isEmpty && running.isEmpty {
            CompactEmptySection(title: L("运行中 · running", "Running"),
                                emptyText: L("无运行中任务", "No running tasks"))
        } else {
            SectionHeader(title: L("运行中 · running", "Running"),
                          count: running.count + runningEchoes.count)
            ForEach(runningEchoes) { PendingEchoRow(echo: $0) }
            ForEach(running, id: \.id) { t in
                TaskRow(task: t, app: app, lane: .running)
            }
        }

        if needsInput.isEmpty {
            CompactEmptySection(title: L("需输入 · needs input", "Needs Input"),
                                emptyText: L("无需输入任务", "No tasks need input"))
        } else {
            SectionHeader(title: L("需输入 · needs input", "Needs Input"),
                          count: needsInput.count)
            ForEach(needsInput, id: \.id) { t in
                TaskRow(task: t, app: app, lane: .needsInput)
            }
        }

        if reviews.isEmpty {
            CompactEmptySection(title: L("待验收 · review", "Review"),
                                emptyText: L("无待验收草稿", "No drafts to review"))
        } else {
            SectionHeader(title: L("待验收 · review", "Review"), count: reviews.count)
            ForEach(reviews, id: \.id) { r in
                ReviewRow(item: r, app: app)
            }
        }

        if completedEchoes.isEmpty && completed.isEmpty {
            CompactEmptySection(title: L("已验收 · delivered", "Delivered"),
                                emptyText: L("无已验收任务", "No delivered tasks"))
        } else {
            SectionHeader(title: L("已验收 · delivered", "Delivered"),
                          count: completed.count + completedEchoes.count)
            ForEach(completedEchoes) { PendingEchoRow(echo: $0) }
            // keep the popover shallow: first 5 delivered, main window has all
            ForEach(completed.prefix(5), id: \.id) { t in
                TaskRow(task: t, app: app, lane: .completed)
            }
            if completed.count > 5 {
                Button {
                    app.openMainWindow(nil)
                } label: {
                    Text(L("共 \(completed.count) 条，去主窗口看",
                           "\(completed.count) total — see the main window"))
                        .font(.system(size: 11))
                        .foregroundColor(.accentColor)
                }
                .buttonStyle(.plain)
            }
        }

        // 备选/Backlog: display rename of the debt lane (dashboard key `debt`
        // and the store projection names stay — 纯展示层).
        if debtEchoes.isEmpty && debt.isEmpty {
            CompactEmptySection(title: L("备选 · backlog", "Backlog"),
                                emptyText: L("暂无备选", "No backlog items"))
        } else {
            SectionHeader(title: L("备选 · backlog", "Backlog"),
                          count: debt.count + debtEchoes.count)
            ForEach(debtEchoes) { PendingEchoRow(echo: $0) }
            ForEach(debt, id: \.id) { d in
                DebtRow(item: d, app: app)
            }
        }

        TrashSectionView(items: store.visibleTrash, count: store.visibleTrashCount,
                         pinnedLocal: store.pinnedLocal, app: app)
    }

    // footer freshness: generated_at age (orange past 90 s = actd likely down);
    // TimelineView keeps the relative label live even when the store publishes
    // nothing (reload short-circuits while the file is unchanged).
    private var footer: some View {
        HStack {
            TimelineView(.periodic(from: .now, by: 15)) { context in
                freshnessLabel(now: context.date)
            }
            Spacer()
            Button(L("主窗口", "Main Window")) { app.openMainWindow(nil) }
                .font(.system(size: 11))
            Button(L("退出", "Quit")) { NSApp.terminate(nil) }
                .font(.system(size: 11))
        }
    }

    @ViewBuilder private func freshnessLabel(now: Date) -> some View {
        if let gen = store.dashboard?.generated_at, let d = Self.parseISO(gen) {
            let age = now.timeIntervalSince(d)
            if age > 90 {
                Text(L("数据生成于 \(max(1, Int(age / 60))) 分钟前，actd 可能未运行",
                       "Data generated \(max(1, Int(age / 60))) min ago — actd may be down"))
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
            } else {
                Text(L("数据生成于 ", "Data generated ")
                    + (RelativeTime.since(gen) ?? Self.timeFmt.string(from: d)))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
        } else if let t = store.lastRefresh {
            // generated_at missing → silent degrade to the old refresh stamp
            Text(L("刷新于 ", "Refreshed at ") + Self.timeFmt.string(from: t))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
    }

    private static func parseISO(_ s: String) -> Date? {
        Self.iso.date(from: s) ?? Self.isoFrac.date(from: s)
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let timeFmt: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()
}

// count=0 section: header + empty line collapse into ONE dimmed row
private struct CompactEmptySection: View {
    let title: String
    let emptyText: String
    var body: some View {
        HStack(spacing: 6) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(.secondary.opacity(0.55))
            Text("— " + emptyText)
                .font(.system(size: 10))
                .foregroundColor(.secondary.opacity(0.5))
            Spacer()
        }
        .padding(.top, 4)
    }
}
