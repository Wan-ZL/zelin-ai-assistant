// 关于页「立即检查」三道守卫 + 投影变更重拉 + 卸载确认全文（CONTRACT §26 / §68.6 2026-09-05 追记；parity 批次
// about-update-guards-uninstall-copy）——原生 Pages.swift UpdateCheckModel / confirmUninstall 的行为：
//   - 一进页就按 about.check_enabled 灰掉按钮并说「自动检查新版本已关闭」（不必等第一次点击的回执；旧 server 缺席 = 开）；
//   - 每次「立即检查」落地后冷却 10 s（不论成败），到点自动解锁；
//   - 看板投影 update_available 变了 → 重拉 /api/about；
//   - 卸载确认正文 = 会做的三件事（• 列表）+ 默认保留什么，zh/en 逐字原生（第三条点名 Zelin's AI Assistant.app）。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAbout, fetchBoard, postUpdateCheck } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests } from "../../shellBridge";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { AboutInfo, Board } from "../../types";
import { AboutSection, CHECK_COOLDOWN_MS, projectedLatest, updateView } from "./AboutSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(), fetchHealth: vi.fn(), fetchAbout: vi.fn(), postUpdateCheck: vi.fn(),
    postUpdateInstall: vi.fn(), postUninstallTerminal: vi.fn(), postSetupStep: vi.fn(),
  };
});

const about: AboutInfo = { version: "1.0.7", home: "/h", repo: "/r", update_available: null, update_check: { checked_at: "2026-09-02T11:00:00Z", latest: "1.0.7" } };
const NOW = Date.parse("2026-09-02T12:00:00Z");
const renderIn = (language: "zh" | "en", node: React.ReactNode) => render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  // 假 Date + 假 setTimeout（冷却计时器），但让时间随真实时间前进——RTL 的 waitFor / findBy 内部也用 setTimeout
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(NOW));
  vi.mocked(fetchAbout).mockReset();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(postUpdateCheck).mockReset();
  vi.mocked(fetchAbout).mockResolvedValue(about);
  window.history.replaceState(null, "", "/?page=about");
  delete (window as Window & { webkit?: unknown }).webkit;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("updateView seeds enabled from the server (native reload: override → config → on)", () => {
  it("about.check_enabled false → enabled false before any click; absent → true; the check receipt still wins", () => {
    expect(updateView({ ...about, check_enabled: false }, null).enabled).toBe(false);
    expect(updateView(about, null).enabled).toBe(true);
    expect(updateView({ ...about, check_enabled: true }, null).enabled).toBe(true);
    // 原生 finish(): enabled = obj["enabled"] ?? enabled —— 回执没带 enabled 就沿用快照的
    expect(updateView({ ...about, check_enabled: false }, { ok: true, latest: null }).enabled).toBe(false);
    expect(updateView({ ...about, check_enabled: false }, { ok: true, enabled: true, latest: "1.0.7" }).enabled).toBe(true);
  });

  it("projectedLatest reads dashboard.update_available.latest and tolerates the unknown shape", () => {
    expect(projectedLatest(null)).toBeNull();
    expect(projectedLatest({ update_available: undefined } as unknown as Board)).toBeNull();
    expect(projectedLatest({ update_available: "junk" } as unknown as Board)).toBeNull();
    expect(projectedLatest({ update_available: { latest: 3 } } as unknown as Board)).toBeNull();
    expect(projectedLatest({ update_available: { latest: "1.0.8", url: "https://rel" } } as unknown as Board)).toBe("1.0.8");
  });
});

describe("AboutSection Check now guards (native .disabled(checking || cooldown || !enabled))", () => {
  it("auto-check off: the sentence shows immediately and Check now is disabled without any click", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, check_enabled: false });
    renderIn("zh", <AboutSection />);
    expect(await screen.findByText("自动检查新版本已关闭——到「设置」页可重新开启。")).toBeTruthy();
    const button = screen.getByRole("button", { name: "立即检查" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(postUpdateCheck).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(postUpdateCheck).not.toHaveBeenCalled();
  });

  it("auto-check on (or an old server without the field): Check now is enabled and the sentence is absent", async () => {
    renderIn("en", <AboutSection />);
    expect(await screen.findByText("Up to date")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Check now" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(/Automatic update checks are off/)).toBeNull();
  });

  it("after a check settles the button stays disabled for 10 s, then unlocks (success and failure alike)", async () => {
    vi.mocked(postUpdateCheck).mockResolvedValue({ ok: true, enabled: true, current: "1.0.7", latest: "1.0.7", update_available: false, checked_at: "2026-09-02T12:00:00Z" });
    renderIn("en", <AboutSection />);
    const button = await screen.findByRole("button", { name: "Check now" }) as HTMLButtonElement;
    fireEvent.click(button);
    await waitFor(() => expect(postUpdateCheck).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText("Checking…")).toBeNull());
    expect(button.disabled).toBe(true);   // 冷却中——已不是「正在检查」，仍不许再点
    await act(async () => { vi.advanceTimersByTime(CHECK_COOLDOWN_MS - 1000); });
    expect(button.disabled).toBe(true);
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(button.disabled).toBe(false);
    expect(CHECK_COOLDOWN_MS).toBe(10_000);

    // 失败一样冷却（原生 finish() 不看结果）
    vi.mocked(postUpdateCheck).mockRejectedValue(new Error("boom"));
    fireEvent.click(button);
    await waitFor(() => expect(postUpdateCheck).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/boom/));
    expect(button.disabled).toBe(true);
    await act(async () => { vi.advanceTimersByTime(CHECK_COOLDOWN_MS); });
    expect(button.disabled).toBe(false);
  });

  it("a fresh board projection (update_available changed) re-reads /api/about; an unchanged one does not", async () => {
    renderIn("en", <AboutSection />);
    await screen.findByText("Up to date");
    await waitFor(() => expect(fetchAbout).toHaveBeenCalledTimes(1));
    // 同一投影（没有 latest）→ 不重拉
    vi.mocked(fetchBoard).mockResolvedValue({ generated_at: "x", counts: {} } as unknown as Board);
    await act(async () => { await refreshBoard(); });
    expect(fetchAbout).toHaveBeenCalledTimes(1);
    // actd 一个 pass 落了新版投影 → 重拉，关于行跟着变
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, update_available: { latest: "1.0.8", url: "https://rel/1.0.8" }, update_check: { checked_at: "2026-09-02T12:00:00Z", latest: "1.0.8" } });
    vi.mocked(fetchBoard).mockResolvedValue({ generated_at: "y", counts: {}, update_available: { latest: "1.0.8", url: "https://rel/1.0.8" } } as unknown as Board);
    await act(async () => { await refreshBoard(); });
    await waitFor(() => expect(fetchAbout).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: "Update v1.0.8 available — install now" })).toBeTruthy();
  });
});

describe("Uninstall confirmation body mirrors the native informativeText", () => {
  const ZH = {
    intro: "将执行以下操作（在 Terminal 中逐条显示，动手前再确认一次）：",
    bullets: [
      "停止并移除全部后台服务（AI 派发、屏幕录制、雷达、定时任务）",
      "从 crontab 移除本产品的行（你的其他行原样保留）",
      "退出看板 app，删除 /Applications 里的 Zelin's AI Assistant.app 与系统级管线副本",
    ],
    kept: "默认保留：任务历史（state/）、API 密钥、Obsidian vault、屏幕录像——每一项都会附上删除命令。",
  };
  const EN = {
    intro: "What will happen (each step shown in Terminal, with one final confirmation there):",
    bullets: [
      "Stop and remove every background service (AI dispatch, screen recording, radars, scheduled jobs)",
      "Remove this product's lines from your crontab (all your other lines kept)",
      "Quit the board app, delete Zelin's AI Assistant.app in /Applications and the system-level pipeline copy",
    ],
    kept: "Kept by default: task history (state/), API keys, your Obsidian vault, screen recordings — each listed with its removal command.",
  };

  for (const [language, copy, open, confirm] of [["zh", ZH, "卸载…", "在 Terminal 中卸载…"], ["en", EN, "Uninstall…", "Uninstall in Terminal…"]] as const) {
    it(`${language}: intro line + three bullets + kept-by-default line, then the Terminal button`, async () => {
      renderIn(language, <AboutSection />);
      fireEvent.click(await screen.findByRole("button", { name: open }));
      const dialog = await screen.findByRole("dialog", { hidden: true });
      expect(screen.getByText(copy.intro)).toBeTruthy();
      const items = Array.from(dialog.querySelectorAll("ul.dialog-list li")).map((li) => li.textContent);
      expect(items).toEqual(copy.bullets);
      expect(screen.getByText(copy.kept)).toBeTruthy();
      expect(screen.getByRole("button", { name: confirm, hidden: true })).toBeTruthy();
    });
  }
});
