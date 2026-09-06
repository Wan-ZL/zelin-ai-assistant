pr: `feat/rail-recaps-under-board`（无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；owner 决策 D32（会议纪要紧跟任务台）
law: §54.4 追记（导航栏七项、⌘1…⌘7、分隔线退役、判例清单）/ §63.5 追记（入口位置，指回 §54.4）

**owner 原话（2026-09-04）**：「我希望会议纪要在任务台下面」。

**做了什么**：`NavRail.tsx` 里会议纪要从分隔线下方搬到第二位、tooltip 带 `(⌘2)`；`shortcutPage` 的顺序表插入 `recaps`，原生六项整体后移一位（录制与数据接入 ⌘3 / 回收站 ⌘4 / 永久性完成 ⌘5 / 设置 ⌘6 / 关于 ⌘7）。分隔线下再无条目：`<div class="rail-divider" role="separator">` 与 `shell.css` 的 `.rail-divider` 规则一并删除（`--border-hairline` token 别处仍在用，不动）。**没动**：会议纪要仍不带 `data-rail-item`（标 `data-rail-extra="recaps"`）、不进 `RAIL_PAGE` / `mainSection`（原生 UserDefaults 没有这个值，`activeRailSlug("recaps")` 仍是 null）；`RailLink` 组件、六个原生条目的字面锚、清单、提取器、探针一个字没改。

**parity（§66）**：`parity_check._rail_order_ok` 用 `re.findall(r'data-rail-item="…"')` 读源码里锚的出现顺序，会议纪要没有锚，插在 dashboard 与 ingest 之间不改判决；`run_gates.sh` 报告 gated 851：PRESENT 832 / PENDING 15 / MISSING 0 / STALE 0 / WAIVED 4，与 #207 / #208 相同，`report.*` 零 diff（truth = `ui/parity/report.json`），两本账本零改动。

**判例**：`NavRail.test.tsx` +1（七项 `.rail-item` 文案顺序 zh、会议纪要第二且无 `data-rail-item`、`.rail-divider` 不存在、带锚六项仍 = 清单 gated 顺序、`?page=recaps` 点亮且不点亮任务台、`activeRailSlug("recaps")` null）；既有判例改钉 ⌘1…⌘7（收起态 tooltip「Trash (⌘4)」/「回收站 (⌘4)」/「Recaps (⌘2)」/「会议纪要 (⌘2)」，⌘6 → settings、⌘2 → recaps、⌘3 → ingest、⌘7 → about、⌘8 / ⌘9 无页、输入框里不劫持）、`rememberMainSection("recaps")` 不记。

**门**：web typecheck / build / vitest 68 文件 1300 通过（4 skipped）；ruff 0 / unittest 5833 通过（3 skipped）；`scripts/qa/run_gates.sh` 六道 OK（complexity / crap / coverage-floor 97.26% / deps / hygiene / ui-parity）。**Playwright golden 未重生成**：刷 golden 的 PR（`chore/visual-goldens-2026-09-04`）尚未合并进 main，六张 golden 自 #204 / #205 起本就全红，本 PR 只让侧栏多一项在第二位——留给刷 golden 的 PR 一并更新。
