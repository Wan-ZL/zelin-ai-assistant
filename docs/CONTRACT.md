# 组件间数据契约（锁定 — 三层都按此实现，不得偏离）

> **English orientation** — This is the frozen data contract between the Python pipeline and the
> Mac app. Three files make it up: `act/registry/<ID>.yaml` (source of truth; state machine
> `detected → card_sent → approved → executing → review → delivered`, any state → `trashed`),
> `state/dashboard.json` (actd writes, app reads), and `state/inbox/<uuid>.json` (app writes,
> actd reads then deletes). Fields are **add-only** — never renamed or removed; the Swift side
> decodes every new field with `decodeIfPresent`. Change this file *before* any code that touches
> these shapes. **Section numbers §1–§20 are referenced from code and docs — never renumber.**
> The Chinese body is canonical.

## 1. 注册表 YAML（真源）— `act/registry/<ID>.yaml`

一条需求一个文件。状态机：
`detected → card_sent → approved → executing → review → delivered`，旁支 `rejected` / `merged_into:<父ID>`。

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

状态跃迁时用 `osascript -e 'display notification ...'`：
- 新 card_sent（雷达发现新需求）→ "有新需求待审批：<title>"
- executing → done → "任务完成：<title>"
- executing → blocked(needs_input) → "任务需要你输入：<title>"
- 凭证失效（执行日志含 auth/login 关键词）→ "需要重新登录：<service>"

## 6. Mac app 行为

- LSUIElement（菜单栏 app，无 Dock 图标），NSStatusItem
- 每 5s 读 dashboard.json 重渲染；菜单栏标题显示待审批数（>0 时高亮）
- 五区：待审批（卡片带 ✅/❌/💬 按钮）/ 运行中 / 需输入 / 已完成 / 欠账
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
`approve` | `reject`(→trash) | `comment` | `raise`(debt→建议) | `trash`(→回收站) | `restore`(回收站→prev_status) | `pin`(回收站项设永久) | `capture`(快速捕获，见下) | `done_external`(已办完·系统外完成，v0.10.2，允许状态扩展 v0.12) | `abort_execution`(停止并退回待审批，v0.10.2) | `revert_review`(退回待验收，v0.10.2)。actd 读后删 inbox 文件。

**v0.10.2 逆向动作**（公共规则：状态不匹配的动作 = 幂等 no-op + 日志，防连点/迟到 inbox；三个动作均走现有 `inbox_{action}` analytics 自动打点）：
- `done_external`（已办完·系统外完成）：允许 `card_sent | review | approved | executing`（v0.12 从 `card_sent | review` 扩展；动机：agent 停在 blocked 等输入、但 Zelin 已在 attach 会话里拿到交付——这是唯一的完成出口）→ 置 `delivered`；`execution.accepted_at` = UTC ISO now；notes 追加 `[done outside] Zelin 在系统外完成`。分状态行为：
  - `card_sent | review`：有活 session 不动它（人做完了，AI 会话自然闲置）——原语义不变；
  - `executing` 且有 `session_id`：先 best-effort `executor.harvest_delivery(session_id)`（**非空才写** `execution.delivered_summary`/`final_draft`，失败只记日志），再 best-effort `executor.stop_session(session_id)`（清掉挂着的 blocked agent；失败只记日志，**绝不阻塞交付落账**），然后照常落账；
  - `approved`（排队未派发）：直接落账，无 harvest/stop。
- `abort_execution`（停止并退回待审批）：允许 `approved | executing` → 活 session 先 best-effort 停止（`executor.stop_session(session_id)`，即 rework「活进程先 claude stop」的同一路径；stop 失败只记日志，不阻塞状态回退）；`execution.session_id` 归档为 `execution.aborted_session_id` 后删除（保证重新批准时干净重派发），删 `execution.done`，记 `execution.aborted_at` = ISO now → 置 `card_sent`。
- `revert_review`（退回待验收）：允许 `delivered` → 置 `review`；删 `execution.accepted_at`，记 `execution.reverted_at` = ISO now。

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
4) **关于**：版本、repo 路径、`python -m act.report` 提示。

**菜单栏 / popover 补充（v0 bootstrap）**：
- **录制三态**：菜单栏控制 Screenpipe 录制，三态 关 / 仅屏幕 / 屏幕+音频。存 UserDefaults `recordingMode` ∈ `"off"|"screen"|"screen_audio"`，默认 `"screen"`；开 app 时按当前模式**自动启动**录制引擎（引擎运行判定 = `pgrep -f "screenpipe.*record"` 有结果）。引擎启动参数含 sensitive-app 排除（每个 config `recording.ignored_apps` 词条一个 `--ignored-windows`，默认密码管理器 + 无痕窗口标题；`ingest/screenpipe-export.sh` 导出时用同一清单二次过滤——见 docs/PRIVACY.md「你有哪些控制」）。
  - **v0.11 补充（P0-11，覆盖上行 default，字段语义与取值不变）**：fresh install（UserDefaults 无 `recordingMode` key）默认视为 `"off"`，首启弹**一次性**双语 consent alert（`RecordingConsent`，Onboarding.swift）：说明采集什么、去哪里、保留多久，链 docs/PRIVACY.md，按钮 仅屏幕 / 屏幕+音频 / 暂不开启。任一选择均持久化 `recordingMode` + UserDefaults `recordingConsentShown`（Bool），两个 key 任一存在即不再弹；自动启动仅在已存在模式值时进行。已有 `recordingMode` 值的存量安装不受影响、永不询问。
- **popover 快速捕获输入框**：一句话回车 → 写 `state/inbox/capture-<uuid>.json`（§10 capture 动作），app 不直接碰注册表。
- **菜单栏图标显示开关**：UserDefaults `showMenuBarIcon`（Bool，默认 true）；录制状态图标开关 `showRecordingIcon`（Bool，默认 true）。
- **语言即时切换**：界面语言存 `settings_overrides.json` 的 `"language"`（`"zh"|"en"`），切换即时生效（app 与 Python 侧共用该值）。

## 16. Feature flags + 自我进化
- config `features: {slack_radar, gmail_radar, obsidian_radar, digest, auto_resume, analytics, manager_pack}`，默认全 on；各模块入口检查 flag，off 则 no-op。overrides 可改。
- 周一 digest 末尾加**进化建议**节：基于 analytics（30 天未用的功能→建议关；重复风暴/高拒绝率→建议改），生成 type=self-improvement 的卡片（target_repo=本 repo），批准后照常 claude --bg 实现并以 **draft PR** 交付——app 更新永远走 PR。

## 17. 周一 digest + Manager pack
- `python -m act.digest`：待审批积压、待验收积压、needs_input/resume_exhausted 卡住项、低置信度(detected 欠账)清单、双向承诺账本(registry notes 里 [MANAGER-OWES] 标记项)、analytics 摘要+进化建议。产出 markdown 存 workbench + macOS/Slack 通知摘要。crontab 周一 09:07。
- Manager pack（flag: manager_pack）：①obsidian radar 扫到含 manager（watch_people 首项的 first-name token）的新会议记录时，额外派 T0 任务生成**会后 action-item 清单草稿**（workbench/meetings/<date>-action-items.md，通知）；②`python -m act.oneonone` 生成 1:1 准备页（ready/not-ready per registry + 双向欠账 + 上次以来 delta），digest 周一自动附带。

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
