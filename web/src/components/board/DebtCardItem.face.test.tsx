// 潜在任务卡面（原生 DebtRow.rowContent，Cards.swift:2028-2039）：
//   · 标题 = §37 摘要优先链（item.displaySummary：钦定名 > summary > display_title > title）；
//   · 难度章走 hardnessLabel：hard → 「较难 / Hard」红章，soft → 「常规 / Routine」灰章，未知值原样灰章，
//     缺席不出章（此前 soft 什么都不渲染、hard 写成「硬需求」）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { DebtCard } from "../../types";
import { DebtCardItem } from "./DebtCardItem";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

function item(over: Partial<DebtCard> = {}): DebtCard {
  return { id: "P-113", title: "example-bench 的 README 安装一节过时了", summary: "setup 命令已经跑不通", display_title: "README 过时", ...over };
}

describe("DebtCardItem headline (§37 summary-first)", () => {
  it("summary 压过 display_title；aria-label 同字串", () => {
    render(<DebtCardItem item={item()} />);
    expect(screen.getByRole("article", { name: "Backlog · setup 命令已经跑不通" })).toBeTruthy();
    expect(screen.queryByText("README 过时")).toBeNull();
  });

  it("user_titled=true → 钦定名上卡面", () => {
    render(<DebtCardItem item={item({ user_titled: true })} />);
    expect(screen.getByRole("article", { name: "Backlog · README 过时" })).toBeTruthy();
  });
});

describe("DebtCardItem hardness chip (native hardnessLabel)", () => {
  it("hard → Hard 红章；soft → Routine 灰章；未知值原样灰章；缺席不出章", () => {
    const { unmount } = render(<DebtCardItem item={item({ hardness: "hard" })} />);
    expect(screen.getByText("Hard").className).toBe("chip chip-danger");
    unmount();
    render(<DebtCardItem item={item({ hardness: "soft" })} />);
    expect(screen.getByText("Routine").className).toBe("chip");
    cleanup();
    render(<DebtCardItem item={item({ hardness: "medium" })} />);
    expect(screen.getByText("medium").className).toBe("chip");
    cleanup();
    render(<DebtCardItem item={item()} />);
    expect(document.querySelectorAll(".card-badges .chip").length).toBe(0);
  });

  it("zh：较难 / 常规（原生 L 对逐字）", () => {
    const { unmount } = render(<LanguageContext.Provider value="zh"><DebtCardItem item={item({ hardness: "hard" })} /></LanguageContext.Provider>);
    expect(screen.getByText("较难").className).toContain("chip-danger");
    expect(screen.queryByText("硬需求")).toBeNull();
    unmount();
    render(<LanguageContext.Provider value="zh"><DebtCardItem item={item({ hardness: "soft" })} /></LanguageContext.Provider>);
    expect(screen.getByText("常规").className).toBe("chip");
  });
});

describe("DebtCardItem §76.2 completion_hint (issue #313)", () => {
  it("有提示 → 绿章 + 证据一句，出口仍是卡上既有的三颗动词（不开新动作）", () => {
    render(<DebtCardItem item={item({ completion_hint: { at: 1788948000, note: "Compass repo 已建", channel: "meeting" } })} />);
    expect(screen.getByText("✅ Looks already done").className).toBe("chip chip-success");
    expect(screen.getByText("Compass repo 已建")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Done for good (seal, stop suggesting)" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /mark delivered/ })).toBeNull();
  });

  it("缺席 / 空壳 → 章与证据行都不渲染", () => {
    const { unmount } = render(<DebtCardItem item={item()} />);
    expect(screen.queryByText("✅ Looks already done")).toBeNull();
    unmount();
    render(<DebtCardItem item={item({ completion_hint: { at: null, note: "" } })} />);
    expect(screen.queryByText(/✅ Evidence/)).toBeNull();
  });
});
