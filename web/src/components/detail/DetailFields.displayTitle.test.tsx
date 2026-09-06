// §37 展示名在详情面（D34 唯一详情面）：抬头是冻结 title（DetailDrawer h2 = title ‖ name），display_title 与抬头不同
// 就给一行「显示名 / Display name」——只跟抬头去重、不跟卡面去重（侧栏是 modal：深链 / 收起的书立条时卡面不在眼前，
// 用户钦定的名字不能在唯一详情面上消失）；活标题四键（display_title / user_titled / former_titles / notes_text）
// 不再漏进「其他字段」兜底区；notes_text 是 notes 的搜索副本：registry notes 在就不渲染两遍，缺席时它是折叠信息唯一来源（§38）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CardDetail } from "../../types";
import { DetailFields, faceHeadline } from "./DetailFields";

afterEach(cleanup);

const proposal: CardDetail = {
  id: "P-301", lane: "needs_approval", title: "example-bench: leaderboard 一键导出评测报告",
  summary: "大白话摘要一句", display_title: "短名字", tier: "T1", show_cost: false,
  former_titles: ["旧名"], notes_text: "[radar] 备注副本 [@2026-09-01T10:00]", user_titled: false,
};

describe("faceHeadline — lane decides the chain", () => {
  it("摘要优先面（needs_approval / debt / trash / archived / 无 lane）= cardHeadline", () => {
    expect(faceHeadline(proposal)).toBe("大白话摘要一句");
    expect(faceHeadline({ ...proposal, lane: "debt" })).toBe("大白话摘要一句");
    expect(faceHeadline({ ...proposal, lane: null })).toBe("大白话摘要一句");
    expect(faceHeadline({ ...proposal, user_titled: true })).toBe("短名字");
  });

  it("名字优先面（running / needs_input / review / completed）= display_title > name > title", () => {
    const row: CardDetail = { id: "R-1", lane: "running", name: "冻结名", summary: "摘要", display_title: "显示名" };
    expect(faceHeadline(row)).toBe("显示名");
    expect(faceHeadline({ ...row, display_title: undefined })).toBe("冻结名");
    expect(faceHeadline({ ...row, lane: "review", display_title: "" })).toBe("冻结名");
  });
});

describe("DetailFields — 显示名 row", () => {
  it("提案：display_title ≠ 抬头 title → 一行「Display name」（LLM 短名让位给 summary 后仍有家）", () => {
    render(<DetailFields detail={proposal} />);
    expect(screen.getByText("Display name").tagName).toBe("DT");
    expect(screen.getByText("短名字").tagName).toBe("DD");
  });

  it("钦定名（user_titled）也给行——卡面未必在眼前，抽屉里必须能看到用户钉的名字", () => {
    render(<DetailFields detail={{ ...proposal, user_titled: true }} />);
    expect(screen.getByText("Display name").tagName).toBe("DT");
    expect(screen.getByText("短名字").tagName).toBe("DD");
  });

  it("display_title 等于抬头（冻结 title）→ 不重复", () => {
    render(<DetailFields detail={{ ...proposal, display_title: proposal.title }} />);
    expect(screen.queryByText("Display name")).toBeNull();
  });

  it("名字优先面：抬头是 name（无 title）——display_title ≠ name 给行，等于 name 不给", () => {
    const row: CardDetail = { id: "R-1", lane: "running", name: "冻结名", summary: "摘要", display_title: "显示名", state: "working" };
    const { unmount } = render(<DetailFields detail={row} />);
    expect(screen.getByText("Display name").tagName).toBe("DT");
    expect(screen.getByText("显示名").tagName).toBe("DD");
    unmount();
    render(<DetailFields detail={{ ...row, display_title: "冻结名" }} />);
    expect(screen.queryByText("Display name")).toBeNull();
  });
});

describe("DetailFields — 活标题四键不落「其他字段」", () => {
  it("四个键名都不出现在兜底区", () => {
    render(<DetailFields detail={{ ...proposal, notes: "[radar] 备注副本 [@2026-09-01T10:00]" }} />);
    expect(screen.queryByRole("heading", { name: "Other fields" })).toBeNull();
    expect(screen.queryByText("notes_text")).toBeNull();
    expect(screen.queryByText("former_titles")).toBeNull();
    expect(screen.queryByText("user_titled")).toBeNull();
    expect(screen.queryByText("display_title")).toBeNull();
  });

  it("registry notes 在：折叠信息只渲染一遍（notes_text 不重复）", () => {
    render(<DetailFields detail={{ ...proposal, notes: "[radar] 备注副本 [@2026-09-01T10:00]" }} />);
    expect(screen.getByRole("heading", { name: "📎 Folded-in updates" })).toBeTruthy();
    expect(screen.getAllByText(/备注副本/)).toHaveLength(1);
  });

  it("registry notes 缺席（store2 不在 / 只有投影）：notes_text 兜底成 📎 折叠信息，拆成新卡句柄还在", () => {
    render(<DetailFields detail={proposal} />);
    expect(screen.getByRole("heading", { name: "📎 Folded-in updates" })).toBeTruthy();
    expect(screen.getAllByText(/备注副本/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Split into card" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Other fields" })).toBeNull();
  });
});
