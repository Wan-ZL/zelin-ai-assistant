// 凭证行验证回执的三分渲染（CONTRACT §68.3 2026-09-05 追记；原生 Settings.swift handleOutcome / humanAuthReason）：凭证错 →
// 章「验证失败」+ server 的分类人话 `reason`（按 UI 语言；老 server 没有就 detail）；判决未知（network:true）→ 章**不翻**、退回
// 「已保存（未验证）」+ 橙色「无法验证（网络/服务问题），稍后点「验证」重试：」+ detail。v2.0.0：Gmail 地址前置条件与 Slack 身份句 /
// 目录勾选器重载随各自的接入区退役。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSetup, putSecret, verifySecret } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { refreshSecrets, resetStoreForTests } from "../../store";
import type { SecretStatus, SecretVerifyResult } from "../../types";
import { SecretRow } from "./SecretRow";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchSecrets: vi.fn(),
    putSecret: vi.fn(),
    verifySecret: vi.fn(),
    fetchSetup: vi.fn(),
  };
});

const row = (name: string, over: Partial<SecretStatus> = {}): SecretStatus =>
  ({ name, label: { zh: name, en: name }, present: true, verifiable: true, mtime: null, legacy: false, ...over });

const receipt = (over: Partial<SecretVerifyResult>): SecretVerifyResult => ({ ok: false, network: false, detail: "", extra: {}, ...over });

const KEY_REASON = {
  zh: "key 无效——到 console.anthropic.com 重新生成，回来粘贴保存（HTTP 401）",
  en: "The key is invalid — regenerate it at console.anthropic.com, then paste and save (HTTP 401)",
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
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
});
afterEach(cleanup);

describe("SecretRow verify feedback (§68.3 追记) — classified reason", () => {
  it("credential failure: chip flips to failed and the server's reason is shown in the UI language", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt")] });
    vi.mocked(verifySecret).mockResolvedValue(receipt({ detail: "HTTP 401", reason: KEY_REASON }));
    renderIn("zh", <SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    fireEvent.click(await screen.findByRole("button", { name: "验证" }));
    await screen.findByText("验证失败：");
    expect(screen.getByText(KEY_REASON.zh)).toBeTruthy();
    expect(screen.queryByText(/^auth\.test failed: invalid_auth$/)).toBeNull();   // raw 只在括号里，不再单独裸露
    expect(chip().textContent).toBe("验证失败");
    expect(chip().className).toContain("is-failed");
  });

  it("English UI picks the en sentence; save-then-verify keeps the 「已保存，但」 prefix", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt")] });
    vi.mocked(putSecret).mockResolvedValue(row("anthropic-api-key.txt"));
    vi.mocked(verifySecret).mockResolvedValue(receipt({ detail: "HTTP 401", reason: KEY_REASON }));
    renderIn("en", <SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    fireEvent.change(await screen.findByLabelText("anthropic-api-key.txt value"), { target: { value: "sk-ant-bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved, but verification FAILED:");
    expect(screen.getByText(KEY_REASON.en)).toBeTruthy();
  });

  it("without a reason (older server) the raw detail is the fallback", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [row("anthropic-api-key.txt")] });
    vi.mocked(verifySecret).mockResolvedValue(receipt({ detail: "HTTP 403: permission denied" }));
    renderIn("en", <SecretRow name="anthropic-api-key.txt" />);
    await refreshSecrets();
    fireEvent.click(await screen.findByRole("button", { name: "Verify" }));
    await screen.findByText("Verification failed:");
    expect(screen.getByText("HTTP 403: permission denied")).toBeTruthy();
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
