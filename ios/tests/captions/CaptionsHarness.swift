// CaptionsHarness.swift — behavior tests for mac/Sources/CaptionCore.swift:
// the Doubao streaming-ASR binary frame codec (byte-exact vectors from the
// 火山 sauc v3 protocol spec), the gzip payload decoder, the server-payload
// interpreter (definite/partial dedup), and the 2-line caption roll-up
// reducer. Compiled by run.sh into a plain macOS CLI tool — no Xcode, no
// XCTest. Exits non-zero on any failure. (Same harness style as
// ios/tests/contract.)

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

func hex(_ data: Data) -> String {
    data.map { String(format: "%02x", $0) }.joined(separator: " ")
}

// ---- 1. client frame encoding: byte-exact against the protocol spec ----
print("[1] full client request framing (JSON serialization, seq 1):")
let configPayload = Data(#"{"a":1}"#.utf8)
let full = DoubaoFrame.fullClientRequest(json: configPayload, sequence: 1)
let expectedFull = Data([0x11, 0x11, 0x10, 0x00,          // v1|4B, full-client|pos-seq, JSON|none
                         0x00, 0x00, 0x00, 0x01,          // sequence 1 (BE)
                         0x00, 0x00, 0x00, 0x07]          // payload size 7 (BE)
                        + [UInt8](configPayload))
check(full == expectedFull, "byte-exact full client request",
      "got \(hex(full)) want \(hex(expectedFull))")

print("[2] audio frame framing (raw serialization):")
let audio = DoubaoFrame.audioFrame(Data([0x01, 0x02]), sequence: 2, last: false)
let expectedAudio = Data([0x11, 0x21, 0x00, 0x00,         // audio-only|pos-seq, raw|none
                          0x00, 0x00, 0x00, 0x02,
                          0x00, 0x00, 0x00, 0x02,
                          0x01, 0x02])
check(audio == expectedAudio, "byte-exact audio frame",
      "got \(hex(audio)) want \(hex(expectedAudio))")

print("[3] LAST audio frame: flags 0b0011 + NEGATIVE sequence:")
let lastFrame = DoubaoFrame.audioFrame(Data(), sequence: 5, last: true)
let expectedLast = Data([0x11, 0x23, 0x00, 0x00,
                         0xFF, 0xFF, 0xFF, 0xFB,          // -5 two's-complement BE
                         0x00, 0x00, 0x00, 0x00])
check(lastFrame == expectedLast, "byte-exact end-of-stream frame",
      "got \(hex(lastFrame)) want \(hex(expectedLast))")

// ---- 2. server frame parsing ----
print("[4] full server response (uncompressed, with sequence):")
let serverPayload = Data(#"{"result":{"text":"hi"}}"#.utf8)
var serverBytes = Data([0x11, 0x91, 0x10, 0x00,           // full-server|pos-seq
                        0x00, 0x00, 0x00, 0x02,
                        0x00, 0x00, 0x00, UInt8(serverPayload.count)])
serverBytes += serverPayload
if let frame = DoubaoFrame.parseServerFrame(serverBytes) {
    check(frame.messageType == DoubaoFrame.typeFullServer, "message type")
    check(frame.sequence == 2, "sequence", "got \(String(describing: frame.sequence))")
    check(frame.payload == serverPayload, "payload passthrough")
    check(!frame.isError && !frame.isLast, "not error / not last")
} else { check(false, "parse", "valid server frame must parse") }

print("[5] server LAST frame: flags 0b0011 + negative sequence:")
var lastServer = Data([0x11, 0x93, 0x10, 0x00,
                       0xFF, 0xFF, 0xFF, 0xFD,            // -3
                       0x00, 0x00, 0x00, 0x02])
lastServer += Data([0x7B, 0x7D])                          // {}
if let frame = DoubaoFrame.parseServerFrame(lastServer) {
    check(frame.isLast, "isLast")
    check(frame.sequence == -3, "negative sequence", "got \(String(describing: frame.sequence))")
} else { check(false, "parse", "valid last frame must parse") }

print("[6] error frame: 4-byte error code before the payload, no sequence:")
let errBody = Data("quota".utf8)
var errFrame = Data([0x11, 0xF0, 0x10, 0x00,              // error type, flags 0
                     0x02, 0xAE, 0xA5, 0x41,              // 45000001 BE
                     0x00, 0x00, 0x00, UInt8(errBody.count)])
errFrame += errBody
if let frame = DoubaoFrame.parseServerFrame(errFrame) {
    check(frame.isError, "isError")
    check(frame.errorCode == 45_000_001, "error code", "got \(String(describing: frame.errorCode))")
    check(frame.sequence == nil, "no sequence on flags 0")
    check(frame.payload == errBody, "error body")
} else { check(false, "parse", "valid error frame must parse") }

print("[7] extended header (header size 2 → 8 bytes) still parses:")
var extHeader = Data([0x12, 0x91, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00,  // 8B header
                      0x00, 0x00, 0x00, 0x07,
                      0x00, 0x00, 0x00, 0x02])
extHeader += Data([0x7B, 0x7D])
if let frame = DoubaoFrame.parseServerFrame(extHeader) {
    check(frame.sequence == 7, "sequence after extended header")
    check(frame.payload == Data([0x7B, 0x7D]), "payload after extended header")
} else { check(false, "parse", "extended header must parse") }

print("[8] truncated / malformed frames → nil (never crash, never garbage):")
check(DoubaoFrame.parseServerFrame(Data([0x11, 0x91])) == nil, "short header")
check(DoubaoFrame.parseServerFrame(Data([0x11, 0x91, 0x10, 0x00,
                                         0x00, 0x00, 0x00, 0x01])) == nil,
      "missing payload size")
check(DoubaoFrame.parseServerFrame(Data([0x11, 0x91, 0x10, 0x00,
                                         0x00, 0x00, 0x00, 0x01,
                                         0x00, 0x00, 0x00, 0x63])) == nil,
      "payload size larger than the frame")

// ---- 3. gzip payload decode ----
print("[9] gunzip (python3 gzip.compress fixture of '你好 hello', mtime=0):")
let gzFixture = Data([0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0xff,
                      0x7b, 0xb2, 0x77, 0xc1, 0xd3, 0xa5, 0x7b, 0x15, 0x32, 0x52,
                      0x73, 0x72, 0xf2, 0x01, 0x2c, 0x90, 0x31, 0x29, 0x0c, 0x00,
                      0x00, 0x00])
check(DoubaoFrame.gunzip(gzFixture) == Data("你好 hello".utf8), "fixture roundtrip")
check(DoubaoFrame.gunzip(Data([0x00, 0x01, 0x02])) == nil, "junk → nil")

print("[10] gzip-compressed server frame is transparently decompressed:")
var gzServer = Data([0x11, 0x91, 0x11, 0x00,              // compression nibble = gzip
                     0x00, 0x00, 0x00, 0x02,
                     0x00, 0x00, 0x00, UInt8(gzFixture.count)])
gzServer += gzFixture
if let frame = DoubaoFrame.parseServerFrame(gzServer) {
    check(frame.payload == Data("你好 hello".utf8), "gunzipped payload",
          "got \(hex(frame.payload))")
} else { check(false, "parse", "gzip server frame must parse") }

// ---- 4. server payload interpretation (definite dedup by start_time) ----
print("[11] DoubaoSession: partial → definite → restated definite + new partial:")
var session = DoubaoSession()
func interpret(_ json: String) -> ASRUpdate? {
    session.interpret(payload: Data(json.utf8))
}
var u = interpret(#"{"result":{"utterances":[{"text":"你好","definite":false,"start_time":0}]}}"#)
check(u == ASRUpdate(finals: [], partial: "你好"), "in-flight utterance → partial", "got \(String(describing: u))")
u = interpret(#"{"result":{"utterances":[{"text":"你好世界。","definite":true,"start_time":0,"end_time":1200}]}}"#)
check(u == ASRUpdate(finals: ["你好世界。"], partial: ""), "definite → final, live cleared", "got \(String(describing: u))")
u = interpret(#"{"result":{"utterances":[{"text":"你好世界。","definite":true,"start_time":0},{"text":"第二","definite":false,"start_time":1300}]}}"#)
check(u == ASRUpdate(finals: [], partial: "第二"), "restated definite NOT re-emitted", "got \(String(describing: u))")
u = interpret(#"{"result":{"utterances":[{"text":"你好世界。","definite":true,"start_time":0},{"text":"第二句。","definite":true,"start_time":1300}]}}"#)
check(u == ASRUpdate(finals: ["第二句。"], partial: ""), "only the NEW definite emitted", "got \(String(describing: u))")

print("[12] DoubaoSession: no utterances → result.text as the live line:")
var bare = DoubaoSession()
let bareU = bare.interpret(payload: Data(#"{"result":{"text":"abc"}}"#.utf8))
check(bareU == ASRUpdate(finals: [], partial: "abc"), "text fallback", "got \(String(describing: bareU))")
check(bare.interpret(payload: Data("not json".utf8)) == nil, "junk payload → nil")

// ---- 5. 2-line roll-up reducer ----
print("[13] reducer: partial / finalize / translation attach + late-drop:")
var reducer = CaptionReducer()
reducer.partial("你好")
check(reducer.lines == CaptionLines(finalID: 0, finalText: "", finalTranslation: "", liveText: "你好"),
      "partial fills the live line", "got \(reducer.lines)")
let id1 = reducer.finalize("你好世界。")
check(id1 == 1, "first sentence id 1", "got \(String(describing: id1))")
check(reducer.lines == CaptionLines(finalID: 1, finalText: "你好世界。", finalTranslation: "", liveText: ""),
      "finalize pushes up + clears live", "got \(reducer.lines)")
reducer.translation(1, "Hello")
reducer.translation(1, "Hello world.")
check(reducer.lines.finalTranslation == "Hello world.", "streaming translation attaches")
reducer.partial("第二")
let id2 = reducer.finalize("第二句。")
check(id2 == 2, "monotonic ids")
reducer.translation(1, "STALE")
check(reducer.lines.finalTranslation == "", "late stream for a replaced sentence is dropped",
      "got \(reducer.lines.finalTranslation)")
reducer.translation(2, "Second sentence.")
check(reducer.lines == CaptionLines(finalID: 2, finalText: "第二句。",
                                    finalTranslation: "Second sentence.", liveText: ""),
      "current sentence still accepts", "got \(reducer.lines)")

print("[14] reducer: whitespace-only finals + reset keep ids monotonic:")
check(reducer.finalize("   ") == nil, "whitespace final → nil id")
check(reducer.lines.finalText == "第二句。", "top line untouched by empty final")
reducer.reset()
check(reducer.lines == CaptionLines(), "reset clears the display")
check(reducer.finalize("第三句。") == 3, "ids stay monotonic across reset")

// ---- 6. translation direction ----
print("[15] TranslateDirection: fixed modes + auto script sniff:")
check(TranslateDirection.target(for: "hello", mode: "zh2en") == "en", "zh2en fixed")
check(TranslateDirection.target(for: "你好", mode: "en2zh") == "zh", "en2zh fixed")
check(TranslateDirection.target(for: "今天的会议先讨论 roadmap", mode: "auto") == "en",
      "auto: mostly-Chinese sentence → English")
check(TranslateDirection.target(for: "Let's review the quarterly numbers", mode: "auto") == "zh",
      "auto: English sentence → Chinese")
check(TranslateDirection.target(for: "12345 …", mode: "auto") == "zh",
      "auto: no letters at all → zh (safe default)")

// ---- 7. async ownership gate (PR #51 review: the shared fix for every
// stale-completion finding — reconnect storms, orphaned SCStreams, late TCC
// grants all reduce to "a completion outlived its pipeline") ----
print("[16] AsyncGate: bump invalidates every outstanding token:")
var gate = AsyncGate()
let t0 = gate.token
check(gate.isCurrent(t0), "initial token is current")
let t1 = gate.bump()
check(!gate.isCurrent(t0), "old token stale after bump")
check(gate.isCurrent(t1), "bump returns the new current token")
let t2 = gate.bump()
check(!gate.isCurrent(t1) && gate.isCurrent(t2), "second bump: only newest survives")
// the reconnect-storm shape (review A): teardown bumps FIRST; the dead
// connection's still-registered receive callback AND any previously queued
// reconnect both hold older tokens — neither may act
let receiveToken = gate.token
let reconnectToken = gate.bump()   // scheduleReconnect: bump, then queue
check(!gate.isCurrent(receiveToken), "dead connection's receive re-arm is stale")
check(gate.isCurrent(reconnectToken), "exactly one reconnect owns the gate")

// ---- 8. pcm mixing (extracted from the send loop for the catch-up drain) ----
print("[17] CaptionMixer: saturation + passthrough + unequal lengths:")
check(CaptionMixer.mix([], []) == [], "both empty → empty")
check(CaptionMixer.mix([1, 2], []) == [1, 2], "empty b → a passthrough")
check(CaptionMixer.mix([], [3]) == [3], "empty a → b passthrough")
check(CaptionMixer.mix([1000, -1000], [500, 500]) == [1500, -500], "plain sum")
check(CaptionMixer.mix([32000], [32000]) == [32767], "positive clamp")
check(CaptionMixer.mix([-32000], [-32000]) == [-32768], "negative clamp")
check(CaptionMixer.mix([100, 200, 300], [10]) == [110, 200, 300],
      "shorter stream zero-padded, longer one intact")

// ---- 9. overlay status precedence (review G) ----
print("[18] CaptionDisplayState: paused outranks engine status/errors:")
var display = CaptionDisplayState()
check(display.statusLine(pausedLabel: "PAUSED") == nil, "idle → no status line")
display.statusText = "connecting"
if let line = display.statusLine(pausedLabel: "PAUSED") {
    check(line == ("connecting", false), "engine status shows when not paused")
} else { check(false, "status", "engine status must render") }
display.paused = true
if let line = display.statusLine(pausedLabel: "PAUSED") {
    check(line == ("PAUSED", false), "paused label wins over engine status",
          "got \(line)")
} else { check(false, "paused", "paused label must render") }
display.statusText = ""
if let line = display.statusLine(pausedLabel: "PAUSED") {
    check(line == ("PAUSED", false), "paused label survives a .listening wipe")
} else { check(false, "paused", "paused label must survive statusText clear") }
display.statusText = "key invalid"
display.statusIsError = true
if let line = display.statusLine(pausedLabel: "PAUSED") {
    check(line == ("PAUSED", false), "paused label wins over an error too")
} else { check(false, "paused", "paused label must outrank errors") }
display.paused = false
if let line = display.statusLine(pausedLabel: "PAUSED") {
    check(line == ("key invalid", true), "unpause reveals the error unchanged")
} else { check(false, "status", "error must render after unpause") }

// ---- 10. v0.37.1 BYO speech credential: paste auto-detection ----
print("[19] VolcanoSpeechCredential.parse — new key vs legacy pair:")
let tok32 = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"  // 32 chars, old-console shape
check(VolcanoSpeechCredential.parse("sk-abcdef1234567890")
      == .apiKey("sk-abcdef1234567890"), "bare new-console key")
check(VolcanoSpeechCredential.parse("  key-padded  ") == .apiKey("key-padded"),
      "surrounding whitespace trimmed")
check(VolcanoSpeechCredential.parse("") == nil, "empty paste → nil")
check(VolcanoSpeechCredential.parse("   \n ") == nil, "whitespace-only → nil")
let wantLegacy = VolcanoSpeechCredential.legacy(appID: "1234567890",
                                                accessToken: tok32)
check(VolcanoSpeechCredential.parse("1234567890:\(tok32)") == wantLegacy,
      "AppID:Token on one line")
check(VolcanoSpeechCredential.parse("1234567890：\(tok32)") == wantLegacy,
      "full-width colon separator")
check(VolcanoSpeechCredential.parse("1234567890 , \(tok32)") == wantLegacy,
      "comma + spaces separator")
check(VolcanoSpeechCredential.parse("1234567890\n\(tok32)") == wantLegacy,
      "two non-empty lines")
check(VolcanoSpeechCredential.parse("appid:1234567890\ntoken:\(tok32)")
      == wantLegacy, "own stored format pastes back (labels stripped)")
check(VolcanoSpeechCredential.parse("123456:\(tok32)")
      == .legacy(appID: "123456", accessToken: tok32), "6-digit id is enough")
check(VolcanoSpeechCredential.parse("12345:\(tok32)")
      == .apiKey("12345:\(tok32)"), "5-digit prefix → NOT legacy (too short)")
check(VolcanoSpeechCredential.parse("1234567890:shorttoken")
      == .apiKey("1234567890:shorttoken"), "token under 20 chars → NOT legacy")

print("[20] stored-file format: encode/decode + pre-0.37.1 compat:")
check(wantLegacy.fileRepresentation == "appid:1234567890\ntoken:\(tok32)",
      "legacy stores as two labeled lines")
check(VolcanoSpeechCredential.apiKey("k1").fileRepresentation == "k1",
      "api key stays a bare line")
check(VolcanoSpeechCredential.decode(wantLegacy.fileRepresentation + "\n")
      == wantLegacy, "legacy file round-trips (trailing newline ok)")
check(VolcanoSpeechCredential.decode("plain-old-saved-key\n")
      == .apiKey("plain-old-saved-key"), "pre-0.37.1 bare key file unchanged")
check(VolcanoSpeechCredential.decode("") == nil, "empty file → nil")
check(VolcanoSpeechCredential.decode("appid:1\ntoken:")
      == .apiKey("appid:1\ntoken:"),
      "half-empty labeled pair falls back to bare content, never a broken pair")
check(wantLegacy.isLegacy && !VolcanoSpeechCredential.apiKey("k").isLegacy,
      "isLegacy flag")

print("[21] wsHeaders per console generation (recon §1):")
let newHeaders = VolcanoSpeechCredential.apiKey("KEY")
    .wsHeaders(resourceID: "rid", requestID: "req")
check(newHeaders.map(\.field)
      == ["X-Api-Key", "X-Api-Resource-Id", "X-Api-Request-Id"],
      "new console: X-Api-Key + resource + request ids",
      "got \(newHeaders.map(\.field))")
check(newHeaders.map(\.value) == ["KEY", "rid", "req"], "new header values")
let legacyHeaders = wantLegacy.wsHeaders(resourceID: "rid", requestID: "req")
check(legacyHeaders.map(\.field)
      == ["X-Api-App-Key", "X-Api-Access-Key",
          "X-Api-Resource-Id", "X-Api-Request-Id"],
      "legacy: X-Api-App-Key + X-Api-Access-Key + resource + request ids",
      "got \(legacyHeaders.map(\.field))")
check(legacyHeaders.map(\.value) == ["1234567890", tok32, "rid", "req"],
      "legacy header values")

// ---- 11. 检测 verdict mapping ----
print("[22] DoubaoProbeLogic: first-frame + upgrade classification:")
func errFrame(_ code: UInt32, _ text: String) -> DoubaoFrame.ServerFrame {
    DoubaoFrame.ServerFrame(messageType: DoubaoFrame.typeError, flags: 0,
                            sequence: nil, errorCode: code,
                            payload: Data(text.utf8))
}
let ackFrame = DoubaoFrame.ServerFrame(messageType: DoubaoFrame.typeFullServer,
                                       flags: 0b0001, sequence: 1,
                                       errorCode: nil, payload: Data())
check(DoubaoProbeLogic.verdict(for: ackFrame) == .ok, "server ack → ok")
check(DoubaoProbeLogic.verdict(for: errFrame(401, "bad key"))
      == .badKey(detail: "401 bad key"), "in-band 401 → bad key")
check(DoubaoProbeLogic.verdict(for: errFrame(403, ""))
      == .badKey(detail: "403"), "in-band 403 without text → bad key")
check(DoubaoProbeLogic.verdict(for: errFrame(45000001, "invalid request"))
      == .serviceError(code: "45000001", message: "invalid request"),
      "unknown code → raw code + message, no guessing")
check(DoubaoProbeLogic.verdict(for: errFrame(45000030, "resource not granted"))
      == .resourceNotEnabled(code: "45000030", message: "resource not granted"),
      "server message naming a resource problem → its own verdict")
check(DoubaoProbeLogic.verdict(upgradeStatus: 401, message: "")
      == .badKey(detail: "HTTP 401"), "upgrade 401 → bad key")
check(DoubaoProbeLogic.verdict(upgradeStatus: 403, message: "服务未开通")
      == .resourceNotEnabled(code: "HTTP 403", message: "服务未开通"),
      "upgrade 403 naming 未开通 → resource verdict")
check(DoubaoProbeLogic.verdict(upgradeStatus: 503, message: "busy")
      == .serviceError(code: "HTTP 503", message: "busy"),
      "upgrade 5xx → raw service error")

print("[23] ArkProbeLogic: chat-completion probe classification:")
check(ArkProbeLogic.verdict(status: 200, errorCode: "", errorMessage: "")
      == .ok, "200 → ok")
check(ArkProbeLogic.verdict(status: 401, errorCode: "AuthenticationError",
                            errorMessage: "invalid key")
      == .badKey(detail: "AuthenticationError: invalid key"), "401 → bad key")
check(ArkProbeLogic.verdict(status: 403, errorCode: "", errorMessage: "")
      == .badKey(detail: "HTTP 403"), "bare 403 → bad key with HTTP status")
check(ArkProbeLogic.verdict(status: 404, errorCode: "ModelNotFound",
                            errorMessage: "no such model")
      == .modelNotFound(detail: "ModelNotFound: no such model"),
      "404 → model-ID verdict")
check(ArkProbeLogic.verdict(status: 400, errorCode: "ModelNotOpen",
                            errorMessage: "activate first")
      == .modelNotFound(detail: "ModelNotOpen: activate first"),
      "ModelNotOpen code → model-ID verdict even on 400")
check(ArkProbeLogic.verdict(status: 429, errorCode: "RateLimitExceeded",
                            errorMessage: "slow down")
      == .serviceError(code: "HTTP 429",
                       message: "RateLimitExceeded: slow down"),
      "other statuses → raw service error")

if allOK {
    print("ALL CAPTIONS TESTS PASSED")
    exit(0)
} else {
    print("CAPTIONS TESTS FAILED")
    exit(1)
}
