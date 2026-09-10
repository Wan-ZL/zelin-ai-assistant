pr: `ai/self-improve/R-205`（PR #321；self_improve lane 草稿 PR，修的是 PR #292 的红）
phase: 横切（CI 卫生；D5/D12「皇上只看绿的」）
law: —（判例改动，无法条变动）

**红在哪**：PR #292（`feat/session-search-layer`，vitest 断网桩）的必需检查 **Web tests (build + vitest)** 红一条：`src/pages/DepsIngestAbout.test.tsx` 的卸载判例 `TestingLibraryElementError: Unable to find an accessible element with the role "button" and name "OK"`。同一条 run 里另一条红是 **Web visual (playwright)**（4 张 golden 5% 像素差）——那个 job 出生就是 `continue-on-error`（`ci.yml:425`，CONTRACT §56.8 的七条必需检查不含它），main 自己的 run（34118772176 / 34201803207）同样红而 run 结论仍是 success，所以它不是本卡要清的账（同 R-201 / R-289 的判词）。

**根因**：`ModalDialog`（`web/src/components/board/ModalDialog.tsx`）在挂载后的 `useEffect` 里调 `showModal()`——`<dialog>` 先进 DOM，`open` 后到。判例先 `await screen.findByText("Uninstall script not found")`：`findByText` 不看可见性，DOM 一变就命中，此刻对话框可能还没 `open`；jsdom 的 UA 样式表里 `dialog:not([open])` 是 `display:none`，不进无障碍树，于是紧随其后的同步 `getByRole("button", { name: "OK" })` 在 runner 上什么都找不到。CI 日志里的 DOM 快照是铁证：`<dialog class="zai-dialog">` 正文俱全却没有 `open` 属性，可访问角色清单里只有页面上的四个按钮、没有对话框。本机 React 的 passive effect 恰好先于 waitFor 的这一轮刷掉、判例照绿（本机全量 207 文件 / 2452 条全绿，加探针断言 `dialog.open === true` 也绿），runner 上调度换个次序就红——是一条早就埋着的竞态，#292 的 `setupFiles` 只是挪动了微任务次序把它掀出来。

**改法（一处判例，两行）**：`fireEvent.click(await screen.findByRole("button", { name: "OK" }))`——按角色取就等到角色出现，与 `LaneComposer.images.test.tsx` 里既有的 `await screen.findByRole("dialog")` 同源写法。生产代码一个字不动；`ModalDialog` 的 `showModal()` 语义正确，错的是判例假设它同步。仓库里同型写法只此一处（`grep 'getByRole("button", { name: "OK" })'` 的另外三处都在 `await findByRole("dialog")` 之后，安全）。提交落在 `feat/session-search-layer` 上（`b313a91f`），因为 DoD 是 PR #292 转绿；本分支只带这份 progress note 与交付说明。

**门**（本机，`PATH=/usr/bin` 优先）：`compileall` OK；`AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests` → Ran 6351 tests, OK (skipped=3)；`cd web && npx tsc -p tsconfig.json --noEmit` OK；`npm run build` OK；`npx vitest run` → 207 文件 / 2452 通过 / 4 跳过。ruff 本机未装（改动零 Python，CI 的 Lint 腿判）。无版本 pin 改动、无 changelog fragment（不面向用户）。
