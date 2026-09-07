// 向导第 5 步「笔记放在哪里?」（原生 SetupWizard.swift vaultStep 的 web 版，§68.5）：屏幕记录提炼出的笔记
// 落在哪个目录，雷达也从这里发现待办。行的顺序：当前生效的笔记库根（GET /api/permissions 的 vault.root =
// 生效 obsidian_raw 的父目录，override → config.yaml → 默认）· Obsidian 自己登记过的库一行一个（GET /api/setup/vaults
// = obsidian.json 里路径仍存在的条目，「Obsidian vault」徽章——原生 ObsidianVaults.registered，SetupWizard.swift:205-227 /
// 889-895；当前根就是其中一个时合成一行、两枚徽章都挂，D51）· 「不用 Obsidian — 存成普通 Markdown 文件夹」
// + 「选择…」：壳在场走 §61.1 桥 `chooseFolder`（NSOpenPanel 只选目录、可新建，起点 = 已选的自定义目录或原生默认
// ~/Documents/AI Assistant Notes，prompt「选择」——原生 chooseCustomFolder，SetupWizard.swift:975–985）；浏览器 /
// 老壳（NO_BRIDGE / UNKNOWN_METHOD）拿不到目录对话框——展开一个路径输入框，「选择」确认（§68.5 既有路）。
// 列表读不到（没装 Obsidian / server 老）→ 只剩当前 + 自定义两行，不报错（原生 registered() 缺文件 → []）。
// 「下一步」时若与当前不同 → PUT /api/settings/obsidian {obsidian_raw: <root>/2 - raw}（server diff-write，
// §15.3 同一键；根 → raw 的换算与 设置 → 笔记库 共用 `vaultPaths.rawDirOf`——同一把键两处一条规则，§68.1 追记）；
// 四个标准子目录由导出 / ingest 链首次落笔记时建（server 不替 web 建目录）。
// 文案逐字镜像 SetupWizard.swift:884–985。
import { Fragment, useEffect, useState } from "react";
import { fetchSetupVaults } from "../../api";
import { useI18n } from "../../i18n";
import { chooseFolder, hasShellBridge, isBridgeUnavailable } from "../../shellBridge";
import { saveSettingsSection, useAppState } from "../../store";
import type { SetupVault } from "../../types";
import { rawDirOf } from "../../vaultPaths";
import { errorMessage } from "../settings/useToast";

/** 原生 loadVaultChoices 的自定义目录默认值（也是对话框的起点与输入框的 placeholder） */
export const DEFAULT_CUSTOM_ROOT = "~/Documents/AI Assistant Notes";
/** 原生 vaultRow 的徽章字面（SetupWizard.swift:893；不翻译——产品名） */
export const OBSIDIAN_VAULT_BADGE = "Obsidian vault";

export interface VaultChoice {
  root: string;
  custom: boolean;
}

function basename(path: string): string {
  const parts = path.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || path;
}

/** 路径相等：结尾 / 不算（server 两边都给绝对路径——permissions.vault_root 已 expanduser，obsidian.json 本来就是绝对的） */
function samePath(a: string, b: string): boolean {
  const norm = (p: string) => p.replace(/\/+$/, "") || "/";
  return norm(a) === norm(b);
}

/** server 回的列表逐字段消毒（LLM 不可信的纪律同样适用于老 server / 手改文件）：只留 name、path 都是非空字串的 */
export function sanitizeVaults(input: unknown): SetupVault[] {
  const list = input && typeof input === "object" && Array.isArray((input as { vaults?: unknown }).vaults)
    ? ((input as { vaults: unknown[] }).vaults)
    : [];
  return list.flatMap((v) => {
    if (!v || typeof v !== "object") return [];
    const { name, path } = v as { name?: unknown; path?: unknown };
    if (typeof path !== "string" || !path.trim()) return [];
    return [{ name: typeof name === "string" && name ? name : basename(path), path }];
  });
}

export function VaultStep({ choice, onChoose, error }: { choice: VaultChoice | null; onChoose: (c: VaultChoice) => void; error: string | null }) {
  const { text } = useI18n();
  const { permissions } = useAppState();
  const currentRoot = permissions?.vault?.root ?? "";
  const [vaults, setVaults] = useState<SetupVault[]>([]);
  const [customRoot, setCustomRoot] = useState("");
  const [chooserOpen, setChooserOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);

  // 预填当前生效值（原生 loadVaultChoices：重跑向导落在正在用的那个）
  useEffect(() => {
    if (!choice && currentRoot) onChoose({ root: currentRoot, custom: false });
  }, [choice, currentRoot, onChoose]);

  // Obsidian 登记过的库（原生 ObsidianVaults.registered）：一次拉取；失败 / 老 server 404 → 没有这些行，不报错
  useEffect(() => {
    const controller = new AbortController();
    fetchSetupVaults(controller.signal).then(
      (snapshot) => { if (!controller.signal.aborted) setVaults(sanitizeVaults(snapshot)); },
      () => { /* 没装 Obsidian 是常态；读不到就不列 */ },
    );
    return () => controller.abort();
  }, []);

  const selected = choice?.root ?? currentRoot;
  const isSelectedRoot = (root: string) => !choice?.custom && Boolean(selected) && samePath(selected, root);
  const currentIsRegistered = Boolean(currentRoot) && vaults.some((v) => samePath(v.path, currentRoot));

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

  /** 一行一个候选根（当前 / Obsidian 库）：整行可点，◉ / ○ 是同一个 button（原生 vaultRow 的 Button 包整行）。
   *  可访问名 = 库名 + 路径（原生 Button 的 label 就是整行内容，VoiceOver 连路径一起读）——两座同名库（如
   *  ~/Notes 与 /Volumes/ext/Notes）只靠 basename 分不开，路径不在 button 里读屏就不知道选的是哪一个 */
  function rootRow(root: string, title: string, badges: string[], key: string) {
    const on = isSelectedRoot(root);
    return (
      <div key={key} className={`setup-vault-row${on ? " is-selected" : ""}`} data-vault-root={root}>
        <button type="button" className="setup-vault-radio" aria-pressed={on} aria-label={`${title} — ${root}`} onClick={() => onChoose({ root, custom: false })}>
          {on ? "◉" : "○"}
        </button>
        <span>
          <div className="settings-list-title">
            {title}
            {badges.map((badge) => <Fragment key={badge}> <span className="setup-vault-badge">{badge}</span></Fragment>)}
          </div>
          <div className="settings-list-dim">{root}</div>
        </span>
      </div>
    );
  }

  return (
    <>
      {currentRoot && !currentIsRegistered && rootRow(currentRoot, basename(currentRoot), [text("当前", "current")], "current")}
      {vaults.map((v) => rootRow(
        v.path,
        v.name,
        samePath(v.path, currentRoot) ? [OBSIDIAN_VAULT_BADGE, text("当前", "current")] : [OBSIDIAN_VAULT_BADGE],
        `vault:${v.path}`,
      ))}
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

/** 「下一步」时落盘：与当前生效根相同则不写（原生 applyVaultChoice 的 diff-write）；失败原句回给页面、不放行。
 *  落的 raw = `rawDirOf(root)`（`<根>/2 - raw`；选到的就是 `2 - raw` 目录本身则原样——与设置页同一条规则） */
export async function applyVaultChoice(choice: VaultChoice | null, currentRoot: string): Promise<string | null> {
  const root = choice?.root.trim() ?? "";
  if (!root || root === currentRoot) return null;
  try {
    await saveSettingsSection("obsidian", { obsidian_raw: rawDirOf(root) });
    return null;
  } catch (err) {
    return errorMessage(err);
  }
}
