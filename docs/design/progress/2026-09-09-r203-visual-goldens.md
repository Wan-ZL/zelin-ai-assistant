pr: `ai/self-improve/R-203`（self-improve 车道，卡 R-203「修红 CI：PR #291」；起因 = daily_loop 2026-09-09 看见开放 PR 上有红）
phase: 横切（CI 卫生；§66.4 视觉基线）
law: §66.4（第一条容差 / 遮罩的字面量改指针；重拍流程本身零修订，本轮就是照它走的第一次「非 UI PR 补拍」）

**红在哪**：PR #291（`ai/self-improve/R-290`，screenpipe 磁盘用量 + 保留天数）的检查里只有一条红——informational 的「Web visual (playwright)」，四张 golden 不匹配（看板 / 设置 × light / dark，回收站两张绿）。查 main 自己的 CI 发现同样四张在 main 上也红（run 34201803207、34118772176 …，自 2026-09-05 那次重拍之后一路红），所以这不是 #291 弄坏的，是 main 的 golden 欠了债、#291 只是被它绊住。

**债从哪来**：上一次 golden 出自 `834d0e55`（2026-09-05，main @ f297b9b）。此后落地的 UI 决策没有一个带上重拍——比对 diff 三件套（artifact `web-visual` 的 expected / actual / diff）逐项对上：看板面上多出的「怎样算办完 / 验收清单——逐条对照」是 D43（`beae42f1`），卡面少掉的「在终端接管」是 D36（`ad47d87c`），输入框右侧新出的回形针是 D41（`31bd352a`）；设置页多出的分区与行是 D44 / D45 / D47 / D51 / D53（分区开合、⌘F 会话搜索、语气档案生成、Obsidian 登记库、headless fallback model）。即：**四张 golden 的漂移背后是四五个已批的决策**，不是回归——CONTRIBUTING「Visual baselines」要求「先看 diff 再更新」，这一步是看完 diff 才动的。

**怎么修**：按 §66.4 2026-09-05 追记的唯一合法路径——golden 只出自 `.github/workflows/visual-goldens.yml`，绝不出自任何一台 Mac（笔记本的文字光栅化与 `macos-latest` 不同，每个 CJK 字形都会「回归」）。本轮 `workflow_dispatch` 跑了两次：`ref=main`（run 34340759446）拍出本 PR 提交的这套，`ref=ai/self-improve/R-290`（run 34340900799）拍出 #291 分支上那套（它的设置页多一块「磁盘与保留」，golden 得从它自己的树里出）。两次的 capture 之后都紧跟同一 runner 的 verify——runner 必须复现自己的输出，verify 绿才允许提交。

**#291 与本 PR 的分工**：main 的 golden 归本 PR，带「磁盘与保留」的那套归 #291 自己的分支（已推上去，推完重跑「Web visual (playwright)」= pass，它 11 项检查全绿）。两次 capture 逐字节比过：`board-dark` / `trash-dark` / `trash-light` 三张两边完全相同，`settings-light` / `settings-dark`（#291 多一块「磁盘与保留」）与 `board-light`（看板卡面上的相对时间文案随 capture 时刻漂一两个字，本来在 0.2% 容差内）三张不同——所以谁后合并谁在这 **3 个二进制文件**上撞一次；解法固定：**取带新 UI 的那一侧**（#291 的），因为它是后一版设置页。

**顺手一针**：§66.4 第一条把容差写成了字面量「`maxDiffPixelRatio` 2%」，而 `web/playwright.config.ts` 的真值是 `0.002`（0.2%，`c7ef8524` 收紧过一次），遮罩也少列了 `.settings-global-path` 一处。按防腐十条第 5 条改成指针（truth = 配置与 spec 本身），免得下一个 session 按 2% 估「这点差异应该能过」。

**没做什么**：不动 spec、不动阈值、不动 `visual-goldens.yml`（protected path），不在本地 `visual:update`（产出的 png 按 §66.4 不许提交），不把这个 job 升成必需检查（那是另一条决策，出生 informational 的理由还在）。
