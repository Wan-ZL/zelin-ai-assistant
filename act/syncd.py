"""syncd — the headless cloud-sync daemon (plan of record §5).

``syncd`` is a SECOND client of the two-file contract, next to the Mac app: it
reads ``state/dashboard.json`` (DOWN) and writes ``state/inbox/<action_id>.json``
(UP). It NEVER imports ``actd`` and never touches the registry. Supabase only
ever relays ciphertext produced by ``act/lib/e2e.py`` (per-pairing symmetric
AEAD) — the maintainer/operator cannot read card bodies.

Startup gate (feasibility must-fix §medium-7 / plan §5.1): the daemon reads
``state/sync.json`` and, if that file is absent or ``mode != "cloud"``, exits 0
IMMEDIATELY — before any other filesystem work and before any network. A plain
install (or a plain upgrade) that never opted in therefore does ZERO network,
even though a launchd plist may be loaded. Turning sync ON = writing
``state/sync.json`` with ``mode:"cloud"``; turning it OFF = ``mode:"off"`` or
deleting the file (a full return to local-only).

DOWN (daemon → Supabase → phone), plan §5.2:
  * poll ``dashboard.json`` mtime (≤10s), sha256 it LOCALLY as a change-gate
    (the hash is NEVER uploaded — a plaintext-hash column would be an equality
    oracle on the server);
  * on change bump ``seq`` (seeded at startup to ``max(server row seq, local
    seq)+1`` so it never regresses under a device), ``e2e.encrypt_board`` the
    raw dashboard bytes, UPSERT ``board_snapshots`` with the device JWT;
  * mirror the blob's embedded nonce into the ``nonce`` column (schema NOT NULL);
  * heartbeat every 30s → ``device_heartbeats`` carrying ``last_pushed_seq`` (so
    a stuck board-push can't masquerade as "online, unchanged, safe", §5.6).

UP (phone → Supabase → this daemon), plan §5.3:
  * poll ``inbox_actions WHERE target_device_id=me AND status='pending'`` (10s);
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

Auth (plan §3): a launchd daemon has no login session, so it exchanges a
per-device secret (from ``config/secrets.json`` or ``state/sync.json``) for a
1-hour device-scoped JWT via the ``exchange_device_token`` Edge Function, caches
it and refreshes before expiry. An exchange failure PAUSES syncing (logs +
writes a status file with the honest "云同步已暂停:请在 App 重新配对" copy, retries
with backoff) — it NEVER crashes and never affects actd, which keeps writing
``dashboard.json`` locally.

Transport reuses the ``analytics_sync`` posture: stdlib ``urllib`` only, atomic
cursor writes, and EVERY network call is best-effort — nothing here ever raises
into the daemon loop.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import secrets as _pysecrets
import time
import urllib.request
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from act import __version__ as _VERSION
from act.lib import config, e2e

# --------------------------------------------------------------------------- #
# Paths (all env-driven, same layer as the rest of the project)
# --------------------------------------------------------------------------- #
SYNC_CONFIG_PATH: Path = config.STATE_DIR / "sync.json"       # opt-in gate + routing
SYNC_DIR: Path = config.STATE_DIR / "sync"                    # owns the files below
DELIVERED_LEDGER: Path = SYNC_DIR / "delivered.jsonl"         # L3 dedup ledger (UP)
APPLIED_LEDGER: Path = SYNC_DIR / "applied.jsonl"             # written by actd (§5.4)
APPLIED_CURSOR_PATH: Path = SYNC_DIR / "applied_cursor.json"  # ack-tail byte cursor
DOWN_STATE_PATH: Path = SYNC_DIR / "down_state.json"          # {seq, hash} snapshot_seq
STATUS_PATH: Path = SYNC_DIR / "status.json"                  # UI-readable pause reason
SECRETS_JSON_PATH: Path = config.HOME / "config" / "secrets.json"

DASHBOARD_POLL_SECONDS = 10
HEARTBEAT_SECONDS = 30
TOKEN_REFRESH_MARGIN = 300   # refresh 5 min before expiry
TIMEOUT_SECONDS = 15
_ALG = "chacha20poly1305-ietf"

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

端到端加密保护不了什么: 元数据——同步时间、数据大小、卡片数量、设备数量、你的匿名设备 ID,以及“你在用这个功能”本身。弄丢配对密钥 = 服务器上的数据无法恢复;拿到你配对密钥的任何人都能读到你的卡片。

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
    """Return the sync config ONLY when opted in (``mode == "cloud"``), else None.

    The single decision point that keeps a non-opted-in install fully offline:
    ``main`` returns 0 the instant this returns None, before building any
    transport or touching the network.
    """
    cfg = read_sync_config()
    if not cfg or str(cfg.get("mode") or "").lower() != "cloud":
        return None
    if not cfg.get("device_id"):
        return None
    return cfg


# --------------------------------------------------------------------------- #
# device secret — config/secrets.json wins, then state/sync.json
# --------------------------------------------------------------------------- #
def _load_device_secret(sync_cfg: dict) -> Optional[str]:
    try:
        data = json.loads(SECRETS_JSON_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("sync_device_secret"):
            return str(data["sync_device_secret"])
    except (OSError, ValueError):
        pass
    s = sync_cfg.get("device_secret")
    return str(s) if s else None


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
# Transport — semantic PostgREST + Edge-Function operations. The default is
# urllib; tests inject a fake with the SAME four methods so no network happens.
# --------------------------------------------------------------------------- #
class Transport:
    def exchange_token(self, device_id: str, secret: str) -> dict:
        raise NotImplementedError

    def select(self, table: str, params: dict, token: str) -> List[dict]:
        raise NotImplementedError

    def upsert(self, table: str, row: dict, token: str, on_conflict: str) -> None:
        raise NotImplementedError

    def patch(self, table: str, params: dict, patch: dict, token: str) -> None:
        raise NotImplementedError


class HttpTransport(Transport):
    """Stdlib-only PostgREST/Edge transport (no third-party deps, like
    analytics_sync). Every method raises on a non-2xx response; the Syncd caller
    swallows those (best-effort)."""

    def __init__(self, supabase_url: str, edge_url: str, apikey: str):
        self._rest = supabase_url.rstrip("/") + "/rest/v1"
        self._edge = edge_url
        self._apikey = apikey

    def _headers(self, token: str, extra: Optional[dict] = None) -> dict:
        h = {
            "apikey": self._apikey,
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _send(req: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()

    def exchange_token(self, device_id: str, secret: str) -> dict:
        body = json.dumps({"device_id": device_id, "secret": secret}).encode("utf-8")
        req = urllib.request.Request(
            self._edge, data=body, method="POST",
            headers={"Content-Type": "application/json", "apikey": self._apikey,
                     "Authorization": "Bearer " + self._apikey})
        raw = self._send(req)
        return json.loads(raw) if raw else {}

    def _url(self, table: str, params: Optional[dict] = None) -> str:
        from urllib.parse import urlencode
        url = f"{self._rest}/{table}"
        if params:
            url += "?" + urlencode(params)
        return url

    def select(self, table: str, params: dict, token: str) -> List[dict]:
        req = urllib.request.Request(
            self._url(table, params), method="GET",
            headers=self._headers(token, {"Accept": "application/json"}))
        raw = self._send(req)
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []

    def upsert(self, table: str, row: dict, token: str, on_conflict: str) -> None:
        body = json.dumps([row], ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(table, {"on_conflict": on_conflict}), data=body, method="POST",
            headers=self._headers(token, {
                "Prefer": "resolution=merge-duplicates,return=minimal"}))
        self._send(req)

    def patch(self, table: str, params: dict, patch: dict, token: str) -> None:
        body = json.dumps(patch, ensure_ascii=False).encode("utf-8")
        # urllib has no PATCH constant, but Request honours an explicit method.
        req = urllib.request.Request(
            self._url(table, params), data=body, method="PATCH",
            headers=self._headers(token, {"Prefer": "return=minimal"}))
        self._send(req)


def _default_transport(sync_cfg: dict) -> HttpTransport:
    url = str(sync_cfg.get("supabase_url") or "").rstrip("/")
    edge = (str(sync_cfg.get("edge_url") or "").strip()
            or url + "/functions/v1/exchange_device_token")
    apikey = str(sync_cfg.get("apikey") or "")
    return HttpTransport(url, edge, apikey)


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #
class Syncd:
    def __init__(self, sync_cfg: dict, transport: Transport, *, clock=time.time):
        self.cfg = sync_cfg
        self.device_id = str(sync_cfg["device_id"])
        self.owner = sync_cfg.get("owner")
        self.transport = transport
        self._clock = clock

        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._paused_reason: Optional[str] = None
        self._last_heartbeat = 0.0

        # DOWN durable state (snapshot_seq + change-gate hash live under state/sync/)
        st = _load_json(DOWN_STATE_PATH)
        self._last_hash: Optional[str] = st.get("hash")
        self._last_pushed_seq: Optional[int] = st.get("seq")
        self._next_seq: Optional[int] = None   # seeded lazily on first push
        self._seeded = False

        # pairing key (epoch + K_i); loaded lazily so a missing pairing → pause
        self._epoch: Optional[int] = None
        self._k_i: Optional[bytes] = None

    # -- pause / resume ------------------------------------------------------ #
    def _pause(self, reason: str) -> None:
        if self._paused_reason != reason:
            self._paused_reason = reason
            _log(f"PAUSED: {reason}")
            try:
                _atomic_write_json(STATUS_PATH, {
                    "paused": True, "reason": reason, "at": _iso_now(),
                    "device_id": self.device_id})
            except OSError:
                pass

    def _resume(self) -> None:
        if self._paused_reason is not None:
            self._paused_reason = None
            _log("resumed: token exchange succeeded")
            try:
                _atomic_write_json(STATUS_PATH, {
                    "paused": False, "at": _iso_now(), "device_id": self.device_id})
            except OSError:
                pass

    # -- auth ---------------------------------------------------------------- #
    def ensure_token(self) -> Optional[str]:
        """Return a valid device JWT, exchanging/refreshing as needed. On any
        failure PAUSE (log + status file) and return None — never raise."""
        now = self._clock()
        if self._token and now < self._token_exp - TOKEN_REFRESH_MARGIN:
            return self._token
        secret = _load_device_secret(self.cfg)
        if not secret:
            self._pause(PAUSE_MESSAGE + "(缺少设备密钥)")
            return None
        try:
            resp = self.transport.exchange_token(self.device_id, secret)
        except Exception as e:  # noqa: BLE001 - exchange failure must not crash
            self._pause(PAUSE_MESSAGE)
            _log(f"token exchange failed (will retry): {e}")
            return None
        token = (resp or {}).get("access_token")
        if not token:
            self._pause(PAUSE_MESSAGE + "(令牌交换失败)")
            return None
        self._token = str(token)
        try:
            ttl = int(resp.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600
        self._token_exp = now + ttl
        self._resume()
        return self._token

    # -- pairing key --------------------------------------------------------- #
    def _load_keys(self) -> bool:
        if self._k_i is not None:
            return True
        try:
            self._epoch, self._k_i = e2e.load_pairing(self.device_id)
            return True
        except (FileNotFoundError, ValueError, OSError) as e:
            self._pause(PAUSE_MESSAGE + "(缺少配对密钥)")
            _log(f"pairing key load failed: {e}")
            return False

    # -- DOWN ---------------------------------------------------------------- #
    def _seed_seq(self, token: str) -> None:
        """Seed the seq to ``max(server row seq, local seq) + 1`` so it never
        regresses under this device (§5.2 anti-rollback). Server read is
        best-effort; on failure we seed from local only."""
        if self._seeded:
            return
        server_seq = 0
        try:
            rows = self.transport.select(
                "board_snapshots",
                {"device_id": f"eq.{self.device_id}", "select": "seq"}, token)
            if rows:
                server_seq = int(rows[0].get("seq") or 0)
        except Exception as e:  # noqa: BLE001 - best-effort seed
            _log(f"DOWN: server seq read failed (seeding from local): {e}")
        local_seq = int(self._last_pushed_seq or 0)
        self._next_seq = max(server_seq, local_seq) + 1
        self._seeded = True

    def push_down_if_changed(self, token: str) -> bool:
        """Change-gated full-snapshot UPSERT. Returns True iff a push happened."""
        try:
            raw = config.DASHBOARD_PATH.read_bytes()
        except OSError:
            return False  # no dashboard yet — nothing to push
        digest = hashlib.sha256(raw).hexdigest()   # LOCAL ONLY, never uploaded
        if digest == self._last_hash:
            return False
        if not self._load_keys():
            return False
        self._seed_seq(token)
        seq = int(self._next_seq)
        try:
            blob = e2e.encrypt_board(self._k_i, self._epoch, self.device_id, seq, raw)
            row = {
                "device_id": self.device_id,
                "owner": self.owner,
                "seq": seq,
                "payload_enc": _to_bytea(blob),
                "nonce": _to_bytea(e2e.embedded_nonce(blob)),
                "alg": _ALG,
                "schema_version": 1,
                "updated_at": _iso_now(),
            }
            self.transport.upsert("board_snapshots", row, token, on_conflict="device_id")
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

    def heartbeat(self, token: str) -> None:
        now = self._clock()
        if now - self._last_heartbeat < HEARTBEAT_SECONDS:
            return
        row = {
            "device_id": self.device_id,
            "owner": self.owner,
            "beat_at": _iso_now(),
            "last_pushed_seq": self._last_pushed_seq,
            "daemon_version": _VERSION,
        }
        try:
            self.transport.upsert("device_heartbeats", row, token, on_conflict="device_id")
            self._last_heartbeat = now
        except Exception as e:  # noqa: BLE001 - best-effort liveness
            _log(f"heartbeat failed (will retry): {e}")

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
        capture's ``text``) but guarantee ``ts`` and stamp the server-authoritative
        ``board_seq`` when the row carried one (consumed by the actd §5.4 guard).
        Returns False (skip) if the payload is not a usable inbox action."""
        if not isinstance(action, dict) or not action.get("action"):
            _log(f"UP: {action_id} decrypted payload has no action — skipped")
            return False
        record = dict(action)
        record.setdefault("ts", _iso_now())
        if board_seq is not None and "board_seq" not in record:
            record["board_seq"] = board_seq
        try:
            config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
            path = config.INBOX_DIR / f"{action_id}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError as e:
            _log(f"UP: writing inbox file for {action_id} failed: {e}")
            return False

    def _patch_delivered(self, token: str, action_id: str) -> None:
        try:
            self.transport.patch(
                "inbox_actions", {"action_id": f"eq.{action_id}"},
                {"status": "delivered", "delivered_at": _iso_now()}, token)
        except Exception as e:  # noqa: BLE001 - best-effort status advance
            _log(f"UP: patch delivered {action_id} failed (will retry): {e}")

    def pull_up(self, token: str) -> int:
        """Materialise pending actions addressed to this device. Returns the
        number of NEW inbox files written."""
        if not self._load_keys():
            return 0
        try:
            rows = self.transport.select(
                "inbox_actions",
                {"target_device_id": f"eq.{self.device_id}", "status": "eq.pending",
                 "select": "action_id,payload_enc,board_seq"}, token)
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
                self._patch_delivered(token, aid)
                continue
            board_seq = row.get("board_seq")
            try:
                blob = _from_bytea(row.get("payload_enc"))
                plaintext = e2e.decrypt_action(
                    self._k_i, self._epoch, self.device_id, aid, board_seq, blob)
            except Exception as e:  # noqa: BLE001 - bad/forged blob → skip, never crash
                _log(f"UP: decrypt {aid} failed (skipped): {e}")
                continue
            try:
                action = json.loads(plaintext)
            except ValueError:
                _log(f"UP: {aid} decrypted payload is not JSON — skipped")
                continue
            # M4 mark-then-materialise: append to the L3 delivered ledger BEFORE
            # writing the inbox file. If we crash between the two and actd has
            # already consumed+deleted the file, a re-run must NOT re-materialise
            # it (that would double-apply a non-idempotent action — capture /
            # feedback). Ledger-first means the re-run sees it delivered and
            # skips; the cost is that a crash in this same window drops the
            # action instead (safe: the phone simply never sees it 'applied' and
            # can retry with a fresh idempotency key, vs a silent duplicate).
            self._ledger_append(aid)
            delivered.add(aid)
            if not self._write_inbox_file(aid, action, board_seq):
                continue
            self._patch_delivered(token, aid)
            written += 1
        if written:
            _log(f"UP: materialised {written} action(s) to the inbox")
        return written

    # -- ack-tail ------------------------------------------------------------ #
    def ack_tail(self, token: str) -> int:
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
                         "applied_at": _iso_now()}, token)
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
        """One best-effort cycle: auth → DOWN → heartbeat → UP → ack-tail.
        Never raises; a closed auth gate simply pauses the whole pass."""
        token = self.ensure_token()
        if token is None:
            return  # paused — actd keeps writing dashboard.json locally regardless
        self.push_down_if_changed(token)
        self.heartbeat(token)
        self.pull_up(token)
        self.ack_tail(token)


# --------------------------------------------------------------------------- #
# Mac-side pairing (the CLI the Settings UI calls; plan §3/§4.4)
# --------------------------------------------------------------------------- #
def _argon2_hash(secret: str) -> Optional[str]:
    """argon2id(secret) for device_secrets.secret_hash if a hasher is available
    (optional dep). None → the registration artifact ships the raw secret with a
    note that the server must hash it. Never used in the hot sync path."""
    try:
        from argon2 import PasswordHasher  # type: ignore
        return PasswordHasher().hash(secret)
    except Exception:  # noqa: BLE001 - optional; hashing may be done server-side
        return None


def pair(label: str, supabase_url: str, apikey: str, owner: str,
         platform: str = "macos", edge_url: Optional[str] = None) -> dict:
    """Provision this Mac as a sync device and enable cloud sync (the opt-in).

    Mints/loads the sync-only device UUID, a per-pairing symmetric key K_i (QR
    only, never uploaded) and a per-device secret (stored 0600 in
    config/secrets.json, server keeps only argon2id(secret)); writes
    state/sync.json (mode=cloud + routing) and a registration artifact the app/
    operator uses to insert the devices + device_secrets rows via service_role.
    Returns a dict with the QR blob + artifact path (also printed by the CLI)."""
    device_id = e2e.sync_device_id()
    epoch = 1
    k_i = e2e.new_pairing_key()
    e2e.save_pairing(device_id, epoch, k_i)

    # per-device secret (32 bytes hex) → config/secrets.json (0600)
    secret = _pysecrets.token_hex(32)
    SECRETS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SECRETS_JSON_PATH.parent, 0o700)
    except OSError:
        pass
    existing = _load_json(SECRETS_JSON_PATH)
    existing["sync_device_secret"] = secret
    _atomic_write_json(SECRETS_JSON_PATH, existing)
    try:
        os.chmod(SECRETS_JSON_PATH, 0o600)
    except OSError:
        pass

    # opt-in: state/sync.json (mode=cloud). Deleting it / mode=off = back to local.
    _atomic_write_json(SYNC_CONFIG_PATH, {
        "mode": "cloud",
        "device_id": device_id,
        "owner": owner,
        "epoch": epoch,
        "platform": platform,
        "supabase_url": supabase_url,
        "apikey": apikey,
        **({"edge_url": edge_url} if edge_url else {}),
    })

    # registration artifact for the devices + device_secrets rows (service_role)
    label_blob = e2e.encrypt_label(k_i, epoch, device_id, label)
    secret_hash = _argon2_hash(secret)
    registration = {
        "device_id": device_id,
        "owner": owner,
        "platform": platform,
        "key_epoch": epoch,
        "label_enc": _to_bytea(label_blob),
        "label_nonce": _to_bytea(e2e.embedded_nonce(label_blob)),
        "secret_hash": secret_hash,
        # only present when a local argon2 hasher was unavailable:
        **({} if secret_hash else {"secret_PLAINTEXT_hash_me_server_side": secret}),
        "note": ("Insert one row into public.devices (id/owner/label_enc/"
                 "label_nonce/platform/key_epoch) and one into "
                 "public.device_secrets (device_id/owner/secret_hash=argon2id"
                 "(secret)) using the service_role key."),
    }
    reg_path = SYNC_DIR / "pairing_registration.json"
    _atomic_write_json(reg_path, registration)
    try:
        # may carry the raw secret when no local argon2 hasher exists — 0600 it
        os.chmod(reg_path, 0o600)
    except OSError:
        pass

    qr_blob = e2e.build_pairing_blob(device_id, epoch, k_i, label)
    _log(f"paired device {device_id} (epoch={epoch}, platform={platform})")
    return {"device_id": device_id, "epoch": epoch, "qr_blob": qr_blob,
            "registration_path": str(reg_path)}


def disable() -> None:
    """Turn sync OFF (mode=off) — a full return to local-only. The pairing key
    and device secret are left in place so re-enabling needs no re-pairing."""
    cfg = read_sync_config() or {}
    cfg["mode"] = "off"
    _atomic_write_json(SYNC_CONFIG_PATH, cfg)
    _log("sync disabled (mode=off) — daemon will exit 0 on next launch")


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
                        help="provision this Mac as a sync device + enable sync")
    parser.add_argument("--disable", action="store_true", help="turn sync off")
    parser.add_argument("--label", default="这台 Mac")
    parser.add_argument("--supabase-url", default="")
    parser.add_argument("--apikey", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--platform", default="macos",
                        choices=("macos", "ios", "linux"))
    parser.add_argument("--edge-url", default=None)
    args = parser.parse_args(argv)

    if args.consent_text:
        print(CONSENT_DISCLOSURE_ZH)
        return 0

    if args.disable:
        disable()
        print("云同步已关闭。")
        return 0

    if args.pair:
        if not (args.supabase_url and args.apikey and args.owner):
            parser.error("--pair requires --supabase-url, --apikey and --owner")
        result = pair(args.label, args.supabase_url, args.apikey, args.owner,
                      platform=args.platform, edge_url=args.edge_url)
        print("配对二维码 blob(在 App 里扫描 / 展示):")
        print(result["qr_blob"])
        print(f"\n设备注册材料已写入: {result['registration_path']}")
        print(f"device_id: {result['device_id']}")
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
    _log(f"syncd starting (interval={interval}s, device={sync_cfg['device_id']})")
    while True:
        try:
            daemon.run_once()
        except Exception as e:  # noqa: BLE001 - one bad pass must not kill the loop
            _log(f"loop pass FAILED: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
