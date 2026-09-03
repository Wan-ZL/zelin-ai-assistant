# web/ 组件约定（Build 阶段 A6/A7/A8/A9/A11 必读——脚手架已定型，跟着走别另起炉灶）

## 目录与命名
- `src/*.ts` 小写 = 非组件模块（`api.ts` / `store.ts` / `realtime.ts` / `i18n.ts` / `route.ts` / `types.ts`）。新的纯逻辑模块照此命名（如 A8 的 `taskFilters.ts` 仿 dashi 同名文件）。
- `src/pages/PascalCase.tsx` = 页面级组件（`BoardPage.tsx`，A6 重写内容但保留文件名/导出名；回收站页 `TrashPage.tsx`）。
- `src/components/PascalCase.tsx` = 可复用组件（卡片/列/抽屉/弹窗/chips）。fork 来的 `MarkdownDocument.tsx`、`TaskPropertyPicker.tsx` 也放这里，保持原文件名以便 NOTICE 对账。
- `src/styles/tokens.css` = 唯一 token 源（A9 的动画/组件样式放 `src/styles/` 下新文件，import 进 main.tsx；只允许引用 tokens.css 里已有变量，不造新色值）。
- 测试与被测模块同目录同名：`foo.test.ts(x)`。vitest 环境 jsdom，显式 `import { describe, it, expect, vi } from "vitest"`（未开 globals）。
- 单文件 <300 行（契约 §2.2 预算）。

## Props 风格
- 具名导出 function 组件（`export function CardActions(...)`），不用 default export、不用 `React.FC`。
- Props 一律内联解构 + 独立 interface（组件名 + `Props` 后缀），callback 命名 `onXxx`，布尔命名 `isXxx`/`showXxx`。
- 事件回调只上抛语义动作（如 `onApprove(cardId)`），不上抛原始 DOM event。

## 组件怎么读/写 state（红线）
- 读：`const { board, connection } = useAppState()`（`store.ts` 唯一入口，整快照返回，随手解构）。禁止组件间 prop-drilling 全局数据、禁止新开 Context 存业务态（Context 只有 `LanguageContext`）。
- 写：只调 `store.ts` 导出的 action 函数（`refreshBoard` / `selectCard` / `setConnection`…）。新增 UI 态 = `AppState` 加字段 + 加 action，同一文件。组件内 `useState` 只许存纯本地瞬态（输入框草稿、hover、弹窗开合）。
- 服务端交互只经 `api.ts` 导出的函数；动作 payload 由动作组件按 live CONTRACT §3 逐字段构造后传 `postAction(body)`——**一个多余字段都会被 server 400（UNKNOWN_FIELD zero-tolerance）**。动作发出后不做乐观更新，等 SSE → refetch 回流（单向数据流）。
- wire 类型在 `types.ts`：add-only，只加 optional 字段，绝不改名/收紧；渲染未知枚举值时按字符串兜底显示。

## i18n / 主题 / 路由
- 所有用户可见文案：`const { text } = useI18n()` → `text("中文", "English")` 内联对。固定枚举词表（列名/tier/动词）加到 `i18n.ts` 的 Record 表（仿 dashi STATUS_LABELS）。
- **server-owned 文案不落 client**（防腐十条 #10）：列说明（`GET /api/lanes`，store.lanes）、canonical 模型列表等由 server 目录给 zh/en 两键，组件按 `language` 取键、逐字镜像，绝不在 client 再写一份。原生看板（mac/Sources，冻结）是文案与行为规格——CONTRACT §54.1 是 parity 清单。
- 相对时间一律经 `relativeTime.ts`（刚刚/N分钟前/N小时前/N天前 + 时长 N小时M分），hover `title` 给绝对时间；卡片排序经 `cardSort.ts`（偏好键 `cardSortOrder`）。
- 颜色/阴影/字体只用 `var(--...)` token。暗色自动生效（data-theme 覆写 + prefers-color-scheme 兜底，见 tokens.css 头注释）；列语义→token 映射也在那条注释里。主题切换组件写 `localStorage["zai.theme"]` + `document.documentElement.dataset.theme`。
- **字号 / 字重 / 描边三把旋钮（CONTRACT §54.1 第 12 项）**：组件 CSS 里字号只许 `font: var(--type-…)` 或 `font-size: calc(<px> * var(--text-scale))`，字重只许 `var(--w-regular|medium|semibold|bold)`，描边只许 `var(--stroke-w)`——字面 px 字号、数值字重、0.5–1.5px 描边一律被 `displayPrefs.test.ts` 判红。值 → 变量的映射只住 tokens.css；JS 只写 `<html>` 的 `data-text-size` / `data-text-weight` / `data-stroke`（`displayPrefs.ts`），别在组件里碰这三个变量。
- 深链只经 `route.ts`（`?page=` + `?card=`）；抽屉开合调 `selectCard(id|null)` 并用 `history.replaceState(buildAppUrl(...))` 同步 URL。过滤器序列化独立成 `taskFilters.ts`（A8）。

## 语义红线（契约 §0.8）
- 看板列是审批状态机投影：**没有拖拽换状态**，一切转移都是显式按钮动词。动效可加（A9 的 fork 动画块），语义不可加。
