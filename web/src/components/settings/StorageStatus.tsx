// 「录制数据与磁盘」区的状态行（CONTRACT §71.1；issue #28）：GET /api/screenpipe/disk 的快照——当前占用（总 / 数据库 / 备份 /
// 日志 / 媒体）、可复用空间（freelist）、每月增长估算（依据随数字一起说：按最近样本 / 按全部历史平均 / 样本不足）、最早最新数据、
// 上次清理回执。server 的 GET 永不阻塞：首次回 computing 空壳、后台扫完才有数字——这里在 computing / refreshing 期间每
// POLL_INTERVAL_MS 轮询一次（上限 POLL_MAX 次，之后停在「统计中」而不是无限打）。「刷新」= ?refresh=1，让 server 重算一次。
// 保留天数本身是目录字段 screenpipe_retention_days（CatalogSection 渲）；本组件只读不写。备份文件只报路径与大小、不给删除按钮
// （§0 第 2 条：不可恢复的删除留给用户亲手做）。
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchScreenpipeDisk } from "../../api";
import { useI18n } from "../../i18n";
import type { ScreenpipeDisk, ScreenpipeDiskGrowth, ScreenpipePruneReceipt } from "../../types";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

export const POLL_INTERVAL_MS = 1500;
export const POLL_MAX = 40;

/** 十进制单位（访达同款）：null → —；≥ 10 GB 取整、≥ 1 GB 一位小数、MB 取整、其余 KB */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${Math.max(0, Math.round(bytes / 1e3))} KB`;
}

/** 「约 X GB / 月（依据）」；样本不足时把原因说出来而不是给 0 */
export function growthText(growth: ScreenpipeDiskGrowth, text: Text): string {
  if (growth.bytes_per_month === null) {
    return text("样本不足（需要相隔 ≥ 1 天的两次统计）", "Not enough samples yet (needs two measurements ≥ 1 day apart)");
  }
  const amount = formatBytes(Math.abs(growth.bytes_per_month));
  const sign = growth.bytes_per_month < 0 ? "−" : "";
  const days = growth.span_days ?? "?";
  const basis = growth.basis === "samples"
    ? text(`按最近 ${days} 天采样`, `from the last ${days} days of samples`)
    : text(`按全部 ${days} 天录制历史平均`, `average over all ${days} recorded days`);
  return text(`约 ${sign}${amount} / 月（${basis}）`, `≈ ${sign}${amount} / month (${basis})`);
}

/** ISO 时间戳 → YYYY-MM-DD；null → —；坏形原样 */
export function dayOf(ts: string | null | undefined): string {
  if (!ts) return "—";
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(ts);
  return m ? m[1] : ts;
}

/** 上次清理回执的一句话（act/lib/screenpipe_retention.py 的 receipt 形） */
export function pruneText(receipt: ScreenpipePruneReceipt | null, text: Text): string {
  if (!receipt) return text("尚未运行", "Not run yet");
  const ranAt = receipt.ran_at ?? "";
  const when = ranAt ? dayOf(ranAt) + (ranAt.length >= 16 ? ` ${ranAt.slice(11, 16)} UTC` : "") : "";
  if (receipt.error) return text(`${when} 出错：${receipt.error}`, `${when} failed: ${receipt.error}`);
  if (receipt.skipped === "retention_off") {
    return text(`${when} 保留期关闭（0 = 永久保留），未删除`, `${when} retention off (0 = keep forever), nothing deleted`);
  }
  if (receipt.skipped) return text(`${when} 跳过（${receipt.skipped}）`, `${when} skipped (${receipt.skipped})`);
  const frames = receipt.deleted_frames ?? 0;
  const audio = receipt.deleted_audio ?? 0;
  const tail = receipt.budget_exhausted ? text("；本轮时间预算用尽，下一轮继续", "; time budget used up, continues next round") : "";
  return text(`${when} 删除 ${frames} 帧 / ${audio} 条转写${tail}`, `${when} deleted ${frames} frames / ${audio} transcripts${tail}`);
}

function abbreviateHome(path: string): string {
  const m = /^(\/Users\/[^/]+)(\/.*)?$/.exec(path);
  return m ? `~${m[2] ?? ""}` : path;
}

function isPending(snap: ScreenpipeDisk): boolean {
  return snap.state === "computing" || snap.refreshing === true;
}

export function StorageStatus() {
  const { text } = useI18n();
  const [disk, setDisk] = useState<ScreenpipeDisk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const polls = useRef(0);
  const timer = useRef<number | null>(null);
  const alive = useRef(true);

  const load = useCallback(async (refresh: boolean): Promise<ScreenpipeDisk | null> => {
    setBusy(true);
    try {
      const snap = await fetchScreenpipeDisk(refresh);
      if (!alive.current) return null;
      setDisk(snap);
      setError(null);
      return snap;
    } catch (err) {
      if (alive.current) setError(errorMessage(err));
      return null;
    } finally {
      if (alive.current) setBusy(false);
    }
  }, []);

  // 拉一次；server 还在算（computing / refreshing）就间隔轮询，直到 ready 或次数用尽
  const poll = useCallback(async (refresh: boolean) => {
    const snap = await load(refresh);
    if (!snap || !alive.current) return;
    if (isPending(snap) && polls.current < POLL_MAX) {
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

  function refresh() {
    polls.current = 0;
    if (timer.current !== null) window.clearTimeout(timer.current);
    void poll(true);
  }

  if (!disk) {
    return error
      ? <p className="settings-warning" role="alert">{error}</p>
      : <p className="settings-helper">{text("统计中…", "Measuring…")}</p>;
  }
  const computing = disk.state === "computing";
  const measuring = text("统计中…", "Measuring…");
  const total = computing ? measuring : formatBytes(disk.total_bytes);
  const breakdown = computing ? "" : text(
    `数据库 ${formatBytes(disk.db_bytes)} · 备份 ${formatBytes(disk.backup_bytes)} · 日志 ${formatBytes(disk.log_bytes)} · 媒体 ${formatBytes(disk.media_bytes)}`,
    `database ${formatBytes(disk.db_bytes)} · backups ${formatBytes(disk.backup_bytes)} · logs ${formatBytes(disk.log_bytes)} · media ${formatBytes(disk.media_bytes)}`);
  const reclaimable = !computing && disk.db_reclaimable_bytes !== null && disk.db_reclaimable_bytes > 0 ? disk.db_reclaimable_bytes : null;

  return (
    <div className="storage-status">
      <div className="settings-field is-string">
        <div className="settings-field-head">
          <span className="settings-knob-label">{text("当前占用", "Disk usage")}</span>
          <span className="settings-source-chip" data-testid="storage-total">{total}</span>
          {disk.refreshing && !computing && <span className="settings-helper">{text("重新统计中…", "Re-measuring…")}</span>}
        </div>
        <div className="settings-knob-controls">
          <code className="settings-global-path">{abbreviateHome(disk.root)}</code>
          <button type="button" className="btn" disabled={busy || disk.refreshing} onClick={refresh}>{text("刷新", "Refresh")}</button>
        </div>
        {breakdown && <p className="settings-helper">{breakdown}</p>}
        {reclaimable !== null && (
          <p className="settings-helper">
            {text(`其中数据库内可复用空间 ${formatBytes(reclaimable)}（已清理、等新数据填入）`,
              `of which ${formatBytes(reclaimable)} inside the database is reusable (pruned, waiting for new data)`)}
          </p>
        )}
        {disk.state === "error" && (
          <p className="settings-warning" role="alert">{text(`统计失败：${disk.error ?? ""}`, `Measurement failed: ${disk.error ?? ""}`)}</p>
        )}
        {disk.db_error && disk.db_error !== "no_db" && (
          <p className="settings-warning" role="alert">{text(`数据库未能读取：${disk.db_error}`, `Database could not be read: ${disk.db_error}`)}</p>
        )}
        {!disk.root_exists && (
          <p className="settings-helper">{text("还没有录制数据目录（尚未录制过）", "No recording data directory yet (nothing recorded so far)")}</p>
        )}
      </div>
      <div className="settings-field is-string">
        <div className="settings-field-head">
          <span className="settings-knob-label">{text("每月增长", "Monthly growth")}</span>
          <span className="settings-source-chip" data-testid="storage-growth">{computing ? measuring : growthText(disk.growth, text)}</span>
        </div>
        <p className="settings-helper">
          {text(`最早数据 ${dayOf(disk.oldest_frame_ts)} · 最新 ${dayOf(disk.newest_frame_ts)}`,
            `oldest ${dayOf(disk.oldest_frame_ts)} · newest ${dayOf(disk.newest_frame_ts)}`)}
        </p>
      </div>
      <div className="settings-field is-string">
        <div className="settings-field-head">
          <span className="settings-knob-label">{text("上次清理", "Last prune")}</span>
          <span className={`settings-source-chip${disk.last_prune?.error ? " is-warning" : ""}`} data-testid="storage-prune">
            {pruneText(disk.last_prune, text)}
          </span>
        </div>
      </div>
      {disk.backups.length > 0 && (
        <p className="settings-warning" role="status">
          {text(`发现 ${disk.backups.length} 个数据库备份文件，共 ${formatBytes(disk.backup_bytes)}：`,
            `Found ${disk.backups.length} database backup file(s), ${formatBytes(disk.backup_bytes)} in total:`)}
          {" "}
          {disk.backups.map((b) => `${b.name}（${formatBytes(b.bytes)}）`).join("、")}
          {" "}
          {text("不再需要的话可在访达里手动删除；这里不会自动删。", "Delete them by hand in Finder if no longer needed; nothing here deletes them.")}
        </p>
      )}
    </div>
  );
}
