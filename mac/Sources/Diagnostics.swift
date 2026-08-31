// Diagnostics.swift — v0.19.0 板级诊断卡：把静默的 ingest 失败变成看得见、点得动的卡。
//
// DESIGN LAW: 每张卡必须 (1) 用大白话说清哪条路断了，(2) 给一个直达修复的主
// 按钮（凌晨一点也能用）。数据全在 Swift 侧合成——读 state/radar_health.json
// （radar 写的 per-source 健康）+ 已有的 app context（录制模式 / 屏幕 TCC / 引擎
// 存活 / 凭证文件），不新增 dashboard.json partition，不新增导航。
//
// ANTI-NAG（防 manager-pack 反向复现）：板上只显示"用户 INTENDED 的路径在静默
// 失败"。从未配过的可选集成不上板。fresh user（录制关 + 无凭证）看到 0 张卡。
// 每 path 至多一卡；可 dismiss；修好即不再产出；signature 变 / 成功过一次 /
// 满 7 天才会重现。DiagnosticCardView 一律 colocate 在此文件（不进 Cards.swift）。

import AppKit
import SwiftUI
import Foundation

// MARK: - model

/// The ingest path a card is about (one card per path, max).
enum IngestPath: String {
    case screenpipe      // Obsidian raw notes from screen capture (radar.py)
    case gmail
    case slack
}

/// The PRIMARY next-action a card routes to — all reuse existing navigation
/// (MainNav.section / pendingAnchor) or existing RecordingController controls.
enum DiagAction {
    case restartEngine        // 录制引擎死了 → 原地重启
    case grantScreen          // 屏幕录制 TCC 被收回 → 去系统设置授权
    case openCredentials      // 凭证/API key → 设置页 credentials 锚点
    case openDeps             // 链路报错 → 依赖检查页
    case openVaultSetting     // 没设 Obsidian 目录 → 设置页
    case reinstallAgent(String)  // §48.6 源开着但 plist 缺失 → 原地重装调度

    /// ``app`` non-nil (popover context) also brings the main window forward;
    /// in the kanban (already the main window) it is passed too and is a no-op
    /// beyond focusing.
    @MainActor func perform(app: AppDelegate?) {
        switch self {
        case .restartEngine:
            RecordingController.shared.restartEngine()
            return
        case .grantScreen:
            RecordingController.openScreenRecordingSettings()
            return
        case .reinstallAgent(let label):
            // 与设置面板「重新安装」同一条路（LaunchAgents.install 渲染 +
            // load）。结果必须回执给 DiagnosticsModel：plist 写成但 launchctl
            // load 失败时雷达照样死，丢弃结果 = 卡片消失 + 永远没人再响。
            // 成功则下一个 5s tick 卡片自然消失。
            DispatchQueue.global(qos: .userInitiated).async {
                let (ok, msg) = LaunchAgents.install(label: label)
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        DiagnosticsModel.shared.noteAgentRepair(
                            label: label, ok: ok, message: msg)
                    }
                }
            }
            return
        case .openCredentials:
            MainNav.shared.pendingAnchor = "credentials"
            MainNav.shared.section = .settings
        case .openDeps:
            MainNav.shared.section = .deps
        case .openVaultSetting:
            MainNav.shared.section = .settings
        }
        app?.openMainWindow(nil)   // page switches are only visible in the window
    }
}

/// One synthesized diagnostic card. ``signature`` = "<path>:<reasonCode>" is
/// the dismissal identity — a different reason is a new card, so a fix that
/// swaps one failure for another re-alerts.
struct DiagnosticCard: Identifiable {
    let id: String            // "diag.<path>"
    let signature: String     // "<path>:<reasonCode>"
    let path: IngestPath
    let title: String         // plain-language problem
    let detail: String        // one honest line of context
    let actionLabel: String   // the PRIMARY button
    let action: DiagAction
    let lastAttempt: Date?
    // dismissal bookkeeping (not shown): a success AFTER dismissal re-alerts.
    let lastOK: Date?
}

@MainActor
final class DiagnosticsModel: ObservableObject {
    static let shared = DiagnosticsModel()

    @Published private(set) var cards: [DiagnosticCard] = []

    // §48.6 卡上重装的失败回执（label → 错误信息）。plist 可能写成了但
    // launchctl load 失败——只看「plist 存在」会把失败吞成成功；有回执时
    // 卡留着并亮失败原因。**持久化**（RepairReceiptStore / UserDefaults）：
    // 只放内存的话 App 重启即清空，plist 又在 → 卡永久消失且 health 已清、
    // liveness 没有基线——重启后必须仍进「失败态复核」路径，launchctl
    // 确认真跑起来才出账。成功回执/后台复核发现已 loaded 时出账。
    private let repairReceipts = RepairReceiptStore()

    func noteAgentRepair(label: String, ok: Bool, message: String) {
        if ok {
            repairReceipts.clear(label: label)
        } else {
            repairReceipts.recordFailure(label: label, message: message)
        }
        rebuild()
    }

    /// 失败态的后台复核：设置面板「重新安装」等旁路把 agent 装好后自动
    /// 出账（launchctl print 只在失败态才跑，不进平时 5s tick 的成本）。
    fileprivate func revalidateRepairFailure(label: String) {
        DispatchQueue.global(qos: .utility).async { [repairReceipts] in
            guard LaunchAgents.isLoaded(label: label) else { return }
            repairReceipts.clear(label: label)
        }
    }

    // dismissal: signature → epoch seconds dismissed. Mirrors the board's
    // hiddenOnce/hiddenSticky idiom (UserDefaults, survives relaunch).
    private let dismissKey = "dismissedDiagnostics"
    // warm-up debounce: signature → epoch first observed. vault_empty only
    // alarms once the empty state has persisted ~one ingest cycle.
    private let firstSeenKey = "diagnosticsFirstSeen"
    private static let warmupSeconds: TimeInterval = 35 * 60   // 对齐 install.sh */30
    private static let agentMissingWarmupSeconds: TimeInterval = 120   // §48.6 闪卡防抖
    private static let reappearAfter: TimeInterval = 7 * 86_400

    private init() {}

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]   // "yyyy-MM-ddTHH:mm:ssZ"
        return f
    }()

    private struct HealthEntry {
        let hasData: Bool
        let lastOK: Date?
        let skipReason: String?
        let lastAttempt: Date?
    }

    /// Rebuild the strip. Cheap: a tiny JSON read + a few stat calls + cached
    /// RecordingController @Published values (the same 5 s tick that drives
    /// DashboardStore.reload() already refreshed those). Runs on the main
    /// actor — never spawns pgrep/CGPreflight here (uses cached liveness).
    func rebuild() {
        let health = Self.readHealth()
        let rec = RecordingController.shared
        let recOn = rec.mode != "off"

        // intent signals: a path is only eligible for a card when the user
        // INTENDED it (§3.6 anti-nag). §48 起 intent 的真源在 Python
        // （act/lib/sources.py），经 dashboard 的 radar_sources 投影读到——
        // 不再猜「凭证文件非空」。投影缺失（旧 actd payload）时回退老判据。
        // 该不该出卡的判断全在 DiagnosticsRules（LogicTests 钉住的纯逻辑）。
        let projected = Self.readRadarSources()
        let slackNonEmpty = SecretsIO.hasSecret(SecretsIO.slackFile)
        let slackStarted = FileManager.default.fileExists(
            atPath: SecretsIO.path(SecretsIO.slackFile))   // 存在但可能空 = 已开始配
        // setup 类卡的意愿信号：用户碰过开关（override 键存在，开或关都算）
        // 或凭证文件已存在——「enabled 默认 true」本身不算 intent（§48.4）。
        let gmailSwitchTouched = SettingsIO.readOverrides()["gmail_enabled"] != nil
        let gmailCredFileExists = FileManager.default.fileExists(
            atPath: SecretsIO.path(SecretsIO.gmailFile))

        var out: [DiagnosticCard] = []
        var liveSignatures = Set<String>()

        // --- obsidian / screenpipe (intent: recording on) ---
        if recOn, let ob = health["obsidian"], let reason = ob.skipReason {
            if let card = obsidianCard(reason: reason, entry: ob, rec: rec) {
                liveSignatures.insert(card.signature)
                if !isDebounced(card) { out.append(card) }
            }
        }

        // --- §48.6 雷达调度未安装（源开着但 launchd plist 缺失） ---
        // 先于凭证卡构建：调度都不在，skip_reason 必然陈旧——同 path 冲突时
        // agent_missing 赢（凭证卡经 schedulerMissing flag 让位）。
        // 典型路径：关着时 .pkg 升级把 plist 退役 → 用户重新打开开关（功能
        // 开关面板不装 plist）→ 配置 on 但没人再装，雷达永久静默且因 health
        // 条目已被清、连 liveness 死亡告警都没有基线可响。修复动作与设置
        // 面板「重新安装」同路。只认真实投影（旧 payload 不出卡）。
        // 撤卡判据：plist 存在**且**没有失败回执（RepairReceiptStore，
        // UserDefaults 持久化——App 重启不丢）——重装可能写成 plist 但
        // launchctl load 失败，那时卡留着并亮出失败原因；失败态每 tick
        // 后台复核 launchctl（设置面板等旁路修好后自动出账）。
        let agentBySource: [(String, IngestPath, String)] = [
            ("gmail", .gmail, GmailSettingsModel.agentLabel),
            ("slack", .slack, SlackSettingsModel.agentLabel),
        ]
        var schedulerMissingBySource: [String: Bool] = [:]
        for (src, path, label) in agentBySource {
            let plistExists = FileManager.default.fileExists(
                atPath: LaunchAgents.plistDest(label))
            let failMsg = repairReceipts.failure(label: label)
            if failMsg != nil { revalidateRepairFailure(label: label) }
            let missing = DiagnosticsRules.schedulerMissing(
                projected: projected[src], plistExists: plistExists,
                repairFailed: failMsg != nil)
            schedulerMissingBySource[src] = missing
            guard missing else { continue }
            let entry = health[src]
            let card = DiagnosticCard(
                id: "diag.\(src)", signature: "\(src):agent_missing", path: path,
                title: src == "gmail"
                    ? L("Gmail 雷达开着，但后台调度没装上", "The Gmail radar is on but its scheduler isn't installed")
                    : L("Slack 雷达开着，但后台调度没装上", "The Slack radar is on but its scheduler isn't installed"),
                detail: failMsg.map {
                    L("上次重装失败：", "The last reinstall failed: ") + $0
                } ?? L("开关是开的，但 launchd 里没有它的调度任务（多半是关着时升级被卸载了）——点一下原地装回去。",
                       "The switch is on, but launchd has no job for it (likely removed by an upgrade while it was off) — one click reinstalls it in place."),
                actionLabel: failMsg == nil
                    ? L("重装后台调度", "Reinstall the scheduler")
                    : L("再试一次", "Try again"),
                action: .reinstallAgent(label),
                lastAttempt: entry?.lastAttempt, lastOK: entry?.lastOK)
            liveSignatures.insert(card.signature)
            if !isDebounced(card) { out.append(card) }
        }

        // --- gmail (intent: §48 radar_sources.gmail.enabled + 意愿信号) ---
        // 告警资格 = DiagnosticsRules.gmailCardEligible：源开着 + skip_reason
        // 非空，setup 类另需真实意愿（碰过开关/凭证文件存在）。手写的 reason
        // 白名单退役——Python 已不再产出 `disabled`（关掉的源条目整个消失），
        // 规则里只把升级瞬间可能残留的旧 `disabled` 记录排除掉。
        if let gm = health["gmail"],
           DiagnosticsRules.gmailCardEligible(
               reason: gm.skipReason, projected: projected["gmail"],
               legacyCredentialNonEmpty: SecretsIO.hasSecret(SecretsIO.gmailFile),
               switchTouched: gmailSwitchTouched,
               credentialFileExists: gmailCredFileExists,
               schedulerMissing: schedulerMissingBySource["gmail"] ?? false),
           let reason = gm.skipReason {
            // 文案按 failure 形态分组（DiagnosticsRules.gmailCardKind）——
            // command 类（§14bis 抓取命令）跟应用密码无关，说成密码问题是
            // 把用户往错误的修复路上引。
            let title: String, detail: String
            switch DiagnosticsRules.gmailCardKind(reason: reason) {
            case .setup:
                title = L("Gmail 雷达开着但还没配好", "The Gmail radar is on but not set up")
                detail = L("开关开着，但缺应用密码或邮箱地址——补上它雷达才能开始扫。",
                           "The switch is on but the app password or address is missing — add it so the radar can scan.")
            case .command:
                title = L("Gmail 抓取命令没跑成", "The Gmail fetch command is failing")
                detail = L("邮件走的是你的自定义抓取命令（gmail_fetch_command），它在报错或输出不是雷达能读的格式——去 Gmail 设置里检查那条命令。",
                           "Mail comes via your custom fetch command (gmail_fetch_command), and it's erroring or emitting output the radar can't read — check that command in Gmail settings.")
            case .connection:
                title = L("Gmail 雷达连不上", "The Gmail radar can't connect")
                detail = L("存了应用密码，但雷达没法用它登录——多半是密码过期或邮箱地址没填对。",
                           "An app password is saved but the radar can't log in — the password likely expired or the address is off.")
            }
            out.append(DiagnosticCard(
                id: "diag.gmail", signature: "gmail:" + reason, path: .gmail,
                title: title, detail: detail,
                actionLabel: L("检查 Gmail 设置", "Check Gmail settings"),
                action: .openCredentials,
                lastAttempt: gm.lastAttempt, lastOK: gm.lastOK))
            liveSignatures.insert("gmail:" + reason)
        }

        // --- slack (intent: credential non-empty; mcp fallback: file exists) ---
        // 调度缺失时凭证卡让位（同 gmail：skip_reason 必然陈旧，先修调度）
        if !(schedulerMissingBySource["slack"] ?? false),
           let sl = health["slack"], let reason = sl.skipReason {
            if reason == "connect_failed" && slackNonEmpty {
                out.append(DiagnosticCard(
                    id: "diag.slack", signature: "slack:connect_failed", path: .slack,
                    title: L("Slack token 无效", "The Slack token is invalid"),
                    detail: L("存了 token，但 Slack 拒绝了它——重新复制 User OAuth Token（xoxp- 开头）再试。",
                              "A token is saved but Slack rejected it — copy the User OAuth Token (starts with xoxp-) again."),
                    actionLabel: L("检查 Slack 设置", "Check Slack settings"),
                    action: .openCredentials,
                    lastAttempt: sl.lastAttempt, lastOK: sl.lastOK))
                liveSignatures.insert("slack:connect_failed")
            } else if reason == "mcp_not_configured" && slackStarted {
                out.append(DiagnosticCard(
                    id: "diag.slack", signature: "slack:mcp_not_configured", path: .slack,
                    title: L("Slack 兜底没连上", "Slack fallback isn't connected"),
                    detail: L("还没存 token，兜底走 claude 的 Slack MCP——但 CLI 里没配这个 MCP。存个 token 或加上 Slack MCP 都行。",
                              "No token yet, so the fallback uses claude's Slack MCP — but it isn't registered in the CLI. Save a token or add the Slack MCP."),
                    actionLabel: L("连接 Slack", "Connect Slack"),
                    action: .openCredentials,
                    lastAttempt: sl.lastAttempt, lastOK: sl.lastOK))
                liveSignatures.insert("slack:mcp_not_configured")
            }
        }

        pruneFirstSeen(keeping: liveSignatures)
        cards = out.filter { !isDismissed($0) }
    }

    /// obsidian skip_reason → a card, refined by app context. Returns nil for
    /// reasons that shouldn't surface a card (e.g. "disabled").
    private func obsidianCard(reason: String, entry: HealthEntry,
                              rec: RecordingController) -> DiagnosticCard? {
        func card(_ sig: String, _ title: String, _ detail: String,
                  _ label: String, _ action: DiagAction) -> DiagnosticCard {
            DiagnosticCard(
                id: "diag.screenpipe", signature: "screenpipe:" + sig,
                path: .screenpipe, title: title, detail: detail,
                actionLabel: label, action: action,
                lastAttempt: entry.lastAttempt, lastOK: entry.lastOK)
        }
        switch reason {
        case "vault_empty":
            if !rec.engineRunning {
                return card("vault_empty.engine",
                    L("录制开着，但没在生成笔记", "Recording is on but no notes are being made"),
                    L("录制引擎没在跑，屏幕内容没被抓下来，也就没有笔记进 vault。原地重启引擎试试。",
                      "The capture engine isn't running, so nothing is captured and no notes reach the vault. Restart it in place."),
                    L("重启录制引擎", "Restart the engine"), .restartEngine)
            }
            if rec.tccLost {
                return card("vault_empty.tcc",
                    L("屏幕录制权限被收回了", "Screen Recording permission was revoked"),
                    L("引擎在跑，但 macOS 收回了「屏幕录制」授权（系统更新/重装会静默失效）——录不到任何东西。",
                      "The engine runs, but macOS revoked Screen Recording (an OS update/reinstall silently drops it) — nothing gets captured."),
                    L("去授权屏幕录制", "Grant Screen Recording"), .grantScreen)
            }
            return card("vault_empty.other",
                L("录制开着，但 vault 里没有新笔记", "Recording is on but no new notes appear"),
                L("屏幕→笔记这条链有一环没通（导出/清洗/ingest）。过一遍依赖检查能定位到具体哪一步。",
                  "A step in the screen→note chain isn't firing (export/cleanup/ingest). The dependency check pinpoints which one."),
                L("打开依赖检查", "Open Dependencies"), .openDeps)
        case "no_api_key":
            return card("no_api_key",
                L("定时任务没有 API key", "The scheduled job has no API key"),
                L("截图能录，但把截图变成笔记要调用 claude，而定时任务读不到 Anthropic API key。",
                  "Capture works, but turning captures into notes calls claude — and the scheduled job can't read an Anthropic API key."),
                L("填入 Anthropic API Key", "Enter the Anthropic API Key"), .openCredentials)
        case "extract_failed":
            return card("extract_failed",
                L("截图→笔记这条链在报错", "The capture→note chain is erroring"),
                L("有 API key，但 claude 处理笔记时失败了（模型报错/超时/输出无法解析）。依赖检查里有完整日志。",
                  "A key exists, but claude failed while processing a note (error/timeout/unparseable output). Full logs are in the dependency check."),
                L("打开依赖检查", "Open Dependencies"), .openDeps)
        case "vault_missing":
            return card("vault_missing",
                L("还没指定 Obsidian 目录", "No Obsidian folder is set"),
                L("录制开着，但没告诉助手笔记该放哪个 vault 目录——先指定它，链路才能落地。",
                  "Recording is on but no vault folder is set for the notes — point to one so the pipeline has somewhere to land."),
                L("指定 Obsidian 目录", "Set the Obsidian folder"), .openVaultSetting)
        default:
            return nil   // "disabled" etc. — not a board card
        }
    }

    // MARK: dismissal + warm-up debounce

    func dismiss(_ card: DiagnosticCard) {
        var d = UserDefaults.standard.dictionary(forKey: dismissKey) as? [String: Double] ?? [:]
        d[card.signature] = Date().timeIntervalSince1970
        UserDefaults.standard.set(d, forKey: dismissKey)
        Analytics.log("diag_card", fields: ["sig": card.signature, "act": "dismiss"])
        rebuild()
    }

    /// Dismissed AND still valid: same signature, no success since dismissal,
    /// within the 7-day re-appear window.
    private func isDismissed(_ card: DiagnosticCard) -> Bool {
        let d = UserDefaults.standard.dictionary(forKey: dismissKey) as? [String: Double] ?? [:]
        guard let ts = d[card.signature] else { return false }
        let dismissedAt = Date(timeIntervalSince1970: ts)
        if Date().timeIntervalSince(dismissedAt) > Self.reappearAfter { return false }
        if let ok = card.lastOK, ok > dismissedAt { return false }   // recovered, then broke again
        return true
    }

    /// warm-up: suppress a card until its state has persisted a while.
    /// vault_empty waits ~one ingest cycle (the fresh-setup false alarm);
    /// agent_missing waits ~2 min（开关切换的瞬间投影落后一个 actd pass、
    /// 异步安装还在跑——闪卡防抖）; everything else surfaces immediately.
    private func isDebounced(_ card: DiagnosticCard) -> Bool {
        let warmup: TimeInterval
        if card.signature.hasPrefix("screenpipe:vault_empty") {
            warmup = Self.warmupSeconds
        } else if card.signature.hasSuffix(":agent_missing") {
            warmup = Self.agentMissingWarmupSeconds
        } else {
            return false
        }
        var seen = UserDefaults.standard.dictionary(forKey: firstSeenKey) as? [String: Double] ?? [:]
        let now = Date().timeIntervalSince1970
        if let first = seen[card.signature] {
            return (now - first) < warmup
        }
        seen[card.signature] = now
        UserDefaults.standard.set(seen, forKey: firstSeenKey)
        return true   // first sight — wait out the warm-up before alarming
    }

    private func pruneFirstSeen(keeping live: Set<String>) {
        let seen = UserDefaults.standard.dictionary(forKey: firstSeenKey) as? [String: Double] ?? [:]
        let kept = seen.filter { live.contains($0.key) }
        if kept.count != seen.count {
            UserDefaults.standard.set(kept, forKey: firstSeenKey)
        }
    }

    // MARK: dashboard.json radar_sources projection (§48, tolerant)

    /// state/dashboard.json 顶层 `radar_sources` map（actd 投影的源开关
    /// intent + 健康摘要）。走 Contract.swift 的 `RadarSourceHealth` 解码
    /// ——与 Store 同一套 wire 类型，不再维护第二条裸 JSONSerialization
    /// 读法。缺失/坏文件/坏 map → [:]（调用方回退老 intent 判据）。
    nonisolated static func readRadarSources() -> [String: RadarSourceHealth] {
        struct Projection: Decodable {   // 只解顶层这一个键，别的分区不碰
            let radar_sources: [String: RadarSourceHealth]?
        }
        let path = AppPaths.stateRoot + "/state/dashboard.json"
        guard let data = FileManager.default.contents(atPath: path),
              let proj = try? JSONDecoder().decode(Projection.self, from: data)
        else { return [:] }
        return proj.radar_sources ?? [:]
    }

    /// §48.1 投影新鲜度：dashboard.json 的 mtime 是否不早于
    /// settings_overrides.json 的 mtime。投影每 pass 现读 config 重建——
    /// mtime 更新的投影必然已吸收 override；反之（用户刚写 override、actd
    /// 还没跑 / 已停摆）投影是旧世界的快照，设置面板的有效值判定要回退
    /// override 判据（`DiagnosticsRules.effectiveSourceEnabled` 的
    /// `projectionFresh` 入参）。dashboard 缺失 → false（没有投影可信）；
    /// overrides 缺失 → true（用户从没写过，投影不可能落后于它）。
    nonisolated static func projectionFresh() -> Bool {
        func mtime(_ path: String) -> Date? {
            (try? FileManager.default.attributesOfItem(atPath: path))?[
                .modificationDate] as? Date
        }
        guard let dash = mtime(AppPaths.stateRoot + "/state/dashboard.json")
        else { return false }
        guard let ov = mtime(AppPaths.settingsOverridesPath) else { return true }
        return dash >= ov
    }

    // MARK: radar_health.json (tolerant — never crashes the tick)

    private static func readHealth() -> [String: HealthEntry] {
        let path = AppPaths.stateRoot + "/state/radar_health.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return [:] }
        var out: [String: HealthEntry] = [:]
        for (key, val) in obj {
            let d = val as? [String: Any]
            out[key] = HealthEntry(
                hasData: d != nil,
                lastOK: (d?["last_ok"] as? String).flatMap { iso.date(from: $0) },
                skipReason: (d?["skip_reason"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                lastAttempt: (d?["last_attempt"] as? String).flatMap { iso.date(from: $0) })
        }
        return out
    }
}

// MARK: - view

/// The strip inserted after PipelineHealthBanner in the kanban header (was
/// also mirrored in the popover until its v0.48.x removal). Renders nothing
/// when there are no unhealthy INTENDED paths (the fresh-user default).
struct DiagnosticsStrip: View {
    @ObservedObject private var model = DiagnosticsModel.shared
    @ObservedObject private var i18n = LanguageStore.shared
    unowned let app: AppDelegate
    var horizontalPadding: CGFloat = 0
    var bottomPadding: CGFloat = 0

    var body: some View {
        if model.cards.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(model.cards) { card in
                    DiagnosticCardView(card: card, app: app)
                }
            }
            .padding(.horizontal, horizontalPadding)
            .padding(.bottom, bottomPadding)
        }
    }
}

/// One diagnostic card: plain-language problem + ONE primary fix button, with
/// a dismiss affordance. Styled like PipelineHealthBanner (calm, orange).
struct DiagnosticCardView: View {
    let card: DiagnosticCard
    unowned let app: AppDelegate
    @ObservedObject private var i18n = LanguageStore.shared
    private let tint = Color.orange

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 10))
                    .foregroundColor(tint)
                Text(card.title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.primary.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button {
                    DiagnosticsModel.shared.dismiss(card)
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("忽略这张卡（问题还在会重新出现）", "Dismiss (returns if still broken)"))
            }
            Text(card.detail)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button {
                    Analytics.log("diag_card",
                                  fields: ["sig": card.signature, "act": "open"])
                    card.action.perform(app: app)
                } label: {
                    Text(card.actionLabel)
                }
                .font(.system(size: 11))
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                if let attempt = card.lastAttempt,
                   let rel = RelativeTime.since(Self.iso.string(from: attempt)) {
                    Text(L("上次尝试 ", "last tried ") + rel)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
}
