# lite 2.0 计划书（stub）

Status: **owner 决定 2026-10-23**。本文件是 lite 2.0 这一轮的决策锚点；完整版（切片记录、默认取舍的
理由、迁移步骤）由 PR-E 补齐。1.x 的 `docs/design/vnext2-plan.md` 与它的 D 编号（D1–D78）是历史，
全文在 git tag / branch `full`；本文件的新决定用 `L` 编号，与旧 D 号互不复用
（tests/test_doc_numbering_unique.py 查重号）。

法典：`docs/CONTRACT.md`（§0 修宪段、§78、§79 是本轮的规范来源）。行为变化与法条同 PR。

## 1. 决策台账

| # | 决定 | 来源 | 日期 |
|---|---|---|---|
| L1 | **退役 Act 管线，只留录制 / ingest / 会议纪要**；原地减法，切片在 `dev` 上暂存、各自过完整 CI，最后一条 dev→main PR 贴 `release: major` 切出 v2.0.0 | owner 拍板 | 10-23 |
| L2 | **实时字幕删除**（LiveCaptions / CaptionCore / CaptionOverlay、web 字幕控件、volcano 凭证） | owner 拍板（计划第 9 节第 1 条） | 10-23 |
| L3 | **recap → Slack 草稿删除**（§63.4） | 第 9 节第 2 条默认，owner 接受 | 10-23 |
| L4 | **Linux / Windows 删除，lite 只支持 macOS**（install-linux.sh、install.ps1、systemd / 计划任务模板） | 第 9 节第 3 条默认，owner 接受 | 10-23 |
| L5 | **遥测全删**（上传、同意、insights / keepalive workflow、Supabase key） | 第 9 节第 4 条默认，owner 接受 | 10-23 |
| L6 | **§66 UI parity 与 §77 全量覆盖清单退役**（规格来源 mac/Sources 删除后两者没有对象） | 第 9 节第 5 条默认，owner 接受 | 10-23 |
| L7 | **state/ 留在 `<repo>/state`**；本轮只搬 claude-config 与 headless cwd | 第 9 节第 6 条默认，owner 接受 | 10-23 |
| L8 | **看板 server 保留一个 LaunchAgent `com.zelin.aiassistant.server`** | 第 9 节第 7 条默认，owner 接受 | 10-23 |
| L9 | **a11y prune 并入 ingest/screenpipe-cleanup.sh 的每日一步**，退役独立 LaunchAgent | 第 9 节第 8 条默认，owner 接受 | 10-23 |
| L10 | **保留 App Support 里的稳定 claude 副本** | 第 9 节第 9 条默认，owner 接受 | 10-23 |
| L11 | **卸载按钮改为复制命令对话框** | 第 9 节第 10 条默认，owner 接受 | 10-23 |
| L12 | **保留 models.pipeline / fallback 两个旋钮与 doctor 活性行**；删「跟随 Claude Code 全局默认」 | 第 9 节第 11 条默认，owner 接受 | 10-23 |
| L13 | **升级 = 检查 + 手动「Upgrade now」**，`updates.auto_install` 默认 false；git ff-only 到带 tag 的 origin/main + install.sh；不发 .pkg（§79） | 第 9 节第 12 条默认，owner 接受 | 10-23 |
| L14 | **repo 内 `.claude/`**：删 skills/board-agent 与 worktrees 残留；开发侧 skill 由 owner 决定去留 | 第 9 节第 13 条默认，owner 接受 | 10-23 |
| L15 | **headless transcript 归档到 `~/Projects/data/claude-sessions-archive`** | 第 9 节第 14 条默认，owner 接受 | 10-23 |
| L16 | **1.x 归档 = branch `full` + annotated tag `full`，都指向 v1.0.117** | 第 9 节第 15 条默认，owner 接受 | 10-23 |
| L17 | **owner 只 review PR-0（bridge）与 PR-F（dev→main）**；切片 PR 合进 dev 只需 CI 绿 | 第 9 节第 16 条默认，owner 接受 | 10-23 |
| L18 | **PAT 权限先用 REST 核实**，缺 actions / pull_requests 写权限时由 owner 在网页点一次 | 第 9 节第 17 条默认，owner 接受 | 10-23 |
