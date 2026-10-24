# web/ 组件约定（动手前必读——脚手架已定型，跟着走别另起炉灶）

v2.0.0（lite）：页面 = 会议纪要（缺省页）/ 录制与导入 / 设置 / 关于 + 权限体检 / 首次运行向导。1.x 的看板、详情抽屉、过滤栏
与它们的约定随 Act 管线退役，全文见 tag `full`。

## 目录与命名
- `src/*.ts` 小写 = 非组件模块（`api.ts` / `store.ts` / `realtime.ts` / `i18n.ts` / `route.ts` / `types.ts` / `copyText.ts`）。新的纯逻辑模块照此命名。
- `src/pages/PascalCase.tsx` = 页面级组件（`RecapsPage.tsx` / `SettingsPage.tsx` / `IngestPage.tsx` / `AboutPage.tsx` / `PermissionsPage.tsx` / `SetupPage.tsx`）。
- `src/components/<area>/PascalCase.tsx` = 可复用组件，按页面区域分目录（`recaps/` `settings/` `setup/` `permissions/` `shell/`）；跨页共用的小件（`ModalDialog` / `ForkDialog` / `RelativeTime` / `CopiedAnnouncer`）住 `components/shell/`。
- `src/styles/tokens.css` = 唯一 token 源；跨页控件（按钮 / chip / 原生 `<dialog>` / 页头）在 `src/styles/controls.css`，壳层布局在 `src/styles/shell.css`，各区自己的样式跟组件同目录（`settings.css` / `recaps.css` / `permissions.css`）。只允许引用 tokens.css 里已有变量，不造新色值。
- 测试与被测模块同目录同名：`foo.test.ts(x)`。vitest 环境 jsdom，显式 `import { describe, it, expect, vi } from "vitest"`（未开 globals）。**判例环境没有网络**（防腐 #7）：`src/vitest.setup.ts`（`vite.config.ts` `test.setupFiles`）把 `globalThis.fetch` 换成不开 socket、立刻拒绝的桩——会碰 api.ts 的判例用 `vi.mock("./api", …)` 盖住用到的函数，要真响应形状就 `vi.stubGlobal("fetch", …)`；没 mock 住的调用走断网路径（`READ_FAILED`），不会真打 localhost。
- 单文件 <300 行（存量超线的按防腐十条 #1 处理）。

## Props 风格
- 具名导出 function 组件（`export function RecapList(...)`），不用 default export、不用 `React.FC`。
- Props 一律内联解构 + 独立 interface（组件名 + `Props` 后缀），callback 命名 `onXxx`，布尔命名 `isXxx`/`showXxx`。
- 事件回调只上抛语义动作（如 `onSelect(key)`），不上抛原始 DOM event。

## 组件怎么读/写 state（红线）
- 读：`const { recaps, connection } = useAppState()`（`store.ts` 唯一入口，整快照返回，随手解构）。禁止组件间 prop-drilling 全局数据、禁止新开 Context 存业务态（Context 只有 `LanguageContext`）。
- 写：只调 `store.ts` 导出的 action 函数（`refreshRecaps` / `markRecap` / `saveSettingsSection` / `setConnection`…）。新增 UI 态 = `AppState` 加字段 + 加 action，同一文件。组件内 `useState` 只许存纯本地瞬态（输入框草稿、hover、弹窗开合）。
- 服务端交互只经 `api.ts` 导出的函数，它只导出 `server/app.py` 路由表里还在的路由（CONTRACT §49）。请求体按 CONTRACT 逐字段构造（例：`postRecapGenerate({ key, note?, partial?, shape?, answers? })`）——**一个多余字段都会被 server 400（UNKNOWN_FIELD zero-tolerance）**。动作发出后等 SSE `recaps.updated` → refetch 回流（单向数据流）；只有回执自己说清了结果的才落本地乐观态（§63.5 标记 / §63.8 排队中）。
- wire 类型在 `types.ts`：add-only，只加 optional 字段，绝不改名/收紧；渲染未知枚举值时按字符串兜底显示。整族退役（art.6 v2.0 条款）时同 PR 删掉。

## i18n / 主题 / 路由
- 所有用户可见文案：`const { text } = useI18n()` → `text("中文", "English")` 内联对。固定枚举词表若再出现，加到 `i18n.ts` 的 Record 表（仿 dashi STATUS_LABELS）。
- **server-owned 文案不落 client**（防腐十条 #10）：设置目录的标题 / 字段说明、canonical 模型列表、失败目录（`GET /api/failures`）等由 server 给 zh/en 两键，组件按 `language` 取键、逐字镜像，绝不在 client 再写一份。
- 相对时间一律经 `relativeTime.ts`（刚刚/N分钟前/N小时前/N天前 + 时长 N小时M分），渲染经 `components/shell/RelativeTime`，hover `title` 给绝对时间。
- 颜色/阴影/字体只用 `var(--...)` token。暗色经 `data-theme` 覆写（见 tokens.css 头注释）。主题切换组件写 `localStorage["zai.theme"]` + `document.documentElement.dataset.theme`。
- **字号 / 字重 / 描边三把旋钮（CONTRACT §54.1 第 12 项）**：组件 CSS 里字号只许 `font: var(--type-…)` 或 `font-size: calc(<px> * var(--text-scale))`，字重只许 `var(--w-regular|medium|semibold|bold)`，描边只许 `var(--stroke-w)`——字面 px 字号、数值字重、0.5–1.5px 描边一律被 `displayPrefs.test.ts` 判红。值 → 变量的映射只住 tokens.css；JS 只写 `<html>` 的 `data-text-size` / `data-text-weight` / `data-stroke`（`displayPrefs.ts`），别在组件里碰这三个变量。
- 深链只经 `route.ts`（`?page=`，缺省 = 会议纪要；设置页区块用 `?anchor=`）；换页经 `navigate()`（pushState，不重载）。

## 语义红线（宪法第 4 条）
- 录到的屏幕只变成笔记与会议纪要：页面上没有任何「发送」路径，纪要唯一的出口是剪贴板（复制）。
