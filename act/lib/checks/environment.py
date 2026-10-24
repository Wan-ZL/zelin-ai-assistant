"""doctor 探针家族：工具链 / 配置 / 凭证 / 目录（CONTRACT §25；§19 凭证顺序；
§18 cron 之外的 macOS 录制依赖）。

行：``AIASSISTANT_HOME`` / ``version`` / ``claude CLI`` / ``stable claude`` +
``daemon claude``（§55 第五幕；判决逻辑在 act/lib/claude_bin.py；daemon = 唯一的
server LaunchAgent）/ ``daemon python`` / ``config.yaml`` / ``anthropic key``（§78：
``--bare`` 只认 API key，缺 key = FAIL）/ ``state dirs`` / ``obsidian vault`` /
``screenpipe db`` / ``node/npx``。默认探针实现 ``installed_daemon_path_env`` /
``login_shell_claude`` 也住这里（Probes 的缺省值）。

v2.0.0：``power probe``（§71.1）与 ``gh CLI`` 行随 Act 管线退役；花钱的 ``claude auth``
活探针搬进 act/doctor.py（它要走 act/llm.py 的隔离 argv / env，lib 层不可达）。
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Callable, Optional

from act.lib import claude_bin, config, platform, secrets
from act.lib import version as version_lib
from act.lib.checks.core import (FAIL, OK, SERVER_LABEL, WARN, CheckResult,
                                 installer, pick, row_from, run)

MIN_PYTHON = (3, 9)
# the export cron fires every 30 min while recording; 2h with no db write
# means the capture engine is stopped.
SCREENPIPE_STALE_SECONDS = 2 * 3600

_PLIST_PATH_RE = re.compile(r"<key>PATH</key>\s*<string>([^<]+)</string>")


# --------------------------------------------------------------------------- #
# Default probe implementations (Probes.daemon_path_env / login_shell_claude)
# --------------------------------------------------------------------------- #
def installed_daemon_path_env() -> Optional[str]:
    """The PATH the resident server agent actually runs with — read from the
    INSTALLED plist (~/Library/LaunchAgents/<server label>.plist ``<key>PATH</key>``),
    not the repo template: what the installer rendered is what launchd exports."""
    if not platform.is_darwin():
        return None
    plist = Path.home() / "Library" / "LaunchAgents" / (SERVER_LABEL + ".plist")
    try:
        text = plist.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _PLIST_PATH_RE.search(text)
    return m.group(1) if m else None


installed_actd_path_env = installed_daemon_path_env   # v1 name


def login_shell_claude(runner: Callable = run) -> Optional[str]:
    """The claude the USER'S login shell resolves (same probe install.sh uses).
    None when the shell probe fails or finds nothing."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    rc, out = runner([shell, "-lc", "command -v claude"], timeout=15)
    if rc != 0 or not out.strip():
        return None
    last = out.strip().splitlines()[-1].strip()
    return last if last.startswith("/") else None


def resolve_key(probes) -> "tuple[Optional[str], str]":
    """Anthropic key content per CONTRACT §19 order, plus its source label.

    Goes through act/lib/secrets so the first-token-line semantics match every
    runtime consumer exactly — a whole-file read of a multiline key file used
    to make the live probe FAIL on a key that works everywhere else.
    """
    val = secrets.read_secret(secrets.ANTHROPIC_API_KEY_FILE)
    if val:
        return val, "config/secrets/anthropic-api-key.txt"
    val = secrets.read_path(probes.legacy_key_path)
    if val:
        return val, str(probes.legacy_key_path)
    return None, ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_home(probes):
    if not (config.HOME / "install.sh").exists():
        return CheckResult(
            "AIASSISTANT_HOME", FAIL,
            pick("%s 不像是仓库目录（没有 install.sh）——下面所有路径都由它推导",
                 "%s does not look like the repo (no install.sh) - every path below derives from it") % config.HOME,
            pick("export AIASSISTANT_HOME=<你的 clone>，或运行 bash <你的 clone>/install.sh（会写入 home 指针）",
                 "export AIASSISTANT_HOME=<your clone>, or run bash <your clone>/install.sh (writes the home pointer)"))
    return CheckResult("AIASSISTANT_HOME", OK, str(config.HOME))


def check_version(probes):
    return [CheckResult(*row) for row in version_lib.doctor_rows(probes.version_status())]


def check_claude(probes):
    path = probes.which("claude")
    if not path:
        return CheckResult(
            "claude CLI", FAIL,
            "not on PATH - meeting recaps and note processing cannot run",
            "install Claude Code (https://claude.com/claude-code), then re-run this check",
        ).with_failure("claude_cli_missing")
    rc, out = probes.run([path, "--version"], timeout=15)
    if rc != 0:
        return CheckResult(
            "claude CLI", WARN,
            pick("%s 存在但 `claude --version` 失败（%s）",
                 "%s exists but `claude --version` failed (%s)") % (path, out.strip()[:80]),
            pick("重装 Claude Code", "reinstall Claude Code"))
    version = out.strip().splitlines()[0][:60] if out.strip() else "unknown version"
    return CheckResult("claude CLI", OK, "%s (%s)" % (path, version))


# -- stable / daemon claude (§55 第五幕; verdict logic in act/lib/claude_bin.py) --- #
def check_stable_claude(probes):
    """§55 第五幕 — row `stable claude` (act/lib/claude_bin.py stable_claude_row):
    the daemon copy install.sh maintains at a fixed $HOME path, the one path
    the owner's Full Disk Access grant is attached to. macOS only (elsewhere
    no TCC, install.sh does not maintain it) and only when a claude exists to
    copy (`claude CLI` row already FAILs otherwise)."""
    if not platform.is_darwin() or not probes.which("claude"):
        return []
    return row_from(claude_bin.stable_claude_row(
        probes.run, probes.login_shell_claude, probes.which, installer()), claude_bin.STABLE_ROW)


def check_daemon_claude(probes):
    """Row `daemon claude` (act/lib/claude_bin.py daemon_claude_row): the
    2026-07-08 two-installs incident — launchd's PATH resolved an outdated
    claude (no --bg) while the login shell used the new one. §55 第五幕: with
    the stable daemon copy present that file is what every site launches, so
    the row names it and only the --bg probe applies."""
    return row_from(claude_bin.daemon_claude_row(
        probes.run, probes.daemon_path_env, probes.login_shell_claude, installer()),
        claude_bin.DAEMON_ROW)


# -- daemon python ------------------------------------------------------------ #
def _pinned_python_row() -> "tuple[str, Optional[CheckResult]]":
    """(pinned python, row): row is the WARN/FAIL when there is no usable pin."""
    rj = config.HOME / "config" / "runtime.json"
    if not rj.exists():
        return "", CheckResult(
            "daemon python", WARN,
            pick("config/runtime.json 缺失——server agent 和 App 只能靠猜解释器",
                 "config/runtime.json missing - the server agent and the app guess at an interpreter"),
            pick("bash install.sh（重新探测并固定解释器）",
                 "bash install.sh (re-detects and pins the interpreter)"))
    py = _runtime_pin(rj)
    if not py or not os.access(py, os.X_OK):
        return py, CheckResult(
            "daemon python", FAIL,
            pick("config/runtime.json 指向一个不可执行的 python（%s）",
                 "config/runtime.json points at a non-executable python (%s)") % (py or "empty"),
            pick("bash install.sh（重新探测解释器）",
                 "bash install.sh (re-detects the interpreter)"))
    return py, None


def _runtime_pin(rj: Path) -> str:
    """``python`` in config/runtime.json; "" when the file is malformed."""
    try:
        return str(json.loads(rj.read_text(encoding="utf-8")).get("python") or "")
    except Exception:  # noqa: BLE001 - malformed file is just another symptom
        return ""


def _too_old(ver: str) -> bool:
    """``ver`` ("3.8") below MIN_PYTHON; unparsable versions are not judged."""
    try:
        return tuple(int(x) for x in ver.split(".")) < MIN_PYTHON
    except ValueError:
        return False


def check_runtime_python(probes):
    py, row = _pinned_python_row()
    if row is not None:
        return row
    rc, out = probes.run(
        [py, "-c", "import sys, yaml; print('%d.%d' % sys.version_info[:2])"],
        timeout=20)
    if rc != 0:
        return CheckResult(
            "daemon python", FAIL,
            pick("%s 无法 `import yaml`——server 在 launchd 下会立即退出",
                 "%s cannot `import yaml` - the server exits immediately under launchd") % py,
            "%s -m pip install --user pyyaml   (PEP 668 python: add --break-system-packages)" % py)
    ver = out.strip().splitlines()[-1] if out.strip() else ""
    if _too_old(ver):
        return CheckResult(
            "daemon python", FAIL,
            pick("%s 是 Python %s（需要 >= %s）",
                 "%s is Python %s (need >= %s)") % (py, ver, ".".join(map(str, MIN_PYTHON))),
            "AIASSISTANT_PYTHON=<newer python3> bash install.sh")
    return CheckResult("daemon python", OK,
                       "%s (Python %s, PyYAML importable)" % (py, ver))


# -- config / key / dirs ------------------------------------------------------ #
def check_config(probes):
    if not config.CONFIG_PATH.exists():
        return CheckResult(
            "config.yaml", WARN,
            pick("缺失——正在用 config.example.yaml 的默认值运行（默认 vault 路径）",
                 "missing - running on config.example.yaml defaults (default vault path)"),
            "cp config.example.yaml config.yaml && edit sources.*")
    if config.yaml is None:
        return CheckResult(
            "config.yaml", FAIL,
            pick("这个 python 缺 PyYAML——config 无法解析",
                 "PyYAML missing for this python - config cannot be parsed"),
            "%s -m pip install --user pyyaml" % sys.executable)
    try:
        config.yaml.safe_load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        first = str(exc).splitlines()[0][:80]
        return CheckResult(
            "config.yaml", FAIL,
            "invalid YAML (%s) - every component silently falls back to defaults" % first,
            "fix the syntax; verify: python3 -c \"import yaml; yaml.safe_load(open('config.yaml'))\"",
        ).with_failure("config_invalid")
    return CheckResult("config.yaml", OK, str(config.CONFIG_PATH))


def _secrets_key_row(sec: Path) -> CheckResult:
    """The key lives in config/secrets/: OK unless other users can read it."""
    mode = stat.S_IMODE(sec.stat().st_mode)
    if mode & 0o077:
        return CheckResult(
            "anthropic key", WARN,
            pick("config/secrets/anthropic-api-key.txt 其他用户也能读（mode %o）",
                 "config/secrets/anthropic-api-key.txt is readable by other users (mode %o)") % mode,
            "chmod 600 '%s'" % sec)
    return CheckResult("anthropic key", OK,
                       "config/secrets/anthropic-api-key.txt (0600)")


def check_anthropic_key(probes):
    sec = secrets.SECRETS_DIR / secrets.ANTHROPIC_API_KEY_FILE
    key, source = resolve_key(probes)
    if key and source.startswith("config/secrets"):
        return _secrets_key_row(sec)
    if key:
        return CheckResult("anthropic key", OK, "%s (§19 second tier)" % source)
    return CheckResult(
        "anthropic key", FAIL,
        pick("没有 Anthropic API key——隔离运行的 headless claude（§78，--bare）只认 API key，"
             "不读 Claude Code 登录或钥匙串，所以会议纪要和笔记整理都会认证失败",
             "no Anthropic API key - the isolated headless claude (§78, --bare) accepts an API "
             "key only, never the Claude Code login or the keychain, so recaps and note "
             "processing will fail to authenticate"),
        pick("在设置页「凭证」粘贴你的 API key（写入 config/secrets/anthropic-api-key.txt）",
             "paste your API key in Settings > Credentials (writes config/secrets/anthropic-api-key.txt)"),
    ).with_failure("claude_auth_failed")


def check_state_dirs(probes):
    dirs = (config.STATE_DIR, config.LOG_DIR)
    missing = [d for d in dirs if not d.is_dir()]
    if missing:
        return CheckResult(
            "state dirs", FAIL,
            pick("缺失：%s——recap / 设置无法持久化任何东西",
                 "missing: %s - recaps / settings cannot persist anything") % ", ".join(map(str, missing)),
            pick("bash install.sh（创建 state/）",
                 "bash install.sh (creates state/)"))
    blocked = [d for d in dirs if not os.access(d, os.W_OK)]
    if blocked:
        return CheckResult(
            "state dirs", FAIL,
            pick("不可写：%s", "not writable: %s") % ", ".join(map(str, blocked)),
            "chown -R $(whoami) '%s'" % config.STATE_DIR)
    return CheckResult("state dirs", OK, "%s writable" % config.STATE_DIR)


# -- optional dependencies ---------------------------------------------------- #
def check_obsidian(probes):
    cfg = config.load_config()
    raw = cfg.obsidian_raw
    if not (raw and str(raw).strip()):
        return CheckResult(
            "obsidian vault", WARN,
            pick("sources.obsidian_raw 未配置——正在用默认 vault 路径",
                 "sources.obsidian_raw not set - the default vault path is in use"),
            pick("在 config.yaml 的 sources.obsidian_raw 填上 vault 的 raw 笔记目录",
                 "set sources.obsidian_raw in config.yaml to your vault's raw-notes folder"))
    raw_path = Path(str(raw)).expanduser()
    if not raw_path.is_dir():
        return CheckResult(
            "obsidian vault", WARN,
            pick("sources.obsidian_raw 不存在（%s）——ingest 的笔记没有落脚点，而且是静默的",
                 "sources.obsidian_raw does not exist (%s) - ingested notes have nowhere to land, silently") % raw_path,
            pick("创建该目录，或改 config.yaml 里的路径",
                 "create the folder or fix the path in config.yaml"))
    unprocessed = Path(str(cfg.obsidian_unprocessed)).expanduser()
    if not unprocessed.is_dir():
        return CheckResult(
            "obsidian vault", WARN,
            pick("ingest 收件目录缺失（%s）——导出的笔记没有落脚点",
                 "ingest inbox missing (%s) - exports have nowhere to land") % unprocessed,
            "mkdir -p '%s'" % unprocessed)
    return CheckResult("obsidian vault", OK, "%s (+ ingest inbox)" % raw_path)


def check_screenpipe(probes):
    db = probes.screenpipe_db
    if not db.exists():
        return CheckResult(
            "screenpipe db", WARN,
            pick("%s 缺失——录制从未运行过（如果你本来就不开录制，这是正常的）",
                 "%s missing - recording has never run (fine if you keep recording off)") % db,
            pick("App -> 打开录制（引擎经 npx 运行）",
                 "the app -> enable recording (the engine runs via npx)"))
    age = probes.now() - db.stat().st_mtime
    if age > SCREENPIPE_STALE_SECONDS:
        return CheckResult(
            "screenpipe db", WARN,
            "last write %dh ago - the capture engine looks stopped" % int(age // 3600),
            "the app -> recording toggle (needs node/npx)",
        ).with_failure("engine_dead")
    return CheckResult("screenpipe db", OK,
                       "recording data fresh (last write %d min ago)" % int(age // 60))


def check_npx(probes):
    path = probes.which("npx")
    if not path:
        return CheckResult(
            "node/npx", WARN,
            "missing - the recording engine (`npx screenpipe`) cannot start",
            "brew install node",
        ).with_failure("node_missing")
    return CheckResult("node/npx", OK, path)
