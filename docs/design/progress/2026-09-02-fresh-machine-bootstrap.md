pr: `feat/fresh-machine-bootstrap`（PR-7；无版本 bump，版本由 tag 派生）
phase: D25 终态验收的机器化（§9.2 一条命令 + §9.3 的机器可读判决；owner：「我能够在另一台电脑上起一个空白环境，或者在其他电脑上更新这个软件，就能够直接使用。」）
law: **§69（新增）** / §23 / §25 / §54.1 第 11 项 / §55 / §56.5

**一条命令装到能用 + CI 机器验收**。`scripts/bootstrap.sh`（`curl … | bash`）：preflight（macOS / Xcode CLT / git / python3，缺 CLT 打出 `xcode-select --install`、永不自己弹对话框）→ checkout 目录（默认 `~/Projects/zelin-ai-assistant`，`$HOME` 之外打 §55 per-binary TCC 警告）→ clone 或 `fetch` + `--ff-only` 更新（本地改动 = 不动代码只装现状；非 git 非空目录 / 别的 repo = 拒绝，一个字节不碰）→ `config.yaml` 缺则从模板复制、存在永不覆盖 → `install.sh --non-interactive` → `doctor --fresh-install` → `open` 看板。同一条命令 = 安装 = 更新（§9.4 的手动兜底）；`curl | bash` 的 stdin 纪律（`main()` 包裹、子进程 `</dev/null`）。

`install.sh --no-launchd`：不接调度器、跑一次 `actd --once`（§23 新 step `actd_once`，fail 进 `failed_deploy_steps`）；flag 可叠加、未知 flag exit 2；`--check` 透传 doctor 参数；真空环境是每种模式的合法输入。`act.doctor --fresh-install`：五桶（wired / human / unwired / broken / notes）+ 带本机路径的 `manual_steps`（看板 → Claude Code → key 文件 → 三条完全磁盘访问路径 → 接线），**exit = broken 数**——TCC 与凭证永不计入，这是 §9.3「doctor 零 FAIL、key 允许 warn」的机器版。看板侧的首启入口 = §68.5 的首次运行向导（#164）——bootstrap 装完 `open` 壳、看板按 `needed` 自动进 `?page=setup`；初版的 SetupPanel 在 rebase 到 dev 时撤下（不造第二套），顺手让权限体检页的 claude 一条指向 §55 第五幕的稳定副本（§54.1 第 11 项改写）。

`.github/workflows/fresh-install.yml`「Fresh install (macOS)」：干净 runner、空 `$HOME`、本地 bare origin，重放 `bootstrap.sh --no-launchd` 并断言 §23 报告 / server 三个端点 / doctor exit 0 / 第二次运行 = update 且 config 字节不变——§9.3 第 1、3、4 条中不依赖登录 launchd 会话与 TCC 的部分自此每个 PR 都跑一遍（出生 informational，绿稳后升 required）；launchd 真接线、自动部署（§9.3 第 6 条）与一张卡走通（第 5 条）仍需真机。INSTALL.md 围绕一条命令重写，README 两语 quickstart 改为 curl 一行。本地实测：有工具链 15 s、`env -i` 系统 PATH 裸环境 12 s，两者 exit 0。
