// InteropHarness.swift — Swift half of the cross-language E2E crypto interop
// test. Compiled (with ios/Sources/E2E.swift) into a plain macOS command-line
// tool by run.sh — no Xcode / iOS SDK needed, because E2E.swift is pure
// Foundation + CryptoKit and CryptoKit ships in the macOS SDK too.
//
//   ./InteropHarness <python_fixtures.json> <swift_out.json>
//
// Reads Python-produced blobs, decrypts + asserts (DOWN + pairing), then
// encrypts the requested plaintexts (UP) and writes them for Python to verify.
// Exits non-zero on any mismatch.

import Foundation

func b64(_ s: String) -> Data { Data(base64Encoded: s)! }
func b64e(_ d: Data) -> String { d.base64EncodedString() }

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(Data(("HARNESS ERROR: " + msg + "\n").utf8))
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: InteropHarness <in.json> <out.json>") }

guard let inData = FileManager.default.contents(atPath: args[1]),
      let doc = (try? JSONSerialization.jsonObject(with: inData)) as? [String: Any]
else { fail("cannot read/parse \(args[1])") }

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

// ---- DOWN: decrypt Python's blobs, assert plaintext matches ----
print("Swift decrypting Python blobs (DOWN):")
let decryptCases = doc["decrypt_cases"] as? [[String: Any]] ?? []
for (i, c) in decryptCases.enumerated() {
    let kind = c["kind"] as! String
    let k = b64(c["k"] as! String)
    let epoch = UInt32(c["epoch"] as! Int)
    let dev = c["device_id"] as! String
    let blob = b64(c["blob"] as! String)
    let want = b64(c["plaintext"] as! String)
    do {
        let got: Data
        switch kind {
        case "board": got = try E2E.decryptBoard(kI: k, epoch: epoch, deviceId: dev, seq: c["seq"] as! Int, blob: blob)
        case "label": got = Data(try E2E.decryptLabel(kI: k, epoch: epoch, deviceId: dev, blob: blob).utf8)
        case "action":
            let bs = c["board_seq"] as? Int   // JSON null → nil
            got = try E2E.decryptAction(kI: k, epoch: epoch, deviceId: dev,
                                        actionId: c["action_id"] as! String, boardSeq: bs, blob: blob)
        default: fail("unknown kind \(kind)")
        }
        check(got == want, "[\(i)] \(kind)", "plaintext mismatch")
    } catch {
        check(false, "[\(i)] \(kind)", "decrypt threw: \(error)")
    }
}

// ---- pairing: parse Python's QR blob, assert all fields ----
print("Swift parsing Python pairing blob:")
if let p = doc["pairing"] as? [String: Any],
   let blob = p["blob"] as? String, let expect = p["expect"] as? [String: Any] {
    do {
        let info = try E2E.parsePairingBlob(blob)
        check(info.deviceId == expect["device_id"] as! String, "pairing.device_id")
        check(info.epoch == UInt32(expect["epoch"] as! Int), "pairing.epoch")
        check(info.key == b64(expect["key"] as! String), "pairing.key")
        check(info.label == expect["label"] as! String, "pairing.label", "got \(info.label)")
    } catch {
        check(false, "pairing parse", "threw: \(error)")
    }
} else { check(false, "pairing", "missing block") }

// ---- UP: encrypt the requested plaintexts for Python to verify ----
print("Swift encrypting for Python to verify (UP):")
var encrypted: [[String: Any]] = []
let specs = doc["encrypt_specs"] as? [[String: Any]] ?? []
for c in specs {
    let kind = c["kind"] as! String
    let k = b64(c["k"] as! String)
    let epoch = UInt32(c["epoch"] as! Int)
    let dev = c["device_id"] as! String
    let pt = b64(c["plaintext"] as! String)
    do {
        var out: [String: Any] = ["kind": kind, "k": c["k"]!, "epoch": c["epoch"]!,
                                  "device_id": dev, "plaintext": c["plaintext"]!]
        switch kind {
        case "action":
            let bs = c["board_seq"] as? Int
            out["action_id"] = c["action_id"]!
            out["board_seq"] = c["board_seq"] ?? NSNull()
            out["blob"] = b64e(try E2E.encryptAction(kI: k, epoch: epoch, deviceId: dev,
                                                      actionId: c["action_id"] as! String,
                                                      boardSeq: bs, plaintext: pt))
        case "board":
            out["seq"] = c["seq"]!
            out["blob"] = b64e(try E2E.encryptBoard(kI: k, epoch: epoch, deviceId: dev,
                                                    seq: c["seq"] as! Int, plaintext: pt))
        case "label":
            out["blob"] = b64e(try E2E.encryptLabel(kI: k, epoch: epoch, deviceId: dev,
                                                    label: String(decoding: pt, as: UTF8.self)))
        default: fail("unknown kind \(kind)")
        }
        encrypted.append(out)
        print("  ENC \(kind) epoch=\(epoch)")
    } catch {
        check(false, "encrypt \(kind)", "threw: \(error)")
    }
}

let outDoc: [String: Any] = ["encrypted": encrypted]
let outData = try! JSONSerialization.data(withJSONObject: outDoc, options: [.prettyPrinted])
try! outData.write(to: URL(fileURLWithPath: args[2]))
print("wrote \(encrypted.count) Swift-encrypted blobs → \(args[2])")

if !allOK { fail("one or more Swift-side checks failed") }
print("Swift harness: ALL PASS")
