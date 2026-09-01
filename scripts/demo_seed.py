#!/usr/bin/env python3
"""Demo-data seeder — writes an ENTIRELY FICTIONAL state/dashboard.json.

Purpose: launch the Mac menu-bar app against a fake AIASSISTANT_HOME for
README screenshots / demo videos, with every card type and edge state
visible. See docs/DEMO.md for the full recording workflow.

    python3 scripts/demo_seed.py /tmp/assistant-demo
    python3 scripts/demo_seed.py /tmp/assistant-demo --scene running
    python3 scripts/demo_seed.py /tmp/assistant-demo --check

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

HERO_ID = "R-101"  # the card the --scene flag walks through the pipeline

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
    """R-101 as a needs_approval card (scene=initial)."""
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
            "id": "R-102",
            "title": "inkweld: 搭对外可访问的 demo 环境 + 种子数据",
            "summary": "新建 inkweld-demo 仓库，部署一个带种子数据的公开 demo 站。对外可见，所以需要你文字确认。",
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
            "id": "R-103",
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
            "id": "R-104",
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
            "id": "R-105",
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
            "id": "R-106",
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
            "id": "R-107",
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
            "id": "R-108",
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
            "id": "R-109",
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
        },
        {
            "id": "R-110",
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
            "id": "R-111",
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
            "id": "R-112",
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
            "id": "R-113",
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
            "id": "R-114",
            "title": "周会纪要没人整理，action items 常丢",
            "summary": "口头说好的事没人记，下周就忘。",
            "hardness": "hard",
            "type": "process",
            "sources": [
                _src("manager", "meeting", _date(now, -12),
                     "上周说好的两件事这周都没人记得"),
            ],
        },
        {
            "id": "R-115",
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
            "id": "R-116",
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
# scenes — walk the hero card (R-101) through the pipeline
# --------------------------------------------------------------------------- #
def _epoch(now: dt.datetime) -> int:
    return int(now.timestamp())


def _hero_captured(now: dt.datetime) -> dict:
    """R-101 as a raising placeholder — the moment right after a meeting
    recording was ingested and radar picked the requirement up (scene=captured).
    Same shape dashboard.py emits for status=raising (cf. R-104)."""
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
        # 同仓 R-105 还在跑 → 排队等它；UI chip「排队中 · 等 R-105」
        # （meeting-born 卡无 origin_trust 字段——中间档，人批后照常排队派发）
        "queued_reason": {"kind": "waiting_card", "blocking_id": "R-105"},
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
    """scene=steer——R-101 executing 途中 owner 在卡上留了两条捎话（steer）：
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


def build(scene: str, now: dt.datetime | None = None) -> dict:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    needs_approval = _needs_approval(now)
    running = _running(now)
    needs_input = _needs_input(now)
    review = _review(now)
    completed = _completed(now)

    if scene != "initial":
        needs_approval = [c for c in needs_approval if c["id"] != HERO_ID]
    if scene == "captured":
        needs_approval = [_hero_captured(now)] + needs_approval
    elif scene == "approved":
        running = [_hero_queued(now)] + running
    elif scene == "running":
        running = [_hero_running(now)] + running
    elif scene == "steer":
        running = [_hero_steer(now)] + running
    elif scene == "review":
        review = [_hero_review(now)] + review
    elif scene == "done":
        completed = [_hero_done(now)] + completed

    debt = _debt(now)
    trash = _trash(now)
    return {
        "generated_at": _iso(now),
        "counts": {
            "needs_approval": len(needs_approval),
            "running": len(running),
            "needs_input": len(needs_input),
            "review": len(review),
            "completed": len(completed),
            "debt": len(debt),
            "trash": len(trash),
        },
        "needs_approval": needs_approval,
        "running": running,
        "needs_input": needs_input,
        "review": review,
        "completed": completed,
        "debt": debt,
        "trash": trash,
    }


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
    if qr.get("kind") not in QUEUED_REASON_KINDS:
        problems.append(f"{where}.queued_reason.kind: must be one of "
                        f"{QUEUED_REASON_KINDS} (closed list — UI 由 kind 渲染)")
    for k in ("detail", "blocking_id"):
        if qr.get(k) is not None and not isinstance(qr[k], str):
            problems.append(f"{where}.queued_reason.{k}: string or null")
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
        if not isinstance(n.get("text"), str) or not n["text"]:
            problems.append(f"{nw}.text: required non-empty string")
        if not isinstance(n.get("ts"), str) or not n["ts"]:
            problems.append(f"{nw}.ts: required ISO string — the "
                            f"timestamp-bearing dedup key (verbatim repeats "
                            f"are legitimate steers; web parser drops rows "
                            f"without a string ts)")
        status = n.get("status")
        if status not in STEER_STATUSES:
            problems.append(f"{nw}.status: must be one of {STEER_STATUSES}")
        elif status == "delivered":
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

    for i, c in enumerate(dash.get("needs_approval") or []):
        w = f"needs_approval[{i}]"
        _check_str(problems, w, c, "id", "title", "tier")
        for k, t in (("show_cost", bool), ("processing", bool)):
            if not isinstance(c.get(k), t):
                problems.append(f"{w}.{k}: required {t.__name__}")
        for k in ("sources", "plan", "dod"):
            if not isinstance(c.get(k), list):
                problems.append(f"{w}.{k}: required list")
        _check_sources(problems, w, c.get("sources") or [])
        _check_origin_trust(problems, w, c)
        _check_effective_tier(problems, w, c)
        if c.get("cost_usd") is not None and not isinstance(c["cost_usd"], (int, float)):
            problems.append(f"{w}.cost_usd: number or null")

    for sec in ("running", "needs_input", "completed"):
        for i, t in enumerate(dash.get(sec) or []):
            w = f"{sec}[{i}]"
            _check_str(problems, w, t, "id", "name", "state")
            _check_epoch(problems, w, t, "started_at", "dispatched_at", "accepted_at")
            _check_origin_trust(problems, w, t)
            _check_queued_reason(problems, w, t)
            _check_steers(problems, w, t)
            if t.get("state") == "queued":
                for k in ("session_id", "copy_cmd", "short_id"):
                    if k in t:
                        problems.append(f"{w}: queued items must not carry {k} "
                                        f"(dashboard.py omits it — no session yet)")
                if "dispatch_error" not in t:
                    problems.append(f"{w}: queued items carry dispatch_error "
                                    f"(null while pending)")
            else:
                _check_str(problems, w, t, "session_id")

    for i, r in enumerate(dash.get("review") or []):
        w = f"review[{i}]"
        _check_str(problems, w, r, "id", "name")
        if not isinstance(r.get("dod"), list):
            problems.append(f"{w}.dod: required list")
        _check_sources(problems, w, r.get("sources") or [])
        _check_origin_trust(problems, w, r)
        _check_epoch(problems, w, r, "dispatched_at", "review_at")
        if r.get("delivery_mode") not in ("chat", "repo"):
            problems.append(f"{w}.delivery_mode: must be 'chat' or 'repo'")
        if r.get("final_draft") is not None and not isinstance(r["final_draft"], str):
            problems.append(f"{w}.final_draft: string or null")

    for i, d in enumerate(dash.get("debt") or []):
        w = f"debt[{i}]"
        _check_str(problems, w, d, "id", "title")
        _check_sources(problems, w, d.get("sources") or [])

    for i, t in enumerate(dash.get("trash") or []):
        w = f"trash[{i}]"
        _check_str(problems, w, t, "id", "title")
        if not isinstance(t.get("permanent"), bool):
            problems.append(f"{w}.permanent: required bool")
        if not isinstance(t.get("trashed_at"), str):
            problems.append(f"{w}.trashed_at: required ISO string")

    return problems


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
    ap.add_argument("--check", action="store_true",
                    help="only validate an existing dashboard.json, write nothing")
    args = ap.parse_args(argv)

    target = Path(args.target).expanduser()
    path = target if target.suffix == ".json" else target / "state" / "dashboard.json"

    if args.check:
        if not path.exists():
            print(f"MISSING: {path}", file=sys.stderr)
            return 1
        try:
            dash = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"UNREADABLE: {path}: {exc}", file=sys.stderr)
            return 1
    else:
        dash = build(args.scene)
        path.parent.mkdir(parents=True, exist_ok=True)
        # atomic write, same as act/lib/dashboard.py (.tmp then rename)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(dash, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        # validate what actually landed on disk
        dash = json.loads(path.read_text(encoding="utf-8"))
        print(f"wrote {path} (scene={args.scene})")

    problems = validate(dash)
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print(f"OK: {_summary_line(dash)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
