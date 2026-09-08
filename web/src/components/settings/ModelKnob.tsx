// 一把模型旋钮的控件（§59）：<select>（哨兵 / canonical ids / 自定义…）+ 自定义时的文本框。
// 值的三态：哨兵（dispatch / pipeline 是 server 的 follow；fallback（D53）是 off）| canonical id | 自由文本
// （server 照收，附整句 WARN）。纯受控组件：不碰 store，不发请求；父组件（ModelsSection）持有草稿并统一 PUT。
import { useI18n } from "../../i18n";

export const CUSTOM_CHOICE = "__custom__";

export interface ModelKnobProps {
  /** wire 键名（dispatch | pipeline | fallback），也是 select 的可访问名前缀 */
  mode: string;
  label: string;
  /** 一句话解释这把旋钮管什么（"手" / "脑" / "回退"） */
  helper: string;
  /** 当前草稿值：哨兵 | 模型 id */
  value: string;
  /** 哨兵值（server 的 follow / off） */
  follow: string;
  canonical: string[];
  /** follow 选项里显示的全局默认（null = 未设置）；sentinelLabel 给了时不用 */
  globalDefault: string | null;
  /** D53：哨兵不是 follow 时的选项文案（fallback 的「关闭回退」） */
  sentinelLabel?: string;
  /** D53：排在 canonical 之前、带「（默认）」标注的出厂值（fallback 的 claude-opus-5[1m] 不在 canonical 表里） */
  defaultChoice?: string;
  isCustom: boolean;
  onChoose: (choice: string) => void;
  onCustomText: (text: string) => void;
}

export function ModelKnob({
  mode, label, helper, value, follow, canonical, globalDefault, sentinelLabel, defaultChoice,
  isCustom, onChoose, onCustomText,
}: ModelKnobProps) {
  const { text } = useI18n();
  const selectValue = isCustom ? CUSTOM_CHOICE : value;
  const followLabel = sentinelLabel ?? (globalDefault
    ? text(`跟随 Claude Code 全局（当前 ${globalDefault}）`, `Follow Claude Code default (now ${globalDefault})`)
    : text("跟随 Claude Code 全局（未设置 → CLI 内置默认）", "Follow Claude Code default (unset → CLI built-in)"));
  const showDefaultChoice = defaultChoice !== undefined && !canonical.includes(defaultChoice);

  return (
    <div className="settings-knob">
      <label className="settings-knob-label" htmlFor={`model-${mode}`}>{label}</label>
      <div className="settings-knob-controls">
        <select
          id={`model-${mode}`}
          className="settings-select"
          value={selectValue}
          onChange={(event) => onChoose(event.target.value)}
        >
          <option value={follow}>{followLabel}</option>
          {showDefaultChoice && (
            <option value={defaultChoice}>{text(`${defaultChoice}（默认）`, `${defaultChoice} (default)`)}</option>
          )}
          {canonical.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
          <option value={CUSTOM_CHOICE}>{text("自定义…", "Custom…")}</option>
        </select>
        {isCustom && (
          <input
            className="settings-input"
            type="text"
            aria-label={text(`${label} 自定义模型 id`, `${label} custom model id`)}
            placeholder="claude-…"
            value={value === follow ? "" : value}
            onChange={(event) => onCustomText(event.target.value)}
            spellCheck={false}
          />
        )}
      </div>
      <p className="settings-helper">{helper}</p>
      {isCustom && (
        <p className="settings-warning">
          {text(
            "别名 / 后缀（如 [1m]、-eap）随时可能下线——下线那天这里的调用会静默全败。能选 canonical id 就选它。",
            "Aliases / suffixes (like [1m], -eap) can disappear any day — the day they do, these calls fail silently. Prefer a canonical id.",
          )}
        </p>
      )}
    </div>
  );
}
