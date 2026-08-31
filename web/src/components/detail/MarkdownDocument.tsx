// Markdown 渲染组件，fork-适配自 dashi web/src/components/MarkdownDocument.tsx
// （Apache-2.0，NOTICE 登记）。保留：组件名/props 面（value/onCopy/onLinkClick）、
// HTML 注释剥除、URL 消毒、链接 target=_blank rel=noreferrer、mermaid 围栏 → 沙箱组件。
// 剥除（Codex/taskboard 专属）：resolvePersistedAttachmentUrl 附件重写、
// composer-reference 深链、renderLink 内联媒体注入。
// 适配：react-markdown 不在依赖白名单 → 改用本目录 markdown.ts 的零依赖解析器
// （escape-first：正文全部走 React 文本节点，无 dangerouslySetInnerHTML）。
import { useMemo, type ClipboardEventHandler, type MouseEvent } from "react";
import { parseMarkdown, sanitizeUrl, type BlockNode, type InlineNode } from "./markdown";
import { MermaidDiagram } from "./MermaidDiagram";
import "./markdown.css";

export type MarkdownLinkClickHandler = (event: MouseEvent<HTMLAnchorElement>, href?: string) => void;

interface InlineViewProps {
  nodes: InlineNode[];
  onLinkClick?: MarkdownLinkClickHandler;
}

function InlineView({ nodes, onLinkClick }: InlineViewProps) {
  return (
    <>
      {nodes.map((node, index) => {
        switch (node.type) {
          case "text":
            return node.value;
          case "break":
            return <br key={index} />;
          case "code":
            return <code key={index}>{node.value}</code>;
          case "strong":
            return <strong key={index}><InlineView nodes={node.children} onLinkClick={onLinkClick} /></strong>;
          case "em":
            return <em key={index}><InlineView nodes={node.children} onLinkClick={onLinkClick} /></em>;
          case "del":
            return <del key={index}><InlineView nodes={node.children} onLinkClick={onLinkClick} /></del>;
          case "link": {
            const href = sanitizeUrl(node.href);
            // 被消毒掉的链接降级成纯文本（不给可点面）
            if (!href) return <span key={index}><InlineView nodes={node.children} onLinkClick={onLinkClick} /></span>;
            return (
              <a
                key={index}
                href={href}
                target="_blank"
                rel="noreferrer"
                onClick={(event) => onLinkClick?.(event, href)}
              >
                <InlineView nodes={node.children} onLinkClick={onLinkClick} />
              </a>
            );
          }
          case "image": {
            const src = sanitizeUrl(node.src, true);
            if (!src) return <span key={index}>{node.alt}</span>;
            return <img key={index} src={src} alt={node.alt} loading="lazy" />;
          }
          default:
            return null;
        }
      })}
    </>
  );
}

interface BlockViewProps {
  block: BlockNode;
  onLinkClick?: MarkdownLinkClickHandler;
}

function BlockView({ block, onLinkClick }: BlockViewProps) {
  switch (block.type) {
    case "heading": {
      const Tag = `h${Math.min(6, Math.max(1, block.depth))}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      return <Tag><InlineView nodes={block.children} onLinkClick={onLinkClick} /></Tag>;
    }
    case "paragraph":
      return <p><InlineView nodes={block.children} onLinkClick={onLinkClick} /></p>;
    case "codeBlock":
      if (block.language === "mermaid") return <MermaidDiagram source={block.value} />;
      return (
        <pre>
          <code className={block.language ? `language-${block.language}` : undefined}>{block.value}</code>
        </pre>
      );
    case "blockquote":
      return (
        <blockquote>
          {block.children.map((child, index) => <BlockView key={index} block={child} onLinkClick={onLinkClick} />)}
        </blockquote>
      );
    case "list": {
      const items = block.items.map((item, index) => {
        // tight list：唯一段落项直接内联渲染（贴近 GFM 输出）
        const tight = !block.loose && item.length === 1 && item[0].type === "paragraph";
        return (
          <li key={index}>
            {tight
              ? <InlineView nodes={(item[0] as Extract<BlockNode, { type: "paragraph" }>).children} onLinkClick={onLinkClick} />
              : item.map((child, childIndex) => <BlockView key={childIndex} block={child} onLinkClick={onLinkClick} />)}
          </li>
        );
      });
      return block.ordered
        ? <ol start={block.start !== 1 ? block.start : undefined}>{items}</ol>
        : <ul>{items}</ul>;
    }
    case "table":
      return (
        <table>
          <thead>
            <tr>
              {block.header.map((cell, index) => (
                <th key={index} style={block.align[index] ? { textAlign: block.align[index]! } : undefined}>
                  <InlineView nodes={cell} onLinkClick={onLinkClick} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} style={block.align[cellIndex] ? { textAlign: block.align[cellIndex]! } : undefined}>
                    <InlineView nodes={cell} onLinkClick={onLinkClick} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "hr":
      return <hr />;
    default:
      return null;
  }
}

export interface MarkdownDocumentProps {
  value: string;
  onCopy?: ClipboardEventHandler<HTMLDivElement>;
  onLinkClick?: MarkdownLinkClickHandler;
}

export function MarkdownDocument({ value, onCopy, onLinkClick }: MarkdownDocumentProps) {
  const blocks = useMemo(() => parseMarkdown(value), [value]);
  return (
    <div className="zai-markdown" onCopy={onCopy}>
      {blocks.map((block, index) => <BlockView key={index} block={block} onLinkClick={onLinkClick} />)}
    </div>
  );
}
