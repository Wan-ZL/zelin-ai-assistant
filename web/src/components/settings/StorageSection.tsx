// 设置页「录制」区里的磁盘块（CONTRACT §71，issue #28）：占了多少盘 · 大概每月涨多少 ·
// 原始媒体留多久 · 上一次 prune 什么时候跑的。录制区在此之前只有「录多少」，没有「留多少」——
// 第一个信号是一个月后磁盘满了。
// 数据 = GET /api/settings/storage（server 在后台线程里走目录树，这个 GET 从不阻塞；还没有结果
// 时回 state:"scanning"，这里最多轮询 SCAN_POLLS 次，每次 SCAN_POLL_MS）。保存 = PUT 一个键，
// server 夹取到 [bounds.min, bounds.max] 并 diff-write；下一轮 cron（每 30 分钟）生效，无需重启。
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchStorageSettings, putStorageSettings } from "../../api";
import { useI18n } from "../../i18n";
import type { StorageGrowth, StoragePrune, StorageSettings, StorageUsage } from "../../types";
import { errorMessage } from "./useToast";

const SCAN_POLL_MS = 2000;
const SCAN_POLLS = 30;      // 60 s 之后不再追——超大目录的结果下次进页面时自然到

type Text = (zh: string, en: string) => string;

/** 字节 → 人话（1024 进制，≥10 的量级不带小数：「1.4 GB」「23 GB」「812 MB」） */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

/** 「约 X/月」——估计的基准也说出来：采样期够长就说量了几天，否则说是建立至今的平均 */
export function growthSentence(growth: StorageGrowth | null, text: Text): string | null {
  if (!growth || growth.bytes_per_month <= 0) return null;
  const size = formatBytes(growth.bytes_per_month);
  return growth.basis === "samples"
    ? text(`约 ${size}/月（按最近 ${growth.days} 天的实测增长）`, `About ${size}/month (measured over the last ${growth.days} days)`)
    : text(`约 ${size}/月（按目录建立至今 ${growth.days} 天的平均）`, `About ${size}/month (average since the folder was created ${growth.days} days ago)`);
}

/** prune 状态行。停掉的 prune 与「没东西可删」的 prune 从外面看一模一样——前一种会悄悄把盘吃满，
 *  所以 never / stale / unreadable 都进 warning 档，只有新鲜的 ok 是平常语气。 */
export function pruneSentence(prune: StoragePrune, text: Text): { message: string; warn: boolean } {
  if (prune.state === "never") {
    return { message: text("清理任务还没有留下过回执——它可能从没跑过。", "The prune job has never left a receipt — it may never have run."), warn: true };
  }
  if (prune.state === "unreadable") {
    return { message: text(`上次 ${prune.ran_at}：清理任务读不了录制目录（权限），什么都没删。`, `Last run ${prune.ran_at}: the prune job could not read the recording folder (permissions) and deleted nothing.`), warn: true };
  }
  if (prune.stale) {
    return { message: text(`上次清理 ${prune.ran_at}——超过 3 小时没跑（正常每 30 分钟一轮），清理链可能停了。`, `Last prune ${prune.ran_at} — over 3 hours ago (it normally runs every 30 min); the prune chain may have stopped.`), warn: true };
  }
  if (prune.state === "no_data_dir") {
    return { message: text(`上次清理 ${prune.ran_at}：还没有录制数据目录。`, `Last prune ${prune.ran_at}: no recording data folder yet.`), warn: false };
  }
  const files = prune.deleted_files ?? 0;
  const freed = formatBytes(prune.deleted_bytes ?? 0);
  return {
    message: files > 0
      ? text(`上次清理 ${prune.ran_at}：删了 ${files} 个媒体文件，腾出 ${freed}。`, `Last prune ${prune.ran_at}: deleted ${files} media files, freed ${freed}.`)
      : text(`上次清理 ${prune.ran_at}：没有超期的媒体文件。`, `Last prune ${prune.ran_at}: nothing was past the retention window.`),
    warn: false,
  };
}

function UsageRows({ usage, text }: { usage: StorageUsage; text: Text }) {
  if (usage.state === "missing") {
    return <p className="settings-helper">{text(`还没有录制数据（${usage.dir} 不存在）。`, `No recording data yet (${usage.dir} does not exist).`)}</p>;
  }
  if (usage.state === "error") {
    return <p className="settings-warning">{text(`读不了 ${usage.dir}：${usage.error ?? ""}`, `Cannot read ${usage.dir}: ${usage.error ?? ""}`)}</p>;
  }
  if (usage.state === "scanning" || !usage.bytes) {
    return <p className="settings-helper" role="status">{text("正在统计磁盘占用…", "Measuring disk usage…")}</p>;
  }
  const b = usage.bytes;
  return (
    <ul className="settings-helper storage-breakdown">
      <li data-kind="total"><strong>{formatBytes(b.total)}</strong> {text("总计", "total")} — {usage.dir}</li>
      {/* 两类分开报不是装饰：文本每天几十 KB，几个 GB 全在媒体上——「媒体删、文本永久留」只有分开才说得清 */}
      <li data-kind="media">{text("原始媒体（帧 / 音频片段）", "Raw media (frames / audio clips)")}：{formatBytes(b.media)}　{text("按下面的保留期清理", "pruned by the retention window below")}</li>
      <li data-kind="index">{text("文本索引（OCR + 转写，db.sqlite）", "Text index (OCR + transcripts, db.sqlite)")}：{formatBytes(b.index)}　{text("永久保留，清理任务不碰", "kept indefinitely; the prune job never touches it")}</li>
      {b.other > 0 && <li data-kind="other">{text("其它", "Other")}：{formatBytes(b.other)}</li>}
      {usage.truncated && <li data-kind="truncated">{text("文件太多，只统计了前一部分——这个数偏小。", "Too many files; only the first batch was measured — this number is low.")}</li>}
    </ul>
  );
}

export function StorageSection() {
  const { text } = useI18n();
  const [snap, setSnap] = useState<StorageSettings | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<{ kind: "ok" | "error"; message: string } | null>(null);
  const [isSaving, setSaving] = useState(false);
  const polls = useRef(0);

  const load = useCallback(async (refresh: boolean) => {
    try {
      const fresh = await fetchStorageSettings(refresh);
      setSnap(fresh);
      setDraft(String(fresh.media_retention_minutes));
    } catch (err) {
      setNote({ kind: "error", message: errorMessage(err) });
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  // 扫描还没出结果就轮询；到了 SCAN_POLLS 次就停（不把一个慢目录变成永不停歇的定时器）
  useEffect(() => {
    if (!snap || snap.usage.state !== "scanning" || polls.current >= SCAN_POLLS) return undefined;
    const timer = window.setTimeout(() => {
      polls.current += 1;
      void load(false);
    }, SCAN_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [snap, load]);

  async function save() {
    if (!snap) return;
    const n = Number(draft.trim());
    setSaving(true);
    setNote(null);
    try {
      const fresh = await putStorageSettings({ media_retention_minutes: Number.isFinite(n) ? n : (draft.trim() as unknown as number) });
      setSnap(fresh);
      setDraft(String(fresh.media_retention_minutes));
      setNote({
        kind: "ok",
        message: text(`已保存：原始媒体保留 ${fresh.media_retention_minutes} 分钟，下一轮清理（每 30 分钟一轮）生效。`,
          `Saved: raw media kept for ${fresh.media_retention_minutes} minutes; effective on the next prune round (every 30 min).`),
      });
    } catch (err) {
      setNote({ kind: "error", message: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }

  if (!snap) {
    return (
      <div className="settings-field storage-block">
        <p className="settings-helper" role="status">{text("正在读取磁盘占用…", "Loading disk usage…")}</p>
        {note && <p className="settings-warning" role="alert">{note.message}</p>}
      </div>
    );
  }

  const growth = growthSentence(snap.growth, text);
  const prune = pruneSentence(snap.prune, text);
  const dirty = draft.trim() !== String(snap.media_retention_minutes);
  return (
    <div className="settings-field storage-block" data-usage-state={snap.usage.state}>
      <div className="settings-field-head">
        <label className="settings-knob-label" htmlFor="storage-retention">{text("磁盘占用与保留期", "Disk usage and retention")}</label>
      </div>
      <UsageRows usage={snap.usage} text={text} />
      {growth && <p className="settings-helper storage-growth">{growth}</p>}
      <p className={prune.warn ? "settings-warning storage-prune" : "settings-helper storage-prune"} role={prune.warn ? "alert" : undefined}>{prune.message}</p>
      <div className="settings-knob-controls">
        <input
          id="storage-retention"
          className="settings-input settings-input-short"
          type="number"
          min={snap.bounds.min}
          max={snap.bounds.max}
          step={5}
          value={draft}
          disabled={isSaving}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter" && dirty) void save(); }}
        />
        <span className="settings-helper">
          {text(`分钟——原始帧与音频片段活这么久就删（${snap.bounds.min}–${snap.bounds.max}；默认 ${snap.bounds.default}）。OCR 文本与转写不受影响，永久留在 db.sqlite。`,
            `minutes — raw frames and audio clips are deleted once they are this old (${snap.bounds.min}–${snap.bounds.max}; default ${snap.bounds.default}). OCR text and transcripts are unaffected and stay in db.sqlite forever.`)}
        </span>
        <button type="button" className="btn" disabled={!dirty || isSaving} onClick={() => void save()}>
          {isSaving ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        <button type="button" className="btn btn-quiet" disabled={snap.usage.scanning} onClick={() => { polls.current = 0; void load(true); }}>
          {text("重新统计", "Re-measure")}
        </button>
      </div>
      {note && <p className={note.kind === "ok" ? "settings-helper is-ok" : "settings-warning"} role={note.kind === "ok" ? "status" : "alert"}>{note.message}</p>}
    </div>
  );
}
