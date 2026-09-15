// 会议纪要页（CONTRACT §63；?page=recaps 深链，顶栏入口）。数据源 = board.recaps（dashboard.json
// 顶层 add-only recaps[]，SSE 回流），本地标记（recapMarks）乐观覆盖；三把旋钮经 refreshRecapSettings。
// 页面骨架：返回链接 + 标题 + 分栏过滤器 + 左列表 / 右详情。业务态只有「选中的 key」与「看哪一栏」
// ——都放本地 useState（纯瞬态，不进 localStorage：默认永远从活跃栏开始）。
// §63.8（issue #297）：每行的生成态 = server 回执 generate_request × 本地乐观 recapPending；只要还有行
// 在生成，就每 5 s 补拉一次 /api/board（SSE 之外的保险，新版本落地即停），版本号变了面板自然换内容。
// §63.5 追记（issue #301）：三栏 活跃 / 已归档（sent_at 派生）/ 已忽略（dismissed_at），默认只看活跃；
// 刚被归档 / 忽略的那一行**留在右侧**（带着 toast 与撤销按钮）、只离开左列——不然人按下去什么反馈都没有。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import "../components/recaps/recaps.css";
import { RecapDetail } from "../components/recaps/RecapDetail";
import { RecapList } from "../components/recaps/RecapList";
import {
  generationPhase, isGenerating, laneCounts, recapLane, RECAP_LANES,
  type GenerationPhase, type RecapLane,
} from "../components/recaps/recapText";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { clearRecapPending, refreshBoard, refreshRecapSettings, useAppState, type RecapMark, type RecapPending } from "../store";
import type { RecapRow } from "../types";

/** 生成中的补拉间隔：actd pass 是 10 s，一半足够及时；不在生成时零请求 */
export const GENERATING_POLL_MS = 5000;

function withMarks(rows: RecapRow[], marks: Record<string, RecapMark>): RecapRow[] {
  return rows.map((row) => {
    const local = marks[row.key];
    return local
      ? { ...row, copied_at: local.copied_at ?? row.copied_at, sent_at: local.sent_at ?? null,
          dismissed_at: local.dismissed_at ?? null }
      : row;
  });
}

function phasesFor(rows: RecapRow[], pending: Record<string, RecapPending>, now: number): Record<string, GenerationPhase> {
  const out: Record<string, GenerationPhase> = {};
  for (const row of rows) out[row.key] = generationPhase(row, pending[row.key], now);
  return out;
}

/** 某一栏空着时那一句（整页空另有开场文案；这里说的是「这一栏」为什么空） */
function emptyLaneLine(lane: RecapLane, text: (zh: string, en: string) => string): string {
  if (lane === "archived") {
    return text("还没有归档的纪要。「标记已发送」即归档，取消标记就回到活跃。",
                "Nothing archived yet. Marking a recap as sent files it here; unmarking brings it back.");
  }
  if (lane === "dismissed") {
    return text("还没有被忽略的纪要。不需要记录的会议按「忽略」放到这里。",
                "Nothing dismissed yet. Press Dismiss on a meeting that needs no written record.");
  }
  return text("活跃列表空了——所有纪要都已归档或忽略。", "The active list is empty: every recap is archived or dismissed.");
}

export function RecapsPage() {
  const { text, language } = useI18n();
  const { board, recapSettings, recapMarks, recapPending, boardLoading } = useAppState();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [lane, setLane] = useState<RecapLane>("active");

  useEffect(() => {
    void refreshRecapSettings();
  }, []);

  const rows = withMarks(Array.isArray(board?.recaps) ? board.recaps : [], recapMarks);
  const counts = laneCounts(rows);
  const visible = rows.filter((row) => recapLane(row) === lane);
  // 选中的行按 key 在**全集**里找：刚归档 / 忽略的那一行不会从右侧被抽走（左列已经不再列它）
  const selected = rows.find((row) => row.key === selectedKey) ?? visible[0] ?? null;
  const phases = phasesFor(rows, recapPending, Date.now());
  const anyGenerating = Object.values(phases).some(isGenerating);
  // 乐观「排队中」的收尾：actd 回执接管 / 新版本落地 / 10 分钟退场 / 行消失 → 本地表里这一键退场
  // （unclaimed 仍留着——「后台未接手」那句要靠它显示，直到新回执 / 新版本 / 再按一次覆盖）
  const settled = Object.keys(recapPending).filter((key) => phases[key] !== "queued" && phases[key] !== "unclaimed");
  const settledKey = settled.join("\n");

  useEffect(() => {
    if (!settledKey) return;
    settledKey.split("\n").forEach(clearRecapPending);
  }, [settledKey]);

  // 默认选中第一行后把它钉住（否则它被归档 / 忽略时右侧会跳到下一行，人看不到自己按的那一下）
  const selectedNow = selected?.key ?? null;
  useEffect(() => {
    if (selectedNow) setSelectedKey(selectedNow);
  }, [selectedNow]);

  // 生成中每 5 s 补拉一次看板（SSE 掉线 / 被合并时的保险）；没有行在生成就不拉
  useEffect(() => {
    if (!anyGenerating) return;
    const timer = setInterval(() => void refreshBoard(), GENERATING_POLL_MS);
    return () => clearInterval(timer);
  }, [anyGenerating]);

  return (
    <main className="recaps-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="trash-page-head">
        <h2 className="trash-page-title">{text("会议纪要", "Meeting recaps")}</h2>
        <span className="trash-page-count">{rows.length}</span>
      </div>
      <p className="settings-helper">
        {text(
          "会议结束后 5–35 分钟自动出稿，5 行纯文本，复制即用。不会自动发给任何人。",
          "A 5-line plain-text recap lands 5–35 minutes after each meeting. Copy and paste; nothing is ever sent for you.",
        )}
        {recapSettings && !recapSettings.enabled && (
          <> {text("（会议纪要已在设置里关闭）", "(Meeting recaps are turned off in Settings)")}</>
        )}
      </p>
      {rows.length === 0 ? (
        <p className="recap-empty">
          {boardLoading
            ? text("读取中…", "Loading…")
            : text("还没有会议纪要。开着录屏 + 音频参加一场 10 分钟以上的 Zoom / Teams / Meet，结束后回来看。", "No recaps yet. Join a 10+ minute Zoom / Teams / Meet with screen + audio recording on and come back after it ends.")}
        </p>
      ) : (
        <>
          <div className="recap-filter recap-segmented" role="tablist" aria-label={text("分栏", "Filter")}>
            {RECAP_LANES.map((entry) => (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={lane === entry.id}
                className={`recap-segment${lane === entry.id ? " is-active" : ""}`}
                onClick={() => { setLane(entry.id); setSelectedKey(null); }}
              >
                {`${language === "zh" ? entry.zh : entry.en} ${counts[entry.id]}`}
              </button>
            ))}
          </div>
          {visible.length === 0 && <p className="recap-empty">{emptyLaneLine(lane, text)}</p>}
          <div className="recaps-layout">
            <RecapList rows={visible} selectedKey={selected?.key ?? null} onSelect={setSelectedKey} phases={phases} />
            {selected && <RecapDetail row={selected} settings={recapSettings} phase={phases[selected.key] ?? "idle"} />}
          </div>
        </>
      )}
    </main>
  );
}
