"""Usage analytics — append-only event log for every feature use.

CONTRACT §16（features.analytics 本地 gate，隐私 fail-closed）/ §15（telemetry
内容字段：content_gate + clip_content 无条件密钥掩码）。

One JSONL line per event in ``state/analytics/events.jsonl``:
    {"ts": "2026-07-06T23:01:02Z", "event": "inbox_approve", "req": "R-004", ...}

``python -m act.report`` aggregates: per-feature frequency (7d/30d), hour-of-day
and day-of-week heat, health signals (rework rate = unclear proposals, resume
failures = ineffective repetition, approval latency), and repetition storms.

Analytics must NEVER break the pipeline — every failure here is swallowed.
"""
from __future__ import annotations

import datetime as _dt
import json
import re as _re
import time as _time
from pathlib import Path
from typing import Iterator, Optional

from act import __version__
from act.lib import config, secret_patterns

ANALYTICS_DIR: Path = config.STATE_DIR / "analytics"
EVENTS_PATH: Path = ANALYTICS_DIR / "events.jsonl"
# Once-per-install milestone markers (docs/TELEMETRY.md 生命周期里程碑): one
# empty file per milestone name under here suppresses every later log_first for
# that milestone. Python counterpart of the Swift Analytics.firstReach marker
# (mac/Sources/Utils.swift), which uses a UserDefaults flag for the same job.
FIRST_DIR: Path = ANALYTICS_DIR / "first"

# Hard cap for every user-typed content field (docs/TELEMETRY.md「输入文本
# 收集」): capture text / Ask questions / card comments / instruction
# summaries all pass through clip(text, CONTENT_CLIP). Model OUTPUT and
# ingested third-party content (screen OCR / emails / Slack
# messages, tests/test_telemetry_level.py boundary guard) are never captured
# at any setting — only what the user typed into this app.
CONTENT_CLIP: int = 500

# v2 consent-surface marker (CONTRACT §15 v0.18): written by the app the
# first time the NEW disclosure renders. Historical role: under the v0.18
# default-ON regime it stood in for consent. Since v0.48 capture_input
# defaults OFF (opt-in) and the marker alone NEVER arms content — only the
# explicit capture_input key (first-run checkbox / Settings toggle /
# config.yaml) does. The marker keeps being written as a record that the
# disclosure surface was shown (the v1 marker still gates ALL uploads in
# telemetry_upload, unchanged).
CONSENT_V2_PATH: Path = config.STATE_DIR / "telemetry_consent_shown_v2"


def content_gate(cfg=None) -> bool:
    """Emit-side gate for user-typed content fields (docs/TELEMETRY.md).

    ALL required (v0.48 opt-in revision, CONTRACT §15):
    1. telemetry.capture_input on AND level "detailed"
       (Config.capture_input_active — capture_input defaults OFF);
    2. consent: capture_input was set EXPLICITLY (first-run checkbox /
       Settings toggle / config.yaml — writing the key is the informed
       choice; the v2 disclosure marker alone no longer opens the gate);
    3. nothing crashed — any failure means False (fail closed).

    Only text the user typed into THIS app may sit behind this gate — never
    pipeline/ingested content. Loads config lazily so no-cfg call sites
    (actd inbox helpers) can use it.
    """
    try:
        cfg = cfg or config.load_config()
        if not cfg.capture_input_active():
            return False
        return bool(getattr(cfg, "telemetry_capture_input_explicit", False))
    except Exception:  # noqa: BLE001 - fail closed, never break the pipeline
        return False


def _collapse_ws(text) -> str:
    """Whitespace-collapsed str of any value (None → "")."""
    return " ".join(str(text or "").split())


def clip_content(text) -> Optional[str]:
    """clip() for user-typed CONTENT fields: secret-mask FIRST, then cap at
    CONTENT_CLIP. The masking (act/lib/secret_patterns.SECRET_PATTERNS) is
    UNCONDITIONAL — independent of every redaction.* switch — because the
    docs promise keys never ride in telemetry at any setting (the Swift
    writer mirrors the same patterns in Analytics.clip). Fail closed: if
    masking itself breaks, the content is dropped, never sent raw.
    """
    s = _collapse_ws(text)
    if not s:
        return None
    try:
        positions = secret_patterns.secret_positions(s)
        if positions:
            s = secret_patterns.mask_positions(s, positions)
    except Exception:  # noqa: BLE001 - never emit unmasked content
        return None
    return s[:CONTENT_CLIP] or None


# feature_gate 的进程内短缓存：radar/actd 循环逐事件 emit，不该每条都付一次
# config parse。缓存以两份配置源的 mtime+size 指纹为键——配置文件一变（含
# Settings 的原子写 temp+rename），下一条事件就重判，「关闭后 TTL 内照记」的
# 盲窗不存在；TTL 只是指纹失灵（罕见文件系统）时的兜底。每事件的常态开销
# = 两次 stat(2)。测试用 reset_feature_gate_cache() 保证判例之间互不串味。
GATE_TTL: float = 5.0
_gate_cache: Optional[tuple] = None  # (monotonic 过期时刻, bool 结果, 源指纹)


def reset_feature_gate_cache() -> None:
    """清空 gate 缓存（测试注入缝；生产无人调用也无妨）。"""
    global _gate_cache
    _gate_cache = None


def _sources_fingerprint() -> tuple:
    """两份配置源的 (mtime_ns, size) 指纹；文件不存在记 None。

    指纹一变即视为配置可能已改（缓存作废）；stat 失败按 None 处理——与
    「文件消失」同款，宁可多算一次也不给出陈旧的 gate 结果。"""
    fp = []
    for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
        try:
            st = p.stat()
            fp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            fp.append(None)
    return tuple(fp)


_MISSING = object()   # 「键不存在」哨兵（None 是合法的 yaml 值，不能兼任）


class _CorruptSource(Exception):
    """配置源存在但读不懂（无解析器 / 顶层不是 mapping）——隐私 fail-closed 用。"""


def _yaml_source():
    """config.yaml 的顶层 mapping；文件不存在 → None；存在但读不懂 → raise
    （PyYAML 缺失、顶层不是 mapping、yaml/OS 错误——调用方一律按损坏处理）。"""
    if not config.CONFIG_PATH.exists():
        return None
    if config.yaml is None:  # 文件在场却无解析器 = 损坏同款
        raise _CorruptSource("config.yaml present but no yaml parser")
    loaded = config.yaml.safe_load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    if loaded is not None and not isinstance(loaded, dict):
        raise _CorruptSource("config.yaml top level is not a mapping")
    return loaded


def _overrides_source():
    """settings_overrides.json 的顶层 dict；文件不存在 → None；坏 JSON / 非
    dict → raise（同上，按损坏处理）。"""
    if not config.SETTINGS_OVERRIDES_PATH.exists():
        return None
    data = json.loads(config.SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise _CorruptSource("settings_overrides.json top level is not a mapping")
    return data


def _nested_analytics(mapping) -> object:
    """嵌套形 ``features: {analytics: …}`` 的原值；键不存在 → _MISSING。"""
    feats = mapping.get("features") if isinstance(mapping, dict) else None
    if isinstance(feats, dict) and "analytics" in feats:
        return feats["analytics"]
    return _MISSING


def _flat_analytics(data) -> object:
    """平铺形 ``"features.analytics"`` 键（§15）的原值；键不存在 → _MISSING。"""
    if data is not None and "features.analytics" in data:
        return data["features.analytics"]
    return _MISSING


def _coerce_if_present(raw) -> None:
    """判不动布尔（"banana" 之类的手改坏值）→ raise，调用方按损坏处理。"""
    if raw is not _MISSING:
        config._coerce_bool(raw)


def _config_sources_intact() -> bool:
    """两份配置源是否处于「存在但读不懂」的损坏态（隐私 fail-closed 用）。

    load_config 对坏 yaml / 坏 overrides 的惯例是静默退回默认（宪法第 11
    条），但 §16 默认 = analytics on——损坏期间用户已写下的显式退出会被
    无声顶掉。这里单独探测损坏；文件**不存在**不算损坏（用户从未表达过
    退出，默认 on 是诚实的）。损坏也包括 flag 值本身写了但判不动布尔
    （"banana" 之类的手改坏值）——load_config 会把它静默退回默认 on，
    可用户写下它时想表达的很可能是退出，宁可按 off 处理（Swift 侧
    Analytics.featureEnabled 同一保守探测）。「读不懂」也包括 PyYAML 缺失
    （config.yaml=None）而文件在场——退出可能就写在这份没人能读的文件里
    （运行时依赖白名单本含 PyYAML，走到这说明环境已残，但 fail-closed
    不赌可达性）。overrides 侧嵌套形与平铺形**都**要判得动。
    """
    try:
        _coerce_if_present(_nested_analytics(_yaml_source()))
    except Exception:  # noqa: BLE001 - 读不到/判不动 = 按损坏处理
        return False
    try:
        data = _overrides_source()
        _coerce_if_present(_nested_analytics(data))
        _coerce_if_present(_flat_analytics(data))
    except Exception:  # noqa: BLE001 - 读不到/判不动 = 按损坏处理
        return False
    return True


def _evaluate_gate(cfg: Optional["config.Config"] = None) -> bool:
    """flag 与损坏探测合取；任何异常 = off（隐私 flag：读不到就当关）。"""
    try:
        if cfg is None:
            cfg = config.load_config()
        return bool(cfg.feature("analytics")) and _config_sources_intact()
    except Exception:  # noqa: BLE001 - privacy flag: unreadable = off
        return False


def _cached_gate(now: float, fp: tuple) -> Optional[bool]:
    """缓存命中（未过期且配置源指纹未变）→ 缓存值；否则 None。"""
    if (_gate_cache is not None and now < _gate_cache[0]
            and fp == _gate_cache[2]):
        return _gate_cache[1]
    return None


def feature_gate(cfg: Optional["config.Config"] = None) -> bool:
    """§16 feature gate for the local event log: ``features.analytics``.

    False ⇒ log_event/log_first 全静默——本地 events.jsonl 一行不写，上传侧
    (act.analytics_sync / feedback 附带的事件) 读的正是这份文件，且上传端
    自己也过同一 gate（关 = 积压也不上传）。

    隐私特例（§16 追记）：与其它 flag 的 fail-open 惯例相反，本 gate 在
    「配置读不到 / 存在但损坏」时 **fail-closed**（= 不记）——用户显式退出
    的隐私承诺压过功能可用性默认，否则坏一份 yaml 就能让退出静默失效。
    判定自身绝不 raise（宪法第 11 条），只会返回 False。

    ``cfg`` 注入缝：run 开始时已持有 Config 的调用方（sync_once 早退检查）
    直接传入，跳过缓存与重复 load；无 cfg 的高频 emit 路径走进程内缓存
    ——缓存键含两份配置源的指纹（_sources_fingerprint），配置一变下一条
    事件即重判，TTL 只兜指纹失灵的底。
    """
    if cfg is not None:
        return _evaluate_gate(cfg)
    global _gate_cache
    now = _time.monotonic()
    fp = _sources_fingerprint()
    cached = _cached_gate(now, fp)
    if cached is not None:
        return cached
    value = _evaluate_gate()
    _gate_cache = (now + GATE_TTL, value, fp)
    return value


def _override_analytics(data) -> object:
    """overrides 的 analytics 原值：嵌套 features 块优先，其次平铺键（§15）。"""
    raw = _nested_analytics(data)
    if raw is not _MISSING:
        return raw
    return _flat_analytics(data)


def feature_gate_fresh() -> bool:
    """单快照新鲜判定——上传端每个 batch 送出前的最后一道检查用。

    与 feature_gate 的「load_config + _config_sources_intact 各读一遍文件」
    不同，这里每份配置源只读**一次** bytes：flag 值与「存在但读不懂 = off」
    的损坏判定出自同一份快照。否则 load_config 读到旧值（on）、intact 检查
    确认的却是用户刚原子写入的新文件（语法有效），这个 TOCTOU 窗口会把刚
    退出的用户的积压送出去。优先级与 load_config 一致：overrides（嵌套
    features 块 → 平铺 features.* 键）→ config.yaml features 块 → 默认 on。
    不读不写 GATE_TTL 缓存；绝不 raise，判不动一律 False（隐私 fail-closed，
    宪法第 11 条）。
    """
    value = True  # §16 默认 on：键不存在 = 用户从未表达过退出
    try:
        raw = _nested_analytics(_yaml_source())
        if raw is not _MISSING:
            value = config._coerce_bool(raw)
    except Exception:  # noqa: BLE001 - 存在但读不懂/判不动 = off
        return False
    try:
        raw = _override_analytics(_overrides_source())
        if raw is not _MISSING:
            value = config._coerce_bool(raw)
    except Exception:  # noqa: BLE001 - 存在但读不懂/判不动 = off
        return False
    return value


def log_event(event: str, **fields) -> bool:
    """Append one event. Non-None fields only. Never raises.

    Gated on ``features.analytics`` (§16): with the flag off this is a no-op —
    nothing is written locally, hence nothing can ever be uploaded.

    Returns True only when the line was actually appended（镜像 Swift
    Analytics.appendLine 的返回值语义）：log_first 拿它决定 marker——gate
    中途翻关 / 磁盘错被吞时返回 False，marker 不落笔，里程碑不会「已标记
    却从未落盘」。既有调用点全部忽略返回值，add-only。
    """
    if not feature_gate():
        return False
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": str(event),
            # writer-level version stamp (docs/TELEMETRY.md): every python
            # event carries "v", mirroring the Swift writer — no emitter can
            # forget it, so app_version is never "(unset)" on upload.
            "v": __version__,
        }
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 - analytics must never break the pipeline
        return False


_MARKER_SAFE = _re.compile(r"[^A-Za-z0-9_.-]+")


def _first_marker(event: str) -> Path:
    """里程碑名 → 文件系统安全的 marker 路径（非法字符折成 _，空名回落 event）。"""
    name = _MARKER_SAFE.sub("_", str(event)).strip("._") or "event"
    return FIRST_DIR / name


def log_first(event: str, **fields) -> None:
    """Emit ``event`` at most once per install (lifecycle milestone).

    A persistent empty marker under ``state/analytics/first/<event>`` records
    that the milestone already fired; every later call is a no-op. ``fields``
    are behavior-only metadata (req ids, counts) exactly like :func:`log_event`
    — NEVER card content — so this fits the existing content_gate/privacy scope
    without touching it.

    Write-success-then-mark（镜像 Swift Analytics.firstReach）：log_event 返回
    写入是否真的落盘，成功才落 marker——事件在内部被吞（gate 在两次检查之间
    翻关、磁盘错）时 marker 不写，里程碑留到下次触发再发。反向的 crash 窗口
    （写成功、marker 没落）至多 double-emit，无害：消费方
    (scripts/insights_report.py) 按 DISTINCT device 计数，多进程（radar cron
    vs. actd）竞态同理只多几条重复。Never raises.

    Gate BEFORE the marker (§16): flag off 期间不许 touch marker——否则
    里程碑被标记「已发」却从未落盘，重新开启后 once-per-install 事件永久
    丢失；off 期间整个函数 no-op，里程碑留到 flag 重开后首次触发再发。
    """
    if not feature_gate():
        return
    try:
        marker = _first_marker(event)
        if marker.exists():
            return
        if not log_event(event, **fields):
            return  # 事件没真正落盘 → 不 mark，下次再试
        FIRST_DIR.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except Exception:  # noqa: BLE001 - analytics must never break the pipeline
        pass


def clip(text, limit: int = 200) -> Optional[str]:
    """Whitespace-collapsed, truncated string for telemetry payload fields
    (docs/TELEMETRY.md; content fields use limit=CONTENT_CLIP) — None when
    empty so log_event drops it.
    """
    return _collapse_ws(text)[:limit] or None


def parse_ts(s: str) -> Optional[_dt.datetime]:
    """Parse an event 'ts' (UTC) -> aware datetime, or None."""
    try:
        return _dt.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def _event_passes_since(d: dict, since: Optional[_dt.datetime]) -> bool:
    """``since`` 过滤：无 since 一律放行；ts 缺失/坏形 → 不放行。"""
    if since is None:
        return True
    try:
        ts = _dt.datetime.strptime(d.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return ts.replace(tzinfo=_dt.timezone.utc) >= since


def read_events(since: Optional[_dt.datetime] = None) -> Iterator[dict]:
    """Yield parsed events (optionally only those newer than ``since``, UTC)."""
    try:
        fh = open(EVENTS_PATH, encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _event_passes_since(d, since):
                yield d
