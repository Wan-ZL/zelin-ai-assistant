// 可复制的一行命令（原生 Cards.swift CopyPathLine 的 web 版：label + 等宽正文 + 点一下进剪贴板，✓ 1.5 s）。
// 形状与详情侧栏的 CmdLine / CopyChip 同（DetailFields.tsx）：label 独占一个节点、值住 <code>、右侧「复制」→「已复制」
// 1.5 s + role=status 播报（按钮文案变化 VoiceOver 不一定读）。住 chrome/ 供横幅（PipelineBanner 手动命令：）等
// 卡片以外的面共用——DetailFields 的那两颗是卡片详情的积木，不外借。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import { CopiedAnnouncer } from "../board/cardChrome";
import { copyText } from "../detail/copyText";
import "./chrome.css";

export interface CopyLineProps {
  label: string;
  value: string;
}

export function CopyLine({ label, value }: CopyLineProps) {
  const { text } = useI18n();
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current != null) window.clearTimeout(timer.current);
  }, []);
  const copy = () => {
    void copyText(value).then((ok) => {
      setCopied(ok);
      if (!ok) return;
      if (timer.current != null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <span className="copy-line">
      <span className="copy-line-label">{label}</span>
      <code className="copy-line-value">{value}</code>
      <button type="button" className="copy-line-chip" onClick={copy}>
        {copied ? text("已复制", "Copied") : text("复制", "Copy")}
      </button>
      <CopiedAnnouncer copied={copied} />
    </span>
  );
}
