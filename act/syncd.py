"""syncd — the headless cloud-sync daemon (QR-only capability model, DESIGN §syncd).

``syncd`` is a SECOND client of the two-file contract, next to the Mac app: it
reads ``state/dashboard.json`` (DOWN) and writes ``state/inbox/<action_id>.json``
(UP). It NEVER imports ``actd`` and never touches the registry. Supabase only
ever relays ciphertext produced by ``act/lib/e2e.py`` (per-pairing symmetric
AEAD) — the maintainer/operator cannot read card bodies.

Capability model (v2 — replaces the v1 account/email + device-token exchange):
the Mac's QR *is* the credential. There is no Supabase Auth, no email OTP, no
``exchange_device_token`` edge function, no per-device JWT. Each Mac owns a
stable **channel**:
  * ``channel_id`` — UUIDv4, the stable identity + the READ capability.
    ``state/sync/channel_id``.
  * ``write_secret`` — 32 random bytes (persisted base64url), the WRITE
    capability. The server stores only ``sha256(<header text>)``.
    ``state/sync/write_secret``.
  * ``K`` — the 32-byte E2E symmetric key (existing per-pairing key), QR-only,
    never uploaded. ``state/pairings/<channel_id>.key`` via ``e2e.save_pairing``.

Transport: the publishable/anon Supabase key (public by design — RLS makes it
safe, exactly like telemetry) sent as both ``apikey`` and ``Authorization:
Bearer <anon>``, plus two capability headers read by RLS:
  * ``x-sync-channel: <channel_id>`` — on EVERY request.
  * ``x-sync-write: <write_secret>`` — on writes ONLY (channels INSERT,
    board_snapshots INSERT/UPDATE, inbox_actions INSERT/UPDATE).

Startup gate (unchanged): the daemon reads ``state/sync.json`` and, if that file
is absent or ``mode != "cloud"`` (or it carries no ``channel_id``), exits 0
IMMEDIATELY — before any filesystem work and before any network. A plain install
that never opted in therefore does ZERO network. Turning sync ON = writing
``state/sync.json`` with ``mode:"cloud"`` (``--pair`` does this); turning it OFF
= ``mode:"off"`` or deleting the file.

DOWN (daemon → Supabase → phone):
  * poll ``dashboard.json`` mtime (≤10s), sha256 it LOCALLY as a change-gate
    (the hash is NEVER uploaded — a plaintext-hash column would be an equality
    oracle on the server);
  * on change bump ``seq`` (seeded at startup to ``max(server row seq, local
    seq)+1`` so it never regresses under a device), ``e2e.encrypt_board`` the raw
    dashboard bytes, UPSERT ``board_snapshots`` on_conflict ``channel_id``;
  * mirror the blob's embedded nonce into the ``nonce`` column (schema NOT NULL).
  * liveness is ``board_snapshots.updated_at`` (no more heartbeats table).

UP (phone → Supabase → this daemon):
  * poll ``inbox_actions WHERE channel_id=eq.<id> AND status='pending'`` (10s);
  * dedup via the ``delivered.jsonl`` ledger (L3) so a re-materialise is
    idempotent (one inbox file per ``action_id``);
  * ``e2e.decrypt_action`` (AEAD-authenticated: a relay cannot forge or re-route
    a blob), atomically write ``state/inbox/<action_id>.json`` in the exact
    inbox schema plus ``expected_status``/``board_seq`` (consumed by the actd
    §5.4 guards), PATCH the row ``delivered``;
  * ack-tail: tail ``state/sync/applied.jsonl`` (written by actd on EVERY
    terminal disposition) with a byte cursor and PATCH the row ``applied`` +
    ``result_status`` — so the phone's "did my approve land?" is a durable
    truth, never a false-negative inferred from a deleted inbox file.

Transport reuses the ``analytics_sync`` posture: stdlib ``urllib`` only, atomic
cursor writes, and EVERY network call is best-effort — nothing here ever raises
into the daemon loop.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from act.lib import config, e2e

# --------------------------------------------------------------------------- #
# Paths (all env-driven, same layer as the rest of the project)
# --------------------------------------------------------------------------- #
SYNC_CONFIG_PATH: Path = config.STATE_DIR / "sync.json"       # opt-in gate + routing
SYNC_DIR: Path = config.STATE_DIR / "sync"                    # owns the files below
CHANNEL_ID_PATH: Path = SYNC_DIR / "channel_id"              # stable channel UUID (read cap)
WRITE_SECRET_PATH: Path = SYNC_DIR / "write_secret"         # 32 bytes base64url (write cap)
PAIRING_QR_PNG: Path = SYNC_DIR / "pairing-qr.png"          # scannable QR of the blob
DELIVERED_LEDGER: Path = SYNC_DIR / "delivered.jsonl"        # L3 dedup ledger (UP)
APPLIED_LEDGER: Path = SYNC_DIR / "applied.jsonl"            # written by actd (§5.4)
APPLIED_CURSOR_PATH: Path = SYNC_DIR / "applied_cursor.json"  # ack-tail byte cursor
DOWN_STATE_PATH: Path = SYNC_DIR / "down_state.json"         # {seq, hash} snapshot_seq
STATUS_PATH: Path = SYNC_DIR / "status.json"                 # UI-readable pause reason

DASHBOARD_POLL_SECONDS = 10
TIMEOUT_SECONDS = 15
_ALG = "chacha20poly1305-ietf"

# Capability header names (DESIGN §RLS; contract phase 1, verbatim).
HDR_CHANNEL = "x-sync-channel"
HDR_WRITE = "x-sync-write"

# Honest pause copy surfaced to the UI (plan §3: "失败即降级不崩").
PAUSE_MESSAGE = "云同步已暂停:请在 App 重新配对"


# --------------------------------------------------------------------------- #
# §7.3 B — the two-consent model's SYNC disclosure (Chinese). Stored here so the
# Settings UI can show it verbatim (`python -m act.syncd --consent-text`); this
# is a separate switch from the anonymous-usage-statistics consent (§7.3 A).
# --------------------------------------------------------------------------- #
CONSENT_DISCLOSURE_ZH = """开启多设备同步 / 默认关闭

开启后,你的任务卡片会离开这台 Mac,经 Supabase 服务器中转并存储,你的 iPhone 和另一台 Mac 才能看到同一块看板。

会离开这台机器的内容: 卡片标题、摘要、链接、备注、计划/验收清单、你在手机上的操作(通过/拒绝/修改意见文字)、设备标签。

端到端加密做了什么: 卡片正文与设备标签在离开这台 Mac 之前就已加密,密钥只经配对二维码传给你的设备、从不上传服务器。Supabase 和维护者都读不到明文。

端到端加密保护不了什么: 元数据——同步时间、数据大小、卡片数量、设备数量,以及“你在用这个功能”本身。弄丢配对二维码 = 服务器上的数据无法恢复;拿到你配对二维码的任何人都能读到你的卡片,还能替你操作。

这和“匿名使用统计”是两个独立开关,互不影响。

启用需第二次显式点按「我明白,开启同步」。无预勾选、无 dark pattern。"""


# --------------------------------------------------------------------------- #
# logging (own log file, never touches actd's)
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{_dt.datetime.now().isoformat(timespec='seconds')}  {msg}\n"
        with (config.STATE_DIR / "syncd.log").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# bytea <-> bytes (PostgREST hex text format for `bytea` columns: "\\x<hex>")
# --------------------------------------------------------------------------- #
def _to_bytea(b: bytes) -> str:
    return "\\x" + bytes(b).hex()


def _from_bytea(v) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    s = str(v)
    if s.startswith("\\x"):
        s = s[2:]
    return bytes.fromhex(s)


# --------------------------------------------------------------------------- #
# base64url (no padding) — the write_secret's on-disk + header text form. The
# QR carries write_secret as 32 raw bytes; on-disk and in the x-sync-write
# header it is this base64url text, and write_secret_hash = sha256(that text).
# --------------------------------------------------------------------------- #
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    s = str(s).strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _write_secret_hash(write_secret_text: str) -> str:
    """encode(sha256(<x-sync-write header text>),'hex') — matches the server's
    ``encode(digest(current_setting(...->>'x-sync-write'),'sha256'),'hex')``."""
    return hashlib.sha256(write_secret_text.encode("ascii")).hexdigest()


def _is_duplicate_error(exc: Exception) -> bool:
    """True when an INSERT failed because the row already exists — i.e. the
    channel is already registered, which for the self-heal path is *success*.
    Covers PostgREST's 409 (HTTPError.code) and the unique-violation text it
    returns (SQLSTATE 23505 / "duplicate" / "conflict" / "already exists")."""
    if getattr(exc, "code", None) == 409:
        return True
    text = str(exc).lower()
    return any(s in text for s in
               ("409", "conflict", "duplicate", "23505", "already exists"))


# --------------------------------------------------------------------------- #
# startup gate — the ONLY filesystem touch before we know sync is opted-in
# --------------------------------------------------------------------------- #
def read_sync_config() -> Optional[dict]:
    """Parse ``state/sync.json`` → dict, or None if absent/unreadable/not a dict."""
    try:
        data = json.loads(SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def startup_gate() -> Optional[dict]:
    """Return the sync config ONLY when opted in (``mode == "cloud"`` and a
    ``channel_id`` is present), else None.

    The single decision point that keeps a non-opted-in install fully offline:
    ``main`` returns 0 the instant this returns None, before building any
    transport or touching the network.
    """
    cfg = read_sync_config()
    if not cfg or str(cfg.get("mode") or "").lower() != "cloud":
        return None
    if not cfg.get("channel_id"):
        return None
    return cfg


# --------------------------------------------------------------------------- #
# small durable-state helpers (atomic writes, corrupt files self-heal)
# --------------------------------------------------------------------------- #
def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _complete_lines(path: Path, offset: int) -> Iterator[Tuple[bytes, int]]:
    """Yield (raw_line, end_offset) for each COMPLETE line past ``offset``
    (mirrors analytics_sync: a trailing chunk without a newline is left)."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if offset > size:
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        pos = offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            pos += len(raw)
            yield raw, pos


# --------------------------------------------------------------------------- #
# identity — channel_id + write_secret + K (generated once, persisted 0600)
# --------------------------------------------------------------------------- #
def _ensure_sync_dir() -> None:
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SYNC_DIR, 0o700)
    except OSError:
        pass


def _load_or_create_channel_id() -> str:
    try:
        val = CHANNEL_ID_PATH.read_text(encoding="utf-8").strip()
        if val:
            return str(uuid.UUID(val))
    except (OSError, ValueError):
        pass
    val = str(uuid.uuid4())
    _ensure_sync_dir()
    tmp = CHANNEL_ID_PATH.with_suffix(".tmp")
    tmp.write_text(val + "\n", encoding="utf-8")
    os.replace(tmp, CHANNEL_ID_PATH)
    try:
        os.chmod(CHANNEL_ID_PATH, 0o600)
    except OSError:
        pass
    return val


def _valid_write_secret(val: str) -> bool:
    """True iff ``val`` decodes to exactly ``WRITE_SECRET_LEN`` bytes.
    (b64decode silently DISCARDS junk characters, so the length check — not
    the decode call — is the real corruption gate.)"""
    try:
        return len(_b64url_decode(val)) == e2e.WRITE_SECRET_LEN
    except Exception:  # noqa: BLE001 - any decode failure = invalid
        return False


def _load_or_create_write_secret() -> str:
    """Return the write_secret as base64url(no-pad) text (43 chars for 32 bytes),
    generating + persisting it 0600 on first use.

    A PRESENT-but-corrupt secret must NOT be silently regenerated under the
    same channel_id: the server keeps the OLD ``write_secret_hash`` and the
    ``channels`` table is anon INSERT-only (no UPDATE policy), so every future
    write would fail RLS forever while ``--pair`` reassures the user. Recovery
    is rotating the CHANNEL: drop ``channel_id`` so ``init_channel`` mints a
    fresh, server-consistent one — the caller is already re-pairing, and the
    regenerated QR covers the phone re-scan."""
    val = _load_write_secret_text() or ""
    if _valid_write_secret(val):
        return val
    if val or WRITE_SECRET_PATH.exists():
        # present but corrupt/unreadable — same brick either way
        _log("write_secret corrupt — rotating channel_id so this re-pair "
             "mints a fresh, server-consistent channel")
        try:
            CHANNEL_ID_PATH.unlink()
        except OSError:
            pass
    val = _b64url_encode(os.urandom(e2e.WRITE_SECRET_LEN))
    _ensure_sync_dir()
    tmp = WRITE_SECRET_PATH.with_suffix(".tmp")
    tmp.write_text(val + "\n", encoding="utf-8")
    os.replace(tmp, WRITE_SECRET_PATH)
    try:
        os.chmod(WRITE_SECRET_PATH, 0o600)
    except OSError:
        pass
    return val


def _load_write_secret_text() -> Optional[str]:
    try:
        val = WRITE_SECRET_PATH.read_text(encoding="utf-8").strip()
        return val or None
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Transport — semantic PostgREST operations over the anon key + capability
# headers. The default is urllib; tests inject a fake with the SAME methods so
# no network happens. ``channel_id`` is fixed per transport (x-sync-channel on
# every request); ``write_secret`` (the x-sync-write header text) is passed on
# writes only.
# --------------------------------------------------------------------------- #
class Transport:
    def select(self, table: str, params: dict) -> List[dict]:
        raise NotImplementedError

    def insert(self, table: str, row: dict, write_secret: str) -> None:
        raise NotImplementedError

    def upsert(self, table: str, row: dict, on_conflict: str, write_secret: str) -> None:
        raise NotImplementedError

    def patch(self, table: str, params: dict, patch: dict, write_secret: str) -> None:
        raise NotImplementedError


class HttpTransport(Transport):
    """Stdlib-only PostgREST transport (no third-party deps, like
    analytics_sync). Every method raises on a non-2xx response; the Syncd caller
    swallows those (best-effort)."""

    def __init__(self, supabase_url: str, apikey: str, channel_id: str):
        self._rest = supabase_url.rstrip("/") + "/rest/v1"
        self._apikey = apikey
        self._channel_id = str(channel_id)

    def _headers(self, write_secret: Optional[str] = None,
                 extra: Optional[dict] = None) -> dict:
        h = {
            "apikey": self._apikey,
            "Authorization": "Bearer " + self._apikey,
            "Content-Type": "application/json",
            HDR_CHANNEL: self._channel_id,
        }
        if write_secret:
            h[HDR_WRITE] = write_secret
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _send(req: urllib.request.Request) -> bytes:
        # B310: URL is built from the https Supabase endpoint in the user's
        # own cloud-sync config
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # nosec B310
            return resp.read()

    def _url(self, table: str, params: Optional[dict] = None) -> str:
        from urllib.parse import urlencode
        url = f"{self._rest}/{table}"
        if params:
            url += "?" + urlencode(params)
        return url

    def select(self, table: str, params: dict) -> List[dict]:
        req = urllib.request.Request(
            self._url(table, params), method="GET",
            headers=self._headers(extra={"Accept": "application/json"}))
        raw = self._send(req)
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []

    def insert(self, table: str, row: dict, write_secret: str) -> None:
        body = json.dumps([row], ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(table), data=body, method="POST",
            headers=self._headers(write_secret, {"Prefer": "return=minimal"}))
        self._send(req)

    def upsert(self, table: str, row: dict, on_conflict: str, write_secret: str) -> None:
        body = json.dumps([row], ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(table, {"on_conflict": on_conflict}), data=body, method="POST",
            headers=self._headers(write_secret, {
                "Prefer": "resolution=merge-duplicates,return=minimal"}))
        self._send(req)

    def patch(self, table: str, params: dict, patch: dict, write_secret: str) -> None:
        body = json.dumps(patch, ensure_ascii=False).encode("utf-8")
        # urllib has no PATCH constant, but Request honours an explicit method.
        req = urllib.request.Request(
            self._url(table, params), data=body, method="PATCH",
            headers=self._headers(write_secret, {"Prefer": "return=minimal"}))
        self._send(req)


def _resolve_anon_key() -> str:
    """The publishable/anon Supabase key. Mirrors telemetry's handling exactly
    (contract): a key file (config/secrets/supabase-service-key.txt or an
    explicit telemetry.key_path) wins, else the built-in publishable key —
    public by design, RLS makes it safe (anon has INSERT-only on channels and
    no SELECT there, so write_secret_hash never leaves the server)."""
    try:
        from act.lib import analytics_sync
        return analytics_sync._resolve_key(config.load_config())
    except Exception:  # noqa: BLE001 - never fail to a missing key
        return config.DEFAULT_TELEMETRY_PUBLISHABLE_KEY


def _resolve_supabase_url(sync_cfg: dict) -> str:
    """The project URL: an explicit sync.json override wins, else the built-in
    default project (same project as telemetry, per contract)."""
    return (str(sync_cfg.get("supabase_url") or "").strip()
            or config.DEFAULT_TELEMETRY_SUPABASE_URL)


def _default_transport(sync_cfg: dict) -> HttpTransport:
    return HttpTransport(_resolve_supabase_url(sync_cfg), _resolve_anon_key(),
                         str(sync_cfg["channel_id"]))


# --------------------------------------------------------------------------- #
# inbox payload shape gate (fail-closed at the syncd boundary)
# --------------------------------------------------------------------------- #
# Scalar fields every downstream consumer (actd.process_inbox) calls str
# methods on. A non-string value is a poison file: actd crashes AFTER applying
# but BEFORE ack+unlink, so the same file re-crashes (and re-applies) every
# 10s pass forever — the whole board freezes until it is removed by hand.
_INBOX_STR_FIELDS = ("action", "id", "comment", "text", "primary", "mode",
                     "title", "note_ts")


def _inbox_shape_error(action: dict) -> Optional[str]:
    """Field-type check for a decrypted inbox payload: the AEAD authenticates
    the bytes, not the shape — anyone holding the pairing QR (or a buggy
    client build) can relay validly-encrypted junk. ``None``-valued fields
    count as absent (actd coerces them). Returns a reason string to log, or
    None when the shape is usable."""
    for key in _INBOX_STR_FIELDS:
        v = action.get(key)
        if v is not None and not isinstance(v, str):
            return f"field '{key}' is not a string"
    ids = action.get("ids")
    if ids is not None and (
            not isinstance(ids, list)
            or any(not isinstance(x, str) for x in ids)):
        return "field 'ids' is not a list of strings"
    return None


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #
class Syncd:
    def __init__(self, sync_cfg: dict, transport: Transport, *, clock=time.time):
        self.cfg = sync_cfg
        self.channel_id = str(sync_cfg["channel_id"])
        self.transport = transport
        self._clock = clock

        self._paused_reason: Optional[str] = None

        # DOWN durable state (snapshot_seq + change-gate hash live under state/sync/)
        st = _load_json(DOWN_STATE_PATH)
        self._last_hash: Optional[str] = st.get("hash")
        self._last_pushed_seq: Optional[int] = st.get("seq")
        self._next_seq: Optional[int] = None   # seeded lazily on first push
        self._seeded = False

        # write capability (x-sync-write header text); loaded lazily so a missing
        # secret → pause rather than crash.
        self._write_secret: Optional[str] = None

        # pairing key (epoch + K); loaded lazily so a missing pairing → pause
        self._epoch: Optional[int] = None
        self._k_i: Optional[bytes] = None

        # self-heal: whether this channel's registry row is known to exist. In
        # memory (re-attempted once per daemon run) so a Mac that paired OFFLINE
        # — its --pair INSERT never landed — re-registers on the next online
        # pass instead of failing every write's FK / write-secret check forever.
        self._channel_registered = False

    # -- pause / resume ------------------------------------------------------ #
    def _pause(self, reason: str) -> None:
        if self._paused_reason != reason:
            self._paused_reason = reason
            _log(f"PAUSED: {reason}")
            try:
                _atomic_write_json(STATUS_PATH, {
                    "paused": True, "reason": reason, "at": _iso_now(),
                    "channel_id": self.channel_id})
            except OSError:
                pass

    def _resume(self) -> None:
        if self._paused_reason is not None:
            self._paused_reason = None
            _log("resumed: capability + keys available")
            try:
                _atomic_write_json(STATUS_PATH, {
                    "paused": False, "at": _iso_now(), "channel_id": self.channel_id})
            except OSError:
                pass

    # -- capability / keys --------------------------------------------------- #
    def _load_write_secret(self) -> bool:
        if self._write_secret is not None:
            return True
        secret = _load_write_secret_text()
        if not secret:
            self._pause(PAUSE_MESSAGE + "(缺少写入密钥)")
            return False
        if not _valid_write_secret(secret):
            # A corrupt on-disk secret would make EVERY write fail RLS forever
            # while status.json keeps claiming sync is on — pause with an
            # honest reason instead (re-pairing rotates the channel).
            self._pause(PAUSE_MESSAGE + "(写入密钥已损坏)")
            return False
        self._write_secret = secret
        return True

    def _load_keys(self) -> bool:
        if self._k_i is not None:
            return True
        try:
            self._epoch, self._k_i = e2e.load_pairing(self.channel_id)
            return True
        except (FileNotFoundError, ValueError, OSError) as e:
            self._pause(PAUSE_MESSAGE + "(缺少配对密钥)")
            _log(f"pairing key load failed: {e}")
            return False

    def _ensure_ready(self) -> bool:
        """Load the write capability + pairing key. On success clear any prior
        pause; on failure PAUSE (status file + reason) and return False —
        never raise, so actd keeps writing dashboard.json locally regardless."""
        if not self._load_write_secret():
            return False
        if not self._load_keys():
            return False
        self._resume()
        return True

    # -- self-heal channel registration -------------------------------------- #
    def _ensure_channel_registered(self) -> None:
        """Best-effort, idempotent registration of this Mac's channel row.

        Registration normally happens once at ``--pair`` time, but if that
        INSERT failed (e.g. the Mac was offline when it paired) the daemon would
        otherwise never retry — and every board/inbox write then fails the FK /
        ``sync_write_ok`` check forever. So before the first board push of each
        online pass we re-attempt the INSERT (same shape as ``init_channel``:
        ``channel_id`` + ``write_secret_hash`` + E2E ``label_enc``, carrying the
        ``x-sync-write`` header). A duplicate / 409 means it already exists =
        success; any other error is swallowed + logged and retried next pass.
        NEVER raises — actd keeps writing dashboard.json locally regardless."""
        if self._channel_registered:
            return
        try:
            label = str(self.cfg.get("label") or "")
            label_blob = e2e.encrypt_label(self._k_i, self._epoch,
                                           self.channel_id, label)
            self.transport.insert("channels", {
                "channel_id": self.channel_id,
                "write_secret_hash": _write_secret_hash(self._write_secret),
                "label_enc": _to_bytea(label_blob),
            }, self._write_secret)
            self._channel_registered = True
            _log("channel registration confirmed (self-heal INSERT succeeded)")
        except Exception as e:  # noqa: BLE001 - best-effort; never crash the pass
            if _is_duplicate_error(e):
                self._channel_registered = True
                _log("channel already registered (duplicate) — self-heal satisfied")
            else:
                _log(f"channel self-heal register failed (will retry next pass): {e}")

    # -- DOWN ---------------------------------------------------------------- #
    def _seed_seq(self) -> None:
        """Seed the seq to ``max(server row seq, local seq) + 1`` so it never
        regresses under this device (§5.2 anti-rollback). Server read is
        best-effort; on failure we seed from local only."""
        if self._seeded:
            return
        server_seq = 0
        try:
            rows = self.transport.select(
                "board_snapshots",
                {"channel_id": f"eq.{self.channel_id}", "select": "seq"})
            if rows:
                server_seq = int(rows[0].get("seq") or 0)
        except Exception as e:  # noqa: BLE001 - best-effort seed
            _log(f"DOWN: server seq read failed (seeding from local): {e}")
        local_seq = int(self._last_pushed_seq or 0)
        self._next_seq = max(server_seq, local_seq) + 1
        self._seeded = True

    def push_down_if_changed(self) -> bool:
        """Change-gated full-snapshot UPSERT. Returns True iff a push happened."""
        try:
            raw = config.DASHBOARD_PATH.read_bytes()
        except OSError:
            return False  # no dashboard yet — nothing to push
        digest = hashlib.sha256(raw).hexdigest()   # LOCAL ONLY, never uploaded
        if digest == self._last_hash:
            return False
        self._seed_seq()
        seq = int(self._next_seq)
        try:
            blob = e2e.encrypt_board(self._k_i, self._epoch, self.channel_id, seq, raw)
            row = {
                "channel_id": self.channel_id,
                "seq": seq,
                "payload_enc": _to_bytea(blob),
                "nonce": _to_bytea(e2e.embedded_nonce(blob)),
                "alg": _ALG,
                "schema_version": 1,
                # updated_at deliberately NOT sent: the SERVER clock is the
                # liveness authority (a trigger stamps it on every write; see
                # the board_snapshots_server_updated_at migration). A skewed
                # Mac clock must never paint a dead board FRESH on the phone.
            }
            self.transport.upsert("board_snapshots", row, on_conflict="channel_id",
                                  write_secret=self._write_secret)
        except Exception as e:  # noqa: BLE001 - never advance state on failure
            _log(f"DOWN: board upsert failed (seq={seq}, will retry): {e}")
            return False
        # success — advance durable state so seq is monotone across restarts
        self._next_seq = seq + 1
        self._last_hash = digest
        self._last_pushed_seq = seq
        try:
            _atomic_write_json(DOWN_STATE_PATH, {"seq": seq, "hash": digest})
        except OSError:
            pass
        _log(f"DOWN: pushed board snapshot seq={seq} ({len(raw)} bytes)")
        return True

    # -- UP ------------------------------------------------------------------ #
    def _delivered_set(self) -> set:
        seen: set = set()
        try:
            with DELIVERED_LEDGER.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    aid = rec.get("action_id")
                    if aid:
                        seen.add(str(aid))
        except OSError:
            pass
        return seen

    def _ledger_append(self, action_id: str) -> None:
        try:
            SYNC_DIR.mkdir(parents=True, exist_ok=True)
            with DELIVERED_LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"action_id": str(action_id), "ts": _iso_now()},
                    ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _write_inbox_file(self, action_id: str, action: dict, board_seq) -> bool:
        """Atomically materialise ``state/inbox/<action_id>.json`` from the
        decrypted (AEAD-authenticated) action payload. The payload IS the inbox
        record; we pass it through (preserving action-specific fields like
        capture's ``text``) but fail CLOSED on field types — the AEAD
        authenticates the bytes, not the shape, and a poison payload (e.g. a
        dict-valued ``comment``) must die here, not wedge actd's whole inbox
        pass forever. We guarantee ``ts`` and stamp the server-authoritative
        ``board_seq`` when the row carried one (consumed by the actd §5.4
        guard). Returns False (skip) if the payload is not a usable inbox
        action or the write failed.

        M4 mark-then-materialise, refined: stage to a tmp file first, append
        the L3 delivered ledger, THEN atomically replace into the inbox. The
        file only becomes visible to actd AFTER its ledger entry exists (so a
        crash can never re-materialise a consumed action → no double-apply),
        while a failed stage write (disk full / permissions) leaves the action
        un-ledgered — it stays 'pending' and is retried next pass instead of
        being marked delivered with no inbox file ever written."""
        if not isinstance(action, dict) or not action.get("action"):
            _log(f"UP: {action_id} decrypted payload has no action — skipped")
            return False
        shape_err = _inbox_shape_error(action)
        if shape_err:
            _log(f"UP: {action_id} decrypted payload rejected ({shape_err}) — skipped")
            return False
        record = dict(action)
        record.setdefault("ts", _iso_now())
        if board_seq is not None and "board_seq" not in record:
            record["board_seq"] = board_seq
        path = config.INBOX_DIR / f"{action_id}.json"
        tmp = path.with_suffix(".json.tmp")
        try:
            config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            _log(f"UP: staging inbox file for {action_id} failed (will retry): {e}")
            return False
        self._ledger_append(action_id)
        try:
            os.replace(tmp, path)
        except OSError as e:
            # Already ledgered: the action is dropped (safe direction — the
            # phone never sees it applied and can retry), never double-applied.
            _log(f"UP: finalising inbox file for {action_id} failed: {e}")
            return False
        return True

    def _patch_delivered(self, action_id: str) -> None:
        try:
            self.transport.patch(
                "inbox_actions", {"action_id": f"eq.{action_id}"},
                {"status": "delivered"}, self._write_secret)
        except Exception as e:  # noqa: BLE001 - best-effort status advance
            _log(f"UP: patch delivered {action_id} failed (will retry): {e}")

    def pull_up(self) -> int:
        """Materialise pending actions for this channel. Returns the number of
        NEW inbox files written."""
        try:
            rows = self.transport.select(
                "inbox_actions",
                {"channel_id": f"eq.{self.channel_id}", "status": "eq.pending",
                 "select": "action_id,payload_enc,board_seq"})
        except Exception as e:  # noqa: BLE001 - best-effort pull
            _log(f"UP: pull failed (will retry): {e}")
            return 0
        delivered = self._delivered_set()
        written = 0
        for row in rows:
            aid = str(row.get("action_id") or "")
            if not aid:
                continue
            if aid in delivered:
                # L3 dedup: already materialised — one inbox file per action_id.
                # Re-attempt the delivered PATCH in case a prior one was lost.
                self._patch_delivered(aid)
                continue
            board_seq = row.get("board_seq")
            try:
                blob = _from_bytea(row.get("payload_enc"))
                plaintext = e2e.decrypt_action(
                    self._k_i, self._epoch, self.channel_id, aid, board_seq, blob)
            except Exception as e:  # noqa: BLE001 - bad/forged blob → skip, never crash
                _log(f"UP: decrypt {aid} failed (skipped): {e}")
                continue
            try:
                action = json.loads(plaintext)
            except ValueError:
                _log(f"UP: {aid} decrypted payload is not JSON — skipped")
                continue
            # M4 ordering lives inside _write_inbox_file (stage → ledger →
            # atomic replace): a stage-write failure keeps the action pending
            # for retry next pass (never falsely 'delivered'), while a crash
            # mid-materialise can never double-apply a consumed action.
            if not self._write_inbox_file(aid, action, board_seq):
                continue
            delivered.add(aid)
            self._patch_delivered(aid)
            written += 1
        if written:
            _log(f"UP: materialised {written} action(s) to the inbox")
        return written

    # -- ack-tail ------------------------------------------------------------ #
    def ack_tail(self) -> int:
        """Tail actd's ``applied.jsonl`` and PATCH ``inbox_actions`` to the
        durable terminal state (status='applied' + result_status). The byte
        cursor only advances past a line whose PATCH succeeded (a network
        failure stops the tail so the line is retried next cycle). Returns the
        number of acks applied this pass."""
        cursor = int(_load_json(APPLIED_CURSOR_PATH).get("offset") or 0)
        applied = 0
        for raw, end in _complete_lines(APPLIED_LEDGER, cursor):
            try:
                rec = json.loads(raw.decode("utf-8"))
                aid = rec.get("action_id")
                result_status = rec.get("result_status")
            except (UnicodeDecodeError, ValueError):
                self._save_cursor(end)   # skip an unreadable line
                continue
            if aid:
                try:
                    self.transport.patch(
                        "inbox_actions", {"action_id": f"eq.{aid}"},
                        {"status": "applied", "result_status": result_status,
                         "applied_at": _iso_now()}, self._write_secret)
                except Exception as e:  # noqa: BLE001 - stop, retry this line later
                    _log(f"ack-tail: patch {aid} failed (will retry): {e}")
                    break
                applied += 1
            self._save_cursor(end)
        return applied

    @staticmethod
    def _save_cursor(offset: int) -> None:
        try:
            _atomic_write_json(APPLIED_CURSOR_PATH, {"offset": int(offset)})
        except OSError:
            pass

    # -- one pass ------------------------------------------------------------ #
    def run_once(self) -> None:
        """One best-effort cycle: ensure-ready → DOWN → UP → ack-tail.
        Never raises; a missing capability/key simply pauses the whole pass."""
        if not self._ensure_ready():
            return  # paused — actd keeps writing dashboard.json locally regardless
        self._ensure_channel_registered()
        self.push_down_if_changed()
        self.pull_up()
        self.ack_tail()


# --------------------------------------------------------------------------- #
# Mac-side pairing (the CLI the Settings UI calls; DESIGN §syncd / §Mac-side QR)
# --------------------------------------------------------------------------- #
def init_channel(label: str, supabase_url: str = "",
                 platform: str = "macos") -> dict:
    """Provision this Mac's channel and enable cloud sync (the opt-in).

    Loads-or-creates the three stable secrets — ``channel_id`` (read cap),
    ``write_secret`` (write cap), and ``K`` (E2E key, epoch) — persisting them
    0600 under ``state/sync/`` and ``state/pairings/``. Registers the channel
    with Supabase (``INSERT channels`` with ``write_secret_hash`` +
    ``label_enc``) using the anon key + capability headers, then writes
    ``state/sync.json`` (mode=cloud + routing) and renders the QR.

    Idempotent: re-running loads the existing secrets (the QR is stable), so a
    duplicate channel INSERT is expected and swallowed. Returns
    ``{channel_id, qr_blob, qr_png_path, registered}``.
    """
    # Write secret FIRST: a present-but-corrupt secret rotates channel_id
    # (see _load_or_create_write_secret), so the channel id must be read after.
    write_secret_text = _load_or_create_write_secret()
    channel_id = _load_or_create_channel_id()
    write_secret_raw = _b64url_decode(write_secret_text)

    # K + epoch: reuse the existing pairing key when present (stable QR).
    try:
        epoch, k_i = e2e.load_pairing(channel_id)
    except (FileNotFoundError, ValueError, OSError):
        epoch = 1
        k_i = e2e.new_pairing_key()
        e2e.save_pairing(channel_id, epoch, k_i)

    # register the channel (anon INSERT; write header carried per contract).
    label_blob = e2e.encrypt_label(k_i, epoch, channel_id, label)
    registered = False
    try:
        transport = HttpTransport(_resolve_supabase_url({"supabase_url": supabase_url}),
                                  _resolve_anon_key(), channel_id)
        transport.insert("channels", {
            "channel_id": channel_id,
            "write_secret_hash": _write_secret_hash(write_secret_text),
            "label_enc": _to_bytea(label_blob),
        }, write_secret_text)
        registered = True
    except Exception as e:  # noqa: BLE001 - a re-pair duplicates the PK; keep going
        _log(f"channel register (INSERT channels) returned an error "
             f"(ok if re-pairing): {e}")

    # opt-in: state/sync.json (mode=cloud). Deleting it / mode=off = back to local.
    _atomic_write_json(SYNC_CONFIG_PATH, {
        "mode": "cloud",
        "channel_id": channel_id,
        "epoch": epoch,
        "label": label,
        "platform": platform,
        **({"supabase_url": supabase_url} if supabase_url else {}),
    })

    # render the scannable QR (terminal + PNG) of the pairing blob.
    qr_blob = e2e.build_channel_qr(channel_id, epoch, write_secret_raw, k_i, label)
    qr_png_path: Optional[str] = None
    try:
        from act.lib import qr
        _ensure_sync_dir()
        qr.qr_png(qr_blob, PAIRING_QR_PNG)
        qr_png_path = str(PAIRING_QR_PNG)
    except Exception as e:  # noqa: BLE001 - PNG is a convenience; the blob still prints
        _log(f"QR PNG render failed (blob still available): {e}")

    _log(f"initialised channel {channel_id} (epoch={epoch}, platform={platform}, "
         f"registered={registered})")
    return {"channel_id": channel_id, "qr_blob": qr_blob,
            "qr_png_path": qr_png_path, "registered": registered}


def disable() -> None:
    """Turn sync OFF (mode=off) — a full return to local-only. The channel_id,
    write_secret and pairing key are left in place so re-enabling needs no
    re-pairing (the QR stays stable)."""
    cfg = read_sync_config() or {}
    cfg["mode"] = "off"
    _atomic_write_json(SYNC_CONFIG_PATH, cfg)
    _log("sync disabled (mode=off) — daemon will exit 0 on next launch")


def _open_file(path: str) -> None:
    """Best-effort ``open`` of the QR PNG on macOS (no-op elsewhere / on error)."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["open", path], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 - opening the preview must never fail --pair
        pass


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="syncd", description="cloud sync daemon")
    parser.add_argument("--once", action="store_true", help="one pass then exit")
    parser.add_argument("--interval", type=int, default=None, help="poll seconds")
    parser.add_argument("--consent-text", action="store_true",
                        help="print the §7.3 sync consent disclosure and exit")
    parser.add_argument("--pair", action="store_true",
                        help="initialise this Mac's channel + enable sync + print QR")
    parser.add_argument("--json", action="store_true",
                        help="with --pair: emit ONE JSON object to stdout "
                             "(machine-readable, for the Mac app) and nothing else")
    parser.add_argument("--disable", action="store_true", help="turn sync off")
    parser.add_argument("--label", default=None,
                        help="device label for the pairing QR; when omitted, "
                             "the already-paired label (state/sync.json) is "
                             "kept, else 这台 Mac")
    parser.add_argument("--supabase-url", default="",
                        help="optional project URL override (defaults to built-in)")
    parser.add_argument("--platform", default="macos",
                        choices=("macos", "ios", "linux"))
    args = parser.parse_args(argv)

    if args.consent_text:
        print(CONSENT_DISCLOSURE_ZH)
        return 0

    if args.disable:
        disable()
        print("云同步已关闭。")
        return 0

    if args.pair:
        # Resolve the label: explicit arg → existing state/sync.json label →
        # default. The Settings page re-runs a bare --pair on every open, so
        # the old hardcoded default clobbered any custom label back to 这台 Mac.
        label = (args.label or "").strip()
        if not label:
            prior = read_sync_config() or {}
            label = str(prior.get("label") or "").strip() or "这台 Mac"
        result = init_channel(label, supabase_url=args.supabase_url,
                              platform=args.platform)
        if args.json:
            # Machine-readable: EXACTLY one JSON object on stdout, nothing else
            # (init_channel's diagnostics already go to the syncd logfile, not
            # stdout). The Mac app consumes this to render the QR itself.
            print(json.dumps({
                "channel_id": result["channel_id"],
                "qr_blob": result["qr_blob"],
                "qr_png_path": result.get("qr_png_path"),
                "registered": bool(result.get("registered")),
                "label": label,
            }, ensure_ascii=False))
            return 0
        print("用手机 App 扫描下面的二维码即可配对这台 Mac:\n")
        try:
            from act.lib import qr
            print(qr.qr_terminal(result["qr_blob"]))
        except Exception as e:  # noqa: BLE001 - fall back to the raw blob
            print(f"(二维码渲染失败: {e})")
        print("\n配对 blob(扫码失败时可手动输入):")
        print(result["qr_blob"])
        if result.get("qr_png_path"):
            print(f"\n二维码图片: {result['qr_png_path']}")
            _open_file(result["qr_png_path"])
        print(f"channel_id: {result['channel_id']}")
        if not result.get("registered"):
            print("注意: 频道注册请求未成功(重复配对时属正常;否则请检查网络后重试 --pair)。")
        return 0

    # STARTUP GATE — exit 0 immediately (zero further fs/network) when not opted in.
    sync_cfg = startup_gate()
    if sync_cfg is None:
        return 0

    transport = _default_transport(sync_cfg)
    daemon = Syncd(sync_cfg, transport)

    if args.once:
        try:
            daemon.run_once()
        except Exception as e:  # noqa: BLE001 - a single pass must never crash
            _log(f"run_once FAILED: {e}")
        return 0

    interval = args.interval or DASHBOARD_POLL_SECONDS
    _log(f"syncd starting (interval={interval}s, channel={sync_cfg['channel_id']})")
    while True:
        try:
            daemon.run_once()
        except Exception as e:  # noqa: BLE001 - one bad pass must not kill the loop
            _log(f"loop pass FAILED: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
