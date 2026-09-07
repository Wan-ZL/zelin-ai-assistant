// 语言只有一把开关（CONTRACT §15 追记 2026-09-06，owner 授权代拍 D37；§49 / §61.1 追记）：真源 = server 的 general.language。
// store 三条路：chooseLanguage（用户显式选：UI 立刻切 + PUT general.language，失败静默）；hydrateLanguage（启动水合：显式值压过
// 首帧缓存；source default → 首启持久化一次；?lang= 一次性覆写两件都不做；用户先选了就用户赢）；saveSettingsSection("general",
// {language}) 成功 → UI 立刻切（设置区「保存」= 同一把开关）。经 vi.mock 替换 api，零真实网络。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSettingsCatalog, fetchSettingsSection, putSettingsSection } from "./api";
import {
  chooseLanguage, getState, hasLanguageQueryOverride, hydrateLanguage, refreshSettingsCatalog, resetStoreForTests, saveSettingsSection,
  setLanguage,
} from "./store";
import type { SettingsField, SettingsSection } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchSettingsSection: vi.fn(), putSettingsSection: vi.fn(), fetchSettingsCatalog: vi.fn() };
});

function languageField(effective: string, source: string): SettingsField {
  return {
    key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
    default: "zh", choices: ["zh", "en"], effective, source,
  };
}

function general(effective: string, source: string, extra: SettingsField[] = []): SettingsSection {
  return { id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" }, fields: [languageField(effective, source), ...extra] };
}

/** server 对 PUT 的回执：write:always → 落的就是 patch 里的值、source override */
function receipt(language: string): SettingsSection {
  return general(language, "override");
}

const offline = () => new ApiError(0, { error: { code: "SERVICE_UNAVAILABLE", message: "offline" } });

let originalSearch: string;

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(fetchSettingsSection).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSettingsCatalog).mockReset();
  originalSearch = window.location.search;
  window.history.replaceState(null, "", "/");
  setLanguage("en"); // jsdom 的 navigator.language 是 en-US；显式钉住起点
});

afterEach(() => {
  window.history.replaceState(null, "", `/${originalSearch}`);
});

describe("chooseLanguage（顶栏 / /lang / 向导）", () => {
  it("UI 立刻切、首帧缓存刷、PUT general {language}", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    const done = chooseLanguage("zh");
    expect(getState().language).toBe("zh");                       // 不等 PUT
    expect(window.localStorage.getItem("zai.lang")).toBe("zh");
    await done;
    expect(putSettingsSection).toHaveBeenCalledTimes(1);
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "zh" }]);
    expect(getState().language).toBe("zh");
  });

  it("PUT 失败静默：本次会话仍是新语言（离线 / 无 token 的浏览器会话）", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(offline());
    await expect(chooseLanguage("zh")).resolves.toBeUndefined();
    expect(getState().language).toBe("zh");
  });

  it("回执落地时把目录里的 general 区换成回执（设置页「界面语言」的来源章随之）", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue({ sections: [general("en", "default")] });
    await refreshSettingsCatalog();
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    await chooseLanguage("zh");
    const field = getState().settingsCatalog!.sections[0].fields[0];
    expect([field.effective, field.source]).toEqual(["zh", "override"]);
  });

  it("连拨两次：晚到的第一次回执不许把语言拨回去", async () => {
    let resolveFirst: (s: SettingsSection) => void = () => undefined;
    vi.mocked(putSettingsSection)
      .mockImplementationOnce(() => new Promise<SettingsSection>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(receipt("en"));
    const first = chooseLanguage("zh");
    const second = chooseLanguage("en");
    await second;
    resolveFirst(receipt("zh"));
    await first;
    expect(getState().language).toBe("en");
  });
});

describe("hydrateLanguage（启动）", () => {
  it("server 有显式值（override）→ 压过首帧缓存 / 浏览器猜测，缓存跟着刷", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "override"));
    await hydrateLanguage();
    expect(fetchSettingsSection).toHaveBeenCalledWith("general");
    expect(getState().language).toBe("zh");
    expect(window.localStorage.getItem("zai.lang")).toBe("zh");
    expect(putSettingsSection).not.toHaveBeenCalled();          // 显式值不重写
  });

  it("config.yaml 的 language 也是显式值（python 侧 _persisted_language 读它）", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "config"));
    await hydrateLanguage();
    expect(getState().language).toBe("zh");
    expect(putSettingsSection).not.toHaveBeenCalled();
  });

  it("source default（谁都没选过）→ 首启持久化：把此刻显示的语言 PUT 一次、UI 不动", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "default"));   // 目录 default 是 zh 占位
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    await hydrateLanguage();
    expect(getState().language).toBe("en");                                         // 不采纳占位 default
    expect(vi.mocked(putSettingsSection).mock.calls).toEqual([["general", { language: "en" }]]);
  });

  it("首启持久化是幂等的：第二次启动 server 已是 override → 只读不写", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValueOnce(general("zh", "default"));
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    await hydrateLanguage();
    vi.mocked(fetchSettingsSection).mockResolvedValueOnce(general("en", "override"));
    await hydrateLanguage();
    expect(putSettingsSection).toHaveBeenCalledTimes(1);
    expect(getState().language).toBe("en");
  });

  it("首启持久化的 PUT 失败静默（下次启动再试）", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "default"));
    vi.mocked(putSettingsSection).mockRejectedValue(offline());
    await expect(hydrateLanguage()).resolves.toBeUndefined();
    expect(getState().language).toBe("en");
  });

  it("首启持久化时目录 GET 正在路上 → 等它落地再补拉一次（来源章不说谎）", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "default"));
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    let resolveCatalog: (c: { sections: SettingsSection[] }) => void = () => undefined;
    vi.mocked(fetchSettingsCatalog)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveCatalog = resolve; }))   // 写之前发出的 GET：还是 default
      .mockResolvedValueOnce({ sections: [general("en", "override")] });                       // 补拉：新鲜
    const inflight = refreshSettingsCatalog();
    await hydrateLanguage();
    resolveCatalog({ sections: [general("zh", "default")] });
    await inflight;
    await vi.waitFor(() => expect(fetchSettingsCatalog).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(getState().settingsCatalog!.sections[0].fields[0].source).toBe("override"));
  });

  it("?lang= 在场 = 一次性覆写：不读、不写", async () => {
    window.history.replaceState(null, "", "/?lang=zh");
    expect(hasLanguageQueryOverride()).toBe(true);
    await hydrateLanguage();
    expect(fetchSettingsSection).not.toHaveBeenCalled();
    expect(putSettingsSection).not.toHaveBeenCalled();
  });

  it("读失败 → 留着首帧的提示，不报错", async () => {
    vi.mocked(fetchSettingsSection).mockRejectedValue(offline());
    await expect(hydrateLanguage()).resolves.toBeUndefined();
    expect(getState().language).toBe("en");
  });

  it("没有 language 字段（老 server）→ 什么都不做", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue({ ...general("zh", "override"), fields: [] });
    await hydrateLanguage();
    expect(getState().language).toBe("en");
    expect(putSettingsSection).not.toHaveBeenCalled();
  });

  it("水合期间用户先选了语言 → 用户赢（server 的旧值不压回来）", async () => {
    let resolveGet: (s: SettingsSection) => void = () => undefined;
    vi.mocked(fetchSettingsSection).mockImplementation(() => new Promise((resolve) => { resolveGet = resolve; }));
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    const hydrating = hydrateLanguage();
    await chooseLanguage("zh");
    resolveGet(general("en", "override"));
    await hydrating;
    expect(getState().language).toBe("zh");
  });
});

describe("saveSettingsSection（设置区「保存」）", () => {
  it("general 区保存了 language → UI 立刻切（不等下次启动）", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    await saveSettingsSection("general", { language: "zh", default_output_format: "html" });
    expect(getState().language).toBe("zh");
    expect(window.localStorage.getItem("zai.lang")).toBe("zh");
  });

  it("general 区没动 language / 别的区 → 语言不动", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(general("zh", "override"));
    await saveSettingsSection("general", { default_output_format: "html" });
    expect(getState().language).toBe("en");
    await saveSettingsSection("approval", { trash_retention_days: 7 });
    expect(getState().language).toBe("en");
  });

  it("保存期间用户在顶栏又选了别的 → 顶栏赢", async () => {
    let resolveSave: (s: SettingsSection) => void = () => undefined;
    vi.mocked(putSettingsSection)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve; }))
      .mockResolvedValueOnce(receipt("en"));
    const saving = saveSettingsSection("general", { language: "zh" });
    await chooseLanguage("en");
    resolveSave(receipt("zh"));
    await saving;
    expect(getState().language).toBe("en");
  });

  it("PUT 失败原样抛给页面 toast，语言不动", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "bad" } }));
    await expect(saveSettingsSection("general", { language: "zh" })).rejects.toBeInstanceOf(ApiError);
    expect(getState().language).toBe("en");
  });
});
