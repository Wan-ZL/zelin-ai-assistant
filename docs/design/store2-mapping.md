# store2 field mapping — registry YAML → SQLite 权威字段清单（B2）

本文是 PR2（store2 地基）的 card shape 真源，供 B1（schema.sql）、B3（migrate_yaml/export_yaml）、B4（parity 测试）直接引用。依据：live 树 `act/lib/registry.py` 全文、`docs/CONTRACT.md` §1、以及对 live registry 全部 **173 张真卡**（含 `archive/` 5 张）的逐字段 census + 10 张多状态样本细读（R-001 trashed / R-032 delivered+chat / R-048 review+rework / R-053 executing+resume / R-061 merged / R-127 former_titles / R-130 silent_merge / R-164 gmail+thread_key / archive/R-010 archived / R-000-example）。live 树只读，未做任何改动。

## 0. 读取方要先知道的四条 YAML 事实

1. **单文件单卡为主，但 list 文件是合法形态**：`registry.py` 明文支持一个 `.yaml` 装 YAML 列表（历史欠账批 R-002..R-006），`save()`/`delete()` 都有 list-member 分支。当前 173 卡 corpus 里 **0 个 list 文件**（已全部拆单），但 migrate 必须处理两种形态。
2. **`to_dict()` 的省略语义**：`_CORE_ORDER` 13 个核心字段永远序列化（哪怕 null）；`_OPTIONAL_ORDER` 字段值 `in (None, "", [], False)` 时整键跳过。**quirk：`0 == False` 为真**，所以 `silent_merge_count: 0` 也被跳过（代码注释明示这是有意的）。→ DB 读侧「键缺失」与「零值/False/空」必须同义。
3. **`delivery_mode` 只序列化非默认值**：磁盘上只会出现 `chat`（54 张）；缺失（119 张）== `repo`。migrate 时缺失一律落 `"repo"`。
4. **未知键在 `from_dict` 被静默丢弃**（kwargs 过滤到 dataclass 已知字段），一次 save 回写即消失。census 确认 corpus 现存 31 个 top-level 键全部在已知集合内，**没有野键**。**已实现决定（migrate，比 live registry 更严）**：一次性迁移丢字段不可逆，野键默认**整体 refuse**（点名键名、非零退出、零写入）；`--allow-unknown` 才显式降级为 WARN + 丢弃（from_dict 语义）。

## 1. 热列（cards 表）← YAML 顶层字段

| 热列 | YAML 来源 | 类型/空 | 事实与 quirk |
|---|---|---|---|
| `id` | `id` | TEXT PK, NOT NULL | 形如 `R-\d{3}` 但**无格式保证**：`from_dict` 对 int 手写值 `id: 4` str() 归一成 `"4"`；文件名还允许 `R-042-notes` 后缀形。存原字符串，勿加格式 CHECK。 |
| `status` | `status` | TEXT NOT NULL | 词表见 §6。**legacy `merged_into:<id>` 是合法 status 字符串**（`is_merged` 前缀判定），corpus 现存 0 例但代码路径活着。**已实现决定（B1/B3）**：热列是纯投影——legacy 串归一为 `merged` + `merged_into_id` 回填（schema 状态 CHECK 只认 11 词，verbatim 串无处安放）；**payload 真源保 verbatim 原串**，export 走 payload 逐字节还原，matching 语义差异（见 §6）由读 payload 的一侧按 `is_merged` 前缀判定。 |
| `prev_status` | `prev_status` | TEXT NULL | **被 trash 与 archive 两条路径复用**作 restore 目标（corpus 值：detected 31 / card_sent 12 / delivered 5）。restore 缺失时兜底 detected（trash 路径）/ delivered（archive 路径）。 |
| `tier` | `tier` | TEXT NOT NULL DEFAULT 'T1' | 名义枚举 T0/T1/T2（corpus：35/99/39，干净），但 `from_dict` 只做 int→str 归一**不校验值域**（`tier: 7` → `"7"` 会存活）。存 TEXT，勿加枚举 CHECK。 |
| `type` | `type` | TEXT NOT NULL DEFAULT '' | **自由 folksonomy，不是枚举**：corpus 有 60+ 个不同值，含 `vendor-deadline (OpenAI, not manager)`、`monitoring (from R-010 premium decision)` 这类 LLM 长文值，以及 `code-review`/`code_review`、`evidence_upload`/`evidence-upload` 连字符/下划线双写。永不校验、永不归一。 |
| `title` | `title` | TEXT NOT NULL | **FROZEN 身份锚**（§37）：铸卡后永不改，是 `merge_or_new`/`_same_source_and_title`/re-raise 的 dedupe 键（`_norm_title` 空白折叠+lower 比较）。人类可读名在 `display_title`（payload）。`from_dict` 对 int 值 str() 归一（数字 title 真实出现过）。 |
| `origin_trust` | **无 YAML 祖先** | TEXT | v-next 新增（信任矩阵：手打自动/外部要批）。migrate 需推导，建议启发式 = 首 source 的 `channel`（`quick_capture`/`quick` → 手打，其余 → 外部）；**推导规则未在任何契约中拍板 → `TODO(contract)`，见 §9**。 |
| `target_repo` | `target_repo`（读侧别名 `repo`） | TEXT NULL | corpus 28 张有。`from_dict` 接受 `repo:` 别名但只写 `target_repo`（corpus 现存 0 例 `repo:`）；migrate 读侧保留别名容忍。 |
| `deadline` | `deadline` | TEXT NULL | corpus：152 null / 21 个 `'YYYY-MM-DD'` 引号字符串。**比较全是字符串比较**（`_carries_increment` 用 `str() <`），存 TEXT 保语义。历史上出现过 LLM 给 bool deadline（CLAUDE.md 血泪），registry 层不校验 → migrate 遇非 str 非 null 时 str() 存原样并在 dry-run 报警。 |
| `created` | **无 YAML 祖先** | TEXT/INT | 卡片**没有任何创建时间字段**。最近似 proxy 按优先级：`card.sent_at`（corpus 0 例）> 首 source 的 `date` > 文件 mtime。推导规则需拍板 → §9。 |
| `updated` | **无 YAML 祖先** | TEXT/INT | 同上无祖先；migrate 初值 = 文件 mtime 或 migrate 时刻，此后由 store.py 写事务维护。 |
| `version` | **无 YAML 祖先** | INT NOT NULL | CAS 列，migrate 初值 1。 |
| `payload` | 其余全部字段 | TEXT(JSON) NOT NULL | 见 §2。 |

另有 BUILD-CONTRACT §3 点名的终态字段：`merged_into`（YAML 顶层，8 张 merged 卡全带）——**已实现：提升为热列 `merged_into_id`**（自引用 FK；migrate 从现代 `merged_into` 字段或 legacy status 串后缀回填），`_canonical_id` 的 lineage 跳转和 `find_open_follow_up` 接线时按它查。

## 2. payload JSON 字段（冷列）← 其余 YAML 顶层字段

「出现数」= 173 卡 census 的磁盘键出现次数（受 §0.2 省略语义影响，缺失≠从未设置）。

| 字段 | 类型 | 出现数 | 序列化组 | 语义/quirk |
|---|---|---|---|---|
| `hardness` | str `hard\|soft` | 173（core） | core | corpus 干净（soft 141 / hard 32）；`_carries_increment` 用 `soft→hard` 判升级。 |
| `repeated_mentions` | int ≥1 | 173（core） | core | fold/restatement 计数；代码里 `int(x or 1) + n` 防 None。 |
| `green_sign_required` | bool | 173（core） | core | corpus 全 false。语义=需要绿灯签核（CONTRACT §1 核心字段）。 |
| `disagreement` | str\|null | 173（core） | core | corpus 全 null。语义=分歧记录（§1 核心字段）。 |
| `cost_estimate_usd` | float\|null | 173（core） | core | LLM 回流经 `_coerce_cost`（`float()` 失败→None）。注意 `float(True)==1.0` 能穿过——bool 不被拦。 |
| `sources` | list[dict] | 173（core） | core | → §3 sources 表。 |
| `plan` | list[str]\|str\|null | 173（core） | core | dataclass 是 `Union[str,list,None]`；corpus 只有 null(118)/list(55)，但 str 是合法 wire 形（`_coerce_plan` 把多行 str 劈成 list）。migrate 保留原形态或统一 list——统一即偏离 round-trip 等价，需 B4 parity 侧同规。 |
| `summary` | str | 69 | optional | 大白话一句话（§7）；LLM 产物，re-raise 时被追加 `· 新增:<note>`。 |
| `definition_of_done` | list[str] | 24 | optional | §11 验收标准；`_apply_expansion` 截前 3 条、逐项 str()。 |
| `outputs` | list | 0 | optional | **corpus 从未出现**；仅 dashboard.py 投影时 `list(req.outputs or [])` 透传。CONTRACT §1 列为 `outputs?`。保留 payload 位，语义=交付物描述（弱确定，见 §9）。 |
| `card` | dict | 0 | optional | **corpus 从未出现**。CONTRACT §1 形状 `{sent_at, slack_ts?, slack_channel?}`；唯一读者 `oneonone.py` 取 `card.sent_at` 算卡龄。历史 Slack 审批通道退役后基本死键，但读者还在——保留 payload。 |
| `execution` | dict | 48 | optional | → §4。 |
| `improvement_of` | str(R-id) | 4 | optional | 增量子卡/follow-up 的 lineage 指针，`find_open_follow_up` 按 canonical 化后的它 dedupe。 |
| `merged_into` | str(R-id) | 8 | optional | merged 终态的主卡指针（`merged_parent` 读它或 legacy status 后缀）。见 §1 末行的热列建议。 |
| `target_kind` | str `new\|existing` | 46 | optional | actd 计算（目录存在且非空→existing）；`_apply_expansion` 白名单校验。 |
| `delivery_mode` | str `chat\|repo` | 54（只存 chat） | optional 特例 | 见 §0.3；读侧未知值→`repo`（`from_dict` 白名单）。 |
| `notes` | str（多行 blob） | 100 | optional | → §5 notes 表。 |
| `trashed_at` | str ISO UTC | 43 | optional | `%Y-%m-%dT%H:%M:%SZ`。 |
| `trash_reason` | str | 43 | optional | **不是二值枚举**：文档说 `rejected\|deleted`，corpus 实有第三形 `silent-merge: 已并入 R-xxx`（§44 静默并入写的自由文本，3 例）。存 TEXT 原样。 |
| `permanent` | bool | 0 | optional | 回收站钉住（retention 不硬删）；False 被 to_dict 跳过，corpus 0 例=没人钉过。 |
| `thread_id` | str(R-id) | 123 | optional | thread 锚=thread 根卡的 R-id（复用 R- 命名空间），lazily backfill（`or parent.id`/`or self.id`）。 |
| `thread_key` | str | 8 | optional | 强确定桶，仅 `gmail:<X-GM-THRID>` / `slack:<thread_ts>` 两形（`derive_thread_key`），永不模糊。corpus 8 例全 gmail。matching 需按它查 → 建议 payload 之外建表达式索引或提热列（§9）。 |
| `archived_at` | str ISO UTC | 5 | optional | 只在 `archive/` 子目录卡上。 |
| `archive_reason` | str `user\|auto` | 5 | optional | corpus 全 `user`。 |
| `display_title` | str ≤64 | 40 | optional | §37 活显示名；写入唯一落笔点 `set_display_title`：clip+空白折叠、**含 `sanitize.MASK` 一律拒收**。 |
| `user_titled` | bool | 0 | optional | True=用户钉名，LLM/harvest 永不覆写。corpus 0 例（False 被跳过）。 |
| `former_titles` | list[str] | 1 | optional | 旧显示名，dedupe、newest last、cap 3（`FORMER_TITLES_CAP`），保可搜索性。 |
| `split_from` | str(R-id) | 0 | optional | §38 拆卡 lineage，机器可读（auto_merge 永不建议把拆出的卡并回去）。代码活跃、corpus 暂 0。 |
| `silent_merge_count` | int | 3 | optional | §44 静默并入次数（区别于 repeated_mentions）；0 不序列化（§0.2）。 |
| `preset` | str | 0 | optional | §34bis 预置 plan 卡标记（词表现仅 `proposals_triage`）。**刻意放顶层不放 execution**——dispatch 成功路径会整个重建 execution dict，放里面活不过起跑。代码活跃、corpus 暂 0。 |

## 3. `sources[]` → sources 表

corpus 388 条 source dict 的键 census：`channel`/`date`/`quote`/`who` 各 388（事实必填），`ref` 343（可缺），`gmail_thread_id` 8。代码另定义 `slack_thread_ts`（`derive_thread_key` 读它，slack radar 写方），corpus 暂 0 —— **add-only wire 键，建列或留在 source 行 JSON 里都要预留**。

| 键 | 类型 | 事实 |
|---|---|---|
| `channel` | str | corpus 词表：meeting 272 / quick_capture 38 / claude_code 29 / weekly-digest 23 / gmail 8 / quick 7 / digest 5 / radar-diagnostic 3 / analytics 3。**quick vs quick_capture、digest vs weekly-digest 双写并存**——不是枚举，勿归一。 |
| `date` | str | **格式不统一**：多为 `'YYYY-MM-DD'`，gmail 源是 RFC 2822 全文（`Thu, 06 Feb 2025 15:54:59 +0000`）。dedup 键里 `str(date)` 原样比较，存原样。 |
| `quote` | str | 引文，可极长（archive/R-010 单条 quote 数 KB）。TEXT 无上限。 |
| `who` | str | 自由文本：`manager`/`zelin`/邮箱全称/甚至整个 screenpipe 文件名 slug。 |
| `ref` | str\|缺失 | 文件路径 / `act.weekly_digest` / gmail Message-ID 等。 |
| `gmail_thread_id` | str\|缺失 | 强 thread 信号（注意 YAML 里是引号字符串 `'1823324031954270241'`，勿转 int——超 int53 且是标识符）。 |
| `slack_thread_ts` | str\|缺失 | 代码定义、corpus 0 例。 |

**应用层去重键逐字复刻 `registry._dedupe_sources`**：`(channel.lower(), str(date), (ref or quote).strip().lower())` —— 注意第三元是 **ref 优先、缺 ref 才退 quote** 的 fallback。这个三元组是**应用层语义，不进 DB 键**。**已实现决定（B1/B3）**：schema 的 `origin_key` 只存外部强信号——`slack:<ts>` / `gmail:<message_id>` 两形，无强信号一律 NULL、绝不 fuzzy（对齐 §10 thread_key 纪律）；`(channel, origin_key)` partial-unique 只约束强信号行。**migration 一律写 NULL**（回溯推导强信号有同 thread 多卡撞 partial-unique 的风险，留给接线后的写路径）。source 行无自然主键、无时间序保证（list 顺序即到达顺序，**必须保序**——`sources[0]` 是 thread_key 推导的首选源）。

**wiring PR checklist（PR3 接线必读）**：migration 后 `sources.origin_key` 全为 NULL → 在写路径接线并回填之前，**历史线程的 DB 级去重（同一条外部消息只喂一张卡）存在空窗**。接线时需：① 新写入立即带 origin_key；② 对历史行做一次防撞回填（撞 `(channel, origin_key)` 键的行保留 NULL 并报告，人工核对）；③ 回填完成前 thread 级 dedupe 仍走 payload.thread_key 的应用层查询，不得依赖 `sources_dedup` 索引。

## 4. `execution.*` 词表 → dispatches 表 vs payload

execution 是**单写者 actd/executor 反复整体重建的杂物抽屉**，混了三类语义。corpus+代码全集（corpus 出现数标注，无标注=仅代码可见）：

**A. dispatch/runtime 类 → dispatches 表**（BUILD-CONTRACT §3「runtime 字段独立于 cards」的对象）：
`session_id`(37) · `dispatched_at`(38) · `log`(38) · `root_session_id`(4, UUID 全形) · `resume_attempts`(1) · `last_resume_at`(1) · `last_resume_ok`(1, bool) · `resume_exhausted`(bool) · `resume_storm_at` · `dispatch_attempts` · `last_dispatch_attempt_at` · `last_error` · `last_error_at` · `aborted_session_id`(1) · `aborted_at`(1) · `reraised_session_id`（re-raise 时归档的旧 session_id，registry.py:1091——**新一轮 dispatch 的死键前提**：dispatch_approved 见到活 session_id 就跳过） · `stop_failed_at` · `stop_failed_error`。

**B. 交付/验收 artifact 类 → 留 payload.execution**（属卡不属 dispatch 轮次）：
`delivered_summary`(46, 可数 KB) · `final_draft`(43, 成稿全文) · `review_at`(46) · `accepted_at`(29) · `done`(36, bool) · `rework_count`(3) · `last_rework_at`(3) · `reverted_at`(1) · `reraised_at` · `reraised_note` · `_review_active`(3, bool——**带下划线但确实落盘**，actd.py:2665 attach 回流 roster 标记，收工即 pop；migrate 原样保留)。

**C. 投递/簿记类 → 归属需 B1/B3 拍板（§9）**：
`inbox_stem`（capture[run] 幂等 replay 键，actd.py:739 按它查重——若 dispatches 表不存它，replay 查询要回 payload 扫描） · `answer_count` / `last_answer_at`（needs_input 回答簿记） · `pending_briefings` / `delivered_briefings` / `briefing_count` / `briefing_attempts` / `last_briefing_at`（§44.3 静默并入向活 session 投递 briefing 的队列+签收，语义≈notes 表的 `delivered_at/acked_at`） · `registry_snapshot_ref`（§34bis 快照护栏引用，actd.py:591 **用后即焚 pop**——迁移瞬间存在即保留） · `merged_deliverables` · `attachments`（§10bis 附件路径列表，**GC 的引用源**：附件 GC 对 registry 做 strict 逐文件解析，任一卡读不出→整轮零删除；store2 上线后这条 GC 引用链要改走 DB，PR2 不接线但 schema 别把它埋深） · `approved_at`。

**闸门事实（B1 trigger 需要）**：`one_active WHERE status='running'` 的 partial-unique 对应现行不变量「一卡一活 session」；re-raise 路径证明**同一卡可有多轮历史 dispatch**（reraised_session_id / aborted_session_id 就是前轮遗迹）——dispatches 按轮次多行、cards 不存 session_id 是正确解法。

**易混警告**：config.yaml 也有个 `execution:` 配置节（`skip_permissions`/`quality_gate`/`memory_inject`/`claude_bin`/`auto_resume`/`create_github_repo`/`default_target_repo`）——那是 **config 键，不是卡字段**，两者仅同名。grep 时别混入。

## 5. `notes` blob → notes 表

YAML `notes` 是**换行拼接的混合 blob**，含四种行：

1. fold 行（结构化，FROZEN 形状）：`[radar|quick] <text> [@<ts>]`（+可选 ` [已拆出 R-yyy]`）——`parse_fold_notes` 是唯一解析器，legacy 无 ts 行合法（ts=None，不可拆）；同秒多 fold 用 `#n` 后缀保 ts 唯一（拆卡 handle）。Swift 端 Cards.swift 有镜像 parser，形状锁死。
2. `[re-raised] <note>` 行（registry.py:1077，非 fold 形，parse_fold_notes 跳过它）。
3. `[deferred] 暂缓，入库` 类动作回执行。
4. 纯 prose（`from app quick capture`、`needs_reply=False · from Gmail`、`(auto-expand failed, needs manual)` fallback tag——注意这条是**空格拼接**不是换行）。

**mapping 决定**：fold 行可投影进 notes 表（kind/text/ts/split_into 四列现成），但 2-4 类没有结构——**payload 里必须原样保留完整 blob 作真源**，notes 表只做投影/索引，否则 export_yaml 无法逐字节 round-trip（B4 parity 的硬要求）。dedupe 键 = `(kind, text)`（append_fold_note 语义，retry 无害不变量）。

## 6. status 词表与状态机事实（B1 trigger 输入）

CONTRACT §1 状态机 + State enum 全集（11 值）与 corpus 分布：`detected` 53 · `card_sent` 23 · `raising` 0 · `approved` 0 · `executing` 1 · `review` 17 · `delivered` 24 · `rejected` 0 · `trashed` 43 · `merged` 8 · `archived` 5（仅 archive/ 目录）。

- `approved`/`raising` 是**短命过渡态**（actd 快速消化），corpus 抓拍不到 ≠ 不存在，trigger 转移表必须包含。
- `rejected` 0 例的原因：reject 动作实际走 `trash(reason="rejected")` 落 `trashed`——`rejected` 态在现行管线近乎理论态，但 enum/matchable 都还引用它，勿删。
- legacy `merged_into:<id>` verbatim status：与终态 `merged` **matching 语义相反**——`matchable()` 排除 legacy（`is_merged`）但放行 `merged`（当 delivered 参与匹配压重述，决策 6）。**已实现决定**：热列投影归一为 `merged` + `merged_into_id`（schema 状态 CHECK 只认 11 词，verbatim 串进不了热列，trigger 白名单也不含前缀形）；**payload 真源保留原字符串**，export 走 payload 不失真，matching 语义差异由消费 payload 的一侧按 `is_merged` 前缀判定——热列不承载这层区别。
- 终态分立（schema 既有设计确认）：`rejected/trashed/merged/archived` 四终态 + `merged_into_id`；`trashed`/`archived` 可逆（restore/unarchive 走 `prev_status`），`merged` 不可逆（UI 明示）。
- **archive 目录语义**：archived 卡物理搬到 `archive/`，热扫描（非递归 glob）天然跳过；crash-mid-move 会留**双份**，archive 副本权威（load() 明文规则）。migrate 扫描两目录时**同 id 冲突取 archive 版**，并在 dry-run 报 residue。`R-000-example.yaml` 按文件名排除（永不入库）。

## 7. LLM 污染与 sanitizer 护栏（migrate/store 读侧必须同等容忍）

这些是 sanitizer 真实拦过的畸形，store2 读侧遇到时**照 registry 语义归一，绝不 raise**（宪法第 11 条：解析失败不许崩 pass）：

- `id`/`title`/`tier` 手写/LLM 给成 int → str() 归一（`from_dict`；数字 title、`id: 4` 真实出现过；int id 会让 `next_id` 正则 TypeError、int title 会让 Swift 硬 String decode 清空整列）。
- `deadline` 给成 bool/int → registry 不拦（历史血泪在下游消毒）；migrate 遇非 str 非 null 存 str() 原样 + dry-run 报警。
- `plan` 给成 str → 合法（多行劈 list 是消费侧的事）；给成其他类型 → `_coerce_plan` 归 []。
- `cost_estimate_usd` 非数字 → `_coerce_cost` 归 None。
- `delivery_mode` 未知值 → `repo`。`target_kind` 白名单 `new|existing` 外丢弃。
- `definition_of_done` 非 list 丢弃；list 内逐项 str()、cap 3。
- `display_title` 经 clip（≤64、空白折叠）+ `sanitize.MASK` 拒收；`former_titles` 逐项 str() 过滤空白。
- sources 列表内**非 dict 项合法存在可能**（`_dedupe_sources`/`load_all` 都静默跳过 non-dict）——migrate 跳过 + dry-run 报。
- 坏文件语义：单卡文件损坏 → load_all 跳过（但 `next_id` 按**文件名**计 id 防复用覆盖）；migrate 必须同样跳过 + 报告，且**绝不**因一个坏文件废整轮（对照附件 GC 的 fail-safe 先例）。
- 空文件（`yaml.safe_load` → None）→ 跳过。

## 8. add-only wire 字段 vs 内部字段

- **add-only wire 字段**（跨组件契约，dashboard/Swift/iOS 都按 `decodeIfPresent` 消费，**改名/改型/删除 = 违宪**）：§1/§2 全部顶层字段 + `execution` 内 A/B 两类 + sources 键 + fold 行文本形状。store2 的 export_yaml 输出必须逐字段可还原它们。
- **内部字段（永不序列化）**：`Requirement._file` / `_in_list`（dataclass repr=False 簿记）——store2 的对应物是 DB 行本身，migrate 不搬、export 不产。
- **落盘但带下划线**：`execution._review_active`——不是内部字段，是 wire 上的临时标记（见 §4B），照搬。
- **旁路台账（不属卡片，勿入 cards 表）**：`state/registry_writes.jsonl`（§34bis 写入台账）是 registry 模块的副产物，store2 写路径接线时（PR3+）需要等价物（activities 表天然覆盖），PR2 不管。

## 9. FLAGGED — 语义未定/需要拍板的字段（`TODO(contract)` 全集）

1. **`origin_trust` 推导规则**：无 YAML 祖先。channel 启发式（§1 表）未经契约拍板；`claude_code`/`meeting`/`digest` 源算哪档不明确。→ B3 实现时 `# TODO(contract): origin_trust derivation`，默认取最保守档（外部要批）。
2. **`created` 推导**：无祖先无 proxy 共识（card.sent_at corpus 0 例）。建议 sources[0].date 优先、文件 mtime 兜底，dry-run 列出每卡取值来源。→ `TODO(contract)`。
3. **`merged_into` / `thread_key` 是否提热列**：matching（`_canonical_id`、thread_key 首匹配）都按它们查；BUILD-CONTRACT §3 热列清单没点名。留 payload 则 PR3 接线时全表扫。→ B1 拍板，本文建议至少 `merged_into` 提列（schema 已有 `merged_into_id` 名额）。
4. **`outputs`**：CONTRACT §1 点名、dashboard 透传、corpus 0 例、无写方可寻——语义只能推断为「交付物描述列表」。**未能完全确定**。
5. **`card` dict**：Slack 审批通道退役后疑似死键但 oneonone.py 还读 `sent_at`。`slack_ts`/`slack_channel` 的现行语义**未能确定**（通道已 v0.21 移除）。原样入 payload。
6. **execution C 类键归属**（§4C 全列表）：`inbox_stem` 的幂等查询、briefing 队列与 notes 表 `delivered_at/acked_at` 的关系、`merged_deliverables` 的确切形状（仅 1 处代码引用，形状**未读到写方**）——PR3 接线前需逐个拍板；PR2 一律原样留 payload.execution，parity 不受影响。
7. **`plan` 的 str 形态**：migrate 统一成 list 会破坏逐字节 round-trip；不统一则 DB 消费方要处理双形。→ B3/B4 同步拍板（本文建议：**原样保留**，round-trip 优先）。
8. **notes blob 双写**（§5）：payload 真源 + notes 表投影的一致性由谁维护（PR2 只 migrate 一次性投影，无一致性问题；PR3 接线时 fold 写路径要双写或改单源）。→ 修宪草案（A12 的 vnext.md）应预留此条。
9. **`type` 值域**：60+ 自由值无归一计划；若 v-next UI 要按 type filter，归一是产品决定不是 migrate 决定。→ 不动，flag 给 UI 团队。
10. **crash-mid-move 双份 residue**：migrate 遇同 id 双份取 archive 版（load() 语义），但**是否顺手清理 active 残件**超出「不接线」边界 → 不清理，dry-run 报告。

## 10. 给 B1/B3/B4 的交接摘要

- B1（schema）：status 词表 §6（含 legacy 前缀白名单）、终态四分立、`prev_status` 双用途、sources 去重三元组 §3、dispatches 收 §4A、trigger 拒 `actor_type='agent'` 的 approve/accept 类转移。
- B3（migrate/export）：§0 的四条 YAML 事实、§6 archive 双份规则、§7 全部容忍规则、§9.1/2 的推导 TODO、export 按 `_CORE_ORDER`+`_OPTIONAL_ORDER` 顺序与省略语义逐字节还原（`yaml.safe_dump(allow_unicode=True, sort_keys=False, width=100)`）。
- B4（parity）：省略语义 §0.2（0/False/空串/空列表）、plan 双形 §9.7、多源卡 dedupe 键 §3、`R-000-example.yaml` 排除、坏文件跳过语义 §7。
