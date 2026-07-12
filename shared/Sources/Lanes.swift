// Lanes.swift — ListKind + LaneHelp（看板五列的定义与说明文案）
// SHARED between the Mac app and the iOS app. Foundation-only by contract.
// ListKind was mac/Sources/Store.swift; LaneHelp was mac/Sources/Cards.swift —
// both moved verbatim so the iOS pager reuses the exact same lane copy/order.

import Foundation

/// Which dashboard list an item (or its echo) belongs to.
enum ListKind: String { case approval, running, review, debt, trash, completed, archived }

// Lane definitions (v0.18) — the single source both surfaces (board columns
// and popover sections) pass into SectionHeader's `help:`. Copy is derived
// from the triage prompt / code truth so the UI and the radar tell the same
// story; keep them in sync with quick_capture.py's triage rules.
enum LaneHelp {
    static var backlog: String {
        L("真实但不着急的事都先停在这里：雷达低置信度捕获、导入的旧会话、你暂缓的提案。不会自动执行、永不过期；再次提起会自动合并计数。点「研究并提议」升级成提案。",
          "Real but not-urgent asks park here — low-confidence radar captures, imported sessions, proposals you deferred. Nothing runs on its own and nothing expires; restatements merge in automatically. Press \"Research & propose\" to promote one.")
    }
    static var proposals: String {
        L("需要你现在拍板的卡：AI 已附上计划、成本和验收标准。批准=后台开始执行；修改=补充方向重提；入库=先不做。灰色卡是 AI 正在研究的占位。",
          "Cards that need your decision now, each with a plan, cost, and acceptance criteria. Approve = start executing; Comment = redo with your input; Backlog = not now. Grey cards are placeholders the AI is still researching.")
    }
    static var running: String {
        L("已批准的任务由 AI 在后台执行（排队中显示灰卡）。橙色「需输入」= AI 卡住等你回答，排在最前。",
          "Approved tasks the AI is executing in the background (queued ones show grey). Orange \"Needs input\" = the AI is blocked on your answer; those sort first.")
    }
    static var review: String {
        L("AI 认为做完了：看交付摘要或 draft PR。验收=归档进已验收；打回=带你的反馈继续改。",
          "The AI thinks it's done — check the delivery summary or draft PR. Accept archives it; Send back continues with your feedback.")
    }
    static var done: String {
        L("你验收通过的任务存档。徽章数字是真实总数，列表只显示最近 50 条；可退回待验收。",
          "Tasks you accepted. The badge shows the true total; the list keeps the latest 50. Items can be sent back to Review.")
    }
}
