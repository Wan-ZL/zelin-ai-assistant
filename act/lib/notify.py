"""macOS notifications + phone-channel mirror + transition classifiers (CONTRACT §5, §13).

State transitions surfaced as native notifications:
  - new card_sent (radar found a new requirement)  -> "有新需求待审批：<title>"
  - executing -> done                              -> "任务完成：<title>"
  - executing -> blocked (needs_input)             -> "任务需要你输入：<title>"
  - credential failure (log has auth/login words)  -> "需要重新登录：<service>"

v0.4 (§13): every notification is ALSO mirrored to the phone channel
(best-effort, never raises) so it reaches Zelin's phone. Pass ``req="R-xxx"``
so the mirrored message carries ``#R-xxx`` and is tracked in the channel's
outbox — reacting to it (Slack ✅ / iMessage 👍/❤️ tapback) then approves that
requirement.

v0.12 (§13, channel-pluggable): the mirror routes on config ``phone_channel``:
  - "imessage"        -> iMessage message-yourself thread (osascript ->
                         Messages.app; tracked in state/imessage_outbox.json)
  - "slack" / "none"  -> the legacy Slack self-DM path, which self-gates on
                         features.slack_radar + a readable user token — so
                         existing setups keep working with no config change.
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
    Also mirrors to the configured phone channel (§13) best-effort; the return
    value reflects ONLY the osascript path (unchanged behavior). ``req`` (an
    R-xxx id, optional) makes the mirrored copy reaction-approvable.
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
        _phone_mirror(title, body, req=req)   # best-effort phone mirror (§13)
    except Exception:  # noqa: BLE001 - the mirrors already never raise; belt+braces
        pass
    return ok


def _phone_mirror(title: str, body: str, req: Optional[str] = None) -> None:
    """Route the phone mirror by config (§13, channel-pluggable).

    ``phone_channel: imessage`` -> iMessage only. ``slack`` and ``none``
    (including a missing key) both take the legacy Slack path, which self-gates
    on features.slack_radar + token — an existing Slack setup keeps mirroring
    after an upgrade without touching its config, and a tokenless one stays a
    no-op exactly as before.
    """
    from act.lib import config as _config
    cfg = _config.load_config()
    if getattr(cfg, "phone_channel", "none") == "imessage":
        imessage_notify(title, body, req=req, cfg=cfg)
    else:
        slack_notify(title, body, req=req)


# --------------------------------------------------------------------------- #
# iMessage message-yourself thread (§13 outbound, v0.12)
# --------------------------------------------------------------------------- #
def imessage_notify(title: str, body: str, req: Optional[str] = None,
                    cfg=None, runner=None) -> bool:
    """Post ``🔔 <title>\\n<body>`` (+ ``#R-xxx`` when ``req`` given) into the
    user's own iMessage thread. Returns True when the send succeeded.

    Best-effort: channel not selected / no self_handle / osascript failure ->
    False, NEVER raises. When ``req`` is given it is recorded in
    state/imessage_outbox.json so radar_imessage can turn a 👍/❤️ tapback on
    the message into an inbox approve. ``runner`` is the injectable osascript
    send runner (tests).
    """
    try:
        # lazy import: radar_imessage owns all iMessage plumbing (same pattern
        # as slack_notify -> radar_slack below).
        from act import radar_imessage
        from act.lib import config as _config

        if cfg is None:
            cfg = _config.load_config()
        if getattr(cfg, "phone_channel", "none") != "imessage":
            return False
        handle = str(getattr(cfg, "imessage_self_handle", None) or "").strip()
        if not handle:
            return False
        text = f"🔔 {title}\n{body}"
        if req:
            text += f"\n#{req}"
        if not radar_imessage.send_imessage(handle, text, runner=runner):
            return False
        if req:
            radar_imessage.record_outbox(req)
        return True
    except Exception:  # noqa: BLE001 - a phone mirror must never break the daemon
        return False


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
