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

from act.lib import config, failures, health, policy, risk, sources, steer, titles
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
# needs_input question memoization (§39)
# --------------------------------------------------------------------------- #
# executor.extract_question reads + json-parses the FULL transcript, and a
# genuinely blocked agent sits with an unchanged transcript for hours — so the
# ~10s pass must never re-parse an idle one. Same (path, mtime, size)
# freshness-signature scheme as _TINFO_CACHE (the v0.33.1 tinfo memo
# precedent): an appended transcript (the agent said more / got answered)
# invalidates immediately, an idle one costs only the stat calls.
_QUESTION_CACHE: dict[str, tuple[tuple, Optional[str]]] = {}
_QUESTION_CACHE_MAX = 512


def _question_cached(sid: str) -> Optional[str]:
    from act.executor import extract_question  # lazy: keep dashboard import-light
    sig = _transcript_sig(sid)
    if sig is None:
        return extract_question(sid)
    hit = _QUESTION_CACHE.get(sid)
    if hit is not None and hit[0] == sig:
        return hit[1]
    q = extract_question(sid)
    if len(_QUESTION_CACHE) >= _QUESTION_CACHE_MAX:
        _QUESTION_CACHE.clear()
    _QUESTION_CACHE[sid] = (sig, q)
    return q


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
    """ISO hard-delete deadline for a trash row (§40): trashed_at + retention.

    None (key emitted as null) when the row is pinned, retention is disabled
    (``trash_retention_days <= 0``), or ``trashed_at`` doesn't parse — EXACTLY
    the conditions under which actd.purge_trash skips the row, so the countdown
    never promises a purge that isn't coming. The parse below mirrors
    actd._parse_iso byte-for-byte (NOT the laxer _epoch, which accepts bare
    numerics purge_trash rejects — a numeric trashed_at used to show a red
    countdown for a purge that would never happen)."""
    days = int(cfg.trash_retention_days or 0)
    if days <= 0 or req.permanent:
        return None
    ts = req.trashed_at
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        trashed = _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        try:
            trashed = _dt.datetime.strptime(str(ts).strip(),
                                            "%Y-%m-%dT%H:%M:%SZ")
            trashed = trashed.replace(tzinfo=_dt.timezone.utc)
        except (TypeError, ValueError):
            return None
    if trashed.tzinfo is None:
        trashed = trashed.replace(tzinfo=_dt.timezone.utc)
    dt = trashed.astimezone(_dt.timezone.utc) + _dt.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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
                    # v0.20.0 §5: 「回锅」marker — this proposal came from a
                    # re-raise of a card the user had already accepted; the app
                    # shows an amber Returned badge + the new ask.
                    "reraised": bool(ex.get("reraised_at")),
                    "reraised_note": str(ex.get("reraised_note") or ""),
                    # v-next add-only（§50/§51/C-6）：出身章 + auto-dispatch
                    # 拦下原因（origin:*/disabled 常态原因不上卡，见 actd）。
                    **_opt("origin_trust", getattr(req, "origin_trust", None)),
                    **_opt("auto_dispatch_block", ex.get("auto_dispatch_block")),
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
                    # v-next add-only（§50）
                    **_opt("origin_trust", getattr(req, "origin_trust", None)),
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
            elif state in _BLOCKED_STATES or (
                    not (agent or {}).get("pid") and ex.get("resume_exhausted")
                    and not ex.get("done")):
                # §39: surface WHAT the agent is asking — the transcript's
                # last assistant text after the last real user turn (the same
                # fence/sidechain-disciplined extraction harvest uses), cached
                # per (sid, transcript signature) above.
                # §46 第二臂：auto-resume 已放弃（resume_exhausted，含 resume
                # 风暴降级）且会话已无活 pid 的 executing 卡 —— 事实上就是
                # 「需要人才能推进」，投影进 需输入 列（回答…/停止 都在这里），
                # 不再顶着 unknown 状态在 运行中 列装忙（宪法 3：诚实的健康报告）。
                # 死的判据 = 无活 pid（本文件 copy_cmd 的既有活性判据），不是
                # 「不在 roster」——roster --all 会给 failed/stopped 留死条目，
                # agent is None 判死会让这些卡继续在 running 里装忙。
                degraded = (not (agent or {}).get("pid")
                            and ex.get("resume_exhausted"))
                # 降级卡的 question 用固定文案：死 transcript 的最后一条
                # assistant 文本不是提问（agent 并没有在等这个答案），拿来
                # 当 question 展示是误导——固定文案说清事实和两个现存出口。
                if degraded:
                    question = failures.pick(
                        "自动救活多次后仍中断，需要人工确认：点「回答…」给它"
                        "指示继续，或点「停止」",
                        'Auto-resume kept failing; needs a human call — press '
                        '"Answer…" to instruct it onward, or "Stop"')
                else:
                    question = (_question_cached(str(resume_sid or sid))
                                if sid else None)
                row = {
                    "id": _s(req.id),
                    "name": name,
                    **_title_fields(req),
                    "session_id": resume_sid,
                    "short_id": short_id,
                    "copy_cmd": copy_cmd,
                    "agent_name": agent_name,
                    "state": "blocked",
                    # §39: the bare "input" fallback stays ONLY when no
                    # transcript text exists — next to a real question it
                    # was pure noise.
                    "waiting_for": ((agent or {}).get("waiting_for")
                                    or (None if question else "input")),
                    # §39: an undeliverable answer (executor.answer failure)
                    # must be visible ON the card, not just in a notification.
                    "last_error": ex.get("last_error"),
                    "last_error_id": failures.classify(ex.get("last_error")),
                }
                if question:
                    row["question"] = question
                if degraded:
                    # §46 add-only：告诉 App（和 detect_transitions）这行是
                    # 降级卡，不是 agent 真的在提问 —— 老 App decodeIfPresent
                    # 直接忽略。
                    row["resume_exhausted"] = True
                # v-next §M6.1：steer 三态诚实回执（queued/delivered）
                steers = _steers_view(req)
                row.update(_opt("steers", steers))
                row.update(_opt("origin_trust", getattr(req, "origin_trust", None)))
                needs_input.append(row)
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
