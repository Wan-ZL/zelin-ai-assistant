// 看板装配（BUILD-CONTRACT §2.2 列序 + 原生 Kanban.swift 的两根书立条；列名 / 空列文案逐字镜像原生，§54.4）：
//   潜在任务（BacklogStrip 左侧折叠条，经 renderCard 缝注入 DebtCardItem）|
//   运行中（合并列：needs_input blocked 卡最前 → running 分区 queued/working 混排，顶部 direct-run 框）|
//   待验收（列头 ReviewLaneTools：隐藏 🤖 / 选中全部建议验收 / 选中全部中断收割，D74）| 阶段性完成 |
//   永久性完成（ArchiveStrip 右侧折叠条——原生 v0.33 的第二根书立条）。
// **§78 提案列退役**（tombstone）：`needs_approval[]` 恒空，机器卡一律落潜在任务；这一列连同
//   §34bis「清理积压」按钮（D80.11）一起删（D80.4：§51 免批手动车道同时退役）；原本只挂在它上面的
//   四件东西**搬家不删**——快速捕获框（§34 追记：意图 = 记一件事，落 `detected`，所以它去潜在任务
//   列头；⌘L / ⌃⌥Space 跟着它走）与三个次级面（§21 合并建议卡 / §44.6 并入回执 / §21bis 强制合并
//   超时条）一并进 BacklogStrip。运行中列头的直跑框留在原地（意图 = 现在就做，`mode:"run"` → `approved`），
//   占位句里的「（跳过提案）」随列退役删掉，声明诚实归声明（.help 那句仍说清跳过了预估）。
//   两根书立条的位置不动：左条仍在全部 <Lane> 之前、右条仍在之后（§66 探针 lanes:rail-left /
//   lanes:rail-right 按这个顺序判）。
// 全部列消费全局过滤 chips + ⌘F 搜索（taskFilters.matchesCardFilters，G4 与 BacklogStrip
// 同一条规则；§37.2 词表 + 归一化 AND + 会话正文第三层——store.sessionIndex 按 id 取这张卡的归一化正文传进匹配函数，
// 只靠会话命中的卡照样留下、计入「命中/总数」并在章行长出「命中会话」（cardChrome.SessionHitChip，D45）），
// 再按 store.sortOrder 排序（cardSort.ts 镜像原生 Store.sortCards：默认新的在上）；徽章数字 = counts
// 真实总数，过滤生效时显示「命中/总数」。列头「?」说明文案来自 server 目录（Lane.tsx）。
// 列是审批状态机的投影——没有拖拽换状态，一切转移都是卡上的显式按钮动词（§0.8）。
// 滚动模型（D42，§54.4 2026-09-06 追记；原生 Kanban.swift 横向 ScrollView 里每列各一个纵向 ScrollView）：
// 本组件 = `.board-main` 这条横排（只横向滚，board.css），每列的 `.column-list` 各自纵向滚（Lane.tsx）——列头与
// 列顶输入框钉在列顶；一屏高的页壳 `.board-page` 与看板底部的多选操作条在 pages/BoardPage。
import { sortCards } from "../../cardSort";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { cardFilterCount, matchesCardFilters } from "../../taskFilters";
import { ArchiveStrip } from "../chrome/ArchiveStrip";
import { BacklogStrip } from "../chrome/BacklogStrip";
import { DebtCardItem } from "./DebtCardItem";
import { DoneCard } from "./DoneCard";
import { Lane } from "./Lane";
import { LaneComposer } from "./LaneComposer";
import { ReviewCard } from "./ReviewCard";
import { ReviewLaneTools } from "./ReviewLaneTools";
import { RunningCard } from "./RunningCard";

export function BoardLanes() {
  const { text } = useI18n();
  const { board, filters, sortOrder, sessionIndex } = useAppState();
  if (!board) return null; // AppShell 只在有快照时渲染页面，这里兜底防御

  // 第三层（会话正文）按 id 从 store.sessionIndex 取（没拉过 / 缺席 = undefined，只剩字段层）。
  // §37.2 的「processing 占位行不被搜索词藏起」随 raising 灰卡搬去了 BacklogStrip（§78）——
  // 这几列没有占位行。
  const pick = <T extends Record<string, unknown> & { id: string }>(rows: T[]): T[] =>
    rows.filter((row) => matchesCardFilters(row, filters, sessionIndex?.texts[row.id]));

  const blocked = sortCards(pick(board.needs_input), sortOrder);
  const running = sortCards(pick(board.running), sortOrder);
  const review = sortCards(pick(board.review), sortOrder);
  const completed = sortCards(pick(board.completed), sortOrder);

  const counts = board.counts;
  // 原生 laneEmptyText：搜索 / 过滤生效时空列说「无匹配卡片」而不是「什么都没有」
  const filtering = cardFilterCount(filters) > 0;
  const emptyText = (normal: string) => (filtering ? text("无匹配卡片", "No matching cards") : normal);
  // 徽章 = counts 真实总数（completed cap 50 后仍读 counts）；过滤命中数另行标注
  const label = (shown: number, total: number) => (shown === total ? `${total}` : `${shown}/${total}`);
  const runningTotal = (counts["running"] ?? board.running.length) + (counts["needs_input"] ?? board.needs_input.length);
  const completedTotal = counts["completed"] ?? board.completed.length;

  // data-scroll-memory：窄窗下横向滚过的列位置随换页记住、回看板还原（route.rememberScroll / restoreScroll，D40）
  return (
    <div className="board-main" data-scroll-memory="board-main">
      <BacklogStrip renderCard={(card) => <DebtCardItem item={card} />} />

      {/* 运行中列空态 = 原生 Kanban.swift 在常驻 composer 之下手动渲染的 lanePlaceholder 那句（「或在上面输入框里直接开跑」）；
          原生 column(emptyText:) 参数里的「AI 就开始干活」因 isEmpty: false 从未显示过，web 不镜像它 */}
      <Lane
        title={text("运行中 · running", "Running")}
        slug="running"
        countLabel={label(blocked.length + running.length, runningTotal)}
        colorToken="--status-progress"
        composer={
          <LaneComposer
            placeholder={text("一句话，直接开跑…", "One line — run it now…")}
            submitLabel={text("直跑", "Run")}
            buildBody={(t) => ({ action: "capture", text: t, mode: "run" })}
          />
        }
        isEmpty={blocked.length === 0 && running.length === 0}
        emptyText={emptyText(text("没有正在执行的任务。批准一个提案，或在上面输入框里直接开跑", "Nothing running — approve a proposal, or type above to run one now"))}
      >
        {blocked.map((row) => (
          <RunningCard key={row.id} row={row} isBlocked />
        ))}
        {running.map((row) => (
          <RunningCard key={row.id} row={row} />
        ))}
      </Lane>

      <Lane
        title={text("待验收 · review", "Review")}
        slug="review"
        countLabel={label(review.length, counts["review"] ?? board.review.length)}
        colorToken="--status-review"
        composer={<ReviewLaneTools visible={review} all={board.review} />}
        isEmpty={review.length === 0}
        emptyText={emptyText(text("没有等你验收的交付", "No drafts waiting for your review"))}
      >
        {review.map((card) => (
          <ReviewCard key={card.id} card={card} />
        ))}
      </Lane>

      <Lane
        title={text("阶段性完成 · done for now", "Done for now")}
        slug="completed"
        countLabel={label(completed.length, completedTotal)}
        colorToken="--status-done"
        capNote={
          completedTotal > board.completed.length
            ? text(`仅显示最近 ${board.completed.length} 条`, `Showing the latest ${board.completed.length} only`)
            : undefined
        }
        isEmpty={completed.length === 0}
        emptyText={emptyText(text("还没有验收过的交付", "Nothing accepted yet"))}
      >
        {completed.map((row) => (
          <DoneCard key={row.id} row={row} />
        ))}
      </Lane>

      <ArchiveStrip />
      {/* §21 多选操作条自 D42 起住 pages/BoardPage（.board-page 的末位、横贯看板底部），不进这条横排 */}
    </div>
  );
}
