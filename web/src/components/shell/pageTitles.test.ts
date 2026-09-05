// 窗口 / 标签页标题表（CONTRACT §54.1 追记）：原生 MainWindow.installTitleSink 的「Zelin's AI Assistant — <section.title>」——
// 每个 ?page= 值都有 zh / en 标签、原生六页逐字镜像 MainSection.title、依赖检查两个旧深链落在「设置」（D30）、
// 分隔符是原生字面（空格 + em dash + 空格）。
import { describe, expect, it } from "vitest";
import { getI18n } from "../../i18n";
import type { AppPage } from "../../route";
import { APP_TITLE, PAGE_LABELS, pageLabel, pageTitle } from "./pageTitles";

const zh = getI18n("zh").text;
const en = getI18n("en").text;

/** route.ts 的合法页全集（表必须覆盖每一个——漏一页 tsc 先红，这里再钉一次运行时形状） */
const ALL_PAGES: readonly AppPage[] = [
  "board", "trash", "styleguide", "settings", "recaps", "archive", "permissions", "diagnostics", "setup", "deps", "ingest", "about",
];

describe("pageTitles", () => {
  it("每一页都有非空的 zh / en 标签", () => {
    for (const page of ALL_PAGES) {
      expect(PAGE_LABELS[page].zh.length, page).toBeGreaterThan(0);
      expect(PAGE_LABELS[page].en.length, page).toBeGreaterThan(0);
      expect(pageLabel(page, zh)).toBe(PAGE_LABELS[page].zh);
      expect(pageLabel(page, en)).toBe(PAGE_LABELS[page].en);
    }
  });

  it("原生六页逐字镜像 MainSection.title（mac/Sources/MainWindow.swift）", () => {
    expect(PAGE_LABELS.board).toEqual({ zh: "任务台", en: "Workbench" });
    expect(PAGE_LABELS.ingest).toEqual({ zh: "录制与数据接入", en: "Recording & Data Sources" });
    expect(PAGE_LABELS.trash).toEqual({ zh: "回收站", en: "Trash" });
    expect(PAGE_LABELS.archive).toEqual({ zh: "永久性完成", en: "Done for good" });
    expect(PAGE_LABELS.settings).toEqual({ zh: "设置", en: "Settings" });
    expect(PAGE_LABELS.about).toEqual({ zh: "关于", en: "About" });
  });

  it("依赖检查的两个旧深链（?page=deps / diagnostics）渲染设置页，标题也是「设置」（D30）", () => {
    expect(pageLabel("deps", zh)).toBe("设置");
    expect(pageLabel("diagnostics", en)).toBe("Settings");
  });

  it("整条标题 = 「Zelin's AI Assistant — <页>」，随语言变", () => {
    expect(APP_TITLE).toBe("Zelin's AI Assistant");
    expect(pageTitle("board", zh)).toBe("Zelin's AI Assistant — 任务台");
    expect(pageTitle("board", en)).toBe("Zelin's AI Assistant — Workbench");
    expect(pageTitle("trash", zh)).toBe("Zelin's AI Assistant — 回收站");
    expect(pageTitle("settings", en)).toBe("Zelin's AI Assistant — Settings");
    expect(pageTitle("recaps", zh)).toBe("Zelin's AI Assistant — 会议纪要");
    expect(pageTitle("setup", en)).toBe("Zelin's AI Assistant — Setup");
  });
});
