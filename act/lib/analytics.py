"""Usage analytics — append-only event log for every feature use.

One JSONL line per event in ``state/analytics/events.jsonl``:
    {"ts": "2026-07-06T23:01:02Z", "event": "inbox_approve", "req": "R-004", ...}

``python -m act.report`` aggregates: per-feature frequency (7d/30d), hour-of-day
and day-of-week heat, health signals (rework rate = unclear proposals, resume
failures = ineffective repetition, approval latency), and repetition storms.

Analytics must NEVER break the pipeline — every failure here is swallowed.
"""
from __future__ import annotations

import datetime as _dt
import json
import re as _re
from pathlib import Path
from typing import Iterator, Optional

from act import __version__
from act.lib import config

ANALYTICS_DIR: Path = config.STATE_DIR / "analytics"
EVENTS_PATH: Path = ANALYTICS_DIR / "events.jsonl"
# Once-per-install milestone markers (docs/TELEMETRY.md 生命周期里程碑): one
# empty file per milestone name under here suppresses every later log_first for
# that milestone. Python counterpart of the Swift Analytics.firstReach marker
# (mac/Sources/Utils.swift), which uses a UserDefaults flag for the same job.
FIRST_DIR: Path = ANALYTICS_DIR / "first"

# Hard cap for every user-typed content field (docs/TELEMETRY.md「输入文本
# 收集」): capture text / Ask questions / card comments / instruction
# summaries all pass through clip(text, CONTENT_CLIP). Model OUTPUT and
# ingested third-party content (screen OCR / emails / Slack
# messages, tests/test_telemetry_level.py boundary guard) are never captured
# at any setting — only what the user typed into this app.
CONTENT_CLIP: int = 500

# v2 consent-surface marker (CONTRACT §15 v0.18): written by the app the
# first time the NEW disclosure (the one that says typed text is included)
# renders. Pre-v0.18 installs only have the old telemetry_consent_shown
# marker, written when the copy still said "no personal text" — their
# behavior telemetry keeps flowing on that old marker, but CONTENT stays off
# until the new disclosure has been seen (or capture_input is set
# explicitly, which is its own informed choice).
CONSENT_V2_PATH: Path = config.STATE_DIR / "telemetry_consent_shown_v2"


def content_gate(cfg=None) -> bool:
    """Emit-side gate for user-typed content fields (docs/TELEMETRY.md).

    ALL required:
    1. telemetry.capture_input on AND level "detailed"
       (Config.capture_input_active — both default ON since v0.18);
    2. consent: the v2 disclosure marker exists, OR capture_input was set
       EXPLICITLY (config.yaml / overrides — writing the key is an informed
       choice; upgraded installs that never saw the new copy have neither,
       so their content stays off while behavior telemetry continues);
    3. nothing crashed — any failure means False (fail closed).

    Only text the user typed into THIS app may sit behind this gate — never
    pipeline/ingested content. Loads config lazily so no-cfg call sites
    (actd inbox helpers) can use it.
    """
    try:
        cfg = cfg or config.load_config()
        if not cfg.capture_input_active():
            return False
        if getattr(cfg, "telemetry_capture_input_explicit", False):
            return True
        return CONSENT_V2_PATH.exists()
    except Exception:  # noqa: BLE001 - fail closed, never break the pipeline
        return False


def _secret_positions(s: str, patterns) -> set:
    """Index set of every character of ``s`` that is secret material.

    两遍扫描：先扫原串；再把空白拼掉扫一遍并映射回原串下标——邮件式换行/
    空格会把 key 劈成两段，只有拼合后才能看出整条是密钥素材（§15 承诺任何
    设置下都不收集 key，劈开的尾段也是）。拼合可能把紧邻 key 的词也圈进来
    （无法与折行区分），宁可多掩不可半漏（fail safe）。
    """
    positions: set = set()
    for pat in patterns:
        for m in pat.finditer(s):
            positions.update(range(m.start(), m.end()))
    idx_map = [i for i, ch in enumerate(s) if ch != " "]
    compact = "".join(ch for ch in s if ch != " ")
    for pat in patterns:
        for m in pat.finditer(compact):
            positions.update(idx_map[j] for j in range(m.start(), m.end()))
    return positions


def clip_content(text) -> Optional[str]:
    """clip() for user-typed CONTENT fields: secret-mask FIRST, then cap at
    CONTENT_CLIP. The masking (act/lib/sanitize._SECRET_PATTERNS) is
    UNCONDITIONAL — independent of every redaction.* switch — because the
    docs promise keys never ride in telemetry at any setting (the Swift
    writer mirrors the same patterns in Analytics.clip). Fail closed: if
    masking itself breaks, the content is dropped, never sent raw.
    """
    s = " ".join(str(text or "").split())
    if not s:
        return None
    try:
        from act.lib import sanitize  # lazy: keep analytics import-light
        positions = _secret_positions(s, sanitize._SECRET_PATTERNS)
        if positions:
            # 每段连续的密钥区间折叠成一个 MASK；夹在两段掩码之间的折行
            # 空格一并吞掉（它只是被 split 归一出来的换行痕迹）
            out: list = []
            i = 0
            while i < len(s):
                if i in positions:
                    out.append(sanitize.MASK)
                    while i < len(s):
                        if i in positions:
                            i += 1
                        elif s[i] == " " and (i + 1) in positions:
                            i += 1
                        else:
                            break
                else:
                    out.append(s[i])
                    i += 1
            s = "".join(out)
    except Exception:  # noqa: BLE001 - never emit unmasked content
        return None
    return s[:CONTENT_CLIP] or None


def log_event(event: str, **fields) -> None:
    """Append one event. Non-None fields only. Never raises."""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": str(event),
            # writer-level version stamp (docs/TELEMETRY.md): every python
            # event carries "v", mirroring the Swift writer — no emitter can
            # forget it, so app_version is never "(unset)" on upload.
            "v": __version__,
        }
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - analytics must never break the pipeline
        pass


_MARKER_SAFE = _re.compile(r"[^A-Za-z0-9_.-]+")


def log_first(event: str, **fields) -> None:
    """Emit ``event`` at most once per install (lifecycle milestone).

    A persistent empty marker under ``state/analytics/first/<event>`` records
    that the milestone already fired; every later call is a no-op. ``fields``
    are behavior-only metadata (req ids, counts) exactly like :func:`log_event`
    — NEVER card content — so this fits the existing content_gate/privacy scope
    without touching it.

    Emit-then-mark: the event is logged first and the marker written after, so a
    crash in between at worst double-emits. That is harmless because every
    consumer of these milestones (scripts/insights_report.py) counts DISTINCT
    devices, and multiple processes (radar cron vs. actd) racing the check can
    likewise only cause a few harmless duplicates. Never raises.
    """
    try:
        name = _MARKER_SAFE.sub("_", str(event)).strip("._") or "event"
        marker = FIRST_DIR / name
        if marker.exists():
            return
        log_event(event, **fields)
        FIRST_DIR.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except Exception:  # noqa: BLE001 - analytics must never break the pipeline
        pass


def clip(text, limit: int = 200) -> Optional[str]:
    """Whitespace-collapsed, truncated string for telemetry payload fields
    (docs/TELEMETRY.md; content fields use limit=CONTENT_CLIP) — None when
    empty so log_event drops it.
    """
    s = " ".join(str(text or "").split())
    return s[:limit] or None


def parse_ts(s: str) -> Optional[_dt.datetime]:
    """Parse an event 'ts' (UTC) -> aware datetime, or None."""
    try:
        return _dt.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def read_events(since: Optional[_dt.datetime] = None) -> Iterator[dict]:
    """Yield parsed events (optionally only those newer than ``since``, UTC)."""
    try:
        fh = open(EVENTS_PATH, encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None:
                try:
                    ts = _dt.datetime.strptime(d.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")
                    ts = ts.replace(tzinfo=_dt.timezone.utc)
                except ValueError:
                    continue
                if ts < since:
                    continue
            yield d
