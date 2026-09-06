// 每周摘要「现在生成一份」（CONTRACT §24 / §68.1 追记；原生 SettingsWeeklyDigest.swift generateNow）：
// 点一下 = POST /api/actions {action:"weekly_digest_now"}（只这一个字段——多一个 server 会 400）；
// 成功 / 失败各一句逐字镜像原生，失败句前缀与 server 原句分节点；忙态期间按钮禁用。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postAction } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { DigestExtras } from "./DigestExtras";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, postAction: vi.fn() };
});

function renderIn(language: Language) {
  return render(<LanguageContext.Provider value={language}>{<DigestExtras />}</LanguageContext.Provider>);
}

beforeEach(() => {
  vi.mocked(postAction).mockReset();
});
afterEach(cleanup);

describe("DigestExtras — 现在生成一份", () => {
  it("posts exactly {action:'weekly_digest_now'} and shows the native success sentence (zh)", async () => {
    let release: (value: unknown) => void = () => undefined;
    vi.mocked(postAction).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    renderIn("zh");
    const button = screen.getByRole("button", { name: "现在生成一份" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(postAction).toHaveBeenCalledWith({ action: "weekly_digest_now" });
    expect(button.disabled).toBe(true); // 忙态：回执前不许再点
    release({ ok: true });
    await screen.findByText("已请求生成——完成后会弹通知，摘要出现在「待验收」。");
    await waitFor(() => expect(button.disabled).toBe(false));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the English success sentence when the UI is English", async () => {
    vi.mocked(postAction).mockResolvedValue({ ok: true });
    renderIn("en");
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await screen.findByText("Requested — you'll get a notification; the recap appears in the Review lane.");
  });

  it("server rejects → native disk-failure prefix as its own node + the server sentence, in both languages", async () => {
    vi.mocked(postAction).mockRejectedValue(new Error("inbox not writable (EACCES)"));
    renderIn("zh");
    fireEvent.click(screen.getByRole("button", { name: "现在生成一份" }));
    const alert = await screen.findByRole("alert");
    expect(screen.getByText("没能写入请求（磁盘问题），请再点一次：")).toBeTruthy();
    expect(screen.getByText("inbox not writable (EACCES)")).toBeTruthy();
    expect(alert.textContent).toBe("没能写入请求（磁盘问题），请再点一次：inbox not writable (EACCES)");
    expect(screen.queryByText("已请求生成——完成后会弹通知，摘要出现在「待验收」。")).toBeNull();
    cleanup();
    renderIn("en");
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await screen.findByText("Could not write the request (disk issue) — try again:");
  });

  it("a retry after a failure clears the failure sentence and can succeed", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce({ ok: true });
    renderIn("zh");
    const button = screen.getByRole("button", { name: "现在生成一份" });
    fireEvent.click(button);
    await screen.findByRole("alert");
    fireEvent.click(button);
    await screen.findByText("已请求生成——完成后会弹通知，摘要出现在「待验收」。");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(postAction).toHaveBeenCalledTimes(2);
  });
});
