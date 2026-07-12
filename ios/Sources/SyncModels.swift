// SyncModels.swift — the Supabase row shapes the phone reads/writes, plus the
// stored-pairing model and the PostgREST bytea <-> Data bridge.
//
// PostgREST renders a `bytea` column as PostgreSQL's hex text (`\x…`) in JSON,
// and accepts the same `\x…` form on write. Our E2E blobs are self-contained
// (they carry their own nonce), so `payload_enc` / `label_enc` hold the WHOLE
// blob and the sibling `nonce` columns just mirror the embedded 12-byte nonce
// (kept NOT NULL by the schema). We decode the blob column and hand it straight
// to E2E; we fill `nonce` from the blob's own nonce slice on write.

import Foundation

/// A pairing the phone scanned (K_i lives in Keychain; this is the in-memory
/// view). `epoch` must match `devices.key_epoch` for decryption to succeed.
struct Pairing: Identifiable, Equatable {
    let deviceId: String
    let epoch: UInt32
    let key: Data          // K_i, 32 bytes (from Keychain)
    var label: String      // decrypted device label
    var id: String { deviceId }
}

/// `devices` row (owner-scoped SELECT). label_enc decrypts to the human label
/// only if we hold this device's K_i; otherwise it renders as "未配对".
struct DeviceRow: Decodable, Identifiable {
    let id: String
    let platform: String
    let key_epoch: Int
    let last_seen_at: String?
    let label_enc: String?   // bytea hex, may be nil for our own not-yet-pushed row
    var idValue: String { id }
}

/// `board_snapshots` row. `payload_enc` is the full E2E board blob; `seq` is the
/// plaintext monotonic counter that also feeds the board AAD.
struct BoardSnapshotRow: Decodable {
    let device_id: String
    let seq: Int
    let payload_enc: String     // bytea hex — full E2E blob
    let updated_at: String?
    let schema_version: Int?
}

/// `device_heartbeats` row — freshness authority (server clock).
struct HeartbeatRow: Decodable {
    let device_id: String
    let beat_at: String?
    let last_pushed_seq: Int?
    let daemon_version: String?
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
