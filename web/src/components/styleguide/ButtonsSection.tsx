// 第 2 节 Buttons：全部真按钮动词，从【真组件】里长出来（fixture props 喂进
// DebtCardItem / RunningCard / ReviewCard / DoneCard / LaneComposer）。
// 按钮是活的——点击会真发 POST /api/actions（fixture id 不存在，server 拒绝，无副作用）。
// 变体条（.btn / .btn-primary / .btn-danger + disabled）用真实 class 直接展示态。
// §78（D80，issue #447）：提案卡的那一格随 ProposalCard 一起墓碑——它的四颗动词里有两颗
// （批准 / 暂缓）在新架构里不存在，留在样板间等于让退役动词从这一页漏回产品。
import { useI18n } from "../../i18n";
import { DebtCardItem } from "../board/DebtCardItem";
import { DoneCard } from "../board/DoneCard";
import { LaneComposer } from "../board/LaneComposer";
import { ReviewCard } from "../board/ReviewCard";
import { RunningCard } from "../board/RunningCard";
import {
  BACKLOG_T1,
  REVIEW_FIXTURE,
  TASK_BLOCKED,
  TASK_DONE,
  TASK_WORKING,
} from "./fixtures";
import { SpecimenNote } from "./SpecimenNote";

export function ButtonsSection() {
  const { text } = useI18n();
  return (
    <div className="sg-grid">
      <figure className="sg-specimen">
        <RunningCard row={TASK_BLOCKED} isBlocked />
        <SpecimenNote
          zh="需输入卡动词：回答…（.btn-warning 橙，Mac answer 同橙）· 停止（.btn-warning 橙，fork 弹窗内「退回提案」为 danger 语义）"
          en="Blocked-card verbs: Answer… (.btn-warning orange, matching Mac), Stop (.btn-warning orange; the fork dialog's discard branch is the danger one)"
        />
      </figure>
      <figure className="sg-specimen">
        <RunningCard row={TASK_WORKING} />
        <SpecimenNote
          zh="执行中卡动词：评论（中性 .btn——web fork 动词，走 steer 中继，Mac 无对应 tint）· 停止（.btn-warning 橙）"
          en="Working-card verbs: Comment (neutral .btn — a web-fork verb relayed as steer, no Mac tint counterpart), Stop (.btn-warning orange)"
        />
      </figure>
      <figure className="sg-specimen">
        <ReviewCard card={REVIEW_FIXTURE} />
        <SpecimenNote
          zh="待验收卡动词：验收（.btn-success 绿）· 打回（.btn-warning 橙）· 复制成稿（.btn-accent 青，final_draft 非空才出现，1.5s「已复制 ✓」回执）"
          en="Review verbs: Accept (.btn-success green), Send Back (.btn-warning orange), Copy final draft (.btn-accent teal, only with final_draft; 1.5s Copied ✓ receipt)"
        />
      </figure>
      <figure className="sg-specimen">
        <DoneCard row={TASK_DONE} />
        <SpecimenNote
          zh="完成卡动词：退回待验收（.btn-accent 青）· 永久完成（中性 .btn 灰，确认弹窗）"
          en="Done verbs: Back to review (.btn-accent teal), Done for good (neutral grey .btn, confirm dialog)"
        />
      </figure>
      <figure className="sg-specimen">
        <DebtCardItem item={BACKLOG_T1} />
        <SpecimenNote
          zh="潜在任务卡动词（§78 起这是看板上唯一的机器卡动词行，Mac tint 一比一）：促成运行（.btn-success 绿，原生批准同一颗绿；T2 才弹键入确认）· 拒绝（.btn-danger 红，fork 弹窗）· 修改（.btn-info 蓝）· 研究并提议（.btn-info 蓝，Mac DebtRow 同蓝）· 删除（.btn-danger 红，进回收站可恢复所以不弹确认）· 永久完成（封存）（中性 .btn 灰）"
          en="Backlog verbs (since §78 the board's only machine-card verb row; one-to-one with Mac tints): Run it (.btn-success green — the native Approve green; typed confirm only on T2), Reject (.btn-danger red, fork dialog), Comment (.btn-info blue), Research & propose (.btn-info blue, matching Mac's DebtRow), Delete (.btn-danger red; recoverable via trash, so no confirm), Done for good (neutral .btn)"
        />
      </figure>
      <figure className="sg-specimen">
        {/* 占位句逐字镜像各自的挂载点（防腐 #10）：捕获框在潜在任务条头（BacklogStrip），直跑框在运行中列头
            （BoardLanes）——§78 起捕获框不再说「提案」，直跑也不再说「跳过提案」（那一列没了） */}
        <LaneComposer
          placeholder={text("一句话，先记下来，AI 来补计划…", "One line — jot it down, the AI fills in the plan…")}
          submitLabel={text("捕获", "Capture")}
          buildBody={(t) => ({ action: "capture", text: t })}
        />
        <LaneComposer
          placeholder={text("一句话，直接开跑…", "One line — run it now…")}
          submitLabel={text("直跑", "Run")}
          buildBody={(t) => ({ action: "capture", text: t, mode: "run" })}
        />
        <SpecimenNote
          zh="两个输入框（真 LaneComposer，⚠️ 真捕获通道——在这里提交会真的铸卡）：捕获框住潜在任务条头、直跑框住运行中列头（§78）；多行 textarea 1…5 行自动增高，回车 = 换行、只有按钮提交（D35）；捕获 / 直跑按钮都是 .btn-primary、贴底；输入框空或提交中自动 disabled（下方即活的 disabled 态）；focus 环 --accent-soft + --accent"
          en="Two composers (real LaneComposer — ⚠️ the real capture channel; submitting here mints a real card): Capture sits at the head of the Backlog strip and Run at the head of the Running lane (§78); a textarea that grows from 1 to 5 rows, Enter = newline, the button is the only submit (D35); Capture / Run are .btn-primary, bottom-aligned; the button disables itself while empty or busy (a live disabled state); focus ring --accent-soft + --accent"
        />
      </figure>
      <figure className="sg-specimen">
        <div className="card-actions">
          <button type="button" className="btn">{text("中性", "Neutral")}</button>
          <button type="button" className="btn btn-primary">{text("主色", "Primary")}</button>
          <button type="button" className="btn btn-success">{text("绿", "Green")}</button>
          <button type="button" className="btn btn-danger">{text("红", "Red")}</button>
          <button type="button" className="btn btn-info">{text("蓝", "Blue")}</button>
          <button type="button" className="btn btn-warning">{text("橙", "Orange")}</button>
          <button type="button" className="btn btn-accent">{text("青", "Teal")}</button>
          <button type="button" className="btn" disabled>{text("中性 disabled", "Neutral disabled")}</button>
          <button type="button" className="btn btn-primary" disabled>{text("主色 disabled", "Primary disabled")}</button>
          <button type="button" className="btn btn-danger" disabled>{text("危险 disabled", "Danger disabled")}</button>
        </div>
        <SpecimenNote
          zh="按钮变体全家 + disabled 态（opacity .5）。语义动词按钮是阶梯第 3 档（描边+色字）：hover 升 tinted 底（--X-soft）并走 --X-hover，active 走 --X-active——light 逐级加深、dark 逐级提亮，全部 token 化。filled 只保留 .btn-primary（--accent/--on-accent，hover --accent-hover / active --accent-active）"
          en="All button variants plus disabled (opacity .5). Semantic verb buttons are ladder step 3 (outline + hue text): hover raises the tinted bg (--X-soft) with --X-hover, active uses --X-active — light darkens per step, dark lightens, all tokenized. Filled stays reserved for .btn-primary (--accent/--on-accent, hover --accent-hover / active --accent-active)"
        />
      </figure>
    </div>
  );
}
