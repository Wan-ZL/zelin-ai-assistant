// 设置页 section「会议纪要」（CONTRACT §63，issue #129 拍板）。三把旋钮：
//   enabled（会后自动出稿）、default_language（auto/zh/en）、slack_draft_enabled（**默认关**——
//   开了以后 recap 作为草稿进你自己 Slack 的「Drafts & Sent」，发送键仍在人手里）。
// 数据经 store（refreshRecapSettings/saveRecapSettings）；这里只存草稿 + toast 瞬态。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { useI18n } from "../../i18n";
import { refreshRecapSettings, saveRecapSettings, useAppState } from "../../store";
import type { RecapSettings } from "../../types";

interface Draft {
  enabled: boolean;
  default_language: string;
  slack_draft_enabled: boolean;
}

const TOAST_MS = 6000;

function draftFrom(s: RecapSettings): Draft {
  return { enabled: s.enabled, default_language: s.default_language, slack_draft_enabled: s.slack_draft_enabled };
}

export function RecapSection() {
  const { text } = useI18n();
  const { recapSettings, settingsError } = useAppState();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  useEffect(() => {
    void refreshRecapSettings();
  }, []);

  useEffect(() => {
    if (recapSettings) setDraft(draftFrom(recapSettings));
  }, [recapSettings]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  const title = text("会议纪要", "Meeting recaps");
  if (!recapSettings || !draft) {
    return (
      <section className="settings-section">
        <h3 className="settings-section-title">{title}</h3>
        <p className="settings-helper">{settingsError && !recapSettings ? settingsError : text("读取中…", "Loading…")}</p>
      </section>
    );
  }

  const isDirty = draft.enabled !== recapSettings.enabled
    || draft.default_language !== recapSettings.default_language
    || draft.slack_draft_enabled !== recapSettings.slack_draft_enabled;

  async function save() {
    if (!draft) return;
    setSaving(true);
    setToast(null);
    try {
      await saveRecapSettings(draft);
      setToast({ kind: "ok", message: text("已保存，下一轮 cron 生效。", "Saved — applies on the next cron round.") });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setSaving(false);
    }
  }

  const languageLabel = (lang: string) =>
    lang === "zh" ? text("中文", "Chinese") : lang === "en" ? "English" : text("跟随界面语言", "Follow UI language");

  return (
    <section className="settings-section" aria-labelledby="settings-recap-title">
      <h3 id="settings-recap-title" className="settings-section-title">{title}</h3>
      <p className="settings-helper">
        {text(
          "会议（Zoom / Teams / Meet / Webex / FaceTime / Slack Huddle，≥10 分钟）结束后 5–35 分钟自动出一份 5 行纯文本纪要，你只做一件事：复制粘贴。默认没有任何发送路径。",
          "5–35 minutes after a meeting (Zoom / Teams / Meet / Webex / FaceTime / Slack Huddle, ≥10 min) a 5-line plain-text recap appears; you copy and paste. There is no send path by default.",
        )}
      </p>

      <label className="settings-check">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
        />
        <span>{text("会后自动生成纪要", "Generate a recap after each meeting")}</span>
      </label>

      <div className="settings-knob">
        <label className="settings-knob-label" htmlFor="recap-language">{text("默认语言", "Default language")}</label>
        <div className="settings-knob-controls">
          <select
            id="recap-language"
            className="settings-select"
            value={draft.default_language}
            onChange={(event) => setDraft({ ...draft, default_language: event.target.value })}
          >
            {recapSettings.languages.map((lang) => (
              <option key={lang} value={lang}>{languageLabel(lang)}</option>
            ))}
          </select>
        </div>
        <p className="settings-helper">{text("中英两版每次同时产出，这里只决定详情页先显示哪一版。", "Both languages are always produced; this only picks which one the page shows first.")}</p>
      </div>

      <label className="settings-check">
        <input
          type="checkbox"
          checked={draft.slack_draft_enabled}
          onChange={(event) => setDraft({ ...draft, slack_draft_enabled: event.target.checked })}
        />
        <span>{text("把纪要放进 Slack 草稿箱（默认关）", "Place the recap in my Slack drafts (default off)")}</span>
      </label>
      <p className="settings-helper">
        {text(
          "开了以后，纪要以草稿形式出现在你自己 Slack 的「Drafts & Sent」里、附着到对应会话——不会发送，按发送键的仍然是你。目标会话来自 config.yaml recap.slack_draft.targets 或详情页手选；两者都没有就不投。走 Slack MCP 白名单（只允许建草稿），不是 prompt 约束。",
          "When on, the recap appears as a draft in your own Slack Drafts & Sent, attached to the conversation — never sent; you press send. Targets come from config.yaml recap.slack_draft.targets or a manual pick on the detail page; neither → no draft. Enforced by a Slack MCP tool allowlist (draft only), not by a prompt.",
        )}
      </p>

      <div className="settings-actions">
        <button type="button" className="btn btn-primary" disabled={!isDirty || isSaving} onClick={() => void save()}>
          {isSaving ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
      </div>

      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}
