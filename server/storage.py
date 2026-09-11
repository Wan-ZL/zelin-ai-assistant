"""server/storage.py — 录制数据的磁盘占用与保留期（CONTRACT §71；issue #28）。

「screenpipe 在用户眼里无限长：UI 里既看不见它占了多少盘、也看不见数据留多久，
第一个信号是一个月后磁盘满了。」这一页把三件事搬到设置页「录制」区：

1. **占用**（``usage``）：``~/.screenpipe`` 递归一遍，按类分开——``media``
   （``*.jpg`` / ``*.mp4`` 原始帧与音频片段，被 prune 削的那一半）、``index``
   （``db.sqlite*``：OCR 文本 + 转写，prune 永不碰、也是长期唯一在涨的那一半）、
   ``other``。分开报不是装饰：文本是每天几十 KB，几个 GB 全在媒体上——「媒体删、
   文本永久留」这个多数人真正想要的策略，只有两个数分开才说得清。
2. **增长估计**（``growth``）：每次扫描往 ``state/storage_samples.json`` 追一个
   ``[ts, total]`` 采样（**最多 :data:`SAMPLES_CAP` 条**、彼此至少 1 小时，满了
   丢最旧——防腐 #4：出生即带帽），跨度够 :data:`GROWTH_MIN_SPAN_S` 就按首尾两点
   算 ``bytes_per_month``（``basis: "samples"``）；样本不够就退回「目录建立至今
   的平均」（``basis: "lifetime"``，看 ``st_birthtime``／``st_ctime``）；都不够
   → ``null``（UI 就不说这句，不编）。
3. **保留期 + prune 可观测**：``media_retention_minutes`` 是
   ``recording.media_retention_minutes`` 的三层读（override → config.yaml →
   60），PUT 走与模型旋钮同一套 **diff-write**；``prune`` 是
   ``state/screenpipe_prune.json`` 的投影——``ingest/screenpipe-cleanup.sh``
   每轮写的回执（ts / state / 删了几个文件几个字节）。``stale`` = 距上次回执
   超过 :data:`PRUNE_STALE_S`（cron 链每 30 分钟一轮）：**停掉的 prune 与
   「没东西可删」的 prune 从外面看一模一样**，而前一种会悄悄把盘吃满。

**渲染路径上不许有阻塞 IO**（issue 的验收标准之一，也是 §71 的红线）：``du``
在后台线程里跑（``spawn`` 注入缝，判例绝不起真线程），端点永远立刻返回——
缓存里没有结果就回 ``state:"scanning"`` 并踢一轮扫描，web 轮询到 ``ready``
为止。缓存 :data:`SCAN_TTL_S` 过期后下一次读**先回旧值**再后台刷新（不让用户
盯着 spinner），``?refresh=1`` 强制重扫。扫描自己也有帽：最多
:data:`WALK_FILE_CAP` 个文件，超了如实 ``truncated: true``。

server 不 import act（§49）：默认值 60 与 [5, 365×24×60] 的夹取规则镜像
act/lib/config.py（``DEFAULT_MEDIA_RETENTION_MINUTES`` /
``coerce_retention_minutes``），tests/test_server_paths_mirror.py 钉住不漂。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from server import paths, settings
from server.errors import InvalidFieldError, UnknownFieldError

# ---- act/lib/config.py 的镜像（drift-pinned；§49 server 不 import act） ---- #
DEFAULT_RETENTION_MINUTES = 60
MIN_RETENTION_MINUTES = 5
MAX_RETENTION_MINUTES = 365 * 24 * 60
RETENTION_OVERRIDE_KEY = "recording_media_retention_minutes"
CONFIG_BLOCK = "recording"
CONFIG_FIELD = "media_retention_minutes"

SCAN_TTL_S = 600            # 扫描结果的新鲜期；过期先回旧值再后台刷新
WALK_FILE_CAP = 400_000     # 一次扫描最多看这么多文件（超了 truncated:true）
SAMPLES_CAP = 60            # state/storage_samples.json 的条数帽（防腐 #4）
SAMPLE_MIN_GAP_S = 3600     # 两个采样之间至少一小时
GROWTH_MIN_SPAN_S = 6 * 3600
PRUNE_STALE_S = 3 * 3600    # cron 链每 30 分钟一轮：3 小时没回执 = 停了

MEDIA_SUFFIXES = (".jpg", ".jpeg", ".mp4", ".mov", ".m4a", ".wav")
INDEX_PREFIX = "db.sqlite"

Spawn = Callable[[Callable[[], None]], None]

_lock = threading.Lock()
_cache: dict = {}           # {"usage": dict, "at": float, "scanning": bool}


def _default_spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="zai-storage-scan", daemon=True).start()


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# 保留期旋钮：三层读 + diff-write（与 §59 模型旋钮同一套语义）
# --------------------------------------------------------------------------- #
def coerce_retention(value) -> int:
    """严格路径（PUT）：整数并夹进 [MIN, MAX]；垃圾 → ValueError（400 的原文）。
    夹取而不是报错，与 act/lib/config.coerce_retention_minutes 逐字同规则。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("media_retention_minutes 必须是整数（分钟）"
                         " / media_retention_minutes must be an integer number of minutes")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("media_retention_minutes 必须是整数（分钟）"
                         " / media_retention_minutes must be an integer number of minutes")
    return max(MIN_RETENTION_MINUTES, min(MAX_RETENTION_MINUTES, n))


def _config_retention(home: Path) -> "tuple[int, bool]":
    """(value, present) from config.yaml ``recording.media_retention_minutes``。
    坏值 = 回落默认（与 config._apply_recording 同一条宽松规则）。"""
    blk = settings.config_block(home, CONFIG_BLOCK)
    if CONFIG_FIELD not in blk:
        return DEFAULT_RETENTION_MINUTES, False
    try:
        return coerce_retention(blk.get(CONFIG_FIELD)), True
    except ValueError:
        return DEFAULT_RETENTION_MINUTES, True


def _retention(home: Path) -> "tuple[int, str]":
    value, present = _config_retention(home)
    source = "config" if present else "default"
    raw = settings.read_overrides(home).get(RETENTION_OVERRIDE_KEY)
    if raw is not None:
        try:
            value, source = coerce_retention(raw), "override"
        except ValueError:
            pass  # 管线也是 per-entry 跳过：生效值不变
    return value, source


def update(home: Path, payload: dict) -> dict:
    """``PUT /api/settings/storage {"media_retention_minutes": N}``——
    字段白名单（400 UNKNOWN_FIELD）+ 夹取（400 INVALID_FIELD 带人话）+
    diff-write（等于 config/默认的生效值 → 删键）。回最新 snapshot。"""
    unknown = set(payload) - {CONFIG_FIELD}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if CONFIG_FIELD not in payload:
        raise InvalidFieldError("nothing to save: give media_retention_minutes")
    try:
        wanted = coerce_retention(payload[CONFIG_FIELD])
    except ValueError as exc:
        raise InvalidFieldError(str(exc), {"field": CONFIG_FIELD})
    base, _present = _config_retention(home)
    overrides = settings.read_overrides(home)
    if wanted == base:
        overrides.pop(RETENTION_OVERRIDE_KEY, None)
    else:
        overrides[RETENTION_OVERRIDE_KEY] = wanted
    settings.atomic_write_json(settings.settings_overrides_path(home), overrides)
    return snapshot(home)


# --------------------------------------------------------------------------- #
# prune 回执（ingest/screenpipe-cleanup.sh 每轮写；server 只读）
# --------------------------------------------------------------------------- #
def prune_receipt_path(home: Path) -> Path:
    return home / "state" / "screenpipe_prune.json"


def _prune(home: Path, now: float) -> dict:
    """``{"ran_at", "state", "deleted_files", "deleted_bytes",
    "retention_minutes", "age_seconds", "stale"}``；从没跑过 / 坏文件 →
    ``state:"never"`` + ``stale:true``（看不见的 prune 就是停了的 prune）。"""
    missing = {"ran_at": None, "state": "never", "deleted_files": None,
               "deleted_bytes": None, "retention_minutes": None,
               "age_seconds": None, "stale": True}
    try:
        doc = json.loads(prune_receipt_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return missing
    if not isinstance(doc, dict) or not isinstance(doc.get("ts"), str):
        return missing
    age = None
    try:
        parsed = _dt.datetime.strptime(doc["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
        age = max(0.0, now - parsed.timestamp())
    except ValueError:
        return missing
    state = doc.get("state") if doc.get("state") in ("ok", "no_data_dir", "unreadable") else "unknown"
    return {"ran_at": doc["ts"], "state": state,
            "deleted_files": _int_or_none(doc.get("deleted_files")),
            "deleted_bytes": _int_or_none(doc.get("deleted_bytes")),
            "retention_minutes": _int_or_none(doc.get("retention_minutes")),
            "age_seconds": int(age), "stale": age > PRUNE_STALE_S}


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --------------------------------------------------------------------------- #
# 占用扫描（后台线程 + 缓存；渲染路径零阻塞 IO）
# --------------------------------------------------------------------------- #
def _kind(name: str) -> str:
    if name.startswith(INDEX_PREFIX):
        return "index"
    return "media" if name.lower().endswith(MEDIA_SUFFIXES) else "other"


def walk(root: Path, cap: int = WALK_FILE_CAP) -> dict:
    """``~/.screenpipe`` 递归一遍 → 按类分的字节数 + 文件数。读不到的条目
    （权限 / 半路消失）跳过而不是炸——这是一个只读观测面，宁可少算不可 500。
    符号链接不跟（``followlinks=False`` 是 os.walk 的默认）：录制引擎不造软链，
    跟了只会把同一份数据算两遍、或者顺着一条指向 ``/`` 的链走出宇宙。"""
    started = time.time()
    by_kind = {"media": 0, "index": 0, "other": 0}
    counts = {"media": 0, "index": 0, "other": 0}
    seen, truncated = 0, False
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        for name in filenames:
            if seen >= cap:
                truncated = True
                break
            seen += 1
            kind = _kind(name)
            try:
                size = os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
            by_kind[kind] += size
            counts[kind] += 1
        if truncated:
            break
    by_kind["total"] = by_kind["media"] + by_kind["index"] + by_kind["other"]
    return {"state": "ready", "bytes": by_kind, "files": counts,
            "truncated": truncated, "scanned_at": _iso(started),
            "scan_seconds": round(time.time() - started, 2)}


def _scan(root: Path) -> dict:
    if not root.exists():
        return {"state": "missing", "bytes": None, "files": None,
                "truncated": False, "scanned_at": _iso(time.time()),
                "scan_seconds": 0.0}
    try:
        return walk(root)
    except OSError as exc:      # 目录在但整棵树读不了（TCC）：如实报，不 500
        return {"state": "error", "error": str(exc), "bytes": None, "files": None,
                "truncated": False, "scanned_at": _iso(time.time()),
                "scan_seconds": 0.0}


def _run_scan(home: Path, root: Path) -> None:
    usage = _scan(root)
    if usage["state"] == "ready":
        _record_sample(home, usage["bytes"]["total"], time.time())
    with _lock:
        _cache.update({"usage": usage, "at": time.time(), "scanning": False})


def _start_scan(home: Path, root: Path, spawn: Spawn) -> None:
    with _lock:
        if _cache.get("scanning"):
            return
        _cache["scanning"] = True
    try:
        spawn(lambda: _run_scan(home, root))
    except Exception:           # noqa: BLE001 — 起不来线程也不能让读面 500
        with _lock:
            _cache["scanning"] = False
        raise


def _read_cache(ttl_from: float) -> "tuple[Optional[dict], bool, bool]":
    """(结果, 还新鲜吗, 后台在扫吗) —— 一次持锁读完，调用方不再碰 ``_cache``。"""
    with _lock:
        cached, at = _cache.get("usage"), _cache.get("at", 0.0)
        scanning = bool(_cache.get("scanning"))
    return cached, cached is not None and (ttl_from - at) < SCAN_TTL_S, scanning


def _pending_usage(root: Path, scanning: bool) -> dict:
    """还没有任何结果时的形（web 据此轮询）。"""
    return {"state": "scanning", "dir": str(root), "bytes": None, "files": None,
            "truncated": False, "scanned_at": None, "scan_seconds": None,
            "stale": False, "scanning": scanning}


def usage_snapshot(home: Path, *, refresh: bool = False,
                   spawn: Optional[Spawn] = None) -> dict:
    """缓存里的占用 + 需要时踢一轮后台扫描。**永不在请求线程里走目录树。**
    还没有任何结果 → ``state:"scanning"``（web 轮询）；有旧结果但过期 →
    先回旧的（带 ``stale:true``）再后台刷新。"""
    root = paths.screenpipe_dir()
    cached, fresh, scanning = _read_cache(time.time())
    if refresh:
        fresh = False
    if not fresh and not scanning:
        _start_scan(home, root, spawn or _default_spawn)
        # 再读一次：spawn 可能是同步的（判例的注入缝），那一轮已经把结果放进来了
        cached, fresh, scanning = _read_cache(time.time())
    if cached is None:
        return _pending_usage(root, scanning)
    return dict(cached, dir=str(root), stale=not fresh, scanning=scanning)


# --------------------------------------------------------------------------- #
# 增长估计（采样台账，出生即带帽）
# --------------------------------------------------------------------------- #
def samples_path(home: Path) -> Path:
    return home / "state" / "storage_samples.json"


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_sample(row) -> bool:
    """一条采样的形：``[ts, total_bytes]``，两个都是真数字（bool 不算数）。"""
    return (isinstance(row, list) and len(row) == 2
            and _is_number(row[0]) and _is_number(row[1]))


def read_samples(home: Path) -> list:
    """台账里形状对的那些行（坏行整行丢；坏文件 = 空台账，永不 raise）。"""
    try:
        doc = json.loads(samples_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("samples") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    return [[float(r[0]), int(r[1])] for r in rows if _valid_sample(r)][-SAMPLES_CAP:]


def _record_sample(home: Path, total: int, now: float) -> None:
    """一条 ``[ts, total_bytes]``；离上一条不足 :data:`SAMPLE_MIN_GAP_S` 就
    **改写**最后一条而不是追加（页面刷十次不该挤掉十天的历史）。写失败静默——
    观测面不许因为写不了台账而让读面失败。"""
    rows = read_samples(home)
    if rows and now - rows[-1][0] < SAMPLE_MIN_GAP_S:
        rows[-1] = [now, total]
    else:
        rows.append([now, total])
    rows = rows[-SAMPLES_CAP:]
    try:
        settings.atomic_write_json(samples_path(home), {"samples": rows})
    except OSError:
        pass


def _lifetime_growth(root: Path, total: int, now: float) -> Optional[dict]:
    """采样不够时的退路：目录建立至今的平均。``st_birthtime`` 在 macOS 上就是
    创建时间，Linux 上退回 ``st_ctime``（够用——两者都只是个起点）。"""
    try:
        st = root.stat()
    except OSError:
        return None
    born = getattr(st, "st_birthtime", None) or st.st_ctime
    days = (now - born) / 86400.0
    if days < 1.0 or total <= 0:
        return None
    return {"basis": "lifetime", "days": round(days, 1),
            "bytes_per_month": int(total / days * 30)}


def _samples_growth(rows: list) -> Optional[dict]:
    """采样法：只用首尾两点——中间的 prune 波动不该被当成趋势，两端之间涨了
    多少就是这段时间真实的净增。跨度不够 → None（让调用方退回 lifetime）。
    净增为负（用户刚缩短了保留期）算 0，不报负增长吓人。"""
    if len(rows) < 2:
        return None
    span = rows[-1][0] - rows[0][0]
    if span < GROWTH_MIN_SPAN_S:
        return None
    span_days = span / 86400.0
    delta = max(0, rows[-1][1] - rows[0][1])
    return {"basis": "samples", "days": round(span_days, 1),
            "bytes_per_month": int(delta / span_days * 30)}


def growth(home: Path, usage: dict, now: Optional[float] = None) -> Optional[dict]:
    """``{"basis": "samples"|"lifetime", "days": float, "bytes_per_month": int}``
    或 ``None``（还说不出来——UI 就不说这句）。"""
    if usage.get("state") != "ready" or not isinstance(usage.get("bytes"), dict):
        return None
    from_samples = _samples_growth(read_samples(home))
    if from_samples is not None:
        return from_samples
    total = int(usage["bytes"].get("total") or 0)
    return _lifetime_growth(paths.screenpipe_dir(), total,
                            time.time() if now is None else now)


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #
def snapshot(home: Path, *, refresh: bool = False,
             spawn: Optional[Spawn] = None) -> dict:
    """``GET /api/settings/storage`` 的 wire 形（web/src/types.ts
    ``StorageSettings`` 逐字镜像）::

        {"media_retention_minutes": int, "source": "override|config|default",
         "bounds": {"min": int, "max": int, "default": int},
         "usage": {...}, "growth": {...} | null, "prune": {...}}
    """
    value, source = _retention(home)
    usage = usage_snapshot(home, refresh=refresh, spawn=spawn)
    return {"media_retention_minutes": value, "source": source,
            "bounds": {"min": MIN_RETENTION_MINUTES, "max": MAX_RETENTION_MINUTES,
                       "default": DEFAULT_RETENTION_MINUTES},
            "usage": usage, "growth": growth(home, usage),
            "prune": _prune(home, time.time())}


def reset_cache() -> None:
    """判例用：清掉进程内的扫描缓存（server 重启即清，不是账本）。"""
    with _lock:
        _cache.clear()
