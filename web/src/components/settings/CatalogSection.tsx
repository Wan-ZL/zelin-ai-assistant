// 通用设置区（§68）：按 server 目录（GET /api/settings）渲染一个 section 的全部 field，
// 草稿 → 「保存」一次 PUT 改动过的键（server diff-write：等于 config 层即删键）。
// 目录里没有的 section id（老 server）渲染成一行说明而不是空白；文案全部 server-owned。
// `children` 是 section 尾部的装饰槽（来源区放健康摘要 + 凭证行；通用区留空）。
import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { refreshSettingsCatalog, saveSettingsSection, useAppState } from "../../store";
import type { SettingsSection } from "../../types";
import { pickText } from "./catalogText";
import { FieldControl } from "./FieldControl";
import { errorMessage, useToast } from "./useToast";

export interface CatalogSectionProps {
  sectionId: string;
  /** 覆盖目录标题（如「来源开关」区想加副标题）；缺省用 server 目录的 title */
  titleOverride?: string;
  children?: ReactNode;
}

type Draft = Record<string, unknown>;

function draftOf(section: SettingsSection): Draft {
  const out: Draft = {};
  for (const field of section.fields) out[field.key] = field.effective;
  return out;
}

/** 与 effective 不同的键（string 比较时 trim；number 用 ===） */
export function changedKeys(section: SettingsSection, draft: Draft): string[] {
  return section.fields
    .filter((field) => {
      const before = field.effective;
      const after = draft[field.key];
      if (field.kind === "string") return String(before ?? "").trim() !== String(after ?? "").trim();
      return before !== after;
    })
    .map((field) => field.key);
}

export function CatalogSection({ sectionId, titleOverride, children }: CatalogSectionProps) {
  const { text, language } = useI18n();
  const { settingsCatalog, pageErrors } = useAppState();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [toast, setToast] = useToast();

  useEffect(() => {
    if (!settingsCatalog) void refreshSettingsCatalog();
  }, [settingsCatalog]);

  const section = settingsCatalog?.sections.find((s) => s.id === sectionId) ?? null;
  const fingerprint = section ? JSON.stringify(section.fields.map((f) => [f.key, f.effective])) : "";

  // server 快照到了 / 保存回执到了 → 草稿对齐（草稿只在用户编辑期间领先于 server）
  useEffect(() => {
    if (section) setDraft(draftOf(section));
  }, [fingerprint]); // eslint-disable-line react-hooks/exhaustive-deps

  const title = titleOverride ?? (section ? pickText(section.title, language) : sectionId);
  const error = pageErrors.settingsCatalog;

  if (!section) {
    return (
      <section className="settings-section" id={`settings-${sectionId}`} aria-labelledby={`settings-${sectionId}-title`}>
        <h3 id={`settings-${sectionId}-title`} className="settings-section-title">{title}</h3>
        {error && !settingsCatalog
          ? <p className="settings-error" role="alert">{error}</p>
          : <p className="settings-helper">
            {settingsCatalog
              ? text("这个 server 版本没有这一区（升级后出现）。", "This server version has no such section (appears after an upgrade).")
              : text("读取中…", "Loading…")}
          </p>}
        {children}
      </section>
    );
  }

  const dirty = draft ? changedKeys(section, draft) : [];

  async function save() {
    if (!draft || dirty.length === 0) return;
    setSaving(true);
    setToast(null);
    const patch: Record<string, unknown> = {};
    for (const key of dirty) patch[key] = draft[key];
    try {
      await saveSettingsSection(section!.id, patch);
      setToast({ kind: "ok", message: text("已保存。", "Saved.") });
    } catch (err) {
      setToast({ kind: "error", message: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-section" id={`settings-${sectionId}`} aria-labelledby={`settings-${sectionId}-title`}>
      <h3 id={`settings-${sectionId}-title`} className="settings-section-title">{title}</h3>
      {pickText(section.help, language) && <p className="settings-helper">{pickText(section.help, language)}</p>}
      {draft && section.fields.map((field) => (
        <FieldControl
          key={field.key}
          sectionId={section.id}
          field={field}
          value={draft[field.key]}
          isBusy={isSaving}
          onChange={(key, value) => setDraft((d) => (d ? { ...d, [key]: value } : d))}
        />
      ))}
      <div className="settings-actions">
        <button type="button" className="btn btn-primary" disabled={dirty.length === 0 || isSaving} onClick={() => void save()}>
          {isSaving ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        {dirty.length > 0 && (
          <span className="settings-helper">{text(`${dirty.length} 项未保存`, `${dirty.length} unsaved`)}</span>
        )}
      </div>
      {children}
      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}
