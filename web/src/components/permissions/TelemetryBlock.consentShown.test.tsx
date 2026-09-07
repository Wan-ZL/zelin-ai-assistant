// 披露块的 consent-surface 标记（CONTRACT §15 追记；owner 决策 D49；原生 Permissions.swift TelemetryConsent.markSurfaceShown）：
//   · 挂载 / 渲染披露块**不**请求标记（§15 issue #37 追记「永不在挂载时写」——web 宿主改为显式动作，不用 IntersectionObserver）；
//   · 复选框翻动 → PUT /api/settings/telemetry 成功 → 请 server 落标记一次（复选框就在披露块里，保存 = 块在屏上）；
//   · PUT 被拒 → 不落标记（错误行照旧）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import { markTelemetryConsentShown } from "../../telemetry";
import type { SettingsCatalog, SettingsSection } from "../../types";
import { TelemetryBlock } from "./TelemetryBlock";

vi.mock("../../telemetry", () => ({
  markTelemetryConsentShown: vi.fn(),
  trackEvent: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), putSettingsSection: vi.fn() };
});

function telemetrySection(captureInput: boolean): SettingsSection {
  return {
    id: "telemetry",
    title: { zh: "产品改进计划", en: "Product improvement" },
    help: { zh: "", en: "" },
    fields: [{
      key: "telemetry.capture_input", kind: "bool", label: { zh: "上传我输入的文本", en: "Upload the text I type" },
      help: { zh: "", en: "" }, default: false, choices: null, effective: captureInput, source: captureInput ? "override" : "default",
    }],
  };
}

function catalog(captureInput = false): SettingsCatalog {
  return { sections: [telemetrySection(captureInput)] };
}

const consentMock = vi.mocked(markTelemetryConsentShown);
const putMock = vi.mocked(putSettingsSection);

function renderBlock() {
  return render(<LanguageContext.Provider value="en"><TelemetryBlock /></LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  consentMock.mockReset().mockResolvedValue(undefined);
  putMock.mockReset();
  vi.mocked(fetchSettingsCatalog).mockReset().mockResolvedValue(catalog());
});

afterEach(() => cleanup());

describe("TelemetryBlock → consent-shown marker (D49)", () => {
  it("mounting and rendering the disclosure never requests the marker", async () => {
    renderBlock();
    const box = await screen.findByRole("checkbox", { name: "Share typed text to improve the product" });
    await waitFor(() => expect((box as HTMLInputElement).disabled).toBe(false));
    expect(fetchSettingsCatalog).toHaveBeenCalled();
    expect(consentMock).not.toHaveBeenCalled();
  });

  it("toggling the checkbox: PUT succeeds → marker requested once", async () => {
    putMock.mockResolvedValue(telemetrySection(true));
    renderBlock();
    const box = await screen.findByRole("checkbox", { name: "Share typed text to improve the product" });
    await waitFor(() => expect((box as HTMLInputElement).disabled).toBe(false));
    fireEvent.click(box);
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("telemetry", { "telemetry.capture_input": true }));
    await waitFor(() => expect(consentMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true));
  });

  it("toggling the checkbox: PUT rejected → no marker, the error line shows", async () => {
    putMock.mockRejectedValue(new Error("overrides file is not valid JSON"));
    renderBlock();
    const box = await screen.findByRole("checkbox", { name: "Share typed text to improve the product" });
    await waitFor(() => expect((box as HTMLInputElement).disabled).toBe(false));
    fireEvent.click(box);
    await screen.findByRole("alert");
    expect(putMock).toHaveBeenCalledTimes(1);
    expect(consentMock).not.toHaveBeenCalled();
  });
});
