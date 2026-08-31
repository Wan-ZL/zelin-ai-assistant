// 运行中合并列的卡（BUILD-CONTRACT §2.2）：三个子形态共用一个组件——
//   blocked（needs_input 混排在最前，橙）：问题正文 + 「回答…」(answer_input) + 停止 fork；
//   queued（灰卡）：「排队中」chip + dispatch_error 原因 chip + 评论 + 停止 fork；
//   working：sheen 动效行（fork 的 task-processing 块）+ 评论 + 停止 fork。
// 停止 fork = Mac v0.21 两选弹窗：退回提案（abort_execution，destructive）/ 去待验收
// （stop_to_review）；两动词都允许 approved（排队卡）与 executing。无拖拽换状态（§0.8）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import type { TaskRow } from "../../types";
import { ANSWER_MAX_CODE_POINTS, cardAction, openCardDetail, useSubmit } from "./boardActions";
import { ForkDialog } from "./ForkDialog";
import { TextDialog } from "./TextDialog";

interface RunningCardProps {
  row: TaskRow;
  /** true = 来自 needs_input 分区（blocked，排最前） */
  isBlocked?: boolean;
}

type DialogKind = "none" | "stop" | "comment" | "answer";

export function RunningCard({ row, isBlocked = false }: RunningCardProps) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [dialog, setDialog] = useState<DialogKind>("none");

  const isQueued = row.state === "queued";
  const question = typeof row["question"] === "string" ? (row["question"] as string) : null;

  const act = (body: Record<string, unknown>) => {
    setDialog("none");
    void submit(body);
  };

  const cardClass = ["task-card", isQueued ? "is-queued" : "", isBlocked ? "is-blocked" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={cardClass} onDoubleClick={() => openCardDetail(row.id)}>
      <div className="card-id">{row.id}</div>
      <div className="card-title">{row.name}</div>
      {isBlocked ? (
        <>
          <div className="card-badges">
            <span className="chip chip-warning">{text("需输入", "Input")}</span>
            {row.resume_exhausted && (
              <span className="chip chip-danger">{text("恢复已放弃", "Auto-resume exhausted")}</span>
            )}
            {row.waiting_for && <span className="chip">{text(`等待：${row.waiting_for}`, `waiting: ${row.waiting_for}`)}</span>}
          </div>
          {question && <p className="card-line is-warning">{question}</p>}
        </>
      ) : isQueued ? (
        <>
          <div className="card-badges">
            <span className="chip">{text("排队中", "Queued")}</span>
            {row.dispatch_error && (
              // TODO(contract): wire 只有 dispatch_error 文本，没有「等 R-xx / 等预算」的
              // 结构化排队原因字段——原因 chip 先原样透传 dispatch_error，字段落定后再细分。
              <span className="chip chip-danger">{row.dispatch_error}</span>
            )}
          </div>
          {row.summary && <p className="card-summary">{row.summary}</p>}
        </>
      ) : (
        <>
          <div className="task-processing-row is-running">
            <span className="task-processing-ring" aria-hidden="true"><span /></span>
            <span className="task-processing-label">
              {typeof row["agent_name"] === "string" && row["agent_name"]
                ? (row["agent_name"] as string)
                : text("执行中", "Working")}
            </span>
          </div>
          {row.summary && <p className="card-summary">{row.summary}</p>}
          {row.last_error && <p className="card-line is-warning">{row.last_error}</p>}
        </>
      )}
      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
      ) : (
        <div className="card-actions">
          {isBlocked && (
            <button type="button" className="btn btn-primary" onClick={() => setDialog("answer")}>
              {text("回答…", "Answer…")}
            </button>
          )}
          {!isBlocked && (
            <button type="button" className="btn" onClick={() => setDialog("comment")}>
              {text("评论", "Comment")}
            </button>
          )}
          <button type="button" className="btn" onClick={() => setDialog("stop")}>
            {text("停止", "Stop")}
          </button>
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {dialog === "stop" && (
        <ForkDialog
          title={text(`停止 ${row.id}？`, `Stop ${row.id}?`)}
          body={text(
            "退回提案＝丢弃这次结果重来；去待验收＝留下它做的，我来检查",
            "Discard & re-propose throws this run away; Keep for review keeps what it made for you to check",
          )}
          choices={[
            {
              label: text("退回提案", "Discard & re-propose"),
              isDanger: true,
              onPick: () => act(cardAction(row.id, "abort_execution")),
            },
            {
              label: text("去待验收", "Keep for review"),
              onPick: () => act(cardAction(row.id, "stop_to_review")),
            },
          ]}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "comment" && (
        <TextDialog
          title={text("评论 / 补充方向", "Comment / steer")}
          body={text("文字会并入这张卡的记录，执行状态不变。", "Your note folds into the card; execution state is unchanged.")}
          placeholder={text("想补充什么？", "What to add?")}
          submitLabel={text("提交", "Submit")}
          onSubmit={(t) => act(cardAction(row.id, "comment", t))}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "answer" && (
        <TextDialog
          title={text("回答需输入", "Answer the blocked agent")}
          body={question ?? undefined}
          placeholder={text("你的回答（送回原会话）", "Your answer (delivered to the session)")}
          submitLabel={text("发送", "Send")}
          maxCodePoints={ANSWER_MAX_CODE_POINTS}
          onSubmit={(t) => act({ action: "answer_input", id: row.id, text: t })}
          onCancel={() => setDialog("none")}
        />
      )}
    </article>
  );
}
