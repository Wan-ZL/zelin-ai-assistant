// 设置 → 通用 · 「界面语言」保存 = 同一把开关（CONTRACT §15 追记 2026-09-06，D37；§68.1；行为对齐审计
// settings-language-two-switches「Settings 界面语言 does not switch the board UI」）：草稿选 en → 「保存」→ PUT 成功 → store.language
// 立刻切（原生 Settings.persistLanguage 写完即 LanguageStore.lang = …），不等下次启动；同区别的键保存不碰语言；PUT 失败 toast、语言不动。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSettingsCatalog, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { getState, resetStoreForTests, setLanguage } from "../../store";
import type { SettingsSection } from "../../types";
import { CatalogSection } from "./CatalogSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), putSettingsSection: vi.fn() };
});

function general(language: string, source: string, format = "markdown"): SettingsSection {
  return {
    id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
    fields: [
      { key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
        default: "zh", choices: ["zh", "en"], effective: language, source },
      { key: "default_output_format", kind: "enum", label: { zh: "交付物默认格式", en: "Deliverable format" }, help: { zh: "", en: "" },
        default: "markdown", choices: ["markdown", "html"], effective: format, source: "default" },
    ],
  };
}

function renderZh() {
  setLanguage("zh");
  return render(<LanguageContext.Provider value="zh"><CatalogSection sectionId="general" /></LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue({ sections: [general("zh", "override")] });
});

afterEach(cleanup);

describe("CatalogSection · 界面语言保存即切换", () => {
  it("选 en → 保存 → PUT 只带 language、store 立刻 en、缓存刷", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(general("en", "override"));
    renderZh();
    const select = await screen.findByLabelText("界面语言") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "en" } });
    expect(getState().language).toBe("zh");                                      // 草稿不切——保存才切（草稿 + 保存模型不变）
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "en" }]);
    await waitFor(() => expect(getState().language).toBe("en"));
    expect(window.localStorage.getItem("zai.lang")).toBe("en");
  });

  it("同区只改别的键 → 语言不动", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(general("zh", "override", "html"));
    renderZh();
    const select = await screen.findByLabelText("交付物默认格式") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "html" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { default_output_format: "html" }]);
    await screen.findByRole("status");
    expect(getState().language).toBe("zh");
  });

  it("PUT 失败 → 「保存设置失败: 」toast、语言不动", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "language must be one of zh, en" } }));
    renderZh();
    const select = await screen.findByLabelText("界面语言") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("保存设置失败: ");
    expect(getState().language).toBe("zh");
  });
});
