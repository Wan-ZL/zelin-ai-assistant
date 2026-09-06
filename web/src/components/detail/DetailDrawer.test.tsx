// 抽屉行为测试：开合（selectCard/Esc/背板）、?card= 深链同步、详情渲染、
// 复制为 Markdown、交付物页签切换；⎋ 开着侧栏时只关侧栏——FilterBar 的两段 ⎋（清词 → 退出多选，§54.4）
// 那一下不动（D34 后侧栏是唯一详情面、⎋ 是它的正式关法，不能顺手把用户筛好的看板抽掉）。fetch 全程 stub——绝不打真 server。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DetailDrawer } from "./DetailDrawer";
import { FilterBar } from "../chrome/FilterBar";
import { getState, resetStoreForTests, selectCard, setFilters, setSelectionMode } from "../../store";

const DETAIL = {
  id: "R-101",
  title: "给 example-bench 加导出",
  lane: "needs_approval",
  tier: "T1",
  summary: "一句话摘要。",
  plan: ["step A", "step B"],
  dod: ["有导出按钮"],
  sources: [{ who: "manager", channel: "slack", date: "2026-08-20", quote: "要能导出" }],
  final_draft: "# Draft heading",
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    if (String(url).includes("/api/cards/")) return jsonResponse(DETAIL);
    return jsonResponse({ ok: true });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("DetailDrawer", () => {
  it("renders nothing until a card is selected, then shows enriched fields", async () => {
    const { container } = render(<DetailDrawer />);
    expect(container.firstChild).toBeNull();

    act(() => selectCard("R-101"));
    await screen.findByText("step A");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("给 example-bench 加导出")).toBeTruthy();
    expect(screen.getByText("有导出按钮")).toBeTruthy();
    expect(screen.getByText("要能导出")).toBeTruthy();
    // ?card= 深链已同步
    expect(new URLSearchParams(window.location.search).get("card")).toBe("R-101");
  });

  it("closes on Escape and clears the deep link", async () => {
    render(<DetailDrawer />);
    act(() => selectCard("R-101"));
    await screen.findByRole("dialog");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(new URLSearchParams(window.location.search).get("card")).toBeNull();
  });

  it("⎋ with the drawer open closes only the drawer — the search term and selection mode survive (§54.4 two-stage ⎋ stays out)", async () => {
    window.history.replaceState(null, "", "/?q=example");
    render(<><FilterBar /><DetailDrawer /></>);
    act(() => { setFilters({ search: "example" }); setSelectionMode(true); });
    act(() => selectCard("R-101"));
    await screen.findByRole("dialog");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(getState().filters.search).toBe("example");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("example");
    expect(getState().selectionMode).toBe(true);

    // 侧栏关了，下一下 ⎋ 才是 FilterBar 的第一段：清词
    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().filters.search).toBe("");
    expect(getState().selectionMode).toBe(true);
  });

  it("restores a ?card= deep link on mount", async () => {
    window.history.replaceState(null, "", "/?card=R-101");
    render(<DetailDrawer />);
    await screen.findByRole("dialog");
  });

  it("switches to the deliverable tab and renders the final draft", async () => {
    render(<DetailDrawer />);
    act(() => selectCard("R-101"));
    await screen.findByText("step A");

    fireEvent.click(screen.getByRole("tab", { name: "Deliverable" }));
    await screen.findByRole("heading", { level: 1, name: "Draft heading" });
  });

  it("copies the card as markdown from the header button", async () => {
    const writeText = vi.fn(async (_value: string) => undefined);
    vi.stubGlobal("navigator", { ...window.navigator, clipboard: { writeText } });
    render(<DetailDrawer />);
    act(() => selectCard("R-101"));
    await screen.findByText("step A");

    fireEvent.click(screen.getByRole("button", { name: "Copy as Markdown" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = writeText.mock.calls[0][0];
    expect(copied).toContain("# 给 example-bench 加导出");
    expect(copied).toContain("- [ ] 有导出按钮");
  });

  it("offers copy-as-markdown from the context menu", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...window.navigator, clipboard: { writeText } });
    render(<DetailDrawer />);
    act(() => selectCard("R-101"));
    await screen.findByText("step A");

    fireEvent.contextMenu(screen.getByRole("dialog"));
    const item = await screen.findByRole("menuitem", { name: "Copy as Markdown" });
    fireEvent.click(item);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
  });
});

describe("DetailDrawer §60 two-stage ids (D21)", () => {
  it("抬头显示 display_id（工作编号），主键并排；深链按工作编号也能命中", async () => {
    const detail = { ...DETAIL, id: "P-012", work_id: "R-280", display_id: "R-280", id_kind: "work" };
    vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
      // server 侧 /api/cards/{ref} 同样接受工作编号（§60.3），响应 id 恒为主键
      if (String(url).includes("/api/cards/")) return jsonResponse(detail);
      return jsonResponse({ ok: true });
    }));
    render(<DetailDrawer />);
    act(() => selectCard("R-280"));
    await screen.findByText("step A");
    const ids = Array.from(document.querySelectorAll(".zai-drawer-id")).map((n) => n.textContent);
    expect(ids).toEqual(["R-280", "P-012"]);
    // 字段面：工作编号 + 主键两行都在
    expect(screen.getByText("Work number")).toBeTruthy();
    expect(screen.getByText("Card key")).toBeTruthy();
  });

  it("无工作编号的提案卡：抬头只有主键一枚，不并排", async () => {
    const detail = { ...DETAIL, id: "P-007", display_id: "P-007", id_kind: "proposal" };
    vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
      if (String(url).includes("/api/cards/")) return jsonResponse(detail);
      return jsonResponse({ ok: true });
    }));
    render(<DetailDrawer />);
    act(() => selectCard("P-007"));
    await screen.findByText("step A");
    const ids = Array.from(document.querySelectorAll(".zai-drawer-id")).map((n) => n.textContent);
    expect(ids).toEqual(["P-007"]);
    expect(screen.queryByText("Work number")).toBeNull();
  });
});
