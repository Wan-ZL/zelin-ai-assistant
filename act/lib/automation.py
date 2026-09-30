"""自动行为总账（unattended-behaviour ledger）— CONTRACT §81（owner 决策 D83，issue #451）。

owner 原话：「当前软件有很多自动的东西。我觉得太多了，有优化的空间。需要整理出来后
重新设计。去掉冗余设计」。2026-09-23 的只读审计在 launchd / crontab / actd 主循环 /
``server/`` / Mac 两个壳 / GitHub Actions 上数出几十条**无人值守**行为——没有一处能
回答「它们现在到底哪些开着、开关在哪、动手时留没留痕、能不能撤」。

本模块就是那一处。它与 §48 的 :mod:`act.lib.sources` 同形，只是把那里对三个雷达源
做的事推广到**全部**自动行为：

    一条行为 = 总账里的一行 :class:`Behaviour`；
    「它现在开着吗」 = :func:`enabled`（**每次现读配置**，翻开关下一 pass 生效）；
    「它刚才动手了」 = :func:`audit`（一份带帽的 append-only ``state/automation.jsonl``）。

四条不变量（机器执法在 ``scripts/qa/automation_check.py``，账本
``qa/automation_baseline.txt`` shrink-only）：

1. **一行一开关**：每条 ``keep`` 行要么有 ``switch``（Config 字段名，合取），要么
   挂在账本上明账欠着——不许有「没人知道怎么关」的自动行为。
2. **开关是热的**：``switch`` 里的字段名进 :func:`live_fields`，actd 每 pass 从盘上
   现读一次刷到启动时冻结的 cfg 上（``act/actd.py:_refresh_automation_switches``）——
   设置页一翻，下一 pass 生效，不重启守护进程。
3. **动手留痕**：``audit`` 指明它在哪里留痕（``automation.jsonl`` = 经 :func:`audit`）。
4. **代价大的默认关**（§0 第 2/10 条 + ask 4）：effect 沾 :data:`COSTLY`
   （铸卡 / 删数据 / 花钱）的行，出厂默认必须是关——除非明账挂在 baseline 上。

``verdict`` 是 issue #451 第 1 问的答案（keep / merge / remove 各一句理由），
``merged_into`` 指向并入后的幸存者。词表只增不改（§0 第 6 条）。

运行时依赖只有 stdlib + :mod:`act.lib.config` / :mod:`act.lib.logcap`（防腐 #2）。
本模块**绝不触网、绝不写卡片**——只读配置 + 追加一份自己的日志。
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Optional

from act.lib import config, logcap

# --------------------------------------------------------------------------- #
# 词表（add-only）
# --------------------------------------------------------------------------- #
# effect：这条行为动手时改变了什么。COSTLY 三类 = issue #451 ask 4 点名的
# 「写卡 / 删数据 / 花钱」，出厂必须默认关。
EFFECT_CARDS = "cards"        # 铸卡 / 改卡状态
EFFECT_DELETE = "delete"      # 删数据（文件 / 账本行）
EFFECT_SPEND = "spend"        # 花钱（headless claude / 派工子进程）
EFFECT_NETWORK = "network"    # 出网（上传 / gh / API）
EFFECT_NOTIFY = "notify"      # 打扰人（通知 / 徽章）
EFFECT_STATE = "state"        # 只改本机状态（投影 / 索引 / 标记）
EFFECTS = (EFFECT_CARDS, EFFECT_DELETE, EFFECT_SPEND,
           EFFECT_NETWORK, EFFECT_NOTIFY, EFFECT_STATE)
COSTLY = (EFFECT_CARDS, EFFECT_DELETE, EFFECT_SPEND)

# runner：谁在跑它。只有 actd 这一族吃得到「每 pass 现读」的热开关；其余进程
# 各有各的生命周期（cron 每轮新起、launchd 常驻、GitHub 在云上），它们的
# 「热」由「进程短命 = 下一轮现读」或「没有开关」两种形态承担。
RUNNER_ACTD = "actd"
RUNNER_CRON = "cron"
RUNNER_LAUNCHD = "launchd"
RUNNER_SERVER = "server"
RUNNER_SHELL = "shell"
RUNNER_GHA = "gha"
RUNNERS = (RUNNER_ACTD, RUNNER_CRON, RUNNER_LAUNCHD,
           RUNNER_SERVER, RUNNER_SHELL, RUNNER_GHA)

# kind：``switch`` 里的字段怎么读成一个布尔。
KIND_BOOL = "bool"            # 全部字段真 = 开（合取，与 sources.enabled 同款）
KIND_THRESHOLD = "threshold"  # 全部字段 > 0 = 开（天数 / 条数型旋钮，0 = 关）
KIND_NONE = "none"            # 还没有开关（= 欠账，必须挂在 baseline 上）
KINDS = (KIND_BOOL, KIND_THRESHOLD, KIND_NONE)

# verdict：issue #451 第 1 问的处置。
VERDICT_KEEP = "keep"
VERDICT_MERGED = "merged"     # 并进 merged_into 那条，本条只剩墓碑
VERDICT_RETIRED = "retired"   # 整条退役
VERDICTS = (VERDICT_KEEP, VERDICT_MERGED, VERDICT_RETIRED)

AUDIT_LOG = "automation.jsonl"   # state/ 下的一份，经 audit() 落笔
AUDIT_NONE = "none"              # 还没有留痕（= 欠账）
# 别处已有的留痕面（不是本模块写的，总账只是指过去）。收成闭词表而不是散在
# 六十行里的字符串字面量——防腐 #9：同一个东西只许有一种写法。
AUDIT_ACTD_LOG = "actd.log"
AUDIT_DAILY_LOOP = "daily_loop.jsonl"
AUDIT_RADAR_HEALTH = "radar_health.json"
AUDIT_GHA = "GitHub Actions 日志"
AUDIT_CHANNELS = (AUDIT_LOG, AUDIT_NONE, AUDIT_ACTD_LOG, AUDIT_DAILY_LOOP,
                  AUDIT_RADAR_HEALTH, AUDIT_GHA)

# 外部调度器的登记名里唯一被多条行为共用的那个（ingest 那条 30 分钟 cron 链，
# 一行 crontab 挂着四个步骤）。
UNIT_INGEST_CHAIN = "install.sh:INGEST_CHAIN"
AUDIT_MAX_BYTES = 1 << 20        # 防腐 #4：出生即带帽（registry_writes.jsonl 同款）
_AUDIT_FIELD_CAP = 200           # 单个附加字段的字符上限（日志不当数据库使）


@dataclass(frozen=True)
class Behaviour:
    """总账的一行。字段只增不改（§0 第 6 条）。"""

    slug: str                       # canonical id（防腐 #9：模块名 / 开关 / 日志同字派生）
    zh: str                         # 中文名（看板 / 设置页文案）
    en: str                         # English label
    runner: str                     # RUNNERS 之一
    cadence: str                    # 人话的触发节奏（"每 pass" / "*/30 cron" / "每日 03:30"）
    effect: tuple                   # EFFECTS 的子集，至少一项
    code: str                       # "<相对路径>:<符号>" —— 门会核验文件与符号都在
    law: tuple                      # ("§9", ...) —— 门会核验每个 § 在 CONTRACT 里有正文
    verdict: str                    # VERDICTS 之一
    why: str                        # 这条处置的一句理由（issue #451 ask 1）
    switch: tuple = ()              # Config 字段名（合取）；空 = 没有开关
    kind: str = KIND_NONE
    audit: str = AUDIT_NONE         # AUDIT_LOG 或别的留痕处（"actd.log" / "daily_loop.jsonl"…）
    reversible: str = ""            # 怎么撤；只读行为写 "n/a（只读）"
    merged_into: str = ""           # verdict=merged 时指向幸存者的 slug
    overlaps: tuple = field(default_factory=tuple)   # 同族的其它 slug（去重分析用）
    # 外部调度器里的**登记名**：launchd Label / GitHub workflow 路径 /
    # `install.sh` 的 cron 变量名。门拿它与仓库里那几类可枚举文件做**互为子集**
    # 的比对（`unlisted:` 违例）——从 `cadence` 的人话里抠正则是数不准的。
    # 住在 actd pass 里的行没有外部登记，留空。
    unit: str = ""

    @property
    def costly(self) -> bool:
        """这条行为是否属于「写卡 / 删数据 / 花钱」三类（ask 4 的射程）。"""
        return any(e in COSTLY for e in self.effect)

    @property
    def live(self) -> bool:
        """它的开关吃不吃得到 actd 的每 pass 现读。"""
        return self.runner == RUNNER_ACTD and bool(self.switch)


# --------------------------------------------------------------------------- #
# 总账（truth）。行序 = 阅读顺序：出生 → 审批 → 执行 → 整理 → 出网 → 外部调度。
#
# 「这一行为什么长这样」的证据全在 ``code`` 与 ``law`` 两列里——门会核验两列
# 都指得到真东西。人看的渲染 = docs/design/automation-ledger.md（由本表生成）。
# --------------------------------------------------------------------------- #
def _b(**kw) -> Behaviour:
    """行构造器：把 effect / law / switch / overlaps 的元组化收在一处。"""
    for key in ("effect", "law", "switch", "overlaps"):
        if key in kw:
            kw[key] = tuple(kw[key])
    return Behaviour(**kw)


LEDGER: tuple = (
    # ---------------------------------------------------------------- 卡片出生
    _b(slug="radar_obsidian", zh="Obsidian 笔记雷达", en="Obsidian note radar",
       runner=RUNNER_CRON, cadence="crontab */30（ingest 链末步）",
       effect=[EFFECT_CARDS, EFFECT_SPEND], code="act/radar.py:main", unit=UNIT_INGEST_CHAIN,
       law=["§42", "§48"], verdict=VERDICT_KEEP,
       why="唯一从笔记立案的通道；§48 已给它一把合取开关，保留不动。",
       switch=["features.obsidian_radar", "obsidian_enabled"], kind=KIND_BOOL,
       audit=AUDIT_RADAR_HEALTH, reversible="建出来的卡可 trash / 静默并入可拆",
       overlaps=["radar_slack", "radar_gmail"]),
    _b(slug="radar_slack", zh="Slack 雷达", en="Slack radar",
       runner=RUNNER_LAUNCHD, cadence="launchd StartInterval=180s",
       effect=[EFFECT_CARDS, EFFECT_SPEND, EFFECT_NETWORK],
       code="act/radar_slack.py:_main", unit="com.zelin.aiassistant.slackradar",
       law=["§13", "§48"], verdict=VERDICT_KEEP,
       why="同上；没有凭证时自身静默 no-op，开关真源已归一到 §48。",
       switch=["features.slack_radar", "slack_enabled"], kind=KIND_BOOL,
       audit=AUDIT_RADAR_HEALTH, reversible="建出来的卡可 trash",
       overlaps=["radar_obsidian", "radar_gmail"]),
    _b(slug="radar_gmail", zh="Gmail 雷达", en="Gmail radar",
       runner=RUNNER_LAUNCHD, cadence="launchd StartInterval=300s",
       effect=[EFFECT_CARDS, EFFECT_SPEND, EFFECT_NETWORK],
       code="act/radar_gmail.py:_main", unit="com.zelin.aiassistant.gmailradar",
       law=["§14", "§48"], verdict=VERDICT_KEEP,
       why="同上。", switch=["features.gmail_radar", "gmail_enabled"], kind=KIND_BOOL,
       audit=AUDIT_RADAR_HEALTH, reversible="建出来的卡可 trash",
       overlaps=["radar_obsidian", "radar_slack"]),
    _b(slug="daily_loop_proposals", zh="每日循环铸提案卡", en="daily-loop proposals",
       runner=RUNNER_ACTD, cadence="每天 daily_loop.time 后的第一个 pass",
       effect=[EFFECT_CARDS, EFFECT_SPEND],
       code="act/lib/daily_loop.py:_propose", law=["§70"], verdict=VERDICT_KEEP,
       why="D10 的主体；D86 起唯一的输入是素材库（出厂关），上限旋钮在设置页",
       switch=["daily_loop_enabled", "daily_loop_max_proposals_per_day"],
       kind=KIND_THRESHOLD, audit=AUDIT_DAILY_LOOP,
       reversible="铸出的卡可 trash（循环卡 90 天可恢复）",
       overlaps=["loop_material_proposals", "weekly_digest", "digest_card"]),
    _b(slug="loop_material_proposals", zh="素材库铸提案卡", en="material-library proposals",
       runner=RUNNER_ACTD, cadence="随每日循环",
       effect=[EFFECT_CARDS, EFFECT_SPEND, EFFECT_NETWORK],
       code="act/lib/loop_inputs.py:materials_signals", law=["§62", "§70"],
       verdict=VERDICT_KEEP,
       why="D86：原挂 §65.1 通道开关，通道删除后改为 yaml 专用开关 "
           "daily_loop.materials_enabled（出厂关）",
       switch=["daily_loop_enabled", "daily_loop_materials_enabled"], kind=KIND_BOOL,
       audit=AUDIT_DAILY_LOOP, reversible="铸出的卡可 trash",
       overlaps=["daily_loop_proposals"]),
    _b(slug="digest_card", zh="状态摘要卡", en="state digest card",
       runner=RUNNER_CRON, cadence="crontab 每天 09:07 唤醒，按 digest.frequency 自闸",
       effect=[EFFECT_CARDS, EFFECT_SPEND], code="act/digest.py:main", unit="install.sh:DIGEST_LINE",
       law=["§17", "§16"], verdict=VERDICT_KEEP,
       why="D19 已把 frequency 出厂设 off，两键合取即「出厂零卡片」，保留。",
       switch=["features.digest", "digest_frequency"], kind=KIND_BOOL,
       audit="digest.json", reversible="卡可 trash",
       overlaps=["weekly_digest", "daily_loop_proposals"]),
    _b(slug="weekly_digest", zh="每周 ingest 摘要卡", en="weekly ingest digest",
       runner=RUNNER_LAUNCHD, cadence="launchd 每天唤醒，按 day/hour 自闸",
       effect=[EFFECT_CARDS, EFFECT_SPEND], code="act/weekly_digest.py:main", unit="com.zelin.aiassistant.weeklydigest",
       law=["§24"], verdict=VERDICT_KEEP,
       why="D19 已出厂关（0/15 获批）；留着给想要的人开。",
       switch=["weekly_digest_enabled"], kind=KIND_BOOL,
       audit=AUDIT_ACTD_LOG, reversible="卡可 trash", overlaps=["digest_card"]),
    _b(slug="quick_capture_fold", zh="入库前折叠判官", en="pre-filing fold judge",
       runner=RUNNER_CRON, cadence="每张候选新卡一次（阻塞式 LLM 判官）",
       effect=[EFFECT_CARDS, EFFECT_SPEND],
       code="act/lib/quick_capture.py:_pre_filing_fold",
       law=["§44", "§38", "§76"], verdict=VERDICT_KEEP,
       why="折叠优先是「少建卡」的主力（fold note 可拆），但它是近重复这一族的**第三个**"
           "调度器，且此前完全没有开关——关掉 merge_silent 只关住了巡检与落盘两端，"
           "雷达每轮照样起判官花钱。自此三处共用 `features.merge_silent`。",
       switch=["features.merge_silent"], kind=KIND_BOOL,
       audit="fold_receipts", reversible="fold note 可拆出（§38.2）",
       overlaps=["silent_merge", "near_dupe_scan", "loop_dedup_merge"]),

    # ---------------------------------------------------------------- 审批 / 执行
    _b(slug="auto_dispatch", zh="免批自动派发", en="policy auto-dispatch",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_CARDS, EFFECT_SPEND],
       code="act/lib/actd/dispatch.py:auto_dispatch_pass（retired D86）", law=["§51", "§50", "§71"],
       verdict=VERDICT_RETIRED,
       why="D86：§51 两条免批 lane 均已退役（hand D80.4、self_improve D86），无可免批的卡",
       switch=[], kind=KIND_NONE, audit=AUDIT_NONE,   # D86：行为已删，不再有 audit() 调用点
       reversible="n/a（行为已删除）"),
    _b(slug="dispatch_approved", zh="批准即派 headless 会话", en="dispatch approved cards",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_SPEND], code="act/lib/actd/dispatch.py:dispatch_approved",
       law=["§4", "§34"], verdict=VERDICT_KEEP,
       why="人点了批准就是授权，不该再给它一把「批了也不跑」的开关；"
           "D86 起没有无人值守的那一半（免批 lane 全部退役）。",
       audit=AUDIT_ACTD_LOG, reversible="会话可 stop，卡可打回"),
    _b(slug="auto_resume", zh="死会话自动续命", en="auto-resume dead sessions",
       runner=RUNNER_ACTD, cadence="每 pass（带退避）",
       effect=[EFFECT_SPEND], code="act/lib/actd/reconcile.py:reconcile_executing",
       law=["§16", "§46"], verdict=VERDICT_KEEP,
       why="§16 已把两键合取并做成每 pass 现读——本总账照抄这条先例，不动它。",
       switch=["auto_resume", "features.auto_resume"], kind=KIND_BOOL,
       audit=AUDIT_ACTD_LOG, reversible="停会话即止"),
    _b(slug="card_summary", zh="待验收卡 AI 摘要", en="review-card AI summary",
       runner=RUNNER_ACTD, cadence="每 pass（指纹变了才派）",
       effect=[EFFECT_SPEND], code="act/lib/card_summary.py:tick",
       law=["§64"], verdict=VERDICT_KEEP,
       why="只是建议、永不改 status；开关是冷的——本轮转热。",
       switch=["card_summary_enabled"], kind=KIND_BOOL, audit=AUDIT_ACTD_LOG,
       reversible="n/a（只写 assessment 字段，不改状态）"),
    _b(slug="raising_expansion", zh="欠账卡自动展开", en="raising-debt expansion",
       runner=RUNNER_ACTD, cadence="每 pass 一张",
       effect=[EFFECT_CARDS, EFFECT_SPEND], code="act/actd.py:process_raising",
       law=["§1", "§40"], verdict=VERDICT_KEEP,
       why="展开的是人已经放进来的欠账，不是凭空立案；有界（每 pass 一张）。"
           "以前它一把开关都没有——本轮补 `features.raising`。",
       switch=["features.raising"], kind=KIND_BOOL, audit=AUDIT_LOG,
       reversible="展开出的卡可 trash"),

    # ---------------------------------------------------------------- 整理 / 清理
    _b(slug="silent_merge", zh="近重复静默并入", en="silent merge of near-duplicates",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_CARDS], code="act/lib/silent_merge.py:consume_judged",
       law=["§44"], verdict=VERDICT_KEEP,
       why="§44 的执行端（判官在旁路、落盘在主循环）。它一直没有开关——本轮补一把。",
       switch=["features.merge_silent"], kind=KIND_BOOL, audit=AUDIT_LOG,
       reversible="并入可拆（§44.4 fold note + 回执）",
       overlaps=["near_dupe_scan", "loop_dedup_merge", "quick_capture_fold"]),
    _b(slug="near_dupe_scan", zh="新卡近重复巡检", en="near-duplicate scan of new cards",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_SPEND], code="act/lib/auto_merge.py:scan_new_cards",
       law=["§38", "§44"], verdict=VERDICT_KEEP,
       why="silent_merge 的**探测端**，同一条法条的两半：共用一把开关"
           "（merge_silent_enabled），不再是两条互不知情的自动行为。",
       switch=["features.merge_silent"], kind=KIND_BOOL, audit=AUDIT_LOG,
       reversible="只请求判定，落盘在 silent_merge 那一半",
       overlaps=["silent_merge", "loop_dedup_merge"]),
    _b(slug="loop_dedup_merge", zh="每日同题合并", en="daily-loop dedup merge",
       runner=RUNNER_ACTD, cadence="每天一次（每日循环第一阶段 dedup）",
       effect=[EFFECT_CARDS], code="act/lib/maintenance.py:dedup_lanes",
       law=["§70"], verdict=VERDICT_KEEP,
       why="它做的是 silent_merge 做不了的事——**同题多卡合成一张新卡**（D10 原话），"
           "不是近重复两两并入；两者射程不同，保留但在总账里明写分工。",
       switch=["daily_loop_enabled"], kind=KIND_BOOL, audit=AUDIT_DAILY_LOOP,
       reversible="旧卡进回收站（90 天可恢复）",
       overlaps=["silent_merge", "near_dupe_scan"]),
    _b(slug="loop_idle_sweep", zh="提案/潜在任务过时清理", en="idle-card sweep",
       runner=RUNNER_ACTD, cadence="每天一次（每日循环第二阶段 stale_sweep）",
       effect=[EFFECT_CARDS], code="act/lib/maintenance.py:sweep_stale",
       law=["§70"], verdict=VERDICT_KEEP,
       why="只碰提案 / 潜在任务两列、只进回收站（可恢复），与 archive_stale"
           "（碰 delivered、进 archive）射程不重叠。",
       switch=["daily_loop_enabled", "daily_loop_stale_days"], kind=KIND_THRESHOLD,
       audit=AUDIT_DAILY_LOOP, reversible="进回收站，90 天内可恢复",
       overlaps=["loop_review_aging", "archive_stale"]),
    _b(slug="loop_review_aging", zh="待验收卡老化", en="review-lane aging",
       runner=RUNNER_ACTD, cadence="每天一次（两阶段：先通知，隔 20h 才动）",
       effect=[EFFECT_CARDS], code="act/lib/maintenance.py:sweep_review_notices",
       law=["§70"], verdict=VERDICT_KEEP,
       why="D74 的两阶段「先说再做」；碰的是待验收列，与 idle_sweep 两列不重叠。",
       switch=["daily_loop_enabled", "daily_loop_review_stale_days"],
       kind=KIND_THRESHOLD, audit=AUDIT_DAILY_LOOP,
       reversible="进回收站，90 天内可恢复", overlaps=["loop_idle_sweep"]),
    _b(slug="loop_worktree_sweep", zh="worktree 回收", en="worktree GC",
       runner=RUNNER_ACTD, cadence="每天一次（每日循环第三阶段）",
       effect=[EFFECT_DELETE], code="act/lib/worktrees.py:sweep",
       law=["§75", "§70"], verdict=VERDICT_KEEP,
       why="D68 定的三条删除理由本身很保守；缺的是一把**配置**开关"
           "（今天只有环境变量 AIASSISTANT_WORKTREE_SWEEP）——本轮补上。",
       switch=["daily_loop_enabled", "features.worktree_sweep"], kind=KIND_BOOL,
       audit=AUDIT_LOG,
       reversible="分支不删、有本地独有提交的推迟到 14 天线；worktree 目录本身不可恢复"),
    _b(slug="archive_stale", zh="冷交付卡自动封存", en="auto-archive cold delivered",
       runner=RUNNER_ACTD, cadence="每 24h 一次",
       effect=[EFFECT_CARDS], code="act/lib/actd/housekeeping.py:archive_stale",
       law=["§4"], verdict=VERDICT_KEEP,
       why="封存可逆（archive/ 里还在，prev_status 记着），且带未来 deadline / "
           "同簇有活卡的一律不动；开关是冷的——本轮转热。",
       switch=["archive_after_days"], kind=KIND_THRESHOLD, audit=AUDIT_LOG,
       reversible="从归档里捞回来即可", overlaps=["loop_idle_sweep", "purge_trash"]),
    _b(slug="purge_trash", zh="回收站硬删", en="trash hard purge",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_DELETE], code="act/lib/actd/housekeeping.py:purge_trash",
       law=["§9", "§70"], verdict=VERDICT_KEEP,
       why="§0 第 2 条「绝无不可恢复的自动删除」与它直接冲突：本轮**出厂改为关**"
           "（retention_days 默认 0 = 永不自动硬删），要清的人自己开。",
       switch=["trash_retention_days"], kind=KIND_THRESHOLD, audit=AUDIT_LOG,
       reversible="开着时不可逆——这正是出厂关的理由",
       overlaps=["archive_stale"]),
    _b(slug="gc_attachments", zh="孤儿贴图清理", en="orphan attachment GC",
       runner=RUNNER_ACTD, cadence="每 24h 一次",
       effect=[EFFECT_DELETE], code="act/lib/actd/housekeeping.py:gc_attachments",
       law=["§10"], verdict=VERDICT_KEEP,
       why="只删「无引用且 >30 天」的文件、引用读不出就整轮零删除；风险已被 fail-safe "
           "罩住，但删除不可逆——补一把开关，出厂仍开（不删会按 5-15MB/张无限涨）。",
       switch=["features.attachment_gc"], kind=KIND_BOOL, audit=AUDIT_LOG,
       reversible="不可逆（只删无人引用的孤儿文件）"),
    _b(slug="merge_job_cleanup", zh="合并作业 TTL 清扫", en="merge-job TTL sweep",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_STATE], code="act/lib/actd/merge.py:cleanup_merge_jobs",
       law=["§21"], verdict=VERDICT_KEEP,
       why="只清自己的作业文件、不碰卡片；纯管家，不值得一把开关。",
       audit=AUDIT_ACTD_LOG, reversible="n/a（只清中间态作业）"),
    _b(slug="triage_snapshot_sweep", zh="分诊快照清扫", en="triage snapshot sweep",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_STATE], code="act/lib/actd/triage_guard.py:sweep_triage_snapshots",
       law=["§34"], verdict=VERDICT_KEEP,
       why="同上，清的是自己的侧文件。", audit=AUDIT_ACTD_LOG,
       reversible="n/a（只清中间态快照）"),
    _b(slug="search_index_prune", zh="搜索索引裁剪", en="search index prune",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_STATE], code="act/lib/search_index.py:prune",
       law=["§37"], verdict=VERDICT_KEEP,
       why="派生索引，删了会重建；纯管家。", audit=AUDIT_ACTD_LOG,
       reversible="重新索引即可"),
    _b(slug="screenpipe_retention", zh="录制数据保留期清理",
       en="screenpipe retention cleanup",
       runner=RUNNER_CRON, cadence="crontab */30（ingest 链第二步）",
       effect=[EFFECT_DELETE], code="act/lib/screenpipe_retention.py:prune", unit=UNIT_INGEST_CHAIN,
       law=["§72"], verdict=VERDICT_KEEP,
       why="§72 已有两把设置页旋钮（天数 / 分钟数），DB 行的出厂值是 0 = 永久保留；"
           "媒体分钟数出厂 60（= 历来写死值）。留，不动默认。",
       switch=["screenpipe_retention_days"], kind=KIND_THRESHOLD,
       audit="screenpipe_cleanup 回执", reversible="不可逆（已导出进 vault 的才删）"),

    # ---------------------------------------------------------------- 出网
    _b(slug="telemetry_upload", zh="遥测上传", en="telemetry upload",
       runner=RUNNER_CRON, cadence="crontab 每小时 :17（install.sh TELEMETRY_LINE）",
       unit="install.sh:TELEMETRY_LINE",
       effect=[EFFECT_NETWORK], code="act/analytics_sync.py:main",
       law=["§15", "§16"], verdict=VERDICT_KEEP,
       why="两层门（features.analytics 管记不记、telemetry.enabled 管传不传）已经"
           "是本仓库最严的一处，隐私 fail-closed；不动。",
       switch=["features.analytics", "telemetry_enabled"], kind=KIND_BOOL,
       audit="analytics 游标", reversible="n/a（关掉即停，积压不补传）"),
    _b(slug="feedback_retry", zh="建议上传重试", en="feedback upload retry",
       runner=RUNNER_ACTD, cadence="每 pass（每条只重试一次）",
       effect=[EFFECT_NETWORK], code="act/lib/feedback.py:retry_pending",
       law=["§29"], verdict=VERDICT_KEEP,
       why="传的是用户自己按「提建议」写的东西（逐条 opt-in），不是遥测；"
           "只重试一次、失败即放弃，保留。",
       switch=["telemetry_enabled"], kind=KIND_BOOL, audit=AUDIT_ACTD_LOG,
       reversible="n/a（重试的是用户自己提交过的建议）"),
    _b(slug="feedback_sync", zh="建议同步成 GitHub issue", en="feedback → GitHub issues",
       runner=RUNNER_ACTD, cadence="每 pass（无 token 即静默 no-op）",
       effect=[EFFECT_NETWORK], code="act/lib/feedback_sync.py:sweep",
       law=["§29"], verdict=VERDICT_KEEP,
       why="逐条 opt-in + token 文件不在就整体关；开关是冷的——本轮转热。",
       switch=["features.feedback_sync"], kind=KIND_BOOL, audit=AUDIT_ACTD_LOG,
       reversible="issue 可关（公开过就是公开过——所以是逐条 opt-in）"),
    _b(slug="update_check", zh="应用内更新检查", en="in-app update check",
       runner=RUNNER_ACTD, cadence="至多每 24h 一次网络请求",
       effect=[EFFECT_NETWORK], code="act/lib/update_check.py:check",
       law=["§26"], verdict=VERDICT_KEEP,
       why="只查版本号、绝不自动下载安装；开关是冷的——本轮转热。",
       switch=["updates_check_enabled"], kind=KIND_BOOL, audit=AUDIT_ACTD_LOG,
       reversible="n/a（只读一个版本号）"),
    _b(slug="self_improve_tick", zh="自动 PR 通道巡检", en="self-improve lane tick",
       runner=RUNNER_ACTD, cadence="n/a（retired D86）",
       effect=[EFFECT_NETWORK, EFFECT_CARDS],
       code="act/lib/self_improve.py:tick_hook（retired D86，模块已删）", law=["§65"],
       verdict=VERDICT_RETIRED,
       why="D86：owner「你把这个自动读 issue 写 PR 的循环功能完整删掉」——§65 通道整条删除，"
           "巡检随之退役。",
       switch=[], kind=KIND_NONE, audit="lane.json",
       reversible="n/a（行为已删除）"),
    _b(slug="syncd", zh="云同步守护进程", en="cloud sync daemon",
       runner=RUNNER_LAUNCHD, cadence="常驻（KeepAlive）",
       effect=[EFFECT_NETWORK, EFFECT_STATE], code="act/syncd.py:main", unit="com.zelin.aiassistant.syncd",
       law=["§31", "§32"], verdict=VERDICT_KEEP,
       why="没配对就整体静默；不动。", audit="syncd.log",
       reversible="n/a（同步是双向镜像，冲突有 sync-safety 层）"),

    # ---------------------------------------------------------------- ingest 链
    _b(slug="ingest_screenpipe_export", zh="屏幕录制导出进 vault",
       en="screenpipe export to vault",
       runner=RUNNER_CRON, cadence="crontab */30（第一步）",
       effect=[EFFECT_STATE], code="ingest/screenpipe-export.sh", unit=UNIT_INGEST_CHAIN,
       law=["§18"], verdict=VERDICT_KEEP,
       why="写的是笔记文件，不是卡片（§0 第 4 条：记录 ≠ 立案）；留。",
       audit="ingest 日志", reversible="笔记文件在 vault 里，人可删"),
    _b(slug="ingest_media_cleanup", zh="原始截图/录像删除",
       en="raw media deletion",
       runner=RUNNER_CRON, cadence="crontab */30（第二步）",
       effect=[EFFECT_DELETE], code="ingest/screenpipe-cleanup.sh", unit=UNIT_INGEST_CHAIN,
       law=["§72"], verdict=VERDICT_KEEP,
       why="§72.4 的分钟数旋钮已在设置页，出厂 60（= 历来写死值）；不删的话磁盘会炸。",
       switch=["screenpipe_media_retention_minutes"], kind=KIND_THRESHOLD,
       audit="screenpipe_cleanup 回执", reversible="不可逆（已导出的才删）"),
    _b(slug="ingest_meeting_recap", zh="会议纪要生成", en="meeting recap",
       runner=RUNNER_CRON, cadence="crontab */30（第三步）",
       effect=[EFFECT_SPEND], code="act/recap.py:main", unit=UNIT_INGEST_CHAIN, law=["§63"],
       verdict=VERDICT_KEEP,
       why="copy-only 纪要，不是卡、没有发送路径；已有设置页开关。",
       switch=["recap_enabled"], kind=KIND_BOOL, audit="recap 存档",
       reversible="纪要可忽略 / 删除"),
    _b(slug="ingest_vault_process", zh="headless 笔记加工", en="headless vault ingest",
       runner=RUNNER_CRON, cadence="crontab */30（第四步，上限 2 小时）",
       effect=[EFFECT_SPEND], code="ingest/process-screenpipe.sh", unit=UNIT_INGEST_CHAIN,
       law=["§18"], verdict=VERDICT_KEEP,
       why="ingest 链里最花钱的一步，却没有任何开关——本轮补一把总闸。",
       switch=["features.ingest"], kind=KIND_BOOL, audit="ingest 日志",
       reversible="加工产物落在 vault，人可删"),

    # ---------------------------------------------------------------- 通知 / 投影
    _b(slug="notify_transitions", zh="状态翻面通知", en="transition notifications",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_NOTIFY], code="act/lib/actd/alerts.py:detect_transitions",
       law=["§28", "§76"], verdict=VERDICT_KEEP,
       why="§28 的四把偏好（安静时段 + 三类开关）本来就每次现读，不动。",
       switch=["notify_proposals"], kind=KIND_BOOL, audit="notify_queue",
       reversible="n/a（一条横幅）"),
    _b(slug="radar_liveness_alert", zh="源死亡告警", en="radar liveness alert",
       runner=RUNNER_ACTD, cadence="每 pass（每源一次，恢复出账）",
       effect=[EFFECT_NOTIFY], code="act/actd.py:_check_radar_liveness",
       law=["§48", "§28"], verdict=VERDICT_KEEP,
       why="诚实的健康报告是 §0 第 3 条——**扫描永不可关**；能关的只有「要不要打扰"
           "你」那一层，真源是 §28 的 `notify_failures`（关掉时两道扫描照跑、只是不响）。",
       switch=["notify_failures"], kind=KIND_BOOL,
       audit=AUDIT_RADAR_HEALTH, reversible="n/a"),
    _b(slug="auth_failure_alert", zh="凭证失效告警", en="auth failure alert",
       runner=RUNNER_ACTD, cadence="每 pass",
       effect=[EFFECT_NOTIFY], code="act/lib/actd/alerts.py:check_auth_failures",
       law=["§25", "§28"], verdict=VERDICT_KEEP, why="同上：扫描不可关，打扰可关。",
       switch=["notify_failures"], kind=KIND_BOOL, audit="failures 台账",
       reversible="n/a"),
    _b(slug="store2_tick", zh="数据层激活 / 每日导出", en="store2 activate + export",
       runner=RUNNER_ACTD, cadence="每 pass（激活后一次 stat 级开销）",
       effect=[EFFECT_STATE], code="act/actd.py:_store2_tick", law=["§53"],
       verdict=VERDICT_KEEP,
       why="真源迁移 + 人类可读镜像；registry.backend 是回滚开关不是自动化闸。",
       audit=AUDIT_ACTD_LOG, reversible="backend: yaml 回滚（保留一个版本）"),
    _b(slug="board_watcher", zh="看板变更推送", en="board SSE watcher",
       runner=RUNNER_SERVER, cadence="300ms mtime 轮询",
       effect=[EFFECT_STATE], code="server/watcher.py:POLL_INTERVAL",
       law=["§49"], verdict=VERDICT_KEEP,
       why="只读 mtime、只推一个时间戳；零状态改变。", audit=AUDIT_NONE,
       reversible="n/a（只读）"),

    # ---------------------------------------------------------------- 部署 / 外部
    _b(slug="auto_deploy", zh="合并即上岗自动部署", en="auto-deploy on merge",
       runner=RUNNER_LAUNCHD, cadence="launchd 每 10 分钟",
       effect=[EFFECT_STATE, EFFECT_NETWORK], code="scripts/auto-deploy.sh", unit="com.zelin.aiassistant.autodeploy",
       law=["§56"], verdict=VERDICT_KEEP,
       why="它改的是**这台机器上正在跑的软件**，却只在 install.sh 那一刻看一眼 "
           "features.auto_deploy，脚本自己不读——受保护路径，本轮只在总账里记明这道欠账。",
       switch=["features.auto_deploy"], kind=KIND_BOOL, audit="deploy_state.json",
       reversible="doctor 失败自动回滚；可手动 git checkout 旧 tag"),
    _b(slug="gha_mutation_nightly", zh="夜间变异测试", en="nightly mutation run",
       runner=RUNNER_GHA, cadence="GitHub cron（每晚）",
       effect=[EFFECT_NETWORK], code=".github/workflows/mutation-nightly.yml",
       unit=".github/workflows/mutation-nightly.yml", law=["§57"], verdict=VERDICT_KEEP,
       why="D5 定的「永不拦 PR」；它会开 / 重开 issue——那条噪音记在总账里，"
           "受保护路径，修法另开 PR。",
       audit=AUDIT_GHA, reversible="issue 可关"),
    _b(slug="gha_insights", zh="夜间 insights 报告", en="nightly insights",
       runner=RUNNER_GHA, cadence="GitHub cron",
       effect=[EFFECT_NETWORK], code=".github/workflows/insights.yml",
       unit=".github/workflows/insights.yml", law=["§58"], verdict=VERDICT_KEEP,
       why="同上：它也会重开 owner 关掉的 issue。受保护路径，本轮只记账。",
       audit=AUDIT_GHA, reversible="issue 可关"),
    _b(slug="gha_update_pr_branches", zh="自动把 main 并进每个 PR",
       en="auto-update PR branches",
       runner=RUNNER_GHA, cadence="main 每次移动",
       effect=[EFFECT_NETWORK], code=".github/workflows/update-pr-branches.yml",
       law=["§56"], verdict=VERDICT_KEEP,
       why="§56.6 的 merge-queue 替身，没它并行 PR 全要手动 rebase。受保护路径。",
       audit=AUDIT_GHA, reversible="no-autoupdate 标签可逐 PR 关"),
    _b(slug="gha_release_on_merge", zh="合并即发版", en="release on merge",
       runner=RUNNER_GHA, cadence="push 到 main",
       effect=[EFFECT_NETWORK], code=".github/workflows/release-on-merge.yml",
       law=["§56"], verdict=VERDICT_KEEP,
       why="§0 第 8 条的版本真源就是它铸的 tag。受保护路径。",
       audit="GitHub Releases", reversible="tag 可删（不该删）"),
    _b(slug="gha_keepalive", zh="仓库保活", en="repo keepalive",
       runner=RUNNER_GHA, cadence="GitHub cron",
       effect=[EFFECT_NETWORK], code=".github/workflows/keepalive.yml",
       unit=".github/workflows/keepalive.yml", law=["§56"], verdict=VERDICT_KEEP,
       why="防 GitHub 60 天静默停掉计划任务；零状态改变。受保护路径。",
       audit=AUDIT_GHA, reversible="n/a"),

    # ---------------------------------------------------------------- 壳 / 客户端
    _b(slug="shell_notify_relay", zh="通知队列消费（新壳）", en="notify relay (shell)",
       runner=RUNNER_SHELL, cadence="队列轮询",
       effect=[EFFECT_NOTIFY], code="shell/Sources/NotifyRelay.swift",
       law=["§28", "§61"], verdict=VERDICT_KEEP,
       why="D3 之后的唯一消费者。", audit="board-shell.log", reversible="n/a"),
    _b(slug="mac_notify_relay", zh="通知队列消费（旧 app）", en="notify relay (old app)",
       runner=RUNNER_SHELL, cadence="队列轮询",
       effect=[EFFECT_NOTIFY], code="mac/Sources/NotifyRelay.swift",
       law=["§28", "§54"], verdict=VERDICT_MERGED, merged_into="shell_notify_relay",
       why="两个 app 同时在班时两边都消费同一个队列、都去重启录制引擎"
           "（issue #451 最后一条）。D3 已判旧 app 退役——总账把它记成并入，"
           "实际代码删除随 P8 旧 app 退役同车。",
       audit="通知日志", reversible="n/a", overlaps=["shell_notify_relay"]),

    # ---------------------------------------------------------------- 常驻壳（进程本体）
    _b(slug="launchd_actd", zh="主循环常驻 + 自动重启", en="actd resident agent",
       runner=RUNNER_LAUNCHD, cadence="RunAtLoad + KeepAlive（崩了 launchd 拉起）",
       effect=[EFFECT_STATE], code="act/launchd/com.zelin.aiassistant.actd.plist",
       unit="com.zelin.aiassistant.actd", law=["§55"], verdict=VERDICT_KEEP,
       why="它是上面绝大多数行的宿主；自身不做业务动作，开关 = 装不装这个 agent。",
       audit=AUDIT_ACTD_LOG, reversible="launchctl bootout 即停"),
    _b(slug="launchd_server", zh="看板 server 常驻 + 自动重启", en="board server agent",
       runner=RUNNER_LAUNCHD, cadence="RunAtLoad + KeepAlive",
       effect=[EFFECT_STATE], code="act/launchd/com.zelin.aiassistant.server.plist",
       unit="com.zelin.aiassistant.server", law=["§54", "§55"], verdict=VERDICT_KEEP,
       why="回环看板面（§0 第 9 条的唯一监听例外）；重启不写任何用户数据。",
       audit="server.launchd.log", reversible="launchctl bootout 即停"),
    _b(slug="server_token_remint", zh="重启时重铸实例 token",
       en="instance token re-mint on restart",
       runner=RUNNER_SERVER, cadence="每次 server 进程启动（含 KeepAlive 拉起）",
       effect=[EFFECT_STATE], code="server/security.py",
       law=["§49"], verdict=VERDICT_KEEP,
       why="安全姿势正确（每装一份一个 token），但它是一次**无人值守的凭证轮换**，"
           "今天一行日志都不留——所有开着的页签下一次写操作 401，症状像 bug。"
           "本轮只记账：补留痕要动 server 的启动序，另开 PR。",
       reversible="刷新页面即可（server 把新 token 注进 index.html）"),
    _b(slug="web_language_autowrite", zh="页面加载自动写语言键",
       en="language auto-write on page load",
       runner=RUNNER_SERVER, cadence="每次页面加载（没显式存过语言且 URL 无 ?lang=）",
       effect=[EFFECT_STATE], code="web/src/store.ts",
       law=["§15"], verdict=VERDICT_KEEP,
       why="「打开看板 = 写一次设置文件」是 web 侧最像违反『没有人点就不改状态』的一条。"
           "它真实存在、可逆（设置页改回来），但 fire-and-forget 两个分支都吞掉。"
           "本轮只记账——改它要动语言持久化语义，值得单独一张卡。",
       reversible="设置页改语言，或删掉 overrides 里那一键"),

    # ---------------------------------------------------------------- 外部（GitHub）
    _b(slug="gha_ci_nightly", zh="夜间 Windows / qlty 巡检", en="nightly CI legs",
       runner=RUNNER_GHA, cadence="GitHub cron（每晚 10:43 UTC）",
       effect=[EFFECT_NETWORK], code=".github/workflows/ci-nightly.yml",
       unit=".github/workflows/ci-nightly.yml", law=["§56"], verdict=VERDICT_KEEP,
       why="informational（continue-on-error），永不是 required check；零状态改变。",
       audit=AUDIT_GHA, reversible="n/a"),
    # ---------------------------------------------------------------- 补完（2026-09-29 复核补进的六条）
    # 下面六条是 issue #451 附的那份只读审计**没数到**的——它们散在 .pkg 安装器、
    # 雷达的重试阶梯、store2 的备份、syncd 的台账、两个壳的通知溢出、iOS 端。
    # 一条都还没修，但总账的价值就在于「数得到」：记下来，账本盯着，别再隐形。
    _b(slug="pkg_postinstall", zh=".pkg 安装后脚本（root）", en="pkg postinstall (root)",
       runner=RUNNER_SHELL, cadence="每次 .pkg 安装 / Sparkle 自动更新落地",
       effect=[EFFECT_STATE, EFFECT_NETWORK], code="mac/package.sh",
       law=["§74", "§56"], verdict=VERDICT_KEEP,
       why="以 root 跑：rsync 主副本盖进用户的 live checkout、替用户跑 install.sh"
           "（装 launchd + 改 crontab）、**并 open 一次已经退役的旧 app**——2026-09-07 "
           "「live tree 被回退」事故的整条因果链就在这里。§74 已禁止 .pkg 写进 checkout，"
           "旧 app 的自动拉起该随 P8 一起摘掉；本轮先把它记进总账，别再是个没人数到的东西。",
       audit="/var/log/install.log", reversible="no——盖过去的工作副本要从 backup 手动捞",
       overlaps=["auto_deploy", "mac_notify_relay"]),
    _b(slug="radar_retry_ladder", zh="雷达提取的重试阶梯", en="radar extraction retry ladder",
       runner=RUNNER_CRON, cadence="pass 内一次退避 + 跨 pass 台账最多 5 次（iCloud 驱逐 20 次）",
       effect=[EFFECT_SPEND], code="act/radar.py:_extract_with_retry",
       law=["§47", "§42"], verdict=VERDICT_KEEP,
       why="宪法第 11 条要的「失败不外溢 + 放弃要留痕」，但代价是**同一篇笔记最多被"
           "计费提取 20 次**（`FAILED_MAX_ATTEMPTS_DEFERRED`，给 iCloud 还没落地的文件留的）。"
           "今天它只跟着源开关走，没有自己的上限旋钮——够不够、要不要收，留给 owner 看这张表时定。",
       switch=["features.obsidian_radar", "obsidian_enabled"], kind=KIND_BOOL,
       audit="state/radar_failed.json（重试台账）", reversible="n/a（钱花了就是花了）",
       overlaps=["radar_obsidian"]),
    _b(slug="store2_backup", zh="激活前的 registry 全量备份",
       en="pre-activation registry backup",
       runner=RUNNER_ACTD, cadence="每次 store2 激活尝试（被拒后 6 小时再试一轮）",
       effect=[EFFECT_STATE], code="act/lib/store2/activate.py:backup_registry",
       law=["§53"], verdict=VERDICT_KEEP,
       why="D2 要的那份「切换失败能手动导回去」的备份，**刻意永不覆盖**（已存在就加 -2/-3）。"
           "代价：`state/backups/registry-<ts>/` 只增不减，既没有 cap 也没有 retention"
           "（防腐 #4 的例外，没人立过案）。设置页的「录制数据与磁盘」只**量**它、不清它。",
       audit="备份目录旁的 .manifest.json", reversible="yes（备份本身就是回程票）"),
    _b(slug="syncd_delivered_ledger", zh="云同步的已送达台账",
       en="sync delivered ledger",
       runner=RUNNER_LAUNCHD, cadence="每 10 秒一 pass，整份读回 + 追加",
       effect=[EFFECT_STATE], code="act/syncd.py:_ledger_append",
       law=["§31"], verdict=VERDICT_KEEP,
       why="`state/sync/delivered.jsonl` 是纯 append 且**不走 logcap**——同一个仓库里"
           "别的台账都带帽（防腐 #4）。没配对时整条静默，"
           "所以今天不痛；配上之后它每 10 秒被整份读一次，涨起来是 O(n) 的。",
       switch=[], kind=KIND_NONE, audit="它自己就是台账",
       reversible="n/a（只记送达）", overlaps=["syncd"]),
    _b(slug="notify_burst_cap", zh="通知溢出即删", en="notification burst overflow drop",
       runner=RUNNER_SHELL, cadence="每次队列扫描（单轮 >5 条即触发）",
       effect=[EFFECT_DELETE, EFFECT_NOTIFY], code="shell/Sources/NotifyRelay.swift",
       law=["§28"], verdict=VERDICT_KEEP,
       why="一轮超过 5 条时，多出来的队列文件**直接删掉、从不弹**，只合成一条「+N 条」。"
           "横幅是对的（不刷屏），删文件不对——那是队列里唯一一份内容，没有回程票，"
           "也没有任何一行日志说删了什么。**它一把开关都没有**：壳侧的中继只读 "
           "`review_notify` 一个键，§28 的 `notify_proposals` 管不到它（`grep notify_proposals shell/` 零命中），"
           "所以这一行的 switch 列是空的、明账挂在 baseline 上——在总账里给它填一把"
           "管不着它的开关，就是这张表最不该犯的错。合成横幅保留、溢出条目改成落痕，值得单独一张卡。",
       switch=[], kind=KIND_NONE, audit=AUDIT_NONE,
       reversible="no（队列文件已 unlink）", overlaps=["shell_notify_relay"]),
    _b(slug="ios_refresh_and_notify", zh="iOS 前台激活即刷新 + 逐卡本地通知",
       en="iOS foreground refresh + per-card local notifications",
       runner=RUNNER_SHELL, cadence="每次 app 变 active，以及每次刷新后",
       effect=[EFFECT_NETWORK, EFFECT_NOTIFY], code="ios/Sources/AppState.swift:maybeNotify",
       law=["§28", "§31"], verdict=VERDICT_KEEP,
       why="iOS 端有**第二套通知策略**：没有安静时段、没有分类开关、阻塞卡逐张一条"
           "（不合并），与 Mac 侧 §28 的四把偏好互不知情。开关只有系统级授权。"
           "两套并成一套是 §28 该管的事，本轮先记账。",
       switch=[], kind=KIND_NONE, audit=AUDIT_NONE,
       reversible="n/a（一条横幅）", overlaps=["shell_notify_relay", "notify_transitions"]),

    _b(slug="gha_fresh_install", zh="夜间全新装机验收", en="nightly fresh-install",
       runner=RUNNER_GHA, cadence="GitHub cron + push 到 main",
       effect=[EFFECT_NETWORK], code=".github/workflows/fresh-install.yml",
       unit=".github/workflows/fresh-install.yml", law=["§69"], verdict=VERDICT_KEEP,
       why="§69「一条命令装到能用」的守夜人；跑在干净 runner 上，不碰任何真机。",
       audit=AUDIT_GHA, reversible="n/a"),
)


def by_slug(slug: str) -> Optional[Behaviour]:
    """总账里的那一行；不认识的 slug → None（调用方 fail-closed）。"""
    for row in LEDGER:
        if row.slug == slug:
            return row
    return None


def slugs() -> tuple:
    return tuple(row.slug for row in LEDGER)


def kept() -> tuple:
    """还活着的行（verdict=keep）——设置页与门只管这些。"""
    return tuple(row for row in LEDGER if row.verdict == VERDICT_KEEP)


def live_fields() -> tuple:
    """actd 每 pass 要从盘上现读的 Config 字段名（去重、稳定序）。

    §81 不变量 2 的真源：``act/actd.py:_refresh_live_switches`` 遍历它，
    所以「总账里加一行带开关的 actd 行为」= 那把开关自动变热，没有第二处要改。
    """
    seen: list = []
    for row in LEDGER:
        if not row.live:
            continue
        for name in row.switch:
            if name not in seen:
                seen.append(name)
    return tuple(seen)


# --------------------------------------------------------------------------- #
# 「它现在开着吗」
# --------------------------------------------------------------------------- #
def _switch_value(cfg, name: str):
    """一个开关字段的值。两种拼法：扁平 Config 字段 / ``features.<flag>``。
    （第三种 ``<块>.<键>``（cfg.raw）只服务过 `autodispatch.enabled`，随 §51 第二条
    lane retired D86；再出现就读成 None = 关，fail-closed，总账测试钉死不许出现。）"""
    head, _, tail = name.partition(".")
    if not tail:
        return getattr(cfg, name, None)
    if head == "features":
        return cfg.feature(tail) if hasattr(cfg, "feature") else True
    return None


# 字符串型旋钮里表示「关」的字面量（`digest.frequency: off` 是第一个客户——
# 它是 off|daily|every2days|weekly 的枚举，`bool("off")` 为真会把一把出厂关着
# 的旋钮读成开着）。与 config.py 的布尔词表同源，另收一个 "off"。
_OFF_WORDS = frozenset({"", "off", "false", "no", "0", "none"})


def _positive_int(value) -> bool:
    """天数 / 条数型旋钮的判真：> 0 才算开；坏值按关（配错一个字不许让一条会删
    数据的规则悄悄跑起来）。"""
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _truthy(value, kind: str) -> bool:
    if kind == KIND_THRESHOLD:
        return _positive_int(value)
    if isinstance(value, str):
        return value.strip().lower() not in _OFF_WORDS
    return bool(value)


def enabled(slug: str, cfg=None) -> bool:
    """这条行为现在开着吗（**默认现读配置**——热开关的实现点）。

    - 不认识的 slug → False（fail-closed，与 :func:`sources.enabled` 同纪律）；
    - ``verdict != keep`` 的行 → False（退役/并入的行为永不再跑）；
    - ``kind=none``（还没有开关）→ True：它今天本来就一直在跑，总账只是把这个
      事实写明并挂进 baseline 欠着，不是偷偷把人家关掉。
    - 其余 = ``switch`` 各字段按 ``kind`` 判真后的**合取**（任一假即关）。

    ``cfg=None`` 时自己 ``load_config()``；调用方已在手上有新鲜 cfg 的（actd 的
    pass 内）传进来省一次 parse。配置读不出来 → 按「盘上没有这一键」= 出厂默认，
    由 ``config.Config()`` 兜底（本函数绝不抛，宪法第 11 条）。
    """
    return row_enabled(by_slug(slug), cfg)


def row_enabled(row: Optional[Behaviour], cfg=None) -> bool:
    """:func:`enabled` 的按行形（门与判例用注入的合成行求值时走这条）。"""
    if row is None or row.verdict != VERDICT_KEEP:
        return False
    if row.kind == KIND_NONE or not row.switch:
        return True
    cfg = cfg if cfg is not None else _fresh_config()
    return all(_truthy(_switch_value(cfg, name), row.kind) for name in row.switch)


def _fresh_config():
    try:
        return config.load_config()
    except Exception:  # noqa: BLE001 - 坏 config 不许让开关判定崩掉调用方
        return config.Config()


# --------------------------------------------------------------------------- #
# 「它刚才动手了」— 一份带帽的 append-only 日志
# --------------------------------------------------------------------------- #
def audit_path():
    return config.STATE_DIR / AUDIT_LOG


def _clip(value):
    """附加字段的消毒：标量原样，其余 str() 后截断——日志不当数据库使。"""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_AUDIT_FIELD_CAP]


def audit(slug: str, action: str, path=None, **fields) -> bool:
    """记一行「<slug> 做了 <action>」。返回是否落盘成功；**永不抛**。

    形状 ``{"ts", "slug", "action", ...fields}``——一行一个 JSON，机器读。
    `action` 惯例：``ran``（跑了一轮）/ ``skipped``（开关关着或没到点）/
    ``acted``（真改了东西，带计数字段）。落盘后按 :data:`AUDIT_MAX_BYTES`
    自压缩（防腐 #4）。
    """
    entry = {"ts": _now_iso(), "slug": slug, "action": action}
    for key in sorted(fields):
        entry[key] = _clip(fields[key])
    target = path or audit_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logcap.cap(target, AUDIT_MAX_BYTES)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_lines(path) -> list:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _audit_row(line: str):
    """一行 JSON → dict；坏行 / 非 dict → None（日志是可截断的，坏行必须能吞）。"""
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def recent(limit: int = 50, path=None) -> list:
    """最近 N 行审计（新的在前）。文件不在 / 坏行 → 跳过，绝不抛。"""
    cap = max(0, int(limit or 0))
    rows = (_audit_row(line) for line in reversed(_audit_lines(path or audit_path())))
    out = [row for row in rows if row is not None]
    return out[:cap]


# --------------------------------------------------------------------------- #
# 投影（门 / 文档 / 设置页共用同一份）
# --------------------------------------------------------------------------- #
def inventory() -> dict:
    """总账的 JSON 投影（server / web 走 HTTP 时用的形状）。

    **真源永远是本模块的 :data:`LEDGER`**，不另存一份 JSON 进仓库——第二份文件
    就是第二个真源，还得再养一条保鲜判例（`ui/parity/native-inventory.json`
    要 commit 是因为它的源在 Swift，Python 读不到；这里源就在手边）。
    文档纪律（防腐 #5）：任何文档要写「有多少条自动行为」都写
    "truth = act/lib/automation.py:LEDGER"，不许手抄数字。
    """
    return {"behaviours": [_row_wire(row) for row in LEDGER]}


def _row_wire(row: Behaviour) -> dict:
    return {
        "slug": row.slug, "zh": row.zh, "en": row.en,
        "runner": row.runner, "cadence": row.cadence,
        "effect": list(row.effect), "code": row.code, "law": list(row.law),
        "verdict": row.verdict, "why": row.why,
        "switch": list(row.switch), "kind": row.kind,
        "audit": row.audit, "reversible": row.reversible,
        "merged_into": row.merged_into, "overlaps": list(row.overlaps),
        "unit": row.unit, "costly": row.costly, "live": row.live,
    }


def _cli_json() -> int:
    print(json.dumps(inventory(), ensure_ascii=False, indent=1, sort_keys=True))
    return 0


def _cli_list() -> int:
    for row in LEDGER:
        print("%-26s %-8s %-8s %-7s %s"
              % (row.slug, row.runner, row.verdict,
                 "on" if enabled(row.slug) else "off", row.zh))
    return 0


def _cli_enabled(slug: str) -> int:
    """`--enabled <slug>` 的出口码：0 = on / 3 = off / 2 = 不认识这条。

    与 :func:`act.lib.sources.main` 逐字同款——**1 号出口刻意空着**，那是 python
    自己崩掉的码（缺模块 / 缺 PyYAML / 未捕获异常）。「关」必须独占一个不会被
    故障撞上的出口，shell 调用方才能对一切故障 fail-open（照常跑）。
    """
    if by_slug(slug) is None:
        print("unknown behaviour: %s" % slug)
        return 2
    on = enabled(slug)
    print("on" if on else "off")
    return 0 if on else 3


def main(argv=None) -> int:
    """CLI（同 :mod:`act.lib.sources` 的出口码纪律）::

        python3 -m act.lib.automation --list
        python3 -m act.lib.automation --json
        python3 -m act.lib.automation --enabled <slug>
    """
    import argparse

    parser = argparse.ArgumentParser(prog="automation", description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--enabled", metavar="SLUG")
    args = parser.parse_args(argv)

    if args.json:
        return _cli_json()
    if args.list:
        return _cli_list()
    if args.enabled:
        return _cli_enabled(args.enabled)
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover - CLI wiring
    raise SystemExit(main())
