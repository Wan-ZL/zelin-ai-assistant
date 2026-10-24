// 设置页「模型」section 行为（CONTRACT §59，D22 / D53）：
//   1) 两把旋钮（pipeline / fallback）从 server 快照水合，下拉 = server 给的 canonical 全集（+ 不在表里的出厂值单列）；
//   2) 保存 = 一次 PUT 两键、零多余字段，成功 toast 说明「下一次调用生效，无需重启」；
//   3) server 校验失败（400 INVALID_FIELD）的整句原文以 toast(role=alert) 显示，不吞；
//   4) 自定义 = 自由文本 + 别名下线警告；非 canonical 保存后回显 server 的 warnings；自定义框留空 = 出厂值。
// v2.0.0 退役（全文见 tag full）：dispatch 旋钮、「跟随 Claude Code 全局默认」哨兵与「设为 <id>」（写 ~/.claude/settings.json）——
// §78 隔离下 app 不读不写 owner 的 Claude Code 设置。fallback 旋钮的细节见 ModelsSection.fallback.test.tsx。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchModelsSettings, putModelsSettings } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { ModelsSettings } from "../../types";
import { ModelsSection } from "./ModelsSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchModelsSettings: vi.fn(), putModelsSettings: vi.fn() };
});

const CANONICAL = ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"];

function snapshot(over: Partial<ModelsSettings> = {}): ModelsSettings {
  return {
    pipeline: "claude-opus-5",
    fallback: "claude-opus-5[1m]",
    off: "off",
    pipeline_default: "claude-opus-5",
    fallback_default: "claude-opus-5[1m]",
    canonical: CANONICAL,
    source: { pipeline: "default", fallback: "default" },
    warnings: [],
    ...over,
  };
}

function renderSection() {
  return render(
    <LanguageContext.Provider value="en">
      <ModelsSection />
    </LanguageContext.Provider>,
  );
}

const pipelineSelect = async () => (await screen.findByLabelText("Pipeline judgment (brain)")) as HTMLSelectElement;

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchModelsSettings).mockReset().mockResolvedValue(snapshot());
  vi.mocked(putModelsSettings).mockReset();
});

afterEach(cleanup);

describe("ModelsSection", () => {
  it("hydrates the two knobs, lists the server catalog, never mentions the Claude Code global default", async () => {
    renderSection();
    const pipeline = await pipelineSelect();
    expect(pipeline.value).toBe("claude-opus-5");
    expect(Array.from(pipeline.options).map((o) => o.value)).toEqual([...CANONICAL, "__custom__"]);
    expect((screen.getByLabelText("Fallback model") as HTMLSelectElement).value).toBe("claude-opus-5[1m]");
    expect(screen.queryByText(/Follow Claude Code default/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Set to/ })).toBeNull();
    expect(screen.queryByText(/settings\.json/)).toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("save PUTs the two knobs with zero extra fields and toasts 'no restart needed'", async () => {
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ pipeline: "claude-sonnet-5", source: { pipeline: "override", fallback: "default" } }));
    renderSection();
    fireEvent.change(await pipelineSelect(), { target: { value: "claude-sonnet-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Saved — applies to the next call, no restart needed/);
    expect(vi.mocked(putModelsSettings).mock.calls[0][0]).toEqual({ pipeline: "claude-sonnet-5", fallback: "claude-opus-5[1m]" });
    await waitFor(() => expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("surfaces the server's validation sentence as an alert toast", async () => {
    vi.mocked(putModelsSettings).mockRejectedValue(new ApiError(400, {
      error: { code: "INVALID_FIELD", message: "a model id is letters, digits and . _ - [ ] only", details: { field: "pipeline" } },
    }));
    renderSection();
    fireEvent.change(await pipelineSelect(), { target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("Pipeline judgment (brain) custom model id"), { target: { value: "bad id" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("letters, digits");
  });

  it("custom choice shows the alias warning and PUTs the typed id; server warnings echo back", async () => {
    const warning = "pipeline uses the non-canonical model id \"claude-opus-5-eap\"";
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ pipeline: "claude-opus-5-eap", warnings: [warning] }));
    renderSection();
    fireEvent.change(await pipelineSelect(), { target: { value: "__custom__" } });
    expect(screen.getByText(/Aliases \/ suffixes .* can disappear any day/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Pipeline judgment (brain) custom model id"), { target: { value: " claude-opus-5-eap " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0].pipeline).toBe("claude-opus-5-eap");
    expect(screen.getAllByText(warning).length).toBeGreaterThan(0);
  });

  it("custom with an empty box saves as the factory default (never an empty --model)", async () => {
    vi.mocked(fetchModelsSettings).mockResolvedValue(snapshot({ pipeline: "claude-sonnet-5" }));
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot());
    renderSection();
    fireEvent.change(await pipelineSelect(), { target: { value: "__custom__" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0].pipeline).toBe("claude-opus-5");
  });

  it("read failure renders the error instead of a blank section", async () => {
    vi.mocked(fetchModelsSettings).mockRejectedValue(new ApiError(0, { error: { code: "READ_FAILED", message: "models unreachable" } }));
    renderSection();
    expect((await screen.findByRole("alert")).textContent).toBe("models unreachable");
  });
});
