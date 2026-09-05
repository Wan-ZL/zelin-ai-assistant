"""server/settings_catalog.py — 设置页的通用 section 目录（CONTRACT §15.3 / §49 / §68）。

原生 Settings.swift 的 20 个区里，凡是「一把旋钮 = settings_overrides.json 的一个
键」的都收进这一张 server-owned 目录：每个 section 一组 field，每个 field 说明
自己的 wire 键、类型、可选值、默认值、config.yaml 落点、override 拼法与文案
（zh/en 两键——防腐 #10：文案进 server-owned catalog，web 只逐字镜像、按 UI
语言取键）。web 设置页据此**通用渲染**（bool → 开关、enum → 下拉、string /
number → 输入框、list → 逗号分隔输入框），新增一个旋钮 = 目录里加一行，前端零改动。
section 与 field 的**标签逐字镜像原生**（ui/parity/native-inventory.json 的 control:settings.*，
§66.2）：区按原生分（general / notifications / obsidian / slack / gmail / telemetry / digest /
approval / flags / voice / redaction / maintainer），凭证行与桥旋钮不在此表（§68.3 / §68.2）。

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
LIST_MAX = 200          # list 字段：项数与每项长度的帽

# 与 act/lib/config.py _BOOL_TRUE_WORDS / _BOOL_FALSE_WORDS 同一词表（config.yaml 层容忍字符串拼法）
_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0"})


def _f(key: str, kind: str, zh: str, en: str, *, default: Any = None,
       config: "tuple | None" = None, choices: "tuple | None" = None,
       help_zh: str = "", help_en: str = "", override: Optional[str] = None,
       write: str = "diff", placeholder: "tuple | None" = None,
       path: Optional[str] = None) -> dict:
    """一条 field 描述（目录内部形；对外投影去掉 config/override/write 三个内部键）。
    ``placeholder``（add-only，zh/en 两键）= 输入框的示例文案（原生 TextField 的 prompt，如「例：you@gmail.com」）。
    ``path``（add-only；今日词表 ``"dir"``）= 这是一个目录字段：投影多带 ``path`` 与 ``path_exists``
    （effective 值展开 ``~`` 后是不是目录；空值 → null），web 据此渲染 选择… / 打开 / 创建 与
    「目录不存在」警告（原生 obsidianGroup / approvalGroup；§68.1）。"""
    zh_ph, en_ph = placeholder or ("", "")
    return {"key": key, "kind": kind, "label": {"zh": zh, "en": en},
            "help": {"zh": help_zh, "en": help_en}, "default": default,
            "choices": list(choices) if choices else None, "config": config,
            "override": override or key, "write": write, "placeholder": {"zh": zh_ph, "en": en_ph},
            "path": path}


def _section(sid: str, zh: str, en: str, fields: list, *, help_zh: str = "",
             help_en: str = "") -> dict:
    return {"id": sid, "title": {"zh": zh, "en": en},
            "help": {"zh": help_zh, "en": help_en}, "fields": fields}


# --------------------------------------------------------------------------- #
# 目录（顺序 = 设置页通用区的显示顺序）
# --------------------------------------------------------------------------- #
# 原生 Settings.swift featureFlagsGroup 的六条双语标签 + web 才有的两把（feedback_sync / auto_deploy）。
# 键写全拼（"features.<flag>"，与 overrides 嵌套块的点号拼法一致）——设置键探针按字面量找（§66.2 setting:overrides:*）。
_FLAGS = (
    ("features.slack_radar", "Slack 需求雷达", "Slack demand radar"),
    ("features.gmail_radar", "Gmail 捕获", "Gmail capture"),
    ("features.obsidian_radar", "Obsidian 雷达", "Obsidian radar"),
    ("features.digest", "状态摘要", "status digest"),
    ("features.auto_resume", "后台任务自动拉起", "auto-resume background tasks"),
    ("features.analytics", "用量统计", "usage stats"),
    ("features.feedback_sync", "建议同步到 GitHub", "feedback sync to GitHub"),
    ("features.auto_deploy", "自动部署", "auto-deploy"),
)

SECTIONS: tuple = (
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
            _f("updates_check_enabled", "bool", "自动检查新版本（每天最多一次）",
               "Check for updates automatically (at most once a day)", default=True,
               config=("updates", "check_enabled"),
               help_zh="至多每 24h 向 GitHub 查一次最新版本号（只暴露 IP + 当前版本号）。",
               help_en="At most one GitHub version check per 24h (exposes only your IP + current version)."),
            # 原生 UserDefaults terminalApp 的 server 侧落点（§66.2 setting:prefs:terminalApp；标签逐字镜像
            # Settings.swift「终端应用」）：执行者是 server（open -a），所以偏好住 overrides 而非浏览器。
            _f("terminal_app", "enum", "终端应用", "Terminal app", default="auto",
               choices=("auto", "ghostty", "terminal", "iterm2"),
               help_zh="「在终端打开」（接管会话）/ 开发会话 / 卸载 都在这个终端里新开窗口运行。自动 = 装了 Ghostty 就用 Ghostty，否则 Terminal；选了没装的会回落到系统默认终端。",
               help_en="\"Open in terminal\" (take over a session) / development session / uninstall open a new window in this terminal. Auto = Ghostty when installed, else Terminal; a choice that is not installed falls back to the system default terminal."),
            # 原生 AppDelegate.rememberFeedbackPublishDefault 的 override 键（§66.2 setting:overrides:feedback_publish_default）：
            # 「提建议」弹窗里「同时公开到 GitHub」勾选的默认态 = 上次选择；web 弹窗读 effective、勾选即 PUT（§29bis）。
            _f("feedback_publish_default", "bool", "提建议默认勾选「公开到 GitHub」", "Publish feedback by default",
               default=False,
               help_zh="「提建议」弹窗里「同时公开到 GitHub 建议跟踪表」的默认勾选态；弹窗里改一次就记住。",
               help_en="Default state of the \"also publish to the GitHub feedback tracker\" checkbox in the feedback dialog; changing it there is remembered."),
        ],
    ),
    _section(
        "notifications", "通知", "Notifications",
        [
            _f("review_notify", "enum", "任务完成提醒", "Task-done alert", default="sound",
               choices=("off", "banner", "sound"),
               help_zh="卡片进入「待验收」时的系统通知：关 / 横幅 / 横幅+声音（默认）。其余通知不受影响。",
               help_en="System notification when a card reaches In review: off / banner / banner + sound (default). Other notifications are unaffected."),
        ],
        help_zh="系统通知由看板 app（壳）投递（§28）；app 没开就没有系统通知。通知权限见「权限体检」。",
        help_en="System notifications are posted by the board app (§28); no app running = no banners. Permission status: Permissions checkup.",
    ),
    _section(
        "obsidian", "笔记库", "Notes vault",
        [
            _f("obsidian_enabled", "bool", "启用 Obsidian 雷达", "Enable the Obsidian radar", default=True,
               config=("sources", "obsidian", "enabled"),
               help_zh="扫描笔记库 raw 目录里的新笔记（屏幕/会议 ingest 的落点）。",
               help_en="Scans new notes in the vault's raw folder (where screen / meeting ingest lands)."),
            _f("obsidian_raw", "string", "Obsidian Vault 位置", "Obsidian Vault location", default="",
               config=("sources", "obsidian_raw"), path="dir",
               help_zh="雷达扫描源（raw 目录）；其余三个管线目录由它的上级（vault 根）自动派生。",
               help_en="The radar's scan source (raw folder); the other three pipeline folders derive from its parent (the vault root)."),
        ],
        help_zh="每个源 = feature flag × 源开关（§48）；这里是源开关，flag 在「Feature flags」。真正生效的状态看下方健康摘要。",
        help_en="Each source = feature flag × source switch (§48); this is the switch, the flag lives under \"Feature flags\". The health line below shows what is actually in effect.",
    ),
    _section(
        "slack", "Slack 接入", "Slack",
        [
            _f("slack_enabled", "bool", "启用 Slack 雷达", "Enable the Slack radar", default=True,
               config=("sources", "slack", "enabled"),
               help_zh="扫描 DM / @提及 / 自发消息（手机端快速捕获入口）。凭证在下方 Slack user token。",
               help_en="Scans DMs / @mentions / self-DMs (the phone capture inlet). Credential: the Slack user token below."),
            _f("owner_slack_user_id", "string", "你的 Slack user id", "Your Slack user id", default="",
               help_zh="验证 token 通过后自动填好（auth.test）；一般不用手改。",
               help_en="Auto-filled when the token verifies (auth.test); rarely needs editing."),
            _f("slack_channels", "list", "监控频道（channel id，逗号分隔）", "Watched channels (channel ids, comma-separated)",
               default=[], config=("sources", "slack_channels"),
               help_zh="雷达额外扫描的频道；留空 = 只看 DM / @提及 / 自发消息。",
               help_en="Extra channels the radar scans; blank = DMs / @mentions / self-DMs only."),
            _f("watch_people", "list", "关注的人（handle，逗号分隔）", "People to watch (handles, comma-separated)",
               default=[], config=("sources", "watch_people"),
               help_zh="这些人发的消息按需求候选提取。", help_en="Messages from these people are mined for asks."),
        ],
    ),
    _section(
        "gmail", "Gmail 接入", "Gmail",
        [
            _f("gmail_enabled", "bool", "启用 Gmail 雷达", "Enable the Gmail radar", default=True,
               config=("sources", "gmail", "enabled"),
               help_zh="读取收件箱未读邮件提炼需求卡（只读，不发信）。凭证在下方 Gmail 应用密码。",
               help_en="Reads unread inbox mail into proposal cards (read-only, never sends). Credential: the Gmail app password below."),
            _f("gmail_address", "string", "Gmail 地址", "Gmail address", default="",
               config=("sources", "gmail", "address"), placeholder=("例：you@gmail.com", "e.g. you@gmail.com"),
               help_zh="IMAP 登录用的邮箱地址；留空 = 用 config.yaml 里的值。",
               help_en="Address used for the IMAP login; blank = whatever config.yaml says."),
            _f("gmail_fetch_command", "string", "自定义抓取命令（B 路径）", "Custom fetch command (path B)", default="",
               config=("sources", "gmail", "fetch_command"), placeholder=("例：/Users/you/bin/gmail-fetch.sh", "e.g. /Users/you/bin/gmail-fetch.sh"),
               help_zh="填了就走 B · 自定义抓取命令（stdout 一行一封）；留空走 A · 应用专用密码（推荐）。",
               help_en="Set = path B, a custom fetch command (one mail per stdout line); blank = path A, the app password (recommended)."),
        ],
    ),
    _section(
        "people_ledger", "重点人物账本", "People ledger",
        [
            _f("people_ledger_enabled", "bool", "启用重点人物账本（默认关）", "Enable the people ledger (off by default)",
               default=False, config=("people_ledger", "enabled"),
               help_zh="开了以后，笔记库里新出现的笔记只要提到下面这些人，就把「我答应对方的 / 对方答应我的」记进每人一本的账（带来源引文）；后来的笔记显示做完了就标完成。文件落在工作台（未配置则 state/）的 people_ledger/ 下；不是卡片、不会发送。首次开启不回填旧笔记。",
               help_en="When on, every new note in the vault that mentions one of the people below adds \"what I owe them / what they owe me\" to that person's rolling ledger (with the source quote); a later note showing completion marks the item done. Files land under people_ledger/ in the workbench (or state/ when none is configured); not a card, never sent. Turning it on does not backfill old notes."),
            _f("people_ledger_people", "list", "记账的人（姓名或 handle，逗号分隔）", "People to keep a ledger for (names or handles, comma-separated)",
               default=[], config=("people_ledger", "people"),
               help_zh="留空 = 沿用「Slack 接入」里的「关注的人」。示例占位 your.manager、少于 3 个字母的名字和 your / the / my 这类停用词会被自动跳过，不会拿来扫笔记。",
               help_en="Blank = the \"people to watch\" list under Slack. The placeholder your.manager, names shorter than 3 letters and stopwords such as your / the / my are skipped automatically and never used to scan notes."),
        ],
        help_zh="旧「manager pack」的按人重做（issue #23）：每个人一本滚动账，而不是每篇笔记一个文件；严格 opt-in。",
        help_en="The per-person redo of the old \"manager pack\" (issue #23): one rolling ledger per person instead of one file per note; strictly opt-in.",
    ),
    _section(
        "telemetry", "产品改进计划", "Product improvement program",
        [
            _f("telemetry.enabled", "bool", "参与产品改进（默认开，仅事件元数据——输入文本需在下方单独勾选）",
               "Product improvement (on by default; event metadata only — typed text needs the separate opt-in below)",
               default=True, config=("telemetry", "enabled"),
               help_zh="默认开：只上传事件元数据（事件名 / 耗时 / 计数）。关 = 完全不上传。",
               help_en="On by default: uploads event metadata only (names / durations / counts). Off = nothing is uploaded."),
            _f("telemetry.level", "enum", "行为事件级别", "Behavior-event level", default="detailed",
               choices=("basic", "detailed"), config=("telemetry", "level"),
               help_zh="basic / detailed 都只是元数据粒度；切 basic 同时停掉输入文本上传。",
               help_en="basic / detailed are both metadata-only granularity; basic also switches off typed-text upload."),
            _f("telemetry.capture_input", "bool",
               "上传我输入的文本以更懂我（默认关，勾选即同意：快速捕获、提问、打回反馈、搜索词；每条 ≤500 字符）",
               "Upload the text I type, to know me better (off by default — checking is opting in: captures, questions, rework feedback, search terms; ≤500 chars each)",
               default=False, config=("telemetry", "capture_input"), write="always",
               help_zh="绝不含 AI 回答、屏幕内容、邮件、Slack 消息、密钥。",
               help_en="Never AI output, screen content, mail, Slack messages or secrets."),
        ],
        help_zh="字段表与边界见 docs/TELEMETRY.md。",
        help_en="Field table and boundaries: docs/TELEMETRY.md.",
    ),
    _section(
        "digest", "每周摘要", "Weekly digest",
        [
            _f("weekly_digest_enabled", "bool", "每周自动生成「本周你都在忙什么」回顾卡（默认关）",
               "Auto-generate a weekly \"what you were up to\" recap (default off)", default=False,
               config=("sources", "weekly_digest", "enabled"),
               help_zh="近 7 天 ingest 的回顾卡（待验收列）；没有新数据时自动跳过。",
               help_en="A recap card over the last 7 days of ingest (In review lane); skipped when nothing new landed."),
            _f("digest_frequency", "enum", "状态摘要频率", "Status digest cadence", default="off",
               choices=("off", "daily", "every2days", "weekly"), config=("digest", "frequency"),
               help_zh="待审批 / 待验收积压 + 卡住任务 + 欠账的一张摘要卡。默认 off（D19）。",
               help_en="One card summarizing approvals / review backlog, stuck tasks and debts. Default off (D19)."),
        ],
    ),
    _section(
        "approval", "审批 / 成本", "Approval / Cost",
        [
            _f("default_target_repo", "string", "任务工作目录", "Task working folder",
               default="~/Projects/your-workbench", config=("execution", "default_target_repo"), path="dir",
               help_zh="卡片没指定落点时的兜底目录（文书 / 调研的家；绝不默认进你的代码 repo）。",
               help_en="Fallback folder when a card names no target (home for paperwork / research; never your code repo by default)."),
            _f("skip_permissions", "bool", "后台任务免确认执行（更快，默认开）",
               "Run background tasks without per-action confirmations (faster, default on)", default=True,
               config=("execution", "skip_permissions"),
               help_zh="claude --bg 带 --dangerously-skip-permissions；关 = 被挡住的 agent 会等你确认。",
               help_en="claude --bg passes --dangerously-skip-permissions; off = blocked agents wait for you."),
            _f("create_github_repo", "bool", "允许自动创建 GitHub 私有仓库（默认关）",
               "Allow auto-creating private GitHub repos (default off)", default=False,
               config=("execution", "create_github_repo"),
               help_zh="target_kind=new 的卡自动 gh repo create --private（draft PR 需要）。",
               help_en="target_kind=new cards run gh repo create --private (needed for draft PRs)."),
            _f("show_cost_above_usd", "number", "显示成本阈值（USD ≥）", "Show cost above (USD ≥)", default=5.0,
               config=("approval", "cost_thresholds", "show_cost_above_usd"),
               help_zh="低于此值卡片不显示预估费用。请输入不小于 0 的数字，如 5。",
               help_en="Cards below this estimate hide the cost chip. Enter a number ≥ 0, e.g. 5."),
            _f("require_text_confirm_above_usd", "number", "文字确认阈值（USD ≥）", "Typed confirm above (USD ≥)", default=50.0,
               config=("approval", "cost_thresholds", "require_text_confirm_above_usd"),
               help_zh="高于此值升 T2：批准要输入确认词。请输入不小于 0 的数字，如 50。",
               help_en="Above this the card is T2: approval needs a typed confirmation. Enter a number ≥ 0, e.g. 50."),
            _f("trash_retention_days", "int", "回收站保留天数", "Trash retention days", default=60,
               config=("trash", "retention_days"),
               help_zh="超期且未标永久的卡硬删；0 = 永不自动清。", help_en="Unpinned cards older than this are purged; 0 = never."),
        ],
    ),
    _section(
        "flags", "Feature flags（§16，默认全开）", "Feature flags (§16, all on by default)",
        [_f(key, "bool", key.split(".", 1)[1] + " — " + zh, key.split(".", 1)[1] + " — " + en, default=True,
            config=("features", key.split(".", 1)[1]))
         for key, zh, en in _FLAGS],
        help_zh="总开关层：关掉 analytics = 本机不再写任何行为事件；关掉 auto_deploy = install.sh 不再装自动部署 agent。",
        help_en="Master switches: analytics off = no behavior events are written locally; auto_deploy off = install.sh stops installing the deploy agent.",
    ),
    _section(
        "voice", "语气档案（以你的口吻起草）", "Voice profile (drafts in your voice)",
        [
            _f("voice_enabled", "bool", "启用语气注入（默认开）", "Voice injection (default on)", default=True,
               config=("voice", "enabled"),
               help_zh="以你的口吻起草 Slack 回复 / 邮件（docs/VOICE.md）。档案生成：终端 python3 -m act.voice_gen。",
               help_en="Drafts Slack replies / mail in your voice (docs/VOICE.md). Generate the profile: python3 -m act.voice_gen in a terminal."),
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
    _section(
        "maintainer", "开发者 · 开发会话", "Developer session",
        [
            _f("maintainer_repo_path", "string", "本软件的仓库路径", "This software's repo path", default="",
               config=("maintainer", "repo_path"), path="dir",
               help_zh="「让 AI 修」与开发会话打开的仓库；留空 = 当前 checkout。",
               help_en="Repo opened by Fix with AI and developer sessions; blank = this checkout."),
            _f("maintainer_session_id", "string", "续接的会话 id", "Session id to resume", default="",
               config=("maintainer", "session_id"), placeholder=("例：6f9619ff-8b86-d011-b42d-00cf4fc964ff", "e.g. 6f9619ff-8b86-d011-b42d-00cf4fc964ff"),
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
    """一个 list 元素的投影：{id, name} 字典取 id；字串 / 整数取 strip 后的字串；其余丢。"""
    if isinstance(item, dict):
        return str(item["id"]) if item.get("id") else None
    if isinstance(item, (str, int)) and str(item).strip():
        return str(item).strip()
    return None


def _coerce_list(value) -> Optional[list]:
    """list 字段：文件里是字串表（slack_channels 允许 {id, name} 字典——投影取 id）；空表 = 缺席。"""
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
_PUBLIC_FIELD_KEYS = ("key", "kind", "label", "help", "default", "choices", "placeholder")


def path_exists(value: Any) -> Optional[bool]:
    """目录字段的存在性：非空字串展开 ``~`` 后 ``is_dir()``；空 / 非字串 → None（无从判断）。"""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return None
    try:
        return Path(raw).expanduser().is_dir()
    except (OSError, ValueError):
        return False


def _project_field(field: dict, overrides: dict, config_doc: dict) -> dict:
    out = {k: field[k] for k in _PUBLIC_FIELD_KEYS}
    value, source = effective(field, overrides, config_doc)
    out["effective"] = value
    out["source"] = source
    if field.get("path"):
        # add-only（§68.1 目录字段）：web 的 选择… / 打开 / 创建 与「目录不存在」警告据此渲染
        out["path"] = field["path"]
        out["path_exists"] = path_exists(value)
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


def _split_list_input(value) -> list:
    """web 输入框给逗号 / 换行分隔的一个字串，JSON 客户端给字串表——两种入站形归一成字串表。"""
    if isinstance(value, str):
        return value.replace("\n", ",").split(",")
    return value if isinstance(value, list) else [value]


def _check_list_bounds(items: list, key: str) -> None:
    """帽：≤200 项、每项 ≤200 字（频道 id / handle 都远小于此；防无界 payload）。"""
    if len(items) > LIST_MAX or any(len(item) > LIST_MAX for item in items):
        raise InvalidFieldError("%s has too many / too long entries" % key, {"field": key})


def _validate_list(value, key: str) -> Optional[list]:
    """list 入站形：JSON 字串表，或一个逗号 / 换行分隔的字串（web 输入框）；空表 = 清掉 override。"""
    parts = _split_list_input(value)
    if any(not isinstance(item, str) for item in parts):
        raise InvalidFieldError("%s must be a list of strings" % key, {"field": key})
    items = [item.strip() for item in parts if item.strip()]
    _check_list_bounds(items, key)
    return items or None


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
