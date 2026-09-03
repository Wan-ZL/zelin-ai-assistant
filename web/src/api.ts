// fetch 客户端 + 错误分类学。模式 fork 自 dashi web/src/api.ts（Apache-2.0，NOTICE 登记）：
// ApiError{status,code,details} 统一封装 + GET 幂等重试（2 次退避）+ 网络失败合成码。
// error envelope 契约（server/app.py）：{"error":{"code","message","details"}}，
// server 端码：UNKNOWN_FIELD / INVALID_FIELD / NOT_FOUND / INTERNAL_ERROR；
// 客户端合成码：READ_FAILED（读失败，UI 静默重试）/ SERVICE_UNAVAILABLE（写失败，UI 明确报错）。
// 本模块不 import React——文案经 setApiText 注入（app.tsx 接线），vitest node 环境可直测。
import type {
  AiFixReceipt,
  Board,
  CardDetail,
  ClaudeCodeDefault,
  ClaudeCodeDefaultWrite,
  HealthSnapshot,
  LaneCatalog,
  MaterialItem,
  MaterialsList,
  ModelsSettings,
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
      // 只重试幂等读——写动作绝不自动重发（inbox 动作不做客户端幂等假设）
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
                "暂时读不到看板数据，会自动重试。",
                "Board data is temporarily unavailable. Retrying automatically.",
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
    body = {} as T & ApiErrorBody;
  }
  if (!response.ok) throw new ApiError(response.status, body);
  return body;
}

/** GET /api/board — dashboard.json 原样透传 */
export function fetchBoard(signal?: AbortSignal): Promise<Board> {
  return request<Board>("/api/board", { signal });
}

/** GET /api/cards/{id} — 投影 + registry 详情增补 */
export function fetchCard(id: string, signal?: AbortSignal): Promise<CardDetail> {
  return request<CardDetail>(`/api/cards/${encodeURIComponent(id)}`, { signal });
}

/** GET /api/health — 管线活性（心跳年龄 / 看板新鲜度 / 连崩计数 → verdict，§47.4） */
export function fetchHealth(signal?: AbortSignal): Promise<HealthSnapshot> {
  return request<HealthSnapshot>("/api/health", { signal });
}

/**
 * POST /api/actions — 写 inbox 动作。body 形状 = live CONTRACT §3 现有动词清单，
 * 由动作发起组件逐字段构造；本函数不校验、不补字段（多一个字段 server 会 400 UNKNOWN_FIELD）。
 */
export function postAction(body: Record<string, unknown>): Promise<unknown> {
  return request<unknown>("/api/actions", { method: "POST", body: JSON.stringify(body) });
}

/** POST /api/reveal — 访达定位交付物（路径由 server 从卡片记录推导，客户端只传 card_id） */
export function postReveal(cardId: string): Promise<unknown> {
  return request<unknown>("/api/reveal", {
    method: "POST",
    body: JSON.stringify({ card_id: cardId }),
  });
}

/** GET /api/lanes — 列说明文案目录（server-owned，§54；静态内容，进程内拉一次即可） */
export function fetchLanes(signal?: AbortSignal): Promise<LaneCatalog> {
  return request<LaneCatalog>("/api/lanes", { signal });
}

/**
 * POST /api/ai-fix — 「让 AI 修」：server 从投影行推导错误上下文并起
 * act.ai_fix 的 Terminal 修复会话（§54）。客户端只传 card_id + UI 语言，
 * 绝不传错误文本（server 端不接受）。非 darwin / config 关闭 → 501。
 */
export function postAiFix(cardId: string, lang: "zh" | "en"): Promise<AiFixReceipt> {
  return request<AiFixReceipt>("/api/ai-fix", {
    method: "POST",
    body: JSON.stringify({ card_id: cardId, lang }),
  });
}

/** 交付物静态 URL（iframe/链接用；server 端做路径推导与穿越校验） */
export function deliverableUrl(cardId: string, name: string): string {
  return resolveApiUrl(`/files/deliverables/${encodeURIComponent(cardId)}/${encodeURIComponent(name)}`);
}

/** GET /api/settings/models — 两把模型旋钮的 effective 值 + canonical 下拉全集（CONTRACT §59） */
export function fetchModelsSettings(signal?: AbortSignal): Promise<ModelsSettings> {
  return request<ModelsSettings>("/api/settings/models", { signal });
}

/**
 * PUT /api/settings/models — 保存旋钮（写请求：四闸同 POST，api.ts 自动带 token）。
 * body 只许 dispatch / pipeline 两键（server UNKNOWN_FIELD 零容忍）；值 = "follow" 或模型 id。
 */
export function putModelsSettings(body: { dispatch?: string; pipeline?: string }): Promise<ModelsSettings> {
  return request<ModelsSettings>("/api/settings/models", { method: "PUT", body: JSON.stringify(body) });
}

/** GET /api/claude-code/default-model — follow 模式继承的 Claude Code 全局默认 */
export function fetchClaudeCodeDefault(signal?: AbortSignal): Promise<ClaudeCodeDefault> {
  return request<ClaudeCodeDefault>("/api/claude-code/default-model", { signal });
}

/** POST /api/claude-code/default-model — owner 显式一键「设为 <id>」：server 只改 model 键、先备份 */
export function postClaudeCodeDefault(model: string): Promise<ClaudeCodeDefaultWrite> {
  return request<ClaudeCodeDefaultWrite>("/api/claude-code/default-model", {
    method: "POST",
    body: JSON.stringify({ model }),
  });
}

/** GET /api/materials/list?status= — 素材库（CONTRACT §62）；open = 尚未开 PR / 完成 / 放弃的条目（弹窗） */
export function fetchMaterials(status: "open" | "all" = "open", signal?: AbortSignal): Promise<MaterialsList> {
  return request<MaterialsList>(`/api/materials/list?status=${encodeURIComponent(status)}`, { signal });
}

/** POST /api/materials/add — 扔一条链接 + 备注进素材库（body 只许 url / note 两键，server UNKNOWN_FIELD 零容忍） */
export function postMaterialAdd(body: { url: string; note: string }): Promise<MaterialItem> {
  return request<MaterialItem>("/api/materials/add", { method: "POST", body: JSON.stringify(body) });
}

/** POST /api/materials/dismiss — 放弃一条（状态机 → dismissed；台账保留记录，API 侧可恢复） */
export function postMaterialDismiss(id: string): Promise<MaterialItem> {
  return request<MaterialItem>("/api/materials/dismiss", { method: "POST", body: JSON.stringify({ id }) });
}
