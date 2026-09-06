"""建议公开跟踪表 — publish opted-in feedback records as GitHub issues.

契约：CONTRACT §29（feedback 记录形状 + 「同时公开到 GitHub」opt-in 的同步器：
预写计数、重试先对账、瞬态不烧预算、无 token = 整体静默关闭）。

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
3. 重试先对账: a retry (the record carries EVIDENCE of an earlier send — a
   counted attempt, or just ``last_sync_attempt_at`` left by a rolled-back
   transient failure — and still no ``issue_number``: the previous POST may
   have half-succeeded) first LISTS recent issues (up to
   :data:`LIST_MAX_PAGES` pages, ``pull_request`` entries skipped — the
   /issues endpoint interleaves PRs) and scans for the marker; a hit just
   writes the number back (no second POST), and a failed list skips the
   record this pass（宁可晚发不可重发）.

Anti-loop guards on top: retries are SPACED — a record attempted less than
:data:`MIN_RETRY_AGE_SECONDS` ago is skipped without burning an attempt
(feedback.retry_pending 先例; actd passes come every ~10s, so without the
spacing one offline minute would eat every attempt) — and only NON-TRANSIENT
failures (4xx, response-shape surprises) count toward
:data:`MAX_SYNC_ATTEMPTS`, past which a record is never tried again (a schema
mistake or revoked token must not burn the GitHub API forever). Transient
trouble (connection errors, timeouts, HTTP 5xx) rolls the counter back and
costs only the spacing: the whole budget is ~2 minutes, and one sleep/wake or
captive-portal window must not permanently give up an opt-in the user
explicitly checked. ``publish`` absent or false — including every record
written before this feature existed — is never synced: publishing is an
explicit per-report opt-in.

The network call is an injection seam (``transport`` argument, same pattern as
feedback.Transport) so tests never touch the real API. Nothing here may raise
past :func:`sweep` — a broken sync must never take the daemon pass down.
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.error
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
    return secrets.read_path(_token_path(cfg))


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


def _utc_ts(ts: str) -> str:
    """Stored 'ts' normalized to UTC ISO ("…Z") for the PUBLIC issue body.
    Deliberately NOT the system-local rendering: %Z would print the user's
    timezone abbreviation — a coarse location signal — on a page anyone can
    read (PRIVACY §16 promises text+time+version only). Local-time display
    belongs to private surfaces. Unparseable ts falls back to the raw
    string."""
    dt = analytics.parse_ts(str(ts or ""))
    if dt is None:
        return str(ts or "")
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _marker(record: dict) -> str:
    """Invisible-in-render attribution line: ties an issue back to its local
    record, and lets the retry reconcile (below) recognize a half-created
    issue instead of posting a duplicate."""
    return f"<!-- feedback-id: {record.get('id')} -->"


def _issue_payload(record: dict) -> dict:
    text = str(record.get("text") or "")
    body = (
        f"{text}\n\n---\n"
        f"提交时间 / Submitted (UTC): {_utc_ts(str(record.get('ts') or ''))}\n"
        f"App 版本 / App version: {record.get('app_version') or 'unknown'}\n"
        "来自 Zelin's AI Assistant 内置提建议入口。\n"
        f"{_marker(record)}\n"
    )
    return {"title": _title(text), "body": body, "labels": [ISSUE_LABEL]}


# --------------------------------------------------------------------------- #
# Transport — plain urllib POST to api.github.com (no new dependency)
# --------------------------------------------------------------------------- #
def _parse_json_or_empty(data: bytes) -> object:
    """Body → JSON; a lost/torn body parses to {} → "no number" → counted as a
    failure, and the NEXT pass reconciles by marker instead of re-posting
    (the duplicate-guard docstring, piece 3)."""
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _make_transport(token: str) -> Transport:
    def send(method: str, url: str, payload: Optional[dict] = None) -> object:
        body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None else None)
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
        return _parse_json_or_empty(data)

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
        hit = _page_hit(resp, marker)
        if hit is not None:
            return hit
        if len(resp) < LIST_PAGE_SIZE:
            break  # short page = end of the listing, no point paging on
    return None


def _is_issue_with_marker(item, marker: str) -> bool:
    """A real issue (not a PR) whose body carries this record's marker."""
    return bool(isinstance(item, dict) and "pull_request" not in item
                and marker in str(item.get("body") or ""))


def _page_hit(resp: list, marker: str) -> Optional[dict]:
    return next((item for item in resp if _is_issue_with_marker(item, marker)), None)


# --------------------------------------------------------------------------- #
# Sweep — the one public entry point (daemon-safe, never raises)
# --------------------------------------------------------------------------- #
def _opted_in_unpublished(record) -> bool:
    """Well-formed, publish explicitly opted in, no issue yet (idempotence).
    Legacy records without the explicit True never sync."""
    return bool(isinstance(record, dict) and record.get("id")
                and record.get("publish") is True
                and not record.get("issue_number"))


def _sync_attempts(record: dict) -> int:
    """Attempt counter; an unreadable counter fails safe (= the cap, stop)."""
    try:
        return int(record.get("sync_attempts") or 0)
    except (TypeError, ValueError):
        return MAX_SYNC_ATTEMPTS


def _in_cooldown(record: dict) -> bool:
    """重试节流 (feedback.retry_pending 先例): skipped passes cost nothing —
    the counter only moves inside _sync_one's 预写, so an offline stretch
    burns ONE attempt per MIN_RETRY_AGE_SECONDS, not one per 10s pass.
    An unparseable timestamp fails open: the attempts cap still bounds it."""
    last = analytics.parse_ts(str(record.get("last_sync_attempt_at") or ""))
    if last is None:
        return False
    now = _dt.datetime.now(_dt.timezone.utc)
    return (now - last).total_seconds() < MIN_RETRY_AGE_SECONDS


def _is_pending(record) -> bool:
    """publish opted-in, not yet on GitHub, not given up, not in cooldown."""
    if not _opted_in_unpublished(record):
        return False
    if _sync_attempts(record) >= MAX_SYNC_ATTEMPTS:
        return False
    return not _in_cooldown(record)


def _is_transient(e: Exception) -> bool:
    """Transient = the next pass may genuinely succeed on its own: connection
    trouble (sleep/wake, captive portal, DNS, timeouts) and HTTP 5xx. These
    must not burn the MAX_SYNC_ATTEMPTS budget — the whole budget is
    ~2 minutes of wall clock, one bad-network window would permanently and
    silently drop a publish the user explicitly checked. Everything else
    (4xx, response-shape surprises) is counted: it won't heal by waiting,
    and the burn guard must stay bounded."""
    code = getattr(e, "code", None)  # urllib's HTTPError carries the status
    if isinstance(code, int):
        return code >= 500
    # URLError subclasses OSError; socket timeouts/ConnectionError are OSError
    return isinstance(e, (OSError, urllib.error.URLError))


def _sync_one(send: Transport, repo: str, record: dict) -> bool:
    """One publish attempt; True = the record now has its GitHub issue.
    All record rewrites are atomic tmp+replace via feedback.write_record.

    预写计数 (duplicate-guard piece 1): ``sync_attempts`` and
    ``last_sync_attempt_at`` (the retry-spacing clock) are bumped and
    persisted BEFORE any network call. If even that write fails (disk full,
    EPERM) the record is skipped with ZERO requests sent — an issue created
    now could never be remembered, and every later pass would mint another
    public duplicate, unbounded.

    Transient failures roll ``sync_attempts`` back afterwards (they don't
    burn the budget), but ``last_sync_attempt_at`` STAYS — it is both the
    60s spacing clock and the evidence that a request may have reached
    GitHub, which is what routes the next pass through the reconcile below
    instead of the fresh-POST fast path.
    """
    already_tried = int(record.get("sync_attempts") or 0)
    # Evidence of an earlier send — a counted attempt, or the timestamp a
    # rolled-back transient left behind. Read BEFORE the 预写 overwrites it.
    ever_sent = already_tried >= 1 or bool(record.get("last_sync_attempt_at"))
    if not _prewrite_attempt(record, already_tried):
        return False
    try:
        _record_issue(record, _locate_or_create(send, repo, record, ever_sent))
        return True
    except Exception as e:  # noqa: BLE001 - one bad record must not stop the sweep
        _note_failure(record, e, already_tried)
        return False


def _prewrite_attempt(record: dict, already_tried: int) -> bool:
    """预写计数: bump + persist BEFORE any network call; False = skip with
    ZERO requests sent (no bookkeeping => no network, period)."""
    try:
        record["sync_attempts"] = already_tried + 1
        record["last_sync_attempt_at"] = _iso_now()
        feedback.write_record(record)
        return True
    except Exception:  # noqa: BLE001
        return False


def _locate_or_create(send: Transport, repo: str, record: dict, ever_sent: bool) -> dict:
    """Reconcile by marker first when an earlier POST may have half-succeeded
    (a failed listing raises => the record is skipped this pass, 宁可晚发不可
    重发); otherwise POST the issue."""
    resp: Optional[dict] = None
    if ever_sent:
        resp = _find_existing(send, repo, record)
    if resp is None:
        created = _create_issue(send, repo, record)
        resp = created if isinstance(created, dict) else {}
    return resp


def _record_issue(record: dict, resp: dict) -> None:
    number = resp.get("number")
    if not isinstance(number, int):
        raise ValueError("issue create response carried no number")
    record["issue_number"] = number
    record["issue_url"] = str(resp.get("html_url") or "")
    record["issue_synced_at"] = _iso_now()
    record.pop("sync_error", None)
    feedback.write_record(record)


def _note_failure(record: dict, e: Exception, already_tried: int) -> None:
    """Transient failures roll the counter back (they don't burn the budget);
    ``last_sync_attempt_at`` stays as the spacing clock + send evidence."""
    if _is_transient(e):
        record["sync_attempts"] = already_tried
    record["sync_error"] = str(e)[:ERROR_CAP]
    try:
        feedback.write_record(record)
    except Exception:  # noqa: BLE001 - bookkeeping is best-effort too
        pass


def sweep(cfg: Optional[config.Config] = None,
          transport: Optional[Transport] = None) -> int:
    """Publish every pending opted-in record as a GitHub issue.

    Zero-cost when nothing is pending (one directory glob, no config/token/
    network work). Silent no-op when the feature flag is off or no token file
    exists. Returns the number of issues created. Never raises.
    """
    try:
        pending = _pending_records()
        if not pending:
            return 0
        return _publish_all(pending, cfg, transport)
    except Exception:  # noqa: BLE001 - sync must never break the daemon pass
        return 0


def _pending_records() -> list:
    """Every record that passes :func:`_is_pending` (one directory glob;
    unreadable files skipped)."""
    try:
        files = sorted(feedback.FEEDBACK_DIR.glob("*.json"))
    except OSError:
        return []
    pending: list = []
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _is_pending(record):
            pending.append(record)
    return pending


def _transport_for(cfg: config.Config, transport: Optional[Transport]) -> Optional[Transport]:
    """The injected transport, else a token-backed one; None = no credential
    (feature quietly off, gmail 哲学)."""
    if transport is not None:
        return transport
    token = _read_token(cfg)
    if not token:
        return None
    return _make_transport(token)


def _publish_all(pending: list, cfg: Optional[config.Config],
                 transport: Optional[Transport]) -> int:
    cfg = cfg or config.load_config()
    if not cfg.feature("feedback_sync"):
        return 0
    send = _transport_for(cfg, transport)
    if send is None:
        return 0
    repo = _repo(cfg)
    return sum(1 for record in pending if _sync_one(send, repo, record))
