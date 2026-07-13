// SyncModels.swift — the Supabase row shapes the phone reads/writes, plus the
// stored-channel model and the PostgREST bytea <-> Data bridge (QR-only v2).
//
// v2 capability model: each paired Mac is a CHANNEL. The QR carries
// {channel_id, epoch, write_secret, K, label}; the phone stores it in Keychain
// and talks to Supabase as the anon role with per-request headers
// (x-sync-channel, and x-sync-write on writes). There is no account, no device
// registry and no heartbeat table — liveness is `board_snapshots.updated_at`.
//
// PostgREST renders a `bytea` column as PostgreSQL's hex text (`\x…`) in JSON,
// and accepts the same `\x…` form on write. Our E2E blobs are self-contained
// (they carry their own nonce), so `payload_enc` holds the WHOLE blob and the
// sibling `nonce` column just mirrors the embedded 12-byte nonce (kept NOT NULL
// by the schema). We decode the blob column and hand it straight to E2E; we fill
// `nonce` from the blob's own nonce slice on write.

import Foundation

/// A paired channel = one Mac. `writeSecret` + `key` live in the Keychain; this
/// is the in-memory view. `channelId` is the canonical lowercase UUID string.
struct Channel: Identifiable, Equatable {
    let channelId: String
    let epoch: UInt32
    let writeSecret: Data  // 32 bytes — the write capability (x-sync-write)
    let key: Data          // K, 32 bytes — the E2E decrypt key (never uploaded)
    var label: String      // human label carried in the QR (not encrypted in v2)
    var id: String { channelId }
}

/// `board_snapshots` row (one per channel, keyed by channel_id). `payload_enc`
/// is the full E2E board blob; `seq` is the plaintext monotonic counter that also
/// feeds the board AAD; `updated_at` (server clock) is the liveness authority.
struct BoardSnapshotRow: Decodable {
    let channel_id: String
    let seq: Int
    let payload_enc: String     // bytea hex — full E2E blob
    let updated_at: String?
    let schema_version: Int?
}

// MARK: - bytea <-> Data (PostgREST hex text form) ---------------------------
enum PgBytea {
    /// Decode a PostgREST bytea field (`\x48656c6c6f` or bare hex, or base64
    /// fallback) into raw bytes.
    static func decode(_ s: String) -> Data? {
        var hex = s
        if hex.hasPrefix("\\x") { hex.removeFirst(2) }
        else if hex.hasPrefix("\\\\x") { hex.removeFirst(3) }
        if let d = hexToData(hex) { return d }
        return Data(base64Encoded: s)   // tolerate a base64 representation too
    }

    /// Encode raw bytes into the `\x…` hex literal PostgREST accepts for bytea.
    static func encode(_ data: Data) -> String {
        "\\x" + data.map { String(format: "%02x", $0) }.joined()
    }

    private static func hexToData(_ hex: String) -> Data? {
        let chars = Array(hex)
        guard chars.count % 2 == 0 else { return nil }
        var out = Data(capacity: chars.count / 2)
        var i = 0
        while i < chars.count {
            guard let hi = chars[i].hexDigitValue, let lo = chars[i + 1].hexDigitValue else { return nil }
            out.append(UInt8(hi << 4 | lo))
            i += 2
        }
        return out
    }
}
