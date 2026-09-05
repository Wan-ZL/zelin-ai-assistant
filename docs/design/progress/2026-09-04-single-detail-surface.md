pr: `feat/single-detail-surface`（issue #217；无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；owner 决策 D34（卡片详情只留侧栏一面）
law: §49 追记（单一详情面 + 渲染器归一 + T2 闸门改读 detailViewedIds + parity 改判）/ §54.1 第 2 项 tombstone（就地展开 + `store.expandedCardIds` retired）/ §66.1 追记（`CONTROL_OWNER` 收两条「收起 ▾」）

**owner 原话（2026-09-04）**：「我还看到了 217 这个 issue，也是我提的。我觉得这应该属于功能设计的修改，你可以看看如何修改。我觉得挺好的，你觉得呢？如果你觉得好的话，也帮我改。」

**做了什么**：卡上「展开详情 ▸」自此调 `openCardDetail`（选中卡 + `?card=` 深链）打开右侧详情侧栏，不再就地撑开卡片；焦点在卡上按 Enter / Space 同一入口；卡上的 `onDoubleClick` 删掉（双击语义留给 #216 终端接管，`server/terminal_launch.py` / `shell/` 一字未动）。就地展开整体删除：`store.expandedCardIds` / `toggleCardExpanded`、`cardChrome.CardDetails` / `useCardExpanded`、`board/detailBlocks.tsx`、五张卡里的 `<CardDetails>` 块、`board.css` 的 `.card-detail-*` 详情槽样式、只剩它们消费的三个 type token（`--type-detail-title` / `--type-card-error-mono` / `--type-card-body-strong`；`typeScale.ts` 同步）。`DetailFields` 成为唯一渲染器：先对照两套渲染逐块比过再删——侧栏此前缺的积木全部补上并逐字原生标签（提案 `💰 预计费用: $N` / `💰 成本未知` 只在 needs_approval 列；`💬 需求来自` / `📋 要做什么`（"[修改方向]" 行橙）/ `怎样算办完：`；待验收 `交付了什么：` + `验收清单——逐条对照：` §11 永远渲染带兜底句；`错误全文` + 复制 / 已复制；`日志：` / `指令：` 各自一节点 + 复制、`resumeCommand` 与卡面同源（搬进 `boardActions.ts`）；`会话 ID：` / `claude agents 列表名：`；需输入列指令行用 §39 `在终端接管会话：`），旧的 web 自造小节名（计划 / 验收标准 / 来源引文 / 交付总结 / 会话·复制命令）退场。**T2 闸门**：「T2 需先展开看明细」字面不变，语义改读新的 `store.detailViewedIds`（本会话打开过侧栏的卡；`selectCard` 记入、关侧栏不忘）——侧栏是带背板的 modal，开着时卡面点不到，闸门不能读「正开着」。

**parity（§66）**：`CONTROL_OWNER` 收 `control:board.card:button:collapse` / `control:board.needs_approval:button:collapse`（「收起 ▾」，侧栏关闭是 × / ⎋），理由带 §49；详情槽其余 16 条 id 照判、渲染面换侧栏——`parity.test.tsx` 看板主遍第 ① 轮改为 `openEachDetail`（逐卡点「展开详情 ▸」、等详情落地收一遍、再点侧栏正文的复制），`fetchCard` mock 回真 `lane`（server 一直如此），轮换提交只轮 `.card-actions` 里的动词。`parity_check.py`：gated 849 → PRESENT 830 / PENDING 15 / MISSING 0 / STALE 0 / WAIVED 4（少的两条即 retired；truth = `ui/parity/report.json`），两本账本零改动。

**判例**：新 `DetailFields.blocks.test.tsx`（侧栏每块积木 + 逐字标签 + 复制回执）、`store.test.ts` +1（`detailViewedIds`）、`a11y.test.tsx` +1（Details ▸ 开 / 双击不开）、`cardParity.test.tsx` 改钉「卡面永远收起、Details ▸ = selectCard + ?card=、无 Collapse ▾、双击不开」、`ProposalCard.test.tsx` / `VerdictChip.test.tsx` 改钉新语义、`tests/test_ui_inventory_extractor.py` 钉两条 collapse retired；真浏览器新 `web/e2e/cardDetail.spec.ts`（点「展开详情 ▸」→ 侧栏带这张卡的标题 + `?card=`、卡片高度不变、⎋ 关；Enter → 同一侧栏；双击不开；`?card=` 深链刷新还原）。

**门**：web typecheck / build / vitest 69 文件 1299 通过（4 skipped）；Playwright `cardDetail` 3 + `headerLayout` 26 + `visual` 6 全过、**六张 golden 未变**（卡面收起态一像素没动）；compileall / unittest 5833 通过（3 skipped）/ hygiene / depgraph / ledger_diff 全 OK。
