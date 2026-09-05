// 纯文本里的 URL 变成可点链接（原生 Utils.swift:877-889 `linkified`：NSDataDetector 找 URL、标 .link + 下划线，
// SwiftUI Text 直接可点，「Slack-style, no gesture code needed」）。原生应用在：提案卡摘要（Cards.swift:1073）、
// 💬 需求来自 引文（:508 / :1311「Slack quotes often carry links — make them clickable」）、📋 要做什么 步骤
// （:529 / :1329）、潜在任务卡摘要（:2028）；运行中 / 待验收行标题与正文**不**linkify（:1829 原生放弃：
// 链接点击与整卡复制手势冲突）——web 照抄这条边界，不多不少。
// web 落点：按 https?:// 切段，URL 段渲染 <a target=_blank rel=noreferrer>（壳的 WKUIDelegate 把 target=_blank
// 交系统浏览器，§54 追记），其余原样文本节点；href 过 detail/markdown.ts 的 sanitizeUrl 白名单（正则只认
// https?:// 本已排除 javascript: / data:，白名单是双保险）。**没有 URL 时原样返回字符串**——DOM 与此前逐节点相同，
// 既有判例 / 视觉 golden 不动。URL 边界：空白、<>、「」()（） 不算进去（中文引文常用引号 / 括号包着链接）；
// 尾随标点（.,;:!?。，、；：！？ 与右引号）不算进 URL（NSDataDetector 同判：「见 https://x.dev/a。」链接是 a 不是 a。）。
// 只是展示积木：不含文案（无 i18n）、不上抛事件、不读 store。CONTRACT §54.1 追记。
import type { ReactNode } from "react";
import { sanitizeUrl } from "../detail/markdown";

const URL_RE = /https?:\/\/[^\s<>「」()（）]+/g;
const TRAILING_PUNCT_RE = /[.,;:!?。，、；：！？'"”’]+$/;

export type LinkifiedPart = { kind: "text" | "link"; value: string };

/** 纯函数：把一段文本切成 text / link 段（相邻 text 合并）；无 URL → 单段 text */
export function linkifyParts(text: string): LinkifiedPart[] {
  const parts: LinkifiedPart[] = [];
  const pushText = (value: string) => {
    if (!value) return;
    const last = parts[parts.length - 1];
    if (last && last.kind === "text") last.value += value;
    else parts.push({ kind: "text", value });
  };
  let cursor = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    const raw = match[0];
    const url = raw.replace(TRAILING_PUNCT_RE, "");
    // 剥完尾标点还得有主机部分（「https://.」不是链接）；白名单拒的（理论上只剩空）按原文
    const href = /^https?:\/\/./i.test(url) ? sanitizeUrl(url) : undefined;
    pushText(text.slice(cursor, start));
    if (href) {
      parts.push({ kind: "link", value: href });
      pushText(raw.slice(url.length));
    } else {
      pushText(raw);
    }
    cursor = start + raw.length;
  }
  pushText(text.slice(cursor));
  if (parts.length === 0) parts.push({ kind: "text", value: text });
  return parts;
}

/** 文本 → 混排节点：URL 段 <a class="linkified">，其余裸文本节点；无 URL 时就是那个字符串 */
export function Linkified({ text }: { text: string }): ReactNode {
  const parts = linkifyParts(text);
  if (parts.length === 1 && parts[0].kind === "text") return text;
  return (
    <>
      {parts.map((part, index) => (
        part.kind === "link"
          ? <a key={index} className="linkified" href={part.value} target="_blank" rel="noreferrer">{part.value}</a>
          : part.value
      ))}
    </>
  );
}
