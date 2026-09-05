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
  /** §10 add-only（issue #7）：出生 capture 的 inbox stem（`capture-<uuid>` = POST /api/actions 回的 `file` 去掉 .json）；只有出生行带 */
  capture_id?: string;
  [key: string]: unknown;
}

/** 提案卡（needs_approval 分区项；含 raising 占位项 processing=true） */
export interface ApprovalCard {
  id: string;
  title: string;
  /** §60（D21）工作编号 R-xxx：进入 approved 时 server 分配；提案/备选/回收站卡缺席 */
  work_id?: string | null;
  /** §60 展示编号（= work_id ?? id），server 算好；旧 server 缺席时客户端按 cardId.ts 回落 */
  display_id?: string;
  /** §60 编号分类 work | legacy | proposal（server 给，客户端不按前缀猜） */
  id_kind?: string;
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
  /** §7 落点三元组：target_kind "new"（新建 repo）/ "existing"（改现有；basename 以 your-workbench 结尾 = 只出文档） */
  target_repo?: string | null;
  target_name?: string | null;
  target_kind?: "new" | "existing" | string | null;
  /** §7 add-only（issue #11）：批准即出机的后果；kind 开放枚举，今日唯一值 github_repo_create（缺席 = 旧 server） */
  egress?: EgressRow[];
  /** §10 add-only（issue #7）：卡级 capture_id（= 出生 sources[].capture_id）——占位行与提案行都带；非 capture 出身的卡缺席 */
  capture_id?: string;
  /** §44 静默并入次数（0 = 从未）——原生「已并入×N」紫章 */
  silent_merged?: number;
  /** §40 "estimated" | "unknown"（unknown 时 cost_usd 不当估价读） */
  cost_state?: string;
  /** §37 展示名 / 曾用名——提案是摘要优先面：卡面标题走 cardHeadline（钦定名 > summary > display_title > title），
   *  不是 running 族的 rowTitle（display_title 优先） */
  display_title?: string;
  /** §37 用户钦定标记（server 只在为真时发键）：为真时 display_title 压过 summary 成为卡面标题 */
  user_titled?: boolean;
  former_titles?: string[];
  [key: string]: unknown;
}

/**
 * 出机后果行（needs_approval 项 `egress[]`，CONTRACT §7 issue #11）。kind 开放枚举：
 * github_repo_create（批准后 `gh repo create <target> --private` + 推送派生内容）；
 * 未知 kind 按 kind 原文降级显示，永不吞掉——披露宁多勿少。
 */
export interface EgressRow {
  kind: string;
  target?: string | null;
  visibility?: string | null;
  [key: string]: unknown;
}

/**
 * 排队原因（running 分区 queued 项，add-only optional）。wire 真源 =
 * docs/CONTRACT.md §51（可能缺席，也可能是纯字符串——UI 经 steer.ts 双兼容
 * 解析）。kind 开放枚举：waiting_card（等前置卡，带 blocking_id=前置卡主键）/
 * concurrency（等并发位）；waiting_budget retired v0.48.7（D9）。
 */
export interface QueuedReason {
  kind: string;
  detail?: string | null;
  /** 前置卡主键（lineage 口径） */
  blocking_id?: string | null;
  /** §60 add-only：前置卡的展示编号（work_id ?? id）——chip 文案用它，缺席回落 blocking_id */
  blocking_display_id?: string | null;
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

/** §64（issue #128）AI 一句话摘要 + 完成度评语——server 只在有摘要或评语时才发整键；
 *  只是建议：客户端只渲染，验收/打回仍是人按的按钮。verdict 是开放枚举，三个已知值见 VERDICTS */
export interface CardAssessment {
  summary?: string | null;
  verdict?: string | null;
  verdict_reason?: string | null;
  /** 评语生成时刻（epoch 秒） */
  at?: number | null;
}

/** 运行中/需输入/已完成 分区项（running 混入 state="queued" 的排队项，无 session_id） */
export interface TaskRow {
  id: string;
  name: string;
  /** §60（D21）工作编号 R-xxx：进入 approved 时 server 分配；提案/备选/回收站卡缺席 */
  work_id?: string | null;
  /** §60 展示编号（= work_id ?? id），server 算好；旧 server 缺席时客户端按 cardId.ts 回落 */
  display_id?: string;
  /** §60 编号分类 work | legacy | proposal（server 给，客户端不按前缀猜） */
  id_kind?: string;
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
  /** §30 待验收卡因会话再活跃投影回运行中——原生「已交付过·再运行」青章 */
  from_review?: boolean;
  /** §25 错误分类 id（null = 未分类）——原生据此挑人话句；web 目前只用原文 */
  last_error_id?: string | null;
  dispatch_error_id?: string | null;
  agent_name?: string | null;
  question?: string | null;
  display_title?: string;
  former_titles?: string[];
  /** §64 AI 摘要 + 评语（阶段性完成卡只用 summary 一句） */
  assessment?: CardAssessment | null;
  [key: string]: unknown;
}

/** §65.3 self_improve 卡的 gh 物理核验结果（review 行 `delivery`，wire key 逐字镜像 execution.delivery） */
export interface Delivery {
  verified: boolean;
  reason?: string | null;
  branch?: string;
  pr_number?: number | null;
  pr_url?: string | null;
  pr_draft?: boolean | null;
  pr_state?: string | null;
  changed_files?: number;
  sensitive_paths?: string[];
  [key: string]: unknown;
}

/** §65 顶层 `self_improve`：自动草稿 PR 通道的开关 + 暂停状态（敏感路径护栏） */
export interface SelfImproveState {
  enabled: boolean;
  paused: boolean;
  paused_reason?: string | null;
  paused_pr?: number | null;
  paused_pr_url?: string | null;
  paused_paths?: string[];
  paused_at?: string | null;
  [key: string]: unknown;
}

/** 待验收卡（review 分区项） */
export interface ReviewCard {
  id: string;
  name: string;
  /** §65.3 self_improve 卡才有：草稿 PR 核验结果 */
  delivery?: Delivery;
  /** §60（D21）工作编号 R-xxx：进入 approved 时 server 分配；提案/备选/回收站卡缺席 */
  work_id?: string | null;
  /** §60 展示编号（= work_id ?? id），server 算好；旧 server 缺席时客户端按 cardId.ts 回落 */
  display_id?: string;
  /** §60 编号分类 work | legacy | proposal（server 给，客户端不按前缀猜） */
  id_kind?: string;
  delivered_summary?: string;
  final_draft?: string | null;
  plan?: string[];
  dod: string[];
  sources?: CardSource[];
  log?: string;
  dispatched_at?: number;
  review_at?: number;
  delivery_mode: "chat" | "repo" | string;
  /** 原生 ReviewRow meta 行：cwd basename 章 / 会话有新活动 / 单击复制指令 */
  cwd?: string;
  copy_cmd?: string | null;
  session_active?: boolean;
  summary?: string | null;
  agent_name?: string | null;
  display_title?: string;
  /** §64 AI 摘要 + 完成度评语（建议验收 / 需继续做 / 需要拍板，带一行理由） */
  assessment?: CardAssessment | null;
  [key: string]: unknown;
}

/** 欠账/备选卡（debt 分区项，v0.17 起展示层叫「潜在任务/Backlog」） */
export interface DebtCard {
  id: string;
  title: string;
  /** §60（D21）工作编号 R-xxx：进入 approved 时 server 分配；提案/备选/回收站卡缺席 */
  work_id?: string | null;
  /** §60 展示编号（= work_id ?? id），server 算好；旧 server 缺席时客户端按 cardId.ts 回落 */
  display_id?: string;
  /** §60 编号分类 work | legacy | proposal（server 给，客户端不按前缀猜） */
  id_kind?: string;
  hardness?: string;
  type?: string;
  sources?: CardSource[];
  summary?: string;
  /** §37 摘要优先面（原生 DebtRow displaySummary）：卡面标题走 cardHeadline */
  display_title?: string;
  user_titled?: boolean;
  [key: string]: unknown;
}

/** 永久性完成行（archived[] 分区，§5 v0.20.0：镜像回收站行 + 封存簿记；dashboard.py _archived_view） */
export interface ArchivedRow {
  id: string;
  title: string;
  summary?: string;
  kind?: "suggestion" | "debt" | string;
  archived_at?: string | null;
  archive_reason?: "user" | "auto" | string | null;
  prev_status?: string | null;
  type?: string;
  hardness?: string;
  /** §37 摘要优先面（原生 ArchiveRow displaySummary）：行标题走 cardHeadline */
  display_title?: string;
  user_titled?: boolean;
  [key: string]: unknown;
}

/** 回收站行（§9 + §40.5：purge_at 为 null/缺失 = 不会自动清，不显示倒计时） */
export interface TrashRow {
  id: string;
  title: string;
  /** §60（D21）工作编号 R-xxx：进入 approved 时 server 分配；提案/备选/回收站卡缺席 */
  work_id?: string | null;
  /** §60 展示编号（= work_id ?? id），server 算好；旧 server 缺席时客户端按 cardId.ts 回落 */
  display_id?: string;
  /** §60 编号分类 work | legacy | proposal（server 给，客户端不按前缀猜） */
  id_kind?: string;
  permanent: boolean;
  trashed_at: string;
  summary?: string;
  kind?: "suggestion" | "debt" | string;
  trash_reason?: "rejected" | "deleted" | string;
  type?: string;
  hardness?: string;
  purge_at?: string | null;
  /** §37 摘要优先面（原生 TrashRow displaySummary；trash/archived 行只解码这两键）：行标题走 cardHeadline */
  display_title?: string;
  user_titled?: boolean;
  [key: string]: unknown;
}

/**
 * §56 合并即上岗：scripts/auto-deploy.sh 最近一次运行的结果（dashboard add-only
 * 顶层键 deploy_state；字段逐字镜像 wire key，全部 string）。status 已知值：
 * deployed | up_to_date | rolled_back | rollback_failed | refused_dirty |
 * refused_branch | fetch_failed | ci_pending | ci_failed | failed |
 * install_incomplete | blocked_tcc —— 未知值按"需要人看"处理。v0.48.20 add-only：
 * running_version（actd 心跳里的版本）/ install_report_version / reason /
 * last_incident（上一次回滚判决「<ts> <status>: <detail>」，healthy 状态下仍在 =
 * 回滚被拒后没人看过，直到下一次 deployed 才清）。2026-09-03 add-only：
 * behind_main / behind_main_why（上一次部署停在 origin/main head 之前的最新绿 commit，
 * head 的 CI 还没绿 / 红了 / 已中毒；部署到 head 或 up_to_date 时清掉）。
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
  running_version?: string;
  install_report_version?: string;
  reason?: string;
  last_incident?: string;
  behind_main?: string;
  behind_main_why?: string;
  [key: string]: unknown;
}

/**
 * §70 每日自我改进循环的投影（dashboard add-only 顶层键 maintenance；act/lib/daily_loop.projection）。
 * phase 已知值：idle | dedup | stale_sweep | proposals（未知值按「在跑」显示）；时间全是 epoch 秒或 null。
 * last_result 是最近一次运行的计数：合并 N 张、清理 M 张（回收站可撤销）、提案 K 张、非 owner issue 摘要、阶段错误数；
 * advisories（D33）= 自检类信号——不铸卡，只在横幅里列出来（kind / text / ref / fingerprint / first_seen 逐字镜像 wire）。
 */
export interface MaintenanceAdvisory {
  kind: string;
  text: string;
  ref?: string;
  fingerprint?: string;
  first_seen?: string;
  [key: string]: unknown;
}

export interface Maintenance {
  phase: string;
  started_at: number | null;
  last_run_at: number | null;
  next_run_at?: number | null;
  last_result: {
    merged: number;
    trashed: number;
    proposals: number;
    summaries?: number;
    errors?: number;
    advisories?: MaintenanceAdvisory[];
    [key: string]: unknown;
  };
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
  archived?: ArchivedRow[];
  /** §21 合并建议作业（analyzing/done/failed）；旧 server 缺席 */
  merge_suggestions?: MergeSuggestion[];
  update_available?: unknown;
  device_label?: string;
  deploy_state?: DeployState;
  /** §70 每日整理投影（add-only；旧 server 缺席）——顶部横幅读它 */
  maintenance?: Maintenance;
  /** §63 会议 recap 投影（add-only；旧 server 缺席）——不是卡，页面 ?page=recaps 读它 */
  recaps?: RecapRow[];
  /** §65 自动草稿 PR 通道状态（add-only 顶层键；老 daemon 无此键） */
  self_improve?: SelfImproveState;
  /** §48 源健康投影：gmail / slack / obsidian 的 enabled / last_ok / skip_reason / stale */
  radar_sources?: Record<string, RadarSourceHealth>;
  /** §44.6 静默并入回执（add-only 顶层键；TTL 600 s 内、cap 10、按 at 降序）——提案列顶一行 info 通知 */
  fold_receipts?: FoldReceipt[];
  [key: string]: unknown;
}

/** §44.6 并入回执行（dashboard._fold_receipts 的 wire 形逐字镜像）：只有目标卡 id + 展示名，永不带被并入原文 */
export interface FoldReceipt {
  id: string;
  req: string;
  title: string;
  channel: string;
  at: number;
  [key: string]: unknown;
}

/**
 * §63 会议 recap 行（dashboard.json 顶层 recaps[] 的元素 = act/lib/recap_store 投影，
 * wire key 逐字镜像）。status open = 进行中（无正文）；en/zh = 5 行纯文本（null =
 * 未生成 / 无音频 / 转写不全 / 生成失败，看 quality）；copied_at / sent_at = server
 * 本地标记（marks.json，无控制流读它）；slack_draft = §63.4 草稿投递回执。
 */
export interface RecapRow {
  key: string;
  app: string;
  start: string;
  end: string;
  duration_min: number;
  status: "open" | "closed" | string;
  version: number;
  generated_at?: string | null;
  partial?: boolean;
  en?: string[] | null;
  zh?: string[] | null;
  /** ok | needs_review | thin_transcript | no_audio | generation_failed | null */
  quality?: string | null;
  transcript_words?: number;
  frames?: number;
  audio_rows?: number;
  note?: string | null;
  history_count?: number;
  copied_at?: string | null;
  sent_at?: string | null;
  slack_draft?: {
    status: string;
    channel_link?: string | null;
    at?: string | null;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}

/** GET/PUT /api/settings/recap（§63）：server/recaps.py snapshot 的 wire 形逐字镜像 */
export interface RecapSettings {
  enabled: boolean;
  default_language: "auto" | "zh" | "en" | string;
  slack_draft_enabled: boolean;
  languages: string[];
  source: { [key: string]: unknown };
  [key: string]: unknown;
}

/** GET/PUT /api/settings/display（§54.1 第 12 项）：server/display.py snapshot 的 wire 形逐字镜像；
 *  三个词表由 server 给（segmented control 从这里渲染，client 不存第二份） */
export interface DisplaySettings {
  text_size: "s" | "m" | "l" | "xl" | string;
  text_weight: "regular" | "medium" | "bold" | string;
  stroke: "thin" | "normal" | "thick" | string;
  text_sizes: string[];
  text_weights: string[];
  strokes: string[];
  source: { [key: string]: unknown };
  [key: string]: unknown;
}

/** PUT /api/settings/display 的 body：三键任意子集（server 零容忍多余字段） */
export interface DisplaySettingsPatch {
  text_size?: string;
  text_weight?: string;
  stroke?: string;
}

/** POST /api/recaps/mark 回执 */
export interface RecapMarkReceipt {
  ok: boolean;
  key: string;
  copied_at: string | null;
  sent_at: string | null;
  [key: string]: unknown;
}

/** GET /api/lanes（CONTRACT §54）：列说明文案的 server-owned 目录——web 列头「?」气泡逐字镜像，按 UI 语言取 zh / en */
export interface LaneCatalogEntry {
  slug: string;
  help: { zh: string; en: string; [key: string]: unknown };
  [key: string]: unknown;
}

export interface LaneCatalog {
  lanes: LaneCatalogEntry[];
  [key: string]: unknown;
}

/** POST /api/ai-fix 回执（§54 让 AI 修）：server 已在 Terminal 打开修复会话；command_file = 生成的 .command 路径 */
export interface AiFixReceipt {
  ok: boolean;
  command_file?: string;
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

/** GET/PUT /api/settings/daily-loop（CONTRACT §70，D10）：server/settings.py daily_loop_snapshot 的 wire 形逐字镜像。
 *  time = 本地 HH:MM；三个天数/张数都是非负整数（0 = 关掉那一项）；source = 每个字段的生效来源 override|config|default */
export interface DailyLoopSettings {
  enabled: boolean;
  time: string;
  max_proposals_per_day: number;
  stale_days: number;
  trash_retention_days: number;
  source: { [key: string]: unknown };
  [key: string]: unknown;
}

/** PUT /api/settings/daily-loop 的 body：五键任意子集 */
export type DailyLoopPatch = Partial<Pick<DailyLoopSettings,
  "enabled" | "time" | "max_proposals_per_day" | "stale_days" | "trash_retention_days">>;

/** POST /api/claude-code/default-model 的回执（只改 model 键；backup = 改前副本路径，文件原本不存在时为 null） */
export interface ClaudeCodeDefaultWrite {
  model: string;
  previous: string | null;
  backup: string | null;
  path: string;
  [key: string]: unknown;
}

/** 素材库一条记录（CONTRACT §62.2；act/lib/materials.py 台账行的 wire 形逐字镜像）。
 *  status 词表 new | picked_up | proposal_created | pr_opened | done | dismissed（开放枚举：未知值原样展示）；
 *  links = 循环回填的 proposal_id / pr_url */
export interface MaterialItem {
  id: string;
  ts: string;
  created_at: string;
  url: string;
  note: string;
  status: string;
  links: { proposal_id?: string; pr_url?: string; [key: string]: unknown };
  [key: string]: unknown;
}

/** GET /api/materials/list?status=…（§62.4）：items 按创建时间新→旧；status = 生效的过滤名；counts 反映全量台账 */
export interface MaterialsList {
  items: MaterialItem[];
  status: string;
  counts: { open: number; total: number; [key: string]: unknown };
  [key: string]: unknown;
}

/** GET /api/skills 的一行（CONTRACT §67）：act/lib/skills.Store.inspect 的 wire 形逐字镜像（add-only）。
 *  state = disabled | enabled | copy | custom | foreign；toggle = enable | disable | locked；
 *  relation/distance = 本机副本相对仓库版本的 same | behind | ahead | unknown 与「N 版」距离 */
export interface SkillRow {
  name: string;
  version: string;
  upstream: string | null;
  upstream_version: string | null;
  default_enabled: boolean;
  description: string;
  path: string;
  target: string;
  link: string;
  state: string;
  stale_target: boolean;
  installed_version: string | null;
  relation: string;
  distance: number;
  decision: string | null;
  project_visible: boolean;
  toggle: string;
  [key: string]: unknown;
}

/** GET /api/skills 快照 = POST /api/skills 回执（§67） */
export interface SkillsSnapshot {
  skills: SkillRow[];
  skills_dir: string;
  repo_skills_dir: string;
  state_path: string;
  [key: string]: unknown;
}

// ----- §21 合并建议（merge_suggestions 分区；dashboard.py _merge_suggestions 的 wire 形逐字镜像） ----- #
/** MS-xxxx 作业投影：analyzing / done / failed（dismissed 不发）；verdict/primary/rationale/action_plan 仅 done 时齐备 */
export interface MergeSuggestion {
  id: string;
  ids: string[];
  status: "analyzing" | "done" | "failed" | string;
  verdict?: "merge" | "link_improvement" | "keep_separate" | "close_secondary" | "partition" | string | null;
  primary?: string | null;
  rationale?: string | null;
  action_plan?: string[];
  confidence?: "high" | "medium" | "low" | string | null;
  error?: string | null;
  requested_at?: number | null;
  /** §21ter partition 分组方案（仅 partition verdict 携带） */
  groups?: Array<{ primary: string; ids: string[]; reason?: string | null; [key: string]: unknown }>;
  [key: string]: unknown;
}

/** §48.7「立即测试一轮」回执（radar_sources.<src>.test_round；actd 台账 × health 的纯投影）：
 *  running = 子进程起了、雷达还没落笔；done = 请求之后 health 有新一轮；noop = 没起（note: disabled /
 *  launch_failed）；lost = 超时仍无落笔 */
export interface RadarTestRound {
  requested_at: string;
  state: "running" | "done" | "noop" | "lost" | string;
  note: string | null;
  [key: string]: unknown;
}

/** §48 radar_sources 投影（dashboard 顶层键）：每源 enabled / last_ok / skip_reason / stale
 *  + §48.7 add-only last_attempt（原生「最近一轮 <相对时间>」）/ test_round
 *  + §48.4 add-only intent / secret_present（意愿信号；setup 类 / Slack token 类诊断卡的资格） */
export interface RadarSourceHealth {
  enabled: boolean;
  last_ok?: string | null;
  skip_reason?: string | null;
  stale?: boolean;
  last_attempt?: string | null;
  test_round?: RadarTestRound | null;
  /** 碰过开关（overrides 里有 <src>_enabled / features.<src>_radar）/ 凭证文件在 / 凭证非空；缺 = 旧 payload = 老判据 */
  intent?: boolean;
  /** §19 凭证非空（slack user token / gmail 应用密码；obsidian 恒 false） */
  secret_present?: boolean;
  [key: string]: unknown;
}

/** GET /api/radars（§48.7）：每源的 launchd agent 状态——loaded 问 launchd 本人（非 darwin 为 null），
 *  interval_s 读模板 StartInterval（原生「已安装，每 N 分钟自动运行」的 N） */
export interface RadarAgentStatus {
  label: string;
  interval_s: number | null;
  loaded: boolean | null;
  plist_installed: boolean;
  [key: string]: unknown;
}

export interface RadarAgentsSnapshot {
  radars: Record<string, RadarAgentStatus>;
  [key: string]: unknown;
}

/** POST /api/radars/reinstall 回执（§48.7）：install.sh --reinstall-agent 跑完后再问一次 launchd */
export interface RadarReinstallReceipt {
  ok: boolean;
  source: string;
  label: string;
  loaded: boolean;
  [key: string]: unknown;
}

/** POST /api/folders/{open,create} 回执（§68.1 目录字段） */
export interface FolderReceipt {
  ok: boolean;
  key: string;
  path: string;
  created?: boolean;
  git_init?: "done" | "skipped" | "failed" | null | string;
  /** open 的 add-only（§68.4 追记）：目录不在时实际打开的最近既有祖先目录；在的时候不带 */
  opened?: string;
  /** open 的 add-only：true = `path` 不是目录、打开的是 `opened`（原生 reveal 的 deletingLastPathComponent 回落） */
  missing?: boolean;
  [key: string]: unknown;
}

// ----- §68 设置目录（server/settings_catalog.py 的 wire 形；文案 zh/en 两键 server-owned） ----- #
export interface BilingualText {
  zh: string;
  en: string;
  [key: string]: unknown;
}

export interface SettingsField {
  key: string;
  kind: "bool" | "enum" | "string" | "number" | "int" | "list" | string;
  label: BilingualText;
  help: BilingualText;
  default: unknown;
  choices: string[] | null;
  effective: unknown;
  source: "override" | "config" | "default" | string;
  /** add-only（§68.1）：输入框示例文案（原生 TextField prompt，如「例：you@gmail.com」，zh/en 两键）；老 server 缺席 */
  placeholder?: BilingualText;
  /** add-only（§68.1 目录字段）：`"dir"` = 目录路径字段（渲染 选择… 与 打开 / 创建）；老 server 缺席 */
  path?: "dir" | string;
  /** add-only：effective 值展开 ~ 后是不是目录；空值 null（无从判断）；老 server 缺席 */
  path_exists?: boolean | null;
  /** add-only（§68.1 追记）：值的形状校验——web 保存前镜像同一条规则、显示 server-owned 的同一句（kind 词表今日 `email` / `session_id`）；
   *  `reasons`（add-only，§68.7 追记）= 多句的 kind 按 reason 分句（session_id：`leading_hyphen`），没对上的 reason 用 `message`；老 server 缺席 */
  check?: { kind: "email" | "session_id" | string; message: BilingualText; reasons?: Record<string, BilingualText> };
  [key: string]: unknown;
}

export interface SettingsSection {
  id: string;
  title: BilingualText;
  help: BilingualText;
  fields: SettingsField[];
  /** add-only（§68.7 追记，只有 `maintainer` 区带）：resolved 终端的展示名（原生 TerminalLauncher.preferred.displayName）——「会在 <终端> 中打开」；老 server 缺席 */
  terminal_app_name?: string;
  [key: string]: unknown;
}

export interface SettingsCatalog {
  sections: SettingsSection[];
  [key: string]: unknown;
}

/** GET /api/secrets（§19 / §68）：只有状态，永无值 */
export interface SecretStatus {
  name: string;
  label: BilingualText;
  present: boolean;
  verifiable: boolean;
  mtime: number | null;
  /** add-only（§68.3 2026-09-03 追记）：secrets 文件缺席但 §19 第二 / 三层旧路径的文件非空 = 原生「使用旧路径」态；老 server 缺席 */
  legacy?: boolean;
  /** PUT 回执 add-only（§68.3 2026-09-05 追记）：豆包语音凭证识别为旧版 App ID + Access Token 对；GET 行不带 */
  legacy_pair?: boolean;
  [key: string]: unknown;
}

export interface SecretsStatus {
  secrets: SecretStatus[];
  [key: string]: unknown;
}

/** GET /api/sync（§68.15）：state/sync.json 的开关 + syncd 落下的配对二维码（PNG base64；开着才带回） */
export interface SyncStatus {
  enabled: boolean;
  channel_id: string;
  label: string;           // state/sync.json 里的设备名（从未命名 = ""）
  default_label: string;   // 这台 Mac 的主机名（预填）
  qr_png_base64: string | null;
  [key: string]: unknown;
}

/** POST /api/sync/pair 回执：ok:true 带 channel / label / registered / 二维码；ok:false 带 error（no_python | pair_failed）+ message */
export interface SyncPairReceipt {
  ok: boolean;
  channel_id?: string;
  label?: string;
  registered?: boolean;
  qr_png_base64?: string | null;
  error?: string;
  message?: string;
  [key: string]: unknown;
}

/** POST /api/sync/disable 回执 = ok + 快照（失败带 error / message） */
export interface SyncDisableReceipt extends SyncStatus {
  ok: boolean;
  error?: string;
  message?: string;
}

/** GET /api/voice（§68.1 追记）：语气档案两级候选的在场性 + 开关 */
export interface VoiceProfileStatus {
  enabled: boolean;
  private_path: string;
  private_exists: boolean;
  default_path: string;
  default_exists: boolean;
  effective_path: string | null;
  [key: string]: unknown;
}

/** GET /api/slack/directory（§68.1 追记）：act/lib/slack_setup.directory 的 JSON 行原样 */
export interface SlackDirEntry {
  id: string;
  name: string;
  real_name?: string;
  [key: string]: unknown;
}

export interface SlackDirectory {
  ok: boolean;
  fetched_at?: string;
  channels: SlackDirEntry[];
  users: SlackDirEntry[];
  error?: string;      // ok:false 时：no_token / no_python / directory_failed / …（act 侧词表）
  message?: string;    // ok:false 时的人话（act 侧按界面语言生成；no_python / directory_failed 是尾巴原文）
  [key: string]: unknown;
}

/** POST /api/secrets/{name}/verify 回执（§68.3；三分判决：ok / 凭证错 network:false / 判决未知 network:true） */
export interface SecretVerifyResult {
  ok: boolean;
  network: boolean;
  detail: string;
  extra: Record<string, unknown>;   // Slack ok：user_id / user / team（auth.test 原字段）；Gmail 没地址：precondition = "gmail_address"（探针没跑）
  /** §68.3 2026-09-05 追记（add-only）：只在 ok:false ∧ network:false 且探针真跑过（无 extra.precondition）时带——原生 humanAuthReason 的分类人话，raw detail 在括号里 */
  reason?: BilingualText;
  [key: string]: unknown;
}

// ----- §25 doctor 行（act/doctor.render_json 的 wire 形；status 小写 ok|warn|fail = act/lib/checks/core 的常量，
// server/doctor_run 归一后透出——比较一律按小写字面量，不做大小写翻译层） ----- #
export interface DoctorRow {
  name: string;
  status: "ok" | "warn" | "fail" | string;
  detail: string;
  fix: string;
  failure_id?: string;
  action_id?: string;
  [key: string]: unknown;
}

export interface DoctorReport {
  ok: boolean;
  checks: DoctorRow[];
  home: string;
  rc: number;
  fast: boolean;
  ran_at: string;
  error?: string;
  [key: string]: unknown;
}

// ----- §68.3 权限体检 ----- #
export interface FdaExecutable {
  role: string;
  path: string | null;
  realpath: string | null;
  exists: boolean;
  note: BilingualText;
  [key: string]: unknown;
}

export interface PermissionsSnapshot {
  home: string;
  on_external_volume: boolean;
  fda: { needed: boolean; pane: string; executables: FdaExecutable[]; [key: string]: unknown };
  panes: { full_disk?: string; screen?: string; microphone?: string; notifications?: string; files_folders?: string; [key: string]: unknown };
  doctor: DoctorRow[];
  doctor_ran_at: string | null;
  doctor_ok: boolean;
  /** 笔记库（Documents）被动探针：state/vault_sync_mode=mirror → granted；root = 生效 obsidian_raw 的父目录（add-only） */
  vault?: { status: "granted" | "unknown" | string; root: string; [key: string]: unknown };
  [key: string]: unknown;
}

// ----- §68.4 诊断 ----- #
export interface LogEntry {
  name: string;
  path: string;
  size: number;
  mtime: number;
  [key: string]: unknown;
}

export interface InstallReport {
  version?: string | null;
  generated_at?: string | null;
  ok?: boolean | null;
  steps: Array<{ name?: string; status?: string; detail?: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

/** state/cron_probe.json 公开子集（§25；原生 CronProbe.read——「定时任务磁盘权限」行四态由页面判；add-only） */
export interface CronProbe {
  ts: string | null;
  read_ok: boolean | null;
  protected_path: string | null;
  [key: string]: unknown;
}

/** 录制页「最近活动」三个时间戳（原生 IngestModel.refreshLabels；epoch 秒，缺席 null；add-only） */
export interface IngestActivity {
  screenpipe_db: { path: string; mtime: number | null; [key: string]: unknown };
  actd_log: { path: string; mtime: number | null; [key: string]: unknown };
  /** readable:false = 目录住 TCC 保护位置且不在 mirror 模式——server 永不读 ~/Documents（§68.3） */
  unprocessed: { path: string; mtime: number | null; readable: boolean; [key: string]: unknown };
  [key: string]: unknown;
}

export interface DiagnosticsSnapshot {
  doctor: DoctorReport;
  health: HealthSnapshot;
  deploy_state: DeployState | null;
  radar_sources: Record<string, RadarSourceHealth> | null;
  install_report: InstallReport | null;
  registry_backend: string;
  logs: LogEntry[];
  cron_probe?: CronProbe | null;
  activity?: IngestActivity | null;
  /** config.yaml doctor.ai_fix_enabled（原生 AIFix.enabled；false = 「让 AI 修」整颗不出现；缺席 = 开；add-only） */
  ai_fix_enabled?: boolean;
  [key: string]: unknown;
}

/** POST /api/ingest/{export,run} 回执：脚本在 server 后台线程跑，页面拿 job id 轮询（同脚本在跑 → reused） */
export interface IngestJobStart {
  ok: boolean;
  job: string;
  state: "running" | string;
  script: string;
  reused?: boolean;
  [key: string]: unknown;
}

/** GET /api/ingest/jobs/{id}：running 只有前四键；done 多出脚本回执（同一条 ingest/ 脚本、同一套退出码；skipped = ingest 的 exit 3 持锁） */
export interface IngestJob {
  id: string;
  script: string;
  state: "running" | "done" | string;
  started_at: string;
  ok?: boolean;
  rc?: number;
  skipped?: boolean;
  tail?: string;
  seconds?: number;
  [key: string]: unknown;
}

/** GET /api/failures：§25 FailureCatalog 的 server-owned 投影（原生 FailureCatalog.message） */
export interface FailureCatalog {
  failures: Record<string, { zh: string; en: string; action_id?: string | null; [key: string]: unknown }>;
  [key: string]: unknown;
}

export interface LogTail {
  name: string;
  path: string;
  size: number;
  lines: string[];
  truncated: boolean;
  [key: string]: unknown;
}

// ----- §68.5 首次运行向导 ----- #
export interface SetupSnapshot {
  needed: boolean;
  done: boolean;
  config_exists: boolean;
  config_example_exists: boolean;
  secrets: Record<string, boolean>;
  home: string;
  protected_location: boolean;
  [key: string]: unknown;
}

export interface SetupReceipt {
  ok: boolean;
  setup: SetupSnapshot;
  path?: string;
  [key: string]: unknown;
}

/** GET /api/setup/engine（§68.5；原生 EngineDetector）：CLI 路径 / 版本 / 认证梯子（顺序 = server AUTH_LADDER） */
export type EngineAuth = "oauth" | "env_key" | "secrets_file" | "legacy_file";
export interface SetupEngine {
  cli_path: string | null;
  version: string | null;
  auth: EngineAuth | string | null;
  auth_sources: Record<string, boolean>;
  ready: boolean;
  [key: string]: unknown;
}

/** POST /api/setup/seed-dashboard：ok:false 带 error 尾巴（不 500） */
export interface SeedDashboardReceipt {
  ok: boolean;
  rc: number;
  error?: string;
  [key: string]: unknown;
}

// ----- §68.6 关于 / 更新（§26） ----- #
export interface AboutInfo {
  version: string;
  home: string;
  repo: string;
  update_available: { current?: string; latest?: string; url?: string; pkg_asset_url?: string | null; [key: string]: unknown } | null;
  update_check: { checked_at?: string | null; latest?: string | null; url?: string | null; pkg_asset_url?: string | null; [key: string]: unknown } | null;
  /** §68.6 追记（add-only）：updates.check_enabled 的 effective 值（override → config → true）；旧 server 缺席 = 当 true */
  check_enabled?: boolean;
  [key: string]: unknown;
}

/** POST /api/update/check = §26 CLI 那一行 JSON（ok:false 时 error） */
export interface UpdateCheckResult {
  ok: boolean;
  enabled?: boolean;
  current?: string;
  latest?: string | null;
  update_available?: boolean;
  url?: string | null;
  checked_at?: string | null;
  error?: string;
  [key: string]: unknown;
}

// ----- §68.9 MCP servers（Skills 商店的 wire 形在 §67 SkillRow / SkillsSnapshot） ----- #
export interface McpServer {
  name: string;
  scope: string;
  transport: string;
  summary: string;
  env_count: number;
  [key: string]: unknown;
}

export interface McpScope {
  scope: "user" | "project" | string;
  path: string;
  path_display?: string;   // add-only（§68.9 追记）：$HOME 缩成 ~ 的展示路径；老 server 缺席时退回 path
  exists: boolean;
  parseable: boolean;
  servers: McpServer[];
  [key: string]: unknown;
}

export interface McpList {
  scopes: McpScope[];
  [key: string]: unknown;
}

// ----- §22 导入 Claude Code 工作（radar_claude_sessions --scan 的 JSON 行） ----- #
export interface ClaudeSessionCandidate {
  session_id: string;
  project?: string;
  project_dir?: string;
  title?: string;
  gist?: string;
  last_activity?: string;
  ended_waiting_on_user?: boolean;
  answered?: boolean;
  session_mismatch?: boolean;
  [key: string]: unknown;
}

export interface ClaudeSessionsScan {
  ok: boolean;
  reason?: string;
  root?: string;
  window: number;
  candidates: ClaudeSessionCandidate[];
  error?: string;
  [key: string]: unknown;
}

/** POST /api/terminal 回执（§68.7）：server 已写 .command 并 open */
export interface TerminalReceipt {
  ok: boolean;
  command: string;
  command_file: string;
  cwd: string;
  /** add-only（§68.7 追记；今日只有 POST /api/maintainer/terminal 带）：打开用的终端展示名；老 server 缺席 */
  terminal_app_name?: string;
  [key: string]: unknown;
}

/** POST /api/repair/actd 回执（§68.8） */
export interface RepairReceipt {
  ok: boolean;
  label: string;
  action: string;
  [key: string]: unknown;
}
