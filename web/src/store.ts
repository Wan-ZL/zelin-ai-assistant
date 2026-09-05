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
  fetchSecrets,
  fetchSettingsCatalog,
  fetchSetup,
  fetchSkills,
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
import { resolveLanguage, type Language } from "./i18n";
import {
  EMPTY_CARD_FILTERS,
  readCardFilters,
  writeCardFilters,
  type CardFilters,
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
  SettingsSection,
  SetupSnapshot,
} from "./types";

export type ConnectionState = "connecting" | "live" | "reconnecting";

export interface AppState {
  board: Board | null;
  boardError: string | null;      // 最近一次 board 读失败的用户可读文案（成功后清空）
  /** server 可达但 dashboard.json 不存在（`GET /api/board` 404 `NOT_FOUND`，§49）——原生 Store.missing 的镜像：
   *  首次安装 / 后台服务从没跑过。与 boardError 互斥：404 不是「连不上」，不许借离线文案说话（§54.1 追记） */
  boardMissing: boolean;
  boardLoading: boolean;          // 首载 true；SSE 触发的静默 refetch 不置位
  connection: ConnectionState;
  health: HealthSnapshot | null;  // GET /api/health 最近快照（§47.4；PipelineBanner 读）
  selectedCardId: string | null;  // 详情侧栏当前卡（route.ts 同步 ?card= 深链）——卡片详情的唯一面（D34，§49）
  cardDetail: CardDetail | null;  // selectedCardId 对应的 /api/cards/{id} 增补详情
  cardDetailError: string | null;
  /** 本会话里详情侧栏**落地过**的卡主键（不持久化）：T2 提案「需先展开看明细」的闸门读它——看过明细才给「批准」（§54.1 第 2 项追记） */
  detailViewedIds: ReadonlySet<string>;
  language: Language;             // UI 语言（G7 shell：?lang= 覆写 > localStorage > 浏览器）
  filters: CardFilters;           // 过滤 chips + ⌘F 搜索（G4：URL query 是唯一持久化，taskFilters.ts）
  models: ModelsSettings | null;  // GET /api/settings/models 最近快照（§59 设置页「模型」）
  claudeCodeDefault: ClaudeCodeDefault | null; // GET /api/claude-code/default-model（follow 继承的全局默认）
  dailyLoop: DailyLoopSettings | null; // GET /api/settings/daily-loop 最近快照（§70 设置页「每日整理」）
  dailyLoopError: string | null;       // 该 section 读失败的用户可读文案（成功后清空）
  settingsError: string | null;   // 设置页读失败的用户可读文案（成功后清空；保存失败由页面 toast）
  materials: MaterialsList | null; // GET /api/materials/list?status=open 最近快照（§62 设置页「素材库」）
  materialsError: string | null;  // 素材库读失败的用户可读文案（成功后清空；写失败由 section toast）
  sortOrder: SortOrder;           // 卡片排序偏好（镜像原生 cardSortOrder；localStorage 持久化，cardSort.ts）
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

// 启动时解析一次语言偏好：URL ?lang=（一次性覆写）> localStorage > navigator。
// try/catch 兜底（无 window / localStorage 被禁的环境一律回落 en）。
function detectInitialLanguage(): Language {
  try {
    const fromQuery = new URLSearchParams(window.location.search).get("lang");
    if (fromQuery) return resolveLanguage(fromQuery);
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === "zh" || stored === "en") return stored;
    return resolveLanguage(navigator.language);
  } catch {
    return "en";
  }
}

const initialState: AppState = {
  board: null,
  boardError: null,
  boardMissing: false,
  boardLoading: true,
  connection: "connecting",
  health: null,
  selectedCardId: null,
  cardDetail: null,
  cardDetailError: null,
  detailViewedIds: new Set<string>(),
  language: detectInitialLanguage(),
  filters: EMPTY_CARD_FILTERS,
  models: null,
  claudeCodeDefault: null,
  dailyLoop: null,
  dailyLoopError: null,
  settingsError: null,
  materials: null,
  materialsError: null,
  sortOrder: readSortOrder(),
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

/** 全量拉取看板（初载 + SSE board.updated 后 + 断线重连后都走这一条） */
export function refreshBoard(): Promise<void> {
  if (boardRequest) return boardRequest;
  boardRequest = (async () => {
    try {
      const board = await fetchBoard();
      // 「合并中…」章不看 generated_at：每一版快照都跑一遍 §21bis 谓词（副卡全部离开所有列才算落地）
      setState({ board, boardError: null, boardMissing: false, boardLoading: false, forceMergingIds: settledForceMerging(board) });
    } catch (error) {
      if (isBoardMissingError(error)) {
        // 原生 Store.refresh 的缺文件分支（dashboard = nil / missing = true / loadError = nil）：快照一并清——
        // server 明说文件没了，留着旧快照再挂「连不上」横幅是两句谎话
        setState({ board: null, boardError: null, boardMissing: true, boardLoading: false });
        return;
      }
      const message = error instanceof ApiError ? error.message : String(error);
      setState({ boardError: message, boardMissing: false, boardLoading: false });
    } finally {
      boardRequest = null;
    }
  })();
  return boardRequest;
}

/** 选中卡片（null = 关侧栏）；选中即拉详情增补。详情**落地**才记「看过明细」（T2 闸门）：拉失败 / 换卡后迟到的
 *  响应都不算——用户没看到任何明细。记的是 server 回的主键（§60.3：响应 `id` 恒为主键），所以 `?card=<work_id>`
 *  深链打开的侧栏也能解锁卡面按主键判的「批准」。 */
export function selectCard(cardId: string | null) {
  setState({ selectedCardId: cardId, cardDetail: null, cardDetailError: null });
  if (!cardId) return;
  void fetchCard(cardId).then(
    (detail) => {
      if (getState().selectedCardId !== cardId) return;
      const viewedId = typeof detail.id === "string" && detail.id ? detail.id : cardId;
      const detailViewedIds = state.detailViewedIds.has(viewedId)
        ? state.detailViewedIds
        : new Set([...state.detailViewedIds, viewedId]);
      setState({ cardDetail: detail, detailViewedIds });
    },
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

/** 切换 UI 语言并持久化（localStorage zai.lang；写失败静默——仅影响下次启动的默认值） */
export function setLanguage(language: Language) {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    /* 隐私模式等 localStorage 不可写：跳过持久化，本次会话仍然生效 */
  }
  if (state.language !== language) setState({ language });
}

/** 深链进场：从当前 URL 水合过滤器（FilterBar 挂载时调一次；popstate 暂不监听，与 route.ts 一致） */
export function initFiltersFromUrl() {
  setState({ filters: readCardFilters(window.location.search) });
}

/** 改过滤器（部分更新）并同步 URL（replaceState，不进历史栈） */
export function setFilters(patch: Partial<CardFilters>) {
  const filters = { ...state.filters, ...patch };
  setState({ filters });
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
  | "failures" | "mcp" | "claudeSessions";

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

/** 保存一个通用 section（PUT，server 校验 + diff-write）；成功以回执替换目录里的该 section，失败原样抛给页面 toast */
export async function saveSettingsSection(sectionId: string, patch: Record<string, unknown>): Promise<SettingsSection> {
  const section = await putSettingsSection(sectionId, patch);
  const catalog = state.settingsCatalog;
  if (catalog) {
    setState({ settingsCatalog: { ...catalog, sections: catalog.sections.map((s) => (s.id === section.id ? section : s)) } });
  }
  // §48.1 合取写：slack / gmail 的雷达开关翻开 = server 同一笔也写 features.<src>_radar=true（合取的另一半住 flags 区），
  // 而 PUT 回执只有本区——整本目录再拉一次让「Feature flags」那一格跟上（best-effort：拉不到不影响本次保存的回执）
  if ((sectionId === "slack" || sectionId === "gmail") && patch[`${sectionId}_enabled`] === true) void refreshSettingsCatalog();
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
    detailViewedIds: new Set<string>(),
    selectedIds: new Set<string>(),
    claudeSessionsImported: new Set<string>(),
    slackTokenVerifications: 0,
    pageErrors: {},
  };
  boardRequest = null;
  pageRequests.clear();
  diagnosticsLang = null;
  for (const b of forceMergeBatches) window.clearTimeout(b.timer);
  forceMergeBatches = [];
}
