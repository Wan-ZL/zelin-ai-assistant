// 「清理积压」按钮的回执诚实纪律（CONTRACT §34bis / §10 / §41 2026-09-05 追记；原生 AppDelegate.submitProposalsTriage →
// store.beginCapture(run: true) → Cards.swift:848,863-867 RunCapturePendingRow 的状态句）：
//   1) 管线 ok → 「已提交，直接开跑（跳过提案），排队派发中…」；stalled / failing / stale → 「已保存到队列，pipeline 启动后直接开跑」；
//   2) 健康在回执挂着时变了，句子跟着变（原生 body 每次重算 stalled）；
//   3) 失败仍是 server 原文，不套状态句；payload 不变（preset 信号 + 短标签 + mode:"run"）；
//   4) 寿命 = 列顶输入框直跑回执的寿命（useCaptureReceipt，原生同一个 beginCapture(run: true) 占位卡）：刷新带来
//      running / needs_input 里名字前缀匹配短标签的行即清（review 不算、提交那一刻的快照不算）；否则 180 s 后换成原生
//      橙色超时条「「<短标签前 20 字>」任务没有开始——后台可能没在跑（检查 actd）」，管线不 ok 时不计时，120 s 褪去；
//      失败的再点一次不替换上一份回执。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchHealth, postAction } from "../../api";
import { refreshBoard, refreshHealth, resetStoreForTests } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { CAPTURE_NOTICE_FADE_MS, CAPTURE_TIMEOUT_MS, clip20 } from "./captureReceipt";
import { PROPOSALS_TRIAGE_PRESET, PROPOSALS_TRIAGE_TEXT, ProposalsTriageButton } from "./ProposalsTriageButton";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn(),
  fetchHealth: vi.fn(),
  fetchBoard: vi.fn(),
}));

const OK = "Submitted — running it now (skipped proposal), queued for dispatch…";
const STALLED = "Saved to the queue — runs once the pipeline is up";
const TIMEOUT = `"${clip20(PROPOSALS_TRIAGE_TEXT)}" did not start — the backend may not be running (check actd)`;

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

const task = (name: string) => ({ id: "R-1", name, state: "queued" }) as unknown as Board["running"][number];

async function setBoard(next: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(next);
  await act(async () => {
    await refreshBoard();
  });
}

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

async function setHealth(snapshot: HealthSnapshot) {
  vi.mocked(fetchHealth).mockResolvedValue(snapshot);
  await act(async () => {
    await refreshHealth();
  });
}

async function fire() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /Clean up/ }));
  });
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(postAction).mockReset();
  vi.mocked(postAction).mockResolvedValue({ ok: true });
  vi.mocked(fetchHealth).mockReset();
  vi.mocked(fetchBoard).mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("ProposalsTriageButton — receipt says where the run actually is", () => {
  it("pipeline ok (or no health yet): the native direct-run receipt; payload unchanged", async () => {
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    expect(postAction).toHaveBeenCalledWith({ action: "capture", text: PROPOSALS_TRIAGE_TEXT, mode: "run", preset: PROPOSALS_TRIAGE_PRESET });
    expect(screen.getByText(OK)).toBeTruthy();
  });

  for (const [label, snap] of [
    ["stalled", health({ verdict: "stalled" })],
    ["failing", health({ verdict: "failing", loop_health: { consecutive_failures: 3, last_error: "boom" } })],
    ["stale", health({ verdict: "stale", heartbeat: null })],
  ] as const) {
    it(`pipeline ${label}: saved-to-queue wording instead of 'queued for dispatch'`, async () => {
      await setHealth(snap);
      render(<ProposalsTriageButton backlogCount={3} />);
      await fire();
      expect(screen.getByText(STALLED)).toBeTruthy();
      expect(screen.queryByText(OK)).toBeNull();
    });
  }

  it("unknown verdict is not stalled (banner predicate, not `verdict !== ok`)", async () => {
    await setHealth(health({ verdict: "unknown", heartbeat: null }));
    render(<ProposalsTriageButton backlogCount={1} />);
    await fire();
    expect(screen.getByText(OK)).toBeTruthy();
  });

  it("flips live with health while the receipt is showing", async () => {
    await setHealth(health());
    render(<ProposalsTriageButton backlogCount={1} />);
    await fire();
    expect(screen.getByText(OK)).toBeTruthy();
    await setHealth(health({ verdict: "stalled" }));
    expect(screen.getByText(STALLED)).toBeTruthy();
    await setHealth(health());
    expect(screen.getByText(OK)).toBeTruthy();
  });

  it("a failed submit shows the server's words, not a status sentence", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox not writable"));
    render(<ProposalsTriageButton backlogCount={1} />);
    await fire();
    expect(screen.getByText(/inbox not writable/)).toBeTruthy();
    expect(screen.queryByText(OK)).toBeNull();
    expect(screen.queryByText(STALLED)).toBeNull();
  });
});

describe("ProposalsTriageButton — the receipt has the direct-run placeholder's lifetime (same beginCapture(run: true))", () => {
  it("clears when a later snapshot carries the triage session in Running (name = the short label, normalized prefix)", async () => {
    await setBoard(board("t0"));
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    expect(screen.getByText(OK)).toBeTruthy();
    await setBoard(board("t1", { running: [task("unrelated run")] }));
    expect(screen.getByText(OK)).toBeTruthy(); // 别的行不算
    await setBoard(board("t2", { running: [task(PROPOSALS_TRIAGE_TEXT)] }));
    expect(screen.queryByText(OK)).toBeNull();
  });

  it("a needs_input row counts; a review row and the submit-time snapshot do not", async () => {
    await setBoard(board("t0", { running: [task(PROPOSALS_TRIAGE_TEXT)] })); // 提交那一刻已有同名行（上一轮清理留下的）
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    expect(screen.getByText(OK)).toBeTruthy(); // 同一帧不清
    await setBoard(board("t1", { review: [{ id: "R-9", name: PROPOSALS_TRIAGE_TEXT } as unknown as Board["review"][number]] }));
    expect(screen.getByText(OK)).toBeTruthy(); // 一周前的待验收清理卡不是这次的
    await setBoard(board("t2", { needs_input: [task(PROPOSALS_TRIAGE_TEXT)] }));
    expect(screen.queryByText(OK)).toBeNull();
  });

  it("exact key: a running row carrying the POST's capture_id lands it even under a rewritten name", async () => {
    await setBoard(board("t0"));
    vi.mocked(postAction).mockResolvedValueOnce({ ok: true, file: "capture-77aa.json", action: "capture" });
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    await setBoard(board("t1", { running: [{ ...task("Backlog triage"), capture_id: "capture-77aa" } as unknown as Board["running"][number]] }));
    expect(screen.queryByText(OK)).toBeNull();
  });

  it("after 180 s without the row the note becomes the orange 'did not start' notice naming the label; it fades after 120 s", async () => {
    vi.useFakeTimers();
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 1));
    expect(screen.getByText(OK)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText(OK)).toBeNull();
    const notice = screen.getByRole("status");
    expect(notice.textContent).toBe(TIMEOUT);
    expect(notice.className).toBe("composer-notice is-run-timeout");
    act(() => vi.advanceTimersByTime(CAPTURE_NOTICE_FADE_MS - 1));
    expect(screen.getByRole("status")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("status")).toBeNull();
    expect(document.querySelector(".lane-triage .card-meta-text")).toBeNull(); // 什么都不剩：不再永久挂着「排队派发中…」
  });

  it("while the pipeline is not ok the clock does not run; recovery re-arms the full window", async () => {
    vi.useFakeTimers();
    await setHealth(health({ verdict: "stalled" }));
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    expect(screen.getByText(STALLED)).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run * 3));
    expect(screen.getByText(STALLED)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    await setHealth(health());
    expect(screen.getByText(OK)).toBeTruthy();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 1));
    expect(screen.getByText(OK)).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status").textContent).toBe(TIMEOUT);
  });

  it("a row landing before the timeout cancels the clock (no notice ever)", async () => {
    vi.useFakeTimers();
    await setBoard(board("t0"));
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    await setBoard(board("t1", { running: [task(PROPOSALS_TRIAGE_TEXT)] }));
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run + 1));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText(OK)).toBeNull();
  });

  it("a failed second click shows the error in front but does not replace the first receipt", async () => {
    vi.useFakeTimers();
    render(<ProposalsTriageButton backlogCount={3} />);
    await fire();
    act(() => vi.advanceTimersByTime(2000)); // 2 s 防连点过去
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox not writable"));
    await fire();
    expect(screen.getByText(/inbox not writable/)).toBeTruthy();
    expect(screen.queryByText(OK)).toBeNull();
    act(() => vi.advanceTimersByTime(2000));
    await fire(); // 第三次成功 → 替换回执，时钟重来
    expect(screen.getByText(OK)).toBeTruthy();
    expect(screen.queryByText(/inbox not writable/)).toBeNull();
    act(() => vi.advanceTimersByTime(CAPTURE_TIMEOUT_MS.run - 1));
    expect(screen.queryByRole("status")).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status").textContent).toBe(TIMEOUT);
  });
});
