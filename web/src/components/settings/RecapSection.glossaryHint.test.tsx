// 设置页「会议纪要」section——§63.14 术语表提示（issue #440）：组成来自 server 快照的只读 `glossary`
// （路径 / 文件在不在 / config 条数）；老 server 无此键就不说；这一格不进 PUT。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchRecapSettings, putRecapSettings } from "../../api";
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
  vi.mocked(fetchRecapSettings).mockReset();
  vi.mocked(putRecapSettings).mockReset();
});

afterEach(() => {
  cleanup();
});

describe("RecapSection glossary hint", () => {
  it("names the file, whether it exists, and the config count", async () => {
    vi.mocked(fetchRecapSettings).mockResolvedValue(snapshot({
      glossary: { path: "/home/z/state/recap-glossary.md", present: false, config_terms: 2 } }));
    renderSection();
    const hint = await screen.findByTestId("recap-glossary-hint");
    expect(hint.textContent).toContain("/home/z/state/recap-glossary.md");
    expect(hint.textContent).toContain("not created yet");
    expect(hint.textContent).toContain("2 line(s) in config.yaml recap.glossary");
  });

  it("says present when the file is there and stays quiet on an old server", async () => {
    vi.mocked(fetchRecapSettings).mockResolvedValue(snapshot({
      glossary: { path: "/home/z/state/recap-glossary.md", present: true, config_terms: 0 } }));
    renderSection();
    expect((await screen.findByTestId("recap-glossary-hint")).textContent).toContain("(present)");
    cleanup();
    resetStoreForTests();
    vi.mocked(fetchRecapSettings).mockResolvedValue(snapshot());
    renderSection();
    await screen.findByLabelText(/Generate a recap after each meeting/);
    expect(screen.queryByTestId("recap-glossary-hint")).toBeNull();
  });
});
