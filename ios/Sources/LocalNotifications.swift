// LocalNotifications.swift — the honest free-tier notification ladder
// (plan §6.4). While the app is in the FOREGROUND and the needs_approval count
// RISES, we post a LOCAL notification. There is no APNs / server push on the
// free tier — that needs the paid Apple Developer Program — so the disclosure
// copy says so plainly and the Mac stays the real alert channel.

import Foundation
import UserNotifications

/// iOS silently discards notifications posted while the app is foreground-active
/// unless the center's delegate implements `willPresent` — and foreground is the
/// ONLY time this app can post at all (see header). Installed at app start.
final class ForegroundNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = ForegroundNotificationDelegate()
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }
}

enum LocalNotifications {
    /// Wire the foreground-presentation delegate. Must run at app start, before
    /// any notification is posted, or every foreground banner is dropped.
    static func installDelegate() {
        UNUserNotificationCenter.current().delegate = ForegroundNotificationDelegate.shared
    }

    static func requestAuthorization() async -> Bool {
        do {
            return try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
        } catch { return false }
    }

    /// Fire a local notification for newly-arrived proposals. `delta` = how many
    /// new needs_approval cards appeared since we last checked.
    static func notifyNewProposals(delta: Int, total: Int) {
        guard delta > 0 else { return }
        let content = UNMutableNotificationContent()
        content.title = L("有新提案待审批", "New proposals to approve")
        content.body = delta == 1
            ? L("有 1 张新卡等你拍板。", "1 new card is waiting for your decision.")
            : L("有 \(delta) 张新卡等你拍板。", "\(delta) new cards are waiting for your decision.")
        content.badge = NSNumber(value: total)
        content.sound = .default
        let req = UNNotificationRequest(identifier: "new-proposals-\(UUID().uuidString)",
                                        content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    /// When the app is closed we can't push; the badge reflects the last known
    /// count so reopening shows the truth (plan §6.4 case 3). `setBadgeCount` is
    /// iOS 16.0+ and updates the icon badge without posting a notification.
    static func setBadge(_ count: Int) {
        UNUserNotificationCenter.current().setBadgeCount(count)
    }

    /// The honest disclosure shown next to the notification toggle in Settings.
    static var disclosure: String {
        L("免费版只能在 App 打开时提醒你。App 关掉后的推送需要付费的 Apple Developer Program（APNs）。你的 Mac 仍会照常在桌面提醒。",
          "The free version can only alert you while the app is open. Push when the app is closed needs the paid Apple Developer Program (APNs). Your Mac still alerts you on the desktop as usual.")
    }
}
