// 凭证行的保存路径（CONTRACT §68.3 2026-09-05 追记；原生 SecureField.onSubmit）：
// - Enter（非输入法组字中）= 点「保存」，同一道闸（框空 / 忙时不动）；
// - server 400 带 details.reason {zh,en} 时按 UI 语言取原句。
// v2.0.0 退役（全文见 tag full）：Slack xoxb- / xoxp- 把关、Gmail 16 位提示、火山字幕两把 key 的「检测」与 legacy_pair 句。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSecrets, fetchSetup, putSecret, verifySecret } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshSecrets, resetStoreForTests } from "../../store";
import type { SecretStatus } from "../../types";
import { SecretRow } from "./SecretRow";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSecrets: vi.fn(), putSecret: vi.fn(), verifySecret: vi.fn(), fetchSetup: vi.fn() };
});

const KEY = "anthropic-api-key.txt";
/** 没有 server 探针的凭证（保存 = 只存本机不联网）：Enter 判例用它，免得「保存即验证」把回执句换掉 */
const PLAIN = "other-key.txt";

const row = (name: string, over: Partial<SecretStatus> = {}): SecretStatus =>
  ({ name, label: { zh: name, en: name }, present: false, verifiable: true, mtime: null, legacy: false, ...over });

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
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
});
afterEach(cleanup);

describe("SecretRow save path — server reason", () => {
  it("a server 400 carrying details.reason {zh,en} is shown in the UI language", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(KEY)] });
    vi.mocked(putSecret).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "value rejected",
      details: { field: "value", reason: { zh: "中文原句", en: "English sentence" } } } }));
    renderIn("zh", <SecretRow name={KEY} />);
    await refreshSecrets();
    fireEvent.change(input(KEY), { target: { value: "anything-the-server-dislikes" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("保存失败:");
    expect(screen.getByText("中文原句")).toBeTruthy();
    expect(screen.queryByText(/value rejected/)).toBeNull();
  });
});

describe("SecretRow save path — Enter saves (native SecureField.onSubmit)", () => {
  it("Enter in the box saves exactly like the button", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(PLAIN, { verifiable: false })] });
    vi.mocked(putSecret).mockResolvedValue(row(PLAIN, { present: true, verifiable: false, legacy: false }));
    renderIn("en", <SecretRow name={PLAIN} />);
    await refreshSecrets();
    fireEvent.change(input(PLAIN), { target: { value: "  ark-key  " } });
    fireEvent.keyDown(input(PLAIN), { key: "Enter" });
    await screen.findByText("Saved ✓");
    expect(putSecret).toHaveBeenCalledTimes(1);
    expect(putSecret).toHaveBeenCalledWith(PLAIN, "ark-key");
  });

  it("Enter with an empty / whitespace box, while composing, or while busy does nothing", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row(PLAIN, { verifiable: false })] });
    let release: (v: SecretStatus) => void = () => {};
    vi.mocked(putSecret).mockImplementation(() => new Promise<SecretStatus>((resolve) => { release = resolve; }));
    renderIn("en", <SecretRow name={PLAIN} />);
    await refreshSecrets();
    fireEvent.keyDown(input(PLAIN), { key: "Enter" });
    fireEvent.change(input(PLAIN), { target: { value: "   " } });
    fireEvent.keyDown(input(PLAIN), { key: "Enter" });
    expect(putSecret).not.toHaveBeenCalled();
    fireEvent.change(input(PLAIN), { target: { value: "ark-key" } });
    fireEvent.keyDown(input(PLAIN), { key: "Enter", isComposing: true });   // 输入法选字的 Enter
    expect(putSecret).not.toHaveBeenCalled();
    fireEvent.keyDown(input(PLAIN), { key: "Enter" });
    expect(putSecret).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input(PLAIN), { key: "Enter" });                      // 保存中：闸关着
    expect(putSecret).toHaveBeenCalledTimes(1);
    release(row(PLAIN, { present: true, verifiable: false, legacy: false }));
    await screen.findByText("Saved ✓");
  });
});
