"""actd — the assistant daemon loop (entrypoint + compatibility facade).

Each pass:
  (a) drain STATE/inbox/*.json decisions
        approve  -> status=approved（W17：外部出身未扩写 -> 转 raising）
        reject   -> status=rejected
        comment  -> fold text into plan/notes, keep card_sent (re-approval)
                    ——除 EXECUTING 卡：comment = steer（§44.3-S 中途转向指令，
                    入队等安全窗口 flush 进 live session，状态机零改动）
        merge_review / merge_apply / merge_dismiss -> merge-review 契约 一/四/五
      delete the decision file after reading it.
  (a') auto-dispatch（§51 hand lane + §65 self_improve lane）：card_sent 卡过天花板即免批 approved。
  (b) dispatch every status=approved requirement that has no execution yet
      （并发上限内；超出留在合并运行列的 queued 子状态）。
  (b') merge-review housekeeping: TTL-sweep state/merge/ job files; fail
       'analyzing' jobs older than 20 minutes.
  (b'') feedback upload retry (§29): pending state/feedback/ records get ONE
        more attempt, then uploaded:false (kept local, never retried again).
  (c) build + atomically write dashboard.json.
  (d) diff against the previous dashboard; notify on state transitions.

Robust: a single exception never kills the loop; everything is logged to
STATE/actd.log. ``--once`` runs exactly one pass then exits (for tests/cron).

Run: ``python -m act.actd`` (or ``python -m act.actd --once``).

Layout (P3b, CONTRACT §58.4): the pass logic lives in the ``act.lib.actd``
package, one module per phase (inbox / decisions / merge / dispatch /
reconcile / housekeeping / alerts, plus session / triage_guard / seam). This
file keeps three things: (1) the entry-layer collaborators — ``executor`` /
``analyze`` / ``merge_review`` / ``radar_claude_sessions`` are imported HERE
and nowhere below (防腐 #2: lib never imports upward; qa/deps_baseline.txt
keys the four grandfathered edges to this path); (2) the loop itself
(``run_once`` / ``main`` / LoopHealthTracker / heartbeat phases); (3) the
compatibility surface every test patches — the names below are the seam the
lib code reads at call time through ``_ctx()`` (``seam.Daemon``), so
``patch.object(actd, "executor", …)`` keeps working. New code should import
the ``act.lib.actd.*`` modules directly; the re-exported ``_private`` names
here exist only for that compatibility and are deprecated as import targets
(CONTRACT §58.4 P3b entry).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import time
import traceback
from pathlib import Path
from typing import Optional

from act.lib import (
    analytics,
    card_summary,
    config,
    daily_loop,
    detached,
    heartbeat,
    logcap,
    maintenance,
    notify,
    recap_store,
    registry,  # noqa: F401 - surface: tests patch ``actd.registry.load`` (module attr)
    self_improve,
)
from act.lib.actd import alerts as _alerts
from act.lib.actd import decisions as _decisions
from act.lib.actd import dispatch as _dispatch
from act.lib.actd import housekeeping as _housekeeping
from act.lib.actd import inbox as _inbox
from act.lib.actd import merge as _merge
from act.lib.actd import reconcile as _reconcile
from act.lib.actd import seam as _seam
from act.lib.actd import session as _session
from act.lib.actd import triage_guard as _triage_guard
from act.lib.agent_states import BLOCKED_STATES, DONE_STATES, LIVE_STATES, RUNNING_STATES
from act.lib.dashboard import build_dashboard, index_agents, run_claude_agents, write_dashboard
from act.lib.registry import Requirement, State, load, load_all, save  # noqa: F401 - re-exported surface

try:
    from act import executor
except Exception:  # pragma: no cover - executor import must not kill daemon
    executor = None  # type: ignore

try:
    from act import analyze
except Exception:  # pragma: no cover - analyze import must not kill daemon
    analyze = None  # type: ignore

try:
    from act import merge_review
except Exception:  # pragma: no cover - merge_review import must not kill daemon
    merge_review = None  # type: ignore

try:
    from act import radar_claude_sessions
except Exception:  # pragma: no cover - session import must not kill daemon
    radar_claude_sessions = None  # type: ignore

try:
    from act.lib import update_check
except Exception:  # pragma: no cover - update check must not kill daemon
    update_check = None  # type: ignore

try:
    from act.lib import auto_merge
except Exception:  # pragma: no cover - auto merge hints must not kill daemon
    auto_merge = None  # type: ignore

try:
    from act.lib import feedback
except Exception:  # pragma: no cover - feedback import must not kill daemon
    feedback = None  # type: ignore


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    config.ensure_state_dirs()
    line = f"{_dt.datetime.now().isoformat(timespec='seconds')}  {msg}\n"
    try:
        # errors="replace": a decision file may legally json-decode into text
        # containing lone UTF-16 surrogates ("\ud800"), which utf-8 refuses to
        # encode — logging about bad input must never crash on the bad input
        # itself (nightly audit 2026-07-14).
        with (config.STATE_DIR / "actd.log").open(
                "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line)
        # 自压缩（best-effort）：KeepAlive 常驻进程的日志从不轮转会无限增长
        # （live 事故：syncd.log 74MB）——超 ~1MB 只留后半，registry 台账同款。
        logcap.cap(config.STATE_DIR / "actd.log")
    except (OSError, UnicodeError):
        pass


def _iso_now() -> str:
    """UTC ISO stamp — the registry-side timestamp format (dashboard 转 epoch)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# §5.4 sync ack ledger — one line per terminal inbox disposition.
# --------------------------------------------------------------------------- #
# M2 sync-active cache — keyed on state/sync.json's stat, so an opt-in/opt-out
# flip (syncd rewrites the file) is picked up without a daemon restart, while a
# non-sync install pays only one cheap os.stat() per call (never a JSON parse).
_SYNC_ACTIVE_CACHE: Optional[tuple] = None  # (stat_key, is_active)


def _sync_stat_key(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _sync_mode_cloud(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and str(data.get("mode") or "").lower() == "cloud"


def _sync_active() -> bool:
    """M2: True only when cloud sync is opted in (``state/sync.json`` exists with
    ``mode == "cloud"``). Gates ``_write_applied_ack`` so a purely local Mac/web
    user never creates ``state/sync/`` nor grows ``applied.jsonl``; a synced user
    still gets every ack (the ack→delivered/applied flow syncd relies on)."""
    global _SYNC_ACTIVE_CACHE
    path = config.STATE_DIR / "sync.json"
    stat_key = _sync_stat_key(path)
    if _SYNC_ACTIVE_CACHE is not None and _SYNC_ACTIVE_CACHE[0] == stat_key:
        return _SYNC_ACTIVE_CACHE[1]
    active = stat_key is not None and _sync_mode_cloud(path)
    _SYNC_ACTIVE_CACHE = (stat_key, active)
    return active


def _write_applied_ack(action_id: str, result_status: str) -> None:
    """Append an ack line to ``state/sync/applied.jsonl`` (§5.4).

    ``syncd`` tails this file and PATCHes ``inbox_actions.status='applied'`` +
    ``result_status`` from it, so a phone-issued action reaches a DURABLE
    terminal state for EVERY outcome — not just success, but a guarded no-op
    (result_status=noop), an unknown/gone card (unknown) and an unreadable file
    (bad_json) too. Without this the phone can only infer application from a
    deleted inbox file (``_safe_unlink`` runs on every path), which is a
    false-negative retry loop / a false 已生效.

    M2: no-op unless cloud sync is ACTIVE — a local-only install has no phone to
    ack to, so it must not create ``state/sync/`` or grow ``applied.jsonl``.

    ``action_id`` is the inbox file stem (= the cloud idempotency key for synced
    actions; a random Mac-app uuid for local ones, which simply matches no cloud
    row — a harmless PATCH of 0 rows). Runs on macOS/Linux too; best-effort,
    never raises into the daemon pass.
    """
    if not _sync_active():
        return
    try:
        d = config.STATE_DIR / "sync"
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"action_id": str(action_id), "result_status": str(result_status),
             "ts": _iso_now()},
            ensure_ascii=False)
        with (d / "applied.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# detached special forms（§24 weekly digest on demand, §63 recap buttons）
# --------------------------------------------------------------------------- #
def _spawn_weekly_digest(_decision: Optional[dict] = None) -> str:
    """§24 Settings「现在生成一份」→ ``act.weekly_digest --now`` detached (§5.4 ack)."""
    result = detached.launch(["act.weekly_digest", "--now"], "weekly_digest.log",
                             "weekly_digest_now", _log)
    if result == detached.RUNNING:
        analytics.log_event("weekly_digest_requested")
    return result


def _spawn_recap(decision: dict) -> str:
    """§63 ``recap_generate`` / ``recap_slack_draft`` → ``act.recap <argv>`` detached;
    malformed (bad key / note / channel id) = honest noop — the store validates."""
    argv = recap_store.inbox_argv(decision)
    if argv is None:
        _log(f"inbox: {decision.get('action')} malformed — dropped")
        return detached.NOOP
    return detached.launch(["act.recap"] + argv, "recap.log", str(decision.get("action")), _log)


_DETACHED_ACTIONS = {  # late-bound lambdas: tests patch the module attribute
    "weekly_digest_now": lambda decision: _spawn_weekly_digest(),
    "recap_generate": lambda decision: _spawn_recap(decision),
    "recap_slack_draft": lambda decision: _spawn_recap(decision),
}


# --------------------------------------------------------------------------- #
# the seam: this module's namespace, snapshotted per call (§58.3 / 防腐 #2)
# --------------------------------------------------------------------------- #
def _ctx() -> _seam.Daemon:
    """Build the ``Daemon`` view the ``act.lib.actd`` modules read. Built on
    every call on purpose: tests patch names on THIS module, and the moved
    code must see the patched value, not an import-time copy."""
    return _seam.Daemon(
        executor=executor, analyze=analyze, merge_review=merge_review,
        radar_claude_sessions=radar_claude_sessions, feedback=feedback,
        log=_log, iso_now=_iso_now, save=save, safe_unlink=_safe_unlink,
        write_applied_ack=_write_applied_ack, detached_actions=_DETACHED_ACTIONS,
        run_claude_agents=_run_claude_agents,
        apply_decision=_apply_decision, apply_capture=_apply_capture,
        stop_session_tracked=_stop_session_tracked, stop_live_session=_stop_live_session,
        merge_into_primary=_merge_into_primary, apply_merge_verdict=_apply_merge_verdict,
        promote_if_delivered=_promote_if_delivered,
    )


# --------------------------------------------------------------------------- #
# compatibility surface — the names tests and the seam address on this module.
# Each is a thin delegate; the behaviour (and its docstring) lives in act.lib.actd.
# --------------------------------------------------------------------------- #
_run_claude_agents = run_claude_agents          # roster reader seam (tests patch it here)
_index_agents = index_agents
_BLOCKED_STATES, _DONE_STATES, _LIVE_STATES, _RUNNING_STATES = (
    BLOCKED_STATES, DONE_STATES, LIVE_STATES, RUNNING_STATES)
_TransitionDenied = _inbox.TransitionDenied
_parse_iso = maintenance.parse_iso
_mtime_dt = _merge.mtime_dt
_MERGE_DEAD_STATES = _merge.MERGE_DEAD_STATES
PROPOSALS_TRIAGE_PRESET = _triage_guard.PROPOSALS_TRIAGE_PRESET
_proposals_triage_plan = _triage_guard.proposals_triage_plan
_proposals_triage_in_flight = _triage_guard.proposals_triage_in_flight
_registry_snapshot = _triage_guard.registry_snapshot
_triage_snapshot_path = _triage_guard.triage_snapshot_path
_precondition_ok = _decisions.precondition_ok
_fold_comment = _decisions.fold_comment
_clean_image_paths = _inbox.clean_image_paths
_decision_actor = _inbox.decision_actor
_ingress_channel = _inbox.ingress_channel
_is_owner_ingress = _inbox.is_owner_ingress
_partition_results_summary = _merge.partition_results_summary
_card_cost = _dispatch.card_cost
_has_future_deadline = _housekeeping.has_future_deadline
_cluster_has_live_sibling = _housekeeping.cluster_has_live_sibling
_thread_last_activity = _housekeeping.thread_last_activity
_registry_attachment_refs = _housekeeping.registry_attachment_refs
_ARCHIVE_SWEEP_MARKER = _housekeeping.ARCHIVE_SWEEP_MARKER
_OPEN_STATES = _housekeeping.OPEN_STATES
_ATTACH_GC_MARKER = _housekeeping.ATTACH_GC_MARKER
_ATTACH_GC_INTERVAL_S = _housekeeping.ATTACH_GC_INTERVAL_S
_ATTACH_GC_MAX_AGE_S = _housekeeping.ATTACH_GC_MAX_AGE_S
_by_id = _alerts.by_id
_NEW_CARD_BATCH_ABOVE = _alerts.NEW_CARD_BATCH_ABOVE
_WAKE_JUMP_FACTOR = _alerts.WAKE_JUMP_FACTOR
_WAKE_JUMP_FLOOR_SECONDS = _alerts.WAKE_JUMP_FLOOR_SECONDS
_WAKE_GRACE_SECONDS = _alerts.WAKE_GRACE_SECONDS
_wake_state = _alerts.WAKE_STATE                # shared dict: tests mutate it in place
_no_baseline_since = _alerts.NO_BASELINE_SINCE  # shared dict: tests clear it in place
_HARVEST_PROBE_AT = _reconcile.HARVEST_PROBE_AT  # shared dict: tests clear / patch.dict it
_HARVEST_PROBE_INTERVAL_S = _reconcile.HARVEST_PROBE_INTERVAL_S
RESUME_STORM_THRESHOLD = _reconcile.RESUME_STORM_THRESHOLD
RESUME_STORM_WINDOW_S = _reconcile.RESUME_STORM_WINDOW_S
RESUME_HISTORY_CAP = _reconcile.RESUME_HISTORY_CAP
_recent_resume_count = _reconcile.recent_resume_count


# (a) inbox ------------------------------------------------------------------
def process_inbox() -> int:
    return _inbox.process_inbox(_ctx())


def _apply_capture(text: Optional[str], mode: Optional[str] = None, images=None,
                   plan: Optional[list] = None, preset: Optional[str] = None,
                   inbox_stem: Optional[str] = None, via: Optional[object] = None) -> str:
    return _inbox.apply_capture(_ctx(), text, mode, images, plan=plan, preset=preset,
                                inbox_stem=inbox_stem, via=via)


def _attach_capture_images(req: Requirement, images) -> None:
    return _inbox.attach_capture_images(_ctx(), req, images)


def _apply_split_note(req_id, note_ts) -> str:
    return _inbox.apply_split_note(_ctx(), req_id, note_ts)


def _apply_set_title(req: Requirement, title) -> str:
    return _inbox.apply_set_title(_ctx(), req, title)


def _apply_feedback(decision: dict) -> str:
    return _inbox.apply_feedback(_ctx(), decision)


def _apply_claude_import(decision: dict) -> str:
    return _inbox.apply_claude_import(_ctx(), decision)


def _apply_with_actor(decision: dict, fn, *args, **kwargs) -> str:
    return _inbox.apply_with_actor(_ctx(), decision, fn, *args, **kwargs)


def _apply_decision(req: Requirement, action: Optional[str], comment: Optional[str],
                    expected_status: Optional[str] = None, board_seq=None,
                    ts: Optional[object] = None, via: Optional[object] = None,
                    stem: Optional[str] = None) -> str:
    return _decisions.apply_decision(_ctx(), req, action, comment, expected_status,
                                     board_seq, ts=ts, via=via, stem=stem)


def _record_nonowner_comment(req: Requirement, comment: Optional[str], via: object) -> str:
    return _decisions.record_nonowner_comment(_ctx(), req, comment, via)


# live sessions (§11 / §37 / §46) ---------------------------------------------
def _stop_session_tracked(req: Requirement, ex: dict, sid, why: str,
                          log_prefix: str = "inbox") -> tuple:
    return _session.stop_session_tracked(_ctx(), req, ex, sid, why, log_prefix=log_prefix)


def _stop_live_session(req: Requirement, why: str) -> None:
    return _session.stop_live_session(_ctx(), req, why)


def _apply_harvest_title(req: Requirement, harvested: dict) -> None:
    return _session.apply_harvest_title(_ctx(), req, harvested)


def _update_search_index(card_id, session_id) -> None:
    return _session.update_search_index(_ctx(), card_id, session_id)


# §34bis triage guard ---------------------------------------------------------
def _stamp_triage_snapshot(req_id: str) -> Optional[str]:
    return _triage_guard.stamp_triage_snapshot(_ctx(), req_id)


def _check_triage_registry_guard(req, ex: dict) -> None:
    return _triage_guard.check_triage_registry_guard(_ctx(), req, ex)


def _sweep_triage_snapshots() -> None:
    return _triage_guard.sweep_triage_snapshots(_ctx())


# merge-review (§21) ----------------------------------------------------------
def _apply_merge_review(ids) -> str:
    return _merge.apply_merge_review(_ctx(), ids)


def _apply_merge_force(ids, primary) -> str:
    return _merge.apply_merge_force(_ctx(), ids, primary)


def _apply_merge_decision(action: str, suggestion_id) -> str:
    return _merge.apply_merge_decision(_ctx(), action, suggestion_id)


def _apply_merge_verdict(job: dict) -> None:
    return _merge.apply_merge_verdict(_ctx(), job)


def _apply_merge_partition(job: dict) -> None:
    return _merge.apply_merge_partition(_ctx(), job)


def _merge_into_primary(primary_id: str, secondaries: list) -> None:
    return _merge.merge_into_primary(_ctx(), primary_id, secondaries)


def cleanup_merge_jobs() -> int:
    return _merge.cleanup_merge_jobs(_ctx())


# (a') auto-dispatch / (b) dispatch / raising --------------------------------
def _rearm_dispatch(ex: dict) -> dict:
    return _dispatch.rearm_dispatch(_ctx(), ex)


def auto_dispatch_pass(cfg: config.Config) -> int:
    return _dispatch.auto_dispatch_pass(_ctx(), cfg)


def dispatch_approved(cfg: config.Config) -> int:
    return _dispatch.dispatch_approved(_ctx(), cfg)


def process_raising(cfg: config.Config) -> int:
    return _dispatch.process_raising(_ctx(), cfg)


# reconcile -------------------------------------------------------------------
def _reconcile_review_attach(req: Requirement, agents: dict) -> None:
    return _reconcile.reconcile_review_attach(_ctx(), req, agents)


def _promote_if_delivered(req, ex: dict, sid) -> bool:
    return _reconcile.promote_if_delivered(_ctx(), req, ex, sid)


def _harvest_to_review(req: Requirement, ex: dict, sid, note_tag: str, log_reason: str,
                       interrupted_reason: str = "", agent: Optional[dict] = None) -> None:
    return _reconcile.harvest_to_review(_ctx(), req, ex, sid, note_tag, log_reason,
                                 interrupted_reason=interrupted_reason, agent=agent)


def _drop_steers(req: Requirement, pend: list, reason: str, why: str) -> None:
    return _reconcile.drop_steers(_ctx(), req, pend, reason, why)


def _flush_steers(req: Requirement, cfg: config.Config) -> None:
    return _reconcile.flush_steers(_ctx(), req, cfg)


def reconcile_executing(cfg: config.Config, resume_notified: set) -> int:
    return _reconcile.reconcile_executing(_ctx(), cfg, resume_notified)


# housekeeping ----------------------------------------------------------------
def purge_trash(cfg: config.Config) -> int:
    return _housekeeping.purge_trash(_ctx(), cfg)


def _purge_one(req: Requirement, cfg: config.Config, now: _dt.datetime) -> int:
    return _housekeeping.purge_one(_ctx(), req, cfg, now)


def archive_stale(cfg: config.Config) -> int:
    return _housekeeping.archive_stale(_ctx(), cfg)


def _sweep_attachment_dirs(now: Optional[float] = None) -> int:
    return _housekeeping.sweep_attachment_dirs(_ctx(), now)


def gc_attachments() -> int:
    return _housekeeping.gc_attachments(_ctx())


# (d) alerts ------------------------------------------------------------------
def detect_transitions(prev: Optional[dict], curr: dict) -> list:
    return _alerts.detect_transitions(prev, curr)


def _check_auth_failures(notified: set) -> list:
    return _alerts.check_auth_failures(notified)


def _wake_grace(cfg: config.Config, wall: float, interval: Optional[int] = None,
                mono: Optional[float] = None) -> bool:
    return _alerts.wake_grace(cfg, wall, interval, mono)


def _check_radar_liveness(notified: set, now: Optional[_dt.datetime] = None,
                          interval: Optional[int] = None, mono: Optional[float] = None,
                          missing_since: Optional[dict] = None) -> list:
    return _alerts.check_radar_liveness(_ctx(), notified, now=now, interval=interval,
                                        mono=mono, missing_since=missing_since)


# --------------------------------------------------------------------------- #
# §47.3 loop health — 连续 pass 崩溃的可见化（state/loop_health.json）
# --------------------------------------------------------------------------- #
LOOP_HEALTH_NAME = "loop_health.json"
# 连续失败达到该阈值 App 侧才报警（Mac Store 的 PipelineHealth.failing 同一
# 数值，mac/Sources/LoopHealth.swift）：单次失败可能是瞬时抖动，连续 3 次
# （~30s）说明每轮都在同一处崩（2026-07-06 的 NameError 连崩 15+ pass，只有
# 日志一条 log，用户一周后才发现——这个文件就是那次事故的止血带）。
LOOP_ALARM_AFTER = 3


def _persisted_failures() -> int:
    """盘上计数（缺失/损坏/非法按 0）——重启恰是连崩的标准恢复路径。"""
    try:
        data = json.loads((config.STATE_DIR / LOOP_HEALTH_NAME)
                          .read_text(encoding="utf-8"))
        n = data.get("consecutive_failures")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return n
    except Exception:  # noqa: BLE001 - 诊断文件绝不反杀主循环启动
        pass
    return 0


class LoopHealthTracker:
    """记录主循环 pass 成败并投影到 state/loop_health.json（原子写，绝不抛）。

    形状（add-only，Mac app 只读）：
      {"consecutive_failures": int, "last_error": str|null, "updated_at": iso}
    写盘策略：失败每次都写（计数在涨）；成功只在「上一状态非零」时写一次
    （清零回执）——空闲稳态一个字节都不写，不给 10s 心跳加磁盘开销。
    """

    def __init__(self) -> None:
        # init 继承盘上计数：从 0 起算会让重启后首个成功 pass 撞上 record_success
        # 的稳态 early-return，盘上 consecutive_failures≥3 永不清零、红横幅永久挂着。
        self.consecutive_failures = _persisted_failures()

    def _write(self, error: Optional[str]) -> None:
        try:
            config.ensure_state_dirs()
            path = config.STATE_DIR / LOOP_HEALTH_NAME
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "consecutive_failures": self.consecutive_failures,
                "last_error": (error or "")[:300] or None,
                "updated_at": _dt.datetime.now(_dt.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - health 投影绝不反杀主循环
            pass

    def record_failure(self, error: str) -> None:
        self.consecutive_failures += 1
        self._write(error)

    def record_success(self) -> None:
        if self.consecutive_failures == 0:
            return  # 稳态不写盘
        self.consecutive_failures = 0
        self._write(None)  # 恢复回执 → App 侧红点自动消


def _store2_tick() -> None:
    """§53（D2）：数据层激活/导出的每 pass 钩子——已激活时一次 stat 级开销；
    未激活时尝试「备份 → 迁移 → 导出 → 逐字段比对 → 零差异才写标记」；任何
    差异 = YAML 仍是真源 + 响亮日志 + doctor FAIL。绝不崩 pass（宪法 11）。"""
    try:
        from act.lib.store2 import activate as store2_activate
        for line in store2_activate.tick():
            _log(f"store2: {line}")
    except Exception as e:  # noqa: BLE001 - 数据层钩子绝不反杀主循环
        _log(f"store2 tick FAILED: {e}")


# --------------------------------------------------------------------------- #
# one pass + loop
# --------------------------------------------------------------------------- #
def _refresh_model_knobs(cfg: config.Config) -> None:
    """§59（D22）：把两把模型旋钮从磁盘现读到启动时冻结的 cfg 上——每 pass 一次，
    web 设置页保存后下一 pass 生效、无需重启（雷达/ask/判官/digest 是独立进程，
    本来就每次现读）。做法同 ``auto_resume`` 的现读判定（§16 追记）：只刷这几个
    字段，其余 startup-frozen 语义不动；§70 的五把每日循环旋钮同一刷新点。"""
    try:
        fresh = config.load_config()
    except Exception:  # noqa: BLE001 - 坏 config 不影响本 pass 的其它工作
        return
    cfg.models_dispatch = fresh.models_dispatch
    cfg.models_pipeline = fresh.models_pipeline
    for knob in daily_loop.LIVE_KNOBS:
        setattr(cfg, knob, getattr(fresh, knob))


def _early_dashboard(cfg: config.Config) -> None:
    """write-early：审批/派发刚落账就先写一次 dashboard，app 立刻看到 queued/executing
    回显，不用等 reconcile/raising（都可能慢）跑完；pass 尾部照常再写最终版。"""
    try:
        write_dashboard(build_dashboard(cfg=cfg))
    except Exception as e:  # noqa: BLE001 - early write is best-effort
        _log(f"early dashboard write FAILED: {e}")


def _silent_merge_sweep() -> None:
    try:
        # §44: execute same-thing verdicts in THIS thread (the daemon is the single merge
        # writer — the detached judge is registry-read-only), then fail stuck checks + purge expired jobs.
        from act.lib import silent_merge
        silent_merge.consume_judged()
        silent_merge.sweep()
    except Exception:  # noqa: BLE001 - sweep must not kill the daemon
        pass


def _search_index_prune() -> None:
    try:
        # §37 session-content search layer: drop terminal/absent cards. Cheap:
        # returns immediately when state/search_index.json doesn't exist.
        from act.lib import search_index
        search_index.prune()
    except Exception as e:  # noqa: BLE001 - housekeeping must not kill the pass
        _log(f"search index prune failed: {e}")


def _feedback_sync_sweep(cfg: config.Config) -> None:
    try:
        # 建议公开跟踪表: opted-in feedback records -> GitHub issues. Zero
        # cost with nothing pending; silent no-op without a token file; a
        # broken sync must never take the pass down (same try/except shape
        # as the silent_merge sweep).
        from act.lib import feedback_sync
        feedback_sync.sweep(cfg)
    except Exception:  # noqa: BLE001 - sweep must not kill the daemon
        pass


def _gc_attachments_guarded() -> None:
    try:
        # 贴图附件孤儿清理 — 日频节流；被节流的 pass 只付一次 marker stat()
        gc_attachments()
    except Exception:  # noqa: BLE001 - housekeeping must not kill the pass
        pass


def _housekeeping_phase(cfg: config.Config, interval: Optional[int]) -> None:
    process_raising(cfg)     # expand ONE 'raising' debt per pass (bounded block)
    purge_trash(cfg)
    _sweep_triage_snapshots()   # §34bis: 收不到割的快照侧文件按 pass 清扫
    archive_stale(cfg)       # §4/W1.c: 冷 delivered 卡自动封存（默认 30 天，0=off）
    daily_loop.tick(cfg, interval=interval)   # §70: 到点跑一次「先维护再提案」，自吞异常
    cleanup_merge_jobs()     # §21: TTL sweep + fail stuck 'analyzing' jobs
    self_improve.tick_hook(cfg, log=_log)   # §65.5 lane PR 巡检（自身节流）
    _silent_merge_sweep()
    # §64：待验收卡 AI 摘要 + 完成度评语——同款两段式（detached 判官只读，本线程落卡）；只是建议，永不改 status；绝不抛
    card_summary.tick(cfg)
    if auto_merge is not None:
        # §38/§44: deterministic near-dupe rule for newly appeared open cards
        # → detached silent two-card check (radar cron files cards from
        # outside this process, so "new" is detected by ledger diff).
        auto_merge.scan_new_cards()
    _search_index_prune()
    if feedback is not None:
        # §29: retry pending feedback uploads ONCE, then give up (uploaded:
        # false). Records created THIS pass (process_inbox above already did
        # their inline attempt) are age-gated inside retry_pending, so the
        # single retry lands on a genuinely later pass, not seconds later
        # inside the same outage. Cheap when state/feedback/ is empty.
        feedback.retry_pending(cfg)
    _feedback_sync_sweep(cfg)
    _gc_attachments_guarded()


def _dashboard_phase(cfg: config.Config) -> dict:
    dash = build_dashboard(cfg=cfg)
    # §26 in-app update check: cheap (ETag-cached, at most one network attempt per 24h) and
    # never raises — the field is simply absent when no newer release is known or the check is off.
    if update_check is not None:
        dash = update_check.attach(dash, cfg)
    write_dashboard(dash)
    return dash


def _alerts_phase(prev_dash: Optional[dict], dash: dict, auth_notified: set,
                  radar_dead_notified: Optional[set], interval: Optional[int]) -> None:
    for title, body, rid, kind in detect_transitions(prev_dash, dash):
        notify.notify(title, body, req=rid, kind=kind)
    for title, body in _check_auth_failures(auth_notified):
        notify.notify(title, body)
    # §48 源死亡告警：开着的源超阈值没成功 → 报一次（anti-nag 台账在
    # radar_dead_notified）；dashboard 侧的可见投影在 radar_sources.stale。
    # 巡检内部现读配置（App 翻开关立即生效，不吃启动时冻结的 cfg）。
    for title, body in _check_radar_liveness(
            radar_dead_notified if radar_dead_notified is not None else set(),
            interval=interval):
        notify.notify(title, body)


def run_once(
    cfg: config.Config,
    prev_dash: Optional[dict],
    auth_notified: set,
    resume_notified: Optional[set] = None,
    radar_dead_notified: Optional[set] = None,
    interval: Optional[int] = None,   # 主循环真实 pass 间隔（--interval 优先）
) -> dict:
    config.ensure_state_dirs()
    _refresh_model_knobs(cfg)   # §59：模型旋钮改动下一 pass 生效，无需重启
    # §47.4 心跳：每个阶段边界 touch 一次 state/actd.heartbeat——mtime 是活性
    # 真源，phase 说明循环最后被看见在哪一步（2026-08-31 静默卡死 2.5h 无人知）。
    heartbeat.beat("store2", interval)
    _store2_tick()   # §53 数据层：首跑激活（备份→迁移→比对→标记）+ 每日导出
    heartbeat.beat("inbox", interval)
    n_inbox = process_inbox()
    n_auto = auto_dispatch_pass(cfg)   # §51：hand 卡免批通道（card_sent→approved）
    heartbeat.beat("dispatch", interval)
    n_dispatched = dispatch_approved(cfg)
    # 仅在真有变化时才早写——空闲 pass 不额外跑 build_dashboard（内含 `claude agents`
    # 子进程 + 全量 registry 加载）。
    if n_inbox or n_auto or n_dispatched:
        _early_dashboard(cfg)
    heartbeat.beat("reconcile", interval)
    reconcile_executing(cfg, resume_notified if resume_notified is not None else set())
    heartbeat.beat("housekeeping", interval)
    _housekeeping_phase(cfg, interval)
    heartbeat.beat("dashboard", interval)
    dash = _dashboard_phase(cfg)
    _alerts_phase(prev_dash, dash, auth_notified, radar_dead_notified, interval)
    return dash


def _parse_args(argv: Optional[list]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="actd", description="assistant daemon loop")
    parser.add_argument("--once", action="store_true", help="one pass then exit")
    parser.add_argument("--interval", type=int, default=None, help="override poll seconds")
    return parser.parse_args(argv)


def _startup_config() -> config.Config:
    try:
        return config.load_config()
    except Exception as e:  # noqa: BLE001 — 坏 config.yaml/overrides 绝不拒启：
        # 用内置默认起动并 log 一条（load_config 自身已防崩，这里是纵深防御）
        _log(f"load_config FAILED at startup ({e}); using built-in defaults")
        return config.Config()


def _loop_forever(cfg: config.Config, interval: int, auth_notified: set,
                  resume_notified: set, radar_dead_notified: set) -> None:
    _log(f"actd starting (interval={interval}s, home={config.HOME})")
    prev_dash: Optional[dict] = None
    loop_health = LoopHealthTracker()  # §47.3 连续崩溃可见化
    heartbeat.beat("starting", interval)
    while True:
        try:
            prev_dash = run_once(cfg, prev_dash, auth_notified, resume_notified,
                                 radar_dead_notified, interval=interval)
            loop_health.record_success()
            heartbeat.beat("idle", interval)      # §47.4：pass 完整跑完
        except Exception as e:  # noqa: BLE001 - one bad pass must not kill loop
            loop_health.record_failure(f"{type(e).__name__}: {e}")
            heartbeat.beat("failed", interval)    # 崩了也算活着——循环还在转
            _log(f"loop pass FAILED: {e}\n{traceback.format_exc()}")
        time.sleep(interval)


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    cfg = _startup_config()
    interval = args.interval or cfg.poll_interval_seconds or 10
    auth_notified: set = set()
    resume_notified: set = set()
    radar_dead_notified: set = set()   # §48 anti-nag：每源一次，恢复出账

    if args.once:
        try:
            run_once(cfg, None, auth_notified, resume_notified,
                     radar_dead_notified, interval=interval)
        except Exception as e:  # noqa: BLE001
            _log(f"run_once FAILED: {e}\n{traceback.format_exc()}")
            return 1
        return 0
    _loop_forever(cfg, interval, auth_notified, resume_notified, radar_dead_notified)
    return 0  # pragma: no cover - the loop never returns


if __name__ == "__main__":
    raise SystemExit(main())
