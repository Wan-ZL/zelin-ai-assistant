// 目录字段的三颗按钮（CONTRACT §68.1 目录字段 `path: "dir"`；原生 Settings.swift obsidianGroup 的
// 选择… / 打开 / 创建 与 approvalGroup 的 选择… / 创建文件夹，标签逐字镜像）：
//   · 「选择…」：壳里走 §61.1 桥 `chooseFolder`（NSOpenPanel，只选目录、可新建，prompt = 原生「选择」），
//     选中即写进草稿（仍要按「保存」落盘——与其它字段同一保存语义）；浏览器 / 老壳（NO_BRIDGE / UNKNOWN_METHOD）
//     退化成一行路径文本框 + 「选择」确认 / 「取消」；
//   · 「打开」/「创建」（「创建文件夹」）：POST /api/folders/{open,create} {key}——路径由 server 从**已保存**的
//     effective 值读，客户端只传 key（reveal / ai-fix 同一纪律），草稿未保存时禁用并提示先保存；
//     笔记库的「打开」由 server 落到 vault 根（raw 的父目录，原生 `openInFinder(vaultRoot)`），与框里显示的同一处；
//   · 「⚠︎ 目录不存在」警告来自 server 投影 `path_exists`（空值 null 不警告）；创建失败 → 「创建目录失败：」+ 原文；
//   · 「Obsidian Vault 位置」（`obsidian_raw`，§68.1 追记 vault 根）：框里显示的是 **vault 根**（= 存值的父目录，原生
//     loadVault 的 `deletingLastPathComponent`），敲字 / 选择… 落进草稿的是 `<根>/2 - raw`（原生 commitVaultRoot →
//     ObsidianVaultSetup.apply 的 `root + "/2 - raw"`；叶子已是 "2 - raw" 则原样）——规则单源 `vaultPaths.ts`。
import { useEffect, useState } from "react";
import { postFolderCreate, postFolderOpen } from "../../api";
import { useI18n } from "../../i18n";
import { chooseFolder, hasShellBridge, isBridgeUnavailable } from "../../shellBridge";
import { refreshSettingsCatalog } from "../../store";
import type { SettingsField } from "../../types";
import { DEFAULT_VAULT_ROOT, rawDirOf, vaultRootOf } from "../../vaultPaths";
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
  /** 浏览器路径框的示例文案（与该字段主输入框同一句：vault 字段是默认根，其它字段是目录 placeholder / 无） */
  placeholder?: string;
}

/** 「选择…」：桥在场开 NSOpenPanel；否则（或老壳 UNKNOWN_METHOD）退化成路径文本框 + 「选择」 */
export function FolderPicker({ current, onPick, disabled = false, placeholder }: FolderPickerProps) {
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
            placeholder={placeholder}
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

export interface VaultRootFieldProps {
  id: string;
  /** 草稿里的 obsidian_raw（raw 目录，落盘形）；"" = 未设置 */
  raw: string;
  onChange: (raw: string) => void;
  disabled?: boolean;
  /** 空值时的示例（server 目录的 placeholder；缺省 = 默认根 ~/Documents/Obsidian Vault） */
  placeholder?: string;
}

/** 「Obsidian Vault 位置」的输入框 + 「选择…」：显示 vault 根、落 `<根>/2 - raw`（原生 Settings.swift:740-792 一格 vault 根字段）。
 *  框里的字是本地态：敲的每个字都立刻换算成 raw 写进草稿，但敲字期间显示不反向派生——否则「~/Notes/」刚敲完的 / 会被
 *  `vaultRootOf(rawDirOf(…))` 抹掉、下一个字接不上；**失焦**（blur）时才把显示重派生成草稿的根（原生 commitField 在
 *  focus-out / Enter 时 commit → loadVault 重派生同拍；点「保存」本身先失焦，所以保存后框里是规范形，不留结尾 / 或首尾空白），
 *  草稿从**外部**换了（保存对齐 / 目录合并 / 选择…）也重新派生显示。 */
export function VaultRootField({ id, raw, onChange, disabled = false, placeholder = DEFAULT_VAULT_ROOT }: VaultRootFieldProps) {
  const [root, setRoot] = useState(() => vaultRootOf(raw));
  useEffect(() => {
    if (rawDirOf(root) !== raw) setRoot(vaultRootOf(raw));
  }, [raw]); // eslint-disable-line react-hooks/exhaustive-deps

  /** 敲字：框里原样留着（含结尾 /），草稿收换算后的 raw */
  function type(next: string) {
    setRoot(next);
    onChange(rawDirOf(next));
  }

  /** 失焦：显示 = 草稿的根（`~/Vault/` → `~/Vault`；草稿本身不动，dirty 判定不变） */
  function settle() {
    setRoot(vaultRootOf(raw));
  }

  /** 选择…：一次性的完整路径——框里显示派生出的根（选到 raw 目录本身也显示它的父目录），草稿收 raw */
  function pick(path: string) {
    const raw = rawDirOf(path);
    setRoot(vaultRootOf(raw));
    onChange(raw);
  }

  return (
    <>
      <input
        id={id}
        type="text"
        className="settings-input"
        value={root}
        disabled={disabled}
        spellCheck={false}
        placeholder={placeholder}
        onChange={(event) => type(event.target.value)}
        onBlur={settle}
      />
      <FolderPicker current={root.trim()} disabled={disabled} placeholder={placeholder} onPick={pick} />
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
  // tone：ok = 绿（建好了）；missing = 警告色但 role=status（目录不在、开了上级——不是错误，DepRows 的「显示」同一形）；error = alert
  const [note, setNote] = useState<{ tone: "ok" | "missing" | "error"; prefix?: string; message: string } | null>(null);
  const missing = field.path_exists === false;
  const hasValue = typeof field.effective === "string" && field.effective.trim() !== "";

  async function run(action: "open" | "create") {
    setBusy(true);
    setNote(null);
    try {
      if (action === "open") {
        // 目录不在 → server 开最近的既有祖先并回 add-only `missing`（§68.4 追记）；如实说一句，「创建」就在旁边
        const receipt = await postFolderOpen(field.key);
        if (receipt.missing) setNote({ tone: "missing", message: text("目录不存在，已打开上级目录", "Folder doesn't exist — opened its parent instead") });
      } else {
        const receipt = await postFolderCreate(field.key);
        setNote({ tone: "ok", message: receipt.created ? text("已创建。", "Created.") : text("目录已存在。", "The folder already exists.") });
        void refreshSettingsCatalog();
      }
    } catch (err) {
      // 原生 noteError(L("创建目录失败：", …) + error)：前缀与原文各自一个节点（探针按节点文本逐字判前缀）
      setNote({ tone: "error", prefix: action === "create" ? text("创建目录失败：", "Couldn't create the folder: ") : undefined, message: errorMessage(err) });
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
        <span className={note.tone === "ok" ? "settings-helper is-ok" : "settings-warning"} role={note.tone === "error" ? "alert" : "status"}>
          {note.prefix ? <span>{note.prefix}</span> : null}
          <span>{note.message}</span>
        </span>
      )}
    </div>
  );
}
