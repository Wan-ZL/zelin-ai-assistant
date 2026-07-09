// Freshness.swift — FreshnessLabel（dashboard.json 新鲜度小组件）
//
// 与 popover footer（DashboardView.freshnessLabel，只读参考）同语义：
// generated_at 距今 >90s → 橙色「actd 可能未运行」警告；新鲜 → 灰色相对时间；
// generatedAt == nil → 整个隐藏（popover 自己另有 lastRefresh 降级，这里不需要）。
// TimelineView 每 15s 重算，文件没变（store 不 publish）时标签也保持活。

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
