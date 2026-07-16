"""Native notifications + transition classifiers (CONTRACT §5, §28).

State transitions surfaced as native notifications:
  - new card_sent (radar found a new requirement)  -> "有新需求待审批：<title>"
  - executing -> done                              -> "任务完成：<title>"
  - executing -> blocked (needs_input)             -> "任务需要你输入：<title>"
  - credential failure (log has auth/login words)  -> "需要重新登录：<service>"

§28 (app identity relay): on darwin the native path never fires osascript —
it queues one JSON file into state/notify_queue/ for the menu-bar app, which
posts it via UNUserNotificationCenter (proper "Zelin's AI Assistant"
identity/icon instead of Script Editor) and deletes the file. There is NO
fallback by owner decision (2026-07-10): app closed = no native notification
(the app auto-starts at login, so running is the normal state).

v0.21 removed the phone mirror (iMessage transport + Slack self-DM
notification/approval): the Mac app is now the sole approval surface. Slack
self-DM remains a one-way quick-capture inbox (see act/radar_slack.py) — the
assistant no longer posts anything back into it.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from act.lib import platform


# --------------------------------------------------------------------------- #
# raw notification
# --------------------------------------------------------------------------- #
def notify(title: str, body: str, subtitle: Optional[str] = None,
           req: Optional[str] = None) -> bool:
    """Fire a native notification via the app relay queue (§28).

    Never raises — a failed notification must not break the daemon loop.
    ``req`` (an R-xxx id, optional) is accepted for caller compatibility but is
    no longer used (v0.21 removed the phone mirror / reaction-approval surface).
    """
    return _native_notify(title, body, subtitle)


# --------------------------------------------------------------------------- #
# app relay queue (§28) — native notifications carry the app's identity
# --------------------------------------------------------------------------- #
# The menu-bar app drains state/notify_queue/*.json on its 5 s refresh tick and
# posts each entry via UNUserNotificationCenter, then deletes the file. No
# osascript fallback, by owner decision: app closed = no native notification
# (Script Editor identity is exactly what this replaces, and the app
# auto-starts at login). Writers sweep entries older than STALE_AFTER_S so the
# queue can't grow unboundedly when the app never runs.
STALE_AFTER_S = 600.0   # §28 stale storm guard (both sides, 10 min)


def _native_notify(title: str, body: str, subtitle: Optional[str] = None) -> bool:
    """§28 relay-only native notification. Never raises.

    darwin: queue for the app — its 5 s tick posts and deletes the entry;
    nothing is posted while the app is closed (no fallback, on purpose).
    Other OSes keep the plain OS seam (notify-send) — the relay exists only
    because the darwin app owns the notification identity.
    """
    if not platform.is_darwin():
        return platform.notify_user(title, body, subtitle)
    return _queue_write(title, body, subtitle) is not None


def _queue_write(title: str, body: str, subtitle: Optional[str] = None,
                 now: Optional[float] = None) -> Optional[Path]:
    """Write one §28 queue entry (atomic .json.tmp + rename).

    Sweeps stale siblings (mtime older than STALE_AFTER_S) first, so an
    always-closed app can't make the dir grow without bound. Returns the
    queue file path, or None on ANY failure. ``now`` is the injectable clock.
    """
    try:
        from act.lib import config as _config
        qdir = Path(_config.NOTIFY_QUEUE_DIR)
        qdir.mkdir(parents=True, exist_ok=True)
        _sweep_stale(qdir, now=now)
        nid = uuid.uuid4().hex
        entry = {"id": nid, "title": str(title), "body": str(body),
                 "created_at": int(now if now is not None else time.time())}
        if subtitle:
            entry["subtitle"] = str(subtitle)
        target = qdir / (nid + ".json")
        tmp = qdir / (nid + ".json.tmp")   # the app only ever matches *.json
        try:
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)    # no corpse on a failed rename
        return target
    except Exception:  # noqa: BLE001 - a notification must never break a caller
        return None


def _sweep_stale(qdir: Path, now: Optional[float] = None) -> int:
    """Delete queue entries (and tmp corpses) older than STALE_AFTER_S.

    Best-effort, never raises; returns how many files were removed. Losing a
    race with the app deleting the same file is fine (missing_ok).
    """
    removed = 0
    cutoff = (now if now is not None else time.time()) - STALE_AFTER_S
    try:
        for f in qdir.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue   # raced with the app's own delete
    except OSError:
        pass
    return removed


# --------------------------------------------------------------------------- #
# message builders (CONTRACT §5 copy — v0.14: bilingual per the UI language
# setting, and every message names the user's next step; act/lib/failures.pick
# is the single language switch for ALL python-originated user-facing copy)
# --------------------------------------------------------------------------- #
def _pick(zh: str, en: str) -> str:
    from act.lib import failures
    return failures.pick(zh, en)


def msg_new_card(title: str) -> tuple[str, str]:
    return (_pick("有新需求待审批", "New card awaiting approval"),
            _pick(f"{title} —— 打开菜单栏面板，✅ 批准或 ❌ 拒绝",
                  f"{title} — open the menu-bar panel: ✅ approve or ❌ reject"))


def msg_new_cards_batch(n: int) -> tuple[str, str]:
    """§40: >2 fresh proposals in one actd pass collapse to ONE notification
    (a radar backfill was previously n pings in a row). 需输入/回锅/失败
    classes stay per-card — those each demand a distinct decision."""
    return (_pick(f"雷达新增 {n} 张待审批卡", f"{n} new cards awaiting approval"),
            _pick("打开菜单栏面板逐张审批（✅ 批准 / ❌ 拒绝）",
                  "Open the menu-bar panel to review them (✅ approve / ❌ reject)"))


def msg_done(title: str) -> tuple[str, str]:
    return (_pick("任务完成", "Task finished"),
            _pick(f"{title} —— 打开 App 验收或打回",
                  f"{title} — open the app to accept or send back"))


def msg_needs_input(title: str) -> tuple[str, str]:
    return (_pick("任务需要你输入", "A task needs your input"),
            _pick(f"{title} —— 打开 App 的「需输入」列查看它在等什么",
                  f"{title} — open the app's Needs-input column to see what it's waiting for"))


def msg_auth(service: str) -> tuple[str, str]:
    return (_pick("需要重新登录", "Login needed again"),
            _pick(f"{service} —— 打开 App 设置页重新粘贴对应的 key/密码",
                  f"{service} — open the app's Settings and re-paste the key/password"))


def msg_reraised(title: str, note: str = "") -> tuple[str, str]:
    """re-raise -> card_sent (v0.20.0 §5「回锅」): a card the user already
    accepted came back with new actionable info and is a proposal again."""
    extra = f"：{note}" if note else ""
    return (_pick("回锅：你验收过的事来了新信息", "Returned: new info on an accepted task"),
            _pick(f"{title}{extra} —— 打开菜单栏面板重新审批（✅ 批准 / ❌ 拒绝）",
                  f"{title}{extra} — open the menu-bar panel to re-approve (✅ / ❌)"))


def msg_review_ready(title: str) -> tuple[str, str]:
    """executing -> review: the draft is ready for Zelin's ✓/↩︎."""
    return (_pick("待验收：AI 已交付草稿", "Ready for review: draft delivered"),
            _pick(f"{title} —— 打开 App 的「待验收」列验收或打回",
                  f"{title} — open the app's Review column to accept or send back"))


def msg_dispatch_failed(title: str, reason: Optional[str] = None) -> tuple[str, str]:
    """dispatch launch failed; actd auto-retries with backoff (P0-6).

    ``reason`` = the §25 plain-language sentence when the error classified
    (failures.user_message) — without it the notification said nothing usable
    (2026-07-08: an outdated claude retried for hours behind「任务派发失败」)."""
    if reason:
        return (_pick("任务派发失败（会自动重试）", "Task launch failed (will auto-retry)"),
                _pick(f"{title}：{reason}", f"{title}: {reason}"))
    return (_pick("任务派发失败（会自动重试）", "Task launch failed (will auto-retry)"),
            _pick(f"{title} —— 一直失败的话，打开 App 排队卡片上的错误提示按对应按钮修",
                  f"{title} — if it keeps failing, open the app: the queued card"
                  " shows the error with a fix button"))


def msg_resuming(title: str) -> tuple[str, str]:
    return (_pick("任务疑似中断，正在自动恢复", "Task looks interrupted — auto-recovering"),
            _pick(f"{title} —— 无需操作；持续失败会另行通知",
                  f"{title} — nothing to do; you'll be notified if it keeps failing"))


def msg_auto_resume_exhausted(title: str) -> tuple[str, str]:
    """5 straight resume failures — actd gives up; name the exact buttons.

    v0.21 起运行中卡只有一个「停止/Stop」→ 二选一对话框（退回提案 / 去待验收），
    文案必须指向现存按钮（审计：旧文案引用已删除的「停止并退回」「已办完」）。"""
    return (_pick("自动恢复已放弃（连续失败 5 次）",
                  "Auto-recovery gave up (5 straight failures)"),
            _pick(f"{title} —— 打开 App，在「运行中」列对这张卡点「停止」，"
                  "选「退回提案」重新批准，或选「去待验收」留下已产出的结果由你验收",
                  f"{title} — open the app: on this card in Running, press"
                  " \"Stop\", then \"Discard & re-propose\" to re-approve or"
                  " \"Keep for review\" to keep what it produced for your check"))


# --------------------------------------------------------------------------- #
# classifiers
# --------------------------------------------------------------------------- #
# High-precision credential-failure signatures only, aligned with
# act/lib/failures.py claude_auth_failed. Generic single words (auth / login /
# credentials) are excluded on purpose: they matched repo paths like
# ~/Projects/auth-service in the launch log and fabricated a "需要重新登录"
# notification right after a successful dispatch.
_AUTH_RE = re.compile(
    r"authentication_error|invalid (x-)?api[- _]?key|"
    r"\b401\b|OAuth token has expired|(?<![\w-])unauthorized|"
    r"please run /login|api key.{0,20}(invalid|expired|revoked)|"
    r"session expired|invalid[_ -]?token|please sign in",
    re.IGNORECASE,
)

# The dispatch launch log's fixed header (act/executor.py) embeds the target
# path — "# dispatch R-x @ ..." / "# cwd=<target>". Never classify it: the
# path is user data, not an error message.
_LOG_HEADER_RE = re.compile(r"^# (dispatch\b|cwd=).*$", re.MULTILINE)


def detect_auth_failure(log_text: str) -> bool:
    """True if an execution log looks like a credential/login failure."""
    if not log_text:
        return False
    return bool(_AUTH_RE.search(_LOG_HEADER_RE.sub("", log_text)))


def notify_new_card(title: str, req: Optional[str] = None) -> bool:
    return notify(*msg_new_card(title), req=req)


def notify_done(title: str, req: Optional[str] = None) -> bool:
    return notify(*msg_done(title), req=req)


def notify_needs_input(title: str, req: Optional[str] = None) -> bool:
    return notify(*msg_needs_input(title), req=req)


def notify_auth(service: str) -> bool:
    return notify(*msg_auth(service))
