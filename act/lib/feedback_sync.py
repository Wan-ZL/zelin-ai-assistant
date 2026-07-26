"""建议公开跟踪表 — publish opted-in feedback records as GitHub issues.

Records land in ``state/feedback/<id>.json`` via act/lib/feedback.py; the ones
the user explicitly checked「同时公开到 GitHub 建议跟踪表」carry ``publish:
true``. Each actd pass, :func:`sweep` turns every published record that has no
``issue_number`` yet into an issue on the configured public repo
(``feedback_sync.repo``, default the upstream project repo) so anyone can see
what was suggested and whether it got done. Status flows one way only (v1):
the maintainer closes/labels issues on GitHub; nothing is pulled back here.

Credential philosophy = gmail's: the token file (``feedback_sync.token_path``,
default ``config/secrets/github-feedback-token.txt``, resolved against the
pipeline root) simply not existing means the whole module is a silent no-op —
no health complaint, no log spam. The token needs only ``issues: write`` on
the target repo.

Anti-loop guard: every failed create bumps ``sync_attempts`` on the record;
records at :data:`MAX_SYNC_ATTEMPTS` are never tried again (a schema mistake
or revoked token must not burn the GitHub API forever). ``publish`` absent or
false — including every record written before this feature existed — is never
synced: publishing is an explicit per-report opt-in.

The network call is an injection seam (``transport`` argument, same pattern as
feedback.Transport) so tests never touch the real API. Nothing here may raise
past :func:`sweep` — a broken sync must never take the daemon pass down.
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from act.lib import analytics, config, feedback, secrets

API_BASE = "https://api.github.com"
DEFAULT_REPO = "Wan-ZL/zelin-ai-assistant"
DEFAULT_TOKEN_PATH = "config/secrets/github-feedback-token.txt"  # nosec B105 - file NAME, not a secret
USER_AGENT = "zelin-ai-assistant-feedback-sync"
ISSUE_LABEL = "suggestion"
TITLE_CAP = 60         # issue title = first chars of the suggestion text
ERROR_CAP = 200        # like feedback.py: first 200 chars of the last error
TIMEOUT_SECONDS = 10   # inline in the daemon pass — keep the block bounded
MAX_SYNC_ATTEMPTS = 3  # terminal: never retried past this (API burn guard)

# (url, payload) -> parsed response dict, raises on any transport failure.
# A 422 must surface as an exception with ``code == 422`` (urllib's HTTPError
# already does) so the labels-retry below can recognize it.
Transport = Callable[[str, dict], dict]


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Config plumbing — repo, token file (missing file = module off)
# --------------------------------------------------------------------------- #
def _repo(cfg: config.Config) -> str:
    return str(getattr(cfg, "feedback_sync_repo", "") or "").strip() or DEFAULT_REPO


def _token_path(cfg: config.Config) -> Path:
    raw = str(getattr(cfg, "feedback_sync_token_path", "") or "").strip() \
        or DEFAULT_TOKEN_PATH
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = config.HOME / p  # pipeline root, same anchor as redaction_terms_file
    return p


def _read_token(cfg: config.Config) -> Optional[str]:
    """First token line of the token file, or None (missing/empty = feature
    quietly off — the gmail no-credential philosophy)."""
    return secrets._read_path(_token_path(cfg))


# --------------------------------------------------------------------------- #
# Issue payload — title/body from the local record
# --------------------------------------------------------------------------- #
def _title(text: str) -> str:
    """First TITLE_CAP chars of the whitespace-collapsed suggestion text
    (GitHub titles are single-line)."""
    t = " ".join(str(text or "").split())
    if len(t) > TITLE_CAP:
        return t[:TITLE_CAP] + "…"
    return t or "用户建议"


def _local_ts(ts: str) -> str:
    """Stored UTC 'ts' rendered in the SYSTEM LOCAL timezone for display —
    the issue reader shouldn't have to do UTC math. Unparseable ts falls back
    to the raw string (storage stays UTC either way)."""
    dt = analytics.parse_ts(str(ts or ""))
    if dt is None:
        return str(ts or "")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _issue_payload(record: dict) -> dict:
    text = str(record.get("text") or "")
    body = (
        f"{text}\n\n---\n"
        f"提交时间 / Submitted: {_local_ts(str(record.get('ts') or ''))}\n"
        f"App 版本 / App version: {record.get('app_version') or 'unknown'}\n"
        "来自 Zelin's AI Assistant 内置提建议入口。\n"
    )
    return {"title": _title(text), "body": body, "labels": [ISSUE_LABEL]}


# --------------------------------------------------------------------------- #
# Transport — plain urllib POST to api.github.com (no new dependency)
# --------------------------------------------------------------------------- #
def _make_transport(token: str) -> Transport:
    def send(url: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        # B310: url is built from API_BASE (https) + the configured repo slug
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # nosec B310
            data = resp.read()
        try:
            parsed = json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001 - a 201 with odd body still parsed below
            parsed = None
        return parsed if isinstance(parsed, dict) else {}

    return send


def _create_issue(send: Transport, repo: str, record: dict) -> dict:
    """POST the issue; on a 422 (typically: the `suggestion` label does not
    exist on the target repo) retry ONCE without the labels field — an
    unlabeled public issue beats a lost report."""
    url = f"{API_BASE}/repos/{repo}/issues"
    payload = _issue_payload(record)
    try:
        return send(url, payload)
    except Exception as e:  # noqa: BLE001 - only the 422 shape is retried
        if getattr(e, "code", None) != 422:
            raise
        payload.pop("labels", None)
        return send(url, payload)


# --------------------------------------------------------------------------- #
# Sweep — the one public entry point (daemon-safe, never raises)
# --------------------------------------------------------------------------- #
def _is_pending(record) -> bool:
    """publish opted-in, not yet on GitHub, not given up."""
    if not isinstance(record, dict) or not record.get("id"):
        return False
    if record.get("publish") is not True:
        return False  # explicit opt-in only — legacy records never sync
    if record.get("issue_number"):
        return False  # already published (idempotence)
    try:
        attempts = int(record.get("sync_attempts") or 0)
    except (TypeError, ValueError):
        attempts = MAX_SYNC_ATTEMPTS  # unreadable counter: fail safe, stop
    return attempts < MAX_SYNC_ATTEMPTS


def _sync_one(send: Transport, repo: str, record: dict) -> bool:
    """One create attempt; rewrites the record with the outcome (atomic
    tmp+replace via feedback._write_record). True = issue created."""
    try:
        resp = _create_issue(send, repo, record)
        number = resp.get("number")
        if not isinstance(number, int):
            raise ValueError("issue create response carried no number")
        record["issue_number"] = number
        record["issue_url"] = str(resp.get("html_url") or "")
        record["issue_synced_at"] = _iso_now()
        record.pop("sync_error", None)
        feedback._write_record(record)
        return True
    except Exception as e:  # noqa: BLE001 - one bad record must not stop the sweep
        record["sync_attempts"] = int(record.get("sync_attempts") or 0) + 1
        record["sync_error"] = str(e)[:ERROR_CAP]
        try:
            feedback._write_record(record)
        except Exception:  # noqa: BLE001 - bookkeeping is best-effort too
            pass
        return False


def sweep(cfg: Optional[config.Config] = None,
          transport: Optional[Transport] = None) -> int:
    """Publish every pending opted-in record as a GitHub issue.

    Zero-cost when nothing is pending (one directory glob, no config/token/
    network work). Silent no-op when the feature flag is off or no token file
    exists. Returns the number of issues created. Never raises.
    """
    try:
        pending: list = []
        try:
            files = sorted(feedback.FEEDBACK_DIR.glob("*.json"))
        except OSError:
            return 0
        for path in files:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _is_pending(record):
                pending.append(record)
        if not pending:
            return 0

        cfg = cfg or config.load_config()
        if not cfg.feature("feedback_sync"):
            return 0
        send = transport
        if send is None:
            token = _read_token(cfg)
            if not token:
                return 0  # no credential = feature quietly off (gmail 哲学)
            send = _make_transport(token)

        repo = _repo(cfg)
        created = 0
        for record in pending:
            if _sync_one(send, repo, record):
                created += 1
        return created
    except Exception:  # noqa: BLE001 - sync must never break the daemon pass
        return 0
