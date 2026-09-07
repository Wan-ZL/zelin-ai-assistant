// 壳 → 页面命令的回执（CONTRACT §54.4 / §68.13 2026-09-06 追记，D40）：壳发 cancelable 的 zai-shell-command，页面的 onShellCommand
// 监听器接到就 preventDefault → 壳侧 `dispatchEvent` 回 false = 「有人接」；没挂监听（文档还在加载 / React 树没起来 / 老 web 构建
// 没有这个词）→ true = 没人接，壳退回整页加载深链（ShellBridge.pushCommand completion）。老壳发的事件不 cancelable，preventDefault
// 是 no-op（dispatchEvent 照旧回 true），handler 仍然被叫。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { onShellCommand, resetShellBridgeForTests, SHELL_COMMAND_EVENT } from "./shellBridge";

/** 壳 ShellBridge.commandScript 的那句 JS：`!window.dispatchEvent(new CustomEvent(name, {detail, cancelable: true}))` */
function shellDispatch(detail: unknown, cancelable = true): boolean {
  return !window.dispatchEvent(new CustomEvent(SHELL_COMMAND_EVENT, { detail, cancelable }));
}

beforeEach(() => {
  resetShellBridgeForTests();
  window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn().mockResolvedValue({}) } } };
});

afterEach(() => {
  delete window.webkit;
});

describe("shellBridge — command ack", () => {
  it("a listening page acknowledges (handled = true) and receives command + args; after stop nobody acknowledges", () => {
    const handler = vi.fn();
    const stop = onShellCommand(handler);
    expect(shellDispatch({ command: "open_page", page: "settings", anchor: "live_captions" })).toBe(true);
    expect(handler).toHaveBeenCalledWith("open_page", { page: "settings", anchor: "live_captions" });
    expect(shellDispatch({ command: "quick_capture" })).toBe(true);
    expect(handler).toHaveBeenLastCalledWith("quick_capture", {});
    stop();
    expect(shellDispatch({ command: "open_page", page: "about" })).toBe(false); // 没人接：壳退回整页加载
    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("a malformed detail (no string command) is neither handled nor acknowledged", () => {
    const handler = vi.fn();
    const stop = onShellCommand(handler);
    expect(shellDispatch({ page: "about" })).toBe(false);
    expect(shellDispatch(null)).toBe(false);
    expect(handler).not.toHaveBeenCalled();
    stop();
  });

  it("an old shell's non-cancelable event still reaches the handler (preventDefault is a no-op there)", () => {
    const handler = vi.fn();
    const stop = onShellCommand(handler);
    expect(shellDispatch({ command: "quick_capture" }, false)).toBe(false); // 不 cancelable：dispatchEvent 恒 true
    expect(handler).toHaveBeenCalledWith("quick_capture", {});
    stop();
  });
});
