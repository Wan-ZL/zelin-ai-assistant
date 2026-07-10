"""Usage-insights report builder for the pinned GitHub issue (insights.yml).

Machine-independent telemetry loop: runs monthly in GitHub Actions, pulls the
last N days of ``analytics_events`` rows from the telemetry Supabase project
(docs/TELEMETRY.md) and reduces them to AGGREGATES ONLY — counts by event /
day / app version / level and per-event error rates. No raw rows, no props
payloads, and no device ids ever appear in the output (devices show up only
as a distinct COUNT).

If ``ANTHROPIC_API_KEY`` is set, the aggregates are additionally summarized
into 3-5 insights (claude-sonnet-5, small max_tokens) with confidence labels;
without the key the issue simply carries the raw aggregate tables.

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
        + "?select=device_id,event,app_version,inserted_at,props"
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
# aggregate — counts only; nothing row-level survives this function
# --------------------------------------------------------------------------- #
def aggregate(rows: list) -> dict:
    by_event: Counter = Counter()
    by_day: Counter = Counter()
    by_version: Counter = Counter()
    by_level: Counter = Counter()
    devices: set = set()
    errors: dict = defaultdict(lambda: {"fail": 0, "total": 0})

    for row in rows:
        if not isinstance(row, dict):
            continue
        event = str(row.get("event") or "(unknown)")
        by_event[event] += 1
        ts = str(row.get("inserted_at") or "")
        if len(ts) >= 10:
            by_day[ts[:10]] += 1
        by_version[str(row.get("app_version") or "(unset)")] += 1
        dev = row.get("device_id")
        if dev:
            devices.add(str(dev))
        props = row.get("props")
        if isinstance(props, dict):
            level = props.get("level")
            if isinstance(level, str) and level:
                by_level[level] += 1
            ok = props.get("ok")
            if isinstance(ok, bool):
                errors[event]["total"] += 1
                if not ok:
                    errors[event]["fail"] += 1

    return {
        "total": sum(by_event.values()),
        "devices": len(devices),
        "by_event": dict(by_event),
        "by_day": dict(by_day),
        "by_version": dict(by_version),
        "by_level": dict(by_level),
        "error_rates": {k: dict(v) for k, v in errors.items()},
    }


# --------------------------------------------------------------------------- #
# render — markdown tables
# --------------------------------------------------------------------------- #
def _table(headers: list, rows: list) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_tables(agg: dict) -> str:
    parts = [
        f"**Totals:** {agg['total']} events from {agg['devices']} devices.",
        "",
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
# optional AI analysis — claude-sonnet-5 over the AGGREGATE tables only
# --------------------------------------------------------------------------- #
ANALYSIS_PROMPT = (
    "You are analyzing anonymous usage telemetry AGGREGATES (counts only) for "
    "'Zelin's AI Assistant', an open-source personal AI secretary "
    "(screen-capture ingest -> Obsidian wiki, requirement radars -> approval "
    "cards -> autonomous execution). Based ONLY on the tables below, write "
    "3-5 short insights a maintainer can act on (adoption trends, feature "
    "usage imbalance, reliability problems, version rollout health). One "
    "bullet each, markdown, ending with a confidence label: "
    "[confidence: high|medium|low]. Do not invent numbers that are not "
    "derivable from the tables; if the data is too thin to say much, say so "
    "honestly in fewer bullets.\n\n"
)


def analyze(tables_md: str, api_key: str,
            opener=None) -> "str | None":
    """3-5 insights via the Anthropic API. Returns None on ANY failure —
    the report must degrade to tables-only, never crash the run."""
    opener = opener or urllib.request.urlopen
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1000,
        # Sonnet 5 runs adaptive thinking when the field is omitted; disable
        # it so the small max_tokens budget is all visible text.
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": ANALYSIS_PROMPT + tables_md}],
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
        if data.get("stop_reason") == "refusal":
            return None
        texts = [b.get("text", "") for b in data.get("content", [])
                 if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(t for t in texts if t).strip()
        return joined or None
    except Exception as exc:  # noqa: BLE001 — degrade, never fail the report
        print(f"anthropic analysis skipped: {type(exc).__name__}: "
              f"{str(exc)[:160]}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# body assembly
# --------------------------------------------------------------------------- #
def build_body(agg: "dict | None", insights: "str | None", days: int,
               error: "str | None" = None,
               missing_key: bool = False) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"_Auto-updated by `.github/workflows/insights.yml` — last run "
        f"{now}, window: last {days} days. Aggregates only; no raw rows "
        f"leave the database._",
        "",
    ]
    if missing_key:
        parts += [
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
        return "\n".join(parts)
    if error:
        parts += [
            "## ⚠️ This run failed",
            "",
            f"Could not read aggregates from Supabase: `{error}`",
            "",
            "The previous report (if any) was replaced by this notice; the "
            "next scheduled run will retry.",
        ]
        return "\n".join(parts)

    if insights:
        parts += ["## Insights (AI-generated from the aggregates below)",
                  "", insights, ""]
    else:
        parts += ["_No AI analysis this run (no `ANTHROPIC_API_KEY` or the "
                  "call failed) — raw aggregate tables below._", ""]
    parts += ["## Aggregates", "", render_tables(agg)]
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build the usage-insights issue body")
    ap.add_argument("--out", default="insights-body.md")
    args = ap.parse_args(argv)

    days = int(os.environ.get("INSIGHTS_DAYS") or 30)
    url = os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    key = (os.environ.get("SUPABASE_INSIGHTS_KEY") or "").strip()
    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()

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

    insights = None
    if anthropic_key and agg["total"] > 0:
        insights = analyze(render_tables(agg), anthropic_key)
    write(build_body(agg, insights, days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
