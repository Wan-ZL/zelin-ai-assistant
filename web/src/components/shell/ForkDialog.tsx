// 两选 fork 弹窗（§41 网页停止/拒绝 fork 与 Mac 同款语义）：
//   - 提案卡「拒绝」fork：不想做（进回收站，reject）/ 已办完（记为已交付，done_external）；
//   - 运行卡「停止」fork：退回提案（abort_execution，destructive）/ 去待验收（stop_to_review）。
// 弹窗本身即二次确认；正文由调用方给（卡片摘要 / 分叉解释）。取消永远在场。
// body 收 string（一段 <p class="dialog-body">）或已排好版的 ReactNode（原生 informativeText 带项目符号的多段正文——
// 关于页「卸载 Zelin's AI Assistant？」§68.6——调用方自己给 .dialog-body 容器）。
import type { ReactNode } from "react";
import { useI18n } from "../../i18n";
import { ModalDialog } from "./ModalDialog";

interface ForkChoice {
  label: string;
  isDanger?: boolean;
  onPick: () => void;
}

interface ForkDialogProps {
  title: string;
  body: ReactNode;
  choices: ForkChoice[];
  onCancel: () => void;
}

export function ForkDialog({ title, body, choices, onCancel }: ForkDialogProps) {
  const { text } = useI18n();
  return (
    <ModalDialog title={title} onCancel={onCancel}>
      {typeof body === "string" ? <p className="dialog-body">{body}</p> : body}
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel}>
          {text("取消", "Cancel")}
        </button>
        {choices.map((choice) => (
          <button
            key={choice.label}
            type="button"
            className={choice.isDanger ? "btn btn-danger" : "btn btn-primary"}
            onClick={choice.onPick}
          >
            {choice.label}
          </button>
        ))}
      </div>
    </ModalDialog>
  );
}
