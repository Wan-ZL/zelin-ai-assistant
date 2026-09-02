// 原生看板 parity 判例（mac/Sources/Cards.swift 为规格）：
//   1) 详情默认收起，「Details ▸ / Collapse ▾」按卡切换，展开态按卡 id 在会话内记忆（卸载重挂仍展开）；
//   2) 卡面 chips / 行从投影字段渲染：提案落点行 + 已并入×N；待验收 repo 章 + 耗时 + 已等待验收；
//      阶段性完成 已交付 + repo 章 + 验收于（相对时间，hover 绝对）+ 单击复制指令；
//   3) 出错的执行卡：让 AI 修（POST /api/ai-fix，只传 card_id + lang）+ 回答…（comment/steer 四键形）+ 停止；
//   4) 卡 id 在标题行右侧（.card-head 内）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { ApprovalCard, ReviewCard as ReviewRow, TaskRow } from "../../types";
import { DoneCard } from "./DoneCard";
import { ProposalCard } from "./ProposalCard";
import { ReviewCard } from "./ReviewCard";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  postAiFix: vi.fn().mockResolvedValue({ ok: true, command_file: "/tmp/x.command" }),
}));
import { postAction, postAiFix } from "../../api";

beforeEach(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  resetStoreForTests();
  vi.mocked(postAction).mockClear();
  vi.mocked(postAiFix).mockClear();
});

afterEach(cleanup);

const NOW_S = Math.floor(Date.now() / 1000);

function proposal(extra: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    id: "R-301",
    title: "leaderboard 一键导出评测报告（技术标题）",
    summary: "在 dashboard 加导出按钮",
    tier: "T1",
    show_cost: false,
    processing: false,
    sources: [{ who: "sam", channel: "slack", date: "2026-08-30", quote: "能不能一键导出" }],
    plan: ["加按钮", "接后端"],
    dod: ["点击后下载 CSV"],
    ...extra,
  };
}

describe("details collapsed by default + per-card memory", () => {
  it("提案卡：plan/DoD/来源默认不在 DOM；点 Details ▸ 出现；再点 Collapse ▾ 收起", () => {
    render(<ProposalCard card={proposal()} />);
    expect(screen.queryByText(/接后端/)).toBeNull();
    expect(screen.queryByText(/点击后下载 CSV/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    expect(screen.getByText(/接后端/)).toBeTruthy();
    expect(screen.getByText(/点击后下载 CSV/)).toBeTruthy();
    expect(screen.getByText(/能不能一键导出/)).toBeTruthy();
    expect(screen.getByText(/技术标题/)).toBeTruthy(); // 长技术标题住在详情里
    fireEvent.click(screen.getByRole("button", { name: "Collapse ▾" }));
    expect(screen.queryByText(/接后端/)).toBeNull();
  });

  it("展开态按卡 id 记忆：卸载重挂仍展开；另一张卡不受影响", () => {
    const { unmount } = render(<ProposalCard card={proposal()} />);
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    unmount();
    render(
      <>
        <ProposalCard card={proposal()} />
        <ProposalCard card={proposal({ id: "R-302", plan: ["另一张卡的步骤"] })} />
      </>,
    );
    expect(screen.getByText(/接后端/)).toBeTruthy();
    expect(screen.queryByText(/另一张卡的步骤/)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Collapse ▾" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Details ▸" })).toHaveLength(1);
  });

  it("卡 id 在标题行右侧（.card-head 内的 .card-id）", () => {
    const { container } = render(<ProposalCard card={proposal()} />);
    const head = container.querySelector(".card-head")!;
    expect(head.querySelector(".card-title")?.textContent).toBe("在 dashboard 加导出按钮");
    expect(head.querySelector(".card-id")?.textContent).toBe("R-301");
  });
});

describe("proposal chips from projection fields", () => {
  it("落点行三态：新建 repo 绿 / your-workbench 只出文档 / 改现有 橙（basename 来自 target_name 或 target_repo）", () => {
    const { unmount } = render(<ProposalCard card={proposal({ target_kind: "new", target_name: "acme-site" })} />);
    expect(screen.getByText("🟢 New repo: acme-site").className).toContain("is-success");
    unmount();
    const { unmount: u2 } = render(<ProposalCard card={proposal({ target_kind: "existing", target_repo: "/Users/z/Projects/your-workbench" })} />);
    expect(screen.getByText(/Drafts land in: your-workbench \(documents only, no code touched\)/)).toBeTruthy();
    u2();
    render(<ProposalCard card={proposal({ target_kind: "existing", target_repo: "/Users/z/Projects/zelin-ai-assistant/" })} />);
    expect(screen.getByText("🟠 Modify existing: zelin-ai-assistant (draft PR only, main branch untouched)").className).toContain("is-warning");
  });

  it("无 target_kind 不渲染落点行；已并入×N 紫 quiet 章只在 silent_merged ≥ 1 时出现", () => {
    const { container, unmount } = render(<ProposalCard card={proposal({ silent_merged: 0 })} />);
    expect(container.querySelector(".card-line.is-success, .card-line.is-warning")).toBeNull();
    expect(screen.queryByText(/Folded ×/)).toBeNull();
    unmount();
    render(<ProposalCard card={proposal({ silent_merged: 2 })} />);
    const chip = screen.getByText("Folded ×2");
    expect(chip.className).toContain("chip-purple");
    expect(chip.className).toContain("chip-quiet");
    expect(chip.getAttribute("title")).toMatch(/2 duplicate card/);
  });
});

describe("review card meta line", () => {
  it("repo 章 + 耗时 + 已等待验收（自驱时长）+ 单击复制指令；DoD 在详情里", () => {
    const card: ReviewRow = {
      id: "R-410",
      name: "周报成稿",
      dod: ["覆盖三条来源"],
      delivery_mode: "chat",
      cwd: "/Volumes/x/Projects/your-workbench",
      copy_cmd: "cd '/tmp/w' && claude --resume abc",
      dispatched_at: NOW_S - 3 * 3600 - 59 * 60 - 600,
      review_at: NOW_S - 600,
      delivered_summary: "已按 DoD 完成成稿",
    };
    render(<ReviewCard card={card} />);
    expect(screen.getByText("your-workbench").className).toContain("chip");
    expect(screen.getByText("took 3h 59m")).toBeTruthy();
    expect(screen.getByText("in review 10m")).toBeTruthy();
    const copyLine = screen.getByRole("button", { name: /Click to copy the command/ });
    expect(copyLine.getAttribute("title")).toBe("cd '/tmp/w' && claude --resume abc");
    expect(screen.queryByText(/覆盖三条来源/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    expect(screen.getByText(/☐ 覆盖三条来源/)).toBeTruthy();
    expect(screen.getByText("Delivered:")).toBeTruthy();
  });
});

describe("done card meta line", () => {
  it("已交付 章 + repo 章 + 验收于 <相对时间>（hover 绝对）+ 单击复制指令", () => {
    const row: TaskRow = {
      id: "R-520",
      name: "已验收的任务",
      state: "delivered",
      cwd: "/Users/z/Projects/acme",
      copy_cmd: "claude --resume 1234",
      accepted_at: NOW_S - 19 * 86400 - 3600,
    };
    render(<DoneCard row={row} />);
    expect(screen.getByText("Delivered").className).toContain("chip-success");
    expect(screen.getByText("acme").className).toContain("chip");
    const accepted = screen.getByText("accepted 19d ago");
    expect(accepted.getAttribute("title")).toBe(new Date((NOW_S - 19 * 86400 - 3600) * 1000).toLocaleString("en"));
    expect(screen.getByRole("button", { name: /Click to copy the command/ }).getAttribute("title")).toBe("claude --resume 1234");
    expect(screen.getByRole("button", { name: "Back to review" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Done for good" })).toBeTruthy();
  });
});

describe("failed running card: 让 AI 修 + 回答… + 停止", () => {
  const failed: TaskRow = {
    id: "R-610",
    name: "修 flaky e2e",
    state: "working",
    session_id: "sess-1",
    last_error: "Traceback: boom",
    started_at: NOW_S - 2 * 3600,
    cwd: "/x/zelin-ai-assistant",
  };

  it("按钮齐：Fix with AI · Answer… · Stop；无错误的卡是 Comment · Stop", () => {
    const { unmount } = render(<RunningCard row={failed} />);
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Answer…" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Comment" })).toBeNull();
    expect(screen.getByText(/Error: Traceback: boom/)).toBeTruthy();
    expect(screen.getByText("2h ago")).toBeTruthy(); // 运行时长相对时间
    expect(screen.getByText("zelin-ai-assistant").className).toContain("chip"); // repo 章
    expect(screen.queryByText("Idle")).toBeNull(); // working 由 sheen 行表达，不再出状态章
    unmount();
    const { unmount: u2 } = render(<RunningCard row={{ ...failed, state: "idle" }} />);
    expect(screen.getByText("Idle").className).toContain("chip-info"); // 非常规状态才出章
    u2();
    render(<RunningCard row={{ ...failed, last_error: undefined }} />);
    expect(screen.queryByRole("button", { name: "Fix with AI" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Answer…" })).toBeNull();
    expect(screen.getByRole("button", { name: "Comment" })).toBeTruthy();
  });

  it("让 AI 修 → POST /api/ai-fix 只带 card_id + lang，成功后显示已在 Terminal 打开", async () => {
    render(<RunningCard row={failed} />);
    fireEvent.click(screen.getByRole("button", { name: "Fix with AI" }));
    expect(postAiFix).toHaveBeenCalledWith("R-610", "en");
    expect(await screen.findByText(/Repair session opened in Terminal/)).toBeTruthy();
    expect(postAction).not.toHaveBeenCalled(); // 不是 inbox 动作
  });

  it("让 AI 修 失败 → 红字带 server 原句", async () => {
    vi.mocked(postAiFix).mockRejectedValueOnce(new Error("Fix with AI is disabled in config.yaml"));
    render(<RunningCard row={failed} />);
    fireEvent.click(screen.getByRole("button", { name: "Fix with AI" }));
    const line = await screen.findByText(/Fix with AI failed to launch: Fix with AI is disabled/);
    expect(line.className).toContain("is-danger");
  });

  it("回答… → comment 四键形（steer 通道），wire 与评论完全相同", () => {
    render(<RunningCard row={failed} />);
    fireEvent.click(screen.getByRole("button", { name: "Answer…" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "跳过这个用例" } });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "comment", comment: "跳过这个用例", id: "R-610" });
  });

  it("排队卡派发失败：Dispatch failed 一句 + Fix with AI；无 Answer…（没有会话）", () => {
    render(<RunningCard row={{ id: "R-611", name: "排队", state: "queued", dispatch_error: "spawn failed" }} />);
    expect(screen.getByText(/Dispatch failed: spawn failed/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Answer…" })).toBeNull();
    expect(screen.getByRole("button", { name: "Comment" })).toBeTruthy();
  });
});
