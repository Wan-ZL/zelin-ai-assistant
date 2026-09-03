// 设置页「每日整理」section（CONTRACT §65，D10）：
//   1) 五把旋钮从 server 快照水合；2) 保存 = 一次 PUT、只带改动键、数字原样交 server 校验；
//   3) 400 的整句原文以 toast(role=alert) 显示；4) 读失败只红本 section。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchDailyLoopSettings, putDailyLoopSettings } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { DailyLoopSettings } from "../../types";
import { DailyLoopSection, diffPatch } from "./DailyLoopSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchDailyLoopSettings: vi.fn(), putDailyLoopSettings: vi.fn() };
});

function snapshot(over: Partial<DailyLoopSettings> = {}): DailyLoopSettings {
  return {
    enabled: true,
    time: "03:30",
    max_proposals_per_day: 5,
    stale_days: 45,
    trash_retention_days: 90,
    source: { enabled: "default", time: "default" },
    ...over,
  };
}

function renderSection() {
  return render(
    <LanguageContext.Provider value="en">
      <DailyLoopSection />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchDailyLoopSettings).mockReset();
  vi.mocked(putDailyLoopSettings).mockReset();
});
afterEach(cleanup);

describe("diffPatch", () => {
  it("sends only the changed keys, numbers as numbers, junk verbatim for the server to reject", () => {
    const cur = snapshot();
    const base = { enabled: true, time: "03:30", max_proposals_per_day: "5", stale_days: "45", trash_retention_days: "90" };
    expect(diffPatch(base, cur)).toEqual({});
    expect(diffPatch({ ...base, enabled: false, max_proposals_per_day: "2" }, cur)).toEqual({ enabled: false, max_proposals_per_day: 2 });
    expect(diffPatch({ ...base, time: " 4:00 " }, cur)).toEqual({ time: "4:00" });
    expect(diffPatch({ ...base, stale_days: "abc" }, cur)).toEqual({ stale_days: "abc" });
  });
});

describe("<DailyLoopSection />", () => {
  it("hydrates the knobs and saves a diff PUT", async () => {
    vi.mocked(fetchDailyLoopSettings).mockResolvedValue(snapshot());
    vi.mocked(putDailyLoopSettings).mockResolvedValue(snapshot({ max_proposals_per_day: 2, source: { max_proposals_per_day: "override" } }));
    renderSection();
    const cap = await screen.findByLabelText("Max proposals per day") as HTMLInputElement;
    expect(cap.value).toBe("5");
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(cap, { target: { value: "2" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putDailyLoopSettings).toHaveBeenCalledWith({ max_proposals_per_day: 2 }));
    await screen.findByRole("status");
    expect((await screen.findByLabelText("Max proposals per day") as HTMLInputElement).value).toBe("2");
  });

  it("shows the server's plain-language 400 as an alert toast", async () => {
    vi.mocked(fetchDailyLoopSettings).mockResolvedValue(snapshot());
    vi.mocked(putDailyLoopSettings).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "time 必须是 HH:MM / time must be HH:MM local, e.g. 03:30" } }));
    renderSection();
    const time = await screen.findByLabelText("Time of day (local)");
    fireEvent.change(time, { target: { value: "25:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("HH:MM");
  });

  it("toggle writes enabled=false", async () => {
    vi.mocked(fetchDailyLoopSettings).mockResolvedValue(snapshot());
    vi.mocked(putDailyLoopSettings).mockResolvedValue(snapshot({ enabled: false }));
    renderSection();
    const toggle = await screen.findByRole("checkbox");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putDailyLoopSettings).toHaveBeenCalledWith({ enabled: false }));
  });

  it("read failure reds only this section", async () => {
    vi.mocked(fetchDailyLoopSettings).mockRejectedValue(new ApiError(500, { error: { code: "INTERNAL", message: "boom" } }));
    renderSection();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("boom");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
