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
`approve` | `reject`(→trash) | `comment` | `raise`(debt→建议) | `trash`(→回收站) | `restore`(回收站→prev_status) | `pin`(回收站项设永久) | `capture`(快速捕获，见下) | `done_external`(已办完·系统外完成，v0.10.2，允许状态扩展 v0.12) | `abort_execution`(停止并退回待审批，v0.10.2) | `revert_review`(退回待验收，v0.10.2) | `merge_review`(多选请求合并建议，v0.12，见 §21) | `merge_apply`(接受合并建议，v0.12，见 §21) | `merge_dismiss`(取消合并建议，v0.12，见 §21) | `import_claude_sessions`(一键导入 Claude Code 近期会话，v0.13.x，见 §22) | `weekly_digest_now`(立即生成每周摘要，v0.14，无 `id` 字段，见 §24)。actd 读后删 inbox 文件。

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
- **Telemetry 覆写（add-only 补充，docs/TELEMETRY.md）**：设置页「产品改进计划」区写嵌套形式 `{"telemetry": {"enabled": …, "level": …}}`（与首启权限页同一 override 键；扁平 `"telemetry.enabled"` / `"telemetry.level"` 两个点号键 Python 侧同样接受），`config.load_config()` 最后合并（优先级最高，覆盖 config.yaml `telemetry:` 块）：
  - `enabled`（Bool）——匿名使用统计上传总开关。**默认 true（默认开 + 明确可关）**。
  - `level`（`"basic" | "detailed"`，默认 `"basic"`）——上传粒度。非法值一律按 `"basic"` 处理。只有 `"detailed"`（用户主动 opt-in）允许 dispatch / delivery 事件携带 ≤200 字符的指令/交付摘要字段（emit 端 gate：basic 级这些字段根本不写入 events.jsonl，因此也永不上传）。

**v0.13 补充（iPhone 联动 / iMessage 设置区，add-only）**：设置页新增「iPhone 联动（iMessage）」区（`mac/Sources/SettingsIMessage.swift`，改动即时生效、不走表单的保存按钮），写两个 §15.3 overrides 键：`phone_channel`（该区只写 `"imessage"` 或 `"none"`）与 `imessage_self_handle`（str，E.164 手机号或 iCloud 邮箱）——两键自 v0.12 起即在 `act/lib/config.py` `_OVERRIDE_FIELDS` 允许列表内，语义见 §13 通道可插拔。App 侧附带职责（不新增数据契约字段）：①开关 = 按 install.sh step 5 相同的占位符替换规则把 `act/launchd/com.zelin.aiassistant.imessageradar.plist` 渲染进 `~/Library/LaunchAgents/` 并 `launchctl load`/`unload`（先写 overrides 再 load，保证 RunAtLoad 首轮就能读到 `phone_channel: imessage`）；②状态行读 `state/radar_health.json` 的 `imessage` 条目（契约 E 同形，radar_imessage 每轮写入）+ `launchctl print gui/<uid>/…`，「立即测试一轮」= `launchctl kickstart`（Full Disk Access 的真值只能来自 launchd 语境下 python 的真实运行结果——TCC 按 responsible process 判权限，app 内直接探测会失真）；③「发送测试消息」经 runtime python（CONTRACT §19 指针）调 `act.radar_imessage` 的同一 osascript 发送路径。

**v0.14 补充（初始设置向导，add-only；不新增 pipeline 契约字段）**：首启界面从单页权限窗升级为多步「初始设置向导」（`mac/Sources/SetupWizard.swift`，步骤：欢迎+语言 → AI 引擎 → 系统权限 → 屏幕记录 consent → 笔记库 → 健康检查）。

- **完成标记** = UserDefaults `setupWizardCompleted`（Bool）：缺失或非 Bool（损坏）→ 下次启动自动重开向导；只有向导结尾的「完成」按钮写 true。设置 → 通用 提供「重新运行初始设置」随时重开。
- **幂等性**：向导所有步骤预填当前生效值，绝不清除数据、绝不重复导入。录制 consent 的 key 与语义完全不变（`recordingConsentShown` / `recordingMode`，v0.11/v0.13 补充照旧）——已回答过的 consent 在向导里只显示状态行，不再询问；向导中途关窗仍按 暂不 记录（同 v0.13 权限窗行为）。存量安装升级后向导会出现一次（标记缺失），走完即消失。
- **写入面**：只写既有的 §15.3 overrides 键（`language`、`obsidian_raw`——均在 `_OVERRIDE_FIELDS` 允许列表内，且仅在与当前生效值不同时 diff-write）与 §19 的 `config/secrets/anthropic-api-key.txt`（粘贴 key 经 api.anthropic.com/v1/models 免费探针验证通过后才落盘，0600）。笔记库步骤会在所选根目录下创建 4 个标准管线子目录（与 config.py `_derive_obsidian_dirs` 同名，幂等 mkdir）。
- **App 侧附带职责（同 v0.13 iMessage 区先例，不新增契约字段）**：健康检查页的「启动后台服务」按钮按 install.sh step 5 相同的占位符替换规则把 `act/launchd/com.zelin.aiassistant.actd.plist` 渲染进 `~/Library/LaunchAgents/` 并 `launchctl load`；「立即生成一次」经 runtime python（§19 指针）跑 `python -m act.lib.dashboard` 补种 dashboard.json。

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

**analytics**：`merge_review_requested{n}`（actd）、`merge_suggestion_done{verdict,confidence}`（分析子进程）；apply/dismiss 由 app 侧 `card_action` 自动覆盖。

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
  仅仅是近期活动 → `status=detected`（欠账）。与其他雷达的置信分流同构。
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

action_id 词表（app 侧动作）：`install_claude · open_settings_key ·
install_node · restart_engine · reload_agent · repair_cron · grant_cron_fda ·
restart_actd · fix_config · retry`。

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