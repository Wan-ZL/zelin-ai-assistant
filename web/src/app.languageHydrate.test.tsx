// App 挂载即从 server 水合语言（CONTRACT §15 追记 2026-09-06，D37；§49 追记；行为对齐审计 pages-shell-nav-language-two-knobs
// 「initial language ignores the persisted override」/ pages-shell-nav-first-run-language-not-persisted）：
// - server 有显式 general.language → 压过 localStorage 的首帧缓存，整个 UI（顶栏 / rail / 标题）换语言，缓存跟着刷；
// - source default → 把首帧显示的语言 PUT 一次（首启持久化），UI 不动；
// - ?lang= 在场 → 不读不写（截图 / 演示的一次性覆写）；
// - 壳在场：水合改了语言 → ShellControls 再发一次 setLanguage 给壳（悬浮窗 / 通知文案跟随，§61.1）。
// api 全 mock（fetchBoard 回最小快照，其余 offline）、realtime mock。
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchSettingsSection, putSettingsSection } from "./api";
import { App } from "./app";
import { resetRouterForTests } from "./route";
import { resetShellBridgeForTests } from "./shellBridge";
import { getState, resetStoreForTests, setLanguage } from "./store";

const { BOARD, SHELL_STATE } = vi.hoisted(() => ({
  BOARD: {
    generated_at: "2026-09-06T12:00:00Z",
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], counts: {},
  },
  SHELL_STATE: {
    recording: { available: false, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
    captions: { available: false, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false },
    language: "en",
  },
}));

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  const offline: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(actual)) {
    if (typeof value === "function" && /^(fetch|post|put|verify)/.test(name)) {
      offline[name] = vi.fn().mockRejectedValue(new Error("offline"));
    }
  }
  return { ...actual, ...offline, fetchBoard: vi.fn().mockResolvedValue(BOARD) };
});

vi.mock("./realtime", () => ({
  createBoardRealtime: vi.fn(() => ({ start: vi.fn(), stop: vi.fn() })),
}));

const general = (language: string, source: string) => ({
  id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
  fields: [{ key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
    default: "zh", choices: ["zh", "en"], effective: language, source }],
});

const boardTitle = () => document.querySelector("h1")?.textContent ?? "";

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.sessionStorage.setItem("zai.launched", "1");
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  resetRouterForTests();
  resetShellBridgeForTests();
  vi.mocked(fetchBoard).mockClear();
  vi.mocked(fetchSettingsSection).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  setLanguage("en"); // 首帧提示（localStorage / 浏览器猜的）
});

afterEach(() => {
  cleanup();
  delete window.webkit;
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("App · 语言水合", () => {
  it("server override zh → 整个 UI 换成中文、缓存刷；只读不写", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "override"));
    render(<App />);
    await waitFor(() => expect(getState().language).toBe("zh"));
    expect(fetchSettingsSection).toHaveBeenCalledTimes(1);
    expect(fetchSettingsSection).toHaveBeenCalledWith("general");
    await waitFor(() => expect(boardTitle()).not.toBe(""));
    expect(document.documentElement.lang).toBe("zh-CN");           // AppShell 随 store 语言写 <html lang>
    expect(window.localStorage.getItem("zai.lang")).toBe("zh");
    expect(putSettingsSection).not.toHaveBeenCalled();
  });

  it("source default → 首启持久化：PUT 首帧显示的 en 一次，UI 不动", async () => {
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "default"));
    vi.mocked(putSettingsSection).mockResolvedValue(general("en", "override"));
    render(<App />);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "en" }]);
    expect(getState().language).toBe("en");
  });

  it("?lang=zh 一次性覆写：不读不写，UI 按 URL", async () => {
    window.history.replaceState(null, "", "/?lang=zh");
    setLanguage("zh"); // detectInitialLanguage 在模块加载时算过一次；这里模拟它按 ?lang= 得出的首帧值
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("en", "override"));
    render(<App />);
    await waitFor(() => expect(fetchBoard).toHaveBeenCalled());
    expect(fetchSettingsSection).not.toHaveBeenCalled();
    expect(putSettingsSection).not.toHaveBeenCalled();
    expect(getState().language).toBe("zh");
  });

  it("水合读失败（离线）→ 留着首帧提示", async () => {
    vi.mocked(fetchSettingsSection).mockRejectedValue(new Error("offline"));
    render(<App />);
    await waitFor(() => expect(fetchSettingsSection).toHaveBeenCalledTimes(1));
    expect(getState().language).toBe("en");
  });

  it("壳在场：水合换了语言 → 壳再收到一次 setLanguage（悬浮窗 / 通知跟随，§61.1）", async () => {
    const postMessage = vi.fn().mockResolvedValue(SHELL_STATE);
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    vi.mocked(fetchSettingsSection).mockResolvedValue(general("zh", "override"));
    render(<App />);
    await waitFor(() => expect(getState().language).toBe("zh"));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ method: "setLanguage", lang: "zh" }));
    const langCalls = postMessage.mock.calls.map((c) => c[0]).filter((m) => m.method === "setLanguage").map((m) => m.lang);
    expect(langCalls[langCalls.length - 1]).toBe("zh");
  });
});
