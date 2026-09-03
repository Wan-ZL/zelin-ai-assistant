// 多选操作条（原生 Kanban.swift「选择」态的底部 bar，§21 / §21bis / §68.12）：selectionMode 下
// 常驻底部：已选 N · 请求合并建议（merge_review，≥2）· 强制合并（merge_force + 主卡弹窗）·
// 批量批准 / 批量拒绝（只对提案列的卡；T2 卡跳过——typed-confirm 不能批量绕过，§0.8 / §50 W17）·
// 清空 · 退出。每个批量动作 = 逐卡一条 inbox 动作（§3 四键形，server 零容忍不接受批量形）。
// 入口「选择」按钮在 FilterBar；本条只在 selectionMode 渲染。
import { useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { clearSelection, markForceMerging, setSelectionMode, useAppState } from "../../store";
import type { ApprovalCard } from "../../types";
import { cardAction, describeActionError, effectiveTier } from "./boardActions";
import { FeedbackDialog } from "./FeedbackDialog";
import { ForceMergeDialog, forceMergeBody } from "./ForceMergeDialog";
import { titlesFor } from "./MergeSuggestionCard";
import { ModalDialog } from "./ModalDialog";

type Confirm = "none" | "force" | "approve" | "reject" | "feedback";

/** 批量批准 / 拒绝的资格：只有提案列真实卡（processing 占位不算）；T2（含 W17 生效 T2）批准跳过 */
export function batchable(ids: ReadonlySet<string>, proposals: ApprovalCard[], verb: "approve" | "reject"): { ok: string[]; skippedT2: string[] } {
  const ok: string[] = [];
  const skippedT2: string[] = [];
  for (const card of proposals) {
    if (!ids.has(card.id) || card.processing) continue;
    if (verb === "approve" && effectiveTier(card) === "T2") skippedT2.push(card.id);
    else ok.push(card.id);
  }
  return { ok, skippedT2 };
}

export function SelectionBar() {
  const { text } = useI18n();
  const { selectionMode, selectedIds, board } = useAppState();
  const [confirm, setConfirm] = useState<Confirm>("none");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!selectionMode) return null;

  const ids = [...selectedIds];
  const proposals = board?.needs_approval ?? [];
  const titles = titlesFor(ids, board as unknown as Record<string, unknown> | null);
  const approve = batchable(selectedIds, proposals, "approve");
  const reject = batchable(selectedIds, proposals, "reject");

  async function run(bodies: Array<Record<string, unknown>>, done: string) {
    setConfirm("none");
    if (bodies.length === 0) return;
    setBusy(true);
    setNote(null);
    let failed = 0;
    for (const body of bodies) {
      try {
        await postAction(body);
      } catch (e) {
        failed += 1;
        setNote(describeActionError(e, text));
      }
    }
    setBusy(false);
    if (failed === 0) {
      setNote(done);
      clearSelection();
    }
  }

  return (
    <div className="selection-bar" role="toolbar" aria-label={text("多选操作", "Selection actions")}>
      <span className="selection-count">{text(`已选 ${ids.length}`, `${ids.length} selected`)}</span>
      <button type="button" className="btn btn-primary" disabled={busy || ids.length < 2}
        onClick={() => void run([{ action: "merge_review", ids }], text("已请求合并建议，AI 分析中（提案列顶会出现建议卡）", "Merge review requested; the suggestion card appears atop Proposals"))}>
        {text(`请求合并建议 (${ids.length})`, `Suggest merge (${ids.length})`)}
      </button>
      <button type="button" className="btn btn-danger" disabled={busy || ids.length < 2} onClick={() => setConfirm("force")}>
        {text(`强制合并 (${ids.length})`, `Force-merge (${ids.length})`)}
      </button>
      <button type="button" className="btn btn-success" disabled={busy || approve.ok.length === 0} onClick={() => setConfirm("approve")}
        title={approve.skippedT2.length ? text(`T2 卡需单独输入确认：${approve.skippedT2.join(", ")}`, `T2 cards need their own typed confirm: ${approve.skippedT2.join(", ")}`) : undefined}>
        {text(`批量批准 (${approve.ok.length})`, `Approve (${approve.ok.length})`)}
      </button>
      <button type="button" className="btn btn-danger" disabled={busy || reject.ok.length === 0} onClick={() => setConfirm("reject")}>
        {text(`批量拒绝 (${reject.ok.length})`, `Reject (${reject.ok.length})`)}
      </button>
      {/* §29 targeted 提建议（原生多选条同位）：ids = 选中卡 */}
      <button type="button" className="btn" disabled={busy || ids.length === 0} onClick={() => setConfirm("feedback")}>
        {text(`提建议 (${ids.length})`, `Send feedback (${ids.length})`)}
      </button>
      <button type="button" className="btn" disabled={busy || ids.length === 0} onClick={() => clearSelection()}>{text("清空", "Clear")}</button>
      <button type="button" className="btn" disabled={busy} onClick={() => setSelectionMode(false)} title="⎋">{text("退出选择", "Done")}</button>
      {note && <span className="selection-note">{note}</span>}

      {confirm === "feedback" && (
        <FeedbackDialog ids={ids} onSubmit={(body) => void run([body], text("已记录建议，感谢", "Feedback recorded"))} onCancel={() => setConfirm("none")} />
      )}
      {confirm === "force" && (
        // 提交即给涉及的卡挂「合并中…」章（原生 mergeForcingBadge），下一版看板落地才退场
        <ForceMergeDialog ids={ids} titles={titles}
          onConfirm={(primary) => { markForceMerging(ids); void run([forceMergeBody(ids, primary)], text("已提交强制合并", "Force merge submitted")); }}
          onCancel={() => setConfirm("none")} />
      )}
      {(confirm === "approve" || confirm === "reject") && (
        <ModalDialog
          title={confirm === "approve" ? text(`批准 ${approve.ok.length} 张提案？`, `Approve ${approve.ok.length} proposals?`) : text(`拒绝 ${reject.ok.length} 张提案？`, `Reject ${reject.ok.length} proposals?`)}
          onCancel={() => setConfirm("none")}
        >
          <p className="dialog-body">
            {(confirm === "approve" ? approve.ok : reject.ok).map((id) => `${id} ${titles[id] ?? ""}`).join("\n")}
            {confirm === "approve" && approve.skippedT2.length > 0 && text(`\n\n跳过 T2（需单独输入确认词）：${approve.skippedT2.join(", ")}`, `\n\nSkipped T2 (each needs its own typed confirm): ${approve.skippedT2.join(", ")}`)}
            {confirm === "reject" && text("\n\n拒绝 = 不想做，进回收站（可恢复）。", "\n\nReject = won't do; goes to Trash (restorable).")}
          </p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => setConfirm("none")}>{text("取消", "Cancel")}</button>
            <button type="button" className={`btn ${confirm === "approve" ? "btn-success" : "btn-danger"}`}
              onClick={() => void run((confirm === "approve" ? approve.ok : reject.ok).map((id) => cardAction(id, confirm === "approve" ? "approve" : "reject")),
                confirm === "approve" ? text("已批量提交批准", "Approvals submitted") : text("已批量提交拒绝", "Rejections submitted"))}>
              {confirm === "approve" ? text("批准", "Approve") : text("拒绝", "Reject")}
            </button>
          </div>
        </ModalDialog>
      )}
    </div>
  );
}
