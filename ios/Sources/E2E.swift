// E2E.swift — Swift side of the end-to-end encryption contract.
//
// MUST interoperate byte-for-byte with the Python `act/lib/e2e.py` (Phase 1a):
// a Mac's syncd encrypts its board / label with `act.lib.e2e`, this Swift code
// decrypts it, and vice-versa for actions. The frozen wire format, HKDF info
// strings and AAD byte strings are reproduced here verbatim. See the interop
// test at ios/tests/interop/ which round-trips real blobs between the two.
//
// Suite (== CryptoKit ↔ pyca one-to-one):
//   AEAD  : ChaCha20-Poly1305-IETF  (12-byte nonce, 16-byte tag)  → `ChaChaPoly`
//   subkey: HKDF-SHA256(ikm=K_i, salt=32B, info=…, L=32)          → `HKDF<SHA256>`
//
// Blob layout (big-endian), matching e2e.py exactly:
//   magic(4)="ZSYN" ‖ ver(1)=1 ‖ alg(1)=1 ‖ epoch(4, BE u32) ‖
//   salt(32) ‖ nonce(12) ‖ ciphertext(var) ‖ tag(16)
//
// AAD (UTF-8, decimal ints, `|`-joined, NO padding):
//   board  : "board|"  + device_id + "|" + seq       + "|" + epoch
//   action : "action|" + device_id + "|" + action_id + "|" + board_seq + "|" + epoch   (board_seq nil → "")
//   label  : "label|"  + device_id + "|" + epoch

import Foundation
import CryptoKit

enum E2EError: Error, CustomStringConvertible {
    case badKeyLength
    case badEpoch
    case blobTooShort
    case badMagic
    case unsupportedVersion(UInt8)
    case unsupportedAlg(UInt8)
    case epochMismatch(blob: UInt32, expected: UInt32)
    case badPairingBlob(String)

    var description: String {
        switch self {
        case .badKeyLength: return "K_i must be exactly 32 bytes"
        case .badEpoch: return "epoch must fit in an unsigned 32-bit integer"
        case .blobTooShort: return "blob too short"
        case .badMagic: return "bad magic (not a sync blob)"
        case .unsupportedVersion(let v): return "unsupported blob version \(v)"
        case .unsupportedAlg(let a): return "unsupported alg \(a)"
        case .epochMismatch(let b, let e): return "epoch mismatch: blob=\(b) expected=\(e)"
        case .badPairingBlob(let m): return "bad pairing blob: \(m)"
        }
    }
}

enum E2E {
    // ---- frozen wire constants (see e2e.py) -------------------------------
    static let magic = Data("ZSYN".utf8)               // 4 bytes
    static let version: UInt8 = 1
    static let algChaCha20Poly1305IETF: UInt8 = 1

    static let keyLen = 32
    static let saltLen = 32
    static let nonceLen = 12
    static let tagLen = 16
    // magic(4)+ver(1)+alg(1)+epoch(4)+salt(32)+nonce(12)
    static let headerLen = 4 + 1 + 1 + 4 + 32 + 12

    static let infoBoard = Data("actd/board/v1".utf8)
    static let infoAction = Data("actd/action/v1".utf8)
    static let infoLabel = Data("actd/label/v1".utf8)

    // ---- AAD builders (byte-identical to e2e.py) --------------------------
    static func aadBoard(deviceId: String, seq: Int, epoch: UInt32) -> Data {
        Data("board|\(deviceId)|\(seq)|\(epoch)".utf8)
    }
    static func aadAction(deviceId: String, actionId: String, boardSeq: Int?, epoch: UInt32) -> Data {
        let seqS = boardSeq.map { String($0) } ?? ""   // None → "" (empty)
        return Data("action|\(deviceId)|\(actionId)|\(seqS)|\(epoch)".utf8)
    }
    static func aadLabel(deviceId: String, epoch: UInt32) -> Data {
        Data("label|\(deviceId)|\(epoch)".utf8)
    }

    // ---- core seal / open -------------------------------------------------
    private static func checkKey(_ k: Data) throws {
        guard k.count == keyLen else { throw E2EError.badKeyLength }
    }

    private static func contentKey(kI: Data, salt: Data, info: Data) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(
            inputKeyMaterial: SymmetricKey(data: kI),
            salt: salt, info: info, outputByteCount: keyLen)
    }

    private static func epochBE(_ epoch: UInt32) -> Data {
        var be = epoch.bigEndian
        return withUnsafeBytes(of: &be) { Data($0) }   // 4 bytes, big-endian
    }

    /// Seal `plaintext` under `kI`/`epoch` with the given HKDF info + AAD.
    static func seal(kI: Data, epoch: UInt32, info: Data, aad: Data, plaintext: Data) throws -> Data {
        try checkKey(kI)
        var salt = Data(count: saltLen)
        var nonceBytes = Data(count: nonceLen)
        _ = salt.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, saltLen, $0.baseAddress!) }
        _ = nonceBytes.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, nonceLen, $0.baseAddress!) }
        let key = contentKey(kI: kI, salt: salt, info: info)
        let nonce = try ChaChaPoly.Nonce(data: nonceBytes)
        let box = try ChaChaPoly.seal(plaintext, using: key, nonce: nonce, authenticating: aad)
        var out = Data()
        out.append(magic)
        out.append(version)
        out.append(algChaCha20Poly1305IETF)
        out.append(epochBE(epoch))
        out.append(salt)
        out.append(nonceBytes)
        out.append(box.ciphertext)   // pyca returns ciphertext‖tag combined; we lay them out identically
        out.append(box.tag)
        return out
    }

    /// Parse + verify the header, rebuild AAD from the authenticated epoch, and
    /// decrypt. Throws on any tamper / wrong key / wrong metadata (== e2e.py).
    static func open(kI: Data, epoch: UInt32, info: Data,
                     aadFor: (UInt32) -> Data, blob: Data) throws -> Data {
        try checkKey(kI)
        guard blob.count >= headerLen + tagLen else { throw E2EError.blobTooShort }
        let b = [UInt8](blob)
        guard Data(b[0..<4]) == magic else { throw E2EError.badMagic }
        let ver = b[4]
        let alg = b[5]
        guard ver == version else { throw E2EError.unsupportedVersion(ver) }
        guard alg == algChaCha20Poly1305IETF else { throw E2EError.unsupportedAlg(alg) }
        let blobEpoch = (UInt32(b[6]) << 24) | (UInt32(b[7]) << 16) | (UInt32(b[8]) << 8) | UInt32(b[9])
        guard blobEpoch == epoch else { throw E2EError.epochMismatch(blob: blobEpoch, expected: epoch) }
        var off = 10
        let salt = Data(b[off..<off + saltLen]); off += saltLen
        let nonceBytes = Data(b[off..<off + nonceLen]); off += nonceLen
        let ctAndTag = Data(b[off...])
        let ct = ctAndTag.prefix(ctAndTag.count - tagLen)
        let tag = ctAndTag.suffix(tagLen)
        let key = contentKey(kI: kI, salt: salt, info: info)
        let nonce = try ChaChaPoly.Nonce(data: nonceBytes)
        let box = try ChaChaPoly.SealedBox(nonce: nonce, ciphertext: ct, tag: tag)
        return try ChaChaPoly.open(box, using: key, authenticating: aadFor(blobEpoch))
    }

    // ---- public API: board / action / label ------------------------------
    static func encryptBoard(kI: Data, epoch: UInt32, deviceId: String, seq: Int, plaintext: Data) throws -> Data {
        try seal(kI: kI, epoch: epoch, info: infoBoard,
                 aad: aadBoard(deviceId: deviceId, seq: seq, epoch: epoch), plaintext: plaintext)
    }
    static func decryptBoard(kI: Data, epoch: UInt32, deviceId: String, seq: Int, blob: Data) throws -> Data {
        try open(kI: kI, epoch: epoch, info: infoBoard,
                 aadFor: { aadBoard(deviceId: deviceId, seq: seq, epoch: $0) }, blob: blob)
    }

    static func encryptAction(kI: Data, epoch: UInt32, deviceId: String, actionId: String,
                              boardSeq: Int?, plaintext: Data) throws -> Data {
        try seal(kI: kI, epoch: epoch, info: infoAction,
                 aad: aadAction(deviceId: deviceId, actionId: actionId, boardSeq: boardSeq, epoch: epoch),
                 plaintext: plaintext)
    }
    static func decryptAction(kI: Data, epoch: UInt32, deviceId: String, actionId: String,
                              boardSeq: Int?, blob: Data) throws -> Data {
        try open(kI: kI, epoch: epoch, info: infoAction,
                 aadFor: { aadAction(deviceId: deviceId, actionId: actionId, boardSeq: boardSeq, epoch: $0) },
                 blob: blob)
    }

    static func encryptLabel(kI: Data, epoch: UInt32, deviceId: String, label: String) throws -> Data {
        try seal(kI: kI, epoch: epoch, info: infoLabel,
                 aad: aadLabel(deviceId: deviceId, epoch: epoch), plaintext: Data(label.utf8))
    }
    static func decryptLabel(kI: Data, epoch: UInt32, deviceId: String, blob: Data) throws -> String {
        let pt = try open(kI: kI, epoch: epoch, info: infoLabel,
                          aadFor: { aadLabel(deviceId: deviceId, epoch: $0) }, blob: blob)
        return String(decoding: pt, as: UTF8.self)
    }

    // ---- QR pairing blob (mirror e2e.build/parse_pairing_blob) ------------
    struct PairingInfo: Equatable, Identifiable {
        let deviceId: String
        let epoch: UInt32
        let key: Data      // K_i, 32 bytes
        let label: String
        var id: String { deviceId }
    }

    /// Parse the opaque QR pairing blob → PairingInfo. Verifies v==1 and
    /// decrypts the label with the carried key (which authenticates that the
    /// key/device_id/epoch travelled together untampered). Never a URL scheme.
    static func parsePairingBlob(_ blob: String) throws -> PairingInfo {
        guard let raw = Data(base64Encoded: blob.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw E2EError.badPairingBlob("not base64")
        }
        guard let obj = (try? JSONSerialization.jsonObject(with: raw)) as? [String: Any] else {
            throw E2EError.badPairingBlob("not JSON")
        }
        guard (obj["v"] as? Int) == 1 else {
            throw E2EError.badPairingBlob("unsupported version \(obj["v"] ?? "nil")")
        }
        guard let deviceId = obj["device_id"] as? String,
              let epochInt = (obj["epoch"] as? Int) ?? (obj["epoch"] as? NSNumber)?.intValue,
              epochInt >= 0, epochInt <= Int(UInt32.max),
              let kB64 = obj["k"] as? String, let kI = Data(base64Encoded: kB64), kI.count == keyLen,
              let labelB64 = obj["label_enc"] as? String, let labelBlob = Data(base64Encoded: labelB64)
        else { throw E2EError.badPairingBlob("missing/invalid fields") }
        let epoch = UInt32(epochInt)
        let label = try decryptLabel(kI: kI, epoch: epoch, deviceId: deviceId, blob: labelBlob)
        return PairingInfo(deviceId: deviceId, epoch: epoch, key: kI, label: label)
    }

    /// The 12-byte nonce embedded in a blob (magic4+ver1+alg1+epoch4+salt32 →
    /// offset 42). The Supabase schema keeps a NOT NULL `nonce` column mirroring
    /// it; callers fill it from here. Returns nil if the blob is too short.
    static func nonceSlice(of blob: Data) -> Data? {
        let start = 4 + 1 + 1 + 4 + saltLen   // 42
        guard blob.count >= start + nonceLen else { return nil }
        let b = [UInt8](blob)
        return Data(b[start..<start + nonceLen])
    }

    /// A fresh 32-byte per-pairing key (only the Mac mints these; kept here for
    /// the interop test's reverse direction).
    static func newPairingKey() -> Data {
        var k = Data(count: keyLen)
        _ = k.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, keyLen, $0.baseAddress!) }
        return k
    }

    // ---- Channel pairing blob (QR-only capability sync v2) ----------------
    // Mirrors act/lib/e2e.build_channel_qr / parse_channel_qr byte-for-byte.
    // Fixed binary layout, base64url (no padding) → the QR text:
    //   MAGIC2("ZQR1") ‖ ver(1) ‖ channel_id(16) ‖ epoch(4 BE u32) ‖
    //   write_secret(32) ‖ K(32) ‖ label_utf8(var)
    // The whole blob is the master key; the label is NOT separately encrypted.
    static let magic2 = Data("ZQR1".utf8)
    static let pairingVersion: UInt8 = 1
    static let writeSecretLen = 32
    // magic(4)+ver(1)+channel_id(16)+epoch(4)+write_secret(32)+K(32)
    static let pairingMinLen = 4 + 1 + 16 + 4 + 32 + 32

    struct ChannelPairing: Equatable, Identifiable {
        let channelId: String   // canonical lowercase UUID string (== Python str(uuid))
        let epoch: UInt32
        let writeSecret: Data   // 32 bytes
        let key: Data           // K, 32 bytes
        let label: String
        var id: String { channelId }
    }

    private static func base64urlNoPad(_ d: Data) -> String {
        var s = d.base64EncodedString()
        s = s.replacingOccurrences(of: "+", with: "-")
             .replacingOccurrences(of: "/", with: "_")
        while s.hasSuffix("=") { s.removeLast() }
        return s
    }

    private static func base64urlDecode(_ s: String) -> Data? {
        var t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        while t.count % 4 != 0 { t.append("=") }
        return Data(base64Encoded: t)
    }

    private static func uuidBytes(_ id: UUID) -> Data {
        let u = id.uuid
        return Data([u.0, u.1, u.2, u.3, u.4, u.5, u.6, u.7,
                     u.8, u.9, u.10, u.11, u.12, u.13, u.14, u.15])
    }

    private static func uuidString(from bytes: Data) -> String {
        let b = [UInt8](bytes)
        let id = UUID(uuid: (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                             b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]))
        return id.uuidString.lowercased()
    }

    /// Build the v2 channel pairing blob (base64url, no pad) — byte-identical to
    /// the Python encoder. `channelId` must be a UUID; write_secret and key are
    /// 32 bytes each; label is arbitrary UTF-8.
    static func buildChannelQR(channelId: String, epoch: UInt32,
                               writeSecret: Data, key: Data, label: String) throws -> String {
        guard writeSecret.count == writeSecretLen else { throw E2EError.badPairingBlob("write_secret must be 32 bytes") }
        try checkKey(key)
        guard let cid = UUID(uuidString: channelId) else { throw E2EError.badPairingBlob("bad channel_id") }
        var raw = Data()
        raw.append(magic2)
        raw.append(pairingVersion)
        raw.append(uuidBytes(cid))
        raw.append(epochBE(epoch))
        raw.append(writeSecret)
        raw.append(key)
        raw.append(Data(label.utf8))
        return base64urlNoPad(raw)
    }

    /// Parse the v2 channel pairing blob → ChannelPairing. Byte-identical to the
    /// Python parser (canonical lowercase channelId).
    static func parseChannelQR(_ blob: String) throws -> ChannelPairing {
        guard let raw = base64urlDecode(blob) else { throw E2EError.badPairingBlob("not base64url") }
        guard raw.count >= pairingMinLen else { throw E2EError.badPairingBlob("too short") }
        let b = [UInt8](raw)
        guard Data(b[0..<4]) == magic2 else { throw E2EError.badPairingBlob("bad magic") }
        guard b[4] == pairingVersion else { throw E2EError.badPairingBlob("unsupported version \(b[4])") }
        var off = 5
        let channelId = uuidString(from: Data(b[off..<off + 16])); off += 16
        let epoch = (UInt32(b[off]) << 24) | (UInt32(b[off + 1]) << 16)
                  | (UInt32(b[off + 2]) << 8) | UInt32(b[off + 3]); off += 4
        let writeSecret = Data(b[off..<off + writeSecretLen]); off += writeSecretLen
        let key = Data(b[off..<off + keyLen]); off += keyLen
        let label = String(decoding: Data(b[off...]), as: UTF8.self)
        return ChannelPairing(channelId: channelId, epoch: epoch,
                              writeSecret: writeSecret, key: key, label: label)
    }
}
