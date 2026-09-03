"""``deploy_state.json`` readers (CONTRACT §56 合并即上岗).

Two files, one writer (scripts/auto-deploy.sh; one flat object of strings,
atomic tmp+rename, rewritten every run):

- ``state/deploy_state.json`` in the repo — the PROJECTION, written
  best-effort. Read here for the dashboard (add-only top-level key
  ``deploy_state``, §2 sibling field, same convention as ``update_available``
  / ``device_label``: absent when the file is absent or unreadable) and for
  the ``act.doctor`` ``auto-deploy`` row (OK for deployed/up_to_date, WARN for
  every other outcome, no row when the file does not exist). :func:`read`
  prefers the mirror whenever the mirror says it describes THIS checkout
  (``repo``): a projection the job could not rewrite (that is what
  ``blocked_tcc`` means) must not keep showing the last status it could.
- ``~/Library/Application Support/ZelinAIAssistant/deploy_state.json`` — the
  HOME MIRROR, the script's own truth (v0.48.20, §56.4). macOS gates
  background access to removable volumes per responsible executable (TCC)
  and never gates ``$HOME``, so a launchd-fired run that cannot touch the
  repo still records what happened here. It carries every projected field
  plus the run's identity (``trigger`` / ``interpreter`` / ``volume`` /
  ``repo``) and the ``unattended_*`` triple — the last verdict of a run NOT
  started from a terminal, which the doctor's ``launchd volume access`` row
  reads (a green ``--force`` from the owner's terminal inherits the
  terminal's grants and proves nothing about the job).

Field by field type-checking, never raising: the writer is a shell script and
a torn/half-edited file must not take the dashboard pass down (§0 第 11 条).
Unknown keys are dropped, unknown ``status`` values are kept verbatim
(add-only: readers tolerate what they do not know).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Optional

from act.lib import config, failures

PATH: Path = config.STATE_DIR / "deploy_state.json"
# Same directory as the §19 home pointer (home.txt): one app-support dir.
MIRROR_PATH: Path = (Path.home() / "Library" / "Application Support"
                     / "ZelinAIAssistant" / "deploy_state.json")

# Projected keys (all strings). v0.48.20 add-only: `running_version` (what
# state/actd.heartbeat says is in memory), `install_report_version` (what
# install.sh last finished on), `reason` (machine tokens behind a non-healthy
# status), `last_incident` ("<ts> <status>: <detail>" of the last rollback
# verdict — kept through every routine write until the next `deployed`, so a
# refused rollback stays visible after the following interval's up_to_date;
# #135 review). 2026-09-03 add-only: `behind_main` / `behind_main_why` — the
# last deploy stopped short of origin/main's head (it chose the newest GREEN
# ancestor because the head's CI was pending / red / already poisoned, §56.3
# step 3); present only while that is so, cleared by a deploy of the head or
# an up_to_date. The script also keeps private bookkeeping (`notified_sha`,
# `failed_shas` — every poisoned sha since the last clear, `failed_sha` being
# only the newest —, `incomplete_runs` / `incomplete_runs_sha` /
# `incomplete_seen` / `incomplete_sha` / `incomplete_notified_sha`,
# `tcc_notified_day`); not projected.
FIELDS = ("status", "version", "head", "prev", "last_deployed", "last_run",
          "detail", "failed_sha", "running_version", "install_report_version",
          "reason", "last_incident", "behind_main", "behind_main_why")

# Mirror-only keys (never in the dashboard: local paths and the unattended
# triple are diagnostics for the doctor, not board content).
MIRROR_FIELDS = FIELDS + ("trigger", "interpreter", "volume", "repo", "denied_path",
                          "unattended_status", "unattended_last_run",
                          "unattended_detail")

# Outcomes that mean "nothing needs a human" — everything else is a WARN row.
HEALTHY = frozenset({"deployed", "up_to_date"})

# §56.4 open vocabulary, v0.48.20 additions: `install_incomplete` (HEAD is at
# origin/main but install_report / heartbeat disagree — install.sh re-run),
# `blocked_tcc` (the volume-access probe got EPERM before any git call).
INSTALL_INCOMPLETE = "install_incomplete"
BLOCKED_TCC = "blocked_tcc"


def _load_object(target: Path) -> Optional[dict]:
    """The file as a dict, or None when absent / unreadable / not an object."""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _read_fields(target: Path, fields) -> Optional[dict]:
    raw = _load_object(target)
    if raw is None:
        return None
    out = {}
    for key in fields:
        value = raw.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out or None


def _mirror_describes(repo: str, mirror_path: Path) -> bool:
    """Whether the HOME mirror's ``repo`` is this checkout (physical paths).
    A mirror without ``repo`` is not trusted here: some other clone's run, or
    a pre-mirror file — the projection stays authoritative for it."""
    raw = _load_object(mirror_path)
    if not raw:
        return False
    other = raw.get("repo")
    return bool(other) and isinstance(other, str) and same_repo(other, repo)


def read(path: Optional[Path] = None) -> Optional[dict]:
    """The sanitized deploy state, or None when absent/unreadable/not a dict.

    Without an explicit ``path``: the HOME mirror when it describes this
    checkout (its ``last_run`` is always >= the projection's — the script
    writes it first and the projection best-effort), else the projection."""
    if path is not None:
        return _read_fields(path, FIELDS)
    if _mirror_describes(str(config.HOME), MIRROR_PATH):
        return _read_fields(MIRROR_PATH, FIELDS)
    return _read_fields(PATH, FIELDS)


def read_mirror(path: Optional[Path] = None) -> Optional[dict]:
    """The sanitized HOME mirror (superset of :func:`read`), or None."""
    return _read_fields(path or MIRROR_PATH, MIRROR_FIELDS)


def attach(dash: dict, path: Optional[Path] = None) -> dict:
    """Set ``dash["deploy_state"]`` when a readable state exists (add-only)."""
    state = read(path)
    if state:
        dash["deploy_state"] = state
    return dash


# --------------------------------------------------------------------------- #
# §56.3 第 1 步 — the unattended verdict the doctor's `launchd volume access`
# row renders (pure functions: no file IO, no CheckResult; act/doctor.py wraps)
# --------------------------------------------------------------------------- #
# What EPERM looks like in launchd's stderr (2026-09-02 verbatim): python's
# PermissionError [Errno 1], rm/bash "Operation not permitted", and the python
# launcher failing to import act at all (`python3 -m act.auto_deploy` dies
# before the script runs — the mirror never gets written, this log is the only
# witness).
# Two regexes, searched in order: an EPERM spelling anywhere in the tail is
# unambiguous and wins; the import failure alone is the fallback (ambiguous).
EPERM_RE = re.compile(r"PermissionError: \[Errno 1\]|Operation not permitted|\bEPERM\b")
IMPORT_RE = re.compile(r"No module named 'act'")
# launchd's stderr file has no timestamps: "written in the last 24h" can only
# mean the file's mtime.
LAUNCHD_LOG_EVIDENCE_S = 24 * 3600

# verdict kinds (add-only)
BLOCKED_MIRROR = "blocked_mirror"   # the last unattended run recorded blocked_tcc
BLOCKED_LOG = "blocked_log"         # launchd stderr shows EPERM / import failure < 24h
OK_RECORDED = "ok_recorded"         # an unattended run reached the repo
OK_NONE = "ok_none"                 # nothing recorded yet
# `No module named 'act'` on its own is ambiguous (§55): a TCC-blind
# interpreter OR a plist whose PYTHONPATH is wrong/symlink-shaped look
# identical in the log; only the EPERM spellings are unambiguous.
IMPORT_ONLY = "No module named 'act'"


def parse_iso_utc(text) -> Optional[float]:
    """`2026-09-02T00:48:54Z` → epoch seconds; None when not that shape."""
    try:
        return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def same_repo(path, repo: str) -> bool:
    """Empty path = unknown = assume ours; otherwise compare physical paths."""
    return not path or os.path.realpath(str(path)) == os.path.realpath(repo)


def mirror_for_repo(mirror, repo: str) -> dict:
    """The mirror when it describes THIS checkout, else {} (another clone's
    verdict is not evidence about ours)."""
    mirror = dict(mirror or {})
    if same_repo(mirror.get("repo", ""), repo):
        return mirror
    return {}


def _superseded(last_unattended: str, log_mtime: float) -> bool:
    """A later unattended run that reached the repo retires older log lines."""
    later = parse_iso_utc(last_unattended)
    return later is not None and later > log_mtime


def _denial_match(log_tail: str):
    """The unambiguous EPERM spelling if any, else the import-only line, else None."""
    return EPERM_RE.search(log_tail) or IMPORT_RE.search(log_tail)


def log_evidence(log_tail: str, log_mtime, now: float, last_unattended: str):
    """(matched text, age seconds) when launchd's stderr still counts as
    evidence of a blocked job; None otherwise."""
    if not log_tail or log_mtime is None:
        return None
    age = now - log_mtime
    if not 0 <= age < LAUNCHD_LOG_EVIDENCE_S:
        return None
    hit = _denial_match(log_tail)
    if hit is None or _superseded(last_unattended, log_mtime):
        return None
    return hit.group(0), age


def _nz(value: str, default: str) -> str:
    return value if value else default


def unattended_verdict(mirror, repo: str, log_tail: str, log_mtime, now: float) -> dict:
    """Combine the HOME mirror's unattended triple with the launchd stderr
    evidence into one dict: ``{"kind": <BLOCKED_MIRROR|BLOCKED_LOG|OK_RECORDED|
    OK_NONE>, ...}``. Never raises on odd inputs."""
    m = mirror_for_repo(mirror, repo)
    last = str(m.get("unattended_last_run", ""))
    if m.get("unattended_status") == BLOCKED_TCC:
        return {"kind": BLOCKED_MIRROR, "last_run": _nz(last, "?"),
                "volume": _nz(str(m.get("volume", "")), repo),
                "denied_path": _nz(str(m.get("denied_path", "")), repo),
                "detail": _nz(str(m.get("unattended_detail", "")), BLOCKED_TCC)}
    evidence = log_evidence(log_tail, log_mtime, now, last)
    if evidence is not None:
        return {"kind": BLOCKED_LOG, "match": evidence[0], "age_s": evidence[1],
                "ambiguous": evidence[0] == IMPORT_ONLY}
    if last:
        return {"kind": OK_RECORDED, "last_run": last,
                "status": _nz(str(m.get("unattended_status", "")), "?")}
    return {"kind": OK_NONE}


def volume_access_fix(interp: str) -> str:
    """The doctor's remediation for a TCC-blind deploy job — both grants named
    (the plist's ProgramArguments[0] and the stable daemon copy of claude, §55
    第五幕: a fixed path, so unlike ~/.local/share/claude/versions/<v> the
    grant survives claude updates), and the 2026-09-02 observation spelled
    out: runs started from a terminal (even a `launchctl kickstart` typed
    there) inherit the terminal's grant, so a green one proves nothing about
    timer-fired runs; wait for the timer."""
    stable_claude = str(config.stable_claude_bin())
    return failures.pick(
        "系统设置 → 隐私与安全性 → 完全磁盘访问 → +（Command-Shift-G 粘贴路径），加两条："
        "① 后台任务的解释器 %s（plist 的 ProgramArguments[0]）；② %s（install.sh 维护的 "
        "claude 稳定副本——路径固定，授一次即跨 claude 更新有效；还没有这个文件就先跑一次 "
        "bash install.sh）。然后**等 timer 自己触发一轮（≤ 10 min）**再重跑 doctor 看本行。"
        "从终端起的运行——bash scripts/auto-deploy.sh、python3 -m act.auto_deploy、乃至在终端里"
        "敲的 launchctl kickstart——都继承终端的授权，绿了对 timer 触发的运行什么都不证明"
        % (interp, stable_claude),
        "System Settings > Privacy & Security > Full Disk Access > + (Command-Shift-G, paste "
        "the path), two entries: (1) the job's interpreter %s (the plist's "
        "ProgramArguments[0]); (2) %s (the stable daemon copy of claude that install.sh "
        "maintains - a fixed path, so the grant survives claude updates; run bash install.sh "
        "once if the file is not there yet). Then WAIT for the timer to fire one run "
        "(<= 10 min) and re-run doctor. Runs started from a terminal - bash "
        "scripts/auto-deploy.sh, python3 -m act.auto_deploy, even a launchctl kickstart typed "
        "in that terminal - inherit the terminal's grant: a green one proves nothing about "
        "timer-fired runs" % (interp, stable_claude))


# --------------------------------------------------------------------------- #
# Doctor rows for §56 (same dict shape as act/lib/board_server.py rows —
# ``{"status": "ok"|"warn"|"fail", "detail", "fix", "failure_id"}`` — which
# act/doctor.py `_row_from` turns into a CheckResult; kept here so doctor.py
# stays under the 防腐 #1 file cap)
# --------------------------------------------------------------------------- #
DEPLOY_BLIND_FAILURE_ID = "deploy_blind_tcc"


def _row(status: str, detail: str, fix: str = "", failure_id: str = "") -> dict:
    return {"status": status, "detail": detail, "fix": fix, "failure_id": failure_id}


def volume_access_row(verdict: dict, interp: str, repo: str) -> dict:
    """``launchd volume access`` row from :func:`unattended_verdict`."""
    kind = verdict["kind"]
    if kind == BLOCKED_MIRROR:
        return _row("fail", failures.pick(
            "上一次无人值守的部署运行（%s）读不了 %s（卡在 %s）：%s——launchd 任务没有"
            "这个卷的访问权，每 10 分钟都在原地打转，什么都没部署",
            "the last unattended deploy run (%s) could not access %s (denied at %s): %s - "
            "the launchd job has no grant for that volume; it idles every 10 min and "
            "deploys nothing")
            % (verdict["last_run"], verdict["volume"], verdict["denied_path"], verdict["detail"]),
            volume_access_fix(interp), DEPLOY_BLIND_FAILURE_ID)
    if kind == BLOCKED_LOG:
        # `No module named 'act'` alone cannot tell TCC from a mis-rendered
        # PYTHONPATH (§55): say so and send the reader to `launchd paths` first.
        fix = volume_access_fix(interp)
        if verdict.get("ambiguous"):
            fix = failures.pick(
                "先看 doctor 的 launchd paths 行：不是 OK → bash install.sh 重渲 plist；"
                "是 OK → 解释器读不到 repo：",
                "check doctor's launchd paths row first: not OK -> bash install.sh re-renders "
                "the plist; OK -> the interpreter cannot read the repo: ") + fix
        return _row("fail", failures.pick(
            "autodeploy.launchd.log（%d 分钟前写入）尾部有「%s」——launchd 起的"
            "启动器/解释器 %s 读不到 %s 上的 repo（这份 stderr 没时间戳，只能按"
            "文件 mtime 判「最近 24h」）",
            "autodeploy.launchd.log (written %d min ago) ends with \"%s\" - the "
            "launchd-spawned launcher/interpreter %s cannot reach the repo on %s "
            "(this stderr file has no timestamps; \"last 24h\" = file mtime)")
            % (int(verdict["age_s"] // 60), verdict["match"], interp, repo), fix, DEPLOY_BLIND_FAILURE_ID)
    if kind == OK_RECORDED:
        return _row("ok", "last unattended run %s reached %s (status %s)"
                    % (verdict["last_run"], repo, verdict["status"]))
    return _row("ok", "no unattended run recorded yet (mirror %s) - expect one within 10 min of install"
                % MIRROR_PATH)


def _auto_deploy_fix(status: str) -> str:
    """WARN 行的修法按状态词分三种（§56.4）。"""
    if status == BLOCKED_TCC:
        # 探针在第一次 git 调用前就拒了——修法是授权，不是 --force；精确的
        # 解释器路径与证据在 `launchd volume access` 行
        return failures.pick(
            "给 plist 里那个解释器授「完全磁盘访问」——见 doctor 的 launchd volume "
            "access 行与 docs/TROUBLESHOOTING.md「外置盘 + launchd 权限」",
            "grant Full Disk Access to the plist's interpreter - see the launchd volume "
            "access row and docs/TROUBLESHOOTING.md (external volume + launchd)")
    if status == INSTALL_INCOMPLETE:
        return failures.pick(
            "下一轮会自动重跑 install.sh（连续几轮无效即停并通知）；等不及就手动"
            " bash install.sh 或 bash scripts/auto-deploy.sh --force",
            "the next run re-runs install.sh by itself (gives up + notifies after a few "
            "consecutive runs); or by hand: bash install.sh / bash scripts/auto-deploy.sh --force")
    return failures.pick(
        "tail -40 ~/Library/Logs/zelin-ai-assistant/auto-deploy.log；修好后"
        " bash scripts/auto-deploy.sh --force（重试被记为失败的那个 origin/main）",
        "tail -40 ~/Library/Logs/zelin-ai-assistant/auto-deploy.log; once fixed:"
        " bash scripts/auto-deploy.sh --force (retries the origin/main sha marked failed)")


def _auto_deploy_warn_detail(state: dict) -> str:
    """「last run ended '<status>' on v<version> (running v<running>): <detail>」——
    `running_version`（心跳里的版本）与 checkout 版本不同时才点名。"""
    version = state.get("version", "")
    running = state.get("running_version", "")
    detail = state.get("detail", "")
    parts = ["last run ended '%s'" % (state.get("status", "") or "?")]
    if version:
        parts.append(" on v%s" % version)
    if running and running != version:
        parts.append(" (running v%s)" % running)
    if detail:
        parts.append(": " + detail)
    return "".join(parts)


def _behind_main_note(state: dict) -> str:
    """「; origin/main <sha> not deployed: <why>」while the last deploy deliberately
    stopped short of the head (§56.3 step 3: the newest green ancestor went live
    instead; still OK — the machine runs the newest tested code — but the reader
    should see main is ahead). "" otherwise."""
    behind = state.get("behind_main", "")
    if not behind:
        return ""
    return "; origin/main %s not deployed: %s" % (behind[:7], state.get("behind_main_why") or "?")


def _auto_deploy_ok_detail(state: dict) -> str:
    when = state.get("last_deployed") or state.get("last_run") or ""
    return "%s (v%s%s)%s" % (state.get("status", ""), state.get("version") or "?",
                             (" at " + when) if when else "", _behind_main_note(state))


def auto_deploy_row(state: dict) -> dict:
    """``auto-deploy`` row for a sanitized :func:`read` result. Healthy
    statuses are OK — unless a `last_incident`
    (a rollback verdict no later `deployed` has cleared) is still on file: the
    machine may well be up to date NOW, but the refusal that put it there has
    not been looked at, and the routine `up_to_date` write must not hide it
    (#135 review)."""
    status = state.get("status", "")
    if status not in HEALTHY:
        return _row("warn", _auto_deploy_warn_detail(state), _auto_deploy_fix(status))
    incident = state.get("last_incident", "")
    if not incident:
        return _row("ok", _auto_deploy_ok_detail(state))
    return _row("warn", "%s; unresolved deploy incident: %s" % (_auto_deploy_ok_detail(state), incident),
                failures.pick(
                "上一次回滚判决还没人看过（新 sha 留在原地）：核对它点名的问题；下一次成功部署"
                "（合并新提交，或 bash scripts/auto-deploy.sh --force 部署新 sha）会清掉本行",
                "the last rollback verdict has not been looked at (the new sha stayed in place): "
                "check what it names; the next successful deploy (a new commit on main, or bash "
                "scripts/auto-deploy.sh --force onto a new sha) clears this"))
