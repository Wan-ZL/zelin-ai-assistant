// 提建议弹窗（§29 feedback；原生 AppDelegate.promptFeedback 的 web 版）：文本 + 「同时公开到 GitHub
// 建议跟踪表」勾选（出厂不勾——公开是逐条 opt-in，§29bis；本地记住上次选择 localStorage `zai.feedbackPublish`）
// → {action:"feedback", text, publish, ids}（ids = 多选时的目标卡，升序；全局提建议 = []）。
// 粘贴图片的 images[] 走 §65.14 的诚实例外（上传通道另 PR），本弹窗只发文字。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { ModalDialog } from "./ModalDialog";

const PUBLISH_KEY = "zai.feedbackPublish";

export function readPublishDefault(): boolean {
  try {
    return window.localStorage.getItem(PUBLISH_KEY) === "1";
  } catch {
    return false;
  }
}

export function writePublishDefault(on: boolean): void {
  try {
    window.localStorage.setItem(PUBLISH_KEY, on ? "1" : "0");
  } catch {
    /* 隐私模式：不持久化 */
  }
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
  const [draft, setDraft] = useState("");
  const [publish, setPublish] = useState(readPublishDefault());
  const trimmed = draft.trim();

  return (
    <ModalDialog title={ids.length ? text(`💡 提建议（${ids.length} 张卡）`, `💡 Send feedback (${ids.length} cards)`) : text("💡 提建议（对整体）", "💡 Send feedback (overall)")} onCancel={onCancel}>
      <p className="dialog-body">
        {text("哪里不对、想要什么——写给这个软件的维护者。本地先落 state/feedback/，勾选公开才会同步成 GitHub issue。", "What is wrong or what you want — goes to this software's maintainer. Stored locally under state/feedback/ first; only the publish checkbox syncs it to a GitHub issue.")}
      </p>
      <textarea rows={4} value={draft} autoFocus placeholder={text("建议内容…", "Your feedback…")} onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && trimmed) { e.preventDefault(); onSubmit(feedbackBody(trimmed, publish, ids)); } }} />
      <p className="dialog-note">{text("↩ 发送 · ⇧↩ 换行", "↩ send · ⇧↩ newline")}</p>
      <label className="dialog-check">
        <input type="checkbox" checked={publish} onChange={(e) => { setPublish(e.target.checked); writePublishDefault(e.target.checked); }} />
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
