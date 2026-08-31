// 极简 markdown 解析器（纯函数、零依赖、escape-first）。
// TODO(contract): BUILD-CONTRACT §2.2 点名 fork dashi MarkdownDocument.tsx，但它依赖
// react-markdown/remark-gfm/js-yaml/mermaid/dompurify——全部不在 §0.4 npm 运行时白名单
// （react/react-dom 到此为止）。两条契约冲突，按 §0.9 选最保守实现：不加任何依赖，
// 手写 GFM 常用子集解析器。安全性质靠构造保证：输出是纯数据 AST，渲染端只产 React
// 文本节点（绝无 dangerouslySetInnerHTML 渲染 markdown 正文），原始 HTML 一律按字面
// 文本展示（与 react-markdown 无 rehype-raw 时的行为一致）；URL 过 sanitizeUrl 白名单。
// 待契约裁决依赖后，本模块可整体换回 dashi 原 fork（渲染端 API 已对齐）。

export type InlineNode =
  | { type: "text"; value: string }
  | { type: "code"; value: string }
  | { type: "strong" | "em" | "del"; children: InlineNode[] }
  | { type: "link"; href: string; children: InlineNode[] }
  | { type: "image"; src: string; alt: string }
  | { type: "break" };

export type BlockNode =
  | { type: "heading"; depth: number; children: InlineNode[] }
  | { type: "paragraph"; children: InlineNode[] }
  | { type: "codeBlock"; language: string | null; value: string }
  | { type: "blockquote"; children: BlockNode[] }
  | { type: "list"; ordered: boolean; start: number; loose: boolean; items: BlockNode[][] }
  | { type: "table"; align: Array<"left" | "center" | "right" | null>; header: InlineNode[][]; rows: InlineNode[][][] }
  | { type: "hr" };

// dashi RAW_COMMENT 同款：HTML 注释整段剥除（entity-encoded 变体无需处理——本解析器
// 不解码实体，编码形式只会按字面文本显示，不构成隐藏语义）。
const RAW_COMMENT = /<!--[\s\S]*?-->/g;

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})[ \t]*([^`\s]*)/;
const HEADING_RE = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/;
const HR_RE = /^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$/;
const QUOTE_RE = /^ {0,3}> ?/;
const ITEM_RE = /^( *)([-*+]|\d{1,9}[.)]) +(.*)$/;
const TABLE_DELIM_RE = /^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;

/** URL 白名单（对齐 react-markdown defaultUrlTransform 的意图，取更保守子集）：
 * 相对路径 / 锚点放行；绝对 URL 仅 http(s)、mailto（图片仅 http(s)）；
 * javascript:/data:/vbscript:/protocol-relative 一律拒。拒 = 返回 undefined。 */
export function sanitizeUrl(raw: string | null | undefined, forImage = false): string | undefined {
  const url = (raw ?? "").trim();
  if (!url) return undefined;
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) {
    if (forImage) return /^https?:/i.test(url) ? url : undefined;
    return /^(https?|mailto):/i.test(url) ? url : undefined;
  }
  if (url.startsWith("//")) return undefined;
  return url;
}

export function stripHtmlComments(source: string): string {
  return source.replace(RAW_COMMENT, "");
}

export function parseMarkdown(source: string): BlockNode[] {
  const normalized = stripHtmlComments(String(source ?? "")).replace(/\r\n?/g, "\n");
  return parseBlocks(normalized.split("\n"));
}

function isBlockStart(line: string): boolean {
  return FENCE_RE.test(line) || HEADING_RE.test(line) || HR_RE.test(line)
    || QUOTE_RE.test(line) || ITEM_RE.test(line);
}

function parseBlocks(lines: string[]): BlockNode[] {
  const blocks: BlockNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    const fence = line.match(FENCE_RE);
    if (fence) {
      const closeRe = new RegExp(`^ {0,3}\\${fence[1][0]}{${fence[1].length},}[ \\t]*$`);
      const body: string[] = [];
      let j = i + 1;
      while (j < lines.length && !closeRe.test(lines[j])) { body.push(lines[j]); j += 1; }
      blocks.push({ type: "codeBlock", language: fence[2] ? fence[2].toLowerCase() : null, value: body.join("\n") });
      i = j + 1;
      continue;
    }

    if (HR_RE.test(line)) { blocks.push({ type: "hr" }); i += 1; continue; }

    const heading = line.match(HEADING_RE);
    if (heading) {
      blocks.push({ type: "heading", depth: heading[1].length, children: parseInline(heading[2]) });
      i += 1;
      continue;
    }

    if (QUOTE_RE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && (QUOTE_RE.test(lines[i])
        || (body.length > 0 && lines[i].trim() !== "" && !isBlockStart(lines[i])))) {
        body.push(lines[i].replace(QUOTE_RE, ""));
        i += 1;
      }
      blocks.push({ type: "blockquote", children: parseBlocks(body) });
      continue;
    }

    if (ITEM_RE.test(line.replace(/\t/g, "  "))) {
      const { block, next } = parseList(lines, i);
      blocks.push(block);
      i = next;
      continue;
    }

    if (i + 1 < lines.length && line.includes("|") && lines[i + 1].includes("-")
      && TABLE_DELIM_RE.test(lines[i + 1])) {
      const { block, next } = parseTable(lines, i);
      blocks.push(block);
      i = next;
      continue;
    }

    const body: string[] = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() !== "" && !isBlockStart(lines[i])) {
      body.push(lines[i]);
      i += 1;
    }
    // remark-breaks 语义：段内单个换行 = <br>
    const children: InlineNode[] = [];
    body.forEach((l, idx) => {
      if (idx > 0) children.push({ type: "break" });
      children.push(...parseInline(l.trim()));
    });
    blocks.push({ type: "paragraph", children });
  }
  return blocks;
}

function parseList(lines: string[], start: number): { block: BlockNode; next: number } {
  const first = lines[start].replace(/\t/g, "  ").match(ITEM_RE)!;
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const startNumber = ordered ? parseInt(first[2], 10) || 1 : 1;
  const items: Array<{ lines: string[]; contentIndent: number }> = [];
  let loose = false;
  let pendingBlank = false;
  let i = start;
  while (i < lines.length) {
    const raw = lines[i];
    if (!raw.trim()) { pendingBlank = true; i += 1; continue; }
    const line = raw.replace(/\t/g, "  ");
    if (HR_RE.test(line)) break;
    const indent = line.match(/^ */)![0].length;
    const m = line.match(ITEM_RE);
    const current = items[items.length - 1];
    if (m && m[1].length <= baseIndent + 1) {
      if (/\d/.test(m[2]) !== ordered) break; // marker 家族切换（- ↔ 1.）= 新列表
      if (pendingBlank && items.length > 0) loose = true;
      items.push({ lines: [m[3]], contentIndent: m[1].length + m[2].length + 1 });
      pendingBlank = false;
      i += 1;
      continue;
    }
    if (current && indent > baseIndent) {
      if (pendingBlank) { current.lines.push(""); loose = true; }
      current.lines.push(line.slice(Math.min(indent, current.contentIndent)));
      pendingBlank = false;
      i += 1;
      continue;
    }
    break;
  }
  return {
    block: { type: "list", ordered, start: startNumber, loose, items: items.map((item) => parseBlocks(item.lines)) },
    next: i,
  };
}

/** 拆一行表格 cell：处理 ``\|`` 转义 + 首尾框线 pipe */
function splitRow(line: string): string[] {
  const cells: string[] = [];
  let cur = "";
  for (let k = 0; k < line.length; k += 1) {
    const ch = line[k];
    if (ch === "\\" && line[k + 1] === "|") { cur += "|"; k += 1; continue; }
    if (ch === "|") { cells.push(cur); cur = ""; continue; }
    cur += ch;
  }
  cells.push(cur);
  if (cells.length > 0 && cells[0].trim() === "") cells.shift();
  if (cells.length > 0 && cells[cells.length - 1].trim() === "") cells.pop();
  return cells.map((c) => c.trim());
}

function parseTable(lines: string[], start: number): { block: BlockNode; next: number } {
  const headerCells = splitRow(lines[start]);
  const align = splitRow(lines[start + 1]).map<"left" | "center" | "right" | null>((cell) => {
    if (/^:-+:$/.test(cell)) return "center";
    if (/^-+:$/.test(cell)) return "right";
    if (/^:-+$/.test(cell)) return "left";
    return null;
  });
  const rows: InlineNode[][][] = [];
  let i = start + 2;
  while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|") && !isBlockStart(lines[i])) {
    const cells = splitRow(lines[i]).slice(0, headerCells.length);
    while (cells.length < headerCells.length) cells.push("");
    rows.push(cells.map(parseInline));
    i += 1;
  }
  return {
    block: { type: "table", align, header: headerCells.map(parseInline), rows },
    next: i,
  };
}

function findDelim(text: string, from: number, delim: string): number {
  if (/\s/.test(text[from] ?? "")) return -1; // opener 后不能紧跟空白（GFM 左翼规则简化）
  for (let k = from + 1; k <= text.length - delim.length; k += 1) {
    if (text[k] === "\\") { k += 1; continue; }
    if (text.startsWith(delim, k) && !/\s/.test(text[k - 1] ?? "")) return k;
  }
  return -1;
}

const LINK_RE = /^\[((?:\\.|[^\]])*)\]\(\s*<?([^)<>\s]*)>?(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)/;
const IMAGE_RE = /^!\[((?:\\.|[^\]])*)\]\(\s*<?([^)<>\s]*)>?(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)/;
const AUTOLINK_RE = /^<((?:https?|mailto):[^ <>]+)>/i;

export function parseInline(text: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  let buf = "";
  const flush = () => { if (buf) { nodes.push({ type: "text", value: buf }); buf = ""; } };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    const rest = text.slice(i);
    if (ch === "\\" && i + 1 < text.length && /[\\`*_{}[\]()#+\-.!~<>|]/.test(text[i + 1])) {
      buf += text[i + 1];
      i += 2;
      continue;
    }
    if (ch === "`") {
      const open = rest.match(/^`+/)![0];
      const close = text.indexOf(open, i + open.length);
      if (close !== -1) {
        flush();
        nodes.push({ type: "code", value: text.slice(i + open.length, close) });
        i = close + open.length;
        continue;
      }
    }
    if (rest.startsWith("![")) {
      const m = rest.match(IMAGE_RE);
      if (m) {
        flush();
        nodes.push({ type: "image", src: m[2], alt: m[1].replace(/\\(.)/g, "$1") });
        i += m[0].length;
        continue;
      }
    }
    if (ch === "[") {
      const m = rest.match(LINK_RE);
      if (m) {
        flush();
        nodes.push({ type: "link", href: m[2], children: parseInline(m[1]) });
        i += m[0].length;
        continue;
      }
    }
    if (ch === "<") {
      const m = rest.match(AUTOLINK_RE);
      if (m) {
        flush();
        nodes.push({ type: "link", href: m[1], children: [{ type: "text", value: m[1] }] });
        i += m[0].length;
        continue;
      }
    }
    const delim = rest.match(/^(\*\*|__|\*|_|~~)/);
    if (delim) {
      const d = delim[1];
      // GFM：下划线不做词内强调（snake_case 保持字面）
      const intraword = (d === "_" || d === "__") && /\w/.test(text[i - 1] ?? "");
      if (!intraword) {
        const closeIdx = findDelim(text, i + d.length, d);
        if (closeIdx !== -1) {
          flush();
          const type = d === "~~" ? "del" : d.length === 2 ? "strong" : "em";
          nodes.push({ type, children: parseInline(text.slice(i + d.length, closeIdx)) });
          i = closeIdx + d.length;
          continue;
        }
      }
    }
    buf += ch;
    i += 1;
  }
  flush();
  return nodes;
}
