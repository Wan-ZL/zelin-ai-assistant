// CertExpiry.swift — the 7-day free-provisioning expiry countdown (plan §6.5).
// A free (personal-team) build's embedded provisioning profile expires in 7
// days; after that iOS refuses to launch the app. Since a dead app can't warn
// you, we warn IN-APP while it still runs: parse `embedded.mobileprovision`'s
// ExpirationDate, fall back to (compile date + 7). daysLeft ≤ 2 → banner.

import Foundation

enum CertExpiry {
    /// Days until this build stops launching (nil if we truly can't tell, e.g.
    /// a simulator build with no provisioning profile and no build date).
    static func daysLeft(now: Date = Date()) -> Int? {
        guard let exp = expirationDate() else { return nil }
        let secs = exp.timeIntervalSince(now)
        return Int(floor(secs / 86_400))
    }

    static func expirationDate() -> Date? {
        if let d = provisioningExpiration() { return d }
        return compileDate()?.addingTimeInterval(7 * 86_400)   // fallback: build+7
    }

    /// Show the banner when ≤ 2 days remain (plan §6.5).
    static func shouldWarn(now: Date = Date()) -> Bool {
        guard let d = daysLeft(now: now) else { return false }
        return d <= 2
    }

    // MARK: - embedded.mobileprovision -----------------------------------------
    private static func provisioningExpiration() -> Date? {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url) else { return nil }
        // The file is a CMS (PKCS#7) container; the plist payload sits between
        // <?xml … ?> … </plist>. Slice it out and parse it.
        guard let plistData = extractPlist(from: data),
              let plist = try? PropertyListSerialization.propertyList(from: plistData, options: [], format: nil) as? [String: Any]
        else { return nil }
        return plist["ExpirationDate"] as? Date
    }

    private static func extractPlist(from data: Data) -> Data? {
        guard let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8)) else { return nil }
        return data.subdata(in: start.lowerBound..<end.upperBound)
    }

    // MARK: - build-date fallback ----------------------------------------------
    /// Fallback when there is no provisioning profile (e.g. a simulator build):
    /// the executable's own modification time approximates "when this build was
    /// produced". The provisioning ExpirationDate above is always preferred on a
    /// real free-provisioned device; this only keeps the countdown sane in the
    /// simulator/dev, where the 7-day expiry does not actually apply.
    private static func compileDate() -> Date? {
        guard let exe = Bundle.main.executableURL,
              let attrs = try? FileManager.default.attributesOfItem(atPath: exe.path),
              let mtime = attrs[.modificationDate] as? Date else { return nil }
        return mtime
    }
}
