# HANDOFF — 给接手这个项目的 Claude Code（或人类）

> **English orientation** — lite 2.0 records the screen and audio (screenpipe, hosted by the Dock
> shell), turns the recordings into Obsidian notes every 30 minutes (the cron ingest chain) and writes
> meeting recaps. Read [docs/CONTRACT.md](docs/CONTRACT.md) §0 first; the Chinese body is canonical.
> The 1.x Act pipeline (cards, radars, executor, menu-bar app) retired in v2.0.0; its handoff book is
> at git tag `full`.

## 0. 一句话

录屏录音（`shell/` 托管 screenpipe）→ cron 的 ingest 链（`ingest/`：export → cleanup → process）把内容整理进
Obsidian vault → `act/recap.py` 出会议纪要到 `state/recap/` → `server/`（只听 127.0.0.1）给 `web/` 与壳读写。

## 1. 地图

- **壳**（`shell/`，bundle id `com.zelin.ai-board`）：Dock app，托管或接管录制引擎（§61.8），连 server。
- **server**（`server/`，LaunchAgent `com.zelin.aiassistant.server`，§49/§54）：唯一的 HTTP 面；派生 `act.recap`、
  `scripts/upgrade.sh`；SSE `recaps.updated`。
- **ingest**（`ingest/`，crontab 一行，§18）：`claude-env.sh` 提供 §78 的隔离常量，`process-screenpipe.sh` 在
  repo 树外的 cwd 里跑 headless claude。
- **纪要**（`act/recap.py`，§63）：确定性判会议 session，单写者。
- **生命周期**：`install.sh`（装 + 1.x 迁移）、`uninstall.sh`（逐项删）、`scripts/upgrade.sh`（§79）、
  `scripts/bootstrap.sh`（新机一条命令，§69）。常驻足迹是 §78 的封闭清单。

## 2. 付过学费的坑

- **TCC 按二进制记账**：launchd / cron 起的进程不继承终端的授权。daemon 解释器、稳定的 claude 副本
  （`~/Library/Application Support/ZelinAIAssistant/bin/claude`）、`/usr/sbin/cron` 各要一份完全磁盘访问（§55）。
- **壳的签名身份要稳定**（`shell/scripts/make-signing-cert.sh`）：ad-hoc 签名每次构建都换身份，录屏授权随之失效。
- **pgrep 自匹配**：模式写成字符类，`screenpipe.*[r]ecord`。
- **`claude -p` 的 prompt 紧跟 `-p`**：`--allowedTools` 是变长参数，会吞掉后面的位置参数。
- **`--bare` 只认 `ANTHROPIC_API_KEY`**：不读 OAuth / 钥匙串（§78）；没有 key = ingest 退出 1、纪要不出。
- **ff-only 失败的 "Aborting" 走 stderr**：合并后用 `git log -1` 确认 HEAD 真的动了。
- **"虚构"示例是泄漏重灾区**：标注 fictional 的 fixture 里曾混进真实数据。公开前跑独立的对抗审计。

## 3. 第一小时

读 `CLAUDE.md` → CONTRACT §0 与你要动的那一节 → `CONTRIBUTING.md` 的本地门。验证安装改动时用临时 `$HOME`
（在一份 repo 拷贝里跑 `HOME=$(mktemp -d) bash install.sh --non-interactive --no-launchd`），不要在 owner 的 checkout 上直接跑 `install.sh`。
