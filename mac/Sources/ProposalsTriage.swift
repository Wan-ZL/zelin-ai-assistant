// ProposalsTriage.swift — 提案泳道头「清理积压」按钮的纯逻辑（CONTRACT §34bis）。
//
// 点击 = 一次固定 prompt 的 direct-run capture（§34 mode:"run" 同机制）：
// 固定 prompt 的单一真源在 Python 侧（act/actd.py 的 _proposals_triage_plan），
// Swift 只发 add-only 键 `preset` 信号 + 短标签 text —— 防跨端 prompt 漂移。
// 本文件只 import Foundation：LogicTests 经 symlink 钉载荷形状（第四道门）。

import Foundation

enum ProposalsTriage {
    /// preset 词表值 — 与 act/actd.py 的 PROPOSALS_TRIAGE_PRESET 逐字一致
    /// （§34bis；两侧各有测试钉同一字面量，§10bis ANSWER_ATTACHMENT_PREFIX 先例）。
    static let presetKey = "proposals_triage"

    /// capture 短标签 = 卡片标题 + 运行中列灰色占位卡的回显文案
    /// （actd 不改写 text —— 占位卡 ↔ 后端行的归一匹配天然成立）。
    static let captureText = "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议"

    /// 按钮可用性（§34bis）：提案列没有积压（count==0）时禁用——空列开
    /// 清理会话只会交付一张空清单。cooling = 2s 防连点（UI 层辅助；真正
    /// 的防双开是 actd 的在途判重）。
    static func buttonEnabled(backlogCount: Int, cooling: Bool) -> Bool {
        backlogCount > 0 && !cooling
    }

    /// inbox capture 载荷（§34bis 形状）：§10 capture + §34 mode:"run"
    /// + preset 信号。字段 add-only，缺 preset 的老 actd 把它当普通 direct-run。
    static func payload(ts: String) -> [String: Any] {
        [
            "action": "capture",
            "text": captureText,
            "mode": "run",
            "preset": presetKey,
            "ts": ts,
        ]
    }
}
