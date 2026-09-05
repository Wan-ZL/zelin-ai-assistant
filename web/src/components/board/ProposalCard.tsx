// 提案卡（needs_approval 分区项，含 raising 占位）。四颗决策动词镜像 Mac v0.21 拍板：
//   批准（T2 走 typed-confirm 弹窗，wire 不变）· 拒绝（fork：不想做 reject / 已办完
//   done_external，§41）· 修改（comment 文本弹窗）· 暂缓（defer，提案→潜在任务）。
// processing=true 的灰卡是 AI 研究中占位——只展示 sheen，不给决策按钮。
// 卡面（原生 ApprovalCardView.normalBody 收起态）：摘要 + 落点行（§7 target_kind）+ 章行
//   + 分歧 + 回锅注。技术标题 / 💰 费用 / 💬 需求来自 / 📋 要做什么 / 怎样算办完 住右侧详情侧栏
//   （「展开详情 ▸」打开，D34——卡片详情只有这一面，DetailFields 渲染）。id 在右上角（原生 idTag）。
// 标题 = §37 摘要优先链 cardHeadline（原生 displaySummary：钦定名 > summary > display_title > title）——
//   卡面、aria-label、T2 / 拒绝弹窗正文、AI 研究中占位同一个字串（原生 Cards.swift 945 / 984 / 1001 / 1073）。
import { useState } from "react";
import { displayId } from "../../cardId";
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import type { ApprovalCard } from "../../types";
import { cardAction, costLine, deadlinePhrase, effectiveTier, hardnessLabel, moneyOf, tierHint, useSubmit, pendingNote } from "./boardActions";
import { CardHead, CardSurface, DetailsToggle, MergeStateChip, useDetailViewed } from "./cardChrome";
import { cardHeadline } from "./cardHeadline";
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
  const { text, language } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();
  const [dialog, setDialog] = useState<DialogKind>("none");
  // 原生 T2 gate 的「展开过」= 本会话打开过这张卡的详情侧栏（就地展开退役后唯一的「看明细」入口）
  const detailViewed = useDetailViewed(card.id);

  // §37 摘要优先面：卡面 / 弹窗 / 占位 全用同一个 headline（原生 card.displaySummary）
  const headline = cardHeadline(card) || card.title;

  if (card.processing) {
    // raising 占位：dashboard.py 对 status=raising 发的形状（cf. demo_seed R-104）；原生 Cards.swift:945 同读 displaySummary
    return (
      <CardSurface cardId={card.id} label={`${text("AI 研究中", "AI researching")} · ${headline}`}>
        <CardHead card={card} title={headline} variant="placeholder" />
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
    <CardSurface cardId={card.id} label={`${text("提案", "Proposal")} · ${headline}`}>
      {/* 原生 ApprovalCardView：大白话摘要 15 semibold（其余四种卡是 12 medium 行标题） */}
      <CardHead card={card} title={headline} variant="lg" selectable />
      <TargetLine card={card} />
      <EgressLines card={card} />
      <div className="card-badges">
        {/* 合并态角标（合并分析中… / 合并中…）——原生 cardOverlay 压在卡右上；web 放章行首 */}
        <MergeStateChip cardId={card.id} />
        {/* tier 章 = Mac systemPurple 粉紫（owner 验收单：粉紫T1章）；交付 tag 同紫（§10 提取表拍板）。
            原生 tierLine：「T1 · 一键可批」——tier 与大白话各一个节点；未知 tier 只剩「未分级」 */}
        <span className="chip chip-purple">
          {typeof card.tier === "string" && /^T[0-2]$/.test(card.tier) && <><span>{card.tier}</span>{"\u00a0·\u00a0"}</>}
          <span>{tierHint(card, text)}</span>
        </span>
        {/* 原生 ↳ 改进 #R-xx（improvement_of，§7 提案改进已交付的卡） */}
        {typeof card.improvement_of === "string" && card.improvement_of && (
          <span className="chip chip-quiet">{text(`↳ 改进 #${card.improvement_of}`, `↳ Improves #${card.improvement_of}`)}</span>
        )}
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
        {/* 紧急截止 = Mac 红字——outline 档红 chip（文字前置），非紧急保持中性；
            原生「截止 2026-09-08 · 还剩 6 天」（已逾期 N 天 / 今天截止 / 还剩 N 天） */}
        {card.deadline && (
          <span className={typeof card.days_left === "number" && card.days_left <= 3 ? "chip chip-danger chip-outline" : "chip"}>
            <span>{text(`截止 ${card.deadline}`, `Due ${card.deadline}`)}</span>
            {deadlinePhrase(card.days_left, text) && <>{"\u00a0·\u00a0"}<span>{deadlinePhrase(card.days_left, text)}</span></>}
          </span>
        )}
        {/* 原生 Cards.swift:1240 `if card.show_cost, let cost = card.cost_usd { Badge(money(cost)) }`——
            money：整数不带小数（$12），否则两位（$0.50）；show_cost 只在有估价（cost_state=estimated）时为真 */}
        {card.show_cost && moneyOf(card) && (
          <span className="chip">{moneyOf(card)}</span>
        )}
        {hardnessLabel(card.hardness, text) && (
          <span className={card.hardness === "hard" ? "chip chip-danger" : "chip"}>{hardnessLabel(card.hardness, text)}</span>
        )}
        {typeof card.type === "string" && card.type && <span className="chip">{domainLabel(TYPE_LABELS, language, card.type)}</span>}
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
        {/* 原生 reraisedBadge（Cards.swift:1183-1196）：琥珀胶囊「↩︎ 回锅 · Returned」+ 同色大白话小字并排 */}
        {card.reraised && (
          <>
            <span className="chip chip-warning">{text("↩︎ 回锅 · Returned", "↩︎ Returned")}</span>
            <span className="card-meta-text is-warning">
              {text("你之前验收过这件事，来了新信息", "You accepted this before — new info arrived")}
            </span>
          </>
        )}
      </div>
      {/* 原生 returnedNote：「新增：<回锅带来的新信息>」 */}
      {card.reraised && card.reraised_note && (
        <p className="card-line is-warning"><span className="card-detail-label">{text("新增：", "New: ")}</span><span>{String(card.reraised_note)}</span></p>
      )}
      {card.disagreement && (
        <p className="card-line is-warning is-body"><span className="card-detail-label">{text("⚠︎ 分歧: ", "⚠︎ Disagreement: ")}</span><span>{String(card.disagreement)}</span></p>
      )}
      {pending ? (
        <p className="card-pending-note">{pendingNote(pendingAction, text)}</p>
      ) : (
        <div className="card-actions">
          {/* 四动词色相 = Mac tint 一比一（Cards.swift normalBody）：绿批准 · 红拒绝 · 蓝修改 · 灰暂缓 */}
          {/* 原生 T2 gate：没看过明细（详情侧栏没打开过）不给「批准」，只给一句提示——先看明细再确认（§50 读 effectiveTier） */}
          {effectiveTier(card) === "T2" && !detailViewed ? (
            <span className="card-line is-warning card-t2-hint">{text("T2 需先展开看明细", "T2: expand details first")}</span>
          ) : (
            <button
              type="button"
              className="btn btn-success"
              // W17（§50）：typed-confirm 闸门读 effective_tier——外部升档卡
              // （声明 T1、生效 T2）也必须过确认词，绝不单击直批；T2 的「批准」开的是弹窗（a11y 标出）
              aria-haspopup={effectiveTier(card) === "T2" ? "dialog" : undefined}
              onClick={() => (effectiveTier(card) === "T2" ? setDialog("t2") : decide("approve"))}
            >
              {text("批准", "Approve")}
            </button>
          )}
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
          summary={headline}
          costLine={costLine(card, text)}
          onConfirm={() => decide("approve")}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "reject" && (
        <ForkDialog
          title={text("这张卡不需要执行？", "No need to run this card?")}
          body={headline}
          choices={[
            { label: text("不想做（进回收站）", "Won't do (to trash)"), isDanger: true, onPick: () => decide("reject") },
            { label: text("已办完（记为已交付）", "Already done (mark delivered)"), onPick: () => decide("done_external") },
          ]}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "comment" && (
        <TextDialog
          title={text("💬 修改方向", "💬 Comment / Change Direction")}
          body={text("你的意见会并入计划，卡片重新等待审批。", "Your input folds into the plan; the card waits for re-approval.")}
          placeholder={text("改哪里…", "What to change…")}
          submitLabel={text("提交", "Submit")}
          onSubmit={(t) => decide("comment", t)}
          onCancel={() => setDialog("none")}
        />
      )}
    </CardSurface>
  );
}
