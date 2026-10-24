// AppShell 行为测试（G7）：v2.0.0 起壳不再按看板快照分派整页状态——每一页自拉快照、自报读失败，壳只给导航栏 + 顶栏 +
// 主区，页面内容无条件渲染（读看板的加载 / 离线 / 缺文件 / 解不出来四态随 dashboard.json 退役，全文见 tag full）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetRouterForTests } from "../../route";
import { resetStoreForTests, setConnection } from "../../store";
import { AppShell } from "./AppShell";

function renderShell() {
  return render(
    <LanguageContext.Provider value="en">
      <AppShell>
        <div>page-content</div>
      </AppShell>
    </LanguageContext.Provider>,
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    resetStoreForTests();
    resetRouterForTests();
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    cleanup();
  });

  it("首帧（还在连）就渲染页面内容——没有整页的「正在加载看板…」", () => {
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    expect(screen.queryByText(/Loading the board/)).toBeNull();
  });

  it("骨架 = 导航栏（四项）+ 顶栏 + 可聚焦、带滚动记忆的 <main>", () => {
    renderShell();
    expect(document.querySelectorAll("[data-rail-item]")).toHaveLength(4);
    expect(document.querySelector("header.shell-header")).toBeTruthy();
    const main = document.querySelector<HTMLElement>("main.shell-main")!;
    expect(main.tabIndex).toBe(-1);
    expect(main.dataset.scrollMemory).toBe("shell-main");
  });

  it("SSE 断线时页面照常渲染，不挂任何横幅（连接状态只在顶栏的连接点上说）", () => {
    setConnection("reconnecting");
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.querySelector(".shell-connection.is-reconnecting")).toBeTruthy();
  });
});
