"""Usage analytics — append-only event log for every feature use.

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
from act.lib import config

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
# analytics_sync, unchanged).
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


def _secret_positions(s: str, patterns) -> set:
    """Index set of every character of ``s`` that is secret material.

    两遍扫描：先扫原串；再把空白拼掉扫一遍并映射回原串下标——邮件式换行/
    空格会把 key 劈成两段，只有拼合后才能看出整条是密钥素材（§15 承诺任何
    设置下都不收集 key，劈开的尾段也是）。拼合可能把紧邻 key 的词也圈进来
    （无法与折行区分），宁可多掩不可半漏（fail safe）。
    """
    positions: set = set()
    for pat in patterns:
        for m in pat.finditer(s):
            positions.update(range(m.start(), m.end()))
    idx_map = [i for i, ch in enumerate(s) if ch != " "]
    compact = "".join(ch for ch in s if ch != " ")
    for pat in patterns:
        for m in pat.finditer(compact):
            positions.update(idx_map[j] for j in range(m.start(), m.end()))
    return positions


def clip_content(text) -> Optional[str]:
    """clip() for user-typed CONTENT fields: secret-mask FIRST, then cap at
    CONTENT_CLIP. The masking (act/lib/sanitize._SECRET_PATTERNS) is
    UNCONDITIONAL — independent of every redaction.* switch — because the
    docs promise keys never ride in telemetry at any setting (the Swift
    writer mirrors the same patterns in Analytics.clip). Fail closed: if
    masking itself breaks, the content is dropped, never sent raw.
    """
    s = " ".join(str(text or "").split())
    if not s:
        return None
    try:
        from act.lib import sanitize  # lazy: keep analytics import-light
        positions = _secret_positions(s, sanitize._SECRET_PATTERNS)
        if positions:
            # 每段连续的密钥区间折叠成一个 MASK；夹在两段掩码之间的折行
            # 空格一并吞掉（它只是被 split 归一出来的换行痕迹）
            out: list = []
            i = 0
            while i < len(s):
                if i in positions:
                    out.append(sanitize.MASK)
                    while i < len(s):
                        if i in positions:
                            i += 1
                        elif s[i] == " " and (i + 1) in positions:
                            i += 1
                        else:
                            break
                else:
                    out.append(s[i])
                    i += 1
            s = "".join(out)
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
    不赌可达性）。
    """
    try:
        if config.CONFIG_PATH.exists():
            if config.yaml is None:  # 文件在场却无解析器 = 损坏同款
                return False
            loaded = config.yaml.safe_load(
                config.CONFIG_PATH.read_text(encoding="utf-8"))
            if loaded is not None and not isinstance(loaded, dict):
                return False
            feats = loaded.get("features") if isinstance(loaded, dict) else None
            if isinstance(feats, dict) and "analytics" in feats:
                config._coerce_bool(feats["analytics"])  # 判不动 → except
    except Exception:  # noqa: BLE001 - 读不到/判不动 = 按损坏处理
        return False
    try:
        if config.SETTINGS_OVERRIDES_PATH.exists():
            data = json.loads(
                config.SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            feats = data.get("features")
            if isinstance(feats, dict) and "analytics" in feats:
                config._coerce_bool(feats["analytics"])
            if "features.analytics" in data:  # 平铺形（§15）
                config._coerce_bool(data["features.analytics"])
    except Exception:  # noqa: BLE001 - 读不到/判不动 = 按损坏处理
        return False
    return True


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
        try:
            return bool(cfg.feature("analytics")) and _config_sources_intact()
        except Exception:  # noqa: BLE001 - privacy flag: unreadable = off
            return False
    global _gate_cache
    now = _time.monotonic()
    fp = _sources_fingerprint()
    if (_gate_cache is not None and now < _gate_cache[0]
            and fp == _gate_cache[2]):
        return _gate_cache[1]
    try:
        value = (bool(config.load_config().feature("analytics"))
                 and _config_sources_intact())
    except Exception:  # noqa: BLE001 - privacy flag: unreadable = off
        value = False
    _gate_cache = (now + GATE_TTL, value, fp)
    return value


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
        if config.CONFIG_PATH.exists():
            if config.yaml is None:  # 文件在场却无解析器 = 损坏同款 off
                return False
            loaded = config.yaml.safe_load(
                config.CONFIG_PATH.read_text(encoding="utf-8"))
            if loaded is not None and not isinstance(loaded, dict):
                return False
            feats = loaded.get("features") if isinstance(loaded, dict) else None
            if isinstance(feats, dict) and "analytics" in feats:
                value = config._coerce_bool(feats["analytics"])
    except Exception:  # noqa: BLE001 - 存在但读不懂/判不动 = off
        return False
    try:
        if config.SETTINGS_OVERRIDES_PATH.exists():
            data = json.loads(
                config.SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            feats = data.get("features")
            if isinstance(feats, dict) and "analytics" in feats:
                value = config._coerce_bool(feats["analytics"])
            elif "features.analytics" in data:  # 平铺形（§15），嵌套形优先
                value = config._coerce_bool(data["features.analytics"])
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
        name = _MARKER_SAFE.sub("_", str(event)).strip("._") or "event"
        marker = FIRST_DIR / name
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
    s = " ".join(str(text or "").split())
    return s[:limit] or None


def parse_ts(s: str) -> Optional[_dt.datetime]:
    """Parse an event 'ts' (UTC) -> aware datetime, or None."""
    try:
        return _dt.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return None


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
            if since is not None:
                try:
                    ts = _dt.datetime.strptime(d.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")
                    ts = ts.replace(tzinfo=_dt.timezone.utc)
                except ValueError:
                    continue
                if ts < since:
                    continue
            yield d
