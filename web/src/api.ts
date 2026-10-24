// fetch 客户端 + 错误分类学。模式 fork 自 dashi web/src/api.ts（Apache-2.0，NOTICE 登记）：
// ApiError{status,code,details} 统一封装 + GET 幂等重试（2 次退避）+ 网络失败合成码。
// error envelope 契约（server/app.py）：{"error":{"code","message","details"}}，
// server 端码：UNKNOWN_FIELD / INVALID_FIELD / NOT_FOUND / CONFLICT / NOT_IMPLEMENTED / INTERNAL_ERROR；
// 客户端合成码：READ_FAILED（读失败，UI 静默重试）/ SERVICE_UNAVAILABLE（写失败，UI 明确报错）。
// 本模块不 import React——文案经 setApiText 注入（app.tsx 接线），vitest node 环境可直测。
// v2.0.0：只导出 server/app.py 路由表里**还在**的路由（CONTRACT §49 路由集）；看板 / 卡片 / inbox 动作 / 附件 /
// 技能 / MCP / Slack / 同步 / 语气档案 / 雷达 / 遥测 / 终端 / 修复等路由随 Act 管线退役，全文见 tag full。
import type {
  AboutInfo,
  DiagnosticsSnapshot,
  DisplaySettings,
  DisplaySettingsPatch,
  DoctorReport,
  FailureCatalog,
  FolderReceipt,
  HealthSnapshot,
  IngestJob,
  IngestJobStart,
  LogTail,
  ModelsSettings,
  PermissionsSnapshot,
  RecapGenerateReceipt,
  RecapHistory,
  RecapMarkKind,
  RecapMarkReceipt,
  RecapRevertReceipt,
  RecapSettings,
  RecapsSnapshot,
  ScreenpipeDisk,
  SecretStatus,
  SecretVerifyResult,
  SecretsStatus,
  SettingsCatalog,
  SettingsSection,
  SetupEngine,
  SetupReceipt,
  SetupSnapshot,
  SetupVaults,
  UpdateCheckResult,
  UpdateInstallReceipt,
} from "./types";

interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

let apiText = (_chinese: string, english: string) => english;

export function setApiText(text: typeof apiText) {
  apiText = text;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? apiText(`请求失败（${status}）`, `Request failed (${status})`));
    this.name = "ApiError";
    this.status = status;
    this.code = body.error?.code ?? "REQUEST_FAILED";
    this.details = body.error?.details;
  }
}

/** 相对 document.baseURI 解析（build 产物 base:"./"，由 server 静态服务时仍然正确） */
export function resolveApiUrl(path: string): string {
  return new URL(path.replace(/^\//, ""), document.baseURI).href;
}

/**
 * per-install instance token（CONTRACT §49 auth model）：server 把
 * window.__ZAI_TOKEN__ 注入它服务的 index.html，一切写请求必须回带
 * X-Zai-Token 头。vite dev server 不注入（写动作会被 server 401）——
 * 带写路径的开发面走 scripts/dev-preview.sh 服务的 dist。
 */
const TOKEN_HEADER = "X-Zai-Token";

function instanceToken(): string | null {
  const token = (window as Window & { __ZAI_TOKEN__?: unknown }).__ZAI_TOKEN__;
  return typeof token === "string" && token ? token : null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const method = (init?.method ?? "GET").toUpperCase();
  const readRequest = method === "GET" || method === "HEAD";
  if (!readRequest) {
    // 写请求带 instance token（读路径 token-light，头也不发——§49）
    const token = instanceToken();
    if (token && !headers.has(TOKEN_HEADER)) headers.set(TOKEN_HEADER, token);
  }

  let response: Response;
  for (let attempt = 0; ; attempt += 1) {
    try {
      response = await fetch(resolveApiUrl(path), { ...init, headers });
      break;
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") throw error;
      // 只重试幂等读——写动作绝不自动重发（生成 / 回退会派生子进程，不做客户端幂等假设）
      if (readRequest && attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
        continue;
      }
      const failure = error instanceof Error && error.name === "TimeoutError"
        ? "timeout"
        : error instanceof TypeError
          ? "browser-network"
          : "network";
      throw new ApiError(0, {
        error: {
          code: readRequest ? "READ_FAILED" : "SERVICE_UNAVAILABLE",
          message: readRequest
            ? apiText(
                "暂时读不到本地服务的数据，会自动重试。",
                "Local data is temporarily unavailable. Retrying automatically.",
              )
            : apiText(
                "暂时连不上本地服务，请稍后重试。",
                "The local service is temporarily unavailable. Try again later.",
              ),
          details: { method, failure },
        },
      });
    }
  }

  let body: T & ApiErrorBody;
  try {
    body = (await response.json()) as T & ApiErrorBody;
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    if (response.ok) {
      // 2xx 却解不出 JSON（代理页、写了一半的响应……）：不许当 `{}` 放行——空对象一路进 store 会让页面渲染崩。
      // 按读失败上报，调用方留旧快照；status 带真值（2xx），store 据此与断网（status 0）分开
      throw new ApiError(response.status, {
        error: {
          code: "READ_FAILED",
          message: apiText(
            `服务端响应不是合法 JSON（${response.status}）`,
            `The server response is not valid JSON (${response.status})`,
          ),
          details: { method, failure: "invalid-json" },
        },
      });
    }
    body = {} as T & ApiErrorBody; // 非 2xx 的非 JSON 体：ApiError 用通用文案（REQUEST_FAILED）
  }
  if (!response.ok) throw new ApiError(response.status, body);
  return body;
}

const EMPTY_BODY = JSON.stringify({});

// ----- 活性 / 失败目录 ------------------------------------------------------------------------------------------- #

/** GET /api/health — 活性（server 在答 + cron ingest 链探针新鲜度 + 上次 recap 轮次 → verdict，§49） */
export function fetchHealth(signal?: AbortSignal): Promise<HealthSnapshot> {
  return request<HealthSnapshot>("/api/health", { signal });
}

/** GET /api/failures：§25 失败目录的 server-owned 双语句（依赖行 / 引擎诊断行按 failure id 取） */
export function fetchFailures(signal?: AbortSignal): Promise<FailureCatalog> {
  return request<FailureCatalog>("/api/failures", { signal });
}

// ----- 会议纪要（§63） -------------------------------------------------------------------------------------------- #

/** GET /api/recaps — §63.5 投影（两份预算切过的行 + 三栏真实总数；server 现读 state/recap/，SSE recaps.updated 后重拉） */
export function fetchRecaps(signal?: AbortSignal): Promise<RecapsSnapshot> {
  return request<RecapsSnapshot>("/api/recaps", { signal });
}

/** POST /api/recaps/generate 的 body（§63.8 / §63.10 / §63.11）：server 字段闸零容忍——多一个键 400 UNKNOWN_FIELD */
export interface RecapGenerateBody {
  key: string;
  note?: string;
  partial?: true;
  shape?: string;
  answers?: string[];
}

/** POST /api/recaps/generate — 「重新生成 / 现在生成」：server 派生 `python -m act.recap --generate`（单写者仍是 act.recap，§63.6）
 *  并把这一次请求记进 §63.8 台账；回执带 generate_request（行上「生成中」的真源） */
export function postRecapGenerate(body: RecapGenerateBody): Promise<RecapGenerateReceipt> {
  return request<RecapGenerateReceipt>("/api/recaps/generate", { method: "POST", body: JSON.stringify(body) });
}

/** POST /api/recaps/revert {key, version} — §63.9「回退到这一版」：server 派生 `act.recap --revert`（非破坏：当前正文先进 history） */
export function postRecapRevert(key: string, version: number): Promise<RecapRevertReceipt> {
  return request<RecapRevertReceipt>("/api/recaps/revert", { method: "POST", body: JSON.stringify({ key, version }) });
}

/**
 * POST /api/recaps/mark {key, mark, on} — §63 本地标记（marks.json，server 单写者）：
 * copied（复制）/ sent（标记已发送 = 归档）/ dismissed（忽略，§63.5 追记 issue #301）；on:false = 撤销。
 */
export function postRecapMark(key: string, mark: RecapMarkKind, on = true): Promise<RecapMarkReceipt> {
  return request<RecapMarkReceipt>("/api/recaps/mark", {
    method: "POST",
    body: JSON.stringify({ key, mark, on }),
  });
}

/** GET /api/recaps/history?key=… — §63.9 单份纪要存着的每一版 + 正文（只读；正文只在这条路上） */
export function fetchRecapHistory(key: string, signal?: AbortSignal): Promise<RecapHistory> {
  return request<RecapHistory>(`/api/recaps/history?key=${encodeURIComponent(key)}`, { signal });
}

/** GET /api/settings/recap — 两把旋钮（enabled / default_language）+ 只读的出厂形状（§63.10） */
export function fetchRecapSettings(signal?: AbortSignal): Promise<RecapSettings> {
  return request<RecapSettings>("/api/settings/recap", { signal });
}

/** PUT /api/settings/recap — 只认 enabled / default_language（server diff-write，§15） */
export function putRecapSettings(body: { enabled?: boolean; default_language?: string }): Promise<RecapSettings> {
  return request<RecapSettings>("/api/settings/recap", { method: "PUT", body: JSON.stringify(body) });
}

// ----- 设置（§15 / §59 / §68.1） --------------------------------------------------------------------------------- #

/** GET /api/settings/models — pipeline / fallback 两把模型旋钮的 effective 值 + canonical 下拉全集（§59） */
export function fetchModelsSettings(signal?: AbortSignal): Promise<ModelsSettings> {
  return request<ModelsSettings>("/api/settings/models", { signal });
}

/** PUT /api/settings/models 的 body：pipeline = 模型 id；fallback = "off" 或模型 id */
export type ModelsPatch = { pipeline?: string; fallback?: string };

/** PUT /api/settings/models — 保存旋钮（写请求：四闸同 POST，自动带 token）；body 只许 pipeline / fallback 两键 */
export function putModelsSettings(body: ModelsPatch): Promise<ModelsSettings> {
  return request<ModelsSettings>("/api/settings/models", { method: "PUT", body: JSON.stringify(body) });
}

/** GET /api/settings/display — 字号 / 字重 / 描边三把显示旋钮（§54.1 第 12 项） */
export function fetchDisplaySettings(signal?: AbortSignal): Promise<DisplaySettings> {
  return request<DisplaySettings>("/api/settings/display", { signal });
}

/** PUT /api/settings/display — 三键任意子集（server 零容忍多余字段） */
export function putDisplaySettings(body: DisplaySettingsPatch): Promise<DisplaySettings> {
  return request<DisplaySettings>("/api/settings/display", { method: "PUT", body: JSON.stringify(body) });
}

/** GET /api/settings — 通用设置目录（server/settings_catalog.py 的全部 section） */
export function fetchSettingsCatalog(signal?: AbortSignal): Promise<SettingsCatalog> {
  return request<SettingsCatalog>("/api/settings", { signal });
}

/** GET /api/settings/{section} — 单个 section 的快照 */
export function fetchSettingsSection(section: string, signal?: AbortSignal): Promise<SettingsSection> {
  return request<SettingsSection>(`/api/settings/${encodeURIComponent(section)}`, { signal });
}

/** PUT /api/settings/{section} — 保存一个 section 的字段子集（server 校验 + diff-write 进 settings_overrides.json） */
export function putSettingsSection(section: string, body: Record<string, unknown>): Promise<SettingsSection> {
  return request<SettingsSection>(`/api/settings/${encodeURIComponent(section)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** GET /api/secrets — 凭证状态（§19：v2.0.0 只剩 Anthropic API key；永无值） */
export function fetchSecrets(signal?: AbortSignal): Promise<SecretsStatus> {
  return request<SecretsStatus>("/api/secrets", { signal });
}

/** PUT /api/secrets/{name} — 写凭证（空值 = 删）；回执只有状态 */
export function putSecret(name: string, value: string): Promise<SecretStatus> {
  return request<SecretStatus>(`/api/secrets/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

/** POST /api/secrets/{name}/verify — 最小活探针（server 侧）。带 value = 粘贴即验证（§68.3；只探这个值、不落盘——向导「先验后存」用） */
export function verifySecret(name: string, value?: string): Promise<SecretVerifyResult> {
  return request<SecretVerifyResult>(`/api/secrets/${encodeURIComponent(name)}/verify`, {
    method: "POST",
    body: JSON.stringify(value === undefined ? {} : { value }),
  });
}

/** POST /api/folders/open {key} — 目录字段「打开」：server 从已保存的设置读路径，访达打开（§68.1） */
export function postFolderOpen(key: string): Promise<FolderReceipt> {
  return request<FolderReceipt>("/api/folders/open", { method: "POST", body: JSON.stringify({ key }) });
}

/** POST /api/folders/create {key} — 目录字段「创建」：server 建目录（§68.1） */
export function postFolderCreate(key: string): Promise<FolderReceipt> {
  return request<FolderReceipt>("/api/folders/create", { method: "POST", body: JSON.stringify({ key }) });
}

/** POST /api/reveal 的词表（server/folders.REVEAL_TARGETS 逐字）：config = config.yaml（缺席则模板）；voice_profile = recap 读的语气档案 */
export type RevealTarget = "config" | "voice_profile";
export type RevealMode = "reveal" | "open";

/** POST /api/reveal {target, mode?} — 访达定位 / 默认编辑器打开；路径永远由 server 推导，客户端只点名词表项（非 darwin 501） */
export function postRevealTarget(target: RevealTarget, mode?: RevealMode): Promise<unknown> {
  const body: Record<string, string> = { target };
  if (mode) body.mode = mode;
  return request("/api/reveal", { method: "POST", body: JSON.stringify(body) });
}

// ----- 权限体检 / 依赖检查 / 日志（§25 / §68.3 / §68.4） --------------------------------------------------------- #

/** GET /api/permissions — 权限体检快照（refresh = 重跑 doctor 的权限行） */
export function fetchPermissions(refresh = false, signal?: AbortSignal): Promise<PermissionsSnapshot> {
  return request<PermissionsSnapshot>(`/api/permissions${refresh ? "?refresh=1" : ""}`, { signal });
}

/** GET /api/diagnostics — doctor（缓存）+ 活性 + install_report + 日志清单 + cron 探针 + 最近活动；lang = doctor 人话的语言 */
export function fetchDiagnostics(refresh = false, lang?: "zh" | "en", signal?: AbortSignal): Promise<DiagnosticsSnapshot> {
  const params = new URLSearchParams();
  if (refresh) params.set("refresh", "1");
  if (lang) params.set("lang", lang);
  const query = params.toString();
  return request<DiagnosticsSnapshot>(`/api/diagnostics${query ? `?${query}` : ""}`, { signal });
}

/** GET /api/doctor — 完整 doctor（fast=false 含活探针，会花 token）；lang 同上 */
export function fetchDoctor(fast = true, refresh = false, lang?: "zh" | "en", signal?: AbortSignal): Promise<DoctorReport> {
  const params = new URLSearchParams();
  if (!fast) params.set("fast", "0");
  if (refresh) params.set("refresh", "1");
  if (lang) params.set("lang", lang);
  const query = params.toString();
  return request<DoctorReport>(`/api/doctor${query ? `?${query}` : ""}`, { signal });
}

/** GET /api/logs/{name}?lines=N — 日志尾巴（只读、size-cap；name 必须在诊断快照的清单里） */
export function fetchLogTail(name: string, lines = 200, signal?: AbortSignal): Promise<LogTail> {
  return request<LogTail>(`/api/logs/${encodeURIComponent(name)}?lines=${lines}`, { signal });
}

// ----- 首次运行向导（§68.5） ------------------------------------------------------------------------------------- #

/** GET /api/setup — 向导判定（config.yaml / Anthropic key / 完成标记） */
export function fetchSetup(signal?: AbortSignal): Promise<SetupSnapshot> {
  return request<SetupSnapshot>("/api/setup", { signal });
}

/** POST /api/setup/{config-from-example|complete|reset} — 复制模板（已存在 409，绝不覆盖）/ 写完成标记 / 删标记 */
export function postSetupStep(step: "config-from-example" | "complete" | "reset"): Promise<SetupReceipt> {
  return request<SetupReceipt>(`/api/setup/${step}`, { method: "POST", body: EMPTY_BODY });
}

/** GET /api/setup/engine — claude CLI 路径 / 版本 / API key 梯子（§78：headless 只认 API key） */
export function fetchSetupEngine(signal?: AbortSignal): Promise<SetupEngine> {
  return request<SetupEngine>("/api/setup/engine", { signal });
}

/** GET /api/setup/vaults — Obsidian 自己登记过的库（只读；没装 Obsidian = 空表） */
export function fetchSetupVaults(signal?: AbortSignal): Promise<SetupVaults> {
  return request<SetupVaults>("/api/setup/vaults", { signal });
}

// ----- 关于 / 更新（§26 / §79） ---------------------------------------------------------------------------------- #

/** GET /api/about — 版本 / 路径 / 更新缓存（update_available 由 server 现算） */
export function fetchAbout(signal?: AbortSignal): Promise<AboutInfo> {
  return request<AboutInfo>("/api/about", { signal });
}

/** POST /api/update/check — §26「立即检查」（只问不装；检查关着时 server 侧自己拒发网络请求） */
export function postUpdateCheck(): Promise<UpdateCheckResult> {
  return request<UpdateCheckResult>("/api/update/check", { method: "POST", body: EMPTY_BODY });
}

/** POST /api/update/install — §79「立即升级」：分离起 scripts/upgrade.sh；跨大版本 409 manual_upgrade、
 *  这个 checkout 还没有升级器 501（两者 details.fix = 手动命令） */
export function postUpdateInstall(): Promise<UpdateInstallReceipt> {
  return request<UpdateInstallReceipt>("/api/update/install", { method: "POST", body: EMPTY_BODY });
}

// ----- 录制与导入（§18 / §72） ----------------------------------------------------------------------------------- #

/** POST /api/ingest/export — 现在导出一次（ingest/screenpipe-export.sh，server 后台线程跑） */
export function postIngestExport(): Promise<IngestJobStart> {
  return request<IngestJobStart>("/api/ingest/export", { method: "POST", body: EMPTY_BODY });
}

/** POST /api/ingest/run — 现在整理一次（ingest/process-screenpipe.sh；持锁 = skipped） */
export function postIngestRun(): Promise<IngestJobStart> {
  return request<IngestJobStart>("/api/ingest/run", { method: "POST", body: EMPTY_BODY });
}

/** GET /api/ingest/jobs/{id} — 手动 ingest 任务的状态（running → done + 回执） */
export function fetchIngestJob(id: string, signal?: AbortSignal): Promise<IngestJob> {
  return request<IngestJob>(`/api/ingest/jobs/${encodeURIComponent(id)}`, { signal });
}

/** GET /api/screenpipe/disk — 录制数据占用 + 增长 + 两道保留期回执（§72；refresh = 重新 du） */
export function fetchScreenpipeDisk(refresh = false, signal?: AbortSignal): Promise<ScreenpipeDisk> {
  return request<ScreenpipeDisk>(refresh ? "/api/screenpipe/disk?refresh=1" : "/api/screenpipe/disk", { signal });
}
