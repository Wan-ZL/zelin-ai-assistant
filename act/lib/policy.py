"""policy — origin trust matrix + auto-dispatch ceilings（v-next 信任矩阵，纯函数）.

契约：docs/CONTRACT.md §50（信任矩阵）/ §51（自动派发天花板 + queued 词表）。

Owner 拍板（2026-08-30，见 docs/design/vnext-amendments.md 的修宪草案）：

- 手打捕获与 Slack self-DM 的卡 **自动派发**（免审批开跑）；
- AI 自提（digest/诊断/会话挖掘）与会议音频出生的卡照旧走人工审批；
- 外部 Slack/Gmail 出生的卡：审批 + 强制 plan 扩写（W17 cheap layer——
  effective tier 的投影判定在 act/lib/risk.py，本模块只产 origin 分类，
  铸卡侧拿它给 ``origin_trust`` 盖章）；
- 屏幕内容永不铸卡（§45 不变——"screen" 在本表只是防御行，正常永不出现）。

设计沿袭 act/lib/provenance.py 的裁决表习惯：显式、有限、可枚举的纯数据 +
normalize 收敛 + 全函数（任意垃圾输入都有确定裁决，绝不 raise）。卡片来源
channel 由各 radar/capture 写入端硬编码（quick/slack/gmail/meeting/...），
一切不认识的 channel **fail-closed 落 external**——最不信任、要审批还要扩写，
与 executor 遥测 provenance 白名单（live v0.47 _USER_ORIGIN_CHANNELS）同一
条纪律：宁可错关，不可错开。

本模块只做裁决，不做 I/O、不写 registry（§44 单写者不变）：actd 主循环拿着
裁决去改状态；repo 存在性检查经由可注入的 ``path_exists`` seam（测试绝不碰
真文件系统）。

预算天花板（`daily_budget_usd` 单卡上限 + 当日累计台账 `today_spend`）retired
v0.48.7——owner decision D9（docs/design/vnext2-plan.md：「取消一切预算……钱是
足够的」）。钱的可见性由 §7/§41 的 `require_text_confirm_above_usd` 文字确认线
承担（那是审批语义不是预算），卡上的 cost_estimate_usd 仍作披露展示。

第二条免批 lane（§64，P6；owner 决策 D7/D8/D9，§0 第 12 条修宪）：出身仍是
proposed（四类词表不动），**资格**另裁——sources 全部是写死的 `self_improve`
渠道 **且** `target_repo` 的 realpath 就是本仓库（`self_improve.repo_path`，
默认安装根）才免批；type/target_repo 之类 LLM 可写字段单独永远开不了这条
lane（§50 M1.d 教训：判据必须锚在 producer 硬编码的字段上）。
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from act.lib import config

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
#   self_improve — §64 自动草稿 PR 通道的**唯一**铸卡渠道（每日循环提案 /
#       PR 跟进卡，producer 硬编码写入）：出身仍是 proposed（AI 自提），免批
#       资格由 may_auto_dispatch 的第二条 lane 另裁——见模块 docstring。
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
    classes: list = []
    if card_sources:
        if isinstance(card_sources, (list, tuple)):
            for s in card_sources:
                chan = s.get("channel") if isinstance(s, dict) else None
                classes.append(channel_class(chan))
        else:
            classes.append(EXTERNAL)   # 畸形 sources：fail-closed
    if capture_channel is not None:
        classes.append(channel_class(capture_channel))
    if not classes:
        return PROPOSED
    return min(classes, key=lambda c: _TRUST_RANK[c])


# --------------------------------------------------------------------------- #
# autodispatch 配置（config.yaml `autodispatch:` 块，全 add-only）
# --------------------------------------------------------------------------- #
AUTODISPATCH_DEFAULTS: dict = {
    "enabled": True,            # 总开关：关掉 = 全部回人工审批
    "max_concurrent": 3,        # 自动派发并发上限（超出 -> queued: concurrency）
    "notify": True,             # 观察模式：每次自动派发发一条通知
    # daily_budget_usd — retired v0.48.7（D9）：旧 config 里残留的键被静默忽略。
}


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


def autodispatch_config(cfg: object) -> dict:
    """读 `autodispatch:` 配置块（cfg.raw 或裸 dict），脏值逐键回退默认——
    配置永远解析出一个完整合法的块，绝不 raise（宪法第 11 条口径）。"""
    block: dict = {}
    raw = getattr(cfg, "raw", None)
    if not isinstance(raw, dict) and isinstance(cfg, dict):
        raw = cfg
    if isinstance(raw, dict):
        b = raw.get("autodispatch")
        if isinstance(b, dict):
            block = b
    out = dict(AUTODISPATCH_DEFAULTS)
    if "enabled" in block:
        out["enabled"] = bool(block["enabled"])
    cap = _int(block.get("max_concurrent"))
    if cap is not None and cap >= 1:
        out["max_concurrent"] = cap
    if "notify" in block:
        out["notify"] = bool(block["notify"])
    return out


# --------------------------------------------------------------------------- #
# self_improve 配置（config.yaml `self_improve:` 块，全 add-only；§64）
# --------------------------------------------------------------------------- #
SELF_IMPROVE_DEFAULTS: dict = {
    "enabled": True,        # 通道总开关：false = self_improve 卡照旧人工审批
    "repo_path": "",        # "" = 安装根（config.HOME）；比对用 realpath
    "tick_minutes": 60,     # PR 跟进巡检（owner 评论 / 红 CI / 合并 / 关闭）间隔
    "owner_logins": [],     # 额外算作 owner 的 GitHub login（gh 当前身份恒在）
    "github_repo": "",      # 显式 owner/repo；"" = 首次使用时 gh repo view 取并缓存
}


def _raw_block(cfg: object, key: str) -> dict:
    """cfg.raw 或裸 dict 里取一个配置块；不是 dict 一律当空块。"""
    raw = getattr(cfg, "raw", None)
    if not isinstance(raw, dict) and isinstance(cfg, dict):
        raw = cfg
    block = raw.get(key) if isinstance(raw, dict) else None
    return block if isinstance(block, dict) else {}


def _str_or(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _str_list(value: object) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def self_improve_config(cfg: object) -> dict:
    """读 `self_improve:` 块，脏值逐键回退默认（宪法第 11 条口径）——通道配置
    的唯一读取点（同 autodispatch_config 的纪律）。"""
    block = _raw_block(cfg, "self_improve")
    out = dict(SELF_IMPROVE_DEFAULTS)
    out["enabled"] = bool(block.get("enabled", out["enabled"]))
    out["repo_path"] = _str_or(block.get("repo_path"), "")
    minutes = _int(block.get("tick_minutes"))
    out["tick_minutes"] = (minutes if minutes is not None and minutes >= 1
                           else out["tick_minutes"])
    out["owner_logins"] = _str_list(block.get("owner_logins"))
    out["github_repo"] = _str_or(block.get("github_repo"), "")
    return out


def self_improve_repo_path(cfg: object) -> str:
    """通道唯一放行的仓库路径（未 realpath；比对时再 realpath）。默认 = 安装根
    ``config.HOME``——本软件自己的 checkout（D7：只给 zelin-ai-assistant 开）。"""
    configured = self_improve_config(cfg)["repo_path"]
    return configured or str(config.HOME)


def same_repo(a: object, b: object,
              realpath: Optional[Callable[[str], str]] = None) -> bool:
    """两个路径 realpath 后是否同一目录（`~/Projects/...` 是指向外置卷的
    symlink——v0.48.2 的 symlink 事故就在这里，必须 realpath 再比）。非字符串 /
    空串 = False（fail-closed）。``realpath`` 是测试注入缝。"""
    pa, pb = _str_or(a, ""), _str_or(b, "")
    if not pa or not pb:
        return False
    rp = realpath if realpath is not None else os.path.realpath
    return rp(os.path.expanduser(pa)) == rp(os.path.expanduser(pb))


def is_self_improve_sources(sources: object) -> bool:
    """sources 非空且**每一条**都是 `self_improve` 渠道。混入任何别的渠道
    （hand 卡被 fold、slack 来源并入……）即失格——「混合来源取最小信任」在
    这条 lane 上的同构：搭便车两个方向都关死。"""
    if not isinstance(sources, (list, tuple)) or not sources:
        return False
    return all(isinstance(s, dict)
               and channel_class_key(s.get("channel")) == SELF_IMPROVE_CHANNEL
               for s in sources)


def channel_class_key(channel: object) -> str:
    """channel 值归一（strip+lower）；非字符串给空串。"""
    return channel.strip().lower() if isinstance(channel, str) else ""


# --------------------------------------------------------------------------- #
# may_auto_dispatch — 自动派发资格闸（天花板全过才放行）
# --------------------------------------------------------------------------- #
# 拒绝原因 token（稳定机读词表；UI 文案由调用方映射）：
#   disabled          — autodispatch.enabled=false
#   origin:<class>    — 出身非 hand（proposed/meeting/external 都要人批）
#   t2_confirm        — T2 / green_sign_required / 估价高过文字确认线（§7/§41
#                       typed-confirm 语义不可被自动派发绕开）
#   outbound          — comms 类卡（可能产生对外通信，永不自动开跑）
#   repo:new          — target_kind=new（自动派发绝不建新 repo）
#   repo:none         — 卡与配置都给不出 target_repo
#   repo:missing      — 落点 repo 在磁盘上不存在（existing target_repo only）
#   cost:unknown      — 无成本估计（不可证明 <= 文字确认线，保守拒）
#   cost:over_ceiling / budget:unknown / budget:exhausted — retired v0.48.7
#                       （D9 取消预算天花板；旧卡上残留的 token 由 actd 在下一
#                       pass 按「解除即清」清掉，不再产生）
#   ok:self_improve   — 放行，且走的是 §64 lane（actd 据此选文案/通知）
#   self_improve:disabled      — self_improve.enabled=false（常态，不上卡）
#   self_improve:paused        — 通道被敏感路径护栏挂起（§64.4），等 owner 清
#   self_improve:needs_mcp     — 卡声明 needs_mcp：只能走 owner 亲批路径
#   self_improve:repo_mismatch — target_repo 的 realpath 不是本仓库（D7）
MAY_REASONS = (
    "ok", "disabled", "origin:proposed", "origin:meeting", "origin:external",
    "t2_confirm", "outbound", "repo:new", "repo:none", "repo:missing",
    "cost:unknown",
    "ok:self_improve", "self_improve:disabled", "self_improve:paused",
    "self_improve:needs_mcp", "self_improve:repo_mismatch",
)
# 常态原因：不上卡不留痕（C-6，宪法第 10 条口径）
_ROUTINE_REASONS = ("disabled", "self_improve:disabled")


def is_routine_reason(reason: object) -> bool:
    """C-6：`origin:*` / `disabled` / `self_improve:disabled` 是常态回落——
    逐卡留痕即噪音。其余 token 上卡陈述。"""
    return reason in _ROUTINE_REASONS or str(reason).startswith("origin:")


def auto_dispatch_note(reason: str, cost: float, today: str) -> str:
    """免批放行的 notes 痕（actd 落卡）：hand lane 原文不动；§64 lane 报自己的名字。"""
    if reason == "ok:self_improve":
        return (f"[{today} auto-dispatch] self_improve 通道免批自动派发"
                "（交付只能是草稿 PR，§64）")
    return f"[{today} auto-dispatch] hand 出身免批自动派发（est ${cost:g}）"


def _field(card: object, name: str, default: object = None) -> object:
    """Requirement dataclass 与投影 dict 双形态取字段。"""
    if isinstance(card, dict):
        return card.get(name, default)
    return getattr(card, name, default)


def _lane_gate(card: object, cfg: object, lane_paused: bool,
               realpath: Optional[Callable[[str], str]]) -> Optional[str]:
    """§64 lane 的专属天花板（sources 已判定全为 self_improve）：
    开关 → 暂停 → needs_mcp → 仓库 realpath。返回拒绝 token 或 None。"""
    si = self_improve_config(cfg)
    if not si["enabled"]:
        return "self_improve:disabled"
    if lane_paused:
        return "self_improve:paused"
    if bool(_field(card, "needs_mcp")):
        return "self_improve:needs_mcp"
    if not same_repo(_field(card, "target_repo"), self_improve_repo_path(cfg),
                     realpath):
        return "self_improve:repo_mismatch"
    return None


def _origin_gate(card: object, cfg: object, lane_paused: bool,
                 realpath: Optional[Callable[[str], str]]) -> tuple:
    """出身闸 -> (拒绝 token | None, 是否走 §64 lane)。hand 直接放行；sources
    全为 self_improve 交给 _lane_gate；其余出身一律 `origin:<class>`。"""
    sources = _field(card, "sources") or []
    origin = classify_origin(sources)
    if origin == HAND:
        return None, False
    if not is_self_improve_sources(sources):
        return "origin:" + origin, False
    return _lane_gate(card, cfg, lane_paused, realpath), True


def _cost_verdict(cost: Optional[float], lane: bool) -> tuple:
    """末位裁决：hand 卡估价缺失即拒（不可证明 ≤ 文字确认线）；§64 lane 无
    审批步骤、无预算（D9），估价缺失不拦，token 报 `ok:self_improve`。"""
    if lane:
        return True, "ok:self_improve"
    if cost is None:
        return False, "cost:unknown"
    return True, "ok"


def may_auto_dispatch(
    card: object,
    cfg: object,
    path_exists: Optional[Callable[[str], bool]] = None,
    *,
    lane_paused: bool = False,
    realpath: Optional[Callable[[str], str]] = None,
) -> tuple:
    """自动派发资格裁决 -> (bool, reason_token)。

    只裁资格，不改状态：True 时 actd 把卡从 card_sent 直接推进 approved
    （actor=policy，autodispatch.notify=true 则发观察模式通知）；False 时卡
    留在待审批，reason token 上卡陈述（locked：over-ceiling => falls back to
    needs-approval with a stated reason）。并发上限不在这里管——它不是资格
    问题而是排队问题，由 queued_reason 在派发时刻裁（超并发的卡已 approved，
    排在合并运行列的 queued 子状态）。预算不在这里管——没有预算（D9，v0.48.7
    起 ``today_spend`` 参数随台账一并退役）。纯函数：repo 存在性经
    ``path_exists`` seam（默认 os.path.exists），测试注入假的。

    §64 第二条 lane（add-only kwargs）：``lane_paused`` = 通道暂停状态（actd
    从 state/self_improve/lane.json 读后传入，本模块不做 I/O）；``realpath``
    = 仓库比对的 seam（默认 os.path.realpath）。lane 卡放行 token 为
    ``ok:self_improve``；其余天花板（t2_confirm / outbound / repo:*）对两条
    lane 一视同仁。
    """
    ad = autodispatch_config(cfg)
    if not ad["enabled"]:
        return False, "disabled"

    blocked, lane = _origin_gate(card, cfg, lane_paused, realpath)
    if blocked:
        return False, blocked

    # §7/§41 审批语义不变：T2 / green-sign / 高成本文字确认线，一律人批。
    tier = _field(card, "tier")
    tier = tier.strip().upper() if isinstance(tier, str) else ""
    cost = _num(_field(card, "cost_estimate_usd"))
    confirm_over = _num(getattr(cfg, "require_text_confirm_above_usd", None))
    if (tier == "T2" or bool(_field(card, "green_sign_required"))
            or (cost is not None and confirm_over is not None
                and cost > confirm_over)):
        return False, "t2_confirm"

    # never outbound：comms 类卡的执行天然指向对外回复/沟通稿，不自动开跑。
    ctype = _field(card, "type")
    if isinstance(ctype, str) and ctype.strip().lower() == "comms":
        return False, "outbound"

    # existing target_repo only：绝不为自动派发建新 repo；落点必须已存在。
    tk = _field(card, "target_kind")
    if isinstance(tk, str) and tk.strip().lower() == "new":
        return False, "repo:new"
    repo = _field(card, "target_repo")
    repo = repo.strip() if isinstance(repo, str) else ""
    if not repo:
        fallback = getattr(cfg, "default_target_repo", None)
        repo = fallback.strip() if isinstance(fallback, str) else ""
    if not repo:
        return False, "repo:none"
    exists = path_exists if path_exists is not None else os.path.exists
    if not exists(os.path.expanduser(repo)):
        return False, "repo:missing"

    # 估价必须存在（hand lane）：缺失即不可证明 <= 上面的文字确认线，保守回人批。
    # 金额本身不设上限——单卡 $5 天花板与当日预算 retired v0.48.7（D9）。
    return _cost_verdict(cost, lane)


# --------------------------------------------------------------------------- #
# queued_reason — 合并运行列 queued 子状态的原因 chip（locked 词表）
# --------------------------------------------------------------------------- #
# "budget" retired v0.48.7（D9）——词表 tombstone，token 永不复用。
QUEUED_REASONS = ("dependency", "concurrency")


def queued_reason(card: object, state: object) -> Optional[str]:
    """approved-未派发卡的排队原因 -> {dependency, concurrency} 或 None
    （无阻塞，纯粹还没轮到/上次派发失败在退避）。

    ``state`` 是调用方（actd/dashboard 投影）算好的快照 dict，键全部可选，
    缺键 = 跳过该项检查（policy 不做 I/O，不自己数并发）：
      blocked_by        — 非空（list/str）= 有未完结的依赖卡 -> dependency
      running + max_concurrent       — 在跑数达上限 -> concurrency
    优先级 dependency > concurrency：chip 只有一个位置，报最「粘」的阻塞
    （依赖不随时间自愈；并发最快松动）。旧快照里残留的 today_spend /
    daily_budget_usd 键不认、不 raise（D9 之后没有「等预算」这回事）。
    全函数：垃圾 state/card 只会让检查被跳过，绝不 raise。
    """
    st = state if isinstance(state, dict) else {}
    if st.get("blocked_by"):
        return "dependency"
    running = _int(st.get("running"))
    cap = _int(st.get("max_concurrent"))
    if running is not None and cap is not None and running >= cap:
        return "concurrency"
    return None
