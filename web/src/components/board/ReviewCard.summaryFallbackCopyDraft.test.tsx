// 待验收卡面的两处原生 parity（批次 review-running-card-fixes；§64.5 追记 / §54.1）：
//   (b) 一句话回落（原生 Cards.swift:1832-1854 ReviewRow 永远给一句交付说明；gap board-cards-review-face-delivery）：
//       §64 AI 摘要优先 → 缺席回落 delivered_summary → 再回落审批时 summary → 都没有才不渲染；
//       回落句不带 is-ai 细条；空串按缺席算（原生 `!ds.isEmpty`）；执行器原话全文仍住详情侧栏，卡面只有单行截断。
//   (c) 复制成稿走 detail/copyText（Clipboard API → execCommand 兜底，原生 :1803-1815 NSPasteboard；
//       gap board-cards-copy-draft-no-fallback）：成功 → 「已复制 ✓」1.5 s + role=status 播报；两条路都失败 →
//       role=alert 短注（点名到详情里手动复制）、按钮文案不变；此前直接 navigator.clipboard.writeText 无 catch，
//       非 secure context 下静默无事发生。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { ReviewCard as ReviewRow } from "../../types";
import { COPY_FAILED_NOTE_MS, ReviewCard } from "./ReviewCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock("../detail/copyText", () => ({ copyText: vi.fn() }));
import { copyText } from "../detail/copyText";

function review(extra: Partial<ReviewRow> = {}): ReviewRow {
  return {
    id: "R-501",
    name: "修登录页报错",
    dod: ["报错消失"],
    delivery_mode: "chat",
    summary: "审批时的摘要：修 LoginForm 的空指针",
    delivered_summary: "## Done\n- fixed the null deref in LoginForm",
    ...extra,
  };
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(copyText).mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("review face delivery sentence fallback (§64.5 追记)", () => {
  it("AI 摘要优先（带 is-ai 细条），执行器原话不在卡面", () => {
    render(<ReviewCard card={review({ assessment: { summary: "白话一句", verdict: "建议验收" } })} />);
    const line = document.querySelector(".card-summary-line") as HTMLElement;
    expect(line.textContent).toBe("白话一句");
    expect(line.className).toContain("is-ai");
    expect(screen.queryByText(/fixed the null deref/)).toBeNull();
  });

  it("没有 assessment → 回落 delivered_summary（单行、hover 全文、无 is-ai）", () => {
    render(<ReviewCard card={review()} />);
    const line = document.querySelector(".card-summary-line") as HTMLElement;
    expect(line.textContent).toBe("## Done\n- fixed the null deref in LoginForm");
    expect(line.getAttribute("title")).toBe("## Done\n- fixed the null deref in LoginForm");
    expect(line.className).not.toContain("is-ai");
    // 「交付了什么：」小标题与 ☐ 清单仍只在详情侧栏
    expect(screen.queryByText("Delivered:")).toBeNull();
    expect(screen.queryByText(/报错消失/)).toBeNull();
  });

  it("assessment 有但没 summary（只有评语）→ 章在、句子回落 delivered_summary", () => {
    render(<ReviewCard card={review({ assessment: { verdict: "需要拍板", summary: null } })} />);
    expect(screen.getByRole("button", { name: "AI verdict: Needs your call" })).toBeTruthy();
    const line = document.querySelector(".card-summary-line") as HTMLElement;
    expect(line.textContent).toContain("fixed the null deref");
    expect(line.className).not.toContain("is-ai");
  });

  it("delivered_summary 缺席 / 空串 / 纯空白 → 回落审批时 summary（原生 else-if 分支）", () => {
    const { rerender } = render(<ReviewCard card={review({ delivered_summary: undefined })} />);
    expect((document.querySelector(".card-summary-line") as HTMLElement).textContent).toBe("审批时的摘要：修 LoginForm 的空指针");
    rerender(<ReviewCard card={review({ delivered_summary: "" })} />);
    expect((document.querySelector(".card-summary-line") as HTMLElement).textContent).toBe("审批时的摘要：修 LoginForm 的空指针");
    // 纯空白不许吞掉 summary：`||` 会把 "   " 当真值留下，AssessmentSummaryLine 再 trim 成空 → 卡面一句都没有
    rerender(<ReviewCard card={review({ delivered_summary: "   \n\t" })} />);
    expect((document.querySelector(".card-summary-line") as HTMLElement).textContent).toBe("审批时的摘要：修 LoginForm 的空指针");
  });

  it("三者皆无 → 不渲染句子", () => {
    render(<ReviewCard card={review({ delivered_summary: undefined, summary: null })} />);
    expect(document.querySelector(".card-summary-line")).toBeNull();
  });
});

describe("复制成稿 goes through copyText", () => {
  const draft = "**Weekly Report**\n- line 1";

  it("成功 → 按钮变「已复制 ✓」1.5 s 后复原，role=status 播报", async () => {
    vi.useFakeTimers();
    vi.mocked(copyText).mockResolvedValue(true);
    render(<ReviewCard card={review({ final_draft: draft })} />);
    const button = screen.getByRole("button", { name: "Copy final draft" });
    await act(async () => {
      fireEvent.click(button);
      await Promise.resolve();
    });
    expect(copyText).toHaveBeenCalledWith(draft);
    expect(button.textContent).toBe("Copied ✓");
    expect(screen.getByRole("status").textContent).toBe("Copied to clipboard");
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    expect(button.textContent).toBe("Copy final draft");
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("两条路都失败（copyText → false）→ 短注 role=alert，按钮文案不变，注在 COPY_FAILED_NOTE_MS 后消失", async () => {
    vi.useFakeTimers();
    vi.mocked(copyText).mockResolvedValue(false);
    render(<ReviewCard card={review({ final_draft: draft })} />);
    const button = screen.getByRole("button", { name: "Copy final draft" });
    await act(async () => {
      fireEvent.click(button);
      await Promise.resolve();
    });
    expect(button.textContent).toBe("Copy final draft");
    const note = screen.getByRole("alert");
    expect(note.textContent).toMatch(/^Copy failed/);
    expect(note.textContent).toContain("Details ▸");
    await act(async () => {
      vi.advanceTimersByTime(COPY_FAILED_NOTE_MS);
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("失败后再点成功 → 注撤掉、回执照给", async () => {
    vi.useFakeTimers();
    vi.mocked(copyText).mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    render(<ReviewCard card={review({ final_draft: draft })} />);
    const button = screen.getByRole("button", { name: "Copy final draft" });
    await act(async () => {
      fireEvent.click(button);
      await Promise.resolve();
    });
    expect(screen.getByRole("alert")).toBeTruthy();
    await act(async () => {
      fireEvent.click(button);
      await Promise.resolve();
    });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(button.textContent).toBe("Copied ✓");
  });

  it("没有 final_draft → 没有按钮、没有播报节点", () => {
    render(<ReviewCard card={review({ final_draft: null })} />);
    expect(screen.queryByRole("button", { name: "Copy final draft" })).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
