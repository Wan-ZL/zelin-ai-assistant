// CaptionOverlay.swift — the 实时字幕 always-on-top overlay: a borderless
// NON-ACTIVATING NSPanel (lyrics style) that floats over every app and Space,
// survives the app's .accessory↔.regular activation-policy flips
// (MainWindow.swift), and never steals focus from what the user is doing.
// Singleton controller modeled on PermissionsWindowController.

import AppKit
import SwiftUI

@MainActor
final class CaptionOverlayController {
    static let shared = CaptionOverlayController()
    private var panel: NSPanel?

    func show() {
        if panel == nil {
            let p = NSPanel(contentRect: Self.defaultFrame(),
                            styleMask: [.borderless, .nonactivatingPanel, .resizable],
                            backing: .buffered, defer: false)
            p.isFloatingPanel = true
            p.level = .statusBar
            p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            p.isReleasedWhenClosed = false
            p.backgroundColor = .clear
            p.isOpaque = false
            p.hasShadow = false
            p.hidesOnDeactivate = false
            p.becomesKeyOnlyIfNeeded = true
            p.isMovableByWindowBackground = true
            p.minSize = NSSize(width: 320, height: 72)
            p.maxSize = NSSize(width: 2400, height: 480)
            p.contentViewController = NSHostingController(rootView: CaptionOverlayView())
            // restores the user's dragged position/size across launches
            // (falls back to the bottom-center default frame above)
            p.setFrameAutosaveName("liveCaptionsPanel")
            panel = p
        }
        panel?.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    /// Bottom-center of the main screen — the lyrics spot.
    private static func defaultFrame() -> NSRect {
        let visible = NSScreen.main?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let width = min(760.0, visible.width * 0.6)
        let height = 110.0
        return NSRect(x: visible.midX - width / 2, y: visible.minY + 60,
                      width: width, height: height)
    }
}

// MARK: - view

struct CaptionOverlayView: View {
    @ObservedObject private var cap = LiveCaptionsController.shared
    @ObservedObject private var i18n = LanguageStore.shared
    @State private var hovering = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            VStack(alignment: .leading, spacing: 4) {
                Spacer(minLength: 0)
                // status precedence lives in CaptionDisplayState (CaptionCore,
                // tested): the paused label always outranks engine status, so
                // a late .listening can never claim we're listening while
                // nothing is captured (review G)
                if let status = displayState.statusLine(pausedLabel: Self.pausedLabel) {
                    Text(status.text)
                        .font(.system(size: 11))
                        .foregroundColor(status.isError ? .orange
                            : cap.paused ? .yellow.opacity(0.9) : .white.opacity(0.55))
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !cap.sourceNote.isEmpty && !cap.paused {
                    Text(cap.sourceNote)
                        .font(.system(size: 11))
                        .foregroundColor(.orange.opacity(0.9))
                        .fixedSize(horizontal: false, vertical: true)
                }
                finalBlock
                if !cap.lines.liveText.isEmpty {
                    captionText(cap.lines.liveText, size: cap.fontSize * 0.8,
                                color: .white.opacity(0.6))
                }
                if idle {
                    Text(L("实时字幕正在听…", "Live captions — listening…"))
                        .font(.system(size: 12))
                        .foregroundColor(.white.opacity(0.4))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

            if hovering { controlStrip }
        }
        .background(RoundedRectangle(cornerRadius: 12)
            .fill(Color.black.opacity(cap.opacity)))
        .onHover { hovering = $0 }
    }

    static var pausedLabel: String {
        L("已暂停 — 未在采集，也不计费", "Paused — nothing is captured or billed")
    }

    private var displayState: CaptionDisplayState {
        CaptionDisplayState(paused: cap.paused, statusText: cap.statusText,
                            statusIsError: cap.statusIsError)
    }

    /// Nothing to show yet (and no status line explaining why).
    private var idle: Bool {
        !cap.paused && cap.statusText.isEmpty && cap.sourceNote.isEmpty
            && cap.lines.finalText.isEmpty && cap.lines.liveText.isEmpty
    }

    // top line: last finalized sentence — bilingual mode renders the pair as
    // 原文小字 + 译文大字, captions-only mode renders the original big
    @ViewBuilder private var finalBlock: some View {
        if !cap.lines.finalText.isEmpty {
            if cap.translationActive {
                captionText(cap.lines.finalText, size: cap.fontSize * 0.6,
                            color: .white.opacity(0.75))
                if !cap.lines.finalTranslation.isEmpty {
                    captionText(cap.lines.finalTranslation, size: cap.fontSize,
                                color: .white)
                }
            } else {
                captionText(cap.lines.finalText, size: cap.fontSize, color: .white)
            }
        }
    }

    private func captionText(_ text: String, size: Double, color: Color) -> some View {
        Text(text)
            .font(.system(size: size, weight: .semibold))
            .foregroundColor(color)
            .shadow(color: .black.opacity(0.8), radius: 2, x: 0, y: 1)
            .fixedSize(horizontal: false, vertical: true)
    }

    // hover-only strip: pause / settings / close
    private var controlStrip: some View {
        HStack(spacing: 10) {
            // engineDead = capture already stopped; nothing left to pause
            // (toggling off / fixing the key in settings are the real moves)
            if !cap.engineDead {
                Button {
                    cap.togglePause()
                } label: {
                    Image(systemName: cap.paused ? "play.fill" : "pause.fill")
                }
                .help(cap.paused ? L("继续", "Resume")
                                 : L("暂停（停止采集与计费）", "Pause (stops capture and billing)"))
            }
            Button {
                MainNav.shared.pendingAnchor = "live_captions"
                MainNav.shared.section = .settings
                (NSApp.delegate as? AppDelegate)?.openMainWindow(nil)
            } label: {
                Image(systemName: "gearshape.fill")
            }
            .help(L("字幕设置", "Caption settings"))
            Button {
                cap.setEnabled(false)
            } label: {
                Image(systemName: "xmark.circle.fill")
            }
            .help(L("关闭实时字幕", "Turn off live captions"))
        }
        .buttonStyle(.plain)
        .font(.system(size: 13))
        .foregroundColor(.white.opacity(0.85))
        .padding(8)
    }
}
