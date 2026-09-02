// 第 3 节 Chips / labels：看板全部 chip 语义清单。chip 不是独立组件而是 class 约定
// （board.css .chip 家族）——本节按各卡组件的真实写法逐个复现（class + 文案 + 词表
// 全部同源：TYPE_LABELS / queuedReasonLabel / steer 词表），注脚标出宿主组件与 token。
// origin_trust / effective-tier 两项已立法未接线（vnext-amendments W17/M1）——诚实标注。
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import { queuedReasonLabel } from "../../steer";
import { SpecimenNote } from "./SpecimenNote";

interface ChipSpecimen {
  key: string;
  chips: Array<{ className: string; zh: string; en: string }>;
  noteZh: string;
  noteEn: string;
}

export function ChipsSection() {
  const { text, language } = useI18n();

  // queued_reason 走真解析函数（steer.ts），与 RunningCard/DetailFields 同一条词表
  // （waiting_budget chip retired v0.48.7，D9）
  const reasonCard = queuedReasonLabel({ kind: "waiting_card", blocking_id: "R-101" }, text) ?? "";
  const reasonSlot = queuedReasonLabel("concurrency", text) ?? "";

  const specimens: ChipSpecimen[] = [
    {
      key: "tier",
      chips: [
        { className: "chip chip-purple", zh: "T0", en: "T0" },
        { className: "chip chip-purple", zh: "T1 · 需要批准", en: "T1 · 需要批准" },
        { className: "chip chip-purple", zh: "T2 · 键入确认", en: "T2 · 键入确认" },
      ],
      noteZh: "tier chip（ProposalCard）：.chip-purple → --purple / --purple-soft（Mac systemPurple 粉紫章一比一）；文案 = tier + tier_hint",
      noteEn: "Tier chip (ProposalCard): .chip-purple → --purple / --purple-soft (one-to-one with Mac's pink-magenta systemPurple); label = tier + tier_hint",
    },
    {
      key: "deliver",
      chips: [{ className: "chip chip-purple", zh: "交付：聊天成稿", en: "Deliver: chat draft" }],
      noteZh: "交付 tag（ProposalCard delivery_mode=chat）：.chip-purple——owner 验收单「紫交付」+ §10 提取表拍板紫（源码 Badge 为 .blue，差异见第 1 节 ⚠️ 行）",
      noteEn: "Deliver tag (ProposalCard delivery_mode=chat): .chip-purple — ratified purple by the owner checklist and the §10 map (the source badge is .blue; see the flagged row in section 1)",
    },
    {
      key: "type",
      chips: ["engineering", "process", "research", "writing", "digest"].map((v) => ({
        className: "chip", zh: domainLabel(TYPE_LABELS, language, v), en: domainLabel(TYPE_LABELS, language, v),
      })),
      noteZh: "type chip（DebtCardItem）：中性 .chip + TYPE_LABELS 词表（未知枚举原样展示）",
      noteEn: "Type chip (DebtCardItem): neutral .chip + the TYPE_LABELS table (unknown enums render verbatim)",
    },
    {
      key: "deadline",
      chips: [
        { className: "chip", zh: "2026-09-20（剩 21 天）", en: "2026-09-20 (21d left)" },
        { className: "chip chip-danger chip-outline", zh: "2026-09-02（剩 2 天）", en: "2026-09-02 (2d left)" },
      ],
      noteZh: "deadline chip（ProposalCard）：days_left ≤ 3 升级 .chip-danger.chip-outline（红字描边档，Mac 紧急截止红字同 hue）",
      noteEn: "Deadline chip (ProposalCard): escalates to .chip-danger.chip-outline at days_left ≤ 3 (outline+red-text step, same hue as Mac's red urgent deadline)",
    },
    {
      key: "waiting",
      chips: [{ className: "chip chip-notice", zh: "等待：署名语言", en: "waiting: signature language" }],
      noteZh: "等待 chip（RunningCard blocked，waiting_for）：.chip-notice → --notice / --notice-soft（Mac .yellow 槽位，owner 验收单「黄等待」）",
      noteEn: "Waiting chip (RunningCard blocked, waiting_for): .chip-notice → --notice / --notice-soft (Mac's .yellow slot; owner checklist: yellow waiting)",
    },
    {
      key: "cost",
      chips: [{ className: "chip", zh: "$2", en: "$2" }],
      noteZh: "cost chip（ProposalCard）：show_cost && cost_usd 才渲染；中性 .chip",
      noteEn: "Cost chip (ProposalCard): renders only with show_cost && cost_usd; neutral .chip",
    },
    {
      key: "origin-trust",
      chips: [
        { className: "chip", zh: "来源：手打", en: "origin: hand" },
        { className: "chip chip-warning", zh: "来源：外部（永远等人批）", en: "origin: external (always needs approval)" },
      ],
      noteZh: "⚠️ origin_trust（M1 信任矩阵）：字段已立法、web 消费尚未接线——此为规划形态（hand 中性 / external 警告橙），非现役组件输出",
      noteEn: "⚠️ origin_trust (M1 trust matrix): field is ratified but not consumed on web yet — planned shape (hand neutral / external warning), not live component output",
    },
    {
      key: "effective-tier",
      chips: [{ className: "chip chip-warning", zh: "T1 → 生效 T2（外部来源）", en: "T1 → effective T2 (external origin)" }],
      noteZh: "⚠️ effective_tier 升档（W17）：外部来源卡按 T2 对待；投影字段已落，web 消费尚未接线——规划形态",
      noteEn: "⚠️ Effective-tier escalation (W17): external-origin cards are treated as T2; the projection field exists but web doesn't consume it yet — planned shape",
    },
    {
      key: "steer",
      chips: [
        { className: "chip chip-warning", zh: "方向修正排队 ×1", en: "Steer queued ×1" },
        { className: "chip chip-success", zh: "方向修正已送达 ×1", en: "Steer delivered ×1" },
        { className: "chip chip-danger", zh: "方向修正未送达 ×1", en: "Steer dropped ×1" },
      ],
      noteZh: "steer 回执（RunningCard，§M6.1 诚实三态）：排队 --warning / 送达 --success / 未送达 --danger",
      noteEn: "Steer receipts (RunningCard, §M6.1 honest tri-state): queued --warning / delivered --success / dropped --danger",
    },
    {
      key: "queued-reason",
      chips: [
        { className: "chip", zh: "排队中", en: "Queued" },
        { className: "chip", zh: reasonCard, en: reasonCard },
        { className: "chip", zh: reasonSlot, en: reasonSlot },
      ],
      noteZh: "queued + 排队原因 chip（RunningCard，§M6.2）：中性 .chip；文案出自 steer.ts queuedReasonLabel（本页即真函数输出）",
      noteEn: "Queued + reason chips (RunningCard, §M6.2): neutral .chip; labels come from steer.ts queuedReasonLabel (rendered here via the real function)",
    },
    {
      key: "disagreement",
      chips: [],
      noteZh: "分歧不是 chip 而是警示行（ProposalCard .card-line.is-warning → --warning）：",
      noteEn: "Disagreement is a warning line, not a chip (ProposalCard .card-line.is-warning → --warning):",
    },
    {
      key: "reraised",
      chips: [
        { className: "chip chip-warning", zh: "↩︎ 回锅", en: "↩︎ Returned" },
        { className: "chip chip-warning chip-quiet", zh: "被提×3", en: "Raised ×3" },
      ],
      noteZh: "回锅是状态警示（重档 .chip-warning）；被提×N 是 lineage 计数（.chip-quiet 安静档 → --warning-soft-quiet）——同 hue 两档底色（字重同原生 Badge 一律 semibold）",
      noteEn: "Returned is a state alert (heavy .chip-warning); Raised ×N is a lineage counter (.chip-quiet → --warning-soft-quiet) — one hue, two tints (weight stays semibold like the native Badge)",
    },
    {
      key: "lineage",
      chips: [
        { className: "zai-chip zai-chip--improves", zh: "↳ 改进自 R-88", en: "↳ Improves R-88" },
        { className: "zai-chip zai-chip--merged", zh: "已并入 R-42", en: "Merged into R-42" },
      ],
      noteZh: "lineage chips（宿主：详情抽屉 DetailFields，真 class）：↳ 改进 teal quiet（--accent-soft-quiet）/ 已并入 紫 quiet（--purple-soft-quiet）——Mac 卡面 badge 的抽屉版，位置差异见第 1 节 ⚠️ 行",
      noteEn: "Lineage chips (host: DetailFields in the drawer, real classes): improves teal quiet (--accent-soft-quiet) / merged purple quiet (--purple-soft-quiet) — the drawer version of Mac's card badges; placement difference flagged in section 1",
    },
  ];

  return (
    <div className="sg-grid">
      {specimens.map((s) => (
        <figure key={s.key} className="sg-specimen" data-chip-specimen={s.key}>
          {s.chips.length > 0 && (
            <div className="card-badges">
              {s.chips.map((chip, i) => (
                <span key={i} className={chip.className}>{text(chip.zh, chip.en)}</span>
              ))}
            </div>
          )}
          {s.key === "disagreement" && (
            <p className="card-line is-warning">{text("⚠ 有分歧：两条来源对交付格式说法不一致。", "⚠ Disagreement: sources conflict on the delivery format.")}</p>
          )}
          <SpecimenNote zh={s.noteZh} en={s.noteEn} />
        </figure>
      ))}
    </div>
  );
}
