// §37 展示名在详情面（D34 唯一详情面）：抬头是冻结 title（DetailDrawer h2），卡面是 lane 决定的 headline——
// display_title 两处都不是时才给一行「显示名 / Display name」，否则不重复；活标题四键
// （display_title / user_titled / former_titles / notes_text）不再漏进「其他字段」兜底区（notes_text 是 notes 的搜索副本）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CardDetail } from "../../types";
import { DetailFields, faceHeadline } from "./DetailFields";

afterEach(cleanup);

const proposal: CardDetail = {
  id: "P-301", lane: "needs_approval", title: "example-bench: leaderboard 一键导出评测报告",
  summary: "大白话摘要一句", display_title: "短名字", tier: "T1", show_cost: false,
  former_titles: ["旧名"], notes_text: "radar: 备注副本", user_titled: false,
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
  it("提案：display_title ≠ summary（卡面）≠ title（抬头）→ 一行「Display name」", () => {
    render(<DetailFields detail={proposal} />);
    expect(screen.getByText("Display name").tagName).toBe("DT");
    expect(screen.getByText("短名字").tagName).toBe("DD");
  });

  it("钦定名已在卡面（user_titled）→ 不重复给行；display_title 等于冻结 title 也不给", () => {
    const { unmount } = render(<DetailFields detail={{ ...proposal, user_titled: true }} />);
    expect(screen.queryByText("Display name")).toBeNull();
    unmount();
    render(<DetailFields detail={{ ...proposal, display_title: proposal.title }} />);
    expect(screen.queryByText("Display name")).toBeNull();
  });

  it("名字优先面上 display_title 就是卡面标题 → 不重复", () => {
    render(<DetailFields detail={{ id: "R-1", lane: "running", name: "冻结名", summary: "摘要", display_title: "显示名", state: "working" }} />);
    expect(screen.queryByText("Display name")).toBeNull();
  });

  it("活标题四键不落「其他字段」（notes_text 是 notes 的搜索副本，不渲染两遍）", () => {
    render(<DetailFields detail={proposal} />);
    expect(screen.queryByRole("heading", { name: "Other fields" })).toBeNull();
    expect(screen.queryByText("notes_text")).toBeNull();
    expect(screen.queryByText("former_titles")).toBeNull();
    expect(screen.queryByText("user_titled")).toBeNull();
    expect(screen.queryByText(/radar: 备注副本/)).toBeNull();
  });
});
