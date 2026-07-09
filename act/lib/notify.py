"""macOS notifications + Slack self-DM mirror + transition classifiers (CONTRACT §5, §13).

State transitions surfaced as native notifications:
  - new card_sent (radar found a new requirement)  -> "有新需求待审批：<title>"
  - executing -> done                              -> "任务完成：<title>"
  - executing -> blocked (needs_input)             -> "任务需要你输入：<title>"
  - credential failure (log has auth/login words)  -> "需要重新登录：<service>"

v0.4 (§13): every notification is ALSO mirrored to the Slack self-DM
(best-effort, never raises, requires features.slack_radar + a readable user
token) so it reaches Zelin's phone. Pass ``req="R-xxx"`` so the Slack message
carries ``#R-xxx`` and gets tracked in state/slack_outbox.json — a ✅ reaction
on it then approves that requirement (radar_slack polls reactions.get).
"""
from __future__ import annotations

import re
import subprocess
from typing import Optional

# self-DM channel id per token — resolved once per process (auth.test +
# conversations.list are not free; notifications are frequent enough to cache).
_SELF_DM_CACHE: dict = {}


# --------------------------------------------------------------------------- #
# raw notification
# --------------------------------------------------------------------------- #
def notify(title: str, body: str, subtitle: Optional[str] = None,
           req: Optional[str] = None) -> bool:
    """Fire a macOS notification via osascript. Returns True on success.

    Never raises — a failed notification must not break the daemon loop.
    Also mirrors to the Slack self-DM (§13) best-effort; the return value
    reflects ONLY the osascript path (unchanged behavior). ``req`` (an R-xxx
    id, optional) makes the Slack copy ✅-approvable.
    """
    def esc(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(body)}" with title "{esc(title)}"'
    if subtitle:
        script += f' subtitle "{esc(subtitle)}"'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ok = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False

    try:
        slack_notify(title, body, req=req)   # best-effort phone mirror (§13)
    except Exception:  # noqa: BLE001 - slack_notify already never raises; belt+braces
        pass
    return ok


# --------------------------------------------------------------------------- #
# Slack self-DM channel (§13 outbound)
# --------------------------------------------------------------------------- #
def slack_notify(title: str, body: str, req: Optional[str] = None) -> bool:
    """Post ``🔔 <title>\\n<body>`` (+ ``#R-xxx`` when ``req`` given) to the
    Slack self-DM. Returns True when the message was posted.

    Best-effort: no token / feature off / network trouble -> False. NEVER
    raises and never calls :func:`notify` back (no recursion). When ``req`` is
    given the message ts is recorded in state/slack_outbox.json so radar_slack
    can turn a ✅ reaction on it into an inbox approve.
    """
    try:
        # lazy import: radar_slack owns all Slack plumbing; importing it here at
        # module load would be a needless cycle risk for every notify() caller.
        from act import radar_slack
        from act.lib import config as _config

        cfg = _config.load_config()
        if not radar_slack.feature_on(cfg, "slack_radar"):
            return False
        token = radar_slack.get_token(cfg)
        if not token:
            return False
        channel = _self_dm_channel(token)
        if not channel:
            return False
        text = f"🔔 {title}\n{body}"
        if req:
            text += f"\n#{req}"
        resp = radar_slack.post_message(token, channel, text)
        if not resp.get("ok"):
            return False
        if req:
            radar_slack.record_outbox(resp.get("ts"), req, channel)
        return True
    except Exception:  # noqa: BLE001 - a phone mirror must never break the daemon
        return False


def _self_dm_channel(token: str) -> Optional[str]:
    if token in _SELF_DM_CACHE:
        return _SELF_DM_CACHE[token]
    from act import radar_slack
    auth = radar_slack.verify_token(token)
    if not auth.get("ok"):
        return None
    channel = radar_slack.find_self_dm(token, auth.get("user_id"))
    if channel:
        _SELF_DM_CACHE[token] = channel
    return channel


# --------------------------------------------------------------------------- #
# message builders (CONTRACT §5 copy)
# --------------------------------------------------------------------------- #
def msg_new_card(title: str) -> tuple[str, str]:
    return ("有新需求待审批", title)


def msg_done(title: str) -> tuple[str, str]:
    return ("任务完成", title)


def msg_needs_input(title: str) -> tuple[str, str]:
    return ("任务需要你输入", title)


def msg_auth(service: str) -> tuple[str, str]:
    return ("需要重新登录", service)


# --------------------------------------------------------------------------- #
# classifiers
# --------------------------------------------------------------------------- #
_AUTH_RE = re.compile(
    r"\b(auth(?:entication)?|unauthorized|401|login|log in|re-?login|"
    r"session expired|invalid[_ -]?token|please sign in|credentials?)\b",
    re.IGNORECASE,
)


def detect_auth_failure(log_text: str) -> bool:
    """True if an execution log looks like a credential/login failure."""
    if not log_text:
        return False
    return bool(_AUTH_RE.search(log_text))


def notify_new_card(title: str, req: Optional[str] = None) -> bool:
    return notify(*msg_new_card(title), req=req)


def notify_done(title: str, req: Optional[str] = None) -> bool:
    return notify(*msg_done(title), req=req)


def notify_needs_input(title: str, req: Optional[str] = None) -> bool:
    return notify(*msg_needs_input(title), req=req)


def notify_auth(service: str) -> bool:
    return notify(*msg_auth(service))
