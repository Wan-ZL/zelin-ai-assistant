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

    /// Highest board seq seen per channel — the anti-replay floor. Within an
    /// epoch the Mac's seq never regresses (§5.2), so a snapshot with a lower
    /// seq is a replayed old row and is ignored, never rendered as current.
    private var lastSeenSeq: [String: Int] = [:]

    /// Tracks needs_approval count per channel so we fire a local notification
    /// only on a rise within that channel (switching channels is not "new").
    private var lastApprovalCount: [String: Int] = [:]

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
        // A fresh QR scan is a new trust anchor; the seq floor restarts with it.
        lastSeenSeq.removeValue(forKey: c.channelId)
        if selectedChannelId == nil { selectedChannelId = c.channelId }
    }

    /// Unpair a channel: forget its keys (plan §6.6 — 擦除密钥). The board becomes
    /// unreadable until re-scanned.
    func unpair(channelId: String) {
        channels.removeValue(forKey: channelId)
        updatedAt.removeValue(forKey: channelId)
        lastSeenSeq.removeValue(forKey: channelId)
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
            let row = try await self.client.fetchBoard(channelId: id)
            // Drop a stale in-flight response: the user switched channels (or
            // unpaired this one) while the fetch was in the air — applying it
            // would show channel A's board under channel B's label and pin
            // subsequent actions to the wrong target.
            guard self.selectedChannelId == id, self.channels[id] != nil else { return }
            guard let row else { self.board = nil; return }
            guard let blob = PgBytea.decode(row.payload_enc) else {
                throw SupabaseError.decode("board payload not decodable bytea")
            }
            // Anti-replay floor: an intact old (seq, blob) tuple decrypts fine,
            // so refuse to go backwards — equal seq is the same snapshot
            // re-polled and still refreshes updated_at.
            if let floor = self.lastSeenSeq[id], row.seq < floor { return }
            // The record blob is UNCHANGED from v1; the channel_id plays the role
            // of the AAD identifier the Mac sealed under.
            let plaintext = try E2E.decryptBoard(kI: channel.key, epoch: channel.epoch,
                                                 deviceId: id, seq: row.seq, blob: blob)
            let dash = try JSONDecoder().decode(Dashboard.self, from: plaintext)
            self.board = dash
            self.boardSeq = row.seq
            self.lastSeenSeq[id] = row.seq
            if let u = parseISO(row.updated_at) { self.updatedAt[id] = u }
            self.maybeNotify(dash, channelId: id)
        }
    }

    private func maybeNotify(_ dash: Dashboard, channelId: String) {
        let n = dash.counts.needs_approval
        let last = lastApprovalCount[channelId] ?? 0
        if n > last {
            LocalNotifications.notifyNewProposals(delta: n - last, total: n)
        }
        LocalNotifications.setBadge(n)
        lastApprovalCount[channelId] = n
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
            // §32.2: pin the status this verb's lane rendered from, so a stale
            // tap (board moved since the phone saw it) no-ops on the Mac.
            let plaintext = InboxAction.card(id: cardId, verb: verb, comment: comment,
                                             expectedStatus: verb.pinnedExpectedStatus, ts: ts)
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

    // MARK: - merge-review actions (契约 §21 / §21bis) -------------------------
    /// Accept an AI merge suggestion (merge_apply). `suggestionId` = MS- id.
    func submitMergeApply(suggestionId: String) async -> Bool {
        await sealAndPost { ts in InboxAction.mergeApply(suggestionId: suggestionId, ts: ts) }
    }

    /// Dismiss an AI merge suggestion (merge_dismiss).
    func submitMergeDismiss(suggestionId: String) async -> Bool {
        await sealAndPost { ts in InboxAction.mergeDismiss(suggestionId: suggestionId, ts: ts) }
    }

    /// 契约 §21bis 强制合并: user-chosen primary, skips the AI. De-dups + guards
    /// ≥2 distinct ids with primary ∈ ids before writing (actd re-validates).
    func submitMergeForce(ids: [String], primary: String) async -> Bool {
        var seen = Set<String>()
        let uniq = ids.filter { seen.insert($0).inserted }
        guard uniq.count >= 2, uniq.contains(primary) else { return false }
        return await sealAndPost { ts in InboxAction.mergeForce(ids: uniq, primary: primary, ts: ts) }
    }

    /// Seal a suggestion-level action plaintext under the selected channel's key
    /// and POST it to inbox_actions — same transport as submit/submitCapture.
    private func sealAndPost(_ build: (String) -> Data) async -> Bool {
        guard let target = selectedChannelId, let channel = channels[target] else {
            lastError = L("请先选择并配对一台设备", "Pick and pair a device first"); return false
        }
        let actionId = UUID().uuidString.lowercased()
        let ts = InboxAction.nowTimestamp()
        let seq = boardSeq
        let plaintext = build(ts)
        return await run {
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
