"""Dashboard builder — produces ``state/dashboard.json`` (CONTRACT §2).

actd writes this file; the Mac app reads it (never writes). The write is atomic
(``.tmp`` then ``rename``). Running/needs_input/completed partitions come from
joining registry ``status=executing`` items with ``claude agents --json --all``
by ``session_id``.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from act.lib import config, policy, risk, steer
from act.lib.registry import Requirement, State, load_all

TIER_HINTS = {
    "T0": "自动执行",
    "T1": "一键可批",
    "T2": "需文字确认",
}


# --------------------------------------------------------------------------- #
# claude agents --json --all
# --------------------------------------------------------------------------- #
def _run_claude_agents() -> list[dict]:
    """Return the raw list of live agents. Defensive: never raises."""
    try:
        proc = subprocess.run(
            ["claude", "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        # tolerate {"agents": [...]} or {"sessions": [...]}
        for k in ("agents", "sessions", "items", "data"):
            if isinstance(data.get(k), list):
                return data[k]
        return []
    if isinstance(data, list):
        return data
    return []


def _norm_agent(a: dict) -> dict:
    """Normalize an agent record to the fields we join/emit on."""
    def pick(*keys):
        for k in keys:
            if a.get(k) not in (None, ""):
                return a[k]
        return None

    return {
        "session_id": pick("session_id", "sessionId", "id", "session"),
        "short_id": pick("id"),   # claude's short id — what `claude attach` shows
        "pid": a.get("pid"),      # present ONLY while the process is alive
        "cwd": pick("cwd", "working_directory", "workingDirectory", "directory"),
        "name": pick("name", "title", "summary"),
        "state": (pick("state", "status") or "").lower(),
        "started_at": pick("started_at", "startedAt", "created_at", "createdAt"),
        "waiting_for": pick("waiting_for", "waitingFor", "blocked_on", "blockedOn"),
    }


def _index_agents(agents: list[dict]) -> dict[str, dict]:
    """Index by EVERY id shape claude exposes.

    `claude agents --json` gives both a short ``id`` (e.g. c895e960) and a full
    ``sessionId`` (c895e960-....). dispatch/resume capture the SHORT id from the
    "backgrounded · <id>" line, so we must key on both — otherwise a live agent
    looks "vanished" and reconcile spuriously re-resumes it (spawning a dup).
    """
    idx: dict[str, dict] = {}
    for a in agents:
        if not isinstance(a, dict):
            continue
        n = _norm_agent(a)
        for key in (a.get("id"), a.get("sessionId"), a.get("session_id"), n["session_id"]):
            if key:
                idx.setdefault(str(key), n)
    return idx


_RUNNING_STATES = {"working", "running", "executing", "active", "busy", "in_progress"}
_BLOCKED_STATES = {"blocked", "waiting", "needs_input", "paused", "waiting_for_input"}
_DONE_STATES = {"done", "completed", "finished", "exited", "complete", "success"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _today() -> _dt.date:
    return _dt.date.today()


def days_left(deadline: Optional[str]) -> Optional[int]:
    if not deadline:
        return None
    try:
        d = _dt.date.fromisoformat(str(deadline))
    except ValueError:
        return None
    return (d - _today()).days


def _as_list(plan: Any) -> list:
    if plan is None:
        return []
    if isinstance(plan, list):
        return [str(p) for p in plan]
    # split a multi-line string block into steps
    lines = [ln.strip() for ln in str(plan).splitlines() if ln.strip()]
    return lines


def _source_view(req: Requirement, cfg: config.Config) -> list[dict]:
    out = []
    for s in req.sources or []:
        if not isinstance(s, dict):
            continue
        # Swift 端 Source 四个字段都是非可选 String（合成 Decodable）：任何一个
        # null 会让所在数组整体解码失败（如 debt 整列被 `?? []` 清空）——所以这里
        # 把 None 一律归一成空串（契约 B 的 {who,channel,date,quote} 同形不变）。
        d = s.get("date")
        out.append(
            {
                "who": s.get("who") or cfg.requester_display(),
                "channel": s.get("channel") or "",
                "date": str(d) if d is not None else "",
                "quote": s.get("quote") or s.get("ref") or "",
            }
        )
    return out


def _dir_is_nonempty(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _target_view(req: Requirement, cfg: config.Config) -> tuple[str, str, str]:
    """Return (target_repo, target_name, target_kind) for a card (§7).

    - explicit ``target_repo``: existing if the dir exists & is non-empty, else new.
    - no ``target_repo``: default to the default-target-repo path -> existing.
    """
    if req.target_repo:
        target = Path(req.target_repo).expanduser()
        kind = "existing" if _dir_is_nonempty(target) else "new"
        return req.target_repo, target.name, kind
    default = cfg.target_repo_path
    return str(default), default.name, "existing"


def _cost_view(req: Requirement, cfg: config.Config) -> tuple[Optional[float], bool]:
    cost = req.cost_estimate_usd
    if cost is None:
        return None, False
    return cost, float(cost) >= cfg.show_cost_above_usd


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(ts: Any) -> Optional[int]:
    """ISO string (registry format) -> epoch int (dashboard format, §2).

    The registry stores ISO strings; the dashboard emits epoch ints — same
    convention as ``started_at``. Returns None when unparsable (Swift reads
    every timestamp with decodeIfPresent).
    """
    if not ts:
        return None
    try:
        s = str(ts).strip().replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp())


def _delivery_mode(req: Requirement) -> str:
    """"chat" | "repo" — missing/legacy objects count as "repo" (§20)."""
    dm = getattr(req, "delivery_mode", None)
    return dm if dm in ("chat", "repo") else "repo"


# --------------------------------------------------------------------------- #
# v-next 投影辅助（amendments §51/§M6/M8.3 C-2·C-3·C-4，全部 add-only optional）
# --------------------------------------------------------------------------- #
def _spend_cards() -> dict:
    """auto-dispatch 当日花费台账的只读镜像 {R-id: usd}。写者 = actd
    （act/actd.py::_save_spend_ledger，文件名同 _SPEND_LEDGER_FILE）——import
    actd 会循环依赖，故此处独立小读器。隔日/坏文件 = 空账（视同 $0）。"""
    try:
        raw = json.loads((config.STATE_DIR / "autodispatch_spend.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("date") != _today().isoformat():
        return {}
    out: dict = {}
    if isinstance(raw.get("cards"), dict):
        for k, v in raw["cards"].items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def _queued_reason_view(req: Requirement, state: dict) -> Optional[dict]:
    """M1.c token → 结构化 wire 形（M8.3 C-2 终裁为 canonical）：
    dependency → {kind: waiting_card, blocking_id}｜budget → {kind:
    waiting_budget}｜concurrency → {kind: concurrency}。None = 无阻塞
    （纯粹没轮到/派发失败退避——后者由 dispatch_error 独立表达，不混写）。"""
    token = policy.queued_reason(req, state)
    if token == "dependency":
        blocking = state.get("blocked_by")
        first = blocking[0] if isinstance(blocking, list) and blocking else None
        out = {"kind": "waiting_card"}
        if first:
            out["blocking_id"] = str(first)
        return out
    if token == "budget":
        return {"kind": "waiting_budget"}
    if token == "concurrency":
        return {"kind": "concurrency"}
    return None


def _steers_view(req: Requirement) -> list:
    """running/needs_input 行的 ``steers[]``（§M6.1 / C-3 / C-4）：delivered
    环（带 delivered_at）在前、pending（status=queued）在后；dropped 不投影
    ——可见性由 notes 痕 `[追加指令未送达]` 承担。ts 保台账 ISO 原文（C-4：
    与 execution.* 逐字对账；web 端只认 string ts，无 ts 的行不投）。"""
    out = []
    for e in steer.delivered_entries(req):
        out.append({"text": e["text"], "ts": e["ts"],
                    "status": "delivered", "delivered_at": e["delivered_at"]})
    for n in steer.pending_steers(req):
        if not n.get("ts"):
            continue
        out.append({"text": n["text"][:steer.TRACE_CLIP], "ts": n["ts"],
                    "status": "queued", "delivered_at": None})
    return out


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build_dashboard(
    reqs: Optional[list[Requirement]] = None,
    agents: Optional[list[dict]] = None,
    cfg: Optional[config.Config] = None,
) -> dict:
    """Assemble the dashboard dict (CONTRACT §2). Pure/injectable for testing."""
    if cfg is None:
        cfg = config.load_config()
    if reqs is None:
        reqs = load_all()
    if agents is None:
        agents = _run_claude_agents()
    agent_idx = _index_agents(agents)

    # v-next queued_reason 快照（§51）：并发口径与 actd.dispatch_approved 一致
    # （EXECUTING 且带 session 的卡数）；预算口径只对 auto_dispatched 卡生效
    # ——人批的卡不受预算闸，chip 不许谎报「等预算」。
    ad_cfg = policy.autodispatch_config(cfg)
    spend_cards = _spend_cards()
    live_sessions = sum(
        1 for r in reqs
        if r.status == State.EXECUTING.value
        and isinstance(r.execution, dict) and r.execution.get("session_id"))

    needs_approval: list[dict] = []
    running: list[dict] = []
    needs_input: list[dict] = []
    review: list[dict] = []
    completed: list[dict] = []
    debt: list[dict] = []
    trash: list[dict] = []

    for req in reqs:
        if req.is_merged or req.status == State.REJECTED.value:
            continue

        if req.status == State.CARD_SENT.value:
            cost, show_cost = _cost_view(req, cfg)
            target_repo, target_name, target_kind = _target_view(req, cfg)
            needs_approval.append(
                {
                    "id": req.id,
                    "title": req.title,
                    "summary": req.summary or req.title,
                    "target_repo": target_repo,
                    "target_name": target_name,
                    "target_kind": target_kind,
                    "tier": req.tier,
                    "tier_hint": TIER_HINTS.get(req.tier, ""),
                    # W17 add-only：生效档位（外部来源强制 T2；余同声明 tier）。
                    # 存量卡无 origin_trust 字段 => 恒等于 tier，客户端可放心
                    # decodeIfPresent。审批语义仍由 tier 决定，接线见 amendments §W17。
                    "effective_tier": risk.effective_tier(req).tier,
                    "hardness": req.hardness,
                    "deadline": req.deadline,
                    "days_left": days_left(req.deadline),
                    "repeated": int(req.repeated_mentions or 1),
                    "cost_usd": cost,
                    "show_cost": show_cost,
                    "green_sign": bool(req.green_sign_required),
                    "disagreement": req.disagreement,
                    "improvement_of": req.improvement_of,
                    "sources": _source_view(req, cfg),
                    "plan": _as_list(req.plan),
                    "outputs": list(req.outputs or []),
                    "dod": list(req.definition_of_done or []),
                    "processing": False,
                    "delivery_mode": _delivery_mode(req),
                    # v-next add-only（§50/§51/C-6）：出身章 + auto-dispatch
                    # 拦下原因（origin:*/disabled 常态原因不上卡，见 actd）。
                    **({"origin_trust": req.origin_trust}
                       if req.origin_trust else {}),
                    **({"auto_dispatch_block":
                        (req.execution or {}).get("auto_dispatch_block")}
                       if isinstance(req.execution, dict)
                       and req.execution.get("auto_dispatch_block") else {}),
                }
            )

        elif req.status == State.RAISING.value:
            # AI is expanding this debt into a proposal — show it in 待审批 as a
            # greyed spinner placeholder so the click gives immediate feedback.
            needs_approval.append(
                {
                    "id": req.id,
                    "title": req.title,
                    "summary": req.summary or req.title,
                    "tier": req.tier,
                    "effective_tier": risk.effective_tier(req).tier,  # W17 add-only
                    "tier_hint": "AI 研究中",
                    "processing": True,
                    "sources": [],
                    "plan": [],
                    "dod": [],
                    "show_cost": False,
                    "delivery_mode": _delivery_mode(req),
                    **({"origin_trust": req.origin_trust}
                       if req.origin_trust else {}),   # v-next add-only（§50）
                }
            )

        elif req.status == State.DETECTED.value:
            debt.append(
                {
                    "id": req.id,
                    "title": req.title,
                    "summary": req.summary or req.title,
                    "hardness": req.hardness,
                    "type": req.type,
                    "sources": _source_view(req, cfg),
                }
            )

        elif req.status == State.TRASHED.value:
            trash.append(
                {
                    "id": req.id,
                    "title": req.title,
                    "summary": req.summary or req.title,
                    "kind": "debt" if req.prev_status == State.DETECTED.value else "suggestion",
                    "trashed_at": req.trashed_at,
                    "trash_reason": req.trash_reason,
                    "permanent": bool(req.permanent),
                    "type": req.type,
                    "hardness": req.hardness,
                }
            )

        elif req.status == State.APPROVED.value:
            # §2 queued 项：已批准但还没（成功）派发 —— 混入 running 分区，✅ 一点
            # 下去立刻有回显。没有会话可 attach，所以无 session_id/copy_cmd；
            # dispatch_error = 上次派发失败原因（重试成功后消失）。
            ex = req.execution if isinstance(req.execution, dict) else {}
            # v-next §51：排队原因 chip（结构化 wire 形，C-2）。快照口径与
            # actd.dispatch_approved 的闸完全一致（预算只对 auto_dispatched
            # 卡、且排除本卡自己的预留）；blocked_by 依赖字段未立法（T-26），
            # 首版仅 budget/concurrency 两因。与 dispatch_error 并存不混写。
            snap: dict = {"running": live_sessions,
                          "max_concurrent": ad_cfg["max_concurrent"]}
            if ex.get("auto_dispatched"):
                snap["today_spend"] = sum(
                    v for k, v in spend_cards.items() if k != req.id)
                snap["daily_budget_usd"] = ad_cfg["daily_budget_usd"]
            qr = _queued_reason_view(req, snap)
            running.append(
                {
                    "id": req.id,
                    "name": req.title or req.id,
                    "state": "queued",
                    "summary": req.summary or None,
                    "plan": _as_list(req.plan),
                    "dod": list(req.definition_of_done or []),
                    "delivery_mode": _delivery_mode(req),
                    "dispatch_error": ex.get("last_error") or None,
                    **({"queued_reason": qr} if qr else {}),
                    **({"origin_trust": req.origin_trust}
                       if req.origin_trust else {}),
                }
            )

        elif req.status in (State.EXECUTING.value, State.REVIEW.value,
                            State.DELIVERED.value):
            ex = req.execution if isinstance(req.execution, dict) else {}
            sid = ex.get("session_id")
            agent = agent_idx.get(str(sid)) if sid else None
            # prefer the requirement title: claude uses the (huge) injected prompt
            # as the agent "name", which is useless to display.
            name = req.title or (agent or {}).get("name") or req.id
            cwd = (agent or {}).get("cwd") or (req.target_repo or str(cfg.target_repo_path))
            state = (agent or {}).get("state") or "unknown"
            # emit the FULL sessionId for the `claude --resume` copy: dispatch
            # stored the SHORT id, but the resume picker matches the full UUID.
            resume_sid = (agent or {}).get("session_id") or sid
            short_id = (agent or {}).get("short_id") or sid
            # correct command by PROCESS liveness, not task state: even a task
            # whose work is "done" keeps its bg process alive (idle) for ~1h and
            # `--resume` errors with "currently running as a background agent".
            # `pid` is present in claude agents --json ONLY while the process is
            # alive -> attach; once it exits (pid gone) -> --resume.
            # NOTE: --resume is DIRECTORY-scoped (transcripts key to the session
            # cwd, usually the agent's worktree) -> prefix with cd so the copied
            # command works from any terminal. attach is roster-global, no cd.
            if agent is not None and agent.get("pid"):
                copy_cmd = f"claude attach {short_id}"
            else:
                # full UUID + the transcript's LAST cwd (the agent's worktree) —
                # both required for --resume; the roster shows the launch dir,
                # which is the wrong place to resume from.
                from act.executor import _transcript_info
                tinfo = _transcript_info(str(resume_sid or short_id or ""))
                if tinfo:
                    copy_cmd = f"cd '{tinfo[1]}' && claude --resume {tinfo[0]}"
                else:
                    copy_cmd = f"claude --resume {resume_sid}"
            agent_name = (agent or {}).get("name")

            if req.status == State.DELIVERED.value:
                # §11 已验收 — archive row
                completed.append(
                    {
                        "id": req.id,
                        "name": name,
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "state": "delivered",
                        "cwd": cwd,
                        "summary": req.summary or None,
                        "delivered_summary": ex.get("delivered_summary"),
                        "accepted_at": _epoch(ex.get("accepted_at")),
                        "dod": list(req.definition_of_done or []),
                    }
                )
            elif req.status == State.REVIEW.value and state in _RUNNING_STATES:
                # attach 回流（只动投影层，不动状态机）：待验收后 Zelin 可能
                # `claude attach` 回原 session 继续输入，agent 重新 working ——
                # registry 仍是 review，但卡片临时回到「运行中」列（state=
                # "review-active"，Swift 端 teal 徽章）。roster blocked/done/
                # 缺席则走下面的分支，照旧进 review。
                running.append(
                    {
                        "id": req.id,
                        "name": name,
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "cwd": cwd,
                        "state": "review-active",
                        "started_at": (agent or {}).get("started_at"),
                        "summary": req.summary or None,
                        "plan": _as_list(req.plan),
                        "dod": list(req.definition_of_done or []),
                        "log": ex.get("log"),
                        "dispatched_at": _epoch(ex.get("dispatched_at")),
                        "delivery_mode": _delivery_mode(req),
                        "last_error": ex.get("last_error"),
                    }
                )
            elif req.status == State.REVIEW.value or state in _DONE_STATES:
                # §11 待验收 — draft ready, awaiting Zelin's ✓/↩︎
                # (agent-done-while-still-executing lands here too, covering the
                # gap between dashboard passes and actd's promotion.)
                review.append(
                    {
                        "id": req.id,
                        "name": name,
                        "summary": req.summary or None,
                        "dod": list(req.definition_of_done or []),
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "state": "review",
                        "cwd": cwd,
                        "delivered_summary": ex.get("delivered_summary"),
                        "final_draft": ex.get("final_draft"),
                        "plan": _as_list(req.plan),
                        "sources": _source_view(req, cfg),
                        "log": ex.get("log"),
                        "dispatched_at": _epoch(ex.get("dispatched_at")),
                        "review_at": _epoch(ex.get("review_at")),
                        "delivery_mode": _delivery_mode(req),
                    }
                )
            elif state in _BLOCKED_STATES:
                steers = _steers_view(req)
                needs_input.append(
                    {
                        "id": req.id,
                        "name": name,
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "state": "blocked",
                        "waiting_for": (agent or {}).get("waiting_for") or "input",
                        # v-next §M6.1：steer 三态诚实回执（queued/delivered）
                        **({"steers": steers} if steers else {}),
                        **({"origin_trust": req.origin_trust}
                           if req.origin_trust else {}),
                    }
                )
            else:
                # running, or agent not found yet -> still consider it running
                steers = _steers_view(req)
                running.append(
                    {
                        "id": req.id,
                        "name": name,
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "cwd": cwd,
                        "state": "working" if state in _RUNNING_STATES else state,
                        "started_at": (agent or {}).get("started_at"),
                        "summary": req.summary or None,
                        "plan": _as_list(req.plan),
                        "dod": list(req.definition_of_done or []),
                        "log": ex.get("log"),
                        "dispatched_at": _epoch(ex.get("dispatched_at")),
                        "delivery_mode": _delivery_mode(req),
                        "last_error": ex.get("last_error"),
                        # v-next §M6.1：steer 三态诚实回执（queued/delivered；
                        # dropped 不投影，notes 痕承担可见性——C-3）
                        **({"steers": steers} if steers else {}),
                        **({"origin_trust": req.origin_trust}
                           if req.origin_trust else {}),
                    }
                )
        # approved surfaces as a "queued" item inside running (branch above, §2)

    return {
        "generated_at": _iso_now(),
        "counts": {
            "needs_approval": len(needs_approval),
            "running": len(running),
            "needs_input": len(needs_input),
            "review": len(review),
            "completed": len(completed),
            "debt": len(debt),
            "trash": len(trash),
        },
        "needs_approval": needs_approval,
        "running": running,
        "needs_input": needs_input,
        "review": review,
        "completed": completed,
        "debt": debt,
        "trash": trash,
    }


def _json_default(o):
    """PyYAML parses bare YYYY-MM-DD into date/datetime; coerce to ISO string."""
    if isinstance(o, (_dt.date, _dt.datetime)):
        return o.isoformat()
    return str(o)


def write_dashboard(dash: Optional[dict] = None, path: Optional[Path] = None) -> dict:
    """Atomically write the dashboard JSON (.tmp then rename)."""
    if dash is None:
        dash = build_dashboard()
    target = path or config.DASHBOARD_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dash, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    tmp.replace(target)
    return dash


if __name__ == "__main__":
    import sys

    d = build_dashboard()
    write_dashboard(d)
    json.dump(d, sys.stdout, ensure_ascii=False, indent=2, default=_json_default)
    sys.stdout.write("\n")
