"""fold_receipts — 静默并入的看板回执台账（CONTRACT §44.6）.

2026-08-07 拍板：radar / 普通 capture 通道的静默并入（fold）保留，但并入发生时
看板必须给「已并入 R-xxx」回执——不能再有"卡片转圈后消失、文本不知去向"的
黑洞体验（8-07 事故：两条 [run] 输入被静默并入正在执行的卡，看板零回执）。

机制（复用 §28 notify_queue 的 one-file-per-entry 形制，天然免并发写竞态——
radar cron 与 actd 都可能是 fold 的执行者）：

- fold 执行点调用 :func:`record` → ``state/fold_receipts/<key>.json`` 原子落
  一条 ``{"id","req","channel","at"}``；写入前顺手清扫过期兄弟条目（同 notify
  的 stale sweep，目录不会无界增长）。
- **隐私红线（P0）**：回执文件与投影**永不携带被并入内容原文**——dashboard.json
  会被 syncd 整包上云同步，capture 原话（可能含密钥/本机路径）不得出机。回执
  只存 channel + 目标卡 id；投影文案由目标卡的 display_title（本就在
  dashboard 里）拼成，见 :func:`act.lib.dashboard._fold_receipts`。
- **内容键去重**：``id`` = sha1(channel|req|条目指纹) 前 32 位（条目指纹 =
  被并入内容的规范化文本，只参与散列、不落盘）。radar 的 failed-note 重试队列
  会对同一条目反复 re-fold——TTL 窗口内同键已有回执时不重发（否则用户每轮
  重试都看到一条假"刚刚并入"）；过期清扫后同键可再发。
- dashboard 投影（actd 单写者）经 :func:`load_recent` 读回 TTL 内的条目，
  作为 add-only 顶层键 ``fold_receipts`` 发给 App；Swift 端渲染为一行可消失
  的 info 提示（LocalNotice 机制复用）。
- 回执是尽力而为的观测面：record/load 任何失败都绝不打断 fold 本身
  （宪法第 11 条——失败不外溢）。

[run] 通道（capture mode:"run"）与本台账无关：§34 修订后它彻底不做判重并入，
一律新卡直接开跑——新卡出现在运行中列本身就是回执。

**§44.6 追记（issue #308）：回执只属于用户自己投进来的东西。**

- **用户通道闸**：`record` 只对 `policy.CHANNEL_CLASS` 判为 HAND 的通道
  （quick / quick_capture）落回执；radar / daily_loop 这类自动通道的并入
  一条不写（它们的可见面 = 目标卡的 §38 折叠记录 + §44.5「已并入×N」章 +
  §70 每日整理横幅）。词表不另立第二张表——单源就是 policy 那张。
  `load_recent` 读时再滤一次，旧 actd 已经落盘的自动通道回执也不投影。
- **按目标卡合簇**：同一张卡在 TTL 窗口内被并入多次只出一行，`count` =
  簇内条目数（add-only 字段），`id`/`at` 取簇内最新的那条；`PROJECTION_CAP`
  自此数的是簇不是条目。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

# 回执存活窗口：App 5s 刷新一次、通知 10 分钟 stale（§28 同款）——足够用户
# 看到一次；过期即从投影消失（"短暂 notice"的产品语义）。
TTL_S = 600.0
# 投影上限：防御性 cap（正常一个窗口内不会有 10 条 fold）。
PROJECTION_CAP = 10


def _dir() -> Path:
    from act.lib import config
    return Path(config.FOLD_RECEIPTS_DIR)


def _content_key(req_id: str, channel: str, note: str) -> str:
    """回执内容键：channel + 目标卡 + 条目指纹 → 确定性 id（去重身份）。

    ``note``（被并入内容）只进散列——绝不写盘（隐私红线）。空白折叠后散列，
    radar 重试队列里同一条目的逐字重放必然同键。
    """
    fp = " ".join(str(note or "").split())
    raw = f"{channel}|{req_id}|{fp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _is_user_channel(channel: object) -> bool:
    """这条并入是**用户自己投进来的东西**被折走吗？（§44.6 追记，issue #308）

    判据单源 = :data:`act.lib.policy.CHANNEL_CLASS` 的 HAND 类（quick /
    quick_capture）——不另立第二张白名单表（防腐十条第 9 条）。表里没有的
    通道（radar / daily_loop / meeting / slack …）一律 fail-closed 落
    EXTERNAL，即「不是用户刚才敲的」，不出回执。policy 出问题也不打断
    fold：判不了就当不是用户通道（宪法 11，回执是尽力而为的观测面）。
    """
    try:
        from act.lib import policy
        return policy.channel_class(channel) == policy.HAND
    except Exception:  # noqa: BLE001
        return False


def record(req_id: str, channel: str, note: str = "",
           now: Optional[float] = None) -> Optional[Path]:
    """落一条并入回执（原子写 .tmp→rename + 过期清扫 + 内容键去重）。绝不 raise。

    ``req_id`` = 并入目标（主卡）；``channel`` = 触发并入的入库通道
    （quick_capture / quick / radar）；``note`` = 被并入内容——**只用于
    内容键散列，永不落盘**（隐私红线：dashboard 会整包上云）。

    ``channel`` 不是用户通道（:func:`_is_user_channel`）→ 直接 None：不建
    目录、不写文件、不清扫。所有 fold 执行点照旧无条件调本函数，闸只有
    这一处（§44.6 追记，issue #308）。

    TTL 窗口内同键已有回执 → 返回既有文件、不重写（不刷新 ``at``，Swift
    seen-set 的 id 不变 → 不重复弹提示）。
    """
    if not _is_user_channel(channel):
        return None
    try:
        qdir = _dir()
        qdir.mkdir(parents=True, exist_ok=True)
        _sweep_stale(qdir, now=now)
        rid = _content_key(str(req_id or ""), str(channel or ""), note)
        target = qdir / (rid + ".json")
        if target.exists():
            # 内容键去重：清扫后还在 = TTL 内同键已回执过（radar 重试重放）。
            return target
        _write_entry(qdir, rid, _entry(rid, req_id, channel, now))
        return target
    except Exception:  # noqa: BLE001 - 回执绝不打断 fold 本身（宪法 11）
        return None


def _entry(rid: str, req_id, channel, now: Optional[float]) -> dict:
    return {
        "id": rid,
        "req": str(req_id or ""),
        "channel": str(channel or ""),
        "at": int(now if now is not None else time.time()),
    }


def _write_entry(qdir: Path, rid: str, entry: dict) -> None:
    """原子写 .tmp→rename；rename 失败不留尸体（同 notify）。"""
    tmp = qdir / (rid + ".json.tmp")
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, qdir / (rid + ".json"))
    finally:
        tmp.unlink(missing_ok=True)


def load_recent(now: Optional[float] = None) -> list[dict]:
    """TTL 内的回执，按目标卡合簇后按 ``at`` 降序、cap :data:`PROJECTION_CAP`
    **簇**。绝不 raise。

    坏文件/坏形状直接跳过（dashboard 投影的"损坏条目跳过"既有约定）。旧格式
    文件多出的 ``title``/``text`` 字段一律忽略（向后兼容 + 隐私红线：原文
    即便躺在旧盘面上也不再进投影）。非用户通道的条目也在这里滤掉——旧 actd
    落盘的 radar/daily_loop 回执不该在升级后还弹（§44.6 追记，issue #308）。
    """
    cutoff = (now if now is not None else time.time()) - TTL_S
    try:
        paths = list(_dir().glob("*.json"))
    except OSError:
        return []
    rows = [row for row in (_recent_row(p, cutoff) for p in paths)
            if row is not None and _is_user_channel(row["channel"])]
    out = _group_by_target(rows)
    out.sort(key=lambda e: (e["at"], e["id"]), reverse=True)
    return out[:PROJECTION_CAP]


def _group_by_target(rows: list) -> list:
    """按目标卡合簇（§44.6 追记，issue #308）：同一张卡在 TTL 窗口内被并入
    多次只出一行——「已并入 P-008」平铺四条是 issue #308 的第二宗罪。

    簇代表 = 簇内最新的那条（``id``/``at``/``channel`` 跟着它走，前端 seen-set
    的键因此仍是一条真实存在的回执 id）；add-only ``count`` = 簇内条目数。
    先按 ``(at, id)`` 排序再折叠，结果与 glob 的目录序无关（确定性）。
    """
    groups: dict = {}
    for row in sorted(rows, key=lambda e: (e["at"], e["id"])):
        cur = groups.get(row["req"])
        if cur is None:
            groups[row["req"]] = dict(row, count=1)
        else:
            cur.update(id=row["id"], at=row["at"], channel=row["channel"],
                       count=cur["count"] + 1)
    return list(groups.values())


def _read_entry(p: Path) -> Optional[dict]:
    """文件 → dict；读不了 / 不是 JSON / 不是 dict → None。"""
    try:
        entry = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def _entry_at(entry: Optional[dict]) -> Optional[int]:
    """``at`` 字段 → int；缺条目 / 非数 → None。"""
    if entry is None:
        return None
    try:
        return int(entry.get("at") or 0)
    except (TypeError, ValueError):
        return None


def _projected(p: Path, entry: dict, at: int) -> dict:
    return {
        "id": str(entry.get("id") or p.stem),
        "req": str(entry.get("req")),
        "channel": str(entry.get("channel") or ""),
        "at": at,
    }


def _recent_row(p: Path, cutoff: float) -> Optional[dict]:
    """一个回执文件 → 投影行；坏文件 / 过期 / 无目标卡 → None。"""
    entry = _read_entry(p)
    at = _entry_at(entry)
    if at is None or at < cutoff or not str(entry.get("req") or ""):
        return None
    return _projected(p, entry, at)


def _unlink_if_stale(f: Path, cutoff: float) -> int:
    """mtime 超 TTL 即删；stat/unlink 失败按没删（best-effort）。"""
    try:
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            return 1
    except OSError:
        pass
    return 0


def _sweep_stale(qdir: Path, now: Optional[float] = None) -> int:
    """清掉过期条目（mtime 超 TTL）。best-effort，绝不 raise。"""
    removed = 0
    cutoff = (now if now is not None else time.time()) - TTL_S
    try:
        for f in qdir.iterdir():
            removed += _unlink_if_stale(f, cutoff)
    except OSError:
        pass
    return removed
