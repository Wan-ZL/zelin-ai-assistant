// 设置页「模型」section 行为（CONTRACT §57，D22）：
//   1) 两把旋钮从 server 快照水合，follow 选项标出 Claude Code 全局默认，下拉 = server 给的 canonical 全集；
//   2) 保存 = 一次 PUT 两键、零多余字段，成功 toast 说明「下一次调用生效，无需重启」；
//   3) server 校验失败（400 INVALID_FIELD）的整句原文以 toast(role=alert) 显示，不吞；
//   4) 自定义 = 自由文本 + 别名下线警告；非 canonical 保存后回显 server 的 warnings；
//   5) 「设为 <id>」走确认弹窗 → POST 只带 model → toast 含备份路径。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  fetchClaudeCodeDefault,
  fetchModelsSettings,
  postClaudeCodeDefault,
  putModelsSettings,
} from "../../api";
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

function snapshot(over: Partial<ModelsSettings> = {}): ModelsSettings {
  return {
    dispatch: "follow",
    pipeline: "follow",
    follow: "follow",
    canonical: CANONICAL,
    source: { dispatch: "default", pipeline: "default" },
    warnings: [],
    ...over,
  };
}

function ccDefault(over: Partial<ClaudeCodeDefault> = {}): ClaudeCodeDefault {
  return {
    model: "claude-fable-5-1[1m]",
    path: "/Users/me/.claude/settings.json",
    exists: true,
    parseable: true,
    canonical: false,
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

beforeEach(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.open = false;
    };
  }
  resetStoreForTests();
  vi.mocked(fetchModelsSettings).mockReset().mockResolvedValue(snapshot());
  vi.mocked(fetchClaudeCodeDefault).mockReset().mockResolvedValue(ccDefault());
  vi.mocked(putModelsSettings).mockReset();
  vi.mocked(postClaudeCodeDefault).mockReset();
});

afterEach(cleanup);

describe("ModelsSection", () => {
  it("hydrates both knobs on follow, names the global default, lists the server catalog", async () => {
    renderSection();
    const dispatch = (await screen.findByLabelText("Dispatch agents (hands)")) as HTMLSelectElement;
    const pipeline = screen.getByLabelText("Pipeline judgment (brain)") as HTMLSelectElement;
    expect(dispatch.value).toBe("follow");
    expect(pipeline.value).toBe("follow");
    expect(dispatch.options[0].textContent).toContain("claude-fable-5-1[1m]");
    const ids = Array.from(dispatch.options).map((o) => o.value);
    expect(ids).toEqual(["follow", ...CANONICAL, "__custom__"]);
    // save is disabled until something changes
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    // the global-default row shows the alias + its non-canonical warning
    expect(screen.getByText("claude-fable-5-1[1m]")).toBeTruthy();
    expect(screen.getByText(/is not a canonical id/)).toBeTruthy();
  });

  it("save PUTs both knobs with zero extra fields and toasts 'no restart needed'", async () => {
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ dispatch: "claude-opus-5",
      source: { dispatch: "override", pipeline: "default" } }));
    renderSection();
    const dispatch = (await screen.findByLabelText("Dispatch agents (hands)")) as HTMLSelectElement;
    fireEvent.change(dispatch, { target: { value: "claude-opus-5" } });
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await screen.findByText(/Saved — applies to the next call, no restart needed/);
    expect(putModelsSettings).toHaveBeenCalledTimes(1);
    const body = vi.mocked(putModelsSettings).mock.calls[0][0];
    expect(body).toEqual({ dispatch: "claude-opus-5", pipeline: "follow" });
    expect(Object.keys(body)).toHaveLength(2);
    // draft re-aligned to the server receipt: nothing dirty anymore
    await waitFor(() => expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("surfaces the server's validation sentence as an alert toast", async () => {
    vi.mocked(putModelsSettings).mockRejectedValue(new ApiError(400, {
      error: { code: "INVALID_FIELD", message: "a model id is letters, digits and . _ - [ ] only", details: { field: "pipeline" } },
    }));
    renderSection();
    const pipeline = (await screen.findByLabelText("Pipeline judgment (brain)")) as HTMLSelectElement;
    fireEvent.change(pipeline, { target: { value: "__custom__" } });
    const input = screen.getByLabelText("Pipeline judgment (brain) custom model id");
    fireEvent.change(input, { target: { value: "bad id" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const toast = await screen.findByRole("alert");
    expect(toast.textContent).toContain("a model id is letters, digits");
    expect(toast.className).toContain("is-error");
  });

  it("custom choice shows the alias warning and PUTs the typed id; server warnings echo back", async () => {
    const warning = "dispatch uses the non-canonical model id \"claude-opus-5-eap\"";
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot({ dispatch: "claude-opus-5-eap", warnings: [warning] }));
    renderSection();
    const dispatch = (await screen.findByLabelText("Dispatch agents (hands)")) as HTMLSelectElement;
    fireEvent.change(dispatch, { target: { value: "__custom__" } });
    expect(screen.getByText(/Aliases \/ suffixes .* can disappear any day/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Dispatch agents (hands) custom model id"),
      { target: { value: " claude-opus-5-eap " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByRole("status");
    expect(vi.mocked(putModelsSettings).mock.calls[0][0]).toEqual({ dispatch: "claude-opus-5-eap", pipeline: "follow" });
    // the section keeps the select on Custom with the id in the box, and echoes the server warning
    await waitFor(() => expect((screen.getByLabelText("Dispatch agents (hands)") as HTMLSelectElement).value).toBe("__custom__"));
    expect(screen.getAllByText(warning).length).toBeGreaterThan(0);
  });

  it("custom with an empty box saves as follow (never an empty --model)", async () => {
    vi.mocked(putModelsSettings).mockResolvedValue(snapshot());
    renderSection();
    const pipeline = (await screen.findByLabelText("Pipeline judgment (brain)")) as HTMLSelectElement;
    fireEvent.change(pipeline, { target: { value: "claude-sonnet-5" } });
    fireEvent.change(pipeline, { target: { value: "__custom__" } });
    // nothing typed → effective follow → equals server → save stays disabled
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("Set to <id> confirms in a dialog, POSTs only the model, toasts the backup path", async () => {
    vi.mocked(postClaudeCodeDefault).mockResolvedValue({
      model: "claude-fable-5", previous: "claude-fable-5-1[1m]",
      backup: "/Users/me/.claude/settings.json.bak-20260901T120000Z", path: "/Users/me/.claude/settings.json",
    });
    vi.mocked(fetchClaudeCodeDefault)
      .mockResolvedValueOnce(ccDefault())
      .mockResolvedValue(ccDefault({ model: "claude-fable-5", canonical: true }));
    renderSection();
    const button = await screen.findByRole("button", { name: "Set to claude-fable-5" });
    fireEvent.click(button);
    // nothing posted before the confirmation
    expect(postClaudeCodeDefault).not.toHaveBeenCalled();
    expect(screen.getByText("Change the Claude Code global default?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Set it" }));

    const toast = await screen.findByRole("status");
    expect(toast.textContent).toContain("settings.json.bak-20260901T120000Z");
    expect(postClaudeCodeDefault).toHaveBeenCalledWith("claude-fable-5");
    // the row now reads the fresh value and the alias warning is gone
    await waitFor(() => expect(screen.queryByText(/is not a canonical id/)).toBeNull());
    expect(screen.getByText("claude-fable-5", { selector: "code" })).toBeTruthy();
  });

  it("cancelling the dialog posts nothing", async () => {
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Set to claude-fable-5" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(postClaudeCodeDefault).not.toHaveBeenCalled();
    expect(screen.queryByText("Change the Claude Code global default?")).toBeNull();
  });

  it("an unreadable settings.json disables the one-click and says so", async () => {
    vi.mocked(fetchClaudeCodeDefault).mockResolvedValue(ccDefault({ model: null, parseable: false, canonical: true }));
    renderSection();
    await screen.findByLabelText("Dispatch agents (hands)");
    expect(screen.getByRole("alert").textContent).toContain("not valid JSON");
    expect((screen.getByRole("button", { name: /Set to/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("read failure renders the error instead of a blank section", async () => {
    vi.mocked(fetchModelsSettings).mockRejectedValue(new ApiError(0, {
      error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." },
    }));
    renderSection();
    expect((await screen.findByRole("alert")).textContent).toContain("temporarily unavailable");
  });
});
