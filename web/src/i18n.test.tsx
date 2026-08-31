// i18n 回退行为测试：resolveLanguage 的 zh 判定与 en 兜底、text(zh,en) 内联对选边、
// useI18n 无 Provider 时默认英文（LanguageContext 缺省值即回退语义）。
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ReactNode } from "react";
import { getI18n, LanguageContext, resolveLanguage, useI18n } from "./i18n";

describe("resolveLanguage", () => {
  it("maps zh and zh-* variants (any case, _ or -) to zh", () => {
    expect(resolveLanguage("zh")).toBe("zh");
    expect(resolveLanguage("zh-CN")).toBe("zh");
    expect(resolveLanguage("zh_TW")).toBe("zh");
    expect(resolveLanguage(" ZH-Hans ")).toBe("zh");
  });

  it("falls back to en for everything else, including empty/null/undefined", () => {
    expect(resolveLanguage("en")).toBe("en");
    expect(resolveLanguage("en-US")).toBe("en");
    expect(resolveLanguage("fr-FR")).toBe("en"); // 未支持语言 → 英文兜底
    expect(resolveLanguage("zhx")).toBe("en"); // 只认 zh 全等或 zh- 前缀，不认裸前缀
    expect(resolveLanguage("")).toBe("en");
    expect(resolveLanguage(null)).toBe("en");
    expect(resolveLanguage(undefined)).toBe("en");
  });
});

describe("getI18n", () => {
  it("picks the matching side of the text(zh,en) inline pair", () => {
    expect(getI18n("zh").text("看板", "Board")).toBe("看板");
    expect(getI18n("en").text("看板", "Board")).toBe("Board");
  });

  it("exposes the matching locale for date formatting", () => {
    expect(getI18n("zh").locale).toBe("zh-CN");
    expect(getI18n("en").locale).toBe("en");
  });
});

describe("useI18n", () => {
  it("defaults to English when no LanguageContext provider is mounted", () => {
    const { result } = renderHook(() => useI18n());
    expect(result.current.language).toBe("en");
    expect(result.current.text("中文", "English")).toBe("English");
  });

  it("follows the provided language", () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <LanguageContext.Provider value="zh">{children}</LanguageContext.Provider>
    );
    const { result } = renderHook(() => useI18n(), { wrapper });
    expect(result.current.language).toBe("zh");
    expect(result.current.text("中文", "English")).toBe("中文");
  });
});
