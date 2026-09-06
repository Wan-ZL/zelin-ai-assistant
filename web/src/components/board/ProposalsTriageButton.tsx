// 提案列头「清理积压」（§34bis；原生 Kanban.swift ProposalsTriageButton 逐字镜像）：一次固定
// prompt 的 direct-run capture——{action:"capture", text:<短标签>, mode:"run", preset:"proposals_triage"}；
// 固定 prompt 的单一真源在 actd（_proposals_triage_plan），web 只发 preset 信号 + 短标签。
// 提案列没有积压（后端提案卡 0 张，processing 占位也计入）时禁用；2s 防连点。
// 回执 = 原生 RunCapturePendingRow 的状态句（AppDelegate.submitProposalsTriage → store.beginCapture(run: true)，
// Cards.swift:848,863-867）：管线 ok「已提交，直接开跑（跳过提案），排队派发中…」/ 不 ok「已保存到队列，pipeline 启动后
// 直接开跑」——判据与列顶输入框同一个 pipelineStalled（captureReceipt.ts），健康一变句子随之切换（§10 / §41 追记）。
// 寿命也与列顶输入框同一份（useCaptureReceipt：原生那张占位卡就是同一个 beginCapture）：刷新带来 running / needs_input
// 里名字前缀匹配短标签的行即清（原生注释「text = 短标签 = 后端卡标题，归一匹配天然清除」）；否则 180 s（管线 ok 时才计时）
// 后换成原生的橙色超时条「「<短标签前 20 字>」任务没有开始——后台可能没在跑（检查 actd）」，120 s 褪去。
import { useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { describeActionError } from "./boardActions";
import { captureNote, captureTimeoutNotice } from "./captureReceipt";
import { useCaptureReceipt } from "./useCaptureReceipt";

/** preset 词表值——与 act/actd.py PROPOSALS_TRIAGE_PRESET / 原生 ProposalsTriage.presetKey 逐字一致 */
export const PROPOSALS_TRIAGE_PRESET = "proposals_triage";
/** capture 短标签 = 卡片标题 + 运行中列灰色占位卡的回显文案（原生 ProposalsTriage.captureText） */
export const PROPOSALS_TRIAGE_TEXT = "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议";

export function proposalsTriageBody() {
  return { action: "capture", text: PROPOSALS_TRIAGE_TEXT, mode: "run", preset: PROPOSALS_TRIAGE_PRESET };
}

export function ProposalsTriageButton({ backlogCount }: { backlogCount: number }) {
  const { text } = useI18n();
  const { receipt, stalled, begin: beginReceipt } = useCaptureReceipt("run");
  const [cooling, setCooling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const enabled = backlogCount > 0 && !cooling;

  const fire = async () => {
    if (!enabled) return;
    setCooling(true);
    setError(null);
    try {
      const response = await postAction(proposalsTriageBody());
      beginReceipt(PROPOSALS_TRIAGE_TEXT, response); // 成功才替换上一份回执（原生 writeInboxFile 失败不 beginCapture）
    } catch (e) {
      setError(describeActionError(e, text));
    }
    window.setTimeout(() => setCooling(false), 2000);
  };

  return (
    <div className="lane-triage">
      <button
        type="button"
        className="btn lane-triage-button"
        disabled={!enabled}
        onClick={() => void fire()}
        title={backlogCount > 0
          ? text("启动一个临时 Claude 会话审阅本列全部积压提案：逐张判断仍值得做/过时/重复，和你对话确认后交付一份保留/丢弃/合并建议清单（会话出现在运行中列，可随时打开参与；它不会直接改动任何卡片）", "Launch a temporary Claude session to review this lane's backlog: it judges each proposal (keep / stale / duplicate), confirms with you in conversation, and delivers a keep/drop/merge recommendation list. The session appears in Running — join anytime; it never modifies cards directly.")
          : text("提案列没有积压——有卡片堆起来时再用", "No backlog in Proposals — come back when cards pile up")}
      >
        {/* 原生 Image(sparkles) + Text：图标与动词各一节点 */}
        <span aria-hidden="true">✦ </span><span>{text("清理积压", "Clean up")}</span>
      </button>
      {/* 一行栈：失败句 > 超时条 > 状态句（渲染时现算 stalled：健康在回执挂着时变了，句子跟着变——原生 body 每次重算） */}
      {error ? (
        <span className="card-meta-text">{error}</span>
      ) : receipt?.timedOut ? (
        // 原生 NoticeRow raiseTimeout = .orange（--warning）
        <span className="composer-notice is-run-timeout" role="status">{captureTimeoutNotice("run", receipt.text, text)}</span>
      ) : receipt ? (
        <span className="card-meta-text">{captureNote("run", stalled, text)}</span>
      ) : null}
    </div>
  );
}
