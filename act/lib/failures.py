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
# --------------------------------------------------------------------------- #
FAILURES: dict = {
    "claude_cli_missing": {
        "plain_zh": "claude 命令行没装好——助手无法研究或执行任何卡片",
        "plain_en": "The claude CLI is not installed — the assistant cannot research or execute any card",
        "action_id": "install_claude",
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
