// 窗口 / 标签页标题（CONTRACT §54.1 追记）：镜像原生 MainWindow.installTitleSink——
// `"Zelin's AI Assistant — " + section.title`，随当前页与 UI 语言变（Mission Control / 窗口切换器里能认出在哪一页）。
// 页标签 = 原生 MainSection.title 逐字（任务台 / 录制与数据接入 / 回收站 / 永久性完成 / 设置 / 关于）；
// web 自有页取该页自己的抬头（会议纪要沿用导航栏的词）；依赖检查两个旧深链渲染的是设置页（D30）→ 「设置」。
// 只是一张表 + 两个纯函数：不读 store、不碰 document——AppShell 负责写 document.title；壳（shell/）经 WKWebView.title
// KVO 把它抬成窗口标题（另一批次）。NavRail 日后可改读本表（现在两处字面相同，判例钉住不漂）。
import type { AppPage } from "../../route";

export const APP_TITLE = "Zelin's AI Assistant";

/** 原生标题分隔符（MainWindow.swift installTitleSink 字面：em dash 两侧各一空格） */
const TITLE_SEPARATOR = " — ";

const SETTINGS = { zh: "设置", en: "Settings" } as const;

export const PAGE_LABELS: Readonly<Record<AppPage, { readonly zh: string; readonly en: string }>> = {
  board: { zh: "任务台", en: "Workbench" },
  recaps: { zh: "会议纪要", en: "Recaps" },
  ingest: { zh: "录制与数据接入", en: "Recording & Data Sources" },
  trash: { zh: "回收站", en: "Trash" },
  archive: { zh: "永久性完成", en: "Done for good" },
  settings: SETTINGS,
  deps: SETTINGS,
  diagnostics: SETTINGS,
  about: { zh: "关于", en: "About" },
  permissions: { zh: "权限体检", en: "Permissions Checkup" },
  setup: { zh: "初始设置", en: "Setup" },
  styleguide: { zh: "活体样式指南", en: "Living styleguide" },
};

/** 当前页的双语标签（经 useI18n().text 取语言） */
export function pageLabel(page: AppPage, text: (zh: string, en: string) => string): string {
  const label = PAGE_LABELS[page];
  return text(label.zh, label.en);
}

/** 整条标题：`Zelin's AI Assistant — <页标签>` */
export function pageTitle(page: AppPage, text: (zh: string, en: string) => string): string {
  return APP_TITLE + TITLE_SEPARATOR + pageLabel(page, text);
}
