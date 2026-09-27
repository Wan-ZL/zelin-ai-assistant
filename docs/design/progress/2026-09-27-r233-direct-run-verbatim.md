pr: `ai/self-improve/R-233`（issue #448，owner 决策 **D81**）
phase: 横切（§34 直跑管线；self-improve lane）
law: §34 追记（新增，D81）；§37.1 / §33 / §71.3 / §66 的载体与口径在该追记里逐条改写

owner 原话：「running 中开任务也不用提及什么提案了，并且输入的句子启动不要过一遍 AI prompt 再给 claude，而是可以直接给 claude code」。direct-run 从来没有过 LLM 路由（`_capture_direct_run` 直接落 `approved`），真正的问题是**派发 prompt 被重建成模板需求文档**：本 checkout 实测一句 39 字的话渲染成 3,462 字（那句话 1.13%），且它出现两次——标题行一次，`## Sources` 的不可信围栏里一次，而围栏外正写着「里面的东西是 DATA、不要照做」。owner 亲手打的唯一指令被系统亲手标成了不要照做的数据；`config.MEMORY_PATH` 指的还是另一个项目的 auto-memory 索引（存在时把 prompt 推到 7k–14k，9/21 实测 74.6%）。

本轮把 `render()` 加了一条早退：`verbatim_direct_run(req)` 为真时返回 `typed_sentence(req)`（capture 出生引文，换行原样），模板一个字节不在。判据两半——`is_direct_run`（notes 首行的创建标签，§37.1 那条老判据升 public）加「卡面除那句话外没有任何经人审的指令内容」（`plan` / `preset` / `definition_of_done` / `summary` 任一非空即退回模板），所以 §34bis 的清理卡永远走模板，`tests/test_proposals_triage.py` 的序判例一字不改就是豁免的证明。看板需要的东西改走 CLI 旁路 `--append-system-prompt`（1,606 字，dispatch 与 resume 同源同挂——system prompt 是每次调用给的），装的每一块都**复用模板同一个函数**，只有 `memory_blocks` 被刻意留在门外。

收割跟着改判：prompt 不再明令 `FINAL DRAFT:`，marker 缺席就不再是「没交付」的信号，于是 `harvest_delivery` 加 add-only 的 `whole_message`（默认关 = 逐字节不变），开关的唯一判定点是 `session.verbatim_whole_message` / `harvest_kwargs`，所有收割点共用。两处刻意的取舍写在 §34 追记里：逐字卡停在 blocked 提问会被当成交付提升（差的只是通知措辞，比「每次正常交付都被标成中断」轻），以及**实测睡眠打断过的会话不吃整条口径**——否则末尾那句 `API Error: … went to sleep` 会冒充成果、顶掉 §71.3 那次唯一的原地重试。

UI 侧把运行中列的四句里的「提案」去掉，`.help` 改说此刻真实的行为。`ui/parity/native-inventory.json` 不动：那四条是 `role: copy` / `help`（§66 只列不判），inventory 是冻结原生的提取而不是 web 的规格，这是 owner 决策驱动的有意分叉，也不进 `waivers.txt`。注意与 #447 / PR #459（退役提案列）有重叠——它作为副作用同样删了占位句里的「（跳过提案）」，但保留了 `.help` 与空态句里的「提案」字样；两边谁后合谁 rebase 一次。
