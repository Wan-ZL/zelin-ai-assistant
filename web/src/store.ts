// 全局唯一 state 源：手写 useSyncExternalStore 小店（禁止 App 巨石，禁状态库）。
// 约定（Build 阶段全体组件遵守）：
//   1. 组件读 state 只经 useAppState()（整快照，emit 时整体替换，浅比较即变更检测）；
//   2. 改 state 只经本文件导出的 action 函数（refreshBoard/selectCard/...），组件不直接 setState；
//   3. 新增 UI state（filters/搜索词/抽屉页签…）= 给 AppState 加字段 + 加 action 函数，别处不许存全局态；
//   4. 服务端数据只进 board/cardDetails，前端绝不改写 wire 字段。
import { useSyncExternalStore } from "react";
import {
  ApiError,
  fetchAbout,
  fetchBoard,
  fetchCard,
  fetchClaudeCodeDefault,
  fetchDisplaySettings,
  fetchClaudeSessions,
  fetchDiagnostics,
  fetchDailyLoopSettings,
  fetchFailures,
  fetchHealth,
  fetchLanes,
  fetchMaterials,
  fetchMcp,
  fetchModelsSettings,
  fetchRecapSettings,
  fetchPermissions,
  fetchSearchIndex,
  fetchSecrets,
  fetchSettingsCatalog,
  fetchSettingsSection,
  fetchSetup,
  fetchSkills,
  fetchVoiceProfile,
  postClaudeCodeDefault,
  postMaterialAdd,
  postMaterialDismiss,
  postRecapMark,
  putDisplaySettings,
  postSkill,
  putDailyLoopSettings,
  putModelsSettings,
  putRecapSettings,
  putSettingsSection,
} from "./api";
import { readSortOrder, writeSortOrder, type SortOrder } from "./cardSort";
import { forceMergeLanded } from "./components/board/pendingSettle";
import { applyDisplayPrefs, prefsOf } from "./displayPrefs";
import { getI18n, resolveLanguage, type Language } from "./i18n";
import { navigate, readCardId } from "./route";
import { readExpandedSections, toggledSections, writeExpandedSections } from "./settingsFolds";
import {
  EMPTY_CARD_FILTERS,
  normalizeSessionIndex,
  readCardFilters,
  writeCardFilters,
  type CardFilters,
  type SessionIndex,
} from "./taskFilters";
import type {
  AboutInfo,
  Board,
  CardDetail,
  ClaudeCodeDefault,
  DisplaySettings,
  DisplaySettingsPatch,
  ClaudeSessionsScan,
  DiagnosticsSnapshot,
  DailyLoopPatch,
  DailyLoopSettings,
  FailureCatalog,
  HealthSnapshot,
  LaneCatalog,
  MaterialItem,
  MaterialsList,
  McpList,
  ModelsSettings,
  RecapSettings,
  SkillsSnapshot,
  PermissionsSnapshot,
  SecretsStatus,
  SettingsCatalog,
  SettingsField,
  SettingsSection,
  SetupSnapshot,
  VoiceProfileStatus,
} from "./types";

export type ConnectionState = "connecting" | "live" | "reconnecting";

export interface AppState {
  board: Board | null;
  boardError: string | null;      // 最近一次 board 读失败的用户可读文案（成功后清空）
  /** server 可达但 dashboard.json 不存在（`GET /api/board` 404 `NOT_FOUND`，§49）——原生 Store.missing 的镜像：
   *  首次安装 / 后台服务从没跑过。与 boardError 互斥：404 不是「连不上」，不许借离线文案说话（§54.1 追记） */
  boardMissing: boolean;
  /** 2026-09-05 add-only（§49 追记 `store-resilience-drawer`）：server 答了 2xx 但 dashboard.json 解不出来（不是 JSON /
   *  顶层不是带 `generated_at` 的对象）——原生 Store.swift:320-324 decode 失败分支的镜像：**旧快照留着**（不清 board）、
   *  一行「读取 dashboard.json 失败: …」。与 boardError（连不上）/ boardMissing（文件不在）三态互斥：server 在跑、文件在，
   *  只是内容坏了——健康横幅照常说话，不许借离线文案说「连不上」 */
  boardDecodeError: string | null;
  boardLoading: boolean;          // 首载 true；SSE 触发的静默 refetch 不置位
  connection: ConnectionState;
  health: HealthSnapshot | null;  // GET /api/health 最近快照（§47.4；PipelineBanner 读）
  selectedCardId: string | null;  // 详情侧栏当前卡（route.ts 同步 ?card= 深链）——卡片详情的唯一面（D34，§49）
  cardDetail: CardDetail | null;  // selectedCardId 对应的 /api/cards/{id} 增补详情
  cardDetailError: string | null;
  /** 本会话里详情侧栏**落地过**的卡主键（不持久化）：T2 提案「需先展开看明细」的闸门读它——看过明细才给「批准」（§54.1 第 2 项追记） */
  detailViewedIds: ReadonlySet<string>;
  language: Language;             // UI 语言（D37 §15：真源 = server general.language；首帧 ?lang= 覆写 > localStorage 缓存 > 浏览器，hydrateLanguage 随后对齐）
  filters: CardFilters;           // 过滤 chips + ⌘F 搜索（G4：URL query 是唯一持久化，taskFilters.ts）
  /** §37.2 会话内容层（D45）：GET /api/search-index 的归一化缓存 {card_id → 归一化正文} + ETag。null = 还没拉过——
   *  第一次非空搜索才懒加载（原生 Store.reloadSearchIndexIfNeeded），之后每次搜索开始 / 每版看板落地都条件 GET 重验
   *  （304 零传输）；层缺席（文件不在 → server 200 空表 / 拒读 / 读失败）= 空表，字段搜索照常，永不报错 */
  sessionIndex: SessionIndex | null;
  models: ModelsSettings | null;  // GET /api/settings/models 最近快照（§59 设置页「模型」）
  claudeCodeDefault: ClaudeCodeDefault | null; // GET /api/claude-code/default-model（follow 继承的全局默认）
  dailyLoop: DailyLoopSettings | null; // GET /api/settings/daily-loop 最近快照（§70 设置页「每日整理」）
  dailyLoopError: string | null;       // 该 section 读失败的用户可读文案（成功后清空）
  settingsError: string | null;   // 设置页读失败的用户可读文案（成功后清空；保存失败由页面 toast）
  materials: MaterialsList | null; // GET /api/materials/list?status=open 最近快照（§62 设置页「素材库」）
  materialsError: string | null;  // 素材库读失败的用户可读文案（成功后清空；写失败由 section toast）
  sortOrder: SortOrder;           // 卡片排序偏好（镜像原生 cardSortOrder；localStorage 持久化，cardSort.ts）
  expandedSettingsSections: ReadonlySet<string>; // 设置页展开着的区（D44；镜像原生 settings.expandedSections；localStorage 持久化，settingsFolds.ts）
  lanes: LaneCatalog | null;      // GET /api/lanes 列说明目录（server-owned 文案，Lane 头「?」气泡读）
  recapSettings: RecapSettings | null; // GET /api/settings/recap（§63：enabled / 语言 / Slack 草稿开关）
  recapMarks: Record<string, RecapMark>; // 「复制」/「标记已发送」的乐观本地回执（等下一次 board 回流覆盖）
  displaySettings: DisplaySettings | null; // GET /api/settings/display（§54.1 第 12 项：字号 / 字重 / 描边；到达即落 <html> data-*）
  skills: SkillsSnapshot | null;  // GET /api/skills 最近快照（§67 设置页「Skills」）
  skillsError: string | null;     // 设置页 Skills 读失败的用户可读文案（成功后清空；切换失败由页面 toast）

  // ----- §68 P4 parity 页的 server 快照（每页自己 refresh；读失败落 pageErrors[key]） -----
  settingsCatalog: SettingsCatalog | null; // GET /api/settings（通用 section 目录）
  secrets: SecretsStatus | null;           // GET /api/secrets（只有状态）
  permissions: PermissionsSnapshot | null; // GET /api/permissions
  diagnostics: DiagnosticsSnapshot | null; // GET /api/diagnostics
  setup: SetupSnapshot | null;             // GET /api/setup（首次运行向导判定）
  about: AboutInfo | null;                 // GET /api/about
  failures: FailureCatalog | null;         // GET /api/failures（§25 失败目录双语句；引擎诊断行 / 依赖行按 id 取）
  mcp: McpList | null;                     // GET /api/mcp
  claudeSessions: ClaudeSessionsScan | null; // GET /api/claude-sessions
  voiceProfile: VoiceProfileStatus | null;   // GET /api/voice（§68.1 追记：语气档案「当前生效」行；生成完成后 VoiceGenerate 重拉）
  /** §68.10 追记：本页会话里「导入所选」已提交的 session_id（原生 locallyImported）——与 claudeSessions 快照同寿命
   *  （快照跨组件卸载留存，这个集合也得留存；整页刷新一起清），重新扫描回来的同一批照样过滤 */
  claudeSessionsImported: ReadonlySet<string>;
  /** §68.3 追记：已保存的 Slack token 通过 auth.test 的次数（原生 SettingsSlack.verifyToken .ok → loadDirectory(refresh:true)）——
   *  SecretRow 每次成功 +1，SlackDirectoryPicker 看到它变了就带 refresh 重载一次；会话内瞬态，不是快照 */
  slackTokenVerifications: number;
  pageErrors: Record<string, string | null>; // 上述各面最近一次读失败的文案（成功后清空）
  // ----- §21 多选（原生 Kanban「选择」态）：选中主键集合 + 是否在多选态 -----
  selectionMode: boolean;
  selectedIds: ReadonlySet<string>;
  /** §21bis 强制合并已提交、等真信号的卡（原生 mergeForcingBadge「合并中…」）；会话内瞬态：一批的**每张副卡都
   *  离开所有列**（成为终态 merged）才清（settleForceMerging，原生 PendingForceMerge 判据）——不是 generated_at
   *  一变就清（actd 每个 pass 都重写看板，§39.3 / §21bis）；180 s 没等到 → 章退场 + forceMergeTimedOutAt 落时间戳 */
  forceMergingIds: ReadonlySet<string>;
  /** 2026-09-05 add-only：最近一批强制合并 180 s 没落地的时刻（epoch ms）；提案列顶据此显示原生那句诚实超时条，
   *  关掉 / 120 s 后归 null（原生 notice-merge-force） */
  forceMergeTimedOutAt: number | null;
  /** 2026-09-05 add-only（§54.1 追记 `strips-force-open`）：两条书立条（潜在任务 / 永久性完成）的展开态——挂 store 不挂
   *  组件 @State，换页不丢、**不持久化**（每次启动都收起；原生 Store.swift:127-128）。回执不能落在收起的条里：useSubmit 在
   *  暂缓 / 放回看板 提交成功与 debt / archived 源动作 180 s 超时时置 true（原生 addEcho / beginReturn / sweepTimeouts）；
   *  搜索命中潜在任务时左条不看这面旗直接展开（BacklogStrip，原生 Kanban.swift:326 `.constant(true)`） */
  backlogStripExpanded: boolean;
  archiveStripExpanded: boolean;
}

/** §63 本地标记（server marks.json 的镜像片段） */
export interface RecapMark {
  copied_at?: string | null;
  sent_at?: string | null;
}

const LANGUAGE_STORAGE_KEY = "zai.lang";

const isLanguage = (value: unknown): value is Language => value === "zh" || value === "en";

/** URL `?lang=<值>` 的一次性覆写：**非空**才算（`?lang=` 空值与没写一样），值经 resolveLanguage 归到 zh / en（`zh-CN` 也是 zh，
 *  与 navigator.language 同一规则）。detectInitialLanguage 与 hasLanguageQueryOverride 都读这一条——两边判据永远一致，
 *  不会出现「首帧没用它、却因它跳过水合」的裂缝 */
function readLanguageQueryOverride(search: string): Language | null {
  const fromQuery = new URLSearchParams(search).get("lang");
  return fromQuery ? resolveLanguage(fromQuery) : null;
}

// 首帧语言（D37 §15 追记：只是**水合前的提示**，真源在 server 的 general.language——hydrateLanguage 随后对齐）：
// URL ?lang=（本次会话的一次性覆写，截图 / 演示用；有它就不水合也不持久化）> localStorage 缓存（上次水合 / 选择落下的值，
// 免得首帧闪一下另一种语言）> navigator。try/catch 兜底（无 window / localStorage 被禁的环境一律回落 en）。
function detectInitialLanguage(): Language {
  try {
    const fromQuery = readLanguageQueryOverride(window.location.search);
    if (fromQuery) return fromQuery;
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (isLanguage(stored)) return stored;
    return resolveLanguage(navigator.language);
  } catch {
    return "en";
  }
}

/** URL 上有（非空的）`?lang=` = 这次加载的语言由 URL 说了算（一次性覆写）：不从 server 水合、也不把它写进设置 */
export function hasLanguageQueryOverride(search = window.location.search): boolean {
  try {
    return readLanguageQueryOverride(search) !== null;
  } catch {
    return false;
  }
}

const initialState: AppState = {
  board: null,
  boardError: null,
  boardMissing: false,
  boardDecodeError: null,
  boardLoading: true,
  connection: "connecting",
  health: null,
  selectedCardId: null,
  cardDetail: null,
  cardDetailError: null,
  detailViewedIds: new Set<string>(),
  language: detectInitialLanguage(),
  filters: EMPTY_CARD_FILTERS,
  sessionIndex: null,
  models: null,
  claudeCodeDefault: null,
  dailyLoop: null,
  dailyLoopError: null,
  settingsError: null,
  materials: null,
  materialsError: null,
  sortOrder: readSortOrder(),
  expandedSettingsSections: readExpandedSections(),
  lanes: null,
  recapSettings: null,
  recapMarks: {},
  displaySettings: null,
  skills: null,
  skillsError: null,
  settingsCatalog: null,
  secrets: null,
  permissions: null,
  diagnostics: null,
  setup: null,
  about: null,
  failures: null,
  mcp: null,
  claudeSessions: null,
  voiceProfile: null,
  claudeSessionsImported: new Set<string>(),
  slackTokenVerifications: 0,
  pageErrors: {},
  selectionMode: false,
  selectedIds: new Set<string>(),
  forceMergingIds: new Set<string>(),
  forceMergeTimedOutAt: null,
  backlogStripExpanded: false,
  archiveStripExpanded: false,
};

let state: AppState = initialState;
const listeners = new Set<() => void>();

function setState(patch: Partial<AppState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
}

export function getState(): AppState {
  return state;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 组件读 store 的唯一入口 */
export function useAppState(): AppState {
  return useSyncExternalStore(subscribe, getState, getState);
}

// ----- actions ------------------------------------------------------------ #

let boardRequest: Promise<void> | null = null; // 并发 refetch 合并成一个在途请求

/** `GET /api/board` 的 404 = server 在、文件不在（server/board_source.py 对缺席的 dashboard.json 抛 NOT_FOUND）——
 *  不是离线。导出供判例直测分类。 */
export function isBoardMissingError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.code === "NOT_FOUND");
}

/** `GET /api/board` 2xx 却解不出 JSON（api.request 合成 `READ_FAILED`、status 仍是 2xx）——server 答了、内容坏了，
 *  与断网（status 0 的 `READ_FAILED`）分开。导出供判例直测分类。 */
export function isBoardDecodeError(error: unknown): boolean {
  return error instanceof ApiError && error.code === "READ_FAILED" && error.status >= 200 && error.status < 300;
}

/** 顶层形状校验（原生 `JSONDecoder().decode(Dashboard.self)` 的 web 版最小门）：必须是带字符串 `generated_at` 的对象。
 *  只验顶层——列级由 normalizeBoardShape 补齐、行级宽容留给各组件（wire add-only，前端绝不因新字段崩渲染）。
 *  返回不合格的原因（null = 合格）。 */
export function boardShapeProblem(value: unknown): string | null {
  const { text } = getI18n(state.language);
  if (value === null || typeof value !== "object" || Array.isArray(value)) return text("顶层不是对象", "top level is not an object");
  if (typeof (value as { generated_at?: unknown }).generated_at !== "string") return text("缺少 generated_at", "generated_at is missing");
  return null;
}

/** 七个必有列（`Board` 类型的必填数组键；原生 Dashboard CodingKeys 同一组） */
const BOARD_LANE_KEYS = ["needs_approval", "running", "needs_input", "review", "completed", "debt", "trash"] as const;
/** 可选列（旧 server 缺席即缺席——缺席不补，免得往 wire 镜像里塞 server 没说的键；在场却不是数组才归 `[]`） */
const BOARD_OPTIONAL_LIST_KEYS = ["archived", "merge_suggestions", "fold_receipts", "recaps"] as const;

/** 列级宽容（原生 `Dashboard.init(from:)` / `decodeLossyRows`，shared/Sources/Contract.swift：缺列或整列不是数组 → `[]`，
 *  `counts` 不是对象 → `Counts.empty`）。过了顶层门的合法 JSON 若少一列，`BoardLanes` 直接 `.filter` / `counts[...]` 会把
 *  整板炸进错误边界——而旧快照那时已经被换掉，「重试」拉回同一份体只会再炸一次。这里把它补成能渲染的形状：
 *  一切正常时原样返回（同一引用，不白拷）。 */
export function normalizeBoardShape(board: Board): Board {
  let out: Record<string, unknown> | null = null;
  const patch = (key: string, value: unknown) => {
    out ??= { ...board };
    out[key] = value;
  };
  for (const key of BOARD_LANE_KEYS) if (!Array.isArray(board[key])) patch(key, []);
  for (const key of BOARD_OPTIONAL_LIST_KEYS) if (key in board && !Array.isArray(board[key])) patch(key, []);
  const counts: unknown = board.counts;
  if (counts === null || typeof counts !== "object" || Array.isArray(counts)) patch("counts", {});
  return out === null ? board : (out as unknown as Board);
}

/** 原生 Store.swift:320-324「Keep the previously good dashboard rather than blanking the UI」：快照不动，
 *  一行 `读取 dashboard.json 失败: <原因>`（`L(...) + error.localizedDescription` 逐字）；离线 / 缺文件两态清掉——
 *  server 答了，就不是连不上也不是文件不在 */
function failBoardDecode(reason: string) {
  const { text } = getI18n(state.language);
  setState({
    boardDecodeError: text("读取 dashboard.json 失败: ", "Failed to read dashboard.json: ") + reason,
    boardError: null,
    boardMissing: false,
    boardLoading: false,
  });
}

/** 全量拉取看板（初载 + SSE board.updated 后 + 断线重连后都走这一条） */
export function refreshBoard(): Promise<void> {
  if (boardRequest) return boardRequest;
  boardRequest = (async () => {
    try {
      const raw = await fetchBoard();
      const shapeProblem = boardShapeProblem(raw);
      if (shapeProblem !== null) {
        failBoardDecode(shapeProblem);
        return;
      }
      const board = normalizeBoardShape(raw); // 缺列 / 坏列 → `[]`、坏 counts → `{}`（原生列级宽容），渲染面永远拿到能读的形状
      const previous = state.board;
      // 「合并中…」章不看 generated_at：每一版快照都跑一遍 §21bis 谓词（副卡全部离开所有列才算落地）
      setState({
        board, boardError: null, boardMissing: false, boardDecodeError: null, boardLoading: false,
        forceMergingIds: settledForceMerging(board),
      });
      // 侧栏开着 + 看板换版 → 详情跟上（原生 @Published dashboard 一发布，展开区从新快照重渲染，Store.swift:56-57）。
      // 只认 generated_at 变化：同版重拉（断线重连）不多打一次；首版落地不拉——selectCard 自己的那一拉正在路上 / 刚落地
      const selected = state.selectedCardId;
      if (selected && previous && previous.generated_at !== board.generated_at) followSelectedCardDetail(selected);
      // §37.2 会话层：搜索开着就跟着每版看板重验一次索引（条件 GET，没变 304；原生按 ~10 s tick 的 (mtime,size) 重验）
      if (hasSearch(state.filters)) void refreshSessionIndex();
    } catch (error) {
      if (isBoardMissingError(error)) {
        // 原生 Store.refresh 的缺文件分支（dashboard = nil / missing = true / loadError = nil）：快照一并清——
        // server 明说文件没了，留着旧快照再挂「连不上」横幅是两句谎话
        setState({ board: null, boardError: null, boardMissing: true, boardDecodeError: null, boardLoading: false });
        return;
      }
      if (isBoardDecodeError(error)) {
        failBoardDecode((error as ApiError).message);
        return;
      }
      const message = error instanceof ApiError ? error.message : String(error);
      setState({ boardError: message, boardMissing: false, boardDecodeError: null, boardLoading: false });
    } finally {
      boardRequest = null;
    }
  })();
  return boardRequest;
}

/** 详情落地：用户还停在这张卡才替换 cardDetail，并记「看过明细」（T2 闸门）。selectCard 的首拉与看板换版后的
 *  跟随重拉共用同一条落地路——两条路对同一张卡的响应谁后到谁算（都是 server 此刻的真话） */
function landCardDetail(cardId: string, detail: CardDetail) {
  if (getState().selectedCardId !== cardId) return;
  const viewedId = typeof detail.id === "string" && detail.id ? detail.id : cardId;
  const detailViewedIds = state.detailViewedIds.has(viewedId)
    ? state.detailViewedIds
    : new Set([...state.detailViewedIds, viewedId]);
  setState({ cardDetail: detail, cardDetailError: null, detailViewedIds });
}

let detailFollowSeq = 0; // 跟随重拉的序号：只有最新一次的响应才落 cardDetail（乱序到达的旧版丢弃）；换卡即作废在途的

/** 看板换版后让开着的侧栏跟上：静默重拉 `/api/cards/{id}`，**成功才替换**——中途不清旧详情（不闪「加载详情…」，
 *  旧详情仍在说上一版的真话）、失败不报（cardDetailError 归 selectCard 的首拉；下一版再试）。 */
function followSelectedCardDetail(cardId: string) {
  const seq = ++detailFollowSeq;
  void fetchCard(cardId).then(
    (detail) => {
      if (seq !== detailFollowSeq) return;
      landCardDetail(cardId, detail);
    },
    () => { /* 静默：旧详情留着 */ },
  );
}

/** 选中卡片（null = 关侧栏）；选中即拉详情增补。详情**落地**才记「看过明细」（T2 闸门）：拉失败 / 换卡后迟到的
 *  响应都不算——用户没看到任何明细。记的是 server 回的主键（§60.3：响应 `id` 恒为主键），所以 `?card=<work_id>`
 *  深链打开的侧栏也能解锁卡面按主键判的「批准」。 */
export function selectCard(cardId: string | null) {
  detailFollowSeq += 1; // 上一张卡在途的跟随重拉作废
  setState({ selectedCardId: cardId, cardDetail: null, cardDetailError: null });
  if (!cardId) return;
  void fetchCard(cardId).then(
    (detail) => landCardDetail(cardId, detail),
    (error) => {
      if (getState().selectedCardId !== cardId) return;
      const message = error instanceof ApiError ? error.message : String(error);
      setState({ cardDetailError: message });
    },
  );
}

export function setConnection(connection: ConnectionState) {
  if (state.connection !== connection) setState({ connection });
}

/** 拉一次 /api/health（§47.4）。读失败保留上一份快照——离线由 ErrorBanner 声明，这里不双报 */
export async function refreshHealth(): Promise<void> {
  try {
    const health = await fetchHealth();
    setState({ health });
  } catch {
    /* server 连不上：ErrorBanner 负责；旧快照留着（它可能仍在说真话） */
  }
}

// ----- 语言（D37，§15 追记 2026-09-06：一把开关）----------------------------------------------------- #
// 真源 = server 的 general.language（settings_overrides.json `language`，python 侧通知 / 修法句读同一个键，壳启动时也读它）。
// web 是它的**写者**：顶栏切换 / `/lang` / 向导单选 / 设置区「保存」全走 PUT /api/settings/general {language}（server 对这把键
// write:always——显式选择必须落键，原生 Settings.persistLanguage）；壳不写（§61.1）。localStorage `zai.lang` 自此只是首帧缓存。

/** 本地半边：切换 UI 语言 + 刷首帧缓存（localStorage zai.lang；写失败静默）。**不写 server**——那是 chooseLanguage / 设置区保存的事；
 *  水合（hydrateLanguage）与设置区保存成功后的即时切换走这里 */
export function setLanguage(language: Language) {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    /* 隐私模式等 localStorage 不可写：跳过缓存，本次会话仍然生效 */
  }
  if (state.language !== language) setState({ language });
}

// 每一笔带 language 的 general 写 +1（saveSettingsSection 记；chooseLanguage / 设置区「保存」/ 首启持久化都经它）：晚到的 PUT 回执 /
// 启动水合不许压掉更新的选择（detailFollowSeq 同款守卫）
let languageChoiceSeq = 0;

/** 用户显式选语言（顶栏切换 / `/lang` / 向导单选；原生 Store.swift `/lang` 与 Settings.persistLanguage 同一条路）：UI 立刻切、
 *  摘掉 URL 上一次性的 `?lang=`（三个入口一个样——不摘，刷新后 query 又压过刚选的语言、且有它就不水合），再 PUT general.language
 *  让 python 侧 / 壳 / 下次启动都跟上。PUT 失败静默（离线、无 token 的浏览器会话）：本次会话仍是新语言，下次启动由 server 的值
 *  决定——与 §29bis feedback_publish_default 的 best-effort 同款 */
export function chooseLanguage(language: Language): Promise<void> {
  setLanguage(language);
  dropLanguageQueryOverride();
  return saveSettingsSection("general", { language }).then(() => undefined, () => undefined);
}

/** 摘掉 URL 上的 `?lang=`（别的 query 留着；replaceState 不进历史栈，经 route.navigate 让 useRoute 订阅者同步——D44 摘锚点同法）；
 *  URL 操作失败不影响语言切换本身 */
function dropLanguageQueryOverride(): void {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("lang")) return;
    url.searchParams.delete("lang");
    navigate(url, true);
  } catch {
    /* 无 window / URL 不可写：只是留着一次性覆写，本次切换照样生效 */
  }
}

/** 启动水合（App 挂载一次）：`GET /api/settings/general` → 显式值（source override / config）压过首帧的缓存 / 浏览器猜测；
 *  **source == default（谁都没选过）→ 首启持久化**：把用户此刻正看着的语言写进设置（原生 L10n.swift 首启的同一件事——launchd / cron
 *  下的 python 没有 LANG，不持久化的 zh 用户通知会回落成 en；幂等：只在 default 时写，永不盖显式值；best-effort——离线 / 无 token
 *  就留给下一次启动）。`?lang=` 在场 = 一次性覆写，两件事都不做；读失败 = 留着首帧的提示（离线由 ErrorBanner 声明）；水合期间用户
 *  先选了语言（顶栏 / `/lang` / 向导 / 设置区「保存」——都经 saveSettingsSection 记序号）→ 用户赢，server 的旧值不压回来、也不再首启持久化 */
export async function hydrateLanguage(): Promise<void> {
  if (hasLanguageQueryOverride()) return;
  const seq = languageChoiceSeq;
  let field: SettingsField | undefined;
  try {
    field = (await fetchSettingsSection("general")).fields.find((f) => f.key === "language");
  } catch {
    return;
  }
  if (!field || seq !== languageChoiceSeq) return;
  if (field.source === "default") {
    await saveSettingsSection("general", { language: state.language }).then(() => undefined, () => undefined);
    return;
  }
  if (isLanguage(field.effective)) setLanguage(field.effective);
}

/** 深链进场：从当前 URL 水合过滤器（FilterBar 挂载时调一次；之后的换页 / 后退前进由 syncRouteFromUrl 跟） */
export function initFiltersFromUrl() {
  applyFilters(readCardFilters(window.location.search));
}

function sameFilters(a: CardFilters, b: CardFilters): boolean {
  return a.deadline === b.deadline && a.reraisedOnly === b.reraisedOnly && a.search === b.search
    && a.tiers.length === b.tiers.length && a.tiers.every((tier, i) => tier === b.tiers[i]);
}

const hasSearch = (filters: CardFilters): boolean => filters.search.trim() !== "";

/** 过滤器落 store 的唯一落点：搜索从空变非空（键入第一个字 / `?q=` 深链 / 后退带回一版搜索词）= 会话层的懒加载 / 重验时机
 *  （原生 hitInfo 在有查询时才 reloadSearchIndexIfNeeded）。第一次拉全量，之后条件 GET（304 零传输）。 */
function applyFilters(filters: CardFilters) {
  const began = hasSearch(filters) && !hasSearch(state.filters);
  setState({ filters });
  if (began) void refreshSessionIndex();
}

// ----- §37.2 会话内容层（D45；原生 Store.reloadSearchIndexIfNeeded + searchIndexNorm） ----------------------------- #

let sessionIndexRequest: Promise<void> | null = null; // 并发重验合并成一个在途请求
let sessionIndexSeq = 0; // resetStoreForTests 一变，在途请求的回执作废（detailFollowSeq 同款）

/** 空层（缺席 / 拒读 / 读失败）：有了它就不再算「还没拉过」，下次搜索开始 / 看板落地再重验 */
const ABSENT_SESSION_INDEX: SessionIndex = { etag: null, texts: {} };

const isAbsentIndex = (index: SessionIndex | null): boolean =>
  index !== null && index.etag === null && Object.keys(index.texts).length === 0;

/** 条件 GET /api/search-index：304 → 缓存不动；200 → 归一化一次落 sessionIndex（正文只在这里归一化，不按键归一化）；
 *  缺席（200 空表）/ 断网 / 坏体 → 空层（字段搜索照常，永不报错）。导出给判例与别的回流路径。 */
export function refreshSessionIndex(): Promise<void> {
  if (sessionIndexRequest) return sessionIndexRequest;
  const seq = sessionIndexSeq;
  const etag = state.sessionIndex?.etag ?? null;
  sessionIndexRequest = fetchSearchIndex(etag).then(
    (result) => {
      if (result === null || seq !== sessionIndexSeq) return; // 304：带去的 ETag 仍有效 / 回执已作废
      const next: SessionIndex = { etag: result.etag, texts: normalizeSessionIndex(result.snapshot.entries) };
      // 缺席 → 缺席（404 → 404）不换对象：免得每版看板都让全部卡片重渲染一遍
      if (isAbsentIndex(next) && isAbsentIndex(state.sessionIndex)) return;
      setState({ sessionIndex: next });
    },
    () => {
      if (seq === sessionIndexSeq && state.sessionIndex === null) setState({ sessionIndex: ABSENT_SESSION_INDEX });
    },
  ).finally(() => {
    if (seq === sessionIndexSeq) sessionIndexRequest = null;
  });
  return sessionIndexRequest;
}

/** D40 客户端路由（§49 / §54.4 2026-09-06 追记）：`route.navigate` / popstate 之后把 URL 里**不进历史栈**的两样东西同步回
 *  store——过滤器（URL 是它唯一的持久化：后退 / 前进带回那一版的 `?q=` / `tier=`）与 `?card=` 抽屉（后退回到开着抽屉的
 *  那一版就重开；换页链接不带 `card` → 关）。页本身不进 AppState：组件经 `route.useRoute()` 直接读 URL（真源），
 *  没有第二份。App 挂载时 `subscribeRoute(syncRouteFromUrl)` 接线。幂等——与 FilterBar / DetailDrawer 挂载时的水合同一结果。 */
export function syncRouteFromUrl(): void {
  const search = window.location.search;
  const filters = readCardFilters(search);
  if (!sameFilters(filters, state.filters)) applyFilters(filters);
  const cardId = readCardId(search);
  if (cardId !== state.selectedCardId) selectCard(cardId);
}

/** 改过滤器（部分更新）并同步 URL（replaceState，不进历史栈） */
export function setFilters(patch: Partial<CardFilters>) {
  const filters = { ...state.filters, ...patch };
  applyFilters(filters);
  writeCardFilters(filters);
}

export function clearFilters() {
  setFilters(EMPTY_CARD_FILTERS);
}

// ----- 看板展示偏好（原生 parity：排序 / 列说明；就地展开详情 D34 退役——详情只有侧栏一面） ------ #

/** 改卡片排序偏好并持久化（localStorage cardSortOrder，原生同名 UserDefaults 键） */
export function setSortOrder(sortOrder: SortOrder) {
  writeSortOrder(sortOrder);
  if (state.sortOrder !== sortOrder) setState({ sortOrder });
}

// ----- 设置页分区开合（D44；原生 SettingsCollapseStore：toggle 由区头按钮驱动、expand 由 ?anchor= / 目录点击驱动，都记住） ----- #

/** 翻一区的开合并持久化（localStorage settings.expandedSections，原生同名 UserDefaults 键） */
export function toggleSettingsSection(id: string) {
  const expandedSettingsSections = toggledSections(state.expandedSettingsSections, id);
  writeExpandedSections(expandedSettingsSections);
  setState({ expandedSettingsSections });
}

/** 强制展开一区并记住（深链 / 目录点击；已展开则零动作——原生 `expand` 的 guard） */
export function expandSettingsSection(id: string) {
  if (state.expandedSettingsSections.has(id)) return;
  const expandedSettingsSections = new Set(state.expandedSettingsSections);
  expandedSettingsSections.add(id);
  writeExpandedSections(expandedSettingsSections);
  setState({ expandedSettingsSections });
}

/** 拉一次列说明目录（server 常量；失败保留 null——列头只是少个「?」，不双报） */
export async function refreshLanes(): Promise<void> {
  try {
    const lanes = await fetchLanes();
    setState({ lanes });
  } catch {
    /* 离线由 ErrorBanner 声明 */
  }
}

// ----- settings（§59 设置页） ---------------------------------------------- #

/** 拉设置页「模型」的两份快照（旋钮 + Claude Code 全局默认）；读失败落 settingsError */
export async function refreshSettings(): Promise<void> {
  try {
    const [models, claudeCodeDefault] = await Promise.all([
      fetchModelsSettings(),
      fetchClaudeCodeDefault(),
    ]);
    setState({ models, claudeCodeDefault, settingsError: null });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : String(error);
    setState({ settingsError: message });
  }
}

/** 保存旋钮（PUT，server 校验 + diff-write）；成功以 server 回执替换快照，失败原样抛给页面 toast */
export async function saveModels(patch: { dispatch?: string; pipeline?: string }): Promise<ModelsSettings> {
  const models = await putModelsSettings(patch);
  setState({ models });
  return models;
}

/** 一键「设为 <id>」：改 Claude Code 全局默认（server 只改 model 键、先备份）；成功后重拉全局默认 */
export async function setClaudeCodeDefaultModel(model: string): Promise<string | null> {
  const receipt = await postClaudeCodeDefault(model);
  const claudeCodeDefault = await fetchClaudeCodeDefault();
  setState({ claudeCodeDefault });
  return receipt.backup;
}

// ----- settings（§70 每日整理） -------------------------------------------- #

/** 拉设置页「每日整理」的快照；读失败落 dailyLoopError（与「模型」section 互不连坐） */
export async function refreshDailyLoop(): Promise<void> {
  try {
    const dailyLoop = await fetchDailyLoopSettings();
    setState({ dailyLoop, dailyLoopError: null });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : String(error);
    setState({ dailyLoopError: message });
  }
}

/** 保存旋钮子集（PUT，server 校验 + diff-write）；成功以 server 回执替换快照，失败原样抛给页面 toast */
export async function saveDailyLoop(patch: DailyLoopPatch): Promise<DailyLoopSettings> {
  const dailyLoop = await putDailyLoopSettings(patch);
  setState({ dailyLoop });
  return dailyLoop;
}

// ----- 素材库（§62 设置页 section） ------------------------------------------ #

/** 拉开放条目（弹窗内容 + 按钮计数）；读失败落 materialsError */
export async function refreshMaterials(): Promise<void> {
  try {
    const materials = await fetchMaterials("open");
    setState({ materials, materialsError: null });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : String(error);
    setState({ materialsError: message });
  }
}

/** 加入一条（server 归一 + 校验）；成功后重拉列表，失败原样抛给 section toast */
export async function addMaterial(body: { url: string; note: string }): Promise<MaterialItem> {
  const item = await postMaterialAdd(body);
  await refreshMaterials();
  return item;
}

/** 放弃一条；成功后重拉列表 */
export async function dismissMaterial(id: string): Promise<MaterialItem> {
  const item = await postMaterialDismiss(id);
  await refreshMaterials();
  return item;
}

// ----- 会议纪要（§63） ------------------------------------------------------- #

/** 拉 recap 三把旋钮（页面与设置 section 共用）；读失败落 settingsError */
export async function refreshRecapSettings(): Promise<void> {
  try {
    const recapSettings = await fetchRecapSettings();
    setState({ recapSettings, settingsError: null });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : String(error);
    setState({ settingsError: message });
  }
}

/** 保存 recap 旋钮（PUT，server diff-write）；成功以 server 回执替换快照 */
export async function saveRecapSettings(
  patch: { enabled?: boolean; default_language?: string; slack_draft_enabled?: boolean },
): Promise<RecapSettings> {
  const recapSettings = await putRecapSettings(patch);
  setState({ recapSettings });
  return recapSettings;
}

/** 「复制」/「标记已发送」：POST 本地标记并乐观记住回执（board 回流时以 server 投影为准） */
export async function markRecap(key: string, mark: "copied" | "sent", on = true): Promise<void> {
  const receipt = await postRecapMark(key, mark, on);
  setState({ recapMarks: { ...state.recapMarks, [key]: { copied_at: receipt.copied_at, sent_at: receipt.sent_at } } });
}

// ----- 显示偏好（§54.1 第 12 项） ------------------------------------------- #

/** 拉三把显示旋钮并立刻落到 <html>（App 启动一次 + 设置 section 挂载）；读失败落 settingsError、页面保持首帧缓存的值 */
export async function refreshDisplaySettings(): Promise<void> {
  try {
    const displaySettings = await fetchDisplaySettings();
    applyDisplayPrefs(prefsOf(displaySettings));
    setState({ displaySettings, settingsError: null });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : String(error);
    setState({ settingsError: message });
  }
}

/** 改一把旋钮：先落 <html>（即时预览，Apple 设置式无保存键），再 PUT；server 拒绝则回滚到最近快照并把错误抛给 section toast */
export async function saveDisplaySettings(patch: DisplaySettingsPatch): Promise<DisplaySettings> {
  const previous = state.displaySettings;
  if (previous) applyDisplayPrefs(prefsOf({ ...previous, ...patch }));
  try {
    const displaySettings = await putDisplaySettings(patch);
    applyDisplayPrefs(prefsOf(displaySettings));
    setState({ displaySettings });
    return displaySettings;
  } catch (error) {
    if (previous) applyDisplayPrefs(prefsOf(previous));
    throw error;
  }
}

// ----- skills（§67 设置页「Skills」） ------------------------------------------ #

/** 拉 skill 商店快照（manifest + 本机状态）；读失败落 skillsError */
export async function refreshSkills(): Promise<void> {
  try {
    const skills = await fetchSkills();
    setState({ skills, skillsError: null });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : String(error);
    setState({ skillsError: message });
  }
}

/** 启用/停用一个 skill（POST，server 建/删 ~/.claude/skills 软链接）；成功以 server 回执替换快照，失败原样抛给页面 toast */
export async function toggleSkill(name: string, action: "enable" | "disable"): Promise<SkillsSnapshot> {
  const skills = await postSkill(name, action);
  setState({ skills, skillsError: null });
  return skills;
}

// ----- §68 parity 页快照（一个通用 loader：成功落字段、失败落 pageErrors[key]） -------- #

type PageKey = "settingsCatalog" | "secrets" | "permissions" | "diagnostics" | "setup" | "about"
  | "failures" | "mcp" | "claudeSessions" | "voiceProfile";

const pageRequests = new Map<PageKey, Promise<void>>(); // 同一面并发 refresh 合并成一个在途请求（十个通用区同时挂载）

function loadPage<K extends PageKey>(key: K, fetcher: () => Promise<AppState[K]>): Promise<void> {
  const inflight = pageRequests.get(key);
  if (inflight) return inflight;
  const request = (async () => {
    try {
      const data = await fetcher();
      setState({ [key]: data, pageErrors: { ...state.pageErrors, [key]: null } } as Partial<AppState>);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setState({ pageErrors: { ...state.pageErrors, [key]: message } });
    } finally {
      pageRequests.delete(key);
    }
  })();
  pageRequests.set(key, request);
  return request;
}

export const refreshSettingsCatalog = () => loadPage("settingsCatalog", fetchSettingsCatalog);
export const refreshSecrets = () => loadPage("secrets", fetchSecrets);
export const refreshPermissions = (refresh = false) => loadPage("permissions", () => fetchPermissions(refresh));
// lang = store 的当前 UI 语言：doctor 子进程的人话随之（§68.4 追记；原生 DepsView 切语言即 model.check()）。
// doctor 要跑几秒：在途请求带的若是另一种语言（正跑着切了语言），loadPage 的在途合并会把这次切换吞掉——
// 等它落地再按当前语言补拉一次，旧语言的行不许留着。
let diagnosticsLang: Language | null = null;   // 在途 diagnostics 请求带的语言
export function refreshDiagnostics(refresh = false): Promise<void> {
  const inflight = pageRequests.get("diagnostics");
  if (inflight && diagnosticsLang !== state.language) return inflight.then(() => refreshDiagnostics(refresh));
  const lang = state.language;
  diagnosticsLang = lang;
  return loadPage("diagnostics", () => fetchDiagnostics(refresh, lang));
}
export const refreshSetup = () => loadPage("setup", fetchSetup);
export const refreshAbout = () => loadPage("about", fetchAbout);
export const refreshFailures = () => loadPage("failures", fetchFailures);
export const refreshMcp = () => loadPage("mcp", fetchMcp);
export const refreshClaudeSessions = (window = 7) => loadPage("claudeSessions", () => fetchClaudeSessions(window));
export const refreshVoiceProfile = () => loadPage("voiceProfile", fetchVoiceProfile);

/** 保存一个通用 section（PUT，server 校验 + diff-write）；成功以回执替换目录里的该 section，失败原样抛给页面 toast */
export async function saveSettingsSection(sectionId: string, patch: Record<string, unknown>): Promise<SettingsSection> {
  // D37（§15 追记）：patch 带 language = 拨了「界面语言」这把开关（顶栏 / `/lang` / 向导经 chooseLanguage，设置区「保存」与首启持久化
  // 直接到这里）——记一个序号：晚到的启动水合、更早那一笔的回执都不许压掉它
  const chosen = sectionId === "general" && isLanguage(patch.language) ? patch.language : null;
  if (chosen) languageChoiceSeq += 1;
  const languageSeq = languageChoiceSeq;
  const section = await putSettingsSection(sectionId, patch);
  const superseded = chosen !== null && languageSeq !== languageChoiceSeq;
  const catalog = state.settingsCatalog;
  // 回执替换目录里的该 section——被更新的语言选择超过的回执除外：拿它替目录会让「界面语言」那格说旧话（下面改成整本再拉）
  if (catalog && !superseded) {
    setState({ settingsCatalog: { ...catalog, sections: catalog.sections.map((s) => (s.id === section.id ? section : s)) } });
  }
  if (chosen) {
    // 落键成功 = 同一把开关拨了——UI 立刻切（原生 persistLanguage 写完即 LanguageStore.lang = …），不等下次启动；
    // 写的期间用户又选了别的 → 后选的赢，这份回执不拨回去
    if (!superseded) setLanguage(chosen);
    // 目录 GET 正在路上（与设置页同时挂载 / 首启持久化）时带回的还是写之前的快照——等它落地再补拉一次（refreshDiagnostics 的在途
    // 语言同款处理），「界面语言」那格的来源章才不说谎；被超过的回执没替目录，目录在手就补拉一次对齐 server 现状
    const inflight = pageRequests.get("settingsCatalog");
    if (inflight) void inflight.then(() => refreshSettingsCatalog());
    else if (superseded && catalog) void refreshSettingsCatalog();
  }
  // §48.1 合取写：slack / gmail 的雷达开关翻开 = server 同一笔也写 features.<src>_radar=true（合取的另一半住 flags 区），
  // 而 PUT 回执只有本区——整本目录再拉一次让「Feature flags」那一格跟上（best-effort：拉不到不影响本次保存的回执）
  if ((sectionId === "slack" || sectionId === "gmail") && patch[`${sectionId}_enabled`] === true) void refreshSettingsCatalog();
  // §68.7 追记：「通用 · 终端应用」换了 = 开发者区投影的 `terminal_app_name`（「会在 <终端> 中打开」的名字，server 算的）
  // 要跟着变，而它住另一区、PUT 回执只有本区——同样整本再拉一次（best-effort）
  if (sectionId === "general" && "terminal_app" in patch) void refreshSettingsCatalog();
  return section;
}

/** 外部（向导 / 凭证保存）改了 setup 判定后直接落新快照 */
export function setSetup(setup: SetupSnapshot) {
  setState({ setup });
}

/** §68.10 追记：「导入所选」成功提交的 session_id 记进本页会话（原生 locallyImported）；ClaudeImportSection 据此从候选里剔除 */
export function markClaudeSessionsImported(ids: Iterable<string>) {
  setState({ claudeSessionsImported: new Set([...state.claudeSessionsImported, ...ids]) });
}

/** §68.3 追记：已保存的 Slack token 刚通过 auth.test（原生「token freshly working → offer the pickers with fresh data」）；
 *  挂着的 SlackDirectoryPicker 据此带 refresh 重载一次 */
export function markSlackTokenVerified() {
  setState({ slackTokenVerifications: state.slackTokenVerifications + 1 });
}

// ----- §21 多选态（原生 Kanban「选择」）：进入/退出 + 勾选 -------------------------------- #

export function setSelectionMode(on: boolean) {
  setState({ selectionMode: on, selectedIds: on ? state.selectedIds : new Set<string>() });
}

export function toggleSelected(cardId: string) {
  const next = new Set(state.selectedIds);
  if (next.has(cardId)) next.delete(cardId);
  else next.add(cardId);
  setState({ selectedIds: next });
}

export function clearSelection() {
  setState({ selectedIds: new Set<string>() });
}

// ----- v0.33 两条书立条的展开态（原生 Store.backlogStripExpanded / archiveStripExpanded；§54.1 追记） ------------ #
// 只有这两个 setter 写旗：书立条头的开合按钮、useSubmit 的强制展开。不进 URL、不进 localStorage。

export function setBacklogStripExpanded(on: boolean) {
  if (state.backlogStripExpanded !== on) setState({ backlogStripExpanded: on });
}

export function setArchiveStripExpanded(on: boolean) {
  if (state.archiveStripExpanded !== on) setState({ archiveStripExpanded: on });
}

// ----- §21bis 强制合并的在途批次（原生 Store.mergeForcingLocal: [PendingForceMerge]） -------------------- #

/** 原生 180 s sweep 同款：一批副卡 180 s 还没离开所在列 = 合并没落地（actd 没在跑 / 请求被判无效丢弃） */
export const FORCE_MERGE_TIMEOUT_MS = 180_000;

interface ForceMergeBatch {
  primary: string;
  secondaries: string[];
  sentGeneratedAt: string | null;
  timer: number;
}

let forceMergeBatches: ForceMergeBatch[] = []; // 章的真源；forceMergingIds 是它派生的平铺集合

function forceMergingIdsOf(batches: readonly ForceMergeBatch[]): ReadonlySet<string> {
  return new Set(batches.flatMap((b) => [b.primary, ...b.secondaries]));
}

/** §21bis 强制合并已提交：涉及的卡挂「合并中…」章，直到每张副卡都离开所有列（settleForceMerging）或 180 s 到期。
 *  primary 缺席（旧调用方）→ 第一张当主卡。 */
export function markForceMerging(ids: Iterable<string>, primary: string | null = null) {
  const list = [...new Set(ids)];
  if (list.length === 0) return;
  const head = primary !== null && list.includes(primary) ? primary : list[0];
  const batch: ForceMergeBatch = {
    primary: head,
    secondaries: list.filter((id) => id !== head),
    sentGeneratedAt: state.board?.generated_at ?? null,
    timer: 0,
  };
  batch.timer = window.setTimeout(() => expireForceMerge(batch), FORCE_MERGE_TIMEOUT_MS);
  forceMergeBatches = [...forceMergeBatches, batch];
  setState({ forceMergingIds: forceMergingIdsOf(forceMergeBatches) });
}

/** 对一版快照跑 §21bis 谓词：落地的批次出列（清它的定时器），返回还在途的平铺 id 集合（不 setState——refreshBoard
 *  与 board 同一笔落地；对外的 settleForceMerging 才 setState） */
function settledForceMerging(board: Board): ReadonlySet<string> {
  const remaining = forceMergeBatches.filter((b) => !forceMergeLanded(b.secondaries, board, b.sentGeneratedAt));
  if (remaining.length === forceMergeBatches.length) return state.forceMergingIds;
  for (const b of forceMergeBatches) if (!remaining.includes(b)) window.clearTimeout(b.timer);
  forceMergeBatches = remaining;
  return forceMergingIdsOf(remaining);
}

/** add-only：按一版快照结算在途的强制合并批次（refreshBoard 内联同一谓词；导出给判例与别的回流路径） */
export function settleForceMerging(board: Board) {
  const forceMergingIds = settledForceMerging(board);
  if (forceMergingIds !== state.forceMergingIds) setState({ forceMergingIds });
}

/** 180 s 到期：这一批的章退场，提案列顶给原生那句诚实超时条（forceMergeTimedOutAt） */
function expireForceMerge(batch: ForceMergeBatch) {
  if (!forceMergeBatches.includes(batch)) return;
  forceMergeBatches = forceMergeBatches.filter((b) => b !== batch);
  setState({ forceMergingIds: forceMergingIdsOf(forceMergeBatches), forceMergeTimedOutAt: Date.now() });
}

/** 关掉强制合并超时条（用户点 × / 120 s 自动） */
export function dismissForceMergeTimeout() {
  if (state.forceMergeTimedOutAt !== null) setState({ forceMergeTimedOutAt: null });
}

/** 仅测试用：重置 store（vitest 各 case 之间隔离） */
export function resetStoreForTests() {
  state = {
    ...initialState,
    sortOrder: readSortOrder(),
    expandedSettingsSections: readExpandedSections(),
    detailViewedIds: new Set<string>(),
    selectedIds: new Set<string>(),
    claudeSessionsImported: new Set<string>(),
    slackTokenVerifications: 0,
    pageErrors: {},
  };
  boardRequest = null;
  sessionIndexRequest = null;
  sessionIndexSeq += 1;
  detailFollowSeq = 0;
  pageRequests.clear();
  diagnosticsLang = null;
  languageChoiceSeq = 0;
  for (const b of forceMergeBatches) window.clearTimeout(b.timer);
  forceMergeBatches = [];
}
