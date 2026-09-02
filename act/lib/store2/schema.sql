-- store2 schema v2 — SQLite 真源的 DDL（CONTRACT §53；v0.48.8 起接线，registry 门面是唯一调用者）
--
-- 版本史（§53.1 升级梯子在 store.py `_UPGRADES`，每级一个幂等函数）：
--   v1  v0.48.8  首版（cards/sources/notes/dispatches/activities/board_revision + 触发器）
--   v2  v0.48.14  §60（D21）两段式编号：cards.work_id 列 + 唯一索引 + set-once 触发器
-- 本文件永远是「全新库的完整 DDL」；已有库按 user_version 逐级走 _UPGRADES。
-- **两条路必须收敛到同一形状**（判例 tests/test_two_stage_card_ids.py 比对 sqlite_master）。
--
-- 设计根据（真源只读参考，勿改）：
--   * live docs/CONTRACT.md §1 状态机 + §8/§9/§10/§21/§24/§30/§45 各转移法条
--   * live act/lib/registry.py（State 枚举 / prev_status / merged_into 语义）
--   * live act/actd.py inbox handlers（每个动作的 allowed-status 白名单，逐条核对）
--   * dashi database.mjs 的 DDL 惯用法（CHECK 枚举 / partial unique / RAISE trigger /
--     version CAS 列 / 单行 revision 表）——只仿 pattern，不搬 Node 代码
--
-- 约定（B2 store.py 必须遵守，schema 层只做 backstop）：
--   * PRAGMA foreign_keys=ON 是 per-connection 设置，B2 每个连接都要开
--   * 每笔写事务：BEGIN IMMEDIATE → bump board_revision → 把新 value 盖到被触
--     碰卡片的 board_rev → COMMIT（子表 notes/dispatches 变更也 bump 所属卡）
--   * 所有 status UPDATE 必须同时 SET last_actor_type（trigger 靠它判 actor）
--   * 硬删卡片被禁止（trigger）：回收站保留期到期 = tombstone 化
--     （tombstone=1、payload='{}'、bump board_rev），删除因此进 revision 流
--
-- 字段纪律与 YAML registry 同一条宪法：add-only，只增不改不删不重编号。
--
-- 版本钉扎在**文件末尾**：executescript 途中崩溃时版本必须还是 0，
-- _ensure_schema 重跑才会补全建表——版本号先行会把半截库伪装成完工库，
-- 击穿版本门（crash window）。

-- ---------------------------------------------------------------------------
-- cards — 卡片主表：热列（看板投影/过滤要用的）+ payload JSON 冷列（其余字段
-- 原样存 registry YAML 的 JSON 化全文：sources/plan/dod/card/execution/notes…）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
  id              TEXT PRIMARY KEY,            -- 主键：'P-xxx'（v0.48.14 起出生）或存量 'R-xxx'（legacy），永不改写
  status          TEXT NOT NULL CHECK (status IN (
    'detected', 'card_sent', 'raising', 'approved', 'executing',
    'review', 'delivered', 'rejected', 'trashed', 'merged', 'archived'
  )),
  -- prev_status：trash/archive 的回程票（§9/§10）——restore/unarchive 按它精确复位
  prev_status     TEXT CHECK (prev_status IS NULL OR prev_status IN (
    'detected', 'card_sent', 'raising', 'approved', 'executing',
    'review', 'delivered', 'rejected', 'trashed', 'merged', 'archived'
  )),
  tier            TEXT NOT NULL DEFAULT 'T1' CHECK (tier IN ('T0', 'T1', 'T2')),
  type            TEXT NOT NULL DEFAULT '',
  title           TEXT NOT NULL,
  -- origin_trust：v-next 信任矩阵（手打自动 / 外部要批）。默认 external = fail-closed
  -- （出身不明的卡一律走审批）。词表 = §50 四值 canonical（act/lib/policy.py
  -- ORIGINS 单一真源；T-15 已定，PR #106 终审落地放宽）
  origin_trust    TEXT NOT NULL DEFAULT 'external'
                  CHECK (origin_trust IN ('hand', 'proposed', 'meeting', 'external')),
  target_repo     TEXT,
  deadline        TEXT CHECK (deadline IS NULL OR
                    deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  created         TEXT NOT NULL,               -- ISO-8601 UTC
  updated         TEXT NOT NULL,               -- ISO-8601 UTC
  -- version：乐观锁 CAS 列（dashi 惯用法）。写路径 WHERE id=? AND version=?，
  -- changes!=1 时重查分 404/409 —— 具体三件套在 store.py（B2）
  version         INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  -- merged 终态的父指针（§21）：副卡并入主卡。legacy 'merged_into:<id>' 状态串
  -- 由 migrate_yaml（B3）归一为 status='merged' + merged_into_id
  merged_into_id  TEXT REFERENCES cards(id),
  -- board_rev：本行最后一次变更时的全局 revision（board_revision.value 快照）。
  -- 客户端增量同步 = WHERE board_rev > :since（含 tombstone 行 → 学到删除）
  board_rev       INTEGER NOT NULL DEFAULT 0,
  -- tombstone：回收站保留期硬删的替身。行保留（id + board_rev 可同步），内容清空
  tombstone       INTEGER NOT NULL DEFAULT 0 CHECK (tombstone IN (0, 1)),
  -- last_actor_type：本行最近一次写入者。状态机 trigger 读它执法；
  -- 每次 status UPDATE 都必须显式 SET（B2 约定），activities 表留完整审计
  last_actor_type TEXT NOT NULL DEFAULT 'system'
                  CHECK (last_actor_type IN ('user', 'agent', 'system')),
  payload         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
  -- work_id（schema v2，§60/D21）：人看的工作编号 'R-<m>'，卡进入 approved 时由
  -- registry.save 分配、set-once（trigger cards_work_id_set_once）。NULL = 从未批准
  -- 过（提案/备选/回收站）或存量 legacy 卡。热列而非只进 payload：purge_trashed 清
  -- payload 但保留热列——已硬删卡的编号照样占位，序列永不复用（§60.2）。
  -- 注：v1→v2 升级用 ALTER TABLE ADD COLUMN 追加，列位置在 payload 之后（这里同序）
  work_id         TEXT,
  -- 终态一致性：merged 必带父指针；trashed/archived 必带回程票
  -- （migrate_yaml 对缺 prev_status 的 legacy 卡按 live registry 的 restore/
  --  unarchive fallback 回填：trashed→'detected'，archived→'delivered'）
  CHECK (status <> 'merged'   OR merged_into_id IS NOT NULL),
  CHECK (status <> 'trashed'  OR prev_status IS NOT NULL),
  CHECK (status <> 'archived' OR prev_status IS NOT NULL),
  -- 只有回收站里的卡才可能被 tombstone 化（保留期硬删仅作用于 trashed，§9；
  -- archived NEVER purge，§10）
  CHECK (tombstone = 0 OR status = 'trashed')
);

-- 看板投影按 status 拉列（排除 tombstone）；增量同步按 board_rev
CREATE INDEX IF NOT EXISTS cards_status_live
  ON cards(status, updated) WHERE tombstone = 0;
CREATE INDEX IF NOT EXISTS cards_board_rev
  ON cards(board_rev);
-- 工作编号全局唯一（§60.2）：并发分配撞号 = IntegrityError → StoreError，绝不静默复用；
-- 也是 resolve(work_id) 的查找索引
CREATE UNIQUE INDEX IF NOT EXISTS cards_work_id
  ON cards(work_id) WHERE work_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- sources — 卡片来源引文（CONTRACT §1 sources[{channel,date,ref,quote}] + who）。
-- origin_key = 外部强信号去重键（如 'slack:<ts>' / 'gmail:<message_id>'），
-- (channel, origin_key) 全局 partial-unique：同一条外部消息永远只铸/只喂一张卡；
-- merge 并卡时把副卡 sources 的 card_id 重指主卡（UPDATE，不是复制插入）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
  id          INTEGER PRIMARY KEY,
  card_id     TEXT NOT NULL REFERENCES cards(id),
  channel     TEXT NOT NULL,                   -- meeting/slack/gmail/quick_capture/…
  who         TEXT,
  date        TEXT,
  ref         TEXT,
  quote       TEXT,
  origin_key  TEXT,                            -- 无强信号 = NULL，绝不 fuzzy（对齐 §10 thread_key 纪律）
  created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS sources_dedup
  ON sources(channel, origin_key) WHERE origin_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS sources_card
  ON sources(card_id, created_at, id);

-- ---------------------------------------------------------------------------
-- notes — comment / steer / fold 回执，append-only（trigger 执法）。
-- delivered_at/acked_at 是仅有的可写回执列（set-once）：comment 何时注入
-- session、session 何时确认——对齐 §32.2「改方向要有真实回执」的诚实语义
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notes (
  id            INTEGER PRIMARY KEY,
  card_id       TEXT NOT NULL REFERENCES cards(id),
  kind          TEXT NOT NULL CHECK (kind IN ('comment', 'steer', 'fold')),
  body          TEXT NOT NULL,
  actor_type    TEXT NOT NULL CHECK (actor_type IN ('user', 'agent', 'system')),
  created_at    TEXT NOT NULL,
  delivered_at  TEXT,                          -- 注入运行中 session 的时刻
  acked_at      TEXT                           -- session/流程确认消费的时刻
);

CREATE INDEX IF NOT EXISTS notes_card
  ON notes(card_id, created_at, id);
-- 待投递扫描（delivered_at IS NULL 的积压）
CREATE INDEX IF NOT EXISTS notes_undelivered
  ON notes(card_id) WHERE delivered_at IS NULL;

-- ---------------------------------------------------------------------------
-- dispatches — 派发/会话台账，runtime 字段与 cards 解耦（一卡多轮：rework/
-- re-raise/abort 重批各起一行；YAML 时代塞在 execution.* 里的 session 账目
-- 在这里成为一等公民）。one_active partial unique = 一卡至多一个活 session
-- （§46 stop-confirmed 语义的数据库层）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatches (
  id             INTEGER PRIMARY KEY,
  card_id        TEXT NOT NULL REFERENCES cards(id),
  runtime        TEXT NOT NULL DEFAULT 'claude',  -- 执行运行时；今日仅 'claude'，留 TEXT 不锁死
  session_id     TEXT,                            -- 派发成功后回填（可能拿不到，见 §4）
  worktree_path  TEXT,
  branch         TEXT,
  status         TEXT NOT NULL CHECK (status IN (
    'running', 'completed', 'failed', 'stopped'   -- stopped = 用户 stop_to_review/abort
  )),
  exit_code      INTEGER,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  -- running 行必然未收尾；收尾行必然带 finished_at
  CHECK ((status = 'running') = (finished_at IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS dispatches_one_active
  ON dispatches(card_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS dispatches_card
  ON dispatches(card_id, started_at, id);

-- ---------------------------------------------------------------------------
-- activities — append-only 审计流（dashi task_activities 同型）：
-- changes = JSON 数组 [{field, before, after}]，actor 三态与权限墙同一词表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
  id          INTEGER PRIMARY KEY,
  card_id     TEXT NOT NULL REFERENCES cards(id),
  actor_type  TEXT NOT NULL CHECK (actor_type IN ('user', 'agent', 'system')),
  actor_id    TEXT,                            -- 细分身份（inbox 动作名/radar pass/session 短 id）
  changes     TEXT NOT NULL CHECK (json_valid(changes)),
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS activities_card
  ON activities(card_id, created_at, id);

-- ---------------------------------------------------------------------------
-- board_revision — 单行全局游标（dashi comment_attachment_revision 同型）。
-- 每笔写事务 +1；SSE/轮询客户端拿它做「有没有新东西」的一个整数答案
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS board_revision (
  id     INTEGER PRIMARY KEY CHECK (id = 1),
  value  INTEGER NOT NULL CHECK (value >= 0)
);

INSERT OR IGNORE INTO board_revision (id, value) VALUES (1, 0);

-- ---------------------------------------------------------------------------
-- transition_whitelist — 状态机合法转移表（数据即法条）。
-- 逐条派生自 live CONTRACT + actd inbox handlers 的 allowed-status 集合：
--   approve(detected|card_sent→approved) · raise(detected|card_sent→raising) ·
--   process_raising(raising→card_sent) · comment-on-raising(→card_sent) ·
--   defer(card_sent→detected) · done_external(card_sent|approved|executing|
--   review→delivered) · abort_execution(approved|executing|review→card_sent) ·
--   stop_to_review(approved|executing|review→review) · revert_review
--   (delivered→review) · dispatch(approved→executing) · reconcile-done
--   (executing→review) · rework/§30 session_active(review→executing) ·
--   §21 merge(任意活状态→merged) · §10 re-raise(delivered→card_sent, system) ·
--   §10 archive(delivered|detected→archived; auto 仅 delivered) · unarchive
--   (archived→prev_status) · any→trashed(契约 header) · restore(trashed→
--   prev_status 精确复位) · §24 digest 刷新(delivered→review, system)
-- 不在表里 = 非法（fail-closed）。actor_type='agent' 一行都没有：D3 权限墙,
-- 旁路 agent 进程对 status 零写权（宪法第 1 条单写者的 SQL 化）。
-- 追加合法转移 = 新增 INSERT 行（add-only），绝不改语义地删行。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transition_whitelist (
  old_status  TEXT NOT NULL,
  new_status  TEXT NOT NULL,
  actor_type  TEXT NOT NULL CHECK (actor_type IN ('user', 'agent', 'system')),
  PRIMARY KEY (old_status, new_status, actor_type)
) WITHOUT ROWID;

INSERT OR IGNORE INTO transition_whitelist (old_status, new_status, actor_type) VALUES
  -- detected（备选/Backlog）
  ('detected',  'raising',   'user'),    -- §8 raise：研究并提议
  ('detected',  'raising',   'system'),  -- capture relates_to → raise（§13 快速捕获）
  ('detected',  'card_sent', 'system'),  -- 雷达 act-now 命中提升 / deferred 卡自动升回（§10 defer）
  ('detected',  'approved',  'user'),    -- actd approve 允许 detected 直批
  ('detected',  'archived',  'user'),    -- §10 archive Q2：备选封存（仅用户）
  ('detected',  'trashed',   'user'),
  ('detected',  'trashed',   'system'),
  ('detected',  'merged',    'user'),    -- §21 merge_apply/merge_force 副卡（用户拍板）
  -- card_sent（提案）
  ('card_sent', 'approved',  'user'),    -- ★ 批准 = 用户专属（权限墙核心）
  ('card_sent', 'raising',   'user'),    -- raise 重扩写（actd 允许 card_sent）
  ('card_sent', 'raising',   'system'),
  ('card_sent', 'detected',  'user'),    -- §10 defer 存备选
  ('card_sent', 'delivered', 'user'),    -- §10 done_external（系统外完成，唯一完成出口）
  ('card_sent', 'trashed',   'user'),    -- reject → 回收站（§9）
  ('card_sent', 'trashed',   'system'),
  ('card_sent', 'merged',    'user'),
  -- raising（AI 扩写中）
  ('raising',   'card_sent', 'system'),  -- 扩写完成/失败兜底都落提案（§8）
  ('raising',   'card_sent', 'user'),    -- comment 折回 card_sent 重审批（actd §32.2 分支）
  ('raising',   'trashed',   'user'),
  ('raising',   'trashed',   'system'),
  ('raising',   'merged',    'user'),
  -- approved（排队中）
  ('approved',  'executing', 'system'),  -- §4 dispatch
  ('approved',  'card_sent', 'user'),    -- §10 abort_execution
  ('approved',  'review',    'user'),    -- §10 stop_to_review（排队卡直接收成果）
  ('approved',  'delivered', 'user'),    -- done_external
  ('approved',  'trashed',   'user'),
  ('approved',  'trashed',   'system'),
  ('approved',  'merged',    'user'),
  -- executing（运行中）
  ('executing', 'review',    'system'),  -- 自然完成收割（reconcile done 分支）
  ('executing', 'review',    'user'),    -- stop_to_review
  ('executing', 'card_sent', 'user'),    -- abort_execution
  ('executing', 'delivered', 'user'),    -- done_external
  ('executing', 'trashed',   'user'),
  ('executing', 'trashed',   'system'),
  ('executing', 'merged',    'user'),
  -- review（待验收）
  ('review',    'delivered', 'user'),    -- ★ 验收 = 用户专属（权限墙核心）
  ('review',    'executing', 'user'),    -- rework 打回
  ('review',    'executing', 'system'),  -- §30 session_active 同调翻回 / §21 merge rework 注入
  ('review',    'card_sent', 'user'),    -- abort_execution（v0.28.1 §30 放宽）
  ('review',    'trashed',   'user'),
  ('review',    'trashed',   'system'),
  ('review',    'merged',    'user'),
  -- delivered（已验收）
  ('delivered', 'review',    'user'),    -- §10 revert_review
  ('delivered', 'review',    'system'),  -- §24 weekly digest 卡刷新拉回待验收
  ('delivered', 'card_sent', 'system'),  -- §10 re-raise 回锅（radar/triage 命中已交付线程）
  ('delivered', 'archived',  'user'),
  ('delivered', 'archived',  'system'),  -- §10 auto-archive(archive_stale) 只封存冷 delivered
  ('delivered', 'trashed',   'user'),
  ('delivered', 'trashed',   'system'),
  ('delivered', 'merged',    'user'),    -- 已交付副卡并入（§21 搬 merged_deliverables）
  -- rejected（legacy 旁支，现行 reject 已改走回收站）
  ('rejected',  'trashed',   'user'),
  ('rejected',  'trashed',   'system'),
  -- trashed（回收站）→ restore 精确复位 prev_status（§9，任何来路都能回去）
  ('trashed',   'detected',  'user'),
  ('trashed',   'card_sent', 'user'),
  ('trashed',   'raising',   'user'),
  ('trashed',   'approved',  'user'),
  ('trashed',   'executing', 'user'),
  ('trashed',   'review',    'user'),
  ('trashed',   'delivered', 'user'),
  ('trashed',   'rejected',  'user'),
  ('trashed',   'merged',    'user'),
  ('trashed',   'archived',  'user'),
  -- merged（终态；契约 header「any state → trashed」仍适用）
  ('merged',    'trashed',   'user'),
  ('merged',    'trashed',   'system'),
  -- archived（封存）→ unarchive 回 prev_status（只可能是 delivered/detected）
  ('archived',  'delivered', 'user'),
  ('archived',  'detected',  'user'),
  ('archived',  'trashed',   'user');

-- v0.48.8 接线补行（add-only，§53.2）：白名单首版从 CONTRACT/handlers 派生时
-- 漏收的**真实管线转移**，接线 parity 测试逐条撞出（schema.md T-14 预案）：
--   card_sent→approved(system)  = §51 hand 卡免批通道（policy.may_auto_dispatch
--     是资格闸门；actor=system 因为发起者是 actd 自主管线，不是 agent——
--     approve 的 user 独占语义收窄为「user 或过 §51 天花板的 system」，agent 仍零行）
--   raising→detected(system)    = §8 扩写失败兜底退回欠账（actd.process_raising）
--   delivered→detected(system)  = §45 LIMITED 天花板下的 re-raise 只落备选
--     （registry.reraise_or_followup cap_detected）
--   merged→card_sent/detected(system) = §3.3 canonical 链 dead-end 在 merged
--     终态上的 re-raise（registry.canonical 跳链落空时的既有路径）
INSERT OR IGNORE INTO transition_whitelist (old_status, new_status, actor_type) VALUES
  ('card_sent', 'approved',  'system'),
  ('raising',   'detected',  'system'),
  ('delivered', 'detected',  'system'),
  ('merged',    'card_sent', 'system'),
  ('merged',    'detected',  'system');

-- ---------------------------------------------------------------------------
-- triggers — 状态机 + 权限墙 + append-only 执法（dashi RAISE 惯用法）
-- ---------------------------------------------------------------------------

-- 出生权限墙：agent 不得直接 INSERT 批准后各态的卡（migration/正常铸卡走
-- system/user；transition trigger 只管 UPDATE，这里补上 INSERT 面）。
-- prev_status 同查：带毒回程票（如 trashed + prev_status='approved'）经用户
-- 一次无辜 restore 就精确复位进 approved——组合权限旁路，出生时一并拒收
CREATE TRIGGER IF NOT EXISTS cards_agent_insert_wall
BEFORE INSERT ON cards
WHEN NEW.last_actor_type = 'agent' AND (
  NEW.status IN ('approved', 'delivered', 'executing', 'review')
  OR NEW.prev_status IN ('approved', 'delivered', 'executing', 'review'))
BEGIN
  SELECT RAISE(ABORT, 'AGENT_TRANSITION_FORBIDDEN');
END;

-- 字段权限墙（UPDATE 面）：prev_status 是 restore 的目的地、merged_into_id 是
-- lineage 父指针——agent 改写任一 = 给用户后续动作预埋弹药，与 status 墙同族
CREATE TRIGGER IF NOT EXISTS cards_agent_field_wall
BEFORE UPDATE ON cards
WHEN NEW.last_actor_type = 'agent'
  AND (NEW.prev_status IS NOT OLD.prev_status
       OR NEW.merged_into_id IS NOT OLD.merged_into_id)
BEGIN
  SELECT RAISE(ABORT, 'AGENT_FIELD_FORBIDDEN');
END;

-- 状态机执法：①agent 的 approve/accept 类转移点名拒绝（清晰报错优先）；
-- ②其余一切转移查 whitelist，查不到 = 非法。同一 trigger 内两连发保证顺序
CREATE TRIGGER IF NOT EXISTS cards_status_transition
BEFORE UPDATE OF status ON cards
WHEN OLD.status <> NEW.status
BEGIN
  SELECT RAISE(ABORT, 'AGENT_TRANSITION_FORBIDDEN')
  WHERE NEW.last_actor_type = 'agent'
    AND NEW.status IN ('approved', 'delivered');
  SELECT RAISE(ABORT, 'ILLEGAL_TRANSITION')
  WHERE NOT EXISTS (
    SELECT 1 FROM transition_whitelist
    WHERE old_status = OLD.status
      AND new_status = NEW.status
      AND actor_type = NEW.last_actor_type
  );
END;

-- origin_trust 信任档：非用户只许**降档**（v0.48.8 接线修订）。§50 M1.a 的
-- live 语义 = 管线在每次 fold/铸卡后按 sources 重算章（最小信任者定卡）——
-- sources 只增不减，重算只可能降档或持平；升档（如自封 hand = 免审批快车道）
-- 才是 M1.d 要堵的自提权，只许用户拨。首版 trigger 一刀切禁了非用户的一切
-- 改动，把合法的 fold 降档也拦死（接线 parity 测试撞出）。同值重写放行——
-- 幂等 retry 无害。信任序 = policy._TRUST_RANK（hand 3 > proposed 2 >
-- meeting 1 > external 0）。
CREATE TRIGGER IF NOT EXISTS cards_origin_trust_user_only
BEFORE UPDATE ON cards
WHEN NEW.origin_trust <> OLD.origin_trust AND NEW.last_actor_type <> 'user'
  AND (CASE NEW.origin_trust WHEN 'hand' THEN 3 WHEN 'proposed' THEN 2
       WHEN 'meeting' THEN 1 ELSE 0 END)
    > (CASE OLD.origin_trust WHEN 'hand' THEN 3 WHEN 'proposed' THEN 2
       WHEN 'meeting' THEN 1 ELSE 0 END)
BEGIN
  SELECT RAISE(ABORT, 'ORIGIN_TRUST_USER_ONLY');
END;

-- 身份锚点不可改写（§37：title 都不许动身份，id 更不行）
CREATE TRIGGER IF NOT EXISTS cards_id_immutable
BEFORE UPDATE OF id ON cards
WHEN OLD.id <> NEW.id
BEGIN
  SELECT RAISE(ABORT, 'CARD_ID_IMMUTABLE');
END;

-- 工作编号 set-once（§60.1/§37 身份锚点族）：NULL → 值 只许一次；已有值不得改写/清空。
-- put_card 的 UPDATE 从 payload 推导 work_id——payload 若丢了编号，这里响亮拒绝而不是静默抹掉
CREATE TRIGGER IF NOT EXISTS cards_work_id_set_once
BEFORE UPDATE OF work_id ON cards
WHEN OLD.work_id IS NOT NULL AND NEW.work_id IS NOT OLD.work_id
BEGIN
  SELECT RAISE(ABORT, 'WORK_ID_SET_ONCE');
END;

-- tombstone 行冻结：删过的卡只剩 id+board_rev 供同步，任何字段不得复活
CREATE TRIGGER IF NOT EXISTS cards_tombstone_frozen
BEFORE UPDATE ON cards
WHEN OLD.tombstone = 1
BEGIN
  SELECT RAISE(ABORT, 'TOMBSTONE_FROZEN');
END;

-- 禁硬删：删除 = tombstone 化（否则增量同步的客户端永远学不到删除）
CREATE TRIGGER IF NOT EXISTS cards_no_hard_delete
BEFORE DELETE ON cards
BEGIN
  SELECT RAISE(ABORT, 'USE_TOMBSTONE');
END;

-- notes append-only：核心列不可变；delivered_at/acked_at 各 set-once
CREATE TRIGGER IF NOT EXISTS notes_append_only
BEFORE UPDATE ON notes
BEGIN
  SELECT RAISE(ABORT, 'NOTES_APPEND_ONLY')
  WHERE NEW.id <> OLD.id
     OR NEW.card_id <> OLD.card_id
     OR NEW.kind <> OLD.kind
     OR NEW.body <> OLD.body
     OR NEW.actor_type <> OLD.actor_type
     OR NEW.created_at <> OLD.created_at;
  SELECT RAISE(ABORT, 'NOTES_RECEIPT_SET_ONCE')
  WHERE (OLD.delivered_at IS NOT NULL AND NEW.delivered_at IS NOT OLD.delivered_at)
     OR (OLD.acked_at IS NOT NULL AND NEW.acked_at IS NOT OLD.acked_at);
END;

CREATE TRIGGER IF NOT EXISTS notes_no_delete
BEFORE DELETE ON notes
BEGIN
  SELECT RAISE(ABORT, 'NOTES_APPEND_ONLY');
END;

-- activities append-only：审计流一个字都不许动
CREATE TRIGGER IF NOT EXISTS activities_immutable
BEFORE UPDATE ON activities
BEGIN
  SELECT RAISE(ABORT, 'ACTIVITIES_APPEND_ONLY');
END;

CREATE TRIGGER IF NOT EXISTS activities_no_delete
BEFORE DELETE ON activities
BEGIN
  SELECT RAISE(ABORT, 'ACTIVITIES_APPEND_ONLY');
END;

-- transition_whitelist 只许追加（法条表 add-only）：UPDATE/DELETE = 篡改
-- 状态机法条，与 notes/activities 同规执法；追加合法转移的 INSERT 面不设限
CREATE TRIGGER IF NOT EXISTS transition_whitelist_no_update
BEFORE UPDATE ON transition_whitelist
BEGIN
  SELECT RAISE(ABORT, 'WHITELIST_APPEND_ONLY');
END;

CREATE TRIGGER IF NOT EXISTS transition_whitelist_no_delete
BEFORE DELETE ON transition_whitelist
BEGIN
  SELECT RAISE(ABORT, 'WHITELIST_APPEND_ONLY');
END;

-- 全局游标单调递增（回拨 = 客户端增量同步静默漏数据，直接拒绝）
CREATE TRIGGER IF NOT EXISTS board_revision_monotonic
BEFORE UPDATE ON board_revision
WHEN NEW.value <= OLD.value
BEGIN
  SELECT RAISE(ABORT, 'REVISION_MONOTONIC');
END;

-- ---------------------------------------------------------------------------
-- 版本钉扎 — 必须是本文件**最后一条语句**（测试钉死）：全部 DDL 落地后
-- 版本号才生效，建库途中崩溃 = 版本仍 0 = 下次重跑幂等补全。
-- 数值必须等于 store.py SCHEMA_VERSION（判例钉死）
-- ---------------------------------------------------------------------------
PRAGMA user_version = 2;
