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

Duplicate guard (公开 repo 烧不起重复 issue) — a bare POST-retry loop is
at-least-once, and two real failure shapes turn that into duplicates: the POST
lands but the RESPONSE is lost (timeout/torn body → no number recorded), and
the issue gets created but the write-back to disk fails (disk full / EPERM —
this repo has TCC/EPERM 前科), leaving nothing that even counts the attempt.
Three cooperating pieces make creation effectively once-only:

1. 预写计数: ``sync_attempts`` is bumped and PERSISTED **before** any network
   request; if that pre-write fails the record is skipped without sending
   anything — an issue the bookkeeping can't remember must never be created,
   or every later pass would create another.
2. body 标记: every issue body ends with ``<!-- feedback-id: <record id> -->``
   so an issue is always attributable to its record after the fact.
3. 重试先对账: a retry (``sync_attempts >= 1`` and still no ``issue_number``
   — the previous POST may have half-succeeded) first LISTS recent issues
   (up to :data:`LIST_MAX_PAGES` pages, ``pull_request`` entries skipped — the
   /issues endpoint interleaves PRs) and scans for the marker; a hit just
   writes the number back (no second POST), and a failed list skips the
   record this pass（宁可晚发不可重发）.

Anti-loop guards on top: retries are SPACED — a record attempted less than
:data:`MIN_RETRY_AGE_SECONDS` ago is skipped without burning an attempt
(feedback.retry_pending 先例; actd passes come every ~10s, so without the
spacing one offline minute would eat every attempt), and records at
:data:`MAX_SYNC_ATTEMPTS` are never tried again (a schema mistake or revoked
token must not burn the GitHub API forever). ``publish`` absent or false —
including every record written before this feature existed — is never synced:
publishing is an explicit per-report opt-in.

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
MIN_RETRY_AGE_SECONDS = 60  # feedback.py 先例: two attempts at least 60s apart
LIST_PAGE_SIZE = 100   # 对账扫描页宽（GitHub /issues 每页上限）
LIST_MAX_PAGES = 3     # 对账最多翻 3 页 — 半成功都是刚发生的，不会更深

# (method, url, payload) -> parsed JSON: dict for POST, list for GET (payload
# is None for GET). Raises on any transport failure. A 422 must surface as an
# exception with ``code == 422`` (urllib's HTTPError already does) so the
# labels-retry below can recognize it.
Transport = Callable[[str, str, Optional[dict]], object]


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


def _marker(record: dict) -> str:
    """Invisible-in-render attribution line: ties an issue back to its local
    record, and lets the retry reconcile (below) recognize a half-created
    issue instead of posting a duplicate."""
    return f"<!-- feedback-id: {record.get('id')} -->"


def _issue_payload(record: dict) -> dict:
    text = str(record.get("text") or "")
    body = (
        f"{text}\n\n---\n"
        f"提交时间 / Submitted: {_local_ts(str(record.get('ts') or ''))}\n"
        f"App 版本 / App version: {record.get('app_version') or 'unknown'}\n"
        "来自 Zelin's AI Assistant 内置提建议入口。\n"
        f"{_marker(record)}\n"
    )
    return {"title": _title(text), "body": body, "labels": [ISSUE_LABEL]}


# --------------------------------------------------------------------------- #
# Transport — plain urllib POST to api.github.com (no new dependency)
# --------------------------------------------------------------------------- #
def _make_transport(token: str) -> Transport:
    def send(method: str, url: str, payload: Optional[dict] = None) -> object:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
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
            # a 201 whose body was lost/torn parses to {} → "no number" →
            # counted as a failure, and the NEXT pass reconciles by marker
            # instead of re-posting (the duplicate-guard docstring, piece 3)
            return json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    return send


def _create_issue(send: Transport, repo: str, record: dict) -> object:
    """POST the issue; on a 422 (typically: the `suggestion` label does not
    exist on the target repo) retry ONCE without the labels field — an
    unlabeled public issue beats a lost report."""
    url = f"{API_BASE}/repos/{repo}/issues"
    payload = _issue_payload(record)
    try:
        return send("POST", url, payload)
    except Exception as e:  # noqa: BLE001 - only the 422 shape is retried
        if getattr(e, "code", None) != 422:
            raise
        payload.pop("labels", None)
        return send("POST", url, payload)


def _find_existing(send: Transport, repo: str, record: dict) -> Optional[dict]:
    """重试先对账: scan the newest issues for this record's body marker —
    up to LIST_MAX_PAGES pages of LIST_PAGE_SIZE (an active repo can push a
    day-old half-success past page 1), stopping early on a short page. Only
    a fully scanned window may report "not found" and unlock a re-POST.

    ``pull_request`` entries are skipped: the /issues endpoint interleaves
    PRs, and a PR body quoting the marker (e.g. a fix PR citing the issue
    text) must never be written back as the record's issue number.

    Deliberately NOT filtered by the ``suggestion`` label: the 422 fallback
    above creates label-less issues, and a ``labels=`` filter would miss
    exactly the half-created issue this reconcile exists to find. Raises on
    any transport/shape failure — the caller must then SKIP the record this
    pass (a blind re-POST is the one thing that can duplicate)."""
    marker = _marker(record)
    for page in range(1, LIST_MAX_PAGES + 1):
        url = (f"{API_BASE}/repos/{repo}/issues"
               f"?state=all&per_page={LIST_PAGE_SIZE}&page={page}")
        resp = send("GET", url, None)
        if not isinstance(resp, list):
            raise ValueError("issue list response was not a list")
        for item in resp:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            if marker in str(item.get("body") or ""):
                return item
        if len(resp) < LIST_PAGE_SIZE:
            break  # short page = end of the listing, no point paging on
    return None


# --------------------------------------------------------------------------- #
# Sweep — the one public entry point (daemon-safe, never raises)
# --------------------------------------------------------------------------- #
def _is_pending(record) -> bool:
    """publish opted-in, not yet on GitHub, not given up, not in cooldown."""
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
    if attempts >= MAX_SYNC_ATTEMPTS:
        return False
    # 重试节流 (feedback.retry_pending 先例): skipped passes cost nothing —
    # the counter only moves inside _sync_one's 预写, so an offline stretch
    # burns ONE attempt per MIN_RETRY_AGE_SECONDS, not one per 10s pass.
    # An unparseable timestamp fails open: the attempts cap still bounds it.
    last = analytics.parse_ts(str(record.get("last_sync_attempt_at") or ""))
    if last is not None:
        now = _dt.datetime.now(_dt.timezone.utc)
        if (now - last).total_seconds() < MIN_RETRY_AGE_SECONDS:
            return False
    return True


def _sync_one(send: Transport, repo: str, record: dict) -> bool:
    """One publish attempt; True = the record now has its GitHub issue.
    All record rewrites are atomic tmp+replace via feedback._write_record.

    预写计数 (duplicate-guard piece 1): ``sync_attempts`` and
    ``last_sync_attempt_at`` (the retry-spacing clock) are bumped and
    persisted BEFORE any network call. If even that write fails (disk full,
    EPERM) the record is skipped with ZERO requests sent — an issue created
    now could never be remembered, and every later pass would mint another
    public duplicate, unbounded.
    """
    already_tried = int(record.get("sync_attempts") or 0)
    try:
        record["sync_attempts"] = already_tried + 1
        record["last_sync_attempt_at"] = _iso_now()
        feedback._write_record(record)
    except Exception:  # noqa: BLE001 - no bookkeeping => no network, period
        return False
    try:
        resp: Optional[dict] = None
        if already_tried >= 1:
            # duplicate-guard piece 3: the previous POST may have half-
            # succeeded (response lost) — reconcile by body marker first; a
            # failed listing raises => this record is skipped this pass
            # (宁可晚发不可重发).
            resp = _find_existing(send, repo, record)
        if resp is None:
            created = _create_issue(send, repo, record)
            resp = created if isinstance(created, dict) else {}
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
        record["sync_error"] = str(e)[:ERROR_CAP]  # attempts already counted
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
