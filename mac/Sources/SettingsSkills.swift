// SettingsSkills.swift — 设置 · Skills（Claude Code 技能）
//
// Read-only inventory + create, v1 scope (user feedback #7):
// - Lists skills from BOTH scopes: user-level ~/.claude/skills/<name>/SKILL.md
//   and project-level <repo>/.claude/skills/<name>/SKILL.md (repo root =
//   AppPaths.stateRoot, CONTRACT §19 — same resolution the whole app uses).
// - Each row shows the frontmatter `description:` (tolerant parser: plain
//   scalar, quoted, or a folded/literal block; missing frontmatter ⇒ shown as
//   "无描述"), a scope badge, and a reveal-in-Finder button.
// - "新建 skill" writes ~/.claude/skills/<name>/SKILL.md with standard
//   frontmatter (name + description) + body. Existing dirs are never
//   overwritten. No edit/delete here — editing goes through Finder.

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

@MainActor
final class SkillsSettingsModel: ObservableObject {
    enum Scope: String, Sendable {
        case user, project
    }

    struct SkillEntry: Identifiable, Sendable {
        var id: String { scope.rawValue + ":" + name }
        let name: String
        let description: String
        let scope: Scope
        let dir: String           // absolute skill directory (contains SKILL.md)
    }

    @Published var skills: [SkillEntry] = []
    @Published var scanning = false
    @Published var scanned = false
    // 新建表单
    @Published var showForm = false
    @Published var newName = ""
    @Published var newDescription = ""
    @Published var newBody = ""
    @Published var createNote = ""
    @Published var createNoteIsError = false

    nonisolated static var userRoot: String { NSHomeDirectory() + "/.claude/skills" }
    nonisolated static var projectRoot: String { AppPaths.stateRoot + "/.claude/skills" }

    var userCount: Int { skills.filter { $0.scope == .user }.count }
    var projectCount: Int { skills.filter { $0.scope == .project }.count }

    // MARK: scan

    func scan() {
        guard !scanning else { return }
        scanning = true
        DispatchQueue.global(qos: .userInitiated).async {
            let found = Self.scanRoots()
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.scanning = false
                    self.scanned = true
                    self.skills = found
                    Analytics.log("mw_skills_scan", fields: ["n": found.count])
                }
            }
        }
    }

    nonisolated private static func scanRoots() -> [SkillEntry] {
        var out = scanRoot(userRoot, scope: .user)
        // a repo checked out at $HOME would make the two roots one — guard
        // against double-listing rather than crash on the weird setup
        if projectRoot != userRoot {
            out += scanRoot(projectRoot, scope: .project)
        }
        return out.sorted {
            $0.name == $1.name ? ($0.scope == .user && $1.scope == .project)
                               : $0.name.localizedCompare($1.name) == .orderedAscending
        }
    }

    /// Every subdirectory holding a SKILL.md is a skill; anything else is noise.
    nonisolated private static func scanRoot(_ root: String, scope: Scope) -> [SkillEntry] {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: root) else { return [] }
        return names.compactMap { name in
            let dir = root + "/" + name
            let md = dir + "/SKILL.md"
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: dir, isDirectory: &isDir), isDir.boolValue,
                  fm.fileExists(atPath: md) else { return nil }
            let text = (try? String(contentsOfFile: md, encoding: .utf8)) ?? ""
            return SkillEntry(name: name,
                              description: frontmatterDescription(text),
                              scope: scope, dir: dir)
        }
    }

    /// Tolerant frontmatter reader — returns "" when there is no frontmatter
    /// or no description (the row then shows 无描述). Handles the shapes seen
    /// in the wild: plain scalar, single/double-quoted, and `>`/`|` blocks
    /// (continuation = the indented lines that follow).
    nonisolated static func frontmatterDescription(_ text: String) -> String {
        let lines = text.components(separatedBy: .newlines)
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else { return "" }
        var i = 1
        while i < lines.count {
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            if t == "---" { break }
            // Top-level keys are flush-left; a trimmed prefix check would also
            // match a nested mapping's indented `description:`.
            if lines[i].hasPrefix("description:") {
                var v = String(t.dropFirst("description:".count))
                    .trimmingCharacters(in: .whitespaces)
                if v.isEmpty || ["|", "|-", ">", ">-"].contains(v) {
                    var parts: [String] = []
                    var j = i + 1
                    while j < lines.count, lines[j].hasPrefix(" ") {
                        let c = lines[j].trimmingCharacters(in: .whitespaces)
                        if c == "---" || c.isEmpty { break }
                        parts.append(c)
                        j += 1
                    }
                    v = parts.joined(separator: " ")
                }
                if v.count >= 2,
                   (v.hasPrefix("\"") && v.hasSuffix("\""))
                    || (v.hasPrefix("'") && v.hasSuffix("'")) {
                    v = String(v.dropFirst().dropLast())
                }
                return v
            }
            i += 1
        }
        return ""
    }

    // MARK: create (user scope only, never overwrites)

    nonisolated static func isValidName(_ s: String) -> Bool {
        s.range(of: "^[a-z0-9]+(-[a-z0-9]+)*$", options: .regularExpression) != nil
    }

    /// Live inline hint under the name field; nil = fine (empty shows nothing,
    /// the Save button is disabled instead).
    var nameHint: String? {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !Self.isValidName(name) else { return nil }
        return L("名称须是 kebab-case：小写字母/数字，用 - 连接（例：my-skill）",
                 "Name must be kebab-case: lowercase letters/digits joined by - (e.g. my-skill)")
    }

    func create() {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard Self.isValidName(name) else { return }
        let dir = Self.userRoot + "/" + name
        let fm = FileManager.default
        if fm.fileExists(atPath: dir) {
            createNote = L("已有同名 skill（\(name)）——不覆盖。用「在 Finder 显示」直接改它，或换个名字。",
                           "A skill named \(name) already exists — not overwriting. Edit it via \"Reveal in Finder\", or pick another name.")
            createNoteIsError = true
            return
        }
        // A pasted multi-line description would break the single-line
        // `description:` scalar — fold newlines into spaces before quoting.
        let desc = newDescription
            .replacingOccurrences(of: "[\r\n]+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        let body = newBody.trimmingCharacters(in: .whitespacesAndNewlines)
        var content = "---\nname: \(name)\ndescription: \(Self.yamlScalar(desc))\n---\n"
        if !body.isEmpty { content += "\n" + body + "\n" }
        do {
            try fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try content.write(toFile: dir + "/SKILL.md", atomically: true, encoding: .utf8)
        } catch {
            createNote = L("写入失败: ", "Write failed: ") + error.localizedDescription
            createNoteIsError = true
            return
        }
        createNote = L("已创建 ✓ ~/.claude/skills/\(name)/SKILL.md——Claude Code 下次会话即可用；要改内容用「在 Finder 显示」。",
                       "Created ✓ ~/.claude/skills/\(name)/SKILL.md — available to Claude Code from its next session; edit it via \"Reveal in Finder\".")
        createNoteIsError = false
        newName = ""; newDescription = ""; newBody = ""
        showForm = false
        Analytics.log("mw_skills_create")
        scan()
    }

    /// Plain YAML scalars break on ": " / " #" / a leading indicator char —
    /// quote defensively so a pasted description can't corrupt the frontmatter.
    nonisolated static func yamlScalar(_ s: String) -> String {
        let leading = s.first.map { "!&*?|>%@`\"'{}[]#,-".contains($0) } ?? false
        guard s.contains(": ") || s.contains(" #") || s.hasSuffix(":") || leading
        else { return s }
        let escaped = s.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        return "\"\(escaped)\""
    }

    func reveal(_ e: SkillEntry) {
        // select the SKILL.md itself — that's the file to edit
        NSWorkspace.shared.activateFileViewerSelecting(
            [URL(fileURLWithPath: e.dir + "/SKILL.md")])
        Analytics.log("mw_skills_reveal", fields: ["scope": e.scope.rawValue])
    }
}

// MARK: - View

struct SkillsSettingsSection: View {
    @StateObject private var model = SkillsSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    // Content-only (v0.21): the card / title / collapse chrome is supplied by
    // the shared CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("Skill 是给 Claude Code 的可复用指令包（一个文件夹 + SKILL.md）。这里汇总两个作用域：用户级 ~/.claude/skills/ 对所有项目生效，项目级 <仓库>/.claude/skills/ 只对本仓库生效。此处只看和新建；改内容用「在 Finder 显示」直接编辑文件。",
                   "A skill is a reusable instruction pack for Claude Code (a folder + SKILL.md). Both scopes are listed: user-level ~/.claude/skills/ applies everywhere, project-level <repo>/.claude/skills/ applies to this repo only. This panel lists and creates; to edit, use \"Reveal in Finder\" and change the file."))
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
                if model.scanned && !model.skills.isEmpty {
                    Text(L("共 \(model.skills.count) 个（用户级 \(model.userCount) · 项目级 \(model.projectCount)）",
                           "\(model.skills.count) total (user \(model.userCount) · project \(model.projectCount))"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                Spacer()
                Button(model.showForm ? L("收起表单", "Hide form") : L("新建 skill", "New skill")) {
                    model.showForm.toggle()
                }
                .controlSize(.small)
            }

            if model.showForm { createForm }

            if !model.createNote.isEmpty {
                Text(model.createNote)
                    .font(.system(size: 11))
                    .foregroundColor(model.createNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if model.scanned && model.skills.isEmpty {
                Text(L("还没有任何 skill——点上面「新建 skill」创建第一个，或把现成的 skill 文件夹放进 ~/.claude/skills/ 再点「刷新」。",
                       "No skills yet — click \"New skill\" above to create your first, or drop an existing skill folder into ~/.claude/skills/ and hit Refresh."))
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if !model.skills.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(model.skills) { e in
                        skillRow(e)
                        if e.id != model.skills.last?.id { Divider() }
                    }
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.04))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
        }
        .font(.system(size: 12))
        .onAppear { if !model.scanned { model.scan() } }
    }

    // MARK: rows

    private func skillRow(_ e: SkillsSettingsModel.SkillEntry) -> some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(e.name)
                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                        .lineLimit(1)
                    scopeBadge(e.scope)
                    Spacer(minLength: 0)
                }
                Text(e.description.isEmpty ? L("无描述", "No description") : e.description)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button(L("在 Finder 显示", "Reveal in Finder")) { model.reveal(e) }
                .controlSize(.small)
        }
    }

    private func scopeBadge(_ scope: SkillsSettingsModel.Scope) -> some View {
        let (label, color): (String, Color) = scope == .user
            ? (L("用户", "user"), .blue)
            : (L("项目", "project"), .purple)
        return Text(label)
            .font(.system(size: 9, weight: .semibold))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(color.opacity(0.18))
            .foregroundColor(color)
            .clipShape(Capsule())
    }

    // MARK: create form

    private var createForm: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("新建 skill（保存到 ~/.claude/skills/，用户级）",
                   "New skill (saved to ~/.claude/skills/, user scope)"))
                .font(.system(size: 12, weight: .medium))
            TextField(L("名称（kebab-case，例：my-skill）", "Name (kebab-case, e.g. my-skill)"),
                      text: $model.newName)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
            if let hint = model.nameHint {
                Text(hint)
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            TextField(L("一句话描述（Claude 靠它决定何时启用这个 skill）",
                        "One-line description (Claude uses it to decide when to activate the skill)"),
                      text: $model.newDescription)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12))
            Text(L("正文指令（SKILL.md 的内容，Markdown）", "Body instructions (SKILL.md content, Markdown)"))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            TextEditor(text: $model.newBody)
                .font(.system(size: 12, design: .monospaced))
                .frame(height: 110)
                .scrollContentBackground(.hidden)
                .padding(4)
                .background(Color.primary.opacity(0.03))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay {
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.primary.opacity(0.12), lineWidth: 1)
                }
            HStack(spacing: 8) {
                Button(L("保存", "Save")) { model.create() }
                    .controlSize(.small)
                    .disabled(model.newName.trimmingCharacters(in: .whitespaces).isEmpty
                              || model.nameHint != nil)
                Button(L("取消", "Cancel")) { model.showForm = false }
                    .controlSize(.small)
                Spacer()
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
