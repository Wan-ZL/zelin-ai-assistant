// 目录字段的 选择… / 打开 / 创建（CONTRACT §68.1；§61.1 chooseFolder；原生 obsidianGroup / approvalGroup）：
//   1) 壳在场：「选择…」→ 桥 chooseFolder(current, prompt「选择」) → 选中写进草稿；取消不动；
//   2) 无桥 / 老壳（UNKNOWN_METHOD）：退化成路径框 + 「选择」确认 / 「取消」；桥真出错 → 原文；
//   3) path_exists=false → 原生警告句 + 「创建」/「创建文件夹」；null → 不警告；
//   4) 打开 / 创建 只传 key；草稿未保存时禁用并提示先保存；创建失败 → 「创建目录失败：」前缀 + 原文；
//   5) FieldControl 只对 path:"dir" 字段长出这些按钮（笔记库字段按 vault 根显示 / 派生的判例在 VaultRootField.test.tsx）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, postFolderCreate, postFolderOpen } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import type { SettingsField } from "../../types";
import { FieldControl } from "./FieldControl";
import { FolderActions, FolderPicker, folderUi } from "./FolderControls";

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

const wrap = (node: JSX.Element) => render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);

function installShell(postMessage: (body: unknown) => Promise<unknown>) {
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
}

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  vi.mocked(postFolderOpen).mockReset();
  vi.mocked(postFolderCreate).mockReset();
});
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("folderUi", () => {
  it("carries the native sentences per field and a generic fallback", () => {
    expect(folderUi("obsidian_raw").create).toEqual(["创建", "Create"]);
    expect(folderUi("default_target_repo").create).toEqual(["创建文件夹", "Create folder"]);
    expect(folderUi("default_target_repo").open).toBe(false);
    expect(folderUi("maintainer_repo_path").missing).toEqual(["路径不存在", "Path doesn't exist"]);
    expect(folderUi("something_else").create).toEqual(["创建", "Create"]);
  });
});

describe("<FolderPicker />", () => {
  it("with the shell bridge: Choose… opens the panel with current + prompt and hands back the pick", async () => {
    const postMessage = vi.fn(async () => ({ recording: {}, captions: {}, dialog: { path: "~/Picked" } }));
    installShell(postMessage);
    const onPick = vi.fn();
    wrap(<FolderPicker current="~/Notes" onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await waitFor(() => expect(onPick).toHaveBeenCalledWith("~/Picked"));
    expect(postMessage).toHaveBeenCalledWith({ method: "chooseFolder", current: "~/Notes", prompt: "Choose" });
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("a cancelled panel (dialog.path null) picks nothing", async () => {
    installShell(async () => ({ dialog: { path: null } }));
    const onPick = vi.fn();
    wrap(<FolderPicker current="" onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await waitFor(() => expect(window.webkit).toBeTruthy());
    await new Promise((r) => setTimeout(r, 0));
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("without a bridge: falls back to a path field whose confirm button is the native Choose", () => {
    const onPick = vi.fn();
    wrap(<FolderPicker current="~/Notes" onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    const input = screen.getByLabelText("Folder path") as HTMLInputElement;
    expect(input.value).toBe("~/Notes");
    fireEvent.change(input, { target: { value: "  ~/Other  " } });
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(onPick).toHaveBeenCalledWith("~/Other");
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("an older shell that rejects UNKNOWN_METHOD also falls back; a real bridge error is shown", async () => {
    installShell(async () => { throw "UNKNOWN_METHOD: chooseFolder"; });
    const onPick = vi.fn();
    wrap(<FolderPicker current="" onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await screen.findByRole("group");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group")).toBeNull();
    cleanup();
    installShell(async () => { throw "INTERNAL: panel exploded"; });
    wrap(<FolderPicker current="" onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("panel exploded");
    expect(onPick).not.toHaveBeenCalled();
  });
});

describe("<FolderActions />", () => {
  it("renders the native warning + Create for a missing vault and posts only the key", async () => {
    vi.mocked(postFolderCreate).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/n", created: true, git_init: null });
    wrap(<FolderActions field={field()} dirty={false} />);
    expect(screen.getByText(/The vault folder doesn't exist yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(postFolderCreate).toHaveBeenCalledWith("obsidian_raw"));
    await screen.findByRole("status");
  });

  it("Create folder for the workbench; Open only where the native had it; nothing when path_exists is null", () => {
    wrap(<FolderActions field={field({ key: "default_target_repo", effective: "~/Projects/x" })} dirty={false} />);
    expect(screen.getByRole("button", { name: "Create folder" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
    expect(screen.getByText(/first approved card will fail/)).toBeTruthy();
    cleanup();
    wrap(<FolderActions field={field({ path_exists: true })} dirty={false} />);
    expect(screen.getByRole("button", { name: "Open" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
    cleanup();
    const { container } = wrap(<FolderActions field={field({ effective: "", path_exists: null })} dirty={false} />);
    expect(container.textContent).toBe("");
  });

  it("Open posts the key; a dirty draft disables both buttons with a save-first hint", async () => {
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/n" });
    wrap(<FolderActions field={field()} dirty={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => expect(postFolderOpen).toHaveBeenCalledWith("obsidian_raw"));
    cleanup();
    wrap(<FolderActions field={field()} dirty />);
    expect((screen.getByRole("button", { name: "Open" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Save first/)).toBeTruthy();
  });

  it("a failed create shows the native prefix as its own node plus the server sentence", async () => {
    vi.mocked(postFolderCreate).mockRejectedValue(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "could not create the folder: [Errno 13] Permission denied" } }));
    wrap(<FolderActions field={field()} dirty={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const alert = await screen.findByRole("alert");
    expect(alert.children[0].textContent).toBe("Couldn't create the folder: ");
    expect(alert.textContent).toContain("Permission denied");
  });
});

describe("<FieldControl /> for a folder field", () => {
  it("grows Choose… and the actions only for path:dir; a plain string field stays a bare input", () => {
    // 工作目录是逐字存取的目录字段（笔记库那一把按 vault 根换算，判例在 VaultRootField.test.tsx）
    const onChange = vi.fn();
    wrap(<FieldControl sectionId="approval" field={field({ key: "default_target_repo", effective: "~/Projects/x" })} value="~/Projects/x" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Choose…" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create folder" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));   // 无桥 → 路径框
    fireEvent.change(screen.getByLabelText("Folder path"), { target: { value: "~/Elsewhere" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(onChange).toHaveBeenCalledWith("default_target_repo", "~/Elsewhere");
    cleanup();
    wrap(<FieldControl sectionId="gmail" field={field({ key: "gmail_address", path: undefined, path_exists: undefined })} value="a@b" onChange={onChange} />);
    expect(screen.queryByRole("button", { name: "Choose…" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
  });

  it("a draft that differs from the saved value disables Open / Create until saved", () => {
    wrap(<FieldControl sectionId="obsidian" field={field({ path_exists: true })} value="~/Draft" onChange={() => undefined} />);
    expect((screen.getByRole("button", { name: "Open" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
