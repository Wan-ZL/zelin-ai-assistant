// 依赖检查区的 `?log=<name>` 深链跟路由（CONTRACT §49 2026-09-06 追记 (b)，D40）：横幅「查看日志」→ `?page=settings&anchor=deps&log=actd.log`
// 直接翻开该日志尾巴。D40 换页不重载：已在设置页时点「查看引擎日志」本区不重挂——`?log=` 从路由订阅里读，URL 变了就再翻一次；
// 名字只认 server 同一白名单形（`[A-Za-z0-9._-]+\.log`），别的形不打 /api/logs；同一 URL 不重复拉。
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDiagnostics, fetchDoctor, fetchFailures, fetchLogTail, fetchSecrets } from "../../api";
import { LanguageContext } from "../../i18n";
import { navigate, resetRouterForTests } from "../../route";
import { resetStoreForTests } from "../../store";
import type { DiagnosticsSnapshot } from "../../types";
import { DepsSection } from "./DepsSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn().mockResolvedValue({}), fetchHealth: vi.fn(), fetchDiagnostics: vi.fn(), fetchDoctor: vi.fn(), fetchSecrets: vi.fn(),
    fetchFailures: vi.fn(), fetchLogTail: vi.fn(),
  };
});

const diagnostics = {
  doctor: { ok: true, fast: true, rc: 0, home: "/h", ran_at: "x", checks: [] },
  health: { verdict: "ok", heartbeat: null, dashboard: null, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" },
  deploy_state: null, install_report: null, registry_backend: "sqlite", radar_sources: null, logs: [], cron_probe: null, activity: null,
} as unknown as DiagnosticsSnapshot;

async function renderDeps(url: string) {
  window.history.replaceState(null, "", url);
  render(<LanguageContext.Provider value="en"><DepsSection /></LanguageContext.Provider>);
  await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalled());
}

beforeEach(() => {
  resetStoreForTests();
  resetRouterForTests();
  for (const fn of [fetchDiagnostics, fetchDoctor, fetchSecrets, fetchFailures, fetchLogTail]) vi.mocked(fn).mockReset();
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchFailures).mockResolvedValue({ failures: {} });
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics);
  vi.mocked(fetchLogTail).mockResolvedValue({ name: "actd.log", path: "/h/state/actd.log", size: 6, lines: ["hello"], truncated: false });
});

afterEach(() => {
  cleanup();
  resetRouterForTests();
  window.history.replaceState(null, "", "/");
});

describe("DepsSection — ?log= deep link follows the route (D40)", () => {
  it("mounting on ?log=actd.log opens that tail once; a later navigate to another ?log= opens it without a remount", async () => {
    await renderDeps("/?page=settings&log=actd.log");
    await waitFor(() => expect(fetchLogTail).toHaveBeenCalledWith("actd.log", 300));
    expect(fetchLogTail).toHaveBeenCalledTimes(1);
    act(() => {
      navigate("/?page=settings&log=engine.log"); // 录制页 / 诊断条的「查看引擎日志」，已在设置页时点
    });
    await waitFor(() => expect(fetchLogTail).toHaveBeenCalledWith("engine.log", 300));
    expect(fetchLogTail).toHaveBeenCalledTimes(2);
    expect(fetchDiagnostics).toHaveBeenCalledTimes(1); // 没重挂：快照没重拉
  });

  it("a URL without ?log= (or with a name outside the whitelist) does not touch /api/logs", async () => {
    await renderDeps("/?page=settings");
    act(() => {
      navigate("/?page=settings&log=../etc/passwd");
    });
    act(() => {
      navigate("/?page=settings&log=actd");
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchLogTail).not.toHaveBeenCalled();
  });
});
