// 窗口 / 标签页标题表（CONTRACT §54.1 追记）：原生 MainWindow.installTitleSink 的「Zelin's AI Assistant — <section.title>」——
// 每个 ?page= 值都有 zh / en 标签、栏上四项与导航栏同词、依赖检查两个旧深链落在「设置」（D30）、
// 分隔符是原生字面（空格 + em dash + 空格）。
import { describe, expect, it } from "vitest";
import { getI18n } from "../../i18n";
import type { AppPage } from "../../route";
import { APP_TITLE, PAGE_LABELS, pageLabel, pageTitle } from "./pageTitles";

const zh = getI18n("zh").text;
const en = getI18n("en").text;

/** route.ts 的合法页全集（表必须覆盖每一个——漏一页 tsc 先红，这里再钉一次运行时形状） */
const ALL_PAGES: readonly AppPage[] = ["recaps", "settings", "permissions", "diagnostics", "setup", "deps", "ingest", "about"];

describe("pageTitles", () => {
  it("每一页都有非空的 zh / en 标签", () => {
    for (const page of ALL_PAGES) {
      expect(PAGE_LABELS[page].zh.length, page).toBeGreaterThan(0);
      expect(PAGE_LABELS[page].en.length, page).toBeGreaterThan(0);
      expect(pageLabel(page, zh)).toBe(PAGE_LABELS[page].zh);
      expect(pageLabel(page, en)).toBe(PAGE_LABELS[page].en);
    }
  });

  it("栏上四项与导航栏同词（§54.4：会议纪要 / 录制与导入 / 设置 / 关于）", () => {
    expect(PAGE_LABELS.recaps).toEqual({ zh: "会议纪要", en: "Recaps" });
    expect(PAGE_LABELS.ingest).toEqual({ zh: "录制与导入", en: "Recording & import" });
    expect(PAGE_LABELS.settings).toEqual({ zh: "设置", en: "Settings" });
    expect(PAGE_LABELS.about).toEqual({ zh: "关于", en: "About" });
    expect(Object.keys(PAGE_LABELS).sort()).toEqual([...ALL_PAGES].sort()); // 退役页不留标签
  });

  it("依赖检查的两个旧深链（?page=deps / diagnostics）渲染设置页，标题也是「设置」（D30）", () => {
    expect(pageLabel("deps", zh)).toBe("设置");
    expect(pageLabel("diagnostics", en)).toBe("Settings");
  });

  it("整条标题 = 「Zelin's AI Assistant — <页>」，随语言变", () => {
    expect(APP_TITLE).toBe("Zelin's AI Assistant");
    expect(pageTitle("recaps", zh)).toBe("Zelin's AI Assistant — 会议纪要");
    expect(pageTitle("recaps", en)).toBe("Zelin's AI Assistant — Recaps");
    expect(pageTitle("settings", en)).toBe("Zelin's AI Assistant — Settings");
    expect(pageTitle("ingest", zh)).toBe("Zelin's AI Assistant — 录制与导入");
    expect(pageTitle("setup", en)).toBe("Zelin's AI Assistant — Setup");
  });
});
