// 提案列头「清理积压」（§34bis；原生 Kanban.swift ProposalsTriageButton 逐字镜像）：一次固定
// prompt 的 direct-run capture——{action:"capture", text:<短标签>, mode:"run", preset:"proposals_triage"}；
// 固定 prompt 的单一真源在 actd（_proposals_triage_plan），web 只发 preset 信号 + 短标签。
// 提案列没有积压（后端提案卡 0 张，processing 占位也计入）时禁用；2s 防连点。
import { useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { describeActionError } from "./boardActions";

/** preset 词表值——与 act/actd.py PROPOSALS_TRIAGE_PRESET / 原生 ProposalsTriage.presetKey 逐字一致 */
export const PROPOSALS_TRIAGE_PRESET = "proposals_triage";
/** capture 短标签 = 卡片标题 + 运行中列灰色占位卡的回显文案（原生 ProposalsTriage.captureText） */
export const PROPOSALS_TRIAGE_TEXT = "清理提案积压：审阅提案列的积压卡片，给出保留/丢弃/合并建议";

export function proposalsTriageBody() {
  return { action: "capture", text: PROPOSALS_TRIAGE_TEXT, mode: "run", preset: PROPOSALS_TRIAGE_PRESET };
}

export function ProposalsTriageButton({ backlogCount }: { backlogCount: number }) {
  const { text } = useI18n();
  const [cooling, setCooling] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const enabled = backlogCount > 0 && !cooling;

  const fire = async () => {
    if (!enabled) return;
    setCooling(true);
    setNote(null);
    try {
      await postAction(proposalsTriageBody());
      setNote(text("清理会话已提交（运行中列出现）", "Clean-up session submitted (appears in Running)"));
    } catch (e) {
      setNote(describeActionError(e, text));
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
      {note && <span className="card-meta-text">{note}</span>}
    </div>
  );
}
