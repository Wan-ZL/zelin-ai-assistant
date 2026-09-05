#!/usr/bin/env python3
"""Demo-data seeder — writes an ENTIRELY FICTIONAL state/dashboard.json.

Purpose: launch the Mac menu-bar app against a fake AIASSISTANT_HOME for
README screenshots / demo videos, with every card type and edge state
visible. See docs/DEMO.md for the full recording workflow.

    python3 scripts/demo_seed.py /tmp/assistant-demo
    python3 scripts/demo_seed.py /tmp/assistant-demo --scene running
    python3 scripts/demo_seed.py /tmp/assistant-demo --english   # or --lang en
    python3 scripts/demo_seed.py /tmp/assistant-demo --check

Language (issue #18): the dataset is authored once, in Chinese (the default —
existing docs/scripts stay valid byte-for-byte). ``--english`` / ``--lang en``
runs the finished dashboard through ``_localize``, a string → string table
(``_EN``) applied to every string value, so the two languages can never drift
in SHAPE — one dataset, two vocabularies. tests/test_demo_seed.py pins that the
English output carries no CJK character at all: add a Chinese string to the
dataset without its ``_EN`` row and that test fails.

Field names/types mirror what act/lib/dashboard.py:build_dashboard emits
(CONTRACT.md §2 incl. v0.10) so the Swift decoders in mac/Sources/Models.swift
accept every item. Critical invariants (a violation silently DROPS cards in
the app, which defeats the whole point):

- every ``sources`` entry has all four of who/channel/date/quote as strings
  (Swift ``Source`` fields are non-optional; one null kills the whole array);
- started_at / dispatched_at / review_at / accepted_at are epoch ints;
  generated_at / trashed_at are ISO-8601 strings;
- queued running items carry NO session_id/copy_cmd keys (no session yet).

v-next add-only fields (信任矩阵/排队原因/捎话；wire 真源 =
docs/design/vnext-amendments.md 的 ratification-ready 草案 + 已落仓实现——
``act/lib/risk.py`` 词表、``act/lib/store2/schema.sql`` CHECK 集合、
``web/src/steer.ts`` 解析器；出入以它们为准)：

- ``origin_trust``（可选 string，``hand``/``external``）：信任矩阵档位——
  ``hand`` = owner 亲笔（quick capture / 直跑框 / Slack self-DM），可
  auto-dispatch；``external`` = 外部渠道铸卡，永不自动派发且强制 plan
  展开（W17）；**缺席** = AI 提案/会议音频等中间档（要人批、不提级）；
- ``effective_tier``（可选 string）：审批时生效档位——external 卡提到 T2，
  **只升不降**（低于声明 tier 视为违约；声明 ``tier`` 字段永不改写）；
- ``queued_reason``（可选，仅 queued 行）：结构化排队原因
  ``{kind, detail?, blocking_id?}``——``waiting_card`` 必带 ``blocking_id``
  （被等卡 id）；过渡期也接受纯字符串形（web/src/steer.ts 双兼容）；
- ``steers``（可选 list，运行行）：owner 捎话（steer）回执台账，每条
  ``{text, ts, status, delivered_at}``——``ts`` 为 ISO 字符串（带时间戳的
  dedup key，重复文本合法），status=delivered 必带 ISO delivered_at，
  其余状态必须为 null（诚实投递状态，绝不假装送达）。

All names, repos, quotes and drafts below are fictional (example-bench,
inkweld, alex.doe, sam.rivera…) — never real coworker or company data.

Stdlib only; runs from any checkout without PYTHONPATH.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

SCENES = ("captured", "initial", "approved", "running", "steer", "review", "done")

# §60（D21）两段式编号：主键 P-<n> 出生即定、终身不变；工作编号 R-<m> 只在
# 进入 approved 时分配。hero 卡主键 P-101，approved 起工作编号 R-101——
# 看板上人看到的是 display_id（= work_id or id）。
HERO_ID = "P-101"       # the card the --scene flag walks through the pipeline
HERO_WORK_ID = "R-101"  # allocated the moment the hero is approved (scene>=approved)

HOME = "~/Projects/zelin-ai-assistant"

# v-next 词表（TODO(contract): 尚未入 CONTRACT，以 docs/design/vnext-amendments.md
# 草案为准；validator 与 demo 数据共用这一份，改这里两边同步）。
# origin_trust 与 act/lib/risk.py TRUST_* / store2 schema.sql CHECK 集合对齐
# （信任矩阵更细的 self_dm/meeting/ai 档在 wire 上折叠为 hand/external/缺席）。
ORIGIN_TRUST = ("hand", "external")
TIER_RANK = {"T0": 0, "T1": 1, "T2": 2}
# CONTRACT §51 词表（waiting_budget retired v0.48.7，D9——kind 值永不复用）
QUEUED_REASON_KINDS = ("waiting_card", "concurrency")
STEER_STATUSES = ("queued", "delivered", "dropped")


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(now: dt.datetime, days: int) -> str:
    return (now + dt.timedelta(days=days)).date().isoformat()


def _src(who: str, channel: str, date: str, quote: str) -> dict:
    # Swift Source: 4 non-optional Strings — never emit null here.
    return {"who": who, "channel": channel, "date": date, "quote": quote}


# --------------------------------------------------------------------------- #
# fictional dataset
# --------------------------------------------------------------------------- #
def _hero_card(now: dt.datetime) -> dict:
    """P-101 as a needs_approval card (scene=initial) — no work number yet (§60)."""
    deadline = _date(now, 6)
    return {
        "id": HERO_ID,
        "title": "example-bench: leaderboard 一键导出评测报告",
        "summary": "给评测面板加一个「导出报告」按钮，批准后 AI 会在 example-bench 开 draft PR，不动主分支。",
        "target_repo": "~/Projects/example-bench",
        "target_name": "example-bench",
        "target_kind": "existing",
        "tier": "T1",
        "tier_hint": "一键可批",
        # meeting/audio-born → 信任矩阵中间档：要人批但不提级——wire 上表现为
        # 「无 origin_trust 字段」（词表只有 hand/external，缺席即中间档）
        "hardness": "hard",
        "deadline": deadline,
        "days_left": (dt.date.fromisoformat(deadline) - now.date()).days,
        "repeated": 2,
        "cost_usd": 12,
        "show_cost": True,
        "green_sign": False,
        "disagreement": None,
        "improvement_of": None,
        "sources": [
            _src("manager", "meeting", _date(now, -7),
                 "能不能加个按钮，一键把 leaderboard 导出成报告发出去"),
            _src("alex.doe", "slack", _date(now, -2),
                 "上周说的导出报告那个还做吗？周会又有人问了"),
        ],
        "plan": [
            "在 example-bench 的 dashboard 页加「导出报告」按钮",
            "后端把当前 leaderboard 渲染成 markdown + png",
            "产出 draft PR，不 merge",
        ],
        "outputs": ["draft PR: example-bench#42"],
        "dod": [
            "dashboard 页出现「导出报告」按钮",
            "点击后生成 markdown + png 到 exports/",
            "draft PR 通过 CI",
        ],
        "processing": False,
        "delivery_mode": "repo",
    }


def _hero_plan_dod(now: dt.datetime) -> dict:
    card = _hero_card(now)
    return {k: card[k] for k in ("summary", "plan", "dod", "sources")}


def _needs_approval(now: dt.datetime) -> list[dict]:
    deadline_t2 = _date(now, 13)
    return [
        _hero_card(now),
        {
            "id": "P-102",
            "title": "inkweld: 搭对外可访问的 demo 环境 + 种子数据",
            "summary": "新建 inkweld-demo 仓库，部署一个带种子数据的公开 demo 站。对外可见，所以需要你文字确认。",
            # §37 LLM 便车给的短名（无 user_titled）：提案是摘要优先面，卡面仍是 summary，
            # 短名只在详情面「显示名」一行——钉住 display_title 不许挤掉大白话摘要
            "display_title": "搭 inkweld 公开 demo 站",
            "target_repo": "~/Projects/inkweld-demo",
            "target_name": "inkweld-demo",
            "target_kind": "new",
            "tier": "T2",
            "tier_hint": "需文字确认",
            # 外部渠道催生（sales/客户）→ external；声明已是 T2，提级无感
            "origin_trust": "external",
            "effective_tier": "T2",
            "hardness": "hard",
            "deadline": deadline_t2,
            "days_left": (dt.date.fromisoformat(deadline_t2) - now.date()).days,
            "repeated": 3,
            "cost_usd": 85,
            "show_cost": True,
            "green_sign": True,
            "disagreement": "demo 用真实数据还是合成数据，manager 和 alex.doe 意见不一致",
            "improvement_of": None,
            "sources": [
                _src("manager", "meeting", _date(now, -9),
                     "客户那边想先看个能点的 demo，不用完整功能"),
                _src("sam.rivera", "slack", _date(now, -3),
                     "demo 环境这事本周能定吗？销售又来问了"),
            ],
            "plan": [
                "新建 inkweld-demo 仓库（从 inkweld 模板裁剪）",
                "写种子数据生成脚本（全部合成数据）",
                "部署到内部 PaaS，配只读演示账号",
                "README 写清 demo 边界与重置方式",
            ],
            "outputs": [],
            "dod": [
                "demo 站可公网访问，演示账号能登录",
                "数据全部合成，无任何真实客户信息",
                "一条命令可重置 demo 数据",
            ],
            "processing": False,
            "delivery_mode": "repo",
        },
        {
            "id": "P-103",
            "title": "起草 Q3 planning 的 one-pager（中英双语）",
            "summary": "写作任务：Q3 planning 一页纸初稿，会话内交付成稿，不动任何仓库。",
            "target_repo": "~/Projects/workbench",
            "target_name": "workbench",
            "target_kind": "existing",
            "tier": "T1",
            "tier_hint": "外部来源提级，需文字确认",
            # W17 展示位：gmail（外部渠道）铸卡 → effective_tier 提到 T2 且
            # 强制 plan 展开（act/lib/risk.py effective_tier）；声明 tier 保持
            # T1 不被改写（add-only，不动老字段语义）
            "origin_trust": "external",
            "effective_tier": "T2",
            "hardness": "soft",
            "deadline": _date(now, 3),
            "days_left": 3,
            "repeated": 1,
            "cost_usd": None,
            "show_cost": False,
            "green_sign": False,
            "disagreement": None,
            "improvement_of": None,
            "sources": [
                _src("manager", "gmail", _date(now, -1),
                     "Q3 planning 下周三要一版 one-pager，先给个提纲也行"),
            ],
            "plan": [
                "梳理 Q2 结果与 Q3 候选方向",
                "按 objective / key results / risks 出提纲",
                "中英双语成稿，放在结束总结里等你定稿",
            ],
            "outputs": [],
            "dod": ["提纲覆盖 3 个 objective", "中英双语", "一页以内"],
            "processing": False,
            "delivery_mode": "chat",
        },
        # raising placeholder — exact shape dashboard.py emits for status=raising
        {
            "id": "P-104",
            "title": "统一 example-bench 和 inkweld 的 lint 配置",
            "summary": "统一 example-bench 和 inkweld 的 lint 配置",
            "tier": "T1",
            "tier_hint": "AI 研究中",
            "processing": True,
            "sources": [],
            "plan": [],
            "dod": [],
            "show_cost": False,
            "delivery_mode": "repo",
        },
    ]


def _running(now: dt.datetime) -> list[dict]:
    e = _epoch(now)
    return [
        {
            "id": "P-105",
            "name": "example-bench: 修 flaky 的 e2e 测试（retry 逻辑）",
            "session_id": "a7c9e2f4-8b31-4d6a-9e05-2f7c8d1b3a54",
            "short_id": "a7c9e2f4",
            "copy_cmd": "claude attach a7c9e2f4",
            "agent_name": "fix flaky e2e retries",
            "cwd": "~/Projects/example-bench",
            "state": "working",
            # 手打卡（quick capture 直跑框）→ 信任矩阵 auto-dispatch 档
            "origin_trust": "hand",
            "started_at": e - 1500,
            "summary": "e2e 套件里 3 个用例偶发超时，加统一的 retry + 诊断日志。",
            "plan": [
                "复现并定位 3 个 flaky 用例的超时点",
                "抽一个带指数退避的 retry helper",
                "连续跑 40 轮验证零失败",
            ],
            "dod": ["连续 40 轮 e2e 零失败", "retry helper 有单测"],
            "log": f"{HOME}/state/logs/R-105.log",
            "dispatched_at": e - 1560,
            "delivery_mode": "repo",
            "last_error": None,
        },
        # queued: approved but not yet dispatched — NO session_id/copy_cmd keys
        {
            "id": "P-106",
            "name": "inkweld: README 快速上手一节重写",
            "state": "queued",
            "summary": "现在的快速上手照着跑会卡在第二步，按新安装脚本重写。",
            "plan": ["按 install.sh 现状重走一遍安装流程", "重写 README 快速上手一节"],
            "dod": ["新人照 README 十分钟内跑起来"],
            "delivery_mode": "repo",
            "dispatch_error": None,
            # Slack self-DM 铸卡 = owner 亲笔 → 盖 hand（auto-dispatch 档）；
            # 并发位满先排队，UI 由 kind 渲染「排队中 · 等并发位」chip
            # （结构化原因走闭集 kind，非自由文本——web/src/steer.ts 词表）
            "origin_trust": "hand",
            "queued_reason": {"kind": "concurrency"},
        },
        {
            "id": "P-107",
            "name": "example-bench: 数据集 v2 的 loader 兼容层",
            "session_id": "c3f8a2d1-6e97-4b28-a1c4-9d5e0f7b2c86",
            "short_id": "c3f8a2d1",
            "copy_cmd": "claude attach c3f8a2d1",
            "agent_name": "dataset v2 loader shim",
            "cwd": "~/Projects/example-bench",
            "state": "working",
            "origin_trust": "hand",
            "started_at": e - 7200,
            "summary": "数据集 v2 换了 schema，加兼容层让老评测脚本不用改。",
            "plan": ["对比 v1/v2 schema 差异", "写字段映射兼容层", "老脚本回归全过"],
            "dod": ["v1 脚本零改动跑通 v2 数据集"],
            "log": f"{HOME}/state/logs/R-107.log",
            "dispatched_at": e - 7260,
            "delivery_mode": "repo",
            "last_error": "auto-resume attempt 1 failed: session busy — retried in 60s, OK",
        },
    ]


def _needs_input(now: dt.datetime) -> list[dict]:
    return [
        {
            "id": "P-108",
            "name": "给 inkweld 接 Supabase auth（需要 service key）",
            "session_id": "d4b8f1c6-2a53-4e79-b8d0-1c6f9a3e5d27",
            "short_id": "d4b8f1c6",
            "copy_cmd": "claude attach d4b8f1c6",
            "agent_name": "inkweld supabase auth",
            "state": "blocked",
            "waiting_for": "permission",
        },
    ]


def _review(now: dt.datetime) -> list[dict]:
    e = _epoch(now)
    return [
        {
            "id": "P-109",
            "name": "example-bench: 评测缓存层（重复 run 提速 10x）",
            "summary": "给 runner 加 content-hash 缓存，重复评测直接读缓存。",
            "dod": ["同 config 重复 run 命中缓存", "缓存失效逻辑有单测", "CI 全绿"],
            "session_id": "e5a1c7d9-4f62-4b8e-9a35-7d0c2b8f4e61",
            "short_id": "e5a1c7d9",
            "copy_cmd": "cd '~/Projects/example-bench' && claude --resume "
                        "e5a1c7d9-4f62-4b8e-9a35-7d0c2b8f4e61",
            "agent_name": "eval cache layer",
            "state": "review",
            "cwd": "~/Projects/example-bench",
            "delivered_summary": "已在 example-bench 开 draft PR #87：runner 加 content-hash "
                                 "缓存层，重复 run 从 ~12min 降到 ~70s；失效逻辑带 6 个单测，CI 全绿。",
            "final_draft": None,
            "plan": ["设计 config 的 content-hash 规则", "runner 读写缓存", "补失效逻辑单测"],
            "sources": [
                _src("alex.doe", "slack", _date(now, -6),
                     "每次改一行 config 都要全量重跑，太费时间了"),
            ],
            "log": f"{HOME}/state/logs/R-109.log",
            "dispatched_at": e - 9000,
            "review_at": e - 1800,
            "delivery_mode": "repo",
            # §64 AI 摘要 + 完成度评语（只是建议；评于待验收期）
            "assessment": {
                "summary": "给评测加了缓存，重跑快十倍，等你看 PR",
                "verdict": "建议验收",
                "verdict_reason": "清单三条都有对应交付：缓存命中、失效单测 6 个、CI 全绿",
                "at": e - 1500,
            },
        },
        {
            "id": "P-110",
            "name": "写本周 weekly report（发出去前先过目）",
            "summary": "会话内交付：本周周报初稿，验收后自己粘贴发送。",
            "dod": ["覆盖本周两个 project 的进展", "中英双语", "不超过一页"],
            "session_id": "f2d6b8a3-9c14-4d57-8e60-3b7a5f1c9d42",
            "short_id": "f2d6b8a3",
            "copy_cmd": "cd '~/Projects/workbench' && claude --resume "
                        "f2d6b8a3-9c14-4d57-8e60-3b7a5f1c9d42",
            "agent_name": "weekly report draft",
            "state": "review",
            "cwd": "~/Projects/workbench",
            "delivered_summary": "周报初稿完成：两个 project 各一段进展 + 下周计划，中英双语。",
            "assessment": {
                "summary": "周报草稿写好了，缺一页内的篇幅确认",
                "verdict": "需要拍板",
                "verdict_reason": "清单第 3 条「不超过一页」要你按目标读者定：现在双语版约一页半",
                "at": e - 900,
            },
            "final_draft": (
                "**Weekly Report | 周报**\n"
                "\n"
                "## example-bench\n"
                "- 评测缓存层 draft PR 已提交，重复 run 提速 ~10x（12min → 70s）。\n"
                "  Eval cache layer in draft PR; repeat runs ~10x faster.\n"
                "- e2e flaky 修复中：retry 逻辑已连续 40 轮零失败。\n"
                "  Flaky e2e fix in progress; 40 consecutive green runs with retries.\n"
                "\n"
                "## inkweld\n"
                "- 对外 demo 环境方案已成型，等 green light 后开工。\n"
                "  Public demo environment proposal ready, pending sign-off.\n"
                "- README 快速上手重写已排队。\n"
                "  Quick-start rewrite queued.\n"
                "\n"
                "## Next week\n"
                "- leaderboard 一键导出评测报告（PR review 中）\n"
                "- Q3 planning one-pager 初稿\n"
            ),
            "plan": ["汇总本周两个 project 的进展", "中英双语成稿", "放进结束总结等验收"],
            "sources": [
                _src("manager", "slack", _date(now, -1),
                     "这周的 weekly 别忘了，最好周五中午前"),
            ],
            "log": f"{HOME}/state/logs/R-110.log",
            "dispatched_at": e - 2400,
            "review_at": e - 600,
            "delivery_mode": "chat",
        },
    ]


def _completed(now: dt.datetime) -> list[dict]:
    e = _epoch(now)
    return [
        {
            "id": "P-111",
            "name": "example-bench: CI 加 lint gate（ruff + prettier）",
            "session_id": "b9e3d5f7-1a48-4c26-9b70-5e2d8c4a6f13",
            "short_id": "b9e3d5f7",
            "copy_cmd": "cd '~/Projects/example-bench' && claude --resume "
                        "b9e3d5f7-1a48-4c26-9b70-5e2d8c4a6f13",
            "agent_name": "ci lint gate",
            "state": "delivered",
            "cwd": "~/Projects/example-bench",
            "summary": "PR 必须过 ruff + prettier 才能合并。",
            "delivered_summary": "draft PR #61 验收通过：lint gate 上线，历史违规一次性清零。",
            "accepted_at": e - 86400,
            "dod": ["PR 未过 lint 无法合并", "本地一条命令自动修复"],
        },
        {
            "id": "P-112",
            "name": "把周会 action items 自动整理成清单",
            "session_id": "a1f5c8e2-7d39-4b64-8c05-2e9b6d3f7a58",
            "short_id": "a1f5c8e2",
            "copy_cmd": "cd '~/Projects/workbench' && claude --resume "
                        "a1f5c8e2-7d39-4b64-8c05-2e9b6d3f7a58",
            "agent_name": "meeting action items",
            "state": "delivered",
            "cwd": "~/Projects/workbench",
            "summary": "每次周会纪要落盘后自动产出 action-item 清单。",
            "delivered_summary": "脚本已交付：会议纪要落盘后 5 分钟内生成清单并通知。",
            "accepted_at": e - 259200,
            "dod": ["每条 action item 带 owner 和 deadline"],
        },
    ]


def _debt(now: dt.datetime) -> list[dict]:
    return [
        {
            "id": "P-113",
            "title": "example-bench 的 README 安装一节过时了",
            "summary": "setup 命令已经跑不通，新人第一步就卡住。",
            "hardness": "soft",
            "type": "engineering",
            "sources": [
                _src("sam.rivera", "slack", _date(now, -5),
                     "README 里那个 setup 命令已经跑不通了吧？"),
            ],
        },
        {
            "id": "P-114",
            "title": "周会纪要没人整理，action items 常丢",
            "summary": "口头说好的事没人记，下周就忘。",
            # §37 用户钦定名（user_titled）：压过 summary 成为卡面标题（server 只在为真时发键）
            "display_title": "周会 action items 自动整理",
            "user_titled": True,
            "hardness": "hard",
            "type": "process",
            "sources": [
                _src("manager", "meeting", _date(now, -12),
                     "上周说好的两件事这周都没人记得"),
            ],
        },
        {
            "id": "P-115",
            "title": "inkweld 报错日志太吵，真错误被淹没",
            "summary": "warning 刷屏，出真错时没人看得见。",
            "hardness": "soft",
            "type": "engineering",
            "sources": [
                _src("alex.doe", "slack", _date(now, -4),
                     "日志一分钟滚几百行 warning，真出事根本发现不了"),
            ],
        },
    ]


def _trash(now: dt.datetime) -> list[dict]:
    return [
        {
            "id": "P-116",
            "title": "给 slack 加自动回复 bot",
            "summary": "检测到的建议，被拒绝进回收站——不想要自动回复。",
            "kind": "suggestion",
            "trashed_at": _iso(now - dt.timedelta(days=2)),
            "trash_reason": "rejected",
            "permanent": False,
            "type": "engineering",
            "hardness": "soft",
        },
    ]


# --------------------------------------------------------------------------- #
# scenes — walk the hero card (P-101 → work number R-101 once approved) through the pipeline
# --------------------------------------------------------------------------- #
def _epoch(now: dt.datetime) -> int:
    return int(now.timestamp())


def _hero_captured(now: dt.datetime) -> dict:
    """P-101 as a raising placeholder — the moment right after a meeting
    recording was ingested and radar picked the requirement up (scene=captured).
    Same shape dashboard.py emits for status=raising (cf. P-104)."""
    return {
        "id": HERO_ID,
        "title": "leaderboard 一键导出评测报告",
        "summary": "leaderboard 一键导出评测报告",
        "tier": "T1",
        "tier_hint": "AI 研究中",
        "processing": True,
        "sources": [],
        "plan": [],
        "dod": [],
        "show_cost": False,
        "delivery_mode": "repo",
    }


def _hero_queued(now: dt.datetime) -> dict:
    h = _hero_plan_dod(now)
    return {
        "id": HERO_ID,
        "name": _hero_card(now)["title"],
        "state": "queued",
        "summary": h["summary"],
        "plan": h["plan"],
        "dod": h["dod"],
        "delivery_mode": "repo",
        "dispatch_error": None,
        # 同仓 P-105（工作编号 R-105）还在跑 → 排队等它；UI chip「排队中 · 等 R-105」
        # （blocking_id = 主键，blocking_display_id = 展示编号，§60 add-only；
        # meeting-born 卡无 origin_trust 字段——中间档，人批后照常排队派发）
        "queued_reason": {"kind": "waiting_card", "blocking_id": "P-105",
                          "blocking_display_id": "R-105"},
    }


def _hero_running(now: dt.datetime) -> dict:
    h = _hero_plan_dod(now)
    e = _epoch(now)
    return {
        "id": HERO_ID,
        "name": _hero_card(now)["title"],
        "session_id": "b1e4d7a2-5c38-4f9e-8d21-6a0b3c9e7f45",
        "short_id": "b1e4d7a2",
        "copy_cmd": "claude attach b1e4d7a2",
        "agent_name": "export leaderboard report",
        "cwd": "~/Projects/example-bench",
        "state": "working",
        "started_at": e - 40,
        "summary": h["summary"],
        "plan": h["plan"],
        "dod": h["dod"],
        "log": f"{HOME}/state/logs/{HERO_ID}.log",
        "dispatched_at": e - 45,
        "delivery_mode": "repo",
        "last_error": None,
    }


def _hero_steer(now: dt.datetime) -> dict:
    """scene=steer——P-101（R-101）executing 途中 owner 在卡上留了两条捎话（steer）：
    第一条已经过 §44.3 送达点注入会话（delivered，带 ISO delivered_at），
    第二条还在等安全窗口（queued，delivered_at 必须为 null）——投递状态诚实
    可见，绝不假装已送达。ts 是 ISO 字符串（带时间戳的 dedup key：同文重申
    是新指令，web/src/steer.ts 只认 string ts）。"""
    card = _hero_running(now)
    e = _epoch(now)
    card["started_at"] = e - 900
    card["dispatched_at"] = e - 960
    card["steers"] = [
        {
            "text": "导出格式优先 markdown，png 可以放到后续 PR",
            "ts": _iso(now - dt.timedelta(seconds=600)),
            "status": "delivered",
            "delivered_at": _iso(now - dt.timedelta(seconds=540)),
        },
        {
            "text": "报告文件名里带上日期，方便归档",
            "ts": _iso(now - dt.timedelta(seconds=60)),
            "status": "queued",
            "delivered_at": None,
        },
    ]
    return card


def _hero_review(now: dt.datetime) -> dict:
    h = _hero_plan_dod(now)
    e = _epoch(now)
    return {
        "id": HERO_ID,
        "name": _hero_card(now)["title"],
        "summary": h["summary"],
        "dod": h["dod"],
        "session_id": "b1e4d7a2-5c38-4f9e-8d21-6a0b3c9e7f45",
        "short_id": "b1e4d7a2",
        "copy_cmd": "cd '~/Projects/example-bench' && claude --resume "
                    "b1e4d7a2-5c38-4f9e-8d21-6a0b3c9e7f45",
        "agent_name": "export leaderboard report",
        "state": "review",
        "cwd": "~/Projects/example-bench",
        "delivered_summary": "已开 draft PR example-bench#42：dashboard 加「导出报告」按钮，"
                             "后端渲染 markdown + png，CI 全绿。",
        "final_draft": None,
        "plan": h["plan"],
        "sources": h["sources"],
        "log": f"{HOME}/state/logs/{HERO_ID}.log",
        "dispatched_at": e - 1560,
        "review_at": e - 30,
        "delivery_mode": "repo",
    }


def _hero_done(now: dt.datetime) -> dict:
    h = _hero_plan_dod(now)
    e = _epoch(now)
    return {
        "id": HERO_ID,
        "name": _hero_card(now)["title"],
        "session_id": "b1e4d7a2-5c38-4f9e-8d21-6a0b3c9e7f45",
        "short_id": "b1e4d7a2",
        "copy_cmd": "cd '~/Projects/example-bench' && claude --resume "
                    "b1e4d7a2-5c38-4f9e-8d21-6a0b3c9e7f45",
        "agent_name": "export leaderboard report",
        "state": "delivered",
        "cwd": "~/Projects/example-bench",
        "summary": h["summary"],
        "delivered_summary": "已开 draft PR example-bench#42：dashboard 加「导出报告」按钮，"
                             "后端渲染 markdown + png，CI 全绿。",
        "accepted_at": e - 10,
        "dod": h["dod"],
    }


# --------------------------------------------------------------------------- #
# English vocabulary (issue #18) — one row per Chinese string in the dataset.
# Keys are the EXACT Chinese values above; _localize swaps them in place after
# the dashboard is built, so shape/ids/timestamps are identical in both
# languages. Keep rows sorted by first appearance; the all-English test
# (tests/test_demo_seed.py) fails on any dataset string missing here.
# --------------------------------------------------------------------------- #
LANGS = ("zh", "en")

_EN: dict = {
    # hero card P-101
    "example-bench: leaderboard 一键导出评测报告":
        "example-bench: one-click leaderboard report export",
    "给评测面板加一个「导出报告」按钮，批准后 AI 会在 example-bench 开 draft PR，不动主分支。":
        "Add an “Export report” button to the eval dashboard; once approved the AI "
        "opens a draft PR on example-bench and never touches main.",
    "一键可批": "One-click approval",
    "在 example-bench 的 dashboard 页加「导出报告」按钮":
        "Add an “Export report” button to the example-bench dashboard page",
    "后端把当前 leaderboard 渲染成 markdown + png":
        "Backend renders the current leaderboard as markdown + png",
    "产出 draft PR，不 merge": "Open a draft PR, do not merge",
    "dashboard 页出现「导出报告」按钮":
        "The dashboard page shows an “Export report” button",
    "点击后生成 markdown + png 到 exports/":
        "Clicking it writes markdown + png into exports/",
    "draft PR 通过 CI": "The draft PR passes CI",
    "能不能加个按钮，一键把 leaderboard 导出成报告发出去":
        "Can we add a button that exports the leaderboard as a report in one click?",
    "上周说的导出报告那个还做吗？周会又有人问了":
        "Are we still doing the report export from last week? It came up in the weekly again.",
    # P-102 inkweld demo
    "inkweld: 搭对外可访问的 demo 环境 + 种子数据":
        "inkweld: public demo environment + seed data",
    "新建 inkweld-demo 仓库，部署一个带种子数据的公开 demo 站。对外可见，所以需要你文字确认。":
        "Create the inkweld-demo repo and deploy a public demo site with seed data. "
        "It is publicly visible, so this needs your typed confirmation.",
    "需文字确认": "Typed confirmation required",
    "搭 inkweld 公开 demo 站": "Stand up the public inkweld demo site",
    "demo 用真实数据还是合成数据，manager 和 alex.doe 意见不一致":
        "manager and alex.doe disagree on whether the demo should use real or synthetic data",
    "新建 inkweld-demo 仓库（从 inkweld 模板裁剪）":
        "Create the inkweld-demo repo (trimmed from the inkweld template)",
    "写种子数据生成脚本（全部合成数据）":
        "Write the seed-data generator (all synthetic)",
    "部署到内部 PaaS，配只读演示账号":
        "Deploy to the internal PaaS with a read-only demo account",
    "README 写清 demo 边界与重置方式":
        "README documents the demo's boundaries and how to reset it",
    "demo 站可公网访问，演示账号能登录":
        "The demo site is reachable from the public internet and the demo account can log in",
    "数据全部合成，无任何真实客户信息":
        "All data is synthetic — no real customer information",
    "一条命令可重置 demo 数据": "One command resets the demo data",
    "客户那边想先看个能点的 demo，不用完整功能":
        "The customer wants a clickable demo first; full features can wait",
    "demo 环境这事本周能定吗？销售又来问了":
        "Can we settle the demo environment this week? Sales is asking again.",
    # P-103 Q3 one-pager
    "起草 Q3 planning 的 one-pager（中英双语）":
        "Draft the Q3 planning one-pager (bilingual)",
    "写作任务：Q3 planning 一页纸初稿，会话内交付成稿，不动任何仓库。":
        "Writing task: first draft of the Q3 planning one-pager, delivered in-session; "
        "no repository is touched.",
    "外部来源提级，需文字确认":
        "Escalated (external source) — typed confirmation required",
    "梳理 Q2 结果与 Q3 候选方向": "Review Q2 outcomes and candidate Q3 directions",
    "按 objective / key results / risks 出提纲":
        "Outline by objective / key results / risks",
    "中英双语成稿，放在结束总结里等你定稿":
        "Bilingual draft placed in the closing summary for your sign-off",
    "提纲覆盖 3 个 objective": "The outline covers 3 objectives",
    "中英双语": "Bilingual",
    "一页以内": "One page or less",
    "Q3 planning 下周三要一版 one-pager，先给个提纲也行":
        "I need a Q3 planning one-pager by next Wednesday — an outline is fine to start.",
    # P-104 placeholder
    "统一 example-bench 和 inkweld 的 lint 配置":
        "Unify the lint configuration across example-bench and inkweld",
    "AI 研究中": "AI researching",
    # running lane
    "example-bench: 修 flaky 的 e2e 测试（retry 逻辑）":
        "example-bench: fix the flaky e2e tests (retry logic)",
    "e2e 套件里 3 个用例偶发超时，加统一的 retry + 诊断日志。":
        "Three cases in the e2e suite time out intermittently; add a shared retry + diagnostic logging.",
    "复现并定位 3 个 flaky 用例的超时点":
        "Reproduce and locate the timeout in the 3 flaky cases",
    "抽一个带指数退避的 retry helper": "Extract a retry helper with exponential backoff",
    "连续跑 40 轮验证零失败": "Run 40 consecutive rounds to verify zero failures",
    "连续 40 轮 e2e 零失败": "40 consecutive green e2e runs",
    "retry helper 有单测": "The retry helper has unit tests",
    "inkweld: README 快速上手一节重写":
        "inkweld: rewrite the README quick-start section",
    "现在的快速上手照着跑会卡在第二步，按新安装脚本重写。":
        "Following the current quick start gets stuck at step two; rewrite it for the new install script.",
    "按 install.sh 现状重走一遍安装流程":
        "Walk through the install flow with the current install.sh",
    "重写 README 快速上手一节": "Rewrite the README quick-start section",
    "新人照 README 十分钟内跑起来":
        "A newcomer is up and running within ten minutes by following the README",
    "example-bench: 数据集 v2 的 loader 兼容层":
        "example-bench: dataset v2 loader compatibility shim",
    "数据集 v2 换了 schema，加兼容层让老评测脚本不用改。":
        "Dataset v2 changed its schema; add a shim so the old eval scripts run unchanged.",
    "对比 v1/v2 schema 差异": "Compare the v1/v2 schema differences",
    "写字段映射兼容层": "Write the field-mapping compatibility shim",
    "老脚本回归全过": "All legacy-script regressions pass",
    "v1 脚本零改动跑通 v2 数据集":
        "v1 scripts run on the v2 dataset with zero changes",
    "给 inkweld 接 Supabase auth（需要 service key）":
        "Wire Supabase auth into inkweld (service key needed)",
    # review lane
    "example-bench: 评测缓存层（重复 run 提速 10x）":
        "example-bench: eval cache layer (repeat runs 10x faster)",
    "给 runner 加 content-hash 缓存，重复评测直接读缓存。":
        "Add a content-hash cache to the runner so repeat evaluations read from cache.",
    # §64 AI 摘要 + 评语（prose only — the three verdict tokens are server wire vocabulary,
    # rendered per language by web VerdictChip, so they are deliberately NOT in this table）
    "给评测加了缓存，重跑快十倍，等你看 PR":
        "Added an eval cache; repeat runs are ~10x faster — the PR is ready for you",
    "清单三条都有对应交付：缓存命中、失效单测 6 个、CI 全绿":
        "All three checklist items delivered: cache hits, 6 invalidation unit tests, CI all green",
    "周报草稿写好了，缺一页内的篇幅确认":
        "Weekly report draft is written; needs your call on the one-page limit",
    "清单第 3 条「不超过一页」要你按目标读者定：现在双语版约一页半":
        "Checklist item 3 “no longer than one page” is your call given the audience: the bilingual version runs ~1.5 pages",
    "已在 example-bench 开 draft PR #87：runner 加 content-hash 缓存层，重复 run 从 ~12min 降到 ~70s；失效逻辑带 6 个单测，CI 全绿。":
        "Draft PR #87 opened on example-bench: content-hash cache layer in the runner, repeat runs "
        "down from ~12 min to ~70 s; invalidation logic has 6 unit tests, CI all green.",
    "同 config 重复 run 命中缓存": "A repeat run with the same config hits the cache",
    "缓存失效逻辑有单测": "Cache invalidation has unit tests",
    "CI 全绿": "CI all green",
    "设计 config 的 content-hash 规则": "Design the content-hash rule for configs",
    "runner 读写缓存": "Runner reads and writes the cache",
    "补失效逻辑单测": "Add unit tests for invalidation",
    "每次改一行 config 都要全量重跑，太费时间了":
        "Changing one line of config forces a full rerun every time — it takes forever.",
    "写本周 weekly report（发出去前先过目）":
        "Write this week's weekly report (review before sending)",
    "会话内交付：本周周报初稿，验收后自己粘贴发送。":
        "In-session delivery: first draft of this week's report; paste and send after accepting.",
    "周报初稿完成：两个 project 各一段进展 + 下周计划，中英双语。":
        "Weekly report draft done: a progress paragraph per project + next week's plan, bilingual.",
    "覆盖本周两个 project 的进展": "Covers this week's progress on both projects",
    "不超过一页": "No more than one page",
    "汇总本周两个 project 的进展": "Collect this week's progress across both projects",
    "中英双语成稿": "Bilingual draft",
    "放进结束总结等验收": "Place it in the closing summary for acceptance",
    "这周的 weekly 别忘了，最好周五中午前":
        "Don't forget this week's weekly — ideally before Friday noon.",
    "**Weekly Report | 周报**\n"
    "\n"
    "## example-bench\n"
    "- 评测缓存层 draft PR 已提交，重复 run 提速 ~10x（12min → 70s）。\n"
    "  Eval cache layer in draft PR; repeat runs ~10x faster.\n"
    "- e2e flaky 修复中：retry 逻辑已连续 40 轮零失败。\n"
    "  Flaky e2e fix in progress; 40 consecutive green runs with retries.\n"
    "\n"
    "## inkweld\n"
    "- 对外 demo 环境方案已成型，等 green light 后开工。\n"
    "  Public demo environment proposal ready, pending sign-off.\n"
    "- README 快速上手重写已排队。\n"
    "  Quick-start rewrite queued.\n"
    "\n"
    "## Next week\n"
    "- leaderboard 一键导出评测报告（PR review 中）\n"
    "- Q3 planning one-pager 初稿\n":
        "**Weekly Report**\n"
        "\n"
        "## example-bench\n"
        "- Eval cache layer in draft PR; repeat runs ~10x faster (12 min → 70 s).\n"
        "- Flaky e2e fix in progress; 40 consecutive green runs with retries.\n"
        "\n"
        "## inkweld\n"
        "- Public demo environment proposal ready, pending sign-off.\n"
        "- Quick-start rewrite queued.\n"
        "\n"
        "## Next week\n"
        "- One-click leaderboard report export (in PR review)\n"
        "- Q3 planning one-pager, first draft\n",
    # completed lane
    "example-bench: CI 加 lint gate（ruff + prettier）":
        "example-bench: add a lint gate to CI (ruff + prettier)",
    "PR 必须过 ruff + prettier 才能合并。":
        "PRs must pass ruff + prettier before they can merge.",
    "draft PR #61 验收通过：lint gate 上线，历史违规一次性清零。":
        "Draft PR #61 accepted: lint gate live, historical violations cleared in one pass.",
    "把周会 action items 自动整理成清单":
        "Turn weekly-meeting action items into a checklist automatically",
    "每次周会纪要落盘后自动产出 action-item 清单。":
        "Produce an action-item checklist automatically whenever the weekly notes land.",
    "脚本已交付：会议纪要落盘后 5 分钟内生成清单并通知。":
        "Script delivered: the checklist is generated and announced within 5 minutes of the notes landing.",
    "PR 未过 lint 无法合并": "A PR that fails lint cannot merge",
    "本地一条命令自动修复": "One local command auto-fixes violations",
    "每条 action item 带 owner 和 deadline": "Every action item has an owner and a deadline",
    # debt lane
    "example-bench 的 README 安装一节过时了":
        "example-bench's README install section is out of date",
    "setup 命令已经跑不通，新人第一步就卡住。":
        "The setup command no longer works; newcomers get stuck on step one.",
    "周会纪要没人整理，action items 常丢":
        "Nobody tidies the weekly notes; action items keep getting lost",
    "口头说好的事没人记，下周就忘。":
        "Verbal agreements go unrecorded and are forgotten by next week.",
    "周会 action items 自动整理": "Auto-tidy the weekly action items",
    "inkweld 报错日志太吵，真错误被淹没":
        "inkweld's error log is too noisy; real errors drown",
    "warning 刷屏，出真错时没人看得见。":
        "Warnings scroll by nonstop; when a real error hits nobody sees it.",
    "README 里那个 setup 命令已经跑不通了吧？":
        "That setup command in the README doesn't work anymore, does it?",
    "上周说好的两件事这周都没人记得":
        "Nobody remembers the two things we agreed on last week",
    "日志一分钟滚几百行 warning，真出事根本发现不了":
        "The log scrolls hundreds of warning lines a minute — a real failure would never be noticed",
    # trash lane
    "给 slack 加自动回复 bot": "Add an auto-reply bot to Slack",
    "检测到的建议，被拒绝进回收站——不想要自动回复。":
        "A detected suggestion, rejected into the recycle bin — no auto-replies wanted.",
    # hero scenes
    "leaderboard 一键导出评测报告": "One-click leaderboard report export",
    "导出格式优先 markdown，png 可以放到后续 PR":
        "Prefer markdown for the export; png can follow in a later PR",
    "报告文件名里带上日期，方便归档":
        "Put the date in the report filename for easier archiving",
    "已开 draft PR example-bench#42：dashboard 加「导出报告」按钮，后端渲染 markdown + png，CI 全绿。":
        "Draft PR example-bench#42 opened: “Export report” button on the dashboard, "
        "backend renders markdown + png, CI all green.",
}


def _localize(obj, lang: str):
    """Apply the ``_EN`` vocabulary to every string value (recursively) when
    ``lang == "en"``; any other lang returns ``obj`` untouched. Strings absent
    from the table pass through — the all-English test is what catches them."""
    if lang != "en":
        return obj
    if isinstance(obj, dict):
        return {k: _localize(v, lang) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_localize(v, lang) for v in obj]
    if isinstance(obj, str):
        return _EN.get(obj, obj)
    return obj


def build(scene: str, now: dt.datetime | None = None, lang: str = "zh") -> dict:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    lanes = {
        "needs_approval": _needs_approval(now),
        "running": _running(now),
        "needs_input": _needs_input(now),
        "review": _review(now),
        "completed": _completed(now),
        "debt": _debt(now),
        "trash": _trash(now),
    }
    _place_hero(lanes, scene, now)
    _stamp_lane_ids(lanes)
    dash = {
        "generated_at": _iso(now),
        "counts": {name: len(rows) for name, rows in lanes.items()},
        **lanes,
    }
    return _localize(dash, lang)


# scene → (lane the hero card is prepended to, its builder); "initial" keeps
# the hero where _needs_approval seeds it.
_HERO_SCENES = {
    "captured": ("needs_approval", _hero_captured),
    "approved": ("running", _hero_queued),
    "running": ("running", _hero_running),
    "steer": ("running", _hero_steer),
    "review": ("review", _hero_review),
    "done": ("completed", _hero_done),
}


def _place_hero(lanes: dict, scene: str, now: dt.datetime) -> None:
    """Move the hero card to the lane the requested pipeline moment shows."""
    if scene == "initial":
        return
    lanes["needs_approval"] = [c for c in lanes["needs_approval"] if c["id"] != HERO_ID]
    placement = _HERO_SCENES.get(scene)
    if placement is not None:
        lane, hero = placement
        lanes[lane] = [hero(now)] + lanes[lane]


# 批准过的 lane：每行带工作编号（§60）；提案/备选/回收站只有 P- 主键
_WORK_LANES = ("running", "needs_input", "review", "completed")


def _stamp_lane_ids(lanes: dict) -> None:
    """§60 投影面：批准过的 lane（running/needs_input/review/completed）每行带工作
    编号 R-<n>（demo 里取主键同号，P-105 → R-105，方便肉眼对账）；提案/备选/
    回收站只有 P- 主键。display_id / id_kind 与 act/lib/dashboard._title_fields 同式。"""
    for name, rows in lanes.items():
        for row in rows:
            if name in _WORK_LANES:
                row.setdefault("work_id", "R-" + row["id"][2:])
            _stamp_ids(row)


def _stamp_ids(row: dict) -> None:
    """§60（D21）：display_id 恒在（= work_id or id）；id_kind ∈ work | proposal
    （demo 无 legacy 卡——存量 R- 主键只出现在真实迁移安装上）。"""
    row.setdefault("display_id", row.get("work_id") or row["id"])
    row.setdefault("id_kind", "work" if row.get("work_id") else "proposal")


# --------------------------------------------------------------------------- #
# validation — mirrors the Swift decoders' hard requirements
# --------------------------------------------------------------------------- #
SECTIONS = ("needs_approval", "running", "needs_input", "review",
            "completed", "debt", "trash")


def _check_sources(problems: list, where: str, sources) -> None:
    if not isinstance(sources, list):
        problems.append(f"{where}: sources is not a list")
        return
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            problems.append(f"{where}: sources[{i}] not a dict")
            continue
        for k in ("who", "channel", "date", "quote"):
            if not isinstance(s.get(k), str):
                problems.append(
                    f"{where}: sources[{i}].{k} must be a string "
                    f"(got {type(s.get(k)).__name__}) — a null here makes "
                    f"Swift drop the WHOLE sources array / debt row")


def _check_epoch(problems: list, where: str, item: dict, *keys: str) -> None:
    for k in keys:
        if k in item and item[k] is not None and not isinstance(item[k], int):
            problems.append(f"{where}.{k}: epoch fields must be int, "
                            f"got {type(item[k]).__name__}")


def _check_optional_str(problems: list, where: str, item: dict, k: str) -> None:
    if k in item and item[k] is not None and not isinstance(item[k], str):
        problems.append(f"{where}.{k}: must be str when present")


def _check_ids(problems: list, where: str, item: dict) -> None:
    """§60（add-only optional，老快照可缺席）：work_id/display_id/id_kind 出现时
    必为 str；work_id 有则 display_id 必等于它（server 公式 display_id = work_id
    or id）；id_kind 词表 work | legacy | proposal。"""
    for k in ("work_id", "display_id", "id_kind"):
        _check_optional_str(problems, where, item, k)
    wid = item.get("work_id")
    if wid and item.get("display_id", wid) != wid:
        problems.append(f"{where}.display_id must equal work_id when set")
    if item.get("id_kind") not in (None, "work", "legacy", "proposal"):
        problems.append(f"{where}.id_kind: unknown value {item['id_kind']!r}")


def _check_str(problems: list, where: str, item: dict, *keys: str) -> None:
    for k in keys:
        if not isinstance(item.get(k), str) or not item[k]:
            problems.append(f"{where}.{k}: required non-empty string")


def _check_origin_trust(problems: list, where: str, item: dict) -> None:
    # 可选字段（老 dashboard 没有）；一旦出现必须落在枚举内
    v = item.get("origin_trust")
    if v is not None and v not in ORIGIN_TRUST:
        problems.append(f"{where}.origin_trust: must be one of {ORIGIN_TRUST}")


def _check_effective_tier(problems: list, where: str, item: dict) -> None:
    et = item.get("effective_tier")
    if et is None:
        return
    if et not in TIER_RANK:
        problems.append(f"{where}.effective_tier: must be one of "
                        f"{tuple(TIER_RANK)}")
        return
    tier = item.get("tier")
    if tier in TIER_RANK and TIER_RANK[et] < TIER_RANK[tier]:
        problems.append(f"{where}.effective_tier={et} below tier={tier} — "
                        f"W17 escalation is one-way (提级不降级)")


def _check_queued_reason(problems: list, where: str, item: dict) -> None:
    qr = item.get("queued_reason")
    if qr is None:
        return
    if item.get("state") != "queued":
        problems.append(f"{where}.queued_reason: only queued items may carry it")
    _check_queued_reason_value(problems, where, qr)


def _check_queued_reason_value(problems: list, where: str, qr) -> None:
    if isinstance(qr, str):
        # 过渡期纯字符串形（web/src/steer.ts 双兼容）——非空即可
        if not qr.strip():
            problems.append(f"{where}.queued_reason: string form must be "
                            f"non-empty")
        return
    if not isinstance(qr, dict):
        problems.append(f"{where}.queued_reason: must be an object "
                        f"{{kind, detail?, blocking_id?}} or a string")
        return
    _check_queued_reason_object(problems, where, qr)


def _check_queued_reason_object(problems: list, where: str, qr: dict) -> None:
    if qr.get("kind") not in QUEUED_REASON_KINDS:
        problems.append(f"{where}.queued_reason.kind: must be one of "
                        f"{QUEUED_REASON_KINDS} (closed list — UI 由 kind 渲染)")
    for k in ("detail", "blocking_id"):
        if qr.get(k) is not None and not isinstance(qr[k], str):
            problems.append(f"{where}.queued_reason.{k}: string or null")
    _check_waiting_card(problems, where, qr)


def _check_waiting_card(problems: list, where: str, qr: dict) -> None:
    if qr.get("kind") == "waiting_card" and not (
            isinstance(qr.get("blocking_id"), str) and qr["blocking_id"]):
        problems.append(f"{where}.queued_reason.blocking_id: waiting_card "
                        f"must carry the blocking card id")


def _check_steers(problems: list, where: str, item: dict) -> None:
    notes = item.get("steers")
    if notes is None:
        return
    if not isinstance(notes, list):
        problems.append(f"{where}.steers: must be a list")
        return
    for j, n in enumerate(notes):
        nw = f"{where}.steers[{j}]"
        if not isinstance(n, dict):
            problems.append(f"{nw}: not a dict")
            continue
        _check_steer_note(problems, nw, n)


def _check_steer_note(problems: list, nw: str, n: dict) -> None:
    if not isinstance(n.get("text"), str) or not n["text"]:
        problems.append(f"{nw}.text: required non-empty string")
    if not isinstance(n.get("ts"), str) or not n["ts"]:
        problems.append(f"{nw}.ts: required ISO string — the "
                        f"timestamp-bearing dedup key (verbatim repeats "
                        f"are legitimate steers; web parser drops rows "
                        f"without a string ts)")
    _check_steer_status(problems, nw, n)


def _check_steer_status(problems: list, nw: str, n: dict) -> None:
    status = n.get("status")
    if status not in STEER_STATUSES:
        problems.append(f"{nw}.status: must be one of {STEER_STATUSES}")
        return
    _check_delivered_at(problems, nw, n, status)


def _check_delivered_at(problems: list, nw: str, n: dict, status: str) -> None:
    """delivered notes carry an ISO string; every other status keeps it null."""
    if status == "delivered":
        if not isinstance(n.get("delivered_at"), str) or not n["delivered_at"]:
            problems.append(f"{nw}.delivered_at: delivered notes must "
                            f"carry an ISO string (honest status)")
    elif n.get("delivered_at") is not None:
        problems.append(f"{nw}.delivered_at: must be null unless "
                        f"status=delivered (honest status)")


def validate(dash: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(dash, dict):
        return ["top level is not a JSON object"]
    if not isinstance(dash.get("generated_at"), str):
        problems.append("generated_at: required ISO string")
    _check_counts(problems, dash)
    for sec, check in _SECTION_CHECKS.items():
        for i, item in enumerate(_rows(dash, sec)):
            check(problems, f"{sec}[{i}]", item)
    return problems


def _rows(dash: dict, sec: str) -> list:
    return dash.get(sec) or []


def _check_debt(problems: list, w: str, d: dict) -> None:
    _check_str(problems, w, d, "id", "title")
    _check_sources(problems, w, d.get("sources") or [])


def _check_counts(problems: list, dash: dict) -> None:
    counts = dash.get("counts")
    if not isinstance(counts, dict):
        problems.append("counts: required object")
        counts = {}
    for sec in SECTIONS:
        items = dash.get(sec)
        if not isinstance(items, list):
            problems.append(f"{sec}: required list")
            continue
        if counts.get(sec) != len(items):
            problems.append(f"counts.{sec}={counts.get(sec)} but "
                            f"len({sec})={len(items)}")


def _check_proposal_fields(problems: list, w: str, c: dict) -> None:
    _check_str(problems, w, c, "id", "title", "tier")
    for k, t in (("show_cost", bool), ("processing", bool)):
        if not isinstance(c.get(k), t):
            problems.append(f"{w}.{k}: required {t.__name__}")
    for k in ("sources", "plan", "dod"):
        if not isinstance(c.get(k), list):
            problems.append(f"{w}.{k}: required list")


def _check_proposal(problems: list, w: str, c: dict) -> None:
    _check_proposal_fields(problems, w, c)
    _check_sources(problems, w, c.get("sources") or [])
    _check_origin_trust(problems, w, c)
    _check_effective_tier(problems, w, c)
    _check_ids(problems, w, c)
    if c.get("cost_usd") is not None and not isinstance(c["cost_usd"], (int, float)):
        problems.append(f"{w}.cost_usd: number or null")


def _check_task(problems: list, w: str, t: dict) -> None:
    _check_str(problems, w, t, "id", "name", "state")
    _check_epoch(problems, w, t, "started_at", "dispatched_at", "accepted_at")
    _check_origin_trust(problems, w, t)
    _check_queued_reason(problems, w, t)
    _check_steers(problems, w, t)
    _check_ids(problems, w, t)
    if t.get("state") == "queued":
        _check_queued_task(problems, w, t)
    else:
        _check_str(problems, w, t, "session_id")


def _check_queued_task(problems: list, w: str, t: dict) -> None:
    for k in ("session_id", "copy_cmd", "short_id"):
        if k in t:
            problems.append(f"{w}: queued items must not carry {k} "
                            f"(dashboard.py omits it — no session yet)")
    if "dispatch_error" not in t:
        problems.append(f"{w}: queued items carry dispatch_error "
                        f"(null while pending)")


def _check_review(problems: list, w: str, r: dict) -> None:
    _check_str(problems, w, r, "id", "name")
    if not isinstance(r.get("dod"), list):
        problems.append(f"{w}.dod: required list")
    _check_sources(problems, w, r.get("sources") or [])
    _check_origin_trust(problems, w, r)
    _check_ids(problems, w, r)
    _check_epoch(problems, w, r, "dispatched_at", "review_at")
    _check_review_delivery(problems, w, r)


def _check_review_delivery(problems: list, w: str, r: dict) -> None:
    if r.get("delivery_mode") not in ("chat", "repo"):
        problems.append(f"{w}.delivery_mode: must be 'chat' or 'repo'")
    if r.get("final_draft") is not None and not isinstance(r["final_draft"], str):
        problems.append(f"{w}.final_draft: string or null")


def _check_trash(problems: list, w: str, t: dict) -> None:
    _check_str(problems, w, t, "id", "title")
    if not isinstance(t.get("permanent"), bool):
        problems.append(f"{w}.permanent: required bool")
    if not isinstance(t.get("trashed_at"), str):
        problems.append(f"{w}.trashed_at: required ISO string")


# validate() walks the sections in this order (the problem list is ordered)
_SECTION_CHECKS = {
    "needs_approval": _check_proposal,
    "running": _check_task,
    "needs_input": _check_task,
    "completed": _check_task,
    "review": _check_review,
    "debt": _check_debt,
    "trash": _check_trash,
}


def _summary_line(dash: dict) -> str:
    return " ".join(f"{sec}={len(dash.get(sec) or [])}" for sec in SECTIONS)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Seed <target-dir>/state/dashboard.json with fictional demo data.")
    ap.add_argument("target", help="demo AIASSISTANT_HOME directory "
                                   "(or, with --check, a dashboard.json path)")
    ap.add_argument("--scene", choices=SCENES, default="initial",
                    help="pipeline moment for the demo video (default: initial)")
    ap.add_argument("--lang", choices=LANGS, default="zh",
                    help="language of the fictional card contents (default: zh)")
    ap.add_argument("--english", action="store_const", const="en", dest="lang",
                    help="shorthand for --lang en (all-English dashboard)")
    ap.add_argument("--check", action="store_true",
                    help="only validate an existing dashboard.json, write nothing")
    args = ap.parse_args(argv)

    target = Path(args.target).expanduser()
    path = target if target.suffix == ".json" else target / "state" / "dashboard.json"
    if args.check:
        dash = _read_existing(path)
    else:
        dash = _seed(path, args.scene, args.lang)
    if dash is None:
        return 1
    return _report(dash)


def _report(dash: dict) -> int:
    problems = validate(dash)
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print(f"OK: {_summary_line(dash)}")
    return 0


def _read_existing(path: Path):
    """--check: the dashboard on disk, or None (missing / unreadable, reported)."""
    if not path.exists():
        print(f"MISSING: {path}", file=sys.stderr)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"UNREADABLE: {path}: {exc}", file=sys.stderr)
        return None


def _seed(path: Path, scene: str, lang: str) -> dict:
    """Write the demo dashboard atomically (.tmp then rename, same as
    act/lib/dashboard.py) and return what actually landed on disk."""
    dash = build(scene, lang=lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dash, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    dash = json.loads(path.read_text(encoding="utf-8"))
    print(f"wrote {path} (scene={scene}, lang={lang})")
    return dash


if __name__ == "__main__":
    raise SystemExit(main())
