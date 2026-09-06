// 屏幕记录 · 一次性同意 / 实时状态行（原生 Permissions.swift RecordingConsentSection 的 web 版，§15 P0-11 / §68.3）：
// 权限体检页与初始设置向导第 4 步共用。没答过同意问题 → 「现在开启屏幕记录吗?」披露块（采集什么 / 去哪里 / 留多久）
// + 开启 / 暂不 / 隐私说明…（单选只开「仅屏幕」；音频留给 设置 → 录制 单独打开）；答过 → 一行状态
// （屏幕记录 · 已关闭 / 已开启,引擎未在录制 / 录制中(屏幕+音频) / 录制中(仅屏幕)）+ 开启(…) / 关闭。
// 「答过没」没有第二把标记（原生 `recordingConsentShown` 已按 §66.2 归属表退役、并入 recordingMode + setup_done.json，
// PR #186）：录制开着 = 同意过；向导写过完成标记（`GET /api/setup` done）= 问过了；否则本会话内点过 开启 / 暂不 = 答过
// （只在内存，刷新后还回来——向导是正路，app.tsx 在 needed 时本来就把看板换成向导）。引擎真相经 §61 桥；
// 浏览器里（无桥）如实说只在看板 app 里可控。文案逐字镜像 Permissions.swift:394–518。
//
// §68.3 追记（parity 批 `recording-consent-header-ui`）：「开启」是披露块的默认按钮（原生 `.keyboardShortcut(.defaultAction)`，
// Permissions.swift:416-421）——块一挂载就 autoFocus 到它，块内焦点不在按钮 / 输入框 / 链接上时 Return 也等于点它
// （preventDefault 让 §68.5 向导的 document 级 Return 让路）；「暂不」照旧只有点击 / Tab。autoFocus 只在块是本节挂载后
// **首次**展示的内容时生效：状态行出现过之后再冒出来的块（用户从状态行 / 顶栏菜单把录制关掉 → mode 回 off 的 web-only 重问）
// 不抢焦点——否则焦点会从用户正在操作的地方跳到「开启」，下一下 Space / Enter 就把录制又开回去。状态行的三句说明按原生 else-if
// 排：自愈成功句（`self_heal_note`，绿）> 拒绝 / 回滚说明（`note`，橙）> TCC 收回（`tcc_lost`，橙），Permissions.swift:466-486。
// TCC 提示不在这里发：桥的 `setRecording {on:true}` 自己先补（§61.1 追记 (a)）。
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState, type ShellRecordingState } from "../../shellBridge";
import { useAppState } from "../../store";
import type { SetupSnapshot } from "../../types";

const PRIVACY_URL = "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/PRIVACY.md";

type Text = (zh: string, en: string) => string;

/** 焦点落在这些元素上时 Enter 归它们自己（按钮激活 / 链接跳转 / 输入框提交）——与 SetupPage.RETURN_OWNERS 同一张表 */
const RETURN_OWNERS = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"]);

/** 披露块里的这次 Enter 是否等于点「开启」（原生 defaultAction）：Enter、无修饰键、不在 IME 组字、焦点不在 RETURN_OWNERS 上 */
export function consentReturnActivates(event: Pick<KeyboardEvent, "key" | "altKey" | "ctrlKey" | "metaKey" | "shiftKey" | "isComposing" | "target">): boolean {
  if (event.key !== "Enter" || event.isComposing) return false;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
  const tag = event.target instanceof Element ? event.target.tagName.toUpperCase() : "";
  return !RETURN_OWNERS.has(tag);
}

/** 一次性同意问题该不该再问：录制已开 / 向导已完成 = 不问（原生 P0-11：壳无存值 = 未同意 = off） */
export function consentPending(rec: ShellRecordingState, setup: SetupSnapshot | null): boolean {
  if (rec.mode !== "off") return false;
  return !(setup && setup.done);
}

/** 原生 recordingStateWord：已关闭 / 已开启,引擎未在录制 / 录制中(屏幕+音频) / 录制中(仅屏幕) */
export function consentStateWord(rec: ShellRecordingState, text: Text): string {
  if (rec.mode === "off") return text("已关闭", "Off");
  if (!rec.engine_running) return text("已开启,引擎未在录制", "On — engine not recording");
  return rec.mode === "screen_audio" ? text("录制中(屏幕+音频)", "Recording (screen + audio)") : text("录制中(仅屏幕)", "Recording (screen only)");
}

export function RecordingConsentSection() {
  const { text } = useI18n();
  const shell = useShellState();
  const { setup } = useAppState();
  const present = hasShellBridge();
  const [answeredHere, setAnsweredHere] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rec = shell?.recording;
  const showPending = present && !!rec && !answeredHere && consentPending(rec, setup);
  const showAnswered = present && !!rec && !showPending;
  // 状态行出现过 → 之后再冒出来的披露块不 autoFocus（见文件头）；ref 不触发重渲染，effect 在状态行提交后落笔
  const answeredShown = useRef(false);
  useEffect(() => {
    if (showAnswered) answeredShown.current = true;
  }, [showAnswered]);

  if (!present || !rec) {
    return (
      <div className="settings-list-row perm-consent" data-state="no-bridge">
        <span className="settings-list-title">{text("屏幕记录", "Screen recording")}</span>
        <p className="settings-list-desc">
          {text("录制引擎只在看板 app（壳）里可控——这是浏览器里打开的看板，看不到引擎。", "The recording engine is only controllable inside the board app (shell) — this board is open in a browser and cannot see it.")}
        </p>
      </div>
    );
  }

  const call = (on: boolean, mode?: string) => {
    setError(null);
    void callShell("setRecording", on ? { on: true, mode } : { on: false }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  };

  const turnOn = () => {
    setAnsweredHere(true);
    call(true, "screen");
  };

  if (showPending) {
    // tabIndex=-1：点到块里的文字时焦点落在块上（而不是 body），Return 才到得了下面的 onKeyDown——原生 defaultAction 是窗口级的
    const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (!consentReturnActivates(event.nativeEvent)) return;
      event.preventDefault();
      turnOn();
    };
    return (
      <div className="perm-consent-block" data-state="pending" tabIndex={-1} onKeyDown={onKeyDown}>
        <h4 className="perm-consent-title">{text("现在开启屏幕记录吗?", "Turn on screen recording now?")}</h4>
        <p className="perm-consent-copy">{text("Zelin AI Assistant 的核心功能依赖持续屏幕记录(OCR 文字识别)。", "Zelin AI Assistant's core features rely on continuous screen recording (OCR text capture).")}</p>
        <ul className="perm-consent-copy">
          <li>{text("采集什么:屏幕上的可见文字(OCR);密码管理器与无痕窗口默认排除", "What is captured: visible on-screen text (OCR); password managers and private-browsing windows are excluded by default")}</li>
          <li>{text("去哪里:先写入本地数据库和 Obsidian vault;摘要经 claude CLI 发送到 Anthropic API 做分析", "Where it goes: stored locally first (database + Obsidian vault); summaries are sent to the Anthropic API via the claude CLI for analysis")}</li>
          <li>{text("保留多久:原始录屏本地保留约 1 天后自动清理;提炼后的笔记留在本地 vault", "How long it is kept: raw recordings are cleaned up locally after ~1 day; distilled notes stay in your local vault")}</li>
        </ul>
        <div className="settings-actions">
          {/* autoFocus：原生 .keyboardShortcut(.defaultAction)——块一出现 Return 就是「开启」；状态行出现过之后的重问不抢焦点 */}
          <button type="button" className="btn btn-primary" autoFocus={!answeredShown.current} onClick={turnOn}>{text("开启", "Turn On")}</button>
          <button type="button" className="btn" onClick={() => setAnsweredHere(true)}>{text("暂不", "Not Now")}</button>
          <a className="settings-link" href={PRIVACY_URL} target="_blank" rel="noreferrer">{text("隐私说明…", "Privacy Details…")}</a>
        </div>
        <p className="settings-helper">{text("不录音频。语音转写(屏幕+音频)之后可在「设置 → 录制」里单独打开;这里的选择也随时可改。", "No audio is recorded. Voice transcription (Screen + Audio) can be enabled separately later in Settings → Recording; this choice can be changed anytime.")}</p>
        {error && <p className="settings-warning" role="alert">{error}</p>}
      </div>
    );
  }

  const tone = rec.engine_running ? "granted" : rec.mode !== "off" ? "denied" : "unknown";
  return (
    <div className="settings-list-row perm-consent" data-state="answered" data-mode={rec.mode}>
      <div className="perm-capability-head">
        <span className={`perm-dot is-${tone}`} aria-hidden="true" />
        <span className="settings-list-title">{text("屏幕记录", "Screen recording")}</span>
        <span className="perm-status">{consentStateWord(rec, text)}</span>
        <span className="perm-capability-action">
          {rec.mode === "off"
            ? (
              <button type="button" className="btn" onClick={() => call(true, rec.resume_mode)}>
                {rec.resume_mode === "screen_audio" ? text("开启(屏幕+音频)", "Turn On (screen + audio)") : text("开启(仅屏幕)", "Turn On (screen only)")}
              </button>
            )
            : <button type="button" className="btn" onClick={() => call(false)}>{text("关闭", "Turn Off")}</button>}
        </span>
      </div>
      <p className="settings-list-desc">{text("屏幕上的可见文字会被识别并整理进你的本地知识库;音频只能在「设置 → 录制」里显式打开。", "Visible on-screen text is captured into your local knowledge base; audio can only be enabled explicitly in Settings → Recording.")}</p>
      {rec.self_heal_note && (
        // 原生 Permissions.swift:466-470（audit 2.2）：consent-race 自愈刚触发——绿字一句，壳侧 15 s 后自己清空；下面两句让位（else-if）
        <p className="settings-helper is-ok self-heal-note" role="status">{rec.self_heal_note}</p>
      )}
      {!rec.self_heal_note && rec.note && <p className="settings-warning">{rec.note}</p>}
      {!rec.self_heal_note && !rec.note && rec.tcc_lost && rec.mode !== "off" && (
        <p className="settings-warning">{text("「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复", "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume")}</p>
      )}
      {error && <p className="settings-warning" role="alert">{error}</p>}
    </div>
  );
}
