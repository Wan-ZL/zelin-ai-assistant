// Freshness.swift — FreshnessLabel（dashboard.json 新鲜度小组件）
// + PipelineHealthBanner / PipelineEmptyStateView（P1-4/P1-5 健康横幅与恢复路径）
//
// 与 popover footer（DashboardView.freshnessLabel，只读参考）同语义：
// generated_at 距今 >90s → 橙色「actd 可能未运行」警告；新鲜 → 灰色相对时间；
// generatedAt == nil → 整个隐藏（popover 自己另有 lastRefresh 降级，这里不需要）。
// TimelineView 每 15s 重算，文件没变（store 不 publish）时标签也保持活。

import AppKit
import SwiftUI
import Foundation

struct FreshnessLabel: View {
    let generatedAt: Date?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 15)) { context in
            label(now: context.date)
        }
    }

    @ViewBuilder private func label(now: Date) -> some View {
        if let d = generatedAt {
            let age = now.timeIntervalSince(d)
            if age > 90 {
                Text(L("数据生成于 \(max(1, Int(age / 60))) 分钟前，actd 可能未运行",
                       "Data generated \(max(1, Int(age / 60))) min ago — actd may be down"))
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
            } else {
                Text(L("数据生成于 ", "Data generated ") + Self.relative(age: age))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
        }
    }

    // mirror of RelativeTime.since (Utils.swift — S5 名下，不动) starting from
    // an already-parsed Date; the fresh branch (≤90s) only ever hits 刚刚/1分钟前.
    private static func relative(age: TimeInterval) -> String {
        if age < 60 { return L("刚刚", "just now") }
        let mins = Int(age / 60)
        if mins < 60 { return L("\(mins)分钟前", "\(mins)m ago") }
        let hours = mins / 60
        if hours < 24 { return L("\(hours)小时前", "\(hours)h ago") }
        let days = hours / 24
        return L("\(days)天前", "\(days)d ago")
    }

    /// ISO8601（含小数秒变体）→ Date? — 调用方（KanbanView header）用它把
    /// store.dashboard?.generated_at 字符串转成本组件要的 Date?。
    static func parseISO(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        return iso.date(from: s) ?? isoFrac.date(from: s)
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}

// MARK: - P1-4/P1-5 shared recovery actions

enum PipelineHealthUI {
    /// Canonical actd (re)start — install.sh's documented remedy for
    /// loaded-but-dead agents; also works right after a fresh install.
    static let startCommand =
        "launchctl unload ~/Library/LaunchAgents/com.zelin.aiassistant.actd.plist 2>/dev/null; "
        + "launchctl load ~/Library/LaunchAgents/com.zelin.aiassistant.actd.plist"

    @MainActor static func revealActdLog() {
        Analytics.log("health_banner", fields: ["action": "log"])
        let p = AppPaths.actdLogPath
        // log not written yet (actd never ran) → reveal state/ instead
        let target = FileManager.default.fileExists(atPath: p)
            ? p : (p as NSString).deletingLastPathComponent
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: target)])
    }

    @MainActor static func openDeps(app: AppDelegate) {
        Analytics.log("health_banner", fields: ["action": "deps"])
        MainNav.shared.section = .deps
        app.openMainWindow(nil)
    }
}

// MARK: - PipelineHealthBanner (P1-4) — slow vs broken, with a way out
//
// Shared by the popover (DashboardView) and the kanban header. Renders
// nothing for .ok; .missing is owned by PipelineEmptyStateView (P1-5) so the
// same message never shows twice.

struct PipelineHealthBanner: View {
    @ObservedObject var store: DashboardStore
    unowned let app: AppDelegate
    var horizontalPadding: CGFloat = 0
    var bottomPadding: CGFloat = 0
    // §25 one-click repair state (shared: popover + kanban banner show the
    // same spinner/result instead of a copy-this-command line).
    @ObservedObject private var repair = PipelineRepair.shared
    @State private var aiFixStatus = ""

    var body: some View {
        switch store.pipelineHealth {
        case .ok, .missing:
            EmptyView()
        case .stale(let mins):
            banner(color: .orange,
                   title: L("数据 \(mins) 分钟没更新——后台服务可能变慢或刚停止",
                            "Data \(mins) min stale — the background service may be slow or just stopped"),
                   reason: L("卡片操作仍会写入队列，后台服务恢复后照常生效。",
                             "Card actions still land in the queue and apply once the service recovers."))
        case .dead(let mins, let why):
            banner(color: .red,
                   title: L("后台服务已停止：数据 \(mins) 分钟没更新",
                            "Background service down: data \(mins) min stale"),
                   reason: why == .radarsAlive
                       ? L("雷达仍在上报——只有主后台服务停了。点「一键修复」原地重启它。",
                           "Radars still report — only the main service is down. \"Fix now\" restarts it in place.")
                       : L("整条链路都没有输出——先点「一键修复」，仍不行就过一遍依赖检查。",
                           "No pipeline output at all — press \"Fix now\" first; if that fails, run the dependency check."))
        }
    }

    private func banner(color: Color, title: String, reason: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 10))
                    .foregroundColor(color)
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.primary.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            Text(reason)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            repairRow
            HStack(spacing: 8) {
                Button(L("查看日志", "View log")) { PipelineHealthUI.revealActdLog() }
                Button(L("依赖检查", "Dependency check")) { PipelineHealthUI.openDeps(app: app) }
                Spacer()
            }
            .font(.system(size: 11))
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .padding(.horizontal, horizontalPadding)
        .padding(.bottom, bottomPadding)
    }

    /// §25: primary = 一键修复 (in-app launchctl, honest verification);
    /// the raw command downgraded to a collapsed fallback + AI escape hatch.
    @ViewBuilder private var repairRow: some View {
        switch repair.phase {
        case .idle:
            HStack(spacing: 8) {
                Button {
                    repair.restartActd()
                } label: {
                    Label(L("一键修复", "Fix now"), systemImage: "wrench.and.screwdriver")
                }
                .font(.system(size: 11))
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                Spacer()
            }
        case .running:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text(L("正在重启后台服务并等待数据更新（最多 15 秒）…",
                       "Restarting the service and waiting for fresh data (up to 15 s)…"))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
        case .success:
            Label(L("已恢复 ✓ 数据重新更新了", "Recovered ✓ data is updating again"),
                  systemImage: "checkmark.circle.fill")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(.green)
        case .failure(let detail):
            VStack(alignment: .leading, spacing: 4) {
                Text(L("自动修复没成功：", "Auto-repair didn't work: ") + detail)
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button(L("再试一次", "Try again")) { repair.restartActd() }
                    if AIFix.enabled {
                        Button(L("让 AI 修", "Fix with AI")) {
                            aiFixStatus = L("正在准备诊断包…", "Preparing the diagnostic bundle…")
                            AIFix.launch(context: L("看板健康横幅：后台服务已停止；一键修复失败：",
                                                    "Board health banner: background service down; one-click repair failed: ")
                                         + detail) { _, msg in aiFixStatus = msg }
                        }
                    }
                    Spacer()
                }
                .font(.system(size: 11))
                .buttonStyle(.bordered)
                .controlSize(.small)
                if !aiFixStatus.isEmpty {
                    Text(aiFixStatus)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
                // fallback for the terminal-comfortable: the raw command stays
                // available but demoted (audit 4.1 — never the primary path)
                CopyPathLine(label: L("手动命令：", "Manual command: "),
                             path: PipelineHealthUI.startCommand)
            }
        }
    }
}

// MARK: - PipelineEmptyStateView (P1-5) — "no dashboard.json" is not a dead end
//
// One copy of the first-launch empty state for the popover AND the kanban:
// what's missing, the canonical start command (click-to-copy), and a button
// into the dependency check where TCC/npx/key blockers are diagnosed.

struct PipelineEmptyStateView: View {
    unowned let app: AppDelegate
    @ObservedObject private var repair = PipelineRepair.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: "hourglass")
                .font(.system(size: 22))
                .foregroundColor(.secondary)
            Text(L("后台服务还没写出数据", "The background service hasn't produced data yet"))
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Text(L("首次安装或服务未启动时会这样。点「启动后台服务」原地拉起它。",
                   "This happens on a fresh install or when the service isn't running. \"Start service\" launches it in place."))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            switch repair.phase {
            case .running:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(L("正在启动并等待首份数据…", "Starting and waiting for the first data…"))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
            case .failure(let detail):
                Text(L("启动没成功：", "Start didn't work: ") + detail)
                    .font(.system(size: 10))
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                CopyPathLine(label: L("手动命令：", "Manual command: "),
                             path: PipelineHealthUI.startCommand)
            default:
                EmptyView()
            }
            HStack(spacing: 8) {
                Button {
                    repair.restartActd()
                } label: {
                    Label(L("启动后台服务", "Start service"), systemImage: "play.circle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(repair.phase == .running)
                Button {
                    PipelineHealthUI.openDeps(app: app)
                } label: {
                    Label(L("打开依赖检查", "Open dependency check"), systemImage: "checklist")
                }
                .buttonStyle(.bordered)
            }
            .font(.system(size: 11))
            .controlSize(.small)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
