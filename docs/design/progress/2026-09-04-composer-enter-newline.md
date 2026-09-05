pr: `feat/composer-enter-newline`（无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；owner 决策 D35（列顶输入框 Enter = 换行、只按钮提交）
law: §41 追记（web 列顶输入框键盘纪律 + 「Return 发送」墓碑）/ §66.1 追记（`CONTROL_OWNER` 收原生键位提示句）

**owner 原话（2026-09-04，附提案列「捕获」与运行中列「直跑」两个输入框的截图）**：「这个我回车我不希望是直接跑而是下一行，要跑是需要点击按钮。」

**做了什么**：`web/src/components/board/LaneComposer.tsx`（`BoardLanes.tsx` 里的两个实例）从单行 `<input>` 换成 `<textarea>`：`fitComposerRows` 按 scrollHeight / 行高把 `rows` 定在 1…5（行高 / 内边距从 computed style 读，单源 `--type-composer`；软换行的长句也长高），第 6 行起框内滚动。Enter 不拦（浏览器原生换行，IME 候选上屏的回车天然安全），Shift+Enter 也是换行，⌘↵ / Ctrl+↵ 也不提交——**键盘上没有提交键，只有「捕获」/「直跑」按钮**。换行原样进 wire `text`（server `inbox_writer` 只判非空）——**诚实注**：只到 inbox 文件为止，actd `_capture_text`（§10）目前仍把空白含换行折成单空格，多行是编辑体验、落卡与派发时为单行（既有守护行为，原生 Shift+Return 同路），保留换行需另立 §10 追记；空草稿不量 scrollHeight、恒为 1 行（Chromium / WebKit 都把软换行的 placeholder 算进 scrollHeight，英文 + `text_size=xl` 时删空会弹回 2 行——#220 审查发现并修）；草稿保留不变（仅成功后清空）；Esc 只 blur 不丢草稿；↑/↓ 历史条件不变（空草稿或翻历史中才接管），改字即退出翻历史（此前不退出，单行时无感、多行时会劫走光标键）；IME 组合中任何键不接管。`board.css`：`.lane-composer` 按钮贴底（原生 `HStack(alignment: .bottom)`），`--composer-row-inner` 让按钮最小高 = 一行 textarea——demo seed 实测一行几何与旧 `<input>` 逐像素相同（28.8px），Playwright 视觉 golden 六张零 diff、未重生成。`app.tsx` 壳 `quick_capture` 聚焦选择器改 `.lane-composer textarea`。**诚实注**：原生 Composer.swift 是 Return 发送 / Shift+Return 换行；web 曾是回车提交；D35 同时取代两者。

**parity（§66）**：`CONTROL_OWNER` 收 `control:board.composer:copy:send-newline-esc-dismiss-v-pastes-images`（原生「↩ 发送 · ⇧↩ 换行 · Esc 退出 · ⌘V 可贴图」；copy 本就只列不判，点名是落成判例、理由带 D35），清单重铸；`parity_check` gated 851：PRESENT 832 / PENDING 15 / MISSING 0 / STALE 0 / WAIVED 4（不判分类 informational 415 → 414、retired 53 → 54，`report.*` 随之更新），两本账本零改动。

**判例**：新 `LaneComposer.test.tsx` 16 条（textarea 不是 input；Enter / Shift+Enter / ⌘Enter / Ctrl+Enter 都不提交也不 preventDefault；按钮提交换行原样、成功清空并进历史；纯空白（含裸换行）按钮禁用；失败留多行草稿 + 「Submit failed — input kept」；rows 1→3→5、8 行仍 5、清空回 1；软换行按 scrollHeight 长；placeholder 软换行的空草稿仍 1 行；无布局不动 rows；↑/↓ 空草稿接管 / 多行草稿不接管 / 改字退出翻历史；Esc blur 留草稿；IME 组合中不接管）；`parity.test.tsx` 的 composer 失败句采集改抓 `textarea`。

**门**：web typecheck / build / vitest 69 文件 1316 通过（4 skipped）；Playwright `headerLayout` + `visual` 32 通过（golden 零 diff）；unittest 全绿；`parity_check` OK。
