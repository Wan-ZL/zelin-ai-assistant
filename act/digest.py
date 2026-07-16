"""周一 digest (CONTRACT §17/§40.7) — the Monday morning state-of-the-world.

Sections:
  1. 待审批积压   — status=card_sent, with age in days
  2. 待验收       — status=review
  3. 卡住         — executing items that look stuck (resume exhausted, or
                    dispatched >24h ago with no promotion)
  4. 潜在任务     — status=detected (low-confidence backlog)
  5. 双向承诺账本 — registry notes carrying the [MANAGER-OWES] tag
  6. analytics 摘要 — act.report.build_report(days=7) in a folded block
  7. 进化建议     — CONTRACT §16: features unused for 30 days -> 建议关闭;
                    resume-failure storms / high reject ratio -> one-liners.
                    Each suggestion ALSO lands in the registry as a
                    type=self-improvement card (status=detected, i.e. it shows
                    up in 潜在任务 for the owner to raise — never auto-card_sent).

Output (§40.7): a review-lane chat card — same filing pattern as
``act.weekly_digest`` (status=review, delivery_mode=chat, final_draft = the
full markdown, merge_or_new dedup on the per-Monday title so a same-day
re-run refreshes instead of stacking) — plus a notification pointing at the
待验收 lane. No workbench file is written anymore and no filesystem path
rides in the notification (the old ``digests/digest-YYYY-MM-DD.md`` output
was invisible from every app surface). The 1:1 prep page (``act.oneonone``)
is still generated alongside and linked inside the digest text.

Run: ``python -m act.digest --now`` (crontab: Mondays 09:07; without ``--now``
it no-ops unless today is Monday). Feature flag: ``features.digest``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
from typing import Optional

from act import oneonone
from act.lib import analytics, config, failures, notify
from act.lib.registry import Requirement, State, load_all, merge_or_new, save
from act.oneonone import first_seen, lane_name, ledger_empty, ledger_header, promises_owed
from act.report import build_report

# Self-improvement cards target the assistant's own repo (§16: app 更新永远走 PR).
ASSISTANT_REPO = "~/Projects/zelin-ai-assistant"

STUCK_AFTER_HOURS = 24

SOURCE_CHANNEL = "digest"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        ts = _dt.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")
        return ts.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def _age_days(req: Requirement, today: _dt.date) -> Optional[int]:
    d = first_seen(req)
    return (today - d).days if d else None


def _fmt(req: Requirement, today: _dt.date, extra: str = "") -> str:
    age = _age_days(req, today)
    age_s = f"，{age} 天" if age is not None else ""
    extra_s = f" — {extra}" if extra else ""
    # §40 (#19): lane display name, not the raw registry status word
    return (f"- {req.id} · {req.title or '(untitled)'}"
            f"（{lane_name(req.status)}{age_s}）{extra_s}")


def _is_stuck(req: Requirement, now: _dt.datetime) -> Optional[str]:
    """Reason string if an executing item looks stuck, else None."""
    ex = req.execution or {}
    if ex.get("resume_exhausted"):
        return "自动恢复已放弃，需人工"
    if ex.get("last_resume_ok") is False:
        return "上次自动恢复失败"
    dispatched = _parse_iso(ex.get("dispatched_at"))
    if dispatched and (now - dispatched).total_seconds() > STUCK_AFTER_HOURS * 3600:
        hours = int((now - dispatched).total_seconds() // 3600)
        return f"已执行 {hours}h 未交付"
    return None


# --------------------------------------------------------------------------- #
# 进化建议 (§16) — analytics-driven, each suggestion becomes a detected card
# --------------------------------------------------------------------------- #
def _feature_related(feature: str, event: dict) -> bool:
    ev = str(event.get("event", ""))
    src = str(event.get("source", ""))
    if feature == "slack_radar":
        return src == "slack" or ev.startswith("slack")
    if feature == "gmail_radar":
        return src == "gmail" or ev.startswith("gmail")
    if feature == "obsidian_radar":
        return ev == "radar_scan" and src == "obsidian"
    if feature == "digest":
        # write_digest emits both: the 1:1 prep page is generated alongside
        return ev in ("digest_generated", "oneonone_prep")
    if feature == "auto_resume":
        return ev in ("auto_resume", "resume_launch", "auto_resume_exhausted")
    if feature == "analytics":
        return True  # any event at all means analytics is earning its keep
    return True  # unknown feature -> never suggest closing it


def build_suggestions(cfg: config.Config,
                      events: Optional[list[dict]] = None) -> list[tuple[str, str]]:
    """进化建议 from the last 30 days of analytics events.

    Returns ``(stable_title, volatile_detail)`` pairs. The title must NEVER
    embed live counts: it becomes the filed card's title, and merge_or_new
    dedups on title — a count baked in would mint a near-duplicate card every
    Monday the number moves. Counts live in ``detail`` (summary/quote only).
    """
    if events is None:
        since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
        events = list(analytics.read_events(since=since))
    suggestions: list[tuple[str, str]] = []

    # DATA-SUFFICIENCY GUARD: never suggest closing a feature on a fresh install.
    # "zero events" on day 1 means no history, NOT an unused feature. Require the
    # analytics log to span >=14 days AND have a real volume of events before the
    # unused-feature heuristic is trusted (else every feature looks unused).
    all_events = list(analytics.read_events())
    span_days = 0.0
    if len(all_events) >= 2:
        ts0 = analytics.parse_ts(all_events[0].get("ts", ""))
        ts1 = analytics.parse_ts(all_events[-1].get("ts", ""))
        if ts0 and ts1:
            span_days = (ts1 - ts0).total_seconds() / 86400
    enough_history = span_days >= 14 and len(all_events) >= 30

    # a) enabled features with zero related events in 30 days -> 建议关闭
    #    (only once there's enough history to trust "zero" == "unused")
    if enough_history:
        for name in config.DEFAULT_FEATURES:
            if not cfg.feature(name):
                continue  # already off
            if not any(_feature_related(name, e) for e in events):
                suggestions.append(
                    (f"建议关闭功能 {name}（近 30 天零相关事件，白耗资源）", ""))

    # b) auto-resume failure storm
    resume_fails = sum(
        1 for e in events
        if e.get("event") in ("auto_resume", "resume_launch")
        and e.get("ok") is False
    )
    if resume_fails > 10:
        suggestions.append((
            "建议修自动恢复（近 30 天失败频繁）",
            f"近 30 天失败 {resume_fails} 次（无效重复的头号来源）",
        ))

    # c) high reject ratio
    n_rej = sum(1 for e in events if e.get("event") == "inbox_reject")
    n_appr = sum(1 for e in events if e.get("event") == "inbox_approve")
    if (n_rej + n_appr) > 0 and n_rej / (n_rej + n_appr) > 0.5:
        suggestions.append((
            "建议改进提案质量（拒绝率超过 50%）",
            f"拒绝率 {n_rej}/{n_rej + n_appr}（先看卡片 summary 是否说人话）",
        ))
    return suggestions


def file_suggestion_cards(suggestions: list[tuple[str, str]],
                          today: Optional[_dt.date] = None) -> list[Requirement]:
    """Land each suggestion in the registry as a self-improvement card.

    status=detected (NOT card_sent) — they show up in 潜在任务 for the owner
    to raise. ``merge_or_new`` dedups on title, so repeat Mondays don't stack
    duplicates — which is why the volatile detail (live counts) stays out of
    the title and only lands in summary/quote.
    """
    today = today or _dt.date.today()
    filed: list[Requirement] = []
    for title, detail in suggestions:
        req = Requirement(
            id="",  # merge_or_new assigns
            title=title,
            type="self-improvement",
            tier="T1",
            status=State.DETECTED.value,
            hardness="soft",
            summary=f"建议：{title}" + (f" — {detail}" if detail else ""),
            target_repo=ASSISTANT_REPO,
            sources=[{
                "channel": "analytics",
                "date": today.isoformat(),
                "ref": "act.digest 进化建议",
                "quote": detail or title,
                "who": "digest",
            }],
        )
        try:
            filed.append(merge_or_new(req, high_confidence=False))
        except Exception:  # noqa: BLE001 — a bad card must not kill the digest
            continue
    return filed


# --------------------------------------------------------------------------- #
# digest assembly
# --------------------------------------------------------------------------- #
def build_digest(today: Optional[_dt.date] = None,
                 oneonone_path: Optional[Path] = None) -> str:
    today = today or _dt.date.today()
    now = _dt.datetime.now(_dt.timezone.utc)
    cfg = config.load_config()

    # 进化建议 first — filing them (status=detected) lets 潜在任务 below include them.
    suggestions = build_suggestions(cfg)
    file_suggestion_cards(suggestions, today)

    reqs = [
        r for r in load_all()
        if not r.is_merged
        and r.status not in (State.TRASHED.value, State.REJECTED.value)
    ]
    card_sent = [r for r in reqs if r.status == State.CARD_SENT.value]
    review = [r for r in reqs if r.status == State.REVIEW.value]
    executing = [r for r in reqs if r.status == State.EXECUTING.value]
    detected = [r for r in reqs if r.status == State.DETECTED.value]
    stuck = [(r, _is_stuck(r, now)) for r in executing]
    stuck = [(r, why) for r, why in stuck if why]

    def section(header: str, lines: list[str], empty: str = "- （无）") -> list[str]:
        return [header] + (lines if lines else [empty]) + [""]

    out: list[str] = [f"# 周一 digest · {today.isoformat()}", ""]
    # one-line overview right under the title — doubles as the review-lane
    # card's summary (_file_digest_card picks the first non-header line).
    out += [failures.pick(
        f"待审批 {len(card_sent)} · 待验收 {len(review)} · 卡住 {len(stuck)}"
        f" · 潜在任务 {len(detected)}",
        f"{len(card_sent)} awaiting approval · {len(review)} in review ·"
        f" {len(stuck)} stuck · {len(detected)} in backlog"), ""]
    out += section(f"## 📨 待审批积压（{len(card_sent)}）",
                   [_fmt(r, today) for r in card_sent])
    out += section(f"## 🔍 待验收（{len(review)}）",
                   [_fmt(r, today) for r in review])
    out += section(f"## 🧱 卡住（{len(stuck)}）",
                   [_fmt(r, today, extra=why) for r, why in stuck])
    out += section(f"## 📡 潜在任务（{len(detected)}）",
                   [_fmt(r, today) for r in detected])
    out += section(ledger_header(cfg), promises_owed(reqs), empty=ledger_empty())

    if oneonone_path is not None:
        out += ["## 🗓 1:1 准备页", f"- [{oneonone_path.name}]({oneonone_path})", ""]

    out += section("## 💡 进化建议（已作为 self-improvement 卡片进入潜在任务）",
                   [f"- {t} — {d}" if d else f"- {t}" for t, d in suggestions],
                   empty="- （无 —— 各功能都在被用，健康）")

    try:
        report = build_report(days=7)
    except Exception:  # noqa: BLE001 — report failure must not kill the digest
        report = "(analytics 报告生成失败)"
    out += ["<details>", "<summary>📊 analytics 摘要（近 7 天）</summary>", "",
            "```", report, "```", "", "</details>", ""]

    return "\n".join(out)


def _file_digest_card(md: str, today: _dt.date) -> Requirement:
    """Digest -> review-lane chat card (§40.7) — the same filing pattern as
    act/weekly_digest: status=review, delivery_mode=chat, final_draft = the
    full markdown, delivered_summary = its head. merge_or_new dedups on the
    per-Monday title, so a same-day re-run refreshes the existing card
    instead of stacking a duplicate."""
    title = failures.pick(f"周一 digest · {today.isoformat()}",
                          f"Monday digest · {today.isoformat()}")
    first_line = next((ln.strip() for ln in md.splitlines()
                       if ln.strip() and not ln.strip().startswith("#")),
                      title)
    req = Requirement(
        id="",  # merge_or_new assigns
        title=title,
        type="digest",
        tier="T0",
        status=State.REVIEW.value,
        hardness="soft",
        summary=first_line[:160],
        delivery_mode="chat",
        sources=[{
            "channel": SOURCE_CHANNEL,
            "date": today.isoformat(),
            "ref": "act.digest",
            "quote": failures.pick("周一状态盘点", "Monday state of the world"),
            "who": "assistant",
        }],
    )
    filed = merge_or_new(req, high_confidence=False)
    ex = dict(filed.execution or {})
    ex["review_at"] = _iso_now()
    ex["delivered_summary"] = md[:500]
    ex["final_draft"] = md[:20000]
    filed.execution = ex
    filed.summary = first_line[:160]
    if filed.status != State.TRASHED.value:
        filed.set_status(State.REVIEW)
    save(filed)
    return filed


def publish_digest(today: Optional[_dt.date] = None) -> Requirement:
    """Generate the 1:1 prep page + the digest, land the digest as a
    review-lane chat card (§40.7 — no workbench file, no path in the
    notification), notify. Returns the filed card."""
    today = today or _dt.date.today()

    prep_path: Optional[Path] = None
    try:
        prep_path = oneonone.write_prep(today)
    except Exception:  # noqa: BLE001 — prep failure must not block the digest
        prep_path = None

    md = build_digest(today, oneonone_path=prep_path)
    card = _file_digest_card(md, today)

    # §5 v0.14：python 侧全部通知经 failures.pick 走 UI 语言，body 必带下一步
    notify.notify(
        failures.pick("周一 digest 已生成", "Monday digest ready"),
        failures.pick(
            "去「待验收」看这周的盘点；进化建议在潜在任务列。",
            "Open the Review lane for this week's state of the world; "
            "suggestions sit in the backlog.",
        ),
    )
    analytics.log_event("digest_generated", req=card.id)
    return card


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="digest", description="Monday digest")
    ap.add_argument("--now", action="store_true",
                    help="generate immediately regardless of weekday")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    if not cfg.feature("digest"):
        print("features.digest is off — no-op")
        return 0
    if not args.now and _dt.date.today().weekday() != 0:
        print("today is not Monday — skipping (use --now to force)")
        return 0

    card = publish_digest()
    print(f"{card.id} (review lane)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
