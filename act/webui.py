"""Local web dashboard server (v1) — stdlib-only HTTP front end for actd.

`python -m act.webui` serves a small web UI on 127.0.0.1 that renders the same
five-lane board the Mac app shows and lets you act on cards. It is the second
client of the frozen two-file contract (docs/CONTRACT.md §2/§3/§6/§10): it
*reads* ``state/dashboard.json`` (actd writes it, the UI never does) and *acts*
by atomically writing ``state/inbox/<uuid>.json`` decision files (actd consumes
and deletes them). Exactly the same read-only + inbox-write pattern as the Mac
app (mac/Sources/AppDelegate.swift ``writeInboxFile``) and the planned iOS app.

Security model (a local service that can APPROVE work must resist a malicious
local web page doing CSRF / DNS-rebinding):
  * Bound to 127.0.0.1 ONLY — never 0.0.0.0.
  * Per-install token in ``state/webui.token`` (0600), required on EVERY /api/*
    request via the ``X-Webui-Token`` header. The token is injected into
    index.html SERVER-SIDE at serve time so the same-origin page has it; it is
    never returned by any endpoint a cross-origin page could read.
  * Strict Host validation on every request (blocks DNS-rebinding) + strict
    Origin validation on POST (blocks cross-origin form/CSRF posts).
  * Static serving is a fixed allow-list of files — no path joining with the
    request path, so directory traversal is impossible.

Stdlib only (http.server) — no new dependencies beyond PyYAML (unused here).
This module deliberately does NOT wire into install.sh / launchd / the Swift
app: auto-start and a menu-bar entry point are a follow-up.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hmac
import json
import os
import re
import secrets
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from act.lib import config

# --------------------------------------------------------------------------- #
# inbox action allow-list
# --------------------------------------------------------------------------- #
# The full set actd will actually apply (CONTRACT §10). Source of truth =
# act/actd.py: the no-requirement dispatch in ``process_inbox()`` plus the
# ``_apply_decision()`` elif whitelist. Keep in sync with actd; anything not in
# here is rejected with 400 before an inbox file is ever written.
ALLOWED_ACTIONS = frozenset({
    # requirement-level (actd._apply_decision elif chain + §37 set_title)
    "approve", "reject", "comment", "raise", "trash", "restore", "pin",
    "accept", "rework", "done_external", "abort_execution", "stop_to_review",
    "revert_review", "defer", "archive", "unarchive", "set_title",
    # no-requirement / suggestion-level (actd.process_inbox dispatch)
    "capture", "feedback", "merge_review", "merge_apply", "merge_dismiss",
    "import_claude_sessions", "weekly_digest_now",
})

# Fields we accept from a POST body and forward into the inbox file. Everything
# else is dropped; ``ts`` is always (re)stamped server-side so the client can
# never spoof it. This mirrors the Mac app's inbox payload shapes (§3/§10/§21).
_INBOX_KEYS = ("id", "action", "comment", "text", "ids", "title")

# A scalar ``id`` in a POST body is forwarded verbatim into the inbox file and
# ends up in merge_review.job_path() as ``MERGE_DIR / f"{id}.json"`` (via
# actd _apply_merge_decision for merge_apply/merge_dismiss), so an unsanitized
# id like ``../../../tmp/x`` would build a path OUTSIDE state/merge/. This
# conservative allow-list admits every legitimate id — requirement ids
# ``R-\d+`` (act/lib/registry.py _ID_RE / next_id) and merge-session ids
# ``MS-`` + 8 hex (act/merge_review.py new_suggestion_id) — while blocking all
# traversal: no ``/``, no ``.`` (so no ``..`` and no dotfile), no leading dash,
# no NUL, capped length. Actions with no scalar id (capture/feedback/weekly
# digest carry ``ids`` or nothing) are unaffected; only a PRESENT id is checked.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_TOKEN_HEADER = "X-Webui-Token"  # nosec B105 - header NAME, not a secret
_TOKEN_PLACEHOLDER = "__WEBUI_TOKEN__"  # nosec B105 - substitution placeholder, not a secret
_DEFAULT_PORT = 8787
_PORT_FALLBACKS = 10  # try _DEFAULT_PORT .. _DEFAULT_PORT+9, then an ephemeral port

# webui/ source assets live at the repo root next to act/ (NOT under state).
_WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"

# Static allow-list: request path -> (filename on disk, content-type). No path
# is ever built from the request, so ../ traversal cannot escape this map.
_STATIC = {
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #
def token_path() -> Path:
    return config.STATE_DIR / "webui.token"


def load_or_create_token() -> str:
    """Return the per-install token, generating + persisting it (0600) once."""
    config.ensure_state_dirs()
    p = token_path()
    try:
        existing = p.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    # O_EXCL-free but O_CREAT|O_TRUNC with a 0600 mode — single writer, and the
    # umask can only make it *more* restrictive; chmod pins it regardless.
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok + "\n")
    finally:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return tok


# --------------------------------------------------------------------------- #
# inbox write (mirror the Mac app / CONTRACT §3 exactly)
# --------------------------------------------------------------------------- #
def write_inbox(payload: dict) -> str:
    """Atomically write a decision file; return its filename.

    Mirrors mac/Sources/AppDelegate.swift writeInboxFile: a fresh uuid name,
    ``comment`` defaulting to null for requirement-level actions, and ``ts``
    stamped by the writer. Atomic = write ``<name>.tmp`` then os.replace (the
    ``.tmp`` never matches actd's ``*.json`` glob, so a partial file is never
    consumed).
    """
    config.ensure_state_dirs()
    action = payload["action"]
    rec: dict = {}
    for k in _INBOX_KEYS:
        if k in payload:
            rec[k] = payload[k]
    # requirement-level actions always carry an explicit comment (null when
    # absent) — matches the Swift writer's dict["comment"] = comment ?? NSNull().
    if "id" in rec and action not in ("merge_apply", "merge_dismiss"):
        rec.setdefault("comment", None)
    rec["action"] = action
    rec["ts"] = _iso_now()

    # capture keeps the Mac app's ``capture-`` filename prefix (§10/§15); every
    # other action gets a plain uuid name.
    stem = f"capture-{uuid.uuid4()}" if action == "capture" else str(uuid.uuid4())
    target = config.INBOX_DIR / f"{stem}.json"
    tmp = target.with_suffix(".json.tmp")
    data = json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target.name


# --------------------------------------------------------------------------- #
# request handler
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    server_version = "ZelinWebUI/1"
    protocol_version = "HTTP/1.1"
    # Bound a slow-drip (slowloris-style) local client: StreamRequestHandler
    # arms this on the connection socket, so a client that stalls mid-request
    # times out instead of pinning a worker thread indefinitely.
    timeout = 15

    # -- helpers ----------------------------------------------------------- #
    def _send(self, code: int, ctype: str, body: bytes,
              extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Harden the served page; NEVER emit Access-Control-Allow-Origin — a
        # cross-origin page must not be able to read any response.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        # Anti-framing (clickjacking): the token-armed page must never render
        # inside another page's iframe — Safari/Firefox load public->local
        # frames, and one baited click on 批准 dispatches an autonomous agent.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _host_ok(self) -> bool:
        """Reject anything whose Host header is not our loopback origin.

        This is the DNS-rebinding defense: a page at http://evil.example that
        rebinds its DNS to 127.0.0.1 would still send Host: evil.example, so it
        never matches and cannot reach /api/* even though the socket is local.
        """
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts  # type: ignore[attr-defined]

    def _origin_ok(self) -> bool:
        """POST-only: Origin must be our exact loopback origin (CSRF defense)."""
        origin = (self.headers.get("Origin") or "").strip().lower()
        return origin in self.server.allowed_origins  # type: ignore[attr-defined]

    def _token_ok(self) -> bool:
        got = self.headers.get(_TOKEN_HEADER) or ""
        want = self.server.token  # type: ignore[attr-defined]
        return hmac.compare_digest(got, want)

    # -- GET --------------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Host check on EVERY request (page load included) — blocks DNS rebinding.
        if not self._host_ok():
            self._json(403, {"error": "bad host"})
            return

        if path == "/":
            self._serve_index()
            return
        if path in _STATIC:
            self._serve_static(path)
            return
        if path == "/api/dashboard":
            if not self._token_ok():
                self._json(401, {"error": "missing or bad token"})
                return
            self._serve_dashboard()
            return
        self._json(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _serve_index(self) -> None:
        try:
            html = (_WEBUI_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            self._json(500, {"error": "index.html missing"})
            return
        # Inject the token server-side so the same-origin page holds it without
        # any endpoint ever handing it to a cross-origin reader.
        html = html.replace(_TOKEN_PLACEHOLDER, self.server.token)  # type: ignore[attr-defined]
        self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))

    def _serve_static(self, path: str) -> None:
        fname, ctype = _STATIC[path]
        try:
            body = (_WEBUI_DIR / fname).read_bytes()
        except OSError:
            self._json(404, {"error": "asset missing"})
            return
        self._send(200, ctype, body)

    def _serve_dashboard(self) -> None:
        try:
            body = config.DASHBOARD_PATH.read_bytes()
        except OSError:
            body = b"{}"
        self._send(200, "application/json; charset=utf-8", body)

    # -- POST -------------------------------------------------------------- #
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Drain the request body FIRST so the socket is clean for keep-alive —
        # rejecting before reading it would leave bytes that misparse as the
        # next request. Oversized bodies close the connection instead.
        raw = self._read_body()
        if raw is None:
            self.close_connection = True
            self._json(400, {"error": "bad body length"})
            return
        if not self._host_ok():
            self._json(403, {"error": "bad host"})
            return
        if not self._origin_ok():
            self._json(403, {"error": "bad origin"})
            return
        if not self._token_ok():
            self._json(401, {"error": "missing or bad token"})
            return
        if path != "/api/inbox":
            self._json(404, {"error": "not found"})
            return
        self._handle_inbox(raw)

    def _read_body(self) -> Optional[bytes]:
        """Read exactly Content-Length bytes (capped). None = bad/oversized."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 1_000_000:
            return None
        return self.rfile.read(length)

    def _handle_inbox(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "body must be a json object"})
            return
        action = payload.get("action")
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            self._json(400, {"error": f"unknown action: {action!r}"})
            return
        # Defense-in-depth: a present scalar ``id`` must match a real id shape
        # so it can never traverse out of state/merge/ downstream. A missing /
        # null id is fine (capture/feedback/digest); a present unsafe one is 400.
        rid = payload.get("id")
        if rid is not None and not (isinstance(rid, str) and _SAFE_ID_RE.match(rid)):
            self._json(400, {"error": "invalid id"})
            return
        # Fail closed on field TYPES too: free-text fields must be str (or
        # null/absent — the Mac app writes ``comment: null``). A non-string
        # forwarded verbatim would poison the inbox file and wedge actd's
        # ``(comment or "").strip()``-style handling every pass.
        for key in ("comment", "text"):
            if payload.get(key) is not None and not isinstance(payload[key], str):
                self._json(400, {"error": f"{key} must be a string"})
                return
        # §37 set_title: title must be a short string — fail closed here so a
        # poison/oversize value never reaches the inbox file (actd re-checks).
        title = payload.get("title")
        if title is not None and not (isinstance(title, str)
                                      and 0 < len(title) <= 64):
            self._json(400, {"error": "title must be a string of 1-64 chars"})
            return
        ids = payload.get("ids")
        if ids is not None and not (isinstance(ids, list)
                                    and all(isinstance(x, str) for x in ids)):
            self._json(400, {"error": "ids must be a list of strings"})
            return
        try:
            name = write_inbox(payload)
        except OSError as e:
            # Log the detail server-side; the client gets a generic message so
            # the response body never echoes a local filesystem path.
            self.log_error("inbox write failed: %s", e)
            self._json(500, {"error": "internal error"})
            return
        self._json(200, {"ok": True, "file": name})

    # quieter, single-line logging to stderr (default BaseHTTPRequestHandler is
    # noisy); keep it so a curl proof still shows requests.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("webui %s - %s\n" % (self.address_string(), fmt % args))


# --------------------------------------------------------------------------- #
# server factory
# --------------------------------------------------------------------------- #
class _WebUIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: Optional[int] = None):
    """Build (but do not serve) the loopback server. Returns (httpd, url, token).

    Binds 127.0.0.1 ONLY. Tries the requested/default port, then a small range,
    then an ephemeral port (0) so a stale instance never wedges startup.
    """
    token = load_or_create_token()
    candidates = ([port] if port else
                  [_DEFAULT_PORT + i for i in range(_PORT_FALLBACKS)] + [0])
    httpd = None
    last_err: Optional[OSError] = None
    for cand in candidates:
        try:
            httpd = _WebUIServer(("127.0.0.1", cand), _Handler)
            break
        except OSError as e:
            last_err = e
            continue
    if httpd is None:
        raise SystemExit(f"webui: could not bind any port ({last_err})")

    bound_port = httpd.server_address[1]
    httpd.token = token  # type: ignore[attr-defined]
    httpd.allowed_hosts = {  # type: ignore[attr-defined]
        f"127.0.0.1:{bound_port}", f"localhost:{bound_port}",
    }
    httpd.allowed_origins = {  # type: ignore[attr-defined]
        f"http://127.0.0.1:{bound_port}", f"http://localhost:{bound_port}",
    }
    url = f"http://127.0.0.1:{bound_port}"
    return httpd, url, token


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m act.webui",
                                 description="Local web dashboard for actd.")
    ap.add_argument("--port", type=int, default=None,
                    help=f"port to bind (default {_DEFAULT_PORT}, falls back)")
    args = ap.parse_args(argv)

    httpd, url, _token = make_server(args.port)
    print(f"Zelin AI Assistant — web dashboard on {url}", flush=True)
    print("  local only (127.0.0.1), per-install token required; "
          "leave this running and open the URL in a browser.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nwebui: shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
