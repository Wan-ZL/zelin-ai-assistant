// 合并建议卡（§21 / §21ter；原生 Cards.swift MergeSuggestionCardView，紫 accent，提案列顶）：
//   analyzing = spinner + 涉及卡；done = 结论（verdict 词表 → 人话）+ 主/副卡 + rationale +
//   「接受后将执行」动作清单全文 + confidence 章 + 「接受」(merge_apply) / 「取消」(merge_dismiss)；
//   partition 多一段分组清单（主按钮「按分组合并（k 组）」仍是 merge_apply——方案存作业文件）；
//   failed = 橙色 + error + 仅「取消」。verdict ≠ merge 或 failed 时给「仍然合并」覆盖（ForceMergeDialog →
//   merge_force），成功后顺手 merge_dismiss 掉这条被取代的建议（原生同）。
// 无乐观更新：动作发出 → 回流（useSubmit 180s 兜底）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import type { MergeSuggestion } from "../../types";
import { cardAction, useSubmit } from "./boardActions";
import { RelativeTime } from "./cardChrome";
import { ForceMergeDialog, forceMergeBody } from "./ForceMergeDialog";

type Text = (zh: string, en: string) => string;

/** verdict 词表 → 人话（未知原样） */
export function verdictLabel(verdict: string | null | undefined, text: Text): string {
  switch (verdict) {
    case "merge": return text("建议合并", "Merge");
    case "link_improvement": return text("挂为改进卡", "Link as improvement");
    case "keep_separate": return text("保持独立", "Keep separate");
    case "close_secondary": return text("关闭副卡", "Close secondary");
    case "partition": return text("按分组合并", "Merge by groups");
    default: return verdict ?? "";
  }
}

export function confidenceChip(confidence: string | null | undefined): string {
  return confidence === "high" ? "chip chip-success" : confidence === "low" ? "chip chip-warning" : "chip";
}

/** 看板里按 id 找展示标题（提案 / 运行 / 待验收三列） */
export function titlesFor(ids: string[], board: Record<string, unknown> | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!board) return out;
  const rows = (["needs_approval", "running", "needs_input", "review", "completed"] as const)
    .flatMap((k) => (Array.isArray(board[k]) ? (board[k] as Array<Record<string, unknown>>) : []));
  for (const id of ids) {
    const row = rows.find((r) => r.id === id);
    const title = row && ((typeof row.display_title === "string" && row.display_title) || (typeof row.title === "string" && row.title) || (typeof row.name === "string" && row.name));
    if (title) out[id] = title as string;
  }
  return out;
}

export function MergeSuggestionCard({ suggestion }: { suggestion: MergeSuggestion }) {
  const { text } = useI18n();
  const { board } = useAppState();
  const { pending, error, submit } = useSubmit();
  const [forcing, setForcing] = useState(false);
  const titles = titlesFor(suggestion.ids, board as unknown as Record<string, unknown> | null);

  const isDone = suggestion.status === "done";
  const isFailed = suggestion.status === "failed";
  const canOverride = isFailed || (isDone && suggestion.verdict !== "merge" && suggestion.verdict !== "partition");

  const apply = () => void submit(cardAction(suggestion.id, "merge_apply"));
  const dismiss = () => void submit(cardAction(suggestion.id, "merge_dismiss"));
  const force = async (primary: string) => {
    setForcing(false);
    const ok = await submit(forceMergeBody(suggestion.ids, primary));
    if (ok) void submit(cardAction(suggestion.id, "merge_dismiss"));
  };

  return (
    <article className={`task-card merge-card is-${suggestion.status}`} data-suggestion={suggestion.id}>
      <div className="card-head">
        <span className="card-dot is-merge" aria-hidden="true" />
        <div className="card-title">{text("合并建议", "Merge suggestion")}</div>
        <span className="card-id">{suggestion.id}</span>
      </div>
      <div className="card-badges">
        {suggestion.ids.map((id) => <span key={id} className={`chip${id === suggestion.primary ? " chip-purple" : ""}`} title={titles[id]}>{id}</span>)}
        <RelativeTime epoch={suggestion.requested_at} />
      </div>

      {suggestion.status === "analyzing" && (
        <div className="task-processing-row is-running">
          <span className="task-processing-ring" aria-hidden="true"><span /></span>
          <span className="task-processing-label">{text("AI 正在分析这批卡该如何归并…", "AI is analyzing how these cards should be merged…")}</span>
        </div>
      )}

      {isDone && (
        <>
          <div className="card-badges">
            <span className="chip chip-purple">{verdictLabel(suggestion.verdict, text)}</span>
            {suggestion.primary && <span className="card-meta-text">{text("主卡 ", "primary ")}{suggestion.primary}</span>}
            {suggestion.confidence && <span className={confidenceChip(suggestion.confidence)}>{suggestion.confidence}</span>}
          </div>
          {suggestion.rationale && <p className="card-line is-body">{suggestion.rationale}</p>}
          {suggestion.groups && suggestion.groups.length > 0 && (
            <ul className="merge-groups">
              {suggestion.groups.map((g, i) => (
                <li key={i}><strong>{g.primary}</strong> ← {g.ids.filter((x) => x !== g.primary).join(", ")}{g.reason ? ` · ${g.reason}` : ""}</li>
              ))}
              {suggestion.ids.filter((id) => !suggestion.groups!.some((g) => g.ids.includes(id))).map((id) => (
                <li key={id} className="card-meta-text">{id} · {text("保持独立", "keeps separate")}</li>
              ))}
            </ul>
          )}
          {suggestion.action_plan && suggestion.action_plan.length > 0 && (
            <div className="card-details">
              <p className="card-detail-heading">{text("接受后将执行：", "On accept:")}</p>
              <ol className="card-detail-list">{suggestion.action_plan.map((step, i) => <li key={i}>{step}</li>)}</ol>
            </div>
          )}
        </>
      )}

      {isFailed && <p className="card-line is-warning is-body">{text("分析失败：", "Analysis failed: ")}{suggestion.error ?? ""}</p>}

      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
      ) : (
        <div className="card-actions">
          {isDone && (
            <button type="button" className="btn btn-success" onClick={apply}>
              {suggestion.verdict === "partition"
                ? text(`按分组合并（${suggestion.groups?.length ?? 0} 组）`, `Merge by groups (${suggestion.groups?.length ?? 0})`)
                : text("接受", "Accept")}
            </button>
          )}
          {canOverride && (
            <button type="button" className="btn btn-danger" onClick={() => setForcing(true)}>{text("仍然合并…", "Merge anyway…")}</button>
          )}
          {suggestion.status !== "analyzing" && (
            <button type="button" className="btn" onClick={dismiss}>{text("取消", "Dismiss")}</button>
          )}
        </div>
      )}
      {error && <p className="card-error">{error}</p>}
      {forcing && (
        <ForceMergeDialog ids={suggestion.ids} titles={titles} defaultPrimary={suggestion.primary} onConfirm={(p) => void force(p)} onCancel={() => setForcing(false)} />
      )}
    </article>
  );
}
