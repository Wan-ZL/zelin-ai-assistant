// App 的壳命令接线（CONTRACT §68.13 quick_capture → §54.4 2026-09-05 追记 focusComposer）：壳的全局 ⌃⌥Space /
// 壳菜单 View ▸ 聚焦捕获框（⌘L）都 dispatch `zai-shell-command` {command:"quick_capture"}，App 必须把它交给
// focusComposer——与 rail 的 ⌘L 同一落点（原生 AppDelegate.swift focusCaptureField 的 web 版）。钉的是接线：命令到 →
// 调一次；别的命令 / 坏 detail 不调；App 卸载后监听器摘掉。api / realtime 全 mock，壳桥用假 messageHandler 顶着。
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./app";
import { focusComposer } from "./components/board/focusComposer";
import { resetShellBridgeForTests, SHELL_COMMAND_EVENT } from "./shellBridge";
import { resetStoreForTests } from "./store";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  const offline = () => vi.fn().mockRejectedValue(new Error("offline"));
  return {
    ...actual,
    fetchBoard: offline(), fetchHealth: offline(), fetchLanes: offline(), fetchDisplaySettings: offline(), fetchSetup: offline(),
  };
});

vi.mock("./realtime", () => ({
  createBoardRealtime: () => ({ start: vi.fn(), stop: vi.fn() }),
}));

vi.mock("./route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./route")>();
  return { ...actual, navigate: vi.fn() };
});

vi.mock("./components/board/focusComposer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./components/board/focusComposer")>();
  return { ...actual, focusComposer: vi.fn() };
});

function shellCommand(detail: unknown) {
  act(() => {
    window.dispatchEvent(new CustomEvent(SHELL_COMMAND_EVENT, { detail }));
  });
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  resetShellBridgeForTests();
  vi.mocked(focusComposer).mockReset();
  // 壳在场（onShellCommand 只在 hasShellBridge() 时挂监听）；setBadge / getState 都回空快照
  window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn().mockResolvedValue({}) } } };
});

afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("App — 壳的 quick_capture 命令交给 focusComposer", () => {
  it("quick_capture 到 → focusComposer 调一次；别的命令 / 没有 command 的 detail 不调", () => {
    render(<App />);
    expect(focusComposer).not.toHaveBeenCalled();
    shellCommand({ command: "quick_capture" });
    expect(focusComposer).toHaveBeenCalledTimes(1);
    shellCommand({ command: "toggle_recording" });
    shellCommand({});
    shellCommand(undefined);
    expect(focusComposer).toHaveBeenCalledTimes(1);
    shellCommand({ command: "quick_capture" });
    expect(focusComposer).toHaveBeenCalledTimes(2);
  });

  it("App 卸载后监听器摘掉：再推 quick_capture 不再调", () => {
    const view = render(<App />);
    shellCommand({ command: "quick_capture" });
    expect(focusComposer).toHaveBeenCalledTimes(1);
    view.unmount();
    shellCommand({ command: "quick_capture" });
    expect(focusComposer).toHaveBeenCalledTimes(1);
  });
});
