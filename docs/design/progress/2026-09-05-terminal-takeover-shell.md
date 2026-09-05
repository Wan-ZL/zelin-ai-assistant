pr: `ai/self-improve/R-285`（issue #216；无版本 bump，版本由 tag 派生）
phase: P4 余量 / D36（终端接管三件套：壳侧 Apple Events、砍按钮、双击手势）
law: §49 codes（`SHELL_UNAVAILABLE` 503）/ §54.1 第 3 / 11 项追记 / §68.7 追记（含 `.command` 通道 tombstone）/ §68.13 追记

**为什么动**：web 看板的「在终端接管」走 server 写时间戳文件名的 `.command` 再 `open -a`，macOS 26 对每个「脚本文档」都弹一次 "Allow Ghostty to execute …?"——文件名唯一，没有「记住我」可言；原 docstring「不需要任何自动化授权」被现实推翻（owner 拍板 2026-09-04，issue #216）。

**server**（`server/terminal_launch.py` 重写；`maintainer_launch.py` / `uninstall_launch.py` 跟着走）：`POST /api/terminal {card_id}` 端点保留，语义改为入队——校验 → 从投影行推导命令（不变，绝不接受客户端文本）→ `require_shell`（`state/shell.heartbeat` mtime 15 s 内，否则 503 `SHELL_UNAVAILABLE`，不入队）→ `enqueue` 写 `state/terminal_queue/<uuid>.json`（§28 通知中继同款：原子 .json.tmp + rename、写侧清扫 60 s 以外的条目含 .tmp 尸体；写不进去 500）。条目 `{id, kind, command, shell_line, cwd, created_at, card_id?}`；回执保留 `command_file` 键（= 队列条目路径）、加 `queue_id`。三条 `.command` 通道（接管 / 开发会话 / 卸载）共用 `enqueue` + `require_shell`，`write_command_file` / `open_command_file` / `_default_opener` / `resolve_terminal` 等 tombstone；`act/ai_fix.py` 的 .command 用途不在本 PR。`server/paths.py` 加 `terminal_queue_dir` / `shell_heartbeat_path`；`server/errors.py` 加 `ShellUnavailableError`。

**壳**：`shell/Sources/TerminalLauncher.swift` = 退役 `mac/Sources/TerminalLauncher.swift` 的移植（Ghostty new tab in window 1 / Terminal do script / iTerm2 create window；zsh -lc + PATH 兜底；`runner` 注入缝），唯一改动 = 终端偏好读 overrides `terminal_app`（server 仍是写者、壳只读；`resolve(setting:installed:)` 与 server 旧规则同款）。新 `TerminalRelay.swift`：1 s tick `drain()` 扫队列，60 s 以外未启动即删、坏形删并记日志、其余按 created_at 升序先删文件再 launch；`ShellHeartbeat.beat()` 随 5 s 引擎 tick（起跑立即一次），`applicationWillTerminate` 删心跳让 server 立刻转 503。`shell/Info.plist` 加 `NSAppleEventsUsageDescription`。桥词表零改动。

**web**：`TerminalButton` 删除（运行卡 / 待验收卡动作行都不再有它）；`CardSurface` 新 prop `takeoverCmd`——非空时 `onDoubleClick` 走 `useTerminalTakeover`（`terminalTakeover.ts`）：成功「已在终端打开」、被拒「打开终端失败 · 原句」红字、501 / 503 同一条降级 = 复制指令 + 「无法直接打开终端 · 已复制指令，粘贴到终端即可接管」；卡内按钮 / 输入框上的双击归它们自己，指令行例外；Enter 仍开详情；无会话的卡双击 no-op；在途去重。指令行文案 →「单击复制指令 · 双击在终端接管」。

**判例**：Python `test_server_board_tools.py`（入队形状 / 心跳缺席与过期 503 / 写侧清扫 / 写失败 500 / 无 .command helper）+ `test_server_slack_uninstall_maintainer.py`（两通道走队列、503 带手动命令）；Swift `shell/tests/run.sh` 第 6 节（AppleScript 三形 + 引号层、resolve 六例、drain 新鲜 / 过期 / 坏形 / .tmp / 顺序 / 消费、心跳 beat / stop）；web 新 `cardTakeover.test.tsx` 9 条 + `parity.test.tsx` 看板主遍 ①b 双击接管、拒绝遍改双击。§66 清单：`opened-in-terminal` / `terminal-launch-failed` 两句照判（渲染面换成卡尾 status 行），`PREF_OWNER.terminalApp` 理由改述、`native-inventory.json` 重生成。
