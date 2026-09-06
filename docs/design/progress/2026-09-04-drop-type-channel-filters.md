pr: `feat/drop-type-channel-filters`（web + docs；无版本 bump，版本由 tag 派生）
phase: P4 看板收口（D28 新增；R2.2.6 新增）
law: §49 追记（T-21 旁：FilterBar 维度 = tier / deadline / reraised + ⌘F；类型 / 渠道 retired；旧 `type=` / `channel=` 容忍读取）/ §68 判例 `FilterBar.test.tsx` 括注

**决策**：owner 2026-09-04「‘类型’， ‘渠道’我觉得可以去掉。」→ D28。筛选条的五个维度来自上一轮 v-next 的外部 build contract `dashi-research/BUILD-CONTRACT.md` §2.2（repo 外，仅 docs/design/vnext.md 转引），不在本轮 §2 需求清单里；「类型」只对 debt / trash 行有意义、「渠道」只对带 sources 的行有意义，两颗 chip 都是「看板上有值才长出来」的条件渲染，owner 看了觉得多余。

**做了**：`web/src/taskFilters.ts` 的 `CardFilters` 去掉 `types` / `channels` 两字段、`readCardFilters` / `applyCardFilters` 去掉 `type=` / `channel=` 序列化、`matchesCardFilters` 去掉两段匹配、`collectTypes` / `collectChannels` / `rowChannels` 整段删除；新 `LEGACY_PARAMS = ["type", "channel"]`——读时不认、`applyCardFilters` 写时顺手 `delete`，旧书签 / 深链带着它们进来不报错、下次任何一次过滤器写回即消失。`FilterBar.tsx` 删两颗 `TaskPropertyPicker`、`ChipKey` 收成 `"tier" | "deadline"`、`chipContent` / `multiOptions` 去掉只为这两维存在的 `LabelTable` 参数；`i18n.ts` 的 `CHANNEL_LABELS` 随之退役（唯一消费者）。`TYPE_LABELS` 留着（DebtCardItem / ProposalCard / BacklogStrip 卡面 chip 与 styleguide 仍用）。⌘F 搜索不动：`SEARCH_FIELDS` 仍含 `type`，`sources[].channel` 仍进搜索文本。

**判例**：`taskFilters.test.ts` 新增「D28：旧 URL 的 type= / channel= 容忍读取、下次写回丢弃、其余参数不动」与「debt 行的 type / channel 不再是过滤维度但仍可被 ⌘F 搜到」，`cardFilterCount` 满配从 6 → 4；`FilterBar.test.tsx` 新增「chip 恰好三颗 Tier / 期限 / 回锅 + 搜索框——看板带 type / channel 也不长出类型、渠道 chip」（mock `fetchBoard` 喂一块两维都有值的看板）、深链水合那条加上旧参数并断言首次写回后 URL 里没有它们；`BacklogStrip.test.tsx` 「吃全局过滤器」那条从 `types` 改走 `search`（D28 后 debt 行唯一适用的维度）。删掉 `collectTypes` / `collectChannels` / `channel 从 sources 取` 三条死测试。

**门**：web typecheck / build / vitest 67 文件 1278 过；ruff / compileall / unittest 5834 过；`scripts/qa/run_gates.sh` 六门 OK——ui-parity PRESENT 844 / PENDING 15 / MISSING 0 / STALE 0 / WAIVED 4（原生 inventory 从没有这两颗 chip，`pending.txt` / `waivers.txt` 零改动）。**未做**：`web/e2e/__screenshots__` 六张 golden（board / trash / settings × light / dark）的顶栏都会少两颗 chip，本 PR 不重拍，另开 PR 显式 `npm run visual:update`。
