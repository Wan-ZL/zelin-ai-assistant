// UI 对齐判例（CONTRACT §66.2）—— 由 ui/parity/native-inventory.json 驱动，不手写列表。
// 原生 mac/Sources（D3 冻结）的每个 gated `control:*` 条目在这里变成一条 it()：
//   · 不在 pending/waivers 上 → 断言标签「在」：用 demo fixture 渲染看板 / 回收站 / 设置 / 关于 / 问问助手 /
//     依赖检查 / 录制与数据接入 七个面（zh 与 en 各一遍；看板另渲染一遍「空板 + 搜索中 + 后台服务卡住」
//     收空态与横幅文案）+ 把每颗按钮点一遍收集弹窗文案，按 accessible name / 自身文本精确匹配；
//     时钟冻结在 fixture 的 FIXED_NOW（相对时间词表才确定）；装一个假 zaiShell 桥（壳里才渲染的
//     录制 / 字幕 / 登录时启动 开关也要判）；server 目录（设置 / 凭证）用 fixture 快照（文案 server-owned）。
//   · 在 ui/parity/pending.txt 上 → it 标题带 ` [pending]`，断言「不在」——补齐后不划账即红
//     （与 qa/*_baseline.txt 同一 shrink-only 语义）；
//   · 在 ui/parity/waivers.txt 上 → it.skip（报告计 WAIVED）。
// scripts/ui/parity_check.py 以 --reporter=json 跑本文件、按 it 标题读判决；两边读同两本账本，
// 判决一致。双语都要命中（原生 L("zh","en") 是逐字规格，PR #143「逐字镜像」同理）。
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import inventory from "../../ui/parity/native-inventory.json";
import demoBoard from "../../ui/parity/fixtures/demo-board.json";
import demoLanes from "../../ui/parity/fixtures/lanes.json";
import demoSettings from "../../ui/parity/fixtures/settings.json";
import demoSecrets from "../../ui/parity/fixtures/secrets.json";
import pendingText from "../../ui/parity/pending.txt?raw";
import waiversText from "../../ui/parity/waivers.txt?raw";
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
  fetchSecrets,
  fetchSettingsCatalog,
  fetchSetup,
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
import { SettingsPage } from "./pages/SettingsPage";
import { TrashPage } from "./pages/TrashPage";
import { resetShellBridgeForTests, type ShellState } from "./shellBridge";
import {
  refreshBoard,
  refreshHealth,
  refreshLanes,
  refreshSettings,
  resetStoreForTests,
  selectCard,
  setFilters,
  setLanguage,
} from "./store";
import type { AboutInfo, Board, ClaudeCodeDefault, DiagnosticsSnapshot, HealthSnapshot, ModelsSettings, SetupSnapshot } from "./types";

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
    verifySecret: vi.fn().mockResolvedValue({ ok: true, network: false, detail: "ok", extra: {} }),
    postSetupStep: vi.fn().mockResolvedValue({ ok: true, setup: { needed: false, done: true } }),
    postUpdateCheck: vi.fn().mockResolvedValue({ ok: true, checked_at: "2026-09-02T11:00:00Z", update_available: false, latest: "0.48.30" }),
    postAsk: vi.fn().mockResolvedValue({ ok: true, answer: "42", citation: "README", lang: "en", elapsed_s: 1 }),
    postTerminal: vi.fn().mockResolvedValue({ ok: true }),
    postUninstallTerminal: vi.fn().mockResolvedValue({ ok: true, command: "cd /r && bash uninstall.sh", command_file: "/tmp/u.command" }),
    postRepairActd: vi.fn().mockResolvedValue({ ok: true }),
    postSelfImproveResume: vi.fn().mockResolvedValue({ ok: true, paused: false, was_paused: true }),
    postClaudeCodeDefault: vi.fn().mockResolvedValue({ model: "x", previous: null, backup: null, path: "p" }),
    postAction: vi.fn().mockResolvedValue({ ok: true }),
    postReveal: vi.fn().mockResolvedValue({ ok: true }),
    postAiFix: vi.fn().mockResolvedValue({ ok: true, command_file: "/tmp/x.command" }),
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
// 才会按页判定；未登记的 screen（permissions / setup_wizard…）在全部面的并集里找（"any"）。
const SURFACES = ["board", "trash", "settings", "about", "ask", "deps", "ingest"] as const;
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
  ["ingest", "ingest"],
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
const controls = (inventory.controls as ControlItem[]).filter((c) => c.gated && c.owner === "web");

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
const diagnostics: DiagnosticsSnapshot = {
  doctor: { ok: true, checks: [{ name: "python", status: "OK", detail: "3.9", fix: null }, { name: "claude", status: "FAIL", detail: "missing", fix: "install" }], home: "/h", rc: 0, fast: true, ran_at: "2026-09-02T11:59:00Z" },
  health, deploy_state: null, radar_sources: demoBoard.radar_sources ?? null, install_report: null, registry_backend: "yaml",
  logs: [{ name: "actd.log", path: "/h/state/logs/actd.log", size: 2048, mtime: 1788350000 }],
} as unknown as DiagnosticsSnapshot;
/** 假 zaiShell 桥：录制开着、字幕开着且已暂停（header 两开关 + 设置录制 / 字幕区 + 关于页壳区都有得渲染） */
const shellState: ShellState = {
  recording: { available: true, on: true, mode: "screen_audio", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
  captions: { available: true, on: true, engine: "doubao", paused: true, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: true, translate_direction: "auto", apple_locale: "zh", ark_model: "doubao-seed-1-6-flash", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "granted" },
  launch_at_login: true,
  hotkey: "⌃⌥Space",
  language: "en",
};
function installFakeShell() {
  window.webkit = { messageHandlers: { zaiShell: { postMessage: async () => shellState } } };
}

function clickAll(buttons: Iterable<HTMLButtonElement>) {
  for (const button of buttons) {
    try {
      fireEvent.click(button);
    } catch {
      /* 某些按钮依赖 jsdom 没有的 API（clipboard / navigation）——忽略，只收文案 */
    }
  }
}

/** 只开弹窗 / 菜单、不提交动作的按钮（原生同名动词）：先点它们，弹窗文案才收得到 */
const OPENS_DIALOG = /拒绝|Reject|修改|Comment|打回|Send Back|停止|Stop|提建议|feedback|改名|Rename|强制合并|Force-merge|仍然合并|Merge anyway|评论|回答|Answer|清理积压|Clean up|不需要执行|No need to run|退回|Discard|选择|Select/;

/** 把页面上每颗按钮点一遍（弹窗 / 折叠详情 / 菜单展开后的文案也要收），失败的点击静默跳过。
 *  三轮：① 「展开详情 ▸」② 开弹窗的按钮 ③ 其余。动作按钮（批准 / 暂缓…）会把卡切到 pending 态并
 *  卸掉整个动作行——之后再点同卡的按钮就点在脱离 DOM 的旧节点上，所以提交类放最后；
 *  ③ 跳过 toggle，别把刚展开的又收起。 */
function clickEverything(root: ParentNode, pool: Set<string>) {
  const all = () => Array.from(root.querySelectorAll<HTMLButtonElement>("button"));
  // 先给每个空文本框填点字：提交类按钮（提问 / 保存 / 发送）在空输入时是 disabled 的，点了没反应
  root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input[type="text"], input[type="password"], input:not([type]), textarea').forEach((el) => {
    if (!el.value) fireEvent.change(el, { target: { value: "demo" } });
  });
  clickAll(all().filter((b) => b.classList.contains("card-details-toggle")));
  collectLabels(document.body, pool);
  clickAll(all().filter((b) => !b.classList.contains("card-details-toggle") && OPENS_DIALOG.test(b.textContent ?? "")));
  collectLabels(document.body, pool); // 弹窗开着时收：弹窗标题 / 选项 / 提交键
  clickAll(all().filter((b) => !b.classList.contains("card-details-toggle") && !OPENS_DIALOG.test(b.textContent ?? "")));
  collectLabels(document.body, pool); // 点击后立刻收：in-flight 的忙态文案（保存中… / 正在准备诊断包…）
}

const PAGES: Record<Surface, () => JSX.Element> = {
  board: () => <BoardPage />,
  trash: () => <TrashPage />,
  settings: () => <SettingsPage />,
  about: () => <AboutPage />,
  ask: () => <AskPage />,
  deps: () => <DiagnosticsPage />,
  ingest: () => <IngestPage />,
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** 渲染 + 让页内的 useEffect 数据拉取（mock 即时 resolve）落地：几拍 microtask 足够 */
async function settle() {
  for (let i = 0; i < 4; i += 1) await tick();
}

async function renderSurface(language: Language, page: Surface) {
  const pool = found[language][page];
  window.history.replaceState(null, "", page === "board" ? "/" : `/?page=${page}`);
  const view = render(
    <LanguageContext.Provider value={language}>
      <AppShell searchSlot={<FilterBar />}>
        {PAGES[page]()}
        <DetailDrawer />
      </AppShell>
    </LanguageContext.Provider>,
  );
  if (page === "settings") await refreshSettings();
  await settle();
  collectLabels(document.body, pool);
  clickEverything(view.container, pool);
  await settle();
  if (page === "board") {
    // 详情抽屉：选中 hero 卡、再选一张待验收卡（抽屉里的字段标题 / 动作 / 所属列章）
    for (const id of [demoBoard.needs_approval[0].id, demoBoard.review[0].id]) {
      selectCard(id);
      await settle();
      collectLabels(document.body, pool);
      clickEverything(document.body, pool);
      await settle();
    }
  }
  collectLabels(document.body, pool);
  cleanup();
}

/** 第二遍看板：空板 + 搜索中 + 后台服务卡住 → 空态文案 / 横幅动词 */
async function renderEmptyStalledBoard(language: Language) {
  const pool = found[language].board;
  vi.mocked(fetchBoard).mockResolvedValueOnce(emptyBoard);
  vi.mocked(fetchHealth).mockResolvedValueOnce(stalledHealth);
  await refreshBoard();
  await refreshHealth();
  setFilters({ search: "zzz" });
  window.history.replaceState(null, "", "/");
  const view = render(
    <LanguageContext.Provider value={language}>
      <AppShell searchSlot={<FilterBar />}><BoardPage /><DetailDrawer /></AppShell>
    </LanguageContext.Provider>,
  );
  await settle();
  collectLabels(document.body, pool);
  clickEverything(view.container, pool);
  await settle();
  collectLabels(document.body, pool);
  cleanup();
  setFilters({ search: "" });
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
  vi.mocked(fetchBoard).mockResolvedValue(demoBoard as unknown as Board);
  vi.mocked(fetchCard).mockImplementation(async (id: string) => ({ id, notes: "demo notes\n2026-09-01T10:00:00Z 追加：又问了一次", log_tail: "ok" }));
  vi.mocked(fetchHealth).mockResolvedValue(health);
  vi.mocked(fetchLanes).mockResolvedValue(demoLanes);
  vi.mocked(fetchModelsSettings).mockResolvedValue(models);
  vi.mocked(fetchClaudeCodeDefault).mockResolvedValue(ccDefault);
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(demoSettings as never);
  vi.mocked(fetchSecrets).mockResolvedValue(demoSecrets as never);
  vi.mocked(fetchSetup).mockResolvedValue(setup);
  vi.mocked(fetchAbout).mockResolvedValue(about);
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics);
  vi.mocked(fetchMcp).mockResolvedValue({ scopes: [{ scope: "user", path: "/Users/demo/.claude.json", exists: true, parseable: true, servers: [{ name: "slack", transport: "stdio", command: "npx", args: ["slack-mcp"], env_count: 1, incomplete: false }, { name: "broken", transport: "stdio", command: "", args: [], env_count: 0, incomplete: true }] }, { scope: "project", path: "/h/.mcp.json", exists: false, parseable: true, servers: [] }] } as never);
  vi.mocked(fetchClaudeSessions).mockResolvedValue({ ok: true, window: 7, root: "/Users/demo/.claude/projects", candidates: [{ session_id: "abc12345-0000-4000-8000-000000000001", project: "example-bench", title: "修 flaky 测试", last_activity: "2026-09-01T10:00:00Z", ended_waiting_on_user: true, answered: false, session_mismatch: false }, { session_id: "abc12345-0000-4000-8000-000000000002", project: "inkweld", title: "问答", last_activity: "2026-08-30T10:00:00Z", ended_waiting_on_user: false, answered: true, session_mismatch: false }] } as never);
  vi.mocked(fetchAskHistory).mockResolvedValue({ items: [{ q: "为什么没有新卡片？", a: "雷达每 3 分钟扫一次。", citation: "docs/TROUBLESHOOTING.md", ts: "2026-09-02T11:30:00Z", elapsed_s: 4.2 }] });
  for (const language of LANGUAGES) {
    resetStoreForTests();
    resetShellBridgeForTests();
    setLanguage(language);
    await refreshBoard();
    await refreshHealth();
    await refreshLanes();
    for (const page of SURFACES) {
      await renderSurface(language, page);
    }
    await renderEmptyStalledBoard(language);
  }
});

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
