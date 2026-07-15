"""1:1 prep page — Manager pack ② (CONTRACT §17).

Builds a one-line-per-item snapshot of the registry for Zelin's 1:1 with
the manager, grouped by readiness:

  - ready      : status=review（待验收） + delivered within the last 7 days
  - in-flight  : card_sent / approved / executing
  - not-ready  : detected（欠账/低置信度）

plus the 双向承诺账本 — every ``[MANAGER-OWES]`` line found in registry notes.

Output: ``<execution.default_target_repo>/oneonone/prep-YYYY-MM-DD.md``, or
``state/oneonone/`` while no target repo has been configured.
Run standalone: ``python -m act.oneonone`` (prints the written path).
The Monday digest (``act.digest``) generates and links this page automatically.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Optional

from act.lib import analytics, config
from act.lib.registry import Requirement, State, load_all


def output_root() -> Path:
    """Root for generated pages (digest + 1:1 prep), resolved at call time.

    Honors ``execution.default_target_repo``; when it was never explicitly
    configured, falls back to STATE_DIR so the literal example placeholder
    path (~/Projects/your-workbench) is never created on the user's disk
    (config.default_target_repo_configured).
    """
    cfg = config.load_config()
    if cfg.default_target_repo_configured:
        return cfg.target_repo_path
    return config.STATE_DIR

_STATUS_ICON = {
    State.DETECTED.value: "📡",
    State.CARD_SENT.value: "📨",
    State.APPROVED.value: "👍",
    State.EXECUTING.value: "🏃",
    State.REVIEW.value: "🔍",
    State.DELIVERED.value: "✅",
}

_MANAGER_OWES_TAG = "[MANAGER-OWES]"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# --------------------------------------------------------------------------- #
# dates / age
# --------------------------------------------------------------------------- #
def _parse_date(s) -> Optional[_dt.date]:
    if not s:
        return None
    m = _DATE_RE.search(str(s))
    if not m:
        return None
    try:
        return _dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def first_seen(req: Requirement) -> Optional[_dt.date]:
    """Earliest date we can attribute to this requirement (source date, card
    sent_at, dispatch time — whichever is oldest)."""
    dates = []
    for s in req.sources or []:
        if isinstance(s, dict):
            d = _parse_date(s.get("date"))
            if d:
                dates.append(d)
    d = _parse_date((req.card or {}).get("sent_at"))
    if d:
        dates.append(d)
    d = _parse_date((req.execution or {}).get("dispatched_at"))
    if d:
        dates.append(d)
    return min(dates) if dates else None


def last_activity(req: Requirement) -> Optional[_dt.date]:
    """Latest date we can attribute to this requirement."""
    dates = []
    ex = req.execution or {}
    for key in ("dispatched_at", "last_resume_at", "last_rework_at"):
        d = _parse_date(ex.get(key))
        if d:
            dates.append(d)
    d = _parse_date((req.card or {}).get("sent_at"))
    if d:
        dates.append(d)
    for s in req.sources or []:
        if isinstance(s, dict):
            d = _parse_date(s.get("date"))
            if d:
                dates.append(d)
    return max(dates) if dates else None


def _age_str(req: Requirement, today: _dt.date) -> str:
    d = first_seen(req)
    if d is None:
        return ""
    days = (today - d).days
    return f"{days} 天" if days >= 0 else ""


def _line(req: Requirement, today: _dt.date) -> str:
    icon = _STATUS_ICON.get(req.status, "•")
    age = _age_str(req, today)
    tail = f"（{req.status}，{age}）" if age else f"（{req.status}）"
    return f"- {icon} {req.id} · {req.title or '(untitled)'} {tail}"


# --------------------------------------------------------------------------- #
# 双向承诺账本 — [MANAGER-OWES] lines in registry notes
# --------------------------------------------------------------------------- #
def manager_owes(reqs: list[Requirement]) -> list[str]:
    """Every note line carrying the [MANAGER-OWES] tag, prefixed with its req id."""
    out: list[str] = []
    for r in reqs:
        for line in (r.notes or "").splitlines():
            if _MANAGER_OWES_TAG.lower() in line.lower():
                out.append(f"- {r.id} · {line.strip()}")
    return out


# --------------------------------------------------------------------------- #
# build + write
# --------------------------------------------------------------------------- #
def build_prep(today: Optional[_dt.date] = None) -> str:
    today = today or _dt.date.today()
    reqs = [
        r for r in load_all()
        if not r.is_merged
        and r.status not in (State.TRASHED.value, State.REJECTED.value)
    ]

    ready: list[Requirement] = []
    in_flight: list[Requirement] = []
    not_ready: list[Requirement] = []
    for r in reqs:
        if r.status == State.REVIEW.value:
            ready.append(r)
        elif r.status == State.DELIVERED.value:
            la = last_activity(r)
            if la is None or (today - la).days <= 7:
                ready.append(r)  # delivered this week (best-effort dating)
        elif r.status in (State.CARD_SENT.value, State.APPROVED.value,
                          State.EXECUTING.value):
            in_flight.append(r)
        elif r.status == State.DETECTED.value:
            not_ready.append(r)

    def section(header: str, lines: list[str], empty: str = "- （无）") -> list[str]:
        return [header] + (lines if lines else [empty]) + [""]

    out: list[str] = [f"# 1:1 prep · {today.isoformat()}", ""]
    out += section(f"## ✅ Ready（可汇报：待验收 + 本周交付，{len(ready)}）",
                   [_line(r, today) for r in ready])
    out += section(f"## 🏃 In-flight（进行中，{len(in_flight)}）",
                   [_line(r, today) for r in in_flight])
    out += section(f"## 📡 Not ready（欠账/未确认，{len(not_ready)}）",
                   [_line(r, today) for r in not_ready])
    out += section("## 🤝 双向承诺账本（manager 欠的）", manager_owes(reqs),
                   empty="- （无 —— notes 里用 [MANAGER-OWES] 标记他的承诺）")
    return "\n".join(out)


def write_prep(today: Optional[_dt.date] = None) -> Path:
    """Build + write the prep page; returns the written path."""
    today = today or _dt.date.today()
    out_dir = output_root() / "oneonone"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"prep-{today.isoformat()}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_prep(today))
    analytics.log_event("oneonone_prep", path=str(path))
    return path


def main(argv: Optional[list[str]] = None) -> int:
    path = write_prep()
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
