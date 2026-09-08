// 设置页「模型」的第三把旋钮「回退模型」（CONTRACT §59，owner 决策 D53）：
//   1) 从 server 快照水合：哨兵是 off（文案「关闭回退」）、出厂值 claude-opus-5[1m] 以「（默认）」单列在 canonical 之前，
//      默认值不算自定义（不弹别名警告）；
//   2) 选「关闭回退」→ PUT 带 fallback: "off"，两把 D22 旋钮原样同车；选 canonical id / 自定义 → PUT 带那个 id；
//   3) 自定义框留空 = 出厂值（与 server「空白 = 出厂值」同一规则；留空不该悄悄关掉回退，off 只经「关闭回退」显式选项）；
//   4) server 给的 fallback 专用 warning 原句回显；server 400 的整句以 alert toast 显示。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchClaudeCodeDefault, fetchModelsSettings, putModelsSettings } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { ClaudeCodeDefault, ModelsSettings } from "../../types";
import { ModelsSection } from "./ModelsSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchModelsSettings: vi.fn(),
    putModelsSettings: vi.fn(),
    fetchClaudeCodeDefault: vi.fn(),
    postClaudeCodeDefault: vi.fn(),
  };
});

const CANONICAL = ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"];
const DEFAULT = "claude-opus-5[1m]";

function snapshot(over: Partial<ModelsSettings> = {}): ModelsSettings {
  return {
    dispatch: "follow",
    pipeline: "follow",
    fallback: DEFAULT,
    follow: "follow",
    off: "off",
    fallback_default: DEFAULT,
    canonical: CANONICAL,
    source: { dispatch: "default", pipeline: "default", fallback: "default" },
    warnings: [],
    ...over,
  };
}

const ccDefault: ClaudeCodeDefault = {
  model: "claude-fable-5-1[1m]",
  path: "/Users/me/.claude/settings.json",
  exists: true,
  parseable: true,
  canonical: false,
};

function renderSection() {
  return render(
    <LanguageContext.Provider value="en">
      <ModelsSection />
    </LanguageContext.Provider>,
  );
}

async function fallbackSelect(): Promise<HTMLSelectElement> {
  return (await screen.findByLabelText("Fallback model")) as HTMLSelectElement;
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchModelsSettings).mockReset().mockResolvedValue(snapshot());
  vi.mocked(fetchClaudeCodeDefault).mockReset().mockResolvedValue(ccDefault);
  vi.mocked(putModelsSettings).mockReset();
});

afterEach(cleanup);

describe("ModelsSection — fallback knob (D53)", () => {
  it("hydrates on the product default, lists off → default → canonical → custom, no alias warning", async () => {
    renderSection();
    const select = await fallbackSelect();
    expect(select.value).toBe(DEFAULT);
    const options = Array.from(select.options);
    expect(options.map((o) => o.value)).toEqual(["off", DEFAULT, ...CANONICAL, "__custom__"]);
    expect(options[0].textContent).toContain("No fallback");
    expect(options[1].textContent).toBe(`${DEFAULT} (default)`);
    // the default is a fixed choice, not "custom": no alias warning, no text box
    expect(screen.queryByLabelText("Fallback model custom model id")).toBeNull();
    expect(screen.queryByText(/Aliases \/ suffixes .* can disappear any day/)).toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("hydrates on off when the server says so", async () => {
    vi.mocked(fetchModelsSettings).mockResolvedValue(snapshot({ fallback: "off",
      source: { dispatch: "default", pipeline: "default", fallback: "override" } }));
    renderSection();
    expect((await fallbackSelect()).value).toBe("off");
  });

  it("choosing 'off' PUTs fallback: off with the two D22 knobs unchanged", async () => {
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ fallback: "off",
      source: { dispatch: "default", pipeline: "default", fallback: "override" } }));
    renderSection();
    fireEvent.change(await fallbackSelect(), { target: { value: "off" } });
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await screen.findByText(/Saved — applies to the next call, no restart needed/);
    expect(vi.mocked(putModelsSettings).mock.calls[0][0]).toEqual({ dispatch: "follow", pipeline: "follow", fallback: "off" });
    await waitFor(() => expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true));
    expect((screen.getByLabelText("Fallback model") as HTMLSelectElement).value).toBe("off");
  });

  it("choosing a canonical id PUTs it", async () => {
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ fallback: "claude-sonnet-5" }));
    renderSection();
    fireEvent.change(await fallbackSelect(), { target: { value: "claude-sonnet-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0]).toEqual({ dispatch: "follow", pipeline: "follow", fallback: "claude-sonnet-5" });
  });

  it("back to the default from off is a dirty change that PUTs the default id", async () => {
    vi.mocked(fetchModelsSettings).mockResolvedValue(snapshot({ fallback: "off" }));
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot());
    renderSection();
    fireEvent.change(await fallbackSelect(), { target: { value: DEFAULT } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0].fallback).toBe(DEFAULT);
  });

  it("custom shows the alias warning and PUTs the typed id; the server's fallback warning echoes back", async () => {
    const warning = "fallback uses the non-canonical model id \"claude-opus-5-eap\"";
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ fallback: "claude-opus-5-eap", warnings: [warning] }));
    renderSection();
    fireEvent.change(await fallbackSelect(), { target: { value: "__custom__" } });
    expect(screen.getByText(/Aliases \/ suffixes .* can disappear any day/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Fallback model custom model id"), { target: { value: " claude-opus-5-eap " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0].fallback).toBe("claude-opus-5-eap");
    await waitFor(() => expect((screen.getByLabelText("Fallback model") as HTMLSelectElement).value).toBe("__custom__"));
    expect(screen.getAllByText(warning).length).toBeGreaterThan(0);
  });

  it("custom with an empty box means the product default, never off and never an empty --fallback-model", async () => {
    // hydrated on off: picking 自定义… and leaving the box blank must NOT silently keep the
    // fallback off — blank reads as the default (same rule as the server's "blank = default")
    vi.mocked(fetchModelsSettings).mockResolvedValue(snapshot({ fallback: "off" }));
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot());
    renderSection();
    const select = await fallbackSelect();
    fireEvent.change(select, { target: { value: "__custom__" } });
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0].fallback).toBe(DEFAULT);
  });

  it("custom with an empty box while already on the default is not a change", async () => {
    renderSection();
    const select = await fallbackSelect();
    fireEvent.change(select, { target: { value: "claude-sonnet-5" } });
    fireEvent.change(select, { target: { value: "__custom__" } });
    // nothing typed → effective default → equals the server value → not dirty
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("off is reachable only through the explicit 'No fallback' option", async () => {
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ fallback: "off" }));
    renderSection();
    const select = await fallbackSelect();
    fireEvent.change(select, { target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("Fallback model custom model id"), { target: { value: "   " } });
    // whitespace-only custom text is blank → default → still equal to the server → not dirty
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Fallback model"), { target: { value: "off" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0].fallback).toBe("off");
  });

  it("the D22 knobs do not change their follow sentinel", async () => {
    renderSection();
    const dispatch = (await screen.findByLabelText("Dispatch agents (hands)")) as HTMLSelectElement;
    expect(Array.from(dispatch.options).map((o) => o.value)).toEqual(["follow", ...CANONICAL, "__custom__"]);
  });

  it("surfaces the server's validation sentence for the fallback field as an alert toast", async () => {
    vi.mocked(putModelsSettings).mockRejectedValue(new ApiError(400, {
      error: { code: "INVALID_FIELD", message: "a model id is letters, digits and . _ - [ ] only; off = no fallback", details: { field: "fallback" } },
    }));
    renderSection();
    fireEvent.change(await fallbackSelect(), { target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("Fallback model custom model id"), { target: { value: "bad id" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const toast = await screen.findByRole("alert");
    expect(toast.textContent).toContain("off = no fallback");
  });
});
