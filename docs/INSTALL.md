# INSTALL — 安装指南

lite 2.0 首次安装、升级与卸载的权威文档。**一条命令**（`scripts/bootstrap.sh`）在空白 Mac 上装到能用；同一条命令在已装过的
机器上就是更新。装完剩下的事只有人能做：贴 Anthropic API key、给几个二进制开完全磁盘访问（需要时）、在 app 里打开录制。
每一步末尾有一个 ✅ **预期状态**；到不了就先去 [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。法条：`docs/CONTRACT.md`
§69（bootstrap）、§23（安装报告）、§55（TCC）、§78（隔离与常驻足迹）、§79（升级）。

## English quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/Wan-ZL/zelin-ai-assistant/main/scripts/bootstrap.sh | bash
```

That is the whole install, and re-running it is the update (an existing checkout is fast-forwarded; `config.yaml` is never
overwritten). Prerequisites: macOS 14+ with the Xcode Command Line Tools (`xcode-select --install`), the
[Claude Code CLI](https://claude.com/claude-code) and Node.js LTS (`brew install node`; it builds the web UI and runs the
capture engine). The installer clones to `~/Projects/zelin-ai-assistant`, runs `bash install.sh --non-interactive`, prints what
is left for you (`python3 -m act.doctor --fresh-install`) and opens the app.

What it leaves on the machine (CONTRACT §78, a closed list): one LaunchAgent (`com.zelin.aiassistant.server`), one cron line
(the 30-minute ingest chain), the app in `/Applications`, and — only when you turn it on in the app — a login item and the
screenpipe engine. Every headless `claude -p` runs with its own config dir under `~/Library/Application Support/ZelinAIAssistant/claude-config/`
and an Anthropic API key; it never reads or writes your `~/.claude`. Nothing is uploaded except model calls and a daily
version check against GitHub (switchable in Settings). Details: [docs/PRIVACY.md](PRIVACY.md).

## 前置条件

| 组件 | 版本 | 用途 / 自检命令 |
|---|---|---|
| macOS | **14+** | Dock 壳 app、launchd / cron、TCC 权限模型。lite 只支持 macOS（Linux / Windows 安装器在 v2.0.0 退役） |
| Xcode Command Line Tools | 随系统 | git、swiftc、`/usr/bin/python3`；`xcode-select -p` 有输出即可 |
| [Claude Code CLI](https://claude.com/claude-code) | 最新版 | ingest 与会议纪要的 headless 运行；`claude --version`。需支持 `--bare`（doctor `claude bare supported` 行） |
| Anthropic API key | — | headless 运行唯一的凭据（见[认证模型](#认证模型)） |
| Python | **3.9+** | `/usr/bin/python3` 即可；PyYAML 由 install.sh 自动 `pip install --user` |
| Node.js | LTS（含 `npx`） | 构建 web UI；录制引擎经 `npx screenpipe` 运行 |
| Obsidian（推荐） | — | ingest 的笔记落点 |

## 一条命令（推荐路线）

```bash
curl -fsSL https://raw.githubusercontent.com/Wan-ZL/zelin-ai-assistant/main/scripts/bootstrap.sh | bash
```

自选目录：`curl -fsSL … | bash -s -- ~/Code/zelin-ai-assistant`（或 `--dir PATH`）。**推荐留在 `$HOME` 之内**：放到外置卷、
`~/Documents` 之类受保护位置时，macOS 按二进制授权（§55），守护解释器、claude 与 cron 都要各自开完全磁盘访问。

bootstrap 的七步：preflight（macOS、Command Line Tools、git、python3、claude）→ checkout 目录 → clone 或 fast-forward →
`config.yaml` 缺则从模板复制 → `bash install.sh --non-interactive` → `python3 -m act.doctor --fresh-install` → 打开 app。

`install.sh` 自己的八步（每步一行 `[ ok ]` / `[warn]` / `[ERR ]`，结尾写 `state/install_report.json`）：

1. **依赖检查**：claude、swift 工具链、python3 + PyYAML、node/npx。
2. **配置与运行环境**：`config.yaml`（存在永不覆盖）与脱敏词表；版本盖章；守护解释器（两道闸门，§55）写进 `config/runtime.json`；
   home 指针；claude 的稳定副本（`~/Library/Application Support/ZelinAIAssistant/bin/claude`）；§78 的隔离目录
   `claude-config/` 与 `claude-config/cwd/`；`config/secrets/anthropic-api-key.txt` 缺而 `$ANTHROPIC_API_KEY` 或
   `~/.config/anthropic-key.txt` 有 key 时复制过来（交互时先问）。
3. **state 目录**。
4. **1.x 迁移**（只在装过 1.x 的机器上有动作）：见下方[从 1.x 迁移](#从-1x-迁移)。
5. **UI**：web 构建 + Dock 壳 app 装进 `/Applications`（node / swiftc 缺席时跳过，不算失败）。
6. **server agent**：唯一的 LaunchAgent `com.zelin.aiassistant.server`。
7. **crontab**：唯一的一行，每 30 分钟的 ingest 链（export → cleanup → process），日志 `state/ingest.cron.log`；你自己的其它 cron 行原样保留。
8. **体检**（交互运行）：`python3 -m act.doctor`。

录制引擎**永不**被 install.sh 停掉：已在录的引擎照录，app 启动时接管它（§61.8）。

> ✅ **预期状态**：末尾 `bootstrap done (cloned)`（更新时 `updated`）、doctor `exit 0`；app 窗口打开，默认页是会议纪要。

### 更新

关于页的「立即升级」（或 `bash scripts/upgrade.sh`）：`git fetch --tags` → 要求 origin/main 恰好是一个 `v*` release tag →
`git merge --ff-only` → `bash install.sh --non-interactive --from-upgrade` → 重开 app（引擎不停）。工作树有改动、不在 main 或
本地有 origin 上没有的 commit 时，升级器**什么都不改**，把要你手动跑的命令写进 `state/upgrade_state.json`。设置页 updates 区的
「自动升级」（`updates.auto_install`，默认关）打开后，每日的版本检查发现同一大版本内的新版时自动跑同一个升级器；新的大版本
永远手动（§79）。

## 剩下的是你的（机器做不了）

`bash install.sh --check --fresh-install` 与 app 的首次运行向导 / 权限页（`?page=setup` / `?page=permissions`）列出同一份清单：

### 1 · Claude Code CLI

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

装好后重跑 `bash install.sh`（刷新稳定副本、cron 的 PATH）。

### 2 · Anthropic API key

```bash
cd ~/Projects/zelin-ai-assistant
mkdir -p config/secrets && chmod 700 config/secrets
printf '%s\n' 'sk-ant-…' > config/secrets/anthropic-api-key.txt && chmod 600 config/secrets/anthropic-api-key.txt
```

或在 app 的 设置 → 凭证 粘贴。

> ✅ **预期状态**：`bash install.sh --check` 里 `anthropic key` 行 `[ ok ]`。

### 3 · 完全磁盘访问（TCC，按二进制授权）

系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 「+」→ ⌘⇧G 粘贴路径：

| 给谁 | 路径（doctor `--fresh-install` 打出本机真实值） | 为什么 |
|---|---|---|
| 守护解释器 | `config/runtime.json` 的 `python`（通常 `/usr/bin/python3`） | 看板 server 以它运行 |
| claude（稳定副本） | `~/Library/Application Support/ZelinAIAssistant/bin/claude` | ingest 与纪要的 headless claude；授权一次即可，claude 更新不失效 |
| cron | `/usr/sbin/cron` | ingest 链读 `~/Documents` 下的 Obsidian vault |

repo 或 vault 在 `~/Documents`、`~/Desktop`、`~/Downloads` 或外置卷上时**必需**；都在 `$HOME` 其它位置时可以不授。

### 4 · 屏幕录制、麦克风与登录项

app 里打开录制时系统会弹屏幕录制授权（音源含麦克风时另弹麦克风）。想让录制在重启后自己恢复，在 app 里打开
「Launch at login」（opt-in，SMAppService）。

> ✅ **预期状态**：`bash install.sh --check --fresh-install` 末行 `exit 0`，「waiting on you」桶为空。

## 认证模型

每一次 headless `claude -p`（ingest、会议纪要、doctor 的探针）都在 §78 的隔离常量下运行：`CLAUDE_CONFIG_DIR` 指向
`~/Library/Application Support/ZelinAIAssistant/claude-config/`，cwd 是它下面的空目录 `cwd/`（在 repo 树之外），argv 恒带
`--bare --no-session-persistence --setting-sources "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'`。`--bare` **永不读**
claude CLI 存的 OAuth 凭据或钥匙串，所以 Pro/Max 订阅登录不能代替 API key，`claude login` 进隔离目录也不行。

key 的解析顺序（§19）：进程环境里的 `ANTHROPIC_API_KEY` → `config/secrets/anthropic-api-key.txt` → `~/.config/anthropic-key.txt`。
三处都没有时 ingest 以退出码 1 失败（`/tmp/screenpipe-auto.log` 一行 `❌ no Anthropic API key`），会议纪要失败并写进
`state/recap.log`，claude 不起。

**计费预期**：所有 headless 用量按 API 计费。ingest 每 30 分钟最多一次调用（inbox 为空时不调），每场会议一次纪要调用。
建议在 console.anthropic.com 设 spend limit。

## 手动路线（等价于 bootstrap 做的事）

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant
cd ~/Projects/zelin-ai-assistant
bash install.sh                            # 交互模式：缺 claude / swift 会停下来告诉你；结尾跑全套 doctor
bash install.sh --check --fresh-install    # 剩下要你做的事
```

模式：默认交互；`--non-interactive`（永不提问，退出码 = 失败步数）；`--no-launchd`（不装 agent、不读写 crontab，CI 验收与
干跑用）；`--from-upgrade`（升级器用）；`--check [doctor 参数]`。

> ✅ **预期状态**：`ls ~/Library/LaunchAgents | grep zelin` 只有 `com.zelin.aiassistant.server.plist`；`crontab -l | grep screenpipe-export`
> 恰一行；`http://127.0.0.1:47820/` 打开会议纪要页。

### 首次启动（Gatekeeper）

本机构建的壳 app 用稳定的自签证书签名（`bash shell/scripts/make-signing-cert.sh` 生成一次；没有证书时回落 ad-hoc），本机
`open` 不会被拦。从别处拷来的 bundle 首次启动需右键 → 打开。

## 从 1.x 迁移

1.x 与 2.0 不能在同一台机器上并存。1.x 的自动部署在大版本边界停下（v1.0.117 的桥），所以升到 2.0 是手动的一次：

```bash
cd ~/Projects/zelin-ai-assistant
git branch keep/local-$(date +%Y%m%d) main     # 本地若有 origin 上没有的 commit，先留底
git fetch --tags origin && git checkout main && git reset --hard v2.0.0
bash install.sh
```

`install.sh` 的第 4 步：bootout 并删除 1.x 的十个 LaunchAgent（actd、autodeploy、各雷达、syncd、weeklydigest 与
`com.zelin.screenpipe-{process,prune}`），只 unlink `~/.claude/skills` 里指向本 repo 的软链（你自己的 skill 不碰），删
`deploy_state.json`；第 7 步把 crontab 里的 1.x 行（带 radar 尾巴的旧链、digest、telemetry）收成一条 ingest 链。它还会**打印**
（不执行）删除 1.x headless transcript 的命令（`~/.claude/projects/` 下以 `-…-zelin-ai-assistant-state` 开头的目录）——那是
你的 Claude Code 目录，要删还是归档由你决定。正在录的引擎不受影响。

> ✅ **预期状态**：doctor 的 `isolation footprint` 行 `[ ok ]`；`launchctl list | grep zelin` 只有 server。

## 卸载

```bash
cd ~/Projects/zelin-ai-assistant
bash uninstall.sh --dry-run   # 先预览：只打印计划，不改任何东西
bash uninstall.sh             # 执行（y/N 确认）
```

它删除 §78 清单里的每一项：LaunchAgent（`com.zelin.aiassistant.*` 与 1.x 的 `com.zelin.screenpipe-*`）、crontab 里**只属于本产品**
的行、app bundle、`~/Library/Logs/zelin-ai-assistant`、`~/Library/Caches/zelin-ai-assistant`、Application Support 里的
`bin/`、`claude-config/` 与 `deploy_state.json`、ingest 的锁文件与日志。**录制引擎默认照录**（加 `--stop-engine` 才停）。
它打印、不执行：`tccutil reset All com.zelin.ai-board`、登录项的手动检查、1.x transcript 的删除命令。

**默认保留你的数据**——会议纪要与设置（`state/`）、`config.yaml`、API key（`config/secrets/`）、Obsidian vault、`~/.screenpipe`，
结尾逐项附删除命令。`bash uninstall.sh --purge` 一并删除（vault 永不碰）。

> ✅ **预期状态**：`launchctl list | grep zelin` 无输出；`crontab -l` 里没有我们的行；`~/.claude` 与装机前一字不差。

## 这条路线在 CI 上每天都走一遍

`.github/workflows/fresh-install.yml`（"Fresh install (macOS)"）在每次 push 到 main、每晚一次以及手动触发时用一台干净的 macOS runner 复现本页（不按 PR 跑——macOS runner 稀缺，CONTRACT §56.8）：空 `$HOME`、本地 origin、`bash scripts/bootstrap.sh`（即 `bash install.sh --non-interactive`，真的装 LaunchAgent、真的写 crontab）→ 断言安装报告；断言封闭足迹（CONTRACT §78）：`~/.claude` 从未被创建、`~/Library/LaunchAgents` 恰好只有 `com.zelin.aiassistant.server.plist`、crontab 里恰好一行属于本产品；安装后没有被改动的 tracked 文件；LaunchAgent 起的 server 答 `/api/health` `/api/recaps` `/api/setup` `/api/permissions`；`doctor --fresh-install` 退出 0；第二次运行是更新且足迹不变；最后 `bash uninstall.sh --yes` 逐项删干净。绿 = 「另一台电脑、空白环境、一条命令、直接能用」成立（CONTRACT §69.4 与其 v2.0.0 追记）。

## 附：GitHub Release 里有什么

每个 release（CONTRACT §56.2）带三个资产：`ZelinAIAssistant-<tag>-macos.zip`（CI 上用 `shell/build.sh` 构建、带 build provenance attestation 的 Dock app，`gh attestation verify <file> --repo Wan-ZL/zelin-ai-assistant` 可验）、`checksums.sha256`、以及零条目的 `appcast.xml`（CONTRACT §74.3：让残留的 1.x 菜单栏 app 的 Sparkle 永远读到「已是最新」）。app 依赖它的 checkout（server、ingest 脚本、纪要代码都在仓库里），所以安装路线仍是上面的 bootstrap；zip 适合核对或单独替换 bundle。2.0 不再发 .pkg、不再发 Linux / Windows 包（CONTRACT §74.1 / §74.2 tombstone，1.x 的安装器在 tag `full`）。
