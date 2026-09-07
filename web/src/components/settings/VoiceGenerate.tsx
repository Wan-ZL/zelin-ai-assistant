// 语气档案区尾的「从我的消息生成/更新档案」（原生 Settings.swift voiceGroup 4) + runVoiceGen；docs/VOICE.md；
// CONTRACT §68.1 追记 / §10 voice_generate / §49 GET /api/voice/generate-status；D47）。
// 原生在 app 进程里同步跑几分钟的 `python -m act.voice_gen`（按钮「生成中…」+ 转圈 + 结果一句）。web 没有进程可挂，
// 走既有的 inbox 路：按钮 = POST /api/actions {action:"voice_generate"} → actd 分离起 act.voice_gen --job（§44 单写者：
// server 不起子进程）→ 子进程把那一句人话写回 state/voice_gen/job.json → 这里每 3 s 轮询 GET /api/voice/generate-status，
// 忙着才轮询、停了就停。三个时钟：
//   · 点击 → actd 接手（≤ 一个 pass 间隔）之间按钮先按「生成中…」画（记住点击时看到的上一份 started_at，新回执落地即交棒）；
//     90 s 还没人接手 = 后台没在跑，诚实说一句、解锁按钮（RadarAgentPanel「立即测试一轮」同一兜底）；
//   · running 超过 15 分钟没回执 = server 判 lost（子进程崩在 import / 被杀），同样一句 + 解锁；
//   · 从 running 变到 done / failed → 重拉 store.voiceProfile（原生 runVoiceGen 收尾的 refreshVoiceProfileStatus()——
//     失败也拉：备份 / 回滚可能改了哪个文件在）。
// 结果行逐字镜像原生 voiceGenStatus：running 的那句 / done = 工具 stdout 那一句（缺席才「已生成 ✓」）/ failed = 错误原文。
// 刷新页面回来：挂载先拉一次，running 就接着忙——不靠本页内存。
import { useEffect, useRef, useState } from "react";
import { fetchVoiceGenerateStatus, postAction } from "../../api";
import { useI18n } from "../../i18n";
import { refreshVoiceProfile } from "../../store";
import type { VoiceGenJob } from "../../types";
import { errorMessage } from "./useToast";

export const POLL_MS = 3_000;
export const PICKUP_TIMEOUT_MS = 90_000;

type Text = (zh: string, en: string) => string;
type Tone = "busy" | "ok" | "warning";

/** 忙态 = 刚点了还没人接手，或 server 说 running 且没丢 */
export function isGenerating(job: VoiceGenJob | null, requested: boolean): boolean {
  return requested || (job !== null && job.status === "running" && !job.lost);
}

/** 结果行（原生 voiceGenStatus）：忙 / 成功一句 / 失败原文 / 丢了；没跑过 → null */
export function generateStatusLine(job: VoiceGenJob | null, requested: boolean, text: Text): { tone: Tone; message: string } | null {
  if (isGenerating(job, requested)) {
    return { tone: "busy", message: text("生成中……会读取你最近发出的 Slack 消息，可能需要几分钟。", "Generating… reads Slack messages you sent recently; this can take a few minutes.") };
  }
  if (!job) return null;
  if (job.status === "done") return { tone: "ok", message: job.message ?? text("已生成 ✓", "Generated ✓") };
  if (job.status === "failed") {
    return { tone: "warning", message: job.error ?? text("生成失败，没有更多输出（看 state/voice_gen/run.log）。", "Generation failed with no further output (see state/voice_gen/run.log).") };
  }
  // running 却丢了：超过 15 分钟没回执
  return { tone: "warning", message: text("这一次没有回音：超过 15 分钟没写结果（看 state/voice_gen/run.log）", "No word from this run: no result written for over 15 minutes (see state/voice_gen/run.log)") };
}

const jobKey = (job: VoiceGenJob | null): string | null => (job ? `${job.started_at ?? ""}|${job.lost ? "lost" : job.status}` : null);

export function VoiceGenerate() {
  const { text } = useI18n();
  const [job, setJob] = useState<VoiceGenJob | null>(null);
  // 点击时看到的上一份 started_at：新回执（started_at 变了）落地即交棒给 job 本身的状态
  const [requested, setRequested] = useState<{ prev: string | null } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const seenKey = useRef<string | null | undefined>(undefined); // undefined = 还没拉到第一份

  const running = isGenerating(job, requested !== null);

  async function poll() {
    try {
      const snap = await fetchVoiceGenerateStatus();
      setJob(snap.job);
    } catch (err) {
      setNote(errorMessage(err));
    }
  }

  useEffect(() => {
    void poll();
  }, []);

  useEffect(() => {
    if (requested && job && job.started_at !== requested.prev) setRequested(null);
  }, [job, requested]);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => void poll(), POLL_MS);
    return () => window.clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (!requested) return;
    const id = window.setTimeout(() => {
      setRequested(null);
      setNote(text("后台没有接手：actd 可能没在跑（看「依赖检查」区的管线活性）", "Nothing picked the request up: actd may not be running (see Pipeline liveness under Dependency check)"));
    }, PICKUP_TIMEOUT_MS);
    return () => window.clearTimeout(id);
  }, [requested]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const key = jobKey(job);
    const before = seenKey.current;
    // 第一份不算「变了」——挂载那一拍 job 还是 null、不算拉到过，哨兵要等第一份真实快照才落位
    //（否则挂载时的 done 会被当成「刚落成」，白拉一次 /api/voice——VoiceStatus 自己已经拉过）
    if (before === undefined) {
      if (job) seenKey.current = key;
      return;
    }
    seenKey.current = key;
    // 之后任何一次落成 done / failed（含从 running 交棒过来的）→ 「当前生效」行重拉
    if (before === key || !job) return;
    if (job.status === "done" || job.status === "failed") void refreshVoiceProfile();
  }, [job]);

  async function generate() {
    setNote(null);
    setRequested({ prev: job?.started_at ?? null });
    try {
      await postAction({ action: "voice_generate" });
    } catch (err) {
      setRequested(null);
      setNote(errorMessage(err));
    }
  }

  const line = generateStatusLine(job, requested !== null, text);
  const lineClass = line?.tone === "ok" ? "settings-helper is-ok" : line?.tone === "warning" ? "settings-warning" : "settings-helper";

  return (
    <div className="settings-field is-string voice-generate">
      <div className="settings-knob-controls">
        <button type="button" className="btn" disabled={running} aria-busy={running || undefined} onClick={() => void generate()}>
          {running ? text("生成中…", "Generating…") : text("从我的消息生成/更新档案", "Generate from my messages")}
        </button>
        <span className="settings-helper">{text("需要 Slack 连接；生成前会自动备份现有档案。", "Requires the Slack connection; the existing profile is backed up automatically before generating.")}</span>
      </div>
      {line && <p className={lineClass} role={line.tone === "warning" ? "alert" : "status"}>{line.message}</p>}
      {note && <p className="settings-warning" role="alert">{note}</p>}
    </div>
  );
}
