pr: `fix/skills-sidebar-page`（PR #361；issue #360，base `dev`）
phase: web 面（§54.4 左侧导航栏 / §67.5 skill 商店的面；owner 授权代拍 D78）
law: §54.4 / §61 / §67.5 追记各一条 2026-09-15（add-only，不开新 §）

**做了什么**：把 Skills（Claude Code 技能）从设置页的一区搬成左侧导航栏的独立页「技能」（`?page=skills`）。新页只有页壳（返回看板 + 页头「技能 / Skills」+ skill 计数），正文是**同一个** `SkillsSection` 组件——没有复制第二份，`GET /api/skills` 的 wire 与行字段一个字没动。导航栏第四项插在「录制与数据接入」之后、「回收站」之前（图标 `puzzlepiece.extension`，同一套 16×16 线稿），⌘ 数字键按栏上八项连续重编（技能 ⌘4，回收站起各后移一位）；与会议纪要（D32）同一性质的 web 自有页——不带 `data-rail-item`、不进 `mainSection`、不参与 §66 清单判卷。设置页原处的区与目录条目原位留着（同一个 id、同一对标题字面量 `SKILLS_TITLE`，设置搜索照旧命中），正文只剩一行入口；旧深链 `?anchor=skills` / `#settings-skills` 到达即 replace 改道到新页（不重放）。壳一行没改：`open_page` 的页名白名单只有 web 的 `route.readPage` 那一份（`shell/Sources` 里没有静态表），这条写进了 §61 追记。

**parity 与 golden**：新页是 web 自有面，`ui/parity/native-inventory.json` 与 `pending.txt` / `waivers.txt` 一个字没动；原生 `screen:settings.skills` 的控件仍判在 settings 面上——`parity.test.tsx` 的 `PAGES.settings` 自此把设置页与技能页渲进同一个池（原生新建表单的「保存 / 取消」仍由设置页其它区提供）。侧栏多一项会动 board / settings / trash 三张视觉 golden：本机没有重拍（§66.4 基线只由 runner 上的 `visual-goldens.yml` 刷），由 dev→main train 统一刷新。

**门**（本机，`/usr/bin/python3`）：`cd web && npm ci && npm run typecheck && npm run build && npx vitest run` → 220 files / 2615 passed / 4 skipped；`npx playwright test e2e/clientRouting.spec.ts` → 4 passed；`scripts/qa/run_gates.sh` → complexity 0 / crap 0 / coverage 97.45% vs floor 83.5% / deps OK / hygiene OK / ui-parity PRESENT 836 · MISSING(new) 0 → qa-gates: OK；`scripts/qa/ledger_diff.py --base origin/main` → 0 findings；`changelog_fragments.py check` / `progress_log.py check` / `tests/test_doc_numbering_unique.py` OK。Python 门与 Swift 门未跑：本 PR 没有 `act/` / `server/` / `shell/` 改动。
