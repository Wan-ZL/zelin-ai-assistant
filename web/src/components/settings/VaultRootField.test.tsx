// 「Obsidian Vault 位置」按 vault 根显示 / 派生（CONTRACT §68.1 追记 vault 根；原生 Settings.swift:740-792 obsidianGroup 一格
// vault 根字段：loadVault 显示 effective obsidian_raw 的父目录，commitVaultRoot → ObsidianVaultSetup.apply 落 root + "/2 - raw"）：
//   1) 框里显示的是存值的父目录（vault 根），不是 raw 目录；
//   2) 选择…（桥 / 浏览器路径框）从根出发，选中的目录落草稿为 `<选中>/2 - raw`；叶子已是 "2 - raw" 原样；
//   3) 敲字：每个字换算成 raw 进草稿，结尾的 / 留在框里（不被反向派生抹掉）；清空 = 清键；
//   4) 草稿从外部换了（保存对齐 / 目录合并）→ 显示重新派生；
//   5) placeholder = server 目录的 placeholder（默认根），老 server 缺席时回落到同一句；
//   6) 其它目录字段（工作目录）仍逐字存取——只有 obsidian_raw 这一把键按根换算。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import type { SettingsField } from "../../types";
import { DEFAULT_VAULT_ROOT } from "../../vaultPaths";
import { FieldControl } from "./FieldControl";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, postFolderOpen: vi.fn(), postFolderCreate: vi.fn(), fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }) };
});

function vaultField(over: Partial<SettingsField> = {}): SettingsField {
  return {
    key: "obsidian_raw", kind: "string", label: { zh: "Obsidian Vault 位置", en: "Obsidian Vault location" },
    help: { zh: "", en: "" }, default: "", choices: null, effective: "~/Notes/2 - raw", source: "override",
    placeholder: { zh: "~/Documents/Obsidian Vault", en: "~/Documents/Obsidian Vault" }, path: "dir", path_exists: true, ...over,
  };
}

const wrap = (node: JSX.Element) => render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);

/** CatalogSection 的草稿角色：把 onChange 的值喂回 value（受控） */
function Draft({ field, initial, onChange }: { field: SettingsField; initial: string; onChange: (key: string, value: unknown) => void }) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <>
      <FieldControl sectionId="obsidian" field={field} value={value} onChange={(key, next) => { setValue(next); onChange(key, next); }} />
      <button type="button" onClick={() => setValue("~/Elsewhere/2 - raw")}>server-reset</button>
    </>
  );
}

function installShell(postMessage: (body: unknown) => Promise<unknown>) {
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
}

const vaultInput = () => screen.getByLabelText("Obsidian Vault location") as HTMLInputElement;

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
});
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("Obsidian Vault location shows the vault root", () => {
  it("renders the parent of the stored raw dir, never the raw dir itself", () => {
    wrap(<FieldControl sectionId="obsidian" field={vaultField()} value="~/Notes/2 - raw" onChange={() => undefined} />);
    expect(vaultInput().value).toBe("~/Notes");
  });

  it("a hand-customised raw dir (leaf ≠ 2 - raw) still shows its parent, like the native loadVault", () => {
    wrap(<FieldControl sectionId="obsidian" field={vaultField({ effective: "~/Custom/inbox" })} value="~/Custom/inbox" onChange={() => undefined} />);
    expect(vaultInput().value).toBe("~/Custom");
  });

  it("an unset value shows an empty box with the default vault root as placeholder", () => {
    wrap(<FieldControl sectionId="obsidian" field={vaultField({ effective: "", source: "default", path_exists: null })} value="" onChange={() => undefined} />);
    expect(vaultInput().value).toBe("");
    expect(vaultInput().placeholder).toBe("~/Documents/Obsidian Vault");
  });

  it("an older server without the placeholder key falls back to the same default root", () => {
    wrap(<FieldControl sectionId="obsidian" field={vaultField({ placeholder: undefined })} value="" onChange={() => undefined} />);
    expect(vaultInput().placeholder).toBe(DEFAULT_VAULT_ROOT);
  });
});

describe("Choose… derives <picked>/2 - raw", () => {
  it("with the shell bridge: the panel starts at the root and the pick is stored as its raw dir", async () => {
    const postMessage = vi.fn(async () => ({ dialog: { path: "~/Picked" } }));
    installShell(postMessage);
    const onChange = vi.fn();
    wrap(<Draft field={vaultField()} initial="~/Notes/2 - raw" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("obsidian_raw", "~/Picked/2 - raw"));
    expect(postMessage).toHaveBeenCalledWith({ method: "chooseFolder", current: "~/Notes", prompt: "Choose" });
    expect(vaultInput().value).toBe("~/Picked");
  });

  it("a pick whose leaf already is 2 - raw is stored unchanged (no 2 - raw/2 - raw)", async () => {
    installShell(async () => ({ dialog: { path: "~/Picked/2 - raw" } }));
    const onChange = vi.fn();
    wrap(<Draft field={vaultField()} initial="~/Notes/2 - raw" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("obsidian_raw", "~/Picked/2 - raw"));
    expect(vaultInput().value).toBe("~/Picked");
  });

  it("without a bridge: the path box is prefilled with the root, shares the placeholder, and the confirm stores the raw dir", () => {
    const onChange = vi.fn();
    wrap(<Draft field={vaultField()} initial="~/Notes/2 - raw" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    const box = screen.getByLabelText("Folder path") as HTMLInputElement;
    expect(box.value).toBe("~/Notes");
    expect(box.placeholder).toBe("~/Documents/Obsidian Vault");
    fireEvent.change(box, { target: { value: "~/Vault/" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(onChange).toHaveBeenLastCalledWith("obsidian_raw", "~/Vault/2 - raw");
    expect(vaultInput().value).toBe("~/Vault");   // 选中是一次性的完整路径：框里显示派生出的根
  });
});

describe("typing the root", () => {
  it("stores <typed>/2 - raw on every keystroke and keeps a trailing slash in the box", () => {
    const onChange = vi.fn();
    wrap(<Draft field={vaultField()} initial="~/Notes/2 - raw" onChange={onChange} />);
    fireEvent.change(vaultInput(), { target: { value: "~/Notes/" } });
    expect(onChange).toHaveBeenLastCalledWith("obsidian_raw", "~/Notes/2 - raw");
    expect(vaultInput().value).toBe("~/Notes/");
    fireEvent.change(vaultInput(), { target: { value: "~/Notes/Sub" } });
    expect(onChange).toHaveBeenLastCalledWith("obsidian_raw", "~/Notes/Sub/2 - raw");
    expect(vaultInput().value).toBe("~/Notes/Sub");
  });

  it("typing the full raw path is accepted as-is", () => {
    const onChange = vi.fn();
    wrap(<Draft field={vaultField()} initial="" onChange={onChange} />);
    fireEvent.change(vaultInput(), { target: { value: "~/Vault/2 - raw" } });
    expect(onChange).toHaveBeenLastCalledWith("obsidian_raw", "~/Vault/2 - raw");
  });

  it("clearing the box clears the key (server diff-write drops the override)", () => {
    const onChange = vi.fn();
    wrap(<Draft field={vaultField()} initial="~/Notes/2 - raw" onChange={onChange} />);
    fireEvent.change(vaultInput(), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith("obsidian_raw", "");
  });

  it("a draft replaced from outside (save aligned / catalog merge) re-derives the shown root", () => {
    wrap(<Draft field={vaultField()} initial="~/Notes/2 - raw" onChange={() => undefined} />);
    fireEvent.change(vaultInput(), { target: { value: "~/Typing/" } });
    expect(vaultInput().value).toBe("~/Typing/");
    fireEvent.click(screen.getByRole("button", { name: "server-reset" }));
    expect(vaultInput().value).toBe("~/Elsewhere");
  });

  it("dirty is judged on the stored raw dir: Open / Create act on the saved path only after saving", () => {
    wrap(<Draft field={vaultField({ path_exists: false })} initial="~/Notes/2 - raw" onChange={() => undefined} />);
    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.change(vaultInput(), { target: { value: "~/Other" } });
    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Save first/)).toBeTruthy();
  });
});

describe("other folder fields keep verbatim semantics", () => {
  it("the task working folder stores exactly what was picked and shows its own placeholder", () => {
    const onChange = vi.fn();
    const field = vaultField({
      key: "default_target_repo", label: { zh: "任务工作目录", en: "Task working folder" }, effective: "~/Projects/x",
      placeholder: { zh: "", en: "" },
    });
    wrap(<FieldControl sectionId="approval" field={field} value="~/Projects/x" onChange={onChange} />);
    const input = screen.getByLabelText("Task working folder") as HTMLInputElement;
    expect(input.value).toBe("~/Projects/x");
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    const box = screen.getByLabelText("Folder path") as HTMLInputElement;
    expect(box.placeholder).toBe("");
    fireEvent.change(box, { target: { value: "~/Projects/y" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(onChange).toHaveBeenCalledWith("default_target_repo", "~/Projects/y");
  });
});
