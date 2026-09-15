// §63.11（issue #302）「重新生成」面板里的意图问答：第一版已经在上面了，这几句问的是
// 「这一份是干什么用的」，第二版按答案出、第一版作为「转写原版」留着可切。
// 问题的**组成**逐字来自 wire（`recaps[].questions`，daemon 从这一版正文自己推出来的：
// 逐条分工 / 截止 / 对方的要求 / 研究级细节 / 发给谁 / 认不认领 / 要不要比上一份）——
// client 不造问题、不排序、不补默认值（防腐 #10）。问法与选项文案走页面同一套 text(zh, en)。
// 一个问题**没点过就不发答案**：默认值会变成一句 owner 从没说过的指令，而这一条 issue 的
// 正题恰恰是「管线从来没问过就替人做了决定」。再点一次选中的那一项 = 取消这个答案。
import type { RecapQuestion } from "../../types";
import { answerLabel, questionLabel } from "./recapText";

export interface RecapIntentProps {
  questions: RecapQuestion[];
  /** 已点过的答案 {问题 id: 选项}；没点过的 id 不在表里 */
  picks: Record<string, string>;
  onPick: (id: string, option: string) => void;
  text: (zh: string, en: string) => string;
  disabled?: boolean;
}

export function RecapIntentPanel({ questions, picks, onPick, text, disabled = false }: RecapIntentProps) {
  if (!questions.length) return null;
  const answered = Object.keys(picks).length;
  return (
    <div className="recap-intent">
      <span className="recap-panel-label" id="recap-intent-label">
        {text("这一份纪要是干什么用的？（答过的问题会进下一次生成；没答的不算）",
              "What is this recap for? (answered questions steer the next version; unanswered ones do not)")}
      </span>
      <ul className="recap-intent-list" aria-labelledby="recap-intent-label">
        {questions.map((question) => (
          <li key={question.id} className="recap-intent-row">
            <span className="recap-intent-ask">
              {questionLabel(question, text)}
              {question.subject ? <em className="recap-intent-subject">{question.subject}</em> : null}
            </span>
            <div className="recap-segmented" role="radiogroup"
                 aria-label={questionLabel(question, text)}>
              {question.options.map((option) => {
                const picked = picks[question.id] === option;
                return (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={picked}
                    disabled={disabled}
                    className={`recap-segment${picked ? " is-active" : ""}`}
                    onClick={() => onPick(question.id, option)}
                  >
                    {answerLabel(option, text)}
                  </button>
                );
              })}
            </div>
          </li>
        ))}
      </ul>
      <p className="recap-hint">
        {answered
          ? text(`已回答 ${answered} 项：重新生成会按这些答案出第二版，现在这一版会留在「转写原版」里。`,
                 `${answered} answered: regenerating writes a second version from them, and this one stays under "What the transcript said".`)
          : text("没回答也可以直接重新生成——那就是按转写原样再出一版。",
                 "You can regenerate without answering: that is another version straight from the transcript.")}
      </p>
    </div>
  );
}
