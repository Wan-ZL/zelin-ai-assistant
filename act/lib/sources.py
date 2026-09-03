"""源开关真源（single source of truth for source on/off）— CONTRACT §48.

历史上「一个源开没开」有四套并存的判据：``features.<source>_radar`` flag、
``sources.gmail.enabled``（→ ``cfg.gmail_enabled``）、Swift 端「凭证文件非空」
的 intent 猜测、launchd plist 是否存在。四套判据互相打架（关了 flag 却装着
plist；删了 plist 下次 install.sh 又装回来），没有一处能回答「这个源现在到底
开没开」。本模块就是那一处：

    enabled(cfg, source) = cfg.feature("<source>_radar") AND cfg.<source>_enabled

（合取——两个既有开关一个都不废除，任一为 false 即关；没有对应
``<source>_enabled`` 字段的源按 True 处理，flag 单独裁决。）

读者：三个雷达的入口 gate、actd 的 liveness 巡检（§48）、dashboard 的
``radar_sources`` 投影、install.sh 的 plist 安装闸门（经本模块的 CLI 入口）。

CLI（install.sh 防复活闸门用）::

    python3 -m act.lib.sources --enabled gmail
    # exit 0 = enabled（stdout "on"）, 3 = disabled（stdout "off"）,
    # 2 = unknown source / bad invocation
    # exit 1 被刻意空出：那是 python 崩溃的环境码（ModuleNotFoundError、
    # 缺 PyYAML、任何未捕获异常）——「关」必须独占一个不会被故障撞上的
    # 出口码，install.sh 才能对所有故障类 fail-open（照常安装）。

依赖只有 act.lib.config（stdlib+PyYAML 白名单内）；绝不触网、绝不写文件。
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from act.lib import config

# 已归一的源集合 — dashboard `radar_sources` 投影与 actd liveness 巡检都遍历它。
SOURCES: tuple = ("gmail", "slack", "obsidian")

# liveness 阈值（秒）：开着的源 last_ok（缺失时 last_attempt）比现在早超过
# 这个数 → 判「源死亡」（§48）。取值 ≈ 调度间隔 × 大余量，避开正常抖动：
# - gmail  launchd StartInterval=300s  → 6h（72 个周期没成功一次才算死）
# - slack  launchd StartInterval=180s  → 6h（同量级，统一 6h 好记）
# - obsidian cron */30（且随 Mac 合盖/夜间停摆是常态）→ 36h（沿用 72x 比例，
#   周末合盖不误报）
LIVENESS_THRESHOLDS: dict = {
    "gmail": 6 * 3600,
    "slack": 6 * 3600,
    "obsidian": 36 * 3600,
}


def enabled(cfg: Optional[config.Config], source: str) -> bool:
    """真源判据：feature flag 与 sources.<source>.enabled 的合取。

    未知 source 返回 False（fail-closed——真源不知道的源不能算开）。
    """
    if source not in SOURCES:
        return False
    if cfg is None:
        cfg = config.load_config()
    flag = cfg.feature(f"{source}_radar") if hasattr(cfg, "feature") else True
    # 三个源都有 sources.<src>.enabled（cfg.<src>_enabled，config.py 解析）；
    # getattr 默认 True 兜底老 Config 对象（add-only）。
    per_source = bool(getattr(cfg, f"{source}_enabled", True))
    return bool(flag) and per_source


def _parse_iso(value) -> Optional[_dt.datetime]:
    """health 条目里的 ISO 时间戳 → aware datetime；坏值/缺失 → None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def has_baseline(entry: Optional[dict]) -> bool:
    """health 条目是否带任何可解析的活动时间戳（liveness 的评判基线）。

    False = 雷达从未落笔（条目缺失或两个时间戳皆空/坏）——``is_stale`` 对这种
    形态返回 False（没有基线不能诚实宣布死亡），但「开着却持续无基线」本身
    是另一条死路（plist 写成而 launchctl load 失败，§48.3 的兜底台账消费方）。
    """
    if not isinstance(entry, dict):
        return False
    return any(_parse_iso(entry.get(k)) is not None
               for k in ("last_ok", "last_attempt"))


def is_stale(source: str, entry: Optional[dict],
             now: Optional[_dt.datetime] = None) -> bool:
    """开着的源是否已「死亡」（超过 LIVENESS_THRESHOLDS 没有任何活动信号）。

    信号取 ``last_ok`` 与 ``last_attempt`` 里**较新**的那个——真死亡（plist
    被删/调度停摆）两个时间戳一起停；「配好后一直失败」的雷达 last_attempt
    仍在前进，属诊断卡（skip_reason）的辖区而非死亡告警，合盖睡醒后的第一批
    pass 也因此少一类误报。条目缺失或两个时间戳都没有 → False（没有基线就
    不能诚实地宣布死亡——配置类静默失败由诊断卡负责，§0 第 3 条）。
    调用方自行保证只对 enabled 的源调用（关掉的源天然不进巡检循环）。
    """
    threshold = LIVENESS_THRESHOLDS.get(source)
    if threshold is None or not isinstance(entry, dict):
        return False
    signal = _latest_signal(entry)
    if signal is None:
        return False
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    return (now - signal).total_seconds() > threshold


def _latest_signal(entry: dict) -> Optional[_dt.datetime]:
    """较新的那个活动时间戳（last_ok / last_attempt）；两个都缺/坏 → None。"""
    stamps = [t for t in (_parse_iso(entry.get("last_ok")),
                          _parse_iso(entry.get("last_attempt"))) if t]
    return max(stamps) if stamps else None


def main(argv: Optional[list] = None) -> int:
    """CLI 入口（install.sh plist 闸门）：--enabled <source> → exit code."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="act.lib.sources",
        description="query the source-switch single source of truth")
    parser.add_argument("--enabled", metavar="SOURCE",
                        help="exit 0 if SOURCE is enabled, 3 if disabled, "
                             "2 if unknown (1 is reserved: python crash)")
    args = parser.parse_args(argv)
    if not args.enabled:
        parser.print_usage()
        return 2
    source = args.enabled.strip().lower()
    if source not in SOURCES:
        print(f"unknown source: {source} (known: {', '.join(SOURCES)})")
        return 2
    try:
        cfg = config.load_config()
    except Exception:  # noqa: BLE001 - 坏 config 按默认（全开）处理，别拦安装
        cfg = config.Config()
    on = enabled(cfg, source)
    # 「off」独占 exit 3 + stdout 字面量（install.sh 双重校验），exit 1 留给
    # python 自身崩溃 —— 探针故障与「关」必须不同码，fail-open 才成立。
    print("on" if on else "off")
    return 0 if on else 3


if __name__ == "__main__":
    raise SystemExit(main())
