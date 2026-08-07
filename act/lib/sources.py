"""源开关真源（single source of truth for source on/off）— CONTRACT §46.

历史上「一个源开没开」有四套并存的判据：``features.<source>_radar`` flag、
``sources.gmail.enabled``（→ ``cfg.gmail_enabled``）、Swift 端「凭证文件非空」
的 intent 猜测、launchd plist 是否存在。四套判据互相打架（关了 flag 却装着
plist；删了 plist 下次 install.sh 又装回来），没有一处能回答「这个源现在到底
开没开」。本模块就是那一处：

    enabled(cfg, source) = cfg.feature("<source>_radar") AND cfg.<source>_enabled

（合取——两个既有开关一个都不废除，任一为 false 即关；没有对应
``<source>_enabled`` 字段的源按 True 处理，flag 单独裁决。）

读者：三个雷达的入口 gate、actd 的 liveness 巡检（§46）、dashboard 的
``radar_sources`` 投影、install.sh 的 plist 安装闸门（经本模块的 CLI 入口）。

CLI（install.sh 防复活闸门用）::

    python3 -m act.lib.sources --enabled gmail
    # exit 0 = enabled, 1 = disabled, 2 = unknown source / bad invocation

依赖只有 act.lib.config（stdlib+PyYAML 白名单内）；绝不触网、绝不写文件。
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from act.lib import config

# 已归一的源集合 — dashboard `radar_sources` 投影与 actd liveness 巡检都遍历它。
SOURCES: tuple = ("gmail", "slack", "obsidian")

# liveness 阈值（秒）：开着的源 last_ok（缺失时 last_attempt）比现在早超过
# 这个数 → 判「源死亡」（§46）。取值 ≈ 调度间隔 × 大余量，避开正常抖动：
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
    # gmail 有 sources.gmail.enabled（cfg.gmail_enabled）；slack/obsidian 目前
    # 没有对应字段 —— getattr 默认 True，将来加字段无需改这里（add-only）。
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


def is_stale(source: str, entry: Optional[dict],
             now: Optional[_dt.datetime] = None) -> bool:
    """开着的源是否已「死亡」（超过 LIVENESS_THRESHOLDS 没有成功信号）。

    信号取 ``last_ok``，从未成功过则退而取 ``last_attempt``（区分「配好后
    一直失败」与「刚装上还没跑过」）；条目缺失或两个时间戳都没有 → False
    （没有基线就不能诚实地宣布死亡——配置类静默失败由诊断卡负责，§0 第 3 条）。
    调用方自行保证只对 enabled 的源调用（关掉的源天然不进巡检循环）。
    """
    threshold = LIVENESS_THRESHOLDS.get(source)
    if threshold is None or not isinstance(entry, dict):
        return False
    signal = _parse_iso(entry.get("last_ok")) or _parse_iso(entry.get("last_attempt"))
    if signal is None:
        return False
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    return (now - signal).total_seconds() > threshold


def main(argv: Optional[list] = None) -> int:
    """CLI 入口（install.sh plist 闸门）：--enabled <source> → exit code."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="act.lib.sources",
        description="query the source-switch single source of truth")
    parser.add_argument("--enabled", metavar="SOURCE",
                        help="exit 0 if SOURCE is enabled, 1 if disabled, "
                             "2 if unknown")
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
    print("on" if on else "off")
    return 0 if on else 1


if __name__ == "__main__":
    raise SystemExit(main())
