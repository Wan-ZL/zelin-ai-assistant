# Gmail 捕获源

> **正常路径不需要这份文档** —— 打开菜单栏 App → **设置 → Gmail 接入**，三步在 App 里完成：
>
> 1. 点 **「打开 Google 应用专用密码页」**（要求账号已开两步验证；页面里 App name 随便填 → 创建）；
> 2. 在设置里填 **Gmail 地址** 并保存；
> 3. 把 Google 显示的 16 位密码**粘贴**进密码行 → 保存（空格自动去掉，**保存即做一次真实
>    IMAP 登录验证**），打开「启用 Gmail 雷达」开关即可。全程不用改任何文件。
>
> 下面的内容只服务**进阶与排错**：公司 Workspace 限制、密码解析顺序、YAML 手工配置。

> **⚠️ 公司 Google Workspace 大概率此路不通**：如果 App passwords 页面显示
> "The setting you are looking for is not available for your account"，说明管理员禁用了应用专用密码
> （部分企业 Workspace 实测如此）。不用再试。邮件捕获由 screenpipe 录屏链兜底
> （你读邮件的画面会进 Obsidian ingest → 雷达）。设置页在 IMAP 验证失败带出对应错误时，
> 也会用同一句人话直接告诉你。真·直连的将来选项：Gmail API OAuth（需 GCP 项目）
> 或 Mail.app 本地读取。gmail_radar 无密码文件时静默待机，无需关闭。

这套系统要用 IMAP 轮询你的 Gmail 收件箱未读邮件（**只读**，用 BODY.PEEK 拉取，
不会把邮件标成已读）。普通密码不行——Google 早已禁用 IMAP 明文密码登录，
需要一个 **应用专用密码（App Password）**，前提是账号开了 **两步验证**。

## 排错 / 手工验证

```bash
cd "${AIASSISTANT_HOME:-$HOME/Projects/zelin-ai-assistant}" && AIASSISTANT_HOME="$PWD" python3 -m act.radar_gmail --check
# 期望输出: {"ok": true, "address": "your.name@gmail.com"}
# 失败输出的 error 字段：no_address（没填地址）| auth_failed（密码/地址不对，
#   或 Workspace 管理员禁了 IMAP/应用密码）| connect_failed（网络）
```

通过后 gmailradar 的 launchd 每 5 分钟自动扫一次：收件箱里新的未读邮件会被
LLM 三选一（需要你处理 → 出提案卡 / 纯 FYI → 跳过）。noreply 发件人、
带退订头的 newsletter、日历"已接受"回执会被直接过滤，不进 LLM。

## 进阶（YAML 手工配置）

设置页写入的是 `state/settings_overrides.json`（优先级最高）；不用 App 也可以在
`config.yaml` 里手工配置（`sources.gmail` 节）：

```yaml
sources:
  gmail:
    address: "your.name@gmail.com"   # 推荐改在 App 设置里填（gmail_address override）
    enabled: true                    # 默认 true，可省略；App 的开关写 gmail_enabled override
    # app_password_path 可省略 —— 推荐在 App 设置窗口粘贴保存；
    # 填了则作为显式路径，优先级在 config/secrets/ 之后、旧默认路径之前。
```

## 备注

- **哪儿都找不到密码 = 整个雷达静默不跑**（no-op），不会报错刷日志。想临时停用，
  关掉设置里的「启用 Gmail 雷达」开关即可（也可改 `sources.gmail.enabled: false`
  或 feature flag `features.gmail_radar: false`）。
- 密码解析顺序（CONTRACT §19）：`config/secrets/gmail-app-password.txt` →
  config 显式 `app_password_path` → 旧默认 `~/Desktop/Keys/gmail-app-password.txt`（已 deprecated）。
- 撤销：https://myaccount.google.com/apppasswords 里删掉该条目即可让密码立刻失效。
- 处理进度记在 `state/gmail_radar.json`（最后处理的 IMAP UID），删掉它会让雷达
  从头重扫当前所有未读邮件（已建过卡的会被 registry 按标题去重合并）。
