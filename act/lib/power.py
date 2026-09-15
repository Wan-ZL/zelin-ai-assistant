"""power — 机器醒着吗：派发闸的判决 + 睡眠时长采样（CONTRACT §71.1 / §71.2）。

三件事，都不抛：
  1. `read_power()` —— 经 `act.lib.platform` 的第四件事（§71.1 探针）取一次
     原始读数；非 darwin / 命令缺席 / 格式漂移一律少键或空读数。
  2. `verdict()` —— 读数 → `awake` | `asleep` | `unknown` 的纯函数判决；
     **只有正证据才判 asleep**（fail-open：探不到就当醒着，宁可多派一张卡也
     不让整条自动派发在一台探不出状态的机器上静默饿死）。
  3. `sample_suspension()` / `credit_sleep()` —— 每 pass 量一次真实挂起时长
     （wall 前进量 − monotonic 前进量），超过 `SLEEP_GAP_SECONDS` 就记到在跑
     的卡上（§71.2 诚实耗时 + §71.3 重试一次的唯一证据）。

单采样者不变式（§48）：本模块有**自己的** `SUSPEND_STATE`，绝不读写
`act.lib.actd.alerts.WAKE_STATE`——那个 dict 的语义是「雷达睡醒宽限」，
两个采样者共用一份基线会互相把对方的跳变吃掉。
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, Optional

from act.lib import platform, policy, registry

# 判决词表（wire 侧的 chip 由 dashboard 映射，§51 词表见 policy.QUEUED_REASONS）
AWAKE = "awake"
ASLEEP = "asleep"
UNKNOWN = "unknown"

# System Capabilities 位图里的 Graphics 位（满醒 = 0xF；dark wake 没有它）
GRAPHICS_BIT = 0x2
# 判决缓存窗口：派发 pass 每 10 s 一轮，探针是三个子进程——一分钟一次够用，
# 「睡着」这件事也不会在一分钟内反复横跳。
MEMO_SECONDS = 60.0
# 超过这个挂起秒数才算「电脑睡过一觉」（同 alerts.WAKE_JUMP_FLOOR_SECONDS 的
# 量级：5 分钟以下的跳变是长 pass / 时钟校准，不是睡眠）。
SLEEP_GAP_SECONDS = 300.0
# 进程级总闸（同 §55 `AIASSISTANT_LAUNCHD_PROBE` / §70 `AIASSISTANT_DAILY_LOOP`
# 的 belt-and-braces）：`0` = 不探测、判决恒 unknown（fail-open）。测试套件
# 默认设它——没有哪条判例该在开发者的 Mac 上真起 pmset/ioreg。
PROBE_ENV = "AIASSISTANT_POWER_PROBE"

# 本进程的判决缓存（不是注入缝——注入缝是每个函数的 `probe=` 参数，防腐 #3）
_MEMO: Dict[str, object] = {"at": None, "verdict": None}
# §71.2 挂起采样的自有基线（wall / monotonic 各一份）
SUSPEND_STATE: Dict[str, Optional[float]] = {"last_wall": None, "last_mono": None}

Probe = Callable[[], dict]


def probe_enabled() -> bool:
    """探针总闸（`AIASSISTANT_POWER_PROBE=0` 关掉，缺省开）。"""
    return str(os.environ.get(PROBE_ENV, "1")).strip().lower() not in ("0", "false", "no")


def read_power(runner=None) -> dict:
    """一次原始读数（键全部可选）：`state` / `max_state`（IOPMrootDomain 电源档）、
    `capabilities`（System Capabilities 位图）、`user_active`（IOPMUserIsActive）、
    `assertions`（`pmset -g assertions` 系统级计数）。总闸关着 → `{}`。"""
    if not probe_enabled():
        return {}
    out: dict = {}
    state = platform.power_state(runner=runner)
    if state:
        out["state"], out["max_state"] = state
    out.update(platform.power_capabilities(runner=runner))
    assertions = platform.power_assertions(runner=runner)
    if assertions:
        out["assertions"] = assertions
    return out


def held_awake(reading: dict) -> bool:
    """有人/有东西正把这台机器摁醒着（`IOPMUserIsActive` 或 assertion
    `UserIsActive` / `PreventUserIdleDisplaySleep` 非零）——这是「显示器睡了但
    机器 24h 醒着的台式 Mac」不被闸饿死的那道护栏。"""
    if reading.get("user_active"):
        return True
    assertions = reading.get("assertions")
    if not isinstance(assertions, dict):
        return False
    return any(_positive(assertions.get(key))
               for key in ("UserIsActive", "PreventUserIdleDisplaySleep"))


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _root_verdict(reading: dict) -> Optional[str]:
    """主判据（arm64 实测活着）：IOPMrootDomain 当前电源档 < 最高档 = 不在满醒。
    None = 这条判据没答案，交给能力位兜底。"""
    state, ceiling = reading.get("state"), reading.get("max_state")
    if not (isinstance(state, int) and isinstance(ceiling, int) and ceiling > 0):
        return None
    return ASLEEP if state < ceiling else AWAKE


def _capability_verdict(reading: dict) -> str:
    """兜底判据：System Capabilities 缺 Graphics 位 = dark wake（屏幕没亮，
    机器是被维护唤醒叫起来的）。`held_awake` 压过它——显示器睡着但有人在用的
    机器照常派发。两条都没答案 = unknown。"""
    caps = reading.get("capabilities")
    if not isinstance(caps, int):
        return AWAKE if held_awake(reading) else UNKNOWN
    if caps & GRAPHICS_BIT or held_awake(reading):
        return AWAKE
    return ASLEEP


def verdict(reading: object) -> str:
    """读数 → `awake` | `asleep` | `unknown`（纯函数，垃圾输入 → unknown）。"""
    if not isinstance(reading, dict):
        return UNKNOWN
    return _root_verdict(reading) or _capability_verdict(reading)


def reset_probe_memo() -> None:
    """清掉本进程的判决缓存（判例之间互不串味；生产侧无人调用）。"""
    _MEMO.update({"at": None, "verdict": None})


def current_verdict(probe: Optional[Probe] = None, now: Optional[float] = None) -> str:
    """本机现在的判决（`MEMO_SECONDS` 缓存窗口内复用上一次的答案）。

    `probe` = 零参注入缝（默认 `read_power`）；`now` = monotonic 读数注入缝。
    探针抛异常 = 空读数 = unknown（宪法第 11 条：探针坏了不许崩 pass）。
    """
    now = time.monotonic() if now is None else now
    at = _MEMO.get("at")
    if isinstance(at, float) and 0 <= now - at < MEMO_SECONDS:
        return str(_MEMO.get("verdict") or UNKNOWN)
    answer = verdict(_read(probe))
    _MEMO.update({"at": float(now), "verdict": answer})
    return answer


def _read(probe: Optional[Probe]) -> dict:
    try:
        return (probe or read_power)()
    except Exception:  # noqa: BLE001 - a power probe must never break the pass
        return {}


def observed_verdict(now: Optional[float] = None) -> Optional[str]:
    """本进程**最近一次观察到**的判决，不探测；从未观察过 / 已过期 → None。

    投影侧（dashboard）专用：看板只说观察到的事（宪法第 3 条），绝不为了画一个
    chip 再起三个子进程——派发闸每 pass 先跑，缓存恒是新鲜的。
    """
    now = time.monotonic() if now is None else now
    at = _MEMO.get("at")
    if isinstance(at, float) and 0 <= now - at < MEMO_SECONDS:
        return str(_MEMO.get("verdict") or UNKNOWN)
    return None


def require_awake(cfg: object) -> bool:
    """`autodispatch.require_awake` 旋钮（默认 true，§71.1）。"""
    return bool(policy.autodispatch_config(cfg)["require_awake"])


def machine_asleep(cfg: object, probe: Optional[Probe] = None,
                   log: Optional[Callable[[str], None]] = None) -> bool:
    """§71.1 派发闸：True = 这台机器不在清醒态，本 pass 的 approved 卡一张都不派。

    `unknown` → False（fail-open）。旋钮关掉 → False 且不探测。判决**变化**时
    写一行日志（每 pass 一行会把 actd.log 刷满；探不到状态也要说话——不然一个
    永远解析不出的探针会让整条闸静默成 no-op，这正是本条法的审查焦点）。
    """
    if not require_awake(cfg):
        return False
    before = _MEMO.get("verdict")
    answer = current_verdict(probe=probe)
    if log is not None and answer != before:
        log(f"power: machine {answer}"
            + (" (probe unreadable — dispatching anyway, §71.1 fail-open)"
               if answer == UNKNOWN else ""))
    return answer == ASLEEP


# --------------------------------------------------------------------------- #
# §71.2 诚实耗时：每 pass 量一次挂起时长，记到在跑的卡上
# --------------------------------------------------------------------------- #
def sample_suspension(wall: Optional[float] = None,
                      mono: Optional[float] = None) -> float:
    """上一次采样到现在的**真实挂起秒数** = wall 前进量 − monotonic 前进量。

    macOS 的 monotonic 走 mach_absolute_time，睡眠期间停摆，wall 照走——差值
    就是机器睡着的时长（同 `alerts._suspended_seconds` 的算术，各用各的基线）。
    首次采样 / 基线缺失 → 0.0；负数（时钟回拨）收敛到 0.0。
    """
    wall = time.time() if wall is None else wall
    mono = time.monotonic() if mono is None else mono
    last_wall, last_mono = SUSPEND_STATE["last_wall"], SUSPEND_STATE["last_mono"]
    SUSPEND_STATE["last_wall"], SUSPEND_STATE["last_mono"] = wall, mono
    if last_wall is None or last_mono is None:
        return 0.0
    return max(0.0, (wall - last_wall) - (mono - last_mono))


def credit_sleep(d, seconds: float) -> int:
    """把测到的挂起时长记到在跑的卡上（§71.2）：`execution.slept_seconds` 累加、
    `execution.sleep_interrupted` 立旗；返回落账卡数。

    门槛 `SLEEP_GAP_SECONDS` 以下什么都不做（一次写盘都不发生）。写者是 actd
    主循环自己（§44 单写者），`d.save` 是同一把 seam。
    """
    if seconds < SLEEP_GAP_SECONDS:
        return 0
    n = 0
    for req in registry.load_all():
        if req.status != registry.State.EXECUTING.value:
            continue
        ex = dict(req.execution or {})
        if not ex.get("session_id"):
            continue
        ex["slept_seconds"] = _int_or_zero(ex.get("slept_seconds")) + int(seconds)
        ex["sleep_interrupted"] = True
        req.execution = ex
        d.save(req)
        n += 1
    return n


def _int_or_zero(value: object) -> int:
    try:
        return max(0, int(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def sample_pass(d, wall: Optional[float] = None, mono: Optional[float] = None) -> int:
    """actd.run_once 顶部的一次采样 + 落账（§71.2）。绝不抛。"""
    try:
        return credit_sleep(d, sample_suspension(wall, mono))
    except Exception as e:  # noqa: BLE001 - bookkeeping must never kill the pass
        d.log(f"power: sleep accounting FAILED: {e}")
        return 0
