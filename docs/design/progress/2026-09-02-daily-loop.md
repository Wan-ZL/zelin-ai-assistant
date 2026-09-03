pr: `feat/daily-loop`（PR-P5）
phase: P5（D10 / D12 / D18；R2.4；素材库消费侧 R2.5.3）
law: §1 / §2 / §9 / §15 / §40.5 / §49 / §50 追记；**§70（新增）**

**每日自我改进循环，先维护再提案**：actd pass 内 `daily_loop.tick`（默认 03:30 本地，一天一次，标记 `state/daily_loop.json`；进程级总闸 `AIASSISTANT_DAILY_LOOP=0` 给测试沙箱）。维护 = `act/lib/maintenance.py`：提案列 + 潜在任务列去重合成（同题簇 → 一张新卡 `merged_from[]`，旧卡 `daily-merge: 并入 <new>` 进回收站）+ 过时卡进回收站 `stale:<deadline_passed|diagnostic_expired|superseded|idle>`（45 天 + guards：未来 deadline / user_titled / 提及 ≥3 / 同簇在跑 / 解析不了 = 不动）；循环卡保留 90 天，`purge_at` 与 `purge_trash` 单源（§9/§40.5）。

提案 = `act/lib/loop_inputs.py` 十二个确定性读取器（s2 §3 parse spec：卡片 execution / analytics 风暴 / radar_failed / 写风暴 / actd.log / install_report / launchd 日志 / doctor FAIL / 夜间变异 pinned issue / GitHub issue（owner 铸卡、他人只摘要、「do it」升格——D18）/ PR 红 CI + owner 评论（D12）/ 素材库 §62 台账（`new` / `picked_up` → 提案，回写 `picked_up` → `proposal_created` + `links.proposal_id`）），指纹去重（registry 含回收站 + 90 天台账）、每 class 一条、GitHub 同题不重提、≤ `max_proposals_per_day`（默认 5），铸 `🤖` card_sent 卡 channel `self_improve`（write-locked → proposed，照旧人批），外来文本 fence。**不调 LLM**（R2.9）；三阶段隔离绝不崩 pass；审计 `state/daily_loop.jsonl`（1 MB 帽）。投影 `maintenance` 顶层键 + web `MaintenanceBanner`「今日整理：合并 N、清理 M（可撤销）」（不弹通知）；设置 `daily_loop.*` 五键（config + overrides + `GET/PUT /api/settings/daily-loop` + web section「每日整理」，actd 每 pass 现读）。

**结构决定**：编排住 `act/lib/daily_loop.py` 而非 `act/daily_loop.py`——actd 必须 import 它（trash/合成是状态转移，单写者），而 §58.3 禁止 entrypoint 互引；CLI `python3 -m act.lib.daily_loop --plan` 只读。**待后续**：P6 通道对 `self_improve` + 物理 repo 的免批准入；semantic（改述）去重需 §44.1 式 detached 判官，本轮只认确定性信号；tests/__init__ 出网名单收编 `gh` 待 doctor 的 `gh auth status` 探针可注入。
