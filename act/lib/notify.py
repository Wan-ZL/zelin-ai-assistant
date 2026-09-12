"""Native notifications + transition classifiers (CONTRACT §5, §28).

State transitions surfaced as native notifications:
  - new card_sent (radar found a new requirement)  -> "有新需求待审批：<title>"
  - executing -> done                              -> "任务完成：<title>"
  - executing 受阻/放弃救活 -> review（#119 收割）  -> "任务停下来了：<title>"
  - credential failure (log has auth/login words)  -> "需要重新登录：<service>"

§28 (app identity relay): on darwin the native path never fires osascript —
it queues one JSON file into state/notify_queue/ for the menu-bar app, which
posts it via UNUserNotificationCenter (proper "Zelin's AI Assistant"
identity/icon instead of Script Editor) and deletes the file. There is NO
fallback by owner decision (2026-07-10): app closed = no native notification
(the app auto-starts at login, so running is the normal state).

§28 追记（issue #29）通知偏好：安静时段 + 分类开关。分类 = 队列条目的
``kind``；抑制发生在**写方**（这里），不在 §28 的 drain —— 队列有 10 分钟
stale 清扫，「压到早上再弹」在那之下是谎话（被压住的条目会先老死）。写方
不入队 = 队列的 stale / burst / 消费即删三条语义一个字不动。见下方
``suppression_reason``。

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
           req: Optional[str] = None, kind: Optional[str] = None) -> bool:
    """Fire a native notification via the app relay queue (§28).

    Never raises — a failed notification must not break the daemon loop.
    ``req`` (an R-xxx id, optional) is accepted for caller compatibility but is
    no longer used (v0.21 removed the phone mirror / reaction-approval surface).
    ``kind`` (add-only, v0.46) names the notification's category: it rides into
    the queue entry so the app relay can apply presentation preferences
    ("review_ready" ↔ the 完成提醒 三档开关 off/banner/sound), and it is what
    the writer-side preference gate below judges (安静时段 + 分类开关, issue
    #29). An entry without a kind has no category switch of its own, but quiet
    hours still applies to it.

    Returns whether the notification was handed to the consumer. A deliberate
    **True** for one suppressed by the user's preferences: that is not a
    failure — the only False means "could not hand it over" (an unwritable
    queue dir, which scripts/auto-deploy.sh reports as such).
    """
    if _suppressed(kind):
        return True
    return _native_notify(title, body, subtitle, kind=kind)


# --------------------------------------------------------------------------- #
# §28 通知偏好（issue #29）—— 安静时段 + 分类开关
# --------------------------------------------------------------------------- #
# 分类 = 队列条目的 ``kind``（§28 v0.46 add-only 键）。issue #29 的四组：
# 提案 / 完成 / 需输入 / 失败。「完成」一类的开关**不在这里**——`review_notify`
# 三档（关 / 横幅 / 横幅+声音）由消费方（壳 NotifyRelay）执法，逐字不动。
#
# 为什么抑制在写方而不是 §28 的 drain：队列有 10 分钟 stale 清扫
# （STALE_AFTER_S），「压到早上再弹」在这个机制下是谎话——被压住的条目会先
# 老死。所以安静时段 = **写方不入队**：队列的 stale / burst / 消费即删三条
# 语义一个字不动，§28 的 staleness 保证不被触碰。附带两个好处：非 darwin
# 的 notify-send 路径同样受偏好管；shell/Sources/NotifyRelay.swift 得以继续是
# mac/ 冻结件的逐字副本（§28 v0.48.x，判例 tests/test_shell_engine_mirror.py）。
# 职责切分自此是：**抑制归写方（Python），呈现（横幅 / 声音）归消费方（壳）**。
KIND_PROPOSAL = "proposal"          # 新卡待审批 / 批量 / 回锅
KIND_REVIEW_READY = "review_ready"  # 交付进待验收（v0.46 就有）
KIND_NEEDS_INPUT = "needs_input"    # 任务停下来了 / 反复中断 / 停止重试，等人一句话
KIND_FAILURE = "failure"            # 需重新登录 / 雷达停摆 / 派发失败 / 会话没停住

# 分类 → overrides 扁键（布尔，全部默认开：新装机行为与本改动前逐字一致）。
CATEGORY_PREFERENCE = {
    KIND_PROPOSAL: "notify_proposals",
    KIND_NEEDS_INPUT: "notify_needs_input",
    KIND_FAILURE: "notify_failures",
}

# 失败类穿透安静时段：夜里凭证过期 / 雷达停摆同样要当场知道（issue #29
# 「failures probably not silenceable by default」——开关仍在、默认开，只是
# 安静时段管不着它；要静音失败得显式关掉 notify_failures）。
QUIET_HOURS_EXEMPT = frozenset({KIND_FAILURE})


def _minute_of_day(hhmm) -> Optional[int]:
    """``"HH:MM"`` → 当日分钟数；坏值 None（调用方据此把安静时段当关——
    fail-open：配错一个字不许把所有通知静音）。词法单源 = §70 的
    ``config.coerce_clock_time``（server/settings.py 镜像同一正则）。"""
    from act.lib import config as _config
    try:
        text = _config.coerce_clock_time(hhmm)
    except (TypeError, ValueError):
        return None
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def in_quiet_hours(start, end, minute_of_day: int) -> bool:
    """半开区间 ``[start, end)`` 是否盖住当日第 ``minute_of_day`` 分钟。

    跨午夜（典型的 22:00 → 08:00）算两段；``start == end``（零长窗）或任一
    端坏值 = 关。纯函数，判例直接钉它。"""
    a, b = _minute_of_day(start), _minute_of_day(end)
    if a is None or b is None or a == b:
        return False
    if a < b:
        return a <= minute_of_day < b
    return minute_of_day >= a or minute_of_day < b


def _category_off(kind, cfg) -> bool:
    """本类的分类开关被显式关掉了（没登记开关的 kind 恒为 False）。"""
    preference = CATEGORY_PREFERENCE.get(kind or "")
    return preference is not None and not bool(getattr(cfg, preference, True))


def _quiet_now(kind, cfg, now) -> bool:
    """当下落在安静时段里且本类不豁免（失败类穿透）。"""
    if kind in QUIET_HOURS_EXEMPT or not bool(getattr(cfg, "quiet_hours_enabled", False)):
        return False
    local = now if now is not None else time.localtime()
    return in_quiet_hours(getattr(cfg, "quiet_hours_start", ""),
                          getattr(cfg, "quiet_hours_end", ""),
                          local.tm_hour * 60 + local.tm_min)


def suppression_reason(kind, cfg, now=None) -> Optional[str]:
    """本条通知该被吃掉的原因词（``"category"`` / ``"quiet_hours"``），None = 照发。

    ``cfg`` = ``act.lib.config.Config``（注入缝）；``now`` = ``time.struct_time``
    本地时间（注入缝，缺省现读）。判序：分类开关先（关掉 = 任何时候都不发），
    再是安静时段（失败类豁免）。没登记在 ``CATEGORY_PREFERENCE`` 的 kind
    （``recap_ready`` / 无 kind 的其余守护进程通知）没有分类开关，但同样守安静时段
    ——「晚上不弹横幅」就是这个意思。"""
    if _category_off(kind, cfg):
        return "category"
    if _quiet_now(kind, cfg, now):
        return "quiet_hours"
    return None


def _suppressed(kind) -> bool:
    """现读一次偏好并判本条是否被吃掉。

    现读（不吃启动时冻结的 cfg）：用户在设置页翻完开关应该立即生效，
    与 §48 雷达巡检同款；通知是稀事件，一次盘读不心疼。读配置出任何
    意外一律**照发**（fail-open：丢一条通知比吞一条通知贵）。"""
    try:
        from act.lib import config as _config
        return suppression_reason(kind, _config.load_config()) is not None
    except Exception:  # noqa: BLE001 - a notification must never break a caller
        return False


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


def _native_notify(title: str, body: str, subtitle: Optional[str] = None,
                   kind: Optional[str] = None) -> bool:
    """§28 relay-only native notification. Never raises.

    darwin: queue for the app — its 5 s tick posts and deletes the entry;
    nothing is posted while the app is closed (no fallback, on purpose).
    Other OSes keep the plain OS seam (notify-send) — the relay exists only
    because the darwin app owns the notification identity.
    """
    if not platform.is_darwin():
        return platform.notify_user(title, body, subtitle)
    return _queue_write(title, body, subtitle, kind=kind) is not None


def _queue_write(title: str, body: str, subtitle: Optional[str] = None,
                 kind: Optional[str] = None,
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
        if kind:
            entry["kind"] = str(kind)
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
            removed += _unlink_if_stale(f, cutoff)
    except OSError:
        pass
    return removed


def _unlink_if_stale(f: Path, cutoff: float) -> int:
    """1 when the entry was older than ``cutoff`` and removed, else 0. Losing a
    race with the app deleting the same file is fine (missing_ok); a stat
    failure (raced with the app's own delete) skips the entry."""
    try:
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            return 1
    except OSError:
        pass
    return 0


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
    classes stay per-card — those each demand a distinct decision.

    Copy is source-NEUTRAL on purpose: actd only sees the board diff, and
    fresh cards may come from any filer (radar, weekly digest, capture) —
    attributing them to 雷达 would mislabel every non-radar batch."""
    return (_pick(f"新增 {n} 张待审批卡", f"{n} new cards awaiting approval"),
            _pick("打开菜单栏面板逐张审批（✅ 批准 / ❌ 拒绝）",
                  "Open the menu-bar panel to review them (✅ approve / ❌ reject)"))


def msg_registry_guard(title: str, files: str) -> tuple[str, str]:
    """§34bis 机械护栏：清理会话期间 registry 出现非 actd 的文件变动。"""
    return (_pick("清理会话疑似改动了 registry，请核查",
                  "Triage session may have modified the registry"),
            _pick(f"{title} —— 快照比对发现非 actd 写入：{files}。会话按律只读，"
                  "请人工核查这些卡片文件",
                  f"{title} — snapshot diff found non-actd writes: {files}. "
                  "The session is read-only by law; please inspect these card files"))


def msg_done(title: str) -> tuple[str, str]:
    return (_pick("任务完成", "Task finished"),
            _pick(f"{title} —— 打开 App 验收或打回",
                  f"{title} — open the app to accept or send back"))


# （msg_needs_input / msg_answer_not_delivered / msg_answer_failed：retired
# v0.48.8（#119）——受阻会话不再挂「需输入」等回答，收割进待验收由
# msg_review_interrupted 通知。）


def msg_review_interrupted(title: str) -> tuple[str, str]:
    """#119（§46.3 v0.48.8）：受阻/不再推进的会话被收割进待验收——不是一次
    正常交付（msg_review_ready 的「已交付草稿」是虚报），文案指向现存出口：
    验收 / 丢弃 / 打回附一句话（打回即回答，rework 管道继续会话）。"""
    return (_pick("任务停下来了，去看看它做到哪了", "A task stopped — see where it got to"),
            _pick(f"{title} —— 会话在等输入，已收下现有成果进「待验收」。"
                  "验收、丢弃，或点「打回」附一句话回答它并继续",
                  f"{title} — the session was waiting for input; its work so far"
                  " is in Review. Accept, discard, or press Send back with a"
                  " note to answer it and continue"))


def msg_radar_dead(source: str, hours: int) -> tuple[str, str]:
    """§48 源死亡告警：开着的源超过 liveness 阈值没有成功过一次。

    只对 enabled 的源发（关掉的源全静默）；actd 侧有 anti-nag 台账保证
    同一次死亡只响一次（恢复后再死才会再响）。"""
    names = {"gmail": "Gmail", "slack": "Slack", "obsidian": "Obsidian"}
    name = names.get(source, source)
    return (_pick(f"{name} 雷达停摆了", f"The {name} radar has gone quiet"),
            _pick(f"开着的 {name} 源已 {hours} 小时没有成功扫过一次"
                  f" —— 打开 App 设置页对应源区看运行状态与失败原因",
                  f"The {name} source is on but hasn't completed a scan in "
                  f"{hours}h — check its status row in the app's Settings"))


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


def msg_dispatch_halted(title: str, n: int, reason: Optional[str] = None) -> tuple[str, str]:
    """§4 dispatch-storm brake: ``n`` straight launch failures of one failure
    class — actd stops retrying and parks the card in the blocked lane.

    2026-08-31: a 256-fd cap made one card fail 66 launches in 13h while the
    only notification said「会自动重试」. The body names the classified cause
    when there is one and the exact buttons that re-arm the card (停止 →
    退回提案 → 批准 clears the streak; approve is the re-arm verb)."""
    why = f"：{reason}" if reason else ""
    why_en = f": {reason}" if reason else ""
    return (_pick(f"任务派发已停止重试（连续失败 {n} 次）",
                  f"Task launch stopped retrying ({n} straight failures)"),
            _pick(f"{title}{why} —— 这张卡在「需输入」列。修好原因后点「停止」选"
                  "「退回提案」，再重新批准即恢复派发",
                  f"{title}{why_en} — the card is in Needs input. Fix the cause, then"
                  " press \"Stop\" → \"Discard & re-propose\" and approve it again"
                  " to resume dispatch"))


def msg_resuming(title: str) -> tuple[str, str]:
    return (_pick("任务疑似中断，正在自动恢复", "Task looks interrupted — auto-recovering"),
            _pick(f"{title} —— 无需操作；持续失败会另行通知",
                  f"{title} — nothing to do; you'll be notified if it keeps failing"))


def msg_auto_resume_exhausted(title: str) -> tuple[str, str]:
    """5 straight resume failures — actd gives up and harvests to review
    (#119, §46.3 v0.48.8); the copy names the exact Review-lane verbs."""
    return (_pick("自动恢复已放弃（连续失败 5 次）",
                  "Auto-recovery gave up (5 straight failures)"),
            _pick(f"{title} —— 已停止自动拉起，现有成果收进了「待验收」。"
                  "验收、丢弃，或点「打回」附一句话让它继续",
                  f"{title} — auto-relaunch stopped; its work so far is in"
                  " Review. Accept, discard, or press Send back with a note"
                  " to keep it going"))


def msg_resume_storm(title: str, n: int) -> tuple[str, str]:
    """§46 resume 风暴降级：短窗口内自动救活 n 次后会话又死了 —— 卡死→救→再死
    的循环没有出口，actd 停止无限救活并收割进待验收（#119）请人看一眼。"""
    return (_pick(f"任务反复中断（30 分钟内已自动救活 {n} 次）",
                  f"Task keeps dying ({n} auto-recoveries in 30 min)"),
            _pick(f"{title} —— 自动恢复已暂停，现有成果收进了「待验收」列。"
                  "验收、丢弃，或点「打回」附一句话给它新指示",
                  f"{title} — auto-recovery paused; its work so far is in"
                  " Review. Accept, discard, or press Send back with fresh"
                  " directions"))


def msg_stop_failed(title: str) -> tuple[str, str]:
    """§46 stop 确认失败：重试后会话进程仍存活——可能还在后台烧钱/占资源，
    绝不静默；卡片 notes 同步留 [stop-failed] 台账。"""
    return (_pick("会话没停住（可能仍在后台运行）",
                  "Session didn't stop (may still be running)"),
            _pick(f"{title} —— 已重试仍存活；在终端跑 `claude agents` 找到它，"
                  "再 `claude stop <id>` 手动停止",
                  f"{title} — still alive after retries; run `claude agents`"
                  " in a terminal to find it, then `claude stop <id>`"))


# --------------------------------------------------------------------------- #
# §65 self_improve 自动草稿 PR 通道（宪法第 10 条：自动化替 owner 做的事必须可见）
# --------------------------------------------------------------------------- #
def msg_self_improve_dispatched(title: str) -> tuple[str, str]:
    """§65 lane 免批派发的观察模式通知（同 hand lane 的 autodispatch.notify）。"""
    return (_pick("自我改进通道：已免批派发（交付只能是草稿 PR）",
                  "Self-improve lane: dispatched without approval (draft PR only)"),
            _pick(f"{title} —— 做完后守护进程会用 gh 物理核验草稿 PR；你只需看绿色的 PR",
                  f"{title} — the daemon verifies the draft PR with gh when it finishes;"
                  " you only need to look at green PRs"))


def msg_auto_dispatched(reason: str, title: str) -> tuple[str, str]:
    """§51 观察模式通知按 lane 分派：hand lane 文案逐字不变（v0.48 原句）。"""
    if reason == "ok:self_improve":
        return msg_self_improve_dispatched(title)
    return ("观察模式：手打卡已自动派发（免批）", title)


def msg_self_improve_unverified(title: str, reason: str) -> tuple[str, str]:
    """§65.3 交付核验失败：卡进待验收但带 interrupted 标记，原因 token 上卡。"""
    return (_pick("自我改进通道：草稿 PR 未通过核验",
                  "Self-improve lane: draft PR failed verification"),
            _pick(f"{title} —— 原因 {reason}。卡在「待验收」列：打回附一句话让它补上，或丢弃",
                  f"{title} — reason {reason}. The card sits in Review: send it back with a"
                  " note to fix, or discard"))


def msg_self_improve_paused(title: str, pr_url: str, paths: list) -> tuple[str, str]:
    """§65.4 敏感路径护栏：PR 打 needs-owner-eyes 标签，通道挂起直到 owner 清。"""
    shown = ", ".join(paths[:3]) + ("…" if len(paths) > 3 else "")
    return (_pick("自我改进通道已暂停：PR 触碰了受保护路径",
                  "Self-improve lane paused: PR touches protected paths"),
            _pick(f"{title} —— {shown}。{pr_url} 已打 needs-owner-eyes；处理该 PR"
                  "（合并/关闭）或在看板点「恢复通道」后自动派发才继续",
                  f"{title} — {shown}. {pr_url} is labelled needs-owner-eyes; auto-dispatch"
                  " resumes once you handle that PR (merge/close) or press Resume on the board"))


def msg_self_improve_followup(pr_number: int, n_comments: int, n_red: int) -> tuple[str, str]:
    """§65.5 PR 跟进卡铸出（owner 评论 / 红 required check → 新卡入通道）。"""
    return (_pick(f"自我改进通道：PR #{pr_number} 有新活要做",
                  f"Self-improve lane: follow-up filed for PR #{pr_number}"),
            _pick(f"{n_comments} 条 owner 评论、{n_red} 项红检查 —— 已铸跟进卡并入通道",
                  f"{n_comments} owner comments, {n_red} red checks — follow-up card filed"
                  " into the lane"))


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
