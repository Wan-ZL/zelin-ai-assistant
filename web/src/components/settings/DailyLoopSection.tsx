// 设置页 section「每日整理」（CONTRACT §62，owner 决策 D10）。
// 五把旋钮：开关 / 解锁时刻（本地 HH:MM）/ 每天最多几张 🤖 提案（默认 5）/ 过时天数 / 循环卡回收站保留天数。
// 数据经 store（refreshDailyLoop / saveDailyLoop）；这里只存草稿 + toast。保存 = 一次 PUT 改动过的键；
// server 校验失败（400 INVALID_FIELD 等）的整句原文以 toast 显示。下一个 actd pass 生效，无需重启。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { useI18n } from "../../i18n";
import { refreshDailyLoop, saveDailyLoop, useAppState } from "../../store";
import type { DailyLoopPatch, DailyLoopSettings } from "../../types";

const TOAST_MS = 6000;
const NUMERIC_FIELDS = ["max_proposals_per_day", "stale_days", "trash_retention_days"] as const;
type NumericField = (typeof NUMERIC_FIELDS)[number];

interface Draft {
  enabled: boolean;
  time: string;
  max_proposals_per_day: string;
  stale_days: string;
  trash_retention_days: string;
}

interface Toast {
  kind: "ok" | "error";
  message: string;
}

function draftFrom(s: DailyLoopSettings): Draft {
  return {
    enabled: s.enabled,
    time: s.time,
    max_proposals_per_day: String(s.max_proposals_per_day),
    stale_days: String(s.stale_days),
    trash_retention_days: String(s.trash_retention_days),
  };
}

/** 草稿 → 只含改动键的 PUT body（数字字段原样送字符串给 server 校验，不在客户端猜） */
export function diffPatch(draft: Draft, current: DailyLoopSettings): DailyLoopPatch {
  const patch: DailyLoopPatch = {};
  if (draft.enabled !== current.enabled) patch.enabled = draft.enabled;
  if (draft.time.trim() !== current.time) patch.time = draft.time.trim();
  for (const field of NUMERIC_FIELDS) {
    if (draft[field].trim() !== String(current[field])) {
      const n = Number(draft[field].trim());
      patch[field] = Number.isFinite(n) ? n : (draft[field] as unknown as number);
    }
  }
  return patch;
}

export function DailyLoopSection() {
  const { text } = useI18n();
  const { dailyLoop, dailyLoopError } = useAppState();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    void refreshDailyLoop();
  }, []);

  useEffect(() => {
    if (dailyLoop) setDraft(draftFrom(dailyLoop));
  }, [dailyLoop]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  const title = text("每日整理", "Daily tidy-up");
  if (dailyLoopError && !dailyLoop) {
    return (
      <section className="settings-section">
        <h3 className="settings-section-title">{title}</h3>
        <p className="settings-error" role="alert">{dailyLoopError}</p>
      </section>
    );
  }
  if (!dailyLoop || !draft) {
    return (
      <section className="settings-section">
        <h3 className="settings-section-title">{title}</h3>
        <p className="settings-helper">{text("读取中…", "Loading…")}</p>
      </section>
    );
  }

  const patch = diffPatch(draft, dailyLoop);
  const isDirty = Object.keys(patch).length > 0;

  async function save() {
    setSaving(true);
    setToast(null);
    try {
      await saveDailyLoop(patch);
      setToast({
        kind: "ok",
        message: text("已保存，下一个后台 pass 生效，无需重启。", "Saved — applies on the next daemon pass, no restart needed."),
      });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setSaving(false);
    }
  }

  const numeric = (field: NumericField, label: string, helper: string) => (
    <div className="settings-knob" key={field}>
      <label className="settings-knob-label" htmlFor={`daily-loop-${field}`}>{label}</label>
      <div className="settings-knob-controls">
        <input
          id={`daily-loop-${field}`}
          className="settings-input settings-input-short"
          type="number"
          min={0}
          step={1}
          value={draft[field]}
          onChange={(event) => setDraft((d) => d && { ...d, [field]: event.target.value })}
        />
        <span className="settings-helper">{helper}</span>
      </div>
    </div>
  );

  return (
    <section className="settings-section" aria-labelledby="settings-daily-loop-title">
      <h3 id="settings-daily-loop-title" className="settings-section-title">{title}</h3>
      <p className="settings-helper">
        {text(
          "每天固定时刻，后台服务先整理看板（提案列与潜在任务列：同主题多卡合成一张新卡、过时卡进回收站——都可撤销），再从日志、doctor、GitHub issue / PR 和素材库里挑最多 N 条改进，铸成 🤖 提案卡等你审批。运行中 / 待验收 / 已交付的卡永不被碰。",
          "Once a day the daemon first tidies the board (proposal + backlog lanes: same-topic cards become one new card, stale cards go to the trash — all undoable), then reads logs, doctor, GitHub issues / PRs and the materials box and drafts at most N improvement proposals as 🤖 cards for your approval. Running / review / delivered cards are never touched.",
        )}
      </p>

      <div className="settings-knob">
        <label className="settings-knob-label settings-checkbox">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => setDraft((d) => d && { ...d, enabled: event.target.checked })}
          />
          {text("启用每日整理与提案", "Enable the daily tidy-up and proposals")}
        </label>
      </div>

      <div className="settings-knob">
        <label className="settings-knob-label" htmlFor="daily-loop-time">{text("每天几点（本地时间）", "Time of day (local)")}</label>
        <div className="settings-knob-controls">
          <input
            id="daily-loop-time"
            className="settings-input settings-input-short"
            type="text"
            inputMode="numeric"
            placeholder="03:30"
            value={draft.time}
            onChange={(event) => setDraft((d) => d && { ...d, time: event.target.value })}
            spellCheck={false}
          />
          <span className="settings-helper">
            {text("HH:MM；到点后的第一个 pass 跑，一天只跑一次。", "HH:MM; runs on the first pass after this time, once a day.")}
          </span>
        </div>
      </div>

      {numeric("max_proposals_per_day",
        text("每天最多几张提案", "Max proposals per day"),
        text("默认 5（owner 决策 D10）。0 = 只整理不提案。", "Default 5 (owner decision D10). 0 = tidy only, no proposals."))}
      {numeric("stale_days",
        text("多少天没动算过时", "Days without activity before a card is stale"),
        text("默认 45。带未来 deadline / 你改过名 / 提及 ≥3 次 / 同簇有在跑的卡一律不动；0 = 关掉这条规则。", "Default 45. Cards with a future deadline / your own title / ≥3 mentions / a running sibling are never touched; 0 = rule off."))}
      {numeric("trash_retention_days",
        text("自动清理的卡在回收站保留几天", "Days auto-cleaned cards stay in the trash"),
        text("默认 90，比手动删除的 60 天更长——你没亲眼看过它们进回收站。", "Default 90 — longer than the 60 days for manual deletes; you never saw these go in."))}

      <div className="settings-actions">
        <button type="button" className="btn btn-primary" disabled={!isDirty || isSaving} onClick={() => void save()}>
          {isSaving ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        <span className="settings-helper">
          {text("下一个后台 pass 即按新设置运行。", "The next daemon pass runs with the new settings.")}
        </span>
      </div>

      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}
