// 提建议弹窗（§29 feedback；原生 AppDelegate.promptFeedback 的 web 版）：文本 + 「同时公开到 GitHub
// 建议跟踪表」勾选（出厂不勾——公开是逐条 opt-in，§29bis）→ {action:"feedback", text, publish, ids}
// （ids = 多选时的目标卡，升序；全局提建议 = []）。
// 勾选的默认态 = 上次选择，住 settings_overrides 的 `feedback_publish_default`（原生
// rememberFeedbackPublishDefault 同一把键，§66.2 setting:overrides:*；server 目录 general 区投影它，
// 写 = PUT /api/settings/general）——不再有第二份 localStorage 副本。记不住只影响下次默认态，不挡发送
// （原生 try? 先例：写失败静默）。
// 粘贴图片的 images[] 走 §68.14 的诚实例外（上传通道另 PR），本弹窗只发文字。
// 键盘纪律（§41 2026-09-05 追记，D35 同款）：弹窗一律按钮提交——Enter 在 textarea 里就是换行（不拦、不
// preventDefault），键盘上没有提交键；IME 候选上屏的回车因此天然安全（半截拼音再也不会被发出去、还公开成 issue）。
// 正文 = §29 的明示条款（原生 AppDelegate.promptFeedback 同款）：建议全文 + 所选卡片标题快照会上传给维护者，
// 不受匿名统计开关限制——文案不得暗示是本地闭环。
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
        {text(
          "说说哪里不对 / 可以更好。发送后，建议全文与所选卡片的标题快照会上传给维护者用于改进产品（即使你关闭了匿名统计）——请勿包含敏感信息。勾选公开时还会出现在公开 GitHub 仓库的 issue 列表里。",
          "What's off / could be better. On send, your feedback text and the selected cards' title snapshots are uploaded to the maintainer to improve the product (even with anonymous stats off) — avoid sensitive details. With publish checked it also appears in the public GitHub repository's issue list.",
        )}
      </p>
      <textarea rows={4} value={draft} autoFocus placeholder={text("建议内容…", "Your feedback…")} onChange={(e) => setDraft(e.target.value)} />
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
