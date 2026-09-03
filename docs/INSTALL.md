# INSTALL — 安装指南

首次安装与更新的**唯一权威文档**。**一条命令**（`scripts/bootstrap.sh`）在空白 Mac 上把一切装到能用；同一条命令在已装过的机器上就是更新。装完剩下的事只有三件，而且只有人能做：给两个二进制开完全磁盘访问（需要时）、贴 API key、打开看板。每一步末尾有一个 ✅ **预期状态** checkpoint——到不了预期状态就先去 [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)，别带病继续。法条：`docs/CONTRACT.md` §69（bootstrap + CI 验收）、§23（安装报告）、§55（TCC）。

## English quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/Wan-ZL/zelin-ai-assistant/main/scripts/bootstrap.sh | bash
```

That is the whole install — and the whole update (re-run it any time; an existing checkout is fast-forwarded, `config.yaml` is never overwritten). Prerequisite: macOS 14+ with the Xcode Command Line Tools (`xcode-select --install`; the script tells you if they are missing and never pops the dialog itself). Optional but recommended before or after: [Claude Code CLI](https://claude.com/claude-code), Node.js LTS (`brew install node` — builds the web board and runs the capture engine), Obsidian, `gh`.

What the command does: clones to `~/Projects/zelin-ai-assistant` (or `bash -s -- <dir>` / `--dir`), creates `config.yaml` from the example, runs `bash install.sh --non-interactive` (never prompts: dependency checks with PyYAML auto-install, version stamp, daemon interpreter selection, state dirs, web board + Dock app build, launchd agents, cron chain), prints **what is left for you** (`python3 -m act.doctor --fresh-install` — with this machine's real paths), and opens the board app. Its exit code is 0 when nothing is broken; permissions and credentials still pending are listed, not counted.

Then, in the board's **first-run wizard** (`?page=setup`, it opens by itself until config and credentials are in place): install Claude Code if missing, drop your Anthropic API key into `config/secrets/anthropic-api-key.txt` (the panel gives the exact command), and grant Full Disk Access to the daemon interpreter and to `claude` if the checkout or your task repos live under `~/Documents`, `~/Desktop`, `~/Downloads` or an external volume (paths are printed). Diagnostics any time: `bash install.sh --check` (full) or `bash install.sh --check --fresh-install` (what is left).

Telemetry: anonymous usage statistics are on by default (event metadata only — names, timestamps, a random device id, version). **The text you type** into the board is uploaded only if you opt in (`telemetry.capture_input`, default off); the master switch is `telemetry.enabled: false`. Details and field tables: [docs/TELEMETRY.md](TELEMETRY.md).

No API key yet? `bash scripts/dev-preview.sh` previews the entire board with fictional data — see [docs/DEMO.md](DEMO.md).

## 前置条件

| 组件 | 版本 | 用途 / 自检命令 |
|---|---|---|
| macOS | **14+** | 看板壳 app、launchd/cron 定时、TCC 权限模型 |
| Xcode Command Line Tools | 随系统 | **唯一硬前置**：带来 git、swiftc、`/usr/bin/python3`；`xcode-select -p` 有输出即可，缺则 `xcode-select --install` |
| [Claude Code CLI](https://claude.com/claude-code) | 最新版 | 雷达提取、提案扩写、执行全靠它；`claude --version`。缺席时装机照常完成，守护进程待机 |
| Anthropic API key | — | headless 运行必需（为什么订阅不够用，见下方[认证模型](#认证模型api-key-vs-promax-订阅)） |
| Python | **3.9+** | `/usr/bin/python3` 即可；PyYAML 由 install.sh 自动 `pip install --user` |
| Node.js | LTS（含 `npx`） | 构建 web 看板（`web/`）、录制引擎经 `npx screenpipe` 运行；缺失时看板 UI 跳过构建，`/` 显示一页说明，`brew install node` 后重跑即补上 |
| Obsidian（可选，推荐） | — | vault 是雷达扫描源与 wiki 落点 |
| `gh` CLI（可选） | — | draft-PR 交付 |

## 一条命令（推荐路线）

```bash
curl -fsSL https://raw.githubusercontent.com/Wan-ZL/zelin-ai-assistant/main/scripts/bootstrap.sh | bash
```

自选目录：`curl -fsSL … | bash -s -- ~/Code/zelin-ai-assistant`（或 `--dir PATH`；`ZAI_BOOTSTRAP_DIR` 环境变量同义）。**推荐留在 `$HOME` 之内**：放到外置卷 / 网络盘 / `~/Documents` 之类受保护位置时，macOS 按 binary 授权（CONTRACT §55），守护解释器和 `claude` 都要各自开完全磁盘访问——脚本会在第 2 步醒目提示。

它按顺序做七件事，每件一行 `[ ok ]` / `[warn]` / `[ERR ]`：

1. **preflight**：macOS（Linux 请用 `install-linux.sh`，Windows 用 `install.ps1`）；Xcode Command Line Tools；git；python3；claude（缺席只警告）。
2. **checkout 目录**：默认 `~/Projects/zelin-ai-assistant`；在 `$HOME` 之外则打出 TCC 警告。
3. **clone 或更新**：首跑 `git clone --branch main`；已是本 repo 的 checkout 则 `fetch` + `--ff-only`。**有本地改动 = 不动代码只装现状**；目录存在但不是 git checkout、或是别的 repo → 拒绝，一个字节不碰。
4. **config.yaml**：缺则从 `config.example.yaml` 复制，**存在永不覆盖**。
5. **`bash install.sh --non-interactive`**：永不停下来问；依赖检查（PyYAML 缺则自动装，PEP 668 环境自动带 `--break-system-packages` 重试）→ 版本盖章 → 守护解释器两道闸门（§55）→ state 目录 → web 看板 + Dock 壳 app 构建安装（node / swiftc 缺席时跳过，不算失败）→ launchd agents（actd 常驻 + 看板 server + 雷达）→ crontab（ingest 链 + digest + 遥测）。退出码 = 失败步数。
6. **`python3 -m act.doctor --fresh-install`**：把体检行分成「installer 接好的 / 等你的（机器做不了）/ 按要求没接的 / 坏了的」四类，并按序列出**剩下要你做的事**——每条带可复制命令与本机真实路径。
7. **打开看板**：`open "/Applications/Zelin's AI Assistant.app"`（`--no-open` 跳过）。看板会自己开在**首次运行向导**（`?page=setup`，CONTRACT §68.5）：配置文件 → 后台进程的磁盘授权（权限体检页给出可复制的路径）→ 可选凭证 → 完成；`python3 -m act.doctor --fresh-install` 是同一份清单的命令行版。

> ✅ **预期状态**：末尾 `bootstrap done (cloned)`（更新时 `updated`）、`exit 0 — nothing broken: the rest is yours`；看板窗口弹出并显示「还差 N 步就能用了」的面板。`bash ~/Projects/zelin-ai-assistant/install.sh --check` 随时可重跑全套诊断。

### 更新

同一条命令再跑一遍（或 `cd ~/Projects/zelin-ai-assistant && git pull && bash install.sh`）。此外，合并进 `main` 的每个 PR 会由 `com.zelin.aiassistant.autodeploy` 每 10 分钟自动部署到已装机器（CONTRACT §56；关掉：config.yaml `features.auto_deploy: false`）。

## 剩下的是你的（机器做不了）

装完后 `python3 -m act.doctor --fresh-install`（= `bash install.sh --check --fresh-install`）与看板的首次运行向导 / 权限体检页（`?page=setup` / `?page=permissions`）都会列出这几件：

### 1 · Claude Code CLI

```bash
curl -fsSL https://claude.ai/install.sh | bash   # 然后：claude login
```

装好后重跑 `bash install.sh`（让 launchd plist 的 PATH 指到它）。

### 2 · Anthropic API key

cron/launchd 的 daemon session 读不了 Keychain OAuth，所以 headless claude 必须有**文件形式的 key**（目录 0700 / 文件 0600，CONTRACT §19）：

```bash
cd ~/Projects/zelin-ai-assistant
mkdir -p config/secrets && chmod 700 config/secrets
printf '%s\n' 'sk-ant-…' > config/secrets/anthropic-api-key.txt && chmod 600 config/secrets/anthropic-api-key.txt
```

旧路径 `~/.config/anthropic-key.txt` 仍兜底可用。只有 Pro/Max 订阅、没有 API key？见下方[认证模型](#认证模型api-key-vs-promax-订阅)。

> ✅ **预期状态**：`bash install.sh --check` 里 `anthropic key` 与 `claude auth` 两行都是 `[ ok ]`（doctor 会用这个 key 做一次廉价 live 调用）。

### 3 · 完全磁盘访问（TCC，按二进制授权）

macOS 把文件访问权**按每个可执行文件**授予，launchd 起的进程不继承你终端的授权（CONTRACT §55 的全部事故史）。系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 左下「+」→ ⌘⇧G 粘贴路径：

| 给谁 | 路径（doctor `--fresh-install` 打出本机真实值） | 为什么 |
|---|---|---|
| 守护解释器 | `config/runtime.json` 的 `python`（通常 `/usr/bin/python3`） | actd / 雷达 / 看板 server 以它运行 |
| claude（稳定副本） | `~/Library/Application Support/ZelinAIAssistant/bin/claude`——install.sh 维护的 claude 稳定副本（CONTRACT §55 第五幕），**授权一次即可**，claude 更新不再失效；doctor `stable claude` 行确认 | 派出的 agent 以它读你的仓库 |
| cron | `/usr/sbin/cron` | ingest 链读 `~/Documents` 下的 Obsidian vault |

**什么时候需要**：本 repo 或你的任务仓库在 `~/Documents`、`~/Desktop`、`~/Downloads` 或外置卷上时**必需**；都在 `$HOME` 其它位置时可以不授（多授无害）。doctor 的 `launchd volume access` / `cron write access` / `launchd claude` 行会在被拦时点名。

### 4 · 屏幕录制与麦克风（只在你开录制时）

看板右上角的「录制」开关首次打开时系统会弹屏幕录制授权；字幕音源含麦克风时弹麦克风授权。默认**仅屏幕**不录音频。

> ✅ **预期状态**：看板不再自动跳到向导页（完成标记已写）；`bash install.sh --check --fresh-install` 末行 `exit 0`，「waiting on you」桶为空（或只剩你有意不接的可选源）。

## 认证模型（API key vs Pro/Max 订阅）

claude CLI 有两套互不相通的凭据，而这个项目的不同组件用到的**不是同一套**——装完后「终端里跑得通、cron 却静默死」几乎都源于此：

| 认证方式 | 是什么 | 谁能用 | 计费 |
|---|---|---|---|
| **OAuth（Pro/Max 订阅）** | `claude` 登录后存进 macOS Keychain 的订阅凭据 | 只有你 GUI 登录会话里的进程（交互终端等） | 计入订阅额度，不另付费 |
| **API key（`sk-ant-…`）** | [console.anthropic.com](https://console.anthropic.com) 签发的 metered key，以文件形式存放 | 任何进程，包括 cron / launchd 的 daemon session | 按 token 计费（API 账单） |

**为什么 headless 必须用 key 文件**：cron 和 launchd agents 跑在 daemon session 里，macOS 有两层沙箱把 OAuth 挡死——Keychain 只对 GUI 会话开放，而 `launchctl asuser` 也无法从 cron 的 daemon audit session 桥接过去（细节见 `ingest/process-screenpipe.sh` 顶部注释块）。因此：

- **需要 key 文件**的组件（全部 headless，under cron/launchd）：ingest 链（`process-screenpipe.sh`）、各 radar、状态 digest、actd 派发的 `claude --bg` 执行。
- **订阅凭据就够**的场景：你在终端手动跑 `claude`（交互 session 读得到 Keychain）。

**ingest 的 fallback 行为**（其余 headless 组件同理）：按 CONTRACT §19 顺序解析 key——`config/secrets/anthropic-api-key.txt` → 旧路径 `~/.config/anthropic-key.txt`；**两个文件都没有时不会立刻失败**，而是回落到 claude CLI 自己存储的凭据试跑。在常年保持登录的 Mac（如 Mac mini）上这条兜底路经常能通（此时计入订阅额度）；不通时错误只落在 `/tmp/screenpipe-auto.log`，表面症状是「radar 静默数天不出卡」。**别赌这条兜底**——贴一个 key 文件才是可靠路径。

**计费预期**：key 文件存在时，所有 headless 用量按 API 计费（不消耗订阅额度）。量级参考：ingest 每 30 分钟一次 headless 调用（≈48 次/天，时长随积压素材量波动）+ 每张批准的卡一个执行 session。建议在 console.anthropic.com 设 spend limit，观察第一周的实际用量再调。

**怎么验证**：`bash install.sh --check`（即 `python3 -m act.doctor`）整链体检——它会用与 headless 组件**相同的凭据解析顺序**做一次廉价 live 调用，key 贴错当场可见，而不是几分钟后死在没人看的 cron log 里。

## 手动路线（等价于 bootstrap 做的事）

想看清每一步、或在 CI / 容器里复现，可以手动走：

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant
cd ~/Projects/zelin-ai-assistant
cp config.example.yaml config.yaml         # 改不改都行：vault 路径 / Slack / Gmail 之后在看板设置里改
bash install.sh                            # 交互模式：缺 claude / swift 会停下来告诉你；结尾跑全套 doctor
bash install.sh --check --fresh-install    # 剩下要你做的事
```

`bash install.sh` 的模式：默认交互；`--non-interactive`（永不提问，自动部署与 bootstrap 用；退出码 = 失败步数）；`--no-launchd`（不装任何 launchd agent、不写 crontab，改为跑一次 `python3 -m act.actd --once` 证明 daemon 能跑——CI 验收与干跑用，正式安装不要传）；`--check [doctor 参数]`。每次运行都写 `state/install_report.json`（CONTRACT §23）记录实际做了什么。

> ✅ **预期状态**：输出没有 `[ERR]`；`launchctl list | grep com.zelin.aiassistant` 至少 actd 与 server 两行；`crontab -l | grep screenpipe-export` 恰一行；`/Applications/Zelin's AI Assistant.app`（看板壳，或 `~/Applications/`）存在；`http://127.0.0.1:47820/` 能打开看板。

### 首次启动看板（Gatekeeper）

自己构建的壳 app 是 ad-hoc 签名，本机 `open` 不会被拦。若是从别处拷来的 bundle，首次启动需在 `/Applications` 里**右键 → 打开**；macOS Sequoia+ 若仍被拒，系统设置 → 隐私与安全性 → 底部「仍要打开」。

> ✅ **预期状态**：Dock 里出现 "Zelin's AI Assistant"，窗口顶部新鲜度标签显示看板数据 ≤10 秒前生成——说明 actd 活着、契约两端接通。若显示橙色「后台服务没在运行」：`launchctl list | grep actd`、`tail ~/Library/Logs/zelin-ai-assistant/actd.launchd.log`，并对照 TROUBLESHOOTING。

## 第一张卡（5 分钟）

radar 出卡需要 screenpipe + Obsidian 里先积累素材；**新装机器请先走快速捕获**——只要 claude CLI + API key + actd 在跑，就能体验完整闭环。

1. 打开看板，用提案列顶的输入框（⌘L 直接聚焦）。
2. 输入一个 starter task（可直接复制）：

   > 在 ~/Projects/assistant-hello 新建一个小脚本：统计 ~/Downloads 里各扩展名的文件数，输出 markdown 表格，配一个单元测试。

3. 回车。占位卡**立刻**出现（乐观回显）；LLM 对照注册表三选一后，真实的**提案卡**通常 **15 秒–2 分钟**内落地（actd 每 10s 一个 pass + 一次 claude 判定）。
4. 点 ✅ 批准 → 卡片先灰显「排队」（瞬时），随后进入**执行中**（`claude --bg` 在独立 worktree 里跑）。这样的简单任务通常 **2–10 分钟**。
5. 完工后卡片进入**待验收**，带交付摘要（代码任务给分支/draft PR；文书任务给可直接复制的 FINAL DRAFT）。点 ✅ 验收（卡片进「阶段性完成」），或 💬 带评论打回重做。

**慢 vs 坏**的判别线（拿不准就先跑 `bash install.sh --check`，它会把坏的一环直接指出来）：

- 捕获后 **>5 分钟**没有提案卡 → actd 没在跑或 key 无效：`launchctl list | grep actd`、`tail state/actd.log`。
- 批准后卡在「排队」 **>2 分钟** → 派发失败，卡片会显示 last_error；看 `state/actd.log`。

### 零 key 的 UI 预览

不想先配 key？`bash scripts/dev-preview.sh` 起一份完全虚构的看板，五种卡片和边缘状态全部可见——完整用法见 [`docs/DEMO.md`](DEMO.md)。

## 装完之后（可选接入）

- **Slack 雷达**：`docs/SLACK_SETUP.md`（user token，或 MCP 只读兜底）。
- **Gmail 雷达**：`docs/GMAIL_SETUP.md`（应用专用密码）。
- **遥测**（匿名使用统计，默认开、仅事件元数据、一键可关 `telemetry.enabled: false`；**你输入**进看板的文本默认**不**上传，想帮忙改进产品可 opt in：`telemetry.capture_input: true`）：`docs/TELEMETRY.md`。
- 什么数据会离开你的机器：`docs/PRIVACY.md`。
- **Skills**（仓库内 skill 商店，CONTRACT §67）：`install.sh` 已把默认开的 skill（`board-agent`、`test-code`）链进 `~/.claude/skills`；其余在看板设置页 → Skills 启用/停用，或 `python3 -m act.lib.skills enable <name>`。另一台机器更新后跑 `bash scripts/skills_sync.sh --pull` 即同步；本地改过的副本标为「自定义」，商店永不覆盖（`skills/README.md`）。

## 这条路线在 CI 上每天都走一遍

`.github/workflows/fresh-install.yml`（"Fresh install (macOS)"）在每次 push 到 main、每晚一次以及手动触发时用一台干净的 macOS runner 复现本页（不按 PR 跑——macOS runner 稀缺，CONTRACT §56.8）：空 `$HOME`、本地 origin、`bash scripts/bootstrap.sh --no-launchd` → 断言安装报告、起看板 server 断言 `/api/board` `/api/health` `/api/setup`、`doctor --fresh-install` 退出 0、第二次运行是更新且 `config.yaml` 字节不变。绿 = 「另一台电脑、空白环境、一条命令、直接能用」成立（CONTRACT §69.4）。

## 卸载（clean uninstall）

```bash
cd ~/Projects/zelin-ai-assistant
bash uninstall.sh --dry-run   # 先预览：只打印计划，不改任何东西
bash uninstall.sh             # 执行（Y/n 确认）
```

它做什么：卸载全部 launchd agents、从 crontab 移除**只属于本产品**的行（按标记 token 匹配，你的其他 cron 行原样保留）、退出 App 并停止录制引擎、删除 `/Applications` 里的 App 与 root 所有的管线母本（权限不够时会打印对应的 `sudo` 命令）。

**默认保留你的数据**——任务历史（`state/`）、`config.yaml` 与 API 密钥（`config/secrets/`）、Obsidian vault、`~/.screenpipe` 录像，结尾逐项附上删除命令。想一次删干净（vault 除外，**永不碰**）：

```bash
bash uninstall.sh --purge
```

> ✅ **预期状态**：`launchctl list | grep com.zelin.aiassistant` 无输出；`crontab -l` 里没有 screenpipe/act.* 行；后台不再有任何 claude 调用与屏幕录制。

## 附：.pkg 安装包（旧路线，仍可用）

[GitHub Releases](https://github.com/Wan-ZL/zelin-ai-assistant/releases) 里的 `ZelinAIAssistant-<tag>.pkg` 装的是**旧的菜单栏 app**（现名 `Zelin's AI Assistant (old).app`，D3 退役中，保留到 owner 明确下令删除；产品名 `Zelin's AI Assistant.app` 归看板壳）+ 管线母本，postinstall 跑 `install.sh --pkg-postinstall`。未签名，Gatekeeper 会拦：右键 → 打开；若仍被拒，系统设置 → 隐私与安全性 → 底部「仍要打开」。新机器请用上面的一条命令。
