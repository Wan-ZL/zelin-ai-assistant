// §21 多选态下卡的动作行对 a11y 树也失效（CONTRACT §54.1 2026-09-05 追记 (a)；原生 Kanban.swift:671-705 的
// tap catcher 盖住整卡，按钮本就不可达）：CardSurface 在 selecting 时给 `.card-actions` 挂 `inert`——
// 不在 tab 序、读屏不报「批准, 按钮」；退出多选即摘掉；不可选的卡（AI 研究中占位）从不挂；
// 挂在动作行上而不是逐颗按钮 disabled（每张卡各改一遍），CSS 指针穿透 + capture 兜底不变。
// jsdom 不实现 inert 语义（焦点 / 事件照常），这里钉的是 attribute 本身；真浏览器的 tab 序由 review probe 验过。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests, setSelectionMode } from "../../store";
import { PROPOSAL_PROCESSING, PROPOSAL_T1, TASK_BLOCKED, TASK_DONE } from "../styleguide/fixtures";
import { DoneCard } from "./DoneCard";
import { ProposalCard } from "./ProposalCard";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});
afterEach(cleanup);

const actionsRow = () => document.querySelector(".card-actions") as HTMLElement;

describe("selection mode makes the action row inert", () => {
  it("多选态：.card-actions 带 inert；退出多选即摘掉", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_T1} />);
    expect(actionsRow().hasAttribute("inert")).toBe(true);
    act(() => setSelectionMode(false));
    expect(actionsRow().hasAttribute("inert")).toBe(false);
  });

  it("进入多选态时已挂载的卡也补上 inert（不只看首次 render）", () => {
    render(<DoneCard row={TASK_DONE} />);
    expect(actionsRow().hasAttribute("inert")).toBe(false);
    act(() => setSelectionMode(true));
    expect(actionsRow().hasAttribute("inert")).toBe(true);
  });

  it("需输入行（全 lane 可选）的动作行同样 inert", () => {
    setSelectionMode(true);
    render(<RunningCard row={TASK_BLOCKED} />);
    expect(actionsRow().hasAttribute("inert")).toBe(true);
  });

  it("不可选的 AI 研究中占位：多选态下动作行不挂 inert", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_PROCESSING} />);
    const row = actionsRow();
    // 占位卡若没有动作行，也不该有任何 inert 元素
    expect(row ? row.hasAttribute("inert") : false).toBe(false);
    expect(document.querySelector("[inert]")).toBeNull();
  });

  it("非多选态：任何卡都没有 inert", () => {
    render(<><ProposalCard card={PROPOSAL_T1} /><DoneCard row={TASK_DONE} /></>);
    expect(document.querySelector("[inert]")).toBeNull();
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });
});
