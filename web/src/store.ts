// 全局唯一 state 源：手写 useSyncExternalStore 小店（禁止 App 巨石，禁状态库）。
// 约定（全体组件遵守）：
//   1. 组件读 state 只经 useAppState()（整快照，emit 时整体替换，浅比较即变更检测）；
//   2. 改 state 只经本文件导出的 action 函数（refreshRecaps / markRecap / ...），组件不直接 setState；
//   3. 新增 UI state = 给 AppState 加字段 + 加 action 函数，别处不许存全局态；
//   4. 服务端数据原样进 store，前端绝不改写 wire 字段。
// v2.0.0：数据源 = 会议纪要（GET /api/recaps，SSE recaps.updated 后重拉）+ 设置 / 权限 / 诊断 / 向导 / 关于各自的快照；
// 看板（dashboard.json）、卡片详情、过滤器、会话索引、多选、强制合并章、书立条与 Act 管线的设置区随 Act 管线退役，全文见 tag full。
import { useSyncExternalStore } from "react";
import {
  ApiError,
  fetchAbout,
  fetchDiagnostics,
  fetchDisplaySettings,
  fetchFailures,
  fetchHealth,
  fetchModelsSettings,
  fetchPermissions,
  fetchRecapSettings,
  fetchRecaps,
  fetchSecrets,
  fetchSettingsCatalog,
  fetchSettingsSection,
  fetchSetup,
  postRecapMark,
  putDisplaySettings,
  putModelsSettings,
  putRecapSettings,
  putSettingsSection,
  type ModelsPatch,
} from "./api";
import { applyDisplayPrefs, prefsOf } from "./displayPrefs";
import { resolveLanguage, type Language } from "./i18n";
import { navigate } from "./route";
import { readExpandedSections, toggledSections, writeExpandedSections } from "./settingsFolds";
import type {
  AboutInfo,
  DiagnosticsSnapshot,
  DisplaySettings,
  DisplaySettingsPatch,
  FailureCatalog,
  HealthSnapshot,
  ModelsSettings,
  PermissionsSnapshot,
  RecapLaneTotals,
  RecapMarkKind,
  RecapRow,
  RecapSettings,
  SecretsStatus,
  SettingsCatalog,
  SettingsField,
  SettingsSection,
  SetupSnapshot,
} from "./types";

export type ConnectionState = "connecting" | "live" | "reconnecting";

export interface AppState {
  /** GET /api/recaps 的行（§63.5 两份预算切过的投影）；null = 还没拉到过 */
  recaps: RecapRow[] | null;
  /** 三栏的真实总数（§63.5 追记 issue #301）；老 server 缺席 = null */
  recapCounts: RecapLaneTotals | null;
  recapsError: string | null;      // 最近一次 /api/recaps 读失败的用户可读文案（成功后清空；旧行留着）
  recapsLoading: boolean;          // 首载 true；SSE 触发的静默 refetch 不置位
  connection: ConnectionState;
  health: HealthSnapshot | null;   // GET /api/health 最近快照（§49）
  language: Language;              // UI 语言（D37 §15：真源 = server general.language；首帧 ?lang= 覆写 > localStorage 缓存 > 浏览器，hydrateLanguage 随后对齐）
  models: ModelsSettings | null;   // GET /api/settings/models 最近快照（§59 设置页「模型」）
  settingsError: string | null;    // 设置页读失败的用户可读文案（成功后清空；保存失败由页面 toast）
  expandedSettingsSections: ReadonlySet<string>; // 设置页展开着的区（D44；localStorage 持久化，settingsFolds.ts）
  recapSettings: RecapSettings | null; // GET /api/settings/recap（§63：enabled / 语言 / 出厂形状）
  recapMarks: Record<string, RecapMark>; // 「复制」/「标记已发送」/「忽略」的乐观本地回执（等下一次 /api/recaps 回流覆盖）
  recapPending: Record<string, RecapPending>; // 「重新生成 / 现在生成」按下后到回执（generate_request）落地前的乐观「排队中」（§63.8）
  displaySettings: DisplaySettings | null; // GET /api/settings/display（§54.1 第 12 项：字号 / 字重 / 描边；到达即落 <html> data-*）

  // ----- 各页的 server 快照（每页自己 refresh；读失败落 pageErrors[key]） -----
  settingsCatalog: SettingsCatalog | null; // GET /api/settings（通用 section 目录）
  secrets: SecretsStatus | null;           // GET /api/secrets（只有状态）
  permissions: PermissionsSnapshot | null; // GET /api/permissions
  diagnostics: DiagnosticsSnapshot | null; // GET /api/diagnostics
  setup: SetupSnapshot | null;             // GET /api/setup（首次运行向导判定）
  about: AboutInfo | null;                 // GET /api/about
  failures: FailureCatalog | null;         // GET /api/failures（§25 失败目录双语句；引擎诊断行 / 依赖行按 id 取）
  pageErrors: Record<string, string | null>; // 上述各面最近一次读失败的文案（成功后清空）
}

/** §63 本地标记（server marks.json 的镜像片段；dismissed_at = §63.5 追记 issue #301） */
export interface RecapMark {
  copied_at?: string | null;
  sent_at?: string | null;
  dismissed_at?: string | null;
}

/** §63.8 乐观「排队中」：按下时看到的版本号与回执 requested_at（新版本或新回执落地即结束）+ 按下时刻（10 分钟兜底） */
export interface RecapPending {
  version: number;
  requested_at: string | null;
  at: number;
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
  recaps: null,
  recapCounts: null,
  recapsError: null,
  recapsLoading: true,
  connection: "connecting",
  health: null,
  language: detectInitialLanguage(),
  models: null,
  settingsError: null,
  expandedSettingsSections: readExpandedSections(),
  recapSettings: null,
  recapMarks: {},
  recapPending: {},
  displaySettings: null,
  settingsCatalog: null,
  secrets: null,
  permissions: null,
  diagnostics: null,
  setup: null,
  about: null,
  failures: null,
  pageErrors: {},
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

const messageOf = (error: unknown): string => (error instanceof ApiError ? error.message : String(error));

// ----- 会议纪要列表（§63.5） ------------------------------------------------------------------------------------- #

let recapsRequest: Promise<void> | null = null; // 并发 refetch 合并成一个在途请求

/** 全量拉取纪要列表（初载 + SSE recaps.updated 后 + 断线重连后 + 生成中的 5 s 补拉都走这一条）。
 *  读失败留着上一份行（它可能仍在说真话），只记一句错误；server 答了但形状不对（不是带 recaps 数组的对象）当读失败 */
export function refreshRecaps(): Promise<void> {
  if (recapsRequest) return recapsRequest;
  recapsRequest = (async () => {
    try {
      const snapshot = await fetchRecaps();
      if (!snapshot || typeof snapshot !== "object" || !Array.isArray(snapshot.recaps)) {
        throw new Error("GET /api/recaps: recaps is not a list");
      }
      const counts = snapshot.recap_counts;
      setState({
        recaps: snapshot.recaps,
        recapCounts: counts && typeof counts === "object" && !Array.isArray(counts) ? counts : null,
        recapsError: null,
        recapsLoading: false,
      });
    } catch (error) {
      setState({ recapsError: messageOf(error), recapsLoading: false });
    } finally {
      recapsRequest = null;
    }
  })();
  return recapsRequest;
}

export function setConnection(connection: ConnectionState) {
  if (state.connection !== connection) setState({ connection });
}

/** 拉一次 /api/health（§49）。读失败保留上一份快照——离线由顶栏的连接状态声明，这里不双报 */
export async function refreshHealth(): Promise<void> {
  try {
    const health = await fetchHealth();
    setState({ health });
  } catch {
    /* server 连不上：旧快照留着 */
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
 *  决定——best-effort */
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
 *  就留给下一次启动）。`?lang=` 在场 = 一次性覆写，两件事都不做；读失败 = 留着首帧的提示（离线由顶栏的连接状态声明）；水合期间用户
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

// ----- settings（§59 设置页「模型」） ---------------------------------------------------------------------------- #

/** 拉设置页「模型」的快照（pipeline / fallback 两把旋钮）；读失败落 settingsError */
export async function refreshSettings(): Promise<void> {
  try {
    const models = await fetchModelsSettings();
    setState({ models, settingsError: null });
  } catch (error) {
    setState({ settingsError: messageOf(error) });
  }
}

/** 保存旋钮（PUT，server 校验 + diff-write）；成功以 server 回执替换快照，失败原样抛给页面 toast */
export async function saveModels(patch: ModelsPatch): Promise<ModelsSettings> {
  const models = await putModelsSettings(patch);
  setState({ models });
  return models;
}

// ----- 会议纪要旋钮与本地标记（§63） ----------------------------------------------------------------------------- #

/** 拉 recap 旋钮（页面与设置 section 共用）；读失败落 settingsError */
export async function refreshRecapSettings(): Promise<void> {
  try {
    const recapSettings = await fetchRecapSettings();
    setState({ recapSettings, settingsError: null });
  } catch (error) {
    setState({ settingsError: messageOf(error) });
  }
}

/** 保存 recap 旋钮（PUT，server diff-write；只认 enabled / default_language）；成功以 server 回执替换快照 */
export async function saveRecapSettings(patch: { enabled?: boolean; default_language?: string }): Promise<RecapSettings> {
  const recapSettings = await putRecapSettings(patch);
  setState({ recapSettings });
  return recapSettings;
}

/**
 * 「复制」/「标记已发送」/「忽略」：POST 本地标记并乐观记住回执（/api/recaps 回流时以 server 投影为准）。
 * §63.5 追记（issue #301）：sent_at → 已归档、dismissed_at → 已忽略，两者都靠 `on: false` 恢复。
 */
export async function markRecap(key: string, mark: RecapMarkKind, on = true): Promise<void> {
  const receipt = await postRecapMark(key, mark, on);
  setState({ recapMarks: { ...state.recapMarks, [key]: {
    copied_at: receipt.copied_at, sent_at: receipt.sent_at, dismissed_at: receipt.dismissed_at ?? null } } });
}

/** 「重新生成 / 现在生成」POST 成功后：记下按下时看到的版本与回执，行随即显示「生成中」（§63.8；回流带新版本 / 新回执即结束） */
export function markRecapPending(row: RecapRow, now = Date.now()) {
  const pending: RecapPending = { version: row.version ?? 0, requested_at: row.generate_request?.requested_at ?? null, at: now };
  setState({ recapPending: { ...state.recapPending, [row.key]: pending } });
}

/** 乐观「排队中」结束（新版本落地 / server 回执接管 / 兜底超时）：把这一键从本地表里拿掉 */
export function clearRecapPending(key: string) {
  if (!(key in state.recapPending)) return;
  const { [key]: _gone, ...rest } = state.recapPending;
  setState({ recapPending: rest });
}

// ----- 显示偏好（§54.1 第 12 项） ------------------------------------------- #

/** 拉三把显示旋钮并立刻落到 <html>（App 启动一次 + 设置 section 挂载）；读失败落 settingsError、页面保持首帧缓存的值 */
export async function refreshDisplaySettings(): Promise<void> {
  try {
    const displaySettings = await fetchDisplaySettings();
    applyDisplayPrefs(prefsOf(displaySettings));
    setState({ displaySettings, settingsError: null });
  } catch (error) {
    setState({ settingsError: messageOf(error) });
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

// ----- 各页快照（一个通用 loader：成功落字段、失败落 pageErrors[key]） ----------------------------------------- #

type PageKey = "settingsCatalog" | "secrets" | "permissions" | "diagnostics" | "setup" | "about" | "failures";

const pageRequests = new Map<PageKey, Promise<void>>(); // 同一面并发 refresh 合并成一个在途请求（几个通用区同时挂载）

function loadPage<K extends PageKey>(key: K, fetcher: () => Promise<AppState[K]>): Promise<void> {
  const inflight = pageRequests.get(key);
  if (inflight) return inflight;
  const request = (async () => {
    try {
      const data = await fetcher();
      setState({ [key]: data, pageErrors: { ...state.pageErrors, [key]: null } } as Partial<AppState>);
    } catch (error) {
      setState({ pageErrors: { ...state.pageErrors, [key]: messageOf(error) } });
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
// lang = store 的当前 UI 语言：doctor 子进程的人话随之（§68.4 追记）。doctor 要跑几秒：在途请求带的若是另一种语言
// （正跑着切了语言），loadPage 的在途合并会把这次切换吞掉——等它落地再按当前语言补拉一次，旧语言的行不许留着。
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

/** 保存一个通用 section（PUT，server 校验 + diff-write）；成功以回执替换目录里的该 section，失败原样抛给页面 toast */
export async function saveSettingsSection(sectionId: string, patch: Record<string, unknown>): Promise<SettingsSection> {
  // D37（§15 追记）：patch 带 language = 拨了「界面语言」这把开关（顶栏 / 向导经 chooseLanguage，设置区「保存」与首启持久化
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
    // 落键成功 = 同一把开关拨了——UI 立刻切，不等下次启动；写的期间用户又选了别的 → 后选的赢，这份回执不拨回去
    if (!superseded) setLanguage(chosen);
    // 目录 GET 正在路上（与设置页同时挂载 / 首启持久化）时带回的还是写之前的快照——等它落地再补拉一次，「界面语言」那格的
    // 来源章才不说谎；被超过的回执没替目录，目录在手就补拉一次对齐 server 现状
    const inflight = pageRequests.get("settingsCatalog");
    if (inflight) void inflight.then(() => refreshSettingsCatalog());
    else if (superseded && catalog) void refreshSettingsCatalog();
  }
  return section;
}

/** 外部（向导 / 凭证保存）改了 setup 判定后直接落新快照 */
export function setSetup(setup: SetupSnapshot) {
  setState({ setup });
}

/** 仅测试用：重置 store（vitest 各 case 之间隔离） */
export function resetStoreForTests() {
  state = {
    ...initialState,
    expandedSettingsSections: readExpandedSections(),
    recapMarks: {},
    recapPending: {},
    pageErrors: {},
  };
  recapsRequest = null;
  pageRequests.clear();
  diagnosticsLang = null;
  languageChoiceSeq = 0;
}
