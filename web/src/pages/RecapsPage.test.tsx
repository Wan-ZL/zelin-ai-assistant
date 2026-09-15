// 会议纪要页行为（CONTRACT §63 / issue #129 §3）：
//   1) 行从 board.recaps 渲染、按日分组、默认选中第一行、进行中行无正文；
//   2) 复制 = 剪贴板写入（§63.5 追记：一行日期表头 + 5 行正文，表头与面板 h3 同一份）
//      + POST /api/recaps/mark copied（唯一出口）；
//   3) 重新生成 → inbox recap_generate（shape 恒在、note 可选，零多余字段）；OPEN 行「现在生成」→ partial:true；
//      备注命中五行契约做不到的诉求 → 面板逐条说明、按钮改口、toast 不再假装全做到了（issue #296）；
//   4) 「投到 Slack 草稿」只在开关开着时出现，走 recap_slack_draft {meeting_key, channel_id}；
//   5) needs_review 的原因逐条摊在脚注里、自动修剪过的行也说出来（§63.3 追记，issue #298）；
//   6) 三栏 活跃 / 已归档 / 已忽略：标记已发送即归档、忽略 / 恢复一颗按钮，默认只看活跃（§63.5 追记，issue #301）；
//   7) §63.10（issue #303）：可发送长版的正文照 daemon 渲染好的 copy_* 显示（所见即所复制）、
//      不给按位置的引用 chip；「重新生成」面板的形状选择器把 shape 一并送进 inbox，
//      备注预检随形状收口并在五行形下指路长版；
//   8) 「上一版」= GET /api/recaps/history 的两版并排 + 逐行改动 + 一颗回退（inbox recap_revert），
//      回退在途时面板留一条回执（排队中 / 90 s 后「actd 可能没在跑」）、帽满时说清回退会挤掉最早一版，
//      正文下方五颗引用 chip 复制 `2026-08-31 Zoom #D`（§63.9，issue #300）。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRecapHistory, fetchRecapSettings, postAction, postRecapMark } from "../api";
import { PICKUP_TIMEOUT_MS, REVERT_POLL_MS } from "../components/recaps/recapText";
import { LanguageContext } from "../i18n";
import { getState, refreshBoard, resetStoreForTests } from "../store";
import type { Board, RecapHistory, RecapLaneTotals, RecapRow, RecapSettings } from "../types";
import { GENERATING_POLL_MS, RecapsPage } from "./RecapsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(),
    fetchRecapHistory: vi.fn(),
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

/** §63.10 一份可发送长版：en/zh 空着，正文在 sections_* 与 daemon 渲染好的 copy_* 里 */
const SECTIONS_EN = "Decided:\n1. The run moves to Monday\n\nProposed (proposed):\n2. Ship behind a flag";

function sectionsRecap(over: Partial<RecapRow> = {}): RecapRow {
  return recap({
    shape: "sections", en: null, zh: null,
    sections_en: [{ key: "decided", modality: "decided", items: ["The run moves to Monday"] },
                  { key: "proposed", modality: "proposed", items: ["Ship behind a flag"] }],
    sections_zh: [{ key: "decided", modality: "decided", items: ["训练周一开始"] },
                  { key: "proposed", modality: "proposed", items: ["先挂开关上线"] }],
    copy_en: SECTIONS_EN,
    copy_zh: "定了：\n1. 训练周一开始\n\n提议：\n2. 先挂开关上线",
    ...over,
  });
}

function settings(over: Partial<RecapSettings> = {}): RecapSettings {
  return { enabled: true, default_language: "auto", slack_draft_enabled: false,
    languages: ["auto", "zh", "en"], source: {}, ...over };
}

function seedBoard(recaps: RecapRow[], recapCounts?: RecapLaneTotals): Board {
  const board = { generated_at: "2026-09-01T00:00:00Z", counts: {}, needs_approval: [], running: [],
    needs_input: [], review: [], completed: [], debt: [], trash: [], recaps } as unknown as Board;
  // recap_counts 缺席 = 老 daemon（页面就不说「另有 N 条」，也不给计数加 `+`）
  return recapCounts ? ({ ...board, recap_counts: recapCounts } as unknown as Board) : board;
}

async function renderPage(recaps: RecapRow[], over: Partial<RecapSettings> = {},
                          recapCounts?: RecapLaneTotals) {
  vi.mocked(fetchRecapSettings).mockResolvedValue(settings(over));
  vi.mocked(fetchBoard).mockResolvedValue(seedBoard(recaps, recapCounts));
  await refreshBoard();   // store 只经 action 改：board 从 mock 的 fetchBoard 回流
  const view = render(
    <LanguageContext.Provider value="en">
      <RecapsPage />
    </LanguageContext.Provider>,
  );
  await waitFor(() => expect(getState().recapSettings).not.toBeNull());
  return view;
}

const V1_EN = ["Decided: Ann owns the data mix", "Split: Ann, Bo", "Deadline: Monday",
  "Changed since last plan: none recorded", "Open: none"];

/** §63.9 `GET /api/recaps/history` 的形：current + entries（newest first）+ 帽 */
function history(over: Partial<RecapHistory> = {}): RecapHistory {
  return {
    key: KEY,
    // §63.10 add-only：每一版都带形状与渲染好的 copy_*（五行形 = 正文仍读 en/zh）
    current: { version: 2, generated_at: "2026-08-31T20:40:00Z", partial: false, quality: "ok",
               en: EN, zh: ZH, shape: "lines", copy_en: null, copy_zh: null },
    entries: [{ version: 1, generated_at: "2026-08-31T20:20:00Z", partial: false, quality: "ok",
                en: V1_EN, zh: ZH, shape: "lines", copy_en: null, copy_zh: null }],
    history_cap: 5,
    truncated: false,
    ...over,
  };
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchRecapHistory).mockReset().mockResolvedValue(history());
  vi.mocked(fetchRecapSettings).mockReset();
  vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true });
  vi.mocked(postRecapMark).mockReset().mockImplementation(async (key, mark, on = true) => ({
    ok: true, key, copied_at: mark === "copied" && on ? "2026-09-01T00:00:00Z" : null,
    sent_at: mark === "sent" && on ? "2026-09-01T00:00:01Z" : null,
    dismissed_at: mark === "dismissed" && on ? "2026-09-01T00:00:02Z" : null,
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

  it("copy writes the date header plus the five lines to the clipboard and marks copied", async () => {
    await renderPage([recap()]);
    // §63.5 追记（issue #299）：面板标题就是复制出去的第一行——所见即所复制
    const heading = screen.getByRole("heading", { level: 3, name: /^\d{4}-\d{2}-\d{2} \(\w{3}\) / });
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "copied", true));
    const written = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0];
    const lines = written.split("\n");
    expect(lines.length).toBe(6);
    expect(lines[0]).toMatch(/^\d{4}-\d{2}-\d{2} \(\w{3}\) \d{2}:\d{2}–\d{2}:\d{2} · Zoom · 20 min$/);
    expect(lines[0]).toBe(heading.textContent);
    expect(lines.slice(1)).toEqual(EN);              // 正文仍是 server 存的那 5 行，一字不改
    await waitFor(() => expect(screen.getByText("Copied")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Mark as sent" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "sent", true));
    // §63.5 追记（issue #301）：标记已发送 = 归档——行离开活跃栏，在「已归档」里带 Sent badge
    await waitFor(() => expect(screen.getByRole("tab", { name: "Active 0" })).toBeTruthy());
    expect(screen.getByText(/back in the active list|filed under Archived/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Archived 1" }));
    expect(screen.getByRole("button", { name: /Zoom · 20 min/ })).toBeTruthy();
    expect(screen.getByText("Sent")).toBeTruthy();
  });

  it("dismiss files a recap under Dismissed and Restore brings it back", async () => {
    // issue #301：不想要的那场会不必被迫标成「已发送」才能离开列表，而且这一步可逆
    await renderPage([recap()]);
    expect(screen.getByRole("tab", { name: "Active 1" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "dismissed", true));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Dismissed 1" })).toBeTruthy());
    expect(screen.getByRole("tab", { name: "Active 0" })).toBeTruthy();
    // 刚忽略的那一行留在右侧：脚注说清它会更早被删，撤销就在同一颗按钮上
    expect(screen.getByText(/deleted earlier than the others/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Dismissed 1" }));
    expect(screen.getByText("Dismissed")).toBeTruthy();                      // 行 badge
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "dismissed", false));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Active 1" })).toBeTruthy());
  });

  it("an open meeting cannot be dismissed", async () => {
    // 会还开着：无从判断这场会要不要纪要，也不该让一个标记删掉之后才落地的正文
    await renderPage([recap({ status: "open", en: null, zh: null, quality: null })]);
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  });

  it("an empty lane says why it is empty instead of showing nothing", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("tab", { name: "Dismissed 0" }));
    expect(screen.getByText(/Nothing dismissed yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Archived 0" }));
    expect(screen.getByText(/Nothing archived yet/)).toBeTruthy();
  });

  it("a lane whose cap cut rows says how many are missing instead of swallowing them", async () => {
    // issue #301 review：两份预算只是提高了门槛——`recap_counts` 报真实总数，栏里说出差额，
    // 计数带 `+`（上限可以是硬的，界面不许悄悄少东西；硬删仍只由 §63.3 的保留窗执行）
    await renderPage([recap({ sent_at: "2026-09-01T00:00:00Z" })], {},
                     { active: 7, archived: 61, dismissed: 0 });
    fireEvent.click(screen.getByRole("tab", { name: "Archived 1+" }));
    expect(screen.getByText(/60 older recap\(s\) are not listed in this lane/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Active 0+" }));
    expect(screen.getByText(/7 older recap\(s\) are not listed in this lane/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Dismissed 0" }));    // 没被切 = 不说话
    expect(screen.queryByText(/are not listed in this lane/)).toBeNull();
  });

  it("regenerate posts recap_generate with the picked shape, an optional note and nothing else", async () => {
    // §63.10：形状恒随请求走（面板上选的那一个就是要生成的那一个，不靠 daemon 猜）
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.change(screen.getByLabelText(/Correction note/), { target: { value: "deadline is Friday" } });
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY, shape: "lines", note: "deadline is Friday" }));
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
      action: "recap_generate", meeting_key: KEY, shape: "lines",
      note: "Omit the Open line and write the rest in more detail." }));
    await waitFor(() => expect(screen.getByText(/cannot be honored and will not change/)).toBeTruthy());
  });

  it("an ordinary correction keeps the plain button and the plain success line", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    fireEvent.change(screen.getByLabelText(/Correction note/), { target: { value: "deadline is Friday" } });
    expect(screen.queryByText(/cannot honor these/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    // §63.8 后普通成功文案多了一句「落地后自动更新」；关键是它不带预检的那句警告
    await waitFor(() => expect(screen.getByText("Regeneration queued; this panel updates when it lands")).toBeTruthy());
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

  it("needs_review lists the reason per line so a correction note can target it", async () => {
    await renderPage([recap({
      quality: "needs_review",
      problems: [{ code: "line_too_long", lang: "en", line: 1, limit: 140, over: 6 },
                 { code: "timestamp", lang: "zh", line: null, limit: null, over: null }],
    })]);
    expect(screen.getByText("Needs review")).toBeTruthy();
    expect(screen.getByText(/Validator flagged this text/)).toBeTruthy();
    expect(screen.getByText("English line 1: 6 characters over the 140-character cap")).toBeTruthy();
    expect(screen.getByText("Chinese: contains a timestamp")).toBeTruthy();
  });

  it("a line trimmed on the way in is stated, never silently pasted", async () => {
    await renderPage([recap({ repairs: [{ lang: "en", line: 1, over: 6, removed: 8 }] })]);
    expect(screen.queryByText("Needs review")).toBeNull();                 // 修好了就是 ok
    expect(screen.getByText(
      "Trimmed English line 1 automatically: 8 characters off the end (it was 6 over the cap)")).toBeTruthy();
  });

  it("empty board shows the onboarding line", async () => {
    await renderPage([]);
    expect(screen.getByText(/No recaps yet/)).toBeTruthy();
  });

  // ----- §63.9 / issue #300：上一版看得见、回得去，每行可被引用 -------------------------------- //

  const stored = (over: Partial<RecapRow> = {}) => recap({
    version: 2,
    history_versions: [{ version: 1, generated_at: "2026-08-31T20:20:00Z", partial: false }],
    ...over,
  });

  it("shows the previous version side by side with the current one and marks the lines that differ", async () => {
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    await waitFor(() => expect(fetchRecapHistory).toHaveBeenCalledWith(KEY));
    // 版本选择器 + 两列 + 逐行「改」标记（第 1、2、3 行不同，后两行相同）
    expect(await screen.findByRole("tab", { name: /^Version 1 · / })).toBeTruthy();
    expect(screen.getByText(/Decided: Ann owns the data mix/)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 4, name: "Current (version 2)" })).toBeTruthy();
    expect(screen.getByText(/3 line\(s\) differ/)).toBeTruthy();
    expect(screen.getAllByText("changed").length).toBe(6);       // 3 行 × 两列
    // HISTORY_CAP 是 server 给的（client 不写死上限）：老版本会老化掉，这句必须在
    expect(screen.getByText(/Only the last 5 versions are kept/)).toBeTruthy();
  });

  it("revert posts recap_revert with the picked version and promises it is undoable", async () => {
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Revert to this version" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_revert", meeting_key: KEY, version: 1 }));
    await waitFor(() => expect(screen.getByText(/Revert to version 1 queued/)).toBeTruthy());
  });

  it("a landed revert says where the text came from, in the flash and in the footnote", async () => {
    await renderPage([stored()]);
    await reflow([stored({ version: 3, reverted_from: 1, en: V1_EN })]);
    await waitFor(() => expect(screen.getByText("Updated to version 3 (restored from version 1)")).toBeTruthy());
    expect(screen.getByText(/restored from version 1 \(the replaced text is in history/)).toBeTruthy();
  });

  it("a queued revert keeps saying so, polls, and falls back to the actd line after 90 s", async () => {
    // 回退没有 daemon 台账（§63.8 只记生成），所以这条本地回执是「它到底发生了没有」的唯一信号：
    // 闪一句就消失、之后面板装死，正是 issue #297 在生成那一侧要消灭的事。
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Revert to this version" }));
    await waitFor(() => expect(postAction).toHaveBeenCalled());
    const queued = await screen.findByText(/Revert to version 1 is queued, waiting for the daemon/);
    expect(queued.getAttribute("data-revert-phase")).toBe("queued");
    // 排队中每 5 s 补拉一次看板（SSE 掉线时的保险，与 §63.8 同款）
    const before = vi.mocked(fetchBoard).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(REVERT_POLL_MS); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(before + 1);
    // 4 s 后闪句消失，回执行仍在（面板不装死）
    expect(screen.queryByText("Revert to version 1 queued")).toBeNull();
    expect(screen.getByText(/is queued, waiting for the daemon/)).toBeTruthy();
    // 90 s 没落地 → 与 §63.8 unclaimed 逐字同一句，补拉停
    await act(async () => { await vi.advanceTimersByTimeAsync(PICKUP_TIMEOUT_MS); });
    expect(screen.getByText(/Nothing picked this up in 90 s: actd may not be running/)).toBeTruthy();
    const polls = vi.mocked(fetchBoard).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(REVERT_POLL_MS * 3); });
    expect(vi.mocked(fetchBoard).mock.calls.length).toBe(polls);
  });

  it("the queued revert receipt ends when the reverted version lands", async () => {
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Revert to this version" }));
    await screen.findByText(/is queued, waiting for the daemon/);
    await reflow([stored({ version: 3, reverted_from: 1, en: V1_EN })]);
    await waitFor(() => expect(screen.queryByText(/is queued, waiting for the daemon/)).toBeNull());
    expect(screen.getByText("Updated to version 3 (restored from version 1)")).toBeTruthy();
  });

  it("says that a revert on a full history ages the oldest version out", async () => {
    // act/recap._push_history 在满帽时挤掉最早那一条：回退**也**会花掉一个回退目标
    // （判例 tests/test_recap_revert.py），面板不许只把老化归因于「下一次生成」
    const entries = [5, 4, 3, 2, 1].map((version) => ({
      version, generated_at: `2026-08-31T20:0${version}:00Z`, partial: false,
      quality: "ok" as const, en: V1_EN, zh: ZH,
      shape: "lines", copy_en: null, copy_zh: null }));
    vi.mocked(fetchRecapHistory).mockResolvedValue(history({ entries }));
    await renderPage([stored({ version: 6 })]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    expect(await screen.findByText(/a revert pushes the current text into history first/)).toBeTruthy();
    expect(screen.getByText(/that pushes the oldest stored version out/)).toBeTruthy();
    expect(screen.getByText(/History is already full at 5/)).toBeTruthy();
    // 还没满的时候不吓人（默认 fixture 只有一条）
    cleanup();
    resetStoreForTests();
    vi.mocked(fetchRecapHistory).mockResolvedValue(history());
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    expect(await screen.findByText(/Only the last 5 versions are kept/)).toBeTruthy();
    expect(screen.queryByText(/History is already full/)).toBeNull();
  });

  it("no stored version means no entry point, and an empty history says so", async () => {
    await renderPage([recap()]);                                  // 老 daemon / 只生成过一次
    expect(screen.queryByRole("button", { name: "Previous version…" })).toBeNull();
    expect(fetchRecapHistory).not.toHaveBeenCalled();
    cleanup();
    resetStoreForTests();
    vi.mocked(fetchRecapHistory).mockResolvedValue(history({ entries: [] }));
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    expect(await screen.findByText(/No earlier version is stored/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Revert to this version" })).toBeNull();
  });

  it("an unreadable history file is admitted, not faked", async () => {
    vi.mocked(fetchRecapHistory).mockResolvedValue(history({ current: null, entries: [], truncated: true }));
    await renderPage([stored()]);
    fireEvent.click(screen.getByRole("button", { name: "Previous version…" }));
    expect(await screen.findByText(/file is too large to read/)).toBeTruthy();
  });

  it("each line has a citation chip and copying one never touches the five-line body", async () => {
    await renderPage([recap()]);
    const chips = screen.getAllByRole("button", { name: /^Copy citation / });
    expect(chips.length).toBe(5);
    expect(chips[0].textContent).toBe("#D");
    expect(chips[4].textContent).toBe("#O");
    fireEvent.click(chips[0]);
    await waitFor(() => expect(vi.mocked(navigator.clipboard.writeText).mock.calls.length).toBe(1));
    expect(vi.mocked(navigator.clipboard.writeText).mock.calls[0][0]).toMatch(/^\d{4}-\d{2}-\d{2} Zoom #D$/);
    // 正文那一份没被动过（复制正文仍是表头 + server 存的五行）
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(postRecapMark).toHaveBeenCalledWith(KEY, "copied", true));
    const body = vi.mocked(navigator.clipboard.writeText).mock.calls[1][0];
    expect(body.split("\n").slice(1)).toEqual(EN);
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
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY, shape: "lines" }));
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
  // ------------------------------------------------------------------ §63.10
  it("shows a sendable recap as the document that will be pasted, with no positional citations", async () => {
    await renderPage([sectionsRecap()]);
    // 正文 = daemon 渲染好的那一份（节标题 + 跨节连续编号 + 语气后缀），一字不改
    expect(screen.getByText(SECTIONS_EN, { collapseWhitespace: false })).toBeTruthy();
    // 行 badge 不许把它说成「没出稿」，复制 / 标记已发送照常在
    expect(screen.getByText("New")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    // 五颗按位置的引用 chip 不出现（条目每次重生成整批换掉），并且照直说一句为什么
    expect(screen.queryByRole("button", { name: /Copy citation/ })).toBeNull();
    expect(screen.getByText(/the item numbers hold for this version only/)).toBeTruthy();
  });

  it("the regenerate panel picks a shape and sends it with the request", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    const longForm = screen.getByRole("radio", { name: "Sendable long form" });
    expect(screen.getByRole("radio", { name: "Quick five lines" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(longForm);
    expect(longForm.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/Sections and numbered items/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY, shape: "sections" }));
  });

  it("stops refusing what the long shape can do, and points at it while the five lines cannot", async () => {
    await renderPage([recap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    const box = screen.getByLabelText(/Correction note/);
    fireEvent.change(box, { target: { value: "Omit the Open line and write the rest in more detail." } });
    expect(screen.getByText(/A line cannot be dropped/)).toBeTruthy();
    // 指路：这几件事换成长版就能办到（issue #303 的正题）
    expect(screen.getByText(/switch to it above and regenerate/)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Sendable long form" }));
    expect(screen.queryByText(/A line cannot be dropped/)).toBeNull();
    expect(screen.queryByText(/cannot honor these/)).toBeNull();
    expect(screen.getByRole("button", { name: "Regenerate" })).toBeTruthy();   // 不再改口成「仍要重新生成」
  });

  it("a recap already in the long shape defaults the picker to it", async () => {
    await renderPage([sectionsRecap()]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate…" }));
    expect(screen.getByRole("radio", { name: "Sendable long form" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({
      action: "recap_generate", meeting_key: KEY, shape: "sections" }));
  });
});
