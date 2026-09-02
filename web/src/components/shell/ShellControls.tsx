// 壳内原生开关组（CONTRACT §61.2）：只在 `window.webkit.messageHandlers.zaiShell`
// 存在（看板跑在 shell/ 壳（"Zelin's AI Assistant"）里）时渲染「录制」「实时字幕」；普通浏览器
// 会话整组不渲染（连占位都没有）。挂载即开始监听壳推送 + 拉一次快照；语言切换
// 同步给壳（悬浮窗/通知文案跟随，§61.1 setLanguage）。
import { useEffect } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, startShellBridge, useShellState } from "../../shellBridge";
import { CaptionsControl } from "./CaptionsControl";
import { RecordingControl } from "./RecordingControl";

export function ShellControls() {
  const { language } = useI18n();
  const isPresent = hasShellBridge();
  const state = useShellState();

  useEffect(() => {
    if (!isPresent) return;
    return startShellBridge();
  }, [isPresent]);

  useEffect(() => {
    if (!isPresent) return;
    void callShell("setLanguage", { lang: language }).catch(() => {
      /* 老壳不认识 setLanguage：文案留在壳自己的语言，开关照常工作 */
    });
  }, [isPresent, language]);

  if (!isPresent || !state) return null;
  return (
    <div className="shell-native-controls">
      {state.recording.available && <RecordingControl state={state.recording} />}
      {state.captions.available && <CaptionsControl state={state.captions} />}
    </div>
  );
}
