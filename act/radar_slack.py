"""Slack capture source for the requirement radar + the phone command surface.

Watches the things that need Zelin's attention on Slack — DMs, group DMs, and
@mentions in watched channels — and turns the actionable ones into registry
cards (coding OR paperwork/comms). Anything that is just FYI is skipped.

v0.4 (CONTRACT §13) — the SELF-DM (the im channel with yourself) is the phone
command channel. Zelin's OWN messages there are NOT skipped; they are handled as:
  - approval commands  批准/拒绝/打回/验收 R-xxx [反馈]  -> state/inbox/<uuid>.json
    (打回 requires nonempty feedback; guidance is replied when missing)
  - anything else (text and/or photo/video attachments)  -> quick capture
    (act/lib/quick_capture.py three-way: new_proposal / relates_to / ignore),
    with the result replied back into the self-DM.
Attachments are downloaded via url_private (files:read) to state/media/<ts>/;
videos are split into <=12 frames (ffmpeg if present, else mac/build/framegrab;
neither -> reply 视频暂不支持，请发图片). A ✅ (white_check_mark) reaction on an
outbound notification we sent (tracked in state/slack_outbox.json by
act/lib/notify.slack_notify) approves that requirement (reactions:read poll).

Design notes / landmines:
- Reading YOUR OWN DMs + mentions needs a Slack **user token** (xoxp-), NOT a
  bot token (xoxb-): bots can't see a user's DMs and can't call search.messages.
  Required user-token scopes: search:read, im:history, im:read, mpim:history,
  mpim:read, channels:history, groups:history, users:read, files:read,
  chat:write, reactions:read.
- Token resolution (CONTRACT §19, via act/lib/secrets.resolve_credential):
  config/secrets/slack-user-token.txt (App 设置窗口保存) -> config
  sources.slack_token_path -> legacy ~/Desktop/Keys/slack-user-token.txt.
  Never printed/logged.
- Assistant-authored self-DM posts are prefixed 🔔 (notify.slack_notify) or 🤖
  (replies here) and are skipped on re-scan, so the radar never quick-captures
  its own output (that would loop forever). Markers are NOT bumped past our own
  replies — a message Zelin sends mid-scan must survive to the next pass.
- Comms items are draft-only: the pipeline NEVER auto-sends Slack to OTHERS. An
  approved comms card produces a DRAFT for Zelin to review and send (the manager's
  rule + the never-auto tier). chat.postMessage here goes ONLY to the self-DM.
- This radar only touches the network + state/, so it is safe to run under a
  launchd agent (unlike the Obsidian radar, which is TCC-blocked from ~/Documents
  and must use crontab).
- Feature flag: features.slack_radar (CONTRACT §16); off -> scan() no-ops.
- v0.11 MCP fallback: while the xoxp token is stuck in admin approval, scan()
  does NOT just early-exit — every sources.slack_mcp_interval_minutes (default
  30; throttled via state/slack_mcp.marker, so the 3-minute launchd cadence is
  fine) it runs headless ``claude -p`` with the USER-level Slack MCP restricted
  to read/search tools only (_MCP_ALLOWED_TOOLS — never send/draft/reaction/
  canvas/schedule) and pushes actionable items through the same merge_or_new
  card pipeline. Once a token exists the native API path takes over untouched.

Run standalone:  python -m act.radar_slack --once
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Optional

from act.lib import analytics, config, health, registry, sanitize, secrets

SLACK_API = "https://slack.com/api/"
STATE_FILE = "slack_radar.json"        # per-channel last-seen ts markers
MCP_MARKER_FILE = "slack_mcp.marker"   # iso start-ts of the last SUCCESSFUL MCP pass
OUTBOX_FILE = "slack_outbox.json"      # outbound notification msgs (§13d): [{ts, req, channel, ...}]
DEFAULT_TOKEN_PATH = "~/Desktop/Keys/slack-user-token.txt"
MEDIA_DIR = config.STATE_DIR / "media"
FRAMEGRAB = config.HOME / "mac" / "build" / "framegrab"   # AVFoundation frame extractor (mac/build.sh)
MAX_FRAMES = 12

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
VIDEO_EXTS = {".mp4", ".mov"}

# our own self-DM posts (must never be re-captured): 🔔 = notify.slack_notify,
# 🤖 = command/capture replies posted below.
_ASSISTANT_PREFIXES = ("🔔", "🤖", "✅ 已批准")

# phone approval commands (§13): 批准/拒绝/打回/验收 R-xxx [comment...]
_CMD_RE = re.compile(r"^(批准|拒绝|打回|验收)\s+(R-\d+)(.*)$", re.DOTALL)
_CMD_MAP = {"批准": "approve", "拒绝": "reject", "打回": "rework", "验收": "accept"}

_ssl_ctx = ssl.create_default_context()


# --------------------------------------------------------------------------- #
# feature flag (§16)
# --------------------------------------------------------------------------- #
def feature_on(cfg: config.Config, name: str = "slack_radar") -> bool:
    """features.<name> (CONTRACT §16); default on when absent."""
    feats = getattr(cfg, "features", None)
    if not isinstance(feats, dict):
        feats = (getattr(cfg, "raw", None) or {}).get("features") or {}
    return bool(feats.get(name, True))


# --------------------------------------------------------------------------- #
# token + API
# --------------------------------------------------------------------------- #
def get_token(cfg: Optional[config.Config] = None) -> Optional[str]:
    """Resolve the user token per CONTRACT §19:
    config/secrets/slack-user-token.txt -> config path -> legacy default."""
    if cfg is None:
        cfg = config.load_config()
    return secrets.resolve_credential(
        secrets.SLACK_TOKEN_FILE,
        getattr(cfg, "slack_token_path", None),
        DEFAULT_TOKEN_PATH,
    )


def slack_api(method: str, token: str, params: Optional[dict] = None) -> dict:
    """POST to the Slack Web API. Never raises — returns {'ok': False, ...}."""
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(
        SLACK_API + method,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001 - network/JSON, never crash the daemon
        return {"ok": False, "error": f"transport:{e}"}


def verify_token(token: str) -> dict:
    """auth.test — confirm the token works and return the acting user id."""
    return slack_api("auth.test", token)


def download_file(token: str, url: str, dest: Path) -> bool:
    """Raw authorized GET (Slack ``url_private`` needs the Bearer header; the
    JSON helper above can't carry binary bodies). Never raises."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def post_message(token: str, channel: str, text: str) -> dict:
    """chat.postMessage (chat:write). Used ONLY for the self-DM."""
    return slack_api("chat.postMessage", token, {"channel": channel, "text": text})


def find_self_dm(token: str, my_id: Optional[str]) -> Optional[str]:
    """The im channel whose counterpart user == my_id (i.e. DM with yourself)."""
    if not my_id:
        return None
    cursor = None
    for _ in range(10):  # paginate defensively
        params: dict = {"types": "im", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = slack_api("conversations.list", token, params)
        if not resp.get("ok"):
            return None
        for c in resp.get("channels", []):
            if c.get("user") == my_id:
                return c.get("id")
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            return None
    return None


# --------------------------------------------------------------------------- #
# markers
# --------------------------------------------------------------------------- #
def _marker_path() -> Path:
    return config.STATE_DIR / STATE_FILE


def _load_markers() -> dict:
    try:
        return json.loads(_marker_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_markers(m: dict) -> None:
    p = _marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# --------------------------------------------------------------------------- #
# outbox (§13d) — outbound notification messages awaiting a ✅ reaction
# --------------------------------------------------------------------------- #
def _outbox_path() -> Path:
    return config.STATE_DIR / OUTBOX_FILE


def load_outbox() -> list:
    try:
        data = json.loads(_outbox_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_outbox(items: list) -> None:
    p = _outbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_outbox(ts: Optional[str], req: Optional[str], channel: Optional[str] = None) -> None:
    """Track an outbound notification message so a ✅ reaction can approve it.
    Called by act/lib/notify.slack_notify. Never raises."""
    if not ts or not req:
        return
    try:
        items = load_outbox()
        items.append({
            "ts": str(ts),
            "req": str(req),
            "channel": channel,
            "sent_at": _iso_now(),
        })
        save_outbox(items)
    except Exception:  # noqa: BLE001
        pass


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# inbox decisions (same schema the Mac app writes — CONTRACT §3/§10)
# --------------------------------------------------------------------------- #
def _write_inbox(action: str, req_id: str, comment: Optional[str] = None) -> Path:
    config.ensure_state_dirs()
    payload = {"id": req_id, "action": action, "comment": comment or None,
               "ts": _iso_now()}
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# fetch new messages that may need attention
# --------------------------------------------------------------------------- #
def fetch_new_messages(token: str, my_id: str, cfg: config.Config,
                       markers: dict, self_dm: Optional[str] = None) -> list[dict]:
    """Collect new messages from DMs, group DMs, and watched channels.

    Returns a list of {channel, channel_type, ts, user, text, permalink}.
    Updates ``markers`` in place (per-channel last ts).

    §13: in the SELF-DM (``self_dm``) my own messages are NOT skipped — they
    come back with ``channel_type="self"`` (+ a ``files`` list) and are handled
    by :func:`_handle_self_message` as commands / quick capture.
    """
    out: list[dict] = []

    def history(channel_id: str, channel_type: str) -> None:
        is_self = self_dm is not None and channel_id == self_dm
        oldest = markers.get(channel_id, "0")
        params = {"channel": channel_id, "oldest": oldest, "limit": 50}
        resp = slack_api("conversations.history", token, params)
        if not resp.get("ok"):
            return
        newest_ts = oldest
        for m in resp.get("messages", []):
            ts = m.get("ts", "0")
            if float(ts) <= float(oldest or 0):
                continue
            sub = m.get("subtype")
            if sub and not (is_self and sub == "file_share"):
                continue                     # joins/leaves/bot noise
            if m.get("user") == my_id:      # my own messages
                if is_self:
                    text = (m.get("text") or "")
                    # skip our own 🔔/🤖 posts — never re-capture assistant output
                    if not text.strip().startswith(_ASSISTANT_PREFIXES):
                        out.append({
                            "channel": channel_id,
                            "channel_type": "self",
                            "ts": ts,
                            "user": my_id,
                            "text": text,
                            "files": m.get("files") or [],
                            "permalink": None,
                        })
                newest_ts = max(newest_ts, ts, key=lambda x: float(x))
                continue
            text = m.get("text", "")
            # in a plain channel, only care if I'm @mentioned; DMs = always
            if channel_type == "channel" and f"<@{my_id}>" not in text:
                newest_ts = max(newest_ts, ts, key=lambda x: float(x))
                continue
            out.append({
                "channel": channel_id,
                "channel_type": channel_type,
                "ts": ts,
                "user": m.get("user"),
                "text": text,
                "permalink": _permalink(token, channel_id, ts),
            })
            newest_ts = max(newest_ts, ts, key=lambda x: float(x))
        markers[channel_id] = newest_ts

    # 1) DMs + group DMs (user token required)
    convs = slack_api("conversations.list", token,
                      {"types": "im,mpim", "limit": 200})
    if convs.get("ok"):
        for c in convs.get("channels", []):
            ctype = "mpim" if c.get("is_mpim") else "im"
            history(c["id"], ctype)

    # 2) explicitly watched channels (from config), mentions only
    for ch in (cfg.slack_channels or []):
        cid = ch.get("id") if isinstance(ch, dict) else ch
        if cid:
            history(cid, "channel")

    return out


def _permalink(token: str, channel: str, ts: str) -> Optional[str]:
    resp = slack_api("chat.getPermalink", token,
                     {"channel": channel, "message_ts": ts})
    return resp.get("permalink") if resp.get("ok") else None


# --------------------------------------------------------------------------- #
# LLM extraction -> requirements
# --------------------------------------------------------------------------- #
_EXTRACT_PROMPT = """你在帮 Zelin 从 Slack 消息里挑出"需要他处理的事"。下面是若干条 Slack 消息（DM / 群 / 频道@提及）。

对每条判断：这是否需要 Zelin 采取行动？纯 FYI / 闲聊 / 已解决的，跳过。
需要行动的，输出一个 JSON 对象，字段：
- summary: 大白话一句话，说清要 Zelin 做什么
- type: 之一 [comms, paperwork, code, research, review, other]
- tier: T0(纯调研/草稿/自动) | T1(一键) | T2(要花钱/大事)
- needs_reply: true/false（是否需要回一条 Slack 消息）
- plan: 步骤数组
- permalink: 原消息链接（原样抄回）

只输出一个 JSON 数组（可能为空 []）。不要多余文字。

消息：
"""


def _default_extractor(prompt: str) -> subprocess.CompletedProcess:
    from act.executor import _runner_env
    prompt, _ = sanitize.scrub(prompt)
    return subprocess.run(
        ["claude", "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
        env=_runner_env(),
    )


def _parse_json_array(text: str) -> list:
    """Tolerant: find the first [...] block."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        val = json.loads(text[start:end + 1])
        return val if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


def extract_requirements(messages: list[dict],
                         extractor: Optional[Callable[[str], subprocess.CompletedProcess]] = None
                         ) -> list[dict]:
    if not messages:
        return []
    if extractor is None:
        extractor = _default_extractor
    lines = []
    for m in messages:
        lines.append(
            f"- [{m.get('channel_type')} {m.get('ts')}] "
            f"{m.get('text')}  (permalink: {m.get('permalink')})"
        )
    prompt = _EXTRACT_PROMPT + "\n".join(lines)
    try:
        proc = extractor(prompt)
        return _parse_json_array((getattr(proc, "stdout", "") or ""))
    except (OSError, subprocess.SubprocessError):
        return []


# --------------------------------------------------------------------------- #
# v0.11 MCP fallback — no xoxp token yet? headless claude + user-level Slack MCP
# --------------------------------------------------------------------------- #
# Read/search-only tool set for the fallback agent. NEVER add send/draft/
# reaction/canvas/schedule tools here — the fallback must not write anything to
# Slack (same red line as analyze._EXPAND_ALLOWED_TOOLS, which is the
# production precedent that the user-level Slack MCP is reachable headless).
_MCP_ALLOWED_TOOLS = ",".join([
    "mcp__slack__slack_search_public_and_private",
    "mcp__slack__slack_search_public",
    "mcp__slack__slack_read_channel",
    "mcp__slack__slack_read_thread",
    "mcp__slack__slack_search_users",
    "mcp__slack__slack_read_user_profile",
    "mcp__slack__slack_search_channels",
])

_MCP_LOOKBACK_DEFAULT_H = 24   # no marker yet -> look back this far
_MCP_LOOKBACK_CAP_H = 48       # stale marker (long lid-closed) -> cap the window

# Judgment wording kept in sync with _EXTRACT_PROMPT above (the native path):
# same "需要他处理的事 / 纯 FYI 跳过" bar, so the two paths file the same cards.
_MCP_SCAN_PROMPT = """你在帮 {owner} 从 Slack 消息里挑出"需要他处理的事"。用可用的 Slack 只读工具（搜索/读频道/读 thread），找出自 {since}（UTC）以来的新消息：
1. 发给 {owner} 的 DM / 群 DM；
2. @提及 {owner}{owner_handle} 的消息；
3. 这些关注频道里的新消息：{channels}。

对每条判断：这是否需要 {owner} 采取行动？纯 FYI / 闲聊 / 已解决的，跳过。
需要行动的，输出一个 JSON 对象，字段：
- title: 大白话一句话，说清要 {owner} 做什么
- summary: 一两句背景 + 具体要做什么
- who: 发消息的人
- channel: 频道或 DM 名称
- date: 消息日期 YYYY-MM-DD
- quote: 原文关键句（逐字抄）

只输出一个 JSON 数组（可能为空 []）。不要多余文字。
"""

_MCP_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _mcp_marker_path() -> Path:
    return config.STATE_DIR / MCP_MARKER_FILE


def _read_mcp_marker() -> Optional[_dt.datetime]:
    try:
        raw = _mcp_marker_path().read_text(encoding="utf-8").strip()
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (OSError, ValueError):
        return None


def _write_mcp_marker(ts: _dt.datetime) -> None:
    config.ensure_state_dirs()
    _mcp_marker_path().write_text(
        ts.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8")


def _default_mcp_runner(prompt: str) -> subprocess.CompletedProcess:
    from act.executor import _runner_env
    from act.radar import _claude_bin   # cron/launchd PATH 兜底（radar.py 事故注）
    prompt, _ = sanitize.scrub(prompt)
    return subprocess.run(
        # NOTE: prompt must come BEFORE --allowedTools — the claude CLI parses
        # --allowedTools as variadic and would swallow a trailing positional
        # prompt (same landmine as analyze._default_runner, verified 2026-07-07).
        [
            _claude_bin(), "-p", prompt,
            "--output-format", "text",
            "--allowedTools", _MCP_ALLOWED_TOOLS,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=_runner_env(),
    )


def _parse_mcp_output(raw: str) -> Optional[list]:
    """radar._parse_extraction's tolerant parse (strip a ```json fence, then
    find the array), except failure is distinguishable: returns None when NO
    JSON array can be recovered (-> marker must not advance), [] only for a
    genuine empty result (-> successful pass, marker advances)."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = _MCP_JSON_ARRAY_RE.search(text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, list):
        return None
    return [d for d in data if isinstance(d, dict)]


def mcp_scan(cfg: config.Config,
             runner: Optional[Callable[[str], subprocess.CompletedProcess]] = None) -> int:
    """One token-less fallback pass. Returns the number of new cards created.

    Throttling: launchd fires every 3 minutes, but a real scan only runs once
    per ``sources.slack_mcp_interval_minutes`` (marker = start time of the last
    SUCCESSFUL pass). A not-yet-due call returns 0 SILENTLY — no radar_skip
    beacon, or the analytics log would fill with non-events. On failure the
    marker is untouched, so the next due pass re-covers the same window (this
    is what closes multi-hour lid-closed gaps).
    """
    interval = int(getattr(cfg, "slack_mcp_interval_minutes", 30) or 30)
    now = _dt.datetime.now(_dt.timezone.utc)
    marker = _read_mcp_marker()
    if marker is not None and (now - marker) < _dt.timedelta(minutes=interval):
        return 0   # not due yet — silent by design (see docstring)

    since = marker or (now - _dt.timedelta(hours=_MCP_LOOKBACK_DEFAULT_H))
    floor = now - _dt.timedelta(hours=_MCP_LOOKBACK_CAP_H)
    if since < floor:
        since = floor
    channels = ", ".join(
        str((ch.get("name") or ch.get("id")) if isinstance(ch, dict) else ch)
        for ch in (cfg.slack_channels or [])
    ) or "（无——只看 DM 和 @提及）"
    owner = (getattr(cfg, "owner_name", "") or "").strip() or "用户"
    handle = (getattr(cfg, "owner_slack_user_id", "") or "").strip()
    prompt = _MCP_SCAN_PROMPT.format(
        since=since.strftime("%Y-%m-%dT%H:%M:%SZ"), channels=channels,
        owner=owner,
        owner_handle=f"（Slack user id {handle}）" if handle else "")

    if runner is None:
        runner = _default_mcp_runner
    try:
        proc = runner(prompt)
    except (OSError, subprocess.SubprocessError) as e:   # incl. TimeoutExpired
        _note_skip("mcp_failed: " + f"{type(e).__name__}: {e}"[:120])
        return 0
    if getattr(proc, "returncode", 1) != 0:
        err = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        _note_skip("mcp_failed: " + f"exit {getattr(proc, 'returncode', '?')}: {err}"[:120])
        return 0
    items = _parse_mcp_output(getattr(proc, "stdout", "") or "")
    if items is None:
        out = (getattr(proc, "stdout", "") or "").strip()
        _note_skip("mcp_failed: " + f"unparseable output: {out}"[:120])
        return 0

    # Same card pipeline as the native path in scan() below — Requirement with
    # the same defaults, reconciled through registry.merge_or_new.
    created = 0
    for r in items:
        if not isinstance(r, dict) or not (r.get("title") or r.get("summary")):
            continue
        new = registry.Requirement(
            id=registry.next_id(),
            title=(r.get("title") or r.get("summary") or "")[:80],
            summary=r.get("summary") or r.get("title"),
            type="comms",
            tier="T1",
            status="card_sent",
            hardness="soft",
            plan=[],
            sources=[{
                "who": r.get("who") or "slack",
                "channel": r.get("channel") or "slack",
                "date": r.get("date"),
                "quote": r.get("quote") or r.get("summary"),
                "ref": None,
            }],
            notes="from Slack (MCP fallback)",
        )
        registry.merge_or_new(new)
        created += 1

    _write_mcp_marker(now)   # = this pass's start; messages during it survive
    analytics.log_event("radar_scan", source="slack", mode="mcp",
                        new_cards=created)
    try:
        health.update_radar_health("slack", ok=True)
    except Exception:  # noqa: BLE001 - health must never break the pass
        pass
    return created


# --------------------------------------------------------------------------- #
# §13 self-DM: media (photos + video frames)
# --------------------------------------------------------------------------- #
def _extract_frames(video: Path, outdir: Path,
                    max_frames: int = MAX_FRAMES) -> Optional[list[Path]]:
    """Split a video into <= max_frames JPEG frames.

    ffmpeg when installed, else the AVFoundation helper mac/build/framegrab
    (built by mac/build.sh; CLI assumed: ``framegrab <video> <outdir> <max>``).
    Returns None when NEITHER tool exists (caller replies 视频暂不支持) and []
    when a tool ran but produced nothing.
    """
    ffmpeg = shutil.which("ffmpeg")
    grab = str(FRAMEGRAB) if FRAMEGRAB.exists() else None
    if not ffmpeg and not grab:
        return None
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        if ffmpeg:
            subprocess.run(
                [ffmpeg, "-y", "-i", str(video), "-vf", "fps=1",
                 "-frames:v", str(max_frames), str(outdir / "frame_%02d.jpg")],
                capture_output=True, timeout=300,
            )
        else:
            subprocess.run(
                [grab, str(video), str(outdir), str(max_frames)],
                capture_output=True, timeout=300,
            )
    except (OSError, subprocess.SubprocessError):
        return []
    frames = sorted(
        p for p in outdir.iterdir()
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    return frames[:max_frames]


def _collect_media(token: str, files: list, ts: str) -> tuple[list[Path], list[str]]:
    """Download self-DM attachments to state/media/<ts>/.

    Returns (image_paths, problems): images kept as-is, videos become frame
    images; ``problems`` carries user-facing complaints (e.g. no frame tool).
    """
    dest_dir = MEDIA_DIR / str(ts or "0")
    images: list[Path] = []
    problems: list[str] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        url = f.get("url_private") or f.get("url_private_download")
        if not url:
            continue
        name = os.path.basename(str(f.get("name") or f.get("id") or "file"))
        ext = Path(name).suffix.lower()
        dest = dest_dir / name
        if ext in IMAGE_EXTS:
            if download_file(token, url, dest):
                images.append(dest)
        elif ext in VIDEO_EXTS:
            if not download_file(token, url, dest):
                continue
            frames = _extract_frames(dest, dest_dir / f"frames_{dest.stem}")
            if frames is None:
                problems.append("视频暂不支持，请发图片")
            else:
                images.extend(frames)
        # other file types: ignored (quick capture is photos/videos + text)
    return images, problems


# --------------------------------------------------------------------------- #
# §13 self-DM: commands + quick capture
# --------------------------------------------------------------------------- #
def _post_reply(token: str, channel: str, text: str) -> dict:
    """Reply into the self-DM. The 🤖 prefix marks it as assistant output so the
    next fetch skips it (never bump markers here — a message Zelin sends while a
    slow capture is running must still be picked up by the next scan)."""
    return post_message(token, channel, f"🤖 {text}")


def _handle_self_message(m: dict, token: str, self_dm: str, cfg: config.Config,
                         extractor: Optional[Callable] = None) -> None:
    """One self-DM message: approval command FIRST (no LLM), else quick capture."""
    text = (m.get("text") or "").strip()

    # (b) command parsing BEFORE any LLM call
    cmd = _CMD_RE.match(text)
    if cmd:
        verb, rid, rest = cmd.group(1), cmd.group(2), (cmd.group(3) or "").strip()
        action = _CMD_MAP[verb]
        if action == "rework" and not rest:
            _post_reply(token, self_dm,
                        f"打回需要带反馈，例如：打回 {rid} 参数表要补上依据")
            return
        _write_inbox(action, rid, rest or None)
        analytics.log_event("slack_command", action=action, req=rid)
        _post_reply(token, self_dm, f"收到：{verb} {rid}（已写入处理队列）")
        return

    # media attachments -> images (photos as-is, videos -> frames)
    images: list[Path] = []
    problems: list[str] = []
    if m.get("files"):
        images, problems = _collect_media(token, m["files"], m.get("ts") or "0")
    if problems and not images and not text:
        _post_reply(token, self_dm, problems[0])
        return

    # quick capture (text and/or images) — three-way decision
    desc = text
    if images:
        desc = (
            (text + "\n\n" if text else "")
            + "Read these images first (use the Read tool on each absolute path "
              "below), then decide based on what they show:\n"
            + "\n".join(str(p) for p in images)
        )
    if not desc.strip():
        return
    from act.lib import quick_capture
    try:
        res = quick_capture.capture(desc, cfg, extractor=extractor)
        reply = quick_capture.apply_result(res, cfg)
    except Exception as e:  # noqa: BLE001 - reply the failure, don't kill the scan
        reply = f"快速捕获失败：{e}"
    if problems:
        reply = problems[0] + "；" + reply
    _post_reply(token, self_dm, reply)


# --------------------------------------------------------------------------- #
# §13d reactions approval: ✅ on a tracked outbound notification = approve
# --------------------------------------------------------------------------- #
_OUTBOX_MAX_AGE_S = 14 * 86400   # stop polling (and drop) entries older than this


def poll_reaction_approvals(token: str, self_dm: Optional[str]) -> int:
    """reactions.get over unconsumed outbox entries; ✅ -> inbox approve (once)."""
    items = load_outbox()
    if not items:
        return 0
    approved = 0
    changed = False
    keep: list = []
    now = time.time()
    for it in items:
        if not isinstance(it, dict):
            changed = True
            continue
        try:
            too_old = (now - float(it.get("ts") or 0)) > _OUTBOX_MAX_AGE_S
        except (TypeError, ValueError):
            too_old = True
        if too_old:
            changed = True
            continue                      # prune — bounds the polling volume
        keep.append(it)
        if it.get("consumed"):
            continue
        channel = it.get("channel") or self_dm
        ts, rid = it.get("ts"), it.get("req")
        if not (channel and ts and rid):
            continue
        resp = slack_api("reactions.get", token, {"channel": channel, "timestamp": ts})
        if not resp.get("ok"):
            continue
        reactions = (resp.get("message") or {}).get("reactions") or []
        names = {r.get("name") for r in reactions if isinstance(r, dict)}
        if "white_check_mark" in names:
            _write_inbox("approve", str(rid), None)
            it["consumed"] = True
            changed = True
            approved += 1
            analytics.log_event("slack_reaction_approve", req=str(rid))
            _post_reply(token, channel, f"✅ 已批准 {rid}（点✅）")
    if changed:
        save_outbox(keep)
    return approved


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def _note_skip(reason: str) -> None:
    """radar_skip beacon + health mark on an early-exit (contract §E/§F).
    Belt: both helpers already never raise, but a skipped pass must NEVER
    turn into a crashed pass, so swallow everything anyway."""
    try:
        analytics.log_event("radar_skip", source="slack", reason=reason)
        health.update_radar_health("slack", ok=False, skip_reason=reason)
    except Exception:  # noqa: BLE001
        pass


def scan(cfg: Optional[config.Config] = None,
         fetcher: Optional[Callable] = None,
         extractor: Optional[Callable] = None,
         mcp_runner: Optional[Callable] = None) -> int:
    """One capture pass. Returns the number of new requirement cards created.

    With a token the native API path below runs, unchanged. Without one (admin
    approval pending) the v0.11 MCP fallback takes over — see :func:`mcp_scan`.
    """
    if cfg is None:
        cfg = config.load_config()
    if not feature_on(cfg, "slack_radar"):
        _note_skip("disabled")
        return 0
    token = get_token(cfg)
    if not token:
        if getattr(cfg, "slack_mcp_fallback", True):
            return mcp_scan(cfg, runner=mcp_runner)
        _note_skip("no_credentials")
        return 0
    auth = verify_token(token)
    if not auth.get("ok"):
        _note_skip("connect_failed")
        return 0
    my_id = auth.get("user_id") or cfg.owner_slack_user_id
    self_dm = find_self_dm(token, my_id)
    markers = _load_markers()

    if fetcher is None:
        messages = fetch_new_messages(token, my_id, cfg, markers, self_dm=self_dm)
    else:
        messages = fetcher(token, my_id, cfg, markers)

    # §13: split my own self-DM messages (commands / quick capture) from the rest
    self_msgs = sorted(
        (m for m in messages if m.get("channel_type") == "self"),
        key=lambda m: float(m.get("ts") or 0),
    )
    others = [m for m in messages if m.get("channel_type") != "self"]

    reqs = extract_requirements(others, extractor=extractor)
    created = 0
    for r in reqs:
        if not isinstance(r, dict) or not r.get("summary"):
            continue
        new = registry.Requirement(
            id=registry.next_id(),
            title=(r.get("summary") or "")[:80],
            summary=r.get("summary"),
            type=r.get("type") or "comms",
            tier=r.get("tier") or "T1",
            status="card_sent",
            hardness="soft",
            plan=r.get("plan") or [],
            sources=[{
                "who": "slack",
                "channel": "slack",
                "date": None,
                "quote": r.get("summary"),
                "ref": r.get("permalink"),
            }],
            notes=f"needs_reply={r.get('needs_reply')} · from Slack",
        )
        registry.merge_or_new(new)
        created += 1

    # §13 phone surface: commands + quick capture, then ✅-reaction approvals
    if self_dm:
        for m in self_msgs:
            try:
                _handle_self_message(m, token, self_dm, cfg, extractor=extractor)
            except Exception:  # noqa: BLE001 - one bad message must not kill the pass
                pass
        try:
            poll_reaction_approvals(token, self_dm)
        except Exception:  # noqa: BLE001
            pass

    _save_markers(markers)
    try:
        health.update_radar_health("slack", ok=True)
    except Exception:  # noqa: BLE001 - health must never break the pass
        pass
    analytics.log_event("radar_scan", source="slack", messages=len(messages),
                        new_cards=created, self_dm_msgs=len(self_msgs) or None)
    return created


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="radar_slack")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true", help="verify token only")
    args = parser.parse_args(argv)
    cfg = config.load_config()
    if args.check:
        tok = get_token(cfg)
        if not tok:
            print("no token at", secrets.SECRETS_DIR / secrets.SLACK_TOKEN_FILE,
                  "or", getattr(cfg, "slack_token_path", None) or DEFAULT_TOKEN_PATH)
            return 1
        auth = verify_token(tok)
        print(json.dumps({k: auth.get(k) for k in ("ok", "user", "user_id", "team", "error")},
                         ensure_ascii=False))
        if auth.get("ok"):
            dm = find_self_dm(tok, auth.get("user_id"))
            print(f"self-DM channel: {dm or 'NOT FOUND (send yourself one message first)'}")
        return 0 if auth.get("ok") else 1
    n = scan(cfg)
    print(f"slack radar: {n} new card(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
