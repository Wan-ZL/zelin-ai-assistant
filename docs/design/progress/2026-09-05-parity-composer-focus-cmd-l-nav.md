pr: `fix/parity-composer-focus-cmd-l-nav`（无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；行为对齐审计 batch `composer-focus-cmd-l-nav`（chain nav-focus 第 1 批；gap ids composer-cmd-l-focus / pages-shell-nav-shortcut-cmd-l-focus-capture / pages-shell-nav-quick-capture-offboard-loses-focus / pages-shell-nav-sidebar-collapse-animation）
law: §54.4 追记（⌘L 落点、`zai.pendingFocus` 接力、⌥⌘S tombstone、折叠动效、判例清单）

**做了什么**：原生 View ▸ 聚焦捕获框（⌘L，`AppDelegate.swift focusCaptureField` → `Composer.swift` 只有 propose composer 响应、已开着就只 refocus）在 web 落成 NEW `web/src/components/board/focusComposer.ts`：`focusComposer()` 聚焦第一个 `.board-column .lane-composer textarea`（提案列）、光标 `setSelectionRange` 到草稿末尾——不再 `select()` 全选；不在看板页先写 sessionStorage `zai.pendingFocus=composer` 再 `navigate` 回看板，`BoardPage` 挂载时 `consumePendingFocus()` 一次性消费（读到即删，刷新不重放；坏值也清；sessionStorage 抛错不崩）。`NavRail` 的 window keydown 除 ⌘1…⌘7 外另认 ⌘L（只认 ⌘ 单修饰，输入框里也接）→ preventDefault + `focusComposer()`；`app.tsx` 的 `quick_capture` 处理器（壳的 ⌃⌥Space、`shell-menu-l10n` 批要加的壳菜单 ⌘L）改调同一个 `focusComposer()`，三条入口一个落点。侧栏折叠 / 展开动效：`shell.css` 在 `@media (prefers-reduced-motion: no-preference)` 下给 `.rail` `transition: width 150ms ease-in-out, padding 150ms ease-in-out`（原生 `MainWindow.swift` `.animation(.easeInOut(duration: 0.15), value: sidebarCollapsed)`），拖把手期间 `NavRail` 挂 `is-dragging`（`transition: none`），pointerup / pointercancel 摘掉。**没做**：⌥⌘S 折叠侧栏（原生 View 菜单）随 s4 DELETE 退役、owner 决策不移植，§54.4 追记留 tombstone；提案 textarea 的 tooltip「快速捕获（⌘L）」（清单 `control:board.composer:help:quick-capture-l`，只列不判）没加——`LaneComposer.tsx` 归 composer chain 三批并行在改，避免同文件冲突，且 web 没有原生那个折叠行；shell 菜单项本身归 `shell-menu-l10n` 批。

**诚实注**：浏览器标签页里 ⌘L 是地址栏快捷键、页面拦不到，壳（WKWebView）里可用；原生 ⌘L 只前置窗口不换页（看板之外的原生页没有 composer），web 的「先回看板再聚焦」是更宽容的实现。

**parity（§66）**：`shortcut:menu.main:cmd-l-focus-capture-field`（owner=shell，只列不判）现由 web 与壳共同交付；`opt-cmd-s-collapse-expand-sidebar` 按退役读；`python3 scripts/ui/parity_check.py` gated 849：PRESENT 830 / PENDING 15 / MISSING 0 / STALE 0 / WAIVED 4，`report.json` 零 diff，两本账本零改动。

**判例**（三个 NEW 文件，防腐 #7 一行为一文件）：`focusComposer.test.ts`（6 条）、`NavRail.cmdL.test.tsx`（5 条：⌘L 聚焦 + preventDefault、输入框里也接、Caps Lock 的 L、⌃L / ⌥⌘L / ⇧⌘L 不算、⌘1…⌘7 照旧、⌥⌘S 什么都不做、离板接力）、`NavRail.collapseMotion.test.tsx`（2 条：CSS 过渡只在 no-preference 块且 is-dragging 关掉、拖动挂 / 摘 is-dragging、折叠钮不挂）。既有 `NavRail.test.tsx` 未改。

**门**：web typecheck / build / vitest 73 文件 1339 通过（4 skipped）；compileall / hygiene / depgraph OK；unittest 全量见 PR。**Playwright golden 未重生成**：静态截图里侧栏展开态一像素没动（过渡只在切换瞬间存在）。
