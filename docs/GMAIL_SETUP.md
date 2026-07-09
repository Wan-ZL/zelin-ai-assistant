> **⚠️ 公司 Google Workspace 大概率此路不通**：如果 App passwords 页面显示
> "The setting you are looking for is not available for your account"，说明管理员禁用了应用专用密码
> （作者的公司账号实测如此，2026-07-07）。不用再试。邮件捕获由 screenpipe 录屏链兜底
> （你读邮件的画面会进 Obsidian ingest → 雷达）。真·直连的将来选项：Gmail API OAuth（需 GCP 项目）
> 或 Mail.app 本地读取。gmail_radar 无密码文件时静默待机，无需关闭。

# Gmail 捕获源：建应用专用密码（一次性，~2 分钟）

这套系统要用 IMAP 轮询你的 Gmail 收件箱未读邮件（**只读**，用 BODY.PEEK 拉取，
不会把邮件标成已读）。普通密码不行——Google 早已禁用 IMAP 明文密码登录，
需要一个 **应用专用密码（App Password）**，前提是账号开了 **两步验证**。

## 步骤

1. **生成密码**：直达 https://myaccount.google.com/apppasswords
   （要求账号已开两步验证；没开的话先去 安全 → 两步验证 开启，否则没有这个入口）。
   App name 填 `Zelin AI Engineer`（随意），点 **创建**。

2. **粘贴保存**：Google 会显示一个 16 位密码（形如 `abcd efgh ijkl mnop`），**只显示这一次**。
   立即打开菜单栏 app 的**设置窗口** → 凭证栏粘贴 → 保存（建议去掉空格）。
   （app 会写入 `config/secrets/gmail-app-password.txt`，目录 0700/文件 0600。
   旧路径 `~/Desktop/Keys/gmail-app-password.txt` 仍兜底可用，已有布置不受影响。）

3. **填 Gmail 地址**：在 `config.yaml` 里填地址（`sources.gmail` 节）：

   ```yaml
   sources:
     gmail:
       address: "your.name@gmail.com"   # 填你的 Gmail 地址
       enabled: true                    # 默认 true，可省略
       # app_password_path 可省略 —— 推荐在 App 设置窗口粘贴保存；
       # 填了则作为显式路径，优先级在 config/secrets/ 之后、旧默认路径之前。
   ```

## 验证

```bash
cd ~/Projects/zelin-ai-assistant && AIASSISTANT_HOME="$PWD" python -m act.radar_gmail --check
# 期望输出: {"ok": true, "address": "your.name@gmail.com"}
```

通过后 gmailradar 的 launchd 每 5 分钟自动扫一次：收件箱里新的未读邮件会被
LLM 三选一（需要你处理 → 出卡片待审批 / 纯 FYI → 跳过）。noreply 发件人、
带退订头的 newsletter、日历"已接受"回执会被直接过滤，不进 LLM。

## 备注

- **哪儿都找不到密码 = 整个雷达静默不跑**（no-op），不会报错刷日志。想临时停用，
  改 `config.yaml` 里 `sources.gmail.enabled: false` 或 feature flag
  `features.gmail_radar: false` 即可。
- 密码解析顺序（CONTRACT §19）：`config/secrets/gmail-app-password.txt` →
  config 显式 `app_password_path` → 旧默认 `~/Desktop/Keys/gmail-app-password.txt`。
- 撤销：https://myaccount.google.com/apppasswords 里删掉该条目即可让密码立刻失效。
- 处理进度记在 `state/gmail_radar.json`（最后处理的 IMAP UID），删掉它会让雷达
  从头重扫当前所有未读邮件（已建过卡的会被 registry 按标题去重合并）。
