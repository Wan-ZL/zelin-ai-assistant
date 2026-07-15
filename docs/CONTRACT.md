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
`approve` | `reject`(→trash) | `comment` | `raise`(debt→建议) | `trash`(→回收站) | `restore`(回收站→prev_status) | `pin`(回收站项设永久) | `capture`(快速捕获，见下) | `done_external`(已办完·系统外完成，v0.10.2，允许状态扩展 v0.12) | `abort_execution`(停止并退回待审批，v0.10.2) | `stop_to_review`(停止并收下成果待验收「去待验收」，见下) | `revert_review`(退回待验收，v0.10.2) | `merge_review`(多选请求合并建议，v0.12，见 §21) | `merge_apply`(接受合并建议，v0.12，见 §21) | `merge_dismiss`(取消合并建议，v0.12，见 §21) | `merge_force`(强制合并·用户钦定主卡、跳过 AI，携带 `ids`≥2 + `primary`，v0.31，见 §21) | `import_claude_sessions`(一键导入 Claude Code 近期会话，v0.13.x，见 §22) | `weekly_digest_now`(立即生成每周摘要，v0.14，无 `id` 字段，见 §24) | `feedback`(建议上报，无 `id` 字段、携带 `ids` 数组（可空），见 §29) | `defer`(存备选，提案→备选，v0.18，见下) | `archive`(封存线程,已验收/备选→归档,v0.20.0,见下) | `unarchive`(归档→prev_status,v0.20.0,见下)。actd 读后删 inbox 文件。

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
