# Slack 捕获源：三步接好（~2 分钟）

这套系统要读你的 **DM / 群 / 被 @提及**，需要一个 Slack **user token**（`xoxp-` 开头）。
Bot token（`xoxb-`）不行——它读不到你的私信、也不能用 search。
所需权限已全部写在仓库的 manifest 里，不用逐个点 scope。

> **报 "We can't translate a manifest with errors" 的修法**：对话框里切到 **JSON** 标签页，粘贴
> `config/slack-app-manifest.json` 的内容（Slack 的 YAML 解析器对注释/非 ASCII 字符很挑剔，JSON 版最稳）。
> 真正的公司限制不会长这样——若安装一步出现"需要管理员审批"，按流程提交等批准即可。

## 三步

1. **建 app（用 manifest，一次粘贴）**
   打开 https://api.slack.com/apps → **Create New App** → **From a manifest** →
   Workspace 选**你的公司 workspace** → 把 `config/slack-app-manifest.yaml` 的内容整个粘贴进去 → **Create**。

2. **安装授权**
   页面顶部 **Install to Workspace** → 授权。
   - ⚠️ 如果你的公司是 enterprise workspace，可能需要**管理员批准**才能安装。卡在审批时：
     走内部审批（IT 频道或 workspace admin），等 token 下来再继续第 3 步。
   - 已装过旧版 app 的：改用 manifest 后需 **Reinstall to Workspace** 重新授权，token 会换新。

3. **存 token**
   安装后回到 **OAuth & Permissions**，复制 **User OAuth Token**（`xoxp-...`），
   打开菜单栏 app 的**设置窗口** → 凭证栏粘贴 → 保存。
   （app 会写入 `config/secrets/slack-user-token.txt`，目录 0700/文件 0600。
   旧路径 `~/Desktop/Keys/slack-user-token.txt` 仍兜底可用，已有布置不受影响。）

## 验证

```bash
cd ~/Projects/zelin-ai-assistant && AIASSISTANT_HOME="$PWD" python -m act.radar_slack --check
# 期望输出: {"ok": true, "user": "your.name", "user_id": "U01234ABCDE", "team": "..."}
```

通过后 slackradar 的 launchd 每 3 分钟自动扫一次；DM / 群 / 关注频道里 @你 且需要处理的
事会变成卡片。对外回复**只出草稿**，永远你自己发。

## 监控范围

- **DM + 群 DM**：全部消息都看（有人私你 = 大概率要你处理）。
- **频道**：只有在 `config.yaml` 的 `sources.slack_channels` 里列出的频道、且 **@你** 才建卡片
  （避免频道噪音）。示例配置里是 <your-team-channel> 和 <your-weekly-report-channel>（周报所在），换成你自己的。
- 想加频道：把频道 ID 加进 `config.yaml` 的 `slack_channels`。

## 手机用法（self-DM = 指挥通道）

在 Slack 手机 app 里给**自己**发消息（搜索自己的名字打开跟自己的 DM）。系统只把
"你发给你自己"的消息当指令/捕获，其他 DM / 群 / 频道逻辑不变。

- **发文字 = 快速提案/指令**：随手一句话，系统对照现有条目自动三选一——新想法建卡
  （直接进待审批）、在说已有条目就关联并追加备注、闲话忽略，然后回你一条结果。
- **发图片/视频 = 拍照建任务**：白板、屏幕、纸条拍下来直接发；视频自动抽帧（≤12 帧）
  识别后走同样的三选一。图片存 `state/media/`。
- **回复审批指令**（精确格式，行首）：
  - `批准 R-007` — 批准该卡
  - `拒绝 R-007` — 拒绝（进回收站，可恢复）
  - `打回 R-007 <哪里要改>` — 验收打回，反馈**必填**
  - `验收 R-007` — 验收通过（归档 delivered）
- **点 ✅ = 批准**：系统发来的 🔔 通知消息（带 `#R-xxx`）上点 ✅（white_check_mark）
  即批准该需求，一次生效。

出站通知（新卡待审批 / 待验收 / 需输入 / 自动恢复放弃）也会发到这个 self-DM，
带 `#R-xxx`；🔔 / 🤖 开头的消息是系统自己发的，不会被再次捕获。

## Token 批下来之前：MCP 兜底扫描（v0.11，默认开）

第 2 步卡在管理员审批时雷达不会干等：检测不到 token 就自动切到兜底路径——用
headless claude 挂上你**用户级的 Slack MCP**（只读工具）扫一遍新消息，判断需要
你处理的走同一条建卡管线进"待审批"。token 一旦保存，原生 API 路径自动优先，
兜底自然停用，无需改任何配置。

- **频率**：每 30 分钟一轮真扫描（launchd 仍每 3 分钟触发，节流标记
  `state/slack_mcp.marker` 记录上次成功扫描的开始时刻，未到点静默跳过）。
- **覆盖窗口**：自上次成功扫描以来；首次回看 24 小时，封顶 48 小时——合盖几个
  小时再打开也不漏，失败的一轮不推进标记，下一轮自动重扫同一窗口。
- **范围**：发给你的 DM / 群 DM、@你 的消息、`sources.slack_channels` 关注频道。
- **只读红线**：兜底代理只拿到 Slack 搜索/读频道/读 thread/查用户这组只读工具，
  发消息、草稿、reaction、canvas、定时消息一律不给。
- **开关与调频**（`config.yaml` 的 `sources` 段）：
  - `slack_mcp_fallback: false` — 关闭兜底（没 token 时回到原来的静默跳过）；
  - `slack_mcp_interval_minutes: 30` — 扫描间隔。
- **局限**：手机 self-DM 指令/快速捕获、✅ 点选批准这些**写**动作不在兜底范围，
  要等 token 批下来才可用。
