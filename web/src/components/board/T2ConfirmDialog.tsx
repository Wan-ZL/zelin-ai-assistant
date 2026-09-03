// T2 typed-confirm 弹窗——纯客户端闸门，wire 与 T0/T1 完全相同（inbox-actions.md §2）。
// 语义逐项镜像 Mac AppDelegate.confirmT2（live CONTRACT §7/§40/§41）：
//   - 标题「T2 · 高影响操作确认」；正文点名卡片 id/摘要 + 金额行（或「成本未知」）；
//   - 输入 trim + lowercase 后必须恰为「确认」或「go」；
//   - 不匹配不静默失败：提示「上次输入不匹配。」并留在弹窗里重试；取消是唯一 false 出口。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { ModalDialog } from "./ModalDialog";

interface T2ConfirmDialogProps {
  cardId: string;
  summary: string;
  /** 金额行（boardActions.costLine 推导：预计费用 $N / 成本未知） */
  costLine: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function T2ConfirmDialog({ cardId, summary, costLine, onConfirm, onCancel }: T2ConfirmDialogProps) {
  const { text } = useI18n();
  const [typed, setTyped] = useState("");
  const [mismatched, setMismatched] = useState(false);

  const approve = () => {
    const normalized = typed.trim().toLowerCase();
    if (normalized === "确认" || normalized === "go") {
      onConfirm();
    } else {
      setMismatched(true);
      setTyped("");
    }
  };

  return (
    <ModalDialog title={text("T2 · 高影响操作确认", "T2 · High-Impact Action Confirmation")} onCancel={onCancel}>
      {/* 原生 informativeText 的三行（批准 id：摘要 / 金额行 / 提示）——各自一个节点，金额行「预计费用：$N」/「成本未知」逐字 */}
      <p className="dialog-body">
        <span>{text(`批准 ${cardId}：${summary}`, `Approve ${cardId}: ${summary}`)}</span>
        {"\n"}
        <span className="dialog-cost-line">{costLine}</span>
        {"\n\n"}
        <span>{text("请输入 确认 或 go 后再点「批准」。", "Type 确认 or go, then click \"Approve\".")}</span>
      </p>
      <input
        type="text"
        value={typed}
        placeholder={text("输入 确认 或 go", "Type 确认 or go")}
        autoFocus
        onChange={(e) => setTyped(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing) {
            e.preventDefault();
            approve();
          }
        }}
      />
      {mismatched && <p className="dialog-note">{text("上次输入不匹配。", "Previous input didn't match.")}</p>}
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel}>
          {text("取消", "Cancel")}
        </button>
        <button type="button" className="btn btn-primary" onClick={approve}>
          {text("批准", "Approve")}
        </button>
      </div>
    </ModalDialog>
  );
}
