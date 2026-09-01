// 设置页 section「模型」（CONTRACT §59，owner 决策 D22）。
// 两把旋钮：dispatch（「手」——claude --bg 派工 agent）与 pipeline（「脑」——雷达提取/分诊/判官/问答
// 的 headless claude -p）。每把 = 跟随 Claude Code 全局 | canonical id | 自定义。
// 数据经 store（refreshSettings/saveModels/setClaudeCodeDefaultModel）；这里只存草稿 + toast 这类瞬态。
// 保存 = 一次 PUT 两键；server 校验失败（400 INVALID_FIELD 等）的整句原文以 toast 显示。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { useI18n } from "../../i18n";
import {
  refreshSettings,
  saveModels,
  setClaudeCodeDefaultModel,
  useAppState,
} from "../../store";
import { ClaudeCodeDefaultRow } from "./ClaudeCodeDefaultRow";
import { CUSTOM_CHOICE, ModelKnob } from "./ModelKnob";

const MODES = ["dispatch", "pipeline"] as const;
type Mode = (typeof MODES)[number];
type Draft = Record<Mode, { value: string; isCustom: boolean }>;

const TOAST_MS = 6000;

interface Toast {
  kind: "ok" | "error";
  message: string;
}

function draftFrom(models: { dispatch: string; pipeline: string; follow: string; canonical: string[] }): Draft {
  const one = (value: string) => ({
    value,
    isCustom: value !== models.follow && !models.canonical.includes(value),
  });
  return { dispatch: one(models.dispatch), pipeline: one(models.pipeline) };
}

export function ModelsSection() {
  const { text } = useI18n();
  const { models, claudeCodeDefault, settingsError } = useAppState();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [isSettingDefault, setSettingDefault] = useState(false);
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

  const follow = models.follow;
  const globalDefault = claudeCodeDefault?.model ?? null;

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
    return typed || follow;
  };
  const isDirty = MODES.some((mode) => effective(mode) !== models[mode]);

  async function save() {
    setSaving(true);
    setToast(null);
    try {
      const saved = await saveModels({ dispatch: effective("dispatch"), pipeline: effective("pipeline") });
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

  async function setDefault(model: string) {
    setSettingDefault(true);
    setToast(null);
    try {
      const backup = await setClaudeCodeDefaultModel(model);
      setToast({
        kind: "ok",
        message: backup
          ? text(`Claude Code 全局默认已设为 ${model}；原文件备份在 ${backup}`, `Claude Code global default set to ${model}; the old file is backed up at ${backup}`)
          : text(`Claude Code 全局默认已设为 ${model}（此前没有 settings.json，已新建）`, `Claude Code global default set to ${model} (settings.json did not exist; created)`),
      });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setSettingDefault(false);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="settings-models-title">
      <h3 id="settings-models-title" className="settings-section-title">{text("模型", "Models")}</h3>
      <p className="settings-helper">
        {text(
          "两把旋钮。「手」= 派出去干活的 agent；「脑」= 管线里的判断（雷达提取、分诊、并入判官、问答、摘要）。默认都跟随 Claude Code 全局默认，即不传 --model。",
          "Two knobs. \"Hands\" = the agents dispatched to do the work; \"brain\" = the pipeline's judgment calls (radar extraction, triage, merge judge, ask, digests). Both follow the Claude Code global default unless set, i.e. no --model is passed.",
        )}
      </p>

      <ModelKnob
        mode="dispatch"
        label={text("派工 agent（手）", "Dispatch agents (hands)")}
        helper={text(
          "claude --bg 启动的后台 agent 用哪个模型。改它影响批准后的每一次派工、恢复与打回。",
          "Which model the claude --bg background agents run on. Affects every dispatch, resume and rework after approval.",
        )}
        value={draft.dispatch.value}
        follow={follow}
        canonical={models.canonical}
        globalDefault={globalDefault}
        isCustom={draft.dispatch.isCustom}
        onChoose={(choice) => choose("dispatch", choice)}
        onCustomText={(value) => customText("dispatch", value)}
      />
      <ModelKnob
        mode="pipeline"
        label={text("管线判断（脑）", "Pipeline judgment (brain)")}
        helper={text(
          "所有 headless claude -p 用哪个模型：雷达提取、快速捕获分诊、合并判官、问答、digest、语气档。便宜快的模型在这里最划算。",
          "Which model every headless claude -p uses: radar extraction, quick-capture triage, merge judge, ask, digests, voice profile. A cheaper, faster model pays off most here.",
        )}
        value={draft.pipeline.value}
        follow={follow}
        canonical={models.canonical}
        globalDefault={globalDefault}
        isCustom={draft.pipeline.isCustom}
        onChoose={(choice) => choose("pipeline", choice)}
        onCustomText={(value) => customText("pipeline", value)}
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
          {text("保存后下一次派工 / 管线调用即生效，无需重启后台服务。", "Applies to the next dispatch / pipeline call after saving; no daemon restart.")}
        </span>
      </div>

      <ClaudeCodeDefaultRow
        current={claudeCodeDefault}
        canonical={models.canonical}
        isBusy={isSettingDefault}
        onSetDefault={(model) => void setDefault(model)}
      />

      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}
