# SANITIZATION — 公开导出的脱敏说明

本仓库是 **Zelin's AI Assistant** 私有仓库的**净化导出**(sanitized export)。本文档只记录出处(provenance):公开版相对私有版改了什么。安装指南见 `docs/INSTALL.md`,发布前的三道验证 gate 见 `CONTRIBUTING.md`。

## 相对私有版改了什么

- **移除**:运行时状态(`state/`)、真实配置(`config.yaml`、`config/runtime.json`、`config/secrets/`)、真实注册表条目(`act/registry/R-*.yaml`,留了一个虚构示例 `R-000-example.yaml`)、构建产物(`mac/build/`)、含真实使用数据的早期设计文档(`docs/design/`,留了说明)。
- **通用化**:真实人名 → "manager"/通用称呼;真实项目名/频道名/Slack ID → 占位符(`<your-team-channel>`、`C01234ABCDE` 等);绝对路径 `/Users/<user>/...` → `~`/`$HOME` 写法。
- **行为等价**:feature flag `manager_pack`(私有版叫别的名字)、`[MANAGER-OWES]` 标签、`~/Projects/your-workbench` 默认落点等重命名是全局一致的,逻辑未变;manager 提及识别改为从 `config.yaml` 的 `sources.watch_people` 首项派生。
- 文档里出现的 `~/Desktop/Keys/` 等默认凭证路径只是**本机约定示例**,推荐用 App 设置窗口把凭证写入 `config/secrets/`(见 `docs/CONTRACT.md` §19)。

## 脱敏审计原则

标注 "fictional" 的示例数据曾是泄漏重灾区(真实数据换皮混进 fixture)。公开任何新内容前跑一次独立的对抗审计,别信自查——见 `HANDOFF.md` §3 末条。
