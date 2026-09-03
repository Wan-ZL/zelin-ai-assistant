// 通用设置区（§68）：按 server 目录（GET /api/settings）渲染一个 section 的全部 field，
// 草稿 → 「保存」一次 PUT 改动过的键（server diff-write：等于 config 层即删键）。
// 目录里没有的 section id（老 server）渲染成一行说明而不是空白；文案全部 server-owned。
// `children` 是 section 尾部的装饰槽（来源区放健康摘要 + 凭证行；通用区留空）。
import { Fragment, useEffect, useState, type ReactNode } from "react";
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
  /** 只渲染这些 field（原生把同一 section 的字段拆在不同位置时用；缺省全部） */
  only?: string[];
  /** 插在标题之后、field 之前的装饰槽（引导步骤 / 链接） */
  lead?: ReactNode;
  /** 紧跟某个 field 之后的装饰槽（key = field.key；原生在字段之间夹步骤说明 / 凭证行时用） */
  between?: Record<string, ReactNode>;
  children?: ReactNode;
}

type Draft = Record<string, unknown>;

/** list 字段的草稿形：逗号分隔字串（server PUT 接受这个形，空 = 清键） */
export function listDraft(value: unknown): string {
  return Array.isArray(value) ? value.map((v) => String(v)).join(", ") : "";
}

const normalizeList = (value: unknown): string => (Array.isArray(value) ? value.map(String) : String(value ?? "").split(/[\n,]/))
  .map((v) => v.trim()).filter(Boolean).join(",");

function draftOf(section: SettingsSection): Draft {
  const out: Draft = {};
  for (const field of section.fields) out[field.key] = field.kind === "list" ? listDraft(field.effective) : field.effective;
  return out;
}

/** 与 effective 不同的键（string 比较时 trim；list 按归一后的项集；number 用 ===） */
export function changedKeys(section: SettingsSection, draft: Draft): string[] {
  return section.fields
    .filter((field) => {
      const before = field.effective;
      const after = draft[field.key];
      if (field.kind === "string") return String(before ?? "").trim() !== String(after ?? "").trim();
      if (field.kind === "list") return normalizeList(before) !== normalizeList(after);
      return before !== after;
    })
    .map((field) => field.key);
}

export function CatalogSection({ sectionId, titleOverride, only, lead, between, children }: CatalogSectionProps) {
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

  const visible = only ? { ...section, fields: section.fields.filter((f) => only.includes(f.key)) } : section;
  const dirty = draft ? changedKeys(visible, draft) : [];

  async function save() {
    if (!draft || dirty.length === 0) return;
    setSaving(true);
    setToast(null);
    const patch: Record<string, unknown> = {};
    for (const key of dirty) patch[key] = draft[key];
    try {
      await saveSettingsSection(visible.id, patch);
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
      {lead}
      {draft && section.fields.filter((field) => !only || only.includes(field.key)).map((field) => (
        <Fragment key={field.key}>
          <FieldControl
            sectionId={section.id}
            field={field}
            value={draft[field.key]}
            isBusy={isSaving}
            onChange={(key, value) => setDraft((d) => (d ? { ...d, [key]: value } : d))}
          />
          {between?.[field.key]}
        </Fragment>
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
