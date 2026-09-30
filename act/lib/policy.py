"""policy — origin trust matrix + dispatch queue vocabulary（v-next 信任矩阵，纯函数）.

契约：docs/CONTRACT.md §50（信任矩阵）/ §51（queued 词表 + 并发上限；**免批 lane
全部退役**）/ §71.1（睡眠感知派发：`autodispatch.require_awake` 旋钮 +
`machine_asleep` 排队原因）/ §78（提案车道退役）。

§51 的两条免批 lane 都已退役：hand lane 并入 §78（owner decision D80.4），§65
self_improve lane 整条删除（owner decision D86，2026-09-30：「你把这个自动读 issue
写 PR 的循环功能完整删掉」）。自此没有任何卡能绕过 owner 的点击进 approved——
``may_auto_dispatch`` 与它的全部天花板随最后一条 lane 一起删除，拒绝原因 token
词表 tombstone 在下方，永不复用。

仍在的部分：

- origin 四类词表与 channel → class 裁决表（铸卡侧拿它给 ``origin_trust`` 盖章；
  W17 effective tier 的投影判定在 act/lib/risk.py）；
- 屏幕内容永不铸卡（§45 不变——"screen" 在本表只是防御行，正常永不出现）；
- `autodispatch:` 配置块（`max_concurrent` / `require_awake` 仍喂
  dispatch_approved；`enabled` / `notify` 自 D86 起无人读）；
- queued 子状态的原因 chip（``queued_reason``）。

设计沿袭 act/lib/provenance.py 的裁决表习惯：显式、有限、可枚举的纯数据 +
normalize 收敛 + 全函数（任意垃圾输入都有确定裁决，绝不 raise）。一切不认识的
channel **fail-closed 落 external**——宁可错关，不可错开。本模块只做裁决，不做
I/O、不写 registry（§44 单写者不变）。

预算天花板 retired v0.48.7——owner decision D9。钱的可见性由 §7/§41 的
`require_text_confirm_above_usd` 文字确认线承担（那是审批语义不是预算）。
"""
from __future__ import annotations

from typing import Optional

# --------------------------------------------------------------------------- #
# 域：origin trust classes（四类，locked）
# --------------------------------------------------------------------------- #
HAND = "hand"          # 用户手打（quick capture / Slack self-DM / iMessage 自发）
PROPOSED = "proposed"  # AI 自提（digest 建议、诊断卡、会话挖掘、拆分卡）
MEETING = "meeting"    # 会议音频/笔记出生（obsidian radar 主通道）
EXTERNAL = "external"  # 外部第三方（Slack 他人消息 / Gmail）——最不信任
ORIGINS = (HAND, PROPOSED, MEETING, EXTERNAL)

# 信任序（越大越信任）；聚合规则 = 全部来源取最小信任（最不信任者定卡）。
_TRUST_RANK = {HAND: 3, PROPOSED: 2, MEETING: 1, EXTERNAL: 0}

# -- 法条本体：sources[].channel -> trust class ------------------------------ #
# channel 字面量清单来自两棵树的写入端盘点（live v0.47 为准，worktree v0.10.3
# 缺的行按 forward-compat 收录；出处见 vnext-amendments.md §M1.a/§M1.d）：
#   quick / quick_capture — act/lib/quick_capture.py、actd 快速捕获（含 Slack
#       self-DM 与 iMessage 自发通道：两者都经 quick_capture.capture 落卡）
#   agent_capture / remote_capture — T-28 ingress 落款：HTTP 写入面 via 标记
#       为 "agent"（boardctl 自报）/"remote"（act.webui 远程面）的 capture，
#       actd 按落款盖捕获源 channel——AI/远程投递的候选一律回人工审批
#   split — actd split_note：车主拆折叠备注成新卡（文本非手打，保守要审批）
#   digest / weekly-digest — AI 自提的 digest 建议卡（act/digest.py 与
#       act/weekly_digest.py 的 SOURCE_CHANNEL 常量，逐字同款）：AI 从积累的
#       ingest 里挖出的建议，天然是 proposed（需 owner 批准）。**遗漏即执行
#       面 bug**：W17 自 sources 现算 effective_tier 后（§50 v0.48.1），漏收
#       这两个 channel 会 fail-closed 成 external，把存量 digest 卡一夜错抬成
#       T2+强制扩写——两个常量必须与本表同步。
#   analytics / claude_code / radar-diagnostic / radar-parse-degraded — AI 自
#       提形态（会话挖掘卡、§40/§47.2 诊断降级卡；analytics 是遥测建议通道）
#   meeting / audio — obsidian radar 的会议音频与笔记通道
#   slack / gmail — 第三方消息（radar_slack 非 self-DM 路径、radar_gmail）
#   screen — §45 防御行：屏幕永不铸卡，真出现即异常，按最不信任处理
#   self_improve — §70 每日循环 🤖 卡的铸卡渠道（D86 起只剩素材库提案；存量的
#       §65 通道卡 / PR 跟进卡也带它），producer 硬编码写入——act/lib/daily_loop.py
#       SOURCE_CHANNEL 逐字同款，无 LLM 参与 = write-locked：出身 proposed（AI 自提），
#       需 owner 点击。原先的免批第二条 lane 随 §65 retired D86；本行保留，否则这些卡
#       会 fail-closed 落 external。
SELF_IMPROVE_CHANNEL = "self_improve"
CHANNEL_CLASS: dict = {
    "quick": HAND,
    "quick_capture": HAND,
    "agent_capture": PROPOSED,
    "remote_capture": PROPOSED,
    "split": PROPOSED,
    "digest": PROPOSED,
    "weekly-digest": PROPOSED,
    "analytics": PROPOSED,
    "claude_code": PROPOSED,
    "radar-diagnostic": PROPOSED,
    "radar-parse-degraded": PROPOSED,
    SELF_IMPROVE_CHANNEL: PROPOSED,
    "meeting": MEETING,
    "audio": MEETING,
    "slack": EXTERNAL,
    "gmail": EXTERNAL,
    "screen": EXTERNAL,
}


def channel_class(channel: object) -> str:
    """单个 channel 值 -> trust class。全函数：非字符串/大小写混乱/臆造值
    一律 fail-closed 落 EXTERNAL（同 executor 白名单纪律：未知渠道不享信任）。"""
    if isinstance(channel, str):
        return CHANNEL_CLASS.get(channel.strip().lower(), EXTERNAL)
    return EXTERNAL


def normalize_origin(value: object) -> str:
    """持久化过的 origin_trust 字段回读收敛：不认识的值落 EXTERNAL。"""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ORIGINS:
            return v
    return EXTERNAL


def classify_origin(card_sources: object,
                    capture_channel: object = None) -> str:
    """卡片出身裁决：sources[].channel（+ 可选的捕获面 channel）取最小信任。

    - 空 sources 且无 capture_channel -> PROPOSED（无来源 = AI 自铸卡形态，
      如 digest 建议）；
    - sources 不是 list（畸形持久化）-> 按一条未知来源处理 -> EXTERNAL；
    - 条目不是 dict / 缺 channel -> 该条按未知 -> EXTERNAL；
    - 混合来源（fold 并入过外部渠道）由最不信任的渠道定卡：手打卡被 slack
      来源 fold 过 -> external——外来文本已经上卡，自动开跑资格随之消失。
    全函数，永不 raise。
    """
    classes = _source_classes(card_sources)
    if capture_channel is not None:
        classes.append(channel_class(capture_channel))
    if not classes:
        return PROPOSED
    return min(classes, key=lambda c: _TRUST_RANK[c])


def _source_classes(card_sources: object) -> list:
    """sources[] → 每条来源的 trust class（空 = []；畸形 sources = 一条 EXTERNAL）。"""
    if not card_sources:
        return []
    if not isinstance(card_sources, (list, tuple)):
        return [EXTERNAL]   # 畸形 sources：fail-closed
    return [channel_class(s.get("channel") if isinstance(s, dict) else None)
            for s in card_sources]


# --------------------------------------------------------------------------- #
# autodispatch 配置（config.yaml `autodispatch:` 块，全 add-only）
# --------------------------------------------------------------------------- #
AUTODISPATCH_DEFAULTS: dict = {
    "enabled": True,            # retired D86, unread（§51 免批 lane 全部退役）；兼容键
    "max_concurrent": 3,        # 派发并发上限（超出 -> queued: concurrency）
    "notify": True,             # retired D86, unread（免批派发通知随 lane 删除）；兼容键
    "require_awake": True,      # §71.1：机器不在清醒态就不派发（探不到 = 按醒着）
    # daily_budget_usd — retired v0.48.7（D9）：旧 config 里残留的键被静默忽略。
}
# 逐键 bool 收敛的键（脏值一律 bool() —— 与历史行为逐字相同）
_AUTODISPATCH_BOOL_KEYS = ("enabled", "notify", "require_awake")


def _num(value: object) -> Optional[float]:
    """宽松数值收敛：int/float/数字字符串 -> float；bool/垃圾 -> None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _int(value: object) -> Optional[int]:
    n = _num(value)
    return int(n) if n is not None else None


def _raw_block(cfg: object, key: str) -> dict:
    """cfg.raw 或裸 dict 里取一个配置块；不是 dict 一律当空块。"""
    raw = getattr(cfg, "raw", None)
    if not isinstance(raw, dict) and isinstance(cfg, dict):
        raw = cfg
    block = raw.get(key) if isinstance(raw, dict) else None
    return block if isinstance(block, dict) else {}


def autodispatch_config(cfg: object) -> dict:
    """读 `autodispatch:` 配置块（cfg.raw 或裸 dict），脏值逐键回退默认——
    配置永远解析出一个完整合法的块，绝不 raise（宪法第 11 条口径）。"""
    block = _raw_block(cfg, "autodispatch")
    out = dict(AUTODISPATCH_DEFAULTS)
    for key in _AUTODISPATCH_BOOL_KEYS:
        if key in block:
            out[key] = bool(block[key])
    cap = _int(block.get("max_concurrent"))
    if cap is not None and cap >= 1:
        out["max_concurrent"] = cap
    return out


def is_self_improve_sources(sources: object) -> bool:
    """sources 非空且**每一条**都是 `self_improve` 渠道。混入任何别的渠道
    （hand 卡被 fold、slack 来源并入……）即失格——「混合来源取最小信任」在
    这条 lane 上的同构：搭便车两个方向都关死。"""
    if not isinstance(sources, (list, tuple)) or not sources:
        return False
    return all(isinstance(s, dict)
               and channel_class_key(s.get("channel")) == SELF_IMPROVE_CHANNEL
               for s in sources)


def _lower_key(value: object) -> str:
    """字符串字段归一（strip+lower）；非字符串给空串——channel / type / target_kind 同尺。"""
    return value.strip().lower() if isinstance(value, str) else ""


def channel_class_key(channel: object) -> str:
    """channel 值归一（strip+lower）；非字符串给空串。"""
    return _lower_key(channel)


# --------------------------------------------------------------------------- #
# may_auto_dispatch — retired D86（§51 第二条 lane 墓碑）
# --------------------------------------------------------------------------- #
# Retired reason tokens (D80.4 hand lane, D86 self_improve lane) — never reuse:
# ok, disabled, origin:proposed, origin:meeting, origin:external, t2_confirm,
# outbound, repo:new, repo:none, repo:missing, cost:unknown, ok:hand,
# ok:self_improve, self_improve:disabled, self_improve:paused,
# self_improve:needs_mcp, self_improve:repo_mismatch.
# (cost:over_ceiling / budget:unknown / budget:exhausted retired earlier, D9.)
# 存量卡上残留的 `execution.auto_dispatch_block` 值照常透传投影（add-only），不再写。


# --------------------------------------------------------------------------- #
# queued_reason — 合并运行列 queued 子状态的原因 chip（locked 词表）
# --------------------------------------------------------------------------- #
# "budget" retired v0.48.7（D9）——词表 tombstone，token 永不复用。
# "machine_asleep" 加入 v0.48.x（§71.1）——add-only，位置即优先级。
QUEUED_REASONS = ("dependency", "machine_asleep", "concurrency")


def queued_reason(card: object, state: object) -> Optional[str]:
    """approved-未派发卡的排队原因 -> {dependency, machine_asleep, concurrency}
    或 None（无阻塞，纯粹还没轮到/上次派发失败在退避）。

    ``state`` 是调用方（actd/dashboard 投影）算好的快照 dict，键全部可选，
    缺键 = 跳过该项检查（policy 不做 I/O，不自己数并发、也不自己探电源）：
      blocked_by        — 非空（list/str）= 有未完结的依赖卡 -> dependency
      machine_asleep    — 真 = §71.1 闸按住了本 pass 的全部派发 -> machine_asleep
      running + max_concurrent       — 在跑数达上限 -> concurrency
    优先级 dependency > machine_asleep > concurrency：chip 只有一个位置，报最
    「粘」的阻塞（依赖不随时间自愈；机器醒来要等人；并发最快松动）。旧快照里
    残留的 today_spend / daily_budget_usd 键不认、不 raise（D9 之后没有「等预算」
    这回事）。全函数：垃圾 state/card 只会让检查被跳过，绝不 raise。
    """
    st = state if isinstance(state, dict) else {}
    if st.get("blocked_by"):
        return "dependency"
    if st.get("machine_asleep"):
        return "machine_asleep"
    return "concurrency" if _at_concurrency_cap(st) else None


def _at_concurrency_cap(st: dict) -> bool:
    running = _int(st.get("running"))
    cap = _int(st.get("max_concurrent"))
    return running is not None and cap is not None and running >= cap
