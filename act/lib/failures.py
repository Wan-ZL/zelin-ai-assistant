"""Failure classification — the routing table the error UX hangs on (CONTRACT §25).

Every known failure mode gets ONE stable id, a plain-language sentence in both
UI languages, and an ``action_id`` the Mac app maps to a one-click repair (or a
deep-link). Producers (executor dispatch errors, dashboard projection, doctor
checks) attach the id ALONGSIDE the raw text — raw text is never replaced, only
demoted to detail/tooltip.

Design law: fewer failure modes classified WELL beats full coverage. Anything
:func:`classify` cannot match returns ``None`` and the UI falls back to the
raw text plus the generic "让 AI 修 / Fix with AI" escape hatch (act/ai_fix.py).

The Swift side mirrors these ids in mac/Sources/Doctor.swift (FailureCatalog);
tests/test_failures.py drift-guards the two lists.
"""
from __future__ import annotations

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
#   open_deps         jump to the dependencies/diagnostics page (the doctor row
#                     shows the exact binaries/paths involved)
# --------------------------------------------------------------------------- #
FAILURES: dict = {
    "claude_cli_missing": {
        "plain_zh": "claude 命令行没装好——助手无法研究或执行任何卡片",
        "plain_en": "The claude CLI is not installed — the assistant cannot research or execute any card",
        "action_id": "install_claude",
    },
    # a SECOND, older claude install shadowing the real one on the daemon's
    # PATH (2026-07-08: /opt/homebrew/bin/claude 2.1.16 vs ~/.local/bin
    # 2.1.206) — dispatch dies on "unknown option '--bg'" and retries forever.
    "claude_cli_outdated": {
        "plain_zh": "这台机器上有多个 claude 命令，后台服务在用过旧的那个——更新或删掉旧版，再重跑一次安装",
        "plain_en": "This Mac has more than one claude CLI and the background service is using an outdated copy — update or remove the old one, then re-run the installer",
        "action_id": "open_deps",
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
    # screenpipe needs ffmpeg to encode; missing/off-PATH ffmpeg silently kills
    # recording. Actionable: install it (brew) — the app already broadens PATH to
    # cover the common install dirs, so an installed ffmpeg is found on restart.
    "engine_ffmpeg_missing": {
        "plain_zh": "录制引擎找不到 ffmpeg（录屏编码要用它）——终端运行 `brew install ffmpeg`,再点「重启引擎」",
        "plain_en": "The recording engine can't find ffmpeg (needed to encode the screen capture) — run `brew install ffmpeg` in Terminal, then Restart engine",
        "action_id": "show_engine_log",
    },
    # macOS ties the Screen Recording grant to the app's code signature —
    # an OS update or app reinstall changes it and silently revokes the grant.
    "screen_tcc_lost": {
        "plain_zh": "「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复",
        "plain_en": "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume",
        "action_id": "regrant_screen",
    },
    "agent_unloaded": {
        "plain_zh": "一个后台服务没有装载——它负责的工作停了",
        "plain_en": "A background service is not loaded — its work has stopped",
        "action_id": "reload_agent",
    },
    "cron_missing": {
        "plain_zh": "定时任务没有安装——屏幕记录不会变成笔记和卡片",
        "plain_en": "The scheduled jobs are not installed — screen captures never become notes or cards",
        "action_id": "repair_cron",
    },
    "cron_fda_blocked": {
        "plain_zh": "定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失",
        "plain_en": "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost",
        "action_id": "grant_cron_fda",
    },
    "dashboard_stale": {
        "plain_zh": "后台服务停止更新数据——看板显示的是旧内容",
        "plain_en": "The background service stopped updating data — the board shows old content",
        "action_id": "restart_actd",
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
}

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
    # version-mismatch signatures only: the exact flags/subcommands dispatch
    # relies on (--bg/--name/--resume, `claude agents`) rejected as unknown.
    # A generic "unknown option" must NOT match — could be the task's own text.
    ("claude_cli_outdated", re.compile(
        r"unknown option.{0,10}['\"]?--(bg|name|resume)\b|"
        r"unknown command.{0,10}['\"]?agents\b", re.IGNORECASE)),
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
    # screenpipe can't find ffmpeg (needed to encode frames) and its own
    # auto-download failed — recording is dead until ffmpeg is installed /
    # placed on the engine's PATH. Distinct, actionable ("brew install ffmpeg").
    ("engine_ffmpeg_missing", re.compile(
        r"ffmpeg not found|please install ffmpeg|failed to install ffmpeg",
        re.IGNORECASE)),
    # npx cache-miss download banner (npm >= 7 prints the first line, the
    # interactive prompt the second). Ranked AFTER network_error on purpose:
    # a download that died on the network must not classify as "in progress".
    ("engine_npm_download", re.compile(
        r"package was not found and will be installed|"
        r"need to install the following packages?", re.IGNORECASE)),
]


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
    # ffmpeg missing is the same verdict whether the engine is briefly alive or
    # already dead — screenpipe exits without it, so surface it either way.
    if fid == "engine_ffmpeg_missing":
        return fid
    if engine_alive:
        # while the npx process is alive, the download banner means exactly
        # that: first-run download in progress. (A DEAD process whose last
        # line is the banner is a failed download -> crashed, below.)
        return "engine_npm_download" if fid == "engine_npm_download" else None
    # dead with real output -> crashed (callers surface the tail verbatim);
    # dead with nothing but our own markers -> plain "not running".
    return "engine_crashed" if text else "engine_dead"


# --------------------------------------------------------------------------- #
# language + copy helpers (python side of the UI language setting, §15)
# --------------------------------------------------------------------------- #
def ui_lang() -> str:
    """The UI language ("zh" | "en") per settings_overrides/config (§15)."""
    try:
        from act.lib import config
        lang = str(config.load_config().language or "").strip().lower()
        return "en" if lang == "en" else "zh"
    except Exception:  # noqa: BLE001 - copy helpers must never raise
        return "zh"


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
