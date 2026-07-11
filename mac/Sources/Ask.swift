// Ask.swift — 问问助手 in-app Q&A (CONTRACT §27)
//
// A question box in the main window backed by a hidden headless-claude run:
// the user asks anything ("为什么没有新卡片?", "怎么换录制模式?") and gets a
// short plain answer grounded in the product's real docs + this machine's
// real state. Terminal never appears.
//
//  - AskModel   runs `runtime-python -m act.ask "<question>"` off the main
//               actor (cancellable Process), parses the §27 JSON line, keeps
//               an elapsed-seconds counter for latency honesty (60 s ceiling
//               lives python-side; a 75 s watchdog here is belt & braces).
//  - AskPageView  input + "思考中" state + answer card (citation, 👍/👎) +
//               classified failure row (FailureCatalog §25 + retry) + the
//               last-20 history read from state/ask_history.json (python is
//               the only writer; the app only renders it).
//
// Privacy: 👍/👎 feedback goes to the LOCAL analytics log only — event name +
// verdict at telemetry level basic; the question text is attached ONLY at
// level "detailed" (emit-side gate, docs/TELEMETRY.md).

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

@MainActor
final class AskModel: ObservableObject {
    enum Phase: Equatable { case idle, thinking, answered, failed }

    struct HistoryEntry: Identifiable {
        let id: String
        let q: String
        let a: String
        let citation: String?
        let ts: String
    }

    @Published var question = ""
    @Published var phase: Phase = .idle
    @Published var answer = ""
    @Published var citation: String? = nil
    @Published var errorText = ""
    @Published var failureId: String? = nil
    @Published var wasTimeout = false
    @Published var elapsed = 0
    @Published var history: [HistoryEntry] = []
    /// "up" | "down" once the user rated the current answer (buttons lock).
    @Published var feedback: String? = nil

    private(set) var lastQuestion = ""
    private var proc: Process?
    private var timer: Timer?
    private var cancelled = false

    /// config.yaml `ask.enabled: false` hides the input (CONTRACT §27).
    static var enabled: Bool {
        (SettingsIO.configNestedScalar(block: "ask", key: "enabled") ?? "true")
            .lowercased() != "false"
    }

    /// Adds `question` to `fields` only when the content gate is open
    /// (capture_input AND level=detailed, Telemetry.contentCaptureActive —
    /// emit-side gate: otherwise the text never reaches events.jsonl).
    private static func logGated(_ event: String, question: String,
                                 fields: [String: Any] = [:]) {
        var f = fields
        if Telemetry.contentCaptureActive() {
            f["question"] = Analytics.clip(question)
        }
        Analytics.log(event, fields: f)
    }

    // MARK: ask / retry / cancel

    func submit() {
        ask(question)
    }

    func retry() {
        guard !lastQuestion.isEmpty else { return }
        ask(lastQuestion)
    }

    func ask(_ q: String) {
        let text = q.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, phase != .thinking else { return }
        lastQuestion = text
        question = ""
        cancelled = false
        feedback = nil
        phase = .thinking
        elapsed = 0
        startTimer()
        Analytics.firstReach("ask")
        Self.logGated("ask_submit", question: text, fields: ["chars": text.count])

        let py = IMessageSettingsModel.runtimePython()
        let root = AppPaths.stateRoot
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: py)
            p.arguments = ["-m", "act.ask", text]
            p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
            var env = ProcessInfo.processInfo.environment
            env["AIASSISTANT_HOME"] = root
            p.environment = env
            let outPipe = Pipe()
            let errPipe = Pipe()
            p.standardOutput = outPipe
            p.standardError = errPipe
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self?.proc = p }
            }
            var launchError: String? = nil
            do { try p.run() } catch { launchError = error.localizedDescription }
            var obj: [String: Any] = [:]
            var errTail = ""
            if launchError == nil {
                let data = outPipe.fileHandleForReading.readDataToEndOfFile()
                let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
                errTail = String((String(data: errData, encoding: .utf8) ?? "").suffix(200))
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self?.finish(obj: obj, launchError: launchError, errTail: errTail)
                }
            }
        }
    }

    func cancel() {
        cancelled = true
        proc?.terminate()
        proc = nil
        stopTimer()
        phase = .idle
        Analytics.log("ask_cancel", fields: ["elapsed_s": elapsed])
    }

    private func finish(obj: [String: Any], launchError: String?, errTail: String) {
        stopTimer()
        proc = nil
        guard !cancelled else { return }  // user already moved on
        if (obj["ok"] as? Bool) == true,
           let text = obj["answer"] as? String, !text.isEmpty {
            answer = text
            citation = (obj["citation"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            phase = .answered
            reloadHistory()
            return
        }
        // failure: prefer python's structured §27 error, fall back honestly
        if let e = obj["error"] as? String, !e.isEmpty {
            errorText = e
            failureId = obj["failure_id"] as? String
            wasTimeout = (obj["timeout"] as? Bool) ?? false
        } else if let launchError {
            errorText = L("问答组件启动失败（\(launchError)）——先到「诊断」页跑一次体检。",
                          "Couldn't start the Q&A helper (\(launchError)) — run a checkup on the Diagnostics page first.")
            failureId = nil
            wasTimeout = false
        } else {
            errorText = errTail.isEmpty
                ? L("没有得到回答——点「重试」再问一次。",
                    "No answer came back — hit Retry.")
                : errTail
            failureId = nil
            wasTimeout = false
        }
        phase = .failed
    }

    // MARK: feedback (local analytics only — basic: event + verdict)

    func rate(_ verdict: String) {
        guard feedback == nil else { return }
        feedback = verdict
        Self.logGated("ask_feedback", question: lastQuestion,
                      fields: ["verdict": verdict])
    }

    // MARK: elapsed timer (latency honesty)

    private func startTimer() {
        stopTimer()
        let t = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func tick() {
        elapsed += 1
        // python enforces the 60 s ceiling itself; if its answer (or timeout
        // JSON) still hasn't landed by 75 s, kill the child — never hang the UI.
        if elapsed >= 75, phase == .thinking {
            proc?.terminate()
            proc = nil
            stopTimer()
            errorText = L("AI 没有在 60 秒内回答——点「重试」再问一次。",
                          "The AI didn't answer within 60 s — hit Retry.")
            failureId = nil
            wasTimeout = true
            phase = .failed
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    // MARK: history (state/ask_history.json — python writes, app renders)

    func reloadHistory() {
        let path = AppPaths.stateRoot + "/state/ask_history.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let entries = obj["entries"] as? [[String: Any]]
        else {
            history = []
            return
        }
        history = entries.enumerated().compactMap { i, e in
            guard let q = e["q"] as? String, let a = e["a"] as? String else { return nil }
            let ts = (e["ts"] as? String) ?? ""
            return HistoryEntry(id: "\(ts)#\(i)", q: q, a: a,
                                citation: (e["citation"] as? String).flatMap {
                                    $0.isEmpty ? nil : $0
                                },
                                ts: ts)
        }
    }
}

// MARK: - Page

struct AskPageView: View {
    @StateObject private var model = AskModel()
    @StateObject private var engine = EngineDetector()
    @ObservedObject private var i18n = LanguageStore.shared
    @FocusState private var inputFocused: Bool
    // guards the engine-missing card until the first detect() has been kicked
    // off — avoids a one-frame orange flash before onAppear runs
    @State private var didAppear = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("问问助手", "Ask the assistant"))
                .font(.system(size: 18, weight: .semibold))
            Text(L("关于这个产品的任何问题——为什么没有新卡片、怎么换录制模式……回答基于产品文档和这台 Mac 的真实状态。提问会把问题、相关文档摘录和机器状态摘要发送给你的 AI 引擎（Anthropic）；在后台完成，不弹终端。",
                   "Ask anything about this product — why there are no new cards, how to switch recording modes… Answers are grounded in the product docs and this Mac's real state. Asking sends your question, the relevant doc excerpts and a machine-state summary to your AI engine (Anthropic); it runs in the background — no terminal window."))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if !AskModel.enabled {
                disabledCard
            } else if didAppear && !engine.checking && !engine.detection.ready {
                engineMissingCard
            } else {
                inputRow
                stateArea
            }

            if !model.history.isEmpty {
                historySection
            }
        }
        .frame(maxWidth: 560, alignment: .leading)
        .font(.system(size: 12))
        .onAppear {
            engine.detect()   // sets checking=true synchronously
            didAppear = true
            model.reloadHistory()
        }
    }

    // MARK: input

    private var inputRow: some View {
        HStack(spacing: 8) {
            TextField(L("输入问题，回车提问", "Type a question, press Return"),
                      text: $model.question)
                .textFieldStyle(.roundedBorder)
                .focused($inputFocused)
                .onSubmit { model.submit() }
                // Esc releases the caret without touching the typed question
                // (click-outside parity; Composer.escKey 同款 IME red line)
                .onKeyPress(.escape) {
                    if let tv = NSApp.keyWindow?.firstResponder as? NSTextView,
                       tv.hasMarkedText() { return .ignored }
                    inputFocused = false
                    return .handled
                }
                .disabled(model.phase == .thinking)
            Button(L("提问", "Ask")) { model.submit() }
                .disabled(model.phase == .thinking ||
                          model.question.trimmingCharacters(
                              in: .whitespacesAndNewlines).isEmpty)
        }
    }

    // MARK: state area (thinking / answer / failure)

    @ViewBuilder
    private var stateArea: some View {
        switch model.phase {
        case .idle:
            EmptyView()
        case .thinking:
            thinkingRow
        case .answered:
            answerCard
        case .failed:
            failureRow
        }
    }

    private var thinkingRow: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            // latency honesty: show real elapsed seconds + the ceiling
            Text(L("思考中… 已 \(model.elapsed) 秒（最多 60 秒）",
                   "Thinking… \(model.elapsed)s elapsed (60s max)"))
                .foregroundColor(.secondary)
            Button(L("取消", "Cancel")) { model.cancel() }
                .controlSize(.small)
            Spacer()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var answerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "bubble.left.fill")
                    .font(.system(size: 11))
                    .foregroundColor(.accentColor)
                    .padding(.top, 2)
                Text(linkified(model.answer))
                    .font(.system(size: 12.5))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                if let cite = model.citation {
                    Label(cite, systemImage: "book")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                Text(L("用时 \(String(format: "%.0f", Double(model.elapsed))) 秒",
                       "\(String(format: "%.0f", Double(model.elapsed)))s"))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                feedbackButtons
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var feedbackButtons: some View {
        HStack(spacing: 4) {
            Button {
                model.rate("up")
            } label: {
                Image(systemName: model.feedback == "up"
                      ? "hand.thumbsup.fill" : "hand.thumbsup")
                    .foregroundColor(model.feedback == "up" ? .green : .secondary)
            }
            .buttonStyle(.plain)
            .disabled(model.feedback != nil)
            .help(L("有帮助（记一条匿名事件，随使用统计上传；基础级不含问题内容，详细级会附问题文本）",
                    "Helpful (logs an anonymous event that uploads with usage stats; Basic carries no question text, Detailed attaches it)"))
            Button {
                model.rate("down")
            } label: {
                Image(systemName: model.feedback == "down"
                      ? "hand.thumbsdown.fill" : "hand.thumbsdown")
                    .foregroundColor(model.feedback == "down" ? .orange : .secondary)
            }
            .buttonStyle(.plain)
            .disabled(model.feedback != nil)
            .help(L("没帮助（记一条匿名事件，随使用统计上传；基础级不含问题内容，详细级会附问题文本）",
                    "Not helpful (logs an anonymous event that uploads with usage stats; Basic carries no question text, Detailed attaches it)"))
        }
        .font(.system(size: 12))
    }

    private var failureRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundColor(.orange)
                    .padding(.top, 1)
                // §25: classified failures get the plain-language sentence;
                // unmatched ones keep the raw text — honesty over prettiness.
                Text(FailureCatalog.message(model.failureId) ?? model.errorText)
                    .foregroundColor(.orange)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .help(model.errorText)
            }
            HStack(spacing: 8) {
                Button(L("重试", "Retry")) { model.retry() }
                    .controlSize(.small)
                if let action = FailureCatalog.actionLabel(model.failureId) {
                    Button(action) { FailureCatalog.perform(model.failureId) }
                        .controlSize(.small)
                }
                Spacer()
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: disabled / engine-missing states

    private var disabledCard: some View {
        Text(L("问问助手已在 config.yaml 里关闭（ask.enabled: false）。",
               "Ask is disabled in config.yaml (ask.enabled: false)."))
            .foregroundColor(.secondary)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.04))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var engineMissingCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "bolt.slash")
                    .foregroundColor(.orange)
                Text(L("AI 引擎未连接——先接入 AI 引擎才能提问。",
                       "The AI engine is not connected — connect it first to ask questions."))
                    .foregroundColor(.orange)
            }
            HStack(spacing: 8) {
                Button(L("去接入（初始设置向导）", "Connect (setup wizard)")) {
                    SetupWizardController.shared.show()
                }
                .controlSize(.small)
                Button(L("重新检测", "Re-detect")) { engine.detect() }
                    .controlSize(.small)
                if engine.checking { ProgressView().controlSize(.small) }
                Spacer()
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: history

    private var historySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("最近的问答", "Recent questions"))
                .font(.system(size: 13, weight: .semibold))
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 0) {
                ForEach(model.history) { e in
                    historyRow(e)
                    if e.id != model.history.last?.id {
                        Divider().padding(.vertical, 6)
                    }
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private func historyRow(_ e: AskModel.HistoryEntry) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(e.q)
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(2)
                if let rel = RelativeTime.since(e.ts) {
                    Text(rel)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
            Text(linkified(e.a))
                .font(.system(size: 11.5))
                .foregroundColor(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let cite = e.citation {
                Label(cite, systemImage: "book")
                    .font(.system(size: 9.5))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
    }
}
