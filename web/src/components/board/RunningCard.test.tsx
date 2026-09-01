// RunningCard 的 M6 增面行为测试：
//   1) queued 卡的结构化排队原因 chip（§M6.2；对象形/字符串形/缺席三态）；
//   2) working 卡的 steer 回执 chips（§M6.1；诚实三态计数）；
//   3) comment 即 steer：wire 仍是 §3 四键形（无新字段），server 响应 steer:true
//      时 pending 文案升级为「Submitted · steer queued…」（排队回执）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { TaskRow } from "../../types";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { postAction } from "../../api";

// jsdom <dialog> 兜底（同 ProposalCard.test.tsx）
beforeEach(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.open = false;
    };
  }
  resetStoreForTests();
  vi.mocked(postAction).mockClear();
  vi.mocked(postAction).mockResolvedValue({ ok: true });
});

afterEach(cleanup);

function queuedRow(extra: Partial<TaskRow> = {}): TaskRow {
  return { id: "R-106", name: "README 快速上手重写", state: "queued", ...extra };
}

function workingRow(extra: Partial<TaskRow> = {}): TaskRow {
  return { id: "R-105", name: "修 flaky e2e", state: "working", ...extra };
}

describe("queued card reason chip", () => {
  it("结构化 queued_reason（waiting_card）→「waiting on R-xx」chip", () => {
    render(<RunningCard row={queuedRow({ queued_reason: { kind: "waiting_card", blocking_id: "R-105" } })} />);
    expect(screen.getByText("waiting on R-105")).toBeTruthy();
  });

  it("字符串形 queued_reason 原样透传；缺席不渲染额外 chip", () => {
    const { unmount } = render(<RunningCard row={queuedRow({ queued_reason: "等预算窗口" })} />);
    expect(screen.getByText("等预算窗口")).toBeTruthy();
    unmount();
    render(<RunningCard row={queuedRow()} />);
    // 只有「Queued」状态 chip，无原因 chip
    expect(screen.getByText("Queued")).toBeTruthy();
    expect(document.querySelectorAll(".card-badges .chip")).toHaveLength(1);
  });
});

describe("working card steer chips", () => {
  it("steers[] 按诚实三态计数出 chips；缺席不渲染", () => {
    const { unmount } = render(
      <RunningCard
        row={workingRow({
          steers: [
            { ts: "t1", status: "queued" },
            { ts: "t2", status: "delivered" },
            { ts: "t3", status: "delivered" },
            { ts: "t4", status: "dropped" },
          ],
        })}
      />,
    );
    expect(screen.getByText("Steer queued ×1")).toBeTruthy();
    expect(screen.getByText("Steer delivered ×2")).toBeTruthy();
    expect(screen.getByText("Steer dropped ×1")).toBeTruthy();
    unmount();
    render(<RunningCard row={workingRow()} />);
    expect(screen.queryByText(/Steer/)).toBeNull();
  });
});

describe("comment-as-steer flow", () => {
  it("working 卡 comment：wire 仍是 {action,comment,id} 四键形；steer:true 响应 → 排队回执文案", async () => {
    vi.mocked(postAction).mockResolvedValue({ ok: true, steer: true, steer_status: "queued" });
    render(<RunningCard row={workingRow()} />);
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "先别动 schema" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "comment",
      comment: "先别动 schema",
      id: "R-105",
    });
    expect(await screen.findByText("Submitted · steer queued…")).toBeTruthy();
  });

  it("无 steer 标注的响应 → 普通「Submitted…」文案", async () => {
    render(<RunningCard row={queuedRow()} />);
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "备注一下" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByText("Submitted…")).toBeTruthy();
  });
});

describe("dispatch-halted blocked row (§4 storm brake)", () => {
  function haltedRow(): TaskRow {
    return {
      id: "R-175",
      name: "被 fd 上限卡住的卡",
      state: "blocked",
      dispatch_halted: true,
      dispatch_attempts: 5,
      last_error: "error: An unknown error occurred, possibly due to low max file descriptors",
      question: "Launch failed 5 times in a row; auto-retry stopped: fd limit. Fix the cause…",
    };
  }

  it("shows the halt chip with the attempt count and the fixed explanation", () => {
    render(<RunningCard row={haltedRow()} isBlocked />);
    expect(screen.getByText("Launch stopped ×5")).toBeTruthy();
    expect(screen.getByText(/auto-retry stopped/)).toBeTruthy();
  });

  it("offers Stop but never Answer… (there is no session to answer)", () => {
    render(<RunningCard row={haltedRow()} isBlocked />);
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Answer…" })).toBeNull();
  });

  it("blocked rows never offer Answer… (#119: answer_input retired)", () => {
    render(<RunningCard row={{ id: "R-1", name: "x", state: "blocked", question: "A or B?" }} isBlocked />);
    expect(screen.queryByRole("button", { name: "Answer…" })).toBeNull();
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    expect(screen.queryByText(/Launch stopped/)).toBeNull();
  });
});
