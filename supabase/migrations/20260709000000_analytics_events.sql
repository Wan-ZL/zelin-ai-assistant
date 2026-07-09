-- analytics_events — landing table for the opt-in telemetry sync
-- (act/lib/analytics_sync.py, docs/TELEMETRY.md). One row per JSONL event;
-- props keeps the full original record.

create table analytics_events (
    id          bigint generated always as identity primary key,
    device_id   text not null,
    sid         text,
    app_version text,
    source      text,
    event       text not null,
    props       jsonb not null default '{}'::jsonb,
    client_ts   timestamptz,
    inserted_at timestamptz not null default now()
);

-- RLS on with NO policies: anon/authenticated can neither read nor write —
-- only the service_role key (which bypasses RLS) used by the uploader.
alter table analytics_events enable row level security;

create index analytics_events_event_client_ts_idx
    on analytics_events (event, client_ts);
create index analytics_events_props_idx
    on analytics_events using gin (props);
