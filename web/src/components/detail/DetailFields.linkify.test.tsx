// 详情侧栏（D34 唯一详情面）里的 URL 可点（原生 Utils.swift linkified 的详情槽落点，§54.1 追记）：
//   · 摘要（原生提案面 Cards.swift:1073 / 潜在任务面 :2028）、💬 需求来自 引文（:508 / :1311「Slack quotes often
//     carry links」）、📋 要做什么 步骤（:529 / :1329）里的 https?:// 成 <a target=_blank rel=noreferrer>；
//   · 「[修改方向]」步骤仍带 is-rework；引文的 「」 不算进链接；URL 后紧跟的全角 ， 不算进链接（中文引文常态）；
//   · 交付了什么 正文（:1829 原生不 linkify）与 怎样算办完（DodListView）不变链接；
//   · 待验收行的灰色审批时摘要原生是纯 Text（:1843-1850，手势冲突理由）——web 侧栏无该手势，有意 linkify（扩展，非照抄）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CardDetail } from "../../types";
import { DetailFields } from "./DetailFields";

afterEach(cleanup);

const proposal: CardDetail = {
  id: "P-301", lane: "needs_approval", title: "leaderboard 一键导出评测报告", tier: "T1", show_cost: false,
  summary: "按 https://github.com/Wan-ZL/example-bench/issues/7 的讨论加导出按钮",
  sources: [{ who: "sam", channel: "slack", date: "2026-08-30", quote: "能不能一键导出，参考「https://docs.example.dev/export」" }],
  plan: ["读 https://docs.example.dev/export 的字段表", "[修改方向] 别动 schema，见 https://x.dev/schema。"],
  dod: ["点击后下载 CSV，格式照 https://x.dev/csv-spec"],
};

const hrefs = (root: ParentNode) => Array.from(root.querySelectorAll("a")).map((a) => a.getAttribute("href"));

describe("DetailFields — URLs are clickable in summary / quotes / plan", () => {
  it("summary → anchor（target=_blank rel=noreferrer），text intact", () => {
    render(<DetailFields detail={proposal} />);
    const summary = document.querySelector(".zai-detail-summary");
    const a = summary?.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://github.com/Wan-ZL/example-bench/issues/7");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toBe("noreferrer");
    expect(summary?.textContent).toBe(proposal.summary);
  });

  it("💬 需求来自 quote → anchor; 「」 stay outside the URL", () => {
    render(<DetailFields detail={proposal} />);
    const quote = document.querySelector(".zai-detail-source-quote");
    expect(hrefs(quote as ParentNode)).toEqual(["https://docs.example.dev/export"]);
    expect(quote?.textContent).toBe("能不能一键导出，参考「https://docs.example.dev/export」");
  });

  it("💬 quote with `URL，更多字` (no space): the anchor text stops at the URL", () => {
    const quoteText = "看 https://x.dev/a，更多字在后面。再看https://y.dev/b、谢谢";
    render(<DetailFields detail={{ ...proposal, sources: [{ who: "sam", channel: "slack", date: "2026-08-30", quote: quoteText }] }} />);
    const quote = document.querySelector(".zai-detail-source-quote");
    const anchors = Array.from(quote?.querySelectorAll("a") ?? []);
    expect(anchors.map((a) => a.getAttribute("href"))).toEqual(["https://x.dev/a", "https://y.dev/b"]);
    expect(anchors.map((a) => a.textContent)).toEqual(["https://x.dev/a", "https://y.dev/b"]);
    expect(quote?.textContent).toBe(quoteText);
  });

  it("📋 要做什么 steps → anchors; [修改方向] keeps is-rework; trailing 。 outside", () => {
    render(<DetailFields detail={proposal} />);
    const items = Array.from(document.querySelectorAll(".zai-detail-section ol li"));
    const plan = items.filter((li) => li.textContent?.includes("docs.example.dev/export") || li.textContent?.includes("x.dev/schema"));
    expect(plan).toHaveLength(2);
    expect(hrefs(plan[0])).toEqual(["https://docs.example.dev/export"]);
    expect(plan[1].className).toBe("is-rework");
    expect(hrefs(plan[1])).toEqual(["https://x.dev/schema"]);
    expect(plan[1].textContent).toBe("[修改方向] 别动 schema，见 https://x.dev/schema。");
  });

  it("怎样算办完 stays plain (native DodListView never linkified)", () => {
    render(<DetailFields detail={proposal} />);
    const dodHeading = screen.getByRole("heading", { name: "Definition of done:" });
    expect(hrefs(dodHeading.parentElement as ParentNode)).toEqual([]);
  });

  it("交付了什么 body stays plain (Cards.swift:1829); the grey approval-time summary below it is linkified (deliberate web extension over :1843-1850)", () => {
    const review: CardDetail = {
      id: "R-410", lane: "review", state: "review", name: "导出按钮",
      delivered_summary: "已加按钮，PR 在 https://github.com/Wan-ZL/example-bench/pull/13",
      summary: "按 https://github.com/Wan-ZL/example-bench/issues/7 加导出按钮",
    };
    render(<DetailFields detail={review} />);
    const paras = Array.from(document.querySelectorAll(".zai-detail-summary"));
    expect(paras).toHaveLength(2);
    expect(hrefs(paras[0])).toEqual([]);
    expect(paras[0].textContent).toBe(review.delivered_summary);
    expect(hrefs(paras[1])).toEqual(["https://github.com/Wan-ZL/example-bench/issues/7"]);
  });

  it("no URL anywhere → no anchor at all", () => {
    render(<DetailFields detail={{ ...proposal, summary: "加导出按钮", sources: [{ who: "sam", channel: "slack", date: "2026-08-30", quote: "能不能一键导出" }], plan: ["加按钮"], dod: [] }} />);
    expect(document.querySelectorAll("a")).toHaveLength(0);
  });
});
