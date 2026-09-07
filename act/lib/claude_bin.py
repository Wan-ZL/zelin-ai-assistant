"""act/lib/claude_bin.py — what the doctor knows about the claude binary.

CONTRACT §25 (`daemon claude` / `stable claude` rows), §55 第三幕 (the
`launchd claude` grant text) and §55 第五幕 (the stable daemon copy).
Resolution itself lives in act/lib/config.py (``resolve_claude_bin`` /
``stable_claude_bin``), argv construction in act/llm.py; this module holds the
pure row builders act/doctor.py wraps (same dict shape as
``deploy_state.volume_access_row`` / board_server rows: status / detail / fix /
failure_id), so the doctor stays under the file cap (防腐 #1) and every
sentence is testable without a Probes object. Every machine touch comes in as
a callable (``run`` = doctor Probes.run, ``which``, ``login_shell_claude``).
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from act.lib import config, failures, platform

Run = Callable[..., Tuple[int, str]]

STABLE_ROW = "stable claude"

# The TCC death shapes of a claude that cannot read its cwd (§55 第三幕): Bun's
# unmapped-errno guess, or the raw EPERM spelling a node-installed claude
# prints. Shared by the `launchd claude` and `stable claude` rows.
BLIND_RE = re.compile(
    r"possibly due to low max file descriptors|operation not permitted|\bEPERM\b",
    re.IGNORECASE)

# A `--version` that fails once is asked again after this pause: install.sh
# may be `mv`-ing a refreshed copy into place at that very moment (§55 第五幕),
# and the doctor runs seconds after the install under auto-deploy (§56.3).
VERSION_RETRY_PAUSE_S = 1.0


def _row(status: str, detail: str, fix: str = "", failure_id: str = "") -> dict:
    return {"status": status, "detail": detail, "fix": fix, "failure_id": failure_id}


def looks_blind(text: str) -> bool:
    """Does this claude output carry a TCC-denied signature (§55 第三幕)?"""
    return bool(BLIND_RE.search(text or ""))


def _first_line(text: str, limit: int = 60) -> str:
    return text.strip().splitlines()[0][:limit] if text.strip() else ""


def version_of(run: Run, claude_path: str) -> str:
    """First line of ``<claude_path> --version``, "" when it fails."""
    rc, out = run([claude_path, "--version"], timeout=15)
    return _first_line(out) if rc == 0 else ""


def login_shell_version(run: Run, login_shell_claude: Callable[[], Optional[str]],
                        which: Callable[[str], Optional[str]]) -> Tuple[str, str]:
    """(path, version) of the claude the owner's shell runs; ("", "") unknown."""
    shell_claude = login_shell_claude() or which("claude") or ""
    return shell_claude, (version_of(run, shell_claude) if shell_claude else "")


# --------------------------------------------------------------------------- #
# §55 第五幕 — row `stable claude`
# --------------------------------------------------------------------------- #
def stable_claude_row(run: Run, login_shell_claude: Callable[[], Optional[str]],
                      which: Callable[[str], Optional[str]], installer: str,
                      stable: Optional[Path] = None) -> dict:
    """The stable daemon copy install.sh maintains at a fixed $HOME path
    (config.stable_claude_bin) — the one path the owner's Full Disk Access
    grant is attached to.

    - missing → WARN: daemons are running the per-version path Full Disk
      Access cannot follow across updates (the 2026-09-02 2.1.258 → 2.1.259
      regression); `bash install.sh` creates it.
    - present but `--version` fails (asked twice, VERSION_RETRY_PAUSE_S
      apart — install.sh may be replacing the file right now) → FAIL: every
      dispatch launches exactly this file (resolution order in
      config.resolve_claude_bin). Two FAILs, told apart by the output:
        - a TCC signature (looks_blind) → failure_id `claude_blind`, class
          owner_action: the copy is fine, this session's cwd is a gated path
          (auto-deploy runs the doctor from the repo on the external volume,
          under launchd) and the copy has no Full Disk Access grant yet —
          the 2026-09-03 v1.0.7 false rollback. Only the owner can fix it;
          the §56.3 verdict must not count it.
        - anything else (exec format, dyld, killed by Gatekeeper) →
          unclassified FAIL: the file itself is broken, re-copy it.
    - present, older than the login shell's Claude Code → WARN: still a
      working claude, just one version behind; the next `bash install.sh` /
      auto-deploy refreshes it in place (same path, grant kept).
    - present, same version → OK.
    """
    stable = stable or config.stable_claude_bin()
    if not config.stable_claude_present(stable):
        return _row("warn", failures.pick(
            "还没有稳定的 claude 副本（%s）——后台任务在用 ~/.local/bin/claude 指向的版本目录，"
            "claude 每次更新都换路径、完全磁盘访问授权跟不上",
            "no stable daemon copy yet (%s) - daemons run whatever ~/.local/bin/claude points at, "
            "a per-version path that Full Disk Access cannot follow across updates") % stable,
            failures.pick(
            "bash %s（复制一份到该路径；之后只需对该路径授一次完全磁盘访问）",
            "bash %s (copies claude there; then grant that one path Full Disk Access once)")
            % installer)
    return _stable_present_row(run, login_shell_claude, which, installer, stable)


def stable_version(run: Run, stable: Path) -> Tuple[str, str]:
    """(version, failure output) of the copy's ``--version`` — one retry."""
    rc, out = run([str(stable), "--version"], timeout=15)
    if rc != 0 or not out.strip():
        time.sleep(VERSION_RETRY_PAUSE_S)
        rc, out = run([str(stable), "--version"], timeout=15)
    if rc == 0 and out.strip():
        return _first_line(out), ""
    return "", out.strip()


def _stable_cannot_run_row(stable: Path, err: str, installer: str) -> dict:
    first = _first_line(err, 200) or "no output"
    if looks_blind(err):
        return _row("fail", failures.pick(
            "稳定副本 %s 在本会话里跑不了 `--version`（%s）——macOS 按可执行文件路径授完全磁盘"
            "访问，这份副本还没有；后台派发进外置卷 / Documents 下的任务目录会死在同一处",
            "the stable daemon copy %s cannot run `--version` from this session (%s) - macOS "
            "grants Full Disk Access per executable path and this copy has none yet; every "
            "dispatch into a task folder on an external volume / under Documents dies the same "
            "way") % (stable, first),
            failures.pick(
            "系统设置 → 隐私与安全性 → 完全磁盘访问：加入 %s（一次即可——install.sh 保持这条路径不变）",
            "System Settings > Privacy & Security > Full Disk Access: enable %s once - install.sh "
            "keeps this path stable across claude updates") % stable,
            "claude_blind")
    return _row("fail", failures.pick(
        "稳定副本 %s 跑不了 `--version`（%s）——后台派发起的就是这个文件",
        "the stable daemon copy %s cannot run `--version` (%s) - every dispatch launches exactly "
        "this file") % (stable, first),
        failures.pick("bash %s（重新从登录 shell 的 claude 复制一份）",
                      "bash %s (re-copies it from your login shell's claude)") % installer)


def _stable_present_row(run: Run, login_shell_claude: Callable[[], Optional[str]],
                        which: Callable[[str], Optional[str]], installer: str, stable: Path) -> dict:
    stable_ver, err = stable_version(run, stable)
    if not stable_ver:
        return _stable_cannot_run_row(stable, err, installer)
    shell_claude, shell_ver = login_shell_version(run, login_shell_claude, which)
    if shell_ver and shell_ver != stable_ver:
        return _row("warn", failures.pick(
            "稳定副本 %s 是 %s，而你的 shell 跑的是 %s（%s）——后台任务仍用副本，只是落后一版",
            "stable daemon copy %s is %s while your shell runs %s (%s) - daemons keep using the "
            "copy, just one version behind") % (stable, stable_ver, shell_claude, shell_ver),
            failures.pick(
            "bash %s 刷新副本（原地替换、同一路径，完全磁盘访问授权不变）；下一次自动部署也会刷",
            "bash %s refreshes the copy in place (same path, the Full Disk Access grant stays); "
            "the next auto-deploy does too") % installer)
    same = failures.pick("——与登录 shell 同版本", " - same version as your login shell") if shell_ver else ""
    return _row("ok", failures.pick("%s（%s）%s", "%s (%s)%s") % (stable, stable_ver, same))


# --------------------------------------------------------------------------- #
# row `daemon claude` (§25, 2026-07-08 two-installs incident; §55 第五幕)
# --------------------------------------------------------------------------- #
DAEMON_ROW = "daemon claude"
OUTDATED = "claude_cli_outdated"


def _shown(version: str) -> str:
    return version or "version unknown"


def _no_service_row(installer: str) -> dict:
    if platform.is_darwin():
        where = "launchd plist"
    elif platform.is_windows():
        where = "scheduled task"
    else:
        where = "systemd unit"
    return _row("warn", failures.pick(
        "actd 的 %s 未安装（或没带 PATH）——无法确认后台服务用的是哪个 claude",
        "actd %s not installed (or carries no PATH) - cannot verify which claude the daemon runs")
        % where,
        failures.pick("bash %s（重渲染服务配置，把你 shell 的 claude 目录排在 PATH 最前）",
                      "bash %s (renders the agent with your shell's claude dir first on PATH)")
        % installer)


def daemon_claude_target(daemon_path_env: Callable[[], Optional[str]],
                         login_shell_claude: Callable[[], Optional[str]],
                         installer: str) -> Tuple[Optional[dict], str, Optional[str], bool]:
    """Which file the daemons launch: ``(row, daemon_claude, shell_claude,
    via_stable)`` — ``row`` set means the answer is already a verdict (no
    service installed → WARN; nothing on the daemon PATH → FAIL). §55 第五幕:
    the stable copy, when present, is the answer (config.resolve_claude_bin),
    and the shell comparison is skipped for it (its version gap is the
    `stable claude` row's WARN, not a two-installs FAIL)."""
    stable = config.stable_claude_bin()
    if config.stable_claude_present(stable):
        return None, str(stable), None, True
    path_env = daemon_path_env()
    if not path_env:
        return _no_service_row(installer), "", None, False
    daemon_claude = shutil.which("claude", path=path_env)
    if not daemon_claude:
        return _row("fail",
                    "no claude anywhere on the daemon PATH - dispatch and radar extraction cannot run",
                    "install Claude Code, then: bash %s (re-renders the daemon PATH)" % installer,
                    "claude_cli_missing"), "", None, False
    return None, daemon_claude, login_shell_claude(), False


def two_installs_row(run: Run, daemon_claude: str, daemon_ver: str,
                     shell_claude: Optional[str], installer: str) -> Optional[dict]:
    """FAIL when the daemon's claude is a different, differently-versioned
    binary than the login shell's (2026-07-08: /opt/homebrew 2.1.16 shadowed
    ~/.local/bin 2.1.206 on the launchd PATH)."""
    if not shell_claude or os.path.realpath(shell_claude) == os.path.realpath(daemon_claude):
        return None
    shell_ver = version_of(run, shell_claude)
    if daemon_ver == shell_ver:
        return None
    return _row("fail",
                "the daemon runs %s (%s) but your shell runs %s (%s) - two installs; "
                "background dispatch uses the old one"
                % (daemon_claude, _shown(daemon_ver), shell_claude, _shown(shell_ver)),
                "update or remove the outdated copy, then: bash %s "
                "(re-renders the daemon PATH with your shell's claude first)" % installer,
                OUTDATED)


def bg_unsupported_row(run: Run, daemon_claude: str, daemon_ver: str,
                       installer: str) -> Optional[dict]:
    """--bg is what dispatch hangs off. Two-step probe: `--help` (side-effect
    free; 2.1.206 lists "--bg, --background") and, ONLY when help lacks it, a
    bare `claude --bg` whose error must carry the exact §25 outdated signature
    — so a reformatted future help page alone can never false-FAIL."""
    rc, help_out = run([daemon_claude, "--help"], timeout=15)
    if rc != 0 or not help_out.strip() or "--bg" in help_out:
        return None
    rc2, bg_out = run([daemon_claude, "--bg"], timeout=15)
    if rc2 != 0 and failures.classify(bg_out) == OUTDATED:
        return _row("fail",
                    "%s (%s) does not support --bg - every dispatch fails with "
                    "\"unknown option '--bg'\"" % (daemon_claude, _shown(daemon_ver)),
                    "update Claude Code (or remove this outdated copy), then: bash %s" % installer,
                    OUTDATED)
    return None


def daemon_claude_ok_suffix(via_stable: bool, shell_claude: Optional[str],
                            daemon_claude: str) -> str:
    if via_stable:
        return " - the stable daemon copy (§55; the `stable claude` row tracks its age)"
    if shell_claude and os.path.realpath(shell_claude) == os.path.realpath(daemon_claude):
        return " - same as your login shell"
    return ""


def daemon_claude_row(run: Run, daemon_path_env: Callable[[], Optional[str]],
                      login_shell_claude: Callable[[], Optional[str]], installer: str) -> dict:
    """launchd/cron can resolve a DIFFERENT claude than the login shell — a
    second, outdated install ranked first on the daemon PATH once broke every
    dispatch with "unknown option '--bg'", retrying forever behind a generic
    notification (2026-07-08). Compare the binary the daemons launch against
    the login shell's, and probe --bg support."""
    row, daemon_claude, shell_claude, via_stable = daemon_claude_target(
        daemon_path_env, login_shell_claude, installer)
    if row:
        return row
    daemon_ver = version_of(run, daemon_claude)
    row = (two_installs_row(run, daemon_claude, daemon_ver, shell_claude, installer)
           or bg_unsupported_row(run, daemon_claude, daemon_ver, installer))
    if row:
        return row
    return _row("ok", "%s (%s)%s" % (daemon_claude, _shown(daemon_ver),
                                     daemon_claude_ok_suffix(via_stable, shell_claude, daemon_claude)))


# --------------------------------------------------------------------------- #
# §55 第三幕/第五幕 — the `launchd claude` row's grant sentence
# --------------------------------------------------------------------------- #
def grant_text(claude_bin: str, real_bin: str, installer: str) -> str:
    """The Full Disk Access subject is whatever the probe just launched. The
    stable copy is a fixed path, so the grant is a one-time act; a versioned
    ~/.local/share/claude/versions/<v> path (no copy yet) still has to be
    re-granted after every update — say which one the owner is looking at."""
    stable = config.stable_claude_bin()
    if Path(claude_bin) == stable or real_bin == str(stable):
        return "enable %s once - install.sh keeps this path stable across claude updates" % stable
    return ("enable %s (again after every claude update; `bash %s` creates the stable copy %s "
            "so the grant only has to be done once)" % (real_bin, installer, stable))
