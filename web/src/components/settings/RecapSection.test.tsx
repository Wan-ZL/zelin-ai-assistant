// 设置页「会议纪要」section（CONTRACT §63）：两把旋钮从 server 快照水合；Slack 草稿开关随 §63.4 退役（不再渲染）；
// 保存 = 一次 PUT 两键、零多余字段；server 400 的整句以 toast(role=alert) 显示。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchRecapSettings, putRecapSettings } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { RecapSettings } from "../../types";
import { RecapSection } from "./RecapSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchRecapSettings: vi.fn(), putRecapSettings: vi.fn() };
});

function snapshot(over: Partial<RecapSettings> = {}): RecapSettings {
  return { enabled: true, default_language: "auto",
    default_shape: "lines", languages: ["auto", "zh", "en"], source: { enabled: "default" }, ...over };
}

function renderSection() {
  return render(
    <LanguageContext.Provider value="en">
      <RecapSection />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchRecapSettings).mockReset().mockResolvedValue(snapshot());
  vi.mocked(putRecapSettings).mockReset();
});

afterEach(() => {
  cleanup();
});

describe("RecapSection", () => {
  it("hydrates from the server snapshot; there is no Slack draft toggle any more", async () => {
    renderSection();
    expect(((await screen.findByLabelText(/Generate a recap after each meeting/)) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByLabelText(/Slack/)).toBeNull();
    expect((screen.getByLabelText("Default language") as HTMLSelectElement).value).toBe("auto");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("saves exactly the two knobs and shows the receipt", async () => {
    vi.mocked(putRecapSettings).mockResolvedValue(snapshot({ enabled: false, default_language: "en" }));
    renderSection();
    fireEvent.click(await screen.findByLabelText(/Generate a recap after each meeting/));
    fireEvent.change(screen.getByLabelText("Default language"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putRecapSettings).toHaveBeenCalledWith({ enabled: false, default_language: "en" }));
    expect((await screen.findByRole("status")).textContent).toContain("Saved");
  });

  it("shows the server's rejection verbatim", async () => {
    vi.mocked(putRecapSettings).mockRejectedValue(new ApiError(400, {
      error: { code: "INVALID_FIELD", message: "default_language must be one of auto, zh, en", details: {} } }));
    renderSection();
    fireEvent.click(await screen.findByLabelText(/Generate a recap after each meeting/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("default_language must be one of auto, zh, en");
  });
});
