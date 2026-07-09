# Zelin's AI Assistant

个人 AI 助理套件：**感知（Ingest）+ 行动（Act）** 两条 pipeline，一个 repo。

- **Ingest**（已运转，本 repo 收编版本控制）：Screenpipe 屏幕+音频 → 增量导出 → headless Claude 全自动加工 → Obsidian LLM-wiki。
- **Act**（新建）：需求雷达扫描会议记录/Slack/Confluence → 需求注册表（跨源合并去重）→ 审批卡片（电脑/手机一键 ✅/❌/💬）→ `claude --bg` 后台执行（代码/benchmark/实验/训练）→ 质量门 → 交付闭环。

设计文档：`docs/design/`（私有版设计文档含真实使用数据，公开导出中已移除，仅留说明）。

## 目录

```
ingest/            # screenpipe→Obsidian 链路（导出/加工/清理脚本 + /unprocessed-ingest skill）
act/
  actd.py          # 守护进程：inbox → 派发 → reconcile → dashboard
  executor.py      # claude --bg 派发 + resume/rework + 质量门 + 交付收割
  radar*.py        # 三路需求雷达（Obsidian / Slack / Gmail）
  analyze.py       # 欠账 → 可审批提案的 LLM 扩写
  digest.py        # 周一 digest + self-improvement 建议卡
  lib/             # config / registry(状态机) / dashboard 投影 / notify / secrets / …
  registry/        # 需求注册表（YAML，一条需求一个文件，运行时生成）
  launchd/         # actd + 雷达 plists
mac/               # SwiftUI 菜单栏 app（读 dashboard.json，写 inbox，永不碰密钥）
ios/               # v3: 纯遥控器（占位）
docs/CONTRACT.md   # dashboard.json / inbox 数据契约（改字段必先改这里）
HANDOFF.md         # 交接书：架构地图 + 设计决策 + 血泪坑清单
```

## 关键决策（2026-07-06 确认）

- 分级审批：T0 自动 / T1 一键 / T2 文字确认 / 永不自动（对外发消息、merge、删资源）
- 成本双阈值：<$5 卡片不显示成本；$5–$50 显示一行；>$50 升 T2
- 训练按成本分级，但每个 ckpt 强制 system card（训前设计 + 训后结果）
- 注册表规则：纯重述合并不发卡；含增量发"改进卡"链接父条目
- 执行层复用官方设施：`claude --bg` 派发（自动 worktree 隔离）+ `claude agents --json` 状态监控
- 质量门 = Anthropic 五层的 solo 版：可运行检查 + 只读测试 + fresh-context 审 diff + 风险分级 + draft PR 可回滚
- Jira 集成默认关；注册表是唯一台账

## 运行状态

当前版本 v0.10.3，全链路实测可用（详见 `HANDOFF.md` §5）。

- [x] 设计确认（早期设计文档，公开导出未含）
- [x] v0：审批卡片 → ✅ → 执行任务闭环
- [x] v1：三路雷达 cron/launchd 接入，审批回传，周一 digest
- [x] v2：SwiftUI 菜单栏 app（popover + 看板主窗口 + 快速捕获 + 回收站 + 双语）
- [ ] v3：iOS 遥控器

首次安装见 `PUBLISHING.md` 与 `install.sh`。

## Ingest 切换说明（暂不执行）

现有生产 crontab 仍指向 `~/Applications/*.sh` 和 `~/.local/bin/process-screenpipe.sh`。本 repo 中为受版本控制的副本。验证一段时间后，将 crontab 改指向本 repo 路径，原件归档。

## 地雷（新组件开发必读）

1. cron 的 daemon session 读不了 Keychain OAuth → headless Claude 用 `ANTHROPIC_API_KEY`（`config/secrets/anthropic-api-key.txt`，见 `docs/CONTRACT.md` §19）。
2. launchd 进程被 TCC 挡在 ~/Documents 外 → 定时任务走 crontab，launchd 只做不碰 Documents 的活。
3. Slack/Atlassian MCP 的 OAuth 在 headless 下未验证；兜底 = Atlassian API token（写入 `config/secrets/`，见 `docs/CONTRACT.md` §19）。
4. 执行器必须注入 auto-memory 的 program map 与约束（例如：eval 走统一 CLI、数据放固定目录、云端资源命名规则等）。

## 已知坑：重装 app 后屏幕录制授权失效（ad-hoc 签名）

`mac/build.sh` 用 ad-hoc 签名（无开发者证书），TCC 把授权绑定到签名指纹上——
**每次重新构建安装后，"屏幕录制"授权会静默失效**（系统设置里开关看着还开着，
但 ScreenCaptureKit 枚举 0 台显示器，引擎启动即退）。symptom: engine.log 里
`permission monitor screen=true` 却 `no monitors available`。

修复：`tccutil reset ScreenCapture com.zelin.ai-engineer`，重启 app 让它重新
请求，然后在 系统设置 → 隐私与安全性 → 屏幕录制 重新打开开关。日常使用不受
影响；只在 app 更新后需要重做一次。

## License

本项目以 [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md) 发布：

- **允许**：使用、fork、修改、分发，包括非竞争性的商业用途（公司内部使用等）。
- **禁止**：用本软件做与作者竞争的商业产品或服务。
- **未来开源**：每个版本发布满 2 年后自动转为 MIT License。
- **贡献**：欢迎 issue、建议和 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

Copyright (c) 2026 Zelin Wan (https://github.com/Wan-ZL)
