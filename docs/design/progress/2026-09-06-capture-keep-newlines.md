pr: `feat/capture-keep-newlines`（行为对齐审计决策批次 `capture-keep-newlines`，chain `composer` 第 2 棒；D35 审查留下的 follow-up）
phase: D52（owner 2026-09-06 整批授权「所有我希望有的功能全部都推进到完成」→ Claude 代拍选项 (a)：换行活到卡上，title 折成一行）
law: §10 追记（capture 正文归一 `normalize_capture_text` / 单行 `capture_title`）/ §41 追记修正（D35 (b) 「多行只到 inbox 文件为止」失效）

**补回什么**：D35 把列顶输入框改成多行 textarea，但 §41 追记 (b) 诚实注明 actd `_capture_text`（`act/lib/actd/inbox.py`）仍把全部空白含换行折成单空格——用户排好的列表 / 段落在 LLM 扩写与 agent 派发之前就成了一行糊，多行编辑体验没有落地价值。Slack self-DM 通道（§13 `quick_capture.py`）本就保留原话换行、只在兜底 title 上折行，inbox capture 是唯一折平的入口。

**怎么补**：`inbox.py` 新增两个 public 纯函数——`normalize_capture_text`（CRLF / 孤 CR → LF；换行保留；每行内部空白串折单空格、行尾空白剥掉；行首缩进只认空格与 tab，最后剥掉非空行的公共缩进、保留相对缩进；连续空行最多 1 行；首尾空行剥掉；单行输入与旧规则逐字相同）与 `capture_title`（整段折成一行再截 [:80]）；`_capture_text` 改走前者，`apply_capture` 的 `title=` 改走后者。归一后的多行正文进 `registry.capture_source` 的 `quote`、`_capture_proposal`（merge_or_new / fold 回执内容键 / analytics gated 文本）与 `_capture_direct_run`，于是 `analyze.build_expand_prompt` 的 SOURCES 块与 `dispatch_prompt` 的围栏引文都按用户的行呈现。web 半边：详情页 `.zai-detail-source-quote` 加 `white-space: pre-wrap`（原生 SwiftUI `Text` 本就逐行显示）；「复制为 Markdown」（`cardMarkdown.ts`）的多行引文续行缩进到列表项内容列（4 格）、空行不带尾随空格，整段仍是同一个列表项。inbox 文件形状 / server `inbox_writer` / `images[]` / `capture_id` / dashboard 投影零改动。

**开放细节的取法**：spec 允许 title「首行或折平」——取折平：title 是判重 / re-raise 的身份锚（§37 set_title 只改显示名），与 `_norm_title` 的空白折叠同口径，既有「title == text」单行判例零改动，而首行做标题会让「1.」「TODO:」之类的短首行成为身份；行首缩进保留是 spec 之外的一处加法（spec 只说「行内空白串折单空格」），嵌套列表与贴进来的代码靠它——review 抓到首版用 `.strip()` 收尾把首行缩进单独吃掉（`"  - a\n  - b"` 变父子）、且 `lstrip()` 把 NBSP / FF 之类也当缩进留下，改为「只认空格与 tab、剥公共缩进保留相对缩进」；同一轮 review 也补上 web 第二个 quote 消费者 `cardMarkdown.ts`（多行引文曾把自己的项目符号逃成小节顶层项）。

**判例**：新 `tests/test_capture_keep_newlines.py`（20：归一规则九例——单行等价旧规则 / CRLF / 行内折叠与行尾 / 缩进保留 / 空行上限 / 首尾剥离且首行缩进按相对算 / 公共缩进剥离 / 只有空格与 tab 算缩进 / 全空白为空；title 折行 / 80 上限 / 单行原样；端到端——提案 quote 保留换行且 title 单行、YAML 落盘往返、直跑同样保留、CRLF 客户端、全空白 noop 不铸卡、有无换行判重成一张、扩写 prompt 与派发 prompt 带行）；`cardMarkdown.test.ts` 多行引文一例。全套 6295 条全绿（rebase 到 2026-09-07 main 后）；`ui/parity` 判卷面零变化（PRESENT 835 / PENDING 7 / MISSING 0）。**视觉 golden 不变**（三张 golden 页不含详情栏，demo 引文无换行）。
