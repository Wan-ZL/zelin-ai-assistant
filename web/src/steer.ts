// steer / queued_reason 投影字段的防御性解析（M6）。
// wire 真源 = docs/design/vnext-amendments.md §M6（ratification-ready；
// scripts/demo_seed.py 的 validator 与本文件词表对齐，改词表两边同步）。
// 生产端 actd 尚未接线——字段缺席/形状不合时所有函数原样降级，UI 不渲染 steer 面。
// 铁律同 i18n 词表：开放枚举，未知值原样展示，绝不因新值崩渲染。
import type { QueuedReason, SteerNote } from "./types";

type Text = (chinese: string, english: string) => string;

/** steers 数组防御性解析：只收带 string ts 的对象项，其余静默丢弃 */
export function parseSteers(value: unknown): SteerNote[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is SteerNote =>
      typeof item === "object" && item !== null && !Array.isArray(item)
      && typeof (item as { ts?: unknown }).ts === "string",
  );
}

export interface SteerSummary {
  queued: number;
  delivered: number;
  dropped: number;
}

/** 按诚实三态计数；未知 status 按 queued 兜底（最保守——不谎报送达） */
export function summarizeSteers(notes: SteerNote[]): SteerSummary {
  const summary: SteerSummary = { queued: 0, delivered: 0, dropped: 0 };
  for (const note of notes) {
    if (note.status === "delivered") summary.delivered += 1;
    else if (note.status === "dropped") summary.dropped += 1;
    else summary.queued += 1;
  }
  return summary;
}

/** steer 行状态 → 双语标签（未知 status 原样展示） */
export function steerStatusLabel(status: unknown, text: Text): string {
  switch (status) {
    case "delivered":
      return text("已送达", "Delivered");
    case "dropped":
      return text("未送达", "Dropped");
    case "queued":
      return text("排队中", "Queued");
    default:
      return typeof status === "string" && status !== "" ? status : text("排队中", "Queued");
  }
}

/**
 * queued_reason → 单行标签（「排队中 · 等 R-xx / 等并发位」chip 与详情行共用）。
 * 双词表兼容（canonical 由 integrator 终裁，见 §M6.2）：
 * - 结构化形 {kind, detail?, blocking_id?}，kind = waiting_card / concurrency
 *   （demo_seed QUEUED_REASON_KINDS 对齐）；
 * - 扁平 token 形（act/lib/policy.py QUEUED_REASONS：dependency / concurrency）
 *   ——M1.c 的 dashboard 投影直出形，同表翻译；
 * - 未知 kind/token 按 detail/原文原样展示（开放枚举不崩渲染）；解析不出 → null。
 *   waiting_budget / budget retired v0.49（CONTRACT §51，owner decision D9）：
 *   不再有专属文案，旧快照里若还出现就走这条原文降级路径。
 */
export function queuedReasonLabel(value: unknown, text: Text): string | null {
  let kind: string | null = null;
  let detail: string | null = null;
  let blocking: string | null = null;
  if (typeof value === "string") {
    kind = value.trim() !== "" ? value : null;
  } else if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const reason = value as QueuedReason;
    kind = typeof reason.kind === "string" && reason.kind !== "" ? reason.kind : null;
    detail = typeof reason.detail === "string" && reason.detail.trim() !== "" ? reason.detail : null;
    blocking = typeof reason.blocking_id === "string" && reason.blocking_id !== "" ? reason.blocking_id : null;
  }
  switch (kind) {
    case null:
      return null;
    case "waiting_card":
    case "dependency":
      return text(`等 ${blocking ?? "前置卡"}`, `waiting on ${blocking ?? "another card"}`);
    case "concurrency":
      return text("等并发位", "waiting on a run slot");
    default:
      return detail ?? kind;
  }
}

/** POST /api/actions 响应的 steer 标注（server add-only 键 steer:true）；形状不合一律 false */
export function steerAcknowledged(response: unknown): boolean {
  return typeof response === "object" && response !== null
    && (response as { steer?: unknown }).steer === true;
}
