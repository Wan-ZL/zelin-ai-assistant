// 列顶输入框的提示栈与 title（CONTRACT §41 2026-09-05 追记；原生 Composer.swift:101-104 `.help`、:121 onChange 清错、
// :134-149 slashError → hintLine 栈）：
//   1) 草稿以 "/" 开头时输入框下给一行命令词表提示（hintLine），不以 "/" 开头就没有；提示行顶掉成功回执——
//      包括 ↑/↓ 翻历史翻出一条 "/…" 旧捕获（不走 onChange）的那条路；回执（捕获的 / 斜杠的）一改字即过期；
//   2) 失败句优先于提示行；一改字失败句即清（原生 `.onChange(of: text) { slashError = nil }`），"/" 草稿随即回到提示行；
//   3) textarea 与按钮的 title：直跑 = 「直接开跑：跳过提案与费用预估，成果仍进「待验收」」；捕获 = 「快速捕获（⌘L · <壳全局键>）」，
//      壳不在场时没有键可报就只有「快速捕获」（浏览器里 ⌘L 归地址栏，不谎报）；身份从 buildBody 的 payload 读（mode:"run"）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyShellState, resetShellBridgeForTests } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import { hintLine } from "./composerCommands";
import { LaneComposer, composerMode, composerTitle, quickCaptureKeys } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { postAction } from "../../api";

const en = (_zh: string, english: string) => english;
const HINT = hintLine(en);
const RUN_TITLE = "Runs now — skips the proposal & cost preview; the result still lands in Review";
/** 捕获回执 = 「"<原话前 20 字>" Submitted — analyzing (usually 2-3 min)」（captureReceipt.ts） */
const RECEIPT = /Submitted — analyzing/;

function mount(mode: "propose" | "run" = "propose") {
  render(
    <LaneComposer
      placeholder="type here"
      submitLabel={mode === "run" ? "Run" : "Capture"}
      buildBody={(t) => (mode === "run" ? { action: "capture", text: t, mode: "run" } : { action: "capture", text: t })}
    />,
  );
  return {
    field: screen.getByPlaceholderText("type here") as HTMLTextAreaElement,
    button: screen.getByRole("button", { name: mode === "run" ? "Run" : "Capture" }) as HTMLButtonElement,
  };
}

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  window.localStorage.clear();
  vi.mocked(postAction).mockClear();
  vi.mocked(postAction).mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  resetShellBridgeForTests();
});

describe("LaneComposer — '/' hint line", () => {
  it("shows the command vocabulary while the draft starts with '/', and only then", () => {
    const { field } = mount();
    expect(screen.queryByText(HINT)).toBeNull();
    fireEvent.change(field, { target: { value: "/" } });
    expect(screen.getByText(HINT)).toBeTruthy();
    fireEvent.change(field, { target: { value: "/Users/zelin/x 整理一下" } });
    expect(screen.getByText(HINT)).toBeTruthy(); // 原生 hasPrefix("/")：路径草稿也给提示（它仍是普通捕获）
    fireEvent.change(field, { target: { value: "plain text" } });
    expect(screen.queryByText(HINT)).toBeNull();
    fireEvent.change(field, { target: { value: " /rec off" } });
    expect(screen.queryByText(HINT)).toBeNull(); // 前导空格：不以 "/" 开头就不提示（原生 hasPrefix 同判）
  });

  it("the hint replaces the success receipt while a new '/' draft is being typed", async () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "a capture" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.getByText(RECEIPT)).toBeTruthy();
    fireEvent.change(field, { target: { value: "/op" } });
    expect(screen.queryByText(RECEIPT)).toBeNull();
    expect(screen.getByText(HINT)).toBeTruthy();
  });

  it("a slash command's receipt is one-shot: the next keystroke retires it, '/' shows the hint instead", async () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "/lang en" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.getByText("Language → en")).toBeTruthy();
    expect(field.value).toBe(""); // 命令成功清空草稿
    fireEvent.change(field, { target: { value: "/" } });
    expect(screen.queryByText("Language → en")).toBeNull();
    expect(screen.getByText(HINT)).toBeTruthy();
    fireEvent.change(field, { target: { value: "x" } });
    expect(screen.queryByText("Language → en")).toBeNull(); // 回执不回来：新草稿一开打它就过期
    expect(screen.queryByText(HINT)).toBeNull();
    expect(postAction).not.toHaveBeenCalled();
  });

  it("↑ recalling a '/…' capture from history (no onChange) still shows exactly one line: the hint, not the receipt", async () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "/Users/zelin/x 整理一下" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.getByText(RECEIPT)).toBeTruthy(); // 路径捕获照常发出并进历史
    expect(postAction).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(field, { key: "ArrowUp" }); // 空草稿 → 翻历史接管，直接 setDraft，不经过 onChange
    expect(field.value).toBe("/Users/zelin/x 整理一下");
    expect(screen.getByText(HINT)).toBeTruthy();
    expect(screen.queryByText(RECEIPT)).toBeNull();
    // 斜杠回执那条路同样：/lang en 成功后 ↑ 翻出 "/…" 旧条目 → 提示行顶掉「Language → en」
    // （成功的命令自己也进历史——原生 "commands count too"，§34 2026-09-05 追记——所以第一下翻出的是 /lang en，第二下才是路径）
    fireEvent.change(field, { target: { value: "/lang en" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.getByText("Language → en")).toBeTruthy();
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("/lang en");
    expect(screen.getByText(HINT)).toBeTruthy();
    expect(screen.queryByText("Language → en")).toBeNull();
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("/Users/zelin/x 整理一下");
    expect(screen.getByText(HINT)).toBeTruthy();
    expect(screen.queryByText("Language → en")).toBeNull();
    expect(document.querySelectorAll(".column-help")).toHaveLength(1);
  });
});

describe("LaneComposer — the error line clears on edit and outranks the hint", () => {
  it("a bad slash argument shows 未识别 (no hint underneath); the next keystroke clears it and the hint returns", async () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "/rec nope" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.getByText("Unrecognized or bad argument:")).toBeTruthy();
    expect(screen.queryByText(HINT)).toBeNull();
    expect(field.value).toBe("/rec nope"); // 输入保留
    fireEvent.change(field, { target: { value: "/rec nop" } });
    expect(screen.queryByText("Unrecognized or bad argument:")).toBeNull();
    expect(screen.getByText(HINT)).toBeTruthy();
    expect(postAction).not.toHaveBeenCalled();
  });

  it("a rejected capture's failure line also clears on the next edit", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox not writable"));
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "keep me" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(screen.getByText("Submit failed — input kept")).toBeTruthy();
    fireEvent.change(field, { target: { value: "keep me!" } });
    expect(screen.queryByText("Submit failed — input kept")).toBeNull();
    expect(field.value).toBe("keep me!");
  });
});

describe("LaneComposer — native .help tooltips as title", () => {
  it("the run composer's field and button carry the native run tooltip", () => {
    const { field, button } = mount("run");
    expect(field.title).toBe(RUN_TITLE);
    expect(button.title).toBe(RUN_TITLE);
  });

  it("the propose composer says 「快速捕获」 without a key in a plain browser (⌘L belongs to the address bar, no global key)", () => {
    const { field, button } = mount("propose");
    expect(field.title).toBe("Quick capture");
    expect(button.title).toBe("Quick capture");
  });

  it("inside the shell the propose tooltip names ⌘L (the native key) and the shell's global quick-capture hotkey", () => {
    const { field, button } = mount("propose");
    act(() => {
      applyShellState({ hotkey: "⌃⌥Space" });
    });
    expect(field.title).toBe("Quick capture (⌘L · ⌃⌥Space)");
    expect(button.title).toBe("Quick capture (⌘L · ⌃⌥Space)");
  });

  it("quickCaptureKeys: nothing without a shell; ⌘L alone when the shell reports no global hotkey", () => {
    expect(quickCaptureKeys(null)).toBeNull();
    expect(quickCaptureKeys({ hotkey: "⌃⌥Space" })).toBe("⌘L · ⌃⌥Space");
    expect(quickCaptureKeys({ hotkey: "" })).toBe("⌘L"); // 原生字面量「快速捕获（⌘L）」
  });

  it("composerMode reads the identity off the payload; composerTitle renders both languages", () => {
    expect(composerMode((t) => ({ action: "capture", text: t }))).toBe("propose");
    expect(composerMode((t) => ({ action: "capture", text: t, mode: "run" }))).toBe("run");
    const zh = (chinese: string) => chinese;
    expect(composerTitle("run", null, zh)).toBe("直接开跑：跳过提案与费用预估，成果仍进「待验收」");
    expect(composerTitle("propose", "⌘L · ⌃⌥Space", zh)).toBe("快速捕获（⌘L · ⌃⌥Space）");
    expect(composerTitle("propose", "⌘L", zh)).toBe("快速捕获（⌘L）");
    expect(composerTitle("propose", null, zh)).toBe("快速捕获");
    expect(composerTitle("propose", "", zh)).toBe("快速捕获");
  });
});
