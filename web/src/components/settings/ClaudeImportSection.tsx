// 导入 Claude Code 工作（§22 / §68.10）：原生 SettingsClaudeImport 的 web 版——扫描近 N 天的
// ~/.claude/projects 会话（GET /api/claude-sessions）→ 勾选 → 一个 import_claude_sessions inbox 动作
// （§10 词表既有；server inbox_writer 校验 session_ids）。默认只勾「等你回复」的（原生同）。
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

  const candidates = claudeSessions?.candidates ?? [];

  async function importPicked() {
    if (picked.size === 0) return;
    setBusy(true);
    setNote(null);
    try {
      // §22 wire：{action, session_ids}——ts 由 server 盖章，多一个字段 400
      await postAction({ action: "import_claude_sessions", session_ids: [...picked] });
      setNote({ ok: true, message: text(`已提交 ${picked.size} 个会话，几秒后出现在潜在任务列。`, `Submitted ${picked.size} session(s); they appear in Backlog within seconds.`) });
      setPicked(new Set());
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
        {text("扫描最近的 Claude Code 会话，把「AI 在等你回复」的那些变成潜在任务卡（空看板的最便宜种子）。已导入过的不会重复。", "Scans recent Claude Code sessions and turns the ones waiting on you into Backlog cards (the cheapest seed for an empty board). Already-imported sessions are skipped.")}
      </p>
      <div className="settings-actions">
        <label className="settings-knob-label" htmlFor="claude-import-window">{text("窗口（天）", "Window (days)")}</label>
        <input id="claude-import-window" className="settings-input is-number" type="number" min={1} max={90} value={window}
          onChange={(e) => setWindow(Math.min(90, Math.max(1, Number(e.target.value) || 7)))} />
        <button type="button" className="btn" disabled={busy} onClick={() => void refreshClaudeSessions(window)}>
          {claudeSessions ? text("重新扫描", "Re-scan") : text(`扫描最近 ${window} 天`, `Scan last ${window} days`)}
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || picked.size === 0} onClick={() => void importPicked()}>
          {text(`导入所选 (${picked.size})`, `Import selected (${picked.size})`)}
        </button>
      </div>
      {pageErrors.claudeSessions && <p className="settings-error" role="alert">{pageErrors.claudeSessions}</p>}
      {claudeSessions && !claudeSessions.ok && (
        <p className="settings-warning">
          {claudeSessions.reason === "no_claude_dir"
            ? text(`没有找到 Claude Code 会话目录（${claudeSessions.root ?? "~/.claude/projects"}）。`, `No Claude Code sessions directory found (${claudeSessions.root ?? "~/.claude/projects"}).`)
            : text("扫描失败：", "Scan failed: ") + (claudeSessions.error ?? claudeSessions.reason ?? "")}
        </p>
      )}
      {claudeSessions?.ok && candidates.length === 0 && <p className="settings-helper">{text("这个窗口里没有可导入的会话。", "No importable sessions in this window.")}</p>}
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
