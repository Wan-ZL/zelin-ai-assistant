"""版本解析与盖章（CONTRACT §56.1；宪法第 8 条 2026-09 修宪）。

版本的唯一真源是 main 上的 git tag ``vX.Y.Z``——没有任何被 PR 编辑的文件承载
版本号（六个并行 PR 为同一个 patch 号互相 rebase 的那一夜之后的结论）。
运行时 ``act.__version__`` 按以下顺序解析，第一个答得上来的赢：

1. ``act/_version.py``——生成文件（git-ignored），install.sh / mac/build.sh /
   shell/build.sh / release 打包 / ``scripts/version_stamp.py --write`` 写。
   守护进程只读它、永不 spawn git（launchd 下 git 是另一个二进制，TCC 按
   二进制授权——外置卷上的 checkout 会让它读不到 .git）。
2. checkout 的 git tag（``git describe --tags --long``）：HEAD 恰在 tag 上 =
   ``X.Y.Z``；领先 N 个 commit = ``X.Y.Z+N``（semver build metadata，
   ``update_check.parse_version`` 比较时忽略）。
3. 烘焙回落常量——act/__init__.py 里那一行（tarball、无 git、浅 clone 用）。
   **过渡条款**：回落常量比 checkout 最近的 tag 还新（= 手工声明的「待发
   版本」），HEAD 又不在 tag 上时，以回落常量为准——旧版部署脚本用 sed 读
   那一行当期望版本、新 actd 的心跳必须与之逐字相等，否则第一次部署本
   改动的那一轮会被误判回滚（§56.1 过渡条款）。新常态下常量 ≤ 最新 tag，
   本条永不触发。

同一个模块也给 release-on-merge 算下一个 tag（``next_tag``）、给 CI 的
pins 检查解析 tag 名（``parse_tag``）。stdlib only（宪法第 7 条）。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

PKG_DIR = Path(__file__).resolve().parents[1]          # …/act
REPO_ROOT = PKG_DIR.parent
STAMP_PATH = PKG_DIR / "_version.py"
INIT_PATH = PKG_DIR / "__init__.py"

# iOS 的两处 MARKETING_VERSION pin（ios/project.yml + pbxproj 两行）：提交的
# 永远是这个中性占位，构建前由 scripts/version_stamp.py --ios 在 runner 上
# sed 成真版本、绝不提交（CI「Version pins untouched」把关）。
PIN_PLACEHOLDER = "0.0.0-dev"
PIN_FILES = ("ios/project.yml", "ios/ZelinAIAssistant.xcodeproj/project.pbxproj")

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_DESCRIBE_RE = re.compile(r"^(v?\d+\.\d+\.\d+)-(\d+)-g[0-9a-fA-F]+$")
_VERSION_LINE_RE = re.compile(r'^__version__ = "([^"]*)"', re.M)
_PR_SUBJECT_RE = re.compile(r"\(#(\d+)\)\s*$")
_PR_MERGE_RE = re.compile(r"^Merge pull request #(\d+)\b")

BUMPS = ("patch", "minor", "major")
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def parse_tag(name: str) -> Optional[Tuple[int, int, int]]:
    """``v0.48.15`` / ``0.48.15`` → (0, 48, 15)；别的形状 → None。"""
    m = _TAG_RE.match((name or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def format_version(parts: Tuple[int, int, int]) -> str:
    return "%d.%d.%d" % parts


def _read_version_line(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _VERSION_LINE_RE.search(text)
    return m.group(1).strip() if m and m.group(1).strip() else None


def read_stamp(path: Optional[Path] = None) -> Optional[str]:
    """act/_version.py 的版本；文件不在 / 读不了 / 形状不对 → None。"""
    return _read_version_line(path or STAMP_PATH)


def read_fallback(path: Optional[Path] = None) -> Optional[str]:
    """act/__init__.py 里烘焙的回落常量（``__version__ = "…"`` 那一行）。"""
    return _read_version_line(path or INIT_PATH)


def _run_describe(root: str, runner: Runner) -> Optional[str]:
    """`git describe` 的 stdout；rc≠0 / spawn 失败 / 超时 → None。"""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        proc = runner(["git", "-C", root, "describe", "--tags", "--long",
                       "--match", "v[0-9]*"],
                      capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def parse_describe(text: Optional[str]) -> Optional[Tuple[str, int]]:
    """``v0.48.16-3-gabcdef0`` → ("0.48.16", 3)；别的形状 → None。"""
    m = _DESCRIBE_RE.match((text or "").strip())
    parts = parse_tag(m.group(1)) if m else None
    if parts is None:
        return None
    return format_version(parts), int(m.group(2))


def git_describe(repo_root: Optional[Path] = None,
                 run: Optional[Runner] = None) -> Optional[Tuple[str, int]]:
    """(最近的 vX.Y.Z tag 版本, 领先的 commit 数)；不是 git checkout / 没有
    tag / git 不在 / 出错 → None。永不抛。"""
    return parse_describe(_run_describe(str(repo_root or REPO_ROOT), run or subprocess.run))


def from_describe(described: Tuple[str, int], fallback: Optional[str]) -> str:
    """describe 结果 → 版本串。恰在 tag 上 = tag；领先 N = ``tag+N``，除非
    回落常量比 tag 新（过渡条款，见模块 docstring）。"""
    tag, ahead = described
    if ahead == 0:
        return tag
    fb = parse_tag(fallback or "")
    if fb is not None and fb > (parse_tag(tag) or (0, 0, 0)):
        return format_version(fb)
    return "%s+%d" % (tag, ahead)


def compute(fallback: Optional[str], repo_root: Optional[Path] = None,
            run: Optional[Runner] = None) -> Tuple[str, str]:
    """这个 checkout **应该**带的版本（不看已有的 stamp）：(版本, 来源)，
    来源 ∈ git | fallback。"""
    described = git_describe(repo_root, run)
    if described is not None:
        return from_describe(described, fallback), "git"
    return (fallback or "0.0.0"), "fallback"


def resolve(fallback: Optional[str], stamp_path: Optional[Path] = None,
            repo_root: Optional[Path] = None, run: Optional[Runner] = None) -> str:
    """运行时 ``act.__version__``：stamp → git → 回落常量。永不抛。"""
    try:
        stamped = read_stamp(stamp_path)
        if stamped:
            return stamped
        return compute(fallback, repo_root, run)[0]
    except Exception:  # noqa: BLE001 - 版本是装饰，绝不让 import act 崩
        return fallback or "0.0.0"


def stamp_text(version: str) -> str:
    return ('"""Generated by scripts/version_stamp.py — do not edit, git-ignored '
            '(CONTRACT §56.1: version truth = git tag)."""\n'
            '__version__ = "%s"\n' % version)


def write_stamp(version: str, path: Optional[Path] = None) -> Path:
    """原子写 act/_version.py（tmp + rename，0644）。调用方决定内容；这里不判断。
    mkstemp 默认 0600——.pkg 把 payload 装成 root-owned 后 postinstall 以登录用户
    rsync，0600 的 stamp 会让整个同步失败（Codex review #142 P1）。"""
    target = Path(path or STAMP_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="._version-", suffix=".py", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(stamp_text(version))
        os.chmod(tmp, 0o644)
        os.replace(tmp, str(target))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def stamp_decision(fallback: Optional[str], stamp_path: Optional[Path] = None,
                   repo_root: Optional[Path] = None,
                   run: Optional[Runner] = None) -> Tuple[str, str]:
    """install.sh / 构建脚本盖章时写什么：(版本, 来源)。git 答得上 → 算出来的；
    git 答不上（.pkg 副本、tarball）但已有 stamp → **保留**它（打包时盖的是真
    tag，别用回落常量盖掉）；两者皆无 → 回落常量。来源 ∈ git | stamp | fallback。"""
    computed, source = compute(fallback, repo_root, run)
    if source == "git":
        return computed, source
    existing = read_stamp(stamp_path)
    if existing:
        return existing, "stamp"
    return computed, "fallback"


def status(fallback: Optional[str], stamp_path: Optional[Path] = None,
           repo_root: Optional[Path] = None, run: Optional[Runner] = None) -> dict:
    """doctor 探针的原料（§25 ``version`` 行）：stamp 值、checkout 算出的值、
    git 是否答得上。永不抛。"""
    described = git_describe(repo_root, run)
    return {
        "stamp": read_stamp(stamp_path),
        "git": described is not None,
        "computed": from_describe(described, fallback) if described else (fallback or ""),
        "fallback": fallback or "",
    }


# --------------------------------------------------------------------------- #
# release-on-merge：下一个 tag（纯函数，判例 tests/test_version_tags.py）
# --------------------------------------------------------------------------- #

def highest_tag(tags: Iterable[str]) -> Optional[Tuple[int, int, int]]:
    parsed = [p for p in (parse_tag(t) for t in tags) if p is not None]
    return max(parsed) if parsed else None


def bump(parts: Optional[Tuple[int, int, int]], kind: str = "patch") -> Tuple[int, int, int]:
    """(0,48,15) + patch → (0,48,16)；minor → (0,49,0)；major → (1,0,0)。
    没有任何 tag：patch → 0.0.1、minor → 0.1.0、major → 1.0.0。未知 kind = patch。"""
    major, minor, patch = parts or (0, 0, 0)
    if kind == "major":
        return major + 1, 0, 0
    if kind == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def next_tag(tags: Iterable[str], kind: str = "patch") -> str:
    """现有 tag 全集 + bump 种类 → ``vX.Y.Z``（永不与现有 tag 重名：最高号之上）。"""
    return "v" + format_version(bump(highest_tag(tags), kind))


def previous_tag(tags: Iterable[str], current: str) -> Optional[str]:
    """比 ``current`` 低的最高 tag（release notes 的比较基线）；没有 → None。"""
    cur = parse_tag(current)
    if cur is None:
        return None
    lower = [p for p in (parse_tag(t) for t in tags) if p is not None and p < cur]
    return ("v" + format_version(max(lower))) if lower else None


def bump_from_labels(labels: Iterable[str]) -> str:
    """PR label ``release: major`` > ``release: minor`` > 默认 patch（空格/大小写
    宽松：``release:minor`` 也认）。"""
    found = {re.sub(r"\s+", "", (label or "").lower()) for label in labels}
    if "release:major" in found:
        return "major"
    if "release:minor" in found:
        return "minor"
    return "patch"


def pr_number_from_subject(subject: str) -> Optional[int]:
    """被合并的 PR 号：squash 首行 ``… (#141)`` → 141；merge commit（merge queue 的
    MERGE 方法也是这个形状）首行 ``Merge pull request #141 from …`` → 141。没有 → None。"""
    first = (subject or "").strip().splitlines()[0] if (subject or "").strip() else ""
    m = _PR_MERGE_RE.match(first) or _PR_SUBJECT_RE.search(first)
    return int(m.group(1)) if m else None
