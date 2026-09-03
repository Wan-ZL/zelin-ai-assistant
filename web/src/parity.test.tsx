// UI 对齐判例（CONTRACT §66.2）—— 由 ui/parity/native-inventory.json 驱动，不手写列表。
// 原生 mac/Sources（D3 冻结）的每个 gated `control:*` 条目在这里变成一条 it()：
//   · 不在 pending/waivers 上 → 断言标签「在」：用 demo fixture 渲染看板 / 回收站 / 设置 / 关于 / 问问助手 /
//     依赖检查 / 录制与数据接入 / 初始设置向导（七步逐步）/ 权限体检 九个面（zh 与 en 各一遍；看板另渲染
//     「空板 + 搜索中 + 后台服务卡住」与「诊断条：Gmail / Slack / 录制链各种 skip_reason」几遍；向导与权限体检
//     另按几套假壳状态（录制开 / 关、引擎在 / 不在、授权 granted / denied / unknown）与 server 快照（引擎就绪 /
//     没装 / 没登录；后台服务在跑 / 没跑；cron 探针 ok / 被挡 / 停跑）各渲染几遍收全部状态词）+ 把每颗按钮点一遍
//     收集弹窗文案，按 accessible name / 自身文本精确匹配；
//     时钟冻结在 fixture 的 FIXED_NOW（相对时间词表才确定）；装一个假 zaiShell 桥（壳里才渲染的
//     录制 / 字幕 / 登录时启动 开关也要判）；server 目录（设置 / 凭证）用 fixture 快照（文案 server-owned）。
//   · 在 ui/parity/pending.txt 上 → it 标题带 ` [pending]`，断言「不在」——补齐后不划账即红
//     （与 qa/*_baseline.txt 同一 shrink-only 语义）；
//   · 在 ui/parity/waivers.txt 上 → it.skip（报告计 WAIVED）。
// scripts/ui/parity_check.py 以 --reporter=json 跑本文件、按 it 标题读判决；两边读同两本账本，
// 判决一致。双语都要命中（原生 L("zh","en") 是逐字规格，PR #143「逐字镜像」同理）。
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  fetchAbout,
  fetchAskHistory,
  fetchBoard,
  fetchCard,
  fetchClaudeCodeDefault,
  fetchClaudeSessions,
  fetchDiagnostics,
  fetchHealth,
  fetchLanes,
  fetchMcp,
  fetchModelsSettings,
  fetchPermissions,
  fetchSecrets,
  fetchSettingsCatalog,
  fetchSetup,
  fetchSetupEngine,
} from "./api";
import { AppShell } from "./components/shell/AppShell";
import { FilterBar } from "./components/chrome/FilterBar";
import { DetailDrawer } from "./components/detail/DetailDrawer";
import { LanguageContext, type Language } from "./i18n";
import { AboutPage } from "./pages/AboutPage";
import { AskPage } from "./pages/AskPage";
import { BoardPage } from "./pages/BoardPage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { IngestPage } from "./pages/IngestPage";
import { PermissionsPage } from "./pages/PermissionsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage, STEPS as SETUP_STEPS } from "./pages/SetupPage";
import { TrashPage } from "./pages/TrashPage";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "./shellBridge";
import {
  refreshBoard,
  refreshHealth,
  refreshLanes,
  refreshPermissions,
  refreshSettings,
  resetStoreForTests,
  selectCard,
  setFilters,
  setLanguage,
} from "./store";
import type {
  AboutInfo,
  Board,
  ClaudeCodeDefault,
  DiagnosticsSnapshot,
  DoctorRow,
  HealthSnapshot,
  LaneCatalog,
  ModelsSettings,
  PermissionsSnapshot,
  RadarSourceHealth,
  SecretsStatus,
  SetupEngine,
  SettingsCatalog,
  SetupSnapshot,
} from "./types";

// 仓库根 ui/parity/ 的清单、两本账本与 fixture 经 import.meta.glob 读——vitest 在 web/ 里跑，
// 解析得到。不用静态 import：tsc 会按 resolveJsonModule 去找那些文件，而 install.sh 的 ui 步
// 把 web/ 镜像到 $HOME 下的构建目录里编（CONTRACT §56.5），仓库根不在那里——2026-09-03 的
// 首次 fresh-install 验收正是死在这几个 import 上。web/src 里任何文件都不许留 web/ 之外的
// 静态 import（tests/test_web_build_self_contained.py 钉）；glob 找不到 = 抛错，不静默空转。
const PARITY_ROOT = "../../ui/parity/";
const parityJson = import.meta.glob(["../../ui/parity/native-inventory.json", "../../ui/parity/fixtures/*.json"],
  { eager: true, import: "default" });
const parityText = import.meta.glob("../../ui/parity/*.txt", { eager: true, import: "default", query: "?raw" });

function parityFile<T>(files: Record<string, unknown>, rel: string): T {
  const value = files[PARITY_ROOT + rel];
  if (value === undefined) {
    throw new Error(`ui/parity/${rel} not found — this suite runs from the repo's web/ dir (got: ${Object.keys(files).join(", ") || "nothing"})`);
  }
  return value as T;
}

const inventory = parityFile<{ controls: ControlItem[] }>(parityJson, "native-inventory.json");
const demoBoard = parityFile<Board>(parityJson, "fixtures/demo-board.json");
const demoLanes = parityFile<LaneCatalog>(parityJson, "fixtures/lanes.json");
const demoSettings = parityFile<SettingsCatalog>(parityJson, "fixtures/settings.json");
const demoSecrets = parityFile<SecretsStatus>(parityJson, "fixtures/secrets.json");
const pendingText = parityFile<string>(parityText, "pending.txt");
const waiversText = parityFile<string>(parityText, "waivers.txt");

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(),
    fetchCard: vi.fn(),
    fetchHealth: vi.fn(),
    fetchLanes: vi.fn(),
    fetchModelsSettings: vi.fn(),
    fetchClaudeCodeDefault: vi.fn(),
    fetchSettingsCatalog: vi.fn(),
    fetchSecrets: vi.fn(),
    fetchSetup: vi.fn(),
    fetchSetupEngine: vi.fn(),
    fetchPermissions: vi.fn(),
    fetchAbout: vi.fn(),
    fetchDiagnostics: vi.fn(),
    fetchMcp: vi.fn(),
    fetchClaudeSessions: vi.fn(),
    fetchAskHistory: vi.fn(),
    fetchDoctor: vi.fn().mockResolvedValue({ ok: true, checks: [], home: "/h", rc: 0, fast: false, ran_at: "2026-09-02T11:59:00Z" }),
    fetchLogTail: vi.fn().mockResolvedValue({ name: "actd.log", path: "/h/state/logs/actd.log", size: 12, lines: ["ok"], truncated: false }),
    fetchSlackManifest: vi.fn().mockResolvedValue({ manifest: "{}", path: "config/slack-app-manifest.json" }),
    fetchSkills: vi.fn().mockResolvedValue({ skills: [], skills_dir: "/h/.claude/skills", repo_skills_dir: "/r/skills", state_path: "/h/state/skills.json" }),
    fetchMaterials: vi.fn().mockResolvedValue({ items: [], status: "open", counts: { open: 0, total: 0 } }),
    fetchRecapSettings: vi.fn().mockResolvedValue({ enabled: true, default_language: "zh", slack_draft_enabled: false, languages: ["auto", "zh", "en"], source: {} }),
    putModelsSettings: vi.fn().mockResolvedValue({}),
    putSettingsSection: vi.fn().mockResolvedValue({}),
    putSecret: vi.fn().mockResolvedValue({}),
    // 隔一个 macrotask 再回：保存 → 「已保存，验证中…」/「验证中…」这一拍才收得到（下一拍收「验证通过」）
    verifySecret: vi.fn(() => new Promise((resolve) => setTimeout(() => resolve({ ok: true, network: false, detail: "ok", extra: {} }), 0))),
    postSetupStep: vi.fn().mockResolvedValue({ ok: true, setup: { needed: false, done: true } }),
    postSeedDashboard: vi.fn().mockResolvedValue({ ok: true, rc: 0 }),
    postRevealTarget: vi.fn().mockResolvedValue({ ok: true }),
    postUpdateCheck: vi.fn().mockResolvedValue({ ok: true, checked_at: "2026-09-02T11:00:00Z", update_available: false, latest: "0.48.30" }),
    postAsk: vi.fn().mockResolvedValue({ ok: true, answer: "42", citation: "README", lang: "en", elapsed_s: 1 }),
    postTerminal: vi.fn().mockResolvedValue({ ok: true }),
    postUninstallTerminal: vi.fn().mockResolvedValue({ ok: true, command: "cd /r && bash uninstall.sh", command_file: "/tmp/u.command" }),
    postMaintainerTerminal: vi.fn().mockResolvedValue({ ok: true, command: "cd /r && claude", command_file: "/tmp/m.command", cwd: "/r" }),
    postRepairActd: vi.fn().mockResolvedValue({ ok: true }),
    postSelfImproveResume: vi.fn().mockResolvedValue({ ok: true, paused: false, was_paused: true }),
    postClaudeCodeDefault: vi.fn().mockResolvedValue({ model: "x", previous: null, backup: null, path: "p" }),
    postAction: vi.fn().mockResolvedValue({ ok: true }),
    postReveal: vi.fn().mockResolvedValue({ ok: true }),
    postAiFix: vi.fn().mockResolvedValue({ ok: true, command_file: "/tmp/x.command" }),
    // §48.7 后台雷达行：launchd 说两个 agent 都「未安装」；点「重新安装」后回执说已加载（面板信回执）→
    // 「已安装，每 3 / 5 分钟自动运行」与「未安装」都渲染到
    fetchRadarAgents: vi.fn().mockResolvedValue({ radars: {
      gmail: { label: "com.zelin.aiassistant.gmailradar", interval_s: 300, loaded: false, plist_installed: false },
      slack: { label: "com.zelin.aiassistant.slackradar", interval_s: 180, loaded: false, plist_installed: false },
    } }),
    // 隔一个 macrotask 再回（同 verifySecret）：同一轮点击里「刷新」的重拉先落地，回执的 loaded 才是最后一笔
    postRadarReinstall: vi.fn((source: string) => new Promise((resolve) => setTimeout(() => resolve({ ok: true, source, label: `com.zelin.aiassistant.${source}radar`, loaded: true }), 0))),
    // §68.1 目录字段：创建失败一次（「创建目录失败：」这句才收得到），打开永远成功
    postFolderOpen: vi.fn().mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/Users/demo/Documents/Obsidian Vault/2 - raw" }),
    postFolderCreate: vi.fn().mockRejectedValue(new Error("could not create the folder: [Errno 13] Permission denied")),
  };
});

interface ControlItem {
  id: string;
  zh: string;
  en: string;
  role: string;
  screen: string;
  gated: boolean;
  owner: string;
}

const LANGUAGES: Language[] = ["zh", "en"];
// web 已有的页面 = 渲染面；原生 screen 前缀 → 该面。web 新开页面时在这两处登记，它的原生条目
// 才会按页判定；未登记的 screen 在全部面的并集里找（"any"）。setup 排在 permissions 之前：向导末步
// 首帧要在 store 还没有 permissions 快照时渲染一次，「检测中…」这类瞬态词才收得到。
const SURFACES = ["board", "trash", "settings", "about", "ask", "deps", "ingest", "setup", "permissions"] as const;
type Surface = (typeof SURFACES)[number];
const SCREEN_SURFACE: Array<[prefix: string, surface: Surface]> = [
  ["settings", "settings"],
  ["trash", "trash"],
  ["board", "board"],
  ["header", "board"],
  ["window", "board"],
  ["rail", "board"],
  ["shared", "board"],
  ["about", "about"],
  ["ask", "ask"],
  ["deps", "deps"],
  ["doctor", "deps"],           // Doctor.swift FailureCatalog 的对症动词：web 落在依赖检查页的 doctor 行上
  ["ingest", "ingest"],
  ["setup_wizard", "setup"],
  ["permissions", "permissions"],
];

function surfaceOf(screen: string): Surface | "any" {
  for (const [prefix, surface] of SCREEN_SURFACE) {
    if (screen === prefix || screen.startsWith(prefix + ".")) return surface;
  }
  return "any";
}

/** 账本文本 → id 集合（# 注释与空行忽略，每行首个 token 是 id）。 */
function ledgerIds(text: string): Set<string> {
  const ids = new Set<string>();
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    ids.add(line.split(/\s+/)[0]);
  }
  return ids;
}

const pending = ledgerIds(pendingText);
const waivers = ledgerIds(waiversText);
const controls = inventory.controls.filter((c) => c.gated && c.owner === "web");

const normalize = (s: string | null | undefined): string => (s ?? "").replace(/\s+/g, " ").trim();

/** 收集一棵 DOM 里所有可当「标签」的字符串：aria-label / title / placeholder / alt / value /
 *  元素自身的直接文本 / 交互与标题元素的整段文本（≈ accessible name）。 */
function collectLabels(root: ParentNode, into: Set<string>) {
  const add = (s: string | null | undefined) => {
    const t = normalize(s);
    if (t) into.add(t);
  };
  root.querySelectorAll("*").forEach((el) => {
    for (const attr of ["aria-label", "title", "placeholder", "alt"]) add(el.getAttribute(attr));
    if (el instanceof HTMLInputElement || el instanceof HTMLButtonElement || el instanceof HTMLOptionElement) {
      add(el.value);
    }
    add(Array.from(el.childNodes).filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent ?? "").join(""));
    if (/^(BUTTON|A|LABEL|H1|H2|H3|H4|H5|H6|SUMMARY|OPTION|TH|LEGEND|LI|SPAN|P|DT|DD)$/.test(el.tagName)
      || el.getAttribute("role")) {
      add(el.textContent);
    }
  });
}

/** 原生标签里的插值 `{expr}` → 宽松正则；无插值 → 精确匹配。 */
function matcher(label: string): (candidate: string) => boolean {
  const target = normalize(label);
  if (!target.includes("{")) return (c) => c === target;
  const pattern = target
    .split(/\{[^}]*\}/)
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join(".+?");
  const re = new RegExp("^" + pattern + "$");
  return (c) => re.test(c);
}

const emptyPools = (): Record<Surface, Set<string>> =>
  Object.fromEntries(SURFACES.map((s) => [s, new Set<string>()])) as Record<Surface, Set<string>>;
const found: Record<Language, Record<Surface, Set<string>>> = { zh: emptyPools(), en: emptyPools() };

function isFound(language: Language, surface: Surface | "any", label: string): boolean {
  const test = matcher(label);
  const pools = surface === "any" ? SURFACES.map((s) => found[language][s]) : [found[language][surface]];
  for (const pool of pools) for (const candidate of pool) if (test(candidate)) return true;
  return false;
}

function isPresent(control: ControlItem): { zh: boolean; en: boolean } {
  const surface = surfaceOf(control.screen);
  return { zh: isFound("zh", surface, control.zh), en: isFound("en", surface, control.en) };
}

const health: HealthSnapshot = {
  verdict: "ok",
  heartbeat: { age_s: 3, phase: "dashboard", pid: 4242, interval: 10, stale_after_s: 90, stale: false },
  dashboard: { generated_at: demoBoard.generated_at, age_s: 3, stale: false },
  loop_health: { consecutive_failures: 0, last_error: null },
  checked_at: demoBoard.generated_at,
};
const models: ModelsSettings = {
  dispatch: "follow",
  pipeline: "claude-opus-5",
  follow: "follow",
  canonical: ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
  source: { dispatch: "default", pipeline: "override" },
  warnings: [],
};
const ccDefault: ClaudeCodeDefault = {
  model: "claude-fable-5-1[1m]",
  path: "/Users/demo/.claude/settings.json",
  exists: true,
  parseable: true,
  canonical: false,
};
/** 第二遍看板：后台服务卡住（PipelineBanner 的修复 / 诊断动词）+ 空板 + 搜索中（空态文案） */
const stalledHealth: HealthSnapshot = {
  ...health,
  verdict: "stalled",
  heartbeat: { age_s: 900, phase: "dispatch", pid: 4242, interval: 10, stale_after_s: 90, stale: true },
  loop_health: { consecutive_failures: 4, last_error: "boom" },
};
const emptyBoard = {
  ...demoBoard,
  needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], archived: [], merge_suggestions: [],
  counts: { needs_approval: 0, running: 0, needs_input: 0, review: 0, completed: 0, debt: 0, trash: 0, archived: 0 },
} as unknown as Board;
const setup: SetupSnapshot = { needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/Users/demo/zai", protected_location: false };
const about: AboutInfo = {
  version: "0.48.29", home: "/Users/demo/zai", repo: "/Users/demo/Projects/zelin-ai-assistant",
  update_available: { latest: "0.48.30", url: "https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.48.30" },
  update_check: { checked_at: "2026-09-02T11:00:00Z" },
} as unknown as AboutInfo;
/** 词表行：act/lib/failures.py 里每个带 in-app 动作的 failure_id 各一行（原生 FailureCatalog.actionLabel 的对症动词全在场） */
const FAILURE_ROWS: DoctorRow[] = [
  "claude_cli_missing", "node_missing", "claude_cli_outdated", "claude_auth_failed", "engine_dead", "engine_npm_download",
  "engine_crashed", "engine_ffmpeg_missing", "screen_tcc_lost", "agent_unloaded", "cron_missing", "interpreter_blind",
  "cron_fda_blocked", "config_invalid",
].map((id) => ({ name: `_vocab_${id}`, status: "FAIL", detail: id, fix: "see docs", failure_id: id, action_id: "" }));
const diagnostics: DiagnosticsSnapshot = {
  doctor: { ok: true, checks: [{ name: "python", status: "OK", detail: "3.9", fix: null }, { name: "claude", status: "FAIL", detail: "missing", fix: "install" }, ...FAILURE_ROWS], home: "/h", rc: 0, fast: true, ran_at: "2026-09-02T11:59:00Z" },
  health, deploy_state: null, radar_sources: demoBoard.radar_sources ?? null, install_report: null, registry_backend: "yaml",
  logs: [{ name: "actd.log", path: "/h/state/logs/actd.log", size: 2048, mtime: 1788350000 }],
} as unknown as DiagnosticsSnapshot;

/** 权限体检 / 向导的 server 半边：FDA 清单 + TCC 相关 doctor 行（cron disk access 三态各一份）+ 笔记库被动探针 */
function permissionsFixture(cron: "ok" | "blocked" | "stale" | "none"): PermissionsSnapshot {
  const cronRow: DoctorRow[] = cron === "none" ? [] : [cron === "ok"
    ? { name: "cron disk access", status: "OK", detail: "cron read ok (probe 12 min ago)", fix: "" }
    : cron === "blocked"
      ? { name: "cron disk access", status: "FAIL", detail: "cron CANNOT read the vault - Full Disk Access is blocking it", fix: "System Settings > Privacy & Security > Full Disk Access > + > /usr/sbin/cron", failure_id: "cron_fda_blocked" }
      : { name: "cron disk access", status: "WARN", detail: "last cron probe 5h ago - the cron chain looks stopped", fix: "bash install.sh", failure_id: "cron_missing" }];
  return {
    home: "/Users/demo/zai", on_external_volume: false,
    fda: { needed: cron === "blocked", pane: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
      executables: [
        { role: "daemon_python", path: "/usr/bin/python3", realpath: "/usr/bin/python3", exists: true, note: { zh: "守护进程解释器", en: "Daemon interpreter" } },
        { role: "claude", path: "/Users/demo/.local/bin/claude", realpath: "/Users/demo/.local/share/claude/1.0.0/claude", exists: true, note: { zh: "claude CLI", en: "claude CLI" } },
        { role: "node", path: null, realpath: null, exists: false, note: { zh: "node", en: "node" } },
      ] },
    panes: { full_disk: "x", screen: "y", microphone: "z", notifications: "n", files_folders: "f" },
    doctor: [{ name: "launchd volume access", status: "FAIL", detail: "EPERM", fix: "grant FDA", failure_id: "deploy_blind_tcc" }, ...cronRow],
    doctor_ran_at: "2026-09-02T11:59:00Z", doctor_ok: true,
    vault: { status: cron === "ok" ? "granted" : "unknown", root: "/Users/demo/Documents/Obsidian Vault" },
  };
}
/** AI 引擎检测三态：就绪（Claude Code 登录）/ 没装 CLI / 装了没登录 */
const ENGINES: Record<"ready" | "no_cli" | "no_auth", SetupEngine> = {
  ready: { cli_path: "/Users/demo/.local/bin/claude", version: "1.0.99 (Claude Code)", auth: "oauth", auth_sources: { oauth: true, env_key: false, secrets_file: true, legacy_file: false }, ready: true },
  no_cli: { cli_path: null, version: null, auth: null, auth_sources: { oauth: false, env_key: false, secrets_file: false, legacy_file: false }, ready: false },
  no_auth: { cli_path: "/opt/homebrew/bin/claude", version: "1.0.99 (Claude Code)", auth: null, auth_sources: { oauth: false, env_key: false, secrets_file: false, legacy_file: false }, ready: false },
};
/** 假 zaiShell 桥：录制开着、字幕开着且已暂停（header 两开关 + 设置录制 / 字幕区 + 关于页壳区都有得渲染） */
const shellState: ShellState = {
  recording: { available: true, on: true, mode: "screen_audio", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
  captions: { available: true, on: true, engine: "doubao", paused: true, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: true, translate_direction: "auto", apple_locale: "zh", ark_model: "doubao-seed-1-6-flash", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "granted", vault: "unknown" },
  launch_at_login: true,
  hotkey: "⌃⌥Space",
  language: "en",
};
/** 假壳只认 §61.1 的既有方法词表；`chooseFolder`（文件对话框）按老壳的样子 reject UNKNOWN_METHOD ——
 *  目录字段因此退化成路径文本框，原生 NSOpenPanel 的确认词「选择」在 DOM 里才收得到 */
const FAKE_SHELL_METHODS = new Set(["getState", "setRecording", "restartRecording", "openScreenRecordingSettings",
  "setCaptions", "setLanguage", "getPermissions", "requestPermission", "openPane", "setLaunchAtLogin", "setCaptionPrefs", "setBadge"]);
/** 权限体检 / 向导要收全的壳状态词：录制 关（两种恢复模式）/ 开但引擎没在录 / 录制中(仅屏幕)；授权 denied / unknown */
const SHELL_VARIANTS: Record<string, ShellState> = {
  default: shellState,
  off_audio: { ...shellState, recording: { ...shellState.recording, on: false, mode: "off", engine_running: false, resume_mode: "screen_audio" },
    permissions: { screen: "denied", microphone: "denied", notifications: "denied", vault: "denied" } },
  off_screen: { ...shellState, recording: { ...shellState.recording, on: false, mode: "off", engine_running: false, resume_mode: "screen" },
    permissions: { screen: "denied", microphone: "unknown", notifications: "unknown", vault: "unknown" } },
  on_dead: { ...shellState, recording: { ...shellState.recording, on: true, mode: "screen", engine_running: false, screen_permission: false, resume_mode: "screen" },
    permissions: { screen: "denied", microphone: "unknown", notifications: "unknown", vault: "unknown" } },
  on_screen: { ...shellState, recording: { ...shellState.recording, on: true, mode: "screen", engine_running: true, resume_mode: "screen" } },
  tcc_lost: { ...shellState, recording: { ...shellState.recording, on: true, mode: "screen", engine_running: true, tcc_lost: true, screen_permission: false, resume_mode: "screen" } },
};
let currentShell: ShellState = shellState;
function installFakeShell() {
  window.webkit = { messageHandlers: { zaiShell: { postMessage: async (body: unknown) => {
    const method = (body as { method?: string } | null)?.method ?? "";
    if (!FAKE_SHELL_METHODS.has(method)) throw new Error(`UNKNOWN_METHOD: ${method}`);
    return currentShell;
  } } } };
}
/** 换一套壳状态：假 handler 之后都答它，并直接推进桥的小店（页面读 useShellState） */
function useShellVariant(name: keyof typeof SHELL_VARIANTS) {
  currentShell = SHELL_VARIANTS[name];
  applyShellState(currentShell);
}

/** 依次点；给了 pool 就每点一下收一遍——in-flight 的忙态词（验证中… / 启动中…）只活到下一颗按钮把这一步卸掉之前 */
function clickAll(buttons: Iterable<HTMLButtonElement>, pool?: Set<string>) {
  for (const button of buttons) {
    try {
      fireEvent.click(button);
    } catch {
      /* 某些按钮依赖 jsdom 没有的 API（clipboard / navigation）——忽略，只收文案 */
    }
    if (pool) collectLabels(document.body, pool);
  }
}

/** 只开弹窗 / 菜单、不提交动作的按钮（原生同名动词）：先点它们，弹窗文案才收得到 */
const OPENS_DIALOG = /拒绝|Reject|修改|Comment|打回|Send Back|停止|Stop|提建议|feedback|改名|Rename|强制合并|Force-merge|仍然合并|Merge anyway|评论|回答|Answer|清理积压|Clean up|不需要执行|No need to run|退回|Discard|选择|Select/;

/** 把页面上每颗按钮点一遍（弹窗 / 折叠详情 / 菜单展开后的文案也要收），失败的点击静默跳过。
 *  三轮：① 「展开详情 ▸」② 开弹窗的按钮 ③ 其余。动作按钮（批准 / 暂缓…）会把卡切到 pending 态并
 *  卸掉整个动作行——之后再点同卡的按钮就点在脱离 DOM 的旧节点上，所以提交类放最后；
 *  ③ 跳过 toggle，别把刚展开的又收起。 */
/** 给每个空输入框填点字：提交类按钮（提问 / 保存 / 发送）在空输入时是 disabled 的；数字框填 -1 让校验句出现；
 *  搜索框填一个不可能命中的词让「无匹配」空态出现。 */
function fillInputs(root: ParentNode, searches: boolean) {
  root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input[type="text"], input[type="password"], input:not([type]), textarea').forEach((el) => {
    if (!el.value) fireEvent.change(el, { target: { value: "demo" } });
  });
  root.querySelectorAll<HTMLInputElement>('input[type="number"]').forEach((el) => fireEvent.change(el, { target: { value: "-1" } }));
  // 搜索框会把整块列表过滤空——只在「空态」那几遍填（看板主遍要让卡都在场）
  if (!searches) return;
  root.querySelectorAll<HTMLInputElement>('input[type="search"]').forEach((el) => {
    if (!el.value) fireEvent.change(el, { target: { value: "zzz-no-such-card" } });
  });
}

function clickEverything(root: ParentNode, pool: Set<string>, searches = false, perClick = false) {
  const all = () => Array.from(root.querySelectorAll<HTMLButtonElement>("button"));
  fillInputs(root, searches);
  clickAll(all().filter((b) => b.classList.contains("card-details-toggle")));
  collectLabels(document.body, pool);
  clickAll(all().filter((b) => !b.classList.contains("card-details-toggle") && OPENS_DIALOG.test(b.textContent ?? "")));
  collectLabels(document.body, pool); // 弹窗开着时收：弹窗标题 / 选项 / 提交键
  // perClick（向导 / 权限体检）：每点一下收一遍——「启动中…」「验证中…」只活到下一颗按钮把这一步换掉之前；
  // 向导页脚的 上一步 / 下一步 / 完成 不点（会把当前步卸掉，先验后存的「✅ key 有效,已保存」就落不到 DOM 上）——它们按文本判
  const isWizardNav = (b: HTMLButtonElement) => perClick && b.closest(".setup-footer") !== null;
  clickAll(all().filter((b) => !b.classList.contains("card-details-toggle") && !OPENS_DIALOG.test(b.textContent ?? "") && !isWizardNav(b)), perClick ? pool : undefined);
  collectLabels(document.body, pool); // 点击后立刻收：in-flight 的忙态文案（保存中… / 正在准备诊断包…）
  fillInputs(root, searches); // 点开后才出现的输入框（书立条里的搜索框）
  collectLabels(document.body, pool);
}

const PAGES: Record<Surface, () => JSX.Element> = {
  board: () => <BoardPage />,
  trash: () => <TrashPage />,
  settings: () => <SettingsPage />,
  about: () => <AboutPage />,
  ask: () => <AskPage />,
  deps: () => <DiagnosticsPage />,
  ingest: () => <IngestPage />,
  setup: () => <SetupPage />,
  permissions: () => <PermissionsPage />,
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** 渲染 + 让页内的 useEffect 数据拉取（mock 即时 resolve）落地：几拍 microtask 足够。
 *  每拍都收一遍标签：保存 → 验证 → 通过 这类多段异步的中间态文案（已保存，验证中… / 验证中…）只在某一拍存在。 */
async function settle(pool?: Set<string>) {
  for (let i = 0; i < 4; i += 1) {
    await tick();
    if (pool) collectLabels(document.body, pool);
  }
}

/** 渲染一面（可带 query，如向导的 &step=）：首帧同步收一遍（「检测中…」这类拉取前的瞬态词），再等数据落地 */
function mount(language: Language, page: Surface, query = "") {
  const pool = found[language][page];
  window.history.replaceState(null, "", (page === "board" ? "/" : `/?page=${page}`) + query);
  const view = render(
    <LanguageContext.Provider value={language}>
      <AppShell searchSlot={<FilterBar />}>
        {PAGES[page]()}
        <DetailDrawer />
      </AppShell>
    </LanguageContext.Provider>,
  );
  collectLabels(document.body, pool);
  return view;
}

/** 初始设置向导：一屏一步，按 ?step= 逐步渲染（末步先来一遍——store 还空着时「检测中…」才在） */
async function renderSetupSteps(language: Language, steps: readonly string[]) {
  const pool = found[language].setup;
  for (const step of steps) {
    const view = mount(language, "setup", `&step=${step}`);
    await settle(pool);
    collectLabels(document.body, pool);
    clickEverything(view.container, pool, true, true);
    await settle(pool);
    await settle(pool); // 先验后存：验证 → 保存 → 「✅ key 有效,已保存」再晚一拍
    collectLabels(document.body, pool);
    cleanup();
    setLanguage(language); // 向导第 1 步的语言单选会把 store 语言切走，判卷面的语言由 Provider 定——切回
  }
}

async function renderSurface(language: Language, page: Surface) {
  const pool = found[language][page];
  if (page === "setup") {
    await renderSetupSteps(language, ["finale", ...SETUP_STEPS]);
    return;
  }
  const view = mount(language, page);
  if (page === "settings") await refreshSettings();
  await settle(pool);
  collectLabels(document.body, pool);
  clickEverything(view.container, pool, page !== "board");
  await settle(pool);
  if (page === "board") {
    // 详情抽屉：选中 hero 卡、再选一张待验收卡（抽屉里的字段标题 / 动作 / 所属列章）
    for (const id of [demoBoard.needs_approval[0].id, demoBoard.review[0].id]) {
      selectCard(id);
      await settle(pool);
      collectLabels(document.body, pool);
      clickEverything(document.body, pool);
      await settle(pool);
    }
  }
  collectLabels(document.body, pool);
  cleanup();
}

/** 第二 / 三遍看板与回收站：空板 + 搜索中 + 后台服务「卡住」/「没在跑」且一键修复失败 →
 *  空态文案（无匹配卡片 / 回收站为空）与横幅动词（一键修复 / 启动后台服务 / 再试一次 / 手动命令） */
async function renderEmptyBoardVariants(language: Language) {
  const { postRepairActd } = await import("./api");
  // 这两遍里一键修复永远失败（横幅的失败态动词才渲染）；遍完恢复成功态
  vi.mocked(postRepairActd).mockRejectedValue(new Error("launchctl kickstart failed (exit 113)"));
  // 不用 mockResolvedValueOnce：一键修复 / 向导「启动后台服务」成功后 3 s 会再拉一次 health（真定时器），
  // 一次性的假值会被那一拉吃掉、这一遍就渲染不出横幅——按遍设值、遍完复原
  for (const health2 of [stalledHealth, { ...stalledHealth, verdict: "stale", heartbeat: null }]) {
    vi.mocked(fetchBoard).mockResolvedValue(emptyBoard);
    vi.mocked(fetchHealth).mockResolvedValue(health2);
    await refreshBoard();
    await refreshHealth();
    setFilters({ search: "zzz" });
    for (const page of ["board", "trash"] as const) {
      const pool = found[language][page];
      window.history.replaceState(null, "", page === "board" ? "/" : "/?page=trash");
      const view = render(
        <LanguageContext.Provider value={language}>
          <AppShell searchSlot={<FilterBar />}>{PAGES[page]()}<DetailDrawer /></AppShell>
        </LanguageContext.Provider>,
      );
      await settle(pool);
      collectLabels(document.body, pool);
      clickEverything(view.container, pool, true);
      await settle(pool);
      await settle(pool); // 一键修复的 reject → 失败态文案再晚一拍也收得到
      collectLabels(document.body, pool);
      cleanup();
    }
    setFilters({ search: "" });
  }
  vi.mocked(postRepairActd).mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
  vi.mocked(fetchBoard).mockResolvedValue(demoBoard as unknown as Board);
  vi.mocked(fetchHealth).mockResolvedValue(health);
  await refreshBoard();
  await refreshHealth();
}

/** 权限体检 + 向导（录制步 / 末步 / 引擎步）在其余壳状态与 server 快照下再渲染几遍：
 *  录制 关 → 开启(屏幕+音频) / 开启(仅屏幕)；开着没在录 → 已开启,引擎未在录制 + 启动引擎；被拒 → 未授权 / 打开系统设置；
 *  引擎没装 → 安装命令 / 打开安装页；装了没登录 → 在终端运行；后台服务没跑 → 启动后台服务；没有首份数据 → 立即生成一次；
 *  cron 被挡 → 去授权 / 停跑 → 查看诊断 / 没探针 → 中性一句。 */
async function renderPermissionVariants(language: Language) {
  const staleHealth: HealthSnapshot = { ...health, verdict: "stale", heartbeat: null, dashboard: null };
  const noAgeHealth = { ...health, dashboard: { generated_at: demoBoard.generated_at, age_s: null, stale: false } } as unknown as HealthSnapshot;
  // 末步的按钮按 DOM 顺序逐颗点：「去配置…」会跳回引擎步、卸掉后面的行——所以「引擎没就绪」只和「其余行都绿」同组。
  // fail = server 拒绝那一遍（启动失败: / 生成失败: / key 有效,但保存失败: / 保存失败: 这些失败前缀才渲染）；
  // network = 探针网络不通（手动「保存」走「先存、稍后验」那条路）；firstRun = 权限体检页脚「完成」（否则「关闭」）
  type Variant = { shell: keyof typeof SHELL_VARIANTS; engine: keyof typeof ENGINES; cron: "ok" | "blocked" | "stale" | "none"; health: HealthSnapshot; fail?: boolean; network?: boolean; firstRun?: boolean };
  const variants: Variant[] = [
    { shell: "off_audio", engine: "ready", cron: "blocked", health: staleHealth, fail: true, firstRun: true },
    { shell: "off_screen", engine: "no_auth", cron: "stale", health: noAgeHealth },                  // 粘贴 key → ✅ key 有效,已保存
    { shell: "on_dead", engine: "no_cli", cron: "none", health, fail: true, firstRun: true },          // key 有效,但保存失败:
    { shell: "on_screen", engine: "no_cli", cron: "ok", health, fail: true, network: true },           // 保存失败:（网络不通 → 先存 → 存不进）
    { shell: "tcc_lost", engine: "ready", cron: "ok", health },
  ];
  const { postRepairActd, postSeedDashboard, putSecret, verifySecret } = await import("./api");
  const verifyOk = () => new Promise((resolve) => setTimeout(() => resolve({ ok: true, network: false, detail: "ok", extra: {} }), 0));
  for (const v of variants) {
    useShellVariant(v.shell);
    vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINES[v.engine]);
    vi.mocked(fetchPermissions).mockResolvedValue(permissionsFixture(v.cron));
    vi.mocked(fetchHealth).mockResolvedValue(v.health);
    vi.mocked(fetchSetup).mockResolvedValue({ ...setup, needed: Boolean(v.firstRun), done: !v.firstRun });
    if (v.fail) {
      vi.mocked(postRepairActd).mockRejectedValue(new Error("com.zelin.aiassistant.actd is not loaded in launchd"));
      vi.mocked(postSeedDashboard).mockResolvedValue({ ok: false, rc: 1, error: "Traceback: boom" });
      vi.mocked(putSecret).mockRejectedValue(new Error("EACCES: config/secrets not writable"));
    } else {
      vi.mocked(postRepairActd).mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
      vi.mocked(postSeedDashboard).mockResolvedValue({ ok: true, rc: 0 });
      vi.mocked(putSecret).mockResolvedValue({ name: "anthropic-api-key.txt", label: { zh: "k", en: "k" }, present: true, verifiable: true, mtime: 1 } as never);
    }
    vi.mocked(verifySecret).mockImplementation((v.network
      ? () => Promise.resolve({ ok: false, network: true, detail: "dns down", extra: {} })
      : verifyOk) as never);
    await refreshHealth();
    await refreshPermissions(true);
    const pool = found[language].permissions;
    const view = mount(language, "permissions");
    await settle(pool);
    collectLabels(document.body, pool);
    clickEverything(view.container, pool, true, true);  // 首颗「开启」答掉同意 → 状态行（开启(…) / 关闭）同一遍收到
    await settle(pool);
    collectLabels(document.body, pool);
    cleanup();
    await renderSetupSteps(language, ["engine", "recording", "finale"]);
  }
  useShellVariant("default");
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINES.ready);
  vi.mocked(fetchPermissions).mockResolvedValue(permissionsFixture("ok"));
  vi.mocked(fetchHealth).mockResolvedValue(health);
  vi.mocked(fetchSetup).mockResolvedValue(setup);
  vi.mocked(putSecret).mockResolvedValue({} as never);
  vi.mocked(verifySecret).mockImplementation(verifyOk as never);
  await refreshHealth();
  await refreshPermissions(true);
}

/** 看板诊断条（原生 DiagnosticsStrip）：Gmail / Slack / 录制链每种 skip_reason 一张卡 + 一颗修复按钮 */
async function renderDiagnosticsVariants(language: Language) {
  const on = (skip_reason: string | null, last_ok: string | null = null): RadarSourceHealth => ({ enabled: true, last_ok, last_attempt: "2026-09-02T11:57:00Z", skip_reason, stale: false });
  // vault_empty 要等满一个 ingest 周期才报——把「首见」时间拨到 36 分钟前
  const firstSeen = Object.fromEntries(["screenpipe:vault_empty.engine", "screenpipe:vault_empty.tcc", "screenpipe:vault_empty.other"].map((sig) => [sig, Date.now() - 36 * 60_000]));
  window.localStorage.setItem("diagnosticsFirstSeen", JSON.stringify(firstSeen));
  const variants: Array<{ shell: keyof typeof SHELL_VARIANTS; sources: Record<string, RadarSourceHealth> }> = [
    { shell: "default", sources: { gmail: on("auth_failed", "2026-09-01T10:00:00Z"), slack: on("connect_failed"), obsidian: on("no_api_key") } },
    { shell: "default", sources: { gmail: on("no_credentials"), slack: on("mcp_not_configured"), obsidian: on("vault_missing") } },
    { shell: "default", sources: { gmail: on("fetch_command_failed"), slack: on(null), obsidian: on("extract_failed") } },
    { shell: "on_dead", sources: { obsidian: on("vault_empty") } },
    { shell: "tcc_lost", sources: { obsidian: on("vault_empty") } },
    { shell: "on_screen", sources: { obsidian: on("vault_empty") } },
  ];
  for (const v of variants) {
    useShellVariant(v.shell);
    vi.mocked(fetchBoard).mockResolvedValue({ ...demoBoard, radar_sources: v.sources } as unknown as Board);
    await refreshBoard();
    const pool = found[language].board;
    mount(language, "board");
    await settle(pool);
    collectLabels(document.body, pool);
    cleanup();
  }
  useShellVariant("default");
  vi.mocked(fetchBoard).mockResolvedValue(demoBoard as unknown as Board);
  await refreshBoard();
}

beforeAll(async () => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: () => Promise.resolve() },
  });
  // 时钟冻结在 fixture 的 FIXED_NOW：相对时间（刚刚 / N分钟前 / N天）与截止倒数才是确定的
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(demoBoard.generated_at));
  installFakeShell();
  vi.mocked(fetchBoard).mockResolvedValue(demoBoard);
  vi.mocked(fetchCard).mockImplementation(async (id: string) => ({ id, notes: "demo notes\n2026-09-01T10:00:00Z 追加：又问了一次", log_tail: "ok" }));
  vi.mocked(fetchHealth).mockResolvedValue(health);
  vi.mocked(fetchLanes).mockResolvedValue(demoLanes);
  vi.mocked(fetchModelsSettings).mockResolvedValue(models);
  vi.mocked(fetchClaudeCodeDefault).mockResolvedValue(ccDefault);
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(demoSettings);
  vi.mocked(fetchSecrets).mockResolvedValue(demoSecrets);
  vi.mocked(fetchSetup).mockResolvedValue(setup);
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINES.ready);
  vi.mocked(fetchPermissions).mockResolvedValue(permissionsFixture("ok"));
  vi.mocked(fetchAbout).mockResolvedValue(about);
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics);
  vi.mocked(fetchMcp).mockResolvedValue({ scopes: [{ scope: "user", path: "/Users/demo/.claude.json", exists: true, parseable: true, servers: [{ name: "slack", transport: "stdio", command: "npx", args: ["slack-mcp"], env_count: 1, incomplete: false }, { name: "broken", transport: "stdio", command: "", args: [], env_count: 0, incomplete: true }] }, { scope: "project", path: "/h/.mcp.json", exists: false, parseable: true, servers: [] }] } as never);
  vi.mocked(fetchClaudeSessions).mockResolvedValue({ ok: true, window: 7, root: "/Users/demo/.claude/projects", candidates: [{ session_id: "abc12345-0000-4000-8000-000000000001", project: "example-bench", title: "修 flaky 测试", last_activity: "2026-09-01T10:00:00Z", ended_waiting_on_user: true, answered: false, session_mismatch: false }, { session_id: "abc12345-0000-4000-8000-000000000002", project: "inkweld", title: "问答", last_activity: "2026-08-30T10:00:00Z", ended_waiting_on_user: false, answered: true, session_mismatch: false }] } as never);
  vi.mocked(fetchAskHistory).mockResolvedValue({ items: [{ q: "为什么没有新卡片？", a: "雷达每 3 分钟扫一次。", citation: "docs/TROUBLESHOOTING.md", ts: "2026-09-02T11:30:00Z", elapsed_s: 4.2 }] });
  for (const language of LANGUAGES) {
    resetStoreForTests();
    resetShellBridgeForTests();
    useShellVariant("default");
    setLanguage(language);
    await refreshBoard();
    await refreshHealth();
    await refreshLanes();
    for (const page of SURFACES) {
      await renderSurface(language, page);
    }
    await renderEmptyBoardVariants(language);
    await renderPermissionVariants(language);
    await renderDiagnosticsVariants(language);
  }
  // 九个面 × 两种语言 × 若干状态变体：几十次整页渲染（单机 ~12 s），远超 vitest 默认 10 s 的 hook 预算
}, 120_000);

afterAll(() => {
  cleanup();
  vi.useRealTimers();
  delete window.webkit;
});

describe("native → web control parity (ui/parity/native-inventory.json)", () => {
  // 调试用：VITE_PARITY_DEBUG=<文件路径> npx vitest run src/parity.test.tsx -t dump → 每个面收到的全部标签倒进该文件
  const debugPath = (import.meta as unknown as { env: Record<string, string | undefined> }).env.VITE_PARITY_DEBUG;
  if (debugPath) {
    it("dump", async () => {
      const fsModule = "node:fs";
      const fs = (await import(/* @vite-ignore */ fsModule)) as { writeFileSync: (path: string, text: string) => void };
      const out: Record<string, unknown> = {};
      for (const surface of SURFACES) out[surface] = { zh: Array.from(found.zh[surface]).sort(), en: Array.from(found.en[surface]).sort() };
      fs.writeFileSync(debugPath, JSON.stringify(out, null, 1));
    });
  }

  it("清单与账本都读到了（防空转：0 条 it 也会「全绿」）", () => {
    expect(controls.length).toBeGreaterThan(100);
    expect(found.zh.board.size).toBeGreaterThan(50);
    expect(found.en.board.size).toBeGreaterThan(50);
  });

  for (const control of controls) {
    if (waivers.has(control.id)) {
      it.skip(`${control.id} [waived]`, () => undefined);
    } else if (pending.has(control.id)) {
      it(`${control.id} [pending]`, () => {
        // 断言「不在」：一旦 web 补齐了这条，必须同 PR 从 pending.txt 划掉（账本只许缩）
        const present = isPresent(control);
        expect(present.zh && present.en, `${control.id} is now present in the web — strike it from ui/parity/pending.txt`).toBe(false);
      });
    } else {
      it(control.id, () => {
        const present = isPresent(control);
        expect(present.zh, `zh label not rendered on the ${surfaceOf(control.screen)} surface: ${JSON.stringify(control.zh)}`).toBe(true);
        expect(present.en, `en label not rendered on the ${surfaceOf(control.screen)} surface: ${JSON.stringify(control.en)}`).toBe(true);
      });
    }
  }
});
