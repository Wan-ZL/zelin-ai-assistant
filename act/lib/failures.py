"""Failure classification — the routing table the error UX hangs on (CONTRACT §25).

Every known failure mode gets ONE stable id, a plain-language sentence in both
UI languages, and an ``action_id`` the Mac app maps to a one-click repair (or a
deep-link). Producers (executor dispatch errors, dashboard projection, doctor
checks) attach the id ALONGSIDE the raw text — raw text is never replaced, only
demoted to detail/tooltip.

Design law: fewer failure modes classified WELL beats full coverage. Anything
:func:`classify` cannot match returns ``None`` and the UI falls back to the
raw text plus the generic "让 AI 修 / Fix with AI" escape hatch (act/ai_fix.py).

The Swift side mirrors these ids in mac/Sources/Doctor.swift (FailureCatalog);
tests/test_failures.py drift-guards the two lists.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

# --------------------------------------------------------------------------- #
# catalog — id -> {plain_zh, plain_en, action_id}
#
# action_id vocabulary (Swift RepairAction):
#   install_claude    open the Claude Code install page
#   open_settings_key jump to Settings credentials (re-paste the API key)
#   install_node      open the Node.js download page
#   restart_engine    RecordingController stop->start (in-app)
#   reload_agent      render + launchctl load the agent plist (in-app)
#   repair_cron       re-run the installer's cron step (in-app, streamed)
#   grant_cron_fda    guided Full Disk Access grant for /usr/sbin/cron
#   restart_actd      render + reload the actd launchd agent (in-app)
#   fix_config        reveal config.yaml / restore from template
#   retry             transient — just try the action again
#   show_engine_log   reveal ~/.screenpipe/engine.log (download progress lives there)
#   regrant_screen    open System Settings -> Screen Recording (re-grant)
#   install_ffmpeg    open the ffmpeg download page (screen_audio dependency)
#   open_deps         jump to the dependencies/diagnostics page (the doctor row
#                     shows the exact binaries/paths involved)
# --------------------------------------------------------------------------- #
FAILURES: dict = {
    "claude_cli_missing": {
        "plain_zh": "claude 命令行没装好——助手无法研究或执行任何卡片",
        "plain_en": "The claude CLI is not installed — the assistant cannot research or execute any card",
        "action_id": "install_claude",
    },
    # a SECOND, older claude install shadowing the real one on the daemon's
    # PATH (2026-07-08: /opt/homebrew/bin/claude 2.1.16 vs ~/.local/bin
    # 2.1.206) — dispatch dies on "unknown option '--bg'" and retries forever.
    "claude_cli_outdated": {
        "plain_zh": "这台机器上有多个 claude 命令，后台服务在用过旧的那个——更新或删掉旧版，再重跑一次安装",
        "plain_en": "This Mac has more than one claude CLI and the background service is using an outdated copy — update or remove the old one, then re-run the installer",
        "action_id": "open_deps",
    },
    # §55 第三幕（2026-08-31 storm, verified 2026-09-01 under a throwaway
    # launchd job): the claude binary launched by the launchd-run actd gets
    # EPERM on getcwd/readdir when the task folder sits on a TCC-gated path
    # (external volume, ~/Documents, ~/Desktop, ~/Downloads). Bun renders that
    # unmapped errno as "An unknown error occurred, possibly due to low max
    # file descriptors (Unexpected)" — a GUESS, and a wrong one: the same job
    # runs `claude --version` fine with cwd=$HOME at ulimit 256, and raising
    # the fd ceiling to 8192 changed nothing (11 more failures, "Current
    # limit: 8192"). macOS keys Full Disk Access per executable PATH, so the
    # grant Terminal/python3 hold does not cover ~/.local/share/claude/
    # versions/<v> — and every claude update is a new path. No one-click
    # repair: it is the owner's TCC toggle (or moving the folder).
    "claude_blind": {
        "plain_zh": "后台服务里的 claude 读不到任务目录（macOS 按可执行文件授磁盘权限，launchd 起的"
                    " claude 没有「完全磁盘访问」，任务目录又在外置卷或 Documents/Desktop/Downloads"
                    " 里；claude 自己报的「low max file descriptors」是猜错的）——系统设置 → 隐私与"
                    "安全性 → 完全磁盘访问，打开 claude 当前版本那一项（~/.local/share/claude/"
                    "versions/<版本>，claude 每次更新后要再打开一次），或把任务目录搬到启动盘的"
                    "家目录下；然后把卡「停止 → 退回提案」再批准。doctor 的 `launchd claude` 行"
                    "能确认",
        "plain_en": "The claude binary the background service launches cannot read the task "
                    "folder (macOS grants disk access per executable; launchd-spawned claude "
                    "has no Full Disk Access and the folder sits on an external volume or in "
                    "Documents/Desktop/Downloads — claude's own \"low max file descriptors\" "
                    "guess is wrong) — System Settings → Privacy & Security → Full Disk "
                    "Access: enable the current claude version (~/.local/share/claude/"
                    "versions/<v>; repeat after each claude update), or move the task folder "
                    "under your home on the boot volume; then Stop → Discard & re-propose → "
                    "approve the card again. Doctor's `launchd claude` row confirms it",
        "action_id": "open_deps",
    },
    # genuine fd exhaustion: EMFILE/ENFILE spellings, Bun's own
    # ProcessFdQuotaExceeded / SystemFdQuotaExceeded ("bun ran out of file
    # descriptors"). launchd's gui domain hands every job a soft `ulimit -n`
    # of 256 (hard unlimited); the templates raise the SOFT limit (§55) and
    # re-rendering the agents is the fix — same one-click path as a dead actd.
    "fd_limit": {
        "plain_zh": "后台服务的打开文件数耗尽（launchd 默认软上限 256；错误里写着 EMFILE / too many"
                    " open files）——重跑一次安装器（bash install.sh）让每个后台服务带上更高的软上限，"
                    "再重新批准这张卡",
        "plain_en": "The background service ran out of open files (launchd's default soft limit "
                    "is 256; the error reads EMFILE / too many open files) — re-run the "
                    "installer (bash install.sh) so every agent carries a higher soft limit, "
                    "then approve the card again",
        "action_id": "restart_actd",
    },
    # issue #89: `claude --bg --dangerously-skip-permissions` refuses to start
    # until the bypassPermissions disclaimer has been accepted ONCE
    # interactively on this machine — a Task Scheduler / launchd session can
    # never do that, so a fresh install dispatches into a wall (49 attempts
    # in the report). One-time human step, same shape as claude_cli_outdated.
    "claude_bypass_disclaimer": {
        "plain_zh": "claude 还没在这台机器上接受过「跳过权限确认」的免责声明——在终端里手动跑一次"
                    " `claude --dangerously-skip-permissions` 并接受，后台派发才能启动",
        "plain_en": "claude has not accepted the bypass-permissions disclaimer on this machine "
                    "yet — run `claude --dangerously-skip-permissions` once in a terminal and "
                    "accept it, then background dispatch can start",
        "action_id": "open_deps",
    },
    "claude_auth_failed": {
        "plain_zh": "AI 的 API key 无效或过期——去设置页重新粘贴一个",
        "plain_en": "The AI API key is invalid or expired — re-paste one in Settings",
        "action_id": "open_settings_key",
    },
    "node_missing": {
        "plain_zh": "缺少 Node.js——录制引擎无法启动",
        "plain_en": "Node.js is missing — the recording engine cannot start",
        "action_id": "install_node",
    },
    "engine_dead": {
        "plain_zh": "录制引擎没有在运行——屏幕内容不会被记录",
        "plain_en": "The recording engine is not running — nothing on screen is being captured",
        "action_id": "restart_engine",
    },
    # NOT an error: first-run npx download of the pinned screenpipe package.
    # Copy must read as calm progress, never as a failure (audit 2.3).
    "engine_npm_download": {
        "plain_zh": "录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制",
        "plain_en": "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes",
        "action_id": "show_engine_log",
    },
    "engine_crashed": {
        "plain_zh": "录制引擎意外停了——点「重启引擎」再试；反复失败就看下面的引擎日志",
        "plain_en": "The recording engine stopped unexpectedly — click Restart engine; if it keeps happening, check the engine log lines below",
        "action_id": "restart_engine",
    },
    # screen_audio hard-requires ffmpeg at engine startup, and screenpipe's
    # built-in auto-installer is unreliable (2026-07-13: it wrote the binary
    # yet still exited "os error 2" on every attempt, killing the engine
    # seconds after spawn while the menu bar blamed Screen Recording TCC).
    "engine_ffmpeg_missing": {
        "plain_zh": "「屏幕+音频」需要 ffmpeg，这台电脑上还没有——装一个（brew install ffmpeg）或切回「仅屏幕」",
        "plain_en": "Screen + Audio needs ffmpeg, which this Mac does not have — install it (brew install ffmpeg) or switch back to Screen Only",
        "action_id": "install_ffmpeg",
    },
    # macOS ties the Screen Recording grant to the app's code signature —
    # an OS update or app reinstall changes it and silently revokes the grant.
    "screen_tcc_lost": {
        "plain_zh": "「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复",
        "plain_en": "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume",
        "action_id": "regrant_screen",
    },
    "agent_unloaded": {
        "plain_zh": "一个后台服务没有装载——它负责的工作停了",
        "plain_en": "A background service is not loaded — its work has stopped",
        "action_id": "reload_agent",
    },
    # §55 症状 4：解释器能 import yaml、plist 路径也对，但 macOS 按 binary 授
    # 权文件访问，它在 launchd 下读不到 repo。重装 agent 没用（同一个解释器会
    # 被再渲一遍）——要换解释器，所以动作是把人送到依赖页去重跑安装器。
    "interpreter_blind": {
        "plain_zh": "后台服务用的那个 Python 读不到项目文件夹（macOS 按程序单独授权，"
                    "后台任务不继承终端的权限）——重跑一次安装器会换一个能读的",
        "plain_en": "The Python the background services run cannot read the project "
                    "folder (macOS grants file access per program, and background jobs "
                    "do not inherit your terminal's grant) — re-running the installer "
                    "picks one that can",
        "action_id": "open_deps",
    },
    "cron_missing": {
        "plain_zh": "定时任务没有安装——屏幕记录不会变成笔记和卡片",
        "plain_en": "The scheduled jobs are not installed — screen captures never become notes or cards",
        "action_id": "repair_cron",
    },
    "cron_fda_blocked": {
        "plain_zh": "定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失",
        "plain_en": "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost",
        "action_id": "grant_cron_fda",
    },
    "dashboard_stale": {
        "plain_zh": "后台服务停止更新数据——看板显示的是旧内容",
        "plain_en": "The background service stopped updating data — the board shows old content",
        "action_id": "restart_actd",
    },
    # §47.4 heartbeat: the process is alive (launchctl shows a pid) but its
    # per-pass heartbeat stopped — 2026-08-31 22:31 actd sat idle in
    # time.sleep for 2.5h, no children, dashboard frozen, doctor green.
    # Distinct from dashboard_stale (process dead / never started): the fix
    # is a hard restart of the live process, not a reload.
    "actd_stalled": {
        "plain_zh": "后台服务进程还活着，但已经停止心跳（不再跑循环）——强制重启它："
                    "launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd",
        "plain_en": "The background service process is alive but its heartbeat stopped (the "
                    "loop is no longer running) — force-restart it: "
                    "launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd",
        "action_id": "restart_actd",
    },
    # a launchd agent with our label prefix but no template in act/launchd —
    # a retired service the installer failed to unload. 2026-08-31 audit: the
    # imessageradar agent (removed v0.21) kept running for 51 days, 23,613
    # tracebacks, because install.sh swallowed the bootout failure.
    "launchd_orphan": {
        "plain_zh": "有已退役的后台服务还在 launchd 里运行（仓库里已没有它的模板）——重跑一次安装器"
                    "把它卸掉（bash install.sh），或手动 launchctl bootout",
        "plain_en": "A retired background service is still loaded in launchd (the repo no "
                    "longer ships its template) — re-run the installer to unload it "
                    "(bash install.sh), or launchctl bootout it by hand",
        "action_id": "open_deps",
    },
    # §57 (D22): an explicit model knob names an id claude cannot serve
    # (alias/suffix retired, typo, no access). Only the doctor's liveness
    # probe produces it — dispatch failures keep their raw text.
    "model_unavailable": {
        "plain_zh": "设置里选的模型不可用——派工会全部失败；去设置页「模型」改回"
                    "「跟随 Claude Code 全局」或换一个 canonical id",
        "plain_en": "The model chosen in Settings is unavailable — every dispatch will "
                    "fail; in Settings → Models switch back to \"follow Claude Code\" "
                    "or pick a canonical id",
        "action_id": "open_deps",
    },
    "config_invalid": {
        "plain_zh": "配置文件写坏了——所有组件都退回默认设置",
        "plain_en": "The config file is broken — every component fell back to defaults",
        "action_id": "fix_config",
    },
    "network_error": {
        "plain_zh": "网络问题——稍后会自动重试",
        "plain_en": "Network trouble — it will retry automatically",
        "action_id": "retry",
    },
}

# --------------------------------------------------------------------------- #
# raw-text classifier — for claude CLI stdout/stderr, dispatch errors, log
# tails. Order matters: first match wins. Patterns are deliberately narrow
# (high precision); unknown text -> None -> the UI keeps the raw string.
# --------------------------------------------------------------------------- #
_RULES: list = [
    ("claude_cli_missing", re.compile(
        r"claude.{0,40}(command not found|no such file)|"
        r"(command not found|no such file or directory).{0,20}claude|"
        r"\[Errno 2\].*claude", re.IGNORECASE | re.DOTALL)),
    # version-mismatch signatures only: the exact flags/subcommands dispatch
    # relies on (--bg/--name/--resume, `claude agents`) rejected as unknown.
    # A generic "unknown option" must NOT match — could be the task's own text.
    ("claude_cli_outdated", re.compile(
        r"unknown option.{0,10}['\"]?--(bg|name|resume)\b|"
        r"unknown command.{0,10}['\"]?agents\b", re.IGNORECASE)),
    # Bun's catch-all for an UNMAPPED errno ("An unknown error occurred,
    # possibly due to low max file descriptors (Unexpected)"). It is NOT fd
    # exhaustion — Bun spells that out separately (ProcessFdQuotaExceeded /
    # SystemFdQuotaExceeded, below). On this product's launch path the errno
    # is TCC's EPERM on the task cwd (§55 第三幕), so the sentence says so.
    # Ranked before auth/network: the message carries no other signature,
    # and a card text would not plausibly contain it.
    ("claude_blind", re.compile(
        r"possibly due to low max file descriptors", re.IGNORECASE)),
    # genuine fd exhaustion: errno spellings + Bun's own quota messages.
    ("fd_limit", re.compile(
        r"\bEMFILE\b|\bENFILE\b|too many open files|"
        r"ran out of file descriptors|FdQuotaExceeded",
        re.IGNORECASE)),
    # claude's exact refusal (issue #89); narrow on purpose — a card that merely
    # talks about permissions or disclaimers must not classify.
    ("claude_bypass_disclaimer", re.compile(
        r"bypassPermissions requires accepting the disclaimer", re.IGNORECASE)),
    ("claude_auth_failed", re.compile(
        r"authentication_error|invalid (x-)?api[- _]?key|"
        r"\b401\b|OAuth token has expired|(?<![\w-])unauthorized|"
        r"please run /login|api key.{0,20}(invalid|expired|revoked)",
        re.IGNORECASE)),
    ("node_missing", re.compile(
        r"npx.{0,40}(command not found|no such file)|"
        r"(command not found|no such file or directory).{0,20}(npx|node)\b|"
        r"env: node: No such file", re.IGNORECASE)),
    ("network_error", re.compile(
        r"connection (refused|reset|timed? ?out)|network is (down|unreachable)|"
        r"getaddrinfo|ENOTFOUND|ETIMEDOUT|ECONNRE|temporary failure in name",
        re.IGNORECASE)),
    # npx cache-miss download banner (npm >= 7 prints the first line, the
    # interactive prompt the second). Ranked AFTER network_error on purpose:
    # a download that died on the network must not classify as "in progress".
    ("engine_npm_download", re.compile(
        r"package was not found and will be installed|"
        r"need to install the following packages?", re.IGNORECASE)),
]


# screenpipe's ffmpeg install-failure phrasing — ENGINE-LOG CONTEXT ONLY,
# deliberately NOT a classify() rule: dispatch/card text may legitimately say
# "failed to install ffmpeg-python" or discuss ffmpeg, and the recording
# catalog sentence would then mislabel an unrelated task failure. The colon
# pins the first signature to screenpipe's exact format ("failed to install
# ffmpeg: <os error>"). Checked directly (not via the rules chain) so an
# install error carrying network/401-flavored words still reads as the
# ffmpeg dependency — mirroring Swift diagnoseEngine's substring check.
_FFMPEG_INSTALL_FAILED = re.compile(
    r"failed to install ffmpeg:|"
    r"ffmpeg not found and installation failed", re.IGNORECASE)


def classify(raw: Optional[str]) -> Optional[str]:
    """Map raw error text to a failure id, or None when honestly unknown."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw)
    for fid, pattern in _RULES:
        if pattern.search(text):
            return fid
    return None


def _strip_app_markers(tail: str) -> str:
    """Drop the app's own breadcrumb lines ("[app …] spawn/autostart …") so a
    log that only contains our markers counts as empty, not as engine output."""
    return "\n".join(
        line for line in tail.splitlines() if not line.startswith("[app")
    ).strip()


def classify_engine_log(tail: Optional[str], npx_present: bool = True,
                        engine_alive: bool = False) -> Optional[str]:
    """Why is the recording engine down? (audit 2.3 — engine-death diagnosis)

    ``tail`` = the last lines of ``~/.screenpipe/engine.log`` (the engine's
    combined stdout/stderr). Returns a failure id, or None when the engine is
    alive and nothing in the log looks wrong (healthy — including "alive but
    quiet"; a locked screen legitimately goes silent, so silence alone is
    never classified as a failure).

    Mirrored in Swift by RecordingController.diagnoseEngine (Recording.swift);
    keep the two in sync when touching this.
    """
    if not npx_present:
        return "node_missing"
    text = _strip_app_markers(str(tail or ""))
    fid = classify(text)
    if fid == "node_missing":
        return fid
    if engine_alive:
        # while the npx process is alive, the download banner means exactly
        # that: first-run download in progress. (A DEAD process whose last
        # line is the banner is a failed download -> crashed, below.)
        return "engine_npm_download" if fid == "engine_npm_download" else None
    # dead on screenpipe's ffmpeg install failure -> the specific fix beats
    # the generic "crashed" (an ALIVE engine with stale ffmpeg lines in its
    # tail already returned healthy/downloading above — it found ffmpeg this
    # time). Direct substring check, see _FFMPEG_INSTALL_FAILED.
    if _FFMPEG_INSTALL_FAILED.search(text):
        return "engine_ffmpeg_missing"
    # dead with real output -> crashed (callers surface the tail verbatim);
    # dead with nothing but our own markers -> plain "not running".
    return "engine_crashed" if text else "engine_dead"


# --------------------------------------------------------------------------- #
# language + copy helpers (python side of the UI language setting, §15)
# --------------------------------------------------------------------------- #
def _persisted_language() -> Optional[str]:
    """The user's explicitly persisted language, or None when neither source
    ever set one. Reads the two sources directly (settings_overrides.json
    wins over config.yaml — load_config precedence) because Config.language's
    dataclass default "zh" is a placeholder, not a user choice, and going
    through load_config() cannot tell the two apart. Values normalize with
    the historical rule: "en" → en, any other non-empty value → zh."""
    from act.lib import config
    try:
        data = json.loads(
            config.SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
        v = str(data.get("language") or "").strip().lower()
        if v:
            return "en" if v == "en" else "zh"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        if config.yaml is not None:
            data = config.yaml.safe_load(
                config.CONFIG_PATH.read_text(encoding="utf-8"))
            v = ""
            if isinstance(data, dict):
                v = str(data.get("language") or "").strip().lower()
            if v:
                return "en" if v == "en" else "zh"
    except (OSError, ValueError):
        pass
    return None


def ui_lang() -> str:
    """The UI language ("zh" | "en"), resolved in order (§15; v0.42):

    1. ``AIASSISTANT_UI_LANG`` env var — the Mac app passes its EFFECTIVE
       display language when spawning python whose output the user reads
       (doctor --json, ask, wizard, settings helpers, …), so app-spawned
       copy matches the app exactly;
    2. the user's persisted setting (settings_overrides.json ``language``,
       else config.yaml ``language``);
    3. the system locale (LC_ALL/LANG: zh* → zh, else en) — mirroring the
       Swift first-run default instead of the old hardcoded "zh", so an
       en-locale user with no persisted override no longer gets zh
       unclassified doctor rows interleaved with en classified ones.
    """
    try:
        env = os.environ.get("AIASSISTANT_UI_LANG", "").strip().lower()
        if env in ("zh", "en"):
            return env
        persisted = _persisted_language()
        if persisted:
            return persisted
        loc = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
        return "zh" if loc.startswith("zh") else "en"
    except Exception:  # noqa: BLE001 - copy helpers must never raise
        return "zh"


def pick(zh: str, en: str, lang: Optional[str] = None) -> str:
    """Python-side L(): choose the string for the current UI language."""
    return en if (lang or ui_lang()) == "en" else zh


def describe(failure_id: Optional[str]) -> Optional[dict]:
    """The catalog entry for an id (None for unknown ids — no KeyError)."""
    if not failure_id:
        return None
    return FAILURES.get(str(failure_id))


def user_message(failure_id: Optional[str], lang: Optional[str] = None) -> Optional[str]:
    """Plain-language sentence for a failure id in the current UI language."""
    entry = describe(failure_id)
    if not entry:
        return None
    key = "plain_en" if (lang or ui_lang()) == "en" else "plain_zh"
    return entry[key]


def action_id(failure_id: Optional[str]) -> Optional[str]:
    entry = describe(failure_id)
    return entry["action_id"] if entry else None
