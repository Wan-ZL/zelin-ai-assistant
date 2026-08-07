// BoardRowMotion.swift — 每行卡片的 motion 派生值（v0.46.x 布局风暴修复）。
// Foundation ONLY, by contract: 与 BoardDiff.swift 同族的纯逻辑文件，
// mac/LogicTests 以 symlink 编译（「单卡变化只影响单行」判例），所以
// AppKit/SwiftUI/Combine 一律不得 import（依赖仅 BoardDiff.swift 的
// BoardMotionEvent）。
//
// 背景：BoardCardMotionModifier 原本整体 @ObservedObject store + flights ——
// 任何一个 @Published 变化（哪怕只涉及一张卡的 pulse/landing）都把全板每一
// 行标脏重排，128 行 × 弹性测量递归 = 2026-07-28 主线程 hang 的最大放大器。
// 现在每行需要的派生值在泳道层算好、以本文件的 Equatable 值传入：值没变的
// 行 SwiftUI 结构比较短路，不重算 body / layout —— O(整板) 降到 O(变化行)。

import Foundation

/// One row's complete motion input, as a VALUE. Equatable 是本类型的全部意义:
/// 一个事件/一次落地只让涉事行的值变化，其余行值逐位不变 → 行 modifier
/// （全值字段，无 ObservedObject）被 SwiftUI 判等跳过。
struct BoardRowMotion: Equatable {
    /// The motion-event generation to stamp as the row's bornGen on appear
    /// (BoardFramesKey 的同 key 消歧). 只有当前事件涉及（moves/inserts/
    /// removals 命中）的行才携带事件 seq —— 无关行恒为 0，事件发布才不会
    /// 把全板 128 行的值一起变掉。语义等价：bornGen 只在「新行与同 key 的
    /// 退场行共存」时参与 merge 判优，而共存只发生在被事件触及的行上。
    let seq: Int
    /// True while the row is the destination of a not-yet-landed flight —
    /// renders at opacity 0 so the proxy is the only "card" visible.
    let hidden: Bool
    /// The row's index among the CURRENT event's inserts (40 ms stagger),
    /// nil = plain opacity fade（非本事件插入 / crossfade / 动画关闭）.
    let dealInIndex: Int?

    /// The at-rest value: what every row carries while nothing animates.
    static let none = BoardRowMotion(seq: 0, hidden: false, dealInIndex: nil)
}

enum BoardRowMotionPlanner {
    /// Derive one row's motion value. Pure — the caller (KanbanView) feeds in
    /// the controller's current sets + the store's current event; 判例测试
    /// 直接喂构造数据钉行为。
    /// - lastSeq: BoardFlightController.lastSeq（事件消费进度；baseline 后
    ///   首帧不隐藏历史 backlog 的 move 目标行）。
    /// - animationsEnabled: BoardMotionPolicy.animationsEnabled，调用方算好
    ///   传入（NSWorkspace 探测不属于纯逻辑）。
    static func state(id: String, event: BoardMotionEvent?, lastSeq: Int,
                      pendingLanding: Set<String>, landed: Set<String>,
                      animationsEnabled: Bool) -> BoardRowMotion {
        BoardRowMotion(
            seq: touchedSeq(id: id, event: event),
            hidden: hidden(id: id, event: event, lastSeq: lastSeq,
                           pendingLanding: pendingLanding, landed: landed,
                           animationsEnabled: animationsEnabled),
            dealInIndex: dealInIndex(id: id, event: event,
                                     animationsEnabled: animationsEnabled))
    }

    /// The event's seq for rows the event touches, 0 for everyone else（见
    /// BoardRowMotion.seq 的注释 —— 无关行的值必须保持恒定）。
    private static func touchedSeq(id: String, event: BoardMotionEvent?) -> Int {
        guard let event else { return 0 }
        let d = event.diff
        let touched = d.moves.contains { $0.id == id }
            || d.inserts.contains { $0.id == id }
            || d.removals.contains { $0.id == id }
        return touched ? event.seq : 0
    }

    /// 逐字复刻原 BoardFlightController.isAwaitingLanding：pendingLanding
    /// 命中即隐藏；否则仅当「当前事件已被消费（seq == lastSeq）、非 crossfade、
    /// 尚未落地、且本行是 move 目标」时在事件渲染与 +0.05s launch 之间的
    /// 空窗里保持隐藏。
    private static func hidden(id: String, event: BoardMotionEvent?,
                               lastSeq: Int, pendingLanding: Set<String>,
                               landed: Set<String>,
                               animationsEnabled: Bool) -> Bool {
        guard animationsEnabled else { return false }
        if pendingLanding.contains(id) { return true }
        guard let event, event.seq == lastSeq, !event.crossfade,
              !landed.contains(id) else { return false }
        return event.diff.moves.contains { $0.id == id }
    }

    /// 逐字复刻原 BoardCardMotionModifier.insertion 的守卫：动画开、有事件、
    /// 非 crossfade、且本行在 inserts 里 → 返回 stagger 用的下标。
    private static func dealInIndex(id: String, event: BoardMotionEvent?,
                                    animationsEnabled: Bool) -> Int? {
        guard animationsEnabled, let event, !event.crossfade else { return nil }
        return event.diff.inserts.firstIndex { $0.id == id }
    }
}
