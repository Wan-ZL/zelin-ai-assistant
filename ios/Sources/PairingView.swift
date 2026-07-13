// PairingView.swift — screen 2 (QR-only v2): scan a Mac's pairing QR, parse the
// opaque channel blob in-app (never a URL scheme), and store the channel
// {channel_id, write_secret, K, label} in the Keychain (ThisDeviceOnly).
// Scanning another Mac adds another channel (multi-channel). A paste fallback
// lets the simulator / a camera-less device pair by pasting the blob text.

import SwiftUI
import AVFoundation
import AudioToolbox

struct PairingView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var scanned: E2E.ChannelPairing?
    @State private var pasteText = ""
    @State private var errorText: String?
    @State private var showPaste = false

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                QRScannerView { handle($0) }
                    .ignoresSafeArea(edges: .horizontal)
                VStack {
                    Spacer()
                    Text(L("对准 Mac 上的配对二维码", "Point at the pairing QR on your Mac"))
                        .font(.subheadline).padding(8)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(.bottom, 24)
                }
            }
            .frame(maxHeight: .infinity)

            if let errorText {
                Text(errorText).font(.footnote).foregroundStyle(.red).padding(.horizontal)
            }

            Button(L("改为粘贴配对码", "Paste the pairing code instead")) { showPaste = true }
                .padding(.vertical, 12)
        }
        .navigationTitle(L("配对 Mac", "Pair a Mac"))
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showPaste) { pasteSheet }
        .alert(item: $scanned) { info in
            Alert(
                title: Text(L("配对这台设备？", "Pair this device?")),
                message: Text(info.label),
                primaryButton: .default(Text(L("配对", "Pair"))) {
                    state.addChannel(info)
                    Task { await state.refreshEverything() }
                    dismiss()
                },
                secondaryButton: .cancel(Text(L("取消", "Cancel"))))
        }
    }

    private var pasteSheet: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(L("粘贴配对码…", "Paste pairing code…"), text: $pasteText, axis: .vertical)
                        .lineLimit(3...6).autocorrectionDisabled().textInputAutocapitalization(.never)
                } footer: {
                    Text(L("在 Mac 上「开启云同步」后可拷贝这串配对码。", "Copy this from the Mac after enabling cloud sync."))
                }
                Button(L("配对", "Pair")) { handle(pasteText); showPaste = false }
                    .disabled(pasteText.isEmpty)
            }
            .navigationTitle(L("粘贴配对码", "Paste pairing code"))
        }
    }

    private func handle(_ blob: String) {
        do {
            scanned = try E2E.parseChannelQR(blob)
            errorText = nil
        } catch {
            errorText = L("配对码无效或已损坏。", "That pairing code is invalid or corrupted.")
        }
    }
}

// AVFoundation QR scanner wrapped for SwiftUI. Deduplicates back-to-back reads.
struct QRScannerView: UIViewControllerRepresentable {
    let onFound: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerVC {
        let vc = ScannerVC(); vc.onFound = onFound; return vc
    }
    func updateUIViewController(_ vc: ScannerVC, context: Context) {}

    final class ScannerVC: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
        var onFound: ((String) -> Void)?
        private let session = AVCaptureSession()
        private var lastValue: String?

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .black
            guard let device = AVCaptureDevice.default(for: .video),
                  let input = try? AVCaptureDeviceInput(device: device),
                  session.canAddInput(input) else { return }
            session.addInput(input)
            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else { return }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = [.qr]
            let preview = AVCaptureVideoPreviewLayer(session: session)
            preview.frame = view.layer.bounds
            preview.videoGravity = .resizeAspectFill
            view.layer.addSublayer(preview)
        }

        override func viewWillAppear(_ animated: Bool) {
            super.viewWillAppear(animated)
            if !session.isRunning { DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() } }
        }
        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            if session.isRunning { session.stopRunning() }
        }

        func metadataOutput(_ output: AVCaptureMetadataOutput,
                            didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
            guard let obj = objects.first as? AVMetadataMachineReadableCodeObject,
                  let value = obj.stringValue, value != lastValue else { return }
            lastValue = value
            AudioServicesPlaySystemSound(kSystemSoundID_Vibrate)
            onFound?(value)
        }
    }
}
