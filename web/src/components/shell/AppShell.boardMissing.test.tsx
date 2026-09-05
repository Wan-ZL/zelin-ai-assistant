// AppShell 的看板读失败分派（CONTRACT §49 / §54.1 / §68.8 追记；原生 Store.swift missing → Kanban.emptyState）：
//   - 只有读看板快照的页等 /api/board（看板 + 回收站 / 永久性完成 / 会议纪要，后三页见 AppShell.boardDependentPages.test.tsx）：
//     自拉快照的 ?page= 在读失败 / 404 时照常渲染 children（原生 MainWindow.detail 非看板 section 不看 dashboard）；
//   - 看板页 404（dashboard.json 不存在）≠ 离线：Freshness.swift PipelineEmptyStateView 文案 +「启动后台服务」+「打开依赖检查」
//     + web 的「立即生成一次」+ 提案列 composer（Kanban.swift：capture 不依赖管线跑过；⌘L / quick_capture 的共同落点
//     focusComposer 也找得到它，跨页接力棒由空态挂载时消费——原生 emptyState 的 KanbanComposer 同样收 .focusCaptureField）；
//     健康横幅同时闭嘴（.missing 归空态）；
//   - 看板页网络失败仍是「连不上本地服务」+ 重试。
// api 层 mock 掉，经 store 真实 action 驱动状态；零真实网络 / 子进程。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchHealth, postAction, postRepairActd, postSeedDashboard } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, refreshHealth, resetStoreForTests } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { focusComposer, focusComposerField, PENDING_FOCUS_KEY } from "../board/focusComposer";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return {
    ...mod,
    fetchBoard: vi.fn(),
    fetchCard: vi.fn(),
    fetchHealth: vi.fn(),
    postAction: vi.fn(),
    postRepairActd: vi.fn(),
    postSeedDashboard: vi.fn(),
  };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const fetchHealthMock = vi.mocked(fetchHealth);
const postActionMock = vi.mocked(postAction);
const postRepairActdMock = vi.mocked(postRepairActd);
const postSeedDashboardMock = vi.mocked(postSeedDashboard);

const MISSING_TITLE = "The background service hasn't produced data yet";
const OFFLINE_TITLE = "Can't reach the local server";

function makeBoard(): Board {
  return {
    generated_at: new Date().toISOString(),
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
  };
}

/** server/board_source.py 对缺席 dashboard.json 抛的 envelope 原形 */
const notFound = () => new ApiError(404, {
  error: { code: "NOT_FOUND", message: "dashboard.json not found — is actd (or the demo seeder) pointed at this AIASSISTANT_HOME?" },
});

/** api.ts 对 fetch 抛错合成的读失败 */
const readFailed = () => new ApiError(0, { error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." } });

const staleHealth: HealthSnapshot = {
  verdict: "stale",
  heartbeat: null,
  dashboard: null,
  loop_health: { consecutive_failures: 0, last_error: null },
  checked_at: "2026-09-05T08:00:05Z",
};

function renderShell(language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <AppShell>
        <div>page-content</div>
      </AppShell>
    </LanguageContext.Provider>,
  );
}

function goTo(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

describe("AppShell · dashboard.json 不存在（看板页）", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
    fetchHealthMock.mockReset();
    postActionMock.mockReset();
    postRepairActdMock.mockReset();
    postSeedDashboardMock.mockReset();
    goTo("");
  });

  afterEach(() => {
    cleanup();
    goTo("");
  });

  it("404 → 原生 PipelineEmptyStateView 文案，不是「连不上本地服务」；children 不渲染", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    await refreshBoard();
    renderShell();
    expect(screen.getByText(MISSING_TITLE)).toBeTruthy();
    expect(screen.getByText(/This happens on a fresh install or when the service isn't running/)).toBeTruthy();
    expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
    expect(screen.queryByText(/dev-preview\.sh/)).toBeNull();
    expect(screen.queryByText("page-content")).toBeNull();
  });

  it("zh：「后台服务还没写出数据」+「首次安装或服务未启动时会这样。点「启动后台服务」原地拉起它。」逐字", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    await refreshBoard();
    renderShell("zh");
    expect(screen.getByText("后台服务还没写出数据")).toBeTruthy();
    expect(screen.getByText("首次安装或服务未启动时会这样。点「启动后台服务」原地拉起它。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "启动后台服务" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开依赖检查" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "立即生成一次" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "捕获" })).toBeTruthy();
  });

  it("「启动后台服务」= RepairButton 的 stale 形（POST /api/repair/actd）；「打开依赖检查」→ ?page=settings&anchor=deps", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    postRepairActdMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    await refreshBoard();
    renderShell();
    const deps = screen.getByRole("link", { name: "Open dependency check" });
    const url = new URL(deps.getAttribute("href") ?? "", "http://127.0.0.1/");
    expect(url.searchParams.get("page")).toBe("settings");
    expect(url.searchParams.get("anchor")).toBe("deps");

    fireEvent.click(screen.getByRole("button", { name: "Start service" }));
    await vi.waitFor(() => expect(postRepairActdMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Starting and waiting for the first data…")).toBeTruthy();
  });

  it("「立即生成一次」= POST /api/setup/seed-dashboard；成功后重拉看板与健康", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    fetchHealthMock.mockResolvedValue(staleHealth);
    await refreshBoard();
    renderShell();
    expect(fetchBoardMock).toHaveBeenCalledTimes(1);

    postSeedDashboardMock.mockResolvedValue({ ok: true, rc: 0 });
    fetchBoardMock.mockResolvedValue(makeBoard());
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await vi.waitFor(() => expect(postSeedDashboardMock).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(fetchBoardMock).toHaveBeenCalledTimes(2));
    expect(fetchHealthMock).toHaveBeenCalled();
    // 文件写出来了 → 空态退场、页面渲染
    expect(await screen.findByText("page-content")).toBeTruthy();
    expect(screen.queryByText(MISSING_TITLE)).toBeNull();
  });

  it("「立即生成一次」server 回 ok:false / 抛错 → 「生成失败: 」+ 原文，空态留着", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    await refreshBoard();
    renderShell();

    postSeedDashboardMock.mockResolvedValue({ ok: false, rc: 1, error: "python3 -m act.lib.dashboard exited 1" });
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    const note = await screen.findByRole("alert");
    expect(note.textContent).toContain("Seeding failed: ");
    expect(note.textContent).toContain("python3 -m act.lib.dashboard exited 1");
    expect(screen.getByText(MISSING_TITLE)).toBeTruthy();

    postSeedDashboardMock.mockRejectedValue(new ApiError(501, { error: { code: "NOT_IMPLEMENTED", message: "darwin only" } }));
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toContain("darwin only"));
  });

  it("提案列 composer 在场且能捕获（Kanban.swift：inbox 写路径不依赖管线跑过）", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    postActionMock.mockResolvedValue({ ok: true });
    await refreshBoard();
    renderShell();
    const field = screen.getByPlaceholderText("One sentence — AI researches and proposes…");
    fireEvent.change(field, { target: { value: "写一份 onboarding 文档" } });
    fireEvent.click(screen.getByRole("button", { name: "Capture" }));
    await vi.waitFor(() => expect(postActionMock).toHaveBeenCalledWith({ action: "capture", text: "写一份 onboarding 文档" }));
    expect(await screen.findByText(/Submitted; AI is analyzing/)).toBeTruthy();
  });

  it("⌘L / quick_capture 的共同落点 focusComposer 在这一态也找得到 composer：光标到草稿末尾、不全选", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    await refreshBoard();
    renderShell();
    const field = screen.getByPlaceholderText<HTMLTextAreaElement>(/One sentence/);
    expect(document.activeElement).not.toBe(field);
    fireEvent.change(field, { target: { value: "draft" } });
    field.blur();

    focusComposer(); // rail ⌘L / 壳 quick_capture（app.tsx）都调它；看板页 → focusComposerField
    expect(document.activeElement).toBe(field);
    expect(field.selectionStart).toBe(5);
    expect(field.selectionEnd).toBe(5);
    expect(focusComposerField()).toBe(true);
  });

  it("从别的页按 ⌘L 过来（sessionStorage 接力棒）：空态挂载时消费它并聚焦 composer；刷新不重放", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    await refreshBoard();
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "composer");
    renderShell();
    const field = screen.getByPlaceholderText(/One sentence/);
    expect(document.activeElement).toBe(field);
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
    cleanup();

    // 没有接力棒：挂载不抢焦点
    renderShell();
    expect(document.activeElement).not.toBe(screen.getByPlaceholderText(/One sentence/));
  });

  it("健康横幅在这一态闭嘴（同一句「启动后台服务」不说两遍——原生 .missing 归 PipelineEmptyStateView）", async () => {
    fetchBoardMock.mockRejectedValue(notFound());
    fetchHealthMock.mockResolvedValue(staleHealth);
    await refreshBoard();
    await refreshHealth();
    renderShell();
    expect(screen.queryByText("Background service is not running")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Start service" })).toHaveLength(1);
  });

  it("网络读失败仍是「连不上本地服务」+ 重试，没有 composer 也没有「启动后台服务」", async () => {
    fetchBoardMock.mockRejectedValue(readFailed());
    await refreshBoard();
    renderShell();
    expect(screen.getByText(OFFLINE_TITLE)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.queryByText(MISSING_TITLE)).toBeNull();
    expect(screen.queryByRole("button", { name: "Start service" })).toBeNull();
    expect(screen.queryByPlaceholderText(/One sentence/)).toBeNull();
    expect(screen.queryByText("page-content")).toBeNull();
  });

  it("有快照后文件被删（404）：看板退回空态（原生 dashboard=nil），不留旧板", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(notFound());
    await refreshBoard();
    renderShell();
    expect(screen.getByText(MISSING_TITLE)).toBeTruthy();
    expect(screen.queryByText("page-content")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull(); // ErrorBanner 不借「连不上」说话
  });
});

describe("AppShell · 自拉快照的页不等看板（原生 MainWindow.detail）", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
    fetchHealthMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    goTo("");
  });

  // 回收站 / 永久性完成 / 会议纪要读的是看板快照，跟看板同一套三态——见 AppShell.boardDependentPages.test.tsx
  for (const page of ["settings", "about", "ingest", "permissions", "setup", "deps", "diagnostics", "styleguide"] as const) {
    it(`?page=${page}：首载中 / 读失败 / 404 三态下 children 都渲染`, async () => {
      goTo(`?page=${page}`);
      // 首载中
      renderShell();
      expect(screen.getByText("page-content")).toBeTruthy();
      expect(screen.queryByText("Loading the board…")).toBeNull();
      cleanup();
      // 网络读失败
      fetchBoardMock.mockRejectedValue(readFailed());
      await refreshBoard();
      renderShell();
      expect(screen.getByText("page-content")).toBeTruthy();
      expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
      cleanup();
      // dashboard.json 不存在
      fetchBoardMock.mockRejectedValue(notFound());
      await refreshBoard();
      renderShell();
      expect(screen.getByText("page-content")).toBeTruthy();
      expect(screen.queryByText(MISSING_TITLE)).toBeNull();
      expect(screen.queryByPlaceholderText(/One sentence/)).toBeNull();
    });
  }

  it("非看板页 + 文件不存在：健康横幅照常说话（server 可达，verdict 是真话；空态不在这一页）", async () => {
    goTo("?page=settings");
    fetchBoardMock.mockRejectedValue(notFound());
    fetchHealthMock.mockResolvedValue(staleHealth);
    await refreshBoard();
    await refreshHealth();
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    expect(screen.getByText("Background service is not running")).toBeTruthy();
  });

  it("看板页首载中仍是 loading 空态（只有看板页在等）", () => {
    goTo("");
    renderShell();
    expect(screen.getByText("Loading the board…")).toBeTruthy();
    expect(screen.queryByText("page-content")).toBeNull();
  });
});
