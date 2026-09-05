pr: `chore/visual-goldens-2026-09-04`（只刷 6 张 Playwright golden；无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；D28 / D29 / D30 / D31 落地后的视觉基线清账
law: §66.4 法条不动（golden 是它的产物，「改 UI 的 PR 必须显式更新 golden」——本 PR 就是那一次显式更新）

**为什么**：CI job「Web visual (playwright)」自 #204（去 类型 / 渠道 chips，D28）与 #205（导航栏八项 → 六项、依赖检查并入设置页，D29 / D30）起在 main 上全红——两个 PR 都按 §54.4 追记「下一次刷 golden 的 PR 一并更新」把 golden 留了下来，#206 / #208 / #209（D31 顶栏三档密度与收尾）又各自确认「本 PR 与 main 数字逐一相同、未重生成」。刷 golden 前先跑一遍不带 `--update-snapshots` 的 `npm run visual` 复现：恰好 6 条红（board 6324 / 6080、settings 23968 / 14579、trash ≈14.6k / 5090 px），与 #206 片段记的数字一致；`headerLayout.spec.ts` 26 条全绿。

**做了什么**：worktree 上 `PYTHON=/usr/bin/python3 npx playwright test e2e/visual.spec.ts --update-snapshots`（demo seed initial 场景 + 临时 `AIASSISTANT_HOME` + 随机端口，绝不碰生产 state/），`git status` 恰好 6 个文件：`web/e2e/__screenshots__/visual.spec.ts/{board,trash,settings}-{light,dark}.png`。再跑一遍不带 update 的 `npm run visual`：32 passed（26 几何 + 6 视觉）。逐张人眼过：默认浅色主题、左侧导航栏六项（任务台 / 录制与数据接入 / 回收站 / 永久性完成 / 设置 / 关于 + 分隔线下 会议纪要）、顶栏单行 full 档只剩 Tier / 期限 / 回锅 三颗 chip（搜索框与排序下拉随之右移 ~50px）、设置页目录多出「依赖检查」（紧跟「通用」，D30）与「同步 / 配对」（#202 §68.15，同样晚于上次 09-02 刷 golden）——两处都是已合并 PR 的既定变化，没有回归。截止日 chip 里的绝对日期（`2026-09-07` 等）随 seed 的「今天」漂移一两个数字，落在 `maxDiffPixelRatio` 0.002 的预算内（playwright.config.ts 注释已写明这类漂移吞得下）。

**门**：web typecheck / build / vitest 过；ruff / unittest 过；`scripts/qa/run_gates.sh` 六门 OK。不带 changelog 片段——golden 是测试产物，不是用户可见变化。
