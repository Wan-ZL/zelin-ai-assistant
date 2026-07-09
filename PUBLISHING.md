# PUBLISHING — 关于这份公开导出

这是 **Zelin's AI Assistant** 私有仓库的**净化导出**（sanitized export），用于公开发布。

## 相对私有版改了什么

- **移除**：运行时状态（`state/`）、真实配置（`config.yaml`、`config/runtime.json`、`config/secrets/`）、真实注册表条目（`act/registry/R-*.yaml`，留了一个虚构示例 `R-000-example.yaml`）、构建产物（`mac/build/`）、含真实使用数据的早期设计文档（`docs/design/`，留了说明）。
- **通用化**：真实人名 → "manager"/通用称呼；真实项目名/频道名/Slack ID → 占位符（`<your-team-channel>`、`C01234ABCDE` 等）；绝对路径 `/Users/<user>/...` → `~`/`$HOME` 写法（launchd plist 里用 `/Users/YOURUSERNAME/` 占位，install.sh 装机时自动渲染成真实路径——plist 不展开 `~`）。
- **行为等价**：feature flag `manager_pack`（私有版叫别的名字）、`[MANAGER-OWES]` 标签、`~/Projects/your-workbench` 默认落点等重命名是全局一致的，逻辑未变；manager 提及识别改为从 `config.yaml` 的 `sources.watch_people` 首项派生。
- 文档里出现的 `~/Desktop/Keys/` 等默认凭证路径只是**本机约定示例**，推荐用 App 设置窗口把凭证写入 `config/secrets/`（见 `docs/CONTRACT.md` §19）。

## 发布到 GitHub

```bash
cd zelin-ai-assistant-public
git init -b main
git add -A
git commit -m "Initial public release"
gh repo create <your-name>/zelin-ai-assistant --public --source=. --push
```

发布前建议再跑一遍自检：

```bash
# 语法
python3 -m compileall act tests
# 测试（指向临时 HOME，避免碰真实 state）
AIASSISTANT_HOME="$(mktemp -d)" python3 -m unittest discover -s tests
# Mac app 构建
cd mac && bash build.sh
```

## 你需要自备的东西

- **Anthropic API key**（必需）：headless `claude -p` / `claude --bg` 在 cron/launchd 下读不了 Keychain OAuth，必须有文件形式的 key。推荐通过 App 设置窗口保存（写入 `config/secrets/anthropic-api-key.txt`）；旧路径 `~/.config/anthropic-key.txt` 兜底。
- **Claude Code CLI**（必需）：执行器与雷达的提取都靠它。
- **Slack user token**（可选）：见 `docs/SLACK_SETUP.md`；没有 token 时走 Slack MCP 只读兜底。
- **Gmail 应用专用密码**（可选）：见 `docs/GMAIL_SETUP.md`；公司 Workspace 大概率禁用，缺文件时静默待机。

## 平台依赖

- **macOS**：menu-bar app（SwiftUI）、launchd/crontab 定时、TCC 权限模型（详见 `README.md` 的"地雷"一节）、`osascript` 通知。
- **[screenpipe](https://github.com/mediar-ai/screenpipe)**（可选）：屏幕+音频捕获，Ingest 管线的源头。
- **Obsidian**（可选但推荐）：vault 是雷达的扫描源与 wiki 的落点，目录约定 `1 - unprocessed` / `2 - raw` / `3 - change-summary` / `4 - wiki`。
- **Python 3.9+ 与 PyYAML**：actd / radar / digest。
- **gh CLI**（可选）：draft-PR 交付。

## 首次运行

1. `cp config.example.yaml config.yaml`，把 Slack ID、watch_people、vault 路径换成你自己的。
2. `bash install.sh`（依赖检查 → 建 state/ → 构建装 app → launchd/crontab）。
3. 打开菜单栏 app 的设置窗口，粘贴 Anthropic key（及可选的 Slack/Gmail 凭证）。
