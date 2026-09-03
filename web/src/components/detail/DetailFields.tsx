// 详情 tab：GET /api/cards/{id} 增补详情的全字段渲染。
// 已知语义字段给专属版式；未知字段落「其他字段」兜底区（wire add-only，
// 新字段先能看见再谈专属 UI）。本组件只读不写——动作按钮归卡片组件（A6）；唯一例外 =
// 「📎 折叠进来的信息」每行的「拆成新卡」（§38.2 split_note，原生 FoldNotesView 同位），因为它只
// 在这里有归属（note_ts 就是这一行）。
import { useState, type ReactNode } from "react";
import { domainLabel, LANE_LABELS, useI18n } from "../../i18n";
import { parseSteers, queuedReasonLabel, steerStatusLabel } from "../../steer";
import type { CardDetail, CardSource } from "../../types";
import { useSubmit } from "../board/boardActions";
import { copyText } from "./copyText";
import { parseFoldNotes } from "./foldNotes";

// 专属版式已覆盖的键——其余进「其他字段」兜底（渲染未知枚举值按字符串兜底，见 CONVENTIONS）
const KNOWN_KEYS = new Set([
  "id", "lane", "title", "name", "tier", "tier_hint", "state", "status", "hardness", "type",
  "delivery_mode", "deadline", "days_left", "repeated", "repeated_mentions",
  "cost_usd", "cost_estimate_usd", "show_cost", "green_sign", "green_sign_required", "processing",
  "summary", "plan", "dod", "definition_of_done", "outputs", "sources", "notes", "execution",
  "copy_cmd", "log", "cwd", "target_repo", "session_id", "short_id", "agent_name",
  "started_at", "dispatched_at", "accepted_at", "review_at", "created", "updated", "trashed_at",
  "permanent", "disagreement", "improvement_of", "reraised", "reraised_note", "waiting_for",
  "last_error", "dispatch_error", "resume_exhausted", "delivered_summary", "final_draft",
  "merged_into", "prev_status",
  // M6（§M6.1/§M6.2）：steer 回执与结构化排队原因有专属版式
  "queued_reason", "steers",
  // §60（D21）两段式编号：work_id 进 meta 行，display_id/id_kind 是抬头的展示口径
  "work_id", "display_id", "id_kind",
]);

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))) : [];
}

/** 投影里的时间戳是 epoch 秒（dispatched_at/review_at/…）；字符串时间原样展示 */
function formatWhen(value: unknown, locale: string): string | null {
  if (typeof value === "number" && Number.isFinite(value) && value > 1e9 && value < 1e11) {
    return new Date(value * 1000).toLocaleString(locale);
  }
  return str(value);
}

function CopyChip({ value, label }: { value: string; label: string }) {
  const { text } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="zai-detail-copy"
      onClick={() => {
        void copyText(value).then((ok) => {
          setCopied(ok);
          if (ok) window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? text("已复制", "Copied") : label}
    </button>
  );
}

/** §38.2 拆成新卡：{action:"split_note", id, note_ts}（legacy 无 ts 的 fold 行不可拆，原生同）。
 *  动词 / 忙态词逐字镜像原生 FoldNotesView（拆成新卡 / 拆分中…，§54.4）。 */
function SplitNoteButton({ cardId, noteTs }: { cardId: string; noteTs: string }) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  return (
    <>
      {pending ? (
        <span className="zai-detail-dim">{text("拆分中…", "Splitting…")}</span>
      ) : (
        <button type="button" className="zai-detail-copy"
          title={text("这条信息不该折在这张卡里？拆出去单独成卡（原记录保留）", "Folded into the wrong card? Split it out (the origin line is kept)")}
          onClick={() => void submit({ action: "split_note", id: cardId, note_ts: noteTs })}>
          {text("拆成新卡", "Split into card")}
        </button>
      )}
      {error && <span className="zai-detail-callout zai-detail-callout--danger">{error}</span>}
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="zai-detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export interface DetailFieldsProps {
  detail: CardDetail;
}

export function DetailFields({ detail }: DetailFieldsProps) {
  const { text, locale, language } = useI18n();

  const chips: Array<{ key: string; label: string; tone?: string }> = [];
  const lane = str(detail.lane);
  if (lane) {
    chips.push({ key: "lane", label: domainLabel(LANE_LABELS, language, lane), tone: `lane-${lane}` });
  }
  for (const key of ["tier", "state", "status", "type", "hardness", "delivery_mode"] as const) {
    const value = str(detail[key]);
    if (value) chips.push({ key, label: key === "delivery_mode" ? `${text("交付", "delivery")}: ${value}` : value });
  }
  if (detail.green_sign === true || detail.green_sign_required === true) {
    chips.push({ key: "green_sign", label: text("需要绿灯", "Green light required"), tone: "warn" });
  }
  if (detail.processing === true) chips.push({ key: "processing", label: text("生成中…", "Processing…"), tone: "warn" });
  if (detail.permanent === true) chips.push({ key: "permanent", label: text("已永久删除", "Permanently deleted"), tone: "danger" });
  // lineage chips（Mac 卡面 badge 同源：↳ 改进 teal / 已并入 紫）——quiet tint 档，排在状态 chip 之后
  const improvementOf = str(detail.improvement_of);
  if (improvementOf) {
    chips.push({ key: "improvement_of", label: text(`↳ 改进自 ${improvementOf}`, `↳ Improves ${improvementOf}`), tone: "improves" });
  }
  const mergedInto = str(detail.merged_into);
  if (mergedInto) {
    chips.push({ key: "merged_into", label: text(`已并入 ${mergedInto}`, `Merged into ${mergedInto}`), tone: "merged" });
  }

  const warnings: Array<{ key: string; label: string; value: string; tone: "warn" | "danger" }> = [];
  const warnDefs: Array<[string, string, string, "warn" | "danger"]> = [
    ["disagreement", "分歧", "Disagreement", "warn"],
    ["waiting_for", "等待输入", "Waiting for", "warn"],
    ["reraised_note", "再提名说明", "Re-raise note", "warn"],
    ["last_error", "最近错误", "Last error", "danger"],
    ["dispatch_error", "派发错误", "Dispatch error", "danger"],
  ];
  for (const [key, zh, en, tone] of warnDefs) {
    const value = str(detail[key]);
    if (value) warnings.push({ key, label: text(zh, en), value, tone });
  }
  if (detail.resume_exhausted === true) {
    warnings.push({ key: "resume_exhausted", label: text("重试", "Retries"), value: text("自动重试已用尽", "Automatic resume attempts exhausted"), tone: "danger" });
  }

  const meta: Array<[string, string]> = [];
  // §60：工作编号（有才显示）+ 主键——抬头已给展示编号，这里把两者都留在字段面
  const workId = str(detail.work_id);
  if (workId) meta.push([text("工作编号", "Work number"), workId]);
  const primaryKey = str(detail.id);
  if (primaryKey && workId && primaryKey !== workId) meta.push([text("主键", "Card key"), primaryKey]);
  const deadline = str(detail.deadline);
  if (deadline) {
    const daysLeft = typeof detail.days_left === "number" ? text(`（剩 ${detail.days_left} 天）`, ` (${detail.days_left}d left)`) : "";
    meta.push([text("截止", "Deadline"), `${deadline}${daysLeft}`]);
  }
  const repeated = detail.repeated ?? detail.repeated_mentions;
  if (typeof repeated === "number" && repeated > 1) meta.push([text("重复提及", "Mentions"), `×${repeated}`]);
  // 结构化排队原因（§M6.2）：queued 卡「排队中 · 等 R-xx / 等预算」的详情行
  const queuedReason = queuedReasonLabel(detail.queued_reason, text);
  if (queuedReason) meta.push([text("排队原因", "Queued because"), queuedReason]);
  const cost = detail.cost_usd ?? detail.cost_estimate_usd;
  if (detail.show_cost !== false && typeof cost === "number") meta.push([text("成本", "Cost"), `$${cost}`]);
  const agent = str(detail.agent_name);
  if (agent) meta.push([text("执行代号", "Agent"), agent]);
  const session = str(detail.short_id) ?? str(detail.session_id);
  if (session) meta.push(["Session", session]);
  const repo = str(detail.target_repo) ?? str(detail.cwd);
  if (repo) meta.push([text("工作目录", "Workdir"), repo]);
  const timeDefs: Array<[string, string, unknown]> = [
    [text("创建", "Created"), "created", detail.created],
    [text("更新", "Updated"), "updated", detail.updated],
    [text("起跑", "Started"), "started_at", detail.started_at],
    [text("派发", "Dispatched"), "dispatched_at", detail.dispatched_at],
    [text("接受", "Accepted"), "accepted_at", detail.accepted_at],
    [text("交付", "Delivered"), "review_at", detail.review_at],
    [text("入回收站", "Trashed"), "trashed_at", detail.trashed_at],
  ];
  for (const [label, , value] of timeDefs) {
    const rendered = formatWhen(value, locale);
    if (rendered) meta.push([label, rendered]);
  }
  const log = str(detail.log);

  const steers = parseSteers(detail.steers);
  const plan = strList(detail.plan);
  const dod = strList(detail.dod ?? detail.definition_of_done);
  const outputs = strList(detail.outputs);
  const sources = Array.isArray(detail.sources) ? (detail.sources as CardSource[]) : [];
  const { folds, rest } = parseFoldNotes(detail.notes);
  const execution = detail.execution && typeof detail.execution === "object" && !Array.isArray(detail.execution)
    ? Object.entries(detail.execution as Record<string, unknown>)
    : [];
  const copyCmd = str(detail.copy_cmd);
  const unknown = Object.entries(detail).filter(([key, value]) => !KNOWN_KEYS.has(key) && value != null);

  return (
    <div className="zai-detail-fields">
      {chips.length > 0 && (
        <div className="zai-detail-chips">
          {chips.map((chip) => (
            <span key={chip.key} className={`zai-chip${chip.tone ? ` zai-chip--${chip.tone}` : ""}`}>{chip.label}</span>
          ))}
        </div>
      )}

      {warnings.map((warning) => (
        <p key={warning.key} className={`zai-detail-callout zai-detail-callout--${warning.tone}`}>
          <strong>{warning.label}</strong> {warning.value}
        </p>
      ))}

      {str(detail.summary) && <p className="zai-detail-summary">{str(detail.summary)}</p>}
      {str(detail.delivered_summary) && (
        <Section title={text("交付总结", "Delivered summary")}>
          <p className="zai-detail-summary">{str(detail.delivered_summary)}</p>
        </Section>
      )}

      {meta.length > 0 && (
        <dl className="zai-detail-meta">
          {meta.map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      )}

      {plan.length > 0 && (
        <Section title={text("计划", "Plan")}>
          <ol>{plan.map((step, index) => <li key={index}>{step}</li>)}</ol>
        </Section>
      )}
      {dod.length > 0 && (
        <Section title={text("验收标准", "Definition of done")}>
          <ul className="zai-detail-dod">{dod.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </Section>
      )}
      {outputs.length > 0 && (
        <Section title={text("产出", "Outputs")}>
          <ul>{outputs.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </Section>
      )}

      {sources.length > 0 && (
        <Section title={text("来源引文", "Sources")}>
          {sources.map((source, index) => (
            <blockquote key={index} className="zai-detail-source">
              <p className="zai-detail-source-quote">{str(source?.quote) ?? text("（无引文）", "(no quote)")}</p>
              <footer>
                {[str(source?.who), str(source?.channel), str(source?.date)].filter(Boolean).join(" · ")}
                {str(source?.ref) && <span className="zai-detail-source-ref"> · {source.ref}</span>}
              </footer>
            </blockquote>
          ))}
        </Section>
      )}

      {folds.length > 0 && (
        // 原生 FoldNotesView（§38）：标题「📎 折叠进来的信息」，每行 💬（quick）/ 📡（radar）+ 正文 +
        // 尾部「已拆出 R-yyy」章 / 拆分中… / 「拆成新卡」——词逐字镜像（§54.4）
        <Section title={text("📎 折叠进来的信息", "📎 Folded-in updates")}>
          <ul className="zai-detail-folds">
            {folds.map((fold, index) => (
              <li key={`fold-${index}`}>
                <span aria-hidden="true">{fold.kind === "quick" ? "💬" : "📡"}</span> {fold.text}
                {fold.ts && <span className="zai-detail-dim"> @{fold.ts}</span>}
                {fold.splitInto && <> <span className="zai-chip">{text(`已拆出 ${fold.splitInto}`, `Split → ${fold.splitInto}`)}</span></>}
                {fold.ts && !fold.splitInto && <> <SplitNoteButton cardId={detail.id} noteTs={fold.ts} /></>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {rest.length > 0 && (
        <Section title={text("备注", "Notes")}>
          <ul className="zai-detail-folds">
            {rest.map((line, index) => <li key={`rest-${index}`}>{line}</li>)}
          </ul>
        </Section>
      )}

      {steers.length > 0 && (
        // steer 回执历史（§M6.1）：每行 = 状态 chip + 正文 + 排队 ts（+ 送达 ts）。
        // 诚实展示：dropped（注入 3 次失败放弃）标红——绝不把未送达装成已生效。
        <Section title={text("方向修正", "Steer notes")}>
          <ul className="zai-detail-folds">
            {steers.map((note, index) => (
              <li key={`steer-${index}`}>
                <span
                  className={`zai-chip${
                    note.status === "dropped" ? " zai-chip--danger"
                    : note.status === "delivered" ? "" : " zai-chip--warn"}`}
                >
                  {steerStatusLabel(note.status, text)}
                </span>{" "}
                {typeof note.text === "string" ? note.text : ""}
                <span className="zai-detail-dim"> @{note.ts}</span>
                {typeof note.delivered_at === "string" && note.delivered_at !== "" && (
                  <span className="zai-detail-dim"> · {text("送达于", "delivered at")} {note.delivered_at}</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(copyCmd || log) && (
        <Section title={text("会话", "Session")}>
          {copyCmd && (
            <div className="zai-detail-cmd">
              <code>{copyCmd}</code>
              <CopyChip value={copyCmd} label={text("复制命令", "Copy command")} />
            </div>
          )}
          {log && <p className="zai-detail-dim">{text("日志：", "Log: ")}{log}</p>}
        </Section>
      )}

      {execution.length > 0 && (
        <Section title={text("执行元数据", "Execution")}>
          <dl className="zai-detail-meta">
            {execution.map(([key, value]) => (
              <div key={key}><dt>{key}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>
            ))}
          </dl>
        </Section>
      )}

      {unknown.length > 0 && (
        <Section title={text("其他字段", "Other fields")}>
          <dl className="zai-detail-meta">
            {unknown.map(([key, value]) => (
              <div key={key}><dt>{key}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>
            ))}
          </dl>
        </Section>
      )}
    </div>
  );
}
