// 待验收列头的三件工具（D74，issue #312）：隐藏 🤖 / 选中全部建议验收 / 选中全部中断收割。
// 钉：(1) 🤖 计数数的是整列、开关写进 URL（`bot=hide`）；(2) 两颗「选中全部」只**预选**——
// 进多选态、带上那批 id，不发任何 inbox 动作；(3) 数为 0 的键是死的；
// (4) 「建议验收」逐字认 §64 判官的词表值，别的评语不算。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { getState, resetStoreForTests } from "../../store";
import type { ReviewCard } from "../../types";
import { ReviewLaneTools, botRows, interruptedRows, suggestedAccept } from "./ReviewLaneTools";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, postAction: vi.fn() };
});

const row = (id: string, over: Partial<ReviewCard> = {}): ReviewCard =>
  ({ id, name: id, dod: [], delivery_mode: "chat", ...over }) as ReviewCard;

const ACCEPTED = row("R-1", { assessment: { summary: null, verdict: "建议验收", verdict_reason: null, at: null } });
const NEEDS_WORK = row("R-2", { assessment: { summary: null, verdict: "需继续做", verdict_reason: null, at: null } });
const INTERRUPTED = row("R-3", { interrupted: true });
const BOT = row("R-4", { self_improve: true });
const PLAIN = row("R-5");
const ROWS = [ACCEPTED, NEEDS_WORK, INTERRUPTED, BOT, PLAIN];

function renderTools(visible: ReviewCard[] = ROWS, all: ReviewCard[] = ROWS) {
  return render(
    <LanguageContext.Provider value="zh">
      <ReviewLaneTools visible={visible} all={all} />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(postAction).mockReset();
});
afterEach(cleanup);

describe("行挑选（纯函数）", () => {
  it("「建议验收」逐字认判官词表，别的评语与没评的都不算", () => {
    expect(suggestedAccept(ROWS)).toEqual(["R-1"]);
    expect(suggestedAccept([row("R-9", { assessment: { summary: null, verdict: "looks done", verdict_reason: null, at: null } })])).toEqual([]);
  });

  it("中断收割只认 interrupted === true；机器卡只认 self_improve === true（缺席 ≠ 机器卡）", () => {
    expect(interruptedRows(ROWS)).toEqual(["R-3"]);
    expect(botRows(ROWS)).toEqual(["R-4"]);
    expect(botRows([PLAIN])).toEqual([]);
  });
});

describe("<ReviewLaneTools />", () => {
  it("隐藏 🤖 数的是整列、按下写进 URL、再按一次取消", () => {
    renderTools([ACCEPTED], ROWS);          // 过滤生效时只剩一行可见，🤖 计数仍是整列
    const toggle = screen.getByRole("button", { name: /隐藏 1/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(toggle);
    expect(getState().filters.hideBot).toBe(true);
    expect(new URLSearchParams(window.location.search).get("bot")).toBe("hide");
    expect(screen.getByRole("button", { name: /已隐藏 1/ }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: /已隐藏 1/ }));
    expect(getState().filters.hideBot).toBe(false);
    expect(new URLSearchParams(window.location.search).has("bot")).toBe(false);
  });

  it("两颗「选中全部」只预选：进多选态、带上那批 id，一条 inbox 动作都不发", () => {
    renderTools();
    fireEvent.click(screen.getByRole("button", { name: "选中全部建议验收 (1)" }));
    expect(getState().selectionMode).toBe(true);
    expect([...getState().selectedIds]).toEqual(["R-1"]);

    fireEvent.click(screen.getByRole("button", { name: "选中全部中断收割 (1)" }));
    expect([...getState().selectedIds]).toEqual(["R-3"]);   // 换一批，不叠加
    expect(postAction).not.toHaveBeenCalled();
  });

  it("数为 0 的键是死的（没有 🤖 / 没有建议验收 / 没有中断收割时）", () => {
    renderTools([PLAIN], [PLAIN]);
    for (const name of [/隐藏 0/, "选中全部建议验收 (0)", "选中全部中断收割 (0)"]) {
      expect((screen.getByRole("button", { name: name as never }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("只数当前可见的行——过滤生效时所见即所选", () => {
    renderTools([NEEDS_WORK, INTERRUPTED], ROWS);
    expect(screen.getByRole("button", { name: "选中全部建议验收 (0)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "选中全部中断收割 (1)" })).toBeTruthy();
  });
});
