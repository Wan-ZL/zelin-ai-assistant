// 设置页「会议纪要」section（CONTRACT §63）：三把旋钮从 server 快照水合；Slack 草稿开关默认关；
// 保存 = 一次 PUT 三键、零多余字段；server 400 的整句以 toast(role=alert) 显示。
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
  return { enabled: true, default_language: "auto", slack_draft_enabled: false,
    languages: ["auto", "zh", "en"], source: { enabled: "default" }, ...over };
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
  it("hydrates from the server snapshot with the Slack draft toggle off", async () => {
    renderSection();
    const draft = await screen.findByLabelText(/Place the recap in my Slack drafts/);
    expect((draft as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText(/Generate a recap after each meeting/) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Default language") as HTMLSelectElement).value).toBe("auto");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("saves exactly the three knobs and shows the receipt", async () => {
    vi.mocked(putRecapSettings).mockResolvedValue(snapshot({ slack_draft_enabled: true, default_language: "en" }));
    renderSection();
    fireEvent.click(await screen.findByLabelText(/Place the recap in my Slack drafts/));
    fireEvent.change(screen.getByLabelText("Default language"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putRecapSettings).toHaveBeenCalledWith({
      enabled: true, default_language: "en", slack_draft_enabled: true }));
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
