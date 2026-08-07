// ReloadGate.swift — dashboard.json 重载闸门的纯逻辑（v0.46.x 布局风暴修复）。
// Foundation ONLY, by contract: mac/LogicTests 以 symlink 编译本文件做判例
// 测试（「假更新不发布」），所以 AppKit/SwiftUI/Combine 一律不得 import。
//
// 背景：actd 每 ~10s 全量重写 dashboard.json，generated_at 心跳字段必变、
// 卡片内容常常一字不变。Store.reload 原本只有字节级短路 —— 心跳一跳就全量
// JSONDecoder decode + withAnimation publish，一次 publish = 全板 ~128 行
// SwiftUI 重排。这正是 2026-07-28（1031s）/ 07-29（70.5s）两份系统 hang
// 报告里主线程卡死的节拍器。本闸门提供「剥掉 generated_at 之后的内容指纹」，
// 让 reload 识别出纯心跳的假更新并跳过发布。

import Foundation

enum DashboardReloadGate {
    /// One parse of a dashboard.json candidate: the content fingerprint
    /// (canonical re-serialization MINUS generated_at) plus the generated_at
    /// string itself (the caller still advances freshness/health off it).
    struct Reading: Equatable {
        /// nil = not a JSON object (corrupt / truncated / top-level array) —
        /// the caller must fall through to the real decoder, never skip.
        let fingerprint: Data?
        let generatedAt: String?
    }

    /// Parse `data` and derive its content fingerprint. `.sortedKeys` makes
    /// the re-serialization canonical (recursively), so two files with the
    /// same content — regardless of key order or the generated_at value —
    /// yield byte-equal fingerprints, and ANY field change anywhere yields a
    /// different one. add-only 契约（CONTRACT header）下未知新字段自然参与
    /// 指纹，永不误判为假更新。
    static func read(_ data: Data) -> Reading {
        guard var obj = (try? JSONSerialization.jsonObject(with: data))
                as? [String: Any] else {
            return Reading(fingerprint: nil, generatedAt: nil)
        }
        let gen = obj.removeValue(forKey: "generated_at") as? String
        let fp = try? JSONSerialization.data(withJSONObject: obj,
                                             options: [.sortedKeys])
        return Reading(fingerprint: fp, generatedAt: gen)
    }
}
