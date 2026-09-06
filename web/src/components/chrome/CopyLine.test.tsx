// CopyLine（原生 Cards.swift CopyPathLine 的 web 版；§68.8 横幅「手动命令：」）：label 独占节点、值住 <code>、
// 「复制」→ 剪贴板 → 「已复制」1.5 s + role=status 播报；写不进剪贴板时按钮文案不变（不假报已复制）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { CopyLine } from "./CopyLine";

function renderLine(language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <CopyLine label={language === "zh" ? "手动命令：" : "Manual command: "} value="launchctl kickstart -k gui/$(id -u)/x" />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("<CopyLine>", () => {
  it("label, <code> value and a Copy chip; the chip writes the value and reads 已复制 for 1.5 s", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderLine();
    expect(screen.getByText("Manual command:")).toBeTruthy();
    const code = screen.getByText("launchctl kickstart -k gui/$(id -u)/x");
    expect(code.tagName).toBe("CODE");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await act(async () => { await Promise.resolve(); });
    expect(writeText).toHaveBeenCalledWith("launchctl kickstart -k gui/$(id -u)/x");
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Copied to clipboard");
    await act(async () => { await vi.advanceTimersByTimeAsync(1499); });
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("zh: 复制 → 已复制", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true });
    renderLine("zh");
    expect(screen.getByText("手动命令：")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("button", { name: "已复制" })).toBeTruthy();
  });

  it("a clipboard that refuses (and no execCommand) leaves the chip on Copy — no false 已复制", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    const execCommand = document.execCommand;
    document.execCommand = vi.fn().mockReturnValue(false);
    try {
      renderLine();
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });
      expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
      expect(screen.getByRole("status").textContent).toBe("");
    } finally {
      document.execCommand = execCommand;
    }
  });
});
