// 顶栏「提建议」（§29；原生 header 按钮的 web 版）：打开 FeedbackDialog，ids=[]（对整体）；
// 发出后不做乐观更新，只给一行回执（feedback 不进看板列，没有回流可等）。
import { useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { describeActionError } from "../board/boardActions";
import { FeedbackDialog } from "../board/FeedbackDialog";

export function FeedbackButton() {
  const { text } = useI18n();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const send = async (body: Record<string, unknown>) => {
    setOpen(false);
    try {
      await postAction(body);
      setNote(text("建议已记下，谢谢", "Feedback recorded — thank you"));
    } catch (e) {
      setNote(describeActionError(e, text));
    }
    window.setTimeout(() => setNote(null), 4000);
  };

  return (
    <>
      <button type="button" className="chrome-select-toggle" onClick={() => setOpen(true)} title={text("给维护者提建议", "Send feedback to the maintainer")}>
        {text("提建议", "Feedback")}
      </button>
      {note && <span className="chrome-feedback-note" role="status">{note}</span>}
      {open && <FeedbackDialog ids={[]} onSubmit={(body) => void send(body)} onCancel={() => setOpen(false)} />}
    </>
  );
}
