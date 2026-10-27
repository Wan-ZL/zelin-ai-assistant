# lite 2.0 计划书

Status: **owner 决定 2026-10-23**（第 9 节的默认取舍同日全部接受；实时字幕 = 删除）。本文件是 lite 2.0
这一轮的决策锚点：owner 的决定、实施方案的默认取舍、切片记录、隔离与发布路径、上线与回滚。

法典：`docs/CONTRACT.md`（§0 修宪段、§78、§79 与各节的 v2.0.0 追记是本轮的规范来源；退役段落只留一行
tombstone `## N. 标题 —（retired v2.0.0，全文见 tag full）`）。行为变化与法条同 PR。

**编号**：本文件的决定用 `L` 编号（L1 起），号码永不复用（`tests/test_doc_numbering_unique.py` 查重号）。
1.x 的 `docs/design/vnext2-plan.md` 与它的 D 编号（D1–D78，§77 引的 D79 亦同）是历史：代码注释与 CONTRACT
里出现的 `D<n>` 一律指 git tag / branch `full` 那棵树上的 vnext2-plan，
`git show full:docs/design/vnext2-plan.md` 可读全文。L 号与 D 号互不复用、互不改写。

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
| L16 | **1.x 归档 = branch `full` + annotated tag `full`，都指向 v1.0.117**；v1.0.117 的 release 标题改为 "last full 1.x"；1.x 与 2.0 不可同机并存，README 写明 | 第 9 节第 15 条默认，owner 接受 | 10-23 |
| L17 | **owner 只 review PR-0（bridge）与 PR-F（dev→main）**；切片 PR 合进 dev 只需 CI 绿 | 第 9 节第 16 条默认，owner 接受 | 10-23 |
| L18 | **PAT 权限先用 REST 核实**，缺 actions / pull_requests 写权限时由 owner 在网页点一次 | 第 9 节第 17 条默认，owner 接受 | 10-23 |
| L19 | **发布资产 = Dock 壳 zip + checksums + 零条目 appcast.xml**，zip 带 attestation；不发 .pkg、不发便携包（§56.2 / §74.3 追记） | 计划默认（PR-E） | 10-24 |
| L20 | **1.x 过渡命令停在 `main` 上**（`git checkout main && git merge --ff-only origin/main && bash install.sh`），不是 `git checkout v2.0.0`：升级器只快进在 `main` 上的 checkout（§79 追记） | PR-E 实施中发现 | 10-24 |
| L21 | **fresh-install 验收真接线**（不带 `--no-launchd`），兼验 §78 封闭足迹：不建 `~/.claude`、恰好一个 agent、恰好一行 cron、安装后无 tracked 改动、卸载逐项删净（§69 追记） | 计划默认（PR-E） | 10-24 |
| L22 | **demo 数据改为虚构会议纪要**（`scripts/demo_seed.py` 写 `state/recap/`），看板卡片场景随 dashboard.json 退役 | 计划默认（PR-E） | 10-24 |

## 2. 一句话方案

在现有目录树上原地删掉 Act 管线（act/ 约 43k 行、server/ 约 5k 行、web/ 约 20k 行、mac/ 与 ios/ 全部、约 380 个
测试），把会议纪要的三条 actd 依赖（列表、生成 / 回退、SSE）搬进 server，把所有 headless `claude -p` 收口到
一套隔离常量，CONTRACT 退役段落只留一行 tombstone 指向归档 tag `full`。所有切片在 `dev` 上暂存、各自过完整
CI，最后一条 dev→main PR 贴 `release: major`、用 merge commit 合并，由现有 release-on-merge 切出 v2.0.0。
前置一个 1.x 补丁（v1.0.117）让残存的 1.x 自动部署拒绝跨大版本边界。

选它的理由（三份候选方案评审的共识）：切完那一刻代码、法条、测试是同一个状态（删功能与 tombstone、修法、
测试重写同一 PR）；隔离靠结构保证（headless 运行的 cwd 在仓库树之外），而不是只信 `--bare` 的语义；常驻
足迹第一天就收敛到封闭清单。

## 3. 切片与顺序

所有 PR 都过 ruleset `protect-main` 的 7 个 required check（名字逐字不变）：`ci`、`Lint (shellcheck + ruff)`、
`Tests on ubuntu (Python 3.9)`、`Tests on ubuntu (Python 3.x)`、`Web tests (build + vitest)`、
`QA gates (complexity + CRAP + coverage floor + deps + hygiene)`、`Version pins untouched`。GitHub 操作用
Wan-ZL 的 PAT 走 REST。各 PR 的当前状态以 GitHub 为准。

| 序 | PR | 目标 | 内容 |
|---|---|---|---|
| S1 | PR-0（#480） | main | 1.x 桥：auto-deploy 在 origin/main 最高 tag 的 major 大于运行中的 major 时拒绝快进；update_check 带 `manual_upgrade`；切出 v1.0.117 |
| S2 | — | — | v1.0.117 出现后至少 30 分钟，建 branch + annotated tag `full`（L16） |
| S3 | — | dev | 备份 dev 到 `archive/dev-self-improve-2026-10`，再把 dev 对齐到 main |
| S3a | PR-A（#481） | dev | Act 引擎删除、会议纪要搬进 server、隔离核心（`act/lib/config.py` / `act/llm.py` 的常量）、§0 修宪 + 大批 tombstone + §78 / §79 |
| S3b | PR-B（#483） | dev | web 与 Dock 壳裁剪到会议纪要 / 录制与导入 / 设置 / 关于；引擎 adopt 语义 |
| S3c | PR-C（#482） | dev | mac/、ios/、Linux / Windows、§66 parity、§77 清单、三个 workflow 删除；版本 pin 清空 |
| S3d | PR-D | dev | 隔离收尾（`ingest/claude-env.sh`）、install.sh 重写（含 1.x 迁移）、uninstall.sh、bootstrap.sh、`scripts/upgrade.sh`、a11y prune 并入 cleanup |
| S3e | PR-E | dev | release.yml 与 fresh-install.yml 重写、readme_audit 修订、README 两语与截图、demo 数据改为会议纪要、本文件完整版、AI review prompt 去 Act 措辞 |
| S4 | — | dev | 本地全门；每一步前后核对录制引擎 PID 不变 |
| S5 | PR-F | main | dev → main，合并前加 label `release: major`；7 项全绿后用 merge commit 合并，**不用 rebase**（release-on-merge 从 merge commit 首行的 `#N` 找 PR 读 label） |
| S6 | — | — | 核对 releases/latest = v2.0.0、资产齐全；本机上线（第 7 节） |
| S7 | PR-G | main | 清理已发 fragment；owner 删 SUPABASE_* secrets；第 6 节的数据归档 |

落后时的放弃顺序（都不阻塞 v2.0.0）：visual goldens → mutation_targets 收缩 → ROADMAP / DEMO 文档 → README
截图。不可放弃：release.yml、ci.yml、install.sh 的 1.x 迁移、CONTRACT 的 tombstone 索引。

## 4. 默认取舍与理由（第 9 节）

- **实时字幕（L2）**：与 Act 无关，但不在「一个小 app」的范围里，且占壳代码约三分之一；代码在 tag `full` 可取回。
- **Slack 草稿（L3）**：隔离目录里没有用户级 Slack MCP，这条路必然失效；不做 app 自带 MCP 的替代。
- **Linux / Windows（L4）**：这两个安装器只装 Act，录制与 ingest 链本来就只在 macOS 上。
- **遥测（L5）**：零上传面；唯一非模型出网 = GitHub releases/latest 的更新检查（设置页可关）。
- **§66 / §77（L6）**：规格来源 mac/Sources 删除后两者没有对象；ledger_diff 同 PR 改。
- **state/ 位置（L7）**：`voice-profile.md` 有外部读者，vault-mirror 是 TCC courier 的目标；本轮只搬隔离目录。
- **server LaunchAgent（L8）**：cron 产出的纪要在 app 关着时也能看；壳的自起回落留给没有 agent 的机器。
- **a11y prune（L9）**：并入 cleanup 的每日一步，回执 `state/screenpipe_a11y_prune.json`，少一个常驻件。
- **稳定 claude 副本（L10）**：app 不跟随 owner 的 CLI 升级节奏；仓库在外置卷时是完全磁盘访问的锚点。
- **卸载（L11）**：复制命令对话框，删掉终端接管链。
- **模型旋钮（L12）**：会议纪要用 `models.pipeline`，fallback 保留；「跟随 Claude Code 全局默认」要读 `~/.claude`，与第 13 条冲突。
- **升级（L13）**：检查 + 手动按钮，自动安装默认关；机制只有 git 快进 + install.sh，没有 .pkg。
- **transcript 归档（L15）**：所有数据进 `~/Projects/data/`；替代方案是等 Claude Code 的 30 天清理自行删除。

## 5. 隔离（宪法第 13 条、§78）

- `CLAUDE_CONFIG_DIR = ~/Library/Application Support/ZelinAIAssistant/claude-config/`；`HEADLESS_CWD = <claude-config>/cwd/`
  （空目录，无 CLAUDE.md、无 `.claude/`）。两处定义：`act/lib/config.py` 与 `ingest/claude-env.sh`，
  `tests/test_isolation_footprint.py` 断言两处字面量相等。
- `ISOLATION_ARGV = --bare --no-session-persistence --setting-sources "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'`；
  会议纪要再叠 `--tools ""`；ingest 再叠 `--add-dir <vault>`、`--append-system-prompt-file <vault>/CLAUDE.md` 与工具白名单。
- 凭证只认 Anthropic API key（`config/secrets/anthropic-api-key.txt`，其次 `~/.config/anthropic-key.txt`）；
  `--bare` 下 Claude Code 登录与钥匙串永不读取。
- 封闭足迹：1 个 LaunchAgent（server）、1 行 crontab（`*/30` ingest 链）、1 个 opt-in login item、1 个录制引擎。
  清单之外的 agent / cron 行 / login item 都算违宪。fresh-install CI 每天在干净的 runner 上核对一遍（L21）。

## 6. 发布与升级

- **发版**：release-on-merge 在 push to main 时铸 tag、dispatch release.yml；`release: major` 贴在 PR-F 上切出
  v2.0.0。release.yml 从 tag 盖章，构建 Dock 壳 zip、checksums、终止版 appcast，正文来自 `changelog.d/`（L19）。
- **2.x 内升级**：关于页「立即升级」→ `scripts/upgrade.sh`：origin/main 恰好是 `v*` tag 时快进 `main`，再跑
  `install.sh --non-interactive --from-upgrade`；树脏或分叉 = 回执 + 手动命令，永不 reset；录制引擎永不被打断。
- **1.x → 2.0**：v1.0.117 的桥把自动部署挡在大版本边界；一次性手动
  `git fetch --tags origin && git checkout main && git merge --ff-only origin/main && bash install.sh`（L20）。
  install.sh 的 `migrate_from_1x` 卸掉 1.x 的 LaunchAgents、剥掉 1.x 的 cron 尾巴、解除 `~/.claude/skills` 里
  指向本仓库的软链，只打印（不执行）1.x headless transcript 的清理命令。

## 7. 上线与数据归档（owner 在本机执行，不是 PR）

上线（S6 之后，避开 `*/30` cron，在 :05–:25 之间）：记下录制引擎 PID → 备份本地分叉
`git branch keep/local-ledger-2026-10-23 main` → 对齐到 v2.0.0 → 先放 API key 到 `config/secrets/` →
`bash install.sh`（预期：迁移步删掉 1.x 残留、装上 server agent、cron 改成一行、日志「engine running: left
untouched」）→ 再核对 PID 不变 → `open -b com.zelin.ai-board`，壳 adopt 现有引擎 → 权限页确认
`com.zelin.ai-board` 的屏幕录制已授权 → 手动生成一次纪要，等下一个 cron 轮，核对 `~/.claude/projects`
无新文件、`claude-config/` 有内容。

归档：所有数据进 `~/Projects/data/`；`store2.db` 用 `sqlite3 … "VACUUM INTO …"` 取快照（有活 WAL，不能裸 cp）；
Act 运行态文件删除前先归档注册表导出；headless transcript（约 7.9 GB）整目录 mv 到
`~/Projects/data/claude-sessions-archive/`（L15）；`~/Projects/data/MANIFEST.json` 记两条。

## 8. 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| 录制被打断 | install.sh 不再 pkill 引擎；壳 adopt 带 recipe 标记的引擎；升级写 `engineHandoff`；每步核对 PID | 按 `shell/Sources/Recording.swift` 的 recipe 手动拉起，或开壳自启 |
| 隔离目录下认证失败 | 上线第一步先放 key；doctor 的 `anthropic key` 行 | 放回正确的 key，无状态损坏 |
| release.yml 首发失败 | 本地以假 tag 干跑每个 shell 步 | 对 v2.0.0 重新 dispatch，或小 PR 发 v2.0.1 |
| README 审计红 | §77.5 的两条放行（Removed 块、`full`）；截图从 demo server 真渲染 | — |
| 安装后树变脏，升级器拒绝 | fresh-install 断言安装后无 tracked 改动（L21） | — |
| coverage floor 83.5 | 切片各自本地先测，补 server / recap 测试 | 最后手段：同 PR 修 ledger_diff + §58 一次性条款 |

整体回滚：代码 `git checkout v1.0.117 && bash install.sh`（1.x 的 install.sh 重装 Act agents，配合
`state/soft-close-2026-10-23/` 的 plist 与 crontab 备份）；数据在 S7 才删、且先归档；`release: major` 发错
可用 v2.0.x 修正，但 v2.0.0 这个号一旦切出不可回收。
