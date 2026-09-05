// 凭证行验证回执的三分渲染（CONTRACT §68.3 2026-09-05 追记；原生 Settings.swift handleOutcome / humanAuthReason、
// SettingsSlack.swift verifyToken）：凭证错 → 章「验证失败」+ server 的分类人话 `reason`（按 UI 语言；老 server 没有就 detail）；
// 判决未知（network:true）→ 章**不翻**、退回「已保存（未验证）」+ 橙色「无法验证（网络/服务问题），稍后点「验证」重试：」+ detail；
// Slack 探已保存的 token 成功 → 「已验证 ✓ 已连接 <team>，身份 @<user> 自动填好——不用再改任何文件。」+ store 计数 +1（勾选器据此
// 带 refresh 重载一次）+ 目录刷新；粘贴即验证的成功不说「自动填好」（server 没回填）、不动计数。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog, fetchSetup, fetchSlackDirectory, putSecret, verifySecret } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { getState, markSlackTokenVerified, refreshSecrets, resetStoreForTests } from "../../store";
import type { SecretStatus, SecretVerifyResult, SlackDirectory } from "../../types";
import { SecretRow } from "./SecretRow";
import { SlackDirectoryPicker } from "./SlackDirectoryPicker";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchSettingsCatalog: vi.fn(),
    fetchSecrets: vi.fn(),
    fetchSlackDirectory: vi.fn(),
    putSecret: vi.fn(),
    verifySecret: vi.fn(),
    fetchSetup: vi.fn(),
  };
});

const row = (name: string, over: Partial<SecretStatus> = {}): SecretStatus =>
  ({ name, label: { zh: name, en: name }, present: true, verifiable: true, mtime: null, legacy: false, ...over });

const receipt = (over: Partial<SecretVerifyResult>): SecretVerifyResult => ({ ok: false, network: false, detail: "", extra: {}, ...over });

const SLACK_REASON = {
  zh: "token 无效——到 api.slack.com/apps → OAuth & Permissions 重新生成 User OAuth Token 再粘贴（auth.test failed: invalid_auth）",
  en: "The token is invalid — regenerate the User OAuth Token at api.slack.com/apps → OAuth & Permissions and paste it again (auth.test failed: invalid_auth)",
};

function renderIn(language: Language, node: React.ReactNode) {
  return render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);
}

const chip = () => document.querySelector(".settings-source-chip") as HTMLElement;

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSecrets).mockReset();
  vi.mocked(putSecret).mockReset();
  vi.mocked(verifySecret).mockReset();
  vi.mocked(fetchSlackDirectory).mockReset();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue({ sections: [] });
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
});
afterEach(cleanup);

describe("SecretRow verify feedback (§68.3 追记) — classified reason", () => {
  it("credential failure: chip flips to failed and the server's reason is shown in the UI language", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt")] });
    vi.mocked(verifySecret).mockResolvedValue(receipt({ detail: "auth.test failed: invalid_auth", reason: SLACK_REASON }));
    renderIn("zh", <SecretRow name="slack-user-token.txt" />);
    await refreshSecrets();
    fireEvent.click(await screen.findByRole("button", { name: "验证" }));
    await screen.findByText("验证失败：");
    expect(screen.getByText(SLACK_REASON.zh)).toBeTruthy();
    expect(screen.queryByText(/^auth\.test failed: invalid_auth$/)).toBeNull();   // raw 只在括号里，不再单独裸露
    expect(chip().textContent).toBe("验证失败");
    expect(chip().className).toContain("is-failed");
  });

  it("English UI picks the en sentence; save-then-verify keeps the 「已保存，但」 prefix", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt")] });
    vi.mocked(putSecret).mockResolvedValue(row("slack-user-token.txt"));
    vi.mocked(verifySecret).mockResolvedValue(receipt({ detail: "auth.test failed: invalid_auth", reason: SLACK_REASON }));
    renderIn("en", <SecretRow name="slack-user-token.txt" />);
    await refreshSecrets();
    fireEvent.change(await screen.findByLabelText("slack-user-token.txt value"), { target: { value: "xoxp-bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved, but verification FAILED:");
    expect(screen.getByText(SLACK_REASON.en)).toBeTruthy();
  });

  it("without a reason (older server) the raw detail is the fallback", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("gmail-app-password.txt")] });
    vi.mocked(fetchSettingsCatalog).mockResolvedValue({ sections: [{ id: "gmail", title: { zh: "", en: "" }, help: { zh: "", en: "" },
      fields: [{ key: "gmail_address", kind: "string", label: { zh: "", en: "" }, help: { zh: "", en: "" }, default: "", choices: null, effective: "me@x.com", source: "override" }] }] });
    vi.mocked(verifySecret).mockResolvedValue(receipt({ detail: "IMAP LOGIN rejected: [AUTHENTICATIONFAILED] Invalid credentials" }));
    renderIn("en", <SecretRow name="gmail-app-password.txt" />);
    await refreshSecrets();
    fireEvent.click(await screen.findByRole("button", { name: "Verify" }));
    await screen.findByText("Verification failed:");
    expect(screen.getByText("IMAP LOGIN rejected: [AUTHENTICATIONFAILED] Invalid credentials")).toBeTruthy();
  });
});

describe("SecretRow verify feedback (§68.3 追记) — network / service = verdict unknown", () => {
  it("network:true leaves the chip on saved (not verified) and says couldn't verify + detail", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt")] });
    vi.mocked(verifySecret).mockResolvedValue(receipt({ network: true, detail: "network error: api.anthropic.com answered HTTP 529: Overloaded" }));
    renderIn("en", <SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    expect((await screen.findByText("saved (not verified)"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Couldn't verify (network/service) — click Verify again later:");
    expect(screen.getByText("network error: api.anthropic.com answered HTTP 529: Overloaded")).toBeTruthy();
    expect(chip().textContent).toBe("saved (not verified)");
    expect(chip().className).not.toContain("is-failed");
    expect(screen.queryByText(/verification failed/i)).toBeNull();
    expect(screen.getByRole("alert").className).toBe("settings-warning");   // 橙色行（原生 .orange）
  });

  it("a network failure after an earlier ✓ reverts the chip to saved (not verified) (native state 3 → 2)", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt")] });
    vi.mocked(verifySecret).mockResolvedValueOnce(receipt({ ok: true, detail: "key accepted" }));
    renderIn("zh", <SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    fireEvent.click(await screen.findByRole("button", { name: "验证" }));
    await screen.findByText("验证通过 ✓");
    expect(chip().textContent).toBe("已验证 ✓");
    vi.mocked(verifySecret).mockResolvedValueOnce(receipt({ network: true, detail: "network error: dns down" }));
    fireEvent.click(screen.getByRole("button", { name: "验证" }));
    await screen.findByText("无法验证（网络/服务问题），稍后点「验证」重试：");
    expect(chip().textContent).toBe("已保存（未验证）");
  });

  it("save-then-verify with a network failure: no 「已保存，但验证失败」, chip stays saved (not verified)", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt")] });
    vi.mocked(putSecret).mockResolvedValue(row("anthropic-api-key.txt"));
    vi.mocked(verifySecret).mockResolvedValue(receipt({ network: true, detail: "network error: timed out" }));
    renderIn("zh", <SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    fireEvent.change(await screen.findByLabelText("anthropic-api-key.txt 的值"), { target: { value: "sk-ant-1" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("无法验证（网络/服务问题），稍后点「验证」重试：");
    expect(screen.queryByText(/验证失败/)).toBeNull();
    expect(chip().textContent).toBe("已保存（未验证）");
  });
});

describe("SecretRow verify feedback (§68.3 追记) — Gmail no-address precondition judged by the server", () => {
  // 目录没到本地（SetupPage 第 6 步不挂 CatalogSection，settingsCatalog 为 null）→ 本地 guard 判不了、探针发出去；
  // server 回 extra.precondition = "gmail_address"（探针没跑，不是凭证的判决）→ 与本地 guard 同一句橙句、章不翻
  const noAddress = receipt({ detail: "no Gmail address configured (Sources → Gmail address)", extra: { precondition: "gmail_address" } });

  it("verify with no catalog loaded: the native no-address sentence, chip stays saved (not verified), no 「验证失败」", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("gmail-app-password.txt")] });
    vi.mocked(verifySecret).mockResolvedValue(noAddress);
    renderIn("en", <SecretRow name="gmail-app-password.txt" />);
    await refreshSecrets();
    expect(getState().settingsCatalog).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Verify" }));
    await screen.findByText("No Gmail address yet —");
    expect(screen.getByText(/fill in "Gmail address" above, then click Verify\./)).toBeTruthy();
    expect(verifySecret).toHaveBeenCalledWith("gmail-app-password.txt");
    expect(screen.queryByText(/Verification failed/)).toBeNull();
    expect(screen.queryByText(/no Gmail address configured/)).toBeNull();   // raw detail 不裸露
    expect(chip().textContent).toBe("saved (not verified)");
    expect(chip().className).not.toContain("is-failed");
    expect(screen.getByRole("alert").className).toBe("settings-warning");
  });

  it("save-then-verify with no catalog loaded: 「已保存，但还没填 Gmail 地址——」 and the chip does not flip", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("gmail-app-password.txt", { present: false })] });
    vi.mocked(putSecret).mockResolvedValue(row("gmail-app-password.txt"));
    vi.mocked(verifySecret).mockResolvedValue(noAddress);
    renderIn("zh", <SecretRow name="gmail-app-password.txt" />);
    await refreshSecrets();
    fireEvent.change(await screen.findByLabelText("gmail-app-password.txt 的值"), { target: { value: "abcd efgh ijkl mnop" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已保存，但还没填 Gmail 地址——");
    expect(screen.getByText(/在上面「Gmail 地址」填好后点「验证」。/)).toBeTruthy();
    expect(screen.queryByText(/验证失败/)).toBeNull();
    expect(chip().className).not.toContain("is-failed");
  });
});

describe("SecretRow verify feedback (§68.3 追记) — Slack identity + directory autoload", () => {
  const slackOk = receipt({ ok: true, detail: "auth.test ok", extra: { user_id: "U1", user: "zelin", team: "Acme" } });

  it("stored-token success composes the native identity sentence, bumps the store counter and refreshes the catalog", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt")] });
    vi.mocked(verifySecret).mockResolvedValue(slackOk);
    renderIn("zh", <SecretRow name="slack-user-token.txt" />);
    await refreshSecrets();
    vi.mocked(fetchSettingsCatalog).mockClear();
    fireEvent.click(await screen.findByRole("button", { name: "验证" }));
    await screen.findByText("已验证 ✓ 已连接 Acme，身份 @zelin 自动填好——不用再改任何文件。");
    expect(screen.queryByText("auth.test ok")).toBeNull();
    expect(chip().textContent).toBe("已验证 ✓");
    expect(getState().slackTokenVerifications).toBe(1);
    await waitFor(() => expect(fetchSettingsCatalog).toHaveBeenCalledTimes(1));
  });

  it("English sentence after save-then-verify (no 「已保存」 prefix — native verifyToken has one success sentence)", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt", { present: false })] });
    vi.mocked(putSecret).mockResolvedValue(row("slack-user-token.txt"));
    vi.mocked(verifySecret).mockResolvedValue(slackOk);
    renderIn("en", <SecretRow name="slack-user-token.txt" />);
    await refreshSecrets();
    fireEvent.change(await screen.findByLabelText("slack-user-token.txt value"), { target: { value: "xoxp-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Verified ✓ Connected to Acme; identity @zelin filled in automatically — no files to edit.");
    expect(screen.queryByText(/Saved ✓ verified/)).toBeNull();
    expect(verifySecret).toHaveBeenLastCalledWith("slack-user-token.txt");   // 保存后探已保存的
    expect(getState().slackTokenVerifications).toBe(1);
  });

  it("paste-verify (not stored, no autofill) keeps the generic sentence and leaves the counter alone", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt", { present: false })] });
    vi.mocked(verifySecret).mockResolvedValue(slackOk);
    renderIn("en", <SecretRow name="slack-user-token.txt" />);
    await refreshSecrets();
    fireEvent.change(await screen.findByLabelText("slack-user-token.txt value"), { target: { value: "xoxp-pasted" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Verified ✓");
    expect(screen.getByText(/auth\.test ok/)).toBeTruthy();
    expect(screen.queryByText(/Connected to Acme/)).toBeNull();
    expect(getState().slackTokenVerifications).toBe(0);
  });

  it("missing team / user in extra → generic sentence; non-Slack rows never compose it", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("slack-user-token.txt"), row("anthropic-api-key.txt")] });
    vi.mocked(verifySecret)
      .mockResolvedValueOnce(receipt({ ok: true, detail: "auth.test ok", extra: { user_id: "U1" } }))
      .mockResolvedValueOnce(receipt({ ok: true, detail: "key accepted", extra: { user: "x", team: "y" } }));
    renderIn("en", <><SecretRow name="slack-user-token.txt" /><SecretRow name="anthropic-api-key.txt" /></>);
    await refreshSecrets();
    const buttons = await screen.findAllByRole("button", { name: "Verify" });
    fireEvent.click(buttons[0]);
    await screen.findByText("Verified ✓");
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(screen.getAllByText("Verified ✓")).toHaveLength(2));
    expect(screen.queryByText(/Connected to/)).toBeNull();
    expect(getState().slackTokenVerifications).toBe(0);
  });
});

describe("SlackDirectoryPicker autoload (§68.3 追记)", () => {
  const directory: SlackDirectory = { ok: true, channels: [{ id: "C1", name: "eng" }], users: [] };

  it("does not load on mount; loads with refresh once per verification; the button still works as before", async () => {
    markSlackTokenVerified();   // 挂载前已有的一次不算（原生的勾选器在验证时就在屏上）
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    renderIn("en", <SlackDirectoryPicker />);
    expect(fetchSlackDirectory).not.toHaveBeenCalled();
    markSlackTokenVerified();
    await screen.findByText("Channels (card only when @mentioned)");
    expect(fetchSlackDirectory).toHaveBeenCalledTimes(1);
    expect(fetchSlackDirectory).toHaveBeenLastCalledWith(true);   // 原生 loadDirectory(refresh:true)
    markSlackTokenVerified();
    await waitFor(() => expect(fetchSlackDirectory).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(fetchSlackDirectory).toHaveBeenCalledTimes(3));
    expect(fetchSlackDirectory).toHaveBeenLastCalledWith(true);
  });

  it("first manual load (no data yet) still goes through the cache", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    renderIn("en", <SlackDirectoryPicker />);
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("Channels (card only when @mentioned)");
    expect(fetchSlackDirectory).toHaveBeenCalledWith(false);
  });

  it("a verification while a load is in flight is skipped (native guard !directoryLoading)", async () => {
    let release: (d: SlackDirectory) => void = () => {};
    vi.mocked(fetchSlackDirectory).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    renderIn("en", <SlackDirectoryPicker />);
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("Loading…");
    markSlackTokenVerified();
    release(directory);
    await screen.findByText("Channels (card only when @mentioned)");
    expect(fetchSlackDirectory).toHaveBeenCalledTimes(1);
  });
});
