// 顶栏语言切换写回 server（CONTRACT §15 追记 2026-09-06，D37；行为对齐审计 settings-language-two-switches /
// store-language-two-switches-ui-vs-daemon）：点一下 = store 立刻翻转 + PUT /api/settings/general {language}——与 `/lang`、向导、
// 设置区「保存」同一把开关；PUT 失败不影响本次切换；一次性的 ?lang= 覆写随手摘掉（否则刷新后又压回去、且有它就不水合）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { getState, resetStoreForTests, setLanguage } from "../../store";
import { LanguageToggle } from "./LanguageToggle";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, putSettingsSection: vi.fn() };
});

const receipt = (language: string) => ({
  id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
  fields: [{ key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
    default: "zh", choices: ["zh", "en"], effective: language, source: "override" }],
});

function renderToggle(language: "zh" | "en") {
  setLanguage(language);
  return render(<LanguageContext.Provider value={language}><LanguageToggle /></LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(putSettingsSection).mockReset();
  window.history.replaceState(null, "", "/");
});

afterEach(cleanup);

describe("LanguageToggle · 写回 server", () => {
  it("zh → en：store 立刻翻转、缓存刷、PUT general {language: en}", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    renderToggle("zh");
    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));
    expect(getState().language).toBe("en");
    expect(window.localStorage.getItem("zai.lang")).toBe("en");
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "en" }]);
  });

  it("en → zh 同款", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    renderToggle("en");
    fireEvent.click(screen.getByRole("button", { name: "Switch to Chinese" }));
    expect(getState().language).toBe("zh");
    await waitFor(() => expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "zh" }]));
  });

  it("PUT 失败（离线 / 浏览器会话无 token）：本次切换照样生效，不抛", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(0, { error: { code: "SERVICE_UNAVAILABLE", message: "offline" } }));
    renderToggle("zh");
    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(getState().language).toBe("en");
  });

  it("摘掉一次性的 ?lang= 覆写（别的 query 留着）", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    window.history.replaceState(null, "", "/?page=settings&lang=zh");
    renderToggle("zh");
    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));
    expect(window.location.search).toBe("?page=settings");
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
  });
});
