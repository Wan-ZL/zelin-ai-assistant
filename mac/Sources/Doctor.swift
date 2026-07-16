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

    /// Render the repo plist template (same 4 placeholder substitutions as
    /// install.sh render_launchd_plist, same order) and launchctl load it.
    /// Blocking — call from a background queue only.
    nonisolated static func install(label: String) -> (Bool, String) {
        let root = AppPaths.stateRoot
        let template = root + "/act/launchd/\(label).plist"
        guard var text = try? String(contentsOfFile: template, encoding: .utf8) else {
            return (false, L("找不到模板 \(template)——repo 不完整？",
                             "Template missing: \(template) — incomplete repo?"))
        }
        let py = RuntimePython.resolve()
        let pyDir = (py as NSString).deletingLastPathComponent
        let home = NSHomeDirectory()
        text = text
            .replacingOccurrences(of: "/Users/YOURUSERNAME/miniconda3/bin/python3", with: py)
            .replacingOccurrences(of: "/Users/YOURUSERNAME/Projects/zelin-ai-assistant", with: root)
            .replacingOccurrences(of: "/Users/YOURUSERNAME/miniconda3/bin", with: pyDir)
            .replacingOccurrences(of: "/Users/YOURUSERNAME", with: home)
        let dest = plistDest(label)
        do {
            try FileManager.default.createDirectory(
                atPath: (dest as NSString).deletingLastPathComponent,
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
        case "cron_missing":
            return L("定时任务没有安装——屏幕记录不会变成笔记和卡片",
                     "The scheduled jobs are not installed — screen captures never become notes or cards")
        case "cron_fda_blocked":
            return L("定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失",
                     "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost")
        case "dashboard_stale":
            return L("后台服务停止更新数据——看板显示的是旧内容",
                     "The background service stopped updating data — the board shows old content")
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
        case "claude_cli_outdated":
            // the doctor row on the diagnostics page names the two binaries
            // and the fix — deep-link there (same rationale as cron_missing)
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
