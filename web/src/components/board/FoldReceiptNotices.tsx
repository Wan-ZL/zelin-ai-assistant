// §44.6 静默并入回执（原生 Store.swift seenFoldReceipts → LocalNotice kind .info，提案列）：用户通道
// （quick / quick_capture）把一条输入并进了已有卡而没建新卡时，看板必须给可见回执——「刚才的输入已并入
// R-xx「<展示名前 20 字>」（没有建新卡）」。数据 = dashboard add-only 顶层键 fold_receipts
// （server TTL 600 s、按目标卡合簇后 cap 10，永不带被并入原文；雷达自扫的并入自 #308 起不进这条通道）。
// 与原生的差别只在「看过没」的记法：原生 app 常驻、首次加载 prime 不回放旧回执；web 页面随时会整页重载，
// 所以用 sessionStorage 记已关掉的回执 id（同一浏览器会话内不再弹），投影过期即整体消失。
// 文案三段各一节点：前缀 / 「title」 / 后缀（原生 name 片段是独立 L()，§66.2 探针按节点判）。
// §44.6 追记（#308）：server 把同一张卡的多次并入合成一行并带 count，count > 1 时再加第四个节点「 ×N」
// （纯数字，无第二套双语机制）；老 server 没有这个键 → 与改动前逐字相同。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import type { FoldReceipt } from "../../types";

export const SEEN_FOLD_RECEIPTS_KEY = "seenFoldReceipts";
const TITLE_PREFIX = 20; // 原生 String(r.title.prefix(20))

function readSeen(): Set<string> {
  try {
    const raw = window.sessionStorage.getItem(SEEN_FOLD_RECEIPTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

function writeSeen(seen: Set<string>): void {
  try {
    window.sessionStorage.setItem(SEEN_FOLD_RECEIPTS_KEY, JSON.stringify([...seen]));
  } catch {
    /* 隐私模式：本次会话内仍生效 */
  }
}

/** 展示名前 20 个字（code point 计；空 = 目标卡已消失，只报 R-xxx） */
export function receiptTitle(receipt: FoldReceipt): string {
  const title = typeof receipt.title === "string" ? receipt.title : "";
  return [...title].slice(0, TITLE_PREFIX).join("");
}

export function FoldReceiptNotices() {
  const { text } = useI18n();
  const { board } = useAppState();
  const [seen, setSeen] = useState(readSeen);
  const receipts = (board?.fold_receipts ?? []).filter((r) => r && typeof r.id === "string" && typeof r.req === "string" && r.req && !seen.has(r.id));
  if (receipts.length === 0) return null;

  const dismiss = (id: string) => {
    const next = new Set(seen);
    next.add(id);
    writeSeen(next);
    setSeen(next);
  };

  return (
    <ul className="fold-receipts" aria-label={text("并入回执", "Fold receipts")}>
      {receipts.map((r) => {
        const title = receiptTitle(r);
        return (
          <li key={r.id} className="fold-receipt" role="status" data-receipt={r.id}>
            <span className="fold-receipt-text">
              <span>{text(`刚才的输入已并入 ${r.req}`, `Your input was merged into ${r.req}`)}</span>
              {title && <span className="fold-receipt-title">{text(`「${title}」`, ` "${title}"`)}</span>}
              <span>{text("（没有建新卡）", " (no new card filed)")}</span>
              {typeof r.count === "number" && r.count > 1 && (
                <span className="fold-receipt-count">{` \u00d7${r.count}`}</span>
              )}
            </span>
            <button type="button" className="fold-receipt-dismiss" aria-label={text("知道了", "Got it")} title={text("知道了", "Got it")} onClick={() => dismiss(r.id)}>×</button>
          </li>
        );
      })}
    </ul>
  );
}
