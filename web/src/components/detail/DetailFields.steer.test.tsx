// DetailFields 的 M6 增面行为测试：steer 回执历史 section + 排队原因详情行，
// 且两个新键不再漏进「其他字段」兜底区（KNOWN_KEYS 已收编）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CardDetail } from "../../types";
import { DetailFields } from "./DetailFields";

afterEach(cleanup);

function detailWith(extra: Record<string, unknown>): CardDetail {
  return { id: "R-105", lane: "running", name: "修 flaky e2e", state: "working", ...extra };
}

describe("DetailFields steer notes", () => {
  it("steers[] 渲染成「Steer notes」section：状态 chip + 正文 + 排队/送达时间", () => {
    render(
      <DetailFields
        detail={detailWith({
          steers: [
            {
              ts: "2026-08-30T09:00:00Z",
              text: "先别动 schema，走兼容层",
              status: "delivered",
              delivered_at: "2026-08-30T09:02:11Z",
            },
            { ts: "2026-08-30T09:30:00Z", text: "加一条 v1 回归用例", status: "queued" },
            { ts: "2026-08-30T10:00:00Z", text: "没送出去的那条", status: "dropped" },
          ],
        })}
      />,
    );
    expect(screen.getByText("Steer notes")).toBeTruthy();
    expect(screen.getByText("Delivered")).toBeTruthy();
    expect(screen.getByText("Dropped")).toBeTruthy();
    expect(screen.getByText(/先别动 schema，走兼容层/)).toBeTruthy();
    expect(screen.getByText(/delivered at 2026-08-30T09:02:11Z/)).toBeTruthy();
    // 未知键兜底区不应出现 steers（有专属版式了）
    expect(screen.queryByText("Other fields")).toBeNull();
  });

  it("queued_reason 渲染成「Queued because」详情行（结构化 → 词表翻译）", () => {
    render(
      <DetailFields
        detail={detailWith({
          id: "R-106",
          state: "queued",
          queued_reason: { kind: "waiting_budget" },
        })}
      />,
    );
    expect(screen.getByText("Queued because")).toBeTruthy();
    expect(screen.getByText("waiting on budget")).toBeTruthy();
    expect(screen.queryByText("Other fields")).toBeNull();
  });

  it("两字段缺席时不渲染任何 steer 面（防御性降级）", () => {
    render(<DetailFields detail={detailWith({})} />);
    expect(screen.queryByText("Steer notes")).toBeNull();
    expect(screen.queryByText("Queued because")).toBeNull();
  });
});
