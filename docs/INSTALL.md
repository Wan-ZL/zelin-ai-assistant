# INSTALL — 安装指南

两条路线：**.pkg 安装包**（新机器推荐，双击装完 app + pipeline）或**从源码**（开发者路线）。

## 路线 A：.pkg 安装包

1. 从 [GitHub Releases](https://github.com/Wan-ZL/zelin-ai-assistant/releases) 下载 `ZelinAIAssistant-<tag>.pkg`。
2. **未签名**（无 Developer ID），Gatekeeper 会拦：右键 → 打开；若仍被拒，去
   系统设置 → 隐私与安全性 → 底部点 "仍要打开"（macOS Sequoia 起只有这条路）。
3. 装了什么、装到哪：
   - `/Applications/Zelin's AI Assistant.app` — 菜单栏 app。
   - `/Library/Application Support/ZelinAIAssistant/pipeline/` — pipeline 母本（root 所有，随版本更新）。
   - postinstall 自动把母本 rsync 到 `~/Projects/zelin-ai-assistant/`（**不覆盖**你已有的
     `config.yaml`、`config/secrets/`、`state/`），再跑 `install.sh --pkg-postinstall`：
     config 模板 copy-if-absent、state 目录、ingest crontab 链（CONTRACT §18）。
   - **不装** launchd agents（actd/radar 需要你先配好 config.yaml）——配置完成后跑一次
     `bash ~/Projects/zelin-ai-assistant/install.sh` 补齐。

## 路线 B：从源码

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant ~/Projects/zelin-ai-assistant
cd ~/Projects/zelin-ai-assistant && bash install.sh
```

`install.sh` 做全套：依赖检查（claude / swiftc / python3+PyYAML）→ config 模板 →
state 目录 → 构建并安装 Mac app → launchd agents → crontab。幂等，可反复跑。

## 装完之后（两条路线都要，手动）

1. **依赖自备**：[Claude Code CLI](https://claude.com/claude-code)（必需）、
   [Node.js](https://nodejs.org) ≥ LTS（录屏 ingest 用——screenpipe 引擎经 npx
   自动运行，无需单独安装）、Obsidian（可选，雷达读 vault）。
2. **Anthropic API key**：打开 app 的设置窗口粘贴保存（写入
   `config/secrets/anthropic-api-key.txt`，见 CONTRACT §19）。headless claude 在
   cron/launchd 下读不了 Keychain OAuth，必须有文件形式的 key。
3. **TCC 权限**：系统设置 → 隐私与安全性 → 屏幕录制，给 app 打开开关（录屏 ingest 用）。
4. 编辑 `config.yaml`（watched people、Slack IDs、source 路径）；可选 Slack / Gmail
   接入见 `docs/SLACK_SETUP.md` / `docs/GMAIL_SETUP.md`。
