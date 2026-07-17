"""actd — the assistant daemon loop.

Each pass:
  (a) drain STATE/inbox/*.json decisions
        approve  -> status=approved
        reject   -> status=rejected
        comment  -> fold text into plan/notes, keep card_sent (re-approval)
        merge_review / merge_apply / merge_dismiss -> merge-review 契约 一/四/五
      delete the decision file after reading it.
  (b) dispatch every status=approved requirement that has no execution yet.
  (b') merge-review housekeeping: TTL-sweep state/merge/ job files; fail
       'analyzing' jobs older than 20 minutes.
  (b'') feedback upload retry (§29): pending state/feedback/ records get ONE
        more attempt, then uploaded:false (kept local, never retried again).
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
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, notify, registry
from act.lib.agent_states import (
    _BLOCKED_STATES,
    _DONE_STATES,
    _LIVE_STATES,
    _RUNNING_STATES,
)
from act.lib.dashboard import (
    build_dashboard,
    write_dashboard,
    _run_claude_agents,
    _index_agents,
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
    except (OSError, UnicodeError):
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
            # §5.4 ack: a terminal disposition even when unreadable, so the phone
            # never sees a stuck 'delivered' → false "未送达" retry loop.
            _write_applied_ack(path.stem, "bad_json")
            _safe_unlink(path)
            continue
        if not isinstance(decision, dict):
            # legal JSON but not an object (null/number/string/list): treating
            # it like a decision would AttributeError OUTSIDE any guard, the
            # file would survive, and — processed in mtime order — the poison
            # file would re-crash every pass, wedging the whole inbox
            # (nightly audit 2026-07-14, blocker).
            _log(f"inbox: decision file {path.name} is not a JSON object "
                 f"({type(decision).__name__}) — discarding")
            _write_applied_ack(path.stem, "bad_json")
            _safe_unlink(path)
            continue

        try:
            req_id = decision.get("id")
            action = decision.get("action")
            comment = decision.get("comment")
            # webui/syncd forward `comment` verbatim from the wire, so a
            # non-string here would AttributeError deep inside the apply path
            # AFTER state changes landed — the file would survive and re-crash
            # every mtime-ordered pass (the non-dict poison class, one field
            # deeper). Never trust wire field types: coerce to None.
            if not isinstance(comment, str):
                comment = None
            # §5.4 sync preconditions carried by the phone (absent for Mac-app files):
            # expected_status pins the card state the phone SAW, board_seq the board
            # revision — a stale action whose precondition no longer holds is a no-op.
            expected_status = decision.get("expected_status")
            board_seq = decision.get("board_seq")

            # §10 capture: no req id — the app popover's one-liner quick capture.
            # v0.34.0: optional mode="run" (运行中 lane input) skips the proposal
            # gate — the card is filed straight into the approved queue.
            if action == "capture":
                result = _apply_capture(decision.get("text"), decision.get("mode"))
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            # §38 split_note (拆成新卡): carries id + note_ts (the fold-note
            # line's ts tag) — the reversible-fold undo, own branch because of
            # the extra field (triple-validated: syncd shape gate + webui 400
            # + the honest no-ops inside).
            if action == "split_note":
                result = _apply_split_note(decision.get("id"),
                                           decision.get("note_ts"))
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            # §29 feedback（建议上报）: carries "ids" (0..n R-/MS- ids), never a
            # requirement-level "id" — validated + recorded by act/lib/feedback.py.
            if action == "feedback":
                result = _apply_feedback(decision)
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            # merge-review actions (§21) — suggestion-level, not requirement-level:
            # merge_review carries "ids" (>=2 R-ids); merge_apply/merge_dismiss carry
            # id=<MS-suggestion id>. None of them go through the req lookup below.
            if action == "merge_review":
                result = _apply_merge_review(decision.get("ids"))
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue
            if action in ("merge_apply", "merge_dismiss"):
                result = _apply_merge_decision(action, decision.get("id"))
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue
            # 强制合并（§21 v0.31）: user-chosen primary, skips the AI entirely —
            # carries "ids" (>=2 R-ids) + "primary" (∈ ids), no MS- suggestion.
            if action == "merge_force":
                result = _apply_merge_force(decision.get("ids"), decision.get("primary"))
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            # §39 回答需输入: carries "id" + "text" (not comment) — the owner's
            # typed answer for a blocked session, delivered via executor.answer.
            if action == "answer_input":
                result = _apply_answer_input(decision)
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            # §22 one-shot Claude Code session import — no requirement-level id.
            if action == "import_claude_sessions":
                result = _apply_claude_import(decision)
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            # weekly digest on demand (CONTRACT §24): no req id — the Settings
            # 「现在生成一份」button. Runs detached so the 420s claude call never
            # blocks the 10s daemon pass.
            if action == "weekly_digest_now":
                result = _spawn_weekly_digest()
                processed += 1
                _write_applied_ack(path.stem, result)
                _safe_unlink(path)
                continue

            req = load(req_id) if req_id else None

            if req is None:
                _log(f"inbox: decision for unknown req {req_id!r} ({action}) — dropped")
                # §5.4 ack: the card is gone → the phone must be told "该卡已不存在"
                # (result_status=unknown), never left guessing on a stuck 'delivered'.
                _write_applied_ack(path.stem, "unknown")
            else:
                if action == "set_title":
                    # §37: carries a `title` field the generic decision path
                    # doesn't know about — validated fail-closed in the helper.
                    result_status = _apply_set_title(req, decision.get("title"))
                else:
                    result_status = _apply_decision(
                        req, action, comment, expected_status, board_seq)
                # the comment (打回反馈/修改方向) is user-typed content —
                # attached only behind the capture_input gate, clipped.
                c = (comment or "").strip()
                analytics.log_event(
                    f"inbox_{action or 'unknown'}", req=req.id,
                    status=str(req.status), has_comment=bool(c) or None,
                    comment=(analytics.clip_content(c)
                             if c and analytics.content_gate() else None))
                # §5.4 ack: durable "did it land?" truth — running (applied a real
                # change) | noop (stale/idempotent guard) | unknown (bad action).
                _write_applied_ack(path.stem, result_status)
                processed += 1

            _safe_unlink(path)
        except Exception as e:  # noqa: BLE001 - one poison file must never wedge the inbox
            # ANY per-file crash (field-type poison, guard regression) must end
            # terminally for THIS file only — ack + delete, exactly like the
            # non-dict guard above — or the file re-crashes every mtime-ordered
            # pass and freezes the whole pipeline behind it.
            _log(f"inbox: decision file {path.name} crashed apply "
                 f"({type(e).__name__}: {e}) — discarding\n{traceback.format_exc()}")
            _write_applied_ack(path.stem, "bad_json")
            _safe_unlink(path)
    return processed


# --------------------------------------------------------------------------- #
# §5.4 sync ack ledger — one line per terminal inbox disposition.
# --------------------------------------------------------------------------- #
# M2 sync-active cache — keyed on state/sync.json's stat, so an opt-in/opt-out
# flip (syncd rewrites the file) is picked up without a daemon restart, while a
# non-sync install pays only one cheap os.stat() per call (never a JSON parse).
_SYNC_ACTIVE_CACHE: Optional[tuple] = None  # (stat_key, is_active)


def _sync_active() -> bool:
    """M2: True only when cloud sync is opted in (``state/sync.json`` exists with
    ``mode == "cloud"``). Gates ``_write_applied_ack`` so a purely local Mac/web
    user never creates ``state/sync/`` nor grows ``applied.jsonl``; a synced user
    still gets every ack (the ack→delivered/applied flow syncd relies on)."""
    global _SYNC_ACTIVE_CACHE
    path = config.STATE_DIR / "sync.json"
    try:
        st = path.stat()
        stat_key = (st.st_mtime_ns, st.st_size)
    except OSError:
        stat_key = None
    if _SYNC_ACTIVE_CACHE is not None and _SYNC_ACTIVE_CACHE[0] == stat_key:
        return _SYNC_ACTIVE_CACHE[1]
    active = False
    if stat_key is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            active = (isinstance(data, dict)
                      and str(data.get("mode") or "").lower() == "cloud")
        except (OSError, ValueError):
            active = False
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


def _precondition_ok(req: Requirement, expected_status: Optional[str],
                     action: Optional[str] = None) -> bool:
    """§5.4 stale-guard: True unless the phone pinned an ``expected_status`` that
    no longer matches the card's current status (the card moved since the phone
    saw it — apply would rip a running/moved card, so caller no-ops).

    Projection alias (integration audit 2026-07-15): the 待验收 lane the phone
    renders is NOT a pure status view — the dashboard also projects an
    on-disk-EXECUTING card there once its roster agent is done (and with
    ``auto_resume: false`` nothing ever promotes it to on-disk review, so that
    shape can persist indefinitely). The phone pins expected_status="review" on
    every accept/rework from that lane, so for those two verbs "review" must be
    satisfied by the SAME relaxed surface the no-expected local path accepts
    (review OR executing — the accept branch's exact status whitelist; NOT
    gated on execution.done, which is never stamped in the auto_resume:false
    shape). Every other mismatch (trashed/card_sent/delivered/…) stays a stale
    no-op. The other pinned verbs have no such alias: the phone renders 修改
    only on non-processing 提案 cards (on-disk card_sent exactly — raising
    cards hide the action bar) and 研究并提议 only in the debt lane (detected
    exactly), so their pins always match at render time.
    """
    if expected_status is None:
        return True
    if str(req.status) == str(expected_status):
        return True
    if (action in ("accept", "rework")
            and str(expected_status) == State.REVIEW.value
            and str(req.status) == State.EXECUTING.value):
        return True
    return False


def _spawn_weekly_digest() -> str:
    """Launch ``python -m act.weekly_digest --now`` detached (CONTRACT §24).

    Same detachment pattern as the merge-review analysis subprocess: never
    waited on, stdout/err appended to ``state/weekly_digest.log``. A failed
    launch only logs — the button press must never take the daemon down.
    Returns the §5.4 result_status ("running" started | "noop" launch failed).
    """
    config.ensure_state_dirs()
    log_path = config.STATE_DIR / "weekly_digest.log"
    try:
        with open(log_path, "ab") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "act.weekly_digest", "--now"],
                cwd=str(config.HOME),
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=fh,
                start_new_session=True,  # detached: outlives the pass
            )
        _log("inbox: weekly_digest_now — generation subprocess started")
        analytics.log_event("weekly_digest_requested")
        return "running"
    except Exception as e:  # noqa: BLE001 — never let the button kill the pass
        _log(f"inbox: weekly_digest_now launch FAILED: {e}")
        return "noop"


def _apply_capture(text: Optional[str], mode: Optional[str] = None) -> str:
    """Quick capture from the app popover (CONTRACT §10/§15; §34 mode="run").

    ``{"action":"capture","text":"...","ts":"..."}`` -> registry.merge_or_new
    (title=text, channel=quick_capture, 原话进 sources) -> status=raising, so the
    existing process_raising() expands it (one per pass) into a card_sent
    proposal. Fast: no LLM call here, the poll loop is never blocked.

    v0.34.0 ``mode="run"`` (the 运行中 lane's second input, CONTRACT §34): the
    SAME minimal card, but instead of the raising→proposal loop it is promoted
    straight to APPROVED so dispatch_approved launches it on the next pass —
    no plan/cost preview; the deliverable still lands in 待验收. Any other
    ``mode`` value (absent, junk, wrong type) fail-safes to today's proposal
    path — junk must never silently start an agent.

    Direct-run dispositions by what the text matched (CONTRACT §34; governing
    rule: ack "running" ONLY when a run is genuinely queued/in motion, and a
    promoted card NEVER inherits repo routing — no preview happened):
      - no match             -> fresh card, approved, chat + default workbench;
      - open pre-approval    -> THAT card promoted (never a twin), delivery
        (detected/card_sent/   FORCED to chat + workbench regardless of what
        raising)               routing it carried;
      - approved/executing   -> fold sources only — the ask is genuinely in
                               motion, ack "running", routing untouched;
      - review (待验收)      -> fold sources only, NOTHING started -> ack
                               "noop" (a silent fake success is the audit's
                               red line);
      - delivered/merged     -> §3.5 re-raise FORCED (the run-box gesture IS
                               the actionable signal merge_or_new's increment
                               gate can't see) -> the new round is promoted
                               like any pre-approval card.
    Returns the §5.4 result_status — the phone's ledger must never show
    已生效 for a capture that filed nothing.
    """
    # non-str text is a poison payload (§33 boundary doctrine): coercing it
    # with str() would file a garbage card — ack noop honestly instead.
    if text is not None and not isinstance(text, str):
        _log(f"inbox: capture with non-string text ({type(text).__name__}) — ignored")
        return "noop"
    t = " ".join(str(text or "").split()).strip()
    if not t:
        _log("inbox: capture with empty text — ignored")
        return "noop"
    run = mode == "run"
    req = Requirement(
        id=registry.next_id(),
        title=t[:80],
        type="other",
        tier="T1",
        status=State.DETECTED.value,
        hardness="soft",
        sources=[{
            "who": "zelin",
            "channel": "quick_capture",
            "date": _dt.date.today().isoformat(),
            "quote": t,
        }],
        notes="[direct-run] 用户直接开跑" if run else "from app quick capture",
    )
    if run:
        # direct-run skips LLM routing entirely — chat delivery at the default
        # workbench is the only no-preview-safe default (§34): no branch/PR
        # lands in a repo the user never confirmed; the FINAL DRAFT (or a
        # deliverables/ file artifact, §33) still reaches 待验收 for acceptance.
        req.delivery_mode = "chat"
    saved = registry.merge_or_new(req)
    if run:
        if registry.is_resolved(saved):
            # the text matched a CLOSED (delivered/merged) card. merge_or_new's
            # deterministic increment gate saw a bare capture (no deadline/
            # cost/hardness) and only folded — but typing into the RUN box IS
            # the actionable signal, so force the §3.5 re-raise for a new
            # round (actionable=True is the explicit-override seam the LLM
            # paths use). An open follow-up of the cluster dedupes the hit
            # (the fold target is then promoted below instead).
            _kind, reraised = registry.reraise_or_followup(
                saved, req, same_task=True, actionable=True,
                sources=req.sources, note="direct-run 重开一轮（运行中输入框）")
            if reraised is None:
                # canonical dead-end (rejected/trashed/archived primary):
                # re-card from scratch — never bury a direct-run in a dead card.
                saved = registry.upsert(req)
            else:
                saved = reraised
        if str(saved.status) in (State.DETECTED.value, State.CARD_SENT.value,
                                 State.RAISING.value):
            saved.set_status(State.APPROVED)
            # same bookkeeping as the approve action — dispatch reports wait_s
            # (approve → launch latency) off this stamp.
            ex = dict(saved.execution or {})
            ex["approved_at"] = _iso_now()
            # a re-raised card still carries the FINISHED round's session_id,
            # and dispatch_approved skips any card with one ("already
            # dispatched") — archive it or the new round never launches.
            sid = ex.get("session_id")
            if sid:
                ex["reraised_session_id"] = sid
                ex.pop("session_id", None)
                ex.pop("done", None)
            saved.execution = ex
            # NEVER inherit repo routing on a direct-run promotion: the match
            # may carry an LLM-routed repo (or raising's implicit repo
            # default), and dispatching that runs the branch/draft-PR prompt
            # with zero preview. Force the §34 no-preview-safe delivery.
            if (getattr(saved, "delivery_mode", None) or "repo") != "chat" \
                    or saved.target_repo:
                saved.delivery_mode = "chat"
                saved.target_repo = None
                tag = "[direct-run] 交付改为 chat（跳过预览，不动 repo）"
                saved.notes = (saved.notes + "\n" + tag).strip() if saved.notes else tag
            save(saved)
            _log(f"inbox: capture[run] -> {saved.id} approved (queued for dispatch)")
            result = "running"
        elif str(saved.status) in (State.APPROVED.value, State.EXECUTING.value):
            # fold into a card already queued/running: no twin agent, but the
            # ask genuinely is in motion — "running" is the honest ack.
            _log(f"inbox: capture[run] merged into {saved.id} "
                 f"(status={saved.status}) — already queued/running, not re-queued")
            result = "running"
        else:
            # fold into 待验收 (review) or any other non-running shape: only
            # bookkeeping changed, NOTHING started — acking "running" here is
            # the silent-fake-success the audit forbids.
            _log(f"inbox: capture[run] merged into {saved.id} "
                 f"(status={saved.status}) — no run filed, acking noop")
            result = "noop"
        analytics.log_event(
            "capture_direct_run", req=saved.id, status=str(saved.status),
            chars=len(t),
            text=(analytics.clip_content(t)
                  if analytics.content_gate() else None))
        return result
    if saved.status == State.DETECTED.value:
        saved.set_status(State.RAISING)
        save(saved)
        _log(f"inbox: capture -> {saved.id} raising (queued for AI expansion)")
    else:
        _log(f"inbox: capture merged into {saved.id} (status={saved.status})")
    # the typed capture text is content — capture_input-gated, clipped;
    # chars stays metadata (usage signal without the words).
    analytics.log_event(
        "inbox_capture", req=saved.id, status=str(saved.status), chars=len(t),
        text=(analytics.clip_content(t)
              if analytics.content_gate() else None))
    return "running"


def _apply_split_note(req_id, note_ts) -> str:
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
    if not isinstance(req_id, str) or not isinstance(note_ts, str):
        _log(f"inbox: split_note with non-string fields "
             f"(id={type(req_id).__name__}, note_ts={type(note_ts).__name__}) — ignored")
        return "noop"
    rid, ts = req_id.strip(), note_ts.strip()
    req = load(rid) if rid else None
    if req is None:
        _log(f"inbox: split_note for unknown req {req_id!r} — dropped")
        return "unknown"
    if str(req.status) in _MERGE_DEAD_STATES or req.is_merged:
        # terminal-state doctrine (§32.2, same set the merge machinery
        # refuses): a stale detail panel must not mint a live card (+1 expand
        # LLM run) out of a card that meanwhile trashed/merged/rejected/
        # archived. Notes stay untouched; honest noop ack.
        _log(f"inbox: {req.id} split_note on terminal card "
             f"({req.status}) — no-op")
        return "noop"
    entry = next((e for e in registry.parse_fold_notes(req.notes)
                  if e["ts"] == ts and not e["split_into"] and e["text"]), None)
    if entry is None:
        _log(f"inbox: {req.id} split_note ts {note_ts!r} not found / already "
             f"split — no-op")
        return "noop"
    text = entry["text"]
    new = Requirement(
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
    # new card FIRST, origin tag second (archive()'s crash-mid-move doctrine:
    # a crash between the two leaves the split recoverable, never lost).
    save(new)
    if registry.mark_note_split(req, ts, new.id):
        save(req)
    _log(f"inbox: {req.id} split_note [@{ts}] -> {new.id} (raising)")
    analytics.log_event("split_note", req=req.id, new=new.id)
    return "running"


def _apply_set_title(req: Requirement, title) -> str:
    """§37 set_title — the user renames a card's DISPLAY title (the frozen
    internal ``title`` never changes; it anchors dedupe/re-raise identity).

    Fail-closed validation (v0.33.1 boundary doctrine): non-string / empty /
    >64-char titles are logged no-ops — a poison payload must never become a
    board title. Sets ``user_titled`` so LLM/harvest titles never overwrite
    the user's choice; the previous display name lands in ``former_titles``
    (still searchable). Archived cards stay sealed (unarchive first), same as
    the central _apply_decision gate. Returns the §5.4 result_status.
    """
    if str(req.status) == State.ARCHIVED.value:
        _log(f"inbox: {req.id} set_title on archived card — no-op (unarchive first)")
        return "noop"
    if not isinstance(title, str):
        _log(f"inbox: {req.id} set_title with non-string title "
             f"({type(title).__name__}) — ignored")
        return "noop"
    t = " ".join(title.split()).strip()
    if not t or len(t) > 64:
        _log(f"inbox: {req.id} set_title invalid title "
             f"(empty or >64 chars, got {len(t)}) — ignored")
        return "noop"
    if not registry.set_display_title(req, t, by_user=True):
        _log(f"inbox: {req.id} set_title no-op (title unchanged)")
        return "noop"
    save(req)
    _log(f"inbox: {req.id} set_title -> {t!r} (user pinned)")
    return "running"


def _apply_harvest_title(req: Requirement, harvested: dict) -> None:
    """§37: apply a harvested ``CARD TITLE:`` line at the same promotion points
    where delivered_summary lands (round boundaries only). Best-effort; a
    user-pinned title wins inside set_display_title. Caller saves ``req``."""
    try:
        t = (harvested or {}).get("card_title")
        if t and registry.set_display_title(req, t):
            _log(f"inbox/reconcile: {req.id} display title refreshed from "
                 f"CARD TITLE line: {str(t)[:64]!r}")
    except Exception as e:  # noqa: BLE001 - titles must never block delivery
        _log(f"harvest title apply failed for {getattr(req, 'id', '?')}: {e}")


def _update_search_index(card_id, session_id) -> None:
    """§37 Mac-local session-content search layer: refresh one card's entry at
    the existing settle/harvest touchpoints. Best-effort, never raises."""
    if not session_id:
        return
    try:
        from act.lib import search_index
        search_index.update_card(str(card_id), str(session_id))
    except Exception as e:  # noqa: BLE001 - indexing must never break the pass
        _log(f"search index update failed for {card_id}: {e}")


def _apply_feedback(decision: dict) -> str:
    """建议上报 (CONTRACT §29) — explicit user report to the maintainer.

    ``{"action":"feedback","ids":["R-001","MS-ab12cd34"],"text":"…"}`` —
    validation here: non-empty text is REQUIRED (empty -> logged drop);
    ``ids`` may be missing/empty/garbage (bad ids degrade to "unknown"
    snapshots inside the record — the text must never be lost over them).
    Recording + best-effort upload live in act/lib/feedback.py; only event
    METADATA reaches the local analytics log — the report text travels solely
    inside the feedback record itself. Returns the §5.4 result_status
    ("running" recorded | "noop" dropped).
    """
    if feedback is None:
        _log("inbox: feedback requested but module unavailable — dropped")
        return "noop"
    text = str(decision.get("text") or "").strip()
    if not text:
        _log("inbox: feedback with empty text — dropped")
        return "noop"
    ids = feedback.clean_ids(decision.get("ids"))
    rec = feedback.record_feedback(ids, text)
    if rec is None:
        _log("inbox: feedback record FAILED — dropped")
        return "noop"
    _log(f"inbox: feedback {rec['id']} recorded "
         f"(ids={ids or []}, uploaded={rec.get('uploaded')})")
    analytics.log_event("inbox_feedback", n=len(ids),
                        uploaded=rec.get("uploaded"))
    return "running"


# §39.2 answer cooldown: a second answer landing this soon after a successful
# one is a race (second device / double-submit), not a reply to a NEW question
# — the resumed session may not even be on the roster yet, so the roster probe
# alone can't see it. 120s covers the resume startup gap plus a phone round
# trip; a genuinely new question normally arrives well past it (and the
# archived-text notice tells the answerer to resend after the window).
_ANSWER_COOLDOWN_S = 120


def _answer_not_delivered(req: Requirement, text: str, kind: str) -> str:
    """§39.2: a VALIDATED answer that cannot be delivered because the moment
    has passed — the card left needs_input (promotion race), the session is
    actively working (someone else answered it first), an answer landed
    moments ago (cooldown), or the text exceeds the wire bound. Both UIs
    already accepted the send as success, so a bare logged no-op silently
    swallows the owner's typed text: archive it in notes + notify instead.
    Returns the §5.4 "noop" ack (nothing was started)."""
    reasons = {
        "working": "会话正在工作中，可能已被回答",
        "review": "任务已完成进了待验收",
        "recent": "刚有一条回答送达，可能还在生效中",
        "oversize": "回答超过 4000 字上限",
    }
    reason = reasons.get(kind, f"卡片已不在需输入状态（现为 {req.status}）")
    stamp = _dt.date.today().isoformat()
    tag = f"[{stamp} 回答未投递] {reason}；原文：{text[:200]}"
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
    save(req)
    notify.notify(*notify.msg_answer_not_delivered(req.title or req.id, kind),
                  req=req.id)
    _log(f"inbox: {req.id} answer_input NOT delivered ({kind}: {reason}) — "
         f"noop, text archived in notes")
    analytics.log_event("inbox_answer_input", req=req.id, ok=False,
                        reason=kind, chars=len(text))
    return "noop"


def _apply_answer_input(decision: dict) -> str:
    """回答需输入 (CONTRACT §39) — deliver the owner's typed answer into a
    blocked session via executor.answer (stop-idle-then-resume, the rework
    plumbing with an ``OWNER ANSWER:`` prefix instead of the 打回 preamble).

    ``{"action":"answer_input","id":"R-001","text":"…"}`` — boundary
    validation here (§33 house pattern, fail-closed): ``text`` must be a str
    whose trimmed length is 1..4000 (non-str / empty / oversize -> logged
    drop — junk must never relaunch a session); unknown card -> "unknown";
    a phone-pinned ``expected_status`` mismatch OR a card no longer EXECUTING
    (需输入 rows only ever project executing cards) -> stale, NOT silent:
    the validated text is archived via _answer_not_delivered.

    §39.2 pre-delivery roster probe: on-disk EXECUTING covers roster working
    AND blocked, and executor.answer STOPS the session before resuming — so
    without a fresh roster read, a stale second device's 「回答…」(or a webui
    answer_input aimed at any executing card) would kill a MID-RUN session at
    an arbitrary tool call and resume it with a duplicate answer. A session
    with a live pid whose state is not blocked is therefore never touched:
    no stop, no resume — archive + notify (the second answerer must learn
    the first answer likely won). Only a genuinely blocked session — or a
    dead/absent one (the existing resume-a-dead-session path) — receives
    stop+resume.

    Honest acks (§5.4): "running" ONLY when the answer genuinely reached the
    session (notes tag [回答已送达]); every other outcome is "noop" AND
    visible — [回答未投递] (stale/working, text archived) or [回答送达失败]
    (launch failure) + notification + the card's last_error (dashboard §39)
    — never silent.
    """
    if executor is None:
        _log("inbox: answer_input requested but executor unavailable — dropped")
        return "noop"
    text = decision.get("text")
    if not isinstance(text, str):
        _log(f"inbox: answer_input with non-string text "
             f"({type(text).__name__}) — dropped")
        return "noop"
    t = text.strip()
    if not t:
        _log("inbox: answer_input with empty text — dropped")
        return "noop"
    req_id = decision.get("id")
    req = load(req_id) if req_id else None
    if req is None:
        _log(f"inbox: answer_input for unknown req {req_id!r} — dropped")
        return "unknown"
    # From here on there is TEXT and a card — any non-delivery must archive
    # the text + notify (§39.2), never a bare logged no-op. Oversize included:
    # the clients clip to 4000 unicode scalars, so landing here means a buggy/
    # raw client — the head of the text is still worth saving.
    if len(t) > 4000:
        return _answer_not_delivered(req, t, "oversize")
    expected_status = decision.get("expected_status")
    if (not _precondition_ok(req, expected_status)
            or str(req.status) != State.EXECUTING.value):
        # stale pin and status-moved collapse to one surface: the phone only
        # ever pins "executing", so a pin mismatch implies the card moved —
        # most commonly _promote_if_delivered's executing→review promotion
        # racing the inbox pass.
        _log(f"inbox: {req.id} answer_input stale "
             f"(expected {expected_status}, is {req.status})")
        kind = ("review" if str(req.status) in (State.REVIEW.value,
                                                State.DELIVERED.value)
                else "moved")
        return _answer_not_delivered(req, t, kind)
    # §39.2 pre-delivery roster probe (see docstring): never stop a session
    # that is actively WORKING with a live process.
    ex_now = req.execution if isinstance(req.execution, dict) else {}
    sid = ex_now.get("session_id")
    if sid:
        try:
            agent = _index_agents(_run_claude_agents()).get(str(sid))
        except Exception:  # noqa: BLE001 - roster probe is best-effort
            agent = None
        state = (agent or {}).get("state", "") if agent else ""
        if agent is not None and agent.get("pid") and state not in _BLOCKED_STATES:
            return _answer_not_delivered(req, t, "working")
    # §39.2 cooldown (belt+braces with the probe): a successful answer's
    # resumed session may not be on the roster yet — during that gap the probe
    # sees "absent" and a racing second answer would stop-kill the fresh
    # session. Within the window, reject UNLESS the last attempt demonstrably
    # failed (execution.last_error survives a failed answer and is cleared by
    # a clean one) — a retry after failure must never be blocked.
    last_answer = _parse_iso(ex_now.get("last_answer_at"))
    if (last_answer is not None and not ex_now.get("last_error")
            and (_dt.datetime.now(_dt.timezone.utc) - last_answer).total_seconds()
                < _ANSWER_COOLDOWN_S):
        return _answer_not_delivered(req, t, "recent")
    ok = False
    try:
        ok = bool(executor.answer(req, t))
    except Exception as e:  # noqa: BLE001 - answer must never kill the pass
        _log(f"inbox: {req.id} answer_input crashed: {e}")
    stamp = _dt.date.today().isoformat()
    if ok:
        tag = f"[{stamp} 回答已送达] {t[:200]}"
        req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
        save(req)
        _log(f"inbox: {req.id} answer_input delivered into session")
        analytics.log_event(
            "inbox_answer_input", req=req.id, ok=True, chars=len(t),
            text=(analytics.clip_content(t)
                  if analytics.content_gate() else None))
        return "running"
    # executor.answer recorded execution.last_error — surface the failure
    # everywhere the owner can see it (§39: never a silent drop).
    reason = str((req.execution or {}).get("last_error") or "启动失败")[:160]
    tag = f"[{stamp} 回答送达失败] {reason}"
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
    save(req)
    notify.notify(*notify.msg_answer_failed(req.title or req.id, reason),
                  req=req.id)
    _log(f"inbox: {req.id} answer_input NOT delivered ({reason}) — acking noop")
    analytics.log_event("inbox_answer_input", req=req.id, ok=False,
                        reason="launch_failed", chars=len(t))
    return "noop"


def _apply_claude_import(decision: dict) -> str:
    """One-shot Claude Code session import (CONTRACT §22).

    ``{"action":"import_claude_sessions","session_ids":[…],"window_days":7}``
    — with explicit ids (the Settings checkbox flow) each session becomes a
    proposal card; without ids, every waiting-on-you session inside the window
    is imported. Idempotent: already-imported ids are skipped via the
    state/claude_sessions_import.json marker, and card creation goes through
    merge_or_new. Cheap (head/tail file reads, no LLM) — safe inline in the
    poll loop. Returns the §5.4 result_status ("running" ran | "noop" failed).
    """
    if radar_claude_sessions is None:
        _log("inbox: import_claude_sessions requested but module unavailable — dropped")
        return "noop"
    raw_ids = decision.get("session_ids")
    ids = [str(s) for s in raw_ids if s] if isinstance(raw_ids, list) else []
    try:
        window = int(decision.get("window_days") or 7)
    except (TypeError, ValueError):
        window = 7
    try:
        if ids:
            n = radar_claude_sessions.import_by_ids(ids)
        else:
            n = radar_claude_sessions.run_once(window_days=window)
        _log(f"inbox: import_claude_sessions -> {n} card(s) "
             f"({len(ids) or 'auto'} requested)")
        return "running"
    except Exception as e:  # noqa: BLE001 — an import failure must not kill the pass
        _log(f"inbox: import_claude_sessions failed: {e}")
        return "noop"


# --------------------------------------------------------------------------- #
# merge-review (§21) — actd side: validate + job file + detached analysis;
# apply is DETERMINISTIC (the AI's action_plan is display-only).
# --------------------------------------------------------------------------- #
# Terminal/sealed states a merge may never write into or absorb from: folding
# live cards into a trashed/merged/archived primary buries them in terminal
# MERGED (no un-merge, no lane renders them) and their carried deliverables
# get hard-deleted with the primary at trash purge (audit 2026-07-15).
_MERGE_DEAD_STATES = (State.TRASHED.value, State.MERGED.value,
                      State.REJECTED.value, State.ARCHIVED.value)


def _apply_merge_review(ids) -> str:
    """契约 五 actd 侧：校验 ids（≥2、去重、都存在）→ 建 analyzing 作业文件 →
    subprocess.Popen 分离启动 ``python -m act.merge_review <id>``（不等待，
    stdout/err 落 state/logs/<suggestion_id>.log）。不合法 -> log 丢弃。
    Returns the §5.4 result_status ("running" job created | "noop" dropped)."""
    if merge_review is None:
        _log("inbox: merge_review requested but module unavailable — dropped")
        return "noop"
    raw = ids if isinstance(ids, list) else []
    seen: set[str] = set()
    uniq: list[str] = []
    for i in raw:
        s = str(i or "").strip()
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    if len(uniq) < 2:
        _log(f"inbox: merge_review needs >=2 distinct ids, got {raw!r} — dropped")
        return "noop"
    missing = [i for i in uniq if load(i) is None]
    if missing:
        _log(f"inbox: merge_review unknown ids {missing} — dropped")
        return "noop"

    job = merge_review.create_job(uniq)
    sid = str(job["id"])
    log_path = config.LOG_DIR / f"{sid}.log"
    try:
        with open(log_path, "ab") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "act.merge_review", sid],
                cwd=str(config.HOME),
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=fh,
                start_new_session=True,  # detached: outlives the pass, never waited on
            )
    except Exception as e:  # noqa: BLE001 - a failed launch must not hang 'analyzing'
        merge_review.mark_failed(sid, f"analysis launch failed: {e}")
        _log(f"inbox: merge_review {sid} launch FAILED: {e}")
        # the job file exists and visibly shows failed — a real, durable change
        return "running"
    _log(f"inbox: merge_review {sid} ids={uniq} — analysis subprocess started")
    analytics.log_event("merge_review_requested", n=len(uniq), suggestion=sid)
    return "running"


def _apply_merge_force(ids, primary) -> str:
    """契约 §21 强制合并（v0.31）：用户钦定主卡、跳过 AI 直接落地 ``merge``。
    校验 ids（≥2、去重、都存在）+ primary ∈ ids → 复用 :func:`_merge_into_primary`
    ——与 AI ``merge`` verdict 逐字同一条确定性执行路径（主卡吸收 sources 去重 /
    repeated_mentions 累加 / notes 留痕 / 交付物搬运，副卡 best-effort 停 session +
    置 ``merged``；主卡在待验收则 rework 注入）。不合法 = log 丢弃（同 merge_review
    公共规则）；执行失败只 log + 打点 outcome=fail，绝不抛穿轮询（用户可重试）。
    Returns the §5.4 result_status ("running" applied | "noop" dropped/failed)."""
    raw = ids if isinstance(ids, list) else []
    seen: set[str] = set()
    uniq: list[str] = []
    for i in raw:
        s = str(i or "").strip()
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    prim = str(primary or "").strip()
    if len(uniq) < 2:
        _log(f"inbox: merge_force needs >=2 distinct ids, got {raw!r} — dropped")
        return "noop"
    if prim not in uniq:
        _log(f"inbox: merge_force primary {primary!r} not in ids {uniq} — dropped")
        return "noop"
    missing = [i for i in uniq if load(i) is None]
    if missing:
        _log(f"inbox: merge_force unknown ids {missing} — dropped")
        return "noop"
    prim_req = load(prim)
    if prim_req is not None and str(prim_req.status) in _MERGE_DEAD_STATES:
        # a stale board can pick a primary the user meanwhile trashed/merged/
        # archived — folding live cards into it loses them (audit 2026-07-15)
        _log(f"inbox: merge_force primary {prim} is {prim_req.status} — dropped")
        return "noop"
    secondaries = [i for i in uniq if i != prim]
    try:
        _merge_into_primary(prim, secondaries)
    except Exception as e:  # noqa: BLE001 - never hang the poll; user can retry/redo
        _log(f"inbox: merge_force primary={prim} secondaries={secondaries} "
             f"FAILED: {e}\n{traceback.format_exc()}")
        analytics.log_event("merge_force", n=len(uniq), outcome="fail")
        return "noop"
    _log(f"inbox: merge_force primary={prim} secondaries={secondaries} applied")
    analytics.log_event("merge_force", n=len(uniq), outcome="ok")
    return "running"


def _apply_merge_decision(action: str, suggestion_id) -> str:
    """契约 一/四：merge_apply（status=done 才可执行，按 verdict 确定性落地，然后
    作业标记 dismissed 留到 TTL 清理）；merge_dismiss（直接标记 dismissed）。
    状态不匹配 / 未知建议 = 幂等 no-op + log（同 v0.10.2 逆向动作公共规则）。
    Returns the §5.4 result_status ("running" | "noop" | "unknown")."""
    if merge_review is None:
        _log(f"inbox: {action} requested but merge_review unavailable — dropped")
        return "noop"
    sid = str(suggestion_id or "").strip()
    job = merge_review.load_job(sid) if sid else None
    if job is None:
        _log(f"inbox: {action} for unknown suggestion {suggestion_id!r} — dropped")
        return "unknown"
    status = str(job.get("status") or "")

    if action == "merge_dismiss":
        if status == "dismissed":
            _log(f"inbox: merge_dismiss {sid} already dismissed — no-op")
            return "noop"
        merge_review.dismiss_job(job)
        _log(f"inbox: merge_dismiss {sid} (was {status})")
        return "running"

    # merge_apply — only a finished analysis is actionable (连点/迟到 -> no-op)
    if status != "done":
        _log(f"inbox: merge_apply {sid} ignored (status={status}) — no-op")
        return "noop"
    verdict = str(job.get("verdict") or "")
    # a done suggestion stays actionable for its 24h TTL, but the board may
    # have moved meanwhile: the user can trash/merge/archive the primary and
    # THEN tap 采纳 from a stale surface. Applying would fold live secondaries
    # into a dead primary — terminal MERGED, no undo, deliverables purged with
    # the primary later. Fail the job visibly instead (audit 2026-07-15).
    if verdict in ("merge", "link_improvement"):
        prim = load(str(job.get("primary") or ""))
        if prim is None or str(prim.status) in _MERGE_DEAD_STATES:
            reason = "主卡已删除/已合并/已封存，该合并建议已失效"
            merge_review.mark_failed(sid, reason)
            _log(f"inbox: merge_apply {sid} ({verdict}) primary "
                 f"{job.get('primary')!r} is gone/dead — job failed, no-op")
            analytics.log_event("merge_apply", suggestion=sid, verdict=verdict,
                                outcome="fail")
            return "noop"
    # merge_apply outcome at the authoritative apply site (docs/TELEMETRY.md):
    # the app's card_action only records intent — a failed deterministic apply
    # was invisible to telemetry before this. No-op paths above stay unlogged
    # (double-clicks are not usage). Metadata only: ids + outcome, no content.
    try:
        _apply_merge_verdict(job)
    except Exception as e:  # noqa: BLE001 - job stays 'done' so Zelin can retry/dismiss
        _log(f"inbox: merge_apply {sid} ({verdict}) FAILED: {e}\n"
             f"{traceback.format_exc()}")
        analytics.log_event("merge_apply", suggestion=sid, verdict=verdict,
                            outcome="fail")
        return "noop"
    merge_review.dismiss_job(job, applied=True)  # 即刻从 dashboard 消失，文件留到 TTL
    _log(f"inbox: merge_apply {sid} ({verdict}) applied")
    analytics.log_event("merge_apply", suggestion=sid, verdict=verdict,
                        outcome="ok")
    return "running"


def _apply_merge_verdict(job: dict) -> None:
    """契约 四 确定性 apply 语义。keep_separate = no-op（调用方统一 dismiss）。"""
    verdict = str(job.get("verdict") or "")
    ids = [str(i) for i in job.get("ids") or []]
    primary_id = str(job.get("primary") or "")
    if verdict == "keep_separate":
        return
    secondaries = [i for i in ids if i != primary_id]
    if (verdict not in ("merge", "link_improvement", "close_secondary")
            or primary_id not in ids or not secondaries):
        raise ValueError(
            f"unusable job: verdict={verdict!r} primary={primary_id!r} ids={ids}")

    if verdict == "link_improvement":
        # 副卡挂为主卡的改进卡，其余（状态/execution）一律不动。
        for rid in secondaries:
            sec = load(rid)
            if sec is None:
                _log(f"merge: link_improvement {rid} not found — skipped")
                continue
            sec.improvement_of = primary_id
            save(sec)
            _log(f"merge: {rid} improvement_of={primary_id}")
        return

    if verdict == "close_secondary":
        # 副卡关闭进回收站（可恢复），理由固定写入 trash_reason。
        for rid in secondaries:
            sec = load(rid)
            if sec is None:
                _log(f"merge: close_secondary {rid} not found — skipped")
                continue
            registry.trash(sec, "merged-review: 不再需要")
            _log(f"merge: {rid} closed -> trash (merged-review)")
        return

    _merge_into_primary(primary_id, secondaries)


def _merge_into_primary(primary_id: str, secondaries: list[str]) -> None:
    """契约 四 merge：主卡 sources 去重合并、repeated_mentions 累加、notes 留痕；
    副卡活 session best-effort 停止、状态置 merged + merged_into；主卡 status==
    review 时用 executor.rework 把副卡交付物/worktree 信息注入其 session（主卡
    回 executing），其他状态只落 notes。"""
    primary = load(primary_id)
    if primary is None:
        raise ValueError(f"primary {primary_id} not found in registry")
    if str(primary.status) in _MERGE_DEAD_STATES:
        # backstop behind the caller-level checks: never absorb live cards
        # into a trashed/merged/archived primary (audit 2026-07-15)
        raise ValueError(
            f"primary {primary_id} is {primary.status} — refusing to merge into a dead card")

    feedback_lines: list[str] = []
    for rid in secondaries:
        sec = load(rid)
        if sec is None:
            _log(f"merge: secondary {rid} not found — skipped")
            continue
        if str(sec.status) in _MERGE_DEAD_STATES:
            # already merged (retry idempotency) or trashed/archived meanwhile —
            # absorbing a sealed card would strip its restorability
            _log(f"merge: {rid} is {sec.status} — skipped (not a live card)")
            continue
        sec_ex = dict(sec.execution or {})
        # 主卡吸收
        merged_sources, _ = registry._dedupe_sources(
            primary.sources or [], sec.sources or [])
        primary.sources = merged_sources
        primary.repeated_mentions = (int(primary.repeated_mentions or 1)
                                     + int(sec.repeated_mentions or 1))
        summary = " ".join(
            str(sec_ex.get("delivered_summary") or sec.title or "").split()).strip()
        # §37 review fix: carry the secondary's DISPLAY names into the
        # primary's notes — notes project as searchable notes_text, so a
        # user-named secondary stays findable by its old name after this
        # IRREVERSIBLE merge (merged is terminal; the frozen sec.title alone
        # broke the "旧名仍可搜索" promise exactly here).
        sec_names = [str(n).strip() for n in
                     ([getattr(sec, "display_title", None)]
                      + list(getattr(sec, "former_titles", None) or []))
                     if n and str(n).strip()]
        names_part = f"（曾用名：{' · '.join(sec_names)}）" if sec_names else ""
        tag = f"[merged] {sec.id} 并入：{summary[:200] or '(无摘要)'}{names_part}"
        primary.notes = (primary.notes + "\n" + tag).strip() if primary.notes else tag
        # Preserve a delivered secondary's FULL deliverable on the primary.
        # MERGED is terminal + UI-unreachable (no un-merge), so a finished
        # final_draft / delivered_summary on the secondary would otherwise be
        # lost from the UI — the notes breadcrumb above is only a ~200-char
        # summary. If the secondary carried finished work, carry the full,
        # UNTRUNCATED content onto the primary's
        # execution.merged_deliverables list (add-only — never touches the
        # primary's OWN delivered_summary/final_draft). At minimum this keeps
        # the deliverable verbatim in the primary's registry YAML.
        sec_final = str(sec_ex.get("final_draft") or "").strip()
        sec_delivered = str(sec_ex.get("delivered_summary") or "").strip()
        if sec_final or sec_delivered:
            prim_ex = dict(primary.execution or {})
            carried = list(prim_ex.get("merged_deliverables") or [])
            carried.append({
                "id": sec.id,
                "title": sec.title or "",
                # §37: display names ride along too (same review fix as the
                # notes tag above — the deliverable must stay attributable
                # to the name the user knew the card by).
                "display_title": getattr(sec, "display_title", None),
                "former_titles": list(getattr(sec, "former_titles", None) or []) or None,
                "delivered_summary": sec_ex.get("delivered_summary"),
                "final_draft": sec_ex.get("final_draft"),
                "merged_at": _iso_now(),
            })
            prim_ex["merged_deliverables"] = carried
            primary.execution = prim_ex
            _log(f"merge: {sec.id} deliverable carried onto {primary.id} "
                 f"(execution.merged_deliverables, n={len(carried)})")
        # 副卡活 session best-effort 停止（失败只记日志，绝不阻塞合并落账）
        sec_sid = sec_ex.get("session_id")
        if sec_sid and executor is not None:
            try:
                stopped = executor.stop_session(str(sec_sid))
                _log(f"merge: {sec.id} stop_session({sec_sid}) -> {stopped}")
            except Exception as e:  # noqa: BLE001 - best-effort
                _log(f"merge: {sec.id} stop_session({sec_sid}) failed (ignored): {e}")
        # Persist the primary's absorption BEFORE marking the secondary as
        # merged: retries skip already-merged secondaries, so a crash between
        # the two saves must never leave the absorbed sources/mentions/notes
        # only in memory.
        save(primary)
        # 副卡终态（registry State.MERGED，语义见 §21）
        sec.set_status(State.MERGED)
        sec.merged_into = primary.id
        save(sec)
        _log(f"merge: {sec.id} -> merged (into {primary.id})")
        # 主卡待验收时注入的反馈材料：副卡交付物/worktree 路径与摘要
        worktree = None
        if sec_sid and executor is not None:
            try:
                worktree = executor._transcript_cwd(str(sec_sid))
            except Exception:  # noqa: BLE001 - inference is best-effort
                worktree = None
        feedback_lines.append(
            f"{sec.id} 已并入，其交付物/worktree：{worktree or sec.target_repo or '(无)'}；"
            f"摘要：{summary[:300] or '(无)'}")

    if not feedback_lines:
        return
    if str(primary.status) == State.REVIEW.value and executor is not None:
        try:
            ok = executor.rework(primary, "\n".join(feedback_lines))
            _log(f"merge: {primary.id} rework injected (ok={ok})")
        except Exception as e:  # noqa: BLE001 - injection is best-effort
            _log(f"merge: {primary.id} rework failed (ignored): {e}")
    # 主卡其他状态：notes 已留痕，不动其 session（契约 四）。


def _stop_live_session(req: Requirement, why: str) -> None:
    """Best-effort stop of a card's live agent before a destructive action
    (reject/trash on an approved/executing/review card — nightly audit
    2026-07-14: the old path binned the card while its agent kept running,
    burning tokens into a worktree nobody would ever look at). Mirrors the
    abort_execution recipe: stop, archive the sid, never block the action."""
    if str(req.status) not in (State.APPROVED.value, State.EXECUTING.value,
                               State.REVIEW.value):
        return
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    if not sid:
        return
    stopped = False
    if executor is not None:
        try:
            stopped = bool(executor.stop_session(str(sid)))
            _log(f"inbox: {req.id} {why} — stop_session({sid}) -> {stopped}")
        except Exception as e:  # noqa: BLE001 - best-effort, never block
            _log(f"inbox: {req.id} {why} — stop_session({sid}) failed (ignored): {e}")
    ex["aborted_session_id"] = sid
    if stopped:
        # only a session we actually killed loses its id — when the stop
        # failed (or executor is unavailable) the agent may still be alive,
        # and a later trash→restore round-trip must be able to re-attach
        # (audit review 2026-07-14: unconditional pop made restore lossy).
        ex.pop("session_id", None)
    req.execution = ex


def _apply_decision(req: Requirement, action: Optional[str],
                    comment: Optional[str],
                    expected_status: Optional[str] = None,
                    board_seq=None) -> str:
    # Full inbox action set (CONTRACT §10) — this elif chain IS the action
    # whitelist/validation; anything else falls through to the logged no-op else:
    #   approve | reject(->trash) | comment | raise(debt->proposal)
    #   | trash(->recycle) | restore(recycle->prev) | pin(recycle->permanent)
    #   | accept(review->delivered) | rework(review->executing)
    #   | done_external(card_sent|review|approved|executing->delivered)
    #                                             (v0.10.2, 扩展 v0.12)
    #   | abort_execution(approved|executing->card_sent)      (v0.10.2)
    #   | stop_to_review(executing|approved->review, 收下成果待验收)
    #   | revert_review(delivered->review)                    (v0.10.2)
    #   | defer(card_sent->detected, back to the backlog)     (v0.18)
    #   | archive(delivered|detected->archived, relocate)     (v0.20.0)
    #   | unarchive(archived->prev_status, back to active)    (v0.20.0)
    # v0.10.2 公共规则：状态不匹配的逆向动作 = 幂等 no-op + log（防连点/迟到 inbox）。
    #
    # Returns a §5.4 result_status for the sync ack ledger:
    #   "running" = applied a real state change; "noop" = guarded/idempotent/
    #   stale no-op; "unknown" = unrecognised action. (Local Mac-app callers may
    #   ignore the return.) The board_seq precondition rides in the AAD + inbox
    #   file for provenance; expected_status is the enforced stale-guard (§5.4).
    # ---- central archived gate (nightly audit 2026-07-14) ----
    # An archived card's FILE lives in archive/ — any status write except
    # unarchive would strand a live-status card inside the archive dir (split
    # brain: dashboard shows it nowhere, purge rules stop applying). Every
    # action but unarchive is a guarded no-op.
    if str(req.status) == State.ARCHIVED.value and action != "unarchive":
        _log(f"inbox: {req.id} {action} on archived card — no-op (unarchive first)")
        return "noop"

    if action == "approve":
        # idempotent: a double-click (or re-approve while already running) must
        # not re-dispatch and spawn a duplicate agent. WHITELIST (nightly audit
        # 2026-07-14): the old blacklist let a late/replayed approve flip
        # trashed/merged/raising cards straight to approved — dispatching
        # deleted or mid-expansion work. Only a live proposal may be approved.
        if str(req.status) not in (State.DETECTED.value, State.CARD_SENT.value):
            _log(f"inbox: {req.id} approve ignored (status={req.status})")
            return "noop"
        req.set_status(State.APPROVED)
        # approval timestamp (add-only bookkeeping, like accepted_at) — lets
        # the dispatch event report wait_s (approve -> launch latency).
        ex = dict(req.execution or {})
        ex["approved_at"] = _iso_now()
        req.execution = ex
        save(req)
        # lifecycle milestone (docs/TELEMETRY.md): first genuine approval on
        # this install. The idempotent guard above means re-approvals of an
        # already-running card never reach here, so only real approvals count.
        analytics.log_first("milestone_first_approval", req=req.id)
        _log(f"inbox: {req.id} approved")
        return "running"
    elif action == "reject":
        _stop_live_session(req, "reject")  # nightly audit: never orphan a live agent
        registry.trash(req, "rejected")  # recoverable, not a bare rejected status
        _log(f"inbox: {req.id} rejected -> trash")
        return "running"
    elif action == "comment":
        # §5.4 stale-guard (SYNC only): when the phone pinned an expected_status
        # that no longer matches, a stale 修改 must not rip a moved card back to
        # card_sent. LOCAL callers (Mac app / web) send no expected_status, so
        # this passes and comment applies unconditionally exactly as on main —
        # the web renders 修改 on RAISING/processing cards too, and folding one
        # back to card_sent for re-approval is the intended local behavior.
        if not _precondition_ok(req, expected_status):
            _log(f"inbox: {req.id} comment stale "
                 f"(expected {expected_status}, is {req.status}) — no-op")
            return "noop"
        if str(req.status) in (State.TRASHED.value, State.MERGED.value,
                               State.REJECTED.value):
            # CONTRACT §32.2 (audit 2026-07-15): a late comment on a terminal
            # card must not fall through to the card_sent write below — that
            # resurrects a rejected/merged card as a live proposal with its
            # trash/merge bookkeeping still attached.
            _log(f"inbox: {req.id} comment ignored (status={req.status} is "
                 f"terminal) — no-op")
            return "noop"
        _fold_comment(req, comment)
        # nightly audit 2026-07-14: a comment landing on a card that is
        # already past approval must NOT rip it back to card_sent — that
        # orphans a live agent (execution.session_id survives, and the next
        # approve re-dispatches against a stale session). Past-approval
        # states keep their status; the note is folded for the record (review
        # has its own formal channel: rework).
        if str(req.status) == State.APPROVED.value:
            # pre-dispatch: the folded note rides into the dispatch prompt —
            # the direction change genuinely lands, so "running" is honest.
            save(req)
            _log(f"inbox: {req.id} comment folded (approved kept, pre-dispatch)")
            return "running"
        if str(req.status) in (State.EXECUTING.value, State.REVIEW.value,
                               State.DELIVERED.value):
            # post-dispatch: nothing consumes the folded note — the live agent
            # never sees it. Fold for the record but ack "noop" so a phone's
            # §5.4 ledger never shows 已生效 for a direction change that had
            # no effect (audit review 2026-07-14). review 的正式改方向通道是
            # rework（打回）。
            save(req)
            _log(f"inbox: {req.id} comment folded (status {req.status} kept — "
                 f"note is record-only, acking noop)")
            return "noop"
        req.set_status(State.CARD_SENT)  # stays pending, re-approval
        save(req)
        _log(f"inbox: {req.id} comment folded — re-approval pending")
        return "running"
    elif action == "raise":
        if analyze is None:
            _log(f"inbox: {req.id} raise requested but analyze unavailable — ignored")
            return "noop"
        # §5.4 stale-guard (SYNC only): a phone-pinned expected_status that no
        # longer matches → no-op (never re-raise a card the board already moved
        # past the backlog). LOCAL callers send no expected_status, so this
        # passes and raise applies unconditionally as on main.
        if not _precondition_ok(req, expected_status):
            _log(f"inbox: {req.id} raise stale "
                 f"(expected {expected_status}, is {req.status}) — no-op")
            return "noop"
        if str(req.status) == State.RAISING.value:
            _log(f"inbox: {req.id} raise already raising — no-op")
            return "noop"
        if str(req.status) not in (State.DETECTED.value, State.CARD_SENT.value):
            # CONTRACT §32.2 (audit 2026-07-15): a late/replayed raise from a
            # stale board must never rip a card past approval back to raising
            # (approved→raising silently cancels the approval: dispatch never
            # picks it up) nor resurrect a terminal card. Backlog/proposal only;
            # card_sent stays allowed — the local web/board deliberately offers
            # 研究并提议 there (see test_actd_sync raise cases).
            _log(f"inbox: {req.id} raise ignored (status={req.status}) — no-op")
            return "noop"
        # Fast: just mark it 'raising' so it shows a processing spinner in 待审批
        # immediately. The slow claude -p expansion happens in process_raising(),
        # one item per loop pass, so 4 raises don't freeze the daemon for minutes.
        req.set_status(State.RAISING)
        save(req)
        _log(f"inbox: {req.id} -> raising (queued for AI expansion)")
        return "running"
    elif action == "trash":
        _stop_live_session(req, "trash")  # nightly audit: never orphan a live agent
        registry.trash(req, "deleted")
        _log(f"inbox: {req.id} trashed (deleted)")
        return "running"
    elif action == "restore":
        # nightly audit 2026-07-14: restore is trash-lane-only — replayed on a
        # live card it would rewrite status to prev_status-or-detected (an
        # executing card silently became detected while its agent kept running).
        if str(req.status) != State.TRASHED.value:
            _log(f"inbox: {req.id} restore ignored (status={req.status}, not trashed)")
            return "noop"
        registry.restore(req)
        _log(f"inbox: {req.id} restored -> {req.status}")
        return "running"
    elif action == "pin":
        registry.pin(req)
        _log(f"inbox: {req.id} pinned permanent")
        return "running"
    elif action == "accept":
        # §11 验收通过 -> delivered（归档）；accepted_at 供 completed 行显示（§2）
        # §5.4 stale-guard (SYNC only): a phone-pinned expected_status mismatch
        # → no-op (a stale tap must not re-deliver a card that already moved).
        # LOCAL callers send no expected_status, so accept applies exactly as on
        # main — CRUCIALLY the 待验收 lane also holds cards whose on-disk status
        # is still EXECUTING (agent done, not yet promoted: process_inbox runs
        # BEFORE reconcile_executing), so a local 验收 must land regardless of
        # the current status. A hard REVIEW-only precondition would silently
        # no-op those and, with auto_resume:false, break accept forever. The
        # phone pins expected_status="review" from that same projected lane, so
        # _precondition_ok grants the review⇄executing alias for this verb.
        if not _precondition_ok(req, expected_status, action):
            _log(f"inbox: {req.id} accept stale "
                 f"(expected {expected_status}, is {req.status}) — no-op")
            return "noop"
        # nightly audit 2026-07-14: accept needs work to accept. The 待验收
        # lane can hold on-disk EXECUTING cards (see above), so executing and
        # review are both legal; delivered is an idempotent double-click. But
        # a replayed accept on a never-dispatched card (detected/card_sent/
        # raising/…) must not teleport it to delivered.
        if str(req.status) == State.DELIVERED.value:
            _log(f"inbox: {req.id} accept ignored (already delivered)")
            return "noop"
        if str(req.status) not in (State.EXECUTING.value, State.REVIEW.value):
            _log(f"inbox: {req.id} accept ignored (status={req.status}, no delivery to accept)")
            return "noop"
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if sid and executor is not None:
            # a chat-mode delivery promoted from blocked leaves its bg session
            # alive waiting for input FOREVER (a bg session never exits on its
            # own) — mirror done_external: best-effort stop the reaped agent,
            # never block the delivered write (audit 2026-07-15).
            try:
                stopped = executor.stop_session(str(sid))
                _log(f"inbox: {req.id} accept — stop_session({sid}) -> {stopped}")
            except Exception as e:  # noqa: BLE001 - best-effort, never block delivery
                _log(f"inbox: {req.id} accept — stop_session({sid}) failed "
                     f"(ignored): {e}")
        req.set_status(State.DELIVERED)
        ex["accepted_at"] = _iso_now()
        req.execution = ex
        save(req)
        _log(f"inbox: {req.id} accepted -> delivered")
        return "running"
    elif action == "rework":
        # §11 打回：把 Zelin 的反馈送回原 session 继续（executor.rework 处理
        # stop-idle-then-resume），状态回 executing
        if executor is None:
            _log(f"inbox: {req.id} rework requested but executor unavailable — ignored")
            return "noop"
        if not (comment or "").strip():
            _log(f"inbox: {req.id} rework with empty feedback — ignored")
            return "noop"
        # §5.4 stale-guard (SYNC only): a phone-pinned expected_status mismatch
        # → no-op (a stale tap must not reopen/double-run a card that moved).
        # LOCAL callers send no expected_status, so rework applies as on main —
        # including the 待验收 EXECUTING-done case (process_inbox runs BEFORE
        # reconcile_executing promotes it to review). executor.rework itself
        # handles stop-idle-then-resume, so an on-disk EXECUTING card is safe.
        # The phone pins expected_status="review" from that same projected
        # lane, so _precondition_ok grants the review⇄executing alias here too.
        if not _precondition_ok(req, expected_status, action):
            _log(f"inbox: {req.id} rework stale "
                 f"(expected {expected_status}, is {req.status}) — no-op")
            return "noop"
        ok = executor.rework(req, comment)
        if not ok:
            # executor.rework bailed (no session / transcript purged / launch
            # failed): the card did NOT go back to executing, so acking
            # "running" would show 已生效 for a 打回 that never started
            # (§5.4 honesty, audit 2026-07-15).
            _log(f"inbox: {req.id} rework NOT sent (ok=False) — card unchanged")
            return "noop"
        _log(f"inbox: {req.id} rework sent — back to executing")
        return "running"
    elif action == "done_external":
        # v0.10.2 已办完（系统外完成）：card_sent|review -> delivered。有活
        # session 不动它 —— 人做完了，AI 会话自然闲置。
        # v0.12 扩展：approved|executing 也允许 —— agent 停在 blocked 等输入、
        # 但 Zelin 已在 attach 会话里拿到交付时，这是唯一的完成出口。
        #   executing 且有 session：先 best-effort 收割交付物（非空才写
        #   delivered_summary/final_draft，失败只 log），再 best-effort
        #   stop_session 清掉挂着的 blocked agent（失败只 log，不阻塞落账）；
        #   approved（排队未派发）：直接落账，无 harvest/stop。
        allowed = (State.CARD_SENT.value, State.REVIEW.value,
                   State.APPROVED.value, State.EXECUTING.value)
        prev_status = str(req.status)
        if prev_status not in allowed:
            _log(f"inbox: {req.id} done_external ignored (status={req.status}) — no-op")
            return "noop"
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if prev_status == State.EXECUTING.value and sid and executor is not None:
            try:
                harvested = executor.harvest_delivery(str(sid)) or {}
                if harvested.get("delivered_summary"):
                    ex["delivered_summary"] = harvested["delivered_summary"]
                if harvested.get("final_draft"):
                    ex["final_draft"] = harvested["final_draft"]
                _apply_harvest_title(req, harvested)   # §37, round boundary
            except Exception as e:  # noqa: BLE001 - harvest is best-effort
                _log(f"inbox: {req.id} done_external — "
                     f"harvest_delivery({sid}) failed (ignored): {e}")
            try:
                stopped = executor.stop_session(str(sid))
                _log(f"inbox: {req.id} done_external — stop_session({sid}) -> {stopped}")
            except Exception as e:  # noqa: BLE001 - best-effort, never block delivery
                _log(f"inbox: {req.id} done_external — "
                     f"stop_session({sid}) failed (ignored): {e}")
            _update_search_index(req.id, sid)          # §37 session-content layer
        ex["accepted_at"] = _iso_now()
        req.execution = ex
        tag = "[done outside] Zelin 在系统外完成"
        req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
        req.set_status(State.DELIVERED)
        save(req)
        _log(f"inbox: {req.id} done_external ({prev_status}) -> delivered")
        return "running"
    elif action == "abort_execution":
        # v0.10.2 停止并退回待审批：approved|executing -> card_sent。活 session
        # 先 best-effort 停止（stop 失败只记日志，绝不阻塞状态回退）；session_id
        # 归档到 aborted_session_id 后删除，保证重新批准时干净重派发。
        # v0.28.1 §30: review is allowed too — a 待验收 card routed into 运行中
        # by attach-reactivated session activity; 「退回提案」 discards this
        # reattached run and kicks it back to card_sent for a fresh decision.
        if str(req.status) not in (State.APPROVED.value, State.EXECUTING.value,
                                   State.REVIEW.value):
            _log(f"inbox: {req.id} abort_execution ignored (status={req.status}) — no-op")
            return "noop"
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
        return "running"
    elif action == "stop_to_review":
        # 手动停止转待验收（「去待验收」）：executing（+ approved）-> review。
        # 三个「停」动作的分工：done_external =「我在系统外做完了」直接落
        # delivered 跳过验收；abort_execution =「不要了」丢弃成果退回待审批；
        # stop_to_review =「停下来我看看它做了什么」—— 停 agent、收下成果、
        # 落 待验收 让 Zelin ✓验收/↩︎打回，绝不跳过验收。
        #   executing 且有 session：先 best-effort harvest_delivery（非空才写
        #   delivered_summary/final_draft），再 best-effort stop_session 停掉
        #   跑着的 agent；两步都吞异常只记日志，绝不阻塞状态落 review。
        #   approved（排队未派发，无 session）：harvest 为空，直接落 review
        #   （空交付物，待验收卡照常渲染，不崩）。
        #   review（v0.28.1 §30：会话有新活动被路由进「运行中」的卡，registry
        #   仍是 review、带活 session）：停掉 attach 回流的 session、重新收割成果、
        #   留在 review —— 「去待验收」在这种卡上就是「停下我看看它这轮跑了什么」。
        allowed = (State.EXECUTING.value, State.APPROVED.value, State.REVIEW.value)
        prev_status = str(req.status)
        if prev_status not in allowed:
            _log(f"inbox: {req.id} stop_to_review ignored (status={req.status}) — no-op")
            return "noop"
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        # harvest whenever a live session exists (executing OR a review card with
        # an attach-reactivated session); approved has no sid so this skips.
        if sid and executor is not None:
            try:
                harvested = executor.harvest_delivery(str(sid)) or {}
                if harvested.get("delivered_summary"):
                    ex["delivered_summary"] = harvested["delivered_summary"]
                if harvested.get("final_draft"):
                    ex["final_draft"] = harvested["final_draft"]
                _apply_harvest_title(req, harvested)   # §37, round boundary
            except Exception as e:  # noqa: BLE001 - harvest is best-effort
                _log(f"inbox: {req.id} stop_to_review — "
                     f"harvest_delivery({sid}) failed (ignored): {e}")
            try:
                stopped = executor.stop_session(str(sid))
                _log(f"inbox: {req.id} stop_to_review — stop_session({sid}) -> {stopped}")
            except Exception as e:  # noqa: BLE001 - best-effort, never block review write
                _log(f"inbox: {req.id} stop_to_review — "
                     f"stop_session({sid}) failed (ignored): {e}")
            _update_search_index(req.id, sid)          # §37 session-content layer
        # mirror the natural executing->review transition's review fields
        # (reconcile_executing §2/§11): done flag + review_at, so the 待验收 card
        # renders (dashboard reads execution.review_at) and a later purge is
        # never mistaken for a crash needing auto-resume.
        ex["done"] = True
        ex["review_at"] = _iso_now()
        req.execution = ex
        tag = "[stopped by user] 手动停止，已收下成果待验收"
        req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
        req.set_status(State.REVIEW)
        save(req)
        _log(f"inbox: {req.id} stop_to_review ({prev_status}) -> review")
        return "running"
    elif action == "revert_review":
        # v0.10.2 退回待验收：delivered -> review（验收撤回）。
        if str(req.status) != State.DELIVERED.value:
            _log(f"inbox: {req.id} revert_review ignored (status={req.status}) — no-op")
            return "noop"
        ex = dict(req.execution or {})
        ex.pop("accepted_at", None)
        ex["reverted_at"] = _iso_now()
        req.execution = ex
        req.set_status(State.REVIEW)
        save(req)
        _log(f"inbox: {req.id} revert_review -> review")
        return "running"
    elif action == "defer":
        # v0.18 存备选：card_sent -> detected（退回备选）。Deliberately NOT
        # trash: a deferred card keeps its expanded summary/plan/sources/
        # repeated_mentions and stays in merge_or_new matching (restatements
        # merge in; radar act-now re-promotes) — trashed cards are excluded
        # and would re-card from scratch. Only card_sent is allowed (raising
        # finishes its expansion and becomes card_sent first); anything else
        # is the v0.10.2 idempotent no-op. Undo = the backlog lane's raise.
        if str(req.status) != State.CARD_SENT.value:
            _log(f"inbox: {req.id} defer ignored (status={req.status}) — no-op")
            return "noop"
        tag = "[deferred] 暂缓，入库"
        req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
        req.set_status(State.DETECTED)
        save(req)
        _log(f"inbox: {req.id} defer -> detected (backlog)")
        return "running"
    elif action == "archive":
        # v0.20.0 封存线程 (§3.7): archive is reachable ONLY from 已验收
        # (delivered) or 备选 (detected) per Q2; anything else is the v0.10.2
        # idempotent no-op. registry.archive relocates the card to archive/ and
        # stamps prev_status/archived_at/archive_reason.
        if str(req.status) not in (State.DELIVERED.value, State.DETECTED.value):
            _log(f"inbox: {req.id} archive ignored (status={req.status}) — no-op")
            return "noop"
        prev = str(req.status)
        registry.archive(req, reason="user")
        _log(f"inbox: {req.id} archived (from {prev})")
        return "running"
    elif action == "unarchive":
        # v0.20.0 取消归档 (§3.7): archived -> prev_status, file back to active dir.
        if str(req.status) != State.ARCHIVED.value:
            _log(f"inbox: {req.id} unarchive ignored (status={req.status}) — no-op")
            return "noop"
        registry.unarchive(req)
        _log(f"inbox: {req.id} unarchived -> {req.status}")
        return "running"
    else:
        _log(f"inbox: {req.id} unknown action {action!r} — ignored")
        return "unknown"


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
# (b) dispatch approved
# --------------------------------------------------------------------------- #
def dispatch_approved(cfg: config.Config) -> int:
    count = 0
    for req in load_all():
        if req.status != State.APPROVED.value:
            continue
        if req.execution and req.execution.get("session_id"):
            continue  # already dispatched
        if executor is None:
            _log(f"dispatch: executor unavailable, cannot dispatch {req.id}")
            continue
        try:
            executor.dispatch(req, cfg)
            _log(f"dispatch: {req.id} -> executing "
                 f"(session={ (req.execution or {}).get('session_id') })")
            count += 1
            # retry succeeded -> clear the failure left by a previous attempt.
            # (dispatch rebuilds execution so this is usually a no-op; kept as a
            # belt-and-braces so a stale last_error never lingers on a live run.)
            # Gated on session_id: a non-raising dispatch that produced no
            # session is a FAILURE, and wiping last_error here would erase the
            # only trace the queued card can show as dispatch_error.
            ex = dict(req.execution or {})
            if ex.get("session_id") and ("last_error" in ex or "last_error_at" in ex):
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
            # executor.dispatch already emits dispatch_failed (with reason/attempt)
            # for DispatchError. Only log unexpected crashes here so analytics
            # is not double-counted for a single failed launch (issue #12).
            if executor is not None and isinstance(e, executor.DispatchError):
                pass
            else:
                analytics.log_event(
                    "dispatch_failed",
                    req=req.id,
                    error=err[:120],
                    reason="dispatch_crashed",
                )
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
# (c') auto-archive stale delivered matters (卡片生命周期 §4 / #10) — DEFAULT OFF
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
    """A delivered card with a deadline still in the future (USCIS/长 matter
    里程碑) must NOT be auto-sealed — new mail on it would open a dup card."""
    if not req.deadline:
        return False
    try:
        d = _dt.date.fromisoformat(str(req.deadline))
    except ValueError:
        return False
    return d >= _dt.date.today()


def _cluster_has_live_sibling(req: Requirement, all_reqs: list[Requirement]) -> bool:
    """True if any OTHER card in this thread/lineage cluster is still open —
    never seal a matter that still has live work attached."""
    thread = req.thread_id or req.id
    for r in all_reqs:
        if r.id == req.id:
            continue
        same_cluster = (
            (r.thread_id or r.id) == thread
            or r.improvement_of == req.id
            or req.improvement_of == r.id
        )
        if same_cluster and str(r.status) in _OPEN_STATES:
            return True
    return False


def _thread_last_activity(req: Requirement) -> Optional[_dt.datetime]:
    """Newest activity timestamp for the card (cross-dep; legacy fallback =
    accepted_at). None when nothing is parseable — then the card is never
    auto-archived (conservative: ambiguous cards are left alone)."""
    ex = req.execution if isinstance(req.execution, dict) else {}
    cands = (ex.get("accepted_at"), ex.get("approved_at"),
             ex.get("dispatched_at"), ex.get("review_at"),
             ex.get("reraised_at"))
    dts = [d for d in (_parse_iso(c) for c in cands) if d is not None]
    return max(dts) if dts else None


def archive_stale(cfg: config.Config) -> int:
    """Auto-archive cold DELIVERED cards (§4 / #10). DEFAULT OFF.

    ``archive_after_days`` defaults to 0 (off) — long-silent immigration/EB-1A
    matters must not be auto-sealed, or new mail re-opens a duplicate card (the
    very bug this feature kills). When enabled it runs at most once per 24h and
    skips cards with a future deadline or a live sibling in their cluster."""
    days = int(getattr(cfg, "archive_after_days", 0) or 0)
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
# (c'') merge-review job housekeeping (§21) — every pass, best-effort
# --------------------------------------------------------------------------- #
def _mtime_dt(path: Path) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc)
    except OSError:
        return None


def cleanup_merge_jobs() -> int:
    """契约 五 actd 每 pass 顺带：state/merge/ 里超过 expires_at 的 done/
    dismissed/failed 作业文件删除；analyzing 超过 20 分钟的置 failed("analysis
    timed out")。缺失/坏 expires_at 用 requested_at（否则文件 mtime）+24h 兜底；
    损坏文件直接删。Returns the number of files removed."""
    if merge_review is None:
        return 0
    try:
        files = sorted(merge_review.MERGE_DIR.glob("*.json"))
    except OSError:
        return 0
    now = _dt.datetime.now(_dt.timezone.utc)
    ttl = _dt.timedelta(hours=merge_review.TTL_HOURS)
    removed = 0
    for path in files:
        try:
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                job = None
            if not isinstance(job, dict):
                _log(f"merge: corrupt job file {path.name} — removed")
                _safe_unlink(path)
                removed += 1
                continue
            status = str(job.get("status") or "")
            if status == "analyzing":
                started = _parse_iso(job.get("requested_at")) or _mtime_dt(path)
                if started is not None and (
                        (now - started).total_seconds()
                        > merge_review.ANALYZING_TIMEOUT):
                    merge_review.mark_failed(str(job.get("id") or path.stem),
                                             "analysis timed out")
                    _log(f"merge: {path.stem} analyzing >20min -> failed (timed out)")
                continue
            if status in ("done", "dismissed", "failed"):
                expires = _parse_iso(job.get("expires_at"))
                if expires is None:
                    base = _parse_iso(job.get("requested_at")) or _mtime_dt(path)
                    expires = base + ttl if base is not None else None
                if expires is not None and now > expires:
                    _safe_unlink(path)
                    removed += 1
                    _log(f"merge: {path.stem} expired ({status}) — removed")
        except Exception as e:  # noqa: BLE001 - one bad job must not abort the pass
            _log(f"merge: cleanup {path.name} failed: {e}")
    return removed


# --------------------------------------------------------------------------- #
# (d) transition detection
# --------------------------------------------------------------------------- #
# §40: more than this many fresh proposals in one pass collapse to one
# notification (msg_new_cards_batch). At 1-2 the per-card copy is still the
# more useful one — it names the ask.
_NEW_CARD_BATCH_ABOVE = 2


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items if i.get("id")}


def detect_transitions(prev: Optional[dict], curr: dict) -> list[tuple[str, str, Optional[str]]]:
    """Return (title, body, req_id) notifications for prev->curr transitions.

    req_id is None for the §40 batched new-cards entry (it names no single
    card); every other class carries the card id."""
    msgs: list[tuple[str, str]] = []
    if prev is None:
        return msgs

    p_na, c_na = _by_id(prev.get("needs_approval", [])), _by_id(curr.get("needs_approval", []))
    p_run = _by_id(prev.get("running", []))
    p_ni, c_ni = _by_id(prev.get("needs_input", [])), _by_id(curr.get("needs_input", []))
    p_rev, c_rev = _by_id(prev.get("review", [])), _by_id(curr.get("review", []))

    # 3-tuples (title, body, req); req is carried for caller compatibility (the
    # phone ✅-reaction approval surface was removed in v0.21 — Mac app only).
    # new card_sent — a re-raised card (v0.20.0「回锅」) uses the Returned copy
    # so Zelin knows it's a card he already accepted, not a brand-new find.
    # §40 batching: >2 fresh (non-reraised) proposals in one pass collapse to
    # ONE 「新增 N 张待审批卡」 — a radar backfill used to fire n pings in a
    # row. 回锅 stays per-card (each names a prior decision of the user's), as
    # do the 需输入/待验收 classes below. The §28 relay queue's 10-min stale
    # sweep is untouched — one batched entry ages out like any other.
    # Cards filed by the weekly digest are skipped entirely: its own
    # notification already announced them by count (「另有 N 条自动化建议进了
    # 待审批」) — re-announcing them here (per-card or batched) was a
    # duplicate ping every suggestion-bearing Monday. Seam = the row's source
    # channel (weekly_digest.SOURCE_CHANNEL rides the dashboard projection).
    fresh: list[tuple[str, dict]] = []
    for rid, item in c_na.items():
        if rid not in p_na:
            if item.get("reraised"):
                t, b = notify.msg_reraised(item.get("title", rid),
                                           item.get("reraised_note") or "")
                msgs.append((t, b, rid))
            elif any(isinstance(s, dict) and s.get("channel") == "weekly-digest"
                     for s in item.get("sources") or []):
                continue  # announced by the digest's own notification
            else:
                fresh.append((rid, item))
    if len(fresh) > _NEW_CARD_BATCH_ABOVE:
        t, b = notify.msg_new_cards_batch(len(fresh))
        msgs.append((t, b, None))
    else:
        for rid, item in fresh:
            t, b = notify.msg_new_card(item.get("title", rid))
            msgs.append((t, b, rid))

    # executing -> review (§11 draft ready, awaiting acceptance)
    for rid, item in c_rev.items():
        if rid not in p_rev and rid in p_run:
            # §30 v0.28.1: skip when the previous running row was a `from_review`
            # re-run (an already-delivered 待验收 card whose attach-reactivated
            # session settled back to review). It was NOT a fresh delivery — on
            # main it never left review[] and never notified — so re-firing
            # "待验收：AI 已交付草稿" on every working↔idle bounce is spurious spam.
            if p_run.get(rid, {}).get("from_review"):
                continue
            t, b = notify.msg_review_ready(item.get("name") or rid)
            msgs.append((t, b, rid))

    # executing -> blocked (newly needs_input, previously running). §39: the
    # notification carries a snippet of the QUESTION the agent is asking.
    for rid, item in c_ni.items():
        if rid not in p_ni and rid in p_run:
            t, b = notify.msg_needs_input(item.get("name") or rid,
                                          item.get("question"))
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
def _reconcile_review_attach(req: Requirement, agents: dict[str, dict]) -> None:
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
                _log(f"reconcile: {req.id} session-active（attach/会话有新活动，非打回返工）")
                analytics.log_event("review_active", req=req.id)
            return

        if ex.get("_review_active") and (agent is None or state in _DONE_STATES):
            # 会话活动结束 -> 重新收割交付物（收割失败/为空不覆盖旧值）
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
                _apply_harvest_title(req, harvested)   # §37, round boundary
            ex.pop("_review_active", None)
            req.execution = ex
            registry.save(req)
            _update_search_index(req.id, sid)          # §37 session-content layer
            _log(f"reconcile: {req.id} 会话活动结束，已重新收割交付物（attach 回流）")
            analytics.log_event("review_reharvested", req=req.id)
    except Exception as e:  # noqa: BLE001 - must never break the daemon pass
        _log(f"reconcile: review attach check {getattr(req, 'id', '?')} failed: {e}")


# transcript-probe throttle for _promote_if_delivered: a genuinely blocked
# agent (no FINAL DRAFT yet) would otherwise get its transcript tail re-read
# every 10 s pass. Process-local is fine — actd is a resident daemon.
_HARVEST_PROBE_AT: dict = {}
_HARVEST_PROBE_INTERVAL_S = 120.0


def _promote_if_delivered(req, ex: dict, sid) -> bool:
    """Promote to 待验收 IFF the transcript carries the standalone FINAL DRAFT
    marker — the chat-delivery contract's STRONG completion signal. A bare
    delivered_summary is any dead session's last words, never proof of
    delivery, so it must not short-circuit a resume. Returns True when
    promoted (callers `continue`).
    """
    if executor is None:
        return False
    now = time.monotonic()
    # None sentinel, NOT 0.0: monotonic() counts from boot, so on a freshly
    # started machine `now - 0.0 < interval` is TRUE for the first minutes —
    # a 0.0 default swallowed the very first probe (surfaced on CI runners,
    # whose uptime is seconds; a just-rebooted Mac would hit it too).
    last = _HARVEST_PROBE_AT.get(str(sid))
    if last is not None and now - last < _HARVEST_PROBE_INTERVAL_S:
        return False
    _HARVEST_PROBE_AT[str(sid)] = now
    try:
        harvested = executor.harvest_delivery(str(sid)) or {}
    except Exception:  # noqa: BLE001 - the probe is best-effort
        return False
    if not str(harvested.get("final_draft") or "").strip():
        return False
    ex["done"] = True
    ex["review_at"] = _iso_now()
    if harvested.get("delivered_summary"):
        ex["delivered_summary"] = harvested["delivered_summary"]
    ex["final_draft"] = harvested["final_draft"]
    _apply_harvest_title(req, harvested)   # §37, round boundary
    req.execution = ex
    req.set_status(registry.State.REVIEW)
    registry.save(req)
    _update_search_index(req.id, sid)      # §37 session-content layer
    exec_s = None
    disp_dt = _parse_iso(ex.get("dispatched_at"))
    if disp_dt is not None:
        exec_s = max(0, round(
            (_dt.datetime.now(_dt.timezone.utc) - disp_dt).total_seconds()))
    analytics.log_event("review_promoted", req=req.id, exec_s=exec_s)
    _log(f"reconcile: {req.id} promoted to review — transcript already "
         f"carries FINAL DRAFT (session {sid} blocked or purged)")
    return True


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
            # waiting for the USER to answer (needs input) — usually NOT dead,
            # and resuming a blocked agent spawns duplicates. But FIRST check
            # for a completed delivery: a chat-mode agent that printed its
            # FINAL DRAFT block settles in exactly this waiting-input state
            # (a bg session never exits on its own), and 2026-07-14 R-041 sat
            # here for hours with the finished brief already in the
            # transcript while the board said 需输入.
            if not ex.get("done") and _promote_if_delivered(req, ex, sid):
                continue
            # §44.3: a blocked session is the safe injection window — flush
            # any queued silent-merge briefings (stop-idle-then-resume, the
            # answer() plumbing; the resumed session un-blocks as a bonus).
            if ex.get("pending_briefings") and executor is not None:
                try:
                    executor.brief(req, cfg)
                except Exception as e:  # noqa: BLE001 - FYI only, never fatal
                    _log(f"reconcile: brief {req.id} failed: {e}")
                continue
            if ex.get("resume_attempts"):
                ex["resume_attempts"] = 0
                req.execution = ex
                registry.save(req)
            resume_notified.discard(req.id)
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
                    _apply_harvest_title(req, harvested)   # §37, round boundary
                except Exception as e:  # noqa: BLE001 - harvest is best-effort
                    _log(f"reconcile: harvest_delivery {req.id} failed: {e}")
                req.execution = ex
                # §11: agent done = 草稿就绪，进入待验收（Zelin ✓验收/↩︎打回）。
                # 通知由 detect_transitions 的 running->review diff 发，避免双发。
                req.set_status(registry.State.REVIEW)
                registry.save(req)
                _update_search_index(req.id, sid)          # §37 session-content layer
                # exec_s (metadata): dispatch -> delivery wall time. No
                # summary excerpt anymore (v0.18): delivered_summary is MODEL
                # OUTPUT, which telemetry never stores at any setting
                # (docs/TELEMETRY.md red line) — the pre-v0.18 detailed-level
                # summary field is retired, not moved behind capture_input.
                exec_s = None
                disp_dt = _parse_iso(ex.get("dispatched_at"))
                if disp_dt is not None:
                    exec_s = max(0, round(
                        (_dt.datetime.now(_dt.timezone.utc) - disp_dt)
                        .total_seconds()))
                analytics.log_event("review_promoted", req=req.id,
                                    exec_s=exec_s)
            continue
        if ex.get("done"):
            # finished earlier; agent purged from the list — promote if missed
            if req.status == registry.State.EXECUTING.value:
                req.set_status(registry.State.REVIEW)
                registry.save(req)
            continue

        # dead (failed/stopped) or vanished-before-completing. BEFORE burning
        # a resume, check the transcript for a completed delivery: a session
        # that finishes while the Mac sleeps is purged from the roster before
        # any reconcile pass ever sees it in a done state (2026-07-14 R-041),
        # and resuming a finished session only spawns a confused duplicate.
        if not ex.get("done") and _promote_if_delivered(req, ex, sid):
            continue
        # -> resume w/ backoff
        if ex.get("resume_exhausted"):
            continue
        attempts = int(ex.get("resume_attempts", 0))
        if attempts >= 5:
            ex["resume_exhausted"] = True
            req.execution = ex
            registry.save(req)
            # §5 v0.14 copy: bilingual + names the exact card buttons to press
            notify.notify(*notify.msg_auto_resume_exhausted(req.title or req.id),
                          req=req.id)
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
            # §44.3: a dead session with queued briefings — resume WITH the
            # briefing prompt instead of a bare resume (one launch, two jobs).
            if ex.get("pending_briefings"):
                ok = executor.brief(req, cfg)
            else:
                ok = executor.resume(req, cfg)
            if not ok:
                # executor.resume's early-return paths (transcript purged, mkdir
                # failed) record NO bookkeeping — without it attempts stays 0
                # forever: the exhaustion notification never fires and the
                # resume+log+analytics burst repeats every 10s pass with zero
                # backoff (audit 2026-07-15). Count the failed attempt here iff
                # resume didn't already (its post-launch bookkeeping did).
                ex_after = dict(req.execution or {})
                if int(ex_after.get("resume_attempts", 0) or 0) == attempts:
                    ex_after["resume_attempts"] = attempts + 1
                    ex_after["last_resume_at"] = _iso_now()
                    ex_after["last_resume_ok"] = False
                    req.execution = ex_after
                    registry.save(req)
            resumed += 1
            _log(f"reconcile: resume {req.id} attempt {attempts + 1} ok={ok}")
            analytics.log_event("auto_resume", req=req.id, ok=ok, attempt=attempts + 1)
            if attempts + 1 >= 3 and req.id not in resume_notified:
                resume_notified.add(req.id)
                notify.notify(*notify.msg_resuming(req.title or req.id))
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
    n_dispatched = dispatch_approved(cfg)
    # write-early：审批/派发刚落账就先写一次 dashboard，app 立刻看到 queued/executing
    # 回显，不用等 reconcile/raising（都可能慢）跑完；pass 尾部照常再写最终版。
    # 仅在真有变化时才写 —— 空闲 pass 不额外跑一次 build_dashboard（内含
    # `claude agents` 子进程 + 全量 registry 加载，白白翻倍热路径开销）。
    if n_inbox or n_dispatched:
        try:
            write_dashboard(build_dashboard(cfg=cfg))
        except Exception as e:  # noqa: BLE001 - early write is best-effort
            _log(f"early dashboard write FAILED: {e}")
    reconcile_executing(cfg, resume_notified if resume_notified is not None else set())
    process_raising(cfg)     # expand ONE 'raising' debt per pass (bounded block)
    purge_trash(cfg)
    archive_stale(cfg)       # §4 / #10: auto-archive cold delivered (DEFAULT OFF)
    cleanup_merge_jobs()     # §21: TTL sweep + fail stuck 'analyzing' jobs
    try:
        # §44: fail stuck silent-merge checks + purge expired job files
        from act.lib import silent_merge
        silent_merge.sweep()
    except Exception:  # noqa: BLE001 - sweep must not kill the daemon
        pass
    if auto_merge is not None:
        # §38/§44: deterministic near-dupe rule for newly appeared open cards
        # → detached silent two-card check (radar cron files cards from
        # outside this process, so "new" is detected by ledger diff).
        auto_merge.scan_new_cards()
    try:
        # §37 session-content search layer: drop terminal/absent cards. Cheap:
        # returns immediately when state/search_index.json doesn't exist.
        from act.lib import search_index
        search_index.prune()
    except Exception as e:  # noqa: BLE001 - housekeeping must not kill the pass
        _log(f"search index prune failed: {e}")
    if feedback is not None:
        # §29: retry pending feedback uploads ONCE, then give up (uploaded:
        # false). Records created THIS pass (process_inbox above already did
        # their inline attempt) are age-gated inside retry_pending, so the
        # single retry lands on a genuinely later pass, not seconds later
        # inside the same outage. Cheap when state/feedback/ is empty.
        feedback.retry_pending(cfg)
    dash = build_dashboard(cfg=cfg)
    # §26 in-app update check: cheap (ETag-cached, at most one network attempt
    # per 24h) and never raises — the field is simply absent when no newer
    # release is known or the check is disabled.
    if update_check is not None:
        dash = update_check.attach(dash, cfg)
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

    try:
        cfg = config.load_config()
    except Exception as e:  # noqa: BLE001 — 坏 config.yaml/overrides 绝不拒启：
        # 用内置默认起动并 log 一条（load_config 自身已防崩，这里是纵深防御）
        _log(f"load_config FAILED at startup ({e}); using built-in defaults")
        cfg = config.Config()
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
