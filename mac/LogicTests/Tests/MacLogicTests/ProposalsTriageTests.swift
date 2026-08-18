// ProposalsTriageTests.swift — §34bis 提案积压清理按钮的载荷判例。
// 判例①：按钮触发 → capture 载荷形状正确（mode:"run" + preset 信号 + 固定
// 短标签 text）。preset 字面量与 act/actd.py 的 PROPOSALS_TRIAGE_PRESET
// 逐字一致——两侧各自的测试钉同一字符串（§10bis 两侧逐字常量先例），
// Python 侧判例在 tests/test_proposals_triage.py。

import Testing
import Foundation
@testable import MacLogic

struct ProposalsTriagePayloadTests {

    /// 载荷形状：§10 capture + §34 mode:"run" + §34bis preset，恰好五键。
    @Test func payloadShape() {
        let ts = "2026-08-07T00:00:00Z"
        let p = ProposalsTriage.payload(ts: ts)
        #expect(p.count == 5)
        #expect(p["action"] as? String == "capture")
        #expect(p["mode"] as? String == "run")
        #expect(p["preset"] as? String == "proposals_triage")
        #expect(p["ts"] as? String == ts)
        #expect(p["text"] as? String == ProposalsTriage.captureText)
    }

    /// 短标签非空、≤80（actd 的 title=text 截 80——标签必须整句进标题，
    /// 否则占位卡 ↔ 后端行的归一匹配会因截断错位）；内容点名「清理提案积压」。
    @Test func captureTextIsTitleSafe() {
        #expect(!ProposalsTriage.captureText.isEmpty)
        #expect(ProposalsTriage.captureText.count <= 80)
        #expect(ProposalsTriage.captureText.contains("清理提案积压"))
    }

    /// 载荷必须能过 JSONSerialization（writeInboxFile 的先决条件）。
    @Test func payloadIsJSONSerializable() {
        #expect(JSONSerialization.isValidJSONObject(
            ProposalsTriage.payload(ts: "2026-08-07T00:00:00Z")))
    }

    /// 积压口径（§34bis）：后端 raising 卡（processing=true 灰显）在固定
    /// plan 的清理范围（card_sent/raising）内，必须计入积压 —— 提案列只剩
    /// 正在扩写的卡时按钮不得禁用。绝不按 processing 过滤后端清单。
    @Test func backlogCountsBackendRaisingCards() {
        let raising = try! JSONDecoder().decode(ApprovalCard.self, from: Data(
            #"{"id":"R-9","title":"扩写中","tier":"","show_cost":false,"processing":true}"#.utf8))
        let sent = try! JSONDecoder().decode(ApprovalCard.self, from: Data(
            #"{"id":"R-8","title":"已成卡","tier":"T2","show_cost":false,"processing":false}"#.utf8))
        #expect(raising.processing)
        #expect(ProposalsTriage.backlogCount(backendCards: [raising]) == 1)
        #expect(ProposalsTriage.backlogCount(backendCards: [raising, sent]) == 2)
        #expect(ProposalsTriage.backlogCount(backendCards: []) == 0)
    }

    /// 可用性矩阵（§34bis）：没有积压（count==0）或冷却中 → 禁用；
    /// 有积压且未冷却 → 可用。
    @Test func buttonEnabledMatrix() {
        #expect(!ProposalsTriage.buttonEnabled(backlogCount: 0, cooling: false))
        #expect(!ProposalsTriage.buttonEnabled(backlogCount: 0, cooling: true))
        #expect(!ProposalsTriage.buttonEnabled(backlogCount: 3, cooling: true))
        #expect(ProposalsTriage.buttonEnabled(backlogCount: 3, cooling: false))
    }
}
