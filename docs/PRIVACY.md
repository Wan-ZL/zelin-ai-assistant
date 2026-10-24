# Privacy — 数据流与信任模型

> 这是一个屏幕 + 音频录制、把录到的内容整理成笔记与会议纪要的个人助理（lite 2.0）。它的核心设计就是**周期性把你屏幕上的
> 文字与音频转写发给 Anthropic API 加工**。本文逐条列出每一个出境通道（触发 / 频率 / payload / 关闭开关）、什么永不出境、
> 本地保留多久、你有哪些控制。安全漏洞上报见 [`/SECURITY.md`](../SECURITY.md)。1.x 的雷达、执行派发、遥测、建议上报、
> 实时字幕等通道随 v2.0.0 退役（全文在 git tag `full`）。

## 信任模型

- **没有产品方服务器**。v2.0 起零遥测上传面（宪法第 9 条）：没有匿名统计、没有建议上报、没有任何去往维护者的流量。
  内容类出境只去 Anthropic API（你的 key）；唯一的非模型出网是每日一次的 GitHub 版本检查（可关）。
- **LLM 通道只有一种形状**：所有发往 Anthropic 的内容都经由官方 `claude` CLI 的 headless `claude -p`，并且都带 §78 的
  隔离常量——应用自己的 `CLAUDE_CONFIG_DIR`（`~/Library/Application Support/ZelinAIAssistant/claude-config/`）、repo 树之外的
  空 cwd、`--bare --no-session-persistence --setting-sources "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'`。效果：
  不加载你的 Claude Code hooks、插件、MCP server、CLAUDE.md 或项目设置；不往你的 `~/.claude/projects` 写会话记录；
  不读你的 Claude Code 登录或钥匙串（只用 API key）。应用**不读不写** `~/.claude` 与 `~/.claude.json`（宪法第 13 条）。
- **看板 server 只听 127.0.0.1**（CONTRACT §49），带 token 鉴权、无 CORS。

## Egress 清单：什么数据、何时、离开你的机器

| # | 通道 | 触发 / 频率 | 去向 | 默认 | 关闭开关 |
|---|------|------------|------|------|----------|
| 1 | Ingest 加工 | cron，每 30 分钟（inbox 为空时不调用） | Anthropic | 开（录制本身需你在 app 里打开） | 关录制 / 删 crontab 那一行 |
| 2 | 会议纪要 | 同一条 cron 链；一场 ≥10 分钟的会议结束后一次 | Anthropic（无工具、无 MCP，只出文字） | 开 | 设置「会议纪要」/ `recap.enabled: false` |
| 3 | 凭证验证 | 你在设置页保存或点「验证」key 时 | Anthropic（`GET /v1/models`，只带 key） | — | 不点即不触发 |
| 4 | 体检的 `claude auth` 行 | 你跑 doctor / 打开权限页时 | Anthropic（固定的极短 prompt，不含你的内容） | — | 不跑即不触发 |
| 5 | 更新检查 | server，至多每 24h 一次（ETag 缓存）；关于页「立即检查」手动触发同一请求 | GitHub releases API | 开 | 设置「自动检查新版本」/ `updates.check_enabled: false` |

另有两类**入向**下载，不带出你的数据：首次录制时 `npx screenpipe@0.3.349` 从 npm registry 下载录制引擎；「立即升级」
（§79）从 GitHub `git fetch` 新版本代码。

### 1. Ingest 加工 → Anthropic

- **触发**：install.sh 写入的唯一一条 crontab（`*/30`）：`screenpipe-export.sh` 把 `~/.screenpipe/db.sqlite` 里新增的屏幕文本与
  音频转写导出为 vault `1 - unprocessed/` 下的 markdown，`process-screenpipe.sh` 再起一个 headless `claude -p`（allowedTools：
  Read / Write / Edit / Bash / Glob / Grep；`--add-dir` 只给 vault）按 `/unprocessed-ingest` skill 加工这些文件。
- **Payload**：**全屏文本**（accessibility text + OCR，含 app 名、窗口名、时间戳）与**音频转写全文**；vault 自己的 `CLAUDE.md`
  （wiki 约定）作为系统提示附上。agent 读文件入 context 即出境。
- **注意**：claude 直接读文件，**词条 redaction 不经过此路径**。屏幕采集按 `recording.ignored_apps` 排除敏感 app（采集 +
  导出两层过滤），清单外的敏感内容仍会进 vault 并被加工（见「残余风险」）。
- **关闭**：在 app 里关录制（不再产生新数据），或 `crontab -e` 删掉那一行。

### 2. 会议纪要 → Anthropic（CONTRACT §63）

- **触发**：同一条 cron 链。`act/recap.py` 只读 `~/.screenpipe/db.sqlite` 做**确定性** session 判定（不调模型）；一场 ≥10 分钟、
  ≥3 帧会议窗口（Zoom / Teams / Meet / Webex / FaceTime / Slack Huddle）安静 5 分钟且转写落地后，才发**一次**模型调用
  （validator 不过再重试一次）。也可以在会议纪要页手动「生成」。
- **Payload**：该会议区间的**音频转写**（含他人发言；只在录制模式含音频时才存在）+ 14 天内最多 3 条往期纪要 + 你在页面上写的
  纠正备注，全部过 `fence_untrusted`，再经 `act/llm.py` 的出口 `sanitize.scrub`。argv 在隔离常量之上再带 `--tools ""`——模型
  拿不到任何工具或 MCP，**只能出文字**（`tests/test_recap_no_egress.py` 钉死）。
- **产物**：`state/recap/recaps/<key>.json`（纯文本纪要 + 元数据，不是任务、没有收件人字段），唯一出口是你在页面上点复制。

### 5. 更新检查 → GitHub releases API

- server 至多**每 24h 一次** GET `/releases/latest`（无鉴权，ETag 缓存；离线 / 限流静默保留缓存）。暴露的只有你的 IP 与
  User-Agent 里的当前版本号。发现新版只在关于页提示；「立即升级」只在你点击时运行（或你打开了 `updates.auto_install`）。
- **关闭**：设置 → 「自动检查新版本」，或 `updates.check_enabled: false`——关闭后不发任何请求。

## 什么永不离开你的 Mac

- **凭证**：`config/secrets/anthropic-api-key.txt`。内容永不打印、永不入日志（CONTRACT §19），出口 secret 掩码再兜一层。
- **screenpipe 原始数据**：`~/.screenpipe/db.sqlite` 与媒体文件本身不上传；出境的是 ingest / 纪要 prompt 里引用到的文本。
- **会议纪要与设置**：`state/` 下的一切。
- **redaction 词表**：只在本地做替换，词表与命中的原文永不出境。

## 本地数据与保留策略

| 数据 | 位置 | 保留 |
|------|------|------|
| 屏幕截图 / 音频媒体 | `~/.screenpipe/data/` | `recording.media_retention_minutes`（出厂 60 分钟，CONTRACT §72.4），每 30 分钟的 cleanup 步删除，回执 `state/screenpipe_prune.json` |
| 屏幕文本 + 音频转写 | `~/.screenpipe/db.sqlite` | `recording.retention_days`（出厂 0 = 永久；N ≥ 1 删「已导出进笔记库且早于 N 天」的行，§72.2） |
| a11y 树（accessibility 文本与元素） | 同上 | 每天一次清掉早于 1 天的（cleanup 步，§72.5），回执 `state/screenpipe_a11y_prune.json` |
| 导出 / 加工后的笔记 | Obsidian vault | 永久（你的 vault，你管理） |
| 会议纪要 | `state/recap/recaps/<key>.json`（转写本身不落这里） | `recap.retention_days`（默认 90 天）；忽略过的那份 14 天 |
| headless claude 的运行数据 | `~/Library/Application Support/ZelinAIAssistant/claude-config/` | Claude Code 自己管理（`--no-session-persistence`：不写会话记录）；`uninstall.sh` 删除 |

## 你有哪些控制

- **录制开关**：app 里随时切录制模式（关 / 仅屏幕 / 屏幕 + 音频）。fresh install 默认关，首次打开前有一次双语 consent。
- **敏感 app 排除清单**（`recording.ignored_apps`，设置页可改；默认：1Password / Bitwarden / LastPass / KeePassXC /
  Keychain Access + Safari「Private Browsing」/ Chrome「Incognito」窗口标题）。两层生效：采集阶段壳把每个词条以
  `--ignored-windows` 传给录制引擎（命中的窗口根本不会被截屏 / OCR / 写库）；导出阶段 `ingest/screenpipe-export.sh` 用同一
  清单过滤历史帧。
- **Redaction**（`act/lib/sanitize.py`）：内置 secret 形状掩码默认开（`redaction.mask_secrets`）；用户词表 opt-in
  （`redaction.enabled`）。覆盖边界：经 `act/llm.py` 的调用（会议纪要）；**不覆盖** ingest（claude 直接读文件）。
- **保留期旋钮**：上表三项，设置页「录制数据与磁盘」区。
- **会议纪要**：设置页「会议纪要」整条关掉。
- **更新**：版本检查与自动升级各一个开关（默认：检查开、自动升级关）。

## Secrets：为什么是文件

cron 与 launchd 下的进程读不到 Keychain，而每次 headless claude 都带 `--bare`（只认 `ANTHROPIC_API_KEY`），所以唯一的凭据是
一个 key 文件：`<repo>/config/secrets/anthropic-api-key.txt`，目录 **0700**、文件 **0600**，gitignored。解析顺序：进程环境 →
该文件 → `~/.config/anthropic-key.txt`（CONTRACT §19）。不要把 key 文件放在 `~/Desktop`（默认被 iCloud 同步）。

## 残余风险（诚实清单）

- 敏感 app 排除是**关键词子串匹配，不是语义识别**：银行页面开在普通浏览器标签里时，只有窗口标题恰好含关键词才会被排除；
  音频转写**不分 app**。清单只影响之后的采集与导出，已进 vault 的历史文本不会被追溯清除。敏感操作前关掉录制仍是最可靠的控制。
- ingest 的 agent 有 Bash / Write 工具、能写 vault；「不运行 git」「只按 skill 整理」是 prompt 级约束，不是系统强制。cwd 在 repo
  树外、`--setting-sources ""` 与 `--bare` 让它够不着 repo 与你的 Claude Code 配置，但它仍以你的用户身份运行。
- 内置 secret 掩码只兜得住 API key 形状的 token。

## 相关文档

- [`/SECURITY.md`](../SECURITY.md) — 安全漏洞上报渠道与范围
- [`docs/CONTRACT.md`](CONTRACT.md) — §19 凭证、§63 会议纪要、§72 保留期、§78 隔离与常驻足迹
- [`docs/INSTALL.md`](INSTALL.md) — 安装、TCC 授权、卸载
