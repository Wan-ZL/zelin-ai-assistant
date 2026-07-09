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

    var body: some View {
        switch store.pipelineHealth {
        case .ok, .missing:
            EmptyView()
        case .stale(let mins):
            banner(color: .orange,
                   title: L("数据 \(mins) 分钟未更新——actd 可能变慢或刚停止",
                            "Data \(mins) min stale — actd may be slow or just stopped"),
                   reason: L("卡片操作仍会写入队列，pipeline 恢复后照常生效。",
                             "Card actions still land in the queue and apply once the pipeline recovers."))
        case .dead(let mins, let why):
            banner(color: .red,
                   title: L("pipeline 已停止：数据 \(mins) 分钟未更新",
                            "Pipeline down: data \(mins) min stale"),
                   reason: why == .radarsAlive
                       ? L("雷达仍在上报——只有 actd 停了，复制下面的命令在终端重启。",
                           "Radars still report — only actd is down; copy the command below and run it in Terminal.")
                       : L("整条 pipeline 都没有输出——launchd agents 可能没装好，先过一遍依赖检查。",
                           "No pipeline output at all — launchd agents may be missing; run the dependency check first."))
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
            CopyPathLine(label: L("重启：", "Restart: "), path: PipelineHealthUI.startCommand)
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
}

// MARK: - PipelineEmptyStateView (P1-5) — "no dashboard.json" is not a dead end
//
// One copy of the first-launch empty state for the popover AND the kanban:
// what's missing, the canonical start command (click-to-copy), and a button
// into the dependency check where TCC/npx/key blockers are diagnosed.

struct PipelineEmptyStateView: View {
    unowned let app: AppDelegate

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: "hourglass")
                .font(.system(size: 22))
                .foregroundColor(.secondary)
            Text(L("等待 pipeline 启动", "Waiting for pipeline"))
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Text(L("未找到 state/dashboard.json——actd 还没写出数据（首次安装：先跑 bash install.sh）",
                   "state/dashboard.json not found — actd hasn't written data yet (first install: run bash install.sh)"))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            CopyPathLine(label: L("启动 actd：", "Start actd: "),
                         path: PipelineHealthUI.startCommand)
            Button {
                PipelineHealthUI.openDeps(app: app)
            } label: {
                Label(L("打开依赖检查", "Open dependency check"), systemImage: "checklist")
            }
            .font(.system(size: 11))
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
