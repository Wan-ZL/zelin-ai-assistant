"""server/settings_catalog.py — 设置页的通用 section 目录（CONTRACT §15.3 / §49 / §68）。

原生 Settings.swift 的 20 个区里，凡是「一把旋钮 = settings_overrides.json 的一个
键」的都收进这一张 server-owned 目录：每个 section 一组 field，每个 field 说明
自己的 wire 键、类型、可选值、默认值、config.yaml 落点、override 拼法与文案
（zh/en 两键——防腐 #10：文案进 server-owned catalog，web 只逐字镜像、按 UI
语言取键）。web 设置页据此**通用渲染**（bool → 开关、enum → 下拉、string /
number → 输入框），新增一个旋钮 = 目录里加一行，前端零改动。

读：``GET /api/settings`` 全目录 + 每 field 的 effective 值与来源
（override / config / default，三层与 ``act/lib/config._apply_settings_overrides``
同一优先级）；``GET /api/settings/{section}`` 单 section。
写：``PUT /api/settings/{section}`` body = ``{key: value}`` 子集；未知键 400
UNKNOWN_FIELD；类型/取值不合法 400 INVALID_FIELD；落盘按 §15.3 v0.14
**diff-write**——新值等于「不含该 override 的 effective 值」就**删键**，不同才写；
``write: "always"`` 的键（telemetry.capture_input——知情选择不可被静默 diff-drop）
只要在 payload 里就落键。nested 拼法（``telemetry`` / ``features`` 块）写嵌套形并
顺手清掉同义的扁平点号键（两种拼法 Python 都读，同文件出现两份会让读者各说各话）。

server/ 不 import act（§49）：override 键名、config 路径与默认值镜像自
act/lib/config.py，判例 tests/test_server_settings_catalog.py 钉住每个键都在
``_OVERRIDE_FIELDS`` / 嵌套白名单里、默认值与 Config 数据类逐字一致。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML absent: config.yaml layer is skipped
    yaml = None  # type: ignore[assignment]

from server import paths, settings
from server.errors import InvalidFieldError, NotFoundError, UnknownFieldError

STRING_MAX = 1024

# 与 act/lib/config.py _BOOL_TRUE_WORDS / _BOOL_FALSE_WORDS 同一词表（config.yaml 层容忍字符串拼法）
_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0"})


def _f(key: str, kind: str, zh: str, en: str, *, default: Any = None,
       config: "tuple | None" = None, choices: "tuple | None" = None,
       help_zh: str = "", help_en: str = "", override: Optional[str] = None,
       write: str = "diff") -> dict:
    """一条 field 描述（目录内部形；对外投影去掉 config/override/write 三个内部键）。"""
    return {"key": key, "kind": kind, "label": {"zh": zh, "en": en},
            "help": {"zh": help_zh, "en": help_en}, "default": default,
            "choices": list(choices) if choices else None, "config": config,
            "override": override or key, "write": write}


def _section(sid: str, zh: str, en: str, fields: list, *, help_zh: str = "",
             help_en: str = "") -> dict:
    return {"id": sid, "title": {"zh": zh, "en": en},
            "help": {"zh": help_zh, "en": help_en}, "fields": fields}


# --------------------------------------------------------------------------- #
# 目录（顺序 = 设置页通用区的显示顺序）
# --------------------------------------------------------------------------- #
_FLAGS = ("slack_radar", "gmail_radar", "obsidian_radar", "digest", "auto_resume",
          "analytics", "feedback_sync", "auto_deploy")

SECTIONS: tuple = (
    _section(
        "sources", "来源开关", "Sources",
        [
            _f("gmail_enabled", "bool", "Gmail 雷达", "Gmail radar", default=True,
               config=("sources", "gmail", "enabled"),
               help_zh="读取收件箱未读邮件提炼需求卡（只读，不发信）。凭证在下方「Gmail 应用专用密码」。",
               help_en="Reads unread inbox mail into proposal cards (read-only, never sends). Credential: the Gmail app password below."),
            _f("gmail_address", "string", "Gmail 地址", "Gmail address", default="",
               config=("sources", "gmail", "address"),
               help_zh="IMAP 登录用的邮箱地址；留空 = 用 config.yaml 里的值。",
               help_en="Address used for the IMAP login; blank = whatever config.yaml says."),
            _f("slack_enabled", "bool", "Slack 雷达", "Slack radar", default=True,
               config=("sources", "slack", "enabled"),
               help_zh="扫描 DM / @提及 / 自发消息（手机端快速捕获入口）。凭证在下方「Slack user token」。",
               help_en="Scans DMs / @mentions / self-DMs (the phone capture inlet). Credential: the Slack user token below."),
            _f("obsidian_enabled", "bool", "Obsidian 雷达", "Obsidian radar", default=True,
               config=("sources", "obsidian", "enabled"),
               help_zh="扫描笔记库 raw 目录里的新笔记（屏幕/会议 ingest 的落点）。",
               help_en="Scans new notes in the vault's raw folder (where screen / meeting ingest lands)."),
            _f("obsidian_raw", "string", "笔记库 raw 目录", "Vault raw folder", default="",
               config=("sources", "obsidian_raw"),
               help_zh="雷达扫描源；其余三个管线目录由它的上级（vault 根）自动派生。",
               help_en="The radar's scan source; the other three pipeline folders derive from its parent (the vault root)."),
        ],
        help_zh="每个源 = feature flag × 源开关（§48）；这里是源开关，flag 在「Feature flags」。真正生效的状态看每行右侧的健康摘要。",
        help_en="Each source = feature flag × source switch (§48); these are the switches, flags live under \"Feature flags\". The health line on the right shows what is actually in effect.",
    ),
    _section(
        "notifications", "通知", "Notifications",
        [
            _f("review_notify", "enum", "任务完成提醒", "Task-done alerts", default="sound",
               choices=("off", "banner", "sound"),
               help_zh="卡片进入「待验收」时的系统通知：关 / 横幅 / 横幅 + 提示音（默认）。其余通知不受影响。",
               help_en="System notification when a card reaches In review: off / banner / banner + sound (default). Other notifications are unaffected."),
        ],
        help_zh="系统通知由看板 app（壳）投递（§28）；app 没开就没有系统通知。通知权限见「权限体检」。",
        help_en="System notifications are posted by the board app (§28); no app running = no banners. Permission status: Permissions checkup.",
    ),
    _section(
        "telemetry", "产品改进计划", "Product improvement program",
        [
            _f("telemetry.enabled", "bool", "匿名使用统计", "Anonymous usage stats", default=True,
               config=("telemetry", "enabled"),
               help_zh="默认开：只上传事件元数据（事件名 / 耗时 / 计数）。关 = 完全不上传。",
               help_en="On by default: uploads event metadata only (names / durations / counts). Off = nothing is uploaded."),
            _f("telemetry.level", "enum", "行为事件级别", "Behavior event level", default="detailed",
               choices=("basic", "detailed"), config=("telemetry", "level"),
               help_zh="basic / detailed 都只是元数据粒度；切 basic 同时停掉输入文本上传。",
               help_en="basic / detailed are both metadata-only granularity; basic also switches off typed-text upload."),
            _f("telemetry.capture_input", "bool", "上传我输入的文本", "Upload my typed text", default=False,
               config=("telemetry", "capture_input"), write="always",
               help_zh="默认关（opt-in）。开 = 你输入进本 app 的文字（捕获 / 打回反馈 / 搜索词，≤500 字符）随事件上传；绝不含 AI 回答、屏幕内容、邮件、Slack 消息、密钥。",
               help_en="Off by default (opt-in). On = text you type into this app (captures / rework feedback / search terms, ≤500 chars) travels with events; never AI output, screen content, mail, Slack messages or secrets."),
        ],
        help_zh="字段表与边界见 docs/TELEMETRY.md。",
        help_en="Field table and boundaries: docs/TELEMETRY.md.",
    ),
    _section(
        "digest", "摘要与回顾", "Digests",
        [
            _f("digest_frequency", "enum", "状态摘要频率", "Status digest cadence", default="off",
               choices=("off", "daily", "every2days", "weekly"), config=("digest", "frequency"),
               help_zh="待审批 / 待验收积压 + 卡住任务 + 欠账的一张摘要卡。默认 off（D19）。",
               help_en="One card summarizing approvals / review backlog, stuck tasks and debts. Default off (D19)."),
            _f("weekly_digest_enabled", "bool", "每周回顾卡", "Weekly recap card", default=False,
               config=("sources", "weekly_digest", "enabled"),
               help_zh="近 7 天 ingest 的回顾卡（待验收列）；没有新数据时自动跳过。",
               help_en="A recap card over the last 7 days of ingest (In review lane); skipped when nothing new landed."),
        ],
    ),
    _section(
        "general", "通用", "General",
        [
            _f("language", "enum", "界面语言", "Interface language", default="zh",
               choices=("zh", "en"), config=("language",),
               help_zh="Python 侧文案（通知 / 修法句）跟随此值；看板自己的语言由顶栏切换。",
               help_en="Python-side copy (notifications / fix sentences) follows this; the board's own language is the header toggle."),
            _f("default_output_format", "enum", "交付物默认格式", "Deliverable format", default="markdown",
               choices=("markdown", "html"), config=("default_output_format",),
               help_zh="以你名义起草文档 / 报告时用哪种标记语言。",
               help_en="Which markup drafts and reports written in your name use."),
            _f("updates_check_enabled", "bool", "自动检查新版本", "Check for updates", default=True,
               config=("updates", "check_enabled"),
               help_zh="至多每 24h 向 GitHub 查一次最新版本号（只暴露 IP + 当前版本号）。",
               help_en="At most one GitHub version check per 24h (exposes only your IP + current version)."),
        ],
    ),
    _section(
        "approval", "审批 / 成本", "Approval / Cost",
        [
            _f("default_target_repo", "string", "任务默认工作目录", "Default task folder",
               default="~/Projects/your-workbench", config=("execution", "default_target_repo"),
               help_zh="卡片没指定落点时的兜底目录（文书 / 调研的家；绝不默认进你的代码 repo）。",
               help_en="Fallback folder when a card names no target (home for paperwork / research; never your code repo by default)."),
            _f("skip_permissions", "bool", "后台 agent 免确认运行", "Agents run unattended", default=True,
               config=("execution", "skip_permissions"),
               help_zh="claude --bg 带 --dangerously-skip-permissions；关 = 被挡住的 agent 会等你确认。",
               help_en="claude --bg passes --dangerously-skip-permissions; off = blocked agents wait for you."),
            _f("create_github_repo", "bool", "允许自动创建 GitHub 私有仓库", "Allow creating private GitHub repos", default=False,
               config=("execution", "create_github_repo"),
               help_zh="target_kind=new 的卡自动 gh repo create --private（draft PR 需要）。默认关。",
               help_en="target_kind=new cards run gh repo create --private (needed for draft PRs). Off by default."),
            _f("show_cost_above_usd", "number", "显示成本阈值（USD）", "Show cost above (USD)", default=5.0,
               config=("approval", "cost_thresholds", "show_cost_above_usd"),
               help_zh="低于此值卡片不显示预估费用。", help_en="Cards below this estimate hide the cost chip."),
            _f("require_text_confirm_above_usd", "number", "文字确认阈值（USD）", "Typed confirm above (USD)", default=50.0,
               config=("approval", "cost_thresholds", "require_text_confirm_above_usd"),
               help_zh="高于此值升 T2：批准要输入确认词。", help_en="Above this the card is T2: approval needs a typed confirmation."),
            _f("trash_retention_days", "int", "回收站保留天数", "Trash retention (days)", default=60,
               config=("trash", "retention_days"),
               help_zh="超期且未标永久的卡硬删；0 = 永不自动清。", help_en="Unpinned cards older than this are purged; 0 = never."),
        ],
    ),
    _section(
        "flags", "Feature flags（§16，默认全开）", "Feature flags (§16, all on by default)",
        [_f("features." + flag, "bool", flag, flag, default=True, config=("features", flag))
         for flag in _FLAGS],
        help_zh="总开关层：关掉 analytics = 本机不再写任何行为事件；关掉 auto_deploy = install.sh 不再装自动部署 agent。",
        help_en="Master switches: analytics off = no behavior events are written locally; auto_deploy off = install.sh stops installing the deploy agent.",
    ),
    _section(
        "redaction", "脱敏（发给 AI 前本地打码）", "Redaction (local masking before the AI sees it)",
        [
            _f("redaction_enabled", "bool", "词表脱敏", "Term-list redaction", default=False,
               config=("redaction", "enabled"),
               help_zh="打开会改变 AI 看到的内容（命中词替换为占位）。", help_en="Changes what the AI sees (matched terms become placeholders)."),
            _f("redaction_terms_file", "string", "词表文件", "Terms file", default="config/redaction_terms.txt",
               config=("redaction", "terms_file"),
               help_zh="一行一条；re: 前缀 = 正则。相对路径按管线根解析。", help_en="One term per line; re: prefix = regex. Relative paths resolve against the pipeline root."),
            _f("redaction_mask_secrets", "bool", "内置密钥掩码", "Built-in secret masking", default=True,
               config=("redaction", "mask_secrets"),
               help_zh="sk-ant- / xox* / AKIA / gh*_ / PEM 无条件掩码；不依赖词表开关。", help_en="sk-ant- / xox* / AKIA / gh*_ / PEM are masked regardless of the term-list switch."),
        ],
    ),
    _section(
        "voice", "语气档案", "Voice profile",
        [
            _f("voice_enabled", "bool", "启用语气注入", "Voice injection", default=True,
               config=("voice", "enabled"),
               help_zh="以你的口吻起草 Slack 回复 / 邮件（docs/VOICE.md）。档案生成：终端 python3 -m act.voice_gen。",
               help_en="Drafts Slack replies / mail in your voice (docs/VOICE.md). Generate the profile: python3 -m act.voice_gen in a terminal."),
        ],
    ),
    _section(
        "maintainer", "开发者 · 开发会话", "Developer session",
        [
            _f("maintainer_repo_path", "string", "本软件的仓库路径", "This software's repo path", default="",
               config=("maintainer", "repo_path"),
               help_zh="「让 AI 修」与开发会话打开的仓库；留空 = 当前 checkout。",
               help_en="Repo opened by Fix with AI and developer sessions; blank = this checkout."),
            _f("maintainer_session_id", "string", "续接的会话 id", "Session id to resume", default="",
               config=("maintainer", "session_id"),
               help_zh="填了就 claude --resume 这个会话，留空开新会话。",
               help_en="When set the session is resumed with claude --resume; blank starts fresh."),
        ],
    ),
)

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


_COERCERS = {
    "bool": lambda field, value: _coerce_bool(value),
    "enum": _coerce_enum,
    "number": lambda field, value: _coerce_number(value, False),
    "int": lambda field, value: _coerce_number(value, True),
    "string": lambda field, value: _coerce_string(value),
}


def coerce(field: dict, value):
    """按 field.kind 归一一个来自文件的值；归一失败 → None（调用方视为缺席）。"""
    if value is None:
        return None
    return _COERCERS[field["kind"]](field, value)


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
_PUBLIC_FIELD_KEYS = ("key", "kind", "label", "help", "default", "choices")


def _project_field(field: dict, overrides: dict, config_doc: dict) -> dict:
    out = {k: field[k] for k in _PUBLIC_FIELD_KEYS}
    value, source = effective(field, overrides, config_doc)
    out["effective"] = value
    out["source"] = source
    return out


def project_section(home: Path, section: dict) -> dict:
    overrides = read_overrides(home)
    config_doc = load_config_doc(home)
    return {"id": section["id"], "title": dict(section["title"]),
            "help": dict(section["help"]),
            "fields": [_project_field(f, overrides, config_doc) for f in section["fields"]]}


def snapshot(home: Path) -> dict:
    """``GET /api/settings``：``{"sections": [...]}``（顺序 = 目录顺序）。"""
    return {"sections": [project_section(home, s) for s in SECTIONS]}


def lookup(section_id: str) -> dict:
    section = _BY_ID.get(section_id)
    if section is None:
        raise NotFoundError("unknown settings section", {"section": section_id})
    return section


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


def validate(field: dict, value):
    """PUT 入站值校验（严格：bool 只认 JSON 布尔，enum 只认 choices）。string 空 → None。"""
    kind, key = field["kind"], field["key"]
    if kind == "bool":
        return _validate_bool(value, key)
    if kind == "enum":
        return _validate_enum(field, value, key)
    if kind in ("number", "int"):
        return _validate_number(field, value, key)
    return _validate_string(value, key)


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


def set_flat_override(home: Path, key: str, value: str) -> None:
    """其它 server 模块的窄写口（Slack auth.test 自动填 ``owner_slack_user_id``，§15.3
    v0.14）：change-write 一个扁平 str 键，其余键原样保留。"""
    overrides = read_overrides(home)
    overrides[key] = value
    write_overrides(home, overrides)
