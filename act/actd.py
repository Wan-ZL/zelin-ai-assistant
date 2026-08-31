"""actd — the assistant daemon loop.

Each pass:
  (a) drain STATE/inbox/*.json decisions
        approve  -> status=approved（W17：外部出身未扩写 -> 转 raising）
        reject   -> status=rejected
        comment  -> fold text into plan/notes, keep card_sent (re-approval)
                    ——除 EXECUTING 卡：comment = steer（§44.3-S 中途转向指令，
                    入队等安全窗口 flush 进 live session，状态机零改动）
      delete the decision file after reading it.
  (a') auto-dispatch（§51）：hand 出身的 card_sent 卡过天花板即免批 approved。
  (b) dispatch every status=approved requirement that has no execution yet
      （并发上限内；超出留在合并运行列的 queued 子状态）。
  (c) build + atomically write dashboard.json.
  (d) diff against the previous dashboard; notify on state transitions.

Robust: a single exception never kills the loop; everything is logged to
STATE/actd.log. ``--once`` runs exactly one pass then exits (for tests/cron).

Run: ``python -m act.actd`` (or ``python -m act.actd --once``).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import time
import traceback
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, notify, policy, registry, risk, steer
from act.lib.dashboard import (
    build_dashboard,
    write_dashboard,
    _run_claude_agents,
    _index_agents,
    _DONE_STATES,
    _BLOCKED_STATES,
    _RUNNING_STATES,
)
from act.lib.registry import Requirement, State, load, load_all, save

try:
    from act import executor
except Exception:  # pragma: no cover - executor import must not kill daemon
    executor = None  # type: ignore

try:
    from act import analyze
except Exception:  # pragma: no cover - analyze import must not kill daemon
    analyze = None  # type: ignore


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    config.ensure_state_dirs()
    line = f"{_dt.datetime.now().isoformat(timespec='seconds')}  {msg}\n"
    try:
        with (config.STATE_DIR / "actd.log").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _iso_now() -> str:
    """UTC ISO stamp — the registry-side timestamp format (dashboard 转 epoch)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# (a) inbox
# --------------------------------------------------------------------------- #
def process_inbox() -> int:
    """Apply and delete every inbox decision file. Returns count processed."""
    if not config.INBOX_DIR.exists():
        return 0
    processed = 0
    for path in sorted(config.INBOX_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            decision = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _log(f"inbox: bad decision file {path.name}: {e}")
            _safe_unlink(path)
            continue

        req_id = decision.get("id")
        action = decision.get("action")
        comment = decision.get("comment")

        # §10 capture: no req id — the app popover's one-liner quick capture.
        if action == "capture":
            _apply_capture(decision.get("text"), via=decision.get("via"))
            processed += 1
            _safe_unlink(path)
            continue

        req = load(req_id) if req_id else None

        if req is None:
            _log(f"inbox: decision for unknown req {req_id!r} ({action}) — dropped")
        else:
            # ts 透传（§44.3-S）：steer 的 dedup 键带时间戳——同一 inbox 文件
            # 重放（unlink 失败）同 ts 去重，owner 重申同文新 ts 是新指令。
            # via 透传（T-28 ingress 落款）+ stem（steer dedup 的文件 nonce）。
            _apply_decision(req, action, comment, ts=decision.get("ts"),
                            via=decision.get("via"), stem=path.stem)
            analytics.log_event(f"inbox_{action or 'unknown'}", req=req.id,
                                status=str(req.status))
            processed += 1

        _safe_unlink(path)
    return processed


# T-28 ingress 落款 → 捕获源 channel。无 via = Mac 等 owner-local 写者（HTTP
# 层之外铸的文件才允许缺 via）；via:"web" = localhost 看板，同为 owner。除这
# 两者外一律非 owner：agent 自报落 agent_capture，"remote" 与一切未知/畸形值
# fail-closed 落 remote_capture——两者都是 PROPOSED 级（policy.CHANNEL_CLASS），
# 调度侧出身从 sources 现算，agent/remote 捕获就此结构性关死自动派发。落款是
# 礼仪 + 取证（同用户裸 HTTP 可不发 actor），硬后盾 = 天花板 + effective_tier
# 强制扩写 + 人工审批列（T-28 诚实条款；收紧路径 T-29）。
def _ingress_channel(via: object) -> str:
    if via is None or via == "web":
        return "quick_capture"
    if via == "agent":
        return "agent_capture"
    return "remote_capture"


def _is_owner_ingress(via: object) -> bool:
    """owner-class ingress = Mac 文件（无 via）或 localhost 看板（via:"web"）。"""
    return via is None or via == "web"


def _apply_capture(text: Optional[str], via: Optional[object] = None) -> None:
    """Quick capture from the app popover (CONTRACT §10/§15).

    ``{"action":"capture","text":"...","ts":"..."}`` -> registry.merge_or_new
    (title=text, channel=quick_capture, 原话进 sources) -> status=raising, so the
    existing process_raising() expands it (one per pass) into a card_sent
    proposal. Fast: no LLM call here, the poll loop is never blocked.

    ``via`` 是 HTTP 写入面的 ingress 落款（T-28）：source channel 按
    ``_ingress_channel`` 盖——owner ingress 照旧 quick_capture（HAND），
    agent/remote 落 PROPOSED 级捕获通道，回人工审批。expansion（process_
    raising）不改 sources，章随卡走到调度侧现算。

    Idempotent: merge_or_new dedupes by title, so the same text arriving twice
    merges into the existing entry instead of creating a second card; an entry
    already raised past 'detected' is left in whatever state it reached.
    """
    t = " ".join(str(text or "").split()).strip()
    if not t:
        _log("inbox: capture with empty text — ignored")
        return
    channel = _ingress_channel(via)
    owner = channel == "quick_capture"
    req = Requirement(
        id=registry.next_id(),
        title=t[:80],
        type="other",
        tier="T1",
        status=State.DETECTED.value,
        hardness="soft",
        sources=[{
            "who": "zelin" if owner else
                   ("agent" if channel == "agent_capture" else "remote"),
            "channel": channel,
            "date": _dt.date.today().isoformat(),
            "quote": t,
        }],
        notes="from app quick capture" if owner else f"from {channel}",
    )
    saved = registry.merge_or_new(req)
    if saved.status == State.DETECTED.value:
        saved.set_status(State.RAISING)
        save(saved)
        _log(f"inbox: capture -> {saved.id} raising (queued for AI expansion, "
             f"channel={channel})")
    else:
        _log(f"inbox: capture merged into {saved.id} (status={saved.status}, "
             f"channel={channel})")
    analytics.log_event("inbox_capture", req=saved.id, status=str(saved.status))


def _apply_decision(req: Requirement, action: Optional[str], comment: Optional[str],
                    ts: Optional[object] = None, via: Optional[object] = None,
                    stem: Optional[str] = None) -> None:
    # Full inbox action set (CONTRACT §10) — this elif chain IS the action
    # whitelist/validation; anything else falls through to the logged no-op else:
    #   approve | reject(->trash) | comment | raise(debt->proposal)
    #   | trash(->recycle) | restore(recycle->prev) | pin(recycle->permanent)
    #   | accept(review->delivered) | rework(review->executing)
    #   | done_external(card_sent|review->delivered)          (v0.10.2)
    #   | abort_execution(approved|executing->card_sent)      (v0.10.2)
    #   | revert_review(delivered->review)                    (v0.10.2)
    #   | archive(delivered|detected->archived) | unarchive   (v-next W1.c)
    # v0.10.2 公共规则：状态不匹配的逆向动作 = 幂等 no-op + log（防连点/迟到 inbox）。
    # v-next 中央归档闸（live §4 判例移植）：归档卡的文件在 archive/ ——除
    # unarchive 外任何状态写都会把活状态卡搁浅在归档目录，一律挡成 no-op。
    if str(req.status) == State.ARCHIVED.value and action != "unarchive":
        _log(f"inbox: {req.id} {action} on archived card — no-op (unarchive first)")
        return
    if action == "approve":
        # idempotent: a double-click (or re-approve while already running) must
        # not re-dispatch and spawn a duplicate agent.
        if str(req.status) in (State.APPROVED.value, State.EXECUTING.value,
                               State.REVIEW.value, State.DELIVERED.value):
            _log(f"inbox: {req.id} approve ignored (already {req.status})")
            return
        # W17（amendments §W17/§50）：外部出身卡强制 plan expansion——未经展开
        # （plan/DoD 双空）的 approve 转 raise 走既有扩写管线，绝不裸批。
        et = risk.effective_tier(req)
        if et.forced_expand and not (req.plan or req.definition_of_done):
            if analyze is None:
                # fail-closed：扩写管线不可用时宁可不批（外部卡裸跑正是 W17 要堵的洞）
                _log(f"inbox: {req.id} approve blocked (W17 forced expansion, "
                     f"analyze unavailable) — stays {req.status}")
                return
            if "[W17]" not in (req.notes or ""):
                tag = "[W17] 外部来源强制展开：批准已转为先扩写、复批后才执行"
                req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
            req.set_status(State.RAISING)
            save(req)
            _log(f"inbox: {req.id} approve -> raising (W17 forced expansion, "
                 f"{et.reason})")
            return
        req.set_status(State.APPROVED)
        save(req)
        _log(f"inbox: {req.id} approved")
    elif action == "reject":
        registry.trash(req, "rejected")  # recoverable, not a bare rejected status
        _log(f"inbox: {req.id} rejected -> trash")
    elif action == "comment":
        # T-28 ingress 落款：steer（OWNER UPDATE 直发 live session）与「折叠 +
        # 退回重批」都是 owner 专属动作——agent/remote ingress 的评论只上卡
        # 记录，绝不 steer、绝不动状态机（trust-grant 时刻按 ingress 裁决）。
        if not _is_owner_ingress(via):
            _record_nonowner_comment(req, comment, via)
            return
        if str(req.status) == State.EXECUTING.value:
            # §44.3-S steer relay：运行中卡上的评论是对 live session 的中途
            # 转向指令，不再「折叠 + 退回重批」。入队等 reconcile 的安全窗口
            # （roster blocked / dead-resume）flush；状态机零改动。
            ts_str = str(ts) if isinstance(ts, (str, int, float)) and str(ts).strip() else None
            note = steer.enqueue_steer(req, comment, ts=ts_str, stem=stem)
            if note is None:
                _log(f"inbox: {req.id} steer noop（重放/空文本，未入队）")
                return
            save(req)
            _log(f"inbox: {req.id} comment -> steer queued (key={note['key']})")
            analytics.log_event("inbox_steer", req=req.id)
            return
        _fold_comment(req, comment)
        req.set_status(State.CARD_SENT)  # stays pending, re-approval
        save(req)
        _log(f"inbox: {req.id} comment folded — re-approval pending")
    elif action == "raise":
        if analyze is None:
            _log(f"inbox: {req.id} raise requested but analyze unavailable — ignored")
            return
        # Fast: just mark it 'raising' so it shows a processing spinner in 待审批
        # immediately. The slow claude -p expansion happens in process_raising(),
        # one item per loop pass, so 4 raises don't freeze the daemon for minutes.
        req.set_status(State.RAISING)
        save(req)
        _log(f"inbox: {req.id} -> raising (queued for AI expansion)")
    elif action == "trash":
        registry.trash(req, "deleted")
        _log(f"inbox: {req.id} trashed (deleted)")
    elif action == "restore":
        registry.restore(req)
        _log(f"inbox: {req.id} restored -> {req.status}")
    elif action == "pin":
        registry.pin(req)
        _log(f"inbox: {req.id} pinned permanent")
    elif action == "accept":
        # §11 验收通过 -> delivered（归档）；accepted_at 供 completed 行显示（§2）
        req.set_status(State.DELIVERED)
        ex = dict(req.execution or {})
        ex["accepted_at"] = _iso_now()
        req.execution = ex
        save(req)
        _log(f"inbox: {req.id} accepted -> delivered")
    elif action == "rework":
        # §11 打回：把 Zelin 的反馈送回原 session 继续（executor.rework 处理
        # stop-idle-then-resume），状态回 executing
        if executor is None:
            _log(f"inbox: {req.id} rework requested but executor unavailable — ignored")
            return
        if not (comment or "").strip():
            _log(f"inbox: {req.id} rework with empty feedback — ignored")
            return
        ok = executor.rework(req, comment)
        _log(f"inbox: {req.id} rework sent (ok={ok}) — back to executing")
    elif action == "done_external":
        # v0.10.2 已办完（系统外完成）：card_sent|review -> delivered。有活
        # session 不动它 —— 人做完了，AI 会话自然闲置。
        if str(req.status) not in (State.CARD_SENT.value, State.REVIEW.value):
            _log(f"inbox: {req.id} done_external ignored (status={req.status}) — no-op")
            return
        req.set_status(State.DELIVERED)
        ex = dict(req.execution or {})
        ex["accepted_at"] = _iso_now()
        req.execution = ex
        tag = "[done outside] Zelin 在系统外完成"
        req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
        save(req)
        _log(f"inbox: {req.id} done_external -> delivered")
    elif action == "abort_execution":
        # v0.10.2 停止并退回待审批：approved|executing -> card_sent。活 session
        # 先 best-effort 停止（stop 失败只记日志，绝不阻塞状态回退）；session_id
        # 归档到 aborted_session_id 后删除，保证重新批准时干净重派发。
        if str(req.status) not in (State.APPROVED.value, State.EXECUTING.value):
            _log(f"inbox: {req.id} abort_execution ignored (status={req.status}) — no-op")
            return
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if sid and executor is not None:
            try:
                stopped = executor.stop_session(str(sid))
                _log(f"inbox: {req.id} abort — stop_session({sid}) -> {stopped}")
            except Exception as e:  # noqa: BLE001 - best-effort, never block rollback
                _log(f"inbox: {req.id} abort — stop_session({sid}) failed (ignored): {e}")
        if sid:
            ex["aborted_session_id"] = sid
            ex.pop("session_id", None)
        ex.pop("done", None)
        ex["aborted_at"] = _iso_now()
        req.execution = ex
        req.set_status(State.CARD_SENT)
        save(req)
        _log(f"inbox: {req.id} abort_execution -> card_sent")
    elif action == "revert_review":
        # v0.10.2 退回待验收：delivered -> review（验收撤回）。
        if str(req.status) != State.DELIVERED.value:
            _log(f"inbox: {req.id} revert_review ignored (status={req.status}) — no-op")
            return
        ex = dict(req.execution or {})
        ex.pop("accepted_at", None)
        ex["reverted_at"] = _iso_now()
        req.execution = ex
        req.set_status(State.REVIEW)
        save(req)
        _log(f"inbox: {req.id} revert_review -> review")
    elif action == "archive":
        # v-next W1.c（移植 live §3.7）：封存只从 已验收(delivered)｜备选
        # (detected) 可达；其余状态 = 幂等 no-op。registry.archive RELOCATE
        # 到 archive/ 并盖 prev_status/archived_at/archive_reason。
        if str(req.status) not in (State.DELIVERED.value, State.DETECTED.value):
            _log(f"inbox: {req.id} archive ignored (status={req.status}) — no-op")
            return
        prev = str(req.status)
        registry.archive(req, reason="user")
        _log(f"inbox: {req.id} archived (from {prev})")
    elif action == "unarchive":
        # v-next W1.c：archived -> prev_status，文件搬回 active 目录。
        if str(req.status) != State.ARCHIVED.value:
            _log(f"inbox: {req.id} unarchive ignored (status={req.status}) — no-op")
            return
        registry.unarchive(req)
        _log(f"inbox: {req.id} unarchived -> {req.status}")
    else:
        _log(f"inbox: {req.id} unknown action {action!r} — ignored")


def _record_nonowner_comment(req: Requirement, comment: Optional[str],
                             via: object) -> None:
    """agent/remote 评论的记录面（T-28）：上卡可见（notes），但不折进 plan
    （plan 是喂给 executor 的指令面——非 owner 文本进 plan 就是绕道 steer）、
    不 enqueue steer、不改状态。空文本只记日志；via 进日志供取证。"""
    body = comment.strip() if isinstance(comment, str) else ""
    if not body:
        _log(f"inbox: {req.id} comment (via={via!r}) empty — ignored")
        return
    label = "agent" if via == "agent" else "remote"
    tag = f"[{_dt.date.today().isoformat()} {label} 备注] {body}"
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
    save(req)
    _log(f"inbox: {req.id} comment recorded (via={label}, no steer, "
         f"status stays {req.status})")


def _fold_comment(req: Requirement, comment: Optional[str]) -> None:
    if not comment:
        return
    stamp = _dt.date.today().isoformat()
    tag = f"[{stamp} 修改方向] {comment}"
    # fold into notes; also append as a plan addendum so the executor sees it
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
    if isinstance(req.plan, list):
        req.plan = req.plan + [tag]
    elif req.plan:
        req.plan = str(req.plan) + "\n" + tag
    else:
        req.plan = tag


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# (a') auto-dispatch（§51 · vnext-amendments M1.b/C-6）+ 当日花费台账
# --------------------------------------------------------------------------- #
_SPEND_LEDGER_FILE = "autodispatch_spend.json"


def _load_spend_ledger() -> dict:
    """当日 auto-dispatch 花费台账：``{"date": <本地 YYYY-MM-DD>, "cards":
    {R-id: usd}}``。按本地日期滚动重置（M1.b 接线点②）；坏文件/隔日 = 空账。
    按卡记账所以重启幂等——同卡重记不翻倍。actd 是唯一写者；dashboard 只读。"""
    today = _dt.date.today().isoformat()
    empty: dict = {"date": today, "cards": {}}
    try:
        raw = json.loads((config.STATE_DIR / _SPEND_LEDGER_FILE)
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(raw, dict) or raw.get("date") != today:
        return empty
    cards: dict = {}
    if isinstance(raw.get("cards"), dict):
        for k, v in raw["cards"].items():
            try:
                cards[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return {"date": today, "cards": cards}


def _save_spend_ledger(ledger: dict) -> None:
    try:
        config.ensure_state_dirs()
        path = config.STATE_DIR / _SPEND_LEDGER_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _ledger_spend(ledger: dict, exclude: Optional[str] = None) -> float:
    cards = ledger.get("cards") if isinstance(ledger, dict) else None
    if not isinstance(cards, dict):
        return 0.0
    return float(sum(v for k, v in cards.items() if k != exclude))


def _card_cost(req: Requirement) -> float:
    try:
        return float(str(req.cost_estimate_usd))
    except (TypeError, ValueError):
        return 0.0


def auto_dispatch_pass(cfg: config.Config) -> int:
    """信任矩阵免批通道（owner 拍板 + amendments §51）：hand 出身的 card_sent
    卡全部天花板通过 → 直接 approved（actor=policy）。任一不过 → 留在待审批，
    原因 token 上卡（``execution.auto_dispatch_block``，C-6 定名；origin:*/
    disabled 两类常态原因不上卡不留痕）。并发上限不在资格闸里——那是排队问题，
    归 dispatch_approved / queued_reason（M1.b）。"""
    ad = policy.autodispatch_config(cfg)
    ledger = _load_spend_ledger()
    approved = 0
    for req in sorted(load_all(), key=lambda r: r.id):
        if req.status != State.CARD_SENT.value:
            continue
        try:
            ok, reason = policy.may_auto_dispatch(req, cfg, _ledger_spend(ledger))
            # W17 belt-and-braces：显式 external 章可能比 sources 现算更严
            # （手改 YAML 等）——forced_expand 的卡绝不自动派发。
            if ok and risk.effective_tier(req).forced_expand:
                ok, reason = False, "origin:external"
            ex = dict(req.execution or {})
            if not ok:
                routine = reason == "disabled" or reason.startswith("origin:")
                if routine:
                    if "auto_dispatch_block" in ex:
                        ex.pop("auto_dispatch_block", None)   # 过期 token 清掉
                        req.execution = ex
                        save(req)
                elif ex.get("auto_dispatch_block") != reason:
                    ex["auto_dispatch_block"] = reason
                    req.execution = ex
                    tag = f"[{_dt.date.today().isoformat()} auto-dispatch 拦下] {reason}"
                    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
                    save(req)
                    _log(f"autodispatch: {req.id} blocked ({reason})")
                    analytics.log_event("auto_dispatch_blocked", req=req.id,
                                        reason=reason)
                continue
            cost = _card_cost(req)
            ex.pop("auto_dispatch_block", None)
            ex["auto_dispatched"] = True          # add-only：预算复核/投影用
            req.execution = ex
            tag = (f"[{_dt.date.today().isoformat()} auto-dispatch] "
                   f"hand 出身免批自动派发（est ${cost:g}）")
            req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
            req.set_status(State.APPROVED)
            save(req)
            ledger["cards"][req.id] = cost        # 预算预留在批准时刻
            _save_spend_ledger(ledger)
            approved += 1
            _log(f"autodispatch: {req.id} card_sent -> approved "
                 f"(est ${cost:g}, today ${_ledger_spend(ledger):g}"
                 f"/{ad['daily_budget_usd']:g})")
            analytics.log_event("auto_dispatch", req=req.id, cost=cost,
                                today_spend=_ledger_spend(ledger))
            if ad["notify"]:
                # 观察模式：每次免批派发都出一条通知，owner 随时可关
                # （autodispatch.notify=false）或全关（enabled=false）。
                notify.notify("观察模式：手打卡已自动派发（免批）",
                              req.title or req.id, req=req.id)
        except Exception as e:  # noqa: BLE001 - one bad card must not kill the pass
            _log(f"autodispatch: {getattr(req, 'id', '?')} FAILED: {e}")
    return approved


# --------------------------------------------------------------------------- #
# (b) dispatch approved
# --------------------------------------------------------------------------- #
def dispatch_approved(cfg: config.Config) -> int:
    count = 0
    ad = policy.autodispatch_config(cfg)
    reqs = load_all()
    # 并发口径（M1.b 接线点③）：EXECUTING 且带 session 的卡数。roster 实况
    # reconcile 才查（子进程贵）；按状态机计数是保守方向——死会话短暂占位只
    # 会让排队多等一个 pass。
    live = sum(1 for r in reqs
               if r.status == State.EXECUTING.value
               and (r.execution or {}).get("session_id"))
    for req in reqs:
        if req.status != State.APPROVED.value:
            continue
        if req.execution and req.execution.get("session_id"):
            continue  # already dispatched
        if executor is None:
            _log(f"dispatch: executor unavailable, cannot dispatch {req.id}")
            continue
        # §51 合并运行列 queued 子状态：并发满 → 卡留 approved 排队（原因
        # chip 由 dashboard 的 queued_reason 投影），槽位空出即派发。
        if live >= int(ad["max_concurrent"]):
            continue
        # auto-dispatch 卡的预算复核：批准后 owner 调低预算/隔日翻账等边界，
        # 派发前再验一次天花板。人批的卡不受预算闸——owner 显式点头即 override。
        ex0 = req.execution if isinstance(req.execution, dict) else {}
        if ex0.get("auto_dispatched"):
            ledger = _load_spend_ledger()
            if (_ledger_spend(ledger, exclude=req.id) + _card_cost(req)
                    > float(ad["daily_budget_usd"])):
                continue  # 排队等预算（queued_reason=waiting_budget）
        try:
            executor.dispatch(req, cfg)
            _log(f"dispatch: {req.id} -> executing "
                 f"(session={ (req.execution or {}).get('session_id') })")
            count += 1
            live += 1                    # 本 pass 内并发口径同步推进
            # retry succeeded -> clear the failure left by a previous attempt.
            # (dispatch rebuilds execution so this is usually a no-op; kept as a
            # belt-and-braces so a stale last_error never lingers on a live run.)
            ex = dict(req.execution or {})
            if "last_error" in ex or "last_error_at" in ex:
                ex.pop("last_error", None)
                ex.pop("last_error_at", None)
                req.execution = ex
                save(req)
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            _log(f"dispatch: {req.id} FAILED: {e}\n{traceback.format_exc()}")
            # leave a trace on execution so the dashboard's queued item can show
            # dispatch_error (§2); status stays approved -> auto-retry next pass.
            err = str(e)[:300]
            try:
                ex = dict(req.execution or {})
                ex["last_error"] = err
                ex["last_error_at"] = _iso_now()
                req.execution = ex
                save(req)
            except Exception:  # noqa: BLE001 - bookkeeping must not block retry
                pass
            analytics.log_event("dispatch_failed", req=req.id, error=err[:120])
    return count


# --------------------------------------------------------------------------- #
# (c') trash retention purge (CONTRACT §9)
# --------------------------------------------------------------------------- #
def _parse_iso(ts: Optional[str]) -> Optional[_dt.datetime]:
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = _dt.datetime.strptime(str(ts).strip(), "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def purge_trash(cfg: config.Config) -> int:
    """Hard-delete trashed items older than the retention window.

    Skips items with ``permanent`` set. ``retention_days <= 0`` disables the
    auto-purge entirely. A single bad item never aborts the pass.
    """
    days = int(cfg.trash_retention_days or 0)
    if days <= 0:
        return 0
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    purged = 0
    for req in load_all():
        try:
            if req.status != State.TRASHED.value:
                continue
            if req.permanent:
                continue
            trashed = _parse_iso(req.trashed_at)
            if trashed is None or trashed >= cutoff:
                continue
            if registry.delete(req):
                purged += 1
                _log(f"trash: purged {req.id} (trashed_at={req.trashed_at})")
        except Exception as e:  # noqa: BLE001 - one bad item must not abort the pass
            _log(f"trash: purge failed for {getattr(req, 'id', '?')}: {e}")
    return purged


# --------------------------------------------------------------------------- #
# (c'') auto-archive stale delivered matters（W1.c：默认 30 天，0=off；
#        移植自 live v0.20.0 §4 的 archive_stale，全部保护保留）
# --------------------------------------------------------------------------- #
_ARCHIVE_SWEEP_MARKER = "last_archive_sweep"
_OPEN_STATES = (
    State.DETECTED.value, State.RAISING.value, State.CARD_SENT.value,
    State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value,
)


def _swept_within_last_24h() -> bool:
    """Daily gate: the auto-archive sweep runs at most once per 24h."""
    try:
        p = config.STATE_DIR / _ARCHIVE_SWEEP_MARKER
        if not p.exists():
            return False
        age = _dt.datetime.now(_dt.timezone.utc).timestamp() - p.stat().st_mtime
        return age < 24 * 3600
    except OSError:
        return False


def _mark_swept() -> None:
    try:
        config.ensure_state_dirs()
        (config.STATE_DIR / _ARCHIVE_SWEEP_MARKER).write_text(
            _iso_now(), encoding="utf-8")
    except OSError:
        pass


def _has_future_deadline(req: Requirement) -> bool:
    """带未来 deadline 的 delivered 卡（USCIS/长 matter 里程碑）绝不自动封存
    ——新邮件到来会开出重复卡（这正是本功能要杀的 bug）。"""
    if not req.deadline:
        return False
    try:
        d = _dt.date.fromisoformat(str(req.deadline))
    except ValueError:
        return False
    return d >= _dt.date.today()


def _cluster_has_live_sibling(req: Requirement, all_reqs: list[Requirement]) -> bool:
    """簇内还有 open 卡就不封（绝不封存仍挂着活工作的 matter）。基线差异：
    v0.10.3 无 thread_id 字段——getattr 前向兼容，实际簇判据 = improvement_of
    双向血缘（live 落法时恢复 thread 维度）。"""
    thread = getattr(req, "thread_id", None) or req.id
    for r in all_reqs:
        if r.id == req.id:
            continue
        same_cluster = (
            (getattr(r, "thread_id", None) or r.id) == thread
            or r.improvement_of == req.id
            or req.improvement_of == r.id
        )
        if same_cluster and str(r.status) in _OPEN_STATES:
            return True
    return False


def _thread_last_activity(req: Requirement) -> Optional[_dt.datetime]:
    """卡片最新活动时间（legacy 兜底 = accepted_at 家族）。全都不可解析 →
    None → 永不自动归档（保守：说不清冷不冷的卡不动）。"""
    ex = req.execution if isinstance(req.execution, dict) else {}
    cands = (ex.get("accepted_at"), ex.get("approved_at"),
             ex.get("dispatched_at"), ex.get("review_at"),
             ex.get("reraised_at"))
    dts = [d for d in (_parse_iso(c) for c in cands) if d is not None]
    return max(dts) if dts else None


def archive_stale(cfg: config.Config) -> int:
    """Auto-archive cold DELIVERED cards（W1.c：vnext 默认 30 天，设 0 关闭）。

    每 24h 至多跑一次；只封冷 delivered、跳过未来 deadline、跳过簇内有 open
    sibling 的卡、时间戳不可解析的卡永不自动归档。W1.a 配额反转后冷卡挤占
    closed recency 槽位（20 个），30 天冷封存把窗口留给近期 closed 卡。"""
    days = int(cfg.archive_after_days or 0)
    if days <= 0:
        return 0
    if _swept_within_last_24h():
        return 0
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    reqs = load_all()
    n = 0
    for req in reqs:
        try:
            if req.status != State.DELIVERED.value:
                continue
            if _has_future_deadline(req):
                continue
            if _cluster_has_live_sibling(req, reqs):
                continue
            last = _thread_last_activity(req)
            if last is None or last >= cutoff:
                continue
            registry.archive(req, reason="auto")
            n += 1
            _log(f"archive: auto-archived {req.id} (last activity {last.isoformat()})")
        except Exception as e:  # noqa: BLE001 - one bad item must not abort the pass
            _log(f"archive: auto-archive failed for {getattr(req, 'id', '?')}: {e}")
    _mark_swept()
    return n


# --------------------------------------------------------------------------- #
# (d) transition detection
# --------------------------------------------------------------------------- #
def _by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items if i.get("id")}


def detect_transitions(prev: Optional[dict], curr: dict) -> list[tuple[str, str]]:
    """Return (title, body) notifications for prev->curr transitions."""
    msgs: list[tuple[str, str]] = []
    if prev is None:
        return msgs

    p_na, c_na = _by_id(prev.get("needs_approval", [])), _by_id(curr.get("needs_approval", []))
    p_run = _by_id(prev.get("running", []))
    p_ni, c_ni = _by_id(prev.get("needs_input", [])), _by_id(curr.get("needs_input", []))
    p_rev, c_rev = _by_id(prev.get("review", [])), _by_id(curr.get("review", []))

    # 3-tuples (title, body, req) so Slack ✅-reaction knows which R-id to approve
    # new card_sent
    for rid, item in c_na.items():
        if rid not in p_na:
            t, b = notify.msg_new_card(item.get("title", rid))
            msgs.append((t, b, rid))

    # executing -> review (§11 draft ready, awaiting acceptance)
    for rid, item in c_rev.items():
        if rid not in p_rev and rid in p_run:
            msgs.append(("待验收：AI 已交付草稿", item.get("name") or rid, rid))

    # executing -> blocked (newly needs_input, previously running)
    for rid, item in c_ni.items():
        if rid not in p_ni and rid in p_run:
            t, b = notify.msg_needs_input(item.get("name") or rid)
            msgs.append((t, b, rid))

    return msgs


def _check_auth_failures(notified: set[str]) -> list[tuple[str, str]]:
    """Scan executing items' logs for credential failures (notify once each)."""
    msgs: list[tuple[str, str]] = []
    for req in load_all():
        if req.status != State.EXECUTING.value:
            continue
        if req.id in notified:
            continue
        log = (req.execution or {}).get("log")
        if not log:
            continue
        try:
            text = Path(log).read_text(encoding="utf-8")
        except OSError:
            continue
        if notify.detect_auth_failure(text):
            notified.add(req.id)
            msgs.append(notify.msg_auth(req.title or "claude"))
    return msgs


# --------------------------------------------------------------------------- #
# auto-resume interrupted executing tasks
# --------------------------------------------------------------------------- #
_LIVE_STATES = {
    "working", "running", "executing", "active", "busy", "in_progress", "idle",
}


def _reconcile_review_attach(req: Requirement, agents: dict[str, dict]) -> None:
    """待验收任务的 attach 回流 —— 只动投影层，不动状态机（registry 仍是 review）。

    Zelin 可能 ``claude attach`` 回原 session 继续输入，agent 重新 working：
    - roster working -> 在 execution 里记 ``_review_active=True``。dashboard 的
      分流看的是 roster 实况，这个标记只给 actd 自己做「返工轮结束」判断用；
    - 此前 ``_review_active`` 且现在 done/缺席 -> 这轮返工收工了：重新
      harvest_delivery 刷新 delivered_summary/final_draft（非空才覆盖旧值），
      并清掉标记。blocked 时标记保留（返工中途等输入，还没收工）。
    Best-effort：任何异常吞掉并记日志，绝不影响主循环。
    """
    try:
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if not sid:
            return
        agent = agents.get(str(sid))
        state = (agent or {}).get("state", "") if agent else ""

        if agent and state in _RUNNING_STATES:
            if not ex.get("_review_active"):
                ex["_review_active"] = True
                req.execution = ex
                registry.save(req)
                _log(f"reconcile: {req.id} review-active（attach 回流，agent 重新工作）")
                analytics.log_event("review_active", req=req.id)
            return

        if ex.get("_review_active") and (agent is None or state in _DONE_STATES):
            # 返工轮结束 -> 重新收割交付物（收割失败/为空不覆盖旧值）
            if executor is not None:
                try:
                    harvested = executor.harvest_delivery(str(sid)) or {}
                except Exception as e:  # noqa: BLE001 - harvest is best-effort
                    harvested = {}
                    _log(f"reconcile: re-harvest {req.id} failed: {e}")
                if harvested.get("delivered_summary"):
                    ex["delivered_summary"] = harvested["delivered_summary"]
                if harvested.get("final_draft"):
                    ex["final_draft"] = harvested["final_draft"]
            ex.pop("_review_active", None)
            req.execution = ex
            registry.save(req)
            _log(f"reconcile: {req.id} 返工轮结束，已重新收割交付物")
            analytics.log_event("review_reharvested", req=req.id)
    except Exception as e:  # noqa: BLE001 - must never break the daemon pass
        _log(f"reconcile: review attach check {getattr(req, 'id', '?')} failed: {e}")


def _drop_steers(req: Requirement, pend: list, reason: str, why: str) -> None:
    """诚实丢弃（§39 红线）：留痕 + save + notify + analytics——owner 打的字
    绝不静默蒸发。``why`` 是 analytics 的机读原因（metadata only）。"""
    steer.drop_trace(req, pend, reason)
    registry.save(req)
    notify.notify("追加指令未送达", f"{req.title or req.id}：{reason}", req=req.id)
    analytics.log_event("steer_dropped", req=req.id, n=len(pend), reason=why)
    _log(f"steer: {req.id} dropped {len(pend)} steer(s) — {reason}")


def _flush_steers(req: Requirement, cfg: config.Config) -> None:
    """§44.3-S 安全窗口①（roster blocked）的 steer flush。

    经 rework 同款 stop-idle-then-resume 管道把 OWNER UPDATE 注入原会话：
    blocked 的 bg 进程拒收 --resume，先 stop（安全：transcript 保留）再带
    prompt resume。成功 mark_delivered、失败 record_attempt（3 次放弃 →
    drop 留痕 + 通知）。任何异常都不许打断 reconcile pass。
    基线差异（amendments W-steer）：v0.10.3 无 §44.3 briefing 送达点/
    _briefing_window_open，故 flush 借 executor.resume(prompt=)；v0.47 落法
    时改挂 executor.brief 同一送达点并移植 last-moment fresh roster 探测。
    """
    if executor is None:
        return
    try:
        pend = steer.pending_steers(req)
        if not pend:
            return
        if steer.give_up_due(req):
            _drop_steers(req, pend, "3 次注入尝试失败", "attempts")
            return
        sid = (req.execution or {}).get("session_id")
        if sid:
            try:
                executor.stop_session(str(sid))
            except Exception as e:  # noqa: BLE001 - flush 失败留队，下 pass 重试
                _log(f"steer: {req.id} stop_session failed（下 pass 重试）: {e}")
                steer.record_attempt(req)
                registry.save(req)
                return
        ok = executor.resume(req, cfg, prompt=steer.build_steer_prompt(pend))
        if ok:
            steer.mark_delivered(req, pend)
            registry.save(req)
            _log(f"steer: {req.id} delivered {len(pend)} steer(s)")
            analytics.log_event("steer_delivered", req=req.id, n=len(pend))
        else:
            n = steer.record_attempt(req)
            registry.save(req)
            _log(f"steer: {req.id} flush failed "
                 f"(attempt {n}/{steer.MAX_STEER_ATTEMPTS})")
    except Exception as e:  # noqa: BLE001 - must never break the daemon pass
        _log(f"steer: flush {getattr(req, 'id', '?')} failed: {e}")


def reconcile_executing(cfg: config.Config, resume_notified: set[str]) -> int:
    """Auto-resume executing tasks whose background agent died (sleep / network
    loss / crash). Skips tasks that already finished. Exponential backoff so a
    long offline period (laptop closed, commute with no wifi) resumes cleanly
    once connectivity returns, instead of hammering.
    """
    try:
        agents = _index_agents(_run_claude_agents())
    except Exception:  # noqa: BLE001
        return 0

    # 待验收 attach 回流（§11 补充）：与 auto_resume 开关无关，所以放在开关之前。
    for req in registry.load_all():
        if req.status == registry.State.REVIEW.value:
            _reconcile_review_attach(req, agents)

    if not getattr(cfg, "auto_resume", True):
        return 0

    resumed = 0
    for req in registry.load_all():
        if req.status != registry.State.EXECUTING.value:
            continue
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if not sid:
            continue  # can't safely auto-resume without a session id
        agent = agents.get(str(sid))
        state = (agent or {}).get("state", "") if agent else ""

        if agent and state in _LIVE_STATES:
            if ex.get("resume_attempts"):            # recovered — reset backoff
                ex["resume_attempts"] = 0
                req.execution = ex
                registry.save(req)
            resume_notified.discard(req.id)
            continue
        if agent and state in _BLOCKED_STATES:
            # waiting for the USER to answer (needs input) — NOT dead. Do NOT
            # resume (resuming a blocked agent spawns duplicates). Leave it be.
            if ex.get("resume_attempts"):
                ex["resume_attempts"] = 0
                req.execution = ex
                registry.save(req)
            resume_notified.discard(req.id)
            # §44.3-S 安全窗口①：blocked = 会话停在等输入，此刻 flush steer
            # 不会打断任何在做的工作（working + live pid 绝不打断）。
            _flush_steers(req, cfg)
            continue
        if agent and state in _DONE_STATES:
            if not ex.get("done"):                   # mark finished so a later
                ex["done"] = True                    # purge isn't mistaken for a crash
                ex["review_at"] = _iso_now()         # 进入待验收的时间（§2）
                # 收割交付物：transcript 最后一条 assistant 消息 -> delivered_summary
                # （chat 模式还有 FINAL DRAFT 全文）。收割失败绝不阻塞提升。
                try:
                    harvested = executor.harvest_delivery(str(sid)) or {}
                    if harvested.get("delivered_summary"):
                        ex["delivered_summary"] = harvested["delivered_summary"]
                    if harvested.get("final_draft"):
                        ex["final_draft"] = harvested["final_draft"]
                except Exception as e:  # noqa: BLE001 - harvest is best-effort
                    _log(f"reconcile: harvest_delivery {req.id} failed: {e}")
                req.execution = ex
                # §44.3-S 诚实丢弃：会话已收工，未送达的转向指令再无处送——
                # 留痕 + 通知（notes `[追加指令未送达]`），绝不静默蒸发。
                pend = steer.pending_steers(req)
                if pend:
                    steer.drop_trace(req, pend, "会话已完成进入待验收，追加指令未及送达")
                    notify.notify("追加指令未送达（任务已完成）",
                                  req.title or req.id, req=req.id)
                    analytics.log_event("steer_dropped", req=req.id,
                                        n=len(pend), reason="done")
                # §11: agent done = 草稿就绪，进入待验收（Zelin ✓验收/↩︎打回）。
                # 通知由 detect_transitions 的 running->review diff 发，避免双发。
                req.set_status(registry.State.REVIEW)
                registry.save(req)
                analytics.log_event("review_promoted", req=req.id)
            continue
        if ex.get("done"):
            # finished earlier; agent purged from the list — promote if missed
            if req.status == registry.State.EXECUTING.value:
                req.set_status(registry.State.REVIEW)
                registry.save(req)
            continue

        # dead (failed/stopped) or vanished-before-completing -> resume w/ backoff
        if ex.get("resume_exhausted"):
            continue
        attempts = int(ex.get("resume_attempts", 0))
        if attempts >= 5:
            ex["resume_exhausted"] = True
            req.execution = ex
            registry.save(req)
            notify.notify("自动恢复已放弃（连续失败 5 次），需要人工处理",
                          req.title or req.id)
            analytics.log_event("auto_resume_exhausted", req=req.id)
            continue
        backoff = min(600, 30 * (2 ** min(attempts, 5)))
        last = ex.get("last_resume_at")
        if last:
            try:
                prev = _dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                elapsed = (_dt.datetime.now(_dt.timezone.utc) - prev).total_seconds()
                if elapsed < backoff:
                    continue
            except (ValueError, TypeError):
                pass
        if executor is None:
            continue
        try:
            # §44.3-S 安全窗口②：会话已死的 resume 时机顺带 flush steer——
            # OWNER UPDATE 直接作 resume 首条输入，不额外打断任何活会话。
            pend = steer.pending_steers(req)
            if pend and steer.give_up_due(req):
                _drop_steers(req, pend, "3 次注入尝试失败", "attempts")
                pend = []
            ok = executor.resume(
                req, cfg,
                prompt=steer.build_steer_prompt(pend) if pend else None)
            if pend:
                if ok:
                    steer.mark_delivered(req, pend)
                    registry.save(req)
                    _log(f"steer: {req.id} delivered {len(pend)} steer(s) via resume")
                    analytics.log_event("steer_delivered", req=req.id, n=len(pend))
                else:
                    steer.record_attempt(req)
                    registry.save(req)
            resumed += 1
            _log(f"reconcile: resume {req.id} attempt {attempts + 1} ok={ok}")
            analytics.log_event("auto_resume", req=req.id, ok=ok, attempt=attempts + 1)
            if attempts + 1 >= 3 and req.id not in resume_notified:
                resume_notified.add(req.id)
                notify.notify("任务疑似中断，正在自动恢复", req.title or req.id)
        except Exception as e:  # noqa: BLE001
            _log(f"reconcile: resume {req.id} FAILED: {e}")
    return resumed


# --------------------------------------------------------------------------- #
# raise expansion — ONE per pass (a slow claude -p; don't block on a batch)
# --------------------------------------------------------------------------- #
def process_raising(cfg: config.Config) -> int:
    if analyze is None:
        return 0
    pending = [r for r in registry.load_all()
               if r.status == registry.State.RAISING.value]
    if not pending:
        return 0
    req = sorted(pending, key=lambda r: r.id)[0]
    try:
        analyze.expand_debt(req)  # -> card_sent (or detected+note on failure)
        _log(f"raising: {req.id} expanded -> {req.status}")
        analytics.log_event("raise_expanded", req=req.id, status=str(req.status))
    except Exception as e:  # noqa: BLE001 - one bad expansion can't kill the loop
        _log(f"raising: {req.id} expand FAILED: {e}")
        req.set_status(registry.State.DETECTED)   # fall back so it's not stuck
        req.notes = ((req.notes or "") + " (raise 展开失败，退回欠账)").strip()
        registry.save(req)
    return 1


# --------------------------------------------------------------------------- #
# one pass + loop
# --------------------------------------------------------------------------- #
def run_once(
    cfg: config.Config,
    prev_dash: Optional[dict],
    auth_notified: set[str],
    resume_notified: Optional[set[str]] = None,
) -> dict:
    config.ensure_state_dirs()
    n_inbox = process_inbox()
    n_auto = auto_dispatch_pass(cfg)   # §51：hand 卡免批通道（card_sent→approved）
    n_dispatched = dispatch_approved(cfg)
    # write-early：审批/派发刚落账就先写一次 dashboard，app 立刻看到 queued/executing
    # 回显，不用等 reconcile/raising（都可能慢）跑完；pass 尾部照常再写最终版。
    # 仅在真有变化时才写 —— 空闲 pass 不额外跑一次 build_dashboard（内含
    # `claude agents` 子进程 + 全量 registry 加载，白白翻倍热路径开销）。
    if n_inbox or n_auto or n_dispatched:
        try:
            write_dashboard(build_dashboard(cfg=cfg))
        except Exception as e:  # noqa: BLE001 - early write is best-effort
            _log(f"early dashboard write FAILED: {e}")
    reconcile_executing(cfg, resume_notified if resume_notified is not None else set())
    process_raising(cfg)     # expand ONE 'raising' debt per pass (bounded block)
    purge_trash(cfg)
    archive_stale(cfg)       # W1.c：冷 delivered 卡 30 天自动封存（24h 门）
    dash = build_dashboard(cfg=cfg)
    write_dashboard(dash)

    for title, body, rid in detect_transitions(prev_dash, dash):
        notify.notify(title, body, req=rid)
    for title, body in _check_auth_failures(auth_notified):
        notify.notify(title, body)

    return dash


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="actd", description="assistant daemon loop")
    parser.add_argument("--once", action="store_true", help="one pass then exit")
    parser.add_argument("--interval", type=int, default=None, help="override poll seconds")
    args = parser.parse_args(argv)

    cfg = config.load_config()
    interval = args.interval or cfg.poll_interval_seconds or 10
    auth_notified: set[str] = set()
    resume_notified: set[str] = set()

    if args.once:
        try:
            run_once(cfg, None, auth_notified, resume_notified)
        except Exception as e:  # noqa: BLE001
            _log(f"run_once FAILED: {e}\n{traceback.format_exc()}")
            return 1
        return 0

    _log(f"actd starting (interval={interval}s, home={config.HOME})")
    prev_dash: Optional[dict] = None
    while True:
        try:
            prev_dash = run_once(cfg, prev_dash, auth_notified, resume_notified)
        except Exception as e:  # noqa: BLE001 - one bad pass must not kill loop
            _log(f"loop pass FAILED: {e}\n{traceback.format_exc()}")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
