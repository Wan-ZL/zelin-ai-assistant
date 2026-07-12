// SettingsClaudeImport.swift — 设置 · 导入 Claude Code 工作（CONTRACT §22）
//
// Cold-start seeding for an empty kanban: scan the user's recent Claude Code
// sessions (~/.claude/projects), preview them with per-item checkboxes
// (waiting-on-you sessions pre-checked — the tired-user default), and write
// ONE `import_claude_sessions` inbox action for the selected ids. actd turns
// them into normal proposal cards within seconds. Everything stays local.
//
// The scan itself runs python-side (`act.radar_claude_sessions --scan`) via
// the pinned runtime python (CONTRACT §19), so app and daemon share one
// parser and the transcript format lives in exactly one place.
//
// Frozen scroll anchor: "claude_import" — the setup wizard's finale (and any
// other surface) can deep-link here via
//   MainNav.shared.pendingAnchor = "claude_import"; MainNav.shared.section = .settings
// (same mechanism as the "credentials" anchor, MainWindow.swift contract 3).

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

@MainActor
final class ClaudeImportModel: ObservableObject {
    struct Candidate: Identifiable {
        var id: String { sessionId }
        let sessionId: String
        let project: String
        let title: String
        let gist: String
        let lastActivity: String
        let waiting: Bool
        /// Soft gate: looks like an answered closed-loop Q&A. Shown (unchecked,
        /// sorted last by the scanner) as an escape hatch — the heuristic has
        /// false positives, and checking the box overrides it python-side.
        let answered: Bool
    }

    @Published var candidates: [Candidate] = []
    @Published var selected: Set<String> = []
    @Published var scanning = false
    @Published var scanned = false            // at least one scan finished
    @Published var emptyReason = ""           // one-sentence empty/error state
    @Published var importStatus = ""
    @Published var importFailed = false
    @Published var showAll = false

    /// ids already sent for import this app session — filtered out of re-scans
    /// so an immediate 重新扫描 doesn't resurface rows actd is still processing.
    private var locallyImported: Set<String> = []

    var waitingCount: Int { candidates.filter { $0.waiting }.count }

    // MARK: scan

    func scan() {
        guard !scanning else { return }
        scanning = true
        importStatus = ""
        importFailed = false
        Analytics.log("mw_claude_import_scan")
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, reason, cands) = Self.runScan()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.scanning = false
                    self.scanned = true
                    guard ok else {
                        self.candidates = []
                        self.selected = []
                        self.emptyReason = reason
                        return
                    }
                    let fresh = cands.filter { !self.locallyImported.contains($0.sessionId) }
                    self.candidates = fresh
                    // tired-user default: waiting-on-you checked, merely-recent not
                    self.selected = Set(fresh.filter { $0.waiting }.map { $0.sessionId })
                    self.emptyReason = fresh.isEmpty
                        ? L("最近 7 天没有可导入的 Claude Code 会话——在看板输入框写一句话也能直接建卡。",
                            "No importable Claude Code sessions in the last 7 days — you can also just type a line in the board's input to create a card.")
                        : ""
                }
            }
        }
    }

    /// Blocking (background queue): runtime python → `--scan --window 7`,
    /// full-stdout JSON per CONTRACT §22.
    nonisolated private static func runScan() -> (Bool, String, [Candidate]) {
        let py = RuntimePython.resolve()
        let root = AppPaths.stateRoot
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = ["-m", "act.radar_claude_sessions", "--scan", "--window", "7"]
        p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        env["AIASSISTANT_HOME"] = root
        p.environment = env
        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe
        do { try p.run() } catch {
            return (false,
                    L("扫描组件启动失败（\(error.localizedDescription)）——先到「诊断」页跑一次体检。",
                      "Couldn't start the scanner (\(error.localizedDescription)) — run a checkup on the Diagnostics page first."),
                    [])
        }
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0,
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else {
            let tail = String((String(data: errData, encoding: .utf8) ?? "").suffix(160))
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return (false,
                    L("扫描没跑成——先到「诊断」页跑一次体检。", "The scan didn't run — run a checkup on the Diagnostics page first.")
                        + (tail.isEmpty ? "" : " (\(tail))"),
                    [])
        }
        if (obj["ok"] as? Bool) != true {
            // the only structured reason today: no_claude_dir
            return (false,
                    L("这台 Mac 上没找到 Claude Code 的会话记录（~/.claude/projects）——用过 Claude Code 之后再来导入。",
                      "No Claude Code session history found on this Mac (~/.claude/projects) — come back after you've used Claude Code."),
                    [])
        }
        let raw = obj["candidates"] as? [[String: Any]] ?? []
        let cands: [Candidate] = raw.compactMap { d in
            guard let sid = d["session_id"] as? String, !sid.isEmpty else { return nil }
            return Candidate(
                sessionId: sid,
                project: (d["project"] as? String) ?? "?",
                title: (d["title"] as? String) ?? "",
                gist: (d["gist"] as? String) ?? "",
                lastActivity: (d["last_activity"] as? String) ?? "",
                waiting: (d["ended_waiting_on_user"] as? Bool) ?? false,
                answered: (d["answered"] as? Bool) ?? false)
        }
        return (true, "", cands)
    }

    // MARK: selection

    func toggle(_ id: String, on: Bool) {
        if on { selected.insert(id) } else { selected.remove(id) }
    }

    func selectAll() { selected = Set(candidates.map { $0.id }) }
    func selectNone() { selected = [] }

    // MARK: import

    func importSelected() {
        let ids = candidates.map { $0.sessionId }.filter { selected.contains($0) }
        guard !ids.isEmpty else { return }
        let dict: [String: Any] = [
            "action": "import_claude_sessions",
            "session_ids": ids,
            "ts": ISO8601DateFormatter().string(from: Date()),
        ]
        do {
            try FileManager.default.createDirectory(atPath: AppPaths.inboxDir,
                                                    withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: dict,
                                                  options: [.prettyPrinted, .sortedKeys])
            let path = AppPaths.inboxDir + "/" + UUID().uuidString + ".json"
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
        } catch {
            importFailed = true
            importStatus = L("这次导入没写进队列（磁盘或权限问题）——请再点一次。",
                             "The import didn't reach the queue (disk or permission issue) — please click again.")
            return
        }
        locallyImported.formUnion(ids)
        candidates.removeAll { locallyImported.contains($0.sessionId) }
        selected = []
        importFailed = false
        importStatus = L("已提交 \(ids.count) 条——后台服务几秒内会把它们变成看板卡片（等你回复的进「提案」，其余进「储备」）。",
                         "Submitted \(ids.count) — the background service turns them into board cards within seconds (waiting-on-you ones go to Proposals, the rest to Backlog).")
        Analytics.log("mw_claude_import_submit", fields: ["n": ids.count])
    }
}

// MARK: - View

struct ClaudeImportSettingsSection: View {
    @StateObject private var model = ClaudeImportModel()
    @ObservedObject private var i18n = LanguageStore.shared

    private let previewCap = 8

    // Content-only (v0.21): the card / title / collapse chrome + the frozen
    // "claude_import" anchor + flash-on-arrival are supplied by the shared
    // CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("把你最近在 Claude Code 里做的事一键变成看板卡片，尤其是 AI 还在等你回复的那些。全程本地，不上传任何内容。",
                   "Turn your recent Claude Code work into board cards in one click — especially sessions where the AI is still waiting on your reply. Everything stays local; nothing is uploaded."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                Button(model.scanning
                       ? L("扫描中…", "Scanning…")
                       : (model.scanned ? L("重新扫描", "Re-scan")
                                        : L("扫描最近 7 天", "Scan last 7 days"))) {
                    model.scan()
                }
                .disabled(model.scanning)
                if model.scanning { ProgressView().controlSize(.small) }
                if model.scanned && !model.candidates.isEmpty {
                    Text(L("找到 \(model.candidates.count) 个会话，其中 \(model.waitingCount) 个在等你回复（已默认勾选）",
                           "Found \(model.candidates.count) sessions — \(model.waitingCount) waiting on you (pre-checked)"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }

            if model.scanned && !model.emptyReason.isEmpty {
                Text(model.emptyReason)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if !model.candidates.isEmpty {
                HStack(spacing: 8) {
                    Button(L("全选", "Select all")) { model.selectAll() }
                        .controlSize(.small)
                    Button(L("全不选", "Select none")) { model.selectNone() }
                        .controlSize(.small)
                    Spacer()
                }
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(visibleCandidates) { c in
                        candidateRow(c)
                        if c.id != visibleCandidates.last?.id { Divider() }
                    }
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.04))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                if model.candidates.count > previewCap {
                    Button(model.showAll
                           ? L("收起", "Show fewer")
                           : L("显示全部 (\(model.candidates.count))",
                               "Show all (\(model.candidates.count))")) {
                        model.showAll.toggle()
                    }
                    .controlSize(.small)
                }
                HStack(spacing: 8) {
                    Button(L("导入所选 (\(model.selected.count))",
                             "Import selected (\(model.selected.count))")) {
                        model.importSelected()
                    }
                    .disabled(model.selected.isEmpty)
                    Spacer()
                }
            }

            if !model.importStatus.isEmpty {
                Text(model.importStatus)
                    .font(.system(size: 11))
                    .foregroundColor(model.importFailed ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .font(.system(size: 12))
    }

    private var visibleCandidates: [ClaudeImportModel.Candidate] {
        model.showAll ? model.candidates : Array(model.candidates.prefix(previewCap))
    }

    private func candidateRow(_ c: ClaudeImportModel.Candidate) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Toggle("", isOn: Binding(
                get: { model.selected.contains(c.id) },
                set: { model.toggle(c.id, on: $0) }))
                .toggleStyle(.checkbox)
                .labelsHidden()
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(c.project)
                        .font(.system(size: 12, weight: .medium))
                        .lineLimit(1)
                    if c.waiting {
                        Text(L("等你回复", "waiting on you"))
                            .font(.system(size: 9, weight: .semibold))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.orange.opacity(0.18))
                            .foregroundColor(.orange)
                            .clipShape(Capsule())
                    }
                    if c.answered {
                        // soft-gate badge: why the row sits at the bottom,
                        // unchecked — checking it still imports (override)
                        Text(L("像已答完的问答", "looks answered"))
                            .font(.system(size: 9, weight: .semibold))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.secondary.opacity(0.14))
                            .foregroundColor(.secondary)
                            .clipShape(Capsule())
                    }
                    if let rel = RelativeTime.since(c.lastActivity) {
                        Text(rel)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                }
                Text(c.gist.isEmpty ? c.title : c.gist)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
