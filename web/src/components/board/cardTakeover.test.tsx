// 双击卡片 = 在终端接管会话（CONTRACT §54.1 第 11 项 / §68.7 2026-09-05 追记，issue #216；D36 2026-09-06）：
//   1) 有可接管会话的卡（执行中 copy_cmd / session_id、待验收 copy_cmd、受阻卡）双击 → POST /api/terminal 只带 card_id，
//      成功一句「已在终端打开」（role=status）；卡面没有「在终端接管」按钮、也没有「单击复制指令」行；
//      单击卡身什么也不做（D36）——不发请求、不开详情、不复制；
//   2) 没有会话的卡（排队 / 提案 / 没 copy_cmd 的待验收）双击 no-op：不发请求、不开详情（400 语义前移到 UI）；
//   3) 键盘 Enter 仍是打开详情侧栏，绝不触发接管；卡内按钮上的双击归按钮；
//   4) 降级：server 501（非 darwin）/ 503 SHELL_UNAVAILABLE（壳没在跑）→ 复制指令到剪贴板 + 提示句；其它错误红字 + 原句；
//   5) 在途中重复双击只发一次；
//   6) §21 多选态里双击 = 两次切换选中，不接管；
//   7) 卡尾的 role=status 节点常驻（空时 :empty 收起）——读屏才播报得到「已在终端打开」/ 降级提示。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { getState, resetStoreForTests, setSelectionMode } from "../../store";
import type { ReviewCard as ReviewRow, TaskRow } from "../../types";
import { PROPOSAL_T1, REVIEW_FIXTURE, TASK_BLOCKED, TASK_QUEUED, TASK_WORKING } from "../styleguide/fixtures";
import { ProposalCard } from "./ProposalCard";
import { ReviewCard } from "./ReviewCard";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  postTerminal: vi.fn().mockResolvedValue({ ok: true, command: "claude attach falcon", command_file: "/h/state/terminal_queue/x.json", cwd: "/h", queue_id: "x" }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));
import { postTerminal } from "../../api";

const writeText = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(postTerminal).mockClear();
  vi.mocked(postTerminal).mockResolvedValue({ ok: true, command: "claude attach falcon", command_file: "/h/state/terminal_queue/x.json", cwd: "/h", queue_id: "x" });
  writeText.mockClear();
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
});
afterEach(cleanup);

const article = (name: RegExp) => screen.getByRole("article", { name });

describe("double-click = take over in a terminal (#216)", () => {
  it("working card: double-click → POST /api/terminal {card_id} → 「Opened in terminal」 status line; no button, no copy line on the card", async () => {
    render(<RunningCard row={TASK_WORKING} />);
    expect(screen.queryByRole("button", { name: /Open in Terminal/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /copy the command/i })).toBeNull();   // D36：卡面无「单击复制指令」行
    expect(screen.queryByText(TASK_WORKING.copy_cmd as string)).toBeNull();          // 命令只在侧栏（DetailFields）
    fireEvent.doubleClick(article(/^Working · /));
    expect(postTerminal).toHaveBeenCalledTimes(1);
    expect(postTerminal).toHaveBeenCalledWith(TASK_WORKING.id);
    await screen.findByText("Opened in terminal", { selector: "[role='status'] span" });
    expect(getState().selectedCardId).toBeNull(); // 双击不再是详情入口
  });

  it("a single click on the card body does nothing (D36): no request, no detail, no copy", () => {
    render(<><RunningCard row={TASK_WORKING} /><ReviewCard card={REVIEW_FIXTURE} /></>);
    fireEvent.click(article(/^Working · /));
    fireEvent.click(article(/^In review · /));
    expect(postTerminal).not.toHaveBeenCalled();
    expect(writeText).not.toHaveBeenCalled();
    expect(getState().selectedCardId).toBeNull();
  });

  it("review card with copy_cmd takes over; review card without copy_cmd is a no-op", () => {
    const { unmount } = render(<ReviewCard card={REVIEW_FIXTURE} />);
    fireEvent.doubleClick(article(/^In review · /));
    expect(postTerminal).toHaveBeenCalledWith(REVIEW_FIXTURE.id);
    unmount();
    vi.mocked(postTerminal).mockClear();
    const bare: ReviewRow = { ...REVIEW_FIXTURE, id: "SG-REVIEW-2", copy_cmd: undefined };
    render(<ReviewCard card={bare} />);
    fireEvent.doubleClick(article(/^In review · /));
    expect(postTerminal).not.toHaveBeenCalled();
  });

  it("blocked card (session exists) takes over — §39 second path into the terminal", () => {
    const blocked: TaskRow = { ...TASK_BLOCKED, session_id: "0123abcd" };
    render(<RunningCard row={blocked} isBlocked />);
    fireEvent.doubleClick(article(/^Needs input · /));
    expect(postTerminal).toHaveBeenCalledWith(blocked.id);
  });

  it("cards without a session (queued / proposal) ignore double-click: no request, no detail", () => {
    render(<><RunningCard row={TASK_QUEUED} /><ProposalCard card={PROPOSAL_T1} /></>);
    fireEvent.doubleClick(article(/^Queued · /));
    fireEvent.doubleClick(article(/^Proposal · /));
    expect(postTerminal).not.toHaveBeenCalled();
    expect(getState().selectedCardId).toBeNull();
  });

  it("Enter on the card opens the detail sidebar and never takes over", () => {
    render(<RunningCard row={TASK_WORKING} />);
    const surface = article(/^Working · /);
    surface.focus();
    fireEvent.keyDown(surface, { key: "Enter" });
    expect(getState().selectedCardId).toBe(TASK_WORKING.id);
    expect(postTerminal).not.toHaveBeenCalled();
  });

  it("double-click on an inner button belongs to the button; on the card's own text it takes over", () => {
    render(<RunningCard row={TASK_WORKING} />);
    fireEvent.doubleClick(screen.getByRole("button", { name: "Stop" }));
    fireEvent.doubleClick(screen.getByRole("button", { name: /Details/ }));
    expect(postTerminal).not.toHaveBeenCalled();
    fireEvent.doubleClick(screen.getByText(TASK_WORKING.name));   // 标题文字 = 卡身，不是控件
    expect(postTerminal).toHaveBeenCalledTimes(1);
  });

  it("503 SHELL_UNAVAILABLE (shell not running) → copies the command + one hint; 501 (not macOS) takes the same path", async () => {
    vi.mocked(postTerminal).mockRejectedValueOnce(new ApiError(503, { error: { code: "SHELL_UNAVAILABLE", message: "the app is not running" } }));
    const { unmount } = render(<RunningCard row={TASK_WORKING} />);
    fireEvent.doubleClick(article(/^Working · /));
    await screen.findByText(/Cannot open a terminal from here · command copied/, { selector: "[role='status'] span" });
    expect(writeText).toHaveBeenCalledWith("claude attach falcon");
    expect(document.querySelector(".card-takeover-status")?.classList.contains("is-danger")).toBe(false);
    expect(document.querySelector(".card-takeover-status")?.textContent).toContain("the app is not running");
    unmount();
    writeText.mockClear();
    vi.mocked(postTerminal).mockRejectedValueOnce(new ApiError(501, { error: { code: "NOT_IMPLEMENTED", message: "macOS only" } }));
    render(<RunningCard row={TASK_WORKING} />);
    fireEvent.doubleClick(article(/^Working · /));
    await screen.findByText(/command copied/, { selector: "[role='status'] span" });
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it("any other failure → 「Terminal launch failed」 in red with the server sentence; nothing copied", async () => {
    vi.mocked(postTerminal).mockRejectedValueOnce(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "could not queue the terminal request: EACCES" } }));
    render(<RunningCard row={TASK_WORKING} />);
    fireEvent.doubleClick(article(/^Working · /));
    await screen.findByText("Terminal launch failed", { selector: "[role='status'] span" });
    const status = document.querySelector(".card-takeover-status")!;
    expect(status.classList.contains("is-danger")).toBe(true);
    expect(status.textContent).toContain("could not queue the terminal request: EACCES");
    expect(writeText).not.toHaveBeenCalled();
  });

  it("double-click in §21 selection mode never takes over (two toggles, no terminal)", () => {
    setSelectionMode(true);
    render(<RunningCard row={TASK_WORKING} />);
    const surface = article(/^Working · /);
    fireEvent.doubleClick(surface);
    expect(postTerminal).not.toHaveBeenCalled();
    expect(writeText).not.toHaveBeenCalled();
    // 双击 = 两次 click：选中再取消（切换两次回到未选），详情也没开
    fireEvent.click(surface);
    expect(getState().selectedIds.has(TASK_WORKING.id)).toBe(true);
    fireEvent.click(surface);
    expect(getState().selectedIds.has(TASK_WORKING.id)).toBe(false);
    expect(getState().selectedCardId).toBeNull();
  });

  it("the role=status live region is mounted before any status arrives (screen readers announce changes, not insertions)", async () => {
    render(<RunningCard row={TASK_WORKING} />);
    const region = article(/^Working · /).querySelector(".card-takeover-status")!;
    expect(region.getAttribute("role")).toBe("status");
    expect(region.textContent).toBe("");   // 空 → CSS :empty 收成 sr-only 尺寸
    fireEvent.doubleClick(article(/^Working · /));
    await waitFor(() => expect(region.textContent).toBe("Opened in terminal"));
  });

  it("repeated double-clicks while a request is in flight send one request", async () => {
    let release: (v: unknown) => void = () => {};
    vi.mocked(postTerminal).mockReturnValueOnce(new Promise((resolve) => { release = resolve; }) as never);
    render(<RunningCard row={TASK_WORKING} />);
    const surface = article(/^Working · /);
    fireEvent.doubleClick(surface);
    fireEvent.doubleClick(surface);
    fireEvent.doubleClick(surface);
    expect(postTerminal).toHaveBeenCalledTimes(1);
    release({ ok: true });
    await waitFor(() => expect(screen.getByText("Opened in terminal")).toBeTruthy());
  });
});
