# TROUBLESHOOTING — 按症状排障

lite 2.0 已知故障模式的单一落点，按**症状 → 原因 → 修复**组织。第一步永远是 `bash install.sh --check`
（= `python3 -m act.doctor`，退出码 = FAIL 行数，每行带一句修法）。背景见 `HANDOFF.md` §2；1.x（卡片、雷达、
派发、自动部署、菜单栏 app）的排障条目随 Act 管线退役，全文在 git tag `full`。

## 重装 / 更新 app 后录屏坏了：开关看着开着，引擎启动即退

**症状**：`state/engine.log` 里 `permission monitor screen=true` 却 `no monitors available`；系统设置里「屏幕录制」开关看着还开着。

**原因**：TCC 把授权绑在签名身份上。壳用 ad-hoc 签名构建时，每次重新构建都换身份，「屏幕录制」授权**静默失效**。

**修复**：`bash shell/scripts/make-signing-cert.sh` 生成一张稳定的自签证书并导入 login keychain，之后 `shell/build.sh`
认出它就用它签名，更新不再掉授权。已经掉了的这一次：`tccutil reset ScreenCapture com.zelin.ai-board`，重开 app，在
系统设置 → 隐私与安全性 → 屏幕录制 里打开 **Zelin's AI Assistant**。从 ad-hoc 换到稳定身份的那一次会再弹一轮授权（屏幕
录制、麦克风、文件与文件夹），之后不再弹。

**录制还在、只是由 Ghostty 之类的终端起的**：引擎持有的是起它那个进程的授权。壳启动时会接管带 recipe 标记的现成引擎
（关于页显示 recording (adopted)，§61.8），但在权限页显示 com.zelin.ai-board 已授权之前，不要点「重启录制」。

## ingest 不出笔记 / 纪要不出：headless claude 在 cron 下退出

**症状**：`state/ingest.cron.log` 或 `/tmp/screenpipe-auto.log` 里有 `❌ no Anthropic API key`，`process-screenpipe.sh` 以 1 退出；
或 `state/recap.log` 里 claude 报认证错误。终端里手跑 `claude` 一切正常。

**原因**：每次 headless `claude -p` 都带 `--bare` 在自己的 `CLAUDE_CONFIG_DIR` 下运行（CONTRACT §78），**只认
`ANTHROPIC_API_KEY`**，永不读你的 Claude Code 登录或钥匙串。你终端里能用，是因为终端里的 claude 用的是你自己的登录。

**修复**：在 app 的 设置 → 凭证 粘贴 key（写入 `config/secrets/anthropic-api-key.txt`，0600），或
`bash install.sh` 时让它从 `~/.config/anthropic-key.txt` / `$ANTHROPIC_API_KEY` 复制。doctor 的 `anthropic key` 行变 OK 即可；
下一轮 cron（:00 / :30）就会出笔记。

## server 起不来：`ModuleNotFoundError: No module named 'act'`（或 `'yaml'`），KeepAlive 反复重启

**症状**：`launchctl list | grep zelin` 里 `com.zelin.aiassistant.server` 状态非 0，`~/Library/Logs/zelin-ai-assistant/server.launchd.log`
只有一行 `No module named 'act'` 或 `No module named 'yaml'`；同一条命令在终端里手跑正常。app 连不上 server。

**先看是哪个模块**——两条长得一样，修法相反：

- `No module named 'yaml'`：pin 的解释器（`config/runtime.json`）没有 PyYAML。
- `No module named 'act'`：解释器**看不见 repo**。要么 plist 里烧进了 symlink 形状的路径（`~/Projects -> /Volumes/…`），要么
  这个解释器没有读 repo 的权限——macOS 按二进制授文件访问，launchd 起的任务不继承你终端的授权（CONTRACT §55）。

**修复**：在 repo 目录里重跑 `bash install.sh`。它用 `pwd -P` 渲染物理路径，只 pin 能 `import yaml`、并且**被 launchd
起起来时真能 import act** 的解释器（起一个一次性 launchd 任务实测）；repo 在 `$HOME` 之外时优先试 `/usr/bin/python3`。
所有候选都过不了时它会照实说，这时给那个解释器授「完全磁盘访问」：系统设置 → 隐私与安全性 → 完全磁盘访问 → `+` →
`Command`-`Shift`-`G` 粘贴解释器路径。确认：doctor 的 `launchd paths` / `launchd python` / `board server` 行。

## Claude Code 更新后 headless claude 读不到 vault / repo

**症状**：Claude Code 自动更新后，cron 里的 ingest 读不到外置卷或 ~/Documents 上的东西；doctor `launchd claude` 行 FAIL
`claude_blind`。

**原因**：macOS 对命令行程序的完全磁盘访问**按路径**记账，而 `~/.local/bin/claude` 每次更新都指向新的
`~/.local/share/claude/versions/<版本>`。

**修复**：install.sh 维护一份稳定副本 `~/Library/Application Support/ZelinAIAssistant/bin/claude`（只接受带有效 Apple
签名的二进制，版本变了原地替换），recap 与 ingest 都起这一份（`config.resolve_claude_bin`）。给**这条路径**授一次完全磁盘
访问，之后 Claude Code 怎么更新都不用重做。副本落后一版时 doctor `stable claude` 行 WARN，重跑 `bash install.sh` 刷新。

## cron 读不到 ~/Documents：ingest 扫到空 vault

**症状**：vault 里有新文件，ingest 却说 inbox 为空；doctor `cron disk access` 行红。

**原因**：cron 起的进程同样按二进制受 TCC 管。

**修复**：给 `/usr/sbin/cron` 授完全磁盘访问（`docs/INSTALL.md` 第 3 项）。cron 链每轮把真实读取结果写进
`state/cron_probe.json`（CONTRACT §25），权限页的「定时任务磁盘权限」行与 doctor 同源。

## 有一台 1.x 留下的后台项：`isolation footprint` 行 WARN，或 `launchctl list` 里有已经删掉的 agent

**症状**：doctor `isolation footprint` 行 WARN `isolation_footprint_violation`，列出 `com.zelin.aiassistant.actd` /
`com.zelin.screenpipe-prune` 之类的 LaunchAgent、`act.radar` / `act.digest` 的 cron 行，或 `~/.claude/skills` 里指进本 repo
的软链。

**原因**：这台机器装过 1.x。lite 的常驻足迹是封闭清单（CONTRACT §78）：一个 server agent、一条 cron 行、opt-in 登录项、
录制引擎。

**修复**：`bash install.sh`。它的第 4 步（`migrate_from_1x`）bootout 并删除 1.x 的十个 agent、unlink 指进本 repo 的
skills 软链、删 `deploy_state.json`，第 7 步把 crontab 里我们的行收成一条 ingest 链（你自己的其它行原样保留）。它还会
**打印**一条删除 1.x headless transcript 的命令（`~/.claude/projects/-…-zelin-ai-assistant-state*`，几个 GB）——那是你的
Claude Code 目录，脚本只打印、不执行；想留就先归档。日志文件是取证材料，脚本不删。

## 「立即升级」没有升上去

**症状**：关于页点了「立即升级」，版本没变。`cat state/upgrade_state.json` 看 `status`：

- `diverged`：`reason` 是 `dirty`（工作树有改动）、`not_on_main` 或 `diverged`（本地 main 有 origin 上没有的 commit）。
  升级器**从不** reset / stash，`commands` 里是要你手动跑的命令（diverged 时先 `git branch keep/local-<日期> HEAD` 留底，再
  `git reset --hard <tag> && bash install.sh`）。
- `refused`（`untagged`）：origin/main 现在不是一个 `v*` release tag，等下一个 release 即可。
- `failed`：`reason=fetch` = 离线；`reason=install` = `install.sh` 有失败步，`state/install_report.json` 写着哪一步。没有自动回滚。
- `running` 卡住：`state/upgrade.lock` 目录还在而 `state/upgrade.log` 不再增长 = 上一轮被杀掉了，删掉那个目录再点一次。

**新的大版本**（关于页写着「需要手动升级」）：大版本永不自动跨，按页面给的命令手动 `git fetch --tags origin &&
git checkout v<版本> && bash install.sh`（CONTRACT §26 / §79）。

## `server.launchd.log` 里全是 `ConnectionResetError` 的 traceback，或想逐条看轮询请求

**原因**：浏览器关 tab / 刷新时 keep-alive 连接被 reset。当前版本对连接类异常静默、行首带本地时间戳，`/api/health`
之类的轮询按 (path, 状态码) 每 5 分钟最多一行（CONTRACT §54.2）。

**逐条看轮询**：给 server agent 的 `EnvironmentVariables` 加 `ZAI_LOG_POLLS=1`（改 `~/Library/LaunchAgents/com.zelin.aiassistant.server.plist`，
再 `launchctl bootout` + `bootstrap`），或前台跑 `ZAI_LOG_POLLS=1 python3 -m server`。重跑 `install.sh` 会用模板覆盖手改的 plist。
已经涨起来的日志由你处置：`: > ~/Library/Logs/zelin-ai-assistant/server.launchd.log` 就地清空。

## 版本号不对：doctor `version` 行 WARN

版本的真源是 main 上的 git tag（CONTRACT §56.1），没有任何文件里写着版本。`act.__version__` 按 `act/_version.py`（生成文件）→
`git describe` → `act/__init__.py` 的回落行解析。

- **`no act/_version.py`**：没跑过 install.sh。修：`bash install.sh --non-interactive`，或只盖章 `python3 scripts/version_stamp.py --write`
  再 `launchctl kickstart -k gui/$UID/com.zelin.aiassistant.server`。
- **stamp 与 checkout 不一致**：手动 `git pull` 了但没跑 install.sh。同上。
- **报 `X.Y.Z+N`**：HEAD 领先最近的 tag N 个 commit——`git fetch --tags --force origin && bash install.sh --non-interactive`。

## 开发注意

在 owner 的 checkout 上不要直接跑 `install.sh`：它会重载 server agent、重写 crontab。验证安装改动时在一份 repo 拷贝里用临时
`$HOME` 跑 `bash install.sh --non-interactive --no-launchd`；判例（`tests/test_install_*.py`、`tests/test_upgrade_script.py`）
都用假 `launchctl` / `crontab` / `pgrep` / `pkill`，从不碰真机。
