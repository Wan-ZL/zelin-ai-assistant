// 设置页 section「显示」（CONTRACT §54.1 第 12 项；owner 2026-09-02 4K 屏「框细字细」）。
// 三个 segmented control（Apple「文字大小」/「粗体文本」式）：文字大小 / 文字粗细 / 线条粗细，
// 词表来自 server 快照（text_sizes / text_weights / strokes），client 只有文案。
// 无保存键：点一档 = 立刻落 <html> data-*（store.saveDisplaySettings 先应用再 PUT，失败回滚 + toast）。
// 预览行用看板真实的类名（task-card / chip / btn）渲染，随 :root 变量即时变化——预览就是实物。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import type { DisplayField } from "../../displayPrefs";
import { useI18n } from "../../i18n";
import { refreshDisplaySettings, saveDisplaySettings, useAppState } from "../../store";

const TOAST_MS = 4000;

interface KnobSpec {
  field: DisplayField;
  /** 词表在快照里的键 */
  listKey: "text_sizes" | "text_weights" | "strokes";
}

const KNOBS: KnobSpec[] = [
  { field: "text_size", listKey: "text_sizes" },
  { field: "text_weight", listKey: "text_weights" },
  { field: "stroke", listKey: "strokes" },
];

export function DisplaySection() {
  const { text } = useI18n();
  const { displaySettings, settingsError } = useAppState();
  const [isSaving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  useEffect(() => {
    void refreshDisplaySettings();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  const title = text("显示", "Display");
  if (!displaySettings) {
    return (
      <section className="settings-section">
        <h3 className="settings-section-title">{title}</h3>
        <p className="settings-helper">{settingsError ?? text("读取中…", "Loading…")}</p>
      </section>
    );
  }

  const knobLabel = (field: DisplayField) =>
    field === "text_size" ? text("文字大小", "Text size")
      : field === "text_weight" ? text("文字粗细", "Text weight")
        : text("线条粗细", "Outline width");

  const optionLabel = (field: DisplayField, value: string) => {
    if (field === "text_size") {
      return value === "s" ? text("小", "Small") : value === "m" ? text("默认", "Default")
        : value === "l" ? text("大", "Large") : value === "xl" ? text("特大", "Extra large") : value;
    }
    if (field === "text_weight") {
      return value === "regular" ? text("常规", "Regular") : value === "medium" ? text("中等", "Medium")
        : value === "bold" ? text("粗体", "Bold") : value;
    }
    return value === "thin" ? text("细", "Thin") : value === "normal" ? text("默认", "Default")
      : value === "thick" ? text("粗", "Thick") : value;
  };

  async function choose(field: DisplayField, value: string) {
    // 一次只飞一个 PUT：两个回执乱序会让 <html> 落到旧值（同一把旋钮连点两档）
    if (isSaving || !displaySettings || displaySettings[field] === value) return;
    setSaving(true);
    setToast(null);
    try {
      await saveDisplaySettings({ [field]: value });
      setToast({ kind: "ok", message: text("已应用并保存。", "Applied and saved.") });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="settings-display-title">
      <h3 id="settings-display-title" className="settings-section-title">{title}</h3>
      <p className="settings-helper">
        {text(
          "看板全部文字与线条的显示方式。点选即时生效并保存，无需重启。系统「增强对比度」开着时线条会再加粗一档。",
          "How every piece of text and every outline on the board is drawn. Changes apply and save instantly. With the system's Increase Contrast on, outlines step up one more notch.",
        )}
      </p>

      {KNOBS.map(({ field, listKey }) => {
        const options = Array.isArray(displaySettings[listKey]) ? (displaySettings[listKey] as string[]) : [];
        const groupId = `display-${field}`;
        return (
          <fieldset key={field} className="settings-segmented">
            <legend className="settings-knob-label">{knobLabel(field)}</legend>
            <div className="settings-segments" role="presentation">
              {options.map((value) => {
                const id = `${groupId}-${value}`;
                return (
                  <label key={value} className="settings-segment" htmlFor={id}>
                    <input
                      id={id}
                      type="radio"
                      name={groupId}
                      value={value}
                      className="sr-only"
                      checked={displaySettings[field] === value}
                      onChange={() => void choose(field, value)}
                    />
                    <span className="settings-segment-face">{optionLabel(field, value)}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        );
      })}

      <div className="settings-knob">
        <span className="settings-knob-label" id="settings-display-preview-label">{text("预览", "Preview")}</span>
        <DisplayPreview />
      </div>

      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}

/** 一张缩小的卡头 + 章 + 动作行：与看板同一套类名，所以随 :root 的三把旋钮即时变化（不另设作用域） */
function DisplayPreview() {
  const { text } = useI18n();
  return (
    <div className="settings-display-preview" role="img" aria-labelledby="settings-display-preview-label">
      <div className="task-card">
        <div className="card-head">
          <span className="card-dot is-review" aria-hidden="true" />
          <div className="card-title">{text("把周报模板改成双语版本", "Make the weekly report template bilingual")}</div>
          <span className="card-id">R-128</span>
        </div>
        <div className="card-badges">
          <span className="chip chip-accent">{text("待验收", "Review")}</span>
          <span className="chip">your-workbench</span>
          <span className="chip chip-purple chip-quiet">{text("已并入×2", "Folded ×2")}</span>
        </div>
        <div className="card-actions">
          <span className="btn btn-success">{text("验收", "Accept")}</span>
          <span className="btn btn-warning">{text("打回…", "Send back…")}</span>
          <span className="btn">{text("暂缓", "Defer")}</span>
        </div>
      </div>
    </div>
  );
}
