#!/usr/bin/env python3
"""web/src/parity.test.tsx 用的 demo fixture（docs/CONTRACT.md §66.2）。

vitest 跑在 jsdom 里，拿不到 python 与 server，所以把 scripts/demo_seed.py 的
`initial` 场景（固定 now，确定性）连同 server 的几张目录落成 JSON：
  ui/parity/fixtures/demo-board.json   —— dashboard.json 形（+ archived[] 两行：demo_seed
                                          没有封存行，而看板右侧书立条要渲染它们；+ 词表行
                                          _vocab_rows：每列再加几行把卡面词表——状态词 / 难度 /
                                          类型 / 截止 / tier 提示 / 分歧 / 回锅 / 已并入 / 合并建议
                                          三态 / 需输入会话 / 时长 秒·天 / 曾用名 / 成本未知 T2 /
                                          §44.6 并入回执——都渲染出来，探针才判得到；探针只认渲染出的字）
  ui/parity/fixtures/lanes.json        —— GET /api/lanes 响应体（server.lanes.catalog()）
  ui/parity/fixtures/settings.json     —— GET /api/settings（空 home = 全默认；文案 server-owned）
  ui/parity/fixtures/secrets.json      —— GET /api/secrets（Anthropic / Gmail / 豆包语音 已保存、Slack 走旧路径、Ark 未设置）
全部虚构数据（demo_seed 的人名/仓库均为虚构）。tests/test_ui_parity_fixture.py 钉
「重跑零 diff」。

用法：
    python3 scripts/ui/parity_fixture.py --write | --check
"""

import argparse
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", ".."))
import demo_seed  # noqa: E402
import ui_common as uc  # noqa: E402
from server import lanes as server_lanes  # noqa: E402
from server import paths, secrets_store, settings_catalog  # noqa: E402

FIXTURES_DIR = os.path.join(uc.PARITY_DIR, "fixtures")
BOARD_PATH = os.path.join(FIXTURES_DIR, "demo-board.json")
LANES_PATH = os.path.join(FIXTURES_DIR, "lanes.json")
SETTINGS_PATH = os.path.join(FIXTURES_DIR, "settings.json")
SECRETS_PATH = os.path.join(FIXTURES_DIR, "secrets.json")
FIXED_NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _archived(now):
    """两行封存卡：一行 owner 点的永久完成、一行自动封存（ArchiveStrip 的两种落款）。"""
    return [
        {"id": "P-071", "title": "inkweld 周报模板定稿", "summary": "模板已交付并沿用三周",
         "kind": "suggestion", "archived_at": demo_seed._iso(now - dt.timedelta(days=9)),
         "archive_reason": "user", "prev_status": "delivered", "display_id": "R-071",
         "work_id": "R-071", "id_kind": "work"},
        {"id": "P-064", "title": "example-bench README 补 badge", "summary": "冷交付 30 天自动封存",
         "kind": "suggestion", "archived_at": demo_seed._iso(now - dt.timedelta(days=21)),
         "archive_reason": "auto", "prev_status": "delivered", "display_id": "R-064",
         "work_id": "R-064", "id_kind": "work"},
    ]


def _epoch(now, **delta):
    return int((now - dt.timedelta(**delta)).timestamp())


def _vocab_proposals(now):
    """提案列词表行：T0 自动执行 + 逾期 + 分歧 + 改进 / T2 需文字确认 + 今天截止 + 回锅新增 + 已并入 /
    未分级 + 成本未知 + 常规难度 + 修改意见合并中（processing rework）。"""
    src = [{"channel": "gmail", "date": "2026-08-30", "quote": "季度报告的图表能自动更新吗", "who": "sam.rivera"}]
    return [
        {"id": "P-131", "display_id": "P-131", "id_kind": "proposal", "title": "季度报告图表自动刷新",
         "summary": "把季度报告里的四张图接到数据源，生成时自动刷新。", "tier": "T0", "tier_hint": "自动执行",
         "hardness": "hard", "type": "code", "cost_usd": 3.5, "show_cost": True, "deadline": "2026-08-30",
         "days_left": -3, "delivery_mode": "repo", "target_kind": "existing", "target_name": "example-bench",
         "target_repo": "~/Projects/example-bench", "improvement_of": "R-111",
         "disagreement": "研究员认为图表来源应改为 warehouse 视图，而不是直接读生产库。",
         "plan": ["接数据源", "生成时刷新"], "dod": ["四张图数据与源一致"], "sources": src, "processing": False},
        {"id": "P-132", "display_id": "P-132", "id_kind": "proposal", "title": "给全员发一封迁移说明邮件",
         "summary": "以你的口吻起草迁移说明，发前你过一眼。", "tier": "T2", "tier_hint": "需文字确认",
         "hardness": "soft", "type": "comms", "cost_usd": 80, "show_cost": True, "deadline": "2026-09-02",
         "days_left": 0, "delivery_mode": "doc", "target_kind": "new", "target_name": "migration-notes",
         "silent_merged": 2, "reraised": True, "reraised_note": "又有两个人在群里问同一件事",
         "plan": ["起草", "你审阅"], "dod": ["邮件草稿在 outbox"], "sources": src, "processing": False},
        {"id": "P-133", "display_id": "P-133", "id_kind": "proposal", "title": "整理 onboarding 文书清单",
         "summary": "把入职文书按部门整理成一页。", "tier": None, "tier_hint": "未分级", "hardness": "soft",
         "type": "paperwork", "cost_usd": None, "show_cost": True, "deadline": "2026-09-12", "days_left": 10,
         "delivery_mode": "doc", "target_kind": "existing", "target_name": "your-workbench",
         "target_repo": "~/Projects/your-workbench", "plan": ["收集", "整理"], "dod": ["一页清单"],
         "sources": src, "processing": False},
        {"id": "P-134", "display_id": "P-134", "id_kind": "proposal", "title": "评审新的 API 设计稿",
         "summary": "按你的修改意见重提中。", "tier": "T1", "tier_hint": "一键可批", "hardness": "hard",
         "type": "review", "processing": True, "rework": True, "sources": src},
        # T2 且成本未知：typed-confirm 弹窗的「成本未知」行（P-132 那张给「预计费用：$80」）；
        # 另带 §37 活标题：display_title + former_titles → 详情抬头的「曾用名: 」
        {"id": "P-135", "display_id": "P-135", "id_kind": "proposal", "title": "把周报模板迁到新的文档系统",
         "display_title": "周报模板迁移", "former_titles": ["周报模板搬家", "迁移周报模板"],
         "summary": "把周报模板从旧 wiki 搬到新的文档系统，保留历史版本。", "tier": "T2", "tier_hint": "需文字确认",
         "hardness": "soft", "type": "paperwork", "cost_usd": None, "cost_state": "unknown", "show_cost": True,
         "delivery_mode": "doc", "target_kind": "existing", "target_name": "your-workbench",
         "target_repo": "~/Projects/your-workbench", "plan": ["导出", "导入", "校对"], "dod": ["新系统里能打开每一版"],
         "sources": src, "processing": False},
    ]


def _vocab_running(now):
    """运行中列词表行：已派发 / 排队中 / 空闲 / 状态未知 + 出错行（错误全文、派发失败）+ 已交付过·再运行。"""
    base = {"id_kind": "work", "cwd": "~/Projects/example-bench", "delivery_mode": "repo",
            "plan": ["步骤一"], "dod": ["验收点"], "summary": "词表行。"}
    rows = [
        dict(base, id="P-141", display_id="R-141", work_id="R-141", name="已派发但还没开工的卡", state="dispatched",
             dispatched_at=_epoch(now, minutes=3), agent_name="dispatched sample", session_id="f1f1f1f1-0000-4000-8000-000000000141",
             short_id="f1f1f1f1", copy_cmd="claude attach f1f1f1f1"),
        # 排队卡的派发失败句读的是 dispatch_error（dashboard.py：与 last_error 分开表达）
        dict(base, id="P-142", display_id="R-142", work_id="R-142", name="排队等派发的卡", state="queued",
             dispatched_at=None, last_error="dispatch failed: claude not found (Errno 2)",
             dispatch_error="claude not found (Errno 2)", dispatch_error_id="claude_cli_missing"),
        dict(base, id="P-143", display_id="R-143", work_id="R-143", name="会话空闲的卡", state="idle",
             dispatched_at=_epoch(now, hours=5, minutes=17), started_at=_epoch(now, hours=5, minutes=17), agent_name="idle sample",
             session_id="f3f3f3f3-0000-4000-8000-000000000143", short_id="f3f3f3f3", copy_cmd="claude attach f3f3f3f3"),
        dict(base, id="P-144", display_id="R-144", work_id="R-144", name="状态未知的卡（roster 漏了）", state="unknown",
             dispatched_at=_epoch(now, days=1, hours=2), started_at=_epoch(now, days=1, hours=2),
             session_id="f4f4f4f4-0000-4000-8000-000000000144", short_id="f4f4f4f4", copy_cmd="claude attach f4f4f4f4",
             last_error="Traceback (most recent call last): boom", from_review=True, accepted_at=_epoch(now, days=2)),
        # 其余状态词：受阻（会话等输入）/ 已完成（会话退出、待收割）/ 会话有新活动（legacy review-active 行）
        dict(base, id="P-145", display_id="R-145", work_id="R-145", name="会话等输入的卡", state="blocked",
             dispatched_at=_epoch(now, minutes=40), started_at=_epoch(now, minutes=40), agent_name="blocked sample",
             session_id="f5f5f5f5-0000-4000-8000-000000000145", short_id="f5f5f5f5", copy_cmd="claude attach f5f5f5f5"),
        dict(base, id="P-146", display_id="R-146", work_id="R-146", name="会话已退出待收割的卡", state="done",
             dispatched_at=_epoch(now, hours=2), started_at=_epoch(now, hours=2), agent_name="done sample",
             session_id="f6f6f6f6-0000-4000-8000-000000000146", short_id="f6f6f6f6", copy_cmd="claude attach f6f6f6f6"),
        dict(base, id="P-147", display_id="R-147", work_id="R-147", name="待验收会话又活跃起来的卡", state="review-active",
             dispatched_at=_epoch(now, hours=8), started_at=_epoch(now, hours=8), agent_name="review-active sample",
             session_id="f7f7f7f7-0000-4000-8000-000000000147", short_id="f7f7f7f7", copy_cmd="claude attach f7f7f7f7"),
    ]
    return rows


def _vocab_needs_input(now):
    """需输入（blocked）行：§4 派发刹车之外的另一形——会话卡住等回答且带会话指令；
    展开详情里「在终端接管会话：」（原生 detailBlock lane == .needsInput）。"""
    return [
        {"id": "P-148", "display_id": "R-148", "work_id": "R-148", "id_kind": "work", "name": "等你回答的会话",
         "state": "blocked", "cwd": "~/Projects/inkweld", "delivery_mode": "repo", "summary": "词表行：受阻会话。",
         "plan": ["步骤一"], "dod": ["验收点"], "dispatched_at": _epoch(now, minutes=25), "started_at": _epoch(now, minutes=25),
         "session_id": "f8f8f8f8-0000-4000-8000-000000000148", "short_id": "f8f8f8f8", "copy_cmd": "claude attach f8f8f8f8",
         "question": "要不要顺手把旧的 migration 也删掉？", "waiting_for": "你的回答", "resume_exhausted": True,
         "last_error": "session paused: waiting for user input"},
    ]


def _vocab_review(now):
    """待验收词表行：耗时 ≥ 1 天带小时位（{days}天{h}小时）+ 会话有新活动；另一行 45 秒（{secs}秒）。"""
    base = {"id_kind": "work", "cwd": "~/Projects/example-bench", "delivery_mode": "repo",
            "plan": ["步骤一"], "dod": ["验收点"], "summary": "词表行。", "sources": []}
    long_start = _epoch(now, days=2, hours=5)
    quick_start = _epoch(now, hours=3)
    return [
        dict(base, id="P-171", display_id="R-171", work_id="R-171", name="跑了一天多才交付的卡",
             dispatched_at=long_start, review_at=long_start + 27 * 3600, session_active=True,
             session_id="a1a1a1a1-0000-4000-8000-000000000171", short_id="a1a1a1a1", copy_cmd="claude attach a1a1a1a1",
             delivered_summary="改完了，PR 已开。"),
        dict(base, id="P-172", display_id="R-172", work_id="R-172", name="45 秒就交付的小卡",
             dispatched_at=quick_start, review_at=quick_start + 45, session_active=False,
             session_id="a2a2a2a2-0000-4000-8000-000000000172", short_id="a2a2a2a2", copy_cmd="claude attach a2a2a2a2",
             delivered_summary="一行 typo 修好了。"),
    ]


def _vocab_trash(now):
    return [
        {"id": "P-151", "display_id": "P-151", "id_kind": "proposal", "kind": "debt", "permanent": True,
         "title": "永久保留在回收站的潜在任务", "summary": "你删除的，且钉住永久保留。", "trash_reason": "deleted",
         "trashed_at": demo_seed._iso(now - dt.timedelta(days=2)), "type": "research", "hardness": "hard"},
        {"id": "P-152", "display_id": "P-152", "id_kind": "proposal", "kind": "suggestion", "permanent": False,
         "title": "训练一个分类小模型", "summary": "拒绝的建议。", "trash_reason": "rejected",
         "trashed_at": demo_seed._iso(now - dt.timedelta(hours=1)), "type": "training", "hardness": "soft",
         "purge_at": demo_seed._iso(now + dt.timedelta(days=59))},
    ]


def _vocab_debt(now):
    """潜在任务词表行：type 闭集里看板别处不出现的三种（调研 / 评审 / 训练）+ 其他。"""
    src = [{"channel": "manual", "date": "2026-09-01", "quote": "先记下", "who": "me"}]
    return [
        {"id": "P-161", "display_id": "P-161", "id_kind": "proposal", "title": "其他类型的潜在任务",
         "summary": "type=other 的词表行。", "type": "other", "hardness": "soft", "sources": src},
        {"id": "P-162", "display_id": "P-162", "id_kind": "proposal", "title": "调研新的向量数据库选型",
         "summary": "type=research 的词表行。", "type": "research", "hardness": "soft", "sources": src},
        {"id": "P-163", "display_id": "P-163", "id_kind": "proposal", "title": "评审队友的 RFC 草稿",
         "summary": "type=review 的词表行。", "type": "review", "hardness": "soft", "sources": src},
        {"id": "P-164", "display_id": "P-164", "id_kind": "proposal", "title": "训练一个小分类模型",
         "summary": "type=training 的词表行。", "type": "training", "hardness": "hard", "sources": src},
    ]


def _fold_receipts(now):
    """§44.6 并入回执一条：看板提案列顶的一行 info 通知「刚才的输入已并入 R-xx「<title>」（没有建新卡）」。"""
    return [{"id": "a3f1c2d4e5b6a7c8d9e0f1a2b3c4d5e6", "req": "R-105",
             "title": "example-bench: 修 flaky 的 e2e 测试（retry 逻辑）",   # = 目标卡的展示名（dashboard._fold_receipts 现查）
             "channel": "quick_capture", "at": _epoch(now, seconds=30)}]


def _merge_suggestions(now):
    """§21 合并建议卡三态：分析中 / 完成（按分组）/ 失败。"""
    return [
        {"id": "MS-1", "ids": ["P-131", "P-133"], "status": "analyzing", "requested_at": _epoch(now, minutes=2)},
        # partition：两组 + 两张分组方案没点名的卡 → 「保持独立：A、B」（分隔符「、」是原生 joined 的独立词）
        {"id": "MS-2", "ids": ["P-101", "P-132", "P-133", "P-102", "P-103"], "status": "done", "verdict": "partition",
         "confidence": "medium", "rationale": "两张讲导出报告，一张讲入职文书，另两张各自独立。",
         "requested_at": _epoch(now, minutes=20),
         "groups": [{"primary": "P-101", "ids": ["P-101", "P-132"], "reason": "同一份报告"},
                    {"primary": "P-133", "ids": ["P-133"], "reason": "独立"}],
         "action_plan": ["P-132 并入 P-101", "P-133 保持独立", "P-102 / P-103 不动"]},
        {"id": "MS-3", "ids": ["P-102", "P-103"], "status": "failed", "error": "model timeout after 60s",
         "requested_at": _epoch(now, hours=1)},
        {"id": "MS-4", "ids": ["P-101", "P-132"], "status": "done", "verdict": "merge", "primary": "P-101", "confidence": "high",
         "rationale": "同一份报告的两次表述。", "requested_at": _epoch(now, minutes=30), "action_plan": ["P-132 并入 P-101"]},
        {"id": "MS-5", "ids": ["P-103", "P-133"], "status": "done", "verdict": "keep_separate", "confidence": "low",
         "rationale": "一个是 planning 文书、一个是 onboarding 清单。", "requested_at": _epoch(now, minutes=45)},
        {"id": "MS-6", "ids": ["P-131", "P-134"], "status": "done", "verdict": None, "confidence": "deterministic",
         "rationale": "§38 规则：同标题前缀。", "requested_at": _epoch(now, minutes=50)},
    ]


def _archived_extra(now):
    """再两行封存卡：原来在「已合并」/「待验收」（prev_status 词表）+ kind=debt（「潜在任务」章）。"""
    return [{"id": "P-072", "title": "重复的周报提案（并入 P-071）", "summary": "被并入主卡后封存",
             "kind": "suggestion", "archived_at": demo_seed._iso(now - dt.timedelta(minutes=40)),
             "archive_reason": "auto", "prev_status": "merged", "display_id": "P-072", "id_kind": "proposal"},
            {"id": "P-073", "title": "待验收时被你封存的潜在任务", "summary": "kind=debt、原来在待验收",
             "kind": "debt", "archived_at": demo_seed._iso(now - dt.timedelta(seconds=30)),
             "archive_reason": "user", "prev_status": "review", "display_id": "R-073", "work_id": "R-073", "id_kind": "work"}]


def build_board(now=FIXED_NOW):
    board = demo_seed.build("initial", now=now)
    board["needs_approval"] += _vocab_proposals(now)
    board["running"] += _vocab_running(now)
    board["needs_input"] = list(board.get("needs_input") or []) + _vocab_needs_input(now)
    board["review"] += _vocab_review(now)
    board["trash"] += _vocab_trash(now)
    board["debt"] += _vocab_debt(now)
    board["merge_suggestions"] = _merge_suggestions(now)
    board["fold_receipts"] = _fold_receipts(now)   # §44.6 add-only 顶层键
    # 第二张待验收卡的「耗时」带分钟位（1 小时 39 分）——时长词表 {h}小时{m}分 才渲染得到
    board["review"][1]["review_at"] = board["review"][1]["dispatched_at"] + 5977
    board["archived"] = _archived(now) + _archived_extra(now)
    for lane in ("needs_approval", "running", "needs_input", "review", "trash", "debt", "archived"):
        board["counts"][lane] = len(board[lane])
    board["device_label"] = "demo-mac"
    # §48 三源健康投影：一个正常、一个静默失败、一个关着（接入区「运行状态」三种句子都渲染到）；
    # §48.7 add-only：slack 带 last_attempt（「最近一轮 N分钟前」）与一轮已完成的 test_round 回执
    board["radar_sources"] = {
        "gmail": {"enabled": True, "last_ok": demo_seed._iso(now - dt.timedelta(minutes=5)), "skip_reason": None, "stale": False,
                  "last_attempt": demo_seed._iso(now - dt.timedelta(minutes=5)), "test_round": None},
        "slack": {"enabled": True, "last_ok": None, "skip_reason": "no_credentials", "stale": False,
                  "last_attempt": demo_seed._iso(now - dt.timedelta(minutes=3)),
                  "test_round": {"requested_at": demo_seed._iso(now - dt.timedelta(minutes=3, seconds=20)), "state": "done", "note": None}},
        "obsidian": {"enabled": False, "last_ok": None, "skip_reason": None, "stale": False,
                     "last_attempt": None, "test_round": None},
    }
    return board


def build_lanes():
    return server_lanes.catalog()


# 目录字段（§68.1 `path: "dir"`）在 fixture 里一律「目录不存在」：笔记库区 / 审批区的
# 「⚠︎ 目录不存在」警告与 创建 / 创建文件夹 按钮才渲染得到；真实存在性依赖生成机器的磁盘，
# 抹成固定值保证零 diff。
_FIXTURE_PATH_EXISTS = False


def build_settings():
    """GET /api/settings 的快照（文案 server-owned）：home 里只有一条 override
    `obsidian_raw`（默认空值下笔记库的目录按钮无从渲染），其余全默认；目录字段的
    `path_exists` 抹成固定值（见 _FIXTURE_PATH_EXISTS）。"""
    with tempfile.TemporaryDirectory() as tmp:
        overrides = paths.settings_overrides_path(Path(tmp))
        overrides.parent.mkdir(parents=True, exist_ok=True)
        overrides.write_text('{"obsidian_raw": "~/Documents/Obsidian Vault/2 - raw"}\n', encoding="utf-8")
        snap = settings_catalog.snapshot(Path(tmp))
    for section in snap["sections"]:
        for field in section["fields"]:
            if field.get("path") and field.get("effective"):
                field["path_exists"] = _FIXTURE_PATH_EXISTS
    return snap


# 凭证行的五种状态章都要渲染到（§66.2 control:settings.credentials:*）：Anthropic / Gmail 已保存且可验证
# （「已保存（未验证）」；Gmail 的探针要地址，fixture 目录里地址为空 → 「还没填 Gmail 地址——」）、豆包语音已保存
# 且无探针（「已保存（App 内管理）」）、Slack 缺 secrets 文件但 §19 旧路径有（「使用旧路径」——旧路径在生成机器
# 的 $HOME 下，抹成固定值）、Ark 未设置（「未设置」）。
_FIXTURE_LEGACY = {"slack-user-token.txt": True}


def build_secrets():
    """GET /api/secrets 的快照：三把已保存（Anthropic / Gmail / 豆包语音）、Slack 走旧路径、Ark 未设置。
    值是占位符，只决定 present；mtime 与 legacy 抹成固定值保证零 diff。"""
    with tempfile.TemporaryDirectory() as tmp:
        secrets_dir = paths.secrets_dir(Path(tmp))
        secrets_dir.mkdir(parents=True)
        for name in ("anthropic-api-key.txt", "gmail-app-password.txt", "volcano-speech-key.txt"):
            (secrets_dir / name).write_text("demo-placeholder\n", encoding="utf-8")
        snap = secrets_store.snapshot(Path(tmp))
    for row in snap["secrets"]:
        if row.get("mtime"):
            row["mtime"] = int(FIXED_NOW.timestamp())
        row["legacy"] = _FIXTURE_LEGACY.get(row["name"], False)
    return snap


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--board", default=BOARD_PATH)
    parser.add_argument("--lanes", default=LANES_PATH)
    parser.add_argument("--settings", default=SETTINGS_PATH)
    parser.add_argument("--secrets", default=SECRETS_PATH)
    args = parser.parse_args(argv)
    fresh = {args.board: uc.dump_json(build_board()), args.lanes: uc.dump_json(build_lanes()),
             args.settings: uc.dump_json(build_settings()), args.secrets: uc.dump_json(build_secrets())}
    if args.write:
        for path, text in fresh.items():
            uc.write_text(path, text)
        print("wrote %s" % ", ".join(uc.display_path(p) for p in fresh))
    return _check_fresh(fresh) if args.check else 0


def _check_fresh(fresh):
    stale = [p for p, text in fresh.items() if not os.path.exists(p) or uc.read_text(p) != text]
    if stale:
        print("stale fixture(s): %s — rerun with --write"
              % ", ".join(uc.display_path(p) for p in stale), file=sys.stderr)
        return 1
    print("parity fixtures are fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
