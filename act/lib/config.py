"""Configuration + canonical runtime paths for Zelin's AI Assistant (lite).

契约：CONTRACT §15（`state/settings_overrides.json` 最后合并、只碰点名的键）+
§19（凭证路径）+ §54（server.port）+ §59（模型旋钮 `models.pipeline` 与 D53 的
`models.fallback`）+ §63（recap 旋钮）+ §72（录制数据保留期：`recording.retention_days`
的 DB 天数与 §72.4 `recording.media_retention_minutes` 的媒体分钟数，后者经
`--print-value` 给 cron 消费）+ §78（headless claude 的隔离常量 CLAUDE_CONFIG_DIR /
HEADLESS_CWD）+ §79（`updates.auto_install`）。

v2.0.0 起 Act 管线退役（tag `full` 留全文）：approval / execution（claude_bin pin 除外）/
registry / digest / daily_loop / self_improve / slack / gmail / telemetry / feedback /
features 这些字段与它们的 override handler 一并删除；settings_overrides.json 里残留的
旧键按「未知键」静默忽略（§15 add-only 的读者一侧）。

Runtime state lives under ``AIASSISTANT_HOME/state`` (gitignored). All paths are
derived from the ``AIASSISTANT_HOME`` env var, defaulting to
``~/Projects/zelin-ai-assistant``. Constants are ``pathlib.Path`` objects so
every component resolves to the same files.

Shell consumers (the ingest scripts) resolve paths and scalar settings through
the same layer: ``python3 -m act.lib.config --print-path obsidian_unprocessed``,
``--print-path headless_cwd``, ``--print-value claude_bin``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml  # PyYAML — install if missing (see module docstring)
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    yaml = None  # type: ignore


# --------------------------------------------------------------------------- #
# Canonical paths (env-driven, single source everywhere)
# --------------------------------------------------------------------------- #
def _home() -> Path:
    return Path(os.environ.get("AIASSISTANT_HOME", "~/Projects/zelin-ai-assistant")).expanduser()


HOME: Path = _home()
STATE_DIR: Path = HOME / "state"
NOTIFY_QUEUE_DIR: Path = STATE_DIR / "notify_queue"  # §28 app notification relay
LOG_DIR: Path = STATE_DIR / "logs"

# Config files (config.yaml is gitignored; config.example.yaml is the fallback).
CONFIG_PATH: Path = HOME / "config.yaml"
CONFIG_EXAMPLE_PATH: Path = HOME / "config.example.yaml"

# Settings overrides (§15) — the web Settings page writes ONLY this file;
# load_config() merges it LAST so it has the highest priority.
SETTINGS_OVERRIDES_PATH: Path = STATE_DIR / "settings_overrides.json"

# Built-in Obsidian vault root fallback — used to derive the pipeline dirs
# when sources.obsidian_raw is not configured (v0.10.3 契约二).
DEFAULT_OBSIDIAN_VAULT: str = "~/Documents/Obsidian Vault"

# Per-user application support directory (never TCC-gated, outside the repo).
APP_SUPPORT_DIR: Path = Path.home() / "Library" / "Application Support" / "ZelinAIAssistant"

# §78 headless claude isolation. Every `claude -p` this app spawns runs with
# CLAUDE_CONFIG_DIR pointed here (never the owner's Claude Code home) and with its cwd
# in HEADLESS_CWD — an empty directory outside the repo tree, so no CLAUDE.md,
# repo-local skills or project settings are on the ancestor chain. ingest/claude-env.sh
# carries the same two literals (equality pinned by tests/test_isolation_footprint.py).
# `AIASSISTANT_CLAUDE_CONFIG_DIR` relocates both (the test sandbox does; same
# pattern as AIASSISTANT_STABLE_CLAUDE below).
CLAUDE_CONFIG_DIR: Path = APP_SUPPORT_DIR / "claude-config"
HEADLESS_CWD: Path = CLAUDE_CONFIG_DIR / "cwd"

# Sensitive-app capture exclusion (P1-9) — applied at BOTH ends of the screen
# pipeline: the shell passes each entry to the engine as --ignored-windows
# (screenpipe skips matching windows BEFORE anything is stored), and
# ingest/screenpipe-export.sh filters already-stored frames with the same list
# (see recording_exclusion_sql). Bare terms match case-insensitive substring
# against app name OR window title; the engine's `App::Title` scoping syntax is
# honoured too. Keep in sync with ScreenpipeRecipe.defaultIgnoredApps in
# shell/Sources/Recording.swift (drift-guarded by tests/test_capture_exclusion.py).
DEFAULT_IGNORED_APPS: list = [
    "1Password",
    "Bitwarden",
    "LastPass",
    "KeePassXC",
    "Keychain Access",
    "Private Browsing",  # Safari private windows (window-title match)
    "Incognito",         # Chrome/Edge incognito windows (window-title match)
]

# §72.4 原始媒体（帧 / 音频片段）的保留期，分钟：cron 链第二步
# ingest/screenpipe-cleanup.sh 的 `find -mmin +N`。出厂 60；下限 5 分钟（链每 30 分钟
# 一轮，比这更短会削到同一轮里正在导出的那批帧）、上限 1 年。区间外 = 坏值（yaml 回落
# 默认、override 整条跳过）——绝不悄悄夹到边界上：文件里写着的数必须就是 cron 用的数。
DEFAULT_MEDIA_RETENTION_MINUTES: int = 60
MIN_MEDIA_RETENTION_MINUTES: int = 5
MAX_MEDIA_RETENTION_MINUTES: int = 365 * 24 * 60

CLOCK_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# §54 看板 server 的回环端口（config.yaml `server.port`）。install.sh 把它渲进
# com.zelin.aiassistant.server.plist 的 ZAI_PORT；server/ 自己只读 env
# （server/app.py DEFAULT_PORT 镜像这个默认值，tests/test_server_paths_mirror.py 钉漂移）。
DEFAULT_SERVER_PORT: int = 47820

# §59 模型旋钮：`models.pipeline`（每一次 headless `claude -p`：会议纪要）与 D53 的
# `models.fallback`（`--fallback-model`）。v2.0.0 退役了 `models.dispatch`（没有派工了）
# 与 "follow" 模式：隔离目录（§78）里没有 owner 的 Claude Code 全局设置可跟随，
# 所以出厂值是一个显式 id；旧文件里写着的 "follow" 读作「未设置」= 出厂值。
# canonical 表是 UI 下拉的全集；表外的自由文本照收（UI 与 doctor 对表外值给 WARN）。
# server/settings.py 镜像这些常量，tests/test_server_paths_mirror.py 钉漂移。
MODEL_MODES: tuple = ("pipeline",)
LEGACY_MODEL_FOLLOW: str = "follow"
CANONICAL_MODELS: tuple = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)
DEFAULT_MODEL_PIPELINE: str = "claude-opus-5"
# 模型 id 的形状闸（不是词表）：字母数字开头，之后允许 . _ - [ ] ，≤64 字符。
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")
# §59 D53：主模型拿不到时 claude 切到的模型；"off" = 不传旗标。
MODEL_FALLBACK_OFF: str = "off"
DEFAULT_MODEL_FALLBACK: str = "claude-opus-5[1m]"

# §63 recap.default_language：auto | zh | en；其余回 auto（跟随 UI 语言）。
RECAP_LANGUAGES: tuple = ("auto", "zh", "en")


def ensure_state_dirs() -> None:
    """Create the runtime state directories if they do not yet exist."""
    for d in (STATE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _ensure_private_dir(path: Path) -> None:
    """mkdir -p + chmod 0700, best effort (a read-only volume must not crash a caller)."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:
        pass


def claude_config_dir() -> Path:
    """§78 CLAUDE_CONFIG_DIR for every headless claude (env override, then default).

    Created on demand (0700): Claude Code writes its own state there, and a
    missing directory must never make it fall back to anything else."""
    override = os.environ.get("AIASSISTANT_CLAUDE_CONFIG_DIR", "").strip()
    path = Path(override).expanduser() if override else CLAUDE_CONFIG_DIR
    _ensure_private_dir(path)
    return path


def headless_cwd() -> str:
    """§78 the neutral cwd of every headless ``claude -p``: ``<claude-config>/cwd``.

    An empty directory under Application Support — outside the repo tree, so the
    repo's CLAUDE.md, its repo-local skills and the vault mirror's project settings
    are never on claude's ancestor chain even if ``--bare`` semantics change.
    Created on demand (both levels 0700); never written into by this app.
    """
    cwd = claude_config_dir() / "cwd"
    _ensure_private_dir(cwd)
    return str(cwd)


# --------------------------------------------------------------------------- #
# Config object
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    """Parsed config.yaml (with defaults so callers never KeyError)."""

    raw: dict = field(default_factory=dict)

    # sources — Obsidian pipeline dirs (v0.10.3 契约二). Unset ones are derived
    # from obsidian_raw's parent (vault root) + the standard folder names;
    # never None after load_config().
    obsidian_raw: Optional[str] = None
    obsidian_unprocessed: Optional[str] = None
    obsidian_change_summary: Optional[str] = None
    obsidian_wiki: Optional[str] = None

    # §28 通知偏好：只剩安静时段（唯一读者 act/lib/notify.suppression_reason）。
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"    # 本地 HH:MM；跨午夜合法
    quiet_hours_end: str = "08:00"

    # §54 看板 server 端口（config.yaml `server.port`，1..65535；坏值回默认）。
    server_port: int = DEFAULT_SERVER_PORT

    # §59 模型旋钮。坏形状回落出厂值——不可把垃圾塞进 argv。
    models_pipeline: str = DEFAULT_MODEL_PIPELINE
    models_fallback: str = DEFAULT_MODEL_FALLBACK

    # explicit claude CLI pin (config.yaml execution.claude_bin) — None = the
    # stable daemon copy, then PATH, then ~/.local/bin/claude (resolve_claude_bin).
    claude_bin: Optional[str] = None

    # screen-capture sensitive-app exclusion (P1-9) — key absent = defaults;
    # explicit `ignored_apps: []` in config.yaml = deliberate opt-out.
    recording_ignored_apps: list = field(
        default_factory=lambda: list(DEFAULT_IGNORED_APPS)
    )
    # §72 screenpipe DB 保留期（天）：0 = 永久保留（出厂）；config.yaml
    # recording.retention_days，override 扁平键 screenpipe_retention_days。
    screenpipe_retention_days: int = 0
    # §72.4 原始 jpg / mp4 保留分钟数：config.yaml recording.media_retention_minutes。
    screenpipe_media_retention_minutes: int = DEFAULT_MEDIA_RETENTION_MINUTES

    # local pre-send redaction (opt-in; act/lib/sanitize.py)
    redaction_enabled: bool = False
    redaction_terms_file: str = "config/redaction_terms.txt"
    redaction_mask_secrets: bool = True

    # §26 in-app update check (GitHub releases API, ETag-cached, ≤1 attempt/24h).
    updates_check_enabled: bool = True
    # §79 add-only: a newer tagged release installs itself when the §26 check
    # finds one. Default OFF — the About page's "Upgrade now" is the normal path.
    updates_auto_install: bool = False

    # 会议 recap（§63）：config.yaml `recap:` 块 + overrides 扁平键 recap_enabled /
    # recap_default_language；其余调参留在 cfg.raw["recap"]，由
    # act/lib/recap_store.settings 读取。
    recap_enabled: bool = True
    recap_default_language: str = "auto"   # auto | zh | en（auto 跟随 language）

    # UI language (§15) — "zh" | "en"
    language: str = "zh"


# --------------------------------------------------------------------------- #
# 类型校验 helpers — config.yaml / settings_overrides.json 都是用户可手改的文件：
# 坏值绝不能崩掉守护进程，更不能把语义反转（bool("false") == True 的经典坑）。
# --------------------------------------------------------------------------- #
_BOOL_TRUE_WORDS = ("true", "yes", "on", "1")
_BOOL_FALSE_WORDS = ("false", "no", "off", "0")


def coerce_bool(value) -> bool:
    """严格布尔转换：真布尔、0/1、常见字符串拼写之外一律 ValueError——调用方按
    「类型错 = 跳过、保留原生效值」处理。公开名（防腐 #2）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        return _bool_word(value)
    raise ValueError(f"not a bool: {value!r}")


def _bool_word(value: str) -> bool:
    """"true"/"yes"/"on"/"1" → True；"false"/"no"/"off"/"0" → False；其余 ValueError。"""
    v = value.strip().lower()
    if v in _BOOL_TRUE_WORDS:
        return True
    if v in _BOOL_FALSE_WORDS:
        return False
    raise ValueError(f"not a bool: {value!r}")


_coerce_bool = coerce_bool   # 旧名（本模块内部沿用）


def _bool_or(value, default: bool) -> bool:
    """config.yaml 路径的宽容版：坏值保留 default（原生效值），不抛。"""
    try:
        return coerce_bool(value)
    except (TypeError, ValueError):
        return default


def _int_or(value, default: int) -> int:
    try:
        if isinstance(value, bool):  # bool 是 int 子类，但语义不是数
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict_or(value) -> dict:
    """config.yaml 的块必须是 mapping；写成标量/列表时按缺失处理。"""
    return value if isinstance(value, dict) else {}


def coerce_recap_language(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in RECAP_LANGUAGES else "auto"


def coerce_clock_time(value) -> str:
    """本地 HH:MM（"3:30" 归一为 "03:30"）；其余形状 ValueError。server/settings.py
    镜像同一正则（tests/test_server_paths_mirror.py 钉）。"""
    if not isinstance(value, str):
        raise ValueError(f"not a HH:MM time: {value!r}")
    m = CLOCK_TIME_RE.match(value.strip())
    if m is None:
        raise ValueError(f"not a HH:MM time: {value!r}")
    return "%02d:%s" % (int(m.group(1)), m.group(2))


def coerce_media_retention_minutes(value) -> int:
    """§72.4 `recording.media_retention_minutes`：非 bool 的 int 或整值 float，且落在
    [MIN, MAX] 内，否则 ValueError（overrides 路径整条跳过、yaml 路径回落 60）。**不夹取**
    ——与 server/settings_catalog.py 的 bounds 闸逐字同一条规则（判例
    tests/test_screenpipe_media_retention_knob.py 两侧同钉）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a retention: %r" % (value,))
    if isinstance(value, float) and not value.is_integer():   # 非整值 / inf / nan
        raise ValueError("not a whole number of minutes: %r" % (value,))
    minutes = int(value)
    if not MIN_MEDIA_RETENTION_MINUTES <= minutes <= MAX_MEDIA_RETENTION_MINUTES:
        raise ValueError("retention out of range: %r" % (value,))
    return minutes


def coerce_model(value) -> str:
    """§59 `models.pipeline` 归一：None / 空白 / 旧哨兵 "follow"（大小写不敏感）→ 出厂值
    DEFAULT_MODEL_PIPELINE；形状合法的 id 原样；其余 → ValueError（调用方按「坏值 =
    保留原生效值」处理）。"""
    if value is None:
        return DEFAULT_MODEL_PIPELINE
    if not isinstance(value, str):
        raise ValueError(f"not a model id: {value!r}")
    s = value.strip()
    if not s or s.lower() == LEGACY_MODEL_FOLLOW:
        return DEFAULT_MODEL_PIPELINE
    if not MODEL_ID_RE.match(s):
        raise ValueError(f"not a model id: {value!r}")
    return s


def coerce_fallback_model(value) -> str:
    """§59 D53 `models.fallback` 归一：None/空白 → 出厂值；"off"（大小写不敏感）**或布尔
    False** → "off"（YAML 1.1 把裸 `off` 读成 False）；形状合法的 id 原样；其余（含 True）
    → ValueError。"""
    if value is None:
        return DEFAULT_MODEL_FALLBACK
    if value is False:
        return MODEL_FALLBACK_OFF
    if not isinstance(value, str):
        raise ValueError(f"not a model id: {value!r}")
    return _coerce_fallback_str(value.strip())


def _coerce_fallback_str(s: str) -> str:
    """coerce_fallback_model 的字符串半边：空 → 出厂值；"off" → off；形状闸。"""
    if not s:
        return DEFAULT_MODEL_FALLBACK
    if s.lower() == MODEL_FALLBACK_OFF:
        return MODEL_FALLBACK_OFF
    if not MODEL_ID_RE.match(s):
        raise ValueError(f"not a model id: {s!r}")
    return s


def _or_default(coerce, value, default):
    try:
        return coerce(value)
    except (TypeError, ValueError):
        return default


def model_is_canonical(value) -> bool:
    """UI/doctor 的 WARN 判据：显式值是否在 CANONICAL_MODELS 表内（空值 = 出厂值，算 True）。"""
    s = str(value or "").strip()
    return (not s) or s in CANONICAL_MODELS


def _config_path() -> Path:
    return CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH


def _load_yaml_dict(path: Path) -> dict:
    """config.yaml → dict；缺文件 / 坏 yaml / 坏编码 / 非 mapping = {}（全默认）。"""
    if yaml is None or not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏 yaml/编码 = 全默认，绝不崩守护进程
        return {}
    return loaded if isinstance(loaded, dict) else {}


# --------------------------------------------------------------------------- #
# config.yaml block appliers (each writes only its own fields)
# --------------------------------------------------------------------------- #
def _apply_sources(cfg: Config, data: dict) -> None:
    sources = _dict_or(data.get("sources"))
    for attr in ("obsidian_raw", "obsidian_unprocessed", "obsidian_change_summary",
                 "obsidian_wiki"):
        setattr(cfg, attr, sources.get(attr, getattr(cfg, attr)))


def _apply_execution(cfg: Config, data: dict) -> None:
    # v2.0.0：execution 块只剩 claude_bin 这一把 pin（其余键随 executor 退役，静默忽略）
    pinned = _dict_or(data.get("execution")).get("claude_bin")
    if pinned and str(pinned).strip():
        cfg.claude_bin = str(pinned).strip()


def _apply_server_models(cfg: Config, data: dict) -> None:
    port = _int_or(_dict_or(data.get("server")).get("port"), DEFAULT_SERVER_PORT)
    cfg.server_port = port if 0 < port < 65536 else DEFAULT_SERVER_PORT
    models = _dict_or(data.get("models"))
    if "pipeline" in models:
        cfg.models_pipeline = _or_default(coerce_model, models.get("pipeline"),
                                          DEFAULT_MODEL_PIPELINE)
    if "fallback" in models:
        cfg.models_fallback = _or_default(coerce_fallback_model, models.get("fallback"),
                                          DEFAULT_MODEL_FALLBACK)


def _apply_recording(cfg: Config, data: dict) -> None:
    recording = _dict_or(data.get("recording"))
    apps = recording.get("ignored_apps")
    if isinstance(apps, list):
        cfg.recording_ignored_apps = [
            str(a).strip() for a in apps if a is not None and str(a).strip()
        ]
    # §72.2：坏值 / 负数按「未设」= 永久保留
    days = _int_or(recording.get("retention_days", cfg.screenpipe_retention_days),
                   cfg.screenpipe_retention_days)
    cfg.screenpipe_retention_days = days if days >= 0 else cfg.screenpipe_retention_days
    # §72.4：区间外 / 坏值按「未设」= 出厂 60
    if "media_retention_minutes" in recording:
        cfg.screenpipe_media_retention_minutes = _or_default(
            coerce_media_retention_minutes, recording.get("media_retention_minutes"),
            DEFAULT_MEDIA_RETENTION_MINUTES)


def _apply_redaction(cfg: Config, data: dict) -> None:
    red = _dict_or(data.get("redaction"))
    cfg.redaction_enabled = _bool_or(red.get("enabled", cfg.redaction_enabled),
                                     cfg.redaction_enabled)
    cfg.redaction_terms_file = red.get("terms_file", cfg.redaction_terms_file)
    cfg.redaction_mask_secrets = _bool_or(red.get("mask_secrets", cfg.redaction_mask_secrets),
                                          cfg.redaction_mask_secrets)
    terms = cfg.redaction_terms_file
    if terms and not str(terms).startswith(("/", "~")):
        cfg.redaction_terms_file = str(HOME / terms)


def _apply_updates_recap_language(cfg: Config, data: dict) -> None:
    updates = _dict_or(data.get("updates"))
    cfg.updates_check_enabled = _bool_or(updates.get("check_enabled", cfg.updates_check_enabled),
                                         cfg.updates_check_enabled)
    cfg.updates_auto_install = _bool_or(updates.get("auto_install", cfg.updates_auto_install),
                                        cfg.updates_auto_install)
    recap = _dict_or(data.get("recap"))
    cfg.recap_enabled = _bool_or(recap.get("enabled", cfg.recap_enabled), cfg.recap_enabled)
    cfg.recap_default_language = coerce_recap_language(
        recap.get("default_language", cfg.recap_default_language))
    if isinstance(data.get("language"), str) and data["language"].strip():
        cfg.language = data["language"].strip()


_BLOCK_APPLIERS = (
    _apply_sources,
    _apply_execution,
    _apply_server_models,
    _apply_recording,
    _apply_redaction,
    _apply_updates_recap_language,
)


def load_config() -> Config:
    """Load ``config.yaml`` (falling back to ``config.example.yaml``).

    Never raises on a missing file — returns a Config with defaults.
    """
    data = _load_yaml_dict(_config_path())
    cfg = Config(raw=data)
    for apply_block in _BLOCK_APPLIERS:
        apply_block(cfg, data)
    _apply_settings_overrides(cfg)
    # AFTER the overrides merge, so an overridden obsidian_raw re-points the
    # derived pipeline dirs too (explicitly-set dirs are left untouched).
    _derive_obsidian_dirs(cfg)
    return cfg


# §55 第五幕：the STABLE copy of the claude binary. install.sh keeps a copy of the
# login shell's claude at this fixed path (refreshed in place when Claude Code
# updates). macOS keys Full Disk Access for bare executables by PATH, and
# `~/.local/bin/claude` re-points to a new versioned directory on every update —
# a grant on THIS path survives. It also keeps the app off the owner's CLI
# upgrade cadence. `AIASSISTANT_STABLE_CLAUDE` relocates it (the test suite
# points it into the sandbox; install.sh honours the same variable).
STABLE_CLAUDE_BIN: Path = APP_SUPPORT_DIR / "bin" / "claude"


def stable_claude_bin() -> Path:
    """Where the stable copy lives (env override, then the default)."""
    override = os.environ.get("AIASSISTANT_STABLE_CLAUDE", "").strip()
    return Path(override).expanduser() if override else STABLE_CLAUDE_BIN


def stable_claude_present(path: Optional[Path] = None) -> bool:
    """True when the stable copy exists and is executable."""
    p = path or stable_claude_bin()
    try:
        return p.is_file() and os.access(str(p), os.X_OK)
    except OSError:
        return False


def resolve_claude_bin(cfg: Optional[Config] = None) -> str:
    """The claude CLI every subprocess site launches (recap here, ingest through
    ``--print-value claude_bin``).

    Order: execution.claude_bin (explicit pin) -> the stable copy
    (:func:`stable_claude_bin`, when present) -> PATH -> ~/.local/bin/claude.
    Daemon PATHs are hostile (cron's misses ~/.local/bin entirely); the stable
    copy ranks above PATH because it is the one path a Full Disk Access grant
    survives Claude Code updates on (§55 第五幕).
    """
    if cfg is None:
        cfg = load_config()
    pinned = str(getattr(cfg, "claude_bin", "") or "").strip()
    if pinned:
        return str(Path(pinned).expanduser())
    stable = stable_claude_bin()
    if stable_claude_present(stable):
        return str(stable)
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


# --------------------------------------------------------------------------- #
# Obsidian pipeline dirs (v0.10.3 契约二)
# --------------------------------------------------------------------------- #
_OBSIDIAN_DIR_NAMES: dict = {
    "obsidian_unprocessed": "1 - unprocessed",
    "obsidian_change_summary": "3 - change-summary",
    "obsidian_wiki": "4 - wiki",
}


def _derive_obsidian_dirs(cfg: Config) -> None:
    """Fill any unset pipeline dir with vault-root + standard folder name."""
    if cfg.obsidian_raw and str(cfg.obsidian_raw).strip():
        vault = Path(str(cfg.obsidian_raw)).expanduser().parent
    else:
        vault = Path(DEFAULT_OBSIDIAN_VAULT).expanduser()
    for attr, name in _OBSIDIAN_DIR_NAMES.items():
        current = getattr(cfg, attr)
        if not (current and str(current).strip()):
            setattr(cfg, attr, str(vault / name))


# --------------------------------------------------------------------------- #
# settings_overrides.json overlay (§15) — highest priority
# --------------------------------------------------------------------------- #
def _coerce_language(value) -> str:
    """§15 language 覆盖：strip 后非空才生效；空/空白抛 ValueError（per-entry 跳过）。"""
    s = str(value).strip()
    if not s:
        raise ValueError("empty language")
    return s


def _clean_str_list(value: list) -> list:
    """字串表的清洗（去空白、丢空项与非字串）。"""
    return [str(v).strip() for v in value
            if isinstance(v, (str, int)) and str(v).strip()]


# Scalar cfg fields the overrides file may set, with a coercion for each. Bad
# values raise → that one entry is skipped (the effective value stays).
_OVERRIDE_FIELDS: dict = {
    "obsidian_raw": str,
    "obsidian_unprocessed": str,
    "obsidian_change_summary": str,
    "obsidian_wiki": str,
    # §28 安静时段（三把）
    "quiet_hours_enabled": coerce_bool,
    "quiet_hours_start": coerce_clock_time,
    "quiet_hours_end": coerce_clock_time,
    # §72 / §72.4 录制数据保留期
    "screenpipe_retention_days": int,
    "screenpipe_media_retention_minutes": coerce_media_retention_minutes,
    "language": _coerce_language,
    "redaction_enabled": coerce_bool,
    "redaction_terms_file": str,
    "redaction_mask_secrets": coerce_bool,
    # §26 / §79
    "updates_check_enabled": coerce_bool,
    "updates_auto_install": coerce_bool,
    # §59 模型旋钮（server/settings.py diff-write）
    "models_pipeline": coerce_model,
    "models_fallback": coerce_fallback_model,
    # §63 会议 recap（server/recaps.py diff-write）
    "recap_enabled": coerce_bool,
    "recap_default_language": coerce_recap_language,
}

# List-valued override keys (add-only): the sensitive-app exclusion list the
# Settings page edits (§15 追记 v2.0.0 `recording.ignored_apps`).
_OVERRIDE_LIST_FIELDS: dict = {"recording_ignored_apps": _clean_str_list}


def _read_overrides() -> Optional[dict]:
    """``STATE_DIR/settings_overrides.json`` as a dict; None when absent,
    malformed or not an object."""
    try:
        if not SETTINGS_OVERRIDES_PATH.exists():
            return None
        data = json.loads(SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never raise on a malformed overrides file
        return None
    return data if isinstance(data, dict) else None


def _apply_settings_overrides(cfg: Config) -> None:
    """Overlay ``STATE_DIR/settings_overrides.json`` onto ``cfg`` (§15).

    Only the fields the file names are touched. Malformed JSON, wrong types, or
    unknown keys (including every key retired with the Act pipeline) are
    silently ignored — a broken overrides file must never take a process down.
    """
    data = _read_overrides()
    if data is None:
        return
    for key, value in data.items():
        try:
            _apply_override(cfg, key, value)
        except Exception:  # noqa: BLE001 — skip just the bad entry
            continue


def _apply_override(cfg: Config, key: str, value) -> None:
    """One overrides entry: ``sources.<field>`` dotted form, a list field, or a scalar."""
    if key.startswith("sources."):
        key = key.split(".", 1)[1]
    if key in _OVERRIDE_LIST_FIELDS:
        if isinstance(value, list):
            setattr(cfg, key, _OVERRIDE_LIST_FIELDS[key](value))
        return
    if key in _OVERRIDE_FIELDS and value is not None:
        setattr(cfg, key, _OVERRIDE_FIELDS[key](value))


# --------------------------------------------------------------------------- #
# Capture-exclusion SQL (P1-9) — consumed by ingest/screenpipe-export.sh via a
# one-line python call. One place builds the fragment so quoting/NULL handling
# is testable and the shell never string-munges app names.
# --------------------------------------------------------------------------- #
def recording_exclusion_sql(cfg: Optional[Config] = None) -> str:
    """WHERE-clause fragment excluding frames whose app/window matches
    ``recording.ignored_apps``, mirroring the engine's --ignored-windows
    semantics (screenpipe 0.3.349 window_pattern, source-verified):

    - bare term        → case-insensitive substring against app name OR title
    - ``App::Title``   → app substring AND title substring must both match
    - ``App::`` / ``::Title`` → app-only / title-only substring

    Returns one line of ``AND …`` clauses — empty string when the list is
    empty (explicit opt-out). ``coalesce`` keeps NULL app/window rows
    exported: ``NULL NOT LIKE`` is NULL in SQLite and would silently drop
    them.
    """
    cfg = cfg or load_config()

    def like(column: str, part: str) -> str:
        return (
            f"lower(coalesce(f.{column}, '')) LIKE"
            " '%" + part.lower().replace("'", "''") + "%'"
        )

    def split_clause(term: str) -> str:
        """``App::Title`` / ``App::`` / ``::Title`` → conjunction of the non-empty parts."""
        app_part, title_part = term.split("::", 1)
        conds = []
        if app_part.strip():
            conds.append(like("app_name", app_part.strip()))
        if title_part.strip():
            conds.append(like("window_name", title_part.strip()))
        return "AND NOT (" + " AND ".join(conds) + ")" if conds else ""

    clauses = []
    for term in cfg.recording_ignored_apps:
        if "::" in term:
            clause = split_clause(term)
            if clause:
                clauses.append(clause)
        else:
            clauses.append(
                f"AND NOT ({like('app_name', term)} OR {like('window_name', term)})"
            )
    return " ".join(clauses)


# --------------------------------------------------------------------------- #
# CLI — `python3 -m act.lib.config --print-path <key>` / `--print-value <key>`.
# Used by the ingest shell scripts to resolve paths through the same config layer.
# Must never trace on a broken/missing config: cron consumers need a usable value
# on stdout, so any load failure prints the built-in default.
# --------------------------------------------------------------------------- #
_CLI_PATH_KEYS: tuple = (
    "obsidian_raw",
    "obsidian_unprocessed",
    "obsidian_change_summary",
    "obsidian_wiki",
    "claude_config_dir",   # §78
    "headless_cwd",        # §78
)

# 标量设置的 shell 消费面：key → 读不出来时打的出厂值（None = 由专门的函数算）。
_CLI_VALUE_KEYS: dict = {
    "screenpipe_media_retention_minutes": DEFAULT_MEDIA_RETENTION_MINUTES,
    "claude_bin": None,    # §78：ingest 与 recap 用同一个解析结果
}


def _fallback_claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def _print_value(key: str) -> int:
    """`--print-value <key>`：一个标量设置的生效值（overrides → config.yaml → 默认）。
    任何加载失败都打出厂值，绝不 traceback、绝不空行。"""
    if key == "claude_bin":
        try:
            value = resolve_claude_bin()
        except Exception:  # noqa: BLE001 — silent-on-error: print the fallback
            value = _fallback_claude_bin()
        print(value)
        return 0
    try:
        value = getattr(load_config(), key)
    except Exception:  # noqa: BLE001 — silent-on-error: print the default
        value = None
    print(_CLI_VALUE_KEYS[key] if value is None else value)
    return 0


def _cli_default_path(key: str) -> str:
    vault = Path(DEFAULT_OBSIDIAN_VAULT).expanduser()
    if key == "obsidian_raw":
        return str(vault / "2 - raw")
    return str(vault / _OBSIDIAN_DIR_NAMES[key])


# §78 的两个路径不经 load_config（不读 config.yaml，永远可用）
_CLI_ISOLATION_PATHS: dict = {
    "claude_config_dir": lambda: str(claude_config_dir()),
    "headless_cwd": headless_cwd,
}


def _print_path(key: str) -> int:
    if key in _CLI_ISOLATION_PATHS:
        print(_CLI_ISOLATION_PATHS[key]())
        return 0
    try:
        value = getattr(load_config(), key)
    except Exception:  # noqa: BLE001 — silent-on-error: print the default
        value = None
    if not (value and str(value).strip()):
        value = _cli_default_path(key)
    resolved = Path(str(value)).expanduser()
    if not resolved.is_absolute():
        # 相对路径按 AIASSISTANT_HOME 锚定（cron 的 cwd 不定）。
        resolved = HOME / resolved
    print(resolved)
    return 0


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m act.lib.config",
        description="Print a resolved config path/value for shell consumers.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--print-path",
        choices=_CLI_PATH_KEYS,
        metavar="KEY",
        help="config key to resolve: %s" % ", ".join(_CLI_PATH_KEYS),
    )
    group.add_argument(
        "--print-value",
        choices=tuple(_CLI_VALUE_KEYS),
        metavar="KEY",
        help="scalar setting to print: %s" % ", ".join(_CLI_VALUE_KEYS),
    )
    args = parser.parse_args(argv)
    if args.print_value:
        return _print_value(args.print_value)
    return _print_path(args.print_path)


if __name__ == "__main__":
    raise SystemExit(main())
