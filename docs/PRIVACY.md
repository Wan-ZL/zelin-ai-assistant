# Privacy — 数据流与信任模型

> **TL;DR**：这是一个屏幕录制 + 自主执行的个人助理。它的核心设计就是**周期性把你屏幕上的文字、
> 音频转写、Slack/Gmail 消息发给 Anthropic API 加工**。本文逐条列出每一个数据出境通道
> （触发 / 频率 / payload / 关闭开关），以及什么永不出境、本地保留多久、你有哪些控制。
> 安全漏洞上报见 [`/SECURITY.md`](../SECURITY.md)。

## 信任模型（一句话版）

- **没有产品方服务器**。本项目没有任何自营后端、没有共享收集端；所有出境流量只去往
  **你自己配置凭证的服务**：Anthropic API（经官方 `claude` CLI）、你自己的 Slack
  workspace（你的 user token）、Gmail IMAP（你的 app password）、GitHub（你的 `gh` 登录）、
  以及可选的**你自己的** Supabase 项目（telemetry，默认关）。
- **LLM 通道只有一个**：所有发往 Anthropic 的内容都经由 `claude` CLI（headless `claude -p`
  或 `claude --bg`），没有绕过它的直连 HTTP 调用。
- Mac app 本身（`mac/Sources/`）**不直接联网**：它只读本地 `state/dashboard.json`、写本地
  `state/inbox/`（见 `docs/CONTRACT.md` §2/§3）。唯一例外是它会以 `npx screenpipe@0.3.349`
  拉起录制引擎（`mac/Sources/Recording.swift`）——首次运行时 npx 会从 npm registry
  **下载**引擎包（进来的流量，不带出你的数据）。

## Egress 清单：什么数据、何时、离开你的机器

总表（每行详情见下方小节）：

| # | 通道 | 触发 / 频率 | 去向 | 默认 | 关闭开关 |
|---|------|------------|------|------|----------|
| 1 | Ingest 加工 | cron，每 30 分钟 | Anthropic | 开 | `recordingMode` off / 删 crontab 行 |
| 2 | Obsidian 雷达 | 同一 cron 链，每 30 分钟 | Anthropic | 开 | `features.obsidian_radar: false` |
| 3 | Slack 雷达 | launchd，每 3 分钟 | Anthropic | 开 | `features.slack_radar: false` / 不配 token |
| 4 | Gmail 雷达 | launchd，每 5 分钟 | Anthropic | 开 | `features.gmail_radar: false` / 不配 app password |
| 5 | Quick capture | 你发 self-DM 时 | Anthropic | — | 不发即不触发 |
| 6 | 欠账扩写 | 欠账升级为提案时 | Anthropic（+ 联网工具） | 开 | 无专用开关（不用欠账循环即不触发） |
| 7 | 执行派发 | **你批准一张卡时** | Anthropic | — | 审批本身就是开关 |
| 8 | 自动建 GitHub repo | 批准指向新目录的卡时 | GitHub | 开 | `execution.create_github_repo: false` |
| 9 | 通知镜像 | 每条 macOS 通知 | 你的 Slack self-DM | 开 | `features.slack_radar: false` / 不配 token |
| 10 | Telemetry | 手动/cron 运行 sync 时 | 你自己的 Supabase | **关** | 默认即关（`telemetry.enabled: false`） |

### 1. Ingest 加工 → Anthropic

- **触发/频率**：用户 crontab 的 ingest 链（install.sh 写入，`*/30 * * * *`）：
  `screenpipe-export.sh` 把 `~/.screenpipe/db.sqlite` 里新增的屏幕文本 + 音频转写导出为
  vault `1 - unprocessed/` 下的 markdown，随后 `process-screenpipe.sh` 起一个 headless
  `claude -p`（allowedTools：Read/Write/Edit/Bash/Glob/Grep）执行 `/unprocessed-ingest`
  skill 加工这些文件。
- **Payload**：**全屏文本**（screenpipe 的 accessibility text + OCR 合并字段 `full_text`，
  含 app 名、窗口名、时间戳）与**音频转写全文**。agent 读文件入 context 即出境。
- **注意**：这条链路是 claude 直接读文件，**词条 redaction 不经过此路径**（redaction 作用于
  拼 prompt 的边界，见下文「Redaction」）。屏幕采集**不区分 app**——密码管理器、银行页面、
  无痕窗口的可见文字同样会进 vault 并被加工（见「残余风险」）。
- **关闭**：菜单栏把录制切到 off（不再产生新数据）；或删掉 crontab 里的 ingest 链一行
  （`crontab -e`）。

### 2. Obsidian 雷达 → Anthropic

- **触发/频率**：同一条 cron 链的末尾（`python3 -m act.radar --once`，每 30 分钟），另有一个
  同样 30 分钟周期的 launchd agent（`act/launchd/com.zelin.aiassistant.radar.plist`）。
- **Payload**：`sources.obsidian_raw` 下新增/变化 note 的**全文**拼进需求提取 prompt
  （`act/radar.py`）；涉及 manager 的 note 会再发一次 action-items 起草 prompt
  （flag `manager_pack`）。
- **注意**：这两处 prompt **目前未过 `sanitize.scrub()`**（redaction 覆盖缺口，修复在
  roadmap 上）。vault raw 是全系统敏感度最高的内容（来源是全屏文本）。
- **关闭**：`features.obsidian_radar: false`（config.yaml 或 App 设置窗口）；
  `manager_pack: false` 单独关 action-items 草稿。

### 3. Slack 雷达 → Anthropic

- **触发/频率**：launchd agent 每 180 秒（`act/launchd/com.zelin.aiassistant.slackradar.plist`）。
- **Payload**：你的 DM、群 DM、被 @ 提及消息的**文本**，self-DM 附件（图片；视频拆帧后按帧）
  先下载到本地 `state/media/` 再描述进 prompt（`act/radar_slack.py`）。prompt 出境前过
  `sanitize.scrub()`。无 xoxp token 时的 MCP 兜底扫描（每 30 分钟节流）只用**只读** Slack
  MCP 工具。
- **读取凭证**：需要你自己的 Slack **user token**（xoxp-），读取范围见 `docs/SLACK_SETUP.md`。
  雷达产出的对外沟通类卡片**永远只生成草稿**——pipeline 不会替你给别人发消息
  （`chat.postMessage` 只发进你自己的 self-DM，见第 9 条）。
- **关闭**：`features.slack_radar: false`；或根本不配置 token（静默 no-op）。

### 4. Gmail 雷达 → Anthropic

- **触发/频率**：launchd agent 每 300 秒（`act/launchd/com.zelin.aiassistant.gmailradar.plist`）。
- **Payload**：INBOX 未读邮件的发件人、主题与**正文（截断到 2000 字符）**拼进 triage prompt
  （`act/radar_gmail.py`）。出境前过 `sanitize.scrub()`。noreply 发件人、带 List-Unsubscribe
  的 newsletter、已接受的日历邀请在本地预过滤，**不会**到达 LLM。
- **读取方式**：IMAP `BODY.PEEK` + readonly SELECT——未读状态不被触碰，只读不写。
- **关闭**：`features.gmail_radar: false` 或 `sources.gmail.enabled: false`；
  不配置 app password 时静默 no-op。

### 5. Quick capture → Anthropic

- **触发**：仅当你主动给自己的 Slack self-DM 发消息（CONTRACT §13）。
- **Payload**：你发的文本/媒体描述 + **当前注册表全量清单**（每条非回收站条目一行
  `R-xxx | status | title`，`act/lib/quick_capture.py`）。出境前过 `sanitize.scrub()`。
- **关闭**：不发 self-DM 即不触发；`features.slack_radar: false` 关掉整个通道。

### 6. 欠账扩写（analyze）→ Anthropic + 联网研究工具

- **触发**：一条欠账（debt）被升级为可审批提案时（CONTRACT §8，`act/analyze.py`）。
- **Payload**：欠账的 title + notes + sources 引文。出境前过 `sanitize.scrub()`。
- **工具白名单（只读红线）**：这个 headless run 允许 `WebFetch` / `WebSearch` + 4 个只读
  Slack MCP 工具（读 thread/channel/搜索/用户资料），**永不**给 Bash/Edit/发消息类工具
  （`act/analyze.py` `_EXPAND_ALLOWED_TOOLS` 及其红线注释）。注意 WebFetch/WebSearch 意味着
  agent 可能把由欠账内容衍生的查询发给搜索引擎和第三方网站。
- **关闭**：无专用开关；不使用欠账循环则不触发。

### 7. 执行派发（executor）→ Anthropic

- **触发**：**只在你批准一张卡之后**（✅ / App 点批准）。审批就是这条通道的开关，也是安全边界。
- **Payload**（`act/executor.py` `build_prompt()`）：卡片的 title / summary / plan /
  definition_of_done + **sources 逐字引文**（来自会议记录/Slack/Gmail 的原文片段）+
  质量门指令 + 你的 Claude Code auto-memory **`MEMORY.md` 的前 60 行**
  （`_read_memory_head()`，路径见 `act/lib/config.py` `MEMORY_PATH`；
  `execution.memory_inject: false` 可关）。dispatch 与 resume 两处 prompt 出境前都过
  `sanitize.scrub()`。
- **执行期间**：被派发的 agent 是一个完整的 `claude --bg` 会话，工作中读到的仓库内容、
  命令输出同样会进入其 context（= 发往 Anthropic）。其权限模型见下文「执行权限」。

### 8. 自动建私有 GitHub repo（ensure_repo）→ GitHub

- **触发**：批准的卡指向一个还不是 git repo / 没有 remote 的目标目录，且 `gh` CLI 在 PATH
  且已登录（`act/executor.py` `ensure_repo()`）。
- **Payload**：`gh repo create <目录名> --private` 在你的 GitHub 账号下新建**私有** repo,
  执行产出（可能源自屏幕/会议/邮件内容）会被推送为 feature 分支 + draft PR。
- **关闭**：`execution.create_github_repo: false`——降级为仅本地 `git init` + 本地分支交付,
  任何失败也自动留在本地（"stay local"，永不阻塞派发）。

### 9. 通知镜像 → 你的 Slack self-DM

- **触发/频率**：actd 每发一条 macOS 通知（新卡待审批 / 任务完成 / 需要输入 / 凭证失效,
  CONTRACT §5），同时 best-effort 镜像一条到你的 Slack self-DM（`act/lib/notify.py`,
  CONTRACT §13）。
- **Payload**：通知标题 + 正文（通常是卡片标题这类元数据，非文档内容）+ `#R-xxx` id;
  消息 ts 记录在本地 `state/slack_outbox.json`（用于 ✅ 反应审批）。
- **关闭**：`features.slack_radar: false` 或不配 token → 只剩本地 osascript 通知。

### 10. Telemetry → 你自己的 Supabase（opt-in，默认关）

- **默认关**。`telemetry.enabled` 默认 `false`,不配置就什么都不发生;开启也只上传到
  **你自己建的** Supabase 项目——没有共享收集端。
- **Payload**：只有 `state/analytics/events.jsonl` 里**已在本机记录**的事件元数据
  （事件名/时间戳/req id/版本/随机 device uuid）,**无任何内容数据**——没有 prompt、
  消息正文、文件内容、密钥。
- 详见 [`docs/TELEMETRY.md`](TELEMETRY.md)。

## 什么永不离开你的 Mac

- **凭证**：`config/secrets/` 下的所有 token/key 文件。凭证内容永不打印、永不入日志
  （CONTRACT §19）,内置 secret-pattern 掩码再兜一层（见下文）。
- **注册表与状态**：`act/registry/R-*.yaml`（真源）、`state/dashboard.json`、`state/inbox/`、
  执行日志、`state/analytics/events.jsonl`（除非你打开 telemetry,而那也只去你自己的
  Supabase）。
- **screenpipe 原始数据**：`~/.screenpipe/db.sqlite` 与媒体文件本身不上传——出境的是
  ingest/雷达 prompt 里**引用到的文本**（见第 1/2 条,这是核心设计而非泄漏）。
- **redaction 词表**：`config/redaction_terms.txt` 只在本地做替换,词表本身与命中的原文
  永不出境、永不入日志——只记录掩码**次数**（`act/lib/sanitize.py`）。

## 本地数据与保留策略

| 数据 | 位置 | 保留 |
|------|------|------|
| 屏幕截图 / 音频媒体 | `~/.screenpipe/data/` | **60 分钟后删除**（`ingest/screenpipe-cleanup.sh`,每 30 分钟跑;引擎本身另有 `--retention-days 1`） |
| 屏幕文本 + 音频转写 | `~/.screenpipe/db.sqlite` | 永久（不自动清理） |
| 导出/加工后的 note | Obsidian vault | 永久（你的 vault,你管理） |
| Slack 附件下载 | `state/media/<ts>/` | 不自动清理 |
| 回收站卡片 | 注册表 trashed 状态 | `trash.retention_days`（默认 60 天）后硬删 |
| 事件埋点 | `state/analytics/events.jsonl` | 永久,append-only,本地 |

## 你有哪些控制

- **录制开关**：菜单栏随时切 `recordingMode`：`off` / `screen` / `screen_audio`。off 后
  不再产生新数据（已在 db.sqlite/vault 里的历史数据不受影响）。
- **Redaction（发给 AI 前的本地脱敏,`act/lib/sanitize.py`）**：
  - **内置 secret-pattern 掩码——默认开**（`redaction.mask_secrets: true`）：sk-ant- / sk- /
    xox* / AKIA / gh*_ / PEM 私钥块等高精度形状,在 prompt 出境前替换为 `[脱敏]`。
  - **用户词条掩码——opt-in**（`redaction.enabled`,默认关,因为掩码会改变模型看到的内容）：
    `config/redaction_terms.txt` 一行一条,`re:` 前缀为正则。
  - **覆盖边界**（scrub 的实际 call site）：executor dispatch/resume、analyze、radar_slack、
    radar_gmail、quick_capture。**未覆盖**：Obsidian 雷达 prompt（缺口,修复在 roadmap）;
    ingest 加工（claude 直接读文件,prompt 级 redaction 不适用于此路径）。
- **Feature flags**（CONTRACT §16,config.yaml `features:` 或 App 设置）：
  `slack_radar` / `gmail_radar` / `obsidian_radar` / `digest` / `auto_resume` / `analytics` /
  `manager_pack`——每一路雷达都能单独关死。
- **`execution.create_github_repo: false`**：杜绝一切自动 GitHub repo 创建。
- **`execution.memory_inject: false`**：关掉 MEMORY.md 注入。
- **Telemetry 默认关**,开也只去你自己的 Supabase（[`docs/TELEMETRY.md`](TELEMETRY.md)）。

## 执行权限（--dangerously-skip-permissions）

**批准一张卡 = 在你的 Mac 上启动一个无人值守的 `claude --bg` agent。** 你需要理解这个 trade-off：

- 默认情况下 executor 以 `--dangerously-skip-permissions` 派发（`act/executor.py`,
  dispatch 与 resume 共三处 call site;CONTRACT §4）。这个 flag 跳过 Claude Code 的
  **全部**逐操作权限确认——agent 可以不经询问地读写文件、跑命令、联网。
- **worktree 隔离保护的是 repo,不是系统**：`claude --bg` 自动把工作副本隔离到
  `<target>/.claude/worktrees/<name>`,所以 agent 不会弄脏目标 repo 的 main 工作区;
  但它仍以**你的完整用户权限**运行在你的机器上——worktree 不是沙箱。
- **质量门是 prompt 级请求,不是强制**：「只开 draft PR、不 merge、不对外发消息」写在
  prompt 里（`act/executor.py` `_quality_gate_block()`）,行为良好的模型会遵守,但它不是
  系统层 enforcement。
- **配置项 `execution.skip_permissions`**（默认 `true`,即现行为）：设为 `false` 时三处
  call site 省略该 flag,agent 走 claude 正常权限模型——被权限阻塞的 agent 会以
  needs_input 浮上卡片等你处理（更安全,更多打断）。
- **残余风险（prompt injection）**：雷达的输入天然包含**第三方内容**（陌生人的邮件、
  Slack 消息、屏幕上打开的网页）。这些内容先变成 LLM 输入,批准后还会以 sources 引文
  形式进入执行 prompt。恶意构造的内容有可能诱导 agent 执行非预期操作。因此:
  **审批是这个系统的安全边界**——批准前请看清卡片的来源与计划,拿不准就 ❌ 或 💬 打回。

## Secrets：为什么是文件而不是 Keychain

macOS Keychain 只对用户 Aqua GUI 会话里的进程可读。本项目的核心组件跑在 **cron 和 launchd**
下（daemon 会话）,读不到 Keychain 的 OAuth token,`launchctl asuser` 桥接在 cron 的 audit
session 下也会被拒（详见 `ingest/process-screenpipe.sh` 头部注释与
`act/executor.py` `_runner_env()`）。因此凭证走文件:

- 位置 `<AIASSISTANT_HOME>/config/secrets/`,目录 **0700**、文件 **0600**
  （App 设置窗口与 `act/lib/secrets.write_secret` 两侧都强制）,整个目录 gitignored。
- 固定文件名与三级解析顺序（secrets 文件 → config 显式路径 → legacy 路径）见
  **CONTRACT §19**。推荐用 App 设置窗口粘贴保存,不要把 key 文件放在 `~/Desktop`
  （legacy 路径仅为兼容保留;Desktop 默认被 iCloud 同步,且 cron 的 TCC 授权覆盖不到那里）。

## 残余风险（诚实清单)

- 屏幕采集**不区分 app**：密码管理器、银行页面、无痕浏览的可见文本一样会进 vault 并出境。
  内置 secret 掩码只能兜住 API-key 形状的 token,兜不住你屏幕上的其他敏感内容。
  在敏感操作前把录制切 off,是目前最可靠的控制。
- Obsidian 雷达 prompt 尚未接入 redaction（见上文覆盖边界）。
- 质量门与「不对外发消息」约束是 prompt 级的,不是系统强制。
- 执行 agent 默认绕过权限确认(见「执行权限」——可用 `execution.skip_permissions: false` 换取
  逐操作确认)。

## 相关文档

- [`/SECURITY.md`](../SECURITY.md) — 安全漏洞上报渠道与范围
- [`docs/TELEMETRY.md`](TELEMETRY.md) — opt-in telemetry 细节
- [`docs/CONTRACT.md`](CONTRACT.md) — §4 执行派发、§19 凭证与 secrets
- [`docs/INSTALL.md`](INSTALL.md) — 安装与 TCC 授权
