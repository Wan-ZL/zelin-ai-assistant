// 实时字幕区（§36 / §61 / §68.2 及其追记）：原生 SettingsLiveCaptions.swift 的 web 版——开关 + 识别引擎 /
// 声音来源 / 本地识别语言 / 翻译开关与方向 / Ark 模型 / 字号 / 不透明度，八个偏好经桥的
// setCaptionPrefs 写进壳的 UserDefaults（captions* 键，只有原生引擎读它们）；两把 BYO key
// （豆包语音 / Ark）是 §19 secrets 文件，SecretRow 经 server 写。字幕悬浮窗齿轮深链到这里
// （?page=settings&anchor=live_captions，§61.3）。无桥 = 只剩凭证行 + 一句说明。
//
// 原生的四句诚实说明（§61.1 追记 (c) 立的 wire，本区消费；壳侧已本地化，页面只在非空时渲）：
//   - 状态行按 CaptionDisplayState.statusLine 的先后（review G）：已暂停 > 引擎状态 / 错误 > 无行——
//     暂停是用户意图，引擎的 status_text 在暂停时是 ""，不能让行消失得像在正常听；
//   - source_note（音源部分不可用的降级句）橙色挂在「声音来源」下、暂停时不出（原生 !paused 同款）；
//   - translation_note（翻译走不通的原因 / Ark 途中报错）次要色挂在翻译开关下；在不在翻只看 translation_active；
//   - 引擎脚注两支按 apple_engine_available 选句（normalize 缺席补 false → 「没有 Apple 本地识别可用」那支）。
// 其余脚注（豆包中英混识 / 两种凭证格式 / 两个控制台 + 只存本机 / 费用与「字幕文本永不离开这台 Mac」）
// 是 copy：文字自己的，事实照原生（§66.2 只列不判）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState, type ShellCaptionsState } from "../../shellBridge";
import { SecretRow } from "./SecretRow";

type Prefs = {
  engine: string; source: string; translate: boolean; translate_direction: string;
  apple_locale: string; ark_model: string; font_size: number; opacity: number;
};

type Text = (zh: string, en: string) => string;

/** 原生 CaptionOverlayView.pausedLabel + SettingsLiveCaptions 拼的尾句（两条清单标签，各占一个节点） */
function pausedStatusParts(text: Text): [string, string] {
  return [
    text("已暂停 — 未在采集，也不计费", "Paused — nothing is captured or billed"),
    text("；在悬浮窗上点 ▶ 继续", " — click ▶ on the overlay to resume"),
  ];
}

/**
 * 原生 CaptionDisplayState.statusLine（CaptionCore.swift，review G）：已暂停永远压过引擎状态（永不在没采集时
 * 声称在听）；然后是引擎状态 / 错误；都没有 = 正常在听，不出行。
 */
export function captionsStatusLine(
  cap: Pick<ShellCaptionsState, "paused" | "status_text" | "status_is_error">,
  text: Text,
): { parts: string[]; isError: boolean } | null {
  if (cap.paused) return { parts: pausedStatusParts(text), isError: false };
  if (!cap.status_text) return null;
  return { parts: [cap.status_text], isError: cap.status_is_error };
}

/** 原生 SettingsLiveCaptions.engineFootnote：有没有 Apple 本地引擎（macOS 26+）决定「自动」怎么解释 */
export function engineFootnote(appleEngineAvailable: boolean, text: Text): string {
  if (appleEngineAvailable) {
    return text("自动 = 有豆包 Key 就用豆包（中英混识、标点更好），否则用 Apple 本地（免费离线）。",
      "Auto = Doubao when a key is saved (better zh/en mixing and punctuation), otherwise Apple on-device (free, offline).");
  }
  return text("自动 = 有豆包 Key 就用豆包。这台 Mac 低于 macOS 26，没有 Apple 本地识别可用。",
    "Auto = Doubao when a key is saved. This Mac is below macOS 26, so Apple on-device recognition is unavailable.");
}

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

  const status = cap ? captionsStatusLine(cap, text) : null;
  const sourceNote = cap && !cap.paused ? (cap.source_note ?? "") : "";
  const translationNote = cap?.translation_note ?? "";

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
              {status && (
                <span className={`settings-helper captions-status${status.isError ? " is-warning" : ""}`} data-paused={cap.paused ? "true" : undefined}>
                  {status.parts.map((part, i) => <span key={i}>{part}</span>)}
                </span>
              )}
            </div>
          </div>
          {select("engine", text("识别引擎", "Recognition engine"), [["auto", text("自动", "Auto")], ["doubao", text("豆包在线", "Doubao (online)")], ["apple", text("Apple 本地", "Apple on-device")]])}
          <p className="settings-helper captions-engine-footnote">{engineFootnote(cap.apple_engine_available === true, text)}</p>
          {select("source", text("声音来源", "Audio source"), [["both", text("麦克风 + 系统声音", "Mic + system audio")], ["mic", text("仅麦克风", "Microphone only")], ["system", text("仅系统声音", "System audio only")]])}
          {sourceNote && <p className="settings-helper is-warning captions-source-note">{sourceNote}</p>}
          <p className="settings-helper">{text("系统声音走「屏幕录制」权限；麦克风首次开启时弹系统授权。", "System audio rides the Screen Recording grant; the mic prompts on first enable.")}</p>
          {select("apple_locale", text("本地识别语言（仅 Apple 引擎）", "On-device language (Apple engine only)"), [["zh", text("中文", "Chinese")], ["en", "English"]])}
          <p className="settings-helper">{text("豆包引擎自动中英混识，无需选择。", "The Doubao engine code-switches zh/en automatically — no choice needed.")}</p>
          <div className="settings-field is-bool">
            <div className="settings-field-head">
              <label className="settings-knob-label" htmlFor="captions-translate">{text("翻译字幕（需要 Ark Key + 豆包引擎）", "Translate captions (needs the Ark key + Doubao engine)")}</label>
            </div>
            <div className="settings-knob-controls">
              <input id="captions-translate" type="checkbox" role="switch" className="settings-switch" checked={cap.translate} onChange={(e) => void set({ translate: e.target.checked })} />
            </div>
          </div>
          {translationNote && <p className="settings-helper captions-translation-note">{translationNote}</p>}
          {select("translate_direction", text("翻译方向", "Translation direction"), [["auto", text("自动（按句判断）", "Auto (per sentence)")], ["zh2en", text("中 → 英", "zh → en")], ["en2zh", text("英 → 中", "en → zh")]])}
          <div className="settings-field is-string">
            <label className="settings-knob-label" htmlFor="captions-ark-model">{text("翻译模型（Ark model ID）", "Translation model (Ark model ID)")}</label>
            <div className="settings-knob-controls">
              <input id="captions-ark-model" className="settings-input" type="text" defaultValue={cap.ark_model} placeholder="doubao-seed-1-6-flash" spellCheck={false}
                onBlur={(e) => { if (e.target.value.trim() && e.target.value.trim() !== cap.ark_model) void set({ ark_model: e.target.value.trim() }); }} />
            </div>
          </div>
          <div className="settings-field is-number">
            <label className="settings-knob-label" htmlFor="captions-font"><span>{text("字号", "Font size")}</span><span className="settings-range-value">{cap.font_size}</span></label>
            <div className="settings-knob-controls">
              <input id="captions-font" type="range" min={14} max={40} step={1} value={cap.font_size} onChange={(e) => void set({ font_size: Number(e.target.value) })} />
              <label htmlFor="captions-opacity" className="settings-knob-label"><span>{text("背景不透明度", "Background opacity")}</span><span className="settings-range-value">{Math.round(cap.opacity * 100)}%</span></label>
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
        helper={text("支持两种：新版控制台的 API Key，或旧版的 App ID + Access Token——最稳是粘成一行 \"AppID:Token\"（带 \"App ID:\"、\"Access Token:\" 等标签也认）；从控制台连标签一起粘两行通常也行（输入框会把换行拍扁，靠标签分辨）。", "Two formats work: the new-console API key, or the legacy App ID + Access Token — most reliable as one \"AppID:Token\" line (labels like \"App ID:\" / \"Access Token:\" are recognized too); pasting both console lines with their labels usually works as well (the box flattens the line break; the labels tell them apart).")}
      />
      <SecretRow
        name="volcano-ark-key.txt"
        links={[{ label: text("Ark 控制台", "Ark console"), href: "https://console.volcengine.com/ark" }]}
        helper={text("两个凭证来自火山引擎的两个不同控制台：语音凭证管识别，Ark Key 管翻译。都只存本机 config/secrets/（页面永不回显），保存后在开启字幕时生效。保存只存本机、不联网；点「检测」才真连一次对应服务器验证。", "The two credentials come from two different Volcano consoles: the speech credential does recognition, the Ark key does translation. Both live only in local config/secrets/ (never echoed back to the page) and take effect when captions start. Saving stores locally without any network call; clicking Test makes one real connection to the matching server.")}
      />
      <p className="settings-helper captions-costs">
        {text("费用（都是你自己的账号）：豆包流式识别约 ¥1/小时，个人实名开通即送 20 小时；翻译走 doubao-seed flash，一小时字幕的翻译费通常不到 ¥0.1，Ark 每个模型另送 50 万 token。Apple 本地引擎完全免费离线（需 macOS 26+）。字幕文本永不离开这台 Mac，只发往你自己开通的识别/翻译服务；本产品的匿名统计里也永远没有字幕内容。", "Costs (all on your own account): Doubao streaming ASR ≈ ¥1/hour with 20 free hours after personal sign-up; translation via doubao-seed flash usually costs under ¥0.1 per captioned hour, and Ark grants 500k free tokens per model. The Apple on-device engine is fully free and offline (macOS 26+). Caption text never leaves this Mac except to your own recognition/translation endpoints, and never appears in this product's anonymous telemetry.")}
      </p>
    </section>
  );
}
