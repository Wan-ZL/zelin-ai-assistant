"""Board server hosting + UI deploy probes — the pure half of two doctor rows.

CONTRACT §54.1 (the board server is a resident launchd agent / systemd unit;
the shell only connects) and §56.5 (the `ui` install step). ``act/doctor.py``
wraps these into ``CheckResult`` rows; this module holds the platform-neutral
logic so it stays testable and keeps doctor.py under the file cap (防腐 #1).

Why a loopback HTTP probe and not ``launchctl list``: the pid column proves the
process was spawned, not that it bound the port — a shell-spawned or hand-run
``python3 -m server`` holding the port leaves the launchd job crash-looping
while the shell happily connects to the stray one. Only ``GET /api/health``
answering tells the truth.

stdlib only (§0 第 7 条); never imports ``server`` (act never does).
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Optional

LABEL = "com.zelin.aiassistant.server"      # launchd label (macOS)
UNIT = "zelin-server.service"               # systemd --user unit (Linux)
FAILURE_ID = "board_server_down"            # §25 catalog id for every non-OK row
UI_TCC_FAILURE_ID = "ui_build_tcc_blocked"  # §56.5 `ui=skipped_tcc`

PROBE_TIMEOUT = 2.0
LOG_HINT = "tail -20 ~/Library/Logs/zelin-ai-assistant/server.launchd.log"
UI_LOG_HINT = "tail -40 ~/Library/Logs/zelin-ai-assistant/ui-build.log"


def health_probe(port: int) -> dict:
    """``{"state": "ok" | "down" | "unavailable", "status": int | None, "text": str}``.

    "down" = connection refused / timeout / non-2xx; "unavailable" = the probe
    is switched off (``AIASSISTANT_HTTP_PROBE=0`` — the test sandbox default,
    so the suite never reads a developer's live board server). Loopback only,
    read-only, 2 s ceiling.
    """
    if os.environ.get("AIASSISTANT_HTTP_PROBE", "1") == "0":
        return {"state": "unavailable", "status": None, "text": "probe disabled"}
    url = "http://127.0.0.1:%d/api/health" % int(port)
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as resp:  # nosec B310 - loopback only
            status = int(resp.status)
            state = "ok" if 200 <= status < 300 else "down"
            return {"state": state, "status": status, "text": "HTTP %d" % status}
    except urllib.error.HTTPError as exc:
        return {"state": "down", "status": int(exc.code), "text": "HTTP %d" % exc.code}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"state": "down", "status": None, "text": str(exc)[-200:]}


def hosted(listing: str, darwin: bool) -> bool:
    """Is the server under the service manager? darwin: the label appears in
    ``launchctl list`` (loaded — the pid may be "-" mid-throttle; column 3);
    linux: the unit appears in ``systemctl --user list-units`` (column 1; a
    ● failed bullet is stripped first)."""
    column, name = (2, LABEL) if darwin else (0, UNIT)
    for line in listing.splitlines():
        parts = line.replace("●", " ").split()
        if len(parts) > column and parts[column] == name:
            return True
    return False


def restart_cmd(darwin: bool) -> str:
    """Hard-restart the server — the shell's failure dialog prints the darwin
    line first (§54.1)."""
    if darwin:
        return "launchctl kickstart -k gui/$(id -u)/%s" % LABEL
    return "systemctl --user restart %s" % UNIT


def assess(verdict: dict, is_hosted: bool, port: int, darwin: bool,
           installer: str) -> dict:
    """The `board server` row as plain data: ``{status, detail, fix, failure_id}``.

    ok + hosted → ok; ok but stray (shell-spawned / hand-run, dies with its
    parent) → warn; down + hosted → fail (crash loop or port fight); down + not
    hosted → warn (the shell will spawn the TCC-fragile fallback).
    """
    host = "launchd" if darwin else "systemd"
    unit = LABEL if darwin else UNIT
    if verdict.get("state") == "ok":
        return _assess_reachable(is_hosted, port, host, installer)
    if is_hosted:
        text = verdict.get("text") or "no response"
        return {
            "status": "fail", "failure_id": FAILURE_ID,
            "detail": ("%s job %s is loaded but http://127.0.0.1:%d/api/health does not "
                       "answer (%s) - the web board and the shell app have nothing to "
                       "connect to; a crash loop, or another process holds the port"
                       % (host, unit, port, text)),
            "fix": ("%s; quit any hand-started `python3 -m server` / old shell app; then %s"
                    % (LOG_HINT, restart_cmd(darwin))),
        }
    return {
        "status": "warn", "failure_id": FAILURE_ID,
        "detail": ("nothing answers on http://127.0.0.1:%d/api/health and the server is "
                   "not %s-hosted - the shell app will spawn a fallback child (the TCC "
                   "shape that failed on 2026-09-02, §54)" % (port, host)),
        "fix": "bash %s  # renders + loads %s" % (installer, unit),
    }


def _assess_reachable(is_hosted: bool, port: int, host: str, installer: str) -> dict:
    if is_hosted:
        return {"status": "ok", "failure_id": "", "fix": "",
                "detail": "reachable on 127.0.0.1:%d (%s-hosted)" % (port, host)}
    return {
        "status": "warn", "failure_id": FAILURE_ID,
        "detail": ("reachable on 127.0.0.1:%d but not %s-hosted (a shell-spawned or "
                   "hand-started server: it dies with its parent)" % (port, host)),
        "fix": "bash %s  # installs the resident %s job" % (installer, host),
    }


def ui_build_row(step: Optional[dict], installer: str) -> Optional[dict]:
    """The `board ui build` row from the §23 install_report `ui` step, or None.

    ``skipped_tcc`` (node TCC-denied under launchd — the deploy finished, the
    web board was not rebuilt) → warn with the FDA fix; ``fail`` (a hand-run
    install.sh — an auto-deploy fail already rolled back) → warn toward the
    build log; anything else → no row.
    """
    if not step:
        return None
    status = str(step.get("status") or "")
    detail = str(step.get("detail") or status)
    if status == "skipped_tcc":
        return {
            "status": "warn", "failure_id": UI_TCC_FAILURE_ID,
            "detail": "the last install.sh could not rebuild the web board: %s" % detail,
            "fix": ("grant Full Disk Access to the node binary the daemon PATH resolves "
                    "(System Settings > Privacy & Security), or run bash %s from a terminal"
                    % installer),
        }
    if status == "fail":
        return {
            "status": "warn", "failure_id": "",
            "detail": "the last install.sh failed to build/install the board UI: %s" % detail,
            "fix": "%s; then bash %s" % (UI_LOG_HINT, installer),
        }
    return None
