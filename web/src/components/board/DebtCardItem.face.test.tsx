// 潜在任务卡面（原生 DebtRow.rowContent，Cards.swift:2028-2039）：
//   · 难度章走 hardnessLabel：hard → 「较难 / Hard」红章，soft → 「常规 / Routine」灰章，未知值原样灰章，
//     缺席不出章（此前 soft 什么都不渲染、hard 写成「硬需求」）；
//   · §76.2 三颗结算信号的卡面表现（completion_hint / decision_due / mention_escalated）。
// §78：这张卡长成了唯一的机器卡面（促成运行 / 拒绝 / 修改 / 研究并提议 / 删除 / 封存），章行里恒有一颗
//   tier 章——促成运行那颗键旁边永远说得出档位。促成运行那半边的判例在 DebtCardItem.promote.test.tsx，
//   §37 摘要优先链在 DebtCardItem.headline.test.tsx（一个 behavior 一个文件）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { DebtCard } from "../../types";
import { DebtCardItem } from "./DebtCardItem";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

function item(over: Partial<DebtCard> = {}): DebtCard {
  return { id: "P-113", title: "example-bench 的 README 安装一节过时了", summary: "setup 命令已经跑不通", display_title: "README 过时", ...over };
}

describe("DebtCardItem hardness chip (native hardnessLabel)", () => {
  it("hard → Hard 红章；soft → Routine 灰章；未知值原样灰章；缺席不出章", () => {
    const { unmount } = render(<DebtCardItem item={item({ hardness: "hard" })} />);
    expect(screen.getByText("Hard").className).toBe("chip chip-danger");
    unmount();
    render(<DebtCardItem item={item({ hardness: "soft" })} />);
    expect(screen.getByText("Routine").className).toBe("chip");
    cleanup();
    render(<DebtCardItem item={item({ hardness: "medium" })} />);
    expect(screen.getByText("medium").className).toBe("chip");
    cleanup();
    render(<DebtCardItem item={item()} />);
    // 难度缺席 = 不出难度章；§78 起章行里恒有一颗 tier 章（这张卡没分级 → 「未分级」）
    expect(screen.queryByText("Hard")).toBeNull();
    expect(screen.queryByText("Routine")).toBeNull();
    expect(screen.getByText("Untiered").closest(".chip")?.className).toBe("chip chip-purple");
  });

  it("zh：较难 / 常规（原生 L 对逐字）", () => {
    const { unmount } = render(<LanguageContext.Provider value="zh"><DebtCardItem item={item({ hardness: "hard" })} /></LanguageContext.Provider>);
    expect(screen.getByText("较难").className).toContain("chip-danger");
    expect(screen.queryByText("硬需求")).toBeNull();
    unmount();
    render(<LanguageContext.Provider value="zh"><DebtCardItem item={item({ hardness: "soft" })} /></LanguageContext.Provider>);
    expect(screen.getByText("常规").className).toBe("chip");
  });
});

describe("DebtCardItem §76.2 completion_hint (issue #313)", () => {
  // §78：这张卡长成了唯一的机器卡面，§76.2 的两颗一键（已办完 / 不做）也跟着提案卡搬了过来
  it("有提示 → 绿章 + 证据一句 + 两颗一键（done_external / reject），封存 / 删除照旧在", () => {
    render(<DebtCardItem item={item({ completion_hint: { at: 1788948000, note: "Compass repo 已建", channel: "meeting" } })} />);
    expect(screen.getByText("✅ Looks already done").className).toBe("chip chip-success");
    expect(screen.getByText("Compass repo 已建")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Done for good (seal, stop suggesting)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Already done · mark delivered" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Won't do · to trash" })).toBeTruthy();
  });

  it("缺席 / 空壳 → 章与证据行都不渲染", () => {
    const { unmount } = render(<DebtCardItem item={item()} />);
    expect(screen.queryByText("✅ Looks already done")).toBeNull();
    unmount();
    render(<DebtCardItem item={item({ completion_hint: { at: null, note: "" } })} />);
    expect(screen.queryByText(/✅ Evidence/)).toBeNull();
  });
});

// §76.2 的红色决策行只有在点名**这张卡上真有的键**时才是出口：§78 把「批准」改叫「促成运行」、
// 「暂缓」随提案列一起退役（卡上没有那颗键了），所以这一句与 act/lib/notify.py 的 msg_deadline_due
// （「现在做个决定：促成运行 / 拒绝」）同一对动词。
describe("DebtCardItem §76.2 decision_due (issue #313 / §78)", () => {
  it("点名 促成运行 / 拒绝，按钮就在下一排；不再出现 approve / defer / 暂缓", () => {
    render(<DebtCardItem item={item({ decision_due: true })} />);
    const note = screen.getByRole("note");
    expect(note.textContent).toBe("⏰ Past its deadline and still undecided — decide now: Run it / Reject");
    expect(note.textContent).not.toMatch(/defer|approve/i);
    expect(screen.getByRole("button", { name: "Run it" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
    cleanup();

    render(<LanguageContext.Provider value="zh"><DebtCardItem item={item({ decision_due: true })} /></LanguageContext.Provider>);
    expect(screen.getByRole("note").textContent).toBe("⏰ 截止日已到，这张卡还没拍板 —— 现在决定：促成运行 / 拒绝");
    expect(screen.queryByRole("button", { name: "暂缓" })).toBeNull();
    expect(screen.getByRole("button", { name: "促成运行" })).toBeTruthy();
  });

  it("缺席 → 不出决策行", () => {
    render(<DebtCardItem item={item()} />);
    expect(screen.queryByRole("note")).toBeNull();
  });
});

// 被提×N 章的 §76.2 升级档：提示句同样只许点名这张卡上真有的动词（§78——「批准」改叫促成运行、
// 「暂缓」没有按钮了），否则章的解释和下一排的按钮对不上。
describe("DebtCardItem §76.2 mention_escalated (issue #313 / §78)", () => {
  it("mention_escalated → 被提×N 章转 danger 并说出「仍未处理」；提示句点名 促成运行 / 拒绝", () => {
    const { unmount } = render(<DebtCardItem item={item({ repeated: 23, mention_escalated: true })} />);
    const loud = screen.getByText("Raised ×23 · still unhandled");
    expect(loud.className).toContain("chip-danger");
    expect(loud.getAttribute("title")).toBe("This came up 23 times and was never run or rejected");
    expect(loud.getAttribute("title")).not.toMatch(/approved|deferred/);
    unmount();

    render(<LanguageContext.Provider value="zh"><DebtCardItem item={item({ repeated: 23, mention_escalated: true })} /></LanguageContext.Provider>);
    expect(screen.getByText("被提×23 · 仍未处理").getAttribute("title")).toBe("这件事被提起过 23 次，一直没有促成运行或拒绝");
  });

  it("为假 → 保持安静档，提示句说的是「重述已合并进这张卡」", () => {
    render(<DebtCardItem item={item({ repeated: 23 })} />);
    const quiet = screen.getByText("Raised ×23");
    expect(quiet.className).toContain("chip-quiet");
    expect(quiet.getAttribute("title")).toMatch(/restatements were merged into this card/);
  });
});
