// 永久性完成整页与右侧书立条共用一份字面量（原生 ArchivePageView 复用 ArchiveSectionView，MainWindow.swift:483；
// Cards.swift:2675 标题「🗄 永久性完成 · done for good」/ :2688 空态「还没有永久完成的卡」「Nothing here yet」）：
//   · 页头 h2 与书立条按钮同一句（且只有一个 🗄——此前书立条多加了一个前缀）；
//   · 空态句同一句（此前整页写成「还没有封存的卡」/「Nothing archived yet」）；搜索无命中「无匹配项」；
//   · 行标题走 §37 摘要优先链（ArchiveRow：summary 压过 display_title，user_titled 钦定名压过一切）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../api";
import { ARCHIVE_EMPTY, ARCHIVE_TITLE, ArchiveStrip } from "../components/chrome/ArchiveStrip";
import { LanguageContext, type Language } from "../i18n";
import { refreshBoard, resetStoreForTests } from "../store";
import type { Board } from "../types";
import { ArchivePage } from "./ArchivePage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn(), postAction: vi.fn().mockResolvedValue({ ok: true }) };
});

function board(archived: Board["archived"]): Board {
  return {
    generated_at: "2026-09-05T12:00:00Z", counts: { archived: archived?.length ?? 0 },
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], archived,
  } as unknown as Board;
}

async function load(archived: Board["archived"]) {
  vi.mocked(fetchBoard).mockResolvedValue(board(archived));
  await refreshBoard();
}

function renderIn(lang: Language, node: React.ReactNode) {
  return render(<LanguageContext.Provider value={lang}>{node}</LanguageContext.Provider>);
}

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

describe("ArchivePage shares ArchiveStrip's literals", () => {
  it.each(["zh", "en"] as const)("%s：页头 = 书立条标题（同一 🗄 字串，只一个图标）", async (lang) => {
    await load([]);
    const expected = lang === "zh" ? ARCHIVE_TITLE[0] : ARCHIVE_TITLE[1];
    const { unmount } = renderIn(lang, <ArchivePage />);
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(expected);
    unmount();
    renderIn(lang, <ArchiveStrip />);
    const toggle = screen.getByRole("button", { name: /Done for good|永久性完成/ });
    expect(toggle.textContent).toContain(expected);
    expect((toggle.textContent?.match(/🗄/g) ?? []).length).toBe(1);
  });

  it.each(["zh", "en"] as const)("%s：空态句 = 原生 EmptyRow 同句，两个面一致", async (lang) => {
    await load([]);
    const expected = lang === "zh" ? ARCHIVE_EMPTY[0] : ARCHIVE_EMPTY[1];
    const { unmount } = renderIn(lang, <ArchivePage />);
    expect(screen.getByText(expected)).toBeTruthy();
    expect(screen.queryByText(/还没有封存的卡|Nothing archived yet/)).toBeNull();
    unmount();
    renderIn(lang, <ArchiveStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Done for good|永久性完成/ }));
    expect(screen.getByText(expected)).toBeTruthy();
  });

  it("有行但搜索无命中 → 「No matches」，不是空态句", async () => {
    await load([{ id: "R-7", title: "Old thread", summary: "done", archived_at: "2026-08-01T00:00:00Z" }]);
    renderIn("en", <ArchivePage />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
    expect(screen.getByText("No matches")).toBeTruthy();
    expect(screen.queryByText("Nothing here yet")).toBeNull();
  });

  it("行标题 §37 摘要优先：summary 压过 display_title；user_titled 钦定名压过一切", async () => {
    await load([
      { id: "R-1", title: "raw-1", summary: "摘要一", display_title: "短名一", archived_at: "2026-08-01T00:00:00Z" },
      { id: "R-2", title: "raw-2", summary: "摘要二", display_title: "我起的名", user_titled: true, archived_at: "2026-08-02T00:00:00Z" },
      { id: "R-3", title: "https://x.y/z", display_title: "x.y ▸ z", archived_at: "2026-08-03T00:00:00Z" },
    ]);
    renderIn("en", <ArchivePage />);
    expect(screen.getByText("摘要一")).toBeTruthy();
    expect(screen.queryByText("短名一")).toBeNull();
    expect(screen.getByText("我起的名")).toBeTruthy();
    expect(screen.queryByText("摘要二")).toBeNull();
    expect(screen.getByText("x.y ▸ z")).toBeTruthy();
    expect(screen.queryByText("https://x.y/z")).toBeNull();
  });
});
