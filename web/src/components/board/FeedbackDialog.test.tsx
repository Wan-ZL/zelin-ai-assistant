// 提建议弹窗的键盘纪律 + §29 明示条款（CONTRACT §41 2026-09-05 追记「弹窗一律按钮提交，Enter 换行（D35 同款）」）：
//   1) Enter / Shift+Enter / ⌘Enter / Ctrl+Enter 都不提交，Enter 也不被 preventDefault（浏览器原生换行）；
//      IME 组合中的回车（isComposing）同样不提交——半截拼音再也不会被上传、还公开成 issue；
//   2) 只有「发送」按钮提交：trimmed 非空才可点，换行原样进 wire text；
//   3) 正文 = §29 的明示条款（上传给维护者 · 不受匿名统计开关限制 · 勾选公开进公开 repo 的 issue 列表），
//      不再有「本地先落 state/feedback/」的本地闭环暗示，也没有「↩ 发送 · ⇧↩ 换行」提示句；
//   4) 公开勾选的默认态读 settings 目录 general.feedback_publish_default，改勾选写回 PUT /api/settings/general。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsCatalog } from "../../types";
import { FeedbackDialog } from "./FeedbackDialog";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchSettingsCatalog: vi.fn(),
  putSettingsSection: vi.fn(),
}));

function catalog(publishDefault: boolean): SettingsCatalog {
  return {
    sections: [{
      id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
      fields: [{ key: "feedback_publish_default", kind: "bool", label: { zh: "", en: "" }, help: { zh: "", en: "" },
        default: false, choices: null, effective: publishDefault, source: publishDefault ? "override" : "default" }],
    }],
  };
}

async function mount(language: "zh" | "en" = "en", ids: string[] = []) {
  const onSubmit = vi.fn();
  const onCancel = vi.fn();
  await act(async () => {
    render(
      <LanguageContext.Provider value={language}>
        <FeedbackDialog ids={ids} onSubmit={onSubmit} onCancel={onCancel} />
      </LanguageContext.Provider>,
    );
  });
  const field = screen.getByPlaceholderText(language === "en" ? "Your feedback…" : "建议内容…") as HTMLTextAreaElement;
  const button = screen.getByRole("button", { name: language === "en" ? "Send" : "发送" }) as HTMLButtonElement;
  return { field, button, onSubmit, onCancel };
}

beforeEach(() => {
  // jsdom <dialog> 兜底（同 parity.test.tsx）：老版本没有 showModal/close
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(false));
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(putSettingsSection).mockResolvedValue(catalog(true).sections[0]);
});

afterEach(cleanup);

describe("FeedbackDialog — Enter is a newline, only the button submits (D35 for dialogs)", () => {
  for (const [label, init] of [
    ["Enter", {}],
    ["Shift+Enter", { shiftKey: true }],
    ["⌘Enter", { metaKey: true }],
    ["Ctrl+Enter", { ctrlKey: true }],
    ["Enter while composing (IME)", { isComposing: true }],
  ] as const) {
    it(`${label} does not submit and is not intercepted`, async () => {
      const { field, onSubmit } = await mount();
      fireEvent.change(field, { target: { value: "ni" } });
      const notPrevented = fireEvent.keyDown(field, { key: "Enter", code: "Enter", ...init });
      expect(notPrevented).toBe(true); // 没 preventDefault → 浏览器原生换行
      expect(onSubmit).not.toHaveBeenCalled();
      expect(field.value).toBe("ni"); // 草稿还在（jsdom 不模拟默认动作，换行由浏览器做）
    });
  }

  it("has no keyboard hint line — the dialog advertises no send key", async () => {
    await mount("zh");
    expect(screen.queryByText(/↩ 发送/)).toBeNull();
    expect(document.querySelector(".zai-dialog .dialog-note")).toBeNull();
    cleanup();
    await mount("en");
    expect(screen.queryByText(/↩ send/)).toBeNull();
  });

  it("the Send button submits the trimmed draft with newlines preserved, ids sorted + deduped", async () => {
    const { field, button, onSubmit } = await mount("en", ["R-2", "R-1", "R-2"]);
    expect(button.disabled).toBe(true);
    fireEvent.change(field, { target: { value: "  line one\nline two  " } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({ action: "feedback", text: "line one\nline two", publish: false, ids: ["R-1", "R-2"] });
  });

  it("whitespace-only drafts (including bare newlines) keep Send disabled", async () => {
    const { field, button, onSubmit } = await mount();
    fireEvent.change(field, { target: { value: "\n\n  \n" } });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("Cancel calls onCancel without submitting", async () => {
    const { field, onSubmit, onCancel } = await mount();
    fireEvent.change(field, { target: { value: "draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("FeedbackDialog — §29 upload disclosure", () => {
  it("zh body discloses the upload to the maintainer, the anonymous-stats exemption and the public issue list", async () => {
    await mount("zh");
    const body = document.querySelector(".zai-dialog .dialog-body")?.textContent ?? "";
    expect(body).toContain("建议全文与所选卡片的标题快照会上传给维护者");
    expect(body).toContain("即使你关闭了匿名统计");
    expect(body).toContain("请勿包含敏感信息");
    expect(body).toContain("勾选公开时还会出现在公开 GitHub 仓库的 issue 列表里");
    // 原生注释的红线：不得暗示是本地闭环
    expect(body).not.toContain("本地先落");
    expect(body).not.toContain("state/feedback/");
  });

  it("en body carries the same disclosure", async () => {
    await mount("en");
    const body = document.querySelector(".zai-dialog .dialog-body")?.textContent ?? "";
    expect(body).toContain("uploaded to the maintainer");
    expect(body).toContain("even with anonymous stats off");
    expect(body).toContain("avoid sensitive details");
    expect(body).toContain("public GitHub repository's issue list");
    expect(body).not.toContain("Stored locally");
  });

  it("the publish checkbox still says text only, no card content (feedback_sync issue body = text + time + version)", async () => {
    await mount("en");
    expect(screen.getByLabelText(/Also publish to the GitHub feedback tracker \(your text only, no card content\)/)).toBeTruthy();
  });
});

describe("FeedbackDialog — publish default from the settings catalog", () => {
  it("reads general.feedback_publish_default once the catalog lands and sends publish:true", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(true));
    const { field, button, onSubmit } = await mount();
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.change(field, { target: { value: "publish me" } });
    fireEvent.click(button);
    expect(onSubmit).toHaveBeenCalledWith({ action: "feedback", text: "publish me", publish: true, ids: [] });
  });

  it("toggling the checkbox remembers the choice through PUT /api/settings/general", async () => {
    await mount();
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
    expect(putSettingsSection).toHaveBeenCalledWith("general", { feedback_publish_default: true });
  });
});
