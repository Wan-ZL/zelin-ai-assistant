pr: `feat/lane-motion-retire`（决策批次 `lane-motion-retire`，chain motion 第 1 批；无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；行为审计 15 项决策的第 D46 项（gap: board-cards-lane-change-motion）
law: §68.14 追记（1.16 看板动画「未做」→ tombstone「不做、不计划」）

**owner 授权（2026-09-06）**：「你之前可能问过我做不做的问题，我会经常忽略掉你的文字。但是我告诉你我希望的是所有我希望有的功能全部都推进到完成，能够完美使用」→ 15 项审计决策整批委托，本批 Claude 代拍 D46 = 审计问题「要不要补做换列飞行 / deal-in / 书立条脉冲」的选项 (c)：正式退役、留 tombstone，不做也不再挂账。

**为什么是 (c)**：原生 §43 的 BoardMotion / BoardDiff 层是纯展示层，web 从未移植（§68.14 1.16 自 s4 起一直是「未做」），审计也判它 user_impact low；D34（单一详情面）的成立理由就是减少泳道布局跳动，一张卡换列时飞过整块看板恰是最大的一次布局动感，与之背道；web 看板回流是 SSE 推的整版快照，同一版里多张卡同时换列时飞行层只能猜起点（原生自己也对 nil / 越界端点直接放弃飞行）。补做 = 新增一层与既有方向相反的动效 + 一份 BoardDiff 移植，收益不抵。

**做了什么**：(1) `web/src/styles/animations.css` 删掉 fork 来的 `.task-card.is-moving` / `.is-settling` / `@keyframes task-card-settle`——grep 全 web/src 无任何 TSX / CSS 引用；头部差异清单加 ③ 记下删了什么与为什么。留下的动效（hover 抬升、sheen / conic 进度环、侧栏滑入、导航栏折叠、spinner）与两重降级（`prefers-reduced-motion` + 「看板动画」开关）一字不动。(2) §66 清单：`scripts/ui/extract_native_inventory.py` 的 `CONTROL_OWNER` 收 `control:board:label:card`（BoardMotion.titlesFor 给飞行标签用的兜底词「卡片 / Card」，飞行层不存在就没有落点），理由带 D46 + §68.14，`native-inventory.json` 重铸（gated 847 → 846，retired 58 → 59）。(3) `ui/parity/pending.txt` 划掉该行（13 → 12，truth = 该文件行数），`waivers.txt` 零改动，`report.json` / `report.md` 重铸（PENDING 13 → 12，MISSING 0）。(4) CONTRACT §68.14 追记 tombstone；`vnext2-plan.md` 决策台账 D46 行。(5) 判例新文件 `tests/test_ui_parity_lane_motion_retired.py`（CONTROL_OWNER 条目带 D46 / §68.14；已提交 JSON gated=False；pending / waivers 都不挂；animations.css 无三条规则、留下的动效与两重降级仍在；web/src 全树无三个名字回流）。未加任何新 UI、未动 TSX，视觉 golden 不变。

**门**：compileall / unittest 全过；web typecheck / build / vitest 166 文件 2135 过（4 skipped）；`parity_check.py` OK（gated 846 / PRESENT 830 / PENDING 12 / MISSING 0 / STALE 0 / WAIVED 4）；hygiene / depgraph / ledger_diff / changelog_fragments / progress_log 过。
