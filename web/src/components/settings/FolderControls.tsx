// 目录字段的三颗按钮（CONTRACT §68.1 目录字段 `path: "dir"`；原生 Settings.swift obsidianGroup 的
// 选择… / 打开 / 创建 与 approvalGroup 的 选择… / 创建文件夹，标签逐字镜像）：
//   · 「选择…」：壳里走 §61.1 桥 `chooseFolder`（NSOpenPanel，只选目录、可新建，prompt = 原生「选择」），
//     选中即写进草稿（仍要按「保存」落盘——与其它字段同一保存语义）；浏览器 / 老壳（NO_BRIDGE / UNKNOWN_METHOD）
//     退化成一行路径文本框 + 「选择」确认 / 「取消」；
//   · 「打开」/「创建」（「创建文件夹」）：POST /api/folders/{open,create} {key}——路径由 server 从**已保存**的
//     effective 值读，客户端只传 key（reveal / ai-fix 同一纪律），草稿未保存时禁用并提示先保存；
//   · 「⚠︎ 目录不存在」警告来自 server 投影 `path_exists`（空值 null 不警告）；创建失败 → 「创建目录失败：」+ 原文。
import { useState } from "react";
import { postFolderCreate, postFolderOpen } from "../../api";
import { useI18n } from "../../i18n";
import { chooseFolder, hasShellBridge, isBridgeUnavailable } from "../../shellBridge";
import { refreshSettingsCatalog } from "../../store";
import type { SettingsField } from "../../types";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

interface FolderUi {
  open: boolean;
  create: [string, string] | null;
  missing: [string, string];
}

/** 按字段的原生文案（键 = 目录里的 field.key；表外的目录字段用通用句） */
const FOLDER_UI: Record<string, FolderUi> = {
  obsidian_raw: {
    open: true,
    create: ["创建", "Create"],
    missing: ["⚠︎ 笔记库目录还不存在——点「选择…」挑一个，或一键创建。", "⚠︎ The vault folder doesn't exist yet — pick one with Choose…, or create it now."],
  },
  default_target_repo: {
    open: false,
    create: ["创建文件夹", "Create folder"],
    missing: ["⚠︎ 目录不存在——第一张批准的卡会派发失败。", "⚠︎ Folder doesn't exist — the first approved card will fail to dispatch."],
  },
  maintainer_repo_path: { open: false, create: null, missing: ["路径不存在", "Path doesn't exist"] },
};
const GENERIC_UI: FolderUi = { open: true, create: ["创建", "Create"], missing: ["⚠︎ 目录不存在", "⚠︎ Folder doesn't exist"] };

export function folderUi(key: string): FolderUi {
  return FOLDER_UI[key] ?? GENERIC_UI;
}

export interface FolderPickerProps {
  current: string;
  onPick: (path: string) => void;
  disabled?: boolean;
}

/** 「选择…」：桥在场开 NSOpenPanel；否则（或老壳 UNKNOWN_METHOD）退化成路径文本框 + 「选择」 */
export function FolderPicker({ current, onPick, disabled = false }: FolderPickerProps) {
  const { text } = useI18n();
  const [fallback, setFallback] = useState(false);
  const [draft, setDraft] = useState(current);
  const [note, setNote] = useState<string | null>(null);

  function openFallback() {
    setDraft(current);
    setFallback(true);
  }

  async function choose() {
    setNote(null);
    if (!hasShellBridge()) {
      openFallback();
      return;
    }
    try {
      const picked = await chooseFolder({ current, prompt: text("选择", "Choose") });
      if (picked) onPick(picked);
    } catch (err) {
      if (isBridgeUnavailable(err)) openFallback();
      else setNote(errorMessage(err));
    }
  }

  return (
    <>
      <button type="button" className="btn" disabled={disabled} onClick={() => void choose()}>{text("选择…", "Choose…")}</button>
      {fallback && (
        <div className="settings-folder-fallback" role="group" aria-label={text("输入目录路径", "Enter a folder path")}>
          <span className="settings-helper">{text("浏览器里没有文件对话框——直接填路径（可用 ~）：", "No file dialog in a browser — type the path (~ works):")}</span>
          <input
            type="text"
            className="settings-input"
            value={draft}
            spellCheck={false}
            placeholder="~/Documents/Obsidian Vault"
            aria-label={text("目录路径", "Folder path")}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!draft.trim()}
            onClick={() => {
              onPick(draft.trim());
              setFallback(false);
            }}
          >
            {text("选择", "Choose")}
          </button>
          <button type="button" className="btn" onClick={() => setFallback(false)}>{text("取消", "Cancel")}</button>
        </div>
      )}
      {note && <span className="settings-warning" role="alert">{note}</span>}
    </>
  );
}

export interface FolderActionsProps {
  field: SettingsField;
  /** 草稿 ≠ 已保存值：打开 / 创建 作用于已保存的路径，先保存再点 */
  dirty: boolean;
}

/** 「打开」/「创建」+「⚠︎ 目录不存在」警告 + 创建失败句 */
export function FolderActions({ field, dirty }: FolderActionsProps) {
  const { text } = useI18n();
  const ui = folderUi(field.key);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; prefix?: string; message: string } | null>(null);
  const missing = field.path_exists === false;
  const hasValue = typeof field.effective === "string" && field.effective.trim() !== "";

  async function run(action: "open" | "create") {
    setBusy(true);
    setNote(null);
    try {
      if (action === "open") {
        await postFolderOpen(field.key);
      } else {
        const receipt = await postFolderCreate(field.key);
        setNote({ ok: true, message: receipt.created ? text("已创建。", "Created.") : text("目录已存在。", "The folder already exists.") });
        void refreshSettingsCatalog();
      }
    } catch (err) {
      // 原生 noteError(L("创建目录失败：", …) + error)：前缀与原文各自一个节点（探针按节点文本逐字判前缀）
      setNote({ ok: false, prefix: action === "create" ? text("创建目录失败：", "Couldn't create the folder: ") : undefined, message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const saveFirst = dirty ? text("先保存，再对已保存的路径操作", "Save first — this acts on the saved path") : undefined;
  const openButton = ui.open && hasValue ? (
    <button type="button" className="btn" disabled={busy || dirty} title={saveFirst} onClick={() => void run("open")}>
      {text("打开", "Open")}
    </button>
  ) : null;
  const createButton = ui.create && missing ? (
    <button type="button" className="btn" disabled={busy || dirty} title={saveFirst} onClick={() => void run("create")}>
      {text(ui.create[0], ui.create[1])}
    </button>
  ) : null;
  if (!openButton && !createButton && !missing && !note) return null;
  return (
    <div className="settings-folder-actions">
      {missing && <span className="settings-warning">{text(ui.missing[0], ui.missing[1])}</span>}
      {openButton}
      {createButton}
      {dirty && (openButton || createButton) && <span className="settings-helper">{saveFirst}</span>}
      {note && (
        <span className={note.ok ? "settings-helper is-ok" : "settings-warning"} role={note.ok ? "status" : "alert"}>
          {note.prefix ? <span>{note.prefix}</span> : null}
          <span>{note.message}</span>
        </span>
      )}
    </div>
  );
}
