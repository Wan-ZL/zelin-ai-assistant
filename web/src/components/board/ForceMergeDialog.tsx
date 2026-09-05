// 强制合并确认弹窗（原生 ForceMergeSheet，§21bis）：列出选中卡、选主卡（默认第一张 / 建议卡的
// primary）、一句大白话说明「副卡停止运行、进入已合并（不可撤销），来源 / 交付物保留在主卡」，
// 确认才写 {action:"merge_force", ids, primary}（ids 保持选择顺序、去重，primary ∈ ids——server 复验）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { ModalDialog } from "./ModalDialog";

export interface ForceMergeDialogProps {
  ids: string[];
  /** 每张卡的展示标题（缺则显示 id） */
  titles: Record<string, string>;
  defaultPrimary?: string | null;
  onConfirm: (primary: string) => void;
  onCancel: () => void;
}

export function forceMergeBody(ids: string[], primary: string) {
  return { action: "merge_force", ids: [...new Set(ids)], primary };
}

export function ForceMergeDialog({ ids, titles, defaultPrimary, onConfirm, onCancel }: ForceMergeDialogProps) {
  const { text } = useI18n();
  const [primary, setPrimary] = useState<string>(defaultPrimary && ids.includes(defaultPrimary) ? defaultPrimary : ids[0]);

  return (
    <ModalDialog title={text(`强制合并 ${ids.length} 张卡片`, `Force-merge ${ids.length} cards`)} onCancel={onCancel}>
      <p className="dialog-body">
        {text(
          "选一张留下的主卡。其余副卡会停止运行、进入「已合并」（不可撤销）；它们的来源引文与交付物保留在主卡上。",
          "Pick the primary card to keep. The others stop running and become \"merged\" (irreversible); their sources and deliverables stay on the primary.",
        )}
      </p>
      <div className="dialog-radio-list" role="radiogroup" aria-label={text("主卡", "Primary card")}>
        {ids.map((id) => (
          <label key={id} className="dialog-radio">
            <input type="radio" name="force-merge-primary" value={id} checked={primary === id} onChange={() => setPrimary(id)} />
            <span className="dialog-radio-id">{id}</span>
            <span className="dialog-radio-title">{titles[id] ?? ""}</span>
            {/* 原生每行的角色按钮：主卡 · 保留 / 副卡 · 并入主卡（点副卡 = 把它设为主卡） */}
            <button type="button" className={`btn btn-quiet dialog-radio-role${primary === id ? " is-primary" : ""}`} onClick={() => setPrimary(id)}>
              {primary === id ? text("主卡 · 保留", "Primary · kept") : text("副卡 · 并入主卡", "Secondary · folds in")}
            </button>
          </label>
        ))}
      </div>
      {/* 键盘纪律（§41 2026-09-05 追记，D35 同款）：弹窗一律按钮提交，没有 Enter=确认——原生 ForceMergeSheet 的
          .defaultAction 是退役规则，不再挂 title="↩" 招牌；Esc 仍由 <dialog> 的 cancel 事件走 onCancel。 */}
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel} title="⎋">{text("取消", "Cancel")}</button>
        <button type="button" className="btn btn-danger" onClick={() => onConfirm(primary)}>
          {text("强制合并", "Force-merge")}
        </button>
      </div>
    </ModalDialog>
  );
}
