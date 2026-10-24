# CLAUDE.md — 入职必读（每个 AI session 的必经之门）

你（Claude 或任何 AI 助手）在一个**长寿命、多 session 接力开发**的 repo 里工作。
之前的 session 做过的决策你看不见，context window 也装不下全部历史——所以这个
repo 不依赖任何一个 session 的记性，依赖下面这套成文体系。**动手前先读这一页。**

## 这是什么软件

Zelin's AI Assistant **lite 2.0**：屏幕 + 音频录制（screenpipe，薄 macOS 壳 `shell/` 托管）→
cron 的 ingest 链（`ingest/`：export → cleanup → process）把录到的内容整理进 Obsidian vault →
会后自动出会议纪要（`act/recap.py` → `state/recap/`），由本机唯一的看板 server（`server/`，只听
127.0.0.1）供 web UI（`web/`）与壳读写。Python 侧 = stdlib + PyYAML。每一次 headless `claude -p`
都在 §78 的隔离常量下运行（自己的 `CLAUDE_CONFIG_DIR`、repo 树之外的 cwd、`ISOLATION_ARGV`），
应用不读不写 owner 的 `~/.claude`（宪法第 13 条）。1.x 的 Act 管线（卡片注册表、actd、雷达、执行器、
看板投影 / inbox、自我改进通道、遥测）在 v2.0.0 退役，全文在 git tag / branch `full`。数据流契约锁定在
`docs/CONTRACT.md`——那是本 repo 的法典。

## 三份必读文件（按顺序）

1. **`docs/CONTRACT.md` 的 §0 设计宪法** — 13 条不变原则（第 12 条是 v2.0.0 的 tombstone）。任何功能与
   宪法冲突：要么改功能，要么在 PR 里显式修宪。没有第三条路。
2. **`docs/CONTRACT.md` 相关章节** — 你要动的每个模块在这里都有一节法条
   （§1-§79，编号永不复用；退役的节是一行 tombstone，全文见 tag `full`）。改行为 = 同步修法，同一个 PR 里。
3. **`CONTRIBUTING.md`** — 四道本地门（`python3 -m compileall` / unittest 全套 / `shell/build.sh` /
   `shell/tests/run.sh`），PR 前自己跑过。

## 当前进行中的大轮次(2026-10 起)

**lite 2.0：退役 Act 管线，只留录制 / ingest / 会议纪要（owner 2026-10-23 决定）。** 切片在 `dev` 上暂存、
各自过完整 CI，最后一条 dev→main PR 切出 v2.0.0。1.x 的 `docs/design/vnext2-plan.md` 是历史台账（全文
留在 tag `full`）；本轮的默认取舍与 owner 决定以 CONTRACT §0 修宪段、§78、§79 为准。

## 添加任何功能前，必答三问（答案写进 PR 描述）

1. 这个改动**触及 CONTRACT 的哪些 §**？（新行为 → 新增/修订哪节法条？）
2. 它与 **§0 宪法的哪几条**相关？有没有哪条被打破？（打破 = 先修宪）
3. 有没有**已存在的机制**做类似的事？（法典里搜一遍再造轮子——triage 闸门、
   fold、静默并入、重试台账、health 分类……大概率已经有你要的一半）

## 最容易踩的雷（历届 session 的血泪，按出现频率排）

- **纪要单写者**：`state/recap/recaps/` 与 `sessions.json` 只由 `act.recap` 写；server 只派生它、只写
  marks / 请求台账 / overrides（宪法第 1 条，§63.6）。
- **字段 add-only**：跨组件 JSON/YAML 字段只增不改不删不重编号（CONTRACT header）。
- **不可信文本进围栏**：外部内容进 LLM prompt 必过 `sanitize.fence_untrusted`。
- **LLM 输出不可信**：类型逐字段消毒（数字 title、bool deadline 都真实出现过）；
  解析失败不许崩 pass（宪法第 11 条）。
- **永不 bump 版本**（§56.1，2026-09-02 起）：版本真源 = main 上的 git tag，合并时
  `release-on-merge` 自动铸造；`act/__init__.py` 的回落行与 CHANGELOG 的版本标题都是
  **只读**的（iOS 的 `0.0.0-dev` 占位随 ios/ 在 v2.0.0 删除），CI「Version pins untouched」会拦。发版说明只写
  `[Unreleased]` 下。要看你这个 checkout 的版本：`python3 scripts/version_stamp.py`。
- **测试即判例**：1500+ 条测试钉着历史行为；改坏一条先想想它当初为什么在那。
  运行时依赖白名单 = stdlib + PyYAML，测试侧可加（hypothesis 在 CI 装）。
- **录到的东西只变成笔记和纪要**（宪法第 4 条）：永不成任务、永不自动外发；唯一出机的是模型调用。
- **不碰 owner 的 `~/.claude`**（宪法第 13 条，§78）：headless claude 只经 `act/llm.py` /
  `ingest/claude-env.sh` 的隔离常量运行；`tests/test_isolation_footprint.py` 的 grep 门会拦。

## 语言与风格

- 代码注释：中文说明 + 英文术语，密度对齐相邻代码（先读再写）。
- CONTRACT 中文正文是 canonical；commit message 用英文 conventional commits。
- 测试风格：unittest + 注入缝（runner/triager/extractor），绝不 spawn 真 `claude`。

## 防腐十条（结构审查 2026-08-30 制定）

1. **文件上限**：Python 单文件 ≤2,000 行、Swift ≤1,500 行、单函数 ≤300 行、单 class/struct ≤800 行——超线的 PR 必须附拆分计划或在 PR 描述里显式豁免一次。**机器执法（§58）**：`scripts/qa/hygiene.py`（阈值 truth = qa/gates.toml；存量豁免 truth = qa/hygiene_baseline.txt，shrink-only；范围 act/ server/ scripts/ + shell/*.swift）。
2. **import 方向**：`act/lib/` 只准 import stdlib+PyYAML+同层；entrypoint 互相不 import；跨模块引用 `_私名` = 当场升 public 或抽进共享模块。**机器执法（§58.3）**：`scripts/qa/depgraph.py`（存量豁免 truth = qa/deps_baseline.txt，shrink-only）。
3. **唯一 LLM 边界**：所有模型调用走 `act/llm.py` 的 `run(prompt, runner=None)` 参数注入；**永久禁止 module-global 注入缝**（`JUDGE_RUNNER` 事故成法）。
4. **数据不进包、日志必有帽**：Python 包目录内禁止运行时数据文件；任何新 append-only 文件出生当天就带 size-cap 或 retention（照 registry_writes.jsonl 的 1MB self-compaction）。
5. **文档指针纪律**：模块 docstring 必须写明管它的 CONTRACT §§；文档里出现具体数字（版本/计数/§ 范围）必须写成 "truth = <文件路径>" 或由脚本生成，禁止手写字面量。**机器执法（§58.3，docstring 半边）**：`scripts/qa/hygiene.py` 查 act/** server/** 的模块 docstring 含 `§<数字>`（__init__.py 豁免；存量欠账在 qa/hygiene_baseline.txt）。
6. **Tombstone 规则**：删功能 = 同一个 PR 里删代码 + 删/搬文档 + CONTRACT 留一行 tombstone（`§N（retired vX.Y，并入 §M）`）；§ 号永不复用、永不静默消失。
7. **测试位置**：一个 behavior 一个文件，文件名说行为不说日期；docstring 引 §；unit/behavior 层禁真 subprocess 和网络；真 IO 只许住 integration/ 且有单文件时间预算。
8. **目录同步只许 `rsync --delete` 或 git 驱动**；working tree 领先 VCS 不得超过一个 release——每次版本 bump 前先 commit。
9. **命名单源**：新组件先定 canonical slug（`radar_gmail` 式），模块名/DB row key/service label/log 名/health key 全部从 slug 逐字派生；同一 basename 禁止出现在两个目录层级；子系统按 object 命名（`cloud_relay`），不按动词。
10. **前端镜像纪律**：client model 字段逐字镜像 wire key，禁翻译层；lane 组成是 server 数据不是 client 代码；文案进 server-owned catalog，禁第二套双语机制。
