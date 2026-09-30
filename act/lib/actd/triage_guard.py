"""triage_guard — 高权限会话的 registry 写入护栏（起止快照比对）。

契约：CONTRACT §34bis（机械护栏本体）/ §34（``mode:"run"`` 直跑卡）/ §78
（提案车道退役：护栏改锚）。

**§34bis 提案积压清理按钮 retired v-next（并入 §78，owner decision D80.11）**：
按钮住在提案泳道头上，那一列随 §78 删了；注入固定 prompt 的 ``preset`` 词表
（``proposals_triage``）连同 ``proposals_triage_plan`` / ``proposals_triage_in_flight``
一起退役。卡片字段 ``preset`` 本身保留（add-only：存量卡仍读得出）。

**护栏机械本体不退役**，改锚在 owner 的**直跑卡**（§34 ``mode:"run"``）上——
那才是真正危险的那一类：owner 一句话起跑、没有 plan 预览、会话带
``--dangerously-skip-permissions`` 且拿得到 REGISTRY_DIR 绝对路径。dispatch
前拍 registry 快照落 state/triage_snapshots/（卡上只留引用
``execution.registry_snapshot_ref``），收割提升时比对起止快照，排除管线合法
写入后仍有差异 = 疑似会话越权 → notes 警告 + notify，交人工核查。只告警不
回滚、绝不阻塞提升（宪法第 11 条）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, notify, registry
from act.lib.actd.seam import Daemon, append_note
from act.lib.registry import State, load_all

# §34bis（retired v-next，并入 §78）preset 词表键。按钮与固定 plan 都已删，
# 常量留着：存量卡的 `preset` 字段仍写着它，护栏照样认（见 guarded_card）。
PROPOSALS_TRIAGE_PRESET = "proposals_triage"


def guarded_card(req) -> bool:
    """本卡起跑前要不要拍 registry 快照（§34bis 护栏的认卡判据）。

    真源 = ``execution.direct_run``（§34 直跑卡出生时盖的 add-only 痕）：
    owner 在「运行中」框里打的一句话没有 plan 预览、没有审批闸，会话物理上
    写得进 registry——正是护栏要盯的那一类。退役的 §34bis preset 卡一并认
    （存量在途卡不能在退役当天失去护栏）。"""
    ex = req.execution if isinstance(getattr(req, "execution", None), dict) else {}
    return bool(ex.get("direct_run")) or getattr(req, "preset", None) == PROPOSALS_TRIAGE_PRESET


def registry_snapshot() -> dict:
    """§34bis 机械护栏起点：registry 快照（backend-aware，键形恒 <id>.yaml；
    yaml = size:mtime_ns，sqlite = v<version>——见 registry.guard_snapshot）。"""
    try:
        return registry.guard_snapshot()
    except Exception:  # noqa: BLE001 - 护栏快照失败绝不崩 pass（宪法 11）
        return {}


def triage_snapshot_path(req_id: str) -> Path:
    """快照落 state/ 侧文件——全 registry 清单写进卡 YAML 会让卡膨胀且
    用户在看板/编辑器里直接看见一坨账本；execution 只留 add-only 引用
    ``registry_snapshot_ref``。"""
    return config.STATE_DIR / "triage_snapshots" / f"{req_id}.json"


def stamp_triage_snapshot(d: Daemon, req_id: str) -> Optional[str]:
    """§34bis 机械护栏起点：拍快照落 state 文件，返回引用路径（失败 None）。"""
    path = triage_snapshot_path(req_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"at": d.iso_now(),
                                   "files": registry_snapshot()},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return str(path)
    except OSError as e:
        d.log(f"guard: snapshot stamp failed for {req_id}: {e}")
        return None


def _read_snapshot(d: Daemon, req, ref) -> object:
    """Read the snapshot payload and consume the file（用后即焚：一轮只比对一次）."""
    snap_path = Path(str(ref))
    try:
        return json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        d.log(f"guard: snapshot unreadable for {req.id}: {e}")
        return None
    finally:
        try:
            snap_path.unlink(missing_ok=True)   # 快照随本轮消费
        except OSError:
            pass


def _snapshot_parts(payload) -> tuple:
    """payload → (files dict | None, at str)."""
    if not isinstance(payload, dict):
        return None, ""
    return payload.get("files"), str(payload.get("at", ""))


def _suspicious_names(req, snap: dict, at: str) -> list:
    """Files that changed between the two snapshots and are neither the
    pipeline's own writes since ``at`` nor this card's file."""
    now_snap = registry_snapshot()
    ours = registry.writes_since(at)
    own = {f"{req.id}.yaml"}               # 本卡自身随收割必然变动
    return sorted(
        name for name in set(snap) | set(now_snap)
        if name not in ours and name not in own
        and snap.get(name) != now_snap.get(name))


def _flag_guard(d: Daemon, req, suspicious: list) -> None:
    shown = ", ".join(suspicious[:5]) + ("…" if len(suspicious) > 5 else "")
    append_note(req, f"[§34bis 护栏] 清理会话期间 registry 出现非 actd 写入：{shown}"
                     " —— 会话按律只读，请核查")
    notify.notify(*notify.msg_registry_guard(req.title or req.id, shown),
                  req=req.id, kind=notify.KIND_FAILURE)
    analytics.log_event("triage_registry_guard", req=req.id,
                        files=len(suspicious))
    d.log(f"guard: {req.id} registry snapshot mismatch: {shown}")


def check_triage_registry_guard(d: Daemon, req, ex: dict) -> None:
    """§34bis 机械护栏终点：收割提升待验收时做起止快照比对（检测型）。

    prompt 级的只读约束终究只是文字——直跑会话带
    --dangerously-skip-permissions 且拿到 REGISTRY_DIR 绝对路径，物理上
    写得进。这里比对 dispatch 时留在 state/triage_snapshots/ 的快照
    （execution.registry_snapshot_ref 引用）：排除管线的合法写入
    （registry.writes_since(快照 ts)——跨进程持久台账，radar 独立进程的
    落卡也在账上）与本卡自身文件后仍有差异 = 疑似会话越权 → 卡 notes 记
    警告 + notify 告警，交人工核查。只告警不回滚、绝不阻塞提升（宪法第
    11 条：检测失败不许崩 pass）；权限模型不变。
    """
    ref = ex.pop("registry_snapshot_ref", None)   # 用后即焚：一轮只比对一次
    if not ref:
        return
    try:
        snap, at = _snapshot_parts(_read_snapshot(d, req, ref))
        if not isinstance(snap, dict) or not at:
            return
        suspicious = _suspicious_names(req, snap, at)
        if suspicious:
            _flag_guard(d, req, suspicious)
    except Exception as e:  # noqa: BLE001 - 护栏自身故障绝不阻塞收割
        d.log(f"guard: registry snapshot check failed for {req.id}: {e}")


_SNAPSHOT_LIVE = (State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value)


def sweep_triage_snapshots(d: Daemon) -> None:
    """§34bis 快照残留清扫：卡没走到收割就离场（executing 中被 abort/trash、
    done_external 直落 delivered）时，state/triage_snapshots/ 的侧文件没人
    消费。存活判据 = 对应卡（文件名 stem = R-id）仍在 approved/executing/
    review——起跑前预拍的快照卡还是 approved，天然受保护；review 在列因为
    attach 复活轮会重拍快照（reconcile_review_attach），等复活轮收割消费；
    其余一律删（再开新一轮会重拍）。每 pass 一次，目录为空时零开销。"""
    root = config.STATE_DIR / "triage_snapshots"
    try:
        files = list(root.glob("*.json"))
    except OSError:
        return
    if not files:
        return
    live = {req.id for req in load_all() if str(req.status) in _SNAPSHOT_LIVE}
    for p in files:
        if p.stem not in live:
            d.safe_unlink(p)
