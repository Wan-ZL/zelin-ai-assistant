"""Post-install diagnostics — ``python3 -m act.doctor`` (CONTRACT §25 行目录 +
机器输出；§59 模型旋钮行；§55 的 TCC 行由探针家族实现；§78 隔离两行
``claude bare supported`` / ``isolation footprint``)。

Every failure mode a fresh install has hit is SILENT: a launchd agent that
loads but never spawns, TCC blocking cron off the vault, a missing API key
killing headless claude minutes later in a log nobody reads, the app polling
the wrong AIASSISTANT_HOME — this module is the user-facing tool that makes
them visible.

    python3 -m act.doctor          # full run (ends with cheap live claude calls)
    python3 -m act.doctor --fast   # skip the live probes (spends no tokens)
    bash install.sh --check        # same as the full run
    python3 -m act.doctor --fresh-install   # §69: wired / needs-you / unwired / broken

One line per check — symptom first, then the one-line fix:

    [ ok ] server: running (pid 4242)
    [FAIL] anthropic key: no Anthropic API key - ...
           fix: paste your API key in Settings > Credentials ...

Never raises; exit code = number of FAILs (0 = healthy). Every touch of the
machine goes through the :class:`Probes` dataclass so tests can inject fakes
(tests/test_doctor.py). The checks live in ``act/lib/checks/`` by probe family
(environment / launchd / cron / pipeline); this module composes them, keeps the
``_check_*`` names the suite and the ``diagnostic crashed`` row derive from,
and owns the rows that must go through ``act/llm.py`` (the lib layer may not
import it): the live ``claude auth`` probe and the §59 model rows run with the
§78 isolation — ``llm.runner_env`` (CLAUDE_CONFIG_DIR + API key), the
ISOLATION_ARGV flags and ``config.headless_cwd()`` — so a probe never writes a
session into the owner's Claude Code home or loads the repo's CLAUDE.md.

v2.0.0 (Act pipeline retired; full text in tag ``full``): the store2 /
dashboard / actd heartbeat / auto-deploy / launchd volume access / launchd fd
limit / gh CLI / power probe rows and the Linux / Windows service rows are gone;
the ``claude code model`` row (which read the owner's Claude Code settings) is
replaced by ``models`` (the app's own knobs only).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # noqa: F401 - tests patch doctor.subprocess.run (the module is shared)
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from act import llm
from act.lib import board_server, config, failures, fresh_install, platform
from act.lib import version as version_lib
from act.lib.checks import core, cron, environment, launchd, pipeline
from act.lib.checks.core import FAIL, OK, WARN, CheckResult

# -- re-exports the suite / sibling entrypoints address as doctor.<name> ------ #
SERVER_LABEL = core.SERVER_LABEL
RESIDENT_LABELS = core.RESIDENT_LABELS
LABEL_PREFIX = core.LABEL_PREFIX
CRON_PROBE_FRESH_SECONDS = cron.CRON_PROBE_FRESH_SECONDS
CRON_PROBE_PATH = cron.CRON_PROBE_PATH
SCREENPIPE_STALE_SECONDS = environment.SCREENPIPE_STALE_SECONDS
MIN_PYTHON = environment.MIN_PYTHON
MISSING_ACT = launchd.MISSING_ACT
MISSING_YAML = launchd.MISSING_YAML
_PROBE_TIMEOUT = core.PROBE_TIMEOUT
# §59 model liveness: a model that exists answers in seconds, one that does
# not is rejected before any generation.
_MODEL_PROBE_TIMEOUT = 60

_installer = core.installer
_pick = core.pick
_run = core.run
_resolve_key = environment.resolve_key
_launchd_log_paths = launchd.launchd_log_paths
_launchd_log_tail = launchd.launchd_log_tail
_launchd_log_mtime = launchd.launchd_log_mtime
_launchd_claude_probe = launchd.claude_probe
_row_from = core.row_from


# --------------------------------------------------------------------------- #
# Probes — every external effect, injectable for tests
# --------------------------------------------------------------------------- #
@dataclass
class Probes:
    which: Callable[[str], Optional[str]] = shutil.which
    run: Callable[..., Tuple[int, str]] = core.run
    launchctl_list: Callable[[], str] = core.launchctl_list
    crontab: Callable[[], str] = core.crontab
    now: Callable[[], float] = time.time
    # None -> derive from act/launchd/*.plist basenames under AIASSISTANT_HOME
    launchd_labels: Optional[List[str]] = None
    screenpipe_db: Path = field(
        default_factory=lambda: Path.home() / ".screenpipe" / "db.sqlite")
    legacy_key_path: Path = field(
        default_factory=lambda: Path("~/.config/anthropic-key.txt").expanduser())
    # daemon-vs-shell claude comparison (the 2026-07-08 two-installs incident);
    # the daemon is the server agent since v2.0.0
    daemon_path_env: Callable[[], Optional[str]] = environment.installed_daemon_path_env
    login_shell_claude: Callable[[], Optional[str]] = environment.login_shell_claude
    # §55 迁移探测：label → 已安装 plist 原文（None = 没装）；tests 注入保持 hermetic
    installed_plist_text: Callable[[str], Optional[str]] = core.installed_plist_text
    # §55 日志归因：short name → 该 agent 自管日志的末尾（"" = 读不到）
    launchd_log_tail: Callable[[str], str] = launchd.launchd_log_tail
    # §55 / §78 孤儿与足迹探测：~/Library/LaunchAgents 里带我们前缀的 plist label（文件面）
    installed_agent_labels: Callable[[], List[str]] = launchd.installed_agent_labels
    # §78 足迹：owner 的 skills 目录里指进本 repo 的软链名（只 lstat / readlink）；tests 注入
    repo_skill_links: Callable[[], List[str]] = launchd.repo_skill_links
    # §55 第三幕：在一次性 launchd job 里跑 `claude --version`（cwd = §78 中性目录）；
    # tests 注入，绝不真起 launchd
    launchd_claude_probe: Callable[[str, str], dict] = launchd.claude_probe
    # §54 看板 server：回环 /api/health 探针（port → verdict dict）；tests 注入
    board_health: Callable[[int], dict] = board_server.health_probe
    # §56.3 日志证据：launchd stderr 文件的 mtime（它没有时间戳）
    launchd_log_mtime: Callable[[str], Optional[float]] = launchd.launchd_log_mtime
    version_status: Callable[[], dict] = version_lib.status_probe  # §56.1 stamp vs describe


# --------------------------------------------------------------------------- #
# Checks — thin named wrappers over the probe families. The ``_check_*`` names
# are load-bearing: ``_safe`` derives the "diagnostic crashed" row name from
# them and the suite composes/inspects the list by name.
# --------------------------------------------------------------------------- #
def _check_home(probes: Probes):
    return environment.check_home(probes)


def _check_version(probes: Probes):
    return environment.check_version(probes)


def _check_claude(probes: Probes):
    return environment.check_claude(probes)


def _check_stable_claude(probes: Probes):
    return environment.check_stable_claude(probes)


def _check_daemon_claude(probes: Probes):
    return environment.check_daemon_claude(probes)


def _check_runtime_python(probes: Probes):
    return environment.check_runtime_python(probes)


def _check_config(probes: Probes):
    return environment.check_config(probes)


def _check_anthropic_key(probes: Probes):
    return environment.check_anthropic_key(probes)


def _check_state_dirs(probes: Probes):
    return environment.check_state_dirs(probes)


def _check_launchd(probes: Probes):
    return launchd.check_agents(probes)


def _check_launchd_paths(probes: Probes):
    return launchd.check_paths(probes)


def _check_launchd_orphans(probes: Probes):
    return launchd.check_orphans(probes)


def _check_launchd_claude(probes: Probes):
    return launchd.check_claude(probes)


def _check_isolation_footprint(probes: Probes):
    return launchd.check_footprint(probes)


def _check_cron(probes: Probes):
    return cron.check_cron(probes)


def _check_cron_probe(probes: Probes, cron_installed: bool):
    return cron.check_cron_probe(probes, cron_installed)


def _check_board_server(probes: Probes):
    return pipeline.check_board_server(probes)


def _check_ui_build(probes: Probes):
    return pipeline.check_ui_build(probes)


def _check_obsidian(probes: Probes):
    return environment.check_obsidian(probes)


def _check_screenpipe(probes: Probes):
    return environment.check_screenpipe(probes)


def _check_npx(probes: Probes):
    return environment.check_npx(probes)


# --------------------------------------------------------------------------- #
# §78 isolation — does this claude know every flag the isolation relies on?
# --------------------------------------------------------------------------- #
def _check_claude_bare_supported(probes: Probes):
    """Row ``claude bare supported`` (§78): ``claude --help`` must list every flag
    in ``llm.ISOLATION_FLAGS``. Without one of them a headless run would read
    the owner's own Claude Code configuration — FAIL ``claude_bare_unsupported``.
    Side-effect free (``--help`` only); rides under --fast."""
    if not probes.which("claude"):
        return []   # the `claude CLI` row already FAILs
    claude = config.resolve_claude_bin()
    rc, out = probes.run([claude, "--help"], timeout=15)
    if rc != 0 or not out.strip():
        return CheckResult(
            "claude bare supported", WARN,
            _pick("`%s --help` 没有输出（exit %s）——确认不了隔离旗标",
                  "`%s --help` gave no output (exit %s) - cannot verify the isolation flags") % (claude, rc),
            _pick("重装 Claude Code 后重跑 doctor", "reinstall Claude Code, then re-run doctor"))
    missing = [flag for flag in llm.ISOLATION_FLAGS if flag not in out]
    if missing:
        return CheckResult(
            "claude bare supported", FAIL,
            _pick("%s 不认识 %s——headless 调用无法与 Claude Code 的个人配置隔离",
                  "%s does not know %s - headless calls cannot be isolated from the personal Claude Code setup")
            % (claude, " ".join(missing)),
            _pick("更新 Claude Code，再 bash install.sh 刷新稳定副本",
                  "update Claude Code, then bash install.sh to refresh the stable copy"),
        ).with_failure("claude_bare_unsupported")
    return CheckResult("claude bare supported", OK,
                       _pick("%s 支持全部隔离旗标（%s）", "%s supports every isolation flag (%s)")
                       % (claude, " ".join(llm.ISOLATION_FLAGS)))


# --------------------------------------------------------------------------- #
# §59 model knobs — where they point + does the pipeline model answer
# --------------------------------------------------------------------------- #
def _knob_text(model: str, fallback: Optional[str]) -> str:
    """``pipeline: <id> · fallback: <id>|off``."""
    return "pipeline: %s · fallback: %s" % (model, fallback or llm.FALLBACK_OFF)


def _check_models(probes: Probes):
    """One row, config reads only (rides under --fast): the pipeline model and the
    D53 fallback. WARN when the pipeline id is not canonical **and** the fallback
    cannot catch it (off, or itself non-canonical) — an alias retiring would then
    fail every recap silently. Never FAIL."""
    cfg = config.load_config()
    model = llm.model_for(llm.MODE_PIPELINE, cfg)
    fallback = llm.fallback_model(cfg)
    text = _knob_text(model, fallback)
    if llm.is_canonical(model) or (fallback and llm.fallback_is_canonical(fallback)):
        return CheckResult("models", OK, text)
    return CheckResult(
        "models", WARN,
        _pick("模型 `%s` 不是 canonical id，回退也接不住——别名下线那天会议纪要会静默全败（%s）",
              "model `%s` is not a canonical id and the fallback cannot catch it - the day the alias "
              "retires every recap fails silently (%s)") % (model, text),
        _pick("设置页「模型」选一个 canonical id，或把「回退模型」设成 canonical id",
              "Settings > Models: pick a canonical id, or set the fallback model to one"))


def _model_failed_row(model: str, rc: int, out) -> CheckResult:
    tail = " ".join(str(out).strip().split())[-120:] if str(out).strip() else "no output"
    return CheckResult(
        "model pipeline", FAIL,
        _pick("模型 %s 不可用，会议纪要会全部失败（exit %s: %s）",
              "model %s is unavailable, every meeting recap will fail (exit %s: %s)")
        % (model, rc, tail),
        _pick("设置页「模型」换一个 canonical id", "Settings > Models: pick a canonical id"),
    ).with_failure("model_unavailable")


def _check_model_liveness(probes: Probes):
    """One minimal live call with the pipeline ``--model`` under the §78 isolation
    (env = ``llm.runner_env``, cwd = ``config.headless_cwd()``)."""
    if not probes.which("claude"):
        return CheckResult("model pipeline", WARN, _pick("跳过（未找到 claude CLI）",
                                                         "skipped (claude CLI not found)"))
    cfg = config.load_config()
    model = llm.model_for(llm.MODE_PIPELINE, cfg)
    rc, out = probes.run(llm.probe_argv(model, cfg), env=llm.runner_env(cfg),
                         timeout=_MODEL_PROBE_TIMEOUT, cwd=config.headless_cwd())
    if rc == 0:
        return CheckResult("model pipeline", OK, _pick("%s — 活探针 ok", "%s - live probe ok") % model)
    return _model_failed_row(model, rc, out)


# --------------------------------------------------------------------------- #
# live auth probe (spends tokens; not under --fast) — §78 isolated
# --------------------------------------------------------------------------- #
def _auth_via(probes: Probes) -> str:
    """Human label of where the key the probe will use comes from ("" = none)."""
    _key, source = environment.resolve_key(probes)
    return source


def _check_claude_auth(probes: Probes):
    """One cheap live call with exactly the credential + isolation every headless
    run uses: ``llm.auth_probe_argv`` (ISOLATION_ARGV), ``llm.runner_env``
    (CLAUDE_CONFIG_DIR + ANTHROPIC_API_KEY), cwd = ``config.headless_cwd()``.
    No key → the ``anthropic key`` row already FAILs; this row says skipped."""
    if not probes.which("claude"):
        return CheckResult("claude auth", WARN,
                           _pick("跳过（未找到 claude CLI）", "skipped (claude CLI not found)"))
    cfg = config.load_config()
    env = llm.runner_env(cfg)
    if not env.get("ANTHROPIC_API_KEY"):
        return CheckResult("claude auth", WARN,
                           _pick("跳过（没有 API key——见 anthropic key 行）",
                                 "skipped (no API key - see the anthropic key row)"))
    via = _auth_via(probes) or "ANTHROPIC_API_KEY"
    rc, out = probes.run(llm.auth_probe_argv(cfg), env=env, timeout=_PROBE_TIMEOUT,
                         cwd=config.headless_cwd())
    if rc == 0:
        return CheckResult("claude auth", OK, "live call ok (API key from %s, isolated)" % via)
    return _auth_failed_row(via, rc, out)


def _auth_failed_row(via: str, rc: int, out: str) -> CheckResult:
    tail = " ".join(out.strip().split())[-120:] if out.strip() else "no output"
    return CheckResult(
        "claude auth", FAIL,
        "live call failed via API key from %s (exit %s: %s)" % (via, rc, tail),
        "check the key (active? billing?) or re-paste it in Settings > Credentials",
    ).with_failure(failures.classify(out) or "claude_auth_failed")


# --------------------------------------------------------------------------- #
# Composition (macOS; the launchd / cron / recording rows are darwin-only)
# --------------------------------------------------------------------------- #
_CHECKS_HEAD = [
    _check_home,
    _check_version,
    _check_claude,
    _check_stable_claude,   # §55 第五幕（darwin only; [] elsewhere）
    _check_daemon_claude,
    _check_claude_bare_supported,   # §78
    _check_runtime_python,
    _check_config,
    _check_anthropic_key,
    _check_state_dirs,
]

_CHECKS_DARWIN = [
    _check_launchd,
    _check_launchd_paths,
    _check_launchd_claude,
    _check_launchd_orphans,
    _check_isolation_footprint,   # §78
    _check_cron,
]

_CHECKS_TAIL = [_check_board_server, _check_ui_build, _check_obsidian]
_CHECKS_RECORDING = [_check_screenpipe, _check_npx]


def _checks_for_platform() -> List:
    """Compose the check list. v2.0.0 is macOS-only; off darwin (a CI runner, a
    dev box) the launchd / cron / recording rows are left out instead of
    reporting a machine that was never meant to run them."""
    if platform.is_darwin():
        return (_CHECKS_HEAD + _CHECKS_DARWIN + _CHECKS_TAIL + _CHECKS_RECORDING
                + [_check_models])
    return _CHECKS_HEAD + _CHECKS_TAIL + [_check_models]


def _safe(fn, probes: Probes) -> List[CheckResult]:
    try:
        res = fn(probes)
        return res if isinstance(res, list) else [res]
    except Exception as exc:  # noqa: BLE001 - a doctor bug must not mask real checks
        name = fn.__name__.replace("_check_", "").replace("_", " ")
        return [CheckResult(
            name, FAIL, "diagnostic crashed: %r" % exc,
            "report this: https://github.com/Wan-ZL/zelin-ai-assistant/issues")]


def run_checks(probes: Optional[Probes] = None, fast: bool = False) -> List[CheckResult]:
    probes = probes or Probes()
    checks = _checks_for_platform()
    if not fast:
        checks.append(_check_claude_auth)
        checks.append(_check_model_liveness)   # §59: one live call for the pipeline model
    results: List[CheckResult] = []
    for fn in checks:
        results.extend(_safe(fn, probes))
    return results


# --------------------------------------------------------------------------- #
# Rendering + entrypoint
# --------------------------------------------------------------------------- #
_BADGE = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


def render(results: List[CheckResult]) -> str:
    lines = []
    for r in results:
        lines.append("%s %s: %s" % (_BADGE[r.status], r.name, r.detail))
        if r.fix and r.status != OK:
            lines.append("       fix: %s" % r.fix)
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    oks = sum(r.status == OK for r in results)
    lines.append("")
    lines.append("%d ok / %d warn / %d fail%s" % (
        oks, warns, fails, "" if fails else " - pipeline looks healthy"))
    return "\n".join(lines)


def rows_of(results: List[CheckResult]) -> List[dict]:
    """§25 wire rows (one dict per check) — shared by --json and --fresh-install;
    `row_class` marks rows whose only fix is the owner's manual grant."""
    return [{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix,
             "failure_id": r.failure_id, "action_id": r.action_id,
             "row_class": r.row_class}
            for r in results]


def render_json(results: List[CheckResult]) -> str:
    """§25 machine output: one row per check for the app's diagnostics page."""
    return json.dumps({"home": str(config.HOME), "checks": rows_of(results)},
                      ensure_ascii=False, indent=1)


def _print_fresh_install(results: List[CheckResult], as_json: bool) -> int:
    """§69 summary over the same rows; exit code = broken rows (never TCC / credentials)."""
    summary = fresh_install.summarize(rows_of(results), fresh_install.read_report(), config.HOME)
    print(fresh_install.render_json(summary) if as_json else fresh_install.render(summary))
    return int(summary["exit_code"])


def _emit(results: List[CheckResult], args) -> int:
    """Print in the requested shape; return the exit code (FAIL count, or §69 broken count)."""
    if args.fresh:
        return _print_fresh_install(results, args.as_json)
    if args.as_json:
        print(render_json(results))
    else:
        print("act.doctor - home: %s" % config.HOME)
        print(render(results))
    return min(sum(r.status == FAIL for r in results), 99)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m act.doctor",
        description="Post-install diagnostics for Zelin's AI Assistant.")
    parser.add_argument("--fast", action="store_true",
                        help="skip the live claude probes (spends no tokens)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output (one row per check, §25)")
    parser.add_argument("--fresh-install", action="store_true", dest="fresh",
                        help="§69 summary: wired / waiting on you / unwired / broken; "
                             "implies --fast; exit code = broken rows")
    return parser


def main(argv: Optional[List[str]] = None, probes: Optional[Probes] = None) -> int:
    """Run all checks, print the report, return the number of FAILs (max 99)."""
    try:
        args = _parser().parse_args(argv)
        results = run_checks(probes=probes, fast=args.fast or args.fresh)
        return _emit(results, args)
    except SystemExit:
        raise  # argparse --help / bad flag
    except Exception as exc:  # noqa: BLE001 - the doctor itself must never crash
        print("[FAIL] doctor: internal error: %r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
