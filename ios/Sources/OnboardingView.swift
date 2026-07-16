// OnboardingView.swift — screen 1 (QR-only v2): the explicit multi-device-sync
// opt-in (default OFF, honest disclosure, no pre-checked boxes). There is NO
// account and NO email step — once sync is on, the app goes straight to pairing
// (scan each Mac's QR). The QR is the credential.

import SwiftUI

struct OnboardingView: View {
    var body: some View {
        NavigationStack {
            ConsentView()
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
                Text(L("默认关闭。开启后，你的任务卡片会离开你的 Mac，经 Supabase 服务器中转并存储，你的 iPhone 才能看到同一块看板。",
                       "Off by default. Once on, your task cards leave your Mac, relay + store through Supabase, and your iPhone can see the same board."))
                    .foregroundStyle(.secondary)

                disclosureBlock(
                    L("会离开这台机器的内容", "What leaves this machine"),
                    L("卡片标题、摘要、链接、备注、计划/验收清单、你在手机上的操作（通过/拒绝/修改意见文字）、设备标签。",
                      "Card titles, summaries, links, notes, plan/acceptance lists, your phone actions (approve/reject/comment text), and device labels."))
                disclosureBlock(
                    L("端到端加密做了什么", "What E2E encryption does"),
                    L("卡片正文在离开这台 Mac 之前就已加密，密钥只经配对二维码传给你的设备、从不上传服务器。Supabase 和维护者都读不到明文。",
                      "Card bodies are encrypted before leaving the Mac; the key travels only via the pairing QR, never to the server. Supabase and the maintainer cannot read the plaintext."))
                disclosureBlock(
                    L("保护不了什么", "What it can't hide"),
                    L("元数据——同步时间、数据大小、卡片数量、设备数量，以及“你在用这个功能”本身。配对二维码就是唯一凭证：拿到它的任何人都能读写你这台 Mac 的看板，请当作密码保管；弄丢密钥 = 服务器上的数据无法恢复。",
                      "Metadata — sync times, data size, card count, device count, and that you use this at all. The pairing QR is the only credential: anyone who has it can read and control that Mac's board, so treat it like a password. Lose the key = the server data is unrecoverable."))

                Text(L("这和“匿名使用统计”是两个独立开关，互不影响。",
                       "This is a separate switch from anonymous usage stats; they don't affect each other."))
                    .font(.footnote).foregroundStyle(.secondary)

                Text(L("开启后，逐台扫描每台 Mac 上的配对二维码即可——无需邮箱、无需账号。",
                       "After turning it on, just scan each Mac's pairing QR — no email, no account."))
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
