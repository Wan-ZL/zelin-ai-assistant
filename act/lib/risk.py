"""Trust/risk 判定 — v-next 信任矩阵的最小实现层(W17/W18).

两件事,都只读不写:
  * ``effective_tier(card)`` — W17 cheap layer:``origin_trust == "external"``
    的卡强制按 T2(需文字确认)对待,并携带 forced-expand 标记(外部来源卡在
    审批前必须走 plan expansion,不允许裸批)。声明 tier 不被改写——这是
    **投影/调度层的判定**,registry YAML 里的 ``tier`` 字段保持原样。
  * ``remote_direct_run_allowed(cfg)`` — W18 远程直跑闸门:webui/syncd 这类
    网络 ingress 放行 capture ``mode:"run"`` 与否。默认 False(fail-closed),
    仅 config.yaml ``remote.allow_direct_run: true`` 显式开启。

法源:docs/design/vnext-amendments.md §W17/§W18(ratification-ready 草案;
CONTRACT.md 本 PR 不动)。stdlib only。
"""
from __future__ import annotations

from typing import Any, NamedTuple, Optional

from act.lib import config

# W17 强制档位 — 外部来源一律抬到最严的一档(T2 = 需文字确认,§7 语义不变)。
FORCED_TIER = "T2"

# origin_trust 词表(store2 schema.sql 同款 CHECK 集合;add-only)。
TRUST_HAND = "hand"
TRUST_EXTERNAL = "external"


class EffectiveTier(NamedTuple):
    """effective_tier() 的返回:生效档位 + 是否强制 plan expansion + 原因。"""

    tier: str
    forced_expand: bool
    reason: Optional[str]


def _field(card: Any, key: str) -> Any:
    """卡片字段读取 — 同时接受 dict(raw YAML / store2 row)和 Requirement。"""
    if isinstance(card, dict):
        return card.get(key)
    return getattr(card, key, None)


def effective_tier(card: Any) -> EffectiveTier:
    """W17:外部来源卡的生效档位 = T2 + forced-expand;其余照声明档位。

    只有**显式** ``origin_trust: external`` 触发强制——缺失该字段的存量卡
    (v0.10.3 registry 尚无此字段)保持声明 tier 不变,避免一夜之间把全部
    历史卡抬成 T2。fail-closed 的责任在铸卡侧(radar/capture 必须盖章)与
    auto-dispatch 侧(缺 origin_trust 一律不许自动派发)——见 amendments
    §W17 的 TODO(contract)。
    """
    declared = str(_field(card, "tier") or "T1").strip() or "T1"
    trust = str(_field(card, "origin_trust") or "").strip().lower()
    if trust == TRUST_EXTERNAL:
        return EffectiveTier(FORCED_TIER, True, "origin_trust=external")
    return EffectiveTier(declared, False, None)


def remote_direct_run_allowed(cfg: Optional[config.Config] = None) -> bool:
    """W18:远程 ingress(webui/syncd)是否放行 capture ``mode:"run"``。

    默认 False——direct-run 跳过人审预览(§34),不该从网络面默认进来。
    任何读取/解析失败一律 False(fail-closed,绝不因坏 config 打开闸门)。
    """
    try:
        if cfg is None:
            cfg = config.load_config()
        return bool(getattr(cfg, "remote_allow_direct_run", False))
    except Exception:  # noqa: BLE001 — 坏 config 不许打开闸门
        return False
