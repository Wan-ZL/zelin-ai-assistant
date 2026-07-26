# 组件间数据契约（锁定 — 三层都按此实现，不得偏离）

> **English orientation** — This is the frozen data contract between the Python pipeline and the
> Mac app. Three files make it up: `act/registry/<ID>.yaml` (source of truth; state machine
> `detected → card_sent → approved → executing → review → delivered`, any state → `trashed`),
> `state/dashboard.json` (actd writes, app reads), and `state/inbox/<uuid>.json` (app writes,
> actd reads then deletes). Fields are **add-only** — never renamed or removed; the Swift side
> decodes every new field with `decodeIfPresent`. Change this file *before* any code that touches
> these shapes. **Section numbers §1–§24 are referenced from code and docs — never renumber.**
> The Chinese body is canonical.

## 1. 注册表 YAML（真源）— `act/registry/<ID>.yaml`

一条需求一个文件。状态机：
`detected → card_sent → approved → executing → review → delivered`，旁支 `rejected` / `merged_into:<父ID>`；merge-review 终态 `merged`（+ 顶层 `merged_into` 字段，语义见 §21）。

字段（见 R-001 实例）：`id, title, type, tier(T0|T1|T2), status, hardness(hard|soft), deadline(YYYY-MM-DD|null), repeated_mentions(int), green_sign_required(bool), disagreement(str|null), cost_estimate_usd(num|null), sources[{channel,date,ref,quote}], plan(str|list), outputs?, card{sent_at,slack_ts?,slack_channel?}, execution?{session_id,dispatched_at,log}, notes`。

多条 doc 的文件（如欠账批量）= YAML 列表，每项同 schema 子集。

## 2. `state/dashboard.json`（actd 写，Mac app 只读，原子写：先写 .tmp 再 rename）

```json
{
  "generated_at": "2026-07-06T20:10:00Z",
  "counts": {"needs_approval": 1, "running": 0, "needs_input": 0, "completed": 0, "debt": 5},
  "needs_approval": [{
    "id":"R-001","title":"...","tier":"T1","tier_hint":"一键可批",
    "hardness":"hard","deadline":"2026-07-14","days_left":8,"repeated":3,
    "cost_usd":12,"show_cost":true,"green_sign":false,"disagreement":null,
    "improvement_of": null,
    "sources":[{"who":"manager","channel":"meeting","date":"2026-07-01","quote":"..."}],
    "plan":["step1","step2"],"outputs":["..."]
  }],
  "running":     [{"id":"R-001","name":"...","session_id":"...","cwd":"...","state":"working","started_at":1783367685}],
  "needs_input": [{"id":"...","name":"...","session_id":"...","state":"blocked","waiting_for":"permission"}],
  "completed":   [{"id":"...","name":"...","session_id":"...","state":"done","cwd":"..."}],
  "debt":        [{"id":"R-002","title":"...","hardness":"hard","type":"process"}]
}
```
- `show_cost` = cost_usd 是否 ≥ config.show_cost_above_usd（<$5 时 false，app 不显示成本）
- running/needs_input/completed 由 actd 把注册表中 status=executing 的项与 `claude agents --json` 按 session_id join 得到
- debt = status=detected 的项

**v0.10 新增字段**（全部 optional，Swift 侧一律 `decodeIfPresent`；注册表存 ISO 字符串，dashboard 输出 **epoch int**——与 `started_at` 一致）：
- 审批卡分区项（needs_approval，含 raising 占位项）加 `delivery_mode`（`"chat"|"repo"`，语义见 §20）
- `running[]` 常规项加 `summary`(str) / `plan`([str]) / `dod`([str]) / `log`(执行日志路径) / `dispatched_at`(epoch int) / `delivery_mode`(str) / `last_error`(str)
- `running[]` **混入 queued 项**（注册表 status=approved、尚未成功派发的任务）：`{id, name, state:"queued", summary, plan, dod, delivery_mode, dispatch_error(str|null)}` —— 无 `session_id`/`copy_cmd`（还没有会话）；`dispatch_error` = execution.last_error（上次派发失败原因，重试成功后消失）
- `review[]` 加 `delivered_summary`(str) / `final_draft`(str) / `plan`([str]) / `sources`（与审批卡 sources 同形 `[{who,channel,date,quote}]`）/ `log`(str) / `dispatched_at`(epoch int) / `review_at`(epoch int) / `delivery_mode`(str)
- `completed[]` 加 `summary`(str) / `delivered_summary`(str) / `accepted_at`(epoch int) / `dod`([str])
- `debt[]` 加 `sources`（同上形状）

**v0.11 行为补充（add-only，字段形状不变）**：`completed[]` 只保留按 `accepted_at` **降序**（最新在前，缺失/不可解析的排最后）的最近 **50** 条（`act/lib/dashboard.py` `COMPLETED_CAP`）；`counts.completed` 仍为**真实总数**，因此可能大于 `len(completed)`。Swift 侧无需改动——列表照常解码，计数徽章一律读 `counts`。

**v0.20.0 新增字段（add-only，Swift 一律 `decodeIfPresent`；见 §10 archive/re-raise）**：
- 顶层新分区 `archived: [{id, title, summary, kind("debt"|"suggestion"), archived_at(str|null), archive_reason("user"|"auto"|null), prev_status(str|null), type, hardness}]`（`load_archived()`，按 `archived_at` 降序 cap 50，`act/lib/dashboard.py` `ARCHIVED_CAP`；镜像回收站 `trash[]` 行 + archive 三字段）；`counts.archived` = **真实总数**。archived 卡**不进**任何看板列（same as trash）。
- `needs_approval[]` 每项加 `reraised`(bool，= truthy `execution.reraised_at`) + `reraised_note`(str)——「回锅」marker：这张提案来自一张你已验收过的卡的 re-raise，App 显 amber「↩︎ Returned」badge + `reraised_note` 的新诉求。

## 3. `state/inbox/<uuid>.json`（Mac app 写，actd 读后删除）

```json
{"id":"R-001","action":"approve","comment":null,"ts":"2026-07-06T20:12:00Z"}
```
`action` ∈ `approve` | `reject` | `comment`。`comment` 动作携带 `comment` 文本（= 💬 修改方向，actd 把它并入需求的 plan/notes 并保持 card_sent 等重新审批）。

## 4. 执行器派发（actd → claude）

approved 的需求：
1. 组装 prompt = 需求 title+plan+sources + **记忆注入**（读 `~/.claude/projects/<encoded ~/Projects>/memory/MEMORY.md` 及相关 program map 摘要，作为 system context）+ 质量门指令（自检可运行 + fresh-context 审 diff + 交付 draft PR，不 merge/不发对外消息）+ 若 type=training 则强制每 ckpt system card。
2. 派发：`cd <target_repo> && claude --bg --dangerously-skip-permissions "<prompt>"`（target_repo 默认 config 的 default_target_repo 或需求指定）。
3. 记录 `execution.session_id`（从派发输出或 `claude agents --json` 最新匹配 cwd 取）+ dispatched_at + log 路径；status → executing。

## 5. macOS 通知（actd）

> **v0.14 追记**：本节的 `osascript` 发送机制已被 **§28 的 notify_queue 中继**
> 取代（通知由 App 以自身身份发出，osascript display-notification 路径已整体
> 移除）。触发时机与文案约定不变，仍以本节为准。

状态跃迁时用 `osascript -e 'display notification ...'`：
- 新 card_sent（雷达发现新需求）→ "有新需求待审批：<title>"
- executing → done → "任务完成：<title>"
- executing → blocked(needs_input) → "任务需要你输入：<title>"
- 凭证失效（执行日志含 auth/login 关键词）→ "需要重新登录：<service>"

## 6. Mac app 行为

- LSUIElement（菜单栏 app，无 Dock 图标），NSStatusItem
- 每 5s 读 dashboard.json 重渲染；菜单栏标题显示待审批数（>0 时高亮）
- 五区：待审批（卡片带 ✅/❌/💬 按钮）/ 运行中 / 需输入 / 已完成 / 欠账（v0.17 起展示层更名「备选/Backlog」，见 v0.17 additions；registry `status=detected` 与 dashboard 的 `debt` key 不变）
- ✅→写 `{action:approve}`；❌→`{action:reject}`；💬→弹输入框→`{action:comment,comment:...}`
- "运行中/需输入"项点击 → 复制 `claude --resume <session_id>` 到剪贴板（方便进会话看）
- app 绝不直接调 claude / 改注册表 / 持密钥——只读 dashboard.json、只写 inbox

---

# v0.1 additions（可读性 + 欠账循环 + 回收站）

## 7. 卡片可读性重构（needs_approval + debt 都适用）

dashboard.json 的每个 needs_approval card 新增字段：
- `summary`（string）：**大白话一句话**——不用行话，说清"这是什么/批了会发生什么"。**默认只显示它**（黑色、醒目）。
- `target_repo`（string 路径）、`target_name`（basename）、`target_kind`（"new"|"existing"）：
  - actd 计算：target_repo 目录存在且非空 → "existing"；否则 "new"。
  - 卡片默认显示一行：新建 = 🟢 `新建 repo: <name>`；修改现有 = 🟠 `修改现有: <name>（只提 draft PR，不动主分支）`。
- 原有字段（sources / plan / tier_hint / hardness / deadline / cost 等）保留，**仅在展开时显示**。

debt item 新增 `summary`（同上，大白话）。

**Mac app 卡片渲染（重构）**：
- 默认折叠：`summary`（黑，大字）+ 目标行 + badge 行（tier / deadline / 成本 / hard·soft / 重复×N）+ 按钮。
- "展开详情 ▸" 切换 → 显示带小标题的两块：**「需求来自」**(sources，灰字原话) 和 **「要做什么」**(plan，编号)。折叠为默认。
- 目的：不展开就能一眼看懂；灰/黑差异由显式小标题承载，不靠颜色猜。

## 8. 欠账 → 建议 循环

- debt 行新增两个按钮：
  - **「研究并提议」** → 写 inbox `{id, action:"raise"}`。
  - **「删除」** → 写 inbox `{id, action:"trash"}`（进回收站）。
- actd 收到 `raise`：调 `analyze.expand_debt(req)`（headless `claude -p` 把简短欠账扩成完整建议：summary/plan/cost/target_repo 建议）→ status=card_sent → 出现在待审批。失败兜底：summary=title、plan=[title]、标注 needs manual。

## 9. 回收站（trash / recycle bin）

- 新状态 `trashed`，字段：`trashed_at`（ISO）、`prev_status`（恢复用）、`trash_reason`（"rejected"|"deleted"）、`permanent`（bool，默认 false）。
- `reject` 动作改为 → 进回收站（status=trashed, prev_status=card_sent, reason=rejected），**可恢复**。
- debt 的 `trash` 动作 → status=trashed, prev_status=detected, reason=deleted。
- dashboard 新增区 `trash`（+ `counts.trash`）：每项 `{id, title, summary, kind:"suggestion"|"debt", trashed_at, trash_reason, permanent, type, hardness}`。
- app 回收站区（默认折叠）：带**搜索框**（客户端过滤 title/summary）；每行按钮 **「恢复」**(→inbox `{action:"restore"}` 回到 prev_status) 和 **「永久保存」**(→inbox `{action:"pin"}` 设 permanent=true)。
- 保留策略：actd 清理 trashed 中 `trashed_at` 早于 `config.trash.retention_days`(默认 60) 且 `permanent!=true` 的项（硬删）。config 加 `trash.retention_days`。

## 10. inbox 动作全集（app → actd）
`approve` | `reject`(→trash) | `comment` | `raise`(debt→建议) | `trash`(→回收站) | `restore`(回收站→prev_status) | `pin`(回收站项设永久) | `capture`(快速捕获，见下) | `done_external`(已办完·系统外完成，v0.10.2，允许状态扩展 v0.12) | `abort_execution`(停止并退回待审批，v0.10.2) | `stop_to_review`(停止并收下成果待验收「去待验收」，见下) | `revert_review`(退回待验收，v0.10.2) | `merge_review`(多选请求合并建议，v0.12，见 §21) | `merge_apply`(接受合并建议，v0.12，见 §21) | `merge_dismiss`(取消合并建议，v0.12，见 §21) | `merge_force`(强制合并·用户钦定主卡、跳过 AI，携带 `ids`≥2 + `primary`，v0.31，见 §21) | `import_claude_sessions`(一键导入 Claude Code 近期会话，v0.13.x，见 §22) | `weekly_digest_now`(立即生成每周摘要，v0.14，无 `id` 字段，见 §24) | `feedback`(建议上报，无 `id` 字段、携带 `ids` 数组（可空），见 §29) | `defer`(存备选，提案→备选，v0.18，见下) | `archive`(封存线程,已验收/备选→归档,v0.20.0,见下) | `unarchive`(归档→prev_status,v0.20.0,见下) | `answer_input`(回答需输入，携带 `id`+`text`，v0.39.0，见 §39)。actd 读后删 inbox 文件。

**v0.10.2 逆向动作**（公共规则：状态不匹配的动作 = 幂等 no-op + 日志，防连点/迟到 inbox；三个动作均走现有 `inbox_{action}` analytics 自动打点）：
- `done_external`（已办完·系统外完成）：允许 `card_sent | review | approved | executing`（v0.12 从 `card_sent | review` 扩展；动机：agent 停在 blocked 等输入、但 Zelin 已在 attach 会话里拿到交付——这是唯一的完成出口）→ 置 `delivered`；`execution.accepted_at` = UTC ISO now；notes 追加 `[done outside] Zelin 在系统外完成`。分状态行为：
  - `card_sent | review`：有活 session 不动它（人做完了，AI 会话自然闲置）——原语义不变；
  - `executing` 且有 `session_id`：先 best-effort `executor.harvest_delivery(session_id)`（**非空才写** `execution.delivered_summary`/`final_draft`，失败只记日志），再 best-effort `executor.stop_session(session_id)`（清掉挂着的 blocked agent；失败只记日志，**绝不阻塞交付落账**），然后照常落账；
  - `approved`（排队未派发）：直接落账，无 harvest/stop。
- `abort_execution`（停止并退回待审批）：允许 `approved | executing | review`（**v0.28.1 §30 add-only**：review = 被 attach 回流投影进运行中的待验收卡，「退回提案」丢弃这轮重跑）→ 活 session 先 best-effort 停止（`executor.stop_session(session_id)`，即 rework「活进程先 claude stop」的同一路径；stop 失败只记日志，不阻塞状态回退）；`execution.session_id` 归档为 `execution.aborted_session_id` 后删除（保证重新批准时干净重派发），删 `execution.done`，记 `execution.aborted_at` = ISO now → 置 `card_sent`。
- `revert_review`（退回待验收）：允许 `delivered` → 置 `review`；删 `execution.accepted_at`，记 `execution.reverted_at` = ISO now。

**`stop_to_review`（停止并收下成果待验收，「去待验收」）**：允许 `executing | approved | review`（**v0.28.1 §30 add-only**：review = 被 attach 回流投影进运行中的待验收卡，「去待验收」停掉回流 session、重新收割刷新交付、留在 review；harvest 门从「仅 executing」放宽为「有活 session 即收割」）→ 置 `review`（待验收）。语义 = 「停下来我看看它做了什么」——**停掉跑着的 agent、KEEP 它已产出的成果**，落 待验收 让 Zelin ✓验收 / ↩︎打回，**绝不跳过验收**。这是运行中卡片的新「去待验收」出口，区别于同样停 agent 的另两个动作：`done_external`（→`delivered`，「我在系统外做完了」直接完成、跳过验收）、`abort_execution`（→`card_sent`，「不要了」丢弃成果退回待审批）。分状态行为：
  - `executing` 且有 `session_id`：先 best-effort `executor.harvest_delivery(session_id)`（**非空才写** `execution.delivered_summary`/`final_draft`，失败只记日志），再 best-effort `executor.stop_session(session_id)`（停掉跑着的 agent；失败只记日志，**绝不阻塞状态落 review**）；
  - `approved`（排队未派发，无 session）：harvest 为空，直接落 `review`（空交付物，待验收卡照常渲染）。
  镜像自然 `executing → review` 迁移的 review 字段：置 `execution.done = True`、`execution.review_at` = ISO now（dashboard 待验收卡读 `execution.review_at`，且防日后 purge 被误判为需 auto-resume 的崩溃）；notes 追加 `[stopped by user] 手动停止，已收下成果待验收`。其余状态 = 幂等 no-op + 日志（v0.10.2 公共规则）；走现有 `inbox_{action}` analytics 自动打点（`inbox_stop_to_review`），零新增事件。

**v0.18 `defer`（入库，提案→储备）**：允许状态**仅** `card_sent` → 置 `detected`；**保留** summary / plan / sources / repeated_mentions（一切已扩写内容不动，只改 status）；notes 追加 `[deferred] 暂缓，入库`；其余状态（含 raising——扩写完自然变 card_sent 再说）= 幂等 no-op + 日志（v0.10.2 公共规则）；走现有 `inbox_{action}` analytics 自动打点，零新增事件。与 `reject`(→trash) 的区别是功能性的：deferred 卡回到 `detected` 后**继续参与 merge_or_new 匹配**（后续重述静默合并计数、雷达 act-now 重提自动升回 card_sent），trashed 被匹配明确排除（重述从零重新出卡）。撤销 = 储备列现成的「研究并提议」(raise)。

**v0.20.0 archive/unarchive**：archive 仅允许 `delivered`/`detected`(Q2)→`archived`，记 `prev_status`+`archived_at`+`archive_reason`(`"user"`|`"auto"`)；其余状态幂等 no-op。`archived` 语义=完成且封存：排除 `merge_or_new` 匹配（同 trashed/rejected）、对 triage/capture LLM 不可见、relocate 到 `act/registry/archive/` 子目录（退出 hot `_iter_files` 扫描）、NEVER purge。后续相关信息开新卡而非 re-raise 本卡。`unarchive` 回 `prev_status`(usually delivered)，文件移回 active dir、清 archive 字段。**关键（数据安全）**：`next_id()` 与 `load()` 都用 `include_archived=True` 扫 archive 子目录，防新 id 碰撞覆盖归档卡；dashboard/matching 仍默认 `include_archived=False`。archived 进 dashboard 新分区 `archived[]`（`load_archived()`，按 `archived_at` newest-first cap，`counts.archived` 为真实总数），不进任何看板列（同回收站）；build-loop 有 archived skip guard 兜底。auto-archive(`archive_stale`)**首发默认 off**（`archive_after_days=0`）：只封存冷 `delivered`（跳过带未来 deadline / cluster 内有 open sibling / 近期活动的卡），daily gate 防重跑——长期静默的移民/EB-1A matter 默认不被自动封存。

**v0.20.0 re-raise（prior-accept = ownership，Q3）**：新 actionable 信息命中未归档 completed（`delivered`/`merged`）线程 → same_task（title 对齐=真 restatement）则把**原卡翻回 `card_sent`（提案）**、折 source、`repeated_mentions`+1、记 `execution.reraised_at`+`reraised_note`、summary 追加「· 新增:…」；same_task=False（同 thread 不同任务，仅 `thread_key` 命中）则开继承 `thread_id` 的 follow-up 子卡（`card_sent`），**不翻原卡、不污染其标题**。pure restatement / `needs_action=false` / 无新增量 只 bump 不翻。re-raise 前先 `canonical` 到主卡重判 `is_resolved`，绝不把 running/queued/review 卡拽回 card_sent；canonical dead-end 在 trashed/rejected/archived 则回退开新卡。两入口（`merge_or_new` 确定性 backstop + `apply_triage`/`_apply_relates_to` LLM 路径）共用 `registry.reraise_or_followup`。dashboard 的 `needs_approval` 行带 `reraised: bool` + `reraised_note`，App 显「↩︎ 回锅」badge；通知走 `notify.msg_reraised`。（thread_key 只来自 external ref：`gmail:<X-GM-THRID>` / `slack:<thread_ts>`，无强信号=None、绝不 fuzzy——见 `registry.derive_thread_key`。）

**v0.20.0 re-raise 修订（2026-07-15，add-only）**：翻回 `card_sent` 时同步把已完结轮次的 `execution.session_id` 归档为 `reraised_session_id` 并删除，连同删 `execution.done`——否则重新批准后 `dispatch_approved` 会把新一轮当 "already dispatched" 跳过，卡永远停在排队、没有 agent 也没有任何报错；其余轮次账目（`accepted_at`/`delivered_summary` 等）留作历史。两入口共用的 `registry.reraise_or_followup` in-place re-raise 分支为唯一落点。

**capture**（无 `id` 字段，app popover 快速捕获输入框写入）：文件名 `state/inbox/capture-<uuid>.json`，内容
```json
{"action":"capture","text":"<用户一句话>","ts":"<ISO8601>"}
```
actd 处理：立即 `registry.merge_or_new`（title=text，来源 `channel="quick_capture"`，sources 里保留原话）→ 置状态 `raising` → 复用 process_raising 每轮扩写一条 → 变 card_sent 正式提案卡。快速、不堵轮询。幂等：同 text 重复文件不重复建卡（merge_or_new 按 title 合并）。

---

# v0.4 additions（手机端/快速捕获/Gmail/主窗口/进化）

## 12. 命名
显示名 **Zelin's AI Assistant**（2026-07-07 /ask-me 拍板）；app bundle "Zelin's AI Assistant.app"。可执行 `ZelinAIEngineer`、bundle id `com.zelin.ai-engineer` **刻意不改**——TCC 授权与 UserDefaults 挂在 bundle id 名下，改=权限设置全部重来。launchd label 与 `AIASSISTANT_HOME` 环境变量名保持不变（兼容）。仓库目录默认 `~/Projects/zelin-ai-assistant`（旧默认兜底；clone 到任意位置均可，实际解析顺序见 §19 的 home 指针条目）。

## 13. Slack 手机端（self-DM = 指挥通道）

> **v0.21 弃用说明（add-only，本节其余内容保留作历史）**：iMessage 通道整体移除（`act/radar_imessage.py`、`com.zelin.aiassistant.imessageradar.plist`、config `phone_channel`/`imessage_self_handle`、§13 v0.13「iPhone 联动 / iMessage 设置区」note（本节 194 行）、Permissions 里「仅 iPhone 联动需要」的 Full Disk Access 行（185 行）均随之退役）。Slack 的**手机审批角色**也移除：不再有出站通知镜像到 self-DM、不再有 `批准/拒绝/打回/验收 R-xxx` 指令面、不再有 ✅ reaction 审批（§5 通知语义里的「§13 手机镜像」与 §29「notify.py 里 osascript 只剩 radar_imessage 用途」等引用一并作古——notify 现在只走 §28 app 身份中继，`req` 参数保留但不再使用）。**Mac App 成为唯一审批面**。**保留**：Slack self-DM 的**快速捕获**（下面 #0 那条：给自己发一条文字/图片/视频 → 三选一建卡），以及全部 Slack 入站 ingest（DM/群/@提及 + MCP 兜底）——self-DM 现在是**只进不出**的手机端捕获入口，助手不再往里回帖。

- radar_slack 对 **自己→自己的 DM**（im channel with self）做特殊处理：自己发的消息 = 指令/快速捕获，其他 DM/群/频道逻辑不变。
- **快速捕获（#0）**：self-DM 文字 → LLM 收到（新文字 + 现有注册表条目清单 id+title+status）→ 三选一 JSON：`{"action":"new_proposal", ...卡片字段}` / `{"action":"relates_to","req":"R-xxx","note":...}`（把该条目 raise/追加 note 并回 DM 告知"已在弄/已关联"）/ `{"action":"ignore","reason":...}`。
- **图片/视频**：self-DM 附件 → 用 token 下载（files:read）到 `state/media/<ts>/` → 视频先拆帧（ffmpeg 有则用之，否则 `mac/framegrab`(AVFoundation, build.sh 编译) 抽 ≤12 帧）→ `claude -p` 带图片路径识别 → 走快速捕获同款三选一。
- **出站通知**：notify 增加 Slack 通道（token 存在时）——新卡/待验收/需输入/恢复放弃 发到 self-DM，格式含 `#R-xxx`。
- **手机审批**：self-DM 回复 `批准 R-xxx` / `拒绝 R-xxx` / `打回 R-xxx <反馈>` / `验收 R-xxx` → 写 inbox 同名 action。对通知消息点 ✅ reaction（reactions:read 轮询）= 批准该消息里的 R-xxx。
- 新增 user-token scopes：`files:read, chat:write, reactions:read`（SLACK_SETUP.md 更新）。
- **通道可插拔（v0.12 additive）**：本节的指令面（`批准/拒绝/打回/验收 R-xxx`、快速捕获、reaction/tapback 审批、🔔 出站镜像）不与 Slack 绑定。config `phone_channel: imessage` 时由 `act/radar_imessage.py` 在 iMessage"给自己发消息"线程上提供**同一指令面、写同一批 inbox 决策文件**（`~/Library/Messages/chat.db` 只读轮询 + osascript 发送；👍/❤️ tapback = ✅；marker = 最后 message ROWID，`state/imessage_radar.json`；出站追踪 `state/imessage_outbox.json`；文法/inbox 写入直接复用 radar_slack，两通道不可能漂移）。`phone_channel: none|slack`（含缺省）时 Slack 侧行为不变。iMessage 侧 v1 仅支持文字（图片/视频仍走 Slack 路径）。详见 `docs/IMESSAGE_SETUP.md`。

## 14. Gmail 捕获
`act/radar_gmail.py`：imaplib SSL 轮询 INBOX 未读（只读、不改已读状态优先用 BODY.PEEK）→ LLM 三选一（需要 Zelin 处理→卡片 / FYI 跳过）。config: `sources.gmail: {address, app_password_path?, enabled}`；密码按 §19 三级顺序解析（`config/secrets/gmail-app-password.txt` → config 显式 `app_password_path` → 旧默认 `~/Desktop/Keys/gmail-app-password.txt`），任一处都没有则静默 no-op。launchd 每 5 分钟（纯网络，TCC 安全）。marker=最后处理的 UID（state/gmail_radar.json）。docs/GMAIL_SETUP.md 写建应用专用密码步骤。

**§14bis 命令后备通道（v0.45，Zelin 2026-07-22 拍板「app password 可用就配置；不可用就定时走 MCP/CLI 主动抓取」的第二分支）**：config 新增 `sources.gmail: {fetch_command?}`（override 键 `gmail_fetch_command`，扁平 + 嵌套两形皆收）。非空即赢过 IMAP——配置了命令就是明确选择；此时**无 app password 也不再 `no_credentials` no-op**。契约（`fetch_via_command`）：命令经 `shlex` 解析为 argv 直接执行（不过 shell；管道写进目标脚本里），env 带 `GMAIL_RADAR_LAST_UID`=当前 marker，stdout 输出 JSON 数组 `{uid:int 单调递增, from, subject, date, message_id, body, gmail_thread_id?}`；`uid <= marker` 在雷达侧丢弃但仍推进 marker（与 IMAP 同规）；dict 层预过滤 = noreply 发件人 + `Accepted:` 日历回执（List-Unsubscribe 等 MIME-only 信号由命令侧自理）。超时 300s。失败分类进健康词表（add-only）：`command_failed`（跑不起来/非零退出/超时）/ `command_bad_output`（stdout 不是 JSON 数组）——绝不与「没有新邮件」混淆，App 设置页照 §15.3 映射成大白话。`--check` 在命令模式下只验证可执行文件可解析（无登录可测）。抓取之后的 triage 管线与 IMAP 路径逐字同一条。

## 15. 主窗口（menu bar 之外的正经窗口）
菜单栏加"打开主窗口"；窗口可关（app 继续后台跑，accessory 不变）。四个区：
1) **依赖检查**：逐行 Node/npx 与录制引擎存活（引擎经 `npx screenpipe@<pin>` 运行，v0.11 起不再检查 /Applications/Screenpipe.app）、claude CLI、gh、PyYAML、Obsidian vault 路径、Slack token、Gmail 密码 —— ✅/⚠️ + 按钮（打开下载页 URL 或 reveal 路径）。"车跑之前轮子都得在"。
2) **录制与 ingest**：启动/退出 Screenpipe（open -a / osascript quit）、"立即导出"（跑 ingest/screenpipe-export.sh）、"立即 ingest"（跑 process 脚本）、显示最近一次导出/ingest 时间（读 log mtime）。
3) **设置**：写 `state/settings_overrides.json`（app 只写这个文件；config.load_config() 最后合并 overrides，优先级最高）。字段：obsidian_raw、slack_token_path、gmail address/密码路径、成本双阈值、trash 保留天数、界面语言(zh/en，先存值)、feature flags 开关。
   - **v0.13 追加（add-only）**：`telemetry.enabled`（bool）——首启权限页「匿名使用统计」复选框取消勾选时写嵌套形式 `{"telemetry": {"enabled": false}}`；重新勾选**删除**该 override 键（回落产品默认）。Python 侧 `_apply_settings_overrides` 同时接受嵌套与扁平 `"telemetry.enabled"` 两种形式，且**只认 enabled / level 两个子键**（level 见下方「Telemetry 覆写」补充）——`telemetry.supabase_url` / `telemetry.key_path` 仅 config.yaml 可设，overrides 里出现一律忽略。
   - **v0.13 追加（add-only，consent 门标记）**：「权限体检」页首次**展示**「匿名使用统计」块时，App 写标记文件 `state/telemetry_consent_shown`（内容 = 首次展示的 UTC 时间戳；含义仅是「披露界面出现过」，与勾选结果无关——开关语义仍由上行 `telemetry.enabled` 承担）。`act/lib/analytics_sync` 上传前要求 该标记 / config.yaml `telemetry:` 块 / overrides 的 telemetry 键 至少存在其一，否则整轮 no-op（堵住 install.sh 先装 cron、consent 界面尚未出现过的上传窗口；docs/TELEMETRY.md「上传何时发生」）。
   - **v0.14 追加（add-only，execution 三键 + 保存语义）**：overrides 允许列表（`act/lib/config.py` `_OVERRIDE_FIELDS`）新增三个扁平键，语义与 config.yaml `execution:` 块同名键逐字一致：`default_target_repo`（str，批准卡片的默认执行目录）、`skip_permissions`（bool，claude --bg 是否带 `--dangerously-skip-permissions`）、`create_github_repo`（bool，是否允许自动创建 GitHub 仓库）。**保存语义（app 写入方约束，读取方不变）**：设置页自 v0.14 起改动即持久化（无全局保存按钮），且对每个键 **diff-write**——新值与「不含该 override 的 effective 值」（config.yaml → 内置默认）相同时**删除**该键，不同才写入；app 永不整节镜像写入未被用户改动的键。读取方（`_apply_settings_overrides`）语义不变：键在则覆盖，键缺省则回落 config.yaml/默认。另：v0.14 起「待审批」列的**显示名**改为「提案 / Proposals」（W8）——纯 L() 文案改动，`needs_approval` / `card_sent` 等内部键与本契约各节原文不变。
4) **关于**：版本、repo 路径、`python -m act.report` 提示。

**菜单栏 / popover 补充（v0 bootstrap）**：
- **录制三态**：菜单栏控制 Screenpipe 录制，三态 关 / 仅屏幕 / 屏幕+音频。存 UserDefaults `recordingMode` ∈ `"off"|"screen"|"screen_audio"`，默认 `"screen"`；开 app 时按当前模式**自动启动**录制引擎（引擎运行判定 = `pgrep -f "screenpipe.*record"` 有结果）。引擎启动参数含 sensitive-app 排除（每个 config `recording.ignored_apps` 词条一个 `--ignored-windows`，默认密码管理器 + 无痕窗口标题；`ingest/screenpipe-export.sh` 导出时用同一清单二次过滤——见 docs/PRIVACY.md「你有哪些控制」）。
  - **v0.11 补充（P0-11，覆盖上行 default，字段语义与取值不变）**：fresh install（UserDefaults 无 `recordingMode` key）默认视为 `"off"`，首启弹**一次性**双语 consent alert（`RecordingConsent`，Onboarding.swift）：说明采集什么、去哪里、保留多久，链 docs/PRIVACY.md，按钮 仅屏幕 / 屏幕+音频 / 暂不开启。任一选择均持久化 `recordingMode` + UserDefaults `recordingConsentShown`（Bool），两个 key 任一存在即不再弹；自动启动仅在已存在模式值时进行。已有 `recordingMode` 值的存量安装不受影响、永不询问。
  - **v0.13 补充（覆盖上行 consent 的呈现形式，key 语义与取值不变）**：consent 改为**首启「权限体检」窗口**（`PermissionsWindowController`，Permissions.swift），单一问题「现在开启屏幕记录吗？」——开启 → `recordingMode="screen"`（**仅屏幕**；onboarding 不再提供 屏幕+音频 选项，音频只能事后在 设置/录制菜单 里显式打开），暂不 / 直接关窗 → `"off"`。任一路径都照旧持久化 `recordingConsentShown` + `recordingMode`。窗口同时列出 屏幕录制 / 通知 / 完全磁盘访问（标注「仅 iPhone 联动需要」）三行实时授权状态（2s 轮询 + 窗口重获焦点刷新，探测分别为 CGPreflightScreenCaptureAccess / UNUserNotificationCenter / 试读 `~/Library/Messages/chat.db`）与「匿名使用统计」复选框（见 3) 的 telemetry.enabled），并取代 P1-5 的首启依赖页弹窗（窗口内含「打开依赖检查」入口）。可随时从 App 菜单 / 状态栏右键菜单 /「设置 → 通用 → 权限体检」重开。
- **popover 快速捕获输入框**：一句话回车 → 写 `state/inbox/capture-<uuid>.json`（§10 capture 动作），app 不直接碰注册表。
- **菜单栏图标显示开关**：UserDefaults `showMenuBarIcon`（Bool，默认 true）；录制状态图标开关 `showRecordingIcon`（Bool，默认 true）。
- **语言即时切换**：界面语言存 `settings_overrides.json` 的 `"language"`（`"zh"|"en"`），切换即时生效（app 与 Python 侧共用该值）。
- **v0.28 追加（add-only，交付物默认格式）**：新增扁平 override 键 `default_output_format`（`"markdown" | "html"`，与 config.yaml 顶层同名键逐字一致；`act/lib/config.py` `_OVERRIDE_FIELDS` 用 `_coerce_output_format` 归一化——非法/typo 一律回落 `"markdown"`，yaml 路径同规则）。语义：`"markdown"` = 现状(executor prompt 逐字不变、零回归)；`"html"` 时 `act/executor.py` `build_prompt` 在交付指令前追加一段「以 HTML 起草交付物」指令(文档/报告/`FINAL DRAFT` 用语义 HTML 标签而非 Markdown 语法)。写入方 = 设置页「通用 → 交付物默认格式」分段选择器，按 §14 v0.14 **diff-write** 语义(与不含该 override 的 effective 值相同则删键、不同才写)。读取方 `_apply_settings_overrides` 语义不变。
- **Telemetry 覆写（add-only 补充，docs/TELEMETRY.md）**：设置页「产品改进计划」区写嵌套形式 `{"telemetry": {"enabled": …, "level": …}}`（与首启权限页同一 override 键；扁平 `"telemetry.enabled"` / `"telemetry.level"` 两个点号键 Python 侧同样接受），`config.load_config()` 最后合并（优先级最高，覆盖 config.yaml `telemetry:` 块）：
  - `enabled`（Bool）——匿名使用统计上传总开关。**默认 true（默认开 + 明确可关）**。
  - `level`（`"basic" | "detailed"`，默认 `"basic"`）——上传粒度。非法值一律按 `"basic"` 处理。只有 `"detailed"`（用户主动 opt-in）允许 dispatch / delivery 事件携带 ≤200 字符的指令/交付摘要字段（emit 端 gate：basic 级这些字段根本不写入 events.jsonl，因此也永不上传）。**v0.18 修订（见下条 capture_input 追加）**：detailed 单独不再附带任何内容字段——内容一律再要求 capture_input，本行仅作历史语义记录。
  - **v0.18 追加（add-only）**：`capture_input`（Bool，**默认 true**；level 的内置默认同时改为 **detailed**）——「输入文本上传」开关，第三个 telemetry 子键（嵌套 `{"telemetry": {"capture_input": …}}` 与扁平 `"telemetry.capture_input"` 均接受，`_apply_settings_overrides` 允许列表同步扩为 enabled / level / capture_input 三键；`supabase_url` / `key_path` 仍 config.yaml-only）。语义：`capture_input=true` **且** `level="detailed"`（出厂默认两者皆真；`Config.capture_input_active()` / Swift `Telemetry.contentCaptureActive()`，任一为假即关）时，用户**输入进本 App 的文本**字段（capture 文本、Ask 问题、卡片评论/打回反馈、看板搜索词、用户批准的派发摘要）以 `analytics.clip(…, CONTENT_CLIP=500)` 截断后附在对应事件上；`review_promoted.summary`（交付摘要 = 模型输出节选）自 v0.18 起**整体退役**、不迁入本开关（该事件只剩 exec_s 等元数据）；emit 端 gate，开关未同时打开时这些字段不写入 events.jsonl。**边界（真实性红线）**：收集范围只限用户亲手输入进本 App 的文字——模型输出、屏幕录制内容、邮件与 Slack/iMessage 消息正文（第三方私人通信）、密钥在任何设置下都不收集（字段表见 docs/TELEMETRY.md；因默认收集输入文本，一切披露文案不得声称「不含个人文本」，tests/test_telemetry_level.py 的 honesty drift-guard 检查 Permissions/Settings 文案）。首启呈现同步修订：v0.13 的「匿名使用统计」复选框改为**一行诚实披露（明说含你输入的文本）+ 「详情与关闭在设置」链接**（TelemetryBlockView；`telemetry_consent` 事件随复选框退役），开关全部集中在设置页「产品改进计划」（同一 override 键形状，含单独的「上传我输入的文本」开关）；consent-surface 标记文件 `state/telemetry_consent_shown` 的写入时机与语义不变（披露行首次展示时写入，展示前 analytics_sync 一律不上传）。四条收紧（同版）：①**内容 v2 consent 门**——输入文本字段额外要求标记 `state/telemetry_consent_shown_v2`（仅首启披露行/设置向导的披露块首次渲染时写，`TelemetryConsent.markSurfaceShownV2`；设置页**不**被动写标记——非 lazy VStack 的 .onAppear 在开页即触发、不代表该节真被看到），或 capture_input 被**显式**配置（`Config.telemetry_capture_input_explicit`；设置页「上传我输入的文本」开关被切动时以 captureTouched 始终写键、且该键不被无关保存 diff-drop——已记录的知情选择不可被静默撤销）；旧安装升级后行为遥测沿用 v1 标记、内容在 v2 面世或显式落键前一律不发（`analytics.content_gate`）。②**dispatch.instruction 按 provenance 白名单**——仅当卡片全部 sources 的 channel ∈ {quick, quick_capture}（`act/executor.py` `_USER_ORIGIN_CHANNELS`，fail-closed）才附**标题**（模型起草的 plan 退出该字段）；雷达/混合来源卡的派发事件纯元数据。③**内容字段无条件密钥掩码**——`analytics.clip_content`（Swift 侧 `Analytics.clip` 同模式，drift-guard 锁定）在截断前先按 `sanitize._SECRET_PATTERNS` 掩码，独立于一切 redaction 配置。④带媒体的 quick capture 只记用户打字部分（`_typed`），合成图片提示与本地路径不进 telemetry。

**v0.13 补充（iPhone 联动 / iMessage 设置区，add-only）**：设置页新增「iPhone 联动（iMessage）」区（`mac/Sources/SettingsIMessage.swift`，改动即时生效、不走表单的保存按钮），写两个 §15.3 overrides 键：`phone_channel`（该区只写 `"imessage"` 或 `"none"`）与 `imessage_self_handle`（str，E.164 手机号或 iCloud 邮箱）——两键自 v0.12 起即在 `act/lib/config.py` `_OVERRIDE_FIELDS` 允许列表内，语义见 §13 通道可插拔。App 侧附带职责（不新增数据契约字段）：①开关 = 按 install.sh step 5 相同的占位符替换规则把 `act/launchd/com.zelin.aiassistant.imessageradar.plist` 渲染进 `~/Library/LaunchAgents/` 并 `launchctl load`/`unload`（先写 overrides 再 load，保证 RunAtLoad 首轮就能读到 `phone_channel: imessage`）；②状态行读 `state/radar_health.json` 的 `imessage` 条目（契约 E 同形，radar_imessage 每轮写入）+ `launchctl print gui/<uid>/…`，「立即测试一轮」= `launchctl kickstart`（Full Disk Access 的真值只能来自 launchd 语境下 python 的真实运行结果——TCC 按 responsible process 判权限，app 内直接探测会失真）；③「发送测试消息」经 runtime python（CONTRACT §19 指针）调 `act.radar_imessage` 的同一 osascript 发送路径。

**v0.14 补充（Slack / Gmail 设置区，add-only）**：设置页新增「Slack 接入」「Gmail 接入」两区（`mac/Sources/SettingsSlack.swift` / `SettingsGmail.swift`，改动即时生效），happy path 全程不碰 config.yaml/docs。overrides 允许列表（`act/lib/config.py`）新增 §15.3 键：

- `owner_slack_user_id`（str，语义 = config.yaml `owner.slack_user_id`）——保存 Slack token 时 auth.test 返回的 `user_id` **自动写入**（身份零手填）。
- `slack_channels`（list，语义 = `sources.slack_channels`；条目为 `{"id": "C…", "name": "…"}`（name 可省）或纯 id 字符串；**空列表 = 明确不看任何频道**）。
- `watch_people`（list[str]，语义 = `sources.watch_people`）。
- 两个 list 键同样接受 `sources.` 点号前缀形式。**写入语义（写入方约束）**：App 只在用户实际改动勾选时写整个列表——App 无法可靠解析 YAML 嵌套列表，v0.14 的 diff-write 在这两个键上退化为 change-write；键缺省时 config.yaml 照常生效（读取方语义不变）。
- **App 侧附带职责**（同 v0.13 iMessage 区先例，不新增管线契约字段）：区内开关按 install.sh step 5 占位符规则渲染 + `launchctl load`/`unload` `com.zelin.aiassistant.slackradar` / `com.zelin.aiassistant.gmailradar`；Slack 开关写 §16 的 `features.slack_radar`（语义不变），Gmail 开关写既有 `gmail_enabled` 键（**显式双向写**——App 读不到两层嵌套的 config 层，为保证 UI==生效值，true/false 都落键）。
- **频道/成员目录**经 runtime python（§19 指针）`python3 -m act.lib.slack_setup --directory [--refresh]`（conversations.list/users.list 分页；缓存 `state/slack_directory.json`，TTL 1h——App 侧缓存文件，可随时删除，不属于管线契约；scope 缺失等错误按 §15 语言设置输出双语人话句）。
- **App Manifest 真源** = `config/slack-app-manifest.json`（生成器 `act/lib/slack_setup.manifest_json`，tests/test_slack_setup.py 防漂移）。v0.14 起 scopes 增补 `channels:read` + `groups:read`（频道勾选器需要）——旧 app 需在 api.slack.com/apps 更新 manifest 后 Reinstall to Workspace。
- Gmail 地址字段从「凭证」组移入 Gmail 区（override 键 `gmail_address` 不变）。radar_gmail 健康 `skip_reason` 词表增补 `no_address` / `auth_failed`（add-only；原 `connect_failed` 语义收窄为网络/其他连接问题）。

**v0.14 补充（初始设置向导，add-only；不新增 pipeline 契约字段）**：首启界面从单页权限窗升级为多步「初始设置向导」（`mac/Sources/SetupWizard.swift`，步骤：欢迎+语言 → AI 引擎 → 系统权限 → 屏幕记录 consent → 笔记库 → 健康检查）。

- **完成标记** = UserDefaults `setupWizardCompleted`（Bool）：缺失或非 Bool（损坏）→ 下次启动自动重开向导；只有向导结尾的「完成」按钮写 true。设置 → 通用 提供「重新运行初始设置」随时重开。
- **幂等性**：向导所有步骤预填当前生效值，绝不清除数据、绝不重复导入。录制 consent 的 key 与语义完全不变（`recordingConsentShown` / `recordingMode`，v0.11/v0.13 补充照旧）——已回答过的 consent 在向导里只显示状态行，不再询问；向导中途关窗仍按 暂不 记录（同 v0.13 权限窗行为）。存量安装升级后向导会出现一次（标记缺失），走完即消失。
- **写入面**：只写既有的 §15.3 overrides 键（`language`、`obsidian_raw`——均在 `_OVERRIDE_FIELDS` 允许列表内，且仅在与当前生效值不同时 diff-write）与 §19 的 `config/secrets/anthropic-api-key.txt`（粘贴 key 经 api.anthropic.com/v1/models 免费探针验证通过后才落盘，0600）。笔记库步骤会在所选根目录下创建 4 个标准管线子目录（与 config.py `_derive_obsidian_dirs` 同名，幂等 mkdir）。
- **App 侧附带职责（同 v0.13 iMessage 区先例，不新增契约字段）**：健康检查页的「启动后台服务」按钮按 install.sh step 5 相同的占位符替换规则把 `act/launchd/com.zelin.aiassistant.actd.plist` 渲染进 `~/Library/LaunchAgents/` 并 `launchctl load`；「立即生成一次」经 runtime python（§19 指针）跑 `python -m act.lib.dashboard` 补种 dashboard.json。

**v0.19.0 补充（板级诊断卡 + obsidian 雷达健康，add-only）**：`state/radar_health.json`（契约 E）新增来源键 `obsidian`（同 gmail/slack 形），由 `act/radar.py` 写入，且**仅在 cron ingest chain**（`AIASSISTANT_CRON=1`，install.sh 的 `*/30` 链）语境下写——`radar._owns_health()` 门控，保证退役/被 TCC 挡住的 launchd 语境或手动 `python -m act.radar` 永不能用假的空 vault 覆盖 cron 的好健康。每条 entry 另可携带**可选** `last_cards: int`（add-only；仅 obsidian 在 `ok` pass 写，= 上次成功抓到的卡数；旧 reader 忽略，Swift 侧 `as? Int` 解）。obsidian 的 `skip_reason` 词表：`disabled` / `vault_missing`（目录未配或不存在）/ `vault_empty`（目录在但零 `.md`）/ `no_api_key`（提取失败且无可解析 Anthropic key）/ `extract_failed`（`claude -p` 对 ≥1 note 失败）；扫过但没有比 marker 更新的 note = `ok=True, last_cards=0`，**不是** skip。radar_slack 健康 `skip_reason` 词表增补 `mcp_not_configured`（fallback 开、无 token、claude CLI 无 Slack MCP；`claude mcp list` 预检，缓存 `state/slack_mcp_present.marker` 30 min），语义区别于 transient 的 `mcp_failed:`。App 侧据此在任务台/popover 合成 `DiagnosticsStrip` 诊断卡（`mac/Sources/Diagnostics.swift`，Swift 侧合成，**不新增 dashboard.json partition**）：每张卡一句大白话问题 + 一个直达修复的主按钮，只显示用户已配置却在静默失败的路径，可 dismiss，修好即消失。canonical entry shape 见 `act/lib/health.py` docstring。

**v0.19 追加（add-only，生命周期里程碑遥测,docs/TELEMETRY.md）**:新增 5 个**每装机至多一条**的里程碑事件,喂 `scripts/insights_report.py` 的激活漏斗。产出统一走**去重一次**写法——App 侧 `Analytics.firstReach(feature)`(UserDefaults 标记,`mac/Sources/Utils.swift`,事件 `feature_first_reach{feature}`)、daemon 侧 `analytics.log_first(event, **fields)`(标记文件 `state/analytics/first/<event>`,emit-then-mark、never raises)。App 端:`feature_first_reach{feature:"app_launch"}`(首启)、`feature_first_reach{feature:"ingest_configured"}`(首个 ingest 源可用)。daemon 端:`milestone_first_card{req}`(`registry.save()` 单一 choke,首张进 card_sent lane)、`milestone_first_approval{req}`(actd approve 分支)、`milestone_first_delivery{req}`(executor dispatch 成功)。全部**仅行为字段**(`req`=需求 id / 计数),绝不含卡片标题/链接/摘要等内容,沿用既有 `analytics.content_gate` 隐私边界,无 schema 迁移(走既有 `event`/`props` 列)。报告侧另派生 retention(按 `client_ts`)与 abandonment 两视图,**不新增事件**,只输出聚合计数/比例,device id 永不外泄;跨所有装机的匿名 device 合并计,per-tenant 区分标记暂缓。

## 16. Feature flags + 自我进化
- config `features: {slack_radar, gmail_radar, obsidian_radar, digest, auto_resume, analytics, manager_pack}`，默认全 on；各模块入口检查 flag，off 则 no-op。overrides 可改。
- 周一 digest 末尾加**进化建议**节：基于 analytics（30 天未用的功能→建议关；重复风暴/高拒绝率→建议改），生成 type=self-improvement 的卡片（target_repo=本 repo），批准后照常 claude --bg 实现并以 **draft PR** 交付——app 更新永远走 PR。

**v0.14 追记（add-only；随 §17 v0.14 修订）**：`manager_pack` 随 manager pack ①的移除退出 flag 集合——`DEFAULT_FEATURES` 与设置窗口均不再包含它，代码中无任何调用点检查；config.yaml/overrides 里遗留的 `features.manager_pack` 键按「未知 flag」语义被静默忽略。现行集合 = {slack_radar, gmail_radar, obsidian_radar, digest, auto_resume, analytics}。1:1 准备页（`act.oneonone`）随 §17 digest 生成，受 `features.digest` 门控，无独立 flag。

## 17. 周一 digest + Manager pack
- `python -m act.digest`：待审批积压、待验收积压、needs_input/resume_exhausted 卡住项、低置信度(detected 欠账)清单、双向承诺账本(registry notes 里 [MANAGER-OWES] 标记项)、analytics 摘要+进化建议。产出 markdown 存 workbench + macOS/Slack 通知摘要。crontab 周一 09:07。
- Manager pack（flag: manager_pack）：①obsidian radar 扫到含 manager（watch_people 首项的 first-name token）的新会议记录时，额外派 T0 任务生成**会后 action-item 清单草稿**（workbench/meetings/<date>-action-items.md，通知）；②`python -m act.oneonone` 生成 1:1 准备页（ready/not-ready per registry + 双向欠账 + 上次以来 delta），digest 周一自动附带。

**v0.14 补充（会后清单落点守卫 + 通知合并 + pass 互斥，add-only；2026-07-08 backfill 风暴修正）**：
- **落点守卫**：清单只在 `execution.default_target_repo` 被**显式**配置（config.yaml `execution:` 块或 §15.3 override `default_target_repo`；Python 侧 `Config.default_target_repo_configured`）时写 `<工作台>/meetings/`；未配置时**绝不**创建示例占位路径，改存 **`state/meetings/`**（add-only 目录），并发**一次性**双语通知指向设置页的「任务工作目录」选择器。已发标记 = **`state/meetings_notice.sent`**（内容为首次提示的 UTC 时间戳；存在即不再提示）。bug 时期遗留的占位目录不迁移、不删除，只是不再写入。
- **通知合并**：单个 radar pass 生成 ≤3 份清单时逐份通知；>3 份（backfill 场景）只发一条汇总（"已生成 N 份会后 action-item 清单 → <目录>"）。清单通知统一延后到 pass 末尾发出。summary 新增 `action_items`（本 pass 写出的清单数，仅日志观测用）。
- **pass 互斥**：整个 obsidian radar pass（`--once` 与 loop 模式共用 `scan()`）持有 **`state/radar.lock`**（fcntl.flock 非阻塞，随进程退出自动释放）；抢不到锁的 pass 以 no-op 退出（exit 0，summary 带 skipped 行 + `radar_skip(reason=lock_held)` 埋点），由在跑的 pass 覆盖本轮。actd 不调用该 scan（它只接 act.radar_claude_sessions），其余 radar 各有自己的 marker，锁只属于 act/radar.py。
- **显式启用（行为变更，随 release 记 CHANGELOG）**：manager pack 自此要求 `features.manager_pack` **显式**出现在 config.yaml `features:` 块或 overrides 且为 true（Python 侧 `Config.feature_explicit("manager_pack")`，基于新增的 `Config.features_explicit` 显式集合）。§16 的「缺省 flag 默认 on」全局语义**不变**——只有本功能在调用点收紧：风暴当晚该 pack 在从未配置过 manager 的安装上靠默认值跑了起来。
- **关键词护栏**：`sources.watch_people` 为空、首项仍为示例占位 `your.manager`（大小写不敏感）、或派生的 first-name token 退化（<3 字符，或属停用词 {your, the, my}）时，本 pass 的 manager pack 直接停用并打一行日志（每进程一次）——**绝不**用退化关键词扫描：占位符派生的 "your" 会把几乎每篇英文笔记都当成 manager 会议记录。

**v0.14 修订（add-only 追记；随 release 记 CHANGELOG）**：manager pack ①（会后 action-item 清单）已**从产品整体移除**——占位配置退化的关键词一晚匹配了 92 篇历史笔记，酿成 backfill 风暴；这个概念将泛化为**按人承诺账本**从头重新设计（issue #23）。自此 `features.manager_pack` **被忽略**（无任何代码再据其门控），`state/meetings/` 与 `state/meetings_notice.sent` **不再写入**（存量文件不迁移、不删除）；本节上文 ① 的描述与 v0.14 各守卫补充仅作历史记录保留。**不在移除范围**：②（`act/oneonone.py` 1:1 准备页）与 `[MANAGER-OWES]` 账本行为不变；整 pass 的 `state/radar.lock` 互斥（保护的是整个 scan，不只该功能）与 `Config.default_target_repo_configured`（设置页「任务工作目录」与 executor 仍在用 `default_target_repo`）保留。

## 18. 定时任务归一（ingest 切换）
install.sh 重写用户 crontab 的 screenpipe 行 → 指向本 repo `ingest/` 内脚本，并在链尾追加 `&& python -m act.radar --once`（cron 有 FDA，radar 可读 ~/Documents）。Screenpipe-Export.command 改为调 repo 脚本（主窗口"立即导出"同源）。

2026-07-14 追加（add-only）：**vault-mirror 模式（claude TCC 身份隔离）**。事故：
claude CLI 改为分版本安装（`~/.local/share/claude/versions/X.Y.Z`），macOS TCC
按真实二进制路径记账 → 每次 CLI 升级都是新身份：GUI 每版重弹「访问 Documents」，
cron 无窗可弹直接 `EPERM`（07-09→07-13 截图→笔记链 38 连败）。契约：
- **唯一触碰 vault 的身份** = `vault-sync-helper`（`mac/VaultSyncHelper.swift`，
  build.sh 编进 app bundle `Contents/MacOS/`，与菜单栏 app 同 bundle id + 同
  稳定签名证书）——用户在权限体检页「笔记库访问」行做**一次** GUI 授权，此后
  跨 app / claude / python 升级永久有效；
- 链序（crontab 行不变）：export 开头 courier `pull`（vault → 精确镜像
  `state/vault-mirror/`，`--delete`；写 `state/vault_sync_mode` = mirror|direct）
  → export 产物写镜像 inbox → claude 对镜像执行 ingest skill → 成功后 courier
  `push`（全目录 `--update` 只增不删；inbox 删除走 **manifest**——pull 时记录
  的文件、镜像中已消失、且 vault 侧 mtime 未变才删，处理期间用户丢进 vault 的
  新文件绝不误删）；push 失败 → `state/vault-sync-push-pending` 标记，下轮
  **先重推后拉取**（宁可重复处理，绝不丢产出），且当轮链以失败上报；
- 读方（radar / weekly digest）走 `config.effective_obsidian_raw()`：mode 文件
  = mirror 且镜像 raw 目录存在 → 读镜像，否则读真 vault；
- **降级永远可用**：helper 缺失 / 未授权（exit 3）/ 非 mac → direct 模式 =
  本节原有行为逐字不变；mirror 是升级，不是前置条件。附带：ingest 的 claude
  调用加 watchdog（默认 7200s，`CLAUDE_MAX_SECONDS` 可调）。

## 19. 凭证与 secrets（跨组件契约，两侧逐字一致）

- **SECRETS 目录** = `<AIASSISTANT_HOME>/config/secrets/`，目录权限 **0700**、文件权限 **0600**（App 设置窗口写入方与 `act/lib/secrets.write_secret` 均强制）。gitignore：`config/secrets/`。
- **固定文件名**（各一行纯 token）：
  - `slack-user-token.txt`（xoxp-…）
  - `gmail-app-password.txt`（16 位应用专用密码）
  - `anthropic-api-key.txt`（sk-ant-…）
- **凭证解析顺序**（Python 读取方 `act/lib/secrets.resolve_credential(secret_name, explicit_path, legacy_default)`；shell 侧 ingest/process-screenpipe.sh 同顺序）：
  1. secrets 文件存在且非空 → 用其内容；
  2. config.yaml 显式路径（如 `sources.slack_token_path`、`sources.gmail.app_password_path`）→ 读该文件内容；
  3. 旧默认路径兜底（slack: `~/Desktop/Keys/slack-user-token.txt`；gmail: `~/Desktop/Keys/gmail-app-password.txt`；anthropic: `~/.config/anthropic-key.txt`）——**deprecated（v0.11 起，warn-only）**：走到这一级时 Python 侧在 stderr 打一行 deprecation 警告并记一条 `legacy_secret_path` analytics 事件（只含凭证文件名，永不含内容/路径外的信息），解析结果不变、永不 raise。理由：`~/Desktop` 在默认 macOS 上被 iCloud 同步。请迁移到第 1 级（App 设置窗口粘贴）。
  行为不变式：config/secrets/ 为空时一切照旧，Zelin 现有布置不断。
- **runtime python 指针** = `<AIASSISTANT_HOME>/config/runtime.json`，内容 `{"python": "<绝对路径>"}`。install.sh 生成（探测顺序：`$AIASSISTANT_PYTHON` env → `~/miniconda3/bin/python3`（存在且能 `import yaml`）→ `which python3`）；Swift 依赖检查用它跑 python 检查。
- **home 指针** = `~/Library/Application Support/ZelinAIAssistant/home.txt`，内容为 repo 根绝对路径（一行）。install.sh 写入，让 clone 到任意位置的 repo 对 GUI app 可见。**Mac app 的 repo 根解析顺序**（`AppPaths.stateRoot`）：① env var `AIASSISTANT_HOME` → ② home 指针文件（其指向的目录存在时）→ ③ 旧默认 `~/Projects/zelin-ai-assistant`。Python 侧不变（env var → 旧默认）：launchd plist（install.sh 渲染时注入）与 crontab 行都显式携带 `AIASSISTANT_HOME`，daemon 不读指针。
- app 侧只**写** secrets 文件（设置窗口粘贴保存），Python 侧只**读**；两侧不通过 secrets 之外的通道传递凭证；凭证内容永不打印/入日志。

---

# v0.10 additions（交付方式 + 交付收割）

## 20. delivery_mode（交付方式）

注册表 Requirement 顶层新增 `delivery_mode: "chat" | "repo"`——**缺失视为 `"repo"`**（registry.py 加载容错：缺失/非法值一律归一成 repo；YAML 只在值为 chat 时序列化，保存往返不丢）。

- **`chat` = 会话内交付成稿**：执行 agent 不为交付物创建/修改 repo 文件、不建分支、不开 PR；把最终可直接粘贴的完整成稿放进结束总结，单独一行 `FINAL DRAFT:` 之后跟全文。常驻升级条款：Zelin 后续说"定稿/存档/落盘/commit"（或同义）时，agent 才把当前最终稿写入 target_repo 合适路径、commit 到新 feature 分支并汇报分支名/文件路径；收到该指令前，草稿只在回复中迭代。
- **`repo` = 分支交付**（默认，维持现状）：有 remote → draft PR；无 remote → 分支 + 汇报分支名。

**execution 块新键**（actd 写，dashboard 投影为 epoch int）：
- `review_at`（ISO）——agent done、提升到待验收的时间
- `accepted_at`（ISO）——Zelin 验收归档（accept → delivered）的时间
- `delivered_summary`（str，≤500 字）——transcript 最后一条 assistant 消息的摘要（回执）
- `final_draft`（str，≤20000 字）——chat 模式结束总结里 `FINAL DRAFT:` 之后的全文；repo 模式/无标记时缺失
- `last_error` / `last_error_at`（str ≤300 字 / ISO）——派发失败留痕（status 停在 approved，下轮自动重试；重试成功后清除）

**收割函数**：`executor.harvest_delivery(session_id) -> {"delivered_summary": str|None, "final_draft": str|None}`——解析 transcript 最后一条 assistant 文本消息；有单独一行以 `FINAL DRAFT:` 开头则其后全文为 final_draft、之前部分为 delivered_summary；任何异常返回双 None、绝不抛。actd 在 review 提升处调用，收割失败不阻塞提升。

---

# v0.12 additions（merge-review：多选合并建议）

## 21. merge-review（多选卡片 → AI 合并建议 → 确定性执行）

看板多选 ≥2 张真实卡（待审批/运行中/待验收列）→ 请求 AI 分析这批卡该如何归并 → 建议卡展示结论与"接受后将执行"清单 → 接受时由 actd **确定性**执行（AI 的 `action_plan` 仅作展示解释，不驱动执行）。

**inbox 动作**（app 写，actd 消费；三个动作都不携带需求级 `id` 语义，不走 §3 的 req 查找）：

```json
{"action":"merge_review","ids":["R-xxx","R-yyy"]}     // ids ≥2；不合法（<2 / 有不存在的 id）→ actd log 后丢弃
{"action":"merge_apply","id":"<suggestion_id>"}        // 仅 status=done 的作业可执行；其余状态幂等 no-op + log
{"action":"merge_dismiss","id":"<suggestion_id>"}      // 作业标记 dismissed，即刻从 dashboard 消失（文件留到 TTL 清理）
```

**作业文件** `state/merge/<suggestion_id>.json`（`suggestion_id` = `"MS-"+8位随机hex`；actd 收到 merge_review 时创建为 `analyzing`；分析子进程 `python -m act.merge_review <suggestion_id>` 完成后**原子重写**——先写 .tmp 再 rename）：

```json
{
  "id": "MS-1a2b3c4d", "ids": ["R-xxx","R-yyy"], "requested_at": "<ISO>",
  "status": "analyzing" | "done" | "failed",
  "verdict": "merge" | "link_improvement" | "keep_separate" | "close_secondary",
  "primary": "R-xxx", "rationale": "…", "action_plan": ["…"],
  "confidence": "high" | "medium" | "low",
  "error": "…（failed 时，前 200 字）",
  "expires_at": "<ISO>（done/failed/dismissed 时 = 落状态时刻 +24h）"
}
```

`verdict?/primary?/rationale?/action_plan?/confidence?` 仅 done 时齐备；`merge_apply`/`merge_dismiss` 之后 status 改写为 `dismissed`（apply 成功另记 `applied_at`）——dismissed 不进 dashboard，文件留到 TTL 清理。

**verdict 枚举（AI 四选一）与 apply 的确定性语义**（actd `_apply_merge_verdict` 实现；`primary` 指定主卡，ids 里其余全部是副卡；merge/link_improvement/close_secondary 的 `primary` 必须 ∈ ids，否则分析判 failed）：

- `merge` = 副卡并入主卡：主卡 `sources` = 去重合并副卡 sources、`repeated_mentions` 累加、`notes` 追加 `[merged] R-yyy 并入：<副卡 delivered_summary 或 title 摘要>`；副卡活 session best-effort `executor.stop_session`（失败只记日志）；副卡状态置 **`merged`** + `merged_into=<primary>`；若主卡 `status==review` → 用 `executor.rework` 把"R-yyy 已并入，其交付物/worktree：<路径与摘要>"作为反馈注入主卡 session（主卡回 executing）；主卡其他状态 → 只落 notes（建议卡 action_plan 里如实说明）。
- `link_improvement` = 副卡挂为主卡的改进卡（`improvement_of=<primary>`），其余（状态/execution）不动。
- `keep_separate` = 保持独立；apply 等同 dismiss（不动任何注册表条目）。
- `close_secondary` = 副卡关闭进回收站：`registry.trash(副卡, "merged-review: 不再需要")`（可恢复，理由入 `trash_reason`）。

**`merged` 状态语义（registry 新终态，`State.MERGED`）**：可见性同回收站——不进任何看板列、purge 不删；但 `merge_or_new` **匹配语义同 delivered**——参与重述匹配以压住后续重复建卡（这点与 trashed 相反：trashed 的重述要重新出卡）。顶层 `merged_into` 字段记主卡 id。旧式 `merged_into:<父ID>` 状态字符串保留兼容，不参与本流程。

**分析子进程**（`act/merge_review.py`，CLI `python -m act.merge_review <suggestion_id>`）：读作业文件 → 对每个 id 收集材料（registry YAML 全文、`execution.delivered_summary`/`final_draft`、transcript 尾部 ~30 条 assistant/user 文本（复用 executor 的 transcript 定位方式：短 id glob `~/.claude/projects`）、worktree 的 `git log --oneline -5` + `git diff --stat`（cwd 从 transcript/execution 推，失败跳过））→ 组装 prompt（材料全部经 `sanitize.scrub` + `fence_untrusted`）→ headless `claude -p` 严格 JSON（timeout 300s，无工具）→ 校验 verdict/primary 合法 → 原子重写作业文件为 done（或 failed + error 前 200 字）。**任何异常必须落 failed，绝不留 analyzing 悬挂**。

**actd 侧**：收到 `merge_review` → 校验 ids ≥2（去重后）且都存在（不合法 → log 丢弃）→ 建 analyzing 作业文件 → `subprocess.Popen` 分离启动分析（不等待；stdout/err 落 `state/logs/<suggestion_id>.log`；启动失败立即置 failed）→ 打点 `merge_review_requested{n}`。每 pass 顺带（`cleanup_merge_jobs`）：`state/merge/` 里超过 `expires_at` 的 done/dismissed/failed 作业文件删除（expires_at 缺失/坏值用 requested_at 否则文件 mtime +24h 兜底；损坏文件直接删）；analyzing 超过 **20 分钟** 的置 failed(`"analysis timed out"`)。

**dashboard.json 新分区 `merge_suggestions`**（§2 的兄弟分区；Swift 侧 `decodeIfPresent` 向后兼容；analyzing/done/failed 都发，dismissed 不发；`requested_at` 输出 epoch int，同其余分区）：

```json
"merge_suggestions": [{
  "id":"MS-1a2b3c4d","ids":["R-xxx","R-yyy"],"status":"done",
  "verdict":"merge","primary":"R-xxx","rationale":"…","action_plan":["…"],
  "confidence":"high","error":null,"requested_at":1783367685
}]
```

**app 侧（概要）**：看板 header「选择」进入多选态；选中 ≥2 → 底部操作条「请求合并建议 (N)」写 `merge_review`；建议卡（紫 accent，待审批列顶）analyzing=spinner、done=结论+主副卡+rationale+**"接受后将执行"动作清单全文**+confidence 徽章+「接受」(`merge_apply`)/「取消」(`merge_dismiss`)、failed=橙色+error+仅「取消」；接受/取消乐观回显 180s 兜底。popover 只镜像显示建议卡（可接受/取消），不做多选。

**analytics**：`merge_review_requested{n}`（actd）、`merge_suggestion_done{verdict,confidence}`（分析子进程）；apply/dismiss 由 app 侧 `card_action` 自动覆盖。**追加（add-only）**：actd 侧确定性 apply 落地点补 `merge_apply{suggestion,verdict,outcome}`（`outcome=ok|fail`——`card_action` 只记录意图，apply 失败此前 telemetry 不可见；连点/迟到的 no-op 分支不打点，不算使用量）。

### 21bis. 强制合并 merge_force（v0.31，add-only）

"AI 建议合并"之外的**用户直断**路径：当用户确信这几张卡就是一回事、不想等 AI 分析、或**不认同** AI 判的 `keep_separate`/`link_improvement`/`close_secondary` 时，钦定主卡直接合并。语义**不新增**——就是 §21 `merge` verdict 那一档，只是 `primary` 由用户选、跳过 `claude -p` 与作业文件、即时落地。

**inbox 动作**（app 写，actd 消费；不携带 `id`/不建 MS- 作业）：

```json
{"action":"merge_force","ids":["R-xxx","R-yyy"],"primary":"R-xxx"}
// ids ≥2（去重后）且都存在；primary ∈ ids。不合法（<2 / 有不存在 id / primary∉ids）→ actd log 后丢弃
```

**actd 侧**（`_apply_merge_force`）：校验 ids（≥2、去重、都存在）+ primary ∈ ids → **复用 `_merge_into_primary(primary, secondaries)`**，与 AI `merge` verdict **逐字同一条确定性执行路径**（主卡 sources 去重合并 / repeated_mentions 累加 / notes 追加 `[merged]` / 副卡 `final_draft`·`delivered_summary` 搬到主卡 `execution.merged_deliverables`；副卡活 session best-effort `executor.stop_session`；副卡置 `merged` + `merged_into`；主卡 `status==review` 则 `executor.rework` 注入，其他状态只落 notes）。**无作业文件、无 claude、无等待**；执行失败只 log + 打点 `outcome=fail`，绝不抛穿轮询（用户可重试）。

**app 侧（概要，Mac + iOS）**：两个入口都走一个**确认弹窗**（`ForceMergeSheet`；因 `merged` 是终态、UI 不可撤销，必须让用户明确看到"哪张留、哪些被吸收"）——① **Mac 看板多选** ≥2 张 → 操作条「强制合并 (N)」（多选是 Mac 专属，iOS 无此入口）；② **Mac / iOS 的 AI 建议卡「仍然合并」覆盖按钮**，出现在 `verdict≠merge`（保持独立/挂改进卡/关副卡）**或分析 `failed`**（无 verdict）时——即 AI 没给出「合并」结论、而用户仍要合的场景；覆盖成功后顺手 `merge_dismiss` 掉这条被取代的建议。弹窗列出选中卡、让用户选主卡（默认第一张 / 建议卡的 primary）、一句大白话说明"副卡将停止运行、进入已合并（不可撤销），其来源/交付物保留在主卡"，确认才写 `merge_force`。乐观回显：**Mac** 涉及卡打「合并中…」角标，副卡落 `merged`（离开所在列）即清、180s 兜底；**iOS** 提交后刷新看板（建议卡随之更新/消失）。iOS 侧 `merge_apply`/`merge_dismiss`/`merge_force` 的 AEAD 明文由 `shared/InboxAction.swift` 的 `mergeApply`/`mergeDismiss`/`mergeForce` builder 生成，经 `syncd` 通用透传落 actd inbox（同 §31.1 手机上行路径）。

**analytics**：`merge_force{n,outcome}`（actd 落地点，`outcome=ok|fail`；仅计数与结果，不记卡片 id/内容——app 侧 `card_action` 另记意图）。

---

# v0.13.x additions（Claude Code 会话导入 — 空看板冷启动）

## 22. import_claude_sessions（一键导入 Claude Code 近期工作）

目标用户几乎一定已在用 Claude Code——首启看板为空时，最近的会话就是最便宜的种子。
`act/radar_claude_sessions.py` 扫描 `~/.claude/projects/<slug>/*.jsonl`（Claude Code 自己的
transcript 目录；`$CLAUDE_CONFIG_DIR` 可改根）。**一次性触发，非常驻**：只由 inbox 动作或
CLI 驱动，绝不定时跑。**全程本地、无 LLM 调用**——gist = 首条用户消息头 + 末条 assistant
消息头（截断）；每个文件只读 head/tail（会话可达数十 MB）。

**inbox 动作**（app 写，actd 消费；无需求级 `id`，不走 §3 的 req 查找）：

```json
{"action":"import_claude_sessions","session_ids":["<uuid>","…"],"window_days":7,"ts":"<ISO>"}
```

- `session_ids`（可选）：设置页勾选流——只导入这些会话（id = jsonl 文件名 stem，直接按
  `*/<id>.jsonl` 定位，不做全扫描；含 `/` 的 id 一律丢弃防路径穿越）。
- `session_ids` 缺失/空 + `window_days`（可选，默认 7）：导入窗口内全部
  「等你回复」（ended_waiting_on_user）会话——与设置页复选框的疲惫用户默认一致。

**落卡语义**（每个导入会话经 `registry.merge_or_new` 建普通提案卡）：
- 会话以 assistant 提问收尾（ended_waiting_on_user）→ `status=card_sent`（待审批）；
  仅仅是近期活动 → `status=detected`（欠账，v0.17 起展示为「备选/Backlog」）。
  与其他雷达的置信分流同构。
- `sources[0] = {who:"claude-code", channel:"claude_code", date:<last_activity 日期>,
  quote:<gist>, ref:<session_id>}`；`summary=gist`；`type=code`；`tier=T1`；
  会话 cwd 存在时作 `target_repo`。
- notes 带 `claude-code 导入 / imported from Claude Code session <短id>` 溯源标记。

**幂等 / 去重（双保险）**：① 状态标记 `state/claude_sessions_import.json`
（`{"imported": {<session_id>: <ISO>}}`，add-only）——scan 与 import 都跳过已导入 id；
② `merge_or_new` 的重述合并。另外**排除本产品自己派发的会话**（session_id 出现在任何
注册表条目的 `execution.session_id`/`aborted_session_id`）——自己的 agent 工作不得回流成新卡。

**CLI**（与 inbox 动作同一实现）：
- `python3 -m act.radar_claude_sessions --once --window 7`（导入等你回复的；`--all` = 全部）
- `python3 -m act.radar_claude_sessions --scan --window 7` — 只扫描，stdout 一行 JSON：
  `{"ok":true,"root":"…","candidates":[{session_id,session_file,project,project_dir,title,
  gist,last_activity,ended_waiting_on_user}]}`（等你回复的在前，组内新的在前，上限 100）；
  目录不存在时 `{"ok":false,"reason":"no_claude_dir","root":"…"}`。设置页「导入 Claude Code
  工作」区经 runtime python（§19 指针）调它渲染预览，勾选后写上面的 inbox 动作。

**analytics**：`claude_sessions_import{requested,imported}`（导入侧）。隐私：一切本地；
gist 只进注册表/看板，与其他雷达来源同等对待，永不上传。

---

# 安装生命周期 additions（install / uninstall）

## 23. `state/install_report.json`（install.sh 写，App / doctor 只读）

install.sh 每次完整跑完（交互模式与 `--pkg-postinstall` 模式皆是）在结尾写一份"这次安装实际做了什么"的机读报告（writer = `act/lib/install_report.py`，原子写：先写 `.json.tmp` 再 rename；写失败只 warn，永不打断安装）：

```json
{
  "version": "0.13.0",
  "generated_at": "2026-07-09T20:15:00Z",
  "mode": "pkg-postinstall",
  "user": "zelin",
  "steps": [
    {"name": "config", "status": "ok", "detail": "created from config.example.yaml"},
    {"name": "runtime_python", "status": "ok", "detail": "/usr/bin/python3"},
    {"name": "state_dirs", "status": "ok", "detail": null},
    {"name": "app", "status": "skipped", "detail": "installed by the .pkg"},
    {"name": "launchd", "status": "ok", "detail": "4 agents loaded"},
    {"name": "cron", "status": "ok", "detail": "ingest chain + digest + telemetry installed"}
  ],
  "agents_loaded": ["com.zelin.aiassistant.actd", "com.zelin.aiassistant.radar"]
}
```

- `mode` ∈ `"interactive" | "pkg-postinstall"`；`user` = 实际执行安装步骤的用户（pkg 路线下 = console user，postinstall 经 `launchctl asuser <uid> sudo -u <user>` 降权执行）。
- `steps[].status` ∈ `ok | warn | fail | skipped`（add-only：读方必须容忍未知值）；`detail` 为自由文本或 null。step 名与顺序不承诺稳定——读方按 `name` 查找、忽略不认识的行。
- `agents_loaded` = 本次成功 load 的 launchd label 列表。
- 消费方（只读）：App 首启界面据此逐条列出失败项（audit 1.4 的修复方向）、`act.doctor` 区分"装完即死"与"健康"。字段 add-only，不改不删。

---

# v0.14 additions（每周摘要：ingest → 回顾 + 自动化建议）

## 24. 每周摘要（weekly digest）

**目标**：把最近 7 天的 Obsidian ingest 产出（`sources.obsidian_raw` 下的 `*.md`，即 `2 - raw`）变成 ① 一张"本周你都在忙什么"回顾卡（进待验收）和 ② 2-3 张"这件事我可以帮你自动化"提案卡（进待审批）。实现：`act/weekly_digest.py`（headless `claude -p`，出站材料统一 `sanitize.scrub` + `fence_untrusted`）。

**config（add-only）** `sources.weekly_digest`：
```yaml
sources:
  weekly_digest:
    enabled: true   # 默认开；无 ingest 数据时任务自动跳过（不调 claude，零成本）
    day: 0          # 0=周一 .. 6=周日（python weekday()）
    hour: 9         # 当地时间小时（24h），到点后的第一个整点触发
```
overrides 允许列表新增扁平键 `weekly_digest_enabled`（bool，App 设置「每周摘要」开关即时写入；true = 产品默认，写 true 时直接删键）。`day`/`hour` 仅 config.yaml 可设。

**调度**：launchd agent `com.zelin.aiassistant.weeklydigest`（install.sh 同一模板渲染管线）每小时 :23 唤醒 `python -m act.weekly_digest`；模块自行闸门 —— enabled 关/未到 day+hour/6 天内已跑过 → 直接退出。因此改 config 的 day/hour **无需重载 plist**。状态标记 `state/weekly_digest.json`：`{"last_run":"YYYY-MM-DD","last_ingest_mtime":<float>}`（原子写）。

**成本护栏**（两级，均打 `weekly_digest_skip{reason}` analytics 事件 + log 一行）：窗口内零笔记 → `no_data` 跳过；有笔记但 mtime 都 ≤ `last_ingest_mtime` → `no_new_data` 跳过。两级都不调 claude。

**卡片语义**（都经 `registry.merge_or_new` 落账，source `channel="weekly-digest"`，同周重跑合并不重复建卡）：
- 回顾卡：title 含日期区间（每周新卡），`type=digest`、`tier=T0`、`delivery_mode=chat`、status=**review**；`execution.review_at`/`delivered_summary`(≤500)/`final_draft`(≤20000，全文) 每次生成都刷新，已 trashed 的不复活，其余状态一律拉回 review（新内容需要重新看）。验收 = 归档本周回顾。
- 建议卡：`type=automation`、`tier=T1`、status=**card_sent**（正常提案卡，批准后照常派发执行）；≤3 张/次。

**inbox 动作** `weekly_digest_now`（§10 全集成员；无 `id` 字段，App 设置「现在生成一份」按钮写入）：actd 收到后 `subprocess.Popen` 分离启动 `python -m act.weekly_digest --now`（stdout/err 追加 `state/weekly_digest.log`；启动失败只 log），打点 `weekly_digest_requested`。`--now` 跳过调度闸门与 `no_new_data` 护栏，但 `no_data`（零笔记）仍跳过并弹通知说明缘由。

**analytics**：`weekly_digest_generated{notes,suggestions}` / `weekly_digest_skip{reason}` / `weekly_digest_requested`（actd）+ app 侧 `weekly_digest_toggle{on}` / `weekly_digest_generate_now`。

---

# v0.14 additions（AI Doctor：错误分类 + 一键修复 + AI 修）

## 25. 失败分类层（failure_id 路由表）

**分类目录** = `act/lib/failures.py` 的 `FAILURES`：每个已知失败模式一个稳定 id →
`{plain_zh, plain_en, action_id}`。Swift 侧镜像在 `mac/Sources/Doctor.swift`
（FailureCatalog，`tests/test_failures.py` 防漂移）。id 集合 **add-only**：

`claude_cli_missing · claude_auth_failed · node_missing · engine_dead ·
agent_unloaded · cron_missing · cron_fda_blocked · dashboard_stale ·
config_invalid · network_error`

v0.14 录制健壮化追加（add-only）：`engine_npm_download`（首次 npx 下载中——
**进度而非错误**，UI 呈现 spinner 语气）· `engine_crashed`（进程死了且
engine.log 有真实输出，原文尾部随行展示）· `screen_tcc_lost`（曾授权过的
「屏幕录制」被 macOS 收回——系统更新/重装改变签名所致；app 侧以
UserDefaults `screenTCCWasGranted` 记住「曾授权」）。engine 死因判定逻辑 =
`failures.classify_engine_log(tail, npx_present, engine_alive)`，Swift 镜像
`RecordingController.diagnoseEngine`（两边同步改）。

action_id 词表（app 侧动作）：`install_claude · open_settings_key ·
install_node · restart_engine · reload_agent · repair_cron · grant_cron_fda ·
restart_actd · fix_config · retry`；v0.14 追加 `show_engine_log`（显示
~/.screenpipe/engine.log）· `regrant_screen`（打开 系统设置 → 屏幕录制）。

2026-07 追加（add-only）：failure id `claude_cli_outdated`——daemon（launchd/
cron）解析到**过旧的第二份 claude** 时的分类（2026-07-08 事故：/opt/homebrew/bin
的 2.1.16 在 launchd PATH 里排在 ~/.local/bin 的 2.1.206 前面，派发全数死在
`unknown option '--bg'` 并无限重试，通知只说「任务派发失败」）。分类签名
**刻意收窄**为派发依赖的 flag/子命令被拒（`unknown option
'--bg'/'--name'/'--resume'`、`unknown command 'agents'`）——泛化的 "unknown
option" 可能来自任务自身文本，绝不匹配。action_id 词表追加 `open_deps`（打开
依赖/诊断页——doctor 行点名两个二进制的具体路径与修法）。配套（同为 add-only）：
- install.sh 以**登录 shell** 解析 claude（`$SHELL -lc 'command -v claude'`，
  兜底 installer PATH → 常见安装位），其目录渲染进每个 launchd plist PATH 的
  **最前**（模板占位符 `/Users/YOURUSERNAME/.claude-bin`）与 §18 cron 链头的
  `export PATH=<dir>:$PATH`；install_report 新 step `claude_bin`。
- config **execution.claude_bin**（仅 config.yaml 可设，无 override 键）：显式
  钉死 claude 路径。运行时统一解析 = pin → PATH → `~/.local/bin/claude`
  （`config.resolve_claude_bin`；executor 全部 launch/roster/stop 调用点、
  radar/ask/merge_review/weekly_digest 都走它）。
- doctor 新检查 `daemon claude`：读**已安装** actd plist 的 PATH 解析 claude，
  与登录 shell 的比对——路径不同且版本不同，或 `--bg` 探测不被支持 → FAIL
  （failure_id=claude_cli_outdated）；plist 未安装 → WARN（诚实跳过）。

2026-07-13 追加（add-only）：failure id `engine_ffmpeg_missing`——「屏幕+音频」
（screen_audio）模式的引擎启动**强制依赖 ffmpeg**，缺失时 screenpipe 自带的
自动安装器不可靠（当日事故：安装器写出了二进制却仍每次报 `os error 2` 后秒退，
引擎反复暴毙，而菜单栏把死因猜成「屏幕录制」权限）。分类**只在引擎日志语境**
（`classify_engine_log` / Swift `diagnoseEngine` 的死引擎分支）做直接子串检测
（`_FFMPEG_INSTALL_FAILED`：`failed to install ffmpeg:`（冒号钉死 screenpipe
格式）/ `ffmpeg not found and installation failed`）——**刻意不进通用
`classify()` 规则链**：派发/卡片文本里的 `failed to install ffmpeg-python`
或聊到 ffmpeg 的散文绝不触发；安装错误自带网络/401 字样时仍归 ffmpeg（修法
是手动装）；活引擎带旧错误尾 = 健康，活引擎带 npm banner = 重新下载中
（banner 语义优先，两侧镜像一致）。action_id 词表追加 `install_ffmpeg`（打开
ffmpeg 下载页；目录句子自带 `brew install ffmpeg`）。配套行为（app 侧，同为
add-only）：
- 切到 screen_audio **先预检 ffmpeg**（登录 shell 依次**执行**
  `ffmpeg -version` / `~/.local/bin/ffmpeg -version` /
  `/opt/homebrew/bin/ffmpeg -version`——执行而非 `test -x`：安装器的残留
  文件不证明能跑；**无缓存**：刚 brew 完的用户不能被旧值误拒）——缺失则拒绝
  切换并解释，**绝不为一次注定失败的切换 pkill 正在跑的引擎**；预检回调
  校验模式未被用户改动（stale click 丢弃）；
- 模式切换失败**自动回滚**到原模式（一次、不递归；`applyMode(rollbackTo:)`）。
  切换路径带**慢死观察**：+0.5s 乐观发布后持有 `applying` 至 ~8s 复核
  （事故引擎 spawn 后 ~4-5s 才死，而存活 pgrep 从 t=0 就匹配 npx wrapper，
  单次 +0.5s 检查看不见慢死）；回滚回写前校验用户没有换新模式（新选择
  绝不被 clobber，错过的选择在收尾补跑一轮 applyMode）；
- 拒绝/回滚的解释走 `recordingNote`（15s transient，录制页 + 菜单栏菜单
  顶部各一行）+ 系统通知（通知未授权时静默丢弃，note 是兜底）；通知正文
  自足（不复用为行内 doctor 行写的目录句——那些句子会跟回滚后的现实矛盾）；
- 菜单栏「未在录制」行按 `diagnoseEngine` 分类显示**真实死因**（权限行仍在，
  但只在 CGPreflight 真报缺权限时出现），不再无条件猜「多半缺权限」；录制页
  的 ffmpeg 诊断行给「安装 ffmpeg」+「装好了，重启引擎」两个动作（死引擎的
  日志尾在装好后仍是旧错误行，就地重启是该页唯一的复活路径）。

**dashboard.json 新字段**（全部 optional，Swift `decodeIfPresent`；原始错误文本
字段不变，分类 id 只是伴随）：
- `running[]` queued 项加 `dispatch_error_id`（str|null）= `failures.classify(dispatch_error)`
- `running[]` 常规项 / review-active 项加 `last_error_id`（str|null）
- 未匹配任何规则时为 null —— app 显示原文 + 「让 AI 修」兜底，绝不硬凑分类。

**doctor 机器输出**：`python3 -m act.doctor --json [--fast]` →
`{"home": str, "checks": [{name, status(ok|warn|fail), detail, fix,
failure_id, action_id}]}`；exit code 仍 = FAIL 数。app 诊断区渲染 non-ok 行：
人话句子（FailureCatalog）+ 对症按钮；raw detail/fix 收进 tooltip 与「完整报告」。
app 在依赖检查发现关键失败（npx/claude/PyYAML/cron_fda/引擎在录制模式下死亡）时
**每次会话自动跑一次** `--fast` 版（零成本，不打真实 claude 调用）。

**state/cron_probe.json**（cron FDA 探针 —— cron 链写，doctor/app 读）：

```json
{"ts":"2026-07-09T18:30:00Z","protected_path":"/Users/x/Documents/Obsidian Vault/1 - unprocessed","read_ok":true}
```

- 写入方 = `ingest/screenpipe-export.sh`，**仅当** `AIASSISTANT_CRON=1`（install.sh
  §18 的 cron 行注入该 env）——app 内手动「立即导出」用的是 app 自己的磁盘授权，
  写探针会造假，因此不写。原子写（.tmp + mv），任何失败不影响导出链。
- `read_ok` = 该 cron 进程对导出目标目录（`obsidian_unprocessed` 解析结果）的真实
  `ls` 结果。vault 不在受保护路径下时 read_ok 恒 true —— 语义是「cron 能否读到它
  要写的地方」，不是「是否授了 FDA」本身，诚实优先。
- 读取方：doctor `cron disk access` 检查（新鲜≤2h 且 read_ok=false → FAIL
  `cron_fda_blocked`；文件缺失/过期 → WARN）；app 依赖检查「定时任务磁盘权限」行
  （按钮 = 复制 `/usr/sbin/cron` + 打开 FDA 面板 + 行内 click-by-click 步骤）。

**「让 AI 修 / Fix with AI」**（`act/ai_fix.py`，app 按钮 = runtime python
`-m act.ai_fix --open [--context-file …]`）：生成 `$TMPDIR/zelin-ai-fix-<ts>.command`
（诊断包 = doctor --fast 报告 + actd/launchd/cron/engine 日志尾部，先过
`sanitize.scrub` 再写盘），`open` 交给 Terminal 里的交互式 claude（**不带**
`--dangerously-skip-permissions`，改动必须经用户确认）；prompt 要求结束时给出
预填好的 GitHub new-issue URL（标题+脱敏正文）。config.yaml
`doctor.ai_fix_enabled: false` 关闭整条路径（CLI exit 2，app 按钮隐藏）。
安全姿态同时写在生成文件的头部注释里。

**§5 通知文案 v0.14 补充（add-only，语义不变）**：python 侧全部通知/手机镜像文案
经 `act/lib/failures.pick(zh, en)` 走 §15 的 UI 语言设置（`language` override），
且每条 body 必带下一步动作（audit Theme 11：「需要人工处理」式句子废止）。
builder 全集在 `act/lib/notify.py`：`msg_new_card / msg_done / msg_needs_input /
msg_auth / msg_review_ready / msg_dispatch_failed / msg_resuming /
msg_auto_resume_exhausted`。

---

# v0.14 additions（应用内更新检查）

## 26. update_available（应用内更新检查）

**目标**：装了就不该永远停在旧版（audit 9.1）。检查器 = `act/lib/update_check.py`；
actd 每 pass 顺带调用（缓存命中 = 零网络、零成本），把结果投影进 dashboard.json。

**检查语义**（`update_check.check()`）：
- 数据源 = GitHub releases API
  `https://api.github.com/repos/Wan-ZL/zelin-ai-assistant/releases/latest`（无鉴权；
  `/latest` 端点天然只返回非 draft、非 prerelease 的最新 release）。
- **至多每 24h 一次网络请求**，失败（离线/限流/坏响应）也计入 24h 预算——绝不重试
  风暴；失败 = 静默保留缓存（诚实：宁可晚一天知道新版，不做假新鲜度）。
- 携带 `If-None-Match` ETag；304 = 缓存仍新鲜，只刷新 `checked_at`。
- 请求暴露的信息只有：你的 IP + User-Agent 里的当前版本号
  （`zelin-ai-assistant/<version> (update-check)`），别无其他
  （docs/TELEMETRY.md「更新检查」节）。
- 版本比较 = 语义化版本（`v` 前缀容忍；同版本号 prerelease < 正式版）；当前版本
  真源 = `act.__version__`。

**状态缓存** `state/update_check.json`（update_check 独占读写，原子写 .tmp+rename）：

```json
{"checked_at":"2026-07-09T18:30:00Z","etag":"W/\"abc\"","latest":"0.14.0",
 "url":"https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.14.0",
 "pkg_asset_url":"https://github.com/…/ZelinAIAssistant-v0.14.0.pkg"}
```

`pkg_asset_url` = release assets 里第一个 `*.pkg` 的下载地址；没有 .pkg 资产时为
null（App 一律只打开 `url` release 页，pkg 地址仅供展示/未来使用）。

**config（add-only）**：

```yaml
updates:
  check_enabled: true   # 默认开；关掉 = 完全不发网络请求（缓存的旧结果也不再投影）
```

overrides 允许列表（`act/lib/config.py` `_OVERRIDE_FIELDS`）新增扁平键
`updates_check_enabled`（bool，App 设置开关按 §15.3 v0.14 diff-write 语义写入）。

**dashboard.json 新顶层 optional 字段 `update_available`**（§2 的兄弟字段；Swift
`decodeIfPresent` 向后兼容；**仅当** 开关开启 **且** latest 语义化版本 > 当前版本
时出现，否则整个字段缺席——缺席 = 没有已知新版）：

```json
"update_available": {"current":"0.13.0","latest":"0.14.0",
  "url":"https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.14.0",
  "pkg_asset_url":"https://github.com/…/ZelinAIAssistant-v0.14.0.pkg"}
```

**App 侧（概要）**：状态栏右键菜单低调一行 + 关于页一行
「新版本 v0.x.y 可用 — 下载安装包」；点击**只打开 release 页**（未签名 .pkg +
信任诚实：绝不自动下载执行）。关于页同行附一句提醒：设置与任务数据都在本机、
升级后原样保留；初始设置向导若需再次出现会预填当前值（§15 v0.14 幂等性条款）。

**§26 add-only：手动「立即检查」CLI**（关于页按钮；actd 周期路径不变）：

`python3 -m act.lib.update_check [--force]` → stdout 一行 JSON：

```json
{"ok":true,"enabled":true,"current":"0.15.0","latest":"0.15.0",
 "update_available":false,"url":"https://github.com/…/releases/tag/v0.15.0",
 "pkg_asset_url":null,"checked_at":"2026-07-10T18:30:00Z"}
```

- 无 `--force` = 与 actd 同语义（24h 预算内缓存命中 = 零网络）。
- `--force` = 跳过 24h 预算立即请求一次（**仅**用户点击触发；仍带
  `If-None-Match`，304 = 缓存仍最新，同样是一次新鲜的成功答案）；成功与失败都
  照旧刷新 `checked_at`——周期预算随手动检查重置，之后 24h 内 actd 不再发请求。
- `updates.check_enabled: false` 时 `--force` 也**绝不**发网络请求
  （`ok=true, enabled=false`，隐私开关高于按钮）。
- 传输失败：`ok=false, "error":"network"`，缓存原样保留、预算照常消耗（老规则
  不变——只是 CLI 把失败如实告诉界面，不再只能静默）。
- 关于页改为**常驻**一行更新状态（新版本可下载 / 已是最新 + 上次检查时间 /
  尚未检查过 / 检查失败）+「立即检查」按钮（客户端 ~10s 防连点）；`checked_at`
  由 App **只读** `state/update_check.json` 取得——写入方仍然只有
  update_check.py。state 文件字段与 dashboard 投影**均无新增**。

---

# v0.14 additions（问问助手：in-app Q&A）

## 27. 问问助手（Ask）— `state/ask_history.json` + `python3 -m act.ask`

**目标**：主窗口里一个提问框——用户问任何"这个产品怎么了/怎么用"的问题
（"为什么没有新卡片？""怎么换录制模式？"），得到一段基于**产品真实文档 +
用户真实状态**的大白话回答。Terminal 永不出现。实现：`act/ask.py`。

**调用面**（App 直接经 runtime python（§19 指针）同步调用，同 §22 扫描先例——
不走 inbox，不经 actd）：`python3 -m act.ask "<question>"` → stdout 一行 JSON：

```json
{"ok":true,"answer":"…","citation":"docs/INSTALL.md · 安装","lang":"zh","elapsed_s":12.4}
{"ok":false,"error":"…原文…","failure_id":"claude_auth_failed"|null,"timeout":false,"elapsed_s":60.0}
```

- `citation` = 回答依据的文档/小节（str|null，模型给不出就是 null，App 隐藏该行）；
- `failure_id` = `failures.classify()` 结果（§25 词表，未匹配 = null，App 显示
  原文 + 重试按钮，绝不硬凑）；`timeout` = 60s 超时（`ASK_TIMEOUT`）。

**上下文 bundle（出站前整体 `sanitize.scrub`；密钥/凭证值在组装阶段就进不来——
只有存在与否的布尔）**：docs 索引 + 问题相关文档节选（本地关键词匹配
`docs/*.md + HANDOFF.md + README`，无 LLM 调用）+ 白名单化 effective config
摘要（语言/通道/features/路径/阈值 + 凭证 present 布尔；**绝不含**
config/secrets 内容、token、gmail 地址）+ `doctor --fast` 报告 + dashboard
headline counts。随后 ONE 次 tool-less `claude -p`（同 merge_review 的
无工具判断调用），prompt 要求：≤150 词、用 §15 的 UI 语言、给出 citation、
bundle 里没有答案时固定回答"我不确定——可以去 GitHub Discussions 问"（不猜）。

**config（add-only）** 顶层 `ask: {enabled: true}`——默认开；false 时 CLI exit 2、
App 隐藏提问框。仅 config.yaml 可设（同 §24 day/hour 先例，无 override 键）。

**`state/ask_history.json`**（python 写方，原子写 .tmp+rename；App 只读渲染）：

```json
{"entries":[{"q":"…","a":"…","citation":"…"|null,"lang":"zh","ts":"<ISO>","elapsed_s":12.4}]}
```

最新在前，上限 **20** 条（`HISTORY_CAP`）。损坏/缺失 = 空历史，永不阻塞提问。

**analytics**（docs/TELEMETRY.md）：`ask_answered{ok,elapsed_s,failure_id?}`
（python）+ App 侧 `ask_submit` / `ask_feedback{verdict:"up"|"down"}`。
**问题原文只在 telemetry level=detailed 时**作为 `question`（≤200 字符）字段
写入这三个事件；basic 级 emit 端 gate——字段根本不写入本地 events.jsonl。

**App 侧（概要）**：主窗口新 sidebar 页「问问助手 / Ask」（`MainSection.ask`，
mac/Sources/Ask.swift）：输入框 + 思考态（spinner + 已耗秒数，可取消，绝不阻塞
UI）+ 答案卡（citation 行 + 👍/👎）+ 分类失败行（§25 人话 + 对症按钮 + 重试）+
历史列表。无 AI 引擎时复用向导的 EngineDetector 显示「AI 引擎未连接」引导态。

# v0.14 additions（通知身份中继）

## 28. 通知中继队列 — `state/notify_queue/`（python 写，App 消费即删）

**目标**：python daemons 的系统通知以 **Zelin's AI Assistant** 的身份/图标弹出，
不再是 osascript 的 Script Editor 身份。实现：`act/lib/notify.py`（写方）
+ `mac/Sources/NotifyRelay.swift`（消费方）。§5 的通知语义、文案与 §13 手机镜像
均不变——只换 native 弹出通道。

**无兜底（owner 拍板 2026-07-10）**：中继是**唯一** native 通知路径，无开关、无
osascript 降级——「app 没开时就不要消息通知了」「不喜欢 Script Editor 的方式」。
所以：**native 通知需要 App 在跑**；App 自 e02cd1f 起默认登录自启，在跑即常态。
App 长期关着时 native 通知静默丢弃（§13 手机镜像照常送达）。notify.py 里
osascript 只剩 radar_imessage 的 iMessage 发送用途（无关，保留）；
`platform.notify_user` 的 darwin osascript 实现保留为 OS seam（docs/PORTING.md），
但 darwin 上无调用方。非 darwin 平台不走队列（App 是 darwin-only），维持
platform.notify_user 原路径（notify-send 无身份问题）。

**队列文件**（每条通知一个文件 `state/notify_queue/<id>.json`；原子写
`<id>.json.tmp` + rename——消费方只认 `.json` 后缀，永远看不到半成品）：

```json
{"id":"<uuid hex>","title":"…","body":"…","subtitle":"…"?,"created_at":<epoch int>}
```

`subtitle` optional；`created_at` = 写入时刻 epoch 秒（同 §21 epoch int 先例）。
add-only：未来字段（如 action hint）只增不改，消费方对未知字段视而不见。
写方每次写入前顺手清扫 mtime 距今 > **10 min** 的旧条目（App 永不运行时目录
不至于无限增长）；队列目录不可写等任何失败 = 该条 native 通知丢弃（返回 False，
不降级）。

**消费方（App）**：5 秒 refresh tick（同 dashboard.json 的节奏）扫描目录。
`created_at` 距今 > **10 min** 的过期文件删而不弹（stale storm guard——关 App
期间的积压不准在下次启动时轰炸用户）；剩余按 `created_at` 升序经
UNUserNotificationCenter 弹出（identifier = `id`），单轮最多 **5** 条
（burst cap），超出部分**只弹一条**「还有 N 条通知 / +N more notifications」
汇总（正文指向打开 App 看板）。无论逐条还是进汇总，本轮扫到的文件**全部消费即删**
（队列常空）。损坏文件 log + 删（留着会每 5 秒重复 log）。通知权限未授予时 UN
add 静默 no-op、文件照删——权限真相在权限体检页，队列不负责重试。点击通知 =
打开主窗口（§5 文案本来就都指向「打开 App」；osascript 旧路径从无点击行为，
无保真负担）。

# v0.17 additions（建议上报：用户 → 维护者反馈通道）

> **车道更名（v0.17，纯展示层）**：原「欠账/debt」车道在 UI 上更名为
> 「备选/Backlog」（双语 `L("备选 · backlog", "Backlog")`）。只是展示层改名：
> registry `status=detected` 与 dashboard.json 的 `debt` key **一律不变**
> （§6/§8/§22 等处「欠账」按此括注理解）。

## 29. feedback（建议上报）— inbox 动作 + `state/feedback/<uuid>.json` + 上传

**目标**：Zelin 在 App 里对某张（或某几张、或不针对任何卡）提意见，一个动作直达
维护者——本地永久留档 + best-effort 上传，用户零等待、绝不因网络丢报告。
实现：`act/actd.py`（inbox 校验/路由）+ `act/lib/feedback.py`（落盘 + 上传）。

**inbox 动作**（App 写 `state/inbox/<uuid>.json`，actd 读后删——同 §3/§10）：

```json
{"action":"feedback","ids":["R-032","MS-ab12cd34"],"text":"这卡张冠李戴了","ts":"<ISO8601>"}
```

- 无 requirement 级 `id` 字段（同 capture / weekly_digest_now 先例）。
- `text` **必填非空**（strip 后为空 = log 丢弃整条）；落盘截断 4000 字符。
- `ids` 可缺失/可空数组/可含垃圾——**坏 ids 容错**：非法条目降级为
  `kind:"unknown"` 快照，绝不因此丢掉 text；数组去重、逐项转字符串。

**本地记录**（`state/feedback/<uuid>.json`，原子写 `.tmp` + rename，**永久保留**）：

```json
{
  "id": "<uuid hex>",
  "ts": "<UTC ISO>",
  "ids": ["R-032", "MS-ab12cd34"],
  "cards": [
    {"id":"R-032","kind":"requirement","type":"other","title":"<报告时刻标题快照>","status":"delivered"},
    {"id":"MS-ab12cd34","kind":"merge_suggestion","type":"merge_suggestion","title":"merge suggestion: R-001 + R-002","status":"done"}
  ],
  "text": "<用户原文>",
  "app_version": "0.16.0",
  "uploaded": null,
  "upload_attempts": 0
}
```

- `cards` = 每个 id 的**报告时刻快照**（类型 + 标题 + 状态）——卡片之后被改名/
  合并/清理，报告仍可读。R- id 查注册表；MS- id 查 `state/merge/` 作业（标题由
  成员卡 id 合成）；查不到 = `kind:"unknown"`、`title:null`。
- `uploaded` 三态：`null` = 待重试（pending）、`true` = 已上传（附
  `uploaded_at`）、`false` = 已放弃（附 `upload_error` 前 200 字）。

**上传（best-effort）**：复用 telemetry 的 **anon INSERT 通道**（docs/TELEMETRY.md
/ `act/lib/analytics_sync.py` 约定）：PostgREST `POST {supabase_url}/rest/v1/
analytics_events`，key 解析同序（§19 key 文件 → `telemetry.key_path` → 内置
publishable key，RLS 仅 INSERT）。**不建新表**——anon 的 INSERT policy 只覆盖
`analytics_events`，feedback 作为**独立事件类型**落同表：`event="feedback"`、
`source="feedback"`、`props` = 本地记录内容（id/ts/ids/cards/text/app_version，
不含 upload 簿记）、`client_ts` = 记录 ts。

**重试语义**（全部 best-effort，任何失败静默、绝不打断 daemon pass）：
1. 落盘后**立即尝试一次**（inline，10s 超时封顶）；
2. 失败 → 记录留在本地（`uploaded:null`，`upload_attempts:1`）；
3. 下一轮 actd pass（`run_once` 的 housekeeping 段）对所有 pending 记录
   **再试一次**；再失败 → `uploaded:false` **永久放弃**（文件保留，之后每轮
   sweep 直接跳过——terminal 态，成本 O(目录扫描)）。

**明确拍板（与 telemetry 的关键差异）**：
- feedback 是**用户显式动作**（点了「上报」就是同意发送），因此上传**不受
  `telemetry.enabled` 开关限制**，也**不看首启 consent 门**
  （`state/telemetry_consent_shown`）——关了匿名统计仍能上报建议。
- 仍尊重 fork 硬关开关：`telemetry.supabase_url` 为空 = 无处可发，记录只留
  本地并立即置 `uploaded:false`（`upload_error:"uploads disabled …"`）。
- **内容含卡片标题快照与用户原文**，可能含敏感词——发送即用户自担（区别于
  telemetry basic 级的"只有元数据"承诺）。App 侧上报入口文案须明示这一点。
- 本地 analytics 事件（`inbox_feedback`）只记元数据（ids 数量 + 上传结果），
  **text 绝不进 events.jsonl**——报告原文只经 feedback 自己的通道走。

---

# v0.17.2 additions（attach ≠ 打回：review 卡会话活动的诚实投影）

## 30. review 卡的会话活动（`session_active`）与返工轮的区分

**背景（2026-07 生产实况）**：v0.17.1 起双击卡片即 `claude attach` 回原会话，
owner 常在待验收卡上 attach 提问/聊天。此前 dashboard 把「status=review +
roster 上该 session 正在 working」投影成 running[] 的 `state="review-active"`，
App 显示「验收后返工中」——但没有任何打回 verdict 发生过，这是误标。

**判别规则（语义拍板）**：真返工轮**只**从打回 verdict 开始（§10 `rework` /
§21 merge 注入，均走 `executor.rework`）。打回派发点写
`execution.rework_count`（int，累计打回次数）与 `execution.last_rework_at`
（UTC ISO）——§20 execution 块此前未列出的既有键，此处补记（add-only）——并且
**同一调用内**把状态置回 `executing`。因此「status=review + session 正在
working」不可能是返工轮，只能是用户 attach / 会话自发活动。

- **dashboard 投影**：这类卡**留在 `review[]`**（不再挪去 running[]）；
  `review[]` 项新增 optional 字段 `session_active`（bool；Swift
  `decodeIfPresent`，缺失=false）。App 在待验收卡上显示平静徽章
  `L("会话有新活动", "Session active")`，验收/打回按钮照常可用；
  counts.review/running 跟随列表。
- **重新收割保持不变**：actd reconcile 见到 review 卡 session 转 working 时记
  内部标记 `execution._review_active`（下划线内部键，非投影字段），settle
  （done/缺席）时 `harvest_delivery` 刷新 `delivered_summary`/`final_draft`
  （非空才覆盖），blocked 保留标记等下一轮——终端对话可能产生新交付物，这是
  特性，保留。analytics 事件名 `review_active`/`review_reharvested` 不变。
- **真返工轮行为不变**：打回后卡回 `executing`，照常走 running[]
  （state="working"），done 后重新提升 review 并收割。
- **兼容性**：老 App + 新 actd —— review[] 未知字段被忽略，卡片留在待验收列
  （诚实降级）；新 App + 老 actd —— 仍可能收到 running[] 里
  `state="review-active"` 的行（该行形状只来自老 actd，add-only 不删），App
  徽章文案改为同语义的「会话有新活动」。

**v0.28.1 追加（add-only，投影修订）**：上面「留在 `review[]` 只标 `session_active`」
在生产暴露了一个盲区——owner 若 attach 回会话**启动了实打实的工作**（例：跑一整个
deep-research workflow，几十个子 agent、数分钟），看板 运行中 显示 0、而该 session
正烧算力,卡却静躺在待验收,与直觉冲突(被判为 bug)。修订:**`status=review` 且该
session 的 roster state ∈ 正在 working 时,dashboard 把该卡投影进 `running[]`**（`state="working"`、
新增 optional 字段 `from_review=true` 供 App 标注「已交付过·再运行」，同时携带
`delivered_summary`/`final_draft` 以免丢草稿）。**关键:这是纯投影改动——磁盘上
registry 状态仍是 `review`,不翻状态机**;因此不碰 auto-resume(review 卡不被
`reconcile_executing` 拉起)、验收/打回 verdict 与交付草稿全保留;session settle
（done/缺席/blocked）后该卡自然落回上文的 `review[]` 分支(§30 判别规则、`session_active`
徽章、`_review_active` 重新收割均不变)。§30 对「attach 活动 ≠ 返工轮」的语义判别**不变**
——`from_review` 卡明确标为 working、非 rework。配套:`stop_to_review` / `abort_execution`
的允许状态扩入 `review`（见 §10），使这类卡在 运行中 车道上的「停止」二选一（去待验收 /
退回提案）真正生效——此前 review 卡无任何 in-app 停止入口。兼容性:老 App 忽略
`from_review` 未知字段、卡仍显示在运行中(诚实降级);老 actd 不产生该投影,卡照旧留待验收。
**通知守卫**:`detect_transitions` 的 running→review「待验收:AI 已交付草稿」通知,当**上一轮 running 行带 `from_review`** 时跳过——这只是 re-run 落回、非新交付(main 上该卡从不离开 review[]、从不通知),否则 attach 会话每次 working↔idle 循环都会误报。真正的 executing→review 首次交付(上一轮 running 行无 `from_review`)照常通知。

# iOS 云同步 additions（Phase 1b — `syncd` + actd sync-safety，plan of record §5/§7.3）

## 31. `syncd` — headless 云同步守护进程（`python3 -m act.syncd`）

> **v0.30.0 supersession（add-only note；权威设计见 `docs/design/qr-only-capability-sync.md`）**：本节以下描述的 v1 认证模型（Supabase 账号/email OTP + `exchange_device_token` Edge Function + per-device JWT + `devices`/`device_secrets`/`device_heartbeats` 表）**已被 QR-only 能力模型取代**。原因:免费版发不了验证码、且项目已迁 ES256 无法自签 HS256。v2 要点(取代下文相应条目,其余"两文件契约/密文/launchd/`state/sync/`"不变):①每台 Mac 一个**稳定** `channel_id`(读能力)+ `write_secret`(写能力)+ E2E `K`,全在一张二维码里(`e2e.build_channel_qr`,主入口=Mac 设置「同步/配对」区,CLI `--pair [--json]` 兜底);②传输用 **anon/publishable key** + header `x-sync-channel`(每请求)/`x-sync-write`(仅写);③Supabase v2 三表 `channels`/`board_snapshots`/`inbox_actions` 按 `channel_id` 存,RLS 对 anon 放行:读要 `channel_id`(强制 header 过滤防遍历)、写要 `write_secret`(服务端 `sha256` 经**硬化的** SECURITY DEFINER `sync_write_ok` 核验:`search_path=''` + 全限定 `extensions.digest`/`public.channels`,并 revoke create on public);④无账号/无 email/无 edge function。安全姿态:二维码=该 Mac 看板的主钥匙。已在生产库端到端实测(读写门控 + 防遍历全通过)。

`syncd` 是既有「两文件契约」的**第二个 client**（与 Mac app 并列）：DOWN 读
`state/dashboard.json`、UP 写 `state/inbox/<action_id>.json`。它**从不 import
`actd`**、从不碰 registry；Supabase 全程只见 `act/lib/e2e.py` 产出的**密文**
（per-pairing 对称 AEAD，维护者读不到正文）。launchd plist
`act/launchd/com.zelin.aiassistant.syncd.plist`（KeepAlive）。

- **启动门（默认关，硬边界）**：进程启动第一件事是读 `state/sync.json`；文件不存在
  或 `mode != "cloud"` → **立即 `exit 0`**，在任何其他文件操作 / 任何网络之前。所以
  一次没 opt-in 的普通安装（哪怕 plist 已 load）**零网络**。开 = 写 `sync.json`
  `mode:"cloud"`；关 = `mode:"off"` 或删文件（完全回本地）。
- **鉴权（§3）**：headless 无 login session，拿 per-device secret（`config/secrets.json`
  的 `sync_device_secret` 优先，否则 `state/sync.json.device_secret`）POST
  `exchange_device_token` Edge Function 换 1h device-scoped JWT，缓存 + 到期前刷新。
  换取失败 → **暂停同步（不 crash、不影响 actd 本地写盘）**，写
  `state/sync/status.json` `{paused:true, reason:"云同步已暂停:请在 App 重新配对"}`
  并退避重试。
- **DOWN（§5.2）**：poll `dashboard.json` mtime（≤10s）→ 本地 sha256 change-gate
  （**hash 只在本地、绝不上传**）→ 变了就 bump `seq`（启动 seed =
  `max(server row seq, 本地 seq)+1`，同一设备下永不回退）→ `e2e.encrypt_board`
  原始 dashboard 字节 → UPSERT `board_snapshots`（on_conflict=device_id，device
  JWT）。把 blob 内嵌 nonce 镜像进 `nonce` 列（schema NOT NULL）。每 30s 心跳
  `device_heartbeats` 带 `last_pushed_seq`（揭穿「心跳活着但推送卡死」）。
- **UP（§5.3）**：poll `inbox_actions WHERE target_device_id=me AND
  status='pending'`（10s）→ 经 `delivered.jsonl` ledger 去重（同 action_id 两次 =
  一个 inbox 文件）→ `e2e.decrypt_action`（AEAD 认证，relay 无法伪造/改路由）→
  原子写 `state/inbox/<action_id>.json`（tmp+os.replace）→ PATCH 行 `delivered`。
- **ack-tail**：用字节游标 tail `state/sync/applied.jsonl`（actd 写，§32）→ PATCH 行
  `applied` + `result_status`（PATCH 失败则不前进游标、下轮重试）。
- **`state/sync/` 归 `syncd`**：`down_state.json`（`snapshot_seq` + change-gate
  hash）、`delivered.jsonl`（L3 去重）、`applied_cursor.json`（ack-tail 游标）、
  `status.json`（UI 可读的暂停原因）、`pairing_registration.json`（配对产物）。
- **网络全 best-effort**：任何 network 调用失败只 log、绝不 raise 进循环。

### `state/sync.json`（opt-in 门 + 路由；不存在 = 纯本地）
```json
{"mode":"cloud","device_id":"<sync-only uuid>","owner":"<auth.uid>","epoch":1,
 "platform":"macos","supabase_url":"https://…","apikey":"sb_publishable_…"}
```
`mode` ∈ `cloud` | `off`（缺失 = off）。`device_id` = **独立 sync-only UUID**
（`e2e.sync_device_id` → `state/sync_device_id`），**绝不复用** telemetry 的
`state/device_id`（§8-4：否则给 operator 去匿名化 telemetry）。可选 `edge_url`、
`device_secret`（也可放 `config/secrets.json`）。

### 配对 / consent CLI（Settings UI 调用）
- `python3 -m act.syncd --pair --label "公司 Mac" --supabase-url … --apikey … --owner …`
  ：mint sync device UUID + per-pairing key `K_i`（`e2e.new_pairing_key` /
  `save_pairing`）+ per-device secret（写 `config/secrets.json` 0600），写
  `state/sync.json`（mode=cloud，即 opt-in），产出 QR blob（`e2e.build_pairing_blob`，
  不透明、非 URL scheme）与 `state/sync/pairing_registration.json`（app/operator 用
  service_role 插 `devices` + `device_secrets` 行所需材料，含 argon2id 或待哈希 secret）。
- `python3 -m act.syncd --disable`：`mode:"off"`，回本地（保留密钥，重开无需重配对）。
- `python3 -m act.syncd --consent-text`：打印 §7.3 B 多设备同步诚实披露文案
  （`syncd.CONSENT_DISCLOSURE_ZH`，与「匿名使用统计」是两个独立开关）。

## 32. actd 的 sync-safety 改动（§5.4；macOS/Linux 同样运行，向后不回归）

1. **`state/sync/applied.jsonl` ack（每个终态一行）**：`process_inbox` 消费**任何**
   inbox 文件后都追加一行 `{"action_id":<文件名 stem>,"result_status":…,"ts":…}`
   —— 不只 apply 成功，连 guarded no-op、unknown-req drop、bad-JSON 也写
   （`result_status` ∈ `running`|`noop`|`unknown`|`bad_json`）。这样手机的
   badge：已提交→已送达→**已生效(`running`)/已是最新(`noop`)/该卡已不存在
   (`unknown`)**，全读 durable status，**绝不靠 inbox 文件消失推断 applied**
   （`actd.py` 无论结果都删文件）。本地 Mac app 的随机 action_id 不匹配任何云端行 →
   syncd PATCH 命中 0 行，无害。best-effort，绝不 raise 进 pass。
2. **`comment`/`raise`/`accept`/`rework` 收紧 status guard**：`_apply_decision`
   现读 inbox 文件里的 `expected_status`/`board_seq`（手机 tap 时钉入），
   `expected_status` 若与当前状态不符 = 幂等 no-op；且各自的固有前置态收紧为
   —— `comment` 仅 `card_sent`/`detected`、`raise` 仅 `detected`、`accept`/`rework`
   仅 `review`。防陈旧/重放动作撕走 running 卡 / 提前归档 / 重复返工。
   （`approve`/`done_external`/`abort_execution`/`revert_review`/`stop_to_review`/
   `defer`/`archive`/`unarchive` 早已有 guard，未改语义，只补 `result_status` 返回值。）
3. **inbox 文件名接受 `<action_id>.json`**：现有 `*.json` glob 已兼容，无需改动
   （`action_id` = 云端幂等键 = 文件名；文件内 `id` 仍是需求 id 如 `R-001`）。

### `state/inbox/<action_id>.json` 的 §5.4 附加字段（add-only，Mac app 不写、缺省即老行为）
```json
{"id":"R-001","action":"approve","comment":null,"ts":"…",
 "expected_status":"card_sent","board_seq":42}
```
- `expected_status`(str|absent)：手机看到该卡时的状态，actd 的 §5.4 guard 前置检查；
  缺省 = 不做 expected 检查（保持 Mac app 老行为）。
- `board_seq`(int|absent)：手机所见看板 revision（也进 `e2e` action AAD），provenance/
  staleness 信号；syncd 从 `inbox_actions.board_seq` 行值回填。

# v0.33.0 additions（车道展示层更名 + Mac 看板两条默认收起的书立条）

> **车道更名（v0.33.0，纯展示层）**：
> - 「储备/Backlog」→「**潜在任务/Backlog**」（EN 不变）
> - 「已验收/Done」→「**阶段性完成/Done for now**」
> - 归档区「归档/Archive」→「**永久性完成/Done for good**」；卡片按钮「归档/Archive」
>   →「永久完成/Done for good」；「取消归档/Unarchive」→「**放回看板/Put back**」；
>   归档行 badge「你归档/自动归档」→「你封存/自动封存 (You sealed/Auto-sealed)」
> - 提案卡 defer 按钮「入库/Backlog」（iOS/webui 旧名「存备选」）三端统一为
>   「**暂缓/Later**」；echo「入库中…」→「暂缓中…」
> - 提案/运行中/待验收 车道名与「验收/Accept」按钮不变
>
> 与 v0.17 的「欠账→备选」一样只改展示层，以下全部**冻结不变**：registry status 名
> （`detected`/`delivered`/`archived` 等）、dashboard keys（`debt`/`completed`/
> `archived[]`/`counts.archived`/`prev_status`/`archive_reason`）、inbox action 名
> （`defer`/`archive`/`unarchive`/`accept` 等）、notes 标签 `[deferred] 暂缓，入库`、
> analytics 事件名、triage prompt 的 `入库把关` 识别标记。

**Mac 看板两条书立条（display-only，无契约变化）**：

- 「潜在任务」列默认收起为 ~44pt 窄条（竖排标题 + 计数）；点窄条展开为正常 400pt
  列，点列头收起。看板最右**新增**「永久性完成」窄条——展开后 = popover 归档区同款
  内容（搜索框 + 归档行 + 放回看板），左右两条书立夹住五列工作流。
- 展开状态 session 内记忆（挂在 store 上，换页不丢）但**不持久化**——每次启动都收起。
  暂缓 echo / debt 车道 notice 到达时潜在任务条自动展开（用户点了按钮，回执不能落在
  看不见的列里）。
- 「永久性完成」条**仍不是看板列**：不进 `selectableIDs`/多选合并面，不参与
  lane-notice 路由（unarchive 仍走 info-strip 机制）。
- iOS 不变：仍是 5 页 pager，无归档 lane（`BoardLane` 不加 case）。

## 33. v0.33.1 审计加固 — add-only 修订与语义澄清

（本节为 v0.33.1 全仓审计批次的契约后果；除明确标注 supersedes 的条目外，均为对既有行为的收紧/澄清，不引入新的对外形状。）

- **§20 修订（chat 交付的文件型例外）**：`delivery_mode="chat"` 仍以 `FINAL DRAFT:`
  为强完成信号，但**文件型交付物**（HTML 页面、表格等不适合纯文本粘贴的产物）改为
  写入 workbench 下 `deliverables/` 的**绝对路径**文件，`FINAL DRAFT:` 之后跟该绝对
  路径 + 3–5 行纯文本摘要（保持非空，`_promote_if_delivered` 的判定不变）。
  所有交付模式新增统一规则：总结中提到的任何文件一律报绝对路径（执行会话隔离在
  `<target>/.claude/worktrees/` 内，相对路径对 owner 无意义）。harvest 侧：final
  draft 若恰为一个存在且可读的 `.html` 绝对路径，`final_draft` 从该文件回填
  （≤20000 字符），路径+摘要留在 `delivered_summary`——「复制成稿」仍复制成品。
- **§5.4/§32.2 ack 语义修订（supersedes「建议级动作一律 ack running」）**：所有
  动作按真实处置回执——被丢弃/校验失败的动作 ack `noop`，未知目标 ack
  `unknown`，坏文件 ack `bad_json`（文件删除，仅该文件终止）；rework 启动失败
  ack `noop`。§32.2 前置条件落地情况：`comment` 对 trashed/merged/rejected 卡
  no-op；`raise` 仅接受 detected/card_sent（card_sent 幂等重放为既定行为，测试
  锚定）；accept/rework 的宽松接受面为本意保留。
- **inbox 三重边界校验**：手机→syncd（非法形状拒收不落盘）、web→webui（400）、
  actd（字段 coercion + per-file 兜底）。字段类型契约：`action/id/comment/text/
  primary` 为 str-or-absent（null=absent），`ids` 为 list-of-str。
- **`board_snapshots.updated_at` 改为服务器时钟**（migration
  `20260715000000_board_snapshots_server_updated_at.sql`，BEFORE INSERT OR
  UPDATE trigger 统一打 `now()`）；syncd 不再发送该列。手机 Freshness 语义不变，
  但不再受 Mac 时钟偏移影响；手机侧另新增 seq 单调性检查（旧快照重放被忽略）。
- **手机动作新增 `expected_status` 自动钉扎**：iOS 对 comment/raise/accept/rework
  按其固有 lane 前置状态写入 `expected_status`（§32.2 guard 由此端到端生效）；
  缺省仍为不检查（Mac app 行为不变，向后兼容）。
- **registry 写入 fail-closed**：`save()` 对读不出/解析失败的既有文件拒绝写入并
  抛错（原为按空文件覆盖）；`next_id()` 将 `R-<n>.yaml` 文件名（active + archive）
  一并计入号段；archive/unarchive 半途残留由 `load()` 优先 archive 副本自愈。
- **v0.33.0 折叠条一节的修订（supersedes「永久性完成条不参与 lane-notice 路由」）**：
  自 v0.33.1 起「放回看板」的 info-strip 反馈与超时通知渲染于永久性完成条内部，
  且反馈到达时该条自动展开（与潜在任务条同一机制）；该条仍不进
  `selectableIDs`/多选合并面。看板搜索命中潜在任务时强制展开该条（仅视图态）。
- **配对 label 解析顺序**：`--label` 显式参数 → `state/sync.json` 既有 label →
  「这台 Mac」。打开设置页不再重置自定义 label。
- **digest / 1:1 prep 输出根**：显式配置了 `execution.default_target_repo` 才写入
  该 workbench，否则写 `state/digests/`、`state/oneonone/`；不再自动创建占位
  `~/Projects/your-workbench`。

# v0.34.0 additions（双输入框：运行中列直接开跑）

## 34. capture 的 `mode:"run"`（add-only；§10 capture 语义扩展）

在提案和运行中分别提供输入框，**用户在哪输入就进入哪个 slot**：提案列输入 =
今天的 capture（雷达 triage → 提案/备选，人批准才跑）；运行中列输入 = 直接开跑。

**inbox 形状（add-only）**：capture 文件新增可选键 `"mode"`（str）。

```json
{"action":"capture","text":"<用户一句话>","mode":"run","ts":"<ISO8601>"}
```

- `mode` 缺省/其它任何值（含非法类型）= 今天的行为不变（raising → triage →
  提案卡）——垃圾值绝不静默启动 agent（fail-safe 落提案路径）。syncd 的
  §33 入站形状闸门把 `mode` 纳入 str-or-absent 字段校验。
- `mode:"run"`：actd 用与普通 capture **同一条极简建卡路径**（title=原话截 80、
  channel=quick_capture、原话进 sources）经 `registry.merge_or_new` 落卡，然后
  把 pre-approval 形态（detected/card_sent/raising）**直接提升为 `approved`**
  （补记 `execution.approved_at`，与 approve 动作同一账目），下一轮
  `dispatch_approved` 照常派发。notes 打 `[direct-run] 用户直接开跑` 标签。
  执行会话的第一件事是自行分析上下文再干活；交付物仍落**待验收**由人验收，
  模糊的任务靠既有**需输入**机制自行澄清。
- **诚实声明：direct-run 跳过了 plan/费用预估的人审预览**——没有提案卡、没有
  cost 提示，任务直接进入派发队列。UI 文案不得暗示有预估。
- **处置表（按 text 命中什么，穷尽分支；治理原则：没有真的排上一轮运行就绝不
  ack `running`，被提升的卡绝不继承 repo 路由）**：
  - **没命中** → 新卡直接 approved，ack `running`；
  - **命中未结 pre-approval 卡（detected/card_sent/raising）** → 提升**那张卡**
    （不双开），提升时**强制改写路由**（见下），ack `running`；
  - **命中 approved/executing 卡** → 只并 sources，不重复排队——这单确实在
    队里/在跑，ack `running`，该卡自身路由**不动**（没有新派发）；
  - **命中 review（待验收）卡** → 只并 sources，**什么都没启动** → ack
    `noop`（假装 running 是审计红线的 silent fake success；Mac 占位卡为同一
    理由不对 review 行做清除匹配，180 s 超时条如实提示「可能命中了已有的卡」）；
  - **命中已交付/已合并（resolved）卡** → **强制走 §3.5 re-raise**
    （merge_or_new 的确定性增量门槛看不见"用户在运行框打字"这个 actionable
    信号，直接调 `reraise_or_followup(actionable=True)`；簇内已有未决
    follow-up 则并入它）→ 重开一轮按 pre-approval 规则提升；提升时把上一轮
    的 `execution.session_id` 归档为 `reraised_session_id` 并删除（否则
    dispatch_approved 把它当 "already dispatched" 跳过，新一轮永远不派发），
    同时删 `execution.done`；canonical dead-end（rejected/trashed/archived
    主卡）则重新开新卡。ack `running`。
  空/非法 `text` 按 §5.4 诚实 ack `noop`。
- **交付强制（无 LLM 路由，钦定设计）**：**任何被 direct-run 提升为 approved
  的卡（新卡、命中提升、re-raise 重开一轮）一律强制 chat 交付 + 默认
  workbench，不进任何 repo**——显式写 `delivery_mode="chat"`、`target_repo`
  清空（派发回退默认 workbench）。命中的卡带着 LLM 选过的 repo 路由也一样被
  改写（notes 追加 `[direct-run] 交付改为 chat（跳过预览，不动 repo）`）：
  没有人审过预览，不得在任何 repo 里建分支/开 PR。chat 交付的 `FINAL DRAFT:`
  （或 §33 的 deliverables/ 文件例外）照常被收割进待验收。唯一不改路由的
  分支是 approved/executing 折叠（上表）——那两种不产生新派发。
- **analytics**：actd 落地点新增 `capture_direct_run`（req/status/chars +
  capture_input 门控的 text，形制同 `inbox_capture`）；App 侧 `capture_submit`
  / `composer_open` 增加 add-only 字段 `mode:"run"`（source/trigger 词表不变）。
- **Mac UI**：运行中列顶常驻 mode=.run 的 KanbanComposer（看板列 + popover
  运行中区各一，placeholder「一句话，直接开跑（跳过提案）…」）；乐观回显 =
  运行中列顶的灰色排队占位卡（复用 capture placeholder 机制，只对 running/
  needs_input 行做归一匹配清除——**刻意不对 review 行清除**：命中旧待验收卡
  时 actd ack 的是 noop，占位卡若被一张一周前的 review 卡清掉就是视觉上的
  fake launch；pipeline 不健康时诚实显示「已保存到队列」，180 s 未确认→橙色
  超时条「任务没有开始——可能这句话命中了已有的卡（看看待验收/提案），或后台
  没在跑」）。⌘L 仍只归提案 composer。
- **iOS**：Running lane 页顶同款 QuickCapture 变体（directRun），走
  `shared/InboxAction.capture(text:mode:)`（additive key，sortedKeys 编码不变）
  经 syncd 通用透传落 actd inbox。
- **webui**：本期不加运行中输入框（web 端 capture 仍只有提案路径）。

## 35. v0.35.0 设备名称（add-only）

- **`dashboard.json` 新增可选顶层 `device_label`**（§2 的兄弟字段，同
  `update_available` 的加法约定）：这台 Mac 的用户自定义设备名，取自
  `state/sync.json` 的 `label`（与配对二维码携带的 label 同源）。未配对 / 无
  label / 文件不可读时**整个键缺失**（不是 null）。旧 app 忽略该键
  （`decodeIfPresent`），旧 payload 照常解码。
- **Mac 设置页提供可编辑的「设备名称」输入框**（设置 · 同步/配对；默认 = 系统
  电脑名，≤64 字符）。提交即以 `--pair --label <新名>` 重跑既有配对路径——
  `init_channel` 幂等，channel_id/密钥/epoch 稳定，仅二维码尾部 label 字节与
  `state/sync.json` 变化，二维码即时重渲染。label 解析顺序不变（§33）：显式
  `--label` → `state/sync.json` 既有 label → 「这台 Mac」。
- **已配对手机无需重新扫码**：iOS 解码看板后，若 `device_label` 非空且与该
  channel 本地 label 不同，更新内存 + Keychain 中的 label（改名经由既有 E2E
  看板通道送达；服务器 `channels.label_enc` 仍是 INSERT-only 死角，不参与）。
  重新扫码路径不变（`addChannel` 照旧覆盖 label）。

## 36. v0.36.0 实时字幕（add-only，Mac 展示层）

实时字幕是**纯 Mac 本机展示层功能**，对既有契约零改动，本节只登记新增面：

- **不碰录制状态机**：`recordingMode` 词表仍冻结为 `"off"|"screen"|"screen_audio"`
  （§15）；实时字幕是独立的 UserDefaults Bool（`liveCaptionsEnabled` 及一组
  `captions*` 外观/引擎偏好），与 screenpipe 引擎、`/rec` slash 命令、dashboard/
  registry/inbox 的任何形状互不相干。音频采集为 App 进程内自有通路
  （AVAudioEngine 麦克风 + ScreenCaptureKit 系统声音），与录制引擎并行共存。
- **新增 secrets 文件名（BYO key，App 专用）**：`config/secrets/` 下新增
  `volcano-speech-key.txt`（豆包流式语音识别）与 `volcano-ark-key.txt`（Ark 翻
  译），同既有 secrets 契约（目录 0700、文件 0600、单行 + 换行）。**只有 Mac App
  读取这两个文件——Python/cron 侧永不读取**（区别于 anthropic/slack/gmail 三个
  跨组件文件）。App 不内置任何 key。
  - **v0.37.1（add-only）**：`volcano-speech-key.txt` 允许第二种内容格式，承载
    旧版语音控制台凭证：两行 `appid:<App ID>` + `token:<Access Token>`（权限
    /归属/换行约定不变）。单行裸内容一律按新版 API Key 解读——v0.37.1 之前
    保存的文件不需迁移。`volcano-ark-key.txt` 格式不变。解析的唯一真源是
    `VolcanoSpeechCredential`（mac/Sources/CaptionCore.swift）。
- **隐私**：字幕文本永不落盘、永不进 analytics/telemetry（只有 `captions_toggle`
  / `captions_autostart` / firstReach `live_captions` 元数据事件）、永不离开本机
  ——唯一外发目的地是用户自己 key 对应的识别/翻译服务端点（Apple 本地引擎则完全
  离线）。
- **TCC 新增面**：首次以麦克风为来源开启时，App 首次主动调用
  `AVCaptureDevice.requestAccess(.audio)`（此前麦克风授权一直由 screenpipe 子进
  程触发）；系统声音复用既有「屏幕录制」授权探测/深链。

## 37. v0.37.0 找得到、看得懂 — 看板搜索全量化 + 活标题（add-only）

### 37.1 活标题 display_title

- **内部 `title` 冻结不变**：它是 `merge_or_new`/`_same_source_and_title`/
  re-raise 的**身份锚点**，任何机制都不得改写。人看的名字走新字段。
- 注册表 Requirement 新增三个 optional 字段（add-only，`to_dict` 空值不序列化）：
  - `display_title`（str）——看板显示名；
  - `user_titled`（bool）——用户钦定标记：为真时 LLM/harvest 标题**永不覆盖**；
  - `former_titles`（list[str]，cap **3**，去重，最新在后）——display_title 每次
    变更把旧名追加进来（`registry.FORMER_TITLES_CAP`），改名后旧名仍可搜索、
    并在展开详情显示一行「曾用名: …」。
  唯一落笔点 = `registry.set_display_title(req, title, by_user=)`（fail-closed：
  非 str/空/collapse 后为空一律 no-op；接受值 whitespace-collapse + 截断
  `titles.MAX_DISPLAY_TITLE`=64）。
- **投影期 fallback 链**（`act/lib/dashboard.py` `_display_title`，每个 pass
  对所有卡生效——legacy 卡零迁移）：存量 `display_title`（用户钦定或 LLM）→
  确定性 `titles.sanitize_title(title)` → `title`。sanitizer（纯函数，
  `act/lib/titles.py`）：http(s) URL → `domain ▸ 最后有意义的路径段/视频id`；
  文件系统路径 → 最后一段；>60 字长文本 → 首句/首分句截 ~48 字加 …；空白折叠。
  **结果：裸 URL/路径永远不会再作为看板标题出现。**
- **dashboard 行新增 add-only 字段**（全部 optional，Swift `decodeIfPresent`；
  空值整键省略、不发 null）：**所有**分区行统一加 `display_title`（恒非空）
  + `user_titled` + `former_titles` + `notes_text`（notes 折叠，cap 2000 字，
  含评论/radar 备注——为搜索投影）。Swift 侧 needs_approval/running 族(含
  queued/needs_input/completed)/review/debt 全量解码；trash/archived 行只解码
  `display_title` + `user_titled`（其余键照 add-only 约定忽略）。running 的
  from_review 行既有 `final_draft` 继续携带（搜索用）。
  展示优先级（Swift 侧 `displaySummary`/`rowTitle`/`displayHeadline`）：
  用户钦定名 > summary（摘要优先面）/ display_title（名字优先面）> 冻结 title。
- **LLM 生成只搭现有便车（零新增调用）**：quick_capture 的 capture/triage
  prompt 与 analyze 的扩写 prompt 新增 optional 输出键 `display_title`
  （≤40 字中文大白话、动词开头）；缺失/坏类型静默降级，绝不影响父解析。
- **`CARD TITLE:` 收割线（标题随讨论演化）**：executor 的收尾指令（三种交付
  closing + rework gate line）允许在结束总结里给**单独一行**
  `CARD TITLE: <≤40字新标题>`（chat 模式放在 `FINAL DRAFT:` 之前）。
  `harvest_delivery` 返回值新增 add-only 键 `card_title`（fence 纪律与
  FINAL DRAFT 相同：``` 围栏内的 marker 不算；最后一条 marker 生效；超长截
  64；该行从 delivered_summary/final_draft **剥除**）。actd 在
  delivered_summary 落账的同一批 promotion 点（done_external / stop_to_review
  / attach 回流 re-harvest / _promote_if_delivered / reconcile done 分支）经
  `set_display_title` 应用——只在轮次边界刷新，user_titled 钦定优先。
- **inbox 动作全集（§10）新增 `set_title`**：
  ```json
  {"id":"R-xxx","action":"set_title","title":"<新显示名>","ts":"<ISO8601>"}
  ```
  三重 fail-closed 校验（v0.33.1 边界原则）：syncd 形状闸门把 `title` 纳入
  str-or-absent 字段表；webui 400（须 str 且 1–64 字符）；actd 侧非 str/空/
  >64/archived 卡一律 no-op + log（ack `noop`），成功置 `display_title` +
  `user_titled: true`（ack `running`）。Mac UI = 各车道卡片展开详情里的
  ✏️「改名」行内编辑（正常 submit 管道 + 乐观回显 `pendingTitles`，180s 兜底
  橙条）。iOS 本期**只显示**（经 shared `displayHeadline`/`rowTitle`/
  `BoardModel.title(of:)`），无改名入口。

### 37.2 看板搜索全量化（Mac）

- **归一化匹配**（`shared/Sources/SearchMatch.swift`，Foundation-only 纯函数，
  contract harness 锁定）：两侧 lowercase 并剥掉 `-`/`_`/`.`/空白后做子串比较
  （"eb1" 命中 "EB-1A"、"h1b" 命中 "H-1B"，"eb2" 不误命中 "EB-1A"）；CJK 原样
  子串；查询按空白切词 = **AND 语义**；空查询 = 直通。
- **词表扩展**（`DashboardStore.searchFields`，per lane 按行有什么搜什么）：
  id + 冻结 title/name + display_title + former_titles + summary + notes_text
  + plan/dod + delivered_summary/final_draft + source quotes + agent_name。
  占位卡/建议卡直通规则不变。
- **会话内容层（LAST layer）**：`state/search_index.json`
  （`{card_id: {updated_at, text}}`，原子写）——actd 在上条的既有 harvest/
  promotion 触点用 `executor.transcript_plain_text`（主线程 user+assistant
  纯文本，沿用 v0.33.1 sidechain/isMeta/tool-result 纪律；**首条 user turn
  跳过**——那是每张卡都相同的派发 prompt 样板，收进索引会让「命中会话」
  对 卡片/draft 这类词全板亮起，其真实内容 title/plan/sources 已在行字段可搜；
  后续 user turns（打回反馈/attach 输入）保留；尾部截 ~50KB/卡）维护。
  每 pass 顺带 prune——**只清不可逆消失的卡**（merged 终态、遗留裸
  rejected、registry 里已硬删的），trashed/archived 可恢复（restore/
  unarchive）所以条目保留（文件不存在时零开销）。**该文件是 Mac-local
  非契约面：永不进 dashboard.json（E2E 看板负载不得增长），手机端不感知。**
  Mac Store 按 (mtime,size) 懒加载并预归一化缓存；**命中语义 = 跨层合并
  AND**——每个查询词可由行字段**或**会话文本满足（"推荐信 chen" 命中
  标题含推荐信、只有会话里提过 chen 的卡）；「命中会话」badge = 命中且
  仅靠行字段不命中（诚实条件）。输入框即时回显、过滤 ~200ms 去抖，
  归一化字段/会话文本与逐卡命中结果均按 (dashboard 解码, 查询, 索引
  mtime) 记忆化——纯 Mac 端实现细节，无契约形状。索引缺失/损坏 = 该层
  静默缺席（字段搜索照常），绝不崩。
- **iOS 本期无搜索 UI**（诚实声明）：搜索仍是 Mac 看板专属；iOS 自动获得的只
  是行渲染上的 display_title。webui 搜索面不变。

## 38. v0.38.0 少建卡、会折叠 — 折叠优先 + 可逆拆分 + 规则合并提示（add-only）

三层设计，目标 = 琐碎信息不再张张成卡；全程不改 §1 状态机、不动冻结 `title`。

### 38.1 判定口径变更（triage/capture prompt bias，语义变更点）

- **折叠优先**：纯进展 / FYI / 补充 / 顺带一提的琐碎信息，只要与清单里某张卡
  相关，一律 `relates_to` 折进那张卡（`needs_action` 照旧如实判断）；**只有
  全新的、需要 owner 行动或决策的可执行诉求才 `new_proposal`**。此前的
  无损原则偏置（拿不准就新建）针对这类信息反转——安全性由 38.2 的**可逆折叠**
  兜底：折错了可以拆回，信息不会丢。
- 入库把关 marker（`入库把关`）与既有判定行全部逐字保留（add-only 追加行）；
  快速捕获（self-DM）prompt 同步追加「折叠优先」段，无损原则原文不动。
- **喂给匹配器的清单增强（实现注记，非契约形状）**：triage/capture 的注册表
  清单每行在 `R-xxx | status | title` 之后追加可选段 ` | 显示名: <display_title
  或确定性 sanitize 回退>` 与 ` | 关键词: <≤6 个确定性 alias>`；prompt 里另有
  「最可能相关」确定性预筛块（`act/lib/match_corpus.py` 的 normalized-token
  overlap，top-3）。`match_corpus.normalize` 是 §37 `SearchMatch.normalize` 的
  **python 孪生**（lowercase + 剥 `-`/`_`/`.`/空白，CJK 原样）——两边语义
  同步改。无任何新增 LLM 调用。
- **匹配语义硬规则（review 定案，测试钉死）**：
  - **隐私**：token 会出现在围栏外的 prompt 文本里（关键词/重合词/规则判定
    rationale），而 normalize 恰好剥掉密钥 pattern 依赖的分隔符、让 runner 端
    整 prompt scrub 失效——所以一切 corpus **先 `sanitize.scrub` 再 tokenize**；
    **alias 只取 title/显示名/summary**（notes 与来源引句是第三方不可信文本 +
    密钥/PII 高发区，永不进 alias）；长纯数字串（电话形状，scrub pattern 不
    覆盖）只参与匹配、**永不展示**（`display_tokens`）。
  - **预筛只对内容排名**：`candidate_desc` 的脚手架（候选需求/原文引句标签、
    来源/日期/链接行）不参与 overlap——否则标签词自制「重合词」证据，把真新
    诉求折进巧合卡。
  - **中文停用**：常见助词/代词/客套 bigram（帮我/一下/我看…含掩码词 脱敏）
    不成为 token；且 **2 字 CJK gram 一律不计入证据数**（只贡献 overlap
    分数）——同一联系人两条不同请求不得因功能词被判 near-dupe。
  - **同一分隔符 run 只算一份证据**：tokenizer 对 "EB-1A" 同时产出
    eb1a/eb/1a（保证互相能命中），但证据计数（`strong_evidence`）按包含关系
    去重——单个共享 identifier 绝不独自凑满 ≥3 词门槛；展示列表（关键词/
    重合词/rationale）只打整 run 词，无 eb/1a/荐信 类碎片。
  - **折叠簿记不参与匹配**：notes 里的 `[@ts]`/`[已拆出 R-yyy]` tag 在
    tokenize 前剥除——两张不相干的折叠卡不得因时间戳碎片「重合」。

### 38.2 可逆折叠 — 折叠备注时间戳 + inbox 动作 `split_note`

- **折叠备注行形状**（`registry.append_fold_note`，radar/quick 两类折叠的唯一
  落笔点）：`[radar|quick] <text> [@<ts>]`，`<ts>` = UTC ISO 秒级时间戳（同卡
  同秒冲突追加 `#n`），是该行的**稳定拆分句柄**。拆出后行尾再追加
  ` [已拆出 R-yyy]`（append-only，原文保留作历史）。`[kind] <text>` 前缀
  冻结（§38 之前的测试锚定它）；§38 之前的无时间戳旧行不可拆（无句柄，诚实
  降级为纯展示）。同 (kind, text) 去重不变（retry 无害不变式）。
- **dashboard 行新增 add-only 字段 `notes_text`**（str，notes 投影，cap 2000
  字，空值整键省略）：`needs_approval[]`、`debt[]`、`review[]` 三个分区携带
  （Swift `decodeIfPresent`）。**截断语义 = 行对齐 TAIL**：超 cap 时保留最后
  ~2000 字、向前对齐到整行、头部加一行「…（更早的备注已省略）」——折叠行追加
  在尾部，HEAD 截断会静默丢掉最新折叠的 `[@ts]` 句柄（拆分入口随之消失）。
  与 §37（PR #55）同名字段合流时收敛为一份实现，**以本节 TAIL 语义为准**
  （键名/cap 逐字相同）。
- **inbox 动作全集（§10）新增 `split_note`**（折叠的撤销，拆成新卡）：
  ```json
  {"action":"split_note","id":"R-xxx","note_ts":"<ts 句柄>","ts":"<ISO8601>"}
  ```
  三重 fail-closed 校验（v0.33.1 边界原则）：syncd 形状闸门把 `note_ts` 纳入
  str-or-absent 字段表；webui `ALLOWED_ACTIONS` 收录 + `note_ts` 须 str 否则
  400；actd 侧非 str / 未知卡（ack `unknown`）/ **终态卡**（trashed/merged/
  rejected/archived，§32.2 终态原则——stale 详情面板不得从死卡铸出活卡）/
  未知 ts / 已拆过的行一律 no-op + log（ack `noop`，重放绝不二次出卡）。
- **actd 语义**：取该行文本走**正常 capture 路径**成新卡（`raising` → AI 扩写
  → 提案；默认路由），notes 带 `[拆自 R-xxx]` 溯源 + **registry 新增 add-only
  optional 字段 `split_from`**（str，= 原卡 id，机器可读血缘——§38.3 的
  auto-merge 永不建议把刚拆出的卡合并回原卡）；**刻意不过 merge_or_new**
  ——用户刚说了这条不属于那张卡，确定性再折叠等于撤销这次撤销。新卡先落盘、
  原行后打标（archive() 的 crash-mid-move 同款次序）。打点 `split_note`
  （metadata only）。折叠行解析器 `FoldNote`（shared/Sources/FoldNote.swift，
  Foundation-only，contract harness 锁定）与 registry 三个正则 lockstep；
  截断的 `[已拆出 R` 残 tag 安全降级为纯展示行，绝不产生幻影拆出标记。
- **Mac UI**：needs_approval / 备选 / 待验收 卡的展开详情渲染「📎 折叠进来的
  信息」行列表（解析 `notes_text`，与 registry 正则 lockstep）；带句柄的行给
  「拆成新卡」小按钮（正常 submit 管道 + 乐观回显 `pendingSplits`，真信号 =
  原行出现 已拆出；180 s 兜底橙条诚实报超时）；已拆行显示灰色「已拆出 R-yyy」
  徽章。**iOS 本期只显示不拆**（诚实声明：无拆分入口，行渲染不变）。webui
  本期无拆分入口（动作已在白名单，仅未做前端）。

### 38.3 规则合并提示 — 确定性 near-dupe 自动建议（无 LLM）

- **触发**：actd 每 pass（`act/lib/auto_merge.scan_new_cards`）对**新出现的
  未结卡**（detected/raising/card_sent/approved/executing/review；增量台账
  `state/auto_merge_seen.json` 的 `scanned`）与其余未结卡做 §38.1 同一套
  normalized-token 重合判定：**高重合**（overlap ≥0.6 且 ≥3 个**强证据**
  重合词）**或同一非 owner 联系人 + 中等重合**（≥0.4 且 ≥2 个强证据词）→
  自动生成一条 §21 合并建议。强证据 = 排除 2 字 CJK gram + 同 run 去重
  （§38.1 匹配硬规则）。血缘/同 thread/**拆分**关联卡（improvement_of /
  thread_id / thread_key / `split_from` 相同或互指）不判——那是刻意关联，
  不是撞车（拆出的卡与原卡内容天然相似，建议合回 = 撤销用户的撤销）。
  rationale 如实区分触发路径：高重合 =「标题/内容高度相似」，联系人路径 =
  「来自同一联系人且内容中等重合」——0.4 档不得自称高度相似。
- **作业文件 = §21 的 MS- 形状原样复用**（`state/merge/MS-*.json`，直接落
  `status="done"`）：`verdict="merge"`、`primary`=较旧卡、`rationale`=
  「规则判定：…（重合关键词：…）」、`action_plan` 如实描述确定性 apply、
  **`confidence="deterministic"`**（App 端渲染「规则判定」徽章；旧 App 按
  未知字符串灰徽章展示，不崩）、**`auto: true`**（provenance 标记，投影
  不转发）、`expires_at`=+24h（§21 TTL 清扫照常适用）。**采纳/取消 = 既有
  `merge_apply` / `merge_dismiss` 路径零改动**。
- **节流（硬规则）**：① **同一无序卡对终生只提示一次**（`auto_merge_seen.json`
  的 `suggested` 台账持久化——MS- 文件 24h 会被清，不能从它派生），因此
  **取消对该卡对即为终局**；② **未决自动建议同时最多 3 条**（auto 且仍
  `done` 在板上的计数）——**超限被延迟的卡不记入 `scanned` 台账**：它下个
  pass 仍算新卡、重新参评，直到看板清空腾出名额（只有完整评估过的卡才退休
  进台账，被延迟的卡对真正存活到出头之日）；③ 终态/封存卡（trashed/merged/
  rejected/archived/delivered）永不参与。
- analytics：`auto_merge_suggested{suggestion,primary,secondary}`（metadata
  only）。

# v0.39.0 additions（需输入卡可直接回答 — 问题上卡 + 应用内作答）

## 39. 需输入的 `question` 字段 + `answer_input` 动作（add-only）

**背景**：agent 卡在 needs_input 时，看板只显示 `waiting_for: "input"`——用户
既看不到 AI 在问什么，也没有任何 App 内回答入口，唯一出路是复制命令去终端。
本节把「问题」投影上卡、把「回答」做成一等 inbox 动作，Mac 与 iPhone 同权。

### 39.1 dashboard `needs_input[]` 行新增字段（add-only，Swift `decodeIfPresent`）

- `question`(str，≤500 字)：被阻塞 session 的**最后一条 assistant 正文**（
  `executor.extract_question`——与 harvest 同一套 transcript 纪律：短 id glob、
  跳过 sidechain/isMeta/tool-result 行、只取**最后一个真实 user turn 之后**的
  文本，rework/answer 注入即 user turn，绝不把上一轮的话当成当前问题）。
  **超长截断以 `…` 结尾**（总长仍 ≤500）——任何 surface 都不得把节选呈现成
  全文。无 transcript / 无正文时**整键缺失**（不是 null）。热路径防线：按
  (sid, transcript 签名) 记忆化（`dashboard._QUESTION_CACHE`，v0.33.1 tinfo
  memo 同款 (path, mtime_ns, size) 签名）——空闲阻塞的 transcript 每 pass 只付
  stat 成本，绝不重复整文件 json-parse。
- `waiting_for` 语义收紧：roster 给出的原因照旧透传；**兜底 `"input"` 只在
  没有任何 transcript 正文（question 缺失）时保留**——真问题旁边的裸
  "input" 是噪音。有 question 且 roster 无原因时 `waiting_for` 为 null。
- `last_error`(str|null) + `last_error_id`(str|null)：与 running 行同源（§25
  分类）——回答送达失败必须在卡上可见，不只在通知里。

### 39.2 inbox 动作 `answer_input`（§10 全集追加）

```json
{"action":"answer_input","id":"R-001","text":"用 A 方案，预算 $50 以内","ts":"…"}
```

- **形状**：`id` + `text`（不是 `comment`）；同步端可钉 `expected_status`
  （§32.2）。三重边界校验（§33 house pattern）：手机→syncd 形状闸门（`text`
  已在 str-or-absent 词表）、web→webui（ALLOWED_ACTIONS + `text` 1..4000
  长度门 400）、actd 侧 fail-closed——`text` 非 str / 空 → logged noop
  （垃圾绝不 relaunch session）；未知卡 → `unknown`；**超 4000 且卡已知** →
  按下条 `[回答未投递]` 存档 + 通知（客户端已按上限裁剪，落到这里=生客户端，
  文本开头仍值得保住）；**仅 EXECUTING 卡可回答**（needs_input 行只投影
  executing 卡）。iPhone 钉 `expected_status:"executing"`；Mac 本地不钉
  （既有惯例）。**4000 上限按 Unicode code point 计**：Swift 端用
  `InboxAction.clipAnswer`（unicode scalars ≈ Python code points）裁剪——
  按 Character 的 `prefix(4000)` 会让 emoji/组合字符串超出 4000 code points、
  在 UI 已显示成功之后被服务端弹回。
- **stale ≠ silent（合法 text + 卡存在之后的任何未投递都必须可见）**：
  `expected_status` 不符 / 卡已不是 EXECUTING（最常见 = `_promote_if_delivered`
  的 executing→review 提升与 inbox pass 赛跑）→ ack `noop`，**且**把打的字
  存档进 notes：`[<date> 回答未投递] <原因>；原文：<text 截 200>` + 通知
  `msg_answer_not_delivered`（「你的回答没有送出去——你打的文字已存进卡片
  备注，没有丢」）。两端 UI 的乐观回显都把发送当成功，裸 logged no-op 就是
  静默吞字——这是 §39.2 自己的红线。
- **投递前 roster 探测（绝不 stop 正在工作的会话）**：磁盘上的 EXECUTING 同时
  覆盖 roster working 和 blocked，而投递管线先 `claude stop` 再 resume——
  不加这道闸，第二台设备的迟到「回答…」（或 webui 对任意 executing 卡发的
  answer_input）会把**正在跑**的 session 在任意 tool call 中间杀掉、再灌一份
  重复答案。规则：actd 投递前 fresh 读 roster；session **有活 pid 且 state ∉
  blocked-states** → 一律不碰（不 stop 不 resume），按上一条的
  `[回答未投递]`（原因=「会话正在工作中，可能已被回答」）存档 + 通知，
  ack `noop`。只有真正 blocked 的会话——或 dead/缺席的（既有的复活路径）——
  才收 stop+resume。
- **回答冷却窗（roster 探测的 belt+braces）**：成功投递后 resume 的新 session
  可能还没出现在 roster（启动间隙）——探测在这个间隙里看到的是「缺席」，
  第二台设备的竞速回答会把刚复活的 session 再 stop 一次。规则：
  `last_answer_at` 距今 **< 120s** 且 `execution.last_error` 为空（上一次
  投递没有失败记录——失败后的合法重试绝不被拦）→ `[回答未投递]`（原因=
  「刚有一条回答送达，可能还在生效中」）存档 + 通知，ack `noop`。120s 覆盖
  resume 启动 + 一个手机往返；agent 的下一个真问题通常远晚于此，即便撞窗
  也只是「两分钟后重发」（通知里写明）。
- **投递（executor.answer）**：与 rework 同一条 stop-idle-then-resume 管线
  （blocked 活进程拒绝 --resume，先 `claude stop`；full-UUID + transcript 最后
  cwd；无 transcript → 不启动直接失败），resume prompt = `OWNER ANSWER:\n` +
  原文——极简前缀，让 session 知道这是对它问题的回答，不是新任务也不是打回。
- **账目（区别于 rework_count，绝不混记）**：`execution.answer_count`(int 累计)
  + `last_answer_at`(UTC ISO)。**成功启动同时重置 auto-resume 退避**：
  `resume_attempts=0`、删 `resume_exhausted`、且**不计**一次 resume_attempt
  （不双记）。理由：reconciler 只在恰好**看见** session 活着时才清零 attempts，
  而 `resume_exhausted` 从不自清——不删的话，一张曾放弃自动恢复的卡在 owner
  亲手救活它之后，未来中断仍被静默拒绝 auto-resume。状态机不动：卡保持
  EXECUTING（resume 铸新 sid 照旧收养，root_session_id 锚定不变）。
- **诚实处置（§5.4）**：session 成功 resumed → ack `running` + notes 追加
  `[<date> 回答已送达] <text 截 200>`；投递失败（transcript 没了 / 启动失败）
  → ack `noop` **且三处可见**：notes 追加 `[<date> 回答送达失败] <原因>`、
  `notify.msg_answer_failed` 通知、卡上 `last_error`（39.1）；stale/working
  未投递 → 上两条的 `[回答未投递]` 存档 + `msg_answer_not_delivered` 通知
  ——任何路径都绝不静默吞答案。analytics：`inbox_answer_input`(ok/chars/
  reason∈working|review|recent|oversize|moved|launch_failed + capture_input
  门控的 text)、executor 侧 `answer_launch`/`answer_failed`（feedback 同款
  形制）。

### 39.3 UI（Mac + iPhone 同权；终端降级为次要通道）

- **Mac**：needs_input 卡主按钮 **「回答…/Answer…」**（橙）→ NSAlert 弹层：
  问题面板（只读可滚动；内容即 `question` 字段——超 500 字为节选、以 `…`
  结尾，绝不把节选标成全文）+ 多行输入（↩ 发送 · ⇧↩ 换行，promptText 同款）；
  发出后卡上原地显示橙色「回答发送中…」（`store.answerPending`），**真信号
  清除** = 卡离开 needs_input（答案送达 session 恢复 working；或投递失败带
  last_error 改投 running）——generated_at bump 不清（§21bis 先例）；180 s
  未动 → 诚实橙色超时条。卡正文显示 question（≤8 行，弹层里看全文）；
  「单击复制·双击终端」的命令回显行从需输入卡正文**降级进 展开详情**（
  「在终端接管会话」+ 命令，点击复制）——回答是主通道，终端是次要通道。
- **iPhone**：RunningRow 需输入变体显示 question（缺失时回退 waiting_for）+
  **回答输入框**（TextField + 发送，走 `InboxAction.answerInput` → 既有
  sealAndPost 密文通道；失败保留草稿）。**已发送态不走 merge 卡的 3.5s
  echo**——answer_input 非幂等（重发会 stop 掉刚复活的 session），输入条在
  `AppState.answerPending`（per-card，Mac answerPending 同语义）里保持
  「回答已发送，等待送达…」直到该卡在 board 刷新中**真正离开 needs_input**，
  180s 未动过期重新解锁（诚实重试）。运行中行渲染 `last_error`（红色紧凑行）
  ——投递失败在手机上必须与成功可区分（§39.1 的字段本就在 wire 上）。
  需输入行同时带**「停止」二选一**（退回提案=`abort_execution` /
  去待验收=`stop_to_review`，Mac v0.21 blocked 行同款文案）——停止与回答
  是对被阻塞 agent 仅有的两个操作，同属本行；两个 verb 都是 v0.10.2 幂等
  逆向动作，走普通 submit 通道。「手机对需输入只读」的旧注记（plan §6.2）
  就此作废。
- **webui**：ALLOWED_ACTIONS 加入 `answer_input`（API 可用）；本期不做 web
  输入框 UI。

### 39.4 通知与角标

- **needs-input 通知带问题摘录**（§5 文案修订）：`msg_needs_input(title,
  question)` body = `<title> 在问：<question 截 120>` + 真实位置指引——看板
  上卡在「运行中」列**顶部**、橙色「需输入」badge、点「回答…」直接回（
  popover 保留独立「需输入」区）。逐卡通知，不合批（既有行为）。
- **iOS 角标语义变更**：badge = `needs_approval + needs_input`（此前只数
  needs_approval）——被阻塞的 agent 正在烧墙钟时间，是最紧急的 owner 决策。
  新增逐卡本地通知 `notifyNeedsInput`（带 question 摘录；首次拉取该 channel
  只记账不通知，防启动风暴）。
- **回答失败通知**：`msg_answer_failed(title, reason)` —— 指向卡上错误详情与
  展开详情里的「在终端接管会话」兜底。

## 40. v0.40.0 钱看得见、事有回执（add-only）

> 一批诚实性/反馈欠账。全部 add-only：老 App 忽略新键（`decodeIfPresent`）、
> 老 payload 照常解码；merge 顺序在 v0.36 系列之后（先合者占号，后合者 rebase）。

### 40.1 `cost_state`（needs_approval 每项，add-only）

- `"estimated"`：`cost_estimate_usd` 能解析成数字——数值照旧发 `cost_usd`；
- `"unknown"`：无估算或坏值（direct-run 提升卡、capture 兜底卡、weekly-digest
  建议卡、`cost_estimate_usd: cheap` 之类）——之前这些卡在 UI 上**看起来免费**。
- 展示语义：**展开详情永远说钱**——有数显「预计费用: $X」，无数显「成本未知」；
  `show_cost`（≥ `show_cost_above_usd` 阈值）继续**只**门控收起态的 cost badge，
  语义不变。T2 打字确认对话框同样带金额（或「成本未知」）。
- 老 payload 缺 `cost_state`：App 端按 `cost_usd` 有无派生（有数=estimated）。
- iOS/webui 的展示是后续跟进：字段在共享 Contract.swift 里已解码，尚无视图消费。

### 40.2 快速捕获 emoji 回执（Slack self-DM）

- 每条被捕获的 self-DM 消息上打**一个** `reactions.add` 回执（打在消息本身，
  **绝不回帖**——v0.21 只进不出的决定不变）：
  - 📥 `inbox_tray` = 已记下（新卡 / 并入已有卡 / 折叠备注 / 后续卡）；
  - ↩️ `leftwards_arrow_with_hook` = 命中已验收卡，回锅重新提案；
  - 🚫 `no_entry_sign` = 判定无需行动，**没有**建卡。
- emoji 由**入库结果**推导：`quick_capture.apply_result_with_kind`（§40 新增的
  **additive seam**）返回 `(kind, saved, reply)`，kind 与 `apply_triage` 完全
  同一词表（proposed/folded/follow_up/reraised/ignored）；`radar_slack.
  _RECEIPT_EMOJI` 按 kind 映射。结果（↩️ vs 📥、sealed-id fall-through 实建新卡）
  在 apply_result 内部才决定，**不可**从决策 dict 推导——包括 **new_proposal
  决策内部触发回锅**的情形（卡片命中已验收母卡时 merge_or_new 会 re-raise），
  这条路径经同形 additive seam `registry.merge_or_new_with_kind`（公共
  `merge_or_new` 签名冻结、纯委托）如实上报。公共 `apply_result(res, cfg) ->
  str` 的签名与回执字符串**逐字冻结**（纯委托 seam 的第三元）——并行分支
  （feat/less-cards）rebase 时只需围绕这两个新增函数，不涉及签名变更。
- 回执只在入库调用正常返回**之后**发——注册表写入结果未知时绝不打 📥。
- Best-effort 红线：reaction 失败（缺 `reactions:write`、网络）只记 analytics
  （`capture_receipt_failed`），**绝不**阻塞或失败捕获；`already_reacted` 视为
  成功回声。开关 `sources.slack_capture_receipts`（默认 true）。manifest 增补
  `reactions:write` scope（json/yaml/slack_setup.py 三处同步）。

### 40.3 雷达 give-up 诊断卡

- `radar.py` 对一篇 note 放弃重试（`FAILED_MAX_ATTEMPTS`）时，除既有 skipped
  行 + `radar_give_up` analytics + 台账案底外，**落一张可见的诊断卡**：
  `status=detected`（备选列）、`type=diagnostic`、标题「有一篇笔记我处理不了：
  <文件名>」、summary 指回原文件（原文还在 <路径>，你可以手动处理或删掉它）、
  notes 带 `[radar-give-up]` 标签 + 最后错误 + 路径。
- 按 note 路径去重（sources 里 `channel="radar-diagnostic"` + `ref=<路径>` 为
  身份，扫描含 trashed/archived）：一篇 note 一辈子至多一张卡，mtime 重置后再
  次 give-up 也不重发。systemic-failure 回滚的 pass 不发卡（账目作废）。
- 入库走 `registry.upsert`（身份=路径，不走 merge_or_new 的标题匹配）。
- 卡片文案随界面语言双语（`failures.pick`，§15 单一语言开关）——去重身份是
  source ref 而非标题，切语言不会导致重发。

### 40.4 weekly digest 失败通知（手动跑）

- `weekly_digest.run(force=True)`（设置页「现在生成一份」，detach 后原本无声）
  的两个错误出口（`claude_failed` / `unparseable`）现在**发通知**：「本周摘要
  生成失败——<一句话原因>，可在设置页『现在生成一份』重试」（`_lang` 双语，
  同 no-data 路径的通知通道）。
- **定时跑失败不通知**（镜像 no-data 的 force 门控）：失败不写 marker，`due()`
  持续为真，launchd 每小时重跑——无条件通知会刷一整天屏。定时失败仍记
  print + analytics（`weekly_digest_skip`）。

### 40.5 `purge_at`（trash 每项，add-only）

- `trash[]` 每项新增 `purge_at`（ISO8601 或 null）= `trashed_at` +
  `trash.retention_days`。null = 不会被自动清（pinned / retention_days≤0 /
  trashed_at 不可解析）——与 `actd.purge_trash` 的实际跳过条件严格一致，
  倒计时绝不许诺一次不会发生的删除。
- Mac 回收站行显示「X 天后永久删除」（≤7 天红色、天数向上取整），pinned 行显示
  「已永久保留」；`purge_at` 缺失/null 时不显示倒计时。iOS/webui 没有回收站
  列表面（只有「删除」动作），无处可显示——本节不涉及。

### 40.6 通知合批（fresh proposals）

- `detect_transitions`：一个 pass 内**新增（非回锅）提案 > 2 张**时合并为一条
  「新增 N 张待审批卡」（`notify.msg_new_cards_batch`；3-tuple 的 req 位为
  null）。≤2 张、回锅（各自点名一个你做过的决定）、需输入、待验收等类保持逐卡
  通知。§28 中继队列的 10 分钟 stale sweep 语义不变。
- 文案 **source-neutral**（不写「雷达」）：actd 只看 board diff，新卡可能来自
  任何入库方（雷达/周摘要/捕获），点名雷达会在非雷达批次上撒谎。
- **weekly digest 落的建议卡整体跳过**（逐卡与合批都不发）：其 §24 通知已按
  数量点名（「另有 N 条自动化建议进了待审批」），再发一遍是重复轰炸。seam =
  行内 `sources[].channel == "weekly-digest"`（dashboard 投影自带）。

### 40.7 周一 digest 落卡（不再落盘）+ 页面用通道显示名

- `act/digest.py` 不再写工作台文件（`digests/digest-YYYY-MM-DD.md`）、通知里
  不再携带文件路径；digest 以 **待验收聊天卡** 落地，与 `act/weekly_digest`
  同一 filing pattern：`status=review`、`delivery_mode=chat`、
  `final_draft`=全文 markdown、`delivered_summary`=开头摘要、按「周一 digest ·
  <日期>」标题 merge_or_new 去重（当天重跑刷新同一张卡）。通知 body 指向
  待验收列。进化建议维持 `status=detected`（潜在任务）——digest.py 自述规则，
  测试钉死。1:1 准备页（`act/oneonone`，独立面）照常写盘、在 digest 正文链接。
- 页面诚实（audit #19 的 digest/oneonone 半边）：条目行用通道显示名
  （`oneonone.lane_name`，随界面语言）而非 registry 原词；承诺账本表述
  owner-neutral 并按 `owner.name` 参数化（`oneonone.ledger_header`）；
  `[MANAGER-OWES]` notes 标签**冻结**兼容，仍被识别与提示。
# v0.41.0 additions（手机和网页不再是二等公民）

## 41. v0.41.0 三端动作一致性（add-only，展示层 + webui 入站闸门）

三端同一个动作应当长同一张脸。本节登记 iOS/网页补齐 Mac 既有语义的面，以及
webui 入站闸门的两个加法。**对 dashboard.json / inbox 文件形状零新增字段**——
唯一的新入站面是 webui 现在放行两个既有形状（§21bis 的 merge_force 与 §34 的
capture `mode:"run"`），Mac/iOS 早已在写。

- **iOS 停止 fork（对齐 Mac v0.21）**：运行中卡的「停止」不再单击即发
  abort_execution——一颗停止按钮打开与 Mac 相同的两选弹窗：退回提案
  （abort_execution，destructive）/ 去待验收（stop_to_review）/ 取消，弹窗
  副标题解释分叉。done_external 随 v0.21 语义离开运行中卡（它住在拒绝弹窗里）。
  **范围注**：本分支只覆盖非 needs-input 行；needs-input 行的停止 fork 随
  §39（feat/answer-input 的回答输入条）在同一处 RunningRow 块落地——两分支
  合并后运行中列所有行才与 Mac 完全对齐。
- **iOS 拒绝 fork（对齐 Mac v0.10.3）**：提案卡与详情页的「拒绝」打开两选弹窗：
  不想做（进回收站，reject）/ 已办完（记为已交付，done_external）/ 取消，弹窗
  正文是卡片摘要。
- **iOS/网页 T2 闸门（对齐 Mac 的 confirmT2 语义）**：`tier=="T2"` 的卡在手机
  和网页上都不再一键批准——「批准」先打开具名确认弹窗（Mac 同款标题
  「T2 · 高影响操作确认」，正文点名卡片 id/摘要与预计成本）。Mac 的键入
  确认/go 流程在触屏/网页上以具名确认弹窗等价（阈值来源同 Mac：tier 字符串）。
- **iOS ActionBar 已提交态**：任一动作发出后按钮条整体切换为「已提交…」加载
  态，直到提交后的刷新落地——复用合并建议卡既有的 busy 模式，杜绝双击重复
  提交。
- **iOS 设备切换器图例**：菜单行在 ●◐○ 后追加 Freshness.label 文字（Menu 会
  剥掉颜色，只剩字形无法区分）；设置页「已配对设备」区补一行图例
  （● 在线 · ◐ 可能陈旧 · ○ 离线/未知）。
- **iOS 详情页补齐**：补上第四颗决策按钮「暂缓」（与卡片行一致）；任一动作发出
  后详情页自动关闭——用户接下来看到的是看板的回执/错误横幅，而不是一张过时的
  详情页。
- **iOS STALE/DEAD 确认并进 fork**：fork 弹窗本身即二次确认；看板可能过时
  （§5.6）时把过时警告行并进 fork 弹窗文案，不再叠加第二个确认弹窗。
- **iOS 诚实切换设备**：切换 channel（或解除当前 channel 的配对）时立即丢弃上
  一台的看板与 boardSeq——A 机的卡绝不在 B 机的名字下渲染，A 机的 seq 也绝不
  被 pin 进发往 B 机的动作（§5.3 目标锁定语义的前提）。
- **网页合并建议卡（对齐契约 §21/§21bis）**：渲染 merge_suggestions 分区——
  analyzing/done/failed 三态、接受=merge_apply、取消=merge_dismiss、AI 未拍板
  「合并」或分析失败时的「仍然合并」=merge_force（主卡选择弹窗 + 不可撤销告
  知；force 成功后顺手 dismiss 该建议，同 Mac/iOS）。
- **网页回收站 + 永久性完成书立条（对齐 v0.33）**：页面底部两条默认收起的
  `<details>` 书立——trash 分区（恢复=restore、永久保存=pin）与 archived 分区
  （放回看板=unarchive；archived[] 被截断时按 counts 真实总数标注「仅显示最近
  N 条」）。删除/归档确认弹窗不再声称「网页端无法恢复」。
- **网页停止/拒绝 fork**：运行中列一颗「停止」打开与 Mac 相同的两选原生
  `<dialog>`（系统外完成随 v0.21 离开运行中列）；提案列「拒绝」打开不想做/
  已办完两选。
- **网页直跑输入框（对齐 §34）**：运行中列顶部常驻直跑输入框，提交
  `{action:"capture", text, mode:"run"}`；IME 回车守卫与草稿保留（仅确认成功
  后清空——顶部快速捕获框同样改为仅成功后清空）同 Mac/iOS。
- **网页 lane help（对齐 LaneHelp）**：每列列头下方渲染共享 LaneHelp 的一行
  定义文案（zh 逐字镜像自 shared/Sources/Lanes.swift；网页有「永久完成」按钮，
  故 done 列用 macOS 变体）。确认弹窗残留的「归档」字样统一为「永久完成」。
  运行中列改为 needs_input 在前（blocked 卡排最前，兑现 help 文案的承诺，同
  shared BoardModel.runningLane 的排序）。
- **网页重建守卫扩展**：看板重建除既有的 pointer-held 延迟外，凡看板内输入框
  （直跑框等）持有焦点时整体延迟到失焦再重建——光标与未上屏的 IME 拼音不再
  被 5s 轮询吞掉。
- **iOS 文案对齐**：Onboarding zh「这台 Mac」↔ en "your Mac" 不一致处统一为
  你的 Mac；试用到期横幅点名 Apple Developer Program（$99/年）且注明在 App 外
  办理，删除悬空的「升级」动词。
- **webui 入站闸门（act/webui.py，加法）**：
  - `ALLOWED_ACTIONS` += `merge_force`；`_INBOX_KEYS` += `primary`、`mode`。
  - `primary` 无论随何种 action 出现，均须通过与 `id` 相同的防穿越 allow-list。
  - `merge_force` 前置校验（fail closed，actd 照旧重校验）：ids 去重后 ≥2 个
    安全 id 且 primary ∈ ids，否则 400、不落 inbox 文件。
  - `mode` 只在 `action=="capture"` 且值恰为 `"run"` 时放行，其余一律 400——
    未定义的 mode 永不落进 inbox 文件（§34 的 str-or-absent 闸门在 webui 前移
    为白名单）。

## 42. v0.42.0 卡面大扫除（display-only + 一项 radar 提取范围变化）

展示层修订为主，**wire 契约与状态机零改动**：dashboard.json/board payload 的字段、
枚举值、analytics id 全部原样（`MainSection.ingest` rawValue 冻结）；Mac 端仅改
渲染（原始指令/会话 ID/agents 名下沉到展开详情、枚举 chips 本地化大白话、doctor
文案走 `failures.pick` §15 单开关）。

**一项 python 管线行为变化（非渲染）**：radar 提取提示词参数化 `owner.name`，
提取语义从「manager 对 owner 的要求」放宽为「笔记中任何人对 {owner} 的请求」——
同一批笔记可能比旧版提出更多候选卡；来源 `who` 不再虚构 "manager"，现为来源
笔记名（新写入卡片的字段值变化，不是形状变化；注意 `who` 拼进 quick_capture 的
candidate 描述，参与 triage LLM 输入）。

**§15 语言解析顺序补充（add-only）**：python 侧 `failures.ui_lang()` 依次取
① 环境变量 `AIASSISTANT_UI_LANG`（`zh`|`en`——Mac App spawn 有用户可见输出的
python 时传入自己的实际显示语言，App 发起的输出与 App 严格同语言）→ ② 持久化
设置（`state/settings_overrides.json` 的 `language`，其次 `config.yaml` 的
`language`）→ ③ 系统 locale（`LC_ALL`/`LANG`：`zh*` → zh，否则 en——与 Swift
首跑默认一致；旧行为是硬编码 zh）。此外 Mac App 首次启动时，若两个持久化来源
都没有 `language`，会把当下实际生效的界面语言写入
`settings_overrides.json`（幂等，绝不覆盖显式选择；设置页展示的正是这个值）——
这样 launchd/cron 侧（无 `LANG` 环境）的通知文案与 App 同语言，未持久化的 zh
用户不会在 ③ 回落成 en。

## 43. v0.43.0 看板动画（display-only）

纯 Mac 展示层：看板卡片动画（`mac/Sources/BoardDiff.swift` 快照差分 + `BoardMotion.swift` 飞行层）只消费既有 `dashboard.json` 快照与 App 本地乐观状态，对 wire/state/inbox/registry 的任何形状**零改动**；开关 `boardAnimations` 为 UserDefaults 纯界面偏好（同 `cardSortOrder`，pipeline 永不读取）。

## 44. v0.44.0 静默并入 — 重复信息二分法（改写 §38.3 第二步）

产品裁定（Zelin 2026-07-17）：重复/重合信息**要么静默补进主卡，要么常规建新卡，
不再有任何需要人工确认的合并建议卡**。§38.3 的触发规则（`is_near_dupe` 双信号
+ 阈值）、seen 台账（卡对终生一次）、血缘/thread/split 排除、预算节流全部原文
沿用；被取代的只有第二步——规则命中后不再生成 §21 建议卡（MS-），改为：

**§44.1 跨卡静默复核（actd 侧，detached）**：规则命中 → `state/silent_merge/
SM-*.json` 记 pending → 分离子进程 `python -m act.lib.silent_merge SM-x` 跑
一次聚焦两卡的 tool-less LLM 复核（材料 scrub+fence，注入防护同 §21 契约五）。
判「同一件事」→ 立即执行 §44.4 的可逆并入；判「不同/不确定/LLM 失败」→ 一律
什么都不做（保守：宁可留重复卡，不可错并）。无论结局，卡对进 `auto_merge_seen`
台账终局。预算语义变更：`MAX_OUTSTANDING=3` 现在限制的是并发 pending 复核数
（LLM 子进程），不再是"板上未决建议卡"。SM- job 永不进 dashboard 投影（§21 的
`merge_suggestions` 分区形状不变，仅剩人工多选路径产出）。actd 每 pass 清扫：
pending >20min 判 failed，done/failed 过 24h 删文件。

**§44.2 建卡前拦截（radar 慢路径，内联）**：triage 判 `new_proposal` 后、
`merge_or_new` 落库前，对 open 卡跑同一确定性规则；命中最佳候选 → 同款两卡
复核 → 同一件事 → 直接 `_fold_into` 主卡（不建新卡，返回既有 kind="folded"），
否则正常落库。triage `_fallback`（LLM 已挂）时跳过复核直接落库。

**§44.3 会话捎话（"By the way" 通道）**：并入目标（主卡）处于 executing 时，
并入摘要排入 `execution.pending_briefings`；actd reconcile 仅在 §39.2 安全
窗口（roster blocked，或会话已死的 resume 时机）经 `executor.brief()` 注入——
stop-idle-then-resume 管道同 answer()，前缀 `BACKGROUND INFO (no action
needed):`，明示"确认后继续原任务，不是新指令"。working+live pid 绝不打断；
独立记账 `briefing_count`/`last_briefing_at`；每批注入失败 3 次后放弃，
notes 留痕「背景信息未送达会话」。状态机零改动（不翻 rework、不动 status）。

**§44.4 可逆并入（执行语义）**：副卡限**轻状态**（detected/raising/card_sent
——用户已投入的 approved/executing/review 卡永不被静默移除；两张都已投入 →
双双保留，卡对终局）。执行 = 主卡 `append_fold_note`（§38.2 冻结行文法
`[radar] 静默并入 R-xxx「标题」：增量摘要 [@ts]`，自带拆出句柄）+ sources
去重合并 + `repeated_mentions` 累加 + 新计数字段 `silent_merge_count` +1，
主卡先落盘；副卡走 `registry.trash`（`prev_status` 完整保留，回收站可恢复/
可 pin）——**绝不使用 §21 的 `merged` 终态**。双向可逆 = 拆出 fold note +
恢复副卡。

**§44.5 可见性与记账（add-only）**：dashboard `needs_approval[]` 新增
`silent_merged`（int，0=从未）；Mac 卡面「已并入×N」紫色 chip（.help 指明
详情里的并入记录可一键拆回）+ webui 同款 badge；周一 digest 总览行追加
「· 静默并入 N」（近 7 天，仅计数）。analytics 事件（元数据，永不含内容）：
`silent_merge_requested{job,primary,secondary}`、`silent_merge{primary,
secondary,outcome∈ok|separate|judge_failed|state_moved|pre_filing_fold}`、
`briefing{req,ok,n}`。
