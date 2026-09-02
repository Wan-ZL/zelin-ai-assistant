// 壳内原生开关判例（CONTRACT §61.2）：桥在/不在的渲染门、录制按钮的文案/颜色三态
// （镜像 mac RecordingMenuButton）、三态单选菜单、实时字幕四态、乐观 UI + 桥 reject
// 回滚、真相追平/拒绝说明退场乐观值、zai-shell-state 推送更新、语言同步。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests, SHELL_STATE_EVENT, type ShellState } from "../../shellBridge";
import { ShellControls } from "./ShellControls";

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeState(overrides: {
  recording?: Partial<ShellState["recording"]>;
  captions?: Partial<ShellState["captions"]>;
} = {}): ShellState {
  return {
    recording: {
      available: true, on: false, mode: "off", engine_running: false, diagnosis: null,
      note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen",
      ...overrides.recording,
    },
    captions: {
      available: true, on: false, engine: "auto", paused: false, engine_dead: false,
      status_text: "", status_is_error: false,
      ...overrides.captions,
    },
    language: "en",
  };
}

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();

function installBridge(initial: ShellState) {
  postMessage.mockReset();
  postMessage.mockImplementation(async (body: unknown) => {
    const { method } = body as { method: string };
    if (method === "getState" || method === "setLanguage") return initial;
    return initial;
  });
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
}

function uninstallBridge() {
  delete window.webkit;
}

function pushState(state: ShellState) {
  act(() => {
    window.dispatchEvent(new CustomEvent(SHELL_STATE_EVENT, { detail: state }));
  });
}

function renderControls(language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <ShellControls />
    </LanguageContext.Provider>,
  );
}

const recButton = () => screen.getByRole("button", { name: "Recording controls" });
const capButton = () => screen.getByRole("button", { name: "Live captions" });

describe("ShellControls", () => {
  beforeEach(() => {
    resetShellBridgeForTests();
  });

  afterEach(() => {
    cleanup();
    uninstallBridge();
    vi.useRealTimers();
  });

  it("普通浏览器（无 zaiShell handler）：整组不渲染，也不调任何桥", () => {
    uninstallBridge();
    const { container } = renderControls();
    expect(container.querySelector(".shell-native-controls")).toBeNull();
    expect(screen.queryByRole("button", { name: "Recording controls" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Live captions" })).toBeNull();
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("壳里：挂载即 getState + setLanguage，两个开关按快照渲染（关态：次级色、Rec: Off）", async () => {
    installBridge(makeState());
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    expect(postMessage).toHaveBeenCalledWith({ method: "getState" });
    expect(postMessage).toHaveBeenCalledWith({ method: "setLanguage", lang: "en" });
    expect(recButton().className).toBe("shell-rec-button is-off");
    expect(recButton().textContent).toContain("Rec: Off");
    expect(capButton().getAttribute("aria-pressed")).toBe("false");
    expect(capButton().textContent).toBe("Live captions");
  });

  it("中文文案镜像原生：录制：关 / 实时字幕；语言同步发 zh", async () => {
    installBridge(makeState());
    renderControls("zh");
    await waitFor(() => expect(screen.getByRole("button", { name: "录制控制" })).toBeTruthy());
    expect(screen.getByRole("button", { name: "录制控制" }).textContent).toContain("录制：关");
    expect(screen.getByRole("button", { name: "实时字幕" }).textContent).toBe("实时字幕");
    expect(postMessage).toHaveBeenCalledWith({ method: "setLanguage", lang: "zh" });
  });

  it("录制三色：引擎在录=红 is-live + 模式词；开了没录上=橙 is-warn + 未在录制", async () => {
    installBridge(makeState({ recording: { on: true, mode: "screen", engine_running: true } }));
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    expect(recButton().className).toBe("shell-rec-button is-live");
    expect(recButton().textContent).toContain("Rec: Screen only");

    pushState(makeState({ recording: { on: true, mode: "screen_audio", engine_running: true } }));
    expect(recButton().textContent).toContain("Rec: Screen + audio");

    pushState(makeState({ recording: { on: true, mode: "screen", engine_running: false, diagnosis: "engine_dead" } }));
    expect(recButton().className).toBe("shell-rec-button is-warn");
    expect(recButton().textContent).toContain("Rec: Not recording");
  });

  it("菜单：三态单选（原生标签 + 当前项 checked）、重启项在关态禁用；引擎死了首行说真实原因", async () => {
    installBridge(makeState({
      recording: { on: true, mode: "screen", engine_running: false, screen_permission: false },
    }));
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    fireEvent.click(recButton());
    const menu = screen.getByRole("menu", { name: "Recording controls" });
    expect(menu.textContent).toContain("Not recording — missing Screen Recording permission");
    const radios = screen.getAllByRole("menuitemradio");
    expect(radios.map((r) => r.textContent?.replace("✓", "").trim())).toEqual(["Off", "Screen only", "Screen + audio"]);
    expect(radios[1].getAttribute("aria-checked")).toBe("true");
    expect(radios[0].getAttribute("aria-checked")).toBe("false");
    expect((screen.getByRole("menuitem", { name: "Restart recording engine" }) as HTMLButtonElement).disabled).toBe(false);
    // 缺权限 → 系统设置深链项，点它走桥
    fireEvent.click(screen.getByRole("menuitem", { name: "Open System Settings → Screen Recording" }));
    expect(postMessage).toHaveBeenCalledWith({ method: "openScreenRecordingSettings" });
    expect(screen.queryByRole("menu")).toBeNull(); // 点完收起
  });

  it("关态下「重启录制引擎」禁用（无引擎可重启）", async () => {
    installBridge(makeState());
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    fireEvent.click(recButton());
    expect((screen.getByRole("menuitem", { name: "Restart recording engine" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("选模式 = 乐观显示目标 + 重启中…，桥收到 setRecording{on,mode}；回执追平即定格", async () => {
    installBridge(makeState());
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    const pending = deferred<unknown>();
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setRecording") return pending.promise;
      return makeState();
    });
    fireEvent.click(recButton());
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Screen \+ audio/ }));
    expect(postMessage).toHaveBeenCalledWith({ method: "setRecording", on: true, mode: "screen_audio" });
    // 还没回执：乐观显示目标模式（橙 = 引擎在起）+ 重启中…
    expect(recButton().textContent).toContain("Rec: Screen + audio");
    expect(recButton().textContent).toContain("restarting…");
    expect(recButton().className).toBe("shell-rec-button is-warn");
    await act(async () => {
      pending.resolve(makeState({ recording: { on: true, mode: "screen_audio", engine_running: true } }));
    });
    expect(recButton().textContent).toContain("Rec: Screen + audio");
    expect(recButton().className).toBe("shell-rec-button is-live");
  });

  it("桥 reject → 回滚到原状态，reject 原文挂在按钮 title 与菜单里", async () => {
    installBridge(makeState({ recording: { on: true, mode: "screen", engine_running: true } }));
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setRecording") throw "INVALID_ARGS: mode must be one of screen|screen_audio";
      return makeState({ recording: { on: true, mode: "screen", engine_running: true } });
    });
    fireEvent.click(recButton());
    fireEvent.click(screen.getByRole("menuitemradio", { name: /^Off$/ }));
    expect(recButton().textContent).toContain("Rec: Off"); // 乐观
    await waitFor(() => expect(recButton().textContent).toContain("Rec: Screen only")); // 回滚
    expect(recButton().getAttribute("title")).toContain("INVALID_ARGS");
    fireEvent.click(recButton());
    expect(screen.getByRole("menu").textContent).toContain("INVALID_ARGS: mode must be one of");
  });

  it("屏幕+音频预检：回执 mode 仍是旧值时乐观值保留，壳推送拒绝说明（note）后回到真相并显示说明", async () => {
    const before = makeState({ recording: { on: true, mode: "screen", engine_running: true } });
    installBridge(before);
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    postMessage.mockImplementation(async () => before); // 预检还在跑：回执里 mode 未变
    fireEvent.click(recButton());
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Screen \+ audio/ }));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ method: "setRecording", on: true, mode: "screen_audio" }));
    expect(recButton().textContent).toContain("Rec: Screen + audio"); // 乐观仍在
    pushState(makeState({
      recording: { on: true, mode: "screen", engine_running: true, note: "「屏幕+音频」需要 ffmpeg…" },
    }));
    expect(recButton().textContent).toContain("Rec: Screen only");
    expect(recButton().getAttribute("title")).toBe("「屏幕+音频」需要 ffmpeg…");
    fireEvent.click(recButton());
    expect(screen.getByRole("menu").textContent).toContain("「屏幕+音频」需要 ffmpeg…");
  });

  it("重启录制引擎 → restartRecording + 重启中…", async () => {
    installBridge(makeState({ recording: { on: true, mode: "screen", engine_running: true } }));
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    fireEvent.click(recButton());
    fireEvent.click(screen.getByRole("menuitem", { name: "Restart recording engine" }));
    expect(postMessage).toHaveBeenCalledWith({ method: "restartRecording" });
    expect(recButton().textContent).toContain("restarting…");
  });

  it("实时字幕：点击乐观翻转 + setCaptions{on}；回执追平；出错/暂停两态文案与颜色", async () => {
    installBridge(makeState());
    renderControls();
    await waitFor(() => expect(capButton()).toBeTruthy());
    const pending = deferred<unknown>();
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setCaptions") return pending.promise;
      return makeState();
    });
    fireEvent.click(capButton());
    expect(postMessage).toHaveBeenCalledWith({ method: "setCaptions", on: true });
    expect(capButton().getAttribute("aria-pressed")).toBe("true"); // 乐观
    expect(capButton().className).toBe("shell-cap-button is-on");
    await act(async () => {
      pending.resolve(makeState({ captions: { on: true } }));
    });
    expect(capButton().getAttribute("aria-pressed")).toBe("true");

    pushState(makeState({ captions: { on: true, paused: true } }));
    expect(capButton().textContent).toBe("Live captions (paused)");
    pushState(makeState({ captions: { on: true, engine_dead: true, status_text: "bad key", status_is_error: true } }));
    expect(capButton().textContent).toBe("Live captions (error — see overlay)");
    expect(capButton().className).toBe("shell-cap-button is-warn");
    expect(capButton().getAttribute("title")).toBe("bad key");
  });

  it("实时字幕：桥 reject → aria-pressed 回滚、title 带 reject 原文", async () => {
    installBridge(makeState({ captions: { on: true } }));
    renderControls();
    await waitFor(() => expect(capButton()).toBeTruthy());
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setCaptions") throw new Error("INTERNAL: boom");
      return makeState({ captions: { on: true } });
    });
    fireEvent.click(capButton());
    expect(capButton().getAttribute("aria-pressed")).toBe("false");
    await waitFor(() => expect(capButton().getAttribute("aria-pressed")).toBe("true"));
    expect(capButton().getAttribute("title")).toBe("INTERNAL: boom");
  });

  it("引擎 available:false 时对应开关不渲染（未来非 mac 壳）", async () => {
    installBridge(makeState({ captions: { available: false } }));
    renderControls();
    await waitFor(() => expect(recButton()).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Live captions" })).toBeNull();
  });
});
