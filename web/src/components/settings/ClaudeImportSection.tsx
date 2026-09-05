// 导入 Claude Code 工作（§22 / §68.10）：原生 SettingsClaudeImport 的 web 版——扫描近 N 天的
// ~/.claude/projects 会话（GET /api/claude-sessions）→ 勾选 → 一个 import_claude_sessions inbox 动作
// （§10 词表既有；server inbox_writer 校验 session_ids）。默认只勾「等你回复」的（原生同）。
// 已提交的 id 记在组件级 `imported`（原生 locallyImported）：立刻从列表消失、下一次重新扫描也不回来——
// actd 处理 inbox 动作前的那几秒，state/claude_sessions_import.json 还没记它们（act/radar_claude_sessions）。
import { useEffect, useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { refreshClaudeSessions, useAppState } from "../../store";
import { RelativeTime } from "../board/cardChrome";
import { errorMessage } from "./useToast";

/** 原生 SettingsClaudeImport：候选默认只列前几条，「显示全部 (N)」展开 */
const SHOW_DEFAULT = 8;

export function ClaudeImportSection() {
  const { text } = useI18n();
  const { claudeSessions, pageErrors } = useAppState();
  const [window, setWindow] = useState(7);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [imported, setImported] = useState<ReadonlySet<string>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!claudeSessions) void refreshClaudeSessions(window);
  }, [claudeSessions, window]);

  // 扫描结果到了 → 预勾「等你回复」的会话（原生默认）
  useEffect(() => {
    if (!claudeSessions) return;
    setPicked(new Set(claudeSessions.candidates.filter((c) => c.ended_waiting_on_user && !c.answered).map((c) => c.session_id)));
  }, [claudeSessions]);

  // 本会话已提交的行不再列出（重新扫描回来的同一批也过滤）
  const candidates = (claudeSessions?.candidates ?? []).filter((c) => !imported.has(c.session_id));
  const waitingCount = candidates.filter((c) => c.ended_waiting_on_user && !c.answered).length;
  // 原生 importSelected：ids = candidates ∩ selected——藏起来的已导入行永不算在内
  const pickedIds = candidates.filter((c) => picked.has(c.session_id)).map((c) => c.session_id);

  async function importPicked() {
    const ids = pickedIds;
    if (ids.length === 0) return;
    setBusy(true);
    setNote(null);
    try {
      // §22 wire：{action, session_ids}——ts 由 server 盖章，多一个字段 400
      await postAction({ action: "import_claude_sessions", session_ids: ids });
      setImported((s) => new Set([...s, ...ids]));
      setPicked(new Set());
      // 两条去向 = act/radar_claude_sessions._import_status：等你回复 → card_sent（提案），其余 → detected（潜在任务）
      setNote({ ok: true, message: text(`已提交 ${ids.length} 条——后台服务几秒内会把它们变成看板卡片（等你回复的进「提案」，其余进「潜在任务」）。`, `Submitted ${ids.length} — the background service turns them into board cards within seconds (waiting-on-you ones go to Proposals, the rest to Backlog).`) });
    } catch (err) {
      setNote({ ok: false, message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const toggle = (id: string) => setPicked((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  return (
    <section className="settings-section" id="settings-claude_import" aria-labelledby="settings-claude_import-title">
      <h3 id="settings-claude_import-title" className="settings-section-title">{text("导入 Claude Code 工作", "Import Claude Code work")}</h3>
      <p className="settings-helper">
        {text("把你最近在 Claude Code 里做的事一键变成看板卡片，尤其是 AI 还在等你回复的那些。全程本地，不上传任何内容。已导入过的不会重复。", "Turn your recent Claude Code work into board cards in one click — especially sessions where the AI is still waiting on your reply. Everything stays local; nothing is uploaded. Already-imported sessions are skipped.")}
      </p>
      <div className="settings-actions">
        <label className="settings-knob-label" htmlFor="claude-import-window">{text("窗口（天）", "Window (days)")}</label>
        <input id="claude-import-window" className="settings-input is-number" type="number" min={1} max={90} value={window}
          onChange={(e) => setWindow(Math.min(90, Math.max(1, Number(e.target.value) || 7)))} />
        <button type="button" className="btn" disabled={busy} onClick={() => { setNote(null); void refreshClaudeSessions(window); }}>
          {claudeSessions ? text("重新扫描", "Re-scan") : text(`扫描最近 ${window} 天`, `Scan last ${window} days`)}
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || pickedIds.length === 0} onClick={() => void importPicked()}>
          {text(`导入所选 (${pickedIds.length})`, `Import selected (${pickedIds.length})`)}
        </button>
      </div>
      {candidates.length > 0 && (
        <p className="settings-helper">
          {text(`找到 ${candidates.length} 个会话，其中 ${waitingCount} 个在等你回复（已默认勾选）`, `Found ${candidates.length} sessions — ${waitingCount} waiting on you (pre-checked)`)}
        </p>
      )}
      {pageErrors.claudeSessions && <p className="settings-error" role="alert">{pageErrors.claudeSessions}</p>}
      {claudeSessions && !claudeSessions.ok && (
        <p className="settings-warning">
          {claudeSessions.reason === "no_claude_dir"
            ? text(`没有找到 Claude Code 会话目录（${claudeSessions.root ?? "~/.claude/projects"}）。`, `No Claude Code sessions directory found (${claudeSessions.root ?? "~/.claude/projects"}).`)
            : text("扫描失败：", "Scan failed: ") + (claudeSessions.error ?? claudeSessions.reason ?? "")}
        </p>
      )}
      {/* 空态是扫描时刻的判决（原生 emptyReason）：刚导入完把列表清空的那一拍只留回执句，重新扫描后才判 */}
      {claudeSessions?.ok && candidates.length === 0 && !note?.ok && <p className="settings-helper">{text("这个窗口里没有可导入的会话。", "No importable sessions in this window.")}</p>}
      {candidates.length > 0 && (
        <div className="settings-actions">
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setPicked(new Set(candidates.filter((c) => !c.session_mismatch).map((c) => c.session_id)))}>{text("全选", "Select all")}</button>
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setPicked(new Set())}>{text("全不选", "Select none")}</button>
          {candidates.length > SHOW_DEFAULT && (
            <button type="button" className="btn btn-quiet" onClick={() => setShowAll((v) => !v)}>
              {showAll ? text("收起", "Show fewer") : text(`显示全部 (${candidates.length})`, `Show all (${candidates.length})`)}
            </button>
          )}
        </div>
      )}
      {candidates.length > 0 && (
        <ul className="settings-list" aria-label={text("会话候选", "Session candidates")}>
          {(showAll ? candidates : candidates.slice(0, SHOW_DEFAULT)).map((c) => (
            <li key={c.session_id} className={`settings-list-row${c.session_mismatch ? " is-muted" : ""}`}>
              <label className="settings-check-row">
                <input type="checkbox" checked={picked.has(c.session_id)} disabled={busy || c.session_mismatch} onChange={() => toggle(c.session_id)} />
                <span className="settings-list-title">{c.title || c.gist || c.session_id}</span>
              </label>
              <span className="settings-list-meta">
                {c.project && <span className="chip">{c.project}</span>}
                {c.ended_waiting_on_user && !c.answered && <span className="chip chip-warning">{text("等你回复", "waiting on you")}</span>}
                {c.answered && <span className="chip chip-quiet">{text("像已答完的问答", "looks answered")}</span>}
                <RelativeTime iso={c.last_activity} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {note && <p className={note.ok ? "settings-helper is-ok" : "settings-warning"} role={note.ok ? "status" : "alert"}>{note.message}</p>}
    </section>
  );
}
