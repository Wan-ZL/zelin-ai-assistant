"""server/settings_catalog.py — 设置页的通用 section 目录（CONTRACT §15.3 / §49 / §68.1）。

凡是「一把旋钮 = settings_overrides.json 的一个键」的设置都收进这一张 server-owned 目录：
每个 section 一组 field，每个 field 说明自己的 wire 键、类型、可选值、默认值、config.yaml
落点、override 拼法与文案（zh/en 两键——防腐 #10：文案进 server-owned catalog，web 只逐字
镜像、按 UI 语言取键）。web 设置页据此**通用渲染**（bool → 开关、enum → 下拉、string /
number → 输入框、list → 逗号分隔输入框），新增一个旋钮 = 目录里加一行，前端零改动。

v2.0.0 的 section（§68.1 修订）：general（语言）/ recording（新增 add-only 字段
``recording.ignored_apps``）/ storage（§72 两把保留期）/ obsidian / notifications（§28 安静
时段）/ updates（§26 ``check_enabled`` + §79 ``auto_install``）/ redaction。凭证（§68.3）、
会议纪要（§63）、模型（§59）各有自己的路由（``/api/secrets*``、``/api/settings/recap``、
``/api/settings/models``）。退役（全文见 tag full）：slack / gmail / telemetry / digest /
approval / flags / voice / maintainer 八个 section、§48.1 雷达源开关合取写、开发者区动态
placeholder 与 ``terminal_app_name``、email / session_id 两种 check。

读：``GET /api/settings`` 全目录 + 每 field 的 effective 值与来源
（override / config / default，三层与 ``act/lib/config._apply_settings_overrides``
同一优先级）；``GET /api/settings/{section}`` 单 section。
写：``PUT /api/settings/{section}`` body = ``{key: value}`` 子集；未知键 400
UNKNOWN_FIELD；类型/取值不合法 400 INVALID_FIELD；落盘按 §15.3 v0.14
**diff-write**——新值等于「不含该 override 的 effective 值」就**删键**，不同才写；
``write: "always"`` 的键（general.language——D37 §15 追记：语言的唯一开关，显式选择必须
落键）只要在 payload 里就落键。数字字段可带 ``bounds``（闭区间，§72.4）：PUT 越界 400，
读到的越界值按缺席落到下一层——与 ``act/lib/config`` 的同名 coercer 同一条规则。
字段可带 ``check``（词表 truth = ``CHECKS``，今日只有 ``clock_time``）：server 400 + 目录投影
双语句，web 镜像同一条规则；过闸后还要**归一**的 kind 登记在 ``_NORMALIZERS``（``9:30`` →
``09:30``）——落进 overrides 的拼法必须与 ``act/lib/config`` 读到的逐字一致。

server/ 镜像 act/lib/config.py 的 override 键名、config 路径与默认值，判例
tests/test_server_settings_catalog.py 钉住每个键都在 ``_OVERRIDE_FIELDS`` / list 白名单里、
默认值与 Config 数据类逐字一致。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML absent: config.yaml layer is skipped
    yaml = None  # type: ignore[assignment]

from server import paths, settings
from server.errors import InvalidFieldError, NotFoundError, UnknownFieldError

STRING_MAX = 1024
LIST_MAX = 200          # list 字段：项数与每项长度的帽

# 与 act/lib/config.py _BOOL_TRUE_WORDS / _BOOL_FALSE_WORDS 同一词表（config.yaml 层容忍字符串拼法）
_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0"})

# 字段校验词表（add-only，§68.1 追记）：kind → 不合格时的双语句（server-owned，防腐 #10）。目录投影
# `check: {kind, message{zh,en}}`，web 保存前按同一条规则拦、显示同一句；server 侧 400 是非 web 客户端的兜底。
# v2.0.0：email（Gmail 地址）与 session_id（开发会话）随各自的 section 退役。
CHECKS = {
    # §28 追记（issue #29）安静时段的两个端点；词法 = server.settings.CLOCK_TIME_RE
    # （act/lib/config.coerce_clock_time 同一正则，tests/test_server_paths_mirror.py 钉）。
    "clock_time": {"zh": "时间要写成 24 小时制的 HH:MM——例：22:00、08:30。",
                   "en": "Write the time as 24-hour HH:MM — e.g. 22:00, 08:30."},
}

# 一个 check 不止一句时的分句（add-only）：kind → {reason → 双语句}。今日无。
CHECK_REASONS: dict = {}

# mirror of act/lib/config.DEFAULT_IGNORED_APPS（§15 追记 v2.0.0 `recording.ignored_apps` 的出厂值）
DEFAULT_IGNORED_APPS = ["1Password", "Bitwarden", "LastPass", "KeePassXC", "Keychain Access",
                        "Private Browsing", "Incognito"]


def _f(key: str, kind: str, zh: str, en: str, *, default: Any = None,
       config: "tuple | None" = None, choices: "tuple | None" = None,
       help_zh: str = "", help_en: str = "", override: Optional[str] = None,
       write: str = "diff", placeholder: "tuple | None" = None,
       path: Optional[str] = None, check: Optional[str] = None,
       bounds: "tuple | None" = None) -> dict:
    """一条 field 描述（目录内部形；对外投影去掉 config/override/write 三个内部键）。
    ``placeholder``（add-only，zh/en 两键）= 输入框的示例文案（原生 TextField 的 prompt，如「例：you@gmail.com」）。
    ``path``（add-only；今日词表 ``"dir"``）= 这是一个目录字段：投影多带 ``path`` 与 ``path_exists``
    （effective 值展开 ``~`` 后是不是目录；空值 → null），web 据此渲染 选择… / 打开 / 创建 与
    「目录不存在」警告（原生 obsidianGroup / approvalGroup；§68.1）。
    ``check``（add-only；词表 = ``CHECKS`` 的键）= 值的形状校验：PUT 不合格 400，投影多带 ``check`` 供 web 镜像。
    ``bounds``（add-only，§72.4；数字 field 的闭区间 ``(min, max)``）= 这把旋钮的合法区间：PUT 越界 400，
    **读**的时候越界值按缺席处理（落到下一层）——act/lib/config 的同名 coercer 对越界值抛 ValueError（override
    整条跳过、yaml 回默认），两侧必须是同一条规则，否则设置页显示的数不是 cron 真用的那个。"""
    zh_ph, en_ph = placeholder or ("", "")
    if check is not None and check not in CHECKS:
        raise ValueError("unknown check kind: %s" % check)
    return {"key": key, "kind": kind, "label": {"zh": zh, "en": en},
            "help": {"zh": help_zh, "en": help_en}, "default": default,
            "choices": list(choices) if choices else None, "config": config,
            "override": override or key, "write": write, "placeholder": {"zh": zh_ph, "en": en_ph},
            "path": path, "check": check, "bounds": bounds}


def _section(sid: str, zh: str, en: str, fields: list, *, help_zh: str = "",
             help_en: str = "") -> dict:
    return {"id": sid, "title": {"zh": zh, "en": en},
            "help": {"zh": help_zh, "en": help_en}, "fields": fields}


# --------------------------------------------------------------------------- #
# 目录（顺序 = 设置页通用区的显示顺序）
# --------------------------------------------------------------------------- #
SECTIONS: tuple = (
    _section(
        "general", "通用", "General",
        [
            # D37（§15 追记 2026-09-06）：语言只有一把开关——顶栏切换 / 向导 / 这里 都写这同一个键。
            # `write="always"`：显式选择必须落键（python 侧没有持久化值时回落到 locale，不是目录里的占位 default）。
            _f("language", "enum", "界面语言", "Interface language", default="zh",
               choices=("zh", "en"), config=("language",), write="always",
               help_zh="看板、系统通知与修法句共用这一把开关：顶栏切换与这里改的都是同一个值。",
               help_en="One switch for the board, system notifications and fix sentences: the header toggle and this field write the same value."),
        ],
    ),
    _section(
        "recording", "录制", "Recording",
        [
            # §15 追记 v2.0.0（add-only）：敏感应用排除表。录制引擎启动时逐条传给 --ignored-windows（匹配的窗口
            # 根本不落盘），导出脚本对已入库的帧再过一遍同一张表（act/lib/config.recording_exclusion_sql）。
            _f("recording_ignored_apps", "list", "不录制的应用 / 窗口（逗号分隔）",
               "Apps / windows never recorded (comma-separated)", default=list(DEFAULT_IGNORED_APPS),
               config=("recording", "ignored_apps"),
               help_zh="应用名或窗口标题里包含这些词的窗口不会被录制（不分大小写；「应用::标题」可同时限定两者）。清空 = 回到出厂表。改完下一次启动录制生效。",
               help_en="Windows whose app name or title contains one of these terms are never recorded (case-insensitive; \"App::Title\" scopes both). Clearing the field restores the defaults. Takes effect the next time recording starts."),
        ],
    ),
    _section(
        "storage", "录制数据与磁盘", "Recording data & disk",
        [
            # §72.2（issue #28）：screenpipe DB 的保留期。0 = 永久保留 = 出厂默认；N ≥ 1 = cron 链的 cleanup 步删
            # 「已导出进 vault 且早于 N 天」的行（act/lib/screenpipe_retention.py）。磁盘占用走 GET /api/screenpipe/disk。
            _f("screenpipe_retention_days", "int", "录制数据保留天数", "Recording data retention (days)", default=0,
               config=("recording", "retention_days"),
               help_zh="早于此天数且已导出进笔记库的屏幕 OCR / 音频转写行会在下一次 30 分钟整理里删掉（分批、不 VACUUM：文件不立刻缩小，空间由新数据复用）；0 = 永久保留（默认）。原始 jpg / mp4 按下面那把「原始媒体保留分钟数」删，与此无关。",
               help_en="Screen OCR / audio-transcript rows older than this that are already exported to the vault are deleted on the next 30-minute tidy (batched, no VACUUM: the file does not shrink at once, new data reuses the space); 0 = keep forever (default). Raw jpg / mp4 are deleted on their own schedule below."),
            # §72.4（issue #28）：原始 jpg / mp4 的保留期。区间外 400 而不是夹取：文件里写着的数必须就是 cron 用的数。
            _f("screenpipe_media_retention_minutes", "int", "原始媒体保留分钟数", "Raw media retention (minutes)",
               default=60, config=("recording", "media_retention_minutes"),
               bounds=(5, 365 * 24 * 60),
               help_zh="截图与音频片段（~/.screenpipe/data 里的 jpg / mp4）在本机留多少分钟——OCR 文本与转写此前已经导出，删的只是原始媒体。默认 60；最短 5 分钟（整理每 30 分钟一轮，比这更短会削到同一轮里正在导出的那批帧），最长 525600（一年）。改完下一轮整理生效，无需重启。",
               help_en="How many minutes raw screenshots and audio chunks (the jpg / mp4 files under ~/.screenpipe/data) stay on this machine — their OCR text and transcripts are already exported, so only the raw media goes. Default 60; minimum 5 (the tidy runs every 30 minutes, and anything shorter cuts into the frames being exported in the same round), maximum 525600 (one year). Takes effect on the next tidy, no restart."),
        ],
        help_zh="录制引擎把 OCR 文本与音频转写攒在 ~/.screenpipe/db.sqlite，原始 jpg / mp4 攒在 ~/.screenpipe/data；这里看它们占了多少盘、每月长多少，并各给一个保留期。",
        help_en="The recording engine keeps OCR text and audio transcripts in ~/.screenpipe/db.sqlite and raw jpg / mp4 under ~/.screenpipe/data; see how much disk they take, how fast they grow, and give each a retention window.",
    ),
    _section(
        "obsidian", "笔记库", "Notes vault",
        [
            # 键存的是 raw 目录（`<根>/2 - raw`，act/lib/config.py 由它的父目录派生另外三个管线目录），但用户面对的是
            # **vault 根**（§68.1 追记）：web 显示父目录、落 `<根>/2 - raw`。
            _f("obsidian_raw", "string", "Obsidian Vault 位置", "Obsidian Vault location", default="",
               config=("sources", "obsidian_raw"), path="dir",
               placeholder=("~/Documents/Obsidian Vault", "~/Documents/Obsidian Vault"),
               help_zh="截图与录音整理成的笔记存这里。vault 内自动使用并创建 4 个标准子目录：1 - unprocessed（截图/录音落点）· 2 - raw · 3 - change-summary（变更日志）· 4 - wiki（知识库）。默认 ~/Documents/Obsidian Vault。",
               help_en="Notes made from your screen and audio captures live here. Four standard subfolders inside the vault are used (and created) automatically: 1 - unprocessed (capture exports) · 2 - raw · 3 - change-summary (change logs) · 4 - wiki (knowledge base). Default: ~/Documents/Obsidian Vault."),
        ],
    ),
    _section(
        "notifications", "通知", "Notifications",
        [
            # §28 v2.0.0：通知只剩两类——会议纪要已生成、录制 / ingest 坏到需要人修（后者穿透安静时段）。
            _f("quiet_hours_enabled", "bool", "安静时段", "Quiet hours", default=False,
               help_zh="开启后，下面的时段内不弹「会议纪要已生成」横幅；录制 / 笔记整理坏掉的通知照常弹。这一段时间的通知是丢掉，不是攒到早上：通知队列本就只留 10 分钟（§28）。纪要本身一份不少地在「会议纪要」页等你。",
               help_en="While on, no \"meeting recap ready\" banner is posted inside the window below; alerts that recording or note processing broke still post. Notifications in that window are dropped, not held until morning: the queue only keeps an entry for 10 minutes (§28). Every recap is still on the Meeting recaps page."),
            _f("quiet_hours_start", "string", "安静时段开始", "Quiet hours start", default="22:00",
               check="clock_time", placeholder=("22:00", "22:00"),
               help_zh="24 小时制 HH:MM，本机时间。开始晚于结束 = 跨午夜（例：22:00 → 08:00）。",
               help_en="24-hour HH:MM, local time. A start later than the end wraps past midnight (e.g. 22:00 → 08:00)."),
            _f("quiet_hours_end", "string", "安静时段结束", "Quiet hours end", default="08:00",
               check="clock_time", placeholder=("08:00", "08:00"),
               help_zh="24 小时制 HH:MM，本机时间。两端相同 = 零长窗，等于没开安静时段。",
               help_en="24-hour HH:MM, local time. Both ends equal = a zero-length window, i.e. quiet hours are effectively off."),
        ],
        help_zh="系统通知由 app（壳）投递（§28）；app 没开就没有系统通知。通知权限见「权限体检」。",
        help_en="System notifications are posted by the app (§28); no app running = no banners. Permission status: Permissions checkup.",
    ),
    _section(
        "updates", "更新", "Updates",
        [
            _f("updates_check_enabled", "bool", "自动检查新版本（每天最多一次）",
               "Check for updates automatically (at most once a day)", default=True,
               config=("updates", "check_enabled"),
               help_zh="至多每 24h 向 GitHub 查一次最新版本号（只暴露 IP + 当前版本号）。这是本应用唯一一处不是模型调用的出网。",
               help_en="At most one GitHub version check per 24h (exposes only your IP + current version). This is the app's only network call that is not a model call."),
            # §79 add-only：发现带 tag 的新版本时自动升级（ff-only 到 origin/main 的 tag + install.sh；永不碰录制引擎）。默认关。
            _f("updates_auto_install", "bool", "发现新版本时自动升级（默认关）",
               "Install new versions automatically (default off)", default=False,
               config=("updates", "auto_install"),
               help_zh="开启后，每日检查发现新的 release 就自动升级（只快进到带版本号的 main，本地有改动或分叉时停下并告诉你手动命令；录制不会被打断）。关着时用「关于」页的「立即升级」。",
               help_en="When on, a newer release found by the daily check is installed automatically (fast-forward only to a tagged main; it stops and tells you the manual command when the checkout has local changes or has diverged; recording is never interrupted). When off, use \"Upgrade now\" on the About page."),
        ],
    ),
    _section(
        "redaction", "脱敏（发给 AI 前本地打码）", "Redaction (local masking before sending to AI)",
        [
            _f("redaction_enabled", "bool", "启用词表脱敏 — 发出 prompt 前把词表词条替换成 [脱敏]",
               "Enable term-list redaction — replace term-list matches with [REDACTED] before sending prompts", default=False,
               config=("redaction", "enabled"),
               help_zh="打开会改变 AI 看到的内容（命中词替换为占位）。", help_en="Changes what the AI sees (matched terms become placeholders)."),
            _f("redaction_terms_file", "string", "词表文件", "Terms file", default="config/redaction_terms.txt",
               config=("redaction", "terms_file"),
               help_zh="一行一条；re: 前缀 = 正则。相对路径按管线根解析。", help_en="One term per line; re: prefix = regex. Relative paths resolve against the pipeline root."),
            _f("redaction_mask_secrets", "bool", "密钥掩码 — 内置正则 (sk-ant-/xox*/AKIA/gh*_/PEM)，始终生效，不依赖词表开关",
               "Secrets masking — built-in regexes (sk-ant-/xox*/AKIA/gh*_/PEM), always on regardless of the toggle above", default=True,
               config=("redaction", "mask_secrets"),
               help_zh="无条件掩码；不依赖词表开关。", help_en="Masked regardless of the term-list switch."),
        ],
    ),
)


def expand_user_path(raw: str) -> Path:
    """``Path(raw).expanduser()``，但 ``~nosuchuser/x`` 不炸：Python 对查不到的用户名抛 RuntimeError（原生
    ``expandingTildeInPath`` 原样返回）——config.yaml 里一个坏路径不许把整份设置快照 / 开发会话启动打成 500
    （§0 第 11 条），原样当路径用（随后的 is_dir 自然是 False）。目录字段的 ``path_exists`` 用它。"""
    path = Path(raw)
    try:
        return path.expanduser()
    except (OSError, RuntimeError, ValueError):
        return path


# 动态 placeholder（add-only；key → fn(field, config_doc) → 一句或 None）。v2.0.0 起没有登记项（开发者区退役）。
DYNAMIC_PLACEHOLDERS: "dict[str, Callable[[dict, dict], Optional[str]]]" = {}

# section 级 add-only 投影（id → fn(home) → 追加到 section 快照的键）。v2.0.0 起没有登记项。
SECTION_EXTRAS: "dict[str, Callable[[Path], dict]]" = {}

_BY_ID = {s["id"]: s for s in SECTIONS}


def section_ids() -> list:
    return [s["id"] for s in SECTIONS]


def field_index(section: dict) -> dict:
    return {f["key"]: f for f in section["fields"]}


# --------------------------------------------------------------------------- #
# overrides / config 读取
# --------------------------------------------------------------------------- #
def read_overrides(home: Path) -> dict:
    """overrides 文档，缺席 = {}；坏文件 → 409 CONFLICT（与 §59 模块同一读法——同一文件同一纪律）。"""
    return settings.read_overrides(home)


def write_overrides(home: Path, doc: dict) -> None:
    settings.atomic_write_json(paths.settings_overrides_path(home), doc)


def load_config_doc(home: Path) -> dict:
    """config.yaml 的 dict（PyYAML 缺席 / 文件缺席 / 坏 yaml → {}——管线同样降级）。"""
    if yaml is None:
        return {}
    try:
        doc = yaml.safe_load(paths.config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _walk(doc: dict, path: tuple):
    cur: Any = doc
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def walk_config(doc: dict, path: tuple):
    """config.yaml 文档按键路径取值（缺席 None）——其它 server 模块读一条非目录键时用（防腐 #2：不引 _私名）。"""
    return _walk(doc, path)


def _split_override(spelling: str) -> "tuple[Optional[str], str]":
    """``"telemetry.enabled"`` → ("telemetry", "enabled")；扁平键 → (None, key)。"""
    if "." in spelling:
        block, sub = spelling.split(".", 1)
        return block, sub
    return None, spelling


def override_raw(overrides: dict, field: dict):
    """该 field 在 overrides 里的原始值（嵌套块优先，再扁平点号键；缺 = None）。"""
    block, sub = _split_override(field["override"])
    if block is None:
        return overrides.get(sub)
    nested = overrides.get(block)
    if isinstance(nested, dict) and nested.get(sub) is not None:
        return nested.get(sub)
    return overrides.get(field["override"])


# --------------------------------------------------------------------------- #
# 值归一（容忍层：config.yaml / overrides 里的旧拼法）
# --------------------------------------------------------------------------- #
def _word_bool(text: str) -> Optional[bool]:
    v = text.strip().lower()
    if v in _TRUE_WORDS:
        return True
    return False if v in _FALSE_WORDS else None


def _coerce_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return _word_bool(value) if isinstance(value, str) else None


def _finite_number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _coerce_number(value, integer: bool):
    num = _finite_number(value)
    if num is None or not integer:
        return num
    return int(num) if num.is_integer() else None


def _coerce_enum(field: dict, value) -> Optional[str]:
    s = str(value).strip().lower()
    return s if s in (field["choices"] or ()) else None


def _coerce_string(value) -> Optional[str]:
    text = str(value)
    return text if text.strip() else None


def _list_item(item) -> Optional[str]:
    """一个 list 元素的投影：{id, ...} 字典取 id（旧形状兼容）；字串 / 整数取 strip 后的字串；其余丢。"""
    if isinstance(item, dict):
        return str(item["id"]) if item.get("id") else None
    if isinstance(item, (str, int)) and str(item).strip():
        return str(item).strip()
    return None


def _coerce_list(value) -> Optional[list]:
    """list 字段：文件里是字串表；空表 = 缺席。"""
    if not isinstance(value, list):
        return None
    out = [text for text in (_list_item(item) for item in value) if text]
    return out or None


_COERCERS = {
    "bool": lambda field, value: _coerce_bool(value),
    "enum": _coerce_enum,
    "number": lambda field, value: _coerce_number(value, False),
    "int": lambda field, value: _coerce_number(value, True),
    "string": lambda field, value: _coerce_string(value),
    "list": lambda field, value: _coerce_list(value),
}


def coerce(field: dict, value):
    """按 field.kind 归一一个来自文件的值；归一失败 / 越界（``bounds``）→ None（调用方视为缺席）。"""
    if value is None:
        return None
    got = _COERCERS[field["kind"]](field, value)
    return None if got is not None and out_of_bounds(field, got) else got


def out_of_bounds(field: dict, value) -> bool:
    """``bounds`` 闭区间外 = True（没登记区间的 field 恒 False）。公开名：写闸与读归一同用一把。"""
    low_high = field.get("bounds")
    if low_high is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return not (low_high[0] <= value <= low_high[1])


def base_effective(field: dict, config_doc: dict) -> "tuple[Any, str]":
    """不含 override 的 effective 值：config.yaml → default。返回 (value, source)。"""
    if field["config"] is not None:
        got = coerce(field, _walk(config_doc, field["config"]))
        if got is not None:
            return got, "config"
    return field["default"], "default"


def effective(field: dict, overrides: dict, config_doc: dict) -> "tuple[Any, str]":
    got = coerce(field, override_raw(overrides, field))
    if got is not None:
        return got, "override"
    return base_effective(field, config_doc)


# --------------------------------------------------------------------------- #
# 投影
# --------------------------------------------------------------------------- #
_PUBLIC_FIELD_KEYS = ("key", "kind", "label", "help", "default", "choices", "placeholder")


def path_exists(value: Any) -> Optional[bool]:
    """目录字段的存在性：非空字串展开 ``~`` 后 ``is_dir()``（``~nosuchuser`` 不炸，expand_user_path）；空 / 非字串 → None（无从判断）。"""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return None
    try:
        return expand_user_path(raw).is_dir()
    except (OSError, ValueError):
        return False


def check_projection(kind: str) -> dict:
    """``check`` 的投影形：``{kind, message{zh,en}}``，多句的 kind 另带 ``reasons{reason: {zh,en}}``（add-only）。"""
    out = {"kind": kind, "message": dict(CHECKS[kind])}
    reasons = CHECK_REASONS.get(kind)
    if reasons:
        out["reasons"] = {reason: dict(sentence) for reason, sentence in reasons.items()}
    return out


def _project_field(field: dict, overrides: dict, config_doc: dict) -> dict:
    out = {k: field[k] for k in _PUBLIC_FIELD_KEYS}
    value, source = effective(field, overrides, config_doc)
    out["effective"] = value
    out["source"] = source
    dynamic = DYNAMIC_PLACEHOLDERS.get(field["key"])
    if dynamic is not None:
        # 动态灰字（§68.7 追记）：生效默认（config 层 / 内建）算出来的一句盖过目录里的静态句；None = 静态句照旧
        hint = dynamic(field, config_doc)
        if hint is not None:
            out["placeholder"] = {"zh": hint, "en": hint}
    if field.get("path"):
        # add-only（§68.1 目录字段）：web 的 选择… / 打开 / 创建 与「目录不存在」警告据此渲染
        out["path"] = field["path"]
        out["path_exists"] = path_exists(value)
    if field.get("check"):
        # add-only（§68.1 追记）：web 保存前镜像同一条形状校验、显示同一句 server-owned 文案
        out["check"] = check_projection(field["check"])
    if field.get("bounds"):
        # add-only（§72.4）：数字旋钮的合法闭区间——web 的 min / max 与「保存」闸镜像同一对数
        low, high = field["bounds"]
        out["bounds"] = {"min": low, "max": high}
    return out


def project_section(home: Path, section: dict) -> dict:
    overrides = read_overrides(home)
    config_doc = load_config_doc(home)
    out = {"id": section["id"], "title": dict(section["title"]),
           "help": dict(section["help"]),
           "fields": [_project_field(f, overrides, config_doc) for f in section["fields"]]}
    extras = SECTION_EXTRAS.get(section["id"])
    if extras is not None:
        out.update(extras(home))
    return out


def snapshot(home: Path) -> dict:
    """``GET /api/settings``：``{"sections": [...]}``（顺序 = 目录顺序）。"""
    return {"sections": [project_section(home, s) for s in SECTIONS]}


def lookup(section_id: str) -> dict:
    section = _BY_ID.get(section_id)
    if section is None:
        raise NotFoundError("unknown settings section", {"section": section_id})
    return section


def effective_value(home: Path, section_id: str, key: str):
    """其它 server 模块读一把旋钮的 effective 值（override → config.yaml → default）。"""
    field = field_index(lookup(section_id))[key]
    value, _src = effective(field, read_overrides(home), load_config_doc(home))
    return value


def section_snapshot(home: Path, section_id: str) -> dict:
    return project_section(home, lookup(section_id))


# --------------------------------------------------------------------------- #
# 写：校验 + diff-write
# --------------------------------------------------------------------------- #
def _validate_bool(value, key: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidFieldError("%s must be true or false" % key, {"field": key})
    return value


def _validate_enum(field: dict, value, key: str) -> str:
    if not isinstance(value, str) or value not in (field["choices"] or ()):
        raise InvalidFieldError("%s must be one of %s" % (key, ", ".join(field["choices"] or ())),
                                {"field": key})
    return value


def _validate_number(field: dict, value, key: str):
    got = _coerce_number(value, field["kind"] == "int")
    if got is not None and out_of_bounds(field, got):
        # §72.4：越界不夹取——夹取会让文件里的数与管线真用的数不是一个（act 侧对越界值
        # 抛 ValueError = 整条跳过）。区间比「非负」更具体，所以先说这一句（负数也走这里）。
        low, high = field["bounds"]
        raise InvalidFieldError("%s must be between %s and %s" % (key, low, high),
                                {"field": key, "min": low, "max": high})
    if got is None or got < 0:
        raise InvalidFieldError("%s must be a non-negative %s" % (
            key, "integer" if field["kind"] == "int" else "number"), {"field": key})
    return got


def _validate_string(value, key: str) -> Optional[str]:
    if not isinstance(value, str):
        raise InvalidFieldError("%s must be a string" % key, {"field": key})
    if len(value) > STRING_MAX:
        raise InvalidFieldError("%s is too long (max %d chars)" % (key, STRING_MAX), {"field": key})
    if "\n" in value or "\x00" in value:
        raise InvalidFieldError("%s must be a single line" % key, {"field": key})
    return value.strip() or None   # 空串 = 清掉 override


# kind → checker(text) → None（合格）或 reason 词（在 CHECK_REASONS[kind] 里就用分句，否则用 CHECKS[kind] 主句）
_CHECKERS: "dict[str, Callable[[str], Optional[str]]]" = {
    "clock_time": lambda text: None if settings.CLOCK_TIME_RE.match(text.strip()) else "shape",
}


# check 过闸后的归一（add-only）：kind → normalizer。落盘拼法必须与 act/lib/config 读到的逐字一致——
# `9:30` 与 `09:30` 是同一个时刻，但 diff-write 与守护进程各读各的字面量（§28 追记 2026-09-12）。
_NORMALIZERS: "dict[str, Callable[[str], str]]" = {
    "clock_time": settings.coerce_clock_time,   # 与 §70 每日循环端点同一把归一器
}


def normalize_check(field: dict, value: Optional[str]) -> Optional[str]:
    """``check`` 过闸后的归一（空值 = 清键，不动）；没登记归一器的 kind 原样返回。"""
    norm = _NORMALIZERS.get(field.get("check") or "")
    if norm is None or value is None:
        return value
    return norm(value)


def run_check(field: dict, value: Optional[str], key: str) -> None:
    """``check`` 字段的形状校验（空值 = 清键，不查）；不合格 → 400，message 双语并列（server/settings.py 同款），
    details 带 ``check`` 词（多句的 kind 再带 ``reason``）让客户端能对上目录里的那句。"""
    kind = field.get("check")
    if kind is None or value is None:
        return
    reason = _CHECKERS[kind](value)
    if reason is None:
        return
    details = {"field": key, "check": kind}
    sentence = CHECK_REASONS.get(kind, {}).get(reason)
    if sentence is not None:
        details["reason"] = reason
    else:
        sentence = CHECKS[kind]
    raise InvalidFieldError("%s / %s" % (sentence["zh"], sentence["en"]), details)


def _split_list_input(value) -> list:
    """web 输入框给逗号 / 换行分隔的一个字串，JSON 客户端给字串表——两种入站形归一成字串表。"""
    if isinstance(value, str):
        return value.replace("\n", ",").split(",")
    return value if isinstance(value, list) else [value]


def _check_list_bounds(items: list, key: str) -> None:
    """帽：≤200 项、每项 ≤200 字（防无界 payload）。"""
    if len(items) > LIST_MAX or any(len(item) > LIST_MAX for item in items):
        raise InvalidFieldError("%s has too many / too long entries" % key, {"field": key})


def _validate_list(value, key: str) -> Optional[list]:
    """list 入站形：JSON 字串表，或一个逗号 / 换行分隔的字串（web 输入框）；空表 = 清掉 override。"""
    parts = _split_list_input(value)
    if any(not isinstance(item, str) for item in parts):
        raise InvalidFieldError("%s must be a list of strings" % key, {"field": key})
    items = [item.strip() for item in parts if item.strip()]
    _check_list_bounds(items, key)
    return items or None   # 空表 = 清掉 override（回到 config / 出厂表）


def validate(field: dict, value):
    """PUT 入站值校验（严格：bool 只认 JSON 布尔，enum 只认 choices）。string / list 空 → None。"""
    kind, key = field["kind"], field["key"]
    if kind == "bool":
        return _validate_bool(value, key)
    if kind == "enum":
        return _validate_enum(field, value, key)
    if kind in ("number", "int"):
        return _validate_number(field, value, key)
    if kind == "list":
        return _validate_list(value, key)
    text = _validate_string(value, key)
    run_check(field, text, key)
    return normalize_check(field, text)


def _drop_override(overrides: dict, field: dict) -> None:
    block, sub = _split_override(field["override"])
    if block is None:
        overrides.pop(sub, None)
        return
    overrides.pop(field["override"], None)
    nested = overrides.get(block)
    if isinstance(nested, dict):
        nested.pop(sub, None)
        if not nested:
            overrides.pop(block, None)


def _set_override(overrides: dict, field: dict, value) -> None:
    block, sub = _split_override(field["override"])
    if block is None:
        overrides[sub] = value
        return
    overrides.pop(field["override"], None)   # 同义扁平键让位（两拼法不共存）
    nested = overrides.get(block)
    if not isinstance(nested, dict):
        nested = {}
        overrides[block] = nested
    nested[sub] = value


def apply_field(overrides: dict, field: dict, value, config_doc: dict) -> None:
    """一把旋钮的 diff-write：None（清）或等于 config 层 effective → 删键；否则写。
    ``write: "always"`` 的键只要给了非 None 值就落键（知情选择不得被静默撤销）。"""
    if value is None:
        _drop_override(overrides, field)
        return
    base, _src = base_effective(field, config_doc)
    if field["write"] != "always" and value == base:
        _drop_override(overrides, field)
    else:
        _set_override(overrides, field, value)


def update_section(home: Path, section_id: str, payload: dict) -> dict:
    """``PUT /api/settings/{section}``：校验全部键后一次落盘；返回该 section 的新快照。"""
    section = lookup(section_id)
    index = field_index(section)
    unknown = set(payload) - set(index)
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if not payload:
        raise InvalidFieldError("nothing to save: give at least one field")
    wanted = {key: validate(index[key], value) for key, value in payload.items()}
    overrides = read_overrides(home)
    config_doc = load_config_doc(home)
    for key, value in wanted.items():
        apply_field(overrides, index[key], value, config_doc)
    write_overrides(home, overrides)
    return project_section(home, section)
