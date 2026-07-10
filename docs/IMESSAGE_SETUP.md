# iMessage 手机通道：给没有 Slack 的用户

> **English** — Normal path: configure everything inside the app's Settings
> window ("iPhone via iMessage" section — one toggle, a handle field with
> inline validation, guided Full Disk Access steps with a copy-path button,
> live status rows, and a test-message button). The rest of this document is
> the **advanced / troubleshooting reference** for manual setup.

## 正常路径：全部在 App 里完成（推荐，不用改任何文件）

打开菜单栏 App → 主窗口 → **设置** → **「iPhone 联动（iMessage）」**：

1. 填**你自己的**手机号（国际格式，如 `+14155551234`）或 Apple ID 邮箱 → 打开开关。
   App 会自动装好后台雷达（launchd），无需跑 install.sh。
2. 按界面上的**「完全磁盘访问」步骤**给 python 授权——界面直接给出要添加的
   python 路径（带「复制路径」按钮）和「打开系统设置」直达按钮。
3. 点**「立即测试一轮」**看状态变绿；点**「发送测试消息」**确认发送链路。
   每一步失败都有大白话原因和修法，跟着界面走即可。

下面的内容是**进阶 / 排障参考**（手工配置、实现原理、故障对照表），正常情况无需阅读。

---

## 这是什么（原理）

没有 Slack？用 iMessage 的 **"给自己发消息"** 线程做手机端指挥通道（CONTRACT §13
通道可插拔）。功能与 Slack self-DM 完全对等（文法、inbox 决策文件一字不差）：

- 手机上回 `批准 R-xxx` / `拒绝 R-xxx` / `打回 R-xxx <反馈>` / `验收 R-xxx` → 写入审批队列
- 随手发的其他文字 → quick capture 三选一（新卡 / 关联已有条目 / 忽略），结果回复到线程里
- 每条 macOS 通知同时镜像成一条 🔔 iMessage（含 `#R-xxx`）；对它点 **👍 或 ❤️ tapback** = 批准
  （👎/哈哈/!!/? 不会批准任何东西——与 Slack 只认 ✅ 同一条红线）

实现：`act/radar_imessage.py` 每 3 分钟**只读**轮询 `~/Library/Messages/chat.db`
（sqlite `mode=ro`，无法写入 Apple 的库）；回复/镜像经 osascript → Messages.app 发给
**你自己**（永远不会给别人发消息）。隐私细节见 [`PRIVACY.md`](PRIVACY.md) 第 11 条。

## 手工配置步骤（进阶——正常情况用上面的 App 设置即可）

1. **确认 Messages 可用**：这台 Mac 的 Messages.app 已登录你的 iMessage 账号
   （设置 → 已启用"信息"的 Apple ID）。手机号要能收发的话，iPhone 上开
   信息 → 短信转发（Text Message Forwarding）勾选这台 Mac。

2. **建"给自己发消息"线程**：Messages.app 新建对话，收件人填**你自己的**手机号或
   iCloud 邮箱，随便发一条。手机端同样能看到这个线程（iOS 16+ 原生支持
   "message yourself"）。记下你用的那个 handle——下一步要填、且必须**完全一致**。

3. **改 config.yaml**：

   ```yaml
   phone_channel: imessage
   imessage:
     self_handle: "+14155551234"   # E.164 手机号或 iCloud 邮箱，与第 2 步线程的收件人一致
   ```

   注意：App 设置窗口写的是 `state/settings_overrides.json`（`phone_channel` /
   `imessage_self_handle` 两个键），**优先级高于 config.yaml**——两处都设过时以
   App 里的为准。

4. **给 python 授 Full Disk Access（必须）**：chat.db 在 `~/Library/Messages` 下，
   受 TCC 保护——launchd 跑的是 python 二进制本身，所以 FDA 必须授给**那个 python**，
   授给 Terminal 没用。
   - 看雷达用哪个 python：`cat config/runtime.json`（install.sh 写入，通常是
     `~/miniconda3/bin/python3`）。
   - 系统设置 → 隐私与安全性 → **完全磁盘访问权限** → `+` → ⌘⇧G 输入上面那个
     python3 的路径 → 添加并打开开关。
   - 注意加的是**真实二进制**：如果那是个 symlink，先 `readlink -f` 找到真身再加。

5. **重跑 `./install.sh`**：step 5 只有在 `phone_channel: imessage` 时才会渲染并加载
   `com.zelin.aiassistant.imessageradar`（180 秒一轮）。改回 `none`/`slack` 再重跑
   则会自动卸载它。（App 设置里的开关做的是同一件事——开=渲染+加载，关=卸载+删除，
   两条路径等价、可混用。）

6. **首次发送授权**：第一条回复发出时 macOS 会弹 "python 想要控制 Messages" 的
   自动化（Automation）授权——点允许。launchd 下弹不出来的话，手动跑一次
   `python3 -m act.radar_imessage --once` 触发。

## 验证

```bash
cd "${AIASSISTANT_HOME:-$HOME/Projects/zelin-ai-assistant}" && AIASSISTANT_HOME="$PWD" python3 -m act.radar_imessage --check
# 期望: phone_channel: imessage / self_handle: +1415... / self chat: [<id>]
# "chat.db unreadable" = FDA 没授对 python；"self chat: NOT FOUND" = 先给自己发一条消息
```

然后在手机上给自己发一条 `批准 R-999` 之类，等一轮（≤3 分钟），应收到
`🤖 收到：批准 R-999（已写入处理队列）` 的回复（`state/inbox/` 里出现决策文件）。

## 限制（诚实清单）

- **仅 macOS**，且 chat.db 的表结构是 Apple **私有实现**——大版本更新可能改 schema。
  雷达对一切数据库错误做静默降级（`state/radar_health.json` 里记 skip_reason，
  绝不 crash），但 schema 大改后需要适配。
- 新 macOS 常把正文存进 `attributedBody`（二进制 typedstream）而非 `text` 列，
  解析是 best-effort 启发式——极端富文本消息可能解不出文字（该条会被跳过）。
- **v1 只支持文字**：图片/视频附件不处理（Slack 通道保留完整的图片/拆帧路径）。
  只发附件不带字的消息会被静默跳过。
- 镜像经 Apple 的 iMessage 服务中转（发给你自己）；不想要任何手机通道就保持默认
  `phone_channel: none`。

## 故障排查

| 症状（`state/radar_health.json` 的 skip_reason） | 原因 / 处理 |
|---|---|
| `disabled` | `phone_channel` 不是 `imessage` |
| `no_self_handle` | config 缺 `imessage.self_handle` |
| `db_missing` | 这台 Mac 的 Messages 从未启用（没有 chat.db） |
| `db_open_failed` / `db_read_failed` | 十有八九是 FDA 没授给雷达的那个 python（第 4 步） |
| `self_chat_not_found` | handle 与线程收件人不一致，或还没给自己发过消息（第 2 步） |
| 收不到 🤖 回复但 inbox 有文件 | osascript 自动化授权没点允许（第 6 步），或 Messages 没登录 |
