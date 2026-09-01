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
  fetchHealth,
  fetchModelsSettings,
  postClaudeCodeDefault,
  putModelsSettings,
} from "./api";
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
  HealthSnapshot,
  ModelsSettings,
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
  models: ModelsSettings | null;  // GET /api/settings/models 最近快照（§57 设置页「模型」）
  claudeCodeDefault: ClaudeCodeDefault | null; // GET /api/claude-code/default-model（follow 继承的全局默认）
  settingsError: string | null;   // 设置页读失败的用户可读文案（成功后清空；保存失败由页面 toast）
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
  settingsError: null,
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

// ----- settings（§57 设置页） ---------------------------------------------- #

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

/** 仅测试用：重置 store（vitest 各 case 之间隔离） */
export function resetStoreForTests() {
  state = initialState;
  boardRequest = null;
}
