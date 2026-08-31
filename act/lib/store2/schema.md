# store2 schema v1 — 设计说明（B1；BUILD-CONTRACT §3）

`schema.sql` 是 v-next SQLite 地基的唯一 DDL 真源（`PRAGMA user_version=1`）。本 PR 不接线：actd 不 import store2，YAML registry 仍是生产真源。本文记录每个结构决定的出处与取舍，供 B2（store.py）/B3（migrate/export）/B4（测试）与未来修宪引用。

## 表设计

### cards — 热列 + 冷列分层

热列 = 看板投影、过滤、状态机执法要用的字段（id/status/prev_status/tier/type/title/origin_trust/target_repo/deadline/created/updated/version）；其余 YAML 字段（sources 冗余副本、plan、definition_of_done、card、execution、notes 文本、thread_id、display_title、silent_merge_count……）JSON 化后原样进 `payload` 冷列。理由：live CONTRACT 的字段是 add-only 长尾（§1 + 30 多节追加），逐列建模会让每次加字段都变 DDL migration；冷列让 add-only 字段继续零成本添加，热列只收「查询要用」的少数派。`json_valid(payload)` CHECK 挡住半截 JSON。

- `version`：乐观锁 CAS 列，抄 dashi `tasks.version`（`CHECK (version > 0)`，写路径 `WHERE id=? AND version=?` + `changes!=1` 重查分 404/409——三件套在 B2）。
- `origin_trust`：v-next 信任矩阵（owner 决策「手打自动/外部要批」）。词表 `hand|external`，默认 `external` fail-closed（出身不明一律走审批）。`// TODO(contract):` live CONTRACT 尚无此字段，词表与判定规则需在 docs/design/vnext.md 修宪落档。
- `merged_into_id`：§21 merge 终态父指针，自引用 FK。legacy `merged_into:<id>` 状态串（registry.py `MERGED_PREFIX`）由 B3 归一为 `status='merged'` + `merged_into_id`。
- `prev_status`：§9/§10 的回程票。CHECK 强制 trashed/archived 必带（宪法第 2 条「一切可逆」的 schema 化）；B3 对缺失的 legacy 卡按 live `registry.restore`/`unarchive` 的 fallback 回填（trashed→`'detected'`，archived→`'delivered'`）。
- `last_actor_type`：状态机 trigger 的 actor 输入。SQLite trigger 无法看到「谁」在 UPDATE，所以 actor 随行入列——**B2 约定：任何 status UPDATE 必须同时 `SET last_actor_type`**。已知限制：writer 忘 SET 时 NEW 继承 OLD 值，trigger 按旧 actor 判——这是 backstop 不是唯一防线，真正的执法闭环 = B2 强制传参 + activities 全量审计 + B4 的 trigger 拒绝测试。
- `deadline` 用 GLOB 钉死 `YYYY-MM-DD`（§1 词形）；`created/updated` 存 ISO-8601 UTC 字符串（与 registry/dashi 一致，SQLite 字符串序即时间序）。

### 终态分立 + tombstone 进 revision 流

`rejected`（legacy 旁支）/`trashed`/`merged`/`archived` 是四个**分立**状态值而非布尔旗标——它们的可见性与匹配语义互不相同（registry.py 注释钉死：merged 参与 merge_or_new 压重述、trashed 排除、archived 排除且 NEVER purge），压成旗标必然丢语义。

删除 = tombstone，不是 DELETE（trigger `cards_no_hard_delete` 强制）：§9 回收站保留期到期的硬删在 store2 里变成 `tombstone=1` + `payload='{}'` + bump `board_rev`。行骨架（id + board_rev）永久保留，因此增量同步客户端（`WHERE board_rev > :since`）能学到删除——这就是「删除写 tombstone 进 revision 流」。CHECK `tombstone=0 OR status='trashed'` 钉死只有回收站可 purge（archived NEVER purge，§10）。tombstone 行整行冻结（trigger）。

### sources — `(channel, origin_key)` partial-unique

引文行从 payload 里拆出来单独成表，因为去重键需要 UNIQUE 索引执法。`origin_key` 只收外部强信号（`slack:<ts>`/`gmail:<message_id>`，对齐 §10 thread_key 的「无强信号=NULL、绝不 fuzzy」纪律），partial-unique `WHERE origin_key IS NOT NULL` 让手工/meeting 引文（无 key）不受约束。**全局**唯一（不含 card_id）= 一条外部消息永远只属于一张卡：merge 并卡时 UPDATE card_id 重指主卡而非复制插入，天然去重。

### notes — append-only + 双回执

comment/steer/fold 落 notes 表，append-only（trigger 执法）。仅有的两个可写列 `delivered_at`/`acked_at` 各 set-once：§32.2 审计教训——「改方向」必须有真实回执，folded note 没被 session 消费就不许谎报已生效。YAML 时代 notes 是字符串列表、fold note 靠 `[@ts]` 前缀标时间（§38.2 split_note 的解析锚点）；表化后时间戳成一等列，split lineage 仍走 cards.payload 的 `split_from`。

### dispatches — runtime 与 cards 解耦

YAML 时代 session 账目挤在 `execution.*` 里且每轮 dispatch 整个重建（CONTRACT 里 aborted_session_id/reraised_session_id 之类的「归档改名」全是这个单槽位的补丁）。表化后一轮一行，rework/re-raise/abort-重批天然成历史序列。`one_active` partial unique（`WHERE status='running'`）= 一卡至多一个活 session，抄 dashi `ai_chat_runs_one_active`，§46 确认式停止语义的数据库层。`runtime` 留 TEXT 不加 CHECK：今日仅 `'claude'`，多 runtime 是 v-next 明牌方向，锁枚举反而要 migration。`CHECK ((status='running') = (finished_at IS NULL))` 防「已收尾还挂 running」的悬挂账（对齐 §21「任何异常必须落 failed，绝不留 analyzing 悬挂」的纪律）。

### activities / board_revision

activities = dashi `task_activities` 同型审计流（`changes` JSON `[{field,before,after}]` + actor 三态），append-only 双 trigger。board_revision = dashi `comment_attachment_revision` 同型单行游标（`CHECK (id=1)`），monotonic trigger 拒绝回拨。**B2 写事务约定：`BEGIN IMMEDIATE` → 游标 +1 → 新值盖到被触碰卡的 `board_rev`（子表变更 bump 所属卡）→ COMMIT。**

## 状态机 trigger — 转移表逐条出处

合法转移不写死在 trigger body 里，而是数据表 `transition_whitelist(old,new,actor)`（fail-closed：查不到 = `ILLEGAL_TRANSITION`）。追加合法转移 = INSERT 行，add-only。每行出处（live CONTRACT + actd handler 的 allowed-status 集合逐条核对）：

| 转移 | actor | 出处 |
|---|---|---|
| detected/card_sent → raising | user, system | §8 raise；actd `raise` handler 允许两态；capture relates_to |
| raising → card_sent | system, user | §8 扩写完成/失败兜底；comment 折回重审批（actd §32.2 分支） |
| detected → card_sent | system | §10 defer「雷达 act-now 重提自动升回」/ 命中提升 |
| detected/card_sent → approved | **user 独占** | §3 approve；actd approve handler 允许两态 |
| approved → executing | system | §4 dispatch |
| executing → review | system, user | reconcile 自然完成；§10 stop_to_review |
| approved/review → review | user | §10 stop_to_review 允许 `executing\|approved\|review` |
| review → delivered | **user 独占** | 验收 |
| card_sent/approved/executing/review → delivered | user | §10 done_external 状态白名单（v0.12 扩展）逐字 |
| approved/executing/review → card_sent | user | §10 abort_execution（v0.28.1 含 review） |
| delivered → review | user, system | §10 revert_review；§24 digest 卡刷新拉回 |
| review → executing | user, system | rework 打回；§30 session_active 同调翻回；§21 merge rework 注入 |
| card_sent → detected | user | §10 defer（仅 card_sent） |
| delivered → card_sent | system | §10 re-raise 回锅（canonical 必为 delivered 才翻） |
| delivered/detected → archived | user（+ system 仅 delivered） | §10 archive Q2；auto-archive 只封存冷 delivered |
| archived → delivered/detected | user | §10 unarchive 回 prev_status（只可能这两态） |
| 任意态 → trashed | user, system | CONTRACT header「any state → trashed」 |
| trashed → 任意态 | user | §9 restore 精确复位 prev_status（含 merged/rejected/archived 等罕见来路，不许 brick） |
| 活状态/delivered → merged | user | §21 merge_apply/merge_force 全程用户拍板，actd 只是确定性执行者 |

**actor 语义**：actor = 动作的**发起者**，不是写库进程——actd 替用户执行 inbox 动作时记 `user`；radar/triage/digest/auto-archive 等自主管线记 `system`；headless 执行 session 及一切旁路进程记 `agent`。

**D3 权限墙**：whitelist 里 `agent` 行数为零（宪法第 1 条单写者的 SQL 化：agent 对 status 零写权）；此外 approve/accept 类（`NEW.status IN ('approved','delivered')`）叠加点名 trigger `AGENT_TRANSITION_FORBIDDEN`（同一 trigger 内先于 whitelist 查询，报错语义更清晰），INSERT 面由 `cards_agent_insert_wall` 补齐（防 agent 直接铸 approved/delivered 卡绕过 UPDATE 执法）。

**INSERT 不限出生状态**：合法出生点很多（detected/card_sent/raising=capture propose/approved=capture mode:"run" 手打直跑/review=weekly digest 卡），且 B3 migration 要按原状态整库 INSERT——出生资格是 §45 决策表的业务判断，放应用层；schema 只挡 agent 铸批准卡。

## TODO(contract) 清单（修宪草案素材，A12/集成 agent 汇总）

- `origin_trust` 词表（`hand|external`）与判定规则未入宪——需在 docs/design/vnext.md 落「信任矩阵」条款。
- §24 digest 卡「其余状态一律拉回 review」只收录了 `delivered → review (system)`；detected/card_sent/approved/executing → review 的 digest 专属拉回未开白名单（现实中 digest 卡不经这些态），如接线后撞 `ILLEGAL_TRANSITION` 再按 add-only 补行。
- `dispatches.status` 词表（`running|completed|failed|stopped`）为本 PR 拟定，live CONTRACT 无对应法条，接线 PR 需入宪。
- queued（approved）卡的「取消排队」独立动作不存在（现行走 abort_execution），如 v-next UI 新增动词需同步补 whitelist 行。
- notes.kind 只收 `comment|steer|fold` 三值（BUILD-CONTRACT §3 原文）；§39 answer_input 的回答若也要入 notes 流，需加值（add-only 的 CHECK 改动 = 表重建，建议接线 PR 一并定稿）。

## 给 B2/B3/B4 的接口约定

- 连接：`PRAGMA foreign_keys=ON`（per-connection！）+ WAL + busy_timeout=5000，每线程一连接（B2）。
- 写事务：`BEGIN IMMEDIATE`；status UPDATE 必带 `SET last_actor_type=?`；每笔事务 bump board_revision 并回盖 `board_rev`。
- CAS：`UPDATE ... WHERE id=? AND version=?`，`changes!=1` → 重查分 404/409（dashi database.mjs:2181-2211 模式）。
- migration（B3）：纯 INSERT（trigger 不拦出生）；legacy `merged_into:<id>` 状态归一；缺失 prev_status 按上文 fallback 回填；`--dry-run` + 回读等价校验。
- 测试锚点（B4）：CAS 冲突、`AGENT_TRANSITION_FORBIDDEN`（UPDATE 与 INSERT 两面）、`ILLEGAL_TRANSITION`、tombstone 冻结 + board_rev 增量可见、notes/activities append-only、`sources_dedup`、`dispatches_one_active`、board_revision 回拨拒绝。
