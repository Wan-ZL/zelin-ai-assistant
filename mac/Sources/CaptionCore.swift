// CaptionCore.swift — pure logic for 实时字幕 (live captions): the Doubao
// streaming-ASR binary frame codec, the server-payload interpreter, and the
// 2-line caption roll-up reducer.
//
// Foundation + Compression ONLY — no AppKit/SwiftUI — so ios/tests/captions
// can compile this file with plain swiftc and assert on byte-exact frames and
// reducer sequences (same harness style as ios/tests/contract). Keep every
// network/UI concern in LiveCaptions.swift / CaptionOverlay.swift.

import Foundation
import Compression

// MARK: - Doubao streaming-ASR wire framing (火山 sauc v3 binary protocol)
//
// Every WebSocket message is: a 4-byte header, an optional 4-byte big-endian
// sequence (when the flags nibble says so), for error frames a 4-byte
// big-endian error code, then a 4-byte big-endian payload size + payload.
//
//   byte 0: (protocol version << 4) | header size in 4-byte units  → 0x11
//   byte 1: (message type << 4) | message-type-specific flags
//   byte 2: (serialization << 4) | compression
//   byte 3: reserved (0x00)
//
// Client → server: one "full client request" (JSON config, sequence 1), then
// audio-only frames with increasing sequence; the FINAL audio frame flips the
// flags to 0b0011 and carries the NEGATIVE sequence (protocol's end marker).

enum DoubaoFrame {
    // message types (high nibble of byte 1)
    static let typeFullClient: UInt8 = 0b0001
    static let typeAudioOnly: UInt8 = 0b0010
    static let typeFullServer: UInt8 = 0b1001
    static let typeError: UInt8 = 0b1111
    // message-type-specific flags (low nibble of byte 1)
    static let flagPosSequence: UInt8 = 0b0001
    static let flagNegSequence: UInt8 = 0b0011  // last frame: sequence < 0
    // serialization / compression nibbles (byte 2)
    static let serialJSON: UInt8 = 0b0001
    static let serialRaw: UInt8 = 0b0000
    static let compressNone: UInt8 = 0b0000
    static let compressGzip: UInt8 = 0b0001

    private static func header(type: UInt8, flags: UInt8, serial: UInt8,
                               compress: UInt8) -> [UInt8] {
        [0x11, (type << 4) | flags, (serial << 4) | compress, 0x00]
    }

    private static func beInt32(_ v: Int32) -> [UInt8] {
        let u = UInt32(bitPattern: v)
        return [UInt8(u >> 24 & 0xFF), UInt8(u >> 16 & 0xFF),
                UInt8(u >> 8 & 0xFF), UInt8(u & 0xFF)]
    }

    /// First frame of a session: the JSON config payload, sequence 1.
    /// Payload is sent UNCOMPRESSED — the sauc server accepts it and it keeps
    /// this codec free of a gzip *encoder*.
    static func fullClientRequest(json payload: Data, sequence: Int32) -> Data {
        var out = header(type: typeFullClient, flags: flagPosSequence,
                         serial: serialJSON, compress: compressNone)
        out += beInt32(sequence)
        out += beInt32(Int32(payload.count))
        return Data(out) + payload
    }

    /// Audio frame (raw pcm_s16le bytes). `last: true` emits the protocol's
    /// end marker: flags 0b0011 + negative sequence.
    static func audioFrame(_ audio: Data, sequence: Int32, last: Bool) -> Data {
        var out = header(type: typeAudioOnly,
                         flags: last ? flagNegSequence : flagPosSequence,
                         serial: serialRaw, compress: compressNone)
        out += beInt32(last ? -sequence : sequence)
        out += beInt32(Int32(audio.count))
        return Data(out) + audio
    }

    struct ServerFrame: Equatable {
        let messageType: UInt8
        let flags: UInt8
        let sequence: Int32?
        let errorCode: UInt32?  // only for typeError frames
        let payload: Data       // already gunzipped when the server compressed
        var isError: Bool { messageType == DoubaoFrame.typeError }
        /// Server marks its final frame the same way the client does (bit 1).
        var isLast: Bool { flags & 0b0010 != 0 }
    }

    /// Parse one server WebSocket message. nil = malformed/truncated frame
    /// (callers drop it and keep the stream alive).
    static func parseServerFrame(_ data: Data) -> ServerFrame? {
        let bytes = [UInt8](data)
        guard bytes.count >= 4 else { return nil }
        let headerSize = Int(bytes[0] & 0x0F) * 4
        guard headerSize >= 4, bytes.count >= headerSize else { return nil }
        let type = bytes[1] >> 4
        let flags = bytes[1] & 0x0F
        let compression = bytes[2] & 0x0F
        var idx = headerSize

        func readBE32() -> UInt32? {
            guard idx + 4 <= bytes.count else { return nil }
            let v = UInt32(bytes[idx]) << 24 | UInt32(bytes[idx + 1]) << 16
                | UInt32(bytes[idx + 2]) << 8 | UInt32(bytes[idx + 3])
            idx += 4
            return v
        }

        var sequence: Int32?
        if flags & 0x01 != 0 {
            guard let raw = readBE32() else { return nil }
            sequence = Int32(bitPattern: raw)
        }
        var errorCode: UInt32?
        if type == typeError {
            guard let raw = readBE32() else { return nil }
            errorCode = raw
        }
        guard let size = readBE32(), idx + Int(size) <= bytes.count else { return nil }
        var payload = Data(bytes[idx..<idx + Int(size)])
        if compression == compressGzip {
            guard let plain = gunzip(payload) else { return nil }
            payload = plain
        }
        return ServerFrame(messageType: type, flags: flags, sequence: sequence,
                           errorCode: errorCode, payload: payload)
    }

    /// RFC 1952 gzip → plain bytes. libcompression's COMPRESSION_ZLIB is RAW
    /// deflate, so strip the gzip wrapper (header + 8-byte trailer) first.
    /// CRC is not verified — a corrupt payload just fails JSON parsing later.
    static func gunzip(_ data: Data) -> Data? {
        let bytes = [UInt8](data)
        guard bytes.count > 18, bytes[0] == 0x1F, bytes[1] == 0x8B,
              bytes[2] == 0x08 else { return nil }
        let flg = bytes[3]
        var idx = 10
        if flg & 0x04 != 0 {  // FEXTRA: 2-byte little-endian length + payload
            guard idx + 2 <= bytes.count else { return nil }
            idx += 2 + (Int(bytes[idx]) | Int(bytes[idx + 1]) << 8)
        }
        if flg & 0x08 != 0 {  // FNAME: NUL-terminated
            while idx < bytes.count, bytes[idx] != 0 { idx += 1 }
            idx += 1
        }
        if flg & 0x10 != 0 {  // FCOMMENT: NUL-terminated
            while idx < bytes.count, bytes[idx] != 0 { idx += 1 }
            idx += 1
        }
        if flg & 0x02 != 0 { idx += 2 }  // FHCRC
        guard idx < bytes.count - 8 else { return nil }
        return inflateRaw(Data(bytes[idx..<bytes.count - 8]))
    }

    private static func inflateRaw(_ data: Data) -> Data? {
        var capacity = max(data.count * 8, 1 << 16)
        // grow-and-retry: a full output buffer means it was too small
        for _ in 0..<6 {
            let dst = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
            defer { dst.deallocate() }
            let written = data.withUnsafeBytes { (src: UnsafeRawBufferPointer) -> Int in
                guard let base = src.bindMemory(to: UInt8.self).baseAddress else { return 0 }
                return compression_decode_buffer(dst, capacity, base, data.count,
                                                 nil, COMPRESSION_ZLIB)
            }
            if written == 0 { return nil }
            if written < capacity { return Data(bytes: dst, count: written) }
            capacity *= 4
        }
        return nil
    }
}

// MARK: - server payload → finals / partial

/// One server response, interpreted: sentences newly finalized by this
/// response (in order) + the current in-progress utterance ("" = none).
struct ASRUpdate: Equatable {
    var finals: [String] = []
    var partial = ""
}

/// Per-connection interpreter for sauc JSON payloads. Responses restate
/// utterances with `definite: true` once a sentence closes (end_window_size
/// VAD); `start_time` identifies each utterance, so definite ones are emitted
/// exactly once even when later responses restate them. Utterances missing
/// start_time fall back to "always new" (rolling-window servers) — captions
/// may repeat rather than silently drop.
struct DoubaoSession {
    private var lastFinalStart = -1.0

    mutating func interpret(payload: Data) -> ASRUpdate? {
        guard let obj = (try? JSONSerialization.jsonObject(with: payload))
                as? [String: Any],
              let result = obj["result"] as? [String: Any] else { return nil }
        guard let utterances = result["utterances"] as? [[String: Any]] else {
            // show_utterances off / degraded server: full text as the live line
            if let text = result["text"] as? String {
                return ASRUpdate(finals: [], partial: text)
            }
            return nil
        }
        var update = ASRUpdate()
        var partials: [String] = []
        for utt in utterances {
            guard let text = utt["text"] as? String else { continue }
            let definite = utt["definite"] as? Bool ?? false
            if definite {
                if let start = numeric(utt["start_time"]) {
                    if start > lastFinalStart {
                        lastFinalStart = start
                        update.finals.append(text)
                    }
                } else {
                    update.finals.append(text)
                }
            } else {
                partials.append(text)
            }
        }
        update.partial = partials.joined()
        return update
    }

    private func numeric(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        return nil
    }
}

// MARK: - 2-line roll-up reducer

/// What the overlay renders: top = the last finalized sentence (plus its
/// translation once it streams in), bottom = the live partial, dimmed.
struct CaptionLines: Equatable {
    /// Monotonic id of the finalized sentence on the top line; 0 = none yet.
    /// Translation deltas attach by this id so a late-arriving stream for an
    /// already-replaced sentence can never clobber the newer one.
    var finalID = 0
    var finalText = ""
    var finalTranslation = ""
    var liveText = ""
}

struct CaptionReducer {
    private(set) var lines = CaptionLines()
    private var nextID = 1

    mutating func partial(_ text: String) {
        lines.liveText = text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A sentence closed: push it to the top line, clear the live line.
    /// Returns the sentence id for the translation stream to attach to;
    /// nil for whitespace-only finals (still clears the live line).
    @discardableResult
    mutating func finalize(_ text: String) -> Int? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        lines.liveText = ""
        guard !t.isEmpty else { return nil }
        lines.finalID = nextID
        nextID += 1
        lines.finalText = t
        lines.finalTranslation = ""
        return lines.finalID
    }

    /// Streaming translation update (full accumulated text so far) for the
    /// sentence `id`. Dropped when the top line already moved on.
    mutating func translation(_ id: Int, _ text: String) {
        guard id == lines.finalID else { return }
        lines.finalTranslation = text
    }

    /// Clear the display (pause/engine switch). Ids stay monotonic so stale
    /// translation streams from before the reset can never re-attach.
    mutating func reset() {
        lines = CaptionLines()
    }
}

// MARK: - async ownership gate
//
// PR #51 review: every lifecycle finding traced to ONE structural root —
// async completions (WS receive, reconnect timers, TCC callbacks,
// capture-start Tasks) applying side effects without re-checking that they
// still own the pipeline they were issued for. This gate is the shared fix:
// a completion captures `token` when issued; every teardown/restart calls
// bump(); a stale completion fails isCurrent() and must apply NOTHING.

struct AsyncGate: Equatable {
    private(set) var token = 0

    /// Invalidate every outstanding completion; returns the new current token.
    @discardableResult
    mutating func bump() -> Int {
        token += 1
        return token
    }

    func isCurrent(_ t: Int) -> Bool { t == token }
}

// MARK: - pcm mixing

enum CaptionMixer {
    /// Sum two 16 kHz mono s16 streams with saturation; the shorter one is
    /// zero-padded (one silent source must not mute the other). Empty inputs
    /// pass the other stream through untouched.
    static func mix(_ a: [Int16], _ b: [Int16]) -> [Int16] {
        if a.isEmpty { return b }
        if b.isEmpty { return a }
        var out = [Int16](repeating: 0, count: max(a.count, b.count))
        for i in 0..<out.count {
            let sum = Int(i < a.count ? a[i] : 0) + Int(i < b.count ? b[i] : 0)
            out[i] = Int16(clamping: sum)
        }
        return out
    }
}

// MARK: - overlay status precedence
//
// Review G: pause is USER intent and must never be overwritten by engine
// chatter (a late .listening once wiped the 已暂停 display, so the overlay
// claimed to listen while feeding nothing). One pure source of truth for
// what the status area shows, so the precedence is executable + tested.

struct CaptionDisplayState: Equatable {
    var paused = false
    var statusText = ""
    var statusIsError = false

    /// The status line to render: the paused label always wins; then the
    /// engine status/error; nil = listening normally (no line).
    func statusLine(pausedLabel: String) -> (text: String, isError: Bool)? {
        if paused { return (pausedLabel, false) }
        if statusText.isEmpty { return nil }
        return (statusText, statusIsError)
    }
}

// MARK: - translation direction

enum TranslateDirection {
    /// Target language ("en" / "zh") for one sentence. mode ∈
    /// "auto" | "zh2en" | "en2zh"; auto = script sniff (CJK share > 30%
    /// of letters → the sentence is Chinese → translate to English).
    static func target(for text: String, mode: String) -> String {
        switch mode {
        case "zh2en": return "en"
        case "en2zh": return "zh"
        default:
            var cjk = 0, letters = 0
            for scalar in text.unicodeScalars {
                let v = scalar.value
                let isCJK = (0x4E00...0x9FFF).contains(v)
                    || (0x3400...0x4DBF).contains(v)
                    || (0x3000...0x303F).contains(v)
                if isCJK { cjk += 1; letters += 1 }
                else if scalar.properties.isAlphabetic { letters += 1 }
            }
            guard letters > 0 else { return "zh" }
            return Double(cjk) / Double(letters) > 0.3 ? "en" : "zh"
        }
    }
}
