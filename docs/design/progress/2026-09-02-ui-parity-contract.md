pr: `feat/ui-parity-contract`（PR-A UI 对齐契约；无版本 bump，版本由 tag 派生）
phase: P4 前置（D3 的执法面；R2.2.2 验收表机器化）
law: **§63（新增）** / §58.4 追记（ledger_diff 看管 ui/parity/*.txt）

owner 09-02 原话「你不能依靠一个个去看，而是要通过一些硬指标、硬代码、硬文档来进行保证」+ 当天点名四条差距（左侧图标栏被挪到顶栏 / 默认主题该是浅色 / 四列该铺满 / 设置页大量 section 缺席）。

**清单**：`scripts/ui/extract_native_inventory.py` 只读冻结的 mac/Sources，机器提取左侧栏 8 项（含 ⌘1..8）、Settings 19 个 section、全部 `L("zh","en")` 控件（按 SwiftUI 调用链归类，附 file:line）、settings 键、看板 6 列 + 左右书立条 + 每列卡面动词、快捷键、通知 kind、主题/布局指针 → `ui/parity/native-inventory.json`（终版规格；唯一手写 = 归属表 + owner 表 web/shell/os/retired，进 JSON；计数 truth = 该文件与 `ui/parity/report.md`）。

**门** `[ui-parity]`（run_gates.sh 第六道，qa-gates job 装 node）：controls 经清单驱动的 `web/src/parity.test.tsx`（demo fixture 渲染三面 × zh/en，逐字按 accessible name 找；`[pending]` it 反向断言 = 补齐不划账即红）、settings 键对 server/settings*.py、rail / lanes / theme / layout 静态探针；`ui/parity/pending.txt`（出生 = 今日全量缺项，每个加 UI 的 PR 必须让它缩）与 `waivers.txt`（种子 = #119 回答对话框四条）两本 shrink-only 账本进 `ledger_diff`；报告 `ui/parity/report.md`。

**token**：`ui/tokens/native-tokens.json`（macOS 语义色 light/dark、叠层比例、字号梯、layout 定点 400/44/12/16/48…、theme.default=light）+ tokens.css 末尾 `@generated native-tokens` 块（只钉数值，PR-B 接线）。**视觉基线**：@playwright/test，`web/e2e/visual.spec.ts` 三页 × light/dark 1440×900 golden 进仓，CI「Web visual (playwright)」macos informational。

**PR-B**（下一辆车）：左侧 rail 回来、默认浅色、列宽 400 铺满、设置 section 逐个补——每一步以 pending.txt 缩多少为量。
