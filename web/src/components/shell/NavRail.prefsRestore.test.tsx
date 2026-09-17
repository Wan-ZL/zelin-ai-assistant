// 侧栏两把 localStorage 偏好键（CONTRACT §66.2 setting:prefs:sidebarCollapsed / sidebarWidth；原生 MainWindow.swift 的
// UserDefaults 同名键）的**读回**这半边——parity.test.tsx 里同名的 it() 钉的是「点真控件 → 键里有值 → 读回来」，这里钉
// 「键里已经有值（上次会话 / 手改 / 坏值）→ 重新挂载的真控件长什么样」，以及持久化前的钳制：
//   1) sidebarCollapsed 只认字面量 "true"（readCollapsed）："1" / "yes" / "TRUE" 都算展开；收起态重开：aria-expanded=false、
//      没有标签、没有拖宽把手、不写行内宽度；再点一下写回 "false"（不是删键），把手与存储的宽度一起回来；
//   2) sidebarWidth 越界 / 非数字 / 非正数的存储值 → 挂载时钳到 160–320 或回落 200（readSidebarWidth），键本身不改写；
//   3) 拖出上下限只持久化钳制后的 320 / 160（onHandleUp 写的是 clampSidebarWidth 之后的 width，不是指针位移），松手前不写。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import { NavRail } from "./NavRail";

function renderRail() {
  return render(
    <LanguageContext.Provider value="en">
      <NavRail />
    </LanguageContext.Provider>,
  );
}

const rail = () => document.querySelector<HTMLElement>(".rail")!;
const toggle = () => screen.getByRole("button", { name: "Collapse/expand sidebar" });
const handle = () => screen.queryByRole("separator", { name: "Drag to resize the sidebar" });

beforeAll(() => {
  // jsdom 没有 Pointer Capture（拖宽把手用它锁指针）——同 NavRail.collapseMotion.test.tsx 的桩
  if (!HTMLElement.prototype.setPointerCapture) HTMLElement.prototype.setPointerCapture = () => {};
});

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(cleanup);

describe("NavRail — sidebarCollapsed 读回", () => {
  it("存储的 \"true\" → 挂载即收起：aria-expanded=false、没有标签、没有拖宽把手、不写行内宽度", () => {
    window.localStorage.setItem("sidebarCollapsed", "true");
    window.localStorage.setItem("sidebarWidth", "240");
    renderRail();
    expect(rail().classList.contains("is-collapsed")).toBe(true);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelectorAll(".rail-label").length).toBe(0);
    expect(handle()).toBeNull();
    expect(rail().style.width).toBe("");
  });

  it("收起态再点一下 → 写回 \"false\"（不是删键）；展开后把手回来，宽度用的是键里存的 240；重开读回展开态", () => {
    window.localStorage.setItem("sidebarCollapsed", "true");
    window.localStorage.setItem("sidebarWidth", "240");
    renderRail();
    fireEvent.click(toggle());
    expect(window.localStorage.getItem("sidebarCollapsed")).toBe("false");
    expect(rail().classList.contains("is-collapsed")).toBe(false);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(handle()).not.toBeNull();
    expect(rail().style.width).toBe("240px");
    expect(window.localStorage.getItem("sidebarWidth")).toBe("240"); // 收起 / 展开不碰宽度键
    cleanup();
    renderRail();
    expect(rail().classList.contains("is-collapsed")).toBe(false);
    expect(rail().style.width).toBe("240px");
  });

  it.each(["1", "yes", "TRUE", " true", "false", ""])("只认字面量 \"true\"：存储 %j 按展开挂载（把手在场），键不改写", (raw) => {
    window.localStorage.setItem("sidebarCollapsed", raw);
    renderRail();
    expect(rail().classList.contains("is-collapsed")).toBe(false);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(handle()).not.toBeNull();
    expect(window.localStorage.getItem("sidebarCollapsed")).toBe(raw);
  });
});

describe("NavRail — sidebarWidth 读回与钳制", () => {
  it.each([
    ["9999", "320px"], ["321", "320px"], ["320", "320px"], ["250", "250px"], ["160", "160px"], ["159", "160px"], ["10", "160px"],
    ["abc", "200px"], ["-5", "200px"], ["0", "200px"], ["", "200px"], ["NaN", "200px"],
  ])("存储值 %j → 挂载宽度 %s（越界钳到 160–320，非正数 / 非数字回落 200）；键本身不改写", (raw, width) => {
    window.localStorage.setItem("sidebarWidth", raw);
    renderRail();
    expect(rail().style.width).toBe(width);
    expect(window.localStorage.getItem("sidebarWidth")).toBe(raw);
  });

  it("没有键 → 200px；挂载不写键（只有松手才持久化）", () => {
    renderRail();
    expect(rail().style.width).toBe("200px");
    expect(window.localStorage.getItem("sidebarWidth")).toBeNull();
  });

  it("拖过上限 → 持久化的是钳制后的 320；拖过下限 → 160（写的是 clampSidebarWidth 之后的宽度，不是指针位移）；松手前不写", () => {
    window.localStorage.setItem("sidebarWidth", "200");
    renderRail();
    const grip = handle()!;
    fireEvent.pointerDown(grip, { clientX: 200, pointerId: 1 });
    fireEvent.pointerMove(grip, { clientX: 1200, pointerId: 1 });
    expect(rail().style.width).toBe("320px"); // 拖动中就钳
    expect(window.localStorage.getItem("sidebarWidth")).toBe("200"); // 松手前不写
    fireEvent.pointerUp(grip, { clientX: 1200, pointerId: 1 });
    expect(window.localStorage.getItem("sidebarWidth")).toBe("320");
    fireEvent.pointerDown(grip, { clientX: 320, pointerId: 2 });
    fireEvent.pointerMove(grip, { clientX: -1000, pointerId: 2 });
    fireEvent.pointerUp(grip, { clientX: -1000, pointerId: 2 });
    expect(window.localStorage.getItem("sidebarWidth")).toBe("160");
    cleanup();
    renderRail();
    expect(rail().style.width).toBe("160px");
  });
});
