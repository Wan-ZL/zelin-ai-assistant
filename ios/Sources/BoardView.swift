// BoardView.swift — screen 3 (plan §6.2/§6.6): the portrait board. Five lanes
// (储备 · 提案 · 运行中 · 待验收 · 已验收) as a horizontally-paged TabView; each
// page is a vertical scroll of cards mirroring the Mac row styling. A top lane
// strip doubles as tap-to-jump + live counts; the device switcher and settings
// live in the toolbar; the 7-day expiry banner pins to the top when ≤2 days.

import SwiftUI

struct BoardView: View {
    @EnvironmentObject var state: AppState
    @State private var lane: BoardLane = .proposals
    @State private var showSettings = false
    @State private var showPairing = false

    private var model: BoardModel? { state.board.map(BoardModel.init) }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if CertExpiry.shouldWarn() { ExpiryBanner() }
                laneStrip
                Divider()
                lanePager
            }
            .navigationTitle(lane.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) { DeviceSwitcher(showPairing: $showPairing) }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                }
            }
            .refreshable { await state.refreshEverything() }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .sheet(isPresented: $showPairing) { NavigationStack { PairingView() } }
            .task { await state.refreshEverything() }
        }
    }

    // Top lane strip — tap a lane to jump; shows counts. (plan §6.2)
    private var laneStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(BoardLane.allCases) { l in
                    Button { withAnimation { lane = l } } label: {
                        HStack(spacing: 4) {
                            Text(l.title).font(.system(size: 13, weight: lane == l ? .semibold : .regular))
                            if let m = model {
                                Text("\(m.count(l))")
                                    .font(.system(size: 11, weight: .bold))
                                    .padding(.horizontal, 5).padding(.vertical, 1)
                                    .background(Color.secondary.opacity(0.18), in: Capsule())
                            }
                        }
                        .foregroundStyle(lane == l ? Color.primary : Color.secondary)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(lane == l ? Color.accentColor.opacity(0.12) : Color.clear, in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
        }
    }

    private var lanePager: some View {
        TabView(selection: $lane) {
            ForEach(BoardLane.allCases) { l in
                LanePage(lane: l, model: model).tag(l)
            }
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
    }
}

// One lane's scrollable content.
private struct LanePage: View {
    @EnvironmentObject var state: AppState
    let lane: BoardLane
    let model: BoardModel?

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                // help/definition strip
                Text(lane.help).font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.bottom, 2)

                if lane == .proposals { QuickCapture() }

                if let model {
                    content(model)
                } else {
                    ContentUnavailableCompat(
                        title: L("暂无看板", "No board yet"),
                        subtitle: L("下拉刷新，或配对一台 Mac。", "Pull to refresh, or pair a Mac."))
                }
            }
            .padding(.horizontal, 14).padding(.top, 8).padding(.bottom, 24)
        }
    }

    @ViewBuilder private func content(_ m: BoardModel) -> some View {
        switch lane {
        case .backlog:
            laneList(m.backlog) { DebtRow(item: $0) }
        case .proposals:
            laneList(m.proposals) { ProposalCardRow(card: $0) }
        case .running:
            laneList(m.runningLane) { RunningRow(task: $0, needsInput: m.isNeedsInput($0)) }
        case .review:
            laneList(m.review) { ReviewRow(item: $0) }
        case .done:
            laneList(m.done) { DoneRow(task: $0) }
        }
    }

    @ViewBuilder private func laneList<T: Hashable, Row: View>(
        _ items: [T], @ViewBuilder row: @escaping (T) -> Row) -> some View {
        if items.isEmpty {
            Text(L("这个列表现在是空的。", "This list is empty right now."))
                .font(.footnote).foregroundStyle(.secondary.opacity(0.8))
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            ForEach(items, id: \.self) { row($0) }
        }
    }
}

// Resident quick-capture at the top of Proposals (plan §6.2).
private struct QuickCapture: View {
    @EnvironmentObject var state: AppState
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            TextField(L("快速记一件事…", "Quick-capture a task…"), text: $text)
                .textFieldStyle(.roundedBorder).focused($focused)
            Button {
                let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !t.isEmpty else { return }
                text = ""; focused = false
                Task { _ = await state.submitCapture(t) }
            } label: { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.bottom, 4)
    }
}

// Cross-lane device switcher (plan §6.3): ●◐○ freshness + decrypted label.
struct DeviceSwitcher: View {
    @EnvironmentObject var state: AppState
    @Binding var showPairing: Bool

    var body: some View {
        Menu {
            ForEach(state.devices) { d in
                Button {
                    state.selectedDeviceId = d.id
                    Task { await state.refreshBoard() }
                } label: {
                    let mark = d.id == state.selectedDeviceId ? "✓ " : ""
                    Text("\(mark)\(state.freshness(for: d.id).glyph)  \(state.label(for: d))")
                }
            }
            Divider()
            Button { showPairing = true } label: { Label(L("配对新设备", "Pair a device"), systemImage: "qrcode.viewfinder") }
        } label: {
            HStack(spacing: 4) {
                if let id = state.selectedDeviceId {
                    Text(state.freshness(for: id).glyph).foregroundStyle(state.freshness(for: id).color)
                    Text(state.selectedPairing?.label ?? L("选择设备", "Select device"))
                        .font(.subheadline).lineLimit(1)
                } else {
                    Text(L("选择设备", "Select device")).font(.subheadline)
                }
                Image(systemName: "chevron.down").font(.caption2)
            }
        }
    }
}

// 7-day free-provisioning expiry banner (plan §6.5).
struct ExpiryBanner: View {
    var body: some View {
        let n = CertExpiry.daysLeft() ?? 0
        return HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(L("此试用版还有 \(n) 天到期。在 Xcode 重新运行一次即可续 7 天，或升级 $99/年长期使用。",
                   "This trial build expires in \(n) day(s). Re-run once from Xcode to renew 7 days, or upgrade ($99/yr) for good."))
                .font(.caption)
            Spacer(minLength: 0)
        }
        .padding(8).foregroundStyle(.orange)
        .background(Color.orange.opacity(0.12))
    }
}

// iOS 16-safe stand-in for ContentUnavailableView (iOS 17+).
struct ContentUnavailableCompat: View {
    let title: String; let subtitle: String
    var body: some View {
        VStack(spacing: 6) {
            Text(title).font(.headline)
            Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 40)
    }
}
