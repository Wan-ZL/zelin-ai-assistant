"""Failure classification — the routing table the error UX hangs on (CONTRACT §25).

Every known failure mode gets ONE stable id, a plain-language sentence in both
UI languages, and an ``action_id`` the shell / web maps to a one-click repair
(or a deep-link). Producers (the recap run, doctor checks, the engine-log
diagnosis) attach the id ALONGSIDE the raw text — raw text is never replaced,
only demoted to detail/tooltip.

Design law: fewer failure modes classified WELL beats full coverage. Anything
:func:`classify` cannot match returns ``None`` and the UI falls back to the raw
text.

v2.0.0: ids that only the Act pipeline produced are **tombstones** (add-only,
§25): the key stays in :data:`FAILURES` with ``"retired": "v2.0.0"`` and a short
sentence, nothing produces it any more, and :data:`RETIRED` lists them. New:
``claude_bare_unsupported`` and ``isolation_footprint_violation`` (§78). The
web / shell read this table through ``server/failure_catalog.py``.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

# --------------------------------------------------------------------------- #
# catalog — id -> {plain_zh, plain_en, action_id}
#
# action_id vocabulary (Swift RepairAction):
#   install_claude    open the Claude Code install page
#   open_settings_key jump to Settings credentials (re-paste the API key)
#   install_node      open the Node.js download page
#   restart_engine    RecordingController stop->start (in-app)
#   reload_agent      render + launchctl load the agent plist (in-app)
#   repair_cron       re-run the installer's cron step (in-app, streamed)
#   grant_cron_fda    guided Full Disk Access grant for /usr/sbin/cron
#   restart_actd      render + reload the actd launchd agent (in-app)
#   fix_config        reveal config.yaml / restore from template
#   retry             transient — just try the action again
#   show_engine_log   reveal ~/.screenpipe/engine.log (download progress lives there)
#   regrant_screen    open System Settings -> Screen Recording (re-grant)
#   install_ffmpeg    open the ffmpeg download page (screen_audio dependency)
#   open_deps         jump to the dependencies/diagnostics page (the doctor row
#                     shows the exact binaries/paths involved)
# --------------------------------------------------------------------------- #
FAILURES: dict = {
    "claude_cli_missing": {
        "plain_zh": "claude 命令行没装好——会议纪要和笔记整理都无法运行",
        "plain_en": "The claude CLI is not installed — meeting recaps and note processing cannot run",
        "action_id": "install_claude",
    },
    "claude_cli_outdated": {
        "plain_zh": "（已退役）claude 版本过旧导致后台派发失败",
        "plain_en": "(retired) an outdated claude broke background dispatch",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "claude_blind": {
        "plain_zh": "（已退役）launchd 派发的 claude 读不到任务目录",
        "plain_en": "(retired) launchd-dispatched claude could not read the task folder",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "fd_limit": {
        "plain_zh": "（已退役）后台服务打开文件数耗尽",
        "plain_en": "(retired) the background service ran out of open files",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "claude_bypass_disclaimer": {
        "plain_zh": "（已退役）claude 未接受跳过权限确认的免责声明",
        "plain_en": "(retired) claude had not accepted the bypass-permissions disclaimer",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "claude_auth_failed": {
        "plain_zh": "AI 的 API key 无效或过期——去设置页重新粘贴一个",
        "plain_en": "The AI API key is invalid or expired — re-paste one in Settings",
        "action_id": "open_settings_key",
    },
    "node_missing": {
        "plain_zh": "缺少 Node.js——录制引擎无法启动",
        "plain_en": "Node.js is missing — the recording engine cannot start",
        "action_id": "install_node",
    },
    "engine_dead": {
        "plain_zh": "录制引擎没有在运行——屏幕内容不会被记录",
        "plain_en": "The recording engine is not running — nothing on screen is being captured",
        "action_id": "restart_engine",
    },
    # NOT an error: first-run npx download of the pinned screenpipe package.
    # Copy must read as calm progress, never as a failure (audit 2.3).
    "engine_npm_download": {
        "plain_zh": "录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制",
        "plain_en": "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes",
        "action_id": "show_engine_log",
    },
    "engine_crashed": {
        "plain_zh": "录制引擎意外停了——点「重启引擎」再试；反复失败就看下面的引擎日志",
        "plain_en": "The recording engine stopped unexpectedly — click Restart engine; if it keeps happening, check the engine log lines below",
        "action_id": "restart_engine",
    },
    # screen_audio hard-requires ffmpeg at engine startup, and screenpipe's
    # built-in auto-installer is unreliable (2026-07-13: it wrote the binary
    # yet still exited "os error 2" on every attempt, killing the engine
    # seconds after spawn while the menu bar blamed Screen Recording TCC).
    "engine_ffmpeg_missing": {
        "plain_zh": "「屏幕+音频」需要 ffmpeg，这台电脑上还没有——装一个（brew install ffmpeg）或切回「仅屏幕」",
        "plain_en": "Screen + Audio needs ffmpeg, which this Mac does not have — install it (brew install ffmpeg) or switch back to Screen Only",
        "action_id": "install_ffmpeg",
    },
    # macOS ties the Screen Recording grant to the app's code signature —
    # an OS update or app reinstall changes it and silently revokes the grant.
    "screen_tcc_lost": {
        "plain_zh": "「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复",
        "plain_en": "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume",
        "action_id": "regrant_screen",
    },
    "agent_unloaded": {
        "plain_zh": "（已退役）一个 Act 后台服务没有装载",
        "plain_en": "(retired) an Act background service was not loaded",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "interpreter_blind": {
        "plain_zh": "（已退役）后台服务的 Python 读不到项目文件夹",
        "plain_en": "(retired) the background services' Python could not read the project folder",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "deploy_blind_tcc": {
        "plain_zh": "（已退役）自动部署任务读不到项目所在的卷",
        "plain_en": "(retired) the auto-deploy job could not read the project volume",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "cron_missing": {
        "plain_zh": "定时任务没有安装——屏幕记录不会变成笔记和会议纪要",
        "plain_en": "The scheduled job is not installed — screen captures never become notes or meeting recaps",
        "action_id": "repair_cron",
    },
    "cron_fda_blocked": {
        "plain_zh": "定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失",
        "plain_en": "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost",
        "action_id": "grant_cron_fda",
    },
    # §23 cron=skipped_tcc（2026-09-02 v0.48.12 实战）：自动部署的 launchd 会话
    # 被 TCC 拒写 crontab（Operation not permitted）——环境问题不算部署失败步
    # （§56.5），这里的人话负责把「给守护 python 开 FDA」说清楚。
    "cron_tcc_blocked": {
        "plain_zh": "后台安装改不了定时任务表（macOS 挡住了 crontab——launchd 会话缺"
                    "「完全磁盘访问」）——给守护 Python 开一次完全磁盘访问再跑 "
                    "bash install.sh；在终端里跑通不算数（Terminal 自带权限）",
        "plain_en": "Background installs cannot rewrite the scheduled jobs (macOS blocks "
                    "crontab — the launchd session has no Full Disk Access) — grant Full "
                    "Disk Access to the daemon Python once, then bash install.sh; a "
                    "terminal run proving it works proves nothing (Terminal has its own grant)",
        "action_id": "open_deps",
    },
    "dashboard_stale": {
        "plain_zh": "（已退役）看板数据停止更新",
        "plain_en": "(retired) the board data stopped updating",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "actd_stalled": {
        "plain_zh": "（已退役）actd 进程停止心跳",
        "plain_en": "(retired) the actd process stopped its heartbeat",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    "launchd_orphan": {
        "plain_zh": "（已退役）退役的后台服务仍在 launchd 里",
        "plain_en": "(retired) a retired background service was still in launchd",
        "action_id": "open_deps", "retired": "v2.0.0",
    },
    # §59: the model knob names an id claude cannot serve (alias/suffix retired,
    # typo, no access). Only the doctor's liveness probe produces it.
    "model_unavailable": {
        "plain_zh": "设置里选的模型不可用——会议纪要会全部失败；去设置页「模型」换一个 canonical id",
        "plain_en": "The model chosen in Settings is unavailable — every meeting recap will "
                    "fail; in Settings → Models pick a canonical id",
        "action_id": "open_deps",
    },
    # §54 board server: nothing answers on the loopback port (launchd job
    # crash-looping / port held by a stray server / never installed). Only
    # the doctor's `board server` row produces it.
    "board_server_down": {
        "plain_zh": "看板 server 没在端口上答话——web 看板和壳 app 连不上；先退掉手动起的"
                    " `python3 -m server` / 旧壳，再 launchctl kickstart -k "
                    "gui/$(id -u)/com.zelin.aiassistant.server（没装则 bash install.sh）",
        "plain_en": "The board server does not answer on its port — the web board and the "
                    "shell app have nothing to connect to; quit any hand-started "
                    "`python3 -m server` / old shell, then launchctl kickstart -k "
                    "gui/$(id -u)/com.zelin.aiassistant.server (bash install.sh if never installed)",
        "action_id": "open_deps",
    },
    # §56.5 `ui` step: node was TCC-denied (EPERM) while the deploy rebuilt the
    # web board under launchd — the daemons upgraded, the UI did not.
    "ui_build_tcc_blocked": {
        "plain_zh": "自动部署没能重建 web 看板：node 在 launchd 会话里缺「完全磁盘访问」（守护进程已升级，"
                    "看板还是旧的）——给 node 二进制开完全磁盘访问，或在终端里跑一次 bash install.sh",
        "plain_en": "The deploy could not rebuild the web board: node lacks Full Disk Access in the "
                    "launchd session (daemons upgraded, board still old) — grant the node binary Full "
                    "Disk Access, or run bash install.sh once from a terminal",
        "action_id": "open_deps",
    },
    "config_invalid": {
        "plain_zh": "配置文件写坏了——所有组件都退回默认设置",
        "plain_en": "The config file is broken — every component fell back to defaults",
        "action_id": "fix_config",
    },
    "network_error": {
        "plain_zh": "网络问题——稍后会自动重试",
        "plain_en": "Network trouble — it will retry automatically",
        "action_id": "retry",
    },
    # §78: the installed claude lacks a flag the isolation depends on (--bare,
    # --no-session-persistence, --setting-sources, --strict-mcp-config,
    # --mcp-config) — running without it would read the owner's own Claude Code
    # setup. Only the doctor's `claude bare supported` row produces it.
    "claude_bare_unsupported": {
        "plain_zh": "这台机器上的 claude 太旧，不支持本应用的隔离运行方式（--bare 等旗标）——"
                    "更新 Claude Code 后重跑 bash install.sh",
        "plain_en": "The claude on this Mac is too old for this app's isolated mode (--bare "
                    "and related flags) — update Claude Code, then re-run bash install.sh",
        "action_id": "install_claude",
    },
    # §78: something outside the closed resident footprint (one LaunchAgent, one
    # cron line, the opt-in login item, the engine) is installed under our name.
    "isolation_footprint_violation": {
        "plain_zh": "发现本应用封闭清单之外的后台项（旧版留下的 LaunchAgent 或定时任务行）——"
                    "重跑一次 bash install.sh 会把它们清掉",
        "plain_en": "Found background items outside this app's closed footprint (a LaunchAgent "
                    "or cron line left by an older version) — re-run bash install.sh to remove them",
        "action_id": "open_deps",
    },
}

# §25 tombstones (v2.0.0): ids nothing produces any more; the entries stay so an
# old receipt that names one still renders a sentence (add-only).
RETIRED: frozenset = frozenset(k for k, v in FAILURES.items() if v.get("retired"))

# --------------------------------------------------------------------------- #
# row class — WHO fixes it (§25; consumed by the §56.3 deploy verdict)
#
# `owner_action`: the only remedy is a manual grant or acceptance by the
# owner — a TCC toggle in System Settings, a one-time disclaimer in a
# terminal. No code change, re-install or rollback can clear such a row, so
# it says nothing about whether the code that just landed works: the deploy
# verdict must never read it as "the new version broke X". 2026-09-03 live:
# v1.0.7 was rolled back to v1.0.3 because `stable claude` FAILed under
# launchd for exactly the Full Disk Access grant install.sh had asked for
# 30 s earlier. Rows keep rendering as FAIL in doctor output; the deploy
# summary lists them as "needs owner: …". Derived from failure_id, so a row
# can only be owner_action by carrying one of these ids.
# --------------------------------------------------------------------------- #
OWNER_ACTION = "owner_action"
OWNER_ACTION_IDS: frozenset = frozenset({
    "cron_fda_blocked",         # FDA for /usr/sbin/cron
    "cron_tcc_blocked",         # FDA for the daemon python (crontab write)
    "ui_build_tcc_blocked",     # FDA for the node binary
    "screen_tcc_lost",          # re-grant Screen Recording
})


def row_class(failure_id: Optional[str]) -> str:
    """``owner_action`` for ids whose fix is the owner's manual grant; "" otherwise."""
    return OWNER_ACTION if failure_id in OWNER_ACTION_IDS else ""

# --------------------------------------------------------------------------- #
# raw-text classifier — for claude CLI stdout/stderr, dispatch errors, log
# tails. Order matters: first match wins. Patterns are deliberately narrow
# (high precision); unknown text -> None -> the UI keeps the raw string.
# --------------------------------------------------------------------------- #
_RULES: list = [
    ("claude_cli_missing", re.compile(
        r"claude.{0,40}(command not found|no such file)|"
        r"(command not found|no such file or directory).{0,20}claude|"
        r"\[Errno 2\].*claude", re.IGNORECASE | re.DOTALL)),
    ("claude_auth_failed", re.compile(
        r"authentication_error|invalid (x-)?api[- _]?key|"
        r"\b401\b|OAuth token has expired|(?<![\w-])unauthorized|"
        r"please run /login|api key.{0,20}(invalid|expired|revoked)",
        re.IGNORECASE)),
    ("node_missing", re.compile(
        r"npx.{0,40}(command not found|no such file)|"
        r"(command not found|no such file or directory).{0,20}(npx|node)\b|"
        r"env: node: No such file", re.IGNORECASE)),
    ("network_error", re.compile(
        r"connection (refused|reset|timed? ?out)|network is (down|unreachable)|"
        r"getaddrinfo|ENOTFOUND|ETIMEDOUT|ECONNRE|temporary failure in name",
        re.IGNORECASE)),
    # npx cache-miss download banner (npm >= 7 prints the first line, the
    # interactive prompt the second). Ranked AFTER network_error on purpose:
    # a download that died on the network must not classify as "in progress".
    ("engine_npm_download", re.compile(
        r"package was not found and will be installed|"
        r"need to install the following packages?", re.IGNORECASE)),
]


# screenpipe's ffmpeg install-failure phrasing — ENGINE-LOG CONTEXT ONLY,
# deliberately NOT a classify() rule: dispatch/card text may legitimately say
# "failed to install ffmpeg-python" or discuss ffmpeg, and the recording
# catalog sentence would then mislabel an unrelated task failure. The colon
# pins the first signature to screenpipe's exact format ("failed to install
# ffmpeg: <os error>"). Checked directly (not via the rules chain) so an
# install error carrying network/401-flavored words still reads as the
# ffmpeg dependency — mirroring Swift diagnoseEngine's substring check.
_FFMPEG_INSTALL_FAILED = re.compile(
    r"failed to install ffmpeg:|"
    r"ffmpeg not found and installation failed", re.IGNORECASE)


def classify(raw: Optional[str]) -> Optional[str]:
    """Map raw error text to a failure id, or None when honestly unknown."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw)
    for fid, pattern in _RULES:
        if pattern.search(text):
            return fid
    return None


def _strip_app_markers(tail: str) -> str:
    """Drop the app's own breadcrumb lines ("[app …] spawn/autostart …") so a
    log that only contains our markers counts as empty, not as engine output."""
    return "\n".join(
        line for line in tail.splitlines() if not line.startswith("[app")
    ).strip()


def classify_engine_log(tail: Optional[str], npx_present: bool = True,
                        engine_alive: bool = False) -> Optional[str]:
    """Why is the recording engine down? (audit 2.3 — engine-death diagnosis)

    ``tail`` = the last lines of ``~/.screenpipe/engine.log`` (the engine's
    combined stdout/stderr). Returns a failure id, or None when the engine is
    alive and nothing in the log looks wrong (healthy — including "alive but
    quiet"; a locked screen legitimately goes silent, so silence alone is
    never classified as a failure).

    Mirrored in Swift by RecordingController.diagnoseEngine (Recording.swift);
    keep the two in sync when touching this.
    """
    if not npx_present:
        return "node_missing"
    text = _strip_app_markers(str(tail or ""))
    fid = classify(text)
    if fid == "node_missing":
        return fid
    if engine_alive:
        return _alive_engine_cause(fid)
    return _dead_engine_cause(text)


def _alive_engine_cause(fid: Optional[str]) -> Optional[str]:
    """While the npx process is alive, the download banner means exactly
    that: first-run download in progress; anything else is healthy. (A DEAD
    process whose last line is the banner is a failed download -> crashed,
    see _dead_engine_cause.)"""
    return "engine_npm_download" if fid == "engine_npm_download" else None


def _dead_engine_cause(text: str) -> str:
    """Why a DEAD engine died, from its (marker-stripped) log tail."""
    # dead on screenpipe's ffmpeg install failure -> the specific fix beats
    # the generic "crashed" (an ALIVE engine with stale ffmpeg lines in its
    # tail already returned healthy/downloading above — it found ffmpeg this
    # time). Direct substring check, see _FFMPEG_INSTALL_FAILED.
    if _FFMPEG_INSTALL_FAILED.search(text):
        return "engine_ffmpeg_missing"
    # dead with real output -> crashed (callers surface the tail verbatim);
    # dead with nothing but our own markers -> plain "not running".
    return "engine_crashed" if text else "engine_dead"


# --------------------------------------------------------------------------- #
# language + copy helpers (python side of the UI language setting, §15)
# --------------------------------------------------------------------------- #
def _persisted_language() -> Optional[str]:
    """The user's explicitly persisted language, or None when neither source
    ever set one. Reads the two sources directly (settings_overrides.json
    wins over config.yaml — load_config precedence) because Config.language's
    dataclass default "zh" is a placeholder, not a user choice, and going
    through load_config() cannot tell the two apart. Values normalize with
    the historical rule: "en" → en, any other non-empty value → zh."""
    return _override_language() or _config_yaml_language()


def _normalize_language(value) -> Optional[str]:
    """The historical rule: "en" → en, any other non-empty value → zh, empty → None."""
    v = str(value or "").strip().lower()
    if not v:
        return None
    return "en" if v == "en" else "zh"


def _override_language() -> Optional[str]:
    """``language`` from settings_overrides.json (the Mac app's writes)."""
    from act.lib import config
    try:
        data = json.loads(
            config.SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return _normalize_language(data.get("language"))
    except (OSError, ValueError, AttributeError):
        return None


def _config_yaml_language() -> Optional[str]:
    """``language`` from config.yaml (a top-level mapping key); None without
    PyYAML, without the file, or when the document is not a mapping."""
    from act.lib import config
    if config.yaml is None:
        return None
    try:
        data = config.yaml.safe_load(
            config.CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _normalize_language(data.get("language")) if isinstance(data, dict) else None


def ui_lang() -> str:
    """The UI language ("zh" | "en"), resolved in order (§15; v0.42):

    1. ``AIASSISTANT_UI_LANG`` env var — the Mac app passes its EFFECTIVE
       display language when spawning python whose output the user reads
       (doctor --json, ask, wizard, settings helpers, …), so app-spawned
       copy matches the app exactly;
    2. the user's persisted setting (settings_overrides.json ``language``,
       else config.yaml ``language``);
    3. the system locale (LC_ALL/LANG: zh* → zh, else en) — mirroring the
       Swift first-run default instead of the old hardcoded "zh", so an
       en-locale user with no persisted override no longer gets zh
       unclassified doctor rows interleaved with en classified ones.
    """
    try:
        env = os.environ.get("AIASSISTANT_UI_LANG", "").strip().lower()
        if env in ("zh", "en"):
            return env
        return _persisted_language() or _locale_language()
    except Exception:  # noqa: BLE001 - copy helpers must never raise
        return "zh"


def _locale_language() -> str:
    """System locale fallback: LC_ALL, else LANG; zh* → zh, anything else → en."""
    loc = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    return "zh" if loc.startswith("zh") else "en"


def pick(zh: str, en: str, lang: Optional[str] = None) -> str:
    """Python-side L(): choose the string for the current UI language."""
    return en if (lang or ui_lang()) == "en" else zh


def describe(failure_id: Optional[str]) -> Optional[dict]:
    """The catalog entry for an id (None for unknown ids — no KeyError)."""
    if not failure_id:
        return None
    return FAILURES.get(str(failure_id))


def user_message(failure_id: Optional[str], lang: Optional[str] = None) -> Optional[str]:
    """Plain-language sentence for a failure id in the current UI language."""
    entry = describe(failure_id)
    if not entry:
        return None
    key = "plain_en" if (lang or ui_lang()) == "en" else "plain_zh"
    return entry[key]


def action_id(failure_id: Optional[str]) -> Optional[str]:
    entry = describe(failure_id)
    return entry["action_id"] if entry else None
