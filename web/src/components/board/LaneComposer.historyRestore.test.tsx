// 捕获框对 captureHistory（CONTRACT §66.2 setting:prefs:captureHistory；原生 Store.swift CaptureHistory 的 UserDefaults 同名键，
// §41 2026-09-05 追记）的**读回**——走真控件，不走 composerCommands 的纯函数（那半边 composerCommands.test.ts 钉着）：
//   1) 只有 v1.0 之前的旧键 zai.captureHistory → 新挂载的输入框 ↑ 照样翻出来，且顺手把历史搬到同名键、旧键删掉（一次性迁移）；
//      挂载本身不读键（第一次 ↑ 才读）；
//   2) 键里是坏 JSON → ↑ 不崩、草稿不动、键不改写；下一次成功提交把它盖成合法的一条；
//   3) 去重 + 封顶 20 走真提交：键里已有 20 条，再提交其中最旧的一条 → 它挪到最前、仍 20 条；再提交一条新的 → 最旧的掉出去、仍 20 条，
//      ↑ 第一下翻出的就是刚提交的。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import { HISTORY_KEY, HISTORY_MAX, LEGACY_HISTORY_KEY } from "./composerCommands";
import { LaneComposer } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchBoard: vi.fn(),
}));
import { postAction } from "../../api";

function mount() {
  render(
    <LaneComposer placeholder="type here" submitLabel="Capture" buildBody={(t) => ({ action: "capture", text: t })} />,
  );
  return {
    field: screen.getByPlaceholderText("type here") as HTMLTextAreaElement,
    button: screen.getByRole("button", { name: "Capture" }) as HTMLButtonElement,
  };
}

const stored = (): string[] => JSON.parse(window.localStorage.getItem(HISTORY_KEY) ?? "[]");

async function submit(field: HTMLTextAreaElement, button: HTMLButtonElement, text: string) {
  fireEvent.change(field, { target: { value: text } });
  await act(async () => {
    fireEvent.click(button);
  });
  expect(field.value).toBe(""); // 成功即清空（§41）
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(postAction).mockClear();
  vi.mocked(postAction).mockResolvedValue({ ok: true });
});

afterEach(cleanup);

describe("LaneComposer — captureHistory 读回", () => {
  it("只有旧键 zai.captureHistory → ↑ 照样翻出来，历史搬到同名键、旧键删掉；挂载本身不读键", () => {
    window.localStorage.setItem(LEGACY_HISTORY_KEY, JSON.stringify(["old one", "older"]));
    const { field } = mount();
    expect(window.localStorage.getItem(HISTORY_KEY)).toBeNull(); // 挂载不读：第一次 ↑ 才读（也才迁移）
    expect(fireEvent.keyDown(field, { key: "ArrowUp" })).toBe(false); // 接管了（preventDefault）
    expect(field.value).toBe("old one");
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("older");
    expect(stored()).toEqual(["old one", "older"]);
    expect(window.localStorage.getItem(LEGACY_HISTORY_KEY)).toBeNull();
  });

  it("键里是坏 JSON → ↑ 不崩、草稿不动、键不改写；下一次成功提交把它盖成合法的一条", async () => {
    window.localStorage.setItem(HISTORY_KEY, "not json");
    const { field, button } = mount();
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("");
    expect(window.localStorage.getItem(HISTORY_KEY)).toBe("not json");
    await submit(field, button, "fresh start");
    expect(stored()).toEqual(["fresh start"]);
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("fresh start");
  });

  it("去重 + 封顶 20 走真提交：最旧的一条再提交 → 挪到最前、仍 20；再来一条新的 → 最旧掉出、仍 20；↑ 先翻出刚提交的", async () => {
    const seed = Array.from({ length: HISTORY_MAX }, (_, i) => `entry ${String(i + 1).padStart(2, "0")}`); // 01 最新 … 20 最旧
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(seed));
    const { field, button } = mount();
    await submit(field, button, "entry 20");
    expect(stored()).toEqual(["entry 20", ...seed.slice(0, HISTORY_MAX - 1)]);
    expect(stored()).toHaveLength(HISTORY_MAX);
    await submit(field, button, "brand new");
    expect(stored()).toEqual(["brand new", "entry 20", ...seed.slice(0, HISTORY_MAX - 2)]);
    expect(stored()).toHaveLength(HISTORY_MAX);
    expect(stored()).not.toContain("entry 19");
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("brand new");
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("entry 20");
    expect(vi.mocked(postAction)).toHaveBeenCalledTimes(2);
  });
});
