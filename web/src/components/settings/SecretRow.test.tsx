// 凭证行的原生状态机余量（CONTRACT §68.3 追记；Settings.swift CredentialRowView）：
// 「使用旧路径」章（server add-only legacy）；「验证」三分支：框里有字探这个值（verify {value}，不落盘）、框空探已保存的、
// 都没有提示先粘贴（Slack 区换自己的那句）；Gmail 没填地址就不发探针（原生 effectiveGmailAddress 短路）；应用密码去空白。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog, fetchSetup, putSecret, verifySecret } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshSecrets, refreshSettingsCatalog, resetStoreForTests } from "../../store";
import type { SecretStatus, SettingsCatalog } from "../../types";
import { SecretRow } from "./SecretRow";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchSettingsCatalog: vi.fn(),
    fetchSecrets: vi.fn(),
    putSecret: vi.fn(),
    verifySecret: vi.fn(),
    fetchSetup: vi.fn(),
  };
});

const row = (name: string, over: Partial<SecretStatus> = {}): SecretStatus =>
  ({ name, label: { zh: name, en: name }, present: false, verifiable: true, mtime: null, legacy: false, ...over });

const gmailCatalog = (address: string): SettingsCatalog => ({ sections: [{
  id: "gmail", title: { zh: "Gmail 接入", en: "Gmail" }, help: { zh: "", en: "" },
  fields: [{ key: "gmail_address", kind: "string", label: { zh: "Gmail 地址", en: "Gmail address" }, help: { zh: "", en: "" },
    default: "", choices: null, effective: address, source: address ? "override" : "default" }],
}] });

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSecrets).mockReset();
  vi.mocked(putSecret).mockReset();
  vi.mocked(verifySecret).mockReset();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
});
afterEach(cleanup);

describe("SecretRow (§68.3 追记)", () => {
  it("shows 使用旧路径 when the secrets file is absent but a legacy tier has one", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt", { legacy: true })] });
    renderEn(<SecretRow name="slack-user-token.txt" />);
    await refreshSecrets();
    await screen.findByText("Using legacy path");
    expect(screen.queryByText("Not set")).toBeNull();
  });

  it("Verify probes the pasted value without saving, then the stored one when the box is empty", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt", { present: true })] });
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "ok", extra: {} });
    renderEn(<SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    const verify = await screen.findByRole("button", { name: "Verify" });
    fireEvent.change(screen.getByLabelText("anthropic-api-key.txt value"), { target: { value: " sk-ant-PASTED " } });
    fireEvent.click(verify);
    await screen.findByText("Verified ✓");
    expect(verifySecret).toHaveBeenLastCalledWith("anthropic-api-key.txt", "sk-ant-PASTED");
    expect(putSecret).not.toHaveBeenCalled();
    expect((screen.getByLabelText("anthropic-api-key.txt value") as HTMLInputElement).value).toBe(" sk-ant-PASTED ");   // 只探不清框
    fireEvent.change(screen.getByLabelText("anthropic-api-key.txt value"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(verifySecret).toHaveBeenCalledTimes(2));
    expect(vi.mocked(verifySecret).mock.calls[1]).toEqual(["anthropic-api-key.txt"]);
  });

  it("with nothing pasted and nothing saved it says paste first — Slack wording via emptyVerifyNote", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt", { legacy: true }), row("anthropic-api-key.txt")] });
    renderEn(<><SecretRow name="anthropic-api-key.txt" /><SecretRow name="slack-user-token.txt" emptyVerifyNote="Paste and save a token first" /></>);
    await refreshSecrets();
    const buttons = await screen.findAllByRole("button", { name: "Verify" });
    expect(buttons.map((b) => (b as HTMLButtonElement).disabled)).toEqual([false, false]);   // 原生：只在验证中才禁用
    fireEvent.click(buttons[0]);
    await screen.findByText("Paste (or save) a credential first");
    fireEvent.click(buttons[1]);
    await screen.findByText("Paste and save a token first");
    expect(verifySecret).not.toHaveBeenCalled();
  });

  it("Gmail: no address in the settings catalog → no probe, native sentence in two nodes; whitespace stripped on save", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("gmail-app-password.txt", { present: true })] });
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(gmailCatalog(""));
    vi.mocked(putSecret).mockResolvedValue(row("gmail-app-password.txt", { present: true }));
    renderEn(<SecretRow name="gmail-app-password.txt" />);
    await refreshSecrets();
    await refreshSettingsCatalog();
    fireEvent.click(await screen.findByRole("button", { name: "Verify" }));
    await screen.findByText("No Gmail address yet —");   // 前缀独立节点（testing-library 归一尾空格）
    expect(screen.getByText(/fill in "Gmail address" above, then click Verify\./)).toBeTruthy();
    expect(verifySecret).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("gmail-app-password.txt value"), { target: { value: "abcd efgh ijkl mnop" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved, but no Gmail address yet —");
    expect(putSecret).toHaveBeenCalledWith("gmail-app-password.txt", "abcdefghijklmnop");
    // 地址填好后探针照常走
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(gmailCatalog("you@gmail.com"));
    await refreshSettingsCatalog();
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "IMAP LOGIN ok", extra: {} });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Verified ✓");
    expect(verifySecret).toHaveBeenCalledWith("gmail-app-password.txt");
  });
});
