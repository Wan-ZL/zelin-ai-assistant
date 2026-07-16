"""Dashboard builder — produces ``state/dashboard.json`` (CONTRACT §2).

actd writes this file; the Mac app reads it (never writes). The write is atomic
(``.tmp`` then ``rename``). Running/needs_input/completed partitions come from
joining registry ``status=executing`` items with ``claude agents --json --all``
by ``session_id``.

merge_suggestions (merge-review 契约 六) is a pure projection of the job files
under ``state/merge/*.json`` (actd/act.merge_review write them; we only read):
analyzing/done/failed are emitted, dismissed is not, corrupt files are skipped,
and ``requested_at`` is converted from registry ISO to epoch int. Cards whose
registry status is ``merged`` (契约 四 终态) enter NO column at all.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from act.lib import config, failures, titles
from act.lib.agent_states import _BLOCKED_STATES, _DONE_STATES, _RUNNING_STATES
from act.lib.registry import Requirement, State, load_all, load_archived

TIER_HINTS = {
    "T0": "自动执行",
    "T1": "一键可批",
    "T2": "需文字确认",
}

# merge-review job files (契约 二) — actd creates them on a merge_review inbox
# action; act.merge_review's analysis subprocess atomically rewrites them.
MERGE_DIR: Path = config.STATE_DIR / "merge"

# Job statuses the dashboard forwards (契约 六): dismissed (and anything
# unknown) stays local to the job file and never reaches the app.
_MERGE_EMIT_STATUSES = ("analyzing", "done", "failed")


# --------------------------------------------------------------------------- #
# transcript-info memoization (hot path)
# --------------------------------------------------------------------------- #
# executor._transcript_info reads + json-parses the FULL transcript of a
# session. The dashboard needs it for every executing/review/delivered card
# without a live pid — and the delivered set grows forever (never auto-
# archived) — so calling it uncached on every ~10s pass is unbounded IO that
# can push a pass past the app's freshness window (false "后台服务可能没在
# 运行" banner). Memoize per session id, validated by the (path, mtime, size)
# signature of every transcript file the lookup would scan: an appended,
# replaced or deleted transcript invalidates immediately, an idle one is free.
_TINFO_CACHE: dict[str, tuple[tuple, Optional[tuple]]] = {}
_TINFO_CACHE_MAX = 512  # tiny entries; bound it so a long-lived actd can't grow


def _transcript_sig(sid: str) -> Optional[tuple]:
    """Freshness signature: (path, mtime_ns, size) of each transcript file
    ``executor._transcript_info(sid)`` would consider — the glob pattern must
    stay in sync with executor's. None = can't sign (short sid / OSError):
    the caller falls through to an uncached lookup, never a stale answer."""
    short = str(sid or "").split("-")[0]
    if len(short) < 8:  # executor's guard: anything shorter globs everything
        return None
    root = Path("~/.claude/projects").expanduser()
    try:
        sig = []
        for f in sorted(root.glob(f"*/{short}*.jsonl")):
            st = f.stat()
            sig.append((str(f), st.st_mtime_ns, st.st_size))
        return tuple(sig)
    except OSError:
        return None


def _transcript_info_cached(sid: str) -> Optional[tuple]:
    from act.executor import _transcript_info  # lazy: keep dashboard import-light
    sig = _transcript_sig(sid)
    if sig is None:
        return _transcript_info(sid)
    hit = _TINFO_CACHE.get(sid)
    if hit is not None and hit[0] == sig:
        return hit[1]
    info = _transcript_info(sid)
    if len(_TINFO_CACHE) >= _TINFO_CACHE_MAX:
        _TINFO_CACHE.clear()
    _TINFO_CACHE[sid] = (sig, info)
    return info


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


# completed[] cap (§2): the registry never archives DELIVERED items, so without
# a ceiling the dashboard grows forever (rebuilt every ~10s, re-decoded by the
# app on every refresh). Keep only the most recent entries by accepted_at;
# counts.completed stays the TRUE total.
COMPLETED_CAP = 50

# archived[] cap (§5 v0.20.0): sealed cards live in the archive/ subdir forever
# (never purged). The app's archive browse only needs the most-recent window;
# counts.archived stays the TRUE total (same convention as completed).
ARCHIVED_CAP = 50


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


def _s(v: Any) -> str:
    """Wire 类型归一：Swift 端 id/title/name/tier 都是硬 String decode（合成
    Decodable），一个 int（如手写 YAML 的 ``id: 300``）就能让整列解码成 []
    而 counts 徽章还显示真实数（§2）。None -> ""（字段本身非可选）。"""
    return "" if v is None else str(v)


def _int_or(v: Any, default: int) -> int:
    """损坏的数字字段（``repeated_mentions: abc``）降级成 default，不让一张
    坏卡把整个 dashboard pass 炸掉。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clip_draft(v: Any) -> Optional[str]:
    """final_draft 契约上限 ≤20000 字（§16）——harvest 端已截断，这里是投影端
    兜底（手写/旧数据不受 harvest 约束），坏数据不放大进 ~10s 重写的热路径。"""
    if v in (None, ""):
        return None
    return str(v)[:20000]


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


def _archived_view(req: Requirement) -> dict:
    """One archived[] row (§5 v0.20.0). Mirrors the trash row fields + archive
    bookkeeping (archived_at / archive_reason / prev_status) so the app decodes
    it with the same shape as TrashItem."""
    return {
        "id": _s(req.id),
        "title": _s(req.title),
        "summary": req.summary or _s(req.title),
        **_title_fields(req),
        "kind": "debt" if req.prev_status == State.DETECTED.value else "suggestion",
        "archived_at": req.archived_at,
        "archive_reason": req.archive_reason,
        "prev_status": req.prev_status,
        "type": req.type,
        "hardness": req.hardness,
    }


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
    try:
        c = float(cost)
    except (TypeError, ValueError):
        # ``cost_estimate_usd: cheap`` 之类的坏值：字段降级成"无成本估算"，
        # 卡片其余部分照常投影（单字段损坏不丢整卡，更不丢整个 pass）。
        return None, False
    return c, c >= cfg.show_cost_above_usd


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(ts: Any) -> Optional[int]:
    """ISO string (registry format) -> epoch int (dashboard format, §2).

    The registry stores ISO strings; the dashboard emits epoch ints — same
    convention as ``started_at``. Returns None when unparsable (Swift reads
    every timestamp with decodeIfPresent).
    """
    if isinstance(ts, bool):
        return None  # bool 是 int 子类，但 True/False 不是时间戳
    if isinstance(ts, (int, float)):
        # 已经是 epoch（claude roster / 手写数据都可能直接给数字）——幂等
        # 返回，不能走 str->fromisoformat 把目标格式反而丢成 None。
        return int(ts)
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


# notes fold user comments / radar updates that used to be unsearchable on the
# board — projected capped so one chatty card can't bloat the ~10s rewrite
# (and the E2E board payload) unboundedly.
_NOTES_TEXT_CAP = 2000
_NOTES_CLIP_MARKER = "…（更早的备注已省略）"


def _display_title(req: Requirement) -> str:
    """§37 fallback chain at projection time: stored display_title (user-pinned
    or LLM) → deterministic sanitize(title) → title. Always non-empty for a
    titled card, so a raw URL/path never renders as a board title — zero
    migration for legacy cards."""
    dt = str(getattr(req, "display_title", "") or "").strip()
    if dt:
        return dt[:titles.MAX_DISPLAY_TITLE]
    return titles.sanitize_title(_s(req.title)) or _s(req.title)


def _notes_text(req: Requirement):
    """§38 clip semantics for the notes projection: line-aligned TAIL. Fold
    lines append at the TAIL — a head clip would silently drop the newest
    folds' [@ts] handles (and can cut an 已拆出 flip mid-tag), exactly what
    拆成新卡 needs. Over the cap the LAST ~2000 chars survive, snapped
    forward to a line boundary so Swift's FoldNote.parse only ever sees
    intact lines; an ellipsis marker line says honestly that older notes
    were dropped. None when the card has no notes."""
    notes = str(req.notes or "").strip()
    if not notes:
        return None
    if len(notes) > _NOTES_TEXT_CAP:
        clipped = notes[-_NOTES_TEXT_CAP:]
        nl = clipped.find("\n")
        if nl >= 0:   # drop the partial first line (a giant single line stays)
            clipped = clipped[nl + 1:]
        notes = f"{_NOTES_CLIP_MARKER}\n{clipped}"
    return notes


def _title_fields(req: Requirement) -> dict:
    """The §37 add-only row fields shared by every lane projection. Empty
    optionals are omitted (not null) so the payload only grows where there is
    something to say; Swift reads them with decodeIfPresent."""
    out: dict = {"display_title": _display_title(req)}
    if getattr(req, "user_titled", False):
        out["user_titled"] = True
    former = [str(x) for x in (getattr(req, "former_titles", None) or [])
              if str(x).strip()]
    if former:
        out["former_titles"] = former
    notes = _notes_text(req)   # §38: tail-aligned clip (fold handles survive)
    if notes:
        out["notes_text"] = notes
    return out


# --------------------------------------------------------------------------- #
# merge_suggestions partition (merge-review 契约 六)
# --------------------------------------------------------------------------- #
def _merge_suggestions(merge_dir: Optional[Path] = None) -> list[dict]:
    """Project ``state/merge/*.json`` into the merge_suggestions partition.

    Read-only and defensive: analyzing/done/failed are emitted, dismissed (and
    unknown statuses) are not, corrupt/unreadable files are skipped one by one.
    ``requested_at`` converts ISO -> epoch int (same convention as the other
    partitions); ``expires_at`` is job-file bookkeeping (actd's TTL sweep) and
    is deliberately NOT forwarded. Newest request first.
    """
    d = Path(merge_dir) if merge_dir is not None else MERGE_DIR
    out: list[dict] = []
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # 损坏文件跳过，绝不拖垮整个 dashboard pass
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or "").strip().lower()
        if status not in _MERGE_EMIT_STATUSES:
            continue  # dismissed / 未知状态不发（契约 六）

        def _opt_str(key: str) -> Optional[str]:
            v = data.get(key)
            return str(v) if v not in (None, "") else None

        ids = data.get("ids")
        action_plan = data.get("action_plan")
        out.append(
            {
                "id": str(data.get("id") or path.stem),
                "ids": [str(i) for i in ids] if isinstance(ids, list) else [],
                "status": status,
                "verdict": _opt_str("verdict"),
                "primary": _opt_str("primary"),
                "rationale": _opt_str("rationale"),
                "action_plan": (
                    [str(s) for s in action_plan]
                    if isinstance(action_plan, list) else []
                ),
                "confidence": _opt_str("confidence"),
                "error": _opt_str("error"),
                "requested_at": _epoch(data.get("requested_at")),
            }
        )
    # newest request first (stable: filename order breaks ties)
    out.sort(key=lambda s: s.get("requested_at") or 0, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def _device_label() -> Optional[str]:
    """This Mac's user-facing device name — the pairing label the owner set in
    设置 · 同步/配对 (``state/sync.json``, the same value the QR carries).
    None when unpaired / unlabeled / unreadable; the dashboard key is then
    omitted entirely (add-only: old apps ignore it, old payloads lack it)."""
    try:
        cfg = json.loads((config.STATE_DIR / "sync.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    label = str(cfg.get("label") or "").strip()
    return label or None


def build_dashboard(
    reqs: Optional[list[Requirement]] = None,
    agents: Optional[list[dict]] = None,
    cfg: Optional[config.Config] = None,
    merge_dir: Optional[Path] = None,
    archived: Optional[list[Requirement]] = None,
) -> dict:
    """Assemble the dashboard dict (CONTRACT §2). Pure/injectable for testing.

    ``archived`` defaults to :func:`registry.load_archived` (the relocated
    archive/ subdir) — kept a SEPARATE source from ``reqs`` (= load_all, which
    excludes archived) so sealed cards enter ONLY the archived[] partition."""
    if cfg is None:
        cfg = config.load_config()
    if reqs is None:
        reqs = load_all()
    if agents is None:
        agents = _run_claude_agents()
    if archived is None:
        archived = load_archived()
    agent_idx = _index_agents(agents)

    needs_approval: list[dict] = []
    running: list[dict] = []
    needs_input: list[dict] = []
    review: list[dict] = []
    completed: list[dict] = []
    debt: list[dict] = []
    trash: list[dict] = []

    # archive() crash-mid-move 残件去重：archive/ 副本已落盘、active 目录里的
    # 同 id 原件还没删掉时，视 active 残件为"已迁移"跳过——否则同一张卡同时
    # 出现在 completed 和 archived 两个分区、各计一次。
    archived_ids = {_s(r.id) for r in (archived or [])}

    def _project(req: Requirement) -> None:
        # merged (契约 四 终态) is invisible everywhere, like the legacy
        # merged_into:<id> statuses — its content lives on in the primary card.
        # ARCHIVED goes in the belt-and-suspenders list too: sealed cards are
        # meant to live in archive/ (out of ``reqs``), but if one lingers in the
        # active dir it must still stay out of every kanban lane (§5).
        if req.is_merged or req.status in (State.REJECTED.value,
                                           State.MERGED.value,
                                           State.ARCHIVED.value):
            return

        if req.status == State.CARD_SENT.value:
            cost, show_cost = _cost_view(req, cfg)
            target_repo, target_name, target_kind = _target_view(req, cfg)
            # 手改 YAML 把 execution 写成字符串时按"无 execution"降级（同
            # executing 分支的 isinstance 守卫）——不炸整卡。
            ex = req.execution if isinstance(req.execution, dict) else {}
            needs_approval.append(
                {
                    "id": _s(req.id),
                    "title": _s(req.title),
                    "summary": req.summary or _s(req.title),
                    **_title_fields(req),
                    "target_repo": target_repo,
                    "target_name": target_name,
                    "target_kind": target_kind,
                    "tier": _s(req.tier),
                    "tier_hint": TIER_HINTS.get(_s(req.tier), ""),
                    "hardness": req.hardness,
                    "deadline": req.deadline,
                    "days_left": days_left(req.deadline),
                    "repeated": _int_or(req.repeated_mentions, 1) or 1,
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
                    # v0.20.0 §5: 「回锅」marker — this proposal came from a
                    # re-raise of a card the user had already accepted; the app
                    # shows an amber Returned badge + the new ask.
                    "reraised": bool(ex.get("reraised_at")),
                    "reraised_note": str(ex.get("reraised_note") or ""),
                }
            )

        elif req.status == State.RAISING.value:
            # AI is expanding this debt into a proposal — show it in 待审批 as a
            # greyed spinner placeholder so the click gives immediate feedback.
            needs_approval.append(
                {
                    "id": _s(req.id),
                    "title": _s(req.title),
                    "summary": req.summary or _s(req.title),
                    **_title_fields(req),
                    "tier": _s(req.tier),
                    "tier_hint": "AI 研究中",
                    "processing": True,
                    "sources": [],
                    "plan": [],
                    "dod": [],
                    "show_cost": False,
                    "delivery_mode": _delivery_mode(req),
                }
            )

        elif req.status == State.DETECTED.value:
            debt.append(
                {
                    "id": _s(req.id),
                    "title": _s(req.title),
                    "summary": req.summary or _s(req.title),
                    **_title_fields(req),
                    "hardness": req.hardness,
                    "type": req.type,
                    "sources": _source_view(req, cfg),
                }
            )

        elif req.status == State.TRASHED.value:
            trash.append(
                {
                    "id": _s(req.id),
                    "title": _s(req.title),
                    "summary": req.summary or _s(req.title),
                    **_title_fields(req),
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
            running.append(
                {
                    "id": _s(req.id),
                    "name": _s(req.title or req.id),
                    **_title_fields(req),
                    "state": "queued",
                    "summary": req.summary or None,
                    "plan": _as_list(req.plan),
                    "dod": list(req.definition_of_done or []),
                    "delivery_mode": _delivery_mode(req),
                    "dispatch_error": ex.get("last_error") or None,
                    # §25: classification id alongside the raw text (None when
                    # unknown — Swift falls back to the raw string + AI fix).
                    "dispatch_error_id": failures.classify(ex.get("last_error")),
                }
            )

        elif req.status in (State.EXECUTING.value, State.REVIEW.value,
                            State.DELIVERED.value):
            ex = req.execution if isinstance(req.execution, dict) else {}
            sid = ex.get("session_id")
            agent = agent_idx.get(str(sid)) if sid else None
            # prefer the requirement title: claude uses the (huge) injected prompt
            # as the agent "name", which is useless to display.
            name = _s(req.title or (agent or {}).get("name") or req.id)
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
                sid_for_resume = str(resume_sid or short_id or "")
                tinfo = (_transcript_info_cached(sid_for_resume)
                         if sid_for_resume else None)
                if tinfo:
                    copy_cmd = f"cd '{tinfo[1]}' && claude --resume {tinfo[0]}"
                elif sid_for_resume:
                    copy_cmd = f"claude --resume {sid_for_resume}"
                else:
                    # No session id at all — emit NO command rather than guess
                    # (an empty sid used to glob-bind an unrelated transcript).
                    copy_cmd = None
            agent_name = (agent or {}).get("name")

            if req.status == State.DELIVERED.value:
                # §11 已验收 — archive row
                completed.append(
                    {
                        "id": _s(req.id),
                        "name": name,
                        **_title_fields(req),
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
                # §30 fix: a delivered 待验收 card whose session is actively
                # WORKING again (user `claude attach` + real work — e.g. a
                # follow-up deep-research) shows in 运行中 while it runs, instead
                # of sitting stranded in 待验收 with only a badge while the
                # 运行中 lane reads 0. Registry status stays review (NO
                # state-machine flip) — so the ✓验收/↩︎打回 verdict and the
                # delivered draft are preserved; when the session settles it
                # falls straight back into the review branch below, refreshed by
                # _reconcile_review_attach's re-harvest. `from_review` lets the
                # app label it, and the stop button routes via stop_to_review /
                # abort_execution which now accept review status (§10).
                running.append(
                    {
                        "id": _s(req.id),
                        "name": name,
                        **_title_fields(req),
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "cwd": cwd,
                        "state": "working",
                        # §2: wire 上时间戳一律 epoch int——roster 若给 ISO 字
                        # 符串必须归一，否则 Swift 端 started_at: Int? 的合成
                        # decode 一个 typeMismatch 会把整个 running 列清空。
                        "started_at": _epoch((agent or {}).get("started_at")),
                        "summary": req.summary or None,
                        "plan": _as_list(req.plan),
                        "dod": list(req.definition_of_done or []),
                        "log": ex.get("log"),
                        "dispatched_at": _epoch(ex.get("dispatched_at")),
                        "delivery_mode": _delivery_mode(req),
                        "last_error": None,
                        "last_error_id": None,
                        # carried so nothing is lost while it re-runs; the app
                        # can hint "已交付过·再运行" off from_review.
                        "from_review": True,
                        "delivered_summary": ex.get("delivered_summary"),
                        "final_draft": _clip_draft(ex.get("final_draft")),
                    }
                )
            elif req.status == State.REVIEW.value or state in _DONE_STATES:
                # §11 待验收 — draft ready, awaiting Zelin's ✓/↩︎
                # (agent-done-while-still-executing lands here too, covering the
                # gap between dashboard passes and actd's promotion.)
                # §30 session_active: a live WORKING agent on a review card can
                # only be user attach / organic session activity — a genuine 打回
                # verdict (executor.rework) flips review->executing in the same
                # call, so it never presents as review+working. The card stays
                # in this lane (calm「会话有新活动」badge in the app); actd's
                # reconcile keeps re-harvesting deliverables when it settles.
                review.append(
                    {
                        "id": _s(req.id),
                        "name": name,
                        "summary": req.summary or None,
                        **_title_fields(req),
                        "dod": list(req.definition_of_done or []),
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "state": "review",
                        "cwd": cwd,
                        "delivered_summary": ex.get("delivered_summary"),
                        "final_draft": _clip_draft(ex.get("final_draft")),
                        "plan": _as_list(req.plan),
                        "sources": _source_view(req, cfg),
                        "log": ex.get("log"),
                        "dispatched_at": _epoch(ex.get("dispatched_at")),
                        "review_at": _epoch(ex.get("review_at")),
                        "delivery_mode": _delivery_mode(req),
                        "session_active": state in _RUNNING_STATES,
                    }
                )
            elif state in _BLOCKED_STATES:
                needs_input.append(
                    {
                        "id": _s(req.id),
                        "name": name,
                        **_title_fields(req),
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "state": "blocked",
                        "waiting_for": (agent or {}).get("waiting_for") or "input",
                    }
                )
            else:
                # running, or agent not found yet -> still consider it running
                running.append(
                    {
                        "id": _s(req.id),
                        "name": name,
                        **_title_fields(req),
                        "session_id": resume_sid,
                        "short_id": short_id,
                        "copy_cmd": copy_cmd,
                        "agent_name": agent_name,
                        "cwd": cwd,
                        "state": "working" if state in _RUNNING_STATES else state,
                        # epoch 归一，理由同 §30 from_review 分支（Swift Int?）。
                        "started_at": _epoch((agent or {}).get("started_at")),
                        "summary": req.summary or None,
                        "plan": _as_list(req.plan),
                        "dod": list(req.definition_of_done or []),
                        "log": ex.get("log"),
                        "dispatched_at": _epoch(ex.get("dispatched_at")),
                        "delivery_mode": _delivery_mode(req),
                        "last_error": ex.get("last_error"),
                        "last_error_id": failures.classify(ex.get("last_error")),
                    }
                )
        # approved surfaces as a "queued" item inside running (branch above, §2)

    for req in reqs:
        if _s(req.id) and _s(req.id) in archived_ids:
            continue  # crash-mid-move 残件——archive/ 里已有权威副本（上方注释）
        try:
            _project(req)
        except Exception as e:  # noqa: BLE001 - 单卡隔离，见下
            # 手改 YAML 把某个字段改坏（execution 变字符串、dod 变 int……）时：
            # 跳过这一张卡 + log，其余卡照常投影——绝不让一张坏卡冻结整个
            # dashboard pass（同 merge_suggestions 分区"损坏文件跳过"的既有约定）。
            print(f"dashboard: skip corrupt card {getattr(req, 'id', '?')!r}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    # §2 completed cap: newest first (missing/unparsable accepted_at sinks to
    # the end), truncated to COMPLETED_CAP; the count keeps the real total.
    completed_total = len(completed)
    completed.sort(key=lambda c: c.get("accepted_at") or 0, reverse=True)
    del completed[COMPLETED_CAP:]

    # §5 v0.20.0 archived[] partition — mirrors the trash row (+ archive fields)
    # so the app's archive browse decodes it the same way; newest archived_at
    # first, capped, with counts.archived carrying the TRUE total.
    archived_rows = []
    for r in (archived or []):
        try:
            archived_rows.append(_archived_view(r))
        except Exception as e:  # noqa: BLE001 - 单卡隔离，同上
            print(f"dashboard: skip corrupt archived card "
                  f"{getattr(r, 'id', '?')!r}: {type(e).__name__}: {e}",
                  file=sys.stderr)
    archived_total = len(archived_rows)
    archived_rows.sort(key=lambda a: str(a.get("archived_at") or ""), reverse=True)
    del archived_rows[ARCHIVED_CAP:]

    dash = {
        "generated_at": _iso_now(),
        "counts": {
            "needs_approval": len(needs_approval),
            "running": len(running),
            "needs_input": len(needs_input),
            "review": len(review),
            "completed": completed_total,
            "debt": len(debt),
            "trash": len(trash),
            "archived": archived_total,
        },
        "needs_approval": needs_approval,
        "running": running,
        "needs_input": needs_input,
        "review": review,
        "completed": completed,
        "debt": debt,
        "trash": trash,
        "archived": archived_rows,
        # merge-review 契约 六 — new partition; Swift reads decodeIfPresent so
        # older apps simply ignore it.
        "merge_suggestions": _merge_suggestions(merge_dir),
    }
    # v0.35 device_label — §2 sibling field (add-only, CONTRACT §35): lets a
    # paired phone adopt a Mac rename from the board payload without re-scanning
    # the QR. Omitted (not null) when unpaired / unlabeled.
    label = _device_label()
    if label:
        dash["device_label"] = label
    return dash


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
