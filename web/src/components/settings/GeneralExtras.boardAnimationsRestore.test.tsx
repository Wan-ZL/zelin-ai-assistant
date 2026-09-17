// 「看板动画」开关对 boardAnimations（CONTRACT §66.2 setting:prefs:boardAnimations；原生 Settings.swift / §43 的 UserDefaults
// 同名键；web 落点 §54.4 2026-09-03 追记；§68.14 追记「飞行层退役、开关不退役」；本组件其余行 §68.6）的**读回**：
// parity.test.tsx 同名 it() 钉「缺键 = 开 → 点一下 → 键 "false" + <html data-board-animations=off>」，这里钉另外三刀：
//   1) 键里已是 "false" → 挂载的开关是关的（checked / aria-checked 都 false）；再点开 → 键 "true"、<html> 上的属性摘掉；重开读回开；
//   2) 只认字面量 "false"（readBoardAnimations 是 `!== "false"`）："0" / "off" / "no" / "False" / 空串 都按开挂载，键不改写；
//   3) index.html 首帧脚本（比 React 早）：键 "false" → 首帧就写 data-board-animations="off"（避免闪一下动效）；缺键 / 其它值 → 不写。
//      脚本从 index.html?raw 里抠出来在 jsdom 里真跑（仓库自己的 checked-in 源，不是外来字串），不是字面量探针（tokens.test.ts 对主题那句是字面量）。
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import indexHtml from "../../../index.html?raw";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import { GeneralExtras } from "./GeneralExtras";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSetup: vi.fn(), postSetupStep: vi.fn(), postRevealTarget: vi.fn() };
});

const KEY = "boardAnimations";
const html = () => document.documentElement;

function mountSwitch() {
  const view = render(
    <LanguageContext.Provider value="en">
      <GeneralExtras />
    </LanguageContext.Provider>,
  );
  return view.container.querySelector<HTMLInputElement>("#setting-general-boardAnimations")!;
}

/** index.html <head> 里的行内 <script>（不带 src；两个 IIFE：主题 + 看板动画、显示偏好）——先确认抠到的是那一段，再在 jsdom 里真跑一遍 */
function runBootScript() {
  const match = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/.exec(indexHtml);
  if (!match) throw new Error("index.html has no inline <script>");
  expect(match[1]).toContain('localStorage.getItem("boardAnimations")'); // 看板动画那行搬家 / 改名 → 这里先红、说清是它
  new Function(match[1])();
}

function clearHtmlDataset() {
  for (const key of Object.keys(html().dataset)) delete html().dataset[key];
}

beforeEach(() => {
  window.localStorage.clear();
  clearHtmlDataset();
  resetStoreForTests();
});

afterEach(() => {
  cleanup();
  clearHtmlDataset();
});

describe("GeneralExtras — boardAnimations 读回", () => {
  it("键里是 \"false\" → 开关挂载即关；点开 → 键 \"true\"、<html> 的 data-board-animations 摘掉；重开读回开", () => {
    window.localStorage.setItem(KEY, "false");
    html().dataset.boardAnimations = "off"; // 首帧脚本落下的状态
    const toggle = mountSwitch();
    expect(toggle.checked).toBe(false);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(window.localStorage.getItem(KEY)).toBe("false"); // 挂载不改写键（开关状态是 useState 首帧读的，键被改写它也不会变）
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(window.localStorage.getItem(KEY)).toBe("true");
    expect(html().dataset.boardAnimations).toBeUndefined();
    cleanup();
    expect(mountSwitch().checked).toBe(true);
  });

  it.each(["0", "off", "no", "False", ""])("只认字面量 \"false\"：存储 %j 按开挂载，键不改写", (raw) => {
    window.localStorage.setItem(KEY, raw);
    const toggle = mountSwitch();
    expect(toggle.checked).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(window.localStorage.getItem(KEY)).toBe(raw);
  });
});

describe("index.html 首帧脚本 — boardAnimations", () => {
  it("键 \"false\" → 首帧就写 data-board-animations=\"off\"（React 挂载前，避免闪一下动效）", () => {
    window.localStorage.setItem(KEY, "false");
    runBootScript();
    expect(html().dataset.boardAnimations).toBe("off");
    expect(html().dataset.theme).toBe("light");
  });

  it.each([null, "true", "0"])("键 %j → 首帧不写属性（缺省 = 开，CSS 零成本）", (raw) => {
    if (raw !== null) window.localStorage.setItem(KEY, raw);
    runBootScript();
    expect(html().dataset.boardAnimations).toBeUndefined();
    expect(html().dataset.theme).toBe("light"); // 同一段脚本的主题默认（theme:default）照常落下——证明脚本真跑了
  });
});
