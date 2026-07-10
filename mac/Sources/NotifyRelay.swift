// NotifyRelay.swift — §28 notification relay queue consumer + click handling.
//
// Python daemons write one JSON file per notification into state/notify_queue/
// (atomic .json.tmp + rename); the app posts each entry via
// UNUserNotificationCenter — so banners carry the "Zelin's AI Assistant"
// identity/icon instead of osascript's Script Editor — and deletes the
// consumed file (consume-on-post keeps the queue empty). This is the ONLY
// native-notification path — python has no fallback, by owner decision: app
// closed = no notification (the app auto-starts at login, so running is the
// normal state).

import AppKit
import Foundation
import UserNotifications

enum NotifyRelay {
    static var queueDir: String { AppPaths.stateRoot + "/state/notify_queue" }

    /// §28 stale storm guard: entries older than this are deleted UNPOSTED —
    /// a closed-app backlog must not carpet-bomb the user on the next launch.
    static let staleAfter: TimeInterval = 600

    /// §28 burst cap: at most this many individual banners per drain pass;
    /// the overflow collapses into ONE "+N more" summary notification.
    static let burstCap = 5

    private struct Entry {
        let path: String
        let id: String
        let title: String
        let body: String
        let subtitle: String?
        let createdAt: TimeInterval
    }

    /// Scan → post → delete. Runs on the 5 s refresh tick (the same cadence
    /// that keeps dashboard.json fresh); a missing/empty dir is a cheap no-op.
    static func drain() {
        // UNUserNotificationCenter traps outside a real .app bundle (bare dev
        // binary) — same guard as RecordingController.postSystemNotice.
        guard Bundle.main.bundleIdentifier != nil else { return }
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: queueDir),
              !names.isEmpty else { return }

        var entries: [Entry] = []
        // python writes atomically as *.json.tmp then renames — the .json
        // filter below never sees a half-written file.
        for name in names where name.hasSuffix(".json") {
            let path = queueDir + "/" + name
            guard let data = fm.contents(atPath: path),
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let id = obj["id"] as? String,
                  let title = obj["title"] as? String,
                  let body = obj["body"] as? String,
                  let created = obj["created_at"] as? Double
            else {
                // malformed: log + delete — kept around, the 5 s tick would
                // re-log it forever
                NSLog("notify_queue: malformed entry dropped: \(name)")
                try? fm.removeItem(atPath: path)
                continue
            }
            entries.append(Entry(path: path, id: id, title: title, body: body,
                                 subtitle: obj["subtitle"] as? String,
                                 createdAt: created))
        }
        guard !entries.isEmpty else { return }

        let now = Date().timeIntervalSince1970
        let center = UNUserNotificationCenter.current()
        // stale entries (closed-app backlog) are silently consumed first
        var fresh: [Entry] = []
        for e in entries {
            if now - e.createdAt <= staleAfter {
                fresh.append(e)
            } else {
                NSLog("notify_queue: stale entry dropped: \(e.id)")
                try? fm.removeItem(atPath: e.path)
            }
        }
        // oldest first, so a burst posts in the order it was produced; the
        // whole pass is consumed either way (individually or via the summary)
        fresh.sort { $0.createdAt < $1.createdAt }
        for e in fresh.prefix(burstCap) {
            let content = UNMutableNotificationContent()
            content.title = e.title
            content.body = e.body
            if let s = e.subtitle { content.subtitle = s }
            // ungranted permission → add() silently no-ops; the truth
            // lives in the Permissions page — the queue never retries.
            center.add(UNNotificationRequest(identifier: e.id,
                                             content: content, trigger: nil))
            try? fm.removeItem(atPath: e.path)
        }
        let overflow = fresh.dropFirst(burstCap)
        guard !overflow.isEmpty else { return }
        let content = UNMutableNotificationContent()
        content.title = L("还有 \(overflow.count) 条通知", "+\(overflow.count) more notifications")
        content.body = L("打开 App 查看看板", "Open the app to see the board")
        center.add(UNNotificationRequest(identifier: "notify-relay-overflow-\(UUID().uuidString)",
                                         content: content, trigger: nil))
        for e in overflow { try? fm.removeItem(atPath: e.path) }
    }
}

// §28 click behavior: every §5 message says "open the app…" — a click on any
// relayed banner opens the main window (the old osascript path had no click
// behavior at all, so nothing is lost). Installed once at launch; also covers
// the recording self-heal notices — same behavior, one delegate. willPresent
// keeps banners visible even while the app is technically frontmost.
final class NotifyRelayDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotifyRelayDelegate()

    static func install() {
        // same bare-binary trap guard as NotifyRelay.drain
        guard Bundle.main.bundleIdentifier != nil else { return }
        UNUserNotificationCenter.current().delegate = shared
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                MainWindowController.shared.show()
            }
        }
        completionHandler()
    }
}
