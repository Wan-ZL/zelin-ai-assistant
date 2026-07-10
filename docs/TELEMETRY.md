# Telemetry（匿名使用统计——**默认开**，随时可关）

> **一句话披露**：本项目默认上传**匿名的功能使用事件**（像 VS Code 一样默认开启），
> 用于驱动产品改进。**不上传**屏幕内容、消息正文、文件内容或任何密钥。
> 关闭只需一步：App 设置 →「产品改进计划」把开关关掉（或 config.yaml 里
> `telemetry.enabled: false`）。

## 收集什么（按级别）

只上传 `state/analytics/events.jsonl` 里**已经在本机记录**的功能使用事件
（`act/lib/analytics.py` / Mac app `Analytics`）。本地 JSONL 永远是 source of
truth，上传只读不改不删。

两个收集级别（`telemetry.level`，默认 `basic`）：

### `basic`（默认）——只有事件元数据

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
| 各事件自带的元数据 | req id、状态、布尔结果、计数等（见 `props`，即事件原始记录） | `"req": "R-004"` |

**basic 级绝不包含内容数据**：没有 prompt、没有指令摘要、没有消息正文、没有
文件内容，更没有密钥。

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

### `detailed`（**opt-in**，需你主动打开）——basic + 简短摘要

在 basic 的全部字段之上，额外允许：

| 字段 | 所在事件 | 内容 |
|------|----------|------|
| `instruction` | `dispatch`（任务派发） | 给 claude 的指令摘要：需求标题 + 计划开头，**≤200 字符**（绝不含完整 prompt 或围栏内的源材料） |
| `summary` | `review_promoted`（任务交付） | 交付摘要节选，**≤200 字符** |
| `question` | `ask_answered` / `ask_submit` / `ask_feedback`（问问助手，CONTRACT §27） | 你输入的问题原文，**≤200 字符**（绝不含回答或上下文 bundle） |

这些字段在 emit 端 gate：级别是 `basic` 时**根本不会写进本地 events.jsonl**，
自然也永远不会上传。切到 `detailed` 才开始记录。这些字段可能包含你任务
标题/计划/提问里的文字，所以它是更敏感的级别——默认不开。basic 级的问问助手
事件只有事件名 + 结果元数据（ok/耗时/failure_id、👍/👎 verdict），没有问题文本。

## 默认开 + 两条关闭路径

`telemetry.enabled` 默认 `true`，上传目标默认是**维护者的** Supabase 项目
（`https://vlxshwmdjpaxmcwbhutb.supabase.co`），用内置的 publishable key 写入。
该 key 是**公开设计**的（Supabase publishable key）：数据库 RLS 只给它 INSERT
权限——它能写入事件，**读不回任何数据**（select/update/delete 全部拒绝，读取
只有维护者的 service key 可以）。

关闭（任选其一，立即生效）：

1. **App 设置**：主窗口 → 设置 →「产品改进计划」→ 关掉「参与匿名使用统计」
   （写 `state/settings_overrides.json` 的 `"telemetry.enabled": false`，
   优先级最高）。
2. **config.yaml**：

   ```yaml
   telemetry:
     enabled: false
   ```

关闭后 `python3 -m act.analytics_sync --once` 静默退出，什么都不发。本地
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

1. 标记文件 `state/telemetry_consent_shown`——App「权限体检」页第一次**展示**
   「匿名使用统计」块时写入（内容为时间戳），与你勾不勾选无关；
2. config.yaml 里显式写了 `telemetry:` 块（显式配置 = 知情同意）；
3. `state/settings_overrides.json` 里有 telemetry 键（在 App 里动过开关）。

也就是说：哪怕 install.sh 已经装好 cron，在你第一次看到披露界面（或显式配置过
telemetry）之前，不会有任何事件离开本机。

## 聚合数据的公开发布

上传的事件会以**聚合形式**公开：GitHub Actions（`.github/workflows/insights.yml`）
**每天**把聚合报告写进本仓库一个公开的置顶 issue（「📊 Usage Insights」——只更新
这一个 issue，不会每次新开）。报告只含聚合值：按事件/日期/版本/级别的**计数**、
错误率、去重设备**数**（`scripts/insights_report.py`）——**绝不**出现原始事件行、
device id 或任何 detailed 级摘要文本。事件总量没有变化的日子跳过更新。

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
