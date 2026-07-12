// OnboardingView.swift — screen 1 (plan §6.6): the explicit multi-device-sync
// opt-in (default OFF, honest disclosure, no pre-checked boxes) followed by the
// email-OTP login. Two independent gates: enabling sync, then signing in.

import SwiftUI

struct OnboardingView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        NavigationStack {
            if !state.syncEnabled {
                ConsentView()
            } else {
                LoginView()
            }
        }
    }
}

// MARK: - Consent (plan §7.3 disclosure) --------------------------------------
private struct ConsentView: View {
    @EnvironmentObject var state: AppState
    @State private var confirming = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(L("开启多设备同步", "Turn on multi-device sync"))
                    .font(.title2).bold()
                Text(L("默认关闭。开启后，你的任务卡片会离开这台 Mac，经 Supabase 服务器中转并存储，你的 iPhone 才能看到同一块看板。",
                       "Off by default. Once on, your task cards leave your Mac, relay + store through Supabase, and your iPhone can see the same board."))
                    .foregroundStyle(.secondary)

                disclosureBlock(
                    L("会离开这台机器的内容", "What leaves this machine"),
                    L("卡片标题、摘要、链接、备注、计划/验收清单、你在手机上的操作（通过/拒绝/修改意见文字）、设备标签。",
                      "Card titles, summaries, links, notes, plan/acceptance lists, your phone actions (approve/reject/comment text), and device labels."))
                disclosureBlock(
                    L("端到端加密做了什么", "What E2E encryption does"),
                    L("卡片正文与设备标签在离开这台 Mac 之前就已加密，密钥只经配对二维码传给你的设备、从不上传服务器。Supabase 和维护者都读不到明文。",
                      "Card bodies and device labels are encrypted before leaving the Mac; the key travels only via the pairing QR, never to the server. Supabase and the maintainer cannot read the plaintext."))
                disclosureBlock(
                    L("保护不了什么", "What it can't hide"),
                    L("元数据——同步时间、数据大小、卡片数量、设备数量、你的匿名设备 ID，以及“你在用这个功能”本身。弄丢配对密钥 = 服务器上的数据无法恢复；拿到你配对密钥的任何人都能读到你的卡片。",
                      "Metadata — sync times, data size, card count, device count, your anonymous device ID, and that you use this at all. Lose the pairing key = the server data is unrecoverable; anyone with your pairing key can read your cards."))

                Text(L("这和“匿名使用统计”是两个独立开关，互不影响。",
                       "This is a separate switch from anonymous usage stats; they don't affect each other."))
                    .font(.footnote).foregroundStyle(.secondary)

                Button {
                    confirming = true
                } label: {
                    Text(L("我明白，开启同步", "I understand — turn on sync"))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .padding(.top, 8)
            }
            .padding()
        }
        .navigationTitle(L("多设备同步", "Multi-device sync"))
        .alert(L("开启同步？", "Enable sync?"), isPresented: $confirming) {
            Button(L("取消", "Cancel"), role: .cancel) {}
            Button(L("开启", "Enable")) { state.syncEnabled = true }
        } message: {
            Text(L("卡片将开始经服务器中转（端到端加密）。可随时在设置里关闭。",
                   "Cards will start relaying through the server (end-to-end encrypted). You can turn this off anytime in Settings."))
        }
    }

    private func disclosureBlock(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.headline)
            Text(body).font(.subheadline).foregroundStyle(.secondary)
        }
    }
}

// MARK: - Email OTP login -----------------------------------------------------
private struct LoginView: View {
    @EnvironmentObject var state: AppState
    @State private var email = ""
    @State private var code = ""
    @State private var codeSent = false

    var body: some View {
        Form {
            Section {
                TextField(L("邮箱", "Email"), text: $email)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                if codeSent {
                    TextField(L("6 位验证码", "6-digit code"), text: $code)
                        .textContentType(.oneTimeCode)
                        .keyboardType(.numberPad)
                }
            } footer: {
                Text(L("用邮箱验证码登录（无需密码）。登录后逐台扫 Mac 的二维码即可。",
                       "Sign in with an emailed code (no password). Then scan each Mac's QR to pair."))
            }

            Section {
                if !codeSent {
                    Button(L("发送验证码", "Send code")) {
                        Task { if await state.sendOTP(email: email.trimmingCharacters(in: .whitespaces)) { codeSent = true } }
                    }
                    .disabled(email.isEmpty || state.isBusy)
                } else {
                    Button(L("验证并登录", "Verify & sign in")) {
                        Task { _ = await state.verifyOTP(email: email.trimmingCharacters(in: .whitespaces),
                                                         code: code.trimmingCharacters(in: .whitespaces)) }
                    }
                    .disabled(code.count < 6 || state.isBusy)
                    Button(L("重新发送", "Resend code")) { codeSent = false; code = "" }
                        .foregroundStyle(.secondary)
                }
            }

            if let err = state.lastError {
                Section { Text(err).font(.footnote).foregroundStyle(.red) }
            }
        }
        .navigationTitle(L("登录", "Sign in"))
        .overlay { if state.isBusy { ProgressView() } }
    }
}
