"""reconcile — auto-resume interrupted executing sessions, harvest finished ones
into 待验收, flush queued steers at the safe windows.

CONTRACT §11（agent done = 草稿就绪进待验收）/ §13 + §46.3（#119：受阻 / 放弃
救活的会话按 stop_to_review 收割进待验收，不再挂「需输入」）/ §16（auto_resume
双键现读）/ §30（待验收 attach 回流不动状态机）/ §34bis（收割时比对快照）/
§37（CARD TITLE + 搜索层）/ §44.3 + §44.3-S（briefing / steer 的安全注入窗口）
/ §46（resume 风暴降级 + 确认式停止）/ §65.3（self_improve 收割核验）。
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Optional

from act.lib import analytics, config, notify, registry, self_improve, steer
from act.lib.actd.seam import Daemon, append_note
from act.lib.actd.session import (apply_harvest_title, fold_harvest, harvest_into,
                                  update_search_index)
from act.lib.actd.triage_guard import (PROPOSALS_TRIAGE_PRESET, check_triage_registry_guard,
                                       stamp_triage_snapshot)
from act.lib.agent_states import BLOCKED_STATES, DONE_STATES, LIVE_STATES, RUNNING_STATES
from act.lib.dashboard import index_agents
from act.lib.maintenance import parse_iso
from act.lib.registry import Requirement

# §46 resume 风暴降级：同一张卡在短窗口内被成功救活（resume/brief 成功启动）
# 达阈值次仍再死 —— 卡死→救→再死的循环没有出口（生产 2026-08-07：R-187 4 分钟
# 三连救；2026-07-29：R-142 13 分钟四连救，attempts 每次被「见到活着」清零，
# 退避永远从零开始）。达阈值即置 resume_exhausted（放弃自动救活的既有账），
# **收割进待验收**（#119：不再投影「需输入」等人回答）。成功启动记录在
# execution.resume_history（ISO 列表，封顶）；失败的启动尝试不入账——那是
# attempts>=5 连续失败分支的地盘（§46.2）。
RESUME_STORM_THRESHOLD = 3          # 窗口内成功救活次数达此值 → 降级
RESUME_STORM_WINDOW_S = 30 * 60     # 风暴判定窗口：30 分钟
RESUME_HISTORY_CAP = 10             # resume_history 保留最近 N 条，防无限增长

# transcript-probe throttle for promote_if_delivered: a genuinely blocked
# agent (no FINAL DRAFT yet) would otherwise get its transcript tail re-read
# every 10 s pass. Process-local is fine — actd is a resident daemon.
HARVEST_PROBE_AT: dict = {}
HARVEST_PROBE_INTERVAL_S = 120.0


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _exec_seconds(ex: dict) -> Optional[int]:
    """dispatch -> delivery wall time (metadata for the review_promoted event)."""
    disp_dt = parse_iso(ex.get("dispatched_at"))
    if disp_dt is None:
        return None
    return max(0, round((_now_utc() - disp_dt).total_seconds()))


def _roster_state(agents: dict, sid) -> tuple:
    """(roster entry | None, its state string — "" when absent)."""
    agent = agents.get(str(sid))
    return agent, ((agent or {}).get("state", "") if agent else "")


# --------------------------------------------------------------------------- #
# 待验收 attach 回流（§30）
# --------------------------------------------------------------------------- #
def reconcile_review_attach(d: Daemon, req: Requirement, agents: dict) -> None:
    """待验收任务的会话活动（attach 回流）—— 不动状态机（registry 仍是 review）。

    Zelin 可能 ``claude attach`` 回原 session 聊天/追问，agent 重新 working。
    这不是返工轮 —— 真返工只从打回 verdict 开始，而打回（executor.rework）会在
    同一调用里写 rework_count/last_rework_at 并把状态置回 executing（§30）：
    - roster working -> 在 execution 里记 ``_review_active=True``。dashboard 的
      分流看的是 roster 实况，这个标记只给 actd 自己做「活动结束」判断用；
    - 此前 ``_review_active`` 且现在 done/缺席 -> 这轮会话活动收工了：重新
      harvest_delivery 刷新 delivered_summary/final_draft（非空才覆盖旧值），
      并清掉标记 —— 终端对话可能产生新交付物，所以照旧收割。blocked 时标记
      保留（等输入，还没收工）。
    Best-effort：任何异常吞掉并记日志，绝不影响主循环。
    """
    try:
        _review_attach(d, req, agents)
    except Exception as e:  # noqa: BLE001 - must never break the daemon pass
        d.log(f"reconcile: review attach check {getattr(req, 'id', '?')} failed: {e}")


def _session_working(agent, state: str) -> bool:
    return bool(agent) and state in RUNNING_STATES


def _activity_ended(agent, state: str) -> bool:
    return agent is None or state in DONE_STATES


def _review_attach(d: Daemon, req: Requirement, agents: dict) -> None:
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    if not sid:
        return
    agent, state = _roster_state(agents, sid)
    if _session_working(agent, state):
        _mark_review_active(d, req, ex)
        return
    if ex.get("_review_active") and _activity_ended(agent, state):
        _settle_review_activity(d, req, ex, sid)


def _mark_review_active(d: Daemon, req: Requirement, ex: dict) -> None:
    if ex.get("_review_active"):
        return
    ex["_review_active"] = True
    _restamp_triage_snapshot(d, req, ex)
    req.execution = ex
    registry.save(req)
    d.log(f"reconcile: {req.id} session-active（attach/会话有新活动，非打回返工）")
    analytics.log_event("review_active", req=req.id)


def _restamp_triage_snapshot(d: Daemon, req: Requirement, ex: dict) -> None:
    """§34bis 复活轮重拍基线：首轮快照已随收割消费（用后即焚），
    attach 复活的仍是同一个带 skip-permissions、握着 registry
    路径的会话——不重拍，本轮活动期间的越权写零告警。复活轮
    是会话先活、快照后拍（夹缝写入进基线）的 best-effort 边界
    （CONTRACT §34bis 记账），与首轮的启动前快照不同。"""
    if getattr(req, "preset", None) == PROPOSALS_TRIAGE_PRESET \
            and not ex.get("registry_snapshot_ref"):
        ref = stamp_triage_snapshot(d, req.id)
        if ref:
            ex["registry_snapshot_ref"] = ref


def _settle_review_activity(d: Daemon, req: Requirement, ex: dict, sid) -> None:
    # 会话活动结束 -> 重新收割交付物（收割失败/为空不覆盖旧值）
    if d.executor is not None:
        try:
            harvested = d.executor.harvest_delivery(str(sid)) or {}
        except Exception as e:  # noqa: BLE001 - harvest is best-effort
            harvested = {}
            d.log(f"reconcile: re-harvest {req.id} failed: {e}")
        fold_harvest(ex, harvested)
        apply_harvest_title(d, req, harvested)   # §37, round boundary
    ex.pop("_review_active", None)
    # §34bis 复活轮收割同样过护栏——比对并消费复活时重拍的快照，
    # 每一轮「活跃→收割」都有基线（非 preset 卡无 ref，零开销）。
    check_triage_registry_guard(d, req, ex)
    req.execution = ex
    registry.save(req)
    update_search_index(d, req.id, sid)          # §37 session-content layer
    d.log(f"reconcile: {req.id} 会话活动结束，已重新收割交付物（attach 回流）")
    analytics.log_event("review_reharvested", req=req.id)


# --------------------------------------------------------------------------- #
# FINAL DRAFT probe（§11 chat 交付的强完成信号）
# --------------------------------------------------------------------------- #
def _probe_throttled(sid) -> bool:
    """One transcript probe per session per HARVEST_PROBE_INTERVAL_S; stamps the probe."""
    now = time.monotonic()
    # None sentinel, NOT 0.0: monotonic() counts from boot, so on a freshly
    # started machine `now - 0.0 < interval` is TRUE for the first minutes —
    # a 0.0 default swallowed the very first probe (surfaced on CI runners,
    # whose uptime is seconds; a just-rebooted Mac would hit it too).
    last = HARVEST_PROBE_AT.get(str(sid))
    if last is not None and now - last < HARVEST_PROBE_INTERVAL_S:
        return True
    HARVEST_PROBE_AT[str(sid)] = now
    return False


def _probe_harvest(d: Daemon, sid) -> dict:
    try:
        return d.executor.harvest_delivery(str(sid)) or {}
    except Exception:  # noqa: BLE001 - the probe is best-effort
        return {}


def promote_if_delivered(d: Daemon, req, ex: dict, sid) -> bool:
    """Promote to 待验收 IFF the transcript carries the standalone FINAL DRAFT
    marker — the chat-delivery contract's STRONG completion signal. A bare
    delivered_summary is any dead session's last words, never proof of
    delivery, so it must not short-circuit a resume. Returns True when
    promoted (callers `continue`).
    """
    if d.executor is None:
        return False
    if _probe_throttled(sid):
        return False
    harvested = _probe_harvest(d, sid)
    if not str(harvested.get("final_draft") or "").strip():
        return False
    _promote_delivered(d, req, ex, sid, harvested)
    return True


def _promote_delivered(d: Daemon, req, ex: dict, sid, harvested: dict) -> None:
    ex["done"] = True
    ex["review_at"] = d.iso_now()
    if harvested.get("delivered_summary"):
        ex["delivered_summary"] = harvested["delivered_summary"]
    ex["final_draft"] = harvested["final_draft"]
    apply_harvest_title(d, req, harvested)   # §37, round boundary
    # §34bis 机械护栏终点：preset 清理卡收割时做起止快照比对。
    check_triage_registry_guard(d, req, ex)
    self_improve.harvest_hook(req, ex, log=d.log)   # §65.3 self_improve 卡：gh 核验
    req.execution = ex
    req.set_status(registry.State.REVIEW)
    registry.save(req)
    update_search_index(d, req.id, sid)      # §37 session-content layer
    analytics.log_event("review_promoted", req=req.id, exec_s=_exec_seconds(ex))
    d.log(f"reconcile: {req.id} promoted to review — transcript already "
          f"carries FINAL DRAFT (session {sid} blocked or purged)")


# --------------------------------------------------------------------------- #
# #119 收割：不再推进的会话按 stop_to_review 落待验收
# --------------------------------------------------------------------------- #
def harvest_to_review(d: Daemon, req: Requirement, ex: dict, sid, note_tag: str,
                      log_reason: str, interrupted_reason: str = "",
                      agent: Optional[dict] = None) -> None:
    """#119（§13/§46.3 v0.48.8）：把一个不再推进的 executing 会话按既有
    stop_to_review 收割路径落进待验收——停 agent（确认式，仅当有活进程）、收下已有成果
    （交付摘要保留会话最后的原话，受阻会话即它的提问原文）、done/review_at
    落账、notes 留痕。``interrupted_reason``（add-only ``execution.
    interrupted_reason``）让 review 投影行带 ``interrupted: true``，
    detect_transitions 据此不再发「AI 已交付草稿」的常规文案（这不是一次
    正常交付）。绝不抛：收割/停止失败都只记日志，状态照落 review。"""
    if sid and d.executor is not None:
        _harvest_stop_index(d, req, ex, sid, log_reason, agent)
    # §34bis 机械护栏终点：收割提升待验收也要比对起止快照（同 stop_to_review）
    check_triage_registry_guard(d, req, ex)
    ex["done"] = True
    ex["review_at"] = d.iso_now()
    if interrupted_reason:
        ex["interrupted_reason"] = interrupted_reason
    self_improve.harvest_hook(req, ex, log=d.log)   # §65.3 核验（失败原因覆盖上面的中断原因）
    req.execution = ex
    append_note(req, note_tag)
    req.set_status(registry.State.REVIEW)
    registry.save(req)
    d.log(f"reconcile: {req.id} {log_reason} -> review（#119 收割）")
    analytics.log_event("review_promoted", req=req.id, exec_s=None)


def _harvest_stop_index(d: Daemon, req: Requirement, ex: dict, sid, log_reason: str,
                        agent: Optional[dict]) -> None:
    err = harvest_into(d, req, ex, sid)
    if err is not None:
        d.log(f"reconcile: {req.id} harvest_delivery({sid}) failed "
              f"(ignored): {err}")
    # 只对确有活进程的会话发确认式停止（受阻会话）——死会话没有可停的
    # 进程，跑 stop 只会在 CI/无 claude 环境制造假 [stop-failed] 台账。
    if (agent or {}).get("pid"):
        d.stop_session_tracked(req, ex, sid, log_reason, log_prefix="reconcile")
    update_search_index(d, req.id, sid)          # §37 session-content layer


# --------------------------------------------------------------------------- #
# §46 resume storm ledger
# --------------------------------------------------------------------------- #
def _in_storm_window(h, now: _dt.datetime) -> bool:
    dt = parse_iso(h if isinstance(h, str) else None)
    return dt is not None and 0 <= (now - dt).total_seconds() <= RESUME_STORM_WINDOW_S


def recent_resume_count(ex: dict, now: Optional[_dt.datetime] = None) -> int:
    """execution.resume_history 里落在风暴窗口内的启动次数（坏条目静默跳过）。"""
    if now is None:
        now = _now_utc()
    hist = ex.get("resume_history")
    if not isinstance(hist, list):
        return 0
    return sum(1 for h in hist if _in_storm_window(h, now))


# --------------------------------------------------------------------------- #
# §44.3-S steer flush
# --------------------------------------------------------------------------- #
def drop_steers(d: Daemon, req: Requirement, pend: list, reason: str, why: str) -> None:
    """诚实丢弃（§39 红线）：留痕 + save + notify + analytics——owner 打的字
    绝不静默蒸发。``why`` 是 analytics 的机读原因（metadata only）。"""
    steer.drop_trace(req, pend, reason)
    registry.save(req)
    notify.notify("追加指令未送达", f"{req.title or req.id}：{reason}", req=req.id)
    analytics.log_event("steer_dropped", req=req.id, n=len(pend), reason=why)
    d.log(f"steer: {req.id} dropped {len(pend)} steer(s) — {reason}")


def flush_steers(d: Daemon, req: Requirement, cfg: config.Config) -> None:
    """§44.3-S 安全窗口①（roster blocked）的 steer flush。

    经 rework 同款 stop-idle-then-resume 管道把 OWNER UPDATE 注入原会话：
    blocked 的 bg 进程拒收 --resume，先 stop（安全：transcript 保留）再带
    prompt resume（executor.resume 的 add-only ``prompt=``）。成功
    mark_delivered、失败 record_attempt（3 次放弃 → drop 留痕 + 通知）。
    任何异常都不许打断 reconcile pass。

    与 §44.3 briefing 共用安全窗口但**永不混批混 prompt**（amendments
    §44.3-S）：blocked 分支里 pending_briefings 先走 executor.brief 并
    continue，steer 等下一个窗口。stop 前借 executor.briefing_window_open
    做 last-moment fresh roster 探测（W-steer 基线差异的 v0.47 落法）——
    pass-start 快照到此刻可能已 blocked→working，窗口关了就留队下 pass，
    不烧尝试次数（那不是一次注入失败）。
    """
    if d.executor is None:
        return
    try:
        pend = steer.pending_steers(req)
        if pend:
            _flush_pending(d, req, cfg, pend)
    except Exception as e:  # noqa: BLE001 - must never break the daemon pass
        d.log(f"steer: flush {getattr(req, 'id', '?')} failed: {e}")


def _flush_pending(d: Daemon, req: Requirement, cfg: config.Config, pend: list) -> None:
    if steer.give_up_due(req):
        drop_steers(d, req, pend, "3 次注入尝试失败", "attempts")
        return
    sid = (req.execution or {}).get("session_id")
    if sid and not _stop_for_steer(d, req, sid):
        return
    _deliver_steers(d, req, cfg, pend)


def _stop_for_steer(d: Daemon, req: Requirement, sid) -> bool:
    """Fresh-window probe + stop; False = leave the queue for the next pass."""
    try:
        if not d.executor.briefing_window_open(sid):
            d.log(f"steer: {req.id} 窗口已关（会话转回 working）— "
                  f"留队下 pass")
            return False
    except Exception:  # noqa: BLE001 - 探测失败按窗口开放（同 brief 姿态）
        pass
    try:
        d.executor.stop_session(str(sid))
    except Exception as e:  # noqa: BLE001 - flush 失败留队，下 pass 重试
        d.log(f"steer: {req.id} stop_session failed（下 pass 重试）: {e}")
        steer.record_attempt(req)
        registry.save(req)
        return False
    return True


def _deliver_steers(d: Daemon, req: Requirement, cfg: config.Config, pend: list) -> None:
    ok = d.executor.resume(req, cfg, prompt=steer.build_steer_prompt(pend))
    if ok:
        steer.mark_delivered(req, pend)
        registry.save(req)
        d.log(f"steer: {req.id} delivered {len(pend)} steer(s)")
        analytics.log_event("steer_delivered", req=req.id, n=len(pend))
    else:
        n = steer.record_attempt(req)
        registry.save(req)
        d.log(f"steer: {req.id} flush failed "
              f"(attempt {n}/{steer.MAX_STEER_ATTEMPTS})")


# --------------------------------------------------------------------------- #
# the pass: reconcile every executing card
# --------------------------------------------------------------------------- #
def reconcile_executing(d: Daemon, cfg: config.Config, resume_notified: set) -> int:
    """Auto-resume executing tasks whose background agent died (sleep / network
    loss / crash). Skips tasks that already finished. Exponential backoff so a
    long offline period (laptop closed, commute with no wifi) resumes cleanly
    once connectivity returns, instead of hammering.
    """
    try:
        agents = index_agents(d.run_claude_agents())
    except Exception:  # noqa: BLE001
        return 0
    # 待验收 attach 回流（§11 补充）：与 auto_resume 开关无关，所以放在开关之前。
    _attach_review_cards(d, agents)
    if not _auto_resume_on():
        return 0
    return sum(_reconcile_one(d, req, cfg, agents, resume_notified)
               for req in registry.load_all()
               if req.status == registry.State.EXECUTING.value)


def _attach_review_cards(d: Daemon, agents: dict) -> None:
    for req in registry.load_all():
        if req.status == registry.State.REVIEW.value:
            reconcile_review_attach(d, req, agents)


def _auto_resume_on() -> bool:
    """开关取两处的 AND（键位漂移修复）：config.yaml `execution.auto_resume`
    与 §16 feature flag `features.auto_resume`（Settings 开关写的是后者，
    经 settings_overrides 落进 cfg.features）——任一为 false 即关。两键默认
    都是 true，老配置行为不变（add-only 精神）。
    判定走新鲜读取（每 pass 直接重读一次配置）而非 actd 启动时冻结的
    cfg——Settings 翻开关下一个 reconcile pass 就生效、对任意 --interval
    成立，无需重启（§16 追记）。不走任何 TTL 缓存：interval 可以小于任何
    TTL，缓存会把「下一 pass 生效」变成盲窗；一 pass 一次 parse 代价可
    忽略。其余 startup-frozen 语义不动，只有这一个判定点吃新鲜值。"""
    fresh = config.load_config()
    return bool(getattr(fresh, "auto_resume", True) and fresh.feature("auto_resume"))


def _agent_class(agent) -> str:
    """roster entry → live | blocked | done | absent（dead / vanished）."""
    if not agent:
        return "absent"
    state = agent.get("state", "")
    if state in LIVE_STATES:
        return "live"
    if state in BLOCKED_STATES:
        return "blocked"
    return "done" if state in DONE_STATES else "absent"


def _reconcile_one(d: Daemon, req: Requirement, cfg: config.Config, agents: dict,
                   resume_notified: set) -> int:
    """One executing card → 1 when a resume was launched this pass, else 0."""
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    if not sid:
        return 0  # can't safely auto-resume without a session id
    agent = agents.get(str(sid))
    handler = _BY_ROSTER_CLASS.get(_agent_class(agent))
    if handler is not None:
        handler(d, req, ex, sid, cfg, agent, resume_notified)
        return 0
    if ex.get("done"):
        _promote_if_missed(req)
        return 0
    return _revive_dead(d, req, ex, sid, cfg, resume_notified)


def _note_alive(d: Daemon, req: Requirement, ex: dict, sid, cfg, agent, resume_notified: set) -> None:
    if ex.get("resume_attempts"):            # recovered — reset backoff
        ex["resume_attempts"] = 0
        req.execution = ex
        registry.save(req)
    resume_notified.discard(req.id)


def _handle_blocked(d: Daemon, req: Requirement, ex: dict, sid, cfg, agent,
                    resume_notified: set) -> None:
    """受阻会话（#119，v0.48.8）：不再挂「需输入」等人回答。FIRST check
    for a completed delivery: a chat-mode agent that printed its
    FINAL DRAFT block settles in exactly this waiting-input state
    (a bg session never exits on its own), and 2026-07-14 R-041 sat
    here for hours with the finished brief already in the
    transcript while the board said 需输入."""
    if not ex.get("done") and d.promote_if_delivered(req, ex, sid):
        return
    # §44.3: a blocked session is the safe injection window — flush
    # any queued silent-merge briefings (stop-idle-then-resume; the
    # resumed session un-blocks as a bonus). 注入队列非空时先注入——
    # briefing/steer 本身就可能让会话继续推进，不急着收割。
    if _flush_briefings(d, req, ex, cfg):
        return
    if steer.pending_steers(req):
        # §44.3-S 安全窗口①：blocked 时 flush steer 不打断工作。
        flush_steers(d, req, cfg)
        return
    # §13/§46.3 v0.48.8（#119）：没有任何待注入的内容、会话又不再
    # 推进 —— 按既有 stop_to_review 收割路径落待验收：停 agent、
    # 收下成果（交付摘要自然保留会话最后的提问原文），用户在待验收
    # 用「打回 + 修改方向」回答并继续，或直接验收/丢弃。
    harvest_to_review(d, req, ex, sid,
                      "[会话受阻] 会话停在等待输入，已收割进待验收——"
                      "用「打回」附一句话即可回答并继续",
                      "blocked, harvested to review",
                      interrupted_reason="blocked", agent=agent)
    notify.notify(*notify.msg_review_interrupted(req.title or req.id),
                  req=req.id)
    resume_notified.discard(req.id)


def _flush_briefings(d: Daemon, req: Requirement, ex: dict, cfg: config.Config) -> bool:
    if not (ex.get("pending_briefings") and d.executor is not None):
        return False
    try:
        d.executor.brief(req, cfg)
    except Exception as e:  # noqa: BLE001 - FYI only, never fatal
        d.log(f"reconcile: brief {req.id} failed: {e}")
    return True


def _handle_done(d: Daemon, req: Requirement, ex: dict, sid, cfg, agent, resume_notified: set) -> None:
    if ex.get("done"):
        return
    ex["done"] = True                    # mark finished so a later purge isn't mistaken for a crash
    ex["review_at"] = d.iso_now()        # 进入待验收的时间（§2）
    # 收割交付物：transcript 最后一条 assistant 消息 -> delivered_summary
    # （chat 模式还有 FINAL DRAFT 全文）。收割失败绝不阻塞提升。
    err = harvest_into(d, req, ex, sid)
    if err is not None:
        d.log(f"reconcile: harvest_delivery {req.id} failed: {err}")
    # §34bis 机械护栏终点：preset 清理卡收割时做起止快照比对。
    check_triage_registry_guard(d, req, ex)
    self_improve.harvest_hook(req, ex, log=d.log)   # §65.3 self_improve 卡：gh 核验
    req.execution = ex
    _drop_undelivered_steers(d, req)
    # §11: agent done = 草稿就绪，进入待验收（Zelin ✓验收/↩︎打回）。
    # 通知由 detect_transitions 的 running->review diff 发，避免双发。
    req.set_status(registry.State.REVIEW)
    registry.save(req)
    update_search_index(d, req.id, sid)          # §37 session-content layer
    # exec_s (metadata): dispatch -> delivery wall time. No
    # summary excerpt anymore (v0.18): delivered_summary is MODEL
    # OUTPUT, which telemetry never stores at any setting
    # (docs/TELEMETRY.md red line) — the pre-v0.18 detailed-level
    # summary field is retired, not moved behind capture_input.
    analytics.log_event("review_promoted", req=req.id, exec_s=_exec_seconds(ex))


def _drop_undelivered_steers(d: Daemon, req: Requirement) -> None:
    """§44.3-S 诚实丢弃（窗口③）：会话已收工，未送达的转向指令再
    无处送——留痕 + 通知（notes `[追加指令未送达]`），绝不静默蒸发。"""
    pend = steer.pending_steers(req)
    if pend:
        steer.drop_trace(req, pend, "会话已完成进入待验收，追加指令未及送达")
        notify.notify("追加指令未送达（任务已完成）",
                      req.title or req.id, req=req.id)
        analytics.log_event("steer_dropped", req=req.id,
                            n=len(pend), reason="done")


_BY_ROSTER_CLASS = {"live": _note_alive, "blocked": _handle_blocked, "done": _handle_done}


def _promote_if_missed(req: Requirement) -> None:
    # finished earlier; agent purged from the list — promote if missed
    if req.status == registry.State.EXECUTING.value:
        req.set_status(registry.State.REVIEW)
        registry.save(req)


# --------------------------------------------------------------------------- #
# dead / vanished sessions: harvest or resume with backoff
# --------------------------------------------------------------------------- #
def _revive_dead(d: Daemon, req: Requirement, ex: dict, sid, cfg: config.Config,
                 resume_notified: set) -> int:
    # dead (failed/stopped) or vanished-before-completing. BEFORE burning
    # a resume, check the transcript for a completed delivery: a session
    # that finishes while the Mac sleeps is purged from the roster before
    # any reconcile pass ever sees it in a done state (2026-07-14 R-041),
    # and resuming a finished session only spawns a confused duplicate.
    if d.promote_if_delivered(req, ex, sid):
        return 0
    if _gave_up(d, req, ex, sid):
        return 0
    attempts = int(ex.get("resume_attempts", 0))
    if _in_backoff(ex, attempts) or d.executor is None:
        return 0
    return _resume(d, req, ex, cfg, attempts, resume_notified)


def _gave_up(d: Daemon, req: Requirement, ex: dict, sid) -> bool:
    """The three「不再救活」exits, each harvesting the card into 待验收."""
    if ex.get("resume_exhausted"):
        # #119：历史上放弃救活的卡曾长期停在 executing 装「需输入」——
        # 现在一律收割进待验收（升级前遗留的卡也在这条路上迁移出来）。
        # 降级那一刻已发过精确通知（msg_resume_storm / exhausted），
        # 这里不再重复 ping。
        harvest_to_review(d, req, ex, sid,
                          "[自动恢复已放弃] 已收割进待验收——验收、丢弃，"
                          "或「打回」附一句话让它继续",
                          "resume exhausted, harvested to review",
                          interrupted_reason="resume_exhausted")
        return True
    # §46 resume 风暴降级：窗口内已成功救活 N 次还是死了 —— 停止无限救活。
    # 与下方 attempts>=5 的「连续失败」放弃互补：风暴计数只数成功启动
    # （救活后短命再死也算），attempts 被「见到活着」清零骗不过它；
    # 连续失败启动则只走 attempts 路径，网络抖动 3 连败不该永久降级。
    storm_n = recent_resume_count(ex)
    if storm_n >= RESUME_STORM_THRESHOLD:
        _storm_degrade(d, req, ex, sid, storm_n)
        return True
    if int(ex.get("resume_attempts", 0)) >= 5:
        _exhaust_after_failures(d, req, ex, sid)
        return True
    return False


def _storm_degrade(d: Daemon, req: Requirement, ex: dict, sid, storm_n: int) -> None:
    ex["resume_exhausted"] = True
    ex["resume_storm_at"] = d.iso_now()
    tag = (f"[resume-storm] 30 分钟内自动救活 {storm_n} 次后会话再次"
           "中断，已停止自动恢复并收割进待验收（#119）——验收、丢弃，"
           "或「打回」附一句话让它继续")
    harvest_to_review(d, req, ex, sid, tag,
                      f"resume storm ({storm_n} revivals)",
                      interrupted_reason="resume_storm")
    notify.notify(*notify.msg_resume_storm(req.title or req.id, storm_n),
                  req=req.id)
    analytics.log_event("resume_storm_degraded", req=req.id, n=storm_n)


def _exhaust_after_failures(d: Daemon, req: Requirement, ex: dict, sid) -> None:
    ex["resume_exhausted"] = True
    harvest_to_review(d, req, ex, sid,
                      "[自动恢复已放弃] 连续 5 次拉起失败，已收割进"
                      "待验收（#119）——验收、丢弃，或「打回」附一句话"
                      "让它继续",
                      "auto-resume exhausted (5 failures)",
                      interrupted_reason="resume_exhausted")
    # §5 v0.14 copy: bilingual + names the exact card buttons to press
    notify.notify(*notify.msg_auto_resume_exhausted(req.title or req.id),
                  req=req.id)
    analytics.log_event("auto_resume_exhausted", req=req.id)


def _in_backoff(ex: dict, attempts: int) -> bool:
    backoff = min(600, 30 * (2 ** min(attempts, 5)))
    last = ex.get("last_resume_at")
    if not last:
        return False
    try:
        prev = _dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        elapsed = (_now_utc() - prev).total_seconds()
        return elapsed < backoff
    except (ValueError, TypeError):
        return False


def _resume(d: Daemon, req: Requirement, ex: dict, cfg: config.Config, attempts: int,
            resume_notified: set) -> int:
    resumed = 0
    try:
        req, ok = _launch_resume(d, req, ex, cfg)
        if req is None:
            return 0
        _book_resume(d, req, attempts, ok)
        resumed = 1
        _announce_resume(d, req, attempts, ok, resume_notified)
    except Exception as e:  # noqa: BLE001
        d.log(f"reconcile: resume {req.id} FAILED: {e}")
    return resumed


def _launch_resume(d: Daemon, req: Requirement, ex: dict, cfg: config.Config) -> tuple:
    """→ (card to book against | None, ok)."""
    if ex.get("pending_briefings"):
        # §44.3: a dead session with queued briefings — resume WITH the
        # briefing prompt instead of a bare resume (one launch, two jobs).
        ok = d.executor.brief(req, cfg)
        # brief 内部走 _rebook（重读卡片再落盘新 session_id/清队列），
        # 传入的 req 仍是启动前的旧快照——必须从盘上重读再记账，否则
        # 下面的 save 会用旧 execution 把 brief 刚写的账整个回滚
        # （旧 session_id 复活 → 每个 pass 重复起会话）。
        fresh = registry.load(req.id)
        if fresh is None:
            # 重读失败（坏 yaml/竞态）也不许拿旧快照垫底——save 同样
            # 会回滚 brief 的账。本轮跳过记账（风暴账少记一条无害），
            # 下 pass 重试。
            d.log(f"reconcile: {req.id} reload after brief failed — "
                  "skipping bookkeeping this pass")
            return None, ok
        return fresh, ok
    return req, _resume_with_steers(d, req, cfg)


def _resume_with_steers(d: Daemon, req: Requirement, cfg: config.Config) -> bool:
    """§44.3-S 安全窗口②：会话已死的 resume 时机顺带 flush steer——
    OWNER UPDATE 直接作 resume 首条输入，零额外打断。briefing
    分支在上面先行（永不混批）；steer 等它清完队再搭下一班车。"""
    pend = steer.pending_steers(req)
    if pend and steer.give_up_due(req):
        drop_steers(d, req, pend, "3 次注入尝试失败", "attempts")
        pend = []
    # 无 steer 时不带 prompt 形参——裸 resume 路径与从前逐字节
    # 相同（add-only 纪律：老注入缝/老 mock 一概不受扰动）。
    if pend:
        ok = d.executor.resume(req, cfg, prompt=steer.build_steer_prompt(pend))
        _settle_steers(d, req, pend, ok)
        return ok
    return d.executor.resume(req, cfg)


def _settle_steers(d: Daemon, req: Requirement, pend: list, ok) -> None:
    if ok:
        steer.mark_delivered(req, pend)
        d.log(f"steer: {req.id} delivered {len(pend)} steer(s) "
              f"via resume")
        analytics.log_event("steer_delivered", req=req.id,
                            n=len(pend))
    else:
        steer.record_attempt(req)


def _book_resume(d: Daemon, req: Requirement, attempts: int, ok) -> None:
    ex_after = dict(req.execution or {})
    if not ok:
        _record_failed_attempt(d, ex_after, attempts)
    else:
        _record_revival(d, ex_after)
    req.execution = ex_after
    registry.save(req)


def _record_failed_attempt(d: Daemon, ex_after: dict, attempts: int) -> None:
    """executor.resume's early-return paths (transcript purged, mkdir
    failed) record NO bookkeeping — without it attempts stays 0
    forever: the exhaustion notification never fires and the
    resume+log+analytics burst repeats every 10s pass with zero
    backoff (audit 2026-07-15). Count the failed attempt here iff
    resume didn't already (its post-launch bookkeeping did)."""
    if int(ex_after.get("resume_attempts", 0) or 0) == attempts:
        ex_after["resume_attempts"] = attempts + 1
        ex_after["last_resume_at"] = d.iso_now()
        ex_after["last_resume_ok"] = False


def _record_revival(d: Daemon, ex_after: dict) -> None:
    """§46 风暴台账：只记「成功启动」（resume 或 brief）——存活即被
    清零的 resume_attempts 骗得过退避、骗不过这本账。失败的启动
    尝试归 attempts>=5 的连续失败分支管：把失败也记进风暴账，
    一次网络抖动 3 连败就永久降级，5 连败分支也成了死代码。"""
    raw = ex_after.get("resume_history")
    hist = [str(h) for h in (raw or []) if h] if isinstance(raw, list) else []
    hist.append(d.iso_now())
    ex_after["resume_history"] = hist[-RESUME_HISTORY_CAP:]


def _announce_resume(d: Daemon, req: Requirement, attempts: int, ok, resume_notified: set) -> None:
    d.log(f"reconcile: resume {req.id} attempt {attempts + 1} ok={ok}")
    analytics.log_event("auto_resume", req=req.id, ok=ok, attempt=attempts + 1)
    if attempts + 1 >= 3 and req.id not in resume_notified:
        resume_notified.add(req.id)
        notify.notify(*notify.msg_resuming(req.title or req.id))
