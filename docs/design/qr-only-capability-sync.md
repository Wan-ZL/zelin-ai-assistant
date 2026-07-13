# QR-only capability sync (v2) — design of record

Replaces the account/email + device-token-exchange sync auth (v1, plan §2/§3)
with a **capability model**: a Mac's QR *is* the credential. No Supabase Auth,
no email OTP, no `exchange_device_token` edge function, no per-device JWT. This
removes the two things that were blocking v1 on the real project: (a) free-tier
email can't send a 6-digit code, (b) the project migrated to ES256 so the daemon
can't self-mint an HS256 token.

## Roles (owner's mental model)
- **Mac = source + remote-controlled end.** Each Mac runs `syncd`, pushes its
  board, applies inbox actions.
- **Phone = viewer + remote control.** Scans a Mac's QR to view/control it.
- A phone may scan **many** Macs; a Mac may be scanned by **many** phones. The
  QR is stable, so re-scans are idempotent. No accounts to reconcile.

## Identity & the QR
Each Mac holds three stable secrets, generated once and persisted under
`state/sync/` (0600), never rotated unless the user re-initialises:
- `channel_id` — UUIDv4 (122-bit random). The Mac's stable identity + the **read
  capability**. `state/sync/channel_id`.
- `write_secret` — 32 random bytes, base64url. The **write capability**.
  `state/sync/write_secret`. The server stores only `sha256(write_secret)`.
- `K` — 32-byte E2E symmetric key (existing per-pairing key). Decrypts card
  bodies. `state/pairings/<channel_id>.key`.

**The QR encodes `channel_id ‖ write_secret ‖ K ‖ epoch ‖ label`** (new pairing
blob, §"pairing blob" below). Possessing the QR = full read+write+decrypt of that
Mac's board. **Security posture (owner-accepted): the QR is the single master key
— treat it like a password. Leaking it = full access to that Mac's board.** This
is the same trust anchor as v1 (the QR already carried K, whose leak already
exposed plaintext); v2 just also folds in DB access, dropping the account gate.

## Supabase schema (v2 — capability, anon-role, no auth.uid())
`create extension if not exists pgcrypto;`

- **channels** — registry. `channel_id uuid pk`, `write_secret_hash text not null`
  (= `encode(sha256(write_secret),'hex')`), `label_enc bytea`, `created_at`,
  `last_seen_at`. anon may INSERT (register own random channel); **no SELECT
  policy** (write_secret_hash never leaves the server; RLS subqueries read it as
  the policy definer).
- **board_snapshots** — DOWN. `channel_id uuid pk`, `seq bigint`, `payload_enc
  bytea`, `nonce bytea`, `alg text`, `schema_version int`, `updated_at`.
- **inbox_actions** — UP. `action_id uuid pk`, `channel_id uuid not null`,
  `payload_enc bytea`, `nonce bytea`, `alg text`, `client_ts`, `board_seq bigint`,
  `status text default 'pending' check in (pending,delivered,applied,dead)`,
  `result_status text`, `received_at`, `applied_at`. index `(channel_id,status)`.
- (dropped from v1: `devices`, `device_secrets`, `device_heartbeats`. Liveness =
  `board_snapshots.updated_at`. `analytics_events` untouched.)

## RLS (the security core)
All policies target the **anon** role (clients use the publishable/anon key; no
user session). Two request headers, read in RLS via
`current_setting('request.headers', true)::json ->> '<name>'`:
- `x-sync-channel` — the channel_id the request operates on.
- `x-sync-write` — the write_secret (only sent on writes).

Helper (SECURITY DEFINER, so it can read `channels` while anon cannot):
```sql
create or replace function public.sync_req_channel() returns uuid
  language sql stable as $$ select nullif(current_setting('request.headers', true)::json ->> 'x-sync-channel','')::uuid $$;
create or replace function public.sync_write_ok(cid uuid) returns boolean
  language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.channels c
    where c.channel_id = cid
      and c.write_secret_hash = encode(digest(
            coalesce(current_setting('request.headers', true)::json ->> 'x-sync-write',''),'sha256'),'hex')) $$;
```
Policies:
- **channels**: `INSERT to anon with check (channel_id = sync_req_channel())` —
  a Mac registers exactly the channel it declares. No select/update/delete.
- **board_snapshots**:
  - `SELECT to anon using (channel_id = sync_req_channel())` — read = knowing the
    channel_id (unguessable; mandatory-header filter blocks enumeration).
  - `INSERT/UPDATE to anon using/with check (channel_id = sync_req_channel() and
    sync_write_ok(channel_id))` — write = write_secret.
- **inbox_actions**:
  - `SELECT to anon using (channel_id = sync_req_channel())`.
  - `INSERT to anon with check (channel_id = sync_req_channel() and
    sync_write_ok(channel_id))` — the phone (has write_secret from QR) enqueues.
  - `UPDATE to anon using/with check (channel_id = sync_req_channel() and
    sync_write_ok(channel_id))` — the Mac acks status.

**Why this is safe:** (1) no header ⇒ `sync_req_channel()` is NULL ⇒ zero rows
(no enumeration / no bulk scrape). (2) channel_id is 122-bit random, only in the
QR ⇒ unguessable read capability. (3) writes require write_secret whose sha256
is compared server-side via pgcrypto ⇒ a reader (channel_id only) cannot write.
(4) bodies are E2E-encrypted with K (QR-only) ⇒ even the server / a channel_id
holder without K sees only ciphertext. (5) `sync_write_ok` is SECURITY DEFINER +
pinned `search_path` ⇒ anon can't shadow it; header values are parameterised
through `current_setting`, not string-concatenated ⇒ no SQLi.
**Residual (documented, accepted):** anyone with the full QR has full access to
that channel; there is no per-user isolation (that's the capability model). A DB
with a permissive anon INSERT on `channels` allows junk channel rows — bounded
(random ids, no read exposure); acceptable for a personal tool.

## Pairing blob (extend act/lib/e2e.py + ios E2E.swift)
New **pairing** blob (distinct from the ZSYN *record* blob, which is UNCHANGED —
interop for encrypt/decrypt_board/action/label must stay byte-identical):
`MAGIC2("ZQR1") ‖ ver(1) ‖ channel_id(16) ‖ epoch(4 BE) ‖ write_secret(32) ‖ K(32) ‖ label_utf8`.
Base64url(no pad) → the QR text. New API: `build_channel_qr(channel_id, epoch,
write_secret, K, label) -> str`, `parse_channel_qr(str) -> {...}`. Swift mirrors
byte-for-byte (interop test extended to cover the pairing blob both ways).

## syncd (act/syncd.py) — anon + headers, no JWT
- `init_channel(label)` (replaces `pair`): load/create channel_id + write_secret
  + K; `INSERT channels (channel_id, write_secret_hash, label_enc)`; return the QR
  blob + a rendered QR (see below).
- transport: anon key (publishable, hardcoded like telemetry key) + `apikey` +
  `Authorization: Bearer <anon>` + `x-sync-channel: <channel_id>`; add
  `x-sync-write: <write_secret>` on writes. **No** device token / edge function.
- DOWN: encrypt board → `UPSERT board_snapshots` (on_conflict channel_id).
- UP: `GET inbox_actions?channel_id=eq&status=eq.pending` → decrypt → write inbox
  → `PATCH status`. Ledger/idempotency unchanged.
- startup gate unchanged (mode=cloud in state/sync.json).

## Mac-side QR rendering (the "扫哪里" gap)
**Primary surface (macOS): the Settings 「同步 / 配对 · Sync / Pairing」 section.**
`mac/Sources/SettingsSync.swift` is now the main pairing UI — no terminal needed.
An ON/OFF toggle runs `python3 -m act.syncd --pair --json` (via `RuntimePython`,
the launchd interpreter) and consumes the single-line JSON
(`{channel_id, qr_blob, qr_png_path, registered, label}`); syncd persists all
state, the app persists nothing. It renders `qr_blob` in-app with CoreImage
`CIQRCodeGenerator` (CIImage → affine scale-up, nearest sampling for crisp
modules → `NSImage`, ~220pt), shows the device label + a "重新生成 / Re-pair"
button (re-runs `--pair --json`) and a "the QR is the master key, keep it
private" warning. OFF runs `--disable`. Errors (no python / pairing failed)
degrade to a plain-language row; the view never crashes.

**Headless fallback (Linux/Windows, or no GUI): `python3 -m act.syncd --pair`.**
Still prints a **scannable** QR, not just the blob, via a tiny **pure-stdlib** QR
encoder (vendored `act/lib/qr.py`, no pip — respects the PyYAML-only floor): a
Unicode/ASCII QR to the terminal AND a PNG at `state/sync/pairing-qr.png`
(`open`ed on macOS). `--pair --json` is the machine-readable variant the Settings
UI consumes (JSON to stdout only; human/QR output suppressed).

## iOS app — QR-only, multi-channel
- **Remove** email/OTP sign-in (OnboardingView/SupabaseClient auth, Sign-in UI).
- Pairing: camera scan QR → `parse_channel_qr` → store {channel_id, write_secret,
  K, label} in Keychain as one of possibly-many channels.
- SupabaseClient: anon key + per-request headers (`x-sync-channel`, and
  `x-sync-write` on inbox inserts). Read board_snapshots, decrypt with K; insert
  inbox_actions (approve/reject/comment) with write_secret.
- Board UI: channel switcher (the "device switcher"); each paired Mac = a channel.
- Onboarding copy: consent/disclosure page stays; "scan each Mac's QR" — no email.

## Migration / compatibility
This is a **breaking** change to the (unreleased-to-users) sync backend. v1 tables
(devices/device_secrets/...) are dropped and replaced. No users on v1 yet, so no
data migration. Bump: minor (0.30.0). The edge function + device-secret code paths
are removed. CONTRACT §30/§5/§7.3 updated (add-only where possible; the v1 sync
additions are superseded — note the supersession, don't silently delete).
