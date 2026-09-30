// 潜在任务卡（debt 行；经 chrome/BacklogStrip 的 renderCard 缝注入——侧条开合归 G4，
// 本组件只管卡面 + 动词）。**§78 提案车道退役后这是唯一的机器卡面**：机器（雷达 / 每日循环 /
// self_improve）铸的卡一律落这里，owner 在这张卡上一次点击（促成运行）才开跑——卡面因此长成
// 旧提案卡的全貌，不再是债务行的缩略版（egress / effective_tier / 费用任缺一样，「促成运行」
// 就成了瞎批：§7 issue #11 / §50 打字确认 / §40）。字段全由 server 的 `_backlog_row` 给，
// 缺席（老 server 的债务行）即不渲染那一节，卡面自然退回改动前的样子。
// 动词（全是 §10 既有 inbox 动词，一个新的都没开）：
//   促成运行（approve；T2 / W17 生效 T2 走 typed-confirm 弹窗，wire 不变）· 拒绝（fork：不想做
//   reject / 已办完 done_external，§41）· 修改（comment 文本弹窗）· 研究并提议（raise → AI **就地**
//   补上下文与计划，卡不换列，回来时同一张卡填满了）· 删除（trash → 回收站，可恢复，不弹确认）·
//   永久完成（封存，不再提示）（archive → 永久性完成书立条，可逆不弹确认）。
// processing=true 的灰卡 = raising 占位（AI 研究中）：只展示 sheen，不给任何决策按钮——
//   还没有计划与验收标准的卡不该能被「促成运行」（§0.4 AI 的话永不自己走到 owner 承诺的状态）。
// §76.2（issue #313）三颗结算信号（全是 server 算好的只读判据）：completion_hint → 绿章
//   「疑似已完成」+ 证据一句 + 两颗一键（done_external / reject）；decision_due → 红色决策提示行；
//   mention_escalated → 被提×N 章从 quiet 转 danger「仍未处理」。三个都不改状态（§76.1）。
// 卡面：摘要标题（§37 摘要优先链 cardHeadline = 原生 item.displaySummary，Cards.swift:2028；URL 可点）
//   + 落点行 + 出机后果 + 章行（type / 难度 / tier / 截止 / 费用 / 被提×N / 已并入×N …）
//   + 怎样算办完（DodFace 紧凑形，D43）+ 分歧 + 回锅注；技术标题 / 💬 需求来自 / 📋 要做什么
//   住右侧详情侧栏（「展开详情 ▸」打开，D34）。
// 落点行 `TargetLine` 与出机后果 `EgressLines` 曾住 ProposalCard.tsx；那张卡 D80 随提案列一起
//   墓碑（§78，issue #447）后两块积木搬进本文件——唯一的机器卡面持有自己的全部积木，不留孤儿模块。
// 卡面不许留退役动词：「批准」在这张卡上叫**促成运行**、「暂缓」（defer）没有按钮了，所以
//   decision_due 决策行与 mention_escalated 提示句点名的都是 促成运行 / 拒绝（§78；wire 动词名按
//   add-only 一个没改，§41 打字确认弹窗里那颗仍是原生的「批准」）。
import { useState } from "react";
import { displayId } from "../../cardId";
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import type { DebtCard } from "../../types";
import { cardAction, costLine, deadlinePhrase, effectiveTier, hardnessLabel, moneyOf, pendingNote, tierHint, useSubmit } from "./boardActions";
import { CardHead, CardSurface, DetailsToggle, MergeStateChip, SessionHitChip, useDetailViewed } from "./cardChrome";
import { cardHeadline } from "./cardHeadline";
import { DodFace } from "./DodFace";
import { ForkDialog } from "./ForkDialog";
import { T2ConfirmDialog } from "./T2ConfirmDialog";
import { TextDialog } from "./TextDialog";

interface DebtCardItemProps {
  item: DebtCard;
}

type DialogKind = "none" | "t2" | "reject" | "comment";

/** §7 落点行（原生 targetLine）：新建 repo 绿 / your-workbench 只出文档 灰 / 改现有 橙。
 *  §78 起只有这张卡长这一行（提案卡退役，两块积木随卡搬进本文件） */
export function TargetLine({ card }: { card: DebtCard }) {
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
 * §7 `egress[]`（issue #11）：促成运行这张卡会触发的出机后果，每条一行、醒目色 + ⇪ 图标，
 * 读作「后果」而不是描述。github_repo_create = 在你的 GitHub 建私有仓库并推送派生内容；
 * 未知 kind 按原文显示（披露宁多勿少）。空/缺席不渲染（flag 关 = 今日默认）。
 * §78 起「促成运行」不许坐在一张藏了出机后果的卡上——这一段跟着动词长在本卡面上。
 * 披露句仍说「批准后」：它是 §41 打字确认弹窗那颗「批准」的同义面（wire 动词 approve 未改名，add-only）。
 */
export function EgressLines({ card }: { card: DebtCard }) {
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

export function DebtCardItem({ item }: DebtCardItemProps) {
  const { text, language } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();
  const [dialog, setDialog] = useState<DialogKind>("none");
  // 原生 T2 gate 的「展开过」= 本会话打开过这张卡的详情侧栏（就地展开退役后唯一的「看明细」入口）
  const detailViewed = useDetailViewed(item.id);
  const headline = cardHeadline(item) || item.title;

  if (item.processing) {
    // raising 占位：dashboard.py `_backlog_row` 对 status=raising 发的形状（processing: true）；
    // §78 起它不再「变成提案」——研究完就是这张卡自己填满了计划与验收标准
    return (
      <CardSurface cardId={item.id} label={`${text("AI 研究中", "AI researching")} · ${headline}`}>
        <CardHead card={item} title={headline} variant="placeholder" />
        <div className="task-processing-row is-running">
          <span className="task-processing-ring" aria-hidden="true"><span /></span>
          <span className="task-processing-label">
            {text("AI 研究中，完成后这张卡会补上计划与验收标准", "AI is researching; the plan and DoD land on this card when it finishes")}
          </span>
        </div>
      </CardSurface>
    );
  }

  const decide = (action: string, comment: string | null = null) => {
    setDialog("none");
    void submit(cardAction(item.id, action, comment));   // 动作回传永远送主键 id（§60）
  };
  const shownId = displayId(item);
  const hardness = hardnessLabel(item.hardness, text);
  // §76.2 疑似已完成证据：wire 形 {at, note, channel}，非对象一律当缺席（server 已消毒过一遍）
  const hint = item.completion_hint && typeof item.completion_hint === "object" ? item.completion_hint : null;
  const hintNote = typeof hint?.note === "string" ? hint.note : "";
  // §50 审批闸门读生效档位；wire 上 tier 恒在（可能是空串 = 真的没分级，卡面照原生说「未分级」）
  const gateTier = effectiveTier({ tier: typeof item.tier === "string" ? item.tier : "", effective_tier: item.effective_tier });

  return (
    <CardSurface cardId={item.id} label={`${text("潜在任务", "Backlog")} · ${headline}`} selectable>
      {/* 摘要里的 URL 可点（原生 Cards.swift:2028 linkified）；v0.21 契约七：潜在任务卡也可多选参与合并（Kanban.swift:337-339）。
          variant="lg" = §7 大白话摘要的审批卡字号（15 semibold，原生 ApprovalCardView Cards.swift:1074 同款）：
          §78 起「促成运行」长在这张卡上，owner 按键前读的就是这一行——它不能还是债务行的 12 medium 行标题 */}
      <CardHead card={item} title={headline} leading={<span className="card-dot is-backlog" aria-hidden="true" />} variant="lg" linkify />
      <TargetLine card={item} />
      <EgressLines card={item} />
      <div className="card-badges">
        {/* 合并态角标（合并分析中… / 合并中…）——原生 cardOverlay 压在卡右上；web 放章行首 */}
        <MergeStateChip cardId={item.id} />
        {/* §37.2 会话层「命中会话」（原生 DebtRow 章行首位，Cards.swift:2034） */}
        <SessionHitChip row={item} />
        {/* tier 章 = Mac systemPurple 粉紫：「T1 · 一键可批」——tier 与大白话各一个节点；
            未知 / 空 tier 只剩「未分级」（原生同样 never T?）——促成运行那颗键旁边永远说得出档位 */}
        <span className="chip chip-purple">
          {typeof item.tier === "string" && /^T[0-2]$/.test(item.tier) && <><span>{item.tier}</span>{" · "}</>}
          <span>{tierHint(item, text)}</span>
        </span>
        {/* 原生 ↳ 改进 #R-xx（improvement_of，§7 改进已交付的卡） */}
        {typeof item.improvement_of === "string" && item.improvement_of && (
          <span className="chip chip-quiet">{text(`↳ 改进 #${item.improvement_of}`, `↳ Improves #${item.improvement_of}`)}</span>
        )}
        {/* §50 W17：外部出身把声明档提级 T2 时点明——否则见 "T1" 却弹 T2 确认框会莫名其妙 */}
        {gateTier === "T2" && item.tier !== "T2" && (
          <span className="chip chip-warning" title={item.origin_trust ? `origin: ${item.origin_trust}` : undefined}>
            {text("外部来源提级 T2", "External → T2")}
          </span>
        )}
        {item.delivery_mode === "chat" && (
          <span className="chip chip-purple">{text("交付：聊天成稿", "Deliver: chat draft")}</span>
        )}
        {/* 紧急截止 = Mac 红字——outline 档红 chip（文字前置），非紧急保持中性 */}
        {item.deadline && (
          <span className={typeof item.days_left === "number" && item.days_left <= 3 ? "chip chip-danger chip-outline" : "chip"}>
            <span>{text(`截止 ${item.deadline}`, `Due ${item.deadline}`)}</span>
            {deadlinePhrase(item.days_left, text) && <>{" · "}<span>{deadlinePhrase(item.days_left, text)}</span></>}
          </span>
        )}
        {/* 原生 Cards.swift:1240：show_cost 只在有估价（cost_state=estimated）时为真；money 整数不带小数 */}
        {item.show_cost && moneyOf(item) && (
          <span className="chip">{moneyOf(item)}</span>
        )}
        {item.type && <span className="chip">{domainLabel(TYPE_LABELS, language, item.type)}</span>}
        {hardness && <span className={item.hardness === "hard" ? "chip chip-danger" : "chip"}>{hardness}</span>}
        {/* 被提×N 是 lineage 计数——quiet 档；§76.2 mention_escalated 时转 danger 并说出「仍未处理」。
            §78：提示句只许点名这张卡上真有的动词——「批准」改叫促成运行、「暂缓」随提案列退役，
            所以这里与 decision_due 那一行、与 notify.py `msg_deadline_due` 同一对动词（促成运行 / 拒绝） */}
        {typeof item.repeated === "number" && item.repeated > 1 && (
          <span
            className={item.mention_escalated ? "chip chip-danger" : "chip chip-warning chip-quiet"}
            title={item.mention_escalated
              ? text(`这件事被提起过 ${item.repeated} 次，一直没有促成运行或拒绝`, `This came up ${item.repeated} times and was never run or rejected`)
              : text(`这件事被提起过 ${item.repeated} 次，重述已合并进这张卡`, `This came up ${item.repeated} times — restatements were merged into this card`)}
          >
            {item.mention_escalated
              ? text(`被提×${item.repeated} · 仍未处理`, `Raised ×${item.repeated} · still unhandled`)
              : text(`被提×${item.repeated}`, `Raised ×${item.repeated}`)}
          </span>
        )}
        {/* §76.2 疑似已完成：雷达扫到「这件事已经发生」的证据——章只说提示，状态一个字没改 */}
        {hint && (
          <span
            className="chip chip-success"
            title={text("雷达在新证据里看到这件事已经发生；状态没有变，怎么处理由你点", "The radar saw evidence this already happened; nothing changed status — the call is yours")}
          >
            {text("✅ 疑似已完成", "✅ Looks already done")}
          </span>
        )}
        {/* §44 静默并入可见且可逆（原生紫章 已并入×N；拆回在详情抽屉的并入记录） */}
        {typeof item.silent_merged === "number" && item.silent_merged >= 1 && (
          <span
            className="chip chip-purple chip-quiet"
            title={text(`${item.silent_merged} 张重复卡片已静默并入这张卡；详情里的并入记录可一键拆回独立卡片`, `${item.silent_merged} duplicate card(s) were silently folded in; each fold note in the details can be split back out`)}
          >
            {text(`已并入×${item.silent_merged}`, `Folded ×${item.silent_merged}`)}
          </span>
        )}
        {item.green_sign && (
          <span className="chip chip-warning">
            {text("需 manager green-sign（只出草稿）", "Needs manager green-sign (draft only)")}
          </span>
        )}
        {/* 原生 reraisedBadge（Cards.swift:1183-1196）：琥珀胶囊 + 同色大白话小字并排 */}
        {item.reraised && (
          <>
            <span className="chip chip-warning">{text("↩︎ 回锅 · Returned", "↩︎ Returned")}</span>
            <span className="card-meta-text is-warning">
              {text("你之前验收过这件事，来了新信息", "You accepted this before — new info arrived")}
            </span>
          </>
        )}
      </div>
      {/* §11 验收标准——D43 紧凑形（前 3 条 + 「+N」，全文在详情侧栏）；促成运行 = 连这份 DoD 一起批 */}
      <DodFace items={item.dod} variant="dod" />
      {/* 原生 returnedNote：「新增：<回锅带来的新信息>」 */}
      {item.reraised && item.reraised_note && (
        <p className="card-line is-warning"><span className="card-detail-label">{text("新增：", "New: ")}</span><span>{String(item.reraised_note)}</span></p>
      )}
      {item.disagreement && (
        <p className="card-line is-warning is-body"><span className="card-detail-label">{text("⚠︎ 分歧: ", "⚠︎ Disagreement: ")}</span><span>{String(item.disagreement)}</span></p>
      )}
      {/* §76.2 疑似已完成的证据一句 + 两颗一键（§10 既有动词 done_external / reject，不开新动作）。
          雷达的完成判断可能是错的——所以这里只给出口，不替用户拍板（§76.1）。 */}
      {hint && hintNote && (
        <p className="card-line is-success is-body">
          <span className="card-detail-label">{text("✅ 证据: ", "✅ Evidence: ")}</span><span>{hintNote}</span>
        </p>
      )}
      {/* §76.2 截止日已到仍没人拍板：点名这张卡上真有的出口，按钮就在下一排（不另造第二套动词）。
          §78 起点名的是**促成运行 / 拒绝**——「批准」改叫促成运行、「暂缓」随提案列一起退役（没有那颗键了），
          与 §76.3 的通知一句逐字同源（act/lib/notify.py `msg_deadline_due`：「现在做个决定：促成运行 / 拒绝」） */}
      {item.decision_due && (
        <p className="card-line is-danger is-body" role="note">
          {text("⏰ 截止日已到，这张卡还没拍板 —— 现在决定：促成运行 / 拒绝", "⏰ Past its deadline and still undecided — decide now: Run it / Reject")}
        </p>
      )}
      {hint && !pending && (
        <div className="card-actions">
          <button type="button" className="btn btn-success" onClick={() => decide("done_external")}>
            {text("已办完 · 记为已交付", "Already done · mark delivered")}
          </button>
          <button type="button" className="btn btn-danger" onClick={() => decide("reject")}>
            {text("不做 · 进回收站", "Won't do · to trash")}
          </button>
        </div>
      )}
      {pending ? (
        <p className="card-pending-note">
          {pendingAction === "raise"
            ? text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted; AI is researching (usually 2-3 min)")
            : pendingNote(pendingAction, text)}
        </p>
      ) : (
        <div className="card-actions">
          {/* 色相 = Mac tint 一比一：绿促成运行 · 红拒绝 · 蓝修改 / 研究并提议 · 灰删除 / 封存 */}
          {/* 原生 T2 gate：没看过明细（详情侧栏没打开过）不给批准，只给一句提示（§50 读 effectiveTier） */}
          {gateTier === "T2" && !detailViewed ? (
            <span className="card-line is-warning card-t2-hint">{text("T2 需先展开看明细", "T2: expand details first")}</span>
          ) : (
            <button
              type="button"
              className="btn btn-success"
              // W17（§50）：typed-confirm 闸门读 effective_tier——外部升档卡（声明 T1、生效 T2）
              // 也必须过确认词，绝不单击直批；T2 的这颗开的是弹窗（a11y 标出）
              aria-haspopup={gateTier === "T2" ? "dialog" : undefined}
              title={text("开始执行这件事（§78：潜在任务 → 运行中，一次点击）", "Start work on this (§78: Backlog → Running, one click)")}
              onClick={() => (gateTier === "T2" ? setDialog("t2") : decide("approve"))}
            >
              {text("促成运行", "Run it")}
            </button>
          )}
          <button type="button" className="btn btn-danger" onClick={() => setDialog("reject")}>
            {text("拒绝", "Reject")}
          </button>
          <button type="button" className="btn btn-info" onClick={() => setDialog("comment")}>
            {text("修改", "Comment")}
          </button>
          <button
            type="button"
            className="btn btn-info"
            title={text("让 AI 就地补上下文、计划与验收标准（卡不换列，研究完还是这张卡）", "Let the AI fill in context, plan and DoD in place (the card stays here)")}
            onClick={() => void submit(cardAction(item.id, "raise"))}
          >
            {text("研究并提议", "Research & propose")}
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => void submit(cardAction(item.id, "trash"))}
          >
            {text("删除", "Delete")}
          </button>
          <button
            type="button"
            className="btn"
            title={text("封存这条：留作记录、不再参与匹配，也就不会再被提起（可从永久性完成放回）", "Seal it: kept as a record, excluded from matching so it never re-suggests (can be put back from Done for good)")}
            onClick={() => void submit(cardAction(item.id, "archive"))}
          >
            {text("永久完成（封存，不再提示）", "Done for good (seal, stop suggesting)")}
          </button>
          <DetailsToggle cardId={item.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {dialog === "t2" && (
        <T2ConfirmDialog
          cardId={shownId}
          summary={headline}
          costLine={costLine(item, text)}
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
          body={text("你的意见会并入计划，卡片留在潜在任务等你再拍板。", "Your input folds into the plan; the card stays in Backlog until you decide.")}
          placeholder={text("改哪里…", "What to change…")}
          submitLabel={text("提交", "Submit")}
          onSubmit={(t) => decide("comment", t)}
          onCancel={() => setDialog("none")}
        />
      )}
    </CardSurface>
  );
}
