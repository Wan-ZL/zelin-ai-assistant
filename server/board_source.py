"""看板数据源：/api/board 透传 + /api/cards/{id} 详情增补。

- GET /api/board = ``state/dashboard.json`` 原样透传（bytes 级，零改写）。
- GET /api/cards/{id} = 投影行 + registry 真源只读增补（add-only 合并，
  绝不覆盖投影字段名）。

真源路由（§53，v0.48.8）：store2 激活标记在 → 从 SQLite 读 payload（经
act/lib/store2/readonly.py 的 ``mode=ro`` 只读面，物理上不可写）；否则走
YAML 目录。registry 只读纪律（§44 单写者）不变：不 import act.lib.registry
（它带 save/archive 写路径）。YAML 侧用 PyYAML safe_load 复刻其文件布局知识：
- 文件 = 单卡 dict 或 list（debt 批次 R-002..R-006 是一个 list 文件）；
- ``R-000-example.yaml`` 是文档样例，永不加载（registry._iter_files 同款）；
- crash-mid-move 残留时 archive/ 副本 authoritative（registry.load 判例），
  所以查找顺序 = archive/ 先于 active。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

# PyYAML 在运行时白名单内（stdlib + PyYAML），但缺席时只降级 registry 增补
# （/api/board 透传与投影行详情照常）——server 不因可选增补面拒绝启动。
try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺 PyYAML 的降级路径
    yaml = None  # type: ignore[assignment]

# store2 只读面（§53）：缺席（部分安装形态）只降级 YAML 路径，不拒启动
try:
    from act.lib.store2 import readonly as store2_readonly
except Exception:  # pragma: no cover - 降级路径
    store2_readonly = None  # type: ignore[assignment]

from server import paths
from server.errors import InvalidFieldError, NotFoundError

# webui.py _SAFE_ID_RE 同款保守 allow-list：无 ``.``/``/``/NUL，长度封顶——
# id 直接参与 ``{id}.yaml`` 文件名拼接，必须防穿越。
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# 投影分区词表（dashboard.py §2 + v0.20 archived）——卡片可能出现的所有 lane
SECTIONS = ("needs_approval", "running", "needs_input", "review",
            "completed", "debt", "trash", "archived")

_EXAMPLE_FILE = "R-000-example.yaml"


# --------------------------------------------------------------------------- #
# /api/board —— 原样透传
# --------------------------------------------------------------------------- #
def board_bytes(home: Path) -> bytes:
    p = paths.dashboard_path(home)
    try:
        return p.read_bytes()
    except OSError:
        raise NotFoundError("dashboard.json not found — is actd (or the demo "
                            "seeder) pointed at this AIASSISTANT_HOME?",
                            {"path": str(p)})


def _board_dict(home: Path) -> dict:
    try:
        doc = json.loads(board_bytes(home).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


# --------------------------------------------------------------------------- #
# registry 只读加载
# --------------------------------------------------------------------------- #
def _load_yaml(p: Path):
    """safe_load 一个 registry 文件；任何读/解析失败 → None（对齐
    registry.load_all 的 skip-unreadable 语义，绝不崩 request）。"""
    if yaml is None:
        return None
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _match_card(doc, card_id: str) -> Optional[dict]:
    if isinstance(doc, dict) and str(doc.get("id")) == card_id:
        return doc
    if isinstance(doc, list):
        for item in doc:
            if isinstance(item, dict) and str(item.get("id")) == card_id:
                return item
    return None


def _registry_dirs(home: Path) -> Iterable[Path]:
    # archive 优先（crash 残留时 archive 副本 authoritative，见模块注释）
    return (paths.archive_dir(home), paths.registry_dir(home))


def load_registry_card(home: Path, card_id: str) -> Optional[dict]:
    """按 id 找卡：store2 激活时读 SQLite payload（§53 真源；标记在时**不**
    回落 YAML——那只是迁移冻结件，回落等于把旧数据当真相）；否则先按
    canonical 文件名 ``<ID>.yaml`` 直取（§1），找不到再全量扫描（list 批次
    文件 / 带 slug 的历史文件名）。"""
    if store2_readonly is not None and paths.store2_truth_path(home).exists():
        db = paths.store2_db_path(home)
        if db.exists():
            return store2_readonly.read_card(db, card_id)
        return None
    for d in _registry_dirs(home):
        hit = _match_card(_load_yaml(d / f"{card_id}.yaml"), card_id)
        if hit is not None:
            return hit
    for d in _registry_dirs(home):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.yaml")):
            if p.name == _EXAMPLE_FILE:
                continue
            hit = _match_card(_load_yaml(p), card_id)
            if hit is not None:
                return hit
    return None


# --------------------------------------------------------------------------- #
# /api/cards/{id} —— 投影行 + YAML 增补
# --------------------------------------------------------------------------- #
def _projection_row(board: dict, card_id: str) -> "tuple[Optional[str], Optional[dict]]":
    for sec in SECTIONS:
        for row in board.get(sec) or []:
            if isinstance(row, dict) and row.get("id") == card_id:
                return sec, row
    return None, None


def is_executing(home: Path, card_id: str) -> bool:
    """卡是否「正在执行」= running 分区的非 queued 行，或 needs_input 分区行。

    steer 判定用（M6，docs/design/vnext-amendments.md §M6.1）：executing
    卡上的 owner comment 会被 actd 按 steer 类经 §44.3 briefing 机制转投递给
    活会话——app.py 据此在 POST /api/actions 响应里做 add-only 标注（inbox
    文件本体仍是 §3 comment 原形，一个字段都不加）。只读投影，任何异常按
    False 兜底（fail-safe：宁可漏标 steer，不误标）。
    """
    try:
        lane, row = _projection_row(_board_dict(home), card_id)
    except Exception:
        return False
    if lane == "needs_input":
        # §4 刹车卡（dispatch_halted）也投影在这一列，但它是 approved 且无
        # 会话——comment 只能折进 notes，标 steer 会是假回执。
        return not (isinstance(row, dict) and row.get("dispatch_halted"))
    return (lane == "running" and isinstance(row, dict)
            and row.get("state") != "queued")


def card_detail(home: Path, card_id: str) -> dict:
    """详情 = 投影行字段（原样） + ``lane``（所在分区名） + YAML 的其余字段
    （plan/definition_of_done/sources/notes/execution/…，add-only：投影已有
    的键绝不覆盖）。投影与 registry 都查无此卡 → 404。"""
    if not SAFE_ID_RE.match(card_id or ""):
        raise InvalidFieldError("invalid card id", {"id": card_id})
    lane, row = _projection_row(_board_dict(home), card_id)
    reg = load_registry_card(home, card_id)
    if row is None and reg is None:
        raise NotFoundError("card not found", {"id": card_id})

    merged: dict = dict(row) if row else {"id": card_id}
    # ``lane`` 是本 endpoint 的新增键（不动投影字段名）；registry-only 卡为 null
    merged["lane"] = lane
    for k, v in (reg or {}).items():
        if k not in merged:
            merged[k] = v
    return merged
