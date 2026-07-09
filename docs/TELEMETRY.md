# Telemetry（可选，默认关）

## 收集什么

只上传 `state/analytics/` 里**已经在本机记录**的功能使用事件（`act/lib/analytics.py` /
Mac app `Analytics`，HANDOFF 决策 15）：事件名、时间戳、sid、版本号、来源等元数据。
**不含任何内容数据** —— 没有 prompt、消息正文、文件内容，更没有密钥（events.jsonl
本来就只记事件元数据）。本地 JSONL 永远是 source of truth，上传只读不改不删。

## 默认关闭

不配置就什么都不发生：`telemetry.enabled` 默认 `false`，`python3 -m act.analytics_sync --once`
静默退出。没有共享的收集端 —— 开启即意味着传到**你自己的** Supabase 项目。

## 如何开启

1. 建一个自己的 Supabase 项目，执行 `supabase/migrations/20260709000000_analytics_events.sql`
   建表（RLS 开、无 policy —— 只有 service_role key 可读写）。
2. service key 存入 `config/secrets/supabase-service-key.txt`（0600；解析顺序同
   CONTRACT §19，也可用 `telemetry.key_path` 指定路径）。
3. config.yaml 打开：

   ```yaml
   telemetry:
     enabled: true
     supabase_url: "https://xxxx.supabase.co"
   ```

4. 手动跑一次验证：`python3 -m act.analytics_sync --once`；定时上传可加一行 crontab
   （同 CONTRACT §18 的 cron 注意事项，绝对路径）：

   ```
   17 * * * * cd <repo> && AIASSISTANT_HOME=<repo> <python3> -m act.analytics_sync --once >> <repo>/state/analytics_sync.log 2>&1
   ```

## 实现要点

- 游标 `state/analytics_sync.json`（按文件记字节偏移，.tmp+rename 原子写），每批
  （≤500 条）上传成功后立即落盘 —— append 场景精确一次，半行留给下次。
- 设备号 `state/device_id`：装机时生成一次的 uuid4，不含任何个人信息。
- 每次运行以 `telemetry_sync` 事件自报 ok/fail + 计数（决策 15 心跳：坏死可见）。
