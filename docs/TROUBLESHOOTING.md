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

## 换壳后的 TCC 重授权:第一次在看板 header 开「录制」/「实时字幕」会弹系统提示(v0.48.19 起)

**症状**:从原生菜单栏 app 换到 "Zelin AI Board" 壳(D3,CONTRACT §61)后,第一次在看板右上角点「录制 → 仅屏幕」弹出 macOS「屏幕录制」授权提示;第一次开「实时字幕」(音源含麦克风)弹「麦克风」提示;在授权之前 header 显示 `录制:未在录制`,菜单首行写「缺「屏幕录制」权限」。

**原因**:这是预期行为,不是故障。TCC 授权按 **bundle id + 签名**归属:原生 app 是 `com.zelin.ai-engineer`,壳是 `com.zelin.ai-board`(审计 Q1 决定保留新身份),两者在系统设置里是两条独立的记录;录制引擎(screenpipe)现在是**壳的直接子进程**、字幕的麦克风/系统声音采集在**壳进程内**,所以两项授权都要给壳重新点一次。壳启动时会一次性把原生 app 里的录制模式/字幕偏好接过来(§61.4),但**刻意不**继承「曾授权过」标记——新身份要自己拿授权。

**修复**:

1. 屏幕录制:点菜单里的「打开系统设置 → 屏幕录制」(或 系统设置 → 隐私与安全性 → 屏幕录制),给 **Zelin AI Board** 打开开关。壳每 5 s 探一次授权,授权一生效引擎自动重启(与原生 app 同一自愈路径,通知「录制已就绪」)。
2. 麦克风:系统提示直接点允许;拒绝了就到 系统设置 → 隐私与安全性 → 麦克风 打开 **Zelin AI Board**,再把「实时字幕」关一次开一次。
3. 壳目前仍是 ad-hoc 签名(P4 过渡期):每次重新 `bash shell/build.sh` 装机后屏幕录制授权会像上一节一样失效——`tccutil reset ScreenCapture com.zelin.ai-board` 后重新打开开关即可。稳定证书随 Mac-retire 清单一起落地后不再需要。
4. 两个 app 同时在跑时(旧 app 已改名 `-old` 备用),谁最后切换模式谁持有 screenpipe 子进程——不必同时开着;只保留壳在跑即可。

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

## 外置盘 + launchd 权限:自动部署每 10 分钟原地打转,或「已 up_to_date」却还在跑旧版本

**症状**(任一):① doctor `launchd volume access` 行 FAIL `deploy_blind_tcc`;② `~/Library/Logs/zelin-ai-assistant/auto-deploy.log` 里每 10 分钟一行 `volume_access=denied (errno 1) — launchd job lacks access to /Volumes/<卷>; grant Full Disk Access to <解释器>`;③ `autodeploy.launchd.log`(launchd 的 stderr,没时间戳)里 `PermissionError: [Errno 1] Operation not permitted`、`rm: … Operation not permitted`、或 `ModuleNotFoundError: No module named 'act'`;④ 顶栏/doctor 写着 `install_incomplete`——checkout 已在新 sha,`state/install_report.json` 与 `state/actd.heartbeat` 却还是旧版本。而你在终端里手跑 `bash scripts/auto-deploy.sh`(或 `launchctl kickstart` 之后马上看)**一切正常**。

**原因**(CONTRACT §56.3 第 1 步、§55 第四幕):repo 住在外置卷(USB/APFS,`/Volumes/…`)上。macOS 按 **responsible executable** 给「可移动卷」授权,launchd 起的任务是它自己的 responsible process、**没有界面接弹窗**,于是默认被拒(errno 1,EPERM);而终端里跑的每一次都把终端(它有完全磁盘访问)的授权借给全部子进程,所以「我手跑是绿的」**对无人值守的运行什么都不证明**。2026-09-02 的实录:timer 起的一轮先把 checkout 推到 v0.48.11(git 碰巧读得到),然后 `bash install.sh` 拿到 EPERM(exit 126)、回滚被拒、`state/deploy_state.json` / 通知队列 / 锁全部写不进去;20 分钟后下一轮看到 HEAD == origin/main 就写了 `up_to_date`,而 actd 内存里还是 v0.48.8。

v0.48.20 起脚本自己会把这件事说出来:每轮**先探针再碰 git**(读 repo、在 `state/` 里 mkstemp),被拒就记 `blocked_tcc`、HEAD 不动、一天最多通知一次;判决和锁都先写进 `~/Library/Application Support/ZelinAIAssistant/`(TCC 从不拦 `$HOME`),repo 里的 `state/deploy_state.json` 只是尽力而为的投影;`up_to_date` 的定义收紧为「HEAD 到位 **且** install_report 与 actd 心跳都是这个版本且心跳新鲜」,否则 `install_incomplete` 并在本轮重跑一次 `install.sh`(连续 3 轮无效即停并通知)。

**确认**:`python3 -m act.doctor` —— `launchd volume access` 行读的是**无人值守那一轮**留下的记录(镜像的 `unattended_status`),不是你刚在终端跑的那一轮;它会点名 plist 里那个解释器的精确路径。想直接看证据:`cat "$HOME/Library/Application Support/ZelinAIAssistant/deploy_state.json"`(看 `unattended_status` / `unattended_detail` / `interpreter` / `volume`)。

**修复**(两条授权都要加):

1. 系统设置 → 隐私与安全性 → 完全磁盘访问 → `+` → `Command`-`Shift`-`G` 粘贴路径,加**两条**:① 后台任务的解释器 = doctor 行给出的 `~/Library/LaunchAgents/com.zelin.aiassistant.autodeploy.plist` 的 `ProgramArguments[0]`(这台机器上出过事的是 `/Applications/Xcode.app/Contents/Developer/usr/bin/python3`;**按路径授权,换了解释器要重授**);② `/Users/<你>/.local/bin/claude`(实体在 `~/.local/share/claude/versions/<版本>`,claude 每次更新后重做,见上一节)。
2. **等 timer 自己触发一轮(≤ 10 分钟)**,再 `python3 -m act.doctor` 看 `launchd volume access` 行变 OK、`auto-deploy` 行变 `deployed` / `up_to_date`(`tail -f ~/Library/Logs/zelin-ai-assistant/auto-deploy.log` 能看到那一轮)。**不要拿自己起的运行当证据**:2026-09-02 的观察是,从终端起的每一次——`bash scripts/auto-deploy.sh`、`python3 -m act.auto_deploy`、乃至在终端里敲的 `launchctl kickstart`——都绿,只有 timer 触发的那一次被拒:终端把自己的授权借给了它起的一切,绿了对 timer 触发的运行什么都不证明。从进程内部看 kickstart 与 timer 分不出来,所以一次终端 kickstart 可能让 doctor 行在下一次 timer 触发前短暂显示 OK——那不是修好。
3. 替代路线:把 repo 搬回启动盘的家目录下(不在 Documents / Desktop / Downloads 里),重跑 `bash install.sh` 重渲 plist。

**别做**:不要用 `--force` 去「修」`blocked_tcc`——它只是从终端借了一次授权,下一轮 timer 照样被拒;也不要手动删 `~/Library/Application Support/ZelinAIAssistant/deploy_state.json`——那是脚本的记账(failed_sha / notified_sha / incomplete_sha 都在里面),删了它会忘掉哪个 sha 已经失败过。

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

## store2 回滚:把卡片账本从 SQLite 切回 YAML(保留一个版本的开关)

**什么时候用**:升级到 v0.48.8+ 后,actd 第一个 pass 会自动做「备份 → 迁移 → 逐字段比对 → 零差异才切换」,把卡片真源从 `act/registry/*.yaml` 切到 `state/store2.db`(CONTRACT §53.3)。如果切换后发现任何不对劲(卡不见了、doctor `store2` 行 FAIL `store2_db_missing`、或你就是想回去),按下面手动回滚——迁移永远先留完整备份,数据不会丢。

**症状确认**:`python3 -m act.doctor` 的 `store2` 行;或 `python3 -m act.lib.store2.activate --report` 打印状态 JSON(`state` ∈ active / refused / db_missing / yaml_forced / pending)。

**回滚步骤(照顺序做)**:

1. **停守护**:`launchctl bootout gui/$(id -u)/com.zelin.aiassistant.actd`(Linux: `systemctl --user stop zelin-actd.service`);雷达是短命进程,不用管。
2. **恢复备份**:激活时的完整 YAML 备份在 `state/backups/registry-<时间戳>/`(旁边的 `.manifest.json` 是逐文件 sha256 清单)。把它整目录拷回:

       cp -R "state/backups/registry-<时间戳>/." act/registry/

   注意:激活**不会**清空 `act/registry/`,里面通常还是切换当刻的原文件;真正需要拷贝的场景是你在 SQLite 上又跑了一段时间(那段时间的新卡只在 DB 里——想要它们的话先 `python3 -m act.lib.store2.activate --export-now`,把 `state/registry-export/` 里对应的 `R-*.yaml` 挑进 `act/registry/`)。
3. **设回滚开关**:`config.yaml` 里加(或改):

       registry:
         backend: yaml

   开关强制 YAML 为真源,store2 标记被无视、每日导出停止(CONTRACT §53.6)。server 的卡详情读(`/api/cards/{id}`)同样跟随开关(逐请求读 config,不用重启 server)。开关保留一个版本——它在的期间不会再自动迁移。
4. **重启**:`launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd`(或重跑 `bash install.sh`)。`python3 -m act.doctor` 应显示 `store2: YAML 后端(registry.backend/env 强制)`。

**想再切回 SQLite**:删掉 config 里的 `registry.backend` 键(或设 `auto`),删 `state/store2_truth.json` + `state/store2.db`,重启 actd——下一个 pass 重新走一遍完整激活协议(重新备份、重新比对)。

**激活被拒(doctor FAIL `store2_refused`)**:这不是故障,是保护——某张卡的形态无法忠实入库(最常见:手编 YAML 里有拼错的未知字段名,或未加引号的日期值——`deadline: 2026-09-15` 会被 YAML 解析成日期对象,JSON 装不下,给值加引号即可)。`cat state/store2_activation.json` 看逐条 diff,修好点名的卡文件后等重试(数据类拒绝退避 6 小时;删掉 `state/store2_activation.json` 立即重试)。拒绝期间 YAML 一直是真源,管线照常。

### schema 降级:代码回退到旧版本后账本打不开(v0.48.15 起)

**症状**:每次 registry 调用都抛 `StoreError: db user_version=2, store2 supports 1`;actd 每个 pass 报同一条 traceback、heartbeat `phase` 永不到 idle,inbox 不处理、不派工、看板冻结。旧版 doctor 的 `store2` 行不真正开库,所以**照样显示绿**——别被它骗。

**为什么**:v0.48.15 起 store2 有 schema 升级梯子(CONTRACT §53.1),新代码第一次开库就把 `user_version` 升上去,而升级是**单向的**——旧代码对更高版本 fail-closed,这是设计(带着不认识的 schema 盲写更糟)。自动部署(§56.3)的回滚跑的是**回滚目标那一版**的 `scripts/auto-deploy.sh`,所以「部署期间 `user_version` 升高 → 拒绝代码回滚」这道闸门(PR #130)只在 #130 已合并**并已部署到本机**之后才护得住跨升级的部署;在那之前的自动回滚,以及任何手动 `git reset` / `checkout` 到旧版,都会走到这一步。**数据本身完好**——v2 库无损,只是这份代码打不开它。

**三条出路(选一,都先停守护 `launchctl bootout gui/$(id -u)/com.zelin.aiassistant.actd`)**:

1. **向前滚(推荐)**:回到 ≥ 0.48.15 的代码——`git -C <repo> reset --hard <新版本 sha>` 后 `bash install.sh`,或 `bash scripts/auto-deploy.sh --force`。什么数据都不丢。
2. **恢复升级前快照**:升级踏出前 store 自动留了 `state/store2.db.pre-v1`(旧代码打得开的 v1 副本,CONTRACT §53.1 单向门条款)。先把 v2 库**连同它的旁文件**挪走保存(升级之后新写的卡只在它里面;`-wal` 可能装着尚未 checkpoint 的已提交写入,**必须跟着主库走、同名后缀改名,什么都不删**):

       for s in "" -wal -shm; do
         [ -e "state/store2.db$s" ] && mv "state/store2.db$s" "state/store2.db.v2-stranded$s"
       done

   再 `cp state/store2.db.pre-v1 state/store2.db`(快照是单文件,没有旁文件),重启。想要 stranded 里的新卡,回到新代码后再比对/导出(`state/store2.db.v2-stranded` 连旁文件一起原样可开)。
3. **YAML 回滚**:按上面「store2 回滚」的常规步骤(恢复 `state/backups/registry-<ts>/` + `registry.backend: yaml`)。注意旧代码开不了 v2 库,`--export-now` 只能在 ≥ 0.48.15 的代码下先跑。

**绝对不要**:手改 `PRAGMA user_version` 把 v2 库伪装成 v1。实测后果:旧代码的 save 会把 payload 里的 `work_id` 剥掉而热列还留着(切回新代码后靠 §60.2 的采纳防御才能从热列把号找回,别赌);更致命的是旧 `next_id()` 会把已发的工作编号(比如 R-264)当主键铸新卡——主键与工作编号从此撞号,`resolve()` 出现歧义,这一步没有任何防御能救。

**快照文件的收拾**:`state/store2.db.pre-v<n>` 一级只有一份,只在真的再次踏出该级升级时才被刷新(恒为「最近一次升级前」的状态);新版本跑顺(auto-deploy 报 deployed、看板正常)之后可以删。

**升级本身被拒(`StoreError: SCHEMA_SNAPSHOT_FAILED`)**:新代码拍不下升级前快照(磁盘满、`state/` 不可写、外置卷瞬态 EPERM)就**不**踏出单向门——DB 留在旧版本,新旧代码都还能开它,下一次开库自动重试;排除写入障碍即可,不需要手动干预数据。

## 版本号不对:doctor `version` 行 WARN、看板顶栏 / `python3 -c "import act; print(act.__version__)"` 报的不是 tag(v0.48.17 起)

版本的真源是 main 上的 git tag(CONTRACT §56.1),**没有任何文件里写着版本**。`act.__version__` 按 `act/_version.py`(生成文件、git-ignored)→ `git describe` → `act/__init__.py` 的烘焙回落行解析;守护进程只读 stamp。三种症状:

- **`no act/_version.py`**:从没跑过 install.sh(或跑失败)。修:`bash install.sh --non-interactive`(重盖章 + 重启 daemons),或只盖章 `python3 scripts/version_stamp.py --write` 再 `launchctl kickstart -k gui/$UID/com.zelin.aiassistant.actd`。
- **stamp 与 checkout 不一致**(`act/_version.py 说 v0.48.16,checkout 是 v0.48.17`):手动 `git pull` 了但没跑 install.sh。同上修法。
- **报 `X.Y.Z+N`**:HEAD 领先最近的 tag N 个 commit——本地 tag 没跟上(`git fetch --tags origin`)或这是一个未发版的开发分支。live 机器上 auto-deploy 的 fetch 自带 `--tags --force`;手动 `git fetch --tags --force origin && bash install.sh --non-interactive`(`--force`:本机一个与 origin 同名不同指的旧 tag 会让不带它的 `fetch --tags` 以 rc 1 拒绝,auto-deploy 就会每轮 `fetch_failed`)。

**首次部署 v0.48.17(切换到 tag 真源的那一版)被回滚**:那一轮跑的是旧部署脚本,它用 sed 读 `act/__init__.py` 的字面行当期望版本(§56.1 过渡条款)。若 release-on-merge 迟到或号猜错导致 `actd:no_heartbeat_from_new_version` 回滚,手动一次即可:`git -C <repo> merge --ff-only origin/main && bash install.sh --non-interactive`——新脚本上机后不再依赖这一切。

## 开发注意(新组件必读)

执行器必须注入 auto-memory 的 program map 与约束(例如:eval 走统一 CLI、数据放固定目录、云端资源命名规则等)——否则执行 agent 会自行发明布局。对应 config 键 `execution.memory_inject`(默认开),实现在 `act/executor.py`。
