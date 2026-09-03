"""act/lib/radar_rounds.py — 「立即测试一轮」：inbox 特形动作 ``radar_test_round`` 的
actd 落点 + dashboard 投影（CONTRACT §48.7）。

原生 SettingsGmail / SettingsSlack 的「立即测试一轮」= ``launchctl kickstart`` 雷达
agent 再读 health。web 看板没有 launchctl，走既有的 inbox 路：server 落
``{"action": "radar_test_round", "source": "gmail"|"slack"}``，actd 在 pass 里
（`_DETACHED_ACTIONS`，与 weekly_digest_now 同款）**分离启动**
``python -m act.radar_<source> --once``——雷达自己会把这一轮写进
``state/radar_health.json``（last_attempt / last_ok / skip_reason，§48.4），
那就是「结果」。本模块只记两件事：

- ``state/radar_test_rounds.json``（**actd 单写者**，每源一条、永远只有两条——
  有界，不需要 cap）：``{"gmail": {"requested_at": iso, "launch": "running"|"noop",
  "note": null|"disabled"|"launch_failed"}}``；
- 投影 ``radar_sources.<src>.test_round``（add-only）：
  ``{"requested_at": iso, "state": "running"|"done"|"noop"|"lost", "note": …}``
  ——``done`` = health 的 ``last_attempt`` 不早于 ``requested_at``（雷达跑完落笔了），
  ``lost`` = 超过 :data:`LOST_AFTER_S` 仍无落笔（子进程起来了却没写 health：崩在
  import 或被杀——诚实说「丢了」而不是永远「运行中」），``noop`` = 没起（源关着
  / 启动失败）。无状态、纯磁盘真值函数，dashboard 一次性构建也算得出。

源关着（§48.2 真静默：雷达入口直接 return、不写 health）时**不起子进程**——起了
也永远不会 done；记 ``noop:disabled``，web 据此说「源开关是关的」。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Callable, Optional

from act.lib import config, detached, logcap, sources

# 有「立即测试一轮」按钮的两个源（obsidian 雷达走 cron ingest 链，原生也没这颗按钮）
SOURCES: tuple = ("gmail", "slack")
_MODULES = {"gmail": "act.radar_gmail", "slack": "act.radar_slack"}
ROUNDS_PATH: Path = config.STATE_DIR / "radar_test_rounds.json"
LOG_NAME = "radar_test_round.log"
# 起了子进程却迟迟没有 health 落笔 → 判 lost（雷达一轮通常几秒到一分钟；网络超时也在此内）
LOST_AFTER_S = 10 * 60

RUNNING = detached.RUNNING
NOOP = detached.NOOP


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def load_rounds() -> dict:
    """整份台账；缺失 / 坏文件 → {}。Never raises。"""
    try:
        data = json.loads(ROUNDS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 台账坏了不许崩 pass / 投影
        return {}
    return data if isinstance(data, dict) else {}


def _record(source: str, launch: str, note: Optional[str]) -> None:
    """actd 单写者：原子写一条（tmp + replace）。写失败只影响投影，不反噬 pass。"""
    try:
        config.ensure_state_dirs()
        data = load_rounds()
        data[source] = {"requested_at": _iso_now(), "launch": launch, "note": note}
        tmp = ROUNDS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ROUNDS_PATH)
    except OSError:
        pass


def _source_enabled(source: str) -> bool:
    """§48.3 同款现读：actd 启动时冻结的 cfg 在用户翻开关后失真。"""
    try:
        return sources.enabled(config.load_config(), source)
    except Exception:  # noqa: BLE001 - 坏 config = 当关处理（fail-closed）
        return False


def request(decision: dict, log: Optional[Callable[[str], None]] = None) -> str:
    """actd 侧入口：校验 → 源开着才起 ``act.radar_<src> --once`` → 记台账。
    返回 §5.4 ack 词表（running / noop）。绝不 raise。"""
    say = log or (lambda _msg: None)
    source = decision.get("source") if isinstance(decision, dict) else None
    if source not in SOURCES:
        say(f"inbox: radar_test_round malformed source {source!r} — dropped")
        return NOOP
    if not _source_enabled(source):
        # §48.2：关着的雷达入口直接 return、不写 health——起了也永远不会 done
        say(f"inbox: radar_test_round {source} — source is switched off, not run")
        _record(source, NOOP, "disabled")
        return NOOP
    logcap.cap(config.STATE_DIR / LOG_NAME)   # 防腐 #4：append-only 日志出生即带帽
    result = detached.launch([_MODULES[source], "--once"], LOG_NAME,
                             f"radar_test_round {source}", say)
    _record(source, result, None if result == RUNNING else "launch_failed")
    return result


def _opt_str(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _round_record(source: str, rounds: Optional[dict]) -> Optional[dict]:
    data = load_rounds() if rounds is None else rounds
    rec = data.get(source) if isinstance(data, dict) else None
    if not isinstance(rec, dict) or not _opt_str(rec.get("requested_at")):
        return None
    return rec


def projection(source: str, health_entry: Optional[dict],
               now: Optional[_dt.datetime] = None,
               rounds: Optional[dict] = None) -> Optional[dict]:
    """dashboard ``radar_sources.<src>.test_round``：无请求记录 → None。"""
    rec = _round_record(source, rounds)
    if rec is None:
        return None
    when = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    return {"requested_at": rec["requested_at"],
            "state": _state(rec, health_entry, when),
            "note": _opt_str(rec.get("note"))}


def _attempt_after(health_entry: Optional[dict], requested_at: str) -> bool:
    # 同格式 ISO-Z 字串，字典序 = 时间序；雷达在请求之后落过笔就是跑完了
    attempt = health_entry.get("last_attempt") if isinstance(health_entry, dict) else None
    return isinstance(attempt, str) and attempt >= requested_at


def _expired(requested_at: str, now: _dt.datetime) -> bool:
    since = _parse_iso(requested_at)
    return since is not None and (now - since).total_seconds() > LOST_AFTER_S


def _state(rec: dict, health_entry: Optional[dict], now: _dt.datetime) -> str:
    if rec.get("launch") != RUNNING:
        return "noop"
    requested_at = rec["requested_at"]
    if _attempt_after(health_entry, requested_at):
        return "done"
    return "lost" if _expired(requested_at, now) else "running"
