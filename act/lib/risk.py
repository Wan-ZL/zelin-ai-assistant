"""Trust/risk 判定 — v-next 信任矩阵的最小实现层(W17/W18).

两件事,都只读不写:
  * ``effective_tier(card)`` — W17 cheap layer:外部出身的卡强制按 T2(需
    文字确认)对待,并携带 forced-expand 标记(外部来源卡在审批前必须走
    plan expansion,不允许裸批)。外部出身 = 显式章 ``origin_trust ==
    "external"`` **或** sources 现算(policy.classify_origin)为 external
    ——缺章/存量卡不再按「缺章 = 声明档」放行(v0.48.1 修订,§50)。声明
    tier 不被改写——这是**投影/调度层的判定**,registry YAML 里的
    ``tier`` 字段保持原样。
  * ``remote_direct_run_allowed(cfg)`` — W18 远程直跑闸门:webui/syncd 这类
    网络 ingress 放行 capture ``mode:"run"`` 与否。默认 False(fail-closed),
    仅 config.yaml ``remote.allow_direct_run: true`` 显式开启。

契约:docs/CONTRACT.md §50(W17 effective tier)/§41 v0.48 修订(W18 远程直跑
闸门)；起草稿 docs/design/vnext-amendments.md §W17/§W18。stdlib only。
"""
from __future__ import annotations

from typing import Any, NamedTuple, Optional

from act.lib import config, policy

# W17 强制档位 — 外部来源一律抬到最严的一档(T2 = 需文字确认,§7 语义不变)。
FORCED_TIER = "T2"

# origin_trust 档位常量 — 转引 policy 的 canonical 四值词表(= store2
# schema.sql 的 CHECK 集),本模块只消费其中两档;字面量不再各写一份。
TRUST_HAND = policy.HAND
TRUST_EXTERNAL = policy.EXTERNAL


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
    """W17:外部出身卡的生效档位 = T2 + forced-expand;其余照声明档位。

    外部出身取**并集**判定,两个洗白方向都关死(v0.48.1 修订,§50):
      * 显式章 ``origin_trust: external``——belt-and-braces,即便 sources
        被手改成 hand,章不被洗掉;
      * sources 现算(policy.classify_origin)为 external——缺章/存量卡
        (v0.10.3 registry 尚无此字段)不再按「缺章 = 保持声明档」放行:
        章可以缺,出身不会缺,与调度侧 may_auto_dispatch「不读章、每次从
        sources 现算」同一条纪律(堵 stamp-less 手改 YAML 裸批的洞)。
    空 sources 的存量卡现算为 proposed,声明档位保持不变——「全部历史卡
    一夜抬成 T2」不会发生,抬档的只有真判 external 的卡:slack/gmail,以及
    **任何不在 policy.CHANNEL_CLASS 表内的 channel**(fail-closed)。后者是
    刀刃:每一个真实的生产端 channel 都必须登记在表内,否则一个 proposed
    级来源(如 digest/weekly-digest)会被静默错判 external、错抬 T2——所以
    新增任何铸卡 channel 都要同步 CHANNEL_CLASS(v0.48.1 补收 digest/
    weekly-digest 正是此雷,见 policy.py 表内注)。
    """
    declared = str(_field(card, "tier") or "T1").strip() or "T1"
    # 注意:这里**故意不走** policy.normalize_origin —— 那个函数把一切不认识
    # 的值收敛成 external(读侧 fail-closed),用在这里会把缺章/脏章的存量卡
    # 一夜之间抬成 T2 并强制扩写,正是上面那段注释禁止的事。抬档只认显式
    # external;fail-closed 的位置在铸卡侧与 auto-dispatch 侧(§50)。
    trust = str(_field(card, "origin_trust") or "").strip().lower()
    if trust == TRUST_EXTERNAL:
        return EffectiveTier(FORCED_TIER, True, "origin_trust=external")
    if policy.classify_origin(_field(card, "sources") or []) == policy.EXTERNAL:
        return EffectiveTier(FORCED_TIER, True, "sources=external")
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
