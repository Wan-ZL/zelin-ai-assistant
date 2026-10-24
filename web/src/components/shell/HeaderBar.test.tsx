// 顶栏行为测试（G7）：主题切换（dataset + localStorage）、语言切换（store.chooseLanguage：store 翻转 + zai.lang 首帧缓存；
// PUT 半边见 LanguageToggle.persist.test.tsx）、连接点（词随 SSE 状态）、三档密度（§49 追记 2026-09-04：data-density 反映
// 档位；tight 壳开关只留图标（title 说全「录制：状态词」）、标题挂 title 全名）。jsdom 无 ResizeObserver → 不给 prop 时恒 full。
// 页面入口在左侧导航栏——判例在 NavRail.test.tsx（§54.4）。v2.0.0 退役：看板新鲜度、§56 部署状态小字、设备标签、
// 搜索 / 过滤槽位、实时字幕开关（全文见 tag full）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { getState, resetStoreForTests, setConnection } from "../../store";
import { HeaderBar, type HeaderBarProps } from "./HeaderBar";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  // putSettingsSection：D37 起顶栏切换还 PUT general.language（判例在 LanguageToggle.persist.test.tsx；这里只要它不打真网络）
  return { ...mod, putSettingsSection: vi.fn().mockResolvedValue({ id: "general", fields: [] }) };
});

function renderHeader(language: "zh" | "en" = "en", props: HeaderBarProps = {}) {
  return render(
    <LanguageContext.Provider value={language}>
      <HeaderBar {...props} />
    </LanguageContext.Provider>,
  );
}

function shellState(): ShellState {
  return {
    recording: {
      available: true, on: false, mode: "off", engine_running: false, diagnosis: null,
      note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen",
    },
    permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
    launch_at_login: false,
    language: "en",
  };
}

describe("HeaderBar", () => {
  beforeEach(() => {
    resetStoreForTests();
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  afterEach(() => {
    cleanup();
  });

  it("主题切换：写 dataset.theme + localStorage zai.theme，往返翻转", () => {
    renderHeader();
    const toggle = screen.getByRole("button", { name: "Toggle dark mode" });
    fireEvent.click(toggle); // jsdom 无 matchMedia → 初始按 light，切到 dark
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("zai.theme")).toBe("dark");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("zai.theme")).toBe("light");
  });

  it("语言切换：store.language 翻转 + 首帧缓存 zai.lang", () => {
    renderHeader("zh");
    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));
    expect(getState().language).toBe("en");
    expect(window.localStorage.getItem("zai.lang")).toBe("en");
  });

  it("连接点：词与 class 随 SSE 状态（连接中 / 已连接 / 重连中）", () => {
    renderHeader("en");
    const connection = () => document.querySelector(".shell-connection")!;
    expect(connection().textContent).toBe("Connecting");
    expect(connection().getAttribute("title")).toBe("Connecting");
    act(() => setConnection("live"));
    expect(connection().className).toContain("is-live");
    expect(connection().textContent).toBe("Live");
    act(() => setConnection("reconnecting"));
    expect(connection().className).toContain("is-reconnecting");
    expect(connection().getAttribute("title")).toBe("Reconnecting");
  });

  it("左翼只有标识 + 标题：没有新鲜度 / 部署 / 设备小字，也没有搜索槽位", () => {
    renderHeader("en");
    expect(document.querySelector(".shell-freshness")).toBeNull();
    expect(document.querySelector(".shell-deploy")).toBeNull();
    expect(document.querySelector(".shell-device")).toBeNull();
    expect(document.querySelector(".shell-search-slot")).toBeNull();
  });
});

describe("HeaderBar 三档密度（§49 追记 2026-09-04）", () => {
  beforeEach(() => {
    resetStoreForTests();
  });

  afterEach(() => {
    cleanup();
    delete window.webkit;
    resetShellBridgeForTests();
  });

  it("jsdom 没有 ResizeObserver：不给 prop 时 data-density=full；给了就按给的", () => {
    renderHeader();
    expect(screen.getByRole("banner").getAttribute("data-density")).toBe("full");
    cleanup();
    renderHeader("en", { density: "compact" });
    expect(screen.getByRole("banner").getAttribute("data-density")).toBe("compact");
  });

  it("tight + 壳桥：录制开关保留 aria-label，title 说全「Rec: Off」（文字由 CSS 收起）；字幕开关不再出现", async () => {
    const state = shellState();
    window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn(async () => state) } } };
    renderHeader("en", { density: "tight" });
    const rec = await screen.findByRole("button", { name: "Recording controls" });
    expect(rec.getAttribute("title")).toBe("Rec: Off");
    expect(rec.textContent).toContain("Off"); // 文案节点仍在（判卷面按节点找），只是 tight 档 CSS 不显示
    expect(screen.queryByRole("button", { name: "Live captions" })).toBeNull();
    cleanup();
    renderHeader("en", { density: "full" });
    expect((await screen.findByRole("button", { name: "Recording controls" })).getAttribute("title")).toBe("Recording controls");
  });

  it("tight：标题挂 title 全名（极窄时省略号的 tooltip，双语）；full / compact 标题不缩、不挂", () => {
    renderHeader("en", { density: "tight" });
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.className).toBe("shell-title");
    expect(heading.textContent).toBe("Zelin's AI Assistant");
    expect(heading.getAttribute("title")).toBe("Zelin's AI Assistant");
    cleanup();
    renderHeader("zh", { density: "tight" });
    expect(screen.getByRole("heading", { level: 1 }).getAttribute("title")).toBe("Zelin 的 AI 助理");
    cleanup();
    renderHeader("zh", { density: "compact" });
    expect(screen.getByRole("heading", { level: 1 }).hasAttribute("title")).toBe(false);
    cleanup();
    renderHeader("zh");
    expect(screen.getByRole("heading", { level: 1 }).hasAttribute("title")).toBe(false);
  });
});
