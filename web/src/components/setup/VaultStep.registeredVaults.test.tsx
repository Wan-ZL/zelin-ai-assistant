// 向导第 5 步列出 Obsidian 自己登记过的库（CONTRACT §68.5 追记 D51；原生 SetupWizard.swift:205-227 ObsidianVaults.registered /
// :889-895 一行一库 + 「Obsidian vault」徽章）：
//   1) GET /api/setup/vaults 的每一座库一行：库名 + 路径 + 「Obsidian vault」徽章，排在当前行之后、自定义行之前；
//   2) 点一行 = 选中它（onChoose {root, custom:false}，◉ + aria-pressed）——一键，不用敲路径；「选择…」/ 输入框照旧在；
//   3) 当前生效根就是登记库之一 → 合成一行（两枚徽章），不重复；不是 → 「当前」行照旧单独在前；
//   4) 列表拉不到（没装 Obsidian / 老 server 404）→ 没有这些行、不报错，当前 + 自定义两行照旧；
//   5) server 回的列表逐字段消毒（缺 path / 类型不对的条目丢掉；缺 name 用 basename）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchPermissions, fetchSetupVaults } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshPermissions, resetStoreForTests } from "../../store";
import type { PermissionsSnapshot } from "../../types";
import { OBSIDIAN_VAULT_BADGE, sanitizeVaults, VaultStep, type VaultChoice } from "./VaultStep";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchPermissions: vi.fn(), fetchSetupVaults: vi.fn() };
});

const CURRENT = "/Users/demo/Documents/Obsidian Vault";
const WORK = "/Users/demo/Work/Team Vault";

function permissions(root = CURRENT): PermissionsSnapshot {
  return {
    home: "/h", on_external_volume: false,
    fda: { needed: false, pane: "x", executables: [] },
    panes: { full_disk: "x", screen: "y", microphone: "z", notifications: "n" },
    doctor: [], doctor_ran_at: "2026-09-02T00:00:00Z", doctor_ok: true,
    vault: { status: "unknown", root },
  };
}

/** 受控宿主 = SetupPage 持有 choice 的那一小段（预填 + 点选都经 onChoose 回来）；最后一次 onChoose 记在 latest */
let latest: VaultChoice | null = null;
function Host() {
  const [choice, setChoice] = useState<VaultChoice | null>(null);
  return (
    <LanguageContext.Provider value="en">
      <VaultStep choice={choice} onChoose={(c) => { latest = c; setChoice(c); }} error={null} />
    </LanguageContext.Provider>
  );
}

async function seedPermissions(root?: string) {
  vi.mocked(fetchPermissions).mockResolvedValue(permissions(root));
  await refreshPermissions();
}

const radio = (name: string) => screen.getByRole("button", { name });

beforeEach(() => {
  resetStoreForTests();
  latest = null;
  vi.mocked(fetchPermissions).mockReset();
  vi.mocked(fetchSetupVaults).mockReset();
});
afterEach(cleanup);

describe("registered Obsidian vaults are one-click rows (原生 ObsidianVaults.registered + vaultRow badge)", () => {
  it("renders one row per vault with name, path and the badge, between the current row and the custom row", async () => {
    await seedPermissions("/Users/demo/Notes");   // 当前根不是登记库
    vi.mocked(fetchSetupVaults).mockResolvedValue({ vaults: [
      { name: "Obsidian Vault", path: CURRENT },
      { name: "Team Vault", path: WORK },
    ] });
    render(<Host />);
    await screen.findByText(WORK);
    const rows = Array.from(document.querySelectorAll<HTMLElement>(".setup-vault-row"));
    expect(rows.map((r) => r.querySelector(".settings-list-dim")?.textContent)).toEqual([
      "/Users/demo/Notes",            // 当前（单独一行，「current」徽章）
      CURRENT,                        // Obsidian vault
      WORK,                           // Obsidian vault
      "(no folder chosen yet)",       // 自定义行
    ]);
    expect(screen.getAllByText(OBSIDIAN_VAULT_BADGE)).toHaveLength(2);
    expect(screen.getAllByText("current")).toHaveLength(1);
    expect(rows[0].textContent).toContain("current");
    expect(rows[1].textContent).toContain("Obsidian Vault");
    // 「选择…」与自定义行仍在（列表不取代敲路径 / 对话框那条路）
    expect(screen.getByRole("button", { name: "Choose…" })).toBeTruthy();
    expect(radio("No Obsidian — plain markdown folder")).toBeTruthy();
  });

  it("clicking a vault row selects it as the choice (root, custom:false) — ◉ + aria-pressed; the current row lets go", async () => {
    await seedPermissions("/Users/demo/Notes");
    vi.mocked(fetchSetupVaults).mockResolvedValue({ vaults: [{ name: "Team Vault", path: WORK }] });
    render(<Host />);
    await screen.findByText(WORK);
    // 预填 = 当前根
    await waitFor(() => expect(radio("Notes").getAttribute("aria-pressed")).toBe("true"));
    expect(radio("Team Vault").getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(radio("Team Vault"));
    await waitFor(() => expect(radio("Team Vault").getAttribute("aria-pressed")).toBe("true"));
    expect(radio("Team Vault").textContent).toBe("◉");
    expect(radio("Notes").getAttribute("aria-pressed")).toBe("false");
    expect(radio("No Obsidian — plain markdown folder").getAttribute("aria-pressed")).toBe("false");
    expect(latest).toEqual({ root: WORK, custom: false });
    // 没有输入框冒出来——一键选中，不用敲路径
    expect(screen.queryByRole("textbox", { name: "Folder path" })).toBeNull();
  });

  it("when the current root is itself a registered vault the two rows merge into one with both badges (preselected)", async () => {
    await seedPermissions(CURRENT);
    vi.mocked(fetchSetupVaults).mockResolvedValue({ vaults: [
      { name: "Obsidian Vault", path: `${CURRENT}/` },   // 结尾 / 也算同一路径
      { name: "Team Vault", path: WORK },
    ] });
    render(<Host />);
    await screen.findByText(WORK);
    const paths = Array.from(document.querySelectorAll(".setup-vault-row .settings-list-dim")).map((n) => n.textContent);
    expect(paths.filter((p) => p?.startsWith(CURRENT))).toHaveLength(1);   // 不重复
    const merged = document.querySelector<HTMLElement>(`[data-vault-root="${CURRENT}/"]`)!;
    expect(merged.textContent).toContain(OBSIDIAN_VAULT_BADGE);
    expect(merged.textContent).toContain("current");
    await waitFor(() => expect(merged.querySelector("button")?.getAttribute("aria-pressed")).toBe("true"));
    expect(radio("Team Vault").getAttribute("aria-pressed")).toBe("false");
  });

  it("no Obsidian (empty list) or an unreachable endpoint → just the current + custom rows, no error", async () => {
    await seedPermissions(CURRENT);
    vi.mocked(fetchSetupVaults).mockRejectedValue(new ApiError(404, { error: { code: "NOT_FOUND", message: "no route" } }));
    render(<Host />);
    await screen.findByText(CURRENT);
    await waitFor(() => expect(fetchSetupVaults).toHaveBeenCalledTimes(1));
    expect(document.querySelectorAll(".setup-vault-row")).toHaveLength(2);
    expect(screen.queryByText(OBSIDIAN_VAULT_BADGE)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    cleanup();
    vi.mocked(fetchSetupVaults).mockResolvedValue({ vaults: [] });
    render(<Host />);
    await screen.findByText(CURRENT);
    expect(document.querySelectorAll(".setup-vault-row")).toHaveLength(2);
  });

  it("sanitizeVaults drops misshapen entries and falls back to the basename for a missing name", () => {
    expect(sanitizeVaults(null)).toEqual([]);
    expect(sanitizeVaults({ vaults: "nope" })).toEqual([]);
    expect(sanitizeVaults({ vaults: [null, 3, {}, { path: "" }, { path: 7, name: "x" }, { path: "/a/B Vault" }, { name: "N", path: "/c" }] }))
      .toEqual([{ name: "B Vault", path: "/a/B Vault" }, { name: "N", path: "/c" }]);
  });
});
