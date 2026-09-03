// §21 合并建议卡 + §21bis 强制合并 + 多选操作条 + §34bis 清理积压 + §37 改名 + §38.2 拆分：
// 每个动作的 wire payload 零多余字段（server 零容忍），T2 卡不进批量批准。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests, setSelectionMode, toggleSelected } from "../../store";
import type { ApprovalCard, Board, MergeSuggestion } from "../../types";
import { normalizeTitle, TitleEditor } from "../detail/TitleEditor";
import { forceMergeBody } from "./ForceMergeDialog";
import { confidenceChip, MergeSuggestionCard, titlesFor, verdictLabel } from "./MergeSuggestionCard";
import { proposalsTriageBody, ProposalsTriageButton } from "./ProposalsTriageButton";
import { batchable, SelectionBar } from "./SelectionBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

const en = (_zh: string, english: string) => english;

function board(): Board {
  return {
    generated_at: "2026-09-02T00:00:00Z",
    counts: { needs_approval: 3 },
    needs_approval: [
      { id: "P-1", title: "T1 card", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
      { id: "P-2", title: "T2 card", tier: "T2", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
      { id: "P-3", title: "External", tier: "T1", effective_tier: "T2", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    ],
    running: [{ id: "R-9", name: "Run", state: "working" }],
    needs_input: [], review: [], completed: [], debt: [], trash: [],
  } as unknown as Board;
}

function suggestion(over: Partial<MergeSuggestion> = {}): MergeSuggestion {
  return { id: "MS-1", ids: ["P-1", "P-2"], status: "done", verdict: "merge", primary: "P-1", rationale: "same thing",
    action_plan: ["fold P-2 into P-1"], confidence: "high", error: null, requested_at: 1_760_000_000, ...over };
}

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(async () => {
  // jsdom <dialog> 兜底（同 ProposalCard.test）：老版本没有 showModal/close
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.open = false;
    };
  }
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board());
  vi.mocked(postAction).mockReset();
  vi.mocked(postAction).mockResolvedValue({});
  await refreshBoard();
});

afterEach(cleanup);

describe("MergeSuggestionCard", () => {
  it("done → accept posts merge_apply with the MS id; dismiss posts merge_dismiss", async () => {
    renderEn(<MergeSuggestionCard suggestion={suggestion()} />);
    expect(screen.getByText("Suggest merging the secondary into the primary")).toBeTruthy();
    expect(screen.getByText("fold P-2 into P-1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "merge_apply", comment: null, id: "MS-1" }));
  });

  it("failed → only dismiss + merge anyway; merge anyway → merge_force then merge_dismiss", async () => {
    renderEn(<MergeSuggestionCard suggestion={suggestion({ status: "failed", verdict: null, error: "timed out" })} />);
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.getByText("Merge analysis failed")).toBeTruthy();
    expect(screen.getByText(/timed out/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Merge anyway" }));
    fireEvent.click(screen.getByRole("radio", { name: /P-2/ }));
    fireEvent.click(screen.getByRole("button", { name: "Force-merge" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "merge_force", ids: ["P-1", "P-2"], primary: "P-2" });
    expect(vi.mocked(postAction).mock.calls[1][0]).toEqual({ action: "merge_dismiss", comment: null, id: "MS-1" });
  });

  it("analyzing shows the spinner and no decision buttons", () => {
    renderEn(<MergeSuggestionCard suggestion={suggestion({ status: "analyzing", verdict: null })} />);
    expect(screen.getByText("Analyzing merge…")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("helpers: verdict labels, confidence chip, titles lookup", () => {
    expect(verdictLabel("keep_separate", en)).toBe("Suggest keeping them separate");
    expect(verdictLabel("weird", en)).toBe("weird");
    expect(confidenceChip("high")).toContain("chip-success");
    expect(confidenceChip("low")).toContain("chip-warning");
    expect(titlesFor(["P-1", "R-9", "nope"], board() as unknown as Record<string, unknown>)).toEqual({ "P-1": "T1 card", "R-9": "Run" });
    expect(forceMergeBody(["a", "b", "a"], "a")).toEqual({ action: "merge_force", ids: ["a", "b"], primary: "a" });
  });
});

describe("SelectionBar", () => {
  it("batchable skips T2 (incl. W17 effective T2) for approve only", () => {
    const proposals = board().needs_approval as ApprovalCard[];
    const ids = new Set(["P-1", "P-2", "P-3", "R-9"]);
    expect(batchable(ids, proposals, "approve")).toEqual({ ok: ["P-1"], skippedT2: ["P-2", "P-3"] });
    expect(batchable(ids, proposals, "reject")).toEqual({ ok: ["P-1", "P-2", "P-3"], skippedT2: [] });
  });

  it("renders only in selection mode; merge review sends ids in selection order", async () => {
    const { container } = renderEn(<SelectionBar />);
    expect(container.textContent).toBe("");
    setSelectionMode(true);
    toggleSelected("P-2");
    toggleSelected("P-1");
    await screen.findByText("2 selected");
    fireEvent.click(screen.getByRole("button", { name: "Suggest merge (2)" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "merge_review", ids: ["P-2", "P-1"] }));
  });

  it("batch approve confirms then posts one approve per eligible card", async () => {
    setSelectionMode(true);
    toggleSelected("P-1");
    toggleSelected("P-2");
    renderEn(<SelectionBar />);
    fireEvent.click(screen.getByRole("button", { name: "Approve (1)" }));
    expect(screen.getByText(/Skipped T2.*P-2/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Approve" }).at(-1)!);
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "P-1" });
  });
});

describe("ProposalsTriageButton", () => {
  it("payload mirrors §34bis and the button is disabled with no backlog", async () => {
    expect(proposalsTriageBody()).toEqual({ action: "capture", text: "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议", mode: "run", preset: "proposals_triage" });
    renderEn(<ProposalsTriageButton backlogCount={0} />);
    expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    renderEn(<ProposalsTriageButton backlogCount={3} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith(proposalsTriageBody()));
  });
});

describe("TitleEditor (§37 set_title)", () => {
  it("normalizes whitespace and enforces 1..64 code points", () => {
    expect(normalizeTitle("  a　 b  ")).toBe("a b");
    expect(normalizeTitle("   ")).toBeNull();
    expect(normalizeTitle("x".repeat(65))).toBeNull();
    expect(normalizeTitle("字".repeat(64))).toBe("字".repeat(64));
  });

  it("submits {action, id, title} and nothing else", async () => {
    renderEn(<TitleEditor cardId="P-1" current="T1 card" />);
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByLabelText("New title") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  New   name " } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "set_title", id: "P-1", title: "New name" }));
  });
});

describe("FeedbackDialog (§29)", () => {
  it("feedbackBody sorts + dedupes ids and carries publish", async () => {
    const { feedbackBody } = await import("./FeedbackDialog");
    expect(feedbackBody(" hi ", true, ["R-2", "R-1", "R-2"])).toEqual({ action: "feedback", text: " hi ", publish: true, ids: ["R-1", "R-2"] });
    expect(feedbackBody("x", false, [])).toEqual({ action: "feedback", text: "x", publish: false, ids: [] });
  });

  it("header 提建议 posts a global feedback with ids:[] and publish false by default", async () => {
    const { FeedbackButton } = await import("../chrome/FeedbackButton");
    window.localStorage.removeItem("zai.feedbackPublish");
    renderEn(<FeedbackButton />);
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    fireEvent.change(screen.getByPlaceholderText("Your feedback…"), { target: { value: "please add dark mode" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "feedback", text: "please add dark mode", publish: false, ids: [] }));
    await screen.findByText(/Feedback recorded/);
  });
});
