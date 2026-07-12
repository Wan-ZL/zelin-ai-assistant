"""e2e — end-to-end encryption for the iOS cloud-sync backend (Phase 1a).

Per-pairing symmetric-key AEAD (plan of record §4). Each Mac ↔ phone pairing
holds ONE 32-byte symmetric key ``K_i`` with an integer ``epoch``. The Mac
generates ``K_i`` at pairing time and hands it to the phone **only via the QR
pairing blob** (true out-of-band) — the key never touches Supabase. The Mac
encrypts its own board with ``K_i`` (DOWN) and decrypts actions addressed to it
(UP); Supabase relays ciphertext only. "The maintainer cannot read the plaintext"
is therefore true: no key wrapping, no key on the server.

Crypto suite (matches iOS/macOS CryptoKit one-to-one so blobs interoperate):
  - AEAD  : ChaCha20-Poly1305-IETF (12-byte nonce, 16-byte tag)
            == CryptoKit ``ChaChaPoly`` with ``.combined`` == nonce‖ct‖tag.
  - subkey: HKDF-SHA256(ikm=K_i, salt=per-record-salt(32), info=..., L=32).
            A fresh 32-byte salt per record ⇒ a fresh content key per record,
            so a random nonce can never collide under the same key.

Self-describing record blob (plan §4.2, EXACT byte layout):

    magic(4) ‖ ver(1) ‖ alg(1) ‖ epoch(4, big-endian u32) ‖
    salt(32) ‖ nonce(12) ‖ ciphertext(var) ‖ tag(16)

  magic = b"ZSYN"; ver = 1; alg = 1 (ChaCha20-Poly1305-IETF).
  pyca's ``ChaCha20Poly1305.encrypt`` returns ``ciphertext‖tag`` combined, so
  the trailing bytes after ``nonce`` are exactly ``ciphertext‖tag``.

Additional authenticated data (AAD) binds each blob to its plaintext routing
metadata (the columns Supabase can see), so the relay cannot move a blob to a
different device/seq/action. AAD strings are ASCII, decimal integers, no
padding — reproduce them byte-for-byte on the Swift side:

    board  : "board|"  + device_id + "|" + seq       + "|" + epoch
    action : "action|" + device_id + "|" + action_id + "|" + board_seq + "|" + epoch
    label  : "label|"  + device_id + "|" + epoch

``cryptography`` (pyca) is an OPTIONAL dependency: it is imported LAZILY inside
the functions that need it, so importing this module never breaks the
PyYAML-only floor when cloud sync is unused. Invoking a crypto function without
it installed raises a clear error (see ``_crypto``).

Key storage (Mac side; mirrors the ``state/device_id`` posture — headless, no
login keychain): ``state/pairings/<device_id>.key`` (dir 0700 / file 0600,
atomic .tmp+rename). The sync-only device UUID lives in ``state/sync_device_id``
— a NEW file, deliberately NOT the telemetry ``state/device_id`` (§8-4: reusing
it would de-anonymize the anonymous telemetry id for the operator).
"""
from __future__ import annotations

import base64
import json
import os
import struct
import uuid
from pathlib import Path
from typing import Tuple

from act.lib import config

# --------------------------------------------------------------------------- #
# Blob constants (frozen wire format — see module docstring / plan §4.2)
# --------------------------------------------------------------------------- #
MAGIC = b"ZSYN"
VERSION = 1
ALG_CHACHA20POLY1305_IETF = 1

KEY_LEN = 32
_SALT_LEN = 32
_NONCE_LEN = 12
_TAG_LEN = 16
_EPOCH_MAX = 0xFFFFFFFF
# magic(4)+ver(1)+alg(1)+epoch(4)+salt(32)+nonce(12)
_HEADER_LEN = 4 + 1 + 1 + 4 + _SALT_LEN + _NONCE_LEN

_INFO_BOARD = b"actd/board/v1"
_INFO_ACTION = b"actd/action/v1"
_INFO_LABEL = b"actd/label/v1"

# Storage paths (env-driven, same layer as everything else).
PAIRINGS_DIR: Path = config.STATE_DIR / "pairings"
SYNC_DEVICE_ID_PATH: Path = config.STATE_DIR / "sync_device_id"

_DIR_MODE = 0o700
_FILE_MODE = 0o600


# --------------------------------------------------------------------------- #
# Lazy crypto import — keep the PyYAML-only floor intact when sync is unused.
# --------------------------------------------------------------------------- #
def _crypto():
    """Return (ChaCha20Poly1305, HKDF, hashes), importing pyca lazily.

    Raises a clear, actionable error only when a crypto function is actually
    invoked without ``cryptography`` installed — importing this module never
    touches pyca.
    """
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:  # pragma: no cover - exercised only sans pyca
        raise RuntimeError(
            "cloud sync needs the cryptography package; install it with "
            "`pip install cryptography` (or the project's optional [cloud] "
            "extra — see requirements-cloud.txt)."
        ) from exc
    return ChaCha20Poly1305, HKDF, hashes


# --------------------------------------------------------------------------- #
# AAD builders (ASCII, decimal ints, no padding — mirror on the Swift side).
# --------------------------------------------------------------------------- #
def _aad_board(device_id: str, seq: int, epoch: int) -> bytes:
    return f"board|{device_id}|{int(seq)}|{int(epoch)}".encode("utf-8")


def _aad_action(device_id: str, action_id: str, board_seq, epoch: int) -> bytes:
    seq_s = "" if board_seq is None else str(int(board_seq))
    return f"action|{device_id}|{action_id}|{seq_s}|{int(epoch)}".encode("utf-8")


def _aad_label(device_id: str, epoch: int) -> bytes:
    return f"label|{device_id}|{int(epoch)}".encode("utf-8")


# --------------------------------------------------------------------------- #
# Core seal / open
# --------------------------------------------------------------------------- #
def _check_key(k_i: bytes) -> None:
    if not isinstance(k_i, (bytes, bytearray)) or len(k_i) != KEY_LEN:
        raise ValueError(f"K_i must be exactly {KEY_LEN} bytes")


def _check_epoch(epoch: int) -> int:
    epoch = int(epoch)
    if epoch < 0 or epoch > _EPOCH_MAX:
        raise ValueError("epoch must fit in an unsigned 32-bit integer")
    return epoch


def _seal(k_i: bytes, epoch: int, info: bytes, aad: bytes, plaintext: bytes) -> bytes:
    _check_key(k_i)
    epoch = _check_epoch(epoch)
    chacha, hkdf, hashes = _crypto()
    salt = os.urandom(_SALT_LEN)
    content_key = hkdf(algorithm=hashes.SHA256(), length=KEY_LEN, salt=salt, info=info).derive(bytes(k_i))
    nonce = os.urandom(_NONCE_LEN)
    ct_and_tag = chacha(content_key).encrypt(nonce, plaintext, aad)
    header = MAGIC + bytes([VERSION, ALG_CHACHA20POLY1305_IETF]) + struct.pack(">I", epoch) + salt + nonce
    return header + ct_and_tag


def _open(k_i: bytes, epoch: int, info: bytes, aad_for, blob: bytes) -> bytes:
    """Parse + verify header, rebuild AAD, decrypt. Raises on any mismatch.

    ``aad_for`` is called with the (authenticated) epoch to build the AAD.
    """
    _check_key(k_i)
    epoch = _check_epoch(epoch)
    blob = bytes(blob)
    if len(blob) < _HEADER_LEN + _TAG_LEN:
        raise ValueError("blob too short")
    if blob[0:4] != MAGIC:
        raise ValueError("bad magic (not a sync blob)")
    ver = blob[4]
    alg = blob[5]
    if ver != VERSION:
        raise ValueError(f"unsupported blob version {ver}")
    if alg != ALG_CHACHA20POLY1305_IETF:
        raise ValueError(f"unsupported alg {alg}")
    (blob_epoch,) = struct.unpack(">I", blob[6:10])
    if blob_epoch != epoch:
        # Anti-rollback: the caller's expected epoch (from devices.key_epoch,
        # plaintext) must match the blob. A stale-epoch blob is rejected before
        # any key work — and the epoch is also bound into the AAD below.
        raise ValueError(f"epoch mismatch: blob={blob_epoch} expected={epoch}")
    off = 10
    salt = blob[off:off + _SALT_LEN]
    off += _SALT_LEN
    nonce = blob[off:off + _NONCE_LEN]
    off += _NONCE_LEN
    ct_and_tag = blob[off:]
    chacha, hkdf, hashes = _crypto()
    content_key = hkdf(algorithm=hashes.SHA256(), length=KEY_LEN, salt=salt, info=info).derive(bytes(k_i))
    aad = aad_for(blob_epoch)
    return chacha(content_key).decrypt(nonce, ct_and_tag, aad)


# --------------------------------------------------------------------------- #
# Public API — board / action / label
# --------------------------------------------------------------------------- #
def encrypt_board(k_i: bytes, epoch: int, device_id: str, seq: int, plaintext: bytes) -> bytes:
    """Encrypt a full board snapshot (the exact ``dashboard.json`` bytes)."""
    return _seal(k_i, epoch, _INFO_BOARD, _aad_board(device_id, seq, epoch), plaintext)


def decrypt_board(k_i: bytes, epoch: int, device_id: str, seq: int, blob: bytes) -> bytes:
    """Decrypt a board blob. Raises if tampered / wrong key / wrong metadata."""
    return _open(k_i, epoch, _INFO_BOARD, lambda e: _aad_board(device_id, seq, e), blob)


def encrypt_action(k_i: bytes, epoch: int, device_id: str, action_id: str, board_seq, plaintext: bytes) -> bytes:
    """Encrypt an approval action addressed to ``device_id`` (target Mac)."""
    return _seal(k_i, epoch, _INFO_ACTION, _aad_action(device_id, action_id, board_seq, epoch), plaintext)


def decrypt_action(k_i: bytes, epoch: int, device_id: str, action_id: str, board_seq, blob: bytes) -> bytes:
    """Decrypt an action blob. Raises if tampered / wrong key / wrong metadata."""
    return _open(k_i, epoch, _INFO_ACTION, lambda e: _aad_action(device_id, action_id, board_seq, e), blob)


def encrypt_label(k_i: bytes, epoch: int, device_id: str, label: str) -> bytes:
    """Encrypt a device label (e.g. "公司 Mac"). Labels are user content, never
    plaintext on the server (plan §4.3)."""
    return _seal(k_i, epoch, _INFO_LABEL, _aad_label(device_id, epoch), label.encode("utf-8"))


def decrypt_label(k_i: bytes, epoch: int, device_id: str, blob: bytes) -> str:
    """Decrypt a device label back to a string."""
    return _open(k_i, epoch, _INFO_LABEL, lambda e: _aad_label(device_id, e), blob).decode("utf-8")


# --------------------------------------------------------------------------- #
# Pairing keys
# --------------------------------------------------------------------------- #
def new_pairing_key() -> bytes:
    """A fresh 32-byte per-pairing symmetric key from the OS CSPRNG."""
    return os.urandom(KEY_LEN)


def _canonical_device_id(device_id: str) -> str:
    """Validate + canonicalize a device id (a UUID). Rejects anything that is
    not a UUID so it can never escape ``state/pairings/`` as a path component."""
    return str(uuid.UUID(str(device_id)))


def sync_device_id() -> str:
    """Get-or-create the sync-only device UUID (``state/sync_device_id``).

    Distinct from the telemetry ``state/device_id`` on purpose (§8-4): the cloud
    ``devices.id`` must never be the anonymous telemetry id, or enabling sync
    would de-anonymize telemetry for the operator. Same posture as the telemetry
    id (uuid4, atomic .tmp+rename), just a separate file.
    """
    try:
        val = SYNC_DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
        if val:
            return val
    except OSError:
        pass
    val = str(uuid.uuid4())
    SYNC_DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SYNC_DEVICE_ID_PATH.with_suffix(".tmp")
    tmp.write_text(val + "\n", encoding="utf-8")
    os.replace(tmp, SYNC_DEVICE_ID_PATH)
    return val


def pairing_key_path(device_id: str) -> Path:
    return PAIRINGS_DIR / f"{_canonical_device_id(device_id)}.key"


def save_pairing(device_id: str, epoch: int, k_i: bytes) -> Path:
    """Persist a pairing key for ``device_id`` (dir 0700 / file 0600, atomic).

    Stores ``{"device_id", "epoch", "key"(base64)}`` as JSON so the epoch is
    kept alongside the key (syncd needs it to encrypt with the current epoch).
    """
    _check_key(k_i)
    epoch = _check_epoch(epoch)
    device_id = _canonical_device_id(device_id)
    PAIRINGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(PAIRINGS_DIR, _DIR_MODE)
    except OSError:
        pass
    path = PAIRINGS_DIR / f"{device_id}.key"
    body = json.dumps(
        {"device_id": device_id, "epoch": epoch, "key": base64.b64encode(bytes(k_i)).decode("ascii")},
        ensure_ascii=False,
    )
    tmp = path.with_suffix(".key.tmp")
    tmp.write_text(body + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, _FILE_MODE)
    except OSError:
        pass
    os.replace(tmp, path)
    return path


def load_pairing(device_id: str) -> Tuple[int, bytes]:
    """Return ``(epoch, K_i)`` for ``device_id``. Raises ``FileNotFoundError``
    if the pairing was never saved / was removed (revoked)."""
    path = pairing_key_path(device_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    k_i = base64.b64decode(data["key"])
    _check_key(k_i)
    return int(data["epoch"]), k_i


# --------------------------------------------------------------------------- #
# QR pairing blob — opaque, in-app-parsed (NOT a URL scheme, §4.4)
# --------------------------------------------------------------------------- #
def build_pairing_blob(device_id: str, epoch: int, k_i: bytes, label: str) -> str:
    """Build the opaque QR pairing blob handed OOB to the phone.

    Carries ``{device_id, epoch, K_i(base64), enc(label)}``. The label is
    encrypted under ``K_i`` (the phone decrypts it after reading the key), so no
    plaintext label rides the QR either. Returns a base64 string with no scheme
    prefix — the app parses it, it is never registered as an ``actd://`` URL.
    """
    _check_key(k_i)
    epoch = _check_epoch(epoch)
    device_id = _canonical_device_id(device_id)
    payload = {
        "v": 1,
        "device_id": device_id,
        "epoch": epoch,
        "k": base64.b64encode(bytes(k_i)).decode("ascii"),
        "label_enc": base64.b64encode(encrypt_label(k_i, epoch, device_id, label)).decode("ascii"),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def parse_pairing_blob(blob: str) -> dict:
    """Parse a QR pairing blob → ``{device_id, epoch, key(bytes), label(str)}``.

    Verifies the version, decrypts the label with the carried key (which also
    authenticates that key/device_id/epoch travelled together untampered).
    """
    payload = json.loads(base64.b64decode(blob))
    if payload.get("v") != 1:
        raise ValueError(f"unsupported pairing-blob version {payload.get('v')}")
    device_id = _canonical_device_id(payload["device_id"])
    epoch = _check_epoch(payload["epoch"])
    k_i = base64.b64decode(payload["k"])
    _check_key(k_i)
    label = decrypt_label(k_i, epoch, device_id, base64.b64decode(payload["label_enc"]))
    return {"device_id": device_id, "epoch": epoch, "key": k_i, "label": label}
