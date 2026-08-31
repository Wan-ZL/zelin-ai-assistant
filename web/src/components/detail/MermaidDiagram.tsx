// Mermaid 沙箱渲染，fork 自 dashi web/src/components/MarkdownDocument.tsx（Apache-2.0，
// NOTICE 登记）的 mermaid 段（外部资源探测器 + DOMPurify hooks + strict securityLevel）。
// 适配（保持全部安全 hooks，只改依赖接入方式）：
//   1. mermaid / dompurify 不在 §0.4 npm 白名单——改为运行时动态 import（@vite-ignore），
//      依赖缺席时 catch → 源码 fallback（<details> 展示源码，绝不渲染）。契约裁决放行
//      依赖后无需改本文件即可点亮渲染。TODO(contract): 依赖白名单裁决。
//   2. js-yaml 缺席：原版用 YAML 解析 flowchart 节点元数据/frontmatter 判外部资源，
//      这里换成更保守的字面探测（宁可误报走 fallback，绝不漏报去渲染）。
//   3. useTaskboardI18n → useI18n；id 前缀 taskboard- → zai-。
import { useEffect, useId, useState } from "react";
import { useI18n } from "../../i18n";

const EXTERNAL_CSS_REFERENCE = /@import|url\s*\(\s*(?!(?:['"]\s*)?#)/i;
const MERMAID_FRONTMATTER = /^([^\S\n\r]*)-{3}\s*[\n\r](.*?)[\n\r]\1-{3}\s*[\n\r]+/s;
// dashi 原正则逐字保留：C4/架构图各 API 里出现 http(s):// 或 //-开头 sprite/icon 即判外部资源
const MERMAID_EXTERNAL_RESOURCE = /^\s*(?:(?:Person(?:_Ext)?|System(?:Db|Queue)?(?:_Ext)?)\s*\((?:(?:"[^"\r\n]*"|[^,\r\n]*)\s*,){3}|(?:(?:Container|Component)(?:Db|Queue)?(?:_Ext)?|Deployment_Node|Node(?:_[LR])?)\s*\((?:(?:"[^"\r\n]*"|[^,\r\n]*)\s*,){4}|(?:Rel(?:_(?:Up|Down|Left|Right|Back|[UDLR]))?|BiRel)\s*\((?:(?:"[^"\r\n]*"|[^,\r\n]*)\s*,){5}|RelIndex\s*\((?:(?:"[^"\r\n]*"|[^,\r\n]*)\s*,){6}|UpdateElementStyle\s*\((?:(?:"[^"\r\n]*"|[^,\r\n]*)\s*,){6})\s*(?:\$sprite\s*=\s*)?["']?\s*(?:https?:)?\/\//im;
const MERMAID_SEQUENCE_PROPERTIES = /(?:^|[;\r\n])\s*properties\s+[^:\r\n;]+\s*:[^\S\r\n]*/gim;

/** 依赖动态载入（@vite-ignore：构建期不解析，运行时缺依赖 → reject → fallback） */
function loadModule(specifier: string): Promise<any> {
  return import(/* @vite-ignore */ specifier);
}

function mermaidObjectEnd(source: string, start: number) {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
    } else if (character === '"') {
      inString = true;
    } else if (character === "{") {
      depth += 1;
    } else if (character === "}") {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

/** flowchart 节点 `@{ img: ... }` 元数据探测。原版 YAML 解析后查 img 键；
 * 无 js-yaml 改为字面探测 img 键出现即视为外部图片（保守：误报只是不渲染）。 */
function hasFlowchartImageResource(source: string) {
  const flowSource = source.replace(/^\s*%%(?!\{)[^\n]+\n?/gm, "");
  let searchFrom = 0;
  for (;;) {
    const index = flowSource.indexOf("@{", searchFrom);
    if (index < 0) return false;
    const objectEnd = mermaidObjectEnd(flowSource, index + 1);
    if (objectEnd < 0) return false;
    const metadataSource = flowSource.slice(index + 2, objectEnd);
    if (/(^|[\s,{])["']?img["']?\s*:/.test(metadataSource)) return true;
    searchFrom = objectEnd + 1;
  }
}

function hasExternalThemeCss(config: unknown) {
  if (config === null || typeof config !== "object" || Array.isArray(config)) return false;
  const themeCss = (config as Record<string, unknown>).themeCSS;
  return typeof themeCss === "string" && EXTERNAL_CSS_REFERENCE.test(themeCss);
}

function hasExternalMermaidCss(source: string) {
  const frontmatter = source.match(MERMAID_FRONTMATTER);
  // frontmatter 原版走 YAML 解析 config.themeCSS；无 js-yaml 改保守字面判定
  if (frontmatter && /themeCSS/i.test(frontmatter[2]) && EXTERNAL_CSS_REFERENCE.test(frontmatter[2])) {
    return true;
  }

  for (const directive of source.matchAll(/%%\{\s*(?:init|initialize)\b\s*:?\s*([\s\S]*?)\}%%/gi)) {
    try {
      if (hasExternalThemeCss(JSON.parse(directive[1].trim().replace(/'/g, '"')))) return true;
    } catch {
      continue;
    }
  }

  const statements: string[] = [];
  let start = 0;
  let quote: '"' | "'" | "`" | null = null;
  let escaped = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (escaped) {
      escaped = false;
    } else if (character === "\\") {
      escaped = true;
    } else if (quote) {
      if (character === quote) quote = null;
    } else if (character === '"' || character === "'" || character === "`") {
      quote = character;
    } else if (character === ";" || character === "\r" || character === "\n") {
      statements.push(source.slice(start, index));
      if (character === "\r" && source[index + 1] === "\n") index += 1;
      start = index + 1;
    }
  }
  statements.push(source.slice(start));

  return statements.some((statement) => (
    /^\s*(?:style\s+\S+|classDef\s+\S+|linkStyle\s+\S+|rect\b|UpdateElementStyle\s*\(|UpdateRelStyle\s*\()/i.test(statement)
    && EXTERNAL_CSS_REFERENCE.test(statement)
  ));
}

function hasExternalMermaidResource(source: string) {
  if (MERMAID_EXTERNAL_RESOURCE.test(source) || hasFlowchartImageResource(source)) return true;
  for (const match of source.matchAll(MERMAID_SEQUENCE_PROPERTIES)) {
    let objectStart = (match.index ?? 0) + match[0].length;
    const wrapPrefix = source.slice(objectStart).match(/^:?(?:no)?wrap:[^\S\r\n]*/);
    if (wrapPrefix) objectStart += wrapPrefix[0].length;
    if (source[objectStart] !== "{") continue;
    const objectEnd = mermaidObjectEnd(source, objectStart);
    if (objectEnd < 0) continue;
    try {
      const properties = JSON.parse(source.slice(objectStart, objectEnd + 1)) as Record<string, unknown>;
      const icon = properties.icon;
      if (typeof icon === "string") {
        const iconSource = icon.trim();
        if (iconSource !== "" && !iconSource.startsWith("@")) return true;
      }
    } catch {
      continue;
    }
  }
  return false;
}

function isLocalSvgReference(element: Element, reference: string) {
  if (!reference.startsWith("#") || reference.length === 1) return false;
  const svg = element.localName === "svg" ? element as SVGSVGElement : (element as SVGElement).ownerSVGElement;
  if (!svg) return false;
  const targetId = reference.slice(1);
  return svg.id === targetId || [...svg.querySelectorAll<SVGElement>("[id]")].some((target) => target.id === targetId);
}

function MermaidFallback({ source, error }: { source: string; error?: boolean }) {
  const { text } = useI18n();
  return (
    <div className="zai-mermaid-fallback" role={error ? "alert" : undefined}>
      {error && <p>{text(
        "无法渲染 Mermaid 图，下面显示图表源码。",
        "Unable to render Mermaid diagram. Showing its source instead.",
      )}</p>}
      <details open={error}>
        <summary>{text("Mermaid 源码", "Mermaid source")}</summary>
        <pre><code className="language-mermaid">{source}</code></pre>
      </details>
    </div>
  );
}

export function MermaidDiagram({ source }: { source: string }) {
  const { text } = useI18n();
  const reactId = useId();
  const renderId = `zai-mermaid-${reactId.replace(/[^A-Za-z0-9_-]/g, "")}`;
  const [theme, setTheme] = useState<"light" | "dark">(() => (
    typeof document !== "undefined" && document.documentElement.dataset.theme === "dark" ? "dark" : "light"
  ));
  const [diagram, setDiagram] = useState<(
    { source: string; theme: "light" | "dark" } & ({ svg: string } | { error: true })
  ) | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const root = document.documentElement;
    const observer = new MutationObserver(() => {
      setTheme(root.dataset.theme === "dark" ? "dark" : "light");
    });
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDiagram(null);
    if (hasExternalMermaidResource(source) || hasExternalMermaidCss(source)) {
      setDiagram({ source, theme, error: true });
      return undefined;
    }
    void Promise.all([loadModule("mermaid"), loadModule("dompurify")])
      .then(async ([mermaidModule, purifierModule]) => {
        const mermaid = mermaidModule.default;
        const purifier = purifierModule.default;
        // dashi 原 hooks 逐字保留：<use> 只允许引用图内锚点；href 全部过局部引用过滤
        const preserveLocalUse = (node: unknown, data: { allowedTags: Record<string, boolean> }) => {
          if (!(node instanceof Element) || node.localName !== "use") return;
          const reference = node.getAttribute("href") ?? node.getAttribute("xlink:href");
          if (reference && isLocalSvgReference(node, reference)) data.allowedTags.use = true;
        };
        const filterSvgReferences = (
          node: unknown,
          data: { attrName: string; attrValue: string; forceKeepAttr?: boolean; keepAttr?: boolean },
        ) => {
          if (data.attrName !== "href" && data.attrName !== "xlink:href") return;
          if (node instanceof Element && isLocalSvgReference(node, data.attrValue)) {
            data.forceKeepAttr = true;
          } else {
            data.keepAttr = false;
          }
        };
        purifier.addHook("uponSanitizeElement", preserveLocalUse);
        purifier.addHook("uponSanitizeAttribute", filterSvgReferences);
        try {
          mermaid.initialize({
            startOnLoad: false,
            securityLevel: "strict",
            suppressErrorRendering: true,
            theme: theme === "dark" ? "dark" : "default",
            htmlLabels: false,
            secure: ["htmlLabels"],
          });
          const { svg } = await mermaid.render(renderId, source);
          const sanitizedSvg = purifier.sanitize(svg, {
            USE_PROFILES: { svg: true, svgFilters: true },
            FORBID_TAGS: ["foreignObject", "image", "script"],
            FORBID_ATTR: ["href", "xlink:href"],
          });
          const svgRoot = document.createElement("template");
          svgRoot.innerHTML = sanitizedSvg;
          if (svgRoot.content.children.length !== 1 || svgRoot.content.firstElementChild?.localName !== "svg") {
            throw new Error("Mermaid did not produce a usable SVG document.");
          }
          svgRoot.content.querySelectorAll("style").forEach((element) => {
            if (EXTERNAL_CSS_REFERENCE.test(element.textContent ?? "")) element.remove();
          });
          svgRoot.content.querySelectorAll<SVGElement>("[style]").forEach((element) => {
            if (EXTERNAL_CSS_REFERENCE.test(element.getAttribute("style") ?? "")) {
              element.removeAttribute("style");
            }
          });
          if (!cancelled) setDiagram({ source, theme, svg: svgRoot.innerHTML });
        } finally {
          purifier.removeHook("uponSanitizeElement", preserveLocalUse);
          purifier.removeHook("uponSanitizeAttribute", filterSvgReferences);
        }
      })
      .catch(() => {
        if (!cancelled) setDiagram({ source, theme, error: true });
      });
    return () => { cancelled = true; };
  }, [renderId, source, theme]);

  const currentDiagram = diagram?.source === source && diagram.theme === theme ? diagram : null;
  if (!currentDiagram) {
    return <div className="zai-mermaid" aria-busy="true"><MermaidFallback source={source} /></div>;
  }
  if ("error" in currentDiagram) {
    return <div className="zai-mermaid"><MermaidFallback source={source} error /></div>;
  }
  // 唯一的 dangerouslySetInnerHTML：内容已过 DOMPurify svg profile + 上面全部 FORBID/hook 收紧
  return (
    <div
      className="zai-mermaid"
      role="img"
      aria-label={text("Mermaid 图", "Mermaid diagram")}
      dangerouslySetInnerHTML={{ __html: currentDiagram.svg }}
    />
  );
}
