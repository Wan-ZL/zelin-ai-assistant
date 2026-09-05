// 卡片动作「已落地」的清除谓词（原生 mac/Sources/PendingSweep.swift `cleared(by:)` 的 web 版；§21bis / §37 /
// §39.3 / §49 追记）。纯函数、零依赖：只看 (提交时的记录 × 当前看板快照)。
//
// 为什么不能用 generated_at：actd 每个 pass 结尾都重写 dashboard.json（act/actd.py run_once → write_dashboard，
// dashboard.py 每次 build 都盖新 generated_at），与卡片有没有动毫无关系——一条动作若落在本 pass 的 inbox drain
// 之后，本 pass 仍会重写看板，「generated_at 变了」就在什么都没发生时把按钮行解锁（可双击重复提交）。原生的
// 判据是每种动词各自的真信号（§39.3「generated_at bump 不清（§21bis 先例）」）：
//   - 换列动词（approve / accept / rework / defer / trash / reject / archive / restore / unarchive / raise /
//     abort_execution / stop_to_review / revert_review / done_external）→ 该 id 离开了提交时所在的列
//     （PendingSweep.swift:250-258 sticky hide 的释放条件；running 与 needs_input 合成一列，同原生 ids(in: .running)）；
//   - merge_apply / merge_dismiss → 该建议离开了 merge_suggestions（:289-292）；
//   - comment → 卡的 plan 指纹变了（actd fold_comment 往 plan 追加「修改方向」tag，:218-229）或 steers[] 变长
//     （运行中卡的 comment 是 steer 中继，§44.3-S）或卡离开了原列；
//   - set_title → 后台行的 display_title（空白归一后）等于提交的新名（:239-249）；
//   - answer_input → 卡离开 needs_input（:293-298）；
//   - merge_force → 每一张副卡都从所有列消失（:230-238，成为终态 merged）；
//   - pin → 回收站行 permanent 为真（:277-279）；
//   - split_note → 原 fold 行在 notes_text 里带上「已拆出」（:299-310）。
// 不认识的动词 / 提交时卡不在看板上 → 退回「新一版快照落地」（旧判据，聊胜于无）。
import type { Board } from "../../types";
import { parseFoldNotes } from "../detail/foldNotes";
import { cardHeadline } from "./cardHeadline";

/** 看板分区键；running 与 needs_input 在成员判定上视为同一列（原生 ListKind.running） */
export type LaneKey = "needs_approval" | "running" | "needs_input" | "review" | "completed" | "debt" | "trash" | "archived";

const LANES: readonly LaneKey[] = ["needs_approval", "review", "debt", "trash", "running", "needs_input", "completed", "archived"];

/** 「id 离开了提交时所在的列」即落地的动词（原生 hideSticky / beginReturn / addEcho 三族） */
export const LANE_VERBS: ReadonlySet<string> = new Set([
  "approve", "accept", "rework", "defer", "trash", "reject", "archive", "restore", "unarchive", "raise",
  "abort_execution", "stop_to_review", "revert_review", "done_external",
]);

type Row = Record<string, unknown> & { id: string };

/** 提交那一刻记下的全部判据（之后只与新快照比较，不再读别的状态） */
export interface PendingRecord {
  action: string | null;
  id: string | null;
  sourceLane: LaneKey | null;
  sentGeneratedAt: string | null;
  /** comment：提交时的 plan 指纹（plan 行以 \n 连）；卡不在看板 → null */
  planFingerprint: string | null;
  /** comment：提交时的 steers[] 长度 */
  steerCount: number;
  /** set_title：提交的新名（空白归一后） */
  title: string | null;
  /** merge_force：副卡（ids 去掉 primary） */
  secondaries: string[];
  /** split_note：被拆的 fold 行 ts */
  noteTs: string | null;
  /** 超时文案用的卡名前 20 字（原生 e.title.prefix(20)；卡不在看板 → id） */
  label: string;
}

/** §37：所有空白 run 折成单空格（actd `" ".join(title.split())` 同款；PendingSweep.normalizedTitle） */
export function normalizedTitle(s: string): string {
  return s.split(/\s+/).filter(Boolean).join(" ");
}

function rows(board: Board, lane: LaneKey): Row[] {
  const list = (board as unknown as Record<string, unknown>)[lane];
  return Array.isArray(list) ? (list.filter((r) => r && typeof r === "object" && typeof (r as Row).id === "string") as Row[]) : [];
}

/** 某列的 id 集合；running / needs_input 互为同一列（原生 ids(in: .running) = running ∪ needs_input） */
export function laneIds(board: Board, lane: LaneKey): Set<string> {
  if (lane === "running" || lane === "needs_input") {
    return new Set([...rows(board, "running"), ...rows(board, "needs_input")].map((r) => r.id));
  }
  return new Set(rows(board, lane).map((r) => r.id));
}

/** 当前哪一列持有这个 id（原生 currentList(of:)）；不在看板 → null */
export function currentLane(board: Board | null, id: string): LaneKey | null {
  if (!board) return null;
  for (const lane of LANES) if (rows(board, lane).some((r) => r.id === id)) return lane;
  return null;
}

function findRow(board: Board, id: string): Row | null {
  for (const lane of LANES) {
    const hit = rows(board, lane).find((r) => r.id === id);
    if (hit) return hit;
  }
  return null;
}

function planFingerprint(row: Row | null): string | null {
  if (!row) return null;
  const plan = row.plan;
  if (Array.isArray(plan)) return plan.map((p) => String(p)).join("\n");
  return typeof plan === "string" ? plan : "";
}

function steerCount(row: Row | null): number {
  return row && Array.isArray(row.steers) ? row.steers.length : 0;
}

/** 卡名（原生 Store.title(of:)：摘要优先面走 displaySummary，名字优先面走 rowTitle = display_title > name） */
function rowLabel(row: Row, lane: LaneKey): string {
  if (lane === "needs_approval" || lane === "debt" || lane === "trash" || lane === "archived") {
    return cardHeadline(row) || row.id;
  }
  const display = typeof row.display_title === "string" && row.display_title ? row.display_title : null;
  const name = typeof row.name === "string" && row.name ? row.name : null;
  return display ?? name ?? (cardHeadline(row) || row.id);
}

function clip20(s: string): string {
  return [...s].slice(0, 20).join("");
}

/** 提交那一刻的记录（useSubmit 在 setPending(true) 前调用；board 为 null 时记录退化为「等新快照」） */
export function recordPending(body: Record<string, unknown>, board: Board | null): PendingRecord {
  const action = typeof body.action === "string" ? body.action : null;
  const id = typeof body.id === "string" && body.id ? body.id : null;
  const sourceLane = id ? currentLane(board, id) : null;
  const row = board && id ? findRow(board, id) : null;
  const ids = Array.isArray(body.ids) ? body.ids.filter((x): x is string => typeof x === "string") : [];
  const primary = typeof body.primary === "string" ? body.primary : null;
  return {
    action,
    id,
    sourceLane,
    sentGeneratedAt: board?.generated_at ?? null,
    planFingerprint: planFingerprint(row),
    steerCount: steerCount(row),
    title: typeof body.title === "string" ? normalizedTitle(body.title) : null,
    secondaries: [...new Set(ids)].filter((x) => x !== primary),
    noteTs: typeof body.note_ts === "string" ? body.note_ts : null,
    label: row && sourceLane ? clip20(rowLabel(row, sourceLane)) : (id ?? ""),
  };
}

/** §21bis：一批强制合并落地 = 每张副卡都从所有列消失（成为终态 merged）；没有副卡（退化）→ 看新快照 */
export function forceMergeLanded(secondaries: readonly string[], board: Board, sentGeneratedAt: string | null): boolean {
  if (secondaries.length === 0) return board.generated_at !== sentGeneratedAt;
  return secondaries.every((id) => currentLane(board, id) === null);
}

/** 这条提交在当前快照里已经落地了吗（原生 cleared(by:) 的逐动词谓词） */
export function landed(rec: PendingRecord, board: Board): boolean {
  const bumped = board.generated_at !== rec.sentGeneratedAt;
  const { action, id } = rec;
  if (action === "merge_force") return forceMergeLanded(rec.secondaries, board, rec.sentGeneratedAt);
  if (!id) return bumped;
  if (action === "merge_apply" || action === "merge_dismiss") {
    return !(board.merge_suggestions ?? []).some((s) => s && s.id === id);
  }
  if (action === "set_title") {
    const row = findRow(board, id);
    const current = row && typeof row.display_title === "string" ? normalizedTitle(row.display_title) : null;
    return rec.title !== null && current === rec.title;
  }
  // §39：严格看 needs_input 本身（原生 blockedIDs = db.needs_input）——回答落地 = 卡恢复 working 回到 running
  if (action === "answer_input") return !rows(board, "needs_input").some((r) => r.id === id);
  if (action === "pin") {
    const row = rows(board, "trash").find((r) => r.id === id);
    return row?.permanent === true;
  }
  if (action === "comment") {
    const row = findRow(board, id);
    if (rec.sourceLane && !laneIds(board, rec.sourceLane).has(id)) return true;
    if (!row) return bumped;
    return planFingerprint(row) !== rec.planFingerprint || steerCount(row) > rec.steerCount;
  }
  if (action === "split_note") {
    const row = findRow(board, id);
    if (!row || typeof row.notes_text !== "string" || !rec.noteTs) return false;
    return parseFoldNotes(row.notes_text).folds.some((f) => f.ts === rec.noteTs && f.splitInto !== null);
  }
  if (LANE_VERBS.has(action ?? "")) {
    if (!rec.sourceLane) return bumped;
    return !laneIds(board, rec.sourceLane).has(id);
  }
  return bumped;
}

/** 180 s 兜底的逐动词诚实文案（原生 Store.swift sweepTimeouts / returnTimeoutText，逐字）。
 *  换列动词看卡还在不在：在 → 只是解锁；不在 → 写入很可能没落地，点名 actd。 */
export function timeoutNotice(rec: PendingRecord | null, board: Board | null, text: (zh: string, en: string) => string): string {
  const action = rec?.action ?? null;
  switch (action) {
    case "restore":
      return text("恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）", "Restore timed out — the card is back in the trash, try again (check that actd is running)");
    case "abort_execution":
      return text("退回提案超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）", "Discard & re-propose timed out — the card is still in Running, try again (check that actd is running)");
    case "revert_review":
      return text("退回待验收超时，卡片仍在「阶段性完成」列，可重试（检查 actd 是否在运行）", "Back-to-review timed out — the card is still in Done for now, try again (check that actd is running)");
    case "unarchive":
      return text("放回看板超时，卡片仍在「永久性完成」区，可重试（检查 actd 是否在运行）", "Put back timed out — the card is still in Done for good, try again (check that actd is running)");
    case "stop_to_review":
      return text("去待验收超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）", "Stop-to-review timed out — the card is still in Running, try again (check that actd is running)");
    case "comment":
      return text("修改意见超时未合并，卡片未变化，请重试（检查 actd 是否在运行）", "Comment timed out before merging — the card is unchanged, try again (check that actd is running)");
    case "set_title":
      return text("改名超时未确认，卡片名字未变化，请重试（检查 actd 是否在运行）", "Rename timed out — the card name is unchanged, try again (check that actd is running)");
    case "answer_input":
      return text("回答超时未确认，卡片仍在「需输入」，请重试（检查 actd 是否在运行）", "Answer timed out unconfirmed — the card still needs input, try again (check that actd is running)");
    case "merge_apply":
    case "merge_dismiss":
      return text("合并建议操作超时，卡片已恢复可操作（检查 actd 是否在运行）", "Merge-suggestion action timed out — the card is interactive again (check that actd is running)");
    case "merge_force":
      return text("强制合并未确认，卡片未变化，请重试（检查 actd 是否在运行）", "Force-merge never confirmed — nothing changed, try again (check that actd is running)");
    case "split_note":
      return text("「拆成新卡」超时未生效，原备注未变化，请重试（检查 actd 是否在运行）", "Split-into-card timed out — the note is unchanged, try again (check that actd is running)");
    case "raise":
      return text(`「${rec?.label ?? ""}」研究提案超时，请重试`, `Research proposal for "${rec?.label ?? ""}" timed out — try again`);
  }
  if (!rec?.id) {
    return text(
      "已提交，但 180 秒内看板未回流——backend 未确认，请检查 actd 是否在运行。",
      "Submitted, but the board never refreshed within 180s — backend unconfirmed; check that actd is running.",
    );
  }
  const stillExists = currentLane(board, rec.id) !== null;
  return stillExists
    ? text("后台响应超时，卡片已恢复可操作", "Backend timed out — the card is interactive again")
    : text(`「${rec.label}」已提交但后台超时未确认，请检查 actd 是否在运行`, `"${rec.label}" was submitted but the backend never confirmed — check that actd is running`);
}
