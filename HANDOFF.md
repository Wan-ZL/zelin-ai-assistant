# HANDOFF — 给接手这个项目的 Claude Code（或人类）

> **English orientation** — This is the handoff book the AI assistant that built this system wrote
> for its successor: the architecture map (§1), the reasoning behind every non-obvious design
> decision (§2), a pitfall list paid for in real debugging time (§3), and development conventions (§4).
> Read it together with [docs/CONTRACT.md](docs/CONTRACT.md) — data contract first, rationale second.
> Five decisions carry most of the weight: app/pipeline decoupling over `dashboard.json` (read) +
> `inbox/` (write); optimistic echo with backend confirmation; the `queued` intermediate state;
> `delivery_mode: chat|repo`; and "fix it in the projection layer, don't touch the state machine".
> The Chinese body is canonical — ask any LLM to translate the sections you need.

> 本文档是原开发环境的 AI 助手写给下一任的交接书：架构地图、每个"奇怪设计"背后的理由、
> 付过学费的坑、以及路线图。读完这份 + `docs/CONTRACT.md`，你就拥有原开发者 90% 的上下文。
> 本文档已脱敏（人名/私有系统均为泛化表述），可随 repo 公开。

## 0. 一句话说清这是什么

**Zelin's AI Assistant = 双 pipeline 的个人 AI 秘书**：
- **Ingest 管线**（`ingest/`）：screenpipe 持续录屏/录音 → 定时导出为 markdown → headless claude skill
  加工进 Obsidian vault（unprocessed → raw → wiki）。
- **Act 管线**（`act/`）：需求雷达（Obsidian/Slack/Gmail）扫出"别人要 Zelin 做的事" → LLM 扩写成
  带成本预估和验收标准的提案卡 → Mac 菜单栏 app 上一键批准 → `claude --bg` 自动执行 →
  产出进"待验收" → 用户验收/打回。目标：把"需求→次日可验收草稿"变成默认状态。

用户只做两件事：**批准** 和 **验收**。其余全自动。

## 1. 架构地图

```
┌─ Mac app（SwiftUI 菜单栏，mac/Sources/ 13+ 文件，无 SPM，swiftc 全模块编译）
│    读: state/dashboard.json（5s 轮询，只读）
│    写: state/inbox/<uuid>.json（用户操作指令）
│    永不直接碰 claude / registry / secrets —— 一切经 dashboard(读) + inbox(写)
│
├─ actd 守护（launchd 常驻，act/actd.py，10s 一个 pass）
│    pass = process_inbox → dispatch_approved → [write-early dashboard]
│           → reconcile_executing → process_raising(每轮最多展开1条,claude -p 420s)
│           → purge_trash → build_dashboard → write_dashboard(原子 .tmp+rename)
│
├─ 雷达（radar.py=Obsidian 30min cron 尾挂；radar_slack.py=launchd 3min；radar_gmail.py=5min）
│    LLM 判定 → registry.merge_or_new（重述合并 / 增量出改进卡 / 新条目）
│    置信分流：hard+有deadline → 直接待审批；其余 → 欠账（低成本停车场，可 raise 升级）
│
├─ registry（act/registry/*.yaml，任务唯一真源）状态机：
│    detected(欠账) → card_sent(待审批) → approved(排队) → executing → review(待验收)
│    → delivered ；任何点可 → trashed(回收站,60天) ；raising=扩写中
│
└─ 执行层 = 官方设施复用：claude --bg 派发（自动 worktree 隔离）+ claude agents --json 监控
     不自建任务运行时。自建的只有：雷达/注册表/审批回传/UI。
```

关键文件快查：
- `act/lib/dashboard.py` — registry→dashboard.json 投影（**UI 所有可见字段的唯一来源**）
- `act/executor.py` — 派发/打回/resume/quality-gate prompt/交付摘要收割
- `act/analyze.py` — 提案扩写 prompt（带只读工具白名单的 headless claude）
- `act/lib/quick_capture.py` — 快速捕获三选一（新卡/关联已有/忽略）
- `mac/Sources/Store.swift` — 前端状态 + 全部乐观回显机制
- `mac/Sources/Cards.swift` — CardSurface 基座 + 五种卡片
- `docs/CONTRACT.md` — dashboard.json/inbox 契约（**改字段必先改这里**）

## 2. 关键设计决策（为什么长这样）

1. **app 与管线彻底解耦**（dashboard.json 读 + inbox 写）：app 崩/重装不影响任务执行；
   管线可以无 UI 运行；两者只靠两个 JSON 文件握手。改任何一侧前先读 CONTRACT.md。
2. **乐观回显（PendingEcho）+ 后端确认**：点击瞬间本地灰卡出现在目标列（opacity 0.75+spinner），
   后端真身出现即原子替换；180s 超时兜底弹提示。**所有点击必须 ≤1 帧内有视觉反馈**——这是
   用户的硬要求。sticky-hide 记录"从哪列隐藏"，等真身离开源列才释放（防闪回）。
3. **queued 中间态**：批准后、派发成功前，任务以 `state:"queued"` 灰显在运行中列。这是后端
   单一事实源方案（v s. 前端 echo 持久化——会形成第二事实源和后端打架，被否）。
4. **actd write-early**：pass 内 inbox+dispatch 之后立即写一次 dashboard，防止同 pass 的 420s
   扩写把"批准已生效"压到 7 分钟后才可见。
5. **delivery_mode: chat|repo**：文书类任务（回复稿/周报/一次性分析）在会话结束总结里给
   `FINAL DRAFT:` 全文，不落盘不建分支；代码/长期文档才走"feature 分支交付"。含"用户说
   '定稿'才落盘"的常驻升级条款（rework 是 resume 原会话，条款一直在上下文里生效）。
   理由：曾有纯文书任务被 quality gate 逼着建了分支，用户只想要一段可粘贴的文字。
6. **拒绝 ≠ 已办完**：回收站条目不参与 merge_or_new 匹配 → 拒绝后同一需求会再次出卡；
   `done_external`（已办完，置 delivered）才能把后续重述压成静默合并。所以拒绝按钮弹二选。
7. **逆操作矩阵而非全局 ⌘Z**：批准 5 秒后 claude 会话已真实在跑，undo 语义不成立。
   每个操作配逆操作：回收站恢复 / 停止并退回待审批（abort_execution，session 归档重派发）/
   退回待验收（revert_review）/ attach 进会话改口。
8. **review-attach 回流**：待验收任务被用户 attach 后 agent 重新 working → dashboard **投影层**
   把它临时投回运行中列（state=review-active），registry 状态机不动；settle 时重新收割交付摘要。
   原则：**能在投影层解决的不动状态机**。
9. **交付摘要收割**：executor prompt 要求 agent 完工写总结；done→review 提升时从 transcript
   收割最后一条 assistant 消息（executor.harvest_delivery），FINAL DRAFT 段拆出全文供"复制成稿"。
10. **分级审批**：T0 自动/T1 一键/T2 需展开+文字确认；成本双阈值（<$5 不显示，>$50 升 T2）；
    对外发送/merge/删资源永不自动。执行 agent 的 quality gate：自检 + fresh-context 审 diff +
    draft 交付不 merge。
11. **manager 泛化**：所有"重点关注某人需求"的功能（manager_pack、[MANAGER-OWES] 账本、
    雷达关键词）从 config 的 `owner_name`/`sources.watch_people` 派生，不硬编码人名。
12. **本地脱敏**（act/lib/sanitize.py）：发往 claude 的 5 个出口统一 scrub（用户词表+内置密钥
    正则）。默认关（打码会改变 AI 输入质量），设置里可开。
13. **凭证**：`config/secrets/`（0700/0600）→ config 显式路径 → 传统位置三级 fallback；
    app 里粘贴保存；密钥永不进 git（.gitignore 有），代码里只有路径和正则。
14. **卡片排序是纯 UI 偏好**（UserDefaults，非后端 config）：newest（默认）/oldest/deadline，
    投影层排序，占位卡恒置顶。
15. **行为埋点 analytics v2**：串行队列+O_APPEND（并发 FileHandle.seekToEnd 会自踩产生损坏行）、
    每事件带 sid/版本、动作记结果闭环（ok/fail/耗时）、radar 早退必打 radar_skip(reason)——
    **"0 新卡"和"静默坏死"必须可区分**，这条是用真实事故换来的。

## 3. 血泪坑清单（每条都付过学费，别再交一遍）

- **TCC 权限绑签名指纹**：ad-hoc 签名每次构建 cdhash 变 → 重装即失效且 macOS 新版对静默拒过的
  app 不再弹窗（要手动 "+" 添加）。解法=稳定签名身份（自签证书本机有效；公开分发用 Developer ID，
  这就是所有正经软件升级不重授权的机制本身）。openssl 3 导 p12 要 `-legacy`。
- **GUI app 派生后台进程**：nohup 孤儿会被 RunningBoard 收割（死在写第一行日志前）——
  必须 `exec` 变身 + Swift 持 Process 引用，让引擎做 app 直接子进程。
- **pgrep 自匹配竞态**：`pgrep -f "foo.*bar"` 会匹配到并发的自己 → 模式写成字符类 `[b]ar`。
- **`claude -p` 与 `--allowedTools`**：后者是变长参数会吞 positional prompt → **prompt 必须紧跟 -p**。
- **cron 环境**：PATH 没有 `~/.local/bin`（claude 二进制找不到）、没有 GUI keychain。
  所有 cron/launchd 里跑的 claude 必须用绝对路径 + 显式注入 API key env。曾因此雷达静默
  失败数日而 analytics 显示"一切正常"——见决策 15。
- **resume 三定律**：① `--resume` 要完整 UUID 且必须在 transcript 真实 cwd 下跑（roster 显示的
  启动目录可能是错的）；② 没有 transcript 的 session id 永不 resume（会 mint 新 id 死循环）；
  ③ 连败 5 次自动放弃+通知。live agent 用 `claude attach <短id>`，done 才用 `--resume <UUID>`。
- **短 id vs 完整 UUID**：`claude agents --json` 两种都有，任何按 session join 的代码要双键索引。
- **NSAlert/输入框在菜单栏 app**：编辑快捷键(⌘C/V/A)靠主菜单 Edit 分发，没有就全死；
  transient popover 检测不到外点（app 从不 active）→ 全局鼠标监听；Return/Shift+Return 要
  IME-safe（hasMarkedText() 防线）；⌃⌥Space 和输入法冲突，默认热键用 ⌥Space。
- **麦克风**：Info.plist 缺 NSMicrophoneUsageDescription = 进程被掐死且不弹任何提示。
- **git 合并验证**：ff-only 失败时 "Aborting" 走 stderr，管道会骗人——**合并后必 `git log -1`
  验 HEAD 真的动了**。`grep -c` 数到 0 退出码是 1，会短路 `&&` 链。
- **多 agent 并行改代码**：一个文件只归一个 agent；跨文件接口先写冻结契约再动工；
  新组件放新文件（天然无冲突）。4000 行单文件先机械拆分再谈并行。
- **repo/目录改名**：`~/.claude/projects/<路径转写>/` 的会话档案不会跟着搬，旧会话 resume 断链，
  要手动把 jsonl+附属目录复制到新转写目录。
- **"虚构"示例是泄漏重灾区**：脱敏时标注 fictional 的 fixture 里曾藏着真实数据换皮。
  公开任何内容前跑独立的对抗审计，别信自查。

## 4. 开发约定

- **永远在 git worktree 里改代码，ff-merge 回 main**：main 工作区是守护进程的运行现场，
  改到一半的文件会被 actd/cron 执行（炸过）。
- **契约先行**：动 dashboard.json/inbox 字段 → 先改 docs/CONTRACT.md；Swift 解码全部
  `decodeIfPresent` 向后兼容，字段只增不改不删。
- **所有用户可见文案 `L("中文","English")` 双语**，语言可即时切换。
- **每个改动批次**：py_compile + `python3 -m unittest discover -s tests`（当前 79 个测试）+
  `bash mac/build.sh` 三关全绿再合并。app 装机 = `bash mac/build.sh --install`。
- **测试用 tempdir AIASSISTANT_HOME**，绝不碰真实 state/registry。
- commit 信息写清楚"为什么"，运行态 registry 文件不进代码 commit。

## 5. 当前状态快照（交接时点）

- 版本 v0.10.3。全链路实测可用：录屏→ingest→wiki、雷达→卡片、批准→执行→验收、
  快速捕获（popover + 看板列顶多行 composer + ⌥Space）、回收站、双语。
- 已知小债：① TaskRow 用 accent 颜色识别"已验收"列（宜改显式参数）；② queued 灰卡上的
  停止按钮可点（合语义但视觉待观察）；③ ingest 导出脚本硬编码 unprocessed 路径，
  未接 config 的 obsidian_unprocessed；④ 排序无 memo（当前量级无碍）。
- **Ingest 切换说明（暂不执行）**：原开发机的生产 crontab 仍指向 `~/Applications/*.sh` 和
  `~/.local/bin/process-screenpipe.sh`；本 repo 内是这些脚本的受版本控制副本。验证一段
  时间后，将 crontab 改指向本 repo 路径，原件归档。（新装机器不受影响——install.sh
  直接装 repo 路径的 cron 链。）
- 平台依赖：macOS + screenpipe（npx 起录制引擎）+ Obsidian vault + claude CLI +
  自备 Anthropic API key。首次跑见 docs/INSTALL.md 与 install.sh；公开导出的脱敏
  说明见 docs/SANITIZATION.md。

## 6. 路线图

已迁至 **[docs/ROADMAP.md](docs/ROADMAP.md)**（按 In progress / Next / Later 分组，单一来源，
避免双维护）。动手前先看 In progress 一节——若干条目已有工作在途，别重复开工。

## 7. 给接手者的第一小时建议

1. 读 `docs/CONTRACT.md`（数据契约）→ 本文档 §2/§3 → `README.md`。
2. `bash install.sh` → 设置里粘 API key → 看菜单栏 popover 有心跳（"数据生成于 X 秒前"）。
3. 改一行代码走一遍完整流程：worktree 改 → 三关验证 → merge → `build.sh --install`。
4. 想加功能先问三个问题：这个状态该住在 registry（真源）还是投影层（dashboard.py）还是
   纯 UI（Prefs）？点击后 1 帧内用户看到什么？失败时用户怎么区分"慢"和"坏"？
   ——这三问就是这个项目的全部设计哲学。
