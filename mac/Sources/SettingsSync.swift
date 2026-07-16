// SettingsSync.swift — 设置 · 同步 / 配对（QR-only capability sync, DESIGN §Mac-side QR）
//
// The PRIMARY pairing surface for the QR-only capability model. Turning the
// toggle ON runs `python3 -m act.syncd --pair --json` (via RuntimePython, the
// same interpreter launchd uses); syncd persists all state (channel_id +
// write_secret + K under state/sync/) and returns the base64url pairing blob.
// We render that blob as a QR right here with CoreImage's CIQRCodeGenerator so
// the owner never needs a terminal — scan it once per Mac from the phone app.
// OFF runs `--disable` (mode=off, secrets kept so re-enabling needs no re-pair).
//
// Security posture (owner-accepted, DESIGN): the QR IS the master key — anyone
// who scans it gets full read + write + decrypt of this Mac's board. The copy
// says so; keep it private. Everything here is happy-path: no YAML, no terminal.

import AppKit
import CoreImage
import Foundation
import SwiftUI

// MARK: - Model

@MainActor
final class SyncSettingsModel: ObservableObject {
    @Published var enabled = false
    @Published var busy = false
    @Published var statusNote = ""
    @Published var errorNote = ""
    @Published var channelId = ""
    @Published var label = "" {
        // QR capacity guard (§35): the name rides the pairing QR's trailing
        // bytes, so cap the editable field at 64 characters.
        didSet { if label.count > 64 { label = String(label.prefix(64)) } }
    }
    @Published var qrBlob = ""
    @Published var qrImage: NSImage? = nil

    /// The name currently persisted in state/sync.json (and thus in the QR);
    /// the Save button lights up only when the field diverges from it.
    @Published private(set) var savedLabel = ""

    private var loaded = false

    /// Default device name when unpaired / never customized: the Mac's own
    /// computer name (falls back to the historical hardcoded label).
    static var defaultDeviceName: String {
        Host.current().localizedName ?? "这台 Mac"
    }

    var labelDirty: Bool {
        let t = label.trimmingCharacters(in: .whitespacesAndNewlines)
        return !t.isEmpty && t != savedLabel
    }

    /// One shot on first appear: read the on/off state from state/sync.json
    /// (no network), and if already enabled fetch the (stable) QR blob to show.
    func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        let cfg = Self.readSyncConfig()
        let mode = (cfg["mode"] as? String ?? "").lowercased()
        let cid = cfg["channel_id"] as? String ?? ""
        if mode == "cloud", !cid.isEmpty {
            enabled = true
            channelId = cid
            label = cfg["label"] as? String ?? ""
            savedLabel = label
            pair(mode: .refresh)   // idempotent: loads existing secrets, stable QR
        } else {
            enabled = false
            // A previous pairing's name survives disable() (mode=off keeps
            // sync.json), so prefill the stored name — re-enabling must keep
            // a custom name, never reset it. Only a never-named Mac gets the
            // computer-name default.
            let stored = (cfg["label"] as? String ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            savedLabel = stored
            label = stored.isEmpty ? Self.defaultDeviceName : stored
        }
    }

    // MARK: on/off + re-pair

    func setEnabled(_ on: Bool) {
        guard !busy else { return }
        if on {
            // Pass an explicit label only on FIRST pair (no stored name yet):
            // the field then holds the computer-name default. When a stored
            // name exists, pass nothing — the §33 resolution chain keeps
            // sync.json's, so re-enabling never clobbers a custom name and an
            // uncommitted half-typed edit is never saved (renames go through
            // commitLabel only).
            let t = label.trimmingCharacters(in: .whitespacesAndNewlines)
            pair(mode: .enable, label: savedLabel.isEmpty && !t.isEmpty ? t : nil)
        } else {
            disable()
        }
    }

    func regenerate() {
        guard !busy else { return }
        // No explicit label: syncd keeps the state/sync.json one (§33), so an
        // uncommitted half-typed name in the field is never accidentally saved.
        pair(mode: .repair)
    }

    /// Commit a device-name edit: re-run the idempotent pair path with the new
    /// label — channel_id/secrets stay stable, only the QR's trailing label
    /// bytes + state/sync.json change (§35).
    func commitLabel() {
        guard !busy, enabled else { return }
        let t = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { label = savedLabel; return }
        guard t != savedLabel else { label = t; return }
        label = t
        pair(mode: .rename, label: t)
    }

    private enum PairMode { case enable, repair, refresh, rename }

    /// Runs `syncd --pair --json` off the main thread; syncd owns all state, we
    /// only render what it returns. Never throws into the UI.
    private func pair(mode: PairMode, label explicitLabel: String? = nil) {
        busy = true
        errorNote = ""
        switch mode {
        case .enable:
            statusNote = L("正在开启同步并生成配对二维码…",
                           "Turning on sync and generating the pairing QR…")
        case .repair:
            statusNote = L("正在重新生成配对二维码…", "Regenerating the pairing QR…")
        case .refresh:
            statusNote = ""
        case .rename:
            statusNote = L("正在更新设备名并刷新二维码…",
                           "Updating the device name and refreshing the QR…")
        }
        Analytics.log("mw_sync_pair", fields: ["mode": "\(mode)"])
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Self.runPairJSON(label: explicitLabel)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.busy = false
                    switch result {
                    case let .ok(cid, blob, label, registered):
                        self.enabled = true
                        self.channelId = cid
                        self.qrBlob = blob
                        self.label = label
                        self.savedLabel = label
                        self.qrImage = Self.qrImage(from: blob)
                        self.errorNote = ""
                        if mode == .refresh {
                            self.statusNote = ""
                        } else if mode == .rename {
                            self.statusNote = L("设备名已更新 ✓ 二维码已同步刷新。",
                                                "Device name updated ✓ The QR has been refreshed too.")
                        } else if registered {
                            self.statusNote = L("已开启 ✓ 用手机扫下面的码即可配对。",
                                                "On ✓ Scan the code below from your phone to pair.")
                        } else {
                            self.statusNote = L("已开启（频道注册会在联网后自动重试）——二维码现在就能扫。",
                                                "On (channel registration retries automatically once online) — the QR is ready to scan now.")
                        }
                        Analytics.firstReach("sync_paired")
                    case let .failed(why):
                        self.statusNote = ""
                        self.errorNote = why
                    }
                }
            }
        }
    }

    private func disable() {
        busy = true
        errorNote = ""
        statusNote = L("正在关闭同步…", "Turning off sync…")
        Analytics.log("mw_sync_toggle", fields: ["on": false])
        DispatchQueue.global(qos: .userInitiated).async {
            let (code, _) = Self.runSyncd(["--disable"])
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.busy = false
                    if code == 0 {
                        self.enabled = false
                        self.qrImage = nil
                        self.qrBlob = ""
                        // Drop any uncommitted edit so re-enabling can never
                        // save a half-typed name (sync.json keeps savedLabel).
                        self.label = self.savedLabel.isEmpty
                            ? Self.defaultDeviceName : self.savedLabel
                        self.statusNote = L("已关闭。密钥保留在本机,随时可以再打开——不用重新配对。",
                                            "Off. The keys stay on this Mac; re-enable anytime — no re-pairing needed.")
                    } else {
                        self.errorNote = L("关闭失败——请稍后重试。",
                                           "Couldn't turn it off — try again later.")
                    }
                }
            }
        }
    }

    // MARK: syncd invocation (runtime python, like the other sections)

    enum PairResult {
        case ok(channelId: String, blob: String, label: String, registered: Bool)
        case failed(String)
    }

    /// Blocking — background queue only. Parses syncd's single-line JSON.
    /// `label` (when non-empty) renames the device: syncd's resolution is
    /// explicit --label → existing state/sync.json label → 「这台 Mac」 (§33).
    nonisolated static func runPairJSON(label: String? = nil) -> PairResult {
        var args = ["--pair", "--json"]
        if let label, !label.isEmpty { args += ["--label", label] }
        let (code, data) = runSyncdData(args)
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let cid = obj["channel_id"] as? String,
              let blob = obj["qr_blob"] as? String, !blob.isEmpty else {
            if code == 127 {
                return .failed(L("找不到可用的 python——先在「通用 · 权限体检 / 初始设置向导」里装好运行环境。",
                                 "No usable python — set up the runtime first in General · Setup wizard."))
            }
            return .failed(L("配对失败——请检查网络后重试（或看 state/syncd.log）。",
                             "Pairing failed — check your network and retry (see state/syncd.log)."))
        }
        return .ok(channelId: cid,
                   blob: blob,
                   label: obj["label"] as? String ?? "",
                   registered: obj["registered"] as? Bool ?? false)
    }

    /// Run `python3 -m act.syncd <args>` returning (exit code, stdout bytes).
    nonisolated static func runSyncdData(_ args: [String]) -> (Int32, Data) {
        let py = RuntimePython.resolve()
        let root = AppPaths.stateRoot
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = ["-m", "act.syncd"] + args
        p.currentDirectoryURL = URL(fileURLWithPath: root, isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        env["AIASSISTANT_HOME"] = root
        env["AIASSISTANT_UI_LANG"] = LanguageMirror.current   // §15: python copy matches the app language
        p.environment = env
        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe
        do { try p.run() } catch {
            return (127, Data())
        }
        // stdout carries the JSON; drain stderr (syncd logs to its logfile, so
        // stderr stays tiny) after stdout EOF to avoid a full-pipe stall.
        let out = outPipe.fileHandleForReading.readDataToEndOfFile()
        _ = errPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, out)
    }

    nonisolated static func runSyncd(_ args: [String]) -> (Int32, Data) {
        runSyncdData(args)
    }

    static func readSyncConfig() -> [String: Any] {
        let path = AppPaths.stateRoot + "/state/sync.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return [:] }
        return obj
    }

    // MARK: QR rendering (CIQRCodeGenerator → crisp scale-up → NSImage)

    nonisolated static func qrImage(from text: String, size: CGFloat = 220) -> NSImage? {
        guard let data = text.data(using: .utf8),
              let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        filter.setValue(data, forKey: "inputMessage")
        filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let output = filter.outputImage, output.extent.width > 0 else { return nil }
        // scale the tiny generator output up with an affine transform; nearest
        // sampling keeps the module edges crisp (no bilinear blur).
        let scale = size / output.extent.width
        let scaled = output
            .samplingNearest()
            .transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        let rep = NSCIImageRep(ciImage: scaled)
        let image = NSImage(size: rep.size)
        image.addRepresentation(rep)
        return image
    }
}

// MARK: - View

struct SyncSettingsSection: View {
    @StateObject private var model = SyncSettingsModel()
    @ObservedObject private var i18n = LanguageStore.shared

    // Content-only (v0.21): the card / title / collapse chrome is supplied by
    // the shared CollapsibleSection wrapper it's registered in (Settings.swift).
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("把这台 Mac 的看板同步到手机,就能在手机上查看、远程审批。开启后生成一个配对二维码——在手机 App 里扫一次即可。卡片正文端到端加密,服务器和维护者都读不到明文。此区改动即时生效。",
                   "Sync this Mac's board to your phone so you can view it and approve remotely. Turning it on generates a pairing QR — scan it once in the phone app. Card bodies are end-to-end encrypted; neither the server nor the maintainer can read them. Changes apply immediately."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Toggle(L("开启同步 / 配对", "Enable sync / pairing"), isOn: Binding(
                get: { model.enabled },
                set: { model.setEnabled($0) }))
                .toggleStyle(.switch)
                .disabled(model.busy)

            if !model.statusNote.isEmpty {
                HStack(spacing: 6) {
                    if model.busy { ProgressView().controlSize(.small) }
                    Text(model.statusNote)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if !model.errorNote.isEmpty {
                Text(model.errorNote)
                    .font(.system(size: 11))
                    .foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }

            if model.enabled { qrCard }
        }
        .font(.system(size: 12))
        .onAppear { model.loadIfNeeded() }
    }

    private var qrCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let img = model.qrImage {
                HStack {
                    Spacer()
                    Image(nsImage: img)
                        .interpolation(.none)
                        .resizable()
                        .frame(width: 220, height: 220)
                        .padding(8)
                        .background(Color.white)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    Spacer()
                }
                Text(L("在手机 App 里扫这个码配对（每台 Mac 扫一次）",
                       "Scan this in the phone app to pair (once per Mac)."))
                    .font(.system(size: 11))
                    .frame(maxWidth: .infinity, alignment: .center)
            } else if model.busy {
                HStack { Spacer(); ProgressView(); Spacer() }
                    .frame(height: 220)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(L("设备名称:", "Device name:"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    TextField(SyncSettingsModel.defaultDeviceName, text: $model.label)
                        .textFieldStyle(.roundedBorder)
                        .controlSize(.small)
                        .font(.system(size: 11))
                        .frame(maxWidth: 200)
                        .disabled(model.busy)
                        .onSubmit { model.commitLabel() }
                    Button(L("保存", "Save")) { model.commitLabel() }
                        .controlSize(.small)
                        .disabled(model.busy || !model.labelDirty)
                }
                Text(L("这个名字会显示在手机 App 里。改名立即进二维码;已配对的手机在下一次刷新看板时自动更新名字,不用重新扫码。",
                       "This name shows in the phone app. A rename goes into the QR immediately; already-paired phones pick up the new name on their next board refresh — no re-scan needed."))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text(L("⚠️ 这个二维码就是主密钥——谁扫到就能看你的看板、还能替你操作。别截图群发、别贴到公开的地方。",
                   "⚠️ This QR is the master key — anyone who scans it can read your board and act on your behalf. Don't share screenshots or post it anywhere public."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                Button(L("重新生成", "Re-pair")) { model.regenerate() }
                    .controlSize(.small)
                    .disabled(model.busy)
                Spacer()
                if !model.channelId.isEmpty {
                    Text(model.channelId)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
