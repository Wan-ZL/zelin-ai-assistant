# Zelin's AI Assistant

[English](README.md) | **简体中文**

[![CI](https://github.com/Wan-ZL/zelin-ai-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Wan-ZL/zelin-ai-assistant/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Wan-ZL/zelin-ai-assistant)](https://github.com/Wan-ZL/zelin-ai-assistant/releases/latest)
[![License: FSL-1.1-MIT](https://img.shields.io/badge/license-FSL--1.1--MIT-blue)](LICENSE.md)
[![Platform: macOS 14+](https://img.shields.io/badge/platform-macOS%2014%2B-lightgrey)](docs/INSTALL.md)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](docs/INSTALL.md)

macOS 上的个人 AI 秘书：盯着工作从哪里来（会议记录、Slack、Gmail），把"别人要你做的事"变成菜单栏里的审批卡片，批准后由后台 Claude agent 自动执行。你只做两件事——**批准** 和 **验收**，其余全自动。

<p align="center">
  <a href="docs/assets/promo-zh.mp4"><img src="docs/assets/promo-teaser-zh.gif" alt="60 秒产品导览：AI 从录音里找出相关碎片，汇聚成一张审批卡" width="760"></a>
  <br><sub>▶️ <a href="docs/assets/promo-zh.mp4">观看 60 秒导览（含配乐）</a> · <a href="docs/assets/promo-en.mp4">English</a> —— 画面全部为虚构 demo 数据，成片可由 <a href="promo/">promo/</a> 一条命令重录</sub>
  <br><sub>配乐：《Voxel Revolution》— Kevin MacLeod（<a href="https://incompetech.com">incompetech.com</a>），<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a></sub>
</p>

![任务台看板](docs/assets/kanban.png)

<table><tr>
<td width="38%" valign="top"><img src="docs/assets/popover.png" alt="菜单栏 popover：提案卡片"></td>
<td valign="top"><img src="docs/assets/flow.gif" alt="审批 → 排队 → 执行 → 待验收 → 已验收 全流程"><br><sub>一张卡片的一生：批准 → 排队 → 执行 → 待验收 → 已验收（<a href="docs/assets/demo.mp4">mp4 版</a>；图中数据全部为虚构 demo 数据，由 <code>scripts/demo_seed.py</code> 生成）</sub></td>
</tr></table>

## 工作原理

- **感知**——[screenpipe](https://github.com/mediar-ai/screenpipe) 本地录屏+录音；定时任务增量导出，headless Claude 加工进 Obsidian wiki（`ingest/`）。
- **发现**——三路需求雷达（Obsidian 笔记 / Slack / Gmail）扫出"别人要你做的事"，写入 YAML 需求注册表，跨源合并去重（`act/`）。
- **审批**——每条需求扩写成提案卡（大白话摘要、成本预估、验收标准），出现在 SwiftUI 菜单栏 app 里。一键 ✅ 批准 / ❌ 拒绝 / 💬 评论。
- **执行**——批准的卡片以 `claude --bg` 派发到独立 git worktree,由常驻守护进程（`actd`）监控,自动 resume + 质量门（自检、fresh-context 审 diff、只交 draft PR）。
- **交付**——完工进"待验收"列：文书任务给可直接粘贴的成稿,代码任务给 draft PR。验收通过归档,不满意带评论打回。

app 与管线彻底解耦：app 只读 `state/dashboard.json`、只写 `state/inbox/`,两个 JSON 文件的契约见 [docs/CONTRACT.md](docs/CONTRACT.md)。

### 架构图

```mermaid
flowchart TB
    subgraph MAC["你的 Mac —— 框内一切留在本机"]
        direction TB

        subgraph INGEST["Ingest 管线(ingest/,cron)"]
            SP["screenpipe 引擎<br/>录屏 + 录音"] --> EXP["增量导出<br/>(markdown)"]
            EXP --> DISTILL["headless claude<br/>ingest skill 加工"]
            DISTILL --> VAULT[("Obsidian vault<br/>unprocessed → raw → wiki")]
        end

        subgraph ACTP["Act 管线(act/)"]
            RADARS["三路雷达<br/>Obsidian · Slack · Gmail"]
            REG[("注册表 —— YAML 唯一真源<br/>detected → card_sent → approved →<br/>executing → review → delivered<br/>(任意状态 → trashed)")]
            ACTD["actd 守护(launchd,10 s 一轮)<br/>inbox → 派发 → reconcile → dashboard"]
            AGENTS["claude --bg agents<br/>独立 git worktree + 质量门<br/>交付:draft PR 或 FINAL DRAFT"]
            RADARS -->|"merge_or_new(去重)"| REG
            ACTD <-->|"状态跃迁"| REG
            ACTD -->|"派发已批准任务"| AGENTS
        end

        VAULT --> RADARS

        DASH["state/dashboard.json<br/>(投影,原子写)"]
        INBOX["state/inbox/*.json<br/>(一个用户操作一个文件)"]

        subgraph APP["Mac app(SwiftUI 菜单栏)"]
            UI["审批卡片 · 看板 ·<br/>快速捕获"]
        end

        ACTD --> DASH
        DASH -->|"只读,5 s 轮询"| UI
        UI -->|"批准 · 拒绝 · 评论 · 捕获"| INBOX
        INBOX --> ACTD
    end

    subgraph EXT["外部服务"]
        ANTH["Anthropic API"]
        GH["GitHub"]
        SRC["Slack · Gmail"]
    end

    DISTILL -.->|"屏幕/音频摘录进 prompt"| ANTH
    RADARS -.->|"笔记与消息正文进 prompt"| ANTH
    AGENTS -.->|"任务 prompt + repo 上下文"| ANTH
    AGENTS -.->|"经 gh 提 draft PR(repo 模式)"| GH
    SRC -.->|"消息 / 未读邮件 / self-DM 快速捕获"| RADARS
```

实线 = 本地文件/进程流;虚线 = 仅有的网络出境点(完整清单及每一条对应的控制开关见 [docs/PRIVACY.md](docs/PRIVACY.md))。app 永不联网、永不碰注册表/密钥/`claude`——它的全部世界就是一个可读文件加一个可写目录。

## 快速开始

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant
cd ~/Projects/zelin-ai-assistant
cp config.example.yaml config.yaml   # 编辑:Obsidian vault 路径、watch_people、Slack ID
bash install.sh                      # 依赖检查 → 构建装 app → launchd agents + crontab
```

首次启动 app 会弹出双语**权限体检页**:唯一的屏幕记录 consent(默认**仅屏幕**,音频需在「设置 → 录制」单独打开)、屏幕录制/通知/完全磁盘访问 的实时授权清单,以及一行匿名使用统计披露(详情与关闭在设置);之后随时可从 菜单 → 权限体检 重开。然后打开菜单栏 app 的设置窗口,粘贴 Anthropic API key(cron/launchd 下的 headless `claude` 读不了 Keychain OAuth,所以 key 以 `0600` 文件存在 `config/secrets/` 下)。

- 完整安装教程(逐步 checkpoint、TCC 授权准确路径、"第一张卡 5 分钟"练习):**[docs/INSTALL.md](docs/INSTALL.md)**(也覆盖 `.pkg` 安装包路线)。
- 还没有 API key?用完全虚构的数据预览整套 UI:`python3 scripts/demo_seed.py /tmp/assistant-demo`,见 [docs/DEMO.md](docs/DEMO.md)。

## 环境要求

| 组件 | 版本 | 用途 |
|---|---|---|
| macOS | 14+ | 菜单栏 app、launchd/cron 定时、TCC 权限模型 |
| Xcode / Swift toolchain | 6.x | 从源码构建 app |
| [Claude Code CLI](https://claude.com/claude-code) + Anthropic API key | 最新版 | 雷达、提案扩写、执行全靠 headless `claude` |
| Python | 3.9+ 与 PyYAML | `actd` 守护、雷达、digest |
| Node.js | LTS(`npx`) | 录制引擎经 `npx screenpipe` 自动运行,无需单独安装 |
| Obsidian(可选) | — | 雷达扫描源 + wiki 落点 |
| `gh` CLI(可选) | — | draft-PR 交付 |

### 平台支持

| 操作系统 | 状态 |
|---|---|
| macOS 14+ | **完整产品**——菜单栏 app、launchd/cron 定时、屏幕录制 ingest、Slack/Gmail 雷达 |
| Linux | **核心已可移植,欢迎认领移植**——headless 流水线(雷达、`actd`、执行器)是纯 Python,完整测试套件在 ubuntu CI 上全绿;还缺 systemd 服务接线、ingest 链和 UI。移植地图与第一个里程碑:[docs/PORTING.md](docs/PORTING.md) |
| Windows | **核心已可移植,欢迎认领移植**——与 Linux 相同(暂无 CI 覆盖);Task Scheduler 对照表见 [docs/PORTING.md](docs/PORTING.md) |

Slack 雷达(含 self-DM 快速捕获)是跨平台的捕获入口;审批统一在 Mac App 里做。

## 功能特性

- **带去重的需求雷达**——纯重述合并进已有卡片不刷屏;含增量出"改进卡"链接父条目;低置信度进备选(backlog)停车场,可 raise 升级。
- **分级审批**——T0 自动 / T1 一键 / T2 文字确认;对外发消息、merge、删资源永不自动。成本 >$5 显示,>$50 升 T2。
- **质量门**——可运行检查 + 只读测试 + fresh-context 审 diff + 风险分级 + 可回滚的 draft PR 交付。
- **两种交付方式**——代码走 `repo`(feature 分支 / draft PR);文书走 `chat`(可直接粘贴的 `FINAL DRAFT`),一段回复稿不会被逼着建分支。
- **快速捕获**——点菜单栏图标(主窗口内 ⌘L)随手记一句;LLM 对照注册表三选一:新卡 / 关联已有 / 忽略。<!-- screenshot slot: docs/assets/t2-card.png -->
- **即时反馈的 UI**——所有点击 ≤1 帧内有视觉反馈(乐观回显),看板主窗口,回收站配逆操作而非假 undo,双语界面(English / 中文)。<!-- screenshot slot: docs/assets/review-final-draft.png -->
- **Slack self-DM 手机端快速捕获**——在手机上给自己发一句话(或拍白板/屏幕/纸条的图片/视频),系统对照注册表三选一建卡,和桌面快速捕获同一条三选一门(见 [docs/SLACK_SETUP.md](docs/SLACK_SETUP.md))。iOS app 上线前这是手机端的捕获方式;**审批统一在 Mac App 里做**(iMessage 通道与 Slack 手机审批已于 v0.21 移除)。
- **内容本地优先**——注册表、dashboard、所有采集内容全部留在本机;默认只上传匿名使用事件(见下方「匿名使用统计」)。

## 匿名使用统计(Telemetry)

> **匿名使用统计默认开启**(像 VS Code 一样),用于驱动产品改进。发送的内容:事件元数据(事件名、时间戳、随机设备号、版本号)以及**默认包含你输入进本 App 的文本**——快速捕获、提问、打回反馈、搜索词,每条截断 500 字符(`telemetry.capture_input`,默认开)。**任何设置下都不发送**:AI 的回答、屏幕录制内容、邮件或 Slack/iMessage 消息正文、文件内容、密钥。关闭在 设置 →「产品改进计划」:单关「上传我输入的文本」只停文本(`telemetry.capture_input: false`),关总开关全部停止(`telemetry.enabled: false`)。Fork 用户注意:不改 `telemetry.supabase_url` 时数据会传给本项目维护者——把 URL 置空(`""`)即彻底禁用上传。字段表与细节见 [docs/TELEMETRY.md](docs/TELEMETRY.md)。

## 隐私与安全

这个工具会录屏、可读你的 Slack/Gmail、还跑无人值守 agent——什么数据、何时、经哪些开关控制着离开你的机器,见 [docs/PRIVACY.md](docs/PRIVACY.md)。sensitive app 在录制引擎层就被排除采集(config.yaml `recording.ignored_apps`,默认含密码管理器、Keychain Access 与无痕窗口;银行 app 请自行加进清单);安全漏洞请走私密渠道上报,见 [SECURITY.md](SECURITY.md)。

## 运行状态

- [x] v0:审批卡片 → ✅ → 执行任务闭环
- [x] v1:三路雷达 cron/launchd 接入,审批回传,周一 digest
- [x] v2:SwiftUI 菜单栏 app(popover + 看板主窗口 + 快速捕获 + 回收站 + 双语)
- [ ] v3:iOS 遥控器(`ios/` 为占位)

进行中与接下来的方向,见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 参与贡献

不需要完整技术栈就能上手:测试套件只要 Python + PyYAML,一秒内跑完;`scripts/demo_seed.py` 用完全虚构的数据驱动整套 UI——无需 API key、screenpipe 或 Obsidian。入口见 [CONTRIBUTING.md](CONTRIBUTING.md);[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 适用于所有社区空间。

提问 → [Discussions(Q&A)](https://github.com/Wan-ZL/zelin-ai-assistant/discussions) · bug → [issue 表单](https://github.com/Wan-ZL/zelin-ai-assistant/issues/new/choose)。

## License

本项目以 [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md) 发布:

- **允许**:使用、fork、修改、分发,包括非竞争性的商业用途(公司内部使用等)。
- **禁止**:用本软件做与作者竞争的商业产品或服务。
- **未来开源**:每个版本发布满 2 年后自动转为 MIT License。
- **贡献**:欢迎 issue、建议和 PR,见 [CONTRIBUTING.md](CONTRIBUTING.md)。(GitHub 的 license 检测器不认识 FSL,只会显示 "Other";以上方 badge 为准。)

更多问题——公司内使用、fork、什么算竞争用途、各版本转 MIT 的具体日期——见大白话问答 [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 唯一权威安装指南:前置条件、逐步 checkpoint、TCC 路径、第一张卡 5 分钟 |
| [HANDOFF.md](HANDOFF.md) | **由构建这套系统的 AI 助手亲笔写下的交接书**:架构地图、每个"奇怪设计"背后的理由、付过学费的坑清单 |
| [docs/CONTRACT.md](docs/CONTRACT.md) | `dashboard.json` / inbox 数据契约——改字段必先改这里 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 按症状组织的已知故障修复手册 |
| [docs/DEMO.md](docs/DEMO.md) | demo 模式(虚构数据、零 key)与录屏指南 |
| [docs/PRIVACY.md](docs/PRIVACY.md) / [SECURITY.md](SECURITY.md) | 数据出境清单 / 漏洞上报 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 路线图:进行中 / 下一步 / 以后 |
| [docs/PORTING.md](docs/PORTING.md) | Windows/Linux 移植地图:今天哪些已可移植、OS seam、服务管理器对照表、第一个里程碑 |
| [CHANGELOG.md](CHANGELOG.md) | 人类可读的版本历史 |
| [CONTRIBUTING.md](CONTRIBUTING.md) / [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | 贡献指南 / 社区行为准则 |
| [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md) | FSL-1.1-MIT 实务问答:公司内能不能用、什么算竞争用途、满 2 年转 MIT |
| [docs/SLACK_SETUP.md](docs/SLACK_SETUP.md) / [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) | 可选的 Slack / Gmail 接入 |
| [docs/SANITIZATION.md](docs/SANITIZATION.md) | 出处说明:这份公开导出相对私有仓库做了哪些脱敏 |

私有版设计文档(`docs/design/`)含真实使用数据,公开导出中已移除,仅留说明。

### 目录结构

```
ingest/            # screenpipe→Obsidian 链路(导出/加工/清理脚本 + /unprocessed-ingest skill)
act/
  actd.py          # 守护进程:inbox → 派发 → reconcile → dashboard
  executor.py      # claude --bg 派发 + resume/rework + 质量门 + 交付收割
  radar*.py        # 三路需求雷达(Obsidian / Slack / Gmail)
  analyze.py       # 欠账 → 可审批提案的 LLM 扩写
  digest.py        # 周一 digest + self-improvement 建议卡
  lib/             # config / registry(状态机) / dashboard 投影 / notify / secrets / …
  registry/        # 需求注册表(YAML,一条需求一个文件,运行时生成)
  launchd/         # actd + 雷达 plists
mac/               # SwiftUI 菜单栏 app(读 dashboard.json,写 inbox,永不碰密钥)
ios/               # v3: 纯遥控器(占位)
```

## 贡献者

感谢每一位贡献者——每个 issue、建议和 PR 都让这个项目更好：

[![Contributors](https://contrib.rocks/image?repo=Wan-ZL/zelin-ai-assistant)](https://github.com/Wan-ZL/zelin-ai-assistant/graphs/contributors)

想加入他们？从一个
[good first issue](https://github.com/Wan-ZL/zelin-ai-assistant/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
开始，并阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

Copyright (c) 2026 Zelin Wan (https://github.com/Wan-ZL)
