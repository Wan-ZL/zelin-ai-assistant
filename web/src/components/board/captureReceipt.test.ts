// 列顶输入框回执的诚实纪律——纯函数半边（CONTRACT §10 / §41 2026-09-05 追记；原生 Cards.swift:934,951-956 / :848,863-867、
// Store.swift:343-353 / :402-411、PendingSweep.swift:169-192）：
//   1) stalled 判据 = 横幅的 describeHealth（stalled / failing / stale 为真；ok / unknown / 还没拉到为假）；
//   2) 四句状态句 + 两句超时条逐字镜像原生；回执带原话前 20 个 code point；
//   3) captureLanded：先认精确键 row.capture_id === POST 回的 inbox stem（§10 issue #7 / §49），再退到原生 captureMatches——
//      归一化（小写、去空白 / 标点 / 符号）后前 10 字双向 contains：propose 只看 needs_approval 的 title / summary，
//      run 只看 running + needs_input 的 name / summary，两者都不看 review。
import { describe, expect, it } from "vitest";
import type { Board, HealthSnapshot } from "../../types";
import {
  CAPTURE_NOTICE_FADE_MS,
  CAPTURE_TIMEOUT_MS,
  captureLanded,
  captureNote,
  captureReceiptLine,
  captureStem,
  captureTimeoutNotice,
  clip20,
  normalizedCapture,
  pipelineStalled,
} from "./captureReceipt";

const zh = (chinese: string) => chinese;
const en = (_zh: string, english: string) => english;

function health(overrides: Partial<HealthSnapshot> = {}): HealthSnapshot {
  return {
    verdict: "ok",
    heartbeat: { age_s: 4, phase: "idle", pid: 1, interval: 10, stale_after_s: 90, stale: false },
    dashboard: { generated_at: "2026-09-05T08:00:00Z", age_s: 5, stale: false },
    loop_health: { consecutive_failures: 0, last_error: null },
    checked_at: "2026-09-05T08:00:05Z",
    ...overrides,
  };
}

function board(overrides: Partial<Board> = {}): Board {
  return {
    generated_at: "2026-09-05T08:00:00Z",
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
    ...overrides,
  } as Board;
}

/** 对账凭据：默认没有 stem（server 没回 / 老 server）→ 只剩前缀猜测 */
const id = (text: string, stem: string | null = null) => ({ text, stem });

const approval = (title: string, summary?: string) =>
  ({ id: "P-1", title, summary, tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] }) as unknown as Board["needs_approval"][number];
const task = (name: string, summary?: string) => ({ id: "R-1", name, summary, state: "queued" }) as unknown as Board["running"][number];

describe("pipelineStalled — the banner's predicate, not `verdict !== ok`", () => {
  it("ok / unknown / no snapshot yet → not stalled (the banner is silent for exactly these)", () => {
    expect(pipelineStalled(null)).toBe(false);
    expect(pipelineStalled(health())).toBe(false);
    expect(pipelineStalled(health({ verdict: "unknown", heartbeat: null }))).toBe(false);
  });

  it("stalled / failing / stale → stalled (the three verdicts the banner speaks for)", () => {
    expect(pipelineStalled(health({ verdict: "stalled" }))).toBe(true);
    expect(pipelineStalled(health({ verdict: "failing", loop_health: { consecutive_failures: 3, last_error: "boom" } }))).toBe(true);
    expect(pipelineStalled(health({ verdict: "stale", heartbeat: null }))).toBe(true);
  });
});

describe("copy — native lines, verbatim", () => {
  it("propose: analyzing vs saved-to-queue (Cards.swift:951-956)", () => {
    expect(captureNote("propose", false, zh)).toBe("已提交，AI 分析中（通常 2-3 分钟）");
    expect(captureNote("propose", false, en)).toBe("Submitted — analyzing (usually 2-3 min)");
    expect(captureNote("propose", true, zh)).toBe("已保存到队列，pipeline 启动后开始处理");
    expect(captureNote("propose", true, en)).toBe("Saved to the queue — processed once the pipeline is running");
  });

  it("run: queued-for-dispatch vs saved-to-queue (Cards.swift:863-867)", () => {
    expect(captureNote("run", false, zh)).toBe("已提交，直接开跑（跳过提案），排队派发中…");
    expect(captureNote("run", false, en)).toBe("Submitted — running it now (skipped proposal), queued for dispatch…");
    expect(captureNote("run", true, zh)).toBe("已保存到队列，pipeline 启动后直接开跑");
    expect(captureNote("run", true, en)).toBe("Saved to the queue — runs once the pipeline is up");
  });

  it("the receipt line quotes the first 20 code points of what was typed (no ellipsis, like `prefix(20)`)", () => {
    expect(clip20("short")).toBe("short");
    expect(clip20("一二三四五六七八九十一二三四五六七八九十廿一")).toBe("一二三四五六七八九十一二三四五六七八九十");
    expect(clip20("😀".repeat(25))).toBe("😀".repeat(20)); // code point 计，不劈开代理对
    expect(captureReceiptLine("propose", "写一份 onboarding 文档", false, zh)).toBe("「写一份 onboarding 文档」已提交，AI 分析中（通常 2-3 分钟）");
    expect(captureReceiptLine("run", "abcdefghijklmnopqrstuvwxyz", true, en)).toBe('"abcdefghijklmnopqrst" Saved to the queue — runs once the pipeline is up');
  });

  it("timeout notices (Store.swift:402-411): run names the text and says it did not start; propose says analysis is slow", () => {
    expect(captureTimeoutNotice("run", "abcdefghijklmnopqrstuvwxyz", zh)).toBe("「abcdefghijklmnopqrst」任务没有开始——后台可能没在跑（检查 actd）");
    expect(captureTimeoutNotice("run", "x", en)).toBe('"x" did not start — the backend may not be running (check actd)');
    expect(captureTimeoutNotice("propose", "anything", zh)).toBe("分析比平时慢，卡片稍后会自动出现；一直没有就打开「依赖检查」页并查看 state/actd.log");
    expect(captureTimeoutNotice("propose", "anything", en)).toBe(
      "Analysis is slower than usual — the card should still appear; if it never does, open the Dependencies page and check state/actd.log",
    );
  });

  it("windows: 300 s propose / 180 s run (Store.swift:349-352); notices fade after 120 s", () => {
    expect(CAPTURE_TIMEOUT_MS).toEqual({ propose: 300_000, run: 180_000 });
    expect(CAPTURE_NOTICE_FADE_MS).toBe(120_000);
  });
});

describe("captureLanded — PendingSweep.captureMatches port", () => {
  it("normalizes case, whitespace, punctuation and symbols", () => {
    expect(normalizedCapture("  Write — the “Onboarding” doc!  ")).toBe("writetheonboardingdoc");
    expect(normalizedCapture("整理 一下：/Users/zelin/x")).toBe("整理一下userszelinx");
    expect(normalizedCapture("…—!?")).toBe("");
  });

  it("propose: a needs_approval row whose title or summary shares the first 10 normalized chars clears", () => {
    const typed = "Write the onboarding doc for new hires";
    expect(captureLanded(id(typed), "propose", board({ needs_approval: [approval("write the onboarding doc for new hires")] }))).toBe(true);
    // backend cosmetic rewrite (quotes, dash, spacing) survives
    expect(captureLanded(id(typed), "propose", board({ needs_approval: [approval("“Write” — the onboarding doc…")] }))).toBe(true);
    // summary counts too
    expect(captureLanded(id(typed), "propose", board({ needs_approval: [approval("unrelated title", "Write the onboarding doc")] }))).toBe(true);
    // bidirectional: the backend title's first 10 chars found anywhere in the typed text also counts (`p.contains(tKey)`)…
    expect(captureLanded(id(typed), "propose", board({ needs_approval: [approval("the onboarding doc")] }))).toBe(true);
    // …and a short typed text whose first 10 chars sit inside a longer backend title (`t.contains(pKey)`)
    expect(captureLanded(id("write the"), "propose", board({ needs_approval: [approval("Write the onboarding doc for new hires")] }))).toBe(true);
    // but neither key inside the other → no match
    expect(captureLanded(id(typed), "propose", board({ needs_approval: [approval("Onboard new hires quickly")] }))).toBe(false);
    expect(captureLanded(id(typed), "propose", board({ needs_approval: [approval("Something else entirely")] }))).toBe(false);
  });

  it("propose ignores running / needs_input / review rows; run ignores needs_approval / review rows", () => {
    const typed = "clean up the proposals backlog";
    const runRow = task("clean up the proposals backlog");
    expect(captureLanded(id(typed), "propose", board({ running: [runRow] }))).toBe(false);
    expect(captureLanded(id(typed), "propose", board({ needs_input: [runRow] }))).toBe(false);
    expect(captureLanded(id(typed), "run", board({ needs_approval: [approval(typed)] }))).toBe(false);
    // review is deliberately not a landing signal for either (a week-old accepted card with the same words = fake launch)
    const reviewRow = { id: "R-9", name: typed, title: typed, summary: typed } as unknown as Board["review"][number];
    expect(captureLanded(id(typed), "propose", board({ review: [reviewRow] }))).toBe(false);
    expect(captureLanded(id(typed), "run", board({ review: [reviewRow] }))).toBe(false);
  });

  it("run: running and needs_input rows match on name or summary", () => {
    const typed = "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议";
    expect(captureLanded(id(typed), "run", board({ running: [task("清理提案积压：审阅提案列的积压卡片")] }))).toBe(true);
    expect(captureLanded(id(typed), "run", board({ needs_input: [task("other", "清理提案积压 审阅提案列")] }))).toBe(true);
    expect(captureLanded(id(typed), "run", board({ running: [task("整理周报")] }))).toBe(false);
  });

  it("empty or punctuation-only input never matches; rows with missing fields are skipped", () => {
    expect(captureLanded(id(""), "propose", board({ needs_approval: [approval("anything")] }))).toBe(false);
    expect(captureLanded(id("!!!"), "propose", board({ needs_approval: [approval("anything")] }))).toBe(false);
    const bare = { id: "P-2" } as unknown as Board["needs_approval"][number];
    expect(captureLanded(id("anything"), "propose", board({ needs_approval: [bare] }))).toBe(false);
    expect(captureLanded(id("anything"), "run", board({ running: [{ id: "R-3" } as unknown as Board["running"][number]] }))).toBe(false);
  });

  it("exact key first: a row whose capture_id equals the POST's inbox stem lands even when the words differ", () => {
    const stem = "capture-0f3c";
    const row = { ...approval("AI rewrote the title completely"), capture_id: stem } as unknown as Board["needs_approval"][number];
    expect(captureLanded(id("my original words", stem), "propose", board({ needs_approval: [row] }))).toBe(true);
    expect(captureLanded(id("my original words", "capture-other"), "propose", board({ needs_approval: [row] }))).toBe(false);
    expect(captureLanded(id("my original words"), "propose", board({ needs_approval: [row] }))).toBe(false); // 没 stem 只剩前缀猜测
    // the key is lane-scoped like the prefix rule: a needs_approval row cannot land a run
    expect(captureLanded(id("my original words", stem), "run", board({ needs_approval: [row] }))).toBe(false);
    const queued = { ...task("queued row"), capture_id: stem } as unknown as Board["running"][number];
    expect(captureLanded(id("my original words", stem), "run", board({ running: [queued] }))).toBe(true);
  });

  it("captureStem reads `file: \"capture-<uuid>.json\"` off the POST response (§49) and tolerates anything else", () => {
    expect(captureStem({ ok: true, file: "capture-0f3c.json", action: "capture" })).toBe("capture-0f3c");
    expect(captureStem({ ok: true })).toBeNull();
    expect(captureStem({ ok: true, file: ".json" })).toBeNull();
    expect(captureStem({ ok: true, file: 42 })).toBeNull();
    expect(captureStem(null)).toBeNull();
    expect(captureStem("capture-1.json")).toBeNull();
  });
});
