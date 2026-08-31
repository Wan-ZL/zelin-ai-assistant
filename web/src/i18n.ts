// i18n：text(zh, en) 内联对模式，fork 自 dashi web/src/i18n.tsx（Apache-2.0，NOTICE 登记）。
// 约定：所有用户可见文案在组件内写成 text("中文", "English") 内联对——没有 key 表、
// 没有翻译文件；新增文案 = 新增一个内联对。领域词表（列名/动词/tier 提示等固定枚举）
// 由 filters/i18n 组件（A8）在本文件追加 Record<Language, Record<K, string>> 表，同 dashi 的
// STATUS_LABELS 形。本文件无 JSX（Provider 在 app.tsx 用 LanguageContext.Provider 直接挂）。
import { createContext, useContext } from "react";

export type Language = "zh" | "en";

export interface I18n {
  language: Language;
  locale: "zh-CN" | "en";
  text: (chinese: string, english: string) => string;
}

const I18N: Record<Language, I18n> = {
  zh: {
    language: "zh",
    locale: "zh-CN",
    text: (chinese) => chinese,
  },
  en: {
    language: "en",
    locale: "en",
    text: (_chinese, english) => english,
  },
};

export function resolveLanguage(value: string | null | undefined): Language {
  const normalized = value?.trim().replaceAll("_", "-").toLowerCase() ?? "";
  return normalized === "zh" || normalized.startsWith("zh-") ? "zh" : "en";
}

export function getI18n(language: Language): I18n {
  return I18N[language];
}

export const LanguageContext = createContext<Language>("en");

export function useI18n(): I18n {
  return I18N[useContext(LanguageContext)];
}

// ----- 领域词表（A8/G4 追加，仿 dashi STATUS_LABELS）：固定枚举值 → 双语标签。 --- #
// 铁律：未知枚举值原样展示（wire add-only，前端绝不因新值崩渲染）——一律经 domainLabel 取。
export type LabelTable = Record<Language, Record<string, string>>;

/** 卡片 type（debt/trash 行；radar 提取的开放枚举，常见值预置双语） */
export const TYPE_LABELS: LabelTable = {
  zh: { engineering: "工程", process: "流程", research: "研究", writing: "写作", digest: "回顾" },
  en: { engineering: "Engineering", process: "Process", research: "Research", writing: "Writing", digest: "Digest" },
};

/** 来源渠道（sources[].channel；开放枚举） */
export const CHANNEL_LABELS: LabelTable = {
  zh: { slack: "Slack", gmail: "Gmail", meeting: "会议", manual: "手动", screen: "屏幕" },
  en: { slack: "Slack", gmail: "Gmail", meeting: "Meeting", manual: "Manual", screen: "Screen" },
};

/** 回收站行 kind —— 对齐 mac/Sources/Cards.swift kindLabel */
export const TRASH_KIND_LABELS: LabelTable = {
  zh: { suggestion: "建议", debt: "潜在任务" },
  en: { suggestion: "Suggestion", debt: "Backlog" },
};

/** 回收站行 trash_reason —— 对齐 mac/Sources/Cards.swift trashReasonLabel */
export const TRASH_REASON_LABELS: LabelTable = {
  zh: { rejected: "你拒绝的", deleted: "你删除的" },
  en: { rejected: "You rejected it", deleted: "You deleted it" },
};

/** 词表取值：命中返回双语标签，未知值原样返回 */
export function domainLabel(table: LabelTable, language: Language, value: string): string {
  return table[language][value] ?? value;
}
