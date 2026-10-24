// 向导「完成」之后补拉会议纪要与健康（CONTRACT §49 2026-09-06 追记 (b)，D40）：换页不再重载文档，App 启动那一拉 /api/recaps
// 多半还是空的（向导期间 cron 可能刚跑出第一份）——回会议纪要时不等下一条 SSE，立刻 refreshRecaps + refreshHealth。判例：点「完成」
// → postSetupStep("complete") → navigate 回会议纪要（不带 ?page=）→ /api/recaps 拉一次、/api/health 比点之前多拉一次；complete 失败
// → 都不拉。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchRecaps, fetchHealth, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine, postSetupStep } from "../api";
import { LanguageContext } from "../i18n";
import { navigate } from "../route";
import { resetStoreForTests } from "../store";
import type { SetupSnapshot } from "../types";
import { SetupPage } from "./SetupPage";

vi.mock("../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../route")>();
  return { ...actual, navigate: vi.fn() };
});

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchRecaps: vi.fn(), fetchHealth: vi.fn(), fetchPermissions: vi.fn(), fetchSetup: vi.fn(), fetchSecrets: vi.fn(), fetchSetupEngine: vi.fn(),
    postSetupStep: vi.fn(), fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }),
  };
});

const ENGINE_READY = { cli_path: "/usr/local/bin/claude", version: "1.0.99 (Claude Code)", auth: "secrets_file", auth_sources: { env_key: false, secrets_file: true, legacy_file: false }, ready: true };
const HEALTH_OK = { verdict: "ok", server: { ok: true }, cron: { age_s: 300, read_ok: true, stale: false }, recap: null, checked_at: "x" };
const SNAPSHOT = { recaps: [] };

function setup(over: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return { needed: true, done: false, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false, ...over };
}

async function renderFinale() {
  window.history.replaceState(null, "", "/?page=setup&step=finale");
  render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
  await screen.findByText("Step 6 of 6");
  await waitFor(() => expect(fetchHealth).toHaveBeenCalled()); // 进末步的那一拉（管线探针）先落地，之后再数
}

beforeEach(() => {
  resetStoreForTests();
  for (const fn of [fetchRecaps, fetchHealth, fetchPermissions, fetchSetup, fetchSecrets, fetchSetupEngine, postSetupStep, navigate]) vi.mocked(fn).mockReset();
  vi.mocked(fetchSetup).mockResolvedValue(setup());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_READY);
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH_OK);
  vi.mocked(fetchRecaps).mockResolvedValue(SNAPSHOT);
  vi.mocked(fetchPermissions).mockRejectedValue(new Error("offline"));
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("setup wizard — Done refreshes the recaps and health after the in-document switch (D40)", () => {
  it("Done → complete → navigate to the recaps page → /api/recaps fetched once and /api/health once more", async () => {
    vi.mocked(postSetupStep).mockResolvedValue({ ok: true, setup: setup({ done: true, needed: false }) });
    await renderFinale();
    const healthBefore = vi.mocked(fetchHealth).mock.calls.length;
    expect(fetchRecaps).not.toHaveBeenCalled(); // 向导页自己不读会议纪要
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(postSetupStep).toHaveBeenCalledWith("complete"));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(String(vi.mocked(navigate).mock.calls[0][0])).not.toContain("page=");
    await waitFor(() => expect(fetchRecaps).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(vi.mocked(fetchHealth).mock.calls.length).toBe(healthBefore + 1));
  });

  it("complete fails → stays on the wizard, nothing refetched", async () => {
    vi.mocked(postSetupStep).mockRejectedValue(new Error("boom"));
    await renderFinale();
    const healthBefore = vi.mocked(fetchHealth).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(postSetupStep).toHaveBeenCalledWith("complete"));
    await screen.findByRole("status");
    expect(navigate).not.toHaveBeenCalled();
    expect(fetchRecaps).not.toHaveBeenCalled();
    expect(vi.mocked(fetchHealth).mock.calls.length).toBe(healthBefore);
  });
});
