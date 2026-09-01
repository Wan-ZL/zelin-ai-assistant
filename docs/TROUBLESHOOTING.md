# TROUBLESHOOTING — 按症状排障

已知故障模式的单一落点,按**症状 → 原因 → 修复**组织。更多背景和 war story 见 `HANDOFF.md` §3。

## 重装/更新 app 后录屏坏了:开关看着开着,引擎启动即退

**症状**:`state/engine.log` 里 `permission monitor screen=true` 却 `no monitors available`;系统设置里"屏幕录制"开关看着还开着。

**原因**:`mac/build.sh` 用 ad-hoc 签名(无开发者证书),TCC 把授权绑定到签名指纹上——每次重新构建安装后,"屏幕录制"授权会**静默失效**(ScreenCaptureKit 枚举 0 台显示器)。

**修复**:

```bash
tccutil reset ScreenCapture com.zelin.ai-engineer
```

重启 app 让它重新请求授权,然后在 系统设置 → 隐私与安全性 → 屏幕录制 重新打开开关。日常使用不受影响;只在 app 更新后需要重做一次。

**永久修复(维护者一次性)**:根因是 ad-hoc 签名——换成一个**稳定的 self-signed code-signing 证书**(免费、不需要 Apple Developer 账号)后,签名指纹跨版本不变,TCC 授权就**不再**因为更新而失效。做法:本地跑一次 `bash mac/scripts/make-signing-cert.sh` 生成稳定证书并导入 login keychain(`mac/build.sh` 会自动认出名为 `Zelin AI Engineer Dev` 的证书来签名);再把脚本打印出来的两个值加成 GitHub secret(`MACOS_SIGN_CERT_P12` 和 `MACOS_SIGN_CERT_PASSWORD`),CI 的 release 构建(`.github/workflows/release.yml`)就会用同一个身份签名。**一次性过渡**:第一个稳定签名的版本因为身份从 ad-hoc 变成 self-signed,会**再弹一次**屏幕录制授权(照上面的 `tccutil reset` + 重新打开开关做一遍),之后所有更新都不再弹。注意 self-signed **不是** notarized,Gatekeeper 首次打开仍需右键→打开(见 `docs/INSTALL.md`)。

## 雷达静默数天没有新卡 / headless claude 在 cron 下直接死

**症状**:数天没有任何新审批卡;`state/radar.cron.log` 里 claude 报 auth 错误或 `command not found`,而手动在终端跑一切正常。

**原因**:cron/launchd 的 daemon session 有两个坑(部分机器如此):① 读不了 Keychain OAuth 凭证;② PATH 里没有 `~/.local/bin`,claude 二进制找不到。

**修复**:headless Claude 优先用文件形式的 API key——打开 app 设置窗口粘贴保存(写入 `config/secrets/anthropic-api-key.txt`,见 `docs/CONTRACT.md` §19);cron/launchd 里的 claude 调用一律用绝对路径。两个 key 文件都缺失时会回退到 claude CLI 自带凭证(常开的 Mac mini 上 cron 通常能用,但不可靠)。

## 后台服务起不来:`ModuleNotFoundError: No module named 'act'`(或 `'yaml'`),KeepAlive 反复重启

**症状**:`launchctl list | grep zelin` 显示 agent 状态非 0(常见 1),`~/Library/Logs/zelin-ai-assistant/actd.launchd.log` 里只有一行 `ModuleNotFoundError: No module named 'act'` 或 `No module named 'yaml'`;同一条命令在终端里手动跑完全正常。看板因此不再更新。

**先看日志里到底是哪个模块** —— 这两条长得一样,修法却相反:

- `No module named 'yaml'` → 缺 PyYAML,见下面原因 2。
- `No module named 'act'` → **不是** PyYAML 的事,是解释器根本**看不见 repo**,见原因 1 和 3。

**原因**(三个,CONTRACT §55):

1. **plist 里烧进了 symlink 形状的路径**。repo 实体在外置卷上、而你习惯用一条便利 symlink 进去(例如 `~/Projects -> /Volumes/…`),install.sh 就会把 symlink 路径写进 `PYTHONPATH` / `AIASSISTANT_HOME`。launchd 起的进程经这个路径形状被 TCC 拒绝,于是 import 不到 `act`。
2. **pin 的解释器没有 PyYAML**。`config/runtime.json` 指到一个 `import yaml` 会失败的 python3(Homebrew 新装的 3.14 最常见),agent 在写下任何日志之前就退出。
3. **pin 的解释器有 PyYAML、路径也全对,但它没有读 repo 的权限**(路径修好之后才露出来的那一幕)。macOS 的文件访问授权**按二进制单独计算**,而 launchd 起的任务是它自己的 responsible process ——**不继承**你终端或 app 的授权。于是 `/usr/bin/python3` 读得了 `/Volumes/…` 上的 repo,`/opt/homebrew/bin/python3` 读不了,而两个都能 `import yaml`,老版本的单闸门恰好挑中瞎的那个。

**怎么区分 1 和 3**(两者症状字面完全相同):跑 `grep -A1 PYTHONPATH ~/Library/LaunchAgents/com.zelin.aiassistant.actd.plist`,把里面的路径与 `cd <repo> && pwd -P` 对比 —— **对不上就是原因 1**(路径形状错),**一字不差却仍然崩就是原因 3**(路径对,是解释器没权限)。想直接验原因 3,拿 plist 里那个解释器跑一次:

    /opt/homebrew/bin/python3 -c "import os; print(len(os.listdir('<repo 的物理路径>')))"

在终端里跑**必然成功**(终端把自己的授权借给了子进程),所以这条只用来确认解释器本身没坏 —— 真正的判据是 launchd 会话里的行为,`python3 -m act.doctor` 已经替你判好了。

**确认**:`python3 -m act.doctor` —— `launchd paths` 行会点名携带 symlink 路径的 agent(原因 1);`launchd python` 行两种原因都管,文案会告诉你是「cannot `import yaml`」(原因 2)还是「imports yaml … yet … cannot READ the repo」(原因 3)。

**修复**:三种原因都从在 repo 目录里重跑 `bash install.sh` 开始 —— 它用 `pwd -P` 解析物理路径,并且只 pin **两道闸门都过**的解释器:能 `import yaml`,而且**被 launchd 起起来时真能 import 到 `act`**(安装器会起一个一次性 launchd 任务实测,亚秒级,跑完自己清理)。repo 在 `$HOME` 之外时它会优先试 `/usr/bin/python3` —— 那是唯一带着你自己文件授权的系统解释器。一次重渲染全部 agent(app 里的「一键修复」只重渲染 actd,所以命令行这一遍更彻底)。

没有任何候选 python3 带 PyYAML 时 install.sh 直接报 `[ERR ]` 并给出 pip 命令。若所有候选都过不了 launchd 那道闸门(例如机器上只有一个 python),它会照实说,这时再手动给那个解释器二进制授「完全磁盘访问」:系统设置 → 隐私与安全性 → 完全磁盘访问 → `+` → `Command`-`Shift`-`G` 粘贴解释器路径。

## 派发反复失败:`possibly due to low max file descriptors`(实为 claude 读不到任务目录)

**症状**:卡批准后每次派发都失败,错误 chip 写着 `An unknown error occurred, possibly due to low max file descriptors (Unexpected)`;`state/actd.log` 每十几秒一条 `dispatch: R-xxx FAILED`;终端里手跑 `claude --bg` 正常。v0.48.4 起同一张卡连续失败 5 次后会**停止重试**并挪到「需输入」列,通知与卡片都写明原因(CONTRACT §4.1、§25 `claude_blind`)。

**原因**(CONTRACT §55 第三幕):**不是**文件句柄。claude 是 Bun 编译的单文件程序,Bun 把它不认识的 errno 统一渲成这句猜测(真正的句柄耗尽它会写 `ProcessFdQuotaExceeded` / `EMFILE`)。这里的 errno 是 **EPERM**:macOS 按可执行文件路径授「完全磁盘访问」,终端里的 claude 继承终端的授权,launchd 起的 claude 只看它自己那一行——而 `~/.local/share/claude/versions/<版本>` 每次更新都是新路径,从来没被授权过。任务 repo 在外置卷(或 ~/Documents、~/Desktop、~/Downloads)上时,claude 一起来就在 `getcwd` 上被拒。2026-08-31 这台机器把 `~/Projects` 搬到外置卷后当天就撞上;当晚给 plist 加 8192 上限的 hotfix 生效后又失败了 11 次(`Current limit: 8192`),这才是证伪。

**确认**:`python3 -m act.doctor` 的 `launchd claude` 行——它在一个一次性 launchd job 里以默认工作 repo 为 cwd 跑 `claude --version`(终端里跑永远是好的,只有 launchd 会话能复现):FAIL `claude_blind` = 就是本条;WARN 且写着 never exited = cwd 在 ~/Documents 这类会弹提示的目录,job 没有界面所以挂住。旁证:`~/.local/share/claude/versions/<版本>` 出现在「完全磁盘访问」列表里且**未打开**——被拒过一次 macOS 就会把它列出来。

**修复**(两条路,选一):

1. 系统设置 → 隐私与安全性 → 完全磁盘访问 → 打开 claude 当前版本那一项(不在列表里就 `+` → `Command`-`Shift`-`G` 粘贴 `~/.local/share/claude/versions/<版本>`)。**claude 每次自动更新后要重做**——授权跟路径走。
2. 把任务 repo 放回启动盘的家目录下(不在 Documents / Desktop / Downloads 里),改 `config.yaml` 的 `execution.default_target_repo`。

然后 `python3 -m act.doctor` 确认 `launchd claude` 行变 OK,再在看板上把停住的卡「停止 → 退回提案」再批准一次(批准会清掉整条失败台账;hand 卡免批通道也会自动接手)。一张卡真的到「执行中」才算修好。结构性根治(一次授权、子进程全继承)是由有授权的 GUI app 托管后台服务,记在 `docs/design/vnext2-plan.md` 等 owner 拍板。

**附带的资源上限**:launchd 给后台任务的默认是 soft `ulimit -n` 256 / hard unlimited。模板自 v0.48.4 起只抬 soft 到 8192(余量);**不要**再手加 `HardResourceLimits`——它把 unlimited 压成 8192,doctor `launchd fd limit` 行会 WARN,重跑 `bash install.sh` 即去掉。

## 看板不更新,但 `launchctl list` 显示 actd 有 pid(进程活着、循环死了)

**症状**:看板顶部横幅「后台服务卡住了」/ doctor `actd heartbeat` 行 FAIL `actd_stalled`;`state/dashboard.json` 与 `actd.log` 的 mtime 几小时不动,`launchctl list | grep actd` 却给出 pid,没有子进程。2026-08-31 22:31 这台机器就这样静默了 2.5 小时。

**原因**:进程没死,主循环卡住了(卡在某个 pass 阶段,或 `time.sleep` 之后再没醒)。§47.3 的 `loop_health.json` 只数 pass **崩溃**,它没崩所以一路 0;`launchctl` 只知道 pid 在。v0.48.4 起 actd 在每个 pass 的每个阶段 touch `state/actd.heartbeat`(CONTRACT §47.4),心跳超过 `max(3 × interval, 90)` 秒没动 = 卡死。

**确认**:`python3 -m act.doctor`(`actd heartbeat` 行会说最后一次心跳的阶段与多久之前);或 `curl -s http://127.0.0.1:47820/api/health`(`verdict: "stalled"`)。

**修复**:kill+respawn,**不是** reload:`launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd`(Linux:`systemctl --user restart zelin-actd.service`)。重启后心跳恢复,横幅自动消。反复出现请把 `state/actd.heartbeat` 里的 `phase`(卡死时的阶段)带进 issue。

## `launchctl list | grep zelin` 里有仓库早已删掉的 agent(孤儿)

**症状**:doctor `launchd orphans` 行 FAIL/WARN `launchd_orphan`,或 `~/Library/Logs/zelin-ai-assistant/<name>.launchd.log` 疯长(2026-08-31 审计:`imessageradar` 退役 51 天还在跑,23,613 条 traceback、14.5 MB)。

**原因**:旧版 install.sh 卸载退役 label 时把 `launchctl bootout` 的失败吞掉了。v0.48.4 起卸载会自证(失败就 `[ERR ]` + 安装报告 `launchd_retired=fail`),并列出带 `com.zelin.aiassistant.` 前缀却已无模板的 label(只报告不动手)。

**修复**:重跑 `bash install.sh`(RETIRED 名单里的会被卸载并验证);不在名单里的孤儿手动 `launchctl bootout gui/$(id -u)/<label> && rm ~/Library/LaunchAgents/<label>.plist`。日志文件是取证材料,脚本不删——确认不需要后自己 `rm`。

## launchd 任务读不到 ~/Documents:radar 扫到空 vault,零报错

**症状**:vault 里明明有新笔记,radar 却什么都扫不出来,日志无报错。

**原因**:launchd 进程被 TCC 挡在 `~/Documents` 之外,Obsidian vault 恰好在里面。

**修复**:读 Documents 的定时任务走 crontab(给 `/usr/sbin/cron` 授 完全磁盘访问权限,准确路径见 `docs/INSTALL.md` 步骤 6),launchd 只做不碰 Documents 的活。`install.sh` 装的 cron 链已按此设计。

v0.14 起这个失败**不再静默**:cron 链每轮把真实读取结果写进 `state/cron_probe.json`(CONTRACT §25),app 主窗口「依赖检查」页的「定时任务磁盘权限」行会变红并给出一键引导(复制 `/usr/sbin/cron` + 打开授权面板 + 行内步骤);`python3 -m act.doctor` 的 `cron disk access` 检查同源。

## 「让 AI 修 / Fix with AI」按钮做了什么(安全姿态)

app 里所有无法一键修复的错误旁都有「让 AI 修」按钮(= `python3 -m act.ai_fix --open`)。它做的事:

1. 本地生成诊断包:`doctor --fast` 报告 + `state/actd.log` / `actd.launchd.log` / `radar.cron.log` / `~/.screenpipe/engine.log` 各末 40 行——写盘**之前**先过 `act/lib/sanitize.scrub`(掩掉 API key/token/私钥与你的词表);
2. 在 `$TMPDIR` 生成一个 `.command` 并交给 Terminal:里面只是 `cd <repo> && claude "<诊断 prompt>"`,**不带** `--dangerously-skip-permissions`——AI 改任何文件、跑任何命令都要你确认;
3. prompt 要求 AI:先一句人话说清根因 → 最小修复 → `doctor --fast` 复验 → 最后给一个预填好的 GitHub new-issue 链接(正文只含诊断行与结论,发不发由你)。

不想要这个入口:config.yaml 里设 `doctor.ai_fix_enabled: false`(按钮隐藏、CLI 直接退出)。

## Slack / Atlassian 接入在 headless 下不工作

**症状**:前台会话里 MCP 能用,cron/launchd 跑起来就挂。

**原因**:Slack/Atlassian MCP 的 OAuth 在 headless 下未验证。

**修复**:走 token 兜底——Slack user token / Atlassian API token 写入 `config/secrets/`(推荐在 app 设置窗口粘贴保存,见 `docs/CONTRACT.md` §19 与 `docs/SLACK_SETUP.md`)。

## 开发注意(新组件必读)

执行器必须注入 auto-memory 的 program map 与约束(例如:eval 走统一 CLI、数据放固定目录、云端资源命名规则等)——否则执行 agent 会自行发明布局。对应 config 键 `execution.memory_inject`(默认开),实现在 `act/executor.py`。
