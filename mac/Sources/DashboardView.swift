// DashboardView.swift — RecordingMenuButton（录制控制按钮，看板 header 使用）
// The popover root view that used to live here was removed in v0.48.x
// together with the menu-bar popover (§15 v0.48.x 追记).

import AppKit
import SwiftUI
import Foundation

// MARK: - SwiftUI views

/// Recording control in the board header (replaces the former separate
/// status-bar item). Dot red while the engine records; menu = mode picker
/// plus a permission escape hatch when TCC blocks the engine. The status
/// item's right-click 录制 submenu mirrors the same semantics (AppDelegate).
struct RecordingMenuButton: View {
    @ObservedObject private var rec = RecordingController.shared
    @ObservedObject private var cap = LiveCaptionsController.shared
    @ObservedObject private var i18n = LanguageStore.shared
    // 契约D feedback: transient 重启中… next to the button text after a mode
    // switch / engine restart; the 5-s refresh loop (refreshEngineState) then
    // takes over via statusLabel/statusColor. Token: an older timer must not
    // clear a newer flash (same pattern as Cards.swift copied-feedback).
    @State private var restarting = false
    @State private var restartToken = 0

    var body: some View {
        Menu {
            if rec.mode != "off" && !rec.engineRunning {
                Text(deadReason)
            } else {
                Text(L("录制：", "Recording: ") + stateWord)
            }
            if !rec.recordingNote.isEmpty {
                // refused / rolled-back mode switch — the durable in-app
                // explanation (15 s; notifications may be denied)
                Text(rec.recordingNote)
            }
            Divider()
            ForEach(modes, id: \.0) { m, label in
                Button {
                    rec.setMode(m)
                    // "off" just stops — no engine spin-up to wait on
                    if m != "off" { flashRestarting() }
                } label: {
                    if rec.mode == m {
                        Label(label, systemImage: "checkmark")
                    } else {
                        Text(label)
                    }
                }
            }
            Divider()
            // v0.36 实时字幕 — an independent Bool, deliberately NOT a 4th
            // recordingMode (frozen vocabulary, CONTRACT §15): the overlay
            // runs its own in-process capture, orthogonal to the engine.
            Button {
                cap.setEnabled(!cap.enabled)
            } label: {
                if cap.enabled && cap.engineDead {
                    // engine failed fatally: capture is stopped and the
                    // overlay shows why — a plain checkmark would lie
                    Label(L("实时字幕（出错，见悬浮窗）", "Live captions (error — see overlay)"),
                          systemImage: "exclamationmark.triangle")
                } else if cap.enabled && cap.paused {
                    Label(L("实时字幕（已暂停）", "Live captions (paused)"),
                          systemImage: "pause.circle")
                } else if cap.enabled {
                    Label(L("实时字幕", "Live captions"), systemImage: "checkmark")
                } else {
                    Text(L("实时字幕", "Live captions"))
                }
            }
            Divider()
            // 契约D: explicit engine restart — same semantics as re-picking
            // the current mode (restartEngine logs "recording_restart" itself).
            Button(L("重启录制引擎", "Restart recording engine")) {
                rec.restartEngine()
                flashRestarting()
            }
            .disabled(rec.mode == "off")
            if !RecordingController.hasScreenPermission() {
                Divider()
                Button(L("打开系统设置 → 屏幕录制",
                         "Open System Settings → Screen Recording")) {
                    RecordingController.openScreenRecordingSettings()
                }
            }
            // ffmpeg is the diagnosis, or the transient refusal note is up
            // (the note names ffmpeg in either language) — offer the fix.
            if rec.diagnosis?.failureId == "engine_ffmpeg_missing"
                || rec.recordingNote.contains("ffmpeg") {
                Divider()
                Button(L("安装 ffmpeg…", "Install ffmpeg…")) {
                    FailureCatalog.perform("engine_ffmpeg_missing")
                }
            }
        } label: {
            // icon + text (Zelin: icon alone is not readable at a glance)
            HStack(spacing: 4) {
                Image(systemName: symbol)
                Text(statusLabel)
                if restarting {
                    Text(L("重启中…", "restarting…"))
                        .foregroundColor(.orange)
                }
            }
            .font(.system(size: 12))
            .foregroundColor(statusColor)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(L("录制控制", "Recording controls"))
    }

    // Dead-engine menu line: name the ACTUAL classified cause (diagnoseEngine)
    // instead of always guessing "permissions" — 2026-07-13 the engine was
    // dying on missing ffmpeg while this line pointed users at Screen
    // Recording TCC. Permission first (it IS checked here), then the §25 id.
    private var deadReason: String {
        if !RecordingController.hasScreenPermission() {
            return L("未在录制 — 缺「屏幕录制」权限",
                     "Not recording — missing Screen Recording permission")
        }
        switch rec.diagnosis?.failureId {
        case "engine_ffmpeg_missing":
            return L("未在录制 — 缺 ffmpeg（「屏幕+音频」需要）",
                     "Not recording — ffmpeg is missing (Screen + Audio needs it)")
        case "node_missing":
            return L("未在录制 — 缺 Node.js", "Not recording — Node.js is missing")
        case "engine_npm_download":
            // in practice unreachable here (a downloading npx wrapper already
            // matches the liveness pgrep, so engineRunning stays true) — kept
            // as defense against a future spawn-shape change
            return L("引擎首次下载中…", "Engine downloading (first run)…")
        case "engine_crashed":
            return L("未在录制 — 引擎意外停了（详见录制页）",
                     "Not recording — the engine stopped unexpectedly (see Recording page)")
        default:
            return L("未在录制", "Not recording")
        }
    }

    // Show 重启中… for a few seconds, then let the normal state refresh speak.
    private func flashRestarting() {
        restartToken += 1
        let token = restartToken
        restarting = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            if restartToken == token { restarting = false }
        }
    }

    // 契约4 recording terms — this board-header button carries the
    // 录制：/Rec: prefix; in-page status lines (Pages side, 桶C) use the
    // bare words.
    private var statusLabel: String {
        L("录制：", "Rec: ") + stateWord
    }

    private var stateWord: String {
        if rec.mode == "off" { return L("关", "Off") }
        if !rec.engineRunning { return L("未在录制", "Not recording") }
        return rec.mode == "screen_audio" ? L("屏幕+音频", "Screen + audio")
                                          : L("仅屏幕", "Screen only")
    }

    private var statusColor: Color {
        if rec.mode == "off" { return .secondary }
        return rec.engineRunning ? .red : .orange
    }

    private var modes: [(String, String)] {
        [("off", L("关", "Off")),
         ("screen", L("仅屏幕", "Screen only")),
         ("screen_audio", L("屏幕+音频", "Screen + audio"))]
    }

    private var symbol: String {
        switch rec.mode {
        case "off": return "record.circle"
        case "screen_audio": return "waveform.circle.fill"
        default: return "record.circle.fill"
        }
    }
}

// (v0.48.x) DashboardView — the menu-bar popover's root view — was removed
// with the popover itself (§15 v0.48.x 追记): the status-item click now opens
// the main window, whose kanban/sidebar pages carry every popover affordance
// (PipelineHealthBanner/一键修复, DiagnosticsStrip, capture composer, trash,
// archive).

