// SupabaseClient.swift — the phone's Supabase transport. URLSession only, NO
// Supabase SDK dependency (plan §6 / item 4). Two surfaces:
//   * GoTrue auth  : email-OTP login → a user JWT (owner = auth.uid()).
//   * PostgREST    : owner-scoped reads of devices / board_snapshots /
//                    device_heartbeats and append-only writes to inbox_actions.
//
// The phone authenticates as the OWNER (email OTP), not as a device: RLS
// `ia_ins` lets the owner INSERT inbox_actions, and `bs_sel`/`dev_all`/`hb_sel`
// let the owner read all their devices' rows. Device tokens (exchange_device_token)
// are the headless daemon's path and are NOT needed by the phone — a helper is
// included only for parity/testing.

import Foundation

/// Persisted auth session (Keychain-backed). `userId` = owner = auth.uid().
struct Session: Codable, Equatable {
    var accessToken: String
    var refreshToken: String
    var expiresAt: Date
    var userId: String
    var email: String

    var isExpired: Bool { Date() >= expiresAt.addingTimeInterval(-60) }
}

enum SupabaseError: Error, CustomStringConvertible {
    case http(Int, String)
    case decode(String)
    case notAuthenticated
    var description: String {
        switch self {
        case .http(let c, let b): return "HTTP \(c): \(b)"
        case .decode(let m): return "decode error: \(m)"
        case .notAuthenticated: return "not signed in"
        }
    }
}

final class SupabaseClient {
    // Project + publishable (anon) key — the same public values the telemetry
    // path uses (act/lib/config.py). The publishable key is public by design.
    static let baseURL = URL(string: "https://vlxshwmdjpaxmcwbhutb.supabase.co")!
    static let anonKey = "sb_publishable_bNWOKJTAH52AfwTao-nHUQ_jdsTUpYi"

    private let session: URLSession
    /// Current auth session, or nil when signed out. Set by verify/refresh.
    var auth: Session?

    init(session: URLSession = .shared, auth: Session? = nil) {
        self.session = session
        self.auth = auth
    }

    // MARK: - GoTrue email OTP -------------------------------------------------

    /// Request a 6-digit code be emailed to `email`. `create_user:true` lets a
    /// brand-new owner sign up on first login.
    func sendOTP(email: String) async throws {
        _ = try await call(path: "/auth/v1/otp", method: "POST", authed: false,
                           body: ["email": email, "create_user": true])
    }

    /// Verify the emailed code → a user session (owner JWT). Stores it in `auth`.
    @discardableResult
    func verifyOTP(email: String, code: String) async throws -> Session {
        let data = try await call(path: "/auth/v1/verify", method: "POST", authed: false,
                                  body: ["email": email, "token": code, "type": "email"])
        let s = try parseSession(data, fallbackEmail: email)
        auth = s
        return s
    }

    /// Refresh an expired session using the stored refresh token.
    @discardableResult
    func refresh() async throws -> Session {
        guard let rt = auth?.refreshToken else { throw SupabaseError.notAuthenticated }
        let data = try await call(path: "/auth/v1/token?grant_type=refresh_token", method: "POST",
                                  authed: false, body: ["refresh_token": rt])
        let s = try parseSession(data, fallbackEmail: auth?.email ?? "")
        auth = s
        return s
    }

    private func parseSession(_ data: Data, fallbackEmail: String) throws -> Session {
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let access = obj["access_token"] as? String,
              let refresh = obj["refresh_token"] as? String
        else { throw SupabaseError.decode("auth response missing tokens: \(String(decoding: data, as: UTF8.self))") }
        let ttl = (obj["expires_in"] as? Double) ?? 3600
        let user = obj["user"] as? [String: Any]
        return Session(
            accessToken: access, refreshToken: refresh,
            expiresAt: Date().addingTimeInterval(ttl),
            userId: (user?["id"] as? String) ?? "",
            email: (user?["email"] as? String) ?? fallbackEmail)
    }

    // MARK: - PostgREST reads --------------------------------------------------

    func fetchDevices() async throws -> [DeviceRow] {
        let data = try await rest("/rest/v1/devices",
            query: "select=id,platform,key_epoch,last_seen_at,label_enc&order=created_at.asc")
        return try decodeJSON([DeviceRow].self, data)
    }

    func fetchBoard(deviceId: String) async throws -> BoardSnapshotRow? {
        let data = try await rest("/rest/v1/board_snapshots",
            query: "select=device_id,seq,payload_enc,updated_at,schema_version&device_id=eq.\(deviceId)")
        return try decodeJSON([BoardSnapshotRow].self, data).first
    }

    func fetchHeartbeats() async throws -> [HeartbeatRow] {
        let data = try await rest("/rest/v1/device_heartbeats",
            query: "select=device_id,beat_at,last_pushed_seq,daemon_version")
        return try decodeJSON([HeartbeatRow].self, data)
    }

    // MARK: - PostgREST write (UP) --------------------------------------------

    /// Append an inbox action (idempotent on action_id via ignore-duplicates).
    /// `blob` is the E2E-sealed action; `nonceMirror` is the blob's own nonce
    /// slice (the schema keeps a NOT NULL `nonce` column mirroring it).
    func postInboxAction(actionId: String, targetDeviceId: String, boardSeq: Int?,
                         blob: Data, clientTs: String) async throws {
        guard let owner = auth?.userId, !owner.isEmpty else { throw SupabaseError.notAuthenticated }
        let nonceMirror = E2E.nonceSlice(of: blob) ?? Data()
        var row: [String: Any] = [
            "action_id": actionId,
            "owner": owner,
            "target_device_id": targetDeviceId,
            "payload_enc": PgBytea.encode(blob),
            "nonce": PgBytea.encode(nonceMirror),
            "client_ts": clientTs,
        ]
        row["board_seq"] = boardSeq ?? NSNull()
        _ = try await rest("/rest/v1/inbox_actions", method: "POST",
                           body: [row], extraHeaders: ["Prefer": "resolution=ignore-duplicates"])
    }

    // MARK: - low-level --------------------------------------------------------

    private func rest(_ path: String, query: String? = nil, method: String = "GET",
                      body: Any? = nil, extraHeaders: [String: String] = [:]) async throws -> Data {
        var comps = URLComponents(url: Self.baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if let query { comps.percentEncodedQuery = query }
        return try await request(url: comps.url!, method: method, authed: true, body: body, extraHeaders: extraHeaders)
    }

    private func call(path: String, method: String, authed: Bool, body: Any?) async throws -> Data {
        try await request(url: Self.baseURL.appendingPathComponent(path), method: method,
                          authed: authed, body: body, extraHeaders: [:])
    }

    private func request(url: URL, method: String, authed: Bool, body: Any?,
                         extraHeaders: [String: String]) async throws -> Data {
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(Self.anonKey, forHTTPHeaderField: "apikey")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if authed, let token = auth?.accessToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        } else {
            req.setValue("Bearer \(Self.anonKey)", forHTTPHeaderField: "Authorization")
        }
        for (k, v) in extraHeaders { req.setValue(v, forHTTPHeaderField: k) }
        if let body { req.httpBody = try JSONSerialization.data(withJSONObject: body) }

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw SupabaseError.http(-1, "no response") }
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
