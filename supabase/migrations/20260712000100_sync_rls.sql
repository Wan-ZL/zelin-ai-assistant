-- Cloud multi-device sync — Row Level Security (plan of record §2.2, FIXED).
--
-- Two independent gates:
--   * account membership (this RLS, owner = auth.uid()) decides which ROWS you
--     can touch;
--   * key possession (the QR-delivered symmetric key, server-invisible) decides
--     which card bodies you can READ. RLS never sees plaintext.
--
-- Daemon vs interactive session is distinguished by the `device_id` JWT claim:
--   auth.jwt()->>'device_id' NON-NULL  => headless daemon (device-scoped) token
--                                          minted by exchange_device_token;
--   NULL                               => phone / Mac interactive user session.
--
-- MUST-FIX summary (all folded in below):
--   * owner = auth.uid() on EVERY using + with_check (can't re-home a row to
--     another user);
--   * board_snapshots / device_heartbeats writes require the device_id claim AND
--     device_id = that claim (phones can NEVER clobber a board — single-writer
--     invariant preserved);
--   * inbox_actions status updates gated to the TARGET device's token;
--   * device_secrets: RLS on, NO policy => service_role only;
--   * analytics_events is UNTOUCHED (no statement here references it).

alter table public.devices enable row level security;
alter table public.board_snapshots enable row level security;
alter table public.device_heartbeats enable row level security;
alter table public.inbox_actions enable row level security;
alter table public.device_secrets enable row level security;   -- no policy => deny all

-- device_secrets is intentionally NOT granted to authenticated/anon: only the
-- service_role key (Edge Function) reads it. analytics_events is left alone.
grant select, insert, update, delete
    on public.devices, public.board_snapshots, public.device_heartbeats, public.inbox_actions
    to authenticated;

-- devices: owner may do anything to their own device rows.
create policy dev_all on public.devices for all to authenticated
    using (owner = auth.uid())
    with check (owner = auth.uid());

-- board_snapshots: owner reads; only the owning daemon token writes its OWN board.
create policy bs_sel on public.board_snapshots for select to authenticated
    using (owner = auth.uid());
create policy bs_ins on public.board_snapshots for insert to authenticated
    with check (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and device_id = (auth.jwt() ->> 'device_id')::uuid
    );
create policy bs_upd on public.board_snapshots for update to authenticated
    using (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and device_id = (auth.jwt() ->> 'device_id')::uuid
    )
    with check (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and device_id = (auth.jwt() ->> 'device_id')::uuid
    );

-- device_heartbeats: same daemon-only write + owner read as board_snapshots.
create policy hb_sel on public.device_heartbeats for select to authenticated
    using (owner = auth.uid());
create policy hb_ins on public.device_heartbeats for insert to authenticated
    with check (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and device_id = (auth.jwt() ->> 'device_id')::uuid
    );
create policy hb_upd on public.device_heartbeats for update to authenticated
    using (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and device_id = (auth.jwt() ->> 'device_id')::uuid
    )
    with check (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and device_id = (auth.jwt() ->> 'device_id')::uuid
    );

-- inbox_actions: owner reads their actions; owner (phone) inserts (the composite
-- FK already guarantees target_device_id belongs to this owner); ONLY the target
-- device's daemon token may advance status (delivered/applied/dead + result).
create policy ia_sel on public.inbox_actions for select to authenticated
    using (owner = auth.uid());
create policy ia_ins on public.inbox_actions for insert to authenticated
    with check (owner = auth.uid());
create policy ia_upd on public.inbox_actions for update to authenticated
    using (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and target_device_id = (auth.jwt() ->> 'device_id')::uuid
    )
    with check (
        owner = auth.uid()
        and auth.jwt() ->> 'device_id' is not null
        and target_device_id = (auth.jwt() ->> 'device_id')::uuid
    );
