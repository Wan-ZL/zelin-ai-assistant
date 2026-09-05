// 设置页目录字段的「打开」遇到目录不在（CONTRACT §68.4 追记 2026-09-05 (2)；parity gap
// pages-shell-nav-reveal-vault-missing-no-parent-fallback 的第二个调用点）：
//   1) server 回 add-only `missing:true` → 说「目录不存在，已打开上级目录」，role=status（不是错误）、警告色不是绿；
//   2) 回执没有 `missing`（目录在 / 老 server）→ 一句都不说；
//   3) 「创建」照旧走绿的 is-ok——两种回执的色调不混。
// 第一个调用点（依赖检查快速行的「显示」）在 DepRows.test.tsx。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postFolderCreate, postFolderOpen } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsField } from "../../types";
import { FolderActions } from "./FolderControls";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, postFolderOpen: vi.fn(), postFolderCreate: vi.fn(), fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }) };
});

function field(over: Partial<SettingsField> = {}): SettingsField {
  return {
    key: "obsidian_raw", kind: "string", label: { zh: "Obsidian Vault 位置", en: "Obsidian Vault location" },
    help: { zh: "", en: "" }, default: "", choices: null, effective: "~/Notes/2 - raw", source: "override",
    placeholder: { zh: "", en: "" }, path: "dir", path_exists: false, ...over,
  };
}

const wrap = (node: JSX.Element, lang: "zh" | "en" = "en") => render(<LanguageContext.Provider value={lang}>{node}</LanguageContext.Provider>);

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(postFolderOpen).mockReset();
  vi.mocked(postFolderCreate).mockReset();
});
afterEach(cleanup);

describe("<FolderActions /> Open when the folder is missing", () => {
  it("says the parent was opened instead — role=status, warning tone, not the green ok style", async () => {
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/n/Vault", opened: "/n", missing: true });
    wrap(<FolderActions field={field()} dirty={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Folder doesn't exist — opened its parent instead");
    expect(status.className).toBe("settings-warning");
    expect(status.className).not.toContain("is-ok");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("the zh sentence is the native one verbatim", async () => {
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/n/Vault", opened: "/n", missing: true });
    wrap(<FolderActions field={field()} dirty={false} />, "zh");
    fireEvent.click(screen.getByRole("button", { name: "打开" }));
    expect((await screen.findByRole("status")).textContent).toBe("目录不存在，已打开上级目录");
  });

  it("stays silent when the receipt has no missing key (folder exists / older server)", async () => {
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/n/Vault" });
    wrap(<FolderActions field={field({ path_exists: true })} dirty={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => expect(postFolderOpen).toHaveBeenCalledWith("obsidian_raw"));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText(/opened its parent/)).toBeNull();
  });

  it("Create keeps the green ok tone — the two receipts do not share a style", async () => {
    vi.mocked(postFolderCreate).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/n", created: true, git_init: null });
    wrap(<FolderActions field={field()} dirty={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Created.");
    expect(status.className).toBe("settings-helper is-ok");
  });
});
