// ui_scout 的观察面（CONTRACT §79.2）：把「这一屏现在是什么样」压成一份可序列化的快照。
//
// 两份东西一起采：
//   · Observation —— 交给驾驶员（截图路径 + 可操作元素表 + 可见文本）；
//   · Probe       —— 交给判官（列头计数 / 报警面 / chrome 文案 / 省略号容器 / 崩溃标记）。
// 采样全在页面里一次 evaluate 完成：同一帧看到的东西才配互相印证。
//
// ref 的纪律（§79.2）：每一步先把上一轮的 `data-ui-scout-ref` 全部抹掉，再按文档顺序发新的。
// 驾驶员只能按 ref 指认元素，永远写不出 selector——越权面就这一道闸。
import type { ElementRef } from "./core/protocol";
import type { AlertSnapshot, ChromeString, LaneSnapshot, TextBox } from "./core/oracles";

export const REF_ATTR = "data-ui-scout-ref";

export interface Probe {
  url: string;
  page: string;
  lang: string;
  crash: boolean;
  lanes: LaneSnapshot[];
  alerts: AlertSnapshot[];
  chrome: ChromeString[];
  boxes: TextBox[];
  text: string;
  elements: ElementRef[];
  settling: boolean;
}

/** 采集器（在浏览器里跑，必须自包含——不能引用模块作用域的任何东西）。 */
/* eslint-disable */
function collect(refAttr: string): Probe {
  const MAX_ELEMENTS = 120;
  const MAX_TEXT = 4000;

  const isVisible = (el: Element): boolean => {
    if (!(el instanceof HTMLElement)) return false;
    if (el.closest("[inert]") || el.closest('[aria-hidden="true"]')) return false;
    if (el.getClientRects().length === 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none";
  };

  const textOf = (el: Element): string =>
    ((el as HTMLElement).innerText || el.textContent || "").replace(/\s+/g, " ").trim();

  const nameOf = (el: Element): string => {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return aria.trim();
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const parts = labelledby.split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => textOf(node as Element));
      if (parts.join(" ").trim()) return parts.join(" ").trim().slice(0, 80);
    }
    const placeholder = el.getAttribute("placeholder");
    const title = el.getAttribute("title");
    const own = textOf(el);
    return (own || placeholder || title || el.getAttribute("value") || "").slice(0, 80);
  };

  const roleOf = (el: Element): string => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return el.hasAttribute("href") ? "link" : "generic";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "number") return "spinbutton";
      if (type === "button" || type === "submit") return "button";
      return "textbox";
    }
    return "generic";
  };

  // 上一轮的 ref 一律作废：陈旧 ref 指到别的元素上是最难查的一类假 bug。
  document.querySelectorAll("[" + refAttr + "]").forEach((node) => node.removeAttribute(refAttr));

  // 模态开着的时候背景是 inert：只发对话框里的元素，免得驾驶员去点点不到的东西。
  const modal = document.querySelector("dialog[open]");
  const root: ParentNode = modal || document;

  const SELECTOR = [
    "a[href]", "button", "summary", "input", "textarea", "select",
    "[role=button]", "[role=link]", "[role=tab]", "[role=switch]", "[role=checkbox]",
  ].join(",");

  const elements: ElementRef[] = [];
  let index = 0;
  for (const el of Array.from(root.querySelectorAll(SELECTOR))) {
    if (elements.length >= MAX_ELEMENTS) break;
    if (!isVisible(el)) continue;
    index += 1;
    const ref = "e" + index;
    el.setAttribute(refAttr, ref);
    const tag = el.tagName.toLowerCase();
    elements.push({
      ref,
      role: roleOf(el),
      name: nameOf(el),
      tag,
      enabled: !(el as HTMLInputElement).disabled && el.getAttribute("aria-disabled") !== "true",
      editable: tag === "textarea" || (tag === "input" && !["checkbox", "radio", "button", "submit"]
        .includes((el.getAttribute("type") || "text").toLowerCase())),
    });
  }

  const lanes: LaneSnapshot[] = Array.from(document.querySelectorAll("section.board-column"))
    .map((column) => {
      const memory = column.querySelector("[data-scroll-memory^='lane:']");
      const slug = (memory?.getAttribute("data-scroll-memory") || "").slice("lane:".length);
      const badge = textOf(column.querySelector(".lane-count") || column);
      return { slug, badge, domCards: column.querySelectorAll("article").length };
    })
    .filter((lane) => lane.slug);

  // 报警面：有 role 的（横幅/设置 toast）与没 role 的（卡面 / 输入框报错）都要采——
  // `.card-error` / `.composer-error` 在产品里不带 role，只看 a11y 树的判官会漏掉它们。
  const ALERTS = [
    "[role=alert]", ".card-error", ".composer-error", ".settings-error", ".settings-warning",
  ].join(",");
  const alerts: AlertSnapshot[] = Array.from(document.querySelectorAll(ALERTS))
    .filter(isVisible)
    .map((el) => ({
      where: el.className ? "." + String(el.className).split(/\s+/)[0] : el.tagName.toLowerCase(),
      text: textOf(el).slice(0, 300),
    }));

  // chrome = 外壳文案（不是卡片内容）：i18n 与 overflow 两只判官都只看这一圈。卡片正文是
  // 用户数据（demo 里本来就是中文、本来就长），拿它去判「没翻译」「被裁了」只会满屏假红。
  const CHROME = [
    ".shell-header", "[data-rail=left]", ".column-header", ".card-actions",
    ".chrome-filterbar", ".settings-toc", ".settings-fold-title", ".dialog-actions",
    ".zai-drawer-header",
  ].join(",");
  // 语言开关按设计显示的是**目标**语言（英文界面上写「中」）——它不是漏翻译。
  const BY_DESIGN_BILINGUAL = ".shell-lang-toggle";

  const containers = Array.from(document.querySelectorAll(CHROME)).filter(isVisible);
  const chrome: ChromeString[] = [];
  const boxes: TextBox[] = [];
  for (const container of containers) {
    const where = "." + String(container.className || container.tagName).split(/\s+/)[0];
    for (const el of Array.from(container.querySelectorAll("button, a, h1, h2, h3, label, span, p"))) {
      if (!isVisible(el) || el.children.length) continue;
      const value = textOf(el);
      if (!value) continue;
      if (!el.closest(BY_DESIGN_BILINGUAL)) {
        chrome.push({ where: where + " " + el.tagName.toLowerCase(), text: value });
      }
      // 省略号容器：单行 + ellipsis 的才算「本该放得下」。省略号本身是设计，判官说的是
      // 「这一处真的把字省掉了」——值得看一眼，不等于 bug（§79.3）。
      if (boxes.length >= 60) continue;
      const style = window.getComputedStyle(el);
      if (style.textOverflow !== "ellipsis" || style.whiteSpace !== "nowrap") continue;
      boxes.push({
        where: "." + String(el.className || el.tagName).split(/\s+/)[0],
        text: value,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      });
    }
  }

  const main = document.querySelector("main.shell-main") || document.body;
  return {
    url: window.location.href,
    page: new URLSearchParams(window.location.search).get("page") || "board",
    lang: document.documentElement.lang || "",
    crash: !!document.querySelector('[data-testid="app-error-boundary"]'),
    lanes,
    alerts,
    chrome,
    boxes,
    text: textOf(main).slice(0, MAX_TEXT),
    elements,
    // 还没落定：转圈的 spinner 或卡面「处理中」小字还在（boardActions.ts 的 pending note）。
    // 选择器与模块级的 SETTLING_SELECTOR 同一串——采集器在页面里跑，引不到模块作用域。
    settling: !!document.querySelector(".shell-spinner, .card-pending-note, .zai-detail-dim"),
  };
}
/* eslint-enable */

/** 还没落定：转圈的 spinner、卡面「处理中」小字、详情抽屉的「加载详情…」还在。 */
export const SETTLING_SELECTOR = ".shell-spinner, .card-pending-note, .zai-detail-dim";

/** 采一帧。`page` 是 Playwright 的 Page（这一层不做类型约束，e2e/ 不进 tsc）。 */
export async function probe(page: { evaluate: (fn: unknown, arg: unknown) => Promise<Probe> }): Promise<Probe> {
  return page.evaluate(collect, REF_ATTR);
}

/**
 * 只问「落定了没」。
 *
 * 等落定是个轮询，而整帧采集会重写全页的 `data-ui-scout-ref`、还要量几十个元素的
 * 宽度——每 150ms 做一次既慢又多余。这里只做一次 querySelector。
 */
export async function settling(page: { evaluate: (fn: unknown, arg: unknown) => Promise<boolean> }): Promise<boolean> {
  return page.evaluate((selector: string) => !!document.querySelector(selector), SETTLING_SELECTOR);
}
