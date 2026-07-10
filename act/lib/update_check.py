"""In-app update check (CONTRACT §26).

Queries the GitHub releases API (``/releases/latest`` — GitHub already filters
out drafts and prereleases there) for the newest published version and caches
the answer in ``state/update_check.json``. actd calls :func:`attach` every
pass; a cache hit costs zero network and zero money.

Honesty rules (the trust posture of the whole product):
- At most ONE network attempt per 24h — failures (offline, rate-limited, bad
  response) consume the budget too, so a broken network can never turn the
  10s daemon pass into a retry storm. Failure = silently keep the cache.
  The ONLY exception is an explicit user click: the About page's「立即检查」
  runs ``python3 -m act.lib.update_check --force`` (see :func:`cli_status`),
  which skips the freshness gate for that one attempt — still ETag-validated,
  still stamping ``checked_at`` (so the periodic budget restarts from it),
  and still a hard no-op while ``updates.check_enabled`` is off.
- The request exposes ONLY the caller's IP and the current version string in
  the User-Agent (docs/TELEMETRY.md「更新检查」). No auth, no cookies, no ids.
- The dashboard projection (``update_available``) is emitted ONLY when a
  strictly newer semver exists AND ``updates.check_enabled`` is on. The app
  merely opens the release page — nothing is ever downloaded or run.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

from act import __version__
from act.lib import config

RELEASES_LATEST_URL = (
    "https://api.github.com/repos/Wan-ZL/zelin-ai-assistant/releases/latest"
)
RELEASES_PAGE_URL = "https://github.com/Wan-ZL/zelin-ai-assistant/releases"

STATE_PATH: Path = config.STATE_DIR / "update_check.json"
CHECK_INTERVAL_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 10

# fetch(etag) -> (http_status, new_etag, release_json_or_None).
# 304 means "cache still fresh" (release is None). Raises on transport
# failure. Injectable for tests — the suite never touches the network.
Fetch = Callable[[Optional[str]], Tuple[int, Optional[str], Optional[dict]]]


# --------------------------------------------------------------------------- #
# semver comparison
# --------------------------------------------------------------------------- #
_CORE_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


def parse_version(text) -> Optional[tuple]:
    """Parse ``v0.14.0`` / ``0.14.0-rc.1`` into a comparable tuple.

    Returns None when unparsable. Shape: ``(major, minor, patch, is_release,
    prerelease_ids)`` — a release (is_release=1) sorts after any prerelease
    of the same core, and prerelease identifiers compare per semver (numeric
    identifiers numerically, alphanumeric ones lexically, numeric < alpha).
    """
    if not text:
        return None
    s = str(text).strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    s = s.split("+", 1)[0]  # build metadata never affects precedence
    core, sep, pre = s.partition("-")
    m = _CORE_RE.match(core.strip())
    if not m:
        return None
    nums = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    if not sep:
        return nums + (1, ())
    ids = []
    for part in pre.split("."):
        if part.isdigit():
            ids.append((0, int(part), ""))
        else:
            ids.append((1, 0, part))
    return nums + (0, tuple(ids))


def is_newer(latest, current) -> bool:
    """True when ``latest`` is a strictly newer semver than ``current``.

    Unparsable input on either side -> False (never nag on garbage).
    """
    lt, ct = parse_version(latest), parse_version(current)
    if lt is None or ct is None:
        return False
    return lt > ct


# --------------------------------------------------------------------------- #
# state cache (state/update_check.json — this module is the only writer)
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    """Atomic write (.tmp + rename); failure must never break the caller."""
    try:
        config.ensure_state_dirs()
        tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        pass


def _parse_iso(ts) -> Optional[_dt.datetime]:
    if not ts:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# GitHub fetch (stdlib urllib only — no third-party deps beyond PyYAML)
# --------------------------------------------------------------------------- #
def _default_fetch(etag: Optional[str]):
    headers = {
        "Accept": "application/vnd.github+json",
        # the ONLY things this request exposes: IP + this version string
        "User-Agent": f"zelin-ai-assistant/{__version__} (update-check)",
    }
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(RELEASES_LATEST_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, resp.headers.get("ETag"), data
    except urllib.error.HTTPError as e:
        if e.code == 304:  # urllib surfaces 304 as an "error"
            return 304, etag, None
        raise


def _release_view(release: dict) -> Optional[dict]:
    """Project a GitHub release object into the cached fields (§26)."""
    tag = str(release.get("tag_name") or "").strip()
    if parse_version(tag) is None:
        return None
    latest = tag[1:] if tag[:1] in ("v", "V") else tag
    url = str(release.get("html_url") or "") or (
        RELEASES_PAGE_URL + "/tag/" + tag)
    pkg = None
    for asset in release.get("assets") or []:
        if (isinstance(asset, dict)
                and str(asset.get("name") or "").endswith(".pkg")
                and asset.get("browser_download_url")):
            pkg = str(asset["browser_download_url"])
            break
    return {"latest": latest, "url": url, "pkg_asset_url": pkg}


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def check(cfg: Optional[config.Config] = None, *,
          fetch: Optional[Fetch] = None,
          now: Optional[_dt.datetime] = None,
          force: bool = False) -> Optional[dict]:
    """Return ``{current, latest, url, pkg_asset_url, checked_at}`` or None.

    None = the check is disabled or nothing is known yet (first run offline).
    Never raises; performs at most one network attempt per 24h (a failed
    attempt consumes the budget too, so failures keep the cached answer and
    stay silent — CONTRACT §26).

    ``force=True`` (manual「立即检查」only — never the actd path) skips the
    24h freshness gate for this one call: the fetch still sends If-None-Match
    (a 304 is a valid fresh "no update"), and ``checked_at`` is stamped as
    usual, so the periodic budget restarts from the manual check. A disabled
    config still short-circuits before any network — force never overrides
    the privacy switch.
    """
    if cfg is None:
        cfg = config.load_config()
    if not getattr(cfg, "updates_check_enabled", True):
        return None

    state = _load_state()
    now_dt = now or _dt.datetime.now(_dt.timezone.utc)
    last = _parse_iso(state.get("checked_at"))
    fresh = (not force
             and last is not None
             and (now_dt - last).total_seconds() < CHECK_INTERVAL_SECONDS)

    if not fresh:
        # the attempt itself consumes the 24h budget, success or not
        state["checked_at"] = _iso(now_dt)
        try:
            status, etag, release = (fetch or _default_fetch)(state.get("etag"))
            if status == 200 and isinstance(release, dict):
                view = _release_view(release)
                if view is not None:
                    state.update(view)
                    state["etag"] = etag
            # 304: cached latest is still current — nothing else to update
        except Exception:  # noqa: BLE001 — offline/rate-limit: keep the cache
            pass
        _save_state(state)

    latest = state.get("latest")
    if not latest:
        return None
    return {
        "current": __version__,
        "latest": str(latest),
        "url": state.get("url") or RELEASES_PAGE_URL,
        "pkg_asset_url": state.get("pkg_asset_url"),
        "checked_at": state.get("checked_at"),
    }


def update_available(cfg: Optional[config.Config] = None, *,
                     fetch: Optional[Fetch] = None,
                     now: Optional[_dt.datetime] = None) -> Optional[dict]:
    """CONTRACT §26 dashboard payload — dict ONLY when latest > current."""
    info = check(cfg, fetch=fetch, now=now)
    if not info or not is_newer(info["latest"], info["current"]):
        return None
    return {
        "current": info["current"],
        "latest": info["latest"],
        "url": info["url"],
        "pkg_asset_url": info["pkg_asset_url"],
    }


def attach(dash: dict, cfg: Optional[config.Config] = None, *,
           fetch: Optional[Fetch] = None,
           now: Optional[_dt.datetime] = None) -> dict:
    """Set ``dash["update_available"]`` when an update is known (§26).

    Leaves the dict untouched otherwise — an ABSENT field is the contract for
    "no known update". Never raises: the update check must never take a
    dashboard write down with it.
    """
    try:
        info = update_available(cfg, fetch=fetch, now=now)
    except Exception:  # noqa: BLE001 — belt and braces (see docstring)
        return dash
    if info:
        dash["update_available"] = info
    return dash


def cli_status(force: bool = False, *,
               cfg: Optional[config.Config] = None,
               fetch: Optional[Fetch] = None,
               now: Optional[_dt.datetime] = None) -> dict:
    """§26 CLI payload behind ``python3 -m act.lib.update_check [--force]``.

    Unlike :func:`check`, this reports a transport failure honestly
    (``ok=False, error="network"``) so the About page's manual check can say
    "check failed" instead of pretending freshness — while the state file
    semantics stay identical (the failed attempt consumes the budget and the
    cached answer is kept). With ``updates.check_enabled`` off it never
    touches the network, force or not: ``ok=True, enabled=False`` plus
    whatever the cache last knew.
    """
    if cfg is None:
        cfg = config.load_config()
    enabled = bool(getattr(cfg, "updates_check_enabled", True))
    inner = fetch or _default_fetch
    errors: list = []

    def tracking_fetch(etag):
        try:
            return inner(etag)
        except Exception as e:  # noqa: BLE001 — recorded, then re-raised into check()'s keep-the-cache path
            errors.append(e)
            raise

    check(cfg, fetch=tracking_fetch, now=now, force=force)
    state = _load_state()
    latest = state.get("latest")
    out = {
        "ok": not errors,
        "enabled": enabled,
        "current": __version__,
        "latest": str(latest) if latest else None,
        "update_available": bool(enabled and latest
                                 and is_newer(latest, __version__)),
        "url": state.get("url") or RELEASES_PAGE_URL,
        "pkg_asset_url": state.get("pkg_asset_url"),
        "checked_at": state.get("checked_at"),
    }
    if errors:
        out["error"] = "network"
    return out


if __name__ == "__main__":
    # §26 CLI (About page「立即检查」+ debugging): one JSON line on stdout.
    # --force = skip the 24h budget for this attempt (user-initiated only).
    import sys
    print(json.dumps(cli_status("--force" in sys.argv[1:]), ensure_ascii=False))
