"""inbox — (a) drain ``state/inbox/*.json`` decision files, one terminal
disposition per file (CONTRACT §5.4 ack ledger / §10 inbox action set / §22
session import / §29 feedback / §33 boundary doctrine / §34 direct-run capture /
§34bis preset capture / §37 set_title / §38 split_note / §53.5 actor wall /
T-28 ingress 落款).

Robust: a poison file (bad JSON, non-object, wrong field types, a guard
regression deep in the apply path) must end terminally for THAT file only —
ack + delete — or, processed in mtime order, it re-crashes every pass and
wedges the whole inbox (nightly audit 2026-07-14). Card-level verbs
(approve / reject / comment / …) live in :mod:`act.lib.actd.decisions`.
"""
from __future__ import annotations

import datetime as _dt
import json
import traceback
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, registry
from act.lib.actd import merge as _merge
from act.lib.actd import triage_guard
from act.lib.actd.seam import Daemon
from act.lib.registry import Requirement, State

# §53.5 agent 墙的错误形（store2.TransitionDenied）——inbox 面把它按干净 no-op
# 处理（不是 poison 文件）。stdlib-only 模块，导入失败只可能是打包损坏。
try:
    from act.lib.store2.store import TransitionDenied
except Exception:  # pragma: no cover - degrade：墙错误按普通异常走 poison 路径
    class TransitionDenied(Exception):  # type: ignore[no-redef]
        pass


# --------------------------------------------------------------------------- #
# the drain loop
# --------------------------------------------------------------------------- #
def process_inbox(d: Daemon) -> int:
    """Apply and delete every inbox decision file. Returns count processed."""
    if not config.INBOX_DIR.exists():
        return 0
    processed = 0
    for path in sorted(config.INBOX_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        decision = _read_decision(d, path)
        if decision is None:
            continue
        try:
            status, counted = _route(d, path, decision)
            d.write_applied_ack(path.stem, status)
            processed += counted
            d.safe_unlink(path)
        except Exception as e:  # noqa: BLE001 - one poison file must never wedge the inbox
            # ANY per-file crash (field-type poison, guard regression) must end
            # terminally for THIS file only — ack + delete, exactly like the
            # non-dict guard — or the file re-crashes every mtime-ordered
            # pass and freezes the whole pipeline behind it.
            d.log(f"inbox: decision file {path.name} crashed apply "
                  f"({type(e).__name__}: {e}) — discarding\n{traceback.format_exc()}")
            d.write_applied_ack(path.stem, "bad_json")
            d.safe_unlink(path)
    return processed


def _read_decision(d: Daemon, path: Path) -> Optional[dict]:
    """Parse one decision file; unreadable / non-object files are acked
    ``bad_json`` and deleted here (None = already disposed of)."""
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        d.log(f"inbox: bad decision file {path.name}: {e}")
        # §5.4 ack: a terminal disposition even when unreadable, so the phone
        # never sees a stuck 'delivered' → false "未送达" retry loop.
        return _discard(d, path)
    if not isinstance(decision, dict):
        # legal JSON but not an object (null/number/string/list): treating
        # it like a decision would AttributeError OUTSIDE any guard, the
        # file would survive, and — processed in mtime order — the poison
        # file would re-crash every pass, wedging the whole inbox
        # (nightly audit 2026-07-14, blocker).
        d.log(f"inbox: decision file {path.name} is not a JSON object "
              f"({type(decision).__name__}) — discarding")
        return _discard(d, path)
    return decision


def _discard(d: Daemon, path: Path) -> None:
    d.write_applied_ack(path.stem, "bad_json")
    d.safe_unlink(path)
    return None


def _route(d: Daemon, path: Path, decision: dict) -> tuple:
    """Dispatch one decision → (result_status, counted). Special forms carry
    no requirement-level id and never reach the card lookup; everything else
    is a card verb (or an unknown card → ack ``unknown``, not counted)."""
    action = decision.get("action")
    special = _SPECIAL_ACTIONS.get(action)
    if special is not None:
        return special(d, path.stem, decision), 1
    # detached special forms (no req id): weekly digest on demand (§24,
    # Settings「现在生成一份」), the §63 recap buttons and the §48.7 radar
    # 「立即测试一轮」— each spawns a subprocess so a minutes-long claude /
    # network call never blocks the pass.
    if action in d.detached_actions:
        return d.detached_actions[action](decision), 1
    return _route_card_verb(d, path, decision, action)


def _route_card_verb(d: Daemon, path: Path, decision: dict, action) -> tuple:
    req_id = decision.get("id")
    # §60.3：id 可以是主键或工作编号（web 显示的是后者）；此后一律用 req.id
    req = registry.resolve(req_id) if req_id else None
    if req is None:
        d.log(f"inbox: decision for unknown req {req_id!r} ({action}) — dropped")
        # §5.4 ack: the card is gone → the phone must be told "该卡已不存在"
        # (result_status=unknown), never left guessing on a stuck 'delivered'.
        return "unknown", 0
    # webui/syncd forward `comment` verbatim from the wire, so a
    # non-string here would AttributeError deep inside the apply path
    # AFTER state changes landed — the file would survive and re-crash
    # every mtime-ordered pass (the non-dict poison class, one field
    # deeper). Never trust wire field types: coerce to None.
    comment = decision.get("comment")
    if not isinstance(comment, str):
        comment = None
    result_status = _apply_card_verb(d, path, decision, req, action, comment)
    _log_inbox_event(req, action, comment)
    # §5.4 ack: durable "did it land?" truth — running (applied a real
    # change) | noop (stale/idempotent guard) | unknown (bad action).
    return result_status, 1


def _log_inbox_event(req: Requirement, action, comment: Optional[str]) -> None:
    # the comment (打回反馈/修改方向) is user-typed content —
    # attached only behind the capture_input gate, clipped.
    c = (comment or "").strip()
    analytics.log_event(
        f"inbox_{action or 'unknown'}", req=req.id,
        status=str(req.status), has_comment=bool(c) or None,
        comment=(analytics.clip_content(c)
                 if c and analytics.content_gate() else None))


def _apply_card_verb(d: Daemon, path: Path, decision: dict, req: Requirement,
                     action, comment: Optional[str]) -> str:
    # §53.5 actor 语义：inbox 决策的发起者——owner 面（Mac/web/
    # 手机同步）= user；agent 通道（via:"agent"）= agent，store2
    # 的 agent 墙（AGENT_TRANSITION_FORBIDDEN）就在这里成为 actd
    # 级现实（R2.1.4）：agent 的 approve/accept 在 save 处被拒。
    if action == "set_title":
        # §37: carries a `title` field the generic decision path
        # doesn't know about — validated fail-closed in the helper.
        return apply_with_actor(d, decision, apply_set_title, d, req, decision.get("title"))
    # ts 透传（§44.3-S）：steer 的 dedup 键带时间戳——同一
    # inbox 文件重放（unlink 失败）同 ts 去重，owner 重申同文
    # 新 ts 是新指令。via 透传（T-28 ingress 落款）+ stem
    # （steer dedup 的文件 nonce）。
    # §5.4 sync preconditions carried by the phone (absent for Mac-app files):
    # expected_status pins the card state the phone SAW, board_seq the board
    # revision — a stale action whose precondition no longer holds is a no-op.
    return apply_with_actor(
        d, decision, d.apply_decision,
        req, action, comment, decision.get("expected_status"), decision.get("board_seq"),
        ts=decision.get("ts"), via=decision.get("via"), stem=path.stem)


# --------------------------------------------------------------------------- #
# special forms（no requirement-level id）
# --------------------------------------------------------------------------- #
def _capture(d: Daemon, stem: str, decision: dict) -> str:
    """§10 capture: no req id — the app popover's one-liner quick capture.
    v0.34.0: optional mode="run" (运行中 lane input) skips the proposal
    gate — the card is filed straight into the approved queue.
    贴图 (建议 #5, add-only): optional images = absolute PNG paths the
    app saved under state/attachments/."""
    # §34bis 提案积压清理按钮：preset 只认词表内的值且必须携带
    # mode:"run" —— 任何其它 preset 值/类型、或缺 run，一律
    # 完全忽略 preset（fail-safe 走该 capture 原本的路径，
    # 垃圾 preset 绝不静默替换任务内容）。
    cap_plan = None
    if _is_triage_preset(decision):
        # §34bis 在途判重：已有未完结的清理会话卡（approved/
        # executing）→ 不铸新卡，ack "running"（那轮清理真在
        # 队列/在跑，诚实回执）。独立于 merge_or_new 的折叠
        # 分支 —— §34.1（[run] 一律新卡）合入后依旧成立；
        # Swift 2s 冷却只是 UI 层辅助，这里才是真防双开。
        if triage_guard.proposals_triage_in_flight():
            d.log("inbox: preset capture skipped — a proposals-"
                  "triage session is already queued/running")
            return "running"
        cap_plan = triage_guard.proposals_triage_plan()
    return d.apply_capture(
        decision.get("text"), decision.get("mode"),
        decision.get("images"), plan=cap_plan,
        preset=triage_guard.PROPOSALS_TRIAGE_PRESET if cap_plan else None,
        inbox_stem=stem, via=decision.get("via"))


def _is_triage_preset(decision: dict) -> bool:
    """T-28：preset 注入固定 plan + 直跑，是 owner 特权面（Mac 按钮
    /本地看板）——agent/remote ingress 的 preset 一律当普通
    capture 处理（server 层对 actor+preset 已 400，这里是 actd
    的 fail-closed 硬后盾）。"""
    return (decision.get("preset") == triage_guard.PROPOSALS_TRIAGE_PRESET
            and decision.get("mode") == "run"
            and is_owner_ingress(decision.get("via")))


def _split_note(d: Daemon, stem: str, decision: dict) -> str:
    # §38 split_note (拆成新卡): carries id + note_ts (the fold-note
    # line's ts tag) — the reversible-fold undo, own branch because of
    # the extra field (triple-validated: syncd shape gate + webui 400
    # + the honest no-ops inside).
    return apply_split_note(d, decision.get("id"), decision.get("note_ts"))


def _feedback(d: Daemon, stem: str, decision: dict) -> str:
    # §29 feedback（建议上报）: carries "ids" (0..n R-/MS- ids), never a
    # requirement-level "id" — validated + recorded by act/lib/feedback.py.
    return apply_feedback(d, decision)


def _merge_review(d: Daemon, stem: str, decision: dict) -> str:
    # merge-review actions (§21) — suggestion-level, not requirement-level:
    # merge_review carries "ids" (>=2 R-ids); merge_apply/merge_dismiss carry
    # id=<MS-suggestion id>. None of them go through the req lookup.
    return _merge.apply_merge_review(d, decision.get("ids"))


def _merge_decision(d: Daemon, stem: str, decision: dict) -> str:
    # §53.5 actor：merge 判决是用户拍板（§21），merged 终态转移在
    # 白名单里是 user 独占
    return apply_with_actor(d, decision, _merge.apply_merge_decision,
                            d, decision.get("action"), decision.get("id"))


def _merge_force(d: Daemon, stem: str, decision: dict) -> str:
    # 强制合并（§21 v0.31）: user-chosen primary, skips the AI entirely —
    # carries "ids" (>=2 R-ids) + "primary" (∈ ids), no MS- suggestion.
    return apply_with_actor(d, decision, _merge.apply_merge_force,
                            d, decision.get("ids"), decision.get("primary"))


def _import_sessions(d: Daemon, stem: str, decision: dict) -> str:
    # §22 one-shot Claude Code session import — no requirement-level id.
    return apply_claude_import(d, decision)


# §39 answer_input：retired v0.48.8（issue #119）——受阻会话不再挂「需输入」等
# 回答，reconcile 直接收割进待验收；迟到的 answer_input 文件走 card-verb 路径
# 的 unknown-action 分支（幂等 ack "unknown"，绝不复活会话）。
_SPECIAL_ACTIONS = {
    "capture": _capture,
    "split_note": _split_note,
    "feedback": _feedback,
    "merge_review": _merge_review,
    "merge_apply": _merge_decision,
    "merge_dismiss": _merge_decision,
    "merge_force": _merge_force,
    "import_claude_sessions": _import_sessions,
}


# --------------------------------------------------------------------------- #
# §53.5 actor wall + T-28 ingress
# --------------------------------------------------------------------------- #
def apply_with_actor(d: Daemon, decision: dict, fn, *args, **kwargs) -> str:
    """inbox 决策的统一 apply 外壳（§53.5）：按 ingress 落款设置 actor 上下文；
    agent 撞权限墙（TransitionDenied——approve/accept 等状态转移对 agent 零
    写权，R2.1.4）= 干净的幂等 no-op + 日志，不是 poison 文件。"""
    try:
        with registry.acting_as(decision_actor(decision)):
            return fn(*args, **kwargs)
    except TransitionDenied as e:
        d.log(f"inbox: action denied by the agent wall ({e}) — noop (§53.5)")
        return "noop"


def decision_actor(decision: dict) -> str:
    """§53.5：inbox 决策文件 → registry actor。via:"agent"（server agent 通道
    落款，§50/§52）= agent；其余（Mac 无 via / web / 手机同步）都是 owner 的
    动作 = user。radar/digest 等自主管线不经 inbox，走默认 system。"""
    return "agent" if decision.get("via") == "agent" else "user"


# T-28 ingress 落款 → 捕获源 channel。无 via = Mac 等 owner-local 写者（HTTP
# 层之外铸的文件才允许缺 via）；via:"web" = localhost 看板，同为 owner。除这
# 两者外一律非 owner：agent 自报落 agent_capture，"remote" 与一切未知/畸形值
# fail-closed 落 remote_capture——两者都是 PROPOSED 级（policy.CHANNEL_CLASS），
# 调度侧出身从 sources 现算，agent/remote 捕获就此结构性关死自动派发。落款是
# 礼仪 + 取证（同用户裸 HTTP 可不发 actor），硬后盾 = 天花板 + effective_tier
# 强制扩写 + 人工审批列（T-28 诚实条款；收紧路径 T-29）。
def ingress_channel(via: object) -> str:
    if via is None or via == "web":
        return "quick_capture"
    if via == "agent":
        return "agent_capture"
    return "remote_capture"


def is_owner_ingress(via: object) -> bool:
    """owner-class ingress = Mac 文件（无 via）或 localhost 看板（via:"web"）。"""
    return via is None or via == "web"


# --------------------------------------------------------------------------- #
# §10 / §34 capture
# --------------------------------------------------------------------------- #
def clean_image_paths(images) -> list:
    """Boundary validation for an inbox ``images`` list (§33 house pattern):
    keep only non-empty string paths, deduped, capped at the app's 4-image
    UI bound — junk entries must never poison a card or the dispatch prompt.
    """
    if not isinstance(images, list):
        return []
    seen: set = set()
    out: list = []
    for item in images:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:4]


def attach_capture_images(d: Daemon, req: Requirement, images) -> None:
    """贴图 (建议 #5, add-only): fold the capture's PNG paths into
    ``execution.attachments`` — the card-level 附图清单 executor.build_prompt
    turns into a「用户附图」Read block. Append-only + deduped, so a capture
    folding into an existing card keeps that card's earlier attachments."""
    paths = clean_image_paths(images)
    if not paths:
        return
    ex = dict(req.execution) if isinstance(req.execution, dict) else {}
    have = ex.get("attachments")
    have = [str(p) for p in have] if isinstance(have, list) else []
    new = [p for p in paths if p not in have]
    if not new:
        return
    ex["attachments"] = have + new
    req.execution = ex
    d.save(req)


def _capture_text(d: Daemon, text) -> Optional[str]:
    """Normalised capture text, or None when the payload must be acked noop."""
    # non-str text is a poison payload (§33 boundary doctrine): coercing it
    # with str() would file a garbage card — ack noop honestly instead.
    if text is not None and not isinstance(text, str):
        d.log(f"inbox: capture with non-string text ({type(text).__name__}) — ignored")
        return None
    t = " ".join(str(text or "").split()).strip()
    if not t:
        d.log("inbox: capture with empty text — ignored")
        return None
    return t


def _capture_who(channel: str) -> str:
    if channel == "quick_capture":
        return "zelin"
    return "agent" if channel == "agent_capture" else "remote"


def _capture_note(run: bool, owner: bool, channel: str) -> str:
    if run:
        return "[direct-run] 用户直接开跑"
    return "from app quick capture" if owner else f"from {channel}"


def apply_capture(d: Daemon, text: Optional[str], mode: Optional[str] = None,
                  images=None, plan: Optional[list] = None,
                  preset: Optional[str] = None,
                  inbox_stem: Optional[str] = None,
                  via: Optional[object] = None) -> str:
    """Quick capture from the app popover (CONTRACT §10/§15; §34 mode="run").

    ``{"action":"capture","text":"...","ts":"..."}`` -> registry.merge_or_new
    (title=text, channel=quick_capture, 原话进 sources) -> status=raising, so the
    existing process_raising() expands it (one per pass) into a card_sent
    proposal. Fast: no LLM call here, the poll loop is never blocked.

    v0.34.0 ``mode="run"`` (the 运行中 lane's second input, CONTRACT §34): the
    SAME minimal card, but instead of the raising→proposal loop it is filed
    straight as APPROVED so dispatch_approved launches it on the next pass —
    no plan/cost preview; the deliverable still lands in 待验收. Any other
    ``mode`` value (absent, junk, wrong type) fail-safes to today's proposal
    path — junk must never silently start an agent.

    §34 修订（2026-08-07 拍板）：direct-run **彻底不做判重并入**——用户在运行
    框打字 = 起一个新任务，一律新卡直接开跑。撞开卡不折叠/不提升、撞完结卡不
    re-raise（旧处置表作废：8-07 事故里两条 [run] 输入的标题撞上 executing 卡
    被静默并入，新文本没递给会话、看板零回执——用户默认这就是建新卡）。新卡
    强制 chat 交付 + 默认 workbench（no-preview-safe，§34 语义不变），ack 恒为
    "running"（空/非法 text 仍按 §5.4 诚实 ack "noop"）。

    普通 capture（mode 缺省）的静默并入保留（多渠道防重复的核心），但 fold
    发生时经 :mod:`act.lib.fold_receipts` 留看板回执（§44.6）。

    §34bis ``plan``/``preset``（add-only）: preset capture（提案积压清理
    按钮）注入的固定 plan + 卡片顶层 preset 标记，随新卡落盘。防双开在
    上游（process_inbox 的在途判重）——走到这里的 preset capture 必然该
    铸新卡；若判重命中既有卡（§34.1 前的世界），折叠/提升分支也不改写
    对方的 plan/preset。preset 标记是快照护栏
    （check_triage_registry_guard）认卡的依据。

    ``via`` 是 HTTP 写入面的 ingress 落款（T-28）：source channel 按
    ``ingress_channel`` 盖——owner ingress 照旧 quick_capture（HAND），
    agent/remote 落 PROPOSED 级捕获通道，回人工审批；非 owner 的
    ``mode:"run"`` 一并降级走提案管线（W18 的 actd 侧硬后盾——direct-run
    是 owner 特权，伪造/绕过 HTTP 层的 mode 也开不了跑）。expansion
    （process_raising）不改 sources，章随卡走到调度侧现算。

    Returns the §5.4 result_status — the phone's ledger must never show
    已生效 for a capture that filed nothing.
    """
    t = _capture_text(d, text)
    if t is None:
        return "noop"
    channel = ingress_channel(via)
    owner = channel == "quick_capture"
    # T-28/W18 fail-closed：direct-run 是 owner 特权——非 owner ingress 的
    # mode:"run" 一律降级为普通提案 capture（宁可少跑不可多跑）。
    run = mode == "run" and owner
    req = Requirement(
        id=registry.next_id(),
        title=t[:80],
        type="other",
        tier="T1",
        status=State.DETECTED.value,
        hardness="soft",
        # §34bis add-only: preset 注入的固定 plan（目前仅 proposals_triage）。
        # 防双开在上游：process_inbox 的在途判重已拦下「还有 approved/
        # executing 清理卡」的重复点击 —— 走到这里的 preset capture 必然
        # 该铸新卡（plan 也不进 _carries_increment 的增量口径）。
        plan=list(plan) if plan else None,
        preset=preset if plan else None,
        # §10 capture_id（issue #7）= inbox 文件 stem，随出生源引文落盘
        sources=[registry.capture_source(_capture_who(channel), channel, t,
                                         capture_id=inbox_stem)],
        notes=_capture_note(run, owner, channel),
    )
    if run:
        return _capture_direct_run(d, req, t, images, inbox_stem)
    return _capture_proposal(d, req, t, images, channel)


def _replayed_run(d: Daemon, inbox_stem: Optional[str]) -> bool:
    """crash-replay 幂等（§34.1）：process_inbox 是 at-least-once（先 apply
    后删文件）——[run] 绕开判重后，apply 与 unlink 之间 crash 的同一
    inbox 文件重放会铸第二张 approved 卡、起两个 agent。幂等键 = inbox
    文件 stem（execution add-only 字段）：同 stem 已有卡 → 诚实 ack
    running 跳过。两个不同文件（用户两次显式输入）stem 不同，照常两张卡。"""
    if not inbox_stem:
        return False
    dup = next(
        (r for r in registry.load_all()
         if isinstance(r.execution, dict)
         and r.execution.get("inbox_stem") == inbox_stem), None)
    if dup is None:
        return False
    d.log(f"inbox: capture[run] replay of {inbox_stem} -> "
          f"{dup.id} already filed — skip")
    return True


def _capture_direct_run(d: Daemon, req: Requirement, t: str, images,
                        inbox_stem: Optional[str]) -> str:
    """§34 修订（2026-08-07 拍板）：[run] 一律新卡直接开跑——绝不经过
    merge_or_new 的判重/折叠/提升/re-raise（旧行为把撞标题的输入静默并
    入在跑的卡：文本没送达会话、看板零回执）。撞开卡/完结卡都视为用户
    要起一个新任务；后续多渠道防重复照常由 radar/普通 capture 通道兜住。"""
    if _replayed_run(d, inbox_stem):
        return "running"
    # direct-run skips LLM routing entirely — chat delivery at the default
    # workbench is the only no-preview-safe default (§34): no branch/PR
    # lands in a repo the user never confirmed; the FINAL DRAFT (or a
    # deliverables/ file artifact, §33) still reaches 待验收 for acceptance.
    req.delivery_mode = "chat"
    req.thread_id = req.id            # self-root（同 merge_or_new 新卡语义）
    req.status = State.APPROVED.value
    # same bookkeeping as the approve action — dispatch reports wait_s
    # (approve → launch latency) off this stamp. inbox_stem = 上面那道
    # 重放闸的幂等键（直接来自 process_inbox 的文件名，纯元数据）。
    req.execution = {"approved_at": d.iso_now()}
    if inbox_stem:
        req.execution["inbox_stem"] = inbox_stem
    saved = registry.upsert(req)
    attach_capture_images(d, saved, images)
    d.log(f"inbox: capture[run] -> {saved.id} approved "
          f"(new card, queued for dispatch)")
    analytics.log_event(
        "capture_direct_run", req=saved.id, status=str(saved.status),
        chars=len(t),
        text=(analytics.clip_content(t)
              if analytics.content_gate() else None))
    return "running"


def _capture_proposal(d: Daemon, req: Requirement, t: str, images, channel: str) -> str:
    kind, saved = registry.merge_or_new_with_kind(req)
    if kind == "folded":
        # §44.6：capture 静默并入必须留看板回执——卡片转圈后"消失"而文本
        # 不知去向，是 8-07 事故的另一半。best-effort，绝不打断 fold。
        # 原话 t 只进内容键散列，不落盘（隐私红线：dashboard 整包上云）。
        from act.lib import fold_receipts
        fold_receipts.record(saved.id, "quick_capture", t)
    if saved.status == State.DETECTED.value:
        saved.set_status(State.RAISING)
        d.save(saved)
        d.log(f"inbox: capture -> {saved.id} raising (queued for AI expansion, "
              f"channel={channel})")
    else:
        d.log(f"inbox: capture merged into {saved.id} (status={saved.status}, "
              f"channel={channel})")
    attach_capture_images(d, saved, images)
    # the typed capture text is content — capture_input-gated, clipped;
    # chars stays metadata (usage signal without the words).
    analytics.log_event(
        "inbox_capture", req=saved.id, status=str(saved.status), chars=len(t),
        text=(analytics.clip_content(t)
              if analytics.content_gate() else None))
    return "running"


# --------------------------------------------------------------------------- #
# §38 split_note / §37 set_title / §29 feedback / §22 session import
# --------------------------------------------------------------------------- #
def _split_fields(d: Daemon, req_id, note_ts) -> Optional[tuple]:
    """Both fields must be strings（§33 poison doctrine）→ (rid, ts) or None."""
    if not isinstance(req_id, str) or not isinstance(note_ts, str):
        d.log(f"inbox: split_note with non-string fields "
              f"(id={type(req_id).__name__}, note_ts={type(note_ts).__name__}) — ignored")
        return None
    return req_id.strip(), note_ts.strip()


def _split_target(d: Daemon, req_id, note_ts):
    """Validate a split_note payload → (req, ts) or the ack string to return."""
    fields = _split_fields(d, req_id, note_ts)
    if fields is None:
        return "noop"
    rid, ts = fields
    req = registry.resolve(rid) if rid else None      # §60.3 主键或工作编号
    if req is None:
        d.log(f"inbox: split_note for unknown req {req_id!r} — dropped")
        return "unknown"
    if str(req.status) in _merge.MERGE_DEAD_STATES or req.is_merged:
        # terminal-state doctrine (§32.2, same set the merge machinery
        # refuses): a stale detail panel must not mint a live card (+1 expand
        # LLM run) out of a card that meanwhile trashed/merged/rejected/
        # archived. Notes stay untouched; honest noop ack.
        d.log(f"inbox: {req.id} split_note on terminal card "
              f"({req.status}) — no-op")
        return "noop"
    return req, ts


def apply_split_note(d: Daemon, req_id, note_ts) -> str:
    """§38 拆成新卡 — the fold undo. ``{"action":"split_note","id":"R-xxx",
    "note_ts":"<ts tag>"}`` takes the fold-note line tagged ``[@note_ts]`` on
    card ``id`` and re-files its text as a NEW card via the normal capture
    path (detected→raising→AI expansion→proposal, default routing), then
    tags the origin line 已拆出 → 新卡 id (append-only, history preserved).

    Deliberately NOT ``merge_or_new``: the user just said this note does NOT
    belong to that card — a deterministic re-fold would undo the undo. Same
    boundary doctrine as capture (§33): poison payloads / unknown ts /
    already-split lines are honest no-ops (a replayed split must never mint a
    second card). Returns the §5.4 result_status.
    """
    target = _split_target(d, req_id, note_ts)
    if isinstance(target, str):
        return target
    req, ts = target
    entry = _unsplit_entry(req, ts)
    if entry is None:
        d.log(f"inbox: {req.id} split_note ts {note_ts!r} not found / already "
              f"split — no-op")
        return "noop"
    new = _split_card(req, entry["text"])
    # new card FIRST, origin tag second (archive()'s crash-mid-move doctrine:
    # a crash between the two leaves the split recoverable, never lost).
    d.save(new)
    if registry.mark_note_split(req, ts, new.id):
        d.save(req)
    d.log(f"inbox: {req.id} split_note [@{ts}] -> {new.id} (raising)")
    analytics.log_event("split_note", req=req.id, new=new.id)
    return "running"


def _unsplit_entry(req: Requirement, ts: str) -> Optional[dict]:
    """The fold-note line tagged ``[@ts]`` that still has text and was never split."""
    return next((e for e in registry.parse_fold_notes(req.notes)
                 if e["ts"] == ts and not e["split_into"] and e["text"]), None)


def _split_card(req: Requirement, text: str) -> Requirement:
    return Requirement(
        id=registry.next_id(),
        title=text[:80],
        type=req.type or "other",
        tier=req.tier or "T1",
        status=State.RAISING.value,   # capture path: AI expands it next pass
        hardness="soft",
        summary=text[:120],
        sources=[{
            "who": "zelin",
            "channel": "split",
            "date": _dt.date.today().isoformat(),
            "quote": text,
        }],
        notes=f"[拆自 {req.id}] 从其折叠备注拆出",
        # machine-readable lineage: the new card's text ≈ the origin note by
        # construction, and auto_merge would otherwise suggest merging it
        # straight back — one 采纳 destroying the undo (§38.3 _linked).
        split_from=req.id,
    )


def apply_set_title(d: Daemon, req: Requirement, title) -> str:
    """§37 set_title — the user renames a card's DISPLAY title (the frozen
    internal ``title`` never changes; it anchors dedupe/re-raise identity).

    Fail-closed validation (v0.33.1 boundary doctrine): non-string / empty /
    >64-char titles are logged no-ops — a poison payload must never become a
    board title. Sets ``user_titled`` so LLM/harvest titles never overwrite
    the user's choice; the previous display name lands in ``former_titles``
    (still searchable). Archived cards stay sealed (unarchive first), same as
    the central apply_decision gate. Returns the §5.4 result_status.
    """
    if str(req.status) == State.ARCHIVED.value:
        d.log(f"inbox: {req.id} set_title on archived card — no-op (unarchive first)")
        return "noop"
    if not isinstance(title, str):
        d.log(f"inbox: {req.id} set_title with non-string title "
              f"({type(title).__name__}) — ignored")
        return "noop"
    t = " ".join(title.split()).strip()
    if not t or len(t) > 64:
        d.log(f"inbox: {req.id} set_title invalid title "
              f"(empty or >64 chars, got {len(t)}) — ignored")
        return "noop"
    if not registry.set_display_title(req, t, by_user=True):
        d.log(f"inbox: {req.id} set_title no-op (title unchanged)")
        return "noop"
    d.save(req)
    d.log(f"inbox: {req.id} set_title -> {t!r} (user pinned)")
    return "running"


def apply_feedback(d: Daemon, decision: dict) -> str:
    """建议上报 (CONTRACT §29) — explicit user report to the maintainer.

    ``{"action":"feedback","ids":["R-001","MS-ab12cd34"],"text":"…",
    "publish":true|false}`` — validation here: the report must carry
    SOMETHING — text, or images (贴图 建议 #4: an image-only report is
    legal, answer 弹窗同款); both empty -> logged drop. ``ids`` may be
    missing/empty/garbage (bad ids degrade to "unknown" snapshots inside the
    record — the content must never be lost over them). ``publish`` is the
    app checkbox「同时公开到 GitHub 建议跟踪表」and only an explicit JSON
    ``true`` counts (absent/garbage — e.g. an older app — stays private;
    act/lib/feedback_sync.py syncs later). ``images`` = local PNG paths under
    state/feedback/attachments/ — recorded in the local file only; the
    upload carries just their count (feedback.clean_images validates).
    Recording + best-effort upload live in act/lib/feedback.py; only event
    METADATA reaches the local analytics log — the report text travels solely
    inside the feedback record itself. Returns the §5.4 result_status
    ("running" recorded | "noop" dropped).
    """
    feedback = d.feedback
    if feedback is None:
        d.log("inbox: feedback requested but module unavailable — dropped")
        return "noop"
    payload = _feedback_payload(d, feedback, decision)
    if payload is None:
        return "noop"
    text, images = payload
    ids = feedback.clean_ids(decision.get("ids"))
    publish = decision.get("publish") is True
    rec = feedback.record_feedback(ids, text, publish=publish, images=images)
    if rec is None:
        d.log("inbox: feedback record FAILED — dropped")
        return "noop"
    d.log(f"inbox: feedback {rec['id']} recorded "
          f"(ids={ids or []}, publish={publish}, uploaded={rec.get('uploaded')})")
    analytics.log_event("inbox_feedback", n=len(ids), publish=publish,
                        uploaded=rec.get("uploaded"))
    return "running"


def _feedback_payload(d: Daemon, feedback, decision: dict) -> Optional[tuple]:
    """(text, images) — the report must carry at least one of them."""
    text = str(decision.get("text") or "").strip()
    images = feedback.clean_images(decision.get("images"))
    if not text and not images:
        d.log("inbox: feedback with no text and no images — dropped")
        return None
    return text, images


def _session_ids(decision: dict) -> list:
    raw_ids = decision.get("session_ids")
    return [str(s) for s in raw_ids if s] if isinstance(raw_ids, list) else []


def _import_window(decision: dict) -> int:
    try:
        return int(decision.get("window_days") or 7)
    except (TypeError, ValueError):
        return 7


def apply_claude_import(d: Daemon, decision: dict) -> str:
    """One-shot Claude Code session import (CONTRACT §22).

    ``{"action":"import_claude_sessions","session_ids":[…],"window_days":7}``
    — with explicit ids (the Settings checkbox flow) each session becomes a
    proposal card; without ids, every waiting-on-you session inside the window
    is imported. Idempotent: already-imported ids are skipped via the
    state/claude_sessions_import.json marker, and card creation goes through
    merge_or_new. Cheap (head/tail file reads, no LLM) — safe inline in the
    poll loop. Returns the §5.4 result_status ("running" ran | "noop" failed).
    """
    radar = d.radar_claude_sessions
    if radar is None:
        d.log("inbox: import_claude_sessions requested but module unavailable — dropped")
        return "noop"
    ids = _session_ids(decision)
    window = _import_window(decision)
    try:
        n = radar.import_by_ids(ids) if ids else radar.run_once(window_days=window)
        d.log(f"inbox: import_claude_sessions -> {n} card(s) "
              f"({len(ids) or 'auto'} requested)")
        return "running"
    except Exception as e:  # noqa: BLE001 — an import failure must not kill the pass
        d.log(f"inbox: import_claude_sessions failed: {e}")
        return "noop"
