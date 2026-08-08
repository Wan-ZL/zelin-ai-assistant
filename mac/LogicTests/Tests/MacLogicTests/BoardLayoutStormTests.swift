// BoardLayoutStormTests.swift — 看板布局风暴修复的「布局预算」判例（防复发）。
// 两份系统 hang 报告（2026-07-28 主线程卡死 1031s / 07-29 70.5s）确诊：actd
// 每 ~10s 的 dashboard.json 心跳重写触发全量 decode+publish，加上每行卡片的
// modifier 整体 @ObservedObject store+flights，任何一个 @Published 变化都把
// 全板 ~128 行拖去重排。纯布局耗时不可直接单测 —— 这里钉住两个可测代理指标：
//
//  1. 「假更新不发布」: 仅 generated_at 变化的 dashboard.json，
//     DashboardReloadGate 的内容指纹必须相同 —— Store.reload 据此跳过
//     decode + withAnimation publish（ReloadGate.swift）。
//  2. 「单卡变化只影响单行」: 每行的 BoardRowMotion 是 Equatable 值，由泳道层
//     算好传入（行 modifier 无对象订阅）—— 150 卡板上一个事件/一次落地，只有
//     涉事行的值变化，其余行值逐位不变（SwiftUI 值比较短路，不重算 body/
//     layout）。谁把 seq/hidden 的派生改成「全板跟着事件走」，这里立刻红。

import Testing
import Foundation
@testable import MacLogic

// MARK: - 判例 1：假更新不发布（DashboardReloadGate）

struct DashboardReloadGateTests {

    /// Minimal dashboard.json in the wire shape actd writes (§2): top-level
    /// generated_at + a couple of card lists. Content 参数化以便造真假更新。
    private func dashboardData(generatedAt: String, title: String = "修复布局风暴",
                               running: [String] = ["R-1"]) -> Data {
        let obj: [String: Any] = [
            "generated_at": generatedAt,
            "needs_approval": [["id": "R-9", "title": title]],
            "running": running.map { ["id": $0, "name": "任务 \($0)"] },
            "counts": ["running": running.count],
        ]
        return try! JSONSerialization.data(withJSONObject: obj)
    }

    /// 心跳假更新：只有 generated_at 变 → 指纹必须相同（reload 据此跳过发布），
    /// 且新的 generated_at 仍被暴露（新鲜度/健康裁决要继续推进）。
    @Test func heartbeatOnlyChangeKeepsFingerprint() {
        let a = DashboardReloadGate.read(
            dashboardData(generatedAt: "2026-08-07T10:00:00Z"))
        let b = DashboardReloadGate.read(
            dashboardData(generatedAt: "2026-08-07T10:00:10Z"))
        #expect(a.fingerprint != nil)
        #expect(a.fingerprint == b.fingerprint)
        #expect(a.generatedAt == "2026-08-07T10:00:00Z")
        #expect(b.generatedAt == "2026-08-07T10:00:10Z")
    }

    /// 真更新：任何内容字段变化（标题改名 / 卡片增减）→ 指纹必须不同。
    @Test func realContentChangeChangesFingerprint() {
        let base = DashboardReloadGate.read(
            dashboardData(generatedAt: "2026-08-07T10:00:00Z"))
        let renamed = DashboardReloadGate.read(
            dashboardData(generatedAt: "2026-08-07T10:00:10Z", title: "改了名"))
        let grown = DashboardReloadGate.read(
            dashboardData(generatedAt: "2026-08-07T10:00:10Z",
                          running: ["R-1", "R-2"]))
        #expect(base.fingerprint != renamed.fingerprint)
        #expect(base.fingerprint != grown.fingerprint)
    }

    /// key 顺序不参与指纹（.sortedKeys 归一）：同内容不同序列化顺序 = 假更新。
    @Test func keyOrderDoesNotAffectFingerprint() {
        let a = Data(#"{"generated_at":"g1","counts":{"running":1},"running":[]}"#.utf8)
        let b = Data(#"{"running":[],"counts":{"running":1},"generated_at":"g2"}"#.utf8)
        let ra = DashboardReloadGate.read(a)
        let rb = DashboardReloadGate.read(b)
        #expect(ra.fingerprint != nil)
        #expect(ra.fingerprint == rb.fingerprint)
    }

    /// 坏 JSON / 顶层非 object → 指纹 nil，调用方必须落回真 decoder（fail
    /// open 到完整路径，绝不把解析不了的文件误判成假更新 —— 宪法 11 条精神）。
    @Test func unparseableInputYieldsNilFingerprint() {
        #expect(DashboardReloadGate.read(Data("{truncated".utf8)).fingerprint == nil)
        #expect(DashboardReloadGate.read(Data("[1,2,3]".utf8)).fingerprint == nil)
        #expect(DashboardReloadGate.read(Data()).fingerprint == nil)
    }
}

// MARK: - 判例 2：单卡变化只影响单行（BoardRowMotionPlanner）

struct BoardRowMotionBudgetTests {

    /// 150 卡的板（比事故现场的 ~128 略大），全部静止时的每行基线值。
    private let ids = (0..<150).map { "R-\($0)" }

    private func states(event: BoardMotionEvent?, lastSeq: Int,
                        pendingLanding: Set<String> = [],
                        landed: Set<String> = []) -> [String: BoardRowMotion] {
        var out: [String: BoardRowMotion] = [:]
        for id in ids {
            out[id] = BoardRowMotionPlanner.state(
                id: id, event: event, lastSeq: lastSeq,
                pendingLanding: pendingLanding, landed: landed,
                animationsEnabled: true)
        }
        return out
    }

    /// 一张卡换道的事件发布后：只有那一张卡的行值变化，其余 149 行的值必须
    /// 与静止基线逐位相等（Equatable 短路的前提 —— 值相等 = 不重算该行）。
    @Test func singleMoveEventOnlyChangesThatRow() {
        let baseline = states(event: nil, lastSeq: 0)
        let diff = BoardDiff.compute(
            previous: [BoardLaneList(lane: "approval", ids: ids)],
            current: [
                BoardLaneList(lane: "approval", ids: ids.filter { $0 != "R-7" }),
                BoardLaneList(lane: "running", ids: ["R-7"]),
            ])
        let event = BoardMotionEvent(seq: 1, diff: diff)
        let after = states(event: event, lastSeq: 1)
        for id in ids where id != "R-7" {
            #expect(after[id] == baseline[id], "无关行 \(id) 的值不许因事件变化")
        }
        #expect(after["R-7"] != baseline["R-7"])
        #expect(after["R-7"]?.hidden == true)   // move 目标行等 proxy 落地
        #expect(after["R-7"]?.seq == 1)         // 涉事行携带事件 seq（bornGen）
    }

    /// 一次落地（finish()：pendingLanding 移除 + landed 加入）只翻转落地那
    /// 一行的 hidden，同事件其余在飞行也好、无关行也好，值都不动。
    @Test func singleLandingOnlyChangesThatRow() {
        let diff = BoardDiff.compute(
            previous: [BoardLaneList(lane: "approval", ids: ids)],
            current: [
                BoardLaneList(lane: "approval",
                              ids: ids.filter { $0 != "R-3" && $0 != "R-4" }),
                BoardLaneList(lane: "running", ids: ["R-3", "R-4"]),
            ])
        let event = BoardMotionEvent(seq: 2, diff: diff)
        let airborne = states(event: event, lastSeq: 2,
                              pendingLanding: ["R-3", "R-4"])
        let oneLanded = states(event: event, lastSeq: 2,
                               pendingLanding: ["R-4"], landed: ["R-3"])
        for id in ids where id != "R-3" {
            #expect(oneLanded[id] == airborne[id],
                    "R-3 落地不许波及行 \(id)")
        }
        #expect(airborne["R-3"]?.hidden == true)
        #expect(oneLanded["R-3"]?.hidden == false)
    }

    /// 静止基线 = BoardRowMotion.none：无事件时每行携带同一个至静值（seq 0、
    /// 不隐藏、无 deal-in）——事件清除（store 0.8s 后置 nil）把全板回到基线。
    @Test func idleBoardIsUniformlyAtRest() {
        for (_, s) in states(event: nil, lastSeq: 5) {
            #expect(s == BoardRowMotion.none)
        }
    }

    /// crossfade 事件（>flightCap 变化）：全员降级为普通淡入 —— 没有行隐藏、
    /// 没有 deal-in（涉事行只有 seq 变，用于 bornGen 消歧）。
    @Test func crossfadeEventHidesNothing() {
        let moves = (0..<10).map {
            BoardDiffResult.Move(id: "R-\($0)", fromLane: "approval",
                                 toLane: "running")
        }
        let event = BoardMotionEvent(
            seq: 3, diff: BoardDiffResult(moves: moves, inserts: [], removals: []))
        #expect(event.crossfade)
        for (_, s) in states(event: event, lastSeq: 3) {
            #expect(s.hidden == false)
            #expect(s.dealInIndex == nil)
        }
    }

    /// deal-in 只给当前事件的 inserts，且下标 = 事件内顺序（40ms stagger）。
    @Test func dealInIndexFollowsInsertOrder() {
        let inserts = [BoardDiffResult.Insert(id: "R-10", lane: "approval"),
                       BoardDiffResult.Insert(id: "R-20", lane: "approval")]
        let event = BoardMotionEvent(
            seq: 4, diff: BoardDiffResult(moves: [], inserts: inserts, removals: []))
        let after = states(event: event, lastSeq: 4)
        #expect(after["R-10"]?.dealInIndex == 0)
        #expect(after["R-20"]?.dealInIndex == 1)
        #expect(after["R-30"]?.dealInIndex == nil)
    }

    /// 动画整体关闭（Reduce Motion / 设置开关）：一切派生都失效 —— 有事件也
    /// 全板保持至静基线的 hidden/dealIn（seq 仍随涉事行走，帧消歧无害）。
    @Test func animationsOffNeverHides() {
        let diff = BoardDiffResult(
            moves: [.init(id: "R-1", fromLane: "approval", toLane: "running")],
            inserts: [.init(id: "R-2", lane: "approval")], removals: [])
        let s1 = BoardRowMotionPlanner.state(
            id: "R-1", event: BoardMotionEvent(seq: 5, diff: diff), lastSeq: 5,
            pendingLanding: ["R-1"], landed: [], animationsEnabled: false)
        let s2 = BoardRowMotionPlanner.state(
            id: "R-2", event: BoardMotionEvent(seq: 5, diff: diff), lastSeq: 5,
            pendingLanding: [], landed: [], animationsEnabled: false)
        #expect(s1.hidden == false)
        #expect(s2.dealInIndex == nil)
    }
}
