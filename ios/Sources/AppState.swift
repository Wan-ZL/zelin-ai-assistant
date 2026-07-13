// AppState.swift — the app's single source of truth (QR-only v2). Owns the
// scanned CHANNELS (each = one Mac: write_secret + K in Keychain), the currently
// selected channel's decrypted board, and the action/capture write path. There
// is no account and no device registry: a channel is added by scanning a Mac's
// QR, and liveness comes from `board_snapshots.updated_at`. Sync is OFF by
// default and only turns on after the explicit consent screen.

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

    @Published var channels: [String: Channel] = [:]        // channelId → channel
    @Published var selectedChannelId: String?
    @Published var board: Dashboard?
    @Published var boardSeq: Int?
    @Published var lastError: String?
    @Published var isBusy = false

    /// Last-seen board `updated_at` per channel — the freshness authority.
    private var updatedAt: [String: Date] = [:]

    /// Tracks needs_approval count so we can fire a local notification on a rise.
    private var lastApprovalCount = 0

    let client = SupabaseClient()

    private let channelPrefix = "channel."

    init() {
        loadChannels()
    }

    var selectedChannel: Channel? { selectedChannelId.flatMap { channels[$0] } }

    // MARK: - Pairing (scanned QR) --------------------------------------------
    /// Store a scanned channel (write_secret + K → Keychain, ThisDeviceOnly).
    /// The QR blob was already parsed by E2E.parseChannelQR in the pairing view.
    /// Scanning another Mac just adds another channel (multi-channel).
    func addChannel(_ info: E2E.ChannelPairing) {
        let c = Channel(channelId: info.channelId, epoch: info.epoch,
                        writeSecret: info.writeSecret, key: info.key, label: info.label)
        channels[c.channelId] = c
        let payload: [String: Any] = [
            "epoch": Int(info.epoch),
            "write_secret": info.writeSecret.base64EncodedString(),
            "key": info.key.base64EncodedString(),
            "label": info.label,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: payload) {
            Keychain.set(data, account: channelPrefix + c.channelId)
        }
        if selectedChannelId == nil { selectedChannelId = c.channelId }
    }

    /// Unpair a channel: forget its keys (plan §6.6 — 擦除密钥). The board becomes
    /// unreadable until re-scanned.
    func unpair(channelId: String) {
        channels.removeValue(forKey: channelId)
        updatedAt.removeValue(forKey: channelId)
        Keychain.delete(channelPrefix + channelId)
        if selectedChannelId == channelId { selectedChannelId = channels.keys.sorted().first }
        if selectedChannelId == nil { board = nil }
    }

    func wipeAllKeys() {
        for id in Array(channels.keys) { unpair(channelId: id) }
    }

    private func loadChannels() {
        var loaded: [String: Channel] = [:]
        for acct in Keychain.accounts(prefix: channelPrefix) {
            let channelId = String(acct.dropFirst(channelPrefix.count))
            guard let data = Keychain.get(acct),
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let epoch = obj["epoch"] as? Int,
                  let wsB64 = obj["write_secret"] as? String, let ws = Data(base64Encoded: wsB64),
                  let keyB64 = obj["key"] as? String, let key = Data(base64Encoded: keyB64)
            else { continue }
            loaded[channelId] = Channel(channelId: channelId, epoch: UInt32(epoch),
                                        writeSecret: ws, key: key,
                                        label: (obj["label"] as? String) ?? channelId)
        }
        channels = loaded
        if selectedChannelId == nil { selectedChannelId = loaded.keys.sorted().first }
    }

    // MARK: - Data refresh -----------------------------------------------------
    func refreshEverything() async {
        guard syncEnabled else { return }
        await refreshBoard()
    }

    func refreshBoard() async {
        guard let id = selectedChannelId, let channel = channels[id] else { board = nil; return }
        _ = await run {
            guard let row = try await self.client.fetchBoard(channelId: id) else { self.board = nil; return }
            guard let blob = PgBytea.decode(row.payload_enc) else {
                throw SupabaseError.decode("board payload not decodable bytea")
            }
            // The record blob is UNCHANGED from v1; the channel_id plays the role
            // of the AAD identifier the Mac sealed under.
            let plaintext = try E2E.decryptBoard(kI: channel.key, epoch: channel.epoch,
                                                 deviceId: id, seq: row.seq, blob: blob)
            let dash = try JSONDecoder().decode(Dashboard.self, from: plaintext)
            self.board = dash
            self.boardSeq = row.seq
            if let u = parseISO(row.updated_at) { self.updatedAt[id] = u }
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

    /// Freshness for a channel, from its last-seen board `updated_at` (server
    /// clock). Channels not yet fetched read as `.unknown`.
    func freshness(for channelId: String) -> Freshness {
        Freshness.compute(updatedAt: updatedAt[channelId], now: ServerClock.shared.now)
    }

    func label(for channelId: String) -> String {
        channels[channelId]?.label ?? channelId
    }

    // MARK: - UP: actions + capture -------------------------------------------
    /// Write an inbox action addressed to the currently-selected channel. The
    /// target is pinned to `selectedChannelId` at call time (plan §5.3).
    func submit(cardId: String, verb: InboxVerb, comment: String? = nil) async -> Bool {
        guard let target = selectedChannelId, let channel = channels[target] else {
            lastError = L("请先选择并配对一台设备", "Pick and pair a device first"); return false
        }
        let actionId = UUID().uuidString.lowercased()
        let ts = InboxAction.nowTimestamp()
        let seq = boardSeq
        return await run {
            let plaintext = InboxAction.card(id: cardId, verb: verb, comment: comment, ts: ts)
            let blob = try E2E.encryptAction(kI: channel.key, epoch: channel.epoch,
                                             deviceId: target, actionId: actionId,
                                             boardSeq: seq, plaintext: plaintext)
            try await self.client.postInboxAction(actionId: actionId, channelId: target,
                                                  writeSecret: channel.writeSecret,
                                                  boardSeq: seq, blob: blob, clientTs: ts)
        }
    }

    func submitCapture(_ text: String) async -> Bool {
        guard let target = selectedChannelId, let channel = channels[target] else { return false }
        let actionId = UUID().uuidString.lowercased()
        let ts = InboxAction.nowTimestamp()
        let seq = boardSeq
        return await run {
            let plaintext = InboxAction.capture(text: text, ts: ts)
            let blob = try E2E.encryptAction(kI: channel.key, epoch: channel.epoch,
                                             deviceId: target, actionId: actionId,
                                             boardSeq: seq, plaintext: plaintext)
            try await self.client.postInboxAction(actionId: actionId, channelId: target,
                                                  writeSecret: channel.writeSecret,
                                                  boardSeq: seq, blob: blob, clientTs: ts)
        }
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
