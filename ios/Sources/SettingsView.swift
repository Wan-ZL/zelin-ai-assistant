// SettingsView.swift — screen 5 (plan §6.6): sync on/off, notification
// permission (with the honest free-tier disclosure), the expiry countdown,
// device labels + unpair/wipe-keys, language, and sign out.

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var lang: LanguageStore
    @Environment(\.dismiss) private var dismiss

    @State private var notifyOn = false
    @State private var confirmWipe = false

    var body: some View {
        NavigationStack {
            Form {
                // --- sync ---
                Section {
                    Toggle(L("多设备同步", "Multi-device sync"), isOn: Binding(
                        get: { state.syncEnabled },
                        set: { on in
                            state.syncEnabled = on
                            if on { Task { await state.refreshEverything() } }
                        }))
                } footer: {
                    Text(L("关闭后手机不再从服务器拉取看板。已存的配对密钥保留在本机 Keychain。",
                           "Off = the phone stops pulling boards from the server. Stored pairing keys stay in this device's Keychain."))
                }

                // --- notifications ---
                Section {
                    Toggle(L("提醒（本地通知）", "Alerts (local notifications)"), isOn: $notifyOn)
                        .onChange(of: notifyOn) { on in
                            if on { Task { notifyOn = await LocalNotifications.requestAuthorization() } }
                        }
                } header: {
                    Text(L("通知", "Notifications"))
                } footer: {
                    Text(LocalNotifications.disclosure)
                }

                // --- expiry ---
                if let d = CertExpiry.daysLeft() {
                    Section(L("试用版有效期", "Trial build validity")) {
                        LabeledContent(L("剩余", "Remaining"), value: L("\(d) 天", "\(d) days"))
                        Text(L("到期前在 Xcode 重跑一次可续期，并保住已配对的密钥（避免重扫码）。",
                               "Re-run from Xcode before expiry to renew — and to keep your paired keys (avoids re-scanning)."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                // --- devices ---
                Section(L("已配对设备", "Paired devices")) {
                    if state.pairings.isEmpty {
                        Text(L("还没有配对任何 Mac。", "No Macs paired yet.")).foregroundStyle(.secondary)
                    }
                    ForEach(Array(state.pairings.values)) { p in
                        HStack {
                            Text(state.freshness(for: p.deviceId).glyph)
                                .foregroundStyle(state.freshness(for: p.deviceId).color)
                            VStack(alignment: .leading) {
                                Text(p.label)
                                Text(p.deviceId).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer()
                            Button(role: .destructive) { state.unpair(deviceId: p.deviceId) } label: {
                                Text(L("解除配对", "Unpair"))
                            }.font(.caption)
                        }
                    }
                    if !state.pairings.isEmpty {
                        Button(role: .destructive) { confirmWipe = true } label: {
                            Text(L("擦除所有配对密钥", "Wipe all pairing keys"))
                        }
                    }
                }

                // --- language + account ---
                Section {
                    Picker(L("语言", "Language"), selection: Binding(
                        get: { lang.lang }, set: { lang.lang = $0 })) {
                        Text("中文").tag("zh")
                        Text("English").tag("en")
                    }
                }
                Section {
                    if let s = state.session { Text(L("已登录：\(s.email)", "Signed in: \(s.email)")).font(.caption) }
                    Button(role: .destructive) { state.signOut() } label: { Text(L("退出登录", "Sign out")) }
                }
            }
            .navigationTitle(L("设置", "Settings"))
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button(L("完成", "Done")) { dismiss() } } }
            .alert(L("擦除所有配对密钥？", "Wipe all pairing keys?"), isPresented: $confirmWipe) {
                Button(L("取消", "Cancel"), role: .cancel) {}
                Button(L("擦除", "Wipe"), role: .destructive) { state.wipeAllKeys() }
            } message: {
                Text(L("擦除后需重新扫码才能再看到这些设备的看板。服务器上的密文无法在无密钥时解密。",
                       "After wiping you must re-scan to see these devices' boards again. Server ciphertext can't be read without the key."))
            }
        }
    }
}
