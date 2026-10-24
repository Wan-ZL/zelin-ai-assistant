# docs/design/

这个目录放**产品设计文档**：从代码里推不出来的设计理由与 owner 决定。这里的文档描述
**意图（intent）**，不是逐字实现契约——真正的**行为契约**（组件间数据格式、隔离、安装足迹、
升级等）在 [`../CONTRACT.md`](../CONTRACT.md)。

当前文档：
- [`lite2-plan.md`](lite2-plan.md) — lite 2.0 的计划书与决策台账（`| LN |` 行）：保留什么、
  删掉什么、为什么，以及 headless `claude` 的隔离设计。

1.x 的设计文档（vnext / vnext2 计划书与它的进度日志、卡片生命周期、静默合并模型与它的 TLA+
规格、inbox 动作、store2 映射、QR 能力同步、移植笔记）随 Act 管线在 v2.0.0 退役，全文在 git
tag / branch `full`：`git show full:docs/design/<文件名>`。代码注释与 CONTRACT 里的 `D<n>`
编号指的是 `full` 上的 `docs/design/vnext2-plan.md`。

---

私有版仓库的这个目录里另有几份设计文档（v1 顶层设计、v2 确认轮、需求画像）。
它们基于真实的会议记录、Slack 消息与工作数据写成，包含大量不宜公开的工作细节，
因此在公开导出中整体移除。
