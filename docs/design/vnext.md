# v-next 设计落档（repo 内契约誊本 + 修宪草案）

状态:**DRAFT**(day-1 build,2026-08-30)。本文是外部 build contract(`dashi-research/BUILD-CONTRACT.md`)的 repo 内浓缩誊本 + D1 修宪草案。**本文不是法典**——与 `docs/CONTRACT.md` 冲突时以 CONTRACT 为准;本文草拟的 §49 在修宪 PR 合入 CONTRACT 之前无法律效力。证据链:`dashi-research/SRC-SYNTHESIS.md`、`src-verify.md`(源码级核验,repo 外)。

## 1. Owner 决策(2026-08-30 锁定)

同仓演进 · 可见优先 · web 看板(D1)· SQLite(D2)· 信任矩阵:手打自动/外部要批(D3)· 商业化方向(D4)· 待办与运行中合并 · 成本上限默认 $5 · 分享 = 访达定位拖拽(无云文档集成)。

历史注:更早的 SYNTHESIS 终裁曾把「SQLite / HTTP server / web UI」列入永不做清单——那份清单**不是宪法文本**,已被上述 owner 决策显式推翻;推翻依据是源码级复核(修宪工程代价远低于当初假设)。不随架构迁移而动的资产(§45 出生管制、`fence_untrusted`、triage 三选一闸门、T0/T1/T2 审批语义、可逆操作矩阵)全部保留,见 §5。

## 2. 架构(同仓演进,三步)

- **PR1(本 worktree,team UI)**:`server/`(Python stdlib HTTP + SSE,独立进程)+ `web/`(Vite + React 18 + TS 看板)。**核心思路:新 UI 是现有两文件契约的又一个客户端**——读 `state/dashboard.json`(投影)+ 只读 `act/registry/*.yaml`(详情增补),写 `state/inbox/*.json`(动作)。actd 一行不改,`act/` 零 diff,生产零风险。
- **PR2(team store)**:`act/lib/store2/`(SQLite schema + CAS store + YAML 迁移/导出),**不接线**——actd 不 import store2。状态机进 DB:trigger 校验 `(old_status, new_status, actor_type)` 合法转移;`actor_type='agent'` 的 approve/accept 类转移直接 RAISE(D3 权限墙的数据库层)。dispatches 表独立于 cards(runtime 字段绝不焊进核心表)。
- **PR3(未来,另案修宪)**:接线 + per-boot instance token + Mac app 降薄壳。不在本次范围。

```
zelin-ai-assistant/
├─ act/                  # 现有管线(PR1 完全不动;PR2 只加 lib/store2,不接线)
├─ server/               # 新:Python stdlib HTTP + SSE(独立进程,可独立运行)
├─ web/                  # 新:React 看板
├─ mac/                  # 不动(未来降薄壳)
├─ scripts/dev-preview.sh# 新:种 demo 数据 + 起 server + 开浏览器
├─ NOTICE                # 新:Apache-2.0 搬运登记(dashi-taskboard fork 清单)
└─ docs/design/vnext.md  # 本文
```

## 3. 信任矩阵(D3,「手打自动/外部要批」)

两个正交维度,两道墙:

| 维度 | 规则 |
|---|---|
| **来源信任(origin_trust)** | 手打来源(quick capture / 直跑框 / owner 本人 loopback 输入)→ 可自动派发;**外部来源**(陌生 email / Slack 等外来信号铸的卡)→ **永远等人批**,auto-dispatch 调度器跳过 `origin_trust: external` 的卡。这属于 actd 调度逻辑,不属 HTTP 层。 |
| **执行者权限(actor_type)** | `agent` actor 永不 approve / accept / reject——approve/accept 类转移仅限 user actor + loopback。执法分层:server 端 ACL(PR3 接线时 route dispatch 前判)+ DB trigger(PR2 store2,数据库层 RAISE)+ prompt 规则保留当第三道纵深。 |

PR1 现状:**根本不存在 agent 写通道**——唯一写入身份是 127.0.0.1 上浏览器里的本机用户;T0/T1/T2 审批语义原样(T2 批准走 typed-confirm,§41 confirmT2 对齐)。

## 4. 看板列与 lane merge(owner 决策:待办与运行中合并)

列 = 审批状态机的投影(dashboard.json 分区 → 列):

| 列 | dashboard 分区 | 备注 |
|---|---|---|
| 潜在任务 | `debt[]`(detected/备选) | 折叠侧条 |
| 提案 | `needs_approval[]` | 顶部 capture 输入框(propose 模式) |
| 运行中(**合并列**) | `running[]`(含混入的 queued 项,§2 v0.10)+ `needs_input[]` | queued 子状态灰卡 + 原因 chip「排队中 · 等 R-xx / 等预算」;working 卡 sheen 动效;needs_input 行排最前(§41 判例);顶部 direct-run 输入框(`capture mode:"run"`,§34) |
| 待验收 | `review[]` | |
| 阶段性完成 | `completed[]` | |
| (单独页)回收站 | `trash[]` + `archived[]` | 恢复/pin/unarchive |

**UI 语义红线:没有拖拽换状态**。所有状态转移都是显式按钮动词,一一对应既有 inbox 动作(§10 全集);动效可以有,语义不行。卡片动词 = Mac app 现有集合:提案卡(批准/拒绝 fork/修改=comment/暂缓=defer)、运行卡(评论/回答 answer_input/停止 fork)、验收卡(验收 accept/打回 rework/复制成稿)、完成卡(退回验收 revert_review/永久完成 archive)。

## 5. 随迁移保留的不变量(修宪 PR 之外一律保持)

1. **§45 屏幕内容永不铸卡**(回声环的一刀,性质测试钉死)——web 面不引入任何新的发起渠道,capture/直跑框是既有显式渠道。
2. **外来文本入 prompt 必过 `sanitize.fence_untrusted`**(宪法第 5 条)——web 面不组装 prompt,天然合规;未来任何评论中继特性必须保留围栏。
3. **triage 三选一闸门** + **T0/T1/T2 审批语义**(T2 = typed-confirm)。
4. **可逆操作矩阵**(trash/archive 记 prev_status、fold note 可拆出,宪法第 2 条)。
5. **隐私:本地优先**——新增网络面仅 127.0.0.1(硬编码 bind),交付物路径一律 server 端从卡片记录推导,绝不接受客户端原始路径;无新增上传面。
6. **registry 单写者**(宪法第 1 条)——server/ 只读 registry + 写 inbox 回执,绝不落盘卡片。
7. **字段 add-only**——/api/board 原样透传 dashboard.json,/api/cards 详情增补只加不改投影字段名。

## 6. server/ 面(浓缩 spec)

- bind **127.0.0.1 硬编码**,端口 env `ZAI_PORT` 默认 47820;PR1 无 token(localhost 单用户过渡,代码留 `# TODO(PR3): instance token` 挂点);body 上限 1MiB。
- 错误 envelope 统一 `{"error":{"code":"...","message":"...","details":{}}}`,codes:`UNKNOWN_FIELD` / `INVALID_FIELD` / `NOT_FOUND` / `INTERNAL_ERROR`;未知 JSON 字段一律 400 `UNKNOWN_FIELD`(zero-tolerance)。
- `GET /api/board` = dashboard.json 原样透传;`GET /api/cards/{id}` = PyYAML 只读解析 registry(含 archive/ fallback)增补 plan/DoD/sources 引文/fold notes/execution 元数据,add-only 合并。
- `POST /api/actions`:动词白名单严格 = live CONTRACT §3/§10 现有清单(先例:`act/webui.py` 的 `ALLOWED_ACTIONS`/`_INBOX_KEYS` 闸门,§41);JSON 形状、文件命名、`inbox_stem` 幂等、tmp+rename 原子写与 Mac `Store.swift` 逐字节等价。**多一个字段都不发明。**
- `GET /api/events`:SSE,事件仅 `board.updated {generated_at}`,25s heartbeat 注释行,无重连契约——客户端断线全量 refetch;300ms mtime 轮询 dashboard.json 触发。
- `GET /files/deliverables/{card_id}/{name}`:交付物静态服务,路径 server 端推导,目录穿越/NUL 全拒;`POST /api/reveal {card_id}`:server 端推导路径后 `open -R`(macOS 访达定位;非 darwin 501)。
- web 侧:HTML 交付物只经 `<iframe sandbox="allow-scripts">`(**绝不加 `allow-same-origin`**);markdown 走 fork 的 MarkdownDocument(mermaid 沙箱 + DOMPurify hooks)。

## 7. 依赖白名单与 fork 纪律

- Python 运行时 = stdlib + PyYAML,一个都不许加(server/ 纯 stdlib)。web/ npm 运行时依赖 = `react`、`react-dom`,到此为止;dev 依赖限 `vite`/`@vitejs/plugin-react`/`typescript`/`vitest`/`jsdom`/`@testing-library/react`。禁 UI 框架、状态库、css 框架。
- Fork 来源 = `chuspeeism/dashi-taskboard`(Apache-2.0,v1.1.14 @ 9c09726);凡搬运在根 `NOTICE` 登记,搬运文件带 header「Forked from dashi-taskboard (Apache-2.0) — see NOTICE」;**绝不搬其 17 个正则测试文件**,每件 fork 自带行为测试。清单见 NOTICE(tokens/动画/MarkdownDocument/TaskPropertyPicker/issueRoute+taskFilters/i18n 模式)。

## 8. 修宪草案(DRAFT — D1 的宪法账,修宪 PR 时逐字搬入 CONTRACT)

### 8.1 D1 触及的 CONTRACT §§

| § | 触及方式 |
|---|---|
| §2 dashboard.json | **新增一个 reader**(server/ 透传)。投影形状零改动,add-only 原则原样。无需修法,§49 登记即可。 |
| §3 + §10 inbox | **新增一个 writer**(server/inbox_writer)。动作动词与 JSON 形状零新增——严格复刻既有清单与 Store.swift 产物。无需改 §3/§10 正文,§49 登记 server 为合法 inbox 客户端。 |
| §41 三端动作一致性 | 新 web 看板继承 §41 全部语义:T2 具名/键入确认、停止 fork(退回提案/去待验收)、拒绝 fork(不想做/已办完)、动作白名单入站闸门(webui 先例)。§41 无需改;§49 引用之。 |
| §44 单写者 | **明确不触及**——server/ 属 blessed inbox-client 类(同 Mac app / act/webui.py / iOS / syncd),绝不写 registry。 |
| §45 出生管制 | **明确不触及**——web 面无新发起渠道。 |
| §49 | **新增**(编号预留,见 8.3)。 |

### 8.2 D1 触及的宪法条款(§0)

- **第 1 条(单写者):PR1 无修**。server/ 只读 + 写 inbox,是既有合法客户端类。未来 PR3 若 server 并进 actd 进程 / store2 接线,须把第 1 条重述为「单写者进程」——那是独立修宪案,不在本案。
- **第 7 条(运行时零重依赖):无修,但需一句解释性 clarification**(写进 §49,不动第 7 条本文):server/ 纯 stdlib;web/ 的 npm 依赖属构建侧,交付物为静态文件;`react`/`react-dom` 是浏览器侧 bundle 的组成,不属 Python 管线运行时——运行时白名单 stdlib + PyYAML 不变。
- **第 9 条(隐私分层):触及但不破**——新增一个本地网络面。§49 固定三道闸:loopback-only 硬编码、交付物路径 server 端推导、零新增上传面。第 9 条本文不改(新面不出本机,不触「上传面」)。
- 其余条款(2/3/4/5/6/8/10/11)不触及;第 5 条(围栏)与第 2 条(可逆)作为 §5 保留不变量随迁移显式带走。

### 8.3 新 §49 提案措辞(DRAFT;编号 49 预留——CONTRACT 现行至 §48,编号永不复用)

> ## 49. v-next web 面 — 两文件契约的又一客户端(server/ + web/)
>
> **地位**:`server/`(Python stdlib,独立进程,非 actd)是 dashboard.json 的 reader + inbox 的 writer,与 Mac app / act/webui.py / iOS 同属客户端类;**绝不写 registry/dashboard**(宪法第 1 条不动)。`web/` 是其静态前端(React,构建产物)。
>
> **网络面**:硬编码 bind 127.0.0.1,端口 env `ZAI_PORT` 默认 47820;交付物路径一律 server 端从卡片记录推导,绝不接受客户端原始路径;本面零上传、零云端(宪法第 9 条口径)。PR1 无 token(localhost 单用户过渡);instance token 随 PR3 落地,届时本节 add-only 增补。
>
> **入站闸门**:`POST /api/actions` 动词白名单 = §10 现有全集(webui §41 同款纪律),JSON 形状/文件命名/`inbox_stem` 幂等/原子写与 Store.swift 逐字节等价;未知 JSON 字段 400 `UNKNOWN_FIELD`。**web 面对 wire 契约零新增字段。**
>
> **UI 语义**:看板列是审批状态机的投影——无拖拽换状态,一切转移 = 显式按钮动词;T2 批准走键入确认(§41 confirmT2);HTML 交付物只经 `<iframe sandbox="allow-scripts">` 渲染(永不 `allow-same-origin`)。
>
> **依赖澄清(宪法第 7 条执法注)**:web/ 的 npm 依赖属构建/测试侧;Python 运行时白名单 stdlib + PyYAML 不变。
>
> **随迁移保留的不变量**(本节存在不松动它们):§45 屏幕永不铸卡、`sanitize.fence_untrusted`、triage 三选一闸门、T0/T1/T2 审批语义、可逆操作矩阵、registry 单写者、字段 add-only。

## 9. TODO(contract) 清单(本文自己的未决项)

- TODO(contract): §49 编号已预留但**未写入** docs/CONTRACT.md——修宪 PR(集成之后、接线之前)才落法典;在那之前本文 8.3 仅为草案。
- TODO(contract): server 动词白名单的最终集合以 A3(inbox_writer)读完 live §3/§10 + Store.swift 后的实现为准;若 A3 收窄为「PR1 UI 实际用到的动词子集」,本文 §6 与 §49 草案须同步收窄。
- TODO(contract): 宪法第 7 条的 react/react-dom「构建侧」定位措辞(8.2)需 owner 在修宪 PR 里确认——是写进 §49(推荐,条文不动)还是直接给第 7 条加括号执法注。
- TODO(contract): `origin_trust` 字段(§3 信任矩阵)属 PR2/PR3 范围,字段名与取值枚举尚未进 CONTRACT——store2 接线的修宪案一并立法。
- TODO(contract): NOTICE 中 fork 目的地路径按最终落地文件名 reconcile(integration agent 终裁)。
- TODO(wiring): wiring PR must verify actd validates feedback/capture image paths are under state/attachments/ before LLM attachment (review-ui finding 7).

## 10. Theme — 调色板继承（Task P：web 深色 = Mac app 现役外观）

来源盘点：`mac/Sources` 全部颜色都是 SwiftUI 系统语义色（`.green/.red/.blue/.purple/.orange/.teal/.gray/.yellow` + `.primary/.secondary` 透明度层），无自定义 hex、无 asset catalog。因此 dark 主题取 macOS dark mode 的系统色解析值；窗口底色族按 owner 截图基准（蓝灰 ~#1b1d23）。light 主题保持原结构，同色相家族按白底对比度加深（chip 文字 WCAG-ish ≥4.5，大件 ≥3）。token 架构（dashi fork 的变量名/分层）不动，只改值；实现见 `web/src/styles/tokens.css` 三个块（light / `[data-theme="dark"]` / `prefers-color-scheme` 兜底，后两块逐值一致）。

| 语义 | Mac 源（SwiftUI） | dark token 值 | light token 值 | token |
|---|---|---|---|---|
| 批准 / 验收通过 | `.green`（systemGreen） | `#32d74b` | `#218739`（加深保对比） | `--success`（`.btn-success` / `.chip-success`）、`--status-review` |
| 拒绝 / 危险 | `.red`（systemRed） | `#ff453a` | `#d70015` | `--danger`（soft: dark rgba(255,69,58,.14) / light #fcecec） |
| 需输入 / 警告 / working / 打回 / 停止 | `.orange`（systemOrange） | `#ff9f0a` | `#c05d00` | `--warning`（`.btn-warning` / `.chip-warning`）、`--status-progress`、`--priority-high` |
| 修改 / 研究并提议 蓝 | `.blue`（systemBlue） | `#0a84ff` | `#0a66cc`（加深保对比） | `--info`（`.btn-info`）——验收单补齐的 blue 槽位 |
| 交付 / done / tier 紫 | `.purple`（systemPurple；注：交付 badge 源码为 `.blue`，本表与 owner 验收单拍板紫） | `#bf5af2` | `#9440d6` | `--status-done`、`--purple`（`.chip-purple`） |
| 等待 / notice 黄 | `.yellow`（systemYellow） | `#ffd60a` | `#8a6d00`（加深保对比；调底用亮锚） | `--notice`（`.chip-notice`）——验收单补齐的 yellow 槽位 |
| queued / backlog 灰 | `.gray`（systemGray） | `#7c7c81` / `#98989d` / `#85858a` | `#8e8e93`（三者同值） | `--status-backlog` / `--status-todo` / `--status-canceled` |
| accent（会话活动 / 主按钮） | `.teal`（systemTeal） | `#6ac4dc`（hover `#7ecfe4` / active `#8ed2e4`） | `#12758c`（hover `#10677b` / active `#0e596a`） | `--accent` / `--accent-hover` / `--accent-active` / `--accent-soft` |
| accent 底上前景 | — | `#0c2a33`（亮 teal 上白字对比不够 → 墨字） | `#ffffff` | `--on-accent`（自本次起随主题取值） |
| 窗口底 | 截图基准 | `#1b1d23` | `#fafbfc` | `--bg` |
| 侧栏（更暗） | 截图基准 | `#17191e` | `#f1f3f6` | `--sidebar-bg` |
| 卡片面（略亮） | 截图基准 | `#23262e`（raised `#282b34`） | `#ffffff` | `--surface` / `--surface-raised` |
| muted/hover/active 面 | — | `#2a2d36` / `#313540` / `#3a3f4b` | `#f1f3f6` / `#f4f6f9` / `#e9ecf2` | `--surface-muted/hover/active` |
| 列头 / 顶栏 | — | `#20232b` / `#1f2229` | `#f6f8fb` / `#fafbfc` | `--column-header` / `--header-bg` |
| 文字四层 | `.primary/.secondary` | `#edeef2` / `#b4b9c2` / `#858c99` / `#636a78` | `#1a1c22` / `#5a5f6b` / `#959ba7` / `#b1b6c1` | `--text-primary…quaternary` |
| 紧急优先级 | `.red` 家族 | 两主题共享 `#e5484d`（:root 单点定义，dark 块历来不覆写 priority） | 同左 | `--priority-urgent` |

语义核对（每个动作保色义，owner 验收单一比一）：approve/accept=green ✓（btn-success / chip-success / status-review）、reject/delete/硬需求/紧急截止=red ✓（btn-danger / chip-danger，紧急截止走 chip-outline 红字档）、modify/raise=blue ✓（btn-info，--info）、defer/archive=gray ✓（中性 .btn）、send-back/stop/answer/needs-input=orange ✓（btn-warning / chip-warning / status-progress）、copy-draft/back-to-review=teal ✓（btn-accent）、tier 章/交付 tag/已并入=purple ✓（chip-purple / zai-chip--merged；交付 badge 源码为 .blue，验收单拍板紫，styleguide 第 1 节保留 ⚠️ 记录）、waiting=yellow ✓（chip-notice，--notice）、queued=gray ✓（status-backlog/todo）、lineage ↳ 改进=teal ✓（zai-chip--improves）。旧注记 ①（无 blue 槽位）与 ②（单主色按钮架构，批准走 teal）已被 owner 验收单 sanctioned enrichment 取代：每个语义 hue 一套三档阶梯（filled 按钮 / tinted chip 底 / outline+文字）+ token 化的 hover/active（light 逐级加深、dark 逐级提亮）+ 权重分层（状态 chip 重档 / lineage chip -soft-quiet 安静档），全部 hue 不变；filled 仍只保留 btn-primary（--accent），语义动词按钮用 outline 档。

token 旁路清理：`board.css` btn-primary 的 `color:#ffffff` → `var(--on-accent)`；`detail.css:289` iframe 白底为**有意保留**（交付物 HTML 自带配色，浅底兜住透明页面，与主题无关）；`animations.css` 无硬编码色（注释里的 hex 是 fork 差异说明）。
