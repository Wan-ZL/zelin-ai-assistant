// i18n：text(zh, en) 内联对模式，fork 自 dashi web/src/i18n.tsx（Apache-2.0，NOTICE 登记）。
// 约定：所有用户可见文案在组件内写成 text("中文", "English") 内联对——没有 key 表、
// 没有翻译文件；新增文案 = 新增一个内联对。本文件无 JSX（Provider 在 app.tsx 用 LanguageContext.Provider 直接挂）。
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

// v2.0.0：领域词表（卡片 type / 看板列 / 回收站 kind 与原因）随看板退役，全文见 tag full；固定枚举词表若再出现，照 dashi
// STATUS_LABELS 的 Record<Language, Record<K, string>> 形加在这里，未知值原样展示（wire add-only）。
