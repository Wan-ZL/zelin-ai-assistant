// 设置 · 同步 / 配对（CONTRACT §68.15；原生 SettingsSync.swift）：开关开 = pair（首次配对带预填电脑名、有存名不带）/ 关 = disable；
// 状态句逐字原生；qrCard 只在开着时渲染（二维码 = server base64）；改名 = pair 带 label、只在与已存名不同且非空时亮；
// 重新生成不带 label；失败句三支（配对失败 / 找不到可用的 python / 关闭失败）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSync, postSyncDisable, postSyncPair } from "../../api";
import { LanguageContext } from "../../i18n";
import type { SyncStatus } from "../../types";
import { SyncSection } from "./SyncSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSync: vi.fn(), postSyncPair: vi.fn(), postSyncDisable: vi.fn() };
});

const off = (label = ""): SyncStatus => ({ enabled: false, channel_id: "", label, default_label: "Zelins-Mac", qr_png_base64: null });
const on = (label = "公司 Mac"): SyncStatus => ({ enabled: true, channel_id: "ch-1", label, default_label: "Zelins-Mac", qr_png_base64: "QUJD" });

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  vi.mocked(fetchSync).mockReset();
  vi.mocked(postSyncPair).mockReset();
  vi.mocked(postSyncDisable).mockReset();
});
afterEach(cleanup);

describe("SyncSection", () => {
  it("off: no QR card; turning on pairs with the prefilled computer name on FIRST pair and renders the card", async () => {
    vi.mocked(fetchSync).mockResolvedValue(off());
    vi.mocked(postSyncPair).mockResolvedValue({ ok: true, channel_id: "ch-1", label: "Zelins-Mac", registered: true, qr_png_base64: "QUJD" });
    renderEn(<SyncSection />);
    const toggle = await screen.findByRole("switch", { name: "Enable sync / pairing" });
    await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false));
    expect(screen.queryByText("Device name:")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByRole("status").textContent).toBe("Turning on sync and generating the pairing QR…");
    await screen.findByText("On ✓ Scan the code below from your phone to pair.");
    expect(postSyncPair).toHaveBeenCalledWith("Zelins-Mac");
    expect((screen.getByAltText("Pairing QR code") as HTMLImageElement).src).toBe("data:image/png;base64,QUJD");
    expect(screen.getByText("Device name:")).toBeTruthy();
    expect(screen.getByText("ch-1")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);   // 名字未改
  });

  it("on: re-enabling never passes the stored name; Re-pair passes nothing; rename passes the new label", async () => {
    vi.mocked(fetchSync).mockResolvedValue(off("书房 Mac"));
    vi.mocked(postSyncPair).mockResolvedValue({ ok: true, channel_id: "ch-1", label: "书房 Mac", registered: false, qr_png_base64: null });
    renderEn(<SyncSection />);
    const toggle = await screen.findByRole("switch");
    await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false));
    expect((screen.queryByLabelText("Device name:") as HTMLInputElement | null)).toBeNull();
    fireEvent.click(toggle);
    await screen.findByText("On (channel registration retries automatically once online) — the QR is ready to scan now.");
    expect(postSyncPair).toHaveBeenLastCalledWith(undefined);   // 有存过的名字：不传，syncd 沿用
    fireEvent.click(screen.getByRole("button", { name: "Re-pair" }));
    expect(screen.getByRole("status").textContent).toBe("Regenerating the pairing QR…");
    await waitFor(() => expect(postSyncPair).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postSyncPair).mock.calls[1]).toEqual([undefined]);
    const name = screen.getByLabelText("Device name:") as HTMLInputElement;
    expect(name.value).toBe("书房 Mac");
    fireEvent.change(name, { target: { value: "  客厅 Mac " } });
    vi.mocked(postSyncPair).mockResolvedValue({ ok: true, channel_id: "ch-1", label: "客厅 Mac", registered: true, qr_png_base64: "QUJD" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("status").textContent).toBe("Updating the device name and refreshing the QR…");
    await screen.findByText("Device name updated ✓ The QR has been refreshed too.");
    expect(postSyncPair).toHaveBeenLastCalledWith("客厅 Mac");
  });

  it("turning off runs disable with the native sentences; failures are the three native error lines", async () => {
    vi.mocked(fetchSync).mockResolvedValue(on());
    vi.mocked(postSyncDisable).mockResolvedValue({ ok: true, ...off("公司 Mac") });
    renderEn(<SyncSection />);
    const toggle = await screen.findByRole("switch");
    await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true));
    fireEvent.click(toggle);
    expect(screen.getByRole("status").textContent).toBe("Turning off sync…");
    await screen.findByText("Off. The keys stay on this Mac; re-enable anytime — no re-pairing needed.");
    expect(screen.queryByAltText("Pairing QR code")).toBeNull();
    vi.mocked(postSyncPair).mockResolvedValue({ ok: false, error: "no_python", message: "Errno 2" });
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("No usable python — set up the runtime first in General · Setup wizard.");
    vi.mocked(postSyncPair).mockResolvedValue({ ok: false, error: "pair_failed", message: "boom" });
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("Pairing failed — check your network and retry (see state/syncd.log).");
    cleanup();
    vi.mocked(fetchSync).mockResolvedValue(on());
    vi.mocked(postSyncDisable).mockResolvedValue({ ok: false, error: "disable_failed", message: "x", ...on() });
    renderEn(<SyncSection />);
    const toggle2 = await screen.findByRole("switch");
    await waitFor(() => expect((toggle2 as HTMLInputElement).checked).toBe(true));
    fireEvent.click(toggle2);
    await screen.findByText("Couldn't turn it off — try again later.");
  });
});
