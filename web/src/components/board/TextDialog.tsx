// 文本输入弹窗：修改方向（comment）/ 打回反馈（rework）/ 回答需输入（answer_input）共用。
//   - allowEmpty=false：空文本提交按钮禁用（comment / answer_input 必须有内容）；
//   - allowEmpty=true：留空可提交，由调用方替换成固定字面量（rework 空反馈自查指令）；
//   - maxCodePoints：按 code points 截断（answer_input 4000，§39.2；与 actd 复验同单位）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { clipCodePoints } from "./boardActions";
import { ModalDialog } from "./ModalDialog";

interface TextDialogProps {
  title: string;
  body?: string;
  placeholder: string;
  submitLabel: string;
  allowEmpty?: boolean;
  maxCodePoints?: number;
  onSubmit: (text: string) => void;
  onCancel: () => void;
}

export function TextDialog({
  title,
  body,
  placeholder,
  submitLabel,
  allowEmpty = false,
  maxCodePoints,
  onSubmit,
  onCancel,
}: TextDialogProps) {
  const { text } = useI18n();
  const [draft, setDraft] = useState("");
  const trimmed = draft.trim();
  const canSubmit = allowEmpty || trimmed.length > 0;

  return (
    <ModalDialog title={title} onCancel={onCancel}>
      {body && <p className="dialog-body">{body}</p>}
      <textarea
        rows={4}
        value={draft}
        placeholder={placeholder}
        autoFocus
        onChange={(e) => {
          const next = e.target.value;
          setDraft(maxCodePoints ? clipCodePoints(next, maxCodePoints) : next);
        }}
      />
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel}>
          {text("取消", "Cancel")}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!canSubmit}
          onClick={() => onSubmit(trimmed)}
        >
          {submitLabel}
        </button>
      </div>
    </ModalDialog>
  );
}
