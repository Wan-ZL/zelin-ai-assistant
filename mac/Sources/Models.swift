// Models.swift — dashboard.json 的 Codable 契约结构（docs/CONTRACT.md section 2；勿改字段）
// Mechanically split from main.swift — zero logic changes.

import AppKit
import SwiftUI
import Foundation

// MARK: - Codable models (strictly per docs/CONTRACT.md section 2)

struct Source: Decodable, Hashable {
    let who: String
    let channel: String
    let date: String
    let quote: String
}

struct ApprovalCard: Decodable, Hashable {
    let id: String
    let title: String
    // v0.1 §7: plain-language one-liner, shown by default (fallback to title).
    let summary: String?
    // v0.1 §7: target repo info, shown as one line by default.
    let target_repo: String?
    let target_name: String?
    let target_kind: String?   // "new" | "existing"
    let tier: String
    let tier_hint: String?
    let hardness: String?
    let deadline: String?
    let days_left: Int?
    let repeated: Int?
    let cost_usd: Double?
    let show_cost: Bool
    let green_sign: Bool?
    let disagreement: String?
    let improvement_of: String?
    let sources: [Source]
    let plan: [String]
    let outputs: [String]?
    let dod: [String]   // §11 验收标准 — approving the card approves this too
    let processing: Bool   // AI is expanding a raised debt -> greyed spinner, no buttons
    let delivery_mode: String?   // v0.10 contract B: "chat" | "repo"

    private enum CodingKeys: String, CodingKey {
        case id, title, summary, target_repo, target_name, target_kind
        case tier, tier_hint, hardness, deadline, days_left
        case repeated, cost_usd, show_cost, green_sign, disagreement
        case improvement_of, sources, plan, outputs, dod, processing
        case delivery_mode
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        summary = try? c.decodeIfPresent(String.self, forKey: .summary)
        target_repo = try? c.decodeIfPresent(String.self, forKey: .target_repo)
        target_name = try? c.decodeIfPresent(String.self, forKey: .target_name)
        target_kind = try? c.decodeIfPresent(String.self, forKey: .target_kind)
        tier = (try? c.decode(String.self, forKey: .tier)) ?? "T?"
        tier_hint = try? c.decodeIfPresent(String.self, forKey: .tier_hint)
        hardness = try? c.decodeIfPresent(String.self, forKey: .hardness)
        deadline = try? c.decodeIfPresent(String.self, forKey: .deadline)
        days_left = try? c.decodeIfPresent(Int.self, forKey: .days_left)
        repeated = try? c.decodeIfPresent(Int.self, forKey: .repeated)
        cost_usd = try? c.decodeIfPresent(Double.self, forKey: .cost_usd)
        show_cost = (try? c.decodeIfPresent(Bool.self, forKey: .show_cost)) ?? false
        green_sign = try? c.decodeIfPresent(Bool.self, forKey: .green_sign)
        disagreement = try? c.decodeIfPresent(String.self, forKey: .disagreement)
        improvement_of = try? c.decodeIfPresent(String.self, forKey: .improvement_of)
        sources = (try? c.decodeIfPresent([Source].self, forKey: .sources)) ?? []
        plan = (try? c.decodeIfPresent([String].self, forKey: .plan)) ?? []
        outputs = try? c.decodeIfPresent([String].self, forKey: .outputs)
        dod = (try? c.decodeIfPresent([String].self, forKey: .dod)) ?? []
        processing = (try? c.decodeIfPresent(Bool.self, forKey: .processing)) ?? false
        delivery_mode = try? c.decodeIfPresent(String.self, forKey: .delivery_mode)
    }

    /// Plain-language headline shown by default.
    var displaySummary: String {
        if let s = summary, !s.isEmpty { return s }
        return title
    }

    /// Synthetic local placeholder while AI is expanding a just-raised debt,
    /// before the backend's `raising` card appears (covers the ≤10s gap).
    static func processingPlaceholder(id: String, summary: String) -> ApprovalCard {
        let json = """
        {"id":"\(id)","title":\(jsonString(summary)),"summary":\(jsonString(summary)),
         "tier":"","show_cost":false,"sources":[],"plan":[],"dod":[],"processing":true}
        """
        return (try? JSONDecoder().decode(ApprovalCard.self, from: Data(json.utf8)))
            ?? ApprovalCard.emptyProcessing(id: id, summary: summary)
    }
    private static func emptyProcessing(id: String, summary: String) -> ApprovalCard {
        let data = Data("{\"id\":\"\(id)\",\"title\":\"\",\"show_cost\":false,\"processing\":true}".utf8)
        return try! JSONDecoder().decode(ApprovalCard.self, from: data)
    }
}

private func jsonString(_ s: String) -> String {
    (try? String(data: JSONSerialization.data(withJSONObject: s, options: .fragmentsAllowed), encoding: .utf8)) ?? "\"\""
}

struct RunningTask: Decodable, Hashable {
    let id: String
    let name: String
    let session_id: String?
    let short_id: String?
    let copy_cmd: String?    // state-correct command: attach (live) / --resume (done)
    let agent_name: String?  // how this session is named in the `claude agents` list
    let cwd: String?
    let state: String?       // v0.10: "queued" = approved, not yet dispatched (no session)
    let started_at: Int?
    let waiting_for: String?
    // v0.10 contract B — optional detail fields (running / queued / completed reuse this struct).
    let summary: String?
    let plan: [String]?
    let dod: [String]?
    let log: String?
    let dispatched_at: Int?      // epoch seconds
    let delivery_mode: String?   // "chat" | "repo"
    let last_error: String?
    let dispatch_error: String?  // queued items only: why dispatch failed (nil = pending)
    // §25 classification ids (act/lib/failures.py) — nil/absent = unclassified
    let last_error_id: String?
    let dispatch_error_id: String?
    // completed[] extras (contract B).
    let delivered_summary: String?
    let accepted_at: Int?        // epoch seconds
}

struct DebtItem: Decodable, Hashable {
    let id: String
    let title: String
    let summary: String?   // v0.1 §7: plain-language one-liner (optional)
    let hardness: String?
    let type: String?
    let sources: [Source]?   // v0.10 contract B: provenance quotes (same shape as approval card)

    /// Plain-language headline shown by default.
    var displaySummary: String {
        if let s = summary, !s.isEmpty { return s }
        return title
    }
}

// v0.1 §9: recycle bin item.
struct TrashItem: Decodable, Hashable {
    let id: String
    let title: String
    let summary: String?
    let kind: String?          // "suggestion" | "debt"
    let trashed_at: String?    // ISO8601
    let trash_reason: String?  // "rejected" | "deleted"
    let permanent: Bool
    let type: String?
    let hardness: String?

    private enum CodingKeys: String, CodingKey {
        case id, title, summary, kind, trashed_at, trash_reason, permanent, type, hardness
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        summary = try? c.decodeIfPresent(String.self, forKey: .summary)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        trashed_at = try? c.decodeIfPresent(String.self, forKey: .trashed_at)
        trash_reason = try? c.decodeIfPresent(String.self, forKey: .trash_reason)
        permanent = (try? c.decodeIfPresent(Bool.self, forKey: .permanent)) ?? false
        type = try? c.decodeIfPresent(String.self, forKey: .type)
        hardness = try? c.decodeIfPresent(String.self, forKey: .hardness)
    }

    /// Plain-language headline shown by default.
    var displaySummary: String {
        if let s = summary, !s.isEmpty { return s }
        return title
    }
}

// §11 待验收 item — draft delivered, awaiting Zelin's ✓/↩︎.
struct ReviewItem: Decodable, Hashable {
    let id: String
    let name: String
    let summary: String?
    let dod: [String]
    let session_id: String?
    let short_id: String?
    let copy_cmd: String?
    let cwd: String?

    let agent_name: String?

    // v0.10 contract B — optional detail fields.
    let delivered_summary: String?
    let final_draft: String?
    let plan: [String]?
    let sources: [Source]?       // same shape as approval-card sources
    let log: String?
    let dispatched_at: Int?      // epoch seconds
    let review_at: Int?          // epoch seconds
    let delivery_mode: String?   // "chat" | "repo"

    private enum CodingKeys: String, CodingKey {
        case id, name, summary, dod, session_id, short_id, copy_cmd, cwd, agent_name
        case delivered_summary, final_draft, plan, sources, log, dispatched_at, review_at, delivery_mode
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        summary = try? c.decodeIfPresent(String.self, forKey: .summary)
        dod = (try? c.decodeIfPresent([String].self, forKey: .dod)) ?? []
        session_id = try? c.decodeIfPresent(String.self, forKey: .session_id)
        short_id = try? c.decodeIfPresent(String.self, forKey: .short_id)
        copy_cmd = try? c.decodeIfPresent(String.self, forKey: .copy_cmd)
        cwd = try? c.decodeIfPresent(String.self, forKey: .cwd)
        agent_name = try? c.decodeIfPresent(String.self, forKey: .agent_name)
        delivered_summary = try? c.decodeIfPresent(String.self, forKey: .delivered_summary)
        final_draft = try? c.decodeIfPresent(String.self, forKey: .final_draft)
        plan = try? c.decodeIfPresent([String].self, forKey: .plan)
        sources = try? c.decodeIfPresent([Source].self, forKey: .sources)
        log = try? c.decodeIfPresent(String.self, forKey: .log)
        dispatched_at = try? c.decodeIfPresent(Int.self, forKey: .dispatched_at)
        review_at = try? c.decodeIfPresent(Int.self, forKey: .review_at)
        delivery_mode = try? c.decodeIfPresent(String.self, forKey: .delivery_mode)
    }
}

// 契约 merge-review §六: dashboard.json 的 merge_suggestions[] 分区 —
// actd 发 analyzing/done/failed 三态（dismissed 不发）。除 id 外全部
// decodeIfPresent 向后兼容（老 actd 不发该分区时 Dashboard 照常解码）。
struct MergeSuggestion: Decodable, Hashable {
    let id: String             // "MS-" + 8 位随机
    let ids: [String]          // 被分析的卡（≥2）
    let status: String         // "analyzing" | "done" | "failed"
    let verdict: String?       // §三: merge | link_improvement | keep_separate | close_secondary
    let primary: String?       // 主卡 id（merge / link_improvement 时有意义）
    let rationale: String?
    let action_plan: [String]  // 「接受后将执行」清单 — UI 展示全文
    let confidence: String?    // "high" | "medium" | "low"
    let error: String?         // failed 时的原因
    let requested_at: Int?     // epoch seconds

    private enum CodingKeys: String, CodingKey {
        case id, ids, status, verdict, primary, rationale
        case action_plan, confidence, error, requested_at
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        ids = (try? c.decodeIfPresent([String].self, forKey: .ids)) ?? []
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "analyzing"
        verdict = try? c.decodeIfPresent(String.self, forKey: .verdict)
        primary = try? c.decodeIfPresent(String.self, forKey: .primary)
        rationale = try? c.decodeIfPresent(String.self, forKey: .rationale)
        action_plan = (try? c.decodeIfPresent([String].self, forKey: .action_plan)) ?? []
        confidence = try? c.decodeIfPresent(String.self, forKey: .confidence)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        requested_at = try? c.decodeIfPresent(Int.self, forKey: .requested_at)
    }
}

struct Counts: Decodable {
    let needs_approval: Int
    let running: Int
    let needs_input: Int
    let review: Int
    let completed: Int
    let debt: Int
    let trash: Int

    private enum CodingKeys: String, CodingKey {
        case needs_approval, running, needs_input, review, completed, debt, trash
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        needs_approval = (try? c.decodeIfPresent(Int.self, forKey: .needs_approval)) ?? 0
        running = (try? c.decodeIfPresent(Int.self, forKey: .running)) ?? 0
        needs_input = (try? c.decodeIfPresent(Int.self, forKey: .needs_input)) ?? 0
        review = (try? c.decodeIfPresent(Int.self, forKey: .review)) ?? 0
        completed = (try? c.decodeIfPresent(Int.self, forKey: .completed)) ?? 0
        debt = (try? c.decodeIfPresent(Int.self, forKey: .debt)) ?? 0
        trash = (try? c.decodeIfPresent(Int.self, forKey: .trash)) ?? 0
    }

    static let empty = Counts()
    private init() {
        needs_approval = 0; running = 0; needs_input = 0; review = 0
        completed = 0; debt = 0; trash = 0
    }
}

struct Dashboard: Decodable {
    let generated_at: String?
    let counts: Counts
    let needs_approval: [ApprovalCard]
    let running: [RunningTask]
    let needs_input: [RunningTask]
    let review: [ReviewItem]
    let completed: [RunningTask]
    let debt: [DebtItem]
    let trash: [TrashItem]
    // 契约 merge-review §六 — optional 分区，缺失时解码为 []（向后兼容）。
    let merge_suggestions: [MergeSuggestion]

    private enum CodingKeys: String, CodingKey {
        case generated_at, counts, needs_approval, running, needs_input, review, completed, debt, trash
        case merge_suggestions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generated_at = try? c.decodeIfPresent(String.self, forKey: .generated_at)
        counts = (try? c.decodeIfPresent(Counts.self, forKey: .counts)) ?? .empty
        needs_approval = (try? c.decodeIfPresent([ApprovalCard].self, forKey: .needs_approval)) ?? []
        running = (try? c.decodeIfPresent([RunningTask].self, forKey: .running)) ?? []
        needs_input = (try? c.decodeIfPresent([RunningTask].self, forKey: .needs_input)) ?? []
        review = (try? c.decodeIfPresent([ReviewItem].self, forKey: .review)) ?? []
        completed = (try? c.decodeIfPresent([RunningTask].self, forKey: .completed)) ?? []
        debt = (try? c.decodeIfPresent([DebtItem].self, forKey: .debt)) ?? []
        trash = (try? c.decodeIfPresent([TrashItem].self, forKey: .trash)) ?? []
        merge_suggestions = (try? c.decodeIfPresent([MergeSuggestion].self,
                                                    forKey: .merge_suggestions)) ?? []
    }
}

// quick-capture placeholder: shown as a grey spinner card until a matching
// needs_approval card appears (title carries the first 20 chars of the text)
// or 180 s pass.
struct CapturePending {
    let id: String
    let text: String
    let created: Date
}
