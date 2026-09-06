// 合并建议卡的 partition / 覆盖 / 标题解析细节（原生 Cards.swift:2183-2225 doneButtons、:2337-2355 verdictHeadline、
// Store.swift:912-920 title(of:)；CONTRACT §21 / §21bis / §54.1 追记）：
//   · partition 有分组方案：结论「建议分成 k 组分别合并」、主按钮「按分组合并（k 组）」、次按钮「保持独立」、且有「仍然合并」；
//   · partition 缺 groups 的老 payload：回落「建议按分组分别合并」/「接受」/「取消」，走单-primary 路径；
//   · keep_separate / link_improvement / close_secondary：有「仍然合并」、次按钮是「取消」（保持独立只给 partition）；
//   · titlesFor 全 lane 覆盖：潜在任务（debt）与回收站（trash）的卡也解析得到标题。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board, MergeSuggestion } from "../../types";
import { MergeSuggestionCard, partitionGroups, titlesFor, verdictLabel } from "./MergeSuggestionCard";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;

function board(): Board {
  return {
    generated_at: "2026-09-05T00:00:00Z",
    counts: {},
    needs_approval: [{ id: "P-1", title: "Proposal one", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] }],
    running: [], needs_input: [], review: [],
    completed: [{ id: "P-5", name: "Done one", state: "delivered" }],
    debt: [{ id: "P-3", title: "Backlog card title" }],
    trash: [{ id: "P-4", title: "Trashed card title", permanent: false, trashed_at: "2026-09-04T00:00:00Z" }],
  } as unknown as Board;
}

function partition(over: Partial<MergeSuggestion> = {}): MergeSuggestion {
  return {
    id: "MS-9", ids: ["P-1", "P-3", "P-4", "P-5"], status: "done", verdict: "partition", primary: null,
    rationale: "两组各自合并", confidence: "medium", error: null, requested_at: 1_760_000_000,
    groups: [
      { primary: "P-1", ids: ["P-1", "P-3"], reason: "同一件事" },
      { primary: "P-5", ids: ["P-5"], reason: "独立" },
    ],
    ...over,
  };
}

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(async () => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board());
  vi.mocked(postAction).mockReset();
  vi.mocked(postAction).mockResolvedValue({});
  await refreshBoard();
});

afterEach(cleanup);

describe("partition verdict with a group plan (Cards.swift:2189-2196, 2217-2220, 2347-2349)", () => {
  it("结论点名组数；主按钮按分组合并（2 组）→ merge_apply；次按钮 保持独立 → merge_dismiss；有 仍然合并", async () => {
    renderEn(<MergeSuggestionCard suggestion={partition()} />);
    expect(screen.getByText("Suggest merging as 2 separate groups")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Merge anyway" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Merge by groups (2)" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "merge_apply", comment: null, id: "MS-9" }));
  });

  it("保持独立 发的是 merge_dismiss（partition 的取消语义 = 全部保持独立）", async () => {
    renderEn(<MergeSuggestionCard suggestion={partition()} />);
    fireEvent.click(screen.getByRole("button", { name: "Keep separate" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "merge_dismiss", comment: null, id: "MS-9" }));
  });

  it("分组清单里 潜在任务 / 回收站 / 阶段性完成 的卡都带标题（titlesFor 全 lane）", () => {
    renderEn(<MergeSuggestionCard suggestion={partition()} />);
    expect(screen.getByText("P-3 · Backlog card title")).toBeTruthy();
    expect(screen.getByText("P-5 · Done one")).toBeTruthy();
    expect(screen.getByText("P-4 · Trashed card title")).toBeTruthy();   // 分组没点名 → 「Stays separate:」行
  });

  it("仍然合并 → 主卡单选里也解析 debt / trash 标题，确认后 merge_force 再顺手 merge_dismiss", async () => {
    renderEn(<MergeSuggestionCard suggestion={partition()} />);
    fireEvent.click(screen.getByRole("button", { name: "Merge anyway" }));
    fireEvent.click(screen.getByRole("radio", { name: /P-3.*Backlog card title/ }));
    fireEvent.click(screen.getByRole("button", { name: "Force-merge" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "merge_force", ids: ["P-1", "P-3", "P-4", "P-5"], primary: "P-3" });
    expect(vi.mocked(postAction).mock.calls[1][0]).toEqual({ action: "merge_dismiss", comment: null, id: "MS-9" });
  });
});

describe("partition without groups — legacy payload falls back to the single-primary path (Cards.swift:2351)", () => {
  it("结论回落「建议按分组分别合并」、按钮 接受 / 仍然合并 / 取消、主卡 / 副卡 行", () => {
    renderEn(<MergeSuggestionCard suggestion={partition({ ids: ["P-1", "P-3"], primary: "P-1", groups: undefined })} />);
    expect(screen.getByText("Suggest merging by groups")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Merge anyway" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep separate" })).toBeNull();
    expect(screen.getByText("P-1 · Proposal one")).toBeTruthy();
    expect(screen.getByText("P-3 · Backlog card title")).toBeTruthy();
  });

  it("groups: [] 同样算缺方案", () => {
    expect(partitionGroups(partition({ groups: [] }))).toBeNull();
    expect(partitionGroups(partition({ verdict: "merge" }))).toBeNull();
    expect(partitionGroups(partition())?.length).toBe(2);
  });
});

describe("other non-merge verdicts (Cards.swift:2207-2212)", () => {
  it.each(["keep_separate", "link_improvement", "close_secondary"])("%s：有 仍然合并，次按钮是 取消（不是 保持独立）", (verdict) => {
    renderEn(<MergeSuggestionCard suggestion={partition({ verdict, primary: "P-1", groups: undefined })} />);
    expect(screen.getByRole("button", { name: "Merge anyway" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep separate" })).toBeNull();
  });

  it("merge：没有 仍然合并", () => {
    renderEn(<MergeSuggestionCard suggestion={partition({ verdict: "merge", primary: "P-1", groups: undefined })} />);
    expect(screen.queryByRole("button", { name: "Merge anyway" })).toBeNull();
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
  });
});

describe("helpers", () => {
  it("verdictLabel partition：有组数点名、无组数回落；zh / en 逐字原生", () => {
    expect(verdictLabel("partition", en, 3)).toBe("Suggest merging as 3 separate groups");
    expect(verdictLabel("partition", zh, 3)).toBe("建议分成 3 组分别合并");
    expect(verdictLabel("partition", en)).toBe("Suggest merging by groups");
    expect(verdictLabel("partition", zh, 0)).toBe("建议按分组分别合并");
  });

  it("titlesFor 覆盖 debt 与 trash（原生 Store.title(of:)）", () => {
    expect(titlesFor(["P-1", "P-3", "P-4", "P-5", "nope"], board() as unknown as Record<string, unknown>)).toEqual({
      "P-1": "Proposal one", "P-3": "Backlog card title", "P-4": "Trashed card title", "P-5": "Done one",
    });
  });
});
