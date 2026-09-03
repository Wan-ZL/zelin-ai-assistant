"""merge — merge-review (CONTRACT §21) on the actd side: validate + job file +
detached analysis; apply is DETERMINISTIC (the AI's action_plan is display-only);
plus the per-pass job housekeeping (契约 五).

Terminal/sealed states a merge may never write into or absorb from: folding
live cards into a trashed/merged/archived primary buries them in terminal
MERGED (no un-merge, no lane renders them) and their carried deliverables
get hard-deleted with the primary at trash purge (audit 2026-07-15).
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, maintenance, registry
from act.lib.actd.seam import Daemon, append_note
from act.lib.registry import Requirement, State, load

MERGE_DEAD_STATES = (State.TRASHED.value, State.MERGED.value,
                     State.REJECTED.value, State.ARCHIVED.value)


# --------------------------------------------------------------------------- #
# 契约 五：merge_review 请求 → analyzing 作业 + 分离子进程
# --------------------------------------------------------------------------- #
def apply_merge_review(d: Daemon, ids) -> str:
    """契约 五 actd 侧：校验 ids（≥2、去重、都存在）→ 建 analyzing 作业文件 →
    subprocess.Popen 分离启动 ``python -m act.merge_review <id>``（不等待，
    stdout/err 落 state/logs/<suggestion_id>.log）。不合法 -> log 丢弃。
    Returns the §5.4 result_status ("running" job created | "noop" dropped)."""
    if d.merge_review is None:
        d.log("inbox: merge_review requested but module unavailable — dropped")
        return "noop"
    uniq = _review_ids(d, ids)
    if uniq is None:
        return "noop"
    job = d.merge_review.create_job(uniq)
    sid = str(job["id"])
    if not _launch_analysis(d, sid):
        # the job file exists and visibly shows failed — a real, durable change
        return "running"
    d.log(f"inbox: merge_review {sid} ids={uniq} — analysis subprocess started")
    analytics.log_event("merge_review_requested", n=len(uniq), suggestion=sid)
    return "running"


def _review_ids(d: Daemon, ids) -> Optional[list]:
    """≥2 distinct existing cards, canonicalised to primary keys（§60.3）; None = dropped."""
    raw = ids if isinstance(ids, list) else []
    # §60.3：ids 可以是主键或工作编号，归一成主键（同卡的两种写法折成一张）
    uniq, missing = registry.canonical_ids(raw)
    if missing:
        d.log(f"inbox: merge_review unknown ids {missing} — dropped")
        return None
    if len(uniq) < 2:
        d.log(f"inbox: merge_review needs >=2 distinct cards, got {raw!r} — dropped")
        return None
    return uniq


def _launch_analysis(d: Daemon, sid: str) -> bool:
    """Detached ``python -m act.merge_review <sid>``; False = launch failed (job marked)."""
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
        d.merge_review.mark_failed(sid, f"analysis launch failed: {e}")
        d.log(f"inbox: merge_review {sid} launch FAILED: {e}")
        return False
    return True


# --------------------------------------------------------------------------- #
# 强制合并（§21 v0.31）
# --------------------------------------------------------------------------- #
def apply_merge_force(d: Daemon, ids, primary) -> str:
    """契约 §21 强制合并（v0.31）：用户钦定主卡、跳过 AI 直接落地 ``merge``。
    校验 ids（≥2、去重、都存在）+ primary ∈ ids → 复用 :func:`merge_into_primary`
    ——与 AI ``merge`` verdict 逐字同一条确定性执行路径（主卡吸收 sources 去重 /
    repeated_mentions 累加 / notes 留痕 / 交付物搬运，副卡 best-effort 停 session +
    置 ``merged``；主卡在待验收则 rework 注入）。不合法 = log 丢弃（同 merge_review
    公共规则）；执行失败只 log + 打点 outcome=fail，绝不抛穿轮询（用户可重试）。
    Returns the §5.4 result_status ("running" applied | "noop" dropped/failed)."""
    targets = _force_targets(d, ids, primary)
    if targets is None:
        return "noop"
    prim, secondaries, n = targets
    try:
        d.merge_into_primary(prim, secondaries)
    except Exception as e:  # noqa: BLE001 - never hang the poll; user can retry/redo
        d.log(f"inbox: merge_force primary={prim} secondaries={secondaries} "
              f"FAILED: {e}\n{traceback.format_exc()}")
        analytics.log_event("merge_force", n=n, outcome="fail")
        return "noop"
    d.log(f"inbox: merge_force primary={prim} secondaries={secondaries} applied")
    analytics.log_event("merge_force", n=n, outcome="ok")
    return "running"


def _force_targets(d: Daemon, ids, primary) -> Optional[tuple]:
    """Validate a merge_force request → (primary id, secondaries, n) or None（logged）."""
    raw = ids if isinstance(ids, list) else []
    # §60.3：ids / primary 都可以是主键或工作编号；lineage（merged_into）只认主键
    uniq, missing = registry.canonical_ids(raw)
    if missing:
        d.log(f"inbox: merge_force unknown ids {missing} — dropped")
        return None
    prim_req = registry.resolve(str(primary or "").strip())
    if prim_req is None or prim_req.id not in uniq:
        d.log(f"inbox: merge_force primary {primary!r} not in ids {uniq} — dropped")
        return None
    return _force_shape(d, raw, uniq, prim_req)


def _force_shape(d: Daemon, raw: list, uniq: list, prim_req: Requirement) -> Optional[tuple]:
    prim = prim_req.id
    if len(uniq) < 2:
        d.log(f"inbox: merge_force needs >=2 distinct cards, got {raw!r} — dropped")
        return None
    if str(prim_req.status) in MERGE_DEAD_STATES:
        # a stale board can pick a primary the user meanwhile trashed/merged/
        # archived — folding live cards into it loses them (audit 2026-07-15)
        d.log(f"inbox: merge_force primary {prim} is {prim_req.status} — dropped")
        return None
    return prim, [i for i in uniq if i != prim], len(uniq)


# --------------------------------------------------------------------------- #
# 契约 一/四：merge_apply / merge_dismiss
# --------------------------------------------------------------------------- #
def apply_merge_decision(d: Daemon, action: str, suggestion_id) -> str:
    """契约 一/四：merge_apply（status=done 才可执行，按 verdict 确定性落地，然后
    作业标记 dismissed 留到 TTL 清理）；merge_dismiss（直接标记 dismissed）。
    状态不匹配 / 未知建议 = 幂等 no-op + log（同 v0.10.2 逆向动作公共规则）。
    Returns the §5.4 result_status ("running" | "noop" | "unknown")."""
    if d.merge_review is None:
        d.log(f"inbox: {action} requested but merge_review unavailable — dropped")
        return "noop"
    sid, job = _load_suggestion(d, suggestion_id)
    if job is None:
        d.log(f"inbox: {action} for unknown suggestion {suggestion_id!r} — dropped")
        return "unknown"
    status = str(job.get("status") or "")
    if action == "merge_dismiss":
        return _merge_dismiss(d, sid, job, status)
    return _merge_apply(d, sid, job, status)


def _load_suggestion(d: Daemon, suggestion_id) -> tuple:
    sid = str(suggestion_id or "").strip()
    return sid, (d.merge_review.load_job(sid) if sid else None)


def _merge_dismiss(d: Daemon, sid: str, job: dict, status: str) -> str:
    if status == "dismissed":
        d.log(f"inbox: merge_dismiss {sid} already dismissed — no-op")
        return "noop"
    d.merge_review.dismiss_job(job)
    d.log(f"inbox: merge_dismiss {sid} (was {status})")
    return "running"


def _merge_apply(d: Daemon, sid: str, job: dict, status: str) -> str:
    # merge_apply — only a finished analysis is actionable (连点/迟到 -> no-op)
    if status != "done":
        d.log(f"inbox: merge_apply {sid} ignored (status={status}) — no-op")
        return "noop"
    verdict = str(job.get("verdict") or "")
    if _primary_gone(d, sid, job, verdict):
        return "noop"
    # merge_apply outcome at the authoritative apply site (docs/TELEMETRY.md):
    # the app's card_action only records intent — a failed deterministic apply
    # was invisible to telemetry before this. No-op paths above stay unlogged
    # (double-clicks are not usage). Metadata only: ids + outcome, no content.
    try:
        d.apply_merge_verdict(job)
    except Exception as e:  # noqa: BLE001 - job stays 'done' so Zelin can retry/dismiss
        d.log(f"inbox: merge_apply {sid} ({verdict}) FAILED: {e}\n"
              f"{traceback.format_exc()}")
        analytics.log_event("merge_apply", suggestion=sid, verdict=verdict,
                            outcome="fail")
        return "noop"
    d.merge_review.dismiss_job(job, applied=True)  # 即刻从 dashboard 消失，文件留到 TTL
    d.log(f"inbox: merge_apply {sid} ({verdict}) applied")
    analytics.log_event("merge_apply", suggestion=sid, verdict=verdict,
                        outcome="ok")
    return "running"


def _primary_gone(d: Daemon, sid: str, job: dict, verdict: str) -> bool:
    """A done suggestion stays actionable for its 24h TTL, but the board may
    have moved meanwhile: the user can trash/merge/archive the primary and
    THEN tap 采纳 from a stale surface. Applying would fold live secondaries
    into a dead primary — terminal MERGED, no undo, deliverables purged with
    the primary later. Fail the job visibly instead (audit 2026-07-15)."""
    if verdict not in ("merge", "link_improvement"):
        return False
    prim = load(str(job.get("primary") or ""))
    if prim is not None and str(prim.status) not in MERGE_DEAD_STATES:
        return False
    d.merge_review.mark_failed(sid, "主卡已删除/已合并/已封存，该合并建议已失效")
    d.log(f"inbox: merge_apply {sid} ({verdict}) primary "
          f"{job.get('primary')!r} is gone/dead — job failed, no-op")
    analytics.log_event("merge_apply", suggestion=sid, verdict=verdict,
                        outcome="fail")
    return True


# --------------------------------------------------------------------------- #
# 契约 四：确定性 apply
# --------------------------------------------------------------------------- #
def _job_fields(job: dict) -> tuple:
    """(verdict, ids, primary_id) — every field string-coerced, blanks for absent."""
    return (str(job.get("verdict") or ""),
            [str(i) for i in job.get("ids") or []],
            str(job.get("primary") or ""))


def apply_merge_verdict(d: Daemon, job: dict) -> None:
    """契约 四 确定性 apply 语义。keep_separate = no-op（调用方统一 dismiss）。"""
    verdict, ids, primary_id = _job_fields(job)
    if verdict == "keep_separate":
        return
    if verdict == "partition":
        # 多对多分组：作业文件自带分组方案，顶层 primary 无执行语义。
        apply_merge_partition(d, job)
        return
    secondaries = [i for i in ids if i != primary_id]
    _require_usable(verdict, primary_id, ids, secondaries)
    _apply_pairwise_verdict(d, verdict, primary_id, secondaries)


def _apply_pairwise_verdict(d: Daemon, verdict: str, primary_id: str, secondaries: list) -> None:
    if verdict == "link_improvement":
        _link_improvements(d, secondaries, primary_id)
    elif verdict == "close_secondary":
        _close_secondaries(d, secondaries)
    else:
        d.merge_into_primary(primary_id, secondaries)


def _require_usable(verdict: str, primary_id: str, ids: list, secondaries: list) -> None:
    if (verdict not in ("merge", "link_improvement", "close_secondary")
            or primary_id not in ids or not secondaries):
        raise ValueError(
            f"unusable job: verdict={verdict!r} primary={primary_id!r} ids={ids}")


def _link_improvements(d: Daemon, secondaries: list, primary_id: str) -> None:
    # 副卡挂为主卡的改进卡，其余（状态/execution）一律不动。
    for rid in secondaries:
        sec = load(rid)
        if sec is None:
            d.log(f"merge: link_improvement {rid} not found — skipped")
            continue
        sec.improvement_of = primary_id
        d.save(sec)
        d.log(f"merge: {rid} improvement_of={primary_id}")


def _close_secondaries(d: Daemon, secondaries: list) -> None:
    # 副卡关闭进回收站（可恢复），理由固定写入 trash_reason。
    for rid in secondaries:
        sec = load(rid)
        if sec is None:
            d.log(f"merge: close_secondary {rid} not found — skipped")
            continue
        registry.trash(sec, "merged-review: 不再需要")
        d.log(f"merge: {rid} closed -> trash (merged-review)")


# --------------------------------------------------------------------------- #
# 契约 四 partition（多对多分组）
# --------------------------------------------------------------------------- #
def apply_merge_partition(d: Daemon, job: dict) -> None:
    """契约 四 partition（多对多分组）：逐组复用 :func:`merge_into_primary` —
    每组就是一次现有单-primary 合并（语义逐字一致），单张组 = 保持独立不动。

    动作面复用既有 ``merge_apply``（不新增 inbox 动作）：分组方案与 primary/
    verdict 一样是分析子进程写进作业文件的结论，inbox 只携带建议 id ——app 侧
    没有注入分组的通道，确定性执行的边界与 §21 其余 verdict 完全相同。

    每组执行前重新校验全组成员仍存在且不在终态（done 建议在 24h TTL 内可执行，
    期间用户可能已 trash/合并组员——此时整组跳过留痕，绝不半合）；某组失败不
    阻塞其余组；逐组结果如实写回作业文件 ``group_results``（文件留到 TTL，
    可追查）。

    结果判定（不许把没并上的组吞成"成功"——legacy merge 主卡死亡置可见 failed
    的同一先例，audit 2026-07-15）：全部组 ok/独立 → 正常返回，调用方照旧
    dismiss(applied) + outcome=ok；**任一组 skipped/failed** → 作业经
    mark_failed 变成看板上可见的失败卡（error = 逐组结果汇总，点名哪些组已
    并成——已成的组不回滚也绝不自动重试，用户的后续动作是「仍然合并」或关闭）
    并 raise，调用方按既有失败路径记 outcome=fail、绝不 dismiss。作业的
    groups 坏形/没有 ≥2 张的组 → ValueError（unusable job，调用方按既有路径
    记 outcome=fail、作业留在 done 可重试/取消）。"""
    results = [_partition_group(d, g) for g in _validated_groups(d, job)]
    # honest per-group receipts on the job file itself; on full success the
    # caller's dismiss_job(applied=True) rewrites the same (mutated) dict
    job["group_results"] = results
    _write_group_results(d, job)
    if any(r.get("outcome") in ("skipped", "failed") for r in results):
        # 没并上的组绝不能被吞成"成功"：作业置 failed（可见橙色失败卡），
        # error 汇总逐组结果；raise 让调用方走既有失败路径（outcome=fail、
        # 不 dismiss）。已并成的组如实点名——不回滚、不自动重试。
        summary = partition_results_summary(results)
        if d.merge_review is not None:
            d.merge_review.mark_failed(str(job.get("id") or ""), summary)
        raise RuntimeError(summary)


def _validated_groups(d: Daemon, job: dict) -> list:
    """The job's partition plan, strictly shape-checked; ValueError = unusable job."""
    ids = [str(i) for i in job.get("ids") or []]
    groups = (d.merge_review.validate_groups(job.get("groups"), ids)
              if d.merge_review is not None else None)
    if not groups:
        raise ValueError(
            f"unusable partition job: groups={job.get('groups')!r} ids={ids}")
    return groups


def _partition_group(d: Daemon, g: dict) -> dict:
    """Execute one group → its receipt（independent / skipped / failed / ok）."""
    primary_id = str(g["primary"])
    members = [str(i) for i in g["ids"]]
    entry: dict = {"primary": primary_id, "ids": members}
    if len(members) < 2:
        entry["outcome"] = "independent"   # 单张组：契约语义 = 不动它
        return entry
    stale = _stale_members(members)
    if stale:
        entry["outcome"] = "skipped"
        entry["error"] = "; ".join(stale)[:200]
        d.log(f"merge: partition group primary={primary_id} skipped "
              f"({entry['error']})")
        return entry
    try:
        d.merge_into_primary(primary_id, [i for i in members if i != primary_id])
    except Exception as e:  # noqa: BLE001 - 某组失败不阻塞其余组
        entry["outcome"] = "failed"
        entry["error"] = str(e)[:200]
        d.log(f"merge: partition group primary={primary_id} FAILED: {e}\n"
              f"{traceback.format_exc()}")
        return entry
    entry["outcome"] = "ok"
    d.log(f"merge: partition group primary={primary_id} absorbed "
          f"{len(members) - 1} secondaries")
    return entry


def _stale_members(members: list) -> list:
    stale = []
    for rid in members:
        req = load(rid)
        if req is None:
            stale.append(f"{rid} 已不存在")
        elif str(req.status) in MERGE_DEAD_STATES:
            stale.append(f"{rid} 已不在可合并状态（{req.status}）")
    return stale


def _write_group_results(d: Daemon, job: dict) -> None:
    try:
        if d.merge_review is not None:
            d.merge_review.write_job(job)
    except OSError as e:
        d.log(f"merge: partition group_results write failed (ignored): {e}")


def partition_results_summary(results: list) -> str:
    """把逐组结果拼成一句可读的失败原因（mark_failed 截前 200 字；完整账目
    在作业文件 group_results 里）。已完成的组必须点名——失败卡不会自动重试，
    用户得知道哪些已并、哪些没并。"""
    return "；".join(_group_line(n, r) for n, r in enumerate(results, 1))


def _group_line(n: int, r: dict) -> str:
    prim = r.get("primary")
    outcome = r.get("outcome")
    if outcome == "ok":
        return f"组{n}（主卡 {prim}）已合并"
    if outcome == "independent":
        return f"组{n}（{prim}）保持独立"
    if outcome == "skipped":
        return f"组{n}（主卡 {prim}）跳过：{r.get('error') or ''}"
    return f"组{n}（主卡 {prim}）失败：{r.get('error') or ''}"


# --------------------------------------------------------------------------- #
# 契约 四 merge：主卡吸收副卡
# --------------------------------------------------------------------------- #
def merge_into_primary(d: Daemon, primary_id: str, secondaries: list) -> None:
    """契约 四 merge：主卡 sources 去重合并、repeated_mentions 累加、notes 留痕；
    副卡活 session best-effort 停止、状态置 merged + merged_into；主卡 status==
    review 时用 executor.rework 把副卡交付物/worktree 信息注入其 session（主卡
    回 executing），其他状态只落 notes。"""
    primary = _live_primary(primary_id)
    feedback_lines = [line for line in (_absorb_secondary(d, primary, rid) for rid in secondaries)
                      if line is not None]
    if not feedback_lines:
        return
    if str(primary.status) == State.REVIEW.value and d.executor is not None:
        _inject_rework(d, primary, feedback_lines)
    # 主卡其他状态：notes 已留痕，不动其 session（契约 四）。


def _live_primary(primary_id: str) -> Requirement:
    primary = load(primary_id)
    if primary is None:
        raise ValueError(f"primary {primary_id} not found in registry")
    if str(primary.status) in MERGE_DEAD_STATES:
        # backstop behind the caller-level checks: never absorb live cards
        # into a trashed/merged/archived primary (audit 2026-07-15)
        raise ValueError(
            f"primary {primary_id} is {primary.status} — refusing to merge into a dead card")
    return primary


def _absorb_secondary(d: Daemon, primary: Requirement, rid: str) -> Optional[str]:
    """Fold one secondary into ``primary`` → its feedback line (None = skipped)."""
    sec = load(rid)
    if sec is None:
        d.log(f"merge: secondary {rid} not found — skipped")
        return None
    if str(sec.status) in MERGE_DEAD_STATES:
        # already merged (retry idempotency) or trashed/archived meanwhile —
        # absorbing a sealed card would strip its restorability
        d.log(f"merge: {rid} is {sec.status} — skipped (not a live card)")
        return None
    sec_ex = dict(sec.execution or {})
    summary = _absorb_card(d, primary, sec, sec_ex)
    sec_sid = sec_ex.get("session_id")
    _stop_secondary(d, sec, sec_ex, sec_sid)
    # Persist the primary's absorption BEFORE marking the secondary as
    # merged: retries skip already-merged secondaries, so a crash between
    # the two saves must never leave the absorbed sources/mentions/notes
    # only in memory.
    d.save(primary)
    # 副卡终态（registry State.MERGED，语义见 §21）
    sec.set_status(State.MERGED)
    sec.merged_into = primary.id
    d.save(sec)
    d.log(f"merge: {sec.id} -> merged (into {primary.id})")
    # 主卡待验收时注入的反馈材料：副卡交付物/worktree 路径与摘要
    return _feedback_line(sec, _secondary_worktree(d, sec_sid), summary)


def _feedback_line(sec: Requirement, worktree, summary: str) -> str:
    return (f"{sec.id} 已并入，其交付物/worktree：{worktree or sec.target_repo or '(无)'}；"
            f"摘要：{summary[:300] or '(无)'}")


def _absorb_sources(primary: Requirement, sec: Requirement) -> None:
    """主卡吸收：sources 去重合并、repeated_mentions 累加."""
    merged_sources, _ = registry.dedupe_sources(
        primary.sources or [], sec.sources or [])
    primary.sources = merged_sources
    primary.repeated_mentions = (int(primary.repeated_mentions or 1)
                                 + int(sec.repeated_mentions or 1))


def _secondary_summary(sec: Requirement, sec_ex: dict) -> str:
    return " ".join(
        str(sec_ex.get("delivered_summary") or sec.title or "").split()).strip()


def _absorb_card(d: Daemon, primary: Requirement, sec: Requirement, sec_ex: dict) -> str:
    """sources 去重 / repeated_mentions 累加 / notes 留痕 / 交付物搬运 → summary."""
    _absorb_sources(primary, sec)
    summary = _secondary_summary(sec, sec_ex)
    # §37 review fix: carry the secondary's DISPLAY names into the
    # primary's notes — notes project as searchable notes_text, so a
    # user-named secondary stays findable by its old name after this
    # IRREVERSIBLE merge (merged is terminal; the frozen sec.title alone
    # broke the "旧名仍可搜索" promise exactly here).
    names_part = _former_names_part(sec)
    append_note(primary, f"[merged] {sec.id} 并入：{summary[:200] or '(无摘要)'}{names_part}")
    _carry_deliverable(d, primary, sec, sec_ex)
    return summary


def _former_names_part(sec: Requirement) -> str:
    sec_names = [str(n).strip() for n in
                 ([getattr(sec, "display_title", None)]
                  + list(getattr(sec, "former_titles", None) or []))
                 if n and str(n).strip()]
    return f"（曾用名：{' · '.join(sec_names)}）" if sec_names else ""


def _carry_deliverable(d: Daemon, primary: Requirement, sec: Requirement, sec_ex: dict) -> None:
    """Preserve a delivered secondary's FULL deliverable on the primary.

    MERGED is terminal + UI-unreachable (no un-merge), so a finished
    final_draft / delivered_summary on the secondary would otherwise be
    lost from the UI — the notes breadcrumb is only a ~200-char summary. If
    the secondary carried finished work, carry the full, UNTRUNCATED content
    onto the primary's execution.merged_deliverables list (add-only — never
    touches the primary's OWN delivered_summary/final_draft). At minimum this
    keeps the deliverable verbatim in the primary's registry YAML."""
    if not _has_deliverable(sec_ex):
        return
    prim_ex = dict(primary.execution or {})
    carried = list(prim_ex.get("merged_deliverables") or [])
    carried.append(_deliverable_record(d, sec, sec_ex))
    prim_ex["merged_deliverables"] = carried
    primary.execution = prim_ex
    d.log(f"merge: {sec.id} deliverable carried onto {primary.id} "
          f"(execution.merged_deliverables, n={len(carried)})")


def _has_deliverable(sec_ex: dict) -> bool:
    sec_final = str(sec_ex.get("final_draft") or "").strip()
    sec_delivered = str(sec_ex.get("delivered_summary") or "").strip()
    return bool(sec_final or sec_delivered)


def _deliverable_record(d: Daemon, sec: Requirement, sec_ex: dict) -> dict:
    return {
        "id": sec.id,
        "title": sec.title or "",
        # §37: display names ride along too (same review fix as the
        # notes tag — the deliverable must stay attributable to the name
        # the user knew the card by).
        "display_title": getattr(sec, "display_title", None),
        "former_titles": list(getattr(sec, "former_titles", None) or []) or None,
        "delivered_summary": sec_ex.get("delivered_summary"),
        "final_draft": sec_ex.get("final_draft"),
        "merged_at": d.iso_now(),
    }


def _stop_secondary(d: Daemon, sec: Requirement, sec_ex: dict, sec_sid) -> None:
    # 副卡活 session best-effort 停止（§46 确认式：失败落台账，绝不阻塞合并落账）
    if sec_sid and d.executor is not None:
        d.stop_session_tracked(sec, sec_ex, sec_sid, "merge-stop", log_prefix="merge")
        sec.execution = sec_ex   # 台账字段随副卡一起落盘（下方 save(sec)）


def _secondary_worktree(d: Daemon, sec_sid) -> Optional[Path]:
    if not (sec_sid and d.executor is not None):
        return None
    try:
        return d.executor.transcript_cwd(str(sec_sid))
    except Exception:  # noqa: BLE001 - inference is best-effort
        return None


def _inject_rework(d: Daemon, primary: Requirement, feedback_lines: list) -> None:
    try:
        ok = d.executor.rework(primary, "\n".join(feedback_lines))
        d.log(f"merge: {primary.id} rework injected (ok={ok})")
    except Exception as e:  # noqa: BLE001 - injection is best-effort
        d.log(f"merge: {primary.id} rework failed (ignored): {e}")


# --------------------------------------------------------------------------- #
# (c'') merge-review job housekeeping (§21) — every pass, best-effort
# --------------------------------------------------------------------------- #
def mtime_dt(path: Path) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc)
    except OSError:
        return None


def cleanup_merge_jobs(d: Daemon) -> int:
    """契约 五 actd 每 pass 顺带：state/merge/ 里超过 expires_at 的 done/
    dismissed/failed 作业文件删除；analyzing 超过 20 分钟的置 failed("analysis
    timed out")。缺失/坏 expires_at 用 requested_at（否则文件 mtime）+24h 兜底；
    损坏文件直接删。Returns the number of files removed."""
    if d.merge_review is None:
        return 0
    try:
        files = sorted(d.merge_review.MERGE_DIR.glob("*.json"))
    except OSError:
        return 0
    now = _dt.datetime.now(_dt.timezone.utc)
    removed = 0
    for path in files:
        try:
            removed += _cleanup_job(d, path, now)
        except Exception as e:  # noqa: BLE001 - one bad job must not abort the pass
            d.log(f"merge: cleanup {path.name} failed: {e}")
    return removed


def _read_job(path: Path) -> Optional[dict]:
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return job if isinstance(job, dict) else None


def _cleanup_job(d: Daemon, path: Path, now: _dt.datetime) -> int:
    """One job file → 1 when removed, else 0."""
    job = _read_job(path)
    if job is None:
        d.log(f"merge: corrupt job file {path.name} — removed")
        d.safe_unlink(path)
        return 1
    status = str(job.get("status") or "")
    if status == "analyzing":
        _fail_stuck_analysis(d, path, job, now)
        return 0
    if status in ("done", "dismissed", "failed") and _expired(d, path, job, now):
        d.safe_unlink(path)
        d.log(f"merge: {path.stem} expired ({status}) — removed")
        return 1
    return 0


def _fail_stuck_analysis(d: Daemon, path: Path, job: dict, now: _dt.datetime) -> None:
    started = maintenance.parse_iso(job.get("requested_at")) or mtime_dt(path)
    if started is not None and (
            (now - started).total_seconds() > d.merge_review.ANALYZING_TIMEOUT):
        d.merge_review.mark_failed(str(job.get("id") or path.stem),
                                   "analysis timed out")
        d.log(f"merge: {path.stem} analyzing >20min -> failed (timed out)")


def _expired(d: Daemon, path: Path, job: dict, now: _dt.datetime) -> bool:
    expires = maintenance.parse_iso(job.get("expires_at"))
    if expires is None:
        base = maintenance.parse_iso(job.get("requested_at")) or mtime_dt(path)
        ttl = _dt.timedelta(hours=d.merge_review.TTL_HOURS)
        expires = base + ttl if base is not None else None
    return expires is not None and now > expires
