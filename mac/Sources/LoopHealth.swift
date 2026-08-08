// LoopHealth.swift — §47.3 actd 主循环健康的只读投影（state/loop_health.json）
//
// Python 侧（act/actd.py LoopHealthTracker）每次 pass 失败都写这个文件、恢复
// 时写一次清零回执。App 侧只在 dashboard 新鲜（pipelineHealth 本会判 .ok）时
// 参考它：连续 LOOP_ALARM_AFTER 次失败 → PipelineHealth.failing —— 这是
// 2026-07-06 事故的形态（NameError 连崩 15+ pass，但 write-early 让
// generated_at 保持新鲜，看板一路绿灯，用户一周后才从日志发现）。
// dashboard 已经 stale/dead 时不看此文件——那两个 verdict 更严重且已有横幅。
//
// 纯 Foundation、无 AppKit——被 mac/LogicTests 以 symlink 方式直接单测。

import Foundation

struct LoopHealth: Equatable {
    let consecutiveFailures: Int
    let lastError: String?

    /// 与 act/actd.py 的 LOOP_ALARM_AFTER 同值：单次失败可能是瞬时抖动，
    /// 连续 3 次（~30s）说明每轮都在同一处崩，才值得亮红。
    static let alarmThreshold = 3

    var failing: Bool { consecutiveFailures >= Self.alarmThreshold }

    /// 解析 loop_health.json 内容。坏 JSON / 形状不对 → nil（缺省 = 不报警，
    /// 诊断文件绝不许自己成为报警源）。
    static func parse(_ data: Data) -> LoopHealth? {
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let n = obj["consecutive_failures"] as? Int, n >= 0
        else { return nil }
        return LoopHealth(consecutiveFailures: n,
                          lastError: obj["last_error"] as? String)
    }

    static func load(path: String) -> LoopHealth? {
        guard let data = FileManager.default.contents(atPath: path) else { return nil }
        return parse(data)
    }
}
