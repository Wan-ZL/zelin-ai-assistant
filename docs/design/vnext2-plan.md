# v-next-2 总设计与需求记录 ——「皇上/臣子」模式

Status: **active plan (ratified by owner 2026-09-01)**. This document is the single memory anchor for the multi-day/multi-week execution that follows the 2026-08-31 → 2026-09-01 brainstorm. Every session or agent working on this round MUST read it first; AI context windows get compressed, this file does not. It records (1) the decisions the owner locked, in his own words; (2) the target end-state as **abstract functional requirements** (what must be true, not how to code it); (3) explicit non-goals; (4) execution order with safety ordering and done-criteria; (5) the owner's verbatim conversation record. Companion research (read-only, outside the repo): `~/Downloads/brainstorm/` (r1–r6, REQUIREMENTS-DRAFT.md, s1–s5, AUDIT-ISSUES-LOGS-SUGGESTIONS.md), `~/Downloads/repo-review/UNCLEBOB-ADOPTION.md`, `~/Downloads/dashi-research/V048-AUDIT.md`.

Governing law: `docs/CONTRACT.md` (§0 constitution + numbered sections; truth for the last number = the final `## N.` heading in that file). Behavior changes below require law changes in the same PR (repo rule). New §§ take the next free number, never reuse one. Constitution articles touched by this round are listed per phase; each is an explicit amendment, never a silent one.

---

## 0. 一句话

让 AI 每天自己看日志、看 issue、看素材库,自己提改进、自己实现、自己开草稿 PR,并把 CI 修绿;人只看绿色的 PR 并决定要不要。**红的是臣子自己的事,皇上只看绿的。** 在此之上:数据层换 SQLite,客户端换成 web 看板 + Dock 套壳并退役原生菜单栏 app,整个仓库装上 Uncle Bob 式的确定性质量仪表并作为合并硬门。

## 1. 决策台账(owner 拍板,按时间)

| # | 决定 | Owner 原话(节选) | 日期 |
|---|---|---|---|
| D1 | **保持 push 派工,不改成 agent 自己认领(pull)** | 「你觉得我们要不要也改成 agent 自己去待办列捞任务?」→ 采纳我的判断:体感无差别,代码上 pull 更差(认领/租约/心跳协议、dashi #184 类 bug) | 09-01 |
| D2 | **数据层最终基于 SQLite(store2 接线),YAML 降为每日导出/备份** | 「我想知道最终版本应该是要什么样的?是基于 SQL 对吗?」「切换时机:等 QA 网配好之后,现在就可以切。备份与回滚:在电脑上留个备份,如果切换失败,手动导回去。先把数据库弄好,再进行 QA 的测试,相当于做最终版本测试。」 | 09-01 |
| D3 | **退役原生 Mac 菜单栏 app;产品 = web 看板 + shell(Dock-only,无菜单栏图标)** | 「起码在视觉上我希望把它去掉。录制状态和字幕开关我一般不用这个入口,直接打开主软件在右上角操作。录屏和录音 Mac 默认就能显示是否在使用。合适的选项应该是 A。」「那个老的 APP 就改名就好,然后留到新的版本。等弄完之后,我明确让你把老的删掉,你再删掉。」「我确实不希望你在即将被删的代码上做变异测试,那确实是白干。」 | 09-01 |
| D4 | **QA 仪表全上,老代码新代码都达标,冲最终完整版;CRAP 上限 6** | 「我希望变异测试、CRAP/复杂度、覆盖率、依赖方向检查接进 CI,今天做最完美的推进,老代码新代码都弄好。反正你是 AI,时间等得起。」「我觉得按 6 来可以。」 | 09-01 |
| D5 | **CI 设计按我的方案**:全套快测试 + 复杂度 + 依赖方向 + 覆盖率不下降 = 必须绿;变异测试每晚自动、永不拦 PR;AI 的结构化测试报告是加分项不是通行证 | 「CI 的设计就按照你的来。」「我永远只看到绿色的,红的是臣子自己的事。」「AI 在 PR 里面描述结构化的测试报告,是加分项没错,但不是硬通过的通行证。」「我能接受 mutation test……这个太慢了」→ 改为夜间自动 | 09-01 |
| D6 | **main 分支保护:只走 PR,禁止直接 push,CI 绿才能合并;不要求他人审批** | 「不能直接 push 到 main,还是要走 PR。」「绿了才合并,我觉得是可以的。因为 AI 还是可能多走一步,自己提了 PR 然后自己把它通过了,也没人挡得了。」 | 09-01 |
| D7 | **自动 PR 通道(原代号 F 车道)只开给 zelin-ai-assistant 本仓库;不改成"所有 proposal 都走通道"** | 「当前这个项目肯定是走车道的……我原本想的是所有 proposal 卡片都由 AI 来处理……但这样的话设计就要从头来。先只给泽林 AI assistant 这一个软件弄车道吧。」 | 09-01 |
| D8 | **agent 开 PR 用 owner 自己的 GitHub 账号,不用 elenvo 账号** | 「Agent 使用的身份是我自己的 GitHub 个人账户,不要使用 elenvo 的账户。」 | 09-01 |
| D9 | **取消一切预算**:新通道不设预算,现有手打卡自动派工的 $5/天也取消 | 「自动派工作,要不先不要搞预算。」「把现有的手打卡自动派工每天 5 块钱的预算也取消吧。目前还没有遇到预算的问题,钱是足够的。」 | 09-01 |
| D10 | **每日自我改进循环**:每天固定时段;新开提案卡 ≤5/天(设置里可改,默认 5);去重范围 = 提案列 + 潜在任务列(running / 待验收不碰);过时卡 → 回收站(可恢复);同主题多卡 → 合成 1 张新卡,旧卡全进回收站;整理时 UI 有提示 | 「每天最多不要超过 5 个……在设置里面允许别人修改,但默认是 5 条。」「Running 就不要去重,毕竟它在跑,但是像潜在任务和提案这里面的都是没有处理的。去重啊,包括过时了的卡片去掉。」「如果合并卡片的话直接合并……把这三四张全部去掉,直接提供一张新的卡片。去掉的老卡片直接丢进回收站。」「可以在 UI 上显示一下……至于最后要不要提醒一声,你作为设计师来判断吧。」→ 设计判断:不弹通知,看板顶部留一行「今日整理:合并 N、清理 M(可撤销)」 | 09-01 |
| D11 | **素材库(原 D 组)不铸卡**:设置里一个文本输入入口(链接 + 备注),与日志同存;一个弹出小窗只显示尚未实现/尚未开 PR 的条目;每日循环消费它;不走 Slack 私信 | 「我看过的内容还是不要做卡片,可以弄一个方案、一个窗口或者一个链接。遇到好的东西我就往里面扔。」「放到软件的设置里面,不需要通过 Slack 私信,直接在软件设置里加一个类似素材库的入口,文本输入的形式。」「弄一个简易的窗口……只显示还没有 implement、还没有提 PR 的内容。所有已经提了 PR 的就把它去掉。每天在某个时间段自动扫一遍所有 log、issue 以及素材库,一旦处理了就从记录里去掉。」 | 09-01 |
| D12 | **人机互动模型 = PR 评论驱动**:owner 在 PR 上留一句(如 "Where is the test?"),每日循环捕捉,由(可能是另一个)会话补做并更新 PR | 「如果我留了一个 comment 'Where is the test?',AI 就知道这个东西需要做 test 了。这可能是另一个不同的 claude code session,没关系。这是一个比较理想的人和 AI 互动的状态。」「臣子把东西做出来了,皇上能挑你的那就是你的大幸;如果没挑,token 浪费这种东西其实不需要考虑。」 | 09-01 |
| D13 | **skill 商店塞进本仓库**(`skills/` + 启用=软链接到 `~/.claude/skills/` + 版本字段;区分默认与自定义) | 「skill 商店 塞进本仓库」 | 09-01 |
| D14 | **测试 skill**:运行后多选要跑的测试种类并并行执行、出一份报告;owner 手动用,agent 收工前也自动用 | 「运行这个 skill 之后,它可以问我需要做哪些 test……让我多选之后,再进行大量的测试。」 | 08-31 |
| D15 | **完整性规则**(已入 memory `push-to-complete-version`):目标永远是最终完整版,分阶段只为安全不为省事 | 「尽量往最完美的去推进。你可以把这个记到你的 rule 中去。」 | 09-01 |
| D16 | **本文档的存在理由** | 「为了防止你中途忘掉……把我们前面聊的这么多内容都写进你要修改的文档里。记录抽象功能:不用写具体代码怎么改,但要写清楚需要达到什么功能。记录沟通内容:把我们的对话记录也添加进文档。」 | 09-01 |
| D17 | **自动部署 = 方案 A:merge 即自动 release + 自动部署到本机,doctor 闸门失败自动回滚** | owner 在 v-next-2 ratify 轮拍板:合并进 main 的每个 PR 自动打 tag、出 release、部署到 live 机器;部署后跑 doctor,红 → 回滚到上一版。「皇上只看绿的 PR」——部署也不该是手动步骤。 | 09-01 |
| D18 | **他人提的 GitHub issue:只做摘要,owner 说「do it」才动** | 外来 issue(非 owner 账号)进每日循环时只生成一条摘要(建议处置 + 理由),不铸提案卡、不开 PR;owner 在摘要上回一句「do it」才进入实现通道。 | 09-01 |
| D19 | **digest 卡默认 OFF,加频率旋钮** | 周一 digest 的 automation-idea 卡 0/15 被批准、3 个 cluster 跨 4 周重铸(审计 L7):默认关闭铸卡;报告卡保留但受 `digest.frequency` 旋钮控制(off / weekly / …),设置里可改。 | 09-01 |
| D20 | **派发失败事故(claude TCC-blind)+ 派发风暴刹车 + 心跳看门狗**(PR-A,#89) | (设计判断,非 owner 原话)2026-08-31 live:launchd 起的 actd 每次 `claude --bg` 都以「possibly due to low max file descriptors」拒启,R-175 13 小时重派 66 次、954 条 traceback、98% 的 registry 写入;22:31 起 actd 静默卡死 2.5 小时,唯一 detector 是退役中的 Mac app 横幅。首版结论「fd 上限 256」被 09-01 审查**证伪**(hotfix 8192 后再失败 11 次);一次性 launchd job 实测真因 = TCC:macOS 按可执行文件路径授完全磁盘访问,launchd 会话里的 claude(每次更新换路径)读不到外置卷上的任务 repo,Bun 把 EPERM 渲成那句猜测。决定:模板只抬 soft fd 上限(余量)、不设 hard;`failures.claude_blind` + doctor `launchd claude` 探针(问 launchd 本人);同类连败 N 次(默认 5,可配)刹车,卡进「需输入」列 + 通知,退避窗口零写零 traceback,**进入 approved 的每条路径都重新上膛**;actd 每阶段写心跳,doctor / `GET /api/health` / web 横幅三读者;install.sh 退役 label 卸载自证 + 孤儿报告,用户日志不删。**待 owner**:(1) 给 claude 当前版本开完全磁盘访问(每次 claude 更新后重做)或把任务 repo 搬回启动盘;(2) 结构性根治的取舍——由有授权的 GUI app(`shell/`)托管 actd,子进程全继承一次授权,vs 继续 launchd + 每次更新手点。P6 自动 PR 通道要派发进本 repo(在外置卷上),不定这条 P6 一张卡也发不出去。 | 09-01 |
| D21 | **卡片编号两段式：检测只给 `P-` 主键，`R-` 工作编号批准才发**（issue #127） | 「如果这个卡片没有执行,就不算是真正的卡片,不需要给它 R 编号;只有我 approve 跑了的,才给编号。」→ 主键 `id` 出生即 `P-<n>` 终身不变（lineage 全指它）；`work_id` 只在进入 approved 时由 `registry.save` 分配、set-once、序列从存量 R 上界之上稠密单调永不复用；存量 `R-<n>` 主键原样保留，已过批准闸的存量卡下一次落盘采纳自己的主键作 work_id，从未批准的存量卡 `id_kind: legacy` 看板灰显、等 P5 清理。绕过 approved 直达 review/delivered 的卡按 D21 字面**不**给号（要放宽先改 CONTRACT §60）。store2 schema v1→v2 = 本 repo 第一级升级梯子。 | 09-01 |
| D22 | **模型选择按建议实现：两把旋钮（手/脑）+ 单一 LLM 边界 + doctor 活探针 + 设置页显式「设为」** | 「关于默认模型的选择，按照你的建议来。你先找机会把它 implement，然后我看看效果。」被采纳的建议：(a) app 此前从不传 `--model`，每次 claude 调用都继承 `~/.claude/settings.json` 的 `model`（当时 `claude-fable-5-1[1m]`），一个 EAP 别名退场曾让派工静默全败；(b) 两把旋钮不是一把：`models.dispatch`（claude --bg 派工 agent）与 `models.pipeline`（~8 处分散的 headless `claude -p`：analyze/triage、radar_slack、radar_gmail、quick_capture、merge_review、ask、golden_eval、judge…），各 = `follow`（默认，不传）或显式 canonical id；(c) doctor 探针：显式旋钮做一次最小活调用，失败 = FAIL 一句人话（「模型 X 不可用，派工会全部失败」）；(d) **不**在启动时改写 `~/.claude/settings.json`——设置页显示当前 Claude Code 全局默认，提供显式一键「设为 <id>」，只改 `model` 键、保留其余、先备份；(e) 下拉只列 canonical id（claude-fable-5 / claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5-20251001），自由文本允许但警告别名/后缀（[1m]、*-eap）会消失。 | 09-01 |
| D23 | **个人账户 repo 装不上 Merge Queue → PR 分支跟随 main 的 workflow 以 report-only 运行，PAT 暂缓** | （事实 + owner 拍板，非 owner 原话）ruleset API 对个人账户 repo 拒绝 `merge_queue`（§56.6 实测）；替代协议 `update-pr-branches.yml` 需要 fine-grained PAT（`PR_AUTOUPDATE_TOKEN`）才能真的更新分支并触发 `pull_request` 检查，owner 决定**暂不**创建 PAT——secret 缺席时 workflow 只打 `needs-rebase` 标签 + 幂等评论、不碰分支；在飞 PR 落后 main 时由各自的 writer 手动 rebase（`--force-with-lease`），auto-merge 照旧 `gh pr merge --auto --merge`。PAT 何时建、是否建，等 owner。 | 09-02 |
| D24 | **现在就发 1.0.0：web 看板 + 壳 + SQLite + merge=release=deploy 这套架构就是 1.0 基线，剩余阶段全是加法** | （owner 拍板 2026-09-02）0.48.x 已把真源（YAML → SQLite，P1）、客户端（菜单栏 app → web 看板 + Dock 壳，P4 Tier-0）、发版机制（手 bump → tag，§56）三样换完，骨架不再变；P5–P8 在此基线上只做加法、不再迁移数据、不再换客户端。发版走既有机制：PR 贴 `release: major` 标签，合并时 `release-on-merge.yml` 铸 `v1.0.0`；CHANGELOG 只写 `[Unreleased]`（首行「1.0.0 — 新架构基线 / architecture baseline」+ 大白话摘要），版本 pin 一字不动。 | 09-02 |
| D25 | **终态验收 = 一台空白 macOS 从 GitHub 一条命令装完、看板能用；P8 旧 app 删除仍等 owner 下令** | 「一路推进到完成。最终状态:我能够在另一台电脑上起一个空白环境,或者在其他电脑上更新这个软件,就能够直接使用。」→ 两条验收路径写进 §9「终态验收清单」：(a) 空白机 `git clone … && bash install.sh`（或 Releases 的 `.pkg`）→ install_report 无 fail、doctor 零 FAIL、Dock 里的看板 app 能用；(b) 已装机器合并后在下一个 timer 间隔内自动部署到新 tag、心跳版本 == tag。P8（卸载旧 app、删 `mac/`、换名）**不在**验收范围内，只等 owner 一句话（D3 原话不变）。 | 09-02 |

## 2. 目标状态(抽象功能需求;每条是"必须为真"的陈述)

### 2.1 数据层
- R2.1.1 卡片账本的唯一真源是 SQLite(`act/lib/store2`);所有读写路径(actd、雷达铸卡、digest、boardctl 经 server、dashboard 投影)经同一存储层。
- R2.1.2 每日自动导出一份 YAML 快照(可 git diff、可肉眼读),作为备份与人类可读镜像;导出 ↔ 导入可往返且测试钉死。
- R2.1.3 切换前**完整备份**旧 YAML registry(含 archive);切换后用「SQL → YAML 导出」与备份逐字段比对,差异为零才算完成;保留一条**手动回滚**路径(文档化命令)。
- R2.1.4 「agent 不得批准/验收」的权限墙从 SQL 触发器变为**实际生效**(store2 接线后自动成立)。
- R2.1.5 单写者精确化:状态变更仍只由 actd 发出;铸卡进程(雷达/digest)经同一存储层写入,由数据库事务保证一致(替代今天的"多进程无锁写 YAML")。

### 2.2 客户端
- R2.2.1 产品 app = `shell/`(Dock 里一个普通 app,WKWebView 承载 `web/`),**没有菜单栏图标**;关窗不退出。
- R2.2.2 web 看板必须补齐原生 app 的全部用户功能后,原生 app 才可卸载:设置页全套(Gmail/Slack 接入、telemetry opt-in、录制模式、字幕引擎与凭证、skills 管理、MCP 查看、素材库)、录制开关(放看板右上角)、权限体检与一键修复、doctor/诊断、更新检查、快速捕获、以及 web 尚缺的看板动作(merge review、split note、set title、import claude sessions 等)。以 `~/Downloads/brainstorm/s4-mac-parity.md` 的清单为验收表。
- R2.2.3 必须留在原生层(shell 内)的最小残留:screenpipe 进程托管、实时字幕悬浮窗宿主、系统通知投递、TCC 权限引导。
- R2.2.4 已安装的旧 app **改名为 "-old" 保留**;`mac/` 看板代码的删除只在 owner **明确下令**后进行(D3)。在它上面不做任何 QA 仪表。
- R2.2.5 录制中的状态不再需要 app 自己显示(依赖 macOS 系统指示器)。

### 2.3 质量仪表与合并护栏
- R2.3.1 四种仪表接入仓库:每函数圈复杂度 + CRAP(公式 `CC² × (1−cov)³ + CC`,上限 **6**)、行覆盖率、模块依赖方向规则(`act/lib` 只准向下;分层模型写进法典)、变异测试。
- R2.3.2 老代码同样达标:凡 CRAP > 6 的函数重构至达标(拆函数 / 补测试),**顺序为先补测试网(变异测试确认无洞)再重构守护核心**;所有重构必须零行为变化,由全套件 + 变异验证。
- R2.3.3 CI 必需检查(合并硬门):全套快测试(单元/集成/e2e/Swift/web)、复杂度 ≤ 6、依赖规则、覆盖率**不低于 main**(不设死数字,便于删功能连带删测试)。
- R2.3.4 变异测试**每晚自动**(GitHub Action 定时或本机 cron),结果写入固定位置(pinned issue / 报告文件),**永不作为 PR 门**;存活变异体自动成为每日循环的输入(→ 补测试提案)。
- R2.3.5 main 分支规则:PR-only(禁直接 push、禁 force push)、必需检查绿;**不**要求他人审批(owner 自己的 PR 可合)。
- R2.3.6 PR 描述携带结构化测试报告(跑了什么、结果在哪)——供 AI 审核参考,非通行证。
- R2.3.7 防腐十条中可机械化的条目(文件行数上限、import 方向、§ 指针、测试放置、命名单源、墓碑)以测试/检查形式执行,不再只是文字;既有违规以显式豁免清单登记并只许缩减。
- R2.3.8 qlty 配置真正在 CI 运行(安全类插件阻塞,风格类评论)。

### 2.4 每日自我改进循环
- R2.4.1 每天固定时段(可配置)在 actd 的 pass 内运行一次「维护 + 提案」:先维护再提案。
- R2.4.2 输入源:守护/雷达/执行日志、`state/analytics` 事件、变异测试存活体、GitHub 开放 issue、开放 PR 的评论与 CI 状态、素材库条目、doctor 结果。外来文本(issue/评论/网页)一律过 `sanitize.fence_untrusted`。
- R2.4.3 输出:新提案卡 ≤ `max_proposals_per_day`(默认 5,设置可改);每张必须带 plan/DoD/成本估计;去重对象 = 现有提案 + 潜在任务 + 开放 issue + 开放 PR。
- R2.4.4 维护动作:提案列与潜在任务列的重复项合并为**一张新卡**(记录来源卡 id 以便恢复),旧卡进回收站;过时卡(已在别处完成 / 长期无人理)进回收站;running / 待验收 / 已交付 **不碰**。回收站保持既有可恢复语义。
- R2.4.5 维护进行中,看板显示状态提示;结束后看板顶部留一行摘要「今日整理:合并 N、清理 M(可撤销)」,不弹系统通知(D10 设计判断)。
- R2.4.6 PR 评论驱动(D12):循环读取 owner 在开放 PR 上的评论与 CI 红状态,为每条生成一个"修复/补充"任务并派会话处理、更新同一 PR。
- R2.4.7 循环的每次运行与决策写入日志(可审计);运行失败不崩 pass(宪法第 11 条)。

### 2.5 素材库
- R2.5.1 owner 可在设置里输入链接和一句备注;条目落到 `state/` 下与日志同级的追加式存储(id/时间/URL/备注/状态)。
- R2.5.2 状态机:新 → 循环已读取 → 已生成提案 → 已开 PR → 完成/放弃;弹出窗只显示「尚未开 PR」的条目,可滚动。
- R2.5.3 循环处理条目时对 URL 做内容获取与理解(YouTube 字幕、网页正文等),据此提出与本产品相关的改进提案;素材库条目视为 owner 的主动行为(hand 级信任),但**不直接铸卡**,只经循环生成提案。
- R2.5.4 被动看到的屏幕内容仍受 §45 约束(只佐证不铸卡);素材库是 owner 主动入口,二者不混。

### 2.6 自动 PR 通道(原代号 F)
- R2.6.1 仅对 `target_repo` = zelin-ai-assistant 本仓库(物理路径匹配 + 写死的来源渠道 `self_improve`)开放;其他仓库的提案照旧走人工批准。
- R2.6.2 通道内的提案卡无需 owner 点批准即派工;交付**只能是草稿 PR**(分支名带前缀),永不推 main、永不合并、永不外发。
- R2.6.3 派工前物理校验:目标仓库存在、处于干净状态、分支可建;交付后物理校验:PR 存在且为 draft、base 为 main、diff 非空、未触碰 `main`;校验结果显示在卡片/PR 上(R2.3.6)。
- R2.6.4 用 owner 自己的 GitHub 身份(D8);无预算限制(D9)。
- R2.6.5 敏感路径护栏:通道内的 agent 不得修改自身的策略/通道/CI 配置文件(写死路径集);若 diff 触及,PR 打上 `needs-owner-eyes` 标签并暂停通道直到 owner 处理。
- R2.6.6 owner 在 PR 上的评论 = 下一轮任务(R2.4.6);owner 合并 = 验收;owner 关闭 = 拒绝(记入"已拒绝"记忆,避免重复提案)。

### 2.7 skill 商店(仓库内)
- R2.7.1 `skills/<name>/SKILL.md` + `references/` + `scripts/`(stdlib py3.9)为规范格式;frontmatter 含 `version` 与 `upstream_version`。
- R2.7.2 设置里可启用/停用;启用 = 软链接到 `~/.claude/skills/<name>`(agent 与 Claude Code 真正读取的位置);另一台机器 `git pull` + 刷新即同步。
- R2.7.3 默认 vs 自定义:仓库版为默认;用户本地改动的副本标记为自定义并显示"落后/领先上游 N 版";升级不覆盖自定义,只提示。
- R2.7.4 派工 agent 能在 worktree 内看到并使用仓库 skills(解决今天"agent 看不见 skills/ 目录"的缺口)。

### 2.8 测试 skill
- R2.8.1 运行后先探测项目技术栈,再列出可跑的测试种类(单元/集成/e2e/覆盖率/复杂度-CRAP/依赖规则/变异/安全扫描)与各自的时间估计;多选后并行执行;产出一份报告(markdown + JSON),含"先修什么"排序。
- R2.8.2 两种调用:owner 交互式;派工 agent 收工前非交互式(预设组合、JSON 输出),报告附进 PR(R2.3.6)。
- R2.8.3 阈值与规则住在仓库配置文件里,skill 只读不定义(防止出现第二套闸门定义)。

### 2.10 模型选择（D22）
- R2.10.1 所有带 prompt 的 claude 调用只经一个边界构造 argv（`act/llm.py`，防腐 #3 落地）；`--model` 只在那里拼一次；两把旋钮都 `follow` 时每个调用点的 argv 与 kwargs 与此前逐字节相同（判例钉死）。
- R2.10.2 两把旋钮 `models.dispatch`（手）/ `models.pipeline`（脑），值 `follow` 或显式 id；config.yaml 与 settings_overrides 两条写入路径，overrides 优先；改动下一次调用生效、无需重启守护进程。
- R2.10.3 doctor：一行显示 Claude Code 全局默认与两把旋钮指向（永不 FAIL；跟随非 canonical 别名 → WARN）；显式旋钮各一次最小活探针，失败 = FAIL `model_unavailable` 一句人话点名后果与修法。
- R2.10.4 设置页「模型」：两把下拉（跟随 / canonical / 自定义+警告）+ 保存；显示 Claude Code 全局默认并提供显式一键「设为 <id>」（确认 → 只改 `model` 键 → 先备份 → 坏文件拒改）；任何自动路径永不改写 `~/.claude/settings.json`。
- R2.10.5 server 是 `state/settings_overrides.json` 的 web 侧写者（diff-write，与 Mac app 同一保存语义，不覆盖其它键）；canonical 列表 server-owned，web 只镜像 wire 键。

### 2.9 采纳 / 拒绝的 Uncle Bob 原则
- 采纳:价值靠确定性工具执行而非提示词(Lost-in-the-Middle);CRAP + 变异测试作为两把尺;多 agent 各自新上下文、窄任务;"agent 说做完了"不算,工具说 OK 才算;架构依赖规则做成机器可检查的文件;识别 agent 挣扎(我们用会话遥测做自动版);阈值按 agent 调(CRAP 6)。
- 拒绝:把人的纪律(TDD 微循环)强加给 agent;Gherkin/规格阶段(我们的 DoD + triage 更强);100% 覆盖率硬指标;可点击 UML 查看器(规则文件才是价值);变异测试进 PR 门(太慢);提前写大计划(每个提案 = 一个 story = 一个 PR)。

## 3. 明确不做

- 不改成 agent 自己认领任务(pull)。
- 不把所有 proposal 都走自动通道——只有本仓库的自我改进走(D7)。
- 不设任何预算(D9)。
- 素材库不走 Slack 私信;不为素材库铸卡。
- 不保留菜单栏图标;不做原生看板的新功能。
- 不在即将删除的 `mac/` 代码上做 QA 仪表。
- 不要求 PR 有他人审批。
- 不在 PR 门里跑变异测试。
- 不做 Windows/Linux 桌面版(本轮)。

## 4. 执行顺序(每阶段 = 分支 → PR → CI 绿 → 合并;阶段间可释放)

| 阶段 | 内容 | 安全顺序理由 | 完成判据 | 回滚 |
|---|---|---|---|---|
| P0 | 本文档入库;memory 指针;**main 分支保护**(PR-only、必需检查、禁 force push);取消全部预算 | 护栏先于任何自动化 | ruleset 生效且一次直接 push 被拒;预算配置项移除/默认无限 | 关闭 ruleset |
| P1 | registry 备份 → store2 接线为真源 → 导出/比对 → 回滚文档 | owner 决定 DB 先行(D2);备份 + parity 是安全网 | 全套件绿;导出 YAML 与备份零差异;actd 在 SQLite 上跑满一天 | 停守护 → 恢复备份 YAML → 切回旧存储层(保留开关一个版本) |
| P2 | QA 仪表:覆盖率、复杂度、CRAP 脚本、import-linter、qlty 入 CI、变异测试夜间任务;CI 必需检查;防腐十条机械化 | 仪表就位才能安全重构 | 四仪表在 CI 产出报告;必需检查生效;夜间变异跑通一次 | 检查设为非必需 |
| P3 | 老代码达标重构(CRAP ≤ 6),按变异测试结果先补洞再拆函数;actd.py 等超限文件拆分 | 网已在 | 全仓库 CRAP ≤ 6;文件/函数上限无非豁免违规;全套件 + 变异绿 | 每函数一 PR,可单独 revert |
| P4 | web 补齐设置/录制/权限/doctor/看板动作;shell 承接字幕/通知/screenpipe;旧 app 改名 -old | 退役前置 | s4 清单全部 EXISTS;owner 一周日用无需打开旧 app | 旧 app 仍可启动 |
| P5 | 每日循环 + 维护(去重/过时/合并/UI 提示)+ 设置项 + 素材库(存储/设置入口/弹窗) | 依赖 P1(事务)与 P2(变异结果作输入) | 连续 3 天自动运行有记录;提案 ≤5/天;维护摘要出现在看板 | 关闭循环开关 |
| P5b | **会议 recap**(issue #129,owner 拍板 2026-09-01):`act/recap.py --once` 挂在既有 30 分钟 screenpipe cron 之后,只读 `~/.screenpipe/db.sqlite` 做**确定性** meeting-session 判定(不调 LLM;gap > 5 min 切 session、CLOSED = 静默 ≥ 5 min 且无 pending 转写、dedup key 一场一份);CLOSED 后生成 5 行 copy-only recap(中英同产);**无发送路径**——不是卡、不进 registry/dispatch、JSON 无 recipient/channel 字段、生成 argv `--tools ""` 由测试钉死,唯一出口是剪贴板;Settings 开关 `recap.slack_draft.enabled`(**默认关**)开后仅经 Slack MCP 白名单 `slack_send_message_draft` 投**草稿**,发送键仍在人手里;页面落 web 看板(D3),dashboard 只多 add-only 顶层 `recaps[]` | 不依赖任何阶段;排在 P2 之后,新代码出生即受 QA 仪表约束 | 一场真实会议 → 恰好一份 recap;`tests/test_recap_no_egress.py` 钉 argv;开关关时 argv 无任何 Slack 工具;开关开时白名单无 send/schedule/reaction | 摘掉 cron 挂点;`state/recap/` 可整目录删 |
| P6 | 自动 PR 通道:`self_improve` 渠道、本仓库白名单、草稿 PR 校验、敏感路径护栏、PR 评论/CI 红捕捉 | 依赖 P0 护栏 + P5 循环 | 一张自提案卡端到端到草稿 PR 且 CI 绿;owner 评论被下一轮捕捉 | 关闭通道开关 |
| P7 | skill 商店 + 测试 skill;agent 收工前自动跑测试 skill | 独立 | 两台机器同步验证;PR 附结构化报告 | — |
| P8 | owner 明确下令后:卸载旧 app、删除 `mac/` 看板代码、CONTRACT 墓碑 | 只等 owner 一句话 | — | git revert |

**P6 附注——AI 完成度评语 + 一句话摘要**(issue #128,proposal,2026-09-01):待验收卡在**验收时刻**附带证据(deliverable manifest:PR / CI 状态 / 文件数 / 触碰的保护路径)与一句 ≤40 字白话摘要,内容变化(新 run、打回、编辑)即重生成;AI 三态评语「建议验收 / 需继续做 / 需要拍板」各带一行理由(引用未满足的 DoD 条目)。**仅为建议**:永不自动验收,验收 / 打回只有人能按(§0 审批边界不动;与 R2.3.6「结构化测试报告是加分项不是通行证」同理)。

## 5. 审计结果补充(2026-09-01 issues/logs/suggestions 审计,摘自 `~/Downloads/brainstorm/AUDIT-ISSUES-LOGS-SUGGESTIONS.md`;处置同步到 GitHub 是 tracker hygiene 步骤)

### 5.1 Issue 处置表(22 open)

| Verdict | Issues | 一句话 |
|---|---|---|
| **SOLVE NOW** | #119 retire needs-input;#89 dispatch failure unactionable | #119 必须落在 store2 接线之前(否则把死态迁进 SQLite);#89 不是历史 bug 而是正在发生的风暴——**本 PR(PR-A)止血 + 诚实归因**:`claude_blind` 分类 + doctor `launchd claude` 探针 + 风暴刹车 + 心跳 + 孤儿探测;真修法(TCC 授权 / 搬 repo / GUI app 托管)在 owner 手里,见 D20。 |
| **LOOP**(喂给每日循环当 seed) | #37 telemetry 裸 stderr 上传;#19 social-preview 图;#18 demo_seed `--english`(seed #1);#16 ingest smoke test(`tests/integration/` 首住户);#15 pin Xcode;#11 卡上披露建 repo(`egress[]`);#8 a11y(改指 web,`vitest-axe`);#90 Windows 只走 PWA | 全部自包含、可验证,正好用来 prove 自动 PR 管线。 |
| **CLOSE** | #26(被 LOOP 取代);#22(milestone 1 done);#17(Swift test infra 已在 + mac 退役);#13(shell 已是新身份);#10(archive 已做,v0.48);#9(前提消失);#7(前提消失);#23(素材库落地后关) | 一次性 close/relabel;15/22 issue 52 天零活动、无 milestone,不清理则 loop 每天重读 Mac 视角的死 issue。 |
| **IGNORE**(blocked / meta) | #29 quiet hours;#28 retention UI;#27 recording schedule;#20 Usage Insights bot(keep pinned,是 LOOP 输入源);#23(产品 idea → 素材库) | 通知 relay / 录制控制 / 设置页都随 Mac 退役 re-home 之后再 scope。 |

另有 12 个 open PR(#121 本计划、#102 bundled skills、#108–#117 dependabot majors)——main 保护前第一批过门,majors 不许 loop 盲合。

### 5.2 日志教训(8 条 → 改进/删除)

| # | 发现 | → 处置 | 状态 |
|---|---|---|---|
| L1 | **派发重试风暴(live)**:R-175 66 attempts / 13h,954 × traceback,`registry_writes.jsonl` 98% 是它;审计当时记的根因「`ulimit -n 256`」**是错的**(hotfix 8192 后又失败 11 次)——真因是 launchd 会话里的 claude 可执行文件对外置卷任务目录 TCC-blind(EPERM,Bun 渲成 fd 猜测句);`failures.classify` 无规则 → `dispatch_error_id=null`,doctor 报 healthy | `failures.claude_blind`(Bun 猜测句)+ `fd_limit` 只留真 EMFILE;doctor `launchd claude`(一次性 launchd job 里跑 `claude --version`);plist 只抬 soft;退避窗口不写卡不打 traceback;同类连败 N 次刹车 → 「需输入」列 + 通知;每条进 approved 的路径重新上膛 | **PR-A 已做止血与归因**(CONTRACT §4.1/§25/§55);**修复待 owner**(TCC 开关 / 搬 repo,D20),验收 = doctor 行 OK + 一张重批卡到 executing |
| L2 | **静默卡死**:actd/syncd 活着、停在 `time.sleep`,dashboard/日志/写入全部冻在 22:31:5x 达 2.5h;没有产品自己看的 heartbeat,唯一 detector 是退役中的 Mac app 横幅 | 每阶段写 `state/actd.heartbeat`;doctor `actd heartbeat`(活着 + 过期 = FAIL `actd_stalled`,kickstart 提示);`GET /api/health` + web `PipelineBanner` | **PR-A 已做**(§47.4) |
| L3 | **孤儿 launchd agent 51 天**:imessageradar(v0.21 已删)23,613 × traceback、14.5 MB 日志;install.sh 的 RETIRED unload 把失败吞进 /dev/null;doctor 只查有模板的 label | install.sh `launchd_retire` 自证 + `launchd_orphans` 报告(install_report 两个 step);doctor `launchd orphans`;**日志不删**(owner 手动) | **PR-A 已做**(§55) |
| L4 | **Gmail 中毒 + 无凭证噪音**:3,921 × `parsedate_to_datetime TypeError`(13.6 天);`radar_skip source=gmail reason=no_credentials` 10,539 次,全部 radar_skip = 67% 的 analytics;54 天 151 次真扫 → 1 张卡;8 张 2024/25 旧件卡 | disabled / no-creds 源不排程(§48 gate 加断言);doctor 读 `radar_failed.json`;gmail 加「N 天以前的邮件不铸卡」出生过滤 | 待办(LOOP seed) |
| L5 | **syncd ack 队头阻塞 49 天**:6,267 × `ack-tail: patch capture-<uuid> failed 400`(每 10s 一条);`applied_cursor.json` 停在 2026-07-13;`syncd.py` 首个失败即 `break`,无 attempt counter | `ack_tail` 加 attempt counter + skip-after-N + 4xx poison 台账(size-capped) | 待办(U-97) |
| L6 | **快照体量**:2,158 × 453 KB 上传 / 30h ≈ 955 MB;477 KB `dashboard.json` 每 10s 无条件重写(~4 GB/天磁盘);review lane 带全文 `final_draft`,57 张 archived/trashed 随每个快照上传 | `write_dashboard` 内容不变短路;推送快照剥掉 `final_draft` 正文 + archived/trashed;ETag/delta 后置 | 待办 |
| L7 | **雷达产出 vs owner 参与**:9,888 次 radar_scan → 5 张卡;`silent_merge` 63 判 → 3 合(5%,每次一个 LLM call);weekly-digest automation 卡 0/15 approved、3 cluster 跨 4 周重铸;owner `card_action` 117(7 月)→ 16(8 月) | CLEANUP day-one 尺寸(≈75 → 30–35 张);merge 候选先走确定性 §38.3 再 LLM;**weekly_digest 的 automation ideas 停铸**(→ D19) | D19 已拍板,PR-B 做 |
| L8 | **死面 + 类型混乱**:152 个 event name → 69 从未触发;`mw_*` ~90% 未观测;auto_dispatch / steer / merge_force / split_note / voice / Ask / Slack settings 全 0;registry `type:` 66 个不同值 / 176 卡 | Mac 退役 DELETE 清单有数据背书;store2 把 `type` 收成 enum + `type_raw`;夜间变异优先打 §46.2/§47.1/§47.2/§34bis 这些 prod 从未执行的防御路径 | P1/P2 |

**三条 hygiene 附注**:H7 privacy——39 个 `state/logs/R-*.log` 逐字含 card title,`radar_failed.json` key 含第三方文件名,两者都不在 `clip_content` 覆盖下 → 停写 R-*.log 或改 id-only;**install_report `app: fail`**——v0.48.3 装机 Mac app build 失败没人发现 → install 有 fail step 就不许报 ok;**Telemetry 0.48.x 空白**——部署两天 insights 无 0.48 事件 → doctor probe。

### 5.3 采纳的建议(去重后 60 条 → 进 plan 的部分)

- **A · QA**:`act/llm.py run(prompt, runner=None)` + 拆 `silent_merge.JUDGE_RUNNER`(防腐 #3 存量违反);`act/actd.py` 3,718 行唯一超 cap → facade 拆 / grandfather 清单;§-docstring lint、§-citation liveness、tombstone check(§11 静默缺席);日志派生的 doctor/failures 扩展(L1–L5,**L1–L3 本 PR 已落**);privacy lint(`log_event(error=)` 禁裸文本)。
- **E · daily loop**:deliverable manifest `execution.deliverables {branch, pr_url, files[]}` + `cost_actual` 收割(approve-at-END 没它就是盲批);fingerprint dedupe vs open cards + open/closed issues + trashed cards,每类每天 ≤1 proposal;parse spec = 读台账不读 traceback(**不读** `state/logs/R-*.log`、legacy `*.launchd.log`、dashboard bodies);seeds 顺序:docs-drift PR → #18 → #19 → #15 → #89 doctor 半边 → U-97 → …
- **F · auto-PR lane**:`egress[]` disclosure;agent-facing token;main ruleset 加 `pull_request` + `required_status_checks`(今天 ff-push main 合法);fine-grained PAT(contents + pull_requests,**无 workflow scope**,branch `ai/*`);protected-path set hit → `needs-owner-eyes` + lane 挂起。
- **Mac-retire**:#119 四泳道语义先定 → `Store.swift` 15 组 optimistic 状态验尸表 → web 补 `merge_suggestions / split_note / set_title / import_claude_sessions / feedback / archive` → server-owned lane catalog → canonical slug(`ZAI_PORT` / `zai.theme` / `com.zelin.ai-board` 三拼法)→ shell 稳定签名 → capture / LiveCaptions / TCC 落点决定。同车退役 legacy `webui/` + `act/webui.py`、`docs/ROADMAP.md` 重写或 tombstone。**parity 1.11(pipeline health banner)已由本 PR 的 `/api/health` + `PipelineBanner` 提前落地**。
- **DB · store2**:T-7/8/10/12/14 表照搬为 wiring checklist;多写者 parity;两条来自日志的断言——一次 transition 一行写(L1)、`type` enum + `type_raw`(L8);新列 `merged_from` add-only;顺序 **#119 → write-storm brake + claude_blind 归因(本 PR) → store2 wiring**。
- **CLEANUP**:day one 合并 3 个 automation cluster + 6 张 3 周以上 `repeated_mentions==1` 卡;backlog 8 张旧件 + 3 张 diagnostic give-up + 8 张过期 deadline + ~13 张已被 shipped 版本覆盖的 dev 卡;3 张 Monday digest 卡 superseded 自动 archive;**现有 ~40 张 trashed 卡 09-08 起触发第一波硬删 → 先 pin 或临时拉长 retention**。

### 5.4 Open decisions(审计留的 6 问,默认值)

Q1 shell bundle identity → **保留 `com.zelin.ai-board`**,接受一次 TCC 重授权;Q2 screenpipe 进程归属 → **shell 原生子进程**;Q3 weekly_digest automation ideas → **停铸**(D19);Q4 stale 阈值 → **45 d + guards**,`stale:*` retention 90 d,先 pin 那 40 张;Q5 loop cap → **config 5、首月跑 2**,前两周走人工审批 lane;Q6 DRAFT PR token → **owner fine-grained PAT**(无 workflow scope);**Q7(PR-A 审查新增)** launchd 会话里的 claude 读不到外置卷 repo(D20)→ 默认建议 **shell app 托管 actd**(一次授权全继承,与 R2.2.3「shell 内最小原生残留」同车),过渡期 owner 手动给 claude 当前版本开完全磁盘访问、每次更新后重做;P6 开通前必须已解。

### 5.5 GitHub 处置 2026-09-01(§5.1 的执行记录;每条 issue 上都有引用证据的评论)

新建 label 七枚:`loop-seed`(每日循环 seed)、`needs-owner`(非 owner 作者,D18 只做摘要)、`mac-retire`(随 P4 re-home)、`素材库-idea`(产品想法,§2.5 落地后迁入)、`owner-decided` / `proposal` / `decision-needed`(owner 新 issue 三档)。

| Issue | 动作 | 理由(证据) |
|---|---|---|
| #10 archive old entries | **关闭(done)** | `archive_stale` `act/actd.py:2405`、`archive_after_days`=30 `act/lib/config.py:248`、`registry.ARCHIVE_DIR`;CONTRACT §10;store2(#126)后性能动机也消失 |
| #22 Windows/Linux port | **关闭(done,milestone 1)** | `install-linux.sh` 277 行、`install.ps1` 272 行、`docs/LINUX.md`、`docs/WINDOWS.md`、CI `tests-windows`(`ci.yml:114`);剩余 = #90 的 PWA;去 help-wanted |
| #89 dispatch unactionable、#119 retire needs-input | 已由 #125 / #126 关闭 | 本轮无动作 |
| #17 Swift test infra | **关闭(superseded)** | `mac/LogicTests` SPM + CI `swift test` 已在;mac/ 退役(D3)且 §3 禁在其上做 QA;真缺口 = shell/ 零测试 → P2/P4 |
| #9 Settings unsaved changes | **关闭(superseded)** | v0.14 起 diff-write on change(`Settings.swift:3-5`,§15.3),前提不存在;Mac Settings 退役 |
| #13 naming decision | **关闭(superseded)** | shell 已是 `com.zelin.ai-board`;TCC 重授权 / 旧 launchd label bootout 并入 Mac-retire 清单(§5.4 Q1) |
| #26 insights auto-file issues | **关闭(superseded)** | 方向反了(→ 提案卡不 → issue);fingerprint dedupe + 每日 cap 两条约束已进 §2.4(D10) |
| #7 capture_id | 留开,`mac-retire` | 全仓零 `capture_id`;症状只在 `mac/Sources/PendingSweep.swift` 的乐观占位,web `LaneComposer` 无 ghost;P4 若加 optimistic echo 再以 add-only 复活 |
| #18 #19 #15 #16 #11 #8 #37 | `loop-seed` | 自包含、可验证,P5/P6 首批 seed(顺序见 §5.3 E) |
| #90 Windows shell(Carol929) | `needs-owner` | 非 owner 作者 → D18 摘要制;技术上只做 PWA manifest |
| #29 #28 #27 quiet hours / retention UI / recording schedule | `mac-retire` | 通知 relay / 设置页 / 录制控制随 P4 re-home 到 web+shell 后再 scope |
| #23 commitments ledger | `素材库-idea` | 产品想法非缺陷,素材库(§2.5)落地后迁入并关 |
| #20 Usage Insights(bot,pinned) | 不动 | 每日循环输入源(R2.4.2) |
| #129 会议 recap | `owner-decided` | owner 拍板 → 本文 P5b 行 |
| #128 完成度评语 + 摘要 | `proposal` | → 本文 P6 附注 |
| #127 R-number 在 detected 时分配 | `decision-needed` → **owner-decided（D21）** | 三个选项(文档化 / 两段 id / 单独 approval 序号)→ owner 拍板方案 2 的变体:`P-` 主键出生即定、`R-` 工作编号批准才发,存量 R 主键原样保留不迁移(CONTRACT §60,`feat/two-stage-card-ids`) |

**dependabot #108–#117(全部先 `@dependabot rebase` 再看绿 CI;actions 的 SHA pin 逐个与上游 tag 比对一致;新 major 全部只是 Node 24 运行时,GitHub-hosted runner 满足,本仓库无 `pull_request_target`/`workflow_run`、setup-python 无 `pip-install` 入参、github-script 脚本不 `require('@actions/github')`)**:

| PR | 结果 |
|---|---|
| #112 checkout 4.3.1→7.0.1 | merged `52e5cd3` |
| #109 setup-python 5.6.0→7.0.0 | merged `3330a54` |
| #110 setup-node 6.5.0→7.0.0 | merged `d9a2c2f` |
| #108 attest-build-provenance 3.0.0→4.2.2 | merged `50594a2`(v4 = `actions/attest` 的 wrapper,入参不变) |
| #111 github-script 7→9 | merged `c619448` |
| #116 jsdom 25→30 | merged `1adbf1c`(engines `^22.22.2`,CI 解析到 Node 22.23.2;本地 build + 159 tests 绿) |
| #113 vitest 3→4 | merged `e1b2f25`(本地 build + 159 tests 绿;jsdom 合并后重新 rebase 再过 CI) |
| #117 typescript 5.7→7.0(Go 原生编译器,20 个平台二进制全在 lockfile) | merged `02bf1c7`(本地验证 `tsc --noEmit` 仍会抓类型错、tsconfig 零弃用警告、build + 159 tests 绿;vitest 合并后再 rebase 一次才合) |
| #114 @vitejs/plugin-react 4→6 | **关闭 + `@dependabot ignore this major version`**:peer 硬依赖 `vite@^8`;实测 vite 8 + vitest 3 会出现两份 vite、`vite.config.ts` 的 `test` 键 tsc 报错——是 vite 8 + vitest 4 + plugin-react 6 三 major 联动,P4 一次做;做 vite 8 时 `@dependabot unignore this dependency` |
| #115 react 18→19 | **关闭(不 ignore)**:PR 只抬了 react + @types/react,react-dom 留在 18 → ERESOLVE,结构上装不上;本地四包齐升 tsc / build / 159 tests 零改动全绿(bundle 271→322 KB),React 19 可行,留给 P4 一次四包同 PR 落;dependabot 无 `groups:` 会继续产半截 bump |

## 6. 对话记录(owner 原话摘录,按主题)

**关于 dashi 与方向(08-30 ~ 08-31)**
- 「我感觉这个项目和当前我这个项目非常像……我觉得还是有很多功能可以借鉴到我这个项目里面的。」
- 「我最终希望取各家所长,做出一个 1+1>2 的新版本。」
- 「该自动的地方就尽量自动,该 human approve 的地方就也要保证权限安全。」

**关于 Uncle Bob 视频与 QA(09-01)**
- 「他在视频中提到了 mutation test 和另一个 test,好像是评估每一个 function 的东西。你看一看那个 test 需不需要添加。」
- 「除了单元测试、集成测试、端到端测试,其实还需要变异测试对吧?还需要另一个给每个 function 的 test。」
- 「你提到所有量化的代码健康指标零仪表……我觉得这是需要解决的问题。我给你这个视频,就是希望你能发现这个问题。」
- 「我希望变异测试、CRAP/复杂度、覆盖率、依赖方向检查接进 CI,今天做最完美的推进,老代码新代码都弄好……冲着最终完整版去弄。反正你是 AI,时间等得起。」

**关于自动化程度(09-01)**
- 「目前这个软件还是有很多东西需要我过来自己 approve。我在想,写软件这个活能不能自动化一点?比如它可以直接提 PR,不进入主程序……我确认的时候,不是在开始让你干活,而是确认接受你的活。」
- 「尽可能让 AI 去做东西,只是通过 PR 的方式来把控:如果 AI 出现差的结果,人就不会受影响,主程序也不会受影响;但是如果人需要的话,不需要等 AI 做出来,因为 AI 已经把东西提供在这里了。」
- 「人是皇上,所有的 AI 都是臣子。臣子把东西做出来了,皇上能挑你的那就是你的大幸;如果没挑,token 浪费这种东西其实不需要考虑。」
- 「因为我每天都有 AI 来扫一遍项目……如果红了,肯定会被车道捕捉到。所以红了也没关系,后续的 AI 会把它处理好。」

**关于每日循环与去重(09-01)**
- 「先频繁一点,比如每隔一天,就去看 log 和这个项目的 issue 等,从而来自我更新这个软件。每天都可以来一遍。」
- 「AI 每天新开卡不需要设上限,只需要每天固定一个时间点清理一下就行……每天最多不要超过 5 个。」
- 「已经在 run 了,不需要去重。但是 Proposal 和 backlog 潜在任务,这个还是去重一下比较好。甚至不只是去重,已经过时了的其实可以把它去掉。」
- 「过时的卡片进回收站吧……万一我什么时候还需要看的话,我还可以自己去回收站找。」

**关于素材库(09-01)**
- 「是不是也可以想个办法,让 AI 自动根据我的需要(包括我看的视频,或者我丢进去的链接)进行分析……把我看到好的东西融入进来,从而实现持续更新。」
- 「我看过的内容还是不要做卡片……遇到好的东西我就往里面扔,扔链接或者一点讲解什么的,你可以把它放到 log 一起。这样在每日自我更新循环的时候,就可以把这些内容抓取出来进行更新。」

**关于 Mac app(09-01)**
- 「我记得之前提过,把 Mac 菜单栏那个东西去掉,不要那个 Mac 的菜单栏。」
- 「Web App:你可以给它改个名,加个 old 表示老版本,现在就可以退役了。如果我真的还需要,我再打开就行。」
- 「你只要把那些任务卡片备份好。在转到 SQL 上之前要先备份,转完之后再看是否和老版本一致。」

**关于执行方式(09-01)**
- 「我觉得整个修改你可以自己改,不管花几天还是几周,最后确认的时候,你帮我一路推进。」
- 「为了防止你中途忘掉……把我们前面聊的这么多内容都写进你要修改的文档里。记录抽象功能……记录沟通内容。」

## 7. 参考文件索引(仓库外,只读)

- `~/Downloads/brainstorm/REQUIREMENTS-DRAFT.md` — 需求草案(含每项的代码位置引用)
- `~/Downloads/brainstorm/r1..r6-*.md` — 原视频精读 / skill 基础设施 / 自我改进循环现状 / 期末批准可行性 / 架构事实 / 测试分类与工具
- `~/Downloads/brainstorm/s1..s5-*.md` + `AUDIT-ISSUES-LOGS-SUGGESTIONS.md` — issue / 日志 / 建议列表 / Mac 迁移清单 / 看板基线
- `~/Downloads/brainstorm/architecture-v048.html` — 架构现状图(含本轮新增部分虚线标注)
- `~/Downloads/repo-review/UNCLEBOB-ADOPTION.md`, `V048-CODE-HEALTH.md`, `STRUCTURE-VERDICT.md` — 质量审计三份
- `~/Downloads/dashi-research/V048-AUDIT.md`, `SYNTHESIS.md`, `SRC-SYNTHESIS.md` — dashi 对照研究

## 8. 进度日志(每个落地的 PR 一行;日期 = 开 PR 当天)

**2026-09-02 起本表冻结为历史，不再直接追加行**（CONTRACT §56.7，`ci/changelog-fragments`）：并行 PR 全部在表尾插一行 = 最后一类相邻行合并冲突。新的一行 = 一个文件 `docs/design/progress/<YYYY-MM-DD>-<slug>.md`（头部 `pr:` / `phase:` / `law:`，空行，正文；形状见 `docs/design/progress/README.md`），CI 门「Version pins untouched」拒绝直接往本表新增 `| YYYY-MM-DD |` 行。读全表：`python3 scripts/ci/progress_log.py render`（历史行 + 全部 fragments → stdout，永不写回本文件）。

| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |
|---|---|---|---|---|
| 2026-09-01 | `fix/launchd-fd-storm-heartbeat`(PR-A,#89) | P0 前置止血(审计 L1/L2/L3) | launchd 模板只抬 soft fd 上限(hard 不设,实测 hard 键只降天花板)、systemd `LimitNOFILE=8192:524288`;`failures.claude_blind`(Bun 猜测句 = TCC EPERM,真因)+ `fd_limit` 只留真 EMFILE;doctor `launchd claude` 探针(一次性 launchd job);派发风暴刹车(同类连败 5 次 → `dispatch_halted`,进「需输入」列 + 通知,退避窗口零写零 traceback,进入 approved 的每条路径 + 退回提案都清账);actd 每阶段写 `state/actd.heartbeat`,doctor `actd heartbeat` / `launchd fd limit` / `launchd orphans` 三探针,`GET /api/health` + web `PipelineBanner`;install.sh `launchd_retire` 自证 + `launchd_orphans` 报告。本节 + D17–D20 + §5 同 PR 入库。 | §2 / §4.1 / §25 / §47.4 / §49 / §55 |
| 2026-09-01 | `feat/store2-source-of-truth`（PR-D，#126，v0.48.8） | P1（D2）+ 前置 #119 | **store2 接线为真源**：actd 首 pass 激活协议（整目录备份带 sha256 manifest 永不覆盖 → 从备份迁移 → 导出 → 逐字段比对 → 零差异 + 无并发 YAML 写才写标记；任何差异 = 删库拒绝 + doctor FAIL，无半态）；registry 门面公开 API 双后端逐字一致（callers 零改动）；每日 YAML 导出镜像 `state/registry-export/`（prune 常开）；多进程写者走 BEGIN IMMEDIATE 事务（跨进程判例）；agent 转移墙实际生效（DB trigger + Python 墙，actd 级判例）；白名单接线补行 5 条 + origin_trust 触发器改「只禁升档」；回滚开关 `registry.backend: yaml` 保留一个版本（TROUBLESHOOTING「store2 回滚」）。**同版落地 #119 需输入退役**：受阻/放弃救活的会话按 stop_to_review 收割进待验收（interrupted 标记 + msg_review_interrupted），answer_input/executor.answer/extract_question/msg_needs_input 全退役，needs_input[] 只剩 §4 刹车行。 | §0.1（显式精确化）/ §1 / §2 / §5 / §6 / §24 / §39（tombstone）/ §44.7 / §46.3 / §53（整节改写） |
| 2026-09-01 | `feat/nightly-mutation`（PR-F，#132，v0.48.13） | P2（D5/R2.3.4 变异测试半边） | **夜间变异测试**：`scripts/qa/mutate.py` stdlib-only 自制 runner（operator flips：比较符对翻 / + - / and or / True False / continue break / return X→None / is / in / 整数常量 ±1；docstring、logging 调用、`__repr__`、`__main__` 守卫整棵跳过——等价变异体高发区），变异体写**临时工作区副本**（真源树零触碰）、跑 `qa/mutation_targets.toml` 映射的定向子集（宪法关键模块先行：sanitize / policy / risk / silent_merge / auto_merge / failures / heartbeat / provenance / registry / store2.store，共 ~1,170 sites），确定性 site 顺序 + `.qa/mutation/state.json` 断点续跑 + 预算封顶 round-robin（跨夜每模块都被访问，模块内容**或映射测试子集**的 hash 变即作废重跑——测试变强必须重判存活，round-1 审查 B3）；`.github/workflows/mutation-nightly.yml`（每天 09:03 UTC，60 min 顶，state 走 Actions cache 跨夜，artifact `mutation-report`）+ pinned issue「Nightly mutation report」幂等 create-or-update（`scripts/qa/mutation_issue.py`，注入缝 + dry-run 判例，零网络可测）；JSON survivors（file:line + operator，字段 add-only）= P5 每日循环的机器可读输入。**永不作为 PR 门**（D5）；本地 `python3 scripts/qa/mutate.py --all`，不装 launchd agent（owner 机器保持精简）。round-1 审查修复：12 个新函数按 §58 复杂度门重构至 CC ≤ 6（账本 shrink-only，新代码必须干净）；logging 跳过收紧为精确名单；pinned issue 列举加 `in:title` search；子集子进程 TMPDIR 嵌进本轮沙箱（防腐 #4 泄漏）。 | §57（新增；PR-E 的合并硬门四仪表在 §58 并为本节预留了 §57 席位，两车后合并者 rebase 冲突） |
| 2026-09-01 | `fix/autodeploy-verdict-race`（PR #130，v0.48.14） | §56 首次实战修复（D17） | **P0 已合并上线（#122–#125）；P1 已合并上线（#126），truth=sqlite。autodeploy 头两次实战**：0.48.7 全自动部署 OK；0.48.8 部署成功，store2 迁移激活 175 张卡、比对零差异（备份 `registry-20260901T210719Z`）——但装后 12 s 的单次 doctor 采样撞上迁移 settle + 外置卷瞬态 EPERM 窗口，报 6 个假「new FAIL」触发假阳性回滚，又被同一窗口里 git 的空输出误报 detached 而**侥幸**拒绝（真回滚会让 SQLite 账本落在无 store2 运行时的 0.48.7 上）。本 PR 把运气换成结构：装后 doctor 判决改有界重试环（默认 3 次 × 45 s，只认最后一次仍在的新增 FAIL）；symbolic-ref rc>1（git 读不了 checkout）≠ detached，入口与回滚重验分开诚实处理，refusal 永不插空 sha；store2 标记在部署期间出现、或 store2.db 的 PRAGMA user_version 在部署期间升高（#135 review：schema bump 时标记早已在场，单看标记会漏）→ 拒绝代码回滚并指向 TROUBLESHOOTING「store2 回滚」；write_state / notify 失败行携带子进程异常、notify 返回 False 也记行、删锁失败也记行（实战 `PermissionError` 只在 launchd stderr 里，无法关联）；review 期两补：booby-trap 判例钉死「一次部署跑的是合并前旧脚本」并入法 §56.3（第 N 版闸门保护不了升到 N 那轮——schema 闸门因此必须先于 #135 入库）。 | §56.3 步 10 + 回滚重验（修订） |
| 2026-09-01 | `feat/digest-frequency-knob`(PR #123,v0.48.5) | D19 落地(审计 L7) | `digest.frequency` 旋钮 `off/daily/every2days/weekly` **默认 off**(config + overrides 扁平键 `digest_frequency`);crontab 行改为每天 09:07 不带 `--now`,模块按滚动间隔自闸门(标记 `state/digest.json`),off/未到期静默;doctor 「cron digest」看见旧的 `--now` 行即 WARN;文案 「周一 digest」→「状态摘要」;weekly digest 默认 off、launchd 每小时唤醒静默;automation-idea 提案卡退役且管道代码同版删除(防腐 #6);两个标记写失败只打一行、卡仍落。**UI 未到**:两把旋钮都还没有设置页可点(Mac 不加功能 D3,web 设置页 = P4),P4 前经 config.yaml / overrides 手改。 | §16 / §17 / §24 / §40.7 |
| 2026-09-01 | `docs/issue-triage-2026-09-01`(docs only,无版本 bump) | tracker hygiene(§5.1 执行) | GitHub 处置落账 §5.5:关 6 个 issue(#10 #22 done;#17 #9 #13 #26 superseded)、#7 留开标 `mac-retire`、七枚新 label 与 17 个 issue 的归类;dependabot #108–#117 八合二关(actions 五 major + jsdom 30 + vitest 4 + TypeScript 7 进 main;plugin-react 6 ignore-major、react 19 关不 ignore,两者归 P4 一次联动);§4 新增 **P5b 会议 recap**(#129,owner 拍板)与 **P6 附注 AI 完成度评语**(#128,proposal)。 | —(纯文档) |
| 2026-09-01 | `feat/model-settings`（D22，v0.48.11） | 横切（P4 设置页首个 section 提前落地） | **`act/llm.py` 真实存在**：`run()` / `dispatch_argv()` / `probe_argv()` 单一边界，10 处 headless `claude -p` + executor 4 个发射点全部经它构造 argv（follow 时逐字节不变，判例逐 site 钉住；`--model` 只拼一处；`_runner_env` 搬入为 `llm.runner_env`，跨模块 `_私名` 引用清零）；两把旋钮 `models.dispatch` / `models.pipeline`（config.yaml 块 + overrides 扁平键，坏形状回落 follow；actd 每 pass 现读两字段，无需重启）；doctor 三行（`claude code model` 永不 FAIL / `model dispatch` / `model pipeline` 活探针，`model_unavailable` 新 failure id，Swift 镜像句）；server `GET/PUT /api/settings/models`、`GET/POST /api/claude-code/default-model`（PUT 进四闸；diff-write overrides；只改 `model` 键 + `.bak-<ts>` 备份 + 坏文件 409 `CONFLICT`）；web 首个设置页 `?page=settings`（顶栏齿轮）section「模型」。 | §15 / §25 / §49 / **§59（新）** |
| 2026-09-01 | `feat/qa-merge-gates`（PR-E，#131，v0.48.12） | P2（D4/D5/D15；R2.3.1/R2.3.3/R2.3.7/R2.3.8） | **QA 合并硬门**：阈值单源 `qa/gates.toml`；五道门 `scripts/qa/`——每函数圈复杂度 ≤6（stdlib ast，口径判例钉死）、CRAP ≤6（`CC²×(1−cov)³+CC`，coverage JSON ∩ AST 行段）、总覆盖率地板 `qa/coverage_floor.txt`（83.2，只许 PR 上调、门自动给建议值）、依赖方向（act/lib 只准向下、entrypoint 互不 import、server 只到 act.lib、跨模块 `_私名` 禁令）、hygiene（防腐 #1/#5 行数上限 + docstring-§；mac/ 按 D3 豁免）；**shrink-only 账本** `qa/*_baseline.txt`（313 complexity / 376 CRAP / 76 deps / 23 hygiene——new/worse/stale 皆 FAIL，账本只许缩，P3 清账）；CI job `qa-gates`（**出生非必需**，绿稳后升 required）+ `qlty` informational（配好 2 个月的 9 插件首次真跑）+ ruff C901 advisory；coverage 派生的两道门只在 canonical linux 判卷（首日实测 `_login_shell_claude` darwin 5.1 vs ubuntu 20.7）；CLAUDE.md 防腐 #1/#2/#5 挂上执法脚本指针。**审查轮 1 补**：`scripts/qa/ledger_diff.py` base 差分门（三态判决看不见「账本自己长了」——新债 + 同 PR 自记账在三态下全绿；PR 上与 merge ref 第一父比较，账本加键/抬分、地板下调、gates.toml 放宽/删键/表外改动全 FAIL，账本出生免比）+ crap 账本对账到 canonical artifact（7 条 darwin 残值取 canonical 实测、_check_launchd 留更紧的 17——对账自身也只许缩、compute_target_kind 划账）；rebase 到 v0.48.11 后重收出生账（#134 的 llm/模型设置债入册，crap 从 run 33577287680 的 artifact 收）。 | **§58（新增）**；CLAUDE.md 防腐十条 #1/#2/#5；CONTRIBUTING |
| 2026-09-01 | `feat/two-stage-card-ids`（#127，v0.48.15） | D21 落地（P1 数据层后续；store2 第一级 schema 升级） | **两段式编号**：`registry.next_id()` 改发 `P-<n>` 主键（12 个铸卡点零改动）；`work_id`（`R-<m>`）只在 `registry.save()` 见到 approved 且无号时分配——owner approve / §51 免批 / capture[run] / restore 四条路径全覆盖，检测·合并·回收站零分配，set-once；序列 = max(存量 R 主键 ∪ work_id ∪ `state/work_seq.json` 高水位)+1（跨进程接力判例）；`resolve()` 主键或工作编号双向可达，inbox / merge / server `/api/cards/{ref}` / boardctl 全接线，lineage 只落主键；投影行 add-only `display_id` / `id_kind` / `work_id`，web 显示 `display_id`、动作送 `id`、legacy 灰显只信 `id_kind`；executor prompt 头 / 会话名 / 日志名用显示编号；`id_sort_key` 修正 actd FIFO 与 auto_merge / quick_capture 的跨命名空间排序；store2 schema v2（`cards.work_id` + 唯一索引 + set-once 触发器）+ `_UPGRADES` 升级梯子（crash window / 形状收敛判例）+ 单向门退路：踏出每级前自动留 `store2.db.pre-v<from>` 快照、拍不下来拒升级（`SCHEMA_SNAPSHOT_FAILED`），快照写锁下复核版本后刷新、单文件；旧代码打不开新库的降级步骤入 TROUBLESHOOTING（v2 库连 -wal/-shm 一起挪走、绝不手改 user_version）；部署回滚闸门在 #130（§56.3，**须先合并并部署到 live**——回滚跑的是 PREV 侧脚本）；陈旧内存副本 / 被旧代码剥号的卡落盘时采纳真源里的号不重铸不清空；demo_seed hero `P-101` → `R-101`。 | §1 / §2 / §3 / §4 / §10 / §37.1 / §53.1 / §53.6 / **§60（新）** |
| 2026-09-02 | `fix/install-cron-eperm-nonfatal`（PR #137，v0.48.16） | §56 实战修复第二弹（D17） | **首次 timer 实战死锁**：v0.48.12 自动部署在 install.sh 第 6 步撞 `crontab: tmp/tmp.<pid>: Operation not permitted`（launchd 会话缺 FDA；此前两次成功部署都在 owner 交互会话拉起的环境里，没暴露）→ cron=fail → 退出 1 → 回滚 → 回滚重装撞同一堵墙 → rollback_failed + sha 中毒，全部后续部署停摆（`--force` 无解）。修复：第 6 步抽成 `apply_crontab`，EPERM 记新值 `cron=skipped_tcc`（§23 add-only）不进 `failed_deploy_steps`（同 `app` 例外——环境问题回滚治不了）；其余 crontab 失败照旧 fail（报错里的 `tmp/tmp.<pid>` 是 crontab 的 spool 相对路径，与 TMPDIR 无关）；doctor 新行 `cron write access`（WARN，新 failure id `cron_tcc_blocked`，Swift 镜像同步）点名给守护 python 开 FDA、终端跑通不算数。根修（TCC 授权）在 owner（D20 家族）。 | §23 / §25 / §56.5（修订） |
| 2026-09-01 | `fix/web-board-parity`（v0.48.17） | P4 前置（D3：web 看板继承原生看板行为与外观） | owner 对照原生看板列出的 9 项回归全部补回：列内排序三模式（`cardSortOrder` 同名偏好，P-/R- 混排按数字后缀）、详情默认收起 + 按卡记忆、卡面 chips/行（落点三态、已并入×N、repo 章、耗时/已等待验收/验收于、单击复制指令）、相对时间处处 + hover 绝对、出错运行卡的 让 AI 修（新 `POST /api/ai-fix` = 原生 AIFix.launch 的 server 落点，非 inbox 动作）+ 回答…（comment/steer）、右侧「永久性完成」书立条、列头「?」说明（新 `GET /api/lanes` server-owned 文案目录，防腐 #10）、composer 原生占位文案、卡 id 右上角。零新增投影键。server 路由表驱动化（§58 门下 `_route_get/_route_post` 账本收紧）。 | §49 路由表 / **§54.1（新）** |
| 2026-09-01 | `feat/ui-deploy-server-agent`（v0.48.18） | D17 实战修复第一弹 + P4 前置（R2.2.1） | **事故（2026-09-02 03:35Z 审计）**：owner 机器守护进程在 v0.48.12，看板 UI **从未被部署**——install.sh 没有任何步骤构建 web/dist 或 shell；/Applications 里是 v0.48.0 的旧 app，壳 app 不存在，直到手工 `npm run build` + `shell/build.sh` 拷进去。手工壳 spawn 的 `python3 -m server` 以 `No module named server` 死掉：**GUI app 是子进程的 TCC responsible process**，壳 bundle 没有磁盘授权（ad-hoc 签名，授权也不跟 build 走），外置卷上的 repo 读不到——D20 的 GUI 版。手工救法 = 抄 actd plist 做 `com.zelin.aiassistant.server`（守护解释器已有 FDA），壳只连接；doctor 的孤儿探针（#125）把这只手做的 label 当孤儿。**成法**：server 是常驻 launchd agent（模板 + systemd 镜像 + `server.port` 单源 + EADDRINUSE 一行退出 75）；壳「探活 → launchd 已加载则只等不 spawn → 未加载才兜底」，弹窗第一条是 kickstart；doctor `board server` 行（回环 /api/health）+ RESIDENT 收编；install.sh 步 4b `ui`（web 在 `~/Library/Caches/zelin-ai-assistant/web-build/` 构建再拷回——**一次性 launchd job 实测 homebrew node 对外置卷 repo EPERM，swiftc/bash/cp 正常**，D20 家族的又一实例；npm ci/build + shell build + stage-then-swap 进 /Applications，缺工具链 skipped 不算失败、残余 EPERM `skipped_tcc` 不算失败（doctor `board ui build` 行可见），构建坏 fail 回滚，600 s/命令看门狗，relaunch 只在自动部署路径且在 server agent 回来之后；整步在 launchd 下实测 6 s）；CI macOS job 编壳。**命名**（owner：「为什么名字变成了 Zelin AI Board」）：显示名改 **"Zelin's AI Assistant (Board)"**（Dock / 窗口 / app 菜单），bundle 文件夹与 id **暂留** `Zelin AI Board.app` / `com.zelin.ai-board`——最终换名（壳接手 "Zelin's AI Assistant"，旧 app 改 "(old)"）**在 P8 与旧 app 退役同车**，因为 TCC（屏幕录制 / 麦克风 / Documents）按 bundle id 记账在旧 id `com.zelin.ai-engineer` 上：P4 把录制 / 字幕 / 通知搬进壳时要决定壳是接过旧 id（授权零重做但与旧 app 同 id 共存冲突）还是保留新 id（重授权一次，§5.4 Q1 默认）；在那之前换名只会让两个 app 在 Dock 上同名而授权归属不明。**待 owner**：D20 家族——server 解释器有 FDA 只是因为 owner 07-10 给 `/usr/bin/python3` 点过；换解释器 / 换机器要重做。 | §23 / §54（§54.2 新增、§54.3 修订）/ §55 / §56.3 / §56.5 |
| 2026-09-01 | `feat/shell-bridge-recording-captions`（PR #138，v0.48.19） | P4 Tier-0 0.4 + Tier-4（D3；R2.2.1–R2.2.3；s4 顺序第 4 步） | **录制 + 实时字幕进壳、header 两开关**：`shell/Sources/ShellBridge.swift` `zaiShell` 桥（`WKScriptMessageHandlerWithReply`；getState / setRecording / restartRecording / openScreenRecordingSettings / setCaptions / setLanguage → 同一份 snake_case 快照，`zai-shell-state` 事件推送，reject code `UNKNOWN_METHOD` / `INVALID_ARGS`）；`Recording.swift` / `CaptionCore.swift` / `LiveCaptions.swift` 自 mac/ **逐字节**搬入（判例钉住），`CaptionOverlay.swift` 只改齿轮去向；同名 helper 只读子集落 `ShellSupport.swift`（FailureCatalog 引擎 6 句与 failures.py 逐字判例）；screenpipe 成为壳的直接子进程（TCC 归属），analytics 事件词表不变；壳关窗不退出、Dock 重开；一次性从 `com.zelin.ai-engineer` 域接过录制/字幕偏好（不搬 TCC 标记）；`Info.plist` 加麦克风 usage description。web：`ShellControls` 只在桥在场时渲染，`RecordingControl`（录制：关/未在录制/仅屏幕/屏幕+音频 三色 + 三态菜单 + 重启 + 权限深链 + 死因）与 `CaptionsControl`（四态）逐字镜像原生 `RecordingMenuButton`，乐观 UI + reject 回滚，语言同步给壳。门：CI 首次编 `shell/build.sh` + `shell/tests/run.sh`（swiftc 全模块 typecheck + XCTest-free 桥 harness）、vitest +18、`tests/test_shell_engine_mirror.py`。**未到**（按 s4 顺序另 PR）：通知中继 / vault-sync-helper / framegrab 进壳（0.1–0.3）、字幕偏好设置页（Tier 2）、稳定签名（0.9）、旧 app 改名 -old。 | §15 追记 / §36 追记 / §54 追记 / **§61（新增）** |
| 2026-09-02 | `fix/autodeploy-tcc-hardening`（PR #140，§56 follow-up，v0.48.20） | D20 第四幕（§56 二次实战） | **事故 2026-09-02T00:48Z**：timer 起的自动部署把 checkout 推到 v0.48.11 后 `bash install.sh` 对外置卷 EPERM（exit 126）、回滚被拒、`state/deploy_state.json` / 通知队列 / 锁全部 `PermissionError [Errno 1]`（只在 launchd 无时间戳的 stderr 里，同一份日志还有 Xcode python3 的 `No module named 'act'`）；01:08Z 下一轮见 HEAD == origin/main 即写 `up_to_date` + `version=0.48.11`，而 actd 心跳还是 0.48.8——install 从未完成。终端 kickstart 的每一次都绿：它借的是终端的完全磁盘访问；macOS 按 responsible executable 给可移动卷授权，launchd 任务收不到弹窗。**做了**：(1) 「deployed 就是在跑」——`up_to_date` := HEAD 到位 ∧ install_report 版本 == checkout ∧ actd 心跳版本 == checkout 且新鲜，否则 `install_incomplete`（`reason` token）+ 本轮重跑一次 install.sh，连续 3 轮无效 → `incomplete_sha` 中毒 + 一条通知；(2) 状态与锁先落 `$HOME`（`~/Library/Application Support/ZelinAIAssistant/`，TCC 永不拦），repo 的 `state/deploy_state.json` 降为尽力投影，镜像多出 `trigger` / `interpreter` / `volume` / `repo` / `unattended_*`；(3) 每轮第一次 git 调用前卷访问探针（stat + 读 + mkstemp），EPERM → `blocked_tcc`、日志点名 plist `ProgramArguments[0]`、通知一天一次、HEAD 不动；(4) doctor `launchd volume access` 行读镜像的无人值守判决或 24h 内的 launchd stderr 证据 → FAIL `deploy_blind_tcc` + 精确修法（完全磁盘访问加 `<解释器>` 与 `~/.local/bin/claude`，然后等 timer 自己触发一轮复验；终端起的运行——含终端里敲的 kickstart——继承终端授权，绿了不算）；trigger 启发式诚实到「有 tty / TERM_PROGRAM = terminal，否则 launchd（timer 与 kickstart 分不出）」。**待 owner**：给 plist 里的解释器授完全磁盘访问（doctor 行给路径），或把 repo 搬回启动盘——D20 Q7 的结构性根治（GUI app 托管）仍待拍板。 | §25 / §55（第四幕指针）/ §56.3 / §56.4 / §56.5 |
| 2026-09-02 | `feat/tag-derived-version-merge-queue` | §56 根治（D17 的发版半边；owner：「一旦一个 main 弄了，其他的就直接自动 rebase」） | **版本真源改为 main 上的 git tag，PR 永不 bump**：`act.__version__` 派生（act/_version.py 盖章 → git describe → 回落值；`act/lib/version.py`），iOS pin 提交占位 `0.0.0-dev` 构建前 runner 上 sed；`tag-on-merge` → `release-on-merge`（最高 tag + 1 patch，label `release: minor|major` 抬档，串行幂等不写分支）；release.yml 从 tag 名盖章、Release 正文 = CHANGELOG `[Unreleased]` 增量、文件不改写；新 required 门 **Version pins untouched**（在飞 PR 按 fork point 过渡放行）；全部 required workflow 响应 `merge_group`（merge queue：`gh pr merge --auto --merge`）；auto-deploy 期望版本走 stamper、fetch `--tags`；install.sh 任何 `import act` 前盖章（step `version`）；doctor 行 `version`。过渡：本 PR 回落行手写为合并将铸的号 + 回落值领先条款，首轮旧脚本部署仍能对上心跳。 | **§0 宪法第 8 条修宪** / §23 / §56.1 / §56.2（改写）/ §56.3 步 2、9 / §56.5 |
| 2026-09-02 | `ci/auto-update-pr-branches` | §56 根治（D17 的合并半边；同一句 owner 原话） | **PR 分支自动跟随 main**：Merge Queue 在个人账户 repo 装不上（ruleset API 拒绝 `merge_queue`，实测），替代 = `update-pr-branches.yml` 每次 push 到 main 把新 main 合进每个在飞同 repo 非草稿 PR（fork / `no-autoupdate` 跳过），auto-merge 在重跑 required check 绿时合并；冲突 → `needs-rebase` 标签 + 一条幂等评论、合干净自动摘。**token 真相**：`GITHUB_TOKEN` 的 update-branch 不触发 `pull_request` workflow（docs + community #26520）→ PR 会停在 Expected 永不合并，所以更新走 fine-grained PAT `PR_AUTOUPDATE_TOKEN`，secret 缺席 = report-only 只打标签。ci.yml 每 job `timeout-minutes`（Windows 30 / 其余 40）——挂死的 informational job 曾占 concurrency group 6 小时。CONTRIBUTING「PR lifecycle」：开 PR → 立刻 `gh pr merge --auto --merge` → 只轮询 required、永不 `--watch`。**待 owner**：创建 PAT 存为 repo secret。 | **§56.6（新增）** / §56.2 追记 |
| 2026-09-02 | `fix/web-typography-parity`（PR #143；无版本 bump，版本由 tag 派生） | P4 前置（D3：web 看板继承原生看板外观；防腐 #10） | owner「文字的粗细」：web 看板字号/字重梯逐字镜像 `mac/Sources`（提案摘要 15 semibold、行标题 12 medium、chip 10 semibold、列头 12 semibold 次级 + 11 bold 计数胶囊、落点行 11 medium、错误句 10、复制行/卡 id/路径 9、composer 12、顶栏小字 10），原生 `.secondary` 统一映射 `--text-secondary`（色值不动）；26 个 `--type-*` token 单源 `web/src/styles/tokens.css` type-scale 块，组件 CSS 只许 `font: var(--type-…)`；对照表 `typeScale.ts` 驱动样式指南第 5 节；vitest 钉 CSS ↔ 表 + board.css 零字面 font-size/weight，`tests/test_web_type_scale_mirror.py` 钉 表 ↔ Swift 源行。阶段性完成卡面补回 delivered_summary 一句。 | §54.1 第 10 项（新） |
| 2026-09-02 | `fix/version-stamp-under-autodeploy`（§56.1 首次实战修复） | §56 根治的第一轮上机（D17） | **v0.48.21 上机那一轮 stamp 步失败、壳报占位 0.1.0**：install.sh `stamp_version` 把 stamper 交给 `command -v python3` = auto-deploy PATH 打头的 Homebrew python3，而 TCC 按每个非平台 binary 单独记账（§55 第三幕；一次性 launchd job 实测：`/opt/homebrew/bin/python3` 打不开外置卷上的 `scripts/version_stamp.py`——`[Errno 1] Operation not permitted`——`/usr/bin/python3` 与 Xcode python 都答 0.48.21）；`2>/dev/null` 吞掉报错，日志只剩「stamp failed」+ `ok (v?)`；同轮 `shell/build.sh` 用同一个 python3 再失败、VERSION 空、Info.plist 占位 0.1.0 装进 /Applications；daemons 反而没事（各自 spawn git 回落）。修法：`stamp_version` 按 §55 daemon 候选顺序（`$AIASSISTANT_PYTHON` 最先）逐个试、第一个盖成的赢、被拒的 `[info]` 点名并带最后一行 stderr、全部失败的 `[warn]` 与 §23 `version` detail 都带 `<解释器>: <stderr>`；**同日 10:14Z 第二轮**：同一 stamp 失败让 09:46Z 手写的 0.48.21 stamp 留在 v0.48.22 checkout 上、新 actd 如实报旧号、脚本按预测把好部署回滚成 `no_heartbeat_from_new_version`——就绪等待改为按 install_report `version=ok:<v>`（install.sh 真盖的号）比对，stamp 步 warn → 只看新 pid + idle + 日志 WARN，下一轮 running_mismatch 重盖章自愈。新 `scripts/build_version.sh`（mac/build.sh + shell/build.sh 共用：候选顺序 → stamper → 同一 stamper 的只读决策退路（不用 `import act`：陈旧 stamp 会先赢，Codex P1）→ 都答不上 **BUILD FAILED**，且在 compile 之前算、不留半成品——永不带占位出厂）；doctor 新行 `board app version`（装好的壳的 CFBundleShortVersionString vs act.__version__，不一致 WARN 指向 `bash install.sh --non-interactive` / 等下一次 deploy）。判例 `tests/integration/test_version_git_fixture.py`（被拒解释器 / 全失败 warn 带 stderr / launchd 式洁净环境 tag 在与不在 / 解析不依赖 cwd）、`tests/integration/test_auto_deploy_script.py`（stamp 失败不回滚 + 自愈）、`tests/integration/test_build_version.py`（含假 swiftc 真跑 shell/build.sh）、`tests/test_version_resolution.py`。 | §56.1 追记、§56.3 第 9 步追记、§23 `version` step detail 追记二、§25 add-only 行 `board app version` |
| 2026-09-02 | `feat/skill-test-code`（skills-only，无版本 bump） | P7 前半（D13 skill 商店约定 + D14 测试 skill v0.2.0 手动模式；R2.7.1 / R2.8.1 / R2.8.3） | **`skills/test-code` 测试代码 skill**：`skills/test-code/scripts/detect.py`（技术栈 / 工具 / 阈值来源——本 repo 读 `qa/gates.toml` 只读、通用项目读 pyproject、零配置用 skill 默认值并注明 Bob-strict = 6；对 **merge-base** 的 diff -U0 + 未跟踪文件 → 新增行触发器；tier 推荐；54 层菜单（core/extended 两圈）含可跑性与时间估计）→ AI 助手按 SKILL.md **问一次**（tier 单选 + 检查多选；headless 记 `recommended, not confirmed`）→ `skills/test-code/scripts/run_ladder.py`（phase 1 静态/自制并行、phase 2 测试/覆盖/变异串行、phase 3 依赖 coverage.json；项目 QA 门 `scripts/qa/*.py` 优先，缺席时 `complexity_min.py` + 内置 CRAP / diff 覆盖 / no-drop；每层超时 档 1 300 s … 档 5 无时限；报告 `.test-code/reports/<UTC>/report.md + report.json`：未跑层三分 N-A / UNAVAILABLE / SUBSTITUTED（替代物永不写 pass）、零-NEW 基线注、fix-first 六级排序、存活变异体 file:line、ASK 记录、工具版本、复跑命令；退出码 0/1/3/2）。触发器加挂层触发即必跑、无判例 = FAIL、waive 须留理由。自制检查 fail closed + 每脚本负控制判例（122 条，含 integration/ 真子进程一份）；skill 脚本自测 CC ≤ 5、CRAP ≤ 6、覆盖 97%。`skills/README.md` 立商店约定（version 字段、默认 off、启用 = 软链接 `~/.claude/skills/<name>`）。首次在本 repo 实跑 档 1 抓到两条真发现：`.github/workflows/pr-review-codex.yml` 的 `actions/github-script@v9` 未 SHA-pin；`docs/CONTRACT.md` 等 20 处反引号路径悬空（多为 tombstone / 运行时路径，可 `--init-baselines` 入账）。R2.8.2 的「agent 收工前自动跑 + 报告附 PR」与 R2.7.2 设置页开关留 P7 后半。 | —（skill = 文档 + 脚本，不改任何法条；§58 阈值单源被 skill 只读遵守） | Owner 09-02 追加并落地：**1–5 档编号**（第 5 档 = 通宵/通几天，无时限）；**核心圈/扩展圈/暂缓圈**（核心必跑、AI 只能多做不能少做——跳过核心层要写理由否则 INCOMPLETE；扩展圈进 `references/catalog.md` 含大厂 presubmit 硬指标）；**结构门**（测试放置、同名模块、目录深度、拥挤目录、import 环、孤儿）；第 5 档 **干净 VM 安装**；报告加**结构性盲区**（如无反馈回路——单次 skill 不做反馈回路，那是 P5 的活）。 |
| 2026-09-02 | `fix/skill-test-code-cross-project`（skills + tests + qa/mutation_targets.toml，无版本 bump） | P7（D14 skill v0.2.1） | **跨项目实跑 + 第 4 档自测**：在 pallets/itsdangerous 上跑第 2 档抓到 3 个真 bug（相对导入误报孤儿、ANSI 颜色让 pytest 收集错误解析失败、空 diff 报 UNAVAILABLE）并修正；对 skill 自身跑变异测试 1,581 体 62% 杀伤（75% 存活体 = CATALOG 时间估计常数 ±1 的等价变异，逻辑存活体逐个补判例）；skill 脚本进夜间变异靶区。本仓库结构账本新增真环 `act.lib.registry>act.lib.store2.export_yaml`（P3 输入）。 |
| 2026-09-02 | `release/1.0.0`（docs + CHANGELOG only；label `release: major` → 合并时 CI 铸 `v1.0.0`，版本 pin 一字不动） | D24 / D25 落账 | **1.0.0 架构基线发版**：CHANGELOG `[Unreleased]` 首行「1.0.0 — 新架构基线 / architecture baseline」+ 自 v0.47 以来的大白话摘要（SQLite 真源、web 看板 + Dock 壳、录制/字幕开关进壳、merge=release=deploy、QA 合并硬门、tag 版本）+ 未进 1.0 清单 + 升级/安装两句——Release 正文 = 相对 v0.48.29 的增量，恰为这段新文字（`scripts/changelog_release_notes.py` 本地验证）；本文新增 D23（无 Merge Queue → 跟随 workflow report-only、PAT 暂缓）、D24（1.0.0 now）、D25（终态验收 = 空白 macOS 一条命令装完看板能用；P8 等 owner）与 **§9 终态验收清单**；README / README.zh-CN 去掉全部「v0.48」字面版本（防腐 #5：版本只从 tag 派生，README 指向 Release 徽章），架构图与「工作原理」按基线改口（SQLite 真源、web 看板 + 壳、旧 app 冻结）。CONTRACT 零改动（无行为变化）。 | —（纯文档；§56.2 既有发版机制照用） |

## 9. 终态验收清单（D25；「空白环境直接能用」的机器可见判据）

**验收事件**：在一台**从未装过本软件**的 macOS 14+ 机器上，按 9.1 前提 + 9.2 命令执行一次，9.3 每一条为真 → D25 通过；任何一条不为真 = 一个 P-卡（修的是产品或安装器，不是验收清单）。已装机器的更新路径按 9.4 验。**截至本节写入（2026-09-02）这份清单尚未在真正的空白机器上执行过**——它是标尺，不是成绩；自动化落点 = `skills/test-code` 第 5 档「干净 VM 安装」层（§2.8）。数字与文件名一律指向 truth 文件，不在此手写。

### 9.1 前提（机器上要有的东西；缺一项 install.sh 会在依赖检查处如实停下）
- Xcode Command Line Tools（`swiftc`，壳与旧 app 都靠它）；系统 `/usr/bin/python3` 可用且装了 PyYAML（运行时白名单 = stdlib + PyYAML，`CONTRIBUTING.md`）；Node.js LTS（构建 `web/` 与 `npx screenpipe`）。
- Claude Code CLI 已登录（`claude --version` 能答）；Anthropic API key 可以装完再贴（贴前雷达安全待机、doctor `anthropic key` 行是 warn 不是 FAIL）。
- 外置卷上 clone 的机器另需 D20 家族的一次性授权（完全磁盘访问给守护解释器与 `~/.local/bin/claude`）；clone 在启动盘 `$HOME` 下则无此步。**验收机器默认 clone 在 `~/Projects/`**——TCC 项不进 9.3 的必真集。

### 9.2 一条命令（就是 README「Quickstart」那一行；不再要求先手抄 config）
- 源码路线：`git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant && cd ~/Projects/zelin-ai-assistant && bash install.sh`（`config.yaml` 缺席时 install.sh 自动从 `config.example.yaml` 建，§23 step `config`；全程无交互提问）。
- 安装包路线：Releases 页最新 tag 的 `ZelinAIAssistant-<tag>.pkg`（未签名，右键打开一次），postinstall 跑同一套步骤。
- 两条路线的产物必须一致（同一 install_report 形状、同一批 launchd label、同一个壳 bundle）。

### 9.3 装完必须为真（机器可读优先，肉眼其次）
1. **安装报告**（truth = `state/install_report.json`，§23）：`steps[]` 里 `version` / `config` / `runtime_python` / `state_dirs` / `launchd` / `ui` / `cron` 全部 `ok`；`app`（冻结的旧 app）`ok` 或 `skipped` 都可、**不计入**验收；不允许任何 `fail`；`agents_loaded` 至少含 `com.zelin.aiassistant.actd` 与 `com.zelin.aiassistant.server`。
2. **版本身份**：`python3 -c 'import act; print(act.__version__)'` == 装的 tag（不带 `+N`、不是回落值、不是占位 `0.0.0-dev` / `0.1.0`）；装好的壳 `CFBundleShortVersionString` 同号（doctor `board app version` 行不 WARN）。
3. **doctor 零 FAIL**：`bash install.sh --check` 里 `version` / `actd heartbeat` / `board server` / `board app version` / `launchd fd limit` / `launchd orphans` 全 ok；`anthropic key` / `claude auth` 允许 warn 直到贴 key；`cron write access` 与 `launchd volume access` 在 9.1 默认路径下不出现或 ok。
4. **看板能用**（壳 = `/Applications/Zelin AI Board.app`，显示名 "Zelin's AI Assistant (Board)"，§54）：Dock 出现图标、**菜单栏无图标**；窗口渲染全部泳道（truth = `server/lanes.py` 的 slug 列表）与顶栏（新鲜度、排序、语言、主题、齿轮）；header 右上出现录制与实时字幕两个开关（说明 `zaiShell` 桥在场，§61）；关窗不退出、点 Dock 重开；`?page=settings` 打开设置页并显示 Claude Code 全局默认模型（§59）。
5. **一张卡走通**（贴 key 之后）：看板 composer 提一句 → 提案列出现 `P-<n>` 卡；批准 → 卡拿到 `R-<m>` 工作编号并进入运行中（§60）；`state/actd.heartbeat` 的 `version` == tag 且新鲜。没 key 时至少：composer 提交后卡进提案列、无 500、`PipelineBanner` 不渲染（`GET /api/health` verdict = ok）。
6. **自动部署已就位**（§56）：装后一个 timer 间隔内（truth = `act/launchd/com.zelin.aiassistant.autodeploy.plist` `StartInterval`）`~/Library/Application Support/ZelinAIAssistant/deploy_state.json` 出现且 `status` 为 `up_to_date`（或 `deployed`），doctor `auto-deploy` 行 ok；这台机器从此由 merge 驱动更新，不再需要人碰。
7. **没有多余东西**：`launchctl list | grep com.zelin` 只列本版模板里的 label（doctor `launchd orphans` 零孤儿）；`crontab -l` 的 screenpipe 链恰一组；`state/` 下无 `*.log` 超过帽（防腐 #4）。

### 9.4 已装机器的更新路径（owner 原话的后半句「在其他电脑上更新这个软件」）
- 合并进 main → CI 铸 tag → 该机器的 timer 在下一轮拉到新 tag → `install.sh --non-interactive`（含 web/dist 与壳）→ doctor 判决 → `deployed`；心跳 `version` == 新 tag；壳被 relaunch 到新 bundle（§56.5）。合并到心跳换号的上界 = timer 间隔 + 装机 + 判决重试窗（truth = autodeploy plist `StartInterval` 与 `scripts/auto-deploy.sh` 顶部常量，不在此手写）。
- 红了会怎样：doctor 新增 FAIL → 回滚到上一 tag、一条通知、`last_incident` 留在仪表与 web 顶栏直到下一次 `deployed`；回滚拒绝（store2 schema 升过）指向 TROUBLESHOOTING「store2 回滚」。
- 手动兜底永远可用：`bash install.sh`（幂等）；`bash install.sh --check` 复验。

### 9.5 明确不在验收范围
- P8：卸载旧 `Zelin's AI Assistant.app`、删 `mac/`、壳接手正式名与 bundle id——等 owner 下令（D3 / D25）。
- P5–P7 的功能（每日循环、素材库、自动 PR 通道、skill 商店设置页）——各阶段自己的完成判据见 §4。
- Windows / Linux 桌面端（§3 明确不做；portable bundle 只保证 headless 管线）。
