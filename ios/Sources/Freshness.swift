// Freshness.swift — board freshness tiers (plan §5.6). The SERVER clock is the
// authority; the phone tracks a server-time offset (from response Date headers)
// so a slow local clock can't paint a stale board FRESH. Mutating actions are
// hard-gated behind a confirm in STALE/DEAD.

import Foundation
import SwiftUI

enum Freshness: Equatable {
    case fresh    // ● daemon online, board is the pushed head
    case quiet    // ● daemon online, nothing changed (still safe)
    case stale    // ◐ gap 90s–10m, OR push stuck (last_pushed_seq > board.seq)
    case dead     // ○ gap ≥ 10m or no heartbeat
    case unknown  // no data yet

    /// ●◐○ glyph for the device switcher.
    var glyph: String {
        switch self {
        case .fresh, .quiet: return "●"
        case .stale: return "◐"
        case .dead: return "○"
        case .unknown: return "○"
        }
    }

    var color: Color {
        switch self {
        case .fresh, .quiet: return .green
        case .stale: return .orange
        case .dead, .unknown: return .secondary
        }
    }

    /// Mutating actions must show a二次确认 in these tiers (plan §5.6).
    var requiresConfirm: Bool { self == .stale || self == .dead || self == .unknown }

    var label: String {
        switch self {
        case .fresh: return L("在线 · 最新", "Online · current")
        case .quiet: return L("在线 · 无变化", "Online · unchanged")
        case .stale: return L("可能陈旧", "Possibly stale")
        case .dead: return L("离线", "Offline")
        case .unknown: return L("未知", "Unknown")
        }
    }

    /// Compute from the board snapshot's `updated_at` (QR-only v2 — liveness is
    /// `board_snapshots.updated_at`, there is no heartbeat table). `now` should be
    /// the server-adjusted current time. `.unknown` when the channel's board has
    /// not been fetched yet.
    static func compute(updatedAt: Date?, now: Date = Date()) -> Freshness {
        guard let u = updatedAt else { return .unknown }
        let gap = now.timeIntervalSince(u)
        if gap >= 600 { return .dead }    // ≥ 10 min
        if gap >= 90 { return .stale }    // 90s–10m
        return .fresh
    }
}

/// Tracks the offset between the server clock and the device clock so freshness
/// math uses server time (plan §5.6). Updated from any response `Date` header.
final class ServerClock {
    static let shared = ServerClock()
    private(set) var offset: TimeInterval = 0   // serverNow - deviceNow

    func update(fromHTTPDate header: String?) {
        guard let header, let d = Self.rfc1123.date(from: header) else { return }
        offset = d.timeIntervalSinceNow
    }

    var now: Date { Date().addingTimeInterval(offset) }

    private static let rfc1123: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "GMT")
        f.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return f
    }()
}

/// Parse an RFC3339/ISO8601 timestamp (with or without fractional seconds).
func parseISO(_ s: String?) -> Date? {
    guard let s else { return nil }
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: s)
}
