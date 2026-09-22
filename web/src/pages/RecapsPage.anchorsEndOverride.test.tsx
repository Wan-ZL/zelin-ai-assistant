// 会议纪要页——issue #440 的面（CONTRACT §63.13 / §63.14 / §63.15 / §63.16）：
//   1) 可发送长版的正文下方有一个折叠的「转写依据」（逐条的戳 + 原话），复制出去的正文与表头一字不带它；
//   2) 「改结束时间…」→ time 输入 → 保存 = POST /api/recaps/end {key, end_override: ISO-Z}，表头 / 行标签
//      乐观换成手改的时刻，脚注说出录制到几点；「回到录制时间」= end_override: null；结束不晚于开始不发请求；
//   3) 「生成未落地」那句的分钟数来自回执的 lost_after_s；4) 脚注报术语表换了几处。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRecapHistory, fetchRecapSettings, postAction, postRecapEnd, postRecapMark } from "../api";
import { localHHMM } from "../components/recaps/recapText";
import { LanguageContext } from "../i18n";
import { getState, refreshBoard, resetStoreForTests } from "../store";
import type { Board, RecapRow, RecapSettings } from "../types";
import { RecapsPage } from "./RecapsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(),
    fetchRecapHistory: vi.fn(),
    fetchRecapSettings: vi.fn(),
    postAction: vi.fn(),
    postRecapEnd: vi.fn(),
    postRecapMark: vi.fn(),
  };
});

const KEY = "meeting:2026-08-31T1256-zoom";
const EN = ["Decided: the run moves to Monday", "Split: not assigned", "Deadline: none set",
  "Changed since last plan: none recorded", "Open: none"];
const ZH = ["定了：训练周一开始", "分工：未分配", "截止：未定", "较上次变化：无记录", "待定：无"];
const COPY_EN = "Decided:\nD1. The run moves to Monday\n\nSplit (decided):\nS1. Bo may take the handover (floated)";

function recap(over: Partial<RecapRow> = {}): RecapRow {
  return {
    key: KEY, app: "zoom", start: "2026-08-31T19:56:00Z", end: "2026-08-31T20:36:00Z",
    duration_min: 40, status: "closed", version: 1, quality: "ok", en: EN, zh: ZH, ...over,
  };
}

function sectionsRecap(over: Partial<RecapRow> = {}): RecapRow {
  return recap({
    shape: "sections", en: null, zh: null,
    sections_en: [
      { key: "decided", modality: "decided", items: ["The run moves to Monday"], tags: ["D1"],
        modalities: ["decided"], anchors: [{ at: "12:58", quote: "the run moves to Monday" }] },
      { key: "split", modality: "decided", items: ["Bo may take the handover"], tags: ["S1"],
        modalities: ["floated"], anchors: [{ at: "13:05", quote: "maybe Bo takes the handover" }] },
    ],
    sections_zh: [
      { key: "decided", modality: "decided", items: ["训练周一开始"], tags: ["D1"] },
      { key: "split", modality: "decided", items: ["交接也许归 Bo"], tags: ["S1"] },
    ],
    copy_en: COPY_EN,
    copy_zh: "定了：\nD1. 训练周一开始\n\n分工（已定）：\nS1. 交接也许归 Bo（有人提过）",
    ...over,
  });
}

function settings(over: Partial<RecapSettings> = {}): RecapSettings {
  return { enabled: true, default_language: "auto", slack_draft_enabled: false,
    default_shape: "lines", languages: ["auto", "zh", "en"], source: {}, ...over };
}

function seedBoard(recaps: RecapRow[]): Board {
  return { generated_at: "2026-09-01T00:00:00Z", counts: {}, needs_approval: [], running: [],
    needs_input: [], review: [], completed: [], debt: [], trash: [], recaps } as unknown as Board;
}

/** 右侧详情的 h3（左列的日分组标题也是 h3——按 class 认，不按级别猜） */
function detailTitle(): string {
  const heading = screen.getAllByRole("heading", { level: 3 }).find((h) => h.className.includes("recap-detail-title"));
  return heading?.textContent ?? "";
}

async function renderPage(recaps: RecapRow[]) {
  vi.mocked(fetchRecapSettings).mockResolvedValue(settings());
  vi.mocked(fetchBoard).mockResolvedValue(seedBoard(recaps));
  await refreshBoard();
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
  vi.mocked(fetchRecapHistory).mockReset();
  vi.mocked(fetchRecapSettings).mockReset();
  vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true });
  vi.mocked(postRecapMark).mockReset().mockImplementation(async (key, mark, on = true) => ({
    ok: true, key, copied_at: mark === "copied" && on ? "2026-09-01T00:00:00Z" : null,
    sent_at: null, dismissed_at: null,
  }));
  vi.mocked(postRecapEnd).mockReset().mockImplementation(async (key, endOverride) => ({ ok: true, key, end_override: endOverride }));
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RecapsPage — issue #440", () => {
  it("folds the transcript evidence under the sendable body and keeps it out of the clipboard", async () => {
    await renderPage([sectionsRecap()]);
    const details = screen.getByTestId("recap-evidence");
    expect(details.querySelector("summary")?.textContent).toContain("Transcript evidence (2)");
    expect(details.textContent).toContain("#D1");
    expect(details.textContent).toContain("12:58");
    expect(details.textContent).toContain("the run moves to Monday");
    expect(details.textContent).toContain("#S1");
    expect(details.textContent).toContain("maybe Bo takes the handover");
    // 正文照 daemon 渲染的 copy_*（逐条语气的尾巴在里面）
    expect(screen.getByText(COPY_EN, { collapseWhitespace: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(vi.mocked(navigator.clipboard.writeText).mock.calls.length).toBe(1));
    const pasted = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0];
    expect(pasted.split("\n").slice(1).join("\n")).toBe(COPY_EN);
    expect(pasted).not.toContain("12:58");
    expect(pasted).not.toContain("maybe Bo takes the handover");
  });

  it("gives no evidence block to a five-line recap or a long form without anchors", async () => {
    await renderPage([recap()]);
    expect(screen.queryByTestId("recap-evidence")).toBeNull();
    cleanup();
    resetStoreForTests();
    await renderPage([sectionsRecap({
      sections_en: [{ key: "decided", modality: "decided", items: ["The run moves to Monday"], tags: ["D1"] }],
    })]);
    expect(screen.queryByTestId("recap-evidence")).toBeNull();
  });

  it("edits the end time: posts an ISO-Z, redraws the header and the row label, and can go back", async () => {
    await renderPage([recap()]);
    expect(detailTitle()).toContain("40 min");
    fireEvent.click(screen.getByRole("button", { name: "Edit end time…" }));
    const input = screen.getByLabelText("End time") as HTMLInputElement;
    expect(input.value).toBe(localHHMM("2026-08-31T20:36:00Z"));
    // 往前挪 6 分钟（会 12:30 结束，录制到 12:36 那一例）
    const start = new Date("2026-08-31T19:56:00Z");
    const target = new Date(start.getTime() + 34 * 60000);
    const hhmm = `${String(target.getHours()).padStart(2, "0")}:${String(target.getMinutes()).padStart(2, "0")}`;
    fireEvent.change(input, { target: { value: hhmm } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(postRecapEnd).toHaveBeenCalledTimes(1));
    const [key, iso] = vi.mocked(postRecapEnd).mock.calls[0];
    expect(key).toBe(KEY);
    expect(new Date(iso as string).getTime()).toBe(target.getTime());
    // 乐观回执：表头与左列行标签都换成 34 min；脚注说出录制到几点；文件里的 end 本页不碰
    await waitFor(() => expect(detailTitle()).toContain("34 min"));
    expect(screen.getAllByRole("button", { name: /Zoom · 34 min/ }).length).toBe(1);
    expect(screen.getByText(new RegExp(`captured until ${localHHMM("2026-08-31T20:36:00Z")}`))).toBeTruthy();
    // 回到录制时间 = end_override: null
    fireEvent.click(screen.getByRole("button", { name: "Edit end time…" }));
    fireEvent.click(screen.getByRole("button", { name: "Use captured time" }));
    await waitFor(() => expect(postRecapEnd).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postRecapEnd).mock.calls[1]).toEqual([KEY, null]);
    await waitFor(() => expect(detailTitle()).toContain("40 min"));
  });

  it("refuses an end that is not after the start without posting, and hides the editor on an open meeting", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Edit end time…" }));
    fireEvent.change(screen.getByLabelText("End time"), { target: { value: localHHMM("2026-08-31T19:56:00Z") } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("The end time has to be after the start")).toBeTruthy();
    expect(postRecapEnd).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("End time")).toBeNull();
    cleanup();
    resetStoreForTests();
    await renderPage([recap({ status: "open", en: null, zh: null, quality: null })]);
    expect(screen.queryByRole("button", { name: "Edit end time…" })).toBeNull();
  });

  it("a server-side override already on the row is shown, and marking copied does not lose it", async () => {
    await renderPage([recap({ end_override: "2026-08-31T20:30:00Z" })]);
    expect(detailTitle()).toContain("34 min");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalled());
    await act(async () => { await Promise.resolve(); });
    expect(detailTitle()).toContain("34 min");
  });

  it("says how long the daemon waited before calling a generation lost, from the receipt", async () => {
    await renderPage([recap({ generate_request: { requested_at: "2026-09-14T00:00:05Z", state: "lost", note: null, lost_after_s: 1339 } })]);
    expect(screen.getByText(/no new version for over 22 minutes/)).toBeTruthy();
  });

  it("reports the glossary corrections in the footer only when there were any", async () => {
    await renderPage([recap({ glossary_hits: 3 })]);
    expect(screen.getByText("The glossary corrected 3 misheard term(s).")).toBeTruthy();
    cleanup();
    resetStoreForTests();
    await renderPage([recap({ glossary_hits: 0 })]);
    expect(screen.queryByText(/glossary corrected/)).toBeNull();
  });
});
