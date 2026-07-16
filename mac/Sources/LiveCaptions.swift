// LiveCaptions.swift — 实时字幕 engine orchestration: own in-process audio
// capture (AVAudioEngine mic tap + ScreenCaptureKit system audio — deliberately
// independent of the screenpipe engine, which exposes no live stream) → a
// pluggable ASR engine (Doubao streaming ASR over WebSocket, or Apple's
// on-device SpeechTranscriber on macOS 26+) → CaptionReducer → the overlay.
//
// BYO-key model: the app ships NO key. Doubao needs the user's own 火山
// speech credential (SecretsIO volcano-speech-key.txt — a new-console API key
// or the legacy App ID + Access Token pair, see VolcanoSpeechCredential);
// optional translation needs a SECOND user key for Ark (volcano-ark-key.txt).
// Caption text never leaves this machine except to the user's own
// ASR/translation endpoints. Pure protocol/reducer logic lives in
// CaptionCore.swift (swiftc-testable).

import AppKit
import SwiftUI
import Foundation
import AVFoundation
import ScreenCaptureKit
#if compiler(>=6.2)
import Speech
#endif

struct CaptionError: Error {
    let message: String
}

// MARK: - thread-safe PCM FIFO (audio threads push, main-actor timer pops)

final class PCMFifo: @unchecked Sendable {
    private var samples: [Int16] = []
    private let lock = NSLock()
    /// 10 s cap: a stalled consumer must bound memory AND latency — captions
    /// falling a minute behind are worse than dropping the backlog.
    private let cap = 16_000 * 10

    func push(_ newSamples: UnsafeBufferPointer<Int16>) {
        lock.lock()
        defer { lock.unlock() }
        samples.append(contentsOf: newSamples)
        if samples.count > cap { samples.removeFirst(samples.count - cap) }
    }

    func pop(_ n: Int) -> [Int16] {
        lock.lock()
        defer { lock.unlock() }
        let take = min(n, samples.count)
        guard take > 0 else { return [] }
        let out = Array(samples.prefix(take))
        samples.removeFirst(take)
        return out
    }

    func drain() {
        lock.lock()
        samples.removeAll()
        lock.unlock()
    }
}

// MARK: - microphone capture (AVAudioEngine input tap → 16 kHz mono s16)

final class MicCapture {
    private let engine = AVAudioEngine()
    private var running = false
    private var fifo: PCMFifo?
    private var observer: NSObjectProtocol?
    /// Fired (main queue) when the tap could not be rebuilt after a device
    /// change — the controller surfaces an honest note instead of a silent
    /// frozen mic (review D).
    var onFailure: ((String) -> Void)?

    func start(into fifo: PCMFifo) throws {
        self.fifo = fifo
        try installTapAndStart()
        // AirPods connect / USB mic unplug / sleep-wake: macOS switches the
        // default input, the engine stops and the captured formats go stale.
        // Rebuild the tap with the fresh input format (main queue — all
        // MicCapture state is touched from the main actor only).
        observer = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine,
            queue: .main) { [weak self] _ in
            self?.handleConfigurationChange()
        }
    }

    private func installTapAndStart() throws {
        guard let fifo else {
            throw CaptionError(message: L("麦克风尚未初始化", "Microphone not initialized"))
        }
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else {
            throw CaptionError(message: L("找不到可用的麦克风", "No usable microphone found"))
        }
        guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                            sampleRate: 16_000, channels: 1,
                                            interleaved: true),
              let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
            throw CaptionError(message: L("麦克风音频格式转换失败", "Microphone format conversion failed"))
        }
        input.installTap(onBus: 0, bufferSize: 4096, format: inFormat) { buffer, _ in
            // audio render thread — convert to 16 k mono s16 and hand off
            let ratio = 16_000.0 / inFormat.sampleRate
            let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
            guard let out = AVAudioPCMBuffer(pcmFormat: outFormat,
                                             frameCapacity: capacity) else { return }
            var fed = false
            var convErr: NSError?
            converter.convert(to: out, error: &convErr) { _, status in
                if fed { status.pointee = .noDataNow; return nil }
                fed = true
                status.pointee = .haveData
                return buffer
            }
            guard convErr == nil, out.frameLength > 0,
                  let ch = out.int16ChannelData else { return }
            fifo.push(UnsafeBufferPointer(start: ch[0], count: Int(out.frameLength)))
        }
        engine.prepare()
        try engine.start()
        running = true
    }

    private func handleConfigurationChange() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        do {
            try installTapAndStart()
        } catch {
            running = false
            onFailure?((error as? CaptionError)?.message
                ?? error.localizedDescription)
        }
    }

    func stop() {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        fifo = nil
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
    }
}

// MARK: - system-audio capture (ScreenCaptureKit; needs the Screen Recording
// grant the app already manages for the screenpipe engine)

final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    // stream/fifo/stopped are touched from the main actor (start/stop), the
    // cooperative pool (post-await resumes), AND SCStream's callback queue —
    // one lock serializes them all (review B).
    private let lock = NSLock()
    private var stream: SCStream?
    private var fifo: PCMFifo?
    private var stopped = false
    private let queue = DispatchQueue(label: "zelin.captions.sysaudio", qos: .userInitiated)
    /// Fired when the stream dies out from under us (display unplug, TCC pull).
    var onStopped: (@Sendable (String) -> Void)?

    // NSLock may not be called directly from async contexts (Swift 6 rule) —
    // these tiny sync helpers do the locking; the async start() calls them.
    private var isStopped: Bool {
        lock.lock()
        defer { lock.unlock() }
        return stopped
    }

    private func setFifo(_ f: PCMFifo?) {
        lock.lock()
        fifo = f
        lock.unlock()
    }

    /// Publish the started stream unless stop() raced us; false = the caller
    /// must stop the stream itself.
    private func publishStream(_ s: SCStream) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if stopped { return false }
        stream = s
        return true
    }

    /// Privacy-critical shape (review B): stop() can arrive at ANY await
    /// suspension below — every resume re-checks `stopped`, and the
    /// post-startCapture window hands the just-started stream straight to
    /// stopCapture instead of publishing it. Throws CancellationError when
    /// stopped mid-flight (callers treat that as silence, not an error note).
    func start(into fifo: PCMFifo) async throws {
        setFifo(fifo)
        let content = try await SCShareableContent
            .excludingDesktopWindows(false, onScreenWindowsOnly: false)
        if isStopped { throw CancellationError() }
        guard let display = content.displays.first else {
            throw CaptionError(message: L("找不到可捕获的显示器", "No display available to capture"))
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.excludesCurrentProcessAudio = true
        cfg.sampleRate = 16_000
        cfg.channelCount = 1
        // audio-only intent: shrink the mandatory video leg to a pixel crawl
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        if isStopped { throw CancellationError() }
        try await s.startCapture()
        guard publishStream(s) else {
            // stop() raced us — never leave the fresh stream running
            try? await s.stopCapture()
            throw CancellationError()
        }
    }

    func stop() {
        lock.lock()
        stopped = true
        let s = stream
        stream = nil
        fifo = nil
        lock.unlock()
        s?.stopCapture { _ in }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio else { return }
        lock.lock()
        let fifo = self.fifo
        let dead = stopped
        lock.unlock()
        guard !dead, let fifo else { return }
        guard let desc = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc)?.pointee
        else { return }
        try? sampleBuffer.withAudioBufferList { bufferList, _ in
            // cfg asks for 16 k mono → first buffer carries the one channel
            guard let buffer = bufferList.first, let base = buffer.mData else { return }
            let byteCount = Int(buffer.mDataByteSize)
            if asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0 {
                let floats = UnsafeBufferPointer(
                    start: base.assumingMemoryBound(to: Float32.self),
                    count: byteCount / MemoryLayout<Float32>.size)
                var out = [Int16](repeating: 0, count: floats.count)
                for i in 0..<floats.count {
                    out[i] = Int16(max(-1.0, min(1.0, floats[i])) * 32767)
                }
                out.withUnsafeBufferPointer { fifo.push($0) }
            } else if asbd.mBitsPerChannel == 16 {
                let ints = UnsafeBufferPointer(
                    start: base.assumingMemoryBound(to: Int16.self),
                    count: byteCount / MemoryLayout<Int16>.size)
                fifo.push(ints)
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        onStopped?(error.localizedDescription)
    }
}

// MARK: - engine abstraction

enum CaptionEngineStatus: Equatable {
    case connecting
    case downloadingModel
    case listening
    case reconnecting
    /// Fatal for this engine (no auto-retry) — `message` is the honest,
    /// user-facing reason (e.g. bad API key, unsupported OS).
    case failed(String)
}

@MainActor
protocol CaptionEngine: AnyObject {
    /// Both callbacks fire on the main actor.
    var onUpdate: ((ASRUpdate) -> Void)? { get set }
    var onStatus: ((CaptionEngineStatus) -> Void)? { get set }
    func start()
    func stop()
    /// 16 kHz mono s16 samples, ~100–200 ms per call.
    func feed(_ samples: [Int16])
}

// MARK: - Doubao streaming ASR (火山 sauc bigmodel_async, BYO key)

@MainActor
final class DoubaoStreamingASR: NSObject, CaptionEngine {
    var onUpdate: ((ASRUpdate) -> Void)?
    var onStatus: ((CaptionEngineStatus) -> Void)?

    // 双向流式优化版 (only replies when the result changes — best latency).
    // Resource ID picks 豆包流式语音识别 2.0 (seedasr, ¥1/h; 20 free hours).
    // Non-private + nonisolated: CaptionKeyProbe runs its 检测 handshake
    // against the SAME endpoint/resource/config so a probe success really
    // predicts an engine success.
    nonisolated static let endpoint =
        URL(string: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")!
    nonisolated static let resourceID = "volc.seedasr.sauc.duration"

    private let credential: VolcanoSpeechCredential
    private let urlSession = URLSession(configuration: .default)
    private var task: URLSessionWebSocketTask?
    private var sequence: Int32 = 1
    private var interpreter = DoubaoSession()
    private var stopped = false
    private var gotFirstFrame = false
    private var backoff: TimeInterval = 1
    /// Ownership gate (CaptionCore): EVERY connection teardown — stop(),
    /// failAuth(), scheduleReconnect() — bumps it, so the dead connection's
    /// still-registered receive callback and any queued reconnect closure
    /// fail isCurrent() and apply nothing (review A: an in-band error frame
    /// used to double-schedule reconnects into a 2^n connection storm).
    private var gate = AsyncGate()

    init(credential: VolcanoSpeechCredential) {
        self.credential = credential
    }

    /// Session config for the full client request — shared with the 检测
    /// probe (CaptionKeyProbe) so the two can never drift apart.
    nonisolated static func sessionConfig() -> [String: Any] {
        [
            "user": ["uid": "zelin-ai-assistant"],
            "audio": ["format": "pcm", "codec": "raw", "rate": 16_000,
                      "bits": 16, "channel": 1],
            "request": [
                "model_name": "bigmodel",
                "enable_punc": true,
                "enable_itn": true,
                "show_utterances": true,   // definite/partial split needs these
                "end_window_size": 500,    // ms of silence closing a sentence
            ],
        ]
    }

    func start() {
        stopped = false
        backoff = 1
        connect()
    }

    func stop() {
        stopped = true
        gate.bump()
        // polite end-of-stream marker (negative sequence), then close
        if let task {
            task.send(.data(DoubaoFrame.audioFrame(Data(), sequence: sequence, last: true))) { _ in }
            task.cancel(with: .normalClosure, reason: nil)
        }
        task = nil
    }

    func feed(_ samples: [Int16]) {
        guard !stopped, let task, !samples.isEmpty else { return }
        // s16le on the wire; arm64/x86_64 are both little-endian
        let data = samples.withUnsafeBufferPointer { Data(buffer: $0) }
        task.send(.data(DoubaoFrame.audioFrame(data, sequence: sequence, last: false))) { _ in }
        sequence &+= 1
    }

    private func connect() {
        guard !stopped else { return }
        onStatus?(.connecting)
        gotFirstFrame = false
        sequence = 1
        interpreter = DoubaoSession()
        var request = URLRequest(url: Self.endpoint)
        for h in credential.wsHeaders(resourceID: Self.resourceID,
                                      requestID: UUID().uuidString) {
            request.setValue(h.value, forHTTPHeaderField: h.field)
        }
        let t = urlSession.webSocketTask(with: request)
        task = t
        t.resume()
        if let payload = try? JSONSerialization.data(withJSONObject: Self.sessionConfig()) {
            t.send(.data(DoubaoFrame.fullClientRequest(json: payload, sequence: 1))) { _ in }
        }
        sequence = 2
        receiveLoop(t, token: gate.token)
    }

    private func receiveLoop(_ t: URLSessionWebSocketTask, token: Int) {
        t.receive { [weak self] result in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, !self.stopped, self.gate.isCurrent(token) else { return }
                    switch result {
                    case .failure(let error):
                        self.handleFailure(error)
                    case .success(let message):
                        if case .data(let data) = message { self.handleFrame(data) }
                        // handleFrame may have torn this connection down (an
                        // error frame → reconnect/auth-fail bumps the gate) —
                        // never re-arm receive on a dead task (review A)
                        guard self.gate.isCurrent(token) else { return }
                        self.receiveLoop(t, token: token)
                    }
                }
            }
        }
    }

    private func handleFrame(_ data: Data) {
        guard let frame = DoubaoFrame.parseServerFrame(data) else { return }
        if frame.isError {
            let code = frame.errorCode ?? 0
            let body = String(data: frame.payload, encoding: .utf8) ?? ""
            if code == 401 || code == 403 {
                failAuth()
            } else {
                scheduleReconnect(after: L("识别服务返回错误 \(code)：\(body)",
                                           "ASR service error \(code): \(body)"))
            }
            return
        }
        if !gotFirstFrame {
            gotFirstFrame = true
            backoff = 1
            onStatus?(.listening)
        }
        guard frame.messageType == DoubaoFrame.typeFullServer,
              let update = interpreter.interpret(payload: frame.payload) else { return }
        onUpdate?(update)
    }

    private func handleFailure(_ error: Error) {
        if let http = task?.response as? HTTPURLResponse,
           http.statusCode == 401 || http.statusCode == 403 {
            failAuth()
            return
        }
        scheduleReconnect(after: error.localizedDescription)
    }

    /// Bad/unactivated key is fatal — hammering reconnects at an auth wall
    /// would just look broken. Honest copy; the settings page has the fix.
    private func failAuth() {
        stopped = true
        gate.bump()
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        onStatus?(.failed(L("豆包语音凭证无效或未开通流式语音识别——去 设置→实时字幕 点「检测」排查",
                            "Doubao speech credential is invalid or the streaming-ASR service is not activated — click 检测 in 设置 → Live captions to diagnose")))
    }

    private func scheduleReconnect(after reason: String) {
        guard !stopped else { return }
        // bump FIRST: the dying connection's receive callback (already
        // registered) and any previously queued reconnect become stale NOW —
        // exactly one reconnect can ever be in flight (review A)
        let token = gate.bump()
        NSLog("[captions] doubao reconnect in %.0fs: %@", backoff, reason)
        onStatus?(.reconnecting)
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + backoff) {
            MainActor.assumeIsolated {
                guard !self.stopped, self.gate.isCurrent(token) else { return }
                self.connect()
            }
        }
        backoff = min(backoff * 2, 30)
    }
}

// MARK: - Apple on-device ASR (macOS 26 SpeechAnalyzer/SpeechTranscriber)
//
// compiler(>=6.2) ≈ Xcode 26 / macOS 26 SDK: on an older build toolchain this
// whole engine compiles out and appleEngineAvailable() below returns false —
// the settings copy then says exactly what is missing.

#if compiler(>=6.2)
@available(macOS 26.0, *)
@MainActor
final class AppleLocalASR: CaptionEngine {
    var onUpdate: ((ASRUpdate) -> Void)?
    var onStatus: ((CaptionEngineStatus) -> Void)?

    private let localeID: String
    private var analyzer: SpeechAnalyzer?
    private var inputBuilder: AsyncStream<AnalyzerInput>.Continuation?
    private var converter: AVAudioConverter?
    private var resultsTask: Task<Void, Never>?
    private var stopped = false

    /// localeID ∈ "zh" | "en" (settings 识别语言; single-locale engine —
    /// unlike Doubao it cannot code-switch mid-sentence).
    init(localeID: String) {
        self.localeID = localeID == "en" ? "en_US" : "zh_CN"
    }

    func start() {
        stopped = false
        onStatus?(.connecting)
        resultsTask = Task { @MainActor in
            do {
                let locale = Locale(identifier: localeID)
                let supported = await SpeechTranscriber.supportedLocales
                guard supported.contains(where: {
                    $0.identifier(.bcp47) == locale.identifier(.bcp47)
                }) else {
                    onStatus?(.failed(L("这台 Mac 的本地语音识别不支持所选语言",
                                        "On-device speech recognition does not support the selected language on this Mac")))
                    return
                }
                let transcriber = SpeechTranscriber(locale: locale,
                                                    transcriptionOptions: [],
                                                    reportingOptions: [.volatileResults],
                                                    attributeOptions: [])
                if let request = try await AssetInventory
                    .assetInstallationRequest(supporting: [transcriber]) {
                    onStatus?(.downloadingModel)
                    try await request.downloadAndInstall()
                }
                guard let format = await SpeechAnalyzer
                    .bestAvailableAudioFormat(compatibleWith: [transcriber]),
                      let inFormat = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                                   sampleRate: 16_000, channels: 1,
                                                   interleaved: true) else {
                    onStatus?(.failed(L("本地识别音频格式协商失败",
                                        "On-device recognizer audio format negotiation failed")))
                    return
                }
                converter = AVAudioConverter(from: inFormat, to: format)
                let analyzer = SpeechAnalyzer(modules: [transcriber])
                self.analyzer = analyzer
                let (sequence, builder) = AsyncStream<AnalyzerInput>.makeStream()
                inputBuilder = builder
                try await analyzer.start(inputSequence: sequence)
                onStatus?(.listening)
                for try await result in transcriber.results {
                    guard !stopped else { break }
                    let text = String(result.text.characters)
                    onUpdate?(result.isFinal
                        ? ASRUpdate(finals: [text], partial: "")
                        : ASRUpdate(finals: [], partial: text))
                }
            } catch {
                if !stopped {
                    onStatus?(.failed(L("本地识别启动失败：", "On-device recognition failed: ")
                        + error.localizedDescription))
                }
            }
        }
    }

    func stop() {
        stopped = true
        inputBuilder?.finish()
        inputBuilder = nil
        resultsTask?.cancel()
        resultsTask = nil
        if let analyzer {
            Task { await analyzer.cancelAndFinishNow() }
        }
        analyzer = nil
    }

    func feed(_ samples: [Int16]) {
        guard !stopped, let inputBuilder, let converter, !samples.isEmpty else { return }
        let inFormat = converter.inputFormat
        guard let inBuf = AVAudioPCMBuffer(pcmFormat: inFormat,
                                           frameCapacity: AVAudioFrameCount(samples.count)),
              let ch = inBuf.int16ChannelData else { return }
        inBuf.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { src in
            ch[0].update(from: src.baseAddress!, count: samples.count)
        }
        let ratio = converter.outputFormat.sampleRate / inFormat.sampleRate
        let capacity = AVAudioFrameCount(Double(samples.count) * ratio) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: converter.outputFormat,
                                         frameCapacity: capacity) else { return }
        var fed = false
        var convErr: NSError?
        // the input block runs synchronously inside convert() — the buffer
        // never actually crosses an isolation boundary
        nonisolated(unsafe) let pending = inBuf
        converter.convert(to: out, error: &convErr) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return pending
        }
        guard convErr == nil, out.frameLength > 0 else { return }
        inputBuilder.yield(AnalyzerInput(buffer: out))
    }
}
#endif

/// Runtime probe the controller + settings copy share.
func appleCaptionEngineAvailable() -> Bool {
    #if compiler(>=6.2)
    if #available(macOS 26.0, *) { return true }
    #endif
    return false
}

// MARK: - Ark translation (doubao-seed flash, OpenAI-compatible SSE; BYO key)

@MainActor
final class ArkTranslator {
    // non-private + nonisolated: CaptionKeyProbe's 检测 hits the same endpoint
    nonisolated static let endpoint =
        URL(string: "https://ark.cn-beijing.volces.com/api/v3/chat/completions")!
    private var current: Task<Void, Never>?

    /// Streams the translation of ONE finalized sentence. Only the newest
    /// sentence is ever displayed, so a new call cancels the previous stream
    /// (stops paying for tokens nobody can see).
    func translate(_ sentence: String, id: Int, target: String, model: String,
                   key: String,
                   onDelta: @escaping @MainActor (Int, String) -> Void,
                   onError: @escaping @MainActor (String) -> Void) {
        current?.cancel()
        current = Task { @MainActor in
            var request = URLRequest(url: Self.endpoint)
            request.httpMethod = "POST"
            request.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let system = target == "en"
                ? "Translate the user's sentence into English. Output only the translation — no explanations, no quotes."
                : "Translate the user's sentence into Simplified Chinese. Output only the translation — no explanations, no quotes."
            let body: [String: Any] = [
                "model": model,
                "stream": true,
                "messages": [["role": "system", "content": system],
                             ["role": "user", "content": sentence]],
            ]
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
            do {
                let (bytes, response) = try await URLSession.shared.bytes(for: request)
                if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                    onError(http.statusCode == 401 || http.statusCode == 403
                        ? L("Ark API Key 无效——翻译暂停（字幕不受影响）",
                            "Ark API key is invalid — translation paused (captions unaffected)")
                        : L("翻译请求失败（HTTP \(http.statusCode)），字幕不受影响",
                            "Translation request failed (HTTP \(http.statusCode)) — captions unaffected"))
                    return
                }
                var accumulated = ""
                for try await line in bytes.lines {
                    guard !Task.isCancelled else { return }
                    guard line.hasPrefix("data:") else { continue }
                    let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                    if json == "[DONE]" { break }
                    guard let obj = (try? JSONSerialization.jsonObject(
                            with: Data(json.utf8))) as? [String: Any],
                          let choices = obj["choices"] as? [[String: Any]],
                          let delta = choices.first?["delta"] as? [String: Any],
                          let piece = delta["content"] as? String, !piece.isEmpty
                    else { continue }
                    accumulated += piece
                    onDelta(id, accumulated)
                }
            } catch {
                if !Task.isCancelled {
                    onError(L("翻译连接失败：", "Translation connection failed: ")
                        + error.localizedDescription)
                }
            }
        }
    }

    func cancel() {
        current?.cancel()
        current = nil
    }
}

// MARK: - key 检测 probes (v0.37.1)
//
// One REAL minimal round-trip per click of the 检测 button — no fake
// "looks like a key" checks. Credentials are only ever placed in auth
// headers; they are never logged, echoed, or included in any verdict text
// (only server-provided codes/messages surface). Raw outcomes are classified
// through the pure DoubaoProbeLogic/ArkProbeLogic (CaptionCore.swift).

/// Runs its body exactly once — a probe has racing completion paths (send
/// error, first receive, watchdog timeout) that must yield ONE verdict.
private final class ProbeFinish: @unchecked Sendable {
    private let lock = NSLock()
    private var fired = false
    private let body: @Sendable (CaptionKeyVerdict) -> Void

    init(_ body: @escaping @Sendable (CaptionKeyVerdict) -> Void) {
        self.body = body
    }

    func run(_ verdict: CaptionKeyVerdict) {
        lock.lock()
        let already = fired
        fired = true
        lock.unlock()
        if !already { body(verdict) }
    }
}

enum CaptionKeyProbe {
    /// Doubao speech: a real WS handshake against the engine's endpoint —
    /// auth headers from the credential, the engine's own session config as
    /// the full client request, read the FIRST server frame, close. No audio
    /// is ever sent, so nothing billable happens.
    static func speech(credential: VolcanoSpeechCredential,
                       done: @escaping @MainActor (CaptionKeyVerdict) -> Void) {
        var request = URLRequest(url: DoubaoStreamingASR.endpoint)
        request.timeoutInterval = 12
        for h in credential.wsHeaders(resourceID: DoubaoStreamingASR.resourceID,
                                      requestID: UUID().uuidString) {
            request.setValue(h.value, forHTTPHeaderField: h.field)
        }
        let session = URLSession(configuration: .ephemeral)
        let task = session.webSocketTask(with: request)
        let finish = ProbeFinish { verdict in
            task.cancel(with: .normalClosure, reason: nil)
            session.invalidateAndCancel()
            DispatchQueue.main.async {
                MainActor.assumeIsolated { done(verdict) }
            }
        }
        task.resume()
        if let payload = try? JSONSerialization.data(
            withJSONObject: DoubaoStreamingASR.sessionConfig()) {
            task.send(.data(DoubaoFrame.fullClientRequest(json: payload,
                                                          sequence: 1))) { error in
                if let error { finish.run(transportVerdict(error, task: task)) }
            }
        }
        task.receive { result in
            switch result {
            case .success(let message):
                if case .data(let data) = message,
                   let frame = DoubaoFrame.parseServerFrame(data) {
                    finish.run(DoubaoProbeLogic.verdict(for: frame))
                } else {
                    // text/malformed first frame — not a documented outcome;
                    // report it honestly instead of guessing a cause
                    finish.run(.serviceError(code: "-",
                        message: L("服务器返回了无法解析的首帧",
                                   "the server's first frame was unparseable")))
                }
            case .failure(let error):
                finish.run(transportVerdict(error, task: task))
            }
        }
        // a server that accepts the socket but never replies must not leave
        // the 检测 button spinning forever
        DispatchQueue.global().asyncAfter(deadline: .now() + 12) {
            finish.run(.network(detail: L("连接超时", "connection timed out")))
        }
    }

    /// WS transport failure → verdict: the HTTP status when the server
    /// refused the upgrade handshake, otherwise a plain network problem.
    /// A stored 101 means the upgrade SUCCEEDED and the connection dropped
    /// afterwards — that is a network verdict, and the transport error (not
    /// the meaningless "HTTP 101") is the detail worth showing.
    private static func transportVerdict(
        _ error: Error, task: URLSessionWebSocketTask) -> CaptionKeyVerdict {
        if let http = task.response as? HTTPURLResponse, http.statusCode != 101 {
            // Volcano's speech gateway explains upgrade refusals here
            let message = http.value(forHTTPHeaderField: "X-Api-Message") ?? ""
            return DoubaoProbeLogic.verdict(upgradeStatus: http.statusCode,
                                            message: message)
        }
        return .network(detail: error.localizedDescription)
    }

    /// Ark: ONE chat completion capped at a single output token against the
    /// configured model — the cheapest documented call that exercises the key
    /// AND the model ID together (a models listing wouldn't catch a typo'd
    /// model, which is its own verdict case).
    static func ark(key: String, model: String,
                    done: @escaping @MainActor (CaptionKeyVerdict) -> Void) {
        var request = URLRequest(url: ArkTranslator.endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 12
        request.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "model": model,
            "max_tokens": 1,
            "messages": [["role": "user", "content": "ping"]],
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        URLSession.shared.dataTask(with: request) { data, response, error in
            let verdict: CaptionKeyVerdict
            if let error {
                verdict = .network(detail: error.localizedDescription)
            } else if let http = response as? HTTPURLResponse {
                let (code, message) = arkErrorBody(data)
                verdict = ArkProbeLogic.verdict(status: http.statusCode,
                                                errorCode: code,
                                                errorMessage: message)
            } else {
                verdict = .network(detail: "no response")
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { done(verdict) }
            }
        }.resume()
    }

    /// {"error":{"code":"...","message":"..."}} → (code, message); tolerant
    /// of the OpenAI-style "type" field standing in for "code".
    private static func arkErrorBody(_ data: Data?) -> (String, String) {
        guard let data,
              let obj = (try? JSONSerialization.jsonObject(with: data))
                as? [String: Any],
              let err = obj["error"] as? [String: Any] else { return ("", "") }
        let code = (err["code"] as? String) ?? (err["type"] as? String) ?? ""
        let message = err["message"] as? String ?? ""
        return (code, message)
    }
}

// MARK: - controller (singleton; owns capture + engine + reducer + prefs)

@MainActor
final class LiveCaptionsController: ObservableObject {
    static let shared = LiveCaptionsController()

    @Published private(set) var enabled = false
    /// Paused = user intent: ALL capture and the engine connection are torn
    /// down (nothing is captured, nothing is billed, indicators go dark);
    /// only the overlay + last lines stay. Resume rebuilds the pipeline.
    @Published private(set) var paused = false
    /// The engine failed fatally (bad key, unsupported OS, …): capture is
    /// stopped (privacy invariant), the overlay shows the reason, and the
    /// menu toggle annotates itself instead of showing a plain checkmark.
    @Published private(set) var engineDead = false
    @Published private(set) var lines = CaptionLines()
    /// "" = listening normally; otherwise the honest status/error line.
    /// Views render it through CaptionDisplayState (pause outranks it).
    @Published private(set) var statusText = ""
    @Published private(set) var statusIsError = false
    /// Honest degradation note for a partially available source ("缺屏幕录制
    /// 权限，只在听麦克风" etc.) — separate from statusText so an engine
    /// status update cannot wipe it.
    @Published private(set) var sourceNote = ""
    /// Why translation is off even though the toggle is on ("" = it works).
    @Published private(set) var translationNote = ""
    @Published private(set) var translationActive = false
    @Published private(set) var activeEngineLabel = ""

    // MARK: prefs (UserDefaults; pure app-side — the Python layer never reads these)

    @Published var engineChoice: String {  // "auto" | "doubao" | "apple"
        didSet { UserDefaults.standard.set(engineChoice, forKey: "captionsEngine"); restartIfRunning() }
    }
    @Published var source: String {        // "both" | "mic" | "system"
        didSet { UserDefaults.standard.set(source, forKey: "captionsSource"); restartIfRunning() }
    }
    @Published var translateEnabled: Bool {
        didSet { UserDefaults.standard.set(translateEnabled, forKey: "captionsTranslate"); recomputeTranslation() }
    }
    @Published var translateDirection: String {  // "auto" | "zh2en" | "en2zh"
        didSet { UserDefaults.standard.set(translateDirection, forKey: "captionsTranslateDirection") }
    }
    @Published var appleLocale: String {   // "zh" | "en" (Apple engine only)
        didSet { UserDefaults.standard.set(appleLocale, forKey: "captionsAppleLocale"); restartIfRunning() }
    }
    @Published var arkModel: String {
        didSet { UserDefaults.standard.set(arkModel, forKey: "captionsArkModel") }
    }
    @Published var fontSize: Double {      // overlay 译文/主行 pt (14–40)
        didSet { UserDefaults.standard.set(fontSize, forKey: "captionsFontSize") }
    }
    @Published var opacity: Double {       // overlay background opacity
        didSet { UserDefaults.standard.set(opacity, forKey: "captionsOpacity") }
    }

    private var reducer = CaptionReducer()
    private var engine: CaptionEngine?
    private var engineIsDoubao = false
    private var mic: MicCapture?
    private var systemCapture: SystemAudioCapture?
    private let micFifo = PCMFifo()
    private let systemFifo = PCMFifo()
    private var sendTimer: Timer?
    private let translator = ArkTranslator()
    /// Ownership gate for the controller's async edges (TCC callback,
    /// SCStream start Task, capture failure callbacks): stopAllCapture()
    /// bumps it, so completions issued for a previous pipeline apply nothing.
    private var pipelineGate = AsyncGate()

    private init() {
        let d = UserDefaults.standard
        engineChoice = Prefs.string("captionsEngine", default: "auto")
        source = Prefs.string("captionsSource", default: "both")
        translateEnabled = Prefs.bool("captionsTranslate", default: false)
        translateDirection = Prefs.string("captionsTranslateDirection", default: "auto")
        appleLocale = Prefs.string("captionsAppleLocale", default: "zh")
        arkModel = Prefs.string("captionsArkModel", default: "doubao-seed-1-6-flash")
        fontSize = d.object(forKey: "captionsFontSize") == nil ? 22 : d.double(forKey: "captionsFontSize")
        opacity = d.object(forKey: "captionsOpacity") == nil ? 0.85 : d.double(forKey: "captionsOpacity")
    }

    // MARK: lifecycle

    func setEnabled(_ on: Bool) {
        guard on != enabled else { return }
        enabled = on
        UserDefaults.standard.set(on, forKey: "liveCaptionsEnabled")
        if on {
            // discovery marker + toggle event — never any caption content
            Analytics.firstReach("live_captions")
            Analytics.log("captions_toggle", fields: ["on": true,
                                                      "engine": engineChoice,
                                                      "source": source])
            CaptionOverlayController.shared.show()
            startPipeline()
        } else {
            Analytics.log("captions_toggle", fields: ["on": false])
            stopPipeline()
            CaptionOverlayController.shared.hide()
        }
    }

    /// App launch: captions were on when the app last quit → bring them back
    /// (same philosophy as recording autostart).
    func restoreOnLaunch() {
        guard Prefs.bool("liveCaptionsEnabled", default: false), !enabled else { return }
        enabled = true
        Analytics.log("captions_autostart")
        CaptionOverlayController.shared.show()
        startPipeline()
    }

    /// Pause = full stop of capture + engine (nothing captured, nothing
    /// billed, mic/screen indicators go dark) with the overlay + last lines
    /// kept. Resume rebuilds the pipeline. This is structural review-G
    /// enforcement: while paused there IS no engine to emit a status that
    /// could claim "listening", and views render the paused label from the
    /// flag (CaptionDisplayState), never from statusText.
    func togglePause() {
        if paused {
            paused = false
            startEngineAndCapture()
        } else {
            paused = true
            stopEngineAndCapture()
            setStatus("", error: false)
        }
    }

    private func restartIfRunning() {
        guard enabled else { return }
        stopPipeline()
        startPipeline()
    }

    // MARK: pipeline
    //
    // Privacy invariant (review): capture may only be live while
    // enabled == true AND the overlay is visible. Every ending path —
    // toggle off, pause, restart, fatal engine failure — funnels through
    // stopAllCapture(), and every async completion that could START capture
    // is pipelineGate/ownership-guarded, so no orphan can survive it.

    private func startPipeline() {
        reducer.reset()
        lines = reducer.lines
        paused = false
        startEngineAndCapture()
    }

    private func stopPipeline() {
        stopEngineAndCapture()
        paused = false
        engineDead = false
        setStatus("", error: false)
        sourceNote = ""
        translationNote = ""
        translationActive = false
    }

    /// Everything that listens or connects, torn down in one place.
    private func stopEngineAndCapture() {
        stopAllCapture()
        engine?.stop()
        engine = nil
        translator.cancel()
    }

    /// THE capture chokepoint: after this returns, no mic tap, no SCStream,
    /// no send timer is owned by the controller, and the gate bump makes
    /// every in-flight async start stale (its ownership guard then stops any
    /// stream it managed to create).
    private func stopAllCapture() {
        pipelineGate.bump()
        sendTimer?.invalidate()
        sendTimer = nil
        mic?.stop()
        mic = nil
        systemCapture?.stop()
        systemCapture = nil
        micFifo.drain()
        systemFifo.drain()
    }

    /// Engine + capture + send loop (shared by start and resume — resume
    /// keeps the last lines on screen instead of blanking them).
    private func startEngineAndCapture() {
        pipelineGate.bump()
        engineDead = false
        sourceNote = ""
        guard let resolved = resolveEngine() else {
            // resolveEngine set the honest fatal status; nothing captures
            engineDead = true
            return
        }
        let eng = resolved.0
        engine = eng
        engineIsDoubao = resolved.1
        activeEngineLabel = resolved.1 ? L("豆包在线", "Doubao (online)")
                                       : L("Apple 本地", "Apple on-device")
        // ownership guard: callbacks from a replaced/stopped engine instance
        // (late WS receive, analyzer task wind-down) must apply nothing
        eng.onUpdate = { [weak self, weak eng] update in
            guard let self, let eng, self.engine === eng else { return }
            self.apply(update)
        }
        eng.onStatus = { [weak self, weak eng] status in
            guard let self, let eng, self.engine === eng else { return }
            self.apply(status)
        }
        eng.start()
        startAudio()
        recomputeTranslation()
        let timer = Timer(timeInterval: 0.15, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        sendTimer = timer
    }

    /// Engine per settings; nil = nothing usable (status explains what's
    /// missing and where to fix it).
    private func resolveEngine() -> (CaptionEngine, Bool)? {
        let hasDoubaoKey = SecretsIO.hasSecret(SecretsIO.volcanoSpeechFile)
        let wantsDoubao = engineChoice == "doubao"
            || (engineChoice == "auto" && hasDoubaoKey)
        if wantsDoubao {
            guard let raw = SecretsIO.read(SecretsIO.volcanoSpeechFile),
                  let credential = VolcanoSpeechCredential.decode(raw) else {
                setStatus(L("还没有豆包语音凭证——去 设置→实时字幕 粘贴（个人实名可开通，送 20 小时）",
                            "No Doubao speech credential yet — paste one in 设置 → Live captions (personal accounts qualify; 20 free hours)"),
                          error: true)
                return nil
            }
            return (DoubaoStreamingASR(credential: credential), true)
        }
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            return (AppleLocalASR(localeID: appleLocale), false)
        }
        #endif
        setStatus(engineChoice == "apple"
            ? L("Apple 本地识别需要 macOS 26 及以上", "Apple on-device recognition needs macOS 26 or later")
            : L("没有可用的识别引擎：加一个豆包语音 API Key（设置→实时字幕），或升级到 macOS 26 用 Apple 本地识别",
                "No usable engine: add a Doubao speech API key (设置 → Live captions), or upgrade to macOS 26 for Apple on-device recognition"),
                  error: true)
        return nil
    }

    // MARK: audio sources + TCC

    private func startAudio() {
        let wantMic = source != "system"
        let wantSystem = source != "mic"
        if wantMic {
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized:
                startMic()
            case .notDetermined:
                // net-new for the app: recording's mic TCC is triggered by the
                // screenpipe child; captions capture in-process, so ask here.
                // The grant can arrive MINUTES later — re-validate ownership
                // and the CURRENT source choice before starting anything
                // (review E: a stale grant once started a mic the user had
                // switched away from, or a second one on re-enable).
                let token = pipelineGate.token
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    DispatchQueue.main.async {
                        MainActor.assumeIsolated {
                            guard self.enabled, !self.paused,
                                  self.pipelineGate.isCurrent(token),
                                  self.source != "system", self.mic == nil
                            else { return }
                            if granted { self.startMic() } else { self.micUnavailable() }
                        }
                    }
                }
            default:
                micUnavailable()
            }
        }
        if wantSystem {
            if RecordingController.hasScreenPermission() {
                startSystem()
            } else {
                // adds the app to the Screen Recording list + system prompt
                RecordingController.requestScreenPermission()
                systemUnavailable(L("缺「屏幕录制」权限，听不到系统声音",
                                    "Missing Screen Recording permission — cannot hear system audio"))
            }
        }
    }

    private func startMic() {
        let capture = MicCapture()
        // device-change rebuild failed (review D) — only the CURRENT mic may
        // report itself dead
        capture.onFailure = { [weak self, weak capture] message in
            guard let self, let capture, self.mic === capture else { return }
            self.mic = nil
            guard self.enabled, !self.paused else { return }
            self.micUnavailable(message)
        }
        do {
            try capture.start(into: micFifo)
            mic = capture
        } catch {
            micUnavailable((error as? CaptionError)?.message)
        }
    }

    private func startSystem() {
        let capture = SystemAudioCapture()
        let token = pipelineGate.token
        // stream died from the outside (display unplug, TCC pull): only the
        // CURRENT capture may nil the reference/raise the note — a stale
        // instance's death once dropped the healthy replacement (review B)
        capture.onStopped = { [weak self, weak capture] reason in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, let capture, self.systemCapture === capture else { return }
                    self.systemCapture = nil
                    guard self.enabled, !self.paused else { return }
                    self.systemUnavailable(L("系统声音捕获中断：", "System-audio capture stopped: ") + reason)
                }
            }
        }
        systemCapture = capture
        Task { @MainActor in
            do {
                try await capture.start(into: systemFifo)
                // the awaits inside start() are a toggle-off/restart window —
                // re-check ownership; a stream we no longer own gets stopped
                // on the spot (review B: orphaned SCStream, purple indicator
                // stuck on with captions off)
                guard enabled, !paused, pipelineGate.isCurrent(token),
                      systemCapture === capture else {
                    capture.stop()
                    return
                }
            } catch {
                if error is CancellationError { return }  // stopped mid-start: expected
                guard systemCapture === capture else { return }
                systemCapture = nil
                guard enabled, pipelineGate.isCurrent(token) else { return }
                systemUnavailable((error as? CaptionError)?.message
                    ?? error.localizedDescription)
            }
        }
    }

    private func micUnavailable(_ detail: String? = nil) {
        let base = detail
            ?? L("麦克风权限被拒——去 系统设置→隐私与安全性→麦克风 打开",
                 "Microphone access denied — enable it in System Settings → Privacy & Security → Microphone")
        noteSourceLoss(mic: true, message: base)
    }

    private func systemUnavailable(_ message: String) {
        noteSourceLoss(mic: false, message: message)
    }

    /// One source failed: keep going on the other (honest note), or surface a
    /// hard error when nothing is left listening.
    private func noteSourceLoss(mic micSide: Bool, message: String) {
        let otherAlive = micSide ? (systemCapture != nil) : (self.mic != nil)
        let otherWanted = micSide ? (source != "mic") : (source != "system")
        if otherWanted && otherAlive {
            sourceNote = message + L("；先只听另一路声音", " — listening on the other source for now")
        } else if otherWanted {
            sourceNote = message
        } else {
            setStatus(message, error: true)
        }
    }

    // MARK: send loop (150 ms cadence: drain fifos → mix → engine)

    private func tick() {
        guard enabled, !paused, let engine else { return }
        let frame = 2400  // 150 ms @ 16 kHz
        // Catch-up drain (review C): a main-thread stall coalesces missed
        // Timer fires into one, but capture kept pushing in real time — pop
        // the WHOLE backlog as multiple ≤150 ms frames (Doubao wants
        // 100–200 ms packets) so a stall is a one-tick blip, not permanent
        // lag ending in silent drops at the fifo's 10 s cap. The iteration
        // bound is that cap itself (67 × 150 ms) — the loop always ends.
        for _ in 0..<67 {
            let mixed = CaptionMixer.mix(micFifo.pop(frame), systemFifo.pop(frame))
            if mixed.isEmpty { return }
            engine.feed(mixed)
            if mixed.count < frame { return }  // under one frame: caught up
        }
    }

    // MARK: results → reducer → overlay

    private func apply(_ update: ASRUpdate) {
        for sentence in update.finals {
            if let id = reducer.finalize(sentence) {
                maybeTranslate(sentence, id: id)
            }
        }
        reducer.partial(update.partial)
        lines = reducer.lines
    }

    private func apply(_ status: CaptionEngineStatus) {
        switch status {
        case .connecting:
            setStatus(L("连接识别服务中…", "Connecting to the recognizer…"), error: false)
        case .downloadingModel:
            setStatus(L("正在下载本地语言模型（只需一次）…",
                        "Downloading the on-device model (one time)…"), error: false)
        case .listening:
            setStatus("", error: false)
        case .reconnecting:
            setStatus(L("连接断开，正在重连…", "Connection lost — reconnecting…"), error: false)
        case .failed(let message):
            // fatal, no retry: capture must not outlive a dead engine
            // (review F — mic/screen indicators once stayed lit for hours
            // feeding a dead engine while the overlay said the key was bad).
            // Keep the overlay + honest error visible; the menu toggle
            // annotates itself via engineDead.
            engineDead = true
            stopEngineAndCapture()
            setStatus(message, error: true)
        }
    }

    private func setStatus(_ text: String, error: Bool) {
        statusText = text
        statusIsError = error
    }

    // MARK: translation

    private func recomputeTranslation() {
        guard translateEnabled else {
            translationActive = false
            translationNote = ""
            return
        }
        guard engine == nil || engineIsDoubao else {
            translationActive = false
            translationNote = L("Apple 本地引擎只出字幕，不翻译——翻译需要豆包在线引擎",
                                "The Apple on-device engine does captions only — translation needs the Doubao online engine")
            return
        }
        guard SecretsIO.hasSecret(SecretsIO.volcanoArkFile) else {
            translationActive = false
            translationNote = L("还没有 Ark API Key——翻译要单独的 Ark 控制台 Key（和语音 Key 不是同一个）",
                                "No Ark API key yet — translation needs a separate Ark console key (not the speech key)")
            return
        }
        translationActive = true
        translationNote = ""
    }

    private func maybeTranslate(_ sentence: String, id: Int) {
        guard translationActive,
              let key = SecretsIO.read(SecretsIO.volcanoArkFile) else { return }
        let target = TranslateDirection.target(for: sentence, mode: translateDirection)
        translator.translate(sentence, id: id, target: target, model: arkModel,
                             key: key,
                             onDelta: { [weak self] id, text in
                                 guard let self else { return }
                                 self.reducer.translation(id, text)
                                 self.lines = self.reducer.lines
                             },
                             onError: { [weak self] message in
                                 self?.translationNote = message
                             })
    }
}
