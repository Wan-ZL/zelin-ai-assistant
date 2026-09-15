// 多选操作条（原生 Kanban.swift「选择」态的底部 bar，§21 / §21bis / §68.12）：selectionMode 下
// 常驻底部：已选 N · 请求合并建议（merge_review，≥2）· 强制合并（merge_force + 主卡弹窗）·
// 批量批准 / 批量拒绝（只对提案列的卡；T2 卡跳过——typed-confirm 不能批量绕过，§0.8 / §50 W17）·
// 批量验收 / 批量打回 / 批量丢弃（只对待验收列的卡，§21 追记 / D74；三者都先过逐条列清单的
// 确认弹窗——打回那颗的弹窗就是反馈输入框，同一句反馈送回每一张，留空 = 各自按验收标准自查）·
// 清空 · 退出。每个批量动作 = 逐卡一条 inbox 动作（§3 四键形，server 零容忍不接受批量形）。
// 入口「选择」按钮在 FilterBar，或待验收列头的两颗「选中全部…」（ReviewLaneTools，D74）；
// 本条只在 selectionMode 渲染。
import { useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { clearSelection, markForceMerging, setSelectionMode, useAppState } from "../../store";
import type { ApprovalCard, ReviewCard } from "../../types";
import { cardAction, describeActionError, effectiveTier, REWORK_EMPTY_FALLBACK } from "./boardActions";
import { FeedbackDialog } from "./FeedbackDialog";
import { ForceMergeDialog, forceMergeBody } from "./ForceMergeDialog";
import { titlesFor } from "./MergeSuggestionCard";
import { ModalDialog } from "./ModalDialog";
import { TextDialog } from "./TextDialog";

type Confirm = "none" | "force" | "approve" | "reject" | "feedback" | "accept" | "rework" | "discard";

/** 待验收列的批量动词（§21 追记 / D74）——三个都是卡面上早就有的动词，零新 inbox 动词 */
type ReviewVerb = "accept" | "rework" | "trash";

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

/** 批量验收 / 批量打回 / 批量丢弃的资格：只有待验收列真实卡（§21 追记 / D74）。
 *  与 batchable() 同形：选中集里不在这一列的 id 一律不算，绝不替人猜别的列该怎么处置。
 *  没有 T2 那样的跳过——三个都不是审批闸，`review→delivered` / `review→executing` /
 *  `review→trashed` 三条转移在 store2 白名单里早已对 user 放行（act/lib/store2/schema.sql）。 */
export function reviewBatchable(ids: ReadonlySet<string>, review: ReviewCard[]): string[] {
  return review.filter((row) => ids.has(row.id)).map((row) => row.id);
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
  // §21 追记 / D74：待验收列的三颗批量键（同一份选中集，各自只认自己那一列的卡）
  const reviewIds = reviewBatchable(selectedIds, board?.review ?? []);
  const reviewVerb: ReviewVerb = confirm === "discard" ? "trash" : "accept";
  // 三颗键的确认弹窗都逐条列出 `<id> <标题>`——批量动作不许让人凭数字下决心
  const reviewListing = reviewIds.map((id) => `${id} ${titles[id] ?? ""}`).join("\n");

  /** 逐条 POST；全部成功才回执 + 清选择。返回「全部成功」——调用方据此决定要不要挂本地章 */
  async function run(bodies: Array<Record<string, unknown>>, done: string): Promise<boolean> {
    setConfirm("none");
    if (bodies.length === 0) return false;
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
    return failed === 0;
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
      {/* §21 追记 / D74：待验收列的批量验收 / 批量打回 / 批量丢弃——三个弹窗都逐条列 id + 标题。
          色相与卡面三动词一比一：绿验收 · 橙打回 · 红丢弃 */}
      <button type="button" className="btn btn-success" disabled={busy || reviewIds.length === 0} onClick={() => setConfirm("accept")}>
        {text(`批量验收 (${reviewIds.length})`, `Accept (${reviewIds.length})`)}
      </button>
      <button type="button" className="btn btn-warning" disabled={busy || reviewIds.length === 0} onClick={() => setConfirm("rework")}>
        {text(`批量打回 (${reviewIds.length})`, `Send back (${reviewIds.length})`)}
      </button>
      <button type="button" className="btn btn-danger" disabled={busy || reviewIds.length === 0} onClick={() => setConfirm("discard")}>
        {text(`批量丢弃 (${reviewIds.length})`, `Discard (${reviewIds.length})`)}
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
        // POST 成功才给涉及的卡挂「合并中…」章（原生 submitMergeForce：`guard writeInboxFile` 才 beginMergeForce）——
        // server 拒了就没有在途批次，否则章挂 180 s 再冒出「检查 actd」是两句谎话；副卡全部离开所在列（真信号）才退场
        <ForceMergeDialog ids={ids} titles={titles}
          onConfirm={(primary) => {
            void run([forceMergeBody(ids, primary)], text("已提交强制合并", "Force merge submitted")).then((ok) => {
              if (ok) markForceMerging(ids, primary);
            });
          }}
          onCancel={() => setConfirm("none")} />
      )}
      {(confirm === "accept" || confirm === "discard") && (
        <ModalDialog
          title={confirm === "accept"
            ? text(`验收 ${reviewIds.length} 张交付？`, `Accept ${reviewIds.length} deliveries?`)
            : text(`丢弃 ${reviewIds.length} 张交付？`, `Discard ${reviewIds.length} deliveries?`)}
          onCancel={() => setConfirm("none")}
        >
          <p className="dialog-body">
            {reviewListing}
            {confirm === "accept"
              ? text("\n\n验收 = 这几张的交付你认了，进「阶段性完成」。AI 的「建议验收」只是建议——上面这份清单是你自己的那一次确认。",
                "\n\nAccept = you take these deliveries; they move to Done for now. The AI's “Looks done” is only advice — the list above is your own confirmation.")
              : text("\n\n丢弃 = 这几张不要了，进回收站（可恢复）。", "\n\nDiscard = you don't want these; they go to Trash (restorable).")}
          </p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => setConfirm("none")}>{text("取消", "Cancel")}</button>
            <button type="button" className={`btn ${confirm === "accept" ? "btn-success" : "btn-danger"}`}
              onClick={() => void run(reviewIds.map((id) => cardAction(id, reviewVerb)),
                confirm === "accept" ? text("已批量提交验收", "Acceptances submitted") : text("已批量提交丢弃", "Discards submitted"))}>
              {confirm === "accept" ? text("验收", "Accept") : text("丢弃", "Discard")}
            </button>
          </div>
        </ModalDialog>
      )}
      {confirm === "rework" && (
        // 打回的弹窗就是反馈输入框（同卡面 ReviewCard 的 TextDialog）：同一句反馈逐卡送回各自的会话，
        // 留空 = 每张各自对照自己的 definition_of_done 自查（REWORK_EMPTY_FALLBACK，客户端字面量）。
        // 正文先逐条列出这批卡——一句反馈要送给 N 份不同的草稿，至少得让人看着名单决定。
        <TextDialog
          title={text(`↩︎ 批量打回 ${reviewIds.length} 张交付？`, `↩︎ Send back ${reviewIds.length} deliveries?`)}
          body={text(
            `${reviewListing}\n\n同一句反馈会送回上面每一张的会话继续改。留空 = 每张各自对照自己的验收标准自查改进。`,
            `${reviewListing}\n\nThe same feedback goes back to every session above. Leave empty = each card self-reviews against its own DoD.`)}
          placeholder={text("改哪里…", "What to change…")}
          submitLabel={text("打回", "Send Back")}
          allowEmpty
          onSubmit={(t) => void run(reviewIds.map((id) => cardAction(id, "rework", t || REWORK_EMPTY_FALLBACK)),
            text("已批量提交打回", "Send-backs submitted"))}
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
