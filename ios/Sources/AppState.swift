// AppState.swift — the app's single source of truth (plan §6). Owns the auth
// session, the scanned pairings (K_i in Keychain), the device list + freshness,
// the currently-selected device's decrypted board, and the action/capture write
// path. Sync is OFF by default and only turns on after the explicit opt-in.

import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    // opt-in gate (plan §7.3): default OFF, flipped only by the consent screen.
    // Backed by UserDefaults but @Published (unlike @AppStorage, which does not
    // republish from inside an ObservableObject) so the root routing reacts.
    static let syncKey = "sync_enabled"
    @Published var syncEnabled: Bool = UserDefaults.standard.bool(forKey: AppState.syncKey) {
        didSet { UserDefaults.standard.set(syncEnabled, forKey: AppState.syncKey) }
    }

    @Published var session: Session?
    @Published var pairings: [String: Pairing] = [:]        // deviceId → pairing
    @Published var devices: [DeviceRow] = []
    @Published var heartbeats: [String: HeartbeatRow] = [:]  // deviceId → heartbeat
    @Published var selectedDeviceId: String?
    @Published var board: Dashboard?
    @Published var boardSeq: Int?
    @Published var lastError: String?
    @Published var isBusy = false

    /// Tracks needs_approval count so we can fire a local notification on a rise.
    private var lastApprovalCount = 0

    let client = SupabaseClient()

    private let sessionAccount = "session"
    private let pairingPrefix = "pairing."

    init() {
        loadSession()
        loadPairings()
    }

    var isSignedIn: Bool { session != nil }

    var selectedPairing: Pairing? { selectedDeviceId.flatMap { pairings[$0] } }

    // MARK: - Auth -------------------------------------------------------------
    func sendOTP(email: String) async -> Bool {
        await run { try await self.client.sendOTP(email: email) }
    }

    func verifyOTP(email: String, code: String) async -> Bool {
        let ok = await run {
            let s = try await self.client.verifyOTP(email: email, code: code)
            self.session = s
            self.persistSession(s)
        }
        if ok { await refreshEverything() }
        return ok
    }

    func signOut() {
        session = nil
        client.auth = nil
        Keychain.delete(sessionAccount)
        devices = []; board = nil; selectedDeviceId = nil
    }

    /// Refresh the JWT if it is (nearly) expired before an authed call.
    private func ensureFreshSession() async {
        guard let s = session, s.isExpired else { return }
        _ = await run {
            let ns = try await self.client.refresh()
            self.session = ns
            self.persistSession(ns)
        }
    }

    // MARK: - Pairing (scanned QR) --------------------------------------------
    /// Store a scanned pairing (K_i → Keychain, ThisDeviceOnly). The QR blob was
    /// already parsed by E2E.parsePairingBlob in the pairing view.
    func addPairing(_ info: E2E.PairingInfo) {
        let p = Pairing(deviceId: info.deviceId, epoch: info.epoch, key: info.key, label: info.label)
        pairings[p.deviceId] = p
        let payload: [String: Any] = [
            "epoch": Int(info.epoch),
            "key": info.key.base64EncodedString(),
            "label": info.label,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: payload) {
            Keychain.set(data, account: pairingPrefix + info.deviceId)
        }
        if selectedDeviceId == nil { selectedDeviceId = p.deviceId }
    }

    /// Unpair a device: forget K_i (plan §6.6 — 擦除密钥). The board becomes
    /// unreadable until re-scanned.
    func unpair(deviceId: String) {
        pairings.removeValue(forKey: deviceId)
        Keychain.delete(pairingPrefix + deviceId)
        if selectedDeviceId == deviceId { selectedDeviceId = pairings.keys.first }
        if selectedDeviceId == nil { board = nil }
    }

    func wipeAllKeys() {
        for id in Array(pairings.keys) { unpair(deviceId: id) }
    }

    private func loadPairings() {
        var loaded: [String: Pairing] = [:]
        for acct in Keychain.accounts(prefix: pairingPrefix) {
            let deviceId = String(acct.dropFirst(pairingPrefix.count))
            guard let data = Keychain.get(acct),
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let epoch = obj["epoch"] as? Int,
                  let keyB64 = obj["key"] as? String, let key = Data(base64Encoded: keyB64)
            else { continue }
            loaded[deviceId] = Pairing(deviceId: deviceId, epoch: UInt32(epoch), key: key,
                                       label: (obj["label"] as? String) ?? deviceId)
        }
        pairings = loaded
        if selectedDeviceId == nil { selectedDeviceId = loaded.keys.sorted().first }
    }

    // MARK: - Data refresh -----------------------------------------------------
    func refreshEverything() async {
        guard syncEnabled, isSignedIn else { return }
        await ensureFreshSession()
        await refreshDevices()
        await refreshHeartbeats()
        await refreshBoard()
    }

    func refreshDevices() async {
        _ = await run { self.devices = try await self.client.fetchDevices() }
        if selectedDeviceId == nil { selectedDeviceId = devices.first?.id }
    }

    func refreshHeartbeats() async {
        _ = await run {
            let hbs = try await self.client.fetchHeartbeats()
            self.heartbeats = Dictionary(uniqueKeysWithValues: hbs.map { ($0.device_id, $0) })
        }
    }

    func refreshBoard() async {
        guard let id = selectedDeviceId, let pairing = pairings[id] else { board = nil; return }
        _ = await run {
            guard let row = try await self.client.fetchBoard(deviceId: id) else { self.board = nil; return }
            guard let blob = PgBytea.decode(row.payload_enc) else {
                throw SupabaseError.decode("board payload not decodable bytea")
            }
            let plaintext = try E2E.decryptBoard(kI: pairing.key, epoch: pairing.epoch,
                                                 deviceId: id, seq: row.seq, blob: blob)
            let dash = try JSONDecoder().decode(Dashboard.self, from: plaintext)
            self.board = dash
            self.boardSeq = row.seq
            self.maybeNotify(dash)
        }
    }

    private func maybeNotify(_ dash: Dashboard) {
        let n = dash.counts.needs_approval
        if n > lastApprovalCount {
            LocalNotifications.notifyNewProposals(delta: n - lastApprovalCount, total: n)
        }
        LocalNotifications.setBadge(n)
        lastApprovalCount = n
    }

    /// Freshness for a device (server-clock adjusted).
    func freshness(for deviceId: String) -> Freshness {
        let hb = heartbeats[deviceId]
        let bs = deviceId == selectedDeviceId ? boardSeq : nil
        return Freshness.compute(beatAt: parseISO(hb?.beat_at),
                                 lastPushedSeq: hb?.last_pushed_seq,
                                 boardSeq: bs, now: ServerClock.shared.now)
    }

    func label(for device: DeviceRow) -> String {
        if let p = pairings[device.id] { return p.label }
        return L("未配对", "Not paired")
    }

    // MARK: - UP: actions + capture -------------------------------------------
    /// Write an inbox action addressed to the currently-selected device. The
    /// target is pinned to `selectedDeviceId` at call time (plan §5.3).
    func submit(cardId: String, verb: InboxVerb, comment: String? = nil) async -> Bool {
        guard let target = selectedDeviceId, let pairing = pairings[target] else {
            lastError = L("请先选择并配对一台设备", "Pick and pair a device first"); return false
        }
        await ensureFreshSession()
        let actionId = UUID().uuidString.lowercased()
        let ts = InboxAction.nowTimestamp()
        let seq = boardSeq
        return await run {
            let plaintext = InboxAction.card(id: cardId, verb: verb, comment: comment, ts: ts)
            let blob = try E2E.encryptAction(kI: pairing.key, epoch: pairing.epoch,
                                             deviceId: target, actionId: actionId,
                                             boardSeq: seq, plaintext: plaintext)
            try await self.client.postInboxAction(actionId: actionId, targetDeviceId: target,
                                                  boardSeq: seq, blob: blob, clientTs: ts)
        }
    }

    func submitCapture(_ text: String) async -> Bool {
        guard let target = selectedDeviceId, let pairing = pairings[target] else { return false }
        await ensureFreshSession()
        let actionId = UUID().uuidString.lowercased()
        let ts = InboxAction.nowTimestamp()
        let seq = boardSeq
        return await run {
            let plaintext = InboxAction.capture(text: text, ts: ts)
            let blob = try E2E.encryptAction(kI: pairing.key, epoch: pairing.epoch,
                                             deviceId: target, actionId: actionId,
                                             boardSeq: seq, plaintext: plaintext)
            try await self.client.postInboxAction(actionId: actionId, targetDeviceId: target,
                                                  boardSeq: seq, blob: blob, clientTs: ts)
        }
    }

    // MARK: - session persistence ----------------------------------------------
    private func loadSession() {
        guard let data = Keychain.get(sessionAccount),
              let s = try? JSONDecoder().decode(Session.self, from: data) else { return }
        session = s
        client.auth = s
    }

    private func persistSession(_ s: Session) {
        client.auth = s
        if let data = try? JSONEncoder().encode(s) { Keychain.set(data, account: sessionAccount) }
    }

    // MARK: - error-catching runner --------------------------------------------
    /// Run an async throwing block, surfacing errors into `lastError` and
    /// toggling `isBusy`. Returns true on success.
    @discardableResult
    private func run(_ block: @escaping () async throws -> Void) async -> Bool {
        isBusy = true; defer { isBusy = false }
        do { try await block(); lastError = nil; return true }
        catch { lastError = "\(error)"; return false }
    }
}
