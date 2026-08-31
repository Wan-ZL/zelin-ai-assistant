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
  delivered_summary?: string;
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
  [key: string]: unknown;
}

/** GET /api/cards/{id} = 投影行 + registry YAML 只读增补（add-only 合并，字段名同投影） */
export type CardDetail = Record<string, unknown> & { id: string };
