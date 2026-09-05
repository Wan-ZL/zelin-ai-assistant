// 会话 id 的保存前校验（§68.7 追记；原生 SettingsMaintainer.validateSessionID 在 saveSessionID 里拦）：
// draftRules.sessionIdProblem 是 server `session_id_problem` 的逐字镜像（首连字符 / 字符白名单 / ≤64 字）；
// FieldControl.fieldProblem 按 reason 取目录 `check.reasons` 的分句、对不上用主句；CatalogSection 据此不放行「保存」；
// 动态 placeholder（生效默认的灰字）原样渲染进输入框。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog, fetchSetup, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsField, SettingsSection } from "../../types";
import { CatalogSection } from "./CatalogSection";
import { checkReason, passesCheck, sessionIdProblem } from "./draftRules";
import { checkSentence, fieldProblem } from "./FieldControl";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), putSettingsSection: vi.fn(), fetchSecrets: vi.fn(), fetchSetup: vi.fn() };
});

const CHARSET = { zh: "会话 ID 只能包含字母、数字和连字符（-）——从 claude 里复制的会话 ID 就是这个样子。", en: "A session id may only contain letters, digits, and hyphens (-) — the id you copy from claude is exactly that shape." };
const HYPHEN = { zh: "会话 ID 不能以连字符（-）开头——那是命令行选项的形状，不是会话 ID。", en: "A session id may not start with a hyphen (-) — that's the shape of a command-line flag, not a session id." };

const sessionField = (over: Partial<SettingsField> = {}): SettingsField => ({
  key: "maintainer_session_id", kind: "string", label: { zh: "续接的会话 id", en: "Session id to resume" }, help: { zh: "", en: "" },
  default: "", choices: null, effective: "", source: "default",
  placeholder: { zh: "例：6f9619ff-8b86-d011-b42d-00cf4fc964ff", en: "e.g. 6f9619ff-8b86-d011-b42d-00cf4fc964ff" },
  check: { kind: "session_id", message: CHARSET, reasons: { leading_hyphen: HYPHEN } },
  ...over,
});

const repoField = (): SettingsField => ({
  key: "maintainer_repo_path", kind: "string", label: { zh: "本软件的仓库路径", en: "This software's repo path" }, help: { zh: "", en: "" },
  default: "", choices: null, effective: "", source: "default", path: "dir", path_exists: null,
  placeholder: { zh: "/Users/demo/Projects/zelin-ai-assistant", en: "/Users/demo/Projects/zelin-ai-assistant" },
});

const section = (): SettingsSection => ({
  id: "maintainer", title: { zh: "开发者 · 开发会话", en: "Developer session" }, help: { zh: "", en: "" },
  terminal_app_name: "Terminal", fields: [repoField(), sessionField()],
});

describe("sessionIdProblem — server session_id_problem 的镜像", () => {
  it("rule table", () => {
    expect(sessionIdProblem("6f9619ff-8b86-d011-b42d-00cf4fc964ff")).toBeNull();
    expect(sessionIdProblem("  abc-123  ")).toBeNull();
    expect(sessionIdProblem("a")).toBeNull();
    expect(sessionIdProblem("a".repeat(64))).toBeNull();
    expect(sessionIdProblem("-abc")).toBe("leading_hyphen");
    expect(sessionIdProblem("--dangerously-skip-permissions")).toBe("leading_hyphen");
    expect(sessionIdProblem("a b")).toBe("charset");
    expect(sessionIdProblem("abc; rm -rf /")).toBe("charset");
    expect(sessionIdProblem("abc_def")).toBe("charset");
    expect(sessionIdProblem("a".repeat(65))).toBe("charset");
  });

  it("checkReason / passesCheck: empty = clearing, never checked; unknown kinds pass", () => {
    const field = sessionField();
    expect(checkReason(field, "")).toBeNull();
    expect(checkReason(field, "   ")).toBeNull();
    expect(checkReason(field, undefined)).toBeNull();
    expect(checkReason(field, "-x")).toBe("leading_hyphen");
    expect(checkReason(field, "x y")).toBe("charset");
    expect(passesCheck(field, "-x")).toBe(false);
    expect(passesCheck(field, "ok-id")).toBe(true);
    expect(passesCheck(sessionField({ check: { kind: "phone", message: CHARSET } }), "-x")).toBe(true);
    expect(passesCheck(sessionField({ check: undefined }), "-x")).toBe(true);
  });
});

describe("fieldProblem / checkSentence — 分句按 reason，主句兜底", () => {
  it("picks the hyphen variant for a leading hyphen and the charset sentence otherwise, in both languages", () => {
    const field = sessionField();
    expect(fieldProblem(field, "-abc", "zh")).toBe(HYPHEN.zh);
    expect(fieldProblem(field, "-abc", "en")).toBe(HYPHEN.en);
    expect(fieldProblem(field, "a b", "zh")).toBe(CHARSET.zh);
    expect(fieldProblem(field, "a b", "en")).toBe(CHARSET.en);
    expect(fieldProblem(field, "abc", "zh")).toBeNull();
    expect(fieldProblem(field, "", "zh")).toBeNull();
  });

  it("an older server without `reasons` falls back to the main sentence for every failure", () => {
    const field = sessionField({ check: { kind: "session_id", message: CHARSET } });
    expect(fieldProblem(field, "-abc", "zh")).toBe(CHARSET.zh);
    expect(fieldProblem(field, "a b", "en")).toBe(CHARSET.en);
  });

  it("checkSentence maps 400 details to the same catalog sentence; other checks / no field → null", () => {
    const field = sessionField();
    expect(checkSentence(field, { field: "maintainer_session_id", check: "session_id", reason: "leading_hyphen" }, "zh")).toBe(HYPHEN.zh);
    expect(checkSentence(field, { check: "session_id" }, "en")).toBe(CHARSET.en);
    expect(checkSentence(field, { check: "session_id", reason: "unknown_reason" }, "en")).toBe(CHARSET.en);
    expect(checkSentence(field, { check: "email" }, "zh")).toBeNull();
    expect(checkSentence(field, null, "zh")).toBeNull();
    expect(checkSentence(undefined, { check: "session_id" }, "zh")).toBeNull();
    expect(checkSentence(sessionField({ check: undefined }), { check: "session_id" }, "zh")).toBeNull();
  });
});

describe("CatalogSection · maintainer — 保存前拦 + 动态灰字", () => {
  beforeEach(() => {
    resetStoreForTests();
    vi.mocked(fetchSettingsCatalog).mockReset();
    vi.mocked(putSettingsSection).mockReset();
    vi.mocked(fetchSecrets).mockReset();
    vi.mocked(fetchSetup).mockReset();
    vi.mocked(fetchSettingsCatalog).mockResolvedValue({ sections: [section()] });
  });
  afterEach(cleanup);

  it("renders the resolved default repo path as the placeholder (native greyed-out default) and the example id", async () => {
    render(<LanguageContext.Provider value="zh"><CatalogSection sectionId="maintainer" /></LanguageContext.Provider>);
    const repo = await screen.findByLabelText("本软件的仓库路径") as HTMLInputElement;
    expect(repo.placeholder).toBe("/Users/demo/Projects/zelin-ai-assistant");
    expect(repo.value).toBe("");
    const sid = screen.getByLabelText("续接的会话 id") as HTMLInputElement;
    expect(sid.placeholder).toBe("例：6f9619ff-8b86-d011-b42d-00cf4fc964ff");
  });

  it("a leading hyphen shows the hyphen sentence and blocks Save; a stray character shows the charset sentence; a valid id saves", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue({ ...section(), fields: [repoField(), sessionField({ effective: "abc-123", source: "override" })] });
    render(<LanguageContext.Provider value="en"><CatalogSection sectionId="maintainer" /></LanguageContext.Provider>);
    const sid = await screen.findByLabelText("Session id to resume") as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;

    fireEvent.change(sid, { target: { value: "--dangerously-skip-permissions" } });
    expect(screen.getByText(HYPHEN.en)).toBeTruthy();
    expect(sid.getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);

    fireEvent.change(sid, { target: { value: "abc 123" } });
    expect(screen.getByText(CHARSET.en)).toBeTruthy();
    expect(screen.queryByText(HYPHEN.en)).toBeNull();
    expect(save.disabled).toBe(true);

    fireEvent.change(sid, { target: { value: "abc-123" } });
    expect(screen.queryByText(CHARSET.en)).toBeNull();
    expect(sid.getAttribute("aria-invalid")).toBeNull();
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["maintainer", { maintainer_session_id: "abc-123" }]);
  });
});
