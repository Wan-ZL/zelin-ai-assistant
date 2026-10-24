// 两样跨页小件（v2.0.0 从看板卡面 chrome 搬来——看板随 Act 管线退役，全文见 tag full）：
//   RelativeTime：相对时间小字（刚刚 / N分钟前 / N小时前 / N天前），hover `title` 给绝对时间；解析不了不渲染；
//   CopiedAnnouncer：复制成功的 role=status 播报（视觉上 sr-only）——按钮文案变化 VoiceOver 不一定读。
// 纪律：颜色只用 token class；文案 text(zh,en) 内联对。
import { useI18n } from "../../i18n";
import { absoluteLabel, sinceEpoch, sinceIso, useNow } from "../../relativeTime";

type Stamp = { epoch?: unknown; iso?: unknown };

/** 相对时间小字（原生 RelativeTime.sinceEpoch / since）+ hover 绝对时间；解析不了不渲染 */
export function RelativeTime({ epoch, iso, prefix = "", suffix = "", className = "" }: Stamp & { prefix?: string; suffix?: string; className?: string }) {
  const { text, locale } = useI18n();
  const now = useNow();
  const label = epoch !== undefined ? sinceEpoch(epoch, now, text) : sinceIso(iso, now, text);
  if (!label) return null;
  // 前缀与时间值各自一个文本节点（探针按节点文本逐字判「上次检查 」之类的前缀）；
  // suffix 收尾括号之类（「（上次检查：<rel>）」整句一个 Text——外层 span 的 textContent 才是那一句）
  return (
    <span className={`card-meta-text${className ? ` ${className}` : ""}`} title={absoluteLabel(epoch ?? iso, locale)}>
      {prefix ? <span className="card-meta-prefix">{prefix}</span> : null}
      <span>{label}</span>
      {suffix ? <span className="card-meta-prefix">{suffix}</span> : null}
    </span>
  );
}

/** 复制成功的屏幕阅读器播报（视觉隐藏；aria-live=polite 不打断正在读的内容） */
export function CopiedAnnouncer({ copied }: { copied: boolean }) {
  const { text } = useI18n();
  return <span className="sr-only" role="status">{copied ? text("已复制到剪贴板", "Copied to clipboard") : ""}</span>;
}
