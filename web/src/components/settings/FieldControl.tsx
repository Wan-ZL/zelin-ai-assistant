// 一个目录 field 的控件（§68 通用设置区）：bool → 开关（checkbox role=switch）、enum → <select>、
// string → 文本框、number/int → 数字框、list → 逗号分隔文本框（草稿存字串，server 拆表）。纯受控：值与回调都来自 CatalogSection 的草稿。
// 目录字段（`path: "dir"`，§68.1）= string 文本框 + 「选择…」（壳桥 NSOpenPanel / 浏览器路径框）+ 「打开」/「创建」
// 与「目录不存在」警告（FolderControls；作用于已保存的值，草稿未保存时禁用）。
// 选项文案逐字镜像原生 Settings.swift 的 Picker 标签（§66.2 control:settings.*）。
// 文案（label / help）是 server-owned 双语键，按 UI 语言取；来源章（override / config / default）
// 让用户知道当前值是谁定的——原生 Settings 没有这一章，web 加它是为了「等于 config 即删键」
// 的 diff-write 语义可见（否则改回 config 值后开关看着没变、文件却少了一行）。
// `disabled` = 同 section 其它草稿值把这一格禁掉（原生 telemetry 组的 `.disabled(...)`，规则在 draftRules.isGated）；
// 数字框的校验句按 kind：number 用原生 commitShowCost / commitConfirmAbove 的通式（示例数 = 目录 default），
// int 的 trash_retention_days 用原生 commitTrashDays 的整句（Settings.swift:1727），其它 int 用整数通式。
// 带 `check`（§68.1 追记；今日词表 email）的 string 字段：保存前镜像 server 的同一条形状规则（draftRules.passesCheck），
// 不合格 = aria-invalid + server-owned 的那句（原生 SettingsGmail.validateAddress 在 saveAddress 里拦），CatalogSection 据此不放行「保存」。
// 「Obsidian Vault 位置」（`obsidian_raw`，§68.1 追记 vault 根）是目录字段里的特例：草稿 / PUT 仍是 raw 目录 `<根>/2 - raw`，
// 但框里显示、对话框起点与落进草稿的都按 **vault 根**换算（VaultRootField；原生 Settings.swift:740-792 一格 vault 根字段）。
import { useI18n, type Language } from "../../i18n";
import type { SettingsField } from "../../types";
import { VAULT_RAW_KEY } from "../../vaultPaths";
import { pickText } from "./catalogText";
import { isValidNumberDraft, passesCheck } from "./draftRules";
import { FolderActions, FolderPicker, VaultRootField } from "./FolderControls";

export interface FieldControlProps {
  sectionId: string;
  field: SettingsField;
  /** 草稿值（与 effective 类型同：bool / string / number） */
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  isBusy?: boolean;
  /** 被同 section 的其它草稿值禁用（联动；与 isBusy 的区别：整格淡显，不是保存中的瞬态） */
  disabled?: boolean;
}

/** 草稿值不合 field.check 的规则 → server-owned 的那句；合格 / 空值（= 清键，server 也不查）/ 无 check / 未知 kind → null */
export function fieldProblem(field: SettingsField, value: unknown, language: Language): string | null {
  return field.check && !passesCheck(field, value) ? pickText(field.check.message, language) : null;
}

/** 数字框的校验句（原生每个数字字段各有一句；web 按 kind + key 取，示例数 = 目录 default） */
export function numberHint(field: SettingsField, text: (zh: string, en: string) => string): string {
  const example = typeof field.default === "number" ? String(field.default) : "5";
  if (field.kind === "int") {
    if (field.key === "trash_retention_days") {
      return text("请输入整数天数，如 60（0 = 永不自动清）", "Enter a whole number of days, e.g. 60 (0 = never auto-purge)");
    }
    return text(`请输入不小于 0 的整数，如 ${example}`, `Enter a whole number ≥ 0, e.g. ${example}`);
  }
  return text(`请输入不小于 0 的数字，如 ${example}`, `Enter a number ≥ 0, e.g. ${example}`);
}

/** 来源章的文案（三值闭枚举，未知原样） */
export function sourceLabel(source: string, text: (zh: string, en: string) => string): string {
  switch (source) {
    case "override":
      return text("已在设置里改过", "set here");
    case "config":
      return text("来自 config.yaml", "from config.yaml");
    case "default":
      return text("出厂默认", "default");
    default:
      return source;
  }
}

/** enum 选项的展示名（词表小、常见值给双语；未知原样） */
export function choiceLabel(key: string, choice: string, text: (zh: string, en: string) => string): string {
  const table: Record<string, [string, string]> = {
    "review_notify:off": ["关", "Off"],
    "review_notify:banner": ["横幅", "Banner"],
    "review_notify:sound": ["横幅+声音", "Banner + sound"],
    "digest_frequency:off": ["关", "Off"],
    "digest_frequency:daily": ["每天", "Daily"],
    "digest_frequency:every2days": ["每两天", "Every 2 days"],
    "digest_frequency:weekly": ["每周", "Weekly"],
    "language:zh": ["中文 (zh)", "中文 (zh)"],
    "language:en": ["English (en)", "English (en)"],
    "default_output_format:markdown": ["Markdown", "Markdown"],
    "default_output_format:html": ["HTML", "HTML"],
    "telemetry.level:basic": ["基础", "Basic"],
    "telemetry.level:detailed": ["详细（默认）", "Detailed (default)"],
    // 原生 TerminalApp.displayName（Ghostty / Terminal / iTerm2）+ web 才有的 auto（原生 preferred 的兜底规则）
    "terminal_app:auto": ["自动（装了 Ghostty 就用它，否则 Terminal）", "Auto (Ghostty if installed, else Terminal)"],
    "terminal_app:ghostty": ["Ghostty", "Ghostty"],
    "terminal_app:terminal": ["Terminal", "Terminal"],
    "terminal_app:iterm2": ["iTerm2", "iTerm2"],
  };
  const hit = table[`${key}:${choice}`];
  return hit ? text(hit[0], hit[1]) : choice;
}

export function FieldControl({ sectionId, field, value, onChange, isBusy = false, disabled = false }: FieldControlProps) {
  const { text, language } = useI18n();
  const id = `setting-${sectionId}-${field.key.replace(/[^a-z0-9_-]/gi, "-")}`;
  const label = pickText(field.label, language);
  const help = pickText(field.help, language);
  const off = isBusy || disabled;

  let control;
  if (field.kind === "bool") {
    control = (
      <input
        id={id}
        type="checkbox"
        role="switch"
        className="settings-switch"
        checked={value === true}
        disabled={off}
        aria-checked={value === true}
        onChange={(event) => onChange(field.key, event.target.checked)}
      />
    );
  } else if (field.kind === "enum") {
    control = (
      <select
        id={id}
        className="settings-select"
        value={typeof value === "string" ? value : ""}
        disabled={off}
        onChange={(event) => onChange(field.key, event.target.value)}
      >
        {(field.choices ?? []).map((choice) => (
          <option key={choice} value={choice}>{choiceLabel(field.key, choice, text)}</option>
        ))}
      </select>
    );
  } else if (field.kind === "number" || field.kind === "int") {
    // 原生 numberField：解析失败红字提示、写 NOTHING——非法草稿由 CatalogSection 挡「保存」并剔出 PUT（draftRules）
    const invalid = !isValidNumberDraft(field.kind, value);
    control = (
      <>
        <input
          id={id}
          type="number"
          className="settings-input is-number"
          step={field.kind === "int" ? 1 : "any"}
          min={0}
          value={typeof value === "number" ? value : ""}
          disabled={off}
          aria-invalid={invalid || undefined}
          onChange={(event) => onChange(field.key, event.target.value === "" ? null : Number(event.target.value))}
        />
        {/* 原生数字框的校验提示（Settings.swift:1703 / 1715 / 1727），句子按 kind + key（numberHint） */}
        {invalid && <span className="settings-warning">{numberHint(field, text)}</span>}
      </>
    );
  } else if (field.path === "dir" && field.key === VAULT_RAW_KEY) {
    // 「Obsidian Vault 位置」：显示 vault 根（= 存值的父目录）、敲字 / 选择… 落 `<根>/2 - raw`；placeholder 缺席时用默认根
    control = (
      <VaultRootField
        id={id}
        raw={typeof value === "string" ? value : ""}
        disabled={off}
        placeholder={pickText(field.placeholder, language) || undefined}
        onChange={(raw) => onChange(field.key, raw)}
      />
    );
  } else {
    // string 与 list 同一个文本框：list 的草稿是逗号分隔字串（CatalogSection.draftOf 拼、server 拆）
    const fallback = typeof field.default === "string" && field.default ? field.default : text("（未设置）", "(unset)");
    const isFolder = field.path === "dir";
    const problem = fieldProblem(field, value, language);
    control = (
      <>
        <input
          id={id}
          type={field.check?.kind === "email" ? "email" : "text"}
          className="settings-input"
          value={typeof value === "string" ? value : ""}
          disabled={off}
          spellCheck={false}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-problem` : undefined}
          placeholder={pickText(field.placeholder, language) || fallback}
          onChange={(event) => onChange(field.key, event.target.value)}
        />
        {isFolder && (
          <FolderPicker
            current={typeof value === "string" ? value : ""}
            disabled={off}
            placeholder={pickText(field.placeholder, language) || undefined}
            onPick={(path) => onChange(field.key, path)}
          />
        )}
        {/* 原生 addressNote 的错误态（validateAddress 那句，server-owned）；「保存」在 CatalogSection 里随之不放行 */}
        {problem && <span id={`${id}-problem`} className="settings-warning">{problem}</span>}
      </>
    );
  }

  const dirty = String(value ?? "").trim() !== String(field.effective ?? "").trim();
  return (
    <div className={`settings-field is-${field.kind}${field.path === "dir" ? " is-folder" : ""}${disabled ? " is-gated" : ""}`}>
      <div className="settings-field-head">
        <label className="settings-knob-label" htmlFor={id}>{label}</label>
        <span className="settings-source-chip" data-source={field.source}>{sourceLabel(field.source, text)}</span>
      </div>
      <div className="settings-knob-controls">{control}</div>
      {field.path === "dir" && <FolderActions field={field} dirty={dirty} />}
      {help && <p className="settings-helper">{help}</p>}
    </div>
  );
}
