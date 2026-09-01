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
| D22 | **模型选择按建议实现：两把旋钮（手/脑）+ 单一 LLM 边界 + doctor 活探针 + 设置页显式「设为」** | 「关于默认模型的选择，按照你的建议来。你先找机会把它 implement，然后我看看效果。」被采纳的建议：(a) app 此前从不传 `--model`，每次 claude 调用都继承 `~/.claude/settings.json` 的 `model`（当时 `claude-fable-5-1[1m]`），一个 EAP 别名退场曾让派工静默全败；(b) 两把旋钮不是一把：`models.dispatch`（claude --bg 派工 agent）与 `models.pipeline`（~8 处分散的 headless `claude -p`：analyze/triage、radar_slack、radar_gmail、quick_capture、merge_review、ask、golden_eval、judge…），各 = `follow`（默认，不传）或显式 canonical id；(c) doctor 探针：显式旋钮做一次最小活调用，失败 = FAIL 一句人话（「模型 X 不可用，派工会全部失败」）；(d) **不**在启动时改写 `~/.claude/settings.json`——设置页显示当前 Claude Code 全局默认，提供显式一键「设为 <id>」，只改 `model` 键、保留其余、先备份；(e) 下拉只列 canonical id（claude-fable-5 / claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5-20251001），自由文本允许但警告别名/后缀（[1m]、*-eap）会消失。 | 09-01 |

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
| #127 R-number 在 detected 时分配 | `decision-needed` | 三个选项(文档化 / 两段 id / 单独 approval 序号)待 owner;方案 2 改 id 契约需迁移 |

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

| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |
|---|---|---|---|---|
| 2026-09-01 | `fix/launchd-fd-storm-heartbeat`(PR-A,#89) | P0 前置止血(审计 L1/L2/L3) | launchd 模板只抬 soft fd 上限(hard 不设,实测 hard 键只降天花板)、systemd `LimitNOFILE=8192:524288`;`failures.claude_blind`(Bun 猜测句 = TCC EPERM,真因)+ `fd_limit` 只留真 EMFILE;doctor `launchd claude` 探针(一次性 launchd job);派发风暴刹车(同类连败 5 次 → `dispatch_halted`,进「需输入」列 + 通知,退避窗口零写零 traceback,进入 approved 的每条路径 + 退回提案都清账);actd 每阶段写 `state/actd.heartbeat`,doctor `actd heartbeat` / `launchd fd limit` / `launchd orphans` 三探针,`GET /api/health` + web `PipelineBanner`;install.sh `launchd_retire` 自证 + `launchd_orphans` 报告。本节 + D17–D20 + §5 同 PR 入库。 | §2 / §4.1 / §25 / §47.4 / §49 / §55 |
| 2026-09-01 | `feat/store2-source-of-truth`（PR-D，#126，v0.48.8） | P1（D2）+ 前置 #119 | **store2 接线为真源**：actd 首 pass 激活协议（整目录备份带 sha256 manifest 永不覆盖 → 从备份迁移 → 导出 → 逐字段比对 → 零差异 + 无并发 YAML 写才写标记；任何差异 = 删库拒绝 + doctor FAIL，无半态）；registry 门面公开 API 双后端逐字一致（callers 零改动）；每日 YAML 导出镜像 `state/registry-export/`（prune 常开）；多进程写者走 BEGIN IMMEDIATE 事务（跨进程判例）；agent 转移墙实际生效（DB trigger + Python 墙，actd 级判例）；白名单接线补行 5 条 + origin_trust 触发器改「只禁升档」；回滚开关 `registry.backend: yaml` 保留一个版本（TROUBLESHOOTING「store2 回滚」）。**同版落地 #119 需输入退役**：受阻/放弃救活的会话按 stop_to_review 收割进待验收（interrupted 标记 + msg_review_interrupted），answer_input/executor.answer/extract_question/msg_needs_input 全退役，needs_input[] 只剩 §4 刹车行。 | §0.1（显式精确化）/ §1 / §2 / §5 / §6 / §24 / §39（tombstone）/ §44.7 / §46.3 / §53（整节改写） |
| 2026-09-01 | `feat/digest-frequency-knob`(PR #123,v0.48.5) | D19 落地(审计 L7) | `digest.frequency` 旋钮 `off/daily/every2days/weekly` **默认 off**(config + overrides 扁平键 `digest_frequency`);crontab 行改为每天 09:07 不带 `--now`,模块按滚动间隔自闸门(标记 `state/digest.json`),off/未到期静默;doctor 「cron digest」看见旧的 `--now` 行即 WARN;文案 「周一 digest」→「状态摘要」;weekly digest 默认 off、launchd 每小时唤醒静默;automation-idea 提案卡退役且管道代码同版删除(防腐 #6);两个标记写失败只打一行、卡仍落。**UI 未到**:两把旋钮都还没有设置页可点(Mac 不加功能 D3,web 设置页 = P4),P4 前经 config.yaml / overrides 手改。 | §16 / §17 / §24 / §40.7 |
| 2026-09-01 | `docs/issue-triage-2026-09-01`(docs only,无版本 bump) | tracker hygiene(§5.1 执行) | GitHub 处置落账 §5.5:关 6 个 issue(#10 #22 done;#17 #9 #13 #26 superseded)、#7 留开标 `mac-retire`、七枚新 label 与 17 个 issue 的归类;dependabot #108–#117 八合二关(actions 五 major + jsdom 30 + vitest 4 + TypeScript 7 进 main;plugin-react 6 ignore-major、react 19 关不 ignore,两者归 P4 一次联动);§4 新增 **P5b 会议 recap**(#129,owner 拍板)与 **P6 附注 AI 完成度评语**(#128,proposal)。 | —(纯文档) |
| 2026-09-01 | `feat/model-settings`（D22，v0.48.11） | 横切（P4 设置页首个 section 提前落地） | **`act/llm.py` 真实存在**：`run()` / `dispatch_argv()` / `probe_argv()` 单一边界，10 处 headless `claude -p` + executor 4 个发射点全部经它构造 argv（follow 时逐字节不变，判例逐 site 钉住；`--model` 只拼一处；`_runner_env` 搬入为 `llm.runner_env`，跨模块 `_私名` 引用清零）；两把旋钮 `models.dispatch` / `models.pipeline`（config.yaml 块 + overrides 扁平键，坏形状回落 follow；actd 每 pass 现读两字段，无需重启）；doctor 三行（`claude code model` 永不 FAIL / `model dispatch` / `model pipeline` 活探针，`model_unavailable` 新 failure id，Swift 镜像句）；server `GET/PUT /api/settings/models`、`GET/POST /api/claude-code/default-model`（PUT 进四闸；diff-write overrides；只改 `model` 键 + `.bak-<ts>` 备份 + 坏文件 409 `CONFLICT`）；web 首个设置页 `?page=settings`（顶栏齿轮）section「模型」。 | §15 / §25 / §49 / **§59（新）** |
