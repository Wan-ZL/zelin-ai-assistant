# vnext 修宪草案（ratification-ready）

> **状态：已入典（v0.48，2026-08-31）**。本文全部草案已按 M8.1 地图落进 `docs/CONTRACT.md`，编号对照：PR1/M8.2 → **§49**（web 面）；M1.a + W17 + T-28/T-29 → **§50**（信任矩阵 + effective tier + ingress 落款）；M1.b + M1.c + §M6.2 + C-2/C-6 → **§51**（自动派发天花板 + queued 词表）；M5 → **§52**（boardctl + board-agent skill）；store2（T-7~T-16 终裁）→ **§53**（休眠地基，NOT YET WIRED）；shell 薄壳 → **§54**；W-steer/M2 + §M6.1 + C-3/C-4 → **§44.3-S**（含 §2 v0.48 字段块、§32.2 comment 白名单扩 executing、§39.2 steer 家族追记）；W18 → **§41 修订**（+ §31 追记、§34 引用注）；W17 引用注 → **§7**；W1.a → **§38.4**；W1.c → **§10 修订**；T-17 → **§3 追记**；T-18 → **§10 追记**；F1/F2/F3 → **§14 追记 / §31 修订 / §32.4**；宪法 localhost 例外 → **§0 第 9 条修宪段**。本文自此转为历史证据链——与 CONTRACT 冲突时以 CONTRACT 为准。
>
> **已知偏离（v0.48.7）**：M1.b/M1.c 与 §M6.2/C-2 里的预算天花板（`daily_budget_usd`、`today_spend` 台账、`budget`/`waiting_budget` 排队原因、`cost:over_ceiling`/`budget:*` token）已按 owner decision D9（`docs/design/vnext2-plan.md`「取消一切预算」）整套退役，CONTRACT §51 留 tombstone。下文相关段落只作历史记录。

本文件收集 v-next wire 团队的全部法条改动草案。原 build PR **不动 `docs/CONTRACT.md`**；每节按可直接并入 CONTRACT 的措辞书写，owner 批准后由 integrator 落入正文（编号沿 CONTRACT 惯例分配新 §，永不复用旧号）。各 builder 只追加自己的小节，不改别人的。

---

## W1 — triage 清单配额反转 + auto-archive 默认 30 天（owner 已拍板）

### W1.a 清单窗口配额反转（修订 live CONTRACT v0.20.0 §2 的 pinning 语义）

**原法条**（live 树 v0.20.0，critique high #5）：triage/capture LLM 的注册表清单窗口 cap = 60；非归档的 delivered/merged 卡 **HARD-PINNED** 全量进窗口，其余（open）卡按 R-号 recency 竞争剩余槽位。

**病灶（W1）**：registry 长大后 delivered/merged 数量无界，pinning 吃满 60 槽，open 卡——triage 唯一真正需要匹配的对象——被挤出窗口，LLM 看不见它们，于是重复建卡、该折叠的折不进去。

**新法条**：配额反转。open 卡（非 delivered / merged / trashed / archived）获得**保证槽位**，永不掉出窗口——open 卡总数超过 cap 时窗口整体超 cap（cap 对 open 卡是目标值不是硬顶）；delivered/merged 按 recency（R-号降序）只填剩余空位，且受独立硬上限 `_CLOSED_RECENCY_CAP = 20`。re-raise recall 的兜底从「LLM 窗口必见」改为「确定性 thread_key 归并」（W1.b）。实现落点：`act/lib/quick_capture.py` `_inventory_reqs()` / `_INVENTORY_CAP` / `_CLOSED_RECENCY_CAP`。

### W1.b 确定性 thread_key 兜底（材料性基线差异，记录不擅补）

live 树 v0.20+ 的 `registry.derive_thread_key` 给带外部 thread 引用的来源派生一个 STRONG 确定性分组键，`merge_or_new` 用它把同一外部 thread 的 re-raise 确定性归并——这就是 W1.a 允许老 delivered/merged 卡掉出 LLM 窗口的安全网。store2 schema（PR2，本 PR 不接线）以 `sources(channel, origin_key)` partial-unique 键承载同一语义。**本 worktree 的 v0.10.3 基线没有 `thread_key` 字段**：在 actd 接线把 live 的 thread_key 机制（或 store2）移植进来之前，一张掉出 recency 窗口的老 delivered 卡收到 follow-up 时存在铸重复卡的残余风险；缓解 = `_CLOSED_RECENCY_CAP = 20` 保证最近 20 张 closed 卡始终可见。该移植是 actd 接线点，不在本 PR 内擅自实现。

### W1.c auto-archive 默认值 0 → 30（修订 live CONTRACT v0.20.0 archive 条款）

**原法条**（live v0.20.0）：`archive_stale` 首发默认 off（`archive_after_days = 0`），理由是长期静默的移民/EB-1A matter 不能被自动封存、新邮件到来时 re-open 重复卡。

**新法条（vnext 决议）**：W1.a 配额反转后，冷 delivered 卡留在 active registry 的代价变成挤占 closed recency 槽位（20 个），冷卡越多、近期 closed 卡越早被挤出——所以默认改为 **30 天自动封存**（`archive_after_days = 30`；设 0 = 关闭，恢复原行为）。原有全部保护保留：只封存冷 `delivered`、跳过带未来 deadline 的卡、跳过 cluster 内有 open sibling 的卡、时间戳不可解析的卡永不自动归档（保守）、once-per-24h sweep gate。

**基线差异（材料性）**：v0.10.3 基线没有 `State.ARCHIVED`、`archive_stale`、`registry.archive` 家族与 `act/registry/archive/` 目录。本 PR 只落配置默认值（`act/lib/config.py` `Config.archive_after_days = 30` + `load_config()` 读 `archive.after_days` + `config.example.yaml` 注释）；sweep 本体 = actd 接线点，接线时按 live `act/actd.py archive_stale`（`getattr(cfg, "archive_after_days", 0)` 需改为直接读 `cfg.archive_after_days`，因为本基线 Config 已显式携带该字段）与 registry archive 家族移植。

---

## W-steer（M2）— 追加指令中继（steer relay，owner 已拍板）

### 新法条（ratification-ready；建议编号沿 CONTRACT 惯例取新 §，语义挂靠 §44.3 送达点）

**语义**：owner 在 EXECUTING 卡上的 `comment` 动作不再是「折叠评论 + 退回重批」，而是对 live session 的中途转向指令（steer）：入队 `execution.pending_steers`，由 actd 在 §39.2 安全窗口（roster blocked，或会话已死的 resume 时机）经既有 §44.3 briefing 送达点 flush 进会话；working + live pid 绝不打断。状态机零改动（卡保持 EXECUTING，不翻 rework、不动 status）。

**note 形状（新 steer class）**：`{class:"steer", text, ts, key}`，`key = <ts>|<sha256(text)[:16]>`——**dedup 键带时间戳**：同 (ts, text)（inbox 文件重放）去重；同 text 新 ts（owner 重申/催促）是**新指令**。与 §44.3 briefing 的纯文本去重语义就此分道。去重查 pending 与已投递台账（`delivered_steers` 环形 20，§44.3 delivered_briefings 判例）双份。class 字段与 store2 `notes` 表（comment/steer/fold）形状对齐（本 PR store2 不接线，字段先对齐）。

**信任级别**：steer 文本 owner 亲打（信任矩阵 trusted 起源）——投递 prompt = `OWNER UPDATE:\n` + 逐条列点 + 尾注（course correction for CURRENT task / not a new task / not a rework）；**不过 `fence_untrusted`**（围栏是给外部内容的），runner 侧 secrets scrub 照旧（防泄密不防注入，两回事）。

**wire 字段（`execution.*`，全部 add-only）**：`pending_steers`（note 队列，cap 10，溢出挤最老一条 + notes 留痕）、`delivered_steers`（key 环形 20）、`steer_queued` / `steer_delivered`（时间戳环形各 cap 10——board「已排队/已送达」诚实投影的数据源）、`steer_count`（累计送达）、`last_steer_at`、`steer_attempts`（每批 3 次放弃，§44.3 同款）。

**诚实处置（§39 红线延伸）**：任何丢弃路径（3 次注入失败 / 队列溢出挤出）都在卡 notes 留 `[<date> 追加指令未送达] <原因>；原文：<截 200>`（§39.2「回答未投递」冻结行文法的 steer 变体），调用点补 notify + analytics——owner 打的字绝不静默蒸发。文本上限 4000 code points（§39.2 同款），超限截断保头部；非 str / 空白 fail-closed 不入队。

**与 briefing 共存（v0.47 接线时）**：同一卡同时有 `pending_steers` 与 `pending_briefings` → steer 先 flush（owner 指令优先于 FYI）；两批各自独立 stop+resume，**不混进同一个 prompt**（信任级别不同，围栏边界不能混）。

### 实现落点与 actd 接线点（integrator 用）

- 模块：`act/lib/steer.py`（纯函数，无 I/O——§44 单写者纪律：save/notify/analytics 全归 actd 调用点）；测试 `tests/test_steer.py`（21 例）。
- 接线 1（入队）：`act/actd.py::_apply_decision` 的 comment 分支特判 `req.status == State.EXECUTING.value` → `steer.enqueue_steer(req, comment, ts=decision.get("ts"))` + `save(req)` + log（返回 None = 重放/垃圾,log noop）；**不再**走 `_fold_comment` + 退 CARD_SENT。`process_inbox` 需把 decision 的 `ts` 传进 `_apply_decision`（现签名只带 action/comment）。
- 接线 2（flush）：`reconcile_executing` 的 blocked 分支（actd.py ~L539）与 dead-resume 分支（~L600）：`pend = steer.pending_steers(req)` 非空时——`steer.give_up_due(req)` → `tags = steer.drop_trace(req, pend, "3 次注入尝试失败")` + save + notify(tags)；否则经 stop-idle-then-resume 管道（executor.rework 同款：stop_session + `--resume` + `sanitize.scrub`）投 `steer.build_steer_prompt(pend)`，成功 → `steer.mark_delivered(req, pend)` + save，失败 → `steer.record_attempt(req)` + save（队列保留，下 pass 重试）。
- 接线 3（投影）：`steer.steer_status(req)` 给 dashboard/board 的 running 行做「已排队 N / 已送达」chip（脏数据容忍，绝不抛）。

### 基线差异（材料性，记录不擅补）

- 本 worktree v0.10.3 基线**没有** §44.3 briefing 机制（无 `pending_briefings` / `executor.brief` / `executor.answer` / `_briefing_window_open`），也没有 `sanitize.fence_untrusted`——steer.py 因此自带独立队列记账，flush 管道暂借 executor.rework 的 stop-idle-then-resume。v0.47 接线时应改挂 `executor.brief` 同一送达点，并移植 `_briefing_window_open` 的 last-responsible-moment fresh roster 探测（pass-start 快照可能已数分钟旧，绝不 stop 一个已回到 working 的会话——live §39.2 判例）。
- v0.10.3 的 comment 分支对**任何**状态都退 CARD_SENT（含 executing）；live v0.47 的 comment 走 fold 机制、语义也与基线不同。接线以本节新法条为准：EXECUTING → steer，其余状态保持基线行为。

---

## M5 — agent 有界通道（boardctl + board-agent skill）

### 新法条（ratification-ready；建议取新 §，语义挂靠信任矩阵与 §44）

1. **通道定义**：headless agent 面向看板的唯一合法接口是 `act/boardctl.py`（读 = `GET /api/board`、`GET /api/cards/{id}`；写 = `POST /api/actions` 且动词仅 `capture` 与 `comment`）。agent 不得直接读写 `act/registry/*.yaml` 或 `state/inbox/*.json`——§44 单写者与既有 inbox 生产者清单不因本节扩大。
2. **capture 即候选**：agent 通道投递的 capture 与手动 note 同权——进 triage 三选一闸门，由 owner 决定去留；信任矩阵中归 **AI-proposed（需批准）**。该通道**永久不提供** `mode:"run"` / `preset` 直跑面（与 W18 的 webui/syncd remote-run opt-in 是两回事：W18 开关只影响 owner 亲打的远程 capture，agent 通道无论如何没有直跑）。
3. **决策动词禁区**：agent 不得 approve/reject/accept/rework/move/archive/merge/trash。执行分三层：(a) boardctl 动词面收窄（CLI 无这些子命令，测试钉死）；(b) store2 接线后由 D3 权限墙执行（`actor_type='agent'` 的 approve/accept 类转移在 DB trigger 层 RAISE）；(c) `skills/board-agent/SKILL.md` 的行为规范仅是礼仪层，不是边界。
4. **CLI 输出契约**：成功 = stdout 单个 JSON object 携带 `schemaVersion`（当前 = 1，add-only）；错误 = stderr 单个 JSON object `{"schemaVersion","error":{"code","message","details"?}}`；exit codes 0/2/3/4/5（成功 / 输入非法 / 服务不可达 / API 或响应错误 / 冲突）。`--help` 是唯一纯文本成功输出。
5. **skill 落位**：`skills/board-agent/SKILL.md` + `references/cli.md`，结构 adapted from dashi-taskboard `manage-taskboard`（Apache-2.0，NOTICE 第 7 条登记）。

### 诚实状态注记（ratification 前必读）

- PR-current 的 `POST /api/actions` **不辨 actor**：localhost 上任何进程都能 POST approve（PR1 单用户 localhost 信任域的既定过渡态）。在 store2 接线（D3 trigger）或 server actor 墙落地之前，「server enforces」的实际边界 = boardctl 动词面 + localhost 信任域。
- boardctl 随每个请求发送 `X-ZAI-Client: boardctl` 请求头，作为未来 server 侧 actor 辨识的挂点（server 现阶段忽略；请求头可伪造，**不是**鉴别边界——真正的墙是 D3）。不动 JSON wire，符合「不擅自扩展 wire 格式」纪律。

### 基线差异登记（M5 相关）

- live v0.47 `act/webui.py` 的 `ALLOWED_ACTIONS` 动词全集较 worktree `server/inbox_writer.py` 有演进，但 M5 用到的 `capture` 与 `comment` 两动词的 wire 形状（字段集合、`capture-<uuid>` 文件命名、comment 恒带非空文本）两侧一致——无分叉处理。
- live 树无 boardctl 前身，无冲突面。

---

## W18 — 远程 capture `mode:"run"` 闸门（owner 已拍板：DEFAULT OFF，config opt-in）

**新法条**：`capture mode:"run"`（direct-run，§34——跳过提案卡与人审预览直接开跑）按 **ingress 信道**分级：Mac app / owner 本机 loopback 输入照旧无条件放行；**网络 ingress（act/webui.py、act/syncd.py，及未来任何非本进程 UI 信道）默认拒绝 direct-run**。开关 = config.yaml `remote.allow_direct_run`（默认 `false`；`Config.remote_allow_direct_run`），settings_overrides 不可覆盖（防 UI 侧一键打开安全闸门）。

**拒绝语义 = 降级不报错**：闸门关闭时收到 `capture mode:"run"` → **剥掉 `mode` 字段**按普通 propose capture 落 inbox（提案照常进 triage 三选一闸门），HTTP 响应 200 + add-only 字段 `notice`（`"direct run is disabled for remote capture (remote.allow_direct_run=false); saved as a proposal"`）。任务永不被吞，也绝不谎报「已开跑」——远端提交方当场知道降级。§34 的 mode 词表校验不变：非 capture 带 mode、或 mode ≠ "run"，仍是 400 fail-closed。

**fail-closed**：config 缺失 / 解析失败 / 字段缺失，一律视为闸门关（`act/lib/risk.py::remote_direct_run_allowed`，任何异常返回 False）。

**实现落点**：`act/webui.py::_Handler._handle_inbox`（mode 校验块之后、write_inbox 之前降级）+ `act/lib/risk.py::remote_direct_run_allowed(cfg)`（canonical 判定，syncd/后续信道复用同一函数）+ `act/lib/config.py`（`remote_allow_direct_run`，add-only）+ `config.example.yaml` `remote:` 节。测试 `tests/test_webui_remote_gate.py`。

### W18 基线差异与 integrator 接线点（材料性，记录不擅补）

- **act/webui.py 在 v0.10.3 基线不存在**——本 PR 从 live 树（v0.47 行为真源）逐字移植 + W18 闸门。`webui/` 静态前端（index.html/app.js/style.css）**未**移植："/" 返回 500，全部 /api/* 端点功能完整；前端移植归 integrator/后续 PR。
- **v0.10.3 actd 的 `_apply_capture(text)` 完全忽略 `mode`**——闸门开着时 `mode:"run"` 会写进 inbox 文件但基线 actd 当普通 capture 处理（CONTRACT §34 已裁定这个方向向后安全）。§34/§34.1 的 actd 侧 direct-run 语义（新卡直接 approved、[direct-run] 标签、强制 chat 交付）是 actd 接线点，本 PR 不擅自移植。
- **webui `ALLOWED_ACTIONS` 保持 live 全集**，其中 `stop_to_review`/`defer`/`archive`/`unarchive`/`set_title`/`answer_input`/`feedback`/`merge_review`/`merge_apply`/`merge_dismiss`/`merge_force`/`import_claude_sessions`/`weekly_digest_now`/`split_note` 在 v0.10.3 actd 中无对应处理（带 id 的静默走完 elif 链无副作用；无 id 的按 unknown req dropped 落日志）。保留全集 = 各 actd 移植落地时 ingress 无需再改；若 integrator 认为黑洞不可接受，可临时裁剪到基线子集（二选一，在 PR 描述声明）。
- **act/syncd.py 不在 worktree**（v0.47-only）。syncd 移植时 W18 闸门接线点 = `_write_inbox_file`：`_inbox_shape_error` 通过之后、`record` 落盘之前——`action.get("action") == "capture" and action.get("mode") == "run" and not risk.remote_direct_run_allowed()` → `record.pop("mode")` + `_log("UP: <id> direct-run downgraded to propose (remote.allow_direct_run=false)")`。syncd 无同步响应信道，诚实声明落在 log + 卡片本身照常出现在 board 提案列。
- **TODO(contract)**：PR1 的 `server/inbox_writer.py`（`_build_capture`）目前无条件转发 `mode:"run"`——它同为 127.0.0.1 网络 listener 但服务本机浏览器看板（运行中列 direct-run 输入框，owner 亲手输入 = 信任矩阵 hand 档）。它算不算「远程 ingress」未拍板：建议 = 暂不套 W18（保 PR1 看板直跑框可用），等 PR3 instance token / 远端访问能力落地时同步复议。不在本 PR 擅改（server/ 归 UI 团队所有权）。

---

## W17 — 外部来源卡的 effective tier（cheap layer：强制 T2 + 强制 plan expansion）

**新法条**：`origin_trust: external` 的卡（外部 Slack / Gmail 等外来信号铸的卡，信任矩阵最低档）在**审批与调度层**一律按 **T2（需文字确认）**对待，且**强制 plan expansion**——不允许跳过提案展开直接裸批。声明字段 `tier` 不改写（registry YAML 原样保留，铸卡时的 LLM 判档照旧落盘）；生效档位是**投影/调度层的派生值**。判定函数 = `act/lib/risk.py::effective_tier(card) -> EffectiveTier(tier, forced_expand, reason)`，同时接受 dict（raw YAML / store2 行 / server 投影）与 `Requirement`。

**触发条件 = 显式 `origin_trust == "external"`**。缺失该字段的存量卡保持声明 tier、不强制 expansion——否则全部历史卡一夜抬成 T2，打破既有审批流与 1500+ 判例。fail-closed 的责任分工：① 铸卡侧（radar/triage）必须给新卡盖 `origin_trust` 章；② **auto-dispatch 侧缺 `origin_trust` 一律不许自动派发**（信任矩阵的自动化红线在调度器，不在 tier 投影——两处方向相反是有意的：缺信息不得授予自主权，也不得追溯锁死人工流）。

**wire 字段（add-only）**：dashboard 投影 `needs_approval[]`（含 raising 占位卡）新增 `effective_tier`（无 origin_trust 时恒等于 `tier`，Swift/web 端 decodeIfPresent 安全）。`origin_trust` 本身的字段名与取值枚举（`hand` / `external`，store2 schema.sql CHECK 同款）沿 store2-mapping.md §9 的 TODO(contract)，随 store2 接线修宪案一并立法。

### W17 基线差异与 integrator 接线点（材料性，记录不擅补）

- **v0.10.3 `Requirement` 无 `origin_trust` 字段**，且 `from_dict` 丢弃未知键、`to_dict` 不回写——即使 YAML 里有 `origin_trust`，经 registry load→save 一轮就**丢失**。铸卡盖章前必须先给 `Requirement` 加 add-only 字段（`origin_trust: Optional[str] = None` + `_OPTIONAL_ORDER` 追加）——归 trust-matrix 接线者/integrator，本任务不跨界改 registry.py。
- PR1 `server/board_source.py` 用 PyYAML 直读卡片 YAML 增补 `GET /api/cards/{id}` 详情（不经 Requirement）——YAML 一旦带 `origin_trust`，web 详情端**已经**能透出，无需 server 改动;`effective_tier` 进投影走 dashboard builder（本 PR 已落）。
- **强制 plan expansion 的执行点**（actd 接线，本 PR 只出 flag）：审批入口处 `risk.effective_tier(req).forced_expand == True` 且卡未经 expansion（v0.10.3 语义 = 未经 `process_raising` 展开、plan/DoD 为空）→ approve 转 `raise`（走既有 §「暂缓/展开」机制），notes 留 `[W17] 外部来源强制展开` 痕。T2 typed-confirm 弹窗（web/Mac）应读 `effective_tier` 而非 `tier`——UI 团队接线点。

---

## M1 — origin trust matrix + auto-dispatch 天花板 + queued 子状态词表（owner 已拍板）

实现落点：`act/lib/policy.py`（纯函数，无 I/O、不写 registry——§44 单写者不变）+ `config.example.yaml` `autodispatch:` 块 + `tests/test_policy.py`。以下三节按 ratification-ready 措辞书写，编号由 integrator 沿 CONTRACT 惯例分配新 §。

### M1.a 新法条 — 卡片出身信任分类（origin trust matrix）

**四类出身（locked）**：`hand`（用户手打：quick capture / Slack self-DM / iMessage 自发——三者都经 quick_capture 落卡，sources channel = `quick`/`quick_capture`）｜`proposed`（AI 自提：digest 建议 `analytics`、会话挖掘 `claude_code`、诊断降级卡 `radar-diagnostic`/`radar-parse-degraded`、拆分卡 `split`）｜`meeting`（会议音频/笔记出生：`meeting`/`audio`）｜`external`（第三方：`slack`/`gmail`）。

**分类规则**（`policy.classify_origin(card_sources, capture_channel)`，全函数永不 raise）：① 逐条 sources 的 `channel` 查表；② 未知/畸形 channel（含 `screen`——§45 屏幕永不铸卡，真出现即异常）**fail-closed 落 external**，与 executor 遥测白名单（live v0.47 `_USER_ORIGIN_CHANNELS`）同一条纪律；③ 混合来源取**最小信任**（信任序 hand > proposed > meeting > external）——手打卡被 slack/gmail 来源 fold 过即按 external 处理，外来文本已上卡则自动开跑资格随之消失；④ 空 sources 且无 capture_channel = AI 自铸卡形态 → `proposed`。

**与 store2 的口径冲突（材料性，本 PR store2 不接线不改）**：`act/lib/store2/schema.sql` 的 `origin_trust CHECK IN ('hand','external')` 是两值词表，store2-mapping.md §9 已自记 TODO(contract)（claude_code/meeting/digest 算哪档不明确）。本节即该 TODO 的答案：**词表应扩为四值** `('hand','proposed','meeting','external')`，store2 接线修宪案一并落。`act/lib/risk.py`（W17）只判 `external` 一档，四值词表向后兼容其判定。

**盖章责任（actd 接线点，本 PR 不接）**：铸卡侧（radar/capture/actd inbox capture）以 `classify_origin` 结果给新卡盖 `origin_trust` 章（registry `Requirement` 需 add-only 加字段，见 W17 基线差异节）；fold 并入新来源后章可能过期——所以调度侧（M1.b）**不读章、每次现算**，章只服务投影/审计。

### M1.b 新法条 — 自动派发天花板（may_auto_dispatch）

**语义**：只有出身 `hand` 的卡有资格免审批自动派发（card_sent → approved，actor=policy）；资格裁决 = `policy.may_auto_dispatch(card, cfg, today_spend) -> (bool, reason)`，全部天花板通过才放行，任一不过 → **回落待审批 + 卡上陈述原因**（locked：over-ceiling => falls back to needs-approval with a stated reason）。原因 token 词表（机读稳定，UI 文案调用方映射）：`disabled` / `origin:{proposed,meeting,external}` / `t2_confirm` / `outbound` / `repo:new` / `repo:none` / `repo:missing` / `cost:unknown` / `cost:over_ceiling` / `budget:unknown` / `budget:exhausted`。

**天花板明细（locked + 保守解释）**：① `autodispatch.enabled=false` 全关；② 出身非 hand 不批——出身**从 sources 现算**（不依赖可能缺失/过期的 `origin_trust` 字段；risk.py 注释「缺 origin_trust 一律不许自动派发」的红线由此以更强形式满足：未知渠道 fail-closed、fold 后降级即时生效）；③ §7/§41 审批语义不变：T2 / `green_sign_required` / 估价高过 `require_text_confirm_above_usd` 一律人批（`t2_confirm`）；④ never outbound：`type=comms` 卡永不自动开跑（保守判据：comms 的执行天然指向对外回复；TODO(contract): 若未来有非 comms 卡携带对外动作，需要更细的 outbound 判据）；⑤ existing target_repo only：`target_kind=new` 拒（绝不自动建 repo）；落点 repo（卡面 `target_repo`，缺省回落 `execution.default_target_repo`——workbench 兜底解释为合法既有落点，TODO(contract) 若 owner 要求「必须卡面显式指定」再收紧）必须在磁盘上已存在；⑥ 成本：估价缺失即拒（不可证明 ≤ 上限）、单卡估价 > `daily_budget_usd`（默认 $5，locked 上限）拒、`today_spend + 估价 > daily_budget_usd` 拒、today_spend 不可解析（台账坏）拒。

**并发上限不在资格闸里**：`max_concurrent` 是排队问题不是资格问题——超并发的卡照常 approved，留在合并运行列的 queued 子状态（M1.c），槽位空出即派发。

**配置（add-only，`config.example.yaml` 已落）**：`autodispatch.enabled`(true) / `autodispatch.daily_budget_usd`(5) / `autodispatch.max_concurrent`(3) / `autodispatch.notify`(true = 观察模式：每次自动派发发一条通知)。脏值逐键回退默认（宪法第 11 条口径），`policy.autodispatch_config(cfg)` 是唯一读取点。

**actd 接线点（本 PR 只出裁决函数）**：① 主循环 triage/inbox 落卡后，对 card_sent 卡调 `may_auto_dispatch`；True → 状态推 approved + analytics 事件 `auto_dispatch`（metadata only，title 不进遥测——沿 docs/TELEMETRY.md 红线）+ `autodispatch.notify` 时发通知；False 且原因非 `origin:*`/`disabled` → 原因 token 上卡（建议 add-only 字段 `card["auto_dispatch_block"]` 或 execution 侧痕迹，integrator 定）；② `today_spend` 台账：现存两树都无每日花费台账——建议 actd 以本地日期聚合当日 auto-dispatch 卡的 `cost_estimate_usd`（state/ 下单文件，重启幂等），实际收割成本可用时改用实际值；③ 并发计数 = EXECUTING 且有活 session 的卡数（reconcile 口径）。

### M1.c 新法条 — 合并运行列 queued 子状态原因词表

**词表（locked）**：`dependency`（有未完结依赖卡）｜`budget`（当日累计 + 本卡估价超 `daily_budget_usd`）｜`concurrency`（在跑数 ≥ `max_concurrent`）。判定 = `policy.queued_reason(card, state) -> reason | None`（state 由 actd/dashboard 投影算好传入，缺键跳过对应检查）；**优先级 dependency > budget > concurrency**——chip 只有一个位置，报最「粘」的阻塞（依赖不随时间自愈、预算等到明天、并发最快松动）。`None` = 无阻塞（纯粹没轮到 / 上次派发失败在退避，后者已有 `dispatch_error` 字段口径）。

**wire 字段（add-only）**：dashboard 投影 running 分区的 queued 项（`act/lib/dashboard.py` 现有 `state:"queued"` 分支）新增可选字段 `queued_reason`（Swift/web decodeIfPresent 安全）；web 端渲染为原因 chip（BUILD-CONTRACT §2.2「排队中 · 等 R-xx / 等预算」）。`blocked_by`（依赖卡 id 列表）的持久化形状未拍板 → TODO(contract)：v0.10.3 与 live 都无卡间依赖字段，首版接线可仅 budget/concurrency 两因，dependency 留词表占位。

### M1.d 基线差异（材料性，记录不擅补）

- **radar_slack MCP 路径的 channel 可被 LLM 控制（安全前置，必须先修再接自动派发）**：worktree v0.10.3 `act/radar_slack.py` mcp_scan 写 `"channel": r.get("channel") or "slack"`——channel 值来自提取 LLM 对第三方消息的自由输出。live v0.47 已修（硬编码 `"slack"`，LLM 报的频道名只进 `ref` 展示位，注释明记 provenance red line）。**在 M1.b 的世界里这不再只是遥测泄露面而是执行面**：注入文本骗 LLM 输出 channel=`quick` → classify_origin 判 hand → 满足其余天花板即自动开跑攻击者措辞的任务。接线 auto-dispatch 前必须把 live 的硬编码修法移植到 worktree（radar_slack 归属另有其人/integrator，本任务不跨界改）。
- **live v0.47 有、worktree v0.10.3 无的出生纪律**：`act/lib/provenance.py` §45 出生资格表、v0.17 triage 统一口径（所有 radar 候选过 quick_capture.triage 三选一闸）、`registry.derive_thread_key`。policy 的 channel 表按 live 盘点收录了 worktree 尚不存在的渠道（`split`/`analytics`/`claude_code`/`radar-diagnostic`/`radar-parse-degraded`）——forward-compat，未移植前这些行不命中、无副作用。
- **executor `_USER_ORIGIN_CHANNELS`（live v0.47 遥测白名单 = `("quick","quick_capture")`）与 policy 的 HAND 行必须保持同步**：两者是同一信任判断的两个消费面（遥测内容闸 / 自动派发闸）。worktree v0.10.3 executor 尚无该白名单；移植时建议改从 policy.CHANNEL_CLASS 派生，消除双表漂移。TODO(contract)。
- **无每日花费台账**：两树均无 today_spend 数据源（executor 只有 stop-confirm 的时间预算）。见 M1.b 接线点 ②。

---

## M6 — web/server 的 steer 回执与排队原因 UI 面（本 PR 已落地）

### §M6.1 steer 的 HTTP 与投影消费面（语义挂靠 W-steer/M2 新法条）

**HTTP 响应标注（已实现，`server/app.py` + `server/board_source.is_executing`）**：`POST /api/actions` 当 `action == "comment"` 且目标卡按投影判定为 executing（`running` 分区非 queued 行，或 `needs_input` 分区行——与 M2 接线 2 的 flush 点一致）时，响应 add-only 增 `"steer": true, "steer_status": "queued"`。server 落盘即排队，只能诚实报 queued；delivered/dropped 由投影回流。**inbox 文件本体保持 §3 comment 四键原形，一个字段都不加**（steer 是 actd 侧分类，不是新动词；`tests/test_server_steer.py` 钉死字节面与响应面）。判定 fail-safe：投影读不到/卡不在 executing 面，一律不标——宁可漏标，不误标。

**board 行投影（add-only optional；web 消费端 = `web/src/steer.ts`，validator = `scripts/demo_seed.py`）**：`running` / `needs_input` 行新增 `steers: [{text, ts, status, delivered_at}]`——`ts` 为 ISO 字符串（与 M2 带时间戳 dedup key 同源，重复文本合法）；`status ∈ {queued, delivered, dropped}` 开放枚举；`status=="delivered"` 必带 ISO `delivered_at`，其余状态为 null（诚实投递状态，绝不假装送达）。web 端只硬性要求 `ts` 为 string，其余字段缺席防御性降级；未知 status 计数时按 queued 兜底（最保守——不谎报送达）。

**UI（已实现）**：working 卡出三态回执 chips（排队 ×N / 已送达 ×N / 未送达 ×N，`RunningCard.tsx`）；executing 卡的 comment 弹窗明示中继语义；提交后 pending 期间按响应标注显示「已提交 · 方向修正排队中…」（`boardActions.useSubmit.steerQueued`），看板回流后以投影 `steers[]` 为准；详情抽屉新增「方向修正」section（逐条状态 chip + ts + 送达时间，`DetailFields.tsx`）。

**integrator 接线点（材料性，M6 不擅补）**：M2 的 act 侧台账（`execution.pending_steers` 带全文；`delivered_steers`/`steer_queued`/`steer_delivered` 环只存 key/ts，**不存全文**）目前推不出 `steers[]` 行里 delivered 条目的全文。二选一由 integrator 定夺：(a) dashboard builder 只投 pending 条目全文 + delivered 条目仅 ts（web 端已兼容 text 缺席）；(b) 扩 `delivered_steers` 环为 `{key, text}`（cap 20 不变）。dropped 条目同理（M2 目前只留 notes 痕 + notify）——(a) 路线下 dropped 不进 `steers[]`，可见性由 notes 痕承担，web 端 dropped chip 自然缺席，不算违约。

### §M6.2 queued 子状态排队原因的 wire 形与 UI（语义挂靠 M1.c 词表）

**wire 双形（canonical 由 integrator 终裁，web 端已双兼容）**：queued 行可选字段 `queued_reason` 现存两种拍板稿——M1.c 的扁平 token 形（`dependency` / `budget` / `concurrency`，`act/lib/policy.py::queued_reason` 直出）与 demo_seed validator 钉的结构化形 `{kind, detail?, blocking_id?}`（kind = `waiting_card`（必带 `blocking_id`）/ `waiting_budget`）。`web/src/steer.ts::queuedReasonLabel` 同表翻译两套（`dependency`≡`waiting_card`、`budget`≡`waiting_budget`、`concurrency`→「等并发位」），未知 token/kind 按 detail/原文原样展示（开放枚举不崩渲染）。**建议 canonical = 结构化形**（`blocking_id` 才能渲染「等 R-xx」；扁平 token 出不了卡号），dashboard builder 接线时把 policy token 映射进 kind：`dependency → waiting_card`（blocking_id 取 blocked_by 首项）、`budget → waiting_budget`、`concurrency → {kind:"concurrency"}`。

**与既有字段的边界**：`queued_reason`（为什么还没派发）与 `dispatch_error`（上次派发为什么失败）/ live 树 `dispatch_error_id`（§25 失败分类）独立并存，生产端不得混写——M1.c 的 `None` 语义（退避中）正是留给 `dispatch_error` 的位置。

**UI（已实现）**：queued 卡在「排队中」chip 旁出原因 chip（`RunningCard.tsx`）；详情抽屉出「排队原因」行（`DetailFields.tsx`）；`dispatch_error` chip 照旧独立。

### M6 基线差异备忘（材料性，记录不擅补）

- live v0.47 `act/lib/dashboard.py` 的 queued 行带 `dispatch_error_id`（§25 失败分类），worktree PR1 的 `web/src/types.ts` 未建模——M6 未补（非本模块面），producer 落地时前端按 add-only 索引签名直接透传，不崩渲染。
- §44.3 briefing 机制只存在于 live v0.47；worktree v0.10.3 act/ 基座没有——M6 的 web/server 面只依赖本节投影字段与 M2 的 `act/lib/steer.py` 台账语义，不依赖 live act/ 内部形状（与 M2 基线差异节互认）。

---

## M8 — 编号地图 · PR1 追认 · 跨节终裁 · TODO(contract) 总对账（本文档 owner）

> 本节是全文的收口：给 integrator 的落法编号地图、PR1 两项修宪案的 ratification 文本、并行 builder 之间形状/词表分叉的**终裁**（含 §M6.1/§M6.2 显式请裁的两处）、以及 PR1/PR2/WIRE 三队全部 `TODO(contract)` 的逐条裁决提案。

### M8.1 落法编号与 live CONTRACT（v0.47）锚点地图

| 本文小节 | 落法位置 | 性质 |
|---|---|---|
| PR1（vnext.md 8.3 + 本节 M8.2 增补） | 新 **§49** — web 面（server/ + web/，localhost sanctioned client） | 新 §（编号 PR1 已预留） |
| M1.a + W17 | 新 **§50** — 卡片出身信任矩阵（origin_trust 四档 + effective tier）；§7 卡片字段与 §41 T2 闸门各加一行引用注（typed-confirm 改读 effective_tier） | 新 § + 两处引用注 |
| M1.b + M1.c + §M6.2 | 新 **§51** — 自动派发天花板（may_auto_dispatch）+ 合并运行列 queued 子状态；§2 running 分区 add-only 字段注记（`queued_reason`） | 新 § + §2 注记 |
| M5 | 新 **§52** — agent 有界通道（boardctl + board-agent skill） | 新 § |
| W-steer/M2 + §M6.1 | **§44.3-S**（§44.3 的 add-only 增补小节）；§32.2 comment guard 白名单扩 `executing`（steer 入队分支）；§39.2 冻结行文法家族追加 `[<date> 追加指令未送达]` 变体；§2 running/needs_input 行 add-only `steers[]` 注记；§49 响应标注注 | 修订 + 增补 |
| W18 | **§41** webui `mode` 放行条件收紧（加 `remote.allow_direct_run` 门）；§31 syncd UP 降级注；§34 引用注 | 修订 |
| W1.a | **§38.1**「喂给匹配器的清单增强」修订段（建议编 §38.4）；**§13 无需改正文**——self-DM 捕获经同一 `_inventory_reqs` 自动继承反转配额 | 修订 |
| W1.c | **§10** v0.20.0 archive 条款默认值修订（`archive_after_days` 0 → 30） | 修订 |

编号 49-52 沿「编号永不复用」惯例顺排；owner 批准顺序若变，编号由 integrator 顺延，锚点关系不变。

### M8.2 PR1 追认 — §49 web 面 + 宪法第 7 条修正草案

**§49 准文本** = `docs/design/vnext.md` 8.3（localhost server 是两文件契约的又一 sanctioned client：读 `state/dashboard.json` + registry 只读增补详情，写 `state/inbox/*.json`；**绝不写 registry/dashboard**——宪法第 1 条零触动；bind 127.0.0.1 硬编码、交付物路径 server 端推导、零上传面——宪法第 9 条口径）。落法时增补三处 add-only：

1. 合法客户端清单加入 `act/boardctl.py`（经 server 中转，动词面收窄见 M5；M5「诚实状态注记」——server 现阶段不辨 actor、真正的墙是 D3——照录入法条，不许法条口径比实现更乐观）；
2. `POST /api/actions` 响应 add-only 键 `steer`(bool) / `steer_status`(恒 `"queued"`)——§M6.1 语义：server 落盘即排队只能诚实报 queued，delivered/dropped 由投影回流，server 永不虚报送达；
3. error envelope codes 词表 add-only 收编 `NOT_IMPLEMENTED`（501，reveal 非 darwin 专用——`server/errors.py` 的 TODO(contract) 就此关闭）；413（body 超限）**追认现状** = `INVALID_FIELD` + HTTP 413（status 已表意，不为 loopback 面扩词表——`server/app.py` 的 TODO(contract) 关闭）。

**宪法第 7 条（运行时零重依赖）修正草案（二选一，推荐 A）**：

- **A（推荐，条文零改动）**：§49 内落执法注：「web/ 的 npm 依赖（react/react-dom + dev 工具链）属构建/测试侧，交付物为静态文件、由 server/（纯 stdlib）服务；Python 管线运行时白名单 stdlib + PyYAML 不变。」
- **B（备选，动条文）**：第 7 条括号执法注追加一句：「（CI 安装清单即是白名单；web 前端 npm 构建依赖属开发侧，不在此列）」。

### M8.3 跨节终裁（并行 builder 的形状/词表分叉，一处一裁）

| # | 分叉 | 终裁 |
|---|---|---|
| C-1 | `origin_trust` 词表三版并存：policy.py 四值（`hand/proposed/meeting/external`）｜store2 schema CHECK 二值（`hand/external`）｜demo_seed 枚举二值 | **四值为 canonical**（M1.a 的分类就是 owner 信任矩阵的完整表达：hand=自动、proposed/meeting=需批、external=需批+W17 提级）。盖章、dashboard 投影、store2 列统一四值；store2 接线迁移把 CHECK 放宽为四值 + 保留 DEFAULT 'external'（mapping §9.1 与 schema.md 首条 TODO 就此关闭）；demo_seed `ORIGIN_TRUST` 枚举扩为四值（现有 hand/external 样例是合法子集，补 proposed/meeting 样例卡）。W17 只判 external，四值向后兼容其判定。 |
| C-2 | queued_reason 双形（§M6.2 请裁）：policy 扁平 token vs 结构化 `{kind, detail?, blocking_id?}` | **canonical = 结构化形**（照准 §M6.2 建议：`blocking_id` 才渲染得出「等 R-xx」）。kind 词表 = `waiting_card`（必带 blocking_id）/ `waiting_budget` / `concurrency`——照准 M6 映射（web switch 已认 `concurrency`，零前端改动）；dashboard builder 接线时映射 `dependency → waiting_card`（blocking_id 取 blocked_by 首项）、`budget → waiting_budget`、`concurrency → {kind:"concurrency"}`；policy 内部 token 不改；demo_seed `QUEUED_REASON_KINDS` 补 `concurrency`（integrator 一行）。`dispatch_error`/`dispatch_error_id` 独立并存、生产端不得混写（§M6.2 边界照录入法条）。 |
| C-3 | steers[] 投影中 delivered/dropped 行的全文来源（§M6.1 请裁 a/b） | **裁 (b) 并定形**：`execution.delivered_steers` 环形元素从裸 key 扩为 `{"key","text"(截 200),"ts","delivered_at"}`（cap 20 不变；**读侧容忍旧裸 key 条目**——enqueue 去重读 key 字段、投影跳过无 text 旧条目，crash 窗口的混合形不许崩）。投影规则：`steers[]` = pending（status=queued，text 全文截 200）+ delivered 环（status=delivered，带 delivered_at）；**dropped 不进 `steers[]`**——可见性由 notes 痕 `[追加指令未送达]` + notify 承担（§M6.1 已声明 web dropped chip 缺席不算违约；STEER_STATUSES 保留 dropped 值作 forward-compat）。steer.py 的 `mark_delivered` 随 actd 接线时改写环形元素，属 M2 接线点。 |
| C-4 | steers 时间戳类型：§2 惯例「dashboard 输出 epoch int」 vs web/demo ISO string | **ISO string**，显式偏离 §2 惯例并在法条注明理由：`ts` 是 dedup key `<ts>\|<sha256(text)[:16]>` 的组成部分，投影保原文才能与 `execution.*` 台账逐字对账；web parser 只认 string ts（无 ts 的行整行丢弃——绝不渲染无法对账的 steer）。 |
| C-5 | W18 是否覆盖 PR1 的 `server/inbox_writer.py`（同为 127.0.0.1 listener，直跑框无条件转发 `mode:"run"`） | 维持 W18 节内裁定：**不套**——loopback 单用户面 = owner 本机输入（信任矩阵 hand 档），看板直跑框照常；PR3 instance token / 远端访问能力落地时同步复议（届时 server 若可从非本机到达，自动落入「网络 ingress」定义、W18 闸门即刻适用）。 |
| C-6 | auto-dispatch 回落的「stated reason」落点未定名（M1.b 留给 integrator） | 字段名照准 M1.b 建议：dashboard needs_approval 行 add-only optional **`auto_dispatch_block`**（str = M1.b reason token，机读稳定、UI 映射文案）；同时 notes 留痕一行 `[auto-dispatch 拦下] <token>`（人读审计）。`origin:*`/`disabled` 两类原因不上卡不留痕（常态而非例外，逐卡留痕即噪音——宪法第 10 条口径）。 |

### M8.4 TODO(contract) 总对账（PR1 / PR2 / WIRE 全部未决项；每条给裁决，owner 批准即生效）

| # | 出处 | 事项 | 裁决提案 |
|---|---|---|---|
| T-1 | vnext.md §9 | §49 编号预留未落法 | 维持：修宪 PR（集成后、接线前）一并落 §49-§52 + 全部修订段（M8.1 地图）。 |
| T-2 | vnext.md §9 | server 动词白名单最终集合 | 已定：= `docs/design/inbox-actions.md` §2+§3 目录（G1 实现即白名单，golden 33 件钉死）；§49 落法引用之。 |
| T-3 | vnext.md §9 | 宪法第 7 条措辞归属 | 推荐方案 A（§49 执法注，条文不动）——见 M8.2。 |
| T-4 | vnext.md §9 | `origin_trust` 入宪时机 | **提前**：字段与四值词表随本案 §50 入宪（registry YAML add-only optional 字段先立法、store2 接线沿用），不再等 PR3。 |
| T-5 | vnext.md §9 | NOTICE fork 目的地路径 reconcile | 维持 integrator 终裁；boardctl（taskctl adaptation）与 board-agent skill 已登记。 |
| T-6 | mapping §9.1 / schema.md / migrate_yaml | `origin_trust` 推导规则 | 已定：C-1 四值 + `policy.CHANNEL_CLASS` 为唯一映射真源（migrate_yaml 的二值启发式升级为查同一张表；未知/畸形 → external fail-closed）。 |
| T-7 | mapping §9.2 / migrate_yaml | `created` 推导无祖先 | 照准 mapping 建议：sources[0].date 可解析优先、文件 mtime 兜底，dry-run 逐卡报取值来源。 |
| T-8 | mapping §9.3 | `merged_into`/`thread_key` 是否提热列 | `merged_into` 提热列（schema 已有 `merged_into_id`，照准）；`thread_key` 留 payload，接线 PR 建表达式索引。 |
| T-9 | mapping §9.4/§9.5 | `outputs` / `card` dict 语义存疑 | 不立新法：payload 原样保留（历史键、读者仍在），语义存疑照实注记。 |
| T-10 | mapping §9.6 / schema.md | execution C 类键归属（inbox_stem/briefing 队列/answer 簿记…） | PR3 接线逐个拍板不变；本案新增 steer 键族（W-steer 全表 + C-3 扩环）显式归 payload.execution，store2 `notes` 表以 kind='steer' 做投影（词表已含，无 DDL 改动）。 |
| T-11 | mapping §9.7 + tests/test_store2_migration.py | `plan` str 形态；畸形值 round-trip 偏离（bool deadline verbatim、非数字 cost 归 None） | 照准已实现裁决：plan 原样保留（round-trip 优先）；payload 尽量 verbatim，唯 cost 经 `_coerce_cost` 归 None 的偏离**追认**（LLM 垃圾值不值得为逐字节等价保真，dry-run 已报）。 |
| T-12 | mapping §9.8 | notes blob 双写一致性 | 维持：PR2 一次性投影无一致性问题；接线 PR 单源化（fold 写路径单源或双写事务），随 store2 接线案立法。 |
| T-13 | mapping §9.9/§9.10 | `type` 值域归一；crash-mid-move residue | 照准：type 永不归一（UI filter 按原字符串）；residue 不清理、dry-run 报告。 |
| T-14 | schema.md | digest 拉回转移行缺口；`dispatches.status` 词表；queued 取消动词；notes.kind 扩 answer | 全部「接线时 add-only 补」：撞 `ILLEGAL_TRANSITION` 再补行；`running\|completed\|failed\|stopped` 随接线 PR 入宪；**不新增**取消排队动词（abort_execution 覆盖）；answer 值随 schema v2 定夺。 |
| T-15 | schema.sql:43 | origin_trust CHECK 二值与四值词表冲突 | **settled**（C-1；PR #106 终审落地）：CHECK 已放宽为 §50 四值 canonical；migrate 推导 = `policy.classify_origin`（全 sources 取最小信任），export shape 表含 `origin_trust`（权威章 round-trip 保真）。 |
| T-16 | store2/store.py:400 | trashed→archived 复位后的 prev_status 语义 | **追认实现**：restore 回 archived 态时补 `prev_status='delivered'`（unarchive 兜底值——schema CHECK 要求封存卡必带回程票）；接线 parity 以 store2 语义为准并入宪。 |
| T-17 | inbox-actions R2 | §3 动词清单是 v0.1 化石 | 修宪 PR 把 §3 改为：形状示例保留 + 「动作全集与语义见 §10；字节形以 Mac prettyPrinted+sortedKeys 为准（golden 集 `tests/fixtures/inbox/`）」。 |
| T-18 | inbox-actions R9 | rework 空反馈替换文案只活在 Swift 代码 | 落进 §10 rework 条目为冻结字面量（客户端行为，三端逐字一致）：「Zelin 打回了这次交付但没有写具体理由。请对照本需求的 definition_of_done 逐条自检：每一条是否真正达成、产出物是否在承诺的位置、质量是否达到可直接使用的程度。找出差距，自行改进后重新交付，并用两三句话说明这次改了什么。」web 复刻后空打回不再走样成空 comment。 |
| T-19 | server/files.py + web deliverables.ts | 卡片无结构化「交付物清单」字段 | 预留 add-only `execution.deliverables`（[str] 相对路径；写方 = executor harvest；server 只服务清单内文件）随接线 PR 落 §33；此前「目录约定推导 + 穿越防护」的保守实现追认为过渡合法。 |
| T-20 | web vite.config.ts | `@types/react`/`@types/react-dom` 不在 dev 白名单 | 白名单 add-only 收编两包（纯类型、零运行时字节）；BUILD-CONTRACT §0.4 与 vnext.md §7 同步增补。 |
| T-21 | web taskFilters.ts | 过滤器跨分区语义未入宪 | 不入 CONTRACT 正文：过滤/搜索是纯客户端展示行为、不产生 wire 动作；§49 落法加此一句钉死。 |
| T-22 | web RunningCard.tsx | queued 原因 chip 无结构化字段 | 已解决：C-2 落定结构化 `queued_reason`；`dispatch_error` 透传保留、可并存（退避中的卡两者都有）。 |
| T-23 | web MermaidDiagram.tsx / markdown.ts | mermaid 依赖；MarkdownDocument fork 因依赖不在白名单而偏离 | mermaid **不进**白名单，保持禁用降级（code block 展示）；markdown 自写替代追认合法，NOTICE 按「adapted」登记而非逐字搬运。 |
| T-24 | M1.b ④ | never-outbound 判据只有 `type=comms` 一刀 | 首版照准（保守可误拦不可漏放）；若未来非 comms 卡携带对外动作，扩确定性出站动词表再修此条——不阻塞 ratify。 |
| T-25 | M1.d | executor `_USER_ORIGIN_CHANNELS` 与 policy HAND 行双表漂移风险 | 照准 M1.d 建议：executor 侧移植时改从 `policy.CHANNEL_CLASS` 派生，单一真源。 |
| T-26 | M1.c / M1.b | `blocked_by`（卡间依赖）无持久化形状；repo 落点是否必须卡面显式指定 | 均维持保守占位：首版接线仅 budget/concurrency 两因（dependency 词表占位）；default_target_repo/workbench 兜底解释为合法既有落点——依赖字段与更严 repo 判据是独立设计题，另案立法，不阻塞本案。 |
| T-27 | scripts/demo_seed.py / web 词表 | v-next 枚举「以 amendments 为准」 | 本节即准：ORIGIN_TRUST 四值（C-1）、queued_reason kind 三值（C-2，demo 补 `concurrency`）、steers 三态 + ISO ts + dropped 不投影（C-3/C-4）；ratify 后 demo/validator 按 C-1/C-2 各补一行，其余已一致。 |
| T-28 | W18 末条 / M5 | server/inbox_writer 的 remote 定性；boardctl capture 与手打 capture 在 wire 上不可区分 | 前者 = C-5。后者**已修（ingress 落款，本 PR）**——原「追认为已知限制」的裁决作废：不可区分性正是 trust-grant 时刻的漏洞（agent capture 冒 hand 章可进自动派发、agent comment 冒 owner 可被当 OWNER UPDATE 直发 live session）。机制（inbox 记录 add-only 键 ``via``）：server/inbox_writer 恒落 ``via:"web"``；capture/comment 两动词接受可选 ``actor:"agent"``（唯一合法值，boardctl 硬编码恒发、配 mode/preset 同请求即 400）——present 时落 ``via:"agent"``；act/webui 恒落 ``via:"remote"``；Mac 文件无 via = owner-local（缺 via 只在非 HTTP 铸的文件上合法）。actd 按落款盖捕获源 channel：owner ingress → ``quick_capture``（HAND 不变）、agent → ``agent_capture``、remote/未知值 fail-closed → ``remote_capture``（两者 PROPOSED 入 ``policy.CHANNEL_CLASS``）——may_auto_dispatch 出身从 sources 现算，agent/remote 捕获的自动派发就此**结构性**关死；executing 卡 comment 只有 owner ingress 才 steer，agent/remote 只上卡记录（notes，不进 plan）。**诚实条款**：via 直发被 400、伪造被覆盖，但同用户在裸 HTTP 层可**省略** actor 冒充 owner ingress——落款是礼仪 + 取证（违规留 actd 日志），不是密码学墙。硬后盾不依赖落款：预算/成本天花板、``effective_tier`` 强制扩写（W17）、人工审批列、§34bis 级篡改取证。密码学收紧 = T-29。 |
| T-29 | T-28 诚实条款 | actor 墙硬化（具名 follow-up，PR3） | per-boot instance token 只发给 owner 面（Mac app / web 看板），agent 面持独立 token（``X-ZAI-Client`` 挂点已留）——届时 via 从「自报礼仪」升级为「鉴权事实」，省略 actor 的裸 HTTP 请求拿不到 owner token 即无法冒充。落地前 T-28 的诚实条款 + 硬后盾是唯一防线。 |

### M8.5 宪法（§0）触及总表（本案全部改动 vs 十一条逐条自检）

- **第 1 条（单写者）**：不破——steer/policy/risk 全是纯函数，判定与落盘归 actd 主循环；boardctl/server 只读 + 写 inbox；webui/syncd 只是 ingress。
- **第 2 条（一切可逆）**：不破——auto-dispatch 的卡走既有 abort_execution/stop_to_review 回程票；auto-archive 保留 unarchive；steer 不动状态机。
- **第 3 条（诚实报告）**：强化——queued 原因结构化、steer 三态诚实投影（未知 status 按 queued 兜底，绝不谎报送达）、W18 降级带 notice、auto-dispatch 回落必带原因（C-6）。
- **第 4 条（记录≠立案）+ §45**：不破——本案零新增发起渠道；boardctl capture 进 triage 三选一闸门；`screen` 在 `CHANNEL_CLASS` 里 fail-closed 落 external 仅是纵深（§45 本就不许它出生）。
- **第 5 条（围栏）**：不破，附一句澄清（随 §44.3-S 入法）——owner 亲打 steer 文本不是「外部文本」，§39.2 `OWNER ANSWER:` 同一先例直发；外部内容进 briefing 照旧围栏，steer 与 briefing 永不混批混 prompt。
- **第 6 条（add-only）**：全部新字段 add-only optional，旧 reader 缺键即老行为（Swift decodeIfPresent / web 防御性解析两侧验证）。
- **第 7 条（零重依赖）**：不破——policy/risk/steer/boardctl/webui 纯 stdlib；PR1 执法注见 M8.2。
- **第 8 条（版本单源）**：不触及。
- **第 9 条（隐私分层）**：不破——steers 投影 text 截 200 与既有 notes_text 同级（syncd 侧 E2E 密文）；analytics 全部纯元数据（auto_dispatch/steer 事件不含标题原文）；无新增上传面、无新增网络面（webui/syncd 是既有面收紧）。
- **第 10 条（打扰要有资格）**：强化——hand 卡免批减少无谓审批打扰；external 卡提级 + 强制展开是「更有资格的打扰」；auto-dispatch 常态回落原因不留痕（C-6）。
- **第 11 条（失败不外溢）**：不破——policy/risk/steer 全函数容忍垃圾输入；坏 config 绝不打开远程闸门；steer 丢弃必留痕。
- **BUILD-CONTRACT §0.6 不变量清单的显式修订声明**：「T0/T1/T2 审批语义」是唯一被动的一条——T2 typed-confirm 原文不动且改读 effective_tier 后只严不松；变化 = T0/T1 的 hand 卡新增天花板内免批通道（M1.b）。triage 三选一闸门、可逆矩阵、§45 出生管制、`fence_untrusted` 全部原样。

---

## W-actd（WIRE 接线）— 各模块落进 actd 主循环的实现备忘（本 PR 已落地）

M1/M2/W1/W17/W18/M6/M8 的裁决在本节全部接线完毕。落点：`act/actd.py`（唯一 WIRE 独占文件）+ `act/lib/dashboard.py`（投影）+ `act/lib/registry.py`（add-only 字段与 archive 家族）+ `act/lib/steer.py`（C-3 扩环，M8.3 已裁属接线期改动）+ `act/executor.py`（resume 的 add-only `prompt=` 参数）+ `act/radar_slack.py`（M1.d 安全前置）。行为测试 `tests/test_actd_wire.py`（32 例）。

### 接线判断（材料性，每条都是 live-vs-worktree 或裁决落地的 judgment call）

1. **盖章集中在 `registry.merge_or_new`**（三个出口：新卡/改进子卡/restatement 并入），不在各 radar 调用点分散盖——所有铸卡与 fold 都经这个漏斗，M1.a「fold 后章过期」由并入时重算章直接解决（手打卡被 slack 来源并入即降 external，测试钉死）。调度侧照旧不读章、每次现算（M1.b②）。
2. **`Requirement` add-only 字段已落**：`origin_trust`（T-4 裁定随 §50 提前入法）+ `archived_at`/`archive_reason` + `State.ARCHIVED`，`_OPTIONAL_ORDER` 尾部追加——YAML round-trip 不再丢章（W17 基线差异节的前置就此关闭）。
3. **并发上限约束全部派发**（M1.b 接线点③字面执行：manual 批的卡也排队），**预算天花板只约束 policy 批的卡**——owner 显式点头 = override，人批卡被预算闸拦下才是谎报。auto 卡在派发时刻做预算复核（台账排除本卡自身预留）：批准后 owner 调低预算/隔日翻账等边界，卡诚实留队（queued_reason=waiting_budget）而非硬跑。
4. **当日花费台账** = `state/autodispatch_spend.json`（`{"date": 本地 YYYY-MM-DD, "cards": {R-id: usd}}`，写者 = actd 单写；预留记在批准时刻；按卡键控所以重启幂等；隔日/坏文件 = 空账）。dashboard 有一个独立只读小读器（import actd 会循环依赖）——文件名双处字面量，改名需同步两处。**已知边界（TODO(contract)）**：昨天 auto 批准、因并发排队跨日的卡，隔日台账翻账后其预留消失、派发复核按新账通过——极端情形下单日实际派发额可略超预算一次估价，保守方向是接受（预算是天花板不是审计账；实际成本核算另案）。
5. **steer flush 管道**：v0.10.3 无 §44.3 brief 送达点，flush 借 `executor.resume(prompt=)`（add-only 参数，缺省行为逐字节不变；prompt 过 `sanitize.scrub` 防泄密、不围栏——owner 亲打）。窗口① roster blocked：先 `stop_session` 再带 prompt resume（rework 同款）；窗口② dead-resume：OWNER UPDATE 直接作 resume 首条输入，零额外打断。**窗口③（M2 spec 之外的新丢弃路径）**：done 晋升 review 时 pending steers 再无处送——`drop_trace` 留痕 + notify（§39 红线：不静默蒸发）。**已知限制**：blocked 判定用 pass-start roster 快照（基线无 `_briefing_window_open` 的 last-moment fresh probe）——blocked→working 的窗口内转换可能被 stop 一次；v0.47 落法时必须移植 fresh probe（W-steer 基线差异节已记，此处重申为接线后残留）。
6. **C-3 已执行**：`steer.mark_delivered` 环形元素扩为 `{key, text(截200), ts, delivered_at}`，读侧容忍旧裸 key（enqueue 去重 `_delivered_keys` 双形、投影 `delivered_entries` 跳过无 text/ts 条目）；`_steers_view` 投影 = delivered 环在前 + pending(status=queued) 在后，dropped 不投影，ts 保 ISO 原文（C-4）。
7. **W1.c archive 移植适配**：基线无 `thread_id` ——簇判据退化为 `improvement_of` 双向血缘（`getattr` 前向兼容，live 落法时恢复 thread 维度）；`unarchive` 无 §34bis 台账（基线无该机制，纯 unlink）；`archive`/`unarchive` inbox 动词 + 中央归档闸（archived 卡除 unarchive 外全 no-op）一并移植——宪法第 2 条（auto-archive 必须可逆）要求动词随 sweep 同 PR 落地。`load()`/`next_id()` 的 include_archived 碰撞防线照 live 判例移植。webui `ALLOWED_ACTIONS` 里的 archive/unarchive 两个动词就此脱离「黑洞」清单（W18 基线差异节的清单相应 -2）。
8. **W17 执法点**：`_apply_decision` approve 分支——`effective_tier.forced_expand` 且 plan/DoD 双空 → 转 RAISING + notes `[W17]`（幂等：痕只留一次）；analyze 不可用时拒批（fail-closed：外部卡裸跑正是 W17 要堵的洞）。auto_dispatch 侧另有 belt-and-braces：显式 external 章即便 sources 现算为 hand（手改 YAML）也绝不自动派发。
9. **C-6 落点**：`execution.auto_dispatch_block`（add-only）+ notes `[<date> auto-dispatch 拦下] <token>`（仅 token 变化时留一次，防每 pass 刷屏）；origin:*/disabled 常态原因不上卡不留痕，且会清掉既有过期 token（投影诚实）。dashboard needs_approval 行透传该字段与 `origin_trust`。
10. **主循环顺序**：inbox → auto_dispatch_pass → dispatch_approved →（有变化才 early-write）→ reconcile（含 steer flush/drop）→ raising → purge_trash → archive_stale（24h 门）→ build+write dashboard。analytics 新事件：`auto_dispatch` / `auto_dispatch_blocked` / `inbox_steer` / `steer_delivered` / `steer_dropped`（全部 metadata only，title 不进遥测——TELEMETRY 红线）。
11. **M1.d 安全前置已修**：`act/radar_slack.py` mcp_scan 的 `sources[0].channel` 从 `r.get("channel") or "slack"` 改为 live v0.47 同款硬编码 `"slack"`（LLM 报的频道名进 `ref` 展示位）——auto-dispatch 接线的前提条件，与本接线同 PR 落地。
12. **T-28 ingress 落款接线**：`actd._ingress_channel` 把 inbox 记录的 `via` 收敛成捕获源 channel（无 via / `"web"` → quick_capture；`"agent"` → agent_capture；`"remote"` 与一切未知/畸形值 fail-closed → remote_capture，后两者 PROPOSED）；comment 分支先过 `_is_owner_ingress`——owner 才走 steer/「折叠 + 退回重批」，agent/remote 走 `_record_nonowner_comment`（只进 notes，**不进 plan**：plan 是喂给 executor 的指令面，非 owner 文本进 plan 等于绕道 steer）；server/app.py 的 steer 响应标注按 via 反映实际裁决（agent comment on executing ⇒ `steer:false`）。expansion（process_raising）不改 sources，章随卡到调度侧现算。附带 m1 修复：steer dedup 键扩为 `<ts>|<inbox stem>|<hash>`——只有真正的同文件重放（unlink 失败）才去重，同秒同文的两条指令是两个 inbox 文件（stem 全局唯一）= 两条 steer。m2（跨午夜预算翻账）维持第 4 条已记录的接受口径，不改代码。

### 仍未接线（诚实清单）

- **W1.b thread_key 兜底未移植**（掉出 recency 窗口的老 delivered 卡收 follow-up 仍可能铸重复卡）——残余风险照 W1.b 记录不变，随 live 落法或 store2 接线解决。
- **dependency 队因无生产者**（T-26 维持）：`blocked_by` 无持久化形状，`waiting_card` 永不被投影——词表占位。
- **§34/§34.1 direct-run 的 actd 侧语义**（W18 基线差异节）未移植：闸开时 `mode:"run"` 仍按普通 capture 落卡（向后安全方向）。
- **store2 保持 dormant**（本 PR 零 import）；`origin_trust` 四值 CHECK 放宽照 T-15 留给接线迁移。

---

## W-tests（T 行为测试）— 锁定决策的判例清单（本 PR 已落地）

每条 owner 拍板的锁定决策都有独立行为测试钉着（unittest，沙箱 HOME，绝不 spawn 真 claude）。新增 36 例（334 → 370 全绿），按决策对账：

- **信任矩阵逐漏斗**（`tests/test_policy_trust_matrix.py`，8 例）：Slack self-DM 经 quick_capture 铸卡 = hand → 天花板内免批直发（端到端）；gmail/slack 漏斗 = external → 人批 + W17 强制扩写；meeting = 人批但**不**强制扩写；AI 自铸（空 sources）= proposed → 人批、常态回落无痕；§45 纵深 = screen 来源即便绕过出生管制上卡也落 external 车道（T2 强制、永不免批、fold 进 hand 卡即降级）；M1.d = mcp_scan 提取 LLM 自报 `channel:"quick"` 不能伪造 hand（硬编码 slack + ref 展示位判例钉死）。
- **auto-dispatch 天花板全集**（`tests/test_policy_ceilings.py`，15 例）：outbound（comms 永不自动开跑）/ repo:new / repo:none / repo:missing / cost:unknown / **$5 精确边界（5.0 过、5.5 拦）** / budget:exhausted（台账累计）/ t2_confirm（T2、green_sign、超文字确认线三面，且压过 cost:over_ceiling——审批语义先于便宜天花板）逐条钉「留待审批 + `auto_dispatch_block` token + notes 一次性留痕 + 不发观察通知」；token 换因重盖与解除即清；并发上限 = 排队非拒绝（槽位空出下一 pass 即派发）；dispatch 预算复核排除本卡自身预留（不排除会饿死每张 auto 卡）。
- **steer relay 外部可观察契约**（`tests/test_steer_relay.py`，8 例）：遥测词表 `inbox_steer`/`steer_delivered(n)`/`steer_dropped(reason=done|attempts)`；flush **之后**重放同一 inbox 文件经 delivered 台账去重（steer_count 不涨、不二次送达）；dropped steer 不进 dashboard `steers[]`（C-4）、可见性由 notes `[追加指令未送达]` 承担；EXECUTING 卡评论绝不触发基线 fold（无 `[修改方向]`、不退 card_sent）；空评论 noop 不动卡。同文异 ts = 两条新指令的 dedup 判例已在 `test_steer.py`/`test_actd_wire.py` 钉死，此处不重复。
- **W1 病根复现**（`tests/test_inventory_quota.py` +2 例）：critique 场景 = 100 张 delivered + 8 张 open——旧配额（live v0.20.0 硬钉 closed）下 open 全部被挤出 60 窗，反转后 open 永不掉窗、delivered 恰吃 recency 最高的 20 张；55 open + 100 delivered 时 closed 份额缩到 5（recency cap 是上限不是配额）。
- **W18 端到端**（`tests/test_webui_remote_gate.py` +3 例）:闸门每请求热读 config（开合无需重启 server）；default-deny 的降级记录进 actd 长成 RAISING 提案（origin_trust=hand）且 `executor.dispatch` 永不被叫——「转 propose」钉到卡为止；**现状钉子**：闸开的 `mode:"run"` 目前仍按普通 capture 走提案管线（§34 actd 侧未接线，向后安全）——§34 落地的 session 必须更新 `test_gate_open_mode_run_still_plain_capture_in_actd`。

---

## F — fire fixes（live 运行实证的三处守护进程病灶，2026-08-31）

三处都是 live 安装上直接观测到的事故，修复随本 PR 落地；法条按 ratification-ready 措辞给出，并入时挂靠既有 §。

### F1 — §14 追记：gmail 毒邮件围栏（宪法 11）

**live 事故**：一封 `Date` 头畸形的邮件让 email 库在 header 惰性解析处抛 `TypeError`（Python 3.9 的 `parsedate_to_datetime`，`radar_gmail.fetch_new_messages` 组装消息字段时触发），整个 gmail pass 崩掉且每轮卡在同一封邮件上。

**新法条（挂 §14）**：IMAP 路径的 per-message 解析（`message_from_bytes`、header 访问、预过滤、字段组装）整段围栏：任一步抛异常 → 该邮件按已放弃记入既有雷达重试台账 `state/radar_failed.json`（键 `gmail:uid:<n>`，`gave_up:true`——marker 已推进、无重试语义，纯案底）+ analytics 事件 `radar_message_failed{source,uid,error:<异常类型名>}`（error 只带类型名不带 message——异常文本可能内嵌邮件头内容，宪法第 9 条），pass 照常继续。案底键自带 20 条上限（uid 序挤最老）；obsidian 雷达的「note 已删除 → 销案」对账对 `gmail:uid:*` 前缀豁免（它不是 note 路径，销了 = 留痕形同虚设）。留痕两路皆 best-effort，失败只吞掉。判例：`tests/test_radar_gmail.py::PoisonMessageTestCase`、`tests/test_radar.py::PoisonLedgerReconcileTestCase`。

### F2 — §31 DOWN 追记：change-gate 摘要剔除易变字段

**live 事故**：dashboard.json 每次重建都重打 `generated_at`（内容一个字节没变也打），而 DOWN change-gate 直接 sha256 原始字节——每次重建 = 一次全量加密快照推送（live 实测 ~30.5 万次推送、2-4GB/天，全是重复上传）。

**新法条（修订 §31 DOWN 条目）**：change-gate 摘要改为「剔除易变顶层键后的 canonical JSON（sort_keys）」的 sha256；易变键表 add-only，首发只含 `generated_at`。推送 payload **仍是原始字节**（`generated_at` 保留给手机端），只有闸门摘要看剥离形；hash 只在本地、绝不上传（原语义不变）。dashboard 不是 JSON object 时退回原始字节摘要（honest fallback：坏 dashboard 顶多退回旧的逢重建必推行为，绝不漏推真变化）。升级后首轮因摘要口径切换会多推一次，一次性、无害。判例：`tests/test_syncd.py::GateDigestTestCase` / `DownTestCase::test_generated_at_only_rebuild_pushes_nothing`。

### F3 — 新条目（挂 §31/§32 旁）：常驻 daemon 日志自压缩

**live 事故**：`state/syncd.log` 涨到 74MB——actd / syncd 是 KeepAlive 常驻进程，进程内 `_log()` 逐行 append、从不轮转。

**新法条**：`state/actd.log` 与 `state/syncd.log` 沿用 `registry_writes.jsonl` 的既有自压缩模式（§34bis 写入台账）：每次 append 后检查，超过 ~1MB 只保留最近半数行（atomic tmp+replace）。实现收敛在 `act/lib/logcap.cap`（stdlib only）；单写者语义（每个日志只有它自己的 daemon 写）；压缩 best-effort，任何失败只吞掉、绝不反噬 daemon。launchd 自管的 `*.launchd.log` / cron 重定向日志不在此列（launchd 持 fd，进程内 replace 会写回旧 inode）。判例：`tests/test_logcap.py`。

### F4/F5 — 僵尸复核结论（无代码改动）

- **radar_imessage**：本树 `import act.radar_imessage` = `ModuleNotFoundError`——模块在 v0.21 已整体退役（CONTRACT §13 v0.21 弃用说明），`act/launchd/` 亦无 imessage plist。live 观测到的 `AttributeError: radar_slack._CMD_RE` 崩溃来自旧安装残件（orphan-base artifact），v0.47/v-next 无此代码，不需要也不应在本 PR 动它。
- **Models.swift 24 处重复声明**：本树 `mac/Sources/` 根本没有 Models.swift，`mac/build.sh` 全量编译 + 装配 + 签名通过（0.48.0）——该 finding 同为 orphan-base artifact，不做任何事。
