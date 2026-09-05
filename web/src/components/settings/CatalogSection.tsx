// 通用设置区（§68）：按 server 目录（GET /api/settings）渲染一个 section 的全部 field，
// 草稿 → 「保存」一次 PUT 改动过的键（server diff-write：等于 config 层即删键）。
// 目录里没有的 section id（老 server）渲染成一行说明而不是空白；文案全部 server-owned。
// `children` 是 section 尾部的装饰槽（来源区放健康摘要 + 凭证行；通用区留空）。
// 草稿三条守则（§68.1 追记；原生 Settings.swift 头注「NO deferred save」的 web 对应）：
// (a) 同 section 的即时写者（Slack 勾选器 / 目录「创建」）刷新目录时**合并**而不是重置——用户改过的键留草稿、
//     其余键跟新 effective；同一个键两边都动了以 server 为准（草稿不得静默撤回别处刚落盘的写）；
//     自己「保存」成功后草稿对齐回执（原生：commit 成功后显示值 = effective）。
// (b) 有未保存改动时挂 beforeunload（rail / ⌘1…⌘7 / /open 都是整页导航，这就是离页守卫）；存净即摘。
// (c) number / int 草稿非法（负数 / 空 / 非整数）→ 「保存」禁用且该键不进 PUT（原生 numberField 解析失败写 NOTHING）；
//     只看用户改过的键——config.yaml 里既有的越界值不锁整区。
// (d) 带 `check` 的 string 字段（如 Gmail 地址）草稿不合格 → 同 (c)：「保存」禁用、不进 PUT（原生 saveAddress 先 validateAddress
//     再落盘）；那句 server-owned 的提示由 FieldControl 就地渲染；同样只看改过的键——config.yaml 里一个坏地址不锁住同区的开关。
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { refreshSettingsCatalog, saveSettingsSection, useAppState } from "../../store";
import type { SettingsSection } from "../../types";
import { pickText } from "./catalogText";
import { isGated, isValidNumberDraft, passesCheck, type Draft } from "./draftRules";
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

/** 草稿里非法的键（挡「保存」、不进 PUT）：number / int 草稿非法，或带 `check` 的 string 草稿不合形状；其它永不在列。
 *  只计**用户改过的**键（草稿 ≠ effective）：config.yaml 里既有的越界值（server `_coerce_number` 读文件不查 ≥0）或坏地址
 *  原样显示（提示句仍在）、不锁整区的「保存」——原生每格独立提交，别的开关照常能落；它本来就不脏、也不会进 PUT。 */
export function invalidKeys(section: SettingsSection, draft: Draft): string[] {
  return section.fields
    .filter((field) => {
      const value = draft[field.key];
      if (field.kind === "number" || field.kind === "int") return value !== field.effective && !isValidNumberDraft(field.kind, value);
      if (field.kind === "string" && field.check) {
        return String(value ?? "").trim() !== String(field.effective ?? "").trim() && !passesCheck(field, value);
      }
      return false;
    })
    .map((field) => field.key);
}

/** 目录刷新时的草稿合并：用户改过的键（草稿 ≠ 刷新前的 effective）留草稿，其余键跟新 effective。
 *  例外：改过的键在 server 侧**也动了**（新 effective ≠ 刷新前的 effective）→ 新 effective 赢——那是别处（勾选器）
 *  刚落盘的写，草稿再盖上去等于下次「保存」把它静默撤回（同键冲突以 server 为准，与首版「整份重置」同向）。 */
export function mergeDraft(previous: Draft | null, previousBase: Draft | null, fresh: Draft): Draft {
  if (!previous || !previousBase) return fresh;
  const merged: Draft = { ...fresh };
  for (const key of Object.keys(fresh)) {
    if (!(key in previous) || !(key in previousBase)) continue;
    const touched = previous[key] !== previousBase[key];
    const serverMoved = fresh[key] !== previousBase[key];
    if (touched && !serverMoved) merged[key] = previous[key];
  }
  return merged;
}

export function CatalogSection({ sectionId, titleOverride, only, lead, between, children }: CatalogSectionProps) {
  const { text, language } = useI18n();
  const { settingsCatalog, pageErrors } = useAppState();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [toast, setToast] = useToast();
  const base = useRef<Draft | null>(null); // 上一次对齐时的 effective（草稿形）——合并时据此判「用户改过没」

  useEffect(() => {
    if (!settingsCatalog) void refreshSettingsCatalog();
  }, [settingsCatalog]);

  const section = settingsCatalog?.sections.find((s) => s.id === sectionId) ?? null;
  const fingerprint = section ? JSON.stringify(section.fields.map((f) => [f.key, f.effective])) : "";

  // server 快照到了 / 同 section 别处写了 → 草稿合并（用户改过的键不丢；首帧 = 全取 effective）
  useEffect(() => {
    if (!section) return;
    const fresh = draftOf(section);
    const previousBase = base.current; // 先取值：updater 可能到下一帧才跑，那时 base 已经换新
    setDraft((previous) => mergeDraft(previous, previousBase, fresh));
    base.current = fresh;
  }, [fingerprint]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = section && only ? { ...section, fields: section.fields.filter((f) => only.includes(f.key)) } : section;
  const invalid = visible && draft ? invalidKeys(visible, draft) : [];
  const dirty = visible && draft ? changedKeys(visible, draft).filter((key) => !invalid.includes(key)) : [];
  const hasUnsaved = dirty.length > 0;

  // 离页守卫：有未保存改动才挂（浏览器只认 preventDefault + 非空 returnValue，文案由浏览器出）；存净 / 卸载即摘
  useEffect(() => {
    if (!hasUnsaved) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = text("有未保存的设置修改", "There are unsaved settings changes");
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [hasUnsaved, text]);

  const title = titleOverride ?? (section ? pickText(section.title, language) : sectionId);
  const error = pageErrors.settingsCatalog;

  if (!section || !visible) {
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

  const sectionKey = visible.id; // 早返回之后 visible 已非空；抓成常量让 save() 的闭包也知道

  async function save() {
    // 非法数字 / 不合形状的地址挡整次保存（不是只剔掉它——用户看着「保存」成功却发现那一格没落，比禁用更糟）
    if (!draft || dirty.length === 0 || invalid.length > 0) return;
    setSaving(true);
    setToast(null);
    const patch: Record<string, unknown> = {};
    for (const key of dirty) patch[key] = draft[key];
    try {
      const saved = await saveSettingsSection(sectionKey, patch);
      // 保存成功 → 草稿对齐回执（原生：commit 成功后显示值 = effective；输入框在保存期间禁用，没有并发编辑可丢）。
      // 回执缺 fields（不该发生）就只靠上面的合并 effect，不当保存失败
      if (Array.isArray(saved?.fields)) {
        const fresh = draftOf(saved);
        base.current = fresh;
        setDraft(fresh);
      }
      // 原生 noteSaved：「已保存 HH:mm:ss」（时刻单独节点）
      setToast({ kind: "ok", prefix: text("已保存 ", "Saved "), message: savedClock() });
    } catch (err) {
      // 原生 SettingsGmail / SettingsSlack / SettingsMaintainer 的 catch：「保存设置失败: 」+ 原句
      setToast({ kind: "error", prefix: text("保存设置失败: ", "Failed to save settings: "), message: errorMessage(err) });
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
            disabled={isGated(field.key, draft)}
            onChange={(key, value) => setDraft((d) => (d ? { ...d, [key]: value } : d))}
          />
          {between?.[field.key]}
        </Fragment>
      ))}
      <div className="settings-actions">
        <button type="button" className="btn btn-primary" disabled={dirty.length === 0 || invalid.length > 0 || isSaving} onClick={() => void save()}>
          {isSaving ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        {dirty.length > 0 && (
          <span className="settings-helper">{text(`${dirty.length} 项未保存`, `${dirty.length} unsaved`)}</span>
        )}
      </div>
      {children}
      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.prefix ? <span>{toast.prefix}</span> : null}<span>{toast.message}</span>
        </div>
      )}
    </section>
  );
}

/** 原生 noteSaved 的 HH:mm:ss（本地时刻） */
function savedClock(): string {
  const now = new Date();
  return [now.getHours(), now.getMinutes(), now.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}
