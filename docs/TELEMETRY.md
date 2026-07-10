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
| `app_version` / `v` | 版本号 | `"0.12.0"` |
| `source` | 事件来源渠道 | `"slack"` |
| 各事件自带的元数据 | req id、状态、布尔结果、计数等（见 `props`，即事件原始记录） | `"req": "R-004"` |

**basic 级绝不包含内容数据**：没有 prompt、没有指令摘要、没有消息正文、没有
文件内容，更没有密钥。

### `detailed`（**opt-in**，需你主动打开）——basic + 简短摘要

在 basic 的全部字段之上，额外允许：

| 字段 | 所在事件 | 内容 |
|------|----------|------|
| `instruction` | `dispatch`（任务派发） | 给 claude 的指令摘要：需求标题 + 计划开头，**≤200 字符**（绝不含完整 prompt 或围栏内的源材料） |
| `summary` | `review_promoted`（任务交付） | 交付摘要节选，**≤200 字符** |

这些字段在 emit 端 gate：级别是 `basic` 时**根本不会写进本地 events.jsonl**，
自然也永远不会上传。切到 `detailed` 才开始记录。这两个字段可能包含你任务
标题/计划里的文字，所以它是更敏感的级别——默认不开。

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
