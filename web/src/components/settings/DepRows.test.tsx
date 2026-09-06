// 依赖检查快速行 / 失败动作的四条原生余量（CONTRACT §25 / §68.3 / §68.4 追记 2026-09-05；parity 批次 deps-quick-rows-failure-actions）：
//   · cron 「去授权」= 原生 CronFDA.beginGrant：先把 /usr/sbin/cron 放进剪贴板再桥 openPane full_disk；浏览器里复制 + 权限体检页深链；
//     失败行下方印原生 grantSteps 整句（ok == false 的三态都印，ok 不印）；
//   · 凭证行 ok = present || legacy，旧路径态的后缀「（App 内管理；当前用旧路径）」（原生 credRow）；
//   · 「看进度」（engine_npm_download）深链依赖检查区的 engine.log 尾巴，不再指回录制页自己；
//   · 「显示」目录不在 → server 回 missing → 「目录不存在，已打开上级目录」+ 设置 → 笔记库 深链。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchFailures, fetchSecrets, postFolderOpen } from "../../api";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import type { DoctorReport, SecretsStatus } from "../../types";
import { buildDepRows, DepRows, secretVerdict } from "./DepRows";
import { CRON_BINARY, cronGrantSteps, FailureActionButton, grantCronFda } from "./failureAction";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSecrets: vi.fn(), fetchFailures: vi.fn(), postFolderOpen: vi.fn() };
});

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;
const renderEn = (node: React.ReactNode) => render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);

const report: DoctorReport = { ok: true, fast: true, rc: 0, home: "/h", ran_at: "x", checks: [
  { name: "obsidian vault", status: "warn", detail: "/v missing", fix: "" },
] };
const secrets: SecretsStatus = { secrets: [
  { name: "anthropic-api-key.txt", label: { zh: "k", en: "k" }, present: true, verifiable: true, mtime: 1, legacy: false },
  { name: "slack-user-token.txt", label: { zh: "s", en: "s" }, present: false, verifiable: true, mtime: null, legacy: true },
  { name: "gmail-app-password.txt", label: { zh: "g", en: "g" }, present: false, verifiable: true, mtime: null, legacy: false },
] };
const shell: ShellState = {
  recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "granted", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space",
};
const NOW = Date.parse("2026-09-05T12:00:00Z");
const cronRow = () => within(document.querySelector("[data-dep='cron_fda']") as HTMLElement);
const BLOCKED = { ts: "2026-09-05T11:40:00Z", read_ok: false, protected_path: "/Users/d/Documents/V" };
const READABLE = { ts: "2026-09-05T11:40:00Z", read_ok: true, protected_path: "/Users/d/Documents/V" };

let writeText: ReturnType<typeof vi.fn>;
let bridgeCalls: unknown[];
/** 剪贴板写入与桥调用共用的一本顺序账（`copy:<text>` / `bridge:<method>`）——原生 beginGrant 是「先剪贴板、再面板」 */
let order: string[];

function installShell() {
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage: async (body: unknown) => {
    bridgeCalls.push(body);
    order.push(`bridge:${(body as { method?: string }).method}`);
    return shell;
  } } } };
  applyShellState(shell);
}

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
  order = [];
  writeText = vi.fn(async (value: string) => { order.push(`copy:${value}`); });
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  bridgeCalls = [];
  vi.mocked(fetchSecrets).mockReset().mockResolvedValue(secrets);
  vi.mocked(fetchFailures).mockReset().mockResolvedValue({ failures: {} });
  vi.mocked(postFolderOpen).mockReset();
  window.history.replaceState(null, "", "/?page=settings&anchor=deps");
  delete (window as Window & { webkit?: unknown }).webkit;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("cron FDA 「去授权」= 复制 /usr/sbin/cron + 开面板（原生 CronFDA.beginGrant）", () => {
  it("CRON_BINARY is the native literal and grantSteps quote it verbatim in both languages", () => {
    expect(CRON_BINARY).toBe("/usr/sbin/cron");
    expect(cronGrantSteps(zh)).toBe("点「去授权」会把 /usr/sbin/cron 复制到剪贴板并打开「完全磁盘访问」面板。然后：点 ➕ → 按 ⌘⇧G → ⌘V 粘贴 → 回车 → 选中 cron → 开启开关。下次定时任务运行（约 30 分钟内）后这一行会自动变绿。");
    expect(cronGrantSteps(en)).toBe("\"Grant…\" copies /usr/sbin/cron to the clipboard and opens the Full Disk Access pane. Then: click ➕ → press ⌘⇧G → ⌘V to paste → Return → select cron → toggle it on. This row turns green after the next scheduled run (within ~30 min).");
  });

  it("with the shell: Grant… writes the clipboard first, then asks the shell to open the Full Disk Access pane", async () => {
    installShell();
    await grantCronFda();
    // 顺序本身就是判例：复制在前、开面板在后（原生 beginGrant：清剪贴板 → 放路径 → 开面板）——反过来两个 toHaveBeenCalled 也能各自过
    expect(order).toEqual(["copy:/usr/sbin/cron", "bridge:openPane"]);
    expect(bridgeCalls).toEqual([{ method: "openPane", pane: "full_disk" }]);
    // 剪贴板不可用（老 WebView 连 execCommand 都没有）也不挡开面板——原生同样不查 pasteboard 结果
    writeText.mockRejectedValueOnce(new Error("denied"));
    bridgeCalls = [];
    order = [];
    await grantCronFda();
    expect(bridgeCalls).toEqual([{ method: "openPane", pane: "full_disk" }]);
    expect(order).toEqual(["bridge:openPane"]);
  });

  it("in a browser: Grant… is the permissions deep link and still copies the path on click", async () => {
    renderEn(<DepRows report={report} probe={BLOCKED} />);
    expect((document.querySelector("[data-dep='cron_fda']") as HTMLElement).getAttribute("data-ok")).toBe("false");
    const link = cronRow().getByRole("link", { name: "Grant…" });   // 屏幕 / 麦克风行也有 Grant…，只看 cron 行
    expect(link.getAttribute("href")).toContain("page=permissions");
    fireEvent.click(link);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("/usr/sbin/cron"));
    expect(bridgeCalls).toEqual([]);
  });

  it("the cron row prints the click-by-click steps under a failing row and hides them when cron can read", async () => {
    installShell();
    renderEn(<DepRows report={report} probe={BLOCKED} />);
    expect(cronRow().getByText(cronGrantSteps(en))).toBeTruthy();
    fireEvent.click(cronRow().getByRole("button", { name: "Grant…" }));
    await waitFor(() => expect(bridgeCalls).toEqual([{ method: "openPane", pane: "full_disk" }]));
    expect(writeText).toHaveBeenCalledWith("/usr/sbin/cron");
    cleanup();
    renderEn(<DepRows report={report} probe={READABLE} />);
    expect(screen.queryByText(cronGrantSteps(en))).toBeNull();
    // 没探针 / 过期也是 ok == false —— 原生按 ok 判、不按被挡判
    const noProbe = buildDepRows(report, null, secrets, null, null, "en", en).find((r) => r.id === "cron_fda")!;
    expect(noProbe.note).toBe(cronGrantSteps(en));
    const fresh = buildDepRows(report, null, secrets, null, READABLE, "en", en).find((r) => r.id === "cron_fda")!;
    expect(fresh.note).toBeUndefined();
  });

  it("the doctor-row FailureActionButton for cron_fda_blocked is the same guided grant", async () => {
    installShell();
    renderEn(<FailureActionButton failureId="cron_fda_blocked" />);
    fireEvent.click(screen.getByRole("button", { name: "Grant…" }));
    await waitFor(() => expect(bridgeCalls).toEqual([{ method: "openPane", pane: "full_disk" }]));
    expect(writeText).toHaveBeenCalledWith("/usr/sbin/cron");
    cleanup();
    delete (window as Window & { webkit?: unknown }).webkit;
    renderEn(<FailureActionButton failureId="cron_fda_blocked" />);
    expect(screen.getByRole("link", { name: "Grant…" }).getAttribute("href")).toContain("page=permissions");
  });
});

describe("凭证行：旧路径的文件算 ok（原生 credRow hasSecret || hasLegacy）", () => {
  it("secretVerdict mirrors the three native suffixes", () => {
    expect(secretVerdict(null, en)).toEqual({ ok: null, suffix: "" });
    expect(secretVerdict({ present: true, legacy: false }, en)).toEqual({ ok: true, suffix: " (managed in-app)" });
    expect(secretVerdict({ present: false, legacy: true }, zh)).toEqual({ ok: true, suffix: "（App 内管理；当前用旧路径）" });
    expect(secretVerdict({ present: false, legacy: true }, en)).toEqual({ ok: true, suffix: " (managed in-app; using legacy path)" });
    expect(secretVerdict({ present: false, legacy: false }, en)).toEqual({ ok: false, suffix: " (managed in-app; not set)" });
    // 老 server 没有 legacy 键：present 单独说话
    expect(secretVerdict({ present: false }, en).ok).toBe(false);
  });

  it("the Slack row on a legacy path is ✅ with the legacy suffix; the unset Gmail row stays ⚠️", async () => {
    const rows = buildDepRows(report, null, secrets, null, null, "en", en);
    const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
    expect(byId.slack.ok).toBe(true);
    expect(byId.gmail.ok).toBe(false);
    expect(byId.anthropic.ok).toBe(true);
    renderEn(<DepRows report={report} probe={READABLE} />);
    // 凭证来自 store（GET /api/secrets），等它回来
    await screen.findByText("(managed in-app; using legacy path)");   // 节点文本首空格被 normalizer 去掉
    expect(screen.getByText("(managed in-app; not set)")).toBeTruthy();
    expect((document.querySelector("[data-dep='slack']") as HTMLElement).getAttribute("data-ok")).toBe("true");
  });
});

describe("「看进度」与「显示」", () => {
  it("engine_npm_download links to the engine.log tail in the dependency check, not back to the Recording page", () => {
    window.history.replaceState(null, "", "/?page=ingest");
    renderEn(<FailureActionButton failureId="engine_npm_download" compact />);
    const href = screen.getByRole("link", { name: "View progress" }).getAttribute("href") ?? "";
    const url = new URL(href, "http://127.0.0.1");
    expect(url.searchParams.get("page")).toBe("settings");
    expect(url.searchParams.get("anchor")).toBe("deps");
    expect(url.searchParams.get("log")).toBe("engine.log");
  });

  it("Reveal on a missing vault says the parent was opened and links to Settings → Notes vault", async () => {
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/v/2 - raw", opened: "/v", missing: true });
    renderEn(<DepRows report={report} probe={READABLE} />);
    fireEvent.click(screen.getByRole("button", { name: "Reveal" }));
    await screen.findByText("Folder doesn't exist — opened its parent instead");
    expect(screen.getByRole("status")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Create it in Settings → Notes vault" });
    const url = new URL(link.getAttribute("href") ?? "", "http://127.0.0.1");
    expect(url.searchParams.get("page")).toBe("settings");
    expect(url.searchParams.get("anchor")).toBe("obsidian");
    // 目录在：回执没有 missing → 不说话
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/v/2 - raw" });
    fireEvent.click(screen.getByRole("button", { name: "Reveal" }));
    await waitFor(() => expect(screen.queryByText("Folder doesn't exist — opened its parent instead")).toBeNull());
  });
});
