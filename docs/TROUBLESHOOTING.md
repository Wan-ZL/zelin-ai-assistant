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
