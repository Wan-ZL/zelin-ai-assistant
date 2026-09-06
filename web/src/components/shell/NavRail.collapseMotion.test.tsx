// 侧栏折叠 / 展开动效判例（CONTRACT §54.4 2026-09-05 追记；原生 MainWindow.swift
// `.animation(.easeInOut(duration: 0.15), value: nav.sidebarCollapsed)`）：shell.css 在 prefers-reduced-motion:
// no-preference 下给 .rail 宽与内边距 150ms ease-in-out 的过渡；拖宽期间 NavRail 挂 `is-dragging`（过渡关掉，
// 宽度跟指针），松手摘掉；折叠钮翻转时不挂。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import shellCss from "../../styles/shell.css?raw";
import { NavRail } from "./NavRail";

function renderRail() {
  window.history.replaceState(null, "", "/");
  return render(
    <LanguageContext.Provider value="en">
      <NavRail />
    </LanguageContext.Provider>,
  );
}

/** 取 shell.css 里 `@media (prefers-reduced-motion: no-preference) { … }` 块的正文（只有一个这样的块） */
function reducedMotionNoPreferenceBlock(css: string): string {
  const start = css.indexOf("@media (prefers-reduced-motion: no-preference)");
  expect(start).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(start, i + 1);
    }
  }
  throw new Error("unbalanced @media block");
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  // jsdom 没有 pointer capture；NavRail 的 onHandleDown 会调它
  if (!HTMLElement.prototype.setPointerCapture) HTMLElement.prototype.setPointerCapture = () => {};
});

afterEach(cleanup);

describe("NavRail — 折叠 / 展开动效", () => {
  it("shell.css：.rail 的 width / padding 150ms ease-in-out 过渡只在 prefers-reduced-motion: no-preference 下，.rail.is-dragging 关掉", () => {
    const block = reducedMotionNoPreferenceBlock(shellCss);
    expect(block).toMatch(/\.rail\s*\{[^}]*transition:\s*width 150ms ease-in-out,\s*padding 150ms ease-in-out;/);
    expect(block).toMatch(/\.rail\.is-dragging\s*\{[^}]*transition:\s*none;/);
    // 块外的 .rail 规则不带 transition（减弱动效 = 即时切换）
    const outside = shellCss.replace(block, "");
    const railRule = /\.rail\s*\{([^}]*)\}/.exec(outside)?.[1] ?? "";
    expect(railRule).not.toContain("transition");
  });

  it("拖动把手期间 .rail 挂 is-dragging，松手（pointerup / pointercancel）摘掉；折叠钮翻转不挂", () => {
    renderRail();
    const rail = document.querySelector(".rail")!;
    const handle = screen.getByRole("separator", { name: "Drag to resize the sidebar" });
    expect(rail.classList.contains("is-dragging")).toBe(false);
    fireEvent.pointerDown(handle, { clientX: 200, pointerId: 1 });
    expect(rail.classList.contains("is-dragging")).toBe(true);
    fireEvent.pointerMove(handle, { clientX: 240, pointerId: 1 });
    expect(rail.classList.contains("is-dragging")).toBe(true);
    expect((rail as HTMLElement).style.width).toBe("240px");
    fireEvent.pointerUp(handle, { clientX: 240, pointerId: 1 });
    expect(rail.classList.contains("is-dragging")).toBe(false);
    expect(window.localStorage.getItem("sidebarWidth")).toBe("240");
    // pointercancel 同样收尾
    fireEvent.pointerDown(handle, { clientX: 240, pointerId: 2 });
    expect(rail.classList.contains("is-dragging")).toBe(true);
    fireEvent.pointerCancel(handle, { pointerId: 2 });
    expect(rail.classList.contains("is-dragging")).toBe(false);
    // 折叠 / 展开走过渡（不挂 is-dragging）
    fireEvent.click(screen.getByRole("button", { name: "Collapse/expand sidebar" }));
    expect(rail.classList.contains("is-collapsed")).toBe(true);
    expect(rail.classList.contains("is-dragging")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Collapse/expand sidebar" }));
    expect(rail.classList.contains("is-collapsed")).toBe(false);
    expect(rail.classList.contains("is-dragging")).toBe(false);
  });
});
