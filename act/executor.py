"""Executor — dispatch an approved requirement to a background claude agent.

Flow (CONTRACT §4):
  1. Assemble prompt = title + plan + sources
       + memory injection (head of MEMORY.md as system context)
       + quality-gate instructions (self-check runnable / fresh-context diff review
         / deliver draft PR, do NOT merge, do NOT send external messages;
         delivery_mode=="chat" (v0.10) swaps the branch/PR clause for a
         paste-ready `FINAL DRAFT:` block in the closing summary, no repo files)
       + if type==training: force a system card per checkpoint.
  2. cd <target_repo> (default ~/Projects/your-workbench, overridable by req/LLM routing)
     and run `claude --bg "<prompt>"` (with --dangerously-skip-permissions while
     execution.skip_permissions is on — the default).
  3. Capture session_id (from output, else newest `claude agents --json` match on
     cwd started after the dispatch); write back req.execution + status=executing
     + save. A failed launch / uncaptured session id keeps the requirement
     APPROVED with execution.last_error set and raises DispatchError (P0-6).

Run standalone: ``python -m act.executor <req_id>``.

Law pointers: §4 dispatch / storm brake / auto-resume, §7 target_kind + repo
bootstrap, §10 契约 C delivery harvesting, §11 rework, §15 output format,
§33 chat delivery, §37.1 CARD TITLE tiers, §39.2 safe window, §44.3 briefings,
§46 stop confirmation, §59 single LLM boundary (argv via act/llm.py), §60
display ids. Transcript reading lives in act/lib/transcripts.py (lib layer);
the ``_transcript_info`` / ``transcript_plain_text`` names here are aliases
kept as the test seams they always were.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from act import llm
from act.lib import (analytics, config, dispatch_prompt, failures, notify, registry, sanitize,
                     self_improve, transcripts)
from act.lib.registry import Requirement, State, display_id, load, save

# prompt text (dispatch / rework / brief) lives in act/lib/dispatch_prompt.py;
# these names stay importable from here — tests and CONTRACT prose use them.
MEMORY_HEAD_LINES = dispatch_prompt.MEMORY_HEAD_LINES
BRIEFING_PREFIX = dispatch_prompt.BRIEFING_PREFIX
resolve_voice_profile = dispatch_prompt.resolve_voice_profile
_card_title_tier = dispatch_prompt.card_title_tier
_current_display_name = dispatch_prompt.current_display_name
_delivery_mode = dispatch_prompt.delivery_mode
_resolve_target = dispatch_prompt.resolve_target

# accept several shapes claude might print the session id in.
# real `claude --bg` prints:  "backgrounded · e88561e5"  (verified 2026-07-06),
# so "backgrounded" + the middot separator must be matched first; also keep the
# session-id / --resume forms and allow 6+ hex (short ids like e88561e5 are 8).
# id 只匹配两种真实形态：完整 UUID 或连续短 hex——旧的 [0-9a-fA-F-]{5,} 会把
# "backgrounded: 2026-07-08" 的日期吞成假 sid（写进 execution 后 resume/
# transcript 永远对不上），也会把紧跟 id 的连字符文本吸进来（e88561e5-abc-de）。
_SESSION_RE = re.compile(
    r"(?:backgrounded|session[_ -]?id|--resume)[\"'\s:=·]+"
    r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{6,})",
    re.IGNORECASE,
)

# CSI escape sequences (color codes etc.) — claude under FORCE_COLOR/
# CLICOLOR_FORCE may wrap the keyword and the id separately, which breaks the
# separator character class; strip before matching (_parse_session_id).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# --------------------------------------------------------------------------- #
# repo bootstrap (CONTRACT v0.1 §7 target_kind + draft-PR delivery)
# --------------------------------------------------------------------------- #
def _git(target: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(target),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def compute_target_kind(target: Path) -> str:
    """"existing" if the dir exists and is non-empty, else "new"."""
    try:
        if target.exists() and target.is_dir() and any(target.iterdir()):
            return "existing"
    except OSError:
        pass
    return "new"


def _has_git_repo(target: Path) -> bool:
    if not target.exists():
        return False
    try:
        proc = _git(target, "rev-parse", "--is-inside-work-tree")
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _has_commits(target: Path) -> bool:
    try:
        return _git(target, "rev-parse", "--verify", "HEAD").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def has_remote(target: Path) -> bool:
    """True if the repo has an ``origin`` (or any) remote configured."""
    if not _has_git_repo(target):
        return False
    try:
        proc = _git(target, "remote")
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _git_init_if_needed(target: Path) -> bool:
    """``git init`` a directory that is not a work tree yet. False only when
    the init itself could not be spawned — the one bootstrap step whose
    failure makes the rest pointless."""
    if _has_git_repo(target):
        return True
    try:
        _git(target, "init")
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _commit_if_empty(target: Path) -> None:
    """An empty initial commit so the agent can branch; failure is tolerated."""
    if _has_commits(target):
        return
    try:
        _git(target, "commit", "--allow-empty", "-m", "chore: initialize repository")
    except (OSError, subprocess.SubprocessError):
        pass


def _wants_github_remote(target: Path, cfg: config.Config) -> bool:
    """Configured + ``gh`` on PATH + no remote yet."""
    return bool(cfg.create_github_repo and shutil.which("gh") and not has_remote(target))


def _create_github_remote(target: Path) -> None:
    try:
        subprocess.run(
            ["gh", "repo", "create", target.name,
             "--private", "--source", str(target), "--remote", "origin"],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # stay local


def ensure_repo(target: Path, cfg: config.Config) -> None:
    """Best-effort: guarantee ``target`` is a git repo with at least one commit,
    and (if configured + ``gh`` present + no remote) a private GitHub origin.

    Everything here tolerates failure and stays local — a missing ``gh`` or a
    network error must never block dispatch.
    """
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    if not _git_init_if_needed(target):
        return
    _commit_if_empty(target)
    if _wants_github_remote(target, cfg):
        _create_github_remote(target)


def build_prompt(req: Requirement, cfg: Optional[config.Config] = None,
                 target: Optional[Path] = None) -> str:
    """``target`` = dispatch 已解析的实际 cwd（含 chat 模式目录不存在时的回退）；
    不传则按 req.target_repo 独立推导 —— 传入可保证 prompt 与实际 cwd 一致。
    Text lives in act/lib/dispatch_prompt (render); this is the seam that
    knows whether the target has a remote."""
    if cfg is None:
        cfg = config.load_config()
    if target is None:
        target = _resolve_target(req, cfg)
    return dispatch_prompt.render(req, cfg, target, has_remote(target))


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
class DispatchError(RuntimeError):
    """A ``claude --bg`` launch failed (non-zero exit / subprocess error / no
    session id captured), or the retry backoff window is still open.

    dispatch() records ``execution.last_error``/``last_error_at`` (the same
    shape rework() writes) BEFORE raising. actd.dispatch_approved's except
    path keeps the requirement APPROVED for the next-pass retry and re-records
    the same error, so the dashboard's queued card keeps showing
    ``dispatch_error``. Its success-path clearing is gated on a session_id
    being present, so a dispatch that signalled failure by RETURNING (no
    session, error recorded) would keep its trace too — raising is the current
    convention, not a load-bearing requirement.
    """


class DispatchBackingOff(DispatchError):
    """The retry backoff window is still open — nothing was launched and
    NOTHING on the card changed. actd must treat this as a no-op (no write,
    no traceback): re-recording the stored error every pass is what turned
    one failing card into 98% of all registry writes (2026-08-31 storm)."""


class DispatchHalted(DispatchError):
    """The dispatch-storm brake tripped (or had already tripped): the card
    stays APPROVED but is no longer retried — ``execution.dispatch_halted``
    parks it in the blocked lane until the owner re-approves it (§4)."""


# execution keys that belong to ONE dispatch streak. A fresh approval wipes
# them (approve = the re-arm verb after a halt); a successful launch rebuilds
# execution wholesale, so they never survive into a live run either.
DISPATCH_STREAK_KEYS = (
    "dispatch_attempts", "last_dispatch_attempt_at",
    "dispatch_error_class", "dispatch_class_streak",
    "dispatch_halted", "dispatch_halted_at",
)


def dispatch_error_class(err: Optional[str]) -> str:
    """The failure class a launch error counts under for the storm brake:
    the §25 catalog id, or ``"unclassified"`` for everything else. Unknown
    errors pool into ONE class on purpose — a message whose text drifts
    (pids, timestamps) must still trip the brake, and a genuinely flapping
    cause is still a storm."""
    return failures.classify(err) or "unclassified"


def session_name(req: Requirement) -> str:
    """Readable display name for the bg session — shows up in `claude agents`
    so Zelin can correlate list entries with assistant cards at a glance.

    卡片 title 是 LLM/用户产物，可能含换行、路径分隔符、控制字符——而 agent
    name 会被 claude 用作 worktree 目录/分支名的一部分
    (<target>/.claude/worktrees/<name>)，合法性必须在本侧保证，不押注下游
    CLI 的内部清洗：路径分隔符和控制字符统一折叠成单个空格。argv 数组传参
    本身无 shell 注入面，这里只管名字的文件系统/git 合法性。"""
    title = (req.title or "").strip()
    title = re.sub(r"[\\/\x00-\x1f\x7f]+", " ", title)   # newlines, / \, ctrl chars
    title = re.sub(r"\s+", " ", title).strip()
    rid = display_id(req)          # §60：工作编号（legacy 卡回落主键）
    return f"{rid} · {title[:48]}" if title else rid


def _claude_bin(cfg: Optional[config.Config] = None) -> str:
    """Resolved claude CLI for the non-prompt subprocess sites (roster / stop).

    A bare "claude" argv trusts the daemon's PATH — under launchd that once
    resolved a second, outdated install and every dispatch died on
    "unknown option '--bg'" (2026-07-08). config.resolve_claude_bin prefers
    the execution.claude_bin pin, then the stable daemon copy (§55 第五幕),
    then PATH, then ~/.local/bin/claude."""
    return llm.claude_bin(cfg)


def _bg_base_cmd(cfg: Optional[config.Config] = None,
                 req: Optional[Requirement] = None) -> list:
    """Base ``claude --bg`` argv shared by all launch sites (dispatch / resume /
    rework / brief) — built by the §59 single LLM boundary (act/llm.py):
    ``--dangerously-skip-permissions`` only while ``execution.skip_permissions``
    is on (default; P0-10 — off means the agent runs under claude's normal
    permission model; a blocked agent is harvested to review by actd's
    reconcile (#119) instead of acting unattended), then ``--model <id>``
    when the dispatch knob is explicit (nothing when it follows). ``req``
    (§65, add-only): a self_improve card without ``needs_mcp`` gets
    ``llm.NO_MCP_ARGV`` appended — the session sees no Slack/Gmail MCP; every
    launch site passes its card so a resume/rework/brief can never re-open the
    MCP surface the dispatch closed. ``req=None`` = byte-identical to before."""
    return llm.dispatch_argv(cfg, no_mcp=self_improve.egress_locked(req))


def _default_runner(prompt: str, cwd: Path, name: Optional[str] = None,
                    cfg: Optional[config.Config] = None,
                    req: Optional[Requirement] = None) -> subprocess.CompletedProcess:
    prompt, _ = sanitize.scrub(prompt)
    cmd = _bg_base_cmd(cfg, req)
    if name:
        cmd += ["--name", name]
    cmd.append(prompt)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        env=llm.runner_env(),
    )


_EPOCH_MIN = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
_ROSTER_ENVELOPE_KEYS = ("agents", "sessions", "items", "data")


def _when_from_epoch(ts: float) -> Optional[_dt.datetime]:
    """Epoch seconds (or millis when > 1e12) -> aware UTC; junk -> None."""
    if ts <= 0:
        return None
    if ts > 1e12:  # epoch millis
        ts /= 1000.0
    try:
        return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _when_from_text(s: str) -> Optional[_dt.datetime]:
    """ISO-8601 (Z or offset; naive = UTC), else a numeric epoch string."""
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return _when_from_epoch(float(s))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _parse_when(value) -> Optional[_dt.datetime]:
    """Best-effort timestamp -> aware UTC datetime (roster ``started_at`` may be
    ISO-8601, epoch seconds, or epoch millis; registry stamps are ISO Z)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _when_from_epoch(float(value))
    s = str(value).strip()
    return _when_from_text(s) if s else None


# --------------------------------------------------------------------------- #
# roster (`claude agents --json --all`) — two readers with different strictness
# --------------------------------------------------------------------------- #
def _roster_query() -> Optional[subprocess.CompletedProcess]:
    """Run the roster CLI; None when it cannot be spawned / times out."""
    try:
        return subprocess.run(
            [_claude_bin(), "agents", "--json", "--all"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_roster(stdout: str):
    """Parsed roster JSON; ``[]`` for empty output, None for unparseable."""
    if not stdout.strip():
        return []
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _unwrap_roster(data) -> list:
    """A bare list as-is; ``{"agents": [...]}``-style envelopes unwrapped;
    anything else is an empty roster."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in _ROSTER_ENVELOPE_KEYS:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _agent_cwd(a) -> str:
    """The agent's working directory under any of claude's field names; ""
    for a non-dict entry or a missing cwd."""
    if not isinstance(a, dict):
        return ""
    return str(a.get("cwd") or a.get("working_directory") or a.get("workingDirectory") or "")


def _in_target(acwd: str, tgt: str) -> bool:
    """Exact match, or the agent's own git worktree under the target
    (claude --bg isolates into <target>/.claude/worktrees/<name>)."""
    return bool(acwd) and (acwd.rstrip("/") == tgt or acwd.startswith(tgt + "/"))


def _agent_sid(a: dict):
    return a.get("session_id") or a.get("sessionId") or a.get("id")


def _agent_started(a: dict):
    return a.get("started_at") or a.get("startedAt") or a.get("created_at") or 0


def _passes_after(started_dt: Optional[_dt.datetime],
                  after: Optional[_dt.datetime]) -> bool:
    """The P0-6 gate: with ``after`` set, only sessions with a parseable start
    no earlier than 2s before it qualify (roster timestamps are second-
    truncated); without it, everything passes."""
    if after is None:
        return True
    return started_dt is not None and started_dt >= after - _dt.timedelta(seconds=2)


def _cwd_candidate(a, tgt: str, after: Optional[_dt.datetime]) -> Optional[tuple]:
    """``(started_dt, sid)`` when the roster entry is a session under ``tgt``
    that passes the after-gate, else None. Unknown ages (only possible
    without the gate) sort oldest — a dated session always wins over them."""
    if not _in_target(_agent_cwd(a), tgt):
        return None
    sid = _agent_sid(a)
    if not sid:
        return None
    started_dt = _parse_when(_agent_started(a))
    if not _passes_after(started_dt, after):
        return None
    # 排序键必须是归一化后的 datetime：started_at 可能混用 ISO/epoch秒/
    # epoch毫秒（_parse_when 三态容忍），str 字典序会把 "17…"(epoch) 排在
    # "2026-…"(ISO) 前面，选错"最新"会话 → 绑到别人的 session（P0-6）。
    return (started_dt or _EPOCH_MIN, sid)


def _newest_session_for_cwd(cwd: str,
                            after: Optional[_dt.datetime] = None) -> Optional[str]:
    """Fallback: query `claude agents --json` and return the newest match on cwd.

    ``after`` (the pre-launch dispatch timestamp) gates the claim: sessions
    started before it — or with no parseable start time at all — are never
    adopted, so a stale unrelated session in the same cwd cannot be claimed as
    the one we just launched (P0-6). 2s slack tolerates second-truncated roster
    timestamps. Ties keep roster order (stable sort, last entry wins).
    """
    proc = _roster_query()
    data = _parse_roster(proc.stdout) if proc is not None else None
    if data is None:
        return None
    tgt = str(cwd).rstrip("/")
    candidates = [c for c in (_cwd_candidate(a, tgt, after) for a in _unwrap_roster(data))
                  if c is not None]
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return str(candidates[-1][1])


def _parse_session_id(output: str) -> Optional[str]:
    if not output:
        return None
    # keyword 和 id 之间夹 ANSI 色码（FORCE_COLOR 下的 claude 输出，llm.runner_env
    # 原样透传 os.environ）会让分隔符字符类匹配不上——先剥转义序列再匹配，
    # 否则一次成功的 launch 会被误判成 no_session_id 并在下轮重试出重复 agent。
    m = _SESSION_RE.search(_ANSI_RE.sub("", output))
    if m:
        return m.group(1)
    return None


# Provenance allowlist for the dispatch instruction content field
# (docs/TELEMETRY.md scope red line): ONLY cards whose every source is the
# user's own typed capture qualify. Radar cards (gmail / slack / meeting /
# claude_code / …) carry LLM summaries of OTHER PEOPLE's private comms in
# title/plan — those must never enter telemetry, so anything not on this
# allowlist (including unknown future channels) is excluded, fail-closed.
_USER_ORIGIN_CHANNELS = ("quick", "quick_capture")


def _instruction_summary(req: Requirement) -> Optional[str]:
    """Content field, gated on analytics.content_gate (docs/TELEMETRY.md
    「输入文本收集」) AND card provenance: the approved TITLE only (the plan
    is model-drafted and stays out), and only when every source channel is
    the user's own capture (_USER_ORIGIN_CHANNELS). Cards with no sources or
    any third-party-derived source return None — the dispatch event then
    carries metadata only."""
    sources = req.sources or []
    if not sources:
        return None
    if not all(_source_channel(s) in _USER_ORIGIN_CHANNELS for s in sources):
        return None
    return analytics.clip_content(req.title)


def _source_channel(s) -> str:
    """A source entry's channel name; "" for a non-dict entry."""
    return str(s.get("channel") or "") if isinstance(s, dict) else ""


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _execution(req: Requirement) -> dict:
    """A mutable copy of ``req.execution`` (None-safe)."""
    return dict(req.execution or {})


def _stored_error(ex: dict, default: str) -> str:
    """The card's recorded error text, else ``default``."""
    return str(ex.get("last_error") or default)


def _backing_off(attempts: int, last_try_raw) -> bool:
    """Whether the exponential retry window (30s·2^attempts, capped 10 min —
    the reconcile_executing curve) is still open since the last attempt."""
    if not attempts:
        return False
    last_try = _parse_when(last_try_raw)
    if last_try is None:
        return False
    backoff = min(600, 30 * (2 ** min(attempts, 5)))
    elapsed = (_utcnow() - last_try).total_seconds()
    return 0 <= elapsed < backoff


def _guard_retry(ex: dict) -> int:
    """Raise when the card must not launch now (halted / backing off — nothing
    on the card changes); otherwise return the attempt count so far."""
    if ex.get("dispatch_halted"):
        # §4 storm brake already tripped: no launch, no bookkeeping — the card
        # waits in the blocked lane for a fresh approval (actd skips halted
        # cards before calling us; this is the guard for direct callers).
        raise DispatchHalted(_stored_error(ex, "dispatch halted after repeated failures"))
    attempts = int(ex.get("dispatch_attempts") or 0)
    if _backing_off(attempts, ex.get("last_dispatch_attempt_at")):
        # still backing off — no launch, nothing changed on the card.
        # The STORED error text rides along verbatim so any caller
        # that does re-record sees a stable fixpoint (no prefix
        # stacking); actd treats the subclass as a pure no-op.
        raise DispatchBackingOff(_stored_error(ex, "dispatch launch failed; retry backing off"))
    return attempts


def _chat_target(target: Path, cfg: config.Config) -> Path:
    """chat 交付不落文件（v0.10）：跳过 ensure_repo — 不 git init、不建 GitHub
    repo。直接在 target_repo 现有目录跑；目录不存在则退回默认工作 repo，
    保证 claude 有一个可用的 cwd。"""
    if target.is_dir():
        return target
    target = cfg.target_repo_path
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return target


def _prepare_target(req: Requirement, cfg: config.Config, target: Path) -> Path:
    """Compute + persist target_kind if unset (dir exists & non-empty ->
    existing), then make sure claude has a cwd: chat delivery falls back to
    the workbench, repo delivery bootstraps a new / empty target."""
    if not req.target_kind:
        req.target_kind = compute_target_kind(target)
    if _delivery_mode(req) == "chat":
        return _chat_target(target, cfg)
    if req.target_kind == "new" or compute_target_kind(target) == "new":
        # Bootstrap a repo for new work (or an empty/missing target dir) so the
        # agent has somewhere to branch + open a draft PR. Best-effort; tolerates
        # failure.
        ensure_repo(target, cfg)
    return target


def _named_runner(req: Requirement, cfg: config.Config) -> Callable:
    """The default launcher: ``_default_runner`` bound to this card's session
    name (computed once, before the launch)."""
    return functools.partial(_default_runner, name=session_name(req), cfg=cfg, req=req)


def _launch(runner: Callable, prompt: str, target: Path) -> tuple[int, str, str]:
    """(returncode, stdout, stderr) of the runner; a spawn failure (claude
    missing from PATH under launchd, timeout, ...) is the same failure path as
    a non-zero exit instead of an opaque traceback in actd.log."""
    try:
        proc = runner(prompt, target)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    return (getattr(proc, "returncode", 1), getattr(proc, "stdout", "") or "",
            getattr(proc, "stderr", "") or "")


def _write_launch_log(log_path: Path, req: Requirement, target: Path,
                      stdout: str, stderr: str) -> None:
    try:
        log_path.write_text(
            f"# dispatch {display_id(req)} ({req.id}) @ {_dt.datetime.now().isoformat()}\n"
            f"# cwd={target}\n\n=== STDOUT ===\n{stdout}\n\n=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _captured_session(rc: int, stdout: str, stderr: str, target: Path,
                      dispatched_dt: _dt.datetime) -> Optional[str]:
    """The launched session id: parsed from either stream, else the roster
    fallback gated on the pre-launch stamp; None on a failed launch."""
    if rc != 0:
        return None
    session_id = _parse_session_id(stdout) or _parse_session_id(stderr)
    if not session_id:
        session_id = _newest_session_for_cwd(str(target), after=dispatched_dt)
    return session_id


def _failure_text(rc: int, stdout: str, stderr: str) -> tuple[str, str]:
    """(error text, analytics reason) for a launch that yielded no session."""
    if rc != 0:
        err = ((stdout or "") + (stderr or "")).strip() \
            or f"claude --bg exited {rc} (no output)"
        return err, "launch_failed"
    return "claude --bg launched but no session id was captured", "no_session_id"


def _bump_streak(ex: dict, klass: str) -> int:
    """§4 storm brake: count CONSECUTIVE failures of the same class; a
    different class restarts the streak (a real cause change deserves
    its own retries), a success rebuilds execution and wipes it all."""
    streak = (int(ex.get("dispatch_class_streak") or 0) + 1
              if ex.get("dispatch_error_class") == klass else 1)
    ex["dispatch_error_class"] = klass
    ex["dispatch_class_streak"] = streak
    return streak


def _brake_tripped(cfg: config.Config, streak: int) -> bool:
    """``cfg.dispatch_max_failures`` (default 5, 0 = off) straight failures of
    one class trip the storm brake."""
    limit = int(getattr(cfg, "dispatch_max_failures", 5) or 0)
    return limit > 0 and streak >= limit


def _card_label(req: Requirement) -> str:
    """What notifications call the card: its title, else its id."""
    return req.title or req.id


def _mark_halted(req: Requirement, ex: dict, streak: int, klass: str,
                 fid: Optional[str], err: str, now: str) -> None:
    ex["dispatch_halted"] = True
    ex["dispatch_halted_at"] = now
    # notes breadcrumb (§38.2 spirit: the card carries its own
    # history) — the hint is the catalog sentence when classified,
    # otherwise the raw error's first line.
    hint = failures.user_message(fid) or (err.splitlines() or [err])[0][:200]
    tag = (f"[dispatch-halted] 派发连续失败 {streak} 次（{klass}），"
           f"已停止自动重试：{hint} [@{now}]")
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag


def _record_launch_failure(req: Requirement, ex: dict, cfg: config.Config,
                           err: str, reason: str, attempts: int,
                           log_path: Path) -> None:
    """Persist the failure on the APPROVED card, emit events + notifications,
    then raise (DispatchHalted when the storm brake trips, else DispatchError)."""
    now = _utcnow_iso()
    ex["last_error"] = err[:500]
    ex["last_error_at"] = now
    ex["dispatch_attempts"] = attempts + 1
    ex["last_dispatch_attempt_at"] = now
    ex["log"] = str(log_path)
    klass = dispatch_error_class(err)
    streak = _bump_streak(ex, klass)
    halted = _brake_tripped(cfg, streak)
    fid = failures.classify(err)
    if halted:
        _mark_halted(req, ex, streak, klass, fid, err, now)
    req.execution = ex
    save(req)  # status untouched — stays APPROVED (retry, or parked if halted)
    # TELEMETRY 红线（issue #37）：只上传分类 id，绝不上传原始 stderr——
    # 路径/值都可能藏在里面；全文只进本机台账（execution.last_error）。
    analytics.log_event("dispatch_failed", req=req.id, failure_id=fid,
                        reason=reason, attempt=attempts + 1)
    if halted:
        analytics.log_event("dispatch_halted", req=req.id, failure_id=fid,
                            attempts=attempts + 1, streak=streak)
        notify.notify(*notify.msg_dispatch_halted(
            _card_label(req), streak, failures.user_message(fid)), req=req.id)
        raise DispatchHalted(err[:500])
    if attempts == 0:  # once per failure streak, not on every retry
        # classified reason in the notification body — "任务派发失败" with
        # zero clue left the 2026-07-08 outdated-claude loop undiagnosed
        notify.notify(*notify.msg_dispatch_failed(
            _card_label(req), failures.user_message(fid)), req=req.id)
    raise DispatchError(err[:500])


def _record_launch_success(req: Requirement, ex: dict, cfg: config.Config,
                           session_id: str, dispatched_dt: _dt.datetime,
                           log_path: Path) -> Requirement:
    """Rebuild execution wholesale around the live session, flip to EXECUTING
    and emit the dispatch event (+ the once-per-install milestone)."""
    # dispatch lifecycle timing (metadata): seconds the card waited between
    # approval (actd stamps execution.approved_at) and this launch.
    wait_s = None
    approved_dt = _parse_when(ex.get("approved_at"))
    if approved_dt is not None:
        wait_s = max(0, round((dispatched_dt - approved_dt).total_seconds()))
    req.execution = {
        "session_id": session_id,
        "dispatched_at": dispatched_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "log": str(log_path),
    }
    if ex.get("inbox_stem"):
        # §34.1 crash-replay 幂等键必须活过派发：inbox 文件 unlink 失败时同一
        # 文件跨 pass 重放，重放闸靠这个键认出"这单已经建过卡"——整体重建
        # execution 抹掉它 = 每 pass 铸一张新卡、起一个新 agent（无上界）。
        req.execution["inbox_stem"] = ex["inbox_stem"]
    # §65：self_improve 卡的派发记录（分支 / 出网档 / 是否走 lane）——非
    # self_improve 卡给 {}，execution 形状不变。
    req.execution.update(self_improve.dispatch_record(req, cfg))
    req.set_status(State.EXECUTING)
    save(req)
    # capture_input gating (docs/TELEMETRY.md): the instruction summary is
    # user-shaped content — recorded ONLY when capture_input AND detailed.
    analytics.log_event(
        "dispatch", req=req.id, target_kind=req.target_kind,
        session=session_id, type=req.type, wait_s=wait_s,
        instruction=(_instruction_summary(req)
                     if analytics.content_gate(cfg) else None))
    # lifecycle milestone (docs/TELEMETRY.md): first successful dispatch on this
    # install — the end of the activation funnel. Once-per-install, behavior
    # only (req id, no instruction content).
    analytics.log_first("milestone_first_delivery", req=req.id)
    return req


def dispatch(
    req: Requirement,
    cfg: Optional[config.Config] = None,
    runner: Optional[Callable[[str, Path], subprocess.CompletedProcess]] = None,
) -> Requirement:
    """Dispatch an approved requirement. Injectable ``runner`` for unit tests.

    A failed launch (claude exits non-zero, subprocess error, or no session id
    captured) must NOT enter EXECUTING (P0-6): reconcile skips executing items
    without a session_id, so the card would hang "执行中" forever with no agent
    behind it. Instead the requirement stays APPROVED (dispatch_approved
    retries it next pass), ``execution.last_error``/``last_error_at`` record
    the failure (rework() shape; the queued card shows it as dispatch_error),
    a ``dispatch_failed`` event + notification fire, and DispatchError is
    raised. Retries back off exponentially (30s·2^attempts, capped 10 min, the
    reconcile_executing curve) via ``dispatch_attempts``/
    ``last_dispatch_attempt_at``, which survive actd's last_error clearing;
    while the window is open the launch is skipped entirely
    (``DispatchBackingOff`` — nothing on the card changes).

    Storm brake (§4, v0.48.4): ``cfg.dispatch_max_failures`` (default 5, 0 =
    off) consecutive failures of the same :func:`dispatch_error_class` set
    ``execution.dispatch_halted`` — the card stays APPROVED but is never
    retried again (``DispatchHalted``), gets a ``[dispatch-halted]`` notes
    line + one notification, and the dashboard projects it into the blocked
    lane. A fresh approve (after 退回提案) clears :data:`DISPATCH_STREAK_KEYS`.
    """
    if cfg is None:
        cfg = config.load_config()
    if runner is None:
        runner = _named_runner(req, cfg)
    config.ensure_state_dirs()
    ex = _execution(req)
    attempts = _guard_retry(ex)
    # 把解析后的 target 传进去：chat 模式目录不存在时已回退到默认 repo，
    # prompt 里的 "Work from ..." 必须与实际 cwd 一致，否则 agent 会去
    # cd/mkdir 一个不存在的路径（与 chat 模式"不落文件"红线冲突）。
    target = _prepare_target(req, cfg, _resolve_target(req, cfg))
    prompt = build_prompt(req, cfg, target=target)
    # §60：日志按显示编号命名（R-<m>.log）；路径持久化在 execution.log，读方
    # 不依赖文件名口径
    log_path = config.LOG_DIR / f"{display_id(req)}.log"
    # pre-launch stamp: the roster fallback only claims sessions started
    # AFTER this moment, so it can never adopt an older unrelated session.
    dispatched_dt = _utcnow()
    rc, stdout, stderr = _launch(runner, prompt, target)
    _write_launch_log(log_path, req, target, stdout, stderr)
    session_id = _captured_session(rc, stdout, stderr, target, dispatched_dt)
    if not session_id:
        err, reason = _failure_text(rc, stdout, stderr)
        _record_launch_failure(req, ex, cfg, err, reason, attempts, log_path)
    return _record_launch_success(req, ex, cfg, session_id, dispatched_dt, log_path)


# --------------------------------------------------------------------------- #
# stop-idle-then-resume plumbing shared by resume / rework / brief
# --------------------------------------------------------------------------- #
def _session_target(ex: dict) -> Optional[tuple[str, Path]]:
    """(full_session_id, last cwd) for the card's session — the current sid,
    else the ROOT session (the conversation that exists on disk). None when
    there is no sid or no transcript anywhere: resuming is then impossible
    (a launch would crash-loop minting new ids), so callers give up WITHOUT
    launching."""
    sid = ex.get("session_id")
    if not sid:
        return None
    tinfo = _transcript_info(sid)
    if tinfo is None and ex.get("root_session_id"):
        tinfo = _transcript_info(str(ex["root_session_id"]))
    return tinfo


def _mkdir_ok(target: Path) -> bool:
    """Recreate the session cwd (the worktree may have been cleaned up while
    the task slept); False when that is impossible (stale path)."""
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def _run_resume(cfg: config.Config, req: Requirement, sid: str, target: Path,
                prompt: Optional[str] = None) -> subprocess.CompletedProcess:
    """``claude --bg --resume <full sid>`` in the transcript's cwd; a non-blank
    ``prompt`` rides as the first input (scrubbed — that is anti-leak, not
    anti-injection; owner text is trusted, see steer.build_steer_prompt)."""
    cmd = _bg_base_cmd(cfg, req) + ["--name", session_name(req), "--resume", str(sid)]
    if prompt and str(prompt).strip():
        cmd.append(sanitize.scrub(str(prompt))[0])
    return subprocess.run(
        cmd,
        cwd=str(target),
        capture_output=True,
        text=True,
        timeout=120,
        env=llm.runner_env(),
    )


def _run_stop_then_resume(cfg: config.Config, req: Requirement, sid: str,
                          target: Path, info: dict,
                          prompt: str) -> subprocess.CompletedProcess:
    """rework / brief launcher: a done-but-idle bg process rejects --resume,
    so stop it first (its work is committed and the transcript preserved)."""
    stop_session(sid, info=info)
    return _run_resume(cfg, req, sid, target, prompt)


def _run_launch(runner: Callable, *args) -> tuple[bool, str]:
    """(clean launch?, combined output) — a runner that raises is a failed
    launch, never an exception (all three callers promise never to raise)."""
    try:
        proc = runner(*args)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    ok = getattr(proc, "returncode", 1) == 0
    out = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
    return ok, out


def _record_resume(req: Requirement, ex: dict, ok: bool, out: str) -> None:
    ex["resume_attempts"] = int(ex.get("resume_attempts", 0)) + 1
    ex["last_resume_at"] = _utcnow_iso()
    ex["last_resume_ok"] = ok
    new_sid = _parse_session_id(out)   # a resume mints a new id
    if ok and new_sid:
        ex["session_id"] = new_sid     # adopt ONLY on clean launch; root stays anchored
    req.execution = ex
    save(req)
    analytics.log_event("resume_launch", req=req.id, ok=ok)


def resume(
    req: Requirement,
    cfg: Optional[config.Config] = None,
    runner: Optional[Callable[[], subprocess.CompletedProcess]] = None,
    prompt: Optional[str] = None,
) -> bool:
    """Resume a previously-dispatched background session (CONTRACT auto-resume).

    Runs ``claude --bg --resume <session_id>`` in the target repo so an agent
    interrupted by sleep / network loss / crash picks up where it left off.
    Records resume bookkeeping on req.execution. ``runner`` is injectable for
    tests. Returns True on a clean launch. Never raises.

    ``prompt``（v-next steer relay，add-only）：非空时作为 resume 的首条输入
    随会话注入（经 sanitize.scrub 防泄密——owner 亲打文本不围栏，见
    steer.build_steer_prompt 的信任级别注）。缺省 None = 行为与从前逐字节相同。
    """
    if cfg is None:
        cfg = config.load_config()
    ex = _execution(req)
    tinfo = _session_target(ex)
    if tinfo is None:
        return False
    sid, target = tinfo
    if not _mkdir_ok(target):
        return False
    ex.setdefault("root_session_id", sid)  # anchor: the conversation that exists on disk
    if runner is None:
        runner = functools.partial(_run_resume, cfg, req, sid, target, prompt)
    ok, out = _run_launch(runner)
    _record_resume(req, ex, ok, out)
    return ok


# transcript reading lives in act/lib/transcripts (lib layer, P3a); these are
# the executor-side names actd / tests / search_index have always used.
_transcript_info = transcripts.transcript_info
transcript_plain_text = transcripts.plain_text


def _transcript_cwd(sid: str) -> Optional[Path]:
    info = _transcript_info(sid)
    return info[1] if info else None


# Public name (P3b, 防腐 #2 rule 4): act.lib.actd.merge infers a merged
# secondary's worktree through it; ``_transcript_cwd`` stays bound for tests.
transcript_cwd = _transcript_cwd


_FINAL_DRAFT_MARKER = "FINAL DRAFT:"


def _fence_marker_idxs(lines: list[str]) -> list[int]:
    """Indices of standalone ``FINAL DRAFT:`` lines OUTSIDE ``` fences.

    A summary/draft often QUOTES the marker inside a fenced example (e.g. a
    draft explaining how chat delivery works) — fence state toggles on every
    line whose stripped text starts with ``` so those quoted markers can never
    win over the real out-of-fence one (audit 2026-07)."""
    idxs: list[int] = []
    in_fence = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and s.startswith(_FINAL_DRAFT_MARKER):
            idxs.append(i)
    return idxs


_CARD_TITLE_MARKER = "CARD TITLE:"


def _extract_card_title(lines: list[str]) -> tuple[Optional[str], list[str]]:
    """Pull the §37 ``CARD TITLE:`` line out of a delivery message.

    Same fence discipline as the FINAL DRAFT marker: only standalone lines
    OUTSIDE ``` fences count (a draft explaining this mechanism can quote the
    marker safely). Returns ``(title, remaining_lines)`` — the LAST marker line
    wins, every out-of-fence marker line is stripped so neither
    delivered_summary nor final_draft carries it. Oversize titles are clipped
    (``titles.clip_title``); an empty remainder yields ``(None, ...)``.
    """
    title: Optional[str] = None
    kept: list[str] = []
    in_fence = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            kept.append(ln)
            continue
        if _is_title_marker(s, in_fence):
            title = _title_candidate(s) or title
            continue  # strip the line either way — it is metadata, not content
        kept.append(ln)
    return title, kept


def _is_title_marker(stripped: str, in_fence: bool) -> bool:
    return not in_fence and stripped.startswith(_CARD_TITLE_MARKER)


def _title_candidate(marker_line: str) -> Optional[str]:
    """The clipped title on a ``CARD TITLE:`` line, or None when it must be
    refused (unclippable, or carrying a scrub placeholder).

    候选里带脱敏占位符 = agent 在复读 scrub 后的 outbound prompt 文本
    （sanitize.scrub 只改出站副本，注册表存原文）——写回会把脱敏词条从看板名
    里顶成 [脱敏] 并制造假 rename 污染 former_titles，打破「原样重复幂等」
    承诺（PR #103 review P1）。与 clip_title 返回 None 同待遇：拒收候选、
    marker 行照剥，fail 方向 = 保留旧名。"""
    from act.lib import titles
    cand = titles.clip_title(marker_line[len(_CARD_TITLE_MARKER):])
    if cand is not None and sanitize.MASK not in cand:
        return cand
    return None


def _lone_html_path(draft: str) -> Optional[Path]:
    """The single absolute ``*.html`` path a draft references, else None.

    §15 html output format: the prompt tells the agent to write HTML
    deliverables to a FILE and put its ABSOLUTE path (plus a short summary)
    after ``FINAL DRAFT:`` instead of pasting raw source. Exactly one such
    path line qualifies — anything ambiguous leaves the draft as-is."""
    hits: list[str] = []
    for ln in draft.splitlines():
        s = ln.strip().strip("`")  # tolerate backtick-quoted paths
        if s.startswith("/") and s.lower().endswith(".html"):
            hits.append(s)
    return Path(hits[0]) if len(hits) == 1 else None


_EMPTY_DELIVERY = {"delivered_summary": None, "final_draft": None, "card_title": None}


def _delivery_texts(session_id: str) -> list[str]:
    """The assistant texts after the last real user turn, from the first
    readable transcript matching the session's short id (bg agents may hop
    dirs mid-session; an unreadable match is skipped)."""
    # locate the transcript the same way transcript_info does: short-id
    # glob over ~/.claude/projects.
    short = str(session_id).split("-")[0]
    if not short:
        return []
    for f in transcripts.transcript_paths(short):
        try:
            texts = transcripts.assistant_texts(f, since_last_user=True)
        except OSError:
            continue
        if texts:
            return texts
    return []


def _delivery_message(texts: list[str]) -> str:
    """The LAST text bearing a standalone out-of-fence FINAL DRAFT marker,
    else the last text (a closing remark after the draft must not hide it)."""
    for t in reversed(texts):
        if _fence_marker_idxs(t.splitlines()):
            return t
    return texts[-1]


def _result(summary: str, draft: Optional[str], title: Optional[str]) -> dict:
    return {"delivered_summary": summary or None, "final_draft": draft,
            "card_title": title}


def _draft_after(lines: list[str], marker_idx: int) -> str:
    """Everything after the marker (the marker line's own remainder included),
    capped at 20000 chars."""
    ln_rest = lines[marker_idx].strip()[len(_FINAL_DRAFT_MARKER):].strip()
    draft_lines = ([ln_rest] if ln_rest else []) + lines[marker_idx + 1:]
    return "\n".join(draft_lines).strip()[:20000]


def _hydrate_html(before: str, final_draft: str) -> tuple[str, str]:
    """§15 html delivery: hydrate the draft from the one referenced file so
    the Mac 复制成稿 button still copies paste-ready HTML; the path stays
    visible in the summary. Fail-closed: any read problem keeps the
    path-draft untouched (the file is still there)."""
    html_file = _lone_html_path(final_draft)
    if html_file is None:
        return before, final_draft
    try:
        contents = html_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        contents = ""
    if not contents:
        return before, final_draft
    before = "\n".join(x for x in (before, final_draft) if x).strip()[:500]
    return before, contents[:20000]


def _split_delivery(text: str) -> dict:
    """Card title + summary + draft out of one delivery message (契约 C)."""
    # §37 CARD TITLE rides in the same delivery message (all delivery
    # modes) — extract + strip it BEFORE the FINAL DRAFT split so neither
    # delivered_summary nor final_draft carries the marker line.
    card_title, lines = _extract_card_title(text.splitlines())
    idxs = _fence_marker_idxs(lines)
    summary_text = "\n".join(lines).strip()[:500]
    if not idxs:
        return _result(summary_text, None, card_title)
    final_draft = _draft_after(lines, idxs[-1])
    if not final_draft:
        return _result(summary_text, None, card_title)
    before = "\n".join(lines[:idxs[-1]]).strip()[:500]
    before, final_draft = _hydrate_html(before, final_draft)
    return _result(before, final_draft, card_title)


def harvest_delivery(session_id: str) -> dict:
    """Extract the delivered summary (and chat-mode final draft) of a finished
    session from its transcript (v0.10 契约 C).

    Returns ``{"delivered_summary": str|None, "final_draft": str|None}``:
    - only assistant messages AFTER the last real user turn count — a 打回
      injects the feedback as a user message, so a previous round's rejected
      draft can never be resurrected into 待验收 (audit 2026-07); the initial
      dispatch prompt is also a user turn, so first deliveries are unchanged;
    - the delivery message is the LAST such text bearing a standalone
      out-of-fence ``FINAL DRAFT:`` line — a closing remark AFTER it (final
      check, cleanup note) must not hide the draft (audit 2026-07); with no
      marker, the last assistant text (500 chars max) is ``delivered_summary``;
    - within that message, everything after the LAST out-of-fence marker
      (20000 chars max) is ``final_draft`` and the part before (500 chars max)
      is ``delivered_summary``; an empty draft after that marker means NO
      draft — never fall back into summary prose (audit 2026-07: a bare
      trailing marker used to promote "FINAL DRAFT: see the doc" prose);
    - a draft referencing one absolute ``*.html`` file (§15 html output
      format) is hydrated from that file so the draft stays paste-ready; the
      path stays visible in ``delivered_summary``;
    - §37: an out-of-fence standalone ``CARD TITLE:`` line in the delivery
      message (any delivery mode) comes back as ``card_title`` (clipped) and
      is STRIPPED from both outputs; absent/empty/fenced -> None.
    Any failure returns all None — never raises.
    """
    empty = dict(_EMPTY_DELIVERY)
    try:
        texts = _delivery_texts(session_id)
        if not texts:
            return empty
        return _split_delivery(_delivery_message(texts))
    except Exception:  # noqa: BLE001 - harvesting must never break the pipeline
        return dict(empty)


# （§39 extract_question：retired v0.48.8（#119）——受阻会话由 reconcile 收割进
# 待验收，交付摘要天然保留会话最后的提问原文，投影不再单独提取 question。）


def _agent_info_strict(sid: str) -> Optional[dict]:
    """roster 探测的严格版：查询失败（CLI 超时/崩溃/非零退出/坏 JSON）返回
    None，与「roster 里真没有这个会话」（{}）区分开——stop 确认必须把前者当
    失败留痕：CLI 挂了不等于进程停了，把查询失败当「已停」会清台账、不发
    stop、不通知（§46.1）。"""
    proc = _roster_query()
    if proc is None or proc.returncode != 0:
        return None
    data = _parse_roster(proc.stdout)
    if data is None:
        return None
    return _find_agent(data, str(sid).split("-")[0])


def _agent_matches(a, short: str) -> bool:
    return isinstance(a, dict) and (
        str(a.get("id", "")) == short or str(a.get("sessionId", "")).startswith(short))


def _find_agent(data, short: str) -> dict:
    """{'pid', 'cwd'} of the roster entry for ``short``; {} when absent. A
    bare list only — this strict reader never unwraps envelopes."""
    for a in data if isinstance(data, list) else []:
        if _agent_matches(a, short):
            return {"pid": a.get("pid"), "cwd": a.get("cwd")}
    return {}


def _agent_info(sid: str) -> dict:
    """{'pid':..., 'cwd':...} for this session from claude agents; {} if unknown.

    宽松版（历史契约）：查询失败与「不在 roster」同样给 {}——resume/rework 等
    调用方本来就把两者当同一回事处理。要区分的走 :func:`_agent_info_strict`。
    """
    info = _agent_info_strict(sid)
    return info if info is not None else {}


def stop_session(session_id: str, info: Optional[dict] = None) -> bool:
    """Stop a live background session (``claude stop <short-id>``), then give
    the process 2s to die — the exact stop-before-resume path :func:`rework`
    has always used, extracted so actd's ``abort_execution`` (v0.10.2) can
    call it too.

    ``info`` = a pre-fetched :func:`_agent_info` dict (rework passes its own,
    keeping its original single-roster-query behaviour unchanged); omitted ->
    query the roster here. No live pid on the roster -> nothing to stop ->
    returns False without running anything. Returns True once the stop command
    has been issued. Raises the same OSError/subprocess.SubprocessError the
    old inline code did — callers decide whether a stop failure is fatal
    (rework: unchanged, handled by its outer try) or best-effort (actd's
    abort_execution catches + logs, state rollback is never blocked).
    """
    if info is None:
        info = _agent_info(session_id)
    if not (info or {}).get("pid"):
        return False
    short = str(session_id).split("-")[0]
    subprocess.run([_claude_bin(), "stop", short],
                   capture_output=True, text=True, timeout=30)
    time.sleep(2)
    return True


# stop 确认重试次数（§46）：stop_session_confirmed 在首次探测/停止之外最多再重试
# 这么多轮（每轮前退避 2s·4s…），仍存活才判失败——生产日志里 stop_session→False
# 一天出现 4 次而无人跟进（2026-08-07），失败必须留痕而不是只打一行日志。
STOP_CONFIRM_RETRIES = 2
# stop 确认总预算（§46.1）：调用方是单线程 actd 主循环，无预算时最坏串行
# ~218s（每轮 roster 探测 30s + stop 30s + 退避）——一次 stop 挂住整个守护
# 不可接受；超预算按失败落台账，让人跟进而不是让 daemon 等。
STOP_CONFIRM_BUDGET_S = 60.0


_PROBE_FAILED_MSG = ("roster query failed — cannot confirm whether "
                     "the session stopped")


class _StopCtx(NamedTuple):
    """The seams + deadline of one stop_session_confirmed call."""
    session_id: str
    prober: Callable[[str], Optional[dict]]
    stopper: Callable[..., bool]
    clock: Callable[[], float]
    deadline: float
    over_msg: str


def _probe_roster(ctx: _StopCtx) -> Optional[dict]:
    """The prober's answer; an exception is a probe FAILURE (None), not a crash."""
    try:
        return ctx.prober(ctx.session_id)
    except Exception:  # noqa: BLE001 - probe failure is a result, not a crash
        return None


def _probe_verdict(ctx: _StopCtx, issued: bool,
                   stopped_msg: str) -> tuple[Optional[tuple], Optional[dict]]:
    """Deadline check + one roster probe. Returns ``(verdict, info)``: a
    terminal verdict (budget over / probe failed / confirmed not running) with
    info None-or-dead, or ``(None, info)`` when the session is still alive."""
    if ctx.clock() >= ctx.deadline:
        return (False, issued, ctx.over_msg), None
    info = _probe_roster(ctx)
    if info is None:
        return (False, issued, _PROBE_FAILED_MSG), None
    if not info.get("pid"):
        return (True, issued, stopped_msg), info
    return None, info


def _try_stop(ctx: _StopCtx, info: dict) -> bool:
    """Issue one stop (with its 2s grace); a spawn failure just means retry."""
    try:
        ctx.stopper(ctx.session_id, info=info)   # 内含 2s 等死窗口
        return True
    except (OSError, subprocess.SubprocessError):
        return False                              # 失败进下一轮重探


def _stop_round(ctx: _StopCtx, issued: bool) -> tuple[Optional[tuple], bool]:
    """One verify-then-stop round: (terminal verdict or None, issued so far)."""
    verdict, info = _probe_verdict(ctx, issued, "stopped" if issued else "not running")
    if verdict is not None:
        return verdict, issued
    if ctx.clock() >= ctx.deadline:
        return (False, issued, ctx.over_msg), issued
    return None, (_try_stop(ctx, info) or issued)


def _stop_seams(prober, stopper) -> tuple[Callable, Callable]:
    """Defaults: the strict roster probe and :func:`stop_session`."""
    if prober is None:
        prober = _agent_info_strict
    if stopper is None:
        stopper = stop_session
    return prober, stopper


def _final_verdict(ctx: _StopCtx, issued: bool, retries: int) -> tuple[bool, bool, str]:
    """After the last round: one more probe decides between a late death and
    the still-alive failure."""
    verdict, info = _probe_verdict(ctx, issued, "stopped")
    if verdict is not None:
        return verdict
    return False, issued, (f"session {ctx.session_id} still alive (pid {info.get('pid')}) "
                           f"after {int(retries) + 1} stop attempts")


def stop_session_confirmed(
    session_id: str,
    retries: int = STOP_CONFIRM_RETRIES,
    prober: Optional[Callable[[str], Optional[dict]]] = None,
    stopper: Optional[Callable[..., bool]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    budget_s: float = STOP_CONFIRM_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, bool, str]:
    """带确认与有限重试的 stop（§46）——:func:`stop_session` 的可靠性外壳。

    旧 stop_session 的 True 只代表「stop 命令发出去了」，False 只代表「roster 上
    没看到活 pid」——两者都没验证进程真的死了。这里改成 verify-first 循环：
    每轮先探 roster（``prober``，默认 :func:`_agent_info_strict`），确认没有活
    pid 即视为已停；有 pid 则发 stop（``stopper``，默认 :func:`stop_session`，
    自带 2s 等死窗口），下一轮前按 2s·4s… 退避（``sleeper`` 可注入，测试传
    no-op）。两条失败快路（§46.1）：

    - **探测失败 ≠ 已停**：prober 返回 None / 抛异常（CLI 超时、崩溃）时进程
      可能还活着，立即判失败留痕——绝不当「不在 roster」清台账；也不重试，
      重试打的还是同一个无响应的 CLI。
    - **总预算 ``budget_s``**（默认 60s，``clock`` 可注入）：超预算立即按失败
      返回，不让单线程 actd 主循环被一次 stop 挂死。

    五个 seam（prober/stopper/sleeper/budget_s/clock）全部可注入——测试绝不
    spawn 真 ``claude``。

    Returns ``(stopped, issued, detail)``：
    - ``stopped``: 结束时会话已**确认**不在 roster 上跑（True 含「本来就没在
      跑」）；探测失败/超预算一律 False——没确认就不算停。
    - ``issued``: 至少真的发过一次 stop 命令（区分「我们停掉的」和「本来就死
      的」——_stop_live_session 只在前者时才收走 session_id，restore 不丢线索）。
    - ``detail``: 人话结论，失败时给台账/通知用。Never raises。
    """
    prober, stopper = _stop_seams(prober, stopper)
    ctx = _StopCtx(
        session_id=session_id,
        prober=prober,
        stopper=stopper,
        clock=clock,
        deadline=clock() + float(budget_s),
        over_msg=(f"stop not confirmed within {budget_s:.0f}s budget — "
                  "treated as failed"),
    )
    issued = False
    for attempt in range(int(retries) + 1):
        if attempt:
            sleeper(2.0 * attempt)   # 退避 2s / 4s / …
        verdict, issued = _stop_round(ctx, issued)
        if verdict is not None:
            return verdict
    return _final_verdict(ctx, issued, retries)


def _rework_abort(req: Requirement, ex: dict, err: str) -> bool:
    """A 打回 that could not even launch: persist the reason so the card
    surfaces it instead of silently staying in review with Zelin's feedback
    dropped (audit 2026-07). Same execution.last_error shape as the
    launch-failed path below; always returns False."""
    ex["last_error"] = err[:500]
    ex["last_error_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    req.execution = ex
    save(req)
    analytics.log_event("rework_failed", req=req.id,
                        failure_id=failures.classify(err))   # id only (#37)
    return False


def _has_feedback(feedback) -> bool:
    return bool((feedback or "").strip())


def _rework_target(req: Requirement, ex: dict) -> Optional[tuple[str, Path, dict]]:
    """(full sid, cwd, roster info) for the session to rework, or None after
    persisting why the 打回 could not launch (audit 2026-07: Zelin's feedback
    must never be dropped silently)."""
    sid = ex.get("session_id")
    if not sid:
        _rework_abort(req, ex, "rework failed: no session to rework "
                               "(card has no session_id)")
        return None
    # full UUID + the transcript's LAST cwd (usually the agent's worktree) —
    # both are REQUIRED for --resume to find the conversation (see _transcript_info).
    # No transcript anywhere (current sid or root) -> resuming is impossible;
    # give up WITHOUT launching (a launch would crash-loop minting new ids).
    info = _agent_info(sid)
    tinfo = _session_target(ex)
    if tinfo is None:
        _rework_abort(req, ex, "rework failed: transcript missing — "
                               "cannot resume the session")
        return None
    sid, target = tinfo
    if not _mkdir_ok(target):   # never OSError on a stale path
        _rework_abort(req, ex, f"rework failed: cannot recreate "
                               f"session cwd {target}")
        return None
    return sid, target, info


def _record_rework_failure(req: Requirement, ex: dict, out: str) -> bool:
    """launch failed — stay in review so the card remains actionable, don't
    pretend it's executing (reconcile would then resume-storm a dead sid).
    v0.10: persist the error so the dashboard/card can surface it."""
    err = (out or "").strip() or "rework launch failed (no output)"
    ex["last_error"] = err[:500]
    ex["last_error_at"] = _utcnow_iso()
    req.execution = ex
    save(req)
    analytics.log_event("rework_launch", req=req.id, ok=False,
                        round=ex["rework_count"])
    analytics.log_event("rework_failed", req=req.id,
                        failure_id=failures.classify(err))   # id only (#37)
    return False


def _record_rework(req: Requirement, ex: dict, cfg: config.Config,
                   feedback: str, ok: bool, out: str) -> bool:
    """§30: the rework round is RECORDED at the verdict; a clean launch flips
    review -> executing, a failed one stays in review with the error visible."""
    ex["rework_count"] = int(ex.get("rework_count", 0)) + 1
    ex["last_rework_at"] = _utcnow_iso()
    if not ok:
        return _record_rework_failure(req, ex, out)
    ex.pop("done", None)                      # it's working again
    ex.pop("last_error", None)                # clean relaunch clears stale errors
    ex.pop("last_error_at", None)
    new_sid = _parse_session_id(out)
    if new_sid:
        ex["session_id"] = new_sid
    req.execution = ex
    req.set_status(State.EXECUTING)
    save(req)
    # round = how many times this delivery got sent back (rework health);
    # the feedback TEXT itself is content — capture_input-gated.
    analytics.log_event("rework_launch", req=req.id, ok=ok,
                        round=ex["rework_count"],
                        feedback=(analytics.clip_content(feedback)
                                  if analytics.content_gate(cfg) else None))
    return ok


def rework(
    req: Requirement,
    feedback: str,
    cfg: Optional[config.Config] = None,
    runner: Optional[Callable[[str], subprocess.CompletedProcess]] = None,
) -> bool:
    """打回：send Zelin's feedback INTO the original session and set it working
    again (§11). A done-but-idle bg process rejects --resume, so stop it first
    (safe: its work is committed and the transcript is preserved), then
    ``claude --bg --resume <sid> "<feedback>"`` continues with full context.
    """
    if cfg is None:
        cfg = config.load_config()
    ex = _execution(req)
    if not _has_feedback(feedback):
        return False  # nothing to send — no feedback was lost (actd acks noop)
    resolved = _rework_target(req, ex)
    if resolved is None:
        return False
    sid, target, info = resolved
    ex.setdefault("root_session_id", sid)
    prompt = dispatch_prompt.rework_prompt(req, cfg, feedback)
    if runner is None:
        runner = functools.partial(_run_stop_then_resume, cfg, req, sid, target, info)
    ok, out = _run_launch(runner, prompt)
    return _record_rework(req, ex, cfg, feedback, ok, out)


# （§39 answer()：retired v0.48.8（#119）——「回答需输入」动作退役，语义由
# 待验收「打回 + 修改方向」（rework）完整覆盖；stop-idle-then-resume 管道由
# resume(prompt=)/brief() 继续承载 §39.2 安全窗口语义。）


def _rebook_briefing(req: Requirement, mutate: Callable[[Requirement, dict], None]) -> None:
    """Re-load the card and apply ONLY the briefing bookkeeping — the
    runner ran for up to 120s and a whole-object save of the stale
    snapshot would clobber concurrent writes (radar fold, §44 merge)."""
    fresh = load(req.id) or req
    fex = dict(fresh.execution or {})
    mutate(fresh, fex)
    fresh.execution = fex
    save(fresh)


def _briefing_give_up(req: Requirement, pend: list, reason: str) -> None:
    """Drop the queue with a notes trace — a briefing is FYI, not worth
    resurrecting a dead session over."""
    def m(fresh, fex):
        fex.pop("pending_briefings", None)
        fex.pop("briefing_attempts", None)
        registry.append_fold_note(fresh, f"背景信息未送达会话（{reason}），仅留档",
                                  "radar")
    _rebook_briefing(req, m)
    analytics.log_event("briefing", req=req.id, ok=False, n=len(pend))


def _pending_briefings(ex: dict) -> list[str]:
    return [str(t) for t in (ex.get("pending_briefings") or []) if str(t).strip()]


def _briefing_precheck(req: Requirement, ex: dict, pend: list) -> Optional[str]:
    """The sid to brief, or None: attempts exhausted / no session (both
    give up with a note) or the §39.2 window is closed (queue kept, no
    attempt burned — a later pass retries)."""
    sid = ex.get("session_id")
    if int(ex.get("briefing_attempts", 0)) >= 3:
        _briefing_give_up(req, pend, "3 次注入尝试失败")
        return None
    if not sid:
        _briefing_give_up(req, pend, "无会话")
        return None
    # §39.2 fresh probe at the last responsible moment: the caller decided
    # from a pass-start roster snapshot that may be minutes old — a session
    # that went back to WORK in that window must not be stop-killed. Window
    # closed → plain False: queue kept, no attempt burned, later pass retries.
    if not _briefing_window_open(sid):
        return None
    return sid


def _briefing_target(req: Requirement, ex: dict, pend: list) -> Optional[tuple[str, Path, dict]]:
    """(full sid, cwd, roster info) to inject into, or None (reason already
    recorded where a note is due)."""
    sid = _briefing_precheck(req, ex, pend)
    if sid is None:
        return None
    info = _agent_info(sid)
    tinfo = _session_target(ex)
    if tinfo is None:
        _briefing_give_up(req, pend, "transcript 缺失")
        return None
    sid, target = tinfo
    if not _mkdir_ok(target):
        _briefing_give_up(req, pend, "会话目录不可用")
        return None
    return sid, target, info


def _without_sent(items, sent: set) -> list:
    return [t for t in (items or []) if str(t) not in sent]


def _apply_briefing_delivery(fresh: Requirement, fex: dict, pend: list, sent: set,
                             now: str, sid: str, new_sid: Optional[str]) -> None:
    """Bookkeeping for a flushed batch (applied on a fresh card load)."""
    # only the lines we actually delivered leave the queue — a briefing
    # queued mid-flight by another process survives for the next pass
    rest = _without_sent(fex.get("pending_briefings"), sent)
    if rest:
        fex["pending_briefings"] = rest
    else:
        fex.pop("pending_briefings", None)
    fex.pop("briefing_attempts", None)
    fex["briefing_count"] = int(fex.get("briefing_count", 0)) + len(pend)
    fex["last_briefing_at"] = now
    # §44.3 已投递台账（add-only 键）：queue_briefing 靠它挡 crash-retry
    # 重放的二次入队——flush 之后 pending 已清，仅查 pending 的去重会让
    # 同一段背景信息进会话两遍（review finding，2026-08-18 第二轮）。
    # briefing 是低频 FYI，环形留最近 20 条足以覆盖 retry 窗口。
    seen = _without_sent(fex.get("delivered_briefings"), sent)
    fex["delivered_briefings"] = (seen + list(pend))[-20:]
    fex["resume_attempts"] = 0            # clean relaunch, answer() semantics
    fex.pop("resume_exhausted", None)
    fex.setdefault("root_session_id", sid)
    if new_sid:
        fex["session_id"] = new_sid


def _record_briefing(req: Requirement, ex: dict, pend: list, sid: str,
                     ok: bool, out: str) -> bool:
    now = _utcnow_iso()
    if not ok:
        attempts = int(ex.get("briefing_attempts", 0))

        def m_fail(fresh, fex):
            fex["briefing_attempts"] = attempts + 1
        _rebook_briefing(req, m_fail)  # queue kept — retried on a later pass, capped above
        analytics.log_event("briefing", req=req.id, ok=False, n=len(pend))
        return False
    _rebook_briefing(req, functools.partial(
        _apply_briefing_delivery, pend=pend, sent=set(pend), now=now, sid=sid,
        new_sid=_parse_session_id(out)))            # status untouched
    analytics.log_event("briefing", req=req.id, ok=True, n=len(pend))
    return True


def brief(
    req: Requirement,
    cfg: Optional[config.Config] = None,
    runner: Optional[Callable[[str], subprocess.CompletedProcess]] = None,
) -> bool:
    """§44.3: flush ``execution.pending_briefings`` into the live session as
    background info — the「By the way, just FYI」channel. Same
    stop-idle-then-resume plumbing as answer(), same rule: the CALLER (actd)
    must have verified the §39.2 safe window first (never interrupt a
    working session). Prefix tells the session explicitly that no action is
    expected, so it acknowledges and continues whatever it was doing.

    Bookkeeping is separate (briefing_count / last_briefing_at; caps at
    3 attempts per batch then drops the queue with a notes trace — a
    briefing is FYI, not worth resurrecting a dead session over).

    Returns True when the queue was flushed. Never raises.
    """
    if cfg is None:
        cfg = config.load_config()
    ex = _execution(req)
    pend = _pending_briefings(ex)
    if not pend:
        return False
    resolved = _briefing_target(req, ex, pend)
    if resolved is None:
        return False
    sid, target, info = resolved
    if runner is None:
        runner = functools.partial(_run_stop_then_resume, cfg, req, sid, target, info)
    ok, out = _run_launch(runner, dispatch_prompt.briefing_prompt(pend))
    return _record_briefing(req, ex, pend, sid, ok, out)


def _roster_agent(sid) -> Optional[dict]:
    """The live roster's entry for a session (any id shape), via the
    dashboard's indexed reader; None when absent."""
    from act.lib.dashboard import _index_agents, _run_claude_agents
    return _index_agents(_run_claude_agents()).get(str(sid))


def _agent_is_working(agent: dict) -> bool:
    """A live process in a non-blocked state — the one case that must not be
    interrupted."""
    from act.lib.agent_states import _BLOCKED_STATES
    state = str(agent.get("state") or "")
    return bool(agent.get("pid")) and state not in _BLOCKED_STATES


def _briefing_window_open(sid) -> bool:
    """§39.2 for briefings: True unless the session is actively WORKING with
    a live process (fresh roster read — never trust a pass-start snapshot
    across minutes). Absent/dead/blocked sessions are all open windows.
    Roster failure → open (matches answer's best-effort probe posture:
    stop_session itself no-ops without a live pid)."""
    try:
        agent = _roster_agent(sid)
        if agent is None:
            return True
        return not _agent_is_working(agent)
    except Exception:  # noqa: BLE001
        return True


# Public name (P3b, 防腐 #2 rule 4): act.lib.actd.reconcile's steer flush
# probes the window through it; ``_briefing_window_open`` stays bound —
# tests patch that spelling.
briefing_window_open = _briefing_window_open


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m act.executor <req_id>")
        return 2
    req_id = argv[0]
    req = load(req_id)
    if req is None:
        print(f"error: requirement {req_id} not found in registry")
        return 1
    try:
        dispatch(req)
    except DispatchError as e:
        print(f"dispatch failed (status stays {req.status}): {e}")
        return 1
    sid = _execution(req).get("session_id")
    print(f"dispatched {req_id} -> session {sid} (status={req.status})")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
