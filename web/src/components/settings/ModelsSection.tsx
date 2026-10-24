// 设置页 section「模型」（CONTRACT §59，owner 决策 D22 + D53）。
// 两把旋钮：pipeline（「脑」——会议纪要与笔记整理的 headless claude -p）= 出厂值 | canonical id | 自定义；
// fallback（D53，「回退」——主模型不可用时每次 headless 调用带的 --fallback-model）= 关闭 | 出厂值 | canonical id | 自定义
// （自定义框留空 = 出厂值，不是 off——off 只经「关闭回退」显式选项）。v2.0.0：dispatch（「手」，claude --bg 派工）随 Act
// 管线退役；「跟随 Claude Code 全局默认」与「设为全局默认」随 §78 隔离退役（app 不读不写 owner 自己的 Claude Code 设置）。
// 数据经 store（refreshSettings / saveModels）；这里只存草稿 + toast 这类瞬态。
// 保存 = 一次 PUT 两键；server 校验失败（400 INVALID_FIELD 等）的整句原文以 toast 显示。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSettings, saveModels, useAppState } from "../../store";
import type { ModelsSettings } from "../../types";
import { CUSTOM_CHOICE, ModelKnob } from "./ModelKnob";

const MODES = ["pipeline", "fallback"] as const;
type Mode = (typeof MODES)[number];
type Draft = Record<Mode, { value: string; isCustom: boolean }>;

/** 每把旋钮的出厂值（server 给；老 server 缺 pipeline_default 时退回第一个 canonical） */
function defaultOf(models: ModelsSettings, mode: Mode): string {
  return mode === "fallback" ? models.fallback_default : (models.pipeline_default ?? models.canonical[0] ?? "");
}

/** 自定义框留空的落点 = 出厂值——与 server「空白 = 出厂值」同一规则，留空是漏填不是决定；off 只经「关闭回退」显式选项到达 */
function blankCustomOf(models: ModelsSettings, mode: Mode): string {
  return defaultOf(models, mode);
}

/** 下拉里的固定选项（「自定义…」之外）：fallback 的哨兵 off + 出厂值 + canonical */
function isFixedChoice(models: ModelsSettings, mode: Mode, value: string): boolean {
  if (mode === "fallback" && value === models.off) return true;
  if (value === defaultOf(models, mode)) return true;
  return models.canonical.includes(value);
}

const TOAST_MS = 6000;

interface Toast {
  kind: "ok" | "error";
  message: string;
}

function draftFrom(models: ModelsSettings): Draft {
  const one = (mode: Mode) => ({
    value: models[mode],
    isCustom: !isFixedChoice(models, mode, models[mode]),
  });
  return { pipeline: one("pipeline"), fallback: one("fallback") };
}

export function ModelsSection() {
  const { text } = useI18n();
  const { models, settingsError } = useAppState();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    void refreshSettings();
  }, []);

  // server 快照到了 / 保存回执到了 → 草稿对齐（草稿只在用户编辑期间领先于 server）
  useEffect(() => {
    if (models) setDraft(draftFrom(models));
  }, [models]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  if (settingsError && !models) {
    return (
      <section className="settings-section">
        <h3 className="settings-section-title">{text("模型", "Models")}</h3>
        <p className="settings-error" role="alert">{settingsError}</p>
      </section>
    );
  }
  if (!models || !draft) {
    return (
      <section className="settings-section">
        <h3 className="settings-section-title">{text("模型", "Models")}</h3>
        <p className="settings-helper">{text("读取中…", "Loading…")}</p>
      </section>
    );
  }

  const choose = (mode: Mode, choice: string) => {
    setDraft((d) => d && {
      ...d,
      [mode]: choice === CUSTOM_CHOICE
        ? { value: d[mode].isCustom ? d[mode].value : "", isCustom: true }
        : { value: choice, isCustom: false },
    });
  };
  const customText = (mode: Mode, value: string) => {
    setDraft((d) => d && { ...d, [mode]: { value, isCustom: true } });
  };

  const effective = (mode: Mode): string => {
    const entry = draft[mode];
    if (!entry.isCustom) return entry.value;
    const typed = entry.value.trim();
    return typed || blankCustomOf(models, mode);
  };
  const isDirty = MODES.some((mode) => effective(mode) !== models[mode]);

  async function save() {
    setSaving(true);
    setToast(null);
    try {
      const saved = await saveModels({
        pipeline: effective("pipeline"),
        fallback: effective("fallback"),
      });
      const warn = saved.warnings.length ? ` · ${saved.warnings.join(" ")}` : "";
      setToast({
        kind: "ok",
        message: text(`已保存，下一次调用即生效，无需重启。${warn}`, `Saved — applies to the next call, no restart needed.${warn}`),
      });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="settings-models-title">
      <h3 id="settings-models-title" className="settings-section-title">{text("模型", "Models")}</h3>
      <p className="settings-helper">
        {text(
          "两把旋钮。「脑」= 会议纪要与笔记整理用哪个模型（每次 headless 调用带 --model）；「回退」= 主模型不可用时切到哪个模型（每次调用带 --fallback-model）。两把都有出厂值，不跟随你自己的 Claude Code 设置。",
          "Two knobs. \"Brain\" = the model meeting recaps and note processing run on (every headless call passes --model); \"fallback\" = the model a call switches to when the primary is unavailable (--fallback-model). Both have factory defaults and never follow your own Claude Code settings.",
        )}
      </p>

      <ModelKnob
        mode="pipeline"
        label={text("管线判断（脑）", "Pipeline judgment (brain)")}
        helper={text(
          "所有 headless claude -p 用哪个模型：会议纪要、笔记整理、权限体检里的模型探针。",
          "Which model every headless claude -p uses: meeting recaps, note processing, the model probe on the Permissions checkup.",
        )}
        value={draft.pipeline.value}
        canonical={models.canonical}
        defaultChoice={defaultOf(models, "pipeline")}
        isCustom={draft.pipeline.isCustom}
        onChoose={(choice) => choose("pipeline", choice)}
        onCustomText={(value) => customText("pipeline", value)}
      />
      <ModelKnob
        mode="fallback"
        label={text("回退模型", "Fallback model")}
        helper={text(
          "上面那把旋钮的模型拿不到时，claude 在本次会话里切到这个模型。关闭 = 不传 --fallback-model。",
          "When the model above is unavailable, claude switches to this model for the session. Off = no --fallback-model is passed.",
        )}
        value={draft.fallback.value}
        canonical={models.canonical}
        sentinel={{ value: models.off, label: text("关闭回退（主模型不可用时直接失败）", "No fallback (fail when the primary is unavailable)") }}
        defaultChoice={models.fallback_default}
        isCustom={draft.fallback.isCustom}
        onChoose={(choice) => choose("fallback", choice)}
        onCustomText={(value) => customText("fallback", value)}
      />

      {models.warnings.length > 0 && !isDirty && (
        <ul className="settings-warning-list">
          {models.warnings.map((w) => <li key={w} className="settings-warning">{w}</li>)}
        </ul>
      )}

      <div className="settings-actions">
        <button type="button" className="btn btn-primary" disabled={!isDirty || isSaving} onClick={() => void save()}>
          {isSaving ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        <span className="settings-helper">
          {text("保存后下一次调用即生效，无需重启。", "Applies to the next call after saving; no restart.")}
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
