"""act/recap.py — meeting recaps: deterministic sessions in, 5 copy-only lines out (CONTRACT §63).

Hangs off the existing 30-minute screenpipe cron chain
(``ingest/process-screenpipe.sh`` runs ``python -m act.recap --once`` before
its own PID lock — no new daemon, no crontab change). One round:

  1. first run: record the engine DB's high-water ids as the marker and stop
     (no backfill — the manager-pack lesson);
  2. read frames / audio_transcriptions **by id range** since the cursor,
     fold them into per-minute presence buckets (act/lib/recap_sessions.py);
  3. audio that lands inside an already-CLOSED meeting = late slice →
     regenerate that recap (version + 1, old text into history);
  4. cluster (gap > 5 min), cut > 4 h segments, judge each session OPEN /
     CLOSED (quiet ≥ 5 min + no pending transcript, forced at 120 min);
  5. CLOSED + eligible → one sealed model call (argv pinned by
     tests/test_recap_no_egress.py: ``--tools ""``, no MCP servers), a
     deterministic validator with one retry, ``state/recap/recaps/<key>.json``,
     a notification; OPEN → listed as 进行中, no model call;
  6. prune recaps past retention, save the cursor/buffer.

Nothing here can send: the recap is not a card (no registry, no dispatch), the
JSON has no recipient / channel field, and the only exit is the clipboard on
the web 会议纪要 page. The optional Slack **draft** (§63.4; Settings toggle,
default off) is a second, whitelisted call (act/lib/recap_slack_draft.py)
that puts the text in the owner's own draft box — sending stays manual.

Other entry points (spawned detached by actd for the inbox special forms):
``--generate <key> [--note …] [--partial]`` and ``--slack-draft <key>
--channel-id <C…>``. All runs serialize on a flock; the cron round gives up
immediately when another run holds it.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from act import llm
from act.lib import (
    analytics,
    config,
    failures,
    logcap,
    notify,
)
from act.lib import recap_sessions as sessions
from act.lib import recap_slack_draft as slack_draft
from act.lib import recap_store as store
from act.lib import recap_text as text

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: no flock, the cron chain is macOS-only anyway
    fcntl = None  # type: ignore[assignment]

LLM_TIMEOUT_S = 240
DRAFT_TIMEOUT_S = 180
MAX_GENERATION_FAILURES = 3
HISTORY_CAP = 5
LOCK_WAIT_S = 120.0
NOTIFY_KIND = "recap_ready"


# --------------------------------------------------------------------------- #
# log
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    try:
        store.ensure_dirs()
        with store.log_path().open("a", encoding="utf-8", errors="replace") as fh:
            fh.write("%s  %s\n" % (_dt.datetime.now().isoformat(timespec="seconds"), msg))
        logcap.cap(store.log_path())
    except OSError:
        pass


def _iso(ts: float) -> str:
    return sessions.iso_utc(ts)


# --------------------------------------------------------------------------- #
# flock — every writer of state/recap/ goes through here
# --------------------------------------------------------------------------- #
class Lock:
    """``with Lock(wait_s) as ok:`` — ok False = someone else holds it."""

    def __init__(self, wait_s: float = 0.0) -> None:
        self.wait_s = wait_s
        self.fh = None

    def __enter__(self) -> bool:
        store.ensure_dirs()
        if fcntl is None:
            return True
        self.fh = store.lock_path().open("a")
        deadline = time.time() + self.wait_s
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.time() >= deadline:
                    return False
                time.sleep(0.5)

    def __exit__(self, *exc) -> None:
        if self.fh is not None:
            self.fh.close()


# --------------------------------------------------------------------------- #
# generation (the sealed model call)
# --------------------------------------------------------------------------- #
def voice_profile_text() -> Optional[str]:
    """docs/VOICE.md two-level fallback (mirrors executor.resolve_voice_profile
    — entrypoints may not import each other): private state/voice-profile.md,
    else the shipped config/voice-profile.default.md, else None."""
    for p in (config.STATE_DIR / "voice-profile.md",
              config.HOME / "config" / "voice-profile.default.md"):
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")[:4000]
    return None


def _call_model(prompt: str, runner, cfg, extra_argv, timeout: float) -> str:
    """§59 single LLM boundary; the recap's argv tail is the no-egress shape."""
    proc = llm.run(prompt, mode=llm.MODE_PIPELINE, runner=runner, timeout=timeout,
                   extra_argv=extra_argv, cwd=config.headless_cwd(), cfg=cfg)
    if proc.returncode != 0:
        raise RuntimeError("claude exit %s: %s" % (proc.returncode,
                                                   (proc.stderr or proc.stdout or "")[-160:]))
    return proc.stdout or ""


def _attempt(args: dict, runner, cfg, problems: Optional[list] = None) -> "tuple[Optional[dict], list]":
    raw = _call_model(text.build_prompt(problems=problems, **args), runner, cfg,
                      text.NO_EGRESS_ARGV, LLM_TIMEOUT_S)
    parsed = text.parse_output(raw)
    if parsed is None:
        return None, ["output was not the JSON object {\"en\": [5], \"zh\": [5]}"]
    return parsed, text.validate(parsed)


def generate_lines(args: dict, runner, cfg) -> "tuple[Optional[dict], str]":
    """One call, one retry with the violations quoted back; the second failure
    is still stored (需复核) — the owner can copy and fix by hand."""
    parsed, problems = _attempt(args, runner, cfg)
    if not problems:
        return parsed, store.QUALITY_OK
    retry, problems = _attempt(args, runner, cfg, problems)
    if not problems:
        return retry, store.QUALITY_OK
    best = retry or parsed
    return best, (store.QUALITY_NEEDS_REVIEW if best else store.QUALITY_FAILED)


def _when(rec: dict, tz: str) -> str:
    start = sessions.parse_ts(rec["start"]) or 0.0
    end = sessions.parse_ts(rec["end"]) or start
    a, b = sessions.local_dt(start, tz), sessions.local_dt(end, tz)
    return "%s %s–%s (%s)" % (a.strftime("%Y-%m-%d"), a.strftime("%H:%M"), b.strftime("%H:%M"), tz)


def _push_history(rec: dict) -> None:
    """The previous text (if any) moves into history, capped at HISTORY_CAP."""
    if not rec.get("en"):
        return
    entry = {"version": rec.get("version"), "generated_at": rec.get("generated_at"),
             "en": rec["en"], "zh": rec["zh"], "partial": bool(rec.get("partial"))}
    rec["history"] = (rec.get("history") or [])[-(HISTORY_CAP - 1):] + [entry]


def _apply_lines(rec: dict, lines: Optional[dict], quality: str, note: Optional[str],
                 partial: bool, now: float) -> None:
    """Version bump with the new (or absent) lines."""
    _push_history(rec)
    rec["version"] = int(rec.get("version") or 0) + 1
    rec["generated_at"] = _iso(now)
    rec["partial"] = bool(partial)
    rec["note"] = note
    rec["quality"] = quality
    rec["en"] = lines["en"] if lines else None
    rec["zh"] = lines["zh"] if lines else None


def fill_record(rec: dict, conn, st: dict, runner, cfg, now: float,
                note: Optional[str] = None, partial: bool = False) -> dict:
    """Read the transcript for the record's interval and (re)generate its
    lines in place. Thin / silent meetings never reach the model."""
    tz = st["options"].timezone
    start, end = sessions.parse_ts(rec["start"]) or 0.0, sessions.parse_ts(rec["end"]) or 0.0
    transcript = sessions.transcript_between(conn, start, end)
    words = text.transcript_words(transcript)
    rec["transcript_words"] = words
    if words == 0:
        lines, quality = None, store.QUALITY_NO_AUDIO
    elif words < text.MIN_TRANSCRIPT_WORDS:
        lines, quality = None, store.QUALITY_THIN
    else:
        args = {"transcript": transcript, "priors": store.priors_for(start, tz),
                "voice_profile": voice_profile_text(), "note": note, "partial": partial,
                "meta": {"when": _when(rec, tz), "app": rec["app"],
                         "duration_min": rec["duration_min"]}}
        lines, quality = generate_lines(args, runner, cfg)
    _apply_lines(rec, lines, quality, note, partial, now)
    return rec


def _row_label(rec: dict, tz: str) -> str:
    start = sessions.parse_ts(rec["start"]) or 0.0
    end = sessions.parse_ts(rec["end"]) or start
    return "%s–%s · %s · %s min" % (sessions.local_dt(start, tz).strftime("%H:%M"),
                                    sessions.local_dt(end, tz).strftime("%H:%M"),
                                    rec["app"], rec["duration_min"])


def _announce(rec: dict, st: dict) -> None:
    """Notification (§28 relay; only when there is text to copy) + metadata-
    only analytics — never the recap text, never the transcript."""
    if rec.get("en"):
        label = _row_label(rec, st["options"].timezone)
        notify.notify(failures.pick("会议纪要已生成", "Meeting recap ready"),
                      failures.pick("%s —— 打开看板「会议纪要」复制" % label,
                                    "%s — open the board's Meeting recaps page to copy" % label),
                      kind=NOTIFY_KIND)
    analytics.log_event("recap_generated", app=rec.get("app"), duration_min=rec.get("duration_min"),
                        words=rec.get("transcript_words"), quality=rec.get("quality"),
                        version=rec.get("version"), partial=rec.get("partial") or None)


# --------------------------------------------------------------------------- #
# Slack draft (§63.4) — only reachable when the toggle is on
# --------------------------------------------------------------------------- #
def _lines_for_language(rec: dict, st: dict, cfg) -> Optional[list]:
    lang = st["default_language"]
    if lang == "auto":
        lang = "en" if getattr(cfg, "language", "zh") == "en" else "zh"
    return rec.get(lang)


def post_slack_draft(rec: dict, channel_id: str, st: dict, runner, cfg, now: float) -> dict:
    """Whitelisted call → ``rec["slack_draft"]`` receipt. Disabled toggle or a
    recap without text short-circuits without any model call."""
    if not st["slack_draft_enabled"]:
        receipt = {"status": slack_draft.STATUS_DISABLED, "channel_link": None}
    elif not rec.get("en"):
        receipt = {"status": slack_draft.STATUS_FAILED, "channel_link": None}
    else:
        body = text.render(_lines_for_language(rec, st, cfg) or rec["en"])
        try:
            raw = _call_model(slack_draft.build_prompt(channel_id, body), runner, cfg,
                              slack_draft.ALLOWLIST_ARGV, DRAFT_TIMEOUT_S)
            receipt = slack_draft.parse_result(raw)
        except Exception as exc:  # noqa: BLE001 - a draft failure is a row badge, not a crash
            _log("slack draft failed for %s: %s" % (rec["key"], exc))
            receipt = {"status": slack_draft.STATUS_FAILED, "channel_link": None}
    receipt["at"] = _iso(now)
    rec["slack_draft"] = receipt
    return receipt


def _auto_draft(rec: dict, st: dict, runner, cfg, now: float) -> None:
    """The CLOSED-time hook: configured target → draft; none → 未投草稿."""
    if not st["slack_draft_enabled"] or not rec.get("en"):
        return
    channel = slack_draft.resolve_target(st["slack_targets"], rec["app"])
    if channel is None:
        rec["slack_draft"] = {"status": slack_draft.STATUS_NO_TARGET, "channel_link": None,
                              "at": _iso(now)}
        return
    post_slack_draft(rec, channel, st, runner, cfg, now)


# --------------------------------------------------------------------------- #
# the cron round
# --------------------------------------------------------------------------- #
def _read_new(conn, state: dict, opts: sessions.Options) -> list:
    """Advance the cursor; return the new presence buckets (frames ∪ audio)."""
    frames, f_last = sessions.read_frame_events(conn, state["cursor"].get("frames", 0), opts.rules)
    audio, a_last = sessions.read_audio_events(conn, state["cursor"].get("audio", 0))
    state["cursor"] = {"frames": f_last, "audio": a_last}
    return frames + audio


def _day_count(state: dict, now: float, tz: str) -> int:
    """Today's generation count (local date); a new day resets it."""
    today = sessions.local_dt(now, tz).strftime("%Y-%m-%d")
    day = state.get("day") if isinstance(state.get("day"), dict) else {}
    if day.get("date") != today:
        state["day"] = {"date": today, "count": 0}
    return int(state["day"].get("count") or 0)


def _bump_day(state: dict) -> None:
    state["day"]["count"] = int(state["day"].get("count") or 0) + 1


def _regenerate_late(hits: dict, conn, st: dict, runner, cfg, now: float, summary: dict) -> None:
    """Late transcript slices → version + 1 on the CLOSED recap they belong to."""
    for key in sorted(hits):
        rec = store.load_recap(key)
        if not rec:
            continue
        try:
            fill_record(rec, conn, st, runner, cfg, now)
            store.save_recap(rec)
            summary["regenerated"] += 1
            _log("late slice: regenerated %s (v%s)" % (key, rec["version"]))
        except Exception as exc:  # noqa: BLE001 - one recap's failure stays its own
            _log("late slice regeneration failed for %s: %s" % (key, exc))


def _record_for(session: sessions.Session, key: str, status: str) -> dict:
    """Existing recap (a partial 现在生成, or a re-close) or a fresh skeleton."""
    rec = store.load_recap(key) or store.new_record(session, key, status)
    rec.update({"status": status, "end": _iso(session.end), "frames": int(session.frames),
                "audio_rows": int(session.audio_rows),
                "duration_min": int(round(session.span_s / 60.0))})
    return rec


def _generate_closed(session: sessions.Session, key: str, conn, st: dict, runner, cfg,
                     now: float, state: dict, summary: dict) -> bool:
    """Try to produce the CLOSED recap; True = consumed (done or given up)."""
    rec = _record_for(session, key, sessions.CLOSED)
    try:
        fill_record(rec, conn, st, runner, cfg, now)
    except Exception as exc:  # noqa: BLE001 - retried next round, given up after N
        n = int(state["failures"].get(key, 0)) + 1
        state["failures"][key] = n
        _log("generation failed for %s (%d/%d): %s" % (key, n, MAX_GENERATION_FAILURES, exc))
        if n < MAX_GENERATION_FAILURES:
            return False
        _apply_lines(rec, None, store.QUALITY_FAILED, None, False, now)
    state["failures"].pop(key, None)
    _auto_draft(rec, st, runner, cfg, now)
    store.save_recap(rec)
    summary["generated"] += 1
    _bump_day(state)
    _announce(rec, st)
    _log("recap %s: %s (%s words, %s)" % (key, rec["quality"], rec["transcript_words"], rec["app"]))
    return True


def _pending(conn, session: sessions.Session, opts: sessions.Options) -> int:
    return sessions.pending_chunks_between(conn, session.start - opts.gap_s, session.end)


class _Budget:
    """max_per_run × max_per_day — a CLOSED meeting over the cap is held in the
    buffer for the next round, never dropped."""

    def __init__(self, per_run: int, day_left: int) -> None:
        self.per_run, self.day_left = int(per_run), int(day_left)

    def exhausted(self) -> bool:
        return self.per_run <= 0 or self.day_left <= 0

    def take(self) -> None:
        self.per_run -= 1
        self.day_left -= 1


def _closed_outcome(s: sessions.Session, key: str, conn, st: dict, runner, cfg, now: float,
                    state: dict, summary: dict, budget: _Budget) -> bool:
    """CLOSED session → True when its events may leave the buffer (generated,
    given up, or never a meeting); False = hold for the next round."""
    if not s.eligible(st["options"]):
        return True  # quiet + too small = never a meeting; drop it
    if budget.exhausted():
        return False
    budget.take()
    return _generate_closed(s, key, conn, st, runner, cfg, now, state, summary)


def _process_sessions(clusters: list, conn, st: dict, runner, cfg, now: float,
                      state: dict, summary: dict) -> "tuple[list, list]":
    """Judge every session; returns (buffer events to keep, OPEN rows)."""
    opts, keep, open_rows = st["options"], [], []
    budget = _Budget(st["max_per_run"], st["max_per_day"] - _day_count(state, now, opts.timezone))
    for s in clusters:
        key = s.key(opts.timezone)
        if sessions.verdict(s, now, _pending(conn, s, opts), opts) == sessions.OPEN:
            keep += s.events
            if s.eligible(opts):
                open_rows.append(_record_for(s, key, sessions.OPEN))
        elif not _closed_outcome(s, key, conn, st, runner, cfg, now, state, summary, budget):
            keep += s.events
    return keep, open_rows


def _round(conn, state: dict, st: dict, runner, cfg, now: float, summary: dict) -> None:
    opts = st["options"]
    new_events = _read_new(conn, state, opts)
    hits, fresh = sessions.late_slices(new_events, store.closed_intervals(now), opts.gap_s)
    _regenerate_late(hits, conn, st, runner, cfg, now, summary)
    clusters = sessions.sessions_from(sessions.merge_buckets(list(state.get("events") or []) + fresh), opts)
    keep, open_rows = _process_sessions(clusters, conn, st, runner, cfg, now, state, summary)
    state["events"] = sessions.merge_buckets(keep)
    state["open"] = open_rows
    summary["open"] = len(open_rows)
    summary["pruned"] = store.prune(now, st["retention_days"])


def _run_locked(conn, st: dict, runner, cfg, now: float, summary: dict) -> None:
    state = store.load_state()
    if state is None:
        store.save_state(store.new_state(sessions.max_ids(conn), _iso(now)))
        summary["first_run"] = True
        _log("first run: marker set at now, no backfill")
        return
    _round(conn, state, st, runner, cfg, now, summary)
    state["updated_at"] = _iso(now)
    store.save_state(state)


def _boot(cfg, now: Optional[float]) -> tuple:
    cfg = cfg or config.load_config()
    return cfg, store.settings(cfg), (time.time() if now is None else float(now))


def _open_db(st: dict):
    """Read-only connection to the engine DB (config `recap.db_path` or
    ~/.screenpipe/db.sqlite); FileNotFoundError when it is not there."""
    db = Path(st["db_path"]) if st["db_path"] else sessions.default_db_path()
    if not db.exists():
        raise FileNotFoundError(str(db))
    return sessions.connect_readonly(db)


def _with_db(conn, st: dict, fn):
    """Run ``fn(conn)`` on the given connection or a fresh one that is closed
    afterwards (tests pass their fixture connection)."""
    own = conn is None
    conn = conn or _open_db(st)
    try:
        return fn(conn)
    finally:
        if own:
            conn.close()


def run_once(now: Optional[float] = None, conn=None, runner=None, cfg=None) -> dict:
    """One cron round. ``conn`` / ``runner`` / ``cfg`` are the injection seams
    (tests hand in a fixture sqlite and a fake runner; no real claude)."""
    cfg, st, now = _boot(cfg, now)
    summary = {"first_run": False, "open": 0, "generated": 0, "regenerated": 0,
               "pruned": 0, "skipped": None}
    if not st["enabled"]:
        summary["skipped"] = "disabled"
        return summary
    try:
        _with_db(conn, st, lambda c: _run_locked(c, st, runner, cfg, now, summary))
    except FileNotFoundError:
        summary["skipped"] = "no_db"  # no engine DB on this machine: nothing to do
    return summary


# --------------------------------------------------------------------------- #
# inbox-spawned entry points
# --------------------------------------------------------------------------- #
def _open_session_record(key: str) -> Optional[dict]:
    rows = store.open_rows(store.load_state() or {})
    return next((o for o in rows if o.get("key") == key), None)


def _target_record(key: str) -> Optional[dict]:
    rec = store.load_recap(key) or _open_session_record(key)
    if rec is None:
        _log("generate: unknown key %s" % key)
    return rec


def generate(key: str, note: Optional[str] = None, partial: bool = False,
             now: Optional[float] = None, conn=None, runner=None, cfg=None) -> Optional[dict]:
    """「重新生成」(CLOSED, with the owner's note) / 「现在生成」(OPEN, partial)."""
    cfg, st, now = _boot(cfg, now)
    rec = _target_record(key)
    if rec is None:
        return None
    partial = bool(partial) or rec.get("status") == sessions.OPEN
    _with_db(conn, st, lambda c: fill_record(rec, c, st, runner, cfg, now, note=note, partial=partial))
    store.save_recap(rec)
    _announce(rec, st)
    _log("generate %s: v%s %s partial=%s" % (key, rec["version"], rec["quality"], partial))
    return rec


def slack_draft_for(key: str, channel_id: str, now: Optional[float] = None,
                    runner=None, cfg=None) -> Optional[dict]:
    """「投到 Slack 草稿」with an explicit conversation pick."""
    cfg, st, now = _boot(cfg, now)
    rec = store.load_recap(key)
    if rec is None or not store.CHANNEL_ID_RE.match(str(channel_id or "")):
        _log("slack draft: unknown key or bad channel id (%s)" % key)
        return None
    receipt = post_slack_draft(rec, channel_id, st, runner, cfg, now)
    store.save_recap(rec)
    _log("slack draft %s: %s" % (key, receipt["status"]))
    return receipt


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _dispatch(args) -> int:
    if args.generate:
        return 0 if generate(args.generate, note=args.note, partial=args.partial) else 1
    if args.slack_draft:
        return 0 if slack_draft_for(args.slack_draft, args.channel_id) else 1
    summary = run_once()
    print("recap: %s" % summary)
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m act.recap", description="meeting recaps (§63)")
    ap.add_argument("--once", action="store_true", help="one cron round (default)")
    ap.add_argument("--generate", metavar="KEY", help="(re)generate one recap")
    ap.add_argument("--note", help="owner correction for --generate (≤500 chars)")
    ap.add_argument("--partial", action="store_true", help="OPEN session: recap so far")
    ap.add_argument("--slack-draft", metavar="KEY", help="place the recap as a Slack draft")
    ap.add_argument("--channel-id", default="", help="Slack conversation id for --slack-draft")
    args = ap.parse_args(argv)
    wait = 0.0 if not (args.generate or args.slack_draft) else LOCK_WAIT_S
    try:
        with Lock(wait) as ok:
            if not ok:
                _log("another recap run holds the lock — skipping")
                return 0
            return _dispatch(args)
    except Exception:  # noqa: BLE001 - the cron chain must never see a traceback exit
        _log("run crashed:\n" + traceback.format_exc())
        print("recap: failed (see %s)" % store.log_path(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
