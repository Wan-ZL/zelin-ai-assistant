// detail/copyText 的三条路（每个复制入口——复制指令行 / 详情 CopyChip / 复制成稿——都从这走，不许各自直连
// navigator.clipboard；批次 review-running-card-fixes，gap board-cards-copy-draft-no-fallback）：
//   1) Clipboard API 在且成功 → true，不碰 execCommand；
//   2) Clipboard API 缺席（非 secure context）或 reject（权限拒绝）→ 退到 textarea + execCommand("copy")，返回它的布尔；
//   3) execCommand 也抛 / 返回 false → false，永不抛给调用方；兜底 textarea 用完即撤。
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./copyText";

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
  else delete (navigator as { clipboard?: unknown }).clipboard;
  vi.restoreAllMocks();
  delete (document as { execCommand?: unknown }).execCommand;
});

describe("copyText", () => {
  it("Clipboard API 成功 → true，不走 execCommand", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    const exec = vi.fn().mockReturnValue(true);
    (document as { execCommand?: unknown }).execCommand = exec;
    await expect(copyText("hello")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(exec).not.toHaveBeenCalled();
  });

  it("Clipboard API 缺席（http 非 localhost 主机）→ execCommand 兜底，值经临时 textarea 选中后复制、textarea 撤掉", async () => {
    setClipboard(undefined);
    const exec = vi.fn().mockImplementation(() => {
      const ta = document.querySelector("textarea");
      expect(ta?.value).toBe("draft body");
      expect(ta?.getAttribute("readonly")).toBe("");
      return true;
    });
    (document as { execCommand?: unknown }).execCommand = exec;
    await expect(copyText("draft body")).resolves.toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("Clipboard API reject（权限拒绝）→ 同样退到 execCommand，返回它的布尔", async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) });
    (document as { execCommand?: unknown }).execCommand = vi.fn().mockReturnValue(false);
    await expect(copyText("x")).resolves.toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("execCommand 抛（老 WebView 没有这个 API）→ false，不抛给调用方，兜底 textarea 也撤掉", async () => {
    setClipboard(undefined);
    // jsdom 默认没有 document.execCommand：调用即 TypeError，走 catch → false
    await expect(copyText("x")).resolves.toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
  });
});
