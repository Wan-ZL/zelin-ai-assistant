// 会议纪要页（CONTRACT §63；?page=recaps 深链，顶栏入口）。数据源 = board.recaps（dashboard.json
// 顶层 add-only recaps[]，SSE 回流），本地标记（recapMarks）乐观覆盖；三把旋钮经 refreshRecapSettings。
// 页面骨架：返回链接 + 标题 + 分栏过滤器 + 左列表 / 右详情。业务态只有「选中的 key」与「看哪一栏」
// ——都放本地 useState（纯瞬态，不进 localStorage：默认永远从活跃栏开始）。
// §63.8（issue #297）：每行的生成态 = server 回执 generate_request × 本地乐观 recapPending；只要还有行
// 在生成，就每 5 s 补拉一次 /api/board（SSE 之外的保险，新版本落地即停），版本号变了面板自然换内容。
// §63.5 追记（issue #301）：三栏 活跃 / 已归档（sent_at 派生）/ 已忽略（dismissed_at），默认只看活跃；
// 刚被归档 / 忽略的那一行**留在右侧**（带着 toast 与撤销按钮）、只离开左列——不然人按下去什么反馈都没有。
// 同一条追记的诚实口径：投影每栏有上限，被切掉的行数由 board.recap_counts（真实总数）减出来，
// 计数带 `+`、栏里多一句「另有 N 条更早的没列出来」——上限可以是硬的，界面不许悄悄少东西。
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
import type { RecapLaneTotals, RecapRow } from "../types";

/** 生成中的补拉间隔：actd pass 是 10 s，一半足够及时；不在生成时零请求 */
export const GENERATING_POLL_MS = 5000;

function withMarks(rows: RecapRow[], marks: Record<string, RecapMark>): RecapRow[] {
  return rows.map((row) => {
    const local = marks[row.key];
    if (!local) return row;
    const merged: RecapRow = { ...row, copied_at: local.copied_at ?? row.copied_at, sent_at: local.sent_at ?? null,
                               dismissed_at: local.dismissed_at ?? null };
    // §63.16：手改的结束时间只在本地标记**带着这个键**时覆盖（null 也是一个值 = 已清、回到录制时间）；
    // 别的标记（复制 / 已发送）不带它，行上 server 给的值照旧
    if ("end_override" in local) merged.end_override = local.end_override ?? null;
    return merged;
  });
}

function phasesFor(rows: RecapRow[], pending: Record<string, RecapPending>, now: number): Record<string, GenerationPhase> {
  const out: Record<string, GenerationPhase> = {};
  for (const row of rows) out[row.key] = generationPhase(row, pending[row.key], now);
  return out;
}

/**
 * 某一栏被投影预算切掉了多少行（§63.5 追记 issue #301）：server 报的真实总数
 * `board.recap_counts` 减掉它**实际发来**的行数。两边都是 server 数据——不拿本地乐观
 * 标记后的行数去减（那会在刚按下「忽略」的一瞬间算出一个假的差额），也不在这里写死上限
 * （防腐 #10：分栏组成是 server 数据，不是 client 代码）。老 daemon 无此键 = 0，不说话。
 */
function hiddenByCap(totals: RecapLaneTotals | undefined, shipped: Record<RecapLane, number>, id: RecapLane): number {
  const total = totals?.[id];
  return typeof total === "number" ? Math.max(0, total - shipped[id]) : 0;
}

/** 被切掉的那几条去哪了：还在磁盘上，直到保留期到（硬删只由 §63.3 的两道保留窗执行） */
function cappedLine(hidden: number, text: (zh: string, en: string) => string): string {
  return text(`另有 ${hidden} 条更早的纪要没列在这一栏（每栏有行数上限）——它们仍在磁盘上，直到保留期到。`,
              `${hidden} older recap(s) are not listed in this lane (each lane is capped); they are still on disk until retention.`);
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

  const shippedRows = Array.isArray(board?.recaps) ? board.recaps : [];
  const rows = withMarks(shippedRows, recapMarks);
  const counts = laneCounts(rows);
  // 预算切掉的行数按**server 发来的**那一份算（乐观标记只影响显示的计数，不影响「被切掉多少」）
  const shipped = laneCounts(shippedRows);
  const hidden = RECAP_LANES.reduce((acc, entry) => {
    acc[entry.id] = hiddenByCap(board?.recap_counts, shipped, entry.id);
    return acc;
  }, {} as Record<RecapLane, number>);
  const totalHidden = RECAP_LANES.reduce((sum, entry) => sum + hidden[entry.id], 0);
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
        <span className="trash-page-count">{`${rows.length}${totalHidden > 0 ? "+" : ""}`}</span>
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
                {`${language === "zh" ? entry.zh : entry.en} ${counts[entry.id]}${hidden[entry.id] > 0 ? "+" : ""}`}
              </button>
            ))}
          </div>
          {hidden[lane] > 0 && <p className="recap-capped">{cappedLine(hidden[lane], text)}</p>}
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
