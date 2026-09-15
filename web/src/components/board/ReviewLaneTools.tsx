// 待验收列头的三件工具（CONTRACT §70.2 追记 / §21 追记 / §64.6 追记；issue #312，owner 决策 D74）。
// 住 Lane 的 composer 槽（提案列「✦ 清理积压」的同一位置——待验收列此前是唯一没有列头动作的列）。
//
//   ① 隐藏 🤖：taskFilters 的 hideBot 维度（URL `bot=hide`），只藏**带** `self_improve` 键的行
//      （§2 追记；人卡与老 server 的行缺这个键 = 照常可见）。19 张里 12 张是机器卡，人的卡先露出来。
//   ② 选中全部「建议验收」(N)：§64 判官给 `建议验收` 的卡 → 带着这批 id 进多选态。
//   ③ 选中全部「中断收割」(N)：`interrupted` 的空卡（#119 受阻/放弃救活收进来的）→ 同上。
//
// 两颗「选中全部」只**预选**，不动任何卡：真正的批量动作在底部多选操作条上，按下去还要在确认
// 弹窗里看完逐条 id + 标题。§64.6 原本的「不在 web 提供按 AI 建议一键验收」由 D74 追记收窄成
// 「不提供不列清单、不用人再按一次的一键验收」——建议仍然不是默认。
// 两颗按钮数的是**当前可见**的行（过滤 / 搜索生效时所见即所选）；🤖 计数数的是整列，否则
// 打开隐藏之后它会自己变成 0。
import { useI18n } from "../../i18n";
import { beginSelection, setFilters, useAppState } from "../../store";
import type { ReviewCard } from "../../types";
import { VERDICTS } from "./VerdictChip";

/** §64 判官逐字给出「建议验收」的行（词表单源 = VerdictChip.VERDICTS） */
export function suggestedAccept(rows: ReviewCard[]): string[] {
  return rows.filter((r) => r.assessment?.verdict === VERDICTS.accept).map((r) => r.id);
}

/** #119 中断收割行（受阻 / 放弃救活被收进待验收的空卡） */
export function interruptedRows(rows: ReviewCard[]): string[] {
  return rows.filter((r) => r.interrupted === true).map((r) => r.id);
}

/** 机器卡（§2 追记的 add-only 键；缺席 = 不是机器卡） */
export function botRows(rows: ReviewCard[]): string[] {
  return rows.filter((r) => r.self_improve === true).map((r) => r.id);
}

interface ReviewLaneToolsProps {
  /** 当前可见的待验收行（过滤 / 搜索之后）——两颗「选中全部」按它数、按它选 */
  visible: ReviewCard[];
  /** 整列的待验收行（过滤之前）——🤖 计数按它数 */
  all: ReviewCard[];
}

export function ReviewLaneTools({ visible, all }: ReviewLaneToolsProps) {
  const { text } = useI18n();
  const { filters } = useAppState();
  const accept = suggestedAccept(visible);
  const interrupted = interruptedRows(visible);
  const bots = botRows(all).length;

  return (
    <div className="lane-tools" role="group" aria-label={text("待验收列的批量工具", "Review lane bulk tools")}>
      <button
        type="button"
        className={`btn lane-tool-button${filters.hideBot ? " is-on" : ""}`}
        aria-pressed={filters.hideBot}
        disabled={bots === 0 && !filters.hideBot}
        onClick={() => setFilters({ hideBot: !filters.hideBot })}
        title={bots > 0 || filters.hideBot
          ? text("把每日循环自己开的 🤖 卡收起来，只留你自己的卡（写进地址栏，刷新还在）",
            "Hide the 🤖 cards the daily loop filed for itself and keep only your own (kept in the URL across reloads)")
          : text("这一列现在没有 🤖 卡", "No 🤖 cards in this lane right now")}
      >
        <span aria-hidden="true">🤖 </span>
        <span>
          {filters.hideBot
            ? text(`已隐藏 ${bots}`, `${bots} hidden`)
            : text(`隐藏 ${bots}`, `Hide ${bots}`)}
        </span>
      </button>
      <button
        type="button"
        className="btn lane-tool-button"
        disabled={accept.length === 0}
        onClick={() => beginSelection(accept)}
        title={accept.length > 0
          ? text("勾选 AI 评为「建议验收」的卡，进多选态——底部操作条上再按一次「批量验收」，确认弹窗会逐张列给你看",
            "Select the cards the AI judged “Looks done” and switch to multi-select — the bar below still needs one press of Accept, and the dialog lists every card first")
          : text("这一列暂时没有 AI 评为「建议验收」的卡", "No cards judged “Looks done” in this lane")}
      >
        {text(`选中全部建议验收 (${accept.length})`, `Select “Looks done” (${accept.length})`)}
      </button>
      <button
        type="button"
        className="btn lane-tool-button"
        disabled={interrupted.length === 0}
        onClick={() => beginSelection(interrupted)}
        title={interrupted.length > 0
          ? text("勾选被中断收割进来的空卡，进多选态——底部操作条的「批量丢弃」把它们送进回收站（可恢复）",
            "Select the interrupted harvests and switch to multi-select — Discard on the bar below moves them to the trash (restorable)")
          : text("这一列暂时没有中断收割的卡", "No interrupted harvests in this lane")}
      >
        {text(`选中全部中断收割 (${interrupted.length})`, `Select interrupted (${interrupted.length})`)}
      </button>
    </div>
  );
}
