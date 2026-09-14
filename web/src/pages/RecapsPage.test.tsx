// 会议纪要页行为（CONTRACT §63 / issue #129 §3）：
//   1) 行从 board.recaps 渲染、按日分组、默认选中第一行、进行中行无正文；
//   2) 复制 = 剪贴板写入 + POST /api/recaps/mark copied（唯一出口）；
//   3) 重新生成 → inbox recap_generate（note 可选，零多余字段）；OPEN 行「现在生成」→ partial:true；
//      备注命中五行契约做不到的诉求 → 面板逐条说明、按钮改口、toast 不再假装全做到了（issue #296）；
//   4) 「投到 Slack 草稿」只在开关开着时出现，走 recap_slack_draft {meeting_key, channel_id}。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRecapSettings, postAction, postRecapMark } from "../api";
import { PICKUP_TIMEOUT_MS } from "../components/recaps/recapText";
import { LanguageContext } from "../i18n";
import { getState, refreshBoard, resetStoreForTests } from "../store";
import type { Board, RecapRow, RecapSettings } from "../types";
import { GENERATING_POLL_MS, RecapsPage } from "./RecapsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(),
    fetchRecapSettings: vi.fn(),
    postAction: vi.fn(),
    postRecapMark: vi.fn(),
  };
});

const KEY = "meeting:2026-08-31T1256-zoom";
const EN = ["Decided: the run moves to Monday", "Split: not assigned", "Deadline: none set",
  "Changed since last plan: none recorded", "Open: none"];
const ZH = ["定了：训练周一开始", "分工：未分配", "截止：未定", "较上次变化：无记录", "待定：无"];

function recap(over: Partial<RecapRow> = {}): RecapRow {
  return {
    key: KEY, app: "zoom", start: "2026-08-31T19:56:00Z", end: "2026-08-31T20:16:00Z",
    duration_min: 20, status: "closed", version: 1, quality: "ok", en: EN, zh: ZH, ...over,
  };
}

function settings(over: Partial<RecapSettings> = {}): RecapSettings {
  return { enabled: true, default_language: "auto", slack_draft_enabled: false,
    languages: ["auto", "zh", "en"], source: {}, ...over };
}

function seedBoard(recaps: RecapRow[]): Board {
  return { generated_at: "2026-09-01T00:00:00Z", counts: {}, needs_approval: [], running: [],
    needs_input: [], review: [], completed: [], debt: [], trash: [], recaps } as unknown as Board;
}

async function renderPage(recaps: RecapRow[], over: Partial<RecapSettings> = {}) {
  vi.mocked(fetchRecapSettings).mockResolvedValue(settings(over));
  vi.mocked(fetchBoard).mockResolvedValue(seedBoard(recaps));
  await refreshBoard();   // store 只经 action 改：board 从 mock 的 fetchBoard 回流
  const view = render(
    <LanguageContext.Provider value="en">
      <RecapsPage />
    </LanguageContext.Provider>,
  );
  await waitFor(() => expect(getState().recapSettings).not.toBeNull());
  return view;
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchRecapSettings).mockReset();
  vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true });
  vi.mocked(postRecapMark).mockReset().mockImplementation(async (key, mark, on = true) => ({
    ok: true, key, copied_at: mark === "copied" && on ? "2026-09-01T00:00:00Z" : null,
    sent_at: mark === "sent" && on ? "2026-09-01T00:00:01Z" : null,
  }));
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("RecapsPage", () => {
  it("renders rows grouped by day and shows the first recap in English by default", async () => {
    await renderPage([recap(), recap({ key: "meeting:2026-08-30T1000-teams", app: "teams",
      start: "2026-08-30T17:00:00Z", end: "2026-08-30T17:30:00Z", duration_min: 30, status: "open", en: null, zh: null, quality: null })]);
    expect(screen.getAllByRole("button", { name: /Zoom · 20 min/ }).length).toBe(1);
    expect(screen.getByText("In progress")).toBeTruthy();
    expect(screen.getByText("New")).toBeTruthy();
    const body = screen.getByText(/Decided: the run moves to Monday/);
    expect(body.textContent?.split("\n").length).toBe(5);
    fireEvent.click(screen.getByRole("tab", { name: "中文" }));
    expect(screen.getByText(/定了：训练周一开始/)).toBeTruthy();
  });

  it("copy writes the five lines to the clipboard and marks copied", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "copied", true));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(EN.join("\n"));
    await waitFor(() => expect(screen.getByText("Copied")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Mark as sent" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "sent", true));
    await waitFor(() => expect(screen.getByText("Sent")).toBeTruthy());
  });

  it("regenerate posts recap_generate with an optional note and nothing else", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.change(screen.getByLabelText(/Correction note/), { target: { value: "deadline is Friday" } });
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY, note: "deadline is Friday" }));
  });

  it("a note the five-line format cannot honor is called out before anything is queued", async () => {
    // issue #296：删一行 + 写详细，两条都是结构上做不到的；面板必须在排队前说清楚。
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    const box = screen.getByLabelText(/Correction note/);
    fireEvent.change(box, { target: { value: "Omit the Open line and write the rest in more detail." } });
    expect(screen.getByText(/cannot honor these/)).toBeTruthy();
    expect(screen.getByText(/A line cannot be dropped/)).toBeTruthy();
    expect(screen.getByText(/More detail does not fit/)).toBeTruthy();
    expect(postAction).not.toHaveBeenCalled();          // 打字不排队，说明不是事后补的
    fireEvent.click(screen.getByRole("button", { name: "Regenerate anyway" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY,
      note: "Omit the Open line and write the rest in more detail." }));
    await waitFor(() => expect(screen.getByText(/cannot be honored and will not change/)).toBeTruthy());
  });

  it("an ordinary correction keeps the plain button and the plain success line", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.change(screen.getByLabelText(/Correction note/), { target: { value: "deadline is Friday" } });
    expect(screen.queryByText(/cannot honor these/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(screen.getByText("Regeneration queued")).toBeTruthy());
  });

  it("an open meeting offers Generate now (partial) and no copy", async () => {
    await renderPage([recap({ status: "open", en: null, zh: null, quality: null })]);
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY, partial: true }));
  });

  it("slack draft button exists only with the toggle on and posts the conversation id", async () => {
    await renderPage([recap()]);
    expect(screen.queryByRole("button", { name: /Slack drafts/ })).toBeNull();
    cleanup();
    resetStoreForTests();
    await renderPage([recap({ slack_draft: { status: "no_target" } })], { slack_draft_enabled: true });
    expect(screen.getByText("No draft: no target conversation")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Place in Slack drafts…" }));
    const input = screen.getByPlaceholderText("C0123456789");
    fireEvent.change(input, { target: { value: "general" } });
    expect((screen.getByRole("button", { name: "Place draft" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "D0ABCDEF12" } });
    fireEvent.click(screen.getByRole("button", { name: "Place draft" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_slack_draft", meeting_key: KEY, channel_id: "D0ABCDEF12" }));
  });

  it("empty board shows the onboarding line", async () => {
    await renderPage([]);
    expect(screen.getByText(/No recaps yet/)).toBeTruthy();
  });

  // ----- §63.8 / issue #297：排队后面板不装死 -------------------------------------------------- //

  let reflows = 0;
  /** 模拟一次 SSE 后的看板回流：新 generated_at、给定的 recaps[] */
  async function reflow(recaps: RecapRow[]) {
    reflows += 1;
    vi.mocked(fetchBoard).mockResolvedValue({ ...seedBoard(recaps), generated_at: `2026-09-14T00:01:${String(reflows).padStart(2, "0")}Z` });
    await refreshBoard();
  }

  it("regenerate shows Generating on the row and panel until the new version lands, then flashes the version", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "recap_generate", meeting_key: KEY }));
    // 乐观排队：行 badge + 状态行 + 生成按钮禁用，纠正备注面板收起
    await screen.findByText("Generating");
    expect(screen.getByText(/Queued, waiting for the daemon/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generating…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByLabelText(/Correction note/)).toBeNull();
    // 看板回流但版本没变（actd 还没接手）→ 仍在排队
    await reflow([recap()]);
    expect(screen.getByText("Generating")).toBeTruthy();
    // actd 回执 running → 状态行换成「正在重新生成」
    await reflow([recap({ generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "running", note: null } })]);
    await waitFor(() => expect(screen.getByText(/Regenerating\. The new version lands here by itself/)).toBeTruthy());
    expect(screen.getByText("Generating")).toBeTruthy();
    // 新版本落地（done）→ badge 退场、正文换新、闪「Updated to version 2」、按钮恢复
    const en2 = ["Decided: the run moves to Tuesday", ...EN.slice(1)];
    await reflow([recap({ version: 2, quality: "needs_review", en: en2,
      generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "done", note: null } })]);
    await waitFor(() => expect(screen.queryByText("Generating")).toBeNull());
    expect(screen.getByText(/Decided: the run moves to Tuesday/)).toBeTruthy();
    expect(screen.getByText("Updated to version 2")).toBeTruthy();
    expect(screen.getByText("Updated")).toBeTruthy();
    expect(screen.getByText("Needs review")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Regenerate…" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(/Queued, waiting/)).toBeNull();
    expect(getState().recapPending).toEqual({});
  });

  it("a running receipt from the daemon shows Generating even without a local click (page reload)", async () => {
    await renderPage([recap({ generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "running", note: null } })]);
    expect(screen.getByText("Generating")).toBeTruthy();
    expect(screen.getByText(/Regenerating\. The new version lands here/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generating…" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("lost and noop receipts explain themselves and leave the button usable", async () => {
    await renderPage([recap({ generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "lost", note: null } })]);
    expect(screen.getByText("Generation lost")).toBeTruthy();
    expect(screen.getByText(/never landed: no new version for over 10 minutes/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Regenerate…" }) as HTMLButtonElement).disabled).toBe(false);
    cleanup();
    resetStoreForTests();
    await renderPage([recap({ generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "noop", note: "launch_failed" } })]);
    expect(screen.getByText("Did not start")).toBeTruthy();
    expect(screen.getByText(/failed to launch/)).toBeTruthy();
  });

  it("Generate now on an open meeting queues and shows the partial wording once the daemon is running", async () => {
    const open = recap({ status: "open", en: null, zh: null, quality: null, version: 0 });
    await renderPage([open]);
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await screen.findByText("Generating");
    expect(screen.getByText(/Queued, waiting/)).toBeTruthy();
    await reflow([{ ...open, generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "running", note: null } }]);
    await waitFor(() => expect(screen.getByText(/Generating the partial recap/)).toBeTruthy());
    await reflow([recap({ status: "open", partial: true, version: 1,
      generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "done", note: null } })]);
    await waitFor(() => expect(screen.queryByText("Generating")).toBeNull());
    expect(screen.getByText("Updated to version 1")).toBeTruthy();
    expect(screen.getByText("Partial")).toBeTruthy();
  });

  it("a regeneration that lands with no text is announced as a failure, never as an update", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await screen.findByText("Generating");
    await reflow([recap({ version: 2, quality: "generation_failed", en: null, zh: null,
      generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "done", note: null } })]);
    await waitFor(() => expect(screen.queryByText("Generating")).toBeNull());
    expect(screen.getByText("Version 2 landed with no text (generation failed)")).toBeTruthy();
    expect(screen.queryByText(/Updated to version/)).toBeNull();
    expect(screen.getByText("Generation failed")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Regenerate…" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("polls the board every GENERATING_POLL_MS only while a row is generating, and stops when the version lands", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const running = recap({ generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "running", note: null } });
    await renderPage([running]);
    expect(screen.getByText("Generating")).toBeTruthy();
    const before = vi.mocked(fetchBoard).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(GENERATING_POLL_MS); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(before + 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(GENERATING_POLL_MS); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(before + 2);
    // 下一次补拉带回新版本 → 生成中退场 → 补拉停
    vi.mocked(fetchBoard).mockResolvedValue({ ...seedBoard([recap({ version: 2,
      generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "done", note: null } })]), generated_at: "2026-09-14T00:02:00Z" });
    await act(async () => { await vi.advanceTimersByTimeAsync(GENERATING_POLL_MS); });
    expect(screen.queryByText("Generating")).toBeNull();
    const settled = vi.mocked(fetchBoard).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(GENERATING_POLL_MS * 3); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(settled);
  });

  it("does not poll at all when nothing is generating", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await renderPage([recap()]);
    const before = vi.mocked(fetchBoard).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(GENERATING_POLL_MS * 3); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(before);
  });

  it("after 90 s without a receipt the row says Not picked up and the button unlocks; a late receipt still takes over", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await screen.findByText("Generating");
    await act(async () => { await vi.advanceTimersByTimeAsync(PICKUP_TIMEOUT_MS + GENERATING_POLL_MS); });
    expect(screen.getByText("Not picked up")).toBeTruthy();
    expect(screen.getByText(/Nothing picked this up in 90 s: actd may not be running/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Regenerate…" }) as HTMLButtonElement).disabled).toBe(false);
    expect(Object.keys(getState().recapPending)).toEqual([KEY]);   // 留着，这句话靠它显示
    const polls = vi.mocked(fetchBoard).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(GENERATING_POLL_MS * 3); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(polls);   // unclaimed 不补拉
    // actd 终于接手：新回执 running → 又是生成中
    await reflow([recap({ generate_request: { requested_at: "2026-09-14T00:03:00Z", state: "running", note: null } })]);
    await screen.findByText("Generating");
    expect(screen.queryByText("Not picked up")).toBeNull();
    expect(getState().recapPending).toEqual({});
  });
});
