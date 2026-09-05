// AppShell / ErrorBanner 对「dashboard.json 解不出来」的分派（CONTRACT §49 追记 `store-resilience-drawer`；原生
// Kanban.swift:60-66：header 下一行橙色 loadError、看板照常渲染上一版、PipelineHealthBanner 照常说话）：
//   - 有旧快照：页面照常渲染 + warning 横幅 = 那一行「Failed to read dashboard.json: …」（不是「连不上本地服务」）；
//     健康横幅不因它闭嘴（server 在跑，verdict 是真话）；
//   - 从未有过快照：整页空态 = 那一行 + 「重试」（走 refreshBoard），不是离线文案；
//   - 离线优先：断网时仍是离线横幅，两句话不同时说。api 层 mock 掉，经 store 真实 action 驱动；零真实网络。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchHealth } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, refreshHealth, resetStoreForTests, setConnection, setLanguage } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn(), fetchHealth: vi.fn(), postAction: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const fetchHealthMock = vi.mocked(fetchHealth);

const DECODE_LINE = "Failed to read dashboard.json: The server response is not valid JSON (200)";
const OFFLINE_TITLE = "Can't reach the local server";

function makeBoard(): Board {
  return { generated_at: "2026-09-05T10:00:00Z", counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [] };
}

/** api.request 对 2xx 非 JSON 体合成的错误原形 */
const invalidJson = () => new ApiError(200, {
  error: { code: "READ_FAILED", message: "The server response is not valid JSON (200)", details: { method: "GET", failure: "invalid-json" } },
});
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

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  setLanguage("en");
  fetchBoardMock.mockReset();
  fetchHealthMock.mockReset();
  fetchHealthMock.mockResolvedValue({ ...staleHealth, verdict: "ok" });
});

afterEach(() => {
  cleanup();
});

describe("AppShell · dashboard.json 解不出来", () => {
  it("有旧快照：页面照常 + warning 横幅是原生那一行，不说「连不上」", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(invalidJson());
    await refreshBoard();
    setConnection("live");
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    const banner = screen.getByRole("alert");
    expect(banner.className).toContain("is-warning");
    expect(banner.textContent).toContain(DECODE_LINE);
    expect(banner.textContent).toContain("last successfully loaded snapshot");
    expect(banner.textContent).not.toContain(OFFLINE_TITLE);
  });

  it("有旧快照：健康横幅不因解码失败闭嘴（server 在跑，verdict 是真话）", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(invalidJson());
    fetchHealthMock.mockResolvedValue(staleHealth);
    await refreshBoard();
    await refreshHealth();
    setConnection("live");
    renderShell();
    expect(screen.getByText(DECODE_LINE)).toBeTruthy();
    expect(screen.getByText("Background service is not running")).toBeTruthy();
  });

  it("从未有过快照：整页空态 = 那一行 + 重试（走 refreshBoard），不是离线文案", async () => {
    fetchBoardMock.mockRejectedValue(invalidJson());
    await refreshBoard();
    renderShell();
    expect(screen.queryByText("page-content")).toBeNull();
    expect(screen.getByText(DECODE_LINE)).toBeTruthy();
    expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
    expect(screen.queryByText(/dev-preview\.sh/)).toBeNull();

    fetchBoardMock.mockResolvedValue(makeBoard());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await vi.waitFor(() => expect(fetchBoardMock).toHaveBeenCalledTimes(2));
    await screen.findByText("page-content");
    expect(screen.queryByRole("alert")).toBeNull(); // 好快照落地：横幅也退场
  });

  it("中文：整页空态那一行逐字原生", async () => {
    setLanguage("zh");
    fetchBoardMock.mockResolvedValue({} as unknown as Board);
    await refreshBoard();
    renderShell("zh");
    expect(screen.getByText("读取 dashboard.json 失败: 缺少 generated_at")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("离线优先：解码失败后断网 → 离线横幅，解码那一行不再说", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(invalidJson());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(readFailed());
    await refreshBoard();
    renderShell();
    const banner = screen.getByRole("alert");
    expect(banner.textContent).toContain(OFFLINE_TITLE);
    expect(banner.textContent).not.toContain("Failed to read dashboard.json");
    expect(banner.className).not.toContain("is-warning");
  });

  it("SSE 重连中 + 解码失败：仍是离线横幅（两句话不同时说）", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(invalidJson());
    await refreshBoard();
    setConnection("reconnecting");
    renderShell();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toContain(OFFLINE_TITLE);
  });

  it("非看板页从不等看板：解码失败时设置页照常渲染", async () => {
    window.history.replaceState(null, "", "/?page=settings");
    fetchBoardMock.mockRejectedValue(invalidJson());
    await refreshBoard();
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
  });
});
