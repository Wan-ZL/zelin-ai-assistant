"""worktrees — `.claude/worktrees/` 的清点与回收（CONTRACT §75；§65.3 / §65.5 追记；§70.1 的第三个维护阶段；issue #315）。

`claude --bg` 每派一个会话就在 `<repo>/.claude/worktrees/<name>/` 隔离出一份完整
checkout（跑过前端的还带 `web/node_modules`），而此前**没有任何一处代码删过它们**：
owner 的生产 checkout 2026-09-09 攒到 30 个、写这条法时 190+，`git status` /
`git gc` 越来越慢，`git branch -vv` 里满是死分支。本模块是那把扫帚，三条腿：

- **清点**（:func:`inventory`）：`git worktree list --porcelain` + 每条的年龄 / 锁 /
  是否属于在飞的卡；占用 = 对**托管根目录**跑一次 `du -sk`（不是每条一次），量不到
  就报 null 而不是 0（§0 第 3 条）。
- **扫**（:func:`sweep`）：判决见下；`git worktree prune` 在前、`git worktree remove`
  在后，**永不 `--force`**、**永不 `git branch -D`**。
- **结算即释放**（:func:`release`）：§65.5 的 PR 合并 / 关闭落账后顺手删掉那张卡自己的
  worktree 与本地分支（best-effort，失败只记日志）。

**判决**（truth = 本模块常量）。硬边界：路径必须在 `<repo>/.claude/worktrees/` 之内、
永不碰主工作树。守卫（命中即留下，reason 逐字进回执）：`main` / `unmanaged` /
`missing` / `locked`（`git worktree list` 报 locked）/ `live`（路径 = 某张
approved·executing·review 卡的会话 cwd，`transcripts.transcript_info`）/ `dirty`
（`git status --porcelain` 非空，读不到也算脏）/ `unpushed`（`git rev-list --count
HEAD --not --remotes` > 0 = 有只存在于本地的提交，**期限内**才留，见下）。够格删的三个
理由：`merged`（分支已并进远端默认分支）/ `gone`（分支在 origin 上已不存在 = 合并后
删枝）/ `stale`（目录 :data:`STALE_DAYS` 天没动过）；三者都另加 :data:`MIN_AGE_DAYS`
天的地板——刚建出来、还没提交过东西的 worktree 在「分支不在 origin 上」这一条上恒真，
没有地板就会误删正在起跑的会话。

**`unpushed` 是延期不是否决**：有只存在于本地的提交时，目录的年龄门槛从
:data:`MIN_AGE_DAYS` 抬到 :data:`STALE_DAYS`（owner 那条「14 天且无未提交改动」），过了
这道线照删，但**那条分支连问都不问**（回执 `kept_branch: "unpushed"`、`branch_deleted:
false`）。删目录从来丢不了提交——分支引用住在主 repo 的 `.git` 里，`worktree remove`
不碰它，一句 `git worktree add <path> <branch>` 就能把工作树再长回来。不加期限的否决
会把一整类 worktree 永久钉在盘上（正是 issue #315 要治的病）。

**为什么 `prune` 有闸、闸又只拦一半**：`git worktree prune` 是**仓库全局**的，会注销任何
「gitdir 指向的目录当下不存在」的登记——owner 机器上还有 142 个手工 worktree 挂在另一个
目录树下，那棵树一旦临时不在（外置卷没挂上）就会被一次 prune 全部注销。所以判据是
**会被注销的那些登记是不是全落在托管根之内**：全在 = 照 prune（托管根下的幽灵登记正是
本模块要收的），有一条在外面 = 整轮跳过记 `skipped:missing_paths`；登记表这一刻读不出来
= 一枪不开，记 `skipped:unknown`（不知道清单时那道闸等于不存在）。

CLI（零写入的那三形是给人看的诊断口）：

    python3 -m act.lib.worktrees              # 清点，人读
    python3 -m act.lib.worktrees --json       # 清点，一行 JSON（server GET 的真源）
    python3 -m act.lib.worktrees --dry-run    # 会删哪些（JSON），一个字节都不动
    python3 -m act.lib.worktrees --sweep      # 真扫（JSON 回执）

`git` 是唯一外部工具，全部经可注入的 ``git(args, cwd) -> (rc, stdout)`` runner
（默认 :func:`default_git`，永不抛，rc=None = 起不来）；测试注入假 runner，不起真进程。
本模块**不写 registry**（§44 单写者不变）——只读卡片判「在飞」。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from act.lib import config, policy, registry, transcripts
from act.lib.card_model import State

GitRunner = Callable[[list, str], "tuple[Optional[int], str]"]

MANAGED_REL = os.path.join(".claude", "worktrees")   # <repo>/.claude/worktrees
STALE_DAYS = 14         # 目录多久没动过算过时（issue #315 Expected 第 2 条）
MIN_AGE_DAYS = 2        # 任何删除的年龄地板：刚建出来的 worktree 一律不碰
MAX_REMOVALS = 50       # 单轮删除上限（防腐 #4：每一轮都有帽）
SCAN_BUDGET_S = 45.0    # 逐条 git status / rev-list 的时间预算，超了本轮不再分类
GIT_TIMEOUT_S = 60
DU_TIMEOUT_S = 90
OUTPUT_CAP = 200_000    # git stdout 字符上限
ROWS_CAP = 200          # 回执里逐条明细的条数上限
LIVE_STATES = (State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value)
# reason 词表（add-only）：留下的九个 + 删除的四个
KEEP_REASONS = ("main", "unmanaged", "missing", "locked", "live", "dirty", "unpushed",
                "active", "budget", "cap")
REMOVE_REASONS = ("merged", "gone", "stale", "settled")
# 不进 `managed` 计数的两个（它们压根不归本模块管，别让「worktree 数」把它们算进去）
UNMANAGED_REASONS = ("main", "unmanaged")
# 进程级总闸（同 §55 AIASSISTANT_LAUNCHD_PROBE / §71.1 AIASSISTANT_POWER_PROBE 的
# belt-and-braces）：只管两个**会动文件系统**的出口（sweep / release），清点不受它管。
# 测试套件默认设 0——忘了注入 git runner 的判例是空转，而不是真删开发者的 worktree。
SWEEP_ENV = "AIASSISTANT_WORKTREE_SWEEP"
_PORCELAIN_FLAGS = ("locked", "prunable", "bare", "detached")
_GITDIR_STAMPS = ("index", "HEAD", os.path.join("logs", "HEAD"))
_REPO_KEYS = ("repo", "root", "managed", "removable", "error")


def sweep_enabled() -> bool:
    return os.environ.get(SWEEP_ENV, "1").strip() not in ("0", "false", "no")


# --------------------------------------------------------------------------- #
# 注入缝：git / du
# --------------------------------------------------------------------------- #
def default_git(args: list, cwd: str) -> "tuple[Optional[int], str]":
    """``git <args>`` in ``cwd`` → (rc, stdout)；起不来 / 超时 = (None, "")。永不抛。"""
    try:
        proc = subprocess.run(["git", *[str(a) for a in args]], cwd=str(cwd), check=False,
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, ""
    return proc.returncode, (proc.stdout or "")[:OUTPUT_CAP]


def default_du(path: str, timeout_s: float = DU_TIMEOUT_S) -> Optional[int]:
    """``du -sk <path>`` → 字节；没有 du（Windows）/ 超时 / 读不出数字 = None（不虚报 0）。"""
    try:
        proc = subprocess.run(["du", "-sk", str(path)], check=False, capture_output=True,
                              text=True, timeout=timeout_s)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return _first_kb(proc.stdout)


def _first_kb(text: object) -> Optional[int]:
    """``du -sk`` 最后一行的首字段（KB）→ 字节；读不出 = None。"""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    try:
        return int(lines[-1].split()[0]) * 1024
    except (IndexError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 路径与登记
# --------------------------------------------------------------------------- #
def managed_root(repo: object) -> str:
    """``<repo>/.claude/worktrees`` 的 realpath——本模块允许触碰的唯一目录。"""
    return os.path.realpath(os.path.join(os.path.expanduser(str(repo)), MANAGED_REL))


def _real(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return os.path.realpath(os.path.expanduser(text))
    except (OSError, ValueError):
        return None


def under(root: str, path: object) -> bool:
    """``path`` 严格在 ``root`` 之内（realpath 后比对；root 自身不算）。"""
    real = _real(path)
    return bool(real) and real.startswith(root.rstrip(os.sep) + os.sep)


def _blank_entry(path: str) -> dict:
    return {"path": path, "head": "", "branch": "", "locked": False,
            "prunable": False, "bare": False}


def _apply_line(cur: dict, key: str, rest: str) -> None:
    """porcelain 的一行写进当前条目；未知键忽略（git 版本不同会多出键）。"""
    if key == "HEAD":
        cur["head"] = rest
    elif key == "branch":
        cur["branch"] = rest.split("refs/heads/", 1)[-1]
    elif key in _PORCELAIN_FLAGS:
        cur[key] = True


def parse_porcelain(text: str) -> list:
    """``git worktree list --porcelain`` → [{path, head, branch, locked, prunable, bare}]。
    第一条恒是主工作树。"""
    rows: list = []
    cur: Optional[dict] = None
    for raw in str(text or "").splitlines():
        key, _sep, rest = raw.strip().partition(" ")
        if key == "worktree":
            cur = _blank_entry(rest)
            rows.append(cur)
        elif cur is not None:
            _apply_line(cur, key, rest)
    return rows


def registered(git: GitRunner, repo: str) -> "tuple[list, Optional[str]]":
    """登记在案的全部 worktree（含主工作树，`main` 标记在第一条）+ 错误说明。"""
    rc, out = git(["worktree", "list", "--porcelain"], repo)
    if rc != 0:
        return [], "git worktree list failed (rc=%s)" % (rc,)
    rows = parse_porcelain(out)
    for i, row in enumerate(rows):
        row["main"] = i == 0
    return rows, None


# --------------------------------------------------------------------------- #
# 年龄、在飞的卡、扫描根
# --------------------------------------------------------------------------- #
def _stat_mtime(path: str) -> Optional[float]:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _gitdir_of(path: str) -> Optional[str]:
    """worktree 里 `.git` 文件指向的 gitdir（`gitdir: <path>` 一行）；读不到 = None。"""
    try:
        text = Path(path, ".git").read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    line = text.splitlines()[0] if text else ""
    return line.partition("gitdir:")[2].strip() or None


def _entry_mtimes(path: str) -> list:
    try:
        with os.scandir(path) as it:
            return [_stat_mtime(entry.path) for entry in it]
    except OSError:
        return []


def _gitdir_mtimes(gitdir: Optional[str]) -> list:
    if not gitdir:
        return []
    return [_stat_mtime(os.path.join(gitdir, name)) for name in _GITDIR_STAMPS]


def touched_at(path: str) -> Optional[float]:
    """这条 worktree「最后动过」的时刻 = 目录本身 / 顶层各条目 / `.git` / gitdir 的
    `index`·`HEAD`·`logs/HEAD` 里最新的 mtime。目录 mtime 只在顶层增删时才变，光看它
    会把还在被人用的 worktree 判成过时；index 与 logs/HEAD 是任何 git 命令都会碰的。

    已知且有意的副作用：:func:`is_dirty` 自己那一下 `git status` 也会刷新 `index`，
    所以被守卫留下的候选下一轮会显得「刚动过」。这只**推迟**再次审视（脏的那条最多
    每 STALE_DAYS 天、merged/gone 的最多每 MIN_AGE_DAYS 天被重新问一次），永远不会
    造成错删——宁可慢一轮，不可多删一个。"""
    stamps = [_stat_mtime(path), _stat_mtime(os.path.join(path, ".git"))]
    stamps += _entry_mtimes(path) + _gitdir_mtimes(_gitdir_of(path))
    known = [s for s in stamps if s is not None]
    return max(known) if known else None


def age_days(mtime: Optional[float], now: float) -> Optional[float]:
    return None if mtime is None else max(0.0, (now - mtime) / 86400.0)


def _session_cwd(ex: dict, resolve: Callable[[str], Optional[Path]]) -> Optional[str]:
    sid = ex.get("session_id")
    if not sid:
        return None
    try:
        cwd = resolve(str(sid))
    except OSError:
        return None
    return str(cwd) if cwd else None


def _card_cwds(req: object, resolve: Callable[[str], Optional[Path]]) -> set:
    """一张卡贡献的会话目录（realpath 后的，0–2 个）。"""
    ex = getattr(req, "execution", None)
    ex = ex if isinstance(ex, dict) else {}
    cands = (ex.get("cwd"), _session_cwd(ex, resolve))
    return {real for real in (_real(c) for c in cands) if real}


def live_paths(reqs: Optional[list] = None,
               resolve: Optional[Callable[[str], Optional[Path]]] = None) -> set:
    """在飞的卡（approved / executing / review）的会话 cwd（realpath）——这些 worktree
    还有人能 `claude --resume` 回去，一条都不许删。读不出 transcript 的卡跳过。"""
    resolve = resolve or transcripts.transcript_cwd
    rows = reqs if reqs is not None else registry.load_all()
    out: set = set()
    for req in rows:
        if getattr(req, "status", None) in LIVE_STATES:
            out |= _card_cwds(req, resolve)
    return out


def _extra_root(req: object, seen: set) -> Optional[str]:
    """卡片 `target_repo` 里够格当扫描根的那些：realpath 去重 + 真的有托管目录。"""
    real = _real(getattr(req, "target_repo", None))
    if not real or real in seen:
        return None
    seen.add(real)
    return real if os.path.isdir(os.path.join(real, MANAGED_REL)) else None


def roots(cfg: object = None, reqs: Optional[list] = None) -> list:
    """扫哪些 repo：通道 repo（§65.3 的物理闸，恒在）∪ 卡片 `target_repo` 里真的带
    `.claude/worktrees/` 的那些；realpath 去重、保序。"""
    primary = _real(policy.self_improve_repo_path(cfg)) or str(config.HOME)
    seen = {primary}
    extra = (_extra_root(req, seen) for req in reqs or [])
    return [primary] + [real for real in extra if real]


# --------------------------------------------------------------------------- #
# 判决
# --------------------------------------------------------------------------- #
def remote_refs(git: GitRunner, repo: str) -> set:
    rc, out = git(["for-each-ref", "--format=%(refname:short)", "refs/remotes"], repo)
    return {ln.strip() for ln in str(out or "").splitlines() if ln.strip()} if rc == 0 else set()


def default_remote_ref(git: GitRunner, repo: str, refs: set) -> Optional[str]:
    """远端默认分支引用（`origin/main` 之类）：先问 `origin/HEAD`，再按 main / master /
    dev 的顺序在已有的远端引用里挑；一个都没有 = None（`merged` 这一条自然失效）。"""
    rc, out = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo)
    if rc == 0 and str(out).strip():
        return str(out).strip()
    for name in ("origin/main", "origin/master", "origin/dev"):
        if name in refs:
            return name
    return None


def merged_branches(git: GitRunner, repo: str, ref: Optional[str]) -> set:
    """已并进 ``ref`` 的本地分支名集合（`git branch --merged`）；拿不到 = 空集。"""
    if not ref:
        return set()
    rc, out = git(["branch", "--merged", ref, "--format=%(refname:short)"], repo)
    return {ln.strip() for ln in str(out or "").splitlines() if ln.strip()} if rc == 0 else set()


def _remote_has(refs: set, branch: str) -> bool:
    """某个远端上有同名分支（`origin/<branch>` 形；多 remote 一并认）。"""
    tail = "/" + branch
    return any(ref.endswith(tail) for ref in refs)


def _stale_or_none(age: float, days: int) -> Optional[str]:
    return "stale" if age >= days else None


def _branch_reason(branch: str, merged: set, refs: set) -> Optional[str]:
    """分支侧的两个理由；detached 或分支在远端还活着 = None（留给年龄那条判）。"""
    if not branch:
        return None
    if branch in merged:
        return "merged"
    return None if _remote_has(refs, branch) else "gone"


def candidate_reason(branch: str, age: Optional[float], merged: set, refs: set,
                     days: int = STALE_DAYS) -> Optional[str]:
    """便宜那一半的判决（零 git 调用）：够格删就给理由，否则 None。"""
    if age is None or age < MIN_AGE_DAYS:
        return None
    return _branch_reason(branch, merged, refs) or _stale_or_none(age, days)


def is_dirty(git: GitRunner, path: str) -> bool:
    """`git status --porcelain` 非空 = 脏；**读不到也算脏**（拿不准就不动，§0 第 10 条）。"""
    rc, out = git(["status", "--porcelain"], path)
    return rc != 0 or bool(str(out or "").strip())


def _count_positive(out: object) -> bool:
    try:
        return int(str(out or "0").strip().split()[0]) > 0
    except (IndexError, ValueError):
        return True


def unpushed(git: GitRunner, repo: str, head: str) -> bool:
    """有只存在于本地的提交（`rev-list --count <head> --not --remotes` > 0）；
    数不出来一律当有（fail-closed：删 worktree 不许赌）。"""
    if not head:
        return True
    rc, out = git(["rev-list", "--count", head, "--not", "--remotes"], repo)
    if rc != 0:
        return True
    return _count_positive(out)


def _age_of(path: str, now: float) -> "tuple[bool, Optional[float]]":
    """(目录还在吗, 多少天没动过)；目录不在 = (False, None)。"""
    if not os.path.isdir(path):
        return False, None
    days = age_days(touched_at(path), now)
    return True, None if days is None else round(days, 1)


def _row(entry: dict, now: float, live: set) -> dict:
    path = str(entry.get("path") or "")
    exists, age = _age_of(path, now)
    return {"path": path, "name": os.path.basename(path.rstrip(os.sep)),
            "branch": str(entry.get("branch") or ""), "head": str(entry.get("head") or ""),
            "locked": bool(entry.get("locked")), "exists": exists, "age_days": age,
            "live": _real(path) in live, "dirty": None, "kept_branch": None,
            "verdict": "keep", "reason": "active"}


def _flag_reason(row: dict) -> Optional[str]:
    if row["locked"]:
        return "locked"
    return "live" if row["live"] else None


def _guard_reason(row: dict, entry: dict, root: str) -> Optional[str]:
    """守卫：命中即给出留下的 reason，都不命中 = None。"""
    if entry.get("main") or entry.get("bare"):
        return "main"
    if not under(root, row["path"]):        # 硬边界：托管根之外的 worktree 永不入判
        return "unmanaged"
    if not row["exists"]:
        return "missing"
    return _flag_reason(row)


def _judge_cheap(row: dict, entry: dict, root: str, merged: set, refs: set,
                 days: int) -> Optional[str]:
    """守卫 + 便宜判决；返回删除理由，或 None（此时 row 已带上留下的 reason）。"""
    guard = _guard_reason(row, entry, root)
    if guard:
        row["reason"] = guard
        return None
    reason = candidate_reason(row["branch"], row["age_days"], merged, refs, days)
    row["reason"] = reason or "active"
    return reason


def _judge_costly(row: dict, git: GitRunner, repo: str, reason: str, days: int) -> None:
    """贵那一半（每条两个 git 子进程）。脏 = 留下（那可能是唯一一份）。有只存在于本地的
    提交 = **分支永不删**，而目录的年龄门槛从 :data:`MIN_AGE_DAYS` 抬到 ``days``
    （`STALE_DAYS` 那把尺，即 owner 那条「mtime 超过 14 天且无未提交改动」）——删目录
    从来不会丢提交（分支引用住在主 repo 的 `.git` 里，`worktree remove` 不碰它），
    所以这一条是**延期**不是否决：不加期限的否决会把一整类 worktree 永久钉在盘上。"""
    row["dirty"] = is_dirty(git, row["path"])
    if row["dirty"]:
        row["reason"] = "dirty"
        return
    if unpushed(git, repo, row["head"]):
        row["kept_branch"] = "unpushed"
        if (row["age_days"] or 0.0) < days:
            row["reason"] = "unpushed"
            return
    row.update({"verdict": "remove", "reason": reason})


# --------------------------------------------------------------------------- #
# 清点一个 repo
# --------------------------------------------------------------------------- #
def _scan_env(git, repo, now, live, days, budget_s, clock, beat) -> dict:
    """一次扫描的只读上下文（默认值在这里落定，判决只读它）。"""
    return {"git": git or default_git, "repo": str(repo), "root": managed_root(repo),
            "now": time.time() if now is None else now,
            "live": set() if live is None else live, "days": days, "clock": clock,
            "deadline": clock() + max(0.0, budget_s), "beat": beat}


def _beat(beat: Optional[Callable[[], None]]) -> None:
    """心跳打一下（§47.4）：**每判一条、每删一条**各一下，不是每个 root 一下——现实里
    root 只有一个（通道 repo），而一轮分类要跑到 :data:`SCAN_BUDGET_S`、一轮删除要删掉
    几十份带 `web/node_modules` 的完整 checkout，中间不打心跳就会被 `/api/health` 与
    `act/doctor.py` 误判成 `actd_stalled`（阈值 max(3×interval, 90) 秒）。"""
    if beat:
        beat()


def _judge_one(env: dict, row: dict, reason: str) -> bool:
    """贵那一半，带预算：预算用尽记 `budget` 并报 truncated（本轮不再问 git）。"""
    if env["clock"]() >= env["deadline"]:
        row["reason"] = "budget"
        return True
    _judge_costly(row, env["git"], env["repo"], reason, env["days"])
    return False


def _classify(env: dict, entries: list, merged: set, refs: set) -> "tuple[list, bool]":
    rows, truncated = [], False
    for entry in entries:
        _beat(env["beat"])
        row = _row(entry, env["now"], env["live"])
        reason = _judge_cheap(row, entry, env["root"], merged, refs, env["days"])
        if reason is not None:
            truncated = _judge_one(env, row, reason) or truncated
        rows.append(row)
    return rows, truncated


def _empty_survey(repo: str, root: str, error: Optional[str]) -> dict:
    return {"repo": repo, "root": root, "registered": 0, "managed": 0, "removable": 0,
            "truncated": False, "rows": [], "error": error}


def _totals(env: dict, rows: list, truncated: bool) -> dict:
    managed = [r for r in rows if r["reason"] not in UNMANAGED_REASONS]
    return {"repo": env["repo"], "root": env["root"], "registered": len(rows),
            "managed": len(managed),
            "removable": sum(1 for r in managed if r["verdict"] == "remove"),
            "truncated": truncated, "rows": managed[:ROWS_CAP], "error": None}


def survey(repo: str, *, git: Optional[GitRunner] = None, now: Optional[float] = None,
           live: Optional[set] = None, days: int = STALE_DAYS,
           budget_s: float = SCAN_BUDGET_S, clock: Callable[[], float] = time.monotonic,
           beat: Optional[Callable[[], None]] = None) -> dict:
    """一个 repo 的清点 + 判决（零写入）。永不抛：git 不可用 = `error` 非空、rows 空。"""
    env = _scan_env(git, repo, now, live, days, budget_s, clock, beat)
    entries, error = registered(env["git"], env["repo"])
    if error:
        return _empty_survey(env["repo"], env["root"], error)
    refs = remote_refs(env["git"], env["repo"])
    merged = merged_branches(env["git"], env["repo"],
                             default_remote_ref(env["git"], env["repo"], refs))
    rows, truncated = _classify(env, entries, merged, refs)
    return _totals(env, rows, truncated)


# --------------------------------------------------------------------------- #
# 执行：prune → remove → branch -d
# --------------------------------------------------------------------------- #
def _would_be_pruned(entry: dict) -> bool:
    """这条登记当下指不到目录 = 一次 `git worktree prune` 就会注销它。"""
    return bool(entry.get("prunable")) or not os.path.isdir(str(entry.get("path") or ""))


def prune(git: GitRunner, repo: str, root: str, entries: list) -> str:
    """`git worktree prune`，**有闸，且闸只拦该拦的**：prune 是仓库全局的，会注销
    **任何**指不到目录的登记——owner 机器上那 142 个手工 worktree 挂在别的目录树下，
    那棵树一旦临时不在（外置卷没挂上），一次 prune 就把它们的登记全注销了。所以判据是
    「**会被注销的**那些登记是不是全落在托管根之内」：全在 = 照 prune（托管根下的幽灵
    正是本节要收的东西，若连它们都拦，prune 这条腿就只在无事可做时才动），有一条在外面
    = 整轮跳过记 `skipped:missing_paths`。**登记表读不出来（空）= 一枪不开**，记
    `skipped:unknown`——不知道有哪些登记的时候，这道闸等于不存在（fail-closed）。"""
    if not entries:
        return "skipped:unknown"
    if any(_would_be_pruned(e) and not under(root, e.get("path")) for e in entries):
        return "skipped:missing_paths"
    rc, _out = git(["worktree", "prune"], repo)
    return "ok" if rc == 0 else "failed"


def _removal(row: dict, removed: bool, branch_deleted: bool, error: Optional[str]) -> dict:
    return {"path": row["path"], "branch": row["branch"], "reason": row["reason"],
            "removed": removed, "branch_deleted": branch_deleted,
            "kept_branch": str(row.get("kept_branch") or "") or None, "error": error}


def remove_one(git: GitRunner, repo: str, row: dict) -> dict:
    """一条：`git worktree remove`（**永不 --force**）→ 成功再 `git branch -d`
    （**永不 -D**：`-d` 拒绝就是「这条分支还有没落地的提交」，那是信息不是障碍）。
    `kept_branch` 非空（有只存在于本地的提交）= 这一枪连问都不问，分支原地留着。"""
    rc, out = git(["worktree", "remove", row["path"]], repo)
    if rc != 0:
        return _removal(row, False, False,
                        ("worktree remove rc=%s %s" % (rc, str(out or "").strip()))[:200])
    keep = bool(row.get("kept_branch"))
    return _removal(row, True, False if keep else _delete_branch(git, repo, row["branch"]), None)


def _delete_branch(git: GitRunner, repo: str, branch: str) -> bool:
    if not branch:
        return False
    rc, _out = git(["branch", "-d", branch], repo)
    return rc == 0


def _tally(rows: list) -> dict:
    out: dict = {}
    for row in rows:
        if row["verdict"] != "remove":
            out[row["reason"]] = out.get(row["reason"], 0) + 1
    return out


def _bump(counter: dict, key: str, n: int) -> None:
    if n:
        counter[key] = counter.get(key, 0) + n


def _iso(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _blank_receipt(now: float, dry_run: bool) -> dict:
    return {"ok": True, "dry_run": bool(dry_run), "swept_at": _iso(now), "removed": [],
            "failed": [], "skipped": {}, "pruned": {}, "worktrees": 0, "removable": 0,
            "truncated": False, "repos": []}


def _merge_survey(receipt: dict, found: dict) -> None:
    receipt["repos"].append({k: found[k] for k in _REPO_KEYS})
    receipt["worktrees"] += found["managed"]
    receipt["removable"] += found["removable"]
    receipt["truncated"] = receipt["truncated"] or found["truncated"]
    for reason, n in _tally(found["rows"]).items():
        _bump(receipt["skipped"], reason, n)


def _execute(receipt: dict, repo: str, root: str, git: GitRunner, doomed: list,
             beat: Optional[Callable[[], None]]) -> None:
    """prune（有闸）在前、逐条 remove 在后。登记表这一刻读不出来 = prune 一枪不开
    （`skipped:unknown`）：清单未知时那道闸拦不住任何东西。"""
    entries, err = registered(git, repo)
    receipt["pruned"][repo] = "skipped:unknown" if err else prune(git, repo, root, entries)
    for row in doomed:
        _beat(beat)
        done = remove_one(git, repo, row)
        (receipt["removed"] if done["removed"] else receipt["failed"]).append(done)


def _sweep_repo(receipt: dict, repo: str, plan: dict) -> None:
    found = survey(repo, git=plan["git"], now=plan["now"], live=plan["live"],
                   days=plan["days"], budget_s=plan["budget_s"], beat=plan["beat"])
    _merge_survey(receipt, found)
    if found["error"]:
        receipt["ok"] = False
        return
    doomed = [r for r in found["rows"] if r["verdict"] == "remove"]
    budget = max(0, plan["budget"])
    _bump(receipt["skipped"], "cap", len(doomed[budget:]))
    if plan["dry_run"]:
        receipt["removed"] += [_removal(r, False, False, None) for r in doomed[:budget]]
        return
    _execute(receipt, str(repo), found["root"], plan["git"], doomed[:budget], plan["beat"])


def _sweep_roots(receipt: dict, cfg: object, cards: list, plan: dict) -> None:
    for repo in roots(cfg, cards):
        _beat(plan["beat"])
        _sweep_repo(receipt, repo, plan)
        plan["budget"] = plan["limit"] - len(receipt["removed"])


def _sweep_inputs(reqs: Optional[list], live: Optional[set]) -> "tuple[list, set]":
    cards = reqs if reqs is not None else registry.load_all()
    return cards, (live_paths(cards) if live is None else live)


def _sweep_plan(git, now, live, days, limit, dry_run, budget_s, beat) -> dict:
    return {"git": git, "now": now, "live": live, "days": days, "limit": int(limit),
            "budget": int(limit), "dry_run": bool(dry_run), "budget_s": budget_s,
            "beat": beat}


def sweep(cfg: object = None, *, git: Optional[GitRunner] = None, now: Optional[float] = None,
          dry_run: bool = False, days: int = STALE_DAYS, limit: int = MAX_REMOVALS,
          reqs: Optional[list] = None, live: Optional[set] = None,
          beat: Optional[Callable[[], None]] = None, budget_s: float = SCAN_BUDGET_S) -> dict:
    """全部 root 扫一遍。回执：`{ok, dry_run, removed[], failed[], skipped{}, pruned{},
    worktrees, removable, truncated, repos[]}`。永不抛（宪法第 11 条）。"""
    now = time.time() if now is None else now
    receipt = _blank_receipt(now, dry_run)
    if git is None and not sweep_enabled():
        receipt["skipped"] = {"disabled": 1}
        return receipt
    cards, live = _sweep_inputs(reqs, live)
    _sweep_roots(receipt, cfg, cards,
                 _sweep_plan(git or default_git, now, live, days, limit, dry_run, budget_s, beat))
    return receipt


# --------------------------------------------------------------------------- #
# 结算即释放（§65.5 的两条出口调用）
# --------------------------------------------------------------------------- #
def _card_branch(req: object) -> str:
    ex = getattr(req, "execution", None) or {}
    block = ex.get("self_improve") if isinstance(ex, dict) else None
    branch = block.get("branch") if isinstance(block, dict) else None
    return str(branch or "")


def _branch_worktrees(git: GitRunner, repo: str, branch: str) -> list:
    """登记表里分支名对得上的那些路径（卡上没有分支名 = 空）。"""
    if not branch:
        return []
    entries, _err = registered(git, repo)
    return [e["path"] for e in entries if not e.get("main") and e.get("branch") == branch]


def _release_targets(git: GitRunner, repo: str, root: str, branch: str, req: object,
                     resolve: Callable[[str], Optional[Path]]) -> list:
    """这张卡自己的 worktree：登记表里同分支的那条 ∪ transcript 记下的会话 cwd；
    两者都必须在托管根之内（会话可能压根没进 worktree，那时 cwd = repo 根）。"""
    paths = _branch_worktrees(git, repo, branch)
    known = {_real(p) for p in paths}
    ex = getattr(req, "execution", None)
    cwd = _session_cwd(ex if isinstance(ex, dict) else {}, resolve)
    if cwd and _real(cwd) not in known:
        paths.append(cwd)
    return [p for p in paths if under(root, p)]


def _release_one(out: dict, git: GitRunner, repo: str, path: str, branch: str) -> None:
    if is_dirty(git, path):
        out["skipped"].append({"path": path, "reason": "dirty"})
        return
    done = remove_one(git, repo, {"path": path, "branch": branch, "reason": "settled"})
    (out["removed"] if done["removed"] else out["skipped"]).append(done)


def _release_body(out: dict, req: object, cfg: object, git: GitRunner,
                  resolve: Callable[[str], Optional[Path]]) -> None:
    try:
        repo = str(policy.self_improve_repo_path(cfg))
        out["branch"] = _card_branch(req)
        for path in _release_targets(git, repo, managed_root(repo), out["branch"], req, resolve):
            _release_one(out, git, repo, path, out["branch"])
    except Exception as exc:  # noqa: BLE001 - 结算路径上的清扫失败只记账
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])


def _log_release(log: Optional[Callable[[str], None]], req: object, out: dict) -> None:
    if not log:
        return
    tail = "" if not out["error"] else " error=" + out["error"]
    log("self_improve: %s worktree release removed=%d skipped=%d%s"
        % (getattr(req, "id", "?"), len(out["removed"]), len(out["skipped"]), tail))


def release(req: object, cfg: object = None, *, git: Optional[GitRunner] = None,
            log: Optional[Callable[[str], None]] = None,
            resolve: Optional[Callable[[str], Optional[Path]]] = None) -> dict:
    """卡结算（PR 合并 = 验收 / 关闭 = 拒绝）后删掉它自己的 worktree 与本地分支。
    best-effort：任何失败只进回执与日志，绝不抛（§65.5 的落账不许被扫地连累）。"""
    out = {"removed": [], "skipped": [], "branch": "", "error": None}
    if git is None and not sweep_enabled():
        out["skipped"].append({"path": None, "reason": "disabled"})
        return out
    _release_body(out, req, cfg, git or default_git, resolve or transcripts.transcript_cwd)
    _log_release(log, req, out)
    return out


# --------------------------------------------------------------------------- #
# 清点（server GET / CLI 的真源）
# --------------------------------------------------------------------------- #
def _measure(root: str, du: Callable[[str], Optional[int]], measure: bool) -> Optional[int]:
    """托管根的占用；不量 / 目录不在 = None。"""
    if not measure or not os.path.isdir(root):
        return None
    return du(root)


def _merge_bytes(out: dict, found: dict) -> None:
    """量到的加进总数；量不到而这个 root 下真有 worktree = 诚实标 partial（不虚报 0）。"""
    if found["bytes"] is None:
        out["bytes_partial"] = out["bytes_partial"] or found["managed"] > 0
        return
    out["bytes"] = (out["bytes"] or 0) + found["bytes"]


def _merge_inventory(out: dict, found: dict) -> None:
    out["repos"].append(found)
    out["worktrees"] += found["managed"]
    out["removable"] += found["removable"]
    out["truncated"] = out["truncated"] or found["truncated"]
    out["ok"] = out["ok"] and not found["error"]
    _merge_bytes(out, found)


def _blank_inventory(now: float, days: int) -> dict:
    return {"ok": True, "scanned_at": _iso(now), "repos": [], "worktrees": 0, "removable": 0,
            "bytes": None, "bytes_partial": False, "truncated": False, "stale_days": int(days)}


def inventory(cfg: object = None, *, git: Optional[GitRunner] = None, now: Optional[float] = None,
              reqs: Optional[list] = None, live: Optional[set] = None, days: int = STALE_DAYS,
              measure: bool = True, du: Optional[Callable[[str], Optional[int]]] = None,
              budget_s: float = SCAN_BUDGET_S) -> dict:
    """全部 root 的清点（零写入）。`bytes` = 各托管根 `du -sk` 之和，量不到 = null +
    `bytes_partial: true`（§0 第 3 条：不虚报数字）。"""
    now = time.time() if now is None else now
    cards, live = _sweep_inputs(reqs, live)
    out = _blank_inventory(now, days)
    for repo in roots(cfg, cards):
        found = survey(repo, git=git or default_git, now=now, live=live, days=days,
                       budget_s=budget_s)
        found["bytes"] = _measure(found["root"], du or default_du, measure)
        _merge_inventory(out, found)
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _human_repo(repo: dict) -> list:
    tail = "（%s）" % repo["error"] if repo.get("error") else ""
    out = ["- %s: %d 条%s" % (repo["repo"], repo["managed"], tail)]
    out += ["    %-38s %-30s %5s d  %s/%s"
            % (row["name"][:38], (row["branch"] or "detached")[:30], row["age_days"],
               row["verdict"], row["reason"])
            for row in repo.get("rows", [])]
    return out


def _human(doc: dict) -> str:
    size = doc.get("bytes")
    lines = ["worktrees: %d（可清理 %d）" % (doc.get("worktrees", 0), doc.get("removable", 0)),
             "占用: %s" % ("未知" if size is None else "%.1f GB" % (size / 1e9))]
    for repo in doc.get("repos", []):
        lines += _human_repo(repo)
    return "\n".join(lines)


def _parse_args(argv: Optional[list]):
    parser = argparse.ArgumentParser(
        prog="python3 -m act.lib.worktrees",
        description="Inventory and reclaim <repo>/.claude/worktrees/ (CONTRACT §75).")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dry-run", action="store_true",
                        help="what a sweep would remove; changes nothing")
    parser.add_argument("--sweep", action="store_true",
                        help="actually remove the eligible worktrees")
    parser.add_argument("--days", type=int, default=STALE_DAYS,
                        help="stale age in days (default %d)" % STALE_DAYS)
    parser.add_argument("--limit", type=int, default=MAX_REMOVALS, help="max removals this run")
    parser.add_argument("--no-bytes", action="store_true", help="skip the du measurement")
    return parser.parse_args(argv)


def _render(doc: dict, args) -> str:
    machine = args.json or args.sweep or args.dry_run
    return json.dumps(doc, ensure_ascii=False, sort_keys=True) if machine else _human(doc)


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    cfg = config.load_config()
    if args.sweep or args.dry_run:
        doc = sweep(cfg, dry_run=not args.sweep, days=args.days, limit=args.limit)
    else:
        doc = inventory(cfg, days=args.days, measure=not args.no_bytes)
    sys.stdout.write(_render(doc, args) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
