// 顶栏行为测试（G7）：新鲜度阈值（镜像 Freshness.swift 的 90s 语义）、
// 主题切换（dataset + localStorage）、语言切换（store.setLanguage + zai.lang 持久化）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { getState, refreshBoard, resetStoreForTests } from "../../store";
import type { Board } from "../../types";
import { HeaderBar } from "./HeaderBar";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);

function makeBoard(generatedAt: string): Board {
  return {
    generated_at: generatedAt,
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

async function seedBoard(ageSeconds: number) {
  const generatedAt = new Date(Date.now() - ageSeconds * 1000).toISOString();
  fetchBoardMock.mockResolvedValue(makeBoard(generatedAt));
  await refreshBoard();
}

function renderHeader(language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <HeaderBar />
    </LanguageContext.Provider>,
  );
}

describe("HeaderBar", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("新鲜数据（≤90s）：显示相对时间，无 actd 警告", async () => {
    await seedBoard(30);
    renderHeader();
    expect(screen.getByText("Data generated just now")).toBeTruthy();
    expect(screen.queryByText(/actd may be down/)).toBeNull();
  });

  it("过期数据（>90s）：显示分钟数 + actd 可能未运行警告", async () => {
    await seedBoard(5 * 60);
    renderHeader();
    expect(screen.getByText("Data generated 5 min ago — actd may be down")).toBeTruthy();
  });

  it("15s tick 自驱变陈旧：80s 时新鲜，跨过 90s 阈值后变警告", async () => {
    vi.useFakeTimers();
    await seedBoard(80);
    renderHeader();
    expect(screen.getByText(/Data generated 1m ago/)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(15_000); // 80s + 15s = 95s > 90s
    });
    expect(screen.getByText(/actd may be down/)).toBeTruthy();
  });

  it("主题切换：写 dataset.theme + localStorage zai.theme，往返翻转", () => {
    renderHeader();
    const toggle = screen.getByRole("button", { name: "Toggle dark mode" });
    fireEvent.click(toggle); // jsdom 无 matchMedia → 初始按 light，切到 dark
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("zai.theme")).toBe("dark");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("zai.theme")).toBe("light");
  });

  it("语言切换：store.language 翻转 + 持久化 zai.lang", () => {
    renderHeader("zh");
    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));
    expect(getState().language).toBe("en");
    expect(window.localStorage.getItem("zai.lang")).toBe("en");
  });
});
