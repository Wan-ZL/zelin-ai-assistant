// 卡面上的「怎样算办完」/「☐ 验收清单」紧凑块（D43；CONTRACT §54.1 第 2 项 2026-09-06 追记）。
// 原生 Cards.swift:1085-1099 把 DoD 放在提案卡收起态正文里（「§11 验收标准 — visible by default: approving the
// card approves this」）、:1858-1876 把 ☐ 清单永远渲染在待验收行上（空给兜底句）；D34 把两者搬进侧栏后卡面一条不剩。
// 自此卡面回到原生的位置，但只给紧凑形：前 FACE_DOD_MAX 条 + 「+N」，每条单行截断（hover 全文）——全文仍只住
// 详情侧栏（DetailFields），卡面摘要 ≠ 详情面：D34 的单一详情面不动，卡上不长任何开合、「+N」也不是按钮。
// 待验收面的方框反映 §64 评语：verdict = 建议验收（判官定义 = 清单 / 需求每条都有对应交付）→ ☑（title 点明是 AI 判断）；
// 需继续做 / 需要拍板 / 未知值 / 没评 → ☐（判官不逐条打分，不知道缺哪条，不猜）。只是展示：验收 / 打回仍只有按钮能按。
// 字级 = 原生 10 semibold 头 + 10 regular 条（--type-detail-subheading / --type-card-meta），spacing 1。
import { useI18n } from "../../i18n";
import type { CardAssessment } from "../../types";
import { VERDICTS } from "./VerdictChip";

/** 卡面最多露几条；其余折成「+N」（全文在详情侧栏） */
export const FACE_DOD_MAX = 3;

/** 待验收面：清单的 ☐ 是否按 §64 评语画成 ☑——只认逐字的「建议验收」 */
export function checklistChecked(assessment: CardAssessment | null | undefined): boolean {
  return assessment?.verdict === VERDICTS.accept;
}

interface DodFaceProps {
  items: unknown;
  /** dod = 提案面「怎样算办完：」编号清单（空不渲染）；checklist = 待验收面「验收清单——逐条对照：」☐ 清单（永远渲染，空给兜底句） */
  variant: "dod" | "checklist";
  assessment?: CardAssessment | null;
}

export function DodFace({ items, variant, assessment }: DodFaceProps) {
  const { text } = useI18n();
  const list = Array.isArray(items) ? items.filter((item): item is string => typeof item === "string" && item.trim() !== "") : [];
  if (variant === "dod" && list.length === 0) return null;
  const shown = list.slice(0, FACE_DOD_MAX);
  const more = list.length - shown.length;
  const checked = variant === "checklist" && checklistChecked(assessment);
  const heading = variant === "dod"
    ? text("怎样算办完：", "Definition of done:")
    : text("验收清单——逐条对照：", "Acceptance checklist:");
  const listTitle = checked
    ? text("AI 评语「建议验收」：清单每条都有对应交付——仍请你逐条对照", "AI verdict “Looks done”: every item has a matching delivery — still check each one yourself")
    : undefined;
  return (
    <div className={`card-dod is-${variant}${checked ? " is-ai-checked" : ""}`}>
      <p className="card-dod-heading">{heading}</p>
      {list.length === 0 ? (
        <p className="card-dod-empty">{text("该任务未定义验收标准，请自行判断", "No acceptance criteria defined — judge manually")}</p>
      ) : (
        <ul className="card-dod-list" title={listTitle}>
          {shown.map((item, index) => (
            // 原生一行一个 Text（"1. …" / "☐ …"）；记号单独一个节点只为给 ☑ 上 accent 色，读屏照读（不 aria-hidden）
            <li key={index} className="card-dod-item" title={item}>
              <span className="card-dod-mark">{variant === "dod" ? `${index + 1}.` : checked ? "☑" : "☐"}</span>
              {" "}{item}
            </li>
          ))}
          {more > 0 && (
            <li className="card-dod-more" title={text(`其余 ${more} 条在「展开详情 ▸」里`, `${more} more under Details ▸`)}>
              +{more}
              <span className="sr-only">{text(" 条，展开详情看全部", " more — see Details ▸")}</span>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
