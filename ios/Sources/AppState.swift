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

    /// §39: needs_input card ids per channel — per-card (not batched) local
    /// notifications fire only for NEWLY blocked cards within that channel.
    /// nil (channel never fetched) stays silent, like the approval counter.
    private var lastNeedsInputIDs: [String: Set<String>] = [:]

    /// §39: answers in flight, card id → send time (the Mac answerPending
    /// semantics). answer_input is NOT idempotent — a second send stop-kills
    /// the first answer's freshly-resumed session — so the input bar stays in
    /// its 已发送 state until the card actually LEAVES needs_input on a board
    /// refresh (real phone round-trip is 20-40s), with a 180s honest timeout
    /// (entry expires → the bar re-arms for a retry).
    @Published var answerPending: [String: Date] = [:]

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
            // §35 v0.35: a Mac rename rides the board payload (device_label) —
            // adopt it for the channel this snapshot came from, no re-scan.
            self.adoptDeviceLabel(dash.device_label, for: id)
            // §39: an answer echo clears on the REAL signal — the card left
            // needs_input (delivered, or failed over to running with
            // last_error) — with a 180s expiry so a dead backend can't lock
            // the input bar forever.
            let blocked = Set(dash.needs_input.map { $0.id })
            self.answerPending = self.answerPending.filter {
                blocked.contains($0.key) && Date().timeIntervalSince($0.value) < 180
            }
            self.maybeNotify(dash, channelId: id)
        }
    }

    /// §35 v0.35 rename-without-rescan: the board payload carries the Mac's
    /// current device name. Update the in-memory Channel and rewrite its
    /// Keychain JSON (the exact shape addChannel persists) so the new name
    /// survives relaunch. No-op when absent / empty / unchanged.
    private func adoptDeviceLabel(_ label: String?, for channelId: String) {
        guard let label, !label.isEmpty,
              var c = channels[channelId], c.label != label else { return }
        c.label = label
        channels[channelId] = c
        let payload: [String: Any] = [
            "epoch": Int(c.epoch),
            "write_secret": c.writeSecret.base64EncodedString(),
            "key": c.key.base64EncodedString(),
            "label": label,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: payload) {
            Keychain.set(data, account: channelPrefix + channelId)
        }
    }

    private func maybeNotify(_ dash: Dashboard, channelId: String) {
        // §39: the badge counts BOTH decisions waiting on the owner — proposals
        // to approve AND blocked agents waiting for an answer (the latter is
        // the more urgent signal: an agent is burning wall-clock on it).
        let n = dash.counts.needs_approval
        let badge = n + dash.counts.needs_input
        let last = lastApprovalCount[channelId] ?? 0
        if n > last {
            LocalNotifications.notifyNewProposals(delta: n - last, total: badge)
        }
        // §39 per-card needs-input notifications (not batched — each names its
        // card + question). First fetch of a channel seeds silently: replaying
        // every already-blocked card on app start would be a notification storm.
        let blocked = dash.needs_input
        if let seen = lastNeedsInputIDs[channelId] {
            for t in blocked where !seen.contains(t.id) {
                LocalNotifications.notifyNeedsInput(
                    name: t.name, question: t.question, badge: badge)
            }
        }
        lastNeedsInputIDs[channelId] = Set(blocked.map { $0.id })
        LocalNotifications.setBadge(badge)
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

    /// `directRun` = direct-run capture (v0.34, CONTRACT §34): the plaintext
    /// carries mode:"run" and actd queues the card straight for dispatch,
    /// skipping the proposal gate. false = today's proposal capture (key omitted).
    func submitCapture(_ text: String, directRun: Bool = false) async -> Bool {
        guard let target = selectedChannelId, let channel = channels[target] else { return false }
        let actionId = UUID().uuidString.lowercased()
        let ts = InboxAction.nowTimestamp()
        let seq = boardSeq
        return await run {
            let plaintext = InboxAction.capture(text: text, mode: directRun ? "run" : nil, ts: ts)
            let blob = try E2E.encryptAction(kI: channel.key, epoch: channel.epoch,
                                             deviceId: target, actionId: actionId,
                                             boardSeq: seq, plaintext: plaintext)
            try await self.client.postInboxAction(actionId: actionId, channelId: target,
                                                  writeSecret: channel.writeSecret,
                                                  boardSeq: seq, blob: blob, clientTs: ts)
        }
    }

    /// §39 回答需输入: seal {"action":"answer_input","id","text"} and POST it —
    /// the same transport as every other inbox action. Pins
    /// expected_status:"executing" (需输入 rows only ever project executing
    /// cards) so a stale tap no-ops honestly on the Mac. Trims + clips to the
    /// 4000 ceiling by unicode scalars (InboxAction.clipAnswer — actd counts
    /// code points); empty after trimming = nothing to send. On success the
    /// card enters answerPending until it leaves needs_input (see the
    /// property doc) — the bar must NOT re-arm on a 3.5s timer, answer_input
    /// is not idempotent.
    func submitAnswer(cardId: String, text: String) async -> Bool {
        let t = InboxAction.clipAnswer(
            text.trimmingCharacters(in: .whitespacesAndNewlines))
        guard !t.isEmpty else { return false }
        let ok = await sealAndPost { ts in
            InboxAction.answerInput(id: cardId, text: t,
                                    expectedStatus: "executing", ts: ts)
        }
        if ok { answerPending[cardId] = Date() }
        return ok
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
