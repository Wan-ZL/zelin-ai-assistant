// InboxAction.swift — the ONE encoder for state/inbox/<id>.json action files.
// SHARED between the Mac app and the iOS app. Foundation-only by contract.
//
// The two-file contract (docs/CONTRACT.md): a client writes an inbox action
// file that actd.process_inbox() consumes. Card decisions are
//   {"id": <req_id>, "action": <verb>, "ts": <ISO8601>, "comment": <str|null>}
// (mirrors mac/Sources/AppDelegate.writeInbox verbatim), quick-capture is
//   {"action": "capture", "text": <str>, "ts": <ISO8601>}.
// On iOS this JSON is the AEAD *plaintext* that E2E.encryptAction seals before
// it is POSTed to inbox_actions; syncd decrypts it back to these exact bytes and
// drops it at state/inbox/<action_id>.json for actd. So the schema here is the
// cross-client contract, not an iOS detail.

import Foundation

/// The action verbs actd.process_inbox / _apply_decision accept. Raw values are
/// the exact strings written into the `action` field (verified against
/// act/actd.py). `.defer` needs backticks (Swift keyword).
enum InboxVerb: String, CaseIterable {
    case approve, reject, comment
    case `defer`
    case raise, trash, restore, pin
    case accept, rework
    case done_external
    case abort_execution, stop_to_review
    case revert_review
    case archive, unarchive
    case capture
}

enum InboxAction {
    /// ISO8601 (UTC, seconds) timestamp — same format as
    /// `ISO8601DateFormatter().string(from: Date())` used by the Mac app.
    static func nowTimestamp(_ date: Date = Date()) -> String {
        let f = ISO8601DateFormatter()
        return f.string(from: date)
    }

    /// A card-decision action: {id, action, ts, comment}. `comment` becomes JSON
    /// null when nil (matches AppDelegate.writeInbox, which always writes the
    /// key). Deterministic key order (sorted) so equal actions serialize equally.
    static func card(id: String, verb: InboxVerb, comment: String?,
                     ts: String = InboxAction.nowTimestamp()) -> Data {
        var obj: [String: Any] = ["id": id, "action": verb.rawValue, "ts": ts]
        obj["comment"] = comment ?? NSNull()
        return encode(obj)
    }

    /// A quick-capture action: {action:"capture", text, ts}.
    static func capture(text: String, ts: String = InboxAction.nowTimestamp()) -> Data {
        encode(["action": InboxVerb.capture.rawValue, "text": text, "ts": ts])
    }

    // merge-review 契约 §21 — suggestion-level actions (not card verbs): the
    // `id` is the MS- suggestion id; merge_force instead carries the raw card
    // ids + the user-chosen primary. actd reads decision["id"] / ["ids"]+["primary"].

    /// Accept an AI merge suggestion: {action:"merge_apply", id, ts}.
    static func mergeApply(suggestionId: String,
                           ts: String = InboxAction.nowTimestamp()) -> Data {
        encode(["action": "merge_apply", "id": suggestionId, "ts": ts])
    }

    /// Dismiss an AI merge suggestion: {action:"merge_dismiss", id, ts}.
    static func mergeDismiss(suggestionId: String,
                             ts: String = InboxAction.nowTimestamp()) -> Data {
        encode(["action": "merge_dismiss", "id": suggestionId, "ts": ts])
    }

    /// 契约 §21bis 强制合并: user-chosen primary, skips the AI —
    /// {action:"merge_force", ids, primary, ts}. actd validates ids≥2 distinct,
    /// all exist, primary ∈ ids (else drops the request).
    static func mergeForce(ids: [String], primary: String,
                           ts: String = InboxAction.nowTimestamp()) -> Data {
        encode(["action": "merge_force", "ids": ids, "primary": primary, "ts": ts])
    }

    /// Serialize with sorted keys so the ciphertext is a pure function of the
    /// logical action (no dictionary-ordering nondeterminism across runs).
    static func encode(_ obj: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])) ?? Data()
    }
}
