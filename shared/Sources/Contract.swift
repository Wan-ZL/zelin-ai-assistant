// Contract.swift — dashboard.json 的 Codable 契约结构（docs/CONTRACT.md section 2；勿改字段）
// SHARED between the Mac app and the iOS app. Foundation-only by contract
// (mac/build.sh lint gate forbids AppKit/UIKit/SwiftUI here). Was mac/Sources/
// Models.swift — moved verbatim (zero logic changes) except the imports.

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
    // v0.20 card-lifecycle re-raise: this proposal is a previously-accepted
    // (delivered/merged) thread re-raised to card_sent after new actionable
    // info arrived. reraised → amber 「回锅/Returned」marker; reraisedNote =
    // the new ask, shown inline (新增:<note>).
    let reraised: Bool
    let reraisedNote: String?

    private enum CodingKeys: String, CodingKey {
        case id, title, summary, target_repo, target_name, target_kind
        case tier, tier_hint, hardness, deadline, days_left
        case repeated, cost_usd, show_cost, green_sign, disagreement
        case improvement_of, sources, plan, outputs, dod, processing
        case delivery_mode
        case reraised
        case reraisedNote = "reraised_note"
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
        reraised = (try? c.decodeIfPresent(Bool.self, forKey: .reraised)) ?? false
        reraisedNote = try? c.decodeIfPresent(String.self, forKey: .reraisedNote)
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

// 缺 id 行的确定性回退 id：由行内容派生（FNV-1a 64-bit），同一行每次 decode
// 得到同一个 id —— 随机 UUID 会让身份每 ~10s reload 漂移一次（SwiftUI 动画
// 抖动、hiddenSticky/pendingMergeActions 等按 id 记账全部失配）。"noid-" 前缀
// 让它在日志/inbox 里一眼可辨（缺 id 的行 actd 本就无法匹配，这点不变）。
private func stableFallbackID(_ parts: String?...) -> String {
    var hash: UInt64 = 0xcbf2_9ce4_8422_2325           // FNV offset basis
    for part in parts {
        for byte in (part ?? "\u{1}").utf8 {           // nil 与空串区分开
            hash = (hash ^ UInt64(byte)) &* 0x0000_0100_0000_01b3
        }
        hash = (hash ^ 0x1f) &* 0x0000_0100_0000_01b3  // 字段分隔，防串接歧义
    }
    return String(format: "noid-%016llx", hash)
}

// 行级 lenient 解码（docs/CONTRACT.md §2 v0.10 容错意图的落实）：一行坏数据只
// 跳过该行（好行存活），绝不把整列清空。之前 `(try? [T].self) ?? []` 会把单行
// 失败放大成整列静默丢失——徽章有数、列却空着，loadError 还是 nil。
// 每个被跳过的行/分区记进 drops（Dashboard.decodeDrops），UI 可见 + NSLog。
private struct AnySkippedRow: Decodable {}   // 只为把坏行从容器里消费掉

private func decodeLossyRows<T: Decodable, K: CodingKey>(
    _ c: KeyedDecodingContainer<K>,
    _ key: K,
    drops: inout [String]
) -> [T] {
    guard var rows = try? c.nestedUnkeyedContainer(forKey: key) else {
        // 键缺失 = 老 payload 正常向后兼容；键存在但整列类型坏 = 可观测地丢弃
        if c.contains(key) { drops.append("\(key.stringValue) (整列损坏 not an array)") }
        return []
    }
    var out: [T] = []
    while !rows.isAtEnd {
        let idx = rows.currentIndex
        if let item = try? rows.decode(T.self) {
            out.append(item)
            continue
        }
        drops.append("\(key.stringValue)[\(idx)]")
        // 失败后 index 一般不前进：消费掉坏行再继续；仍卡住则放弃余下行防死循环
        if rows.currentIndex == idx {
            _ = try? rows.decode(AnySkippedRow.self)
            if rows.currentIndex == idx {
                drops.append("\(key.stringValue)[\(idx)+] (无法跳过，余行丢弃)")
                break
            }
        }
    }
    return out
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
    // §30 v0.28.1: true when a 待验收 card is projected into 运行中 because its
    // session was reactivated via attach (on-disk status is still review).
    let from_review: Bool?
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
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        summary = try? c.decodeIfPresent(String.self, forKey: .summary)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        trashed_at = try? c.decodeIfPresent(String.self, forKey: .trashed_at)
        trash_reason = try? c.decodeIfPresent(String.self, forKey: .trash_reason)
        permanent = (try? c.decodeIfPresent(Bool.self, forKey: .permanent)) ?? false
        type = try? c.decodeIfPresent(String.self, forKey: .type)
        hardness = try? c.decodeIfPresent(String.self, forKey: .hardness)
        // 缺 id → 内容派生的确定性 id（随机 UUID 会让身份每次 reload 漂移）
        id = (try? c.decode(String.self, forKey: .id))
            ?? stableFallbackID("trash", title, summary, kind, trashed_at, trash_reason)
    }

    /// Plain-language headline shown by default.
    var displaySummary: String {
        if let s = summary, !s.isEmpty { return s }
        return title
    }
}

// v0.20 card-lifecycle §5: archived item. Mirrors TrashItem (sealed, off-board
// like trash) PLUS archive-specific fields. Sourced from load_archived(),
// newest-first by archived_at. All fields decodeIfPresent → backward-compatible
// (old payloads without `archived` decode to [] at the Dashboard level).
struct ArchivedItem: Decodable, Hashable {
    let id: String
    let title: String
    let summary: String?
    let kind: String?          // "suggestion" | "debt" | "proposal" …
    let trashed_at: String?    // mirrored from TrashItem (usually absent here)
    let trash_reason: String?  // mirrored from TrashItem (usually absent here)
    let permanent: Bool
    let type: String?
    let hardness: String?
    // archive-specific
    let archived_at: String?     // ISO8601 — sort key + relative-age display
    let archive_reason: String?  // "user" (你封存) | "auto" (自动封存)
    let prev_status: String?     // lane to restore into on unarchive (usually delivered)

    private enum CodingKeys: String, CodingKey {
        case id, title, summary, kind, trashed_at, trash_reason, permanent, type, hardness
        case archived_at, archive_reason, prev_status
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        summary = try? c.decodeIfPresent(String.self, forKey: .summary)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        trashed_at = try? c.decodeIfPresent(String.self, forKey: .trashed_at)
        trash_reason = try? c.decodeIfPresent(String.self, forKey: .trash_reason)
        permanent = (try? c.decodeIfPresent(Bool.self, forKey: .permanent)) ?? false
        type = try? c.decodeIfPresent(String.self, forKey: .type)
        hardness = try? c.decodeIfPresent(String.self, forKey: .hardness)
        archived_at = try? c.decodeIfPresent(String.self, forKey: .archived_at)
        archive_reason = try? c.decodeIfPresent(String.self, forKey: .archive_reason)
        prev_status = try? c.decodeIfPresent(String.self, forKey: .prev_status)
        // 缺 id → 内容派生的确定性 id（随机 UUID 会让身份每次 reload 漂移）
        id = (try? c.decode(String.self, forKey: .id))
            ?? stableFallbackID("archived", title, summary, kind, archived_at, prev_status)
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
    // §30: live working agent on this review card = user attach / organic
    // session activity — NOT a rework round. Absent (older actd) = false.
    let session_active: Bool

    private enum CodingKeys: String, CodingKey {
        case id, name, summary, dod, session_id, short_id, copy_cmd, cwd, agent_name
        case delivered_summary, final_draft, plan, sources, log, dispatched_at, review_at, delivery_mode
        case session_active
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
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
        session_active = (try? c.decodeIfPresent(Bool.self, forKey: .session_active)) ?? false
        // 缺 id → 内容派生的确定性 id（随机 UUID 会让身份每次 reload 漂移）
        id = (try? c.decode(String.self, forKey: .id))
            ?? stableFallbackID("review", name, summary, session_id,
                                dispatched_at.map(String.init))
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
        ids = (try? c.decodeIfPresent([String].self, forKey: .ids)) ?? []
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "analyzing"
        verdict = try? c.decodeIfPresent(String.self, forKey: .verdict)
        primary = try? c.decodeIfPresent(String.self, forKey: .primary)
        rationale = try? c.decodeIfPresent(String.self, forKey: .rationale)
        action_plan = (try? c.decodeIfPresent([String].self, forKey: .action_plan)) ?? []
        confidence = try? c.decodeIfPresent(String.self, forKey: .confidence)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        requested_at = try? c.decodeIfPresent(Int.self, forKey: .requested_at)
        // 缺 id → 内容派生的确定性 id。注意不掺 status/verdict：analyzing→done
        // 的状态推进不应换身份（否则 pendingMergeActions 记账又会失配）。
        id = (try? c.decode(String.self, forKey: .id))
            ?? stableFallbackID("merge", ids.joined(separator: "|"),
                                requested_at.map(String.init))
    }
}

// CONTRACT §26 — optional top-level dashboard field. Present ONLY when actd
// knows a strictly newer release (updates.check_enabled on); absent = no
// known update. The app only ever OPENS the release page — never downloads.
struct UpdateInfo: Decodable, Hashable {
    let current: String?
    let latest: String
    let url: String?
    let pkg_asset_url: String?

    private enum CodingKeys: String, CodingKey {
        case current, latest, url, pkg_asset_url
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        latest = (try? c.decode(String.self, forKey: .latest)) ?? ""
        current = try? c.decodeIfPresent(String.self, forKey: .current)
        url = try? c.decodeIfPresent(String.self, forKey: .url)
        pkg_asset_url = try? c.decodeIfPresent(String.self, forKey: .pkg_asset_url)
    }

    /// Release page to open (§26: open, never auto-download).
    var releaseURL: URL? {
        URL(string: url ?? "https://github.com/Wan-ZL/zelin-ai-assistant/releases")
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
    let archived: Int   // v0.20 card-lifecycle — sealed/off-board like trash

    private enum CodingKeys: String, CodingKey {
        case needs_approval, running, needs_input, review, completed, debt, trash
        case archived
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
        archived = (try? c.decodeIfPresent(Int.self, forKey: .archived)) ?? 0
    }

    static let empty = Counts()
    private init() {
        needs_approval = 0; running = 0; needs_input = 0; review = 0
        completed = 0; debt = 0; trash = 0; archived = 0
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
    // v0.20 card-lifecycle — sealed archived items (off-board like trash).
    // Optional 分区，缺失时解码为 []（向后兼容 old payloads）。
    let archived: [ArchivedItem]
    // 契约 merge-review §六 — optional 分区，缺失时解码为 []（向后兼容）。
    let merge_suggestions: [MergeSuggestion]
    // §26 — optional; nil = no known update (older actd never emits it).
    let update_available: UpdateInfo?
    // 非 wire 字段：行级解码时被跳过的坏行清单（如 "running[1]"）。空 = 全部
    // 解码成功。上层（Store/AppState）用它把「丢了哪些行」亮出来——honest
    // fallback：跳过 + 可观测，绝不静默丢数据。
    let decodeDrops: [String]

    private enum CodingKeys: String, CodingKey {
        case generated_at, counts, needs_approval, running, needs_input, review, completed, debt, trash
        case archived
        case merge_suggestions, update_available
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        var drops: [String] = []
        generated_at = try? c.decodeIfPresent(String.self, forKey: .generated_at)
        counts = (try? c.decodeIfPresent(Counts.self, forKey: .counts)) ?? .empty
        // 行级 lenient：单行坏数据跳过该行并记录，好行存活（之前是整列清空）。
        needs_approval = decodeLossyRows(c, CodingKeys.needs_approval, drops: &drops)
        running = decodeLossyRows(c, CodingKeys.running, drops: &drops)
        needs_input = decodeLossyRows(c, CodingKeys.needs_input, drops: &drops)
        review = decodeLossyRows(c, CodingKeys.review, drops: &drops)
        completed = decodeLossyRows(c, CodingKeys.completed, drops: &drops)
        debt = decodeLossyRows(c, CodingKeys.debt, drops: &drops)
        trash = decodeLossyRows(c, CodingKeys.trash, drops: &drops)
        archived = decodeLossyRows(c, CodingKeys.archived, drops: &drops)
        merge_suggestions = decodeLossyRows(c, CodingKeys.merge_suggestions, drops: &drops)
        // an empty latest is meaningless — treat as "no known update"
        let upd = try? c.decodeIfPresent(UpdateInfo.self, forKey: .update_available)
        update_available = (upd?.latest.isEmpty == false) ? upd : nil
        decodeDrops = drops
        if !drops.isEmpty {   // Foundation-only 契约内的兜底可观测（mac + iOS 都走这）
            NSLog("[Contract] dashboard.json 坏行已跳过: %@", drops.joined(separator: ", "))
        }
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
