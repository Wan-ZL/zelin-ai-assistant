// server wire 形的 TS 镜像（v2.0.0：数据源 = 本机看板 server 的各条 GET 路由，server/*.py 的快照函数逐字；
// dashboard.json 投影与它的卡片 / 看板类型随 Act 管线退役，全文见 tag full）。
// 铁律：add-only——只加 optional 字段，绝不改名 / 收紧类型；未知字段一律保留（索引签名兜底）。
// 退役的整族字段（art.6 v2.0 条款）从这里一并删掉：client 不替 server 记已经不存在的东西。

/**
 * §63 会议 recap 行（`GET /api/recaps` 的 `recaps[]` 元素 = act/lib/recap_store 投影，
 * wire key 逐字镜像）。status open = 进行中（无正文）；en/zh = 5 行纯文本（null =
 * 未生成 / 无音频 / 转写不全 / 生成失败，看 quality）；copied_at / sent_at /
 * dismissed_at = server 本地标记（marks.json）；generate_request = §63.8 「重新生成」
 * 回执（行上「生成中」的真源）。
 * §63.5 追记（issue #301）：sent_at（= 已归档，派生，取消标记即回到活跃）与 dismissed_at
 * 决定行落在哪一栏，并在 recap 侧决定已忽略的保留期；marks 不触发任何发送。
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
  /** §63.10 这一版用的出稿形状：lines = 五行 | sections = 可发送长版（老 daemon 无此键 = lines） */
  shape?: string | null;
  /** §63.10 可发送长版的分节正文（五行形 = null；wire 逐字镜像 act/lib/recap_text） */
  sections_en?: RecapSection[] | null;
  sections_zh?: RecapSection[] | null;
  /** §63.10 **粘出去的那一份**（daemon 渲染好的：空的部分已略掉、分节稿已编号）——
   *  老 daemon 无此键 = 页面退回把 en/zh 直接换行拼起来 */
  copy_en?: string | null;
  copy_zh?: string | null;
  transcript_words?: number;
  frames?: number;
  audio_rows?: number;
  note?: string | null;
  history_count?: number;
  /** §63.9 存着的每一版的句柄（add-only，只有标量；老 daemon 无此键 = 不给「上一版」入口）——
   *  正文不在这里，点开面板才经 `GET /api/recaps/history?key=` 单独读 */
  history_versions?: RecapVersionHandle[] | null;
  /** §63.9 这一版的正文是从第几版回退回来的（add-only；没回退过 = 键不在） */
  reverted_from?: number | null;
  copied_at?: string | null;
  sent_at?: string | null;
  /** §63.5 追记 已忽略的时刻（add-only；没忽略过 = null，老 daemon 无此键）——与 sent_at 一起决定分栏 */
  dismissed_at?: string | null;
  /** §63.4 Slack 草稿回执——v2.0.0 退役（§78 隔离下的 claude 没有 Slack MCP），recap_store 恒发 null；页面不读 */
  slack_draft?: null;
  /** §63.8 「重新生成 / 现在生成」回执（add-only；没请求过 / 过了 TTL = null；老 daemon 无此键） */
  generate_request?: RecapGenerateRequest | null;
  /** §63.3 追记 校验未通过的结构化原因（add-only；clean = []；老 daemon 无此键）——永不含正文 */
  problems?: RecapProblem[] | null;
  /** §63.3 追记 落地前的确定性长度修剪台账（add-only；没修过 = []） */
  repairs?: RecapRepair[] | null;
  /** §63.11 这一份纪要该被问的意图问题（add-only，**投影现算不落盘**；老 daemon 无此键 = 不问） */
  questions?: RecapQuestion[] | null;
  /** §63.11 我们见过的第一版的可粘正文（add-only，只写一次；没有第二版过 = null） */
  baseline?: RecapBaseline | null;
  /** §63.11 这一版是按哪组答案出的（add-only，每版重写；没答案 = null） */
  intent?: RecapIntent | null;
  /** §63.12 这个 key 的逐条标签计数器 `{字母: 发到第几号}`（add-only，单调、永不复用）——
   *  页面不读它（标签本身在正文与 `sections_*[].tags` 里），镜像它只为不撒谎 */
  tag_seq?: Record<string, number> | null;
  [key: string]: unknown;
}

/** `GET /api/recaps`（§63.5，server/recaps.list_recaps）：两份预算切过的行 + 三栏真实总数 */
export interface RecapsSnapshot {
  recaps: RecapRow[];
  recap_counts?: RecapLaneTotals;
  [key: string]: unknown;
}

/** `POST /api/recaps/generate` 回执（§63.8）：launch = running（已派生 act.recap）| noop（派生失败，行显示 launch_failed） */
export interface RecapGenerateReceipt {
  ok: boolean;
  key: string;
  launch: "running" | "noop" | string;
  requested_at: string;
  generate_request: RecapGenerateRequest | null;
  [key: string]: unknown;
}

/** `POST /api/recaps/revert` 回执（§63.9）：版本存不存在由加锁的写者（act/recap.revert）判 */
export interface RecapRevertReceipt {
  ok: boolean;
  key: string;
  version: number;
  launch: "running" | "noop" | string;
  [key: string]: unknown;
}

/**
 * §63.11（issue #302）一条意图问题（`act/lib/recap_intent.derive` 的 wire 形逐字镜像）：
 * `kind` ∈ split | deadline | others | detail | audience | own | prior（add-only 闭表），
 * `options` = 那一类的选项（同样闭表），`subject` 只有逐条的那几个有正文（上一版里的原话）。
 * **问题的组成是 server 数据，不是 client 代码**（防腐 #10）；问法由 `text(zh, en)` 按
 * `kind` / 选项值查表。
 */
export interface RecapQuestion {
  id: string;
  kind: string;
  options: string[];
  subject?: string | null;
  [key: string]: unknown;
}

/**
 * §63.11 「转写原版」= 我们见过的**第一版**的可粘正文（`act/recap._capture_baseline`）：
 * history 的帽是 5 版，第一版早晚被挤掉，而这一条 issue 的正题就是两版并存可切。
 * 存的是渲染好的 `copy_*`（粘出去的那一份），不是 `en` / `zh` 的原始数组。
 */
export interface RecapBaseline {
  version: number;
  generated_at?: string | null;
  shape?: string | null;
  copy_en?: string | null;
  copy_zh?: string | null;
  [key: string]: unknown;
}

/** §63.11 这一版的意图回执：`answers` = 送出去的那组 `id=value`（wire 逐字镜像） */
export interface RecapIntent {
  answers: string[];
  at?: string | null;
  version?: number | null;
  [key: string]: unknown;
}

/**
 * §63.10 可发送长版的一节（issue #303；`act/lib/recap_text` 的 `{key, modality, items}` 逐字镜像）：
 * `key` ∈ decided | split | proposed | deadline | changed | open，`modality` ∈ decided | proposed |
 * floated | open（一节一个语气——决定与提议因此在纸面上长得不一样）。条目的编号**不在数据里**：
 * 它由 daemon 渲染 `copy_*` 时加上——§63.12 之后是那一条自己的**跨版稳定标签**
 * （`tags[i]`，`D1` / `S2`），没有标签的条目才回落到 §63.10 的跨节连续编号。
 */
export interface RecapSection {
  key: string;
  modality: string;
  items: string[];
  /** §63.12 逐条标签（add-only，与 `items` 逐位对齐；老记录无此键 = 渲染回落到连续编号）——
   *  daemon 派发，模型永不是作者（`act/lib/recap_text.assign_tags`） */
  tags?: string[] | null;
  [key: string]: unknown;
}

/**
 * §63.3 追记 校验发现（`recap_text.validate_detail` 的 wire 形逐字镜像；issue #298）：
 * `code` ∈ line_count | label_mismatch | line_too_long | reported_speech | timestamp |
 * link | quotes | markup | emoji | mention（add-only；未知 code 页面退回 `text`）；
 * 整语言级的禁项（时间戳 / 链接 / 引号 / markdown / emoji / @）没有行号，`line` = null；
 * `limit` / `over` 只有 line_too_long 与 line_count 填。`text` = 喂回模型的那句英文原文。
 */
export interface RecapProblem {
  code: string;
  lang?: string | null;
  line?: number | null;
  limit?: number | null;
  over?: number | null;
  text?: string | null;
  [key: string]: unknown;
}

/**
 * §63.3 追记 一行被自动修剪的回执（永远展示——悄悄剪字就是对粘出去的正文说谎）：
 * `over` = 原来超出上限多少，`removed` = 行尾**真正被删掉**多少字符（英文按词边界
 * 回退，所以 removed ≥ over）——面板说的是 removed，说 over 会低报这一刀。
 */
export interface RecapRepair {
  lang: string;
  line: number;
  over: number;
  removed?: number | null;
  [key: string]: unknown;
}

/**
 * §63.8 生成请求回执（act/lib/recap_requests.projection 的 wire 形逐字镜像；与 §48.7 test_round 同形）：
 * running = server 派生了 act.recap、新版本还没落地；done = 文件的 generated_at ≥ requested_at；
 * lost = 超过 10 分钟仍无新版本；noop = 子进程没起来（note = launch_failed）。
 */
export interface RecapGenerateRequest {
  requested_at: string;
  state: "running" | "done" | "noop" | "lost" | string;
  note: string | null;
  [key: string]: unknown;
}

/**
 * §63.9（issue #300）投影行里一版的**句柄**（`act/lib/recap_store.history_versions` 的 wire 形
 * 逐字镜像）：只有标量，正文永不进看板投影（60 行 × 5 版 × 两语言会把 10 s 一轮的轮询撑爆）。
 * 列出来的每一项都有正文 = 都能回退（无正文的 history 条目 daemon 侧就滤掉了）。
 */
export interface RecapVersionHandle {
  version: number;
  generated_at?: string | null;
  partial?: boolean;
  [key: string]: unknown;
}

/**
 * §63.9 一版的完整形（`GET /api/recaps/history` 的 `current` 与 `entries[]` 同形；
 * server/recaps.py `_version_shape` 逐字镜像）：`en` / `zh` 恒是数组（缺席 = []），
 * `quality` 在 §63.9 之前入库的 history 条目上是 null（那一版的校验结论没存下来）。
 */
export interface RecapVersion {
  version: number;
  generated_at: string | null;
  partial: boolean;
  quality: string | null;
  en: string[];
  zh: string[];
  /** §63.10 add-only：那一版的形状与**粘出去的那份正文**（可发送长版的 en/zh 是空的，
   *  两版对照因此读 copy_*；老条目没有这两个键 = lines + null） */
  shape: string;
  copy_en: string | null;
  copy_zh: string | null;
  [key: string]: unknown;
}

/**
 * GET /api/recaps/history?key=…（§63.9）：单份纪要存着的每一版 + 正文。`entries` newest first
 * （第一项 = 上一版），`history_cap` = daemon 侧只留几版（更早的老化掉——面板照它说话），
 * `truncated` = 文件超过读门没被读（层缺席不是错误，宪法第 11 条）。
 */
export interface RecapHistory {
  key: string;
  current: RecapVersion | null;
  entries: RecapVersion[];
  history_cap: number;
  truncated: boolean;
  [key: string]: unknown;
}

/** GET/PUT /api/settings/recap（§63）：server/recaps.py snapshot 的 wire 形逐字镜像 */
export interface RecapSettings {
  enabled: boolean;
  default_language: "auto" | "zh" | "en" | string;
  /** §63.10 出厂形状（只读：config.yaml 层，PUT 只认两把旋钮）——形状选择器的初值 */
  default_shape: "lines" | "sections" | string;
  languages: string[];
  source: { [key: string]: unknown };
  [key: string]: unknown;
}

/** GET/PUT /api/settings/display（§68.1 v2.0.0 修订（原 §54.1 第 12 项，该节退役））：server/display.py snapshot 的 wire 形逐字镜像；
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

/** POST /api/recaps/mark 的 mark 词表（server MARKS 逐字镜像，add-only；dismissed = §63.5 追记 issue #301） */
export type RecapMarkKind = "copied" | "sent" | "dismissed";

/**
 * §63.5 追记（issue #301）`GET /api/recaps` 的 `recap_counts`：三栏各自的**真实**总数，
 * 在 `recaps[]` 被两份预算切之前算（act/lib/recap_store.lane_counts）。键 = 栏 slug
 * （`recap_store.RECAP_LANES` 逐字镜像）。页面拿它减掉实际收到的行数，说出「另有 N 条
 * 更早的没列在这一栏」——上限是硬上限，但不许悄悄少东西（宪法第 3 条）。
 */
export interface RecapLaneTotals {
  active?: number;
  archived?: number;
  dismissed?: number;
  [key: string]: unknown;
}

/** POST /api/recaps/mark 回执 */
export interface RecapMarkReceipt {
  ok: boolean;
  key: string;
  copied_at: string | null;
  sent_at: string | null;
  /** §63.5 追记（issue #301）：老 server 无此键 */
  dismissed_at?: string | null;
  [key: string]: unknown;
}

/** GET /api/health（§49，v2.0.0）：活性 = server 在答 + cron ingest 链的探针新鲜度 + 上次 recap 轮次——
 *  server/health.py 的 wire 形逐字镜像（v1 的 actd 心跳 / 看板新鲜度 / 连崩计数随 actd 退役） */
export interface HealthSnapshot {
  verdict: "ok" | "unknown" | "stale" | string;
  server: { ok: boolean; [key: string]: unknown };
  /** state/cron_probe.json 的年龄；null = 链从没跑过 */
  cron: { age_s: number; read_ok: boolean | null; stale: boolean; [key: string]: unknown } | null;
  /** state/recap/sessions.json 的 updated_at；null = act.recap 从没跑完一轮 */
  recap: { updated_at: string; age_s: number; [key: string]: unknown } | null;
  checked_at: string;
  [key: string]: unknown;
}

/** GET/PUT /api/settings/models（§59）：server/settings.py models_snapshot 的 wire 形逐字镜像。
 *  pipeline = 模型 id；fallback = "off" 或模型 id（headless 调用的 --fallback-model）；off / *_default = server 给的哨兵与出厂值；
 *  canonical = server-owned 下拉全集；warnings = 非 canonical 值的整句警告。v2.0.0：dispatch 旋钮与 "follow"（跟随 Claude Code
 *  全局默认）随 Act 管线与 §78 隔离退役 */
export interface ModelsSettings {
  pipeline: string;
  fallback: string;
  off: string;
  pipeline_default?: string;
  fallback_default: string;
  canonical: string[];
  source: { pipeline?: string; fallback?: string; [key: string]: unknown };
  warnings: string[];
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
  /** add-only（§68.1 追记）：值的形状校验——web 保存前镜像同一条规则、显示 server-owned 的同一句
   *  （kind 词表今日 `email` / `session_id` / `clock_time`，后者是 §28 追记 2026-09-12 的安静时段两端）；
   *  `reasons`（add-only，§68.7 追记）= 多句的 kind 按 reason 分句（session_id：`leading_hyphen`），没对上的 reason 用 `message`；老 server 缺席 */
  check?: { kind: "email" | "session_id" | "clock_time" | string; message: BilingualText; reasons?: Record<string, BilingualText> };
  /** add-only（§72.4）：数字旋钮的合法闭区间（今日 `screenpipe_media_retention_minutes`）——输入框的 min / max 与
   *  「保存」闸都按它判，与 server 的 `out_of_bounds` 同一条规则（越界 PUT 400，不夹取）；老 server 缺席 */
  bounds?: { min: number; max: number };
  [key: string]: unknown;
}

export interface SettingsSection {
  id: string;
  title: BilingualText;
  help: BilingualText;
  fields: SettingsField[];
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
  [key: string]: unknown;
}

export interface SecretsStatus {
  secrets: SecretStatus[];
  [key: string]: unknown;
}

/** GET /api/screenpipe/disk（§72.1，issue #28）：录制数据磁盘占用快照。`state` computing = 首次、后台还在扫（数字全 null）；
 *  ready = 缓存快照；error = 后台算失败（`error`）。字节全是十进制原值，web 自己格式化。`growth.basis` samples = 按最近样本斜率、
 *  lifetime = db 字节 ÷ 最早 frame 至今天数、null = 样本不足；`last_prune` = act/lib/screenpipe_retention.py 的回执原样。 */
export interface ScreenpipeDiskGrowth {
  bytes_per_month: number | null;
  basis: "samples" | "lifetime" | null | string;
  span_days: number | null;
  samples: number;
}

export interface ScreenpipePruneReceipt {
  ran_at?: string;
  retention_days?: number;
  cutoff?: string | null;
  skipped?: string | null;
  deleted_frames?: number;
  deleted_audio?: number;
  eligible_frames?: number;
  eligible_audio?: number;
  budget_exhausted?: boolean;
  dry_run?: boolean;
  error?: string | null;
  [key: string]: unknown;
}

/** §72.4：`ingest/screenpipe-cleanup.sh` 每轮写的媒体清理回执 `state/screenpipe_prune.json` 的投影。
 *  `state` ok = 干净跑完（可能删了 0 个）/ partial = 没扫完（子目录读不到 / 文件在扫的过程中变动）/
 *  no_data_dir = 还没录过 / unreadable = 目录在但进不去（权限，真失败）/ never = 回执缺席或坏形。
 *  `last_ok_ts` = 上次干净跑完的时刻（失败的轮次原样带下去，不擦掉）、`ok_age_seconds` = 距它多少秒；
 *  `stale` 按 `ok_age_seconds` 超过 server 的 PRUNE_STALE_S 算（链本该 30 分钟一轮）——按上一次**尝试**
 *  算会让每 30 分钟失败一次的清理永远显得新鲜。`age_seconds` 仍是上一次尝试的岁数。 */
export interface ScreenpipeMediaPrune {
  state: "ok" | "partial" | "no_data_dir" | "unreadable" | "never" | string;
  ts: string | null;
  retention_minutes: number | null;
  deleted_files: number | null;
  deleted_bytes: number | null;
  data_dir: string | null;
  last_ok_ts: string | null;
  age_seconds: number | null;
  ok_age_seconds: number | null;
  stale: boolean;
  [key: string]: unknown;
}

export interface ScreenpipeDisk {
  state: "computing" | "ready" | "error" | string;
  computed_at: string | null;
  refreshing: boolean;
  root: string;
  root_exists: boolean;
  total_bytes: number | null;
  db_bytes: number | null;
  backup_bytes: number | null;
  log_bytes: number | null;
  media_bytes: number | null;
  other_bytes: number | null;
  file_count: number | null;
  backups: Array<{ name: string; bytes: number }>;
  db_reclaimable_bytes: number | null;
  oldest_frame_ts: string | null;
  newest_frame_ts: string | null;
  db_error: string | null;
  growth: ScreenpipeDiskGrowth;
  retention_days: number;
  last_prune: ScreenpipePruneReceipt | null;
  /** add-only（§72.4）：媒体保留分钟数的 effective 值与上一轮媒体清理的回执；老 server 缺席 */
  media_retention_minutes?: number;
  media_prune?: ScreenpipeMediaPrune;
  error?: string;
  [key: string]: unknown;
}

/** POST /api/secrets/{name}/verify 回执（§68.3；三分判决：ok / 凭证错 network:false / 判决未知 network:true） */
export interface SecretVerifyResult {
  ok: boolean;
  network: boolean;
  detail: string;
  extra: Record<string, unknown>;   // 探针的原字段（v2.0.0 只剩 Anthropic；precondition = 探针没跑的原因）
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
  /** v2.0.0 add-only：state/recap.log 的 mtime（取代退役的 actd_log） */
  recap_log: { path: string; mtime: number | null; [key: string]: unknown };
  /** readable:false = 目录住 TCC 保护位置且不在 mirror 模式——server 永不读 ~/Documents（§68.3） */
  unprocessed: { path: string; mtime: number | null; readable: boolean; [key: string]: unknown };
  [key: string]: unknown;
}

export interface DiagnosticsSnapshot {
  doctor: DoctorReport;
  health: HealthSnapshot;
  install_report: InstallReport | null;
  logs: LogEntry[];
  cron_probe?: CronProbe | null;
  activity?: IngestActivity | null;
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

/** GET /api/setup/engine（§68.5；原生 EngineDetector）：CLI 路径 / 版本 / 认证梯子（顺序 = server AUTH_LADDER）。
 *  v2.0.0：§78 的 `--bare` 只认 API key，`oauth` 档（Claude Code 登录 / 钥匙串）退役 */
export type EngineAuth = "env_key" | "secrets_file" | "legacy_file";

export interface SetupEngine {
  cli_path: string | null;
  version: string | null;
  auth: EngineAuth | string | null;
  auth_sources: Record<string, boolean>;
  ready: boolean;
  [key: string]: unknown;
}

/** GET /api/setup/vaults（§68.5 追记 D51；原生 ObsidianVaults.registered）：Obsidian 自己登记过、路径仍是目录的库 */
export interface SetupVault {
  name: string;
  path: string;
  [key: string]: unknown;
}

export interface SetupVaults {
  vaults: SetupVault[];
  [key: string]: unknown;
}

// ----- §68.6 关于 / 更新（§26） ----- #
export interface AboutInfo {
  version: string;
  home: string;
  repo: string;
  /** manual_upgrade = 新版本跨了大版本（§26 追记）：只许手动升级，页面给手动命令 */
  update_available: { current?: string; latest?: string; url?: string; pkg_asset_url?: string | null; manual_upgrade?: boolean; [key: string]: unknown } | null;
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

/** POST /api/update/install 回执（§79）：升级器 scripts/upgrade.sh 已分离起跑（ff-only 到带 tag 的 main + install.sh；
 *  永不碰录制引擎）；进度在 log。跨大版本 → 409 manual_upgrade；这个 checkout 还没有升级器 → 501（两者 details.fix = 手动命令） */
export interface UpdateInstallReceipt {
  ok: boolean;
  action: "upgrade" | string;
  script: string;
  log: string;
  latest: string | null;
  [key: string]: unknown;
}
