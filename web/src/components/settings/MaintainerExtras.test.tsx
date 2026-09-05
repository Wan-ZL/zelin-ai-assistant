// 开发者 · 开发会话 的动作行（§68.7 追记；原生 SettingsMaintainer.swift:215-226, 330-331）：
// 帮助句带 server 算的终端名（只读目录；「通用」换终端后的跟随在 TerminalAppNameRefresh.test）；成功句是原生整句（不再是命令原文）；open 失败 = 原生句 + 可复制命令；
// 400 会话 id 不合形状 → 目录 check 里的那句（按 UI 语言）；路径不存在 → 「路径不存在」；老 server 缺终端名 → 泛称。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSettingsCatalog, postMaintainerTerminal } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { refreshSettingsCatalog, resetStoreForTests } from "../../store";
import type { SettingsCatalog, SettingsSection, TerminalReceipt } from "../../types";
import { MaintainerExtras } from "./MaintainerExtras";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), postMaintainerTerminal: vi.fn() };
});

const CHARSET = { zh: "会话 ID 只能包含字母、数字和连字符（-）——从 claude 里复制的会话 ID 就是这个样子。", en: "A session id may only contain letters, digits, and hyphens (-) — the id you copy from claude is exactly that shape." };
const HYPHEN = { zh: "会话 ID 不能以连字符（-）开头——那是命令行选项的形状，不是会话 ID。", en: "A session id may not start with a hyphen (-) — that's the shape of a command-line flag, not a session id." };

function maintainerSection(over: Partial<SettingsSection> = {}): SettingsSection {
  return {
    id: "maintainer",
    title: { zh: "开发者 · 开发会话", en: "Developer session" },
    help: { zh: "", en: "" },
    terminal_app_name: "Ghostty",
    fields: [
      { key: "maintainer_repo_path", kind: "string", label: { zh: "本软件的仓库路径", en: "This software's repo path" }, help: { zh: "", en: "" },
        default: "", choices: null, effective: "", source: "default", path: "dir", path_exists: null,
        placeholder: { zh: "/Users/demo/Projects/zelin-ai-assistant", en: "/Users/demo/Projects/zelin-ai-assistant" } },
      { key: "maintainer_session_id", kind: "string", label: { zh: "续接的会话 id", en: "Session id to resume" }, help: { zh: "", en: "" },
        default: "", choices: null, effective: "", source: "default",
        check: { kind: "session_id", message: CHARSET, reasons: { leading_hyphen: HYPHEN } } },
    ],
    ...over,
  };
}

async function renderWith(language: Language, section: SettingsSection | null = maintainerSection()) {
  const catalog: SettingsCatalog = { sections: section ? [section] : [] };
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog);
  await refreshSettingsCatalog();
  return render(<LanguageContext.Provider value={language}><MaintainerExtras /></LanguageContext.Provider>);
}

function apiError(status: number, message: string, details: unknown, code = "INVALID_FIELD") {
  return new ApiError(status, { error: { code, message, details } });
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(postMaintainerTerminal).mockReset();
});
afterEach(cleanup);

describe("MaintainerExtras — 帮助句的终端名", () => {
  it("names the terminal the server resolved (zh / en) instead of hardcoding Terminal.app", async () => {
    await renderWith("zh");
    expect(screen.getByText("会在 Ghostty 中打开（终端应用在「通用」里换）。")).toBeTruthy();
    expect(screen.queryByText(/Terminal\.app/)).toBeNull();
    cleanup();
    await renderWith("en");
    expect(screen.getByText("Opens in Ghostty (change the terminal app under General).")).toBeTruthy();
  });

  it("falls back to a generic word when an older server sends no terminal_app_name", async () => {
    await renderWith("zh", maintainerSection({ terminal_app_name: undefined }));
    expect(screen.getByText("会在 终端 中打开（终端应用在「通用」里换）。")).toBeTruthy();
    cleanup();
    await renderWith("en", null);
    expect(screen.getByText("Opens in the terminal (change the terminal app under General).")).toBeTruthy();
  });
});

describe("MaintainerExtras — 打开的下场", () => {
  it("busy word while opening, then the native success sentence (not the raw command); the helper keeps reading the catalog, not the receipt", async () => {
    let release: (value: TerminalReceipt) => void = () => undefined;
    vi.mocked(postMaintainerTerminal).mockImplementation(() => new Promise<TerminalReceipt>((resolve) => { release = resolve; }));
    await renderWith("zh");
    const button = screen.getByRole("button", { name: "在终端打开开发会话" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(postMaintainerTerminal).toHaveBeenCalledTimes(1);
    expect(button.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("正在打开终端…");
    release({ ok: true, command: "cd /r && claude", command_file: "/tmp/x.command", cwd: "/r", terminal_app_name: "iTerm2" });
    await screen.findByText("已在终端打开 ✓ 直接告诉它要修什么、改什么就行。");
    expect(screen.queryByText("cd /r && claude")).toBeNull();
    // 回执的名字不另存：目录才是真源（「通用」换终端后 store 重拉目录，存了回执的名字就会盖过新值——TerminalAppNameRefresh.test）
    expect(screen.getByText("会在 Ghostty 中打开（终端应用在「通用」里换）。")).toBeTruthy();
    await waitFor(() => expect(button.disabled).toBe(false));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("English success sentence", async () => {
    vi.mocked(postMaintainerTerminal).mockResolvedValue({ ok: true, command: "cd /r && claude", command_file: "/tmp/x", cwd: "/r" });
    await renderWith("en");
    fireEvent.click(screen.getByRole("button", { name: "Open a development session in the terminal" }));
    await screen.findByText("Opened in the terminal ✓ — just tell it what to fix or change.");
  });

  it("open failure (500 with details.command) → native sentence + the command to run by hand, copyable", async () => {
    vi.mocked(postMaintainerTerminal).mockRejectedValue(
      apiError(500, "could not open Terminal: boom", { command_file: "/tmp/x.command", command: "cd '/r' && claude --resume abc" }, "INTERNAL_ERROR"),
    );
    await renderWith("zh");
    fireEvent.click(screen.getByRole("button", { name: "在终端打开开发会话" }));
    const alert = await screen.findByRole("alert");
    expect(screen.getByText("打开终端失败——去「通用」检查终端应用设置，或手动在终端运行：")).toBeTruthy();
    expect(alert.textContent).toContain("cd '/r' && claude --resume abc");
    expect(screen.getByRole("button", { name: "复制" })).toBeTruthy();
    cleanup();
    vi.mocked(postMaintainerTerminal).mockRejectedValue(apiError(500, "could not open Terminal: boom", { command: "cd '/r' && claude" }, "INTERNAL_ERROR"));
    await renderWith("en");
    fireEvent.click(screen.getByRole("button", { name: "Open a development session in the terminal" }));
    await screen.findByText("Couldn't open the terminal — check the terminal app under General, or run this by hand:");
  });

  it("a 500 without a command keeps the server's own message", async () => {
    vi.mocked(postMaintainerTerminal).mockRejectedValue(apiError(500, "disk full", {}, "INTERNAL_ERROR"));
    await renderWith("zh");
    fireEvent.click(screen.getByRole("button", { name: "在终端打开开发会话" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("disk full");
  });
});

describe("MaintainerExtras — 400 的两种", () => {
  it("path missing → 路径不存在", async () => {
    vi.mocked(postMaintainerTerminal).mockRejectedValue(apiError(400, "repo path does not exist", { path: "/nope" }));
    await renderWith("zh");
    fireEvent.click(screen.getByRole("button", { name: "在终端打开开发会话" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("路径不存在");
  });

  it("session-id re-check at launch → the catalog's server-owned sentence in the UI language, reason picks the hyphen variant", async () => {
    vi.mocked(postMaintainerTerminal).mockRejectedValue(
      apiError(400, `${HYPHEN.zh} / ${HYPHEN.en}`, { field: "maintainer_session_id", check: "session_id", reason: "leading_hyphen" }),
    );
    await renderWith("en");
    fireEvent.click(screen.getByRole("button", { name: "Open a development session in the terminal" }));
    let alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(HYPHEN.en);
    cleanup();
    vi.mocked(postMaintainerTerminal).mockRejectedValue(
      apiError(400, `${CHARSET.zh} / ${CHARSET.en}`, { field: "maintainer_session_id", check: "session_id" }),
    );
    await renderWith("zh");
    fireEvent.click(screen.getByRole("button", { name: "在终端打开开发会话" }));
    alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(CHARSET.zh);
  });

  it("a 400 whose details name another check (or no catalog) keeps the server message", async () => {
    vi.mocked(postMaintainerTerminal).mockRejectedValue(apiError(400, "zh / en", { field: "gmail_address", check: "email" }));
    await renderWith("zh");
    fireEvent.click(screen.getByRole("button", { name: "在终端打开开发会话" }));
    let alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("zh / en");
    cleanup();
    vi.mocked(postMaintainerTerminal).mockRejectedValue(
      apiError(400, `${CHARSET.zh} / ${CHARSET.en}`, { field: "maintainer_session_id", check: "session_id" }),
    );
    await renderWith("zh", null);
    fireEvent.click(screen.getByRole("button", { name: "在终端打开开发会话" }));
    alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(`${CHARSET.zh} / ${CHARSET.en}`);
  });
});
