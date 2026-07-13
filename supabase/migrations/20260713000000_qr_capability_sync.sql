-- QR-only capability sync (v2) — schema + RLS.
--
-- Design of record: docs/design/qr-only-capability-sync.md
--
-- Replaces the v1 account/email + device-token-exchange model with a CAPABILITY
-- model: a Mac's QR *is* the credential. Clients speak to Supabase with the
-- publishable (anon) key + two request headers; RLS enforces read = knowing the
-- (unguessable) channel_id, write = presenting the write_secret. No Supabase
-- Auth, no auth.uid(), no per-device JWT, no edge function. Card bodies are
-- E2E-encrypted with K (QR-only), so the server sees ciphertext only.
--
-- Idempotent / re-appliable: v1 tables ARE live on the project, so this drops
-- them first (CASCADE), and every helper/policy is create-or-replace / drop-if-
-- exists. analytics_events (telemetry) is NEVER referenced here.

create extension if not exists pgcrypto with schema extensions;
-- Defence-in-depth (SECURITY DEFINER hardening below relies on this being true):
-- anon/PUBLIC must not be able to CREATE objects in `public`, or they could
-- shadow a function the definer resolves. Modern Supabase already revokes this;
-- we assert it explicitly so the migration is safe on any project state.
revoke create on schema public from public;
revoke create on schema public from anon;

-- --------------------------------------------------------------------------- --
-- Drop v1 (account/device-token) sync objects. CASCADE clears dependent FKs /
-- policies. devices/device_secrets/device_heartbeats are removed outright;
-- board_snapshots/inbox_actions are redefined below (v1 shapes differ), so drop
-- them too for a clean re-apply. analytics_events is intentionally untouched.
-- --------------------------------------------------------------------------- --
drop table if exists public.inbox_actions cascade;
drop table if exists public.board_snapshots cascade;
drop table if exists public.device_heartbeats cascade;
drop table if exists public.device_secrets cascade;
drop table if exists public.devices cascade;

-- --------------------------------------------------------------------------- --
-- Tables (v2)
-- --------------------------------------------------------------------------- --

-- channels — registry + read/write capability anchor. write_secret_hash =
-- encode(sha256(write_secret),'hex'); the secret itself never leaves the client.
-- anon may INSERT only (register its own random channel); NO select policy, so
-- write_secret_hash is invisible to clients (RLS subqueries read it as definer).
create table public.channels (
    channel_id        uuid primary key,
    write_secret_hash text not null,
    label_enc         bytea,
    created_at        timestamptz not null default now(),
    last_seen_at      timestamptz
);

-- board_snapshots — DOWN. One row per channel (UPSERT on channel_id). payload_enc
-- is the opaque §4.2 record blob; nonce mirrors the blob's embedded 12-byte nonce
-- (kept NOT NULL per schema). updated_at (server clock) is the liveness authority.
create table public.board_snapshots (
    channel_id     uuid primary key references public.channels (channel_id) on delete cascade,
    seq            bigint not null,
    payload_enc    bytea not null,
    nonce          bytea not null,
    alg            text not null default 'chacha20poly1305-ietf',
    schema_version int not null default 1,
    updated_at     timestamptz not null default now()
);

-- inbox_actions — UP. Append-only; action_id = idempotency key = inbox file name.
-- The phone (has write_secret from the QR) inserts; the Mac advances status.
create table public.inbox_actions (
    action_id     uuid primary key,
    channel_id    uuid not null references public.channels (channel_id) on delete cascade,
    payload_enc   bytea not null,
    nonce         bytea not null,
    alg           text not null default 'chacha20poly1305-ietf',
    client_ts     timestamptz not null,
    board_seq     bigint,
    status        text not null default 'pending'
                  check (status in ('pending', 'delivered', 'applied', 'dead')),
    result_status text,
    received_at   timestamptz not null default now(),
    applied_at    timestamptz
);

create index inbox_actions_channel_status_idx
    on public.inbox_actions (channel_id, status);

-- --------------------------------------------------------------------------- --
-- Helper functions (DESIGN §RLS, HARDENED). sync_write_ok is SECURITY DEFINER so
-- anon can verify against channels.write_secret_hash through it (but never SELECT
-- channels directly). CRITICAL hardening (adversarial-review finding): pin
-- search_path to EMPTY (not `public`) and FULLY-QUALIFY every non-pg_catalog
-- reference — public.channels and extensions.digest (pgcrypto lives in the
-- `extensions` schema on Supabase; an unqualified `digest` under search_path=public
-- would either fail to resolve, or — if anon could CREATE in public — let an
-- attacker shadow it and run code as the definer). encode/coalesce/current_setting
-- are pg_catalog (always implicit). Header values ride through current_setting
-- (parameterised, no string concat) so there is no SQLi surface.
-- --------------------------------------------------------------------------- --
create or replace function public.sync_req_channel() returns uuid
  language sql stable set search_path = '' as $$
  select nullif(current_setting('request.headers', true)::json ->> 'x-sync-channel','')::uuid $$;

create or replace function public.sync_write_ok(cid uuid) returns boolean
  language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.channels c
    where c.channel_id = cid
      and c.write_secret_hash = encode(extensions.digest(
            coalesce(current_setting('request.headers', true)::json ->> 'x-sync-write',''),
            'sha256'),'hex')) $$;

-- --------------------------------------------------------------------------- --
-- Grants + RLS. Clients use the publishable key => the `anon` role.
-- --------------------------------------------------------------------------- --
alter table public.channels enable row level security;
alter table public.board_snapshots enable row level security;
alter table public.inbox_actions enable row level security;

grant insert on public.channels to anon;
grant select, insert, update on public.board_snapshots to anon;
grant select, insert, update on public.inbox_actions to anon;
grant execute on function public.sync_req_channel() to anon;
grant execute on function public.sync_write_ok(uuid) to anon;

-- channels: register exactly the channel you declare. No select/update/delete.
drop policy if exists channels_ins on public.channels;
create policy channels_ins on public.channels for insert to anon
    with check (channel_id = public.sync_req_channel());

-- board_snapshots: read = knowing the channel_id; write = presenting write_secret.
drop policy if exists board_snapshots_sel on public.board_snapshots;
create policy board_snapshots_sel on public.board_snapshots for select to anon
    using (channel_id = public.sync_req_channel());

drop policy if exists board_snapshots_ins on public.board_snapshots;
create policy board_snapshots_ins on public.board_snapshots for insert to anon
    with check (channel_id = public.sync_req_channel() and public.sync_write_ok(channel_id));

drop policy if exists board_snapshots_upd on public.board_snapshots;
create policy board_snapshots_upd on public.board_snapshots for update to anon
    using (channel_id = public.sync_req_channel() and public.sync_write_ok(channel_id))
    with check (channel_id = public.sync_req_channel() and public.sync_write_ok(channel_id));

-- inbox_actions: read = channel_id; insert (phone) + status update (Mac) = write_secret.
drop policy if exists inbox_actions_sel on public.inbox_actions;
create policy inbox_actions_sel on public.inbox_actions for select to anon
    using (channel_id = public.sync_req_channel());

drop policy if exists inbox_actions_ins on public.inbox_actions;
create policy inbox_actions_ins on public.inbox_actions for insert to anon
    with check (channel_id = public.sync_req_channel() and public.sync_write_ok(channel_id));

drop policy if exists inbox_actions_upd on public.inbox_actions;
create policy inbox_actions_upd on public.inbox_actions for update to anon
    using (channel_id = public.sync_req_channel() and public.sync_write_ok(channel_id))
    with check (channel_id = public.sync_req_channel() and public.sync_write_ok(channel_id));
