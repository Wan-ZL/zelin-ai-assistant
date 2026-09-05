// §37 改名预填：抽屉「✎ 改名」的输入框从此刻的卡面标题起手（原生 TitleEditRow current = displaySummary，
// Cards.swift:1283），不是抬头的冻结 title——提案面 = 钦定名 > summary > display_title > title，
// 运行族 = display_title > name。fetch 全程 stub。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests, selectCard } from "../../store";
import { DetailDrawer } from "./DetailDrawer";

const FROZEN = "example-bench: leaderboard 一键导出评测报告";
const DETAILS: Record<string, Record<string, unknown>> = {
  "P-102": { id: "P-102", lane: "needs_approval", title: FROZEN, summary: "大白话摘要一句", display_title: "LLM 短名", tier: "T1" },
  "P-114": { id: "P-114", lane: "needs_approval", title: FROZEN, summary: "大白话摘要一句", display_title: "钦定名", user_titled: true, tier: "T1" },
  "R-201": { id: "R-201", lane: "running", name: "冻结名", display_title: "运行中显示名", state: "working" },
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    const id = String(url).match(/\/api\/cards\/([^/?]+)/)?.[1];
    if (id && DETAILS[id]) return jsonResponse(DETAILS[id]);
    return jsonResponse({ ok: true });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

async function openRename(cardId: string): Promise<HTMLInputElement> {
  render(<DetailDrawer />);
  act(() => selectCard(cardId));
  const rename = await screen.findByRole("button", { name: "Rename" });
  fireEvent.click(rename);
  return screen.getByRole("textbox", { name: "New title" }) as HTMLInputElement;
}

describe("DetailDrawer — ✎ Rename prefills the face headline", () => {
  it("提案无钦定名：抬头是冻结 title，输入框预填 summary（卡面上的那句）", async () => {
    const input = await openRename("P-102");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(FROZEN);
    expect(input.value).toBe("大白话摘要一句");
  });

  it("提案有钦定名：预填用户钉的名字", async () => {
    const input = await openRename("P-114");
    expect(input.value).toBe("钦定名");
  });

  it("运行族：预填 display_title（名字优先面）", async () => {
    const input = await openRename("R-201");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("冻结名");
    expect(input.value).toBe("运行中显示名");
  });
});
