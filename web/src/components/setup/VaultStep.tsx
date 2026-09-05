// 向导第 5 步「笔记放在哪里?」（原生 SetupWizard.swift vaultStep 的 web 版，§68.5）：屏幕记录提炼出的笔记
// 落在哪个目录，雷达也从这里发现待办。两行单选：当前生效的笔记库根（GET /api/permissions 的 vault.root =
// 生效 obsidian_raw 的父目录，override → config.yaml → 默认）· 「不用 Obsidian — 存成普通 Markdown 文件夹」
// + 「选择…」：壳在场走 §61.1 桥 `chooseFolder`（NSOpenPanel 只选目录、可新建，起点 = 已选的自定义目录或原生默认
// ~/Documents/AI Assistant Notes，prompt「选择」——原生 chooseCustomFolder，SetupWizard.swift:975–985）；浏览器 /
// 老壳（NO_BRIDGE / UNKNOWN_METHOD）拿不到目录对话框——展开一个路径输入框，「选择」确认（§68.5 既有路）。
// 「下一步」时若与当前不同 → PUT /api/settings/obsidian {obsidian_raw: <root>/2 - raw}（server diff-write，
// §15.3 同一键）；四个标准子目录由导出 / ingest 链首次落笔记时建（server 不替 web 建目录）。
// 文案逐字镜像 SetupWizard.swift:884–985。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { chooseFolder, hasShellBridge, isBridgeUnavailable } from "../../shellBridge";
import { saveSettingsSection, useAppState } from "../../store";
import { errorMessage } from "../settings/useToast";

export const RAW_SUBDIR = "2 - raw";
/** 原生 loadVaultChoices 的自定义目录默认值（也是对话框的起点与输入框的 placeholder） */
export const DEFAULT_CUSTOM_ROOT = "~/Documents/AI Assistant Notes";

export interface VaultChoice {
  root: string;
  custom: boolean;
}

function basename(path: string): string {
  const parts = path.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || path;
}

export function VaultStep({ choice, onChoose, error }: { choice: VaultChoice | null; onChoose: (c: VaultChoice) => void; error: string | null }) {
  const { text } = useI18n();
  const { permissions } = useAppState();
  const currentRoot = permissions?.vault?.root ?? "";
  const [customRoot, setCustomRoot] = useState("");
  const [chooserOpen, setChooserOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);

  // 预填当前生效值（原生 loadVaultChoices：重跑向导落在正在用的那个）
  useEffect(() => {
    if (!choice && currentRoot) onChoose({ root: currentRoot, custom: false });
  }, [choice, currentRoot, onChoose]);

  const selected = choice?.root ?? currentRoot;

  function pickCustom(root: string) {
    setCustomRoot(root);
    onChoose({ root, custom: true });
    setChooserOpen(false);
  }

  function openFallback() {
    setDraft(customRoot);
    setChooserOpen(true);
  }

  /** 「选择…」：壳在场开 NSOpenPanel（取消不动）；浏览器 / 老壳退化成路径输入框；桥真出错 → 原文 */
  async function choose() {
    setDialogError(null);
    if (!hasShellBridge()) {
      setChooserOpen((v) => !v);
      setDraft(customRoot);
      return;
    }
    try {
      const picked = await chooseFolder({ current: customRoot || DEFAULT_CUSTOM_ROOT, prompt: text("选择", "Choose") });
      if (picked) pickCustom(picked);
    } catch (err) {
      if (isBridgeUnavailable(err)) openFallback();
      else setDialogError(errorMessage(err));
    }
  }

  return (
    <>
      {currentRoot && (
        <div className={`setup-vault-row${!choice?.custom && selected === currentRoot ? " is-selected" : ""}`}>
          <button type="button" className="setup-vault-radio" aria-pressed={!choice?.custom && selected === currentRoot} aria-label={basename(currentRoot)} onClick={() => onChoose({ root: currentRoot, custom: false })}>
            {!choice?.custom && selected === currentRoot ? "◉" : "○"}
          </button>
          <span>
            <div className="settings-list-title">{basename(currentRoot)} <span className="setup-vault-badge">{text("当前", "current")}</span></div>
            <div className="settings-list-dim">{currentRoot}</div>
          </span>
        </div>
      )}
      <div className={`setup-vault-row${choice?.custom ? " is-selected" : ""}`}>
        <button type="button" className="setup-vault-radio" aria-pressed={Boolean(choice?.custom)} aria-label={text("不用 Obsidian — 存成普通 Markdown 文件夹", "No Obsidian — plain markdown folder")}
          onClick={() => { if (customRoot) onChoose({ root: customRoot, custom: true }); else void choose(); }}>
          {choice?.custom ? "◉" : "○"}
        </button>
        <span className="setup-footer-spacer">
          <div className="settings-list-title">{text("不用 Obsidian — 存成普通 Markdown 文件夹", "No Obsidian — plain markdown folder")}</div>
          <div className="settings-list-dim">{customRoot || text("（还没选文件夹）", "(no folder chosen yet)")}</div>
        </span>
        <button type="button" className="btn" onClick={() => void choose()}>{text("选择…", "Choose…")}</button>
      </div>
      {chooserOpen && (
        <div className="settings-knob-controls">
          <input type="text" className="settings-input" aria-label={text("文件夹路径", "Folder path")} placeholder={DEFAULT_CUSTOM_ROOT} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <button type="button" className="btn btn-primary" disabled={!draft.trim()} onClick={() => pickCustom(draft.trim())}>{text("选择", "Choose")}</button>
        </div>
      )}
      <p className="settings-helper">{text("所选位置下的 4 个标准子目录(1 - unprocessed / 2 - raw / 3 - change-summary / 4 - wiki)由录制导出与 ingest 首次写入时创建;之后可在 设置 → 笔记库 修改。", "The four standard subfolders (1 - unprocessed / 2 - raw / 3 - change-summary / 4 - wiki) are created inside when the export / ingest chain first writes; changeable later in Settings → Notes vault.")}</p>
      {dialogError && <p className="settings-warning" role="alert">{dialogError}</p>}
      {error && <p className="settings-warning" role="alert">{error}</p>}
    </>
  );
}

/** 「下一步」时落盘：与当前生效根相同则不写（原生 applyVaultChoice 的 diff-write）；失败原句回给页面、不放行 */
export async function applyVaultChoice(choice: VaultChoice | null, currentRoot: string): Promise<string | null> {
  const root = choice?.root.trim() ?? "";
  if (!root || root === currentRoot) return null;
  try {
    await saveSettingsSection("obsidian", { obsidian_raw: `${root.replace(/\/+$/, "")}/${RAW_SUBDIR}` });
    return null;
  } catch (err) {
    return errorMessage(err);
  }
}
