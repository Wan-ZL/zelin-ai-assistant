// 提案卡（needs_approval 分区项，含 raising 占位）。四颗决策动词镜像 Mac v0.21 拍板：
//   批准（T2 走 typed-confirm 弹窗，wire 不变）· 拒绝（fork：不想做 reject / 已办完
//   done_external，§41）· 修改（comment 文本弹窗）· 暂缓（defer，提案→潜在任务）。
// processing=true 的灰卡是 AI 研究中占位——只展示 sheen，不给决策按钮。
// 卡面（原生 ApprovalCardView.normalBody 收起态）：摘要 + 落点行（§7 target_kind）+ 章行
//   + 分歧 + 回锅注；「展开详情 ▸」后：技术标题 / 💰 费用 / 💬 需求来自 / 📋 要做什么 /
//   怎样算办完。id 在右上角（原生 idTag）。
import { useState } from "react";
import { displayId } from "../../cardId";
import { useI18n } from "../../i18n";
import type { ApprovalCard } from "../../types";
import { cardAction, costLine, effectiveTier, useSubmit } from "./boardActions";
import { CardDetails, CardHead, CardSurface, DetailsToggle } from "./cardChrome";
import { DodList, PlanList, SourceList } from "./detailBlocks";
import { ForkDialog } from "./ForkDialog";
import { T2ConfirmDialog } from "./T2ConfirmDialog";
import { TextDialog } from "./TextDialog";

interface ProposalCardProps {
  card: ApprovalCard;
}

type DialogKind = "none" | "t2" | "reject" | "comment";

/** §7 落点行（原生 targetLine）：新建 repo 绿 / your-workbench 只出文档 灰 / 改现有 橙 */
export function TargetLine({ card }: { card: ApprovalCard }) {
  const { text } = useI18n();
  const name = (typeof card.target_name === "string" && card.target_name)
    || (typeof card.target_repo === "string" && card.target_repo ? card.target_repo.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : "")
    || "";
  if (!card.target_kind || !name) return null;
  if (card.target_kind === "new") {
    return <p className="card-line is-success">{text(`🟢 新建 repo: ${name}`, `🟢 New repo: ${name}`)}</p>;
  }
  if (card.target_kind !== "existing") return null;
  if (name.endsWith("your-workbench")) {
    // your-workbench = 文书草稿的家，不是改代码——原生同句
    return (
      <p className="card-line">
        {text("📄 草稿落点: your-workbench（只出文档，不动任何代码）", "📄 Drafts land in: your-workbench (documents only, no code touched)")}
      </p>
    );
  }
  return (
    <p className="card-line is-warning">
      {text(`🟠 修改现有: ${name}（只提 draft PR，不动主分支）`, `🟠 Modify existing: ${name} (draft PR only, main branch untouched)`)}
    </p>
  );
}

/**
 * §7 `egress[]`（issue #11）：批准这张卡会触发的出机后果，每条一行、醒目色 + ⇪ 图标，
 * 读作「后果」而不是描述。github_repo_create = 在你的 GitHub 建私有仓库并推送派生内容；
 * 未知 kind 按原文显示（披露宁多勿少）。空/缺席不渲染（flag 关 = 今日默认）。
 */
export function EgressLines({ card }: { card: ApprovalCard }) {
  const { text } = useI18n();
  const rows = Array.isArray(card.egress) ? card.egress.filter((r) => r && typeof r.kind === "string") : [];
  if (rows.length === 0) return null;
  return (
    <ul className="card-egress" aria-label={text("批准后的出机后果", "What leaves this Mac if you approve")}>
      {rows.map((r, i) => {
        const target = typeof r.target === "string" && r.target ? r.target : "";
        const label = r.kind === "github_repo_create"
          ? text(`批准后将在你的 GitHub 新建私有仓库「${target}」并推送内容`, `Approving creates the private GitHub repo “${target}” and pushes content`)
          : text(`批准后出机：${r.kind}${target ? ` → ${target}` : ""}`, `Approving sends data out: ${r.kind}${target ? ` → ${target}` : ""}`);
        return (
          <li key={`${r.kind}-${i}`} className="card-line is-danger card-egress-line">
            <span aria-hidden="true">⇪ </span>{label}
          </li>
        );
      })}
    </ul>
  );
}

export function ProposalCard({ card }: ProposalCardProps) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [dialog, setDialog] = useState<DialogKind>("none");

  const summary = typeof card.summary === "string" && card.summary ? card.summary : card.title;
  const displayTitle = typeof card.display_title === "string" && card.display_title ? card.display_title : summary;

  if (card.processing) {
    // raising 占位：dashboard.py 对 status=raising 发的形状（cf. demo_seed R-104）
    return (
      <CardSurface cardId={card.id} label={`${text("AI 研究中", "AI researching")} · ${card.title}`}>
        <CardHead card={card} title={card.title} variant="placeholder" />
        <div className="task-processing-row is-running">
          <span className="task-processing-ring" aria-hidden="true"><span /></span>
          <span className="task-processing-label">
            {text("AI 研究中，完成后变成正式提案", "AI is researching; becomes a proposal when done")}
          </span>
        </div>
      </CardSurface>
    );
  }

  const decide = (action: string, comment: string | null = null) => {
    setDialog("none");
    void submit(cardAction(card.id, action, comment));   // 动作回传永远送主键 id（§60）
  };
  const shownId = displayId(card);

  return (
    <CardSurface cardId={card.id} label={`${text("提案", "Proposal")} · ${displayTitle}`}>
      {/* 原生 ApprovalCardView：大白话摘要 15 semibold（其余四种卡是 12 medium 行标题） */}
      <CardHead card={card} title={displayTitle} variant="lg" />
      <TargetLine card={card} />
      <EgressLines card={card} />
      <div className="card-badges">
        {/* tier 章 = Mac systemPurple 粉紫（owner 验收单：粉紫T1章）；交付 tag 同紫（§10 提取表拍板） */}
        <span className="chip chip-purple">{card.tier}{card.tier_hint ? ` · ${card.tier_hint}` : ""}</span>
        {/* §50 W17：外部出身把声明档提级 T2 时点明——否则见 "T1" 却弹 T2
            确认框会莫名其妙。origin_trust 也一并 surface（types.ts 已有字段）。 */}
        {effectiveTier(card) === "T2" && card.tier !== "T2" && (
          <span className="chip chip-warning" title={card.origin_trust ? `origin: ${card.origin_trust}` : undefined}>
            {text("外部来源提级 T2", "External → T2")}
          </span>
        )}
        {card.delivery_mode === "chat" && (
          <span className="chip chip-purple">{text("交付：聊天成稿", "Deliver: chat draft")}</span>
        )}
        {/* 紧急截止 = Mac 红字——outline 档红 chip（文字前置），非紧急保持中性 */}
        {card.deadline && (
          <span className={typeof card.days_left === "number" && card.days_left <= 3 ? "chip chip-danger chip-outline" : "chip"}>
            {card.deadline}
            {typeof card.days_left === "number" ? text(`（剩 ${card.days_left} 天）`, ` (${card.days_left}d left)`) : ""}
          </span>
        )}
        {card.show_cost && typeof card.cost_usd === "number" && (
          <span className="chip">${card.cost_usd}</span>
        )}
        {card.hardness === "hard" && <span className="chip chip-danger">{text("硬需求", "Hard")}</span>}
        {/* 被提×N 是 lineage 计数——quiet 档，比状态 chip 安静 */}
        {typeof card.repeated === "number" && card.repeated > 1 && (
          <span
            className="chip chip-warning chip-quiet"
            title={text(`这件事被提起过 ${card.repeated} 次，重述已合并进这张卡`, `This came up ${card.repeated} times — restatements were merged into this card`)}
          >
            {text(`被提×${card.repeated}`, `Raised ×${card.repeated}`)}
          </span>
        )}
        {/* §44 静默并入可见且可逆（原生紫章 已并入×N；拆回在详情抽屉的并入记录） */}
        {typeof card.silent_merged === "number" && card.silent_merged >= 1 && (
          <span
            className="chip chip-purple chip-quiet"
            title={text(`${card.silent_merged} 张重复卡片已静默并入这张卡；详情里的并入记录可一键拆回独立卡片`, `${card.silent_merged} duplicate card(s) were silently folded in; each fold note in the details can be split back out`)}
          >
            {text(`已并入×${card.silent_merged}`, `Folded ×${card.silent_merged}`)}
          </span>
        )}
        {card.green_sign && (
          <span className="chip chip-warning">
            {text("需 manager green-sign（只出草稿）", "Needs manager green-sign (draft only)")}
          </span>
        )}
        {card.reraised && <span className="chip chip-warning">{text("↩︎ 回锅", "↩︎ Returned")}</span>}
      </div>
      {card.reraised && card.reraised_note && <p className="card-line is-warning">{card.reraised_note}</p>}
      {card.disagreement && (
        <p className="card-line is-warning is-body">{text("⚠ 有分歧：", "⚠ Disagreement: ")}{card.disagreement}</p>
      )}
      <CardDetails cardId={card.id}>
        {/* 长技术标题住在详情里（原生 expandedDetail 首行）；展示名与它不同才重复一遍 */}
        {card.title !== displayTitle && <p className="card-detail-title">{card.title}</p>}
        <p className="card-detail-heading">💰 {costLine(card, text)}</p>
        <SourceList sources={card.sources} />
        <PlanList plan={card.plan} />
        <DodList dod={card.dod} />
      </CardDetails>
      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
      ) : (
        <div className="card-actions">
          {/* 四动词色相 = Mac tint 一比一（Cards.swift normalBody）：绿批准 · 红拒绝 · 蓝修改 · 灰暂缓 */}
          <button
            type="button"
            className="btn btn-success"
            // W17（§50）：typed-confirm 闸门读 effective_tier——外部升档卡
            // （声明 T1、生效 T2）也必须过确认词，绝不单击直批
            onClick={() => (effectiveTier(card) === "T2" ? setDialog("t2") : decide("approve"))}
          >
            {text("批准", "Approve")}
          </button>
          <button type="button" className="btn btn-danger" onClick={() => setDialog("reject")}>
            {text("拒绝", "Reject")}
          </button>
          <button type="button" className="btn btn-info" onClick={() => setDialog("comment")}>
            {text("修改", "Comment")}
          </button>
          <button type="button" className="btn" onClick={() => decide("defer")}>
            {text("暂缓", "Later")}
          </button>
          <DetailsToggle cardId={card.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {dialog === "t2" && (
        <T2ConfirmDialog
          cardId={shownId}
          summary={summary}
          costLine={costLine(card, text)}
          onConfirm={() => decide("approve")}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "reject" && (
        <ForkDialog
          title={text(`拒绝 ${shownId}？`, `Reject ${shownId}?`)}
          body={summary}
          choices={[
            { label: text("不想做（进回收站）", "Won't do (to trash)"), isDanger: true, onPick: () => decide("reject") },
            { label: text("已办完（记为已交付）", "Already done (mark delivered)"), onPick: () => decide("done_external") },
          ]}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "comment" && (
        <TextDialog
          title={text("修改方向", "Change of direction")}
          body={text("你的意见会并入计划，卡片重新等待审批。", "Your input folds into the plan; the card waits for re-approval.")}
          placeholder={text("想怎么改？", "What should change?")}
          submitLabel={text("提交", "Submit")}
          onSubmit={(t) => decide("comment", t)}
          onCancel={() => setDialog("none")}
        />
      )}
    </CardSurface>
  );
}
