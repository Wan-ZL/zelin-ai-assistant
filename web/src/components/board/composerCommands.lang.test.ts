// `/lang zh|en` 写回 server（CONTRACT §15 追记 2026-09-06，D37；§41 斜杠命令）：原生 Store.swift `/lang` 是读-合并-写 language
// override + LanguageStore.lang——web 版 = store.chooseLanguage：UI 立刻切、PUT /api/settings/general {language}，回执不等 PUT；
// PUT 失败不改变命令的成功回执（UI 已经切了，下次启动由 server 决定）；参数打错仍是「用法」、不写。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, putSettingsSection } from "../../api";
import { getState, resetStoreForTests, setLanguage } from "../../store";
import { runSlashCommand } from "./composerCommands";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, putSettingsSection: vi.fn() };
});

const en = (_zh: string, english: string) => english;
const receipt = (language: string) => ({
  id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
  fields: [{ key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
    default: "zh", choices: ["zh", "en"], effective: language, source: "override" }],
});

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(putSettingsSection).mockReset();
  setLanguage("en");
});

afterEach(() => window.localStorage.clear());

describe("/lang · 写回 server", () => {
  it("/lang zh → store 立刻 zh + PUT general {language: zh}；回执不等 PUT", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    const result = await runSlashCommand("/lang zh", en);
    expect(result).toEqual({ handled: true, note: "Language → zh" });
    expect(getState().language).toBe("zh");
    await vi.waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "zh" }]);
  });

  it("参数不分大小写（原生 lowercased）：/lang ZH 也写 zh", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    await runSlashCommand("/LANG ZH", en);
    await vi.waitFor(() => expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "zh" }]));
  });

  it("PUT 失败：命令回执仍是成功、UI 仍切了（best-effort）", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(0, { error: { code: "SERVICE_UNAVAILABLE", message: "offline" } }));
    const result = await runSlashCommand("/lang zh", en);
    expect(result).toEqual({ handled: true, note: "Language → zh" });
    await vi.waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(getState().language).toBe("zh");
  });

  it("参数打错：用法句、不写、语言不动", async () => {
    const result = await runSlashCommand("/lang fr", en);
    expect(result.handled).toBe(true);
    expect("error" in result && result.error.kind).toBe("unrecognized");
    expect(putSettingsSection).not.toHaveBeenCalled();
    expect(getState().language).toBe("en");
  });
});
