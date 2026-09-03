// 语气档案区的「当前生效」状态行（原生 Settings.swift voiceGroup 的 1) 2)；docs/VOICE.md；§68.1 追记）：
// 状态词四选一逐字镜像 voiceStatusText——已停用 / 你的私有档案 / 出厂默认（作者风格）/ 无档案（不注入）——
// + 生效文件路径（$HOME 缩成 ~）+「打开档案」（POST /api/reveal {target:"voice_profile"}：server 定位此刻生效、
// 或重开后会生效的那个文件；两个都不在时按钮禁用）。开关本身是目录字段 voice_enabled（CatalogSection 渲）；
// 状态随目录的 effective 即时变（草稿未保存不算）。原生「从我的消息生成/更新档案」（几分钟的 act.voice_gen）另 PR。
import { useEffect, useState } from "react";
import { fetchVoiceProfile, postRevealTarget } from "../../api";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import type { VoiceProfileStatus } from "../../types";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

/** 原生 voiceStatusText：关 → 已停用；私有 > 出厂 > 无 */
export function voiceStatusText(status: VoiceProfileStatus, enabled: boolean, text: Text): string {
  if (!enabled) return text("已停用", "Disabled");
  if (status.private_exists) return text("你的私有档案", "Your private profile");
  if (status.default_exists) return text("出厂默认（作者风格）", "Shipped default (author's style)");
  return text("无档案（不注入）", "No profile (nothing injected)");
}

function abbreviateHome(path: string): string {
  const m = /^(\/Users\/[^/]+)(\/.*)?$/.exec(path);
  return m ? `~${m[2] ?? ""}` : path;
}

export function VoiceStatus() {
  const { text } = useI18n();
  const { settingsCatalog } = useAppState();
  const [status, setStatus] = useState<VoiceProfileStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const field = settingsCatalog?.sections.find((s) => s.id === "voice")?.fields.find((f) => f.key === "voice_enabled");

  useEffect(() => {
    let cancelled = false;
    fetchVoiceProfile().then((s) => { if (!cancelled) setStatus(s); }).catch((err) => { if (!cancelled) setNote(errorMessage(err)); });
    return () => { cancelled = true; };
  }, []);

  if (!status) return note ? <p className="settings-warning" role="alert">{note}</p> : null;
  const enabled = field ? field.effective !== false : status.enabled;
  const tone = !enabled ? "" : status.private_exists ? " is-ok" : status.default_exists ? " is-info" : " is-warning";

  async function open() {
    setNote(null);
    try {
      await postRevealTarget("voice_profile");
    } catch (err) {
      setNote(errorMessage(err));
    }
  }

  return (
    <div className="settings-field is-string voice-status">
      <div className="settings-field-head">
        <span className="settings-knob-label">{text("当前生效", "In effect")}</span>
        <span className={`settings-source-chip${tone}`}>{voiceStatusText(status, enabled, text)}</span>
      </div>
      <div className="settings-knob-controls">
        {status.effective_path && <code className="settings-global-path">{abbreviateHome(status.effective_path)}</code>}
        <button type="button" className="btn" disabled={!status.effective_path} onClick={() => void open()}>{text("打开档案", "Open profile")}</button>
      </div>
      {note && <p className="settings-warning" role="alert">{note}</p>}
    </div>
  );
}
