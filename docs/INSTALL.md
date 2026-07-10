# INSTALL — 安装指南

首次安装的**唯一权威文档**。两条路线:**.pkg 安装包**(新机器推荐)或**从源码**(开发者路线);装完后都走同一套"装完之后"步骤。每一步末尾有一个 ✅ **预期状态** checkpoint——到不了预期状态就先去 [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md),别带病继续。

## English quickstart

1. Prerequisites: macOS 14+, Xcode / Swift 6.x toolchain, [Claude Code CLI](https://claude.com/claude-code) + an Anthropic API key, Python 3.9+ with PyYAML, Node.js LTS (`npx` — the capture engine runs via `npx screenpipe`). Optional: Obsidian, `gh` CLI.
2. `git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant` (several defaults resolve this path; if you clone elsewhere, export `AIASSISTANT_HOME=<path>` everywhere the pipeline runs).
3. `cd ~/Projects/zelin-ai-assistant && cp config.example.yaml config.yaml`, then edit the Obsidian vault path and watched people.
4. `bash install.sh` — dependency checks, builds + installs the app, loads launchd agents, installs the cron chain. Idempotent; safe to re-run.
5. First launch is blocked by Gatekeeper (unsigned build): right-click the app in /Applications → Open. On macOS Sequoia+, also System Settings → Privacy & Security → "Open Anyway". Once open, the app shows a first-run **permissions & setup page**: answer the single screen-recording consent (recording defaults to **screen-only** — audio is a separate opt-in in Settings → Recording), grant Screen Recording / Notifications from the live checklist, and adjust the anonymous-usage-stats checkbox. Reopen it anytime via the app menu → **Permissions Checkup**.
6. Menu-bar app → Settings → paste your Anthropic API key (headless `claude` under cron/launchd cannot read Keychain OAuth; the key is stored as a `0600` file in `config/secrets/`).
7. Grant permissions in System Settings → Privacy & Security: **Screen Recording** and **Microphone** for the app; **Full Disk Access** for `/usr/sbin/cron` (click "+", press ⌘⇧G, type `/usr/sbin/cron`).
8. Expected state: the popover header says the dashboard was generated **≤10 s ago**. Then try the "first card in 5 minutes" exercise below (⌥Space → type a small task → ✅ → a reviewable draft arrives minutes later).
9. Anything off at any step: `bash install.sh --check` (= `python3 -m act.doctor`) re-validates the whole chain — deps, key resolution, launchd agents actually alive, cron lines, dashboard freshness — one `ok/warn/FAIL` line per check with the exact fix.

No API key yet? `python3 scripts/demo_seed.py /tmp/assistant-demo` previews the entire UI with fictional data — see [docs/DEMO.md](DEMO.md).

## 前置条件

| 组件 | 版本 | 用途 / 自检命令 |
|---|---|---|
| macOS | **14+** | app、launchd/cron 定时、TCC 权限模型 |
| Xcode / Swift toolchain | **6.x** | 构建 menu-bar app;`swiftc --version`。旧 toolchain 会死在 main-actor isolation 编译错(CI 同款下限,见 `.github/workflows/ci.yml` 注释) |
| [Claude Code CLI](https://claude.com/claude-code) | 最新版 | 雷达提取、提案扩写、执行全靠它;`claude --version` |
| Anthropic API key | — | headless 运行必需(为什么订阅不够用,见下方[认证模型](#认证模型api-key-vs-promax-订阅)) |
| Python | **3.9+** 与 PyYAML | actd / 雷达 / digest;`python3 -c "import yaml"` |
| Node.js | LTS(含 `npx`) | 录制引擎经 `npx screenpipe` 自动运行,**无需单独安装 screenpipe**;`npx --version`;缺失时 `brew install node` |
| Obsidian(可选,推荐) | — | vault 是雷达扫描源与 wiki 落点 |
| `gh` CLI(可选) | — | draft-PR 交付 |

## 认证模型(API key vs Pro/Max 订阅)

claude CLI 有两套互不相通的凭据,而这个项目的不同组件用到的**不是同一套**——装完后"终端里跑得通、cron 却静默死"几乎都源于此:

| 认证方式 | 是什么 | 谁能用 | 计费 |
|---|---|---|---|
| **OAuth(Pro/Max 订阅)** | `claude` 登录后存进 macOS Keychain 的订阅凭据 | 只有你 GUI 登录会话里的进程(交互终端等) | 计入订阅额度,不另付费 |
| **API key(`sk-ant-…`)** | [console.anthropic.com](https://console.anthropic.com) 签发的 metered key,以文件形式存放 | 任何进程,包括 cron / launchd 的 daemon session | 按 token 计费(API 账单) |

**为什么 headless 必须用 key 文件**:cron 和 launchd agents 跑在 daemon session 里,macOS 有两层沙箱把 OAuth 挡死——Keychain 只对 GUI 会话开放,而 `launchctl asuser` 也无法从 cron 的 daemon audit session 桥接过去(细节见 `ingest/process-screenpipe.sh` 顶部注释块)。因此:

- **需要 key 文件**的组件(全部 headless,under cron/launchd):ingest 链(`process-screenpipe.sh`)、各 radar、每周 digest、actd 派发的 `claude --bg` 执行。
- **订阅凭据就够**的场景:你在终端手动跑 `claude`(交互 session 读得到 Keychain)。

**ingest 的 fallback 行为**(其余 headless 组件同理):按 CONTRACT §19 顺序解析 key——`config/secrets/anthropic-api-key.txt` → 旧路径 `~/.config/anthropic-key.txt`;**两个文件都没有时不会立刻失败**,而是回落到 claude CLI 自己存储的凭据试跑。在常年保持登录的 Mac(如 Mac mini)上这条兜底路经常能通(此时计入订阅额度);不通时错误只落在 `/tmp/screenpipe-auto.log`,表面症状是"radar 静默数天不出卡"。**别赌这条兜底**——贴一个 key 文件才是可靠路径。

**计费预期**:key 文件存在时,所有 headless 用量按 API 计费(不消耗订阅额度)。量级参考:ingest 每 30 分钟一次 headless 调用(≈48 次/天,时长随积压素材量波动)+ 每张批准的卡一个执行 session。建议在 console.anthropic.com 设 spend limit,观察第一周的实际用量再调。

**key 存哪、怎么验证**:app 设置窗口粘贴保存 → 写入 `config/secrets/anthropic-api-key.txt`(目录 0700 / 文件 0600,gitignored;CONTRACT §19)。装完随时可以跑 `bash install.sh --check`(即 `python3 -m act.doctor`)整链体检——它会用与 headless 组件**相同的凭据解析顺序**做一次廉价 live 调用,key 贴错当场可见,而不是几分钟后死在没人看的 cron log 里。

## 路线 A:.pkg 安装包

1. 从 [GitHub Releases](https://github.com/Wan-ZL/zelin-ai-assistant/releases) 下载 `ZelinAIAssistant-<tag>.pkg`。
2. **未签名**(无 Developer ID),Gatekeeper 会拦:右键 → 打开;若仍被拒,去
   系统设置 → 隐私与安全性 → 底部点 "仍要打开"(macOS Sequoia 起只有这条路)。
3. 装了什么、装到哪:
   - `/Applications/Zelin's AI Assistant.app` — 菜单栏 app。
   - `/Library/Application Support/ZelinAIAssistant/pipeline/` — pipeline 母本(root 所有,随版本更新)。
   - postinstall 自动把母本 rsync 到 `~/Projects/zelin-ai-assistant/`(**不覆盖**你已有的
     `config.yaml`、`config/secrets/`、`state/`),再跑 `install.sh --pkg-postinstall`:
     config 模板 copy-if-absent、state 目录、ingest crontab 链(CONTRACT §18)。
   - **不装** launchd agents(actd/radar 需要你先配好 config.yaml)——配置完成后跑一次
     `bash ~/Projects/zelin-ai-assistant/install.sh` 补齐。

> ✅ **预期状态**:`/Applications` 里有 app;`~/Projects/zelin-ai-assistant/` 已铺好。接着从下方 **步骤 2(config)** 继续,并在编辑完 config 后按提示重跑一次 `install.sh`。

## 路线 B:从源码

### 步骤 1 · clone

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant
```

当前版本多处默认路径解析到 `~/Projects/zelin-ai-assistant`(app 与部分脚本)。clone 到别处时,凡是运行管线的环境都要 `export AIASSISTANT_HOME=<你的路径>`,且 app 需以同样的 env 启动(`open` 不传 env,启动方式见 `docs/DEMO.md`)——**推荐直接用默认路径**。

> ✅ **预期状态**:`ls ~/Projects/zelin-ai-assistant/install.sh` 存在。

### 步骤 2 · config.yaml

```bash
cd ~/Projects/zelin-ai-assistant
cp config.example.yaml config.yaml
```

打开 `config.yaml` 至少检查:`sources.obsidian_raw`(你的 vault 路径)、`sources.watch_people`。没有 Obsidian / Slack / Gmail 也能先跑——对应雷达会静默待机,快速捕获链路不受影响。

> ✅ **预期状态**:`config.yaml` 存在且 `python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"` 不报错。

### 步骤 3 · install.sh

```bash
bash install.sh
```

它做七件事:依赖检查(claude / Swift toolchain 版本 / PyYAML,PEP 668 环境自动带 `--break-system-packages` 重试)→ config 模板/runtime 指针 → state 目录 → 构建并安装 Mac app → launchd agents(actd 常驻 + 雷达)→ 统一 crontab(ingest 链 + 周一 digest,CONTRACT §18)→ 收尾自动跑一遍诊断(`python3 -m act.doctor`)。幂等,可反复跑。

> ✅ **预期状态**:输出没有 `[ERR]`,结尾的诊断没有 `[FAIL]`(此刻还没贴 key,`anthropic key` 一行是 `[warn]` 属正常);`launchctl list | grep com.zelin.aiassistant` 至少一行;`crontab -l | grep screenpipe-export` 恰一行;app 出现在 `/Applications`(或 `~/Applications`)。之后任何时候都能用 `bash install.sh --check` 重跑诊断。

### 步骤 4 · 首次启动(Gatekeeper)

app 未签名,首次启动被 Gatekeeper 拦:在 `/Applications` 里**右键 → 打开**;macOS Sequoia+ 若仍被拒,去 系统设置 → 隐私与安全性 → 底部 "仍要打开"。

打开后 app 会弹出首启**权限体检页**:回答唯一的屏幕记录 consent(默认**仅屏幕**,不录音频——语音转写之后可在「设置 → 录制」单独打开)、按清单授予 屏幕录制/通知(完全磁盘访问仅 iPhone 联动需要),并可取消勾选匿名使用统计。之后随时可从 菜单 → 权限体检 重开这一页。

> ✅ **预期状态**:菜单栏出现图标,点击能打开 popover。

### 步骤 5 · Anthropic API key

打开 app 的设置窗口,粘贴 API key 保存——写入 `config/secrets/anthropic-api-key.txt`(目录 0700 / 文件 0600,CONTRACT §19)。cron/launchd 的 daemon session 读不了 Keychain OAuth,所以 headless claude 必须有文件形式的 key;旧路径 `~/.config/anthropic-key.txt` 仍兜底。只有 Pro/Max 订阅、没有 API key?哪些组件受影响、fallback 怎么走、怎么计费,见上方[认证模型](#认证模型api-key-vs-promax-订阅)。

> ✅ **预期状态**:`ls -l config/secrets/anthropic-api-key.txt` 显示 `-rw-------`;`bash install.sh --check` 里 `anthropic key` 与 `claude auth` 两行都是 `[ ok ]`(doctor 会用这个 key 做一次廉价 live 调用)。

### 步骤 6 · TCC 授权(路径逐条)

最快路径:app 的**权限体检页**(首启自动弹出;之后 菜单 → 权限体检)对每一项都有实时状态灯和一键跳转按钮。手动路径如下:

- **屏幕录制**:系统设置 → 隐私与安全性 → 屏幕录制(macOS 15 起叫"屏幕与系统音频录制")→ 打开 "Zelin's AI Assistant"。app 首次启动录制时系统也会主动弹窗。
- **麦克风**:系统设置 → 隐私与安全性 → 麦克风 → 打开 app(录音转写用;只录屏可跳过)。
- **完全磁盘访问权限(给 cron)**:系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 左下 "+" → 按 ⌘⇧G 输入 `/usr/sbin/cron` → 添加并打开开关。ingest/radar 走 crontab 是因为 launchd 被 TCC 挡在 `~/Documents`(vault 所在)之外——见 `HANDOFF.md` §3。

> ✅ **预期状态**:三个开关都已打开。注意:每次重新构建安装 app 后,屏幕录制授权会**静默失效**(ad-hoc 签名),症状与修复见 [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

### 步骤 7 · 心跳验证

> ✅ **预期状态**:popover header 显示"数据生成于 ≤10 秒前"并持续刷新——说明 actd 活着、契约两端接通。若显示橙色 "actd 可能未运行":`launchctl list | grep actd`、`tail state/actd.log`,并对照 TROUBLESHOOTING。

## 第一张卡(5 分钟)

radar 出卡需要 screenpipe + Obsidian 里先积累素材;**新装机器请先走快速捕获**——只要 claude CLI + API key + actd 在跑,就能体验完整闭环。

1. 按 **⌥Space**(默认全局热键)呼出快速捕获,或用 popover / 看板列顶的输入框。
2. 输入一个 starter task(可直接复制):

   > 在 ~/Projects/assistant-hello 新建一个小脚本:统计 ~/Downloads 里各扩展名的文件数,输出 markdown 表格,配一个单元测试。

3. 回车。占位卡**立刻**出现(乐观回显);LLM 对照注册表三选一后,真实的**待审批卡**通常 **15 秒–2 分钟**内落地(actd 每 10s 一个 pass + 一次 claude 判定)。
4. 点 ✅ 批准 → 卡片先灰显"排队"(瞬时),随后进入**执行中**(`claude --bg` 在独立 worktree 里跑)。这样的简单任务通常 **2–10 分钟**。
5. 完工后卡片进入**待验收**,带交付摘要(代码任务给分支/draft PR;文书任务给可直接复制的 FINAL DRAFT)。点 ✅ 验收归档,或 💬 带评论打回重做。

**慢 vs 坏**的判别线(拿不准就先跑 `bash install.sh --check`,它会把坏的一环直接指出来):

- 捕获后 **>5 分钟**没有待审批卡 → actd 没在跑或 key 无效:`launchctl list | grep actd`、`tail state/actd.log`。
- 批准后卡在"排队" **>2 分钟** → 派发失败,卡片会显示 last_error;看 `state/actd.log`。

### 零 key 的 UI 预览

不想先配 key?`python3 scripts/demo_seed.py /tmp/assistant-demo` 生成一份完全虚构的 dashboard,让 app 指着假数据跑,五种卡片和边缘状态全部可见——完整用法见 [`docs/DEMO.md`](DEMO.md)。

## 装完之后(可选接入)

- **Slack 雷达**:`docs/SLACK_SETUP.md`(user token,或 MCP 只读兜底)。
- **Gmail 雷达**:`docs/GMAIL_SETUP.md`(应用专用密码)。
- **遥测**(默认关):`docs/TELEMETRY.md`。
- 什么数据会离开你的机器:`docs/PRIVACY.md`。
