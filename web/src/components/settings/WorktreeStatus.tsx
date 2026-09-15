// 开发者区的「隔离工作树（worktree）」行（CONTRACT §75.4；issue #315）：GET /api/worktrees 的快照——条数、占用、
// 本轮可清理几条，加一颗「清理」按钮（POST /api/worktrees/cleanup）。server 的 GET 永不阻塞：首次回 computing 空壳、
// 后台扫完才有数字，这里在 computing / refreshing 期间轮询（上限 POLL_MAX 次，之后停在「统计中」而不是无限打）。
// 判决全在 server（act/lib/worktrees.py）：脏的、锁着的、还有在飞的卡指着的、有只存在于本地的提交的，一条都不删——
// 页面只逐字复述回执，不自己算该删谁（防腐 #10）。字节格式化复用 StorageStatus 的 formatBytes，不另立一套。
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWorktrees, postWorktreesCleanup } from "../../api";
import { useI18n } from "../../i18n";
import type { WorktreeCleanup, WorktreeInventory } from "../../types";
import { formatBytes } from "./StorageStatus";
import { errorMessage } from "./useToast";

export const POLL_INTERVAL_MS = 1500;
export const POLL_MAX = 40;

function isPending(snap: WorktreeInventory): boolean {
  return snap.state === "computing" || snap.refreshing === true;
}

/** 清理回执的一句话：删了几条、删了几条分支、失败几条、被守卫留下几条 */
export function cleanupText(receipt: WorktreeCleanup, text: (zh: string, en: string) => string): string {
  if (receipt.ok === false) {
    return text(`清理失败：${receipt.message ?? receipt.error ?? ""}`, `Cleanup failed: ${receipt.message ?? receipt.error ?? ""}`);
  }
  const removed = receipt.removed?.length ?? 0;
  const branches = receipt.removed?.filter((r) => r.branch_deleted).length ?? 0;
  const failed = receipt.failed?.length ?? 0;
  const kept = Object.values(receipt.skipped ?? {}).reduce((a, b) => a + b, 0);
  const tail = failed > 0 ? text(`，${failed} 条删不掉`, `, ${failed} could not be removed`) : "";
  return text(`已清理 ${removed} 个 worktree、${branches} 条本地分支${tail}；保留 ${kept} 条（脏 / 锁定 / 在飞 / 有未推送提交）`,
    `Removed ${removed} worktrees and ${branches} local branches${tail}; kept ${kept} (dirty / locked / in flight / unpushed)`);
}

export function WorktreeStatus() {
  const { text } = useI18n();
  const [snap, setSnap] = useState<WorktreeInventory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const polls = useRef(0);
  const timer = useRef<number | null>(null);
  const alive = useRef(true);

  const load = useCallback(async (refresh: boolean): Promise<WorktreeInventory | null> => {
    try {
      const got = await fetchWorktrees(refresh);
      if (!alive.current) return null;
      setSnap(got);
      setError(null);
      return got;
    } catch (err) {
      if (alive.current) setError(errorMessage(err));
      return null;
    }
  }, []);

  const poll = useCallback(async (refresh: boolean) => {
    const got = await load(refresh);
    if (!got || !alive.current) return;
    if (isPending(got) && polls.current < POLL_MAX) {
      polls.current += 1;
      timer.current = window.setTimeout(() => void poll(false), POLL_INTERVAL_MS);
    }
  }, [load]);

  useEffect(() => {
    alive.current = true;
    void poll(false);
    return () => {
      alive.current = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [poll]);

  async function cleanup() {
    setBusy(true);
    setNote(text("正在清理…", "Cleaning up…"));
    try {
      const receipt = await postWorktreesCleanup();
      if (!alive.current) return;
      setNote(cleanupText(receipt, text));
      polls.current = 0;
      void poll(true);
    } catch (err) {
      if (alive.current) setNote(errorMessage(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  if (!snap) {
    return error
      ? <p className="settings-warning" role="alert">{error}</p>
      : <p className="settings-helper">{text("统计中…", "Measuring…")}</p>;
  }
  const computing = snap.state === "computing";
  const measuring = text("统计中…", "Measuring…");
  const count = computing || snap.worktrees === null ? measuring
    : text(`${snap.worktrees} 个 · ${formatBytes(snap.bytes)}${snap.bytes_partial ? text("（占用未能测全）", " (size partly unmeasured)") : ""}`,
      `${snap.worktrees} · ${formatBytes(snap.bytes)}${snap.bytes_partial ? text("（占用未能测全）", " (size partly unmeasured)") : ""}`);
  const removable = snap.removable ?? 0;

  return (
    <div className="settings-field is-string" data-testid="worktree-status">
      <div className="settings-field-head">
        <span className="settings-knob-label">{text("隔离工作树（worktree）", "Isolated worktrees")}</span>
        <span className="settings-source-chip" data-testid="worktree-count">{count}</span>
      </div>
      <div className="settings-knob-controls">
        <button type="button" className="btn" disabled={busy || computing || removable === 0} onClick={() => void cleanup()}>
          {text("清理", "Clean up")}
        </button>
        <button type="button" className="btn" disabled={busy || snap.refreshing === true} onClick={() => { polls.current = 0; void poll(true); }}>
          {text("刷新", "Refresh")}
        </button>
      </div>
      <p className="settings-helper">
        {computing ? measuring : text(
          `本轮可清理 ${removable} 个（分支已在 origin 合并 / 删除，或 ${snap.stale_days ?? 14} 天没动过且没有未提交改动）。脏的、锁定的、还有在飞的卡指着的一律保留。`,
          `${removable} can be reclaimed now (branch merged or deleted on origin, or untouched for ${snap.stale_days ?? 14} days with no uncommitted changes). Dirty, locked and in-flight ones are always kept.`)}
      </p>
      {snap.truncated && (
        <p className="settings-helper">{text("条数太多，本轮只判到时间预算为止——再点一次继续。", "Too many to classify in one pass — click again to continue.")}</p>
      )}
      {snap.state === "error" && (
        <p className="settings-warning" role="alert">{text(`统计失败：${snap.error ?? ""}`, `Measurement failed: ${snap.error ?? ""}`)}</p>
      )}
      {snap.ok === false && snap.state !== "error" && (
        <p className="settings-warning" role="alert">{text(`清点不完整：${snap.message ?? snap.error ?? ""}`, `Incomplete inventory: ${snap.message ?? snap.error ?? ""}`)}</p>
      )}
      {note && <p className="settings-helper" role="status">{note}</p>}
      {error && <p className="settings-warning" role="alert">{error}</p>}
    </div>
  );
}
