// 全局唯一 state 源：手写 useSyncExternalStore 小店（禁止 App 巨石，禁状态库）。
// 约定（Build 阶段全体组件遵守）：
//   1. 组件读 state 只经 useAppState()（整快照，emit 时整体替换，浅比较即变更检测）；
//   2. 改 state 只经本文件导出的 action 函数（refreshBoard/selectCard/...），组件不直接 setState；
//   3. 新增 UI state（filters/搜索词/抽屉页签…）= 给 AppState 加字段 + 加 action 函数，别处不许存全局态；
//   4. 服务端数据只进 board/cardDetails，前端绝不改写 wire 字段。
import { useSyncExternalStore } from "react";
import {
  ApiError,
  fetchBoard,
  fetchCard,
  fetchClaudeCodeDefault,
  fetchDailyLoopSettings,
  fetchHealth,
  fetchLanes,
  fetchMaterials,
  fetchModelsSettings,
  fetchRecapSettings,
  postClaudeCodeDefault,
  postMaterialAdd,
  postMaterialDismiss,
  postRecapMark,
  putDailyLoopSettings,
  putModelsSettings,
  putRecapSettings,
} from "./api";
import { readSortOrder, writeSortOrder, type SortOrder } from "./cardSort";
import { resolveLanguage, type Language } from "./i18n";
import {
  EMPTY_CARD_FILTERS,
  readCardFilters,
  writeCardFilters,
  type CardFilters,
} from "./taskFilters";
import type {
  Board,
  CardDetail,
  ClaudeCodeDefault,
  DailyLoopPatch,
  DailyLoopSettings,
  HealthSnapshot,
  LaneCatalog,
  MaterialItem,
  MaterialsList,
  ModelsSettings,
  RecapSettings,
} from "./types";

export type ConnectionState = "connecting" | "live" | "reconnecting";

export interface AppState {
  board: Board | null;
  boardError: string | null;      // 最近一次 board 读失败的用户可读文案（成功后清空）
  boardLoading: boolean;          // 首载 true；SSE 触发的静默 refetch 不置位
  connection: ConnectionState;
  health: HealthSnapshot | null;  // GET /api/health 最近快照（§47.4；PipelineBanner 读）
  selectedCardId: string | null;  // 详情抽屉当前卡（route.ts 同步 ?card= 深链）
  cardDetail: CardDetail | null;  // selectedCardId 对应的 /api/cards/{id} 增补详情
  cardDetailError: string | null;
  language: Language;             // UI 语言（G7 shell：?lang= 覆写 > localStorage > 浏览器）
  filters: CardFilters;           // 过滤 chips + ⌘F 搜索（G4：URL query 是唯一持久化，taskFilters.ts）
  models: ModelsSettings | null;  // GET /api/settings/models 最近快照（§59 设置页「模型」）
  claudeCodeDefault: ClaudeCodeDefault | null; // GET /api/claude-code/default-model（follow 继承的全局默认）
  dailyLoop: DailyLoopSettings | null; // GET /api/settings/daily-loop 最近快照（§62 设置页「每日整理」）
  dailyLoopError: string | null;       // 该 section 读失败的用户可读文案（成功后清空）
  settingsError: string | null;   // 设置页读失败的用户可读文案（成功后清空；保存失败由页面 toast）
  materials: MaterialsList | null; // GET /api/materials/list?status=open 最近快照（§62 设置页「素材库」）
  materialsError: string | null;  // 素材库读失败的用户可读文案（成功后清空；写失败由 section toast）
  sortOrder: SortOrder;           // 卡片排序偏好（镜像原生 cardSortOrder；localStorage 持久化，cardSort.ts）
  expandedCardIds: ReadonlySet<string>; // 展开详情的卡 id（会话内记忆，不持久化——原生 @State 同义）
  lanes: LaneCatalog | null;      // GET /api/lanes 列说明目录（server-owned 文案，Lane 头「?」气泡读）
  recapSettings: RecapSettings | null; // GET /api/settings/recap（§63：enabled / 语言 / Slack 草稿开关）
  recapMarks: Record<string, RecapMark>; // 「复制」/「标记已发送」的乐观本地回执（等下一次 board 回流覆盖）
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
  boardLoading: true,
  connection: "connecting",
  health: null,
  selectedCardId: null,
  cardDetail: null,
  cardDetailError: null,
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
  expandedCardIds: new Set<string>(),
  lanes: null,
  recapSettings: null,
  recapMarks: {},
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

/** 全量拉取看板（初载 + SSE board.updated 后 + 断线重连后都走这一条） */
export function refreshBoard(): Promise<void> {
  if (boardRequest) return boardRequest;
  boardRequest = (async () => {
    try {
      const board = await fetchBoard();
      setState({ board, boardError: null, boardLoading: false });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setState({ boardError: message, boardLoading: false });
    } finally {
      boardRequest = null;
    }
  })();
  return boardRequest;
}

/** 选中卡片（null = 关抽屉）；选中即拉详情增补 */
export function selectCard(cardId: string | null) {
  setState({ selectedCardId: cardId, cardDetail: null, cardDetailError: null });
  if (!cardId) return;
  void fetchCard(cardId).then(
    (detail) => {
      if (getState().selectedCardId === cardId) setState({ cardDetail: detail });
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

// ----- 看板展示偏好（原生 parity：排序 / 展开详情 / 列说明） -------------------- #

/** 改卡片排序偏好并持久化（localStorage cardSortOrder，原生同名 UserDefaults 键） */
export function setSortOrder(sortOrder: SortOrder) {
  writeSortOrder(sortOrder);
  if (state.sortOrder !== sortOrder) setState({ sortOrder });
}

/** 展开/收起一张卡的详情（会话内记忆：切页/回流不丢，刷新页面即复位） */
export function toggleCardExpanded(cardId: string) {
  const next = new Set(state.expandedCardIds);
  if (next.has(cardId)) next.delete(cardId);
  else next.add(cardId);
  setState({ expandedCardIds: next });
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

// ----- settings（§62 每日整理） -------------------------------------------- #

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

/** 仅测试用：重置 store（vitest 各 case 之间隔离） */
export function resetStoreForTests() {
  state = { ...initialState, sortOrder: readSortOrder(), expandedCardIds: new Set<string>() };
  boardRequest = null;
}
