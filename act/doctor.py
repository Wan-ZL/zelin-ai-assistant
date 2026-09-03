"""Post-install diagnostics — ``python3 -m act.doctor`` (CONTRACT §25 行目录 +
机器输出；§59 模型旋钮两行；§55 / §56 的 TCC 行由探针家族实现)。

Every failure mode a fresh install has hit is SILENT: a launchd agent that
loads but never spawns, TCC blocking cron off the vault, a missing API key
killing headless claude minutes later in a log nobody reads, the app polling
the wrong AIASSISTANT_HOME. HANDOFF §2.15 requires "0 new cards" and
"silently dead" to be distinguishable — this module is the user-facing tool
for that.

    python3 -m act.doctor          # full run (ends with one cheap live claude call)
    python3 -m act.doctor --fast   # skip the live auth probe (spends no tokens)
    bash install.sh --check        # same as the full run

One line per check — symptom first, then the one-line fix:

    [ ok ] actd: running (pid 4242)
    [FAIL] dashboard: stale (generated 23 min ago) - actd is not writing; ...
           fix: launchctl list | grep aiassistant; tail -20 ~/Library/Logs/zelin-ai-assistant/actd.launchd.log

Never raises; exit code = number of FAILs (0 = healthy). Warnings cover
optional or degraded-but-working states (no Obsidian vault, recording off,
subscription-auth mode without a key file, ...).

Every touch of the machine goes through the :class:`Probes` dataclass so
tests can inject fakes (tests/test_doctor.py); the real implementations are
the defaults. The checks themselves live in ``act/lib/checks/`` by probe
family (environment / launchd / services / cron / pipeline); this module
composes them per OS, keeps the ``_check_*`` names the test suite and the
``diagnostic crashed`` row derive from, and owns the two §59 model rows
(they go through ``act/llm.py``, which the lib layer may not import).
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
from act.lib import board_server, config, deploy_state, heartbeat, platform
from act.lib import version as version_lib
from act.lib.checks import core, cron, environment, launchd, pipeline, services
from act.lib.checks.core import FAIL, OK, WARN, CheckResult

# -- re-exports the suite / sibling entrypoints address as doctor.<name> ------ #
ACTD_LABEL = core.ACTD_LABEL
SYNCD_LABEL = core.SYNCD_LABEL
SERVER_LABEL = core.SERVER_LABEL
RESIDENT_LABELS = core.RESIDENT_LABELS
ACTD_UNIT = core.ACTD_UNIT
SERVER_UNIT = core.SERVER_UNIT
ACTD_TASK = core.ACTD_TASK
SYSTEMD_RESIDENT = core.SYSTEMD_RESIDENT
LABEL_PREFIX = core.LABEL_PREFIX
AUTODEPLOY_LABEL = launchd.AUTODEPLOY_LABEL
CRON_PROBE_FRESH_SECONDS = cron.CRON_PROBE_FRESH_SECONDS
CRON_PROBE_PATH = cron.CRON_PROBE_PATH
DASHBOARD_FRESH_SECONDS = pipeline.DASHBOARD_FRESH_SECONDS
SCREENPIPE_STALE_SECONDS = environment.SCREENPIPE_STALE_SECONDS
MIN_PYTHON = environment.MIN_PYTHON
MISSING_ACT = launchd.MISSING_ACT
MISSING_YAML = launchd.MISSING_YAML
_PROBE_TIMEOUT = core.PROBE_TIMEOUT
# §59 model liveness: one "ok" per explicit knob; a model that exists answers
# in seconds, one that does not is rejected before any generation.
_MODEL_PROBE_TIMEOUT = 60

_installer = core.installer
_pick = core.pick
_run = core.run
_resolve_key = environment.resolve_key
_launchd_log_paths = launchd.launchd_log_paths
_launchd_log_tail = launchd.launchd_log_tail
_launchd_log_mtime = launchd.launchd_log_mtime
_launchd_claude_probe = launchd.claude_probe
_plist_number_of_files = launchd.plist_number_of_files
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
    # None -> derive from act/systemd (resident services + *.timer); Linux only
    systemd_units: Optional[List[str]] = None
    # None -> derive from act/tasksched (full \ZelinAIAssistant\ names); Windows only
    scheduled_tasks: Optional[List[str]] = None
    screenpipe_db: Path = field(
        default_factory=lambda: Path.home() / ".screenpipe" / "db.sqlite")
    legacy_key_path: Path = field(
        default_factory=lambda: Path("~/.config/anthropic-key.txt").expanduser())
    # daemon-vs-shell claude comparison (the 2026-07-08 two-installs incident)
    daemon_path_env: Callable[[], Optional[str]] = environment.installed_actd_path_env
    login_shell_claude: Callable[[], Optional[str]] = environment.login_shell_claude
    # §55 迁移探测：label → 已安装 plist 原文（None = 没装）；tests 注入保持 hermetic
    installed_plist_text: Callable[[str], Optional[str]] = core.installed_plist_text
    # §55 日志归因：short name → 该 agent 自管日志的末尾（"" = 读不到）
    launchd_log_tail: Callable[[str], str] = launchd.launchd_log_tail
    # §55 孤儿探测：~/Library/LaunchAgents 里带前缀的 plist label（文件面）
    installed_agent_labels: Callable[[], List[str]] = launchd.installed_agent_labels
    # §47.4 心跳：state/actd.heartbeat 的读取 + 进程探活（tests 注入保持 hermetic）
    heartbeat_read: Callable[[], Optional[dict]] = heartbeat.read
    pid_alive: Callable[[int], Optional[bool]] = pipeline.pid_alive
    # §55 第三幕：在一次性 launchd job 里跑 `claude --version`（cwd = 默认工作
    # repo）——终端看不见的 TCC 失败只能这样问出来；tests 注入，绝不真起 launchd
    launchd_claude_probe: Callable[[str, str], dict] = launchd.claude_probe
    # §59 (D22)：Claude Code 全局默认模型（~/.claude/settings.json `model`）——
    # follow 模式继承的就是它；tests 注入，绝不读开发者的真文件
    claude_code_settings: Callable[[], dict] = llm.read_claude_code_default_model
    # §54 看板 server：回环 /api/health 探针（port → verdict dict）；tests 注入，
    # 默认实现在 AIASSISTANT_HTTP_PROBE=0 下自报 unavailable（行不出）
    board_health: Callable[[int], dict] = board_server.health_probe
    # §56.4 HOME 镜像（auto-deploy 自己的真源，TCC 永不拦 $HOME）：`launchd volume
    # access` 行读它的 unattended_* 三元组；tests 注入保持 hermetic
    deploy_mirror_read: Callable[[], Optional[dict]] = deploy_state.read_mirror
    # §56.3 第 1 步日志证据：launchd stderr 文件的 mtime（它没有时间戳）
    launchd_log_mtime: Callable[[str], Optional[float]] = launchd.launchd_log_mtime
    version_status: Callable[[], dict] = version_lib.status_probe  # §56.1 stamp vs describe；tests 注入（沙箱非 git）


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


def _check_launchd_fd_limit(probes: Probes):
    return launchd.check_fd_limit(probes)


def _check_launchd_claude(probes: Probes):
    return launchd.check_claude(probes)


def _check_launchd_volume_access(probes: Probes):
    return launchd.check_volume_access(probes)


def _check_systemd(probes: Probes):
    return services.check_systemd(probes)


def _check_scheduled_tasks(probes: Probes):
    return services.check_scheduled_tasks(probes)


def _check_cron(probes: Probes):
    return cron.check_cron(probes)


def _check_cron_probe(probes: Probes, cron_installed: bool):
    return cron.check_cron_probe(probes, cron_installed)


def _check_store2(probes: Probes):
    return pipeline.check_store2(probes)


def _check_dashboard(probes: Probes):
    return pipeline.check_dashboard(probes)


def _check_heartbeat(probes: Probes):
    return pipeline.check_heartbeat(probes)


def _check_board_server(probes: Probes):
    return pipeline.check_board_server(probes)


def _check_ui_build(probes: Probes):
    return pipeline.check_ui_build(probes)


def _check_auto_deploy(probes: Probes):
    return pipeline.check_auto_deploy(probes)


def _check_obsidian(probes: Probes):
    return environment.check_obsidian(probes)


def _check_screenpipe(probes: Probes):
    return environment.check_screenpipe(probes)


def _check_npx(probes: Probes):
    return environment.check_npx(probes)


def _check_gh(probes: Probes):
    return environment.check_gh(probes)


def _check_claude_auth(probes: Probes):
    return environment.check_claude_auth(probes)


# --------------------------------------------------------------------------- #
# §59 (D22) model knobs — what "follow" inherits + does an explicit id answer
# --------------------------------------------------------------------------- #
def _model_knobs(cfg) -> dict:
    """{"dispatch": id|None, "pipeline": id|None} — None = follow."""
    return {mode: llm.model_for(mode, cfg) for mode in llm.MODES}


def _knob_text(knobs: dict) -> str:
    return " · ".join("%s: %s" % (mode, knobs[mode] or "follow") for mode in llm.MODES)


def _check_claude_code_model(probes: Probes):
    """One row, file reads only (rides under --fast too): the Claude Code global
    default every follow-mode call inherits, plus where the two knobs point.
    Never FAIL — this row informs; §56's rollback verdict must not turn on it.
    WARN when a knob follows a NON-canonical global default: that is exactly
    the 2026 EAP-alias retirement that broke every dispatch silently."""
    info = probes.claude_code_settings() or {}
    knobs = _model_knobs(config.load_config())
    knob_text = _knob_text(knobs)
    if info.get("exists") and not info.get("parseable"):
        return _unparseable_settings_row(knob_text)
    global_model = info.get("model")
    following = _following_modes(knobs)
    if _alias_risk(global_model, following):
        return _noncanonical_default_row(global_model, following, knob_text)
    return _default_ok_row(global_model, knob_text)


def _following_modes(knobs: dict) -> list:
    return [m for m in llm.MODES if knobs[m] is None]


def _alias_risk(global_model, following: list) -> bool:
    """A knob follows a global default that is not a canonical id."""
    return bool(global_model and following and not llm.is_canonical(global_model))


def _default_ok_row(global_model, knob_text: str) -> CheckResult:
    shown = global_model or _pick("未设置（Claude Code 内置默认）", "unset (Claude Code built-in default)")
    return CheckResult("claude code model", OK,
                       _pick("全局默认 %s（%s）", "global default %s (%s)") % (shown, knob_text))


def _unparseable_settings_row(knob_text: str) -> CheckResult:
    return CheckResult(
        "claude code model", WARN,
        _pick("~/.claude/settings.json 不是合法 JSON——follow 模式继承的全局默认读不出来（%s）",
              "~/.claude/settings.json is not valid JSON - the global default that follow mode inherits is unreadable (%s)") % knob_text,
        _pick("手动修好那个文件（Claude Code 自己也读它）",
              "fix that file by hand (Claude Code reads it too)"))


def _noncanonical_default_row(global_model: str, following: list, knob_text: str) -> CheckResult:
    return CheckResult(
        "claude code model", WARN,
        _pick("全局默认 `%s` 不是 canonical id，%s 跟随它——别名/后缀下线那天这些调用会静默全败（%s）",
              "global default `%s` is not a canonical id and %s follow it - the day the alias/suffix retires those calls fail silently (%s)")
        % (global_model, "/".join(following), knob_text),
        _pick("设置页「模型」→「设为 <canonical id>」改全局默认，或给旋钮选一个显式 canonical id",
              "Settings > Models > \"Set to <canonical id>\" for the global default, or pick an explicit canonical id per knob"))


def _model_failed_row(name: str, mode: str, model: str, rc: int, out) -> CheckResult:
    tail = " ".join(str(out).strip().split())[-120:] if str(out).strip() else "no output"
    consequence = (_pick("派工会全部失败", "every dispatch will fail")
                   if mode == llm.MODE_DISPATCH else
                   _pick("雷达/分诊/判官/问答会全部失败",
                         "radar / triage / judge / ask will all fail"))
    return CheckResult(
        name, FAIL,
        _pick("模型 %s 不可用，%s（exit %s: %s）",
              "model %s is unavailable, %s (exit %s: %s)")
        % (model, consequence, rc, tail),
        _pick("设置页「模型」改回「跟随 Claude Code 全局」或换一个 canonical id",
              "Settings > Models: switch back to \"follow Claude Code\" or pick a canonical id"),
    ).with_failure("model_unavailable")


def _model_row(probes: Probes, cfg, mode: str, model: Optional[str], probed: dict) -> CheckResult:
    """One knob's row; ``probed`` caches {model: (rc, out)} so dispatch ==
    pipeline is one live call, not two."""
    name = "model %s" % mode
    if model is None:
        return CheckResult(name, OK, _pick("follow（继承 Claude Code 全局默认，不探）",
                                           "follow (inherits the Claude Code default, not probed)"))
    if not probes.which("claude"):
        # the `claude CLI` row already FAILs; do not double-blame the model
        return CheckResult(name, WARN, _pick("%s — 跳过（未找到 claude CLI）",
                                             "%s - skipped (claude CLI not found)") % model)
    if model not in probed:
        probed[model] = probes.run(llm.probe_argv(model, cfg), env=llm.runner_env(),
                                   timeout=_MODEL_PROBE_TIMEOUT)
    rc, out = probed[model]
    if rc == 0:
        return CheckResult(name, OK, _pick("%s — 活探针 ok", "%s - live probe ok") % model)
    return _model_failed_row(name, mode, model, rc, out)


def _check_model_liveness(probes: Probes):
    """Per explicit knob: one minimal live call with that --model. follow =
    skipped (nothing to probe; the auth row already covers the default).
    FAIL speaks plainly: the model in Settings is unavailable, dispatch /
    pipeline will fail wholesale."""
    cfg = config.load_config()
    knobs = _model_knobs(cfg)
    probed: dict = {}
    return [_model_row(probes, cfg, mode, knobs[mode], probed) for mode in llm.MODES]


# --------------------------------------------------------------------------- #
# Composition per OS
# --------------------------------------------------------------------------- #
# Shared checks that run on every OS (pure Python / portable subprocess).
_CHECKS_COMMON_HEAD = [
    _check_home,
    _check_version,
    _check_claude,
    _check_stable_claude,   # §55 第五幕（darwin only; [] elsewhere）
    _check_daemon_claude,
    _check_runtime_python,
    _check_config,
    _check_anthropic_key,
    _check_state_dirs,
]


def _service_checks() -> "tuple[list, list]":
    """(middle, tail_extra) for this OS: launchd (macOS) <-> systemd (Linux)
    <-> Task Scheduler (Windows); the macOS-only screen-ingest checks ride
    behind the shared tail."""
    if platform.is_darwin():
        return ([_check_launchd, _check_launchd_paths, _check_launchd_fd_limit,
                 _check_launchd_claude, _check_launchd_volume_access,
                 _check_launchd_orphans, _check_cron],
                [_check_screenpipe, _check_npx])
    if platform.is_windows():
        return [_check_scheduled_tasks], []
    return [_check_systemd], []


def _checks_for_platform() -> List:
    """Compose the check list for the current OS.

    Shared checks always run. The service check swaps launchd (macOS) <->
    systemd (Linux) <-> Task Scheduler (Windows). The macOS-only screen-ingest /
    crontab checks (cron chain + FDA probe, screenpipe db, node/npx) are
    conditioned out off-macOS: Linux/Windows v1 defer screen ingest
    (docs/LINUX.md, docs/WINDOWS.md) and drive radars via systemd timers /
    scheduled tasks, so there is no crontab ingest chain to probe.
    """
    middle, tail_extra = _service_checks()
    # §47.4 heartbeat rides right behind the dashboard freshness row on every
    # OS: the two together tell "dead" (dashboard stale, no pid) from "stuck"
    # (pid alive, heartbeat stale).
    return (_CHECKS_COMMON_HEAD + middle
            + [_check_store2, _check_dashboard, _check_heartbeat,
               _check_board_server, _check_ui_build, _check_auto_deploy,
               _check_obsidian]
            + tail_extra + [_check_gh, _check_claude_code_model])


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
        checks.append(_check_model_liveness)   # §59: spends tokens only for explicit knobs
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


def render_json(results: List[CheckResult]) -> str:
    """§25 machine output: one row per check for the app's diagnostics page."""
    rows = [{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix,
             "failure_id": r.failure_id, "action_id": r.action_id}
            for r in results]
    return json.dumps({"home": str(config.HOME), "checks": rows},
                      ensure_ascii=False, indent=1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m act.doctor",
        description="Post-install diagnostics for Zelin's AI Assistant.")
    parser.add_argument("--fast", action="store_true",
                        help="skip the live claude auth probe (spends no tokens)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output (one row per check, §25)")
    return parser


def main(argv: Optional[List[str]] = None, probes: Optional[Probes] = None) -> int:
    """Run all checks, print the report, return the number of FAILs (max 99)."""
    try:
        args = _parser().parse_args(argv)
        results = run_checks(probes=probes, fast=args.fast)
        if args.as_json:
            print(render_json(results))
        else:
            print("act.doctor - home: %s" % config.HOME)
            print(render(results))
        return min(sum(r.status == FAIL for r in results), 99)
    except SystemExit:
        raise  # argparse --help / bad flag
    except Exception as exc:  # noqa: BLE001 - the doctor itself must never crash
        print("[FAIL] doctor: internal error: %r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
