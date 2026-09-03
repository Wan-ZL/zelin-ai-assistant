// 一个目录 field 的控件（§68 通用设置区）：bool → 开关（checkbox role=switch）、enum → <select>、
// string → 文本框、number/int → 数字框。纯受控：值与回调都来自 CatalogSection 的草稿。
// 文案（label / help）是 server-owned 双语键，按 UI 语言取；来源章（override / config / default）
// 让用户知道当前值是谁定的——原生 Settings 没有这一章，web 加它是为了「等于 config 即删键」
// 的 diff-write 语义可见（否则改回 config 值后开关看着没变、文件却少了一行）。
import { useI18n } from "../../i18n";
import type { SettingsField } from "../../types";
import { pickText } from "./catalogText";

export interface FieldControlProps {
  sectionId: string;
  field: SettingsField;
  /** 草稿值（与 effective 类型同：bool / string / number） */
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  isBusy?: boolean;
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
    "review_notify:sound": ["横幅 + 提示音", "Banner + sound"],
    "digest_frequency:off": ["关", "Off"],
    "digest_frequency:daily": ["每天", "Daily"],
    "digest_frequency:every2days": ["每两天", "Every 2 days"],
    "digest_frequency:weekly": ["每周", "Weekly"],
    "language:zh": ["中文", "Chinese"],
    "language:en": ["English", "English"],
    "telemetry.level:basic": ["基础", "Basic"],
    "telemetry.level:detailed": ["详细", "Detailed"],
  };
  const hit = table[`${key}:${choice}`];
  return hit ? text(hit[0], hit[1]) : choice;
}

export function FieldControl({ sectionId, field, value, onChange, isBusy = false }: FieldControlProps) {
  const { text, language } = useI18n();
  const id = `setting-${sectionId}-${field.key.replace(/[^a-z0-9_-]/gi, "-")}`;
  const label = pickText(field.label, language);
  const help = pickText(field.help, language);

  let control;
  if (field.kind === "bool") {
    control = (
      <input
        id={id}
        type="checkbox"
        role="switch"
        className="settings-switch"
        checked={value === true}
        disabled={isBusy}
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
        disabled={isBusy}
        onChange={(event) => onChange(field.key, event.target.value)}
      >
        {(field.choices ?? []).map((choice) => (
          <option key={choice} value={choice}>{choiceLabel(field.key, choice, text)}</option>
        ))}
      </select>
    );
  } else if (field.kind === "number" || field.kind === "int") {
    control = (
      <input
        id={id}
        type="number"
        className="settings-input is-number"
        step={field.kind === "int" ? 1 : "any"}
        min={0}
        value={typeof value === "number" ? value : ""}
        disabled={isBusy}
        onChange={(event) => onChange(field.key, event.target.value === "" ? null : Number(event.target.value))}
      />
    );
  } else {
    control = (
      <input
        id={id}
        type="text"
        className="settings-input"
        value={typeof value === "string" ? value : ""}
        disabled={isBusy}
        spellCheck={false}
        placeholder={typeof field.default === "string" && field.default ? field.default : text("（未设置）", "(unset)")}
        onChange={(event) => onChange(field.key, event.target.value)}
      />
    );
  }

  return (
    <div className={`settings-field is-${field.kind}`}>
      <div className="settings-field-head">
        <label className="settings-knob-label" htmlFor={id}>{label}</label>
        <span className="settings-source-chip" data-source={field.source}>{sourceLabel(field.source, text)}</span>
      </div>
      <div className="settings-knob-controls">{control}</div>
      {help && <p className="settings-helper">{help}</p>}
    </div>
  );
}
