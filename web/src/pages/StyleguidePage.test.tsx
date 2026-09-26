// 活体样式指南：路由识别 + 参照表行数（≥15）+ 真组件挂载三件事。
// 页面渲染不 mock api——fixture 卡不发请求，store 初始快照（board=null）即可挂载。
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { buildAppUrl, readPage } from "../route";
import { resetStoreForTests } from "../store";
import { StyleguidePage } from "./StyleguidePage";

beforeEach(() => {
  window.history.replaceState(null, "", "/?page=styleguide");
  resetStoreForTests();
});

afterEach(cleanup);

describe("styleguide route", () => {
  it("?page=styleguide 被识别；未知值仍回落 board", () => {
    expect(readPage("?page=styleguide")).toBe("styleguide");
    expect(readPage("?page=bogus")).toBe("board");
    // buildAppUrl 往返：styleguide 非缺省页，落 query；回 board 时清掉
    const url = buildAppUrl("http://127.0.0.1:47820/", "styleguide", null);
    expect(url.searchParams.get("page")).toBe("styleguide");
    expect(readPage(url.search)).toBe("styleguide");
    expect(buildAppUrl(url.href, "board", null).searchParams.get("page")).toBeNull();
  });
});

describe("StyleguidePage", () => {
  it("渲染五节 + 参照表 ≥ 15 行（每行一个语义色）", () => {
    const { container } = render(<StyleguidePage />);
    expect(screen.getByText("Living styleguide")).toBeTruthy();
    const rows = container.querySelectorAll(".sg-ref-table tbody .sg-ref-row");
    expect(rows.length).toBeGreaterThanOrEqual(15);
    // ⚠️ 行存在（老 vs 新可见差异必须在页内被标出，如 批准 绿→teal）
    expect(container.querySelectorAll(".sg-ref-row.is-flagged").length).toBeGreaterThan(0);
  });

  it("真组件挂载：每类卡的动词按钮都在（Buttons + Cards 两节共用原件）", () => {
    render(<StyleguidePage />);
    // DebtCardItem（Buttons 节 T1 + Cards 节 T1/T2 = 3 张，processing 占位无按钮）——§78 起
    // 机器卡的动词行只有这一套，那颗绿键叫「促成运行」；T2 那张没看过明细，给的是提示句不是键
    expect(screen.getAllByRole("button", { name: "Run it" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("T2: expand details first").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Reject" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: "Delete" }).length).toBeGreaterThanOrEqual(2);
    // RunningCard blocked / working、ReviewCard、DoneCard、DebtCardItem
    // （#119：answer_input 已退役——blocked 卡只剩「停止」(+ 让 AI 修) 出口；
    // 「回答…」只回到出错的执行卡上，走 comment/steer 通道——原生 parity 项 5）
    const answers = screen.queryAllByRole("button", { name: "Answer…" });
    expect(answers.length).toBeGreaterThanOrEqual(1);
    expect(answers.every((b) => !b.closest(".task-card")?.classList.contains("is-blocked"))).toBe(true);
    expect(screen.getAllByRole("button", { name: "Fix with AI" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Stop" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: "Accept" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Send Back" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Copy final draft" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Back to review" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Done for good" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "Research & propose" }).length).toBeGreaterThanOrEqual(1);
    // LaneComposer 原件（capture / run）
    expect(screen.getByRole("button", { name: "Capture" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run" })).toBeTruthy();
  });

  it("owner 验收单色相钉死：动词按钮与 chip 的 hue class 一比一", () => {
    const { container } = render(<StyleguidePage />);
    const hasClass = (name: string, cls: string) =>
      screen.getAllByRole("button", { name }).some((b) => b.className.includes(cls));
    // 按钮：绿促成运行 / 红拒绝 / 蓝修改 / 绿验收 / 橙打回 / 青复制成稿；
    // 参照表里老 app 的绿「批准」样本仍是那颗绿（§78 改的是卡面标签，不是色相）
    expect(hasClass("Run it", "btn-success")).toBe(true);
    expect(hasClass("Approve", "btn-success")).toBe(true);
    expect(hasClass("Reject", "btn-danger")).toBe(true);
    expect(hasClass("Comment", "btn-info")).toBe(true);
    // §78/D80：「暂缓 / Later」这颗键在整页（含参照表样本列）一个都不许再长出来
    expect(screen.queryAllByRole("button", { name: "Later" })).toEqual([]);
    expect(screen.queryAllByRole("button", { name: "暂缓" })).toEqual([]);
    expect(hasClass("Accept", "btn-success")).toBe(true);
    expect(hasClass("Send Back", "btn-warning")).toBe(true);
    expect(hasClass("Copy final draft", "btn-accent")).toBe(true);
    expect(hasClass("Stop", "btn-warning")).toBe(true);
    expect(hasClass("Back to review", "btn-accent")).toBe(true);
    // chips：粉紫 tier 章 + 紫交付（chip-purple）/ 黄等待（chip-notice）/
    // lineage 安静档（chip-quiet）/ 紧急截止红字描边档（chip-outline）/ 绿已交付（chip-success）
    expect(container.querySelectorAll(".chip-purple").length).toBeGreaterThanOrEqual(4);
    expect(container.querySelectorAll(".chip-notice").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelectorAll(".chip-quiet").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelectorAll(".chip-outline").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelectorAll(".chip-success").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelectorAll(".zai-chip--merged").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelectorAll(".zai-chip--improves").length).toBeGreaterThanOrEqual(1);
  });

  it("lane 状态卡齐：processing 占位 sheen、queued 灰卡、steer 三态回执可见", () => {
    render(<StyleguidePage />);
    expect(screen.getAllByText("AI is researching; the plan and DoD land on this card when it finishes").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Queued").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Steer queued ×1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Steer delivered ×1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Steer dropped ×1").length).toBeGreaterThanOrEqual(1);
  });
});
