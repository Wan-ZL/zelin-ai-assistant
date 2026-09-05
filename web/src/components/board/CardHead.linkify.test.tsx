// 卡面摘要里的 URL 可点（原生 Utils.swift linkified 的卡面落点，§54.1 追记）：
//   · 提案卡摘要（原生 Cards.swift:1073）与潜在任务卡摘要（:2028）里的 https?:// 成 <a target=_blank rel=noreferrer>；
//   · aria-label / 弹窗正文仍是纯字串（链接只在可见标题里）；
//   · AI 研究中占位（:945）与运行中行标题（:1561；原生 :1829 明确不 linkify）不变链接。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { ApprovalCard, DebtCard, TaskRow } from "../../types";
import { DebtCardItem } from "./DebtCardItem";
import { ProposalCard } from "./ProposalCard";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

const SUMMARY = "把 https://github.com/Wan-ZL/example-bench/pull/12 的评审意见并进 README";

function proposal(over: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    id: "P-001", title: "example-bench: README 评审意见", summary: SUMMARY, tier: "T1",
    show_cost: false, processing: false, sources: [], plan: [], dod: [], ...over,
  };
}

describe("ProposalCard headline linkified (Cards.swift:1073)", () => {
  it("URL in summary → anchor inside .card-title; aria-label stays the plain string", () => {
    render(<ProposalCard card={proposal()} />);
    const title = document.querySelector(".card-title");
    const a = title?.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://github.com/Wan-ZL/example-bench/pull/12");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toBe("noreferrer");
    expect(title?.textContent).toBe(SUMMARY);
    expect(screen.getByRole("article", { name: `Proposal · ${SUMMARY}` })).toBeTruthy();
  });

  it("no URL → title DOM unchanged (a single text node)", () => {
    render(<ProposalCard card={proposal({ summary: "大白话摘要一句" })} />);
    const title = document.querySelector(".card-title");
    expect(title?.innerHTML).toBe("大白话摘要一句");
  });

  it("AI 研究中占位 (:945) is not linkified", () => {
    render(<ProposalCard card={proposal({ processing: true })} />);
    expect(document.querySelector(".card-title a")).toBeNull();
    expect(screen.getByRole("article", { name: `AI researching · ${SUMMARY}` })).toBeTruthy();
  });
});

describe("DebtCardItem headline linkified (Cards.swift:2028)", () => {
  it("URL in summary → anchor; aria-label plain", () => {
    const item: DebtCard = { id: "P-113", title: "README 过时", summary: "参考 https://docs.example.dev/setup 重写安装一节" };
    render(<DebtCardItem item={item} />);
    const a = document.querySelector(".card-title a");
    expect(a?.getAttribute("href")).toBe("https://docs.example.dev/setup");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(screen.getByRole("article", { name: "Backlog · 参考 https://docs.example.dev/setup 重写安装一节" })).toBeTruthy();
  });
});

describe("name-first faces stay plain (Cards.swift:1829)", () => {
  it("RunningCard title with a URL renders no anchor", () => {
    const row: TaskRow = { id: "R-105", name: "看 https://ci.example.dev/run/9 的失败", state: "working" };
    render(<RunningCard row={row} />);
    expect(document.querySelector(".card-title a")).toBeNull();
    expect(document.querySelector(".card-title")?.textContent).toBe("看 https://ci.example.dev/run/9 的失败");
  });
});
