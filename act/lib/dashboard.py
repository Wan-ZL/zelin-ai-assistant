"""Dashboard builder — produces ``state/dashboard.json`` (CONTRACT §2).

actd writes this file; the Mac app reads it (never writes). The write is atomic
(``.tmp`` then ``rename``). Running/completed partitions come from joining
registry ``status=executing`` items with ``claude agents --json --all`` by
``session_id``. ``needs_input[]`` carries ONLY §4 dispatch-halted rows since
v0.48.8 (#119 — the session blocked/waiting join is retired; wire key stays,
add-only).

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

from act.lib import (card_summary, config, daily_loop, deploy_state, failures, health,
                     maintenance, policy, recap_store, risk, self_improve, sources, steer,
                     titles, transcripts)
from act.lib import registry as registry_ids   # §60 display_id / id_kind 单点
from act.lib.agent_states import _DONE_STATES, _RUNNING_STATES
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
# transcripts.transcript_info reads + json-parses the FULL transcript of a
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
    ``transcripts.transcript_info(sid)`` would consider — the glob pattern must
    stay in sync with that module's. None = can't sign (short sid / OSError):
    the caller falls through to an uncached lookup, never a stale answer."""
    short = str(sid or "").split("-")[0]
    if len(short) < 8:  # transcripts' guard: anything shorter globs everything
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
    sig = _transcript_sig(sid)
    if sig is None:
        return transcripts.transcript_info(sid)
    hit = _TINFO_CACHE.get(sid)
    if hit is not None and hit[0] == sig:
        return hit[1]
    info = transcripts.transcript_info(sid)
    if len(_TINFO_CACHE) >= _TINFO_CACHE_MAX:
        _TINFO_CACHE.clear()
    _TINFO_CACHE[sid] = (sig, info)
    return info


# （§39 needs_input question 记忆化：retired v0.48.8（#119）——受阻会话由
# reconcile 直接收割进待验收，投影不再提取「会话在问什么」。）


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


def _delivery_view(ex: dict) -> Optional[dict]:
    """§65.3 review 行 `delivery`：execution.delivery 的 wire 形（缺失 = None →
    整键省略）。字段逐字镜像 self_improve.verify_delivery 的结果，不翻译。"""
    delivery = self_improve.delivery_of({"execution": ex})
    return dict(delivery) if delivery else None


def _self_improve_view(cfg: config.Config) -> dict:
    """§65 顶层 `self_improve`：读不到状态文件也给一个完整形状（宪法第 11 条）。"""
    try:
        return self_improve.board_view(cfg)
    except Exception as e:  # noqa: BLE001 - 投影绝不因通道状态文件崩
        print(f"dashboard: self_improve view failed: {e}", file=sys.stderr)
        return {"enabled": False, "paused": False, "error": str(e)[:200]}


def _opt(key: str, value: Any) -> dict:
    """可选 wire 字段的整键省略展开：``**_opt("origin_trust", v)``。

    值为假（None/""/0/False）时返回空 dict——「缺章 = 整键不存在」是 add-only
    字段的既定读侧语义（老 App 的 decodeIfPresent 与 web 的 `?? fallback` 都
    按这个来，§50 origin_trust / §51 auto_dispatch_block / §M6.1 steers 同款）。
    """
    return {key: value} if value else {}


def _int_or(v: Any, default: int) -> int:
    """损坏的数字字段（``repeated_mentions: abc``）降级成 default，不让一张
    坏卡把整个 dashboard pass 炸掉。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _assessment_view(req: Requirement) -> dict:
    """§64 AI 摘要 + 完成度评语的 wire 形：``{"assessment": {summary, verdict,
    verdict_reason, at(epoch)}}``，只在有摘要或评语**且指纹与当前内容一致**时整键出现
    （失败标记行、内容已变而判官未归的过时评语都不投影——没有章就是没有章，不给客户端
    一个空壳或旧话去猜）。只是建议：客户端只渲染，不据此动状态。"""
    a = getattr(req, "assessment", None)
    if not isinstance(a, dict) or not card_summary.has_content(a):
        return {}
    if not card_summary.is_fresh(req):
        return {}
    verdict = a.get("verdict")
    return {"assessment": {
        "summary": _opt_str(a.get("summary")),
        "verdict": verdict if verdict in card_summary.VERDICTS else None,
        "verdict_reason": _opt_str(a.get("verdict_reason")),
        "at": _epoch(a.get("at")),
    }}


def _opt_str(v: Any) -> Optional[str]:
    s = str(v or "")
    return s or None


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
                # §10 add-only（issue #7）：出生 capture 的 inbox stem；Swift
                # Source 合成 Decodable 忽略多余键，web 可选读
                **_opt("capture_id", s.get("capture_id")),
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


def _cost_view(req: Requirement, cfg: config.Config
               ) -> tuple[Optional[float], bool, str]:
    """(cost_usd, show_cost, cost_state) for a proposal card (§40).

    ``cost_state`` is the honesty bit: "estimated" when a number exists,
    "unknown" when there is none (direct-run promotions, capture fallbacks,
    digest suggestions, corrupt values) — the app says 成本未知 instead of
    letting a missing estimate read as free. ``show_cost`` keeps gating only
    the collapsed badge (≥ show_cost_above_usd); the expanded detail always
    states the money story regardless of the threshold."""
    cost = req.cost_estimate_usd
    if cost is None:
        return None, False, "unknown"
    try:
        c = float(cost)
    except (TypeError, ValueError):
        # ``cost_estimate_usd: cheap`` 之类的坏值：字段降级成"无成本估算"，
        # 卡片其余部分照常投影（单字段损坏不丢整卡，更不丢整个 pass）。
        return None, False, "unknown"
    return c, c >= cfg.show_cost_above_usd, "estimated"


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _purge_at(req: Requirement, cfg: config.Config) -> Optional[str]:
    """ISO hard-delete deadline for a trash row (§40.5): trashed_at + retention.

    None (key emitted as null) when the row is pinned, retention is disabled
    (``trash_retention_days <= 0``), or ``trashed_at`` doesn't parse — EXACTLY
    the conditions under which actd.purge_trash skips the row, so the countdown
    never promises a purge that isn't coming. §70: one judge for both sides
    (maintenance.purge_at / purge_due) — loop-trashed rows (stale:* /
    daily-merge:*) carry their own, longer retention."""
    return maintenance.purge_at(req, cfg)


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


# §7 egress[] 词表（issue #11）：批准这张卡会触发的**出机**后果，每条一个 kind。
# 目前唯一住户 = github_repo_create；后续 kinds 只增不改（add-only）。
EGRESS_GITHUB_REPO_CREATE = "github_repo_create"


def _will_bootstrap_repo(req: Requirement, cfg: config.Config) -> Optional[Path]:
    """The directory ``executor.dispatch`` would hand to ``ensure_repo`` for
    this card, or None when no repo bootstrap happens. Same predicate as the
    executor, re-derived here (act/lib may not import act/executor): repo
    delivery only (chat never touches a repo, §20); target = explicit
    ``target_repo`` else the configured default repo — **not** §7's
    ``_target_view`` shortcut, which reports the default as "existing" without
    looking at the disk (Codex review of #158: an empty/missing default dir
    projected ``egress=[]`` while dispatch still ran ``gh repo create``);
    bootstrap when the stored ``target_kind`` says new OR the dir is
    missing/empty right now (``compute_target_kind``)."""
    if _delivery_mode(req) != "repo":
        return None
    target = Path(req.target_repo).expanduser() if req.target_repo else cfg.target_repo_path
    if req.target_kind == "new" or not _dir_is_nonempty(target):
        return target
    return None


def _egress_view(req: Requirement, cfg: config.Config) -> list[dict]:
    """§7 add-only ``egress[]``: the out-of-machine consequences approving this
    card will trigger, disclosed on the approval card itself (the security
    boundary of the product, issue #11 / PRIVACY.md egress row 8).

    Mirrors the executor's ``ensure_repo`` gate (:func:`_will_bootstrap_repo`)
    + config ``execution.create_github_repo`` on → the dispatch runs
    ``gh repo create <name> --private`` and pushes screen/meeting/mail-derived
    content to GitHub. Flag off (the default) → always ``[]`` — nothing
    changes for existing installs. ``gh`` missing at dispatch time keeps the
    repo local (PRIVACY.md); the card still discloses the intent, because the
    approval decision must not depend on a binary the user cannot see."""
    if not cfg.create_github_repo:
        return []
    target = _will_bootstrap_repo(req, cfg)
    if target is None:
        return []
    return [{"kind": EGRESS_GITHUB_REPO_CREATE, "target": target.name,
             "visibility": "private"}]


def _capture_id(req: Requirement) -> Optional[str]:
    """§10 add-only ``capture_id`` (issue #7): the inbox stem of the capture that
    minted this card = the first ``sources[]`` row carrying one (birth row;
    folds append later rows and never rewrite it). None when the card was not
    born from an inbox capture (radar, Slack self-DM, digest…)."""
    for s in req.sources or []:
        if isinstance(s, dict) and s.get("capture_id"):
            return str(s["capture_id"])
    return None


def _proposal_extras(req: Requirement, ex: dict, cfg: config.Config) -> dict:
    """The add-only tail of a needs_approval (card_sent) row — kept out of
    ``build_dashboard._project`` so the projection body stays under the
    §58 function-length ledger. Every key here is optional/add-only:

    - ``reraised`` / ``reraised_note`` (v0.20.0 §5 「回锅」marker: this
      proposal is a re-raise of a card the user already accepted — amber
      Returned badge + the new ask);
    - ``origin_trust`` / ``auto_dispatch_block`` (§50/§51/C-6: 出身章 +
      auto-dispatch 拦下原因；origin:*/disabled 常态原因不上卡，见 actd) —
      whole key omitted when empty;
    - ``egress`` (§7, issue #11): what leaves the machine on approval — always
      a list, ``[]`` = nothing;
    - ``capture_id`` (§10, issue #7): inbox stem of the birth capture, omitted
      when the card was not born from one."""
    return {
        "reraised": bool(ex.get("reraised_at")),
        "reraised_note": str(ex.get("reraised_note") or ""),
        **_opt("origin_trust", getattr(req, "origin_trust", None)),
        **_opt("auto_dispatch_block", ex.get("auto_dispatch_block")),
        "egress": _egress_view(req, cfg),
        **_opt("capture_id", _capture_id(req)),
    }


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
    something to say; Swift reads them with decodeIfPresent.

    §60（D21）两段式编号的投影面也挂在这里（每条 lane 行都 spread 本函数，
    一个钩子全覆盖）：``display_id``（恒在 = work_id or id）、``work_id``
    （有才发）、``id_kind``（work | legacy | proposal；web 据此灰显存量 R
    主键，不许在客户端按前缀猜——防腐 #10）。``id`` 本身不动：动作回传仍
    用主键。"""
    out: dict = {
        "display_title": _display_title(req),
        "display_id": _s(registry_ids.display_id(req)),
        "id_kind": registry_ids.id_kind(req),
        **_opt("work_id", getattr(req, "work_id", None)),
    }
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
        item = {
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
        # partition（§21 多对多分组）的分组方案 — add-only key：只在作业带着
        # 合法形状时外发（老作业/其余 verdict 连键都没有，Swift decodeIfPresent
        # 向后兼容）；坏形条目逐个跳过，同本分区"损坏跳过"的既有约定。
        groups = data.get("groups")
        if isinstance(groups, list):
            g_out = []
            for g in groups:
                if not isinstance(g, dict):
                    continue
                gids = g.get("ids")
                reason = g.get("reason")
                g_out.append({
                    "primary": str(g.get("primary") or ""),
                    "ids": ([str(i) for i in gids]
                            if isinstance(gids, list) else []),
                    "reason": str(reason) if reason not in (None, "") else None,
                })
            if g_out:
                item["groups"] = g_out
        out.append(item)
    # newest request first (stable: filename order breaks ties)
    out.sort(key=lambda s: s.get("requested_at") or 0, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# v-next 投影辅助（amendments §51/§M6/M8.3 C-2·C-3·C-4，全部 add-only optional）
# --------------------------------------------------------------------------- #
def _queued_reason_view(req: Requirement, state: dict) -> Optional[dict]:
    """M1.c token → 结构化 wire 形（M8.3 C-2 终裁为 canonical）：
    dependency → {kind: waiting_card, blocking_id}｜concurrency → {kind:
    concurrency}。None = 无阻塞（纯粹没轮到/派发失败退避——后者由
    dispatch_error 独立表达，不混写）。`waiting_budget` retired v0.48.7（D9），
    kind 值永不复用。"""
    token = policy.queued_reason(req, state)
    if token == "dependency":
        blocking = state.get("blocked_by")
        first = blocking[0] if isinstance(blocking, list) and blocking else None
        out = {"kind": "waiting_card"}
        if first:
            # 主键（lineage 口径）。§60 追记：blocked_by 至今无生产者（T-26
            # 未立法）；立法时须同车加 add-only ``blocking_display_id`` =
            # 前置卡的 display_id，web chip「等 R-xx」不得拿主键充数。
            out["blocking_id"] = str(first)
        return out
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
def _fold_receipts() -> list[dict]:
    """§44.6 并入回执投影（never raises）。

    回执文件只存 channel + 目标卡 id（隐私红线：dashboard 整包上云，被并入
    内容原文不得出机）——投影文案所需的主卡显示名在这里由 registry 现查
    （``title`` = §37 display_title 链，本就已随卡片行进 dashboard，不是
    新增外泄面）；目标卡已消失（归档/回收）则留空，App 端只报 R-xxx。
    """
    from act.lib import fold_receipts, registry
    out: list[dict] = []
    for e in fold_receipts.load_recent():
        title = ""
        try:
            req = registry.load(e["req"])
            if req is not None:
                title = _display_title(req)
        except Exception:  # noqa: BLE001 - 回执是尽力而为的观测面（宪法 11）
            title = ""
        e["title"] = title
        out.append(e)
    return out


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


def _radar_sources(cfg: config.Config) -> dict:
    """§48 add-only 投影 ``radar_sources``：源开关 intent + 健康摘要一处出。

    形状（每个 act.lib.sources.SOURCES 成员一条，键恒在）::

        {"gmail": {"enabled": bool, "last_ok": iso|null,
                   "skip_reason": str|null, "stale": bool}, ...}

    ``enabled`` 来自真源 sources.enabled()（App 侧的 intent 判断自此读这里，
    不再猜「凭证文件非空」）；``last_ok``/``skip_reason`` 摘自 radar_health
    条目（关掉的源条目已被清除 → null）；``stale`` = 开着且超 liveness 阈值
    没有成功信号（告警的看板投影，恢复后自动变回 false）。配置**现读**——
    actd 启动时冻结的 cfg 在 App 翻开关后失真，投影必须跟着磁盘上的真值走
    （load_config 失败才回退传入的 cfg）。Never raises。
    """
    try:
        cfg = config.load_config()
    except Exception:  # noqa: BLE001 - 坏 config 回退调用方快照，不崩投影
        pass
    out: dict = {}
    try:
        data = health.load_radar_health()
    except Exception:  # noqa: BLE001 - 健康文件坏了不许崩 dashboard
        data = {}
    now = _dt.datetime.now(_dt.timezone.utc)
    for src in sources.SOURCES:
        try:
            on = sources.enabled(cfg, src)
        except Exception:  # noqa: BLE001
            on = False
        entry = data.get(src) if isinstance(data, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        # 关掉的源不带健康摘要：清理僵尸条目发生在 liveness 巡检（同 pass 的
        # dashboard 构建在它之前）——不在这里屏蔽的话，关源后的第一个 pass
        # 会把旧 last_ok/skip_reason 投影出去（关着 = null 的契约被破一拍）。
        if not on:
            entry = {}
        last_ok = entry.get("last_ok")
        # §48.4 出机清洗：skip_reason 只放行闭集词表码（词表外折叠 "error"，
        # mcp_failed:<detail> 去尾）——dashboard 随 syncd 出机，radar 写进
        # health 的自由文本（错误摘录/本机路径）不许跟着出去。
        skip = health.public_skip_reason(entry.get("skip_reason"))
        out[src] = {
            "enabled": on,
            "last_ok": last_ok if isinstance(last_ok, str) and last_ok else None,
            "skip_reason": skip if isinstance(skip, str) and skip else None,
            # stale **不吃** actd 的睡醒/冷启动宽限（§48.4）：投影是无状态的
            # 磁盘真值函数（`python -m act.lib.dashboard` 一次性进程也在跑，
            # 进程级宽限状态会让 CLI 构建永远压掉 stale）；告警宽限是通知侧
            # 的关切。睡醒后的一轮假 stale 随雷达补跑自愈，消费者自行防抖。
            "stale": bool(on and sources.is_stale(src, entry, now)),
        }
    return out


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

    # v-next queued_reason 快照（§51）：并发口径与 actd.dispatch_approved 一致
    # （EXECUTING 且带 session 的卡数）。预算口径 retired v0.48.7（D9）。
    ad_cfg = policy.autodispatch_config(cfg)
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
            cost, show_cost, cost_state = _cost_view(req, cfg)
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
                    # W17 add-only：生效档位（外部来源强制 T2；否则同声明 tier）。
                    # v0.48.1（§50）：外部出身 = 显式 origin_trust=external 章
                    # **或** sources 现算为 external——缺章卡也从 sources 现算，
                    # 不再恒等于 tier。审批语义仍由 effective_tier 决定，
                    # 客户端 decodeIfPresent 兼容（缺字段回落 tier）。
                    "effective_tier": risk.effective_tier(req).tier,
                    "hardness": req.hardness,
                    "deadline": req.deadline,
                    "days_left": days_left(req.deadline),
                    "repeated": _int_or(req.repeated_mentions, 1) or 1,
                    # §44 add-only: silent fold-in events (0 = never)
                    "silent_merged": _int_or(
                        getattr(req, "silent_merge_count", 0), 0) or 0,
                    "cost_usd": cost,
                    "show_cost": show_cost,
                    # §40 add-only: "estimated"|"unknown" — the app renders
                    # 成本未知 for unknown instead of an implied $0.
                    "cost_state": cost_state,
                    "green_sign": bool(req.green_sign_required),
                    "disagreement": req.disagreement,
                    "improvement_of": req.improvement_of,
                    "sources": _source_view(req, cfg),
                    "plan": _as_list(req.plan),
                    "outputs": list(req.outputs or []),
                    "dod": list(req.definition_of_done or []),
                    "processing": False,
                    "delivery_mode": _delivery_mode(req),
                    **_proposal_extras(req, ex, cfg),
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
                    "effective_tier": risk.effective_tier(req).tier,  # W17 add-only
                    "tier_hint": "AI 研究中",
                    "processing": True,
                    "sources": [],
                    "plan": [],
                    "dod": [],
                    "show_cost": False,
                    "delivery_mode": _delivery_mode(req),
                    # v-next add-only（§50）；§10 capture_id（issue #7）——占位
                    # 行就带，客户端对账「我刚输入的那条」不用等扩写完成
                    **_opt("origin_trust", getattr(req, "origin_trust", None)),
                    **_opt("capture_id", _capture_id(req)),
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
                    # §40 add-only: when actd WILL hard-delete this row (null =
                    # pinned / retention off / unparsable trashed_at = never).
                    "purge_at": _purge_at(req, cfg),
                    "type": req.type,
                    "hardness": req.hardness,
                }
            )

        elif req.status == State.APPROVED.value and (
                isinstance(req.execution, dict)
                and req.execution.get("dispatch_halted")):
            # §4 派发风暴刹车已触发：卡仍 approved，但 actd 不再重试——投影进
            # 「需输入」列（blocked 行形），而不是在 运行中 列顶着「排队中」
            # 装忙（宪法 3：诚实的健康报告）。question 是固定文案：这里没有
            # agent 在提问，说的是事实和唯一出口（停止 → 退回提案 → 重批）。
            ex = req.execution
            fid = failures.classify(ex.get("last_error"))
            hint = failures.user_message(fid) or _s(ex.get("last_error")) or ""
            n = int(ex.get("dispatch_class_streak") or ex.get("dispatch_attempts") or 0)
            row = {
                "id": _s(req.id),
                "name": _s(req.title or req.id),
                **_title_fields(req),
                "session_id": None,
                "short_id": None,
                "copy_cmd": None,
                "agent_name": None,
                "state": "blocked",
                "waiting_for": None,
                "question": failures.pick(
                    f"派发连续失败 {n} 次，已停止自动重试：{hint}。修好原因后点"
                    "「停止」选「退回提案」，再重新批准即恢复派发",
                    f"Launch failed {n} times in a row; auto-retry stopped: {hint}. "
                    "Fix the cause, then press \"Stop\" → \"Discard & re-propose\" "
                    "and approve again to resume"),
                "last_error": ex.get("last_error"),
                "last_error_id": fid,
                # add-only（decodeIfPresent）：告诉客户端这是刹车行，不是 agent
                # 在等回答——web 据此隐藏「回答…」、显示派发次数 chip。
                "dispatch_halted": True,
                "dispatch_attempts": int(ex.get("dispatch_attempts") or 0),
                **_opt("origin_trust", getattr(req, "origin_trust", None)),
            }
            needs_input.append(row)

        elif req.status == State.APPROVED.value:
            # §2 queued 项：已批准但还没（成功）派发 —— 混入 running 分区，✅ 一点
            # 下去立刻有回显。没有会话可 attach，所以无 session_id/copy_cmd；
            # dispatch_error = 上次派发失败原因（重试成功后消失）。
            ex = req.execution if isinstance(req.execution, dict) else {}
            # v-next §51：排队原因 chip（结构化 wire 形，C-2）。快照口径与
            # actd.dispatch_approved 的闸完全一致；blocked_by 依赖字段未立法
            # （T-26），现行只有 concurrency 一因（budget retired v0.48.7，D9）。
            # 与 dispatch_error 并存不混写。
            snap: dict = {"running": live_sessions,
                          "max_concurrent": ad_cfg["max_concurrent"]}
            qr = _queued_reason_view(req, snap)
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
                    **_opt("queued_reason", qr),
                    **_opt("origin_trust", getattr(req, "origin_trust", None)),
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
                        **_assessment_view(req),   # §64 摘要一句（评于待验收期）
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
                        # §2: wire 上时间戳一律 epoch int——roster 若给 ISO 字符串必须归一，
                        # 否则 Swift 端 started_at: Int? 的合成 decode 一个 typeMismatch 会把整个 running 列清空。
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
                        # #119 add-only：「中断收割」（受阻/放弃救活收进待验收）而非正常交付——
                        # detect_transitions 据此不发「AI 已交付草稿」，客户端 decodeIfPresent 可标注。
                        **_opt("interrupted", bool(ex.get("interrupted_reason"))),
                        **_assessment_view(req),   # §64 AI 摘要 + 评语（只是建议）
                        **_opt("delivery", _delivery_view(ex)),   # §65.3 add-only：gh 核验结果原样
                    }
                )
            # #119（v0.48.8）：受阻/放弃救活的会话不再投影「需输入」——
            # reconcile 会在下一个 pass 把它们收割进待验收；投影间隙里它们
            # 留在 运行中 列（state 原样，诚实呈现），不再有「回答…」入口。
            # needs_input[] 只剩 §4 派发刹车行（上方 dispatch_halted 分支）。
            else:
                # running, or agent not found yet -> still consider it running
                steers = _steers_view(req)
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
                        # v-next §M6.1：steer 三态诚实回执（queued/delivered；
                        # dropped 不投影，notes 痕承担可见性——C-3）
                        **_opt("steers", steers),
                        **_opt("origin_trust", getattr(req, "origin_trust", None)),
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
        # §44.6 静默并入回执 — add-only 顶层键（decodeIfPresent 向后兼容）：
        # radar/capture 通道的 fold 发生时留在 state/fold_receipts/ 的短暂
        # 回执，App 端渲染为一行可消失的「已并入 R-xxx」提示。
        "fold_receipts": _fold_receipts(),
        # §48 add-only：源开关 intent + 健康摘要投影（Swift decodeIfPresent，
        # 旧 app 忽略；App 侧诊断卡的告警资格自此由 Python 一处裁定）。
        "radar_sources": _radar_sources(cfg),
        "self_improve": _self_improve_view(cfg),   # §65 add-only 顶层键：通道开关 + 暂停状态
    }
    # v0.35 device_label — §2 sibling field (add-only, CONTRACT §35): lets a
    # paired phone adopt a Mac rename from the board payload without re-scanning
    # the QR. Omitted (not null) when unpaired / unlabeled.
    label = _device_label()
    if label:
        dash["device_label"] = label
    # §56 / §70 add-only 顶层键 deploy_state / maintenance（同 device_label 的加法约定：文件缺失或读不了 = 整键不存在）
    deploy_state.attach(dash)
    daily_loop.attach(dash, cfg)
    return recap_store.attach(dash)  # §63 add-only 顶层键 recaps[]（会议 recap，不是卡）


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
