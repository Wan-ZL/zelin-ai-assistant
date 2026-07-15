// LiveCaptions.swift — 实时字幕 engine orchestration: own in-process audio
// capture (AVAudioEngine mic tap + ScreenCaptureKit system audio — deliberately
// independent of the screenpipe engine, which exposes no live stream) → a
// pluggable ASR engine (Doubao streaming ASR over WebSocket, or Apple's
// on-device SpeechTranscriber on macOS 26+) → CaptionReducer → the overlay.
//
// BYO-key model: the app ships NO key. Doubao needs the user's own 火山
// speech API key (SecretsIO volcano-speech-key.txt); optional translation
// needs a SECOND user key for Ark (volcano-ark-key.txt). Caption text never
// leaves this machine except to the user's own ASR/translation endpoints.
// Pure protocol/reducer logic lives in CaptionCore.swift (swiftc-testable).

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

    func start(into fifo: PCMFifo) throws {
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

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
    }
}

// MARK: - system-audio capture (ScreenCaptureKit; needs the Screen Recording
// grant the app already manages for the screenpipe engine)

final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private var fifo: PCMFifo?
    private let queue = DispatchQueue(label: "zelin.captions.sysaudio", qos: .userInitiated)
    /// Fired when the stream dies out from under us (display unplug, TCC pull).
    var onStopped: (@Sendable (String) -> Void)?

    func start(into fifo: PCMFifo) async throws {
        self.fifo = fifo
        let content = try await SCShareableContent
            .excludingDesktopWindows(false, onScreenWindowsOnly: false)
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
        try await s.startCapture()
        stream = s
    }

    func stop() {
        let s = stream
        stream = nil
        fifo = nil
        s?.stopCapture { _ in }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, let fifo else { return }
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
    private static let endpoint =
        URL(string: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")!
    private static let resourceID = "volc.seedasr.sauc.duration"

    private let apiKey: String
    private let urlSession = URLSession(configuration: .default)
    private var task: URLSessionWebSocketTask?
    private var sequence: Int32 = 1
    private var interpreter = DoubaoSession()
    private var stopped = false
    private var gotFirstFrame = false
    private var backoff: TimeInterval = 1
    /// Generation guard: a queued reconnect from a previous stop must not
    /// resurrect a stopped engine.
    private var generation = 0

    init(apiKey: String) {
        self.apiKey = apiKey
    }

    func start() {
        stopped = false
        backoff = 1
        connect()
    }

    func stop() {
        stopped = true
        generation += 1
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
        request.setValue(apiKey, forHTTPHeaderField: "X-Api-Key")
        request.setValue(Self.resourceID, forHTTPHeaderField: "X-Api-Resource-Id")
        request.setValue(UUID().uuidString, forHTTPHeaderField: "X-Api-Request-Id")
        let t = urlSession.webSocketTask(with: request)
        task = t
        t.resume()
        let config: [String: Any] = [
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
        if let payload = try? JSONSerialization.data(withJSONObject: config) {
            t.send(.data(DoubaoFrame.fullClientRequest(json: payload, sequence: 1))) { _ in }
        }
        sequence = 2
        receiveLoop(t, generation: generation)
    }

    private func receiveLoop(_ t: URLSessionWebSocketTask, generation gen: Int) {
        t.receive { [weak self] result in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, !self.stopped, self.generation == gen else { return }
                    switch result {
                    case .failure(let error):
                        self.handleFailure(error)
                    case .success(let message):
                        if case .data(let data) = message { self.handleFrame(data) }
                        self.receiveLoop(t, generation: gen)
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
        generation += 1
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        onStatus?(.failed(L("豆包语音 API Key 无效或未开通流式语音识别——去 设置→实时字幕 检查",
                            "Doubao speech API key is invalid or the streaming-ASR service is not activated — check 设置 → Live captions")))
    }

    private func scheduleReconnect(after reason: String) {
        guard !stopped else { return }
        NSLog("[captions] doubao reconnect in %.0fs: %@", backoff, reason)
        onStatus?(.reconnecting)
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        let gen = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + backoff) {
            MainActor.assumeIsolated {
                guard !self.stopped, self.generation == gen else { return }
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
    private static let endpoint =
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

// MARK: - controller (singleton; owns capture + engine + reducer + prefs)

@MainActor
final class LiveCaptionsController: ObservableObject {
    static let shared = LiveCaptionsController()

    @Published private(set) var enabled = false
    @Published private(set) var paused = false
    @Published private(set) var lines = CaptionLines()
    /// "" = listening normally; otherwise the honest status/error line the
    /// overlay + settings both render.
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

    func togglePause() {
        paused.toggle()
        // pausing stops FEEDING (Doubao bills by audio duration sent) but
        // keeps the connection + overlay; fifos keep draining in tick()
        if paused {
            setStatus(L("已暂停", "Paused"), error: false)
        } else if statusText == L("已暂停", "Paused") {
            setStatus("", error: false)
        }
    }

    private func restartIfRunning() {
        guard enabled else { return }
        stopPipeline()
        startPipeline()
    }

    // MARK: pipeline

    private func startPipeline() {
        reducer.reset()
        lines = reducer.lines
        paused = false
        sourceNote = ""
        guard let resolved = resolveEngine() else { return }
        engine = resolved.0
        engineIsDoubao = resolved.1
        activeEngineLabel = resolved.1 ? L("豆包在线", "Doubao (online)")
                                       : L("Apple 本地", "Apple on-device")
        resolved.0.onUpdate = { [weak self] update in self?.apply(update) }
        resolved.0.onStatus = { [weak self] status in self?.apply(status) }
        resolved.0.start()
        startAudio()
        recomputeTranslation()
        let timer = Timer(timeInterval: 0.15, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        sendTimer = timer
    }

    private func stopPipeline() {
        sendTimer?.invalidate()
        sendTimer = nil
        engine?.stop()
        engine = nil
        mic?.stop()
        mic = nil
        systemCapture?.stop()
        systemCapture = nil
        micFifo.drain()
        systemFifo.drain()
        translator.cancel()
        paused = false
        setStatus("", error: false)
        sourceNote = ""
        translationNote = ""
        translationActive = false
    }

    /// Engine per settings; nil = nothing usable (status explains what's
    /// missing and where to fix it).
    private func resolveEngine() -> (CaptionEngine, Bool)? {
        let hasDoubaoKey = SecretsIO.hasSecret(SecretsIO.volcanoSpeechFile)
        let wantsDoubao = engineChoice == "doubao"
            || (engineChoice == "auto" && hasDoubaoKey)
        if wantsDoubao {
            guard let key = SecretsIO.read(SecretsIO.volcanoSpeechFile) else {
                setStatus(L("还没有豆包语音 API Key——去 设置→实时字幕 粘贴（个人实名可开通，送 20 小时）",
                            "No Doubao speech API key yet — paste one in 设置 → Live captions (personal accounts qualify; 20 free hours)"),
                          error: true)
                return nil
            }
            return (DoubaoStreamingASR(apiKey: key), true)
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
                // screenpipe child; captions capture in-process, so ask here
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    DispatchQueue.main.async {
                        MainActor.assumeIsolated {
                            guard self.enabled else { return }
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
        do {
            try capture.start(into: micFifo)
            mic = capture
        } catch {
            micUnavailable((error as? CaptionError)?.message)
        }
    }

    private func startSystem() {
        let capture = SystemAudioCapture()
        capture.onStopped = { [weak self] reason in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, self.enabled else { return }
                    self.systemCapture = nil
                    self.systemUnavailable(L("系统声音捕获中断：", "System-audio capture stopped: ") + reason)
                }
            }
        }
        systemCapture = capture
        Task { @MainActor in
            do {
                try await capture.start(into: systemFifo)
            } catch {
                guard enabled, systemCapture === capture else { return }
                systemCapture = nil
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
        guard enabled, let engine else { return }
        let n = 2400  // 150 ms @ 16 kHz
        let a = micFifo.pop(n)
        let b = systemFifo.pop(n)
        // paused: fifos drained + dropped so unpausing never replays a backlog
        guard !paused else { return }
        var mixed: [Int16]
        if a.isEmpty {
            mixed = b
        } else if b.isEmpty {
            mixed = a
        } else {
            mixed = [Int16](repeating: 0, count: max(a.count, b.count))
            for i in 0..<mixed.count {
                let sum = Int(i < a.count ? a[i] : 0) + Int(i < b.count ? b[i] : 0)
                mixed[i] = Int16(clamping: sum)
            }
        }
        guard !mixed.isEmpty else { return }
        engine.feed(mixed)
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
