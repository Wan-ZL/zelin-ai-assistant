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

    // dismissal: signature → epoch seconds dismissed. Mirrors the board's
    // hiddenOnce/hiddenSticky idiom (UserDefaults, survives relaunch).
    private let dismissKey = "dismissedDiagnostics"
    // warm-up debounce: signature → epoch first observed. vault_empty only
    // alarms once the empty state has persisted ~one ingest cycle.
    private let firstSeenKey = "diagnosticsFirstSeen"
    private static let warmupSeconds: TimeInterval = 35 * 60   // 对齐 install.sh */30
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

        // intent signals (all from already-read data): a path is only eligible
        // for a card when the user INTENDED it (§3.6 anti-nag).
        let slackNonEmpty = SecretsIO.hasSecret(SecretsIO.slackFile)
        let slackStarted = FileManager.default.fileExists(
            atPath: SecretsIO.path(SecretsIO.slackFile))   // 存在但可能空 = 已开始配
        let gmailNonEmpty = SecretsIO.hasSecret(SecretsIO.gmailFile)

        var out: [DiagnosticCard] = []
        var liveSignatures = Set<String>()

        // --- obsidian / screenpipe (intent: recording on) ---
        if recOn, let ob = health["obsidian"], let reason = ob.skipReason {
            if let card = obsidianCard(reason: reason, entry: ob, rec: rec) {
                liveSignatures.insert(card.signature)
                if !isDebounced(card) { out.append(card) }
            }
        }

        // --- gmail (intent: credential non-empty) ---
        if gmailNonEmpty, let gm = health["gmail"], let reason = gm.skipReason,
           ["auth_failed", "no_address", "invalid_credentials", "connect_failed"]
               .contains(reason) {
            out.append(DiagnosticCard(
                id: "diag.gmail", signature: "gmail:" + reason, path: .gmail,
                title: L("Gmail 雷达连不上", "The Gmail radar can't connect"),
                detail: L("存了应用密码，但雷达没法用它登录——多半是密码过期或邮箱地址没填对。",
                          "An app password is saved but the radar can't log in — the password likely expired or the address is off."),
                actionLabel: L("检查 Gmail 设置", "Check Gmail settings"),
                action: .openCredentials,
                lastAttempt: gm.lastAttempt, lastOK: gm.lastOK))
            liveSignatures.insert("gmail:" + reason)
        }

        // --- slack (intent: credential non-empty; mcp fallback: file exists) ---
        if let sl = health["slack"], let reason = sl.skipReason {
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

    /// vault_empty warm-up: suppress a card until its state has persisted
    /// ~one ingest cycle. Only vault_empty debounces (the fresh-setup false
    /// alarm); everything else surfaces immediately.
    private func isDebounced(_ card: DiagnosticCard) -> Bool {
        guard card.signature.hasPrefix("screenpipe:vault_empty") else { return false }
        var seen = UserDefaults.standard.dictionary(forKey: firstSeenKey) as? [String: Double] ?? [:]
        let now = Date().timeIntervalSince1970
        if let first = seen[card.signature] {
            return (now - first) < Self.warmupSeconds
        }
        seen[card.signature] = now
        UserDefaults.standard.set(seen, forKey: firstSeenKey)
        return true   // first sight — wait one cycle before alarming
    }

    private func pruneFirstSeen(keeping live: Set<String>) {
        let seen = UserDefaults.standard.dictionary(forKey: firstSeenKey) as? [String: Double] ?? [:]
        let kept = seen.filter { live.contains($0.key) }
        if kept.count != seen.count {
            UserDefaults.standard.set(kept, forKey: firstSeenKey)
        }
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

/// The strip inserted after PipelineHealthBanner in both the popover
/// (DashboardView) and the kanban header. Renders nothing when there are no
/// unhealthy INTENDED paths (the fresh-user default).
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
