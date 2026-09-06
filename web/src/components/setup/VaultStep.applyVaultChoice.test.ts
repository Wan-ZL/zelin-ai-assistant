// 向导第 5 步落盘的 raw 派生与 设置 → 笔记库 同一条规则（CONTRACT §68.1 追记 vault 根 / §68.5；`vaultPaths.rawDirOf` 单源）：
//   1) 根 → `<根>/2 - raw`，结尾 / 去掉；
//   2) 选到的就是 `2 - raw` 目录本身 → 原样，不套第二层（设置页 VaultRootField 同款——两面不许各派生一套）；
//   3) 与当前生效根相同 / 没选 → 不写（原生 applyVaultChoice 的 diff-write）；
//   4) PUT 失败 → 原句回给页面（不放行由 SetupPage 判）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, putSettingsSection } from "../../api";
import { resetStoreForTests } from "../../store";
import type { SettingsSection } from "../../types";
import { applyVaultChoice } from "./VaultStep";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, putSettingsSection: vi.fn() };
});

const saved: SettingsSection = { id: "obsidian", title: { zh: "笔记库", en: "Notes vault" }, help: { zh: "", en: "" }, fields: [] };

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(putSettingsSection).mockResolvedValue(saved);
});
afterEach(() => vi.mocked(putSettingsSection).mockReset());

describe("applyVaultChoice derives obsidian_raw with the shared rawDirOf", () => {
  it("stores <root>/2 - raw, trailing slashes stripped", async () => {
    expect(await applyVaultChoice({ root: "~/Vault/", custom: true }, "~/Documents/Obsidian Vault")).toBeNull();
    expect(putSettingsSection).toHaveBeenCalledWith("obsidian", { obsidian_raw: "~/Vault/2 - raw" });
  });

  it("a chosen folder that already is the raw dir is stored unchanged — no 2 - raw/2 - raw (same rule as Settings)", async () => {
    expect(await applyVaultChoice({ root: "~/Vault/2 - raw", custom: true }, "~/Documents/Obsidian Vault")).toBeNull();
    expect(putSettingsSection).toHaveBeenCalledWith("obsidian", { obsidian_raw: "~/Vault/2 - raw" });
  });

  it("writes nothing when the choice equals the current root or nothing was chosen", async () => {
    expect(await applyVaultChoice({ root: "~/Same", custom: false }, "~/Same")).toBeNull();
    expect(await applyVaultChoice(null, "~/Same")).toBeNull();
    expect(await applyVaultChoice({ root: "   ", custom: true }, "~/Same")).toBeNull();
    expect(putSettingsSection).not.toHaveBeenCalled();
  });

  it("returns the server's sentence when the PUT fails", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "obsidian_raw must be a string" } }));
    expect(await applyVaultChoice({ root: "~/Vault", custom: true }, "")).toBe("obsidian_raw must be a string");
  });
});
