"""server/screenpipe_disk.py — 录制数据磁盘占用快照（CONTRACT §72.1；路由 §49 ``GET /api/screenpipe/disk``）。

issue #28：screenpipe 的数据在用户眼里无界增长——界面上没有任何地方说它占了多少盘、留多久，第一个信号是一个月后
磁盘满。本模块给设置页「录制数据与磁盘」区一份**永不阻塞渲染路径**的快照：

- ``snapshot(home)`` 立刻返回缓存（首次为 ``state: "computing"`` 的空壳），过期（``CACHE_TTL_S``）或
  ``?refresh=1`` 时在后台线程重算（``os.walk`` ``~/.screenpipe`` 累加 ``st_size`` + 只读打开 db.sqlite 问
  ``freelist_count`` / 最早最晚 frame）；同一时刻最多一个后台算——GET 路径上零磁盘 IO、零 sqlite。
- 增长估算「约 X GB / 月」：server 每次算完在 ``state/screenpipe_disk_samples.json`` 记一条
  ``[ts, total_bytes]``（间隔 ≥ ``SAMPLE_MIN_GAP_S``，容量 ``SAMPLE_CAP`` 条 —— 防腐 #4 出生即带帽），
  最近 ``SAMPLE_WINDOW_S`` 内首末样本跨度 ≥ 1 天才给斜率（``basis: "samples"``）；样本不够就退到
  「db 字节 ÷ 最早 frame 至今的天数」（``basis: "lifetime"``）；都没有 = ``null`` 并说明（§0 第 3 条：
  不虚报数字，估算的依据随数字一起投影）。
- 备份文件（``db.sqlite.bak*``）单列——owner 机器上一份 6/4 的 33 GB 旧备份是总占用的大头；只报路径与大小，
  **不提供删除按钮**（§0 第 2 条：不可恢复的删除留给用户在访达里亲手做）。
- ``retention_days`` = 目录 storage 区的 effective 值；``last_prune`` = act/lib/screenpipe_retention.py 的回执
  ``state/screenpipe_retention.json`` 原样投影（缺席 = null）。
- **§72.4 媒体那一半**：``media_retention_minutes`` = 同一区的 effective 值（cron 链的 ``find -mmin``），
  ``media_prune`` = ``ingest/screenpipe-cleanup.sh`` 每轮写的回执 ``state/screenpipe_prune.json`` 的投影——
  原样字段 + server 算的 ``age_seconds``（上一次尝试的岁数）/ ``ok_age_seconds`` / ``stale``
  （按回执里的 ``last_ok_ts`` = **上次干净跑完**算，> ``PRUNE_STALE_S``；链本该 30 分钟一轮）。回执缺席 / 坏形
  = ``state: "never"`` + ``stale: true``：**停掉的清理与「没东西可删」的清理从外面看一模一样**，而前一种会一直涨盘
  （§0 第 3 条：宁可报「没跑过」也不报一次干净的空转）。按 ``last_ok_ts`` 而不是 ``ts`` 算新鲜度，是因为
  每 30 分钟失败一次的清理会把 ``ts`` 一直刷新——按它算就永远「刚跑过」，而没有一个文件被删掉。

server/ 不 import act（§49）：两个回执文件名（act 的 ``RECEIPT_NAME`` / 脚本的 ``screenpipe_prune.json``）与
媒体保留期的出厂值都是手抄，判例 tests/test_server_screenpipe_disk.py、tests/test_screenpipe_media_prune_projection.py 钉。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import stat as _stat
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from server import background_jobs, paths, settings_catalog

CACHE_TTL_S = 600.0
SAMPLE_MIN_GAP_S = 6 * 3600.0
SAMPLE_CAP = 240                    # ≈ 60 天 × 4 条/天
SAMPLE_WINDOW_S = 30 * 86400.0
MONTH_S = 30 * 86400.0
DB_TIMEOUT_S = 2.0
RECEIPT_NAME = "screenpipe_retention.json"       # act/lib/screenpipe_retention.RECEIPT_NAME 的镜像
PRUNE_RECEIPT_NAME = "screenpipe_prune.json"     # ingest/screenpipe-cleanup.sh 的媒体回执（§72.4，逐字镜像）
PRUNE_STALE_S = 3 * 3600.0                       # 链每 30 分钟一轮；超过这个岁数 = 清理停了（§72.4）
MEDIA_RETENTION_DEFAULT = 60                     # act/lib/config.DEFAULT_MEDIA_RETENTION_MINUTES 的手抄（server 不 import act，§49）
SAMPLES_NAME = "screenpipe_disk_samples.json"
BACKUP_LIST_CAP = 5

_lock = threading.Lock()
_cache: dict = {}    # str(home) -> {"snapshot": dict, "computed_at": float, "inflight": bool}


# --------------------------------------------------------------------------- #
# 目录扫描
# --------------------------------------------------------------------------- #
def _is_db(name: str, _top: str) -> bool:
    return name in ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm")


def _is_backup(name: str, _top: str) -> bool:
    return name.startswith("db.sqlite.bak") or name.endswith(".bak")


def _is_log(name: str, _top: str) -> bool:
    return name.endswith(".log")


def _is_media(_name: str, top: str) -> bool:
    return top == "data"


# 归类顺序即优先级；(判据(name, 顶层目录名), kind)
_KINDS = ((_is_db, "db"), (_is_backup, "backup"), (_is_log, "log"), (_is_media, "media"))


def classify(rel: str, name: str) -> str:
    """文件归类：db（db.sqlite 及 -wal / -shm）/ backup（db.sqlite.bak*、*.bak）/ log / media（data/ 下）/ other。"""
    top = rel.split(os.sep, 1)[0]
    for pred, kind in _KINDS:
        if pred(name, top):
            return kind
    return "other"


def scan(root: Path) -> dict:
    """``os.walk`` 累加 ``lstat().st_size``（不跟符号链接；读不到的条目跳过）。"""
    sizes = {"db": 0, "backup": 0, "log": 0, "media": 0, "other": 0}
    backups: list = []
    files = 0
    for dirpath, _dirs, names in os.walk(str(root), onerror=lambda _e: None):
        rel = _rel_dir(dirpath, str(root))
        for name in names:
            files += _tally(sizes, backups, rel, name, _lstat_size(os.path.join(dirpath, name)))
    backups.sort(key=lambda b: -b["bytes"])
    return {"sizes": sizes, "total_bytes": sum(sizes.values()), "file_count": files,
            "backups": backups[:BACKUP_LIST_CAP]}


def _rel_dir(dirpath: str, root: str) -> str:
    rel = os.path.relpath(dirpath, root)
    return "" if rel == "." else rel


def _tally(sizes: dict, backups: list, rel: str, name: str, size: Optional[int]) -> int:
    """一个文件记进 sizes（按 kind）与 backups（备份才记）；返回 1 = 计入文件数，0 = 读不到跳过。"""
    if size is None:
        return 0
    kind = classify(rel, name)
    sizes[kind] += size
    if kind == "backup":
        backups.append({"name": os.path.join(rel, name), "bytes": size})
    return 1


def _lstat_size(path: str) -> Optional[int]:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return None if _stat.S_ISLNK(st.st_mode) else int(st.st_size)


# --------------------------------------------------------------------------- #
# db.sqlite 只读问询
# --------------------------------------------------------------------------- #
def db_stats(db: Path) -> dict:
    """只读 URI 打开：可复用页字节（freelist）+ 最早 / 最晚 frame 时间戳；任何失败进 ``db_error``（不虚报）。"""
    out = {"db_reclaimable_bytes": None, "oldest_frame_ts": None, "newest_frame_ts": None, "db_error": None}
    if not db.is_file():
        out["db_error"] = "no_db"
        return out
    try:
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=DB_TIMEOUT_S)
    except sqlite3.Error as exc:
        out["db_error"] = str(exc)
        return out
    try:
        page = int(conn.execute("PRAGMA page_size").fetchone()[0])
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        oldest, newest = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM frames").fetchone()
        out.update({"db_reclaimable_bytes": page * free, "oldest_frame_ts": oldest, "newest_frame_ts": newest})
    except sqlite3.Error as exc:
        out["db_error"] = str(exc)
    finally:
        conn.close()
    return out


# --------------------------------------------------------------------------- #
# 增长估算（样本优先，全程平均兜底）
# --------------------------------------------------------------------------- #
def samples_path(home: Path) -> Path:
    return home / "state" / SAMPLES_NAME


def load_samples(path: Path) -> list:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, list):
        return []
    return [[float(s[0]), int(s[1])] for s in doc if isinstance(s, list) and len(s) == 2]


def append_sample(samples: list, now: float, total_bytes: int) -> list:
    """间隔不足 ``SAMPLE_MIN_GAP_S`` 不记；超 ``SAMPLE_CAP`` 掐掉最旧的。"""
    if samples and now - samples[-1][0] < SAMPLE_MIN_GAP_S:
        return samples
    return (samples + [[float(now), int(total_bytes)]])[-SAMPLE_CAP:]


def save_samples(path: Path, samples: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(samples), encoding="utf-8")
    os.replace(tmp, path)


def _parse_ts(raw) -> Optional[float]:
    """frames.timestamp 的 ISO 字串 → epoch；坏形 None（``Z`` 与空格分隔符都收，naive 按 UTC）。"""
    text = raw.strip().replace(" ", "T").replace("Z", "+00:00") if isinstance(raw, str) else ""
    if not text:
        return None
    try:
        stamp = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    return stamp.timestamp()


def estimate(samples: list, now: float, db_bytes: int, oldest_ts) -> dict:
    """``{bytes_per_month, basis, span_days, samples}``：窗口内首末样本跨度 ≥ 1 天 → 斜率；否则 db 字节 ÷ 录制天数。"""
    recent = [s for s in samples if now - s[0] <= SAMPLE_WINDOW_S]
    out = {"bytes_per_month": None, "basis": None, "span_days": None, "samples": len(recent)}
    out.update(_slope_estimate(recent) or _lifetime_estimate(now, db_bytes, oldest_ts) or {})
    return out


def _slope_estimate(recent: list) -> Optional[dict]:
    """首末样本跨度 ≥ 1 天才可信；不够 → None。"""
    span = recent[-1][0] - recent[0][0] if len(recent) >= 2 else 0.0
    if span < 86400.0:
        return None
    return {"bytes_per_month": int(round((recent[-1][1] - recent[0][1]) * MONTH_S / span)),
            "basis": "samples", "span_days": round(span / 86400.0, 1)}


def _lifetime_estimate(now: float, db_bytes: int, oldest_ts) -> Optional[dict]:
    """db 字节 ÷ 最早 frame 至今的天数（≥ 1 天且有字节才给）。"""
    since = _parse_ts(oldest_ts)
    span = now - since if since is not None else 0.0
    if span < 86400.0 or db_bytes <= 0:
        return None
    return {"bytes_per_month": int(round(db_bytes * MONTH_S / span)), "basis": "lifetime",
            "span_days": round(span / 86400.0, 1)}


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def _iso(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_prune(home: Path) -> Optional[dict]:
    try:
        doc = json.loads((home / "state" / RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def retention_days(home: Path) -> int:
    try:
        return int(settings_catalog.effective_value(home, "storage", "screenpipe_retention_days") or 0)
    except Exception:  # noqa: BLE001 — 目录读不出来不该让磁盘快照整个失败
        return 0


def media_retention_minutes(home: Path) -> int:
    """§72.4 媒体保留分钟数的 effective 值（目录 storage 区；读不出来 = 出厂 60）。"""
    try:
        return int(settings_catalog.effective_value(home, "storage", "screenpipe_media_retention_minutes")
                   or MEDIA_RETENTION_DEFAULT)
    except Exception:  # noqa: BLE001 — 同上：目录的毛病不该炸掉整张快照
        return MEDIA_RETENTION_DEFAULT


def _prune_doc(home: Path) -> Optional[dict]:
    try:
        doc = json.loads((home / "state" / PRUNE_RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def media_prune(home: Path, now: float) -> dict:
    """§72.4 媒体清理回执的投影：脚本写的字段原样带出，另算 ``age_seconds`` / ``ok_age_seconds`` / ``stale``。

    回执缺席 / 坏 JSON / 坏时间戳 → ``state: "never"`` + ``stale: true``：**「清理停了」与「没东西可删」
    从外面看一模一样**，而前一种会一直涨盘——所以宁可报「没跑过」也不报一次干净的空转（宪法第 3 条）。
    脚本自己的 ``unreadable``（目录在但进不去）/ ``partial``（没扫完）也各是独立的 state，同样不与
    「删了 0 个」混为一谈。

    ``stale`` 按**上次干净跑完**（``last_ok_ts``）算，不按上一次尝试（``ts``）算：每 30 分钟失败一次的
    清理会把 ``ts`` 一直刷新，按它算就永远「新鲜」，而盘一直在涨。``age_seconds`` 仍是上一次尝试的岁数。"""
    doc = _prune_doc(home)
    out = {"state": "never", "ts": None, "retention_minutes": None, "deleted_files": None,
           "deleted_bytes": None, "data_dir": None, "last_ok_ts": None,
           "age_seconds": None, "ok_age_seconds": None, "stale": True}
    if doc is None:
        return out
    out.update({key: doc.get(key) for key in ("state", "ts", "retention_minutes", "deleted_files",
                                              "deleted_bytes", "data_dir", "last_ok_ts")})
    if not isinstance(out["state"], str) or not out["state"]:
        out["state"] = "never"
    out["last_ok_ts"] = _last_ok_ts(out)
    out["age_seconds"] = _age_seconds(out["ts"], now)
    ok_age = _age_seconds(out["last_ok_ts"], now)
    out["ok_age_seconds"] = ok_age
    out["stale"] = ok_age is None or ok_age > PRUNE_STALE_S
    return out


def _last_ok_ts(out: dict) -> Optional[str]:
    """上次**干净跑完**的时刻：回执里的 ``last_ok_ts``（脚本每个 ok 轮次刷新、其余轮次原样带下去）。
    升级前写的回执没有这一键——它自己是 ``ok`` 的话，它的 ``ts`` 就是一次成功；其余 state
    （``unreadable`` / ``partial`` / ``no_data_dir``）无从得知，按「没成功过」算：不知道就别报新鲜（宪法第 3 条）。"""
    raw = out.get("last_ok_ts")
    if isinstance(raw, str) and raw.strip():
        return raw
    return out["ts"] if out["state"] == "ok" and isinstance(out["ts"], str) else None


def _age_seconds(ts, now: float) -> Optional[float]:
    """回执时间戳到现在多少秒；坏形 / 缺席 = None（= 当成过期）。未来时间戳按 0（时钟跳变不算新鲜到负）。"""
    stamp = _parse_ts(ts)
    return None if stamp is None else max(0.0, round(now - stamp, 3))


def compute(home: Path, root: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """同步算一份完整快照（后台线程 / 判例直接调）。副作用：样本文件追加一条。"""
    now = time.time() if now is None else now
    root = Path(root) if root is not None else paths.screenpipe_dir()
    scanned = scan(root) if root.is_dir() else {"sizes": {"db": 0, "backup": 0, "log": 0, "media": 0, "other": 0},
                                                "total_bytes": 0, "file_count": 0, "backups": []}
    stats = db_stats(root / "db.sqlite")
    samples = append_sample(load_samples(samples_path(home)), now, scanned["total_bytes"])
    try:
        save_samples(samples_path(home), samples)
    except OSError:
        pass
    sizes = scanned["sizes"]
    return {"state": "ready", "computed_at": _iso(now), "root": str(root), "root_exists": root.is_dir(),
            "total_bytes": scanned["total_bytes"], "db_bytes": sizes["db"], "backup_bytes": sizes["backup"],
            "log_bytes": sizes["log"], "media_bytes": sizes["media"], "other_bytes": sizes["other"],
            "file_count": scanned["file_count"], "backups": scanned["backups"],
            **stats,
            "growth": estimate(samples, now, sizes["db"], stats["oldest_frame_ts"])}


def _placeholder(root: Path) -> dict:
    return {"state": "computing", "computed_at": None, "root": str(root), "root_exists": root.is_dir(),
            "total_bytes": None, "db_bytes": None, "backup_bytes": None, "log_bytes": None, "media_bytes": None,
            "other_bytes": None, "file_count": None, "backups": [], "db_reclaimable_bytes": None,
            "oldest_frame_ts": None, "newest_frame_ts": None, "db_error": None,
            "growth": {"bytes_per_month": None, "basis": None, "span_days": None, "samples": 0}}


_THREADS = background_jobs.Threads("screenpipe-disk")


def _spawn_thread(fn: Callable[[], None]) -> None:
    _THREADS.spawn(fn)


def _finish(key: str, result: dict, now: float) -> None:
    with _lock:
        entry = _cache.setdefault(key, {})
        entry.update({"snapshot": result, "computed_at": now, "inflight": False})


def _job(home: Path, key: str, now: float) -> None:
    """后台算一份；``now`` = 调度那一刻的时钟（缓存新鲜度按它算——判例可注入）。"""
    try:
        result = compute(home, now=now)
    except Exception as exc:  # noqa: BLE001 — 后台线程里的任何失败都要落成 state=error，别让 inflight 卡死
        result = {**_placeholder(paths.screenpipe_dir()), "state": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
    _finish(key, result, now)


def _claim(key: str, refresh: bool, now: float) -> "tuple[bool, Optional[dict], bool]":
    """持锁判一次：要不要起后台算（过期 / refresh 且没在算）；返回 (start, 缓存快照或 None, 此刻是否在算)。"""
    with _lock:
        entry = _cache.setdefault(key, {"snapshot": None, "computed_at": 0.0, "inflight": False})
        stale = entry["snapshot"] is None or refresh or now - entry["computed_at"] >= CACHE_TTL_S
        start = stale and not entry["inflight"]
        if start:
            entry["inflight"] = True
        return start, entry["snapshot"], entry["inflight"]


def snapshot(home: Path, *, refresh: bool = False, now: Optional[float] = None,
             spawn: Callable[[Callable[[], None]], None] = _spawn_thread) -> dict:
    """GET 路径：返回缓存（首次 = computing 空壳）；过期 / refresh 且没有在算 → 起一个后台算。
    这里不扫目录、不开 sqlite——只读两个小 JSON（目录 effective 值 + 上次清理回执），与其它设置 GET 同量级。"""
    now = time.time() if now is None else now
    key = str(home)
    start, cached, inflight = _claim(key, refresh, now)
    if start:
        spawn(lambda: _job(home, key, now))
    base = dict(cached) if cached is not None else _placeholder(paths.screenpipe_dir())
    base.update({"refreshing": inflight, "retention_days": retention_days(home), "last_prune": last_prune(home),
                 "media_retention_minutes": media_retention_minutes(home), "media_prune": media_prune(home, now)})
    return base


def join_jobs_for_tests(timeout: float = background_jobs.JOIN_TIMEOUT_S) -> bool:
    """§72.1 的测试缝：等在飞的后台算落地（有界；``False`` = 到点还有活的）。"""
    return _THREADS.join(timeout)


def reset_cache_for_tests() -> None:
    """清场 = **先等在飞的后台算落地**再清缓存。不等的话 `_job` 会在判例
    `tempfile.TemporaryDirectory` 的 `rmtree` 脚下往 `state/` 里写样本文件
    （CI 2026-09-15：``OSError: [Errno 39] Directory not empty: 'state'``）；
    先 join 后清也保证晚到的 `_finish` 不会把缓存再填回去。判例要把这一下的
    ``addCleanup`` 排在临时目录那一下**之后**登记（cleanup 是 LIFO）。"""
    join_jobs_for_tests()
    with _lock:
        _cache.clear()
