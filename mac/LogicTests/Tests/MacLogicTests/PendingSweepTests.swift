// PendingSweepTests.swift — 布局风暴修复 review P1 的判例（防复发）。
//
// 修复引入的指纹闸门让「内容没变」的 reload 跳过 decode+publish；但 pending
// 清除谓词是 (backend db × 本地 pending) 的联合函数 —— db 一字不变时，闸门
// 跳过期间新建的 pending 也可能一出生就满足清除条件。复现链：改名对话框预填
// 当前名直接回车 → set_title 写同值 → dashboard 内容零变化 → 清除 sweep 若只
// 挂在 decode 路径就永不执行 → 180s 假「改名超时」。两层钉死：
//  1. 同值改名在 UI 层就是 no-op（PendingSweep.renameIsNoOp，不写 inbox
//     不建 pending）；
//  2. 闸门跳过路径仍对缓存 db 跑 sweep —— 谓词纯函数化（PendingSweepState.
//     cleared(by:)），「出生即满足」的 pending 必须被清；无事可清时
//     differs == false（Store 据此零 publish，风暴约束不破）。

import Testing
import Foundation
@testable import MacLogic

// MARK: - 判例 1：同值改名不产生 pending（renameIsNoOp）

struct RenameNoOpTests {

    /// 预填当前名直接回车 = 同值 → no-op（含 actd 同款空白归一：连续空格 /
    /// 全角空格 U+3000 折叠后相等也算同值 —— actd 落盘的就是折叠形）。
    @Test func sameTitleIsNoOp() {
        #expect(PendingSweep.renameIsNoOp(draft: "修复布局风暴", current: "修复布局风暴"))
        #expect(PendingSweep.renameIsNoOp(draft: "修  复　风暴", current: "修 复 风暴"))
        #expect(PendingSweep.renameIsNoOp(draft: " board  fix ", current: "board fix"))
    }

    /// 真改名不许被误吞。
    @Test func realRenameIsNotNoOp() {
        #expect(!PendingSweep.renameIsNoOp(draft: "新名字", current: "旧名字"))
        #expect(!PendingSweep.renameIsNoOp(draft: "board fix 2", current: "board fix"))
    }
}

// MARK: - 判例 2：闸门跳过路径仍清 pending（cleared(by:) 是 db×pending 纯函数）

struct GateSkipSweepTests {

    /// actd 写的最小 wire 形状：R-1 在提案列（display_title 可参数化），
    /// R-2 在运行中。generated_at 参数化以便造「纯心跳假更新」。
    private func dashboardData(generatedAt: String,
                               displayTitle: String? = nil) -> Data {
        var card: [String: Any] = ["id": "R-1", "title": "raw-title",
                                   "plan": ["step 1"]]
        if let displayTitle { card["display_title"] = displayTitle }
        let obj: [String: Any] = [
            "generated_at": generatedAt,
            "needs_approval": [card],
            "running": [["id": "R-2", "name": "任务 R-2"]],
        ]
        return try! JSONSerialization.data(withJSONObject: obj)
    }

    private func decode(_ data: Data) -> Dashboard {
        try! JSONDecoder().decode(Dashboard.self, from: data)
    }

    /// 核心判例：同值改名的 pending「出生即满足」清除谓词 —— 即便两次
    /// dashboard.json 只有 generated_at 变（指纹相同 = Store 走闸门跳过
    /// 路径、不再 decode），对缓存 db 跑 sweep 也必须把它清掉，而不是
    /// 等 180s 假超时。空白差异（actd 落盘折叠形 vs 用户输入）同样命中。
    @Test func bornSatisfiedPendingTitleClearsOnGateSkipPath() {
        let a = dashboardData(generatedAt: "2026-08-07T10:00:00Z",
                              displayTitle: "当前名字")
        let b = dashboardData(generatedAt: "2026-08-07T10:00:10Z",
                              displayTitle: "当前名字")
        // 前置：这确实是闸门会跳过的假更新（判例 1 的语义，此处作场景锚）。
        #expect(DashboardReloadGate.read(a).fingerprint
                    == DashboardReloadGate.read(b).fingerprint)

        let cached = decode(a)   // Store 手里缓存的上次 decode 结果
        var state = PendingSweepState()
        state.pendingTitles = [
            "R-1": PendingTitle(title: "当前名字", created: Date()),
        ]
        let swept = state.cleared(by: cached)
        #expect(swept.pendingTitles.isEmpty)
        #expect(swept.differs(from: state))   // Store 据此才 publish

        // 空白变体：actd 存折叠形（单空格），pending 值带连续/全角空格
        // 也算已生效（normalizedTitle 折叠比较，历史 review 修复的语义）。
        let collapsed = decode(dashboardData(generatedAt: "2026-08-07T10:00:00Z",
                                             displayTitle: "当前 名字"))
        state.pendingTitles = [
            "R-1": PendingTitle(title: "当前　 名字", created: Date()),
        ]
        #expect(state.cleared(by: collapsed).pendingTitles.isEmpty)
    }

    /// 反面：真改名 in flight（backend 还没跟上）必须留着等 REAL signal；
    /// 且 differs == false —— 闸门跳过路径无事可清时零 publish（布局风暴
    /// 修复的核心约束，谁在跳过路径上无条件赋值 @Published 这里立刻红）。
    @Test func inFlightRenameSurvivesAndCausesNoPublish() {
        let cached = decode(dashboardData(generatedAt: "2026-08-07T10:00:00Z",
                                          displayTitle: "旧名字"))
        var state = PendingSweepState()
        state.pendingTitles = [
            "R-1": PendingTitle(title: "新名字", created: Date()),
        ]
        let swept = state.cleared(by: cached)
        #expect(swept.pendingTitles.count == 1)
        #expect(!swept.differs(from: state))
    }

    /// 谓词的联合性不止 title 一家：卡已不在需输入列的 answerPending、
    /// 目标列已含该卡的 approve echo，同样「出生即满足」→ 同一次 sweep 清掉。
    @Test func otherBornSatisfiedPendingsClearToo() {
        let cached = decode(dashboardData(generatedAt: "2026-08-07T10:00:00Z"))
        var state = PendingSweepState()
        // R-9 不在（且从未在）needs_input → 答案回执无信号可等
        state.answerPending = ["R-9": Date()]
        // R-2 已经在 running 列 → echo 的目标列早已含它
        state.pendingEchoes = [PendingEcho(
            id: "echo-R-2", sourceID: "R-2", title: "任务 R-2",
            target: .running, source: .approval, label: "已批准", created: Date())]
        let swept = state.cleared(by: cached)
        #expect(swept.answerPending.isEmpty)
        #expect(swept.pendingEchoes.isEmpty)
        #expect(swept.differs(from: state))
    }
}
