"""daily_loop — 每日自我改进循环：先维护，再提案（CONTRACT §65；R2.4；owner D10/D12/D18）。

一句话：每天固定时段（`daily_loop.time`，默认 03:30 本地）在 actd 的 pass 里
跑一次——**先**整理看板（act/lib/maintenance：提案列 + 潜在任务列去重合成、
过时卡进回收站），**再**从日志台账 / analytics / doctor / 夜间变异报告 / GitHub
issue·PR / 素材库读信号（act/lib/loop_inputs），按指纹去重后铸 ≤
`max_proposals_per_day`（默认 5）张 🤖 提案卡进正常审批闸门。

边界（与法典对齐）：

- **只在 actd 里跑**（:func:`tick` 由 act/actd.py 每 pass 调；本模块的 CLI
  只出计划报告、零写入）——状态转移单写者不变（§0 第 1 条）。
- **不调 LLM**：提案是确定性模板（Uncle Bob 原则「价值靠确定性工具不靠
  提示词」，R2.9）；理解素材 URL / 修 CI / 补测试的智力活留给被派工的 agent。
  外来文本进卡片前已在 loop_inputs 里 fence（§0 第 5 条）。
- 铸卡走 `registry.merge_or_new`（同题折叠、§50 盖章），channel 恒
  `self_improve`（代码硬编码 = write-locked，policy.CHANNEL_CLASS → proposed，
  照旧人批；P6 通道的准入只认这个 channel + 物理 repo 路径）。
- 每次运行落一行 JSON 审计（`state/daily_loop.jsonl`，logcap 1 MB）；投影
  `state/daily_loop.json` → dashboard add-only 顶层键 `maintenance`
  （web 顶部横幅「今日整理：合并 N、清理 M（可撤销）」，不弹系统通知——D10
  设计判断）。任何阶段失败只记进 `errors`，绝不崩 pass（§0 第 11 条）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from act.lib import config, heartbeat, logcap, loop_inputs, maintenance, registry
from act.lib.registry import Requirement, State

SOURCE_CHANNEL = "self_improve"      # policy.CHANNEL_CLASS 同款字面量（write-locked）
REF_PREFIX = "self_improve:"          # sources[].ref = self_improve:<fingerprint>
TITLE_PREFIX = "🤖 "
CARD_TYPE = "self-improvement"
WHO = "daily_loop"

STATE_NAME = "daily_loop.json"
LOG_NAME = "daily_loop.jsonl"
LOG_MAX_BYTES = 1 << 20
LEDGER_DAYS = 90        # 指纹台账保留天数（与循环卡的回收站保留期同长）
LEDGER_CAP = 2000
COMMENT_LOOKBACK_DAYS = 7
TITLE_CAP = 120
DEDUP_MIN_TITLE = 12    # 与开放 issue/PR 标题做包含匹配的最短长度

# 进程级总闸（belt-and-braces，同 §55 AIASSISTANT_LAUNCHD_PROBE）：测试套件把它设为 "0"，
# 任何走真 actd.run_once 的判例都不会在沙箱里跑起整轮循环（真 gh / doctor 子进程）。
DISABLE_ENV = "AIASSISTANT_DAILY_LOOP"

# actd 每 pass 从磁盘现读到冻结 cfg 上的五把旋钮（§59 _refresh_model_knobs 同一刷新点）
LIVE_KNOBS = ("daily_loop_enabled", "daily_loop_time", "daily_loop_max_proposals_per_day",
              "daily_loop_stale_days", "daily_loop_trash_retention_days")

PHASE_IDLE = "idle"
PHASE_DEDUP = "dedup"
PHASE_STALE = "stale_sweep"
PHASE_PROPOSALS = "proposals"
GITHUB_KINDS = ("issue", "pr_red", "pr_comment", "mutation")

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


# --------------------------------------------------------------------------- #
# state file（投影真源）+ audit log
# --------------------------------------------------------------------------- #
def state_path() -> Path:
    return config.STATE_DIR / STATE_NAME


def log_path() -> Path:
    return config.STATE_DIR / LOG_NAME


def load_state(path: Optional[Path] = None) -> dict:
    try:
        data = json.loads((path or state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(state: dict, path: Optional[Path] = None) -> None:
    target = path or state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass   # 投影写不了不算循环失败（下一阶段再写）


def _append_log(entry: dict, path: Optional[Path] = None) -> None:
    target = path or log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logcap.cap(target, LOG_MAX_BYTES)   # 防腐 #4：出生即带帽
    except OSError:
        pass


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


# --------------------------------------------------------------------------- #
# schedule gate
# --------------------------------------------------------------------------- #
def _unlock_minutes(cfg) -> int:
    m = _TIME_RE.match(str(getattr(cfg, "daily_loop_time", "") or ""))
    if m is None:
        m = _TIME_RE.match(config.DEFAULT_DAILY_LOOP_TIME)
    return int(m.group(1)) * 60 + int(m.group(2))


def due(cfg, state: dict, now: _dt.datetime) -> bool:
    """今天还没跑、且本地时间已过解锁时刻、且开关开着。"""
    if not getattr(cfg, "daily_loop_enabled", True):
        return False
    if state.get("last_run_day") == now.date().isoformat():
        return False
    return now.hour * 60 + now.minute >= _unlock_minutes(cfg)


def next_run_at(cfg, state: dict, now: _dt.datetime) -> Optional[_dt.datetime]:
    """下一次解锁时刻（本地）；开关关 = None。"""
    if not getattr(cfg, "daily_loop_enabled", True):
        return None
    minutes = _unlock_minutes(cfg)
    slot = now.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    ran_today = state.get("last_run_day") == now.date().isoformat()
    if ran_today or slot <= now:
        slot += _dt.timedelta(days=1)
    return slot


# --------------------------------------------------------------------------- #
# proposer helpers
# --------------------------------------------------------------------------- #
def _source_refs(req: Requirement) -> list:
    return [str(s.get("ref") or "") for s in (req.sources or []) if isinstance(s, dict)]


def existing_fingerprints(reqs) -> set:
    """registry 里（任何状态，含回收站）已经载有的循环指纹。"""
    out = set()
    for r in reqs:
        out.update(ref[len(REF_PREFIX):] for ref in _source_refs(r) if ref.startswith(REF_PREFIX))
    return out


def _born_today(req: Requirement, today: str) -> bool:
    return any(isinstance(s, dict) and s.get("channel") == SOURCE_CHANNEL
               and str(s.get("date")) == today for s in (req.sources or []))


def proposals_today(reqs, today: str) -> int:
    """今天已铸的循环卡数（任何状态——owner 刚扔进回收站的也算额度）。"""
    return sum(1 for r in reqs if _born_today(r, today))


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def title_on_github(title: str, gh_titles: list) -> bool:
    """提案标题与某个开放 issue/PR 标题互相包含（≥12 字）= 已在 GitHub 上。"""
    t = _norm(title)
    if len(t) < DEDUP_MIN_TITLE:
        return False
    return any(len(g) >= DEDUP_MIN_TITLE and (t in g or g in t) for g in map(_norm, gh_titles))


def _prune_ledger(ledger: dict, today: _dt.date) -> dict:
    cutoff = (today - _dt.timedelta(days=LEDGER_DAYS)).isoformat()
    kept = {fp: day for fp, day in ledger.items() if isinstance(day, str) and day >= cutoff}
    return dict(sorted(kept.items(), key=lambda kv: kv[1])[-LEDGER_CAP:])


class _Selector:
    """按优先级挑今天要铸的信号：跳过已有指纹 / 同 class 今天已取 / GitHub 上
    已有同题 / 超额度。每条 skip 计数进审计行。"""

    def __init__(self, taken: set, gh_titles: list, budget: int) -> None:
        self.taken, self.gh_titles, self.budget = set(taken), list(gh_titles), budget
        self.kinds: set = set()
        self.skipped = {"dedup": 0, "kind_taken": 0, "gh_title": 0, "cap": 0}
        self.chosen: list = []

    def _on_github(self, sig) -> bool:
        return sig.kind not in GITHUB_KINDS and title_on_github(sig.title, self.gh_titles)

    def _reason(self, sig) -> Optional[str]:
        if sig.fingerprint in self.taken:
            return "dedup"
        if sig.kind in self.kinds:
            return "kind_taken"
        if self._on_github(sig):
            return "gh_title"
        return "cap" if len(self.chosen) >= self.budget else None

    def offer(self, sig) -> None:
        why = self._reason(sig)
        if why is not None:
            self.skipped[why] += 1
            return
        self.chosen.append(sig)
        self.kinds.add(sig.kind)
        self.taken.add(sig.fingerprint)


def select_signals(signals: list, *, taken: set, gh_titles: list, budget: int) -> "tuple[list, dict]":
    sel = _Selector(taken, gh_titles, budget)
    for sig in sorted(signals, key=lambda s: (s.priority, s.fingerprint)):
        sel.offer(sig)
    return sel.chosen, sel.skipped


def build_card(sig, today: str, repo_path: str) -> Requirement:
    """一条信号 → 未落盘的提案卡（channel 硬编码 self_improve；plan/DoD/成本齐全）。"""
    return Requirement(
        id="", title=(TITLE_PREFIX + sig.title)[:TITLE_CAP], type=CARD_TYPE, tier="T1",
        status=State.CARD_SENT.value, hardness="soft", summary=str(sig.summary or "")[:300],
        plan=list(sig.plan), definition_of_done=list(sig.dod),
        cost_estimate_usd=float(sig.cost_usd), target_repo=repo_path, delivery_mode="repo",
        sources=[{"channel": SOURCE_CHANNEL, "date": today, "ref": REF_PREFIX + sig.fingerprint,
                  "quote": str(sig.evidence or sig.summary)[:500], "who": WHO}],
    )


def _file_one(sig, today: str, repo_path: str) -> Optional[dict]:
    try:
        kind, saved = registry.merge_or_new_with_kind(build_card(sig, today, repo_path))
    except Exception as exc:  # noqa: BLE001 - 一张坏卡不许崩整轮
        return {"fingerprint": sig.fingerprint, "kind": sig.kind, "error": str(exc)[:200]}
    return {"id": saved.id, "fingerprint": sig.fingerprint, "kind": sig.kind,
            "outcome": kind, "title": str(saved.display_title or saved.title)}


def file_proposals(chosen: list, today: str, repo_path: str) -> list:
    return [x for x in (_file_one(s, today, repo_path) for s in chosen) if x is not None]


# --------------------------------------------------------------------------- #
# signal collection（每个读取器单独隔离）
# --------------------------------------------------------------------------- #
def _beating_gh(gh: Callable, interval) -> Callable:
    def _gh(args):
        heartbeat.beat("daily_loop:gh", interval)
        return gh(args)
    return _gh


def collect_signals(reqs: list, *, now: _dt.datetime, gh: Callable,
                    doctor: Optional[Callable], repo: str, interval=None) -> dict:
    """全部输入源 → {"signals", "summaries", "gh_titles", "inputs"}；坏读取器
    只在 inputs 里记 "unavailable"。"""
    gh = _beating_gh(gh, interval)
    since = now - _dt.timedelta(days=COMMENT_LOOKBACK_DAYS)
    readers = [
        ("registry", lambda: loop_inputs.registry_signals(reqs)),
        ("analytics", lambda: loop_inputs.analytics_signals(now=now)),
        ("radar_failed", loop_inputs.radar_failed_signals),
        ("write_storm", lambda: loop_inputs.write_storm_signals(now=now)),
        ("actd_log", loop_inputs.actd_log_signals),
        ("install_report", loop_inputs.install_report_signals),
        ("launchd_logs", loop_inputs.launchd_log_signals),
        ("doctor", lambda: loop_inputs.doctor_signals(doctor)),
        ("mutation", lambda: loop_inputs.mutation_signals(gh, repo)),
        ("materials", loop_inputs.materials_signals),
    ]
    out: dict = {"signals": [], "summaries": [], "gh_titles": [], "inputs": {}}
    for name, fn in readers:
        _run_reader(out, name, fn)
    _run_reader(out, "issues", lambda: loop_inputs.issue_signals(gh, repo), github=True)
    _run_reader(out, "prs", lambda: loop_inputs.pr_signals(gh, repo, since), github=True)
    return out


def _run_reader(out: dict, name: str, fn: Callable, github: bool = False) -> None:
    try:
        got = fn()
    except Exception as exc:  # noqa: BLE001 - 一个坏读取器只丢它自己
        out["inputs"][name] = f"unavailable: {type(exc).__name__}"
        return
    if github:
        _absorb_github(out, got)
        out["inputs"][name] = len(got[0])
        return
    out["signals"].extend(got)
    out["inputs"][name] = len(got)


def _absorb_github(out: dict, got: tuple) -> None:
    out["signals"].extend(got[0])
    if len(got) == 3:                       # issue_signals: (signals, summaries, titles)
        out["summaries"].extend(got[1])
    out["gh_titles"].extend(got[-1])


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def _set_phase(state: dict, phase: str, interval, **extra) -> None:
    state["phase"] = phase
    state.update(extra)
    _write_state(state)
    heartbeat.beat(f"daily_loop:{phase}", interval)


def _phase(fn: Callable, errors: list, label: str, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 阶段失败只记账，绝不崩 pass
        errors.append(f"{label}: {type(exc).__name__}: {str(exc)[:200]}")
        return default


def _propose(cfg, now: _dt.datetime, gh, doctor, state: dict, interval) -> dict:
    today = now.date().isoformat()
    reqs = registry.load_all()
    collected = collect_signals(reqs, now=now, gh=gh, doctor=doctor,
                                repo=loop_inputs.DEFAULT_REPO, interval=interval)
    ledger = _prune_ledger(dict(state.get("fingerprints") or {}), now.date())
    taken = existing_fingerprints(reqs) | set(ledger)
    budget = max(0, int(getattr(cfg, "daily_loop_max_proposals_per_day", 5) or 0)
                 - proposals_today(reqs, today))
    chosen, skipped = select_signals(collected["signals"], taken=taken,
                                     gh_titles=collected["gh_titles"], budget=budget)
    filed = file_proposals(chosen, today, str(config.HOME))
    ledger.update({row["fingerprint"]: today for row in filed if "id" in row})
    state["fingerprints"] = ledger
    materials_marked = _mark_materials(collected["signals"], filed)
    return {"filed": filed, "skipped": skipped, "budget": budget, "materials": materials_marked,
            "summaries": [{"kind": s.kind, "text": s.text, "ref": s.ref} for s in collected["summaries"]],
            "inputs": collected["inputs"], "signals": len(collected["signals"])}


def _material_id(fingerprint: str) -> Optional[str]:
    prefix = "material:"
    return fingerprint[len(prefix):] if fingerprint.startswith(prefix) else None


def _mark_materials(signals: list, filed: list) -> dict:
    """§62 台账回写：本轮读过的素材 → picked_up，铸了卡的 → proposal_created。"""
    picked = [mid for mid in (_material_id(s.fingerprint) for s in signals) if mid]
    cards = {mid: row["id"] for row in filed if "id" in row
             for mid in [_material_id(row["fingerprint"])] if mid}
    return loop_inputs.mark_materials(picked, cards) if picked else {}


def run(cfg, *, now: Optional[_dt.datetime] = None, gh: Optional[Callable] = None,
        doctor: Optional[Callable] = None, interval=None) -> dict:
    """一次完整运行：dedup → stale sweep → proposals；写投影与审计行。永不 raise。"""
    now = now or local_now()
    started = time.time()
    state = load_state()
    errors: list = []
    _set_phase(state, PHASE_DEDUP, interval, started_at=_iso(now))
    merges = _phase(lambda: maintenance.dedup_lanes(cfg), errors, "dedup", [])
    _set_phase(state, PHASE_STALE, interval)
    trashed = _phase(lambda: maintenance.sweep_stale(cfg, today=now.date()), errors, "stale_sweep", [])
    _set_phase(state, PHASE_PROPOSALS, interval)
    proposed = _phase(lambda: _propose(cfg, now, gh or loop_inputs.default_gh, doctor, state, interval),
                      errors, "proposals", {"filed": [], "skipped": {}, "summaries": [], "inputs": {}})
    filed = [row for row in proposed["filed"] if "id" in row]
    result = {"merged": len(merges), "trashed": len(trashed), "proposals": len(filed),
              "summaries": len(proposed["summaries"]), "errors": errors}
    _set_phase(state, PHASE_IDLE, interval, last_run_at=_iso(now),
               last_run_day=now.date().isoformat(), last_result=result)
    _append_log({"ts": _iso(now), "day": now.date().isoformat(),
                 "duration_s": round(time.time() - started, 1), "merges": merges,
                 "trashed": trashed, "proposals": proposed["filed"],
                 "skipped": proposed["skipped"], "summaries": proposed["summaries"],
                 "inputs": proposed["inputs"], "errors": errors})
    return dict(result, merges=merges, trashed_cards=trashed, filed=filed)


def tick(cfg, *, now: Optional[_dt.datetime] = None, gh: Optional[Callable] = None,
         doctor: Optional[Callable] = None, interval=None) -> Optional[dict]:
    """actd 每 pass 调一次：到点且今天没跑 → run；否则一次 stat 级开销。永不 raise。"""
    try:
        if os.environ.get(DISABLE_ENV) == "0":
            return None
        now = now or local_now()
        if not due(cfg, load_state(), now):
            return None
        return run(cfg, now=now, gh=gh, doctor=doctor, interval=interval)
    except Exception:  # noqa: BLE001 - 循环绝不反杀主循环
        return None


# --------------------------------------------------------------------------- #
# dashboard projection（§2 add-only 顶层键 maintenance）
# --------------------------------------------------------------------------- #
def _epoch(value) -> Optional[int]:
    dt = maintenance.parse_iso(value)
    return int(dt.timestamp()) if dt is not None else None


def _result_ints(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    out = {k: int(src.get(k) or 0) for k in ("merged", "trashed", "proposals", "summaries")}
    out["errors"] = len(src.get("errors") or [])
    return out


def projection(state: dict, cfg=None, now: Optional[_dt.datetime] = None) -> dict:
    """``{"phase", "started_at", "last_run_at", "next_run_at", "last_result"}``
    （时间全是 epoch int 或 null，§2 惯例）。"""
    now = now or local_now()
    return {
        "phase": str(state.get("phase") or PHASE_IDLE),
        "started_at": _epoch(state.get("started_at")),
        "last_run_at": _epoch(state.get("last_run_at")),
        "next_run_at": _next_epoch(cfg, state, now),
        "last_result": _result_ints(state.get("last_result")),
    }


def _next_epoch(cfg, state: dict, now: _dt.datetime) -> Optional[int]:
    nxt = next_run_at(cfg, state, now) if cfg is not None else None
    return int(nxt.timestamp()) if nxt is not None else None


def attach(dash: dict, cfg=None) -> dict:
    """Set ``dash["maintenance"]`` (add-only)；状态文件不存在 = 整键不存在。"""
    state = load_state()
    if state:
        dash["maintenance"] = projection(state, cfg)
    return dash


# --------------------------------------------------------------------------- #
# CLI：计划报告（零写入——真执行只在 actd 的 pass 里，§0 第 1 条）
# --------------------------------------------------------------------------- #
def plan(cfg, now: Optional[_dt.datetime] = None, gh: Optional[Callable] = None) -> dict:
    """会做什么（不做）：同题簇、过时判决、候选提案。"""
    now = now or local_now()
    reqs = registry.load_all()
    collected = collect_signals(reqs, now=now, gh=gh or loop_inputs.default_gh, doctor=None,
                                repo=loop_inputs.DEFAULT_REPO)
    clusters = [[r.id for r in c] for c in maintenance.find_clusters(reqs, cfg)]
    clustered = {rid for c in clusters for rid in c}   # dedup 先跑：簇内卡由合并处置
    return {
        "due": due(cfg, load_state(), now),
        "clusters": clusters,
        "stale": [x for x in _stale_plan(cfg, reqs, now.date()) if x["id"] not in clustered],
        "signals": [{"kind": s.kind, "fingerprint": s.fingerprint, "title": s.title}
                    for s in collected["signals"]],
        "summaries": [s.text for s in collected["summaries"]],
        "inputs": collected["inputs"],
    }


def _stale_plan(cfg, reqs: list, today: _dt.date) -> list:
    stale_days = int(getattr(cfg, "daily_loop_stale_days", 0) or 0)
    verdicts = ((r.id, maintenance.stale_verdict(r, reqs, today, stale_days)) for r in reqs)
    return [{"id": rid, "rule": rule} for rid, rule in verdicts if rule]


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m act.lib.daily_loop",
                                     description="每日自我改进循环——计划报告（只读；真执行在 actd）")
    parser.add_argument("--plan", action="store_true", help="打印今天会做什么（JSON），不写任何东西")
    parser.add_argument("--status", action="store_true", help="打印 state/daily_loop.json 的投影")
    args = parser.parse_args(argv)
    cfg = config.load_config()
    if args.status:
        print(json.dumps(projection(load_state(), cfg), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(plan(cfg), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
