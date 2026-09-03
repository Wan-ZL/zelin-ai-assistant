"""housekeeping — the per-pass sweeps that keep the registry and state/ tidy.

CONTRACT §9（trash retention purge；§70 loop-trashed rows get a longer window
via maintenance.purge_due）/ §4 + #10（auto-archive cold delivered matters；
v-next W1.c 默认 30 天，设 0 关闭）/ §10 v0.46 追记（贴图附件 GC：
state/attachments/ + state/feedback/attachments/ 只写不删会按 5-15MB/张无限增长，
这里删「无引用且 mtime>30 天」的孤儿，引用不可见就不删）。
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path
from typing import Optional

import yaml

from act.lib import config, maintenance, registry
from act.lib.actd.seam import Daemon
from act.lib.registry import Requirement, State, load_all


# --------------------------------------------------------------------------- #
# (c') trash retention purge (CONTRACT §9)
# --------------------------------------------------------------------------- #
def purge_trash(d: Daemon, cfg: config.Config) -> int:
    """Hard-delete trashed items past their retention window (§9; skips
    ``permanent``; ``retention_days <= 0`` disables). §70: loop-trashed rows
    get a longer window — maintenance.purge_due is the one judge shared with
    the §40.5 countdown. A single bad item never aborts the pass."""
    if not maintenance.purge_enabled(cfg):
        return 0
    now = _dt.datetime.now(_dt.timezone.utc)
    purged = 0
    for req in load_all():
        try:
            purged += purge_one(d, req, cfg, now)
        except Exception as e:  # noqa: BLE001 - one bad item must not abort the pass
            d.log(f"trash: purge failed for {getattr(req, 'id', '?')}: {e}")
    return purged


def purge_one(d: Daemon, req: Requirement, cfg: config.Config, now: _dt.datetime) -> int:
    if not maintenance.purge_due(req, cfg, now) or not registry.delete(req):
        return 0
    d.log(f"trash: purged {req.id} (trashed_at={req.trashed_at})")
    return 1


# --------------------------------------------------------------------------- #
# (c') auto-archive stale delivered matters (卡片生命周期 §4 / #10；
#      v-next W1.c：默认 30 天，设 0 关闭)
# --------------------------------------------------------------------------- #
ARCHIVE_SWEEP_MARKER = "last_archive_sweep"
OPEN_STATES = (
    State.DETECTED.value, State.RAISING.value, State.CARD_SENT.value,
    State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value,
)


def _swept_within_last_24h() -> bool:
    """Daily gate: the auto-archive sweep runs at most once per 24h."""
    try:
        p = config.STATE_DIR / ARCHIVE_SWEEP_MARKER
        if not p.exists():
            return False
        age = _dt.datetime.now(_dt.timezone.utc).timestamp() - p.stat().st_mtime
        return age < 24 * 3600
    except OSError:
        return False


def _mark_swept(d: Daemon) -> None:
    try:
        config.ensure_state_dirs()
        (config.STATE_DIR / ARCHIVE_SWEEP_MARKER).write_text(
            d.iso_now(), encoding="utf-8")
    except OSError:
        pass


def has_future_deadline(req: Requirement) -> bool:
    """A delivered card with a deadline still in the future (USCIS/长 matter
    里程碑) must NOT be auto-sealed — new mail on it would open a dup card."""
    if not req.deadline:
        return False
    try:
        d = _dt.date.fromisoformat(str(req.deadline))
    except ValueError:
        return False
    return d >= _dt.date.today()


def _same_cluster(req: Requirement, r: Requirement, thread) -> bool:
    return ((r.thread_id or r.id) == thread
            or r.improvement_of == req.id
            or req.improvement_of == r.id)


def _open_sibling(req: Requirement, r: Requirement, thread) -> bool:
    return (r.id != req.id and _same_cluster(req, r, thread)
            and str(r.status) in OPEN_STATES)


def cluster_has_live_sibling(req: Requirement, all_reqs: list) -> bool:
    """True if any OTHER card in this thread/lineage cluster is still open —
    never seal a matter that still has live work attached."""
    thread = req.thread_id or req.id
    return any(_open_sibling(req, r, thread) for r in all_reqs)


def thread_last_activity(req: Requirement) -> Optional[_dt.datetime]:
    """Newest activity timestamp for the card (cross-dep; legacy fallback =
    accepted_at). None when nothing is parseable — then the card is never
    auto-archived (conservative: ambiguous cards are left alone)."""
    ex = req.execution if isinstance(req.execution, dict) else {}
    cands = (ex.get("accepted_at"), ex.get("approved_at"),
             ex.get("dispatched_at"), ex.get("review_at"),
             ex.get("reraised_at"))
    dts = [d for d in (maintenance.parse_iso(c) for c in cands) if d is not None]
    return max(dts) if dts else None


def archive_stale(d: Daemon, cfg: config.Config) -> int:
    """Auto-archive cold DELIVERED cards (§4 / #10; v-next W1.c 改默认值).

    ``archive_after_days`` 默认 30（W1.c；设 0 关闭）——W1.a 配额反转后冷卡
    挤占 closed recency 槽位（20 个），30 天冷封存把窗口留给近期 closed 卡。
    长静默的 immigration/EB-1A 里程碑由未来 deadline 保护罩住（新邮件到来
    绝不撞上被封存的卡开出重复卡——那正是本功能要杀的 bug）。At most once
    per 24h; skips cards with a future deadline, a live sibling in their
    cluster, or unparseable timestamps."""
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
            n += _archive_if_cold(d, req, reqs, cutoff)
        except Exception as e:  # noqa: BLE001 - one bad item must not abort the pass
            d.log(f"archive: auto-archive failed for {getattr(req, 'id', '?')}: {e}")
    _mark_swept(d)
    return n


def _archive_if_cold(d: Daemon, req: Requirement, reqs: list, cutoff: _dt.datetime) -> int:
    if req.status != State.DELIVERED.value:
        return 0
    if has_future_deadline(req):
        return 0
    if cluster_has_live_sibling(req, reqs):
        return 0
    last = thread_last_activity(req)
    if last is None or last >= cutoff:
        return 0
    registry.archive(req, reason="auto")
    d.log(f"archive: auto-archived {req.id} (last activity {last.isoformat()})")
    return 1


# --------------------------------------------------------------------------- #
# 贴图附件 GC (建议 #4/#5) — state/attachments/ + state/feedback/attachments/
# 只写不删会按 5-15MB/张无限增长；这里删「无引用且 mtime>30 天」的孤儿。
# 引用源 = 全部 registry 卡（含 trash 状态与 archive/——归档卡是真实工作数据）
# 的 execution.attachments + state/feedback/*.json 的 images。fail safe：引用
# 扫描不完整（坏 yaml / 坏 feedback 记录）就按 sweep_attachment_dirs 文档的
# 口径缩范围或整体零删除。
# --------------------------------------------------------------------------- #
ATTACH_GC_MARKER = config.STATE_DIR / "attachments_gc_marker"
ATTACH_GC_INTERVAL_S = 24 * 3600        # daily, marker-throttled (update_check 模式)
ATTACH_GC_MAX_AGE_S = 30 * 24 * 3600    # young orphans get 30 天 grace


def _add_clean_paths(refs: set, items) -> None:
    """A list of path strings → refs（strip 后非空的字符串；非 list 忽略）."""
    if isinstance(items, list):
        refs.update(p.strip() for p in items
                    if isinstance(p, str) and p.strip())


def _collect_attachment_refs(refs: set, ex) -> None:
    """execution.attachments（list of str）→ refs."""
    _add_clean_paths(refs, ex.get("attachments") if isinstance(ex, dict) else None)


def _registry_yaml_files() -> list:
    reg_files = [p for p in config.REGISTRY_DIR.glob("*.yaml")
                 if p.name != "R-000-example.yaml"]
    if registry.ARCHIVE_DIR.exists():
        reg_files += list(registry.ARCHIVE_DIR.glob("*.yaml"))
    return reg_files


def _yaml_docs(path: Path) -> list:
    """single-doc 与 list 文件都认；坏 yaml 直接 raise（fail safe 由调用方兜）."""
    docs = yaml.safe_load(path.read_text(encoding="utf-8"))
    return docs if isinstance(docs, list) else [docs]


def _sqlite_attachment_refs() -> set:
    # store2 真源：payload 是 DB 级校验过的 JSON（json_valid CHECK），
    # 读取失败会 raise —— 与 yaml 侧「引用不可见就整轮零删除」同一 fail-safe
    refs: set = set()
    for req in registry.load_all(include_archived=True):
        _collect_attachment_refs(refs, req.execution)
    return refs


def _yaml_attachment_refs() -> set:
    refs: set = set()
    for path in _registry_yaml_files():
        for doc in _yaml_docs(path):
            _collect_attachment_refs(refs, doc.get("execution") if isinstance(doc, dict) else None)
    return refs


def registry_attachment_refs() -> set:
    """引用收集（registry 侧）——逐文件 STRICT 解析（single-doc 与 list 文件
    都认，archive/ 一并扫，R-000-example.yaml 照 _iter_files 规则跳过）。
    刻意不用 registry.load_all：它对单个坏文件是静默跳过，坏卡引用的 >30 天
    附图会被当孤儿删掉——这里任一 yaml 读不出/解析失败都直接 raise，让本
    pass 整体零删除（fail safe：引用不可见就不删）。"""
    if registry.backend() == registry.BACKEND_SQLITE:
        return _sqlite_attachment_refs()
    return _yaml_attachment_refs()


def _read_record(rec_path: Path) -> Optional[dict]:
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _collect_feedback_refs(d: Daemon, refs: set) -> bool:
    """Add feedback image refs; False when any record was unreadable (or the
    feedback module degraded) — then the feedback attachments dir is off-limits."""
    # feedback 模块可降级为 None（守护导入）——此时它的 images 引用整体不可见，
    # 与坏记录同款处理：本 pass 不动 feedback 附件目录。
    if d.feedback is None:
        return False
    dir_ok = True
    for rec_path in d.feedback.FEEDBACK_DIR.glob("*.json"):
        rec = _read_record(rec_path)
        if rec is None:
            # 这条记录的 images 引用不可见 —— 本 pass 不动 feedback 目录
            dir_ok = False
            continue
        _add_clean_paths(refs, rec.get("images"))
    return dir_ok


def _orphan_past_grace(f: Path, refs: set, now: float) -> bool:
    if not f.is_file():
        return False
    if str(f) in refs or str(f.resolve()) in refs:
        return False
    # young orphan: in-flight inbox actions get time
    return now - f.stat().st_mtime >= ATTACH_GC_MAX_AGE_S


def _sweep_dir(directory: Path, refs: set, now: float) -> int:
    if not directory.is_dir():
        return 0
    removed = 0
    for f in directory.iterdir():
        try:
            if _orphan_past_grace(f, refs, now):
                f.unlink()
                removed += 1
        except OSError:
            continue   # one bad file must not stop the sweep
    return removed


def sweep_attachment_dirs(d: Daemon, now: Optional[float] = None) -> int:
    """Delete unreferenced attachment files older than 30 days; returns the
    number removed.

    Fail-safe 口径（契约 §10 v0.46 追记）——引用不可见就不删：
    - registry 侧任一 yaml 坏形 -> registry_attachment_refs raises，the
      throttled wrapper turns it into a logged no-op（本 pass 整体零删除）；
    - feedback 侧任一记录读不出（IO / 坏 JSON / 非 dict）-> 跳过
      state/feedback/attachments/ 的清扫；state/attachments/ 不受影响
      （feedback 的 images 只落自己的目录，capture/answer 只落另一边）。
    """
    now = time.time() if now is None else now
    refs = registry_attachment_refs()
    feedback_dir_ok = _collect_feedback_refs(d, refs)
    # tolerate symlinked homes: compare both the recorded string and realpath
    refs |= {str(Path(p).resolve()) for p in list(refs)}
    dirs = [config.STATE_DIR / "attachments"]
    if feedback_dir_ok:
        dirs.append(config.STATE_DIR / "feedback" / "attachments")
    else:
        d.log("attachments gc: unreadable feedback record — skipping the "
              "feedback attachments dir this pass")
    return sum(_sweep_dir(directory, refs, now) for directory in dirs)


def _gc_due() -> bool:
    try:
        return time.time() - ATTACH_GC_MARKER.stat().st_mtime >= ATTACH_GC_INTERVAL_S
    except OSError:
        return True   # no marker yet -> run


def gc_attachments(d: Daemon) -> int:
    """Daily-throttled orphan sweep (marker-file mtime — update_check's 24h
    budget pattern; the attempt consumes the budget, success or not). Returns
    files removed (0 when throttled or on failure). Never raises."""
    try:
        if not _gc_due():
            return 0
        ATTACH_GC_MARKER.parent.mkdir(parents=True, exist_ok=True)
        ATTACH_GC_MARKER.touch()
        removed = sweep_attachment_dirs(d)
        if removed:
            d.log(f"attachments gc: removed {removed} orphaned file(s)")
        return removed
    except Exception as e:  # noqa: BLE001 - GC must never kill the pass
        d.log(f"attachments gc FAILED: {e}")
        return 0
