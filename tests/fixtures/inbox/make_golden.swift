// make_golden.swift — golden fixture 生成器（F3，docs/design/inbox-actions.md 的孪生）。
// 逐字复刻 Mac app 的 inbox 写路径序列化：
//   mac/Sources/AppDelegate.swift  writeInboxFile / submitCapture / submitAnswer /
//   submitSetTitle / submitSplitNote / submitMergeReview / submitMergeForce /
//   submitFeedback / submitProposalsTriage
//   mac/Sources/SettingsWeeklyDigest.swift generateNow
//   mac/Sources/SettingsClaudeImport.swift importSelected
// 全部使用 JSONSerialization(options: [.prettyPrinted, .sortedKeys])——golden 即
// 「App 今天真实落盘的字节」（ts 固定为 2026-08-30T12:00:00Z 以便对照）。
// 重新生成：swift make_golden.swift <outdir>

import Foundation

let outDir = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : FileManager.default.currentDirectoryPath

// 固定时间戳 —— 真实 app 为 ISO8601DateFormatter().string(from: Date())，同一格式
let TS = "2026-08-30T12:00:00Z"

func write(_ name: String, _ dict: [String: Any]) {
    // AppDelegate.writeInboxFile 的序列化选项，逐字一致
    let data = try! JSONSerialization.data(
        withJSONObject: dict, options: [.prettyPrinted, .sortedKeys])
    let url = URL(fileURLWithPath: outDir + "/" + name)
    try! data.write(to: url, options: .atomic)
    print("wrote \(name) (\(data.count) bytes)")
}

// —— 卡片决策类（AppDelegate.writeInbox）：comment 键恒在，nil → JSON null ——
func card(_ verb: String, id: String, comment: String?) -> [String: Any] {
    var dict: [String: Any] = ["id": id, "action": verb, "ts": TS]
    dict["comment"] = comment ?? NSNull()
    return dict
}

for verb in ["approve", "reject", "defer", "raise", "trash", "restore", "pin",
             "accept", "done_external", "abort_execution", "stop_to_review",
             "revert_review", "archive", "unarchive"] {
    write("\(verb).golden.json", card(verb, id: "R-001", comment: nil))
}
write("comment.golden.json",
      card("comment", id: "R-001", comment: "改成先出 API 设计再动手，plan 里补一条回滚方案"))
write("rework.golden.json",
      card("rework", id: "R-001", comment: "标题里的数字对不上，请重新核对来源后再交付"))
// merge 建议卡动作走同一 card 路径（id = MS-*，comment 恒 null）
write("merge_apply.golden.json", card("merge_apply", id: "MS-0001", comment: nil))
write("merge_dismiss.golden.json", card("merge_dismiss", id: "MS-0001", comment: nil))

// —— 特形动作 ——
// §38 split_note（AppDelegate.submitSplitNote）
write("split_note.golden.json",
      ["id": "R-001", "action": "split_note",
       "note_ts": "2026-08-30T11:58:03Z", "ts": TS])
// §37 set_title（AppDelegate.submitSetTitle；title 先过 normalizedTitle）
write("set_title.golden.json",
      ["id": "R-001", "action": "set_title",
       "title": "EB-1A 推荐信 3 封定稿", "ts": TS])
// §21 merge_review（顺序 = 用户选择顺序，不排序）
write("merge_review.golden.json",
      ["action": "merge_review", "ids": ["R-012", "R-007"], "ts": TS])
// §21bis merge_force（ids 去重保序，primary ∈ ids）
write("merge_force.golden.json",
      ["action": "merge_force", "ids": ["R-012", "R-007"],
       "primary": "R-012", "ts": TS])
// §29 feedback（ids sorted；publish 恒在）
write("feedback.golden.json",
      ["action": "feedback", "ids": ["R-007", "R-012"],
       "text": "运行中列的排队原因 chip 希望能点开看详情", "publish": false, "ts": TS])
write("feedback-overall.golden.json",
      ["action": "feedback", "ids": [String](),
       "text": "看板整体加载很快，但暗色模式下对比度偏低", "publish": true, "ts": TS])
write("feedback-images.golden.json",
      ["action": "feedback", "ids": [String](),
       "text": "见截图：卡片角标重叠", "publish": false,
       "images": ["/tmp/zai-demo/state/feedback/attachments/D2E0A1B4-1111-2222-3333-444455556666-1.png"],
       "ts": TS])
// （§39 answer_input goldens：retired v0.48.8（#119））
// §10 capture（文件名 capture-<UUID>.json；这里只钉字节形状）
write("capture.golden.json",
      ["action": "capture", "text": "给 OpenReview 提交 rebuttal，提醒我周五前", "ts": TS])
// §34 direct-run capture（mode:"run"）
write("capture-run.golden.json",
      ["action": "capture", "mode": "run",
       "text": "跑一下 tests 里挂掉的 test_dashboard 修掉", "ts": TS])
// §10bis capture 贴图（images = 本机 PNG 绝对路径，≤4）
write("capture-images.golden.json",
      ["action": "capture", "text": "按截图里的样式改下卡片布局",
       "images": ["/tmp/zai-demo/state/attachments/ABCD1234-0000-0000-0000-000000000000-1.png"],
       "ts": TS])
// §34bis 提案积压清理 preset（ProposalsTriage.payload，text/preset 字面量逐字）
write("capture-preset.golden.json",
      ["action": "capture",
       "text": "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议",
       "mode": "run", "preset": "proposals_triage", "ts": TS])
// §24 weekly_digest_now（SettingsWeeklyDigest.generateNow，无 id）
write("weekly_digest_now.golden.json",
      ["action": "weekly_digest_now", "ts": TS])
// §22 import_claude_sessions（SettingsClaudeImport.importSelected）
write("import_claude_sessions.golden.json",
      ["action": "import_claude_sessions",
       "session_ids": ["0f9d3a1c-5b7e-4a2d-9c81-2e6f4b8d0a13",
                       "7c2e9b40-88ad-4f0e-b1d2-3a5c6e7f8901"],
       "ts": TS])
