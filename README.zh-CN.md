# Zelin's AI Assistant

[English](README.md) | **简体中文**

[![CI](https://github.com/Wan-ZL/zelin-ai-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Wan-ZL/zelin-ai-assistant/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Wan-ZL/zelin-ai-assistant)](https://github.com/Wan-ZL/zelin-ai-assistant/releases/latest)
[![License: FSL-1.1-MIT](https://img.shields.io/badge/license-FSL--1.1--MIT-blue)](LICENSE.md)
[![Platform: macOS 14+](https://img.shields.io/badge/platform-macOS%2014%2B-lightgrey)](docs/INSTALL.md)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](docs/INSTALL.md)

一个小的 macOS 助理：在本机录屏录音，把录到的内容整理成 Obsidian 笔记，每场会议结束后写一份简短的会议纪要。这是本项目的 lite 2.0 版本，只有下面四部分。

- **录制**：[screenpipe](https://github.com/mediar-ai/screenpipe) 引擎在你的 Mac 上采集屏幕文字和音频，由 Dock app 托管。
- **Obsidian 导入**：每 30 分钟，一条 cron 把新采集的内容导出，再由一次 headless Claude 运行整理进你的 Obsidian vault（`ingest/`）。
- **会议纪要**：会议结束后大约 5 到 35 分钟，`act/recap.py` 写出纪要：五行带标签的短版（定了什么、谁负责、截止、较上次变化、待定），或者分节的长版。你自己复制粘贴，它从不替你发送。
- **一个小的 Dock app**：本地 web 界面（`web/`，由 `server/` 里的标准库 server 提供）套在一个很薄的 Dock 壳（`shell/`）里：会议纪要、录制与导入、设置、关于。

版本以 `main` 上的 git tag 为准；`python3 scripts/version_stamp.py` 打印你手上这份 checkout 的版本。

![会议纪要：左边是列表，右边是一份纪要和复制、标记已发送、忽略、重新生成按钮](docs/images/recaps-zh-light.png)

<p align="center"><sub>本页每张截图都是 web 界面的真实渲染，数据来自 <code>scripts/demo_seed.py</code> 的虚构会议纪要，由 <code>scripts/qa/readme_shots.mjs</code> 拍摄。图里没有任何真实的人、会议或仓库。</sub></p>

## 安装

只支持 macOS 14+。需要 Xcode Command Line Tools、[Claude Code CLI](https://claude.com/claude-code)、Node.js LTS 和一个 Anthropic API key。

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant
cd ~/Projects/zelin-ai-assistant
bash install.sh
```

`install.sh` 在缺 `config.yaml` 时从 `config.example.yaml` 复制一份，构建 web 界面和 Dock app，为本地 server 装一个 LaunchAgent，为导入链写一条 cron，最后跑一遍体检。空白 Mac 上也可以用一条命令完成同样的步骤：

```bash
curl -fsSL https://raw.githubusercontent.com/Wan-ZL/zelin-ai-assistant/main/scripts/bootstrap.sh | bash
```

剩下要你做的：把 Anthropic API key 放进 `config/secrets/anthropic-api-key.txt`（或在 设置 → 凭证 里粘贴），在 app 里打开录制，按体检的提示给需要的程序开完全磁盘访问。`bash install.sh --check` 列出还缺什么。每一步和它的预期结果见 [docs/INSTALL.md](docs/INSTALL.md)，按症状找修法见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 升级

- **在 app 里**：关于页每天最多向 GitHub 检查一次新版本。有新版时点「立即升级」。
- **在终端里**：`bash scripts/upgrade.sh`，效果相同。

两者都只把 checkout 快进到带 tag 的 `main`，再重跑一遍 `install.sh`。工作树有改动或本地分叉时，它们停下、什么都不改，并打印要你手动跑的命令。录制不会被打断，设置和会议纪要原样保留。同一大版本内的自动升级是设置页里的一个开关（默认关）；新的大版本永远手动升级。

卸载安装器加上的一切：`bash uninstall.sh`（先加 `--dry-run` 看计划）。

## 隐私

- **只在本机。** 录制、笔记和会议纪要都留在你的 Mac 上。server 只监听 127.0.0.1。
- **零遥测。** app 不上传使用统计，也不上传反馈。内容只以模型调用的形式发往 Anthropic API（用你的 key）；唯一的其它请求是每天一次向 GitHub 检查版本，可以关掉。
- **隔离的 Claude 运行。** 每一次 headless `claude -p` 都用自己的配置目录 `~/Library/Application Support/ZelinAIAssistant/claude-config/`、一个在仓库之外的空工作目录，并带 `--bare`。它不加载你的 Claude Code hooks、插件、MCP server 和 CLAUDE.md，不往你的 Claude Code 目录写会话记录，从不读写 `~/.claude`。
- **敏感 app 在引擎层排除采集**（`config.yaml` 的 `recording.ignored_apps`：默认含密码管理器、钥匙串访问和无痕浏览窗口）。

每一个出网通道在什么时候触发、带了什么、用哪个开关关掉：[docs/PRIVACY.md](docs/PRIVACY.md)。安全漏洞请经 [SECURITY.md](SECURITY.md) 私下报告。

## 界面

| | |
|---|---|
| ![English 的会议纪要](docs/images/recaps-en-light.png) | ![设置：一个可搜索的页面，按区折叠](docs/images/settings-en-light.png) |
| English 的会议纪要：每份纪要都带中英两种语言，顶栏一个开关切换整个界面 | 设置：一个可搜索的页面，按区折叠 |
| ![关于：版本、更新检查、升级和卸载](docs/images/about-en-light.png) | ![权限体检：屏幕录制、系统权限和完全磁盘访问](docs/images/permissions-en-light.png) |
| 关于：版本、更新检查、升级和卸载命令 | 权限体检：app 需要 macOS 给的权限，附确切路径 |

不安装也能看界面：跑 demo。它把虚构的会议纪要写进一个临时目录，从不碰真实的 `state/`。

```bash
bash scripts/dev-preview.sh
```

## 从 1.x 迁移

1.x 和 2.0 不能在同一台 Mac 上并存。1.x 在大版本边界停下，不会自己升级到 2.0，所以迁移是一次手动操作，步骤见 [docs/INSTALL.md](docs/INSTALL.md)；`install.sh` 会顺带删掉 1.x 的后台 agent 和 cron 行。

最后一个完整的 1.x 版本归档为 git tag 和 branch `full`。要回到它：

```bash
git fetch --tags && git checkout full && bash install.sh
```

```removed
2.0 删除的东西（全部还在 tag 和 branch full 的树里；最后一个完整的 1.x 版本：v1.0.117）
- Act 管线：Slack、Gmail、Obsidian、Claude 会话雷达，需求登记表，审批卡片与看板，actd 守护进程和它派发的 claude --bg agent，快速捕获，技能页，回收站
- 实时字幕、纪要转 Slack 草稿、往 agent 草稿里注入语气档案
- 遥测：匿名使用事件、Supabase 上传、建议上报通道
- 原生 Mac app（mac/）、iOS 审批端（ios/）、Linux 与 Windows 安装器、.pkg 安装器、Sparkle 更新与自动部署
```

## 环境要求

| 组件 | 版本 | 用途 |
|---|---|---|
| macOS | 14+ | 唯一支持的平台 |
| [Claude Code CLI](https://claude.com/claude-code) + Anthropic API key | 最新 | headless 的导入与纪要运行 |
| Python | 3.9+，带 PyYAML | server、纪要与体检（`/usr/bin/python3` 即可） |
| Node.js | LTS | 构建 web 界面；录制引擎经 `npx screenpipe` 运行 |
| Xcode Command Line Tools | — | git，以及构建 Dock app（`shell/`） |
| Obsidian *（推荐）* | — | 导入链写笔记的地方 |

## 参与贡献

Python 测试只需要 Python 和 PyYAML；`scripts/demo_seed.py` 用虚构数据驱动整个界面，不需要 API key、screenpipe 或 Obsidian。从 [CONTRIBUTING.md](CONTRIBUTING.md) 开始；[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 处处适用。发布说明：[CHANGELOG.md](CHANGELOG.md) 与 GitHub releases。接下来做什么：[docs/ROADMAP.md](docs/ROADMAP.md)。

提问 → [Discussions (Q&A)](https://github.com/Wan-ZL/zelin-ai-assistant/discussions) · 报 bug → [issue 表单](https://github.com/Wan-ZL/zelin-ai-assistant/issues/new/choose)。

## 许可证

以 [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md) 发布。白话版：

- **可以** 使用、fork、修改和再分发，包括在公司内部商用。
- **不可以** 出售与本软件竞争的产品或服务。
- **将来开源**：每个版本发布两年后自动转为 MIT 许可证。
- **欢迎贡献**，见 [CONTRIBUTING.md](CONTRIBUTING.md)。（GitHub 把许可证显示为 "Other"，因为它的检测器不认识 FSL；以上方徽章为准。）

更多问题见 [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 安装、升级、从 1.x 迁移与卸载，逐步说明 |
| [docs/PRIVACY.md](docs/PRIVACY.md) / [SECURITY.md](SECURITY.md) | 每个出网通道和它的开关 / 漏洞报告 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 按症状找修法 |
| [docs/CONTRACT.md](docs/CONTRACT.md) | 行为契约；改行为的 PR 同时改它 |
| [HANDOFF.md](HANDOFF.md) | 系统的简短地图和已经付过学费的坑 |
| [docs/DEMO.md](docs/DEMO.md) | demo 模式与截图怎么拍 |
| [docs/design/lite2-plan.md](docs/design/lite2-plan.md) | lite 2.0 的计划书与决策台账 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 进行中和接下来的事 |
| [CONTRIBUTING.md](CONTRIBUTING.md) / [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | 如何贡献 / 社区准则 |
| [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md) | FSL-1.1-MIT 的实际含义 |
| [docs/SANITIZATION.md](docs/SANITIZATION.md) | 这个公开版是怎样从私有仓库脱敏导出的 |

### 仓库结构

```
ingest/                    # screenpipe → Obsidian 链（export / cleanup / process + skill）
act/recap.py               # 会议纪要（每场会议一次隔离的 headless claude 调用）
act/doctor.py              # 体检 (install.sh --check)
act/lib/                   # 配置 / 纪要存储 / 通知 / 凭证 / 隔离常量
server/                    # 只听 127.0.0.1 的标准库 HTTP + SSE server：纪要、设置、关于、升级
web/                       # React 界面
shell/                     # Dock app：托管录制引擎、通知
scripts/                   # bootstrap、升级、demo 数据、CI 与质量门
```

## 贡献者

感谢每一位贡献者：

[![Contributors](https://contrib.rocks/image?repo=Wan-ZL/zelin-ai-assistant)](https://github.com/Wan-ZL/zelin-ai-assistant/graphs/contributors)

想加入？从 [good first issue](https://github.com/Wan-ZL/zelin-ai-assistant/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) 开始，并阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

Copyright (c) 2026 Zelin Wan (https://github.com/Wan-ZL)
