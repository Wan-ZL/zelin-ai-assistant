// 安静时段两端的保存前校验（§28 追记 2026-09-12，issue #29）：
// draftRules.looksLikeClockTime 是 server `settings.CLOCK_TIME_RE` 的逐字镜像（act/lib/config 同一正则），
// checkReason 对 `clock_time` 只有 `shape` 一个 reason；FieldControl 渲染目录里那句 + aria-invalid，
// CatalogSection 据此不放行「保存」；空串仍是清键（server 也不查），合格但没归一的 `9:30` 照发 —— 归一归 server。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog, fetchSetup, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsField, SettingsSection } from "../../types";
import { CatalogSection } from "./CatalogSection";
import { checkReason, looksLikeClockTime, passesCheck } from "./draftRules";
import { fieldProblem } from "./FieldControl";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), putSettingsSection: vi.fn(), fetchSecrets: vi.fn(), fetchSetup: vi.fn() };
});

const SHAPE = {
  zh: "时间要写成 24 小时制的 HH:MM——例：22:00、08:30。",
  en: "Write the time as 24-hour HH:MM — e.g. 22:00, 08:30.",
};

const clockField = (over: Partial<SettingsField> = {}): SettingsField => ({
  key: "quiet_hours_start", kind: "string", label: { zh: "安静时段开始", en: "Quiet hours start" },
  help: { zh: "", en: "" }, default: "22:00", choices: null, effective: "22:00", source: "default",
  placeholder: { zh: "22:00", en: "22:00" },
  check: { kind: "clock_time", message: SHAPE },
  ...over,
});

const switchField = (): SettingsField => ({
  key: "quiet_hours_enabled", kind: "bool", label: { zh: "安静时段", en: "Quiet hours" },
  help: { zh: "", en: "" }, default: false, choices: null, effective: false, source: "default",
});

const section = (): SettingsSection => ({
  id: "notifications", title: { zh: "通知", en: "Notifications" }, help: { zh: "", en: "" },
  fields: [switchField(), clockField()],
});

describe("looksLikeClockTime — server CLOCK_TIME_RE 的镜像", () => {
  it("truth table", () => {
    for (const ok of ["22:00", "08:30", "0:00", "9:05", "00:00", "23:59", "19:59", "  7:30  "]) {
      expect(looksLikeClockTime(ok), ok).toBe(true);
    }
    for (const bad of ["", "10pm", "24:00", "9:60", "0830", "22:00-08:00", "7:5", "22：00", "1:005", "-1:00"]) {
      expect(looksLikeClockTime(bad), bad).toBe(false);
    }
  });

  it("checkReason / passesCheck: empty = clearing (never checked); only `shape` exists", () => {
    const field = clockField();
    expect(checkReason(field, "")).toBeNull();
    expect(checkReason(field, "   ")).toBeNull();
    expect(checkReason(field, undefined)).toBeNull();
    expect(checkReason(field, "22:00")).toBeNull();
    expect(checkReason(field, "9:30")).toBeNull();      // 合格但没归一：server 归一，web 不拦
    expect(checkReason(field, "24:00")).toBe("shape");
    expect(passesCheck(field, "24:00")).toBe(false);
    expect(passesCheck(field, "08:00")).toBe(true);
  });

  it("fieldProblem renders the catalog sentence in both languages", () => {
    const field = clockField();
    expect(fieldProblem(field, "10pm", "zh")).toBe(SHAPE.zh);
    expect(fieldProblem(field, "10pm", "en")).toBe(SHAPE.en);
    expect(fieldProblem(field, "22:00", "zh")).toBeNull();
    expect(fieldProblem(field, "", "en")).toBeNull();
  });
});

describe("CatalogSection · 通知 — 坏钟点不放行「保存」", () => {
  beforeEach(() => {
    resetStoreForTests();
    vi.mocked(fetchSettingsCatalog).mockReset();
    vi.mocked(putSettingsSection).mockReset();
    vi.mocked(fetchSecrets).mockReset();
    vi.mocked(fetchSetup).mockReset();
    vi.mocked(fetchSettingsCatalog).mockResolvedValue({ sections: [section()] });
  });
  afterEach(cleanup);

  it("a malformed time shows the sentence + aria-invalid and blocks Save; a good one saves", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue({
      ...section(), fields: [switchField(), clockField({ effective: "23:30", source: "override" })],
    });
    render(<LanguageContext.Provider value="en"><CatalogSection sectionId="notifications" /></LanguageContext.Provider>);
    const start = await screen.findByLabelText("Quiet hours start") as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;

    fireEvent.change(start, { target: { value: "10pm" } });
    expect(screen.getByText(SHAPE.en)).toBeTruthy();
    expect(start.getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);

    fireEvent.change(start, { target: { value: "24:00" } });
    expect(save.disabled).toBe(true);

    fireEvent.change(start, { target: { value: "23:30" } });
    expect(screen.queryByText(SHAPE.en)).toBeNull();
    expect(start.getAttribute("aria-invalid")).toBeNull();
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["notifications", { quiet_hours_start: "23:30" }]);
  });
});
