# Telemetry（匿名使用统计——**默认开、默认含你输入的文本**，随时可关）

> **一句话披露**：本项目默认上传**匿名的功能使用事件与你输入进本 App 的文本**
> （快速捕获、提问、打回反馈、搜索词；每条截断 500 字符），用于驱动产品改进。
> **不上传**：AI 的回答、屏幕录制内容、邮件或 Slack 消息正文、文件内容、
> 任何密钥。关闭在 App 设置 →「产品改进计划」：总开关一键全停；只想关文本上传、
> 留下匿名行为统计，关「上传我输入的文本」那一个开关即可（或 config.yaml
> `telemetry.capture_input: false`）。

## 三个开关一览

| 开关 | 默认 | 含义 |
|------|------|------|
| `telemetry.enabled` | **开** | 总开关：关掉即完全停止上传 |
| `telemetry.level` | `detailed` | 行为事件粒度。`basic` / `detailed` **两档都只有事件元数据**（事件名/时间/页面/耗时/计数）、都不含内容文字；`detailed` 同时是下行 capture_input 的**前置档位**——切到 `basic` 会连带停掉输入文本上传 |
| `telemetry.capture_input` | **开** | 【内容开关】与 level=detailed 同时为真时（默认即如此），你**输入进本 App 的文本**（快速捕获、提问、打回反馈、搜索词等，见下方内容字段表）以原文记录并上传，每条截断 **500 字符**。**绝不**含 AI 的回答/模型输出、屏幕内容、第三方消息或密钥。设 `false` = 只留行为事件（两个开关缺一，文本就不收集） |

三个键都可在 config.yaml `telemetry:` 块或 App 设置「产品改进计划」里改
（App 写 `state/settings_overrides.json`，优先级最高，CONTRACT §15）。

## 收集什么

只上传 `state/analytics/events.jsonl` 里**已经在本机记录**的功能使用事件
（`act/lib/analytics.py` / Mac app `Analytics`）。本地 JSONL 永远是 source of
truth，上传只读不改不删。

### `basic` / `detailed`——只有事件元数据

| 字段 | 内容 | 示例 |
|------|------|------|
| `event` | 事件名（固定枚举，如 inbox_approve / dispatch / radar_scan） | `"dispatch"` |
| `client_ts` / `ts` | 事件时间（UTC） | `"2026-07-09T01:02:03Z"` |
| `device_id` | 装机时生成一次的随机 uuid4（`state/device_id`），不含任何个人信息 | `"5f3a…"` |
| `sid` | Mac app 单次运行的 8 位随机会话 id | `"ab12cd34"` |
| `app_version` / `v` | 版本号——两个 writer 在**写入端统一盖章**（python 侧 `act.__version__`，App 侧 bundle 版本），任何事件都带 | `"0.13.0"` |
| `source` | 事件来源渠道 | `"slack"` |
| `outcome` | 动作类事件的结果（`ok` \| `fail`）；目前带此字段的事件：`merge_apply`（合并建议落地）；历史事件见下方注 | `"ok"` |
| `failure` | `outcome="fail"` 时的失败分类 id（`act/lib/failures.py` 目录）；**只有 id，绝不含原始报错文本**，无法分类时整个字段缺席 | `"claude_auth_failed"` |
| 各事件自带的元数据 | req id、状态、布尔结果、计数、耗时秒数、字符**数**（不含字符本身）等（见 `props`，即事件原始记录） | `"req": "R-004"` |

**level 本身（basic / detailed 两档）都不携带内容数据**——内容字段全部由下一节
的 `capture_input` 开关独立控制；把 `capture_input` 关掉后，无论哪一档都只剩
上表的事件元数据。

**v0.18 新增行为事件与字段**（全部元数据、两档都上传；一并列全以便审计）：

| 事件 / 字段 | 内容 |
|-------------|------|
| `mw_section_dwell{from,to,seconds}` | 主窗口切页：从哪页到哪页 + 上一页停留秒数（含窗口在后台的时间，封顶 24h） |
| `mw_setting_change{key}` | 设置页改了**哪个键**（只有键名如 `language` / `features.digest` / `telemetry`，**绝不含新值**——路径/地址/阈值都留在本机） |
| `board_search{chars}` | 看板搜索：一次搜索会话结束时的关键词**长度**（词本身属内容，见下表） |
| `feature_first_reach{feature}` | 某功能（ask/capture/terminal/feedback/merge_review/composer/board_search）在本机**第一次**被用到——每装机每功能至多一条 |
| `dispatch.wait_s` | 批准 → 实际派发的等待秒数 |
| `review_promoted.exec_s` | 派发 → 交付的执行秒数 |
| `rework_launch.round` | 第几轮打回 |
| `radar_scan.secs` | 一轮雷达扫描耗时 |
| `card_action.has_comment` / `inbox_*.has_comment` | 该操作是否带了评论（布尔；评论文本属内容，见下表） |
| `capture_submit.chars` / `ask_submit.chars` / `inbox_capture.chars` / `capture_direct_run.chars` | 输入长度（只有**数字**） |
| `capture_submit.mode` / `composer_open.mode` | v0.34 add-only：值恒为 `"run"`，仅出现在运行中列的直接开跑输入框（缺席 = 提案输入框；source/trigger 词表不变） |

（v0.18 同时移除：首启页勾选框及其 `telemetry_consent` 事件——首启改为一行
披露 + 「详情与关闭在设置」链接，开关全部集中在设置页，写同一个 override 键。）

**v0.19 新增：生命周期里程碑事件**（全部元数据、两档都上传；每装机至多一条，
用于把"📊 Usage Insights"报告做成 install→配置→首卡→首批→首派 的激活漏斗）。
统一由**去重一次**的写法产出——App 侧 `Analytics.firstReach`（UserDefaults 标记，
`mac/Sources/Utils.swift`）、daemon 侧 `analytics.log_first`（`state/analytics/first/`
标记文件，`act/lib/analytics.py`）——同一里程碑重复触发只会落一次。**只带行为
字段（req id/计数），绝不含卡片标题/链接/摘要等内容。**

| 事件 | 端 | 触发 |
|------|----|------|
| `feature_first_reach{feature:"app_launch"}` | App | 本机**第一次**打开 App |
| `feature_first_reach{feature:"ingest_configured"}` | App | 第一次配好任一 ingest 源（Slack key 验过 / 录制授权 / Gmail 凭据存盘） |
| `milestone_first_card{req}` | daemon | 第一张需求卡进入 提案 lane（`registry.save()` 单一 choke，`req`=需求 id） |
| `milestone_first_approval{req}` | daemon | 第一次批准一张卡（`actd` approve 分支） |
| `milestone_first_delivery{req}` | daemon | 第一次成功派发执行（`executor` dispatch 成功处） |

> 报告侧（`scripts/insights_report.py`）另派生两个视图，**不新增任何事件**：
> retention（按 `client_ts` 推每装机的返访日）与 abandonment（配了源却没拿到首卡 /
> 某路径只用过一次）。这些聚合**只输出计数/比例**，device id 永不出现在报告里，
> 且当前跨所有装机的匿名 device 合并计（多用户共享部署会拉高漏斗/留存数，per-tenant
> 区分标记暂缓，属未来 sync/auth 设计）。

> **同表的例外行：`event="feedback"`（建议上报，CONTRACT §29）**。这不是
> telemetry 自动上传的事件，而是你在 App 里**点「提建议」主动发送**的用户报告，
> 复用同一张 `analytics_events` 表落库：其 `props` 含你的建议**全文**与所选卡片的
> **标题快照**——是内容数据，不在上面 basic 级"只有事件元数据"的承诺范围内
> （该承诺只覆盖 telemetry 自动上传的事件）。它也**不受** `telemetry.enabled`
> 开关与首启 consent 门限制（点发送即同意；fork 仍可用 `supabase_url: ""` 硬关）。
> 详见 [`docs/PRIVACY.md`](PRIVACY.md) 第 16 条。

**历史事件**：`meeting_action_items`（会后清单生成，带 `outcome`）——该功能已在
v0.14 从产品移除（发射端不复存在），维护者项目中已上传的历史数据仍然存在；
字段语义同上表，仅作解读旧数据用。

### `capture_input`（**默认开**，可单独关）——你输入的文本

`telemetry.capture_input: true` **且** `telemetry.level: detailed`（出厂默认
两者皆真）时，以下**用户输入的文本**字段以原文附在对应事件上，每条经
`analytics.clip(…, 500)` 截断到 **≤500 字符**；两个开关任一为假，这些字段
一律缺席：

| 字段 | 所在事件 | 内容 |
|------|----------|------|
| `text` | `inbox_capture`（App 快速捕获）/ `quick_capture`（Slack self-DM 快速捕获）/ `capture_direct_run`（运行中列直接开跑，v0.34，CONTRACT §34） | 你打的捕获原文 |
| `question` | `ask_submit` / `ask_answered` / `ask_feedback`（问问助手，CONTRACT §27） | 你输入的问题原文（**绝不含回答**或上下文 bundle） |
| `comment` | `card_action` / `inbox_*`（带评论的卡片操作） | 你打的评论/修改方向 |
| `feedback` | `rework_launch`（打回） | 你打的打回反馈 |
| `instruction` | `dispatch`（任务派发） | **你批准的需求标题**——且仅当卡片的**全部来源**都是你自己的快速捕获（provenance 白名单 quick / quick_capture）；雷达卡（邮件/Slack/会议/屏幕来源）与混合来源卡**一律没有**此字段，模型起草的 plan 也不再随行（`act/executor.py` `_USER_ORIGIN_CHANNELS`） |
| `query` | `board_search`（看板搜索） | 搜索关键词 |

（v0.18 同时**退役**：`review_promoted.summary`——交付摘要是**模型输出**的
节选，触碰下方红线，从 telemetry 整体移除，不是挪到本开关之后；该事件只剩
`exec_s` 等元数据。）

**红线（无论什么设置都不收集）**：AI 的回答/模型输出、屏幕录制文本、邮件与
Slack 的**消息正文**（第三方的私人通信）、文件内容、密钥。收集范围
**只限你亲手输入进本 App 的文字**——雷达从屏幕/Slack/邮件里**提取**的候选内容
不属于「你输入的文本」，永远不进 telemetry：radar_triage 事件是纯元数据，
雷达来源卡片的派发事件不带 instruction 字段（provenance 白名单，见上表），
带媒体附件的 quick capture 只记你打的文字、不记合成的图片提示与本地路径
（唯一例外是 quick capture 本身：你在自己的 self-DM 线程里**亲手打给自己**的
那句话，它就是你的输入）。这条边界由 `tests/test_telemetry_level.py` 的
boundary guard 锁死。**密钥双保险**：每个内容字段在写入本地日志前都先过内置
密钥掩码（`act/lib/analytics.clip_content` / Swift `Analytics.clip`，模式同
`act/lib/sanitize._SECRET_PATTERNS`）——这层掩码**无条件生效**，与 redaction
配置无关；sk-ant-/xox*/AKIA/gh*_/PEM 形状即使被打进捕获或提问里也会以
`[脱敏]` 出现。

这些字段在 emit 端 gate：关掉 `capture_input`（或把 level 切回 basic）后，
新的文本**根本不会写进本地 events.jsonl**，自然也永远不会上传（关掉前已记录、
尚未上传的少量行仍会随行为统计发出；要连这些也清掉可删
`state/analytics/events.jsonl`）
（`act/lib/analytics.content_gate` / `Telemetry.contentCaptureActive`，
测试 `tests/test_telemetry_level.py` 锁死）。关掉后的问问助手事件只剩事件名 +
结果元数据（ok/耗时/failure_id、👍/👎 verdict、字符数），没有问题文本。

## 默认开 + 关闭路径

`telemetry.enabled` 默认 `true`，上传目标默认是**维护者的** Supabase 项目
（`https://vlxshwmdjpaxmcwbhutb.supabase.co`），用内置的 publishable key 写入。
该 key 是**公开设计**的（Supabase publishable key）：数据库 RLS 只给它 INSERT
权限——它能写入事件，**读不回任何数据**（select/update/delete 全部拒绝，读取
只有维护者的 service key 可以）。

关闭（任选其一，立即生效）：

1. **只关文本、留匿名行为统计**：App 设置 →「产品改进计划」→ 关掉「上传我
   输入的文本」；或 config.yaml：

   ```yaml
   telemetry:
     capture_input: false
   ```

2. **全部关掉**：同一设置区关掉最上方的总开关（写
   `state/settings_overrides.json` 的 `"telemetry": {"enabled": false}`，
   优先级最高）；或 config.yaml：

   ```yaml
   telemetry:
     enabled: false
   ```

全部关掉后 `python3 -m act.analytics_sync --once` 静默退出，什么都不发。本地
`state/analytics/events.jsonl` 照常记录（那是本机功能，供 `python -m act.report`
自查用）；不想让 App/daemon 在本地记事件，可关 feature flag `analytics`。

## 上传何时发生

install.sh 会在 crontab 里加一行每小时的 sync（`17 * * * * … python3 -m
act.analytics_sync --once`）。没跑过 install.sh 就没有定时上传——可手动跑
`python3 -m act.analytics_sync --once`。关闭 telemetry 后这行 cron 变成静默
no-op，不必删除。

另有一道 **consent 门**（堵住「cron 先装好、披露界面还没出现」的窗口）：以下
三者**全部缺席**时，`act.analytics_sync` 什么都不上传，只在日志里写一行
"waiting for first-run consent surface"：

1. 标记文件 `state/telemetry_consent_shown`——App「权限体检」页/设置向导第一次
   **展示**「匿名使用统计」披露行时写入（内容为时间戳），与你是否点开设置无关；
2. config.yaml 里显式写了 `telemetry:` 块（显式配置 = 知情同意）；
3. `state/settings_overrides.json` 里有 telemetry 键（在 App 里动过开关）。

也就是说：哪怕 install.sh 已经装好 cron，在你第一次看到披露界面（或显式配置过
telemetry）之前，不会有任何事件离开本机。

**内容另有一道 v2 consent 门（v0.18）**：输入文本字段除双开关外还要求
标记文件 `state/telemetry_consent_shown_v2`——只在**首启披露行**（明说含你
输入文本的那份文案，含设置向导里的同款）真正渲染时写入；或者
`telemetry.capture_input` 被**显式**写进 config.yaml / overrides（写下这个键
本身就是知情选择——设置页「上传我输入的文本」开关被亲手切动过一次即会显式
落键，且此后不会被无关保存 diff 掉）。设置页**不会**被动写标记（页面打开
不等于那一节真的被看到）。从 v0.18 之前升级上来的安装只有旧标记（写它时的
文案还说"不含个人文本"）——行为遥测照旧流动，但**内容一个字都不会上传**，
直到新披露真正出现过或你亲手切过那个开关（`act/lib/analytics.content_gate`，
测试锁死）。

## 聚合数据的公开发布

上传的事件会以**聚合形式**公开：GitHub Actions（`.github/workflows/insights.yml`）
**每天**把聚合报告写进本仓库一个公开的置顶 issue（「📊 Usage Insights」——只更新
这一个 issue，不会每次新开）。报告只含聚合值：按事件/日期/版本/级别的**计数**、
错误率、去重设备**数**（`scripts/insights_report.py`）——**绝不**出现原始事件行、
device id 或任何 capture_input 内容文本。事件总量没有变化的日子跳过更新。

## 容量预算（Supabase free tier）

粗算（单台重度使用的机器）：

- **行为事件量**：常驻 radar/launchd 心跳（radar_skip/radar_scan/telemetry_sync
  等）约 300–600 条/天 + 交互事件（导航/卡片操作/设置）约 100–300 条/天，
  合计 **≤1000 条/天 ≈ 3 万条/月**。轻度使用（不开录制、少交互）约十分之一。
- **单条体积**：行为事件 JSONL 一般 150–300 字节；落库含 props(jsonb)+索引
  开销按 ~600 字节/行估。**一台重度机器 ≈ 18 MB/月**。
- **输入文本增量（默认开）**：内容字段 ≤500 字符/条，且只挂在少数用户主动
  动作的事件上（捕获/提问/打回/搜索，一般几十条/天）——**≪ 5 MB/月**，
  合计仍 **< 25 MB/月/台**。
- **free tier 头寸**：500 MB 数据库 ≈ 单台重度机器 2 年以上；多台按台数线性。
  维护者侧的运维约定：每月看一眼 insights issue 的总量曲线，DB 超过 ~300 MB
  时用 service key 把 90 天前的原始行聚合归档后删除（RLS 不影响 service key）。
  高频心跳事件若成为主要噪音，优先在发射端降频/采样，而不是加大库。

## 更新检查（GitHub API，与 telemetry 上传无关）

除 telemetry 外，产品还有一条独立的轻量网络请求：**应用内更新检查**
（CONTRACT §26，`act/lib/update_check.py`）。actd 至多**每 24h 一次** GET
`https://api.github.com/repos/Wan-ZL/zelin-ai-assistant/releases/latest`
（无鉴权，带 ETag 缓存——版本没变时 GitHub 返回 304，几乎零流量）。
「关于」页的「立即检查」按钮手动触发**同一条请求**（跳过 24h 间隔，但请求
内容与对端完全相同，且开关关闭时按钮同样不发任何请求）。

**这次请求暴露什么**：你的 **IP 地址**（任何 HTTP 请求都会）+ User-Agent 里的
**当前版本号**（`zelin-ai-assistant/<version> (update-check)`）。仅此而已——
没有 device id、没有事件、没有任何内容数据；对端是 GitHub，不是本项目的
收集端，维护者**看不到**这些请求。

离线/限流时静默保留上次结果（同样计入 24h 预算，绝不重试风暴）。发现新版只在
菜单栏菜单与「关于」页低调提示一行，点击**只打开 release 页**——绝不自动下载
或安装。

关闭（任选其一）：App 设置 → 通用 →「自动检查新版本」；或 config.yaml：

```yaml
updates:
  check_enabled: false
```

关闭后不再发出任何请求（已缓存的旧结果也不再提示）。

## Fork 用户须知（重要）

- fork 里**不改配置**的话，telemetry 仍指向维护者的 Supabase 项目——你 fork
  的用户的数据会传给本项目维护者。发布你自己的 fork 前请二选一：
  - 换成你自己的项目：`telemetry.supabase_url` 指向你的 Supabase，跑
    `supabase/migrations/` 建表 + INSERT-only RLS policy，把你的 publishable
    key 放进 `config/secrets/supabase-service-key.txt` 或 `telemetry.key_path`
    指的文件（key 文件存在时**优先于**内置 key）；
  - 或者彻底禁用：config.yaml 里把 URL 置空——`supabase_url: ""` 时上传逻辑
    整体短路，等同于没有这个功能。
- 自建收集端（用你自己的 service key）的老配置完全不受影响：key 文件仍然
  优先，`supabase_url` 显式配置的值原样生效。

## 实现要点

- 上传器 `act/lib/analytics_sync.py`：stdlib urllib，POST
  `{supabase_url}/rest/v1/analytics_events`，批 ≤500 条。
- key 解析顺序：`config/secrets/supabase-service-key.txt` → `telemetry.key_path`
  指向的文件 →（都没有时）内置 publishable key。
- 游标 `state/analytics_sync.json`（按文件记字节偏移，.tmp+rename 原子写），每批
  上传成功后立即落盘——append 场景精确一次，半行留给下次。
- 设备号 `state/device_id`：装机时生成一次的 uuid4，不含任何个人信息。
- 每次运行以 `telemetry_sync` 事件自报 ok/fail + 计数（心跳：坏死可见）。
- 服务端表结构与 RLS 见 `supabase/migrations/`（RLS 开；anon 仅 INSERT）。
