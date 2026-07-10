-- Default-on telemetry (docs/TELEMETRY.md): clients upload with the public
-- publishable key, which maps to the `anon` role. Grant anon INSERT ONLY —
-- no select/update/delete policy exists, so the publishable key can write
-- events but can never read anyone's data back (RLS stays the guard; reads
-- remain service_role only, which bypasses RLS).

create policy analytics_events_anon_insert
    on analytics_events
    for insert
    to anon
    with check (true);
