// 关于页「立即检查」三道守卫 + §79「立即升级」+ 卸载复制命令对话框（CONTRACT §26 / §68.6 / §79；parity 批次
// about-update-guards-uninstall-copy 的守卫半边原样）：
//   - 一进页就按 about.check_enabled 灰掉按钮并说「自动检查新版本已关闭」（不必等第一次点击的回执；旧 server 缺席 = 开）；
//   - 每次「立即检查」落地后冷却 10 s（不论成败），到点自动解锁；
//   - 「立即升级」= POST /api/update/install：回执 → 「已开始升级…进度在 <log>」；409 manual_upgrade / 501 upgrader_missing →
//     一句原因 + server 给的手动命令（带复制键），绝不打开 release 页；快照里 manual_upgrade = true 时不给按钮、直接给命令；
//   - 卸载 = 复制命令对话框（uninstall.sh 会做什么 + 默认保留什么 + `cd <repo> && bash uninstall.sh` + 复制）——页面不开 Terminal。
// v2.0.0：看板投影 update_available 变了就重拉（dashboard.json 退役）与 §56 自动部署话术（#309）的判例随之退役（全文见 tag full）。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchAbout, postUpdateCheck, postUpdateInstall } from "../../api";
import { copyText } from "../../copyText";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import type { AboutInfo } from "../../types";
import { AboutSection, CHECK_COOLDOWN_MS, manualCommand, uninstallCommand, upgradeRefusal, updateView } from "./AboutSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchHealth: vi.fn(), fetchAbout: vi.fn(), postUpdateCheck: vi.fn(), postUpdateInstall: vi.fn(), postSetupStep: vi.fn(),
  };
});

vi.mock("../../copyText", () => ({ copyText: vi.fn(async () => true) }));

const about: AboutInfo = { version: "1.0.7", home: "/h", repo: "/r", update_available: null, update_check: { checked_at: "2026-09-02T11:00:00Z", latest: "1.0.7" } };
const withUpdate: AboutInfo = { ...about, update_available: { current: "1.0.7", latest: "1.0.8", url: "https://rel/1.0.8" }, update_check: { checked_at: "2026-09-02T11:00:00Z", latest: "1.0.8" } };
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
  vi.mocked(postUpdateCheck).mockReset();
  vi.mocked(postUpdateInstall).mockReset();
  vi.mocked(copyText).mockClear();
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
});

describe("§79 upgrade now", () => {
  const text = (_zh: string, en: string) => en;

  it("manualCommand prefers the server's fix and otherwise spells the §79 transition command", () => {
    expect(manualCommand("git fetch --tags origin && git checkout v2.0.0 && bash install.sh", "2.0.0")).toBe("git fetch --tags origin && git checkout v2.0.0 && bash install.sh");
    expect(manualCommand(undefined, "1.0.8")).toBe("git fetch --tags origin && git checkout v1.0.8 && bash install.sh");
    expect(manualCommand(null, null)).toBe("git fetch --tags origin && git checkout v<latest> && bash install.sh");
  });

  it("upgradeRefusal: 409 manual_upgrade and 501 each give a reason and the manual command; other errors → null", () => {
    const conflict = new ApiError(409, { error: { code: "CONFLICT", message: "x", details: { reason: "manual_upgrade", latest: "2.0.0", fix: "FIX-409" } } });
    expect(upgradeRefusal(conflict, "1.0.8", text)).toEqual({ tone: "warning", line: expect.stringMatching(/v2\.0\.0 is a new major version/), command: "FIX-409" });
    const missing = new ApiError(501, { error: { code: "NOT_IMPLEMENTED", message: "x", details: { reason: "upgrader_missing", fix: "FIX-501" } } });
    expect(upgradeRefusal(missing, "1.0.8", text)).toEqual({ tone: "warning", line: expect.stringMatching(/no one-click upgrader yet/), command: "FIX-501" });
    expect(upgradeRefusal(new ApiError(409, { error: { code: "CONFLICT", message: "busy" } }), "1.0.8", text)).toBeNull();
    expect(upgradeRefusal(new Error("boom"), "1.0.8", text)).toBeNull();
  });

  it("the button posts /api/update/install and then says the upgrade started, the app reopens and where the log is", async () => {
    vi.mocked(fetchAbout).mockResolvedValue(withUpdate);
    vi.mocked(postUpdateInstall).mockResolvedValue({ ok: true, action: "upgrade", script: "scripts/upgrade.sh", log: "/h/state/upgrade.log", latest: "1.0.8" });
    renderIn("en", <AboutSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Update v1.0.8 available — upgrade now" }));
    await waitFor(() => expect(postUpdateInstall).toHaveBeenCalledTimes(1));
    expect((await screen.findByRole("status")).textContent).toMatch(/Upgrading to v1\.0\.8: the app reopens by itself.*recording is never interrupted.*\/h\/state\/upgrade\.log/);
  });

  it("501 (this checkout has no upgrader yet) shows the manual command with a Copy button; nothing opens a release page", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    vi.mocked(fetchAbout).mockResolvedValue(withUpdate);
    vi.mocked(postUpdateInstall).mockRejectedValue(new ApiError(501, { error: { code: "NOT_IMPLEMENTED", message: "no upgrader", details: { reason: "upgrader_missing", fix: "git fetch --tags origin && git checkout v1.0.8 && bash install.sh" } } }));
    renderIn("en", <AboutSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Update v1.0.8 available — upgrade now" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("This install has no one-click upgrader yet (scripts/upgrade.sh).");
    expect(alert.textContent).toContain("git fetch --tags origin && git checkout v1.0.8 && bash install.sh");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(copyText).toHaveBeenCalledWith("git fetch --tags origin && git checkout v1.0.8 && bash install.sh"));
    expect(open).not.toHaveBeenCalled();
  });

  it("a snapshot that already says manual_upgrade (new major) shows the command instead of the button", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...withUpdate, update_available: { current: "1.0.7", latest: "2.0.0", url: "https://rel/2.0.0", manual_upgrade: true }, update_check: { checked_at: "2026-09-02T11:00:00Z", latest: "2.0.0" } });
    renderIn("en", <AboutSection />);
    expect(await screen.findByText(/v2\.0\.0 is a new major version/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /upgrade now/ })).toBeNull();
    expect(screen.getByText("git fetch --tags origin && git checkout v2.0.0 && bash install.sh")).toBeTruthy();
  });
});

describe("Uninstall = a copy-command dialog (no Terminal is opened for you)", () => {
  it("uninstallCommand names the real repo when the snapshot has it", () => {
    expect(uninstallCommand(about)).toBe("cd /r && bash uninstall.sh");
    expect(uninstallCommand(null)).toBe("cd <repo> && bash uninstall.sh");
  });

  for (const [language, open, intro, kept] of [
    ["zh", "卸载…", "在 Terminal 里运行下面这条命令。脚本会逐条显示要做的事，动手前再确认一次：", "默认保留：state/（会议纪要与笔记整理记录）、API 密钥、Obsidian vault、屏幕录像——每一项都会附上删除命令。"],
    ["en", "Uninstall…", "Run the command below in Terminal. The script lists each step and asks once more before doing anything:", "Kept by default: state/ (recaps and note-processing records), API keys, your Obsidian vault, screen recordings — each listed with its removal command."],
  ] as const) {
    it(`${language}: intro + three bullets + kept-by-default line + the command with Copy`, async () => {
      renderIn(language, <AboutSection />);
      fireEvent.click(await screen.findByRole("button", { name: open }));
      const dialog = await screen.findByRole("dialog", { hidden: true });
      expect(screen.getByText(intro)).toBeTruthy();
      expect(dialog.querySelectorAll("ul.dialog-list li")).toHaveLength(3);
      expect(screen.getByText(kept)).toBeTruthy();
      expect(screen.getByText("cd /r && bash uninstall.sh")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: language === "zh" ? "复制" : "Copy", hidden: true }));
      await waitFor(() => expect(copyText).toHaveBeenCalledWith("cd /r && bash uninstall.sh"));
    });
  }
});
