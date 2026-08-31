// 交付物发现（纯函数）。
// TODO(contract): 卡片记录没有结构化的「交付物清单」字段（server/files.py 同款批注）——
// §33 只约定文件型交付物落 <root>/deliverables/ 且总结里报绝对路径。保守解：从卡片
// 详情的字符串字段里扫 ``/deliverables/<basename>`` 引用，只取 basename 经
// GET /files/deliverables/{card_id}/{name} 取回（路径推导与穿越校验仍在 server 端，
// 客户端绝不传原始路径）。若日后契约新增结构化清单字段，改读该字段。
import type { CardDetail } from "../../types";
import { deliverableUrl } from "../../api";

export type DeliverableKind = "html" | "markdown" | "text" | "image" | "other";

export interface DeliverableRef {
  name: string;
  kind: DeliverableKind;
}

// basename 允许集镜像 server/files.py _validate_name：非空、≤255、无 NUL/分隔符、不以点开头
const NAME_OK_RE = /^[^./\\\x00][^/\\\x00]{0,254}$/;
// basename 匹配止于：空白、引号、括号闭合、ASCII 句读、CJK 标点区（　-〿）
// 与全角形式区（＀-￯）——路径常嵌在中文散文里（"…report.html，数据在…"）
const PATH_REF_RE = /\/deliverables\/([^\s"'`<>)\]},;:!?　-〿＀-￯]+)/g;
// 句尾黏连的 ASCII 句点等剥掉（"…page.html." → page.html）
const TRAILING_PUNCT_RE = /[.,;:!?]+$/;

export function deliverableKind(name: string): DeliverableKind {
  const ext = name.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] ?? "";
  if (ext === "html" || ext === "htm") return "html";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (["txt", "log", "csv", "json", "yaml", "yml"].includes(ext)) return "text";
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  return "other";
}

function collectStrings(value: unknown, depth: number, out: string[]) {
  if (depth > 4 || value == null) return;
  if (typeof value === "string") { out.push(value); return; }
  if (Array.isArray(value)) { value.forEach((item) => collectStrings(item, depth + 1, out)); return; }
  if (typeof value === "object") {
    Object.values(value as Record<string, unknown>).forEach((item) => collectStrings(item, depth + 1, out));
  }
}

/** 扫详情全部字符串字段，抽出去重后的交付物 basename 清单（出现顺序保序） */
export function extractDeliverables(detail: CardDetail | null): DeliverableRef[] {
  if (!detail) return [];
  const texts: string[] = [];
  collectStrings(detail, 0, texts);
  const seen = new Set<string>();
  const refs: DeliverableRef[] = [];
  for (const text of texts) {
    for (const match of text.matchAll(PATH_REF_RE)) {
      const name = match[1].replace(TRAILING_PUNCT_RE, "");
      // 引用里带子目录的（deliverables/a/b.html）超出 server 服务面，跳过
      if (!NAME_OK_RE.test(name) || seen.has(name)) continue;
      seen.add(name);
      refs.push({ name, kind: deliverableKind(name) });
    }
  }
  return refs;
}

/** final_draft 是否已被 harvest 回填为整页 HTML（§33：恰为一个 .html 时回填文件内容） */
export function looksLikeHtml(text: string | null | undefined): boolean {
  return /^\s*(<!doctype\b|<html\b)/i.test(text ?? "");
}

const TEXT_PREVIEW_CAP = 200_000;

/** 拉取文本型交付物正文（markdown/text 预览用）。非 2xx 抛 Error(状态码信息)。 */
export async function fetchDeliverableText(cardId: string, name: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(deliverableUrl(cardId, name), { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const text = await response.text();
  return text.length > TEXT_PREVIEW_CAP ? `${text.slice(0, TEXT_PREVIEW_CAP)}\n…` : text;
}
