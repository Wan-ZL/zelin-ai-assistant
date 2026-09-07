// 待验收卡面的 ☐ 验收清单（D43；CONTRACT §54.1 第 2 项 2026-09-06 追记；gap board-cards-review-face-delivery 的清单半边）：
//   原生 Cards.swift:1858-1876 在 ReviewRow 收起态永远渲染「验收清单——逐条对照：」+ ☐ 条（空给兜底句），D34 后只剩侧栏。自此：
//   1) 卡面在一句交付说明之后、动作行之前永远渲染清单块——有条目 ☐ 前 3 条 + 「+N」，空给「该任务未定义验收标准，请自行判断」；
//   2) §64 评语 = 建议验收 → ☑（AI 判断，title 点明；验收 / 打回按钮不多不少）；需继续做 / 需要拍板 / 没评 → ☐；
//   3) 执行器原话（「交付了什么：」全文）仍只在侧栏（§64.5）；「展开详情 ▸」后卡面一个字不多；
//   4) 侧栏 DetailFields 的清单全文不变（全部条目、无 ☐ 前缀，D34 渲染器不动）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getState, resetStoreForTests } from "../../store";
import type { CardDetail, ReviewCard as ReviewRow } from "../../types";
import { DetailFields } from "../detail/DetailFields";
import { ReviewCard } from "./ReviewCard";

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

const FOUR = ["同 config 重复 run 命中缓存", "缓存失效逻辑有单测", "CI 全绿", "文档更新"];

function review(extra: Partial<ReviewRow> = {}): ReviewRow {
  return {
    id: "P-109",
    name: "评测缓存",
    dod: FOUR,
    delivery_mode: "repo",
    delivered_summary: "## Done\n- 加了 cache layer",
    ...extra,
  };
}

describe("review face: ☐ 验收清单 always on the face (D43)", () => {
  it("一句交付说明之后、动作行之前：标题 + ☐ 前 3 条 + 「+1」；执行器原话全文不在卡面", () => {
    const { container } = render(<ReviewCard card={review()} />);
    const block = container.querySelector(".card-dod.is-checklist")!;
    expect(block).not.toBeNull();
    expect(block.querySelector(".card-dod-heading")!.textContent).toBe("Acceptance checklist:");
    const items = Array.from(block.querySelectorAll(".card-dod-item")).map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(items).toEqual([`☐ ${FOUR[0]}`, `☐ ${FOUR[1]}`, `☐ ${FOUR[2]}`]);
    expect(block.querySelector(".card-dod-more")!.textContent).toContain("+1");
    expect(screen.queryByText(/文档更新/)).toBeNull();
    expect(container.querySelector(".card-summary-line")!.nextElementSibling).toBe(block);
    expect(block.nextElementSibling!.className).toContain("card-actions");
    expect(screen.queryByText("Delivered:")).toBeNull();
    // 执行器原话只以单行回落句（.card-summary-line，§64.5 追记）出现，不是「交付了什么」块
    expect(screen.getByText(/加了 cache layer/).className).toBe("card-summary-line");
  });

  it("空清单 → 兜底句仍在卡面（原生 always rendered）", () => {
    const { container, rerender } = render(<ReviewCard card={review({ dod: [] })} />);
    expect(container.querySelector(".card-dod.is-checklist")).not.toBeNull();
    expect(screen.getByText("No acceptance criteria defined — judge manually")).toBeTruthy();
    rerender(<ReviewCard card={review({ dod: undefined as unknown as string[] })} />);
    expect(screen.getByText("No acceptance criteria defined — judge manually")).toBeTruthy();
  });

  it("§64 建议验收 → ☑（is-ai-checked，☑ 记号自己的 title 点明 AI 判断）；验收 / 打回按钮照常、不多一颗", () => {
    const { container } = render(<ReviewCard card={review({ assessment: { summary: "缓存做好了", verdict: "建议验收", verdict_reason: "三条都有对应改动" } })} />);
    const block = container.querySelector(".card-dod.is-checklist")!;
    expect(block.className).toContain("is-ai-checked");
    const marks = Array.from(block.querySelectorAll(".card-dod-mark"));
    expect(marks.map((m) => m.textContent)).toEqual(["☑", "☑", "☑"]);
    // 说明挂在记号自己（与标题）上——<ul> 的 title 会被每条 <li title=全文> 盖住，owner 悬停永远看不到
    for (const mark of marks) expect(mark.getAttribute("title")).toContain("AI verdict");
    expect(block.querySelector(".card-dod-heading")!.getAttribute("title")).toContain("AI verdict");
    expect(block.querySelector(".card-dod-list")!.getAttribute("title")).toBeNull();
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send Back" })).toBeTruthy();
    expect(block.querySelectorAll("button")).toHaveLength(0);
  });

  it("需继续做 / 需要拍板 / 没评 → ☐", () => {
    const { container, rerender } = render(<ReviewCard card={review({ assessment: { verdict: "需继续做", verdict_reason: "缺第 2 条" } })} />);
    expect(container.querySelector(".card-dod")!.className).not.toContain("is-ai-checked");
    expect(container.querySelector(".card-dod-mark")!.textContent).toBe("☐");
    rerender(<ReviewCard card={review({ assessment: { verdict: "需要拍板" } })} />);
    expect(container.querySelector(".card-dod-mark")!.textContent).toBe("☐");
    rerender(<ReviewCard card={review()} />);
    expect(container.querySelector(".card-dod-mark")!.textContent).toBe("☐");
  });

  it("点「展开详情 ▸」= 开侧栏；卡面清单仍是 3 条 + +1，不就地撑开", () => {
    const { container } = render(<ReviewCard card={review()} />);
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    expect(getState().selectedCardId).toBe("P-109");
    expect(container.querySelectorAll(".card-dod-item")).toHaveLength(3);
    expect(screen.queryByText(/文档更新/)).toBeNull();
  });

  it("侧栏 DetailFields：清单全文 4 条不变、无 ☐ 前缀、兜底句同词", () => {
    const detail = { ...review(), lane: "review" } as unknown as CardDetail;
    const { container, rerender } = render(<DetailFields detail={detail} />);
    const section = screen.getByText("Acceptance checklist:").closest(".zai-detail-section")!;
    expect(Array.from(section.querySelectorAll("li")).map((li) => li.textContent)).toEqual(FOUR);
    expect(container.querySelector(".card-dod")).toBeNull();
    rerender(<DetailFields detail={{ ...detail, dod: [] } as unknown as CardDetail} />);
    expect(screen.getByText("No acceptance criteria defined — judge manually").className).toContain("zai-detail-dim");
  });
});
