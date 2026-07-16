"""Slack in-app setup helpers — the Settings·Slack section's python side (§15).

Three jobs, all consumed by the Mac app's Slack settings section
(mac/Sources/SettingsSlack.swift) through the runtime python (CONTRACT §19):

1. **App manifest generation** — :func:`manifest_json` builds the Slack app
   manifest from ``REQUIRED_USER_SCOPES`` (the scopes the radar + the in-app
   pickers actually need, nothing more). ``config/slack-app-manifest.json`` /
   ``.yaml`` are the copies users paste into api.slack.com;
   tests/test_slack_setup.py drift-guards file == generator.

2. **Workspace directory** — :func:`directory` lists channels
   (``conversations.list``, public+private) and people (``users.list``),
   paginated and cached in ``state/slack_directory.json`` (TTL 1h) so the
   channel/watch-people pickers don't hammer the API on every Settings visit.

3. **Graceful errors** — Slack error codes (``missing_scope``,
   ``invalid_auth``, …) become bilingual plain-language sentences via
   :func:`failures.pick`, each naming the next action. The app renders the
   ``message`` field verbatim — no raw codes in the happy path (the code
   still rides along in ``error`` for honesty/debugging).

CLI (what the app actually calls):
    python3 -m act.lib.slack_setup --manifest              # manifest JSON
    python3 -m act.lib.slack_setup --directory [--refresh] # directory JSON
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from typing import Callable, Optional

from act.lib import config, failures

DIRECTORY_CACHE_PATH = config.STATE_DIR / "slack_directory.json"
CACHE_TTL_SECONDS = 3600
_PAGE_LIMIT = 200
_MAX_PAGES = 25            # 25 × 200 = 5000 entries — enough for any sane workspace

# The MINIMAL user-token scope set (docs/SLACK_SETUP.md; keep in sync with the
# radar docstring in act/radar_slack.py). The radar itself needs the history/
# search/files/chat/reactions set; the Settings pickers additionally need
# channels:read + groups:read (conversations.list of public/private channels)
# — users:read was already required by the radar.
REQUIRED_USER_SCOPES: list = [
    "search:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "users:read",
    "files:read",
    "chat:write",
    "reactions:read",
    # §40 capture receipts: emoji ack on captured self-DM messages. Missing
    # scope only costs the ack (reactions.add fails soft), never the capture.
    "reactions:write",
]


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def manifest_dict() -> dict:
    """The Slack app manifest, generated from REQUIRED_USER_SCOPES."""
    return {
        "display_information": {
            "name": "Zelin AI Engineer Capture",
            "description": (
                "Personal capture channel. Reads own DMs and mentions to "
                "draft tasks. Drafts only, never auto-sends."
            ),
            "background_color": "#1a1d21",
        },
        "oauth_config": {
            "scopes": {
                "user": list(REQUIRED_USER_SCOPES),
            },
        },
        "settings": {
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def manifest_json() -> str:
    return json.dumps(manifest_dict(), ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------- #
# error classification — Slack API error code -> bilingual next-action sentence
# --------------------------------------------------------------------------- #
def error_message(code: str, lang: Optional[str] = None) -> str:
    """Plain-language sentence (current UI language) for a Slack error code."""
    c = str(code or "unknown_error")
    if c == "missing_scope":
        return failures.pick(
            "token 缺少列频道/成员所需的权限——用「复制 App Manifest」按钮的最新内容"
            "更新你的 Slack app（api.slack.com/apps → App Manifest → 粘贴 → Save），"
            "再 Reinstall to Workspace 换新 token 粘贴回来",
            "The token is missing the scopes needed to list channels/members — "
            "update your Slack app with the latest \"Copy App Manifest\" content "
            "(api.slack.com/apps → App Manifest → paste → Save), then Reinstall "
            "to Workspace and paste the fresh token here",
            lang,
        )
    if c in ("invalid_auth", "not_authed", "token_revoked", "token_expired",
             "account_inactive"):
        return failures.pick(
            "token 无效或已失效——到 api.slack.com/apps → OAuth & Permissions "
            "重新复制 User OAuth Token 粘贴保存",
            "The token is invalid or expired — copy the User OAuth Token again "
            "at api.slack.com/apps → OAuth & Permissions and save it here",
            lang,
        )
    if c == "ratelimited":
        return failures.pick(
            "Slack 限流了——等一分钟再点「刷新」",
            "Slack is rate-limiting — wait a minute and click Refresh again",
            lang,
        )
    if c == "no_token":
        return failures.pick(
            "还没保存 Slack token——先完成上面的第 3 步",
            "No Slack token saved yet — finish step 3 above first",
            lang,
        )
    if c.startswith("transport:"):
        return failures.pick(
            "网络问题，连不上 Slack——稍后点「刷新」重试",
            "Network trouble reaching Slack — click Refresh again later",
            lang,
        )
    return failures.pick(
        f"Slack 返回了错误 {c}——稍后重试；反复出现就点「让 AI 修」",
        f"Slack returned error {c} — retry later; if it persists use Fix with AI",
        lang,
    )


def _fail(code: str) -> dict:
    return {"ok": False, "error": str(code), "message": error_message(code)}


# --------------------------------------------------------------------------- #
# directory (channels + people), paginated
# --------------------------------------------------------------------------- #
def _default_api(method: str, token: str, params: Optional[dict] = None) -> dict:
    from act import radar_slack
    return radar_slack.slack_api(method, token, params)


def _paginate(method: str, token: str, base_params: dict, list_key: str,
              api: Callable) -> tuple:
    """Collect all pages of a cursor-paginated Slack list call.

    Returns (items, None) on success or (None, error_code) on the first
    failing page — partial results are never presented as complete.
    """
    items: list = []
    cursor = None
    for _ in range(_MAX_PAGES):
        params = dict(base_params)
        params["limit"] = _PAGE_LIMIT
        if cursor:
            params["cursor"] = cursor
        resp = api(method, token, params)
        if not resp.get("ok"):
            return None, str(resp.get("error") or "unknown_error")
        items.extend(resp.get(list_key) or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return items, None


def list_channels(token: str, api: Optional[Callable] = None) -> tuple:
    """All non-archived public+private channels as [{id, name}], name-sorted."""
    api = api or _default_api
    raw, err = _paginate(
        "conversations.list", token,
        {"types": "public_channel,private_channel", "exclude_archived": "true"},
        "channels", api)
    if err:
        return None, err
    out = []
    for c in raw:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        out.append({"id": str(c["id"]), "name": str(c.get("name") or c["id"])})
    out.sort(key=lambda c: c["name"].lower())
    return out, None


def list_users(token: str, api: Optional[Callable] = None) -> tuple:
    """Active human members as [{id, name, real_name}], name-sorted.

    ``name`` is the @handle (what config's watch_people expects — the manager
    pack matches its first-name token); bots / deleted users / Slackbot are
    dropped.
    """
    api = api or _default_api
    raw, err = _paginate("users.list", token, {}, "members", api)
    if err:
        return None, err
    out = []
    for u in raw:
        if not isinstance(u, dict) or not u.get("id"):
            continue
        if u.get("deleted") or u.get("is_bot") or u["id"] == "USLACKBOT":
            continue
        name = str(u.get("name") or u["id"])
        real = str((u.get("profile") or {}).get("real_name")
                   or u.get("real_name") or "")
        out.append({"id": str(u["id"]), "name": name, "real_name": real})
    out.sort(key=lambda u: u["name"].lower())
    return out, None


# --------------------------------------------------------------------------- #
# cache (state/slack_directory.json — app-side cache, not a pipeline contract)
# --------------------------------------------------------------------------- #
def _read_cache() -> Optional[dict]:
    try:
        data = json.loads(DIRECTORY_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("ok") else None
    except (OSError, json.JSONDecodeError):
        return None


def _cache_fresh(data: dict) -> bool:
    try:
        ts = _dt.datetime.fromisoformat(
            str(data.get("fetched_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    now = _dt.datetime.now(_dt.timezone.utc)
    return (now - ts).total_seconds() < CACHE_TTL_SECONDS


def _write_cache(data: dict) -> None:
    try:
        config.ensure_state_dirs()
        tmp = DIRECTORY_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(DIRECTORY_CACHE_PATH)
    except OSError:
        pass                       # cache is best-effort, never fail the fetch


def directory(refresh: bool = False, token: Optional[str] = None,
              api: Optional[Callable] = None) -> dict:
    """Channels + people for the Settings pickers (cached).

    Returns ``{"ok": true, "fetched_at": ISO, "channels": [...], "users":
    [...]}`` or ``{"ok": false, "error": code, "message": <bilingual>}``.
    """
    if not refresh:
        cached = _read_cache()
        if cached and _cache_fresh(cached):
            return cached
    if token is None:
        from act import radar_slack
        token = radar_slack.get_token()
    if not token:
        return _fail("no_token")
    channels, err = list_channels(token, api=api)
    if err:
        return _fail(err)
    users, err = list_users(token, api=api)
    if err:
        return _fail(err)
    data = {
        "ok": True,
        "fetched_at": _dt.datetime.now(_dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channels": channels,
        "users": users,
    }
    _write_cache(data)
    return data


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m act.lib.slack_setup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", action="store_true",
                       help="print the Slack app manifest JSON")
    group.add_argument("--directory", action="store_true",
                       help="print channels + people JSON (cached 1h)")
    parser.add_argument("--refresh", action="store_true",
                        help="with --directory: bypass the cache")
    args = parser.parse_args(argv)
    if args.manifest:
        print(manifest_json(), end="")
        return 0
    result = directory(refresh=args.refresh)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
