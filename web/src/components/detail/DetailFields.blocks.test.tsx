// 详情侧栏 = 卡片详情的唯一面（D34 / issue #217；CONTRACT §49 追记 / §54.1 第 2 项追记）——
// 原生 Cards.swift 详情槽的每块积木都要在 DetailFields 里、标签逐字（§66 探针按面精确匹配）：
//   提案：💰 预计费用: $N / 💰 成本未知（§40「展开详情永远说钱」，只在 needs_approval）· 💬 需求来自 · 📋 要做什么 · 怎样算办完：
//   待验收：交付了什么：（执行器原话 + 灰色摘要）· 验收清单——逐条对照：（§11 永远渲染，空给兜底句）· 日志： · 指令： · claude agents 列表名：
//   运行中：错误全文 + 复制 / 已复制 · 📋 要做什么（"[修改方向]" 行 is-rework）· 指令： · 会话 ID：
//   需输入：指令行用 §39 兜底句「在终端接管会话：」；排队卡无指令行（resumeCommand 同卡面）
//   钱：需要审批列走 💰 行；其余列 registry 并进来的 cost_estimate_usd 仍是一行「成本」（老侧栏就有，不能藏）；
//     cost_state（§40 诚实位）是专属版式读的键，不落「其他字段」
//   复制回执可听：每颗「复制」旁有 role=status 播报（卡面 CopyCommandLine 同法，a11y.test 第 3 条）
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CardDetail } from "../../types";
import { DetailFields } from "./DetailFields";

afterEach(cleanup);

/** 标签独占一个节点（前缀与值分开）：按元素自身整段文本精确找（尾部空格归一） */
const norm = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
const label = (expected: string) => (_: string, el: Element | null) =>
  norm(el?.textContent) === norm(expected) && !Array.from(el?.children ?? []).some((c) => norm(c.textContent) === norm(expected));

describe("DetailFields — proposal blocks", () => {
  const proposal: CardDetail = {
    id: "P-301", lane: "needs_approval", title: "leaderboard 一键导出评测报告", tier: "T2", cost_usd: 85, show_cost: true,
    summary: "在 dashboard 加导出按钮",
    sources: [{ who: "sam", channel: "slack", date: "2026-08-30", quote: "能不能一键导出", ref: "slack://C1/p1" }],
    plan: ["加按钮", "[修改方向] 别动 schema"],
    dod: ["点击后下载 CSV"],
  };

  it("💰 预计费用: $N（ASCII 冒号）· 💬 需求来自 · 📋 要做什么（[修改方向] 行橙）· 怎样算办完：", () => {
    render(<DetailFields detail={proposal} />);
    expect(screen.getByText("💰 Estimated cost: $85")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "💬 Requested by" })).toBeTruthy();
    expect(screen.getByText("能不能一键导出")).toBeTruthy();
    expect(screen.getByText(/sam · slack · 2026-08-30/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "📋 Plan" })).toBeTruthy();
    expect(screen.getByText("[修改方向] 别动 schema").className).toBe("is-rework");
    expect(screen.getByText("加按钮").className).toBe("");
    expect(screen.getByRole("heading", { name: "Definition of done:" })).toBeTruthy();
    expect(screen.getByText("点击后下载 CSV")).toBeTruthy();
    // 旧的 web 自造小节名不再出现（渲染器归一，文案逐字原生）
    expect(screen.queryByRole("heading", { name: /^(Plan|Sources|Delivered summary)$/ })).toBeNull();
  });

  it("成本未知（cost_state=unknown / 无数字）→ 💰 成本未知；cost_state 不落「其他字段」", () => {
    const { unmount } = render(<DetailFields detail={{ ...proposal, cost_usd: null, cost_state: "unknown" }} />);
    expect(screen.getByText("💰 Cost unknown")).toBeTruthy();
    expect(screen.queryByText("cost_state")).toBeNull();
    unmount();
    render(<DetailFields detail={{ ...proposal, cost_state: "estimated" }} />);
    expect(screen.getByText("💰 Estimated cost: $85")).toBeTruthy();
    expect(screen.queryByText("cost_state")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Other fields" })).toBeNull();
  });

  it("非提案列不说 💰，但 registry 并进来的 cost_estimate_usd 仍是一行「成本」（show_cost=false 才藏）", () => {
    const { unmount } = render(<DetailFields detail={{ id: "R-410", lane: "review", state: "review", cost_estimate_usd: 12 }} />);
    expect(screen.queryByText(/💰/)).toBeNull();
    expect(screen.getByText("Cost").tagName).toBe("DT");
    expect(screen.getByText("$12")).toBeTruthy();
    expect(screen.queryByText("cost_estimate_usd")).toBeNull(); // 有专属行，不进「其他字段」
    unmount();
    render(<DetailFields detail={{ id: "R-411", lane: "running", state: "working", cost_estimate_usd: 12, show_cost: false }} />);
    expect(screen.queryByText("Cost")).toBeNull();
    expect(screen.queryByText("$12")).toBeNull();
  });
});

describe("DetailFields — review blocks", () => {
  const review: CardDetail = {
    id: "R-410", lane: "review", name: "周报成稿", state: "review",
    summary: "审批时的摘要", delivered_summary: "已按 DoD 完成成稿",
    dod: ["覆盖三条来源"], copy_cmd: "cd '/tmp/w' && claude --resume abc", log: "/tmp/w/run.log", agent_name: "zai-R-410",
  };

  it("交付了什么：正文 + 灰色摘要；验收清单——逐条对照：；日志： / 指令： 各自一节点 + 复制；claude agents 列表名：", () => {
    render(<DetailFields detail={review} />);
    expect(screen.getByRole("heading", { name: "Delivered:" })).toBeTruthy();
    expect(screen.getByText("已按 DoD 完成成稿")).toBeTruthy();
    expect(screen.getByText("审批时的摘要").className).toContain("zai-detail-dim");
    expect(screen.getByRole("heading", { name: "Acceptance checklist:" })).toBeTruthy();
    expect(screen.getByText("覆盖三条来源").closest("ul")?.className).toBe("zai-detail-dod");
    expect(screen.queryByRole("heading", { name: "Definition of done:" })).toBeNull();
    expect(screen.getByText(label("Command: "))).toBeTruthy();
    expect(screen.getByText("cd '/tmp/w' && claude --resume abc").tagName).toBe("CODE");
    expect(screen.getByText(label("Log: "))).toBeTruthy();
    expect(screen.getByText("/tmp/w/run.log")).toBeTruthy();
    expect(screen.getByText(label("claude agents list name: "))).toBeTruthy();
    expect(screen.getByText("zai-R-410")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Copy" })).toHaveLength(2); // 指令 + 日志
  });

  it("§11：清单空时仍渲染小标题 + 兜底句", () => {
    render(<DetailFields detail={{ ...review, dod: [] }} />);
    expect(screen.getByRole("heading", { name: "Acceptance checklist:" })).toBeTruthy();
    expect(screen.getByText("No acceptance criteria defined — judge manually")).toBeTruthy();
  });
});

describe("DetailFields — running / blocked / queued blocks", () => {
  it("错误全文 + 复制 → 已复制；指令：来自 claude --resume <sid>；会话 ID：读 short_id", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<DetailFields detail={{ id: "R-610", lane: "running", name: "修 flaky e2e", state: "working",
      session_id: "sess-1", short_id: "sess", last_error: "Traceback: boom\n  line 2" }} />);
    expect(screen.getByRole("heading", { name: "Full error" })).toBeTruthy();
    expect(screen.getByText(/Traceback: boom/).tagName).toBe("PRE");
    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[0]); // 第一颗 = 错误全文旁的；第二颗 = 指令行
    expect(writeText).toHaveBeenCalledWith("Traceback: boom\n  line 2");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    // 复制成功有 role=status 播报（按钮文案变化 VoiceOver 不一定读）——每颗复制各一个区，只有点过的那个在说话
    expect(await screen.findByText("Copied to clipboard", { selector: "[role='status']" })).toBeTruthy();
    expect(document.querySelectorAll("[role='status']")).toHaveLength(2);
    expect(screen.getByText(label("Command: "))).toBeTruthy();
    expect(screen.getByText("claude --resume sess-1")).toBeTruthy();
    expect(screen.getByText(label("Session ID: "))).toBeTruthy();
    expect(screen.getByText("sess")).toBeTruthy();
    // last_error 不再另出一条「最近错误」callout（全文块就是它）
    expect(screen.queryByText("Last error")).toBeNull();
  });

  it("需输入列：指令行标签 = 在终端接管会话：（§39 兜底句）", () => {
    render(<DetailFields detail={{ id: "R-620", lane: "needs_input", name: "受阻", state: "blocked", copy_cmd: "claude attach 42" }} />);
    expect(screen.getByText(label("Take over in terminal: "))).toBeTruthy();
    expect(screen.queryByText(label("Command: "))).toBeNull();
    expect(screen.getByText("claude attach 42")).toBeTruthy();
  });

  it("排队卡：派发失败进错误全文；无指令行（排队卡没有会话）", () => {
    render(<DetailFields detail={{ id: "R-630", lane: "running", name: "排队", state: "queued", dispatch_error: "spawn failed", session_id: "x" }} />);
    expect(screen.getByText("spawn failed").tagName).toBe("PRE");
    expect(screen.queryByText(label("Command: "))).toBeNull();
  });
});
