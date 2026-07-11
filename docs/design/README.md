# docs/design/

这个目录放 **PM-level 的产品设计文档**：lane 语义、流程、re-raise / 归档这类
**从代码里推不出来的设计理由与决策**。Mermaid 流程图在 GitHub 上直接渲染。
这里的文档描述**意图（intent）**，不是逐字实现契约——真正的**行为契约**
（组件间数据格式、状态机、审批分级、凭证解析等）在
[`../CONTRACT.md`](../CONTRACT.md)。

当前文档：
- [`card-lifecycle-and-reraise.md`](card-lifecycle-and-reraise.md) — card 生命周期全景 +
  「completed 可匹配 / archived 封存」的 archive & re-raise 设计（DRAFT）。

---

私有版仓库的这个目录里另有三份设计文档（v1 顶层设计、v2 确认轮、需求画像）。
它们基于真实的会议记录、Slack 消息与工作数据写成，包含大量不宜公开的工作细节，
因此在公开导出中整体移除；上面列出的文档是公开可分享的部分。
