-- Cloud multi-device sync — tables (plan of record §2.1).
--
-- Introduced by the cloud sync phase (v0.20.0). ALL additive: the existing
-- write-only `analytics_events` telemetry table is NOT touched here (anon stays
-- INSERT-only, no SELECT policy). Every user-content column is opaque ciphertext
-- produced by act/lib/e2e.py; Supabase never sees plaintext.
--
-- Blob columns hold the self-contained §4.2 record blob
-- (magic‖ver‖alg‖epoch‖salt‖nonce‖ciphertext‖tag) in `payload_enc` / `label_enc`.
-- The sibling `nonce` / `label_nonce` columns mirror the 12-byte nonce embedded
-- in that blob (kept per the §2.1 schema; syncd fills them from the blob so
-- NOT NULL holds — see the note in the crypto handoff).
--
-- NOTE (§8-4 must-fix): devices.id is a NEW sync-only UUID minted at pairing
-- (act/lib/e2e.sync_device_id → state/sync_device_id). It is deliberately NOT
-- the telemetry state/device_id — reusing that would de-anonymize the anonymous
-- telemetry identity for the operator.
--
-- NOTE (§2.2 must-fix): board_snapshots / device_heartbeats have NO content_hash
-- column. The change-gate hash is computed locally only and never uploaded —
-- a plaintext hash column would be an equality/confirmation oracle on the server.

-- 1. devices — registry. id = sync-only UUID, minted at pairing.
create table public.devices (
    id           uuid primary key,
    owner        uuid not null references auth.users (id) on delete cascade,
    label_enc    bytea not null,
    label_nonce  bytea not null,
    platform     text not null check (platform in ('macos', 'ios', 'linux')),
    key_epoch    int not null default 1,
    created_at   timestamptz not null default now(),
    last_seen_at timestamptz,
    -- composite target for the downstream FKs (blocks cross-tenant device-squat)
    unique (owner, id)
);

-- 2. device_secrets — daemon auth material. RLS on with NO policy below
-- => only the service_role key (bypasses RLS) can read/write it. The Edge
-- Function `exchange_device_token` verifies argon2id(secret) against secret_hash.
create table public.device_secrets (
    device_id   uuid primary key references public.devices (id) on delete cascade,
    owner       uuid not null references auth.users (id) on delete cascade,
    secret_hash text not null,        -- argon2id(device secret); never returned
    revoked_at  timestamptz
);

-- 3. board_snapshots — DOWN. One row per device (UPSERT). body is opaque ciphertext.
create table public.board_snapshots (
    device_id      uuid primary key references public.devices (id) on delete cascade,
    owner          uuid not null,
    seq            bigint not null,               -- monotonic / anti-rollback
    payload_enc    bytea not null,
    nonce          bytea not null,
    alg            text not null default 'chacha20poly1305-ietf',
    schema_version int not null default 1,        -- dashboard schema gate (plaintext, non-sensitive)
    updated_at     timestamptz not null default now(),  -- server clock = board-freshness authority
    -- guarantees (owner, device_id) is a real, owner-owned device
    foreign key (owner, device_id) references public.devices (owner, id) on delete cascade
);

-- 4. device_heartbeats — liveness. Separate table so a heartbeat never triggers
-- the board Realtime stream. Carries last_pushed_seq to unmask a stuck push.
create table public.device_heartbeats (
    device_id       uuid primary key references public.devices (id) on delete cascade,
    owner           uuid not null,
    beat_at         timestamptz not null default now(),
    last_pushed_seq bigint,
    daemon_version  text,
    foreign key (owner, device_id) references public.devices (owner, id) on delete cascade
);

-- 5. inbox_actions — UP. Append-only. action_id = idempotency key = inbox file name.
create table public.inbox_actions (
    action_id        uuid primary key,             -- minted by the client at tap
    owner            uuid not null,
    target_device_id uuid not null,                -- routing; pinned at tap time
    payload_enc      bytea not null,
    nonce            bytea not null,
    alg              text not null default 'chacha20poly1305-ietf',
    client_ts        timestamptz not null,
    board_seq        bigint,
    status           text not null default 'pending'
                     check (status in ('pending', 'delivered', 'applied', 'dead')),
    result_status    text,                         -- from actd ack: running|noop|unknown|bad_json
    received_at      timestamptz not null default now(),
    delivered_at     timestamptz,
    applied_at       timestamptz,
    -- composite FK: target_device_id MUST belong to this owner (blocks
    -- orphan / typo / cross-tenant injection of actions at another user's device)
    foreign key (owner, target_device_id) references public.devices (owner, id) on delete cascade
);

create index inbox_actions_target_pending_idx
    on public.inbox_actions (target_device_id, status);
