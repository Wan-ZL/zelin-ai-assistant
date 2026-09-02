#!/usr/bin/env python3
"""test-code skill 共用件：子进程 runner 注入缝、shrink-only 账本、TOML 子集、
小 IO 工具。detect.py / checks.py / run_ladder.py / complexity_min.py 都从这里取。

法典指针：docs/CONTRACT.md §58.4（账本 new/worse/stale 语义——只借语义，不 import
本 repo 的 scripts/qa/qa_common.py：skill 必须在任何项目里独立运行）；设计 =
docs/design/vnext2-plan.md R2.8 / D14。stdlib only、py3.9 floor、零网络。

runner 契约（唯一的子进程出口，测试注入 fake）：
    runner(argv, cwd=None, timeout=None, env=None) -> RunResult
    rc=-1 + timed_out=True 表示超时被杀（整个进程组）；rc=-2 表示根本没起来
    （可执行文件缺失等）。fail-closed：调用方把两者都当失败，不当跳过。
判例：tests/test_skill_test_code_common.py；真子进程判例住
tests/integration/test_skill_test_code_runner.py（单文件时间预算）。
"""

import datetime
import json
import os
import signal
import subprocess
import time

SKILL_NAME = "test-code"
SKILL_VERSION = "0.2.0"
REPORT_SCHEMA = 1

# 遍历项目文件时永远跳过的目录（依赖/构建产物/本 skill 自己的输出）。
SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".build", ".qa", ".qa-report", ".test-code", "coverage", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", ".hypothesis", "DerivedData", "target",
})
TEXT_CAP_BYTES = 1024 * 1024


class RunResult(object):
    """一次子进程的结果（普通对象而非 tuple：字段 add-only 更顺手）。"""

    def __init__(self, rc, stdout="", stderr="", timed_out=False, duration=0.0):
        self.rc = rc
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.timed_out = timed_out
        self.duration = duration

    @property
    def ok(self):
        return self.rc == 0 and not self.timed_out

    def text(self):
        return self.stdout + ("\n" + self.stderr if self.stderr else "")


def _kill_group(proc):
    """超时：杀整个进程组（bash → python 这种祖孙链，只杀儿子会让管道挂住）。"""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        proc.kill()


def run_command(argv, cwd=None, timeout=None, env=None):
    """默认 runner。永不抛：起不来 rc=-2、超时 rc=-1（调用方 fail-closed）。"""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, errors="replace", start_new_session=True)
    except (OSError, ValueError) as exc:
        return RunResult(-2, "", "%s: %s" % (type(exc).__name__, exc), False,
                         time.monotonic() - started)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        return RunResult(-1, out, err, True, time.monotonic() - started)
    return RunResult(proc.returncode, out, err, False, time.monotonic() - started)


# --------------------------------------------------------------------------- #
# git 小工具（都经 runner）
# --------------------------------------------------------------------------- #

def git_lines(runner, repo, args, timeout=60):
    """`git <args>` 的 stdout 行；失败 = None（调用方决定 fail-closed 还是降级）。"""
    res = runner(["git"] + list(args), cwd=repo, timeout=timeout)
    if not res.ok:
        return None
    return [line for line in res.stdout.splitlines() if line.strip()]


def resolve_base(runner, repo, requested=None):
    """diff 基线：用户指定 > origin/main > main > master；都没有 = None（无 diff）。"""
    candidates = [requested] if requested else ["origin/main", "main", "master"]
    for ref in candidates:
        res = runner(["git", "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                     cwd=repo, timeout=30)
        if res.ok:
            return ref
    return None


# --------------------------------------------------------------------------- #
# shrink-only 账本（语义同 §58.4：new/worse 判红，stale 只提示）
# --------------------------------------------------------------------------- #

def ledger_line(line):
    """一行 → (key, value) 或 None。`<key> [<value>]`；# 注释（行首或空白后）。"""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split(" #", 1)[0].split()
    value = float(parts[1]) if len(parts) > 1 else 1.0
    return parts[0], value


def load_ledger(path):
    """账本文件 → {key: value}；文件缺席 = 空账（一切违例都算新）。"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        pairs = [ledger_line(line) for line in fh]
    return dict(pair for pair in pairs if pair)


def format_value(value):
    if float(value).is_integer():
        return str(int(value))
    return "%.1f" % value


def write_ledger(path, entries, header):
    """重铸账本：标题注释 + 排序条目（--init-baselines 的唯一写点）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = ["# %s" % header,
             "# shrink-only: NEW / WORSE fail, STALE is advisory — strike fixed lines."]
    lines += ["%s %s" % (key, format_value(entries[key])) for key in sorted(entries)]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(entries)


def _worse(violations, ledger):
    listed = set(violations) & set(ledger)
    return sorted(k for k in listed if violations[k] > ledger[k])


def compare_ledger(violations, ledger):
    """{new, worse, stale, ok}。ok = 无 new 且无 worse（stale 只提示划账）。"""
    new = sorted(k for k in violations if k not in ledger)
    stale = sorted(k for k in ledger if k not in violations)
    worse = _worse(violations, ledger)
    return {"new": new, "worse": worse, "stale": stale, "ok": not (new or worse)}


# --------------------------------------------------------------------------- #
# TOML 子集（[section] + key = int/float/bool/"str"）——读 qa/gates.toml 之类
# --------------------------------------------------------------------------- #

def _toml_scalar(raw):
    text = raw.strip()
    if text.startswith('"'):
        return text[1:text.find('"', 1)]
    text = text.split("#", 1)[0].strip()
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        return float(text)


def _toml_line(line, sections, current):
    """一行 → 行后所处的 section；坏行抛 ValueError。"""
    if line.startswith("["):
        return sections.setdefault(line.strip("[]").strip(), {})
    key, sep, value = line.partition("=")
    if not sep or current is None:
        raise ValueError("unparseable toml line: %r" % line)
    current[key.strip()] = _toml_scalar(value)
    return current


def parse_toml_subset(text):
    """够读阈值文件的最小解析；不认识的行抛 ValueError（阈值坏了必须停，不猜）。"""
    sections, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            current = _toml_line(line, sections, current)
    return sections


# --------------------------------------------------------------------------- #
# 文件/JSON 小工具
# --------------------------------------------------------------------------- #

def read_text(path, cap=TEXT_CAP_BYTES):
    """文本文件 → str；超帽/二进制（前 8KB 含 NUL）→ None；读不到 → 抛 OSError。"""
    if os.path.getsize(path) > cap:
        return None
    with open(path, "rb") as fh:
        data = fh.read()
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def read_text_or_empty(path):
    """探测用的宽松读法：缺席/二进制/读不到 → ""（探测器不 fail closed，检查器才 fail closed）。"""
    try:
        return read_text(path) or ""
    except OSError:
        return ""


def keep_dir(name):
    return not name.startswith(".") and name not in SKIP_DIRS


def walk_files(root):
    """root 下全部文件的相对路径（跳过 SKIP_DIRS；确定性排序）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if keep_dir(d))
        for fn in sorted(filenames):
            yield os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
