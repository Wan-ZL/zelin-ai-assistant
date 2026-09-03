// 设置页「显示」section（CONTRACT §54.1 第 12 项）：三个 segmented control 从 server 词表渲染、
// 可访问名 = 组名 + 档名；点一档 = 立刻落 <html> data-* 再 PUT 恰好那一键；server 拒绝 → 回滚 + alert；
// 预览行用看板真实类名（task-card / chip / btn）且不自设作用域（跟着 :root 走）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchDisplaySettings, putDisplaySettings } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { DisplaySettings } from "../../types";
import { DisplaySection } from "./DisplaySection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchDisplaySettings: vi.fn(), putDisplaySettings: vi.fn() };
});

function snapshot(over: Partial<DisplaySettings> = {}): DisplaySettings {
  return {
    text_size: "m", text_weight: "regular", stroke: "normal",
    text_sizes: ["s", "m", "l", "xl"], text_weights: ["regular", "medium", "bold"], strokes: ["thin", "normal", "thick"],
    source: { text_size: "default", text_weight: "default", stroke: "default" }, ...over,
  };
}

function renderSection(language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <DisplaySection />
    </LanguageContext.Provider>,
  );
}

const html = () => document.documentElement;

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  for (const key of ["textSize", "textWeight", "stroke"]) delete html().dataset[key];
  vi.mocked(fetchDisplaySettings).mockReset().mockResolvedValue(snapshot());
  vi.mocked(putDisplaySettings).mockReset();
});

afterEach(() => {
  cleanup();
  for (const key of ["textSize", "textWeight", "stroke"]) delete html().dataset[key];
});

describe("DisplaySection", () => {
  it("renders three labelled groups from the server vocabulary and applies the snapshot to <html>", async () => {
    renderSection();
    const size = await screen.findByRole("group", { name: "Text size" });
    expect(size.querySelectorAll("input[type=radio]")).toHaveLength(4);
    expect(screen.getByRole("group", { name: "Text weight" }).querySelectorAll("input[type=radio]")).toHaveLength(3);
    expect(screen.getByRole("group", { name: "Outline width" }).querySelectorAll("input[type=radio]")).toHaveLength(3);
    // 「默认」在文字大小与线条粗细两组各一枚，都应选中
    const defaults = screen.getAllByRole("radio", { name: "Default" }) as HTMLInputElement[];
    expect(defaults).toHaveLength(2);
    expect(defaults.every((radio) => radio.checked)).toBe(true);
    expect((screen.getByRole("radio", { name: "Regular" }) as HTMLInputElement).checked).toBe(true);
    await waitFor(() => expect(html().getAttribute("data-text-size")).toBe("m"));
    expect(html().getAttribute("data-text-weight")).toBe("regular");
    expect(html().getAttribute("data-stroke")).toBe("normal");
  });

  it("segments carry Apple-style labels in Chinese too", async () => {
    renderSection("zh");
    await screen.findByRole("group", { name: "文字大小" });
    for (const name of ["小", "大", "特大", "常规", "中等", "粗体", "细", "粗"]) {
      expect(screen.getByRole("radio", { name })).toBeTruthy();
    }
    expect(screen.getAllByRole("radio", { name: "默认" })).toHaveLength(2);
    expect(screen.getByRole("group", { name: "线条粗细" })).toBeTruthy();
  });

  it("choosing a tier applies it to <html> immediately and PUTs exactly that one key", async () => {
    let resolvePut: (value: DisplaySettings) => void = () => {};
    vi.mocked(putDisplaySettings).mockImplementation(() => new Promise((resolve) => { resolvePut = resolve; }));
    renderSection();
    fireEvent.click(await screen.findByRole("radio", { name: "Extra large" }));
    // optimistic: the page already reads xl while the PUT is in flight
    expect(html().getAttribute("data-text-size")).toBe("xl");
    expect(putDisplaySettings).toHaveBeenCalledTimes(1);
    expect(putDisplaySettings).toHaveBeenCalledWith({ text_size: "xl" });
    resolvePut(snapshot({ text_size: "xl", source: { text_size: "override" } }));
    await waitFor(() => expect((screen.getByRole("radio", { name: "Extra large" }) as HTMLInputElement).checked).toBe(true));
    expect(html().getAttribute("data-text-size")).toBe("xl");
    expect(JSON.parse(window.localStorage.getItem("zai.display") ?? "{}").text_size).toBe("xl");
    expect((await screen.findByRole("status")).textContent).toContain("Applied and saved.");
  });

  it("a server rejection rolls <html> back to the last snapshot and shows the message verbatim", async () => {
    vi.mocked(putDisplaySettings).mockRejectedValue(new ApiError(400, {
      error: { code: "INVALID_FIELD", message: "stroke must be one of thin, normal, thick", details: {} } }));
    renderSection();
    fireEvent.click(await screen.findByRole("radio", { name: "Thick" }));
    expect((await screen.findByRole("alert")).textContent).toContain("stroke must be one of thin, normal, thick");
    expect(html().getAttribute("data-stroke")).toBe("normal");
    expect((screen.getByRole("radio", { name: "Thick" }) as HTMLInputElement).checked).toBe(false);
  });

  it("clicking the already-selected tier is a no-op", async () => {
    renderSection();
    fireEvent.click(await screen.findByRole("radio", { name: "Regular" }));
    expect(putDisplaySettings).not.toHaveBeenCalled();
  });

  it("the preview is real board chrome (card / chips / buttons) with no scoped data-* of its own", async () => {
    renderSection();
    const preview = await screen.findByRole("img", { name: "Preview" });
    expect(preview.querySelector(".task-card .card-title")).toBeTruthy();
    expect(preview.querySelectorAll(".chip").length).toBeGreaterThanOrEqual(3);
    expect(preview.querySelectorAll(".btn").length).toBeGreaterThanOrEqual(3);
    expect(preview.querySelector("button")).toBeNull();
    for (const attr of ["data-text-size", "data-text-weight", "data-stroke"]) expect(preview.hasAttribute(attr)).toBe(false);
  });

  it("shows the read error when the snapshot cannot be fetched", async () => {
    vi.mocked(fetchDisplaySettings).mockRejectedValue(new ApiError(500, {
      error: { code: "INTERNAL", message: "server is down", details: {} } }));
    renderSection();
    expect((await screen.findByText("server is down")).textContent).toBe("server is down");
  });
});
