// 卡片 → markdown 文档（「复制为 Markdown」）。纯函数，输出面向粘贴到笔记/聊天。
// 只序列化已知语义字段；未知字段不进成文（避免把内部字段泄进分享文本）。
import { displayId } from "../../cardId";
import type { CardDetail, CardSource } from "../../types";
import { parseFoldNotes } from "./foldNotes";

type TextFn = (chinese: string, english: string) => string;

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function section(out: string[], title: string, body: string[]) {
  if (body.length === 0) return;
  out.push(`## ${title}`, "", ...body, "");
}

export function cardToMarkdown(detail: CardDetail, text: TextFn): string {
  const out: string[] = [];
  const title = asString(detail.title) ?? asString(detail.name) ?? detail.id;
  out.push(`# ${title}`, "");

  const meta: string[] = [];
  // §60：ID 行给展示编号；主键不同时并排（粘贴出去的文本要能定位回卡）
  const shown = displayId(detail as { id: string; display_id?: unknown; work_id?: unknown });
  const metaPairs: Array<[string, unknown]> = [
    ["ID", shown === detail.id ? detail.id : `${shown} (${detail.id})`],
    [text("状态", "Lane"), detail.lane ?? detail.status],
    ["Tier", detail.tier],
    [text("类型", "Type"), detail.type],
    [text("交付方式", "Delivery"), detail.delivery_mode],
    [text("截止", "Deadline"), detail.deadline],
    [text("目标仓库", "Target repo"), detail.target_repo ?? detail.cwd],
  ];
  for (const [label, value] of metaPairs) {
    const rendered = asString(value) ?? (typeof value === "number" ? String(value) : null);
    if (rendered) meta.push(`- ${label}: ${rendered}`);
  }
  if (typeof detail.cost_usd === "number") meta.push(`- ${text("成本", "Cost")}: $${detail.cost_usd}`);
  if (meta.length) out.push(...meta, "");

  const summary = asString(detail.summary);
  if (summary) out.push(summary, "");

  section(out, text("计划", "Plan"), asStringList(detail.plan).map((step, index) => `${index + 1}. ${step}`));
  section(
    out,
    text("验收标准", "Definition of done"),
    asStringList(detail.dod ?? detail.definition_of_done).map((item) => `- [ ] ${item}`),
  );
  section(out, text("产出", "Outputs"), asStringList(detail.outputs).map((item) => `- ${item}`));

  const sources = Array.isArray(detail.sources) ? (detail.sources as CardSource[]) : [];
  section(out, text("来源引文", "Sources"), sources.flatMap((source) => {
    if (!source || typeof source !== "object") return [];
    const head = [source.who, source.channel, source.date].filter(Boolean).join(" · ");
    const lines = [`- ${head}`];
    if (typeof source.quote === "string" && source.quote) lines.push(`  - "${source.quote}"`);
    if (typeof source.ref === "string" && source.ref) lines.push(`  - ref: ${source.ref}`);
    return lines;
  }));

  const { folds, rest } = parseFoldNotes(detail.notes);
  section(out, text("并入记录", "Fold notes"), [
    ...folds.map((fold) => `- [${fold.kind}] ${fold.text}${fold.ts ? ` (@${fold.ts})` : ""}${fold.splitInto ? ` → ${fold.splitInto}` : ""}`),
    ...rest.map((line) => `- ${line}`),
  ]);

  const delivered = asString(detail.delivered_summary);
  section(out, text("交付总结", "Delivered summary"), delivered ? [delivered] : []);

  const finalDraft = asString(detail.final_draft);
  if (finalDraft) out.push(`## ${text("成稿", "Final draft")}`, "", finalDraft, "");

  return `${out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd()}\n`;
}
