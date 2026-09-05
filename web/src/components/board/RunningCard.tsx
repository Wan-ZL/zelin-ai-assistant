// 运行中合并列的卡（BUILD-CONTRACT §2.2）：三个子形态共用一个组件——
//   blocked（needs_input 混排在最前，橙）：v0.48.8（#119）起只剩 §4 派发刹车行
//   （dispatch_halted），无「回答」入口——受阻会话由 actd 收割进待验收；
//   queued（灰卡）：「排队中」chip + 排队原因 chip + 派发失败一句（+ 让 AI 修）+ 评论 + 停止 fork；
//   working：状态章 + 运行时长 + repo 章 + 单击复制指令 行 + steer 回执 + 评论/回答 + 停止 fork。
// 停止 fork = Mac v0.21 两选弹窗：退回提案（abort_execution，destructive）/ 去待验收
// （stop_to_review）；两动词都允许 approved（排队卡）与 executing。无拖拽换状态（§0.8）。
// 出错的卡（原生 TaskRow.errorLine，§25）：错误一句——排队卡看 dispatch_error(_id)、其余看 last_error(_id)；
// failure id 在 §25 失败目录（store.failures ← GET /api/failures）里时卡面说人话、原文降到 hover / 详情，
// 否则原文——+ 目录的对症一键（FailureActionButton，标签逐字原生 actionLabel；未知 id 不装）+ 「让 AI 修」
// （POST /api/ai-fix，起 act.ai_fix 修复会话）+ 「回答…」（= comment 即 steer：answer_input 已退役，方向修正经 §44.3 中继）。
// 错误全文 / summary / 📋 要做什么 / 怎样算办完 / 日志 / 指令 / 会话 ID / agents 列表名 住右侧详情侧栏
// （「展开详情 ▸」打开，D34；DetailFields 渲染）。
import { useState } from "react";
import { displayId } from "../../cardId";
import { useI18n, type Language } from "../../i18n";
import { parseSteers, queuedReasonLabel, summarizeSteers } from "../../steer";
import { useAppState } from "../../store";
import type { FailureCatalog, TaskRow } from "../../types";
import { FailureActionButton } from "../settings/failureAction";
import { cardAction, resumeCommand, useSubmit, pendingNote } from "./boardActions";
import { AiFixButton, CardHead, CardSurface, CopyCommandLine, DetailsToggle, ErrorLine, MergeStateChip, RelativeTime, RepoChip, TerminalButton } from "./cardChrome";
import { ForkDialog } from "./ForkDialog";
import { TextDialog } from "./TextDialog";

interface RunningCardProps {
  row: TaskRow;
  /** true = 来自 needs_input 分区（blocked，排最前） */
  isBlocked?: boolean;
}

type DialogKind = "none" | "stop" | "comment" | "answer";

/** task.state → 大白话（原生 Cards.swift stateLabel；未知值原样） */
export function stateLabel(state: string, text: (zh: string, en: string) => string): string {
  switch (state) {
    case "queued": return text("排队中", "Queued");
    case "dispatched": return text("已派发", "Dispatched");
    case "working": case "running": case "executing": case "active": case "busy": case "in_progress":
      return text("执行中", "Working");
    case "blocked": case "waiting": case "needs_input": case "paused": case "waiting_for_input":
      return text("受阻", "Blocked");
    case "review": return text("待验收", "In review");
    case "delivered": return text("已交付", "Delivered");
    case "done": case "completed": case "finished": case "exited": case "complete": case "success":
      return text("已完成", "Done");
    case "idle": return text("空闲", "Idle");
    case "unknown": return text("状态未知", "Unknown");
    default: return state;
  }
}

/**
 * §25 卡片错误行的人话（原生 `FailureCatalog.message(failureID)`）：failure id 在目录里 → 当前语言那句；
 * 没 id / 目录里没有 / 目录还没回（`null`）→ null，卡面照旧原文。目录 = server-owned `GET /api/failures`（防腐 #10：web 不抄第二份句子）。
 */
export function failureSentence(failureId: unknown, catalog: FailureCatalog | null, lang: Language): string | null {
  if (typeof failureId !== "string" || !failureId) return null;
  const entry = catalog?.failures[failureId];
  const sentence = entry?.[lang];
  return typeof sentence === "string" && sentence !== "" ? sentence : null;
}

export function RunningCard({ row, isBlocked = false }: RunningCardProps) {
  const { text, language } = useI18n();
  const { failures } = useAppState();
  const { pending, pendingAction, error, steerQueued, submit } = useSubmit();
  const [dialog, setDialog] = useState<DialogKind>("none");

  const isQueued = row.state === "queued";
  const question = typeof row.question === "string" ? row.question : null;
  const title = typeof row.display_title === "string" && row.display_title ? row.display_title : row.name;
  // steer / queued_reason 投影字段（vnext-amendments §M6）——缺席即不渲染
  const queuedReason = queuedReasonLabel(row.queued_reason, text);
  const steer = summarizeSteers(parseSteers(row.steers));
  const hasSteers = steer.queued > 0 || steer.delivered > 0 || steer.dropped > 0;
  // 错误文本：排队卡看 dispatch_error，其余看 last_error（原生 TaskRow.errorText）
  const errorText = isQueued ? row.dispatch_error : row.last_error;
  const hasError = typeof errorText === "string" && errorText !== "";
  // §25 分类 id 与原文同源伴随（dashboard.py：queued 项 dispatch_error_id / 其余 last_error_id；未分类 = null）
  const failureId = isQueued ? row.dispatch_error_id : row.last_error_id;
  const sentence = failureSentence(failureId, failures, language);
  const cmd = resumeCommand(row);
  // 「回答…」只给执行中出错的卡（有会话可 steer）；排队/刹车行没有会话
  const showsAnswer = !isBlocked && !isQueued && hasError;

  const act = (body: Record<string, unknown>) => {
    setDialog("none");
    void submit(body);
  };

  const cardClass = [isQueued ? "is-queued" : "", isBlocked ? "is-blocked" : "", hasError ? "has-error" : ""]
    .filter(Boolean)
    .join(" ");
  // a11y（issue #8）：状态词进 aria-label——色点只是装饰，状态不靠颜色
  const stateWord = isBlocked ? text("需输入", "Needs input") : isQueued ? text("排队中", "Queued") : stateLabel(row.state, text);

  return (
    <CardSurface cardId={row.id} className={cardClass} label={`${stateWord} · ${title}`} selectable>
      <CardHead
        card={row}
        title={title}
        isMuted={isQueued}
        leading={<span className={`card-dot ${isBlocked ? "is-blocked" : isQueued ? "is-queued" : "is-running"}`} aria-hidden="true" />}
      />
      {isBlocked ? (
        <>
          <div className="card-badges">
            <MergeStateChip cardId={row.id} />
            <span className="chip chip-warning">{text("需输入", "Input")}</span>
            {row.resume_exhausted && (
              <span className="chip chip-danger">{text("恢复已放弃", "Auto-resume exhausted")}</span>
            )}
            {/* §4 派发风暴刹车：approved 卡停止重试后投影到这里（无会话，不是 agent 在提问） */}
            {row.dispatch_halted && (
              <span className="chip chip-danger">
                {text(`派发已停止 ×${row.dispatch_attempts ?? 0}`, `Launch stopped ×${row.dispatch_attempts ?? 0}`)}
              </span>
            )}
            {/* 等待 chip = Mac .yellow notice（owner 验收单：黄等待）——--notice 槽位 */}
            {row.waiting_for && <span className="chip chip-notice"><span className="card-detail-label">{text("等待: ", "Waiting: ")}</span><span>{String(row.waiting_for)}</span></span>}
            <RepoChip path={row.cwd} />
          </div>
          {question && <p className="card-line is-warning is-body">{question}</p>}
        </>
      ) : isQueued ? (
        <div className="card-badges">
          <MergeStateChip cardId={row.id} />
          <span className="chip">{text("排队中", "Queued")}</span>
          {/* 结构化排队原因（「等 R-xx / 等并发位」）——§M6.2 字段；过渡期字符串形也兼容，缺席不渲染 */}
          {queuedReason && <span className="chip">{queuedReason}</span>}
        </div>
      ) : (
        <>
          <div className="card-badges">
            <MergeStateChip cardId={row.id} />
            {/* 原生 TaskRow meta：状态章（accent 蓝）· 已交付过·再运行（青）· 运行时长 · repo 章。
                working 由下方 sheen 行表达（执行中 / agents 列表名），只有非常规状态（idle / unknown /
                review-active…）才出状态章——同一信息不在卡面说两遍 */}
            {row.state === "review-active"
              ? <span className="chip chip-accent">{text("会话有新活动", "Session active")}</span>
              : row.state !== "working" && <span className="chip chip-info">{stateLabel(row.state, text)}</span>}
            {row.from_review && <span className="chip chip-accent">{text("已交付过·再运行", "Delivered · re-running")}</span>}
            <RelativeTime epoch={row.started_at ?? row.dispatched_at} />
            {row.from_review && <RelativeTime epoch={row.accepted_at} prefix={text("验收于 ", "accepted ")} />}
            {row.waiting_for && <span className="chip chip-notice"><span className="card-detail-label">{text("等待: ", "Waiting: ")}</span><span>{String(row.waiting_for)}</span></span>}
            <RepoChip path={row.cwd} />
          </div>
          <div className="task-processing-row is-running">
            <span className="task-processing-ring" aria-hidden="true"><span /></span>
            <span className="task-processing-label">
              {typeof row.agent_name === "string" && row.agent_name ? row.agent_name : text("执行中", "Working")}
            </span>
          </div>
          {/* steer 回执 chips（诚实三态：排队/送达/未送达）——投影 steers[] 驱动 */}
          {hasSteers && (
            <div className="card-badges">
              {steer.queued > 0 && (
                <span className="chip chip-warning">
                  {text(`方向修正排队 ×${steer.queued}`, `Steer queued ×${steer.queued}`)}
                </span>
              )}
              {steer.delivered > 0 && (
                <span className="chip chip-success">
                  {text(`方向修正已送达 ×${steer.delivered}`, `Steer delivered ×${steer.delivered}`)}
                </span>
              )}
              {steer.dropped > 0 && (
                <span className="chip chip-danger">
                  {text(`方向修正未送达 ×${steer.dropped}`, `Steer dropped ×${steer.dropped}`)}
                </span>
              )}
            </div>
          )}
          <CopyCommandLine cmd={cmd} />
        </>
      )}
      {/* §25 错误一句（红）：排队卡的派发失败 / 执行卡的错误；分类 id 在目录里 → 人话，原文 hover 可见，详情侧栏有全文 + 复制 */}
      {!isBlocked && hasError && (
        <ErrorLine prefix={isQueued ? text("派发失败：", "Dispatch failed: ") : text("错误：", "Error: ")} raw={errorText} sentence={sentence} />
      )}
      {pending ? (
        <p className="card-pending-note">
          {steerQueued
            ? text("已提交 · 方向修正排队中…", "Submitted · steer queued…")
            : pendingNote(pendingAction, text)}
        </p>
      ) : (
        <div className="card-actions">
          {/* 出错的卡（原生 errorLine 的按钮行，顺序同原生）：§25 目录的对症一键（未知 id 渲染 null）→ 让 AI 修 = 起本机
              修复会话；刹车行带 last_error 也给。按钮本体借自 settings/failureAction.tsx（唯一实现，不抄标签表） */}
          {(hasError || (isBlocked && !!row.last_error)) && (
            <>
              <FailureActionButton failureId={failureId} compact />
              <AiFixButton cardId={row.id} />
            </>
          )}
          {/* #119（v0.48.8）：「回答…」(answer_input) 退役——受阻会话由 actd 收割进
              待验收；blocked 行只剩「停止」（+ 让 AI 修）出口。执行中出错的卡：
              「回答…」= comment 即 steer（方向修正经 §44.3 中继），橙色同原生 answer tint。 */}
          {showsAnswer && (
            <button type="button" className="btn btn-warning" onClick={() => setDialog("answer")}>
              {text("回答…", "Answer…")}
            </button>
          )}
          {!isBlocked && !showsAnswer && (
            <button type="button" className="btn" onClick={() => setDialog("comment")}>
              {text("评论", "Comment")}
            </button>
          )}
          <button type="button" className="btn btn-warning" onClick={() => setDialog("stop")}>
            {text("停止", "Stop")}
          </button>
          {/* §68.7 在终端接管（原生双击指令行 → 终端）：有会话指令的执行卡才给 */}
          {!isBlocked && !isQueued && cmd && <TerminalButton cardId={row.id} />}
          <DetailsToggle cardId={row.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {dialog === "stop" && (
        // 标题逐字镜像原生 confirmationDialog「停止这个任务？」；卡号进正文首行（原生的对话框挂在卡上，不需要点名）
        <ForkDialog
          title={text("停止这个任务？", "Stop this task?")}
          body={text(
            `${displayId(row)} · 退回提案＝丢弃这次结果重来；去待验收＝留下它做的，我来检查`,
            `${displayId(row)} · Discard & re-propose throws this run away; Keep for review keeps what it made for you to check`,
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
          body={isQueued
            ? text("文字会并入这张卡的记录，执行状态不变。", "Your note folds into the card; execution state is unchanged.")
            // executing 卡：comment 走 steer 中继（§44.3 安全窗口注入），排队/送达回执上卡面
            : text(
                "文字会并入卡片记录，并在安全窗口转达给执行中的会话；排队/送达状态会显示在卡上。",
                "Folds into the card and is relayed to the live session at a safe window; queued/delivered status shows on the card.",
              )}
          placeholder={text("想补充什么？", "What to add?")}
          submitLabel={text("提交", "Submit")}
          onSubmit={(t) => act(cardAction(row.id, "comment", t))}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "answer" && (
        <TextDialog
          title={text(`回答 ${row.id}`, `Answer ${row.id}`)}
          body={
            text("错误：", "Error: ") + String(errorText) + "\n\n"
            + text(
              "你的回答作为方向修正在安全窗口转达给执行中的会话（同 comment/steer 通道）；排队/送达状态会显示在卡上。",
              "Your answer is relayed to the live session at a safe window as a steer (same channel as comment); queued/delivered status shows on the card.",
            )
          }
          placeholder={text("怎么处理这个错误？", "How should it handle this error?")}
          submitLabel={text("发送回答", "Send answer")}
          onSubmit={(t) => act(cardAction(row.id, "comment", t))}
          onCancel={() => setDialog("none")}
        />
      )}
    </CardSurface>
  );
}
