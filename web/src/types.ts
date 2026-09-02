// dashboard.json 投影的 TS 镜像（wire 契约 = live 树 docs/CONTRACT.md §2 + scripts/demo_seed.py）。
// 铁律：add-only——只加 optional 字段，绝不改名/删字段/收紧类型；未知字段一律保留（索引签名兜底）。
// 服务端透传 actd 的投影，前端不发明字段。

/** 来源引文（needs_approval/review/debt 共用形状） */
export interface CardSource {
  who: string;
  channel: string;
  date: string;
  quote: string;
  ref?: string;
  [key: string]: unknown;
}

/** 提案卡（needs_approval 分区项；含 raising 占位项 processing=true） */
export interface ApprovalCard {
  id: string;
  title: string;
  tier: "T0" | "T1" | "T2" | string;
  /** W17 生效档位（§50）：外部出身恒 "T2"；缺席 = 旧投影，消费端回落 tier */
  effective_tier?: string;
  /** 出身章四值词表（§50）；缺章整键省略 */
  origin_trust?: string;
  tier_hint?: string;
  hardness?: string;
  deadline?: string | null;
  days_left?: number | null;
  repeated?: number;
  cost_usd?: number | null;
  show_cost: boolean;
  green_sign?: boolean;
  disagreement?: string | null;
  improvement_of?: string | null;
  processing: boolean;
  sources: CardSource[];
  plan: string[];
  dod: string[];
  outputs?: string[];
  delivery_mode?: "chat" | "repo" | string;
  reraised?: boolean;
  reraised_note?: string;
  [key: string]: unknown;
}

/**
 * 排队原因（running 分区 queued 项，add-only optional）。wire 真源 =
 * docs/CONTRACT.md §51（可能缺席，也可能是纯字符串——UI 经 steer.ts 双兼容
 * 解析）。kind 开放枚举：waiting_card（等前置卡，带 blocking_id="R-xx"）/
 * concurrency（等并发位）；waiting_budget retired v0.48.7（D9）。
 */
export interface QueuedReason {
  kind: string;
  detail?: string | null;
  blocking_id?: string | null;
  [key: string]: unknown;
}

/**
 * steer 回执行（executing 卡上的 owner 方向修正，经 §44.3 briefing 机制中继；
 * add-only optional，wire 真源 = vnext-amendments.md §M6.1）。
 * status 诚实三态：queued（已排队未注入）/ delivered（已送达会话）/
 * dropped（3 次注入失败放弃，§39 trace 留痕）。
 */
export interface SteerNote {
  ts: string;
  text?: string;
  status?: "queued" | "delivered" | "dropped" | string;
  delivered_at?: string | null;
  [key: string]: unknown;
}

/** 运行中/需输入/已完成 分区项（running 混入 state="queued" 的排队项，无 session_id） */
export interface TaskRow {
  id: string;
  name: string;
  state: "queued" | "working" | "blocked" | "done" | string;
  session_id?: string;
  short_id?: string;
  copy_cmd?: string;
  cwd?: string;
  started_at?: number;
  dispatched_at?: number;
  accepted_at?: number;
  summary?: string;
  plan?: string[];
  dod?: string[];
  log?: string;
  delivery_mode?: string;
  last_error?: string;
  dispatch_error?: string | null;
  waiting_for?: string;
  resume_exhausted?: boolean;
  /** §4 派发风暴刹车：approved 卡连续派发失败 N 次后停止重试，投影为 blocked 行（wire key 逐字镜像） */
  dispatch_halted?: boolean;
  dispatch_attempts?: number;
  delivered_summary?: string;
  queued_reason?: QueuedReason | string | null;
  steers?: SteerNote[];
  [key: string]: unknown;
}

/** 待验收卡（review 分区项） */
export interface ReviewCard {
  id: string;
  name: string;
  delivered_summary?: string;
  final_draft?: string | null;
  plan?: string[];
  dod: string[];
  sources?: CardSource[];
  log?: string;
  dispatched_at?: number;
  review_at?: number;
  delivery_mode: "chat" | "repo" | string;
  [key: string]: unknown;
}

/** 欠账/备选卡（debt 分区项，v0.17 起展示层叫「潜在任务/Backlog」） */
export interface DebtCard {
  id: string;
  title: string;
  hardness?: string;
  type?: string;
  sources?: CardSource[];
  [key: string]: unknown;
}

/** 回收站行（§9 + §40.5：purge_at 为 null/缺失 = 不会自动清，不显示倒计时） */
export interface TrashRow {
  id: string;
  title: string;
  permanent: boolean;
  trashed_at: string;
  summary?: string;
  kind?: "suggestion" | "debt" | string;
  trash_reason?: "rejected" | "deleted" | string;
  type?: string;
  hardness?: string;
  purge_at?: string | null;
  [key: string]: unknown;
}

/**
 * §56 合并即上岗：scripts/auto-deploy.sh 最近一次运行的结果（dashboard add-only
 * 顶层键 deploy_state；字段逐字镜像 wire key，全部 string）。status 已知值：
 * deployed | up_to_date | rolled_back | rollback_failed | refused_dirty |
 * refused_branch | fetch_failed | ci_pending | ci_failed | failed —— 未知值按
 * "需要人看"处理。
 */
export interface DeployState {
  status?: string;
  version?: string;
  head?: string;
  prev?: string;
  last_deployed?: string;
  last_run?: string;
  detail?: string;
  failed_sha?: string;
  [key: string]: unknown;
}

/** 看板投影顶层（GET /api/board = dashboard.json 原样透传） */
export interface Board {
  generated_at: string;
  counts: Record<string, number>;
  needs_approval: ApprovalCard[];
  running: TaskRow[];
  needs_input: TaskRow[];
  review: ReviewCard[];
  completed: TaskRow[];
  debt: DebtCard[];
  trash: TrashRow[];
  archived?: unknown[];
  merge_suggestions?: unknown[];
  update_available?: unknown;
  device_label?: string;
  deploy_state?: DeployState;
  [key: string]: unknown;
}

/** GET /api/cards/{id} = 投影行 + registry YAML 只读增补（add-only 合并，字段名同投影） */
export type CardDetail = Record<string, unknown> & { id: string };

/** GET /api/health（CONTRACT §47.4）：管线活性——server/health.py 的 wire 形逐字镜像 */
export interface HealthSnapshot {
  verdict: "ok" | "unknown" | "stale" | "stalled" | "failing" | string;
  heartbeat: {
    age_s: number;
    phase: string | null;
    pid: number | null;
    interval: number | null;
    stale_after_s: number;
    stale: boolean;
  } | null;
  dashboard: { generated_at: string; age_s: number; stale: boolean } | null;
  loop_health: { consecutive_failures: number; last_error: string | null };
  checked_at: string;
  [key: string]: unknown;
}

/** GET/PUT /api/settings/models（CONTRACT §59，D22）：server/settings.py models_snapshot 的 wire 形逐字镜像。
 *  dispatch/pipeline = "follow" 或显式模型 id；canonical = server-owned 下拉全集；warnings = 非 canonical 值的整句警告 */
export interface ModelsSettings {
  dispatch: string;
  pipeline: string;
  follow: string;
  canonical: string[];
  source: { dispatch?: string; pipeline?: string; [key: string]: unknown };
  warnings: string[];
  [key: string]: unknown;
}

/** GET /api/claude-code/default-model（§59）：follow 模式继承的 Claude Code 全局默认（~/.claude/settings.json `model`） */
export interface ClaudeCodeDefault {
  model: string | null;
  path: string;
  exists: boolean;
  parseable: boolean;
  canonical: boolean;
  [key: string]: unknown;
}

/** POST /api/claude-code/default-model 的回执（只改 model 键；backup = 改前副本路径，文件原本不存在时为 null） */
export interface ClaudeCodeDefaultWrite {
  model: string;
  previous: string | null;
  backup: string | null;
  path: string;
  [key: string]: unknown;
}
