// §64（issue #128）AI 完成度评语章 + 一句话摘要行——只是建议，永不代人按验收/打回。
//   VerdictChip：三态章（建议验收 绿 / 需继续做 橙 / 需要拍板 紫），点一下展开一行理由
//     （有验收清单时引用未满足条目；没有清单时理由兼作建议的验收要点）。词表开放：
//     server 送来未知值就按原文中性 chip 渲染，不猜。
//   AssessmentSummaryLine：卡面一句白话（原生 delivered_summary 行同款 11 次级、单行截断、hover 全文）。
// 全部只读；不 import store。
import { useState } from "react";
import { useI18n } from "../../i18n";
import type { CardAssessment } from "../../types";

/** wire 词表（act/lib/card_summary.py VERDICTS 逐字镜像） */
export const VERDICTS = {
  accept: "建议验收",
  continue: "需继续做",
  decide: "需要拍板",
} as const;

const CHIP_CLASS: Record<string, string> = {
  [VERDICTS.accept]: "chip-success",
  [VERDICTS.continue]: "chip-warning",
  [VERDICTS.decide]: "chip-purple",
};

export function verdictLabel(verdict: string, text: (zh: string, en: string) => string): string {
  if (verdict === VERDICTS.accept) return text("建议验收", "Looks done");
  if (verdict === VERDICTS.continue) return text("需继续做", "Needs more work");
  if (verdict === VERDICTS.decide) return text("需要拍板", "Needs your call");
  return verdict;
}

function nonEmpty(v: unknown): v is string {
  return typeof v === "string" && v.trim() !== "";
}

export function VerdictChip({ assessment }: { assessment: CardAssessment | null | undefined }) {
  const { text } = useI18n();
  const [open, setOpen] = useState(false);
  const verdict = assessment?.verdict;
  if (!nonEmpty(verdict)) return null;
  const reason = nonEmpty(assessment?.verdict_reason) ? assessment.verdict_reason : null;
  const tone = CHIP_CLASS[verdict] ?? "";
  const hint = text("AI 判断，仅供参考——点一下看理由", "AI opinion, advice only — tap for the reason");
  return (
    <>
      <button
        type="button"
        className={`chip verdict-chip ${tone}`.trim()}
        title={reason ?? hint}
        aria-expanded={open}
        aria-label={`${text("AI 评语：", "AI verdict: ")}${verdictLabel(verdict, text)}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {text("AI · ", "AI · ")}{verdictLabel(verdict, text)}
      </button>
      {open && (
        <p className="card-line is-body verdict-reason" role="note">
          {reason ?? text("（AI 没给出理由）", "(no reason given)")}
        </p>
      )}
    </>
  );
}

/** 卡面一句白话摘要；没有就渲染 fallback（阶段性完成卡回落到 delivered_summary） */
export function AssessmentSummaryLine({ assessment, fallback }: { assessment: CardAssessment | null | undefined; fallback?: unknown }) {
  const { text } = useI18n();
  const line = nonEmpty(assessment?.summary) ? assessment.summary : nonEmpty(fallback) ? fallback : null;
  if (!line) return null;
  const isAi = nonEmpty(assessment?.summary);
  return (
    <p
      className={`card-summary-line${isAi ? " is-ai" : ""}`}
      title={isAi ? `${text("AI 摘要：", "AI summary: ")}${line}` : line}
    >
      {line}
    </p>
  );
}
