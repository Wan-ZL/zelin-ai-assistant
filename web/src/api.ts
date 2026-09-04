// fetch 客户端 + 错误分类学。模式 fork 自 dashi web/src/api.ts（Apache-2.0，NOTICE 登记）：
// ApiError{status,code,details} 统一封装 + GET 幂等重试（2 次退避）+ 网络失败合成码。
// error envelope 契约（server/app.py）：{"error":{"code","message","details"}}，
// server 端码：UNKNOWN_FIELD / INVALID_FIELD / NOT_FOUND / INTERNAL_ERROR；
// 客户端合成码：READ_FAILED（读失败，UI 静默重试）/ SERVICE_UNAVAILABLE（写失败，UI 明确报错）。
// 本模块不 import React——文案经 setApiText 注入（app.tsx 接线），vitest node 环境可直测。
import type {
  AboutInfo,
  AiFixReceipt,
  Board,
  CardDetail,
  ClaudeCodeDefault,
  ClaudeCodeDefaultWrite,
  DisplaySettings,
  DisplaySettingsPatch,
  ClaudeSessionsScan,
  DiagnosticsSnapshot,
  DoctorReport,
  FailureCatalog,
  IngestJob,
  IngestJobStart,
  DailyLoopPatch,
  DailyLoopSettings,
  FolderReceipt,
  HealthSnapshot,
  LaneCatalog,
  MaterialItem,
  MaterialsList,
  LogTail,
  McpList,
  ModelsSettings,
  RecapMarkReceipt,
  RecapSettings,
  SkillsSnapshot,
  PermissionsSnapshot,
  RadarAgentsSnapshot,
  RadarReinstallReceipt,
  RepairReceipt,
  SecretStatus,
  SecretVerifyResult,
  SlackDirectory,
  SyncDisableReceipt,
  SyncPairReceipt,
  SyncStatus,
  VoiceProfileStatus,
  SecretsStatus,
  SeedDashboardReceipt,
  SettingsCatalog,
  SettingsSection,
  SetupEngine,
  SetupReceipt,
  SetupSnapshot,
  TerminalReceipt,
  UpdateCheckResult,
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

/** POST /api/self-improve/resume（CONTRACT §65.4）：owner 清掉敏感路径护栏挂起的自动草稿 PR 通道；空 body */
export function postSelfImproveResume(): Promise<{ ok: boolean; paused: boolean; was_paused: boolean }> {
  return request("/api/self-improve/resume", { method: "POST", body: "{}" });
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

/** POST /api/ai-fix {source: "doctor"} — 依赖检查页的「让 AI 修」：上下文 = server 自己跑的 doctor 报告里没过的行（§54.4） */
export function postAiFixDoctor(lang: "zh" | "en"): Promise<AiFixReceipt> {
  return request<AiFixReceipt>("/api/ai-fix", {
    method: "POST",
    body: JSON.stringify({ source: "doctor", lang }),
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

/** GET /api/settings/daily-loop — 每日自我改进循环的五把旋钮 effective 值（CONTRACT §70） */
export function fetchDailyLoopSettings(signal?: AbortSignal): Promise<DailyLoopSettings> {
  return request<DailyLoopSettings>("/api/settings/daily-loop", { signal });
}

/** PUT /api/settings/daily-loop — 保存旋钮子集（写请求：四闸同 POST；server 校验 + diff-write） */
export function putDailyLoopSettings(body: DailyLoopPatch): Promise<DailyLoopSettings> {
  return request<DailyLoopSettings>("/api/settings/daily-loop", { method: "PUT", body: JSON.stringify(body) });
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

/** GET /api/settings/recap — 会议 recap 三把旋钮的 effective 值（CONTRACT §63） */
export function fetchRecapSettings(signal?: AbortSignal): Promise<RecapSettings> {
  return request<RecapSettings>("/api/settings/recap", { signal });
}

/** PUT /api/settings/recap — 只许 enabled / default_language / slack_draft_enabled 三键（server 零容忍） */
export function putRecapSettings(
  body: { enabled?: boolean; default_language?: string; slack_draft_enabled?: boolean },
): Promise<RecapSettings> {
  return request<RecapSettings>("/api/settings/recap", { method: "PUT", body: JSON.stringify(body) });
}

/** GET /api/settings/display — 显示偏好三把旋钮的 effective 值 + 词表（CONTRACT §54.1 第 12 项） */
export function fetchDisplaySettings(signal?: AbortSignal): Promise<DisplaySettings> {
  return request<DisplaySettings>("/api/settings/display", { signal });
}

/** PUT /api/settings/display — 只许 text_size / text_weight / stroke 三键（server 零容忍） */
export function putDisplaySettings(body: DisplaySettingsPatch): Promise<DisplaySettings> {
  return request<DisplaySettings>("/api/settings/display", { method: "PUT", body: JSON.stringify(body) });
}

/** POST /api/recaps/mark — 「复制」/「标记已发送」本地标记（server 独写 marks.json；无控制流读它） */
export function postRecapMark(key: string, mark: "copied" | "sent", on = true): Promise<RecapMarkReceipt> {
  return request<RecapMarkReceipt>("/api/recaps/mark", {
    method: "POST",
    body: JSON.stringify({ key, mark, on }),
  });
}

/** GET /api/skills — skill 商店 manifest + 本机每个 skill 的状态（CONTRACT §67） */
export function fetchSkills(signal?: AbortSignal): Promise<SkillsSnapshot> {
  return request<SkillsSnapshot>("/api/skills", { signal });
}

/**
 * POST /api/skills — 启用/停用一个 skill（写请求：四闸同 POST，api.ts 自动带 token）。
 * body 只许 name / action 两键；自定义副本（state=custom）server 拒改 409 CONFLICT，整句原文由页面 toast。
 */
export function postSkill(name: string, action: "enable" | "disable"): Promise<SkillsSnapshot> {
  return request<SkillsSnapshot>("/api/skills", { method: "POST", body: JSON.stringify({ name, action }) });
}

// ----- §68 legacy-app parity 面（设置全套 / 凭证 / 权限 / 诊断 / 向导 / 关于 / 工具） ----- #

/** GET /api/settings — 通用设置目录全集（section → fields，effective + source；文案 server-owned） */
export function fetchSettingsCatalog(signal?: AbortSignal): Promise<SettingsCatalog> {
  return request<SettingsCatalog>("/api/settings", { signal });
}

/** GET /api/settings/{section} — 单 section 快照 */
export function fetchSettingsSection(section: string, signal?: AbortSignal): Promise<SettingsSection> {
  return request<SettingsSection>(`/api/settings/${encodeURIComponent(section)}`, { signal });
}

/** PUT /api/settings/{section} — body = {key: value} 子集（server 字段白名单 + 类型校验 + diff-write） */
export function putSettingsSection(section: string, body: Record<string, unknown>): Promise<SettingsSection> {
  return request<SettingsSection>(`/api/settings/${encodeURIComponent(section)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** GET /api/secrets — 凭证状态（present / verifiable），值永不回显 */
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

/** POST /api/secrets/{name}/verify — 最小活探针（server 侧；Slack 成功自动填 owner id）。
 *  带 value = 粘贴即验证（§68.3；只探这个值、不落盘——向导「先验后存」用） */
export function verifySecret(name: string, value?: string): Promise<SecretVerifyResult> {
  return request<SecretVerifyResult>(`/api/secrets/${encodeURIComponent(name)}/verify`, {
    method: "POST",
    body: JSON.stringify(value === undefined ? {} : { value }),
  });
}

/** GET /api/permissions — 权限体检的 server 半边（FDA 清单 + TCC 相关 doctor 行） */
export function fetchPermissions(refresh = false, signal?: AbortSignal): Promise<PermissionsSnapshot> {
  return request<PermissionsSnapshot>(`/api/permissions${refresh ? "?refresh=1" : ""}`, { signal });
}

/** GET /api/diagnostics — doctor + health + deploy_state + install_report + 日志清单 */
export function fetchDiagnostics(refresh = false, signal?: AbortSignal): Promise<DiagnosticsSnapshot> {
  return request<DiagnosticsSnapshot>(`/api/diagnostics${refresh ? "?refresh=1" : ""}`, { signal });
}

/** GET /api/doctor — 完整 doctor（fast=false 含活探针，会花 token） */
export function fetchDoctor(fast = true, refresh = false, signal?: AbortSignal): Promise<DoctorReport> {
  const params = new URLSearchParams();
  if (!fast) params.set("fast", "0");
  if (refresh) params.set("refresh", "1");
  const query = params.toString();
  return request<DoctorReport>(`/api/doctor${query ? `?${query}` : ""}`, { signal });
}

/** GET /api/logs/{name}?lines=N — 日志尾巴（只读、size-cap） */
export function fetchLogTail(name: string, lines = 200, signal?: AbortSignal): Promise<LogTail> {
  return request<LogTail>(`/api/logs/${encodeURIComponent(name)}?lines=${lines}`, { signal });
}

/** GET /api/setup — 首次运行向导状态 */
export function fetchSetup(signal?: AbortSignal): Promise<SetupSnapshot> {
  return request<SetupSnapshot>("/api/setup", { signal });
}

/** POST /api/setup/{config-from-example | complete | reset} */
export function postSetupStep(step: "config-from-example" | "complete" | "reset"): Promise<SetupReceipt> {
  return request<SetupReceipt>(`/api/setup/${step}`, { method: "POST", body: JSON.stringify({}) });
}

/** GET /api/setup/engine — AI 引擎检测（claude CLI + 认证梯子；原生 EngineDetector） */
export function fetchSetupEngine(signal?: AbortSignal): Promise<SetupEngine> {
  return request<SetupEngine>("/api/setup/engine", { signal });
}

/** POST /api/setup/seed-dashboard — 首次数据「立即生成一次」（python -m act.lib.dashboard） */
export function postSeedDashboard(): Promise<SeedDashboardReceipt> {
  return request<SeedDashboardReceipt>("/api/setup/seed-dashboard", { method: "POST", body: JSON.stringify({}) });
}

/** POST /api/reveal {target[, name]} — 访达定位 server 词表里的文件（config = config.yaml / 模板，§68.4「显示文件」；
 *  skill + name = 该 skill 的 SKILL.md，§67.5「在 Finder 显示」——客户端只传词与名，路径 server 推导） */
export function postRevealTarget(target: "config" | "skill" | "voice_profile", name?: string): Promise<unknown> {
  return request("/api/reveal", { method: "POST", body: JSON.stringify(name === undefined ? { target } : { target, name }) });
}

/** GET /api/about — 版本 / 路径 / 更新状态 */
export function fetchAbout(signal?: AbortSignal): Promise<AboutInfo> {
  return request<AboutInfo>("/api/about", { signal });
}

/** POST /api/update/check — §26 手动「立即检查」 */
export function postUpdateCheck(): Promise<UpdateCheckResult> {
  return request<UpdateCheckResult>("/api/update/check", { method: "POST", body: JSON.stringify({}) });
}

/** POST /api/update/install — 关于页「新版本 v… 可用 — 一键更新」：提前 kickstart §56 自动部署 agent（未加载 → 409） */
export function postUpdateInstall(): Promise<RepairReceipt> {
  return request<RepairReceipt>("/api/update/install", { method: "POST", body: JSON.stringify({}) });
}

/** POST /api/ingest/export — 录制页「立即导出」= bash ingest/screenpipe-export.sh（后台跑，回 job id） */
export function postIngestExport(): Promise<IngestJobStart> {
  return request<IngestJobStart>("/api/ingest/export", { method: "POST", body: JSON.stringify({}) });
}

/** POST /api/ingest/run — 录制页「立即 ingest」= SCREENPIPE_NO_WAIT=1 bash ingest/process-screenpipe.sh（exit 3 = 持锁跳过） */
export function postIngestRun(): Promise<IngestJobStart> {
  return request<IngestJobStart>("/api/ingest/run", { method: "POST", body: JSON.stringify({}) });
}

/** GET /api/ingest/jobs/{id} — 手动触发的进度：running → done（回执五键） */
export function fetchIngestJob(id: string, signal?: AbortSignal): Promise<IngestJob> {
  return request<IngestJob>(`/api/ingest/jobs/${encodeURIComponent(id)}`, { signal });
}

/** GET /api/failures — §25 失败目录（原生 FailureCatalog.message 的 server-owned 双语句） */
export function fetchFailures(signal?: AbortSignal): Promise<FailureCatalog> {
  return request<FailureCatalog>("/api/failures", { signal });
}

/** GET /api/mcp — MCP servers 两作用域（只读、已掩码） */
export function fetchMcp(signal?: AbortSignal): Promise<McpList> {
  return request<McpList>("/api/mcp", { signal });
}

/** GET /api/claude-sessions?window=N — 导入预览扫描 */
export function fetchClaudeSessions(window = 7, signal?: AbortSignal): Promise<ClaudeSessionsScan> {
  return request<ClaudeSessionsScan>(`/api/claude-sessions?window=${window}`, { signal });
}

/** POST /api/terminal — 在终端接管会话（命令由 server 从投影推导；客户端只传 card_id） */
export function postTerminal(cardId: string): Promise<TerminalReceipt> {
  return request<TerminalReceipt>("/api/terminal", { method: "POST", body: JSON.stringify({ card_id: cardId }) });
}

/** POST /api/repair/actd — 横幅一键修复（launchctl kickstart；未加载 → 409） */
export function postRepairActd(): Promise<RepairReceipt> {
  return request<RepairReceipt>("/api/repair/actd", { method: "POST", body: JSON.stringify({}) });
}

/** GET /api/slack/manifest — repo 的 Slack App Manifest 原文（Slack 接入区「复制 App Manifest」） */
export function fetchSlackManifest(signal?: AbortSignal): Promise<{ manifest: string; path: string }> {
  return request<{ manifest: string; path: string }>("/api/slack/manifest", { signal });
}

/** GET /api/sync — 同步 / 配对状态（开关 + 设备名 + 配对二维码 PNG） */
export function fetchSync(signal?: AbortSignal): Promise<SyncStatus> {
  return request<SyncStatus>("/api/sync", { signal });
}

/** POST /api/sync/pair {label?} — 起 act.syncd --pair --json（开启 / 重新生成 / 改名；幂等，同一 channel 同一码） */
export function postSyncPair(label?: string): Promise<SyncPairReceipt> {
  return request<SyncPairReceipt>("/api/sync/pair", { method: "POST", body: JSON.stringify(label ? { label } : {}) });
}

/** POST /api/sync/disable {} — act.syncd --disable（mode=off，密钥保留） */
export function postSyncDisable(): Promise<SyncDisableReceipt> {
  return request<SyncDisableReceipt>("/api/sync/disable", { method: "POST", body: JSON.stringify({}) });
}

/** GET /api/voice — 语气档案「当前生效」状态行（私有 / 出厂 / 无；开关） */
export function fetchVoiceProfile(signal?: AbortSignal): Promise<VoiceProfileStatus> {
  return request<VoiceProfileStatus>("/api/voice", { signal });
}

/** GET /api/slack/directory[?refresh=1] — 频道 + 成员目录（子进程 act.lib.slack_setup --directory，1 h 缓存；§68.1 追记） */
export function fetchSlackDirectory(refresh = false, signal?: AbortSignal): Promise<SlackDirectory> {
  return request<SlackDirectory>(`/api/slack/directory${refresh ? "?refresh=1" : ""}`, { signal });
}

/** POST /api/uninstall/terminal — 关于页「在 Terminal 中卸载…」：server 写 .command（cd repo && bash uninstall.sh）并 open（§68.6） */
export function postUninstallTerminal(): Promise<TerminalReceipt> {
  return request<TerminalReceipt>("/api/uninstall/terminal", { method: "POST", body: JSON.stringify({}) });
}

/** POST /api/maintainer/terminal — 开发者区「在终端打开开发会话」：cd <repo> && claude [--resume]，参数由 server 读设置（§68.1） */
export function postMaintainerTerminal(): Promise<TerminalReceipt> {
  return request<TerminalReceipt>("/api/maintainer/terminal", { method: "POST", body: JSON.stringify({}) });
}

/** GET /api/radars — Slack / Gmail 后台雷达 agent 的 launchd 状态（§48.7；token-light GET） */
export function fetchRadarAgents(signal?: AbortSignal): Promise<RadarAgentsSnapshot> {
  return request<RadarAgentsSnapshot>("/api/radars", { signal });
}

/** POST /api/radars/reinstall {source} — 「重新安装」：server 跑 install.sh --reinstall-agent <label>（§48.7） */
export function postRadarReinstall(source: "gmail" | "slack"): Promise<RadarReinstallReceipt> {
  return request<RadarReinstallReceipt>("/api/radars/reinstall", { method: "POST", body: JSON.stringify({ source }) });
}

/** POST /api/folders/open {key} — 目录字段「打开」：server 从已保存的设置读路径，访达打开（§68.1） */
export function postFolderOpen(key: string): Promise<FolderReceipt> {
  return request<FolderReceipt>("/api/folders/open", { method: "POST", body: JSON.stringify({ key }) });
}

/** POST /api/folders/create {key} — 目录字段「创建」/「创建文件夹」：mkdir -p（任务工作目录另 git init）（§68.1） */
export function postFolderCreate(key: string): Promise<FolderReceipt> {
  return request<FolderReceipt>("/api/folders/create", { method: "POST", body: JSON.stringify({ key }) });
}
