// 板级诊断条（原生 Diagnostics.swift DiagnosticsStrip / DiagnosticCardView 的 web 版；§48 / §54.4）：把静默的
// ingest 失败变成看得见、点得动的卡——每张卡 (1) 大白话说清哪条路断了，(2) 一颗直达修复的主按钮。
// 数据 = board.radar_sources 投影（§48：enabled 就是用户的 intent，skip_reason 是闭集词表）+ 壳的录制状态
// （引擎活着没 / 屏幕录制授权还在不在）。ANTI-NAG：只显示「用户 INTENDED 的路径在静默失败」——关着的源不上板，
// fresh user 看到 0 张卡；每 path 至多一卡；可 dismiss（localStorage `dismissedDiagnostics`，签名 = <path>:<reason>，
// 换原因 = 新卡；修好过一次再坏 / 满 7 天重现）；vault_empty 先等一个 ingest 周期（~35 min）再报，防装机误报。
// 「Gmail / Slack 雷达开着，但后台调度没装上」一族（原生 agent_missing）：Slack / Gmail 雷达是各自的 launchd agent
// （§48.7），状态问 GET /api/radars（server 问 launchd 本人），「重装后台调度」= POST /api/radars/reinstall（server 跑
// install.sh --reinstall-agent，绝不自己写 plist）；失败留在卡上「上次重装失败：」+ 原文，按钮变「再试一次」
// （原生 RepairReceiptStore 的会话内版）。agent_missing 赢：同源的凭证类卡让位（原生 schedulerMissing flag）。
// 只在看板页渲染（原生：kanban header 里 PipelineHealthBanner 之下）。文案逐字镜像 Diagnostics.swift:210–347。
import { useEffect, useState } from "react";
import { fetchRadarAgents, postRadarReinstall } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, DEPS_ANCHOR, readPage, type AppPage } from "../../route";
import { callShell, hasShellBridge, useShellState, type ShellRecordingState } from "../../shellBridge";
import { useAppState } from "../../store";
import type { RadarAgentStatus, RadarSourceHealth } from "../../types";
import { RelativeTime } from "../board/cardChrome";

type Text = (zh: string, en: string) => string;

export const DISMISS_KEY = "dismissedDiagnostics";
export const FIRST_SEEN_KEY = "diagnosticsFirstSeen";
const REAPPEAR_AFTER_MS = 7 * 86_400_000;
const VAULT_EMPTY_WARMUP_MS = 35 * 60_000; // 对齐 install.sh */30

export type RadarAgentSource = "gmail" | "slack";

export type DiagAction =
  | { kind: "restart_engine" }
  | { kind: "grant_screen" }
  | { kind: "page"; page: AppPage; anchor?: string }
  | { kind: "reinstall_agent"; source: RadarAgentSource };

/** launchd 里两个雷达 agent 的状态 + 会话内的重装失败回执（原生 RepairReceiptStore） */
export interface RadarAgentsView {
  radars: Record<string, RadarAgentStatus> | null;   // null = 还没问到 / 问不到（不出卡）
  failures: Record<string, string>;                  // source → 上次重装失败原文
}

export interface DiagnosticCard {
  id: string;          // "diag.<path>"
  signature: string;   // "<path>:<reason>"
  title: string;
  detail: string;
  actionLabel: string;
  action: DiagAction;
  lastOk: string | null;
  lastAttempt: string | null;  // 「上次尝试 …」（§48.4 add-only 键；旧 payload 缺 → 不显示）
  /** detail 的前缀句（「上次重装失败：」）——有它时前缀与原文各一节点渲染 */
  detailPrefix?: string;
}

function readMap(key: string): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(key);
    const obj = raw ? JSON.parse(raw) : {};
    return obj && typeof obj === "object" ? (obj as Record<string, number>) : {};
  } catch {
    return {};
  }
}

function writeMap(key: string, map: Record<string, number>): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(map));
  } catch {
    /* 隐私模式等不可写 */
  }
}

/** Gmail skip_reason → 三类文案（原生 DiagnosticsRules.gmailCardKind） */
export function gmailCardKind(reason: string): "setup" | "command" | "connection" {
  if (reason === "no_credentials" || reason === "no_address") return "setup";
  if (reason.startsWith("fetch_command") || reason.startsWith("command")) return "command";
  return "connection";
}

function obsidianCard(reason: string, rec: ShellRecordingState | null, entry: RadarSourceHealth, text: Text): DiagnosticCard | null {
  const card = (sig: string, title: string, detail: string, actionLabel: string, action: DiagAction): DiagnosticCard =>
    ({ id: "diag.screenpipe", signature: `screenpipe:${sig}`, title, detail, actionLabel, action, lastOk: entry.last_ok ?? null, lastAttempt: entry.last_attempt ?? null });
  switch (reason) {
    case "vault_empty":
      if (rec && !rec.engine_running) {
        return card("vault_empty.engine", text("录制开着，但没在生成笔记", "Recording is on but no notes are being made"),
          text("录制引擎没在跑，屏幕内容没被抓下来，也就没有笔记进 vault。原地重启引擎试试。", "The capture engine isn't running, so nothing is captured and no notes reach the vault. Restart it in place."),
          text("重启录制引擎", "Restart the engine"), { kind: "restart_engine" });
      }
      if (rec?.tcc_lost) {
        return card("vault_empty.tcc", text("屏幕录制权限被收回了", "Screen Recording permission was revoked"),
          text("引擎在跑，但 macOS 收回了「屏幕录制」授权（系统更新/重装会静默失效）——录不到任何东西。", "The engine runs, but macOS revoked Screen Recording (an OS update/reinstall silently drops it) — nothing gets captured."),
          text("去授权屏幕录制", "Grant Screen Recording"), { kind: "grant_screen" });
      }
      return card("vault_empty.other", text("录制开着，但 vault 里没有新笔记", "Recording is on but no new notes appear"),
        text("屏幕→笔记这条链有一环没通（导出/清洗/ingest）。过一遍依赖检查能定位到具体哪一步。", "A step in the screen→note chain isn't firing (export/cleanup/ingest). The dependency check pinpoints which one."),
        text("打开依赖检查", "Open Dependencies"), { kind: "page", page: "settings", anchor: DEPS_ANCHOR });
    case "no_api_key":
      return card("no_api_key", text("定时任务没有 API key", "The scheduled job has no API key"),
        text("截图能录，但把截图变成笔记要调用 claude，而定时任务读不到 Anthropic API key。", "Capture works, but turning captures into notes calls claude — and the scheduled job can't read an Anthropic API key."),
        text("填入 Anthropic API Key", "Enter the Anthropic API Key"), { kind: "page", page: "settings", anchor: "credentials" });
    case "extract_failed":
      return card("extract_failed", text("截图→笔记这条链在报错", "The capture→note chain is erroring"),
        text("有 API key，但 claude 处理笔记时失败了（模型报错/超时/输出无法解析）。依赖检查里有完整日志。", "A key exists, but claude failed while processing a note (error/timeout/unparseable output). Full logs are in the dependency check."),
        text("打开依赖检查", "Open Dependencies"), { kind: "page", page: "settings", anchor: DEPS_ANCHOR });
    case "vault_missing":
      return card("vault_missing", text("还没指定 Obsidian 目录", "No Obsidian folder is set"),
        text("录制开着，但没告诉助手笔记该放哪个 vault 目录——先指定它，链路才能落地。", "Recording is on but no vault folder is set for the notes — point to one so the pipeline has somewhere to land."),
        text("指定 Obsidian 目录", "Set the Obsidian folder"), { kind: "page", page: "settings", anchor: "obsidian" });
    default:
      return null; // "disabled" / 词表外 → 不上板
  }
}

function gmailCard(reason: string, entry: RadarSourceHealth, text: Text): DiagnosticCard {
  const kind = gmailCardKind(reason);
  const [title, detail] = kind === "setup"
    ? [text("Gmail 雷达开着但还没配好", "The Gmail radar is on but not set up"),
      text("开关开着，但缺应用密码或邮箱地址——补上它雷达才能开始扫。", "The switch is on but the app password or address is missing — add it so the radar can scan.")]
    : kind === "command"
      ? [text("Gmail 抓取命令没跑成", "The Gmail fetch command is failing"),
        text("邮件走的是你的自定义抓取命令（gmail_fetch_command），它在报错或输出不是雷达能读的格式——去 Gmail 设置里检查那条命令。", "Mail comes via your custom fetch command (gmail_fetch_command), and it's erroring or emitting output the radar can't read — check that command in Gmail settings.")]
      : [text("Gmail 雷达连不上", "The Gmail radar can't connect"),
        text("存了应用密码，但雷达没法用它登录——多半是密码过期或邮箱地址没填对。", "An app password is saved but the radar can't log in — the password likely expired or the address is off.")];
  return { id: "diag.gmail", signature: `gmail:${reason}`, title, detail, actionLabel: text("检查 Gmail 设置", "Check Gmail settings"),
    action: { kind: "page", page: "settings", anchor: "gmail" }, lastOk: entry.last_ok ?? null, lastAttempt: entry.last_attempt ?? null };
}

function slackCard(reason: string, entry: RadarSourceHealth, text: Text): DiagnosticCard | null {
  if (reason === "connect_failed" || reason === "auth_failed") {
    return { id: "diag.slack", signature: `slack:${reason}`, title: text("Slack token 无效", "The Slack token is invalid"),
      detail: text("存了 token，但 Slack 拒绝了它——重新复制 User OAuth Token（xoxp- 开头）再试。", "A token is saved but Slack rejected it — copy the User OAuth Token (starts with xoxp-) again."),
      actionLabel: text("检查 Slack 设置", "Check Slack settings"), action: { kind: "page", page: "settings", anchor: "slack" }, lastOk: entry.last_ok ?? null, lastAttempt: entry.last_attempt ?? null };
  }
  if (reason === "mcp_not_configured") {
    return { id: "diag.slack", signature: "slack:mcp_not_configured", title: text("Slack 兜底没连上", "Slack fallback isn't connected"),
      detail: text("还没存 token，兜底走 claude 的 Slack MCP——但 CLI 里没配这个 MCP。存个 token 或加上 Slack MCP 都行。", "No token yet, so the fallback uses claude's Slack MCP — but it isn't registered in the CLI. Save a token or add the Slack MCP."),
      actionLabel: text("连接 Slack", "Connect Slack"), action: { kind: "page", page: "settings", anchor: "slack" }, lastOk: entry.last_ok ?? null, lastAttempt: entry.last_attempt ?? null };
  }
  return null;
}

/** 原生 DiagnosticsRules.schedulerMissing：源开着 ∧（launchd 里没它 ∨ 上次重装失败）。agent 状态未知（非 darwin /
 *  还没问到）= 不判——宁可不出卡也不瞎报。 */
export function schedulerMissing(entry: RadarSourceHealth | undefined, agent: RadarAgentStatus | undefined, failed: boolean): boolean {
  if (!entry?.enabled || !agent || agent.loaded === null) return false;
  return !agent.loaded || !agent.plist_installed || failed;
}

function agentMissingCard(source: RadarAgentSource, entry: RadarSourceHealth, failMsg: string | undefined, text: Text): DiagnosticCard {
  return {
    id: `diag.${source}`, signature: `${source}:agent_missing`,
    title: source === "gmail"
      ? text("Gmail 雷达开着，但后台调度没装上", "The Gmail radar is on but its scheduler isn't installed")
      : text("Slack 雷达开着，但后台调度没装上", "The Slack radar is on but its scheduler isn't installed"),
    detail: failMsg !== undefined
      ? text("上次重装失败：", "The last reinstall failed: ") + failMsg
      : text("开关是开的，但 launchd 里没有它的调度任务（多半是关着时升级被卸载了）——点一下原地装回去。",
        "The switch is on, but launchd has no job for it (likely removed by an upgrade while it was off) — one click reinstalls it in place."),
    detailPrefix: failMsg !== undefined ? text("上次重装失败：", "The last reinstall failed: ") : undefined,
    actionLabel: failMsg === undefined ? text("重装后台调度", "Reinstall the scheduler") : text("再试一次", "Try again"),
    action: { kind: "reinstall_agent", source },
    lastOk: entry.last_ok ?? null, lastAttempt: entry.last_attempt ?? null,
  };
}

/** 该不该出卡的纯逻辑（原生 DiagnosticsRules + DiagnosticsModel.rebuild 的 web 版）；导出供测试直测。
 *  `agents` 缺席 = 不判 agent_missing（旧调用方 / launchd 还没问到）。 */
export function buildDiagnosticCards(sources: Record<string, RadarSourceHealth> | undefined | null, rec: ShellRecordingState | null, text: Text,
  agents?: RadarAgentsView | null): DiagnosticCard[] {
  if (!sources) return [];
  const out: DiagnosticCard[] = [];
  const ob = sources.obsidian;
  // obsidian / screenpipe：intent = 录制开着（壳在场时看壳；浏览器里退成投影的 enabled）
  const recOn = rec ? rec.mode !== "off" : Boolean(ob?.enabled);
  if (ob?.enabled && recOn && ob.skip_reason) {
    const card = obsidianCard(ob.skip_reason, rec, ob, text);
    if (card) out.push(card);
  }
  // agent_missing 赢：调度没装上时凭证 / 连接类卡让位（同源一卡，原生 schedulerMissing flag）
  const missing = (source: RadarAgentSource): boolean => {
    const entry = sources[source];
    const failMsg = agents?.failures[source];
    if (!entry || !schedulerMissing(entry, agents?.radars?.[source], failMsg !== undefined)) return false;
    out.push(agentMissingCard(source, entry, failMsg, text));
    return true;
  };
  const gm = sources.gmail;
  if (!missing("gmail") && gm?.enabled && gm.skip_reason) out.push(gmailCard(gm.skip_reason, gm, text));
  const sl = sources.slack;
  if (!missing("slack") && sl?.enabled && sl.skip_reason) {
    const card = slackCard(sl.skip_reason, sl, text);
    if (card) out.push(card);
  }
  return out;
}

/** 已 dismiss 且仍有效：同签名、dismiss 后没成功过、7 天窗口内 */
export function isDismissed(card: DiagnosticCard, dismissed: Record<string, number>, now: number): boolean {
  const ts = dismissed[card.signature];
  if (!ts) return false;
  if (now - ts > REAPPEAR_AFTER_MS) return false;
  const ok = card.lastOk ? Date.parse(card.lastOk) : NaN;
  if (Number.isFinite(ok) && ok > ts) return false; // 修好过一次又坏了 → 重新报
  return true;
}

/** 预热防抖：vault_empty 等满一个 ingest 周期再报；首次见到只记时间不出卡 */
export function isDebounced(card: DiagnosticCard, seen: Record<string, number>, now: number): { debounced: boolean; seen: Record<string, number> } {
  if (!card.signature.startsWith("screenpipe:vault_empty")) return { debounced: false, seen };
  const first = seen[card.signature];
  if (first !== undefined) return { debounced: now - first < VAULT_EMPTY_WARMUP_MS, seen };
  return { debounced: true, seen: { ...seen, [card.signature]: now } };
}

function actionHref(action: DiagAction): string | null {
  if (action.kind !== "page") return null;
  const url = buildAppUrl(window.location.href, action.page, null);
  if (action.anchor) url.searchParams.set("anchor", action.anchor);
  return url.toString();
}

function DiagnosticCardView({ card, busy, onDismiss, onReinstall }: { card: DiagnosticCard; busy: boolean; onDismiss: () => void; onReinstall: (source: RadarAgentSource) => void }) {
  const { text } = useI18n();
  const shell = hasShellBridge();
  const href = actionHref(card.action);
  const perform = () => {
    if (card.action.kind === "restart_engine") void callShell("restartRecording").catch(() => undefined);
    else if (card.action.kind === "grant_screen") void callShell("openPane", { pane: "screen" }).catch(() => undefined);
    else if (card.action.kind === "reinstall_agent") onReinstall(card.action.source);
  };
  // 浏览器里（无桥）引擎 / 授权类动作退成页面深链（录制页 / 权限体检）
  const fallbackHref = card.action.kind === "restart_engine"
    ? buildAppUrl(window.location.href, "ingest", null).toString()
    : card.action.kind === "grant_screen" ? buildAppUrl(window.location.href, "permissions", null).toString() : null;
  return (
    <div className="shell-banner is-warning diag-card" role="status" data-signature={card.signature}>
      <svg className="shell-banner-icon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z" fill="currentColor" />
      </svg>
      <strong className="shell-banner-title">{card.title}</strong>
      <span className="shell-banner-detail">
        {card.detailPrefix
          ? <><span className="shell-banner-prefix">{card.detailPrefix}</span><span>{card.detail.slice(card.detailPrefix.length)}</span></>
          : card.detail}
      </span>
      <span className="shell-banner-actions">
        {href || (!shell && fallbackHref)
          ? <a className="shell-button" href={href ?? fallbackHref ?? "#"}>{card.actionLabel}</a>
          : <button type="button" className="shell-button" disabled={busy} onClick={perform}>{busy ? text("重装中…", "Reinstalling…") : card.actionLabel}</button>}
        {card.lastAttempt && <RelativeTime iso={card.lastAttempt} prefix={text("上次尝试 ", "last tried ")} className="shell-banner-note" />}
        <button type="button" className="shell-icon-button diag-dismiss" title={text("忽略这张卡（问题还在会重新出现）", "Dismiss (returns if still broken)")} aria-label={text("忽略这张卡（问题还在会重新出现）", "Dismiss (returns if still broken)")} onClick={onDismiss}>×</button>
      </span>
    </div>
  );
}

/** launchd 里两个雷达 agent 的状态：挂载时问一次 GET /api/radars（问不到 = null，不出卡）；重装后再问 */
function useRadarAgents(): [RadarAgentsView, (source: RadarAgentSource) => Promise<void>, RadarAgentSource | null] {
  const [radars, setRadars] = useState<Record<string, RadarAgentStatus> | null>(null);
  const [failures, setFailures] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<RadarAgentSource | null>(null);

  useEffect(() => {
    let alive = true;
    fetchRadarAgents().then((snap) => { if (alive) setRadars(snap.radars ?? {}); }, () => { if (alive) setRadars(null); });
    return () => { alive = false; };
  }, []);

  const reinstall = async (source: RadarAgentSource) => {
    setBusy(source);
    try {
      const receipt = await postRadarReinstall(source);
      // 回执里 server 装完再问过 launchd 的 loaded 才是最后一笔（不信按钮，信回执）
      setRadars((prev) => ({ ...(prev ?? {}), [source]: { ...(prev?.[source] ?? { label: receipt.label, interval_s: null, plist_installed: true }), loaded: receipt.loaded, plist_installed: true } }));
      setFailures((prev) => { const next = { ...prev }; delete next[source]; return next; });
    } catch (e) {
      setFailures((prev) => ({ ...prev, [source]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(null);
    }
  };
  return [{ radars, failures }, reinstall, busy];
}

export function DiagnosticsStrip() {
  const { text } = useI18n();
  const { board, boardError, connection } = useAppState();
  const shellState = useShellState();
  const [dismissed, setDismissed] = useState(() => readMap(DISMISS_KEY));
  const [agents, reinstall, reinstalling] = useRadarAgents();

  if (!board || boardError != null || connection === "reconnecting") return null;
  if (readPage(window.location.search) !== "board") return null;

  const rec = hasShellBridge() ? shellState?.recording ?? null : null;
  const now = Date.now();
  const seen0 = readMap(FIRST_SEEN_KEY);
  let seen = seen0;
  const cards: DiagnosticCard[] = [];
  for (const card of buildDiagnosticCards(board.radar_sources, rec, text, agents)) {
    const verdict = isDebounced(card, seen, now);
    seen = verdict.seen;
    if (verdict.debounced || isDismissed(card, dismissed, now)) continue;
    cards.push(card);
  }
  if (seen !== seen0) writeMap(FIRST_SEEN_KEY, seen); // 首见时间只在新签名出现时落一次
  if (cards.length === 0) return null;

  const dismiss = (card: DiagnosticCard) => {
    const next = { ...dismissed, [card.signature]: now };
    writeMap(DISMISS_KEY, next);
    setDismissed(next);
  };

  return (
    <div className="diag-strip" data-count={cards.length}>
      {cards.map((card) => (
        <DiagnosticCardView key={card.id} card={card} onDismiss={() => dismiss(card)} onReinstall={(source) => void reinstall(source)}
          busy={card.action.kind === "reinstall_agent" && reinstalling === card.action.source} />
      ))}
    </div>
  );
}
