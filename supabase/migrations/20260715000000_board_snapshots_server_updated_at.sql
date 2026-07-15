-- board_snapshots.updated_at is the phone's liveness authority: Freshness
-- computes FRESH/STALE/DEAD from it against ServerClock and gates mutating
-- actions behind a confirm when the board may be stale. syncd used to
-- client-stamp updated_at in its UPSERT, so a Mac with a skewed clock (dead
-- NTP, VM, manual clock) could paint a dead board FRESH — defeating that
-- safety gate — or keep a healthy board permanently "stale". Stamp it
-- SERVER-side on every insert/update and override any client-supplied value
-- (the client no longer sends the column at all). search_path pinned EMPTY
-- like the other definer-adjacent functions in 20260713000000.
create or replace function public.board_snapshots_touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists board_snapshots_touch_updated_at on public.board_snapshots;
create trigger board_snapshots_touch_updated_at
    before insert or update on public.board_snapshots
    for each row execute function public.board_snapshots_touch_updated_at();
