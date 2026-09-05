// 列顶输入框回执的诚实纪律——组件半边（CONTRACT §10 / §41 2026-09-05 追记；原生 Cards.swift:934,951-956 processingBody、
// :848,863-867 RunCapturePendingRow、Store.swift:343-353 sweepTimeouts、:402-411 超时文案、:652-659 updateHealth 重新起算、
// PendingSweep.swift:169-192 captureMatches）：
//   1) 回执 = 「"<原话前 20 字>" + 状态句」，状态句随 /api/health 切换（stalled / failing / stale → 「已保存到队列」；
//      ok / unknown → 「AI 分析中」/「排队派发中」），健康在回执挂着时变了句子跟着变；
//   2) 回执活过键击（新草稿开打不清），只被下一次提交替换；失败句与 "/" 提示行仍顶掉它（一行栈不变）；
//   3) 刷新带来一行属于这次提交的卡即清——先认 row.capture_id === POST 回的 inbox stem（§10 issue #7），再退到原生的
//      标题 / 摘要前缀猜测；propose 看 needs_approval、run 看 running + needs_input、都不看 review；提交那一刻的快照
//      （generated_at 相同）不算；
//   4) 300 s（propose）/ 180 s（run）后换成原生超时条（黄 / 橙）；管线不 ok 时不计时，恢复时重新起算整段窗口；
//      超时条 120 s 褪去。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchHealth, postAction } from "../../api";
import { refreshBoard, refreshHealth, resetStoreForTests } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { CAPTURE_NOTICE_FADE_MS, CAPTURE_TIMEOUT_MS } from "./captureReceipt";
import { hintLine } from "./composerCommands";
import { LaneComposer } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn(),
  fetchHealth: vi.fn(),
  fetchBoard: vi.fn(),
}));

const en = (_zh: string, english: string) => english;
const TYPED = "Write the onboarding doc for new hires";
const HEAD = "Write the onboarding"; // 前 20 个 code point（恰好 20 = "Write the onboarding"）
const PROPOSE_OK = `"${HEAD}" Submitted — analyzing (usually 2-3 min)`;
const PROPOSE_STALLED = `"${HEAD}" Saved to the queue — processed once the pipeline is running`;
const RUN_OK = `"${HEAD}" Submitted — running it now (skipped proposal), queued for dispatch…`;
const RUN_STALLED = `"${HEAD}" Saved to the queue — runs once the pipeline is up`;
const PROPOSE_TIMEOUT = "Analysis is slower than usual — the card should still appear; if it never does, open the Dependencies page and check state/actd.log";
const RUN_TIMEOUT = `"${HEAD}" did not start — the backend may not be running (check actd)`;

function health(overrides: Partial<HealthSnapshot> = {}): HealthSnapshot {
  return {
    verdict: "ok",
    heartbeat: { age_s: 4, phase: "idle", pid: 1, interval: 10, stale_after_s: 90, stale: false },
    dashboard: { generated_at: "2026-09-05T08:00:00Z", age_s: 5, stale: false },
    loop_health: { consecutive_failures: 0, last_error: null },
    checked_at: "2026-09-05T08:00:05Z",
    ...overrides,
  };
}

function board(generatedAt: string, overrides: Partial<Board> = {}): Board {
  return {
    generated_at: generatedAt,
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
    ...overrides,
  } as Board;
}

const approval = (title: string) =>
  ({ id: "P-1", title, tier: "T1", show_cost: false, processing: true, sources: [], plan: [], dod: [] }) as unknown as Board["needs_approval"][number];
const task = (name: string) => ({ id: "R-1", name, state: "queued" }) as unknown as Board["running"][number];

async function setHealth(snapshot: HealthSnapshot) {
  vi.mocked(fetchHealth).mockResolvedValue(snapshot);
  await act(async () => {
    await refreshHealth();
  });
}

async function setBoard(next: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(next);
  await act(async () => {
    await refreshBoard();
  });
}

function mount(mode: "propose" | "run" = "propose") {
  render(
    <LaneComposer
      placeholder="type here"
      submitLabel={mode === "run" ? "Run" : "Capture"}
      buildBody={(t) => (mode === "run" ? { action: "capture", text: t, mode: "run" } : { action: "capture", text: t })}
    />,
  );
  return {
    field: screen.getByPlaceholderText("type here") as HTMLTextAreaElement,
    button: screen.getByRole("button", { name: mode === "run" ? "Run" : "Capture" }) as HTMLButtonElement,
  };
}

async function submit(field: HTMLTextAreaElement, button: HTMLButtonElement, value = TYPED) {
  fireEvent.change(field, { target: { value } });
  await act(async () => {
    fireEvent.click(button);
  });
}

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(postAction).mockReset();
  vi.mocked(postAction).mockResolvedValue({ ok: true, file: "capture-1.json" });
  vi.mocked(fetchHealth).mockReset();
  vi.mocked(fetchBoard).mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("LaneComposer receipt — honest wording follows /api/health", () => {
  it("propose, pipeline ok (or no health yet): quotes the first 20 chars and promises analysis", async () => {
    const { field, button } = mount();
    await submit(field, button);
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    expect(document.querySelector("[data-capture-receipt='propose']")).toBeTruthy();
  });

  for (const [label, snap] of [
    ["stalled", health({ verdict: "stalled", heartbeat: { age_s: 9000, phase: "reconcile", pid: 1, interval: 10, stale_after_s: 90, stale: true } })],
    ["failing", health({ verdict: "failing", loop_health: { consecutive_failures: 3, last_error: "boom" } })],
    ["stale", health({ verdict: "stale", heartbeat: null })],
  ] as const) {
    it(`propose, pipeline ${label}: says the capture is saved to the queue instead of promising 2-3 minutes`, async () => {
      await setHealth(snap);
      const { field, button } = mount();
      await submit(field, button);
      expect(screen.getByText(PROPOSE_STALLED)).toBeTruthy();
      expect(screen.queryByText(PROPOSE_OK)).toBeNull();
    });
  }

  it("unknown verdict (old daemon still writing the board) is not stalled — same as the banner's silence", async () => {
    await setHealth(health({ verdict: "unknown", heartbeat: null }));
    const { field, button } = mount();
    await submit(field, button);
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
  });

  it("run composer uses the direct-run pair", async () => {
    const { field, button } = mount("run");
    await submit(field, button);
    expect(screen.getByText(RUN_OK)).toBeTruthy();
    await setHealth(health({ verdict: "stale", heartbeat: null }));
    expect(screen.getByText(RUN_STALLED)).toBeTruthy();
  });

  it("the wording flips live while the receipt is showing: ok → stalled → ok", async () => {
    await setHealth(health());
    const { field, button } = mount();
    await submit(field, button);
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    await setHealth(health({ verdict: "stalled" }));
    expect(screen.getByText(PROPOSE_STALLED)).toBeTruthy();
    await setHealth(health());
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
  });
});

describe("LaneComposer receipt — survives keystrokes, yields only to the one-line stack", () => {
  it("typing the next draft does not retire the receipt; the next submit replaces it", async () => {
    const { field, button } = mount();
    await submit(field, button);
    fireEvent.change(field, { target: { value: "second thought" } });
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    fireEvent.change(field, { target: { value: "second thought, refined" } });
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
    expect(screen.getByText('"second thought, refi" Submitted — analyzing (usually 2-3 min)')).toBeTruthy();
    expect(document.querySelectorAll(".column-help")).toHaveLength(1);
  });

  it("a '/' draft shows the hint instead (one line at a time); the receipt returns once the draft is no longer a slash draft", async () => {
    const { field, button } = mount();
    await submit(field, button);
    fireEvent.change(field, { target: { value: "/op" } });
    expect(screen.getByText(hintLine(en))).toBeTruthy();
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
    fireEvent.change(field, { target: { value: "op" } });
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    expect(document.querySelectorAll(".column-help")).toHaveLength(1);
  });

  it("a failed second submit shows the failure line alone; editing clears the failure, not the receipt", async () => {
    const { field, button } = mount();
    await submit(field, button);
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox not writable"));
    await submit(field, button, "another");
    expect(screen.getByText("Submit failed — input kept")).toBeTruthy();
    expect(screen.queryByText(PROPOSE_OK)).toBeNull(); // 新提交先清了旧回执（状态行只说最新一次）
    fireEvent.change(field, { target: { value: "another!" } });
    expect(screen.queryByText("Submit failed — input kept")).toBeNull();
    expect(document.querySelectorAll(".column-help, .composer-notice, .composer-error")).toHaveLength(0);
  });

  it("a successful slash command replaces the capture receipt with its own note", async () => {
    const { field, button } = mount();
    await submit(field, button);
    await submit(field, button, "/lang en");
    expect(screen.getByText("Language → en")).toBeTruthy();
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
    expect(document.querySelectorAll(".column-help")).toHaveLength(1);
  });
});

describe("LaneComposer receipt — clears when a refresh brings the matching row (PendingSweep.captureMatches)", () => {
  it("propose: a later needs_approval row whose title prefix-matches (normalized) clears the receipt", async () => {
    await setBoard(board("t0"));
    const { field, button } = mount();
    await submit(field, button);
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    await setBoard(board("t1", { needs_approval: [approval("“Write” — the onboarding doc for new hires")] }));
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
  });

  it("a refresh without the row (or with a row in the wrong lane) keeps the receipt", async () => {
    await setBoard(board("t0"));
    const { field, button } = mount();
    await submit(field, button);
    await setBoard(board("t1", { needs_approval: [approval("unrelated proposal")] }));
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    // 同词的 running / review 行不算提案落地
    await setBoard(board("t2", { running: [task(TYPED)], review: [{ id: "R-9", name: TYPED } as unknown as Board["review"][number]] }));
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
  });

  it("the snapshot current at submit time (same generated_at) does not count even if it already holds a matching row", async () => {
    await setBoard(board("t0", { needs_approval: [approval(TYPED)] }));
    const { field, button } = mount();
    await submit(field, button);
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy(); // 用户得先看见「已提交」
    await setBoard(board("t1", { needs_approval: [approval(TYPED)] })); // 下一版里它还在（merge_or_new 并入）→ 落地
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
  });

  it("run: clears against running or needs_input rows, never against review", async () => {
    await setBoard(board("t0"));
    const { field, button } = mount("run");
    await submit(field, button);
    await setBoard(board("t1", { review: [{ id: "R-9", name: TYPED, title: TYPED } as unknown as Board["review"][number]] }));
    expect(screen.getByText(RUN_OK)).toBeTruthy();
    await setBoard(board("t2", { needs_input: [task("write the onboarding doc")] }));
    expect(screen.queryByText(RUN_OK)).toBeNull();
  });

  it("exact key beats the prefix guess: a row carrying the POST's capture_id lands even after the backend rewrote the title", async () => {
    await setBoard(board("t0"));
    vi.mocked(postAction).mockResolvedValueOnce({ ok: true, file: "capture-0f3c.json", action: "capture" });
    const { field, button } = mount();
    await submit(field, button);
    const rewritten = { ...approval("Onboarding handbook v2"), capture_id: "capture-0f3c" } as unknown as Board["needs_approval"][number];
    await setBoard(board("t1", { needs_approval: [rewritten] }));
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
  });

  it("a row born from someone else's capture (different capture_id, different words) does not land this one", async () => {
    await setBoard(board("t0"));
    vi.mocked(postAction).mockResolvedValueOnce({ ok: true, file: "capture-0f3c.json", action: "capture" });
    const { field, button } = mount();
    await submit(field, button);
    const other = { ...approval("Quarterly budget review"), capture_id: "capture-ffff" } as unknown as Board["needs_approval"][number];
    await setBoard(board("t1", { needs_approval: [other] }));
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
  });

  it("no board at submit time: the first snapshot that arrives is checked", async () => {
    const { field, button } = mount();
    await submit(field, button);
    await setBoard(board("t1", { needs_approval: [approval(TYPED)] }));
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
  });
});

describe("LaneComposer receipt — honest timeout (Store.swift sweepTimeouts)", () => {
  it("propose: after 300 s the receipt becomes the yellow 'analysis is slower' notice; not one ms earlier", async () => {
    vi.useFakeTimers();
    const { field, button } = mount();
    await submit(field, button);
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.propose - 1));
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
    const notice = screen.getByRole("status");
    expect(notice.textContent).toBe(PROPOSE_TIMEOUT);
    expect(notice.className).toBe("composer-notice is-propose-timeout");
  });

  it("run: after 180 s the orange 'did not start' notice names the text", async () => {
    vi.useFakeTimers();
    const { field, button } = mount("run");
    await submit(field, button);
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 1));
    expect(screen.getByText(RUN_OK)).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    const notice = screen.getByRole("status");
    expect(notice.textContent).toBe(RUN_TIMEOUT);
    expect(notice.className).toBe("composer-notice is-run-timeout");
  });

  it("the notice fades after 120 s (native LocalNotice lifetime)", async () => {
    vi.useFakeTimers();
    const { field, button } = mount("run");
    await submit(field, button);
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run));
    expect(screen.getByRole("status")).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_NOTICE_FADE_MS - 1));
    expect(screen.getByRole("status")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keystrokes do not disturb the clock; a new submit restarts it", async () => {
    vi.useFakeTimers();
    const { field, button } = mount("run");
    await submit(field, button);
    act(() => vi.advanceTimersByTime(100_000));
    fireEvent.change(field, { target: { value: "typing meanwhile" } });
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 100_000));
    expect(screen.getByRole("status").textContent).toBe(RUN_TIMEOUT);
    await act(async () => {
      fireEvent.click(button); // 新提交替换超时条，时钟重来
    });
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText('"typing meanwhile" Submitted — running it now (skipped proposal), queued for dispatch…')).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 1));
    expect(screen.queryByRole("status")).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("while the pipeline is not ok the clock does not run; recovery re-arms the full window (updateHealth resets `created`)", async () => {
    vi.useFakeTimers();
    await setHealth(health({ verdict: "stalled" }));
    const { field, button } = mount();
    await submit(field, button);
    expect(screen.getByText(PROPOSE_STALLED)).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.propose * 3));
    expect(screen.getByText(PROPOSE_STALLED)).toBeTruthy(); // 没有假警报
    expect(screen.queryByRole("status")).toBeNull();
    await setHealth(health()); // 恢复
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.propose - 1));
    expect(screen.getByText(PROPOSE_OK)).toBeTruthy(); // 整段窗口从恢复那一刻起算
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status").textContent).toBe(PROPOSE_TIMEOUT);
  });

  it("an outage in the middle of the window pauses it and restarts it from zero on recovery", async () => {
    vi.useFakeTimers();
    await setHealth(health());
    const { field, button } = mount("run");
    await submit(field, button);
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 10));
    await setHealth(health({ verdict: "failing", loop_health: { consecutive_failures: 5, last_error: "x" } }));
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText(RUN_STALLED)).toBeTruthy();
    await setHealth(health());
    act(() => vi.advanceTimersByTime(10)); // 旧窗口只剩 10 ms——但已被重置
    expect(screen.getByText(RUN_OK)).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 10));
    expect(screen.getByRole("status").textContent).toBe(RUN_TIMEOUT);
  });

  it("a row landing after the timeout does not resurrect anything; a row landing before it cancels the clock", async () => {
    vi.useFakeTimers();
    await setBoard(board("t0"));
    const { field, button } = mount();
    await submit(field, button);
    await setBoard(board("t1", { needs_approval: [approval(TYPED)] }));
    expect(screen.queryByText(PROPOSE_OK)).toBeNull();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.propose + 1));
    expect(screen.queryByRole("status")).toBeNull(); // 落地了就没有超时条
  });
});
