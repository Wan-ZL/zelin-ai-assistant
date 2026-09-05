pr: `fix/parity-proposal-headline-face-copy`（行为对齐批次 `proposal-headline-face-copy`，链 cards-face 第 1 批）
phase: P4 收尾（D3：web 看板继承原生看板行为规格；§66 审计「原生有、web 丢」清账）
law: §54.1 追记 / 依 §37.1 既有法条（无新 §）

对照退役原生 app 的行为审计找出三条卡面丢失项，一批修回。**标题链**（gap `board-cards-proposal-face-summary`，user_impact high）：原生 `Contract.swift` 把标题分两种面——运行中 / 待验收 / 已完成是名字优先面（`rowTitle` = display_title > name），提案 / AI 研究中占位 / 潜在任务 / 回收站 / 永久性完成是摘要优先面（`displaySummary` = 用户钦定名 > summary > display_title > 冻结 title；§37.1「用户钦定名 > summary（摘要优先面）/ display_title（名字优先面）> 冻结 title」）。web 把前者泛化到了所有面，而 `dashboard.py _display_title` 对每张卡都发非空 `display_title`，于是真数据上提案卡面永远是 LLM 短名、大白话摘要消失；demo 数据又恰好没有 display_title，golden 与判例都没踩到。落点 = 纯函数 `web/src/components/board/cardHeadline.ts`，提案卡的五处（卡面 / aria-label / T2 弹窗 / 拒绝弹窗 / 占位）、潜在任务卡、永久性完成行同读；`types.ts` 四种行补 add-only `user_titled`。详情面按 lane 算卡面 headline，display_title 没上卡面时给一行「显示名」，活标题四键收编进 KNOWN_KEYS（notes_text 不再作为「其他字段」把 notes 渲染两遍）。回收站行留给同链下一批 `pending-sweep-settle`（import 同一函数）。

**卡面文案 / 格式**（gap `board-cards-face-copy-drift`）：回锅章旁的原生小字「你之前验收过这件事，来了新信息」；费用章走已有的 `boardActions.moneyOf`（原生 `money()`：`$12` / `$0.50`）；潜在任务难度章走已有的 `hardnessLabel`（较难 红 / 常规 灰）。运行中列空态句归 `search-fields-normalization` 批次，本批不动。**永久性完成整页文案**（gap `pages-shell-nav-archive-page-copy-drift`）：原生 ArchivePageView 就是 ArchiveSectionView，所以整页与书立条必须一份字面量——`ArchiveStrip.tsx` 导出三对（标题 / 空态 / 无匹配），`ArchivePage.tsx` 改读；顺手去掉书立条标题外多加的第二个 🗄。

demo 数据：P-102 带 LLM 短名（无钦定——卡面仍是 summary，golden 因此零 diff 且从此钉住「短名不挤掉摘要」）、P-114 带钦定名（书立条默认收起，不进截图）；`tests/fixtures/demo_seed/build.golden.json` 与 `ui/parity/fixtures/demo-board.json` 随之重铸。六个新判例文件各钉一个行为（防腐 #7）；ui-parity 门 MISSING 0 / STALE 0，视觉 golden 本地 6/6 通过、未更新。
