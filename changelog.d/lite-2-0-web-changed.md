type: changed
- **会议纪要成为默认页**：左侧导航栏只剩四项（会议纪要 / 录制与导入 / 设置 / 关于，⌘1–⌘4）；旧深链 `?page=board|trash|archive|skills|styleguide` 回落会议纪要，`?card=` 不再读。纪要列表直接读 `GET /api/recaps`、随 SSE `recaps.updated` 刷新，「重新生成 / 现在生成」与「回退到这一版」分别走 `POST /api/recaps/generate` 与 `POST /api/recaps/revert`。
- **设置页按 server 目录分区**：通用、通知、录制（含「不录制的应用 / 窗口」）、录制数据与磁盘、笔记库、更新（含「发现新版本时自动升级」）、脱敏，加上显示、模型（只剩管线与回退两把，出厂值不跟随你自己的 Claude Code 设置）、依赖检查、凭证（只有 Anthropic API key）、会议纪要。
- **关于页的升级与卸载**：「立即升级」调用 `POST /api/update/install`；新的大版本（409）或这个安装还没有升级器（501）时给出可复制的手动命令，不再打开 release 页。「卸载…」改为给出一条可复制的 `bash uninstall.sh` 命令，页面不再替你打开 Terminal。
- **首次运行向导缩为六步**，第 2 步改为检测 claude CLI 与 Anthropic API key（不再提供「用 Claude Code 登录」那一路）。
- **升级和重启壳不再打断录制**：壳启动时如果已有本 app 配方（`screenpipe@0.3.349 record … --ignored-windows …`）的录制引擎在跑，就直接接管它（记下 pid，写进 `~/Library/Application Support/ZelinAIAssistant/engine.pid`），不再先杀再起；别的引擎只记一行日志、不碰。手动退出壳时只停壳自己启动、此刻还在跑的那一台；安装器重启壳（自动部署路径）和升级脚本都会先写下 `engineHandoff` 偏好，这时一台都不停，新壳接管后清掉它。在页面上关闭录制照旧立即生效。
- **壳的连接探针改为 `GET /api/health`**；签名证书的一次性生成脚本搬到 `shell/scripts/make-signing-cert.sh`（签名身份与 bundle id `com.zelin.ai-board` 不变，屏幕录制授权不受影响）。
- **设置页「不录制的应用 / 窗口」对录制引擎本身生效**：壳在启动录制时先读这一项，再读 config.yaml 的 `recording.ignored_apps`。
- **不再提到看板的文案**：通知太多时的汇总通知正文改为「打开 App 查看会议纪要」，权限页「完全磁盘访问」里壳那一行写作「app（壳）」，通用设置里语言开关的说明改为「界面、系统通知与修法句共用这一把开关」，关于页「登录时启动」的推荐理由改为「重启后录制才能自动恢复」（不再说菜单栏助手常驻）。
