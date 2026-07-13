// Keychain.swift — per-device secret storage for pairing keys + the auth
// session. Everything is stored `…ThisDeviceOnly` with iCloud-Keychain sync
// OFF: a per-pairing key that synced to iCloud would defeat E2E (plan §4.5).

import Foundation
import Security

enum Keychain {
    /// Service namespace for all items this app writes.
    private static let service = "com.zelin.ai-engineer.ios"

    /// Store raw bytes under `account`. Overwrites any existing item.
    /// `ThisDeviceOnly` accessibility ⇒ never leaves the device, never syncs.
    @discardableResult
    static func set(_ data: Data, account: String) -> Bool {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)   // idempotent overwrite
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }

    static func get(_ account: String) -> Data? {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        return SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess ? out as? Data : nil
    }

    @discardableResult
    static func delete(_ account: String) -> Bool {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let s = SecItemDelete(q as CFDictionary)
        return s == errSecSuccess || s == errSecItemNotFound
    }

    /// Accounts matching a prefix (used to enumerate all stored pairings).
    static func accounts(prefix: String) -> [String] {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecReturnAttributes as String: true,
            kSecMatchLimit as String: kSecMatchLimitAll,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let items = out as? [[String: Any]] else { return [] }
        return items.compactMap { $0[kSecAttrAccount as String] as? String }
                    .filter { $0.hasPrefix(prefix) }
    }

    // Convenience typed wrappers ------------------------------------------------
    static func setString(_ s: String, account: String) { set(Data(s.utf8), account: account) }
    static func getString(_ account: String) -> String? { get(account).map { String(decoding: $0, as: UTF8.self) } }
}
