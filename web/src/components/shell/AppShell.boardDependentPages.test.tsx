// AppShell 对「数据住在 /api/board 快照里」的页的分派（CONTRACT §54.1 追记 (a)；§0 第 3 条诚实）：
// 回收站 / 永久性完成 / 会议纪要三页读的是 board.trash / board.archived / board.recaps——与看板本体同一份快照。
//   - 首载中：三页跟看板一样是「正在加载看板…」，不许先闪一帧「回收站 0 / 回收站为空」；
//   - 离线且从未有快照（网络 / 5xx）：三页跟看板一样是「连不上本地服务」+ 重试——不许把「拉不到」渲染成「为空」
//     （ErrorBanner 无快照不说话、PipelineBanner 离线时也拿不到 health，整页空态是唯一能说真话的地方）；
//   - dashboard.json 不存在（404）：三页照常渲染（原生 TrashPageView 读 store.visibleTrash = dashboard?.trash ?? []，
//     没有 dashboard 就是空列表），看板页独享 PipelineEmptyStateView，健康横幅在这三页照常说话；
//   - 自拉快照的页（设置 / 关于 / 录制 / 权限体检 / 向导）三态都不受看板牵连。
// 用真实页面组件当 children——只有这样「假空列表」才会露出来。api 层 mock 掉，经 store 真实 action 驱动。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchHealth, fetchRecapSettings } from "../../api";
import { LanguageContext } from "../../i18n";
import { ArchivePage } from "../../pages/ArchivePage";
import { RecapsPage } from "../../pages/RecapsPage";
import { TrashPage } from "../../pages/TrashPage";
import { refreshBoard, refreshHealth, resetStoreForTests } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return {
    ...mod,
    fetchBoard: vi.fn(),
    fetchCard: vi.fn(),
    fetchHealth: vi.fn(),
    fetchRecapSettings: vi.fn(),
    postAction: vi.fn(),
  };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const fetchHealthMock = vi.mocked(fetchHealth);
const fetchRecapSettingsMock = vi.mocked(fetchRecapSettings);

const LOADING_TITLE = "Loading the board…";
const OFFLINE_TITLE = "Can't reach the local server";
const MISSING_TITLE = "The background service hasn't produced data yet";

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

function makeBoard(): Board {
  return {
    generated_at: "2026-09-05T08:00:00Z",
    counts: { trash: 1, archived: 1 },
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [{ id: "R-1", title: "old idea", summary: "an old idea", trashed_at: "2026-09-04T00:00:00Z", permanent: false }],
    archived: [{ id: "R-2", title: "sealed thread", summary: "a sealed thread", archived_at: "2026-09-04T00:00:00Z" }],
    recaps: [{
      key: "meeting:2026-09-04T1000-zoom", app: "zoom", start: "2026-09-04T17:00:00Z", end: "2026-09-04T17:20:00Z",
      duration_min: 20, status: "closed", version: 1, quality: "ok",
      en: ["Decided: ship it", "Split: none", "Deadline: none", "Changed since last plan: none", "Open: none"],
      zh: ["定了：发", "分工：无", "截止：未定", "较上次变化：无", "待定：无"],
    }],
  } as unknown as Board;
}

/** 三页各自的「空列表」句——离线 / 首载时这些句子出现就是在撒谎 */
const BOARD_FED_PAGES = [
  { page: "trash", Page: TrashPage, emptyCopy: "Trash is empty" },
  { page: "archive", Page: ArchivePage, emptyCopy: "Nothing here yet" },
  { page: "recaps", Page: RecapsPage, emptyCopy: /No recaps yet/ },
] as const;

function renderShell(children: React.ReactNode) {
  return render(
    <LanguageContext.Provider value="en">
      <AppShell>{children}</AppShell>
    </LanguageContext.Provider>,
  );
}

function goTo(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

describe("AppShell · 读看板快照的页（回收站 / 永久性完成 / 会议纪要）跟看板同一套三态", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
    fetchHealthMock.mockReset();
    fetchRecapSettingsMock.mockReset();
    fetchRecapSettingsMock.mockResolvedValue({
      enabled: true, default_language: "auto", slack_draft_enabled: false, languages: ["auto", "zh", "en"], source: {},
    });
  });

  afterEach(() => {
    cleanup();
    goTo("");
  });

  for (const { page, Page, emptyCopy } of BOARD_FED_PAGES) {
    it(`?page=${page}：首载中 = 「正在加载看板…」，不闪空列表`, () => {
      goTo(`?page=${page}`);
      renderShell(<Page />);
      expect(screen.getByText(LOADING_TITLE)).toBeTruthy();
      expect(screen.queryByText(emptyCopy)).toBeNull();
    });

    it(`?page=${page}：离线且从未有快照 = 「连不上本地服务」+ 重试，不是「为空」`, async () => {
      goTo(`?page=${page}`);
      fetchBoardMock.mockRejectedValue(readFailed());
      await refreshBoard();
      renderShell(<Page />);
      expect(screen.getByText(OFFLINE_TITLE)).toBeTruthy();
      expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
      expect(screen.queryByText(emptyCopy)).toBeNull();
      // 看板页独享的缺文件空态与 composer 不在这里
      expect(screen.queryByText(MISSING_TITLE)).toBeNull();
      expect(screen.queryByPlaceholderText(/One sentence/)).toBeNull();
    });

    it(`?page=${page}：dashboard.json 不存在（404）= 页面照常渲染成空列表（原生 dashboard?.trash ?? []）`, async () => {
      goTo(`?page=${page}`);
      fetchBoardMock.mockRejectedValue(notFound());
      fetchHealthMock.mockResolvedValue(staleHealth);
      await refreshBoard();
      await refreshHealth();
      renderShell(<Page />);
      expect(screen.getByText(emptyCopy)).toBeTruthy();
      expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
      expect(screen.queryByText(LOADING_TITLE)).toBeNull();
      // PipelineEmptyStateView 归看板页；这三页上健康横幅照常说「后台服务没在跑」
      expect(screen.queryByText(MISSING_TITLE)).toBeNull();
      expect(screen.getByText("Background service is not running")).toBeTruthy();
    });

    it(`?page=${page}：有快照就渲染页面（离线降级由 ErrorBanner 声明，不整页顶掉）`, async () => {
      goTo(`?page=${page}`);
      fetchBoardMock.mockResolvedValue(makeBoard());
      await refreshBoard();
      fetchBoardMock.mockRejectedValue(readFailed());
      await refreshBoard();
      renderShell(<Page />);
      // 「连不上」此时是 ErrorBanner 的横幅标题（role=alert），不是整页空态——没有「重试」、页面仍在
      expect(screen.getByRole("alert").textContent).toContain(OFFLINE_TITLE);
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(screen.queryByText(LOADING_TITLE)).toBeNull();
      expect(screen.queryByText(emptyCopy)).toBeNull(); // 快照里三页各有一行——渲染的是数据，不是空态
    });
  }

  it("回收站有快照时渲染真实行（不是空态）", async () => {
    goTo("?page=trash");
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    renderShell(<TrashPage />);
    expect(screen.getByText("an old idea")).toBeTruthy();
    expect(screen.queryByText("Trash is empty")).toBeNull();
  });

  for (const page of ["settings", "about", "ingest", "permissions", "setup"] as const) {
    it(`?page=${page}：自拉快照的页在首载中 / 离线 / 404 三态下都渲染 children`, async () => {
      goTo(`?page=${page}`);
      renderShell(<div>page-content</div>);
      expect(screen.getByText("page-content")).toBeTruthy();
      expect(screen.queryByText(LOADING_TITLE)).toBeNull();
      cleanup();
      fetchBoardMock.mockRejectedValue(readFailed());
      await refreshBoard();
      renderShell(<div>page-content</div>);
      expect(screen.getByText("page-content")).toBeTruthy();
      expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
      cleanup();
      fetchBoardMock.mockRejectedValue(notFound());
      await refreshBoard();
      renderShell(<div>page-content</div>);
      expect(screen.getByText("page-content")).toBeTruthy();
      expect(screen.queryByText(MISSING_TITLE)).toBeNull();
    });
  }
});
