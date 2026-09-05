// 列顶输入框的提示栈与 title（CONTRACT §41 2026-09-05 追记；原生 Composer.swift:101-104 `.help`、:121 onChange 清错、
// :134-149 slashError → hintLine 栈）：
//   1) 草稿以 "/" 开头时输入框下给一行命令词表提示（hintLine），不以 "/" 开头就没有；提示行顶掉成功回执；
//   2) 失败句优先于提示行；一改字失败句即清（原生 `.onChange(of: text) { slashError = nil }`），"/" 草稿随即回到提示行；
//   3) textarea 与按钮的 title：直跑 = 「直接开跑：跳过提案与费用预估，成果仍进「待验收」」；捕获 = 「快速捕获（<壳快捷键>）」，
//      壳不在场时没有键可报就只有「快速捕获」（不谎报 ⌘L）；身份从 buildBody 的 payload 读（mode:"run"）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyShellState, resetShellBridgeForTests } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import { hintLine } from "./composerCommands";
import { LaneComposer, composerMode, composerTitle } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { postAction } from "../../api";

const en = (_zh: string, english: string) => english;
const HINT = hintLine(en);
const RUN_TITLE = "Runs now — skips the proposal & cost preview; the result still lands in Review";

function mount(mode: "propose" | "run" = "propose") {
  render(
    <LaneComposer
      placeholder="type here"
      submitLabel={mode === "run" ? "Run" : "Capture"}
      successNote="Submitted"
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
    expect(screen.getByText("Submitted")).toBeTruthy();
    fireEvent.change(field, { target: { value: "/op" } });
    expect(screen.queryByText("Submitted")).toBeNull();
    expect(screen.getByText(HINT)).toBeTruthy();
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

  it("the propose composer says 「快速捕获」 without a key in a plain browser (no shell hotkey to report)", () => {
    const { field, button } = mount("propose");
    expect(field.title).toBe("Quick capture");
    expect(button.title).toBe("Quick capture");
  });

  it("inside the shell the propose tooltip names the shell's real quick-capture hotkey", () => {
    const { field } = mount("propose");
    act(() => {
      applyShellState({ hotkey: "⌃⌥Space" });
    });
    expect(field.title).toBe("Quick capture (⌃⌥Space)");
  });

  it("composerMode reads the identity off the payload; composerTitle renders both languages", () => {
    expect(composerMode((t) => ({ action: "capture", text: t }))).toBe("propose");
    expect(composerMode((t) => ({ action: "capture", text: t, mode: "run" }))).toBe("run");
    const zh = (chinese: string) => chinese;
    expect(composerTitle("run", null, zh)).toBe("直接开跑：跳过提案与费用预估，成果仍进「待验收」");
    expect(composerTitle("propose", "⌃⌥Space", zh)).toBe("快速捕获（⌃⌥Space）");
    expect(composerTitle("propose", null, zh)).toBe("快速捕获");
    expect(composerTitle("propose", "", zh)).toBe("快速捕获");
  });
});
