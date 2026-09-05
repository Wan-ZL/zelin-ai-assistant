// 「清理积压」按钮的回执诚实纪律（CONTRACT §34bis / §10 / §41 2026-09-05 追记；原生 AppDelegate.submitProposalsTriage →
// store.beginCapture(run: true) → Cards.swift:848,863-867 RunCapturePendingRow 的状态句）：
//   1) 管线 ok → 「已提交，直接开跑（跳过提案），排队派发中…」；stalled / failing / stale → 「已保存到队列，pipeline 启动后直接开跑」；
//   2) 健康在回执挂着时变了，句子跟着变（原生 body 每次重算 stalled）；
//   3) 失败仍是 server 原文，不套状态句；payload 不变（preset 信号 + 短标签 + mode:"run"）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, postAction } from "../../api";
import { refreshHealth, resetStoreForTests } from "../../store";
import type { HealthSnapshot } from "../../types";
import { PROPOSALS_TRIAGE_PRESET, PROPOSALS_TRIAGE_TEXT, ProposalsTriageButton } from "./ProposalsTriageButton";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn(),
  fetchHealth: vi.fn(),
}));

const OK = "Submitted — running it now (skipped proposal), queued for dispatch…";
const STALLED = "Saved to the queue — runs once the pipeline is up";

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
});

afterEach(cleanup);

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
