// §64（issue #128）AI 评语章 + 一句话摘要 —— 只是建议的判例：
//   1) 待验收卡：有评语 → 三态章可见（点一下展开理由，再点收起）；没有 → 章零 DOM，一句话回落交付说明
//      （§64.5 2026-09-05 追记；细节判例 ReviewCard.summaryFallbackCopyDraft.test.tsx）；
//   2) 词表镜像 act/lib/card_summary.py VERDICTS（逐字），未知值按原文中性渲染；
//   3) 摘要一句在卡面；执行器原话不在卡面（只住详情侧栏，DetailFields.blocks.test.tsx 钉「交付了什么：」）；
//   4) 点章绝不触发任何 inbox 动作（验收/打回只有按钮能按）；
//   5) 阶段性完成卡：AI 摘要优先，缺席回落 delivered_summary。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { ReviewCard as ReviewRow, TaskRow } from "../../types";
import { DoneCard } from "./DoneCard";
import { ReviewCard } from "./ReviewCard";
import { VERDICTS } from "./VerdictChip";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { postAction } from "../../api";

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(postAction).mockClear();
});
afterEach(cleanup);

function review(extra: Partial<ReviewRow> = {}): ReviewRow {
  return {
    id: "R-501",
    name: "修登录页报错",
    dod: ["报错消失", "有测试"],
    delivery_mode: "repo",
    delivered_summary: "## Done\n- fixed the null deref in LoginForm\n| file | lines |",
    ...extra,
  };
}

describe("VerdictChip on review cards", () => {
  it("词表逐字镜像 server 常量", () => {
    expect(Object.values(VERDICTS)).toEqual(["建议验收", "需继续做", "需要拍板"]);
  });

  it("no assessment → 没有章；一句话回落 delivered_summary（不带 is-ai，§64.5 2026-09-05 追记：原生 ReviewRow 永远有交付一句）", () => {
    render(<ReviewCard card={review()} />);
    expect(screen.queryByRole("button", { name: /AI verdict/ })).toBeNull();
    const line = document.querySelector(".card-summary-line") as HTMLElement;
    expect(line.className).not.toContain("is-ai");
    expect(line.textContent).toContain("fixed the null deref");
  });

  it("有评语 → 章可见；点一下展开理由，再点收起；执行器原话不在卡面", () => {
    render(
      <ReviewCard
        card={review({
          assessment: { summary: "把登录页的报错修好了，等你验收", verdict: "需继续做", verdict_reason: "清单第 2 条「有测试」没看到对应改动", at: 1 },
        })}
      />,
    );
    // 卡面：摘要一句 + 章
    expect(screen.getByText("把登录页的报错修好了，等你验收")).toBeTruthy();
    const chip = screen.getByRole("button", { name: "AI verdict: Needs more work" });
    expect(chip.className).toContain("chip-warning");
    expect(chip.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/没看到对应改动/)).toBeNull();
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("note").textContent).toContain("清单第 2 条「有测试」没看到对应改动");
    fireEvent.click(chip);
    expect(screen.queryByRole("note")).toBeNull();
    // 执行器原话不在卡面（D34：详情只有侧栏一面，卡上不就地展开）
    expect(screen.queryByText(/fixed the null deref/)).toBeNull();
  });

  it("三态色相：建议验收 绿 / 需继续做 橙 / 需要拍板 紫；未知值按原文中性", () => {
    const { rerender } = render(<ReviewCard card={review({ assessment: { verdict: "建议验收" } })} />);
    expect(screen.getByRole("button", { name: "AI verdict: Looks done" }).className).toContain("chip-success");
    rerender(<ReviewCard card={review({ assessment: { verdict: "需要拍板" } })} />);
    expect(screen.getByRole("button", { name: "AI verdict: Needs your call" }).className).toContain("chip-purple");
    rerender(<ReviewCard card={review({ assessment: { verdict: "somethingelse" } })} />);
    const raw = screen.getByRole("button", { name: "AI verdict: somethingelse" });
    expect(raw.className).not.toMatch(/chip-(success|warning|purple)/);
  });

  it("点章不发任何动作；验收/打回按钮照常在", () => {
    render(<ReviewCard card={review({ assessment: { verdict: "建议验收", verdict_reason: "全满足" } })} />);
    fireEvent.click(screen.getByRole("button", { name: "AI verdict: Looks done" }));
    expect(postAction).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send Back" })).toBeTruthy();
  });

  it("只有摘要没有评语 → 摘要行在、章不在", () => {
    render(<ReviewCard card={review({ assessment: { summary: "只做了一半", verdict: null } })} />);
    expect(screen.getByText("只做了一半")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /AI verdict/ })).toBeNull();
  });
});

describe("AssessmentSummaryLine on done cards", () => {
  function done(extra: Partial<TaskRow> = {}): TaskRow {
    return { id: "R-777", name: "已完成", state: "done", delivered_summary: "executor closing words", ...extra };
  }

  it("AI 摘要优先，缺席回落 delivered_summary；两者皆无 → 不渲染", () => {
    const { rerender } = render(<DoneCard row={done({ assessment: { summary: "白话一句" } })} />);
    const line = document.querySelector(".card-summary-line") as HTMLElement;
    expect(line.textContent).toBe("白话一句");
    expect(line.className).toContain("is-ai");
    rerender(<DoneCard row={done()} />);
    const fallback = document.querySelector(".card-summary-line") as HTMLElement;
    expect(fallback.textContent).toBe("executor closing words");
    expect(fallback.className).not.toContain("is-ai");
    rerender(<DoneCard row={done({ delivered_summary: undefined })} />);
    expect(document.querySelector(".card-summary-line")).toBeNull();
  });
});
