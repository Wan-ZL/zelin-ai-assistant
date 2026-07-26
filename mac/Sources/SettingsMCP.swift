// SettingsMCP.swift — 设置 · MCP servers（Claude Code 外接工具，只读 v1）
//
// Lists the MCP servers Claude Code would load, across both scopes:
//   user:    ~/.claude.json         → top-level "mcpServers" object
//   project: <repo>/.mcp.json       → "mcpServers"  (repo = AppPaths.stateRoot)
// (~/.claude.json's per-project projects.<path>.mcpServers is out of scope v1.)
//
// v1 is read-only: list + reveal-in-Finder + rescan. No in-app add/edit/remove
// — hand-editing that JSON is risky, so changes go through `claude mcp add`
// or an editor, and this section just reflects reality.
//
// Privacy rule (non-obvious, load-bearing): ~/.claude.json also holds
// unrelated per-user Claude Code state. Parse ONLY the mcpServers subtree —
// never render or log anything else from the file. A server's `env` block may
// carry tokens, so it surfaces as a key COUNT only ("env ×3"), never values.

import AppKit
import SwiftUI
import Foundation

// MARK: - Scope

// Top-level on purpose: members are called from nonisolated scan code, so the
// enum must not inherit a model class's @MainActor isolation.
enum MCPScope: String {
    case user, project

    var configPath: String {
        switch self {
        case .user: return NSHomeDirectory() + "/.claude.json"
        case .project: return AppPaths.stateRoot + "/.mcp.json"
        }
    }
}

// MARK: - Model

@MainActor
final class MCPSettingsModel: ObservableObject {
    struct Server: Identifiable {
        var id: String { scope.rawValue + ":" + name }
        let name: String
        let scope: MCPScope
        let transport: String   // "stdio" | "http" | "sse"
        let summary: String     // stdio: command + args; remote: URL
        let envCount: Int       // key count only — values never leave the file
    }

    @Published var servers: [Server] = []
    @Published var userNote = ""        // per-scope gray note (missing/bad JSON/empty)
    @Published var projectNote = ""
    @Published var userFileExists = false
    @Published var projectFileExists = false
    @Published var scanning = false
    @Published var scanned = false

    private var loaded = false

    func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        scan()
    }

    func scan() {
        guard !scanning else { return }
        scanning = true
        DispatchQueue.global(qos: .userInitiated).async {
            let user = Self.readScope(.user)
            let project = Self.readScope(.project)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.scanning = false
                    self.scanned = true
                    self.servers = user.servers + project.servers
                    self.userNote = user.note
                    self.projectNote = project.note
                    self.userFileExists = user.exists
                    self.projectFileExists = project.exists
                    Analytics.log("mw_mcp_scan", fields: [
                        "user": user.servers.count,
                        "project": project.servers.count,
                    ])
                }
            }
        }
    }

    func reveal(_ scope: MCPScope) {
        Analytics.log("mw_mcp_reveal", fields: ["scope": scope.rawValue])
        NSWorkspace.shared.activateFileViewerSelecting(
            [URL(fileURLWithPath: scope.configPath)])
    }

    func count(in scope: MCPScope) -> Int {
        servers.filter { $0.scope == scope }.count
    }

    /// Blocking (background queue). note == "" ⇔ at least one server parsed.
    nonisolated private static func readScope(_ scope: MCPScope)
        -> (note: String, servers: [Server], exists: Bool)
    {
        let path = scope.configPath
        guard FileManager.default.fileExists(atPath: path) else {
            return (L("文件不存在——这个作用域还没配置过 MCP server。",
                      "File not found — no MCP servers configured in this scope yet."),
                    [], false)
        }
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else {
            return (L("JSON 解析失败——点「在 Finder 显示」用编辑器检查语法。",
                      "Couldn't parse the JSON — click \"Reveal in Finder\" and check the syntax in an editor."),
                    [], true)
        }
        // privacy rule (header): only the mcpServers subtree is ever extracted.
        guard let mcp = obj["mcpServers"] as? [String: Any], !mcp.isEmpty else {
            return (L("文件里还没有 mcpServers 条目。", "No mcpServers entry in the file yet."),
                    [], true)
        }
        let servers: [Server] = mcp.keys.sorted().compactMap { name in
            guard let v = mcp[name] as? [String: Any] else { return nil }
            let transport = inferTransport(v)
            let summary: String
            if transport == "stdio" {
                let cmd = (v["command"] as? String) ?? ""
                let args = (v["args"] as? [Any])?.compactMap { $0 as? String } ?? []
                summary = ([cmd] + args).filter { !$0.isEmpty }.joined(separator: " ")
            } else {
                summary = (v["url"] as? String) ?? ""
            }
            return Server(
                name: name,
                scope: scope,
                transport: transport,
                summary: summary.isEmpty ? L("（配置不完整）", "(incomplete config)") : summary,
                envCount: (v["env"] as? [String: Any])?.count ?? 0)
        }
        return ("", servers, true)
    }

    /// Explicit `type` wins (incl. "streamable-http" variants); otherwise a
    /// `url` means remote (http) and anything else is a local stdio command.
    nonisolated private static func inferTransport(_ v: [String: Any]) -> String {
        if let t = (v["type"] as? String)?.lowercased(), !t.isEmpty {
            if t.contains("sse") { return "sse" }
            if t.contains("http") { return "http" }
            if t.contains("stdio") { return "stdio" }
        }
        return v["url"] != nil ? "http" : "stdio"
    }
}

// MARK: - View

struct MCPSettingsSection: View {
    @StateObject private var model = MCPSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    // Content-only (v0.21): the card / title / collapse chrome is supplied by
    // the shared CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("MCP server 是 Claude Code 能调用的外接工具服务器——如果说 skill 是菜谱，MCP 就是外接厨具（数据库、浏览器、Slack 这类能力）。这里只读展示，增删改在终端用 `claude mcp add` / `claude mcp remove`，或直接编辑配置文件。",
                   "MCP servers are external tool servers Claude Code can call — if a skill is a recipe, an MCP server is a plug-in kitchen appliance (databases, browsers, Slack and the like). This list is read-only; add or remove servers with `claude mcp add` / `claude mcp remove` in a terminal, or edit the config files directly."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                Button(model.scanning ? L("扫描中…", "Scanning…") : L("刷新", "Refresh")) {
                    model.scan()
                }
                .controlSize(.small)
                .disabled(model.scanning)
                if model.scanning { ProgressView().controlSize(.small) }
                if model.scanned && !model.servers.isEmpty {
                    Text(L("共 \(model.servers.count) 个 server（用户 \(model.count(in: .user)) · 项目 \(model.count(in: .project))）",
                           "\(model.servers.count) servers (user \(model.count(in: .user)) · project \(model.count(in: .project)))"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }

            if !model.servers.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(model.servers) { s in
                        serverRow(s)
                        if s.id != model.servers.last?.id { Divider() }
                    }
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.04))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            } else if model.scanned {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("两个作用域都还没有 MCP server。到终端里加一个，回来点「刷新」就能看到：",
                           "No MCP servers in either scope yet. Add one in a terminal, then come back and hit Refresh:"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("claude mcp add <name> -- <command>")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                }
            }

            Divider()
            scopeRow(.user)
            scopeRow(.project)
        }
        .font(.system(size: 12))
        .onAppear { model.loadIfNeeded() }
        // per-scope notes are built with L() at scan time — rebuild on switch
        .onChange(of: i18n.lang) { _, _ in model.scan() }
    }

    // MARK: rows

    private func serverRow(_ s: MCPSettingsModel.Server) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(s.name)
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(1)
                badge(s.transport, transportColor(s.transport))
                badge(s.scope == .user ? L("用户", "user") : L("项目", "project"),
                      s.scope == .user ? .teal : .green)
                if s.envCount > 0 {
                    Text("env ×\(s.envCount)")
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                        .help(L("环境变量只显示数量——值可能含密钥，绝不显示。",
                                "Env vars show as a count only — values may hold secrets and are never displayed."))
                }
                Spacer()
            }
            Text(s.summary)
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(s.summary)
        }
    }

    private func scopeRow(_ scope: MCPScope) -> some View {
        let note = scope == .user ? model.userNote : model.projectNote
        let exists = scope == .user ? model.userFileExists : model.projectFileExists
        return HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(scope == .user ? L("用户级", "User scope") : L("项目级", "Project scope"))
                        .font(.system(size: 12, weight: .medium))
                    if model.scanned && note.isEmpty {
                        Text(L("\(model.count(in: scope)) 个 server", "\(model.count(in: scope)) servers"))
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                }
                Text(abbrevHome(scope.configPath))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                if model.scanned && !note.isEmpty {
                    Text(note)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
            Button(L("在 Finder 显示", "Reveal in Finder")) { model.reveal(scope) }
                .controlSize(.small)
                .disabled(!exists)
        }
    }

    private func badge(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .semibold))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(color.opacity(0.16))
            .foregroundColor(color)
            .clipShape(Capsule())
    }

    private func transportColor(_ t: String) -> Color {
        switch t {
        case "http": return .blue
        case "sse": return .purple
        default: return .secondary   // stdio
        }
    }

    private func abbrevHome(_ path: String) -> String {
        let home = NSHomeDirectory()
        guard path.hasPrefix(home) else { return path }
        return "~" + String(path.dropFirst(home.count))
    }
}
