"""Usage-insights report builder for the pinned GitHub issue (insights.yml).

Machine-independent telemetry loop: runs daily in GitHub Actions, pulls the
last N days of ``analytics_events`` rows from the telemetry Supabase project
(docs/TELEMETRY.md) and reduces them to AGGREGATES ONLY. The report answers
four product questions instead of dumping vanity per-event counts:

  1. Activation funnel — distinct devices at each lifecycle stage
     (install -> configure -> first card -> first approval -> first delivery)
     with drop-off %.
  2. Reliability — failure rate per ingest / dispatch / action path.
  3. Feature abandonment — configured-but-unused and used-exactly-once.
  4. Retention — distinct devices returning after their first-seen day.

No raw rows, no props payloads, and no device ids ever appear in the output —
devices show up only as a distinct COUNT (pinned by tests/test_insights_report).
The legacy raw counts survive in a collapsed <details> appendix; the
``**Totals:** N events`` line the workflow greps for is emitted once, in the
main body (never duplicated into the appendix).

If ``ANTHROPIC_API_KEY`` is set, the funnel/failure/abandonment/retention views
are summarized into up to 5 concrete "Fix X — because <number>" recommendations
(claude-sonnet-5) with confidence labels; without the key the issue simply
carries the derived views.

Env:
  SUPABASE_URL           optional — defaults to the maintainer project
  SUPABASE_INSIGHTS_KEY  read-capable key (repo secret). Missing -> the body
                         explains how to configure it and the run stays green.
  ANTHROPIC_API_KEY      optional — enables the AI insights section.
  INSIGHTS_DAYS          window in days, default 30.

Usage: python3 scripts/insights_report.py --out insights-body.md
Exit codes: 0 = body written (including graceful degradations);
            2 = Supabase fetch failed (an error body IS still written).

Stdlib only — the repo has no third-party runtime deps beyond PyYAML, and
this script needs none at all.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict

DEFAULT_SUPABASE_URL = "https://vlxshwmdjpaxmcwbhutb.supabase.co"
ANTHROPIC_MODEL = "claude-sonnet-5"
ISSUE_TITLE = "\U0001F4CA Usage Insights"  # 📊 — must match insights.yml

PAGE_SIZE = 1000
MAX_PAGES = 200  # hard cap: 200k rows per run is far beyond expected volume
HTTP_TIMEOUT = 30

# Honest caveat printed above the derived views: the single analytics_events
# table commingles every install's anonymous devices, so a shared / multi-user
# deployment inflates the funnel and retention rates. A per-tenant marker to
# separate them is DEFERRED (privacy-sensitive; belongs with the sync/auth
# design) — this line keeps the report honest until then.
TENANT_CAVEAT = (
    "> _Caveat: these aggregates commingle the anonymous devices of **every** "
    "install. A single shared / multi-user deployment counts as many devices "
    "and can skew the funnel and retention rates below. A per-tenant marker to "
    "separate installs is deferred (privacy-sensitive)._"
)


# --------------------------------------------------------------------------- #
# fetch — PostgREST paging via Range headers, minimal columns
# --------------------------------------------------------------------------- #
def fetch_rows(url: str, key: str, since_iso: str,
               opener=None) -> list:
    """Page through analytics_events rows newer than ``since_iso``."""
    opener = opener or urllib.request.urlopen
    endpoint = (
        url.rstrip("/")
        + "/rest/v1/analytics_events"
        # client_ts drives retention (client behavior time, not server insert)
        + "?select=device_id,event,app_version,inserted_at,client_ts,props"
        + "&inserted_at=gte." + since_iso
        + "&order=inserted_at.asc"
    )
    rows: list = []
    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        req = urllib.request.Request(endpoint, headers={
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Range-Unit": "items",
            "Range": f"{start}-{start + PAGE_SIZE - 1}",
        })
        with opener(req, timeout=HTTP_TIMEOUT) as resp:
            batch = json.loads(resp.read().decode("utf-8"))
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected PostgREST payload: {type(batch)}")
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    return rows


# --------------------------------------------------------------------------- #
# row accessors — every reducer reads rows through these, so nothing
# row-level (device ids, content) leaks past the aggregation boundary
# --------------------------------------------------------------------------- #
def _device(row: dict):
    d = row.get("device_id")
    return str(d) if d else None


def _event(row: dict) -> str:
    return str(row.get("event") or "")


def _props(row: dict) -> dict:
    p = row.get("props")
    return p if isinstance(p, dict) else {}


def _feature(row: dict):
    """The ``feature`` marker on feature_first_reach events (app_launch /
    ingest_configured / ask / capture / ...)."""
    f = _props(row).get("feature")
    return f if isinstance(f, str) and f else None


def _source(row: dict):
    s = _props(row).get("source")
    return str(s) if s else None


def _parse_iso(s: str):
    """Tolerant ISO-8601 -> aware UTC datetime (handles trailing Z, +00:00,
    fractional seconds), or None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _row_dt(row: dict):
    """Behavior time for a row: client_ts if present, else server inserted_at."""
    for k in ("client_ts", "inserted_at"):
        d = _parse_iso(str(row.get(k) or ""))
        if d:
            return d
    return None


# --------------------------------------------------------------------------- #
# 1. activation funnel — DISTINCT devices per lifecycle stage
# --------------------------------------------------------------------------- #
# Each stage's reach is any of a set of event names (or a feature_first_reach
# marker). Milestone events are the v0.19+ signal; the legacy producer events
# are unioned so the funnel is meaningful over historical rows too.
_STAGE_FEATURE = "feature"   # match feature_first_reach{feature=<spec>}
_STAGE_EVENTS = "events"     # match event name in <spec>

FUNNEL = [
    ("installed", "Installed (app launched)",
     (_STAGE_FEATURE, "app_launch")),
    ("configured", "Configured an ingest source",
     (_STAGE_FEATURE, "ingest_configured")),
    ("first_card", "First proposal card",
     (_STAGE_EVENTS, {"milestone_first_card", "card_sent"})),
    ("first_approval", "First approval",
     (_STAGE_EVENTS, {"milestone_first_approval", "inbox_approve"})),
    ("first_delivery", "First delivery (dispatch)",
     (_STAGE_EVENTS, {"milestone_first_delivery", "dispatch"})),
]


def _device_rows(rows: list):
    """(device, row) for every dict row that carries a device id."""
    for row in rows:
        dev = _device(row) if isinstance(row, dict) else None
        if dev:
            yield dev, row


def _stage_hit(row: dict, kind: str, spec) -> bool:
    """Does this row reach a stage: a feature_first_reach marker for the spec'd
    feature, or any event name in the spec set."""
    if kind == _STAGE_FEATURE:
        return _event(row) == "feature_first_reach" and _feature(row) == spec
    return _event(row) in spec


def _reached_by_stage(rows: list) -> "tuple[dict, set]":
    """(stage → devices that reached it, all telemetry-producing devices)."""
    reached: dict = {key: set() for key, _, _ in FUNNEL}
    all_devices: set = set()
    for dev, row in _device_rows(rows):
        all_devices.add(dev)
        for key, _label, (kind, spec) in FUNNEL:
            if _stage_hit(row, kind, spec):
                reached[key].add(dev)
    return reached, all_devices


def _cumulative_counts(reached: dict) -> dict:
    """Monotonic: reaching a later stage implies every earlier stage, so
    accumulate from the bottom up. Guarantees a non-increasing funnel even on
    messy historical data (e.g. a device with only a dispatch row)."""
    cum: set = set()
    counts: dict = {}
    for key, _label, _m in reversed(FUNNEL):
        cum |= reached[key]
        counts[key] = len(cum)
    return counts


def _stage_rows(counts: dict, base: int) -> list:
    stages = []
    prev = None
    for key, label, _m in FUNNEL:
        n = counts[key]
        pct = (100.0 * n / base) if base else 0.0
        drop = (100.0 * (prev - n) / prev) if (prev not in (None, 0)) else None
        stages.append({
            "key": key, "label": label, "devices": n,
            "pct_of_install": pct, "drop_from_prev_pct": drop,
        })
        prev = n
    return stages


def funnel(rows: list) -> dict:
    reached, all_devices = _reached_by_stage(rows)
    # Install proxy: every telemetry-producing device has the app installed, so
    # when the explicit app_launch marker is sparse (upgraded installs never
    # fired it), fall back to all distinct devices.
    if not reached["installed"]:
        reached["installed"] = set(all_devices)
    counts = _cumulative_counts(reached)
    base = counts[FUNNEL[0][0]]
    return {"stages": _stage_rows(counts, base), "install_base": base}


# --------------------------------------------------------------------------- #
# 2. reliability — failure rate per path
# --------------------------------------------------------------------------- #
class _PathTally:
    """Per-row accumulator behind path_failures."""

    def __init__(self):
        self.ok_events: dict = defaultdict(lambda: {"fail": 0, "total": 0})
        self.ingest_scans: Counter = Counter()          # by source
        self.ingest_skips: Counter = Counter()          # by source
        self.skip_reasons: dict = defaultdict(Counter)  # source -> reason -> count
        self.dispatch_ok = 0
        self.dispatch_fail = 0

    def add(self, row: dict) -> None:
        ev = _event(row)
        props = _props(row)
        self._add_ok_flag(ev, props.get("ok"))
        if ev == "radar_scan":
            self.ingest_scans[_source(row) or "(unknown)"] += 1
        elif ev == "radar_skip":
            self._add_skip(_source(row) or "(unknown)", props.get("reason"))
        else:
            self._add_dispatch(ev)

    def _add_dispatch(self, ev: str) -> None:
        if ev == "dispatch":
            self.dispatch_ok += 1
        elif ev == "dispatch_failed":
            self.dispatch_fail += 1

    def _add_ok_flag(self, ev: str, ok) -> None:
        if isinstance(ok, bool):
            self.ok_events[ev]["total"] += 1
            if not ok:
                self.ok_events[ev]["fail"] += 1

    def _add_skip(self, src: str, reason) -> None:
        self.ingest_skips[src] += 1
        if isinstance(reason, str) and reason:
            self.skip_reasons[src][reason] += 1

    def _ingest_view(self) -> dict:
        ingest = {}
        for src in sorted(set(self.ingest_scans) | set(self.ingest_skips)):
            scans = self.ingest_scans[src]
            skips = self.ingest_skips[src]
            denom = scans + skips
            top = self.skip_reasons[src].most_common(1)
            ingest[src] = {
                "scans": scans,
                "skips": skips,
                "skip_rate_pct": (100.0 * skips / denom) if denom else 0.0,
                "top_reason": (top[0][0], top[0][1]) if top else None,
            }
        return ingest

    def view(self) -> dict:
        d_total = self.dispatch_ok + self.dispatch_fail
        return {
            "ok_events": {
                ev: {"fail": v["fail"], "total": v["total"],
                     "rate_pct": 100.0 * v["fail"] / v["total"]}
                for ev, v in sorted(self.ok_events.items()) if v["total"]
            },
            "ingest": self._ingest_view(),
            "dispatch": {
                "ok": self.dispatch_ok, "failed": self.dispatch_fail, "total": d_total,
                "fail_rate_pct": (100.0 * self.dispatch_fail / d_total) if d_total else 0.0,
            },
        }


def path_failures(rows: list) -> dict:
    tally = _PathTally()
    for row in rows:
        if isinstance(row, dict):
            tally.add(row)
    return tally.view()


# --------------------------------------------------------------------------- #
# 3. feature abandonment — configured-but-unused / used-exactly-once
# --------------------------------------------------------------------------- #
_CARD_EVENTS = {"milestone_first_card", "card_sent"}

# Once-per-install milestone / first-reach events are used exactly once BY
# CONSTRUCTION (analytics.log_first fires each a single time per install), so
# they always dominate the "used exactly once" table without signalling any
# abandonment. Exclude them — this view is about features TRIED then DROPPED.
_MILESTONE_EVENTS = {
    "milestone_first_card", "milestone_first_approval",
    "milestone_first_delivery", "feature_first_reach",
}


def _abandonment_sets(rows: list) -> "tuple[set, set, dict]":
    """(configured devices, carded devices, event → device → count)."""
    configured: set = set()
    carded: set = set()
    per_event: dict = defaultdict(Counter)  # event -> device -> count
    for dev, row in _device_rows(rows):
        ev = _event(row)
        if _is_configured_marker(row, ev):
            configured.add(dev)
        if ev in _CARD_EVENTS:
            carded.add(dev)
        if ev not in _MILESTONE_EVENTS:   # milestones are once-by-construction
            per_event[ev][dev] += 1
    return configured, carded, per_event


def _is_configured_marker(row: dict, ev: str) -> bool:
    return ev == "feature_first_reach" and _feature(row) == "ingest_configured"


def _used_once(per_event: dict) -> dict:
    used_once = {}
    for ev, devc in per_event.items():
        once = sum(1 for c in devc.values() if c == 1)
        if once:
            used_once[ev] = once
    return used_once


def abandonment(rows: list) -> dict:
    configured, carded, per_event = _abandonment_sets(rows)
    no_card = configured - carded
    conf = len(configured)
    return {
        "configured": conf,
        "configured_no_card": len(no_card),
        "configured_no_card_pct": (100.0 * len(no_card) / conf) if conf else 0.0,
        "used_once": _used_once(per_event),
    }


# --------------------------------------------------------------------------- #
# 4. retention — distinct devices returning after their first-seen day
# --------------------------------------------------------------------------- #
def _activity_days(rows: list) -> "tuple[dict, dict, object]":
    """(device → first-seen day, device → {active days}, newest day or None)."""
    first_day: dict = {}
    active_days: dict = defaultdict(set)
    max_day = None
    for dev, row in _device_rows(rows):
        d = _row_dt(row)
        if d is None:
            continue
        day = d.date()
        active_days[dev].add(day)
        _note_first_day(first_day, dev, day)
        max_day = max(max_day, day) if max_day is not None else day
    return first_day, active_days, max_day


def _note_first_day(first_day: dict, dev: str, day) -> None:
    if dev not in first_day or day < first_day[dev]:
        first_day[dev] = day


def _cohort(first_day: dict, active_days: dict, max_day, min_delta: int) -> dict:
    """Only devices with enough elapsed window to have HAD the chance to
    return (first-seen at least min_delta days before the newest event)."""
    seen = 0
    returned = 0
    for dev, f in first_day.items():
        if (max_day - f).days < min_delta:
            continue
        seen += 1
        if any((day - f).days >= min_delta for day in active_days[dev]):
            returned += 1
    return {"cohort": seen, "returned": returned,
            "rate_pct": (100.0 * returned / seen) if seen else 0.0}


def retention(rows: list) -> dict:
    first_day, active_days, max_day = _activity_days(rows)
    if max_day is None:
        empty = {"cohort": 0, "returned": 0, "rate_pct": 0.0}
        return {"devices": 0, "d2": dict(empty), "d7": dict(empty)}
    return {"devices": len(first_day),
            "d2": _cohort(first_day, active_days, max_day, 1),
            "d7": _cohort(first_day, active_days, max_day, 6)}


# --------------------------------------------------------------------------- #
# legacy aggregate — counts only; kept for the appendix + the no-change gate
# --------------------------------------------------------------------------- #
class _Aggregate:
    """Per-row accumulator behind aggregate (counts only)."""

    def __init__(self):
        self.by_event: Counter = Counter()
        self.by_day: Counter = Counter()
        self.by_version: Counter = Counter()
        self.by_level: Counter = Counter()
        self.devices: set = set()
        self.errors: dict = defaultdict(lambda: {"fail": 0, "total": 0})

    def add(self, row: dict) -> None:
        event = str(row.get("event") or "(unknown)")
        self.by_event[event] += 1
        self._add_day(row)
        self.by_version[str(row.get("app_version") or "(unset)")] += 1
        dev = row.get("device_id")
        if dev:
            self.devices.add(str(dev))
        props = row.get("props")
        if isinstance(props, dict):
            self._add_props(event, props)

    def _add_day(self, row: dict) -> None:
        ts = str(row.get("inserted_at") or "")
        if len(ts) >= 10:
            self.by_day[ts[:10]] += 1

    def _add_props(self, event: str, props: dict) -> None:
        level = props.get("level")
        if isinstance(level, str) and level:
            self.by_level[level] += 1
        ok = props.get("ok")
        if isinstance(ok, bool):
            self.errors[event]["total"] += 1
            if not ok:
                self.errors[event]["fail"] += 1

    def view(self) -> dict:
        return {
            "total": sum(self.by_event.values()),
            "devices": len(self.devices),
            "by_event": dict(self.by_event),
            "by_day": dict(self.by_day),
            "by_version": dict(self.by_version),
            "by_level": dict(self.by_level),
            "error_rates": {k: dict(v) for k, v in self.errors.items()},
        }


def aggregate(rows: list) -> dict:
    agg = _Aggregate()
    for row in rows:
        if isinstance(row, dict):
            agg.add(row)
    return agg.view()


# --------------------------------------------------------------------------- #
# render — markdown tables
# --------------------------------------------------------------------------- #
def _table(headers: list, rows: list) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _pct(x) -> str:
    return "—" if x is None else f"{x:.1f}%"


def render_funnel(f: dict) -> str:
    rows = []
    for s in f["stages"]:
        rows.append((s["label"], s["devices"], _pct(s["pct_of_install"]),
                     _pct(s["drop_from_prev_pct"])))
    return "\n".join([
        "### 1. Activation funnel",
        "",
        "_Distinct devices reaching each lifecycle stage; later stages imply "
        "the earlier ones. Drop-off is the loss from the stage above._",
        "",
        _table(["stage", "devices", "% of install", "step drop-off"], rows),
    ])


def _ingest_block(pf: dict) -> list:
    ing_rows = []
    for src, v in sorted(pf["ingest"].items()):
        top = v["top_reason"]
        top_str = f"{top[0]} ({top[1]})" if top else "—"
        ing_rows.append((src, v["scans"], v["skips"],
                         _pct(v["skip_rate_pct"]), top_str))
    if not ing_rows:
        return []
    return ["**Ingest paths** — scans vs. skips per source "
            "(top skip reason surfaces `no_credentials` and friends):",
            "",
            _table(["source", "scans", "skips", "skip rate",
                    "top skip reason"], ing_rows),
            ""]


def _dispatch_block(pf: dict) -> list:
    d = pf["dispatch"]
    if not d["total"]:
        return []
    return [f"**Dispatch:** {d['ok']} ok / {d['failed']} failed "
            f"({_pct(d['fail_rate_pct'])} of {d['total']} attempts).", ""]


def _ok_block(pf: dict) -> list:
    ok_rows = [(ev, v["fail"], v["total"], _pct(v["rate_pct"]))
               for ev, v in pf["ok_events"].items()]
    if not ok_rows:
        return []
    return ["**Other action paths** (events carrying an `ok` flag):",
            "",
            _table(["event", "failures", "total", "rate"], ok_rows)]


def render_reliability(pf: dict) -> str:
    parts = ["### 2. Reliability", ""]
    blocks = _ingest_block(pf) + _dispatch_block(pf) + _ok_block(pf)
    if not blocks:
        blocks = ["_No failure-bearing events in this window._"]
    return "\n".join(parts + blocks).rstrip()


def render_abandonment(ab: dict) -> str:
    parts = ["### 3. Feature abandonment", ""]
    if ab["configured"]:
        parts += [
            f"- Configured an ingest source but never reached a first card: "
            f"**{ab['configured_no_card']} of {ab['configured']}** devices "
            f"({_pct(ab['configured_no_card_pct'])}).",
            "",
        ]
    else:
        parts += ["- No `ingest_configured` signal in this window "
                  "(nothing to measure abandonment against).", ""]

    once_rows = sorted(ab["used_once"].items(), key=lambda kv: -kv[1])[:12]
    if once_rows:
        parts += ["**Used exactly once** — devices that touched a path a single "
                  "time (tried-then-dropped candidates):",
                  "",
                  _table(["event", "devices used once"], once_rows)]
    return "\n".join(parts).rstrip()


def render_retention(r: dict) -> str:
    rows = [
        ("day-2 (returned after their first day)",
         r["d2"]["cohort"], r["d2"]["returned"], _pct(r["d2"]["rate_pct"])),
        ("day-7 (still active a week or more later)",
         r["d7"]["cohort"], r["d7"]["returned"], _pct(r["d7"]["rate_pct"])),
    ]
    return "\n".join([
        "### 4. Retention",
        "",
        f"_Of {r['devices']} distinct devices, the fraction that came back "
        "after their first-seen day (by client event time). Cohort = devices "
        "whose first day is old enough to have had the chance to return._",
        "",
        _table(["window", "cohort", "returned", "rate"], rows),
    ])


def render_tables(agg: dict) -> str:
    # NOTE: the **Totals:** line is emitted ONCE by build_body in the main body
    # (the no-change gate greps it via head -n1); the appendix must NOT repeat
    # it, or the report would carry two identical Totals lines.
    parts = [
        "### Events",
        _table(["event", "count"],
               sorted(agg["by_event"].items(), key=lambda kv: -kv[1])),
        "",
        "### Daily volume",
        _table(["day", "events"], sorted(agg["by_day"].items())),
        "",
        "### App versions",
        _table(["version", "events"],
               sorted(agg["by_version"].items(), key=lambda kv: -kv[1])),
    ]
    if agg["by_level"]:
        parts += ["", "### Levels",
                  _table(["level", "events"],
                         sorted(agg["by_level"].items(), key=lambda kv: -kv[1]))]
    err_rows = []
    for event, v in sorted(agg["error_rates"].items()):
        if v["total"]:
            pct = 100.0 * v["fail"] / v["total"]
            err_rows.append((event, v["fail"], v["total"], f"{pct:.1f}%"))
    if err_rows:
        parts += ["", "### Error rates (events carrying an ok flag)",
                  _table(["event", "failures", "total", "rate"], err_rows)]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# optional AI analysis — claude-sonnet-5 over the DERIVED views only
# --------------------------------------------------------------------------- #
ANALYSIS_PROMPT = (
    "You are analyzing anonymous, AGGREGATE-ONLY usage telemetry for 'Zelin's "
    "AI Assistant', an open-source personal AI secretary (screen/inbox ingest "
    "-> approval cards -> autonomous execution). Below are four derived views: "
    "an activation FUNNEL (install -> configure -> first card -> first approval "
    "-> first delivery, with drop-off %), per-path FAILURE rates, feature "
    "ABANDONMENT, and RETENTION.\n\n"
    "Write up to 5 short, concrete recommendations the maintainer can act on "
    "THIS week. Each bullet MUST read exactly: "
    "'**Fix:** <specific change> — because <number copied from the views>. "
    "[confidence: high|medium|low]'. Prioritise the biggest funnel drop-off and "
    "the highest failure rate. Do NOT invent numbers that are not present "
    "below; when a cohort or sample is small, say so and lower the confidence. "
    "Fewer bullets is better than padded ones when the data is thin.\n\n"
)


def _ai_context(f: dict, pf: dict, ab: dict, r: dict) -> str:
    return "\n\n".join([
        render_funnel(f), render_reliability(pf),
        render_abandonment(ab), render_retention(r),
    ])


def analyze(views_md: str, api_key: str,
            opener=None) -> "str | None":
    """Up to 5 fix-recommendations via the Anthropic API. Returns None on ANY
    failure — the report must degrade to views-only, never crash the run."""
    opener = opener or urllib.request.urlopen
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        # roomier budget than the old vanity-count prompt: the funnel + four
        # tables plus per-bullet reasoning need more than the old 1000.
        "max_tokens": 1500,
        # Sonnet 5 runs adaptive thinking when the field is omitted; disable
        # it so the max_tokens budget is all visible text.
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": ANALYSIS_PROMPT + views_md}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with opener(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _message_text(data)
    except Exception as exc:  # noqa: BLE001 — degrade, never fail the report
        print(f"anthropic analysis skipped: {type(exc).__name__}: "
              f"{str(exc)[:160]}", file=sys.stderr)
        return None


def _text_blocks(data: dict) -> list:
    return [b.get("text", "") for b in data.get("content", [])
            if isinstance(b, dict) and b.get("type") == "text"]


def _message_text(data: dict) -> "str | None":
    """The joined text blocks of a messages response; None on refusal / empty."""
    if data.get("stop_reason") == "refusal":
        return None
    joined = "\n".join(t for t in _text_blocks(data) if t).strip()
    return joined or None


# --------------------------------------------------------------------------- #
# body assembly
# --------------------------------------------------------------------------- #
def build_body(agg: "dict | None", insights: "str | None", days: int,
               error: "str | None" = None,
               missing_key: bool = False,
               funnel_v: "dict | None" = None,
               failures_v: "dict | None" = None,
               abandon_v: "dict | None" = None,
               retention_v: "dict | None" = None) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"_Auto-updated by `.github/workflows/insights.yml` — last run "
        f"{now}, window: last {days} days. Aggregates only; no raw rows "
        f"leave the database._",
        "",
    ]
    if missing_key:
        return "\n".join(parts + _MISSING_KEY_LINES)
    if error:
        return "\n".join(parts + _error_lines(error))
    parts += _insights_lines(insights)
    parts += [TENANT_CAVEAT, ""]
    # The **Totals:** line is load-bearing: insights.yml greps it for the
    # no-change gate (it must appear verbatim, N == total event count).
    parts += [f"**Totals:** {agg['total']} events from {agg['devices']} "
              "devices.", ""]
    parts += _view_sections(funnel_v, failures_v, abandon_v, retention_v)
    parts += ["<details>",
              "<summary>Appendix — raw aggregate tables</summary>",
              "",
              render_tables(agg),
              "",
              "</details>"]
    return "\n".join(parts)


_MISSING_KEY_LINES = [
    "## ⚠️ Not configured",
    "",
    "The `SUPABASE_INSIGHTS_KEY` repository secret is missing, so no "
    "data could be read. Set it with a read-capable key:",
    "",
    "```sh",
    "gh secret set SUPABASE_INSIGHTS_KEY -R <owner/repo> < "
    "path/to/service-key.txt",
    "```",
]


def _error_lines(error: str) -> list:
    return [
        "## ⚠️ This run failed",
        "",
        f"Could not read aggregates from Supabase: `{error}`",
        "",
        "The previous report (if any) was replaced by this notice; the "
        "next scheduled run will retry.",
    ]


def _insights_lines(insights: "str | None") -> list:
    if insights:
        return ["## Insights (AI-generated — concrete fixes from the views "
                "below)", "", insights, ""]
    return ["_No AI analysis this run (no `ANTHROPIC_API_KEY` or the "
            "call failed) — the derived views below stand on their own._",
            ""]


def _view_sections(funnel_v, failures_v, abandon_v, retention_v) -> list:
    parts: list = []
    for view, render in ((funnel_v, render_funnel), (failures_v, render_reliability),
                         (abandon_v, render_abandonment), (retention_v, render_retention)):
        if view is not None:
            parts += [render(view), ""]
    return parts


def _env_settings() -> tuple:
    """(days, supabase url, supabase key, anthropic key) from the environment."""
    days = int(os.environ.get("INSIGHTS_DAYS") or 30)
    url = os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    key = (os.environ.get("SUPABASE_INSIGHTS_KEY") or "").strip()
    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return days, url, key, anthropic_key


def _unchanged_since_last(agg: dict) -> bool:
    """Daily no-change gate: the workflow passes the total from the last posted
    report (extracted by regex from the **Totals:** line, validated numeric
    there). Identical total -> no report file at all, so the update step posts
    nothing and the Anthropic call is never made on a quiet day."""
    prev_total = (os.environ.get("INSIGHTS_PREV_TOTAL") or "").strip()
    return prev_total.isdigit() and int(prev_total) == agg["total"]


def _maybe_insights(agg: dict, anthropic_key: str, views: tuple) -> "str | None":
    if anthropic_key and agg["total"] > 0:
        return analyze(_ai_context(*views), anthropic_key)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build the usage-insights issue body")
    ap.add_argument("--out", default="insights-body.md")
    args = ap.parse_args(argv)
    days, url, key, anthropic_key = _env_settings()

    def write(body: str) -> None:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"wrote {args.out} ({len(body)} chars)")

    if not key:
        write(build_body(None, None, days, missing_key=True))
        print("SUPABASE_INSIGHTS_KEY not set — wrote a configuration notice")
        return 0

    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = fetch_rows(url, key, since)
    except (urllib.error.URLError, RuntimeError, OSError, ValueError) as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"fetch failed: {err}", file=sys.stderr)
        write(build_body(None, None, days, error=err))
        return 2

    agg = aggregate(rows)
    print(f"fetched {len(rows)} rows -> {agg['total']} events / "
          f"{agg['devices']} devices")
    if _unchanged_since_last(agg):
        print(f"no new events since last report (total={agg['total']}) "
              "— skipping report and AI analysis")
        return 0

    views = (funnel(rows), path_failures(rows), abandonment(rows), retention(rows))
    insights = _maybe_insights(agg, anthropic_key, views)
    write(build_body(agg, insights, days,
                     funnel_v=views[0], failures_v=views[1],
                     abandon_v=views[2], retention_v=views[3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
