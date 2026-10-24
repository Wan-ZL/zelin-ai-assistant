type: changed
- **会议纪要成为默认页**：左侧导航栏只剩四项（会议纪要 / 录制与导入 / 设置 / 关于，⌘1–⌘4）；旧深链 `?page=board|trash|archive|skills|styleguide` 回落会议纪要，`?card=` 不再读。纪要列表直接读 `GET /api/recaps`、随 SSE `recaps.updated` 刷新，「重新生成 / 现在生成」与「回退到这一版」分别走 `POST /api/recaps/generate` 与 `POST /api/recaps/revert`。
- **设置页按 server 目录分区**：通用、通知、录制（含「不录制的应用 / 窗口」）、录制数据与磁盘、笔记库、更新（含「发现新版本时自动升级」）、脱敏，加上显示、模型（只剩管线与回退两把，出厂值不跟随你自己的 Claude Code 设置）、依赖检查、凭证（只有 Anthropic API key）、会议纪要。
- **关于页的升级与卸载**：「立即升级」调用 `POST /api/update/install`；新的大版本（409）或这个安装还没有升级器（501）时给出可复制的手动命令，不再打开 release 页。「卸载…」改为给出一条可复制的 `bash uninstall.sh` 命令，页面不再替你打开 Terminal。
- **首次运行向导缩为六步**，第 2 步改为检测 claude CLI 与 Anthropic API key（不再提供「用 Claude Code 登录」那一路）。
