// Doctor.swift — AI Doctor 修复中枢（CONTRACT §25）
//
// 四件套：
//  - LaunchAgents      共享 launchctl 管线（render plist 模板 + load/unload），
//                      从 SettingsIMessage.installAgent 泛化而来（同 4 个占位符）
//  - FailureCatalog    failure_id -> 人话句子 + 对症动作（Python 侧
//                      act/lib/failures.py 的镜像，tests/test_failures.py 防漂移）
//  - PipelineRepair    「一键修复」状态机：重装 actd agent -> 轮询 dashboard
//                      变新鲜 -> 诚实汇报 已恢复✓ / 还是不行
//  - AIFix             「让 AI 修」：runtime python -m act.ai_fix --open，
//                      生成带脱敏诊断包的 .command 并交给 Terminal 里的 claude

import AppKit
import SwiftUI
import Foundation

// MARK: - Shared launchd plumbing (mirrors install.sh render_launchd_plist)

enum LaunchAgents {
    static let actdLabel = "com.zelin.aiassistant.actd"

    static func plistDest(_ label: String) -> String {
        NSHomeDirectory() + "/Library/LaunchAgents/\(label).plist"
    }

    /// The directory of the claude the LOGIN SHELL resolves — install.sh's
    /// CLAUDE_LOGIN_BIN substitution (rendered FIRST on the plist PATH).
    /// Review MAJOR 2: without this fifth substitution the generic $HOME
    /// replacement left a nonexistent ~/.claude-bin at the head of PATH —
    /// the exact two-installs condition the doctor daemon-claude check
    /// exists for. Fallback mirrors install.sh's default (~/.local/bin).
    nonisolated static func claudeBinDir() -> String {
        let (code, out) = Shell.run("/bin/zsh", ["-lc", "command -v claude"])
        let lines = out.trimmingCharacters(in: .whitespacesAndNewlines)
            .components(separatedBy: "\n")
        if code == 0, let found = lines.last, found.hasPrefix("/") {
            return (found as NSString).deletingLastPathComponent
        }
        return NSHomeDirectory() + "/.local/bin"
    }

    /// Render the repo plist template (same 5 placeholder substitutions as
    /// install.sh render_launchd_plist, same order — the rendered shape is
    /// pinned by tests/test_launchd_render.py) and launchctl load it.
    /// Blocking — call from a background queue only.
    nonisolated static func install(label: String) -> (Bool, String) {
        // PHYSICAL repo path (CONTRACT §55): a convenience symlink rendered
        // into PYTHONPATH / AIASSISTANT_HOME leaves the launchd session
        // TCC-denied on an external volume — the agent then exits 1 on
        // "No module named 'act'" forever (2026-08-31 incident).
        let root = AppPaths.physicalStateRoot
        let template = root + "/act/launchd/\(label).plist"
        guard var text = try? String(contentsOfFile: template, encoding: .utf8) else {
            return (false, L("找不到模板 \(template)——repo 不完整？",
                             "Template missing: \(template) — incomplete repo?"))
        }
        // §55: absolute interpreter that clears BOTH gates — `import yaml` AND
        // launchd viability (TCC is per-binary, so a python this app can read
        // the repo through may still be blind to it once launchd spawns it).
        // Never a bare PATH guess. The note is dropped on purpose: every caller
        // gates on the Bool, and act/doctor.py's "launchd python" row is what
        // reports a still-unverified interpreter to the user.
        let (py, _) = RuntimePython.resolveForLaunchd(repo: root)
        let pyDir = (py as NSString).deletingLastPathComponent
        let home = NSHomeDirectory()
        text = text
            .replacingOccurrences(of: "/Users/YOURUSERNAME/.claude-bin", with: claudeBinDir())
            .replacingOccurrences(of: "/Users/YOURUSERNAME/miniconda3/bin/python3", with: py)
            .replacingOccurrences(of: "/Users/YOURUSERNAME/Projects/zelin-ai-assistant", with: root)
            .replacingOccurrences(of: "/Users/YOURUSERNAME/miniconda3/bin", with: pyDir)
            .replacingOccurrences(of: "/Users/YOURUSERNAME", with: home)
        let dest = plistDest(label)
        do {
            try FileManager.default.createDirectory(
                atPath: (dest as NSString).deletingLastPathComponent,
                withIntermediateDirectories: true)
            // launchd opens StandardOut/ErrorPath BEFORE exec — the templates
            // point them at ~/Library/Logs/zelin-ai-assistant/ (never under
            // the repo: an external-volume repo fails the spawn, EX_CONFIG
            // 78), and the directory must exist or the spawn fails the same way.
            try FileManager.default.createDirectory(
                atPath: home + "/Library/Logs/zelin-ai-assistant",
                withIntermediateDirectories: true)
            try text.write(toFile: dest, atomically: true, encoding: .utf8)
        } catch {
            return (false, L("写入 \(dest) 失败: ", "Failed to write \(dest): ")
                    + error.localizedDescription)
        }
        _ = Shell.run("/bin/launchctl", ["unload", dest])  // ignore "not loaded"
        let (code, out) = Shell.run("/bin/launchctl", ["load", dest])
        if code != 0 {
            return (false, L("launchctl load 失败: ", "launchctl load failed: ") + out)
        }
        return (true, "")
    }

    nonisolated static func isLoaded(label: String) -> Bool {
        Shell.run("/bin/launchctl", ["print", "gui/\(getuid())/\(label)"]).0 == 0
    }
}

// MARK: - Failure catalog (Swift mirror of act/lib/failures.py — §25)

enum FailureCatalog {
    /// Plain-language sentence for a classification id; nil for unknown ids
    /// (caller keeps showing the raw error text — honesty over prettiness).
    static func message(_ id: String?) -> String? {
        switch id ?? "" {
        case "claude_cli_missing":
            return L("claude 命令行没装好——助手无法研究或执行任何卡片",
                     "The claude CLI is not installed — the assistant cannot research or execute any card")
        case "claude_cli_outdated":
            return L("这台机器上有多个 claude 命令，后台服务在用过旧的那个——更新或删掉旧版，再重跑一次安装",
                     "This Mac has more than one claude CLI and the background service is using an outdated copy — update or remove the old one, then re-run the installer")
        case "claude_auth_failed":
            return L("AI 的 API key 无效或过期——去设置页重新粘贴一个",
                     "The AI API key is invalid or expired — re-paste one in Settings")
        case "node_missing":
            return L("缺少 Node.js——录制引擎无法启动",
                     "Node.js is missing — the recording engine cannot start")
        case "engine_dead":
            return L("录制引擎没有在运行——屏幕内容不会被记录",
                     "The recording engine is not running — nothing on screen is being captured")
        case "engine_npm_download":
            // progress, not an error — callers style this row calmly (spinner)
            return L("录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制",
                     "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes")
        case "engine_crashed":
            return L("录制引擎意外停了——点「重启引擎」再试；反复失败就看下面的引擎日志",
                     "The recording engine stopped unexpectedly — click Restart engine; if it keeps happening, check the engine log lines below")
        case "engine_ffmpeg_missing":
            return L("「屏幕+音频」需要 ffmpeg，这台电脑上还没有——装一个（brew install ffmpeg）或切回「仅屏幕」",
                     "Screen + Audio needs ffmpeg, which this Mac does not have — install it (brew install ffmpeg) or switch back to Screen Only")
        case "screen_tcc_lost":
            return L("「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复",
                     "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume")
        case "agent_unloaded":
            return L("一个后台服务没有装载——它负责的工作停了",
                     "A background service is not loaded — its work has stopped")
        case "interpreter_blind":
            return L("后台服务用的那个 Python 读不到项目文件夹（macOS 按程序单独授权，后台任务不继承终端的权限）——重跑一次安装器会换一个能读的",
                     "The Python the background services run cannot read the project folder (macOS grants file access per program, and background jobs do not inherit your terminal's grant) — re-running the installer picks one that can")
        case "cron_missing":
            return L("定时任务没有安装——屏幕记录不会变成笔记和卡片",
                     "The scheduled jobs are not installed — screen captures never become notes or cards")
        case "cron_fda_blocked":
            return L("定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失",
                     "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost")
        case "dashboard_stale":
            return L("后台服务停止更新数据——看板显示的是旧内容",
                     "The background service stopped updating data — the board shows old content")
        // v0.48.4 (§25/§55, mirror only — the menu-bar app is retiring, D3):
        // sentences kept in lockstep with failures.py for the drift guard.
        case "claude_blind":
            return L("后台服务里的 claude 读不到任务目录（macOS 按可执行文件授磁盘权限，launchd 起的 claude 没有「完全磁盘访问」，任务目录又在外置卷或 Documents/Desktop/Downloads 里；claude 自己报的「low max file descriptors」是猜错的）——系统设置 → 隐私与安全性 → 完全磁盘访问，打开 claude 当前版本那一项（~/.local/share/claude/versions/<版本>，claude 每次更新后要再打开一次），或把任务目录搬到启动盘的家目录下；然后把卡「停止 → 退回提案」再批准。doctor 的 `launchd claude` 行能确认",
                     "The claude binary the background service launches cannot read the task folder (macOS grants disk access per executable; launchd-spawned claude has no Full Disk Access and the folder sits on an external volume or in Documents/Desktop/Downloads — claude's own \"low max file descriptors\" guess is wrong) — System Settings → Privacy & Security → Full Disk Access: enable the current claude version (~/.local/share/claude/versions/<v>; repeat after each claude update), or move the task folder under your home on the boot volume; then Stop → Discard & re-propose → approve the card again. Doctor's `launchd claude` row confirms it")
        case "fd_limit":
            return L("后台服务的打开文件数耗尽（launchd 默认软上限 256；错误里写着 EMFILE / too many open files）——重跑一次安装器（bash install.sh）让每个后台服务带上更高的软上限，再重新批准这张卡",
                     "The background service ran out of open files (launchd's default soft limit is 256; the error reads EMFILE / too many open files) — re-run the installer (bash install.sh) so every agent carries a higher soft limit, then approve the card again")
        case "claude_bypass_disclaimer":
            return L("claude 还没在这台机器上接受过「跳过权限确认」的免责声明——在终端里手动跑一次 `claude --dangerously-skip-permissions` 并接受，后台派发才能启动",
                     "claude has not accepted the bypass-permissions disclaimer on this machine yet — run `claude --dangerously-skip-permissions` once in a terminal and accept it, then background dispatch can start")
        case "actd_stalled":
            return L("后台服务进程还活着，但已经停止心跳（不再跑循环）——强制重启它：launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd",
                     "The background service process is alive but its heartbeat stopped (the loop is no longer running) — force-restart it: launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd")
        case "launchd_orphan":
            return L("有已退役的后台服务还在 launchd 里运行（仓库里已没有它的模板）——重跑一次安装器把它卸掉（bash install.sh），或手动 launchctl bootout",
                     "A retired background service is still loaded in launchd (the repo no longer ships its template) — re-run the installer to unload it (bash install.sh), or launchctl bootout it by hand")
        // §59 (D22, mirror only — D3 freeze): the web Settings page owns the fix.
        case "model_unavailable":
            return L("设置里选的模型不可用——派工会全部失败；去设置页「模型」改回「跟随 Claude Code 全局」或换一个 canonical id",
                     "The model chosen in Settings is unavailable — every dispatch will fail; in Settings → Models switch back to \"follow Claude Code\" or pick a canonical id")
        case "config_invalid":
            return L("配置文件写坏了——所有组件都退回默认设置",
                     "The config file is broken — every component fell back to defaults")
        case "network_error":
            return L("网络问题——稍后会自动重试",
                     "Network trouble — it will retry automatically")
        default:
            return nil
        }
    }

    /// The one-click action for a classification id (nil = no in-app action;
    /// the AI-fix escape hatch still applies).
    static func actionLabel(_ id: String?) -> String? {
        switch id ?? "" {
        case "claude_cli_missing", "node_missing": return L("安装页", "Install page")
        case "claude_cli_outdated": return L("去诊断", "Open diagnostics")
        case "claude_auth_failed": return L("去设置", "Open Settings")
        case "engine_dead": return L("去录制页", "Open Recording")
        case "engine_npm_download": return L("看进度", "View progress")
        case "engine_crashed": return L("重启引擎", "Restart engine")
        case "engine_ffmpeg_missing": return L("安装 ffmpeg", "Install ffmpeg")
        case "screen_tcc_lost": return L("去授权", "Grant…")
        case "agent_unloaded", "dashboard_stale": return L("一键修复", "Fix now")
        case "cron_missing": return L("查看修法", "How to fix")
        // 不给「一键修复」：重装 agent 会把同一个瞎解释器再渲一遍，得重跑安装器
        case "interpreter_blind", "claude_blind": return L("去诊断", "Open diagnostics")
        case "cron_fda_blocked": return L("去授权", "Grant…")
        case "config_invalid": return L("显示文件", "Reveal file")
        default: return nil
        }
    }

    /// Perform the action for a failure id. Deep-links reuse the existing
    /// navigation; the launchd cases go through PipelineRepair.
    @MainActor static func perform(_ id: String?) {
        Analytics.log("failure_action", fields: ["id": id ?? "?"])
        let app = NSApp.delegate as? AppDelegate
        switch id ?? "" {
        case "claude_cli_missing":
            NSWorkspace.shared.open(URL(string: "https://claude.com/claude-code")!)
        case "node_missing":
            NSWorkspace.shared.open(URL(string: "https://nodejs.org")!)
        case "claude_auth_failed":
            MainNav.shared.pendingAnchor = "credentials"
            MainNav.shared.section = .settings
            app?.openMainWindow(nil)
        case "engine_dead":
            MainNav.shared.section = .ingest
            app?.openMainWindow(nil)
        case "engine_npm_download":
            // show the live download output — engine.log is all the progress
            // bar there is (honesty over prettiness)
            NSWorkspace.shared.activateFileViewerSelecting(
                [URL(fileURLWithPath: RecordingController.engineLogPath)])
        case "engine_crashed":
            RecordingController.shared.restartEngine()
        case "engine_ffmpeg_missing":
            // same shape as node_missing: point at the authoritative install
            // page (the catalog sentence already names `brew install ffmpeg`)
            NSWorkspace.shared.open(URL(string: "https://ffmpeg.org/download.html")!)
        case "screen_tcc_lost":
            RecordingController.openScreenRecordingSettings()
        case "agent_unloaded", "dashboard_stale":
            PipelineRepair.shared.restartActd()
        case "claude_cli_outdated", "interpreter_blind", "claude_blind":
            // the doctor row on the diagnostics page names the two binaries
            // and the fix — deep-link there (same rationale as cron_missing).
            // interpreter_blind lands here too: its fix is re-running the
            // installer, which the diagnostics page walks through.
            MainNav.shared.section = .deps
            app?.openMainWindow(nil)
        case "cron_missing":
            // the honest fix is install.sh's cron step — the diagnostics page
            // explains it; deep-link there rather than print a terminal command
            MainNav.shared.section = .deps
            app?.openMainWindow(nil)
        case "cron_fda_blocked":
            CronFDA.beginGrant()
        case "config_invalid":
            let p = AppPaths.stateRoot + "/config.yaml"
            let target = FileManager.default.fileExists(atPath: p)
                ? p : AppPaths.stateRoot + "/config.example.yaml"
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: target)])
        default:
            break
        }
    }
}

// MARK: - cron FDA probe reader + guided grant (§25 state/cron_probe.json)

/// Background-safe ISO8601 parse (FreshnessLabel.parseISO is main-actor-bound
/// through its cached formatters; the repair poll runs off-main).
nonisolated func parseISOBackground(_ s: String?) -> Date? {
    guard let s, !s.isEmpty else { return nil }
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f.date(from: s)
}

struct CronProbe {
    let ts: Date?
    let readOK: Bool
    let path: String

    /// nil = no probe data yet (cron chain never ran with the probe armed).
    static func read() -> CronProbe? {
        let p = AppPaths.stateRoot + "/state/cron_probe.json"
        guard let data = FileManager.default.contents(atPath: p),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return nil }
        return CronProbe(
            ts: parseISOBackground(obj["ts"] as? String),
            readOK: (obj["read_ok"] as? Bool) ?? false,
            path: (obj["protected_path"] as? String) ?? "")
    }

    /// Probe fresh (≤2h) and the read failed -> cron is FDA-blocked right now.
    var isBlocked: Bool {
        guard let ts, Date().timeIntervalSince(ts) <= 2 * 3600 else { return false }
        return !readOK
    }
}

enum CronFDA {
    /// The path the user must add in the FDA pane — put it on the clipboard
    /// so the ⌘⇧G sheet is a single paste (clone of the iMessage FDA flow).
    static let cronBinary = "/usr/sbin/cron"

    @MainActor static func beginGrant() {
        Analytics.log("cron_fda_grant")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(cronBinary, forType: .string)
        if let url = URL(string:
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") {
            NSWorkspace.shared.open(url)
        }
    }

    static var grantSteps: String {
        L("点「去授权」会把 \(cronBinary) 复制到剪贴板并打开「完全磁盘访问」面板。然后：点 ➕ → 按 ⌘⇧G → ⌘V 粘贴 → 回车 → 选中 cron → 开启开关。下次定时任务运行（约 30 分钟内）后这一行会自动变绿。",
          "\"Grant…\" copies \(cronBinary) to the clipboard and opens the Full Disk Access pane. Then: click ➕ → press ⌘⇧G → ⌘V to paste → Return → select cron → toggle it on. This row turns green after the next scheduled run (within ~30 min).")
    }
}

// MARK: - one-click actd repair (P0-3 — replaces the copy-a-launchctl-command UX)

@MainActor
final class PipelineRepair: ObservableObject {
    static let shared = PipelineRepair()

    enum Phase: Equatable {
        case idle
        case running          // install + waiting for a fresh dashboard
        case success
        case failure(String)  // honest failure detail (launchctl output etc.)
    }

    @Published var phase: Phase = .idle

    /// Render + reload the actd launchd agent, then poll dashboard.json for a
    /// fresh generated_at (≤90 s) for up to ~15 s. Honest outcome either way.
    func restartActd() {
        guard phase != .running else { return }
        phase = .running
        Analytics.log("pipeline_repair", fields: ["action": "restart_actd"])
        let dashPath = AppPaths.dashboardPath
        DispatchQueue.global(qos: .userInitiated).async {
            let (ok, err) = LaunchAgents.install(label: LaunchAgents.actdLabel)
            var verdict: Phase = ok
                ? .failure(L("后台服务已重启，但数据还没更新——点「让 AI 修」深挖，或查看日志",
                             "Service restarted but data still isn't updating — try \"Fix with AI\" or view the log"))
                : .failure(err)
            if ok {
                // fresh actd writes dashboard.json within its first ~10 s pass
                for _ in 0..<15 {
                    Thread.sleep(forTimeInterval: 1.0)
                    if Self.dashboardFresh(path: dashPath) {
                        verdict = .success
                        break
                    }
                }
            }
            let final = verdict
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.phase = final
                    Analytics.log("pipeline_repair_result", fields: [
                        "ok": final == .success])
                    if final == .success {
                        // let the banner celebrate briefly, then reset
                        DispatchQueue.main.asyncAfter(deadline: .now() + 6) {
                            MainActor.assumeIsolated {
                                if self.phase == .success { self.phase = .idle }
                            }
                        }
                    }
                }
            }
        }
    }

    nonisolated private static func dashboardFresh(path: String) -> Bool {
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let gen = parseISOBackground(obj["generated_at"] as? String)
        else { return false }
        return Date().timeIntervalSince(gen) <= 90
    }
}

// MARK: - Fix with AI (act/ai_fix.py wrapper)

enum AIFix {
    /// config.yaml `doctor.ai_fix_enabled: false` hides the button entirely.
    static var enabled: Bool {
        (SettingsIO.configNestedScalar(block: "doctor", key: "ai_fix_enabled") ?? "true")
            .lowercased() != "false"
    }

    /// Generate the .command (python builds + scrubs the bundle) and open it
    /// in Terminal. `context` = what the user was looking at (error text).
    /// Completion delivers (ok, detail) on the main actor.
    @MainActor static func launch(context: String?,
                                  completion: @escaping @MainActor (Bool, String) -> Void) {
        Analytics.log("ai_fix_launch")
        let root = AppPaths.stateRoot
        let py = RuntimePython.resolve()
        let ctx = context
        DispatchQueue.global(qos: .userInitiated).async {
            var args = ["-m", "act.ai_fix", "--open"]
            var ctxFile: String?
            if let ctx, !ctx.isEmpty {
                let f = NSTemporaryDirectory() + "zelin-ai-fix-context-\(UUID().uuidString).txt"
                if (try? ctx.write(toFile: f, atomically: true, encoding: .utf8)) != nil {
                    args += ["--context-file", f]
                    ctxFile = f
                }
            }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: py)
            p.arguments = args
            p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
            var env = ProcessInfo.processInfo.environment
            env["AIASSISTANT_HOME"] = root
            env["AIASSISTANT_UI_LANG"] = LanguageMirror.current   // §15: python copy matches the app language
            p.environment = env
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            var code: Int32 = 127
            var out = ""
            do {
                try p.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                code = p.terminationStatus
                out = String(data: data, encoding: .utf8) ?? ""
            } catch {
                out = error.localizedDescription
            }
            if let ctxFile { try? FileManager.default.removeItem(atPath: ctxFile) }
            let ok = code == 0
            let detail = ok
                ? L("已在 Terminal 打开修复会话——跟着 AI 走即可",
                    "Repair session opened in Terminal — just follow the AI")
                : String(out.suffix(300))
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    Analytics.log("ai_fix_result", fields: ["ok": ok])
                    completion(ok, detail)
                }
            }
        }
    }
}
