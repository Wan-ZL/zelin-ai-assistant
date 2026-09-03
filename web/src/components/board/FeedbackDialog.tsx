// 提建议弹窗（§29 feedback；原生 AppDelegate.promptFeedback 的 web 版）：文本 + 「同时公开到 GitHub
// 建议跟踪表」勾选（出厂不勾——公开是逐条 opt-in，§29bis）→ {action:"feedback", text, publish, ids}
// （ids = 多选时的目标卡，升序；全局提建议 = []）。
// 勾选的默认态 = 上次选择，住 settings_overrides 的 `feedback_publish_default`（原生
// rememberFeedbackPublishDefault 同一把键，§66.2 setting:overrides:*；server 目录 general 区投影它，
// 写 = PUT /api/settings/general）——不再有第二份 localStorage 副本。记不住只影响下次默认态，不挡发送
// （原生 try? 先例：写失败静默）。
// 粘贴图片的 images[] 走 §68.14 的诚实例外（上传通道另 PR），本弹窗只发文字。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { refreshSettingsCatalog, saveSettingsSection, useAppState } from "../../store";
import type { SettingsCatalog } from "../../types";
import { ModalDialog } from "./ModalDialog";

export const PUBLISH_DEFAULT_KEY = "feedback_publish_default";
const PUBLISH_DEFAULT_SECTION = "general";

/** 目录里 general.feedback_publish_default 的生效值（缺席 / 旧 server 没这把键 → false） */
export function readPublishDefault(catalog: SettingsCatalog | null): boolean {
  const section = catalog?.sections.find((s) => s.id === PUBLISH_DEFAULT_SECTION);
  const field = section?.fields.find((f) => f.key === PUBLISH_DEFAULT_KEY);
  return field?.effective === true;
}

/** 记住勾选（best-effort：server 校验失败 / 离线只影响下次默认态） */
export function writePublishDefault(on: boolean): void {
  void saveSettingsSection(PUBLISH_DEFAULT_SECTION, { [PUBLISH_DEFAULT_KEY]: on }).catch(() => undefined);
}

/** §29 wire：ids 升序（server 也排一遍）、publish 恒在、text 非空 */
export function feedbackBody(text: string, publish: boolean, ids: string[]) {
  return { action: "feedback", text, publish, ids: [...new Set(ids)].sort() };
}

export interface FeedbackDialogProps {
  ids: string[];
  onSubmit: (body: Record<string, unknown>) => void;
  onCancel: () => void;
}

export function FeedbackDialog({ ids, onSubmit, onCancel }: FeedbackDialogProps) {
  const { text } = useI18n();
  const { settingsCatalog } = useAppState();
  const [draft, setDraft] = useState("");
  const [publish, setPublish] = useState(() => readPublishDefault(settingsCatalog));
  const [touched, setTouched] = useState(false);
  const trimmed = draft.trim();

  // 看板页通常还没拉过设置目录：开弹窗时拉一次，落地后（用户还没碰勾选）把默认态换成上次选择
  useEffect(() => {
    if (!settingsCatalog) void refreshSettingsCatalog();
  }, [settingsCatalog]);
  useEffect(() => {
    if (!touched) setPublish(readPublishDefault(settingsCatalog));
  }, [settingsCatalog, touched]);

  return (
    <ModalDialog title={ids.length ? text(`💡 提建议（${ids.length} 张卡）`, `💡 Send feedback (${ids.length} cards)`) : text("💡 提建议（对整体）", "💡 Send feedback (overall)")} onCancel={onCancel}>
      <p className="dialog-body">
        {text("哪里不对、想要什么——写给这个软件的维护者。本地先落 state/feedback/，勾选公开才会同步成 GitHub issue。", "What is wrong or what you want — goes to this software's maintainer. Stored locally under state/feedback/ first; only the publish checkbox syncs it to a GitHub issue.")}
      </p>
      <textarea rows={4} value={draft} autoFocus placeholder={text("建议内容…", "Your feedback…")} onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && trimmed) { e.preventDefault(); onSubmit(feedbackBody(trimmed, publish, ids)); } }} />
      <p className="dialog-note">{text("↩ 发送 · ⇧↩ 换行", "↩ send · ⇧↩ newline")}</p>
      <label className="dialog-check">
        <input type="checkbox" checked={publish} onChange={(e) => { setTouched(true); setPublish(e.target.checked); writePublishDefault(e.target.checked); }} />
        {text("同时公开到 GitHub 建议跟踪表（去掉卡片内容，只带你写的这段）", "Also publish to the GitHub feedback tracker (your text only, no card content)")}
      </label>
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel}>{text("取消", "Cancel")}</button>
        <button type="button" className="btn btn-primary" disabled={!trimmed} onClick={() => onSubmit(feedbackBody(trimmed, publish, ids))}>
          {text("发送", "Send")}
        </button>
      </div>
    </ModalDialog>
  );
}
