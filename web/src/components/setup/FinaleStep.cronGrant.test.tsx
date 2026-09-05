// 向导末步「定时任务磁盘权限」红行的「去授权」= 原生 SetupWizard cronFDAHealthRow 的 CronFDA.beginGrant（§68.5 / §68.4 追记
// 2026-09-05；parity gap pages-shell-nav-cron-fda-guided-grant）：壳里先把 /usr/sbin/cron 放进剪贴板再 openPane full_disk；
// 浏览器里仍是权限体检页的 <a>（既有判例的 link 形状不变），点击顺手复制。桥 reject → 原生 fixNote 行（原句）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, fetchPermissions } from "../../api";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests } from "../../shellBridge";
import { refreshHealth, refreshPermissions, resetStoreForTests } from "../../store";
import type { HealthSnapshot, PermissionsSnapshot, SetupEngine } from "../../types";
import { FinaleStep } from "./FinaleStep";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn(), fetchPermissions: vi.fn(), postRepairActd: vi.fn(), postSeedDashboard: vi.fn() };
});

const OK: HealthSnapshot = {
  verdict: "ok",
  heartbeat: { age_s: 4, phase: "idle", pid: 4242, interval: 10, stale_after_s: 90, stale: false },
  dashboard: { generated_at: "2026-09-05T08:00:00Z", age_s: 5, stale: false },
  loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "2026-09-05T08:00:05Z",
};
const ENGINE: SetupEngine = { cli_path: "/usr/local/bin/claude", version: "1.0.99", auth: "api_key", auth_sources: {}, ready: true };
const PERMISSIONS = {
  home: "/h", on_external_volume: false,
  fda: { needed: false, pane: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles", executables: [] },
  panes: {}, doctor_ran_at: "x", doctor_ok: true, vault: { status: "unknown", root: "/v" },
  doctor: [{ name: "cron disk access", status: "fail", detail: "blocked", fix: "", failure_id: "cron_fda_blocked" }],
} as unknown as PermissionsSnapshot;

const SHELL = {
  recording: { available: true, on: false, mode: "off", engine_running: false, resume_mode: "screen" }, captions: {},
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" }, launch_at_login: false, hotkey: "x",
};

let writeText: ReturnType<typeof vi.fn>;
let bridgeCalls: unknown[];
let bridgeFails = false;

function installShell() {
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage: async (body: unknown) => {
    bridgeCalls.push(body);
    if (bridgeFails) throw new Error("UNKNOWN_METHOD: openPane");
    return SHELL;
  } } } };
  applyShellState(SHELL);
}

async function renderFinale() {
  vi.mocked(fetchHealth).mockResolvedValue(OK);
  vi.mocked(fetchPermissions).mockResolvedValue(PERMISSIONS);
  await refreshHealth();
  await refreshPermissions();
  render(
    <LanguageContext.Provider value="en">
      <FinaleStep engine={ENGINE} engineChecking={false} goEngine={() => undefined} />
    </LanguageContext.Provider>,
  );
  expect((document.querySelector("[data-row='cron']") as HTMLElement).className).toContain("is-fail");
}

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  bridgeCalls = [];
  bridgeFails = false;
  delete (window as Window & { webkit?: unknown }).webkit;
  window.history.replaceState(null, "", "/?page=setup&step=finale");
});

afterEach(() => cleanup());

describe("FinaleStep cron row Grant… (native CronFDA.beginGrant)", () => {
  it("with the shell: copies /usr/sbin/cron, then opens the Full Disk Access pane", async () => {
    installShell();
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Grant…" }));
    await waitFor(() => expect(bridgeCalls).toEqual([{ method: "openPane", pane: "full_disk" }]));
    expect(writeText).toHaveBeenCalledWith("/usr/sbin/cron");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a bridge that cannot open the pane surfaces the reason as the row's fix note", async () => {
    installShell();
    bridgeFails = true;
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Grant…" }));
    await screen.findByText("UNKNOWN_METHOD: openPane");
    expect(writeText).toHaveBeenCalledWith("/usr/sbin/cron");
  });

  it("in a browser: Grant… stays the permissions deep link and copies the path on click", async () => {
    await renderFinale();
    const link = screen.getByRole("link", { name: "Grant…" });
    expect(link.getAttribute("href")).toContain("page=permissions");
    fireEvent.click(link);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("/usr/sbin/cron"));
    expect(bridgeCalls).toEqual([]);
  });
});
