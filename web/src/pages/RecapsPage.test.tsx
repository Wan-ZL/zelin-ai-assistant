// 会议纪要页行为（CONTRACT §63 / issue #129 §3）：
//   1) 行从 board.recaps 渲染、按日分组、默认选中第一行、进行中行无正文；
//   2) 复制 = 剪贴板写入 + POST /api/recaps/mark copied（唯一出口）；
//   3) 重新生成 → inbox recap_generate（note 可选，零多余字段）；OPEN 行「现在生成」→ partial:true；
//   4) 「投到 Slack 草稿」只在开关开着时出现，走 recap_slack_draft {meeting_key, channel_id}。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRecapSettings, postAction, postRecapMark } from "../api";
import { LanguageContext } from "../i18n";
import { getState, refreshBoard, resetStoreForTests } from "../store";
import type { Board, RecapRow, RecapSettings } from "../types";
import { RecapsPage } from "./RecapsPage";

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
});
