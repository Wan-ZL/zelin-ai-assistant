// SupabaseClient.swift — the phone's Supabase transport (QR-only v2). URLSession
// only, NO Supabase SDK dependency. No GoTrue / no account: the phone talks to
// PostgREST as the ANON role, and the capability is carried in per-request
// headers instead of a JWT:
//
//   apikey: <anon>                    (every request)
//   Authorization: Bearer <anon>      (every request)
//   x-sync-channel: <channel_id>      (every request — selects the channel)
//   x-sync-write: <write_secret b64url>  (writes only — proves the write cap)
//
// RLS then enforces: read board_snapshots / inbox_actions by knowing the
// (unguessable) channel_id; INSERT inbox_actions only when x-sync-write hashes
// to the channel's stored write_secret_hash. Bodies are E2E-encrypted with K
// (QR-only), so the server sees only ciphertext.

import Foundation

enum SupabaseError: Error, CustomStringConvertible {
    case http(Int, String)
    case decode(String)
    var description: String {
        switch self {
        case .http(let c, let b): return "HTTP \(c): \(b)"
        case .decode(let m): return "decode error: \(m)"
        }
    }
}

final class SupabaseClient {
    // Project + publishable (anon) key — the same public values the telemetry
    // path uses (act/lib/config.py). The publishable key is public by design;
    // RLS (anon INSERT-only on channels, no SELECT) makes it safe.
    static let baseURL = URL(string: "https://vlxshwmdjpaxmcwbhutb.supabase.co")!
    static let anonKey = "sb_publishable_bNWOKJTAH52AfwTao-nHUQ_jdsTUpYi"

    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    // MARK: - DOWN: read the board --------------------------------------------

    /// Fetch the single board snapshot for `channelId` (RLS scopes it by the
    /// x-sync-channel header). Returns nil if the Mac hasn't pushed yet.
    func fetchBoard(channelId: String) async throws -> BoardSnapshotRow? {
        let data = try await rest("/rest/v1/board_snapshots",
            query: "select=channel_id,seq,payload_enc,updated_at,schema_version&channel_id=eq.\(channelId)",
            channelId: channelId)
        return try decodeJSON([BoardSnapshotRow].self, data).first
    }

    // MARK: - UP: enqueue an inbox action -------------------------------------

    /// Append an inbox action for `channelId` (idempotent on action_id via
    /// ignore-duplicates). `blob` is the E2E-sealed action; the `nonce` column
    /// mirrors the blob's own embedded nonce. Requires the write header.
    func postInboxAction(actionId: String, channelId: String, writeSecret: Data,
                         boardSeq: Int?, blob: Data, clientTs: String) async throws {
        let nonceMirror = E2E.nonceSlice(of: blob) ?? Data()
        var row: [String: Any] = [
            "action_id": actionId,
            "channel_id": channelId,
            "payload_enc": PgBytea.encode(blob),
            "nonce": PgBytea.encode(nonceMirror),
            "client_ts": clientTs,
        ]
        row["board_seq"] = boardSeq ?? NSNull()
        _ = try await rest("/rest/v1/inbox_actions", method: "POST", body: [row],
                           channelId: channelId, writeSecret: writeSecret,
                           extraHeaders: ["Prefer": "resolution=ignore-duplicates"])
    }

    // MARK: - low-level --------------------------------------------------------

    /// x-sync-write carries the 32-byte write_secret as base64url (no padding) —
    /// the same canonical serialization the Mac persists at state/sync/write_secret
    /// and hashes into channels.write_secret_hash.
    static func writeSecretHeader(_ secret: Data) -> String {
        var s = secret.base64EncodedString()
        s = s.replacingOccurrences(of: "+", with: "-")
             .replacingOccurrences(of: "/", with: "_")
        while s.hasSuffix("=") { s.removeLast() }
        return s
    }

    private func rest(_ path: String, query: String? = nil, method: String = "GET",
                      body: Any? = nil, channelId: String, writeSecret: Data? = nil,
                      extraHeaders: [String: String] = [:]) async throws -> Data {
        var comps = URLComponents(url: Self.baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if let query { comps.percentEncodedQuery = query }
        return try await request(url: comps.url!, method: method, body: body,
                                 channelId: channelId, writeSecret: writeSecret,
                                 extraHeaders: extraHeaders)
    }

    private func request(url: URL, method: String, body: Any?, channelId: String,
                         writeSecret: Data?, extraHeaders: [String: String]) async throws -> Data {
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(Self.anonKey, forHTTPHeaderField: "apikey")
        req.setValue("Bearer \(Self.anonKey)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(channelId, forHTTPHeaderField: "x-sync-channel")
        if let writeSecret {
            req.setValue(Self.writeSecretHeader(writeSecret), forHTTPHeaderField: "x-sync-write")
        }
        for (k, v) in extraHeaders { req.setValue(v, forHTTPHeaderField: k) }
        if let body { req.httpBody = try JSONSerialization.data(withJSONObject: body) }

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw SupabaseError.http(-1, "no response") }
        // Keep the freshness clock aligned to the server (plan §5.6).
        ServerClock.shared.update(fromHTTPDate: http.value(forHTTPHeaderField: "Date"))
        guard (200..<300).contains(http.statusCode) else {
            throw SupabaseError.http(http.statusCode, String(decoding: data, as: UTF8.self))
        }
        return data
    }

    private func decodeJSON<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try JSONDecoder().decode(type, from: data) }
        catch { throw SupabaseError.decode("\(error): \(String(decoding: data, as: UTF8.self))") }
    }
}
