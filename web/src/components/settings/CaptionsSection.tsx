// 实时字幕区（§36 / §61 / §68.2）：原生 SettingsLiveCaptions.swift 的 web 版——开关 + 识别引擎 /
// 声音来源 / 本地识别语言 / 翻译开关与方向 / Ark 模型 / 字号 / 不透明度，八个偏好经桥的
// setCaptionPrefs 写进壳的 UserDefaults（captions* 键，只有原生引擎读它们）；两把 BYO key
// （豆包语音 / Ark）是 §19 secrets 文件，SecretRow 经 server 写。字幕悬浮窗齿轮深链到这里
// （?page=settings&anchor=live_captions，§61.3）。无桥 = 只剩凭证行 + 一句说明。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState } from "../../shellBridge";
import { SecretRow } from "./SecretRow";

type Prefs = {
  engine: string; source: string; translate: boolean; translate_direction: string;
  apple_locale: string; ark_model: string; font_size: number; opacity: number;
};

export function CaptionsSection() {
  const { text } = useI18n();
  const state = useShellState();
  const [error, setError] = useState<string | null>(null);
  const present = hasShellBridge();
  const cap = state?.captions;

  async function set(patch: Partial<Prefs>) {
    setError(null);
    try {
      await callShell("setCaptionPrefs", patch);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const select = (key: keyof Prefs, label: string, options: Array<[string, string]>) => cap && (
    <div className="settings-field is-enum">
      <label className="settings-knob-label" htmlFor={`captions-${key}`}>{label}</label>
      <div className="settings-knob-controls">
        <select id={`captions-${key}`} className="settings-select" value={String(cap[key])} onChange={(e) => void set({ [key]: e.target.value } as Partial<Prefs>)}>
          {options.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select>
      </div>
    </div>
  );

  return (
    <section className="settings-section" id="settings-live_captions" aria-labelledby="settings-live_captions-title">
      <h3 id="settings-live_captions-title" className="settings-section-title">{text("实时字幕", "Live captions")}</h3>
      <p className="settings-helper">
        {text("歌词式置顶字幕：实时转写麦克风和/或系统声音，可选同传翻译。也可从看板右上角开关。", "Lyrics-style always-on-top captions: live transcription of the mic and/or system audio, optional translation. Also toggleable from the board header.")}
      </p>
      {present && cap ? (
        <>
          <div className="settings-field is-bool">
            <div className="settings-field-head">
              <label className="settings-knob-label" htmlFor="captions-on">{text("开启实时字幕悬浮窗", "Show the live-captions overlay")}</label>
            </div>
            <div className="settings-knob-controls">
              <input id="captions-on" type="checkbox" role="switch" className="settings-switch" checked={cap.on} onChange={(e) => void callShell("setCaptions", { on: e.target.checked }).catch((err) => setError(String(err)))} />
              {cap.status_text && <span className={`settings-helper${cap.status_is_error ? " is-warning" : ""}`}>{cap.status_text}</span>}
            </div>
          </div>
          {select("engine", text("识别引擎", "Recognition engine"), [["auto", text("自动", "Auto")], ["doubao", text("豆包在线", "Doubao (online)")], ["apple", text("Apple 本地", "Apple on-device")]])}
          {select("source", text("声音来源", "Audio source"), [["both", text("麦克风 + 系统声音", "Mic + system audio")], ["mic", text("仅麦克风", "Microphone only")], ["system", text("仅系统声音", "System audio only")]])}
          <p className="settings-helper">{text("系统声音走「屏幕录制」权限；麦克风首次开启时弹系统授权。", "System audio rides the Screen Recording grant; the mic prompts on first enable.")}</p>
          {select("apple_locale", text("本地识别语言（仅 Apple 引擎）", "On-device language (Apple engine only)"), [["zh", text("中文", "Chinese")], ["en", "English"]])}
          <div className="settings-field is-bool">
            <div className="settings-field-head">
              <label className="settings-knob-label" htmlFor="captions-translate">{text("同传翻译", "Translate")}</label>
            </div>
            <div className="settings-knob-controls">
              <input id="captions-translate" type="checkbox" role="switch" className="settings-switch" checked={cap.translate} onChange={(e) => void set({ translate: e.target.checked })} />
            </div>
          </div>
          {select("translate_direction", text("翻译方向", "Direction"), [["auto", text("自动", "Auto")], ["zh2en", "中 → EN"], ["en2zh", "EN → 中"]])}
          <div className="settings-field is-string">
            <label className="settings-knob-label" htmlFor="captions-ark-model">{text("Ark 翻译模型", "Ark translation model")}</label>
            <div className="settings-knob-controls">
              <input id="captions-ark-model" className="settings-input" type="text" defaultValue={cap.ark_model} spellCheck={false}
                onBlur={(e) => { if (e.target.value.trim() && e.target.value.trim() !== cap.ark_model) void set({ ark_model: e.target.value.trim() }); }} />
            </div>
          </div>
          <div className="settings-field is-number">
            <label className="settings-knob-label" htmlFor="captions-font">{text(`字号 ${cap.font_size}`, `Font size ${cap.font_size}`)}</label>
            <div className="settings-knob-controls">
              <input id="captions-font" type="range" min={14} max={40} step={1} value={cap.font_size} onChange={(e) => void set({ font_size: Number(e.target.value) })} />
              <label htmlFor="captions-opacity" className="settings-knob-label">{text(`不透明度 ${Math.round(cap.opacity * 100)}%`, `Opacity ${Math.round(cap.opacity * 100)}%`)}</label>
              <input id="captions-opacity" type="range" min={0.2} max={1} step={0.05} value={cap.opacity} onChange={(e) => void set({ opacity: Number(e.target.value) })} />
            </div>
          </div>
          {error && <p className="settings-warning" role="alert">{error}</p>}
        </>
      ) : (
        <p className="settings-warning">{text("字幕引擎与偏好只在看板 app（壳）里可控；这里只能保存凭证。", "The captions engine and its preferences are only controllable inside the board app; only the credentials can be saved here.")}</p>
      )}
      <div className="settings-subhead">{text("凭证（BYO key）", "Credentials (BYO keys)")}</div>
      <SecretRow
        name="volcano-speech-key.txt"
        links={[{ label: text("语音控制台", "Speech console"), href: "https://console.volcengine.com/speech/app" }]}
        helper={text("新版控制台 API Key，或旧版 App ID + Access Token 粘成一行 \"AppID:Token\"。", "New-console API key, or legacy App ID + Access Token as one \"AppID:Token\" line.")}
      />
      <SecretRow
        name="volcano-ark-key.txt"
        links={[{ label: text("Ark 控制台", "Ark console"), href: "https://console.volcengine.com/ark" }]}
        helper={text("翻译用，另一个 Key。", "For translation — a different key.")}
      />
    </section>
  );
}
