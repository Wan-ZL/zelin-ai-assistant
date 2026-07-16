# Slack 捕获源

> **正常路径不需要这份文档** —— 打开菜单栏 App → **设置 → Slack 接入**，三步在 App 里完成：
>
> 1. 点 **「复制 App Manifest」**，再点 **「打开 api.slack.com/apps」** → Create New App →
>    From a manifest → 选你的 workspace → 切到 **JSON** 标签页粘贴 → Create；
> 2. 页面顶部 **Install to Workspace** 授权，然后在 OAuth & Permissions 复制
>    **User OAuth Token**（`xoxp-` 开头）；
> 3. 粘贴回设置里 → **保存即验证**，你的 Slack 身份（user id）自动填好，
>    要看的频道 / 关注的人直接**勾选**。全程不用改任何文件。
>
> 下面的内容只服务**进阶与排错**：管理员审批、MCP 兜底细节、手机端快速捕获、YAML 手工配置。

这套系统要读你的 **DM / 群 / 被 @提及**，需要一个 Slack **user token**（`xoxp-` 开头）。
Bot token（`xoxb-`）不行——它读不到你的私信、也不能用 search（设置页会直接拒收 `xoxb-`）。
所需权限已全部写在 manifest 里（设置页「复制 App Manifest」按钮 = 仓库的
`config/slack-app-manifest.json`），不用逐个点 scope。

> **报 "We can't translate a manifest with errors" 的修法**：对话框里切到 **JSON** 标签页再粘贴
> （Slack 的 YAML 解析器对注释/非 ASCII 字符很挑剔，JSON 版最稳；`config/slack-app-manifest.yaml`
> 是同内容的 YAML 版）。真正的公司限制不会长这样——若安装一步出现"需要管理员审批"，按流程提交等批准即可。

> **v0.14 起 manifest 增补了 `channels:read` / `groups:read`**（设置页的频道勾选器需要）；
> **v0.40 起增补了 `reactions:write`**（快速捕获的 emoji 回执需要——缺这个 scope 只会少回执，
> 捕获本身不受影响）。老版本建的 app 报 `missing_scope` 时：api.slack.com/apps → 你的 app →
> **App Manifest** → 粘贴新 manifest → Save → **Reinstall to Workspace**，token 会换新，
> 重新粘贴一次即可。

## 排错 / 手工验证

```bash
cd "${AIASSISTANT_HOME:-$HOME/Projects/zelin-ai-assistant}" && AIASSISTANT_HOME="$PWD" python3 -m act.radar_slack --check
# 期望输出: {"ok": true, "user": "your.name", "user_id": "U01234ABCDE", "team": "..."}
```

- 卡在**管理员审批**：走内部审批（IT 频道或 workspace admin），等 token 下来再做第 3 步；
  期间雷达自动走下方的 MCP 兜底扫描，不会干等。
- 已装过旧版 app 的：改 manifest 后需 **Reinstall to Workspace** 重新授权，token 会换新。
- token 保存在 `config/secrets/slack-user-token.txt`（目录 0700/文件 0600）；
  旧路径 `~/Desktop/Keys/slack-user-token.txt` 仍兜底可用，但已 deprecated。

通过后 slackradar 的 launchd 每 3 分钟自动扫一次；DM / 群 / 关注频道里 @你 且需要处理的
事会变成卡片。对外回复**只出草稿**，永远你自己发。

## 监控范围

- **DM + 群 DM**：全部消息都看（有人私你 = 大概率要你处理）。
- **频道**：只看你在 **设置 → Slack 接入** 里勾选的频道、且 **@你** 才建卡片（避免频道噪音）。
- **进阶（YAML 手工配置）**：没在设置里勾选过时，沿用 `config.yaml` 的
  `sources.slack_channels` / `watch_people`；设置里的勾选写入 `state/settings_overrides.json`，
  优先级最高。频道条目形如 `{id: C01234ABCDE, name: your-team-channel}`。

## 手机端快速捕获（self-DM）

在 Slack 手机 app 里给**自己**发消息（搜索自己的名字打开跟自己的 DM）。系统只把
"你发给你自己"的消息当快速捕获，其他 DM / 群 / 频道逻辑不变。这是**手机端的捕获入口**
（iOS app 上线前唯一的移动捕获方式）——只进不出：捕获后直接建卡进 App，助手不再往
self-DM 里回帖。

- **发文字 = 快速提案**：随手一句话，系统对照现有条目自动三选一——新想法建卡
  （直接进提案列）、在说已有条目就关联并追加备注、闲话忽略。
- **发图片/视频 = 拍照建任务**：白板、屏幕、纸条拍下来直接发；视频自动抽帧（≤12 帧）
  识别后走同样的三选一。图片存 `state/media/`。
- **回执（v0.40）**：捕获处理完后，你的那条消息上会出现一个 emoji reaction 作为回执——
  📥 已记下（新建卡 / 折进已有卡 / 挂了后续卡）、↩️ 你验收过的事回锅重新提案、
  🚫 判定无需行动（没建卡，觉得不对就换个更明确的说法再发一条）。只打 reaction、
  绝不回帖；token 缺 `reactions:write` scope 时回执静默缺席，捕获不受影响。
  关闭：`config.yaml` 里 `sources.slack_capture_receipts: false`。

> **审批只在 Mac App 里做**（v0.21 起）。旧版的 self-DM 指令（`批准/拒绝/打回/验收 R-xxx`）、
> ✅ reaction 一键批准、以及出站通知镜像到 self-DM 都已移除；iMessage 手机通道也整体退役。
> self-DM 现在纯做捕获（v0.40 起加上面的 emoji 回执，仍然不发任何消息）。

## Token 批下来之前：MCP 兜底扫描（v0.11，默认开）

第 2 步卡在管理员审批时雷达不会干等：检测不到 token 就自动切到兜底路径——用
headless claude 挂上你**用户级的 Slack MCP**（只读工具）扫一遍新消息，判断需要
你处理的走同一条建卡管线进「提案」列。token 一旦保存，原生 API 路径自动优先，
兜底自然停用，无需改任何配置。

- **频率**：每 30 分钟一轮真扫描（launchd 仍每 3 分钟触发，节流标记
  `state/slack_mcp.marker` 记录上次成功扫描的开始时刻，未到点静默跳过）。
- **覆盖窗口**：自上次成功扫描以来；首次回看 24 小时，封顶 48 小时——合盖几个
  小时再打开也不漏，失败的一轮不推进标记，下一轮自动重扫同一窗口。
- **范围**：发给你的 DM / 群 DM、@你 的消息、勾选的关注频道。
- **只读红线**：兜底代理只拿到 Slack 搜索/读频道/读 thread/查用户这组只读工具，
  发消息、草稿、reaction、canvas、定时消息一律不给。
- **开关与调频**（`config.yaml` 的 `sources` 段）：
  - `slack_mcp_fallback: false` — 关闭兜底（没 token 时回到原来的静默跳过）；
  - `slack_mcp_interval_minutes: 30` — 扫描间隔。
- **局限**：手机 self-DM 快速捕获（下载附件 / 建卡）要等 token 批下来才可用——
  兜底路径是只读的，不做这些**写**动作。
