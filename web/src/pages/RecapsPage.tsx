// 会议纪要页（CONTRACT §63；?page=recaps 深链，顶栏入口）。数据源 = board.recaps（dashboard.json
// 顶层 add-only recaps[]，SSE 回流），本地标记（recapMarks）乐观覆盖；三把旋钮经 refreshRecapSettings。
// 页面骨架：返回链接 + 标题 + 左列表 / 右详情。业务态只有「选中的 key」——放本地 useState（纯瞬态）。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import "../components/recaps/recaps.css";
import { RecapDetail } from "../components/recaps/RecapDetail";
import { RecapList } from "../components/recaps/RecapList";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { refreshRecapSettings, useAppState, type RecapMark } from "../store";
import type { RecapRow } from "../types";

function withMarks(rows: RecapRow[], marks: Record<string, RecapMark>): RecapRow[] {
  return rows.map((row) => {
    const local = marks[row.key];
    return local ? { ...row, copied_at: local.copied_at ?? row.copied_at, sent_at: local.sent_at ?? null } : row;
  });
}

export function RecapsPage() {
  const { text } = useI18n();
  const { board, recapSettings, recapMarks, boardLoading } = useAppState();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  useEffect(() => {
    void refreshRecapSettings();
  }, []);

  const rows = withMarks(Array.isArray(board?.recaps) ? board.recaps : [], recapMarks);
  const selected = rows.find((row) => row.key === selectedKey) ?? rows[0] ?? null;

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
        <div className="recaps-layout">
          <RecapList rows={rows} selectedKey={selected?.key ?? null} onSelect={setSelectedKey} />
          {selected && <RecapDetail row={selected} settings={recapSettings} />}
        </div>
      )}
    </main>
  );
}
