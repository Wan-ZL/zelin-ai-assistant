# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Bumping the version

`__version__` in [`act/__init__.py`](act/__init__.py) is the **single source of
truth** for the project version. `mac/build.sh` stamps it into the app bundle's
`Info.plist` at build time, and `mac/package.sh` reads it for the `.pkg` — no
other file needs editing. To cut a release:

0. Pick the bump: **patch** for bug fixes / small UX corrections / docs,
   **minor** for new user-visible features (pre-1.0, breaking changes also
   ride a minor with a `!` commit marker and a prominent changelog callout).
   See CONTRIBUTING.md "Versioning".
1. Bump `__version__` in `act/__init__.py`.
2. Rename the `[Unreleased]` section below to `[X.Y.Z] - YYYY-MM-DD` and add a
   fresh empty `[Unreleased]` heading above it; update the compare links at the
   bottom.
3. Commit and merge the PR. **Nobody tags by hand anymore** (CONTRACT §56):
   `tag-on-merge.yml` reads `act/__init__.py` on every push to `main`, creates
   `vX.Y.Z` when it does not exist yet and dispatches the release workflow.
   Every PR therefore bumps the patch version — a merge that does not bump is
   silently not released. A hand-pushed `vX.Y.Z` tag still works as before.

## [Unreleased]

## [0.48.8] - 2026-09-01

v-next-2 P1（决议 D2）：「切换时机：等 QA 网配好之后，现在就可以切。备份与回滚：在电脑上留个备份，如果切换失败，手动导回去。」卡片账本的真源从 YAML 文件切到 SQLite（store2），同版落地 owner 拍板的 #119（需输入状态退役）。

### Changed
- **store2 接线为真源（CONTRACT §53 从「休眠地基」改写；§0 宪法第 1 条显式精确化；§1/§44 同步修法）**：actd 首个 pass 自动执行激活协议——整目录备份 `act/registry/`（含 archive/，sha256 manifest，`state/backups/registry-<ts>/`，永不覆盖）→ 从备份迁移进 `state/store2.db` → 导出 `state/registry-export/` → 与备份**逐字段比对**→ 零差异且迁移窗口无并发 YAML 写才写真源标记 `state/store2_truth.json`；任何差异 = 删库拒绝 + `state/store2_activation.json` 台账 + doctor FAIL，YAML 照旧是真源（没有半态）。`act/lib/registry.py` 公开 API 两后端逐字一致（callers 零改动、永不见 SQL；判例 `tests/test_registry_backend_parity.py`）；激活后每本地日导出一份可 git-diff 的 YAML 镜像（prune 常开，目录大小 = 活卡数）。多进程写者（雷达/digest/capture）经同一门面走 BEGIN IMMEDIATE 事务（跨进程并发判例 `tests/integration/test_store2_concurrent_writers.py`）；「agent 不得批准/验收」的权限墙自此实际生效（DB trigger + 门面 Python 墙双层，actd 级判例）。doctor 新增 `store2` 行；回滚开关 `registry.backend: yaml` 保留一个版本，手动回滚步骤见 docs/TROUBLESHOOTING.md「store2 回滚」。白名单接线补行 5 条（§51 system 免批通道等）+ origin_trust 触发器改「只禁升档」（§53.2）。
- **需输入状态退役（#119，CONTRACT §2/§5/§6/§13 语义、§39 tombstone、§46.3 修订）**：不再检测 session 是否需要输入——受阻/空闲/放弃救活的 running 会话由 reconcile 按既有 stop_to_review 收割路径直接落**待验收**（交付摘要保留会话最后的提问原文；review 行带 add-only `interrupted: true`，通知 `msg_review_interrupted`）；「回答」由既有「打回 + 修改方向」覆盖。`needs_input[]` 键 add-only 保留，唯一住户 = §4 派发刹车行。resume 风暴 / 5 连败放弃改为「降级即收割进待验收」，升级前滞留 executing 的历史降级卡首个 pass 自动迁出。

### Removed
- `answer_input` inbox 动作 + `executor.answer` + `extract_question` + `msg_needs_input`/`msg_answer_*` 通知 + web「回答…」按钮 + 两份 golden 样张（§39 tombstone；server/webui 对该动作按未知动作 400，actd 对迟到文件幂等 ack）。§39.2 的安全窗口 doctrine（stop-idle-then-resume、roster 探测、「owner 打的字绝不静默蒸发」）由 steer/briefing 继续执行，不在墓碑内。

## [0.48.7] - 2026-09-01

v-next-2 P0 收尾(决议 D9):「自动派工作,要不先不要搞预算。把现有的手打卡自动派工每天 5 块钱的预算也取消吧。目前还没有遇到预算的问题,钱是足够的。」

### Removed
- **自动派发的预算天花板整套退役**(owner decision D9,`docs/design/vnext2-plan.md`;CONTRACT §51 留 tombstone)。删掉的机器:`autodispatch.daily_budget_usd`(默认 $5,兼单卡估价上限)、`may_auto_dispatch` 的 `today_spend` 参数、`state/autodispatch_spend.json` 当日花费台账(actd 写 + dashboard 只读小读器)、派发时刻对 auto 卡的预算复核、`queued_reason` 的 `budget`/`waiting_budget` 与 web 端「等预算」chip、`cost:over_ceiling` / `budget:unknown` / `budget:exhausted` 三个原因 token。现在 hand 出身且有估价的卡不管金额多少、当天累计多少,一律免批派发;并发上限是唯一的排队原因。旧 config 里残留的 `daily_budget_usd` 键被忽略;旧卡上残留的退役 token 在升级后第一个 pass 按「解除即清」清掉并放行;磁盘上的旧台账文件无人读写。**保留**:卡上的成本估价照常展示(披露),`require_text_confirm_above_usd` 文字确认线照常拦 T2(审批语义)——那两条不是预算;`cost:unknown`(无估价保守回人批)也保留,理由改为「不可证明 ≤ 文字确认线」。

## [0.48.6] - 2026-09-01

合并即上岗（owner decision D17，CONTRACT §56）：合进 `main` 的 PR 自动打 tag、自动发版、自动部署到 owner 的 Mac；doctor 出现**新增**红项就自动回滚到上一个提交。人只做两件事——点合并、收通知。

### Added
- **tag-on-merge**：`.github/workflows/tag-on-merge.yml` 在每次 push to `main` 时读 `act/__init__.py`，`v<version>` 不存在就在该提交上建 tag 并显式 dispatch `release.yml`（GITHUB_TOKEN 建的 tag 不会触发 `on: push: tags`，所以 `release.yml` 新增 `workflow_dispatch` 入口；版本闸门两个入口都生效）。tag 已在 = 静默跳过；ruleset 只管分支，tag 不受影响。
- **自动部署 agent** `com.zelin.aiassistant.autodeploy`（每 10 分钟；`python3 -m act.auto_deploy` → `scripts/auto-deploy.sh`）：HEAD 在 `main` 且树干净时：**先问 GitHub check-runs API「origin/main 这个 sha 的 `ci` 绿了吗」**（ruleset 是 non-strict，绿的是 PR head 不是合出来的 merge commit；main 上的 `ci` 要 ~8 min——没结束 = `ci_pending` 下轮再问，红 = `ci_failed` 记账 + 通知一次，`--force` 跳过），绿了才 `git merge --ff-only origin/main` → **自检**（`bash -n scripts/auto-deploy.sh` + `import act.auto_deploy`，弄坏部署 agent 自己的合并会静默终结所有后续部署）→ doctor 基线 → `install.sh --non-interactive`（守护进程 / cron / config，不碰 Mac app）→ **等 `state/actd.heartbeat` 由新进程写下新版本 + `phase=idle`**（新代码上完整跑完一个 pass；180 s 内没等到 = `actd:no_heartbeat_from_new_version` 回滚——原来的「静置 30 s + 一次 `launchctl list` 采样」是抛硬币：import 即死的 KeepAlive actd 每个节流周期亮 ~0.5 s 的 pid，旧 daemon 的 heartbeat/dashboard 文件 90 s 内又都算新鲜）→ doctor 复查；install 失败、相对部署前基线**新增** FAIL、或 doctor 自身跑不出 JSON（`doctor:unparseable`——在任一次运行出现都致命，基线阶段就回滚且不装；两次都用新代码，当成 pre-existing 会让闸门对「弄坏 doctor 的提交」失明）→ `git reset --hard` 回旧 sha + 重装 + 通知「auto-deploy rolled back to …」，该 sha 记账后不再重试（`--force` 才重试）。doctor 里常驻（KeepAlive）agent 的 crash-loop——actd **和 syncd**——都是 FAIL（原来只有 actd；live 上 `mode=cloud`，syncd 死 = 手机/web 看板死却记 `deployed`），周期性 agent 一次非 0 退出仍是 WARN。回滚 `reset --hard` 前**重验**：owner 在部署这几分钟里改了 tracked 文件或切了分支 → 回滚被拒（`rollback_failed` + 通知，改动保留、新版本留在原地），install.sh 自己的 `+x` 翻转不算改动。脏树只通知一次、不动 HEAD；不可 ff 的分叉本地 main 永不被 reset。刚 mkdir 还没写 pid 的锁目录视为活锁，不会被并发的第二个实例当陈旧锁回收。锁 + 1 MB 自截日志（`~/Library/Logs/zelin-ai-assistant/auto-deploy.log`）。只装在 git checkout 上（.pkg 副本没有 `.git`），`features.auto_deploy: false` 可关。
- **`install.sh --non-interactive`**（§23 第三个 mode）：永不提问——缺 claude 只警告、doctor 留给调用方；退出码 = 失败 step 数（旧 Mac app 的 `app` 步骤除外，D3 已冻结它）。**该模式永不构建/安装 Mac app**（step 4 记 `app=skipped`）：`mac/build.sh --install` 会 quit + relaunch 正在跑的 app，screenpipe 是它的直接子进程、实时字幕住在它里面——无人值守的重建等于在合并后 10 分钟内的任意时刻掐断录制或会议字幕，launchd 里的 `swift build` + `codesign` 还会卡在没人点的 keychain 提示上。mac/ 的改动因此只随 owner 手动 `bash install.sh` 上机（§56.5）。
- **`state/deploy_state.json`** → dashboard add-only 顶层键 `deploy_state`、doctor 新行 `auto-deploy`、web 顶栏小字「v0.48.6 · deployed 12m ago」（非 healthy 状态切警告色并点名）。syncd 的变更闸门把整键 `deploy_state` 列为易变键（§31）——agent 每 10 分钟重写 `last_run`，不剔除就是 v0.48 刚修掉的「零活动也每 10 分钟推一次全量快照」风暴回归。

### Fixed
- `ingest/vault-sync.sh` 在 git 里的可执行位（install.sh 每次 `chmod +x` 都把 live checkout 弄脏，自动部署的脏树检查会永远拒绝）。

## [0.48.5] - 2026-09-01

v-next-2 第一批（决议 D19）：两条 digest 通道出厂零卡片。Owner：「像这种每日摘要，好像在设置里面没法关，几天没看就攒起来了……能不能在设置里面让我能够改成一周或者两天摘要，或者完全关掉」；追问「摘要卡还需要吗」的采纳答案是**默认不以卡片形式出现**。

### Changed
- **状态摘要（原「周一 digest」）新增节奏旋钮 `digest.frequency`**：`off | daily | every2days | weekly`，**默认 `off`**；设置层扁平键 `digest_frequency` 同步进 overrides 允许列表——但本版**没有 UI 暴露它**（原生 Mac 设置页不再加功能，web 设置页要到 v-next-2 P4）；在此之前改 `config.yaml` 的 `digest.frequency` 或手写 `state/settings_overrides.json`。crontab 行改为每天 09:07 唤醒且不再带 `--now`，模块按滚动间隔（距上次生成 ≥1/2/7 天，标记 `state/digest.json`）自行闸门——不钉周一，周一睡着的机器周二照样拿到本周那张；off / 未到期的定时 pass 完全静默（不打印、不打点），默认 off 不会在日志或 analytics 里留一行一天。`--now` 仍可手动立即生成。重跑 `bash install.sh` 会把旧的「周一 `--now`」cron 行替换掉（旧行会越过 off 继续每周强制铸卡）；doctor 「cron digest」行看见 crontab 里还带 `--now` 的 digest 行即 **WARN** 指向 `bash install.sh`，不再把它报成「已安装、按节奏」。`state/digest.json` 标记写失败时卡已发布、只打印一行（不 traceback、不静默）——标记缺失会让 `weekly` 退化成一天一张，这一行是唯一让人看见的地方。多年出厂却从未被读取的 `digest.weekly: monday` 模板键随之移除。（CONTRACT §16/§17）
- **文案去周几**：卡片标题「周一 digest · <日期>」→「状态摘要 · <日期>」（en "Status digest"），通知与正文首行同步——日频卡片带「周一」会撒谎。（§40.7）
- **每周摘要（weekly digest）默认关**：`sources.weekly_digest.enabled` 出厂 `false`，显式写 true 才生成回顾卡；launchd 每小时的定时唤醒遇关闭态与「未到期」同款静默，不再每天 24 条 skip 事件。设置页「现在生成一份」按钮遇关闭态仍在日志与 analytics 留回音（无通知，v0.14 判例 `test_disabled_flag_no_ops` 不变）。标记 `state/weekly_digest.json` 写失败同 digest：卡已落、一行日志、不 traceback。（§24）

### Removed
- **weekly digest 的「自动化建议」提案卡退役**（墓碑）：15 张从未获批一张、3 个 cluster 跨 4 周重铸。管道代码（`MAX_SUGGESTIONS`、`_file_suggestion_cards`、parser 分支、prompt 的 `suggestions` 字段）**同版删除**（防腐 #6），prompt 只要 `{"digest": ...}`，模型若仍自带 `suggestions` 键一律忽略；通知不再宣称「另有 N 条自动化建议」。summary / `weekly_digest_generated` 事件里的 `suggestions`（恒 0）、`suggestion_ids`（恒 []）作 add-only 常量保留。反悔 = `git revert`；这类想法的新出口是 v-next-2 的每日自我改进循环（P5）。（§24）

> 升级提示：cron 行形态变了——**重跑 `bash install.sh`** 才会把旧的周一行替换成新的每日自闸门行。不重跑的话旧行仍会每周一强制生成一张（等价于 `weekly` 且忽略 `off`）。

## [0.48.4] - 2026-09-01

2026-08-31 live 审计挖出的三条静默失效(#89):launchd 起的 claude 读不到外置卷上的任务目录、每次派发都死却无限重试(claude 自己把 EPERM 猜成「low max file descriptors」,首版跟着猜错,09-01 审查证伪后修订)、actd 进程活着但循环卡死 2.5 小时无人知、v0.21 退役的 agent 又跑了 51 天。三条都各自加了「让它被看见 + 让它停下」的机制;第一条的修法在 owner 手里(TCC 开关),本版只保证它被诚实地看见、分类、指路。

### Fixed
- **launchd 起的 claude 对任务目录 TCC-blind**(CONTRACT §55 第三幕、§25 `claude_blind`):派发失败原文 `An unknown error occurred, possibly due to low max file descriptors (Unexpected)` 是 Bun 对未映射 errno 的统一猜测,真因是 EPERM——macOS 按可执行文件路径授「完全磁盘访问」,launchd 会话里的 claude(`~/.local/share/claude/versions/<v>`,每次更新换路径)没有授权,任务 repo 又在外置卷上。一次性 launchd job 实测:同 binary 同上限,cwd=$HOME 好、cwd 在外置卷死,同 job 里 homebrew node 直接报 EPERM;TCC 台账里 claude 2.1.251 的 denied 行落款正是首次失败那一分钟。`failures` 目录新增 `claude_blind`(句子写明两条出路:给 claude 当前版本开完全磁盘访问——每次更新后重做;或把 repo 放回启动盘家目录),doctor 新行 **`launchd claude`**——在一次性 launchd job 里以默认工作 repo 为 cwd 跑 `claude --version`,终端里看不见的失败只能这样问出来(FAIL `claude_blind`;探针 `AIASSISTANT_LAUNCHD_PROBE=0` 可关)。**本版不声称该事故已修复**:live 机器上 doctor 此行仍 FAIL,修法需 owner 亲手点 TCC;验收 = 该行 OK 且一张重批的卡真到 executing。结构性根治(有授权的 GUI app 托管 actd)记入 vnext2-plan 待拍板。
- **资源上限改为只抬 soft**:launchd 默认 soft 256 / hard unlimited。首版给全部模板加了 `SoftResourceLimits` + `HardResourceLimits` 8192——实测 hard 键把 unlimited 压成 8192,只降不升;现在模板只带 `SoftResourceLimits.NumberOfFiles = 8192`(余量,不是任何已知事故的修法),systemd 单元镜像 `LimitNOFILE=8192:524288`(soft:hard,裸 8192 会把两把都设成 8192)。`fd_limit` 只留给真句柄耗尽(EMFILE/ENFILE/`FdQuotaExceeded`),句子不再提派发失败;doctor `launchd fd limit` 行:soft 缺失/过低 WARN,**出现 hard 键也 WARN**(hotfix 形状)。**升级需重跑 `bash install.sh`**去掉 hotfix 的 hard 键。
- **`failures` 目录补 `claude_bypass_disclaimer`**(#89 的原始报告:`--bg` 在本机接受过一次「跳过权限确认」免责声明之前拒启,新装机几乎必撞;此前落成 `dispatch_error_id=null`)。doctor 的装机预检暂缺——claude 没有文档化的接受标记可读。
- **派发风暴刹车**(CONTRACT §4.1):同一失败类别连续失败 N 次(`execution.dispatch_max_failures`,默认 5,0 = 关)后卡停止自动重试——`execution.dispatch_halted`、卡上一行 `[dispatch-halted]` 记录、一条通知,投影进「需输入」列(不再在运行中列顶着「排队中」装忙);web 隐藏「回答…」只留「停止」。重新上膛 = **进入 approved 的每条路径**(owner 批准、hand 卡免批通道)都清掉整条失败台账,退回提案本身也清——审查复现的死循环(刹车 → 退回提案 → 免批带着刹车再进 approved → 永远停在「需输入」,再点批准是 no-op)已钉判例。退避窗口内 actd 现在**零写盘零 traceback**——此前每个 pass 都重写一次 `last_error_at` + 28 行 traceback。
- **actd 心跳看门狗**(CONTRACT §47.4):actd 在每个 pass 的每个阶段边界 touch `state/actd.heartbeat`(mtime 为真源,body 带 phase/pid/interval 与写者自定的 `stale_after_s = max(3 × interval, 90)`)。doctor 新行 `actd heartbeat`:进程活着 + 心跳过期 → FAIL `actd_stalled`,修法是 `launchctl kickstart -k`(kill+respawn,不是 reload)。server 新路由 `GET /api/health`(token-light,只 stat 三个文件),web 看板顶部新增管线健康横幅(卡住 / 连崩 / 没跑)——退役中的 Mac app 横幅的替身(parity 1.11)。
- **退役 launchd label 的卸载必须自证**:`install.sh` 的 RETIRED 步骤此前把 `launchctl bootout` 失败吞进 /dev/null,v0.21 删掉的 `imessageradar` agent 因此又跑了 51 天(23,613 条 traceback、14.5 MB 日志)。现在卸载后再问一次 `launchctl list`,还在就 `[ERR ]` + 给出命令,安装报告落 `launchd_retired=fail`;另扫描带我们前缀却已无模板的孤儿 label(只报告不动手,`launchd_orphans=warn`),doctor 新行 `launchd orphans`(已装载的孤儿 FAIL)。用户日志不删。

### Changed
- `docs/design/vnext2-plan.md`:决策台账追加 D17(自动部署方案 A)、D18(他人 issue 只摘要)、D19(digest 卡默认 OFF + 频率旋钮)、D20(本次事故与刹车);§5 填入审计的 issue 处置表与 8 条日志教训;新增 §8 进度日志。

## [0.48.3] - 2026-09-01

真机部署 v0.48.2 时挖到的最后一层:plist 渲染全对、解释器也有 PyYAML,守护**仍然**起不来 —— 因为 macOS 的文件访问授权是**按二进制**发的。

### Fixed
- **解释器选择新增「launchd 可用性」闸门**:此前只验 `import yaml`,于是选中了一个交互式 shell 下一切正常、但在 launchd 会话里读不到外置卷仓库的 python(`import act` 失败)。现在装机时起一个**一次性 launchd agent** 实探 `import act` 是否成功 —— 这是唯一有判别力的探测:直接 spawn 与 `launchctl asuser` 都会继承终端的授权而给出假阴性(两者均已实测)。对照实验确认机制:同一个 python 在 `$HOME` 内的目录下正常,在外置卷上 `os.listdir` 抛 `PermissionError: Operation not permitted`,被 import 机制报成模块缺失。探测亚秒级、自清理,`AIASSISTANT_LAUNCHD_PROBE=0` 可关;结果分 通过/拒绝/**无法判定**三态,无法判定一律不作为拒绝依据。
- **候选顺序按 TCC 敏感度分支**:仓库在 `$HOME` **之外**时 `/usr/bin/python3`(Apple 自带、随用户授权)优先于 homebrew/miniconda(各自需要单独授权);在 `$HOME` 之内维持原顺序。内外判定用**物理路径**比较,符号链接无法伪装成「在 $HOME 内」。全部候选都被 launchd 闸门拒绝时,回退到第一个 yaml 可用者并**显式说明原因**,绝不静默钉一个瞎的。
- **错误归因不再张冠李戴**:`install.sh` / `doctor` / `ai_fix` 现在读 agent 自己的日志来判因 —— `No module named 'act'` = 解释器看不见仓库(TCC/路径),**与 PyYAML 无关**;`No module named 'yaml'` 才给 pip 命令;日志读不到则两因并列。旧代码把任何非零退出一律归咎 PyYAML,正是这次排查绕远路的原因。doctor 新增 `interpreter_blind` 判据(路径全对 + yaml 可用 + agent 当前确在崩溃 + 日志指向 `act`),修复指向「重跑安装器 / 给该解释器授予完全磁盘访问」,**不是**「重载 agent」(那只会用同一个瞎解释器再渲染一遍)。
- Swift 侧两个渲染器同步(`RuntimePython.resolveForLaunchd`,双闸门 + 同款顺序);app 内通用的 `resolve()` 保持 yaml-only —— 全 app 都在调它,不能让它去起 launchd 任务。

> 升级提示:同 v0.48.2 —— 修复需**重跑 `bash install.sh`** 才落到本机(它会同时改写 `config/runtime.json` 的解释器 pin 与全部 agent)。

真机部署 v0.48.1 时抓到的安装器缺陷:仓库在外置卷 + `~/Projects` 是符号链接时,后台服务装完起不来。

### Fixed
- **launchd 渲染改用物理路径**:`install.sh` 的 `SCRIPT_DIR` 走 bash 的**逻辑** pwd,会把进入时的符号链接原样烙进 `PYTHONPATH` / `AIASSISTANT_HOME` / app 的 home.txt 指针 / cron 行;launchd 会话通过该路径形状被 TCC 拒绝,守护以 `ModuleNotFoundError: No module named 'act'` 反复退出(本机实测)。三个渲染器(install.sh、Doctor.swift、SetupWizard.swift)现在一律先解析物理路径再替换,install.sh 额外在渲染函数内二次解析,`install-linux.sh` 同步(systemd 无 TCC 门,但符号链接从不更正确)。
- **解释器选择加 PyYAML 校验**:此前 `$AIASSISTANT_PYTHON` 与最终兜底分支只检查可执行位,可能钉上一个装不了 PyYAML 的 python。现在统一走 pin → miniconda → 安装器所用 python → `/usr/bin/python3` 的候选链,取第一个能 `import yaml` 的(绝对路径,永不 `env`);全部候选失败即响亮报错并在安装报告里记 `runtime_python=fail`,绝不静默钉坏解释器。同一条链同时供给 runtime.json、plist、cron 与 `--check`。
- **doctor 新增两个迁移探针**:已安装 plist 携带符号链接形状的仓库路径(仓库在 $HOME 外为 FAIL、之内为 WARN),以及解释器无法 import yaml(恒为 FAIL);两者都指向 `bash install.sh`。探针按解释器去重,读的是已安装 plist 而非配置里的 pin。CONTRACT §55 补齐物理路径与解释器校验两条,docs/TROUBLESHOOTING.md 补该症状的排查段。

> 升级提示:修复只在**重跑 `bash install.sh`** 后落到本机——app 的「一键修复」只重渲染 actd,cron 行与其余 agent 需要完整安装流程。

## [0.48.1] - 2026-08-31

v0.48 上线当晚的加固批。三条线（安全 / app 体验 / 结构健康）各自走完「建造 → 独立审核 → 修复 → 回归」闭环，累计修 1 CRITICAL + 8 MAJOR + 20 MINOR；测试 2243 → 2309（Python）、137 → 143（web）。

### Security
- **本地看板服务补齐四道门（CRITICAL）**：`server/app.py` 此前无 Origin / Host / token / Content-Type 任何一道，跨源页面可借浏览器直接投递 `mode:"run"` 并绕过全部审批闸门在本机执行。现与 `act/webui.py` 同级——Host loopback 白名单（防 DNS rebinding）、POST Origin 白名单、强制 `Content-Type: application/json`（封死 simple-request 向量）、每装机 token（`state/server.token`：CSPRNG、0600、O_NOFOLLOW，仅注入同源 index.html，写操作必带）。GET/SSE 保持 token-light；交付物响应永不注入 token，agent 产出的 HTML 拿不到凭据。约 90 次真实攻击探测覆盖 null/伪造 Origin、sendBeacon/form/no-cors、DNS rebinding、token 越权与泄漏路径（CONTRACT §49）。
- token 文件加固：预存文件权限过宽时重新收紧、无法收紧则轮换；符号链接不再被跟随覆盖。SSE 响应补齐安全响应头。
- 外部来源提级（W17）现按**并集**判定：显式 `origin_trust: external` 章 **或** sources 现算为 external 都强制 T2 + 展开计划，堵住手改/缺章 YAML 裸批的洞；Mac 与 iOS 客户端也开始读 `effective_tier`（此前只有 web 读，gmail 来源卡在 Mac 上仍是一键批准），三端统一显示「外部来源 → T2」徽章。

### Changed
- 菜单栏 popover 面板移除（owner 拍板）：点菜单栏图标直接打开/聚焦看板主窗口；右键菜单新增「录制」子菜单（三态模式 + 实时字幕开关）。快速捕获入口收敛到看板列顶输入框（⌘L）与图标拖放，capture 契约不变。
- 关闭主窗口后 app 保持常驻（菜单栏 + Dock，Slack 式）——不再退回菜单栏-only、Dock 图标不再消失；点 Dock 图标或菜单栏图标随时重开看板。退出语义不变（菜单退出 / 系统注销照旧）。
- launchd 后台服务日志迁至 `~/Library/Logs/zelin-ai-assistant/`，plist 模板 WorkingDirectory=$HOME + PYTHONPATH=repo：修复 repo 在外置卷时 launchd 以 EX_CONFIG(78) 拒绝 spawn、且「一键修复」把手工修好的 plist 打回故障态的问题（CONTRACT §55）。doctor 新增 stale plist 探测，逐个点名 pre-v0.48 渲染残留并指向 `bash install.sh`；两个 Swift 渲染器补齐 install.sh 的第五处替换（claude bin 目录），不再渲染出指向空目录的 PATH。

### Fixed
- 实时字幕悬浮窗不再随转写累积无限变高：显示层滚动窗只保留最近约 2 句（CaptionRollup），悬浮窗 300 pt 硬上限、每行至多两行，宽度行为不变；翻译改跟随可见尾巴，不再为不可见文本消耗额度。
- **digest / weekly-digest 渠道未登记在信任词表内**，被 fail-closed 误判为 external：周报类自提案卡被错误提级 T2、无 plan/DoD 的还会把「批准」静默转成「研究中」。两个渠道补登为 proposed，并在词表注明「新增铸卡渠道必须同步登记」。
- 测试套件不再偷偷调用付费模型：新增 subprocess 守卫,首次运行即抓出 `test_radar_gmail` / `test_radar_triage` 经 §44.2 fold judge 落到真实 `claude -p` 的路径（异常被吞导致长期无感知），改走既有 `JUDGE_RUNNER` 注入缝。
- 结构健康 SAFE-NOW 批：store2 迁移工具不再手抄 registry 字段词表（改为单源 import + 覆盖率断言，杜绝「新增字段在迁移中静默消失」）、qlty ignore 作用域修复（安全告警 23 → 5、zizmor 全仓归零）、CI 模板注入加固与 action SHA 固定 + dependabot 续期、若干重复逻辑收敛与陈旧文档指针清理。

## [0.48.0] - 2026-08-31

v0.48 v-next 移植列车：把在 v0.10.3 公开导出基线上开发的 v-next 线（web 看板、store2、信任矩阵）整体移植回真 main（v0.47.0）——语义级重放而非补丁级套用，逐条判决记录在 docs/design/transplant-notes.md；配套修宪案与旧法→新法测试映射见 docs/design/vnext-amendments.md（W1/W17/W18/T-28/T-29/§44.3-S/§50/§51）。

### Added

- **Web 看板（localhost SPA）** — Vite + React + TypeScript：提案/运行中/评审/完成泳道、筛选、backlog 条、回收站页、卡片深链（id 大小写保持）；详情抽屉带交付物查看器（markdown/mermaid 渲染）、fold notes 与 steer-aware 评论输入框；v-next 投影可视化——steer 回执按诚实投递状态渲染、结构化 queued-reason chips、origin-trust / effective-tier（W17）徽章、T2 文本确认对话框；未确认提交 180s truth-timeout 后浮出；SSE 实时 + 断线重连；活样式指南页（`/?page=styleguide`）渲染真组件与 tokens，light/dark 主题继承 Mac app 配色（tonal ladders + state layering）；i18n（en/zh）带语言切换。137 条 vitest。部分改编自 dashi-taskboard（Apache-2.0，见 NOTICE）。
- **localhost stdlib HTTP/SSE 服务器**（`server/`，§44）— `python3 -m server`（ZAI_PORT / AIASSISTANT_HOME 环境驱动），纯 stdlib。读侧：`/api/board`、`/api/cards/{id}` 原样投影 dashboard.json（v-next 字段透传不动）、`/api/events` SSE、`/files/deliverables/*` 带安全头 + symlink-safe 路径解析。写侧：`/api/actions` 只写 `state/inbox/*.json`——actd 仍是 registry 单写者（§44）；每条记录盖 `via`（web/agent，T-28 ingress 标记），executing 卡上的评论只在响应里标 steer/steer_status（inbox 文件保持 §3 四键评论形态）。与 Mac app 写入端的 inbox 同形由字节级 golden fixtures 判例钉死。
- **Zelin AI Board.app 薄壳**（`shell/`）— swiftc 手装 bundle，镜像 mac/build.sh 惯例（plutil lint、版本从 act/__init__.py 盖章、ad-hoc codesign）；负责拉起/监控 localhost 服务器并用 WKWebView 渲染 web 看板，服务器死活如实呈现。
- **信任矩阵 + 自动派发**（§50/§51、W17，`act/lib/policy.py` / `act/lib/risk.py`）— 卡片出身分类落 `origin_trust`（hand/external，add-only 字段，每个改 sources 的出口盖 min-trust；LLM 输出伪造不了 hand 信任）：只有 hand 出身（手打快速捕获 / Slack self-DM）的卡有资格免审批自动派发，AI 自提、会议出生、外部 Slack/Gmail 一律照旧人工审批；外部出身卡生效档位单向升 T2（申报 tier 永不改写）并在 approve 时强制 plan 扩写（W17）。自动派发受日预算 / 单卡估价 / 并发三重上限约束（`autodispatch:` 配置块，全部可省略），超限的卡回落待审批并在卡上陈述原因，花费台账落盘，dispatch 时预算复查。
- **steer 中途转向接力**（§44.3-S，`act/lib/steer.py`）— 运行中卡上的 owner 评论作为 steer 排队，在 §44.3 的三个安全窗口（blocked / dead-resume / done-drop）flush 给会话（executor.resume 增可选 prompt=，基于 _bg_base_cmd 构建故保留 skip_permissions 开关，prompt 过 sanitize.scrub）；queued/delivered/dropped 回执按诚实投递状态记账，去重键含 ts（逐字重复的两条都算 steer）；每条已投递 steer 在卡 notes 落永久一行。
- **boardctl：headless agent 的窄接口 CLI + board-agent skill**（`act/boardctl.py`）— 读 `/api/board`、`/api/cards/{id}`；写只有 capture（走 triage 闸门，等价一条手动笔记）与 comment——刻意没有决策动词（approve/reject/accept/move/archive/merge 归 owner）。两个写动词硬编码 actor:"agent"（T-28）：agent 通道的 capture 永不自动派发，comment 只记录不 steer。JSON 输出带 schemaVersion 与类型化退出码。改编自 dashi-taskboard cli/taskctl.mjs（Apache-2.0，见 NOTICE）。
- **store2：SQLite 平行店 v1 地基（尚非写路径）**（`act/lib/store2/`）— registry YAML 仍是唯一真源。schema v1 带库内状态迁移白名单 triggers（origin_trust CHECK 集 = §50 四值 canonical，act/lib/policy.py ORIGINS 单一真源）、CAS 访问层与类型化 transitions（关死绕过审批的复合权限、origin_trust 只许 user actor 改、dispatch close / note 回执防已清除卡）、YAML 一次性迁移（回读 parity 校验，未知顶层键默认拒收）与快照导出。判例钉死 triggers、迁移告警、YAML↔DB parity、CAS 冲突与 tombstone。
- **v-next 设计文档 + NOTICE** — docs/design/vnext.md（总设计）、vnext-amendments.md（修宪案 + 测试映射）、store2-mapping.md（YAML→SQLite 字段判决）、inbox-actions.md、transplant-notes.md（移植判决台账）；NOTICE 登记 web 看板与 agent CLI 复用的 dashi-taskboard fork（Apache-2.0）。

### Changed

- **打字内容遥测改为 opt-in**（`telemetry.capture_input`）— 默认 OFF（此前默认 ON）：输入文本要进遥测，需在 Mac app 首启权限页新增的默认不勾选 checkbox、或既有 Settings 开关里显式打开。总开关 `telemetry.enabled`（事件元数据）语义不变。
- **W1：库存配额反转**（quick_capture）— open 卡永不被挤出库存投影；closed 卡只填剩余空间，上限 20（旧 delivered-pinned-past-cap 判例改钉新配额）。
- **W1.c：自动归档默认 0→30**（`archive.after_days`）— delivered 卡最后活动超 30 天自动封存进 archive（设 0 恢复永不归档；只封存冷 delivered——带未来 deadline / cluster 内有 open 兄弟卡 / 时间戳不可解析的一律不动）。
- **W18：远程直跑闸门，fail-closed 默认关**（`remote.allow_direct_run`，修 §41）— webui/syncd 等网络 ingress 的写入一律盖 via:"remote"（覆写不可 spoof），actd 的 T-28 硬后盾凭该落款把非 owner ingress 的 capture mode:"run" 一律降级为普通提案（照进 triage，不报错不吞任务）。webui 侧闸门关时另在落盘前剥掉 mode 并带 200 降级提示；opt-in=true 时 mode 原样进 inbox（§34 预留）但响应仍带 reserved 提示——actd 现行无条件降级，绝不谎报「已开跑」。开关刻意不接 owner-override；判例钉在 test_webui_remote_gate。

### Fixed

- **gmail radar：畸形 Date header 不再杀整个 scan pass** — 按信容错（宪法 11）：坏信记入 radar 失败台账后跳过，同批其余邮件照常提取。
- **sync/analytics 上传去抖** — 变更闸门 hash 前剥离易变字段（`generated_at`），dashboard 每次重建不再触发内容未变的 no-op 推送（实测线上 ~2-4GB/天）。
- **长寿命 daemon 日志自压实** — registry_writes.jsonl 的 1MB 自压实模式推广到 daemon 日志（syncd.log 实测已涨到 74MB）。

## [0.47.0] - 2026-08-18

v0.47 合并列车：10 个 PR（#94-#98、#87、#99-#101、#103，另有 CI 调参 #104），
每个 PR 过 CI + Claude/Codex 双 AI review 多轮 + 独立终审，共修复/反驳 60+ 条意见。

### Added

- **提案泳道「清理积压」按钮**（#100，§34bis）— 提案 lane header 一键起
  固定 prompt 的 direct-run 清理会话，运行中泳道出现对应 session 卡，用户可
  attach 挑选保留哪些提案。配套 registry 快照护栏（启动前快照 + 收割时比对 +
  越权写入告警，检测型、不回滚）、跨进程写入台账 `state/registry_writes.jsonl`、
  preset 在途判重（清理卡已在 approved/executing 时 ack running 不铸新卡）。
- **活标题第三档：每轮强制重审**（#103，§37.1）— 非 user_titled 卡每轮收尾
  注入当前显示名并要求重新审视：名字已不能概括当前核心动作就必须给
  `CARD TITLE:` 新名（≤40 字动词开头），仍准确原样重复即可；user_titled 卡
  连请求都不发。same-value no-op（注入/收割/落笔三侧同一 clip 规范化）、
  含 `[脱敏]` 掩码的标题在唯一落笔点 `set_display_title` 一律拒收。
- **CARD TITLE 条件强制**（#95，§37.1）— 冻结 title 不可读（URL/路径/超长）
  且无显示名的卡、direct-run 卡，dispatch 首轮强制命名，无「原样重复」豁免。
- **源开关归一 + 源死亡告警随开关**（#99，§48）— 四套并存的源开关判据收归
  `act/lib/sources.py` 单一真源；关闭的源真静默（不写 health、不投影、不告警，
  liveness/投影每 pass 现读配置）；开启的源死亡按阈值告警（睡醒双时钟宽限 +
  last_ok/last_attempt 双停摆判死 + 无基线兜底台账——plist 在而 launchctl
  load 失败的静默死角也能报）；skip_reason 出机词表清洗（闭集 +
  `public_skip_reason()`）；install.sh 探针闸门防复活关闭的雷达；Mac 面板
  install 失败落 `RepairReceiptStore` 持久回执，投影过期按源粒度回退。
- **[run] 直跑一律新卡 + 静默并入看板回执**（#96，§34.1/§44.6）— 运行框
  输入彻底绕开 merge_or_new 直落 approved（不再被并入旧卡）；radar/普通
  capture 通道的每次静默并入在看板落可见回执（不存原文、内容键去重）。
- **§44 静默并入协议的 TLA+ 机器验证**（#87）— `docs/design/SilentMerge.tla`
  + TLC 配置与跑法文档，三条安全不变量（不吞已投入卡 / 永不丢信息 / fold
  至多一次）全状态空间验证通过。

### Fixed

- **看板布局风暴**（#94，两次实测卡死 17min/70s 的元凶）— 每行 Equatable
  派生值取代全局监听、reload 剥 `generated_at` 指纹闸门、13 谓词共用纯函数，
  smoke-deploy 第 5 检按报告内 app 版本归因 `.hang`。
- **session 生命周期可靠化**（#97，§46）— stop 走 verify-first + 60s 预算
  （探测失败≠已停）、resume 风暴只记成功救活、投影 pid 活性 + 固定 question
  文案判例。
- **radar 可靠性三件套**（#98，§47）— transient 失败重试（exit 143/-15）、
  解析失败降级卡（screen 来源退化 §40 形态守 §45）、LoopHealth 盘上继承。
- **三个死开关接线**（#101，§16）— `features.analytics` 全链路 gate（Swift
  记录端 / 上传端每批新鲜快照 + mtime 指纹缓存失效 / log_first marker 时序），
  fail-closed 隐私特例扩至损坏与缺 PyYAML 的 config.yaml；`sources.slack_dms`
  接线；`features.auto_resume` 键位漂移修正（每 pass 现读）。另清理零引用
  死代码 ~300 行 + 316KB 资产。
- **静默并入 crash-retry 不再翻倍计数（TLA+ 模型检查发现）** — actd 死在
  execute() 两笔写之间（主卡 fold 已落盘、副卡 trash 未落）时，job 文件仍是
  judged，重启重跑会把 `repeated_mentions`/`silent_merge_count` 二次施加。
  `docs/design/SilentMerge.tla` 用 TLC 穷举出 5 步反例；修复以主卡 fold note
  的「静默并入 {副卡id}「」前缀作幂等标记（键=副卡 id——全文含可变
  display_title，crash 窗口内被 process_inbox/process_raising 改写会让全文
  判重落空，review 发现）。重跑不再累加计数，但收敛到 §44.4 终态：窗口内
  副卡新吸的 sources 幂等补并、EXECUTING 主卡补 §44.3 briefing、补完 trash
  （`ok_retry`）。§44 协议的三条安全不变量（不吞已投入卡 / 永不丢信息 /
  fold 至多一次）修复后全状态空间通过，跑法见 docs/design/silent-merge-model.md。
  补完路径同样落 §44.6 看板回执——用第一跑的原 note 文本，与成功路径同内容键，
  去重语义保证只一条；周一 digest 的「静默并入 N」计数补认 `ok_retry`。
- **crash-retry 收敛补漏（第二轮 review）** — 三条：(1) 幂等标记探测挪到状态
  复检**之前**——crash 窗口里副卡可能已被批准派发（dispatch_approved 先于
  consume_judged），旧序在 LIGHT 复检处静默 return False，留下永久半 fold
  （主卡带合并记账、副卡活着执行）；现按双卡现状三分收敛：副卡已被本次合并
  trash → 只补观测面（`ok_retry` 事件先查后补 + §44.6 回执），卡对仍合格 →
  补完合并，其余 → 合并中止（半程 note 打 `[已拆出 →副卡]`、留「并入中止」
  审计 note、记 `retry_aborted`，§44.5 枚举 add-only 追加），绝不静默 done。
  (2) trash 落盘之后、log_event/回执之前的 crash 窗口——合并实际完成却从
  digest 计数与看板回执里消失，由三分收敛的情形 1 补齐。(3) briefing 重放
  ——第一跑排队的 briefing 已被 reconcile flush 清队后，retry 仅查 pending
  的去重失效、同文本二次投递；executor.brief 现落 `execution.
  delivered_briefings` 台账（add-only 键，环形 20 条），queue_briefing 双重
  去重（§44.3 追记）。

## [0.46.1] - 2026-07-27

### Fixed

- **贴图被伴生文本挡住**（生产首日事故）——浏览器拷图/聊天工具截图的剪贴板
  里位图旁边总带一段 URL 文本，v0.46.0 的「图文混合让文字优先」规则（防
  Excel）把这类 ⌘V 误判成文字粘贴、图被放走。现在：单个无空白的 URL/路径
  token（大小写不敏感）视为图片元数据 → 照常贴图；另有三个确定性入口兜底
  ——**⌥⌘V 强制贴图、⇧⌘V 强制文本、缩略图行常驻 📎 按钮**；⌘V 让路给文本
  但剪贴板确有图时给 3 秒指路提示。

### Added

- **发布安全三件套**（防复发基建）——`mac/build.sh` 任何编译失败必红退
  （辅助二进制的「WARN 后继续」路径堵死）；新增 Swift 纯逻辑单元测试
  （`mac/LogicTests`，首批 7 条钉死贴图认领矩阵，CI macOS 腿必跑，本地
  三道门变四道门）；新增部署后冒烟脚本 `scripts/smoke-deploy.sh`（版本
  匹配 / 二进制特征标记 / actd 心跳 / doctor 全绿，任一不对即报警）。

## [0.46.0] - 2026-07-26

用户建议批：「提建议」入口收到的建议 + 口头拍板，一夜之间全部落地。每个功能
PR 都过了双 AI review（Claude Fable 5 行级 + Codex 汇总）与多轮对抗审查，
确认的 30+ 问题在合并前全部修复。

### Added

- **合并支持多对多**（建议 #1，#74）——合并建议可给出分组方案：10 张卡并成
  3 张而不是只能全并成一张。逐组执行、任一组出问题变成可见的失败卡（带逐组
  回执），卡面列出未入组的「保持独立」卡。
- **建议公开跟踪表**（建议 #3，#75）——提建议弹窗新增「同时公开到 GitHub
  建议跟踪表」勾选（出厂不勾、记住上次选择）：勾选的建议自动开成公开 issue，
  做没做一目了然。重复防护 effectively once-only（预写计数 + 隐形对账标记 +
  重试先对账），公开正文用 UTC 时间不泄时区。
- **三处输入框支持贴图**（建议 #4 #5，#76）——提建议、直接开跑、回答 AI 提问
  都能 ⌘V 粘贴截图（上限 4 张、自动降采样）。提建议的图只留本机（上传仅
  计数）；任务附图由工作会话用 Read 工具直接查看；30 天无引用孤儿自动清理。
- **Skills 管理**（建议 #7，#72）——设置里列出用户级/项目级 Claude Code 技能，
  可新建（frontmatter 防呆：多行描述折叠、绝不覆盖已有目录）。
- **MCP servers 管理**（建议 #10，#79）——设置里只读列出两个作用域的 MCP
  配置（传输类型徽章、概要脱敏、env 值绝不显示）。
- **开发者·开发会话**（建议 #6，#73）——设置里一键在终端打开对着本软件源码的
  全功能 Claude 会话：修 bug、加功能、提 PR 都行；repo 路径与会话 id 可配
  （flag 形状的 id 被拒，防参数走私）。
- **任务完成提醒**（建议 #8，#71）——卡片进待验收时可响铃：设置·通用三档
  「关 / 横幅 / 横幅+声音」（默认响铃——静音横幅在全屏视频下等于没通知）。
- **菜单栏徽章 = 一切等你动作的卡**（建议 #9，#78）——待拍板 + 需输入 +
  待验收三类计数（与弹窗的可见投影严格同步），不再只算待拍板。
- **每个 PR 自动双 AI review**（#77 #81-#86，repo 基建）——Claude（Fable 5，
  行级评论 + 必发总评）与 Codex（read-only 沙箱第二意见）；外部 fork PR 自动
  跳过，secret 未配置时绿色空跑。

### Changed

- **屏幕 OCR 不再发起卡片（§45 来源角色决策表，回声环的一刀）** — screenpipe
  拍到的系统自身输出（AI 会话、看板、报告）曾被 radar 再铸成新卡（回声）。
  radar 提取新增 `provenance`/`speaker` 两个 add-only 字段，出生资格由显式
  决策表裁决：屏幕来源只能佐证已有卡（fold），永不发起新卡（含 triage 失败
  回退与命中已完结卡的 follow-up 路径）；会议音频（真人）照常发起；来历不明
  最高落备选。拦截计 `echo_blocked` + analytics 留痕。决策表以穷举证明完备
  无矛盾，Hypothesis 性质测试钉死「屏幕永不发起 / assistant 永达不到直发」。
- **治理包（§0 设计宪法）** — `docs/CONTRACT.md` 顶部新增 11 条不变原则
  （修宪必须显式）；repo 根新增 `CLAUDE.md`（AI session 入职必读：三份必读
  文件 + 添加功能前必答三问 + 高频雷区）；PR 模板增设宪法三问；CI 增加
  contract-reminder 软门（改 act// mac/Sources/ 未动 CONTRACT.md 时警告，
  不阻塞）。新增 `act/golden_eval.py` 回测工具：用历史卡的真实结局评估
  triage 政策的误杀/拦截率（数据集只写 state/golden/，永不进 repo）。

### Fixed

- **反射性 ⌘Q 不再误杀后台**（建议 #2，#71）——主窗口开着（含最小化）时按
  裸 ⌘Q 只关窗口；系统注销/关机（含 ⌘⇧Q）与菜单退出照旧直通，绝不阻塞。

## [0.45.0] - 2026-07-22

### Added

- **卡片收起状态显示编号（R-xxx / MS-xxx）** — v0.42 卡面大扫除把「claude agents
  列表名」挪进展开详情后，收起的卡片上没有任何可定位的编号了。现在每张卡
  右上角常驻一枚小号等宽 ID 徽章（提案/待拍板/执行中/待验收/阶段性完成/
  回收站/永久归档/合并建议全部生效），找卡不再需要逐张展开。
- **Gmail 主动抓取后备通道（§14bis）** — Workspace 管理员禁用 app password/
  IMAP 时的第二条路：`sources.gmail.fetch_command` 配置一条用户自有的抓取
  命令（Gmail API 脚本 / MCP 客户端皆可），env 传入增量 marker，stdout 回
  JSON 数组，之后的 LLM triage 管线与 IMAP 路径完全同一条。命令失败/输出
  非法进健康状态行（`command_failed` / `command_bad_output`），与「没有新
  邮件」严格区分；配置了命令即赢过 IMAP，且不再要求 app password 存在。
  设置页 Gmail 区新增「抓取方式」分段选择器（A 应用专用密码 / B 自定义
  命令），B 模式就地填命令即时生效，A/B 切换即改即生效、命令文本保留。

## [0.44.0] - 2026-07-22

### Fixed

- **重开录制不再丢音频档位** — 权限体检页的「开启」按钮此前硬编码回「仅屏幕」：
  7-21 一次手动重开后音频采集静默停了一天，没人主动选过这个降级。现在关闭时
  记住当时的档位（`lastActiveRecordingMode`），重开按钮恢复它并如实显示
  「开启(屏幕+音频)」/「开启(仅屏幕)」；screen_audio 的 ffmpeg 预检照常把关。

### Changed

- **静默并入：重复信息不再请示（§44，改写 §38.3 第二步）** — Zelin 裁定二分法：
  重复/重合信息要么静默补进主卡，要么常规建新卡，任何需要人工点「接受」的
  合并建议卡从此消失。规则命中（§38.3 双信号原文沿用）后改为一次聚焦两卡的
  tool-less LLM 复核（detached 子进程，`state/silent_merge/SM-*.json`）：判
  同一件事 → 立即可逆并入——主卡吃进 fold note（带 §38.2 拆出句柄）+ 来源
  去重合并 + 提及数累加，副卡进回收站（`prev_status` 保留，可恢复）——**绝不
  使用不可逆的 `merged` 终态**；判不同/不确定/LLM 失败 → 一律什么都不做。
  卡对无论结局终生只查一次。副卡限轻状态（detected/raising/card_sent）：
  已投入执行的卡永不被静默移除。
- **建卡前拦截（§44.2）** — radar triage 判 `new_proposal` 后落库前跑同一
  规则+复核，同一件事直接折进既有卡，新卡根本不建。
- **会话捎话（§44.3）** — 并入目标正在执行时，增量信息经 `executor.brief()`
  以「BACKGROUND INFO (no action needed)」前缀注入其 Claude Code 会话
  （§39.2 安全窗口：working 会话绝不打断，排队等 blocked/重启时机；3 次
  失败放弃并留痕）。

### Added

- 卡面「已并入×N」紫色 chip（Mac + webui，`silent_merged` add-only 字段）；
  周一 digest 总览行「· 静默并入 N」；analytics 元数据事件
  `silent_merge_requested` / `silent_merge` / `briefing`。

## [0.43.2] - 2026-07-22

### Fixed

- **radar 提取慢性超时/截断（一个根因的三张脸）** — 大 OCR 笔记（27-70KB）单篇
  提取正常耗时 100-360s+，而 timeout=300 卡在延迟分布正中间，制造出慢性
  TimeoutExpired、流中断的 JSON 截断（exit 0 但数组断在中途）与 exit 143 三种
  表象。四件套：① timeout 300→600；② **截断抢救**——数组被截断时把完整的
  leading 对象照常落库（merge_or_new 保证重跑去重），笔记本身留在重试队列等
  一次完整提取，不再整篇作废；③ **solo-note systemic 误判修正**——单篇笔记
  独自失败（超时/截断/不可读等 note 级错误）不再被误判为系统性故障（此前
  5 次重试上限对它永不生效，每 30min 白烧一轮 300s）；仅 API/网络/key 等
  通道级错误才作废本轮账目；④ 解析失败的完整原始输出落 `state/radar_debug/`
  （保留最近 20 份）供定罪，cron 日志行新增 `ts` 时间戳（此前无法回答
  「这行是几点」）。

## [0.43.1] - 2026-07-16

### Fixed

- **「关于」页更新检查：限流不再谎报「网络不可用」** — GitHub 的匿名 API
  额度（每 IP 每小时 60 次）耗尽时返回 HTTP 403/429，此前一律显示「检查失败——
  网络不可用」，把用户支去检查 Wi-Fi；实际网络毫无问题，且额度一小时内自动恢复。
  §26 CLI 的 `error` 字段现在区分 `"rate_limited"`（403/429）与 `"network"`
  （离线/DNS/超时），关于页对限流给出如实文案：「GitHub 接口暂时限流，约一小时内
  自动恢复；你的网络没有问题。」旧 App 读新 CLI 输出不受影响（未知字段忽略，
  沿用原文案）。

### Notes

- v0.37.0–v0.43.0 七个 tag 于 2026-07-16 批量补推——GitHub 对单次 push 超过
  3 个 tag 不产生 push 事件，release workflow 因此未触发，这些版本没有对应的
  GitHub Release/资产（tag 与 CHANGELOG compare links 正常）。本版起恢复
  逐 tag 发布链。

## [0.43.0] - 2026-07-16

### Added

- **手感版 / Board motion (Mac)** — game-quality card animations on the kanban,
  so cause and effect is *visible*: until now a card changed lanes by
  teleporting on the next 5 s snapshot repaint. Board window only; pure
  display layer (zero wire/state changes — CONTRACT §43).
  - **Lane-change flights**: when a card moves columns (批准/打回/验收/暂缓,
    or the backend moving it between snapshots), a lightweight proxy — rounded
    card silhouette with the card's title, tinted with the destination lane's
    accent — lifts off, flies a curved path above the board, and lands with a
    small spring settle; the real row fades in underneath as it lands.
    Optimistic echoes count as the card they stand in for, so the flight
    launches on your *click*, and the later real snapshot doesn't re-animate.
  - **Deal-in for new cards**: fresh proposals/captures slide in from the lane
    top with a slight rotation settle, staggered 40 ms when several arrive.
  - **Off-board removals** (trash / force-merge): the card shrinks and fades
    toward the lane edge instead of vanishing. Honest limits: a card absorbed
    by an *accepted merge suggestion*, or silently rotated off the
    completed/archived lists' newest-50 cap, leaves WITHOUT animation — those
    aren't board actions, and pretending otherwise would mislead; a title the
    app can no longer resolve shows a generic 「卡片/Card」 label, never a raw
    internal id.
  - **Collapsed-strip arrivals**: a card landing in a folded 潜在任务/永久性完成
    strip flies to the strip itself and pops its count badge once (1.0→1.25→1.0).
  - **Micro-juice**: board cards get a subtle hover lift (1 pt raise, 120 ms).
  - **Restraint & control**: everything is short springs (≤ ~350 ms) that never
    block input; no animation on first load / window open, on search-filter
    changes, or on strip expand/collapse; more than 6 changes in one snapshot
    degrade to a plain crossfade. New 设置 → 通用 「看板动画」 toggle (default
    on, stored locally), and the system Reduce Motion setting force-disables
    all of it regardless of the toggle.
  - Engine: a pure Foundation snapshot differ (`mac/Sources/BoardDiff.swift`,
    moves/inserts/removals per lane) with its own swiftc harness in CI
    (`ios/tests/boarddiff/`), plus a SwiftUI flight overlay
    (`mac/Sources/BoardMotion.swift`).
- **iOS: deliberately skipped** — the phone board pages one lane at a time, so
  a cross-lane flight has nowhere to be seen; nothing changes there beyond the
  version number. The menu-bar popover also stays still (board window only).

## [0.42.0] - 2026-07-16

Display-only release（卡面大扫除）— no wire, state-machine, or analytics-id
changes (CONTRACT §42).

### Changed

- **卡面大扫除（Mac，audit #7）** — internal mechanics no longer leak onto card
  faces. The always-visible raw-command echo lines on 运行中/待验收 cards
  (`单击复制 · 双击在终端运行: cd '/U…'`) are replaced by an action-oriented
  one-liner（「单击复制指令 · 双击在终端打开会话」）; the raw command itself,
  the session id, and the `claude agents` roster name now live in 展开详情
  (with a click-to-copy line for the command, same as the log path).
- **Enum chips speak 大白话（Mac，audit #7）** — known closed sets are
  localized; unknown values still render raw (the verdictHeadline precedent):
  task state (`working`→执行中, `blocked`→受阻, `queued`→排队中, …), hardness
  (`hard`→较难 / `soft`→常规), backlog type (`code`→代码, `comms`→沟通, …),
  trash kind/reason (`suggestion`→建议, `debt`→潜在任务; `rejected`→你拒绝的,
  `deleted`→你删除的), and archived cards' previous status now shows the lane
  name the user knew (`delivered`→阶段性完成).
- **Chip comprehensibility（audit #14）** — the deadline badge says it in words
  (「已逾期 N 天」/「今天截止」/「还剩 N 天」 instead of `(-3d)`); the tier chip
  falls back to a local hint map (T0 自动执行 / T1 一键可批 / T2 需文字确认,
  unknown → 未分级) so it never renders a bare "T1"; 「重复×N」 is renamed
  「被提×N」 with a tooltip explaining restatements were merged into the card.
- **Sidebar wording（audit #15）** — 「录制与 ingest」→「录制与数据接入」
  ("Recording & Data Sources"); display only, rawValue/analytics ids frozen.
- **Doctor speaks the UI language（audit #16）** — `act.doctor`'s unclassified
  detail/fix prose now routes through the `failures.pick` single language
  switch (§15); shell commands stay English in both variants — they are
  commands. The §15 resolution itself now bridges the two halves: the Mac app
  passes its effective display language via `AIASSISTANT_UI_LANG` when
  spawning python with user-facing output, and with nothing persisted python
  falls back to the system locale (zh* → zh, else en) instead of hardcoded
  zh — no more mixed-language doctor pages for en-locale users. On first
  launch (no persisted language anywhere) the Mac app also persists its
  effective language into `settings_overrides.json` — idempotent, never
  overwrites an explicit choice — so cron/launchd copy (no `LANG` there)
  keeps matching the app instead of falling back to en.
- **Radar extraction framing（audit #19）** — `act.radar`'s extraction prompt
  is parameterized on `owner.name` and reframed as "asks directed at the
  owner"; the card source `who` names the actual note instead of a fabricated
  "manager".

### Fixed

- **「让 AI 修」 gives feedback（Mac，audit #17）** — the task-card error line's
  Fix-with-AI button used to discard the launch result; it now shows progress
  and renders a launch failure inline in red (the DepsView aiFixStatus
  pattern).
## [0.41.0] - 2026-07-16

「手机和网页不再是二等公民」— the same action now looks and behaves the same on
Mac, iPhone and the web dashboard (CONTRACT §41; display-layer + webui inbound
gate only, no new dashboard/inbox fields).

### Added

- **Web: AI merge-suggestion cards** (契约 §21/§21bis parity) — the 提案 lane
  now renders `merge_suggestions` like Mac/iOS: analyzing spinner, done verdict
  (primary/secondary titles, rationale, 接受后将执行 plan, confidence), failed
  with the error; 接受 = merge_apply, 取消 = merge_dismiss, and 仍然合并
  (merge_force with a primary-pick dialog + irreversibility notice) when the AI
  didn't land on 「合并」 or the analysis failed.
- **Web: 回收站 + 永久性完成 bookends** (v0.33 parity) — two default-collapsed
  strips below the board: trash (恢复 = restore, 永久保存 = pin) and archived
  (放回看板 = unarchive, 你封存/自动封存 badges). Deleting/archiving from the
  web is no longer a one-way door, and the confirm dialogs stop claiming it is.
- **Web: direct-run input** (v0.34 §34 parity) — the Running lane hosts the
  resident 一句话直接开跑 box (`capture` + `mode:"run"`), with the same IME
  Enter guard and clear-only-on-success draft protection as the capture box.
- **iOS: 暂缓 on the detail sheet** — the sheet now carries the same four
  decisions as the card row (批准 · 修改 · 暂缓 · 拒绝).
- **iOS + Web: T2 gate** — approving a T2 (high-impact) card is no longer a
  bare one-tap/one-click on any surface: 批准 opens a named confirm dialog
  (the Mac's 「T2 · 高影响操作确认」 title, naming the card and its estimated
  cost) before submitting.
- **iOS: device-switcher legend** — each menu row spells the freshness out
  (「● Mac mini · 在线 · 最新」; Menu strips color, so the glyph alone was
  indistinguishable), and the paired-devices settings section gains a one-line
  ●◐○ legend.
- **Web: lane help** — every lane head shows the same one-line definition the
  Mac/iOS boards show (shared LaneHelp copy).
- **webui inbound gate**: `merge_force` allowed (ids deduped ≥2 safe ids,
  primary ∈ ids — fail closed, actd still re-validates) and capture `mode`
  forwarded (only the literal `"run"` passes; anything else is a 400 and never
  reaches the inbox).

### Changed

- **iOS: 停止 is an explicit fork now** (Mac v0.21 parity) — one 停止 button on
  a running card opens the same two-choice dialog as the Mac: 退回提案
  (abort_execution, destructive) / 去待验收 (stop_to_review) / 取消, with the
  explainer line. The old one-tap destructive 停止 and the running-card
  已在别处完成 are gone — done_external lives on the reject fork, where the Mac
  keeps it.
- **iOS: 拒绝 is the Mac two-choice fork** (v0.10.3 parity) — 不想做（进回收站）
  / 已办完（记为已交付）/ 取消, on the proposal row and the detail sheet. A
  STALE/DEAD board folds its warning line into the fork message instead of
  stacking a second confirm.
- **iOS: switching devices drops the old board immediately** — Mac A's cards
  never render under Mac B's label while the fetch is in flight, and A's seq is
  never pinned into an action addressed to B. Unpairing the selected channel
  gets the same treatment.
- **iOS: the detail sheet dismisses once an action fires**, so the board's
  ack/error banner — not a stale sheet — is the next thing you see.
- **Web: 停止/拒绝 are the same forks as Mac/iOS** — the Running lane's three
  buttons (去待验收 / 停止·退回 / 系统外完成) collapse into one 停止 fork
  dialog, and 拒绝 asks 不想做 vs 已办完. 系统外完成 leaves the running lane
  (v0.21 parity).
- **Web: quick capture clears only on confirmed success** — a failed submit
  keeps your draft in the field (the toast explains), matching iOS.
- **iOS: ActionBar shows a submitted state** — after any action the button row
  becomes 「已提交…」 until the post-submit refresh lands (same busy pattern as
  the merge-suggestion card), so a second tap can't double-file the action.
- **Copy fixes** — onboarding zh/en now agree (你的 Mac ↔ your Mac); the trial
  expiry banner names the Apple Developer Program ($99/yr, done outside the
  app) instead of a dangling 「升级」; the web archive confirm says 永久完成
  instead of the retired 归档 wording.
- **Web: blocked cards sort first** — the Running lane renders needs_input
  before running, the order the lane help promises (parity with the shared
  BoardModel sort).
- **Web: typing survives the 5s poll** — the board rebuild now also defers
  while an in-board input (the direct-run box) is focused, so the caret and
  an un-committed IME composition are never dropped mid-typing.
## [0.40.0] - 2026-07-16

主题：**钱看得见、事有回执** —— 一批"系统做了但没告诉你"的诚实性欠账一次还清。

### Added

- **批准前能看到钱了** — 展开卡片详情永远有一行费用：有估算显「预计费用: $X」
  （不再受 $5 阈值影响——阈值只继续管收起状态的小徽章），没有估算的卡（双输入框
  直跑、捕获兜底、周摘要建议）诚实地显「成本未知」，不再看起来像免费。T2 高影响
  确认对话框现在也带金额（或「成本未知」）。iOS/网页的展示是后续跟进——字段已
  在共享契约里解码，只是还没有视图用它。
- **手机捕获有回执了** — 在 Slack 里给自己发的每条消息，处理完会打上一个 emoji
  回执：📥 已记下（建卡/并入已有卡/挂后续卡）、↩️ 你验收过的事回锅重新提案、
  🚫 判定不用行动（没建卡）。只打 reaction、绝不回帖；关闭开关
  `sources.slack_capture_receipts: false`。老 app 需重新粘贴 manifest 加
  `reactions:write` 权限——缺了也只是没回执，捕获照常。
- **雷达放弃一篇笔记会告诉你了** — 某篇笔记连续 5 次提取失败被放弃时，潜在任务列
  会出现一张「有一篇笔记我处理不了：<文件名>」的卡，正文指回原文件路径（你可以
  手动处理或删掉它），备注带最后的报错。之前只写进日志和统计——正是没人看的
  地方。同一篇笔记只出一张卡，永不重复；卡片文案随界面语言（中/英）。
- **周摘要失败会通知了** — 设置页点「现在生成一份」后如果 AI 调用失败或返回
  解析不了，会收到「本周摘要生成失败——可在设置页重试」的通知。之前失败无声，
  而"没有数据"反而有提示。（定时周一跑失败仍只记日志——失败不推进闹钟，每小时
  重试，无条件通知会刷一整天屏。）
- **回收站有倒计时了** — Mac 回收站每行显示「X 天后永久删除」（≤7 天变红），
  点过「永久保存」的行显示「已永久保留」。60 天自动清理不再是暗地里发生的事。
  iOS/网页无回收站列表面，不涉及。
- **通知不刷屏了** — 一次冒出 3 张以上新提案时，合并成一条「新增 N 张待审批卡」
  （文案不点名来源——新卡可能来自雷达/周摘要/捕获任何一方）；周摘要落的建议卡
  由它自己的通知点名数量，不再被重复播报；需要你逐个处理的（需输入、回锅、
  失败、待验收）保持一事一条。

### Changed

- **周一 digest 落卡，不再落盘** — 不再往工作台写 `digests/digest-*.md`、
  通知里也不再塞文件路径（App 里根本点不开）；改为像周摘要一样落一张
  「待验收」聊天卡（全文在卡里，当天重跑合并不堆叠）。1:1 准备页照常生成、
  在 digest 正文里链接。
- **页面不再说黑话** — 周一 digest 和 1:1 准备页里的条目状态从 registry 原词
  （card_sent/review/…）换成通道显示名（待审批/待验收/进行中/潜在任务…，随
  界面语言）；「双向承诺账本（manager 欠的）」改为中性表述并按 `owner.name`
  参数化（`[MANAGER-OWES]` 标签本身冻结兼容，仍被识别）。
- `quick_capture` 新增 additive seam `apply_result_with_kind`、`registry`
  新增同形 `merge_or_new_with_kind`（回执 emoji 的依据——new_proposal 内部
  触发的回锅也如实上报 ↩️；公共 `apply_result`/`merge_or_new` 签名与行为
  逐字冻结，纯委托）。
- 数据契约见 docs/CONTRACT.md §40（全部 add-only：老 App 忽略新键、老
  payload 照常解码）。

## [0.39.0] - 2026-07-16

### Added

- **需输入的卡能直接回答了（Mac + iPhone）** — 以前 AI 卡住等你输入时，卡片
  只写一句「等待: input」：你看不到它在问什么，也没法在 App 里回它，只能复制
  命令去终端。现在：
  - **卡片直接显示 AI 的问题**（它最后说的那段话，最多 500 字）——Mac 看板、
    菜单栏面板、iPhone「运行中」页都能看到；通知里也带上问题摘录，并告诉你
    卡片就在「运行中」列顶部（橙色「需输入」）。
  - **Mac：卡上新增主按钮「回答…」** —— 弹层里上面是问题全文（可滚动）、下面
    是输入框，↩ 发送。答案原路送回那个 session（上下文都在），任务接着跑。
    发送后卡上显示「回答发送中…」，送达后卡自动回到「运行中」；3 分钟没动静
    会诚实提示超时。终端路径没删——降级到「展开详情」里的「在终端接管会话」。
  - **iPhone：需输入卡也有了输入框** —— 直接打字点「发送」，走既有的端到端
    加密通道送回 Mac。手机对需输入「只读」的时代结束。
  - **送不到就明说**：session 已经没了/启动失败时，卡上显示错误、通知告诉你
    原因和终端兜底入口；如果你回答的瞬间任务恰好已经跑起来（比如别人先答了）
    或已经交付进了待验收，也会通知你「回答没送出去」并把你打的字原文存进
    卡片备注——任何情况都绝不静默吞掉你打的字，也绝不打断一个正在干活的
    session。
  - iPhone 角标现在 = 待审批 + 需输入（被卡住的 agent 是最急的事），新增
    逐卡的需输入本地通知。
  - 回答成功会顺便把这张卡的自动恢复（auto-resume）配额清零重来——你亲手救活
    的 session，之后再断线仍然享受自动恢复。
## [0.38.0] - 2026-07-16

### Changed

- **少建卡、会折叠 / Fewer duplicate cards** — 随手一句进展、一条 FYI、一句
  补充，不再动不动就变成一张新卡。判定口径反转：琐碎信息只要跟已有的卡相关，
  就折进那张卡当备注；只有全新的、真要你行动的诉求才开新卡。敢这么折的底气是
  **折叠现在可逆**（见下面「拆成新卡」）——折错了拆回来，信息不会丢。
  - 认卡更准了：给判定 AI 看的卡片清单，每张卡带上大白话显示名和几个关键词
    （标题是网址/路径的卡终于能被认出来），并先用确定性的关键词重合预筛出
    「最可能相关」的卡供它参考。全程零新增 AI 调用。

### Added

- **拆成新卡 / Split a folded note back out (Mac)** — 卡片展开详情新增
  「📎 折叠进来的信息」列表：每条折进来的备注单独一行，带「拆成新卡」小按钮。
  AI 折错了？一键把那条信息拆出去单独成卡（走正常的 AI 扩写变提案），原卡上
  的记录保留并标「已拆出 R-xxx」。提交后行内显示「拆分中…」，超时会诚实提示。
  iOS 本期只能看折叠信息所在的卡，没有拆分按钮（如实声明）。
- **重复卡自动提示 / Automatic duplicate hints** — 新卡一出现，如果跟某张
  未结的卡明显是同一件事（关键词高度重合，或同一个人提的且内容相近），看板
  会自动弹一条「规则判定」合并建议——不是 AI 分析，是确定性规则，卡上有紫色
  「规则判定」徽章说明来源。点「接受」走既有合并流程，点「取消」就再也不会
  对这两张卡重复提示；同时最多挂 3 条，绝不刷屏。
## [0.37.1] - 2026-07-16

实时字幕 credential usability（Mac 展示层，契约 add-only）。

### Added
- **旧版火山凭证支持**：豆包语音凭证输入框现在也接受旧版语音控制台的
  App ID + Access Token，自动识别。**最稳的粘法是一行 `AppID:Token`**；
  带控制台原样标签（`App ID:` / `Access Token:`，大小写/空格/下划线不敏感）
  的一行或两行也认；直接粘两行通常可用，但依赖粘贴时输入框保留换行——
  失败就改用一行形式。形状校验防误伤：两行内容只有在"第一行 6–12 位数字
  + 第二行 ≥20 位无空白 token"时才按旧版凭证解析，被硬折行的新版单 Key
  会重新拼回而不是被撕成假凭证对。引擎握手按代际发对应鉴权头（旧版
  `X-Api-App-Key` + `X-Api-Access-Key`，新版单个 `X-Api-Key` 不变）。
  存储格式：旧版凭证存为两行带标签内容（`appid:` / `token:`），单行裸内容
  一律按新版 API Key 解读——已保存的 Key 无需迁移（CONTRACT §36 add-only）。
- **「检测」按钮（两个凭证行都有）**：点一下做一次**真实**最小连接——语音
  凭证走一次 Doubao WebSocket 握手（发会话配置、读首帧、即断，不发音频、
  不产生计费），Ark Key 向所配翻译模型发一条 `max_tokens=1` 的请求。结果
  就地诚实显示：✅ 有效（连接成功）/ ❌ Key 无效或未开通 / ❌ 资源未开通 /
  ❌ 模型 ID 不存在（Ark 独立情形）/ ⚠️ 网络不通；未收录的错误码原样展示
  （码 + 服务器消息），不猜测。检测可在保存前直接测输入框内容；凭证永不
  写日志、永不回显。

### Changed
- 字幕两行凭证的文案与真实行为对齐：保存**只存本机、不联网**；点「检测」
  才真连一次对应服务器（其余凭证行"保存即验证"的行为不变）。引擎侧的
  致命鉴权错误提示改为指向「检测」按钮排查。

## [0.37.0] - 2026-07-16

「找得到、看得懂」— board search that actually finds things, and card titles
that stay readable and evolve with the work (CONTRACT §37, add-only).

### Added

- **看板搜索全量化 (Mac)** — the board search box now matches far more than
  title/summary/plan/dod/id:
  - **normalized matching** (`shared/Sources/SearchMatch.swift`): "eb1" finds
    "EB-1A", "h1b" finds "H-1B" (`-`/`_`/`.`/spaces are stripped from both
    sides before comparing), CJK matches as a plain substring, and a
    multi-word query is AND — every word must hit the card. "eb2" still does
    NOT match "EB-1A".
  - **expanded word list per lane**: display/former titles, notes (comments &
    radar updates, newly projected as a capped `notes_text` row field),
    delivered summaries and final drafts, source quotes, and the agent name.
  - **session-content layer**: actd maintains `state/search_index.json`
    (per-card main-thread transcript text — the boilerplate dispatch prompt
    of the first user turn is excluded — tail-capped ~50KB, refreshed only
    at the existing harvest/promotion touchpoints — zero new LLM calls) and
    the Mac app searches it as the LAST layer with cross-layer AND: each
    query word may be satisfied by a row field OR the transcript, so
    "推荐信 chen" finds the card whose title says 推荐信 while only the
    session mentions chen. Cards that matched but not on their visible
    fields alone get a purple 「命中会话」 badge. Pruning removes only
    irreversibly-gone cards (merged / hard-purged) — a trashed-then-restored
    card keeps its session search. The file is Mac-local and never enters
    dashboard.json (the E2E board payload does not grow); missing/corrupt
    index = the layer is silently absent. Typing stays smooth on large
    boards: the input echoes instantly, filtering debounces ~200 ms, and
    normalized card/session text plus per-card hit results are memoized per
    dashboard decode / query / index reload.
- **活标题 display_title (§37)** — the internal `title` stays FROZEN (it is
  the dedupe/re-raise identity anchor); a new optional `display_title` +
  `user_titled` + `former_titles` ride the registry and every dashboard row:
  - **fallback chain at projection time** — stored display_title (user or
    LLM) → deterministic `sanitize(title)` (URL → "domain ▸ segment", path →
    last component, overlong text → first-clause clip with …) → title. A raw
    URL/path can never appear as a board title again, with zero migration
    for legacy cards.
  - **LLM titles piggyback on existing calls only**: quick-capture/triage and
    debt-expansion prompts gain an optional `display_title` output key
    (≤40 字中文大白话, 动词开头); executor closing prompts allow an optional
    standalone `CARD TITLE: <new name>` line that `harvest_delivery` parses
    (same fence discipline as `FINAL DRAFT:`, stripped from both outputs)
    and actd applies at the same promotion points as delivered_summary —
    titles refresh at round boundaries as the discussion evolves.
  - **user sovereignty**: new `set_title` inbox action (fail-closed ≤64-char
    validation at syncd/webui/actd, v0.33.1 boundary doctrine) pins
    `user_titled` — a user-chosen name is NEVER overwritten by LLM/harvest
    titles. Mac: ✏️「改名」inline editor in every card's 展开详情, with an
    optimistic name echo (180 s honest timeout notice). Renamed cards stay
    findable: previous names land in `former_titles` (capped 3, searched,
    shown as 「曾用名: …」in the detail).
  - iOS displays the new titles automatically via the shared row helpers
    (`displayHeadline`/`rowTitle`/`BoardModel.title(of:)`).

### Honest scope cuts

- **iOS has no board search UI this release** — search (including the new
  normalized matching and session layer) stays Mac-only; the phone only gains
  the readable display titles on its rows.
- **iOS has no rename entry this release** — `set_title` can be written by
  the Mac app (and webui API); the phone renders `display_title` read-only.
- webui's own search/filter surface is unchanged.

## [0.36.0] - 2026-07-15

### Added

- **实时字幕 / Live captions (Mac)** — a lyrics-style, always-on-top subtitle
  overlay with real-time speech-to-text and optional zh↔en translation.
  Toggle it from the menu-bar 录制 menu or 设置 → 实时字幕; the overlay is a
  non-activating floating panel (draggable, resizable, joins every Space and
  full-screen app, never steals focus), showing the live partial line under
  the last finalized sentence. **BYO-key model: the app ships no API key and
  only *supports* the feature — you bring your own.**
  - **Engines** (设置 picker, default 自动): 豆包流式语音识别 2.0 over
    WebSocket (needs your own 火山引擎 speech API key — personal accounts
    qualify, ≈ ¥1/hour with 20 free hours; best zh/en code-switching and
    punctuation), or **Apple on-device** SpeechAnalyzer — free and fully
    offline but **macOS 26+ only** and single-language (中文 or English,
    picked in settings). 自动 = Doubao when a key is saved, otherwise Apple.
  - **Audio sources**: microphone, system audio (via ScreenCaptureKit — rides
    the existing Screen Recording grant), or both mixed (default). The mic
    prompts for its own permission on first enable. Capture is in-process and
    fully independent of the screenpipe recording engine and its modes.
  - **Optional translation** (Doubao engine only): finalized sentences stream
    through Ark `doubao-seed-1-6-flash` (needs a **second** key from the Ark
    console; usually < ¥0.1 per captioned hour) into a 原文小字 + 译文大字
    pair. Direction 自动/中→英/英→中. The Apple engine is captions-only.
  - Both keys live in local `config/secrets/` (0600) next to the existing
    secrets; only the app reads them — never Python/cron. Caption text never
    leaves this Mac except to your own ASR/translation endpoints, and never
    appears in telemetry.
  - Honest failure surfaces throughout: invalid/unactivated key, missing
    permission per source, engine unavailable on this macOS — each states
    what is missing and where to fix it, with automatic reconnect + backoff
    for transient network drops. A fatal engine failure stops ALL capture
    (mic/screen indicators go dark) while the overlay keeps the reason
    visible and the menu item annotates itself instead of a checkmark.
  - Pause (overlay hover button) fully stops capture and the engine
    connection — nothing is captured or billed while paused; the last lines
    stay on screen and resume rebuilds the pipeline. Hard privacy invariant
    throughout: audio capture only ever runs while captions are enabled and
    the overlay is visible (every async completion re-validates ownership,
    so a toggle-off can never leave an orphaned mic tap or screen-capture
    stream behind).
  - New swiftc test gate `ios/tests/captions/run.sh` (wired into CI): the
    hand-rolled Doubao binary wire framing (byte-exact vectors), gzip payload
    decode, definite/partial dedup, the 2-line roll-up reducer, the async
    ownership gate, pcm mixing, and the paused-status precedence.

### Known limitations

- The Apple on-device engine requires macOS 26+; on older systems only the
  Doubao engine (with your key) is available.
- Translation requires the separate Ark key and the Doubao engine; audio/UI
  capture paths are manually tested (they cannot run headlessly in CI).

### Fixed

- **Re-raised rounds actually run now.** A finished (delivered) card keeps its
  agent session id for the record, and the §3.5 re-raise flip used to leave it
  in place — so after you approved the re-raised round, the dispatcher skipped
  the card as "already dispatched" and it sat queued forever, with no agent
  behind it and no error anywhere. The flip now archives the finished round's
  session id (as `reraised_session_id`) so the new round launches like any
  other approval. Both re-raise entry points (deterministic radar backstop and
  the LLM triage/quick-capture paths) share the fixed seam.

## [0.35.0] - 2026-07-15

### Added

- **Custom Mac device name for phone pairing (设置 · 同步/配对).** The device
  name shown on the phone — previously the hardcoded 「这台 Mac」 — is now an
  editable field in Mac Settings, defaulting to the Mac's computer name
  (max 64 chars). Committing a rename re-runs the idempotent pair path
  (`--pair --label`), so the QR and `state/sync.json` update immediately while
  channel_id / secrets / epoch stay stable.
- **Rename without re-scan.** `dashboard.json` gains an optional top-level
  `device_label` (add-only, CONTRACT §35) mirroring the pairing label; the iOS
  app adopts it after each board refresh, updating the stored channel label in
  memory and Keychain. Old apps ignore the key, old payloads still decode, and
  re-scanning the QR keeps working exactly as before.

## [0.34.0] - 2026-07-15

### Added

- **Dual input — type in the Running lane to run it now** (CONTRACT §34). The
  运行中 lane gets its own resident input, next to the existing proposals one:
  whichever box you type into decides the slot. The proposals box keeps
  today's behavior (AI researches → proposal card → you approve); the new
  Running box (「一句话，直接开跑（跳过提案）…」) files your one-liner straight
  into the approved queue — the agent's first job is to gather its own
  context, and the deliverable still lands in 待验收 for your acceptance.
  Vague asks resolve through the existing 需输入 flow. **Honest caveat:
  direct-run skips the proposal/cost preview entirely** — there is no plan or
  estimate to review before the agent starts. By design, everything the run
  box queues is pinned to chat delivery at the default workbench and never
  touches a repo — even when your line matches an existing card that carried
  repo routing, the promoted card is rewritten to chat (a notes tag records
  the reroute), so no branches or PRs land anywhere you didn't preview
  (file-type outputs go to the workbench `deliverables/` directory per
  CONTRACT §33). Matching an existing card never spawns a twin agent: an
  open proposal/backlog card is promoted in place, a card already
  queued/running just absorbs the mention, a finished (delivered) card is
  re-raised as a new round that genuinely re-dispatches, and a line that
  matches a card sitting in 待验收 starts nothing — the app says so honestly
  instead of pretending a launch happened. Available on the Mac
  board column + popover Running section and the iPhone Running page
  (`mode:"run"` on the capture action, add-only); the web dashboard does not
  get the Running input this release.

## [0.33.1] - 2026-07-15

### Fixed

- **Whole-repo adversarial audit: 72 confirmed findings fixed across every
  subsystem** (15 auditors + 3-lens verification per finding; 11 high). The
  themes, each with regression tests:
  - **Data safety (registry/actd/ingest).** `next_id()` can no longer reissue
    the id of an unreadable card file (filenames now count toward the id
    range) and `save()` refuses to overwrite state it could not read — a
    corrupt or hand-broken YAML file previously let one save wipe every
    sibling card. A poison inbox file (e.g. a non-string `comment`) used to
    re-crash the daemon every 10 s pass forever while re-folding the same
    comment into the card each round; field types are now validated at all
    three boundaries (phone→syncd, web→webui, actd itself) and any future bad
    file is acked `bad_json` and removed, terminal for that file only.
    Ingest markers advance only over rows actually written to a dump that
    actually landed; the PID lock now covers the headless-claude child; a
    failed mirror round salvage-pushes (or pends) its products so the next
    round's pull can never `--delete` them.
  - **Never-lose capture, for real.** A hallucinated/sealed `relates_to`
    target now falls through to filing a new card instead of silently
    dropping the capture; iPhone quick-capture keeps your text (and shows an
    error banner) when the upload fails; a failed Mac menu-bar text drop
    springs back and says so; dragged text is captured, never executed as a
    slash command.
  - **The app stops lying in small ways.** Inbox acks now report what
    actually happened (dropped/no-op actions no longer ack "已生效"); the
    setup wizard can no longer celebrate 🎉 over a vault it failed to
    configure; a refused recording-mode switch explains itself where you
    clicked; a corrupt `settings_overrides.json` blocks further writes
    instead of being silently replaced by one surviving setting; the retired
    Full-Disk-Access permissions row (whose copy overstated coverage) is
    gone; web-dashboard destructive buttons name the card and admit the web
    UI has no undo.
  - **HTML deliverables land as files** (the reported bug): chat-mode
    sessions now write file-type artifacts under the workbench
    `deliverables/` directory and report ABSOLUTE paths after `FINAL DRAFT:`
    (sessions run inside hidden git worktrees, so relative paths pointed
    nowhere); the review card hydrates 复制成稿 from the file so it still
    copies paste-ready HTML. FINAL DRAFT parsing is code-fence-aware and no
    longer misses a draft buried behind a closing remark; a failed 打回
    (rework) surfaces instead of silently discarding your feedback.
  - **Sync hardening.** `board_snapshots.updated_at` is now stamped by the
    server (new migration + trigger), so a Mac with a skewed clock cannot
    paint a dead board FRESH on the phone; the phone rejects replayed older
    board snapshots, pins `expected_status` on card actions (activating the
    §32.2 stale-action guard end-to-end), and renders sync/action errors in
    a visible banner; a custom pairing label is no longer reset to 「这台
    Mac」 every time Settings opens; phone actions that fail to write locally
    are retried instead of being falsely marked delivered.
  - **Board UI honesty (Mac).** 放回看板 feedback and timeouts now render
    inside the 永久性完成 strip (which auto-opens for them); board search
    force-opens the collapsed 潜在任务 strip when it has matches; the
    multi-select bar count always matches what submit will actually send;
    merge-suggestion cards show real titles for backlog cards; a failed
    研究并提议 no longer leaves a ghost 研究中 placeholder.
  - Plus: Monday digest / 1:1 prep no longer create a placeholder
    `~/Projects/your-workbench`; 进化建议 dedup works across Mondays; false
    「需要重新登录」 alarms from cwd text in launch logs; doctor multiline
    key false-negative; webui anti-framing headers, IME-safe Enter, stable
    DOM under the cursor, and delivered-summary display; `build.sh --install`
    stages before it swaps so a failed copy can't delete the installed app.

### Added

- **CI now actually guards the Swift half.** Every PR compiles the iOS app
  and the Mac app WITH Sparkle, runs the Swift↔Python E2E crypto interop
  gate (hardened against vacuous passes), runs a new 28-assert shared
  contract test harness (lossy board decode, BoardModel lane projection),
  and enforces iOS version sync with `act.__version__` (was frozen at 0.1.0).
  Releases preflight that the Sparkle signing key matches the public key
  baked into the app before building anything.
- **+144 Python regression tests** (suite 1069 → 1213), most written to fail
  against the pre-fix code (verified by stashing the fixes).

## [0.33.0] - 2026-07-15

### Changed

- **Board lanes now say what they mean — three renames, zero data changes.**
  The old names described mechanics (储备/已验收/归档); the new ones describe
  what the card *is* to you at 1 a.m.:
  - **储备 · Backlog → 潜在任务 · Backlog** — real-but-not-urgent things that
    *might* become tasks; nothing here runs on its own.
  - **已验收 · Done → 阶段性完成 · Done for now** — you accepted this round,
    but the thread may still be waiting on someone's reply; it can go back to
    Review any time.
  - **归档 · Archive → 永久性完成 · Done for good** — truly over, sealed. The
    card button 归档 → **永久完成 (Done for good)**, and 取消归档 → **放回看板
    (Put back)**. Archive-row badges now read 你封存/自动封存 (You sealed /
    Auto-sealed).
  - The defer button — 入库 on Mac, 存备选 on iPhone/web (a name retired
    back in v0.22.0!) — is finally ONE word everywhere: **暂缓 · Later**.
  提案 / 运行中 / 待验收 and the 验收 (Accept) button are unchanged. Purely
  display-layer: registry statuses, dashboard keys, inbox action names and
  analytics events are all frozen (docs/CONTRACT.md v0.33.0 note).
- **The Mac kanban gets bookends: two default-collapsed strips.** 潜在任务
  (far left) and the new **永久性完成 board presence** (far right) start every
  launch as narrow vertical strips — the five-lane workflow keeps the room,
  the parking lot and the archive stay one click away. Click a strip to expand
  it into a normal column (the archive one opens with the familiar search +
  put-back rows); click the column header to tuck it back. Expansion sticks
  for the session but is never persisted. When you press 暂缓 on a proposal,
  the backlog strip auto-opens so the 暂缓中… echo is never invisible. The
  archive strip is still not a board lane: no multi-select, no merge.
- Weekly digest section headers, quick-capture replies and triage prompts now
  use 潜在任务 instead of the two-generations-old 欠账/备选 vocabulary.
- iPhone app: lane titles/help follow the renames automatically (shared copy);
  still five pages, deliberately no archive lane.

## [0.32.0] - 2026-07-14

### Added

- **强制合并 / Force-merge cards (Mac + iOS).** Alongside the AI-driven **合并
  建议 (Suggest merge)**, you can now merge cards yourself when you're certain —
  skipping the AI analysis entirely. Every path routes through a confirmation
  sheet where you pick which card stays as the **主卡 (primary)** and read a
  plain-language, *not-reversible* warning:
  - **看板多选 → 操作条「强制合并 (N)」** (Mac): tick ≥2 cards and merge now.
  - **建议卡「仍然合并」(Merge anyway)** (Mac **and iPhone**): when the AI
    suggests *keeping cards separate* (or its analysis failed) but you disagree,
    override it in one click. The iPhone app now mirrors the merge-suggestion
    cards too (analyzing / done / failed, with **接受 / 取消 / 仍然合并**).
  The merge itself is the exact same deterministic operation as accepting an
  AI **merge** verdict — the primary absorbs the secondaries' sources, repeat
  counts, notes and finished deliverables; each secondary stops and becomes
  terminal **已合并 (merged)**. On Mac, involved cards show a **合并中…
  (Merging…)** badge until it lands.

## [0.31.1] - 2026-07-14

### Fixed

- **A cron round can no longer wipe the previous round's in-flight ingest
  work in the vault mirror.** Real incident hours after v0.31.0 shipped:
  the previous round's claude was still writing raw/wiki in the mirror when
  the next round's export ran the pull — rsync `--delete` destroyed every
  un-pushed product, and since a mirror-mode dump exists only in the mirror
  until push (with the export markers already advanced), the source dump
  died with it. Two guards: the pull now skips while the processing PID
  lock is held by a live process (the mirror is a live workspace then, not
  a stale copy — the round's own push carries everything home), and the
  export pushes immediately after writing a dump when no processing is in
  flight, so the source lands in the real vault the moment it exists.

## [0.31.0] - 2026-07-14

### Changed

- **Vault-mirror mode: the pipeline no longer touches ~/Documents — one
  app-identity grant replaces every per-tool permission.** Incident
  (2026-07-14): the claude CLI now installs per-version binaries and macOS
  keys permission grants to the real binary path, so every CLI update became
  a new TCC identity — the GUI re-prompted "would like to access your
  Documents folder" on each update, and cron (nowhere to show a prompt) died
  with `EPERM`: 38 consecutive screenshot→notes failures 07-09→07-13. Now a
  `vault-sync-helper` compiled into the app bundle (same bundle id + the
  stable TCC-safe signing identity) is the ONLY thing that touches the
  Obsidian vault: it pulls the vault into a repo-local mirror
  (`state/vault-mirror/`) at the top of each ingest run and publishes
  results back afterwards (additive `--update` everywhere; inbox deletions
  are manifest-based so a file dropped mid-run is never destroyed; a failed
  publish is retried before the next pull so results are never wiped).
  claude, python and bash all work repo-local — the radar and weekly digest
  read the mirror too (`config.effective_obsidian_raw`). The permissions
  checkup gains a "Notes vault access" row: ONE standard GUI prompt, and no
  pipeline permission ever needs granting again — across app AND claude
  updates. Everything degrades automatically to the legacy direct-vault
  behavior when the helper or the grant is missing (Linux/Windows included);
  mirror mode is an upgrade, never a requirement. Also: the ingest claude
  call now runs under a 2 h watchdog (a wedged run once held the chain's
  lock for 41 hours).

### Fixed

- **A doomed switch to Screen + Audio can no longer silently kill recording,
  and the menu bar stops blaming permissions for every engine death.**
  2026-07-13 incident: the Screen + Audio engine hard-requires ffmpeg at
  startup and screenpipe's built-in auto-installer is unreliable (it wrote a
  working binary yet still exited "os error 2" every attempt), so switching
  modes pkilled a healthy screen-only engine, every replacement spawn died
  seconds later, capture stopped — and the menu bar guessed "多半缺「屏幕录制」
  权限" even though the TCC grant was fine. Three-part fix (CONTRACT §25
  add-only): (1) new failure id `engine_ffmpeg_missing` — detected only in
  the engine-log context (`failures.classify_engine_log` + Swift mirror
  `diagnoseEngine`, screenpipe's exact install-failure phrasing; card or
  dispatch text like "failed to install ffmpeg-python" never triggers it),
  with an "Install ffmpeg" action (`install_ffmpeg`; the catalog sentence
  names `brew install ffmpeg`); (2) switching to Screen + Audio now
  prechecks ffmpeg by EXECUTING `-version` (a file test proves nothing —
  the broken installer leaves artifacts behind) and refuses the switch —
  explained via a 15 s in-app note plus a notification — instead of killing
  the running engine first; a click made stale by a newer mode choice is
  dropped; (3) a mode switch whose engine fails to start rolls back to the
  previous mode automatically (one attempt, with a self-contained notice),
  guarded by a slow-death watch: the doomed engine outlives a naive +0.5 s
  liveness check by ~4-5 s (pgrep sees the npx wrapper immediately), so the
  switch is re-verified at ~8 s before being declared good, and a mode the
  user picked meanwhile is never clobbered. The menu-bar "not recording"
  line now names the actual classified cause (ffmpeg / Node.js / crash),
  reserving the permissions wording for when Screen Recording is genuinely
  missing, and offers an "Install ffmpeg…" item when that is the diagnosis;
  the recording page's ffmpeg row pairs "Install ffmpeg" with an
  "Installed — restart engine" retry. The engine spawn PATH now also covers
  the common ffmpeg install dirs (`~/.local/bin`, Intel-brew
  `/usr/local/bin`, MacPorts `/opt/local/bin`) so a present ffmpeg is always
  found — the non-interactive login shell never sources `.zshrc`, which is
  where those dirs usually get added (root-cause hardening from PR #42; the
  precheck probes the same locations).
- **A finished task can no longer sit in 运行中/需输入 forever after its
  session goes quiet.** 2026-07-14 R-041: a chat-mode agent printed its
  complete `FINAL DRAFT` and settled — but a background session never exits
  on its own, so the roster reported "blocked / waiting for input" and the
  board showed 需输入 for hours with the finished brief already in the
  transcript; after the Mac slept, the session was purged from the roster
  entirely, where the reconciler's only move was a resume (spawning a
  confused duplicate of an already-finished job). The reconciler now probes
  the transcript FIRST in both situations — blocked agents and
  vanished-from-roster sessions — and a standalone `FINAL DRAFT` marker (the
  chat-delivery contract's strong completion signal) promotes the card
  straight to 待验收 with the harvested draft. A bare last-message summary
  deliberately does NOT short-circuit anything (any dead session has last
  words; only the explicit marker proves delivery), so the auto-resume path
  for genuinely crashed sessions is unchanged. Transcript probes are
  throttled to one per session per 2 minutes.

## [0.30.0] - 2026-07-13

### Changed

- **Multi-device sync reworked to a QR-only capability model (no account /
  email).** Pairing is now the whole story: each Mac holds a stable
  `channel_id` + `write_secret` + E2E key, all carried in one QR shown in
  **Settings → 同步 / 配对 (Sync / Pairing)**. The phone scans it (once per Mac,
  any number of phones/Macs) — no email OTP, no login. Supabase access is gated
  by the QR itself: reading a board needs the (unguessable) `channel_id`, writing
  needs the `write_secret` (verified server-side via a hardened SECURITY DEFINER
  RLS check); card bodies stay end-to-end encrypted with the QR's key. This
  removes the v1 email/OTP flow and the `exchange_device_token` edge function /
  per-device JWT entirely (the v1 design assumed editable email templates and
  HS256 JWTs — neither holds on a free-tier ES256 project). **Security posture:
  the QR is the master key for that Mac's board — keep it private.** iOS app is
  now QR-only + multi-channel. `syncd` uses the anon key + `x-sync-channel` /
  `x-sync-write` headers. Supersedes the v1 sync tables (dropped + replaced).
  CONTRACT §31 (v0.30.0 supersession note) + docs/design/qr-only-capability-sync.md.

## [0.29.0] - 2026-07-13

### Added

- **Cross-platform release bundles — every Release now ships install packages
  for all three platforms.** Previously only macOS got a downloadable artifact
  (`.pkg` + `.zip`); Windows/Linux friends were told to `git clone`. Now the
  release workflow also produces two portable source bundles —
  `ZelinAIAssistant-<tag>-linux.tar.gz` and `ZelinAIAssistant-<tag>-windows.zip`
  — each a self-contained tree of exactly the files the headless pipeline needs
  (`act/`, `ingest/`, `webui/`, `config/`, `config.example.yaml`,
  `requirements-cloud.txt`, `docs/`, the READMEs, `LICENSE.md`, `CHANGELOG.md`,
  `uninstall.sh`) plus the platform install script (`install-linux.sh` /
  `install.ps1`). The Swift app sources (`mac/`, `ios/`, `shared/`) and repo
  plumbing are excluded. A friend downloads the archive, unpacks it under a
  single `ZelinAIAssistant-<tag>/` dir, and runs the install script from the
  extracted tree (both locate the repo root via their own path) — no git clone
  required. Built by the new `scripts/package-portable.sh` (deterministic file
  set, no compilation, `tar` + `zip` only). Both archives are covered by the
  release `checksums.sha256` and the SLSA build-provenance attestation, and the
  bilingual release notes now point macOS / Windows / Linux users at their
  respective download. `docs/LINUX.md` and `docs/WINDOWS.md` document the new
  download-the-bundle install path alongside the existing git-clone one.

## [0.28.1] - 2026-07-12

### Fixed

- **A 待验收 card whose session is actively working again now shows in 运行中.**
  Previously, if you `claude attach`ed back into a delivered card's session and
  kicked off real work (e.g. a follow-up deep-research), the card sat in 待验收
  behind a calm "会话有新活动" badge while the 运行中 lane read 0 — the board
  didn't reflect that a session was actively burning compute. Now such a card is
  projected into the 运行中 lane (`from_review`) while its session runs, and
  falls straight back to 待验收 (with a refreshed draft) the moment the session
  settles. This is a **presentation-only** reroute — the on-disk status stays
  `review`, so the ✓验收/↩︎打回 verdict and the delivered draft are preserved and
  auto-resume is never triggered. The 停止 button on these cards now works:
  `stop_to_review` / `abort_execution` accept `review` status (CONTRACT §30 /
  §10 v0.28.1 add-only), giving review cards their first in-app stop path.

## [0.28.0] - 2026-07-12

### Added

- **Deliverable output format setting (Markdown / HTML).** New Settings control
  (通用 → 交付物默认格式 / General → Deliverable format) that picks the markup
  language the assistant drafts documents, reports and the `FINAL DRAFT` block
  in. `markdown` is the default and leaves behavior **byte-identical** to before
  (the executor prompt is unchanged); `html` injects an HTML-authoring
  instruction so drafts come back as valid, self-contained HTML instead of
  Markdown. Persists as the `default_output_format` key (config.yaml top-level
  or `settings_overrides.json`, diff-written vs the config layer); invalid/typo
  values fail safe to `markdown` (CONTRACT §15 v0.28 add-only).

## [0.27.0] - 2026-07-12

### Added

- **Multi-device cloud sync (Mac side, OPT-IN, OFF by default).** A new `syncd`
  daemon relays the board to your other devices via Supabase with **per-pairing
  end-to-end encryption** — the server (and the maintainer) can't read card
  bodies. Paired with it, a companion **iOS app** (in `ios/`, build in Xcode)
  lets you view and approve from your phone.
- Shared Swift contract types moved to `shared/` and are now compiled into both
  the Mac and iOS apps, so the two stay byte-for-byte in agreement on the wire
  format.

### Note

- **Beta / setup required.** Nothing syncs until you (a) deploy the Supabase
  migrations + enable Auth (see the wake-up steps), (b) opt in + pair, and
  (c) build the iOS app with your Apple ID.
- **Honest limits (also shown at the consent gate):**
  - End-to-end encryption hides card **bodies**, but not **metadata**: the
    number and size of cards leak from the encrypted blob's size. Anyone who can
    see your Supabase rows learns roughly how much you have on the board, never
    what it says.
  - The device-token Edge Function mints an **HS256** (symmetric) token. It
    **fails closed** if your Supabase project's JWT signing is asymmetric — so
    verify your project's JWT signing config at deploy time before relying on it.
  - `cryptography` is an **optional dependency** — it is only needed for cloud
    sync (`pip install -r requirements-cloud.txt`). A local-only install never
    imports it and is unaffected.

## [0.26.0] - 2026-07-12

### Added

- **Windows support (beta).** The headless Python core now runs under **Task
  Scheduler** — `install.ps1` renders and registers the daemon, radar, web-UI,
  and digest tasks — with the **web dashboard** (`python -m act.webui`) as the
  Windows UI. Notifications use **native Windows toasts**, and `doctor` now
  understands scheduled tasks (task state parsed from `schtasks`) alongside the
  existing launchd and systemd checks.

### Fixed

- POSIX-only `fcntl` import crashed module import on Windows; the import is now
  guarded so the whole test suite can be imported (and run) on Windows.

### Note

- **Windows is beta, like Linux.** Screen-capture ingest is deferred (the web
  dashboard and the ingest cron chain are the Windows surface for now), and Task
  Scheduler task loading / toast notifications / the daemon's `PATH` still need
  testing on a real machine — friends are welcome to file PRs. Task Scheduler's
  restart-on-failure handling is also weaker than launchd/systemd.

## [0.25.0] - 2026-07-12

### Added

- **Linux support (beta).** The headless Python core now runs under **systemd
  user units** — `install-linux.sh` renders and installs the daemon, radar,
  web-UI, and digest units — with the **web dashboard** (`python -m act.webui`)
  as the Linux UI. `doctor` now understands systemd (unit / timer state parsed
  from `systemctl --user`) alongside the existing launchd checks, and the test
  suite now also runs on **Windows** in CI (non-blocking for now) as the
  cross-platform foundation.

### Note

- **Linux/Windows are beta.** Screen-capture ingest is deferred (the web
  dashboard and the ingest cron chain are the Linux surface for now), and
  systemd unit loading / desktop notifications / the daemon's `PATH` still need
  testing on a real machine — friends are welcome to file PRs.

## [0.24.0] - 2026-07-12

### Added

- **Local web dashboard (`python -m act.webui`).** A cross-platform,
  browser-based view of the task board that reads the same dashboard and writes
  the same approvals as the Mac app — so you can watch and steer the board from
  any browser, not just the menu-bar UI. It binds to `127.0.0.1` with a
  per-install token plus Host/Origin checks, so it is not reachable from other
  machines. This is the first step toward Windows/Linux support (the UI has been
  macOS-only until now).

### Fixed

- **`bash mac/build.sh` no longer fails on a fresh checkout without Sparkle.**
  When the Sparkle framework wasn't vendored, expanding the empty
  `SPARKLE_FLAGS` array under `set -u` on macOS's default bash 3.2 raised an
  `unbound variable` error, breaking the "builds fine without the framework"
  fallback. The array expansions are now bash-3.2-safe.

## [0.23.0] - 2026-07-12

### Added

- **One-click auto-update via Sparkle + EdDSA.** The app now downloads,
  verifies (EdDSA signature **and** code-signature), installs, and relaunches
  the new version with a single click — or fully automatically in the
  background — so there's no more manual trip to GitHub to grab the `.pkg`. The
  existing 「检查更新」/「新版本可用」 surfaces (About-page update row and the
  menu line) are wired straight to it. It stays **free**: updates are
  authenticated with the stable self-signed code-signing identity plus an EdDSA
  appcast signature, so no paid Developer ID is needed. Because the update is a
  `.pkg`, the installer asks for your admin password **once per update** (the
  same prompt as a manual install); your settings and task data are preserved
  across the upgrade. **Note the transition:** the *first* Sparkle-enabled
  version (v0.23.0) must still be installed manually once — every update after
  that is one-click / automatic ([`Closes #38`](https://github.com/Wan-ZL/zelin-ai-assistant/issues/38)).

## [0.22.0] - 2026-07-12

### Added

- **Multi-card merge selection now works across all board lanes.** The
  merge-selection affordance is no longer limited to a single lane — you can
  select cards in 储备 / 提案 / 运行中 / 待验收 / 已验收 and request a merge
  proposal across them (legality of cross-status merges stays with the backend
  `merge_review`).
- **Running cards now stop into review instead of vanishing.** The single
  「停止」 on a running card opens a 退回提案 / 去待验收 choice:
  `stop_to_review` stops the agent but **keeps what it produced** and lands the
  card in 待验收 for you to check, instead of discarding the run (退回提案 /
  `abort_execution`) or skipping review entirely.

### Changed

- **Backlog lane renamed 备选 → 储备** (and its proposal defer button
  存备选 → 入库) — a display rename of the former debt/backlog lane; the
  underlying `defer` action and `detected` status are unchanged.
- **Proposal decision buttons are back to one compact row** (批准 · 拒绝 ·
  修改 · 入库), with 展开 demoted to a right-aligned disclosure link rather than
  competing as a fifth button.

### Known limitation

- Stopping an agent **externally** inside Claude Code can still trigger
  auto-resume; use the in-app 「停止」 button to reliably land the card in
  待验收 (follow-up tracked).

## [0.21.0] - 2026-07-12

### Added

- **Settings redesign — collapsible sections + fuzzy search.** Settings is now
  organized into collapsible sections (**default collapsed**) with a search box
  that fuzzy-matches setting names and reveals the matching sections, so the
  growing list of integrations stays scannable.

### Removed

- **⚠️ iMessage transport removed, and Slack's phone-approval commands /
  reactions removed** — a user-visible capability removal. Dropped: the iMessage
  radar (`act/radar_imessage.py` + its launchd agent), the `phone_channel` /
  `imessage_self_handle` config, all outbound notification mirroring to the
  Slack self-DM, the `批准/拒绝/打回/验收 R-xxx` phone command surface, and the
  ✅-reaction approval poll. Upgrades auto-unload the stale `imessageradar`
  launchd agent.
  - **Mobile approval now happens in the Mac app** — it is the sole approval
    surface (a dedicated **iOS app is planned**). Migration: approve/accept
    cards in the Mac app.
  - **Slack self-DM QUICK-CAPTURE is KEPT** — only the phone-approval commands
    were removed. DM yourself a one-liner (or a photo/video) and it still
    triages into a card; self-DM is now a one-way capture inbox (the assistant
    no longer posts replies or notifications back into it) and remains the
    mobile-capture path until the iOS app ships. Slack ingest (DMs / group DMs /
    @mentions + MCP fallback) is unchanged.

### Changed

- **Permissions: Full Disk Access row repurposed for scheduled jobs.** With the
  iMessage radar gone, the FDA capability row no longer references Messages; it
  now explains that Full Disk Access is for scheduled background jobs
  (cron/launchd) reading protected data while the app isn't open.

## [0.20.1] - 2026-07-12

### Fixed

- The first-run finale coach-mark ("我在这里 👆") now points at the menu-bar
  icon instead of floating in the middle of the screen over the Settings
  window. It fires from a delayed dispatch after the wizard window closes, so
  the menu-bar-only app was no longer active and the transient popover failed
  to attach to the status item — the app is now re-activated before the bubble
  is shown so it anchors under the menu-bar icon where the assistant lives.
- Radar scan analytics now count re-raised cards: the `new_cards` field of the
  `radar_scan` event undercounted passes where LLM triage re-raised an
  already-accepted card (kind `"reraised"`, added in v0.20.0) back into
  proposals. Gmail, Slack and Obsidian radars all include it now.

## [0.20.0] - 2026-07-11

Card lifecycle: thread-level matching + an `archived` sealed state + re-raise
of already-accepted cards. A thing you already accepted no longer silently
spawns a duplicate backlog card when related info arrives — it comes back to
your proposals; only after you archive it does later info open a fresh card.

### Added

- **Re-raise (prior acceptance = ownership)**: when new *actionable* info
  matches an un-archived completed (`delivered`/`merged`) card, the original
  card flips back to a proposal (`card_sent`) instead of a new backlog card —
  source folded, `repeated_mentions` bumped, `execution.reraised_at`/
  `reraised_note` stamped, summary appended with "· 新增:…". A hit on the same
  email/Slack thread but a *different* task opens a distinct follow-up child
  (inheriting the thread lineage) without polluting the old card. Both the
  deterministic (`merge_or_new`) and LLM (`apply_triage`/self-DM quick capture)
  paths share `registry.reraise_or_followup`. Pure restatements / `needs_action=
  false` only bump the count — they never flip (Q3: flip on new actionable
  content only). Re-raised proposals carry a `reraised` flag + note in the
  dashboard (app shows an amber "↩︎ Returned" badge) and notify via
  `notify.msg_reraised`.
- **Thread-level matching**: cards gain `thread_id` (grouping anchor, reuses
  the `R-` namespace) and `thread_key` (a strong deterministic bucket from an
  external thread ref only — `gmail:<X-GM-THRID>` / `slack:<thread_ts>`, else
  None, never fuzzy; `registry.derive_thread_key`). `merge_or_new` prefers a
  `thread_key` match, then the legacy title heuristic. The triage/capture LLM
  inventory is capped but HARD-PINS all non-archived delivered/merged cards so
  re-raise recall can't silently fail.
- **`archived` state + `archive`/`unarchive` inbox actions**: seal a completed
  card from 已验收 (`delivered`) or 备选 (`detected`) (Q2). Archived cards
  RELOCATE to `act/registry/archive/` (out of the hot scan, #10), are excluded
  from matching, hidden from the LLM, and NEVER purged; `unarchive` restores the
  prior status and moves the file back. New dashboard partition `archived[]`
  (+ `counts.archived`); archived cards enter no kanban lane.
- **`archive_stale` auto-archive of cold delivered matters (#10) — DEFAULT OFF**
  (`archive_after_days=0`). When enabled it runs at most once per 24h and skips
  cards with a future deadline or a live sibling in their cluster — so a
  long-silent immigration/EB-1A matter is never auto-sealed (which would let new
  mail re-open a duplicate).

### Fixed

- **id-collision data-loss guard**: `next_id()` and `load()` now scan the
  archive subdir, so a freshly allocated id can never overwrite an archived card
  (the highest-risk failure of the relocate model).

### Changed

- **MERGED / delivered card behavior (visible change)**: a restatement carrying
  a new actionable ask on a `delivered`/`merged` card now RE-RAISES the original
  card back to a proposal, rather than silently absorbing it (previously merged
  duplicates just bumped the count). Pure restatements are unchanged (bump only).

## [0.19.2] - 2026-07-11

The first stably-self-signed release: release builds now carry a constant
code-signing identity, so macOS keeps its permission grants across updates.

### Changed

- **Release builds are now signed with a stable self-signed code-signing
  identity, so macOS Screen Recording (and other TCC) permissions persist
  across app updates** instead of re-prompting on every version. A one-time,
  idempotent maintainer script (`mac/scripts/make-signing-cert.sh`) creates a
  free (non-notarized) `Zelin AI Engineer Dev` identity and wires it into CI
  via two secrets (`MACOS_SIGN_CERT_P12`, `MACOS_SIGN_CERT_PASSWORD`); the
  release workflow imports it into a throwaway keychain before building
  (guarded — absent secret ⇒ ad-hoc fallback, still builds) and fails loudly on
  a misconfigured secret. With signing configured the app's Designated
  Requirement stays constant across versions, so the grants no longer reset.
  One-time transition: the first stably-signed update re-prompts for Screen
  Recording once, then never again. Gatekeeper first-open is unchanged —
  self-signed is not notarized.

### Fixed

- **Detect the untrusted self-signed identity correctly** (`mac/build.sh`,
  `.github/workflows/release.yml`, `mac/scripts/make-signing-cert.sh`): the
  `Zelin AI Engineer Dev` cert is self-signed and therefore untrusted
  (`CSSMERR_TP_NOT_TRUSTED`), so `security find-identity -v` (valid/trusted-only)
  would hide it even though it signs fine and yields a stable cert-based
  Designated Requirement. Dropping `-v` from the identity probes makes the build,
  the CI import verification, and the cert script's idempotency guard all detect
  the untrusted-but-usable cert (the guard would otherwise miss an existing cert
  and create a duplicate CN). `make-signing-cert.sh` also supports
  non-interactive setup, using `$KEYCHAIN_PW` for the key partition list instead
  of prompting when it is set.

## [0.19.1] - 2026-07-11

Patch release: a voice-profile cleanup plus two follow-ups from the v0.19.0
review — no new user-visible features.

### Changed

- **Voice-profile default drops the vulgar example**
  (`config/voice-profile.default.md`): the shipped author's-voice layer keeps
  all of its rules and register, but the illustrative Chinese chitchat line no
  longer uses a crude interjection — softened to a clean casual exclamation
  that makes the same point.
- **Usage-insights abandonment table excludes once-per-install milestones**
  (`scripts/insights_report.py`): the "used exactly once" / abandonment view no
  longer counts the milestone / first-reach events (`milestone_first_card`,
  `milestone_first_approval`, `milestone_first_delivery`, `feature_first_reach`)
  that are used exactly once *by construction* and were drowning out the real
  tried-then-dropped signal.
- **De-duplicated the insights `**Totals:**` line**: it was emitted both in the
  main body and again inside the `<details>` appendix — now emitted once, in the
  main body (the no-change gate still greps it via `head -n1`).
- **Hardened the Slack MCP probe** (`act/radar_slack.py`): `_probe_slack_mcp`
  now wraps its imports and `_claude_bin()`/`_runner_env()` arg-eval inside the
  guard too, so any exception (not only `OSError`/`SubprocessError`) degrades to
  "not present / `mcp_not_configured`" instead of escaping into the radar scan.

## [0.19.0] - 2026-07-11

Diagnose-and-fix, then measure: the board now surfaces the ingest paths that
are silently failing with a one-tap fix, and a lifecycle funnel replaces raw
event counts in the usage insights report.

### Added

- **Board diagnostic cards** (`mac/Sources/Diagnostics.swift`): when an ingest
  path you've configured is silently failing, the task board / popover now
  synthesizes a plain-language diagnostic card — one sentence naming the
  problem, one primary button that jumps straight to the fix. Cards only show
  for paths you've actually set up (never noise), are dismissable, and vanish
  on their own once the path recovers. Composed Swift-side from
  `state/radar_health.json`; no new `dashboard.json` partition.
- **Obsidian radar health tracking**: the Obsidian radar now writes an
  `obsidian` entry into `state/radar_health.json` (same shape as gmail/slack),
  but **only** from the cron ingest chain (`AIASSISTANT_CRON=1`) — gated by
  `radar._owns_health()` so a TCC-blocked launchd context or a manual run can
  never stomp cron's good health with a fake-empty vault. Entries carry an
  optional `last_cards` count and a `skip_reason` vocabulary (`disabled` /
  `vault_missing` / `vault_empty` / `no_api_key` / `extract_failed`).
- **Slack `mcp_not_configured` diagnosis** (B4): Slack radar health tells the
  actionable "fallback is on but there's no token and the claude CLI has no
  Slack MCP" case apart from a transient `mcp_failed:` error, via a
  `claude mcp list` pre-check cached in `state/slack_mcp_present.marker`.
- **Lifecycle / activation-funnel telemetry milestones** (metadata only,
  at most one per install; docs/TELEMETRY.md): `feature_first_reach` for
  `app_launch` (first launch) and `ingest_configured` (first ingest source
  live) on the app side; daemon-side `milestone_first_card`,
  `milestone_first_approval`, and `milestone_first_delivery` fired once each
  through a single choke point (`registry.save`, actd approve, executor
  dispatch). Behavior fields only (`req` id / counts) — no card titles, links,
  or summaries — reusing the existing `analytics.content_gate` privacy
  boundary with no schema migration.
- **Rewritten Usage Insights report** (`scripts/insights_report.py`): instead
  of raw event counts it now reports an activation funnel
  (launch → ingest configured → first card → first approval → first delivery),
  reliability, abandonment, and retention (by `client_ts`) — aggregate
  counts / ratios only, device ids never leak, anonymous devices merged across
  all installs.

## [0.18.1] - 2026-07-11

Patch release: bug fixes, one cleanup, and an honesty correction to the config
docs — no new user-visible features.

### Fixed

- **Proposal card action row no longer truncates**: the 存备选 (defer) button
  was being clipped on narrower cards; the action row is restructured into a
  primary/secondary hierarchy so every button stays reachable
  ([`3428413`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/3428413))
- **Gmail radar now goes through the unified triage gate**
  (`act/radar_gmail.py`): it was the only radar still filing every extracted
  item straight into the 提案 lane as a `card_sent` proposal, bypassing the
  shared `quick_capture.triage` / `apply_triage` gate the Slack and Obsidian
  radars use. Gmail candidates now get the same three-way decision — new
  proposal (提案, or 备选 when the ask is real but not urgent), fold into a
  related open card, or ignore pure-FYI mail — gaining ignore / relates_to /
  improvement_of lineage. The existing UID-marker dedup is untouched.

### Removed

- **Retired the redundant Obsidian radar launchd agent**
  (`act/launchd/com.zelin.aiassistant.radar.plist`): it was TCC-blocked from
  `~/Documents` and only ever saw an empty vault. The Obsidian radar already
  runs from the crontab ingest chain (`python3 -m act.radar --once`, every
  30 min); `install.sh` now unloads and removes any previously-installed copy
  on upgrade, and the next-steps output + `docs/PRIVACY.md` no longer reference
  the launchd agent.

### Changed

- **Honest `watch_people` docs** (`config.example.yaml`): the comment used to
  promise these people's messages/meetings "trigger extraction", but no radar
  reads the list to filter what gets extracted — it only derives the
  tracked-requester display name (first entry, `config.requester_display`) and
  drives the Settings people picker. The comment now says so plainly and notes
  it is **not** a hard filter, so no one's messages are dropped for being off
  the list.

## [0.18.0] - 2026-07-11

Board redesign, a defer verdict, and — the big one — richer, honest-by-default
telemetry.

### Added

- **「存备选」— defer a proposal to the backlog** (`docs/CONTRACT.md` §10):
  a fourth on-card button sends a proposal to the backlog instead of
  approving or rejecting it; unlike reject (which trashes and kills
  dedup-matching), defer keeps the card matchable so the radar can merge
  future mentions. One click, undo via the backlog's 研究并提议
  ([#34](https://github.com/Wan-ZL/zelin-ai-assistant/pull/34))
- **Lane definitions**: every board lane header gets a `?` icon — click for
  a popover explaining what the lane means, hover for a tooltip — plus
  empty-state copy that teaches the lane when it has no cards
  ([#33](https://github.com/Wan-ZL/zelin-ai-assistant/pull/33))
- **Richer behavior telemetry (metadata only, default-on)**: new events
  `mw_section_dwell` (per-page dwell), `mw_setting_change` (which settings
  key changed — never the value), `board_search` (query length only),
  `feature_first_reach` (once-per-install feature reach); new metadata
  fields `dispatch.wait_s`, `review_promoted.exec_s`, `rework_launch.round`,
  `radar_scan.secs`, comment/typed-length counters. Full list in
  docs/TELEMETRY.md.
- **`telemetry.capture_input` (default ON, together with the new default
  `level: detailed`)**: telemetry now includes the text you type into the
  app — captures, Ask questions, card comments / rework feedback, board
  search terms — each clipped to 500 chars. The first-run disclosure and all
  docs say so plainly; a dedicated Settings toggle ("上传我输入的文本 /
  Upload the text I type") turns just the text off while keeping anonymous
  behavior stats. Hard scope boundary at any setting: never the AI's
  answers, screen-recording content, email or Slack/iMessage message bodies,
  or secrets — radar-extracted third-party content never enters telemetry.
  The double gate (capture_input AND detailed) is enforced emit-side in both
  Python and Swift and locked by tests (tests/test_telemetry_level.py,
  including an honesty drift-guard on the disclosure copy).
- Capacity budget section in docs/TELEMETRY.md (Supabase free-tier headroom
  + archival guidance).
- Adversarial-review hardening of the content pipeline: dispatch.instruction
  is provenance-gated (user-capture-origin cards only, title only — radar
  cards summarizing third-party mail/messages/screen send no instruction at
  all); a v2 consent marker gates content for upgraded installs (behavior
  telemetry keeps the old marker, typed text waits for the new disclosure
  to render or an explicit capture_input); every content field passes an
  unconditional secret masker (mirrored in Swift, drift-guarded) before
  hitting the local log; media quick-captures record only the typed words,
  never the synthetic image prompt or local file paths.

### Changed

- **Board lane order is now backlog-first**: 备选 | 提案 | 运行中 | 待验收 |
  已验收 (the backlog pool sits upstream, left of proposals, with a quieted
  header so proposals still draw the eye), and the 已验收 lane's English
  label is now "Done"
  ([#33](https://github.com/Wan-ZL/zelin-ai-assistant/pull/33))
- **`level: detailed` no longer attaches any content by itself** (previously
  ≤200-char instruction/delivery/question summaries) — content is controlled
  by the separate `capture_input` switch; level only sets behavior-event
  granularity (and basic also switches text capture off).
- First-run telemetry consent is now a one-line honest disclosure — it
  states that typed text is included by default — with a "Details & opt-out
  in Settings" link (the toggles live in Settings → Product improvement
  program, same override key; the `telemetry_consent` event retired with
  the old checkbox).

### Fixed

- Attaching to a review-lane card's session (the v0.17.1 double-click
  `claude attach`) is no longer misreported as a rework round: the card now
  stays in the review lane with a calm "会话有新活动 / Session active" badge
  (new optional `review[].session_active` field, CONTRACT §30) instead of
  jumping to the running lane as「验收后返工中」— no 打回 verdict ever
  happened. Genuine rework rounds are untouched, and the periodic re-harvest
  of deliverables from attach conversations is kept

## [0.17.1] - 2026-07-11

### Fixed

- Double-click terminal launch opens a new **tab** of the existing Ghostty
  window instead of a separate window, and `--install` swaps the running
  app seamlessly
  ([#32](https://github.com/Wan-ZL/zelin-ai-assistant/pull/32))
- Clicking outside a text field (board search, proposals composer, Ask,
  popover capture…) or pressing Esc now dismisses the caret — drafts are
  never lost, and Esc on a non-empty search clears the filter first
  ([`e790f7a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e790f7a))

### Changed

- CONTRIBUTING/CHANGELOG now codify the versioning rule: patch = fixes and
  small UX corrections, minor = new user-visible features; merging a PR does
  not build an installer — cutting a release does
  ([`789e7c6`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/789e7c6))

## [0.17.0] - 2026-07-10

### Added

- **Unified radar triage gate** — every radar candidate (Slack native, Slack
  MCP sweep, Obsidian notes, self-DM quick capture) now passes one three-way
  gate before filing: act-now proposal, lineage follow-up on a
  delivered/merged ancestor (one per cluster, deduped across passes and
  sources), fold-into-open-card note, backlog demotion for real-but-not-urgent
  items, or ignore for pure-FYI. The proposals lane now strictly means "needs
  the owner's action or decision now"
  ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))
- **Voice-first default** — the author's sanitized voice profile ships as the
  repo default for any text drafted in the owner's name, plus a Settings
  「语气档案 / Voice profile」 group: live status row, master switch
  (`voice.enabled`), open-profile button, and one-click generation of your own
  private profile from your sent Slack messages (`python -m act.voice_gen`;
  read-only MCP tools, automatic backup, never overwrites on failure)
  ([`6cac752`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6cac752))
- **Board search (⌘F)** — local keyword filter across all lanes; the 备选 lane
  is now labelled **备选 · Backlog**
  ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))
- **Card feedback channel** — pick cards on the board, describe what looked
  wrong, and the report is saved under `state/feedback/` (and uploaded to your
  own Supabase when configured — see `docs/PRIVACY.md`)
  ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))

### Fixed

- **Session binding** — cards only carry a `claude --resume` command when the
  transcript really belongs to them: sessionId is validated against the
  transcript itself, and an empty session id no longer globs the whole
  transcript dir and grabs the alphabetically-first session. Double-clicking a
  card's terminal command now bootstraps PATH so `claude` resolves under a
  fresh shell ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))
- The shipped `R-000-example.yaml` is documentation and never loads as a real
  card (it used to surface in the backlog lane on every fresh install)
  ([`6cac752`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6cac752))

## [0.16.0] - 2026-07-10

### Added

- **Always-visible update row** in About: current version, an honest status
  line ("已是最新（上次检查：X 分钟前）", failure and disabled states
  included), and a **立即检查 / Check now** button that bypasses the daily
  budget (`python3 -m act.lib.update_check --force`); the privacy switch
  still wins — when auto-check is off, the button never fires a request
  ([`0ff44a8`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/0ff44a8))
- **Contributors wall** in both READMEs with a good-first-issue pointer —
  celebrating the project's first external contributor
  ([`a59c7ba`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/a59c7ba))

## [0.15.0] - 2026-07-10

Voice, speed, and a milestone: the first external contribution.

### Added

- **Voice profile two-level fallback** ([docs/VOICE.md](docs/VOICE.md)):
  drafts written in the owner's name follow `state/voice-profile.md`
  (private, gitignored) when present, else the neutral
  anti-assistant-register starter template that now ships at
  `config/voice-profile.default.md`; the template is nobody's voice
  (empty example buckets, fingerprint-guard test) and the prompt injection
  no longer hardcodes a personal name
  ([`7329157`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/7329157))
- **Double-click to run in your terminal**: a card's copyable
  `claude attach` / `--resume` command now runs on double-click in a new
  window of your terminal of choice (Ghostty via its AppleScript
  dictionary, Terminal, iTerm2 when installed — pick in Settings →
  General); single click still copies. First run asks for the standard
  macOS Automation consent
  ([`a2c99ac`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/a2c99ac))

### Fixed

- `dispatch_failed` analytics event now fires exactly once per launch
  failure (and backoff-window passes no longer emit noise events);
  unexpected crashes are tagged `dispatch_crashed` — the project's first
  external contribution, thanks @tapheret2!
  ([#24](https://github.com/Wan-ZL/zelin-ai-assistant/pull/24), closes
  [#12](https://github.com/Wan-ZL/zelin-ai-assistant/issues/12))

## [0.14.0] - 2026-07-10

The novice-friendliness release: a guided setup wizard, one-click repair on
every failure, in-app Q&A, fully in-app Slack/Gmail/iMessage setup, and a
`.pkg` that ends with a *live* product — plus the first self-improvement
loops (weekly digest, daily usage insights) and Windows/Linux porting
groundwork. Standard: the happy path never requires YAML, Terminal, or docs.

### Added

- Six-step first-run **setup wizard**: language, AI-engine detection with
  paste-and-verify API key, permissions, screen-only recording consent,
  Obsidian vault picker (reads the Obsidian registry), and a live
  health-check finale with fix buttons and a menu-bar "I live here" bubble;
  re-runnable anytime from Settings
  ([`128d400`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/128d400))
- **AI Doctor** (`docs/CONTRACT.md` §25): a failure-classification catalog
  shared by python and the app, one-click in-app repair buttons on every
  banner that used to print raw `launchctl` commands, auto-run diagnostics,
  a real cron Full-Disk-Access probe, and a **Fix with AI** button that opens
  Terminal on an interactive claude session pre-loaded with a scrubbed
  diagnostic bundle
  ([`0527f4d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/0527f4d))
- **In-app Q&A "问问助手 / Ask"** (§27): ask anything about the product;
  answers are grounded in the docs and this Mac's real state via one
  tool-less headless claude call, with history, feedback, and honest
  disclosure of what is sent where
  ([`d522624`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d522624))
- **Claude Code session import** (§22): scan recent local sessions, preview
  with waiting-on-you badges, and import selected work as proposal cards —
  no more empty board on day one
  ([`c52bcc4`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c52bcc4))
- **In-app update check** (§26): daily ETag-cached GitHub releases query, a
  low-key menu line and About-page download row, Settings toggle
  ([`0154430`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/0154430))
- **Slack & Gmail fully in-app** (Settings): copy-manifest button, token
  paste verified via `auth.test` with identity autofill, channel and people
  pickers; Gmail guided app-password card with in-UI address field —
  `config.yaml` is gone from both happy paths
  ([`202d1f5`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/202d1f5))
- **Weekly digest** (§24): a "what you worked on this week" recap card plus
  2-3 automation-suggestion proposals mined from the week's ingest
  ([`2193ced`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/2193ced))
- **Usage-insights loop**: a GitHub Action aggregates telemetry into one
  pinned issue (aggregates only), with optional Claude analysis; runs daily
  and skips no-change days
  ([`82c79e9`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/82c79e9),
  [`aee705c`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/aee705c))
- **Install lifecycle**: the `.pkg` now ends with a *live* product — launchd
  agents loaded, the app launched, `state/install_report.json` written
  (§23); launch-at-login defaults on; a real `uninstall.sh` (with
  `--dry-run` and `--purge`) plus an About-page uninstall entry; the release
  notes spell out the unsigned-pkg right-click-Open steps
  ([`785979b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/785979b),
  [`e02cd1f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e02cd1f),
  [`501adc5`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/501adc5),
  [`ac539d1`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ac539d1))
- **Recording robustness**: the engine restarts itself the moment Screen
  Recording permission lands, engine death is diagnosed in plain language
  (including the "npm is downloading screenpipe, 1-3 minutes" first-run
  state), and a lost TCC grant after a macOS update is detected with a calm
  re-grant flow
  ([`a6a3b06`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/a6a3b06))
- **Windows/Linux porting groundwork**: an OS seam for service control and
  notifications, `docs/PORTING.md` with a component-by-component map, an
  ubuntu CI lane keeping the core genuinely portable, and a README platform
  matrix
  ([`f4c346d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f4c346d),
  [`f31cb2b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f31cb2b))

### Changed

- Kanban lane renamed: 待审批 → **提案** / "Needs approval" → "Proposals"
  ([`6dfe56f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6dfe56f))
- Settings now **persist on change** with diff-writes (unrelated saves can no
  longer clobber `config.yaml` values), credentials are **verified on save**
  (Slack `auth.test`, Gmail IMAP probe, spaces stripped), and the task
  working folder has a picker with auto-create instead of a dead placeholder
  ([`6dfe56f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6dfe56f))
- One **"Obsidian Vault 位置"** field replaces the four pipeline-directory
  fields (they derive automatically; `config.yaml` overrides remain for
  experts), and the global-hotkey Settings group is gone
  ([`fa92120`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/fa92120))
- System notifications now display under the **"Zelin's AI Assistant"
  identity** (§28): relayed through `state/notify_queue/` and posted by the
  app; the osascript / Script Editor path is gone entirely, so native
  notifications require the running app (it auto-starts at login; phone
  mirrors unaffected); bursts cap at 5 with a "+N more" summary, backlog
  older than 10 minutes is dropped, clicking opens the main window
  ([`591705f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/591705f),
  [`76bae6f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/76bae6f))
- Every headless-claude call site resolves the **claude binary the login
  shell uses** (install-time PATH pinning, runtime `execution.claude_bin`
  pin, doctor check for duplicate/outdated installations, and a classified
  plain-language dispatch-failure reason) — fixes dispatches failing with
  `unknown option '--bg'` when an old npm-global claude shadowed the real one
  ([`997485c`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/997485c))
- Telemetry quality: the version stamp is applied at the **writer level** on
  both sides, action events carry ok/fail outcomes, and a `merge_apply`
  outcome event makes failed merge applies visible
  ([`dd7ca03`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/dd7ca03));
  consent and collection-level copy now state exactly what is sent where,
  and the privacy egress inventory covers every channel including the weekly
  digest, Ask, and Fix-with-AI
  ([`151661a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/151661a))

### Fixed

- Meeting action-items **backfill storm**: an unconfigured install could
  back-process months of historical notes in one evening into a placeholder
  directory with one notification each; fixed with a placeholder-path guard,
  notification coalescing, and a whole-pass radar reentry lock
  ([`aa8e8d1`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/aa8e8d1),
  [`026d83f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/026d83f))
- Linux CI lane healed: shellcheck SC2086, uninstall dry-run portability,
  swiftc type-check timeout
  ([`93de626`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/93de626))

### Removed

- The **manager pack** meeting action-items feature: unconfigurable in
  practice (a placeholder degenerated into matching nearly every note) and
  too narrow to be universal; the concept returns as a per-person
  commitments ledger
  ([#23](https://github.com/Wan-ZL/zelin-ai-assistant/issues/23)).
  `features.manager_pack` is now ignored
  ([`b26f188`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b26f188),
  [`e7a5816`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e7a5816))

## [0.13.0] - 2026-07-09

Card workflow power-ups (merge review, done-outside, voice profiles) plus the
first big novice-friendliness wave: a first-run permissions page, screen-only
recording by default, fully in-app iMessage setup — and anonymous usage
telemetry that now defaults to on behind an explicit first-run consent surface.

### Added

- Multi-select merge review: select two or more cards, an AI pass suggests
  merge / link-improvement / keep-separate / close-secondary with reasoning,
  and the human verdict is applied deterministically; new terminal `merged`
  registry state that still absorbs restatements (`docs/CONTRACT.md` §21)
  ([`5e00555`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5e00555))
- "Done outside" exit for approved and executing tasks: harvest the
  transcript best-effort, stop the lingering session, deliver — no more cards
  stranded behind a blocked agent
  ([`892da54`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/892da54))
- Optional voice profile: when `state/voice-profile.md` exists, dispatched
  agents are told to match the owner's writing style for drafts in their name
  — and to treat the file strictly as style guidance
  ([`b321061`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b321061),
  [`e922df2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e922df2))
- First-run permissions & setup page: live status rows for Screen Recording,
  Notifications, and Full Disk Access (marked iPhone-channel-only) with
  one-click grants and plain-language explanations; reopenable anytime from
  the App menu, the status-item menu, or Settings
  ([`ba6d58d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ba6d58d))
- Fully in-app iMessage (iPhone) setup in Settings: an enable toggle that
  writes config and loads/unloads the launchd radar, handle validation, live
  health rows with plain-language skip reasons, guided Full Disk Access steps
  with a copyable python path, and one-click test rounds / test messages
  ([`d6eebbd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d6eebbd))
- Anonymous usage telemetry with `basic` / `detailed` collection levels
  (`detailed` is opt-in and adds short instruction summaries), a Settings
  "product improvement program" section, and an anon INSERT-only Supabase
  policy so the shipped key can write but never read
  ([`e24e5cd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e24e5cd))
- Supabase keepalive workflow so the maintainer's free-tier telemetry project
  is never paused for inactivity
  ([`5f4738e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5f4738e))

### Changed

- **Recording defaults to screen-only.** First run asks a single on/off
  consent; audio capture moved to an explicit opt-in in Settings
  ([`ba6d58d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ba6d58d))
- **Telemetry now defaults to on** — behind a consent door: nothing uploads
  until a consent surface has been shown (or telemetry is explicitly
  configured), and opting out is one click on first run or in Settings; see
  `docs/TELEMETRY.md`
  ([`e24e5cd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e24e5cd),
  [`5854726`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5854726))

### Fixed

- Merge review persists the primary card's absorbed data before marking each
  secondary as merged, so a mid-merge crash can no longer lose it
  ([`d32d491`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d32d491))
- Repaired CHANGELOG prose corrupted by the history-rewrite text replacement,
  including the inverted `create_github_repo` migration note
  ([`13b64a9`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/13b64a9))

### Removed

- Dependabot version-update PRs; actions stay SHA-pinned and manually reviewed
  ([`5f4738e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5f4738e))

## [0.12.0] - 2026-07-09

The P0 + P1 waves of the open-source readiness review: make a fresh install
work on a clean Mac, default to privacy-safe behavior, give first-time visitors
English docs plus privacy/security policies, and harden the pipeline
(security fencing, diagnostics, state-machine test coverage, an iMessage phone
channel, and sensitive-app capture exclusion).

### Added

- iMessage phone channel: approve/reject/rework/accept cards, quick capture,
  and 👍-tapback approvals from the iMessage "message yourself" thread
  (`phone_channel: imessage`); Slack remains available; see
  `docs/IMESSAGE_SETUP.md`
  ([`fec102f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/fec102f))
- Sensitive-app screen-capture exclusion (`recording.ignored_apps`): password
  managers, Keychain Access, and private-browsing windows are excluded at the
  engine level by default, with a matching SQL filter on export for frames
  recorded earlier
  ([`1b3fc29`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1b3fc29))
- `python3 -m act.doctor` (also `install.sh --check`): 14 post-install
  diagnostics with symptom-first output and per-check fixes; an "auth model"
  section in `docs/INSTALL.md` explains API key vs subscription auth
  ([`b693541`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b693541))
- Pipeline health banner in the app distinguishing slow vs broken (stale/dead
  tiers with recovery actions), first-launch dependencies walkthrough, TCC
  status rows, and instant Anthropic-key validation in Settings
  ([`9adb246`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/9adb246))
- CI: shellcheck + ruff lint gates and a Python 3.9 floor job; releases now
  ship SHA-256 checksums and build-provenance attestations; third-party actions
  pinned to commit SHAs with Dependabot updates
  ([`848aaf6`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/848aaf6))
- 94 new state-machine and radar tests (reconcile/resume/transitions/registry
  merge), plus a shared agent-state vocabulary module ending the
  actd/dashboard drift
  ([`825eb30`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/825eb30))
- Community files: contributor quickstart without the full stack, issue forms,
  PR template, Code of Conduct, and a plain-language license FAQ
  ([`8a5b505`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8a5b505))
- Mermaid architecture diagram with the trust boundary in both READMEs,
  English orientation headers for HANDOFF/CONTRACT, and `docs/ROADMAP.md`
  ([`9cc1a28`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/9cc1a28))
- `docs/PRIVACY.md`: full data-egress inventory — every channel that sends data
  off the machine (ingest cron chain, radars, quick capture, executor, telemetry)
  with trigger, frequency, payload, and off-switch, plus local retention and an
  execution-permissions section explaining `--dangerously-skip-permissions` and
  the `execution.skip_permissions` config
  ([`1a6e45b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1a6e45b))
- `SECURITY.md`: supported versions, private vulnerability reporting via GitHub
  advisories, response window, and explicit scope
  ([`1a6e45b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1a6e45b))
- First-launch recording consent: a fresh install no longer auto-starts screen
  capture; a one-time bilingual sheet explains what is captured and where it
  goes, with Screen Only / Screen + Audio / Not Now choices. Existing installs
  keep their stored mode
  ([`8949f7e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8949f7e))
- English `README.md` (badges, how-it-works, quickstart, features, license
  summary) with the Chinese original moved to `README.zh-CN.md`; hero
  screenshots and demo video under `docs/assets/`
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083),
  [`2399e3b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/2399e3b))
- `docs/INSTALL.md` as the single authoritative install guide: prerequisites
  table with versions, numbered steps with expected-state checkpoints, exact TCC
  grant paths, and a "first card in 5 minutes" path; pitfalls collected into a
  symptom-first `docs/TROUBLESHOOTING.md`
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083))
- `.github/release.yml` release-notes category template
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083))

### Changed

- **BREAKING**: approving a card no longer auto-creates a private GitHub repo
  for new targets — `execution.create_github_repo` now defaults to `false`;
  set it to `true` explicitly to restore the old behavior
  ([`f5feeb2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f5feeb2))
- The dashboard's `completed` list is capped at the 50 most recent items
  (counts stay exact); resolving credentials through the legacy
  `~/Desktop/Keys/` path now logs a deprecation warning
  ([`f5feeb2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f5feeb2))
- Kanban/popover task rows take an explicit lane (retiring the accent-color
  hack), error messages are copyable with full text, and timeout notices
  appear in the lane where the action happened
  ([`00b3e2c`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/00b3e2c))

- UI language now defaults to the system locale (`zh-*` → Chinese, otherwise
  English) instead of hardcoded Chinese; an explicit language override still
  wins ([`8949f7e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8949f7e))
- The app's Dependencies page checks Node/npx and the recording engine
  (npx-pinned canonical path) instead of looking for a Screenpipe.app that the
  pipeline never uses
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- `PUBLISHING.md` slimmed into provenance-only `docs/SANITIZATION.md`
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083))

### Fixed

- Obsidian radar prompts now pass through the same redaction scrub as every
  other outbound channel, and all radar/executor prompts fence untrusted
  source material as data-not-instructions
  ([`1e99ce6`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1e99ce6))
- The Obsidian radar marker is now a watermark: a note that fails extraction
  no longer silently advances the marker (the failure class behind the
  2026-07-08 incident); recovery rescans are idempotent
  ([`825eb30`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/825eb30))
- Ingest scripts resolve the Obsidian vault path through the config layer
  instead of a hardcoded location, with a cron-safe fallback
  ([`3afae87`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/3afae87))
- launchd plists render real paths at install time instead of shipping
  placeholders (fresh installs used to silently never start the daemon), and
  `install.sh` verifies each agent actually spawned
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- Fresh installs actually start: `install.sh` renders the launchd plist
  placeholders (python path, repo root, log paths) before loading and verifies
  each agent really spawned, instead of copying template plists verbatim and
  failing silently
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- Clones outside `~/Projects/zelin-ai-assistant` no longer split-brain against
  the GUI app: `install.sh` persists the repo root to a pointer file the app
  resolves (env var → pointer → legacy default)
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- `install.sh` detects the Node/npx hard dependency of the capture engine
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))

### Security

- Built-in secret-pattern masking (`sk-ant-`/`xox*`/`AKIA`/`gh*_`/PEM) is now
  **default-on** and controlled by its own `redaction_mask_secrets` switch,
  independent of the opt-in user term redaction. Previously `scrub()` returned
  early when `redaction_enabled` was false (the default), so on-screen API keys
  could leave the machine inside outbound prompts
  ([`2a84adf`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/2a84adf))

## [0.11.0] - 2026-07-09

### Added

- Opt-in Supabase telemetry sync: batched uploader tails
  `state/analytics/events.jsonl` and POSTs to a user-owned Supabase project;
  default off, local JSONL stays the source of truth (`docs/TELEMETRY.md`)
  ([`c896a84`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c896a84))
- macOS `.pkg` installer for the full suite: app to `/Applications` plus a
  pipeline master copy with a postinstall that syncs it into the user's home
  and runs `install.sh --pkg-postinstall`; built by `mac/package.sh` and
  published as a release asset
  ([`6046f68`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6046f68))
- Demo-data seeder `scripts/demo_seed.py`: fully fictional `dashboard.json`
  covering all card types and scenes for screenshots and demo video, no real
  data or API key needed (`docs/DEMO.md`)
  ([`e1f42ea`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e1f42ea))

### Fixed

- Ingest falls back to claude CLI credentials when no API key file exists
  ([`72128fe`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/72128fe))
- CI runs on `macos-latest` with the newest installed Xcode selected
  ([`d8e9480`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8e9480))

## [0.10.3] - 2026-07-09

Initial public snapshot: sanitized export of the personal AI assistant — the
ingest pipeline (screenpipe → headless claude → Obsidian), the act pipeline
(radars → registry → approval cards → autonomous execution → review), and the
SwiftUI menu-bar app — plus the FSL-1.1-MIT license, `CONTRIBUTING.md`, CI and
release workflows
([`ef421de`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ef421de)).

[Unreleased]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.8...HEAD
[0.48.8]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.7...v0.48.8
[0.48.7]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.6...v0.48.7
[0.48.6]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.5...v0.48.6
[0.48.5]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.4...v0.48.5
[0.48.4]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.3...v0.48.4
[0.48.3]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.2...v0.48.3
[0.48.2]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.1...v0.48.2
[0.48.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.0...v0.48.1
[0.48.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.47.0...v0.48.0
[0.47.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.46.1...v0.47.0
[0.46.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.46.0...v0.46.1
[0.46.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.45.0...v0.46.0
[0.45.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.44.0...v0.45.0
[0.44.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.43.2...v0.44.0
[0.43.2]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.43.1...v0.43.2
[0.43.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.43.0...v0.43.1
[0.43.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.42.0...v0.43.0
[0.42.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.41.0...v0.42.0
[0.41.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.40.0...v0.41.0
[0.40.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.39.0...v0.40.0
[0.39.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.38.0...v0.39.0
[0.38.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.37.1...v0.38.0
[0.37.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.37.0...v0.37.1
[0.37.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.36.0...v0.37.0
[0.36.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.33.1...v0.36.0
[0.35.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.33.1...v0.35.0
[0.34.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.33.1...v0.34.0
[0.33.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.33.0...v0.33.1
[0.33.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.31.1...v0.32.0
[0.31.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.31.0...v0.31.1
[0.31.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.28.1...v0.29.0
[0.28.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.28.0...v0.28.1
[0.28.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.20.1...v0.21.0
[0.20.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.19.2...v0.20.0
[0.19.2]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.19.1...v0.19.2
[0.19.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.19.0...v0.19.1
[0.19.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.18.1...v0.19.0
[0.18.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.17.1...v0.18.0
[0.17.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.10.3...v0.11.0
[0.10.3]: https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.10.3
