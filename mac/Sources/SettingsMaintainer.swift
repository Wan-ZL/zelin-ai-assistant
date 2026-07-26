// SettingsMaintainer.swift — 设置 · 开发者 · 维护会话（用户建议 #6）
//
// One-click "open a claude session over THIS software's own repo": a user
// who hit a bug (or a tinkerer who wants a change) clicks one button, a
// terminal opens with `cd <repo> && claude [--resume <sid>]`, and they tell
// claude what to fix in plain language — no docs, no manual cd, no CLI
// knowledge required.
//
// - repo path row: `maintainer_repo_path` override, diff-write (empty or
//   equal to the resolved default ⇒ key removed). Default =
//   AppPaths.stateRoot — the CONTRACT §19 repo-root resolution the whole
//   app already uses; the placeholder shows the resolved value.
// - session id row: `maintainer_session_id` override (empty = fresh session,
//   no --resume). Allowlist [A-Za-z0-9-] — claude session ids are UUIDs, and
//   the allowlist doubles as the injection gate since the id later rides on
//   a shell command line.
// - SECURITY (TerminalLauncher precondition: app-generated commands only):
//   the command is assembled HERE from validated parts — the path becomes a
//   single shell word via shellSingleQuoted (the same quoting the pipeline
//   uses for copy_cmd's `cd '<path>' && claude --resume <sid>`), and the
//   session id passed the character allowlist, so user-typed text can never
//   smuggle a second command into the line.

import AppKit
import SwiftUI
import Foundation

// MARK: - Model

@MainActor
final class MaintainerSettingsModel: ObservableObject {
    @Published var repoPath = ""
    @Published var repoPathNote = ""
    @Published var repoPathNoteIsError = false
    @Published var sessionID = ""
    @Published var sessionNote = ""
    @Published var sessionNoteIsError = false
    @Published var launchNote = ""
    @Published var launchNoteIsError = false

    private var loaded = false

    /// Default = the repo this very app runs from (CONTRACT §19 resolution).
    nonisolated static var defaultRepoPath: String { AppPaths.stateRoot }

    /// Effective repo path: non-empty field (tilde-expanded) else the default.
    /// The open button acts on THIS — what the field shows is what runs.
    var effectiveRepoPath: String {
        let v = repoPath.trimmingCharacters(in: .whitespaces)
        return v.isEmpty ? Self.defaultRepoPath
                         : (v as NSString).expandingTildeInPath
    }

    /// Gate for the open button: the effective path must be an existing folder.
    var repoPathExists: Bool {
        var isDir: ObjCBool = false
        return FileManager.default.fileExists(atPath: effectiveRepoPath,
                                              isDirectory: &isDir)
            && isDir.boolValue
    }

    func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        let ov = SettingsIO.readOverrides()
        repoPath = (ov["maintainer_repo_path"] as? String) ?? ""
        sessionID = (ov["maintainer_session_id"] as? String) ?? ""
    }

    // MARK: repo path (diff-write: empty / equal to default ⇒ key removed)

    func saveRepoPath() {
        let v = repoPath.trimmingCharacters(in: .whitespaces)
        repoPath = v
        var merged = SettingsIO.readOverrides()
        if v.isEmpty || (v as NSString).expandingTildeInPath == Self.defaultRepoPath {
            merged.removeValue(forKey: "maintainer_repo_path")
        } else {
            merged["maintainer_repo_path"] = v
        }
        do {
            try SettingsIO.writeOverrides(merged)
        } catch {
            repoPathNote = L("保存设置失败: ", "Failed to save settings: ")
                + error.localizedDescription
            repoPathNoteIsError = true
            return
        }
        if v.isEmpty {
            repoPathNote = L("已清空——使用默认仓库目录。",
                             "Cleared — the default repo folder is used.")
            repoPathNoteIsError = false
        } else if !repoPathExists {
            repoPathNote = L("已保存，但这个路径现在不存在——按钮会一直禁用，直到路径有效。",
                             "Saved, but that path doesn't exist right now — the button stays disabled until it does.")
            repoPathNoteIsError = true
        } else {
            repoPathNote = L("已保存 ✓", "Saved ✓")
            repoPathNoteIsError = false
        }
        Analytics.log("mw_maintainer_repo_save", fields: ["set": !v.isEmpty])
    }

    // MARK: session id (empty = fresh session; [A-Za-z0-9-] only)

    /// nil = ok; otherwise a plain-language fix message. The allowlist is the
    /// injection gate — the id is embedded in a shell command line later.
    nonisolated static func validateSessionID(_ raw: String) -> String? {
        let s = raw.trimmingCharacters(in: .whitespaces)
        guard !s.isEmpty else { return nil }
        let allowed = CharacterSet(charactersIn:
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")
        if s.unicodeScalars.allSatisfy({ allowed.contains($0) }) { return nil }
        return L("会话 ID 只能包含字母、数字和连字符（-）——从 claude 里复制的会话 ID 就是这个样子。",
                 "A session id may only contain letters, digits, and hyphens (-) — the id you copy from claude is exactly that shape.")
    }

    func saveSessionID() {
        let v = sessionID.trimmingCharacters(in: .whitespaces)
        sessionID = v
        if let err = Self.validateSessionID(v) {
            sessionNote = err
            sessionNoteIsError = true
            return
        }
        var merged = SettingsIO.readOverrides()
        if v.isEmpty {
            merged.removeValue(forKey: "maintainer_session_id")
        } else {
            merged["maintainer_session_id"] = v
        }
        do {
            try SettingsIO.writeOverrides(merged)
        } catch {
            sessionNote = L("保存设置失败: ", "Failed to save settings: ")
                + error.localizedDescription
            sessionNoteIsError = true
            return
        }
        sessionNote = v.isEmpty
            ? L("已清空——按钮每次开一个全新会话。",
                "Cleared — the button starts a fresh session each time.")
            : L("已保存 ✓ 按钮会用 --resume 接着这个会话聊。",
                "Saved ✓ The button resumes this session with --resume.")
        sessionNoteIsError = false
        Analytics.log("mw_maintainer_session_save", fields: ["set": !v.isEmpty])
    }

    // MARK: open the session

    func openSession() {
        let sid = sessionID.trimmingCharacters(in: .whitespaces)
        if let err = Self.validateSessionID(sid) {
            sessionNote = err
            sessionNoteIsError = true
            return
        }
        let path = effectiveRepoPath
        guard repoPathExists else {
            launchNote = L("路径不存在——先在上面填一个存在的仓库目录。",
                           "That path doesn't exist — set an existing repo folder above first.")
            launchNoteIsError = true
            return
        }
        // SECURITY: app-assembled command (the TerminalLauncher precondition)
        // — shellSingleQuoted turns the path into one shell word (the same
        // quoting the pipeline's copy_cmd uses) and sid already passed the
        // [A-Za-z0-9-] allowlist above.
        var cmd = "cd " + TerminalLauncher.shellSingleQuoted(path) + " && claude"
        if !sid.isEmpty { cmd += " --resume " + sid }
        Analytics.firstReach("maintainer_session")
        Analytics.log("mw_maintainer_open",
                      fields: ["resume": !sid.isEmpty,
                               "app": TerminalLauncher.preferred.rawValue])
        launchNote = L("正在打开终端…", "Opening the terminal…")
        launchNoteIsError = false
        TerminalLauncher.launch(cmd) { ok in
            // completion arrives on the main queue (TerminalLauncher contract)
            MainActor.assumeIsolated {
                if ok {
                    self.launchNote = L("已在终端打开 ✓ 直接告诉它要修什么、改什么就行。",
                                        "Opened in the terminal ✓ — just tell it what to fix or change.")
                    self.launchNoteIsError = false
                } else {
                    self.launchNote = L("打开终端失败——去「通用」检查终端应用设置，或手动在终端运行：\(cmd)",
                                        "Couldn't open the terminal — check the terminal app under General, or run this by hand: \(cmd)")
                    self.launchNoteIsError = true
                }
            }
        }
    }
}

// MARK: - View

struct MaintainerSettingsSection: View {
    @StateObject private var model = MaintainerSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    // Content-only: the card / title / collapse chrome comes from the shared
    // CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("软件有问题、或想改它？点下面的按钮会在终端打开一个 Claude 会话，工作目录就是这个软件的代码仓库——直接用中文告诉它要修什么。",
                   "Something broken, or want to change this software? The button below opens a Claude session in a terminal whose working directory is this software's own code repo — just tell it what to fix, in plain language."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            repoPathRow
            sessionIDRow
            Divider()
            launchRow
        }
        .font(.system(size: 12))
        .onAppear { model.loadIfNeeded() }
    }

    private var repoPathRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("代码仓库目录（留空 = 默认）", "Code repo folder (empty = default)"))
                .font(.system(size: 12, weight: .medium))
            HStack(spacing: 8) {
                TextField(MaintainerSettingsModel.defaultRepoPath,
                          text: $model.repoPath)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .onSubmit { model.saveRepoPath() }
                Button(L("保存", "Save")) { model.saveRepoPath() }
                    .controlSize(.small)
            }
            Text(L("默认就是本软件自己的仓库（灰字显示的路径）；只有开发者克隆到别处时才需要改。",
                   "The default is this software's own repo (the greyed-out path); only developers with a clone elsewhere need to change it."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.repoPathNote.isEmpty {
                Text(model.repoPathNote)
                    .font(.system(size: 10))
                    .foregroundColor(model.repoPathNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var sessionIDRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("会话 ID（可选，留空 = 每次全新会话）",
                   "Session id (optional; empty = a fresh session each time)"))
                .font(.system(size: 12, weight: .medium))
            HStack(spacing: 8) {
                TextField(L("例：6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                            "e.g. 6f9619ff-8b86-d011-b42d-00cf4fc964ff"),
                          text: $model.sessionID)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .onSubmit { model.saveSessionID() }
                Button(L("保存", "Save")) { model.saveSessionID() }
                    .controlSize(.small)
            }
            Text(L("填上后按钮会用 --resume 回到同一个会话——适合把一次没修完的问题接着聊。",
                   "With an id set, the button resumes that same session via --resume — handy for continuing a fix that didn't finish in one sitting."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.sessionNote.isEmpty {
                Text(model.sessionNote)
                    .font(.system(size: 10))
                    .foregroundColor(model.sessionNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var launchRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Button(L("在终端打开维护会话", "Open a maintenance session in the terminal")) {
                    model.openSession()
                }
                .disabled(!model.repoPathExists)
                if !model.repoPathExists {
                    Text(L("路径不存在", "Path doesn't exist"))
                        .font(.system(size: 10))
                        .foregroundColor(.orange)
                }
            }
            Text(L("会在 \(TerminalLauncher.preferred.displayName) 中打开（终端应用在「通用」里换）。",
                   "Opens in \(TerminalLauncher.preferred.displayName) (change the terminal app under General)."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.launchNote.isEmpty {
                Text(model.launchNote)
                    .font(.system(size: 10))
                    .foregroundColor(model.launchNoteIsError ? .orange : .green)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
