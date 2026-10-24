// AppShell 把 document.title 写成原生窗口标题（CONTRACT §54.1 追记；MainWindow.installTitleSink）：
// 「Zelin's AI Assistant — <页标签>」随 ?page= 与 UI 语言变。<html lang> 的同步一并钉住。
// api 层 mock 掉（AppShell 挂载不发请求）。
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext, type Language } from "../../i18n";
import { resetStoreForTests } from "../../store";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchRecaps: vi.fn(), fetchHealth: vi.fn() };
});

function renderAt(search: string, language: Language) {
  window.history.replaceState(null, "", `/${search}`);
  return render(
    <LanguageContext.Provider value={language}>
      <AppShell>
        <div>page-content</div>
      </AppShell>
    </LanguageContext.Provider>,
  );
}

describe("AppShell · document.title 随页与语言", () => {
  beforeEach(() => {
    resetStoreForTests();
    document.title = "";
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState(null, "", "/");
  });

  const cases: Array<[string, Language, string]> = [
    ["", "zh", "Zelin's AI Assistant — 会议纪要"],
    ["", "en", "Zelin's AI Assistant — Recaps"],
    ["?page=recaps", "zh", "Zelin's AI Assistant — 会议纪要"],
    ["?page=recaps", "en", "Zelin's AI Assistant — Recaps"],
    ["?page=ingest", "zh", "Zelin's AI Assistant — 录制与导入"],
    ["?page=ingest", "en", "Zelin's AI Assistant — Recording & import"],
    ["?page=settings", "zh", "Zelin's AI Assistant — 设置"],
    ["?page=settings", "en", "Zelin's AI Assistant — Settings"],
    ["?page=deps", "zh", "Zelin's AI Assistant — 设置"],
    ["?page=diagnostics", "en", "Zelin's AI Assistant — Settings"],
    ["?page=about", "zh", "Zelin's AI Assistant — 关于"],
    ["?page=about", "en", "Zelin's AI Assistant — About"],
    ["?page=permissions", "zh", "Zelin's AI Assistant — 权限体检"],
    ["?page=permissions", "en", "Zelin's AI Assistant — Permissions Checkup"],
    ["?page=setup", "zh", "Zelin's AI Assistant — 初始设置"],
    ["?page=setup", "en", "Zelin's AI Assistant — Setup"],
    ["?page=trash", "en", "Zelin's AI Assistant — Recaps"], // 退役页回落会议纪要（route.readPage）
    ["?page=nonsense", "en", "Zelin's AI Assistant — Recaps"], // 未知页回落会议纪要
  ];

  for (const [search, language, expected] of cases) {
    it(`${search || "/"} × ${language} → ${expected}`, () => {
      renderAt(search, language);
      expect(document.title).toBe(expected);
    });
  }

  it("<html lang> 随语言：zh → zh-CN、en → en", () => {
    renderAt("", "zh");
    expect(document.documentElement.lang).toBe("zh-CN");
    cleanup();
    renderAt("", "en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("同一挂载里切语言，标题跟着换", () => {
    window.history.replaceState(null, "", "/?page=ingest");
    const view = render(
      <LanguageContext.Provider value="zh">
        <AppShell><div>page-content</div></AppShell>
      </LanguageContext.Provider>,
    );
    expect(document.title).toBe("Zelin's AI Assistant — 录制与导入");
    view.rerender(
      <LanguageContext.Provider value="en">
        <AppShell><div>page-content</div></AppShell>
      </LanguageContext.Provider>,
    );
    expect(document.title).toBe("Zelin's AI Assistant — Recording & import");
  });
});
