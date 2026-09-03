// UI 对齐判例（CONTRACT §63.2）—— 由 ui/parity/native-inventory.json 驱动，不手写列表。
// 原生 mac/Sources（D3 冻结）的每个 gated `control:*` 条目在这里变成一条 it()：
//   · 不在 pending/waivers 上 → 断言标签「在」：用 demo fixture 渲染看板 / 回收站 / 设置页
//     （zh 与 en 各一遍）+ 把每颗按钮点一遍收集弹窗文案，按 accessible name / 自身文本精确匹配；
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
import pendingText from "../../ui/parity/pending.txt?raw";
import waiversText from "../../ui/parity/waivers.txt?raw";
import {
  fetchBoard,
  fetchCard,
  fetchClaudeCodeDefault,
  fetchHealth,
  fetchLanes,
  fetchModelsSettings,
} from "./api";
import { AppShell } from "./components/shell/AppShell";
import { FilterBar } from "./components/chrome/FilterBar";
import { DetailDrawer } from "./components/detail/DetailDrawer";
import { LanguageContext, type Language } from "./i18n";
import { BoardPage } from "./pages/BoardPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TrashPage } from "./pages/TrashPage";
import {
  refreshBoard,
  refreshHealth,
  refreshLanes,
  refreshSettings,
  resetStoreForTests,
  selectCard,
  setLanguage,
} from "./store";
import type { Board, ClaudeCodeDefault, HealthSnapshot, ModelsSettings } from "./types";

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
    putModelsSettings: vi.fn().mockResolvedValue({}),
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
// web 已有的页面 = 渲染面；原生 screen 前缀 → 该面。web 新开页面（ask / deps / about…）时
// 在这两处登记，它的原生条目才会按页判定；未登记的 screen 在全部面的并集里找（"any"）。
const SURFACES = ["board", "trash", "settings"] as const;
type Surface = (typeof SURFACES)[number];
const SCREEN_SURFACE: Array<[prefix: string, surface: Surface]> = [
  ["settings", "settings"],
  ["trash", "trash"],
  ["board", "board"],
  ["header", "board"],
  ["window", "board"],
  ["rail", "board"],
  ["shared", "board"],
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

const found: Record<Language, Record<Surface, Set<string>>> = {
  zh: { board: new Set(), trash: new Set(), settings: new Set() },
  en: { board: new Set(), trash: new Set(), settings: new Set() },
};

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

/** 把页面上每颗按钮点一遍（弹窗 / 折叠详情 / 菜单展开后的文案也要收），失败的点击静默跳过。 */
function clickEverything(root: ParentNode) {
  root.querySelectorAll("button").forEach((button) => {
    try {
      fireEvent.click(button);
    } catch {
      /* 某些按钮依赖 jsdom 没有的 API（clipboard / navigation）——忽略，只收文案 */
    }
  });
}

async function renderSurface(language: Language, page: Surface) {
  const pool = found[language][page];
  window.history.replaceState(null, "", page === "board" ? "/" : `/?page=${page}`);
  const view = render(
    <LanguageContext.Provider value={language}>
      <AppShell searchSlot={<FilterBar />}>
        {page === "trash" ? <TrashPage /> : page === "settings" ? <SettingsPage /> : <BoardPage />}
        <DetailDrawer />
      </AppShell>
    </LanguageContext.Provider>,
  );
  if (page === "settings") {
    await refreshSettings();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  collectLabels(document.body, pool);
  clickEverything(view.container);
  if (page === "board") {
    // 详情抽屉：选中 hero 卡后再收一遍（抽屉里的字段标题 / 动作）
    selectCard(demoBoard.needs_approval[0].id);
    await new Promise((resolve) => setTimeout(resolve, 0));
    collectLabels(document.body, pool);
    clickEverything(document.body);
  }
  collectLabels(document.body, pool);
  cleanup();
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
  vi.mocked(fetchBoard).mockResolvedValue(demoBoard as unknown as Board);
  vi.mocked(fetchCard).mockImplementation(async (id: string) => ({ id, notes: "demo notes", log_tail: "ok" }));
  vi.mocked(fetchHealth).mockResolvedValue(health);
  vi.mocked(fetchLanes).mockResolvedValue(demoLanes);
  vi.mocked(fetchModelsSettings).mockResolvedValue(models);
  vi.mocked(fetchClaudeCodeDefault).mockResolvedValue(ccDefault);
  for (const language of LANGUAGES) {
    resetStoreForTests();
    setLanguage(language);
    await refreshBoard();
    await refreshHealth();
    await refreshLanes();
    for (const page of SURFACES) {
      await renderSurface(language, page);
    }
  }
});

afterAll(cleanup);

describe("native → web control parity (ui/parity/native-inventory.json)", () => {
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
