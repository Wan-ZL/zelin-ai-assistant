// 凭证行保存路上的原生把关（CONTRACT §68.3 2026-09-05 追记；Settings.swift CredentialRowView.save() +
// SettingsSlack.swift saveToken / SecureField.onSubmit）：
// - Slack 行 xoxb- Bot token 门口拒绝、永不 PUT；非 xoxp- 只给橙色提示、照常保存并验证；
// - Gmail 非 16 位字母数字给橙色提示、照常保存并验证；16 位不提示；
// - 火山两把 key 的章是「已保存（未验证）」（不是 .plain 的「App 内管理」），保存句尾随「——点「检测」…」，
//   豆包语音凭证按回执 legacy_pair 说「已保存 ✓（识别为旧版 App ID + Access Token）」；
// - Enter（非输入法组字中）= 点「保存」，同一道闸（框空 / 忙时不动）；
// - server 400 带 details.reason {zh,en} 时按 UI 语言取原句。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSecrets, fetchSettingsCatalog, fetchSetup, putSecret, verifySecret } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshSecrets, refreshSettingsCatalog, resetStoreForTests } from "../../store";
import type { SecretStatus, SettingsCatalog } from "../../types";
import { looksLikeAppPassword, SecretRow } from "./SecretRow";

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

const SLACK = "slack-user-token.txt";
const GMAIL = "gmail-app-password.txt";
const SPEECH = "volcano-speech-key.txt";
const ARK = "volcano-ark-key.txt";

const row = (name: string, over: Partial<SecretStatus> = {}): SecretStatus =>
  ({ name, label: { zh: name, en: name }, present: false, verifiable: true, mtime: null, legacy: false, ...over });

const gmailCatalog = (address: string): SettingsCatalog => ({ sections: [{
  id: "gmail", title: { zh: "Gmail 接入", en: "Gmail" }, help: { zh: "", en: "" },
  fields: [{ key: "gmail_address", kind: "string", label: { zh: "Gmail 地址", en: "Gmail address" }, help: { zh: "", en: "" },
    default: "", choices: null, effective: address, source: address ? "override" : "default" }],
}] });

function renderIn(language: "zh" | "en", node: React.ReactNode) {
  return render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);
}

/** 密码框（aria-label 随 UI 语言：「<label> 的值」/ "<label> value"） */
const input = (name: string) => screen.getByLabelText(new RegExp(`^${name} (value|的值)$`)) as HTMLInputElement;

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSecrets).mockReset();
  vi.mocked(putSecret).mockReset();
  vi.mocked(verifySecret).mockReset();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
});
afterEach(cleanup);

describe("SecretRow save path — Slack token prefixes (SettingsSlack.swift saveToken)", () => {
  it("refuses an xoxb- Bot token verbatim and never PUTs", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SLACK)] });
    renderIn("zh", <SecretRow name={SLACK} />);
    await refreshSecrets();
    fireEvent.change(input(SLACK), { target: { value: " xoxb-123-BOT " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("这是 Bot token（xoxb-）——雷达读你的 DM 需要 User OAuth Token（xoxp- 开头，在 OAuth & Permissions 页的 User 区）。");
    expect(putSecret).not.toHaveBeenCalled();
    expect(verifySecret).not.toHaveBeenCalled();
    expect(input(SLACK).value).toBe(" xoxb-123-BOT ");   // 框不清：用户还要改
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("a non-xoxp- token gets the orange heads-up but is saved and verified anyway", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SLACK)] });
    vi.mocked(putSecret).mockResolvedValue(row(SLACK, { present: true, legacy_pair: false }));
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "auth.test ok", extra: {} });
    renderIn("en", <SecretRow name={SLACK} />);
    await refreshSecrets();
    fireEvent.change(input(SLACK), { target: { value: "xoxe-refresh-shaped" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Heads-up: User OAuth Tokens usually start with xoxp- — double-check the copy. Verifying anyway…");
    await screen.findByText("Saved ✓ verified");
    expect(putSecret).toHaveBeenCalledWith(SLACK, "xoxe-refresh-shaped");
    expect(verifySecret).toHaveBeenCalledWith(SLACK);
    // 提示与验证结果并存（原生的橙句在 web 里不再被下一句冲掉）
    expect(screen.getByText(/Heads-up: User OAuth Tokens/).className).toBe("settings-warning");
  });

  it("an xoxp- token gets no heads-up", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SLACK)] });
    vi.mocked(putSecret).mockResolvedValue(row(SLACK, { present: true }));
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "auth.test ok", extra: {} });
    renderIn("en", <SecretRow name={SLACK} />);
    await refreshSecrets();
    fireEvent.change(input(SLACK), { target: { value: "xoxp-USER" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved ✓ verified");
    expect(screen.queryByText(/Heads-up/)).toBeNull();
  });

  it("a server 400 carrying details.reason {zh,en} is shown in the UI language", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SLACK)] });
    vi.mocked(putSecret).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "that's a Bot token (xoxb-)",
      details: { field: "value", reason: { zh: "中文原句", en: "English sentence" } } } }));
    renderIn("zh", <SecretRow name={SLACK} />);
    await refreshSecrets();
    fireEvent.change(input(SLACK), { target: { value: "anything-the-server-dislikes" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("保存失败:");
    expect(screen.getByText("中文原句")).toBeTruthy();
    expect(screen.queryByText(/that's a Bot token/)).toBeNull();
  });
});

describe("SecretRow save path — Gmail 16-char heads-up (Settings.swift looksLikeAppPassword)", () => {
  it("looksLikeAppPassword is exactly 16 letters/digits", () => {
    expect(looksLikeAppPassword("abcdefghijklmnop")).toBe(true);
    expect(looksLikeAppPassword("abcd1234efgh5678")).toBe(true);
    expect(looksLikeAppPassword("abcdefghijklmno")).toBe(false);     // 15
    expect(looksLikeAppPassword("abcdefghijklmnopq")).toBe(false);   // 17
    expect(looksLikeAppPassword("abcd-efgh-ijkl-m")).toBe(false);    // 标点
  });

  it("a non-16-char password is saved with the heads-up and still verified", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(GMAIL)] });
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(gmailCatalog("you@gmail.com"));
    vi.mocked(putSecret).mockResolvedValue(row(GMAIL, { present: true }));
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "IMAP LOGIN ok", extra: {} });
    renderIn("zh", <SecretRow name={GMAIL} />);
    await refreshSecrets();
    await refreshSettingsCatalog();
    fireEvent.change(input(GMAIL), { target: { value: "my real gmail password" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("提示：应用密码通常是 16 位字母——检查是否粘贴了别的东西。仍会尝试验证…");
    await screen.findByText("已保存 ✓ 验证通过");
    expect(putSecret).toHaveBeenCalledWith(GMAIL, "myrealgmailpassword");
    expect(verifySecret).toHaveBeenCalledWith(GMAIL);
  });

  it("a 16-char password (spaces stripped) gets no heads-up", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(GMAIL)] });
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(gmailCatalog("you@gmail.com"));
    vi.mocked(putSecret).mockResolvedValue(row(GMAIL, { present: true }));
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "IMAP LOGIN ok", extra: {} });
    renderIn("en", <SecretRow name={GMAIL} />);
    await refreshSecrets();
    await refreshSettingsCatalog();
    fireEvent.change(input(GMAIL), { target: { value: "abcd efgh ijkl mnop" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved ✓ verified");
    expect(screen.queryByText(/Heads-up/)).toBeNull();
  });
});

describe("SecretRow save path — caption rows (Settings.swift isVolcano)", () => {
  it("saved caption keys wear 已保存（未验证）, not the .plain 「App 内管理」 badge", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SPEECH, { present: true, verifiable: false }), row(ARK, { present: true, verifiable: false })] });
    renderIn("zh", <><SecretRow name={SPEECH} /><SecretRow name={ARK} /></>);
    await refreshSecrets();
    expect(await screen.findAllByText("已保存（未验证）")).toHaveLength(2);
    expect(screen.queryByText("已保存（App 内管理）")).toBeNull();
    expect(screen.queryByRole("button", { name: "验证" })).toBeNull();   // server 探针按钮仍不渲（无桥也不渲「检测」）
  });

  it("speech key: legacy pair receipt → 已保存 ✓（识别为旧版 App ID + Access Token）——点「检测」…, no probe", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SPEECH, { verifiable: false })] });
    vi.mocked(putSecret).mockResolvedValue(row(SPEECH, { present: true, verifiable: false, legacy_pair: true }));
    renderIn("zh", <SecretRow name={SPEECH} />);
    await refreshSecrets();
    fireEvent.change(input(SPEECH), { target: { value: "123456789:2tzAbCdEfGhIjKlMnOpQrStUvWxYz012" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    // 原生 L(a) + L(b)：两段各占一个节点（清单标签「已保存 ✓」/ 尾句各自精确匹配），同一 <p role=status> 里连着渲
    const saved = await screen.findByText("已保存 ✓（识别为旧版 App ID + Access Token）");
    expect(saved.parentElement?.textContent).toBe("已保存 ✓（识别为旧版 App ID + Access Token）——点「检测」可真连服务器验证一次");
    expect(putSecret).toHaveBeenCalledWith(SPEECH, "123456789:2tzAbCdEfGhIjKlMnOpQrStUvWxYz012");   // 归一是 server 的活
    expect(verifySecret).not.toHaveBeenCalled();
    expect(input(SPEECH).value).toBe("");
  });

  it("speech / ark key: plain receipt → Saved ✓ — click Test for one real server check", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(SPEECH, { verifiable: false }), row(ARK, { verifiable: false })] });
    vi.mocked(putSecret).mockImplementation(async (name) => row(name, { present: true, verifiable: false, legacy_pair: false }));
    renderIn("en", <><SecretRow name={SPEECH} /><SecretRow name={ARK} /></>);
    await refreshSecrets();
    fireEvent.change(input(SPEECH), { target: { value: "sk-new-console-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await screen.findByText("Saved ✓");   // testing-library 归一前导空格：尾句按整句 textContent 判
    expect(screen.getByRole("status").textContent).toBe("Saved ✓ — click Test for one real server check");
    fireEvent.change(input(ARK), { target: { value: "ark-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
    await waitFor(() => expect(screen.getAllByText("Saved ✓")).toHaveLength(2));
    expect(screen.queryByText(/detected legacy/)).toBeNull();
    expect(verifySecret).not.toHaveBeenCalled();
  });
});

describe("SecretRow save path — Enter saves (SettingsSlack.swift SecureField.onSubmit)", () => {
  it("Enter in the box saves exactly like the button", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(ARK, { verifiable: false })] });
    vi.mocked(putSecret).mockResolvedValue(row(ARK, { present: true, verifiable: false, legacy_pair: false }));
    renderIn("en", <SecretRow name={ARK} />);
    await refreshSecrets();
    fireEvent.change(input(ARK), { target: { value: "  ark-key  " } });
    fireEvent.keyDown(input(ARK), { key: "Enter" });
    await screen.findByText("Saved ✓");
    expect(putSecret).toHaveBeenCalledTimes(1);
    expect(putSecret).toHaveBeenCalledWith(ARK, "ark-key");
  });

  it("Enter with an empty / whitespace box, while composing, or while busy does nothing", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(ARK, { verifiable: false })] });
    let release: (v: SecretStatus) => void = () => {};
    vi.mocked(putSecret).mockImplementation(() => new Promise<SecretStatus>((resolve) => { release = resolve; }));
    renderIn("en", <SecretRow name={ARK} />);
    await refreshSecrets();
    fireEvent.keyDown(input(ARK), { key: "Enter" });
    fireEvent.change(input(ARK), { target: { value: "   " } });
    fireEvent.keyDown(input(ARK), { key: "Enter" });
    expect(putSecret).not.toHaveBeenCalled();
    fireEvent.change(input(ARK), { target: { value: "ark-key" } });
    fireEvent.keyDown(input(ARK), { key: "Enter", isComposing: true });   // 输入法选字的 Enter
    expect(putSecret).not.toHaveBeenCalled();
    fireEvent.keyDown(input(ARK), { key: "Enter" });
    expect(putSecret).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input(ARK), { key: "Enter" });                      // 保存中：闸关着
    expect(putSecret).toHaveBeenCalledTimes(1);
    release(row(ARK, { present: true, verifiable: false, legacy_pair: false }));
    await screen.findByText("Saved ✓");
  });
});
