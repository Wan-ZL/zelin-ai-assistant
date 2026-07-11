// Composer.swift — KanbanComposer（待审批列顶的常驻折叠捕获行）
//
// 折叠态：一行「＋ 一句话，AI 来研究并提案…」；点击或 ⌘L 路径发出的
// .focusCaptureField 通知就地展开为多行输入区（自动增高，上限约 5 行）。
// Return 发送 / Shift+Return 换行 —— IME-safe：拼音组合期间 Return 在输入法内
// commit 候选、到不了 onSubmit；Shift+Return 由 AppDelegate 的 app-lifetime
// shiftReturnMonitor 对 field editor 注入换行（与 prompt panel 的
// PromptSendDelegate 同一条红线，参照实现、不改它）。
// Esc 退出输入但从不丢草稿：有草稿 → 只交还光标（保持展开，草稿可见）；
// 空 → 折叠回一行。点击输入框外任意处同样只 defocus（AppDelegate 的
// clickDefocusMonitor）。非空永不折叠 —— 折叠会把未发送的草稿藏起来。
// 斜杠命令提示/报错 + ↑/↓ 历史逻辑从 KanbanView 旧 header 输入框整体搬入
// （Kanban.swift 里已删除原件）。

import AppKit
import SwiftUI
import Foundation

struct KanbanComposer: View {
    unowned let app: AppDelegate
    // observe the UI language so placeholder/hints re-render on switch
    @ObservedObject private var i18n = LanguageStore.shared
    @State private var expanded = false
    @State private var text = ""
    // item 3 (moved from KanbanView): 未识别 slash-command error
    @State private var slashError: String?
    // item 5 (moved from KanbanView): index into CaptureHistory.items
    @State private var historyIndex: Int?
    @FocusState private var focused: Bool

    private var placeholder: String {
        L("一句话，AI 来研究并提案…", "One sentence — AI researches and proposes…")
    }

    var body: some View {
        Group {
            if expanded {
                editor
            } else {
                collapsedRow
            }
        }
        // ⌘L route: AppDelegate posts .focusCaptureField (popover open →
        // the popover field takes it; otherwise main window + this composer).
        // The notification is global: when the popover is open its field owns
        // the caret — this composer must NOT also expand invisibly, steal
        // focus, or log a spurious composer_open.
        .onReceive(NotificationCenter.default.publisher(for: .focusCaptureField)) { _ in
            guard !app.popoverIsShown else { return }
            expand(via: "hotkey")
        }
    }

    // collapsed: resident one-line「＋ …」row; click expands in place
    private var collapsedRow: some View {
        Button {
            expand(via: "click")
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "plus")
                    .font(.system(size: 11, weight: .medium))
                Text(placeholder)
                    .font(.system(size: 12))
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .foregroundColor(.secondary)
            .padding(.vertical, 7)
            .padding(.horizontal, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.04))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(L("快速捕获（⌘L）", "Quick capture (⌘L)"))
    }

    // expanded: auto-growing multi-line input (~5 lines max) + send arrow
    private var editor: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .bottom, spacing: 6) {
                TextField(placeholder, text: $text, axis: .vertical)
                    .lineLimit(1...5)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12))
                    .focused($focused)
                    .onSubmit { submit() }
                    // item 5: ↑/↓ recall submitted history
                    .onKeyPress(.upArrow) { historyKey(up: true) }
                    .onKeyPress(.downArrow) { historyKey(up: false) }
                    .onKeyPress(.escape) { escKey() }
                    .onChange(of: text) { _, _ in slashError = nil }
                Button {
                    submit()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(trimmed.isEmpty ? .secondary : .accentColor)
                }
                .buttonStyle(.plain)
                .disabled(trimmed.isEmpty)
            }
            // item 3: slash-command hint / error; otherwise a keys hint line
            if let err = slashError {
                Text(err)
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
                    .lineLimit(2)
            } else if text.hasPrefix("/") {
                Text(SlashCommands.hintLine)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            } else {
                Text(L("↩ 发送 · ⇧↩ 换行 · Esc 退出",
                       "↩ send · ⇧↩ newline · Esc dismiss"))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.7))
            }
        }
        // first expand: the field lands in the hierarchy here — focus it
        .onAppear { focused = true }
    }

    private var trimmed: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func expand(via: String) {
        if expanded {
            focused = true   // already open → just refocus
            return
        }
        withAnimation(.easeInOut(duration: 0.12)) { expanded = true }
        // 契约F trigger 词表冻结为 user|auto：点击和热键都是用户手势 → "user"；
        // 入口细分（click|hotkey）记在词表外的 via 字段，不占用 trigger。
        Analytics.firstReach("composer")
        Analytics.log("composer_open", fields: ["trigger": "user", "via": via])
        // focus lands via the editor's .onAppear once it exists
    }

    private func collapse() {
        focused = false
        withAnimation(.easeInOut(duration: 0.12)) { expanded = false }
    }

    private func submit() {
        let t = trimmed
        guard !t.isEmpty else { return }
        // 契约F: submitCapture 内部按 source 打 capture_submit（且 slash
        // 命令不计入 capture），这里只传 source，不再重复打点。
        if app.submitCapture(t, source: "kanban") {
            text = ""
            slashError = nil
            historyIndex = nil
            collapse()   // 成功后折叠回一行
        } else if SlashCommands.isCommand(t) {
            // slash command failed: IO error (lastErrorLine) vs typed wrong
            slashError = SlashCommands.lastErrorLine
                ?? (L("未识别或参数错误：", "Unrecognized or bad argument: ") + t)
        } else {
            // capture inbox write failed (wave 2: submitCapture returns false)
            slashError = L("提交失败，已保留输入", "Submit failed — input kept")
        }
    }

    // Esc exits the input WITHOUT discarding the draft (click-outside parity)
    // — IME red line: Esc cancels a live pinyin composition, the input method
    // owns it, pass through untouched.
    private func escKey() -> KeyPress.Result {
        if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
           tv.hasMarkedText() { return .ignored }
        if !text.isEmpty {
            focused = false      // draft present: defocus only, stay expanded
            return .handled      // (collapsing would hide the unsent draft)
        }
        collapse()               // empty: fold back to one line
        return .handled
    }

    // item 5: ↑/↓ history recall — moved verbatim from KanbanView (only when
    // empty or showing an untouched history item; IME candidates win).
    private func historyKey(up: Bool) -> KeyPress.Result {
        if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
           tv.hasMarkedText() { return .ignored }
        let hist = CaptureHistory.items
        guard !hist.isEmpty else { return .ignored }
        let browsing = historyIndex.map {
            hist.indices.contains($0) && text == hist[$0]
        } ?? false
        guard text.isEmpty || browsing else { return .ignored }
        var idx = (browsing ? historyIndex : nil) ?? -1
        idx += up ? 1 : -1
        if idx < 0 {                       // stepped past the newest → empty
            historyIndex = nil
            text = ""
            return .handled
        }
        guard hist.indices.contains(idx) else { return .handled }  // oldest: stay
        historyIndex = idx
        text = hist[idx]
        return .handled
    }
}
